"""Consumer contract tests: the mapping layer against the REAL digest-gated AMH fixtures (MZO-050a).

The fixtures in `tests/contract/amh/fixtures/` are AMH-published, synthetic, non-PHI, and gated by
sha256 in the pin (`make verify-amh-contract-pin` recomputes every digest and refuses an unlisted
file). They are therefore the ONLY honest source of truth for wire shape available to this repo,
which is why the mapping is driven off them here rather than off hand-written dicts: a hand-written
vector proves the mapper agrees with the test author, not with the contract owner.

Every fixture is loaded through its `vendored_path` AS REPORTED BY THE LOADED PIN, and each file's
digest is re-verified here before use — so a locally-edited fixture fails this suite too, not only the
CI gate.

The `*.invalid.json` vectors are the important half. Each carries a violation "a Maezo consumer can
detect and REJECT" (the existing `test_contract_pin.py`'s words), and this module proves the phase-A
mapper is that consumer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh.contract import AmhContractPin, load_contract_pin
from maezo.adapters.amh.mapping import (
    AmhMappingError,
    canonical_payload_hash,
    map_consent_event,
    map_outcome,
    map_work_item,
    outcome_to_wire,
    project_consent_decision,
)
from maezo.ports.consent import CanonicalConsentEvent
from maezo.ports.envelope import ENVELOPE_FIELD_ORDER
from maezo.ports.outcomes import CanonicalOutcome
from maezo.ports.work_items import CanonicalWorkItem

REPO_ROOT = Path(__file__).resolve().parents[3]

PIN: AmhContractPin = load_contract_pin(REPO_ROOT / "config/integrations/amh/contracts.lock.json")

WORK_ITEM_FIXTURES = (
    "tests/contract/amh/fixtures/work_item.authorization_review.json",
    "tests/contract/amh/fixtures/work_item.eligibility_check.json",
)
CONSENT_FIXTURES = (
    "tests/contract/amh/fixtures/consent.granted.json",
    "tests/contract/amh/fixtures/consent.revoked.json",
)
OUTCOME_FIXTURES = (
    "tests/contract/amh/fixtures/outcome.revision-1.json",
    "tests/contract/amh/fixtures/outcome.revision-2.json",
)
VALID_FIXTURES = WORK_ITEM_FIXTURES + CONSENT_FIXTURES + OUTCOME_FIXTURES


def _load(vendored_path: str) -> dict[str, Any]:
    """Read a fixture, re-verifying its pinned digest first."""
    assert vendored_path in PIN.fixture_digests, f"{vendored_path} is not a pinned fixture"
    path = REPO_ROOT / vendored_path
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    assert actual == PIN.fixture_digests[vendored_path], (
        f"{vendored_path}: sha256 {actual} != pinned {PIN.fixture_digests[vendored_path]} — the "
        "vendored bytes were edited locally; AMH-published fixtures are immutable (ADR-0037 XRD-04)"
    )
    parsed: dict[str, Any] = json.loads(raw.decode("utf-8"))
    return parsed


# ---------------------------------------------------------------------------
# The empirical payload_hash canonicalisation finding
# ---------------------------------------------------------------------------


def _hash_with(payload: Any, *, ensure_ascii: bool) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_ensure_ascii_false_is_the_form_that_reproduces_the_producer_hashes() -> None:
    """THE empirical finding, pinned in both directions.

    The pin declares the canonicalisation as "sha256 hex of the canonical JSON form of the payload
    field: UTF-8, sorted keys, compact separators" — and says NOTHING about `ensure_ascii`. Python's
    default is `True`, which escapes non-ASCII as `\\uXXXX` and therefore changes the bytes.

    The two work-item fixtures carry non-ASCII payload text, so they discriminate: their declared
    hashes reproduce ONLY under `ensure_ascii=False`. This test asserts BOTH halves — the correct form
    matches AND the wrong form does not — because "matches" alone would still pass if the two forms
    happened to agree, which is exactly the case for every ASCII-only fixture.
    """
    discriminating = 0
    for vendored_path in WORK_ITEM_FIXTURES:
        event = _load(vendored_path)
        payload = event["payload"]
        declared = event["payload_hash"]
        rendered = json.dumps(payload, ensure_ascii=False)
        assert any(ord(c) > 127 for c in rendered), f"{vendored_path} has no non-ASCII payload text"
        discriminating += 1

        assert _hash_with(payload, ensure_ascii=False) == declared, vendored_path
        assert _hash_with(payload, ensure_ascii=True) != declared, (
            f"{vendored_path}: the two canonicalisation forms agree, so this fixture cannot "
            "discriminate — the finding would be unproven"
        )
        assert canonical_payload_hash(payload) == declared, vendored_path

    assert discriminating == 2, "both non-ASCII work-item vectors must participate"


def test_every_valid_fixture_payload_hash_recomputes_under_the_shipped_canonicalisation() -> None:
    for vendored_path in VALID_FIXTURES:
        event = _load(vendored_path)
        assert canonical_payload_hash(event["payload"]) == event["payload_hash"], vendored_path


def test_ascii_only_fixtures_cannot_discriminate_which_is_why_the_non_ascii_ones_matter() -> None:
    """States the limit of the evidence explicitly rather than leaving it implied."""
    for vendored_path in CONSENT_FIXTURES + OUTCOME_FIXTURES:
        payload = _load(vendored_path)["payload"]
        rendered = json.dumps(payload, ensure_ascii=False)
        assert all(ord(c) <= 127 for c in rendered), vendored_path
        assert _hash_with(payload, ensure_ascii=True) == _hash_with(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Valid fixtures map cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vendored_path", WORK_ITEM_FIXTURES)
def test_valid_work_item_fixtures_map_to_canonical_work_items(vendored_path: str) -> None:
    event = _load(vendored_path)
    item = map_work_item(event, pin=PIN)

    assert isinstance(item, CanonicalWorkItem)
    for field in ENVELOPE_FIELD_ORDER:
        if field in {"occurred_at", "ingested_at", "source_position"}:
            continue
        assert getattr(item, field) == event[field], field
    assert item.payload == event["payload"]
    assert item.occurred_at.tzinfo is not None
    assert item.ingested_at.tzinfo is not None
    assert item.source_position.kind == event["source_position"]["kind"]
    assert item.source_position.value == event["source_position"]["value"]
    assert item.source_position.transaction_ref == event["source_position"]["transaction_ref"]


@pytest.mark.parametrize("vendored_path", CONSENT_FIXTURES)
def test_valid_consent_fixtures_map_and_project(vendored_path: str) -> None:
    event = _load(vendored_path)
    mapped = map_consent_event(event, pin=PIN)
    assert isinstance(mapped, CanonicalConsentEvent)

    decision = project_consent_decision(mapped)
    assert decision.consent_decision_ref == event["consent_decision_ref"]
    assert decision.portable_subject_ref == event["portable_subject_ref"]
    assert decision.purpose_of_use == event["payload"]["purpose"]
    assert decision.consent_revision == event["payload"]["consent_revision"]
    assert decision.granted is (event["payload"]["decision"] == "granted")
    assert decision.decided_at.tzinfo is not None


def test_the_consent_pair_demonstrates_revision_monotonicity() -> None:
    """The fixtures exist to show last-revision-wins for the SAME {subject, purpose, scope}; XRD-10
    requires a business-revision guard on the consuming side, which needs comparable revisions."""
    granted = project_consent_decision(map_consent_event(_load(CONSENT_FIXTURES[0]), pin=PIN))
    revoked = project_consent_decision(map_consent_event(_load(CONSENT_FIXTURES[1]), pin=PIN))
    assert granted.portable_subject_ref == revoked.portable_subject_ref
    assert granted.purpose_of_use == revoked.purpose_of_use
    assert revoked.consent_revision > granted.consent_revision
    assert granted.granted is True
    assert revoked.granted is False
    assert revoked.decided_at > granted.decided_at


@pytest.mark.parametrize("vendored_path", OUTCOME_FIXTURES)
def test_valid_outcome_fixtures_round_trip_byte_identically(vendored_path: str) -> None:
    """Ingress then egress must reproduce the AMH-published bytes' STRUCTURE exactly — same keys, same
    order, same values. A lossy mapping would show up here as a diff."""
    event = _load(vendored_path)
    outcome = map_outcome(event, pin=PIN)
    assert isinstance(outcome, CanonicalOutcome)

    wire = outcome_to_wire(outcome, pin=PIN)
    assert wire == event
    assert list(wire) == list(event), "key ORDER drifted from the published fixture layout"
    assert list(wire) == [*ENVELOPE_FIELD_ORDER, "payload"]


def test_the_outcome_pair_demonstrates_consecutive_business_revisions() -> None:
    first = map_outcome(_load(OUTCOME_FIXTURES[0]), pin=PIN)
    second = map_outcome(_load(OUTCOME_FIXTURES[1]), pin=PIN)
    assert second.payload["outcome_revision"] == first.payload["outcome_revision"] + 1
    # The fixtures README: revision 2's `causation_id` IS revision 1's `event_id`, and the two are
    # linked by `correlation_id`. Both survive the mapping verbatim.
    assert second.causation_id == first.event_id
    assert first.correlation_id == second.correlation_id
    assert second.occurred_at > first.occurred_at


def test_both_pinned_source_products_appear_across_the_fixtures() -> None:
    """Non-vacuity for the vocabulary gate: the valid vectors must exercise BOTH permitted values, or
    a mapper hardcoded to one of them would pass every test above."""
    seen = {_load(p)["source_product"] for p in VALID_FIXTURES}
    assert seen == {"tasy_hospital", "tasy_healthcare_plan"}
    for value in seen:
        assert PIN.is_known_source_product(value)


def test_optional_envelope_fields_are_exercised_in_both_states() -> None:
    """Non-vacuity: the fixtures must cover both a present and an absent optional reference."""
    mpi_states = {_load(p)["amh_mpi_ref"] is None for p in VALID_FIXTURES}
    beneficiary_states = {_load(p)["beneficiary_ref"] is None for p in VALID_FIXTURES}
    assert mpi_states == {True, False}, "no fixture exercises a PRESENT amh_mpi_ref"
    assert beneficiary_states == {True, False}, "no fixture exercises a PRESENT beneficiary_ref"


# ---------------------------------------------------------------------------
# Invalid fixtures are REJECTED
# ---------------------------------------------------------------------------


def test_bad_source_product_fixture_is_rejected() -> None:
    """`work_item.bad-source-product.invalid.json` carries `source_product: "tasy"`, outside the closed
    vocabulary. The Avro enum rejects it on the wire; phase A's mapper must reject it too, because a
    JSON/dev path has no enum to lean on."""
    event = _load("tests/contract/amh/fixtures/work_item.bad-source-product.invalid.json")
    assert event["source_product"] == "tasy"

    with pytest.raises(AmhMappingError) as exc:
        map_work_item(event, pin=PIN)
    assert exc.value.field == "source_product"
    # The PERMITTED vocabulary is quoted (it comes from the pin, not the wire); the OFFENDING value is
    # not. `repr` of the rejected value would render as `'tasy'`, so its absence is the check.
    assert "'tasy'" not in str(exc.value), "the offending wire value leaked into the refusal"
    assert "tasy_hospital" in exc.value.reason


def test_missing_consent_decision_ref_fixture_is_rejected() -> None:
    """`consent.missing-consent-decision-ref.invalid.json` omits a REQUIRED envelope field. The port
    cannot default it away — `maezo.ports.envelope` says a missing required field "is a
    CONTRACT_VIOLATION the ADAPTER must report"."""
    event = _load("tests/contract/amh/fixtures/consent.missing-consent-decision-ref.invalid.json")
    assert "consent_decision_ref" not in event

    with pytest.raises(AmhMappingError) as exc:
        map_consent_event(event, pin=PIN)
    assert exc.value.field == "consent_decision_ref"


