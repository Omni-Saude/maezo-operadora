"""AMH boundary adapter — PHASE A: the three layers that are provable today (MZO-050a, ADR-0037).

    maezo.adapters.amh.contract   runtime loader for the immutable contract pin, fail-closed
    maezo.adapters.amh.mapping    decoded wire event <-> the `maezo.ports` canonical value types
    maezo.adapters.amh.settings   env-driven configuration, DECLARED (nothing wired)

**Why there is no `consumer.py`, stated plainly rather than left as a gap.** MZO-050 declares an
`AmhEventConsumer` alongside these modules. It is NOT here, because it cannot be built honestly yet
and a dishonest one would re-commit a defect this repo already paid for:

1. **`ack` durability (blocking).** `maezo.ports.work_items.WorkItemSource.ack` may return success
   ONLY when settlement is DURABLE, and ADR-0037 XRD-10 states outright that "offsets Kafka nunca
   representam conclusao de negocio" — so committing an offset is not an `ack`. Durable settlement
   requires the AMH inbox (dedup on `{contract_manifest_digest, event_id}` + business-revision
   guard), which is work package **MZO-060**, carries its own DBA-review human gate, and does not
   exist: the newest migration is `0006_retire_dead_checkpoint_tables`. A consumer whose `ack`
   returned `PortResult.ok(None)` without durable settlement would be exactly DL-0038's defect — a
   seam lying about its own result — relocated from egress to intake.
2. **`nack` idempotency-per-handle (blocking).** The port requires `nack` to be idempotent per
   delivery handle, which needs the same settled-set that does not exist yet.
3. **Missing runtime dependencies (blocking).** A live consumer needs `fastavro` AND an MSK IAM/SASL
   signer; neither is in `pyproject.toml` or `uv.lock`, and phase A adds no runtime dependency.
4. **Undeclared wire framing (blocking).** The Glue Avro framing — magic byte and schema-version-id
   layout that precedes the Avro body — is NOT declared anywhere in the pinned contract. Guessing it
   would be inventing wire shape, which ADR-0037 forbids ("nao inventa nenhum campo de wire alem
   deste baseline").

Phase A therefore ships only what the digest-gated fixtures and the pin can actually prove: the pin
loader, the pure mapping, and the settings surface. No broker client, no AWS client, no Avro binary
decoding, no migration.

**Adapter exceptions never cross a port boundary.** `AmhAdapterError` and its subclasses are adapter
types; a port implementation must map them onto the closed `maezo.ports.errors.PortFailureReason`
taxonomy — `CONTRACT_VIOLATION` for both of the ones defined here, since a refused pin and a refused
event are deterministic (retrying identical bytes changes nothing) and must be quarantined.
"""

from __future__ import annotations

from maezo.adapters.amh.contract import (
    AmhAdapterError,
    AmhContractPin,
    AmhContractPinError,
    GlueRegistryPin,
    TopicPin,
    load_contract_pin,
    resolve_contract_pin_path,
)
from maezo.adapters.amh.mapping import (
    AmhMappingError,
    canonical_payload_hash,
    map_consent_event,
    map_outcome,
    map_work_item,
    millis_to_utc,
    outcome_to_wire,
    project_consent_decision,
    truncate_to_wire_millis,
    utc_to_millis,
)
from maezo.adapters.amh.settings import AmhAdapterSettings

__all__ = [
    "AmhAdapterError",
    "AmhAdapterSettings",
    "AmhContractPin",
    "AmhContractPinError",
    "AmhMappingError",
    "GlueRegistryPin",
    "TopicPin",
    "canonical_payload_hash",
    "load_contract_pin",
    "map_consent_event",
    "map_outcome",
    "map_work_item",
    "millis_to_utc",
    "outcome_to_wire",
    "project_consent_decision",
    "resolve_contract_pin_path",
    "truncate_to_wire_millis",
    "utc_to_millis",
]
