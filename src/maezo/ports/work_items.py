"""`WorkItemSource` — the payer core's ERP-neutral intake seam for canonical work items (MZO-030).

**What this port is.** An async iteration + acknowledgement seam over canonical work-item events.
The payer core consumes work FROM it and acknowledges back TO it. That is the whole contract.

**What this port deliberately is NOT.** It names no broker, no topic, no partition, no offset, no
consumer group, no schema registry and no serialisation format. `aiokafka`, `fastavro`, `boto3`
and friends are not importable from this package (enforced by
`tests/unit/ports/test_ports_purity.py`), and the transport identifiers that appear in the
contract pin (topic `amh.maezo.work-items.v1` and its quarantine sibling) are DELIBERATELY absent
from the port surface: a topic name is infrastructure, and ADR-0037's whole point is that the
payer core must be portable off that infrastructure. Binding the port to a concrete topic,
consumer and codec is MZO-050+ adapter scope (still gated) — not this package.

**Delivery handles are opaque.** `WorkItemDelivery.delivery_ref` is an adapter-minted handle whose
ONLY sanctioned use is being handed straight back to `ack`/`nack`. This module defines no format,
no prefix and no parsing for it (DL-0040/DL-0042, same posture as every other reference in this
package: ADR-0037 XRD-05 gated identity/reference semantics on DPO/Legal, DL-0040 opened that
gate and DL-0042 discharged it on 2026-08-05 — discharged on opaque refs, not on a prefix
vocabulary, so identity/reference value semantics remain MZO-020's DPO-gated scope).

**Acknowledgement semantics (deterministic, by contract).**

- `ack(delivery_ref)` — the work item was durably absorbed by the payer core. Acknowledging a
  handle that was already acknowledged MUST succeed again with the same result (idempotent), so a
  caller crash between "absorb" and "ack" cannot wedge the stream.
- `nack(delivery_ref, reason=...)` — the work item is NOT absorbable and MUST be routed to the
  source's quarantine, carrying ONLY the closed `PortFailureReason` token. Never the payload,
  never an upstream error body, never a subject reference (ADR-0037 immutable prohibition #5: no
  PHI nor raw source id in DLQ/quarantine metadata). `nack` is likewise idempotent per handle.
- There is no third outcome. A delivery is absorbed or quarantined; "drop it silently" is not
  expressible through this port, which is what makes the intake path auditable.

**No retries here.** Redelivery/backoff is the caller's (and the adapter's) policy — see
`maezo.ports.errors`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from maezo.ports.envelope import CanonicalEnvelope
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailureReason, PortResult


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalWorkItem(CanonicalEnvelope):
    """A canonical work-item event: the 28 pinned envelope fields + an OPAQUE payload mapping.

    `payload` is intentionally untyped structure (`Mapping[str, Any]`). The payer core's
    work-item semantics (workflow type, business reference, priority, due date, action summary,
    context references) are the CONTRACT OWNER's shape, and modelling them here would (a) copy a
    schema this repo is forbidden to hold editable copies of (ADR-0037 immutable prohibition #4)
    and (b) be speculative generalisation, since no consumer exists yet. The port refuses nothing
    by parsing: a payload that does not satisfy the pinned contract is a `CONTRACT_VIOLATION` the
    ADAPTER detects at decode time and quarantines.
    """

    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkItemDelivery:
    """One delivery from a `WorkItemSource`: the work item plus the opaque handle to settle it."""

    delivery_ref: str
    work_item: CanonicalWorkItem


@runtime_checkable
class WorkItemSource(Protocol):
    """Intake seam for canonical work items. Structural (`typing.Protocol`), never an ABC — the
    payer core depends on the SHAPE, so an adapter, an in-memory fake and a replay harness all
    satisfy it without inheriting anything from this package."""

    def stream(self) -> AsyncIterator[WorkItemDelivery]:
        """Yield deliveries until the caller stops iterating or the implementation closes.

        Deliberately NOT `async def`: the seam is `async for delivery in source.stream()`.

        No `timeout_seconds` here — a long-lived stream has no single deadline; polling/heartbeat
        behaviour is adapter policy. The bounded deadlines in this package are on the
        request/response calls (`ack`/`nack`), which is where an unbounded wait would actually
        stall the payer core's fail-closed accounting.
        """
        ...

    async def ack(
        self,
        delivery_ref: str,
        *,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[None]:
        """Settle a delivery as absorbed. Idempotent per `delivery_ref`.

        Returns `PortResult.ok(None)` when the acknowledgement is DURABLE — an implementation
        that cannot confirm settlement must refuse (`UPSTREAM_UNAVAILABLE`/`TIMEOUT`) rather than
        report a success it did not observe (DL-0038: a seam must not lie about its own result).
        """
        ...

    async def nack(
        self,
        delivery_ref: str,
        *,
        reason: PortFailureReason,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[None]:
        """Settle a delivery as NOT absorbable: route it to quarantine under a closed, opaque,
        non-PHI reason token. Idempotent per `delivery_ref`; same honest-result rule as `ack`."""
        ...
