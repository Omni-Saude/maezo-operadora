"""A cerca TEXTO x FATO: o que a Helena DIZ tem de bater com o que ela FEZ.

O QUE FOI MEDIDO, bateria do diretor de 21/09/2026 contra a imagem `8b014a60` em dev. Dois casos,
opostos na aparencia e identicos na causa:

  C1  "estou com dor de cabeca"
      -> "Um profissional de saude entrara em contato com voce em breve (...) Aguarde nosso
          contato."
      evidencia do mesmo turno: processo aberto = NENHUM · roteamento = NENHUM · erro = NENHUM.

  E4  "quanto eu devo de mensalidade?"
      -> "Este canal pode te orientar sobre como consultar o valor da mensalidade. Voce pode
          verificar pelo aplicativo do plano, no portal da operadora ou entrando em contato com a
          central de atendimento."
      evidencia do mesmo turno: processo ABERTO, `solicitacao_humano`, fila `atendimento-humano`,
      P3 / ack 4h / resolucao 24h.

No C1 uma pessoa ficou esperando um telefonema que ninguem ia dar. No E4 alguem do atendimento
recebeu um caso enquanto o beneficiario ia procurar sozinho no aplicativo. Os dois sao a MESMA
ausencia: nada no repositorio verificava que o texto correspondia ao fato.

A CAUSA DO C1 — e a cadeia do relatorio NAO era ela. O relatorio supos "intensidade desconhecida
-> severidade nula -> o worker recusou fechado"; o codigo desmente cada elo: a red flag P1 produz
`severidade="grave"` por `_severidade_from_prioridade`, e uma recusa de contrato do worker roda
DENTRO do motor, depois de a instancia existir. O que de fato acontece esta' provado abaixo: a
business key e' `ESC-{tenant}-{conversation_id}`, o canal de teste reusa uma faixa de 99 telefones
e havia 27 escalonamentos ABERTOS de baterias anteriores — `start_process_idempotent` encontrou a
instancia VIVA, devolveu-a com `start_outcome=ALREADY_ACTIVE`, sem abrir outra e SEM ERRO. Nada
falhou, entao `start_failed` nunca ligou; e o grafo ignorava `start_outcome`.

O QUE ESTE ARQUIVO PROVA, em ordem de importancia:
  1. promessa de humano NAO sai quando o start nao aconteceu — mesmo com `response_kind="escalate"`
     (o buraco que `test_helena_recusa_de_saida.py` nao cobria);
  2. quando o start aconteceu, o texto e' OBRIGADO a mencionar o encaminhamento;
  3. `already_existed` diz a verdade e deixa RASTRO (desfecho, log e contador proprios);
  4. as excecoes de start que NAO sao `CibSevenError` propagam, em vez de virar "falha tecnica".
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena import graph as helena_graph
from maezo.agents.helena.graph import (
    DESFECHO_ESCALONAMENTO_JA_ABERTO,
    ERRO_HANDOFF_SEM_MENCAO,
    ERRO_PROMESSA_SEM_START,
    PROCESS_KEY,
    RESPOSTA_FALHA_DE_REDACAO,
    RESPOSTA_FALHA_TECNICA_START,
    RESPOSTA_HANDOFF_JA_ABERTO,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_SEM_ENCAMINHAMENTO,
    START_DESFECHO_FALHOU,
    START_DESFECHO_JA_ATIVO,
    START_DESFECHO_JA_CONCLUIDO,
    START_DESFECHO_NAO_REPORTADO,
    START_DESFECHO_NAO_TENTADO,
    START_DESFECHO_NOVO,
    HelenaGraph,
    HelenaState,
    _humano_acionado,
    _start_desfecho_de,
)
from maezo.agents.helena.prompts import (
    RECUSA_DE_SAIDA_VERSION,
    RECUSA_ESCALONAMENTO_JA_ABERTO,
    RECUSA_HANDOFF_SEM_MENCAO,
    RECUSA_PROMESSA_DE_HUMANO,
    RECUSA_PROMESSA_SEM_START,
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)
from maezo.gateway.engine_contracts import EngineRefusalCode
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.turn_telemetry import _DESFECHO_VOCAB
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    CibSevenStartAuthorizationError,
    FakeCibSevenTransport,
    ProcessInstance,
    StartOutcome,
    StartVariableRedactionError,
)
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

# --- Os textos REAIS da bateria de 21/09/2026. Nao sao exemplos inventados. --------------------

#: O texto do C1, verbatim do relatorio. Promete humano; passou pela cerca porque a rota era
#: `escalate`; nenhum processo novo existiu.
C1_PROMESSA = (
    "Antes de prosseguirmos, confirme por favor: voce descreveu essa dor de cabeca como 'a pior "
    "da vida', de inicio subito, correto? Recebemos sua mensagem e, pela descricao, identificamos "
    "um sinal de alerta importante. Um profissional de saude entrara em contato com voce em breve "
    "para avaliacao detalhada. Aguarde nosso contato."
)
#: O texto do E4, verbatim. Informativo, correto em conteudo, e nao diz que um humano assumiu.
E4_SEM_MENCAO = (
    "Este canal pode te orientar sobre como consultar o valor da mensalidade. Voce pode verificar "
    "pelo aplicativo do plano, no portal da operadora ou entrando em contato com a central de "
    "atendimento."
)


class _InferenciaFixa:
    """Devolve sempre o mesmo texto. O que se testa aqui e' a CERCA, nunca o modelo."""

    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.chamadas = 0
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.chamadas += 1
        return self.texto


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append((to_hash, text))
        return {"ok": True}


