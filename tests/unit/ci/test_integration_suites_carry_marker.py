"""Repo-wide fence: every `tests/integration/**/test_*.py` module MUST declare
`pytestmark = pytest.mark.integration` (bare or inside a list, e.g.
`pytestmark = [pytest.mark.integration, pytest.mark.chaos]`) — the mandated unit gate
`pytest tests/ -q -m "not integration"` only deselects a module pytest has actually marked.

Root cause this closes (register arbitration O-12, gap BRIDGE-SUITES-UNMARKED): four suites
under `tests/integration/platform/` shipped with NO `pytestmark`, so the unit gate silently
COLLECTED them instead of deselecting them. Worse, one of the four
(`test_notifications_bridge_live_engine.py`'s `live_tenant` fixture) POSTs a real deployment to
`/deployment/create` whenever an engine answers on the shared host — so every agent's routine
"just run the unit gate" run was quietly deploying BPMN into the one shared CIB Seven engine
(see the "Engine sharing rule" in the gap-closure program brief). This test makes that class of
bug structurally impossible to reintroduce: it is pure static analysis (parses with `ast`,
imports NOTHING from `tests/integration/`) so it can never itself contaminate the engine, and it
fails LOUDLY the moment a new integration test file is added without the marker.

Verified empirically (not merely asserted) before writing this fence: a `pytestmark` assigned
ONLY inside a directory's `conftest.py` does NOT cascade to sibling test modules — pytest's
module-level `pytestmark` mechanism only ever marks the tests collected FROM THAT SAME MODULE, and
`conftest.py` is never itself collected as a test module. A small isolated probe (bare `conftest.py`
with `pytestmark = pytest.mark.integration` + a sibling `test_probe.py`) confirmed
`pytest -m "not integration"` still COLLECTS that sibling test. Some real directories here
(`tests/integration/processes/conftest.py`, `tests/integration/chaos/conftest.py`) already declare
`pytestmark` at the conftest level too — today that is harmless belt-and-suspenders documentation
ONLY, because every test file in those directories *also* carries its own module-level
`pytestmark` (`test_conftest_only_marker_is_not_by_itself_sufficient_in_practice` below pins that
fact so it cannot silently stop being true).

FIX for gap CI-FENCE-TESTS-COUPLING (register arbitration). An earlier version of this fence made
`_Coverage.covered` accept `own_module OR via_conftest_chain` in
`test_every_integration_test_module_declares_the_integration_marker` itself — soundness for a
module relying SOLELY on the (empirically non-cascading) conftest-chain marker then depended
entirely on a SECOND test, `test_every_real_file_is_covered_by_its_own_module_level_marker`,
independently re-checking the stricter own-module-only rule. Deleting that second test would have
silently reopened the exact BRIDGE-SUITES-UNMARKED-class hole: the first test would keep passing
for a module pytest's own `-m "not integration"` does NOT actually deselect. Fixed at the root,
not with a comment: `_Coverage.covered` below now requires the module's OWN marker —
`via_conftest_chain` is computed and reported for diagnostics only, and is never by itself
sufficient. `test_every_integration_test_module_declares_the_integration_marker` therefore
enforces the real, functionally-accurate rule ON ITS OWN; no single test deletion can reopen the
hole. `test_every_real_file_is_covered_by_its_own_module_level_marker` remains as an INDEPENDENT
duplicate — it calls `_module_declares_integration_marker` directly, bypassing `_coverage_for`
entirely — so it still catches a future regression that reintroduces the lenient OR inside
`_coverage_for`/`covered` even though that is no longer the only thing standing between a suite
and engine contamination.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_INTEGRATION_ROOT = _REPO_ROOT / "tests" / "integration"


def _is_pytest_mark_integration(node: ast.expr) -> bool:
    """True for the AST of exactly `pytest.mark.integration` (an `ast.Attribute` chain)."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "integration"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _module_declares_integration_marker(source: str) -> bool:
    """True if a top-level `pytestmark = ...` assignment's value is `pytest.mark.integration`
    itself, or a list/tuple that contains it (e.g. `[pytest.mark.integration, pytest.mark.chaos]`).

    Deliberately restricted to top-level `ast.Module.body` assignments — a `pytestmark` set
    inside a function or class body has no effect on pytest's collection and must not count.
    """
    tree = ast.parse(source)
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets):
            continue
        value = stmt.value
        if _is_pytest_mark_integration(value):
            return True
        if isinstance(value, (ast.List, ast.Tuple)) and any(
            _is_pytest_mark_integration(elt) for elt in value.elts
        ):
            return True
    return False


