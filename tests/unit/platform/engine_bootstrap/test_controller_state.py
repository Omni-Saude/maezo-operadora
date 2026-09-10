"""Actual reducer and signature verification; external-port fakes are binding tests only."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.unit.platform.engine_bootstrap import test_controller_contracts as v
from tests.unit.platform.engine_bootstrap.test_controller_storage import U2, H, U, root

from maezo.platform.engine_bootstrap import controller as d
from maezo.platform.engine_bootstrap import controller_contracts as c
from maezo.platform.engine_bootstrap import controller_storage as s


class BindingOnlyQualification:
    """Offline observation/dispatcher port spy, never evidence of deployed ownership."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def check(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def pair_for(
    record: s.Document, journals: tuple[c.JournalObservation, ...] = ()
) -> tuple[s.Document, d.ObservationPair, d.ObservationVerifier]:
    r = record.value()
    control = v.control_observation()
    database = v.database_observation()
    cp = control["payload"]
    cp.update(
        controller_state=r["state"],
        operation_record_revision=r["revision"],
        journal_revision=r["journal_revision"],
        pending_index_sha256=r["pending_index_sha256"],
        journal=[j.to_wire() for j in journals],
    )
    control["observed_revision"] = r["revision"]
    cp["managed_resources"] = []
    cp["tasks"] = []
    cp["desired_count"] = cp["running_count"] = cp["pending_count"] = 0
    cp["live_dispatchers"] = []
    for path in cp["start_paths"]:
        path["enabled"] = False
    database["payload"]["sessions"] = []
    database["payload"]["prepared_transactions"] = []
    for route in database["payload"]["routes"]:
        route.update(enabled=False, in_flight_admissions=0)
    for role in database["payload"]["roles"]:
        role["can_login"] = False
    database["payload"]["generations"][0]["status"] = "PREPARED"
    database["payload"]["fence"].update(epoch=r["epoch"], owner_run_id=r["run_id"], state="CLOSED")
    for obs in (control, database):
        obs.update(
            scope=r["scope"],
            epoch=r["epoch"],
            run_id=r["run_id"],
            candidate_sha256=r["candidate_sha256"],
            issued_at_ms=100,
            expires_at_ms=4000,
            key_id=obs["kind"],
        )
    bounds = v.sample("InventoryBounds")
    bounds["bounds"] = []

    def collect(value: Any, path: str) -> None:
        if type(value) is dict:
            for key, child in value.items():
                collect(child, path + "." + key)
        elif type(value) is list:
            bounds["bounds"].append(
                {
                    "collection_path": path,
                    "max_entries": len(value),
                    "expected_inventory_sha256": s.digest(s.canonical(value)),
                }
            )
            for i, child in enumerate(value):
                collect(child, path + "." + str(i))

    for obs in (control, database):
        collect(obs["payload"], obs["kind"] + ".payload")
    bounds["bounds"].sort(key=lambda row: row["collection_path"])
    profile = v.sample("TrustProfile")
    profile["inventory_bounds_sha256"] = s.digest(s.canonical(bounds))
    keys = {
        kind: Ed25519PrivateKey.from_private_bytes((b"c" if kind == "control" else b"d") * 32)
        for kind in ("control", "database")
    }
    for kind in keys:
        profile[kind + "_keys"][0].update(
            key_id=kind,
            purpose=kind + "-observation",
            public_key=s.encode64(keys[kind].public_key().public_bytes_raw()),
            not_before_ms=0,
            expires_at_ms=10000,
        )
    ph = s.digest(s.canonical(profile))
    for obs in (control, database):
        obs["trust_profile_sha256"] = ph
    database["payload"]["fence"]["trust_profile_sha256"] = ph
    r["trust_profile_sha256"] = ph
    record = s.Document.create("Root", r)
    for obs in (control, database):
        unsigned = c.parse("SignedObservation", s.canonical(obs))
        obs["signature"] = s.encode64(keys[obs["kind"]].sign(c.signature_input(unsigned)))
    typed_control = c.parse("ControlObservation", s.canonical(control))
    typed_database = c.parse("DatabaseObservation", s.canonical(database))
    verifier = d.ObservationVerifier(
        c.parse("TrustProfile", s.canonical(profile)), c.parse("InventoryBounds", s.canonical(bounds)), ph
    )
    pair = verifier.verify(
        typed_control,
        typed_database,
        challenge=typed_control.challenge,
        request_sha256=typed_control.request_sha256,
        now_ms=200,
        deadline_ms=900,
    )
    return record, pair, verifier


@pytest.mark.parametrize(
    "state", ["REVIEWED", "RESTORING", "RESTORED", "READINESS_VERIFIED", "ACTIVE", "RECOVERY_REQUIRED"]
)
def test_crash_closes_authority_preserving_pending_journal_and_inventory(state: str) -> None:
    kwargs = {
        "state": state,
        "current_generation_id": 1,
        "activation_decision_sha256": H,
        "restore_receipt_sha256": H,
        "readiness_result_sha256": H,
    }
    if state != "ACTIVE":
        kwargs["pending_operation_ids"] = [U]
    before = root(**kwargs)
    change = d.recover(before, "PENDING_UNKNOWN")
    after = change.replacement.value()
    assert after["state"] == "RECOVERY_REQUIRED" and after["current_generation_id"] is None
    for name in (
        "pending_operation_ids",
        "journal_revision",
        "managed_registry_sha256",
        "next_generation_id",
    ):
        assert after[name] == before.value()[name]


