"""Wire <-> canonical mapping for the AMH boundary — pure, minimum-data (MZO-050a, ADR-0037).

**What this module is.** Pure functions that turn an ALREADY-DECODED AMH event (a
`Mapping[str, Any]` — parsed JSON, or Avro decoded to a dict by phase B) into the port value types
of `maezo.ports`, plus the reverse for an outgoing outcome. No I/O, no broker, no codec, no clock,
no randomness: given the same mapping it returns the same value or raises the same refusal, which is
what makes it testable directly against the digest-gated AMH fixtures.

**What it deliberately is NOT.** It does not decode Avro binary, does not speak to Glue, does not
frame or unframe a Kafka record, and does not settle a delivery. Those are phase B (MZO-050b /
MZO-060) and are blocked on dependencies and gates that do not exist yet — see the package
docstring.

Design decisions, each load-bearing:

**1. Timestamps: millis integer -> tz-aware UTC `datetime`.** The pinned wire form is a millis
`long` (every fixture: `"occurred_at": 1785837600000`), while `maezo.ports.envelope` declares
`datetime` and states that tz-awareness "is an adapter obligation". So the conversion happens HERE
and always yields UTC-aware values, computed as `EPOCH + timedelta(milliseconds=n)` — exact integer
arithmetic, never `fromtimestamp(n / 1000)` whose float division loses precision at millisecond
scale for present-day epochs. A NAIVE `datetime` is REFUSED rather than assumed to be UTC: guessing
a timezone for a consent decision instant is how an audit trail acquires a silent one-hour error.
An already-aware `datetime` is accepted and normalised to UTC (phase B's Avro `timestamp-millis`
logical type decodes to exactly that).

**2. `payload_hash` is RECOMPUTED and VERIFIED, not trusted.** The pin declares the
canonicalisation: "sha256 hex of the canonical JSON form of the payload field: UTF-8, sorted keys,
compact separators". `ensure_ascii=False` is the form that actually reproduces the AMH producer's
hashes — established empirically against the fixtures, not assumed: the two work-item fixtures
carry non-ASCII payload text ("Revisão…", "Verificação…") and their declared hashes reproduce ONLY
under `ensure_ascii=False`; under `ensure_ascii=True` the `\\uXXXX` escapes change the bytes and both
hashes diverge. The ASCII-only fixtures hash identically either way, so they cannot discriminate —
the non-ASCII ones are the whole evidence, and `tests/contract/amh/test_amh_mapping_fixtures.py`
pins the finding in both directions. A mismatch is a CONTRACT VIOLATION, not a warning: the hash is
the producer's own binding of its payload, so a payload that fails it was corrupted or rewritten in
transit and must be quarantined.

**3. Unknown EXTRA fields are IGNORED, never propagated (and never rejected).** The pin declares
`compatibility_mode: BACKWARD`, so a compatible MINOR may legitimately add an envelope field, and
Avro schema resolution has a reader ignore writer fields its own schema does not declare. An adapter
that REJECTED an unknown field would therefore be stricter than the wire contract it serves: the
first compatible publication would turn into a total intake outage, and the BACKWARD guarantee the
pin buys would be worthless. So an unrecognised envelope key is dropped silently. Dropping — rather
than carrying it along "just in case" — is the minimum-data half: `maezo.ports` declares exactly 28
envelope fields plus an opaque payload, and widening a canonical value type with a field no port
declares would smuggle contract-owner shape into the payer core (ADR-0037 immutable prohibition #2)
and put data in the domain that no consent decision authorised. MISSING required fields and WRONG
types are refused strictly — tolerance applies only to additions, which is precisely what BACKWARD
promises.

**4. NO reference is parsed.** Every `*_ref`, id and key is copied verbatim as an opaque `str`. No
prefix vocabulary, no splitting on `:`, no regex, no length check — DL-0040 records the DPO/Legal
gate on identity semantics as still OPEN (ADR-0037 XRD-05), and `maezo.ports.envelope` is explicit
that "a downstream package that starts splitting one of them on `:` is pre-empting a decision the
DPO has not made". The one thing checked about a reference is PRESENCE and STRING-NESS, which is
schema conformance, not identity semantics. `maezo.domain` is deliberately not imported.

**5. NO PHI, no raw payload, no wire value in ANY exception.** `AmhMappingError` carries a field
NAME and a closed reason token, and nothing else — no value, no payload, no `repr` of the event, and
it stores no reference to the event either. This is a direct counter-example to
`MalformedBridgeMessageError` in `src/maezo/platform/integrations/notifications_bridge.py`, which
interpolates the entire raw message into its exception text (`f"...: {raw!r}"`) AND retains it as
`self.raw`: on this boundary that would put payload content into logs, traces and quarantine
metadata, violating ADR-0037 immutable prohibition #5. The non-disclosure is proved by test, with a
sentinel value planted in every value-bearing field.

**6. An unknown MAJOR is refused; any MINOR/PATCH within the pinned major is accepted.** Every topic
in the pin is `.v1` with `major_version: 1`, and BACKWARD compatibility is a promise the contract
owner makes WITHIN a major and not across one. So `1.0.0`, an earlier `1.x` and a later compatible
`1.x` are all readable (unknown added fields are ignored, per decision 3), while `2.0.0` — or
anything whose major is not the pin's — is refused closed: a v2 event arriving on a v1 topic is
either a misrouted producer or a contract break, and both are quarantine cases, never
best-effort-parse cases.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from maezo.adapters.amh.contract import AmhAdapterError, AmhContractPin
from maezo.ports.consent import CanonicalConsentEvent, ConsentDecision
from maezo.ports.envelope import ENVELOPE_FIELD_ORDER, SourcePosition
from maezo.ports.outcomes import CanonicalOutcome
from maezo.ports.work_items import CanonicalWorkItem

#: Unix epoch as a tz-aware UTC instant. Millis conversion is exact integer arithmetic against this.
_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)

#: The wire key carrying the event body. Not one of the 28 envelope fields — it is the sibling the
#: three canonical records add, and the ports model it as an opaque `Mapping[str, Any]`.
PAYLOAD_KEY: Final[str] = "payload"

#: The two OPTIONAL envelope fields of the pinned baseline (ADR-0037: "`amh_mpi_ref` (opcional),
#: `beneficiary_ref` (opcional)"). Absent or explicitly null both map to `None`; every OTHER envelope
#: field is required and a missing one is a contract violation.
OPTIONAL_ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset({"amh_mpi_ref", "beneficiary_ref"})

#: Envelope fields whose wire form is a millis integer and whose port type is `datetime`.
_TIMESTAMP_FIELDS: Final[frozenset[str]] = frozenset({"occurred_at", "ingested_at"})

#: Envelope field carrying the nested `{kind, value, transaction_ref}` object -> `SourcePosition`.
_SOURCE_POSITION_FIELD: Final[str] = "source_position"

#: Envelope field carrying an integer count.
_REPLAY_COUNT_FIELD: Final[str] = "replay_count"

#: CLOSED projection of the consent record's `decision` token onto the single bit the payer core
#: needs. The vocabulary is governed OUTSIDE the wire (the fixtures README: `decision` is "string +
#: vocabulario no doc", so "a validacao de valor e fail-closed em contract test, nao no
#: deserializador") — which is exactly why the projection is closed HERE and an unrecognised token is
#: REFUSED rather than defaulted. Defaulting an unknown token to `granted=False` would be worse than
#: refusing: `maezo.ports.consent` states that a `granted=False` decision "is a successful read of a
#: real decision and is materially different from a refusal", so inventing one would write a consent
#: DENIAL that no data subject ever made into the LGPD audit trail. Both tokens below are observed in
#: the digest-gated fixtures (`consent.granted.json`, `consent.revoked.json`); an added token is a
#: deliberate change here, gated on the contract publication that introduces it.
CONSENT_DECISION_TOKENS: Final[Mapping[str, bool]] = {"granted": True, "revoked": False}


class AmhMappingError(AmhAdapterError):
    """A decoded AMH event does not satisfy the pinned canonical contract.

    Deterministic — the same bytes will fail identically — so the port-boundary mapping is
    `PortFailureReason.CONTRACT_VIOLATION` and the delivery must be quarantined, not retried.

    **Discloses nothing.** `field` is a contract field NAME and `reason` is a short closed token;
    neither the offending value nor the event is interpolated into the message or retained on the
    instance. See decision 5 in the module docstring for why that is a hard rule here.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"AMH event rejected: field {field!r}: {reason}")


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    """sha256 hex of the payload under the canonicalisation the pin declares.

    UTF-8, sorted keys, compact separators, `ensure_ascii=False` — the last of which is the
    empirically-established form (see decision 2 in the module docstring; the non-ASCII fixtures are
    the discriminating evidence, and the same spelling is used by
    `tests/contract/amh/test_contract_pin.py`).
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def millis_to_utc(value: object, *, field: str) -> datetime:
    """Convert a pinned wire timestamp to a tz-aware UTC `datetime`.

    Accepts a millis integer (the pinned wire form) or an already tz-aware `datetime` (what an Avro
    `timestamp-millis` logical type decodes to). Refuses everything else, including a naive
    `datetime`, a float and a numeric string — see decision 1: an ambiguous instant is refused, never
    guessed.

    Raises:
        AmhMappingError: the value is not a millis integer or a tz-aware datetime.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise AmhMappingError(field, "naive datetime — a timestamp must carry its timezone")
        return value.astimezone(UTC)
    # `bool` is an `int` subclass; `True` must not read as 1ms past the epoch.
    if isinstance(value, bool) or not isinstance(value, int):
        raise AmhMappingError(
            field, f"expected an epoch-millis integer or tz-aware datetime, got {type(value).__name__}"
        )
    return _EPOCH + timedelta(milliseconds=value)


