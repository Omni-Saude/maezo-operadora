"""ADVERSARIAL (21/09/2026) — a cerca TEXTO x FATO vista de fora: o que ela DEIXA passar.

Este arquivo nao reescreve `test_helena_cerca_texto_x_fato.py`. Ele ataca o que aquele arquivo nao
mediu: a cerca foi construida a partir de DOIS textos (o `C1` e o `E4` da bateria) e a lista de
promessa proibida foi escrita com as frases DAQUELES textos. A pergunta adversarial e' outra:
existe texto que a propria entrega reconhece como "um humano assumiu" e que a cerca NAO recusa
quando nenhum humano assumiu?

Existe, e um deles nem vem de modelo: e' uma CONSTANTE do proprio `graph.py`.

O INVARIANTE que este arquivo cobra, escrito com as duas pecas que a entrega ja' tem:

    menciona_encaminhamento(texto) and not _humano_acionado(estado)  ->  o texto NAO pode sair

E' a formulacao minima do F1 ("o beneficiario acredita que um humano vem, e ninguem vem") usando
so' o que ja' existe no modulo. `menciona_encaminhamento` e' a definicao QUE A PROPRIA ENTREGA deu
para "este texto avisa que um humano assumiu" — entao um texto que ela aprova por aquele lado e
nao recusa por este e' uma contradicao interna, nao uma opiniao de quem testa.

Tudo aqui roda contra as funcoes reais (`motivo_de_recusa`, `menciona_encaminhamento`) e contra os
nos reais (`inform`, `collect`, `respond`), com os duplos estruturais do repo.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from maezo.agents.helena.graph import (
    ERRO_PROMESSA_SEM_START,
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
)
from maezo.agents.helena.prompts import (
    RECUSA_ESCALONAMENTO_JA_ABERTO,
    RECUSA_HANDOFF_SEM_MENCAO,
    RECUSA_PROMESSA_SEM_START,
    RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.inference.errors import InferenceProviderError
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

# --- O texto que o PROPRIO CODIGO envia quando a inferencia cai --------------------------------
#
# `graph.py::_respond_llm`, no `except EXTERNAL_DEPENDENCY_FAILURES`. E' o unico texto do modulo
# que promete um humano SEM ter passado por cerca nenhuma: o `return` esta' ANTES do bloco de
# recusa, entao nem a cerca de saida nem a de canal o veem. Copiado verbatim.
FALLBACK_DE_TRANSPORTE = (
    "Recebemos sua mensagem. Um profissional humano vai continuar o atendimento em breve."
)

#: Rascunhos PLAUSIVEIS de handoff, em portugues que um modelo escreve — nenhum deles usa as
#: frases do `C1`. Todos sao aprovados por `menciona_encaminhamento` (isto e', a entrega afirma
#: que eles avisam o beneficiario de que um humano assumiu).
RASCUNHOS_QUE_ANUNCIAM_HUMANO: tuple[str, ...] = (
    FALLBACK_DE_TRANSPORTE,
    "Um profissional vai dar continuidade ao seu atendimento.",
    "Encaminhamos seu caso para a nossa equipe de saude.",
    "Nossa equipe de saude vai assumir seu caso agora.",
    "Um atendente vai falar com voce.",
    "Seu atendimento ja esta aberto com a enfermagem.",
    "O plantao clinico vai avaliar seu caso.",
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


class _InferenciaForaDoAr:
    """O provedor de inferencia indisponivel — a falha EXTERNA DECLARADA de
    `InferenceProvider.generate` (`InferenceProviderError <: RuntimeError`, portanto dentro de
    `EXTERNAL_DEPENDENCY_FAILURES`). E' o gatilho do fallback canned de `_respond_llm`."""

    def __init__(self) -> None:
        self.chamadas = 0
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.chamadas += 1
        raise InferenceProviderError("noop", "provider unavailable (duplo de teste)", retryable=True)


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append((to_hash, text))
        return {"ok": True}


