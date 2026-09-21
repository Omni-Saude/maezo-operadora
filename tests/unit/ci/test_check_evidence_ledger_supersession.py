"""Adversarial fences for append-only evidence-ledger supersession provenance."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.ci import generate_release_floor
from scripts.ci.check_evidence_ledger_hashes import (
    CONVENTION_START_DATE,
    SupersessionError,
    assert_acyclic_supersession_links,
    build_supersession_plan,
    capture_pytest_recipe,
    main,
    recipe_environment,
    run_recipe,
)

from tests.support.measurement_python import measurement_python

HEADER = (
    "# Evidence Ledger\n\n"
    "| Task ID | Date | Author | Verifier | Commit | Evidence | Test hash | Status |\n"
    "|---|---|---|---|---|---|---|---|\n"
)
LOCK = b'version = 1\nrevision = 1\nrequires-python = ">=3.12"\n'


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _row(task_id: str, test_path: str, digest: str, *, evidence: str = "evidence") -> str:
    return (
        f"| {task_id} | {CONVENTION_START_DATE} | author | verifier | commit | {evidence} | "
        f"sha256:{digest} ({test_path}) | verified |"
    )


@dataclass(frozen=True)
class HistoryRepo:
    root: Path
    source_commit: str
    target_row: str
    successor_row: str
    target_test_sha256: str


def _history_repo(
    tmp_path: Path,
    *,
    source_hash_override: str | None = None,
    successor_hash_override: str | None = None,
    source_test: str | None = None,
    target_path: str = "tests/test_claim.py",
) -> HistoryRepo:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ledger@example.com")
    _git(repo, "config", "user.name", "Ledger Test")
    (repo / ".gitignore").write_text("__pycache__/\n*.py[co]\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "src" / "maezo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "maezo" / "__init__.py").write_text('ORIGIN = "historical"\n', encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n', encoding="utf-8"
    )
    (repo / "uv.lock").write_bytes(LOCK)
    historical_test = source_test or (
        "import maezo\n\ndef test_historical_origin():\n    assert maezo.ORIGIN == 'historical'\n"
    )
    test_file = repo / target_path
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(historical_test, encoding="utf-8")
    historical_outcome = run_recipe(repo, target_path, sys.executable)
    historical_hash = source_hash_override or (
        historical_outcome.computed_hash or "sha256:" + "0" * 64
    ).removeprefix("sha256:")
    target_row = _row("OLD-CLAIM", target_path, historical_hash)
    ledger = repo / "docs" / "evidence-ledger.md"
    ledger.write_text(HEADER + target_row + "\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "historical claim")
    source_commit = _git(repo, "rev-parse", "HEAD")
    source_test_bytes = test_file.read_bytes()

    (repo / "src" / "maezo" / "__init__.py").write_text('ORIGIN = "current"\n', encoding="utf-8")
    test_file.write_text(
        "import maezo\n\n"
        "def test_current_origin():\n"
        "    assert maezo.ORIGIN == 'current'\n\n"
        "def test_current_second_case():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    current_outcome = run_recipe(repo, target_path, sys.executable)
    current_hash = successor_hash_override or (
        current_outcome.computed_hash or "sha256:" + "0" * 64
    ).removeprefix("sha256:")
    marker = (
        "[ledger-supersedes:v1;target=OLD-CLAIM;"
        f"source_commit={source_commit};"
        f"source_row_sha256={hashlib.sha256(target_row.encode()).hexdigest()};"
        f"source_test_sha256={hashlib.sha256(source_test_bytes).hexdigest()};"
        f"source_lock_sha256={hashlib.sha256(LOCK).hexdigest()}]"
    )
    successor_row = _row("CURRENT-CLAIM", target_path, current_hash, evidence=marker)
    ledger.write_text(HEADER + target_row + "\n" + successor_row + "\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "append current claim")
    return HistoryRepo(
        repo,
        source_commit,
        target_row,
        successor_row,
        hashlib.sha256(source_test_bytes).hexdigest(),
    )


def _replace_and_commit(repo: Path, old: str, new: str) -> None:
    ledger = repo / "docs" / "evidence-ledger.md"
    ledger.write_text(ledger.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    _git(repo, "add", "docs/evidence-ledger.md")
    _git(repo, "commit", "-q", "-m", "tamper")


def test_successor_proves_historical_archive_and_current_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = _history_repo(tmp_path)
    exit_code = main(["--base", history.source_commit, "--python", sys.executable], repo_root=history.root)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exit_code == 0
    assert "historical claim verified at exact source_commit" in output
    assert "archived Python source files attested" in output
    assert "CURRENT-CLAIM: verified" in output
    assert "2/2 declared-row proofs verified" in output


def test_unrelated_duplicate_task_id_does_not_make_exact_digest_target_ambiguous(
    tmp_path: Path,
) -> None:
    history = _history_repo(tmp_path)
    ledger = history.root / "docs" / "evidence-ledger.md"
    other = _row("OLD-CLAIM", "tests/test_claim.py", "f" * 64, evidence="unrelated")
    ledger.write_text(ledger.read_text(encoding="utf-8") + other + "\n", encoding="utf-8")
    _git(history.root, "add", "docs/evidence-ledger.md")
    _git(history.root, "commit", "-q", "-m", "unrelated duplicate id")
    plan = build_supersession_plan(history.root, ledger.read_text(encoding="utf-8"))
    assert len(plan.by_target_row_sha256) == 1


def test_forged_current_copy_of_old_row_is_rejected(tmp_path: Path) -> None:
    history = _history_repo(tmp_path)
    _replace_and_commit(
        history.root,
        history.target_row,
        history.target_row.replace("| evidence |", "| forged evidence |"),
    )
    with pytest.raises(SupersessionError, match="resolves to 0 rows"):
        build_supersession_plan(
            history.root,
            (history.root / "docs/evidence-ledger.md").read_text(encoding="utf-8"),
        )


def test_changed_historical_test_digest_is_rejected(tmp_path: Path) -> None:
    history = _history_repo(tmp_path)
    _replace_and_commit(history.root, history.target_test_sha256, "0" * 64)
    with pytest.raises(SupersessionError, match="historical test blob digest mismatch"):
        build_supersession_plan(
            history.root,
            (history.root / "docs/evidence-ledger.md").read_text(encoding="utf-8"),
        )


def test_changed_historical_lock_digest_is_rejected(tmp_path: Path) -> None:
    history = _history_repo(tmp_path)
    _replace_and_commit(history.root, hashlib.sha256(LOCK).hexdigest(), "0" * 64)
    with pytest.raises(SupersessionError, match="historical uv.lock digest mismatch"):
        build_supersession_plan(
            history.root,
            (history.root / "docs/evidence-ledger.md").read_text(encoding="utf-8"),
        )


def test_non_ancestor_source_pin_is_rejected(tmp_path: Path) -> None:
    history = _history_repo(tmp_path)
    sibling = _git(history.root, "commit-tree", "HEAD^{tree}", "-m", "unrelated root")
    _replace_and_commit(history.root, history.source_commit, sibling)
    with pytest.raises(SupersessionError, match="is not an ancestor"):
        build_supersession_plan(
            history.root,
            (history.root / "docs/evidence-ledger.md").read_text(encoding="utf-8"),
        )


def test_duplicate_successors_for_one_exact_target_are_rejected(tmp_path: Path) -> None:
    history = _history_repo(tmp_path)
    ledger = history.root / "docs" / "evidence-ledger.md"
    duplicate = history.successor_row.replace("CURRENT-CLAIM", "OTHER-CURRENT-CLAIM", 1)
    ledger.write_text(ledger.read_text(encoding="utf-8") + duplicate + "\n", encoding="utf-8")
    _git(history.root, "add", "docs/evidence-ledger.md")
    _git(history.root, "commit", "-q", "-m", "duplicate edge")
    with pytest.raises(SupersessionError, match="already has successor"):
        build_supersession_plan(history.root, ledger.read_text(encoding="utf-8"))


def test_cycle_detector_is_non_vacuous() -> None:
    assert_acyclic_supersession_links({"new": "old", "old": "root"})
    with pytest.raises(SupersessionError, match="cycle"):
        assert_acyclic_supersession_links({"a": "b", "b": "a"})


@pytest.mark.parametrize(
    "mutation",
    [
        ";unknown=value]",
        ";source_lock_sha256=]",
        "[ledger-supersedes:v2;",
    ],
)
def test_unknown_or_malformed_metadata_is_rejected(tmp_path: Path, mutation: str) -> None:
    history = _history_repo(tmp_path)
    if mutation.startswith("["):
        replacement = history.successor_row.replace("[ledger-supersedes:v1;", mutation)
    else:
        replacement = history.successor_row.replace("] | sha256:", mutation + " | sha256:")
    _replace_and_commit(history.root, history.successor_row, replacement)
    with pytest.raises(SupersessionError, match="malformed"):
        build_supersession_plan(
            history.root,
            (history.root / "docs/evidence-ledger.md").read_text(encoding="utf-8"),
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing_declaration", "requires a declared successor"),
        ("predated", "requires a convention-qualified successor Date"),
        ("missing_date", "requires a convention-qualified successor Date"),
        ("malformed_date", "requires a convention-qualified successor Date"),
    ],
)
def test_valid_v1_marker_cannot_fall_back_to_legacy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    expected: str,
) -> None:
    history = _history_repo(tmp_path)
    if mutation == "missing_declaration":
        replacement = history.successor_row.replace("sha256:", "sha257:")
    elif mutation == "predated":
        replacement = history.successor_row.replace(CONVENTION_START_DATE, "2000-01-01")
    elif mutation == "missing_date":
        replacement = history.successor_row.replace(CONVENTION_START_DATE, "")
    else:
        replacement = history.successor_row.replace(CONVENTION_START_DATE, "not-a-date")
    _replace_and_commit(history.root, history.successor_row, replacement)

    exit_code = main(["--base", history.source_commit, "--python", sys.executable], repo_root=history.root)
    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert exit_code == 1
    assert expected in output
    assert "SKIP (legacy)" not in output


def test_historical_source_path_escape_is_rejected(tmp_path: Path) -> None:
    history = _history_repo(tmp_path, target_path="tests/../test_escape.py")
    with pytest.raises(SupersessionError, match="historical target path.*unsafe"):
        build_supersession_plan(
            history.root,
            (history.root / "docs/evidence-ledger.md").read_text(encoding="utf-8"),
        )


@pytest.mark.parametrize("external_name", ["outside.py", "outside.txt", "outside.pyw", "outside"])
def test_historical_executed_source_escape_is_rejected_regardless_of_suffix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], external_name: str
) -> None:
    external = tmp_path / external_name
    external.write_text("VALUE = 1\n", encoding="utf-8")
    source = (
        "from importlib.machinery import SourceFileLoader\n"
        "import importlib.util\nimport sys\n"
        f"EXTERNAL = {str(external)!r}\n\n"
        "def test_escape():\n"
        "    loader = SourceFileLoader('historical_helper_escape', EXTERNAL)\n"
        "    spec = importlib.util.spec_from_loader(loader.name, loader)\n"
        "    assert spec is not None and spec.loader is not None\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    sys.modules['historical_helper_escape'] = module\n"
        "    loader.exec_module(module)\n"
        "    assert module.VALUE == 1\n"
    )
    history = _history_repo(tmp_path, source_test=source)
    exit_code = main(["--base", history.source_commit, "--python", sys.executable], repo_root=history.root)
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "historical source import escaped archive" in output


def test_unselected_historical_claim_allows_an_unrelated_committed_lock_update(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = _history_repo(tmp_path)
    base = _git(history.root, "rev-parse", "HEAD")
    lock = history.root / "uv.lock"
    lock.write_bytes(lock.read_bytes() + b"\n# unrelated dependency update\n")
    _git(history.root, "add", "uv.lock")
    _git(history.root, "commit", "-q", "-m", "unrelated dependency update")

    exit_code = main(["--base", base, "--python", sys.executable], repo_root=history.root)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "PASS: 0 rows verified, 0 legacy rows skipped" in output


def test_selected_historical_claim_still_requires_the_exact_current_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = _history_repo(tmp_path)
    lock = history.root / "uv.lock"
    lock.write_bytes(lock.read_bytes() + b"\n# incompatible dependency update\n")
    _git(history.root, "add", "uv.lock")
    _git(history.root, "commit", "-q", "-m", "incompatible dependency update")

    exit_code = main(["--base", history.source_commit, "--python", sys.executable], repo_root=history.root)
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "historical uv.lock differs from HEAD" in output
    assert "1/2 declared-row proofs verified" in output


def test_historical_live_test_is_refused_without_engine_mutex(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = _history_repo(tmp_path, target_path="tests/integration/test_claim.py")
    exit_code = main(["--base", history.source_commit, "--python", sys.executable], repo_root=history.root)
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "HARNESS-LEDGER-HASH-AMBIENT-STACK applies equally to archived tests" in output


def test_recipe_environment_drops_credentials_and_ambient_pytest_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-propagate")
    monkeypatch.setenv("DATABASE_URL", "must-not-propagate")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--lf")
    env = recipe_environment(python_path="/owned/archive/src")
    assert "ANTHROPIC_API_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "PYTEST_ADDOPTS" not in env
    assert env["PYTHONPATH"] == "/owned/archive/src"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_current_recipe_preserves_established_environment_and_plugin_autoload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def _run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="tests/test_claim.py::test_ok PASSED\n", stderr="")

    monkeypatch.setenv("MAEZO_TEST_DATABASE_URL", "postgresql://lane@127.0.0.1:5546/maezo")
    monkeypatch.setenv("PYTEST_PLUGINS", "canonical_evidence_plugin")
    monkeypatch.setattr(subprocess, "run", _run)
    capture = capture_pytest_recipe(tmp_path, "tests/test_claim.py", sys.executable)
    assert capture.ok
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert "env" not in kwargs
    assert observed["argv"] == [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_claim.py",
        "-v",
        "--tb=no",
        "-p",
        "no:cacheprovider",
    ]


def test_historical_live_environment_forwards_only_explicit_lane_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAEZO_TEST_DATABASE_URL", "postgresql://lane:lane@127.0.0.1:5546/maezo")
    monkeypatch.setenv("CIBSEVEN_BASE_URL", "http://127.0.0.1:18080/engine-rest")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-propagate")
    monkeypatch.setenv("DATABASE_URL", "must-not-propagate")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--lf")
    env = recipe_environment(live_coordinates=dict(os.environ))
    assert env["MAEZO_TEST_DATABASE_URL"].endswith("127.0.0.1:5546/maezo")
    assert env["CIBSEVEN_BASE_URL"].endswith("127.0.0.1:18080/engine-rest")
    assert "ANTHROPIC_API_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "PYTEST_ADDOPTS" not in env


def test_historical_live_environment_rejects_non_loopback_coordinates() -> None:
    with pytest.raises(ValueError, match="non-loopback.*MAEZO_TEST_DATABASE_URL"):
        recipe_environment(
            live_coordinates={"MAEZO_TEST_DATABASE_URL": "postgresql://user:secret@db.prod/maezo"}
        )


@pytest.mark.parametrize(
    "query",
    [
        "host=db.example.invalid",
        "hostaddr=203.0.113.10",
        "port=6432",
        "service=production",
        "servicefile=/tmp/pg_service.conf",
        "host=%2Fvar%2Frun%2Fpostgresql",
        "%68ost=db.example.invalid",
    ],
)
def test_historical_database_coordinate_rejects_libpq_destination_overrides(query: str) -> None:
    value = f"postgresql://127.0.0.1:5432/maezo?{query}"
    with pytest.raises(ValueError, match="non-loopback.*MAEZO_TEST_DATABASE_URL"):
        recipe_environment(live_coordinates={"MAEZO_TEST_DATABASE_URL": value})


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://127.0.0.1:5432/maezo",
        "postgres://user:password@localhost:5546/maezo?sslmode=disable",
        "postgresql://[::1]:5432/maezo",
    ],
)
def test_historical_database_coordinate_preserves_unambiguous_loopback_urls(value: str) -> None:
    env = recipe_environment(live_coordinates={"MAEZO_TEST_DATABASE_URL": value})
    assert env["MAEZO_TEST_DATABASE_URL"] == value


@pytest.mark.parametrize(
    "key,value",
    [
        ("CIBSEVEN_BASE_URL", "ftp://127.0.0.1:18080/engine-rest"),
        ("ENGINE_REST_URL", "http://127.0.0.1:70000/engine-rest"),
        ("MAEZO_TEST_DATABASE_URL", "mysql://127.0.0.1:3306/maezo"),
        ("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092/topic"),
    ],
)
def test_historical_live_coordinates_reject_invalid_coordinate_grammar(key: str, value: str) -> None:
    with pytest.raises(ValueError, match=f"non-loopback.*{key}"):
        recipe_environment(live_coordinates={key: value})


def test_wrong_historical_hash_fails_and_refuses_head_fallback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = _history_repo(tmp_path, source_hash_override="0" * 64)
    exit_code = main(["--base", history.source_commit, "--python", sys.executable], repo_root=history.root)
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "historical hash mismatch" in output
    assert "refusing any HEAD fallback" in output


def test_wrong_current_hash_fails_even_when_historical_claim_is_true(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    history = _history_repo(tmp_path, successor_hash_override="0" * 64)
    exit_code = main(["--base", history.source_commit, "--python", sys.executable], repo_root=history.root)
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "historical claim verified" in output
    assert "CURRENT-CLAIM: hash mismatch" in output


def test_release_floor_budget_covers_measured_suite_but_remains_bounded() -> None:
    assert generate_release_floor._UNIT_TESTS_TIMEOUT_SECONDS == 7200
    assert 4973.97 < generate_release_floor._UNIT_TESTS_TIMEOUT_SECONDS < 9000


def test_release_floor_timeout_remains_a_nonpassing_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["pytest", "tests/", "-q"], timeout=7200)

    monkeypatch.setattr(generate_release_floor, "_run_unit_measurement", _timeout)
    counts, returncode, raw = generate_release_floor.measure_unit_tests(tmp_path, sys.executable)
    passed, violations = generate_release_floor.evaluate_unit_tests(counts, returncode, raw)
    assert counts == {}
    assert returncode == -1
    assert passed is None
    assert "TIMEOUT after 7200s" in raw
    assert violations


def test_release_floor_timeout_reaps_the_pytest_descendant_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real pytest-shaped process starts a child that would survive killing only
    # its parent. Both share the new session owned by the measurement runner.
    (tmp_path / "pytest.py").write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "pathlib.Path('owned-pgid').write_text(str(os.getpgrp()))\n"
        "time.sleep(30)\n"
    )
    # The budget must exceed the payload tree's startup (parent exec+init, its
    # child spawn, the marker write), not just the sleep that forces the timeout.
    # Warm tree startup measures ~0.08s; 5s keeps ~60x headroom under CI load
    # while `time.sleep(30)` below still guarantees the timeout fires.
    monkeypatch.setattr(generate_release_floor, "_UNIT_TESTS_TIMEOUT_SECONDS", 5.0)
    counts, returncode, raw = generate_release_floor.measure_unit_tests(
        tmp_path, measurement_python(tmp_path)
    )
    assert (tmp_path / "owned-pgid").exists(), (counts, returncode, raw)
    pgid = int((tmp_path / "owned-pgid").read_text())
    assert not generate_release_floor.process_groups._group_exists(pgid)
    assert pgid not in generate_release_floor.process_groups._pending_groups
    assert counts == {} and returncode == -1 and "TIMEOUT" in raw
    passed, violations = generate_release_floor.evaluate_unit_tests(counts, returncode, raw)
    assert passed is None and violations


def test_release_floor_measurement_keeps_exact_selector_and_rejects_red_suite(
    tmp_path: Path,
) -> None:
    (tmp_path / "pytest.py").write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        "assert sys.stdin.read() == ''\n"
        "print('1 failed, 25 passed in 0.01s')\n"
        "raise SystemExit(1)\n"
    )
    counts, returncode, raw = generate_release_floor.measure_unit_tests(
        tmp_path, measurement_python(tmp_path)
    )
    assert (tmp_path / "argv.json").exists(), (counts, returncode, raw)
    assert json.loads((tmp_path / "argv.json").read_text()) == ["tests/", "-q"]
    assert counts == {"failed": 1, "passed": 25} and returncode == 1
    passed, violations = generate_release_floor.evaluate_unit_tests(counts, returncode, raw)
    assert passed is None and violations
