"""Onda (e) do numero unico (ADR-0062; plano `docs/plans/lucas-numero-unico.md` §2.3-§2.5, §4):
a intencao `cobranca` na Helena e a frase de passagem.

O que este arquivo prova, na ordem do plano:

1. DESLIGADO = o de antes: o prompt de classify e' o `classify-v5` byte a byte (sha256 fixado), o
   validador recusa `cobranca` como `invalid_intent` e o grafo nem tem o no' `handoff_cobranca`.
2. LIGADO, a passagem so' acontece pela precondicao de duas camadas (`_handoff_recusado`):
   cobranca + sintoma -> triagem; cobranca + risco -> P1; cobranca + lexico de pessoa ->
   `solicitacao_humano`; classify falho -> `falha_tecnica`, nunca Lucas.
3. A frase sai UMA vez por passagem (so' com a Helena ativa) e passa nas quatro cercas de saida.
4. As copias de vocabulario (subtipos, desfechos, rota) nao divergem das fontes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from maezo.agents.helena.graph import (
    _VALID_COBRANCA_SUBTIPOS,
    DESFECHO_PASSAGEM_COBRANCA,
    DESFECHO_PASSAGEM_SEM_FRASE,
    FRASE_PASSAGEM_COBRANCA,
    PROMPT_VERSIONS,
    RESPONSE_KIND_HANDOFF,
    RESPOSTA_HANDOFF_RECUSADA,
    HelenaGraph,
    HelenaState,
    _handoff_recusado,
    _texto_tem_negativa_clinica,
    _validate_extraction,
    gate_inbound_state,
    new_helena_state,
)
from maezo.agents.helena.prompts import (
    CLASSIFY_PROMPT_VERSION,
    CLASSIFY_PROMPT_VERSION_ROTEADOR,
    classify_prompt,
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

#: sha256 do texto do `classify-v5` (01/10/2026, base 9da9bd59). Desligado, o roteador NAO pode
#: mudar um byte do que vai ao modelo: se este numero mudar, o v5 mudou, e isso e' bump de versao.
SHA256_CLASSIFY_V5 = "5b480bd3d62d5641fb04a1d18bdf9d23cbfb5890ec2d63d7d881ca8ca9172dcc"

_REF = "hk1_abc123"
#: Rascunho de handoff para as rotas que escalam (o fake devolve "" quando acaba a lista, e a
#: guarda de resposta vazia sobrescreveria o `error` que o teste quer ler).
_HANDOFF = RESPOSTA_HANDOFF_RECUSADA


class _FakeInference:
    def __init__(self, responses: list[str] | None = None, *, fail: bool = False) -> None:
        self._responses = list(responses or [])
        self._fail = fail
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if self._fail:
            raise TimeoutError("provedor fora")
        return self._responses.pop(0) if self._responses else ""


class _Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.sent.append((to_hash, text))
        return {"ok": True}


def _json(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "intent": "cobranca",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "cobranca_subtipo": "boleto_2via",
        "competencia": None,
    }
    base.update(overrides)
    return json.dumps(base)


def _grafo(
    inference: Any, *, ligado: bool = True, dmn: FakeDmnTransport | None = None
) -> tuple[Any, _Sender, FakeCibSevenTransport]:
    sender = _Sender()
    cib = FakeCibSevenTransport()
    g = HelenaGraph(
        inference=inference,
        dmn=dmn or FakeDmnTransport(),
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=sender,
        roteador_lucas_enabled=ligado,
    )
    return g.compile_graph().compile(), sender, cib


def _entrada(**kw: Any) -> HelenaState:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-1",
        "message_body": "preciso da segunda via do boleto",
        "message_ref": _REF,
    }
    base.update(kw)
    return new_helena_state(**base)


# --- 1. Desligado = o de antes -----------------------------------------------------------------


def test_desligado_o_prompt_e_o_classify_v5_byte_a_byte() -> None:
    texto = classify_prompt()
    assert hashlib.sha256(texto.encode("utf-8")).hexdigest() == SHA256_CLASSIFY_V5
    assert classify_prompt(roteador_lucas=False) == texto
    assert CLASSIFY_PROMPT_VERSION == "classify-v5"
    assert '"cobranca"' not in texto.split("population")[0]


def test_ligado_o_prompt_e_o_v6_e_so_ele_conhece_cobranca() -> None:
    v6 = classify_prompt(roteador_lucas=True)
    assert v6 != classify_prompt()
    assert CLASSIFY_PROMPT_VERSION_ROTEADOR == "classify-v6"
    assert PROMPT_VERSIONS["classify_roteador"] == "classify-v6"
    assert '"cobranca"]' in v6
    for subtipo in _VALID_COBRANCA_SUBTIPOS:
        assert f'"{subtipo}"' in v6


async def test_desligado_o_classify_envia_o_v5_ao_modelo() -> None:
    inferencia = _FakeInference([_json(intent="information", cobranca_subtipo=None)])
    compilado, _, _ = _grafo(inferencia, ligado=False)
    await compilado.ainvoke(_entrada())
    assert inferencia.prompts[0].startswith(classify_prompt())
    assert not inferencia.prompts[0].startswith(classify_prompt(roteador_lucas=True))


def test_desligado_cobranca_e_intent_invalido() -> None:
    assert _validate_extraction(json.loads(_json())) == "invalid_intent"
    assert _validate_extraction(json.loads(_json()), cobranca_habilitada=True) is None


@pytest.mark.parametrize(
    ("dados", "falha"),
    [
        ({"cobranca_subtipo": "estorno"}, "invalid_cobranca_subtipo"),
        ({"cobranca_subtipo": None}, "invalid_cobranca_subtipo"),
        ({"cobranca_subtipo": ["boleto_2via"]}, "invalid_cobranca_subtipo"),
        ({"intent": "information", "cobranca_subtipo": "boleto_2via"}, "invalid_cobranca_subtipo"),
        ({"competencia": "09/2026"}, "invalid_competencia"),
        ({"competencia": "2026-13"}, "invalid_competencia"),
        ({"competencia": 202609}, "invalid_competencia"),
    ],
)
def test_ligado_o_dominio_de_cobranca_e_fechado(dados: dict[str, Any], falha: str) -> None:
    assert _validate_extraction(json.loads(_json(**dados)), cobranca_habilitada=True) == falha


async def test_desligado_o_grafo_nao_tem_o_no_de_passagem() -> None:
    compilado, _, _ = _grafo(_FakeInference(), ligado=False)
    assert "handoff_cobranca" not in compilado.get_graph().nodes
    ligado, _, _ = _grafo(_FakeInference(), ligado=True)
    assert "handoff_cobranca" in ligado.get_graph().nodes


async def test_desligado_um_cobranca_do_modelo_vira_falha_tecnica_como_hoje() -> None:
    compilado, sender, _ = _grafo(_FakeInference([_json(), _json(), _HANDOFF, "resumo"]), ligado=False)
    resultado = await compilado.ainvoke(_entrada())
    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert "invalid_intent" in resultado["error"]
    assert resultado.get("handoff") is None
    assert FRASE_PASSAGEM_COBRANCA not in [t for _, t in sender.sent]


# --- 2. A precondicao de duas camadas ----------------------------------------------------------


async def test_ligado_cobranca_limpa_passa_ao_lucas_com_a_frase() -> None:
    compilado, sender, cib = _grafo(_FakeInference([_json(competencia="2026-09")]))
    resultado = await compilado.ainvoke(_entrada())

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["handoff"] == {
        "para": "lucas",
        "cobranca_subtipo": "boleto_2via",
        "competencia": "2026-09",
        "message_ref": _REF,
    }
    assert [t for _, t in sender.sent] == [FRASE_PASSAGEM_COBRANCA]
    assert resultado["desfecho"] == DESFECHO_PASSAGEM_COBRANCA
    assert not resultado.get("escalation_started")
    assert cib._history == {}  # nenhum processo iniciado


async def test_ligado_o_handoff_nao_carrega_texto_do_beneficiario() -> None:
    compilado, _, _ = _grafo(_FakeInference([_json()]))
    resultado = await compilado.ainvoke(_entrada(message_body="boleto 2a via por favor"))
    assert set(resultado["handoff"]) == {"para", "cobranca_subtipo", "competencia", "message_ref"}
    assert "boleto 2a via" not in json.dumps(resultado["handoff"])


async def test_ligado_com_o_lucas_ativo_a_helena_nao_envia_nada() -> None:
    compilado, sender, _ = _grafo(_FakeInference([_json(cobranca_subtipo="vencimento")]))
    resultado = await compilado.ainvoke(_entrada(agente_ativo_conversa="lucas"))
    assert resultado["handoff"]["cobranca_subtipo"] == "vencimento"
    assert sender.sent == []
    assert resultado["desfecho"] == DESFECHO_PASSAGEM_SEM_FRASE
    assert not resultado.get("error")


async def test_ligado_cobranca_com_sintoma_vai_para_a_triagem_sem_handoff() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "dor"}],
    )
    inferencia = _FakeInference(
        [_json(sintoma_codigo="dor_toracica", population="adult", intensidade="grave")]
    )
    compilado, sender, _ = _grafo(inferencia, dmn=dmn)
    resultado = await compilado.ainvoke(_entrada(message_body="o boleto venceu e estou com dor no peito"))

    assert dmn.calls and dmn.calls[0][0] == "triage_redflag_adult"
    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert resultado.get("handoff") is None
    assert FRASE_PASSAGEM_COBRANCA not in [t for _, t in sender.sent]


async def test_ligado_cobranca_com_sintoma_sem_bandeira_segue_o_caminho_do_sintoma() -> None:
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE"}])
    inferencia = _FakeInference([_json(sintoma_codigo="febre", population="adult"), "texto informativo"])
    compilado, _, _ = _grafo(inferencia, dmn=dmn)
    resultado = await compilado.ainvoke(_entrada(message_body="o boleto venceu e estou com febre"))
    assert resultado["intent"] == "symptom"
    assert resultado.get("handoff") is None
    assert resultado.get("cobranca_subtipo") is None


async def test_ligado_cobranca_com_risco_psicossocial_e_p1() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_mental_health",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "risco"}],
    )
    inferencia = _FakeInference([_json(psychosocial_risk=True, cobranca_subtipo="cancelamento")])
    compilado, sender, _ = _grafo(inferencia, dmn=dmn)
    resultado = await compilado.ainvoke(
        _entrada(message_body="quero cancelar porque nao aguento mais viver", sinal_saude_lexico=True)
    )
    assert resultado["escalation_motivo"] == "risco_psicossocial"
    assert resultado["escalation_severidade"] == "grave"
    assert resultado.get("handoff") is None
    assert FRASE_PASSAGEM_COBRANCA not in [t for _, t in sender.sent]


async def test_ligado_cobranca_com_lexico_de_pessoa_e_solicitacao_humano() -> None:
    compilado, sender, _ = _grafo(_FakeInference([_json()]))
    resultado = await compilado.ainvoke(
        _entrada(message_body="quero falar com um atendente sobre o boleto", pedido_humano_lexico=True)
    )
    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert resultado.get("handoff") is None
    assert FRASE_PASSAGEM_COBRANCA not in [t for _, t in sender.sent]


async def test_o_lexico_de_pessoa_nao_reabre_a_pergunta_de_identidade() -> None:
    """Caso `A2` (23/09): "voce e humano?" e' pergunta, nao pedido — mesmo com o termo no lexico."""
    inferencia = _FakeInference([_json(intent="information", cobranca_subtipo=None), "Sou Helena."])
    compilado, _, _ = _grafo(inferencia)
    resultado = await compilado.ainvoke(_entrada(message_body="voce e humano?", pedido_humano_lexico=True))
    assert resultado.get("escalation_motivo") is None


