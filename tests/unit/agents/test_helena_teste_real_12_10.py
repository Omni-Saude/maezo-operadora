"""DL-0088 — o teste real do dono no WhatsApp de dev (12/10/2026), mensagem por mensagem.

O dono escreveu como um beneficiario comum, misturando assuntos, e o log do receptor mostrou quatro
defeitos. Cada bloco abaixo reproduz UMA mensagem real com o modelo simulado (a saida que ele deu, ou a
pior plausivel) e fixa o comportamento que o dono decidiu:

  1. "Quais informacoes financeiras ou de cobranca minhas vc sabe?" — subtipo invalido virava
     `falha_tecnica` e a pessoa lia que o caso foi para a "equipe de saude". Agora vai ao Lucas.
  2. "estou com dor no peito" — escalava certo, sem o 192. Agora o texto traz o 192, inclusive com
     caso ja' aberto.
  3. "eu quero tirar uma duvida sobre minha saude" e "Voce nao pode me tirar a duvida? eu nao quero
     falar com nenhuma equipe" — o classificador carregava a dor do historico, a tabela marcava de
     novo e a Helena repetia "seu atendimento ja esta aberto". Agora: duvida sem sintoma nao escala, a
     recusa e' respeitada, e o aviso de caso aberto nao se repete igual.
  4. "Voce e capaz de responder quais informacoes sobre mim?" — o modelo dizia "nao consigo ver seus
     dados", falso. Agora: texto fixo que aponta onde perguntar, sem a negativa.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.helena.graph import (
    CONVERSA_DUVIDA_DE_SAUDE,
    CONVERSA_RECUSA_DE_EQUIPE,
    FRASE_PASSAGEM_COBRANCA,
    PROCESS_KEY,
    RESPONSE_KIND_HANDOFF,
    RESPOSTA_DUVIDA_DE_SAUDE,
    RESPOSTA_ESCALONAMENTO_URGENTE,
    RESPOSTA_HANDOFF_JA_ABERTO,
    RESPOSTA_HANDOFF_JA_ABERTO_DE_NOVO,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_HANDOFF_SAUDE,
    RESPOSTA_O_QUE_SEI_DE_VOCE,
    RESPOSTA_RECUSA_DE_EQUIPE,
    START_DESFECHO_JA_ATIVO,
    SUBTIPO_COBRANCA_PADRAO,
    HelenaGraph,
    HelenaState,
    _conversa_sem_relato_clinico,
    _pergunta_financeira_sobre_a_pessoa,
    _pergunta_sobre_o_que_sabe,
    _validate_extraction,
    new_helena_state,
)
from maezo.agents.helena.prompts import (
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

EMERGENCIA_192 = "Se for uma emergência, ligue agora para o 192 (SAMU) ou vá ao pronto-socorro mais próximo."
_CONVERSA = "wa:amh:hk1_dono1210"
_CHAVE = f"ESC-amh-{_CONVERSA}"
_REF = "hk1_msg1210"


class _Modelo:
    """O classificador/redator simulado: devolve as respostas na ordem, e grava os prompts."""

    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._respostas.pop(0) if self._respostas else "Resumo para o atendente."


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


def _classify(**campos: Any) -> str:
    base: dict[str, Any] = {
        "intent": "information",
        "population": "adult",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "cobranca_subtipo": None,
        "competencia": None,
    }
    base.update(campos)
    return json.dumps(base)


def _dmn_dor_toracica() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "dor toracica"}],
    )
    return dmn


def _caso_aberto(motivo: str = "red_flag_clinico", chave: str = _CHAVE) -> FakeCibSevenTransport:
    """O estado do teste do dono: um escalonamento JA aberto nesta conversa."""
    cib = FakeCibSevenTransport()
    cib.seed_instance(
        ProcessInstance(
            instance_id="caso-aberto-do-dono",
            process_key=PROCESS_KEY,
            business_key=chave,
            state="ACTIVE",
            already_existed=True,
        ),
        variables={"motivo_categoria": motivo},
    )
    return cib


def _grafo(
    respostas: list[str],
    *,
    cib: FakeCibSevenTransport | None = None,
    roteador: bool = True,
) -> tuple[Any, _WhatsApp, _Modelo, FakeCibSevenTransport]:
    modelo = _Modelo(respostas)
    whatsapp = _WhatsApp()
    cibseven = cib or FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=_dmn_dor_toracica(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=roteador,
        relogio=lambda: datetime(2026, 10, 12, 15, 0, tzinfo=UTC),
    )
    return grafo, whatsapp, modelo, cibseven


def _entrada(mensagem: str, **kw: Any) -> HelenaState:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": _CONVERSA,
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-DONO",
        "message_body": mensagem,
        "message_ref": _REF,
    }
    base.update(kw)
    return new_helena_state(**base)


async def _turno(grafo: HelenaGraph, entrada: HelenaState) -> dict[str, Any]:
    return dict(await grafo.compile_graph().compile().ainvoke(entrada))


def _starts(cib: FakeCibSevenTransport) -> int:
    return sum(len(v) for v in cib._history.values())


# =================================================================================================
# 1. "Quais informacoes financeiras ou de cobranca minhas vc sabe?" (A, B, F)
# =================================================================================================

MSG_1 = "Quais informações financeiras ou de cobrança minhas vc sabe?"


async def test_msg1_subtipo_invalido_do_modelo_vai_ao_lucas_e_nao_vira_falha_tecnica() -> None:
    """O log real: `invalid_cobranca_subtipo` -> `falha_tecnica` -> "equipe de saude". Agora: Lucas."""
    grafo, whatsapp, _, cib = _grafo([_classify(intent="cobranca", cobranca_subtipo="informacoes_gerais")])

    resultado = await _turno(grafo, _entrada(MSG_1))

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["handoff"]["cobranca_subtipo"] == SUBTIPO_COBRANCA_PADRAO == "consulta_valores"
    assert resultado.get("escalation_motivo") != "falha_tecnica"
    assert not resultado.get("error")
    assert whatsapp.enviados == [FRASE_PASSAGEM_COBRANCA]
    assert _starts(cib) == 0, "nenhum escalonamento aberto"


async def test_msg1_mesmo_com_o_modelo_dizendo_information_a_pergunta_financeira_vai_ao_lucas() -> None:
    resultado, whatsapp, _, _ = await _grafo_e_turno_information(MSG_1)
    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["handoff"]["cobranca_subtipo"] == "consulta_valores"
    assert whatsapp.enviados == [FRASE_PASSAGEM_COBRANCA]


async def _grafo_e_turno_information(
    mensagem: str, *, roteador: bool = True
) -> tuple[dict[str, Any], _WhatsApp, _Modelo, FakeCibSevenTransport]:
    grafo, whatsapp, modelo, cib = _grafo([_classify(intent="information")], roteador=roteador)
    return await _turno(grafo, _entrada(mensagem)), whatsapp, modelo, cib


async def test_msg1_falha_tecnica_de_verdade_usa_o_texto_neutro_sem_equipe_de_saude() -> None:
    """B: se o classificador falhar de fato (JSON ilegivel duas vezes), escala `falha_tecnica` — com o
    texto NEUTRO. A pessoa perguntou do financeiro e nunca le' "equipe de saude"."""
    grafo, whatsapp, _, _ = _grafo(["nao e json", "tambem nao"])

    resultado = await _turno(grafo, _entrada(MSG_1))

    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert whatsapp.enviados == [RESPOSTA_HANDOFF_RECUSADA]
    assert "saúde" not in whatsapp.enviados[0]
    assert EMERGENCIA_192 in whatsapp.enviados[0]