def _graph(
    texto: str = "irrelevante",
    *,
    cibseven: FakeCibSevenTransport | None = None,
    whatsapp: _WhatsApp | None = None,
) -> HelenaGraph:
    return HelenaGraph(
        # `cast` e nao `type: ignore`: o duplo satisfaz o Protocol ESTRUTURALMENTE (o seam e'
        # duck-typed), e e' o mesmo idioma que `HelenaGraph.build()` usa em producao. A supressao
        # esconderia tambem uma deriva de assinatura de verdade, que e' o que CC-12 ja custou.
        inference=cast(InferenceProvider, _InferenciaFixa(texto)),
        dmn=FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp or _WhatsApp(),
    )


BUSINESS_KEY = "ESC-amh-wa:amh:c1_deadbeef"


def _estado(**extra: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:c1_deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-C1",
        "message_body": "estou com dor de cabeca",
    }
    s.update(extra)  # type: ignore[typeddict-item]
    return s


def _com_instancia_viva() -> FakeCibSevenTransport:
    """O canal de teste da bateria: business key colidida com um dos 27 escalonamentos abertos."""
    cibseven = FakeCibSevenTransport()
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="instancia-de-uma-bateria-anterior",
            process_key=PROCESS_KEY,
            business_key=BUSINESS_KEY,
            state="ACTIVE",
            already_existed=True,
        )
    )
    return cibseven


# =================================================================================================
# 1. A funcao pura: a cerca passa a olhar o FATO, nao a intencao
# =================================================================================================


def test_o_texto_do_c1_passava_pela_cerca_so_por_causa_da_rota() -> None:
    """O RED deste arquivo, escrito como o codigo era em 20/09: pela ROTA, o C1 passa.

    Este teste nao e' um alvo a corrigir — e' o registro executavel de por que a rota nao bastava.
    `start_aconteceu=None` e' o valor do RASCUNHO (`_respond_llm` redige antes de tentar o start),
    e ali a decisao pela rota continua sendo a certa.
    """
    assert motivo_de_recusa(C1_PROMESSA, "escalate") is None
    assert motivo_de_recusa(C1_PROMESSA, "escalate", start_aconteceu=None) is None


def test_a_promessa_e_recusada_quando_o_start_nao_aconteceu_mesmo_na_rota_escalate() -> None:
    """F1, o nucleo: a cerca autorizava pela rota PRETENDIDA; agora exige o FATO.

    `tests/unit/agents/test_helena_recusa_de_saida.py::
    test_a_promessa_de_humano_e_permitida_na_rota_que_de_fato_escala` fixa o caso em que a rota
    escalou de verdade — e era exatamente esse "de fato" que nada verificava.
    """
    for rota in ("escalate", "schedule"):
        achado = motivo_de_recusa(C1_PROMESSA, rota, start_aconteceu=False)
        assert achado is not None, f"a promessa passou em {rota} sem start nenhum"
        assert achado[0] == RECUSA_PROMESSA_SEM_START, (
            "o grupo tem de ser PROPRIO: `promessa_de_humano` significa rota errada, este "
            "significa rota certa e fato ausente — contar os dois juntos apaga o achado"
        )


def test_com_o_start_confirmado_a_promessa_volta_a_ser_legitima() -> None:
    """A outra metade, que uma cerca incondicional quebraria: em `escalate` COM start, prometer e'
    obrigatorio — o `response_prompt` manda, e um humano foi mesmo acionado."""
    assert motivo_de_recusa(C1_PROMESSA, "escalate", start_aconteceu=True) is None
    assert motivo_de_recusa(C1_PROMESSA, "schedule", start_aconteceu=True) is None


def test_fora_das_rotas_que_escalam_o_grupo_continua_sendo_o_da_rota() -> None:
    """Nao-regressao do rotulo: sem fato declarado, um `inform` que promete continua sendo
    `promessa_de_humano`, e nao o rotulo novo."""
    achado = motivo_de_recusa(C1_PROMESSA, "inform")

    assert achado is not None
    assert achado[0] == RECUSA_PROMESSA_DE_HUMANO


def test_o_texto_do_e4_nao_menciona_encaminhamento_nenhum() -> None:
    """F2: a lista positiva tem de reprovar o E4 — e e' por isso que ela NAO contem "atendimento"
    nem "contato" soltos, as duas palavras que o texto real do E4 usa para dizer o OPOSTO
    ("central de atendimento", "entrando em contato com a central")."""
    assert menciona_encaminhamento(E4_SEM_MENCAO) is False


@pytest.mark.parametrize(
    "texto",
    [
        "Um profissional de saude vai continuar seu atendimento agora.",
        "Um atendente humano vai continuar seu atendimento.",
        "Vou encaminhar seu relato para nossa equipe de saude.",
        "Recebemos sua mensagem; um humano vai continuar o atendimento.",
        RESPOSTA_HANDOFF_RECUSADA,
        RESPOSTA_HANDOFF_JA_ABERTO,
    ],
)
def test_os_textos_de_handoff_reais_contam_como_mencao(texto: str) -> None:
    """Nao-vacuidade da lista positiva: uma lista que reprovasse tudo trocaria TODO handoff pela
    constante, e a Helena perderia a resposta personalizada em todo escalonamento legitimo."""
    assert menciona_encaminhamento(texto) is True