async def test_ligado_cobranca_com_lexico_de_saude_nunca_vai_ao_lucas() -> None:
    compilado, sender, _ = _grafo(_FakeInference([_json(), _HANDOFF, "resumo"]))
    resultado = await compilado.ainvoke(_entrada(sinal_saude_lexico=True))
    assert resultado.get("handoff") is None
    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert resultado["error"] == "handoff recusado: sinal_saude_lexico"
    assert FRASE_PASSAGEM_COBRANCA not in [t for _, t in sender.sent]


@pytest.mark.parametrize("bruto", ["nao e json", '{"intent": "cobranca"}', _json(cobranca_subtipo="estorno")])
async def test_ligado_classify_falho_nunca_vai_ao_lucas(bruto: str) -> None:
    compilado, sender, _ = _grafo(_FakeInference([bruto]))
    resultado = await compilado.ainvoke(_entrada())
    assert resultado.get("handoff") is None
    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert FRASE_PASSAGEM_COBRANCA not in [t for _, t in sender.sent]


async def test_ligado_provedor_fora_nunca_vai_ao_lucas() -> None:
    compilado, _, _ = _grafo(_FakeInference(fail=True))
    resultado = await compilado.ainvoke(_entrada())
    assert resultado.get("handoff") is None
    assert resultado["escalation_motivo"] == "falha_tecnica"


