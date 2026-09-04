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