def _graph(
    inferencia: object | None = None,
    *,
    whatsapp: _WhatsApp | None = None,
    coleta_enabled: bool = False,
) -> HelenaGraph:
    return HelenaGraph(
        # `cast` e nao `type: ignore`: o duplo satisfaz o Protocol ESTRUTURALMENTE (o seam e'
        # duck-typed), o mesmo idioma de `HelenaGraph.build()` e dos testes do repo.
        inference=cast(InferenceProvider, inferencia or _InferenciaFixa("irrelevante")),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=cast(Any, whatsapp or _WhatsApp()),
        coleta_enabled=coleta_enabled,
    )


def _estado(**extra: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:adv_c1",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-ADV",
        "message_body": "quanto eu devo de mensalidade?",
    }
    s.update(extra)  # type: ignore[typeddict-item]
    return s


# =================================================================================================
# 1. O BUG: o texto que o proprio codigo envia quando a inferencia cai
# =================================================================================================


def test_bug_o_fallback_de_transporte_promete_humano_e_a_cerca_nao_o_recusa() -> None:
    """REPROVA — bug real (F1 reaberto por um caminho que nao passa por modelo nenhum).

    `graph.py::_respond_llm` (ramo `except EXTERNAL_DEPENDENCY_FAILURES`) devolve
    "Um profissional humano vai continuar o atendimento em breve." e o `return` esta' ANTES do
    bloco de recusa — o proprio docstring de `_texto_bate_com_o_fato` reconhece isso e declara que
    a cerca nova e' o backstop desse caso:

        "e' o backstop ESTRUTURAL para as rotas que nao abrem processo, incluindo o fallback
         canned de `_respond_llm` num turno `inform`, que promete 'um profissional humano vai
         continuar' sem passar pela cerca."

    Nao e'. `PROMESSA_DE_HUMANO_PROIBIDA` tem "um profissional humano entrara" e o canned diz "um
    profissional humano VAI CONTINUAR" — nenhum dos 22 padroes casa, entao `motivo_de_recusa`
    devolve `None` e o texto sai inteiro num turno em que ZERO processos existem. E' exatamente o
    dano do C1 ("uma pessoa ficou esperando um telefonema que ninguem ia dar"), agora sem modelo
    no meio.

    ALCANCE, medido e nao suposto: uma queda TOTAL do provedor falha primeiro no `classify` (mesma
    porta, `phi=True`) e o turno vira gatilho 4 -> escalate, que e' correto. O caminho deste
    achado e' a falha da SEGUNDA chamada depois de a primeira ter passado — timeout, 429 ou recusa
    de zona na redacao, que e' uma requisicao separada e muito maior (prefixo cacheavel + bloco
    nao confiavel). E' o cenario que este teste monta chamando o no' `inform` direto.

    A propria entrega afirma, pelo outro lado, que este texto anuncia um humano —
    `menciona_encaminhamento(FALLBACK_DE_TRANSPORTE) is True`, cobrado abaixo para que este teste
    nao possa ser lido como opiniao de quem testa.
    """
    assert menciona_encaminhamento(FALLBACK_DE_TRANSPORTE) is True, (
        "premissa do achado: a entrega classifica este texto como aviso de handoff"
    )

    achado = motivo_de_recusa(FALLBACK_DE_TRANSPORTE, "inform", start_aconteceu=False)

    assert achado is not None, (
        "o texto que o PROPRIO codigo envia quando a inferencia cai promete um humano e passa "
        "pela cerca com `start_aconteceu=False` (prompts.py:769 `motivo_de_recusa`; o texto vem "
        "de graph.py:2565)"
    )
    assert achado[0] == RECUSA_PROMESSA_SEM_START


