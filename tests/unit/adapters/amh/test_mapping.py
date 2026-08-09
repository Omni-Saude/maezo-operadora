"""Unit tests for the pure AMH wire<->canonical mapping (MZO-050a, ADR-0037).

The digest-gated AMH fixtures are the source of truth for wire SHAPE and are driven directly in
`tests/contract/amh/test_amh_mapping_fixtures.py`. This file covers the LOGIC around them: each
refusal path, the timestamp conversion algebra, the vocabulary and version gates, the
unknown-extra-field decision, and the non-disclosure guarantee — using a synthetic event built from
the fixture shape so a case can mutate one field at a time without touching the immutable fixtures.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh.contract import (
    CONTRACT_PIN_RELATIVE_PATH,
    MAX_SEMVER_COMPONENT_DIGITS,
    AmhAdapterError,
    load_contract_pin,
    parse_semver,
)
from maezo.adapters.amh.mapping import (
    CONSENT_DECISION_TOKENS,
    OPTIONAL_ENVELOPE_FIELDS,
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
from maezo.ports.envelope import ENVELOPE_FIELD_ORDER, SourcePosition

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES = REPO_ROOT / "tests/contract/amh/fixtures"

#: A value that must NEVER appear in an exception. Shaped like the PHI/raw-source-id classes ADR-0037
#: immutable prohibition #5 bans from keys, logs, traces and quarantine metadata.
PHI_SENTINEL = "SENTINEL-CPF-12345678901-JOAO-DA-SILVA-1980-01-01"


@pytest.fixture(scope="module")
def pin() -> Any:
    return load_contract_pin(REPO_ROOT / CONTRACT_PIN_RELATIVE_PATH)


def _fixture(name: str) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return parsed


def _work_item_event() -> dict[str, Any]:
    return _fixture("work_item.authorization_review.json")


def _consent_event() -> dict[str, Any]:
    return _fixture("consent.granted.json")


def _outcome_event() -> dict[str, Any]:
    return _fixture("outcome.revision-1.json")


def _rehash(event: dict[str, Any]) -> dict[str, Any]:
    """Recompute `payload_hash` after a deliberate payload edit, so a test can isolate the field it is
    actually exercising instead of tripping the hash check first."""
    event["payload_hash"] = canonical_payload_hash(event["payload"])
    return event


# ---------------------------------------------------------------------------
# Timestamps (decision 1)
# ---------------------------------------------------------------------------


def test_millis_convert_to_tz_aware_utc() -> None:
    result = millis_to_utc(1785837600000, field="occurred_at")
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    assert result == datetime(2026, 8, 4, 10, 0, 0, tzinfo=UTC)


def test_millisecond_precision_is_exact_not_float_rounded() -> None:
    """`fromtimestamp(n / 1000)` loses precision at present-day epochs; integer arithmetic does not."""
    for millis in (1785837600001, 1785837600123, 1785837600999):
        result = millis_to_utc(millis, field="occurred_at")
        assert utc_to_millis(result, field="occurred_at") == millis


def test_epoch_and_pre_epoch_millis_round_trip() -> None:
    assert millis_to_utc(0, field="occurred_at") == datetime(1970, 1, 1, tzinfo=UTC)
    assert utc_to_millis(millis_to_utc(-1000, field="occurred_at"), field="occurred_at") == -1000


def test_aware_datetime_is_accepted_and_normalised_to_utc() -> None:
    """Phase B's Avro `timestamp-millis` logical type decodes to an aware datetime."""
    brasilia = timezone(timedelta(hours=-3))
    aware = datetime(2026, 8, 4, 7, 0, 0, tzinfo=brasilia)
    result = millis_to_utc(aware, field="occurred_at")
    assert result == datetime(2026, 8, 4, 10, 0, 0, tzinfo=UTC)
    assert result.utcoffset() == timedelta(0)


def test_naive_datetime_is_refused_not_assumed_utc() -> None:
    """Guessing a timezone is how an audit trail acquires a silent offset error."""
    with pytest.raises(AmhMappingError) as exc:
        millis_to_utc(datetime(2026, 8, 4, 10, 0, 0), field="occurred_at")  # noqa: DTZ001
    assert exc.value.field == "occurred_at"
    assert "naive" in exc.value.reason


@pytest.mark.parametrize(
    "value", ["1785837600000", 1785837600.0, None, True, False, [], {}, "2026-08-04T10:00:00Z"]
)
def test_ambiguous_timestamp_forms_are_refused(value: object) -> None:
    """A numeric string, a float and a bool are all refused rather than coerced. `True`/`False` matter
    specifically: `bool` is an `int` subclass, so an unguarded check would read `True` as 1ms."""
    with pytest.raises(AmhMappingError):
        millis_to_utc(value, field="ingested_at")


def test_naive_datetime_is_refused_on_egress_too() -> None:
    with pytest.raises(AmhMappingError, match="naive"):
        utc_to_millis(datetime(2026, 8, 4, 10, 0, 0), field="occurred_at")  # noqa: DTZ001


# ---------------------------------------------------------------------------
# Timestamps: the legal `long` range is WIDER than `datetime` (decision 9)
# ---------------------------------------------------------------------------

#: Largest / smallest epoch-millis values `datetime` can represent, derived rather than hardcoded so
#: the boundary cases cannot drift from the stdlib.
_MAX_REPRESENTABLE_MILLIS = (
    datetime.max.replace(tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
) // timedelta(milliseconds=1)
_MIN_REPRESENTABLE_MILLIS = (
    datetime.min.replace(tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
) // timedelta(milliseconds=1)

#: Legal values of the pinned millis `long` that `datetime` cannot hold. Each hit a DIFFERENT stdlib
#: guard before the fix — `date value out of range`, `days=...; must have magnitude <= 999999999`, and
#: `Python int too large to convert to C int` respectively — and all three escaped as a bare
#: `OverflowError`.
_OVERFLOWING_MILLIS = (
    253402300800000,  # 10000-01-01, one millisecond past datetime.max
    10**17,
    2**63 - 1,  # the largest legal Avro long
    -(2**63),  # the smallest legal Avro long
)


@pytest.mark.parametrize("millis", _OVERFLOWING_MILLIS)
def test_a_legal_long_outside_the_datetime_range_is_refused_not_overflowed(millis: int) -> None:
    """MAJOR-1 regression. The pinned wire type is a millis `long`, so every one of these values is
    SCHEMA-CONFORMANT; `datetime` simply cannot hold them. Before the fix the epoch arithmetic raised a
    bare `OverflowError`, which `maezo.ports.errors` forbids outright ("No adapter exception may cross a
    port boundary") — and in phase B one poison timestamp would wedge the consumer loop instead of
    quarantining the delivery, turning fail-closed into fail-crash."""
    with pytest.raises(AmhMappingError) as exc:
        millis_to_utc(millis, field="occurred_at")
    assert exc.value.field == "occurred_at"
    assert "representable" in exc.value.reason
    assert not isinstance(exc.value, OverflowError)


@pytest.mark.parametrize("millis", _OVERFLOWING_MILLIS)
def test_the_overflow_band_is_reachable_through_the_real_mapping_path(pin: Any, millis: int) -> None:
    """Non-vacuity for the case above: it is not a direct-call-only curiosity. A producer emitting one
    of these on the pinned topic reaches it through `map_work_item`, i.e. through a port implementation."""
    event = _work_item_event()
    event["occurred_at"] = millis
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "occurred_at"


def test_the_overflow_refusal_discloses_no_wire_derived_value(pin: Any) -> None:
    """The stdlib's own text quotes a day count computed FROM the wire value (`days=1157407407` for
    1e17), and `__cause__`/`__context__` would carry it into any handler that logs a traceback. Decision
    5 keeps it out of every rendered form."""
    import traceback

    event = _work_item_event()
    event["occurred_at"] = 10**17
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)

    derived = "1157407407"
    # Prove the leak is real if unsuppressed: the stdlib exception does quote the derived count.
    with pytest.raises(OverflowError) as raw:
        datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=10**17)
    assert derived in str(raw.value), "this test's premise is stale — the stdlib text changed"

    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(exc.value))
    for text in (str(exc.value), exc.value.reason, rendered):
        assert derived not in text
        assert str(10**17) not in text


@pytest.mark.parametrize("millis", [_MIN_REPRESENTABLE_MILLIS, _MAX_REPRESENTABLE_MILLIS])
def test_the_extremes_datetime_can_represent_still_convert(millis: int) -> None:
    """NON-VACUITY for the overflow refusal: it must reject only what cannot be held, not the whole
    tail. The exact boundary values still convert, so the guard is a range check and not a blanket."""
    result = millis_to_utc(millis, field="occurred_at")
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


