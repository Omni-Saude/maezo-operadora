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
best-effort-parse cases. The version is parsed by the ONE validator this adapter has,
`contract.parse_semver`, which is ASCII-only and rejects leading zeros — see `_check_schema_version`.

**7-13. `AmhMappingError` is the ONLY exception this module raises — no exception of another type
may leave it.** `maezo.ports.errors` states the rule without qualification: "No adapter exception may
cross a port boundary". A `TypeError`, `ValueError` or `OverflowError` escaping here would not merely
skip the closed `PortFailureReason` taxonomy; in phase B it would wedge the consumer loop on a single
poison delivery instead of quarantining it, converting fail-closed into fail-crash. The conversions
that implement it, each documented at its site: **(7)** non-finite floats are refused rather than
hashed into non-JSON `NaN`/`Infinity` bytes; **(8)** a payload value the canonical JSON form cannot
represent (Avro `bytes`/`decimal`/`timestamp-millis` decode to exactly those Python types) is refused;
**(11)** payload text UTF-8 cannot encode — an unpaired surrogate, which `json.loads` accepts and
`ensure_ascii=False` then carries into `str.encode` — is refused rather than forced through with
`errors="surrogatepass"`; **(12)** nesting deeper than `json.dumps` can walk is refused (7, 8, 11 and
12 all in `canonical_payload_hash`); **(9)** an epoch-millis value that is legal for the wire's `long`
but outside `datetime`'s range is refused in `millis_to_utc`/`_to_utc`; **(10)** a sub-millisecond
instant on egress is refused rather than silently truncated in `utc_to_millis`, with
`truncate_to_wire_millis` exported as the one sanctioned way to comply; **(13)** anything a foreign
`tzinfo.utcoffset` raises is converted in `_carries_utc_offset`/`_to_utc`.