def test_as_duas_constantes_de_handoff_passam_na_propria_cerca_e_mencionam() -> None:
    """A propriedade que torna a substituicao segura POR CONSTRUCAO, e nao por sorte do modelo.

    Se a constante violasse a lista de padroes proibidos, a troca enviaria o que acabou de barrar;
    se ela nao mencionasse o encaminhamento, a troca da mencao obrigatoria seria um laco.

    O FATO COBRADO E' `start_aconteceu=True`, E ISSO MUDOU EM 21/09/2026 (segunda rodada). Este
    teste cobrava `False` tambem, e essa metade estava ERRADA por duas razoes:

      * SEMANTICA. As duas constantes ANUNCIAM um handoff ("encaminhamos seu caso", "seu
        atendimento (...) ja esta aberto"). Sem start, esse anuncio e' falso — e' o `C1`. Exigir
        que a cerca as aprove naquele fato e' exigir que ela aprove uma mentira.
      * ALCANCE. Nenhuma das duas e' alcancavel com `start_aconteceu=False`.
        `RESPOSTA_HANDOFF_RECUSADA` sai em dois pontos, e nos dois um humano FOI acionado: o
        `except RespostaRecusadaError` de `_start_escalation` (o processo esta' sendo aberto ali) e
        o ramo `acionado and not menciona_encaminhamento` de `_texto_bate_com_o_fato`.
        `RESPOSTA_HANDOFF_JA_ABERTO` sai so' no ramo `ja_ativo`, que e' um dos dois desfechos COM
        humano. A constante do ramo "ninguem foi acionado" e' outra
        (`RESPOSTA_SEM_ENCAMINHAMENTO`), e e' o teste seguinte que a cobra.

    A troca nao afrouxa nada: o `False` passou a ser cobrado no sentido CERTO (as duas TEM de ser
    recusadas), que e' o invariante `menciona XOR humano_acionado` do
    `test_helena_adv_texto_x_fato.py`.
    """
    for constante in (RESPOSTA_HANDOFF_RECUSADA, RESPOSTA_HANDOFF_JA_ABERTO):
        assert menciona_encaminhamento(constante) is True
        for rota in ("inform", "escalate", "schedule", "collect"):
            assert motivo_de_recusa(constante, rota, start_aconteceu=True) is None
            achado = motivo_de_recusa(constante, rota, start_aconteceu=False)
            assert achado is not None, (
                f"{constante[:40]!r} anuncia handoff e a cerca a aprova em {rota} SEM start"
            )
            assert achado[0] == RECUSA_PROMESSA_SEM_START


@pytest.mark.parametrize(
    ("rotulo", "constante"),
    [
        ("handoff recusado", RESPOSTA_HANDOFF_RECUSADA),
        ("handoff ja aberto", RESPOSTA_HANDOFF_JA_ABERTO),
        ("sem encaminhamento", RESPOSTA_SEM_ENCAMINHAMENTO),
        ("falha tecnica de start", RESPOSTA_FALHA_TECNICA_START),
    ],
)
def test_as_constantes_passam_na_propria_regua_de_clareza_da_helena(rotulo: str, constante: str) -> None:
    """21/09/2026 — um achado que so' apareceu porque a cerca tornou as constantes ALCANCAVEIS.

    `RESPOSTA_HANDOFF_RECUSADA` virou substituto na rota `schedule`, que `EVL-HELENA-CLAREZA-03`
    mede — e reprovou: a primeira oracao tinha 22 palavras contra o limite de 20 que o proprio
    golden declara. `RESPOSTA_FALHA_TECNICA_START` tinha 21 e nunca havia sido medida (nenhum
    golden de falha de start carrega bloco `clarity`). As duas foram quebradas em oracoes, sem
    mudar conteudo.

    O limite 20 nao e' escolhido aqui: e' o `max_words_per_sentence` que os dois goldens de
    clareza da Helena declaram. Uma frase que a regua da propria agente reprova nao pode ser a
    saida honesta dela — e uma constante e' o unico texto do sistema que NINGUEM revisa a cada
    turno, porque nao passa por modelo nenhum.
    """
    from tests.evals._harness import score_clarity

    relatorio = score_clarity(constante, max_words_per_sentence=20)

    assert not relatorio.long_sentences, (
        f"{rotulo}: oracao acima de 20 palavras -> {relatorio.long_sentences}"
    )
    assert relatorio.word_count >= relatorio.min_words


def test_as_constantes_de_nao_start_nunca_prometem_um_humano() -> None:
    """O par simetrico: as duas frases usadas quando NINGUEM foi acionado nao podem conter
    promessa nenhuma, em rota nenhuma — senao a substituicao reintroduziria o defeito."""
    for constante in (RESPOSTA_SEM_ENCAMINHAMENTO, RESPOSTA_FALHA_TECNICA_START):
        for rota in ("inform", "escalate", "schedule", "collect"):
            assert motivo_de_recusa(constante, rota, start_aconteceu=False) is None


# =================================================================================================
# 2. O desfecho REAL do start, derivado do `StartOutcome` que o grafo ignorava
# =================================================================================================


