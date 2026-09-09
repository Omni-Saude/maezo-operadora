"""Actual tiny current executions with real G2 and source-derived relation mapping.

No operational history adapter or real ledger/service execution is represented.
"""

from __future__ import annotations

import json
import shlex
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
        assert item["history"] == "UNRESOLVED"
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


@pytest.mark.parametrize("write_bytecode", [False, True])
def test_actual_named_python_uses_prepared_runtime(tiny: Any, tmp_path: Path, write_bytecode: bool) -> None:
    root, base, old, _ = tiny
    command = ["python", "-I", *([] if write_bytecode else ["-B"]), "-c", "import sys; print(sys.prefix)"]
    (root / TEST).write_text(
        "import sys, subprocess\ndef test_case():\n"
        "    prefix = subprocess.check_output(\n"
        f"        {command!r}, text=True).strip()\n"
        "    assert prefix == sys.prefix\n"
    )
    commit(root)
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    occurrence = current.Occurrence(2, old)
    verdict = runner.run(occurrence)
    binding = json.loads((Path(verdict.packet) / "binding.json").read_text())
    assert binding["environment"]["PATH"].split(":")[0] == str(
        Path(binding["runtime_identity"]["prefix"]) / "bin"
    )
    if write_bytecode:
        assert verdict.status == "FAILED", verdict
        assert "RUNTIME_TOOL_DRIFT" in verdict.reasons
        assert not runner.consume(verdict, occurrence)
    else:
        assert verdict.status == "ACCEPTED", verdict
        assert runner.consume(verdict, occurrence)


def test_actual_failed_old_recipe_never_becomes_passing_history(tiny: Any, tmp_path: Path) -> None:
    root, base, old, _ = v1(tiny, failed_old=True)
    result = consumer.run_integrated(root, base, tmp_path / "proof")
    assert result["current_status"] == "ACCEPTED"
    assert result["status"] == "UNRESOLVED"
    assert result["rows"][0]["original"]["declared_hash"] == recipe(result="FAILED").removeprefix("sha256:")
    assert all(edge["status"] == "FAILED" for edge in result["relations"])