def test_the_extreme_representable_millis_are_exactly_one_below_the_refused_ones() -> None:
    """Pins the boundary itself: `MAX+1` and `MIN-1` are refused, so the guard sits where the stdlib's
    limit actually is rather than at some conservative distance from it."""
    for millis in (_MAX_REPRESENTABLE_MILLIS + 1, _MIN_REPRESENTABLE_MILLIS - 1):
        with pytest.raises(AmhMappingError, match="representable"):
            millis_to_utc(millis, field="occurred_at")


@pytest.mark.parametrize("offset_hours", [-3, 9])
def test_an_offset_that_overflows_the_range_on_utc_normalisation_is_refused(offset_hours: int) -> None:
    """The other overflow door, and it opens in BOTH directions: `astimezone` shifts the wall clock by
    the offset, so an aware instant within ~14h of `datetime.min`/`datetime.max` overflows while merely
    being normalised. Reachable on ingress (Avro `timestamp-millis` decodes to an aware datetime) and on
    egress (a caller-supplied `CanonicalOutcome`)."""
    tz = timezone(timedelta(hours=offset_hours))
    extreme = datetime.max if offset_hours < 0 else datetime.min
    with pytest.raises(AmhMappingError) as exc:
        millis_to_utc(extreme.replace(tzinfo=tz), field="occurred_at")
    assert exc.value.field == "occurred_at"
    assert "representable" in exc.value.reason

    with pytest.raises(AmhMappingError, match="representable"):
        utc_to_millis(extreme.replace(tzinfo=tz), field="occurred_at")


# ---------------------------------------------------------------------------
# Egress: sub-millisecond precision is refused, not truncated (decision 10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("microsecond", [1, 500, 1500, 999, 999999])
def test_a_sub_millisecond_instant_is_refused_on_egress(microsecond: int) -> None:
    """The pinned wire form is a millis `long`, so a finer instant cannot be represented. It is refused
    rather than truncated: a truncated egress would put an `occurred_at` on the wire that is NOT the
    instant the canonical outcome was audited with, so an outbox reconciler comparing what it published
    against what it recorded would find a divergence this adapter created."""
    value = datetime(2026, 8, 4, 10, 0, 0, microsecond, tzinfo=UTC)
    with pytest.raises(AmhMappingError) as exc:
        utc_to_millis(value, field="occurred_at")
    assert exc.value.field == "occurred_at"
    assert "sub-millisecond" in exc.value.reason


def test_millisecond_aligned_instants_still_convert_on_egress() -> None:
    """NON-VACUITY for the refusal above: everything the wire form CAN carry still converts, including
    pre-epoch values."""
    assert utc_to_millis(datetime(2026, 8, 4, 10, 0, 0, 123000, tzinfo=UTC), field="x") == 1785837600123
    assert utc_to_millis(datetime(1970, 1, 1, tzinfo=UTC), field="x") == 0
    assert utc_to_millis(datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC), field="x") == -1000


def test_the_truncation_this_refusal_replaces_was_sign_dependent() -> None:
    """WHY refusing beats documenting the truncation. Floor division rounds toward −∞, so a pre-epoch
    instant truncated AWAY from zero while a post-epoch one truncated toward it — a sign-dependent skew
    of up to a millisecond that no contract declared. Both are now refused; this pins the arithmetic
    that made silence unacceptable."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    post = datetime(1970, 1, 1, 0, 0, 0, 1500, tzinfo=UTC)  # +1.5ms
    pre = datetime(1969, 12, 31, 23, 59, 59, 999500, tzinfo=UTC)  # -0.5ms
    assert (post - epoch) // timedelta(milliseconds=1) == 1  # toward zero
    assert (pre - epoch) // timedelta(milliseconds=1) == -1  # away from zero
    for value in (post, pre):
        with pytest.raises(AmhMappingError, match="sub-millisecond"):
            utc_to_millis(value, field="occurred_at")


def test_a_sub_millisecond_outcome_is_refused_at_the_egress_boundary(pin: Any) -> None:
    """Reached through the real egress path, not just the helper: `datetime.now(UTC)` carries
    microseconds, so this is the shape a phase-B publisher would actually hand over."""
    import dataclasses

    outcome = dataclasses.replace(
        map_outcome(_outcome_event(), pin=pin),
        occurred_at=datetime(2026, 8, 4, 10, 0, 0, 250, tzinfo=UTC),
    )
    with pytest.raises(AmhMappingError) as exc:
        outcome_to_wire(outcome, pin=pin)
    assert exc.value.field == "occurred_at"
    assert "sub-millisecond" in exc.value.reason


def test_mapped_event_timestamps_are_tz_aware(pin: Any) -> None:
    item = map_work_item(_work_item_event(), pin=pin)
    assert item.occurred_at.tzinfo is not None
    assert item.ingested_at.tzinfo is not None
    assert item.occurred_at.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# payload_hash (decision 2)
# ---------------------------------------------------------------------------


def test_payload_hash_mismatch_is_refused(pin: Any) -> None:
    event = _work_item_event()
    event["payload"]["priority"] = "routine"  # payload changed, declared hash left stale
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "payload_hash"


def test_payload_hash_is_sensitive_to_key_addition_and_removal(pin: Any) -> None:
    added = _work_item_event()
    added["payload"]["unexpected_key"] = "x"
    with pytest.raises(AmhMappingError, match="payload_hash"):
        map_work_item(added, pin=pin)

    removed = _work_item_event()
    del removed["payload"]["priority"]
    with pytest.raises(AmhMappingError, match="payload_hash"):
        map_work_item(removed, pin=pin)


def test_canonicalisation_is_key_order_independent() -> None:
    """Sorted keys means a producer's key order cannot change the hash."""
    a = {"b": 1, "a": 2, "c": {"z": 1, "y": 2}}
    b = {"c": {"y": 2, "z": 1}, "a": 2, "b": 1}
    assert canonical_payload_hash(a) == canonical_payload_hash(b)


def test_canonicalisation_uses_compact_separators() -> None:
    """A space after `:` or `,` would change every hash — pin the exact byte form."""
    assert canonical_payload_hash({"a": 1, "b": 2}) == canonical_payload_hash(json.loads('{"a":1,"b":2}'))
    assert canonical_payload_hash({"a": 1, "b": 2}) == hashlib.sha256(b'{"a":1,"b":2}').hexdigest()


#: Values an AVRO-decoded payload can legitimately hold that `json.dumps` cannot serialise. The module
#: contract declares its input as "parsed JSON, or Avro decoded to a dict by phase B", and Avro
#: `bytes`, `decimal` and `timestamp-millis` decode to exactly these three Python types.
_UNSERIALISABLE_PAYLOADS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("avro bytes", {"attachment": b"\x00\x01"}),
    ("avro decimal", {"amount": Decimal("1.5")}),
    ("avro timestamp-millis", {"decided_at": datetime(2026, 8, 4, 10, 0, tzinfo=UTC)}),
    ("non-str key", {b"key": 1}),
    ("set", {"codes": {1, 2}}),
    ("non-comparable key set", {"a": 1, 2: "b"}),
)


@pytest.mark.parametrize(
    ("label", "payload"), _UNSERIALISABLE_PAYLOADS, ids=[p[0] for p in _UNSERIALISABLE_PAYLOADS]
)
def test_a_payload_the_canonical_form_cannot_represent_is_refused(
    label: str, payload: dict[str, Any]
) -> None:
    """MAJOR-2 regression. Before the fix each of these raised a bare `TypeError` out of
    `json.dumps` — an adapter exception crossing a port boundary, which `maezo.ports.errors` forbids.
    A payload the pinned canonical form cannot express has no hash to compare against the producer's,
    so the delivery is a CONTRACT_VIOLATION to quarantine, not something to parse on best effort."""
    with pytest.raises(AmhMappingError) as exc:
        canonical_payload_hash(payload)
    assert exc.value.field == "payload", label
    assert "cannot represent" in exc.value.reason, label
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True


def test_an_unserialisable_payload_is_refused_through_the_real_mapping_path(pin: Any) -> None:
    """Non-vacuity: reachable from `map_work_item`, i.e. from a port implementation, not only by calling
    the hash helper directly."""
    event = _work_item_event()
    event["payload"] = {"attachment": b"\x00\x01"}
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "payload"