@pytest.mark.parametrize(
    ("outcome", "esperado", "ha_humano"),
    [
        (StartOutcome.STARTED, START_DESFECHO_NOVO, True),
        (StartOutcome.ALREADY_ACTIVE, START_DESFECHO_JA_ATIVO, True),
        (StartOutcome.ALREADY_COMPLETED, START_DESFECHO_JA_CONCLUIDO, False),
        (StartOutcome.UNREPORTED, START_DESFECHO_NAO_REPORTADO, False),
    ],
)
def test_cada_veredito_do_chokepoint_tem_um_desfecho_e_um_veredito_de_humano(
    outcome: StartOutcome, esperado: str, ha_humano: bool
) -> None:
    """Os quatro membros de `StartOutcome`, um a um — e QUAIS deles significam "ha' um humano".

    `ALREADY_COMPLETED` fica FORA: uma instancia que ja' rodou e terminou nao e' alguem com o caso
    agora. `UNREPORTED` tambem: sem veredito nao se afirma que um humano foi acionado.
    """
    assert _start_desfecho_de(outcome) == esperado
    assert _humano_acionado({"start_desfecho": esperado}) is ha_humano


def test_um_valor_de_outcome_desconhecido_nao_cai_no_ramo_do_sucesso() -> None:
    """Fail-closed do tradutor: um membro novo em `StartOutcome` (ou um duplo devolvendo qualquer
    string) vira `nao_reportado`, nunca `novo`."""
    assert _start_desfecho_de("UM_VEREDITO_QUE_NAO_EXISTE") == START_DESFECHO_NAO_REPORTADO
    assert _start_desfecho_de(None) == START_DESFECHO_NAO_REPORTADO


def test_estado_sem_o_campo_nao_afirma_que_um_humano_foi_acionado() -> None:
    """A omissao e' o lado seguro, e `escalation_started` NAO e' substituto: era exatamente o
    sinal que o C1 tinha ligado (uma instancia existia — de outro turno) com a promessa falsa."""
    assert _humano_acionado({}) is False
    assert _humano_acionado({"escalation_started": True}) is False
    assert _humano_acionado({"start_desfecho": START_DESFECHO_NAO_TENTADO}) is False
    assert _humano_acionado({"start_desfecho": START_DESFECHO_FALHOU}) is False


async def test_o_start_idempotente_sobre_instancia_viva_e_declarado_ja_ativo() -> None:
    """O C1 reproduzido no grafo: a chave colide com um escalonamento anterior AINDA ABERTO.

    Nada levanta, `escalation_started` e' `True` (a instancia existe) — e o que mudou e' que o
    estado agora DIZ que nenhuma escalacao nova nasceu.
    """
    graph = _graph(C1_PROMESSA, cibseven=_com_instancia_viva())

    saida = await graph.escalate(_estado(escalation_motivo="red_flag_clinico", escalation_severidade="grave"))

    assert saida["escalation_started"] is True
    assert saida["start_desfecho"] == START_DESFECHO_JA_ATIVO
    assert saida.get("start_failed") is not True, "nada falhou — a idempotencia funcionou"
    assert saida["escalation_process_ref"]["instance_id"] == "instancia-de-uma-bateria-anterior"
    assert saida["escalation_process_ref"]["start_outcome"] == StartOutcome.ALREADY_ACTIVE.value


async def test_um_start_de_verdade_e_declarado_novo() -> None:
    """O RED do teste acima: sem colisao, o mesmo caminho declara `novo`. Sem isto, um campo
    constante em `ja_ativo` passaria os dois testes."""
    graph = _graph("Um profissional de saude vai continuar seu atendimento agora.")

    saida = await graph.escalate(_estado(escalation_motivo="red_flag_clinico", escalation_severidade="grave"))

    assert saida["start_desfecho"] == START_DESFECHO_NOVO
    assert saida["escalation_process_ref"]["start_outcome"] == StartOutcome.STARTED.value


async def test_a_falha_de_start_e_declarada_no_mesmo_vocabulario() -> None:
    """CC-01 continua de pe' e passa a falar o mesmo idioma: `start_failed=True` E
    `start_desfecho="falhou"`, para `respond` ter UMA fonte a consultar."""

    class _MotorForaDoAr(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            raise CibSevenError("engine indisponivel (teste)")

    graph = _graph("qualquer texto", cibseven=_MotorForaDoAr())

    saida = await graph.escalate(_estado(escalation_motivo="red_flag_clinico"))

    assert saida["start_failed"] is True
    assert saida["start_desfecho"] == START_DESFECHO_FALHOU
    assert _humano_acionado(saida) is False


# =================================================================================================
# 3. `respond`: o unico ponto em que o fato ja' esta' decidido e o texto ainda nao saiu
# =================================================================================================


async def test_c1_no_grafo_a_promessa_e_trocada_pelo_texto_honesto() -> None:
    """O caso C1 fechado ponta a ponta: a Helena passa a dizer que o atendimento JA estava aberto.

    O rascunho e' trocado INCONDICIONALMENTE neste ramo — nao e' "o modelo errou a frase", e' que a
    frase certa depende de um fato que o modelo nao tinha quando redigiu.
    """
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            response_text=C1_PROMESSA,
            response_kind="escalate",
            escalation_started=True,
            escalation_business_key=BUSINESS_KEY,
            start_desfecho=START_DESFECHO_JA_ATIVO,
        )
    )

    assert whatsapp.enviados == [("c1_deadbeef", RESPOSTA_HANDOFF_JA_ABERTO)]
    assert saida["response_text"] == RESPOSTA_HANDOFF_JA_ABERTO
    assert "entrara em contato" not in saida["response_text"]
    assert "Aguarde nosso contato" not in saida["response_text"]


