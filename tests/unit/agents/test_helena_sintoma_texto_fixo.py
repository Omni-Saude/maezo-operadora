"""Bateria de 01/10/2026 (S4, S5, S8, C07-C09): sintoma sem bandeira recebe TEXTO FIXO, sem modelo.

O modelo que redigia essa resposta listava sinais de alarme e limiares que nenhuma tabela aprovou
("febre por mais de 48 horas"), dava conduta ("aplicar gelo por 15 minutos a cada 2 horas"), dizia
"a tabela de regras nao identificou sinais de alerta" e, quando a cerca de negativa clinica barrava
essa frase, o turno virava P3 `falha_tecnica` por sorteio. O texto fixo nao tem nenhum desses
defeitos por construcao: nao ha modelo para sortear.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import RESPOSTA_SINTOMA_SEM_ALERTA, HelenaGraph, HelenaState
from maezo.agents.helena.prompts import menciona_encaminhamento, motivo_de_recusa
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

#: Rascunhos reais da bateria de 01/10, um por defeito. NENHUM pode chegar a pessoa.
_RASCUNHOS_DA_BATERIA = [
    "Entendi que seu filho esta com febre. Se a febre persistir por mais de 48 horas, procure atendimento.",
    "Para dores leves, voce pode aplicar gelo por 15 minutos a cada 2 horas e manter o pe elevado.",
    "A tabela de regras nao identificou sinais de alerta nesta mensagem.",
    "Pela sua descricao nao ha sinais de alerta.",
]


class _Gravador:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._respostas.pop(0) if self._respostas else "RASCUNHO-DO-MODELO"


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


def _classify(**campos: Any) -> str:
    base: dict[str, Any] = {
        "intent": "symptom",
        "population": "adult",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(campos)
    return json.dumps(base)


def _dmn_sem_bandeira() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE", "motivo": "Sem criterio de red flag"}],
    )
    return dmn


def _graph(gravador: _Gravador, whatsapp: _WhatsApp) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, gravador),
        dmn=_dmn_sem_bandeira(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rascunho", _RASCUNHOS_DA_BATERIA)
async def test_sintoma_sem_bandeira_recebe_o_texto_fixo_qualquer_que_seja_o_rascunho(rascunho: str) -> None:
    gravador = _Gravador([_classify(), rascunho])
    whatsapp = _WhatsApp()
    estado: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:sintoma-fixo",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-SF",
        "message_body": "estou com dor de cabeca",
    }

    resultado = dict(await _graph(gravador, whatsapp).compile_graph().compile().ainvoke(estado))

    assert resultado["response_text"] == RESPOSTA_SINTOMA_SEM_ALERTA
    assert whatsapp.enviados == [RESPOSTA_SINTOMA_SEM_ALERTA]
    assert len(gravador.prompts) == 1, "so' a classificacao chama o modelo; a resposta nao"
    assert not resultado.get("escalation_started"), "nenhuma fila aberta por sorteio de redacao"
    assert resultado.get("escalation_motivo") is None


@pytest.mark.asyncio
async def test_a_frase_de_confirmacao_de_dado_entra_na_frente_do_texto_fixo() -> None:
    """F5: a frase de confirmacao e' pronta (vem de `classify`) e nao pode se perder."""
    graph = _graph(_Gravador([]), _WhatsApp())
    estado = cast(
        HelenaState,
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:sintoma-fixo",
            "message_body": "tenho 70 anos",
            "intent": "symptom",
            "memoria_a_confirmar": "Entendi que o sintoma e febre, certo?",
        },
    )

    saida = await graph.inform(estado)

    assert saida["response_text"] == f"Entendi que o sintoma e febre, certo? {RESPOSTA_SINTOMA_SEM_ALERTA}"


def test_o_texto_fixo_nao_traz_nenhum_dos_defeitos_da_bateria() -> None:
    texto = RESPOSTA_SINTOMA_SEM_ALERTA.lower()

    for proibido in (
        "tabela",
        "regra",
        "grave",
        "sinais de alerta",
        "gelo",
        "repouso",
        "48 horas",
        "diagnostic",
    ):
        assert proibido not in texto, proibido
    assert "contato" not in texto, "inform nao pode prometer que alguem vai procurar a pessoa"


def test_o_texto_fixo_passa_nas_cercas_de_inform_e_nao_anuncia_encaminhamento() -> None:
    """`inform` nao pode prometer humano: nenhum humano foi acionado neste turno."""
    assert motivo_de_recusa(RESPOSTA_SINTOMA_SEM_ALERTA, "inform", start_aconteceu=False) is None
    assert menciona_encaminhamento(RESPOSTA_SINTOMA_SEM_ALERTA) is False


def test_cada_frase_do_texto_fixo_cabe_na_regua_de_clareza() -> None:
    """`EVL-HELENA-CLAREZA-*`: no maximo 20 palavras por frase."""
    for frase in re.split(r"(?<=[.!?])\s+", RESPOSTA_SINTOMA_SEM_ALERTA):
        assert len(frase.split()) <= 20, frase
