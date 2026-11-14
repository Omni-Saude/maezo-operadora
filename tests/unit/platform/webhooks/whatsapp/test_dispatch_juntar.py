"""O despachante e o ESPERAR E JUNTAR da Helena (Plano de ajustes v2, etapa 9): o turno `juntada` nao envia,
nao conta como primeira resposta e nao vai ao Lucas — o turno da mensagem mais nova responde com todas."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena import graph as helena_graph_module
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.platform.webhooks.whatsapp.test_dispatch import _FakeInference, _FakeWhatsAppClient


@pytest.mark.parametrize("refeita", [False, True])
async def test_turno_juntado_nao_envia_nem_conta_como_resposta(
    monkeypatch: pytest.MonkeyPatch, refeita: bool
) -> None:
    from maezo.platform.observability import get_metrics_collector

    class _Compilado:
        async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
            return {"juntada": True, "refeita": refeita, "conversation_id": state["conversation_id"]}

    class _Grafo:
        def compile(self, checkpointer: Any = None) -> _Compilado:
            return _Compilado()

    monkeypatch.setattr(helena_graph_module, "build", lambda _config: _Grafo())
    cliente = _FakeWhatsAppClient()
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference([]),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=cliente,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
    )
    coletor = get_metrics_collector()
    antes = coletor.registry.get_sample_value(
        "maezo_agent_first_response_seconds_count", {"agent_id": "helena"}
    )
    with capture_logs() as logs:
        resultado = await dispatcher.dispatch(
            InboundMessage(from_number="5511999999999", text="oi", message_id="wamid.j1")
        )
    depois = coletor.registry.get_sample_value(
        "maezo_agent_first_response_seconds_count", {"agent_id": "helena"}
    )
    assert resultado["juntada"] is True
    assert cliente.sent == []
    assert depois == antes
    eventos = [e for e in logs if e.get("event") == "helena_dispatch_turn_juntado"]
    assert eventos and eventos[0]["refeita"] is refeita
    assert not any(e.get("event") == "helena_dispatch_turn_completed" for e in logs)
