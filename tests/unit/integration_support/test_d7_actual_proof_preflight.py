"""Closed actual-preflight control flow with finite daemon fixtures; no real service."""

import ast
import contextlib
import copy
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

PACKET = Path(
    "/Users/familia/code/maezo-completion-evidence/strategy-cycle1-20260910/d7-preflight-final-deadline-repair/toolkit"
)
spec = importlib.util.spec_from_file_location(
    "d7_preflight_wrapper_controls", PACKET / "run_d7_secured_image_observed.py"
)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class Runner:
    def __init__(self):
        self._pending_groups = set()
        self._process_owner = self

    def owns(self):
        return True

    def update(self, *args, **kwargs):
        return True

    def _cleanup_signal_mask(self):
        return contextlib.nullcontext()

    def _record_pending(self, pid):
        self._pending_groups.add(pid)

    def _group_exists(self, pid):
        return False

    def _quiesce_group(self, process, pid):
        self._pending_groups.remove(pid)


class Daemon:
    """Finite response machine only; actual guarded Executor call sites remain exercised."""

    def __init__(self, config, fault):
        self.c = config
        self.fault = fault
        self.objects = {}
        self.volumes = set()
        self.prior = {"e" * 64}
        self.calls = []
        self.creates = 0
        self.measured = False

    def reply(self, argv):
        a = argv[5:]
        self.calls.append(a)
        if a[:2] == ["context", "inspect"]:
            return json.dumps(self.c["endpoint"])
        if a[0] == "ps":
            if any(x.startswith("name=") for x in a):
                return ""
            return "\n".join(self.objects)
        if a[:2] == ["network", "ls"]:
            return ""
        if a[:2] == ["volume", "ls"]:
            selector = a[a.index("--filter") + 1]
            if selector == "name=^[0-9a-f]{64}$":
                return "\n".join(self.prior)
            if selector.startswith("name=^") and len(selector) == 71:
                name = selector[6:-1]
                return name if name in self.volumes else ""
            return ""
        if a[:2] == ["image", "inspect"]:
            image = a[-1]
            if a[3] == "{{.Id}}":
                if self.fault == "missing_image":
                    return "absent"
                return image
            return json.dumps({"/synthetic-data": {}})
        if a[0] == "create":
            self.creates += 1
            service = "engine" if self.creates == 1 else "postgres"
            cid = ("a" if service == "engine" else "b") * 64
            name = ("c" if service == "engine" else "d") * 64
            if self.fault == "preexisting_volume":
                name = "e" * 64
            self.objects[cid] = {
                "id": cid,
                "service": service,
                "image": a[-1],
                "project": self.c["project"],
                "owner": self.c["project"],
                "running": False,
                "restart_count": 0,
                "started_at": "0001-01-01T00:00:00Z",
                "volume": name,
            }
            self.volumes.add(name)
            if self.fault == "lost_create":
                raise OSError("synthetic missing response")
            return cid
        if a[0] == "inspect":
            obj = self.objects[a[-1]]
            if ".Mounts" in a[2]:
                if "printf" in a[2]:
                    if self.fault == "bind_mount":
                        return "bind||/synthetic-data\n"
                    return "volume|" + obj["volume"] + "|/synthetic-data\n"
                return obj["volume"] + "\n"
            value = {k: v for k, v in obj.items() if k != "volume"}
            if self.fault == "running_object" or (
                self.fault == "late_postgres_start" and self.measured and obj["service"] == "postgres"
            ):
                value["running"] = True
            if self.fault == "foreign_owner":
                value["owner"] = "foreign"
            return json.dumps(value)
        if a[:2] == ["volume", "inspect"]:
            return json.dumps(
                {
                    "name": a[-1],
                    "driver": "foreign" if self.fault == "foreign_driver" else "local",
                    "scope": "local",
                    "options": {"unsafe": "x"} if self.fault == "volume_options" else None,
                }
            )
        if a[0] == "rm":
            cid = a[-1]
            if self.fault == "lost_remove":
                raise OSError("synthetic removal lost")
            obj = self.objects.pop(cid)
            if self.fault != "retained_volume":
                self.volumes.remove(obj["volume"])
            return cid
        raise AssertionError("unexpected fixture command: " + repr(a))