def test_an_unserialisable_payload_is_refused_on_egress_too(pin: Any) -> None:
    import dataclasses

    outcome = dataclasses.replace(map_outcome(_outcome_event(), pin=pin), payload={"raw": b"\xff"})
    with pytest.raises(AmhMappingError) as exc:
        outcome_to_wire(outcome, pin=pin)
    assert exc.value.field == "payload"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_float_is_refused_rather_than_hashed_as_non_json(value: float) -> None:
    """`allow_nan=False`. Python's default emits the bare tokens `NaN`/`Infinity`/`-Infinity`, which are
    NOT JSON (RFC 8259 has no such literals), so the hash would be taken over bytes no conforming
    producer could ever have produced — a guaranteed disagreement dressed up as a successful hash."""
    with pytest.raises(AmhMappingError) as exc:
        canonical_payload_hash({"score": value})
    assert exc.value.field == "payload"

    # Non-vacuity: prove the default WOULD have hashed it, and over non-JSON bytes.
    permissive = json.dumps({"score": value}, sort_keys=True, separators=(",", ":"), allow_nan=True)
    assert permissive in ('{"score":NaN}', '{"score":Infinity}', '{"score":-Infinity}')
    with pytest.raises(json.JSONDecodeError):
        json.loads(permissive, parse_constant=_reject_json_constant)


def _reject_json_constant(token: str) -> Any:
    """A strict JSON reader: `json.loads` accepts `NaN`/`Infinity` by default, RFC 8259 does not."""
    raise json.JSONDecodeError(f"not JSON: {token}", token, 0)


