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

PACKET = Path("/Users/familia/code/maezo-completion-evidence/successor-d7-startup-stage-observation")
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
            self.assertEqual(ast.dump(a), ast.dump(b))

    def test33_byte_pinned_guard_child(self):
        for name in [
            "d7_guard.py",
            "d7_child.py",
            "d7_diagnostics.py",
            "tool-pins.json",
        ]:
            self.assertEqual((PACKET / name).read_bytes(), (PACKET / "baseline" / name).read_bytes())

    def test34_environment_not_inspected(self):
        with patch.dict(os.environ, {"JAVA_TOOL_OPTIONS": "AMBIENT_CANARY"}):
            result = prep.instrument(self.compose, self.root, self.project)
        self.assertNotIn("AMBIENT_CANARY", json.dumps(result))

    def run_collect(self, stop_rc=0, partial=False, uncertain=False):
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
            def __init__(self, *args):
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
                return json.dumps({m.AREA: "rw,nosuid,nodev,noexec,size=33554432,mode=0700"}).encode(), 0
            if "JFR.stop" in argv:
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
        self.assertTrue((directory / "recording.tar").is_file())
        self.assertEqual(state["returncode"], 1)

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
        recording = (PACKET / "synthetic/results/synthetic.jfr").read_bytes()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
