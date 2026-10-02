"""Bateria de 01/10/2026 (B07, B08): um canal com o NOME certo citado para um assunto que ele nao resolve.

O aplicativo e o portal resolvem boleto, carteirinha, rede e historico (`_AUTOATENDIMENTO`); o
comentario de `CanalConfirmado` ja' dizia que citar o aplicativo para autorizacao de exame seria
errado "mesmo com o nome certo". O modelo fez exatamente isso, e a cerca de canal so' julgava o NOME.
A terceira passagem de `motivo_de_canal_nao_confirmado` olha a MESMA ORACAO: canal e assunto que ele
nao resolve juntos.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import RESPOSTA_INFORM_RECUSADA, HelenaGraph, HelenaState
from maezo.agents.helena.prompts import (
    RECUSA_CANAL_NAO_CONFIRMADO,
    classify_prompt,
    motivo_de_canal_nao_confirmado,
)
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

#: Respostas REAIS da bateria de 01/10 (B07 e B08), antes do DL-0052 para o reembolso.
_RECUSADOS = [
    "Verificar no aplicativo Austa Clinicas ou no portal do plano "
    "se ha alguma mensagem ou justificativa registrada.",
    "Voce pode acessar o aplicativo Austa Clinicas ou o portal do plano, "
    "onde e possivel verificar o status das guias.",
    "Para pedir reembolso, acesse o portal do plano ou o aplicativo Austa Clinicas e envie os documentos.",
    "No portal do plano voce acompanha o protocolo da sua solicitacao.",
    "O cancelamento do plano pode ser pedido pelo aplicativo Austa Clinicas.",
]

#: O que o aplicativo e o portal RESOLVEM, e o que NAO pode ser recusado so' por vizinhanca de palavra.
_PERMITIDOS = [
    "A carteirinha digital fica no aplicativo Austa Clinicas e no portal do plano.",
    "No aplicativo Austa Clinicas voce consulta a rede credenciada e o Guia Medico.",
    "A segunda via do boleto fica no portal do plano.",
    # Duas oracoes: o canal numa, o assunto noutra. A cerca e' POR ORACAO.
    "Para saber da sua autorizacao, procure a central de atendimento do plano. "
    "A carteirinha fica no aplicativo Austa Clinicas.",
    "Para autorizacao de exame, fale com a central de atendimento do plano.",
]


@pytest.mark.parametrize("texto", _RECUSADOS)
def test_canal_citado_para_assunto_que_ele_nao_resolve_e_recusado(texto: str) -> None:
    veredito = motivo_de_canal_nao_confirmado(texto)

    assert veredito is not None, texto
    assert veredito[0] == RECUSA_CANAL_NAO_CONFIRMADO
    assert veredito[1].startswith("finalidade do canal"), veredito


@pytest.mark.parametrize("texto", _PERMITIDOS)
def test_o_que_o_aplicativo_resolve_e_a_oracao_separada_continuam_permitidos(texto: str) -> None:
    assert motivo_de_canal_nao_confirmado(texto) is None, texto


class _Gravador:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        del prompt
        return self._respostas.pop(0) if self._respostas else ""


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


@pytest.mark.asyncio
async def test_o_rascunho_que_cita_o_aplicativo_para_autorizacao_vira_a_constante_e_nao_abre_caso() -> None:
    """Defesa em profundidade: mesmo que o classificador escorregue para `information`, o texto que
    manda a pessoa ao aplicativo para ver a justificativa nao sai, e a troca e' por CONSTANTE (sem
    fila aberta), como em qualquer recusa que nao seja a negativa clinica."""
    classificacao = json.dumps(
        {
            "intent": "information",
            "population": "none",
            "psychosocial_risk": False,
            "sintoma_codigo": None,
            "intensidade": "desconhecida",
        }
    )
    whatsapp = _WhatsApp()
    graph = HelenaGraph(
        inference=cast(InferenceProvider, _Gravador([classificacao, _RECUSADOS[0]])),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
    )
    estado: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:finalidade",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-FIN",
        "message_body": "Minha autorizacao de ressonancia foi negada, por que?",
    }

    resultado = dict(await graph.compile_graph().compile().ainvoke(estado))

    assert resultado["response_text"] == RESPOSTA_INFORM_RECUSADA
    assert whatsapp.enviados == [RESPOSTA_INFORM_RECUSADA]
    assert not resultado.get("escalation_started")


def test_o_prompt_do_classificador_manda_a_autorizacao_negada_para_fora_do_canal() -> None:
    texto = classify_prompt()

    assert "autorizacao NEGADA" in texto
    assert "minha autorizacao foi negada" in texto
    assert "decisao de medico auditor" in texto
