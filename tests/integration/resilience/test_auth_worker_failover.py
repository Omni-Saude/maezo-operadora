"""Failover de worker no meio do fluxo AUTH: SIGKILL real de um processo-worker (R-086, F-120).

Cenario B1c de docs/design/T3.3-chaos-resilience.md §2 (pod-kill do worker), na forma que NAO
destroi a stack compartilhada: o que morre e' um SUBPROCESSO deste teste, nunca um container.
Por isso o teste e' engine-state-safe e roda na lane ``integration`` (shard ``restante``), nao
no job ``chaos`` PG-only nem no job noturno W2 (container-control, ainda nao existe).

Roteiro, contra o engine REAL (ADR-0011) e o BPMN REAL de SP-OP-AUTH-001:

1. implanta BPMN+DMNs do AUTH sob um tenant descartavel (isolamento num engine compartilhado);
2. inicia uma autorizacao sintetica que nao requer autorizacao (rota NAO_REQUER: termina sem
   User Task — negativa/decisao clinica continuam humanas, nada disso e' tocado aqui);
3. o worker A (subprocesso) trava a 1a external task (ST_PublishReceived) e e' morto com
   SIGKILL antes de completar — sem unlock, sem failure: exatamente um pod morto;
4. o worker B so' consegue a MESMA task depois que o lock de A expira (re-entrega do engine);
5. a conclusao tardia do zumbi A e' RECUSADA pelo engine (nenhum efeito em dobro);
6. B completa pela transport REAL dos workers (``CibSevenWorkerTransport.complete``), o processo
   retoma (DMN de admissibilidade engine-side), B completa a task seguinte e a instancia termina
   em ``End_NaoRequerAutorizacao`` com ST_PublishReceived executada UMA vez e um unico log de
   sucesso, do worker B.

Cada asercao e' a que fica vermelha se o mecanismo quebrar: task perdida (B nunca recebe),
lock ignorado (B recebe antes da expiracao), conclusao dupla (zumbi aceito), retomada quebrada
(instancia nao termina / atividade executada duas vezes).
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
import textwrap
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from maezo.platform.deploy.engine_deploy import EngineDeployClient
from maezo.tools.process_business_keys import auth_business_key
from maezo.tools.workers.harness import CibSevenWorkerTransport
from tests.integration.conftest import RUN_ID
from tests.load.auth_scenarios import (
    AUTH_ARTIFACTS,
    AUTH_FIRST_TOPIC,
    AUTH_PROCESS_KEY,
    assert_local_url,
    auth_artifact_paths,
    camunda_variables,
    start_variables,
    synthetic_guia,
    synthetic_tenant,
)

pytestmark = pytest.mark.integration

#: Lock curto de proposito: o teste espera a expiracao inteira. Parametro do drill, nao meta.
LOCK_MS = 3000
WORKER_A = f"r086-worker-a-{RUN_ID}"
WORKER_B = f"r086-worker-b-{RUN_ID}"

#: Worker A: trava UMA task filtrada por tenant+business key (nunca toca task alheia num engine
#: compartilhado), imprime o id e fica pendurado ate' o SIGKILL — o "handler" que nunca volta.
_WORKER_A_SOURCE = textwrap.dedent(
    """
    import json, sys, time
    import httpx
    base, worker, topic, tenant, bkey, lock_ms = sys.argv[1:7]
    body = {"workerId": worker, "maxTasks": 1, "asyncResponseTimeout": 0,
            "topics": [{"topicName": topic, "lockDuration": int(lock_ms),
                        "tenantIdIn": [tenant], "businessKey": bkey}]}
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        tasks = httpx.post(base + "/external-task/fetchAndLock", json=body, timeout=10).json()
        if tasks:
            print(json.dumps({"task_id": tasks[0]["id"]}), flush=True)
            time.sleep(3600)
        time.sleep(0.2)
    print(json.dumps({"task_id": None}), flush=True)
    """
)


@pytest.fixture
def auth_tenant(engine_base_url: str) -> Iterator[str]:
    assert_local_url(engine_base_url)
    tenant = synthetic_tenant()
    client = EngineDeployClient(base_url=engine_base_url)
    try:
        outcome = client.deploy(auth_artifact_paths(), name=f"r086-failover-{tenant}", tenant_id=tenant)
    finally:
        client.close()
    assert outcome.deployed_count == len(AUTH_ARTIFACTS), outcome
    try:
        yield tenant
    finally:
        httpx.delete(
            f"{engine_base_url}/deployment/{outcome.deployment_id}",
            params={"cascade": "true", "skipCustomListeners": "true", "skipIoMappings": "true"},
            timeout=120.0,
        ).raise_for_status()


def _fetch_body(worker_id: str, topic: str, tenant: str, business_key: str) -> dict[str, Any]:
    return {
        "workerId": worker_id,
        "maxTasks": 1,
        "asyncResponseTimeout": 0,
        "topics": [
            {
                "topicName": topic,
                "lockDuration": 60_000,
                "tenantIdIn": [tenant],
                "businessKey": business_key,
            }
        ],
    }


async def _poll_task(
    client: httpx.AsyncClient, worker_id: str, topic: str, tenant: str, business_key: str, *, timeout_s: float
) -> tuple[dict[str, Any] | None, float]:
    """Fetch curto em loop (o long-poll do engine nao acorda na EXPIRACAO de lock, so' em task nova)."""
    body = _fetch_body(worker_id, topic, tenant, business_key)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = await client.post("/external-task/fetchAndLock", json=body)
        resp.raise_for_status()
        tasks = resp.json()
        if tasks:
            return tasks[0], time.monotonic()
        await asyncio.sleep(0.25)
    return None, time.monotonic()


async def test_worker_morto_no_meio_do_auth_engine_reentrega_e_fluxo_termina_uma_vez(
    engine_base_url: str, auth_tenant: str
) -> None:
    guia = synthetic_guia()
    business_key = auth_business_key(tenant_id=auth_tenant, numero_guia_tiss=guia)
    async with httpx.AsyncClient(base_url=engine_base_url, timeout=30.0) as client:
        start = await client.post(
            f"/process-definition/key/{AUTH_PROCESS_KEY}/tenant-id/{auth_tenant}/start",
            json={
                "businessKey": business_key,
                "variables": camunda_variables(start_variables(requer_autorizacao=False)),
            },
        )
        assert start.status_code == 200, start.text[:300]
        instance_id = start.json()["id"]

        # 3. worker A trava a task e morre por SIGKILL antes de completar.
        victim = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _WORKER_A_SOURCE,
            engine_base_url,
            WORKER_A,
            AUTH_FIRST_TOPIC,
            auth_tenant,
            business_key,
            str(LOCK_MS),
            stdout=asyncio.subprocess.PIPE,
        )
        try:
            assert victim.stdout is not None
            line = await asyncio.wait_for(victim.stdout.readline(), timeout=60)
            locked_seen_at = time.monotonic()  # o lock de A nasceu ANTES deste instante
            locked_id = json.loads(line)["task_id"]
            assert locked_id, "worker A nunca recebeu a task (fluxo nao chegou a ST_PublishReceived)"
            locked = (await client.get(f"/external-task/{locked_id}")).json()
            assert locked["workerId"] == WORKER_A
            assert locked["activityId"] == "ST_PublishReceived"
        finally:
            victim.send_signal(signal.SIGKILL)
            await victim.wait()
        assert victim.returncode == -signal.SIGKILL

        # 4. B so' recebe a MESMA task depois da expiracao do lock de A.
        redelivered, got_at = await _poll_task(
            client, WORKER_B, AUTH_FIRST_TOPIC, auth_tenant, business_key, timeout_s=LOCK_MS / 1000 + 30
        )
        assert redelivered is not None, "task PERDIDA: o engine nao re-entregou apos o lock expirar"
        assert redelivered["id"] == locked_id
        # Relogio MONOTONICO do host (o relogio da VM do docker pode derivar): se o engine
        # ignorasse o lock, B receberia a task logo apos o SIGKILL, muito antes de LOCK_MS.
        waited = got_at - locked_seen_at
        assert waited >= LOCK_MS / 1000 - 0.5, f"lock de A ignorado: B recebeu a task {waited:.2f}s depois"

        # 5. Zumbi: A volta do alem e tenta completar — o engine recusa (sem efeito em dobro).
        zombie = await client.post(
            f"/external-task/{locked_id}/complete", json={"workerId": WORKER_A, "variables": {}}
        )
        assert zombie.status_code >= 400, f"conclusao do zumbi ACEITA: {zombie.status_code}"

        # 6. B completa pela transport real e o fluxo retoma ate' o fim.
        transport = CibSevenWorkerTransport(engine_base_url)
        try:
            await transport.complete(locked_id, WORKER_B, {})
            nxt, _ = await _poll_task(
                client, WORKER_B, AUTH_FIRST_TOPIC, auth_tenant, business_key, timeout_s=30
            )
            assert nxt is not None, "fluxo nao retomou apos o failover"
            assert nxt["activityId"] == "ST_PublishNaoRequer"
            await transport.complete(nxt["id"], WORKER_B, {})
        finally:
            await transport.close()

        hist = (await client.get(f"/history/process-instance/{instance_id}")).json()
        assert hist["state"] == "COMPLETED", hist
        # `endActivityId` do historico vem nulo neste engine: o fim e' provado pelo end event.
        fim = (
            await client.get(
                "/history/activity-instance",
                params={"processInstanceId": instance_id, "activityId": "End_NaoRequerAutorizacao"},
            )
        ).json()
        assert len(fim) == 1, fim

        acts = (
            await client.get(
                "/history/activity-instance",
                params={"processInstanceId": instance_id, "activityId": "ST_PublishReceived"},
            )
        ).json()
        assert len(acts) == 1, f"ST_PublishReceived executada {len(acts)} vezes"
        assert acts[0]["canceled"] is False

        success_logs = (
            await client.get(
                "/history/external-task-log", params={"externalTaskId": locked_id, "successLog": "true"}
            )
        ).json()
        assert [log["workerId"] for log in success_logs] == [WORKER_B]
