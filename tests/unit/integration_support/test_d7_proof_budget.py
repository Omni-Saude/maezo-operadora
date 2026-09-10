"""Offline custody/deadline controls; no daemon or private capture payload access."""

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from tests.unit.integration_support.test_d7_startup_observation import m

PACKET = Path("/Users/familia/code/maezo-completion-evidence/strategy-cycle1-20260910/d7-proof-budget-repair")
spec = importlib.util.spec_from_file_location("d7_calibration_controls", PACKET / "calibrate_proof_budget.py")
calibration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibration)
g = m.g


@pytest.fixture
def custody(tmp_path, monkeypatch):
    c, state = calibration.fixture(g, tmp_path.resolve(), counts=(3, 4, 2))
    monkeypatch.setattr(g, "LOCK_PATH", Path(c["lock"]))
    g._HASH_CACHE.clear()
    return c, state


def test_current_full_proof_and_runtime_membership_without_duplicate_hash(custody):
    c, _ = custody
    with patch.object(g, "_entry", wraps=g._entry) as entry:
        g.prove(c)
    # Source+runtime+tools once each, plus private invocation pin; no membership rehash.
    assert entry.call_count == 10
    assert g.runtime_membership(Path(c["checkout"]) / ".venv") == set(c["runtime"])


@pytest.mark.parametrize("kind", ["source", "runtime", "tools"])
@pytest.mark.parametrize("mutation", ["content", "same_size_mtime", "deleted", "inode", "symlink_target"])
def test_each_command_rechecks_all_pinned_inputs(custody, kind, mutation):
    c, _ = custody
    g.prove(c)
    file = Path(next(iter(c[kind])))
    info = file.stat()
    if mutation == "deleted":
        file.unlink()
    elif mutation == "symlink_target":
        target = Path(c["private"]) / "replacement"
        target.write_bytes(file.read_bytes())
        file.unlink()
        file.symlink_to(target)
    elif mutation == "inode":
        replacement = file.with_suffix(".replacement")
        replacement.write_bytes(b"X" * info.st_size)
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        replacement.replace(file)
    else:
        file.write_bytes(b"X" * info.st_size)
        if mutation == "same_size_mtime":
            os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns))
    with pytest.raises((g.Refused, OSError)):
        g.prove(c)


@pytest.mark.parametrize("mutation", ["extra", "directory_symlink", "broken_symlink", "fifo"])
def test_runtime_membership_is_exact_and_rejects_special_entries(custody, mutation):
    c, _ = custody
    g.prove(c)
    path = Path(c["checkout"]) / ".venv/extra"
    if mutation == "extra":
        path.write_text("new")
    elif mutation == "directory_symlink":
        path.symlink_to(Path(c["private"]), target_is_directory=True)
    elif mutation == "broken_symlink":
        path.symlink_to(path.parent / "missing")
    else:
        os.mkfifo(path)
    with pytest.raises(g.Refused):
        g.prove(c)


def test_source_added_and_private_mode_and_foreign_lease_remain_refused(custody):
    c, _ = custody
    g.prove(c)
    extra = Path(c["checkout"]) / "added"
    extra.write_text("new")
    with pytest.raises(g.Refused):
        g.prove(c)
    extra.unlink()
    Path(c["private"]).chmod(0o755)
    with pytest.raises(g.Refused):
        g.prove(c)
    Path(c["private"]).chmod(0o700)
    owner = Path(c["lock"]) / "owner.json"
    value = json.loads(owner.read_text())
    value["token"] = "foreign"
    owner.write_text(json.dumps(value))
    with pytest.raises(g.Refused):
        g.prove(c)