**The method, because a list of conversions is not a guarantee.** Two adversarial rounds found the
same defect four more times, and the diagnosis was about HOW the guards were chosen: placed at the
call the author happened to be thinking about, rather than derived from "every stdlib call that runs on
this data". Decision 11 sat one line below decision 8's own correct fix. So the invariant is asserted
by test rather than by this docstring —
`tests/unit/adapters/amh/test_no_foreign_exception_escapes.py` fuzzes hostile-but-in-contract values
through every public entry point of BOTH modules and asserts the only exception type ever observed is
`AmhAdapterError`, and it additionally walks the AST to prove that no escape-prone stdlib primitive is
called outside a `try` without a written justification. That second half is what would have caught
decision 11 mechanically.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from maezo.adapters.amh.contract import AmhAdapterError, AmhContractPin, parse_semver
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

    **`allow_nan=False` (decision 7).** Python's default emits the bare tokens `NaN`, `Infinity` and
    `-Infinity`, which are NOT JSON (RFC 8259 has no such literals). A payload carrying one would hash
    "successfully" here over bytes no conforming producer could have produced, so the recomputation
    would be comparing against a canonical form the other side cannot reach — a silent
    disagreement dressed as a match. Refused instead.

    **The input is DECODED, not necessarily JSON-typed (decision 8).** The module contract says
    "parsed JSON, or Avro decoded to a dict by phase B", and Avro's `bytes`, `decimal` and
    `timestamp-millis` decode to `bytes`, `Decimal` and `datetime` — precisely the Python types
    `json.dumps` cannot serialise. Left uncaught, that is a bare `TypeError` crossing a port boundary,
    which `maezo.ports.errors` forbids outright. It is refused as a contract violation instead: a
    payload the pinned canonical form cannot express has no hash to compare, so the delivery must be
    quarantined, not parsed on best effort.

    **Unpaired surrogates are REFUSED, not encoded (decision 11).** `ensure_ascii=False` is the
    empirically-established form above, and it is what makes the surrogate reachable: it puts the
    payload's text into the JSON string verbatim, so `str.encode("utf-8")` — not `json.dumps` — is
    where a lone `\\ud800` fails. `json.loads` accepts `"\\ud800"` happily, so this arrives from
    ordinary JSON wire bytes. It is refused rather than smoothed over with `errors="surrogatepass"`:
    that would invent a canonical form for a byte sequence UTF-8 has no encoding for, and AMH's own
    canonicalisation is NOT proven to do the same, so a hash computed that way would be compared
    against a producer hash that cannot exist. A payload the pinned canonical form cannot express is a
    quarantine case, exactly as for the unserialisable types above.

    **`RecursionError` is in the guard (decision 12).** `json.dumps` recurses per nesting level and
    dies past ~1000 on a default interpreter. JSON INGRESS happens to be self-limiting (`json.loads`
    and `json.dumps` share a budget, so anything parsed can be re-serialised), but the module contract
    admits an Avro-decoded dict, and Avro nesting passes through no such gate. `RecursionError` is not
    a `ValueError` or a `TypeError`, so the original two-type guard did not hold it.

    Raises:
        AmhMappingError: the payload contains a value the canonical JSON form cannot represent
            (an unserialisable type, a non-comparable key set, a non-finite float, or nesting deeper
            than the encoder can walk), or text that is not encodable as UTF-8.
    """
    # The `try` spans the encode and the digest, not just `json.dumps`. It used to close one line
    # early, which is the whole of decision 11's defect: the author guarded the call they were
    # thinking about instead of every call that runs on payload bytes.
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        # BEFORE the ValueError clause: `UnicodeEncodeError` IS a `ValueError`, and an operator triaging
        # a quarantined delivery needs these two apart — a lone surrogate is a producer ENCODING fault,
        # an `avro bytes` value is a payload TYPE fault. `from None`: the chained message quotes the
        # offending character and its position (decision 5).
        raise AmhMappingError(
            PAYLOAD_KEY, "payload text is not encodable as UTF-8 (unpaired surrogate)"
        ) from None
    except (TypeError, ValueError, RecursionError):
        # `from None`: the underlying message can quote a key name or a repr of wire data, and nothing
        # from the payload may reach a log, a trace or quarantine metadata (decision 5).
        raise AmhMappingError(
            PAYLOAD_KEY, "payload contains a value the canonical JSON form cannot represent"
        ) from None
    return digest


def _carries_utc_offset(value: datetime, *, field: str) -> bool:
    """Whether `value` declares a usable UTC offset — the ONE place foreign `tzinfo` code is called.

    **Why the guard is `Exception` and that is the derived answer, not a shrug.** `value.tzinfo` is an
    arbitrary object: on ingress whatever the decoder attached, on egress whatever the caller
    constructed its `CanonicalOutcome` with. `tzinfo.utcoffset` is therefore arbitrary code, and its
    raise set is unbounded by definition — the stdlib base class raises `NotImplementedError`, a
    subclass returning a non-`timedelta` produces `TypeError` and one returning an out-of-±24h offset
    produces `ValueError` (both from `astimezone`, not from here), and a subclass may simply raise
    anything at all. Naming the types we can think of is exactly the mistake that left `RuntimeError`
    escaping this line from BOTH `millis_to_utc` and `utc_to_millis`. The body is one foreign call and
    none of this module's own logic, so `Exception` masks nothing of ours; `BaseException` is
    deliberately NOT caught, because `KeyboardInterrupt` and `SystemExit` must still stop a consumer.

    This is also the last DISCLOSURE channel on the boundary: a foreign exception propagating verbatim
    carries its MESSAGE across, and decision 5 forbids that independently of the type — the gatekeeper
    leaked a `RuntimeError("PHI-SHAPED-SECRET")` through both call sites. `from None` and a fixed reason
    token close it.
    """
    try:
        return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None
    except Exception:
        raise AmhMappingError(field, "timezone offset could not be determined") from None


def millis_to_utc(value: object, *, field: str) -> datetime:
    """Convert a pinned wire timestamp to a tz-aware UTC `datetime`.

    Accepts a millis integer (the pinned wire form) or an already tz-aware `datetime` (what an Avro
    `timestamp-millis` logical type decodes to). Refuses everything else, including a naive
    `datetime`, a float and a numeric string — see decision 1: an ambiguous instant is refused, never
    guessed.

    **The legal `long` range is WIDER than `datetime` (decision 9).** The wire type is a millis
    `long`, so every value up to `2**63-1` is schema-conformant, while `datetime` stops at year 9999.
    The epoch arithmetic therefore raises `OverflowError` for a whole band of LEGAL wire values
    (year 10000 onwards, and again once the day count exceeds `timedelta`'s own limit). That is an
    adapter exception crossing a port boundary, which `maezo.ports.errors` forbids — and in phase B
    one poison timestamp would wedge the consumer loop instead of quarantining the delivery, turning
    fail-closed into fail-crash. It is refused as a contract violation instead.

    Raises:
        AmhMappingError: the value is not a millis integer or a tz-aware datetime, or it is an
            epoch-millis value outside the representable instant range.
    """
    if isinstance(value, datetime):
        if not _carries_utc_offset(value, field=field):
            raise AmhMappingError(field, "naive datetime — a timestamp must carry its timezone")
        return _to_utc(value, field=field)
    # `bool` is an `int` subclass; `True` must not read as 1ms past the epoch.
    if isinstance(value, bool) or not isinstance(value, int):
        raise AmhMappingError(
            field, f"expected an epoch-millis integer or tz-aware datetime, got {type(value).__name__}"
        )
    try:
        return _EPOCH + timedelta(milliseconds=value)
    except (OverflowError, OSError):
        # `from None`: the chained OverflowError text carries a day count computed FROM the wire value
        # (`days=1157407407`), which decision 5 keeps out of every rendered form of the refusal.
        raise AmhMappingError(field, "epoch-millis value outside the representable instant range") from None


def _to_utc(value: datetime, *, field: str) -> datetime:
    """Normalise an aware `datetime` to UTC, refusing the offsets that overflow the `datetime` range.

    `astimezone` shifts the wall clock by the offset, so an instant within ~14h of `datetime.min`/
    `datetime.max` carrying a non-UTC offset raises `OverflowError` — reachable from BOTH directions
    (Avro `timestamp-millis` on ingress, a caller-supplied `CanonicalOutcome` on egress).

    Two clauses, because the two failures are genuinely different and an operator must be able to tell
    them apart. `OverflowError`/`OSError` is stdlib arithmetic with a KNOWN raise set, so it keeps its
    precise reason. Everything else comes out of the `tzinfo.utcoffset` call `astimezone` makes
    internally — foreign code again (see `_carries_utc_offset`), which the offset check upstream cannot
    pre-validate because it only tests `is not None` and never inspects the returned type. A subclass
    returning `"nonsense"` or `timedelta(days=2)` therefore reaches HERE, and produced a bare
    `TypeError`/`ValueError` before this clause existed.
    """
    try:
        return value.astimezone(UTC)
    except (OverflowError, OSError):
        raise AmhMappingError(
            field, "instant outside the representable range once normalised to UTC"
        ) from None
    except Exception:
        raise AmhMappingError(field, "timezone offset could not be determined") from None


def utc_to_millis(value: datetime, *, field: str) -> int:
    """Convert a tz-aware `datetime` back to the pinned epoch-millis wire form.

    **Sub-millisecond precision is REFUSED, not truncated (decision 10).** The pinned wire type is a
    millis `long`, so a finer instant cannot be represented. Truncating it silently — which is what
    `delta // timedelta(milliseconds=1)` did — has two failure modes worth refusing over: the emitted
    event would carry an `occurred_at` that is NOT the instant the canonical outcome was audited with
    (so `map_outcome(outcome_to_wire(o)) != o`, and an outbox reconciler comparing the two finds a
    divergence this adapter created), and floor division rounds toward −∞, so a pre-epoch instant
    truncates AWAY from zero while a post-epoch one truncates toward it — a sign-dependent skew nobody
    declared. This is the same posture as the naive-datetime refusal directly above: on this boundary
    an instant is never silently altered, and a caller that wants millis truncates deliberately.

    **"Deliberately" now means `truncate_to_wire_millis`, and that matters more than it looks.** Only
    ~0.07% of the instants `datetime.now(UTC)` produces are millis-exact, so a phase-B publisher that
    stamped an outcome from the clock would be refused ~999 times in 1000. Leaving the guidance as one
    docstring clause invited every call site to improvise its own `.replace(microsecond=...)` — which is
    how the sign-dependent skew this refusal centralised away gets re-created N times over, once per
    improvisation. So the compliant operation is EXPORTED, with its rounding direction stated, and the
    refusal here is the thing that forces callers to it.

    Raises:
        AmhMappingError: the value is not a `datetime` at all, is naive (refusing to invent a timezone
            on egress, exactly as on ingress), carries sub-millisecond precision, or lies outside the
            range representable once normalised to UTC.
    """
    # The annotation says `datetime`, but `CanonicalOutcome` is a plain frozen dataclass with no runtime
    # validation, so `outcome_to_wire` can be handed one whose `occurred_at` is an int — and `.tzinfo`
    # on an int is a bare `AttributeError` crossing the boundary. Refused instead, for the same reason
    # `outcome_to_wire` already refuses a caller-supplied `payload_hash` that does not bind its own
    # payload: egress treats its caller as fallible rather than trusting an unvalidated dataclass.
    if not isinstance(value, datetime):
        raise AmhMappingError(field, f"expected a tz-aware datetime, got {type(value).__name__}")
    if not _carries_utc_offset(value, field=field):
        raise AmhMappingError(field, "naive datetime — a timestamp must carry its timezone")
    delta = _to_utc(value, field=field) - _EPOCH
    # `divmod(timedelta, timedelta)` cannot raise here: the divisor is a non-zero literal, so the one
    # exception it has (`ZeroDivisionError`) is unreachable.
    millis, remainder = divmod(delta, timedelta(milliseconds=1))
    if remainder:
        raise AmhMappingError(
            field,
            "sub-millisecond precision — the pinned wire form is epoch millis, and truncating would "
            "silently alter the instant",
        )
    return millis


def truncate_to_wire_millis(value: datetime, *, field: str = "occurred_at") -> datetime:
    """Drop sub-millisecond precision from an aware instant — the SANCTIONED way to satisfy egress.

    `utc_to_millis` refuses a finer-than-millisecond instant rather than truncating it (decision 10,
    upheld), and the pinned wire type is a millis `long`, so something has to truncate. This is that
    something: ONE implementation, with its direction written down, instead of a `.replace(microsecond=…)`
    improvised at each call site.

    **Direction: toward the PAST, on BOTH sides of the epoch.** The result is the latest millisecond
    boundary at or before `value` — floor on the epoch-millis number line, never "toward zero". That
    distinction IS the point: `divmod`/`//` on a `timedelta` floors toward −∞, so a *toward-zero*
    reading of the same code truncates a pre-epoch instant away from the epoch and a post-epoch one
    toward it. Stating floor-toward-the-past makes the behaviour identical in sign and describable in
    one sentence, which is what a phase-B publisher needs in order to reason about the instant it
    actually emitted.

    The result is normalised to UTC — the same instant, in the tz-aware UTC form every other timestamp
    on this boundary already carries (`millis_to_utc` returns UTC too). It is not a round trip through
    the caller's original zone, because re-entering foreign `tzinfo` code to get back there would add a
    failure mode for a cosmetic gain.

    Guaranteed: `utc_to_millis(truncate_to_wire_millis(v)) == (v - EPOCH) // 1ms` for every `v` this
    function accepts, and calling it twice changes nothing (idempotent).

    Raises:
        AmhMappingError: the value is not a `datetime`, is naive, or lies outside the representable
            range once normalised to UTC — the same three refusals as `utc_to_millis`, so a caller can
            route both identically.
    """
    if not isinstance(value, datetime):
        raise AmhMappingError(field, f"expected a tz-aware datetime, got {type(value).__name__}")
    if not _carries_utc_offset(value, field=field):
        raise AmhMappingError(field, "naive datetime — a timestamp must carry its timezone")
    # The reconstruction below needs NO overflow guard, and the reason is a proof rather than a hope.
    # Flooring moves the instant EARLIER, so the obvious worry is a value within 1ms of `datetime.min`
    # flooring underneath it. It cannot: `_EPOCH - datetime.min` is exactly 719162 days, a whole number
    # of milliseconds, and `datetime.min.microsecond` is 0 — so `datetime.min` sits exactly ON the
    # epoch-millisecond grid, and the floor of any instant at or above it is also at or above it.
    # `_to_utc` has already refused anything outside the range, so `millis` is in range by construction
    # and `millis * 1ms` is the original delta rounded down, which is likewise in `timedelta`'s range.
    # A `try` here would have been dead code, and dead defensive code is indistinguishable from a guard
    # that matters until someone tests it.
    millis = (_to_utc(value, field=field) - _EPOCH) // timedelta(milliseconds=1)
    return _EPOCH + millis * timedelta(milliseconds=1)


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


#: Which HALF of the event a refusal is about. The two halves are genuinely different things — the
#: envelope is the boundary's frozen 28-field baseline, the payload is the record body — and an
#: operator triaging a quarantined delivery has to look in the right one. A missing consent-body field
#: reported as an absent *envelope* field points at the wrong half of the event, so the container is
#: named explicitly rather than assumed (LOW-7).
_ENVELOPE_CONTAINER: Final[str] = "envelope"
_PAYLOAD_CONTAINER: Final[str] = "payload"


def _require_present(event: Mapping[str, Any], field: str, *, container: str = _ENVELOPE_CONTAINER) -> Any:
    if field not in event:
        raise AmhMappingError(field, f"required {container} field absent")
    value = event[field]
    if value is None:
        raise AmhMappingError(field, f"required {container} field is null")
    return value


def _require_str(event: Mapping[str, Any], field: str, *, container: str = _ENVELOPE_CONTAINER) -> str:
    value = _require_present(event, field, container=container)
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


def _require_int(event: Mapping[str, Any], field: str, *, container: str = _ENVELOPE_CONTAINER) -> int:
    value = _require_present(event, field, container=container)
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
    """Refuse an unknown MAJOR; accept any MINOR/PATCH within the pinned major (decision 6).

    Uses `contract.parse_semver` — the ONE version validator this adapter has (LOW-6). The
    hand-rolled `split(".")` + `str.isdigit()` form it replaces disagreed with the pin loader's on
    three inputs it accepted and should not have (`01.0.0`, fullwidth `１.０.０`, `1.0.٠`), and on a
    fourth it turned into a bare `ValueError`: `"²".isdigit()` is `True` while `int("²")` raises, so
    `"².0.0"` escaped the AmhMappingError contract entirely — the same port-boundary leak as the
    overflow above. `maezo.agents` already made exactly this `isdigit()` correction.
    """
    parsed = parse_semver(version)
    if parsed is None:
        raise AmhMappingError("canonical_schema_version", "not a MAJOR.MINOR.PATCH version")
    major = parsed[0]
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

    Every field read here is a PAYLOAD field, so the refusals say so (`container="payload"`): an
    operator told that a "required envelope field" was absent would go looking in the wrong half of a
    quarantined event, and the envelope of a consent event carrying no `purpose` key is perfectly
    well-formed.

    Raises:
        AmhMappingError: the consent body is missing a required field, `consent_revision` is not an
            integer, `decided_at` is not a valid instant, or `decision` is a token outside the closed
            projection (refused, never defaulted — see `CONSENT_DECISION_TOKENS`).
    """
    payload = event.payload
    if not isinstance(payload, Mapping):  # pragma: no cover - the port type guarantees a Mapping
        raise AmhMappingError(PAYLOAD_KEY, "expected an object")

    purpose = _require_str(payload, "purpose", container=_PAYLOAD_CONTAINER)
    decision_token = _require_str(payload, "decision", container=_PAYLOAD_CONTAINER)
    if decision_token not in CONSENT_DECISION_TOKENS:
        raise AmhMappingError(
            "decision",
            f"token outside the closed projection {sorted(CONSENT_DECISION_TOKENS)} — refusing to "
            "infer a consent bit from an unrecognised decision",
        )
    revision = _require_int(payload, "consent_revision", container=_PAYLOAD_CONTAINER)
    decided_at = millis_to_utc(
        _require_present(payload, "decided_at", container=_PAYLOAD_CONTAINER), field="decided_at"
    )

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
    "truncate_to_wire_millis",
    "utc_to_millis",
]