def run_fixture(tmp_path, fault=None, *, deadline=False):
    root = tmp_path.resolve()
    (root / "private").mkdir(mode=0o700)
    (root / "output").mkdir()
    for name in ("MANIFEST.json", "PREFLIGHT-DESCRIPTOR.json"):
        (root / name).write_text("{}")
    c = {
        "source_sha": w.g.SOURCE_SHA,
        "source_tree": w.g.SOURCE_TREE,
        "source": {"synthetic": {}},
        "runtime": {"synthetic": {}},
        "tools": {"synthetic": {}},
        "private": str(root / "private"),
        "output": str(root / "output"),
        "checkout": str(root),
        "docker": "/synthetic/docker",
        "docker_config": "/synthetic/config",
        "endpoint": "unix:///synthetic/socket",
        "project": "d7-synthetic-real-preflight",
        "parent_env": {},
    }
    w.g.save(root / "private/docker-owned.json", {})
    daemon = Daemon(c, fault)
    runner = Runner()
    executor = w.Executor(runner, c)

    class Process:
        pid = 999999
        returncode = 0

        def __init__(self, argv, **kwargs):
            output = daemon.reply(argv)
            kwargs["stdout"].write(output)

        def wait(self, timeout):
            assert timeout > 0

    ticks = [time.monotonic_ns()]
    real_budget = w.g.ProofBudget
    fired = False

    def budget_factory(*args, **kwargs):
        if not args:
            daemon.measured = True
        return real_budget(*args, **kwargs, clock=lambda: ticks[0])

    def proof(config, *, budget=None, **kwargs):
        nonlocal fired
        if budget is not None:
            with budget.stage("custody"):
                if fault == "measured_deadline" and daemon.measured and not fired:
                    fired = True
                    ticks[0] += 16_000_000_000

    docker = w.g.Docker(c, executor)
    with (
        patch.object(w, "HERE", root),
        patch.object(w.g, "ProofBudget", budget_factory),
        patch.object(w.time, "monotonic_ns", lambda: ticks[0]),
        patch.object(w.time, "monotonic", lambda: ticks[0] / 1_000_000_000),
        patch.object(w.g, "prove", proof),
        patch.object(w.startup_observation, "pins"),
        patch.object(w.subprocess, "Popen", Process),
    ):
        result = w._proof_identity_only(
            c, executor, docker, time.monotonic() - 1 if deadline else time.monotonic() + 600
        )
    return result, daemon, executor, docker


def test_real_command_structure_complete_stopped_chain_and_normal_removal(tmp_path):
    result, daemon, executor, docker = run_fixture(tmp_path)
    assert not result["identity_chain_fit"] and result["status"] == "COMPLETE_WITHIN_BUDGET"
    assert result["next_action"] != "ROOT_BOUNDARY_REVIEW_ELIGIBLE"
    # Only main may promote fit after existing finish proves release.
    assert result["custody_proof_count"] == 10
    assert result["command_counts"] == {"context": 3, "inventory": 1, "inspect": 2}
    assert result["measured_elapsed_ns"] < result["budget_ns"] == 15_000_000_000
    assert not daemon.objects and not daemon.volumes and not docker.state()
    assert daemon.creates == 2 and not executor.uncertain
    assert result["actual_full_collection_fit"] is None and not result["readiness"]
    assert all(a[0] not in {"start", "run", "exec", "build", "pull", "compose"} for a in daemon.calls)
    creates = [a for a in daemon.calls if a[0] == "create"]
    assert all(
        "--network" in a and "none" in a and "--entrypoint" in a and "/bin/false" in a for a in creates
    )
    assert all(not {"--env", "--mount", "--publish", "-p"} & set(a) for a in creates)
    assert all(a[:2] == ["rm", "-v"] for a in daemon.calls if a[0] == "rm")
    assert sum(a[:2] == ["volume", "ls"] and "name=^[0-9a-f]{64}$" in a for a in daemon.calls) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "preexisting_volume",
        "foreign_driver",
        "volume_options",
        "bind_mount",
        "running_object",
        "foreign_owner",
        "lost_create",
        "lost_remove",
        "retained_volume",
    ],
)
def test_uncertain_or_foreign_create_and_removal_never_qualify(tmp_path, fault):
    result, daemon, executor, _ = run_fixture(tmp_path, fault)
    assert not result["identity_chain_fit"]
    assert result["status"] == "UNCERTAIN"
    assert executor.uncertain
    assert result["next_action"] == "ROOT_RECOVERY_REQUIRED"
    assert result["cleanup_status"] == "RETAINED"
    if fault != "retained_volume":
        assert daemon.objects


