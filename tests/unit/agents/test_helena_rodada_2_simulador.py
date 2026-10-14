"""DL-0089 — rodada 2: a bateria do simulador de beneficiario (13/10/2026) contra o receptor real.

Vinte conversas de varios turnos, zero aprovadas. Cada bloco abaixo reproduz um trecho de transcricao
com o modelo simulado e fixa o comportamento decidido pelo dono:

  1. CRITICO: caso clinico aberto + mensagem nova de piora -> EMERGENCIA PRIMEIRO (192), nunca "outras
     duvidas"; sem repetir o mesmo texto.
  2. 192 so' em contexto de saude: encaminhamento administrativo e caso aberto administrativo sem SAMU.
  3. Perguntas sobre o caso aberto respondidas pelo que foi perguntado, sem texto identico em seguida.
  4. Mensagem mista cobranca + cobertura: cobranca ao Lucas, cobertura com onde ver.
  5. "quem eu sou pra voce? qual meu plano? qual meu nome?" -> verificado, sem escalar.
  6. Plano/cobertura/carencia com a fonte desligada -> onde ver (nunca "nao encontrei no cadastro").
  7. Duvida de saude sem sintoma -> nunca conduta clinica; e o fallback generico deixa de aparecer.
  8. "uma assistente virtual"; agradecimento; frase de passagem uma vez so'.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.helena.graph import (
    FRASE_PASSAGEM_COBRANCA,
    PROCESS_KEY,
    RESPONSE_KIND_HANDOFF,
    RESPOSTA_CANAIS_DO_PLANO,
    RESPOSTA_CASO_ABERTO_EMERGENCIA,
    RESPOSTA_CASO_ABERTO_EMERGENCIA_2,
    RESPOSTA_CASO_ABERTO_EMERGENCIA_3,
    RESPOSTA_CASO_ABERTO_PEDIDO_DE_PESSOA,
    RESPOSTA_CASO_ABERTO_PRAZO,
    RESPOSTA_CASO_ABERTO_SUPERVISOR,
    RESPOSTA_CASO_ABERTO_URGENTE,
    RESPOSTA_COBERTURA_NA_PASSAGEM,
    RESPOSTA_DESPEDIDA,
    RESPOSTA_DUVIDA_CLINICA,
    RESPOSTA_ESCALONAMENTO_URGENTE,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_PLANO_POR_CANAIS,
    START_DESFECHO_JA_ATIVO,
    HelenaGraph,
    new_helena_state,
)
from maezo.agents.helena.prompts import RESPOSTA_SOU_ASSISTENTE_VIRTUAL, response_prompt
from maezo.platform.webhooks.whatsapp.acesso_cadastro import (
    RESPOSTA_IDENTIDADE_E_PLANO,
    RESPOSTA_SEM_INFORMACAO,
    TIPO_IDENTIDADE_E_PLANO,
    TIPO_PLANO_DETALHE,
    resposta_de_cadastro,
    tipo_de_pergunta,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CONVERSA = "wa:amh:hk1_simulador2"
_CHAVE = f"ESC-amh-{_CONVERSA}"


class _Modelo:
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


def _caso_aberto(motivo: str) -> FakeCibSevenTransport:
    cib = FakeCibSevenTransport()
    cib.seed_instance(
        ProcessInstance(
            instance_id="caso-aberto-simulador",
            process_key=PROCESS_KEY,
            business_key=_CHAVE,
            state="ACTIVE",
            already_existed=True,
        ),
        variables={"motivo_categoria": motivo},
    )
    return cib


def _dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "x"}],
    )
    return dmn


def _grafo(
    respostas: list[str], *, cib: FakeCibSevenTransport | None = None
) -> tuple[Any, _WhatsApp, FakeCibSevenTransport]:
    whatsapp = _WhatsApp()
    cibseven = cib or FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=_Modelo(respostas),  # type: ignore[arg-type]
        dmn=_dmn(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        relogio=lambda: datetime(2026, 10, 13, 15, 0, tzinfo=UTC),
    )
    return grafo.compile_graph().compile(), whatsapp, cibseven


def _entrada(mensagem: str, falas_da_helena: list[str] | None = None, **kw: Any) -> dict[str, Any]:
    """A entrada do turno, com o historico curto (MAEZO_HELENA_HISTORICO=true) das falas anteriores."""
    estado: dict[str, Any] = dict(
        new_helena_state(
            tenant_id="amh",
            conversation_id=_CONVERSA,
            canal="whatsapp",
            beneficiario_pseudo_id="PSEUDO-SIM",
            message_body=mensagem,
            message_ref="hk1_msg",
            **kw,
        )
    )
    agora = datetime.now(UTC).isoformat()
    historico: list[dict[str, str]] = []
    for fala in falas_da_helena or []:
        historico.append({"papel": "beneficiario", "texto": "mensagem anterior", "em": agora})
        historico.append({"papel": "helena", "texto": fala, "em": agora})
    if historico:
        estado["historico_conversa"] = historico
        estado["ultima_mensagem_em"] = agora
    return estado


def _starts(cib: FakeCibSevenTransport) -> int:
    return sum(len(v) for v in cib._history.values())


# =================================================================================================
# 1. CRITICO: caso clinico aberto + piora
# =================================================================================================


async def test_primeira_mensagem_de_emergencia_escala_com_192() -> None:
    grafo, whatsapp, _ = _grafo([_classify(intent="symptom", sintoma_codigo="dor_toracica")])

    resultado = await grafo.ainvoke(
        _entrada("SOCORRO MEU PAI TA COM DOR FORTE NO PEITO E FALTA DE AR", sinal_saude_lexico=True)
    )

    assert resultado["escalation_motivo"] == "red_flag_clinico"
    # RODADA 10: o sintoma e' do pai — o aviso fala dele, nao de quem escreve.
    assert whatsapp.enviados == [
        RESPOSTA_ESCALONAMENTO_URGENTE.replace(
            "Se você estiver com sintomas agora", "Se seu pai estiver com sintomas agora"
        )
    ]
    assert "192" in whatsapp.enviados[0]


@pytest.mark.parametrize(
    ("mensagem", "falas", "esperado"),
    [
        (
            "JA TA LIGANDO PRO 192 MAS E O PLANO DE VC QUE TEM Q MANDAR AMBULANCIA",
            [RESPOSTA_ESCALONAMENTO_URGENTE],
            RESPOSTA_CASO_ABERTO_EMERGENCIA,
        ),
        (
            "ELE TA ROXO JA E NAO FALA MAIS NADA",
            [RESPOSTA_ESCALONAMENTO_URGENTE, RESPOSTA_CASO_ABERTO_EMERGENCIA],
            RESPOSTA_CASO_ABERTO_EMERGENCIA_2,
        ),
        (
            "ELE TA PARANDO DE RESPIRAR",
            [
                RESPOSTA_ESCALONAMENTO_URGENTE,
                RESPOSTA_CASO_ABERTO_EMERGENCIA,
                RESPOSTA_CASO_ABERTO_EMERGENCIA_2,
            ],
            RESPOSTA_CASO_ABERTO_EMERGENCIA_3,
        ),
    ],
)
async def test_piora_com_caso_clinico_aberto_recebe_emergencia_primeiro(
    mensagem: str, falas: list[str], esperado: str
) -> None:
    """A transcricao real: o modelo nao extraiu sintoma, e a Helena respondia "outras duvidas"."""
    cib = _caso_aberto("red_flag_clinico")
    antes = _starts(cib)
    grafo, whatsapp, _ = _grafo([_classify(intent="information")], cib=cib)

    resultado = await grafo.ainvoke(_entrada(mensagem, falas))

    assert resultado["start_desfecho"] == START_DESFECHO_JA_ATIVO
    assert whatsapp.enviados == [esperado]
    enviado = whatsapp.enviados[0]
    assert enviado.startswith("Ligue agora para o 192 (SAMU)")
    assert "ambulância" in enviado and "equipe de saúde" in enviado
    assert "outras dúvidas" not in enviado
    assert enviado not in falas, "nunca o mesmo texto de antes"
    assert _starts(cib) == antes, "o caso aberto e' reaproveitado, nenhum outro e' aberto"


async def test_piora_com_sintoma_extraido_e_caso_aberto_tambem_e_emergencia_primeiro() -> None:
    grafo, whatsapp, _ = _grafo(
        [_classify(intent="symptom", sintoma_codigo="dor_toracica")], cib=_caso_aberto("red_flag_clinico")
    )

    await grafo.ainvoke(
        _entrada("a dor no peito piorou", [RESPOSTA_ESCALONAMENTO_URGENTE], sinal_saude_lexico=True)
    )

    assert whatsapp.enviados[0].startswith("Ligue agora para o 192 (SAMU)")


# =================================================================================================
# 2. 192 so' em contexto de saude
# =================================================================================================


async def test_encaminhamento_administrativo_nao_fala_em_samu() -> None:
    grafo, whatsapp, _ = _grafo([_classify(intent="human_request")])

    # DL-0092: sem cobranca no texto (cobranca no texto vai ao Lucas).
    resultado = await grafo.ainvoke(_entrada("quero falar com um atendente, é sobre meu cadastro"))

    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert whatsapp.enviados == [RESPOSTA_HANDOFF_RECUSADA]
    assert "192" not in whatsapp.enviados[0] and "SAMU" not in whatsapp.enviados[0]


async def test_caso_aberto_administrativo_nao_fala_em_samu() -> None:
    grafo, whatsapp, _ = _grafo([_classify(intent="human_request")], cib=_caso_aberto("solicitacao_humano"))

    await grafo.ainvoke(_entrada("quero falar com alguem", [RESPOSTA_HANDOFF_RECUSADA]))

    # DL-0093 (rodada 6): pedido explicito com o caso aberto recebe o texto proprio (o atendente responde
    # aqui) — e continua sem 192 num caso administrativo.
    assert whatsapp.enviados == [RESPOSTA_CASO_ABERTO_PEDIDO_DE_PESSOA]
    assert "192" not in whatsapp.enviados[0]


# =================================================================================================
# 3. Perguntas sobre o caso aberto
# =================================================================================================


@pytest.mark.parametrize(
    ("mensagem", "esperado"),
    [
        ("quanto tempo demora?", RESPOSTA_CASO_ABERTO_PRAZO),
        ("quando o humano entra?", RESPOSTA_CASO_ABERTO_PRAZO),
        ("me passa pra um supervisor", RESPOSTA_CASO_ABERTO_SUPERVISOR),
        ("e se for urgente?", RESPOSTA_CASO_ABERTO_URGENTE),
    ],
)
async def test_pergunta_sobre_o_caso_aberto_e_respondida(mensagem: str, esperado: str) -> None:
    cib = _caso_aberto("solicitacao_humano")
    antes = _starts(cib)
    grafo, whatsapp, _ = _grafo([_classify(intent="information")], cib=cib)

    await grafo.ainvoke(_entrada(mensagem, [RESPOSTA_HANDOFF_RECUSADA]))

    assert whatsapp.enviados == [esperado]
    assert _starts(cib) == antes
    for prazo in ("minutos", "horas", "hoje", "amanhã"):
        assert prazo not in whatsapp.enviados[0], "nenhum prazo prometido"


async def test_a_mesma_pergunta_de_novo_nao_recebe_o_texto_identico() -> None:
    grafo, whatsapp, _ = _grafo([_classify(intent="information")], cib=_caso_aberto("solicitacao_humano"))

    await grafo.ainvoke(
        _entrada("e quanto tempo vai demorar?", [RESPOSTA_HANDOFF_RECUSADA, RESPOSTA_CASO_ABERTO_PRAZO])
    )

    assert whatsapp.enviados[0] != RESPOSTA_CASO_ABERTO_PRAZO
    assert RESPOSTA_CASO_ABERTO_PRAZO in whatsapp.enviados[0]


@pytest.mark.parametrize("mensagem", ["o link do portal?", "e se pedir a carteirinha?"])
async def test_pergunta_de_canal_com_caso_aberto_e_respondida_sem_link_inventado(mensagem: str) -> None:
    grafo, whatsapp, cib = _grafo([_classify(intent="information")])

    resultado = await grafo.ainvoke(_entrada(mensagem, [RESPOSTA_HANDOFF_RECUSADA]))

    assert resultado["response_kind"] == "inform"
    assert whatsapp.enviados == [RESPOSTA_CANAIS_DO_PLANO]
    assert "http" not in whatsapp.enviados[0]
    assert _starts(cib) == 0


# =================================================================================================
# 4. Mensagem mista: cobranca + cobertura
# =================================================================================================


async def test_mensagem_mista_vai_ao_lucas_e_diz_onde_ver_a_cobertura() -> None:
    grafo, whatsapp, _ = _grafo([_classify(intent="information")])

    resultado = await grafo.ainvoke(
        _entrada("quando vence o boleto desse mês? E também queria saber se o plano cobre dermatologista?")
    )

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert resultado["handoff"]["cobranca_subtipo"] == "vencimento"
    assert whatsapp.enviados == [f"{FRASE_PASSAGEM_COBRANCA} {RESPOSTA_COBERTURA_NA_PASSAGEM}"]
    assert "não consigo ver" not in whatsapp.enviados[0]


def test_o_prompt_proibe_negar_dado_de_cobranca() -> None:
    assert "NUNCA diga que nao consegue ver vencimento, boleto, valor ou pagamento" in response_prompt()


# =================================================================================================
# 5 e 6. Identidade e plano (conversa verificada; fonte do plano desligada)
# =================================================================================================


def test_quem_eu_sou_qual_meu_plano_qual_meu_nome() -> None:
    mensagem = "quem eu sou pra você? qual meu plano? qual meu nome?"
    assert tipo_de_pergunta(mensagem) == TIPO_IDENTIDADE_E_PLANO
    texto = resposta_de_cadastro(TIPO_IDENTIDADE_E_PLANO, None)
    assert texto == RESPOSTA_IDENTIDADE_E_PLANO
    assert "identificado" in texto and "não consigo" not in texto.lower()


@pytest.mark.parametrize(
    ("tipo", "identidade", "fonte_ligada"),
    [
        (TIPO_PLANO_DETALHE, None, True),
        ("carencia", {"carencia_vigente": None}, True),
        ("plano_ativo", None, False),
    ],
)
def test_plano_sem_fonte_diz_onde_ver(
    tipo: str, identidade: dict[str, Any] | None, fonte_ligada: bool
) -> None:
    texto = resposta_de_cadastro(tipo, identidade, fonte_ligada=fonte_ligada)
    assert texto == RESPOSTA_PLANO_POR_CANAIS == RESPOSTA_SEM_INFORMACAO
    assert "Não encontrei" not in texto


async def test_cobertura_classificada_como_pergunta_clinica_nao_escala() -> None:
    grafo, whatsapp, cib = _grafo([_classify(intent="clinical_question")])

    resultado = await grafo.ainvoke(_entrada("o plano cobre dermatologista?"))

    assert resultado["response_kind"] == "inform"
    assert whatsapp.enviados == [RESPOSTA_PLANO_POR_CANAIS]
    assert _starts(cib) == 0


# =================================================================================================
# 7. Duvida de saude sem sintoma: nunca conduta
# =================================================================================================


async def test_conduta_clinica_do_modelo_e_trocada_pelo_texto_fixo() -> None:
    grafo, whatsapp, _ = _grafo(
        [_classify(intent="information"), "Recomendo check-up e colesterol a cada 1 ou 2 anos."]
    )

    await grafo.ainvoke(_entrada("com que frequência eu devo fazer check-up?"))

    assert whatsapp.enviados == [RESPOSTA_DUVIDA_CLINICA]
    assert "a cada" not in whatsapp.enviados[0]


async def test_rascunho_barrado_numa_duvida_de_saude_nao_vira_o_fallback_generico() -> None:
    grafo, whatsapp, _ = _grafo(
        [_classify(intent="information"), "Claro, eu encaminho sua solicitação para a equipe."]
    )

    await grafo.ainvoke(_entrada("qual exame preventivo eu preciso fazer?"))

    assert whatsapp.enviados == [RESPOSTA_DUVIDA_CLINICA]


def test_o_prompt_proibe_conduta_clinica() -> None:
    assert "NUNCA RECOMENDE CONDUTA CLINICA" in response_prompt()


# =================================================================================================
# 8. Detalhes
# =================================================================================================


def test_uma_assistente_virtual() -> None:
    assert RESPOSTA_SOU_ASSISTENTE_VIRTUAL.startswith("Sou uma assistente virtual")
    assert "UMA assistente virtual" in response_prompt()


async def test_agradecimento_com_o_nome_recebe_despedida() -> None:
    grafo, whatsapp, _ = _grafo([_classify(intent="greeting")])

    await grafo.ainvoke(_entrada("valeu pela ajuda, Helena!"))

    assert whatsapp.enviados == [RESPOSTA_DESPEDIDA]


async def test_frase_de_passagem_nao_se_repete_quando_a_conversa_volta() -> None:
    """Lucas escalou/soltou a conversa e a pergunta seguinte e' de cobranca de novo: sem a frase."""
    grafo, whatsapp, _ = _grafo([_classify(intent="cobranca", cobranca_subtipo="consulta_valores")])

    resultado = await grafo.ainvoke(_entrada("e o valor de setembro?", [FRASE_PASSAGEM_COBRANCA]))

    assert resultado["response_kind"] == RESPONSE_KIND_HANDOFF
    assert whatsapp.enviados == []