async def test_o_fallback_de_transporte_nao_promete_humano_num_turno_inform() -> None:
    """O mesmo bug ponta a ponta, pelos nos reais — e o conserto nas DUAS camadas.

    Reproducao minima: provedor de inferencia fora do ar + rota `inform`. `inform` degrada para o
    canned (o `except` declarado), `respond` chama a cerca TEXTO x FATO e `start_desfecho` e'
    `nao_tentado` (`_humano_acionado` -> False).

    O QUE MUDOU NA PREMISSA, e por que ela nao podia continuar como estava. A versao vermelha deste
    teste afirmava "este e' o texto que o no' `inform` produz quando a inferencia cai" e apontava
    para `FALLBACK_DE_TRANSPORTE` — a promessa de humano. O conserto do achado e' exatamente
    remover a promessa DA ORIGEM (`graph.py::RESPOSTA_FALHA_DE_REDACAO`), entao uma premissa que
    fixa a promessa na origem impediria o conserto: ela cobrava a permanencia do defeito.

    O que este teste cobra agora e' o par completo:

      1. CAMADA 1 (origem) — o texto que o `inform` produz sem modelo nao promete humano, nao
         promete prazo e nao cita canal; ele SAI, porque nao ha nada de errado nele;
      2. CAMADA 2 (backstop) — a promessa antiga, se voltar por qualquer caminho, continua sendo
         trocada com `error=ERRO_PROMESSA_SEM_START`. E' a unica forma de a camada 1 nao ser a
         unica defesa, e e' `FALLBACK_DE_TRANSPORTE` (a constante literal, copiada da versao
         anterior) que faz esse papel aqui.
    """
    whatsapp = _WhatsApp()
    graph = _graph(_InferenciaForaDoAr(), whatsapp=whatsapp)

    do_inform = await graph.inform(_estado())
    assert do_inform["response_text"] == RESPOSTA_FALHA_DE_REDACAO, (
        "o no' `inform` com a inferencia fora do ar tem de cair na constante HONESTA"
    )
    assert menciona_encaminhamento(RESPOSTA_FALHA_DE_REDACAO) is False
    assert motivo_de_recusa(RESPOSTA_FALHA_DE_REDACAO, "inform", start_aconteceu=False) is None
    assert motivo_de_canal_nao_confirmado(RESPOSTA_FALHA_DE_REDACAO) is None

    saida = await graph.respond(_estado(**do_inform))

    assert _humano_acionado(_estado(**do_inform)) is False, "premissa: nenhum humano foi acionado"
    assert whatsapp.enviados == [("adv_c1", RESPOSTA_FALHA_DE_REDACAO)], (
        "o texto honesto nao tem por que ser trocado: ele bate com o fato"
    )
    assert saida.get("error") is None

    # CAMADA 2: a promessa antiga, plantada como rascunho, continua sendo barrada.
    whatsapp_backstop = _WhatsApp()
    saida_backstop = await _graph(whatsapp=whatsapp_backstop).respond(
        _estado(response_text=FALLBACK_DE_TRANSPORTE, response_kind="inform")
    )

    assert whatsapp_backstop.enviados[0][1] == RESPOSTA_SEM_ENCAMINHAMENTO, (
        "a promessa de humano do fallback antigo chegou ao beneficiario com ZERO processos "
        f"abertos (desfecho={saida_backstop.get('desfecho')!r})"
    )
    assert saida_backstop.get("error") == ERRO_PROMESSA_SEM_START


