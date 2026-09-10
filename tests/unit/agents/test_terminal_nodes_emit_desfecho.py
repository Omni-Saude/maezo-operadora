"""Cerca CC-09: todo no terminal de todo grafo emite UM `maezo_agent_desfecho_total` (Agent
Fleet Audit, 2026-09-04).

O DEFEITO (CONFIRMED 10/10 pelos confirmadores R1, FER-07/VAL-07/LUC-12/BEA-12/RAF-14 e o
achado cross-cutting CC-09). `spec/agents/*/agent.yaml` declara KPIs de desfecho/rota —
`resolution_rate: track`, `escalation_precision: track`, `false_denial_rate: ==0`,
`human_routing_precision: track` etc. — mas ate' esta correcao NENHUM grafo emitia telemetria de
desfecho por turno: `grep -rln 'record_' src/maezo/agents/*/graph.py` devolvia vazio. Um KPI sem
emissor nao e' "ainda nao atingido" — e' inaferivel.

DUAS CAMADAS, mesma forma de `test_start_failure_routing.py` (a cerca irma CC-01):

  1. ESTRUTURAL (AST) — todo NO TERMINAL de todo grafo (o no' cujo unico destino e' `END`, ou
     cujo corpo grava o `desfecho` final antes de uma aresta incondicional para `END`) chama
     `emit_turn_desfecho` no seu proprio corpo. Um agente novo/um no terminal novo que esqueca a
     chamada quebra aqui, mesmo sem nenhum teste comportamental dedicado a ele.
  2. COMPORTAMENTAL — cada um dos 10 agentes, invocado com um espiao em
     `observability.record_agent_desfecho`, produz EXATAMENTE UMA emissao por turno, com os
     labels que o proprio estado do agente ja carregava (nunca inventados aqui).

Mais duas sondas de mutacao, fora do par AST/comportamental:

  3. CARDINALIDADE — um valor fora do vocabulario fechado do agente (ou PHI-like) NUNCA alcanca
     o label: `emit_turn_desfecho` normaliza para `"outro"` e a mensagem de log preserva so'
     agente+campo, nunca o valor.
  4. EMISSAO UNICA no caminho de falha de start — `runtime.start_outcome.notify_start_failure`
     e' o UNICO site que emite o desfecho de erro; um agente cujo proprio no' de falha TAMBEM
     emitisse duplicaria o turno. `test_start_failure_does_not_double_emit` prova isso para um
     agente representativo (andre) chamando a cadeia completa `start_process` (falha) ->
     `notify_start_failure`.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any, cast

import pytest

from maezo.platform import observability
from maezo.runtime import start_outcome, turn_telemetry
from maezo.runtime.turn_telemetry import emit_turn_desfecho
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

# ---------------------------------------------------------------------------------------------
# Dobradas minimas (mesmo estilo de `test_start_failure_routing.py`)
# ---------------------------------------------------------------------------------------------


class _FakeInference:
    model_id = "claude-cc09-probe"

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        return "texto sintetico"


class _RecordingWhatsApp:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self._fail = fail

    async def send(self, to: str, text: str) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("whatsapp indisponivel (probe CC-09)")
        self.sent.append((to, text))
        return {"ok": True}


class _FailingStartTransport(FakeCibSevenTransport):
    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        raise CibSevenError(f"engine indisponivel (probe CC-09): start de {process_key}")


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _record(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(observability, "record_agent_desfecho", _record)
    return calls


# ---------------------------------------------------------------------------------------------
# 1. Cerca ESTRUTURAL (AST) — todo no terminal chama `emit_turn_desfecho`
# ---------------------------------------------------------------------------------------------

#: (agent_id, [nomes de no terminal esperados a chamar `emit_turn_desfecho`]).
_TERMINAL_NODES: dict[str, tuple[str, ...]] = {
    "andre": ("finalize",),
    "beatriz": ("finalize",),
    "carolina": ("finalize",),
    "fernando": ("notify", "start_process"),
    "gustavo": ("finalize",),
    "helena": ("respond",),
    "lucas": ("complete",),
    "marina": ("finalize",),
    "rafael": ("complete",),
    "valentina": ("no_consent", "stopped", "finalize"),
}

_AGENT_NODE_CASES = [(agent, node) for agent, nodes in _TERMINAL_NODES.items() for node in nodes]


def _agent_graph_path(agent_id: str) -> Path:
    return Path(importlib.import_module(f"maezo.agents.{agent_id}.graph").__file__)


def _find_function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"funcao {name!r} nao encontrada no modulo")


def _calls_emit_turn_desfecho(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "emit_turn_desfecho":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "emit_turn_desfecho":
            return True
    return False


@pytest.mark.parametrize(
    ("agent_id", "node_name"),
    _AGENT_NODE_CASES,
    ids=[f"{a}::{n}" for a, n in _AGENT_NODE_CASES],
)
def test_every_terminal_node_calls_emit_turn_desfecho(agent_id: str, node_name: str) -> None:
    """Cerca estrutural: o corpo do no terminal contem uma chamada a `emit_turn_desfecho`.

    Uma regressao aqui (um no terminal que perde a chamada, ou um agente novo que esquece de
    adota-la) e' exatamente a forma do defeito CC-09 — um KPI declarado sem emissor.
    """
    path = _agent_graph_path(agent_id)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = _find_function(tree, node_name)
    assert _calls_emit_turn_desfecho(fn), (
        f"{agent_id}::{node_name}: no terminal NAO chama `emit_turn_desfecho` — o KPI de "
        f"desfecho deste agente ficaria inaferivel neste caminho (CC-09)"
    )


def test_shared_start_failure_site_calls_emit_turn_desfecho() -> None:
    """`runtime.start_outcome.notify_start_failure` — o UNICO site do desfecho de erro de start
    para os 9 agentes que abrem processo — chama `emit_turn_desfecho` no proprio corpo."""
    path = Path(start_outcome.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = _find_function(tree, "notify_start_failure")
    assert _calls_emit_turn_desfecho(fn), (
        "runtime.start_outcome.notify_start_failure nao chama `emit_turn_desfecho` — a falha de "
        "start pararia de alimentar o KPI de desfecho para os 9 agentes que a delegam"
    )


def test_desfecho_erro_inicio_processo_literal_matches_start_outcome() -> None:
    """`turn_telemetry`'s local literal e o de `start_outcome` sao O MESMO valor (CC-01/CC-09).

    Duplicado deliberadamente como STRING (nao importado) para quebrar o ciclo de import
    `start_outcome -> turn_telemetry` — este teste e' o que garante que as duas copias nunca
    divergem silenciosamente."""
    assert turn_telemetry._DESFECHO_ERRO_INICIO_PROCESSO == start_outcome.DESFECHO_ERRO_INICIO_PROCESSO
    for agent_id, vocab in turn_telemetry._DESFECHO_VOCAB.items():
        if agent_id == "beatriz":
            continue  # nunca chama start_process — o desfecho de erro nao se aplica
        assert start_outcome.DESFECHO_ERRO_INICIO_PROCESSO in vocab, agent_id


def test_fernando_desfecho_constants_are_declared_in_the_vocab() -> None:
    """FERNANDO-NOTIFY-DESFECHO-LITERAL: `fernando/graph.py`'s three named `desfecho` constants
    (mirroring `_DESFECHO_ERRO_INICIO_PROCESSO`/`DESFECHO_ERRO_INICIO_PROCESSO`'s own
    two-independent-copies-checked-equal idiom, one level up) must never drift from the closed
    vocabulary `_DESFECHO_VOCAB["fernando"]` declares."""
    from maezo.agents.fernando import graph as fernando_graph

    constantes = {
        fernando_graph.DESFECHO_NOTIFICACAO_PREVIA_ENVIADA,
        fernando_graph.DESFECHO_LEMBRETE_REGULARIZACAO_ENVIADO,
        fernando_graph.DESFECHO_ENCAMINHADO_ANALISE_HUMANA,
    }
    assert constantes <= turn_telemetry._DESFECHO_VOCAB["fernando"]


def _desfecho_literal_values(no: ast.AST) -> list[str]:
    """Every BARE string literal (never a `Name`/`Attribute` reference to a constant) written as a
    `desfecho` value ANYWHERE under `no`: a `return {"desfecho": ...}` dict entry, a `desfecho=`
    keyword argument (e.g. to `emit_turn_desfecho`), or either branch of an `if/else` expression
    assigned to a local named `desfecho`. Tree-derived (BRIEF-COMMON fence rule) — no `file:line`,
    no hand-maintained inventory of what the code currently happens to contain.

    `no` is any AST node: a single function (the Fernando STRUCTURAL fence, which asks "does THIS
    function use a bare literal at all?") or a whole `ast.Module` (the fleet-wide VALUE fence,
    which asks "is every literal this graph writes inside the agent's declared vocabulary?")."""
    literais: list[str] = []

    def _colhe(valor: ast.expr) -> None:
        if isinstance(valor, ast.Constant) and isinstance(valor.value, str):
            literais.append(valor.value)
        elif isinstance(valor, ast.IfExp):
            _colhe(valor.body)
            _colhe(valor.orelse)

    for node in ast.walk(no):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "desfecho":
                    _colhe(value)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "desfecho":
                    _colhe(kw.value)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "desfecho" for t in node.targets
        ):
            _colhe(node.value)
    return literais


@pytest.mark.parametrize("node_name", ["notify", "escalate"])
def test_fernando_graph_never_writes_a_bare_desfecho_literal(node_name: str) -> None:
    """FERNANDO-NOTIFY-DESFECHO-LITERAL — RED PROOF. `notify()`/`escalate()` must write `desfecho`
    through the module's named `DESFECHO_*` constants, never a bare string — structural (AST
    shape), not value-based, so reverting either call site to a bare literal fails here even when
    the literal's STRING VALUE is still in `_DESFECHO_VOCAB["fernando"]` (that value-domain check
    is `test_fernando_desfecho_constants_are_declared_in_the_vocab`, above — a DIFFERENT property).
    Mutation: put back `"desfecho": "encaminhado_analise_humana"` (or the `notify()` ternary's two
    bare strings) -> this goes RED."""
    path = _agent_graph_path("fernando")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = _find_function(tree, node_name)
    literais = _desfecho_literal_values(fn)
    assert not literais, (
        f"fernando/graph.py::{node_name} atribui `desfecho` a partir de um literal de string "
        f"bruto {literais!r} em vez de uma das constantes nomeadas "
        "(DESFECHO_NOTIFICACAO_PREVIA_ENVIADA/DESFECHO_LEMBRETE_REGULARIZACAO_ENVIADO/"
        "DESFECHO_ENCAMINHADO_ANALISE_HUMANA)"
    )


def _real_agent_ids() -> list[str]:
    """Os agentes REAIS, derivados da ARVORE (`src/maezo/agents/<id>/graph.py` via a introspecao do
    pacote, nunca um caminho de repositorio escrito a mao nem uma lista mantida a mao).

    `_template` fica de fora e o motivo e' de conteudo, nao de conveniencia: ele e' o scaffold, o
    docstring dele INSTRUI o autor de um agente novo a substituir `DESFECHO_PROCESSO_INICIADO` por
    literais proprios, e ele nao tem (nem deve ter) entrada em `_DESFECHO_VOCAB` porque nao e' um
    agente que emita telemetria."""
    pacote = Path(importlib.import_module("maezo.agents").__file__).parent
    return sorted(d.name for d in pacote.iterdir() if d.name != "_template" and (d / "graph.py").is_file())


@pytest.mark.parametrize("agent_id", _real_agent_ids())
def test_every_agent_graph_only_writes_declared_desfecho_literals(agent_id: str) -> None:
    """FERNANDO-NOTIFY-DESFECHO-LITERAL, CERCA AMPLA (achado F3 do gatekeeper R1): TODO literal de
    `desfecho` escrito por QUALQUER grafo de agente esta dentro do vocabulario FECHADO que aquele
    agente declara em `turn_telemetry._DESFECHO_VOCAB`.

    Propriedade DIFERENTE da cerca estrutural do fernando acima. Aquela pergunta "esta funcao usa
    literal solto?" (e so' fernando responde "nao", porque so' fernando ganhou constantes
    nomeadas); esta pergunta "o VALOR escrito esta declarado?", que e' a propriedade que os 9
    outros agentes tambem podem — e devem — satisfazer mantendo seus literais soltos, que e' a
    convencao que `_template/graph.py` ensina. Sem ela, um branch novo com um literal nao
    declarado nao quebra teste nenhum: `emit_turn_desfecho` normaliza silenciosamente para
    `"outro"` (a defesa de cardinalidade/PHI do §3 deste modulo), entao o KPI daquele desfecho
    simplesmente nunca aparece — o mesmo tipo de "inaferivel" que a cerca CC-09 existe para
    impedir.

    A string VAZIA nao conta e nao e' excecao de conveniencia: os quatro sites que a produzem
    (`andre`/`fernando`/`valentina`/`helena`) sao os dicionarios de fabrica de estado
    (`new_*_state`), onde `"desfecho": ""` e' o sentinela "ainda nao houve desfecho neste turno",
    nunca um desfecho escrito por um no' terminal.

    Mutacao que leva este teste a RED: escrever um literal fora do vocabulario em QUALQUER agente
    (p.ex. em `beatriz/graph.py`, `"desfecho": "dossie_instruido"` -> `"desfecho_inventado"`) ->
    RED so' no caso `[beatriz]`."""
    vocabulario = turn_telemetry._DESFECHO_VOCAB.get(agent_id)
    assert vocabulario is not None, (
        f"o agente {agent_id!r} tem `graph.py` mas NENHUMA entrada em `_DESFECHO_VOCAB` — todo "
        "desfecho que ele escrever seria normalizado para `outro` e o KPI dele ficaria inaferivel"
    )
    caminho = _agent_graph_path(agent_id)
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    literais = {valor for valor in _desfecho_literal_values(arvore) if valor != ""}
    fora = sorted(literais - vocabulario)
    assert not fora, (
        f"{agent_id}/graph.py escreve o(s) desfecho(s) {fora!r} fora do vocabulario declarado em "
        f"`turn_telemetry._DESFECHO_VOCAB[{agent_id!r}]` — `emit_turn_desfecho` normalizaria "
        "silenciosamente para `outro` e o KPI desse desfecho nunca apareceria"
    )


