"""NEW-C1-3 (Agent Fleet Audit — CC-13's own disclosed follow-up, "W3
CONTRACT-PARITY-GENERIC-FENCE") — the fully generic engine-variable <-> contract-markdown parity
fence that `test_contract_provenance_parity.py` (CC-13) explicitly scoped OUT of its own charter:

    "SCOPE (deliberate, disclosed): this checks parity for the SPECIFIC additive keys named
    above — NOT a fully generic 'every engine variable must appear in a Variavel table' fence."

CC-13 fixed the ADDITIVE/provenance subset (dossie_<agente>, <agente>_route/_flow,
motivo_encaminhamento, grupo_destino, dmn_decision_refs) for the 9 agent/contract pairs its own
`_CASES` enumerates. This file extends the SAME parity check to EVERY engine variable each
agent's contract-variable builder can write — base input facts included, not just the additive
annotations — using real AST inspection of the builder function (never a dynamic run: a dynamic
probe only ever exercises the ONE state/branch it is given, and would silently miss every
conditionally-written key a different route/flow takes; the audit's own worker-facing precedent
for this shape of static, branch-aware key discovery is `scripts/ci/check_effect_chokepoint_
fence.py`'s AST walk, not a runtime probe).

WHAT THIS CHECKS (per (agent, contract) pair of `test_contract_provenance_parity.py::_CASES`,
minus marina's excluded `reembolso` no-op flow — same exclusion, same reason):

  1. WRITE direction: every string-literal key the builder method
     (`_contract_variables`/`_escalation_variables`/`_inadimplencia_variables`) assigns into its
     `variables`/`common` dict via one of `_VariableCollector`'s HANDLED shapes — an initial
     `dict_var: dict[str, Any] = {...}` literal (`ast.AnnAssign`), a later
     `dict_var["key"] = ...` (`ast.Assign` to a `Subscript`), `dict_var.update({...})`, a
     `for opt in (<literals>): ...` tail, and (branch-aware) an `if`/`else` — for THIS flow,
     branch-aware for the ONE flow-discriminating `if` marina/gustavo's shared builders use
     (`flow`/`fluxo`), unioned across every OTHER (non-flow) conditional, mirroring `_CASES`'s
     own "full additive set" philosophy — must be declared under one of `## Variaveis de
     entrada` / `## Variaveis de saida` / `## Variaveis de proveniencia` in that contract's
     markdown. NOT handled (§Delta-F4 whitelist, `_VariableCollector.blind_spots`, checked and
     currently EMPTY for all 7 real builders): a `**` spread of a Dict literal, a `dict(...)`
     builtin call, `dict_var` aliased to another Name, or any statement inside a `try:`/`while:`/
     `with:` body — any of these appearing in a FUTURE builder fails the fence loudly instead of
     silently under-collecting.
  2. READ direction: every variable declared "obrigatoria: sim" (or a `sim*`/`sim§`
     fail-safe/pre-resolved variant — still a hard input, per those contracts' own legend) under
     `## Variaveis de entrada` must be referenced somewhere in that agent's own `graph.py`
     (`state.get("<var>")` / `state["<var>"]`) — a contract that requires an input the code never
     reads is exactly as much a drift as an engine variable no contract declares.

BRANCH-AWARE AST WALK, NOT A DYNAMIC PROBE. `_VariableCollector` walks the builder's own AST body.
For the two agents whose ONE builder feeds TWO contracts (marina: `flow`; gustavo: `fluxo`), a
per-case `(flow_field, flow_value)` selects which side of the single flow-discriminating `if` to
follow — the matching side's body (terminating on `return`, mirrors the real interpreter cutting
off the fallthrough code) or, for a value that does not match, the `orelse` (or, absent one,
straight fallthrough to the statements textually AFTER the `if` — gustavo's `if fluxo ==
"ans_submit": ...; return common; <fallthrough NIP code>` shape). EVERY OTHER conditional (an
optional `if state.get(x): variables[k] = ...` guard, unrelated to the flow discriminant) is
UNIONED — both arms collected — since the fence wants the FULL set a flow can ever produce, the
same choice `_CASES`'s hand-maintained `full_additive` sets already made.

CLOSED, NAMED EXCEPTIONS (checked before failing outright — grep-verified against the CURRENT
contract text, not carried over from the audit's original framing):
  - none required at the time this fence was written: every base/additive key this walk finds is
    already declared (the 4 base-input-fact declaration gaps `test_contract_provenance_parity.py`
    names as pre-existing/out-of-scope — `divergencia_valor`/CONTAS,
    `beneficiario_pseudo_id`+`canal`+`motivo_categoria`+`resumo_contexto`/INADIMPLENCIA,
    `prazo_resposta_iso`/NIP, `due_date`/ANS-SUBMIT — were ALREADY closed by `r5/contas-intake-
    gate` et al. before this fence was written; `grep -n` each name against its contract's own
    `## Variaveis de entrada` section below confirms it, so this fence carries no inherited debt).
    A future genuine gap of this shape gets a new, individually-cited entry in
    `_KNOWN_UNDECLARED_WRITES` / `_KNOWN_UNREAD_REQUIRED_INPUTS` below — never a blanket skip.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit.agents.test_contract_provenance_parity import _declared_variables

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_SRC = _REPO_ROOT / "src" / "maezo" / "agents"
_CONTRACTS_DIR = _REPO_ROOT / "docs" / "processes" / "contracts"

_ENTRADA_HEADER = "## Variaveis de entrada"
_REQUIRED_MARKERS = ("sim", "sim*", "sim§")  # legend variants seen across these 9 contracts


def _declared_required_entrada(contract_id: str) -> set[str]:
    """Variable names declared REQUIRED under `## Variaveis de entrada` only (never Saida/
    Proveniencia — those are output-only fields no read-side check applies to)."""
    text = (_CONTRACTS_DIR / f"{contract_id}.md").read_text(encoding="utf-8")
    required: set[str] = set()
    active = False
    for line in text.splitlines():
        if line.startswith("## "):
            active = line.startswith(_ENTRADA_HEADER)
            continue
        if not active or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        name = cells[0].strip("`")
        if not name or not name.replace("_", "").isalnum():
            continue
        if cells[2] in _REQUIRED_MARKERS:
            required.add(name)
    return required


def _string_const(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _reads_flow_field(node: ast.expr, flow_field: str) -> bool:
    """True if `node` is `Name(flow_field)` (a local var, e.g. marina's `flow = _flow(state)`)
    or `state.get("<flow_field>")` (gustavo's inline `state.get("fluxo")`)."""
    if isinstance(node, ast.Name) and node.id == flow_field:
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "state"
        and bool(node.args)
        and _string_const(node.args[0]) == flow_field
    )


def _flow_branch_value(test: ast.expr, flow_field: str) -> str | None:
    """If `test` is `<flow-field-expr> == "<value>"` (either operand order), return `<value>`."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
        left, right = test.left, test.comparators[0]
        if _reads_flow_field(left, flow_field):
            return _string_const(right)
        if _reads_flow_field(right, flow_field):
            return _string_const(left)
    return None


# §Delta-F4: the ONLY statement shapes `_VariableCollector._visit` actually understands. Any
# statement type outside this set found ANYWHERE in a builder (via `_check_whitelist`'s
# `ast.walk`, not just the flow-selected branch) is a BLIND SPOT — recorded, never silently
# dropped. `ast.Expr` covers both the docstring (`Expr(Constant)`) and `variables.update(...)`
# calls; a non-`.update` Expr, a `dict(...)` call, a `**`-spread dict literal, or a `dict_var`
# rebind to another Name are caught by the separate expression-level checks below (they are
# still `ast.Assign`/`ast.Expr` at the STATEMENT level, so the statement whitelist alone would
# miss them).
_HANDLED_STMT_TYPES: tuple[type[ast.stmt], ...] = (
    ast.Return,
    ast.Assign,
    ast.AnnAssign,
    ast.Expr,
    ast.If,
    ast.For,
)


class _VariableCollector:
    """Branch-aware AST walk of one contract-variable builder — see module docstring.

    §Delta-F4: `collect()` also runs `_check_whitelist`, a WHITELIST assertion over the builder's
    ENTIRE body (`ast.walk`, not just the flow-selected branch `_visit` descends into) that
    records, in `blind_spots`, any of the four previously-undisclosed shapes the fixed
    `ast.AnnAssign` bug's own self-repair uncovered as a species: a `**` spread of a Dict literal
    (`variables = {**variables, **{...}}`), a `dict(...)` builtin call
    (`variables.update(dict(k="x"))`), `dict_var` aliased to another Name via EITHER
    `ast.Assign` (`alias = variables`) OR `ast.AnnAssign` (`alias: dict[str, Any] = variables` —
    §Delta-F8: the original alias check missed this annotated form, the very Assign-vs-AnnAssign
    species the original collector bug was), or any statement type outside `_HANDLED_STMT_TYPES`
    (e.g. a `try:`/`while:`/`with:` body). A future refactor into one of these shapes now makes
    the fence FAIL LOUDLY instead of silently staying green.
    """

    def __init__(self, dict_var: str, flow_field: str | None, flow_value: str | None) -> None:
        self._dict_var = dict_var
        self._flow_field = flow_field
        self._flow_value = flow_value
        self.keys: set[str] = set()
        self.blind_spots: list[str] = []

    def _collect_dict_literal(self, node: ast.Dict) -> None:
        for key in node.keys:
            name = _string_const(key)
            if name is not None:
                self.keys.add(name)

    def _visit(self, stmt: ast.stmt) -> bool:
        """Returns True iff this statement unconditionally ends the function (a `return`)."""
        if isinstance(stmt, ast.Return):
            return True
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            # `variables: dict[str, Any] = {...}` (every builder's INITIAL declaration) is an
            # `ast.AnnAssign` (singular `.target`, `.value` optional) — a DIFFERENT node type
            # from a later plain `variables["key"] = ...` (`ast.Assign`, plural `.targets`).
            # Missing this case would silently skip the bulk unconditional dict literal every
            # single one of the 9 builders opens with.
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            value = stmt.value
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == self._dict_var
                    and isinstance(value, ast.Dict)
                ):
                    self._collect_dict_literal(value)
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == self._dict_var
                ):
                    name = _string_const(target.slice)
                    if name is not None:
                        self.keys.add(name)
            return False
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "update"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == self._dict_var
                and call.args
                and isinstance(call.args[0], ast.Dict)
            ):
                self._collect_dict_literal(call.args[0])
            return False
        if isinstance(stmt, ast.If):
            branch_value = _flow_branch_value(stmt.test, self._flow_field) if self._flow_field else None
            if branch_value is not None:
                if branch_value == self._flow_value:
                    return self._visit_body(stmt.body)
                if stmt.orelse:
                    return self._visit_body(stmt.orelse)
                return False  # no `else:` -> our flow's code is the fallthrough AFTER this `if`
            # Unrelated (non-flow-discriminating) conditional: union both arms — the fence wants
            # the FULL set of keys this flow can ever produce, optional guards included.
            self._visit_body(stmt.body)
            if stmt.orelse:
                self._visit_body(stmt.orelse)
            return False
        if isinstance(stmt, ast.For):
            # The one shape seen (gustavo `_contract_variables`'s NIP tail): `for opt in
            # ("literal_a", "literal_b"): if state.get(opt): common[opt] = ...` — every literal
            # in the iterable is a possible key regardless of the loop var not being a Constant.
            if isinstance(stmt.iter, (ast.Tuple, ast.List)):
                for element in stmt.iter.elts:
                    name = _string_const(element)
                    if name is not None:
                        self.keys.add(name)
            self._visit_body(stmt.body)
            return False
        return False

    def _visit_body(self, body: list[ast.stmt]) -> bool:
        return any(self._visit(stmt) for stmt in body)

    def _check_whitelist(self, func: ast.FunctionDef) -> None:
        """§Delta-F4: `ast.walk(func)` reaches every nested node regardless of statement type —
        including inside a `try:`/`while:`/`with:` body `_visit`'s catch-all `return False` would
        otherwise silently skip. Any of the four previously-undisclosed shapes (or any other
        statement type outside `_HANDLED_STMT_TYPES`) is appended to `self.blind_spots`, never
        silently dropped."""
        for node in ast.walk(func):
            if node is func:
                continue
            if isinstance(node, ast.stmt) and not isinstance(node, _HANDLED_STMT_TYPES):
                self.blind_spots.append(
                    f"line {node.lineno}: unhandled statement type {type(node).__name__} "
                    f"(outside {[t.__name__ for t in _HANDLED_STMT_TYPES]})"
                )
            elif isinstance(node, ast.Dict) and any(key is None for key in node.keys):
                self.blind_spots.append(
                    f"line {node.lineno}: dict literal contains a `**` spread — a nested key "
                    "reached only through the spread is never collected"
                )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
                self.blind_spots.append(
                    f"line {node.lineno}: `dict(...)` builtin call — its keys are never collected"
                )
            elif (
                # §Delta-F8: BOTH `alias = variables` (`ast.Assign`) AND `alias: dict[str, Any] =
                # variables` (`ast.AnnAssign`) bind `dict_var` to another name — the very
                # Assign-vs-AnnAssign species that caused the original self-disclosed collector
                # bug (every one of the 7 builders opens with an ANNOTATED `variables: dict[str,
                # Any] = {...}`, so an annotated alias is the idiomatic shape, not an edge case).
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(node.value, ast.Name)
                and node.value.id == self._dict_var
                and not any(
                    isinstance(target, ast.Name) and target.id == self._dict_var
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
            ):
                self.blind_spots.append(
                    f"line {node.lineno}: `{self._dict_var}` is aliased to another name — "
                    "writes through the alias are never collected"
                )

    def collect(self, func: ast.FunctionDef) -> set[str]:
        self._check_whitelist(func)
        self._visit_body(func.body)
        return self.keys


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function/method {name!r} not found")


def _agent_module_tree(agent_id: str) -> ast.Module:
    source = (_AGENTS_SRC / agent_id / "graph.py").read_text(encoding="utf-8")
    return ast.parse(source, filename=f"{agent_id}/graph.py")


def _module_reads_key(tree: ast.Module, key: str, *, state_names: frozenset[str]) -> bool:
    """True iff ANY `<state_name>.get("<key>", ...)` or `<state_name>["<key>"]` appears anywhere
    in the module — the read side is not flow-scoped: a required input is typically consumed by
    an earlier node (`gather`/`assess`) than the one that re-embarks it into engine `variables`,
    so this is deliberately whole-module, not restricted to one function's AST like the write
    side's branch-aware walk."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in state_names
            and node.args
            and _string_const(node.args[0]) == key
        ):
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in state_names
            and _string_const(node.slice) == key
        ):
            return True
    return False


