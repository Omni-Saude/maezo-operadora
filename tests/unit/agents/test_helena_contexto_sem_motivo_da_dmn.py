"""Bateria de 01/10/2026 (C07, C08, C09, I04): o `motivo` da tabela nao vai ao modelo que redige.

O contexto da resposta levava `dmn_motivo`, o texto de engenharia da regra ("Sem criterio de red
flag adulto", "Dor toracica: possivel sindrome coronariana aguda", "suspeita de AVC"). O modelo o
repetia ao beneficiario: "nao ha sinais de alerta" — que a cerca de negativa clinica barra e que,
em `inform`, virava P3 `falha_tecnica` por sorteio — e "possivel sindrome coronariana aguda", que
e' diagnostico. O motivo continua na auditoria e no resumo do atendente; o que o modelo precisa para
redigir esta' na mensagem e em `response_kind`.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from maezo.agents.helena.graph import HelenaGraph, HelenaState
from maezo.agents.helena.prompts import response_prompt
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_MOTIVOS = [
    "Sem criterio de red flag adulto",
    "Dor toracica: possivel sindrome coronariana aguda",
    "Deficit neurologico agudo: suspeita de AVC (tempo-dependente)",
]


class _Gravador:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return "texto"


class _WhatsApp:
    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        return {"ok": True}


def _graph(gravador: _Gravador) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, gravador),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["inform", "escalate"])
@pytest.mark.parametrize("motivo", _MOTIVOS)
async def test_o_prompt_de_redacao_nao_leva_o_motivo_da_tabela(motivo: str, response_kind: str) -> None:
    gravador = _Gravador()
    estado: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:contexto",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-CTX",
        "message_body": "estou com dor no peito",
        "dmn_decision": {
            "red_flag": True,
            "prioridade": "P1",
            "conduta": "ESCALATE_EMERGENCY",
            "motivo": motivo,
        },
        "escalation_severidade": "grave",
    }

    await _graph(gravador)._redigir_resposta(estado, cast(Any, response_kind))

    assert len(gravador.prompts) == 1
    prompt = gravador.prompts[0]
    assert motivo not in prompt
    assert "dmn_motivo" not in prompt
    # o que o modelo AINDA recebe para redigir
    assert f"'response_kind': '{response_kind}'" in prompt
    assert "'escalation_severidade': 'grave'" in prompt


def test_o_texto_do_prompt_nao_manda_usar_o_motivo_da_dmn() -> None:
    assert "motivo da DMN" not in response_prompt()
