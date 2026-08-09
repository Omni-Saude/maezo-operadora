"""The frozen 28-field canonical envelope every boundary event carries (ADR-0037, MZO-030).

**Single home for the pinned field order.** `ENVELOPE_FIELD_ORDER` is THE one constant in the
payer core that states the frozen envelope shape, and `CanonicalEnvelope`'s declaration order IS
that order. Both are cross-checked against `config/integrations/amh/contracts.lock.json`
(`envelope.field_order`, `envelope.field_count == 28`) by
`tests/unit/ports/test_envelope_pin.py`, so the ports cannot drift from the pin without failing
CI. The lock file is AMH-owned and NOT hand-editable (ADR-0037 XRD-04, MZO-010/XRG-3); this
module never re-declares a schema, only mirrors the pinned NAMES.

**Why field names that look AMH-flavoured are legitimate here.** ADR-0037's immutable prohibition
#2 bans Tasy/Debezium/AWS/Glue/HAPI/AMH **types** in the payer core. `amh_tenant` and
`amh_mpi_ref` are not types — they are the canonical contract's own frozen field names, published
by the contract owner and pinned by XRG-3. ADR-0037 is explicit that this repo "não inventa nenhum
campo de wire além deste baseline": mirroring the pinned names verbatim is the requirement;
inventing a sibling or a local alias would be the violation. No Avro/Kafka/Glue/FHIR/AWS TYPE
appears anywhere in this package — see `tests/unit/ports/test_ports_purity.py`.

**Every reference is OPAQUE, with NO format defined here (DL-0040/DL-0042).** ADR-0037 XRD-05
(identity) is explicitly gated on DPO/Legal; DL-0040 opened that gate and DL-0042 discharged it
on 2026-08-05 — identity/consent value semantics remain MZO-020's DPO-gated scope. Consequently
this module defines
NO parsing, NO prefix vocabulary, NO regex and NO structural validation for
`portable_subject_ref`, `amh_mpi_ref`, `beneficiary_ref`, `protected_source_record_ref`,
`consent_decision_ref`, `correlation_id`, `causation_id`, `idempotency_key` or `trace_id`. They
are `str` and stay `str`. A downstream package that starts splitting one of them on `:` is
pre-empting a decision the DPO has not made.

**Timestamps are `datetime`, not wire integers.** The canonical wire encoding of `occurred_at` /
`ingested_at` is an AMH-owned concern of the adapter that decodes it (MZO-050+). The port speaks
the ERP-neutral stdlib type; it does not import, mirror or depend on any Avro logical type. The
port does not enforce tz-awareness either — that is an adapter obligation, documented here rather
than smuggled in as a runtime parse rule.

**No PHI, ever.** No field here names or carries a patient/beneficiary identifier, a CPF, a CNS,
a name, a date of birth or any raw source id (ADR-0037 immutable prohibition #5). The
architecture fence asserts this structurally over every value type in this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

# THE pinned field order (28 fields), mirrored verbatim from the immutable contract pin at
# config/integrations/amh/contracts.lock.json -> envelope.field_order. Any edit here that is not
# a mirror of a NEW pin fails tests/unit/ports/test_envelope_pin.py.
ENVELOPE_FIELD_ORDER: Final[tuple[str, ...]] = (
    "event_id",
    "event_type",
    "canonical_schema_version",
    "occurred_at",
    "ingested_at",
    "source_vendor",
    "source_product",
    "source_instance",
    "source_tenant",
    "source_entity",
    "protected_source_record_ref",
    "source_position",
    "amh_tenant",
    "legal_entity",
    "portable_subject_ref",
    "amh_mpi_ref",
    "beneficiary_ref",
    "correlation_id",
    "causation_id",
    "idempotency_key",
    "consent_decision_ref",
    "purpose_of_use",
    "data_classification",
    "trace_id",
    "producer_version",
    "contract_manifest_digest",
    "payload_hash",
    "replay_count",
)

ENVELOPE_FIELD_COUNT: Final[int] = 28
"""Mirrors the pin's `envelope.field_count`. Kept as its own constant so the cross-check test
proves BOTH the count and the order against the lock file, not one derived from the other."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SourcePosition:
    """The source system's ordering position for the record this event was derived from.

    `kind` and `value` are OPAQUE strings from the contract owner's vocabulary (e.g. a change
    sequence marker); `transaction_ref` is the contract's optional transaction reference. The
    payer core never interprets any of the three — they exist so an event can be ordered/replayed
    against its origin without the core learning anything about the source technology.
    """

    kind: str
    value: str
    transaction_ref: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalEnvelope:
    """The 28 frozen envelope fields, in the pinned order — the base of every boundary event.

    Keyword-only by construction: 28 positional arguments would be an ordering footgun in exactly
    the place where a silent field transposition is a PHI/consent hazard, and keyword-only keeps
    the house "keyword-only after the first positional" style honest at this scale.

    `amh_mpi_ref` and `beneficiary_ref` are the two OPTIONAL fields of the pinned baseline
    (ADR-0037: "`amh_mpi_ref` (opcional), `beneficiary_ref` (opcional)"); every other field is
    required. Optionality is expressed as `str | None` with a `None` default — the port refuses
    nothing by parsing, so a missing REQUIRED field is a `CONTRACT_VIOLATION` the ADAPTER must
    report, never something this dataclass silently defaults away.
    """

    event_id: str
    event_type: str
    canonical_schema_version: str
    occurred_at: datetime
    ingested_at: datetime
    source_vendor: str
    source_product: str
    source_instance: str
    source_tenant: str
    source_entity: str
    protected_source_record_ref: str
    source_position: SourcePosition
    amh_tenant: str
    legal_entity: str
    portable_subject_ref: str
    amh_mpi_ref: str | None = None
    beneficiary_ref: str | None = None
    correlation_id: str
    causation_id: str
    idempotency_key: str
    consent_decision_ref: str
    purpose_of_use: str
    data_classification: str
    trace_id: str
    producer_version: str
    contract_manifest_digest: str
    payload_hash: str
    replay_count: int