# ---------------------------------------------------------------------------------------------
# 2. Cerca COMPORTAMENTAL — uma emissao por turno, labels corretos
# ---------------------------------------------------------------------------------------------


async def test_andre_finalize_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.andre.graph import AndreGraph

    calls = _spy(monkeypatch)
    state = {
        "route": "auto_route",
        "desfecho": "analytics_pronto",
        "flow": "population_analytics",
    }
    await AndreGraph.finalize(cast(Any, None), state)
    assert calls == [
        {
            "agent_id": "andre",
            "desfecho": "analytics_pronto",
            "route": "auto_route",
            "motivo_categoria": None,
            "enviada": None,
            "start_failed": False,
            "flow": "population_analytics",
        }
    ]


async def test_beatriz_finalize_emits_one_desfecho_no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.beatriz.graph import BeatrizGraph

    calls = _spy(monkeypatch)
    state = {"desfecho": "dossie_instruido"}
    await BeatrizGraph.finalize(cast(Any, None), state)
    assert len(calls) == 1
    assert calls[0]["agent_id"] == "beatriz"
    assert calls[0]["desfecho"] == "dossie_instruido"
    assert calls[0]["route"] is None
    assert calls[0]["motivo_categoria"] is None


async def test_carolina_finalize_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.carolina.graph import CarolinaGraph

    calls = _spy(monkeypatch)
    state = {"route": "human_review", "desfecho": "analise_credenciamento"}
    await CarolinaGraph.finalize(cast(Any, None), state)
    assert len(calls) == 1
    assert calls[0]["route"] == "human_review"
    assert calls[0]["desfecho"] == "analise_credenciamento"