def utc_to_millis(value: datetime, *, field: str) -> int:
    """Convert a tz-aware `datetime` back to the pinned epoch-millis wire form.

    Raises:
        AmhMappingError: the datetime is naive (refusing to invent a timezone on egress, exactly as
            on ingress).
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise AmhMappingError(field, "naive datetime — a timestamp must carry its timezone")
    delta = value.astimezone(UTC) - _EPOCH
    return delta // timedelta(milliseconds=1)


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def _require_present(event: Mapping[str, Any], field: str) -> Any:
    if field not in event:
        raise AmhMappingError(field, "required envelope field absent")
    value = event[field]
    if value is None:
        raise AmhMappingError(field, "required envelope field is null")
    return value


def _require_str(event: Mapping[str, Any], field: str) -> str:
    value = _require_present(event, field)
    if not isinstance(value, str):
        raise AmhMappingError(field, f"expected a string, got {type(value).__name__}")
    if not value:
        raise AmhMappingError(field, "empty string")
    return value


def _optional_str(event: Mapping[str, Any], field: str) -> str | None:
    """An OPTIONAL reference: absent or null -> `None`; present -> must be a non-empty string.

    Absent and null are treated alike deliberately: under BACKWARD compatibility a producer may omit
    an optional field entirely, and the fixtures spell it as an explicit `null`. Both mean "no
    value", and inventing a distinction between them would be inventing contract semantics.
    """
    value = event.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AmhMappingError(field, f"expected a string or null, got {type(value).__name__}")
    if not value:
        raise AmhMappingError(field, "empty string (use null for absent)")
    return value


def _require_int(event: Mapping[str, Any], field: str) -> int:
    value = _require_present(event, field)
    # `bool` first: it is an `int` subclass, so `True` would otherwise read as the integer 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise AmhMappingError(field, f"expected an integer, got {type(value).__name__}")
    # The annotation makes the isinstance narrowing of an `Any` explicit for mypy strict.
    narrowed: int = value
    return narrowed


def _map_source_position(event: Mapping[str, Any]) -> SourcePosition:
    """The nested `{kind, value, transaction_ref}` object -> `SourcePosition` (1 wire field -> 3).

    `kind` and `value` are required opaque strings; `transaction_ref` is the contract's optional
    transaction reference. None of the three is interpreted — `maezo.ports.envelope` is explicit that
    "the payer core never interprets any of the three".
    """
    raw = _require_present(event, _SOURCE_POSITION_FIELD)
    if not isinstance(raw, Mapping):
        raise AmhMappingError(_SOURCE_POSITION_FIELD, f"expected a nested object, got {type(raw).__name__}")
    # Names are reported QUALIFIED (`source_position.kind`), so an operator reading the refusal knows
    # which of the three sub-fields failed without the value ever being disclosed.
    nested = {f"{_SOURCE_POSITION_FIELD}.{key}": value for key, value in raw.items()}
    kind = _require_str(nested, f"{_SOURCE_POSITION_FIELD}.kind")
    value = _require_str(nested, f"{_SOURCE_POSITION_FIELD}.value")
    transaction_ref = _optional_str(nested, f"{_SOURCE_POSITION_FIELD}.transaction_ref")
    return SourcePosition(kind=kind, value=value, transaction_ref=transaction_ref)


def _require_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    if PAYLOAD_KEY not in event:
        raise AmhMappingError(PAYLOAD_KEY, "required payload absent")
    payload = event[PAYLOAD_KEY]
    if not isinstance(payload, Mapping):
        raise AmhMappingError(PAYLOAD_KEY, f"expected an object, got {type(payload).__name__}")
    return payload


# ---------------------------------------------------------------------------
# Contract checks
# ---------------------------------------------------------------------------


def _check_schema_version(version: str, *, pin: AmhContractPin) -> None:
    """Refuse an unknown MAJOR; accept any MINOR/PATCH within the pinned major (decision 6)."""
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise AmhMappingError("canonical_schema_version", "not a MAJOR.MINOR.PATCH version")
    major = int(parts[0])
    if major != pin.canonical_schema_major:
        raise AmhMappingError(
            "canonical_schema_version",
            f"unknown major v{major}; this adapter serves the pinned major "
            f"v{pin.canonical_schema_major} only",
        )


def _check_source_product(value: str, *, pin: AmhContractPin) -> None:
    """Enforce the CLOSED `source_product` vocabulary from the pin.

    The offending value is NOT quoted (decision 5); the permitted vocabulary IS, because it comes
    from the pin and is not wire data.
    """
    if not pin.is_known_source_product(value):
        raise AmhMappingError(
            "source_product",
            f"outside the closed pinned vocabulary {list(pin.source_product_vocabulary)}",
        )


def _check_payload_hash(declared: str, payload: Mapping[str, Any]) -> None:
    """Recompute the producer's binding of its own payload and refuse a mismatch (decision 2)."""
    if canonical_payload_hash(payload) != declared:
        raise AmhMappingError(
            "payload_hash",
            "recomputed sha256 over the canonical payload form does not match the declared hash",
        )