def _iter_integration_test_modules() -> list[Path]:
    return sorted(p for p in _INTEGRATION_ROOT.rglob("test_*.py") if "__pycache__" not in p.parts)


def _conftest_chain(test_file: Path) -> list[Path]:
    """`conftest.py` at every directory level from `test_file`'s own directory up to and
    including `tests/integration` itself (never above it — that is out of this fence's scope)."""
    chain: list[Path] = []
    directory = test_file.parent
    while True:
        candidate = directory / "conftest.py"
        if candidate.is_file():
            chain.append(candidate)
        if directory == _INTEGRATION_ROOT:
            break
        directory = directory.parent
    return chain


@dataclass(frozen=True)
class _Coverage:
    own_module: bool
    via_conftest_chain: bool

    @property
    def covered(self) -> bool:
        """Gap CI-FENCE-TESTS-COUPLING fix: `covered` requires the module's OWN marker.

        A directory conftest.py's `pytestmark` does NOT actually cascade to sibling test modules
        under pytest's real semantics (see the module docstring's empirical finding, pinned by
        `test_conftest_only_marker_is_not_by_itself_sufficient_in_practice` below) — so
        `via_conftest_chain` alone must never satisfy this property. It is still tracked (and
        reported in `_coverage_for`'s return value) purely for diagnostics/documentation of the
        repo's existing conftest-level convention, never as an alternative path to `covered`.
        """
        return self.own_module


def _coverage_for(test_file: Path) -> _Coverage:
    own = _module_declares_integration_marker(test_file.read_text(encoding="utf-8"))
    via_chain = False
    if not own:
        via_chain = any(
            _module_declares_integration_marker(c.read_text(encoding="utf-8"))
            for c in _conftest_chain(test_file)
        )
    return _Coverage(own_module=own, via_conftest_chain=via_chain)


# ---------------------------------------------------------------------------
# The fence itself
# ---------------------------------------------------------------------------


def test_every_integration_test_module_declares_the_integration_marker() -> None:
    """Every `tests/integration/**/test_*.py` module must be covered — `_Coverage.covered`
    (gap CI-FENCE-TESTS-COUPLING fix) requires the module's OWN module-level `pytestmark`; a
    `conftest.py` in its directory chain up to `tests/integration` is tracked for diagnostics only
    and is never by itself sufficient, matching pytest's real semantics
    (`test_conftest_only_marker_is_not_by_itself_sufficient_in_practice` below proves the
    conftest-only path does not actually work). This test therefore enforces the real,
    functionally-accurate rule ON ITS OWN — it no longer depends on
    `test_every_real_file_is_covered_by_its_own_module_level_marker` existing to catch what its
    own `_coverage_for` would otherwise let through.

    This is the exact regression case for BRIDGE-SUITES-UNMARKED: before that fix, this failed on
    all four of `tests/integration/platform/test_notifications_bridge_live_engine.py`,
    `test_events_kafka_producer_live.py`, `test_notifications_bridge_live_kafka.py`, and
    `test_notifications_bridge_live_pg.py` — none had a `pytestmark`, and their directory's own
    `conftest.py` (`tests/integration/platform/conftest.py`) does not declare one either (it only
    overrides the parent engine-reachability fixture).
    """
    modules = _iter_integration_test_modules()
    uncovered = [str(m.relative_to(_REPO_ROOT)) for m in modules if not _coverage_for(m).covered]
    assert not uncovered, (
        "the following tests/integration/**/test_*.py modules do not declare their OWN "
        "module-level `pytestmark = pytest.mark.integration` (a directory conftest.py's own "
        "pytestmark does NOT cascade to sibling modules under real pytest semantics — see this "
        'module\'s docstring), so `pytest tests/ -q -m "not integration"` would NOT deselect '
        "them:\n  " + "\n  ".join(uncovered)
    )