@pytest.mark.parametrize(
    ("dados", "subtipo_final"),
    [
        ({"intent": "cobranca", "cobranca_subtipo": "estorno"}, "consulta_valores"),
        ({"intent": "cobranca", "cobranca_subtipo": None}, "consulta_valores"),
        ({"intent": "cobranca", "cobranca_subtipo": ["boleto_2via"]}, "consulta_valores"),
        ({"intent": "information", "cobranca_subtipo": "boleto_2via"}, None),
        ({"intent": "cobranca", "cobranca_subtipo": "vencimento"}, "vencimento"),
    ],
)
def test_subtipo_fora_do_dominio_e_normalizado_e_nao_e_falha(
    dados: dict[str, Any], subtipo_final: str | None
) -> None:
    extracao = json.loads(_classify(**dados))
    assert _validate_extraction(extracao, cobranca_habilitada=True) is None
    assert extracao["cobranca_subtipo"] == subtipo_final


def test_o_texto_de_handoff_e_escolhido_pelo_motivo() -> None:
    assert "equipe de saúde" in RESPOSTA_HANDOFF_SAUDE
    assert "saúde" not in RESPOSTA_HANDOFF_RECUSADA
    for texto in (RESPOSTA_HANDOFF_RECUSADA, RESPOSTA_HANDOFF_SAUDE):
        assert motivo_de_recusa(texto, "escalate", start_aconteceu=True) is None
        assert motivo_de_canal_nao_confirmado(texto) is None
        assert menciona_encaminhamento(texto) is True