# (agent_id, builder method name, dict-literal variable name, contract_id, flow_field, flow_value)
# Mirrors `test_contract_provenance_parity.py::_CASES`'s 9 pairs exactly (same agents, same
# contracts, same marina-reembolso exclusion — reembolso's start is a no-op, per that file's own
# docstring, so there is no builder call to check).
_GENERIC_CASES: list[tuple[str, str, str, str, str | None, str | None]] = [
    ("rafael", "_contract_variables", "variables", "SP-OP-AUTH-001", None, None),
    ("marina", "_contract_variables", "variables", "SP-OP-CONTAS-001", "flow", "contas"),
    ("marina", "_contract_variables", "variables", "SP-OP-RECURSO-001", "flow", "recurso"),
    ("carolina", "_contract_variables", "variables", "SP-OP-CRED-001", None, None),
    ("lucas", "_escalation_variables", "variables", "SP-OP-ESCALATION-001", None, None),
    ("gustavo", "_contract_variables", "common", "SP-OP-NIP-001", "fluxo", "nip"),
    ("gustavo", "_contract_variables", "common", "SP-OP-ANS-SUBMIT-001", "fluxo", "ans_submit"),
    ("valentina", "_contract_variables", "variables", "SP-OP-PROGRAMA-001", None, None),
    ("fernando", "_inadimplencia_variables", "variables", "SP-OP-INADIMPLENCIA-001", None, None),
]

