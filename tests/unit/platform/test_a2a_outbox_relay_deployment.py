"""Unit tests for the A2A outbox-relay Deployment (SC-01 / R-004).

Proves, against the REAL rendered chart (no mock):
  1. `deployment-a2a-outbox-relay.yaml` renders a Deployment invoking
     `python -m maezo.a2a.outbox_relay` (no `--once` — loop mode) when `a2aOutboxRelay.enabled`
     (default: `true`).
  2. It gets both `DATABASE_URL` and `KAFKA_BOOTSTRAP_SERVERS` — `build_relay` in
     `outbox_relay.py` refuses to construct without either.
  3. Its egress mirrors `deployment-bridge.yaml` (notifications-bridge): both are members of the
     SAME `wr2-daemon-egress` NetworkPolicy component selector.
  4. The template is fully gated OFF (renders nothing) when `a2aOutboxRelay.enabled=false`.
  5. (SC-01/F3, gatekeeper repair) The livenessProbe uses the heartbeat file, NOT `pgrep` — the
     runtime image (`python:3.12-slim` + `libpq5`, no `procps`) does not have `pgrep`, so the
     ORIGINAL probe would exit 127 and restart-loop forever. The probe reads
     `A2A_OUTBOX_RELAY_HEARTBEAT_PATH` from its OWN environment (the same env var the container
     sets) rather than a Helm-interpolated literal, so the two can never drift independently.
  6. (gatekeeper finding G1, §Delta) The probe's staleness threshold is computed IN PYTHON from
     `A2A_OUTBOX_RELAY_POLL_INTERVAL_S`, never via Helm's `mul`/`int` (which truncates a legitimate
     sub-second `pollIntervalS` to a threshold of `0`, failing the probe forever) — proven both by
     inspecting the rendered script and by actually EXECUTING it for `pollIntervalS` in
     `{0.5, 1, 2, 5}` against missing/fresh/stale heartbeat files.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/unit/platform -> repo root
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_VALUES_AMH = _CHART_DIR / "values-amh.yaml"


def _helm_template(*extra_args: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR), "-f", str(_VALUES_AMH), *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def _find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(f"{kind}/{name} not found in rendered output")


def test_renders_by_default_invoking_the_relay_in_loop_mode() -> None:
    docs = _helm_template()
    deployment = _find(docs, "Deployment", "a2a-outbox-relay")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "-m", "maezo.a2a.outbox_relay"]  # no --once: loop mode


def test_gets_both_env_vars_build_relay_requires() -> None:
    docs = _helm_template()
    deployment = _find(docs, "Deployment", "a2a-outbox-relay")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_names = {e["name"] for e in container["env"]}
    assert "DATABASE_URL" in env_names
    assert "KAFKA_BOOTSTRAP_SERVERS" in env_names
    database_url_env = next(e for e in container["env"] if e["name"] == "DATABASE_URL")
    assert database_url_env["valueFrom"]["secretKeyRef"]["key"] == "database-url"


def test_egress_mirrors_notifications_bridge_same_daemon_egress_policy() -> None:
    docs = _helm_template()
    policy = _find(docs, "NetworkPolicy", "wr2-daemon-egress")
    values = policy["spec"]["podSelector"]["matchExpressions"][0]["values"]
    assert "a2a-outbox-relay" in values
    assert "notifications-bridge" in values  # same policy, same component list — literal mirror


def test_disabled_by_flag_renders_nothing() -> None:
    docs = _helm_template("--set", "a2aOutboxRelay.enabled=false")
    names = {doc.get("metadata", {}).get("name") for doc in docs}
    assert "a2a-outbox-relay" not in names
    policy = _find(docs, "NetworkPolicy", "wr2-daemon-egress")
    values = policy["spec"]["podSelector"]["matchExpressions"][0]["values"]
    assert "a2a-outbox-relay" not in values


# ---------------------------------------------------------------------------
# 5. livenessProbe (SC-01/F3) — heartbeat, not pgrep
# ---------------------------------------------------------------------------


def test_liveness_probe_does_not_use_pgrep() -> None:
    """The original probe (`pgrep -f maezo.a2a.outbox_relay > /dev/null`) would exit 127 on this
    runtime image (no `procps`) and restart-loop the pod forever — the mutation proof: reintroduce
    `pgrep` anywhere in the probe command and this test goes RED."""
    docs = _helm_template()
    deployment = _find(docs, "Deployment", "a2a-outbox-relay")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    probe_command = container["livenessProbe"]["exec"]["command"]
    assert not any("pgrep" in str(token) for token in probe_command), probe_command


def test_liveness_probe_checks_the_heartbeat_file_freshness_via_python() -> None:
    docs = _helm_template()
    deployment = _find(docs, "Deployment", "a2a-outbox-relay")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    probe_command = container["livenessProbe"]["exec"]["command"]
    assert probe_command[0] == "python"
    assert probe_command[1] == "-c"
    script = probe_command[2]
    assert "st_mtime" in script
    assert "os.path.exists" in script


def test_liveness_probe_reads_the_heartbeat_path_from_its_own_environment() -> None:
    """Gatekeeper finding G1 (§Delta): the probe must not embed a Helm-interpolated literal path —
    it reads `A2A_OUTBOX_RELAY_HEARTBEAT_PATH` from `os.environ` at exec time, the SAME variable
    the container's `env:` block sets, so there is exactly one source of truth and no template
    value can desync from what the probe actually checks."""
    docs = _helm_template()
    deployment = _find(docs, "Deployment", "a2a-outbox-relay")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    heartbeat_env = next(e for e in container["env"] if e["name"] == "A2A_OUTBOX_RELAY_HEARTBEAT_PATH")
    assert heartbeat_env["value"].startswith("/tmp/")  # writable under readOnlyRootFilesystem + emptyDir
    probe_script = container["livenessProbe"]["exec"]["command"][2]
    assert "os.environ.get('A2A_OUTBOX_RELAY_HEARTBEAT_PATH'" in probe_script
    # No Helm-rendered literal path baked into the script — only the env-var lookup above.
    assert heartbeat_env["value"] not in probe_script


def test_liveness_probe_computes_its_staleness_threshold_in_python_not_helm() -> None:
    """Gatekeeper finding G1 (§Delta): the threshold used to be `{{ mul (pollIntervalS | int) 3 }}`
    — Helm's `int` truncates a sub-second `pollIntervalS` (a legitimate float) to `0`, making the
    probe fail on every invocation. The probe script must instead read
    `A2A_OUTBOX_RELAY_POLL_INTERVAL_S` from its own environment and compute
    `max(3.0*poll, 5.0)` itself — proven by rendering with several `pollIntervalS` values and
    confirming the SCRIPT TEXT is byte-identical every time (all arithmetic moved into Python)."""
    scripts = set()
    for poll_interval_s in ("0.5", "1", "2", "5"):
        docs = _helm_template("--set", f"a2aOutboxRelay.pollIntervalS={poll_interval_s}")
        deployment = _find(docs, "Deployment", "a2a-outbox-relay")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        script = container["livenessProbe"]["exec"]["command"][2]
        assert "mul" not in script
        assert "os.environ.get('A2A_OUTBOX_RELAY_POLL_INTERVAL_S'" in script
        assert "max(3.0*poll,5.0)" in script or "max(3.0 * poll, 5.0)" in script
        scripts.add(script)
    assert len(scripts) == 1, "the probe script must not vary with pollIntervalS — it reads it at runtime"


@pytest.mark.parametrize("poll_interval_s", ["0.5", "1", "2", "5"])
def test_rendered_probe_command_runs_correctly_for_every_poll_interval(
    poll_interval_s: str, tmp_path
) -> None:
    """G1's own reproduction, executed for real: extract the exact rendered `python -c` script and
    RUN it (no docker needed — it is pure stdlib, verified separately inside
    `python:3.12-slim` itself) with the env vars a real pod would set, against a missing, fresh,
    and stale heartbeat file. Before the fix, `pollIntervalS=0.5` failed even on the FRESH file
    (threshold truncated to 0) — the exact permanent-restart-loop regression."""
    docs = _helm_template("--set", f"a2aOutboxRelay.pollIntervalS={poll_interval_s}")
    deployment = _find(docs, "Deployment", "a2a-outbox-relay")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    probe_command = container["livenessProbe"]["exec"]["command"]
    assert probe_command[:2] == ["python", "-c"]
    assert len(probe_command) == 3
    host_command = [sys.executable, *probe_command[1:]]
    assert host_command[1:] == probe_command[1:]
    heartbeat_path = str(tmp_path / "heartbeat")
    env = {
        **os.environ,
        "A2A_OUTBOX_RELAY_HEARTBEAT_PATH": heartbeat_path,
        "A2A_OUTBOX_RELAY_POLL_INTERVAL_S": poll_interval_s,
    }

    missing = subprocess.run(host_command, env=env, timeout=10)
    assert missing.returncode != 0, "no heartbeat file yet -> must fail (no sweep has completed)"

    Path(heartbeat_path).touch()
    fresh = subprocess.run(host_command, env=env, timeout=10)
    assert fresh.returncode == 0, f"fresh heartbeat must pass at pollIntervalS={poll_interval_s}"

    old_mtime = time.time() - 3600
    os.utime(heartbeat_path, (old_mtime, old_mtime))
    stale = subprocess.run(host_command, env=env, timeout=10)
    assert stale.returncode != 0, "an hour-old heartbeat must fail regardless of poll interval"