async def test_ligado_sem_referencia_da_mensagem_nao_passa() -> None:
    compilado, _, _ = _grafo(_FakeInference([_json(), _HANDOFF, "resumo"]))
    resultado = await compilado.ainvoke(_entrada(message_ref=""))
    assert resultado.get("handoff") is None
    assert resultado["error"] == "handoff recusado: sem_referencia_da_mensagem"


@pytest.mark.parametrize(
    ("estado", "token"),
    [
        ({"error": "x"}, "erro_do_turno"),
        ({"intent": "information"}, "intent_nao_e_cobranca"),
        ({"psychosocial_risk": True}, "risco_psicossocial"),
        ({"psychosocial_risk": None}, "risco_psicossocial"),
        ({"sintoma_codigo": "febre"}, "sintoma_reportado"),
        ({"sinal_saude_lexico": True}, "sinal_saude_lexico"),
        ({"pedido_humano_lexico": True}, "pedido_humano_lexico"),
        ({"cobranca_subtipo": "estorno"}, "subtipo_fora_do_dominio"),
        ({"cobranca_competencia": "2026-9"}, "competencia_invalida"),
    ],
)
def test_handoff_recusado_cobre_cada_precondicao(estado: dict[str, Any], token: str) -> None:
    limpo = {
        "intent": "cobranca",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "sinal_saude_lexico": False,
        "pedido_humano_lexico": False,
        "cobranca_subtipo": "outro",
        "cobranca_competencia": None,
        "message_ref": _REF,
    }
    assert _handoff_recusado(limpo, roteador_ligado=True) is None
    assert _handoff_recusado(limpo, roteador_ligado=False) == "roteador_desligado"
    assert _handoff_recusado({**limpo, **estado}, roteador_ligado=True) == token


