"""Cheap fence — CC-04 (fleet audit): agent-graph module docstrings must not deny A2A infra that
actually exists on disk.

Ground truth this fence protects (re-derive, don't trust): `maezo.a2a.delegation.DelegationEnvelope`
(`src/maezo/a2a/delegation.py`) and `maezo.a2a.dispatcher.DelegationDispatcher`
(`src/maezo/a2a/dispatcher.py`) both exist and are exported from `maezo.a2a` — several `graph.py`
module docstrings used to claim "v2's `a2a/` package has no `DelegationEnvelope`/
`DelegationDispatcher`", which was false the moment those modules landed. CC-04's sweep
(docs-only, `fleet/cc04-docstrings-a2a`) corrected the false claims in
rafael/marina/beatriz/carolina/andre/valentina/gustavo's `graph.py` docstrings; this test is the
cheap regression fence so the claim cannot silently drift back in.

Two checks:
  1. NO `src/maezo/agents/*/graph.py` module docstring may contain the stale denial (regex).
  2. Every agent THIS WORK PACKAGE vouches for that has a real `agents/<agent>/delegation.py`
     (rafael/carolina/andre) must mention "delegation.py" in its module docstring — so a future
     docstring rewrite that silently drops the disclosure trips this fence too. Scoped
     deliberately to this WP's charter (see module docstring above): `helena` also has a
     `delegation.py` but its `graph.py` docstring was OUT OF SCOPE for CC-04 and is not touched
     here (reported as an adjacency NOT fixed, not silently swept under a broader assertion).

A third, narrower check covers the GUS-01-adjacent finding surfaced mid-WP: two worker
docstrings (`tools/workers/nip.py::assemble_response`,
`tools/workers/ans_submit.py::notify_regulatorio`) used to claim they "delegate to Gustavo" /
"convoca o agente Gustavo" while assembling a dict locally with zero `maezo.a2a` involvement.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[3] / "src" / "maezo"

# The exact defect class CC-04 found: a docstring flatly denying the A2A dispatcher/envelope
# infra exists. Deliberately loose (multiple phrasings observed across agents) but anchored on
# "has no"/"no DelegationDispatcher" so a *historical* "prior text ... had no ..." correction
# (past tense) never re-trips it.
_STALE_A2A_DENIAL = re.compile(r"has no .*DelegationEnvelope|a2a/`? package has no|no DelegationDispatcher")

# Agents this WP (cc04-docstrings-a2a) is authoring/vouching for. Scoped exactly to the 7 graphs
# named in the WP brief plus the 3 that already had a correct, wired `delegation.py` disclosure
# before this WP touched anything (rafael/carolina/andre overlap both sets).
_AGENTS_WITH_REAL_DELEGATION_IN_SCOPE = ("rafael", "carolina", "andre")

_ALL_AGENT_GRAPH_FILES = sorted((REPO_SRC / "agents").glob("*/graph.py"))


def _module_docstring(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return ast.get_docstring(tree) or ""


def _agent_id(path: Path) -> str:
    return path.parent.name


@pytest.mark.parametrize("path", _ALL_AGENT_GRAPH_FILES, ids=_agent_id)
def test_graph_module_docstring_never_denies_a2a_infra(path: Path) -> None:
    """No agent graph's module docstring may claim `DelegationEnvelope`/`DelegationDispatcher`
    don't exist — they do (`a2a/delegation.py`, `a2a/dispatcher.py`, exported from `maezo.a2a`).
    """
    doc = _module_docstring(path)
    match = _STALE_A2A_DENIAL.search(doc)
    assert match is None, (
        f"{path}: module docstring still denies A2A infra that exists on disk "
        f"(matched {match.group(0)!r} if this ever regresses — see a2a/delegation.py::"
        f"DelegationEnvelope, a2a/dispatcher.py::DelegationDispatcher)"
    )


@pytest.mark.parametrize("agent_id", _AGENTS_WITH_REAL_DELEGATION_IN_SCOPE)
def test_graph_module_docstring_discloses_real_delegation_module(agent_id: str) -> None:
    """An agent with a real `agents/<agent>/delegation.py` must say so in its graph docstring —
    a bare denial-removal without a positive disclosure is still a stale/misleading docstring.
    """
    delegation_path = REPO_SRC / "agents" / agent_id / "delegation.py"
    assert delegation_path.is_file(), f"fixture drift: {delegation_path} no longer exists"

    graph_path = REPO_SRC / "agents" / agent_id / "graph.py"
    doc = _module_docstring(graph_path)
    assert "delegation.py" in doc, (
        f"{graph_path}: {agent_id} has a real delegation.py but its graph module docstring never mentions it"
    )


# ---------------------------------------------------------------------------------------------
# GUS-01-adjacent: worker docstrings that claimed a live delegation to Gustavo that never existed
# ---------------------------------------------------------------------------------------------

#: Anchored to the START of a line (not "anywhere in the docstring") so a corrected docstring
#: that quotes the old claim mid-sentence while explaining the correction (e.g. `the prior
#: docstring said "Delegates to Gustavo..."`) does NOT re-trip this fence — only a docstring
#: that still OPENS a line with the bare claim does.
_GUSTAVO_DELEGATION_CLAIM = re.compile(
    r"^\s*(Delegates to Gustavo|Convoca o agente Gustavo)", re.IGNORECASE | re.MULTILINE
)


def _function_docstring(path: Path, func_name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"{path}: no top-level function {func_name!r} found (fixture drift)")


def _module_imports_a2a(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return "maezo.a2a" in text or "from maezo import a2a" in text


@pytest.mark.parametrize(
    ("rel_path", "func_name"),
    [
        ("tools/workers/nip.py", "assemble_response"),
        ("tools/workers/ans_submit.py", "notify_regulatorio"),
    ],
)
def test_worker_docstring_never_claims_gustavo_delegation_it_never_wires(
    rel_path: str, func_name: str
) -> None:
    """Neither worker actually delegates to Gustavo (0 `maezo.a2a`/dispatcher hits in either
    module) — the docstring's OPENING claim must not say otherwise (GUS-01, fleet audit).
    """
    path = REPO_SRC / rel_path
    doc = _function_docstring(path, func_name)
    match = _GUSTAVO_DELEGATION_CLAIM.search(doc)
    imports_a2a = _module_imports_a2a(path)
    assert not (match and not imports_a2a), (
        f"{path}::{func_name}: docstring opens a line claiming a delegation to Gustavo "
        f"({match.group(0)!r} if this ever regresses) but the module never imports maezo.a2a — "
        "no such delegation exists"
    )
