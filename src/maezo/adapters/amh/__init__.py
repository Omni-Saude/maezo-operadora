"""AMH boundary adapter — phase A + the MZO-050b codec seam, still no consumer (ADR-0037).

    maezo.adapters.amh.contract       runtime loader for the immutable contract pin, fail-closed
    maezo.adapters.amh.mapping        decoded wire event <-> the `maezo.ports` canonical value types
    maezo.adapters.amh.settings       env-driven configuration, DECLARED (nothing wired)
    maezo.adapters.amh.wire_framing   pluggable wire-framing codec seam, DARK BUILD, fails closed

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
   signer; neither is in `pyproject.toml` or `uv.lock`. `wire_framing.py` itself adds NEITHER: framing
   is fixed-width bytes plus a UUID, so stripping it off needs no Avro library — only the eventual
   Avro-BODY decode would need `fastavro`, and nothing here does that decode.
4. **Undeclared wire framing (PARTIALLY addressed — a DARK BUILD, not a consumer).** The Glue Avro
   framing — magic byte and schema-version-id layout that precedes the Avro body — is still NOT
   declared anywhere in the pinned contract; guessing it would be inventing wire shape, which
   ADR-0037 forbids ("nao inventa nenhum campo de wire alem deste baseline"). `wire_framing.py`
   converts this from a PROJECT-blocker into a MANIFEST-DECLARATION-blocker: a pluggable
   `WireFramingCodec` seam plus a CANDIDATE implementation (`GlueSchemaRegistryCandidateCodec`) now
   exist and are tested against the real pin — which the factory (`select_wire_framing_codec`)
   refuses to use, closed, because the pin does not declare `wire_framing`. Nothing decodes real
   traffic; this blocker remains open for a genuine consumer until AMH publishes the declaration.

Phase A ships what the digest-gated fixtures and the pin can actually prove: the pin loader, the pure
mapping, and the settings surface. MZO-050b adds the codec seam on top, still with no broker client, no
AWS client, no Avro binary decoding, no migration, and no new runtime dependency.

**Adapter exceptions never cross a port boundary.** `AmhAdapterError` and its subclasses are adapter
types; a port implementation must map them onto the closed `maezo.ports.errors.PortFailureReason`
taxonomy — `CONTRACT_VIOLATION` for every one defined here, since a refused pin, a refused event and a
refused wire frame are all deterministic (retrying identical bytes changes nothing) and must be
quarantined.
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
from maezo.adapters.amh.wire_framing import (
    GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE,
    WIRE_FRAMING_MANIFEST_KEY,
    DecodedFrame,
    GlueSchemaRegistryCandidateCodec,
    WireFramingCodec,
    WireFramingDecodeError,
    WireFramingEncodeError,
    WireFramingError,
    WireFramingUndeclaredError,
    WireFramingUnknownDeclarationError,
    select_wire_framing_codec,
)

__all__ = [
    "GLUE_SCHEMA_REGISTRY_CANDIDATE_WIRE_FRAMING_VALUE",
    "WIRE_FRAMING_MANIFEST_KEY",
    "AmhAdapterError",
    "AmhAdapterSettings",
    "AmhContractPin",
    "AmhContractPinError",
    "AmhMappingError",
    "DecodedFrame",
    "GlueRegistryPin",
    "GlueSchemaRegistryCandidateCodec",
    "TopicPin",
    "WireFramingCodec",
    "WireFramingDecodeError",
    "WireFramingEncodeError",
    "WireFramingError",
    "WireFramingUndeclaredError",
    "WireFramingUnknownDeclarationError",
    "canonical_payload_hash",
    "load_contract_pin",
    "map_consent_event",
    "map_outcome",
    "map_work_item",
    "millis_to_utc",
    "outcome_to_wire",
    "project_consent_decision",
    "resolve_contract_pin_path",
    "select_wire_framing_codec",
    "truncate_to_wire_millis",
    "utc_to_millis",
]