async def test_camada_dois_a_aresta_barra_um_handoff_injustificado() -> None:
    """Backstop estrutural: um `next_kind="handoff"` sem a precondicao nao chega ao Lucas."""
    g = HelenaGraph(
        inference=_FakeInference(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_Sender(),
        roteador_lucas_enabled=True,
    )
    estado = {
        "next_kind": "handoff",
        "intent": "cobranca",
        "psychosocial_risk": False,
        "sintoma_codigo": "febre",
    }
    assert g._route_com_roteador(estado) == "escalate"  # type: ignore[arg-type]
    estado_limpo = {
        **estado,
        "sintoma_codigo": None,
        "sinal_saude_lexico": False,
        "pedido_humano_lexico": False,
        "cobranca_subtipo": "boleto_2via",
        "message_ref": _REF,
    }
    assert g._route_com_roteador(estado_limpo) == "handoff"  # type: ignore[arg-type]


# --- 3. A frase ---------------------------------------------------------------------------------


def test_a_frase_e_a_do_plano() -> None:
    assert FRASE_PASSAGEM_COBRANCA == "Vou te passar para o atendimento de cobrança."


@pytest.mark.parametrize("rota", ["handoff", "inform"])
def test_a_frase_passa_nas_quatro_cercas_de_saida(rota: str) -> None:
    """Promessa de humano / capacidade (`motivo_de_recusa`, com e sem o fato do start), canal
    nao confirmado, mencao de encaminhamento e negativa clinica."""
    for start in (None, False):
        assert motivo_de_recusa(FRASE_PASSAGEM_COBRANCA, rota, start_aconteceu=start) is None
    assert motivo_de_canal_nao_confirmado(FRASE_PASSAGEM_COBRANCA) is None
    assert menciona_encaminhamento(FRASE_PASSAGEM_COBRANCA) is False
    assert _texto_tem_negativa_clinica(FRASE_PASSAGEM_COBRANCA) is False


# --- 4. Fronteira de entrada e copias de vocabulario --------------------------------------------


def test_new_helena_state_grava_o_neutro_e_recusa_fora_do_dominio() -> None:
    estado = new_helena_state(
        tenant_id="amh",
        conversation_id="wa:amh:hk1_x",
        canal="whatsapp",
        beneficiario_pseudo_id="p",
        message_body="oi",
    )
    assert estado["agente_ativo_conversa"] == "helena"
    assert estado["pedido_humano_lexico"] is False
    assert estado["sinal_saude_lexico"] is False
    assert estado["message_ref"] == ""
    with pytest.raises(ValueError):
        _entrada(agente_ativo_conversa="fernando")
    with pytest.raises(ValueError):
        _entrada(pedido_humano_lexico=1)


def test_gate_inbound_nao_deixa_um_mapeamento_cru_escolher_o_lucas() -> None:
    gated = gate_inbound_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_x",
            "agente_ativo_conversa": "lucas",
            "pedido_humano_lexico": "sim",
            "sinal_saude_lexico": True,
            "handoff": {"para": "lucas"},
        }
    )
    assert gated["agente_ativo_conversa"] == "helena"
    assert gated["pedido_humano_lexico"] is False
    assert gated["sinal_saude_lexico"] is True
    assert "handoff" not in gated


def test_os_subtipos_sao_os_do_roteador() -> None:
    from maezo.platform.webhooks.whatsapp.roteamento import COBRANCA_SUBTIPOS

    assert frozenset(COBRANCA_SUBTIPOS) == _VALID_COBRANCA_SUBTIPOS


def test_desfechos_e_rota_estao_no_vocabulario_de_telemetria() -> None:
    from maezo.runtime import turn_telemetry

    assert {DESFECHO_PASSAGEM_COBRANCA, DESFECHO_PASSAGEM_SEM_FRASE} <= turn_telemetry._DESFECHO_VOCAB[
        "helena"
    ]
    assert RESPONSE_KIND_HANDOFF in turn_telemetry._ROUTE_VOCAB["helena"]