def _envelope_fields(event: Mapping[str, Any], *, pin: AmhContractPin) -> dict[str, Any]:
    """Extract and convert the 28 pinned envelope fields.

    Iterates `ENVELOPE_FIELD_ORDER` — the ports' single home for the pinned order — so an unknown
    extra wire key is never even looked at (decision 3), and a field cannot be silently missed by a
    hand-maintained list drifting from the pin.
    """
    if pin.envelope_field_order != ENVELOPE_FIELD_ORDER:
        raise AmhMappingError(
            "canonical_schema_version",
            "the loaded pin's envelope field order disagrees with maezo.ports.envelope",
        )

    fields: dict[str, Any] = {}
    for field in ENVELOPE_FIELD_ORDER:
        if field in _TIMESTAMP_FIELDS:
            fields[field] = millis_to_utc(_require_present(event, field), field=field)
        elif field == _SOURCE_POSITION_FIELD:
            fields[field] = _map_source_position(event)
        elif field == _REPLAY_COUNT_FIELD:
            fields[field] = _require_int(event, field)
        elif field in OPTIONAL_ENVELOPE_FIELDS:
            fields[field] = _optional_str(event, field)
        else:
            fields[field] = _require_str(event, field)

    _check_schema_version(fields["canonical_schema_version"], pin=pin)
    _check_source_product(fields["source_product"], pin=pin)
    return fields


