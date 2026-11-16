"""E-SF-17 / GTM-E-13: a passagem Helena -> agente `comercial`, depois das travas de saude.

Guardas nomeadas no plano: `test_saude_vence_comercial` (red flag + intencao de compra ->
`escalate` `red_flag_clinico`, sem passagem comercial) e `test_handoff_comercial_sem_texto` (corpo
anexado a passagem -> recusa tipada). Mais: desligado = grafo de antes; ligado, "quero contratar"
sem sinal de saude -> passagem tipada so' com referencia; paridade do formato entre os dois lados.
"""

from __future__ import annotations

import json
from typing import Any, cast, get_type_hints

import pytest

from maezo.agents.comercial.passagem import (
    CAMPOS_DA_PASSAGEM,
    ComercialInputError,
    parse_passagem,
)
from maezo.agents.comercial.passagem import PASSAGEM_SCHEMA as SCHEMA_DO_COMERCIAL
from maezo.agents.helena.graph import RESPONSE_KIND_HANDOFF, HelenaGraph, HelenaState, new_helena_state
from maezo.agents.helena.habilidades.passagem import comercial as passagem_comercial
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_REF = "hk1_" + "a" * 64
_CONTRATAR = "quero contratar um plano para minha familia"


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(self, prompt: str, **_: Any) -> str:
        return self._responses.pop(0) if self._responses else ""


class _Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.sent.append((to_hash, text))
        return {"ok": True}


def _extracao(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(overrides)
    return json.dumps(base)


def _grafo(respostas: list[str], *, ligado: bool, dmn: FakeDmnTransport | None = None) -> tuple[Any, _Sender]:
    sender = _Sender()
    g = HelenaGraph(
        inference=cast(InferenceProvider, _FakeInference(respostas)),
        dmn=dmn or FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=sender,
        roteador_comercial_enabled=ligado,
    )
    return g.compile_graph().compile(), sender


def _entrada(texto: str, **kw: Any) -> HelenaState:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-1",
        "message_body": texto,
        "message_ref": _REF,
    }
    base.update(kw)
    return new_helena_state(**base)


# --- Desligado = a Helena de antes -------------------------------------------------------------


def test_desligado_o_grafo_e_o_de_antes_no_a_no() -> None:
    antes, _ = _grafo([], ligado=False)
    ligado, _ = _grafo([], ligado=True)
    nos_antes = set(antes.get_graph().nodes)
    assert passagem_comercial.NO_HANDOFF_COMERCIAL not in nos_antes
    assert set(ligado.get_graph().nodes) - nos_antes == {passagem_comercial.NO_HANDOFF_COMERCIAL}


def test_build_so_liga_com_true_explicito() -> None:
    from maezo.agents.helena.graph import build

    deps = {
        "inference": _FakeInference([]),
        "dmn": FakeDmnTransport(),
        "cibseven": FakeCibSevenTransport(),
        "whatsapp": _Sender(),
        "audit_sink": FakeStartAuditSink(),
    }
    for valor in (None, "true", 1):
        nos = build({**deps, "roteador_comercial_enabled": valor}).compile().get_graph().nodes
        assert passagem_comercial.NO_HANDOFF_COMERCIAL not in nos


async def test_desligado_quero_contratar_nao_passa_ao_comercial() -> None:
    compilado, _ = _grafo([_extracao(), "Posso ajudar com informacoes do seu plano."], ligado=False)
    resultado = await compilado.ainvoke(_entrada(_CONTRATAR))
    assert resultado.get("handoff_comercial") is None
    assert resultado.get("response_kind") != RESPONSE_KIND_HANDOFF


# --- Ligado: intencao de contratar sem saude -> passagem tipada --------------------------------


async def test_ligado_quero_contratar_sem_saude_gera_handoff_tipado() -> None:
    compilado, sender = _grafo([_extracao()], ligado=True)
    resultado = await compilado.ainvoke(_entrada(_CONTRATAR))
    assert resultado["handoff_comercial"] == {
        "schema_version": passagem_comercial.PASSAGEM_SCHEMA,
        "para": "comercial",
        "motivo": "intencao_contratar",
        "message_ref": _REF,
    }
    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    # Quem se apresenta e' o comercial: a Helena nao envia frase nenhuma.
    assert sender.sent == []
    # O lado do comercial aceita exatamente o que a Helena produziu.
    assert parse_passagem(resultado["handoff_comercial"]).message_ref == _REF


async def test_saude_vence_comercial() -> None:
    """Red flag + intencao de compra -> `escalate` `red_flag_clinico`, sem passagem comercial."""
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "dor"}],
    )
    compilado, _ = _grafo(
        [_extracao(intent="symptom", sintoma_codigo="dor_toracica", population="adult", intensidade="grave")],
        ligado=True,
        dmn=dmn,
    )
    resultado = await compilado.ainvoke(_entrada("quero contratar um plano, estou com dor forte no peito"))
    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert resultado.get("handoff_comercial") is None
    assert resultado.get("response_kind") != RESPONSE_KIND_HANDOFF


@pytest.mark.parametrize(
    "estado,token",
    [
        ({"sinal_saude_lexico": True}, "sinal_de_saude"),
        ({"psychosocial_risk": True}, "nao_informativo"),
        ({"pedido_humano_lexico": True}, "pedido_de_pessoa"),
        ({"message_ref": "hk1_curto"}, "message_ref_invalida"),
        ({"next_kind": "escalate"}, "nao_informativo"),
        ({"message_body": "qual o horario de atendimento?"}, "sem_intencao_de_contratar"),
    ],
)
def test_a_precondicao_recusa_cada_condicao(estado: dict[str, Any], token: str) -> None:
    g = HelenaGraph(
        inference=cast(InferenceProvider, _FakeInference([])),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_Sender(),
        roteador_comercial_enabled=True,
    )
    base: dict[str, Any] = {
        "next_kind": "inform",
        "intent": "information",
        "message_body": _CONTRATAR,
        "message_ref": _REF,
        "sinal_saude_lexico": False,
        "pedido_humano_lexico": False,
    }
    assert passagem_comercial.passagem_recusada(g, base) is None
    assert passagem_comercial.passagem_recusada(g, {**base, **estado}) == token


# --- A passagem e' so' referencia --------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"message_body": "quero contratar"},
        {"sintoma": "dor no peito"},
        {"motivo_saude": "gestante"},
        {"telefone": "11999990000"},
    ],
)
def test_handoff_comercial_sem_texto(extra: dict[str, Any]) -> None:
    """Corpo/sintoma/telefone anexado a passagem -> recusa tipada, sem eco do valor."""
    valido = passagem_comercial.construir_handoff(_REF)
    with pytest.raises(ComercialInputError) as erro:
        parse_passagem({**valido, **extra})
    assert str(erro.value) == "comercial_passagem_contract_mismatch"
    for valor in extra.values():
        assert valor not in str(erro.value)


def test_message_ref_crua_e_recusada_nos_dois_lados() -> None:
    with pytest.raises(ValueError):
        passagem_comercial.construir_handoff("wamid.HBgLNTUxMTk5OTk5")
    with pytest.raises(ComercialInputError):
        parse_passagem({**passagem_comercial.construir_handoff(_REF), "message_ref": "wamid.HBgL"})


def test_paridade_do_formato_entre_helena_e_comercial() -> None:
    assert frozenset(get_type_hints(passagem_comercial.HandoffComercial)) == CAMPOS_DA_PASSAGEM
    assert passagem_comercial.PASSAGEM_SCHEMA == SCHEMA_DO_COMERCIAL
