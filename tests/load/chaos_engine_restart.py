"""Drill de failover do ENGINE no meio do fluxo AUTH, numa stack DESCARTAVEL e isolada (R-086).

Cenario B3 de docs/design/T3.3-chaos-resilience.md §2 ("Engine restart mid-flight": ``docker
restart cibseven`` com uma task travada -> nenhuma task perdida, nenhum efeito em dobro).

POR QUE NAO E' UM TESTE PYTEST: reiniciar container e' container-control destrutivo. O design
ratificado (§3/§5) manda esse tipo de suite para um job noturno SERIALIZADO com stack propria
(o job ``chaos`` atual e' PG-only e a lane ``integration`` divide o engine entre suites); esse job
ainda nao existe e .github/workflows e' caminho CODEOWNERS. Um teste coletado por qualquer lane
atual ou derrubaria o engine compartilhado ou viraria skip — e o wrapper de evidencia das lanes
reprova skip. Fica como drill executavel, documentado em tests/load/README.md.

ISOLAMENTO (nunca toca a stack compartilhada de ``docker compose``):

- sobe UMA rede, UM postgres e UM cibseven proprios, todos com o prefixo ``r086chaos<hex>``,
  porta do engine efemera e presa a 127.0.0.1 (nada na 8080 compartilhada);
- reinicia SO' o proprio container de engine; nunca ``docker compose``, nunca ``down -v``;
- remove SO' os proprios containers (com os volumes anonimos, ``rm -f -v``) e a rede no ``finally``.

Roteiro: implanta BPMN+DMNs do AUTH (tenant descartavel) -> start sintetico (rota NAO_REQUER) ->
worker A trava ST_PublishReceived com lock longo -> ``docker restart`` do engine -> confere que a
instancia, a task e o LOCK sobreviveram (B nao consegue a task) -> A completa pela transport real
-> o fluxo retoma e termina em End_NaoRequerAutorizacao com a atividade executada UMA vez.

Uso (raiz do repo; ~3-6 min, puxa as imagens se faltarem):

    uv run --extra dev python -m tests.load.chaos_engine_restart

Saida: uma linha ``R086_CHAOS {json}`` e codigo 0 (sucesso) ou 1 (invariante violada).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import time
import uuid
from typing import Any

import httpx

from maezo.platform.deploy.engine_deploy import EngineDeployClient
from maezo.tools.process_business_keys import auth_business_key
from maezo.tools.workers.harness import CibSevenWorkerTransport
from tests.load.auth_scenarios import (
    AUTH_FIRST_TOPIC,
    AUTH_PROCESS_KEY,
    assert_local_url,
    auth_artifact_paths,
    camunda_variables,
    start_variables,
    synthetic_guia,
    synthetic_tenant,
)

#: Mesmas imagens do docker-compose.yml (postgres pinado por digest; cibseven 2.1.0, DL-0006).
POSTGRES_IMAGE = "postgres:17@sha256:f4c66b820c6f974249089d3d16d86a3698eae11e8746eb6644b2271031e91232"
ENGINE_IMAGE = "cibseven/cibseven:2.1.0"
#: Lock maior que o reboot do engine: prova que o LOCK (estado persistido) sobrevive ao restart.
LOCK_MS = 600_000
BOOT_TIMEOUT_S = 420.0
WORKER_A = "r086-chaos-worker-a"
WORKER_B = "r086-chaos-worker-b"


class DrillFailureError(AssertionError):
    pass


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise DrillFailureError(message)


def _docker(*args: str, check: bool = True) -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise DrillFailureError("docker CLI ausente")
    done = subprocess.run([docker, *args], capture_output=True, text=True, check=False)
    if check and done.returncode != 0:
        raise DrillFailureError(f"docker {' '.join(args[:2])} falhou: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _wait_engine(base: str, *, timeout_s: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        try:
            if httpx.get(f"{base}/version", timeout=3.0).status_code == 200:
                return time.monotonic() - started
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    raise DrillFailureError(f"engine nao respondeu /version em {timeout_s:.0f}s")


def _fetch(base: str, worker: str, tenant: str, business_key: str, lock_ms: int) -> list[dict[str, Any]]:
    body = {
        "workerId": worker,
        "maxTasks": 1,
        "asyncResponseTimeout": 0,
        "topics": [
            {
                "topicName": AUTH_FIRST_TOPIC,
                "lockDuration": lock_ms,
                "tenantIdIn": [tenant],
                "businessKey": business_key,
            }
        ],
    }
    resp = httpx.post(f"{base}/external-task/fetchAndLock", json=body, timeout=30.0)
    resp.raise_for_status()
    result: list[dict[str, Any]] = resp.json()
    return result


def _fetch_until(
    base: str, worker: str, tenant: str, business_key: str, *, timeout_s: float, lock_ms: int = 60_000
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        tasks = _fetch(base, worker, tenant, business_key, lock_ms)
        if tasks:
            return tasks[0]
        time.sleep(0.5)
    raise DrillFailureError("nenhuma task disponivel dentro do prazo")


def _flow(base: str, report: dict[str, Any], engine_name: str) -> None:
    tenant = synthetic_tenant()
    client = EngineDeployClient(base_url=base)
    try:
        client.deploy(auth_artifact_paths(), name=f"r086-chaos-{tenant}", tenant_id=tenant)
    finally:
        client.close()
    business_key = auth_business_key(tenant_id=tenant, numero_guia_tiss=synthetic_guia())
    start = httpx.post(
        f"{base}/process-definition/key/{AUTH_PROCESS_KEY}/tenant-id/{tenant}/start",
        json={
            "businessKey": business_key,
            "variables": camunda_variables(start_variables(requer_autorizacao=False)),
        },
        timeout=30.0,
    )
    _check(start.status_code == 200, f"start falhou: {start.status_code} {start.text[:200]}")
    instance_id = start.json()["id"]

    # Lock LONGO de A (LOCK_MS): a 3a execucao com lock de 60 s e reboot de 64 s mostrou a
    # asercao "B recebeu task ainda travada" ficando vermelha quando o lock expira no meio.
    locked = _fetch_until(base, WORKER_A, tenant, business_key, timeout_s=60, lock_ms=LOCK_MS)
    _check(locked["activityId"] == "ST_PublishReceived", f"task inesperada: {locked['activityId']}")
    task_id = locked["id"]

    t0 = time.monotonic()
    _docker("restart", engine_name)
    report["engine_indisponivel_s"] = round(
        time.monotonic() - t0 + _wait_engine(base, timeout_s=BOOT_TIMEOUT_S), 1
    )

    inst = httpx.get(f"{base}/process-instance/{instance_id}", timeout=30.0)
    _check(inst.status_code == 200, "instancia PERDIDA no restart do engine")
    after = httpx.get(f"{base}/external-task/{task_id}", timeout=30.0).json()
    _check(after.get("workerId") == WORKER_A, f"lock de A NAO sobreviveu ao restart: {after.get('workerId')}")
    _check(not _fetch(base, WORKER_B, tenant, business_key, 60_000), "B recebeu task ainda travada por A")

    async def complete_all() -> None:
        transport = CibSevenWorkerTransport(base)
        try:
            await transport.complete(task_id, WORKER_A, {})
            nxt = _fetch_until(base, WORKER_A, tenant, business_key, timeout_s=60)
            _check(nxt["activityId"] == "ST_PublishNaoRequer", f"retomada inesperada: {nxt['activityId']}")
            await transport.complete(nxt["id"], WORKER_A, {})
        finally:
            await transport.close()

    asyncio.run(complete_all())

    hist = httpx.get(f"{base}/history/process-instance/{instance_id}", timeout=30.0).json()
    _check(hist.get("state") == "COMPLETED", f"instancia nao terminou: {hist.get('state')}")
    # `endActivityId` do historico vem nulo neste engine (medido); o fim e' provado pela
    # instancia de atividade do end event.
    fim = httpx.get(
        f"{base}/history/activity-instance",
        params={"processInstanceId": instance_id, "activityId": "End_NaoRequerAutorizacao"},
        timeout=30.0,
    ).json()
    _check(len(fim) == 1, f"fim inesperado: End_NaoRequerAutorizacao alcancado {len(fim)} vezes")
    acts = httpx.get(
        f"{base}/history/activity-instance",
        params={"processInstanceId": instance_id, "activityId": "ST_PublishReceived"},
        timeout=30.0,
    ).json()
    _check(len(acts) == 1, f"ST_PublishReceived executada {len(acts)} vezes")
    report["instancia_completou"] = True
    report["execucoes_st_publish_received"] = len(acts)


def main() -> int:
    prefix = f"r086chaos{uuid.uuid4().hex[:8]}"
    network, pg, engine = prefix, f"{prefix}-pg", f"{prefix}-engine"
    report: dict[str, Any] = {"drill": "auth_engine_restart", "stack": prefix, "ok": False}
    try:
        # Porta FIXA escolhida antes: com `127.0.0.1::8080` o docker sorteia OUTRA porta no
        # restart e o drill passaria a olhar para o lugar errado (medido na 1a execucao).
        host_port = _free_local_port()
        _docker("network", "create", network)
        _docker(
            "run", "-d", "--name", pg, "--network", network, "--label", "maezo.r086.chaos=1",
            "-e", "POSTGRES_USER=maezo", "-e", "POSTGRES_PASSWORD=maezo", "-e", "POSTGRES_DB=maezo",
            POSTGRES_IMAGE,
        )  # fmt: skip
        _docker(
            "run", "-d", "--name", engine, "--network", network, "--label", "maezo.r086.chaos=1",
            "-p", f"127.0.0.1:{host_port}:8080",
            "-e", "DB_DRIVER=org.postgresql.Driver",
            "-e", f"DB_URL=jdbc:postgresql://{pg}:5432/maezo",
            "-e", "DB_USERNAME=maezo", "-e", "DB_PASSWORD=maezo",
            "-e", f"WAIT_FOR={pg}:5432", "-e", "WAIT_FOR_TIMEOUT=120",
            ENGINE_IMAGE,
        )  # fmt: skip
        base = assert_local_url(f"http://127.0.0.1:{host_port}/engine-rest")
        report["boot_s"] = round(_wait_engine(base, timeout_s=BOOT_TIMEOUT_S), 1)
        _flow(base, report, engine)
        report["ok"] = True
    except DrillFailureError as exc:
        report["falha"] = str(exc)
    finally:
        # -v: postgres declara VOLUME; sem isso o volume anonimo vaza a cada execucao
        _docker("rm", "-f", "-v", engine, pg, check=False)
        _docker("network", "rm", network, check=False)
    print("R086_CHAOS " + json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
