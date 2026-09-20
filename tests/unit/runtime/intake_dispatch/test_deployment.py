"""The intake-dispatch Deployment, against the REAL rendered chart (no mock).

Proves:
  1. the template is fully gated OFF by default — nothing renders, so enabling the drain of a
     real admission stays a deployment decision;
  2. when enabled it invokes `python -m maezo.runtime.intake_dispatch` (loop mode, no `--once`)
     and that module actually RESOLVES — the `check-helm-entrypoints` fence only covers what the
     chart renders with default values, and this one deliberately renders nothing there, so the
     phantom-module check has to be made here or not at all;
  3. every setting the daemon refuses to start without is declared in the Deployment;
  4. the livenessProbe's staleness threshold is the SAME function the loop's heartbeat obeys —
     proven by EXECUTING the rendered script against missing/fresh/stale heartbeat files, because
     a probe that can never pass restart-loops the pod forever (the sibling relay's finding G1);
  5. the protected materials are mounted read-only with no group/other permission bits, which is
     what `protected_bytes` requires of every file it will read.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.runtime.intake_dispatch.__main__ import heartbeat_stale_after_s

_REPO_ROOT = Path(__file__).resolve().parents[4]  # tests/unit/runtime/intake_dispatch -> root
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_VALUES_AMH = _CHART_DIR / "values-amh.yaml"
_ENABLE = ("--set", "intakeDispatch.enabled=true")


def _helm_template(*extra: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR), "-f", str(_VALUES_AMH), *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def _deployment(*extra: str) -> dict[str, Any] | None:
    for doc in _helm_template(*extra):
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "intake-dispatch":
            return doc
    return None


def _container(deployment: dict[str, Any]) -> dict[str, Any]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    return containers[0]


def _env(container: dict[str, Any]) -> dict[str, Any]:
    return {entry["name"]: entry for entry in container["env"]}


def test_nothing_renders_by_default_so_draining_stays_a_deployment_decision() -> None:
    assert _deployment() is None


def test_when_enabled_it_runs_the_daemon_module_in_loop_mode() -> None:
    container = _container(_deployment(*_ENABLE) or {})
    assert container["command"] == ["python", "-m", "maezo.runtime.intake_dispatch"]
    assert "--once" not in container["command"]


def test_the_module_the_chart_names_is_a_real_importable_module() -> None:
    module = "maezo.runtime.intake_dispatch"
    container = _container(_deployment(*_ENABLE) or {})
    assert container["command"][2] == module
    assert importlib.util.find_spec(module) is not None


def test_every_setting_the_daemon_refuses_to_start_without_is_declared() -> None:
    env = _env(_container(_deployment(*_ENABLE) or {}))
    for name in (
        "MAEZO_INTAKE_DISPATCH_TENANT",
        "MAEZO_INTAKE_DISPATCH_LIFECYCLE_PATH",
        "MAEZO_INTAKE_DISPATCH_IDENTITY_WRITER_URL",
        "MAEZO_INTAKE_DISPATCH_DEFINITION_PATH",
        "MAEZO_INTAKE_DISPATCH_DEFINITION_DIGEST",
    ):
        assert name in env, name
    # The two that carry material go through a Secret, never a chart literal.
    for name in ("MAEZO_INTAKE_DISPATCH_IDENTITY_WRITER_URL", "MAEZO_INTAKE_DISPATCH_DEFINITION_DIGEST"):
        assert "secretKeyRef" in env[name]["valueFrom"]


def test_the_probe_reads_its_own_environment_rather_than_a_helm_literal() -> None:
    container = _container(_deployment(*_ENABLE) or {})
    script = container["livenessProbe"]["exec"]["command"][2]
    assert "MAEZO_INTAKE_DISPATCH_HEARTBEAT_PATH" in script
    assert "MAEZO_INTAKE_DISPATCH_POLL_INTERVAL_S" in script
    assert "MAEZO_INTAKE_DISPATCH_ROW_BUDGET_S" in script
    # No Helm arithmetic: a truncated threshold is the failure this formula exists to avoid.
    assert "{{" not in script and "mul" not in script


def test_the_row_budget_the_probe_declares_is_rendered_into_the_container_env() -> None:
    """The threshold covers ONE row's worst case, so the budget must travel with the container."""
    container = _container(_deployment(*_ENABLE) or {})
    env = _env(container)
    assert float(env["MAEZO_INTAKE_DISPATCH_ROW_BUDGET_S"]["value"]) > 0
    # The floor + budget threshold, evaluated in Python exactly as `heartbeat_stale_after_s` does.
    poll = float(env["MAEZO_INTAKE_DISPATCH_POLL_INTERVAL_S"]["value"])
    budget = float(env["MAEZO_INTAKE_DISPATCH_ROW_BUDGET_S"]["value"])
    assert heartbeat_stale_after_s(poll, budget) == max(3.0 * poll, 5.0) + budget


