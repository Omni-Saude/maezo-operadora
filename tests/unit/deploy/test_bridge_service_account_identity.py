"""R-198 / NETBRIDGE-SERVICE-PRINCIPAL-IDENTITY (OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-
HUMANA, decisor dono(arquitetura)+seguranca): the network-change-bridge and the notifications-
bridge must run under DISTINCT service principals — least privilege, and what makes the audit
trail of two daemons that can both call `mcp-cibseven.start_process` ATTRIBUTABLE per bridge.

Root cause this closes: before this PR, `deployment-bridge-netchange.yaml` rendered with
`serviceAccountName: {{ include "maezo-tenant.serviceAccountName" . }}` — the SAME shared/legacy
SA the notifications-bridge (and gateway/fhir-sync/webhook-receiver) already use. Two daemons with
start-process powers would appear in `kubectl auth`/CloudTrail-style audit logs under one identity,
so "which bridge did this" could not be answered from the principal alone.

This module proves the FIX on a REAL `helm template` render (never a bare `yaml.safe_load` of
`values.yaml`, which would not catch an overlay silently reintroducing the shared SA) — same
discipline as `test_notifications_bridge_singleton.py`'s gatekeeper-finding F2 lesson.

RED proof (reproduced this session, reverted before commit): reverting
`deployment-bridge-netchange.yaml`'s `serviceAccountName` back to
`{{ include "maezo-tenant.serviceAccountName" . }}` makes
`test_the_two_bridges_render_with_different_service_account_names` fail with
`'maezo-agent' == 'maezo-agent'` — the exact shared-identity shape R-198 closes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant"
_VALUES = _CHART_DIR / "values.yaml"


def _networkchange_bridge_values() -> dict[str, Any]:
    values = yaml.safe_load(_VALUES.read_text(encoding="utf-8"))
    return values["networkChangeBridge"]


def test_network_change_bridge_declares_its_own_service_account_block() -> None:
    """The declaration itself must exist, distinct from the shared `serviceAccount` block."""
    bridge = _networkchange_bridge_values()
    sa = bridge.get("serviceAccount")
    assert sa is not None, "networkChangeBridge.serviceAccount is missing"
    assert sa.get("create") is True
    name = sa.get("name")
    assert name and name != "maezo-agent", (
        f"networkChangeBridge.serviceAccount.name={name!r} must not be the shared SA's name"
    )


def _helm_template(*extra_args: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR), *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed: {result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def _deployment_service_account(docs: list[dict[str, Any]], name: str) -> str:
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == name:
            return doc["spec"]["template"]["spec"]["serviceAccountName"]
    raise AssertionError(f"Deployment/{name} not found in rendered output")


def _service_account_names(docs: list[dict[str, Any]]) -> set[str]:
    return {doc["metadata"]["name"] for doc in docs if doc.get("kind") == "ServiceAccount"}


@pytest.mark.parametrize("overlay", ("values-staging.yaml", "values-amh.yaml"))
def test_the_two_bridges_render_with_different_service_account_names(overlay: str) -> None:
    """The R-198 RED-proof target: both bridges rendered (network-change-bridge forced on, since
    it is `enabled: false` by default — AF-01/R-001, the module does not exist in `src/` yet) must
    carry DIFFERENT `spec.template.spec.serviceAccountName` values."""
    docs = _helm_template("-f", str(_CHART_DIR / overlay), "--set", "networkChangeBridge.enabled=true")
    notifications_sa = _deployment_service_account(docs, "notifications-bridge")
    netchange_sa = _deployment_service_account(docs, "network-change-bridge")
    assert notifications_sa != netchange_sa, (
        f"notifications-bridge and network-change-bridge share the same ServiceAccount "
        f"({notifications_sa!r}) — R-198 requires DISTINCT principals per bridge"
    )
    assert netchange_sa == "maezo-network-change-bridge"


@pytest.mark.parametrize("overlay", ("values-staging.yaml", "values-amh.yaml"))
def test_the_network_change_bridge_service_account_actually_renders(overlay: str) -> None:
    """The `serviceAccountName` a Deployment references must correspond to a REAL rendered
    ServiceAccount object — a dangling reference (typo, or the SA template's own gate diverging
    from the Deployment's gate) would not be caught by comparing strings alone."""
    docs = _helm_template("-f", str(_CHART_DIR / overlay), "--set", "networkChangeBridge.enabled=true")
    sa_names = _service_account_names(docs)
    netchange_sa = _deployment_service_account(docs, "network-change-bridge")
    assert netchange_sa in sa_names, (
        f"Deployment/network-change-bridge references ServiceAccount {netchange_sa!r}, which does "
        f"not render (rendered SAs: {sorted(sa_names)})"
    )


def test_network_change_bridge_service_account_keeps_automount_disabled() -> None:
    """Least privilege carries over: the new SA must not automount its token (the daemon never
    calls the Kubernetes API — the identity exists only for the IRSA role-arn allowlist)."""
    docs = _helm_template(
        "-f", str(_CHART_DIR / "values-amh.yaml"), "--set", "networkChangeBridge.enabled=true"
    )
    for doc in docs:
        if doc.get("kind") == "ServiceAccount" and doc["metadata"]["name"] == "maezo-network-change-bridge":
            assert doc.get("automountServiceAccountToken") is False
            return
    raise AssertionError("ServiceAccount/maezo-network-change-bridge not found in rendered output")
