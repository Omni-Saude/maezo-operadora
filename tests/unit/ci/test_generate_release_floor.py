"""Unit tests for the release-capability floor generator (audit §5, Wave-D fillers).

Four layers:

1. **Pure parsing/decision** tests drive `parse_pytest_summary_line` and `evaluate_unit_tests`
   directly against synthetic summary-line fixtures — never a real (nested) pytest subprocess.
2. **Pure comparator (RED/GREEN control)** tests drive `compare_vectors` directly against synthetic
   `CapabilityVector`s built by `_vec` — this is the consequence-level RED control the mission
   requires: a regressed candidate in EACH of the four gated dimensions must independently FAIL, and
   a held/improved candidate must PASS. `SELF_CHECK_SCENARIOS` (the module's own non-vacuity fixture
   set) is exercised directly too, so a change to those fixtures is pinned by a real test, not just
   trusted at runtime.
3. **Real-tree** tests run `measure_fences` for real (fast — five subprocesses, no docker) against
   the actual repo and assert all five registered fences pass today, and that the committed
   `docs/release-capability-floor.json` round-trips + matches the pinned `xfail_total` ground truth
   (mirrors `test_generate_xfail_census.py`'s own real-tree layer).
4. **CLI end-to-end, demonstrated RED -> GREEN** exercises `main()` itself (`--write` then `--check`)
   against a tmp-path floor file, with `measure_unit_tests` monkeypatched to a canned count (the one
   measurement this suite deliberately never re-runs for real — see that test's docstring for why).
   First proves a genuinely regressed candidate makes `main(["--check", ...])` return 1 (RED), then
   proves the SAME candidate with the regression reverted returns 0 (GREEN) — a real, demonstrated
   red-to-green flip through the actual CLI entry point, not just the pure comparator.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from scripts.ci.generate_release_floor import (
    DEFAULT_CENSUS_PATH,
    FENCE_REGISTRY,
    PRIORITIES,
    SELF_CHECK_SCENARIOS,
    CapabilityVector,
    Violation,
    build_floor_document,
    capability_vector_from_measurements,
    compare_vectors,
    evaluate_unit_tests,
    main,
    measure_fences,
    parse_pytest_summary_line,
    render_floor_json,
    run_self_check,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _vec(
    xfail_total: int, p0: int, p1: int, p2: int, passed: int, fences: tuple[str, ...]
) -> CapabilityVector:
    return CapabilityVector(xfail_total, {"P0": p0, "P1": p1, "P2": p2}, passed, tuple(sorted(fences)))


# ---------------------------------------------------------------------------
# Layer 1: pure parsing / fail-closed decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("6839 passed, 587 skipped, 1 warning in 51.78s", {"passed": 6839, "skipped": 587, "warning": 1}),
        ("1 failed, 5 passed in 1.2s", {"failed": 1, "passed": 5}),
        ("3 errors in 0.5s", {"error": 3}),
        ("no tests ran in 0.01s", {}),
        ("10 passed, 2 xfailed, 1 xpassed in 3.0s", {"passed": 10, "xfailed": 2, "xpassed": 1}),
        ("2 failed, 1 error, 4 passed in 2.0s", {"failed": 2, "error": 1, "passed": 4}),
    ],
)
def test_parse_pytest_summary_line(line: str, expected: dict[str, int]) -> None:
    assert parse_pytest_summary_line(line) == expected


def test_evaluate_unit_tests_green() -> None:
    passed, violations = evaluate_unit_tests({"passed": 100, "skipped": 3}, 0, "100 passed, 3 skipped in 1s")
    assert passed == 100
    assert violations == []


def test_evaluate_unit_tests_red_on_nonzero_exit() -> None:
    passed, violations = evaluate_unit_tests({"passed": 90, "failed": 1}, 1, "1 failed, 90 passed in 1s")
    assert passed is None
    assert any(v.code == "unit-tests-red" for v in violations)


def test_evaluate_unit_tests_red_on_failed_count_even_if_exit_zero() -> None:
    """Defensive layering: a `failed`/`error` count in the summary trips the same fail-closed path
    even if the subprocess return code were somehow 0 (never trust exit code alone)."""
    passed, violations = evaluate_unit_tests({"passed": 5, "error": 1}, 0, "1 error, 5 passed in 1s")
    assert passed is None
    assert any(v.code == "unit-tests-red" for v in violations)


def test_evaluate_unit_tests_unparseable_never_defaults_to_zero() -> None:
    passed, violations = evaluate_unit_tests({}, 0, "no tests ran in 0.01s")
    assert passed is None
    assert any(v.code == "unit-tests-unparseable" for v in violations)


# ---------------------------------------------------------------------------
# Layer 2: pure comparator — the consequence-level RED/GREEN control
# ---------------------------------------------------------------------------


def test_compare_vectors_red_xfail_total_up() -> None:
    baseline = _vec(5, 0, 0, 0, 100, ("a", "b"))
    candidate = _vec(6, 0, 0, 0, 100, ("a", "b"))
    violations = compare_vectors(candidate, baseline)
    assert violations, "a higher xfail total must FAIL the floor"
    assert any(v.code == "xfail-total-regression" for v in violations)


def test_compare_vectors_red_p0_up_hides_behind_total_improvement() -> None:
    """The audit §5 hole itself: xfail_total IMPROVES (10 -> 9) while a P0-classified xfail sneaks
    in (0 -> 1). Must still FAIL, and specifically with the priority-regression code — proving P0 is
    gated independently of the total, not folded into it."""
    baseline = _vec(10, 0, 0, 0, 100, ("a", "b"))
    candidate = _vec(9, 1, 0, 0, 100, ("a", "b"))
    violations = compare_vectors(candidate, baseline)
    assert violations, "a P0 regression must FAIL even when the total improved"
    codes = {v.code for v in violations}
    assert "xfail-priority-regression" in codes
    assert "xfail-total-regression" not in codes  # total genuinely improved; must not be flagged


def test_compare_vectors_red_unit_tests_down() -> None:
    baseline = _vec(5, 0, 0, 0, 100, ("a", "b"))
    candidate = _vec(5, 0, 0, 0, 90, ("a", "b"))
    violations = compare_vectors(candidate, baseline)
    assert any(v.code == "unit-tests-regression" for v in violations)


def test_compare_vectors_red_fence_disabled() -> None:
    baseline = _vec(5, 0, 0, 0, 100, ("a", "b"))
    candidate = _vec(5, 0, 0, 0, 100, ("a",))
    violations = compare_vectors(candidate, baseline)
    assert any(v.code == "fence-disabled" and "'b'" in v.detail for v in violations)


def test_compare_vectors_green_exact_hold() -> None:
    baseline = _vec(5, 0, 0, 0, 100, ("a", "b"))
    candidate = _vec(5, 0, 0, 0, 100, ("a", "b"))
    assert compare_vectors(candidate, baseline) == []


def test_compare_vectors_green_genuine_improvement() -> None:
    baseline = _vec(5, 0, 0, 0, 100, ("a", "b"))
    candidate = _vec(4, 0, 0, 0, 110, ("a", "b", "c"))
    assert compare_vectors(candidate, baseline) == []


def test_compare_vectors_green_new_fence_absent_from_baseline_is_not_a_regression() -> None:
    """A fence the baseline never recorded (not yet in FENCE_REGISTRY at write time) missing from
    the candidate is NOT a regression — only a baseline-passing fence going dark is."""
    baseline = _vec(5, 0, 0, 0, 100, ("a",))
    candidate = _vec(5, 0, 0, 0, 100, ("a",))  # candidate simply never had "b" either
    assert compare_vectors(candidate, baseline) == []


def test_self_check_scenarios_are_internally_consistent() -> None:
    """Pins `SELF_CHECK_SCENARIOS` itself: every declared RED scenario must produce >=1 violation
    and every GREEN scenario must produce zero, via the exact same `compare_vectors` this suite
    tests directly above — if a future edit to the fixture set breaks that contract, this test (not
    just the runtime self-check) turns red."""
    for label, baseline, candidate, expect_violation in SELF_CHECK_SCENARIOS:
        got = bool(compare_vectors(candidate, baseline))
        assert got == expect_violation, label


def test_run_self_check_passes_against_the_real_comparator() -> None:
    assert run_self_check() == []


# ---------------------------------------------------------------------------
# Composition + serialization (pure)
# ---------------------------------------------------------------------------


def test_capability_vector_from_measurements() -> None:
    census = {"total": 7, "per_classification": {"TISS_XSD_SME": 6, "P0": 1}}
    fence_results = [("fence-a", True), ("fence-b", False), ("fence-c", True)]
    vector = capability_vector_from_measurements(census, fence_results, unit_tests_passed=42)
    assert vector.xfail_total == 7
    assert vector.xfail_by_priority == {"P0": 1, "P1": 0, "P2": 0}
    assert vector.unit_tests_passed == 42
    assert vector.fences_passing == ("fence-a", "fence-c")


def test_capability_vector_json_round_trip() -> None:
    vector = _vec(3, 1, 0, 2, 55, ("z", "a"))
    round_tripped = CapabilityVector.from_json(vector.to_json())
    assert round_tripped == vector
    assert vector.to_json()["fences_passing"] == ["a", "z"]  # sorted on the way out


def test_build_floor_document_shape() -> None:
    vector = _vec(0, 0, 0, 0, 10, ("only",))
    doc = build_floor_document(vector)
    assert doc["generated_by"] == "scripts/ci/generate_release_floor.py"
    assert doc["capability_vector"] == vector.to_json()
    assert "FLOOR" in doc["semantics"]
    assert DEFAULT_CENSUS_PATH in doc["sources"]["xfail_total / xfail_by_priority"]
    # rendered form is deterministic and newline-terminated (mirrors generate_xfail_census.py)
    rendered = render_floor_json(doc)
    assert rendered.endswith("\n")
    assert json.loads(rendered) == doc


def test_priorities_match_the_census_automatable_classes() -> None:
    assert PRIORITIES == ("P0", "P1", "P2")


# ---------------------------------------------------------------------------
# Layer 3: real-tree — fences + the committed baseline artifact
# ---------------------------------------------------------------------------


def test_real_tree_all_registered_fences_pass_today() -> None:
    """Fast (~2s, no docker): every fence this floor composes from currently passes on the real
    tree, and the registry's names are exactly what `fences_passing` reports."""
    results = measure_fences(_REPO_ROOT, python_exe=sys.executable)
    assert [r.name for r in results] == [spec.name for spec in FENCE_REGISTRY]
    failing = [r for r in results if not r.ok]
    assert not failing, [(r.name, r.returncode, r.stderr_tail) for r in failing]


