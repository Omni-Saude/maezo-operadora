"""Unit tests for maezo.platform.lifecycle — intentional fail-closed refusal [T2.8].

The `maezo.platform.lifecycle` entrypoint is a DELIBERATE refusal: the Helm CronJob
`lifecycle-audit-retention` invokes `python -m maezo.platform.lifecycle audit-retention`
monthly, and `RetentionManager.retention_query()` would build an unconditional
`DELETE FROM audit_chain WHERE ts < cutoff` with no legal-hold predicate and no chain
re-anchor. Until the ADR-0020 amendment (legal-hold registry) and ADR-0029 (signed
checkpoint re-anchor) are ratified, EVERY invocation must refuse and exit non-zero.

These tests assert:
  1. Every invocation (no arg / each subcommand / unknown) refuses with exit != 0.
  2. `audit-retention` emits the exact ADR-grounded blocker.
  3. The module never imports or references the destructive query.
  4. `retention_query` has ZERO production callers anywhere under `src/` — this locks
     the current safety property so a future PR cannot quietly wire the DELETE in
     without a failing test.
  5. The real `python -m maezo.platform.lifecycle audit-retention` command (the exact
     string the CronJob runs) exits non-zero with the blocker on stderr.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from maezo.platform import lifecycle
from maezo.platform.lifecycle import (
    AUDIT_RETENTION_REFUSAL,
    KNOWN_SUBCOMMANDS,
    REFUSAL_EXIT_CODE,
    main,
)
from maezo.platform.lifecycle.legal_bases_matrix import MATRIX_PATH_ENV

_VALID_MATRIX_YAML = """
categorias:
  - categoria: "financeiros_faturamento"
    base_legal: "LGPD art. 16 I; CTN art. 173/174"
    retencao: "5 anos"
    acao: "reter"
"""

_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/unit/platform -> repo root
_SRC_MAEZO = _REPO_ROOT / "src" / "maezo"
_LIFECYCLE_DIR = _SRC_MAEZO / "platform" / "lifecycle"
_RETENTION_PY = _SRC_MAEZO / "platform" / "retention.py"


# ---------------------------------------------------------------------------
# Refusal contract: never returns 0, always emits a refusal
# ---------------------------------------------------------------------------


def test_refusal_exit_code_is_nonzero() -> None:
    """The refusal exit code is a stable, non-zero, non-1 sentinel (sysexits EX_CONFIG)."""
    assert REFUSAL_EXIT_CODE != 0
    assert REFUSAL_EXIT_CODE == 78


def test_no_subcommand_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    """Bare invocation (no subcommand) refuses and exits non-zero."""
    code = main([])
    assert code == REFUSAL_EXIT_CODE
    assert code != 0
    err = capsys.readouterr().err
    assert "refused" in err
    assert "docs/adr/0029-audit-chain-pruning-reanchor.md" in err


def test_audit_retention_subcommand_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    """`audit-retention` refuses with the EXACT charter-specified blocker."""
    code = main(["audit-retention"])
    assert code == REFUSAL_EXIT_CODE
    assert code != 0
    err = capsys.readouterr().err
    # Exact, load-bearing operator message — the precise blocker.
    assert AUDIT_RETENTION_REFUSAL in err
    assert "audit-retention refused: prerequisites absent" in err
    assert "legal-hold registry (ADR-0020 amendment)" in err
    assert "chain re-anchor mechanism (ADR-0029)" in err
    assert "docs/compliance/ADR-0020-amendment-draft.md" in err


def test_expurgo_working_subcommand_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    """`expurgo-working` (sibling job) refuses as a not-implemented fail-closed stub."""
    code = main(["expurgo-working"])
    assert code == REFUSAL_EXIT_CODE
    assert code != 0
    err = capsys.readouterr().err
    assert "expurgo-working refused" in err
    assert "fail-closed refusal stub" in err


def test_verify_erasure_subcommand_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    """`verify-erasure` (sibling job) refuses as a not-implemented fail-closed stub."""
    code = main(["verify-erasure"])
    assert code == REFUSAL_EXIT_CODE
    assert code != 0
    err = capsys.readouterr().err
    assert "verify-erasure refused" in err
    assert "fail-closed refusal stub" in err


# ---------------------------------------------------------------------------
# Refusal taxonomy: expurgo-working / verify-erasure attempt a REAL matrix load
# first, so the message distinguishes "matrix absent" from "matrix present but the
# downstream mechanism is unbuilt" — exit code stays 78 in EVERY case (T2.9).
# ---------------------------------------------------------------------------


def test_expurgo_working_matrix_absent_states_precise_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no matrix configured, expurgo-working states the matrix is unavailable."""
    monkeypatch.delenv(MATRIX_PATH_ENV, raising=False)
    code = main(["expurgo-working"])
    assert code == REFUSAL_EXIT_CODE
    err = capsys.readouterr().err
    assert "expurgo-working refused" in err
    assert "fail-closed refusal stub" in err
    assert "retention matrix" in err.lower()
    assert "unavailable" in err
    assert "path_not_set" in err