# ---------------------------------------------------------------------------
# Public mapping: wire -> canonical
# ---------------------------------------------------------------------------


def map_work_item(event: Mapping[str, Any], *, pin: AmhContractPin) -> CanonicalWorkItem:
    """A decoded `amh.maezo.work-items.v1` event -> `CanonicalWorkItem`.

    Args:
        event: already-decoded event (parsed JSON / Avro-as-dict). Binary decoding is phase B.
        pin: the verified contract pin (`maezo.adapters.amh.contract.load_contract_pin`).

    Returns:
        The canonical work item: 28 converted envelope fields + the payload copied verbatim.

    Raises:
        AmhMappingError: a required field is absent/null/mistyped, a timestamp is not a millis
            integer or tz-aware datetime, `source_product` is outside the closed vocabulary, the
            canonical major is unknown, or `payload_hash` does not recompute. Map to
            `PortFailureReason.CONTRACT_VIOLATION` at the port boundary.
    """
    fields = _envelope_fields(event, pin=pin)
    payload = _require_payload(event)
    _check_payload_hash(fields["payload_hash"], payload)
    return CanonicalWorkItem(**fields, payload=payload)


def map_consent_event(event: Mapping[str, Any], *, pin: AmhContractPin) -> CanonicalConsentEvent:
    """A decoded `amh.maezo.consent.v1` event -> `CanonicalConsentEvent`. Same rules as
    `map_work_item`; the consent BODY is projected separately by `project_consent_decision`."""
    fields = _envelope_fields(event, pin=pin)
    payload = _require_payload(event)
    _check_payload_hash(fields["payload_hash"], payload)
    return CanonicalConsentEvent(**fields, payload=payload)