def test_absent_image_is_not_fit_and_does_not_create(tmp_path):
    result, daemon, executor, _ = run_fixture(tmp_path, "missing_image")
    assert not result["identity_chain_fit"] and result["objects_verified"] == 0
    assert result["status"] == "INPUT_UNAVAILABLE" and not executor.uncertain
    assert daemon.creates == 0


def test_expired_setup_does_not_create_or_measure(tmp_path):
    result, daemon, _, _ = run_fixture(tmp_path, deadline=True)
    assert not result["identity_chain_fit"] and result["measured_started_monotonic_ns"] is None
    assert daemon.creates == 0


@pytest.mark.parametrize("name", ["named-volume", "A" * 64, "a" * 63, "a" * 64 + "\n" + "a" * 64])
def test_volume_name_projection_is_closed(name):
    with pytest.raises(w.g.Refused):
        w._preflight_names(name)


def test_actual_main_modes_and_transitive_pin_gate_before_runner():
    class ReachedError(Exception):
        pass

    for flag in ("--observe-startup", "--proof-identity-only"):
        with (
            patch.object(sys, "argv", ["tool", "--lane", "boundary", "--output", "/synthetic/fresh", flag]),
            patch.object(w, "tool_pins", return_value={}),
            patch.object(w, "load_runner", side_effect=ReachedError) as reached,
            pytest.raises(ReachedError),
        ):
            w.main()
        reached.assert_called_once()
    with (
        patch.object(
            sys,
            "argv",
            [
                "tool",
                "--lane",
                "boundary",
                "--output",
                "/synthetic/fresh",
                "--observe-startup",
                "--proof-identity-only",
            ],
        ),
        patch.object(w, "load_runner") as runner,
        pytest.raises(SystemExit),
    ):
        w.main()
    runner.assert_not_called()


@pytest.mark.parametrize("fit", [True, False])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_actual_main_proof_branch_cannot_fall_through_to_startup(tmp_path, fit, cleanup_failure):

    root = tmp_path.resolve()
    checkout = root / "materialized"
    checkout.mkdir()
    (checkout / "fixture.py").write_text("synthetic")
    output = root / "output"
    lock = Mock()
    lock.owner = {}
    lock.token = "synthetic-token"
    lock.acquire.return_value = True
    lock.update.return_value = True
    runner = Mock()
    runner._pending_groups = set()
    runner.LOCK_DIR = root / "lease"
    runner.EngineLock.create.return_value = lock
    runner.validate_checkout.return_value = (w.INPUT, None)
    runner._git.return_value = w.g.SOURCE_TREE
    runner.RunnerInterrupted = type("Interrupted", (Exception,), {})

    @contextlib.contextmanager
    def materialize(*args):
        (output / "execution-source.json").write_text(
            json.dumps(
                {
                    "tracked_sha256": {
                        "fixture.py": hashlib.sha256((checkout / "fixture.py").read_bytes()).hexdigest()
                    }
                }
            )
        )
        yield checkout, None

    runner.execution_checkout.side_effect = materialize
    original_sha = w.g.sha

    def sha(path):
        return w.PYTHON_SHA if str(path).endswith(".venv/bin/python") else original_sha(path)

    def context(config):
        config["endpoint"] = "unix:///synthetic/socket"

    result = {
        "identity_chain_fit": fit,
        "cleanup_status": "RETAINED",
        "status": "COMPLETE_WITHIN_BUDGET" if fit else "DEADLINE_REFUSED",
        "budget_ns": 15_000_000_000,
        "measured_started_monotonic_ns": 0,
        "measured_elapsed_ns": 1_000_000_000,
        "objects_expected": 2,
        "objects_verified": 2,
        "all_objects_stopped": True,
        "daemon_uncertain": False,
        "pending_group_count": 0,
        "custody_proof_count": 10,
        "command_counts": {"context": 3, "inventory": 1, "inspect": 2},
        "stages": [
            {"stage": stage, "started_ns": 0, "elapsed_ns": 0, "outcome": "complete"}
            for stage, count in (("custody", 10), ("context", 3), ("inventory", 1), ("inspect", 2))
            for _ in range(count)
        ],
    }
    fake_executor = Mock(uncertain=False)
    fake_executor.side_effect = AssertionError("startup child or command must never execute")
    with (
        patch.object(
            sys, "argv", ["tool", "--lane", "boundary", "--output", str(output), "--proof-identity-only"]
        ),
        patch.object(w, "tool_pins", return_value={}),
        patch.object(w, "load_runner", return_value=runner),
        patch.object(w, "PRIVATE_PARENT", root / "private-parent"),
        patch.object(w.g, "sha", sha),
        patch.object(w.g, "file_map", return_value={}),
        patch.object(w.g, "prove"),
        patch.object(w, "read_context", context),
        patch.object(w, "Executor", return_value=fake_executor),
        patch.object(w, "_proof_identity_only", return_value=result) as proof,
        patch.object(
            w,
            "finish",
            side_effect=ValueError("synthetic private cleanup fault") if cleanup_failure else None,
        ) as finish,
        patch.object(w.signal, "signal"),
        patch.object(w.subprocess, "Popen") as spawn,
    ):
        rc = w.main()
    assert rc == (0 if fit and not cleanup_failure else 1)
    proof.assert_called_once()
    finish.assert_called_once()
    fake_executor.assert_not_called()
    spawn.assert_not_called()
    final = json.loads((output / "state.json").read_text())
    assert final["actual_acceptance"] is False
    assert "actual_http_cases_passed" not in final
    assert json.loads((output / "identity-proof.json").read_text())["cleanup_status"] == (
        "RETAINED" if cleanup_failure else "VERIFIED_RELEASED"
    )
    if cleanup_failure:
        assert not final["identity_proof"]["identity_chain_fit"]
        assert final["identity_proof"]["next_action"] == "ROOT_RECOVERY_REQUIRED"