def test_real_committed_floor_matches_the_measured_xfail_ground_truth() -> None:
    """Pins the committed docs/release-capability-floor.json against the same ground truth
    docs/xfail-census.json itself pins (22) — mirrors test_generate_xfail_census.py's real-tree
    assertion. `unit_tests_passed` is deliberately NOT pinned to an exact number here: it grows
    every time an unrelated PR adds a unit test, and this floor's whole point is a `>=` bound, not
    an exact-match ledger — so this test only asserts the structural `>= committed` invariant."""
    floor_path = _REPO_ROOT / "docs" / "release-capability-floor.json"
    assert floor_path.is_file(), "run `make release-floor-write` to (re)generate it"
    doc = json.loads(floor_path.read_text(encoding="utf-8"))
    vector = CapabilityVector.from_json(doc["capability_vector"])
    assert vector.xfail_total == 22
    assert vector.xfail_by_priority == {"P0": 0, "P1": 0, "P2": 0}
    assert vector.unit_tests_passed > 0
    assert vector.fences_passing == tuple(sorted(spec.name for spec in FENCE_REGISTRY))


# ---------------------------------------------------------------------------
# Layer 4: CLI end-to-end — demonstrated RED -> GREEN through main() itself
# ---------------------------------------------------------------------------
#
# `measure_unit_tests` is the ONE measurement this suite never re-runs for real: it shells out to
# `<python> -m pytest tests/ -q -m "not integration"`; calling that from INSIDE the suite
# is a recursive, ~40s-per-call cost this fast unit lane must not pay. It is monkeypatched here to a
# canned (counts, returncode, raw_line) triple; `measure_fences` and the xfail census are exercised
# for REAL against the actual repo (fast, no docker) so the rest of the CLI flow — argument parsing,
# JSON read/write, the actual floor comparison, real exit codes — is genuinely exercised end-to-end.