def test_allow_nan_false_does_not_change_any_fixture_hash() -> None:
    """The tightening must be invisible to every real vector. All ten pinned fixtures are plain JSON, so
    the two spellings must agree byte-for-byte on each of their payloads — checked directly rather than
    inferred from the contract suite passing."""
    seen = 0
    for path in sorted(FIXTURES.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8")).get("payload")
        if payload is None:
            continue
        seen += 1
        permissive = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert canonical_payload_hash(payload) == hashlib.sha256(permissive.encode("utf-8")).hexdigest(), (
            path.name
        )
    assert seen >= 5, f"non-vacuity: only {seen} fixture payloads were compared"


def test_non_ascii_is_hashed_unescaped() -> None:
    """The empirical finding, pinned as a unit fact: `ensure_ascii=False`. Under `ensure_ascii=True`
    the `\\uXXXX` escapes change the bytes, and the fixtures' declared hashes stop reproducing."""
    payload = {"action_summary": "Revisão"}
    unescaped = hashlib.sha256('{"action_summary":"Revisão"}'.encode()).hexdigest()
    escaped = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    assert canonical_payload_hash(payload) == unescaped
    assert canonical_payload_hash(payload) != escaped


# ---------------------------------------------------------------------------
# source_product vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["tasy", "TASY_HOSPITAL", "tasy_hospital ", "philips", ""])
def test_source_product_outside_the_closed_vocabulary_is_refused(pin: Any, value: str) -> None:
    event = _work_item_event()
    event["source_product"] = value
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "source_product"


@pytest.mark.parametrize("value", ["tasy_hospital", "tasy_healthcare_plan"])
def test_both_pinned_source_products_are_accepted(pin: Any, value: str) -> None:
    event = _work_item_event()
    event["source_product"] = value
    assert map_work_item(event, pin=pin).source_product == value


# ---------------------------------------------------------------------------
# Schema version (decision 6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["1.0.0", "1.0.1", "1.1.0", "1.4.2", "1.99.99"])
def test_any_minor_or_patch_within_the_pinned_major_is_accepted(pin: Any, version: str) -> None:
    """BACKWARD compatibility is the contract owner's promise WITHIN a major. `1.1.0` is the "later
    compatible minor" case; with a pin at 1.0.0 every listed version shares the pinned major."""
    event = _work_item_event()
    event["canonical_schema_version"] = version
    assert map_work_item(event, pin=pin).canonical_schema_version == version


def test_a_prior_compatible_minor_is_accepted_against_a_forward_pin(tmp_path: Path) -> None:
    """The other direction the plan names explicitly: a pin that has moved to 1.2.0 must still read a
    producer still emitting the earlier compatible minor 1.1.0."""
    raw = json.loads((REPO_ROOT / CONTRACT_PIN_RELATIVE_PATH).read_text(encoding="utf-8"))
    raw["provenance"]["canonical_schema_version"] = "1.2.0"
    raw["envelope"]["canonical_schema_version"] = "1.2.0"
    forward = tmp_path / "contracts.lock.json"
    forward.write_text(json.dumps(raw), encoding="utf-8")
    forward_pin = load_contract_pin(forward)

    event = _work_item_event()
    event["canonical_schema_version"] = "1.1.0"
    assert map_work_item(event, pin=forward_pin).canonical_schema_version == "1.1.0"


@pytest.mark.parametrize("version", ["2.0.0", "0.9.0", "3.1.4"])
def test_an_unknown_major_is_refused_closed(pin: Any, version: str) -> None:
    """A v2 event on a v1 topic is a misrouted producer or a contract break — both quarantine cases,
    never best-effort-parse cases."""
    event = _work_item_event()
    event["canonical_schema_version"] = version
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "canonical_schema_version"
    assert "unknown major" in exc.value.reason


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-rc1", "1.0.0.0", "abc", "1.a.0"])
def test_a_malformed_schema_version_is_refused(pin: Any, version: str) -> None:
    event = _work_item_event()
    event["canonical_schema_version"] = version
    with pytest.raises(AmhMappingError, match="canonical_schema_version"):
        map_work_item(event, pin=pin)


#: Version spellings that are not strict semver but that a permissive validator reads as `1.0.0`.
#: `"²"` is the dangerous one: `str.isdigit()` accepts it while `int()` raises, which is why the
#: hand-rolled `split(".") + isdigit()` check leaked a bare `ValueError` (`maezo.agents` already made
#: this exact `isdigit()` -> stricter correction).
_NON_STRICT_VERSIONS = ["01.0.0", "1.00.0", "１.０.０", "1.0.٠", "².0.0", "1.٢.0", " 1.0.0", "1.0.0 "]


@pytest.mark.parametrize("version", _NON_STRICT_VERSIONS)
def test_a_non_strict_semver_is_refused_as_a_mapping_error_never_a_valueerror(pin: Any, version: str) -> None:
    """LOW-6 regression, both halves. The two validators (this module's and the pin loader's) disagreed
    with each other AND both accepted leading zeros and Unicode digits; and `².0.0` escaped as a bare
    `ValueError`, which is the same port-boundary leak as the overflow and the hash TypeError."""
    event = _work_item_event()
    event["canonical_schema_version"] = version
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "canonical_schema_version"
    assert "MAJOR.MINOR.PATCH" in exc.value.reason


def test_the_mapping_and_the_pin_loader_share_one_version_validator() -> None:
    """LOW-6, the consolidation itself: `maezo.adapters.amh.mapping` must not re-implement the grammar.
    Proved structurally (the symbol is imported, not redefined) and behaviourally (one agreed verdict on
    every spelling above)."""
    import ast

    from maezo.adapters.amh import mapping as mapping_module
    from maezo.adapters.amh.contract import parse_semver

    assert mapping_module.parse_semver is parse_semver

    source = Path(mapping_module.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    locally_defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "parse_semver" not in locally_defined, "the mapping layer redefined the version validator"
    # Checked over the AST, not the text: the docstring explains the `isdigit()` hazard by name.
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "isdigit" not in attributes, "the hand-rolled isdigit() validator is back"
    assert "isdecimal" not in attributes, "a second, hand-rolled version validator reappeared"

    for version in [*_NON_STRICT_VERSIONS, "1.0.0", "1.99.99", "2.0.0", "0.0.1"]:
        strict = parse_semver(version) is not None
        assert strict == (version in {"1.0.0", "1.99.99", "2.0.0", "0.0.1"}), version


# ---------------------------------------------------------------------------
# Required / optional / unknown fields (decision 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(set(ENVELOPE_FIELD_ORDER) - OPTIONAL_ENVELOPE_FIELDS))
def test_every_required_envelope_field_is_enforced(pin: Any, field: str) -> None:
    """All 26 required fields, one test each — a missing required field is a contract violation the
    ADAPTER must report, never something a default silently papers over."""
    event = _work_item_event()
    del event[field]
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field.startswith(field)


@pytest.mark.parametrize("field", sorted(set(ENVELOPE_FIELD_ORDER) - OPTIONAL_ENVELOPE_FIELDS))
def test_a_null_required_envelope_field_is_refused(pin: Any, field: str) -> None:
    event = _work_item_event()
    event[field] = None
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field.startswith(field)


@pytest.mark.parametrize("field", sorted(OPTIONAL_ENVELOPE_FIELDS))
def test_optional_fields_accept_null_and_absence_alike(pin: Any, field: str) -> None:
    explicit_null = _work_item_event()
    explicit_null[field] = None
    assert getattr(map_work_item(explicit_null, pin=pin), field) is None

    absent = _work_item_event()
    absent.pop(field, None)
    assert getattr(map_work_item(absent, pin=pin), field) is None


def test_optional_field_present_is_carried_verbatim(pin: Any) -> None:
    event = _consent_event()
    mapped = map_consent_event(event, pin=pin)
    assert mapped.amh_mpi_ref == event["amh_mpi_ref"]


# ---------------------------------------------------------------------------
# The pin <-> ports envelope-order cross-check (the SECONDARY defence)
# ---------------------------------------------------------------------------


def test_a_pin_whose_envelope_order_disagrees_with_the_ports_is_refused(pin: Any) -> None:
    """G10. `_envelope_fields` iterates `maezo.ports.envelope.ENVELOPE_FIELD_ORDER`, so if a loaded pin
    ever declared a DIFFERENT order the mapping would silently build events against the ports' order
    while claiming to serve the pin's — the two would mean different things and nothing would say so.

    The pin loader's `_check_envelope` is the primary defence (and is tested); this is the second,
    independent one at the point of USE, and it is the one that still fires when a pin object reaches the
    mapping without having come from `load_contract_pin` — exactly what `dataclasses.replace` produces
    here, and exactly what a future in-memory or cached pin would produce."""
    import dataclasses

    transposed = list(ENVELOPE_FIELD_ORDER)
    transposed[3], transposed[4] = transposed[4], transposed[3]  # occurred_at <-> ingested_at
    bad_pin = dataclasses.replace(pin, envelope_field_order=tuple(transposed))

    with pytest.raises(AmhMappingError) as exc:
        map_work_item(_work_item_event(), pin=bad_pin)
    assert exc.value.field == "canonical_schema_version"
    assert "disagrees with maezo.ports.envelope" in exc.value.reason


@pytest.mark.parametrize(
    "order",
    [
        (),
        ("event_id",),
        tuple(reversed(ENVELOPE_FIELD_ORDER)),
        (*ENVELOPE_FIELD_ORDER, "a_29th_field"),
        tuple(ENVELOPE_FIELD_ORDER[:-1]),
    ],
    ids=["empty", "truncated-to-one", "reversed", "widened", "narrowed"],
)
def test_every_shape_of_pin_ports_envelope_divergence_is_refused(pin: Any, order: tuple[str, ...]) -> None:
    """Reordering is not the only divergence: a pin could also be narrower or wider than the ports."""
    import dataclasses

    bad_pin = dataclasses.replace(pin, envelope_field_order=order)
    with pytest.raises(AmhMappingError, match="disagrees with maezo.ports.envelope"):
        map_work_item(_work_item_event(), pin=bad_pin)


def test_the_cross_check_passes_for_a_pin_that_does_agree(pin: Any) -> None:
    """NON-VACUITY: the real committed pin agrees with the ports, so the guard is a real comparison and
    not an unconditional refusal."""
    assert pin.envelope_field_order == ENVELOPE_FIELD_ORDER
    assert map_work_item(_work_item_event(), pin=pin).event_id


def test_unknown_extra_envelope_field_is_ignored_not_rejected(pin: Any) -> None:
    """DECISION 3, the BACKWARD half: the pin declares `compatibility_mode: BACKWARD`, so a compatible
    MINOR may add a field and an Avro reader ignores writer fields it does not declare. Rejecting
    would make the first compatible publication a total intake outage."""
    event = _work_item_event()
    event["a_field_a_future_compatible_minor_added"] = "some value"
    event["another_added_object"] = {"nested": [1, 2, 3]}
    item = map_work_item(event, pin=pin)
    assert item.event_id == event["event_id"]


def test_unknown_extra_envelope_field_is_not_carried_into_the_domain(pin: Any) -> None:
    """DECISION 3, the minimum-data half: ignored means DROPPED, not stashed. Widening a canonical
    value type with a field no port declares would smuggle contract-owner shape into the payer core
    and put data in the domain no consent decision authorised."""
    event = _work_item_event()
    event["a_field_a_future_compatible_minor_added"] = PHI_SENTINEL
    item = map_work_item(event, pin=pin)

    assert not hasattr(item, "a_field_a_future_compatible_minor_added")
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(item)}
    assert field_names == set(ENVELOPE_FIELD_ORDER) | {"payload"}
    assert PHI_SENTINEL not in repr(item), "the dropped field leaked into the canonical value"


def test_payload_is_copied_verbatim_and_never_widened(pin: Any) -> None:
    event = _work_item_event()
    item = map_work_item(event, pin=pin)
    assert item.payload == event["payload"]


@pytest.mark.parametrize("field", ["event_id", "portable_subject_ref", "idempotency_key", "trace_id"])
def test_a_non_string_reference_is_refused(pin: Any, field: str) -> None:
    event = _work_item_event()
    event[field] = 12345
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == field


def test_an_empty_required_string_is_refused(pin: Any) -> None:
    event = _work_item_event()
    event["portable_subject_ref"] = ""
    with pytest.raises(AmhMappingError, match="portable_subject_ref"):
        map_work_item(event, pin=pin)


@pytest.mark.parametrize("value", ["0", 1.5, True, None, "many"])
def test_a_non_integer_replay_count_is_refused(pin: Any, value: object) -> None:
    event = _work_item_event()
    event["replay_count"] = value
    with pytest.raises(AmhMappingError, match="replay_count"):
        map_work_item(event, pin=pin)


def test_missing_payload_is_refused(pin: Any) -> None:
    event = _work_item_event()
    del event["payload"]
    with pytest.raises(AmhMappingError, match="payload"):
        map_work_item(event, pin=pin)


@pytest.mark.parametrize("value", ["a string", 42, [1, 2], None])
def test_a_non_object_payload_is_refused(pin: Any, value: object) -> None:
    event = _work_item_event()
    event["payload"] = value
    with pytest.raises(AmhMappingError, match="payload"):
        map_work_item(event, pin=pin)


# ---------------------------------------------------------------------------
# source_position: 1 wire field -> 3 canonical values
# ---------------------------------------------------------------------------


def test_source_position_maps_onto_the_three_field_shape(pin: Any) -> None:
    event = _work_item_event()
    item = map_work_item(event, pin=pin)
    assert item.source_position == SourcePosition(kind="scn", value="128734650912", transaction_ref=None)


def test_source_position_transaction_ref_is_carried_when_present(pin: Any) -> None:
    event = _work_item_event()
    event["source_position"]["transaction_ref"] = "amh:txn:v1:abc"
    assert map_work_item(event, pin=pin).source_position.transaction_ref == "amh:txn:v1:abc"


@pytest.mark.parametrize("field", ["kind", "value"])
def test_a_missing_source_position_subfield_is_refused(pin: Any, field: str) -> None:
    event = _work_item_event()
    del event["source_position"][field]
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == f"source_position.{field}", "the refusal must name the nested field"


@pytest.mark.parametrize("value", ["scn:128", 42, None, []])
def test_a_non_object_source_position_is_refused(pin: Any, value: object) -> None:
    event = _work_item_event()
    event["source_position"] = value
    with pytest.raises(AmhMappingError, match="source_position"):
        map_work_item(event, pin=pin)


# ---------------------------------------------------------------------------
# No reference is parsed (decision 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "protected_source_record_ref",
        "portable_subject_ref",
        "consent_decision_ref",
        "correlation_id",
        "causation_id",
        "idempotency_key",
        "trace_id",
        "amh_tenant",
        "legal_entity",
    ],
)
def test_references_are_opaque_and_copied_verbatim(pin: Any, field: str) -> None:
    """DL-0040 / DL-0042 / ADR-0037 XRD-05: identity semantics are DPO/Legal-gated; DL-0040 opened
    that gate and DL-0042 discharged it on 2026-08-05. A value with no `:` at all, a wrong prefix
    or an unexpected shape must pass through untouched — this adapter validates PRESENCE and
    STRING-NESS, never FORMAT."""
    for opaque in ("no-colons-at-all", "totally:different:prefix:v9", "x", "a" * 500, "::::"):
        event = _work_item_event()
        event[field] = opaque
        assert getattr(map_work_item(event, pin=pin), field) == opaque


def test_a_reference_is_never_split_or_normalised(pin: Any) -> None:
    event = _work_item_event()
    event["portable_subject_ref"] = "  amh:psr:v1:PADDED  "
    # Copied verbatim — no strip(), no case fold, no split on ':'.
    assert map_work_item(event, pin=pin).portable_subject_ref == "  amh:psr:v1:PADDED  "


# ---------------------------------------------------------------------------
# Consent projection
# ---------------------------------------------------------------------------