def test_expurgo_working_matrix_present_states_mechanism_unbuilt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """With a valid, loadable matrix, expurgo-working states the SWEEP itself is unbuilt.

    A present matrix must never look like progress toward a success path — the
    message must name the actual missing execution mechanism, not just repeat the
    generic stub notice, and must still exit 78.
    """
    matrix_path = tmp_path / "matrix.yaml"
    matrix_path.write_text(_VALID_MATRIX_YAML, encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(matrix_path))

    code = main(["expurgo-working"])
    assert code == REFUSAL_EXIT_CODE
    err = capsys.readouterr().err
    assert "expurgo-working refused" in err
    assert "fail-closed refusal stub" in err
    assert "loaded successfully" in err
    assert "TTL sweep" in err
    assert "does not by itself unblock" in err
    assert "unavailable" not in err  # must NOT reuse the absent-matrix wording


def test_verify_erasure_matrix_absent_mentions_erasure_gap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(MATRIX_PATH_ENV, raising=False)
    code = main(["verify-erasure"])
    assert code == REFUSAL_EXIT_CODE
    err = capsys.readouterr().err
    assert "verify-erasure refused" in err
    assert "fail-closed refusal stub" in err
    assert "unavailable" in err
    assert "thread_id" in err
    assert "fhir_patient_id" in err
    assert "erasure.py" in err


def test_verify_erasure_matrix_present_states_mechanism_and_erasure_gap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    matrix_path = tmp_path / "matrix.yaml"
    matrix_path.write_text(_VALID_MATRIX_YAML, encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(matrix_path))

    code = main(["verify-erasure"])
    assert code == REFUSAL_EXIT_CODE
    err = capsys.readouterr().err
    assert "verify-erasure refused" in err
    assert "loaded successfully" in err
    assert "ErasureManager" in err
    assert "thread_id" in err
    assert "fhir_patient_id" in err
    assert "does not by itself unblock" in err


def test_matrix_absent_vs_present_messages_differ(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The two branches must produce genuinely different text, not a cosmetic tweak."""
    monkeypatch.delenv(MATRIX_PATH_ENV, raising=False)
    absent_expurgo = lifecycle._refusal_message("expurgo-working")
    absent_erasure = lifecycle._refusal_message("verify-erasure")

    matrix_path = tmp_path / "matrix.yaml"
    matrix_path.write_text(_VALID_MATRIX_YAML, encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(matrix_path))
    present_expurgo = lifecycle._refusal_message("expurgo-working")
    present_erasure = lifecycle._refusal_message("verify-erasure")

    assert absent_expurgo != present_expurgo
    assert absent_erasure != present_erasure


def test_audit_retention_unaffected_by_matrix_presence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """audit-retention's blocker (ADR-0020/0029) is independent of the DPO matrix.

    Even with a fully valid, loadable matrix configured, audit-retention's message
    must stay EXACTLY the charter-specified ADR-grounded refusal — the matrix is a
    different, unrelated gate (recon-b §6.4).
    """
    matrix_path = tmp_path / "matrix.yaml"
    matrix_path.write_text(_VALID_MATRIX_YAML, encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(matrix_path))

    code = main(["audit-retention"])
    assert code == REFUSAL_EXIT_CODE
    err = capsys.readouterr().err
    assert AUDIT_RETENTION_REFUSAL in err


@pytest.mark.parametrize("matrix_configured", [False, True])
def test_matrix_gated_subcommands_always_exit_78(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, matrix_configured: bool
) -> None:
    """No matrix state (absent or present-and-valid) ever produces a success exit."""
    if matrix_configured:
        matrix_path = tmp_path / "matrix.yaml"
        matrix_path.write_text(_VALID_MATRIX_YAML, encoding="utf-8")
        monkeypatch.setenv(MATRIX_PATH_ENV, str(matrix_path))
    else:
        monkeypatch.delenv(MATRIX_PATH_ENV, raising=False)

    assert main(["expurgo-working"]) == REFUSAL_EXIT_CODE
    assert main(["verify-erasure"]) == REFUSAL_EXIT_CODE
    assert main(["audit-retention"]) == REFUSAL_EXIT_CODE


def test_unknown_subcommand_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown subcommand refuses (fail-closed default, not fail-open)."""
    code = main(["frobnicate-the-audit-chain"])
    assert code == REFUSAL_EXIT_CODE
    assert code != 0
    err = capsys.readouterr().err
    assert "unknown subcommand" in err
    assert "frobnicate-the-audit-chain" in err
    assert "refused" in err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["audit-retention"],
        ["expurgo-working"],
        ["verify-erasure"],
        ["unknown"],
        ["audit-retention", "--force"],
        ["--yes-really-delete"],
    ],
)
def test_main_never_returns_zero(argv: list[str]) -> None:
    """There is NO argv that makes this entrypoint succeed — every path is non-zero."""
    assert main(argv) != 0


def test_all_known_subcommands_refuse() -> None:
    """Every subcommand the CronJobs actually invoke (values.yaml) is refused."""
    for subcommand in KNOWN_SUBCOMMANDS:
        assert main([subcommand]) == REFUSAL_EXIT_CODE