async def test_gustavo_finalize_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.gustavo.graph import GustavoGraph

    calls = _spy(monkeypatch)
    state = {"route": "instruct_nip", "desfecho": "nip_encaminhada_instrucao_humana"}
    await GustavoGraph.finalize(cast(Any, None), state)
    assert len(calls) == 1
    assert calls[0]["desfecho"] == "nip_encaminhada_instrucao_humana"
    assert calls[0]["route"] == "instruct_nip"


async def test_marina_finalize_emits_one_desfecho_with_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.marina.graph import MarinaGraph

    calls = _spy(monkeypatch)
    state = {"route": "auto_route", "desfecho": "pagar_integral", "flow": "contas"}
    await MarinaGraph.finalize(cast(Any, None), state)
    assert len(calls) == 1
    assert calls[0]["flow"] == "contas"
    assert calls[0]["desfecho"] == "pagar_integral"


async def test_rafael_complete_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.rafael.graph import RafaelGraph

    calls = _spy(monkeypatch)
    state = {"route": "human_auditor", "desfecho": "encaminhado_auditor"}
    await RafaelGraph.complete(cast(Any, None), state)
    assert len(calls) == 1
    assert calls[0]["route"] == "human_auditor"
    assert calls[0]["desfecho"] == "encaminhado_auditor"


