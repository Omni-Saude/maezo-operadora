"""CC-15: `key_segment`/`is_blank` promoted from `agents.andre.keys` to `maezo.platform.keys`.

CONTRACT PINNED HERE (see `maezo.platform.keys` module docstring for the full rationale):
  - position-preserving: normalisation never drops, reorders or truncates a segment;
  - refuse-on-blank: `is_blank` is the single predicate a caller uses to decide whether to raise,
    so a blank required segment is always a caller-visible condition, never silently coerced into
    an empty-string segment that a composer could go on to embed in a key;
  - never segment-dropping: `key_segment`/`is_blank` themselves never raise and never omit a
    segment — refusal is the CALLER's job, built on top of `is_blank`.

Also pins the REEXPORT (not copy) at `agents.andre.keys`, and — as a DISCLOSURE fence, not a
blocking gate — the closed inventory of the 10 agent graphs' own `_business_key` composers: which
ones already compose through `key_segment` and which are tracked in `_NOT_YET_ADOPTED` pending a
per-graph adoption WP (CC-15 fix sketch: "adocao por grafo = follow-up").
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.keys import is_blank, key_segment

# ---------------------------------------------------------------------------
# key_segment: position-preserving, never segment-dropping normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("R-001", "R-001", id="plain"),
        pytest.param(" R-001 ", "R-001", id="padded"),
        pytest.param("\tR-001\n", "R-001", id="tab-and-newline-padded"),
        pytest.param(123, "123", id="int"),
        pytest.param(0, "", id="zero-int"),
        pytest.param(0.0, "", id="zero-float"),
        pytest.param(None, "", id="none"),
        pytest.param("", "", id="empty-string"),
        pytest.param("   ", "", id="whitespace-only"),
        pytest.param(
            "\xa0", "", id="nbsp-only"
        ),  # non-breaking space IS whitespace under Python's unicode-aware strip
        pytest.param("héllo", "héllo", id="unicode"),
        pytest.param(" héllo ", "héllo", id="unicode-padded"),
        pytest.param(False, "", id="false"),
        pytest.param(True, "True", id="true"),
    ],
)
def test_key_segment_normalises(value: Any, expected: str) -> None:
    assert key_segment(value) == expected


def test_key_segment_never_turns_none_into_the_literal_string_none() -> None:
    """The M-8-adjacent defect `tools/workers/base.py:non_blank` documents: `str(None)` is the
    literal `"None"`, which is a NON-blank 4-character segment that would silently anchor a key."""
    assert key_segment(None) == ""
    assert key_segment(None) != "None"


def test_key_segment_preserves_position_never_collapses_distinct_shapes() -> None:
    """The exact M-8 collision vector: composing WITHOUT dropping empty segments keeps a
    3-segment and a would-be 4-segment-with-blank-middle shape structurally distinguishable,
    because the blank segment normalises to `""`, not to something absent."""
    a = "-".join(key_segment(v) for v in ("R-001", "2026-Q3", ""))
    b = "-".join(key_segment(v) for v in ("R-001", "", "2026-Q3"))
    assert a == "R-001-2026-Q3-"
    assert b == "R-001--2026-Q3"
    assert a != b  # position preserved -> no collision, unlike a segment-dropping join


def test_key_segment_is_idempotent() -> None:
    for value in ("R-001", " R-001 ", 123, None, "", "  x  "):
        once = key_segment(value)
        assert key_segment(once) == once


# ---------------------------------------------------------------------------
# is_blank: the single refuse-on-blank predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("", True, id="empty-string"),
        pytest.param("   ", True, id="whitespace-only"),
        pytest.param(None, True, id="none"),
        pytest.param(0, True, id="zero-int"),
        pytest.param(False, True, id="false"),
        pytest.param("x", False, id="single-char"),
        pytest.param(" x ", False, id="padded-single-char"),
        pytest.param(123, False, id="int"),
        # the STRING "0" is a real, non-blank segment
        pytest.param("0", False, id="string-zero"),
        pytest.param(True, False, id="true"),
    ],
)
def test_is_blank(value: Any, expected: bool) -> None:
    assert is_blank(value) is expected


def test_is_blank_is_defined_purely_in_terms_of_key_segment() -> None:
    """Pins the relationship, not just the outcome: `is_blank` must not have its own drifted
    notion of blank (e.g. only checking falsiness) once implementations move independently."""
    for value in ("", "  ", None, 0, "x", 123, "  x  "):
        assert is_blank(value) == (key_segment(value) == "")


def test_neither_function_raises_or_drops_a_segment() -> None:
    """`key_segment`/`is_blank` never refuse on their own — refusal is composed by the CALLER on
    top of `is_blank`. A caller-facing composer library (e.g. `agents.andre.keys`) is expected to
    raise; this shared primitive layer never does."""
    for value in (None, "", "   ", 0, object(), [], {}):
        key_segment(value)  # must not raise
        is_blank(value)  # must not raise


# ---------------------------------------------------------------------------
# Reexport identity: agents.andre.keys must NOT hold a copy
# ---------------------------------------------------------------------------


def test_andre_keys_reexports_the_same_object_not_a_copy() -> None:
    andre_keys = importlib.import_module("maezo.agents.andre.keys")
    platform_keys = importlib.import_module("maezo.platform.keys")

    assert andre_keys.key_segment is platform_keys.key_segment
    assert andre_keys.is_blank is platform_keys.is_blank


def test_andre_composers_still_behave_identically_through_the_reexport() -> None:
    """Behavioural pin, independent of identity: the M-8 fix `andre.keys` shipped must be
    byte-identical after the move, not just import-compatible."""
    from maezo.agents.andre.keys import adequacao_business_key, pagto_business_key

    assert adequacao_business_key("amh", "R-001", "cardiologia") == "ADEQ-amh-R-001-cardiologia"
    with pytest.raises(ValueError):
        adequacao_business_key("amh", "R-001", "", "2026-Q3")

    assert pagto_business_key("amh", ordem_pagamento_id="123") == "PAGTO-amh-123"
    with pytest.raises(ValueError):
        pagto_business_key("amh")


def test_contas_worker_imports_key_segment_from_the_shared_platform_module() -> None:
    """CC-15's dependency-inversion fix: a worker must not import from an agent package."""
    contas = importlib.import_module("maezo.tools.workers.contas")
    platform_keys = importlib.import_module("maezo.platform.keys")
    assert contas.key_segment is platform_keys.key_segment


