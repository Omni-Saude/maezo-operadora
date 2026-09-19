"""Repo-wide fence for the `root_fixture` deselection — the ONLY sanctioned way a suite that
needs private, ROOT-supplied materials stays out of the global `-m integration` lane.

Root cause this closes (PR-A landing, real-engine lane of PR #375). PR-A lands three suites whose
coordinates are a PRIVATE fixture the CI lane can never hold: a relay fixture directory, a
disposable CIB PostgreSQL + mTLS manifest, and a dedicated PHI PostgreSQL + mTLS custody. In the
lane they produced 17 setup ERRORS (`tests/integration/gateway/test_human_relay_live_cib.py`,
whose fixture loader asserts rather than skips — deliberately: "ROOT must explicitly provide
MAEZO_HUMAN_RELAY_PRIVATE_DIR; no skip/default") and 3 free-text skips, all 20 rejected by
`scripts/ci/run_live_pytest.py` as `corpo nao verificado, skip/XPASS/falha inesperado`.

That rejection is CORRECT and this fence does not soften it. The wrapper admits exactly three
outcomes — `passed`, `xfailed_executed` (strict xfail whose BODY ran) and `inactive_companion`
(the mutation-guard companion) — and `.github/workflows/ci.yml` says so in as many words: "Um
xfail sem call, XPASS ou skip rotulado livremente falha fechado." A free-text `pytest.skip` is a
silent hole by construction, and turning these suites into `xfail` would be a lie: they PASS in
ROOT, against the real fixture.

So the suite is DESELECTED, never skipped and never faked — the same shape as pytest's own `-m`
deselection, and the same posture `tests/unit/runtime/test_inference_live.py` already takes with
its `llm_live` marker (a coordinate that is an API key, not a repo-served port, so the lane's
marker expression simply never selects it). Deselection is visible three ways: the marker is
registered in `pyproject.toml`, the count lands in pytest's own `deselected` summary, and
`tests/integration/conftest.py::pytest_report_header` announces the posture on every run.

What this fence pins, so the mechanism can neither grow silently nor go inert:
  1. `root_fixture` is a REGISTERED marker (an unregistered one is a typo away from selecting).
  2. The set of suites carrying it is exactly `_ROOT_FIXTURE_SUITES` below — each with the ROOT
     coordinate it needs. A new suite must be added HERE, in review, or the fence reports it.
     This exact-match allowlist is the mechanism's ONLY containment (see 3). The scan covers
     `conftest.py` as well as `test_*.py`, so a marker attached from a conftest cannot slip it.
  3. Every such suite lives under `tests/integration/`. NOTE what this does and does not buy
     (corrected after V8-Q3 disproved the original claim empirically): the hook's REACH is
     repo-wide, not tree-scoped — `pytest_collection_modifyitems` in a sub-conftest receives the
     WHOLE session's item list, so once collection descends into `tests/integration/` (as the
     lane's own `pytest tests` does) a marked module anywhere under `tests/` is deselected too.
     Containment therefore comes from the exact-match allowlist in (2), NEVER from location. What
     the location requirement does buy is the converse: an invocation that never collects
     `tests/integration/` (say `pytest tests/unit`) never loads that conftest, so a marked module
     outside this tree would be INERT there — selected and failing for want of its fixture. Living
     inside the tree is what makes the marker effective in every invocation that can collect it.
  4. The deselection actually happens, and the opt-in actually opts in — proved by running
     `--collect-only` in a subprocess both ways, not by reading the conftest.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

import pytest

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_TESTS_ROOT: Final[Path] = _REPO_ROOT / "tests"

_MARKER: Final[str] = "root_fixture"
_OPT_IN_ENV: Final[str] = "MAEZO_ROOT_FIXTURES"

#: Every suite allowed to carry `root_fixture`, mapped to the ROOT-supplied coordinate that makes
#: it unrunnable in CI. Adding a row is a review decision: it removes tests from the lane's
#: obligation, so the reason must name a PRIVATE artifact, never mere inconvenience.
_ROOT_FIXTURE_SUITES: Final[dict[str, str]] = {
    "tests/integration/gateway/test_human_relay_live_cib.py": (
        "MAEZO_HUMAN_RELAY_PRIVATE_DIR — a private relay fixture directory (relay-fixture.json, "
        "schema human-relay-fixture.v1) served by ROOT on 127.0.0.1:15433; its loader "
        "(tests/support/human_relay_live.py) ASSERTS instead of skipping, on purpose."
    ),
    "tests/integration/gateway/test_decision_binding_live_pg.py": (
        "MAEZO_DECISION_BINDING_TEST_CONFIG — ADR-0049 D5 against a ROOT-supplied DISPOSABLE CIB "
        "PostgreSQL with mTLS identities; host/port/database/roles/certificate files all live "
        "inside the private manifest, there is no caller DSN and no repo-served default."
    ),
    "tests/integration/portal/test_phi_decision_custody.py": (
        "a dedicated PHI PostgreSQL + mTLS custody fixture (TLS-pinned decision custody); the "
        "pin is landed as code by PR-A and activated later — it must not be loosened to run."
    ),
    # PR-C (109e0ac3) and Q7 (fadeed00), 2026-09-18, landed five more ROOT-only suites carrying only
    # `integration`: the global lane collected them, and on main's first completed run
    # (2026-09-19, 6b5b4493) they produced 1139 of the lane's 1139 failures plus 23 unverified
    # skips. Their fail-closed bodies are the authors' intent and are kept untouched; what was
    # missing was the marker this allowlist exists for.
    "tests/integration/test_portal_engine_d7_package.py": (
        "MAEZO_D7_PACKAGE_FIXTURE — the private, seeded, secured fixture that prepare_fixture.py "
        "builds for ROOT (d7-fixture.json, boundary.json, client certs) against the ACTUAL "
        "secured Tomcat image on localhost:18443; `fixture()` ASSERTS instead of skipping, on "
        "purpose. 1119 parametrized cases: unmarked, it alone painted main's lane red on "
        "2026-09-19."
    ),
    "tests/integration/test_native_acquisition_v2.py": (
        "MAEZO_NATIVE_V2_IT_FIXTURE — owner-prepared native v2 fixture (native-v2-fixture.json, "
        "protocol maezo.native-v2-real-fixture.v1, ca.crt, client TLS, synthetic process "
        "instances); `real_client()` ASSERTS on absence by design (see "
        "deploy/cibseven/secured/NATIVE-V2.md)."
    ),
    "tests/integration/test_portal_engine_package.py": (
        "MAEZO_HUMAN_PACKAGE_HTTPS_URL / MAEZO_HUMAN_PACKAGE_CA_FILE and siblings — an isolated "
        "RUNNING secured image with the human SQL, tenant row and explicit trust file installed "
        "(deploy/cibseven/package-test/prepare.py); the module forbids any missing-config skip."
    ),
    "tests/integration/test_portal_engine_reads.py": (
        "MAEZO_PORTAL_READ_PACKAGED_FIXTURE_FILE — a private packaged Q2 fixture with signed "
        "envelopes prepared inside the gateway boundary plus a ROOT-issued browser session; "
        "`required()` fails (pytrace=False) instead of skipping."
    ),
    "tests/integration/platform/test_d7_control_storage_live.py": (
        "the `d7_live_storage_lane` fixture — real PostgreSQL 16 owner connections and the actual "
        "qualified DynamoDB adapter, supplied only by ROOT; without it the module skips with "
        "NOT_RUN, which the live-pytest verifier rightly counts as an unverified body."
    ),
}


def _iter_marked_modules() -> dict[Path, list[str]]:
    """Every module under `tests/` that names `root_fixture`, by AST (never by import).

    `conftest.py` is scanned alongside `test_*.py` (V8-Q3 F-2): a marker can also be attached from
    a conftest (`item.add_marker("root_fixture")`) or re-exported through a non-test helper, and a
    scan limited to `test_*.py` would let the allowlist assertion miss it. The sibling
    `tests/integration/conftest.py` mentions the marker only as the string constant
    `ROOT_FIXTURE_MARKER`, never as `pytest.mark.root_fixture`, so it is not matched by the
    `pytest.mark.<name>` attribute shape below — the mechanism's own implementation does not
    register as one of its own subjects.
    """
    found: dict[Path, list[str]] = {}
    candidates = sorted({*_TESTS_ROOT.rglob("test_*.py"), *_TESTS_ROOT.rglob("conftest.py")})
    for path in candidates:
        try:
            tree = ast.parse(path.read_bytes())
        except SyntaxError:  # pragma: no cover - a broken module is another fence's subject
            continue
        names = [
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == _MARKER
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
        ]
        if names:
            found[path] = names
    return found


def test_the_marker_is_registered_in_pyproject() -> None:
    config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    declarations = [line for line in markers if line.split(":", 1)[0].strip() == _MARKER]
    assert len(declarations) == 1, f"`{_MARKER}` must be registered exactly once; got {declarations}"
    # The registration must say what it does, so `--markers` is an honest index of the mechanism.
    assert _OPT_IN_ENV in declarations[0]
    assert "conftest" in declarations[0]


def test_the_marked_suites_are_exactly_the_reviewed_allowlist() -> None:
    observed = {path.relative_to(_REPO_ROOT).as_posix() for path in _iter_marked_modules()}
    assert observed == set(_ROOT_FIXTURE_SUITES), (
        "a suite gained or lost the `root_fixture` marker without updating this fence's "
        f"allowlist; observed={sorted(observed)} allowlisted={sorted(_ROOT_FIXTURE_SUITES)}"
    )
    assert observed, "the allowlist may not be empty — the fence would be vacuous"


@pytest.mark.parametrize("relative", sorted(_ROOT_FIXTURE_SUITES))
def test_every_marked_suite_lives_where_the_deselection_hook_is_loaded(relative: str) -> None:
    """`pytest_collection_modifyitems` lives in `tests/integration/conftest.py`, so it is loaded
    only by an invocation that collects that tree. A marked module OUTSIDE the tree is therefore
    inert in any run that does not reach `tests/integration/` (e.g. `pytest tests/unit`): nothing
    deselects it, it is selected, and it fails for want of its private fixture — the way the 20
    cases of PR #375's lane did.

    This is NOT a containment claim. Once the conftest IS loaded, the hook receives the whole
    session's item list and its reach is repo-wide; containment is
    `test_the_marked_suites_are_exactly_the_reviewed_allowlist` above, never location."""
    assert relative.startswith("tests/integration/"), relative
    assert (_REPO_ROOT / relative).is_file(), relative


@pytest.mark.parametrize("relative", sorted(_ROOT_FIXTURE_SUITES))
def test_every_marked_suite_also_carries_the_integration_marker(relative: str) -> None:
    """The suites stay honest members of the integration set: they are deselected by an explicit
    opt-out, NOT hidden by dropping the marker that declares what they are."""
    source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
    assert "pytest.mark.integration" in source, relative


def _collect(paths: list[str], *, opt_in: bool, quiet: bool = True) -> str:
    environment = dict(os.environ)
    environment.pop(_OPT_IN_ENV, None)
    if opt_in:
        environment[_OPT_IN_ENV] = "1"
    verbosity = ["-q"] if quiet else []
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *paths,
            *verbosity,
            "-p",
            "no:cacheprovider",
            "--collect-only",
        ],
        cwd=_REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return completed.stdout + completed.stderr


def test_the_deselection_and_its_opt_in_both_actually_work() -> None:
    """Executes the mechanism rather than reading it: without the opt-in every marked test is
    DESELECTED (not skipped, not errored); with it, every one is selected."""
    paths = sorted(_ROOT_FIXTURE_SUITES)

    closed = _collect(paths, opt_in=False)
    assert "deselected" in closed, closed[-2000:]
    assert "no tests collected" in closed, closed[-2000:]
    # A skip here would be the exact hole the CI wrapper rejects.
    assert " skipped" not in closed, closed[-2000:]

    opened = _collect(paths, opt_in=True)
    assert "tests collected" in opened, opened[-2000:]
    assert "deselected" not in opened, opened[-2000:]


def test_the_deselection_is_announced_even_under_the_lane_s_quiet_flag() -> None:
    """Visibility is load-bearing: a deselection nobody can see in the log is the silent skip this
    whole mechanism exists to avoid. The lane runs `-q`, which SUPPRESSES `pytest_report_header` —
    so the announcement must come through the terminal reporter, and it must name the modules."""
    quiet = _collect(sorted(_ROOT_FIXTURE_SUITES), opt_in=False)
    assert f"[{_MARKER}] DESELECTED" in quiet, quiet[-2000:]
    assert _OPT_IN_ENV in quiet, quiet[-2000:]
    for relative in _ROOT_FIXTURE_SUITES:
        assert Path(relative).name in quiet, quiet[-2000:]


def test_the_posture_is_also_stated_in_the_report_header() -> None:
    verbose = _collect(sorted(_ROOT_FIXTURE_SUITES), opt_in=False, quiet=False)
    assert f"{_MARKER} suites: DESELECTED" in verbose, verbose[-2000:]
    assert _OPT_IN_ENV in verbose, verbose[-2000:]