async def test_lucas_complete_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.lucas.graph import LucasGraph

    calls = _spy(monkeypatch)
    state = {
        "route": "escalate_human",
        "desfecho": "escalado_humano",
        "motivo_categoria": "falha_tecnica",
        "mensagem_enviada": True,
    }
    await LucasGraph.complete(cast(Any, None), state)
    assert calls == [
        {
            "agent_id": "lucas",
            "desfecho": "escalado_humano",
            "route": "escalate_human",
            "motivo_categoria": "falha_tecnica",
            "enviada": True,
            "start_failed": False,
            "flow": None,
        }
    ]


async def test_valentina_no_consent_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.valentina.graph import ValentinaGraph

    calls = _spy(monkeypatch)
    outcome = await ValentinaGraph.no_consent(cast(Any, None), {})
    assert outcome == {"desfecho": "sem_consentimento", "process_started": False}
    assert len(calls) == 1
    assert calls[0]["desfecho"] == "sem_consentimento"
    assert calls[0]["route"] is None


async def test_valentina_stopped_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.valentina.graph import ValentinaGraph

    calls = _spy(monkeypatch)
    outcome = await ValentinaGraph.stopped(cast(Any, None), {})
    assert outcome == {"desfecho": "interrompido_revogacao", "process_started": False}
    assert len(calls) == 1
    assert calls[0]["desfecho"] == "interrompido_revogacao"