def map_outcome(event: Mapping[str, Any], *, pin: AmhContractPin) -> CanonicalOutcome:
    """A decoded `maezo.amh.outcomes.v1` event -> `CanonicalOutcome`.

    Present for replay/verification symmetry (the outcome topic is Maezo-produced, so the usual
    direction is `outcome_to_wire`): reading back a published outcome is how an outbox reconciler
    checks what it actually emitted.
    """
    fields = _envelope_fields(event, pin=pin)
    payload = _require_payload(event)
    _check_payload_hash(fields["payload_hash"], payload)
    return CanonicalOutcome(**fields, payload=payload)


def project_consent_decision(event: CanonicalConsentEvent) -> ConsentDecision:
    """Project a canonical consent event onto the minimal `ConsentDecision` the consent gate reads.

    **`purpose_of_use` comes from the consent BODY (`payload.purpose`), not from the envelope.** The
    two are genuinely different purposes and conflating them would break the gate. The envelope's
    `purpose_of_use` is why THIS EVENT was shared (every fixture: `sharing_amh_internal`); the
    decision's purpose is what the data subject actually authorised (`payload.purpose`:
    `analytics`). The fixtures settle it independently — the producer's own
    `idempotency_key` is `consent:{tenant}:{purpose}:{scope}:{revision}` =
    `"consent:austa_hospital:analytics:analytics:1"`, built from the PAYLOAD purpose, and the
    fixtures README describes the vector as a grant of "{purpose=analytics, scope=analytics}". A gate
    keyed on `sharing_amh_internal` would answer `latest_decision(subject, purpose_of_use=...)` for a
    purpose no subject was ever asked about.

    NO `scope` is projected: `maezo.ports.consent` is explicit that scope-to-authorisation mapping is
    the contract manifest's authority, so reading it here would invent contract semantics this repo
    does not own (ADR-0037 XRD-04).

    Raises:
        AmhMappingError: the consent body is missing a required field, `consent_revision` is not an
            integer, `decided_at` is not a valid instant, or `decision` is a token outside the closed
            projection (refused, never defaulted — see `CONSENT_DECISION_TOKENS`).
    """
    payload = event.payload
    if not isinstance(payload, Mapping):  # pragma: no cover - the port type guarantees a Mapping
        raise AmhMappingError(PAYLOAD_KEY, "expected an object")

    purpose = _require_str(payload, "purpose")
    decision_token = _require_str(payload, "decision")
    if decision_token not in CONSENT_DECISION_TOKENS:
        raise AmhMappingError(
            "decision",
            f"token outside the closed projection {sorted(CONSENT_DECISION_TOKENS)} — refusing to "
            "infer a consent bit from an unrecognised decision",
        )
    revision = _require_int(payload, "consent_revision")
    decided_at = millis_to_utc(_require_present(payload, "decided_at"), field="decided_at")

    return ConsentDecision(
        consent_decision_ref=event.consent_decision_ref,
        portable_subject_ref=event.portable_subject_ref,
        purpose_of_use=purpose,
        granted=CONSENT_DECISION_TOKENS[decision_token],
        consent_revision=revision,
        decided_at=decided_at,
    )