def test_non_integer_revision_fixture_is_rejected() -> None:
    """`outcome.non-integer-revision.invalid.json` carries `outcome_revision` as a STRING in a `long`
    field. Avro rejects it on type; phase A rejects it on `payload_hash`, because the fixtures README
    records that this vector deliberately keeps the VALID form's hash ("mantém o `payload_hash` da
    forma válida — inócuo: ela reprova por tipo antes de qualquer verificação de hash"). So the
    stringified revision changes the canonical payload bytes while the declared hash does not move —
    which is precisely a hash mismatch, and a legitimate second line of defence for a consumer that
    has no Avro type check in front of it.
    """
    event = _load("tests/contract/amh/fixtures/outcome.non-integer-revision.invalid.json")
    assert event["payload"]["outcome_revision"] == "1"
    assert isinstance(event["payload"]["outcome_revision"], str)

    # The declared hash is the VALID vector's, and it does not bind these payload bytes.
    valid_hash = _load(OUTCOME_FIXTURES[0])["payload_hash"]
    assert event["payload_hash"] == valid_hash
    assert canonical_payload_hash(event["payload"]) != event["payload_hash"]

    with pytest.raises(AmhMappingError) as exc:
        map_outcome(event, pin=PIN)
    assert exc.value.field == "payload_hash"