@pytest.mark.parametrize("poll", ["0.5", "1", "2", "5"])
def test_the_rendered_probe_computes_the_same_threshold_as_the_loop(tmp_path, poll: str) -> None:
    container = _container(_deployment(*_ENABLE) or {})
    script = container["livenessProbe"]["exec"]["command"][2]
    budget = _env(container)["MAEZO_INTAKE_DISPATCH_ROW_BUDGET_S"]["value"]
    heartbeat = tmp_path / "heartbeat"
    threshold = heartbeat_stale_after_s(float(poll), float(budget))

    def run() -> int:
        return subprocess.run(
            [sys.executable, "-c", script],
            env={
                **os.environ,
                "MAEZO_INTAKE_DISPATCH_HEARTBEAT_PATH": str(heartbeat),
                "MAEZO_INTAKE_DISPATCH_POLL_INTERVAL_S": poll,
                "MAEZO_INTAKE_DISPATCH_ROW_BUDGET_S": budget,
            },
            timeout=30,
        ).returncode

    assert run() == 1, "a missing heartbeat must never look alive"
    heartbeat.touch()
    assert run() == 0, "a fresh heartbeat is alive for every legitimate poll interval"
    stale = time.time() - (threshold + 1)
    os.utime(heartbeat, (stale, stale))
    assert run() == 1, "a heartbeat older than the threshold is dead"


def test_the_protected_materials_are_mounted_read_only_with_no_group_or_other_bits() -> None:
    deployment = _deployment(*_ENABLE) or {}
    container = _container(deployment)
    mount = next(m for m in container["volumeMounts"] if m["name"] == "auth-materials")
    assert mount["readOnly"] is True
    volume = next(
        v for v in deployment["spec"]["template"]["spec"]["volumes"] if v["name"] == "auth-materials"
    )
    mode = volume["secret"]["defaultMode"]
    assert mode & 0o077 == 0, oct(mode)
    assert {item["path"] for item in volume["secret"]["items"]} == {"lifecycle.json", "definition.json"}


def test_the_pod_is_declared_outside_the_phi_zone_and_drops_every_capability() -> None:
    deployment = _deployment(*_ENABLE) or {}
    assert deployment["spec"]["template"]["metadata"]["labels"]["maezo.io/phi-zone"] == "false"
    security = _container(deployment)["securityContext"]
    assert security["capabilities"]["drop"] == ["ALL"]
    assert security["readOnlyRootFilesystem"] is True
    assert security["allowPrivilegeEscalation"] is False


def test_enabling_the_daemon_puts_it_in_the_daemon_egress_policy() -> None:
    for doc in _helm_template(*_ENABLE):
        if doc.get("kind") == "NetworkPolicy" and "intake-dispatch" in yaml.safe_dump(doc):
            # ... and the operator-facing annotation names it too: a stale member list is exactly
            # what an auditor sharing this grant would read instead of the selector.
            assert "intake-dispatch" in doc["metadata"]["annotations"]["maezo.io/note"]
            return
    raise AssertionError("intake-dispatch is not a member of any NetworkPolicy component selector")