def test_bug_texto_que_a_entrega_reconhece_como_handoff_nao_e_recusado_sem_start() -> None:
    """REPROVA — a classe inteira, e a contradicao interna que a torna objetiva.

    `MENCAO_DE_ENCAMINHAMENTO_OBRIGATORIA` (13 padroes, lista POSITIVA) e
    `PROMESSA_DE_HUMANO_PROIBIDA` (22 padroes, lista NEGATIVA) foram escritas para lados opostos
    do mesmo fato, e nao sao complementares: a primeira e' larga (casa "um profissional",
    "atendente", "equipe de saude", "enfermagem", "plantao clinico") e a segunda e' estreita e
    colada nas frases do `C1` ("entrara em contato", "aguarde nosso contato").

    O resultado e' um conjunto de textos em que a Helena AVISA que um humano assumiu (pela
    definicao da propria entrega) sem que nenhum humano tenha assumido — e a cerca nao ve. A
    entrega declara falso NEGATIVO aceitavel na lista POSITIVA (trocar o rascunho pela constante
    honesta); aqui o falso negativo esta' na lista NEGATIVA, onde o custo e' o oposto: o texto
    mentiroso SAI.

    Nota de escopo: `escalate`/`schedule` sempre passam por `_start_escalation` (que grava o
    desfecho), entao a classe alcancavel e' `inform`/`collect` — as duas rotas em que o
    `response_prompt` PEDE ao modelo para nao prometer. Pedir e' instrucao; esta lista e' a cerca.
    """
    escapam = [
        texto
        for texto in RASCUNHOS_QUE_ANUNCIAM_HUMANO
        if menciona_encaminhamento(texto) and motivo_de_recusa(texto, "inform", start_aconteceu=False) is None
    ]

    assert not escapam, (
        "textos que `menciona_encaminhamento` aprova como aviso de handoff e que a cerca NAO "
        "recusa com `start_aconteceu=False` (prompts.py:527 `PROMESSA_DE_HUMANO_PROIBIDA`):\n  "
        + "\n  ".join(repr(t) for t in escapam)
    )


# =================================================================================================
# 2. O que a cerca FAZ bem — matriz completa desfecho x familia de texto
# =================================================================================================

_TODOS_OS_DESFECHOS: tuple[str, ...] = (
    START_DESFECHO_NAO_TENTADO,
    START_DESFECHO_NOVO,
    START_DESFECHO_JA_ATIVO,
    START_DESFECHO_JA_CONCLUIDO,
    START_DESFECHO_NAO_REPORTADO,
    START_DESFECHO_FALHOU,
)

#: A promessa do `C1`, reduzida ao minimo que a cerca reconhece.
PROMESSA_C1 = "Um profissional de saude entrara em contato com voce em breve. Aguarde nosso contato."
#: Texto administrativo honesto, sem promessa e sem mencao (o `E4` limpo de canais inventados).
INFORM_HONESTO = "Este canal pode te orientar sobre onde consultar o valor da mensalidade."


@pytest.mark.parametrize("desfecho", _TODOS_OS_DESFECHOS)
async def test_a_promessa_do_c1_nunca_sai_em_desfecho_sem_humano(desfecho: str) -> None:
    """Os SEIS desfechos, um a um, com o texto do C1. So' `novo` e `ja_ativo` tem humano; em todos
    os outros quatro a promessa tem de ser trocada.

    `ja_concluido` e `nao_reportado` sao declarados inalcancaveis hoje pelo proprio `graph.py`, e
    e' justamente por isso que eles entram: um desfecho que ninguem exercita e' onde um `in`
    esquecido sobrevive ate' o dia em que a postura de dedup muda.
    """
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            response_text=PROMESSA_C1,
            response_kind="escalate",
            start_desfecho=desfecho,
            start_failed=desfecho == START_DESFECHO_FALHOU,
        )
    )

    enviado = whatsapp.enviados[0][1]
    if desfecho == START_DESFECHO_NOVO:
        assert enviado == PROMESSA_C1, "com start novo a promessa e' verdadeira e obrigatoria"
    elif desfecho == START_DESFECHO_JA_ATIVO:
        assert enviado == RESPOSTA_HANDOFF_JA_ABERTO
    elif desfecho == START_DESFECHO_FALHOU:
        assert enviado == RESPOSTA_FALHA_TECNICA_START
    else:
        assert enviado == RESPOSTA_SEM_ENCAMINHAMENTO, (
            f"desfecho {desfecho!r} nao tem humano e a promessa saiu de qualquer jeito"
        )
        assert saida.get("error") == ERRO_PROMESSA_SEM_START
    assert "Aguarde nosso contato" not in enviado or desfecho == START_DESFECHO_NOVO


