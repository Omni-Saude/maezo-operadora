"""DL-0116 (frente C do SP-OP-ESCALATION-001), parte (c): a Helena so' afirma caso aberto que o MOTOR REAL
confirma — e deixa de afirmar quando o atendente conclui a tarefa.

O defeito medido: depois de o atendente concluir `UT_TratarEscalonamento` (`resultado=resolvido_humano`), a
pessoa que dizia "obrigado, tchau" lia "seu atendimento continua com a equipe", porque a Helena so' olhava o
historico curto da conversa. Aqui, contra o CIB Seven da stack (ADR-0011: nunca um motor falso):

  1. a Helena REAL abre SP-OP-ESCALATION-001 (`solicitacao_humano`) pelo transporte HTTP real;
  2. ANTES de a tarefa ser concluida, "obrigado, tchau" com o historico -> `RESPOSTA_DESPEDIDA_CASO_ABERTO`;
  3. o atendente sintetico conclui `UT_TratarEscalonamento` com `resultado=resolvido_humano` e a instancia
     termina; o MESMO turno, com o MESMO historico -> `RESPOSTA_DESPEDIDA` (nunca a de caso aberto).

O modelo e o WhatsApp sao dubles (a aceitacao e' "o motor real muda o que a Helena afirma", nao "um modelo
real classifica"); a sonda de workers e' a mesma de `test_helena_escalation.py`. Sem motor, a sessao PULA
com o motivo explicito (`tests/integration/conftest.py`) — nunca um verde fabricado.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from maezo.agents.helena.graph import (
    RESPOSTA_DESPEDIDA,
    RESPOSTA_DESPEDIDA_CASO_ABERTO,
    build,
    new_helena_state,
)
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.escalation import (
    ESCALATION_BPMN_ERROR_ALLOWLIST,
    register_escalation_workers,
)
from maezo.tools.workers.harness import CibSevenWorkerTransport, WorkerHarness
from tests.support.engine_tenant_deploy import deploy_for_tenant

from ._engine_helpers import active_instances, noop_events_publish, wait_for_task

pytestmark = pytest.mark.integration

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module", autouse=True)
def _escalation_do_tenant(engine_base_url: str, audit_tenant: str) -> None:
    """A Helena inicia no tenant da conversa: a ESCALATION e a DMN dela precisam existir DO tenant."""
    deploy_for_tenant(engine_base_url, audit_tenant, "SP-OP-ESCALATION-001")


@pytest.fixture(autouse=True)
async def _escalation_worker_probe(
    engine_base_url: str, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[None]:
    """Serve `operadora.escalation.*` e `operadora.events.publish` (function-scoped, como em
    `test_helena_escalation.py`: o laco de cada teste e' proprio)."""
    transport = CibSevenWorkerTransport(engine_base_url, timeout=30.0)
    harness = WorkerHarness(
        transport,
        worker_id=f"it-helena-caso-encerrado-probe-{_RUN_ID}",
        tenant=audit_tenant,
        audit_sink=audit_sink,
        bpmn_error_allowlist=ESCALATION_BPMN_ERROR_ALLOWLIST,
        async_response_timeout_ms=5_000,
        poll_interval_ms=250,
    )
    register_escalation_workers(harness)
    harness.register("operadora.events.publish", noop_events_publish)
    run_task = asyncio.create_task(harness.run(), name="helena-it-caso-encerrado-probe")
    try:
        yield
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
        await transport.close()


class _FakeInference:
    """Classificacao roteirizada por turno; as outras chamadas (resumo, rascunho): texto neutro."""

    def __init__(self, classificacoes: list[str]) -> None:
        self._classificacoes = list(classificacoes)

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        del phi, agent_id, tenant_id, task_kind
        if "Tarefa: leia a mensagem do beneficiario" in prompt and self._classificacoes:
            return self._classificacoes.pop(0)
        return "Beneficiario pediu para falar com uma pessoa da equipe."


class _FakeWhatsAppSender:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        del to_hash
        self.enviados.append(text)
        return {"ok": True}


def _classify(intent: str) -> str:
    return json.dumps(
        {
            "intent": intent,
            "population": "adult",
            "psychosocial_risk": False,
            "sintoma_codigo": None,
            "intensidade": "desconhecida",
            "cobranca_subtipo": None,
            "competencia": None,
        }
    )


def _entrada(tenant: str, conversa: str, mensagem: str, historico: Any = None) -> dict[str, Any]:
    estado: dict[str, Any] = dict(
        new_helena_state(
            tenant_id=tenant,
            conversation_id=conversa,
            canal="whatsapp",
            beneficiario_pseudo_id=f"pseudo-{_RUN_ID}",
            message_body=mensagem,
            message_ref=f"hk1_{_RUN_ID}",
        )
    )
    if historico:
        estado["historico_conversa"] = historico
        estado["ultima_mensagem_em"] = datetime.now(UTC).isoformat()
    return estado


async def _turno(
    engine_base_url: str, audit_sink: Any, tenant: str, classificacao: str, entrada: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, tenant_id=tenant, timeout=30.0)
    whatsapp = _FakeWhatsAppSender()
    try:
        grafo = build(
            {
                "inference": _FakeInference([classificacao]),
                "dmn": dmn,
                "cibseven": cibseven,
                "whatsapp": whatsapp,
                "audit_sink": audit_sink,
                "historico_enabled": True,
                "roteador_lucas_enabled": True,
            }
        ).compile()
        resultado = dict(await grafo.ainvoke(entrada))
    finally:
        await dmn.close()
        await cibseven.close()
    return resultado, whatsapp.enviados


async def _esperar_o_fim(client: httpx.AsyncClient, instancia: str, *, tentativas: int = 120) -> None:
    """A instancia sai do runtime (404) e o historico a da' por concluida. O fim depende da sonda servir
    `ST_PublishResolved` depois da conclusao da tarefa, por isso o poll (nunca um sleep cego)."""
    for _ in range(tentativas):
        if (await client.get(f"/process-instance/{instancia}")).status_code == 404:
            historico = await client.get(f"/history/process-instance/{instancia}")
            historico.raise_for_status()
            assert historico.json().get("state") == "COMPLETED", historico.json().get("state")
            return
        await asyncio.sleep(0.5)
    raise AssertionError(f"a instancia {instancia} nao terminou depois da conclusao da tarefa")


async def test_despedida_so_afirma_caso_aberto_enquanto_a_instancia_esta_ativa_no_motor(
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any, audit_tenant: str
) -> None:
    conversa = f"wa:{audit_tenant}:c1-encerrado-{_RUN_ID}"
    chave = f"ESC-{audit_tenant}-{conversa}"

    # 1. A Helena real abre o caso (pedido explicito de pessoa).
    aberto, _ = await _turno(
        engine_base_url,
        audit_sink,
        audit_tenant,
        _classify("human_request"),
        _entrada(audit_tenant, conversa, "quero falar com um atendente"),
    )
    assert aberto["escalation_motivo"] == "solicitacao_humano"
    assert aberto["escalation_started"] is True
    historico = aberto["historico_conversa"]
    assert historico, "o historico curto (ligado) guarda o aviso de caso aberto"
    ativas = await active_instances(engine_client, chave)
    assert len(ativas) == 1
    instancia = str(ativas[0]["id"])

    # 2. Caso ATIVO no motor: a despedida diz que o caso continua.
    antes, enviados_antes = await _turno(
        engine_base_url,
        audit_sink,
        audit_tenant,
        _classify("greeting"),
        _entrada(audit_tenant, conversa, "obrigado, tchau", historico),
    )
    assert antes["caso_aberto_confirmado"] == "administrativo"
    assert enviados_antes == [RESPOSTA_DESPEDIDA_CASO_ABERTO]

    # 3. O atendente conclui a tarefa: a instancia termina.
    tarefa = await wait_for_task(engine_client, instancia)
    assert tarefa["taskDefinitionKey"] == "UT_TratarEscalonamento"
    resp = await engine_client.post(
        f"/task/{tarefa['id']}/complete",
        json={
            "variables": {
                "resultado": {"value": "resolvido_humano", "type": "String"},
                "notas_resolucao": {"value": "resolvido (teste de integracao)", "type": "String"},
            }
        },
    )
    assert resp.status_code in (200, 204), resp.text[:300]
    await _esperar_o_fim(engine_client, instancia)
    assert await active_instances(engine_client, chave) == []

    # 4. O MESMO turno, com o MESMO historico: a despedida comum, nunca a de caso aberto.
    depois, enviados_depois = await _turno(
        engine_base_url,
        audit_sink,
        audit_tenant,
        _classify("greeting"),
        _entrada(audit_tenant, conversa, "obrigado, tchau", historico),
    )
    assert depois["caso_aberto_confirmado"] is None
    assert enviados_depois == [RESPOSTA_DESPEDIDA]
