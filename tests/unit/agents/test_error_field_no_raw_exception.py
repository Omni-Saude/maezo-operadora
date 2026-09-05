"""LUC-06 / NEW-01 / CC-10 (metade `error`) — nenhum campo de estado carrega excecao CRUA.

## O vazamento que esta cerca fecha

CC-10 fechou UMA forma do vazamento (`notes.append(f"...: {exc}")`, o coletor de lacunas do no
`gather`) e deixou a outra intacta: os DOIS produtores do campo `error` de grafo.

1. **Falha de start** — os nove nos `start_process` faziam
   `start_failed_state(business_key=..., error=f"start_process indisponivel: {exc}")`. O `str()`
   de um `CibSevenError` e' texto ARBITRARIO de outro sistema e ja' provou ecoar identificador
   (CC-06/HEL-05): a auditoria reproduziu vivo
   `error == 'start_process indisponivel: engine unreachable at http://internal/cases/CPF-123.456.789-09'`
   em 7 dos 9 agentes (LUC-06).
2. **DMN indisponivel** — os sete `_evaluate_dmn` faziam
   ``return {"error": f"DMN `{table}` indisponivel: {exc}"}``, e `LucasGraph.assess` o equivalente
   em `dmn_error=`. Esse texto vira `state["dmn_error"]`, que e' estado CHECKPOINTADO.

Nenhum dos dois campos vira variavel de processo — mas ambos sobrevivem no checkpoint do grafo
(T4b, `platform/webhooks/whatsapp/dispatch.py` compila COM saver duravel), e `state["error"]` e'
lido num turno POSTERIOR ao que o escreveu (`helena/graph.py::_start_escalation` o costura no
sufixo `[falha tecnica: ...]`). A postura correta e' a que helena ja praticava sozinha: TOKEN DE
CLASSE + texto redigido por `tools/workers/phi_vars.py::redact_error_message`, agora com UMA
definicao em `runtime/error_text.py` em vez de N copias.

## As tres provas deste arquivo

- `test_no_agent_graph_renders_a_raw_exception` — cerca ESTRUTURAL (AST) com politica de NEGACAO
  POR PADRAO: dentro de um `except ... as <nome>`, TODA leitura de `<nome>` e' uma violacao a
  menos que seja uma das formas seguras enumeradas em `_SAFE_CALLEES`/`_is_safe_use`. Ao contrario
  de uma cerca por FORMA DE SINK (a de CC-10 so' via `<nome terminado em notes|lacunas>.append`),
  esta nao tem ponto cego: `.extend`, `+=`, `insert`, receptor `self.x`/`d["k"]`, `return
  {"error": ...}` e `f(error=...)` caem todos do mesmo lado.
- `test_start_failure_error_field_never_carries_the_exception_text` — prova COMPORTAMENTAL, uma
  parametrizacao por agente REAL que inicia processo, derivada do inventario AST (nao de uma
  lista de quatro escrita a mao).
- `test_dmn_unavailable_error_never_carries_the_exception_text` (+ o irmao de Lucas) — idem para
  a familia DMN, sobre todo grafo que a define.

O CPF sintetico e' o mesmo de CC-10: digitos verificadores validos, nao pertence a ninguem.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.andre.graph import ERROR_START_PROCESS_ENGINE_UNAVAILABLE
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from maezo.tools.workers.phi_vars import _ERROR_MESSAGE_MAX_CHARS, _TRUNCATION_MARKER
from tests.support.audit_fakes import FakeStartAuditSink

# A tabela de casos da cerca IRMA de CC-01 e' a fonte unica dos estados minimos por agente
# (`test_start_failure_routing.py` ja' prova, com uma cerca AST propria, que ela cobre exatamente
# os agentes que tem no `start_process`). Reusar em vez de duplicar nove estados.
from tests.unit.agents.test_start_failure_routing import (
    _AGENT_IDS,
    _CASES,
    _agent_graph_paths,
    _FakeDmn,
    _FakeInference,
    _graph_class,
)

#: CPF sintetico (nao pertence a ninguem; verificadores validos, logo indistinguivel de um real
#: para qualquer scanner de PHI — exatamente o ponto).
_CPF_SINTETICO = "123.456.789-09"

#: O marcador que `phi_vars.redact_free_text` deixa no lugar do identificador. Asserir a PRESENCA
#: dele e' o que distingue "o texto passou pela rede e foi redigido" de "o texto nunca chegou"
#: (uma asserceao so' de ausencia do CPF ficaria verde tambem se o campo virasse `""`).
_MARCADOR_REDACAO = "[REDACTED_DIGITS]"


# =================================================================================================
# (A) Cerca ESTRUTURAL — negacao por padrao sobre `src/maezo/agents/**/*.py`
# =================================================================================================

#: Funcoes que consomem o OBJETO da excecao e devolvem algo seguro. `redact_error_message` e
#: `redact_free_text` sao a rede de identificadores (CC-06); `classify_agent_error_type` devolve um
#: token do vocabulario FECHADO de `error_type`; `start_unavailable_error`/`dmn_unavailable_error`
#: sao os dois construtores de `runtime/error_text.py`, que delegam a `redact_error_message`.
_SAFE_CALLEES = frozenset(
    {
        "redact_error_message",
        "redact_free_text",
        "classify_agent_error_type",
        "start_unavailable_error",
        "dmn_unavailable_error",
    }
)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    mapa: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for filho in ast.iter_child_nodes(node):
            mapa[filho] = node
    return mapa


def _is_safe_use(node: ast.Name, pais: dict[ast.AST, ast.AST]) -> bool:
    """A leitura do nome da excecao em `node` esta numa das formas seguras enumeradas?"""
    pai = pais.get(node)

    # `type(exc).__name__` — token de classe.
    if (
        isinstance(pai, ast.Call)
        and isinstance(pai.func, ast.Name)
        and pai.func.id == "type"
        and any(arg is node for arg in pai.args)
    ):
        avo = pais.get(pai)
        return isinstance(avo, ast.Attribute) and avo.attr == "__name__"

    # `<callee_seguro>(..., exc, ...)` — o OBJETO entra; quem redige e' o callee.
    if isinstance(pai, ast.Call) and isinstance(pai.func, ast.Name) and pai.func.id in _SAFE_CALLEES:
        return any(arg is node for arg in pai.args) or any(kw.value is node for kw in pai.keywords)

    # `raise ... from exc` — encadeamento de excecao, nao renderizacao de texto.
    if isinstance(pai, ast.Raise) and pai.cause is node:
        return True

    # `logger.x(..., exc_info=exc)` — o objeto vai para o LOG estruturado, canal de diagnostico.
    return isinstance(pai, ast.keyword) and pai.arg == "exc_info"


def offending_sites(source: str, path: Path) -> list[str]:
    """Toda leitura do nome ligado por `except ... as <nome>` que NAO esta numa forma segura.

    Politica de NEGACAO POR PADRAO — e' o que remove os pontos cegos da cerca de CC-10, que
    enumerava as formas PROIBIDAS de sink (e portanto so' via as que alguem lembrou de escrever).
    """
    tree = ast.parse(source, filename=str(path))
    pais = _parents(tree)
    offenders: list[str] = []
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
        nome = handler.name
        if not nome:
            continue  # `except Exception:` sem binding — nada a interpolar (padrao da Beatriz).
        for node in ast.walk(handler):
            if not (isinstance(node, ast.Name) and node.id == nome):
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            if _is_safe_use(node, pais):
                continue
            pai = pais.get(node, node)
            offenders.append(
                f"{path}:{node.lineno} — `{ast.unparse(pai).strip()[:80]}` "
                f"(excecao ligada como `{nome}`) renderiza o texto CRU"
            )
    return offenders


def _agent_source_paths() -> list[Path]:
    raiz = _agent_graph_paths()[0].parent.parent
    return sorted(p for p in raiz.rglob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("path", _agent_source_paths(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_agent_graph_renders_a_raw_exception(path: Path) -> None:
    """LUC-06/NEW-01: nenhum modulo de agente renderiza `str(exc)` num campo de estado.

    Admite SO' `type(exc).__name__`, os construtores de `runtime/error_text.py`,
    `redact_error_message`/`redact_free_text`, `classify_agent_error_type`, `raise ... from exc` e
    `exc_info=`. Qualquer outra leitura do nome ligado e' uma violacao — inclusive as sete formas
    que a cerca de CC-10 nao enxergava (`.extend`, `+=`, `insert`, `self.x.append`,
    `d["k"].append`, `return {"error": ...}`, `f(error=...)`).
    """
    offenders = offending_sites(path.read_text(encoding="utf-8"), path)
    assert not offenders, "texto cru de excecao em campo de estado (LUC-06/NEW-01):\n" + "\n".join(offenders)


def test_the_structural_fence_sees_every_sink_shape_cc10_was_blind_to() -> None:
    """Nao-vacuidade da politica: as formas que a cerca de CC-10 deixava passar sao acusadas aqui.

    A auditoria de garantia mapeou nove formas de sink invisiveis para `_is_note_sink`
    (`test_gather_notes_no_raw_exception.py`). Cada uma delas e' apresentada aqui como fonte
    sintetica e TEM de ser acusada — sem isto, "endurecemos a cerca" seria uma afirmacao sem prova.
    """
    formas = [
        'notes.extend([f"x: {exc}"])',
        'notes += [f"x: {exc}"]',
        'self.notes.append(f"x: {exc}")',
        'state["notes"].append(f"x: {exc}")',
        'observacoes.append(f"x: {exc}")',
        'pendencias.append(f"x: {exc}")',
        'return {"error": f"x: {exc}"}',
        'notes.insert(0, f"x: {exc}")',
        'return start_failed_state(business_key=k, error=f"x: {exc}")',
        "logger.warning(str(exc))",
        "return {'error': exc.args[0]}",
    ]
    for forma in formas:
        fonte = f"def f():\n    try:\n        g()\n    except Exception as exc:\n        {forma}\n"
        assert offending_sites(fonte, Path("<sintetico>")), f"forma NAO acusada: {forma}"

    seguras = [
        'notes.append(f"x: {type(exc).__name__}")',
        'notes.extend([f"x: {redact_error_message(exc)}"])',
        'return {"error": dmn_unavailable_error(table, exc)}',
        "return start_failed_state(business_key=k, error=start_unavailable_error(exc))",
        "return classify_agent_error_type(exc)",
        'logger.warning("x", exc_info=exc)',
        "raise Outra() from exc",
    ]
    for forma in seguras:
        fonte = f"def f():\n    try:\n        g()\n    except Exception as exc:\n        {forma}\n"
        assert not offending_sites(fonte, Path("<sintetico>")), f"falso positivo: {forma}"


# =================================================================================================
# (B) Prova COMPORTAMENTAL — familia START (todo agente que inicia processo)
# =================================================================================================


#: Literal FECHADO (INFO-1, verificacao independente) — nao o simbolo importado. Comparar contra
#: `ERROR_START_PROCESS_ENGINE_UNAVAILABLE` diretamente tornaria a asserceao tautologica: mutar o
#: VALOR da constante em `andre/graph.py` para outra prosa livre (sem texto de excecao) manteria o
#: modulo inteiro verde, porque os dois lados da igualdade mudariam juntos. Pinando o texto aqui,
#: a asserceao abaixo (`test_andre_token_constante_e_o_literal_fechado`) e a prova comportamental
#: em `test_start_failure_error_field_never_carries_the_exception_text` reprovam se o VALOR mudar,
#: mesmo que o simbolo continue existindo com o mesmo nome.
_ANDRE_TOKEN_LITERAL = "start_process indisponivel (engine inacessivel)"

#: Agentes cujo `error` de falha de start e' um TOKEN DE CLASSE CONSTANTE, sem NENHUM texto da
#: excecao — postura ainda MAIS estrita que a do helper `start_unavailable_error` (que preserva o
#: nome da classe + o corpo redigido). Andre escolheu essa forma antes de LUC-06 existir e ela
#: nao e' rebaixada aqui: o teste apenas troca o criterio "passou pela rede" pelo criterio mais
#: forte "e' exatamente o literal declarado". Inventario FECHADO — um agente novo que apareca com
#: um `error` sem o marcador de redacao e sem entrar nesta tabela reprova.
_ERRO_TOKEN_CONSTANTE: dict[str, str] = {"andre": _ANDRE_TOKEN_LITERAL}


def test_andre_token_constante_e_o_literal_fechado() -> None:
    """INFO-1 (verificacao independente): pina o LITERAL, nao so' o simbolo.

    Sem este teste, mutar o VALOR de `ERROR_START_PROCESS_ENGINE_UNAVAILABLE` em
    `andre/graph.py` para outra prosa livre (ainda sem texto de excecao) deixava o modulo
    inteiro verde (V13 da verificacao independente: `59 passed`) porque
    `_ERRO_TOKEN_CONSTANTE["andre"]` comparava o simbolo com ele mesmo. A asserceao abaixo
    fixa o TEXTO, entao a mesma mutacao reprova aqui e na prova comportamental.
    """
    assert ERROR_START_PROCESS_ENGINE_UNAVAILABLE == _ANDRE_TOKEN_LITERAL, (
        f"o literal de andre mudou de {_ANDRE_TOKEN_LITERAL!r} para "
        f"{ERROR_START_PROCESS_ENGINE_UNAVAILABLE!r} -- atualize _ANDRE_TOKEN_LITERAL DE PROPOSITO "
        "se a mudanca for intencional, nunca por acidente"
    )


class _VazandoStartTransport(FakeCibSevenTransport):
    """O engine recusa o start com uma mensagem que ecoa o identificador — a forma real do vazamento."""

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        raise CibSevenError(
            f"engine unreachable at http://internal/cases/CPF-{_CPF_SINTETICO} ({process_key})"
        )


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@pytest.mark.parametrize(
    ("agent_id", "class_name", "extra", "start_node", "failure_node", "state"),
    _CASES,
    ids=_AGENT_IDS,
)
async def test_start_failure_error_field_never_carries_the_exception_text(
    agent_id: str,
    class_name: str,
    extra: dict[str, Any],
    start_node: str,
    failure_node: str,
    state: dict[str, Any],
) -> None:
    """LUC-06 fim a fim: o `error` do no de start e' token de classe + texto redigido.

    O canario e' o mesmo da triagem viva: a mensagem do transporte carrega um CPF no path da URL.
    A asserceao tem DOIS lados — o CPF nao aparece em NENHUMA string do estado devolvido, e o
    marcador de redacao aparece (prova que o valor ATRAVESSOU a rede, em vez de sumir).
    """
    graph = _graph_class(agent_id, class_name)(
        inference=_FakeInference(),
        dmn=_FakeDmn(),
        cibseven=_VazandoStartTransport(),
        audit_sink=FakeStartAuditSink(),
        **extra,
    )

    saida = await getattr(graph, start_node)(dict(state))

    assert saida.get("start_failed") is True, f"{agent_id}: o caso nao chegou ao caminho de falha"
    erro = str(saida.get("error") or "")
    assert erro, f"{agent_id}: o campo `error` ficou vazio — a degradacao tem de ser explicita"
    assert _CPF_SINTETICO not in _dump(saida), f"{agent_id}: CPF vazou no estado: {_dump(saida)}"

    if agent_id in _ERRO_TOKEN_CONSTANTE:
        # Postura ESTRITA (Andre): a celula e' um literal fechado — NADA da excecao entra, nem
        # redigido. Fixar a IGUALDADE (e nao so' a ausencia do CPF) e' o que impede a degradacao
        # silenciosa desse agente para prosa interpolada, que e' de onde LUC-06 veio.
        assert erro == _ERRO_TOKEN_CONSTANTE[agent_id], (
            f"{agent_id}: o token de classe constante mudou de forma: {erro!r}"
        )
        return

    assert _MARCADOR_REDACAO in erro, f"{agent_id}: `error` nao passou pela rede de PHI: {erro!r}"
    assert "CibSevenError" in erro, f"{agent_id}: o token de classe se perdeu: {erro!r}"
    assert "indisponivel" in erro, f"{agent_id}: o diagnostico se perdeu: {erro!r}"


# =================================================================================================
# (C) Prova COMPORTAMENTAL — familia DMN (todo grafo que avalia DMN)
# =================================================================================================


class _VazandoDmn:
    """A avaliacao da DMN falha com uma mensagem que ecoa a variavel submetida (400 payload echo)."""

    async def evaluate(
        self, table: str, dmn_input: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        raise DmnEvaluationError(f"engine 400: payload echo [CPF {_CPF_SINTETICO}] for `{table}`")


def _extra_de(agent_id: str) -> dict[str, Any]:
    return next(extra for aid, _cls, extra, _n, _f, _s in _CASES if aid == agent_id)


def _classe_de(agent_id: str) -> str:
    return next(cls for aid, cls, _e, _n, _f, _s in _CASES if aid == agent_id)


async def _drive_evaluate_dmn(agent_id: str) -> dict[str, Any]:
    """Chama o `_evaluate_dmn` REAL do agente com um transporte que vaza."""
    graph = _graph_class(agent_id, _classe_de(agent_id))(
        inference=_FakeInference(),
        dmn=_VazandoDmn(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        **_extra_de(agent_id),
    )
    if agent_id == "helena":
        # Helena assina `_evaluate_dmn(extraction, *, force_population=None)` — a tabela sai da
        # populacao, nao de um argumento.
        return await graph._evaluate_dmn({"sintoma_codigo": "dor_toracica", "population": "adult"})
    return await graph._evaluate_dmn("tabela_qualquer", {"campo": "valor"})


#: Grafos SEM metodo de avaliacao de DMN, com a razao. Fixado por `test_dmn_probe_covers_...`.
_GRAFOS_SEM_EVALUATE_DMN = {
    "_template": "scaffold do contrato canonico — nao avalia DMN",
    "beatriz": "agente de leitura FHIR — nao avalia DMN",
    "lucas": "avalia a DMN INLINE em `assess` — coberto pela prova dedicada abaixo",
}


def test_dmn_probe_covers_every_graph_that_evaluates_a_dmn() -> None:
    """Inventario FECHADO, apurado por AST — a cobertura nao pode derivar em silencio.

    Sem esta cerca, um agente novo que ganhe um `_evaluate_dmn` ficaria fora da prova
    comportamental abaixo sem que nada acusasse (foi exatamente o que aconteceu com a metade
    comportamental de CC-10, curada a mao em quatro agentes de nove).
    """
    com_metodo: set[str] = set()
    todos: set[str] = set()
    for path in _agent_graph_paths():
        todos.add(path.parent.name)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == "_evaluate_dmn"
            for n in ast.walk(tree)
        ):
            com_metodo.add(path.parent.name)

    assert todos - com_metodo == set(_GRAFOS_SEM_EVALUATE_DMN), (
        f"grafos sem `_evaluate_dmn` = {sorted(todos - com_metodo)} != inventario declarado "
        f"{sorted(_GRAFOS_SEM_EVALUATE_DMN)} — a prova comportamental da familia DMN esta "
        "cobrindo um conjunto diferente do que o codigo tem"
    )
    assert com_metodo == set(_DMN_AGENT_IDS), (
        f"walk AST = {sorted(com_metodo)} != parametrizacao {sorted(_DMN_AGENT_IDS)}"
    )


_DMN_AGENT_IDS = ["andre", "carolina", "fernando", "gustavo", "helena", "marina", "rafael", "valentina"]


@pytest.mark.parametrize("agent_id", _DMN_AGENT_IDS)
async def test_dmn_unavailable_error_never_carries_the_exception_text(agent_id: str) -> None:
    """NEW-01: `_evaluate_dmn` devolve token de classe + texto redigido, nunca o eco do payload."""
    saida = await _drive_evaluate_dmn(agent_id)

    erro = str(saida.get("error") or "")
    assert erro, f"{agent_id}: `_evaluate_dmn` nao sinalizou a falha"
    assert _CPF_SINTETICO not in _dump(saida), f"{agent_id}: CPF vazou: {_dump(saida)}"
    assert _MARCADOR_REDACAO in erro, f"{agent_id}: nao passou pela rede de PHI: {erro!r}"
    assert "DmnEvaluationError" in erro, f"{agent_id}: token de classe perdido: {erro!r}"
    assert "indisponivel" in erro, f"{agent_id}: diagnostico perdido: {erro!r}"


async def test_lucas_inline_dmn_error_never_carries_the_exception_text() -> None:
    """Lucas avalia `lucas_billing_admissibility` INLINE em `assess` — mesmo vazamento, outro sitio."""
    from maezo.agents.lucas.graph import LucasGraph

    graph = LucasGraph(
        inference=_FakeInference(),
        dmn=_VazandoDmn(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        **_extra_de("lucas"),
    )
    saida = await graph.assess(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hash",
            "beneficiario_pseudo_id": "p1",
            "canal": "whatsapp",
            "intencao": "cobranca_info",
            "tipo_solicitacao": "boleto_2via",
        }
    )

    erro = str(saida.get("dmn_error") or "")
    assert erro, "o `dmn_error` de Lucas ficou vazio"
    assert _CPF_SINTETICO not in _dump(saida), f"CPF vazou: {_dump(saida)}"
    assert _MARCADOR_REDACAO in erro, f"nao passou pela rede de PHI: {erro!r}"
    assert "DmnEvaluationError" in erro, f"token de classe perdido: {erro!r}"


# =================================================================================================
# (D) O helper existe e e' UMA definicao (nunca N copias)
# =================================================================================================


def test_error_text_helpers_preserve_the_class_token_and_scrub_the_body() -> None:
    """Contrato dos dois construtores: prefixo estavel + token de classe + corpo redigido."""
    from maezo.runtime.error_text import dmn_unavailable_error, start_unavailable_error

    exc = CibSevenError(f"engine at http://x/CPF-{_CPF_SINTETICO}")
    inicio = start_unavailable_error(exc)
    assert inicio.startswith("start_process indisponivel: ")
    assert "CibSevenError" in inicio and _MARCADOR_REDACAO in inicio
    assert _CPF_SINTETICO not in inicio

    com_chave = start_unavailable_error(exc, process_key="SP-OP-ESCALATION-001")
    assert com_chave.startswith("start de SP-OP-ESCALATION-001 indisponivel: ")
    assert _CPF_SINTETICO not in com_chave

    dmn = dmn_unavailable_error("cred_admissibility", DmnEvaluationError(f"eco {_CPF_SINTETICO}"))
    assert dmn.startswith("DMN `cred_admissibility` indisponivel: ")
    assert "DmnEvaluationError" in dmn and _MARCADOR_REDACAO in dmn
    assert _CPF_SINTETICO not in dmn


@pytest.mark.parametrize(
    ("n", "espera_idempotente"),
    [
        (10, True),
        (_ERROR_MESSAGE_MAX_CHARS - 40, False),
        (_ERROR_MESSAGE_MAX_CHARS, False),
        (_ERROR_MESSAGE_MAX_CHARS * 3, False),
    ],
    ids=["curta", "quase-no-teto", "no-teto", "muito-acima-do-teto"],
)
def test_redact_error_field_length_cap_reapplies_over_the_prefixed_text(
    n: int, espera_idempotente: bool
) -> None:
    """F1 (achado da verificacao independente, 2026-09-05): fixa o comportamento REAL do teto.

    A rede de identificadores E' idempotente sobre a saida dos construtores (nada de PHI sobra
    para a segunda passada encontrar). O TETO DE COMPRIMENTO NAO E': ele e' reaplicado ao texto
    JA prefixado com o MESMO numero (`_ERROR_MESSAGE_MAX_CHARS`) que os construtores ja' usaram
    so' sobre o corpo, entao uma mensagem perto do teto do corpo ou acima dele perde mais alguns
    caracteres de cauda nesta segunda passada -- e' exatamente o que os dois docstrings (deste
    modulo e de `runtime/start_outcome.py::start_failed_state`) descrevem apos F1. Nenhum
    identificador sobrevive em nenhum dos dois casos, idempotente ou nao.
    """
    from maezo.runtime.error_text import redact_error_field, start_unavailable_error

    exc = CibSevenError("A" * n + f" CPF {_CPF_SINTETICO}")
    uma = start_unavailable_error(exc)
    duas = redact_error_field(uma)

    assert _CPF_SINTETICO not in uma, f"n={n}: CPF vazou na primeira passada: {uma!r}"
    assert _CPF_SINTETICO not in duas, f"n={n}: CPF vazou na segunda passada: {duas!r}"
    assert (duas == uma) is espera_idempotente, (
        f"n={n}: idempotencia da segunda passada mudou para {duas == uma!r}; atualize este teste "
        "E os dois docstrings de F1 (error_text.py::redact_error_field / "
        "start_outcome.py::start_failed_state) juntos, na mesma direcao"
    )
    if not espera_idempotente:
        assert duas.endswith(_TRUNCATION_MARKER), f"n={n}: esperava um segundo corte com marcador"
        assert len(duas) <= _ERROR_MESSAGE_MAX_CHARS + len(_TRUNCATION_MARKER)


def test_start_failed_state_redacts_the_error_cell_as_a_backstop() -> None:
    """Defesa em profundidade: mesmo que um call site futuro monte o texto cru, a fabrica redige.

    A cerca AST acima ja' proibe o call site cru; este teste garante que a proibicao tem uma
    SEGUNDA linha, porque `start_failed_state` e' a UNICA fabrica do marcador `start_failed`.
    """
    from maezo.runtime.start_outcome import start_failed_state

    saida = start_failed_state(business_key="AUTH-amh-1", error=f"cru: CPF {_CPF_SINTETICO}")

    assert _CPF_SINTETICO not in str(saida["error"])
    assert _MARCADOR_REDACAO in str(saida["error"])
    assert saida["start_failed"] is True and saida["process_started"] is False
    assert saida["business_key"] == "AUTH-amh-1"


def test_start_failed_state_leaves_a_class_token_constant_untouched() -> None:
    """Falso positivo que NAO pode acontecer: a constante de Andre atravessa byte a byte."""
    from maezo.agents.andre.graph import ERROR_START_PROCESS_ENGINE_UNAVAILABLE
    from maezo.runtime.start_outcome import start_failed_state

    saida = start_failed_state(business_key="PAGTO-amh-1", error=ERROR_START_PROCESS_ENGINE_UNAVAILABLE)
    assert saida["error"] == ERROR_START_PROCESS_ENGINE_UNAVAILABLE
