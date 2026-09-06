"""The NONE-GUARDRAIL-MISSING fence (fleet audit ciclo 2: BEA-04/VAL-05/AND-08/FER-08).

WHY THIS FILE EXISTS. The family's defect shape is always the same: a pre-resolved worker/DMN
fact (or a caller-input field) the graph only CONSUMES silently collapses, when absent or the
wrong type, to a value that reads exactly like a REAL fact (`0`, `False`) instead of surfacing a
lacuna. `BEA-04` was the textbook instance: `_score_consumed` returned `0` for a corrupted
`score_indicadores`, and the sealed dossier a human investigator reads could not tell "genuinely
zero indicators" from "the worker's score was garbage" apart. `maezo.runtime.guards` closes the
per-field instances this WP was scoped to; THIS fence closes the class going forward — it scans
every `src/maezo/agents/*/graph.py` for the most common vector of the same defect, the literal
`<expr> or 0` idiom (bare, or wrapped in `int(...)`/`float(...)`), and fails the build on any
occurrence NOT already reviewed and named in `_ALLOWLIST` below.

WHAT THIS FENCE DOES NOT CLAIM. It is a syntactic net for ONE idiom (`... or 0`), not a general
proof that every numeric/boolean field in the fleet is guarded — `_score_consumed`'s OWN fix uses
an `isinstance` chain, not `or 0`, so this fence does not (and is not meant to) re-prove BEA-04;
that is `tests/unit/agents/test_beatriz.py`'s job. It also does not flag `.get(key, 0)`
two-argument defaults with no trailing `or 0` (a DIFFERENT shape, e.g. `AND-07`'s
`int(state.get("valor_pagamento_cents", 0))` — tracked by a sibling WP, not this fence: adding it
here would silently absorb a finding this WP does not own).

THE ALLOWLIST IS CLOSED AND BIDIRECTIONAL: every `or 0` idiom the AST finds today must be a
KEY in `_ALLOWLIST` with a reason (`test_every_or_zero_coercion_is_reviewed_and_allowlisted`), and
every allowlist key must still be found in the AST (`test_the_allowlist_has_no_stale_entries`) —
so a fixed occurrence must be REMOVED here, not left as a dead exception, and a NEW occurrence
must be reviewed here, not silently introduced.

STABLE IDENTITY, NEVER `lineno` (§Delta NONE-GUARDRAIL, repair of `VERIFY-NONE-GUARDRAIL.md` F1).
The allowlist used to be keyed by `(relpath, lineno)`. That breaks on any edit ABOVE an allowlisted
line — including an in-flight SIBLING worktree editing the very same file for an unrelated reason.
Measured, not hypothetical: merging this WP's tip against `fleet2/fernando-input-desfecho`
(`c1640861`) shifts `fernando/graph.py`'s three allowlisted `or 0` sites from lines 573/939/940 to
701/1085/1086 with ZERO semantic conflict (both sides add independent helpers at the same anchor)
— under the old line-keyed scheme this makes BOTH direction tests fail on a merge that changes
nothing about any allowlisted site. `_Hit` is now `(relpath, func, key)`: `func` is the innermost
enclosing `def`/`async def` name, and `key` is the literal dict key this `or 0` expression is the
value of (walking up through any `int(...)`/`float(...)` wrapper) — or, when the expression is not
a dict value at all, the normalized source text of the whole expression. Neither component moves
when unrelated lines are inserted or removed elsewhere in the file. `lineno` is kept ONLY inside
failure messages, for a human to find the current line — never as part of the identity itself.

OCCURRENCE COUNT IS PART OF THE IDENTITY'S REVIEW (§Delta NONE-GUARDRAIL D2). Two DIFFERENT
`or 0` occurrences can legitimately share one `(relpath, func, key)` identity (the same dict key
assigned twice inside the same function is unusual but not impossible) — collapsing them would let
a SECOND, unreviewed occurrence hide behind an already-allowlisted one, exactly the "silently
multiply" this fence exists to prevent. `_ALLOWLIST` therefore declares, per identity, how many
occurrences were reviewed (`_Allowed.count`); `test_every_or_zero_coercion_is_reviewed_and_allowlisted`
compares the ACTUAL count found (`len(hits[hit])`) against it, not just set membership.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final, NamedTuple

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_AGENTS_DIR: Final[Path] = _REPO_ROOT / "src" / "maezo" / "agents"


class _Hit(NamedTuple):
    """Stable identity of an `<expr> or 0` occurrence — see the module docstring's STABLE
    IDENTITY section for why `lineno` is deliberately absent from this tuple."""

    relpath: str
    func: str
    key: str


class _Allowed(NamedTuple):
    """One allowlist entry: how many occurrences at this identity were reviewed (see the module
    docstring's OCCURRENCE COUNT section), and why they are out of this WP's scope."""

    count: int
    reason: str


#: Every `<expr> or 0`/`<expr> or 0.0` occurrence in `src/maezo/agents/*/graph.py` that predates
#: this WP and is OUT OF SCOPE for it (not one of BEA-04/VAL-05/AND-08/FER-08's named fields) —
#: reviewed here so it cannot silently multiply. Fixing one is a DIFFERENT WP's ledger row; when
#: it lands, remove the entry (the stale-entry test below fails otherwise).
_ALLOWLIST: Final[dict[_Hit, _Allowed]] = {
    _Hit("fernando/graph.py", "assess", "meses_inadimplencia"): _Allowed(
        1,
        "meses_inadimplencia (assess) -- DEAD-COLUMN residual, not a live masking: feeds "
        "`inadimplencia_status.dmn`'s `in_meses_inadimplencia` input column, which is `-` "
        "(wildcard) in EVERY rule and read by no output expression -- a frozen DEAD input per "
        "`tests/unit/agents/test_dmn_dead_inputs_fence.py::KNOWN_DEAD_INPUTS`, row "
        '("inadimplencia_status.dmn", "in_meses_inadimplencia", "meses_inadimplencia"). The DMN '
        "never actually reads this fact, so the `or 0` masking has NO decision-input effect "
        "today (unlike lucas:assess below, which is a LIVE masking). Pre-existing convention "
        "predating fleet audit ciclo 2; not one of BEA-04/VAL-05/AND-08/FER-08's named fields "
        "(those are score_indicadores, the consent desfecho, decisao_pagamento, and the *_iso "
        "SLA/deadline fields) -- a residual of the SAME family, out of this WP's scope, not "
        "fixed here.",
    ),
    _Hit("fernando/graph.py", "_inadimplencia_variables", "meses_inadimplencia"): _Allowed(
        1,
        "meses_inadimplencia (_inadimplencia_variables, engine-bound): a SEPARATE read site of "
        "the same field, feeding the ENGINE PROCESS VARIABLE `meses_inadimplencia` -- "
        "informational (the engine's own businessRuleTask evaluation of "
        "`inadimplencia_status`/`inadimplencia_purga`/`inadimplencia_sla` reads its OWN process "
        "variables via `${...}`, never re-reads this one back as a DMN input). Also inside "
        "`_inadimplencia_variables`, which sibling WP FERNANDO-INPUT-DESFECHO is actively "
        "revising in its own worktree -- not touched here.",
    ),
    _Hit("fernando/graph.py", "_inadimplencia_variables", "valor_total_devido_cents"): _Allowed(
        1,
        "valor_total_devido_cents (_inadimplencia_variables, engine-bound): a MONEY field "
        "silently defaulting to 0 by the same pre-existing convention as meses_inadimplencia "
        "above -- worth saying out loud (a null 'valor devido' masked to 0 could read to a "
        "human as 'nothing owed' rather than 'the fact is missing'), same engine-process-"
        "variable/informational shape, out of this WP's named scope.",
    ),
    _Hit("gustavo/graph.py", "_assess_nip", "prazo_dias"): _Allowed(
        1,
        'prazo_dias (_assess_nip): read FROM a DMN row OUTPUT (`clf_row.get("prazo_dias")`, a '
        "value the `nip_classification` table's OWN matched rule already produced), never a DMN "
        "INPUT -- this `or 0` cannot mask a fact the DMN evaluates against, so it has no "
        "decision-input effect. Pre-existing convention predating fleet audit ciclo 2, not one "
        "of this WP's named fields.",
    ),
    _Hit("lucas/graph.py", "assess", "ciclos_sem_conciliacao"): _Allowed(
        1,
        "ciclos_sem_conciliacao (assess) -- RESIDUAL-ABERTO: LIVE DMN decision-input masking "
        '(§Delta NONE-GUARDRAIL F2). Feeds `admis_in["ciclos_sem_conciliacao"]` into '
        "`lucas_billing_admissibility.dmn` (hitPolicy=FIRST). That table's FIRST rule, "
        "`lba_r_atraso_escala`, fires when `ciclos_sem_conciliacao >= 1` and routes to "
        "`ESCALAR_HUMANO` -- the ONLY input that can trigger this table's human-escalation "
        "rule. A present-but-NULL `ciclos_sem_conciliacao` is masked to `0` here, so "
        "`0 >= 1` is false, `lba_r_atraso_escala` never fires, and the case instead falls "
        "through to whichever LATER rule matches (e.g. `lba_r_vencimento` -> `LEMBRETE`, a "
        "merely-informational reminder) or the conservative catch-all `ESCALAR_HUMANO` for a "
        "truly unmapped combination -- i.e. the masking can move a case AWAY from the exact "
        "human-escalation rule that exists to catch a delinquency indicator. This is the same "
        "NONE-GUARDRAIL-MISSING harm this WP exists to end, living in a field this WP was NOT "
        "scoped to fix (BEA-04/VAL-05/AND-08/FER-08 are score_indicadores, the consent "
        "desfecho, decisao_pagamento, and the *_iso SLA/deadline fields -- not this counter). "
        "Fixing the CODE at this site is OUT of this WP's scope: the honest fix is a "
        "null-handling rule inside `lucas_billing_admissibility.dmn` itself (CODEOWNED under "
        "`spec/processes/dmn/`, owner decision) -- routing a lacuna to human escalation from "
        "Python instead would move a business rule OUT of the DMN (C3). Registered here as an "
        "OPEN residual for a future owner-gated WP to pick up (see this WP's report's "
        "adjacencies section); the sibling gap LUCAS-MOTIVO-SEVERIDADE-DEFAULTS "
        "(GAP-REGISTER, MERGED #336) closed a DIFFERENT default in the same file (severidade), "
        "not this counter.",
    ),
    _Hit("lucas/graph.py", "_assess_escalation", "ciclos_sem_conciliacao"): _Allowed(
        1,
        "ciclos_sem_conciliacao (_assess_escalation) -- DEAD-COLUMN residual, not a live "
        "masking: a SECOND read site of the same field, feeding `lucas_escalation_routing.dmn`'s "
        "`ler_in_ciclos` input column, which is `-` (wildcard) in EVERY rule and read by no "
        "output expression -- a frozen DEAD input per "
        "`tests/unit/agents/test_dmn_dead_inputs_fence.py::KNOWN_DEAD_INPUTS`, row "
        '("lucas_escalation_routing.dmn", "ler_in_ciclos", "ciclos_sem_conciliacao"). Unlike '
        "the `assess` site above, this table never reads the column at all (dead) AND the "
        "destination here is ALWAYS `escalate_human` regardless of this DMN's outcome -- it "
        "only picks the suggested human GROUP (`_assess_escalation`'s own docstring) -- so the "
        "`or 0` masking has no decision-input effect at this second site. Same field/family as "
        "the LIVE `assess` site above; out of this WP's named scope either way.",
    ),
}


def _is_zero_constant(node: ast.expr) -> bool:
    """`0`/`0.0` only — never `False` (a different, unrelated idiom: `or False` guards a
    BOOLEAN default, not a numeric one, and is out of this fence's declared scope)."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value == 0
    )


def _find_or_zero_nodes(tree: ast.AST) -> list[ast.BoolOp]:
    """Every `<expr> or 0`/`<expr> or 0.0` `BoolOp` node in `tree`.

    `a or b or 0` parses as ONE `BoolOp` with 3+ `values` — only the LAST value being a zero
    constant matters (that is the coercion this fence guards against); `0 or b` (zero first) is a
    different, much rarer shape and not what `<expr> or 0` means colloquially, so it is not
    flagged here."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.Or)
        and len(node.values) >= 2
        and _is_zero_constant(node.values[-1])
    ]


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Every node's parent, computed once per file scan into a plain dict keyed by node identity
    (`ast.AST` never overrides `__eq__`/`__hash__`, so default identity hashing is exactly what a
    parent lookup needs). Deliberately NOT a `.parent` attribute monkey-patched onto the nodes
    (§Delta NONE-GUARDRAIL D1): that shape needs a mypy suppression naming the undeclared attribute
    (`ast.AST` has none such) -- or, swapping the plain assignment for `setattr`, trips ruff's B010
    ("do not call setattr with a constant attribute value") instead. There was no zero-suppression
    way to keep either shape. A plain dict avoids both categorically."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _normalize(text: str) -> str:
    """Collapse whitespace so a purely cosmetic reformat (line wrap, extra space) of the SAME
    expression does not change its identity."""
    return " ".join(text.split())


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    """The innermost enclosing `def`/`async def` name, or `"<module>"` if the node sits at
    module level (e.g. a module-level constant's `or 0`, which none of today's hits are, but the
    scan must not crash if one ever appears)."""
    current: ast.AST | None = parents.get(node)
    while current is not None and not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
        current = parents.get(current)
    if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return current.name
    return "<module>"


def _dict_key_or_source(node: ast.expr, source: str, parents: dict[ast.AST, ast.AST]) -> str:
    """The dict key this expression is the VALUE of — walking up through any `int(...)`/
    `float(...)` wrapper to find the nearest enclosing `ast.Dict` whose `values` entry is on the
    path back down to `node` — or, when no enclosing dict entry is found at all, the normalized
    source text of the expression itself. Either way the result never depends on a line number."""
    prev: ast.AST = node
    current: ast.AST | None = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Dict):
            for key_node, value_node in zip(current.keys, current.values, strict=True):
                if value_node is prev:
                    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                        return key_node.value
                    if key_node is not None:
                        return _normalize(ast.get_source_segment(source, key_node) or "<dict-key>")
                    return "<dict-spread-value>"
        prev = current
        current = parents.get(current)
    return _normalize(ast.get_source_segment(source, node) or "<unknown>")


def _scan(path: Path) -> dict[_Hit, list[int]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    parents = _parent_map(tree)
    relpath = f"{path.parent.name}/graph.py"
    hits: dict[_Hit, list[int]] = {}
    for node in _find_or_zero_nodes(tree):
        hit = _Hit(relpath, _enclosing_function(node, parents), _dict_key_or_source(node, source, parents))
        hits.setdefault(hit, []).append(node.lineno)
    return hits


def _all_hits() -> dict[_Hit, list[int]]:
    hits: dict[_Hit, list[int]] = {}
    for graph_path in sorted(_AGENTS_DIR.glob("*/graph.py")):
        for hit, linenos in _scan(graph_path).items():
            hits.setdefault(hit, []).extend(linenos)
    return hits


def test_every_or_zero_coercion_is_reviewed_and_allowlisted() -> None:
    """Every `<expr> or 0` idiom found by the AST scan today must be a REVIEWED, NAMED entry —
    a brand-new occurrence (a fresh footgun, or a mutation reintroducing a fixed one) fails here
    instead of shipping silently. Includes the OCCURRENCE COUNT (§Delta NONE-GUARDRAIL D2): a
    SECOND `or 0` sharing an already-allowlisted identity is exactly as unreviewed as a brand-new
    identity would be, and must fail here too, not hide behind the existing entry."""
    hits = _all_hits()
    unlisted = set(hits) - set(_ALLOWLIST)
    assert not unlisted, (
        "unreviewed `or 0` coercion(s) in agent graph(s), not in _ALLOWLIST (current line(s) in "
        f"parens): {sorted((hit, hits[hit]) for hit in unlisted)} "
        "-- either this is a NEW instance of NONE-GUARDRAIL-MISSING (fix it, do not allowlist "
        "it), or it is a legitimate default that needs a reviewed reason added to _ALLOWLIST."
    )
    miscounted = {
        hit: {"found": len(hits[hit]), "found_at_lines": hits[hit], "reviewed": _ALLOWLIST[hit].count}
        for hit in hits
        if hit in _ALLOWLIST and len(hits[hit]) != _ALLOWLIST[hit].count
    }
    assert not miscounted, (
        "allowlisted identity(ies) whose occurrence COUNT no longer matches what was reviewed: "
        f"{miscounted} -- a second (or a removed) `or 0` at an already-allowlisted (relpath, func, "
        "key) must be reviewed on its own, never silently absorbed by an existing entry that "
        "shares its identity."
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """The inverse direction: an allowlist entry whose code no longer exists (fixed, moved, or
    renamed) must be REMOVED, not left as a dead exception that could mask a future unrelated
    occurrence landing on the same identity by coincidence."""
    hits = _all_hits()
    stale = set(_ALLOWLIST) - set(hits)
    assert not stale, f"stale _ALLOWLIST entries (no matching code found): {sorted(stale)}"


def test_beatriz_score_consumed_does_not_use_the_or_zero_idiom() -> None:
    """BEA-04's OWN fix (`_score_consumed`, `maezo.runtime.guards.require_number`) must never
    regress to the exact idiom this fence exists to catch — a targeted, load-bearing pin on top
    of the fleet-wide scan above."""
    path = _AGENTS_DIR / "beatriz" / "graph.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("def _score_consumed(")
    end = source.index("\n\n\n", start)
    body = source[start:end]
    tree = ast.parse(body)
    assert not _find_or_zero_nodes(tree), "`_score_consumed` must reject via `require_number`, never `or 0`"
