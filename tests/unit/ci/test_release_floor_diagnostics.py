"""Diagnostic retention controls: synthetic measurements, never the full unit suite."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from scripts.ci import generate_release_floor as floor


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "source.txt").write_text("selected source\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    return root


def measurement(monkeypatch, *, returncode=1):
    stdout = "synthetic traceback private to artifact\n"
    stderr = "3 failed, 5 passed\n" if returncode else "5 passed\n"
    calls = []

    def run(root, python):
        calls.append((root, python))
        return subprocess.CompletedProcess(
            [python, "-m", "pytest", "tests/", "-q"], returncode, stdout, stderr
        )

    monkeypatch.setattr(floor, "_run_unit_measurement", run)
    return stdout, stderr, calls


def test_red_streams_metadata_and_private_modes_are_retained(repository, tmp_path, monkeypatch, capsys):
    stdout, stderr, calls = measurement(monkeypatch)
    target = tmp_path / "evidence"
    counts, rc, raw = floor.measure_unit_tests(repository, sys.executable, evidence_dir=target)
    assert counts == {"failed": 3, "passed": 5} and rc == 1 and raw == stderr.strip()
    assert calls == [(repository, sys.executable)]
    assert (target / "stdout.txt").read_text() == stdout
    assert (target / "stderr.txt").read_text() == stderr
    assert target.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in target.iterdir())
    evidence = json.loads((target / "measurement.json").read_text())
    assert evidence["argv"] == [sys.executable, "-m", "pytest", "tests/", "-q"]
    assert evidence["returncode"] == 1 and not evidence["timed_out"] and evidence["capture_complete"]
    assert evidence["source_before"] == evidence["source_after"]
    source = evidence["source_before"]
    assert (
        source["head"]
        == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository).decode().strip()
    )
    assert source["tracked_checkout_clean"] is True
    assert str(Path(floor.__file__).resolve()) in source["files"]
    assert "environment" not in evidence
    assert "synthetic traceback" not in capsys.readouterr().out


def test_dirty_source_is_disclosed_without_claiming_head_covers_it(repository, tmp_path, monkeypatch):
    measurement(monkeypatch)
    (repository / "source.txt").write_text("changed source\n")
    target = tmp_path / "evidence"
    floor.measure_unit_tests(repository, sys.executable, evidence_dir=target)
    assert (
        json.loads((target / "measurement.json").read_text())["source_before"]["tracked_checkout_clean"]
        is False
    )


@pytest.mark.parametrize("kind", ["inside", "existing", "symlink", "relative"])
def test_unsafe_destination_is_refused_before_measurement(repository, tmp_path, monkeypatch, kind):
    _, _, calls = measurement(monkeypatch)
    target = tmp_path / "evidence"
    if kind == "inside":
        target = repository / "evidence"
    elif kind == "existing":
        target.mkdir()
        (target / "keep").write_text("preserve")
    elif kind == "symlink":
        parent = tmp_path / "real"
        parent.mkdir()
        (tmp_path / "alias").symlink_to(parent, target_is_directory=True)
        target = tmp_path / "alias/evidence"
    else:
        target = Path("relative-evidence")
    with pytest.raises(floor.UnitEvidenceError):
        floor.measure_unit_tests(repository, sys.executable, evidence_dir=target)
    assert not calls
    if kind == "existing":
        assert (target / "keep").read_text() == "preserve"


def test_timeout_retains_partial_streams_and_original_timeout_contract(repository, tmp_path, monkeypatch):
    def timeout(*args):
        raise subprocess.TimeoutExpired(
            "unchanged unit argv", 7200, output=b"partial out", stderr=b"partial err"
        )

    monkeypatch.setattr(floor, "_run_unit_measurement", timeout)
    target = tmp_path / "evidence"
    counts, rc, raw = floor.measure_unit_tests(repository, sys.executable, evidence_dir=target)
    assert counts == {} and rc == -1 and raw.startswith("TIMEOUT after 7200s")
    assert (target / "stdout.txt").read_bytes() == b"partial out"
    assert (target / "stderr.txt").read_bytes() == b"partial err"
    evidence = json.loads((target / "measurement.json").read_text())
    assert evidence["timed_out"] and not evidence["capture_complete"]
    assert evidence["returncode"] is None and evidence["exception_type"] == "TimeoutExpired"


@pytest.mark.parametrize("kind", ["red", "timeout", "interrupt", "green"])
def test_write_failure_preserves_primary_failure_and_never_creates_green(
    repository, tmp_path, monkeypatch, capsys, kind
):
    measurement(monkeypatch, returncode=0 if kind == "green" else 1)

    def broken_writer(*args):
        raise OSError("sensitive failure detail must not be printed")

    monkeypatch.setattr(floor, "_write_unit_evidence", broken_writer)
    if kind in {"timeout", "interrupt"}:

        def interrupted(*args):
            if kind == "timeout":
                raise subprocess.TimeoutExpired("argv", 7200)
            raise KeyboardInterrupt

        monkeypatch.setattr(floor, "_run_unit_measurement", interrupted)
    target = tmp_path / "evidence"
    if kind == "green":
        with pytest.raises(floor.UnitEvidenceError):
            floor.measure_unit_tests(repository, sys.executable, evidence_dir=target)
    elif kind == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            floor.measure_unit_tests(repository, sys.executable, evidence_dir=target)
    else:
        counts, rc, raw = floor.measure_unit_tests(repository, sys.executable, evidence_dir=target)
        assert rc == (-1 if kind == "timeout" else 1)
        assert counts == ({} if kind == "timeout" else {"failed": 3, "passed": 5})
    assert "sensitive failure detail" not in capsys.readouterr().err
    assert not (target / "measurement.json").exists()


def test_absent_option_preserves_two_argument_measurement_and_does_no_diagnostic_io(tmp_path, monkeypatch):
    _, _, calls = measurement(monkeypatch, returncode=0)
    monkeypatch.setattr(
        floor, "_unit_source_identity", lambda *args: pytest.fail("diagnostic IO without option")
    )
    assert floor.measure_unit_tests(tmp_path, sys.executable) == ({"passed": 5}, 0, "5 passed")
    assert calls == [(tmp_path, sys.executable)]
    assert not list(tmp_path.iterdir())


def test_workflow_requests_only_diagnostics_and_uploads_with_short_retention():
    root = Path(__file__).resolve().parents[3]
    job = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]["release-floor"]
    step = next(step for step in job["steps"] if "Release-capability floor gate" in step.get("name", ""))
    assert "scripts/ci/generate_release_floor.py --check" in step["run"]
    assert '--unit-evidence-dir "$RUNNER_TEMP/release-floor-unit-evidence"' in step["run"]
    assert "PYTEST_ADDOPTS" not in step["run"]
    upload = next(
        step for step in job["steps"] if step.get("name") == "Upload release-floor unit diagnostics"
    )
    assert upload["if"] == "always()" and upload["with"]["retention-days"] == 7
    assert upload["with"]["if-no-files-found"] == "error"


def test_real_stdlib_child_capture_keeps_selector_and_owned_cleanup(repository, tmp_path):
    from tests.support.measurement_python import measurement_python

    (repository / "pytest.py").write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path('observed.json').write_text(json.dumps({'argv':sys.argv[1:], 'pgid':os.getpgrp()}))\n"
        "assert sys.stdin.read() == ''\n"
        "print('synthetic failure traceback', file=sys.stderr)\n"
        "print('1 failed, 2 passed', file=sys.stderr)\n"
        "raise SystemExit(1)\n"
    )
    target = tmp_path / "evidence"
    counts, rc, _ = floor.measure_unit_tests(repository, measurement_python(tmp_path), evidence_dir=target)
    observed = json.loads((repository / "observed.json").read_text())
    assert observed["argv"] == ["tests/", "-q"]
    assert counts == {"failed": 1, "passed": 2} and rc == 1
    assert not floor.process_groups._group_exists(observed["pgid"])
    assert observed["pgid"] not in floor.process_groups._pending_groups
    assert "synthetic failure traceback" in (target / "stderr.txt").read_text()


@pytest.mark.parametrize("requested", [False, True])
def test_cli_keeps_legacy_measurement_interface_or_fails_diagnostics_separately(
    repository, tmp_path, monkeypatch, requested
):
    census = tmp_path / "census.json"
    census.write_text("{}")
    target = tmp_path / "evidence"
    monkeypatch.setattr(floor, "measure_fences", lambda root, python: [])
    if requested:

        def measure(root, python, *, evidence_dir):
            assert evidence_dir == target
            raise floor.UnitEvidenceError("synthetic evidence failure")
    else:

        def measure(root, python):
            return {"failed": 1}, 1, "1 failed"

    monkeypatch.setattr(floor, "measure_unit_tests", measure)
    args = ["--write", "--repo-root", str(repository), "--census-path", str(census)]
    if requested:
        args += ["--unit-evidence-dir", str(target)]
    assert floor.main(args) == 1
    assert not (repository / floor.DEFAULT_FLOOR_PATH).exists()