# Closed, individually-cited exceptions — see module docstring. Empty at the time this fence was
# written (every gap the audit/CC-13 previously disclosed for these 9 pairs was already closed on
# `main` before this file existed); a real future drift gets its OWN entry here, never a blanket
# skip, and CI still fails until it is either fixed or named.
_KNOWN_UNDECLARED_WRITES: dict[tuple[str, str], frozenset[str]] = {}

# READ-direction exceptions. Every entry here is a variable this contract's OWN "## Variaveis de
# entrada" table files as REQUIRED, but that is NOT a caller-supplied fact this agent's graph.py
# passes through via `state.get(name)` — each row's own prose says why (grep-verified, cited by
# contract line below), never a blanket "the check is noisy" skip:
#   - `source_agent_id`/`source_agent_version` (ADR-0007 non-repudio): every one of the 9 pairs
#     hardcodes these as a LITERAL agent-id string / `self._agent_version` attribute — never a
#     `state.get(...)` passthrough. 8 of the 9 contracts file them under the agent-specific
#     "## Variaveis de proveniencia" section (outside this check's scope entirely); ONLY
#     SP-OP-ESCALATION-001 (shared by helena AND lucas) files them under the BASE "## Variaveis
#     de entrada" table instead — because that table declares what ANY starter of the SHARED
#     contract must supply, not what lucas's own code reads back from its caller.
#   - `resumo_contexto` (lucas ESCALATION-001:26, fernando INADIMPLENCIA-001:80): both rows'
#     own descriptions say it is "escrito pelo agente"/derived from `dossier.narrativa` — a
#     value the agent COMPUTES and writes, never a key it reads verbatim from `state`.
#   - `data_vencimento` (marina CONTAS-001:70, RECURSO-001:77): the row's own text says
#     "Produzida no runtime pelo INTAKE" (`operadora.contas.identify_glosa`/`ST_ApurarDivergencias`,
#     a DIFFERENT process participant) — marina's graph never touches this variable at all.
_KNOWN_UNREAD_REQUIRED_INPUTS: dict[tuple[str, str], frozenset[str]] = {
    ("rafael", "SP-OP-AUTH-001"): frozenset(),
    ("marina", "SP-OP-CONTAS-001"): frozenset({"data_vencimento"}),
    ("marina", "SP-OP-RECURSO-001"): frozenset({"data_vencimento"}),
    ("carolina", "SP-OP-CRED-001"): frozenset(),
    ("lucas", "SP-OP-ESCALATION-001"): frozenset(
        {"source_agent_id", "source_agent_version", "resumo_contexto"}
    ),
    ("gustavo", "SP-OP-NIP-001"): frozenset(),
    ("gustavo", "SP-OP-ANS-SUBMIT-001"): frozenset(),
    ("valentina", "SP-OP-PROGRAMA-001"): frozenset(),
    ("fernando", "SP-OP-INADIMPLENCIA-001"): frozenset({"resumo_contexto"}),
}