def test_consent_grant_projects_to_granted_true(pin: Any) -> None:
    decision = project_consent_decision(map_consent_event(_consent_event(), pin=pin))
    assert decision.granted is True
    assert decision.consent_revision == 1
    assert decision.decided_at == datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    assert decision.decided_at.tzinfo is not None


def test_consent_revoke_projects_to_granted_false(pin: Any) -> None:
    decision = project_consent_decision(map_consent_event(_fixture("consent.revoked.json"), pin=pin))
    assert decision.granted is False
    assert decision.consent_revision == 2


def test_consent_decision_purpose_comes_from_the_body_not_the_envelope(pin: Any) -> None:
    """The decision that would break the consent gate if taken the other way. The envelope's
    `purpose_of_use` is why the EVENT was shared (`sharing_amh_internal`); the decision's purpose is
    what the subject authorised (`analytics`). The producer's own idempotency_key
    (`consent:{tenant}:{purpose}:{scope}:{revision}`) is built from the PAYLOAD purpose."""
    event = _consent_event()
    assert event["purpose_of_use"] == "sharing_amh_internal"
    assert event["payload"]["purpose"] == "analytics"
    assert "analytics" in event["idempotency_key"]

    decision = project_consent_decision(map_consent_event(event, pin=pin))
    assert decision.purpose_of_use == "analytics"
    assert decision.purpose_of_use != event["purpose_of_use"]


def test_consent_decision_echoes_subject_and_decision_ref_from_the_envelope(pin: Any) -> None:
    event = _consent_event()
    decision = project_consent_decision(map_consent_event(event, pin=pin))
    assert decision.portable_subject_ref == event["portable_subject_ref"]
    assert decision.consent_decision_ref == event["consent_decision_ref"]


def test_an_unknown_decision_token_is_refused_never_defaulted(pin: Any) -> None:
    """Fail-closed, and specifically NOT defaulted to `granted=False`: `maezo.ports.consent` states a
    `granted=False` decision is a successful read of a REAL decision, materially different from a
    refusal. Inventing one would write a consent DENIAL no subject ever made into the audit trail."""
    for token in ("expired", "pending", "GRANTED", "withdrawn", "granted "):
        event = _consent_event()
        event["payload"]["decision"] = token
        _rehash(event)
        with pytest.raises(AmhMappingError) as exc:
            project_consent_decision(map_consent_event(event, pin=pin))
        assert exc.value.field == "decision", token
        assert "closed projection" in exc.value.reason, token


def test_an_empty_or_mistyped_decision_token_is_refused(pin: Any) -> None:
    """The adjacent shapes, which are refused one step EARLIER (on string-ness) rather than by the
    closed projection — still refused, never defaulted."""
    for token in ("", None, 1, True, ["granted"]):
        event = _consent_event()
        event["payload"]["decision"] = token
        _rehash(event)
        with pytest.raises(AmhMappingError) as exc:
            project_consent_decision(map_consent_event(event, pin=pin))
        assert exc.value.field == "decision", token


def test_the_closed_consent_projection_matches_the_observed_fixture_tokens() -> None:
    assert CONSENT_DECISION_TOKENS == {"granted": True, "revoked": False}


@pytest.mark.parametrize("field", ["purpose", "decision", "consent_revision", "decided_at"])
def test_a_missing_consent_body_field_is_refused(pin: Any, field: str) -> None:
    event = _consent_event()
    del event["payload"][field]
    _rehash(event)
    with pytest.raises(AmhMappingError) as exc:
        project_consent_decision(map_consent_event(event, pin=pin))
    assert exc.value.field == field


@pytest.mark.parametrize("field", ["purpose", "decision", "consent_revision", "decided_at"])
@pytest.mark.parametrize("absent", [True, False], ids=["absent", "null"])
def test_a_consent_body_refusal_names_the_payload_half_not_the_envelope(
    pin: Any, field: str, absent: bool
) -> None:
    """LOW-7. `project_consent_decision` reads PAYLOAD fields, so a refusal must say so. Reporting
    "required envelope field absent" sends an operator triaging a quarantined delivery to the wrong half
    of the event — and the envelope of a consent event carrying no `purpose` key is perfectly well-formed,
    so they would find nothing wrong there and conclude the refusal was spurious."""
    event = _consent_event()
    if absent:
        del event["payload"][field]
    else:
        event["payload"][field] = None
    _rehash(event)

    with pytest.raises(AmhMappingError) as exc:
        project_consent_decision(map_consent_event(event, pin=pin))
    assert exc.value.field == field
    assert "payload" in exc.value.reason, exc.value.reason
    assert "envelope" not in exc.value.reason, exc.value.reason


@pytest.mark.parametrize("field", ["event_id", "consent_decision_ref", "purpose_of_use"])
@pytest.mark.parametrize("absent", [True, False], ids=["absent", "null"])
def test_an_envelope_refusal_still_names_the_envelope_half(pin: Any, field: str, absent: bool) -> None:
    """The other half of LOW-7, and the non-vacuity for it: the two halves must be DISTINGUISHABLE, so
    an envelope refusal must keep saying "envelope" rather than both becoming one indistinct word."""
    event = _work_item_event()
    if absent:
        del event[field]
    else:
        event[field] = None

    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == field
    assert "envelope" in exc.value.reason, exc.value.reason


@pytest.mark.parametrize("value", ["1", 1.5, True, None])
def test_a_non_integer_consent_revision_is_refused(pin: Any, value: object) -> None:
    """XRD-10 mandates a business-revision guard on the consuming side; a revision that is not an
    integer cannot be compared, so it is refused rather than coerced."""
    event = _consent_event()
    event["payload"]["consent_revision"] = value
    _rehash(event)
    with pytest.raises(AmhMappingError, match="consent_revision"):
        project_consent_decision(map_consent_event(event, pin=pin))


def test_consent_decision_exposes_no_scope() -> None:
    """Scope-to-authorisation mapping is the contract manifest's authority (ADR-0037 XRD-04), so the
    projection must not invent one even though the wire carries `scope`."""
    import dataclasses

    from maezo.ports.consent import ConsentDecision

    names = {f.name for f in dataclasses.fields(ConsentDecision)}
    assert "scope" not in names


# ---------------------------------------------------------------------------
# Outcome egress
# ---------------------------------------------------------------------------


def test_outcome_round_trips_to_the_pinned_wire_form(pin: Any) -> None:
    original = _outcome_event()
    wire = outcome_to_wire(map_outcome(original, pin=pin), pin=pin)
    assert wire == original


def test_outcome_wire_form_emits_the_pinned_field_order_with_payload_last(pin: Any) -> None:
    wire = outcome_to_wire(map_outcome(_outcome_event(), pin=pin), pin=pin)
    assert list(wire) == [*ENVELOPE_FIELD_ORDER, "payload"]


def test_outcome_egress_emits_timestamps_as_millis_integers(pin: Any) -> None:
    wire = outcome_to_wire(map_outcome(_outcome_event(), pin=pin), pin=pin)
    assert isinstance(wire["occurred_at"], int)
    assert not isinstance(wire["occurred_at"], bool)
    assert wire["occurred_at"] == _outcome_event()["occurred_at"]


def test_outcome_egress_emits_source_position_as_the_nested_object(pin: Any) -> None:
    wire = outcome_to_wire(map_outcome(_outcome_event(), pin=pin), pin=pin)
    assert wire["source_position"] == {
        "kind": "scn",
        "value": "128734660544",
        "transaction_ref": None,
    }


def test_outcome_egress_refuses_a_payload_hash_that_does_not_bind_its_payload(pin: Any) -> None:
    """DL-0038 applied to egress: do not emit an event you have not verified. `payload_hash` is a
    caller-supplied field, so an outcome CAN be built self-contradicting — publishing it would hand
    the consumer a CONTRACT_VIOLATION that originated here."""
    import dataclasses

    outcome = map_outcome(_outcome_event(), pin=pin)
    tampered = dataclasses.replace(outcome, payload={**outcome.payload, "outcome_status": "denied"})
    with pytest.raises(AmhMappingError) as exc:
        outcome_to_wire(tampered, pin=pin)
    assert exc.value.field == "payload_hash"


def test_outcome_egress_refuses_an_unknown_major(pin: Any) -> None:
    import dataclasses

    outcome = dataclasses.replace(map_outcome(_outcome_event(), pin=pin), canonical_schema_version="2.0.0")
    with pytest.raises(AmhMappingError, match="canonical_schema_version"):
        outcome_to_wire(outcome, pin=pin)