@pytest.mark.parametrize("field", ["st_ino", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_gid"])
def test_byte_cache_never_hides_live_stat_identity_change(custody, field):
    c, _ = custody
    file = Path(next(iter(c["runtime"])))
    g.sha(file)
    original = file.stat()
    from types import SimpleNamespace

    values = {
        name: getattr(original, name)
        for name in (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_mode",
            "st_uid",
            "st_gid",
        )
    }
    values[field] += 1
    altered = SimpleNamespace(**values)
    # Synthetic stat change must invalidate cached bytes. A mismatched descriptor
    # then refuses, proving no stale cache hit concealed the changed metadata.
    with patch.object(g.os, "stat", return_value=altered), pytest.raises(g.Refused):
        g.sha(file)


class Clock:
    ns = 0

    def now(self):
        return self.ns

    def advance(self, seconds):
        self.ns += int(seconds * 1_000_000_000)


def test_deadline_exhaustion_inside_proof_stops_before_next_entry(custody):
    c, _ = custody
    clock = Clock()
    budget = g.ProofBudget(clock=clock.now)
    original = g._entry
    count = 0

    def slow(path):
        nonlocal count
        count += 1
        result = original(path)
        clock.advance(16)
        return result

    with patch.object(g, "_entry", slow), pytest.raises(g.Refused):
        g.prove(c, budget=budget)
    assert count == 1
    assert budget.rows[-1]["stage"] == "custody"
    assert budget.rows[-1]["outcome"] == "refused"
    assert budget.rows[-1]["elapsed_ns"] == 16_000_000_000


@pytest.mark.parametrize("exhaust_at", ["proof", "owner_update"])
def test_executor_deadline_includes_preflight_and_no_spawn(custody, exhaust_at):
    c, _ = custody
    clock = Clock()
    budget = g.ProofBudget(clock=clock.now)
    runner = calibration.Runner()
    executor = calibration.executor_class(g, PACKET / "run_d7_secured_image_observed.py")(runner, c)
    executor.uncertain = True
    original = g.prove

    def proof(*args, **kwargs):
        original(*args, **kwargs)
        if exhaust_at == "proof":
            clock.advance(16)

    def update(*args, **kwargs):
        clock.advance(16)
        return True

    if exhaust_at == "owner_update":
        runner.update = update
    with (
        patch.object(g, "prove", proof),
        patch.object(calibration.subprocess, "Popen") as spawn,
        pytest.raises(g.Refused),
    ):
        executor(
            [c["docker"], "--config", c["docker_config"], "--context", "colima", "ps"], 10, budget=budget
        )
    spawn.assert_not_called()
    assert executor.uncertain and not runner._pending_groups


def test_capture_local_cap_includes_proof_and_never_resets_total_deadline(custody):
    c, _ = custody
    clock = Clock()
    budget = g.ProofBudget(clock=clock.now)
    e = Mock(c=c, r=calibration.Runner(), uncertain=False)
    with (
        patch.object(m.time, "monotonic", lambda: clock.ns / 1_000_000_000),
        patch.object(g, "prove", side_effect=lambda *a, **k: clock.advance(3)),
        patch.object(m.subprocess, "Popen") as spawn,
        pytest.raises(g.Refused),
    ):
        m.capture_command(e, ["never"], 2, 8, budget=budget)
    spawn.assert_not_called()
    assert budget.deadline_ns == 15_000_000_000
    assert not e.uncertain


def test_closed_monotonic_stages_refuse_unknown_and_cannot_extend_budget():
    clock = Clock()
    budget = g.ProofBudget(clock=clock.now)
    with budget.stage("context"):
        clock.advance(2)
    assert budget.left() == 13
    assert set(budget.rows[0]) == {"stage", "started_ns", "elapsed_ns", "outcome"}
    with pytest.raises(g.Refused), budget.stage("PRIVATE_CANARY"):
        pass
    with pytest.raises(g.Refused):
        g.ProofBudget(16)


def test_drift_between_context_and_inventory_is_reproved(custody):
    c, _ = custody
    calls = []

    def execute(argv, timeout):
        g.prove(c)
        calls.append(argv)
        source = Path(next(iter(c["source"])))
        source.write_text("changed after context")
        return calibration.subprocess.CompletedProcess(argv, 0, json.dumps(c["endpoint"]), "")

    with pytest.raises(g.Refused):
        g.Docker(c, execute).engine()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    ["foreign_context", "extra_id", "duplicate_id", "foreign_owner", "foreign_image", "missing_engine"],
)
def test_authenticated_identity_chain_retains_exact_owned_predicates(custody, mutation):
    c, state = custody

    def execute(argv, timeout):
        g.prove(c)
        if "context" in argv:
            value = json.dumps("unix:///foreign" if mutation == "foreign_context" else c["endpoint"])
        elif "ps" in argv:
            value = "\n".join(state)
            if mutation == "extra_id":
                value += "\n" + "f" * 64
            if mutation == "duplicate_id":
                value += "\n" + next(iter(state))
        else:
            cid = argv[-1]
            row = {
                "id": cid,
                **state[cid],
                "project": c["project"],
                "owner": c["project"],
                "running": True,
                "restart_count": 0,
                "started_at": "2026-09-10T00:00:00Z",
            }
            if mutation == "foreign_owner":
                row["owner"] = "foreign"
            if mutation == "foreign_image":
                row["image"] = "sha256:" + "f" * 64
            if mutation == "missing_engine":
                row["service"] = "postgres"
            value = json.dumps(row)
        return calibration.subprocess.CompletedProcess(argv, 0, value, "")

    with pytest.raises(g.Refused):
        g.Docker(c, execute).engine()


def test_executor_wait_receives_only_remaining_absolute_budget(custody):
    c, _ = custody
    clock = Clock()
    budget = g.ProofBudget(clock=clock.now)
    runner = calibration.Runner()
    executor = calibration.executor_class(g, PACKET / "run_d7_secured_image_observed.py")(runner, c)
    original = g.prove

    def proof(*args, **kwargs):
        original(*args, **kwargs)
        clock.advance(8)

    waited = []

    class Process:
        pid = 999999
        returncode = 0

        def __init__(self, *args, **kwargs):
            clock.advance(2)

        def wait(self, timeout):
            waited.append(timeout)

    with patch.object(g, "prove", proof), patch.object(calibration.subprocess, "Popen", Process):
        executor(
            [c["docker"], "--config", c["docker_config"], "--context", "colima", "ps"], 10, budget=budget
        )
    assert waited == [5]
    assert not runner._pending_groups
    assert budget.deadline_ns == 15_000_000_000


def test_collector_initial_custody_is_inside_receipt_budget(custody, tmp_path):
    c, _ = custody
    clock = Clock()
    original_budget = g.ProofBudget
    original_entry = g._entry

    def slow(path):
        result = original_entry(path)
        clock.advance(16)
        return result

    e = Mock(c=c, r=calibration.Runner(), uncertain=False)
    parent = tmp_path / "observation-metadata"
    with (
        patch.object(m, "PRIVATE_PARENT", parent),
        patch.object(g, "ProofBudget", lambda: original_budget(clock=clock.now)),
        patch.object(g, "_entry", slow),
        patch.object(m.subprocess, "Popen") as spawn,
        pytest.raises(g.Refused),
    ):
        m.collect(e, Mock(c=c), oracle_terminal=True)
    spawn.assert_not_called()
    receipt = json.loads((parent / c["project"] / "receipt.json").read_text())
    assert receipt["status"] == "UNAVAILABLE"
    assert receipt["container_id"] is None
    assert receipt["proof_budget_ns"] == 15_000_000_000
    assert receipt["proof_stages"][0]["outcome"] == "refused"
    assert receipt["proof_stages"][0]["stage"] == "custody"
