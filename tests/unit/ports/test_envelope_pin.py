"""Pin-anchored test: the ports' envelope constant IS the frozen contract envelope (ADR-0037).

The payer core holds exactly ONE editable statement of the canonical envelope shape —
`maezo.ports.envelope.ENVELOPE_FIELD_ORDER` and the declaration order of `CanonicalEnvelope`. The
AUTHORITY for that shape is the immutable contract pin,
`config/integrations/amh/contracts.lock.json` (`envelope.field_order` / `envelope.field_count`),
which is contract-owner-owned, NOT hand-editable, and gated in CI by
`scripts/ci/verify_amh_contract_pin.py` (ADR-0037 XRD-04, MZO-010/XRG-3).

This module reads the lock file DIRECTLY — no fixture copy, no constant re-typed by hand, no
indirection through another test helper — and asserts equality. Consequences by construction:

- A local edit to the ports' field order fails here (the pin does not move for a Maezo edit).
- A NEW contract publication that moves the pin fails here until the ports are updated to mirror
  it, which is the review checkpoint a versioned contract change is supposed to get.
- The three boundary event types (`CanonicalWorkItem`, `CanonicalConsentEvent`,
  `CanonicalOutcome`) are proven to be "the 28 pinned fields, in order, plus an opaque payload" —
  so a field cannot be inserted, dropped or transposed in one of them without failing.

Field ORDER (not just the field set) is asserted because the pin freezes an order, and a silent
transposition of two same-typed reference fields is exactly the class of defect that would send a
subject reference into a correlation slot.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from maezo.ports.consent import CanonicalConsentEvent
from maezo.ports.envelope import ENVELOPE_FIELD_COUNT, ENVELOPE_FIELD_ORDER, CanonicalEnvelope
from maezo.ports.outcomes import CanonicalOutcome
from maezo.ports.work_items import CanonicalWorkItem

# tests/unit/ports/<this file> -> parents[3] is the repository root.
_LOCK_PATH = Path(__file__).resolve().parents[3] / "config" / "integrations" / "amh" / "contracts.lock.json"


def _lock() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
    return payload


def test_lock_file_is_present_and_readable() -> None:
    """Non-vacuity guard: every assertion below is meaningless if the pin cannot be read."""
    assert _LOCK_PATH.is_file(), f"contract pin not found at {_LOCK_PATH}"
    envelope = _lock()["envelope"]
    assert isinstance(envelope["field_order"], list) and envelope["field_order"], (
        "the pin's envelope.field_order must be a non-empty list"
    )


def test_ports_envelope_field_order_equals_the_pinned_order() -> None:
    """THE cross-check: the ports' single constant == the pin's frozen order, element for element."""
    pinned = tuple(_lock()["envelope"]["field_order"])
    assert pinned == ENVELOPE_FIELD_ORDER, (
        "maezo.ports.envelope.ENVELOPE_FIELD_ORDER drifted from the immutable contract pin "
        f"(config/integrations/amh/contracts.lock.json). ports={ENVELOPE_FIELD_ORDER!r} pin={pinned!r}"
    )


def test_ports_envelope_field_count_equals_the_pinned_count() -> None:
    """Both pinned facts are asserted independently, so neither can be derived from the other."""
    assert ENVELOPE_FIELD_COUNT == _lock()["envelope"]["field_count"] == 28
    assert len(ENVELOPE_FIELD_ORDER) == ENVELOPE_FIELD_COUNT


def test_canonical_envelope_declaration_order_is_the_pinned_order() -> None:
    """The dataclass itself — not only the constant — mirrors the pin, in order."""
    declared = tuple(f.name for f in fields(CanonicalEnvelope))
    assert declared == ENVELOPE_FIELD_ORDER, (
        f"CanonicalEnvelope field order drifted from ENVELOPE_FIELD_ORDER: {declared!r}"
    )


def test_every_boundary_event_is_the_pinned_envelope_plus_an_opaque_payload() -> None:
    """The three canonical event types share ONE envelope declaration (no per-topic copy that
    could drift) and add exactly one field: the opaque payload."""
    for event_type in (CanonicalWorkItem, CanonicalConsentEvent, CanonicalOutcome):
        declared = tuple(f.name for f in fields(event_type))
        assert declared[:ENVELOPE_FIELD_COUNT] == ENVELOPE_FIELD_ORDER, (
            f"{event_type.__name__} does not start with the pinned envelope: {declared!r}"
        )
        assert declared[ENVELOPE_FIELD_COUNT:] == ("payload",), (
            f"{event_type.__name__} must add exactly one field, `payload`: {declared!r}"
        )


def test_optional_envelope_fields_are_exactly_the_two_the_contract_declares_optional() -> None:
    """ADR-0037's baseline marks `amh_mpi_ref` and `beneficiary_ref` optional and nothing else.
    A defaulted field is a field an adapter can silently omit — so the set of defaulted fields is
    itself part of the contract surface and is pinned here."""
    optional = {f.name for f in fields(CanonicalEnvelope) if f.default is not dataclasses.MISSING}
    assert optional == {"amh_mpi_ref", "beneficiary_ref"}, (
        f"unexpected optional envelope fields: {sorted(optional)}"
    )
    assert all(f.default is None for f in fields(CanonicalEnvelope) if f.name in optional)


def test_idempotency_key_is_structurally_required_on_every_boundary_event() -> None:
    """DL-0038 posture: an outcome cannot be constructed without an idempotency key, so a publisher
    can never be handed one to publish without it."""
    for event_type in (CanonicalWorkItem, CanonicalConsentEvent, CanonicalOutcome):
        key_field = next(f for f in fields(event_type) if f.name == "idempotency_key")
        assert key_field.default is dataclasses.MISSING, (
            f"{event_type.__name__}.idempotency_key must have no default (required)"
        )
        assert key_field.default_factory is dataclasses.MISSING