# ---------------------------------------------------------------------------
# DISCLOSURE FENCE (not blocking): closed inventory of the 10 graphs' _business_key composers.
#
# This is NOT a silent allowlist: it is a closed set that FAILS if a new graph shows up
# unclassified, and it SHRINKS (an agent moves out of _NOT_YET_ADOPTED into the "adopted" set) as
# each per-graph adoption WP lands. Adoption itself is explicitly OUT OF SCOPE for CC-15 (fix
# sketch: "adocao por grafo = follow-up") — this fence only makes the current state visible and
# keeps it from drifting unnoticed.
# ---------------------------------------------------------------------------

_AGENTS_DIR = Path(__file__).resolve().parents[3] / "src" / "maezo" / "agents"

#: Every agent package with a module-level `_business_key(state)` function today (the 10 fleet
#: graphs — NOT `_template`, which has no such function and is covered separately by CC-15 step 2).
_EXPECTED_BUSINESS_KEY_AGENTS = frozenset(
    {
        "andre",
        "beatriz",
        "carolina",
        "fernando",
        "gustavo",
        "helena",
        "lucas",
        "marina",
        "rafael",
        "valentina",
    }
)

#: Agents whose `_business_key` does NOT yet compose through `maezo.platform.keys.key_segment`,
#: with the reason. Adoption is a per-graph WP (graphs are off-limits to CC-15 by brief); an agent
#: MUST be removed from this mapping in the same change that ports its `_business_key`.
_NOT_YET_ADOPTED: dict[str, str] = {
    "andre": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
    "beatriz": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
    "carolina": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
    "fernando": (
        "compoe via `base.mint_contract_business_key` (helper proprio, DL-0043) — nao via "
        "`key_segment`; adocao = WP por grafo"
    ),
    "gustavo": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
    "helena": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
    "lucas": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
    "marina": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
    "rafael": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
    "valentina": "adocao = WP por grafo (graph.py fora de escopo do CC-15)",
}


def _business_key_source(agent: str) -> str:
    path = _AGENTS_DIR / agent / "graph.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_business_key":
            segment = ast.get_source_segment(src, node)
            assert segment is not None
            return segment
    raise AssertionError(f"agents.{agent}.graph has no module-level _business_key(state) function")


def _business_key_uses_key_segment(agent: str) -> bool:
    """AST-based, not substring: only a `key_segment` NAME/ATTRIBUTE reference counts — a mention
    inside a docstring/comment does not (comments aren't even in the AST; this also refuses to be
    fooled by a docstring that merely talks about `key_segment` without calling it)."""
    source = _business_key_source(agent)
    tree = ast.parse(source)
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    body_without_docstring = func.body[1:] if ast.get_docstring(func) else func.body
    for node in ast.walk(ast.Module(body=body_without_docstring, type_ignores=[])):
        if isinstance(node, ast.Name) and node.id == "key_segment":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "key_segment":
            return True
    return False


def test_discovered_agents_with_business_key_match_the_closed_inventory() -> None:
    """FAILS CLOSED: a new/renamed agent graph with its own `_business_key` must be classified
    here (added to the adopted set or to `_NOT_YET_ADOPTED` with a reason) before this passes —
    it can never silently join an allowlist unnoticed."""
    discovered = {
        p.parent.name
        for p in _AGENTS_DIR.glob("*/graph.py")
        if p.parent.name != "_template" and "_business_key" in p.read_text(encoding="utf-8")
    }
    assert discovered == _EXPECTED_BUSINESS_KEY_AGENTS, (
        "graphs with a _business_key function drifted from the CC-15 disclosure inventory: "
        f"discovered={sorted(discovered)} expected={sorted(_EXPECTED_BUSINESS_KEY_AGENTS)}"
    )
    assert set(_NOT_YET_ADOPTED) <= _EXPECTED_BUSINESS_KEY_AGENTS


@pytest.mark.parametrize("agent", sorted(_EXPECTED_BUSINESS_KEY_AGENTS))
def test_business_key_adoption_status_is_correctly_classified(agent: str) -> None:
    """Each of the 10 graphs is asserted EITHER adopted (uses key_segment) OR explicitly listed
    in `_NOT_YET_ADOPTED` — never silently neither. An agent that starts using `key_segment` must
    be REMOVED from `_NOT_YET_ADOPTED` in that same change, or this fails (the inventory shrinks,
    it never grows stale)."""
    uses_key_segment = _business_key_uses_key_segment(agent)
    if agent in _NOT_YET_ADOPTED:
        assert not uses_key_segment, (
            f"agents.{agent}.graph._business_key now uses key_segment — remove it from "
            "_NOT_YET_ADOPTED (adoption landed, the disclosure inventory must shrink)"
        )
    else:
        assert uses_key_segment, (
            f"agents.{agent}.graph._business_key does not use key_segment and is not listed in "
            "_NOT_YET_ADOPTED — classify it (this is a closed inventory, not an allowlist)"
        )
