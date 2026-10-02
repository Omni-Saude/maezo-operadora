"""Bateria de 01/10/2026 (J09): a Helena nao afirma nada sobre o tratamento dos dados.

"Voces guardam minhas mensagens?" saiu, em 7 de 7 execucoes, como "as mensagens sao armazenadas de
forma segura e pseudonimizada, seguindo as normas de privacidade do plano". Nada no repositorio
autoriza essa frase: o consentimento nao esta modelado, a matriz de retencao e' um template que o
carregador recusa e a custodia do telefone esta' ligada em dev sob excecao (Plano G2.1 / G2.4).
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import RESPOSTA_INFORM_RECUSADA, HelenaGraph, HelenaState
from maezo.agents.helena.prompts import RECUSA_PROMESSA_DE_CAPACIDADE, classify_prompt, motivo_de_recusa
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

#: As respostas REAIS da bateria, uma por variacao medida.
_ALEGACOES = [
    "Por aqui, as mensagens sao armazenadas de forma segura e pseudonimizada, "
    "seguindo as normas de privacidade do plano.",
    "As conversas sao armazenadas de forma segura e pseudonimizada "
    "para garantir a continuidade do atendimento.",
    "Suas mensagens sao guardadas conforme a LGPD.",
    "Guardamos suas mensagens com criptografia.",
    "Seus dados estao protegidos pela LGPD.",
]

#: O que a Helena PODE dizer sobre identidade e dados sem afirmar tratamento nenhum.
_PERMITIDOS = [
    "Nao, por aqui eu nao consigo te identificar.",
    "Este canal nao consulta cadastro nem identifica beneficiarios.",
    "Se quiser falar com uma pessoa da equipe, e so me pedir.",
]


@pytest.mark.parametrize("texto", _ALEGACOES)
@pytest.mark.parametrize("rota", ["inform", "escalate"])
def test_alegacao_sobre_o_tratamento_dos_dados_e_recusada_em_toda_rota(texto: str, rota: str) -> None:
    veredito = motivo_de_recusa(texto, rota, start_aconteceu=None)

    assert veredito is not None, texto
    assert veredito[0] == RECUSA_PROMESSA_DE_CAPACIDADE


@pytest.mark.parametrize("texto", _PERMITIDOS)
def test_o_que_nao_afirma_tratamento_de_dados_continua_permitido(texto: str) -> None:
    assert motivo_de_recusa(texto, "inform", start_aconteceu=None) is None, texto


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
async def test_o_rascunho_que_afirma_armazenamento_vira_a_constante_e_nao_abre_caso() -> None:
    """Mesmo que o classificador escorregue para `information`, a alegacao nao sai."""
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
        inference=cast(InferenceProvider, _Gravador([classificacao, _ALEGACOES[0]])),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
    )
    estado: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:privacidade",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-PRIV",
        "message_body": "Voces guardam minhas mensagens?",
    }

    resultado = dict(await graph.compile_graph().compile().ainvoke(estado))

    assert resultado["response_text"] == RESPOSTA_INFORM_RECUSADA
    assert whatsapp.enviados == [RESPOSTA_INFORM_RECUSADA]
    assert not resultado.get("escalation_started")


def test_o_prompt_do_classificador_manda_a_pergunta_de_privacidade_para_fora_do_canal() -> None:
    texto = classify_prompt()

    assert "privacidade e armazenamento de dados" in texto
    assert "voces guardam minhas mensagens" in texto