def test_every_pinned_invalid_fixture_is_rejected_by_some_mapper() -> None:
    """Completeness: no `*.invalid.json` in the pin may map cleanly through ANY of the three mappers.

    Driven off the pin's own `role == "invalid"` entries rather than a hardcoded list, so a future AMH
    publication that adds an invalid vector cannot land with this suite silently ignoring it.
    """
    lock = json.loads((REPO_ROOT / "config/integrations/amh/contracts.lock.json").read_text(encoding="utf-8"))
    invalid = [f["vendored_path"] for f in lock["fixtures"] if f["role"] == "invalid"]
    assert len(invalid) == 3, f"expected 3 invalid vectors, found {invalid}"

    for vendored_path in invalid:
        event = _load(vendored_path)
        refusals = 0
        for mapper in (map_work_item, map_consent_event, map_outcome):
            try:
                mapper(event, pin=PIN)
            except AmhMappingError:
                refusals += 1
        assert refusals == 3, (
            f"{vendored_path} was accepted by at least one mapper — every invalid vector must be "
            "refused on every intake path"
        )


def test_valid_fixtures_are_accepted_so_rejection_is_not_indiscriminate() -> None:
    """The negative control for the test above: a mapper that raised on everything would pass it."""
    for vendored_path in WORK_ITEM_FIXTURES:
        assert map_work_item(_load(vendored_path), pin=PIN) is not None
    for vendored_path in CONSENT_FIXTURES:
        assert map_consent_event(_load(vendored_path), pin=PIN) is not None
    for vendored_path in OUTCOME_FIXTURES:
        assert map_outcome(_load(vendored_path), pin=PIN) is not None


def test_no_fixture_carries_a_real_manifest_digest() -> None:
    """Every fixture's `contract_manifest_digest` is the synthetic `ff…` placeholder (the fixtures
    README states this: the real digest is born at XRG-2 publication). A consumer must never treat it
    as a real manifest binding — pinned here so the mapping layer's verbatim copy of the field is not
    mistaken for validation of it."""
    for vendored_path in VALID_FIXTURES:
        event = _load(vendored_path)
        assert event["contract_manifest_digest"] == "f" * 64, vendored_path
        assert event["contract_manifest_digest"] != PIN.manifest_digest

    # And the mapper copies the field VERBATIM, asserting nothing about it — the digest binding is a
    # phase-B inbox concern (dedup on `{contract_manifest_digest, event_id}`, XRD-10), not a mapping one.
    for vendored_path in WORK_ITEM_FIXTURES:
        assert map_work_item(_load(vendored_path), pin=PIN).contract_manifest_digest == "f" * 64