@pytest.mark.parametrize("desfecho", (START_DESFECHO_NOVO, START_DESFECHO_JA_ATIVO))
async def test_com_humano_o_texto_sem_mencao_nunca_sai(desfecho: str) -> None:
    """O avesso (F2), nos dois desfechos que tem humano: um texto informativo puro nunca sai SO'.

    O QUE MUDOU EM 21/09/2026 (segunda rodada), no ramo `ja_ativo`. Este teste cobrava igualdade
    com a constante nos dois desfechos, e no `ja_ativo` essa igualdade era o proprio defeito:
    descartar o rascunho inteiro jogava fora a `memoria_a_confirmar` (a pergunta que o
    `response_prompt` OBRIGA antes de usar um dado LEMBRADO, e que existe para a pessoa poder
    corrigir a populacao que decidiu a tabela) junto com a orientacao do turno. So' o anuncio de um
    handoff NOVO e' falso num `ja_ativo`; um texto que nao anuncia nada nao precisa desaparecer.

    O invariante cobrado continua o mesmo e nao afrouxou: o texto que SAI menciona o encaminhamento,
    e o beneficiario nunca le' um informativo puro num turno em que um humano esta' com o caso. No
    `ja_ativo` a constante entra como PREFIXO — e o teste cobra tambem que a orientacao sobreviveu,
    que e' a metade que nao existia antes.
    """
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(response_text=INFORM_HONESTO, response_kind="escalate", start_desfecho=desfecho)
    )

    enviado = whatsapp.enviados[0][1]
    assert menciona_encaminhamento(enviado) is True
    assert saida["response_text"] == enviado
    if desfecho == START_DESFECHO_JA_ATIVO:
        assert enviado.startswith(RESPOSTA_HANDOFF_JA_ABERTO)
        assert INFORM_HONESTO in enviado, "a orientacao do turno nao tinha nada de errado"
    else:
        assert enviado == RESPOSTA_HANDOFF_RECUSADA


async def test_o_ja_ativo_ganha_de_um_texto_que_ja_menciona_corretamente() -> None:
    """PRECEDENCIA: o ramo `ja_ativo` troca INCONDICIONALMENTE, mesmo quando o rascunho passaria
    pelas outras duas verificacoes.

    E' o que distingue "o modelo errou a frase" de "a frase certa depende de um fato que o modelo
    nao tinha": um texto que anuncia um encaminhamento NOVO e' falso quando nada foi aberto, ainda
    que ele mencione o encaminhamento e nao contenha padrao proibido nenhum.
    """
    anuncio_novo = RESPOSTA_HANDOFF_RECUSADA
    assert menciona_encaminhamento(anuncio_novo) is True
    assert motivo_de_recusa(anuncio_novo, "escalate", start_aconteceu=True) is None

    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            response_text=anuncio_novo,
            response_kind="escalate",
            start_desfecho=START_DESFECHO_JA_ATIVO,
        )
    )

    assert whatsapp.enviados[0][1] == RESPOSTA_HANDOFF_JA_ABERTO
    assert saida["response_text"] == RESPOSTA_HANDOFF_JA_ABERTO


@pytest.mark.parametrize(
    "estado_torto",
    [
        {},
        {"start_desfecho": None},
        {"start_desfecho": ""},
        {"start_desfecho": "Novo"},
        {"start_desfecho": "NOVO"},
        {"start_desfecho": " novo"},
        {"start_desfecho": "novo,ja_ativo"},
        {"start_desfecho": True},
        {"start_desfecho": ["novo"]},
        {"escalation_started": True, "escalation_process_ref": {"instance_id": "x"}},
    ],
)
def test_humano_acionado_e_fail_closed_para_qualquer_valor_que_nao_seja_o_token(
    estado_torto: dict[str, Any],
) -> None:
    """FAIL-CLOSED por OMISSAO, com valores tortos de verdade (caixa, espaco, lista, bool).

    `["novo"]` importa: `str(["novo"])` e' `"['novo']"`, que NAO esta' na allowlist — mas um
    `in` sobre a lista, ou um `any(d in str(...))`, diria sim. E o ultimo caso e' o sinal que o
    `C1` tinha LIGADO (`escalation_started=True` com uma instancia que nao era desta conversa):
    ele nunca pode voltar a valer como prova de humano.
    """
    assert _humano_acionado(cast(Any, estado_torto)) is False