async def test_valentina_finalize_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.valentina.graph import ValentinaGraph

    calls = _spy(monkeypatch)
    state = {"route": "auto_route", "desfecho": "enrollment_realizado"}
    await ValentinaGraph.finalize(cast(Any, None), state)
    assert len(calls) == 1
    assert calls[0]["desfecho"] == "enrollment_realizado"


async def test_fernando_notify_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.fernando.graph import FernandoGraph

    calls = _spy(monkeypatch)
    graph = FernandoGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_RecordingWhatsApp(),
    )
    state: dict[str, Any] = {
        "tenant_id": "amh",
        "route": "notify",
        "status_inadimplencia": "PENDENTE_NOTIFICACAO",
        "to_hash": "hash1",
        "canal": "whatsapp",
    }
    saida = await graph.notify(state)
    assert saida["desfecho"] == "notificacao_previa_enviada"
    assert len(calls) == 1
    assert calls[0]["desfecho"] == "notificacao_previa_enviada"
    assert calls[0]["route"] == "notify"
    assert calls[0]["enviada"] is True


async def test_fernando_start_process_success_emits_one_desfecho(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fernando so' tem `finalize` implicito: a aresta `continue` de `start_process` vai direto
    para END. Por isso o proprio `start_process`, no ramo de SUCESSO, e' o no terminal (ver
    `compile_graph`) — a emissao acontece aqui, nao num no separado."""
    from maezo.agents.fernando.graph import FernandoGraph

    calls = _spy(monkeypatch)
    graph = FernandoGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_RecordingWhatsApp(),
    )
    state: dict[str, Any] = {
        "tenant_id": "amh",
        "business_key": "INAD-amh-1",
        "route": "escalate",
        "motivo_categoria": "inadimplencia",
        "desfecho": "encaminhado_analise_humana",
    }
    saida = await graph.start_process(state)
    assert saida["process_started"] is True
    assert len(calls) == 1
    assert calls[0]["desfecho"] == "encaminhado_analise_humana"
    assert calls[0]["route"] == "escalate"
    assert calls[0]["motivo_categoria"] == "inadimplencia"


async def test_helena_respond_inform_emits_resolvido_automatico(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caminho `inform` (sem escalacao): `HelenaState.desfecho` e' campo morto (nunca escrito por
    nenhum no de Helena) — o `desfecho` do label e' DERIVADO de `escalation_started`/
    `response_kind`, nao lido de `state["desfecho"]`."""
    from maezo.agents.helena.graph import HelenaGraph

    calls = _spy(monkeypatch)
    graph = HelenaGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_RecordingWhatsApp(),
    )
    state: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hash1",
        "response_text": "resposta informativa",
        "response_kind": "inform",
    }
    saida = await graph.respond(state)
    assert "error" not in saida
    assert len(calls) == 1
    assert calls[0]["agent_id"] == "helena"
    assert calls[0]["desfecho"] == "resolvido_automatico"
    assert calls[0]["route"] == "inform"
    assert calls[0]["enviada"] is True