def test_measured_deadline_refuses_without_reset_then_owned_cleanup(tmp_path):
    result, daemon, executor, _ = run_fixture(tmp_path, "measured_deadline")
    assert result["status"] == "DEADLINE_REFUSED"
    assert not result["identity_chain_fit"]
    assert result["next_action"] == "REPAIR_PROOF_BUDGET"
    assert result["budget_ns"] == 15_000_000_000
    assert result["measured_elapsed_ns"] == 16_000_000_000
    assert result["command_counts"] == {"context": 0, "inventory": 0, "inspect": 0}
    assert not daemon.objects and not executor.uncertain


def test_postgres_must_remain_stopped_in_measured_chain(tmp_path):
    result, daemon, executor, _ = run_fixture(tmp_path, "late_postgres_start")
    assert not result["identity_chain_fit"] and executor.uncertain
    assert result["next_action"] == "ROOT_RECOVERY_REQUIRED"
    # Only still-proved stopped objects can be removed; the newly running PG remains.
    assert any(obj["service"] == "postgres" for obj in daemon.objects.values())


@pytest.mark.parametrize("expire", ["initial", "first_archive", "final_archive", "private_disposal"])
def test_cleanup_deadline_prevents_next_disposal_stage(tmp_path, expire):
    private, output, checkout = tmp_path / "private", tmp_path / "output", tmp_path / "checkout"
    for path in (private, output, checkout):
        path.mkdir()
    ticks = [0.0]
    if expire == "initial":
        ticks[0] = 121
    runner = Runner()
    runner._assert_execution_source = Mock()
    runner._source_cleanup_ready = set()
    lock = Mock(owner={"subprocess_quiescent": True})
    lock.owns.return_value = True
    context = Mock()
    context.__exit__ = Mock()
    docker = Mock()
    docker.inventory.return_value = {}
    docker.state.return_value = {}
    docker.ids.return_value = set()
    docker.raw.return_value = subprocess.CompletedProcess([], 0, "", "")

    def archive(*args, label):
        if (expire == "first_archive" and label == "pre-teardown") or (
            expire == "final_archive" and label == "final"
        ):
            ticks[0] = 121

    real_remove = w.shutil.rmtree

    def remove(path):
        real_remove(path)
        if expire == "private_disposal":
            ticks[0] = 121

    with (
        patch.object(w.g, "prove"),
        patch.object(w.time, "monotonic", lambda: ticks[0]),
        patch.object(w, "archive", side_effect=archive) as archived,
        patch.object(w.shutil, "rmtree", side_effect=remove) as removed,
        pytest.raises(w.g.Refused),
    ):
        w.finish(
            runner,
            {"private": str(private), "output": str(output), "project": "d7-synthetic-cleanup"},
            docker,
            context,
            checkout,
            lock,
            executor=Mock(uncertain=False),
            deadline=120,
        )
    context.__exit__.assert_not_called()
    lock.release.assert_not_called()
    assert checkout.exists()
    if expire in ("initial", "first_archive"):
        docker.inventory.assert_not_called()
    if expire != "private_disposal":
        removed.assert_not_called()
    if expire == "initial":
        archived.assert_not_called()