# =================================================================================================
# 2. "estou com dor no peito" (E e a seguranca de C)
# =================================================================================================


async def test_msg2_dor_no_peito_escala_com_o_192() -> None:
    grafo, whatsapp, _, _ = _grafo([_classify(intent="symptom", sintoma_codigo="dor_toracica")])

    resultado = await _turno(grafo, _entrada("estou com dor no peito", sinal_saude_lexico=True))

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    assert whatsapp.enviados == [RESPOSTA_ESCALONAMENTO_URGENTE]
    assert EMERGENCIA_192 in whatsapp.enviados[0]


async def test_msg2_dor_no_peito_com_caso_aberto_ainda_recebe_o_192() -> None:
    """Caso aberto NAO pode esconder a orientacao de emergencia de um sintoma novo."""
    grafo, whatsapp, _, _ = _grafo(
        [_classify(intent="symptom", sintoma_codigo="dor_toracica")], cib=_caso_aberto()
    )

    resultado = await _turno(grafo, _entrada("estou com dor no peito", sinal_saude_lexico=True))

    assert resultado["start_desfecho"] == START_DESFECHO_JA_ATIVO
    assert whatsapp.enviados == [RESPOSTA_HANDOFF_JA_ABERTO]
    assert EMERGENCIA_192 in whatsapp.enviados[0]


# =================================================================================================
# 3. Duvida de saude sem sintoma e recusa da equipe, com caso aberto (C, D)
# =================================================================================================

MSG_3A = "eu quero tirar uma dúvida sobre minha saude"
MSG_3B = "Você não pode me tirar a dúvida? eu nao quero falar com nenhuma equipe"


async def test_msg3a_duvida_de_saude_sem_sintoma_nao_escala_mesmo_com_o_modelo_carregando_a_dor() -> None:
    """O log real: o classificador levou a dor toracica do historico e a tabela marcou de novo."""
    cib = _caso_aberto()
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo([_classify(intent="symptom", sintoma_codigo="dor_toracica")], cib=cib)

    resultado = await _turno(grafo, _entrada(MSG_3A))

    assert resultado["response_kind"] == "inform"
    assert not resultado.get("escalation_started")
    assert resultado.get("sintoma_codigo") is None
    assert whatsapp.enviados == [RESPOSTA_DUVIDA_DE_SAUDE]
    assert _starts(cib) == antes, "nenhum processo novo"


async def test_msg3a_sem_caso_aberto_e_com_o_modelo_dizendo_pergunta_clinica_tambem_nao_escala() -> None:
    grafo, whatsapp, _, cib = _grafo([_classify(intent="clinical_question")])

    resultado = await _turno(grafo, _entrada(MSG_3A))

    assert resultado["response_kind"] == "inform"
    assert whatsapp.enviados == [RESPOSTA_DUVIDA_DE_SAUDE]
    assert _starts(cib) == 0


