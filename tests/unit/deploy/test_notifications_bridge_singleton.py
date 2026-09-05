"""SC-04-b / R-024 (OWNER-DECISIONS-REGISTER): `notificationsBridge.replicaCount: 1` is a
DECLARED singleton, not an accidental default — `singletonByDesignUntil` is the key that makes
that intent machine-checkable instead of prose only a human might read.

Root cause this closes: before this PR, nothing distinguished "we decided this" from "nobody
noticed" — exactly the ambiguity the OWNER-DECISIONS-REGISTER row (R-024) diagnosed as what
would let the next engineer bump the replica count without knowing the producer publishes with
`key=None`-shaped risk (pre-SC-04-a) or, post-SC-04-a (landed in this same tree — see the
`values.yaml` comment), without knowing the remaining blocker is the topic's REAL partition
count on the broker, not code.

GATEKEEPER FINDING F2 (VERIFY-A2-HELM-CAPACITY.md): the original guard read `values.yaml` with
`yaml.safe_load` directly. A per-tenant overlay (`values-staging.yaml` / `values-amh.yaml`) can
set `notificationsBridge.replicaCount: 3` and the bare-file read never notices — `helm template`
merges the override into the RENDERED manifest, which is what actually reaches the cluster. This
module now ALSO asserts `replicas == 1` on `Deployment/notifications-bridge` from a REAL
`helm template` render, for BOTH overlays that render standalone today (bare `values.yaml` does
not render alone at all — `networkPolicy.generalZone.llmEndpoints[0].cidr` and `aurora.endpoint`
are deliberately empty until an overlay fills them in; see SC-05's block in `values.yaml`).

RED proof (reproduced this session, reverted before commit): appending
`notificationsBridge:\\n  replicaCount: 3\\n` to `values-amh.yaml` and re-running
`test_rendered_replicas_stay_one_on_every_real_overlay_while_the_singleton_key_is_set` fails with
`1 != 3` — the exact production-manifest-scales-the-singleton shape the gatekeeper's probe found,
which the OLD (bare-file-only) test stayed green through.
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
_OVERLAYS: tuple[str, ...] = ("values-staging.yaml", "values-amh.yaml")


def _notifications_bridge() -> dict[str, Any]:
    values = yaml.safe_load(_VALUES.read_text(encoding="utf-8"))
    return values["notificationsBridge"]


def test_notifications_bridge_declares_the_singleton_key() -> None:
    """The declaration itself must exist and name the gap it is anchored to."""
    bridge = _notifications_bridge()
    assert bridge.get("singletonByDesignUntil") == "SC-04-a"


def test_replica_count_stays_one_while_the_singleton_key_is_set() -> None:
    """RED proof (revert this file's `values.yaml` companion edit to `replicaCount: 2` — or bump
    it directly — while `singletonByDesignUntil` is still present, and this test fails): the base
    file's OWN declared value must stay 1 while the key is set, independent of any overlay."""
    bridge = _notifications_bridge()
    if bridge.get("singletonByDesignUntil"):
        assert bridge["replicaCount"] == 1, (
            "notificationsBridge.replicaCount > 1 while singletonByDesignUntil is still set — "
            "remove the key (docs/review-queue.md, SC-04) before raising this number"
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


def _notifications_bridge_replicas(docs: list[dict[str, Any]]) -> int:
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == "notifications-bridge":
            return doc["spec"]["replicas"]
    raise AssertionError("Deployment/notifications-bridge not found in rendered output")


def test_bare_values_yaml_does_not_render_standalone() -> None:
    """Documents WHY the bare file is not itself part of the parametrized render check below —
    it genuinely cannot render alone (same constraint SC-05's block cites); a future fix that made
    it render standalone would silently narrow this guard's coverage back to one environment."""
    result = subprocess.run(
        ["helm", "template", "test-release", str(_CHART_DIR)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        "bare values.yaml now renders standalone — add a rendered-chart assertion for it "
        "alongside the two overlays below, this guard would otherwise miss it"
    )


@pytest.mark.parametrize("overlay", _OVERLAYS)
def test_rendered_replicas_stay_one_on_every_real_overlay_while_the_singleton_key_is_set(
    overlay: str,
) -> None:
    """Gatekeeper F2 RED proof target: a per-tenant overlay that raises `replicaCount` to 3 while
    `singletonByDesignUntil` is still set must fail THIS test, on the RENDERED manifest — not just
    on a `yaml.safe_load` of the base file, which never sees the overlay's override."""
    bridge = _notifications_bridge()
    if not bridge.get("singletonByDesignUntil"):
        pytest.skip("singletonByDesignUntil not set on the base file — guard inactive by design")
    docs = _helm_template("-f", str(_CHART_DIR / overlay))
    replicas = _notifications_bridge_replicas(docs)
    assert replicas == 1, (
        f"Deployment/notifications-bridge rendered with {overlay} has replicas={replicas} while "
        "singletonByDesignUntil is still set — remove the key (docs/review-queue.md, SC-04) "
        "before scaling out, in the OVERLAY that set this, not just the base file"
    )