async def test_o_ja_ativo_deixa_rastro_proprio_em_vez_de_contar_como_escalonamento() -> None:
    """ITEM 3 do diretor ("toda recusa/nao-start deixa rastro"). Antes deste desfecho o turno
    contava como `escalado_humano`: trabalho creditado numa fila que ele nao criou — que e'
    precisamente o que deixou 27 escalonamentos reusados invisiveis durante a bateria."""
    graph = _graph()

    saida = await graph.respond(
        _estado(
            response_text=C1_PROMESSA,
            response_kind="escalate",
            escalation_started=True,
            start_desfecho=START_DESFECHO_JA_ATIVO,
        )
    )

    assert saida["desfecho"] == DESFECHO_ESCALONAMENTO_JA_ABERTO
    assert saida["desfecho"] != "escalado_humano"
    assert saida.get("error") is None, (
        "um `already_existed` nao e' falha de ninguem: escrever `error` aqui sujaria o sufixo "
        "`[falha tecnica: ...]` do handoff do turno seguinte com um fato que nao e' falha"
    )


def test_o_desfecho_novo_esta_no_vocabulario_fechado_da_telemetria() -> None:
    """Fora de `_DESFECHO_VOCAB["helena"]` o valor e' normalizado para `"outro"` — e a distincao
    que este achado existe para criar morreria no label."""
    assert DESFECHO_ESCALONAMENTO_JA_ABERTO in _DESFECHO_VOCAB["helena"]


async def test_e4_no_grafo_o_start_aconteceu_e_o_texto_passa_a_dizer() -> None:
    """F2 fechado: alguem do atendimento recebeu o caso, entao o beneficiario e' avisado."""
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            message_body="quanto eu devo de mensalidade?",
            response_text=E4_SEM_MENCAO,
            response_kind="escalate",
            escalation_started=True,
            escalation_motivo="solicitacao_humano",
            start_desfecho=START_DESFECHO_NOVO,
        )
    )

    assert saida["response_text"] == RESPOSTA_HANDOFF_RECUSADA
    assert menciona_encaminhamento(whatsapp.enviados[0][1]) is True
    assert saida["error"] == ERRO_HANDOFF_SEM_MENCAO
    assert saida["desfecho"] == "escalado_humano"


async def test_um_handoff_que_ja_menciona_o_encaminhamento_sai_intacto() -> None:
    """Nao-vacuidade: a cerca nao troca o texto do caminho certo. Sem este teste, uma substituicao
    incondicional passaria o de cima e destruiria toda resposta de escalonamento legitima."""
    redigido = "Um profissional de saude vai continuar seu atendimento agora."
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            response_text=redigido,
            response_kind="escalate",
            escalation_started=True,
            start_desfecho=START_DESFECHO_NOVO,
        )
    )

    assert whatsapp.enviados == [("c1_deadbeef", redigido)]
    assert "response_text" not in saida, "texto conforme nao e' reescrito no estado"
    assert saida["desfecho"] == "escalado_humano"


async def test_promessa_sem_start_numa_rota_que_nao_escala_e_trocada() -> None:
    """O backstop ESTRUTURAL, e ele tem um caminho REAL: o fallback canned de `_respond_llm`
    ("Recebemos sua mensagem. Um profissional humano vai continuar o atendimento em breve.") sai
    por um `return` DENTRO do `except` e nunca passa pela cerca de 13/09 — num turno `inform`
    ninguem foi acionado e a frase e' falsa."""
    fallback_do_respond_llm = (
        "Recebemos sua mensagem. Um profissional humano vai continuar o atendimento em breve. "
        "Alguem da equipe entrara em contato com voce."
    )
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(_estado(response_text=fallback_do_respond_llm, response_kind="inform"))

    assert whatsapp.enviados == [("c1_deadbeef", RESPOSTA_SEM_ENCAMINHAMENTO)]
    assert saida["response_text"] == RESPOSTA_SEM_ENCAMINHAMENTO
    assert saida["error"] == ERRO_PROMESSA_SEM_START
    assert saida["desfecho"] == "resolvido_automatico"


async def test_um_inform_honesto_nao_e_tocado() -> None:
    """A metade que prova que a cerca nao e' uma mordaca: uma resposta administrativa que nao
    promete humano nenhum sai exatamente como foi redigida."""
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(_estado(response_text=E4_SEM_MENCAO, response_kind="inform"))

    assert whatsapp.enviados == [("c1_deadbeef", E4_SEM_MENCAO)]
    assert "response_text" not in saida
    assert "error" not in saida


async def test_a_cerca_nao_mascara_o_turno_que_nao_enviou_nada() -> None:
    """HEL-07 continua sendo quem trata o rascunho vazio. Substituir aqui esconderia um "nada foi
    enviado" atras de uma frase plausivel — e o contador com `enviada=False` e' o unico jeito de
    distinguir "nao enviei" de "enviei e o transporte falhou"."""
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(response_text="   ", response_kind="escalate", start_desfecho=START_DESFECHO_NOVO)
    )

    assert whatsapp.enviados == []
    assert saida["desfecho"] == "resposta_vazia_nao_enviada"


async def test_a_cerca_nao_mexe_na_rota_nem_no_efeito() -> None:
    """DISCIPLINA DE ESCOPO: a rota aconteceu e o processo abriu. O que estava errado e' a NARRACAO
    deles, e e' so' a narracao que se corrige — `response_kind`/`escalation_*` ficam intactos."""
    graph = _graph()

    saida = await graph.respond(
        _estado(
            response_text=E4_SEM_MENCAO,
            response_kind="schedule",
            escalation_started=True,
            escalation_motivo="solicitacao_humano",
            start_desfecho=START_DESFECHO_NOVO,
        )
    )

    assert "response_kind" not in saida
    assert "escalation_motivo" not in saida
    assert "escalation_started" not in saida