async def test_helena_respond_escalated_emits_escalado_humano(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents.helena.graph import HelenaGraph

    calls = _spy(monkeypatch)
    graph = HelenaGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_RecordingWhatsApp(),
    )
    state: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hash1",
        "response_text": "um humano vai continuar",
        "response_kind": "escalate",
        "escalation_started": True,
        "escalation_motivo": "solicitacao_humano",
    }
    saida = await graph.respond(state)
    assert "error" not in saida
    assert len(calls) == 1
    assert calls[0]["desfecho"] == "escalado_humano"
    assert calls[0]["route"] == "escalate"
    assert calls[0]["motivo_categoria"] == "solicitacao_humano"


async def test_helena_respond_start_failed_does_not_double_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    """`start_failed=True`: a emissao vem SO' de `notify_start_failure` (via
    `_start_failure_outcome`) — `respond` nao emite uma segunda vez para o MESMO turno."""
    from maezo.agents.helena.graph import HelenaGraph

    calls = _spy(monkeypatch)
    graph = HelenaGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_RecordingWhatsApp(),
    )
    state: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hash1",
        "start_failed": True,
        "escalation_motivo": "falha_tecnica",
        "escalation_severidade": "leve",
        "business_key": "ESC-amh-hash1",
    }
    saida = await graph.respond(state)
    assert saida["desfecho"] == "erro_inicio_processo"
    assert len(calls) == 1  # UMA emissao so', vinda do helper compartilhado
    assert calls[0]["desfecho"] == "erro_inicio_processo"


async def test_helena_start_failure_route_is_falha_tecnica_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """F1 (VERIFY-CC09): o failure-path de start da Helena tem de rotular `route` com
    `"falha_tecnica_start"` — o literal que `_ROUTE_VOCAB["helena"]` ja declara para exatamente
    este caso (`RESPONSE_KIND_FALHA_TECNICA_START`, `helena/graph.py`) — e nao com `""`/`None`.
    `HelenaState` nao tem chave `route` (usa `response_kind`); o site compartilhado
    `notify_start_failure` le `state.get("route")` de forma generica e por isso, sem um override
    explicito do call site de Helena, o label ficava vazio. Continua exigindo emissao UNICA
    (nenhuma duplicacao do turno)."""
    from maezo.agents.helena.graph import HelenaGraph

    calls = _spy(monkeypatch)
    graph = HelenaGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_RecordingWhatsApp(),
    )
    state: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hash1",
        "start_failed": True,
        "escalation_motivo": "falha_tecnica",
        "escalation_severidade": "leve",
        "business_key": "ESC-amh-hash1",
    }
    saida = await graph.respond(state)
    assert saida["desfecho"] == "erro_inicio_processo"
    assert len(calls) == 1  # UMA emissao so', vinda do helper compartilhado
    assert calls[0]["route"] == "falha_tecnica_start"


