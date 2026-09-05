"""SC-04-b / R-024 (OWNER-DECISIONS-REGISTER): `notificationsBridge.replicaCount: 1` is a
DECLARED singleton, not an accidental default — `singletonByDesignUntil` is the key that makes
that intent machine-checkable instead of prose only a human might read.

Root cause this closes: before this PR, nothing distinguished "we decided this" from "nobody
noticed" — exactly the ambiguity the OWNER-DECISIONS-REGISTER row (R-024) diagnosed as what
would let the next engineer bump the replica count without knowing the producer publishes with
`key=None`-shaped risk (pre-SC-04-a) or, post-SC-04-a (landed in this same tree — see the
`values.yaml` comment), without knowing the remaining blocker is the topic's REAL partition
count on the broker, not code.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_VALUES = Path(__file__).resolve().parents[3] / "deploy" / "helm" / "maezo-tenant" / "values.yaml"


def _notifications_bridge() -> dict:
    values = yaml.safe_load(_VALUES.read_text(encoding="utf-8"))
    return values["notificationsBridge"]


def test_notifications_bridge_declares_the_singleton_key() -> None:
    """The declaration itself must exist and name the gap it is anchored to."""
    bridge = _notifications_bridge()
    assert bridge.get("singletonByDesignUntil") == "SC-04-a"


def test_replica_count_stays_one_while_the_singleton_key_is_set() -> None:
    """RED proof (revert this file's `values.yaml` companion edit to `replicaCount: 2` — or bump
    it directly — while `singletonByDesignUntil` is still present, and this test fails): the key
    is a live guard against scaling out before the SC-04 partition-count confirmation
    (docs/review-queue.md, section "SC-04 — escala do notifications-bridge") lands, not decoration.
    """
    bridge = _notifications_bridge()
    if bridge.get("singletonByDesignUntil"):
        assert bridge["replicaCount"] == 1, (
            "notificationsBridge.replicaCount > 1 while singletonByDesignUntil is still set — "
            "remove the key (docs/review-queue.md, SC-04) before raising this number"
        )