async def test_o_turno_de_falha_de_start_segue_com_a_constante_de_cc01() -> None:
    """Nao-regressao de CC-01: o ramo de falha nao passa pela cerca nova — seu texto ja' e' a
    constante honesta, e o desfecho vem do helper compartilhado."""
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            response_text=C1_PROMESSA,
            response_kind="escalate",
            start_failed=True,
            start_desfecho=START_DESFECHO_FALHOU,
            escalation_business_key=BUSINESS_KEY,
        )
    )

    assert whatsapp.enviados == [("c1_deadbeef", RESPOSTA_FALHA_TECNICA_START)]
    assert saida["desfecho"] == "erro_inicio_processo"
    assert saida["response_kind"] == "falha_tecnica_start"


# =================================================================================================
# 4. Item 2 do diretor: `_start_failure_outcome` cobre so' `CibSevenError` — e esta' certo
# =================================================================================================


async def test_falha_nao_cibseven_no_start_propaga_em_vez_de_virar_falha_tecnica() -> None:
    """ITEM 2, medido em vez de suposto.

    `StartVariableRedactionError` (como `AuditPersistenceError`,
    `StartClaimWithoutInstanceError` e `StartDedupGateUnavailableError`) NAO e' `CibSevenError`, e
    cada uma declara no proprio docstring que isso e' DELIBERADO: um scrub de PHI que nao roda nao
    e' "o fornecedor caiu", e' defeito do proprio controle. Este teste FIXA a propagacao — se
    alguem alargar o `except` para `Exception` "por seguranca", a classe inteira passa a degradar
    silenciosamente para um turno de falha tecnica a mais.
    """

    class _ScrubQuebrado(FakeCibSevenTransport):
        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            raise StartVariableRedactionError("scrub CC-06 indisponivel (teste)")

    graph = _graph("qualquer texto", cibseven=_ScrubQuebrado())

    with pytest.raises(StartVariableRedactionError):
        await graph.escalate(_estado(escalation_motivo="red_flag_clinico"))


async def test_uma_subclasse_de_cibseven_error_cai_no_ramo_de_falha_tecnica() -> None:
    """O contraste que prova que o `except` nao e' estreito por acidente:
    `CibSevenStartAuthorizationError` E' subclasse de `CibSevenError` (D7-A) e portanto e' tratada
    como toda recusa do engine no momento do start."""

    class _AutorizacaoRecusada(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            raise CibSevenStartAuthorizationError(EngineRefusalCode.OPERATION_DENIED)

    graph = _graph("qualquer texto", cibseven=_AutorizacaoRecusada())

    saida = await graph.escalate(_estado(escalation_motivo="red_flag_clinico"))

    assert saida["start_failed"] is True
    assert saida["start_desfecho"] == START_DESFECHO_FALHOU


def test_o_except_do_start_nomeia_exatamente_cibseven_error() -> None:
    """ITEM 2 por AST, e nao por leitura: QUAIS excecoes o `try` do start absorve.

    Esta e' a cerca que impede o conserto errado. A tentacao, diante do C1, e' alargar o `except`
    para `Exception` "para nao perder falha nenhuma" — e isso transformaria um scrub de PHI
    quebrado, um audit que nao persiste e um portao de dedup nao avaliavel em "uma falha tecnica a
    mais", exatamente a degradacao silenciosa que cada uma daquelas classes foi desenhada para
    evitar ao NAO herdar de `CibSevenError`.

    E' tambem a resposta a hipotese do relatorio: uma recusa de contrato do worker
    (`tools/workers/escalation.py::_exigir_severidade`) nao chega a este `except` por CONSTRUCAO —
    o worker e' um external task que o motor entrega DEPOIS de a instancia existir, em outro
    processo. O grafo nao importa aquele modulo (asserido abaixo) e nao teria de onde receber a
    excecao: se o worker recusa, o start SUCEDEU.
    """
    import ast
    import inspect

    from maezo.agents.helena import graph as helena_graph

    arvore = ast.parse(inspect.getsource(helena_graph))
    handlers: list[list[str]] = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Try):
            continue
        chama_o_chokepoint = any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "start_process_idempotent"
            for corpo in no.body
            for inner in ast.walk(corpo)
        )
        if chama_o_chokepoint:
            handlers.append([ast.unparse(h.type) if h.type is not None else "BARE" for h in no.handlers])

    assert handlers == [["CibSevenError"]], (
        f"o `try` do start de SP-OP-ESCALATION-001 absorve {handlers} — tem de ser exatamente "
        "`CibSevenError`: as outras excecoes do chokepoint (StartVariableRedactionError, "
        "AuditPersistenceError, StartClaimWithoutInstanceError, StartDedupGateUnavailableError) "
        "sao defeito do proprio controle e PROPAGAM de proposito"
    )
    # O worker de escalonamento nao e' um seam deste grafo: sem import, sem call site, sem
    # excecao dele para o `except` acima receber.
    importados = {
        alvo.module for alvo in ast.walk(arvore) if isinstance(alvo, ast.ImportFrom) and alvo.module
    }
    assert not any("workers.escalation" in modulo for modulo in importados)


def test_o_grupo_do_ja_ativo_e_fechado_e_distinto_dos_outros() -> None:
    """Os rotulos de metrica sao vocabulario FECHADO (cardinalidade): os cinco grupos desta cerca
    precisam ser distintos, senao um contador agrega achados diferentes sob o mesmo nome."""
    grupos = {
        RECUSA_PROMESSA_DE_HUMANO,
        RECUSA_PROMESSA_SEM_START,
        RECUSA_HANDOFF_SEM_MENCAO,
        RECUSA_ESCALONAMENTO_JA_ABERTO,
    }
    assert len(grupos) == 4