@pytest.mark.parametrize("when", ["before_launch", "after_run"])
def test_actual_replaced_launcher_cannot_replay_runtime_probe(
    tiny: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    root, base, old, _ = tiny
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    occurrence = current.Occurrence(2, old)
    marker = tmp_path / "replay-executed"

    def replace_launcher(python: Path) -> None:
        snapshot = current._runtime_probe(python, root, current.minimal_environment(tmp_path))
        replay = tmp_path / "runtime-probe-replay.json"
        replay.write_bytes(current.canonical(snapshot))
        python.unlink()
        python.write_text(
            "#!/bin/sh\nprintf launched > " + shlex.quote(str(marker)) + "\n"
            "exec /bin/cat " + shlex.quote(str(replay)) + "\n"
        )
        python.chmod(0o700)

    if when == "before_launch":
        prepare = runner._prepare_runtime

        def replacement(packet: Path) -> Any:
            result = prepare(packet)
            replace_launcher(Path(result[0]["python"]))
            return result

        monkeypatch.setattr(runner, "_prepare_runtime", replacement)
    verdict = runner.run(occurrence)
    if when == "after_run":
        assert verdict.status == "ACCEPTED", verdict
        binding = json.loads((Path(verdict.packet) / "binding.json").read_text())
        replace_launcher(Path(binding["command"][0]))
    else:
        assert verdict.status == "REFUSED", verdict
        assert "PRELAUNCH_RUNTIME_LAUNCHER_DRIFT" in verdict.reasons
        assert not (Path(verdict.packet) / "request.json").exists()
    assert not runner.consume(verdict, occurrence)
    assert not marker.exists(), "the replaced launcher must never execute"


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


@pytest.mark.parametrize("when", ["before_launch", "during_body", "after_run"])
def test_actual_ignored_configuration_appearance_breaks_admission_absence_binding(
    tiny: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    root, _, old, _ = tiny
    (root / ".gitignore").write_text("conftest.py\n")
    if when == "during_body":
        (root / TEST).write_text(
            "from pathlib import Path\ndef test_case():\n"
            "    Path('conftest.py').write_text('# Newly appearing ignored configuration.\\n')\n"
        )
    base = commit(root)
    with (root / "docs/evidence-ledger.md").open("a") as stream:
        stream.write(old + "\n")
    commit(root)
    before = current.source_inventory(root)
    occurrence = current.Occurrence(3, old)
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    if when == "before_launch":
        prepare = runner._prepare_runtime

        def appearance(packet: Path) -> Any:
            result = prepare(packet)
            (root / "conftest.py").write_text("# Newly appearing ignored configuration.\n")
            return result

        # Inject a real filesystem event after real uv preparation. G2 itself is
        # the actual classifier, with no admission or policy-provenance seam.
        monkeypatch.setattr(runner, "_prepare_runtime", appearance)
    verdict = runner.run(occurrence)
    if when == "after_run":
        assert verdict.status == "ACCEPTED", verdict
        (root / "conftest.py").write_text("# Newly appearing ignored configuration.\n")
    elif when == "before_launch":
        assert verdict.status == "REFUSED", verdict
        assert "PRELAUNCH_ADMISSION_DRIFT" in verdict.reasons
        assert not (Path(verdict.packet) / "request.json").exists()
    else:
        assert verdict.status == "FAILED", verdict
        assert "G2_ADMISSION_DRIFT" in verdict.reasons
    # This is the exact hole: Git's inventory alone sees no mutation.
    assert current.source_inventory(root) == before
    assert not runner.consume(verdict, occurrence)


def test_actual_unsupported_plan_path_retains_prelaunch_refusal_packet(tiny: Any, tmp_path: Path) -> None:
    root, base, old, _ = tiny
    (root / ".gitignore").write_text("conftest.py\n")
    commit(root)
    runner = current.CurrentRunner(root, tmp_path / "proof", base=base)
    verdict = runner.run(current.Occurrence(2, old))
    assert verdict.status == "REFUSED"
    assert "IC_PATH" in verdict.reasons
    assert (Path(verdict.packet) / "receipt.json").is_file()
    assert not (Path(verdict.packet) / "request.json").exists()


@pytest.mark.parametrize("historical_result", ["PASSED", "FAILED"])
def test_real_v1_history_equality_has_independent_current_credit(
    history_tiny: Any, tmp_path: Path, historical_result: str
) -> None:
    root, base, old, old_commit = history_tiny
    root, base, old, _ = v1((root, base, old, old_commit), failed_old=historical_result == "FAILED")
    result = consumer.run_integrated(root, base, tmp_path / "history-proof")
    assert result["current_status"] == "ACCEPTED"
    assert result["status"] == "ACCEPTED"
    edge = result["relations"][0]
    assert edge["status"] == "VALID_HISTORICAL_EQUALITY"
    assert edge["historical_test_status"] == historical_result
    assert edge["historical_claim_verified"] is True
    assert edge["consumed"] is True
    assert result["history_execution_count"] == 2
    assert result["execution_count"] == 2
    assert len({item["current"]["run_id"] for item in result["rows"]}) == 2
    assert result["global_acceptance"] is False


@pytest.fixture
def history_tiny(tiny: Any) -> Any:
    """Small real locked project; the original current/G1 fixtures stay unchanged."""
    root, base, old, _ = tiny
    (root / "pyproject.toml").write_text(
        '[project]\nname="tiny-history"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n'
        '[project.optional-dependencies]\ndev=["pytest==9.1.1","pytest-asyncio==1.4.0"]\n'
        "[tool.uv]\npackage=false\n"
    )
    subprocess.run(
        [str(current._UV), "lock", "--offline", "--no-config"],
        cwd=root,
        env=current.minimal_environment(Path.home()),
        check=True,
        capture_output=True,
    )
    (root / "src/maezo").mkdir(parents=True)
    (root / "src/maezo/__init__.py").write_text("")
    return root, base, old, commit(root)


def test_real_v1_mismatch_blocks_despite_current_pass(history_tiny: Any, tmp_path: Path) -> None:
    root, base, old, _ = history_tiny
    (root / TEST).write_text("def test_case(): assert False\n")
    source = commit(root)
    root, base, _, _ = v1((root, base, old, source))
    result = consumer.run_integrated(root, base, tmp_path / "mismatch")
    assert result["current_status"] == "ACCEPTED"
    assert result["status"] == "UNRESOLVED"
    edge = result["relations"][0]
    assert edge["status"] == "FAILED"
    assert edge["historical_test_status"] == "FAILED"
    assert edge["historical_claim_verified"] is False
    assert edge["consumed"] is False


@pytest.mark.parametrize("fault", ["copy", "swap", "packet", "replay", "source", "policy"])
def test_real_history_handles_refuse_forgery_drift_and_recover(
    history_tiny: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root, base, _, _ = v1(history_tiny)
    runner = current.HistoryRunner(root, tmp_path / "history", base=base)
    identity = relations.plan_current_relations(root, base).required_relations[0]
    verdict = runner.run(identity)
    assert verdict.status == "VALID_HISTORICAL_EQUALITY", verdict
    receipt = Path(verdict.packet) / "receipt.json"
    original = receipt.read_bytes()
    if fault == "copy":
        assert not runner.consume(replace(verdict), identity)
    elif fault == "swap":
        assert not runner.consume(verdict, "different-physical-edge")
    elif fault == "packet":
        (Path(verdict.packet) / "historical.stdout").write_text("forged")
        assert not runner.consume(verdict, identity)
    elif fault == "replay":
        assert runner.consume(verdict, identity)
    elif fault == "source":
        (root / TEST).write_text("def test_new(): assert False\n")
        assert not runner.consume(verdict, identity)
        (root / TEST).write_text("def test_new(): assert True\n")
    else:
        with monkeypatch.context() as patch:
            patch.setattr(legacy, "verify_historical_row", lambda *a, **k: None)
            assert not runner.consume(verdict, identity)
    assert not runner.consume(verdict, identity)
    assert receipt.read_bytes() == original
    if fault == "packet":
        recovery = current.HistoryRunner(root, tmp_path / "fresh-recovery", base=base)
        fresh = recovery.run(identity)
        assert fresh.status == "VALID_HISTORICAL_EQUALITY", fresh
        assert recovery.consume(fresh, identity)
        assert fresh.run_id != verdict.run_id
        assert (Path(verdict.packet) / "historical.stdout").read_text() == "forged"


@pytest.mark.parametrize("fault", ["classifier", "verifier", "capture", "catalog", "capsule", "adapter"])
def test_history_loaded_authority_refuses_before_execution(
    history_tiny: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from scripts.ci import ledger_history_proofs as proof

    root, base, _, _ = v1(history_tiny)
    runner = current.HistoryRunner(root, tmp_path / "history", base=base)
    identity = relations.plan_current_relations(root, base).required_relations[0]
    calls = []

    def forged(*args: Any, **kwargs: Any) -> Any:
        calls.append("called")
        return None

    if fault == "classifier":
        monkeypatch.setattr(legacy, "classify_test_admission", forged)
    elif fault == "verifier":
        monkeypatch.setattr(legacy, "verify_historical_row", forged)
    elif fault == "capture":
        monkeypatch.setattr(legacy, "_capture_historical_recipe", forged)
    elif fault == "catalog":
        monkeypatch.setattr(proof, "VALID_CATALOG", ())
    elif fault == "capsule":
        monkeypatch.setattr(proof, "producer_capsule", forged)
    else:
        monkeypatch.setattr(current.HistoryRunner, "_v1", forged)
    verdict = runner.run(identity)
    assert verdict.status == "REFUSED"
    assert not runner.consume(verdict, identity)
    assert calls == []
    assert not (Path(verdict.packet) / "runtime").exists()
