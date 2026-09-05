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
     ORIGINAL probe would exit 127 and restart-loop forever. The probe's embedded path is
     byte-identical to the `A2A_OUTBOX_RELAY_HEARTBEAT_PATH` env var — the single source of truth
     is the template's own `$heartbeatPath` variable, so the two can never drift independently.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

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


def test_liveness_probe_path_matches_the_heartbeat_env_var_exactly() -> None:
    """Single source of truth: both the probe's embedded path and the env var the relay reads come
    from the template's own `$heartbeatPath` — this proves they are byte-identical, not merely
    both plausible-looking, so a future edit to one cannot silently desync from the other."""
    docs = _helm_template()
    deployment = _find(docs, "Deployment", "a2a-outbox-relay")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    heartbeat_env = next(e for e in container["env"] if e["name"] == "A2A_OUTBOX_RELAY_HEARTBEAT_PATH")
    probe_script = container["livenessProbe"]["exec"]["command"][2]
    assert heartbeat_env["value"] in probe_script
    assert heartbeat_env["value"].startswith("/tmp/")  # writable under readOnlyRootFilesystem + emptyDir


def test_liveness_probe_staleness_threshold_is_three_times_the_poll_interval() -> None:
    docs = _helm_template("--set", "a2aOutboxRelay.pollIntervalS=5")
    deployment = _find(docs, "Deployment", "a2a-outbox-relay")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    probe_script = container["livenessProbe"]["exec"]["command"][2]
    assert "< 15" in probe_script  # 3 * 5