def test_outcome_egress_refuses_a_source_product_outside_the_vocabulary(pin: Any) -> None:
    import dataclasses

    outcome = dataclasses.replace(map_outcome(_outcome_event(), pin=pin), source_product="tasy")
    with pytest.raises(AmhMappingError, match="source_product"):
        outcome_to_wire(outcome, pin=pin)


def test_outcome_egress_refuses_a_naive_timestamp(pin: Any) -> None:
    import dataclasses

    outcome = dataclasses.replace(
        map_outcome(_outcome_event(), pin=pin),
        occurred_at=datetime(2026, 8, 4, 10, 0, 0),  # noqa: DTZ001
    )
    with pytest.raises(AmhMappingError, match="naive"):
        outcome_to_wire(outcome, pin=pin)


def test_outcome_egress_returns_a_detached_payload_copy(pin: Any) -> None:
    """The emitted mapping must not alias the canonical value's payload — a later mutation of the wire
    dict would otherwise silently rewrite an already-audited canonical outcome."""
    outcome = map_outcome(_outcome_event(), pin=pin)
    wire = outcome_to_wire(outcome, pin=pin)
    wire["payload"]["outcome_status"] = "mutated"
    assert outcome.payload["outcome_status"] == "returned_for_information"


# ---------------------------------------------------------------------------
# Non-disclosure (decision 5)
# ---------------------------------------------------------------------------


#: Envelope fields whose value is structurally constrained (a semver, a closed vocabulary token, a
#: millis integer, a nested object). The sentinel cannot be planted in these without changing WHICH
#: check fires, so each is broken by its own case below instead.
_STRUCTURED_FIELDS: frozenset[str] = frozenset(
    {
        "canonical_schema_version",
        "source_product",
        "occurred_at",
        "ingested_at",
        "replay_count",
        "source_position",
    }
)


def _phi_laden_event() -> dict[str, Any]:
    """A work item with the sentinel planted in every FREE-FORM slot — i.e. every opaque reference and
    the whole payload — while the structurally-constrained fields stay valid so that a case can choose
    exactly which refusal path it exercises."""
    event = _work_item_event()
    for field in ENVELOPE_FIELD_ORDER:
        if field not in _STRUCTURED_FIELDS:
            event[field] = PHI_SENTINEL
    event["source_position"] = {
        "kind": "scn",
        "value": PHI_SENTINEL,
        "transaction_ref": PHI_SENTINEL,
    }
    event["payload"] = {"action_summary": PHI_SENTINEL, "workflow_business_ref": PHI_SENTINEL}
    return event


def _failure_cases(pin: Any) -> list[tuple[str, Any]]:
    """One callable per distinct refusal path. Each event carries the sentinel in its free-form fields
    AND its payload, so whichever path fires has PHI-shaped data in hand at the moment it refuses."""
    import dataclasses

    cases: list[tuple[str, Any]] = []

    bad_product = _phi_laden_event()
    bad_product["source_product"] = "tasy"  # outside the closed vocabulary
    cases.append(("source_product vocabulary", lambda: map_work_item(bad_product, pin=pin)))

    bad_major = _phi_laden_event()
    bad_major["canonical_schema_version"] = "2.0.0"
    cases.append(("unknown major", lambda: map_work_item(bad_major, pin=pin)))

    bad_semver = _phi_laden_event()
    bad_semver["canonical_schema_version"] = PHI_SENTINEL
    cases.append(("malformed schema version", lambda: map_work_item(bad_semver, pin=pin)))

    bad_hash = _phi_laden_event()
    bad_hash["source_product"] = "tasy_hospital"
    cases.append(("payload_hash mismatch", lambda: map_work_item(bad_hash, pin=pin)))

    missing = _phi_laden_event()
    del missing["consent_decision_ref"]
    cases.append(("missing required field", lambda: map_work_item(missing, pin=pin)))

    null_field = _phi_laden_event()
    null_field["legal_entity"] = None
    cases.append(("null required field", lambda: map_work_item(null_field, pin=pin)))

    mistyped = _phi_laden_event()
    mistyped["portable_subject_ref"] = {"cpf": PHI_SENTINEL}
    cases.append(("mistyped reference", lambda: map_work_item(mistyped, pin=pin)))

    bad_ts = _phi_laden_event()
    bad_ts["occurred_at"] = PHI_SENTINEL
    cases.append(("bad timestamp", lambda: map_work_item(bad_ts, pin=pin)))

    naive_ts = _phi_laden_event()
    naive_ts["occurred_at"] = datetime(2026, 8, 4, 10, 0, 0)  # noqa: DTZ001
    cases.append(("naive timestamp", lambda: map_work_item(naive_ts, pin=pin)))

    bad_count = _phi_laden_event()
    bad_count["replay_count"] = PHI_SENTINEL
    cases.append(("non-integer replay_count", lambda: map_work_item(bad_count, pin=pin)))

    bad_position = _phi_laden_event()
    bad_position["source_position"] = {"kind": PHI_SENTINEL}
    cases.append(("missing source_position subfield", lambda: map_work_item(bad_position, pin=pin)))

    scalar_position = _phi_laden_event()
    scalar_position["source_position"] = PHI_SENTINEL
    cases.append(("non-object source_position", lambda: map_work_item(scalar_position, pin=pin)))

    bad_payload = _phi_laden_event()
    bad_payload["payload"] = PHI_SENTINEL
    cases.append(("non-object payload", lambda: map_work_item(bad_payload, pin=pin)))

    no_payload = _phi_laden_event()
    del no_payload["payload"]
    cases.append(("missing payload", lambda: map_work_item(no_payload, pin=pin)))

    bad_decision = _consent_event()
    bad_decision["payload"]["decision"] = PHI_SENTINEL
    _rehash(bad_decision)
    cases.append(
        (
            "unknown consent decision token",
            lambda: project_consent_decision(map_consent_event(bad_decision, pin=pin)),
        )
    )

    bad_revision = _consent_event()
    bad_revision["payload"]["consent_revision"] = PHI_SENTINEL
    _rehash(bad_revision)
    cases.append(
        (
            "non-integer consent revision",
            lambda: project_consent_decision(map_consent_event(bad_revision, pin=pin)),
        )
    )

    # --- the four refusal paths added by the MZO-050a repair -------------------------------------
    overflowing = _phi_laden_event()
    overflowing["occurred_at"] = 10**17
    cases.append(("overflowing epoch millis", lambda: map_work_item(overflowing, pin=pin)))

    unserialisable = _phi_laden_event()
    # The sentinel as Avro-shaped `bytes`: still detectable by the assertions, since `repr(b"SENTINEL…")`
    # contains the sentinel text.
    unserialisable["payload"] = {"attachment": PHI_SENTINEL.encode()}
    cases.append(("unserialisable payload value", lambda: map_work_item(unserialisable, pin=pin)))

    non_finite = _phi_laden_event()
    non_finite["payload"] = {"action_summary": PHI_SENTINEL, "score": float("nan")}
    cases.append(("non-finite float in payload", lambda: map_work_item(non_finite, pin=pin)))

    sub_ms_payload = {"action_summary": PHI_SENTINEL, "workflow_business_ref": PHI_SENTINEL}
    sub_ms = dataclasses.replace(
        map_outcome(_outcome_event(), pin=pin),
        payload=sub_ms_payload,
        payload_hash=canonical_payload_hash(sub_ms_payload),
        occurred_at=datetime(2026, 8, 4, 10, 0, 0, 250, tzinfo=UTC),
        portable_subject_ref=PHI_SENTINEL,
    )
    cases.append(("sub-millisecond egress instant", lambda: outcome_to_wire(sub_ms, pin=pin)))

    non_strict_version = _phi_laden_event()
    non_strict_version["canonical_schema_version"] = "².0.0"
    cases.append(("non-strict semver", lambda: map_work_item(non_strict_version, pin=pin)))

    return cases


def test_no_exception_discloses_a_wire_value(pin: Any) -> None:
    """ADR-0037 immutable prohibition #5. This is the direct counter-example to
    `MalformedBridgeMessageError` in `src/maezo/platform/integrations/notifications_bridge.py`, which
    interpolates the whole raw message into its text (`f"...: {raw!r}"`) AND retains it as
    `self.raw`. On this boundary that text reaches logs, traces and quarantine metadata."""
    import traceback

    cases = _failure_cases(pin)
    assert len(cases) >= 21, "non-vacuity: every distinct refusal path must be covered"

    for label, call in cases:
        with pytest.raises(AmhMappingError) as exc:
            call()
        error = exc.value
        for rendered in (str(error), repr(error), repr(error.args), error.reason, error.field):
            assert PHI_SENTINEL not in rendered, f"{label}: value disclosed in {rendered!r}"
        # A chained cause would carry the wire value into any handler that logs a traceback, so the
        # FORMATTED traceback is checked too — not just the exception's own text.
        formatted = "".join(traceback.format_exception(error))
        assert PHI_SENTINEL not in formatted, f"{label}: value disclosed via the chained traceback"


