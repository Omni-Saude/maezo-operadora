"""A2A delegation facts, emitted to Kafka (`agents.events.delegation.*`, ADR-0003).

Ported from the donor `Maezo-Healthcare-Plan` reference implementation (`src/maezo/a2a/
facts.py:1-103`) as part of the T2.4 A2A W2 (delegation runtime) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §1.2/§5.

Delegations are directed commands (A2A), but they also generate broadcast **facts** on Kafka for
observability and reactive consumers: requested / completed / rejected. The fact NEVER carries
PHI: only `task_id`, the chain, the type, and (on rejection) the reason — never the payload.

`register_a2a_topics()` is the "register the 3 topics" step design §5 W2 calls for. v2's
`maezo.platform.topic_registry.TopicRegistry` (unlike what the design doc's friction table
assumed) is a pure in-memory, injectable class with no `config/topic_registry.yaml` seed file and
no production call site yet (verified: zero non-test hits for `TopicRegistry()` in `src/`) — so
"registering" the topics here means providing the helper a bootstrap can call once such a call
site exists, plus a unit test proving the 3 topic names satisfy `TopicRegistry`'s naming
convention (`agents.events.delegation.{requested,completed,rejected}`, the 4-segment
`agents.events.X.Y` reserved-prefix form the registry already special-cases).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maezo.platform.topic_registry import TopicEntry, TopicRegistry

# Delegation fact topics (registered via `register_a2a_topics`, ADR-0003).
TOPIC_REQUESTED = "agents.events.delegation.requested"
TOPIC_COMPLETED = "agents.events.delegation.completed"
TOPIC_REJECTED = "agents.events.delegation.rejected"


class DelegationFactKind(StrEnum):
    REQUESTED = "requested"
    COMPLETED = "completed"
    REJECTED = "rejected"


_TOPIC_BY_KIND: dict[DelegationFactKind, str] = {
    DelegationFactKind.REQUESTED: TOPIC_REQUESTED,
    DelegationFactKind.COMPLETED: TOPIC_COMPLETED,
    DelegationFactKind.REJECTED: TOPIC_REJECTED,
}


@dataclass(frozen=True, slots=True)
class DelegationFact:
    """A delegation fact (no PHI). Serializes to the Kafka message value."""

    kind: DelegationFactKind
    task_id: str
    task_type: str
    tenant: str
    origin: str
    target: str
    delegation_chain: tuple[str, ...]
    ts: str
    reason: str | None = None
    output_ref: str | None = None

    @property
    def topic(self) -> str:
        return _TOPIC_BY_KIND[self.kind]

    def to_value(self) -> bytes:
        payload: dict[str, object] = {
            "kind": self.kind.value,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "tenant": self.tenant,
            "origin": self.origin,
            "target": self.target,
            "delegation_chain": list(self.delegation_chain),
            "ts": self.ts,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.output_ref is not None:
            payload["output_ref"] = self.output_ref
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def build_fact(
    kind: DelegationFactKind,
    *,
    task_id: str,
    task_type: str,
    tenant: str,
    origin: str,
    target: str,
    delegation_chain: tuple[str, ...],
    reason: str | None = None,
    output_ref: str | None = None,
    meta: Mapping[str, str] | None = None,
) -> DelegationFact:
    """Build a `DelegationFact` stamped with the current timestamp."""
    _ = meta  # reserved for extension; never enters the fact (avoids accidental PHI)
    return DelegationFact(
        kind=kind,
        task_id=task_id,
        task_type=task_type,
        tenant=tenant,
        origin=origin,
        target=target,
        delegation_chain=delegation_chain,
        ts=now_iso(),
        reason=reason,
        output_ref=output_ref,
    )


def register_a2a_topics(registry: TopicRegistry) -> list[TopicEntry]:
    """Register the 3 A2A delegation-fact topics into `registry` (design §5 W2).

    Idempotent-ish per `TopicRegistry.register`'s own semantics (overwrite-with-warning unless
    `strict=True`). All 3 topics carry PHI-safe delegation facts (`DelegationFact`, see module
    docstring) so `pii_zone="zona_geral"` (the registry's default) is correct — never
    `zona_phi`.
    """
    return registry.register_batch(
        [TOPIC_REQUESTED, TOPIC_COMPLETED, TOPIC_REJECTED],
        description="A2A delegation fact (ADR-0003) — task_id/chain/type only, no PHI",
    )