def _run_actual_main_with_helper_result(root, result, expected_fit):
    """Reuse only the existing isolated setup; main and the actual helper result stay intact."""
    node = ast.parse(inspect.getsource(test_actual_main_proof_branch_cannot_fall_through_to_startup)).body[0]
    node.decorator_list = []
    for statement in node.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "result" for target in statement.targets
        ):
            statement.value = ast.Name(id="actual_helper_result", ctx=ast.Load())
    namespace = dict(globals(), actual_helper_result=result)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "<main-fixture>", "exec"
        ),
        namespace,
    )
    root.mkdir()
    namespace[node.name](root, expected_fit, False)
    final = json.loads((root / "output/identity-proof.json").read_text())
    assert final["identity_chain_fit"] is expected_fit
    assert (final["next_action"] == "ROOT_BOUNDARY_REVIEW_ELIGIBLE") is expected_fit
    assert final["cleanup_status"] == "VERIFIED_RELEASED"
    return final


@pytest.mark.parametrize("elapsed", [14_999_999_999, 15_000_000_000, 16_000_000_000])
def test_final_recorded_timestamp_controls_helper_and_actual_main(tmp_path, elapsed):
    base = w.g.ProofBudget

    class SuspendedAfterFinalCheck(base):
        def left(self):
            remaining = super().left()
            caller = sys._getframe(1)
            if (
                caller.f_code.co_name == "_proof_identity_only"
                and sum(row["stage"] == "custody" for row in self.rows) == 10
            ):
                ticks = self.clock.__closure__[0].cell_contents
                ticks[0] = self.started_ns + elapsed
            return remaining

    helper_root = tmp_path / "helper"
    helper_root.mkdir()
    with patch.object(w.g, "ProofBudget", SuspendedAfterFinalCheck):
        result, daemon, executor, docker = run_fixture(helper_root)
    fits = elapsed < 15_000_000_000
    assert result["measured_elapsed_ns"] == elapsed
    assert result["budget_ns"] == 15_000_000_000
    assert result["status"] == ("COMPLETE_WITHIN_BUDGET" if fits else "DEADLINE_REFUSED")
    assert not result["identity_chain_fit"]
    assert not daemon.objects and not executor.uncertain and not docker.state()
    final = _run_actual_main_with_helper_result(tmp_path / "main", result, fits)
    assert final["measured_elapsed_ns"] == elapsed
    if not fits:
        assert final["next_action"] == "REPAIR_PROOF_BUDGET"


@pytest.mark.parametrize(
    "field,value",
    [
        ("measured_elapsed_ns", 15_000_000_000),
        ("measured_elapsed_ns", 16_000_000_000),
        ("measured_elapsed_ns", -1),
        ("measured_elapsed_ns", None),
        ("measured_elapsed_ns", True),
        ("budget_ns", 16_000_000_000),
        ("measured_started_monotonic_ns", None),
        ("measured_started_monotonic_ns", -1),
        ("objects_expected", 1),
        ("objects_verified", 1),
        ("all_objects_stopped", False),
        ("all_objects_stopped", 1),
        ("daemon_uncertain", True),
        ("pending_group_count", 1),
        ("custody_proof_count", 9),
        ("command_counts", {"context": 2, "inventory": 1, "inspect": 2}),
        ("command_counts", {"context": 3, "inventory": True, "inspect": 2}),
        ("stages", []),
    ],
)
def test_complete_status_alone_cannot_promote_incomplete_or_invalid_chain(tmp_path, field, value):
    helper_root = tmp_path / "helper"
    helper_root.mkdir()
    result, _, _, _ = run_fixture(helper_root)
    assert result["status"] == "COMPLETE_WITHIN_BUDGET"
    result[field] = value
    final = _run_actual_main_with_helper_result(tmp_path / "main", result, False)
    assert final["status"] == "INPUT_UNAVAILABLE"


@pytest.mark.parametrize("change", ["refused", "missing_custody", "future_endpoint"])
def test_promotion_rechecks_actual_proof_rows(tmp_path, change):
    helper_root = tmp_path / "helper"
    helper_root.mkdir()
    result, _, _, _ = run_fixture(helper_root)
    result = copy.deepcopy(result)
    if change == "refused":
        result["stages"][0]["outcome"] = "refused"
    elif change == "missing_custody":
        result["stages"] = result["stages"][1:]
    else:
        result["stages"][0]["elapsed_ns"] = result["measured_elapsed_ns"] + 1
    _run_actual_main_with_helper_result(tmp_path / "main", result, False)