def test_exceptions_retain_no_reference_to_the_event(pin: Any) -> None:
    """The other half of the bridge hazard: it kept `self.raw`. An exception object that holds the
    event would leak it through any handler that logs `exc.__dict__` or a traceback frame dump."""
    for label, call in _failure_cases(pin):
        with pytest.raises(AmhMappingError) as exc:
            call()
        stored = vars(exc.value)
        assert set(stored) == {"field", "reason"}, f"{label}: unexpected attributes {sorted(stored)}"
        for value in stored.values():
            assert isinstance(value, str), f"{label}: non-string attribute retained"
            assert PHI_SENTINEL not in value, f"{label}: sentinel retained on the exception"


def test_the_sentinel_would_actually_be_caught_if_disclosed(pin: Any) -> None:
    """NON-VACUITY for the two tests above: they are `assert sentinel not in text` assertions, which
    pass trivially if the sentinel never reaches the code under test. Prove the sentinel IS present in
    the events being rejected, and that the same assertion FAILS against a deliberately-disclosing
    exception of the hazardous shape."""
    bad_product = _phi_laden_event()
    serialised = json.dumps(bad_product, default=str)
    assert serialised.count(PHI_SENTINEL) >= 20, (
        "the sentinel is barely present in the event under test, so 'not in the message' proves "
        f"little (found {serialised.count(PHI_SENTINEL)} occurrences)"
    )

    # The hazardous shape, spelled out: this is what notifications_bridge does.
    class _DisclosingError(Exception):
        def __init__(self, raw: Mapping[str, Any]) -> None:
            self.raw = raw
            super().__init__(f"malformed message: {raw!r}")

    leaky = _DisclosingError(bad_product)
    assert PHI_SENTINEL in str(leaky), "the non-disclosure assertion cannot detect a leak"
    assert PHI_SENTINEL in repr(vars(leaky))


def test_mapping_error_is_an_adapter_error() -> None:
    assert issubclass(AmhMappingError, AmhAdapterError)


def test_mapping_error_exposes_the_field_name_for_operator_triage(pin: Any) -> None:
    """Non-disclosure must not become non-diagnosability: the field NAME is not data, and an operator
    needs it to route a quarantined event."""
    event = _work_item_event()
    event["source_product"] = "tasy"
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "source_product"
    assert "source_product" in str(exc.value)
    assert "tasy_hospital" in str(exc.value), "the PINNED vocabulary is public and aids triage"


# ---------------------------------------------------------------------------
# Round-2 regressions: the remaining port-boundary escapes
#
# Every one is the SAME defect as MAJOR-2 above, found again where the round-1 repair did not sweep.
# Each closure is pinned individually here; the EXHAUSTIVENESS proof — that no other stdlib call on
# either public path can escape — lives in `test_no_foreign_exception_escapes.py`.
# ---------------------------------------------------------------------------


def test_a_lone_surrogate_in_the_payload_is_refused_not_encoded() -> None:
    """`json.dumps` was guarded and `encoded.encode("utf-8")` on the NEXT LINE was not — the `try`
    closed one line early, so a `UnicodeEncodeError` crossed the port boundary.

    Reachable from ordinary JSON wire bytes: `json.loads` accepts `"\\ud800"` without complaint, and
    `ensure_ascii=False` (decision 2, upheld) is what carries it verbatim into the encode. Refused
    rather than forced through with `errors="surrogatepass"`: that would invent a canonical form for a
    character UTF-8 cannot express, and AMH's own canonicalisation is not proven to match, so the hash
    would be compared against a producer hash that cannot exist."""
    payload = json.loads('{"a": "\\ud800"}')
    assert payload == {"a": "\ud800"}, "the surrogate must survive json.loads for this to be the case"
    with pytest.raises(AmhMappingError) as exc:
        canonical_payload_hash(payload)
    assert exc.value.field == "payload"
    assert "UTF-8" in exc.value.reason and "surrogate" in exc.value.reason
    assert exc.value.__cause__ is None, "no chained message: it quotes the offending character"
    assert exc.value.__suppress_context__ is True


def test_a_lone_surrogate_is_refused_through_the_real_mapping_path(pin: Any) -> None:
    """Non-vacuity: reachable from a port implementation, not only from the hash helper.

    No `_rehash` here, deliberately — the helper computes the canonical hash, which is exactly the call
    that refuses, so a rehash could not be reached. The declared `payload_hash` is left as the fixture's
    and the refusal happens inside `_check_payload_hash` before any comparison."""
    event = {**_work_item_event(), "payload": json.loads('{"a": "\\ud800"}')}
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "payload"
    assert "UTF-8" in exc.value.reason, "the surrogate refusal, not a hash MISMATCH"


def test_a_surrogate_is_distinguished_from_an_unserialisable_type() -> None:
    """The two refusals must not collapse into one reason: a lone surrogate is a producer ENCODING
    fault and an `avro bytes` value is a payload TYPE fault, and an operator triaging a quarantined
    delivery acts differently on each."""
    with pytest.raises(AmhMappingError) as surrogate:
        canonical_payload_hash({"a": "\ud800"})
    with pytest.raises(AmhMappingError) as unserialisable:
        canonical_payload_hash({"a": b"\x00"})
    assert surrogate.value.reason != unserialisable.value.reason


def _nested_dict(depth: int) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current = root
    for _ in range(depth):
        nxt: dict[str, Any] = {}
        current["a"] = nxt
        current = nxt
    return root


def test_a_payload_nested_past_the_encoder_depth_is_refused() -> None:
    """`RecursionError` is neither a `TypeError` nor a `ValueError`, so the original two-type guard did
    not hold it. JSON ingress happens to be self-limiting (`json.loads` and `json.dumps` share a
    budget), but the module contract admits an Avro-decoded dict and Avro nesting has no such gate."""
    with pytest.raises(AmhMappingError) as exc:
        canonical_payload_hash(_nested_dict(20_000))
    assert exc.value.field == "payload"
    assert "cannot represent" in exc.value.reason


def test_a_shallow_payload_still_hashes_so_the_depth_guard_is_not_a_blanket_refusal() -> None:
    """Non-vacuity in the other direction: the guard must not have turned legitimate nesting into a
    refusal. 100 levels is far beyond anything the fixtures carry and must still hash."""
    assert len(canonical_payload_hash(_nested_dict(100))) == 64


class _RaisingTzinfo(tzinfo):
    """A `tzinfo` whose `utcoffset` raises a foreign exception carrying a PHI-shaped message."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise RuntimeError(PHI_SENTINEL)


class _WrongTypeTzinfo(tzinfo):
    """A `tzinfo` returning a non-`timedelta`. Passes the `is not None` offset check and fails inside
    `astimezone` — one layer DEEPER than the call the round-2 finding named, which is why the fix
    needed a second clause in `_to_utc` and not only the offset helper."""

    def utcoffset(self, dt: datetime | None) -> Any:
        return PHI_SENTINEL


@pytest.mark.parametrize("tz_factory", [_RaisingTzinfo, _WrongTypeTzinfo], ids=["raises", "wrong type"])
def test_a_hostile_tzinfo_cannot_leak_through_any_timestamp_entry_point(tz_factory: Any) -> None:
    """`value.tzinfo.utcoffset(value)` ran outside any guard in BOTH `millis_to_utc` and
    `utc_to_millis`, so a foreign exception propagated verbatim. That makes it the last DISCLOSURE
    channel on this boundary, not merely a type violation: the exception's MESSAGE crossed too, and
    decision 5 forbids that whatever the type."""
    hostile = datetime(2026, 8, 4, 10, 0, tzinfo=tz_factory())
    for label, call in (
        ("millis_to_utc", lambda: millis_to_utc(hostile, field="occurred_at")),
        ("utc_to_millis", lambda: utc_to_millis(hostile, field="occurred_at")),
        ("truncate_to_wire_millis", lambda: truncate_to_wire_millis(hostile)),
    ):
        with pytest.raises(AmhMappingError) as exc:
            call()
        assert exc.value.field == "occurred_at", label
        assert PHI_SENTINEL not in str(exc.value), f"{label} disclosed the foreign message"
        assert PHI_SENTINEL not in repr(vars(exc.value)), f"{label} retained the foreign message"
        assert exc.value.__cause__ is None, label


def test_a_hostile_tzinfo_is_refused_on_the_real_egress_path(pin: Any) -> None:
    """Non-vacuity: reachable through `outcome_to_wire`, i.e. from a port implementation."""
    outcome = dataclasses.replace(
        map_outcome(_outcome_event(), pin=pin),
        occurred_at=datetime(2026, 8, 4, 10, 0, tzinfo=_RaisingTzinfo()),
    )
    with pytest.raises(AmhMappingError) as exc:
        outcome_to_wire(outcome, pin=pin)
    assert exc.value.field == "occurred_at"
    assert PHI_SENTINEL not in str(exc.value)


def test_a_mistyped_outcome_timestamp_is_refused_on_egress(pin: Any) -> None:
    """`CanonicalOutcome` is a plain frozen dataclass with NO runtime validation, so a caller can hand
    `outcome_to_wire` an `occurred_at` that is not a datetime at all — and `.tzinfo` on an int was a
    bare `AttributeError`. Refused for the same reason egress already refuses a caller-supplied
    `payload_hash` that does not bind its own payload: it treats its caller as fallible."""
    outcome = dataclasses.replace(map_outcome(_outcome_event(), pin=pin), occurred_at=1785837600000)
    with pytest.raises(AmhMappingError) as exc:
        outcome_to_wire(outcome, pin=pin)
    assert exc.value.field == "occurred_at"
    assert "tz-aware datetime" in exc.value.reason


def test_a_schema_version_past_the_int_conversion_limit_is_refused(pin: Any) -> None:
    """The wire `canonical_schema_version` is an Avro `string`, so a 5000-digit major is
    schema-CONFORMANT — and the old unbounded `[0-9]*` let the regex match a digit run that
    `parse_semver`'s own `int()` then refused, raising a bare `ValueError` through `map_work_item`."""
    over_limit = "1" * (sys.get_int_max_str_digits() + 1)
    event = {**_work_item_event(), "canonical_schema_version": f"{over_limit}.0.0"}
    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=pin)
    assert exc.value.field == "canonical_schema_version"
    assert "MAJOR.MINOR.PATCH" in exc.value.reason