async def test_msg3b_recusa_da_equipe_e_respeitada_e_nao_repete_o_aviso_de_caso_aberto() -> None:
    cib = _caso_aberto()
    antes = _starts(cib)
    grafo, whatsapp, _, _ = _grafo(
        [_classify(intent="human_request", sintoma_codigo="dor_toracica")], cib=cib
    )

    resultado = await _turno(grafo, _entrada(MSG_3B))

    assert resultado["response_kind"] == "inform"
    assert whatsapp.enviados == [RESPOSTA_RECUSA_DE_EQUIPE]
    assert RESPOSTA_HANDOFF_JA_ABERTO not in whatsapp.enviados[0]
    assert "Fique tranquilo: nenhuma decisão sobre o seu plano foi tomada." in whatsapp.enviados[0]
    assert _starts(cib) == antes


async def test_recusa_com_a_palavra_atendente_nao_vira_pedido_de_pessoa_pelo_lexico() -> None:
    grafo, whatsapp, _, cib = _grafo([_classify(intent="information")])

    resultado = await _turno(
        grafo, _entrada("nao quero falar com nenhum atendente", pedido_humano_lexico=True)
    )

    assert resultado.get("escalation_motivo") is None
    assert whatsapp.enviados == [RESPOSTA_RECUSA_DE_EQUIPE]
    assert _starts(cib) == 0


async def test_pedido_de_pessoa_continua_escalando() -> None:
    grafo, _, _, _ = _grafo([_classify(intent="human_request")])

    resultado = await _turno(grafo, _entrada("quero tirar uma duvida com um atendente"))

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert resultado["escalation_started"] is True


async def test_caso_aberto_nao_trava_a_cobranca() -> None:
    """C: com um caso aberto, uma pergunta de cobranca segue para o Lucas."""
    grafo, whatsapp, _, cib = _grafo(
        [_classify(intent="cobranca", cobranca_subtipo="consulta_valores")], cib=_caso_aberto()
    )
    antes = _starts(cib)

    resultado = await _turno(grafo, _entrada("qual o valor da minha mensalidade?"))

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert whatsapp.enviados == [FRASE_PASSAGEM_COBRANCA]
    assert _starts(cib) == antes


async def test_o_aviso_de_caso_aberto_nao_sai_igual_duas_vezes_seguidas() -> None:
    grafo, whatsapp, _, _ = _grafo([])
    estado: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": _CONVERSA,
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-DONO",
        "message_body": "quero falar com alguem de novo",
        "response_text": RESPOSTA_HANDOFF_RECUSADA,
        "response_kind": "escalate",
        "escalation_started": True,
        "escalation_motivo": "solicitacao_humano",
        "start_desfecho": START_DESFECHO_JA_ATIVO,
        "historico_conversa": [
            {"papel": "beneficiario", "texto": "quero falar com alguem", "em": "2026-10-12T14:00:00+00:00"},
            {"papel": "helena", "texto": RESPOSTA_HANDOFF_JA_ABERTO, "em": "2026-10-12T14:00:01+00:00"},
        ],
    }

    saida = await grafo.respond(estado)

    assert whatsapp.enviados == [RESPOSTA_HANDOFF_JA_ABERTO_DE_NOVO]
    assert saida["response_text"] == RESPOSTA_HANDOFF_JA_ABERTO_DE_NOVO
    assert EMERGENCIA_192 in whatsapp.enviados[0]
    assert menciona_encaminhamento(whatsapp.enviados[0]) is True


async def test_sem_historico_o_aviso_de_caso_aberto_e_o_de_sempre() -> None:
    grafo, whatsapp, _, _ = _grafo([])
    estado: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": _CONVERSA,
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-DONO",
        "message_body": "quero falar com alguem",
        "response_text": RESPOSTA_HANDOFF_RECUSADA,
        "response_kind": "escalate",
        "escalation_started": True,
        "start_desfecho": START_DESFECHO_JA_ATIVO,
    }

    await grafo.respond(estado)

    assert whatsapp.enviados == [RESPOSTA_HANDOFF_JA_ABERTO]


