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
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final, NamedTuple

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_AGENTS_DIR: Final[Path] = _REPO_ROOT / "src" / "maezo" / "agents"


class _Hit(NamedTuple):
    relpath: str
    lineno: int


#: Every `<expr> or 0`/`<expr> or 0.0` occurrence in `src/maezo/agents/*/graph.py` that predates
#: this WP and is OUT OF SCOPE for it (not one of BEA-04/VAL-05/AND-08/FER-08's named fields) —
#: reviewed here so it cannot silently multiply. Fixing one is a DIFFERENT WP's ledger row; when
#: it lands, remove the entry (the stale-entry test below fails otherwise).
_ALLOWLIST: Final[dict[_Hit, str]] = {
    _Hit("fernando/graph.py", 573): (
        "meses_inadimplencia (assess): pre-existing convention predating fleet audit ciclo 2; "
        "not one of BEA-04/VAL-05/AND-08/FER-08's named fields (those are score_indicadores, "
        "the consent desfecho, decisao_pagamento, and the *_iso SLA/deadline fields) — a "
        "residual of the SAME family, out of this WP's scope, not fixed here."
    ),
    _Hit("fernando/graph.py", 939): (
        "meses_inadimplencia (_inadimplencia_variables, engine-bound): same field/reason as "
        "line 573; also inside `_inadimplencia_variables`, which sibling WP "
        "FERNANDO-INPUT-DESFECHO is actively revising in its own worktree — not touched here."
    ),
    _Hit("fernando/graph.py", 940): (
        "valor_total_devido_cents (_inadimplencia_variables, engine-bound): same shape/reason "
        "as line 939 — a money field defaulting to 0 by the same pre-existing convention, out "
        "of this WP's named scope."
    ),
    _Hit("gustavo/graph.py", 656): (
        "prazo_dias (DMN row read in classify/escalation informational context): pre-existing "
        "convention predating fleet audit ciclo 2, not one of this WP's named fields."
    ),
    _Hit("lucas/graph.py", 651): (
        "ciclos_sem_conciliacao (assess): pre-existing convention predating fleet audit ciclo "
        "2 — the sibling gap LUCAS-MOTIVO-SEVERIDADE-DEFAULTS (GAP-REGISTER, MERGED #336) "
        "closed a DIFFERENT default in the same file (severidade), not this counter."
    ),
    _Hit("lucas/graph.py", 700): (
        "ciclos_sem_conciliacao (a second read site, same field/reason as line 651)."
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


class _OrZeroVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.linenos: list[int] = []

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # `a or b or 0` parses as ONE BoolOp with 3+ `values` — only the LAST value being a zero
        # constant matters (that is the coercion this fence guards against); `0 or b` (zero
        # first) is a different, much rarer shape and not what `<expr> or 0` means colloquially.
        if isinstance(node.op, ast.Or) and len(node.values) >= 2 and _is_zero_constant(node.values[-1]):
            self.linenos.append(node.lineno)
        self.generic_visit(node)


def _scan(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _OrZeroVisitor()
    visitor.visit(tree)
    return visitor.linenos


def _all_hits() -> set[_Hit]:
    hits: set[_Hit] = set()
    for graph_path in sorted(_AGENTS_DIR.glob("*/graph.py")):
        relpath = f"{graph_path.parent.name}/graph.py"
        for lineno in _scan(graph_path):
            hits.add(_Hit(relpath, lineno))
    return hits


def test_every_or_zero_coercion_is_reviewed_and_allowlisted() -> None:
    """Every `<expr> or 0` idiom found by the AST scan today must be a REVIEWED, NAMED entry —
    a brand-new occurrence (a fresh footgun, or a mutation reintroducing a fixed one) fails here
    instead of shipping silently."""
    hits = _all_hits()
    unlisted = hits - set(_ALLOWLIST)
    assert not unlisted, (
        f"unreviewed `or 0` coercion(s) in agent graph(s), not in _ALLOWLIST: {sorted(unlisted)} "
        "-- either this is a NEW instance of NONE-GUARDRAIL-MISSING (fix it, do not allowlist "
        "it), or it is a legitimate default that needs a reviewed reason added to _ALLOWLIST."
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """The inverse direction: an allowlist entry whose code no longer exists (fixed, moved, or a
    typo'd line number) must be REMOVED, not left as a dead exception that could mask a future
    unrelated occurrence landing on the same line number by coincidence."""
    hits = _all_hits()
    stale = set(_ALLOWLIST) - hits
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
    visitor = _OrZeroVisitor()
    visitor.visit(tree)
    assert not visitor.linenos, "`_score_consumed` must reject via `require_number`, never `or 0`"