async def test_texto_vazio_continua_sendo_hel_07_e_nao_ganha_frase_plausivel() -> None:
    """A cerca nao pode esconder um "nada foi enviado" atras de uma constante simpatica — mesmo
    quando o fato (start novo) tornaria a substituicao 'obviamente certa'."""
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(response_text="   \n  ", response_kind="escalate", start_desfecho=START_DESFECHO_NOVO)
    )

    assert whatsapp.enviados == []
    assert saida["desfecho"] == "resposta_vazia_nao_enviada"
    assert "profissional" not in str(saida.get("response_text", ""))


# =================================================================================================
# 3. O falso NEGATIVO declarado da lista positiva: aceitavel SE a substituta for segura
# =================================================================================================

#: Textos que AVISAM que um humano assumiu com palavras fora de
#: `MENCAO_DE_ENCAMINHAMENTO_OBRIGATORIA`. A entrega declara que trocar estes pela constante e'
#: aceitavel ("a pessoa le' uma frase mais seca, e nada mais"). Este bloco cobra o preco dessa
#: declaracao: a constante substituta tem de ser segura em TODAS as outras cercas.
MENCOES_COM_OUTRAS_PALAVRAS: tuple[str, ...] = (
    "Seu caso foi passado para a nossa enfermeira de plantao.",
    "Uma pessoa da nossa central clinica vai te chamar.",
    "Ja avisei o medico responsavel pelo seu caso.",
    "Vou pedir para alguem do time falar com voce.",
)


@pytest.mark.parametrize("texto", MENCOES_COM_OUTRAS_PALAVRAS)
async def test_mencao_com_palavras_fora_da_lista_e_trocada_pela_constante(texto: str) -> None:
    """O falso negativo ACONTECE (a lista e' curta de proposito) — e o efeito e' a troca, nunca
    um texto mentiroso. Provado no no' real, nao so' na funcao pura."""
    assert menciona_encaminhamento(texto) is False, (
        "se a lista passar a casar esta frase, este teste perdeu o objeto e deve ser reescrito"
    )
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(response_text=texto, response_kind="escalate", start_desfecho=START_DESFECHO_NOVO)
    )

    assert whatsapp.enviados[0][1] == RESPOSTA_HANDOFF_RECUSADA
    assert saida["response_text"] == RESPOSTA_HANDOFF_RECUSADA


@pytest.mark.parametrize(
    "constante",
    [
        RESPOSTA_HANDOFF_RECUSADA,
        RESPOSTA_HANDOFF_JA_ABERTO,
        RESPOSTA_SEM_ENCAMINHAMENTO,
        RESPOSTA_FALHA_TECNICA_START,
        RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
        RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    ],
)
def test_as_constantes_substitutas_passam_tambem_na_cerca_de_canal(constante: str) -> None:
    """A cerca de CANAL (F7) nao e' chamada em `respond` — so' em `_respond_llm`. Entao um canal
    inventado dentro de uma CONSTANTE substituta sairia sem ninguem olhar.

    `test_helena_cerca_texto_x_fato.py` cobra as constantes contra `motivo_de_recusa` e contra a
    regua de clareza; a quarta categoria (canal nao confirmado, criada na MESMA entrega) ficou de
    fora. Vale tambem para as duas frases fixas de conformidade, que vao LITERALMENTE ao prompt e
    portanto ao beneficiario.
    """
    assert motivo_de_canal_nao_confirmado(constante) is None


def test_os_tres_grupos_de_troca_sao_distintos_entre_si() -> None:
    """Os rotulos viram label de metrica: fundir dois apaga a distincao que a bateria criou."""
    grupos = (RECUSA_PROMESSA_SEM_START, RECUSA_HANDOFF_SEM_MENCAO, RECUSA_ESCALONAMENTO_JA_ABERTO)
    assert len(set(grupos)) == 3
