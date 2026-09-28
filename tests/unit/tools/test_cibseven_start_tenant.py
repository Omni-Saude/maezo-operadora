"""Todo start de processo nasce no tenant da conversa — ou nao nasce.

Defeito medido no dev em 27/09/2026: a Helena abria `SP-OP-ESCALATION-001` por
`POST /process-definition/key/{key}/start`, que o motor resolve SEM tenant. A instancia nascia com
`tenantId = null`; o portal humano le `tenantIdIn(amh)` e o emissor de casos descarta o que nao e'
do tenant, entao a tarefa humana existia e nao aparecia em `/tasks?queue=team`.

O que estes testes fixam:
  * o transporte HTTP so inicia pela rota `/tenant-id/{tenant}/start`, e RECUSA sem tenant ligado
    (nunca cai no endpoint sem tenant);
  * `start_process_idempotent` confere o tenant do transporte contra o da proveniencia ANTES do
    claim duravel — tenant ausente ou divergente nao escreve auditoria nem chama o motor;
  * a correlacao de mensagem fica no tenant quando ele esta ligado;
  * as tres raizes (`build_cibseven_seam`, normal e fresh-client) ligam o tenant do `SeamContext`
    e o decorador gated o expoe.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    CibSevenHttpTransport,
    CibSevenTenantError,
    FakeCibSevenTransport,
    StartOutcome,
    start_process_idempotent,
)
from tests.support.audit_fakes import FakeStartAuditSink

ESC = "SP-OP-ESCALATION-001"
ESC_KEY = "ESC-amh-wa:amh:hk1_abc"


def _ok(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _provenance(tenant_id: str = "amh") -> AgentDecisionProvenance:
    return AgentDecisionProvenance(
        agent_id="helena",
        agent_version="helena@v0",
        tenant_id=tenant_id,
        decision_basis={"route": "escalate"},
    )


def _http(tenant_id: str | None) -> tuple[CibSevenHttpTransport, AsyncMock, AsyncMock]:
    transport = CibSevenHttpTransport("http://engine/engine-rest", tenant_id=tenant_id)
    post = AsyncMock(return_value=_ok({"id": "proc-1", "state": "ACTIVE"}))
    get = AsyncMock(return_value=_ok([]))
    transport._client.post = post  # type: ignore[method-assign]
    transport._client.get = get  # type: ignore[method-assign]
    return transport, post, get


# --- transporte HTTP --------------------------------------------------------------------------


async def test_start_usa_a_rota_do_tenant() -> None:
    transport, post, _ = _http("amh")
    inst = await transport.start_process_instance(ESC, ESC_KEY, {"severidade": "grave"})
    assert inst.instance_id == "proc-1"
    assert post.call_args[0][0] == f"/process-definition/key/{ESC}/tenant-id/amh/start"
    assert transport.engine_tenant_id == "amh"


async def test_start_sem_tenant_recusa_e_nao_chama_o_motor() -> None:
    transport, post, _ = _http(None)
    with pytest.raises(CibSevenTenantError):
        await transport.start_process_instance(ESC, ESC_KEY, {})
    post.assert_not_called()


@pytest.mark.parametrize("ruim", ["", "a/b", "amh?x=1", "../amh", "1amh", "amh-x", "a" * 41, " amh"])
def test_tenant_invalido_recusado_na_construcao(ruim: str) -> None:
    # Vai para um SEGMENTO DE PATH da REST: nada que mude a rota passa da construcao.
    with pytest.raises(CibSevenTenantError):
        CibSevenHttpTransport("http://engine/engine-rest", tenant_id=ruim)


async def test_correlacao_fica_no_tenant_quando_ligado() -> None:
    transport, post, _ = _http("amh")
    await transport.correlate_message(
        "msg", "", {}, correlation_keys={"numero_contrato": "C-1"}, all_matching=True
    )
    payload = post.call_args[1]["json"]
    assert payload["tenantId"] == "amh"
    assert payload["all"] is True


async def test_correlacao_sem_tenant_nao_inventa_tenant() -> None:
    transport, post, _ = _http(None)
    await transport.correlate_message("msg", ESC_KEY, {})
    assert "tenantId" not in post.call_args[1]["json"]


# --- chokepoint: tenant conferido ANTES do claim duravel ----------------------------------------


async def test_chokepoint_inicia_no_tenant_da_conversa() -> None:
    transport, post, _ = _http("amh")
    sink = FakeStartAuditSink()
    inst = await start_process_idempotent(
        transport,
        process_key=ESC,
        business_key=ESC_KEY,
        variables={"severidade": "grave"},
        audit_sink=sink,
        provenance=_provenance("amh"),
    )
    assert inst.start_outcome is StartOutcome.STARTED
    assert post.call_args[0][0] == f"/process-definition/key/{ESC}/tenant-id/amh/start"
    assert len(sink.records) == 1


@pytest.mark.parametrize(("ligado", "conversa"), [(None, "amh"), ("amh", "outro")])
async def test_chokepoint_recusa_tenant_ausente_ou_divergente_sem_escrever(
    ligado: str | None, conversa: str
) -> None:
    transport, post, get = _http(ligado)
    sink = FakeStartAuditSink()
    with pytest.raises(CibSevenTenantError):
        await start_process_idempotent(
            transport,
            process_key=ESC,
            business_key=ESC_KEY,
            variables={},
            audit_sink=sink,
            provenance=_provenance(conversa),
        )
    # Recusado antes do claim: nenhuma auditoria (nenhuma chave presa) e nenhum toque no motor.
    assert sink.calls == []
    post.assert_not_called()
    get.assert_not_called()


async def test_transporte_que_nao_declara_tenant_segue_o_caminho_de_hoje() -> None:
    # Dubles de teste (e o transporte seguro D7, cuja identidade de tenant e' do perfil) nao
    # declaram `engine_tenant_id`: o chokepoint nao inventa uma recusa para eles.
    fake = FakeCibSevenTransport()
    inst = await start_process_idempotent(
        fake,
        process_key=ESC,
        business_key=ESC_KEY,
        variables={},
        audit_sink=FakeStartAuditSink(),
        provenance=_provenance("amh"),
    )
    assert inst.start_outcome is StartOutcome.STARTED


# --- raizes de composicao --------------------------------------------------------------------


@pytest.mark.parametrize("fresh", [False, True])
async def test_build_cibseven_seam_liga_o_tenant_do_seam(fresh: bool) -> None:
    from maezo.gateway.seams._base import SeamContext
    from maezo.gateway.tool_registry import build_cibseven_seam

    seam = SeamContext(tenant="amh", principal="helena")
    gated = build_cibseven_seam(seam=seam, base_url="http://engine/engine-rest", fresh_client=fresh)
    assert gated.engine_tenant_id == "amh"
    await gated.close()


async def test_fresh_client_repassa_o_tenant_ao_transporte_interno() -> None:
    from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport

    fresh = FreshClientCibSevenTransport("http://engine/engine-rest", tenant_id="amh")
    inner = fresh._new_transport()
    try:
        assert isinstance(inner, CibSevenHttpTransport)
        assert inner.engine_tenant_id == "amh"
    finally:
        await inner.close()


def test_gated_sobre_duble_sem_tenant_continua_nao_declarando() -> None:
    from maezo.gateway.seams._base import SeamContext
    from maezo.gateway.seams.cibseven import gate_cibseven

    gated = gate_cibseven(FakeCibSevenTransport(), SeamContext(tenant="amh", principal="helena"))
    sentinel = object()
    assert getattr(gated, "engine_tenant_id", sentinel) is sentinel
