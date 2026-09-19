"""Bounded observer tooling controls; synthetic data/processes, no engine integration claim."""

import contextlib
import io
import json
import os
import signal
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

PACKET = Path(
    "/Users/familia/code/maezo-completion-evidence/strategy-cycle1-20260910/d7-actual-proof-preflight-repair"
)
# WP-C078 port adaptation: the observation packet is a machine-local evidence bundle that is
# not part of the repository at any ref. Where it is absent (CI runners, other machines) the
# module is skipped at collection with the reason below instead of failing it; where it is
# present every assertion below runs unchanged against the packet.
if not PACKET.is_dir():
    pytest.skip(f"D7 observation packet absent: {PACKET}", allow_module_level=True)
# WP-C078 port adaptation: the sibling preflight test module loads a different evidence packet
# whose runner registers the same module names ("d7_guard") from its own directory, while every
# packet pins its guard by path. Drop any packet-loaded names so the imports below bind to THIS
# packet's files; already-imported packet modules keep their own references and are unaffected.
for _packet_loaded in ("d7_guard", "d7_diagnostics", "d7_startup_observation", "prepare_observation"):
    sys.modules.pop(_packet_loaded, None)
sys.path.insert(0, str(PACKET))
import d7_startup_observation as m  # noqa: E402
import prepare_observation as prep  # noqa: E402


class Owner:
    def owns(self):
        return True

    def update(self, *a, **k):
        return True


class Runner:
    def __init__(self):
        self._pending_groups = set()
        self._process_owner = Owner()

    def _cleanup_signal_mask(self):
        return contextlib.nullcontext()

    def _record_pending(self, p):
        self._pending_groups.add(p)

    def _group_exists(self, p):
        try:
            os.killpg(p, 0)
            return True
        except ProcessLookupError:
            return False

    def _quiesce_group(self, process, p):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p, signal.SIGKILL)
        process.wait(timeout=2)
        self._pending_groups.discard(p)


class Executor:
    def __init__(self, c):
        self.c = c
        self.r = Runner()
        self.uncertain = False


def archive(items):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w") as t:
        for name, data, kind in items:
            i = tarfile.TarInfo(name)
            i.type = kind
            i.size = len(data)
            t.addfile(i, io.BytesIO(data))
    return b.getvalue()