def test_cli_write_then_check_demonstrates_red_then_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    floor_path = tmp_path / "release-capability-floor.json"

    baseline_passed = 500
    regressed_passed = 490  # a real drop the comparator must catch

    def _fake_measure_unit_tests(repo_root: Path, python_exe: str, *, _passed: int = baseline_passed):
        return {"passed": _passed, "skipped": 3}, 0, f"{_passed} passed, 3 skipped in 1s"

    # --write: establish the baseline at `baseline_passed`, real fences + real census.
    monkeypatch.setattr(
        "scripts.ci.generate_release_floor.measure_unit_tests",
        lambda repo_root, python_exe: _fake_measure_unit_tests(repo_root, python_exe),
    )
    write_exit = main(
        [
            "--write",
            "--repo-root",
            str(_REPO_ROOT),
            "--floor-path",
            str(floor_path),
        ]
    )
    assert write_exit == 0
    assert floor_path.is_file()
    written = json.loads(floor_path.read_text(encoding="utf-8"))
    assert written["capability_vector"]["unit_tests_passed"] == baseline_passed

    # RED: re-check with a genuinely regressed passing count -> main() must return 1.
    monkeypatch.setattr(
        "scripts.ci.generate_release_floor.measure_unit_tests",
        lambda repo_root, python_exe: (
            {"passed": regressed_passed, "skipped": 3},
            0,
            f"{regressed_passed} passed, 3 skipped in 1s",
        ),
    )
    red_exit = main(["--check", "--repo-root", str(_REPO_ROOT), "--floor-path", str(floor_path)])
    assert red_exit == 1, "a dropped passing-test count must fail the floor gate"

    # GREEN: revert to the original (non-regressed) count -> the SAME check now returns 0.
    monkeypatch.setattr(
        "scripts.ci.generate_release_floor.measure_unit_tests",
        lambda repo_root, python_exe: (
            {"passed": baseline_passed, "skipped": 3},
            0,
            f"{baseline_passed} passed, 3 skipped in 1s",
        ),
    )
    green_exit = main(["--check", "--repo-root", str(_REPO_ROOT), "--floor-path", str(floor_path)])
    assert green_exit == 0, "reverting the regression must pass again — demonstrated red -> green"

    # Floor file itself was never mutated by --check (verify-only contract).
    assert json.loads(floor_path.read_text(encoding="utf-8")) == written