# =================================================================================================
# 5. O RASTRO (item 3 do diretor, 21/09/2026 — segunda rodada): log, nivel e contador
# =================================================================================================


def _contador_espiao(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Registra as chamadas a `record_resposta_recusada` FEITAS PELO GRAFO.

    Espiona o nome no namespace de `graph` (onde ele foi importado), nao no modulo de
    observabilidade: e' o call site que este arquivo prova, e um espiao na origem passaria mesmo
    se o grafo parasse de chamar.
    """
    chamadas: list[dict[str, Any]] = []

    def _spy(*, agent_id: str, motivo: str, response_kind: str) -> None:
        chamadas.append({"agent_id": agent_id, "motivo": motivo, "response_kind": response_kind})

    monkeypatch.setattr(helena_graph, "record_resposta_recusada", _spy)
    return chamadas


@pytest.mark.parametrize(
    ("rotulo", "estado", "grupo"),
    [
        (
            "promessa sem start",
            {"response_text": C1_PROMESSA, "response_kind": "inform"},
            RECUSA_PROMESSA_SEM_START,
        ),
        (
            "handoff sem mencao",
            {
                "response_text": E4_SEM_MENCAO,
                "response_kind": "escalate",
                "escalation_started": True,
                "start_desfecho": START_DESFECHO_NOVO,
            },
            RECUSA_HANDOFF_SEM_MENCAO,
        ),
        (
            "escalonamento ja aberto",
            {
                "response_text": C1_PROMESSA,
                "response_kind": "escalate",
                "escalation_started": True,
                "start_desfecho": START_DESFECHO_JA_ATIVO,
            },
            RECUSA_ESCALONAMENTO_JA_ABERTO,
        ),
    ],
)
async def test_cada_troca_emite_log_e_contador_com_o_grupo(
    monkeypatch: pytest.MonkeyPatch, rotulo: str, estado: dict[str, Any], grupo: str
) -> None:
    """Os TRES grupos, um a um, nos dois canais de rastro — e a razao de existirem separados.

    O `error` de estado nao aparece nos tres (`ja_ativo` nao e' falha de ninguem), entao o log e o
    contador sao o UNICO rastro comum. Sem este teste, um `_registrar_troca` que deixasse de emitir
    passaria: as substituicoes continuariam corretas e o nao-start voltaria a ser invisivel — que
    e' exatamente como 27 escalonamentos reusados atravessaram uma bateria inteira.
    """
    chamadas = _contador_espiao(monkeypatch)
    graph = _graph()

    with capture_logs() as registros:
        await graph.respond(_estado(**estado))

    evento = next(r for r in registros if r["event"] == "helena_texto_nao_bate_com_o_fato")
    assert evento["grupo"] == grupo
    assert evento["node"] == "respond"
    assert evento["recusa_version"] == RECUSA_DE_SAIDA_VERSION
    assert chamadas == [{"agent_id": "helena", "motivo": grupo, "response_kind": estado["response_kind"]}]


async def test_o_nivel_do_log_separa_a_idempotencia_do_texto_mentiroso() -> None:
    """21/09/2026 (segunda rodada): `escalonamento_ja_aberto` sai em `warning`, os dois de TEXTO x
    FATO em `error`.

    A distincao nao e' estetica: num `ja_ativo` a idempotencia FUNCIONOU e ninguem precisa ser
    acordado — o mesmo raciocinio que ja mantinha o `error` de estado fora daquele ramo. Emitir
    tudo como `error` treina a operacao a ignorar o canal em que os outros dois aparecem, e esses
    dois significam que a Helena ia dizer ao beneficiario algo que nao aconteceu.
    """
    graph = _graph()

    with capture_logs() as ja_aberto:
        await graph.respond(
            _estado(
                response_text=C1_PROMESSA,
                response_kind="escalate",
                escalation_started=True,
                start_desfecho=START_DESFECHO_JA_ATIVO,
            )
        )
    with capture_logs() as sem_start:
        await graph.respond(_estado(response_text=C1_PROMESSA, response_kind="inform"))

    def _nivel(registros: list[dict[str, Any]]) -> str:
        evento = next(r for r in registros if r["event"] == "helena_texto_nao_bate_com_o_fato")
        return str(evento["log_level"])

    assert _nivel(ja_aberto) == "warning"
    assert _nivel(sem_start) == "error"


async def test_o_start_sobre_instancia_viva_deixa_rastro_no_proprio_start() -> None:
    """O OUTRO sinal do item 3, e ele nasce um no' antes: `helena_escalonamento_nao_aberto`.

    Antes dele um nao-start SEM erro era completamente silencioso — nenhuma excecao, nenhum
    contador, nenhum campo. O log carrega a `business_key` (que o proprio engine ja ve) e o
    desfecho; nada ali e' texto de beneficiario, e o teste cobra as duas coisas.
    """
    graph = _graph(C1_PROMESSA, cibseven=_com_instancia_viva())

    with capture_logs() as registros:
        await graph.escalate(_estado(escalation_motivo="red_flag_clinico", escalation_severidade="grave"))

    evento = next(r for r in registros if r["event"] == "helena_escalonamento_nao_aberto")
    assert evento["log_level"] == "warning"
    assert evento["start_desfecho"] == START_DESFECHO_JA_ATIVO
    assert evento["business_key"] == BUSINESS_KEY
    assert evento["process_key"] == PROCESS_KEY
    assert "dor de cabeca" not in json.dumps(registros), "texto do beneficiario num log estruturado"


# =================================================================================================
# 6. O `ja_ativo` nao e' mais uma borracha (21/09/2026, segunda rodada)
# =================================================================================================


async def test_o_ja_ativo_preserva_a_confirmacao_de_memoria_e_a_orientacao() -> None:
    """O rascunho que NAO anuncia handoff novo sobrevive atras da constante.

    O QUE A TROCA INCONDICIONAL CUSTAVA, e por que isto nao e' preferencia de redacao: a
    `memoria_a_confirmar` e' a pergunta que o `response_prompt` OBRIGA a Helena a fazer antes de
    usar um dado LEMBRADO ("entendi que voce esta falando sobre sua crianca de 3 anos, certo?"), e
    ela existe porque o custo de uma populacao errada lembrada e' a tabela errada consultada em
    silencio. Num turno `ja_ativo` a pessoa perdia a chance de corrigir o dado clinico — e o motivo
    era um fato (a escalacao ja' estava aberta) que nada tem a ver com aquele dado.
    """
    confirmacao = "entendi que voce esta falando sobre sua crianca de 3 anos, certo?"
    rascunho = f"{confirmacao} Enquanto isso, se a febre subir, procure emergencia."
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            response_text=rascunho,
            response_kind="escalate",
            escalation_started=True,
            start_desfecho=START_DESFECHO_JA_ATIVO,
        )
    )

    enviado = whatsapp.enviados[0][1]
    assert enviado.startswith(RESPOSTA_HANDOFF_JA_ABERTO), "o fato vem primeiro, sempre"
    assert confirmacao in enviado, "a pergunta de confirmacao do dado lembrado nao pode desaparecer"
    assert "procure emergencia" in enviado
    assert saida["response_text"] == enviado
    assert saida.get("error") is None, "a idempotencia funcionou; nada falhou"


async def test_o_ja_ativo_continua_descartando_o_anuncio_de_handoff_novo() -> None:
    """O RED do teste acima: o prefixo NAO e' a regra geral. Um rascunho que anuncia um
    encaminhamento NOVO continua sendo descartado inteiro — prefixar a constante nele produziria
    "nao abri outro atendimento. Um profissional vai entrar em contato", que e' pior que os dois
    textos separados."""
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    await graph.respond(
        _estado(
            response_text=C1_PROMESSA,
            response_kind="escalate",
            escalation_started=True,
            start_desfecho=START_DESFECHO_JA_ATIVO,
        )
    )

    assert whatsapp.enviados == [("c1_deadbeef", RESPOSTA_HANDOFF_JA_ABERTO)]


# =================================================================================================
# 7. O texto que o codigo envia quando a REDACAO cai (21/09/2026, segunda rodada)
# =================================================================================================


def test_o_fallback_de_redacao_nao_promete_nada_e_passa_nas_quatro_cercas() -> None:
    """A constante que substituiu a promessa canned de `_respond_llm`.

    Ela e' o unico texto do modulo que sai SEM ter passado por modelo nenhum num turno normal, e
    por isso e' cobrada nas quatro cercas de uma vez — inclusive com `start_aconteceu=False`, que
    e' o fato do turno `inform` em que ela e' alcancada. A regua de clareza entra pela mesma razao
    do bloco 1: uma constante e' o unico texto que ninguem revisa a cada turno.
    """
    from tests.evals._harness import score_clarity

    assert menciona_encaminhamento(RESPOSTA_FALHA_DE_REDACAO) is False
    assert motivo_de_canal_nao_confirmado(RESPOSTA_FALHA_DE_REDACAO) is None
    for rota in ("inform", "escalate", "schedule", "collect"):
        assert motivo_de_recusa(RESPOSTA_FALHA_DE_REDACAO, rota, start_aconteceu=False) is None
        assert motivo_de_recusa(RESPOSTA_FALHA_DE_REDACAO, rota, start_aconteceu=None) is None

    relatorio = score_clarity(RESPOSTA_FALHA_DE_REDACAO, max_words_per_sentence=20)
    assert not relatorio.long_sentences, f"oracao acima de 20 palavras -> {relatorio.long_sentences}"


async def test_a_troca_da_cerca_nao_apaga_o_motivo_tecnico_do_turno() -> None:
    """`error` que o turno JA tinha nao e' sobrescrito pela cerca.

    O caso que expos isto: o provedor de inferencia cai, `classify` escreve o motivo tecnico em
    `error` (que e' o que o atendente le' no `[falha tecnica: ...]` do handoff) e o rascunho vira a
    constante de falha de redacao — que nao menciona o encaminhamento. A troca do item 2 gravava
    `handoff sem mencao` em cima do motivo. A narracao do texto nao pode apagar a causa do turno; o
    rastro da troca continua no log e no contador.
    """
    motivo_tecnico = "classify LLM call failed: RuntimeError"
    graph = _graph()

    saida = await graph.respond(
        _estado(
            response_text=RESPOSTA_FALHA_DE_REDACAO,
            response_kind="escalate",
            escalation_started=True,
            start_desfecho=START_DESFECHO_NOVO,
            error=motivo_tecnico,
        )
    )

    assert saida["response_text"] == RESPOSTA_HANDOFF_RECUSADA, "a troca acontece de qualquer forma"
    assert saida.get("error") in (None, motivo_tecnico)
    assert saida.get("error") != ERRO_HANDOFF_SEM_MENCAO
