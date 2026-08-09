"""`OutcomePublisherPort` — the payer core's egress seam for decided outcomes (MZO-030).

**The publisher must not lie (DL-0038).** That decision was paid for with a real defect: a
producer that returned a fabricated `True` regardless of the actual delivery result, so a
best-effort route reported success while the primary publish had silently failed. This port
encodes the fix structurally:

- `publish` RETURNS a result. There is no `None`-returning, fire-and-forget variant, no
  `best_effort=True` parameter, and no default that swallows a failure. If the implementation did
  not OBSERVE the outcome as durably accepted, it must refuse
  (`UPSTREAM_UNAVAILABLE`/`TIMEOUT`/`CONTRACT_VIOLATION`) — never report success.
- The refusal is data on the result, not an exception, so a caller cannot lose it to a broad
  `except` and cannot mistake "swallowed" for "delivered".

**Idempotency is required, structurally.** `CanonicalOutcome.idempotency_key` is one of the 28
pinned envelope fields, declared `str` with NO default — an outcome literally cannot be
constructed without one, which is a stronger guarantee than a per-call argument an adapter could
default. `OutcomeAck.idempotent_replay` then lets the implementation report honestly that this
key had already been accepted (mirroring `DelegationResult.idempotent_replay`), so a redelivery
is distinguishable from a first acceptance instead of being papered over as an identical success.

**Not a transaction manager.** Durability of the state transition that PRODUCED the outcome is the
caller's concern (XRD-10 mandates a transactional outbox in the same transaction as the state
transition — ADR-0024/0027 Postgres pattern). This port publishes and reports; it does not own,
open or join a transaction, and it does not retry (retry is caller/outbox policy —
`maezo.ports.errors`).

**No broker, no codec, no topic.** As with `maezo.ports.work_items`, the destination topic
(`maezo.amh.outcomes.v1`) and its quarantine sibling are pinned INFRASTRUCTURE identifiers and are
deliberately absent from this seam; binding them is MZO-050+/MZO-090 adapter scope, still gated.

**Opaque references (DL-0040/DL-0042).** No reference on the envelope or in the outcome payload
is parsed, validated or given structure here — identity semantics remain MZO-020's DPO/Legal-gated
scope; DL-0040 opened that gate and DL-0042 discharged it on 2026-08-05.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from maezo.ports.envelope import CanonicalEnvelope
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortResult


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalOutcome(CanonicalEnvelope):
    """An outcome event: the 28 pinned envelope fields + an OPAQUE payload mapping.

    Shaped by the pinned outcome record of the canonical catalogue; the payload body (tenant,
    workflow business reference, outcome type/revision/status, deciding actor kind, evidence
    reference, completion timestamp) is contract-owner-owned and is NOT re-declared here — same
    posture as `CanonicalWorkItem.payload`.

    Named `CanonicalOutcome`, not after the wire record: ADR-0037's immutable prohibition #2 bans
    contract-owner/vendor TYPE names in the payer core, and the wire record's identity is already
    pinned in `config/integrations/amh/contracts.lock.json`. The FIELD names are mirrored verbatim
    (they are the frozen contract baseline — see `maezo.ports.envelope`); the TYPE name is
    payer-neutral.
    """

    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class OutcomeAck:
    """Honest acknowledgement of a publish that the implementation actually observed.

    - `event_id` — echo of the published envelope's `event_id`, so the caller can tie the
      acknowledgement to the exact event it emitted.
    - `idempotent_replay` — `True` when this `idempotency_key` had already been accepted, so the
      publish was a no-op. Reported, never hidden: a caller reconciling an outbox needs to tell a
      first acceptance from a redelivery.
    """

    event_id: str
    idempotent_replay: bool = False


@runtime_checkable
class OutcomePublisherPort(Protocol):
    """Outcome egress seam. Structural (`typing.Protocol`), never an ABC."""

    async def publish(
        self,
        outcome: CanonicalOutcome,
        *,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[OutcomeAck]:
        """Publish one outcome and report what actually happened.

        Returns `PortResult.ok(OutcomeAck(...))` ONLY when durable acceptance was observed.
        Refusals: `UPSTREAM_UNAVAILABLE`, `TIMEOUT`, `CONTRACT_VIOLATION` (the outcome does not
        satisfy the pinned contract — deterministic, so the caller must quarantine rather than
        retry identical bytes). No retries here; no fire-and-forget; no fabricated success.
        """
        ...