class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)
        self.compose = {
            "services": {
                "engine": {
                    "environment": {
                        "JAVA_OPTS": prep.DEFAULT_JAVA_OPTS,
                        "JAVA_TOOL_OPTIONS": "PRIVATE_CANARY_OPTIONS",
                    },
                    "volumes": [],
                },
                "postgres": {"unchanged": True},
            }
        }
        self.project = "d7-abcdef0123456789-boundary"

    def tearDown(self):
        self.tmp.cleanup()

    def test01_optin_exact(self):
        result = prep.instrument(self.compose, self.root, self.project)
        self.assertEqual(result["services"]["engine"]["tmpfs"], [prep.TMPFS])
        self.assertEqual(result["services"]["postgres"], self.compose["services"]["postgres"])
        self.assertNotIn("tmpfs", self.compose["services"]["engine"])

    def test02_existing_options_preserved(self):
        result = prep.instrument(self.compose, self.root, self.project)
        self.assertEqual(
            result["services"]["engine"]["environment"]["JAVA_TOOL_OPTIONS"],
            "PRIVATE_CANARY_OPTIONS",
        )
        self.assertTrue(
            result["services"]["engine"]["environment"]["JAVA_OPTS"].startswith(prep.DEFAULT_JAVA_OPTS + " ")
        )

    def test03_foreign_java_opts_refused(self):
        self.compose["services"]["engine"]["environment"]["JAVA_OPTS"] = "foreign"
        self.assertRaises(ValueError, prep.instrument, self.compose, self.root, self.project)

    def test04_existing_tmpfs_refused(self):
        self.compose["services"]["engine"]["tmpfs"] = ["foreign"]
        self.assertRaises(ValueError, prep.instrument, self.compose, self.root, self.project)

    def test05_target_overlap_refused(self):
        self.compose["services"]["engine"]["volumes"] = [{"target": prep.AREA}]
        self.assertRaises(ValueError, prep.instrument, self.compose, self.root, self.project)

    def test06_bad_project(self):
        for value in ["../../secret", "d7-$(id)", "d7-x\n", "PRIVATE_CANARY"]:
            self.assertRaises(ValueError, prep.instrument, self.compose, self.root, value)

    def test07_tar_exact(self):
        self.assertEqual(
            m.one_tar(archive([("status", b"ok", tarfile.REGTYPE)]), "status", 256),
            b"ok",
        )

    def test08_tar_traversal(self):
        self.assertRaises(
            m.g.Refused,
            m.one_tar,
            archive([("../status", b"x", tarfile.REGTYPE)]),
            "status",
            256,
        )

    def test09_tar_link(self):
        self.assertRaises(
            m.g.Refused,
            m.one_tar,
            archive([("status", b"", tarfile.SYMTYPE)]),
            "status",
            256,
        )

    def test10_tar_extra(self):
        self.assertRaises(
            m.g.Refused,
            m.one_tar,
            archive([("status", b"x", tarfile.REGTYPE), ("other", b"x", tarfile.REGTYPE)]),
            "status",
            256,
        )

    def test11_tar_size(self):
        self.assertRaises(
            m.g.Refused,
            m.one_tar,
            archive([("status", b"x" * 257, tarfile.REGTYPE)]),
            "status",
            256,
        )

    def test12_partial_archive(self):
        self.assertIsNone(
            m.recording_from_tar(
                archive(
                    [
                        (
                            "maezo-startup-observation/repository/partial.jfr",
                            b"private",
                            tarfile.REGTYPE,
                        )
                    ]
                )
            )
        )

    def test13_complete_archive(self):
        self.assertEqual(
            m.recording_from_tar(
                archive(
                    [
                        (
                            "maezo-startup-observation/completed.jfr",
                            b"private",
                            tarfile.REGTYPE,
                        )
                    ]
                )
            ),
            b"private",
        )

    def test14_archive_traversal(self):
        self.assertRaises(
            m.g.Refused,
            m.recording_from_tar,
            archive([("maezo-startup-observation/../bad", b"x", tarfile.REGTYPE)]),
        )

    def test15_pid_project_binding(self):
        self.assertEqual(
            m.started_pid(("RECORDING_STARTED|12|" + self.project + "\n").encode(), self.project),
            "12",
        )

    def test16_pid_adversarial(self):
        for raw in [
            b"RECORDING_STARTED|0|x\n",
            b"RECORDING_STARTED|$(id)|x\n",
            b"PRIVATE_CANARY",
            b"RECORDING_UNAVAILABLE\n",
        ]:
            self.assertRaises(m.g.Refused, m.started_pid, raw, self.project)

    def test17_stop_literal(self):
        a = m.stop_argv({"docker": "docker", "docker_config": "private"}, "a" * 64, "12")
        self.assertEqual(
            a[-3:],
            [
                "JFR.stop",
                "name=MaezoD7Startup",
                "filename=" + m.AREA + "/completed.jfr",
            ],
        )
        self.assertNotIn("0", a)

    def test18_stop_bad_id(self):
        self.assertRaises(
            m.g.Refused,
            m.stop_argv,
            {"docker": "docker", "docker_config": "x"},
            "foreign",
            "12",
        )

    def test19_private_read_mode(self):
        f = self.root / "x"
        f.write_bytes(b"x")
        f.chmod(0o644)
        self.assertRaises(m.g.Refused, m.private_read, f, 10)

    def test20_private_read_link(self):
        f = self.root / "x"
        m.save_private(f, b"x")
        os.link(f, self.root / "y")
        self.assertRaises(m.g.Refused, m.private_read, f, 10)

    def test21_private_read_positive(self):
        f = self.root / "x"
        m.save_private(f, b"private")
        self.assertEqual(m.private_read(f, 10), b"private")

    def test22_no_overwrite(self):
        f = self.root / "x"
        m.save_private(f, b"original")
        self.assertRaises(FileExistsError, m.save_private, f, b"changed")
        self.assertEqual(f.read_bytes(), b"original")

    def test23_public_positive(self):
        m.validate_public(json.loads((PACKET / "synthetic/results/projection.json").read_text()))

    def test24_public_no_promotion(self):
        for key, value in [
            ("cause", "DATABASE"),
            ("readiness", True),
            ("complete_history", True),
            ("clock", "ALIGNED"),
        ]:
            p = json.loads((PACKET / "synthetic/results/projection.json").read_text())
            p[key] = value
            self.assertRaises(m.g.Refused, m.validate_public, p)

    def test25_public_canary(self):
        p = json.loads((PACKET / "synthetic/results/projection.json").read_text())
        p["rows"][0]["stage"] = "PRIVATE_CANARY"
        self.assertRaises(m.g.Refused, m.validate_public, p)

    def test26_public_boolean_count(self):
        p = json.loads((PACKET / "synthetic/results/projection.json").read_text())
        p["rows"][0]["count"] = True
        self.assertRaises(m.g.Refused, m.validate_public, p)

    def test27_public_extra_key(self):
        p = json.loads((PACKET / "synthetic/results/projection.json").read_text())
        p["raw"] = "PRIVATE_CANARY"
        self.assertRaises(m.g.Refused, m.validate_public, p)

    def test28_no_preoracle_collection(self):
        self.assertRaises(m.g.Refused, m.collect, None, None, oracle_terminal=False)

    def test29_readonly_overflow_quiesces(self):
        c = {"checkout": str(self.root), "parent_env": {"PATH": "/usr/bin:/bin"}}
        e = Executor(c)
        with patch.object(m.g, "prove"):
            self.assertRaises(
                m.g.Refused,
                m.capture_command,
                e,
                [sys.executable, "-c", 'print("X"*10000)'],
                2,
                10,
            )
        self.assertFalse(e.uncertain)
        self.assertFalse(e.r._pending_groups)

    def test30_mutating_timeout_retains_uncertainty(self):
        c = {"checkout": str(self.root), "parent_env": {"PATH": "/usr/bin:/bin"}}
        e = Executor(c)
        with patch.object(m.g, "prove"):
            self.assertRaises(
                m.g.Refused,
                m.capture_command,
                e,
                [sys.executable, "-c", "import time;time.sleep(1)"],
                0.03,
                10,
                mutating=True,
            )
        self.assertTrue(e.uncertain)
        self.assertFalse(e.r._pending_groups)

    def test31_best_effort_preserves_failure(self):
        e = Executor({})
        state = {"state": "failed", "returncode": 1}
        with patch.object(m, "collect", side_effect=RuntimeError("PRIVATE_CANARY")):
            m.best_effort(e, None, state)
        self.assertEqual(state["returncode"], 1)
        self.assertEqual(state["state"], "failed")
        self.assertNotIn("PRIVATE_CANARY", json.dumps(state))

    def test32_source_oracle_unchanged(self):
        import ast

        before = ast.parse((PACKET / "baseline/run_d7_secured_image.py").read_text())
        after = ast.parse((PACKET / "run_d7_secured_image_observed.py").read_text())
        for name in [
            "finish",
            "_project_readiness_report",
            "_read_readiness_report",
            "_valid_readiness_connection_trace",
            "archive",
            "load_runner",
        ]:
            a = next(x for x in before.body if isinstance(x, ast.FunctionDef) and x.name == name)
            b = next(x for x in after.body if isinstance(x, ast.FunctionDef) and x.name == name)
            if name == "finish":
                # Only an optional cleanup deadline and three added calls to the
                # unchanged safety predicate are allowed in this successor.
                b.args.kwonlyargs.pop()
                b.args.kw_defaults.pop()
                guard = next(n for n in b.body if isinstance(n, ast.FunctionDef))
                self.assertIsInstance(guard.body[0], ast.If)
                self.assertEqual(ast.unparse(guard.body[0].test), "deadline is not None")
                guard.body.pop(0)
                # Remove precisely the added calls before inventory, source disposal,
                # and lease release, retaining every pre-existing safety call.
                for index in reversed(range(len(b.body) - 1)):
                    current, following = b.body[index:index + 2]
                    if (
                        isinstance(current, ast.If)
                        and ast.unparse(current.test) == "deadline is not None"
                        and ast.unparse(current.body[0]) == "require_cleanup_safe()"
                        and (
                        ast.unparse(following).startswith("current = docker.inventory()")
                        or ast.unparse(following).startswith("runner._source_cleanup_ready.add(")
                        or ast.unparse(following).startswith("g.require(lock.release(),")
                        )
                    ):
                        b.body.pop(index)
            self.assertEqual(ast.dump(a), ast.dump(b))

    def test33_byte_pinned_unmodified_dependencies_and_child_behavior(self):
        import hashlib
        import re

        before = (PACKET / "inputs/d7_child.py").read_text()
        after = (PACKET / "d7_child.py").read_text()
        pattern = r'GUARD_SHA256 = "[0-9a-f]+"'
        self.assertEqual(re.sub(pattern, "PIN", before), re.sub(pattern, "PIN", after))
        self.assertIn(hashlib.sha256((PACKET / "d7_guard.py").read_bytes()).hexdigest(), after)
        for name in [
            "d7_diagnostics.py",
            "tool-pins.json",
        ]:
            self.assertEqual((PACKET / name).read_bytes(), (PACKET / "baseline" / name).read_bytes())

    def test34_environment_not_inspected(self):
        with patch.dict(os.environ, {"JAVA_TOOL_OPTIONS": "AMBIENT_CANARY"}):
            result = prep.instrument(self.compose, self.root, self.project)
        self.assertNotIn("AMBIENT_CANARY", json.dumps(result))

    def run_collect(
        self, stop_rc=0, partial=False, uncertain=False, stop_error=None,
        tmpfs_options="rw,nosuid,nodev,noexec,size=33554432,mode=0700,uid=1000,gid=1000",
    ):
        parent = self.root / "retained"
        parent.mkdir(mode=0o700)
        c = {
            "project": self.project,
            "source_sha": m.g.SOURCE_SHA,
            "source_tree": m.g.SOURCE_TREE,
            "secured_image": "sha256:" + "b" * 64,
            "docker": "docker",
            "docker_config": "private",
            "checkout": str(self.root),
            "parent_env": {},
        }
        e = Executor(c)

        class Owned:
            def __init__(self, *args, **kwargs):
                self.c = c

            def engine(self):
                return {
                    "image": c["secured_image"],
                    "running": True,
                    "restart_count": 0,
                    "id": "a" * 64,
                }

        calls = []

        def command(executor, argv, timeout, cap, **kw):
            calls.append(argv)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 15)
            if "inspect" in argv:
                return json.dumps({m.AREA: tmpfs_options}).encode(), 0
            if "JFR.stop" in argv:
                if stop_error is not None:
                    raise stop_error
                if uncertain:
                    e.uncertain = True
                    raise TimeoutError("PRIVATE_CANARY")
                return b"", stop_rc
            if argv[-2].endswith("/status"):
                return archive(
                    [
                        (
                            "status",
                            ("RECORDING_STARTED|12|" + self.project + "\n").encode(),
                            tarfile.REGTYPE,
                        )
                    ]
                ), 0
            name = "repository/partial.jfr" if partial else "completed.jfr"
            return archive(
                [
                    (
                        "maezo-startup-observation/" + name,
                        b"PRIVATE_SYNTHETIC_JFR",
                        tarfile.REGTYPE,
                    )
                ]
            ), 0

        with (
            patch.object(m, "PRIVATE_PARENT", parent),
            patch.object(m.g, "prove"),
            patch.object(m.g, "Docker", Owned),
            patch.object(m, "capture_command", side_effect=command),
        ):
            state = {"returncode": 1}
            m.best_effort(e, Owned(), state)
        return state, parent / self.project, e, calls

    def test35_collect_success_custody(self):
        state, directory, e, calls = self.run_collect()
        self.assertEqual(state["startup_observation"]["status"], "CAPTURED")
        self.assertTrue((directory / "recording.tar").is_file())
        self.assertEqual(state["returncode"], 1)
        self.assertEqual(sum("JFR.stop" in a for a in calls), 1)

    def test36_partial_retained_unavailable(self):
        state, directory, e, calls = self.run_collect(stop_rc=1, partial=True)
        self.assertEqual(state["startup_observation"]["status"], "UNAVAILABLE")
        # Nonzero stop completion is unknown, so no subsequent copy is admitted.
        self.assertFalse((directory / "recording.tar").exists())
        self.assertTrue((directory / "status").is_file())
        self.assertEqual(state["returncode"], 1)
        self.assertTrue(e.uncertain)
        receipt = json.loads((directory / "receipt.json").read_text())
        self.assertTrue(receipt["daemon_uncertain"])
        self.assertEqual(receipt["stop_returncode"], 1)
        self.assertEqual(len(calls), 3)
        self.assert_original_finish_refuses(e)

    def test37_attach_uncertainty(self):
        state, directory, e, calls = self.run_collect(uncertain=True)
        self.assertTrue(e.uncertain)
        self.assertEqual(state["returncode"], 1)
        receipt = json.loads((directory / "receipt.json").read_text())
        self.assertTrue(receipt["daemon_uncertain"])
        self.assertEqual(len(calls), 3)

    def test38_closed_offline_real_synthetic_jfr(self):
        parent = self.root / "recordings"
        parent.mkdir(mode=0o700)
        directory = parent / self.project
        directory.mkdir(mode=0o700)
        recording_path = PACKET / "synthetic/results/synthetic.jfr"
        # WP-C078 port adaptation: the packet's captured synthetic recording is optional packet
        # content (absent from the local packet copy); without it the test's oracle has no real
        # JFR to promote, so skip rather than substitute synthetic bytes for it.
        if not recording_path.is_file():
            self.skipTest(f"packet synthetic recording absent: {recording_path}")
        recording = recording_path.read_bytes()
        raw = archive([("maezo-startup-observation/completed.jfr", recording, tarfile.REGTYPE)])
        m.save_private(directory / "recording.tar", raw)

        def sha(b):
            return __import__("hashlib").sha256(b).hexdigest()

        receipt = {
            "source_sha": m.g.SOURCE_SHA,
            "source_tree": m.g.SOURCE_TREE,
            "project": self.project,
            "status": "CAPTURED",
            "oracle_terminal": True,
            "daemon_uncertain": False,
            "pending_pgids": [],
            "observer_jar_sha256": m.pins()["files"]["bundle/observer.jar"],
            "archive_bytes": len(raw),
            "archive_sha256": sha(raw),
            "jfr_bytes": len(recording),
            "jfr_sha256": sha(recording),
        }
        receipt_raw = json.dumps(receipt).encode()
        m.save_private(directory / "receipt.json", receipt_raw)
        output = self.root / "public"
        output.mkdir(mode=0o700)
        with patch.object(m, "PRIVATE_PARENT", parent):
            self.assertEqual(m.project_private(directory, sha(receipt_raw), output), "PROJECTED")
        self.assertNotIn("PHI_SECRET_CANARY", (output / "startup-stage-projection.json").read_text())

    def test39_nofollow_ancestor_read(self):
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        m.save_private(actual / "data", b"private")
        alias = self.root / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(OSError):
            m.private_read(alias / "data", 10)

    def test40_nofollow_ancestor_write(self):
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(OSError):
            m.save_private(alias / "data", b"private")
        self.assertFalse((actual / "data").exists())

    def assert_original_finish_refuses(self, executor):
        """Execute the unchanged finish function, with every effect denied by mocks."""
        import ast
        from unittest.mock import Mock

        tree = ast.parse((PACKET / "run_d7_secured_image_observed.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "finish")
        namespace = {"g": m.g, "Path": Path}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "original-finish", "exec"), namespace)
        runner = executor.r
        runner._assert_execution_source = Mock()
        docker, context, lock = Mock(), Mock(), Mock()
        lock.owns.return_value = True
        lock.owner = {"subprocess_quiescent": True}
        with patch.object(m.g, "prove"), self.assertRaises(m.g.Refused):
            namespace["finish"](
                runner, {"private": str(self.root)}, docker, context,
                self.root / "checkout", lock, executor=executor,
            )
        runner._assert_execution_source.assert_not_called()
        self.assertEqual(docker.mock_calls, [])
        self.assertEqual(context.mock_calls, [])
        lock.release.assert_not_called()

    def command_executor(self):
        return Executor({"checkout": str(self.root), "parent_env": {"PATH": "/usr/bin:/bin"}})

    def test41_mutating_exit_codes_gate_original_finish(self):
        for rc in (0, 1, 2, 255):
            with self.subTest(rc=rc):
                e = self.command_executor()
                with patch.object(m.g, "prove"):
                    raw, actual = m.capture_command(
                        e, [sys.executable, "-I", "-B", "-c", f"raise SystemExit({rc})"],
                        2, 32, mutating=True, require_success=False,
                    )
                self.assertEqual((raw, actual), (b"", rc))
                self.assertEqual(e.uncertain, rc != 0)
                self.assertFalse(e.r._pending_groups)
                if rc:
                    self.assert_original_finish_refuses(e)

    def test42_success_does_not_clear_prior_uncertainty(self):
        e = self.command_executor()
        e.uncertain = True
        with patch.object(m.g, "prove"):
            self.assertEqual(m.capture_command(
                e, [sys.executable, "-I", "-B", "-c", "pass"], 2, 32, mutating=True,
            ), (b"", 0))
        self.assertTrue(e.uncertain)
        self.assert_original_finish_refuses(e)

    def test43_postspawn_response_failure_and_cancellation(self):
        import asyncio

        for error in (OSError("PRIVATE_CANARY"), KeyboardInterrupt(), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                e = self.command_executor()
                with patch.object(m.g, "prove"), patch.object(
                    m.selectors, "DefaultSelector", side_effect=error,
                ), self.assertRaises(type(error)):
                    m.capture_command(
                        e, [sys.executable, "-I", "-B", "-c", "pass"], 2, 32, mutating=True,
                    )
                self.assertTrue(e.uncertain)
                self.assertFalse(e.r._pending_groups)
                self.assert_original_finish_refuses(e)

    def test44_mutating_timeout_refuses_finish(self):
        e = self.command_executor()
        with patch.object(m.g, "prove"), self.assertRaises(m.g.Refused):
            m.capture_command(
                e, [sys.executable, "-I", "-B", "-c", "import time; time.sleep(1)"],
                0.03, 32, mutating=True,
            )
        self.assertTrue(e.uncertain)
        self.assertFalse(e.r._pending_groups)
        self.assert_original_finish_refuses(e)

    def test45_mutating_group_uncertainty_even_after_rc0(self):
        for result in (True, OSError("PRIVATE_CANARY")):
            with self.subTest(group_result=type(result).__name__):
                e = self.command_executor()
                pids = []
                original_record = e.r._record_pending

                def record(pid, pids=pids, original_record=original_record):
                    pids.append(pid)
                    original_record(pid)

                e.r._record_pending = record
                try:
                    with patch.object(m.g, "prove"), patch.object(
                        e.r, "_group_exists", **({"side_effect": result} if isinstance(result, OSError)
                                                else {"return_value": result}),
                    ):
                        if isinstance(result, OSError):
                            with self.assertRaises(OSError):
                                m.capture_command(e, [sys.executable, "-I", "-B", "-c", "pass"],
                                                  2, 32, mutating=True)
                        else:
                            self.assertEqual(m.capture_command(
                                e, [sys.executable, "-I", "-B", "-c", "pass"], 2, 32, mutating=True,
                            ), (b"", 0))
                    self.assertTrue(e.uncertain)
                    self.assert_original_finish_refuses(e)
                finally:
                    # Completed synthetic child; failed proof preserves pending bookkeeping.
                    for pid in pids:
                        self.assertFalse(e.r._group_exists(pid))

    def test46_unconfirmed_spawn_failure_is_conservative(self):
        e = self.command_executor()
        with (
            patch.object(m.g, "prove"), patch.object(m.subprocess, "Popen", side_effect=OSError()),
            self.assertRaises(OSError),
        ):
            m.capture_command(e, ["synthetic"], 2, 32, mutating=True)
        self.assertTrue(e.uncertain)
        self.assert_original_finish_refuses(e)

    def test47_proved_authority_rejection_never_submits(self):
        e = self.command_executor()
        with patch.object(m.g, "prove", side_effect=m.g.Refused("refused")), patch.object(
            m.subprocess, "Popen",
        ) as spawn, self.assertRaises(m.g.Refused):
            m.capture_command(e, ["synthetic"], 2, 32, mutating=True)
        self.assertFalse(e.uncertain)
        spawn.assert_not_called()

    def test48_collector_rc2_sticky_receipt(self):
        state, directory, e, calls = self.run_collect(stop_rc=2, partial=True)
        self.assertTrue(e.uncertain)
        self.assertTrue(state["startup_observation"]["daemon_uncertain"])
        self.assertEqual(state["returncode"], 1)
        self.assertEqual(json.loads((directory / "receipt.json").read_text())["stop_returncode"], 2)
        self.assertEqual(len(calls), 3)
        self.assertFalse((directory / "recording.tar").exists())
        self.assert_original_finish_refuses(e)

    def test49_collector_missing_response_is_sticky(self):
        state, directory, e, calls = self.run_collect(stop_rc=None)
        self.assertTrue(e.uncertain)
        self.assertEqual(state["returncode"], 1)
        self.assertEqual(len(calls), 3)
        self.assertTrue(json.loads((directory / "receipt.json").read_text())["daemon_uncertain"])
        self.assert_original_finish_refuses(e)

    def test50_collector_exception_propagates_uncertainty(self):
        state, directory, e, calls = self.run_collect(stop_error=OSError("PRIVATE_CANARY"))
        self.assertTrue(e.uncertain)
        self.assertTrue(state["startup_observation"]["daemon_uncertain"])
        self.assertEqual(state["returncode"], 1)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(state))
        self.assertEqual(len(calls), 3)
        self.assert_original_finish_refuses(e)

    def test51_receipt_failure_cannot_clear_uncertainty(self):
        save = m.save_private

        def fail_receipt(path, raw):
            if path.name == "receipt.json":
                raise OSError("PRIVATE_CANARY")
            return save(path, raw)

        with patch.object(m, "save_private", side_effect=fail_receipt):
            state, directory, e, calls = self.run_collect(stop_rc=1)
        self.assertTrue(e.uncertain)
        self.assertTrue(state["startup_observation"]["daemon_uncertain"])
        self.assertEqual(state["returncode"], 1)
        self.assertFalse((directory / "receipt.json").exists())
        self.assertNotIn("PRIVATE_CANARY", json.dumps(state))
        self.assert_original_finish_refuses(e)

    def test52_confirmed_stop_can_retain_partial_without_promotion(self):
        state, directory, e, calls = self.run_collect(stop_rc=0, partial=True)
        self.assertFalse(e.uncertain)
        self.assertEqual(state["startup_observation"]["status"], "UNAVAILABLE")
        self.assertEqual(state["returncode"], 1)
        self.assertTrue((directory / "recording.tar").is_file())
        self.assertEqual(sum("JFR.stop" in argv for argv in calls), 1)

    def test53_mutating_signal_returncode_is_sticky(self):
        e = self.command_executor()
        with patch.object(m.g, "prove"):
            _, rc = m.capture_command(
                e, [sys.executable, "-I", "-B", "-c",
                    "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"],
                2, 32, mutating=True, require_success=False,
            )
        self.assertEqual(rc, -signal.SIGTERM)
        self.assertTrue(e.uncertain)
        self.assert_original_finish_refuses(e)

    def test54_drain_failure_after_response_is_sticky(self):
        selector_type = m.selectors.DefaultSelector

        class DrainFailure(selector_type):
            def select(self, timeout=None):
                super().select(timeout)
                raise OSError("PRIVATE_CANARY")

        e = self.command_executor()
        with (
            patch.object(m.g, "prove"), patch.object(m.selectors, "DefaultSelector", DrainFailure),
            self.assertRaises(OSError),
        ):
            m.capture_command(
                e, [sys.executable, "-I", "-B", "-c", "print('synthetic')"],
                2, 32, mutating=True,
            )
        self.assertTrue(e.uncertain)
        self.assertFalse(e.r._pending_groups)
        self.assert_original_finish_refuses(e)

    def test55_terminal_selector_exception_is_sticky(self):
        selector_type = m.selectors.DefaultSelector

        class CloseFailure(selector_type):
            def __exit__(self, *args):
                super().__exit__(*args)
                raise OSError("PRIVATE_CANARY")

        e = self.command_executor()
        with (
            patch.object(m.g, "prove"), patch.object(m.selectors, "DefaultSelector", CloseFailure),
            self.assertRaises(OSError),
        ):
            m.capture_command(
                e, [sys.executable, "-I", "-B", "-c", "pass"], 2, 32, mutating=True,
            )
        self.assertTrue(e.uncertain)
        self.assertFalse(e.r._pending_groups)
        self.assert_original_finish_refuses(e)

    def test56_successful_capture_private_modes_and_no_promotion(self):
        import stat

        state, directory, e, calls = self.run_collect()
        self.assertFalse(e.uncertain)
        self.assertEqual(state["returncode"], 1)
        self.assertFalse(state["startup_observation"]["readiness"])
        self.assertEqual(state["startup_observation"]["cause"], "UNDETERMINED")
        receipt = json.loads((directory / "receipt.json").read_text())
        self.assertEqual(receipt["stop_returncode"], 0)
        self.assertFalse(receipt["daemon_uncertain"])
        self.assertEqual(receipt["pending_pgids"], [])
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for name in ("status", "recording.tar", "receipt.json"):
            self.assertEqual(stat.S_IMODE((directory / name).stat().st_mode), 0o600)
        self.assertEqual(len(calls), 4)

    def test57_local_quiesce_exception_is_sticky(self):
        e = self.command_executor()
        original = e.r._quiesce_group
        processes = []

        def quiesce(process, pid):
            processes.append(process)
            original(process, pid)
            raise OSError("PRIVATE_CANARY")

        try:
            with (
                patch.object(m.g, "prove"), patch.object(e.r, "_quiesce_group", side_effect=quiesce),
                self.assertRaises(OSError),
            ):
                m.capture_command(
                    e, [sys.executable, "-I", "-B", "-c", "pass"], 2, 32, mutating=True,
                )
            self.assertTrue(e.uncertain)
            self.assertFalse(e.r._pending_groups)
            self.assert_original_finish_refuses(e)
        finally:
            for process in processes:
                process.stdout.close()
                process.stderr.close()

    def test58_tmpfs_ownership_is_closed(self):
        result = prep.instrument(self.compose, self.root, self.project)
        self.assertEqual(result["services"]["engine"]["tmpfs"], [
            prep.AREA + ":rw,nosuid,nodev,noexec,size=33554432,mode=0700,uid=1000,gid=1000",
        ])
        self.assertNotIn("user", result["services"]["engine"])
        self.assertEqual(self.compose["services"]["engine"]["volumes"], [])

    def test59_existing_fixture_user_and_entrypoint_are_preserved(self):
        for user in ("0:0", "1000:1000", "camunda"):
            with self.subTest(user=user):
                self.compose["services"]["engine"]["user"] = user
                self.compose["services"]["engine"]["entrypoint"] = ["/fixed/public-entrypoint"]
                result = prep.instrument(self.compose, self.root, self.project)
                self.assertEqual(result["services"]["engine"]["user"], user)
                self.assertEqual(result["services"]["engine"]["entrypoint"],
                                 ["/fixed/public-entrypoint"])

    def test60_only_tmpfs_owner_changes_generated_configuration(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "prior_prepare", PACKET / "baseline/prepare_observation.py"
        )
        prior = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prior)
        before = prior.instrument(self.compose, self.root, self.project)
        after = prep.instrument(self.compose, self.root, self.project)
        after["services"]["engine"]["tmpfs"] = before["services"]["engine"]["tmpfs"]
        self.assertEqual(after, before)

    def test61_legacy_tmpfs_is_refused_before_mutating_attach(self):
        state, directory, e, calls = self.run_collect(
            tmpfs_options="rw,nosuid,nodev,noexec,size=33554432,mode=0700",
        )
        self.assertEqual(state["startup_observation"]["status"], "UNAVAILABLE")
        self.assertFalse(e.uncertain)
        self.assertEqual(state["returncode"], 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse((directory / "status").exists())
        self.assertFalse((directory / "recording.tar").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
