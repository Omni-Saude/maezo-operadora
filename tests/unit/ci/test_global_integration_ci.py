"""Behavioral fence for the unit/integration marker partition in CI."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _steps(job: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    return list(workflow["jobs"][job]["steps"])


def _step(job: str, name: str) -> dict[str, Any]:
    return next(step for step in _steps(job) if step.get("name") == name)


def _selector(job: str, step_name: str) -> tuple[str, str]:
    logical_commands = str(_step(job, step_name)["run"]).replace("\\\n", " ")
    for line in logical_commands.splitlines():
        tokens = shlex.split(line.split("|", 1)[0])
        try:
            pytest_at = tokens.index("pytest")
        except ValueError:
            continue
        pytest_args = tokens[pytest_at + 1 :]
        roots = [token for token in pytest_args if not token.startswith("-")]
        marker_at = pytest_args.index("-m")
        assert roots, f"no collection root in {line!r}"
        return roots[0], pytest_args[marker_at + 1]
    raise AssertionError(f"no pytest selector found in {job}/{step_name}")


def _collect(root: str, marker: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", root, "--collect-only", "-q", "-m", marker],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line for line in result.stdout.splitlines() if "::" in line}


def test_quality_lane_collects_no_integration_case() -> None:
    unit_root, unit_marker = _selector("quality", "Unit tests + coverage gate (>=85%)")
    integration_cases = _collect("tests", "integration")
    unit_cases = _collect(unit_root, unit_marker)
    assert integration_cases
    assert unit_cases
    assert unit_cases.isdisjoint(integration_cases)


def test_service_lanes_are_a_disjoint_complete_union_of_global_integration_cases() -> None:
    normal_root, normal_marker = _selector("integration", "Integration tests")
    chaos_root, chaos_marker = _selector("chaos", "Chaos tests (seam-level fault injection)")
    all_integration = _collect("tests", "integration")
    normal = _collect(normal_root, normal_marker)
    chaos = _collect(chaos_root, chaos_marker)

    assert normal
    assert chaos
    assert normal.isdisjoint(chaos)
    assert normal | chaos == all_integration

    outside_old_path = {case for case in all_integration if not case.startswith("tests/integration/")}
    assert outside_old_path, (
        "the regression proof became vacuous: no integration cases live outside the old path"
    )


def test_live_lane_publishes_junit_collection_and_checks_infrastructure_skips() -> None:
    run = str(_step("integration", "Integration tests")["run"])
    assert "--junitxml=integration-junit.xml" in run
    logical_run = " ".join(run.replace("\\\n", " ").split())
    assert "check_live_junit.py integration-junit.xml --collection integration-collection.txt" in logical_run
    assert "-ra" in run

    guard = str(_step("integration", "Check for collectable integration tests")["run"])
    assert "integration-collection.txt" in guard
    upload = _step("integration", "Upload integration test results")
    uploaded = str(upload["with"]["path"])
    assert "integration-junit.xml" in uploaded
    assert "integration-collection.txt" in uploaded

    chaos_run = " ".join(
        str(_step("chaos", "Chaos tests (seam-level fault injection)")["run"]).replace("\\\n", " ").split()
    )
    assert "--junitxml=chaos-junit.xml" in chaos_run
    assert "check_live_junit.py chaos-junit.xml --collection chaos-collection.txt" in chaos_run
    chaos_upload = str(_step("chaos", "Upload chaos test results")["with"]["path"])
    assert "chaos-junit.xml" in chaos_upload
    assert "chaos-collection.txt" in chaos_upload