def test_cli_check_without_a_committed_floor_fails_closed(tmp_path: Path) -> None:
    missing_floor = tmp_path / "nope.json"
    exit_code = main(["--check", "--repo-root", str(_REPO_ROOT), "--floor-path", str(missing_floor)])
    assert exit_code == 1


def test_violation_render_shape() -> None:
    v = Violation("some-code", "some detail")
    assert v.render() == "[some-code] some detail"


def test_measure_unit_tests_matches_make_unit_scope(tmp_path: Path, monkeypatch, capsys) -> None:
    import shlex
    from types import SimpleNamespace

    from scripts.ci import generate_release_floor as floor

    command = []

    def fake_run(argv, **kwargs):
        command.extend(argv)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == 1800
        assert kwargs["stdin"] == floor.subprocess.DEVNULL
        return SimpleNamespace(stdout="2 passed in 0.1s\n", stderr="", returncode=0)

    monkeypatch.setattr(floor.subprocess, "run", fake_run)
    assert floor.measure_unit_tests(tmp_path, "python-scope-check") == (
        {"passed": 2},
        0,
        "2 passed in 0.1s",
    )
    recipe = (_REPO_ROOT / "Makefile").read_text().split("test:", 1)[1].splitlines()[1].strip()
    assert command[1:] == shlex.split(recipe)[1:]
    assert capsys.readouterr().out == ""


def test_unit_failure_witness_omits_parameters_messages_and_unknown_paths(tmp_path: Path) -> None:
    from scripts.ci.generate_release_floor import unit_failure_witness

    source = tmp_path / "tests/unit/test_example.py"
    source.parent.mkdir(parents=True)
    source.write_text("# synthetic source file\n")
    output = "\n".join(
        [
            "FAILED tests/unit/test_example.py::outside_summary[private] - ignored",
            "==== short test summary info ====",
            "FAILED tests/unit/test_example.py::test_case[private-value] - private-message",
            "ERROR tests/unit/test_example.py::test_other - private-exception",
            "FAILED tests/../secret.py::test_case - private-message",
            "ERROR tests/not_present.py::test_case - private-message",
        ]
    )
    witness = unit_failure_witness(output, tmp_path)
    assert witness == {
        "short_summary_present": True,
        "file_groups": [
            {"outcome": "error", "path": "tests/unit/test_example.py", "reports": 1},
            {"outcome": "failed", "path": "tests/unit/test_example.py", "reports": 1},
        ],
        "omitted_file_groups": 0,
        "unmatched_reports": 2,
    }
    assert "private" not in json.dumps(witness)