@pytest.mark.parametrize(
    ("agent_id", "method", "dict_var", "contract_id", "flow_field", "flow_value"),
    _GENERIC_CASES,
    ids=[f"{c[0]}->{c[3]}" for c in _GENERIC_CASES],
)
def test_every_engine_variable_the_graph_writes_is_declared_in_its_contract(
    agent_id: str,
    method: str,
    dict_var: str,
    contract_id: str,
    flow_field: str | None,
    flow_value: str | None,
) -> None:
    tree = _agent_module_tree(agent_id)
    func = _find_function(tree, method)
    collector = _VariableCollector(dict_var, flow_field, flow_value)
    written = collector.collect(func)
    assert written, f"{agent_id}::{method}: AST walk found ZERO keys — extractor regressed, not a real gap"
    assert not collector.blind_spots, (
        f"{agent_id}::{method}: _VariableCollector's whitelist found shape(s) it cannot inspect "
        f"(§Delta-F4) — {collector.blind_spots}. Extend `_VariableCollector` to handle the shape, "
        f"or refactor the builder to avoid it; do NOT ignore this, a blind spot here means a "
        f"future undeclared engine variable could slip through this fence unnoticed."
    )

    declared = _declared_variables(contract_id)
    exception = _KNOWN_UNDECLARED_WRITES.get((agent_id, contract_id), frozenset())
    undeclared = sorted((written - declared) - exception)
    assert not undeclared, (
        f"{agent_id}/{contract_id}: engine variable(s) {undeclared} are written by "
        f"{agent_id}/graph.py::{method} but {contract_id}.md declares none of them under "
        f"'## Variaveis de entrada' / '## Variaveis de saida' / '## Variaveis de proveniencia' "
        f"(NEW-C1-3 — CC-13's own disclosed generic-fence follow-up). Declare the variable in "
        f"the contract (spec-first, C3) or, if it should not exist, remove it from the builder."
    )
    stale_exception = sorted(exception - (written - declared))
    assert not stale_exception, (
        f"{agent_id}/{contract_id}: _KNOWN_UNDECLARED_WRITES lists {stale_exception} as still "
        f"undeclared, but it is now either declared or no longer written — drop the stale entry."
    )


