"""DL-0078 (decisao do dono de 07/10/2026): o AVISO DE IDENTIDADE da Helena, no grafo real.

Com o desfecho da resolucao pela AMH no estado (`identidade_desfecho`), a Helena:

  * no PRIMEIRO contato da conversa manda, como mensagem SEPARADA e ANTES da resposta do turno, o texto
    fixo de "reconheci este numero" (ou "nao encontrei este numero") — uma vez por conversa;
  * a "sabe quem sou eu?" (so' a pergunta) responde o texto fixo de identidade;
  * com desfecho indeterminado (ou flag desligada) nao diz NADA sobre identidade: a Helena de sempre;
  * nunca diz dado de plano, e nunca troca, atrasa ou suprime triagem, red flag ou escalonamento.

Os testes rodam o turno completo (classificador roteirizado, DMN fake, WhatsApp fake que grava o que
SAIU) e comparam o texto enviado com as constantes — e as constantes com o texto LITERAL do dono.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena.graph import (
    IDENTIDADE_CHAVES,
    IDENTIDADE_INDETERMINADA,
    IDENTIDADE_NAO_ENCONTRADA,
    IDENTIDADE_RECONHECIDA,
    NOME_OPERADORA_PADRAO,
    RESPOSTA_AVISO_IDENTIDADE_RECONHECIDA_MODELO,
    RESPOSTA_FORA_DO_CANAL,
    RESPOSTA_IDENTIDADE_NAO_ENCONTRADA,
    RESPOSTA_PERGUNTA_IDENTIDADE_RECONHECIDA_MODELO,
    RESPOSTA_SAUDACAO_ABERTURA,
    HelenaGraph,
    HelenaState,
    _apresentou_se,
    build,
    gate_inbound_state,
    new_helena_state,
    texto_aviso_de_identidade,
    texto_pergunta_de_identidade,
)
from maezo.agents.helena.prompts import (
    RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

#: OS TEXTOS DO DONO, LITERAIS (07/10/2026). Nao derivar das constantes: e' o que se quer provar.
AVISO_RECONHECIDO = (
    "Reconheci este número no cadastro de beneficiários da Austa Clínicas. "
    "Por segurança, não mostro dados pessoais por aqui."
)
PERGUNTA_RECONHECIDO = (
    "Este número está cadastrado para um beneficiário da Austa Clínicas. "
    "Por segurança, não mostro nome nem dados pelo WhatsApp."
)
NAO_ENCONTRADO = "Não encontrei este número no cadastro de beneficiários."

#: Uma identidade com valores DISTINTIVOS, para provar que nenhum deles chega ao texto enviado.
IDENTIDADE = {
    "portable_subject_ref": "subj-ref-AVISO-0001",
    "faixa_etaria": "idoso",
    "plano_ativo": True,
    "vigencia_inicio": "2019-03-17",
    "vigencia_fim": "2031-12-31",
    "carencia_vigente": True,
    "titular_ref": "subj-ref-TITULAR-0002",
}


class _Inferencia:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.model_id = "fake"

    def armar(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._respostas.pop(0) if self._respostas else ""


class _WhatsApp:
    def __init__(self, *, falhar_no: int | None = None) -> None:
        self.enviados: list[str] = []
        self._falhar_no = falhar_no
        self._n = 0

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self._n += 1
        if self._falhar_no == self._n:
            raise RuntimeError("provedor fora")
        self.enviados.append(text)
        return {"ok": True}


def _classify(**campos: Any) -> str:
    base: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(campos)
    return json.dumps(base)


def _estado(mensagem: str, desfecho: str | None, **extra: Any) -> HelenaState:
    estado: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_aviso",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-AVISO",
        "message_body": mensagem,
        "identidade_desfecho": desfecho,
        "identidade_beneficiario": dict(IDENTIDADE) if desfecho == IDENTIDADE_RECONHECIDA else None,
    }
    estado.update(extra)
    return cast(HelenaState, estado)


def _grafo(
    respostas: list[str],
    *,
    whatsapp: _WhatsApp | None = None,
    dmn: FakeDmnTransport | None = None,
    cibseven: FakeCibSevenTransport | None = None,
    **kwargs: Any,
) -> tuple[HelenaGraph, _Inferencia, _WhatsApp, FakeCibSevenTransport]:
    inferencia = _Inferencia(respostas)
    zap = whatsapp or _WhatsApp()
    motor = cibseven or FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=cast(InferenceProvider, inferencia),
        dmn=dmn or FakeDmnTransport(),
        cibseven=motor,
        audit_sink=FakeStartAuditSink(),
        whatsapp=zap,
        **kwargs,
    )
    return grafo, inferencia, zap, motor


async def _turno(
    mensagem: str, desfecho: str | None, respostas: list[str], **extra: Any
) -> tuple[dict[str, Any], _WhatsApp, _Inferencia]:
    grafo, inferencia, zap, _ = _grafo(respostas)
    resultado = await grafo.compile_graph().compile().ainvoke(_estado(mensagem, desfecho, **extra))
    return dict(resultado), zap, inferencia


def _red_flag_dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "dor toracica"}],
    )
    return dmn


_RED_FLAG_LLM = [
    _classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"),
    "resumo",
    "um profissional humano vai continuar",
]


# --- os textos -----------------------------------------------------------------------------------


def test_os_textos_padrao_sao_exatamente_os_do_dono() -> None:
    assert NOME_OPERADORA_PADRAO == "Austa Clínicas"
    assert texto_aviso_de_identidade(IDENTIDADE_RECONHECIDA) == AVISO_RECONHECIDO
    assert texto_pergunta_de_identidade(IDENTIDADE_RECONHECIDA) == PERGUNTA_RECONHECIDO
    assert texto_aviso_de_identidade(IDENTIDADE_NAO_ENCONTRADA) == NAO_ENCONTRADO
    assert texto_pergunta_de_identidade(IDENTIDADE_NAO_ENCONTRADA) == NAO_ENCONTRADO
    assert RESPOSTA_IDENTIDADE_NAO_ENCONTRADA == NAO_ENCONTRADO


@pytest.mark.parametrize("desfecho", [IDENTIDADE_INDETERMINADA, None, "outro", True])
def test_desfecho_indeterminado_ou_desligado_nao_tem_texto(desfecho: object) -> None:
    assert texto_aviso_de_identidade(desfecho) is None
    assert texto_pergunta_de_identidade(desfecho) is None


def test_o_nome_da_operadora_e_parametro_e_os_modelos_o_usam() -> None:
    assert "{operadora}" in RESPOSTA_AVISO_IDENTIDADE_RECONHECIDA_MODELO
    assert "{operadora}" in RESPOSTA_PERGUNTA_IDENTIDADE_RECONHECIDA_MODELO
    aviso = texto_aviso_de_identidade(IDENTIDADE_RECONHECIDA, nome_operadora="Operadora Exemplo")
    assert aviso is not None and "da Operadora Exemplo." in aviso


@pytest.mark.parametrize("texto", [AVISO_RECONHECIDO, PERGUNTA_RECONHECIDO, NAO_ENCONTRADO])
def test_os_textos_passam_nas_cercas_de_saida_e_nao_prometem_nada(texto: str) -> None:
    assert motivo_de_canal_nao_confirmado(texto) is None
    assert motivo_de_recusa(texto, "inform", start_aconteceu=False) is None
    assert motivo_de_recusa(texto, "inform") is None
    assert menciona_encaminhamento(texto) is False
    # nao e' o cartao de apresentacao: nao pode acender `apresentacao_ja_feita`
    assert _apresentou_se(texto) is False


@pytest.mark.parametrize("texto", [AVISO_RECONHECIDO, PERGUNTA_RECONHECIDO, NAO_ENCONTRADO])
def test_os_textos_nao_carregam_dado_de_plano(texto: str) -> None:
    baixo = texto.lower()
    for proibido in ("vigên", "vigen", "carên", "caren", "titular", "ativo", "plano", "idade", "faixa"):
        assert proibido not in baixo


# --- primeiro contato ------------------------------------------------------------------------------


async def test_primeiro_contato_reconhecido_avisa_antes_da_resposta() -> None:
    resultado, zap, _ = await _turno("oi", IDENTIDADE_RECONHECIDA, [_classify(intent="greeting")])

    assert zap.enviados == [AVISO_RECONHECIDO, RESPOSTA_SAUDACAO_ABERTURA]
    assert resultado["response_text"] == RESPOSTA_SAUDACAO_ABERTURA
    assert resultado["aviso_identidade_dado"] is True


async def test_primeiro_contato_nao_encontrado_avisa_antes_da_resposta() -> None:
    resultado, zap, _ = await _turno("oi", IDENTIDADE_NAO_ENCONTRADA, [_classify(intent="greeting")])

    assert zap.enviados == [NAO_ENCONTRADO, RESPOSTA_SAUDACAO_ABERTURA]
    assert resultado["aviso_identidade_dado"] is True


@pytest.mark.parametrize("desfecho", [IDENTIDADE_INDETERMINADA, None])
async def test_primeiro_contato_indeterminado_ou_desligado_e_silencioso(desfecho: str | None) -> None:
    resultado, zap, _ = await _turno("oi", desfecho, [_classify(intent="greeting")])

    assert zap.enviados == [RESPOSTA_SAUDACAO_ABERTURA]
    assert resultado["aviso_identidade_dado"] is False


async def test_aviso_ja_dado_nao_se_repete() -> None:
    resultado, zap, _ = await _turno(
        "oi", IDENTIDADE_RECONHECIDA, [_classify(intent="greeting")], aviso_identidade_dado=True
    )

    assert zap.enviados == [RESPOSTA_SAUDACAO_ABERTURA]
    assert resultado["aviso_identidade_dado"] is True


async def test_aviso_uma_vez_por_conversa_com_checkpoint() -> None:
    grafo, inferencia, zap, _ = _grafo([_classify(intent="greeting")])
    compilado = grafo.compile_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "wa:amh:hk1_aviso"}}

    await compilado.ainvoke(_estado("oi", IDENTIDADE_RECONHECIDA), config)
    for mensagem in ("bom dia", "oi de novo"):
        inferencia.armar([_classify(intent="greeting")])
        await compilado.ainvoke(_estado(mensagem, IDENTIDADE_RECONHECIDA), config)

    assert zap.enviados.count(AVISO_RECONHECIDO) == 1
    assert zap.enviados[0] == AVISO_RECONHECIDO
    # outra conversa recebe o seu proprio aviso
    inferencia.armar([_classify(intent="greeting")])
    await compilado.ainvoke(_estado("oi", IDENTIDADE_RECONHECIDA), {"configurable": {"thread_id": "outra"}})
    assert zap.enviados.count(AVISO_RECONHECIDO) == 2


async def test_falha_de_envio_do_aviso_nao_custa_a_resposta_e_o_aviso_volta() -> None:
    grafo, _, zap, _ = _grafo([_classify(intent="greeting")], whatsapp=_WhatsApp(falhar_no=1))
    resultado = await grafo.compile_graph().compile().ainvoke(_estado("oi", IDENTIDADE_RECONHECIDA))

    assert zap.enviados == [RESPOSTA_SAUDACAO_ABERTURA]
    assert resultado["aviso_identidade_dado"] is False
    assert not resultado.get("error")


async def test_aviso_barrado_pela_cerca_nao_sai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "motivo_de_canal_nao_confirmado", lambda texto: ("canal", "x"))
    grafo, _, zap, _ = _grafo([_classify(intent="greeting")])
    # `respond` tambem passa o texto principal pela cerca TEXTO x FATO, que nao usa a cerca de canal.
    resultado = await grafo.compile_graph().compile().ainvoke(_estado("oi", IDENTIDADE_RECONHECIDA))

    assert AVISO_RECONHECIDO not in zap.enviados
    assert resultado["aviso_identidade_dado"] is False


async def test_nome_da_operadora_configurado_chega_ao_texto_pelo_build() -> None:
    zap = _WhatsApp()
    compilado = build(
        {
            "inference": _Inferencia([_classify(intent="greeting")]),
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "whatsapp": zap,
            "audit_sink": FakeStartAuditSink(),
            "identidade_nome_operadora": "Operadora Exemplo",
        }
    ).compile()
    await compilado.ainvoke(_estado("oi", IDENTIDADE_RECONHECIDA))

    assert zap.enviados[0] == AVISO_RECONHECIDO.replace("Austa Clínicas", "Operadora Exemplo")


# --- saude primeiro --------------------------------------------------------------------------------


@pytest.mark.parametrize("desfecho", [IDENTIDADE_RECONHECIDA, IDENTIDADE_NAO_ENCONTRADA])
async def test_red_flag_no_primeiro_contato_sai_sozinha_e_o_aviso_e_adiado(desfecho: str) -> None:
    grafo, inferencia, zap, _ = _grafo(list(_RED_FLAG_LLM), dmn=_red_flag_dmn())
    compilado = grafo.compile_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "wa:amh:hk1_aviso"}}

    r1 = await compilado.ainvoke(_estado("estou com dor no peito", desfecho), config)

    assert r1["escalation_started"] is True
    assert r1["escalation_motivo"] == "red_flag_clinico"
    assert len(zap.enviados) == 1  # so' a resposta de seguranca, sem aviso na frente
    assert all(texto not in zap.enviados[0] for texto in (AVISO_RECONHECIDO, NAO_ENCONTRADO))
    assert r1["aviso_identidade_dado"] is False

    # o proximo turno comum entrega o aviso adiado
    inferencia.armar([_classify(intent="greeting")])
    await compilado.ainvoke(_estado("oi", desfecho), config)
    aviso = AVISO_RECONHECIDO if desfecho == IDENTIDADE_RECONHECIDA else NAO_ENCONTRADO
    assert zap.enviados[1] == aviso


async def test_decisao_clinica_e_identica_com_e_sem_identidade() -> None:
    """Contexto, nunca decisao: a mesma mensagem com red flag decide igual em qualquer desfecho."""
    saidas = {}
    for desfecho in (None, IDENTIDADE_INDETERMINADA, IDENTIDADE_RECONHECIDA, IDENTIDADE_NAO_ENCONTRADA):
        grafo, _, zap, _ = _grafo(list(_RED_FLAG_LLM), dmn=_red_flag_dmn())
        r = await grafo.compile_graph().compile().ainvoke(_estado("estou com dor no peito", desfecho))
        saidas[desfecho] = (
            r["next_kind"],
            r["escalation_motivo"],
            r["escalation_severidade"],
            r["escalation_started"],
            tuple(zap.enviados),
        )
    assert len(set(saidas.values())) == 1


@pytest.mark.parametrize("desfecho", [IDENTIDADE_RECONHECIDA, IDENTIDADE_NAO_ENCONTRADA])
async def test_quem_sou_eu_com_sintoma_vai_para_a_triagem(desfecho: str) -> None:
    grafo, _, zap, _ = _grafo(list(_RED_FLAG_LLM), dmn=_red_flag_dmn())
    r = (
        await grafo.compile_graph()
        .compile()
        .ainvoke(_estado("sabe quem sou eu? estou com dor no peito", desfecho))
    )

    assert r["escalation_started"] is True
    assert r["escalation_motivo"] == "red_flag_clinico"
    assert len(zap.enviados) == 1
    for texto in (PERGUNTA_RECONHECIDO, NAO_ENCONTRADO, AVISO_RECONHECIDO):
        assert texto not in zap.enviados[0]


async def test_quem_sou_eu_com_sinal_lexico_de_saude_nao_usa_o_texto_de_identidade() -> None:
    resultado, zap, _ = await _turno(
        "sabe quem sou eu?",
        IDENTIDADE_RECONHECIDA,
        [_classify(intent="outside_channel")],
        sinal_saude_lexico=True,
    )
    assert PERGUNTA_RECONHECIDO not in resultado["response_text"]


async def test_quem_sou_eu_com_pedido_de_humano_escala() -> None:
    resultado, zap, _ = await _turno(
        "sabe quem sou eu? quero falar com uma pessoa",
        IDENTIDADE_RECONHECIDA,
        [_classify(intent="human_request"), "resumo", "um profissional vai continuar"],
    )
    assert resultado["escalation_started"] is True
    assert all(PERGUNTA_RECONHECIDO not in texto for texto in zap.enviados)
    assert AVISO_RECONHECIDO not in zap.enviados  # escalonamento: o aviso e' adiado


async def test_quem_sou_eu_com_cobranca_segue_o_fluxo_normal() -> None:
    resultado, _, _ = await _turno(
        "sabe quem sou eu? quero a segunda via do boleto",
        IDENTIDADE_RECONHECIDA,
        [_classify(intent="outside_channel")],
    )
    assert PERGUNTA_RECONHECIDO not in resultado["response_text"]


@pytest.mark.parametrize("mensagem", ["não sei mais quem sou eu", "nao sei quem eu sou"])
async def test_nao_sei_quem_sou_nao_e_pergunta_de_identidade(mensagem: str) -> None:
    resultado, _, _ = await _turno(mensagem, IDENTIDADE_RECONHECIDA, [_classify(intent="outside_channel")])
    assert PERGUNTA_RECONHECIDO not in resultado["response_text"]


# --- "sabe quem sou eu?" ---------------------------------------------------------------------------

_INTENCOES = ["outside_channel", "information", "greeting", "human_request"]
_PERGUNTAS = [
    "sabe quem sou eu?",
    "Você sabe quem eu sou?",
    "voce sabe quem eu sou",
    "QUEM SOU EU?!",
    "quem sou eu",
    "me reconhece?",
    "Me reconhece???",
    "sabe com quem está falando?",
    "Sabe com quem você está falando?",
    "vc me conhece?",
    "sabe meu nome?",
]


@pytest.mark.parametrize("intencao", _INTENCOES)
@pytest.mark.parametrize("mensagem", _PERGUNTAS)
async def test_quem_sou_eu_reconhecido_recebe_o_texto_do_dono(mensagem: str, intencao: str) -> None:
    resultado, zap, _ = await _turno(mensagem, IDENTIDADE_RECONHECIDA, [_classify(intent=intencao)])

    # uma mensagem so': a resposta JA' e' o texto de identidade, entao o aviso nao sai separado
    assert zap.enviados == [PERGUNTA_RECONHECIDO]
    assert resultado["aviso_identidade_dado"] is True
    assert not resultado.get("escalation_started")


@pytest.mark.parametrize("intencao", _INTENCOES)
@pytest.mark.parametrize("mensagem", _PERGUNTAS)
async def test_quem_sou_eu_nao_encontrado_recebe_o_texto_do_dono(mensagem: str, intencao: str) -> None:
    resultado, zap, _ = await _turno(mensagem, IDENTIDADE_NAO_ENCONTRADA, [_classify(intent=intencao)])

    assert zap.enviados == [NAO_ENCONTRADO]
    assert not resultado.get("escalation_started")


async def test_quem_sou_eu_repetido_depois_do_aviso_responde_de_novo() -> None:
    resultado, zap, _ = await _turno(
        "sabe quem sou eu?",
        IDENTIDADE_RECONHECIDA,
        [_classify(intent="outside_channel")],
        aviso_identidade_dado=True,
    )
    assert zap.enviados == [PERGUNTA_RECONHECIDO]


async def test_pergunta_sobre_a_helena_e_sobre_a_pessoa_recebe_as_duas_frases() -> None:
    resultado, _, _ = await _turno(
        "você é um robô? sabe quem sou eu?", IDENTIDADE_RECONHECIDA, [_classify(intent="outside_channel")]
    )
    assert resultado["response_text"] == f"{RESPOSTA_SOU_ASSISTENTE_VIRTUAL} {PERGUNTA_RECONHECIDO}"


@pytest.mark.parametrize("intencao", _INTENCOES)
@pytest.mark.parametrize("mensagem", _PERGUNTAS + ["oi", "o plano cobre fisioterapia?"])
async def test_indeterminado_e_exatamente_a_helena_de_sempre(mensagem: str, intencao: str) -> None:
    """Indeterminado (telefone compartilhado, falha, sem base legal...) == flag desligada, texto a texto."""
    saidas = []
    for desfecho in (None, IDENTIDADE_INDETERMINADA):
        respostas = [_classify(intent=intencao), "resumo", "rascunho"]
        resultado, zap, inferencia = await _turno(mensagem, desfecho, respostas)
        saidas.append(
            (
                tuple(zap.enviados),
                resultado.get("next_kind"),
                resultado.get("escalation_started"),
                tuple(inferencia.prompts),
            )
        )
    assert saidas[0] == saidas[1]
    enviados = saidas[0][0]
    for texto in (AVISO_RECONHECIDO, PERGUNTA_RECONHECIDO, NAO_ENCONTRADO):
        assert all(texto not in e for e in enviados)


async def test_indeterminado_mantem_as_frases_f6_de_antes() -> None:
    resultado, _, _ = await _turno(
        "Você sabe quem eu sou?", IDENTIDADE_INDETERMINADA, [_classify(intent="outside_channel")]
    )
    assert resultado["response_text"] == RESPOSTA_NAO_CONSIGO_IDENTIFICAR
    resultado, _, _ = await _turno("sabe quem sou eu?", None, [_classify(intent="outside_channel")])
    assert resultado["response_text"] == RESPOSTA_FORA_DO_CANAL


# --- nenhum dado do plano, nunca -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mensagem", "classificacao"),
    [
        ("oi", _classify(intent="greeting")),
        ("sabe quem sou eu?", _classify(intent="outside_channel")),
        ("meu plano esta ativo? qual a vigencia?", _classify(intent="information")),
        ("estou em carencia? quem e o titular?", _classify(intent="information")),
    ],
)
async def test_nenhum_dado_do_plano_sai_nem_entra_no_prompt(mensagem: str, classificacao: str) -> None:
    resultado, zap, inferencia = await _turno(
        mensagem, IDENTIDADE_RECONHECIDA, [classificacao, "Posso ajudar com informacoes gerais."]
    )
    valores = [str(v) for v in IDENTIDADE.values() if isinstance(v, str)]
    for texto in zap.enviados:
        for valor in valores:
            assert valor not in texto
    for prompt in inferencia.prompts:
        for valor in valores:
            assert valor not in prompt
    assert frozenset(IDENTIDADE) == IDENTIDADE_CHAVES


# --- a fronteira do estado -------------------------------------------------------------------------

_BASE: dict[str, Any] = {
    "tenant_id": "amh",
    "conversation_id": "wa:amh:hk1_x",
    "canal": "whatsapp",
    "beneficiario_pseudo_id": "p",
    "message_body": "oi",
}


def test_new_helena_state_grava_o_desfecho_sempre_e_recusa_incoerencia() -> None:
    assert new_helena_state(**_BASE)["identidade_desfecho"] is None
    com = new_helena_state(
        **_BASE, identidade_beneficiario=IDENTIDADE, identidade_desfecho=IDENTIDADE_RECONHECIDA
    )
    assert com["identidade_desfecho"] == IDENTIDADE_RECONHECIDA
    assert new_helena_state(**_BASE, identidade_desfecho=IDENTIDADE_NAO_ENCONTRADA)[
        "identidade_desfecho"
    ] == (IDENTIDADE_NAO_ENCONTRADA)
    with pytest.raises(ValueError, match="identidade_desfecho fora do dominio"):
        new_helena_state(**_BASE, identidade_desfecho="talvez")
    with pytest.raises(ValueError, match="incoerente"):
        new_helena_state(**_BASE, identidade_desfecho=IDENTIDADE_RECONHECIDA)
    with pytest.raises(ValueError, match="incoerente"):
        new_helena_state(
            **_BASE, identidade_beneficiario=IDENTIDADE, identidade_desfecho=IDENTIDADE_NAO_ENCONTRADA
        )


def test_gate_inbound_state_zera_desfecho_plantado_e_descarta_o_marcador() -> None:
    gated = gate_inbound_state(
        {**_BASE, "identidade_desfecho": IDENTIDADE_RECONHECIDA, "aviso_identidade_dado": True}
    )
    assert gated["identidade_desfecho"] is None
    assert "aviso_identidade_dado" not in gated


@pytest.mark.parametrize(("plantado", "esperado"), [(True, True), ("true", False), (1, False), (None, False)])
async def test_receive_so_preserva_o_marcador_true_literal(plantado: object, esperado: bool) -> None:
    grafo, _, _, _ = _grafo([])
    reset = await grafo.receive(_estado("oi", None, aviso_identidade_dado=plantado))
    assert reset["aviso_identidade_dado"] is esperado
