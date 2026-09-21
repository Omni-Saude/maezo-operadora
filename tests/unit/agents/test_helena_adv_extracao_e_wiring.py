"""ADVERSARIAL (21/09/2026) — o resto da entrega: `apresentacao_ja_feita`, as versoes, o
vocabulario da telemetria, a fronteira da extracao e o corpus `extracao-v2`.

Quatro perguntas adversariais, uma por bloco:

  1. `apresentacao_ja_feita` e' um bool que so' anda para True. Um valor TRUTHY nao-bool ("true",
     1) atravessa `receive`? Um envio que FALHOU acende o sinal?
  2. as versoes: o mapa `PROMPT_VERSIONS` cobre todo artefato versionado do modulo, ou so' os que
     alguem lembrou de registrar?
  3. o `route` da telemetria: o vocabulario fechado tem as QUATRO rotas do grafo?
  4. o corpus: o par minimo/maximo de cada codigo qualificado e' um par de VERDADE — o caso
     minimo nao pode conter a palavra que o codigo exige, e o rotulo dele tem de ser o `sem_ele`
     declarado, nao "qualquer coisa que nao seja o codigo".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, cast, get_args

import pytest

from maezo.agents.helena import prompts as helena_prompts
from maezo.agents.helena.graph import (
    PROMPT_VERSIONS,
    RESPOSTA_FALHA_TECNICA_START,
    START_DESFECHO_FALHOU,
    START_DESFECHO_JA_ATIVO,
    START_DESFECHO_NOVO,
    HelenaGraph,
    HelenaState,
    ResponseKindOut,
)
from maezo.agents.helena.prompts import QUALIFICADORES_OBRIGATORIOS, SINTOMA_CODIGOS_BY_POPULATION
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.turn_telemetry import _ROUTE_VOCAB
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_CORPUS: Final[Path] = Path(__file__).resolve().parents[3] / "tests" / "evals" / "extracao" / "casos.json"


class _InferenciaFixa:
    def __init__(self, texto: str = "Recebemos sua mensagem.") -> None:
        self.texto = texto
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        return self.texto


class _WhatsApp:
    def __init__(self, *, falha: bool = False) -> None:
        self.enviados: list[tuple[str, str]] = []
        self.falha = falha

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        if self.falha:
            raise RuntimeError("transporte do whatsapp fora do ar (duplo de teste)")
        self.enviados.append((to_hash, text))
        return {"ok": True}


def _graph(texto: str = "Recebemos sua mensagem.", *, whatsapp: _WhatsApp | None = None) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, _InferenciaFixa(texto)),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=cast(Any, whatsapp or _WhatsApp()),
    )


def _estado(**extra: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:adv_wiring",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-ADV",
        "message_body": "oi, tudo bem?",
    }
    s.update(extra)  # type: ignore[typeddict-item]
    return s


# =================================================================================================
# 1. `apresentacao_ja_feita`: um bool, e so' um bool
# =================================================================================================


@pytest.mark.parametrize("plantado", ["true", "True", "sim", 1, 2, -1, 0.5, [True], {"v": True}, object()])
async def test_valor_truthy_nao_bool_nao_e_preservado_por_receive(plantado: Any) -> None:
    """`receive` preserva o sinal com `is True`, nao com verdade/falsidade — e a diferenca tem
    consequencia visivel: um `"true"` preservado PROIBIRIA a apresentacao no primeiro turno da
    conversa, que e' exatamente o turno em que ela deve aparecer.

    E' tambem a defesa T1.11 deste campo: quem chama nao pode plantar um valor que muda o texto.
    """
    graph = _graph()

    reset = await graph.receive(_estado(apresentacao_ja_feita=plantado))

    assert reset["apresentacao_ja_feita"] is False


async def test_o_bool_verdadeiro_e_preservado_e_a_ausencia_vira_false() -> None:
    """O RED do teste acima: sem isto, um campo constante `False` passaria os dois."""
    graph = _graph()

    assert (await graph.receive(_estado(apresentacao_ja_feita=True)))["apresentacao_ja_feita"] is True
    assert (await graph.receive(_estado()))["apresentacao_ja_feita"] is False


async def test_um_envio_que_falhou_nao_acende_a_apresentacao() -> None:
    """O sinal existe para dizer "a pessoa JA LEU o cartao". Um envio que falhou nao foi lido por
    ninguem — acender ali faria a Helena nunca mais se apresentar naquela conversa, por causa de
    uma indisponibilidade de transporte."""
    whatsapp = _WhatsApp(falha=True)
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(response_text="Sou Helena, navegadora de saude.", response_kind="inform")
    )

    assert whatsapp.enviados == []
    assert "whatsapp send failed" in str(saida.get("error"))
    assert "apresentacao_ja_feita" not in saida


@pytest.mark.parametrize("desfecho", (START_DESFECHO_NOVO, START_DESFECHO_JA_ATIVO))
async def test_o_envio_de_um_texto_sem_cartao_nao_acende_a_apresentacao(desfecho: str) -> None:
    """O QUE MUDOU EM 21/09/2026 (segunda rodada), e por que a versao anterior estava ERRADA.

    Este teste afirmava o contrario — "inclusive nos turnos em que o texto foi TROCADO pela cerca:
    a pessoa leu uma mensagem desta conversa, e o cartao nao se repete por causa de uma
    substituicao" —, e o raciocinio confunde "leu uma mensagem" com "leu o CARTAO". Sao coisas
    diferentes, e a diferenca tem consequencia visivel:

      * o sinal significa "a pessoa JA LEU a apresentacao", e o efeito de liga-lo e' PROIBIR a
        apresentacao no prompt pelo RESTO da conversa (`response_prompt`);
      * nos dois desfechos deste teste o texto que sai e' uma CONSTANTE da cerca TEXTO x FATO
        ("Recebemos sua mensagem. Seu atendimento (...) ja esta aberto" / "... encaminhamos seu
        caso ..."), e nenhuma delas tem cartao nenhum;
      * logo, acender o sinal ali fazia a Helena NUNCA MAIS se apresentar naquela conversa — por
        causa de um turno em que ela nao se apresentou. E' o oposto do F6, que existe para ela nao
        REPETIR o cartao.

    O invariante que substitui: o sinal acende quando o texto ENVIADO contem a apresentacao
    (`MARCAS_DE_APRESENTACAO`), e nao quando o envio deu certo. Falso negativo aqui repete o cartao
    uma vez; falso positivo cala a agente para sempre.
    """
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(response_text="Recebemos sua mensagem.", response_kind="escalate", start_desfecho=desfecho)
    )

    assert whatsapp.enviados, "premissa: o turno envia alguma coisa"
    assert "sou helena" not in whatsapp.enviados[0][1].lower(), "premissa: o texto nao tem cartao"
    assert "apresentacao_ja_feita" not in saida


_CARTAO = "Sou Helena, navegadora de saude deste canal."


@pytest.mark.parametrize(
    ("rotulo", "estado"),
    [
        (
            "inform sem start: nada e' trocado",
            {
                "response_text": f"{_CARTAO} Pode me contar o que houve?",
                "response_kind": "inform",
            },
        ),
        (
            "escalate com start novo: o rascunho MENCIONA, entao sai intacto",
            {
                "response_text": f"{_CARTAO} Um profissional vai dar continuidade ao seu atendimento.",
                "response_kind": "escalate",
                "start_desfecho": START_DESFECHO_NOVO,
            },
        ),
        (
            "ja_ativo: a constante entra como PREFIXO e o cartao segue atras",
            {
                "response_text": f"{_CARTAO} Pode me contar o que houve?",
                "response_kind": "escalate",
                "start_desfecho": START_DESFECHO_JA_ATIVO,
            },
        ),
    ],
)
async def test_o_envio_do_cartao_acende_a_apresentacao(rotulo: str, estado: dict[str, Any]) -> None:
    """O RED do teste acima: o sinal PRECISA acender quando o cartao sai de verdade.

    Sem esta metade, "nunca acender" passaria o teste de cima e o F6 deixaria de existir — a
    Helena repetiria a apresentacao em todo turno, que e' o defeito medido em `A1`/`A2`/`A3`.

    OS TRES CASOS SAO OS TRES CAMINHOS em que o cartao SOBREVIVE a cerca TEXTO x FATO, e a escolha
    de cada um e' o ponto: num `escalate` com start novo, um rascunho que nao mencionasse o
    encaminhamento seria trocado INTEIRO pela constante (e o cartao morreria com ele, corretamente
    — o fato tem precedencia sobre a apresentacao); no `ja_ativo` a constante entra como PREFIXO e
    o rascunho segue atras, entao o cartao continua no texto enviado.
    """
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(_estado(**estado))

    assert _CARTAO in whatsapp.enviados[0][1], rotulo
    assert saida["apresentacao_ja_feita"] is True, rotulo


async def test_o_turno_de_falha_de_start_envia_e_nao_acende_a_apresentacao() -> None:
    """RESIDUAL DIVULGADO, medido em vez de suposto: o ramo `start_failed=True` envia a constante
    de falha tecnica e sai ANTES do bloco que acende o sinal. Ou seja, um turno que o beneficiario
    leu nao conta como "ja se apresentou", e o cartao pode se repetir no turno seguinte.

    Consequencia pequena, registrada aqui porque o F6 e' sobre exatamente esta repeticao — e
    porque um teste e' o unico lugar em que isto fica visivel.
    """
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            response_text="qualquer rascunho",
            response_kind="escalate",
            start_failed=True,
            start_desfecho=START_DESFECHO_FALHOU,
        )
    )

    assert whatsapp.enviados[0][1] == RESPOSTA_FALHA_TECNICA_START
    assert "apresentacao_ja_feita" not in saida


# =================================================================================================
# 2. As versoes: todo artefato versionado do modulo esta' no mapa
# =================================================================================================


def test_todo_version_do_modulo_de_prompts_esta_em_prompt_versions() -> None:
    """`PROMPT_VERSIONS` e' o que o audit/eval le' para dizer QUAL texto falou com o beneficiario.

    `test_helena_dono_declarado.py` prova que o mapa bate com o `agent.yaml` — as duas COPIAS
    concordam. Este teste ataca o outro lado: um artefato versionado NOVO em `prompts.py` que
    ninguem registrou no mapa deixaria as duas copias concordando sobre um conjunto incompleto, e
    a cerca ficaria verde sobre a omissao.

    A descoberta e' por VARREDURA do modulo (todo nome que termina em `_VERSION`), e nao por lista
    — e' a direcao que pega o artefato novo, que e' a que apodrece em silencio.
    """
    do_modulo = {
        nome: valor
        for nome, valor in vars(helena_prompts).items()
        if nome.endswith("_VERSION") and isinstance(valor, str)
    }
    assert do_modulo, "a varredura nao achou nenhum `*_VERSION` — o teste perdeu o objeto"

    registradas = set(PROMPT_VERSIONS.values())
    faltando = {nome: valor for nome, valor in do_modulo.items() if valor not in registradas}

    assert not faltando, (
        f"artefato(s) versionado(s) de `prompts.py` fora de `PROMPT_VERSIONS`: {faltando}. "
        "Sem a entrada, um texto barrado (ou entregue) nao tem registro de QUAL versao falou."
    )


# =================================================================================================
# 3. O `route` da telemetria
# =================================================================================================


def test_bug_a_rota_collect_nao_esta_no_vocabulario_de_route_da_telemetria() -> None:
    """REPROVA — bug real (pre-existente, e na familia que ESTA entrega editou).

    `_ROUTE_VOCAB["helena"]` (`runtime/turn_telemetry.py:255`) declara em comentario que o call
    site passa `response_kind` (`ResponseKindOut`) naquele campo — e o conjunto tem QUATRO dos
    CINCO valores do `Literal`: falta `collect`. Todo turno de coleta cai em `_normalize` ->
    `"outro"` e emite `agent_desfecho_label_out_of_vocab` (visivel no log capturado de qualquer
    teste que rode o no' `collect`).

    E' a MESMA classe de defeito que esta entrega consertou um campo ao lado: `desfecho` ganhou
    `escalonamento_ja_aberto` no vocabulario porque "um valor fora do vocabulario e' normalizado
    para 'outro', o que apagaria exatamente a distincao que este achado cria". O `route` da coleta
    esta' apagado hoje — a rota que PERGUNTA fica indistinguivel de qualquer valor desconhecido.

    Conserto: `"collect"` entra no frozenset. O teste compara com o `Literal`, nao com uma lista,
    para o proximo `ResponseKind` novo nao repetir a omissao.
    """
    do_literal = set(get_args(ResponseKindOut))

    faltando = do_literal - _ROUTE_VOCAB["helena"]

    assert not faltando, (
        f"valores de `ResponseKindOut` fora de `_ROUTE_VOCAB['helena']`: {sorted(faltando)} "
        "(o label vira 'outro' e o turno emite `agent_desfecho_label_out_of_vocab`)"
    )


# =================================================================================================
# 4. A fronteira da extracao: a postura declarada e' fail-closed para ESCALATE, nao para o vazio
# =================================================================================================


async def test_bug_classify_morre_alto_quando_o_modelo_devolve_lista_num_campo_fechado() -> None:
    """REPROVA — bug real: a postura declarada de `classify` nao se sustenta para um JSON valido
    de forma inesperada.

    `classify` declara:

        "FAIL-CLOSED on classifier failure (R1 cycle-1 blocking fix): an LLM exception,
         unparseable JSON, or schema-invalid JSON is gatilho 4 (`falha_tecnica`) -> escalate,
         NEVER a silent `inform` default"

    Um modelo que responde `"sintoma_codigo": ["febre", "dispneia"]` para quem relatou DOIS
    sintomas ("estou com febre e falta de ar") produz JSON parseavel e schema-invalido — e em vez
    de gatilho 4, `_validate_extraction` levanta `TypeError: unhashable type: 'list'` no
    `not in ALLOWED_SINTOMA_CODIGOS`. `TypeError` esta' em `PROGRAMMING_ERRORS` e PROPAGA: o no'
    morre, o beneficiario nao recebe nada, nenhuma escalacao nasce e nenhum desfecho e' contado.

    E' a mesma silence-class do F1 pelo lado da entrada, e o conserto e' a guarda que os campos de
    IDADE do mesmo validador ja' fazem (`_coerce_age` devolve `(False, None)` para lista/dict).
    """
    payload = json.dumps(
        {
            "intent": "symptom",
            "population": "adult",
            "psychosocial_risk": False,
            "sintoma_codigo": ["febre", "dispneia"],
            "intensidade": "moderada",
            "idade_anos": 30,
            "idade_meses": None,
            "idade_gestacional_semanas": None,
        }
    )
    graph = _graph(payload)

    saida = await graph.classify(_estado(message_body="estou com febre e falta de ar"))

    assert saida.get("next_kind") == "escalate", (
        f"esperado gatilho 4 (falha_tecnica -> escalate); saida={saida!r}"
    )
    assert "schema" in str(saida.get("error", "")) or "classify" in str(saida.get("error", ""))


# =================================================================================================
# 5. O corpus `extracao-v2`: o par minimo/maximo e' um par de verdade
# =================================================================================================


def _casos() -> list[dict[str, Any]]:
    carregado = json.loads(_CORPUS.read_text(encoding="utf-8"))
    return list(carregado["casos"])


def _populacao_do_codigo(codigo: str) -> str:
    for populacao, codigos in SINTOMA_CODIGOS_BY_POPULATION.items():
        if codigo in codigos:
            return populacao
    raise AssertionError(f"{codigo} nao pertence a populacao nenhuma da allowlist")


@pytest.mark.parametrize("codigo", sorted(QUALIFICADORES_OBRIGATORIOS))
def test_o_caso_minimo_nao_contem_a_palavra_que_o_codigo_exige(codigo: str) -> None:
    """O par minimo/maximo so' mede a regra do qualificador se o MINIMO for minimo de verdade.

    `test_corpus_de_extracao.py` cobra a EXISTENCIA dos dois lados; aqui se cobra o CONTEUDO do
    lado negativo: se a mensagem "minima" carregasse o token do qualificador ("subita",
    "intensa", "regulares"), o rotulo `null` estaria ensinando a extracao a ignorar o
    qualificador que a pessoa disse — que e' o falso negativo numa emergencia real, o erro que a
    propria declaracao diz ser pior que o medido.
    """
    qualificador = QUALIFICADORES_OBRIGATORIOS[codigo]
    minimos = [c for c in _casos() if codigo in c.get("nao_pode_casar", [])]
    assert minimos, f"{codigo} nao tem caso negativo (a cerca da existencia vive no outro arquivo)"

    problemas = [
        f"{c['id']}: mensagem contem o token {token!r}"
        for c in minimos
        for token in qualificador.tokens
        if token in c["mensagem"].lower()
    ]

    assert not problemas, "\n  ".join(problemas)


@pytest.mark.parametrize("codigo", sorted(QUALIFICADORES_OBRIGATORIOS))
def test_o_caso_minimo_e_rotulado_com_o_sem_ele_declarado(codigo: str) -> None:
    """O negativo nao pode ser "qualquer coisa menos o codigo": a declaracao diz O QUE a extracao
    deve devolver sem o qualificador (`sem_ele`), e e' isso que o corpus tem de cobrar.

    Hoje os cinco declaram `"null"`; no dia em que um declarar o codigo GENERICO da mesma
    populacao, um corpus rotulado com `null` passaria a medir a coisa errada em silencio.
    """
    esperado_declarado = QUALIFICADORES_OBRIGATORIOS[codigo].sem_ele
    esperado = None if esperado_declarado == "null" else esperado_declarado

    for caso in [c for c in _casos() if codigo in c.get("nao_pode_casar", [])]:
        assert caso["esperado"]["sintoma_codigo"] == esperado, (
            f"{caso['id']}: rotulado {caso['esperado']['sintoma_codigo']!r}, e a declaracao de "
            f"{codigo} diz {esperado_declarado!r}"
        )


@pytest.mark.parametrize("codigo", sorted(QUALIFICADORES_OBRIGATORIOS))
def test_o_par_minimo_e_maximo_vive_na_mesma_populacao_do_codigo(codigo: str) -> None:
    """Um negativo de OUTRA populacao nao e' um quase-acerto: ele nunca poderia casar aquele
    codigo (a allowlist e' por populacao), entao ele nao refuta nada. O par tem de ser a MESMA
    pessoa com e sem a palavra."""
    populacao = _populacao_do_codigo(codigo)
    casos = _casos()

    positivos = [c for c in casos if c["esperado"]["sintoma_codigo"] == codigo]
    negativos = [c for c in casos if codigo in c.get("nao_pode_casar", [])]
    assert positivos and negativos

    for caso in positivos + negativos:
        assert caso["esperado"]["population"] == populacao, (
            f"{caso['id']}: population={caso['esperado']['population']!r}, e {codigo} e' da "
            f"allowlist de {populacao!r}"
        )