@pytest.mark.parametrize(
    ("agent_id", "contract_id"),
    sorted({(c[0], c[3]) for c in _GENERIC_CASES}),
)
def test_every_required_contract_input_is_read_somewhere_in_the_graph(
    agent_id: str, contract_id: str
) -> None:
    tree = _agent_module_tree(agent_id)
    required = _declared_required_entrada(contract_id)
    assert required, f"{contract_id}: zero REQUIRED 'Variaveis de entrada' rows found — parser regressed"

    exception = _KNOWN_UNREAD_REQUIRED_INPUTS.get((agent_id, contract_id), frozenset())
    raw_unread = {
        key for key in required if not _module_reads_key(tree, key, state_names=frozenset({"state"}))
    }
    unread = sorted(raw_unread - exception)
    assert not unread, (
        f"{agent_id}/{contract_id}: {contract_id}.md declares {unread} REQUIRED under "
        f"'## Variaveis de entrada', but {agent_id}/graph.py never reads it via "
        f"state.get(...)/state[...] anywhere (NEW-C1-3). Either the graph is missing the read, "
        f"or the contract over-declares — fix the direction the contract states (C3)."
    )
    stale_exception = sorted(exception - raw_unread)
    assert not stale_exception, (
        f"{agent_id}/{contract_id}: _KNOWN_UNREAD_REQUIRED_INPUTS lists {stale_exception} as "
        f"still unread, but the graph now reads it — drop the stale entry."
    )