# ---------------------------------------------------------------------------
# Public mapping: canonical -> wire
# ---------------------------------------------------------------------------


def outcome_to_wire(outcome: CanonicalOutcome, *, pin: AmhContractPin) -> dict[str, Any]:
    """`CanonicalOutcome` -> the pinned wire mapping for `maezo.amh.outcomes.v1`.

    Emits keys in the pinned envelope order with `payload` last (the fixtures' own layout). Applies
    the SAME contract checks as ingress, in the same direction of strictness: the canonical major
    must be the pinned one, `source_product` must be in the closed vocabulary, timestamps must be
    tz-aware, and the outcome's declared `payload_hash` must actually bind its payload.

    That last check is the honest one. `payload_hash` is one of the 28 fields the caller supplies, so
    an outcome could be constructed with a hash that does not match its own payload — publishing it
    would put a self-contradicting event on the wire and hand the consumer a `CONTRACT_VIOLATION`
    that originated here. Refusing at the boundary is the DL-0038 posture applied to egress: do not
    emit something you have not verified, and never report success for it.

    Returns:
        A plain `dict` ready for the phase-B encoder. This function does NOT encode Avro, does not
        frame a Glue record and does not publish — it produces the mapping and nothing else.

    Raises:
        AmhMappingError: an unknown major, a `source_product` outside the vocabulary, a naive
            timestamp, or a `payload_hash` that does not bind the payload.
    """
    _check_schema_version(outcome.canonical_schema_version, pin=pin)
    _check_source_product(outcome.source_product, pin=pin)
    _check_payload_hash(outcome.payload_hash, outcome.payload)

    wire: dict[str, Any] = {}
    for field in ENVELOPE_FIELD_ORDER:
        value = getattr(outcome, field)
        if field in _TIMESTAMP_FIELDS:
            wire[field] = utc_to_millis(value, field=field)
        elif field == _SOURCE_POSITION_FIELD:
            wire[field] = {
                "kind": value.kind,
                "value": value.value,
                "transaction_ref": value.transaction_ref,
            }
        else:
            wire[field] = value
    wire[PAYLOAD_KEY] = dict(outcome.payload)
    return wire


__all__ = [
    "CONSENT_DECISION_TOKENS",
    "OPTIONAL_ENVELOPE_FIELDS",
    "PAYLOAD_KEY",
    "AmhMappingError",
    "canonical_payload_hash",
    "map_consent_event",
    "map_outcome",
    "map_work_item",
    "millis_to_utc",
    "outcome_to_wire",
    "project_consent_decision",
    "utc_to_millis",
]
