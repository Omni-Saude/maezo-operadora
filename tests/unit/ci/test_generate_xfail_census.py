"""Unit tests for the strict-xfail census generator (PLANS.md Hardening §0.8 Onda 0).

Three layers:

1. **Real-tree** tests run the full scan against `tests/integration/processes/` and assert the
   generator independently reproduces the ground truth confirmed 2026-08-10 (22 markers; 14
   ans_submit / 3 lgpd_dsr / 2 cred / 1 auth / 1 reembolso / 1 adequacao; six distinct `_*_REASON`
   constants, one per suite) — this is the load-bearing regression fence: if a future PR adds or
   flips a strict-xfail marker without updating the committed ledger, THIS test (and `make
   xfail-census-check`) is what turns that into a red CI run.
2. **Synthetic-tree** tests build throwaway `tmp_path` modules to drive each fail-closed path in
   isolation: multiline decorator parsing, reversed-kwarg-order/extra-kwargs canonical recognition,
   an unclassified reason constant, a stale REASON_CLASSIFICATION entry, drift detection between a
   stale committed ledger and the re-derived one, six non-canonical strict-xfail marker SHAPES that
   must each produce a loud `unrecognized-strict-xfail-shape` violation instead of vanishing
   silently (module-level `pytestmark`, class-level decorator, `pytest.param(marks=...)`, aliased
   imports ×2, non-literal `strict=`), and a subdirectory fixture proving `rglob` reaches nested
   test files for both the canonical and violation paths (MAJOR-2 repair).
3. **Content-pinned `build_census`** tests feed synthetic `MarkerRecord`s (never producible by
   echoing the committed `docs/xfail-census.json`) directly to `build_census` and assert its full
   return value — this is what actually kills a mutant that replaces `build_census`'s body with a
   hardcoded return of the committed ledger while every real-tree test above stays green (MAJOR-1
   repair: the real-tree tests alone are vacuous w.r.t. this function, since they only ever exercise
   it with the one input whose derived output legitimately equals that file).
4. **PLANS.md region** tests exercise `apply_plans_updates` directly — including rewrite
   idempotency (running it twice produces the same output the second time).
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ci.generate_xfail_census import (
    DEFAULT_TESTS_DIR,
    REASON_CLASSIFICATION,
    MarkerRecord,
    apply_plans_updates,
    build_census,
    check_stale_classification_entries,
    classify_markers,
    main,
    render_breakdown_fragment,
    render_census_json,
    render_total_fragment,
    scan_tree,
    suite_name,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS_DIR = _REPO_ROOT / DEFAULT_TESTS_DIR


# ---------------------------------------------------------------------------
# Real-tree: independently reproduces the 2026-08-10 ground truth
# ---------------------------------------------------------------------------


def test_real_tree_reproduces_the_confirmed_census() -> None:
    scan = scan_tree(_TESTS_DIR, _REPO_ROOT)
    assert not scan.violations, [v.render() for v in scan.violations]

    records, classify_violations = classify_markers(scan.markers)
    assert not classify_violations, [v.render() for v in classify_violations]

    stale = check_stale_classification_entries(scan.assigned_names)
    assert not stale, [v.render() for v in stale]

    census = build_census(records, DEFAULT_TESTS_DIR)
    assert census["total"] == 22
    assert census["per_suite"] == {
        "adequacao": 1,
        "ans_submit": 14,
        "auth": 1,
        "cred": 2,
        "lgpd_dsr": 3,
        "reembolso": 1,
    }
    assert census["per_classification"] == {
        "ADEQUACAO_RN259": 1,
        "AUTH_D07": 1,
        "CRED_GUARD_SHAPE_TE": 2,
        "LGPD_DPO": 3,
        "REEMBOLSO_D07": 1,
        "TISS_XSD_SME": 14,
    }


def test_real_tree_every_marker_reason_constant_is_a_distinct_reason_per_suite() -> None:
    """Ground-truth shape check: today, each of the 6 suites carrying strict-xfails cites exactly
    ONE `_*_REASON` constant (ans_submit's 14 markers all cite `_NOTIFY_REGULATORIO_GAP_REASON`,
    etc.) — pin that shape so a future PR that splits a suite's reason mid-flight is visible here,
    not just in the opaque total."""
    scan = scan_tree(_TESTS_DIR, _REPO_ROOT)
    records, violations = classify_markers(scan.markers)
    assert not violations

    reasons_by_suite: dict[str, set[str]] = {}
    for record in records:
        reasons_by_suite.setdefault(suite_name(record.file), set()).add(record.reason_constant)

    assert reasons_by_suite == {
        "adequacao": {"_ADEQUACAO_GAP_RULE_ORDER_INVERSION_REASON"},
        "ans_submit": {"_NOTIFY_REGULATORIO_GAP_REASON"},
        "auth": {"_AUTH_CEILING_D07_REASON"},
        "cred": {"_CRED_GUARD_SHAPE_MISMATCH_REASON"},
        "lgpd_dsr": {"_LGPD_SEND_RESPONSE_DPO_MERIT_GATE_REASON"},
        "reembolso": {"_REEMBOLSO_CEILING_D07_REASON"},
    }


def test_real_tree_no_non_strict_xfail_decorators_are_miscounted() -> None:
    """Sanity: the real tree carries zero `@pytest.mark.xfail` decorators WITHOUT `strict=True`
    under this directory — if one ever appears, it must be silently skipped (out of scope for this
    census), never crash the parser or get miscounted as a strict marker."""
    scan = scan_tree(_TESTS_DIR, _REPO_ROOT)
    assert len(scan.markers) == 22


# ---------------------------------------------------------------------------
# Synthetic tree: parsing (multiline-safe)
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_parses_a_multiline_xfail_decorator(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pytest.mark.xfail(\n"
        "    reason=_SYNTH_REASON,\n"
        "    strict=True,\n"
        ")\n"
        "async def test_something() -> None:\n"
        "    assert False\n",
    )
    scan = scan_tree(tmp_path, tmp_path)
    assert not scan.violations
    assert len(scan.markers) == 1
    marker = scan.markers[0]
    assert marker.test == "test_something"
    assert marker.reason_constant == "_SYNTH_REASON"
    assert marker.reason_kind == "constant"


def test_ignores_xfail_decorator_without_strict_true(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pytest.mark.xfail(reason=_SYNTH_REASON)\n"
        "async def test_weak() -> None:\n"
        "    assert False\n",
    )
    scan = scan_tree(tmp_path, tmp_path)
    assert not scan.violations
    assert scan.markers == []


def test_reversed_kwarg_order_still_recognized_as_canonical(tmp_path: Path) -> None:
    """`strict=True` before `reason=` — kwarg order must not matter to canonical recognition, and
    must NOT trip the new unrecognized-shape sweep (MAJOR-2 regression guard)."""
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pytest.mark.xfail(strict=True, reason=_SYNTH_REASON)\n"
        "async def test_something() -> None:\n"
        "    assert False\n",
    )
    scan = scan_tree(tmp_path, tmp_path)
    assert not scan.violations, scan.violations
    assert len(scan.markers) == 1
    assert scan.markers[0].reason_constant == "_SYNTH_REASON"


def test_extra_kwargs_still_recognized_as_canonical(tmp_path: Path) -> None:
    """An extra, unrelated kwarg alongside `reason=`/`strict=True` must not defeat canonical
    recognition or trip the new unrecognized-shape sweep (MAJOR-2 regression guard)."""
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pytest.mark.xfail(reason=_SYNTH_REASON, strict=True, run=False)\n"
        "async def test_something() -> None:\n"
        "    assert False\n",
    )
    scan = scan_tree(tmp_path, tmp_path)
    assert not scan.violations, scan.violations
    assert len(scan.markers) == 1


def test_literal_reason_parses_but_is_unclassifiable(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "@pytest.mark.xfail(reason='inline literal reason', strict=True)\n"
        "async def test_literal() -> None:\n"
        "    assert False\n",
    )
    scan = scan_tree(tmp_path, tmp_path)
    assert not scan.violations
    assert len(scan.markers) == 1
    assert scan.markers[0].reason_constant is None
    assert scan.markers[0].reason_kind == "literal"

    records, violations = classify_markers(scan.markers)
    assert records == []
    assert len(violations) == 1
    assert violations[0].code == "unclassified-literal-reason"


# ---------------------------------------------------------------------------
# Fail-closed: unrecognized strict-xfail marker shapes (MAJOR-2 repair).
#
# Six shapes an adversarial GK proved were silently invisible to the census (no marker, no
# violation — a real strict-xfail marker in the tree with `--check` still PASS): module-level
# `pytestmark`, a class-level decorator, `pytest.param(..., marks=...)`, `import pytest as pt`,
# `from pytest import mark`, and a non-literal `strict=` value. Each fixture below asserts the
# marker produces a LOUD `unrecognized-strict-xfail-shape` violation instead — the census stays
# canonical-only, but non-canonical strict markers now BLOCK the gate rather than vanish from it.
# ---------------------------------------------------------------------------


def _assert_single_unrecognized_shape_violation(tmp_path: Path) -> None:
    scan = scan_tree(tmp_path, tmp_path)
    assert scan.markers == [], "a non-canonical shape must never be silently counted as a marker"
    codes = [v.code for v in scan.violations]
    assert codes == ["unrecognized-strict-xfail-shape"], scan.violations


def test_module_level_pytestmark_xfail_is_not_silently_dropped(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "pytestmark = pytest.mark.xfail(reason=_SYNTH_REASON, strict=True)\n\n"
        "def test_something() -> None:\n"
        "    assert False\n",
    )
    _assert_single_unrecognized_shape_violation(tmp_path)


def test_class_level_xfail_decorator_is_not_silently_dropped(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pytest.mark.xfail(reason=_SYNTH_REASON, strict=True)\n"
        "class TestSomething:\n"
        "    def test_method(self) -> None:\n"
        "        assert False\n",
    )
    _assert_single_unrecognized_shape_violation(tmp_path)


def test_pytest_param_marks_xfail_is_not_silently_dropped(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pytest.mark.parametrize(\n"
        "    'x', [pytest.param(1, marks=pytest.mark.xfail(reason=_SYNTH_REASON, strict=True))]\n"
        ")\n"
        "def test_something(x: int) -> None:\n"
        "    assert False\n",
    )
    _assert_single_unrecognized_shape_violation(tmp_path)


def test_import_pytest_as_alias_xfail_is_not_silently_dropped(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest as pt\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pt.mark.xfail(reason=_SYNTH_REASON, strict=True)\n"
        "def test_something() -> None:\n"
        "    assert False\n",
    )
    _assert_single_unrecognized_shape_violation(tmp_path)


def test_from_pytest_import_mark_xfail_is_not_silently_dropped(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "from pytest import mark\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@mark.xfail(reason=_SYNTH_REASON, strict=True)\n"
        "def test_something() -> None:\n"
        "    assert False\n",
    )
    _assert_single_unrecognized_shape_violation(tmp_path)


def test_bare_xfail_name_import_is_not_silently_dropped(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "from pytest.mark import xfail\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@xfail(reason=_SYNTH_REASON, strict=True)\n"
        "def test_something() -> None:\n"
        "    assert False\n",
    )
    _assert_single_unrecognized_shape_violation(tmp_path)


def test_non_literal_strict_value_is_not_silently_dropped(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n"
        "_STRICT = True\n\n"
        "@pytest.mark.xfail(reason=_SYNTH_REASON, strict=_STRICT)\n"
        "def test_something() -> None:\n"
        "    assert False\n",
    )
    _assert_single_unrecognized_shape_violation(tmp_path)


def test_subdirectory_test_files_are_scanned_for_canonical_markers(tmp_path: Path) -> None:
    """Positive control for the `rglob` fix: a CANONICAL marker nested two directories deep must be
    found like any top-level one — before the fix, `glob("test_*.py")` never even opened this file."""
    _write(
        tmp_path / "sub" / "nested" / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pytest.mark.xfail(reason=_SYNTH_REASON, strict=True)\n"
        "def test_something() -> None:\n"
        "    assert False\n",
    )
    scan = scan_tree(tmp_path, tmp_path)
    assert not scan.violations, scan.violations
    assert len(scan.markers) == 1
    assert scan.markers[0].file == "sub/nested/test_sp_op_synthetic_001.py"


def test_subdirectory_test_files_are_scanned_for_unrecognized_shapes(tmp_path: Path) -> None:
    """A non-canonical shape nested in a subdirectory must ALSO produce the violation — proves the
    `rglob` fix and the shape-detection fix compose: before either fix this file was doubly
    invisible (never opened AND, even if opened, silently unrecognized)."""
    _write(
        tmp_path / "sub" / "nested" / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "pytestmark = pytest.mark.xfail(reason=_SYNTH_REASON, strict=True)\n\n"
        "def test_something() -> None:\n"
        "    assert False\n",
    )
    _assert_single_unrecognized_shape_violation(tmp_path)


# ---------------------------------------------------------------------------
# Fail-closed: unclassified constant
# ---------------------------------------------------------------------------


def test_unclassified_constant_fails_closed(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_BRAND_NEW_REASON = 'never seen before'\n\n"
        "@pytest.mark.xfail(reason=_BRAND_NEW_REASON, strict=True)\n"
        "async def test_new_gap() -> None:\n"
        "    assert False\n",
    )
    scan = scan_tree(tmp_path, tmp_path)
    assert not scan.violations

    records, violations = classify_markers(scan.markers)
    assert records == []
    assert len(violations) == 1
    assert violations[0].code == "unclassified-constant"
    assert "_BRAND_NEW_REASON" in violations[0].detail


def test_end_to_end_main_fails_on_unclassified_constant(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests" / "integration" / "processes"
    _write(
        tests_dir / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_BRAND_NEW_REASON = 'never seen before'\n\n"
        "@pytest.mark.xfail(reason=_BRAND_NEW_REASON, strict=True)\n"
        "async def test_new_gap() -> None:\n"
        "    assert False\n",
    )
    _write(tmp_path / "PLANS.md", "no markers here\n")
    exit_code = main(
        [
            "--check",
            "--repo-root",
            str(tmp_path),
            "--tests-dir",
            "tests/integration/processes",
            "--census-path",
            "docs/xfail-census.json",
            "--plans-path",
            "PLANS.md",
        ]
    )
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Fail-closed: stale REASON_CLASSIFICATION entry
# ---------------------------------------------------------------------------


def test_stale_classification_entry_fails_closed(tmp_path: Path) -> None:
    _write(
        tmp_path / "test_sp_op_synthetic_001.py",
        "import pytest\n\nX = 1\n",
    )
    scan = scan_tree(tmp_path, tmp_path)
    assert not scan.violations
    fake_classification = {"_A_RETIRED_CONSTANT_THAT_NO_LONGER_EXISTS_REASON": "P1"}
    stale = check_stale_classification_entries(scan.assigned_names, fake_classification)
    assert len(stale) == 1
    assert stale[0].code == "stale-classification-entry"
    assert "_A_RETIRED_CONSTANT_THAT_NO_LONGER_EXISTS_REASON" in stale[0].detail


def test_real_reason_classification_has_no_stale_entries() -> None:
    """The committed REASON_CLASSIFICATION mapping, checked against the real tree — every entry's
    constant must still be assigned somewhere under tests/integration/processes/."""
    scan = scan_tree(_TESTS_DIR, _REPO_ROOT)
    stale = check_stale_classification_entries(scan.assigned_names)
    assert stale == [], [v.render() for v in stale]
    # Non-vacuity: the mapping isn't accidentally empty.
    assert len(REASON_CLASSIFICATION) == 6


# ---------------------------------------------------------------------------
# Content-pinned `build_census` (MAJOR-1 repair).
#
# An adversarial GK proved the test suite was vacuous w.r.t. `build_census`: replacing its BODY
# wholesale with a hardcode that ignores its arguments and returns the committed
# `docs/xfail-census.json` verbatim kept every real-tree test above green (since those tests only
# ever feed it the REAL records, whose derived output happens to equal that file). The two fixtures
# below feed `build_census` synthetic `MarkerRecord`s that could NOT be produced by echoing
# `docs/xfail-census.json` — different totals, different `tests_dir`, different suite names,
# different classifications — and assert its FULL return value, so any function body that does not
# actually derive `total`/`per_suite`/`per_classification`/`markers` from its arguments fails one or
# both of these tests. Two independent variants (different totals) make hardcode-evasion via a
# single lucky constant strictly impossible.
# ---------------------------------------------------------------------------


def test_build_census_derives_content_from_arguments_variant_a() -> None:
    records = [
        MarkerRecord(
            file="tests/integration/processes/test_sp_op_zzsynthA_001.py",
            line=42,
            test="test_one",
            reason_constant="_ZZ_SYNTH_A_REASON",
            classification="P0",
        ),
        MarkerRecord(
            file="tests/integration/processes/test_sp_op_zzsynthA_001.py",
            line=99,
            test="test_two",
            reason_constant="_ZZ_SYNTH_A_REASON",
            classification="P0",
        ),
        MarkerRecord(
            file="tests/integration/processes/test_sp_op_zzsynthB_002.py",
            line=7,
            test="test_three",
            reason_constant="_ZZ_SYNTH_B_REASON",
            classification="LGPD_DPO",
        ),
    ]
    census = build_census(records, "zz/synthetic/dir/A")
    assert census == {
        "generated_by": "scripts/ci/generate_xfail_census.py",
        "tests_dir": "zz/synthetic/dir/A",
        "total": 3,
        "per_suite": {"zzsynthA": 2, "zzsynthB": 1},
        "per_classification": {"LGPD_DPO": 1, "P0": 2},
        "markers": [
            {
                "file": "tests/integration/processes/test_sp_op_zzsynthA_001.py",
                "line": 42,
                "test": "test_one",
                "reason_constant": "_ZZ_SYNTH_A_REASON",
                "classification": "P0",
            },
            {
                "file": "tests/integration/processes/test_sp_op_zzsynthA_001.py",
                "line": 99,
                "test": "test_two",
                "reason_constant": "_ZZ_SYNTH_A_REASON",
                "classification": "P0",
            },
            {
                "file": "tests/integration/processes/test_sp_op_zzsynthB_002.py",
                "line": 7,
                "test": "test_three",
                "reason_constant": "_ZZ_SYNTH_B_REASON",
                "classification": "LGPD_DPO",
            },
        ],
    }
    # Real-tree total is 22 — this synthetic total must not accidentally coincide with it, or a
    # partially-hardcoded mutant (e.g. one keyed off `len(records) == 22`) could slip through.
    assert census["total"] != 22


def test_build_census_derives_content_from_arguments_variant_b() -> None:
    """A SECOND fixture with a different total/suite/classification/tests_dir — makes a
    single-constant hardcode (or a mutant that special-cases variant A's shape) strictly
    impossible to pass both this test and variant A simultaneously."""
    records = [
        MarkerRecord(
            file="tests/integration/processes/test_sp_op_zzsynthC_003.py",
            line=1,
            test="test_solo",
            reason_constant="_ZZ_SYNTH_C_REASON",
            classification="P2",
        ),
    ]
    census = build_census(records, "another/synthetic/path")
    assert census == {
        "generated_by": "scripts/ci/generate_xfail_census.py",
        "tests_dir": "another/synthetic/path",
        "total": 1,
        "per_suite": {"zzsynthC": 1},
        "per_classification": {"P2": 1},
        "markers": [
            {
                "file": "tests/integration/processes/test_sp_op_zzsynthC_003.py",
                "line": 1,
                "test": "test_solo",
                "reason_constant": "_ZZ_SYNTH_C_REASON",
                "classification": "P2",
            },
        ],
    }
    assert census["total"] != 22


def test_build_census_orders_markers_by_file_then_line_regardless_of_input_order() -> None:
    """Feed `build_census` records deliberately OUT of (file, line) order — a hardcode-return or a
    mutant that just echoes `records` unsorted would fail this, since the real generator always
    sorts by `(file, line)` before rendering."""
    records = [
        MarkerRecord(
            file="tests/integration/processes/test_sp_op_zzsynthZ_009.py",
            line=500,
            test="test_late",
            reason_constant="_ZZ_SYNTH_Z_REASON",
            classification="P1",
        ),
        MarkerRecord(
            file="tests/integration/processes/test_sp_op_zzsynthZ_009.py",
            line=10,
            test="test_early",
            reason_constant="_ZZ_SYNTH_Z_REASON",
            classification="P1",
        ),
    ]
    census = build_census(records, "zz/order")
    assert [m["test"] for m in census["markers"]] == ["test_early", "test_late"]
    assert [m["line"] for m in census["markers"]] == [10, 500]


# ---------------------------------------------------------------------------
# Drift detection (--check)
# ---------------------------------------------------------------------------


def _write_synthetic_suite(tests_dir: Path) -> None:
    _write(
        tests_dir / "test_sp_op_synthetic_001.py",
        "import pytest\n\n"
        "_SYNTH_REASON = 'because'\n\n"
        "@pytest.mark.xfail(reason=_SYNTH_REASON, strict=True)\n"
        "async def test_something() -> None:\n"
        "    assert False\n",
    )


def _with_only_synth_classification() -> dict[str, str]:
    """Snapshot the real REASON_CLASSIFICATION, then MUTATE IT IN PLACE (clear + set) to hold only
    `_SYNTH_REASON`. `classify_markers`/`check_stale_classification_entries` bind this dict OBJECT
    as their default parameter at def-time, so replacing the module attribute with a new dict
    (`monkeypatch.setattr`) would not be visible to already-bound defaults — an in-place mutation
    is required, and is what real callers (`main`) actually observe. Returns the original snapshot
    so the caller can restore it in a `finally` block."""
    original = dict(REASON_CLASSIFICATION)
    REASON_CLASSIFICATION.clear()
    REASON_CLASSIFICATION["_SYNTH_REASON"] = "P1"
    return original


def _restore_classification(original: dict[str, str]) -> None:
    REASON_CLASSIFICATION.clear()
    REASON_CLASSIFICATION.update(original)


def test_check_detects_stale_committed_ledger(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests" / "integration" / "processes"
    _write_synthetic_suite(tests_dir)
    census_path = tmp_path / "docs" / "xfail-census.json"
    census_path.parent.mkdir(parents=True, exist_ok=True)
    census_path.write_text(json.dumps({"total": 999}), encoding="utf-8")  # deliberately stale
    plans_path = tmp_path / "PLANS.md"
    _write(
        plans_path,
        "history <!-- xfail-census:total:begin -->999<!-- xfail-census:total:end --> "
        "breakdown <!-- xfail-census:breakdown:begin -->WRONG<!-- xfail-census:breakdown:end -->\n",
    )
    original = _with_only_synth_classification()
    try:
        exit_code = main(
            [
                "--check",
                "--repo-root",
                str(tmp_path),
                "--tests-dir",
                "tests/integration/processes",
                "--census-path",
                "docs/xfail-census.json",
                "--plans-path",
                "PLANS.md",
            ]
        )
    finally:
        _restore_classification(original)
    assert exit_code == 1


def test_write_then_check_round_trips_clean(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests" / "integration" / "processes"
    _write_synthetic_suite(tests_dir)
    plans_path = tmp_path / "PLANS.md"
    _write(
        plans_path,
        "history <!-- xfail-census:total:begin --><!-- xfail-census:total:end --> "
        "breakdown <!-- xfail-census:breakdown:begin --><!-- xfail-census:breakdown:end -->\n",
    )
    original = _with_only_synth_classification()
    try:
        write_exit = main(
            [
                "--write",
                "--repo-root",
                str(tmp_path),
                "--tests-dir",
                "tests/integration/processes",
                "--census-path",
                "docs/xfail-census.json",
                "--plans-path",
                "PLANS.md",
            ]
        )
        assert write_exit == 0
        check_exit = main(
            [
                "--check",
                "--repo-root",
                str(tmp_path),
                "--tests-dir",
                "tests/integration/processes",
                "--census-path",
                "docs/xfail-census.json",
                "--plans-path",
                "PLANS.md",
            ]
        )
        assert check_exit == 0
    finally:
        _restore_classification(original)


# ---------------------------------------------------------------------------
# PLANS.md region rewrite (including idempotency)
# ---------------------------------------------------------------------------


def test_apply_plans_updates_rewrites_only_the_marked_regions() -> None:
    text = (
        "prefix history 95 -> 36 -> 24 -> "
        "<!-- xfail-census:total:begin -->OLD<!-- xfail-census:total:end --> unrelated prose\n"
        "breakdown: <!-- xfail-census:breakdown:begin -->OLD BREAKDOWN"
        "<!-- xfail-census:breakdown:end --> more prose that must survive untouched\n"
    )
    new_text, violations = apply_plans_updates(text, "22", "TISS-XSD SME ×14")
    assert not violations
    assert "22" in new_text
    assert "TISS-XSD SME ×14" in new_text
    assert "OLD" not in new_text
    assert "unrelated prose" in new_text
    assert "more prose that must survive untouched" in new_text


def test_apply_plans_updates_is_idempotent() -> None:
    text = (
        "history <!-- xfail-census:total:begin -->OLD<!-- xfail-census:total:end --> "
        "breakdown <!-- xfail-census:breakdown:begin -->OLD<!-- xfail-census:breakdown:end -->\n"
    )
    once, violations_1 = apply_plans_updates(text, "22", "X ×1 · Y ×2")
    assert not violations_1
    twice, violations_2 = apply_plans_updates(once, "22", "X ×1 · Y ×2")
    assert not violations_2
    assert once == twice


def test_apply_plans_updates_fails_closed_on_missing_markers() -> None:
    text = "no markers anywhere in this text\n"
    _, violations = apply_plans_updates(text, "22", "X ×1")
    assert violations
    assert violations[0].code == "plans-marker-missing"


def test_apply_plans_updates_fails_closed_on_duplicated_markers() -> None:
    text = (
        "<!-- xfail-census:total:begin -->A<!-- xfail-census:total:end -->"
        "<!-- xfail-census:total:begin -->B<!-- xfail-census:total:end -->"
        "<!-- xfail-census:breakdown:begin -->C<!-- xfail-census:breakdown:end -->"
    )
    _, violations = apply_plans_updates(text, "22", "X ×1")
    assert violations
    assert violations[0].code == "plans-marker-ambiguous"


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def test_render_total_fragment() -> None:
    assert render_total_fragment(22) == "22"
    assert render_total_fragment(0) == "0"


def test_render_breakdown_fragment_orders_by_class_order_and_skips_zero_counts() -> None:
    fragment = render_breakdown_fragment(
        {
            "ADEQUACAO_RN259": 1,
            "TISS_XSD_SME": 14,
            "AUTH_D07": 0,  # zero count -> must not appear
        }
    )
    assert fragment == "TISS-XSD SME ×14 · adequacao RN259 ×1"


def test_render_census_json_is_deterministic() -> None:
    census = {"total": 1, "per_suite": {"a": 1}, "per_classification": {"P1": 1}, "markers": []}
    first = render_census_json(census)
    second = render_census_json(census)
    assert first == second
    assert first.endswith("\n")


def test_suite_name_extracts_from_conventional_filename() -> None:
    assert suite_name("tests/integration/processes/test_sp_op_ans_submit_001.py") == "ans_submit"
    assert suite_name("tests/integration/processes/test_sp_op_cred_001.py") == "cred"


def test_suite_name_falls_back_for_unconventional_filename() -> None:
    assert suite_name("tests/integration/processes/test_t33_a1_something.py") == "t33_a1_something"