def test_successor_claim_is_atomic_immutable_owner_cas_not_epoch_string() -> None:
    before = root(pending_operation_ids=[U])
    change = d.claim_owner(
        before,
        {"owner_subject": "owner-b", "run_id": U2, "epoch": 10},
        claim_id=U2,
        table_identity_sha256=H,
        now_ms=1001,
    )
    claim = s.Document("OwnerClaimReceipt", change.append[0][1])
    after = change.replacement.value()
    assert claim.value()["previous_owner"] == {"owner_subject": "owner-a", "run_id": U, "epoch": 9}
    assert after["current_owner_claim_sha256"] == claim.digest() and after["state"] == "RECOVERY_REQUIRED"
    assert (
        after["pending_operation_ids"] == [U] and after["revision"] == 45 and after["journal_revision"] == 2
    )


@pytest.mark.parametrize("epoch,time", [(9, 1001), (8, 1001), (10, 999), (10, 1000)])
def test_live_lease_equality_or_old_owner_cannot_claim(epoch: int, time: int) -> None:
    with pytest.raises(s.Refusal):
        d.claim_owner(
            root(),
            {"owner_subject": "b", "run_id": U2, "epoch": epoch},
            claim_id=U2,
            table_identity_sha256=H,
            now_ms=time,
        )


def test_generation_reservation_never_reuses_failed_slot() -> None:
    before = root()
    owner = {k: before.value()[k] for k in ("owner_subject", "run_id", "epoch")}
    first, change = d.reserve_generation(before, owner, 500)
    second, next_change = d.reserve_generation(change.replacement, owner, 501)
    assert (first, second, next_change.replacement.value()["next_generation_id"]) == (3, 4, 5)
    assert s.parse_wire(change.append[0][1])["tombstone"] is True
    with pytest.raises(s.Refusal):
        d.reserve_generation(d.recover(change.replacement, "UNAVAILABLE").replacement, owner, 502)


def test_real_signature_verification_and_deadline_equality() -> None:
    record, pair, verifier = pair_for(root())
    pair.current(record, 500)
    with pytest.raises(s.Refusal):
        pair.current(record, 900)
    forged = replace(pair.control, signature=s.encode64(b"\0" * 64))
    with pytest.raises(s.Refusal, match="AUTH_REFUSED"):
        verifier.verify(
            forged,
            pair.database,
            challenge=forged.challenge,
            request_sha256=forged.request_sha256,
            now_ms=200,
            deadline_ms=900,
        )
    with pytest.raises(s.Refusal):
        d.ObservationPair(pair.control, pair.database, 200, 900, H, object())


def test_advance_requires_exact_next_state_current_pair_and_external_stage() -> None:
    record, pair, _ = pair_for(root(state="LEASED"))
    evidence = {
        "scope": record.value()["scope"],
        "run_id": U,
        "epoch": 9,
        "previous_state": "LEASED",
        "target_state": "START_FENCED",
        "root_sha256": record.digest(),
        "observation_pair_sha256": pair.digest(),
        "stage_evidence_bytes": s.encode64(b"{}"),
        "stage_evidence_sha256": s.digest(b"{}"),
    }
    with pytest.raises(s.Refusal, match="UNAVAILABLE"):
        d.advance(
            record, "START_FENCED", pair, now_ms=500, evidence_wire=s.canonical(evidence), qualification=None
        )
    spy = BindingOnlyQualification()
    change = d.advance(
        record, "START_FENCED", pair, now_ms=500, evidence_wire=s.canonical(evidence), qualification=spy
    )
    assert change.replacement.value()["state"] == "START_FENCED" and spy.calls[0]["root_wire"] == record.wire
    with pytest.raises(s.Refusal):
        d.advance(record, "ACTIVE", pair, now_ms=500, evidence_wire=s.canonical(evidence), qualification=spy)
    with pytest.raises(s.Refusal):
        pair.current(change.replacement, 500)


def test_unknown_dispatch_outcome_is_retained_and_never_settled_by_timeout() -> None:
    journal = c.parse("JournalObservation", s.canonical(v.journal("RESERVED")))
    before = root(pending_operation_ids=[journal.external_operation_id])
    outcome = c.parse("ProviderOutcome", s.canonical(v.sample("ProviderOutcome")))
    with pytest.raises(s.Refusal, match="UNAVAILABLE"):
        d.record_outcome(before, journal, outcome, revision=3, now_ms=1001)
    spy = BindingOnlyQualification()
    change = d.record_outcome(before, journal, outcome, revision=3, now_ms=1001, qualification=spy)
    assert change.replacement.value()["state"] == "RECOVERY_REQUIRED"
    assert change.replacement.value()["pending_operation_ids"] == [journal.external_operation_id]
    assert change.journals[0].replacement.state == "DISPATCHED_UNKNOWN"
    assert change.journals[0].replacement.provider_outcome == outcome
    with pytest.raises(s.Refusal):
        d.JournalUpdate(
            change.journals[0].replacement, c.parse("JournalObservation", s.canonical(v.journal("INTENT")))
        )


def test_terminal_journal_cannot_be_rewritten_or_changed_outcome_adopted() -> None:
    terminal = c.parse("JournalObservation", s.canonical(v.journal("SETTLED")))
    with pytest.raises(s.Refusal):
        d.JournalUpdate(terminal, terminal)
    current = c.parse("JournalObservation", s.canonical(v.journal("ACKNOWLEDGED")))
    bad = replace(
        current, provider_outcome=replace(current.provider_outcome, provider_request_id="different")
    )
    with pytest.raises(s.Refusal):
        d.JournalUpdate(current, bad)