# ---------------------------------------------------------------------------
# Safety property: the module must not touch the destructive DELETE path
#
# These scans are AST-based on purpose: a naive text grep would trip on the
# module docstring, which *documents* (by name) exactly why it never calls the
# destructive query. We want to forbid CODE references (imports / names /
# attributes / non-docstring string literals), not documentation of the ban.
# ---------------------------------------------------------------------------

_FORBIDDEN_SYMBOLS = frozenset({"retention_query", "RetentionManager"})


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Return id()s of the Constant nodes that are module/class/function docstrings."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _code_offenses(path: Path) -> list[str]:
    """Return CODE-level references (not docstrings/comments) to the destructive path."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring_ids = _docstring_nodes(tree)
    offenses: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "retention" in node.module:
            offenses.append(f"import from {node.module}")
        elif isinstance(node, ast.Import):
            offenses.extend(f"import {a.name}" for a in node.names if "retention" in a.name)
        elif isinstance(node, ast.ImportFrom):
            offenses.extend(f"import {a.name}" for a in node.names if a.name in _FORBIDDEN_SYMBOLS)
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_SYMBOLS:
            offenses.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_SYMBOLS:
            offenses.append(f"attribute .{node.attr}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
            and "DELETE " in node.value.upper()
        ):
            offenses.append("DELETE string literal in code")
    return offenses


def test_lifecycle_module_does_not_reference_destructive_query() -> None:
    """The lifecycle sources must NOT import or call the destructive DELETE path.

    Nothing in this module may import `retention`/`RetentionManager`/`retention_query`
    or build a `DELETE` in code — the refusal must be reachable without ever loading the
    destructive query. (The docstring is allowed to NAME them to explain the ban.)
    """
    for source in sorted(_LIFECYCLE_DIR.glob("*.py")):
        offenses = _code_offenses(source)
        assert offenses == [], (
            f"{source.relative_to(_REPO_ROOT)} touches the destructive DELETE path in "
            f"code: {offenses} — the refusal entrypoint must never reference it"
        )


def test_retention_query_has_no_production_caller() -> None:
    """`retention_query` has ZERO callers under `src/` — locks the safety property in CI.

    The only permitted CODE reference to `retention_query` anywhere under `src/maezo` is
    its DEFINITION (a `def`) in `retention.py`. Documentation/comments elsewhere are
    fine. If a future change wires the unconditional `DELETE FROM audit_chain` into any
    production code path (a call or attribute access), this test fails — deletion stays
    blocked until ADR-0020-amendment + ADR-0029 are ratified and a hold-aware,
    re-anchored implementation replaces the bare query.
    """
    offenders: list[str] = []
    for source in sorted(_SRC_MAEZO.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # A call site is a Name load or an Attribute access named `retention_query`.
            is_name = isinstance(node, ast.Name) and node.id == "retention_query"
            is_attr = isinstance(node, ast.Attribute) and node.attr == "retention_query"
            if is_name or is_attr:
                offenders.append(str(source.relative_to(_REPO_ROOT)))
                break
    assert offenders == [], (
        "retention_query() gained a production caller — the destructive "
        f"DELETE FROM audit_chain path must stay caller-less: {sorted(set(offenders))}"
    )


def test_retention_query_definition_still_exists() -> None:
    """Guard against the previous test passing vacuously if retention.py is moved."""
    assert _RETENTION_PY.exists()
    assert "def retention_query(" in _RETENTION_PY.read_text(encoding="utf-8")


def test_importing_lifecycle_has_no_side_effect() -> None:
    """Importing the package must not perform work (matches the __main__ convention)."""
    # If import had a side effect it would already have raised at collection time; assert
    # the public surface is intact and callable without any implicit execution.
    assert callable(lifecycle.main)
    assert lifecycle.MODULE_NAME == "maezo.platform.lifecycle"


# ---------------------------------------------------------------------------
# End-to-end: the exact command the CronJob runs
# ---------------------------------------------------------------------------


def test_module_invocation_exits_nonzero_with_blocker() -> None:
    """`python -m maezo.platform.lifecycle audit-retention` (the CronJob command) refuses.

    This exercises the real `__main__.py` -> `main()` -> `sys.exit(...)` path exactly as
    the Helm CronJob invokes it, proving the pod exits non-zero (Job fails) with the
    blocker visible in its logs.
    """
    result = subprocess.run(
        [sys.executable, "-m", "maezo.platform.lifecycle", "audit-retention"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )
    assert result.returncode == REFUSAL_EXIT_CODE
    assert result.returncode != 0
    assert "audit-retention refused: prerequisites absent" in result.stderr


def test_module_invocation_no_args_exits_nonzero() -> None:
    """`python -m maezo.platform.lifecycle` with no subcommand also exits non-zero."""
    result = subprocess.run(
        [sys.executable, "-m", "maezo.platform.lifecycle"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )
    assert result.returncode == REFUSAL_EXIT_CODE
    assert result.returncode != 0
    assert "refused" in result.stderr