def test_scan_is_not_vacuous() -> None:
    """A passing fence that scanned zero files would be worthless — pin a floor below today's
    real count (32 at the time of writing) so a future refactor that silently narrows the glob
    (e.g. renaming the directory, or mistyping the `rglob` pattern) is itself caught."""
    modules = _iter_integration_test_modules()
    assert len(modules) >= 25, (
        f"expected at least 25 test_*.py modules under {_INTEGRATION_ROOT}, found "
        f"{len(modules)} — the scan may be broken, not the repo shrinking that much"
    )


def test_every_real_file_is_covered_by_its_own_module_level_marker() -> None:
    """INDEPENDENT duplicate of the same invariant `test_every_integration_test_module_declares_
    the_integration_marker` now enforces via `_Coverage.covered` — this test calls
    `_module_declares_integration_marker` directly on each module, bypassing `_coverage_for`/
    `_Coverage` entirely, so it stays true even if a future edit reintroduces the lenient
    `own_module or via_conftest_chain` inside `covered` (the exact gap CI-FENCE-TESTS-COUPLING
    closed: this test existing was the ONLY thing catching that before `covered` itself was
    fixed). Today, every real file's coverage comes from ITS OWN module-level `pytestmark`, never
    only from a conftest.py — because a bare `conftest.py`-level `pytestmark` does not actually
    cascade to sibling modules in pytest. This pins that stronger, no-false-sense-of-safety
    property so a future author cannot "fix" a missing marker by adding it only to a directory
    conftest.py."""
    modules = _iter_integration_test_modules()
    not_self_covered = [
        str(m.relative_to(_REPO_ROOT))
        for m in modules
        if not _module_declares_integration_marker(m.read_text(encoding="utf-8"))
    ]
    assert not not_self_covered, (
        "the following modules rely on a conftest.py-only marker, which does NOT actually "
        "deselect them under pytest's real semantics — add `pytestmark = pytest.mark.integration` "
        "directly to the module instead:\n  " + "\n  ".join(not_self_covered)
    )


def test_conftest_only_marker_is_not_by_itself_sufficient_in_practice() -> None:
    """Documents (and pins) the empirical finding the module docstring describes: a directory's
    `conftest.py` declaring `pytestmark` at module level is real, existing repo convention
    (`tests/integration/processes/conftest.py`, `tests/integration/chaos/conftest.py`) — but it is
    NOT what makes `pytest -m "not integration"` deselect that directory's tests. Every file in
    those two directories also carries its own module-level `pytestmark`; this test proves that
    fact, which is exactly WHY `_Coverage.covered` (gap CI-FENCE-TESTS-COUPLING fix) requires the
    module's own marker and treats `via_conftest_chain` as informational-only rather than as an
    alternative that could quietly become the only thing standing between a suite and engine
    contamination.
    """
    conftest_dirs_with_marker = [
        c.parent
        for c in _INTEGRATION_ROOT.rglob("conftest.py")
        if "__pycache__" not in c.parts and _module_declares_integration_marker(c.read_text(encoding="utf-8"))
    ]
    assert conftest_dirs_with_marker, (
        "expected at least one conftest.py under tests/integration/ to declare pytestmark "
        "(processes/, chaos/) — if this list is now empty the repo convention changed and the "
        "docstring above should be revisited"
    )
    for directory in conftest_dirs_with_marker:
        siblings = sorted(p for p in directory.glob("test_*.py") if "__pycache__" not in p.parts)
        assert siblings, f"{directory} has a marked conftest.py but no test_*.py files"
        for sibling in siblings:
            assert _module_declares_integration_marker(sibling.read_text(encoding="utf-8")), (
                f"{sibling.relative_to(_REPO_ROOT)} relies solely on "
                f"{directory.relative_to(_REPO_ROOT)}/conftest.py for its integration marker — "
                "that does not actually work under pytest; add its own `pytestmark`"
            )