def test_a_version_at_the_component_bound_is_accepted_and_one_past_it_is_not(pin: Any) -> None:
    """The bound is a real edge, not a vague "big number" refusal, so pin exactly nine digits and
    exactly ten. Both are refused HERE but for DIFFERENT reasons (an unknown major vs an unparseable
    version), so assert the reason rather than merely that it raised."""
    nine = "9" * MAX_SEMVER_COMPONENT_DIGITS
    assert parse_semver(f"{nine}.0.0") == (int(nine), 0, 0)
    assert parse_semver(f"9{nine}.0.0") is None

    with pytest.raises(AmhMappingError) as at_bound:
        map_work_item({**_work_item_event(), "canonical_schema_version": f"{nine}.0.0"}, pin=pin)
    assert "unknown major" in at_bound.value.reason, "nine digits parses; the major is simply not 1"

    with pytest.raises(AmhMappingError) as past_bound:
        map_work_item({**_work_item_event(), "canonical_schema_version": f"9{nine}.0.0"}, pin=pin)
    assert "MAJOR.MINOR.PATCH" in past_bound.value.reason


# ---------------------------------------------------------------------------
# truncate_to_wire_millis — the sanctioned way to satisfy the sub-millisecond refusal
# ---------------------------------------------------------------------------


def test_the_sub_millisecond_refusal_is_still_in_force() -> None:
    """Decision 10 is UPHELD. The helper below exists so a caller can COMPLY with it, never to soften
    it — assert the refusal first so a later reading of these tests cannot mistake one for the other."""
    with pytest.raises(AmhMappingError) as exc:
        utc_to_millis(datetime(2026, 8, 4, 10, 0, 0, 1, tzinfo=UTC), field="occurred_at")
    assert "sub-millisecond" in exc.value.reason


def test_truncate_makes_a_clock_instant_publishable() -> None:
    """The gap the helper closes: only ~0.07% of `datetime.now(UTC)` instants are millis-exact, so a
    phase-B publisher stamping an outcome from the clock is refused ~999 times in 1000. With no
    exported operation, each call site improvises its own truncation and re-creates the skew."""
    now = datetime.now(UTC)
    assert utc_to_millis(truncate_to_wire_millis(now), field="occurred_at") == (
        (now - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)
    )


@pytest.mark.parametrize(
    ("label", "value", "expected_millis"),
    [
        ("post-epoch", datetime(2026, 8, 4, 10, 0, 0, 999_999, tzinfo=UTC), 1785837600999),
        ("already exact", datetime(2026, 8, 4, 10, 0, 0, 1000, tzinfo=UTC), 1785837600001),
        ("epoch itself", datetime(1970, 1, 1, tzinfo=UTC), 0),
        ("1us before the epoch", datetime(1969, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC), -1),
        ("well before the epoch", datetime(1960, 6, 1, 12, 0, 0, 1, tzinfo=UTC), -302443200000),
    ],
)
def test_truncation_goes_toward_the_past_on_both_sides_of_the_epoch(
    label: str, value: datetime, expected_millis: int
) -> None:
    """The DIRECTION is the whole reason this is exported rather than left to each call site. A
    toward-zero reading of the same floor division truncates a pre-epoch instant AWAY from the epoch
    and a post-epoch one TOWARD it — the sign-dependent skew decision 10 refused to commit silently.
    Floor-toward-the-past is identical in sign, which is what makes it one sentence to a publisher.

    `1969-12-31T23:59:59.999999Z` is the discriminating vector: floor gives -1 (one millisecond BEFORE
    the epoch); toward-zero would give 0."""
    truncated = truncate_to_wire_millis(value)
    assert utc_to_millis(truncated, field="occurred_at") == expected_millis, label
    assert truncated <= value, "truncation never moves an instant forward"
    assert value - truncated < timedelta(milliseconds=1), "it moves it less than one millisecond"


def test_truncation_is_idempotent_and_normalises_to_utc() -> None:
    brasilia = timezone(timedelta(hours=-3))
    once = truncate_to_wire_millis(datetime(2026, 8, 4, 7, 0, 0, 500_500, tzinfo=brasilia))
    assert once.utcoffset() == timedelta(0), "the result is UTC, like every other timestamp here"
    assert truncate_to_wire_millis(once) == once
    assert utc_to_millis(once, field="occurred_at") == 1785837600500


def test_truncation_refuses_a_naive_datetime_exactly_as_egress_does() -> None:
    """A caller must be able to route both refusals identically, so the helper is not a laxer door into
    the boundary than `utc_to_millis` itself."""
    with pytest.raises(AmhMappingError) as exc:
        truncate_to_wire_millis(datetime(2026, 8, 4, 10, 0))
    assert "naive datetime" in exc.value.reason


def test_truncating_at_the_datetime_bounds_cannot_overflow_because_the_grid_is_aligned() -> None:
    """Flooring moves an instant EARLIER, so the obvious worry is a value within 1ms of `datetime.min`
    flooring underneath it. It cannot, and the reason is arithmetic rather than a guard: `_EPOCH -
    datetime.min` is exactly 719162 days — a whole number of milliseconds — and `datetime.min` carries
    microsecond 0, so `datetime.min` sits exactly ON the epoch-millisecond grid.

    This test exists because the first version of the helper carried a `try/except OverflowError` here,
    and that guard was DEAD CODE: no input could reach it. The proof is pinned instead, so a later
    change to `_EPOCH` that broke the alignment fails here rather than silently reintroducing the
    overflow the removed guard was pretending to cover."""
    assert (datetime(1970, 1, 1, tzinfo=UTC) - datetime.min.replace(tzinfo=UTC)) % timedelta(
        milliseconds=1
    ) == timedelta(0), "the epoch-millisecond grid must stay aligned with datetime.min"

    floor_of_min = truncate_to_wire_millis(datetime.min.replace(tzinfo=UTC) + timedelta(microseconds=500))
    assert floor_of_min == datetime.min.replace(tzinfo=UTC), "floors ONTO datetime.min, never below it"

    ceiling = truncate_to_wire_millis(datetime.max.replace(tzinfo=UTC))
    assert ceiling <= datetime.max.replace(tzinfo=UTC)
    assert truncate_to_wire_millis(datetime.min.replace(tzinfo=UTC)) == datetime.min.replace(tzinfo=UTC)


def test_truncate_is_exported_from_the_package_not_just_the_module() -> None:
    """A helper the docstrings tell callers to reach for must be importable the way callers import."""
    import maezo.adapters.amh as amh_pkg
    from maezo.adapters.amh import mapping as mapping_module

    assert "truncate_to_wire_millis" in mapping_module.__all__
    assert "truncate_to_wire_millis" in amh_pkg.__all__
    assert amh_pkg.truncate_to_wire_millis is truncate_to_wire_millis