@pytest.mark.parametrize(
    ("mensagem", "esperado"),
    [
        (MSG_3A, CONVERSA_DUVIDA_DE_SAUDE),
        (MSG_3B, CONVERSA_RECUSA_DE_EQUIPE),
        ("tenho uma pergunta", CONVERSA_DUVIDA_DE_SAUDE),
        ("nao preciso de atendente", CONVERSA_RECUSA_DE_EQUIPE),
        # Qualquer palavra fora do vocabulario de conversa devolve o caminho de sempre: sintoma
        # nunca passa por aqui, nem o que ninguem previu.
        ("estou com dor no peito", None),
        ("quero tirar uma duvida sobre minha saude, estou com febre", None),
        ("tenho uma duvida: posso tomar dipirona?", None),
        ("tenho uma duvida sobre formigamento no braco", None),
        # Pedido de pessoa (sem recusa) e recusa com pedido junto: o pedido vence.
        ("quero tirar uma duvida com um atendente", None),
        ("nao quero falar com ninguem, quero um humano", None),
        ("preciso de ajuda", None),
    ],
)
def test_conversa_sem_relato_clinico_e_vocabulario_fechado(mensagem: str, esperado: str | None) -> None:
    assert _conversa_sem_relato_clinico(mensagem) == esperado


@pytest.mark.parametrize(
    "texto", [RESPOSTA_DUVIDA_DE_SAUDE, RESPOSTA_RECUSA_DE_EQUIPE, RESPOSTA_O_QUE_SEI_DE_VOCE]
)
def test_os_textos_novos_passam_nas_cercas_de_um_turno_inform(texto: str) -> None:
    assert motivo_de_recusa(texto, "inform", start_aconteceu=False) is None
    assert motivo_de_canal_nao_confirmado(texto) is None
    assert menciona_encaminhamento(texto) is False, "inform nao anuncia encaminhamento"


# =================================================================================================
# 4. "Voce e capaz de responder quais informacoes sobre mim?" (F)
# =================================================================================================

MSG_4 = "Você é capaz de responder quais informações sobre mim?"


async def test_msg4_a_helena_nao_diz_que_nao_ve_dado_nenhum() -> None:
    resultado, whatsapp, modelo, cib = await _grafo_e_turno_information(MSG_4)

    assert resultado["response_kind"] == "inform"
    assert whatsapp.enviados == [RESPOSTA_O_QUE_SEI_DE_VOCE]
    assert "consigo ver" not in whatsapp.enviados[0]
    assert len(modelo.prompts) == 1, "texto fixo: o modelo so' classificou"
    assert _starts(cib) == 0


async def test_msg4_com_o_roteador_desligado_o_caminho_e_o_de_antes() -> None:
    """Sem o Lucas, "pergunte de mensalidade por aqui" seria mentira: o texto fixo so' existe ligado."""
    resultado, whatsapp, modelo, _ = await _grafo_e_turno_information(MSG_4, roteador=False)

    assert resultado["response_kind"] == "inform"
    assert whatsapp.enviados != [RESPOSTA_O_QUE_SEI_DE_VOCE]
    assert len(modelo.prompts) == 2, "classify + redacao do modelo"


@pytest.mark.parametrize(
    ("mensagem", "sobre_a_pessoa", "financeira"),
    [
        (MSG_1, True, True),
        (MSG_4, True, False),
        ("o que você sabe sobre mim?", True, False),
        ("quais dados meus de pagamento voce tem?", True, True),
        ("o que eu faço com meus exames?", False, False),
        ("qual o valor da minha mensalidade?", False, False),
    ],
)
def test_pergunta_sobre_o_que_a_helena_sabe(mensagem: str, sobre_a_pessoa: bool, financeira: bool) -> None:
    assert _pergunta_sobre_o_que_sabe(mensagem) is sobre_a_pessoa
    assert _pergunta_financeira_sobre_a_pessoa(mensagem) is financeira
