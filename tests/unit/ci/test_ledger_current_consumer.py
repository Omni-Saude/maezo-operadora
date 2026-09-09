"""Actual tiny current executions with real G2 and source-derived relation mapping.

No operational history adapter or real ledger/service execution is represented.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_evidence_ledger_current as consumer
from scripts.ci import check_evidence_ledger_hashes as legacy
from scripts.ci import ledger_current_execution as current
from scripts.ci import ledger_current_relations as relations
from scripts.ci import pytest_metadata_admission as metadata

ROOT = Path(__file__).resolve().parents[3]
TEST = "tests/unit/test_case.py"


def commit(root: Path) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "tiny committed source"], cwd=root, check=True, capture_output=True
    )
    return current.git(root, "rev-parse", "HEAD").decode().strip()


def row(task: str, recipe: str, evidence: str = "evidence") -> str:
    return f"| {task} | 2026-09-09 | author | reviewer | source | {evidence} | {recipe} ({TEST}) | test |"


def recipe(name: str = "test_case", result: str = "PASSED") -> str:
    return legacy.compute_recipe_hash([f"{TEST}::{name} {result}"])


@pytest.fixture
def tiny(tmp_path: Path) -> Any:
    root = tmp_path / "source"
    (root / TEST).parent.mkdir(parents=True)
    (root / "docs").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for key, value in [("user.name", "Tiny current consumer"), ("user.email", "current@example.invalid")]:
        subprocess.run(["git", "config", key, value], cwd=root, check=True)
    for name in ("uv.lock", "pyproject.toml", ".python-version"):
        (root / name).write_bytes((ROOT / name).read_bytes())
    (root / "docs/evidence-ledger.md").write_text("# Tiny source\n")
    (root / TEST).write_text("def test_case(): assert True\n")
    base = commit(root)
    old = row("OLD", recipe())
    (root / "docs/evidence-ledger.md").write_text("# Tiny source\n" + old + "\n")
    old_commit = commit(root)
    return root, base, old, old_commit


def v1(tiny: Any, *, failed_old: bool = False) -> tuple[Path, str, str, str]:
    root, base, old, old_commit = tiny
    if failed_old:
        old = row("OLD", recipe(result="FAILED"))
        (root / TEST).write_text("def test_case(): assert False\n")
        (root / "docs/evidence-ledger.md").write_text("# Tiny source\n" + old + "\n")
        old_commit = commit(root)
    marker = (
        f"[ledger-supersedes:v1;target=OLD;source_commit={old_commit};"
        f"source_row_sha256={current.digest(old.encode())};"
        f"source_test_sha256={current.digest((root / TEST).read_bytes())};"
        f"source_lock_sha256={current.digest((root / 'uv.lock').read_bytes())}]"
    )
    new = row("NEW", recipe("test_new"), marker)
    (root / TEST).write_text("def test_new(): assert True\n")
    (root / "docs/evidence-ledger.md").write_text("# Tiny source\n" + old + "\n" + new + "\n")
    commit(root)
    return root, base, old, new


def test_actual_v1_terminal_expectation_and_unresolved_history(tiny: Any, tmp_path: Path) -> None:
    root, base, old, new = v1(tiny)
    result = consumer.run_integrated(root, base, tmp_path / "proof")
    assert result["status"] == "UNRESOLVED"
    assert result["current_status"] == "ACCEPTED"
    assert result["execution_count"] == 2
    assert len(result["required_relation_identities"]) == 1
    assert len({item["current"]["run_id"] for item in result["rows"]}) == 2
    assert [item["original"]["raw_line"] for item in result["rows"]] == [old, new]
    for item in result["rows"]:
        assert item["current_expectation"]["authoritative_row"]["raw_line"] == new
        assert item["history"] == "REQUIRED_NOT_COMPOSED"
        binding = json.loads((Path(item["current"]["packet"]) / "binding.json").read_text())
        assert binding["original"]["raw_line"] == item["original"]["raw_line"]
        assert binding["current_expectation"] == item["current_expectation"]
        assert binding["runtime_preparation"]["returncode"] == 0
        assert any(path.endswith("/pytest_metadata_admission.py") for path in binding["tool_files"])
        assert (
            binding["original"]["declared_hash"]
            == legacy.parse_row_line(item["original"]["raw_line"]).declared_hash
        )


def test_actual_identical_physical_rows_are_separate(tiny: Any, tmp_path: Path) -> None:
    root, base, old, _ = tiny
    (root / "docs/evidence-ledger.md").write_text("# Tiny source\n" + old + "\n" + old + "\n")
    commit(root)
    result = consumer.run_integrated(root, base, tmp_path / "proof")
    assert result["status"] == "ACCEPTED"
    assert result["selected_count"] == result["execution_count"] == 2
    assert result["global_acceptance"] is False
    assert len(set(result["executed_occurrence_identities"])) == 2
    assert len({item["current"]["run_id"] for item in result["rows"]}) == 2


def test_actual_failed_old_recipe_never_becomes_passing_history(tiny: Any, tmp_path: Path) -> None:
    root, base, old, _ = v1(tiny, failed_old=True)
    result = consumer.run_integrated(root, base, tmp_path / "proof")
    assert result["current_status"] == "ACCEPTED"
    assert result["status"] == "UNRESOLVED"
    assert result["rows"][0]["original"]["declared_hash"] == recipe(result="FAILED").removeprefix("sha256:")
    assert all(edge["status"] == "UNRESOLVED" for edge in result["relations"])


@pytest.mark.parametrize("fault", ["helper", "classifier", "policy"])
def test_actual_loaded_policy_swap_refuses_preimport(
    tiny: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root, base, old, _ = tiny
    if fault == "helper":
        monkeypatch.setattr(metadata.Classifier, "module_metadata", lambda *a, **k: None)
    elif fault == "classifier":
        monkeypatch.setattr(
            legacy,
            "classify_test_admission",
            lambda *a, **k: metadata.TestAdmission(
                "OFFLINE", "forged", current.digest((root / TEST).read_bytes()), ()
            ),
        )
    else:
        monkeypatch.setattr(metadata, "_PLAIN_DECORATORS", {"anything"})
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    verdict = runner.run(current.Occurrence(2, old))
    assert verdict.status == "REFUSED"
    assert "G2_LOADED_POLICY_DRIFT" in verdict.reasons
    assert not (Path(verdict.packet) / "request.json").exists()


def test_actual_expectation_handle_swap_replay_and_source_drift(tiny: Any, tmp_path: Path) -> None:
    root, base, old, new = v1(tiny)
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    first = runner.run(current.Occurrence(2, old))
    assert first.status == "ACCEPTED", first
    assert not runner.consume(first, current.Occurrence(3, new))
    assert not runner.consume(first, current.Occurrence(2, old))
    second = runner.run(current.Occurrence(2, old))
    assert second.status == "ACCEPTED"
    assert second.run_id != first.run_id
    assert not runner.consume(replace(second), current.Occurrence(2, old))
    third = runner.run(current.Occurrence(2, old))
    (root / "uv.lock").write_text("# changed lock\n")
    assert not runner.consume(third, current.Occurrence(2, old))


@pytest.mark.parametrize("dependency", [TEST, "pyproject.toml", "uv.lock"])
def test_actual_dependency_only_schedules_both_endpoints(tiny: Any, tmp_path: Path, dependency: str) -> None:
    root, _, old, new = v1(tiny)
    base = current.git(root, "rev-parse", "HEAD").decode().strip()
    with (root / dependency).open("a") as stream:
        stream.write("\n# source-only invalidation\n")
    commit(root)
    result = consumer.run_integrated(root, base, tmp_path / "proof")
    assert result["selected_count"] == 0
    assert result["execution_count"] == 2
    assert result["current_status"] == "ACCEPTED"
    assert result["status"] == "UNRESOLVED"
    assert len(result["required_relation_identities"]) == 1


def test_actual_successor_only_closes_target(tiny: Any, tmp_path: Path) -> None:
    root, _, _, old_commit = tiny
    root, _, _, _ = v1(tiny)
    result = consumer.run_integrated(root, old_commit, tmp_path / "proof")
    assert result["selected_count"] == 1
    assert result["execution_count"] == 2
    assert result["status"] == "UNRESOLVED"


def test_actual_failed_packet_then_fresh_recovery(tiny: Any, tmp_path: Path) -> None:
    root, base, old, _ = tiny
    (root / TEST).write_text("def test_case(): assert False\n")
    commit(root)
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    failed = runner.run(current.Occurrence(2, old))
    assert failed.status == "FAILED"
    saved = (Path(failed.packet) / "receipt.json").read_bytes()
    (root / TEST).write_text("def test_case(): assert True\n")
    commit(root)
    recovered_runner = current.CurrentRunner(root, tmp_path / "proof-recovery", base=base)
    passed = recovered_runner.run(current.Occurrence(2, old))
    assert passed.status == "ACCEPTED", passed
    assert recovered_runner.consume(passed, current.Occurrence(2, old))
    assert not recovered_runner.consume(passed, current.Occurrence(2, old))
    assert (Path(failed.packet) / "receipt.json").read_bytes() == saved


def test_no_caller_expected_hash_or_dataclass_plan(tiny: Any, tmp_path: Path) -> None:
    root, base, old, _ = tiny
    with pytest.raises(TypeError):
        current.CurrentRunner(root, tmp_path / "proof", base=base, expected_hash="f" * 64)
    with pytest.raises(TypeError):
        current.CurrentRunner(
            root, tmp_path / "proof", base=base, plan=relations.plan_current_relations(root, base)
        )


@pytest.mark.parametrize("fault", ["unknown", "live", "asserted_live"])
def test_actual_metadata_refusal_has_zero_executions(tiny: Any, tmp_path: Path, fault: str) -> None:
    root, base, _, _ = tiny
    source = (
        "import pytest\npytestmark = pytest.mark.integration\n"
        'def test_case(): raise AssertionError("must not run")\n'
    )
    if fault == "unknown":
        source = (
            'import pytest\npytestmark = getattr(pytest.mark, "integration")\n'
            'def test_case(): raise AssertionError("must not run")\n'
        )
    (root / TEST).write_text(source)
    commit(root)
    result = consumer.run_integrated(root, base, tmp_path / "proof", allow_live=fault == "asserted_live")
    assert result["status"] == "UNRESOLVED"
    assert result["attempt_count"] == 1
    assert result["execution_count"] == 0
    verdict = result["rows"][0]["current"]
    assert verdict["status"] == "REFUSED"
    assert not (Path(verdict["packet"]) / "request.json").exists()


@pytest.mark.parametrize("fault", ["helper", "runtime", "config", "expectation"])
def test_actual_consumption_checks_fresh_policy_runtime_and_binding(
    tiny: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root, base, old, _ = tiny
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    occurrence = current.Occurrence(2, old)
    verdict = runner.run(occurrence)
    assert verdict.status == "ACCEPTED", verdict
    packet = Path(verdict.packet)
    if fault == "helper":
        monkeypatch.setattr(metadata, "_PLAIN_DECORATORS", {"forged"})
    elif fault == "runtime":
        binding = json.loads((packet / "binding.json").read_text())
        runtime = Path(binding["runtime_identity"]["prefix"])
        with (runtime / "pyvenv.cfg").open("a") as stream:
            stream.write("# modified runtime\n")
    elif fault == "config":
        with (root / "pyproject.toml").open("a") as stream:
            stream.write("# modified config\n")
    else:
        binding = json.loads((packet / "binding.json").read_text())
        binding["current_expectation"]["recipe_sha256"] = "f" * 64
        (packet / "binding.json").write_bytes(current.canonical(binding))
    assert not runner.consume(verdict, occurrence)
    assert not runner.consume(verdict, occurrence)


def test_actual_target_only_closes_successor(tiny: Any, tmp_path: Path) -> None:
    root, _, old, new = v1(tiny)
    ledger = root / "docs/evidence-ledger.md"
    ledger.write_text("# Tiny source\n" + new + "\n")
    base = commit(root)
    ledger.write_text("# Tiny source\n" + new + "\n" + old + "\n")
    commit(root)
    result = consumer.run_integrated(root, base, tmp_path / "proof")
    assert result["selected_count"] == 1
    assert result["selected_occurrence_identities"] == [f"3:{current.digest(old.encode())}"]
    assert result["execution_count"] == 2
    assert result["status"] == "UNRESOLVED"


@pytest.mark.parametrize("fault", ["malformed", "ambiguous", "orphan", "unsupported", "cross_path"])
def test_actual_bad_relations_never_launch(tiny: Any, tmp_path: Path, fault: str) -> None:
    root, base, old, new = v1(tiny)
    if fault == "malformed":
        new = new.replace("source_commit=", "source_commit=INVALID")
    elif fault == "ambiguous":
        old += "\n" + old
    elif fault == "orphan":
        new = new.replace("target=OLD;", "target=MISSING;")
    elif fault == "unsupported":
        new = new.replace("ledger-supersedes:v1;", "ledger-supersedes:v99;")
    else:
        alternate = "tests/unit/test_alternate.py"
        (root / alternate).write_text("def test_new(): assert True\n")
        new = new.replace(TEST, alternate)
    (root / "docs/evidence-ledger.md").write_text("# Tiny source\n" + old + "\n" + new + "\n")
    commit(root)
    with pytest.raises((ValueError, legacy.SupersessionError)):
        consumer.run_integrated(root, base, tmp_path / "proof")
    assert not (tmp_path / "proof").exists()


def test_actual_stale_inprocess_candidate_cannot_reuse_runtime(tiny: Any, tmp_path: Path) -> None:
    root, base, old, _ = tiny
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    occurrence = current.Occurrence(2, old)
    passed = runner.run(occurrence)
    assert passed.status == "ACCEPTED", passed
    assert runner.consume(passed, occurrence)
    with (root / TEST).open("a") as stream:
        stream.write("# new candidate\n")
    commit(root)
    stale = runner.run(occurrence)
    assert stale.status == "REFUSED"
    assert "CURRENT_EXPECTATION_STALE" in stale.reasons
    assert not (Path(stale.packet) / "request.json").exists()