# ---------------------------------------------------------------------------------------------
# 3. Cerca de CARDINALIDADE — fora do vocabulario fechado nunca chega ao label
# ---------------------------------------------------------------------------------------------


async def test_out_of_vocab_desfecho_is_normalized_never_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _spy(monkeypatch)
    emit_turn_desfecho({}, agent_id="rafael", desfecho="valor_nao_declarado_no_vocabulario")
    assert calls[0]["desfecho"] == "outro"


async def test_phi_like_desfecho_is_normalized_never_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um valor PHI-like (CPF sintetico) tentando vazar por `desfecho` e' normalizado — o mesmo
    caminho de seguranca que cobre cardinalidade cobre PHI, porque os dois sao, aqui, o MESMO
    defeito: um valor fora do vocabulario fechado."""
    calls = _spy(monkeypatch)
    emit_turn_desfecho({}, agent_id="rafael", desfecho="cpf_123.456.789-00_vazou")
    assert calls[0]["desfecho"] == "outro"
    assert "123.456.789-00" not in str(calls[0]["desfecho"])


async def test_out_of_vocab_route_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _spy(monkeypatch)
    emit_turn_desfecho({}, agent_id="andre", desfecho="analytics_pronto", route="rota_inventada")
    assert calls[0]["route"] == "outro"


async def test_out_of_vocab_motivo_categoria_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _spy(monkeypatch)
    emit_turn_desfecho(
        {},
        agent_id="lucas",
        desfecho="escalado_humano",
        motivo_categoria="motivo_nao_declarado",
    )
    assert calls[0]["motivo_categoria"] == "outro"


async def test_route_none_stays_none_never_outro(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beatriz nao tem `route`: `None` nunca vira `"outro"` — so' um valor PRESENTE fora do
    vocabulario e' normalizado (`None` significa "campo nao se aplica", nao "valor invalido")."""
    calls = _spy(monkeypatch)
    emit_turn_desfecho({}, agent_id="beatriz", desfecho="dossie_instruido")
    assert calls[0]["route"] is None


async def test_motivo_categoria_none_stays_none_for_agents_without_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _spy(monkeypatch)
    emit_turn_desfecho({}, agent_id="andre", desfecho="analytics_pronto", route="auto_route")
    assert calls[0]["motivo_categoria"] is None


# ---------------------------------------------------------------------------------------------
# 4. Emissao unica no caminho de falha de start (nao duplica a do no terminal do agente)
# ---------------------------------------------------------------------------------------------


async def test_start_failure_does_not_double_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cadeia completa de Andre: `start_process` (falha) -> `notify_start_failure`. O desfecho de
    SUCESSO gravado a montante nao sobrevive (CC-01) e a telemetria (CC-09) dispara EXATAMENTE
    uma vez — nunca duas (uma no `notify_start_failure` do agente delegando, outra dentro do
    helper compartilhado)."""
    from maezo.agents.andre.graph import AndreGraph

    calls = _spy(monkeypatch)
    graph = AndreGraph(
        inference=_FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=_FailingStartTransport(),
        audit_sink=FakeStartAuditSink(),
        fhir=None,
        population=None,
    )
    entrada: dict[str, Any] = {
        "tenant_id": "amh",
        "business_key": "PAGTO-amh-1",
        "route": "auto_route",
        "flow": "pagto_dossier",
        "desfecho": "dossie_pronto_clerical",
    }
    depois_do_start = await graph.start_process(entrada)
    assert depois_do_start.get("start_failed") is True
    assert not calls  # `start_process` em si nunca emite

    fundido = {**entrada, **depois_do_start}
    saida = await graph.notify_start_failure(fundido)

    assert saida["desfecho"] == "erro_inicio_processo"
    assert len(calls) == 1
    assert calls[0]["desfecho"] == "erro_inicio_processo"
    assert calls[0]["route"] == "auto_route"
