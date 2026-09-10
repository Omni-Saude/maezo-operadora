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
    record: s.Document,
    journals: tuple[c.JournalObservation, ...] = (),
    managed: tuple[c.ManagedResource, ...] = (),
    generations: tuple[c.RuntimeAdmissionGeneration, ...] | None = None,
    fence_revision: int = 1,
    task_status: str = "RUNNING",
    unregistered_tasks: bool = False,
    task_inventory: tuple[dict[str, Any], ...] | None = None,
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
    database["observed_revision"] = fence_revision
    cp["managed_resources"] = [m.to_wire() for m in managed]
    cp["tasks"] = v.control_observation()["payload"]["tasks"] if managed or unregistered_tasks else []
    if task_inventory is not None and (managed or unregistered_tasks):
        cp["tasks"] = [dict(task) for task in task_inventory]
    for task in cp["tasks"]:
        task["last_status"] = task_status
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
    if generations == ():
        database["payload"]["roles"] = []
        database["payload"]["memberships"] = []
        database["payload"]["routes"] = []
    if generations is not None:
        oid_map = {}
        for role in database["payload"]["roles"]:
            matching = [g for g in generations if g.binding.generation_id == role["generation_id"]]
            if role["owned"] and matching:
                oid_map[role["oid"]] = matching[0].binding.login_oid
                role.update(
                    oid=matching[0].binding.login_oid,
                    name=matching[0].binding.login_name,
                    can_login=matching[0].status == "OPEN",
                )
        for membership in database["payload"]["memberships"]:
            for field in ("member_oid", "role_oid", "grantor_oid"):
                membership[field] = oid_map.get(membership[field], membership[field])
        for route in database["payload"]["routes"]:
            route["backend_login_oids"] = [oid_map.get(oid, oid) for oid in route["backend_login_oids"]]
        database["payload"]["generations"] = [g.to_wire() for g in generations]
    else:
        database["payload"]["generations"][0]["status"] = "PREPARED"
    database["payload"]["fence"].update(
        epoch=r["epoch"], owner_run_id=r["run_id"], state="CLOSED", revision=fence_revision
    )
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


def intent_inputs(action: str = "RunTask") -> tuple[Any, Any, Any]:
    raw = v.intent(action)
    decision = v.decision(raw["purpose"])
    decision["generation_core"]["credential_binding_sha256"] = v.sha(raw["credential_binding"])
    generation = v.generation(raw["purpose"], raw["expected_precondition"]["target_generation_status"])
    generation.update(binding=decision["generation_core"], activation_decision_sha256=v.sha(decision))
    return (
        c.parse("AdmittedExternalIntent", s.canonical(raw)),
        c.parse("RuntimeAdmissionGeneration", s.canonical(generation)),
        c.parse(
            "CandidateDecision" if raw["purpose"] == "candidate" else "ActivationDecision",
            s.canonical(decision),
        ),
    )


def test_append_actual_run_intent_passes_complete_binding() -> None:
    intent, generation, decision = intent_inputs()
    generation = replace(generation, status="OPEN", revision=2)
    record, pair, _ = pair_for(
        root(
            scope=intent.scope.to_wire(),
            run_id=intent.logical_run_id,
            epoch=intent.epoch,
            owner_subject=intent.owner_subject,
            state="CANDIDATE_ADMITTED",
            current_generation_id=intent.target_generation,
            next_generation_id=intent.target_generation + 1,
            activation_decision_sha256=decision.digest(),
        ),
        generations=(generation,),
    )
    intent = replace(
        intent,
        expected_precondition=replace(
            intent.expected_precondition,
            controller_revision=record.value()["revision"],
            journal_revision=record.value()["journal_revision"],
            pending_index_sha256=record.value()["pending_index_sha256"],
            target_generation_status="OPEN",
        ),
    )
    result = d.append_intent(record, intent, pair, 300, generation=generation, decision=decision)
    assert result.append == (("INTENT#" + intent.external_operation_id, intent.canonical_bytes()),)
    assert result.replacement.value()["pending_operation_ids"] == [intent.external_operation_id]


def recovery_inputs() -> dict[str, Any]:
    origin, generation, decision = intent_inputs()
    managed = c.parse(
        "ManagedResource", s.canonical(v.control_observation()["payload"]["managed_resources"][0])
    )
    original = v.journal("SETTLED")
    original["intent_sha256"] = origin.digest()
    original["reservation"]["intent_sha256"] = origin.digest()
    original["settlement"]["dispatch_terminal"]["intent_sha256"] = origin.digest()
    original["settlement"]["settlement_class"] = "CURRENT_LIVE_TRACKED"
    original["settlement"]["resource_proofs"] = []
    original["provider_outcome"]["returned_resource_ids"] = [managed.provider_resource_id]
    journal = c.parse("JournalObservation", s.canonical(original))
    before = root(
        epoch=1,
        run_id=origin.logical_run_id,
        owner_subject=origin.owner_subject,
        lease_deadline_ms=100,
        managed_registry_sha256=s.digest(s.canonical([managed.to_wire()])),
    )
    change = d.claim_owner(
        before,
        dict(owner_subject="owner-b", run_id=U2, epoch=2),
        claim_id=U2,
        table_identity_sha256=H,
        now_ms=101,
    )
    record, pair, _ = pair_for(
        change.replacement, (journal,), (managed,), (replace(generation, status="RETIRED", revision=2),)
    )
    lineage = s.Document.create(
        "OwnerLineageRead",
        dict(
            protocol="maezo.d7-owner-lineage-read.v1",
            scope=record.value()["scope"],
            control_scope_id=U,
            table_identity_sha256=H,
            challenge_sha256=H,
            authenticated_observer_subject="qualified-owner-observer",
            root_bytes=s.encode64(record.wire),
            root_sha256=record.digest(),
            claim_receipt_bytes=s.encode64(change.append[0][1]),
            claim_receipt_sha256=s.digest(change.append[0][1]),
            ancestor_claim_bytes=[],
            ancestor_claim_sha256s=[],
            strong_read_revision=record.value()["revision"],
            reread_revision=record.value()["revision"],
            observed_at_ms=200,
            deadline_ms=900,
        ),
    )
    request = dict(
        cluster=decision.topology.cluster_arn,
        task=managed.provider_resource_id,
        reason="maezo-controller-owned-stop",
    )
    carrier = dict(
        protocol="maezo.provisioning-recovery-stop.v1",
        action="StopTask",
        external_operation_id="00000000-0000-4000-8000-000000000003",
        scope=record.value()["scope"],
        logical_run_id=U2,
        epoch=2,
        owner_subject="owner-b",
        origin_external_operation_id=origin.external_operation_id,
        origin_intent_sha256=origin.digest(),
        generation_id=generation.binding.generation_id,
        generation_core_sha256=generation.binding.digest(),
        decision_sha256=decision.digest(),
        managed_resource_sha256=managed.digest(),
        expected_precondition=dict(
            controller_revision=record.value()["revision"],
            db_fence_revision=pair.database.payload.fence.revision,
            journal_revision=record.value()["journal_revision"],
            pending_index_sha256=record.value()["pending_index_sha256"],
            target_inventory_sha256=s.digest(s.canonical([managed.to_wire()])),
            target_generation_status="RETIRED",
        ),
        expected_root_sha256=record.digest(),
        current_owner_claim_sha256=record.value()["current_owner_claim_sha256"],
        request=request,
        request_bytes=s.encode64(s.canonical(request)),
        request_sha256=s.digest(s.canonical(request)),
        idempotency_class="SINGLE_DISPATCH_RECONCILE",
        client_token=None,
    )
    return dict(
        root=record,
        intent=s.RecoveryStopTaskIntent(s.canonical(carrier)),
        pair=pair,
        now_ms=300,
        lineage=lineage,
        origin=origin,
        origin_generation=generation,
        decision=decision,
        origin_journal=journal,
        managed_resource=managed,
        pending_intents=(),
        qualification=BindingOnlyQualification(),
    )


def test_current_owner_recovery_stop_preserves_historical_origin_and_parses_actual_dynamodb() -> None:
    from tests.unit.platform.engine_bootstrap.test_controller_dynamodb import (
        AuthoritySpy,
        ProtocolClient,
        binding,
    )

    from maezo.platform.engine_bootstrap import controller_dynamodb as dynamo

    inputs = recovery_inputs()
    history = (
        inputs["origin"].canonical_bytes(),
        inputs["origin_journal"].canonical_bytes(),
        inputs["managed_resource"].canonical_bytes(),
    )
    change = d.append_recovery_stop(**inputs)
    assert change.replacement.value()["state"] == "RECOVERY_REQUIRED"
    assert (
        change.replacement.value()["epoch"] == 2
        and change.replacement.value()["current_generation_id"] is None
    )
    assert history == (
        inputs["origin"].canonical_bytes(),
        inputs["origin_journal"].canonical_bytes(),
        inputs["managed_resource"].canonical_bytes(),
    )
    rows = [
        dynamo.unitem(action["Put"]["Item"])
        for action in dynamo.transaction(binding(), change)["TransactItems"]
    ]
    store = dynamo.DynamoDBStore(ProtocolClient(rows), binding(), AuthoritySpy())
    new = store.read_journal(inputs["intent"].external_operation_id)
    assert new.epoch == 2 and new.logical_run_id == U2 and new.generation_id == 1 and new.action == "StopTask"
    assert new.intent_sha256 == inputs["intent"].digest()


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "root",
        "target",
        "origin",
        "decision",
        "managed",
        "missing_authority",
        "pending",
        "lineage_expired",
    ],
)
def test_recovery_stop_refuses_substituted_or_unqualified_binding(fault: str) -> None:
    inputs = recovery_inputs()
    carrier = inputs["intent"].to_wire()
    if fault == "missing_authority":
        inputs["qualification"] = None
    elif fault == "pending":
        inputs["pending_intents"] = (inputs["origin"].canonical_bytes(),)
    elif fault == "lineage_expired":
        inputs["now_ms"] = 900
    elif fault == "target":
        carrier["request"]["task"] = "arn:aws:ecs:sa-east-1:123456789012:task/foreign"
        carrier["request_bytes"] = s.encode64(s.canonical(carrier["request"]))
        carrier["request_sha256"] = s.digest(s.canonical(carrier["request"]))
    else:
        key = {
            "owner": "owner_subject",
            "root": "expected_root_sha256",
            "origin": "origin_intent_sha256",
            "decision": "decision_sha256",
            "managed": "managed_resource_sha256",
        }[fault]
        carrier[key] = "other-owner" if fault == "owner" else "b" * 64
    inputs["intent"] = s.RecoveryStopTaskIntent(s.canonical(carrier))
    with pytest.raises(s.Refusal):
        d.append_recovery_stop(**inputs)


def initial_completion_inputs() -> dict[str, Any]:
    from tests.unit.platform.engine_bootstrap.test_controller_storage import operation_proof

    record, pair, _ = pair_for(
        root(
            epoch=1,
            revision=1,
            next_generation_id=1,
            db_fence_epoch=0,
            journal_revision=0,
            restore_state="UNRECONCILED",
        ),
        generations=(),
        fence_revision=3,
    )
    r = record.value()
    owner = {k: r[k] for k in ("owner_subject", "run_id", "epoch")}
    lineage = dict(
        protocol="maezo.d7-owner-lineage-read.v1",
        scope=r["scope"],
        control_scope_id=U,
        table_identity_sha256=H,
        challenge_sha256=H,
        authenticated_observer_subject="observer",
        root_bytes=s.encode64(record.wire),
        root_sha256=record.digest(),
        claim_receipt_bytes=None,
        claim_receipt_sha256=None,
        ancestor_claim_bytes=[],
        ancestor_claim_sha256s=[],
        strong_read_revision=1,
        reread_revision=1,
        observed_at_ms=200,
        deadline_ms=900,
    )
    installation = dict(
        protocol="maezo.d7-installation-binding.v1",
        control_scope_id=U,
        database_binding=r["scope"]["database_binding"],
        issuer_scopes=[r["scope"]],
    )
    installation.update(
        installation_id=U,
        component="maezo-d7-control",
        version=1,
        act_schema_name="act",
        act_schema_oid=11,
        control_schema_oid=12,
        controller_table_arn="arn:aws:dynamodb:sa-east-1:123456789012:table/owned",
        role_acl_manifest_sha256=H,
        object_manifest_sha256=H,
        migration_sha256=H,
        cib_abi_sha256=H,
    )
    revision = pair.database.payload.fence.revision
    prior = dict(
        owner_subject=r["owner_subject"],
        run_id=r["run_id"],
        epoch=1,
        revision=revision - 2,
        controller_revision=1,
        database_binding_sha256=s.digest(s.canonical(r["scope"]["database_binding"])),
        next_generation_id=1,
        current_generation_id=None,
    )
    evidence: dict[str, Any] = dict(
        protocol="maezo.d7-initial-reconciliation-evidence.v1",
        root_bytes=s.encode64(record.wire),
        installation_binding_bytes=s.encode64(s.canonical(installation)),
        observation_pair_sha256=pair.digest(),
        current_fence=dict(
            scope=r["scope"],
            **owner,
            revision=revision,
            next_generation_id=1,
            current_generation_id=None,
            mode="CLOSED",
            restore_state="RECONCILED",
            installation_binding_sha256=s.digest(s.canonical(installation)),
        ),
    )
    for prefix, operation, op_id, offset in (
        ("reconcile", "reconcile_fence", U, 1),
        ("close", "close_runtime_fence", U2, 0),
    ):
        proof = operation_proof(operation)
        proof.update(
            scope=r["scope"],
            run_id=r["run_id"],
            epoch=1,
            controller_state="REVIEWED",
            controller_revision=1,
            journal_revision=0,
            observed_at_ms=50,
            controller_lease_deadline_ms=r["lease_deadline_ms"],
            trust_profile_sha256=r["trust_profile_sha256"],
        )
        request: dict[str, Any] = dict(
            operation=operation,
            candidate_sha256=r["candidate_sha256"],
            trust_profile_sha256=r["trust_profile_sha256"],
        )
        if prefix == "reconcile":
            request = dict(
                operation=operation,
                mode="current_owner_reconcile",
                database_binding=r["scope"]["database_binding"],
                expected_owner=prior,
                successor_owner=owner,
                claim_id=None,
                claim_receipt_sha256=None,
                next_generation_id=1,
                retired_generation_index_sha256=s.digest(b"[]"),
            )
            proof = dict(
                protocol="maezo.d7-owner-handoff-proof.v1",
                operation_proof=proof,
                owner_subject=r["owner_subject"],
                owner_lineage_bytes=s.encode64(s.canonical(lineage)),
                owner_lineage_sha256=s.digest(s.canonical(lineage)),
                old_fence_owner_sha256=s.digest(s.canonical(prior)),
                restore_reconciliation_bytes=None,
                restore_reconciliation_sha256=None,
            )
        call = dict(
            protocol="maezo.d7-store-call.v1",
            scope=r["scope"],
            run_id=r["run_id"],
            epoch=1,
            issuer_operation_id=op_id,
            expected_fence_revision=revision - offset - 1,
            request=request,
            proof=proof,
        )
        receipt = dict(
            protocol="maezo.d7-store-transition-receipt.v1",
            scope=r["scope"],
            run_id=r["run_id"],
            epoch=1,
            issuer_operation_id=op_id,
            operation=operation,
            request_sha256=s.logical_request_digest(operation, call),
            fence_revision=revision - offset,
            generation_id=None,
            permit_id=None,
            state="CLOSED" if prefix == "close" else "RECOVERY_REQUIRED",
            issuer_session_user="issuer",
            issuer_login_oid=42,
            issuer_current_user="definer",
            issuer_definer_oid=43,
            recorded_before_commit_at_ms=91 - offset,
            provisioning_schema_version=1,
        )
        evidence[prefix + "_call"] = call
        evidence[prefix + "_result"] = dict(
            protocol="maezo.d7-store-result.v1",
            issuer_operation_id=op_id,
            request_sha256=receipt["request_sha256"],
            result_sha256=s.digest(s.canonical(receipt)),
            canonical_result_bytes=s.encode64(s.canonical(receipt)),
        )
    return dict(
        root=record,
        owner=owner,
        pair=pair,
        now_ms=300,
        evidence_wire=s.canonical(evidence),
        qualification=BindingOnlyQualification(),
    )


def test_initial_reconciliation_retains_qualified_chain_and_reserves_first_id_after_cas() -> None:
    inputs = initial_completion_inputs()
    with pytest.raises(s.Refusal):
        d.reserve_generation(inputs["root"], inputs["owner"], 300)
    change = d.complete_initial_reconciliation(**inputs)
    before, after = inputs["root"].value(), change.replacement.value()
    assert {k for k in before if before[k] != after[k]} == {"revision", "restore_state", "db_fence_epoch"}
    assert change.append == (("PROOF#" + s.digest(inputs["evidence_wire"]), inputs["evidence_wire"]),)
    generation_id, reserved = d.reserve_generation(change.replacement, inputs["owner"], 300)
    assert generation_id == 1
    assert reserved.replacement.value()["next_generation_id"] == 2
    assert inputs["qualification"].calls[0]["evidence_wire"] == inputs["evidence_wire"]
    with pytest.raises(s.Refusal):
        d.complete_initial_reconciliation(**{**inputs, "root": change.replacement})


@pytest.mark.parametrize(
    "fault",
    [
        "missing_authority",
        "boolean_evidence",
        "owner",
        "expired",
        "reserved",
        "unknown_reconcile",
        "missing_close",
        "unreconciled_fence",
        "wrong_fence",
        "changed_root",
    ],
)
def test_initial_completion_refuses_unqualified_or_incomplete_closure(fault: str) -> None:
    inputs = initial_completion_inputs()
    evidence = s.parse_wire(inputs["evidence_wire"])
    if fault == "missing_authority":
        inputs["qualification"] = None
    elif fault == "boolean_evidence":
        evidence = {"committed": True}
    elif fault == "owner":
        inputs["owner"] = dict(inputs["owner"], owner_subject="other")
    elif fault == "expired":
        inputs["now_ms"] = 900
    elif fault == "reserved":
        inputs["root"] = root(epoch=1, revision=1, next_generation_id=2)
    elif fault == "unknown_reconcile":
        evidence["reconcile_result"] = {"kind": "UNKNOWN"}
    elif fault == "missing_close":
        del evidence["close_result"]
    elif fault == "unreconciled_fence":
        evidence["current_fence"]["restore_state"] = "UNRECONCILED"
    elif fault == "wrong_fence":
        evidence["current_fence"]["next_generation_id"] = 2
    else:
        evidence["root_bytes"] = s.encode64(root().wire)
    inputs["evidence_wire"] = s.canonical(evidence)
    with pytest.raises(s.Refusal):
        d.complete_initial_reconciliation(**inputs)


def recovery_reserved_inputs() -> dict[str, Any]:
    inputs = recovery_inputs()
    appended = d.append_recovery_stop(**inputs)
    intent = inputs["intent"]
    journal = c.parse(
        "JournalObservation",
        s.canonical(
            dict(
                external_operation_id=intent.external_operation_id,
                logical_run_id=intent.logical_run_id,
                epoch=intent.epoch,
                generation_id=intent.target_generation,
                intent_sha256=intent.digest(),
                action="StopTask",
                state="INTENT",
                reservation=None,
                provider_outcome=None,
                settlement=None,
                cancellation_unsent_proof=None,
            )
        ),
    )
    record, pair, _ = pair_for(
        appended.replacement,
        (inputs["origin_journal"], journal),
        (inputs["managed_resource"],),
        (replace(inputs["origin_generation"], status="RETIRED", revision=2),),
    )
    reservation = v.sample("DispatcherReservation")
    reservation["intent_sha256"] = intent.digest()
    reservation["credential_session"]["generation_id"] = intent.target_generation
    reserved, change = d.reserve_dispatch(
        record,
        journal,
        c.parse("DispatcherReservation", s.canonical(reservation)),
        pair,
        300,
        qualification=inputs["qualification"],
    )
    outcome = v.sample("ProviderOutcome")
    outcome["returned_resource_ids"] = [inputs["managed_resource"].provider_resource_id]
    outcome_change = d.record_outcome(
        change.replacement,
        reserved,
        c.parse("ProviderOutcome", s.canonical(outcome)),
        revision=change.replacement.value()["journal_revision"] + 1,
        now_ms=300,
        qualification=inputs["qualification"],
    )
    current_journal = outcome_change.journals[0].replacement
    record, pair, _ = pair_for(
        outcome_change.replacement,
        (inputs["origin_journal"], current_journal),
        (inputs["managed_resource"],),
        (replace(inputs["origin_generation"], status="RETIRED", revision=2),),
        task_status="STOPPED",
    )
    proof = v.sample("ResourceTerminalProof")
    proof.update(
        resource_id=inputs["managed_resource"].provider_resource_id,
        generation_id=intent.target_generation,
        terminal_kind="TASK_STOPPED",
    )
    typed_proof = c.parse("ResourceTerminalProof", s.canonical(proof))
    terminal = replace(
        inputs["managed_resource"],
        desired_state="STOPPED",
        current_observed_state="STOPPED",
        retired_terminal_proof_sha256=typed_proof.digest(),
        observation_sha256=typed_proof.observation_sha256,
    )
    settlement = v.sample("Settlement")
    settlement.update(
        settlement_class="RETIRED_RESOURCES_TERMINAL",
        resource_proofs=[proof],
        managed_resource_registry_sha256=s.digest(s.canonical([terminal.to_wire()])),
    )
    settlement["dispatch_terminal"].update(
        intent_sha256=intent.digest(), invocation_id=reserved.reservation.invocation_id
    )
    return dict(
        inputs=inputs,
        root=record,
        journal=current_journal,
        pair=pair,
        now_ms=300,
        managed=(terminal,),
        settlement=c.parse("Settlement", s.canonical(settlement)),
        recovery_intent=intent,
        qualification=inputs["qualification"],
    )


def test_successor_stop_full_source_chain_settles_with_retained_creator_and_terminal_witness() -> None:
    values = recovery_reserved_inputs()
    inputs = values.pop("inputs")
    original = inputs["origin_journal"].canonical_bytes()
    assert not c.live_ready(values["pair"].control.payload, inputs["managed_resource"])
    with pytest.raises(s.Refusal):
        d._closed_observations(values["pair"])
    settled, change = d.settle(**values)
    assert change.replacement.value()["pending_operation_ids"] == []
    assert change.documents[0].expected_wire == inputs["managed_resource"].canonical_bytes()
    record, pair, _ = pair_for(
        change.replacement,
        (inputs["origin_journal"], settled),
        values["managed"],
        (replace(inputs["origin_generation"], status="RETIRED", revision=2),),
        task_status="STOPPED",
    )
    pair.current(record, 300)
    d._closed_observations(pair)
    assert pair.control.payload.journal[0].canonical_bytes() == original
    assert c.task_terminal_witness(pair.control.payload, pair.control.payload.managed_resources[0])
    assert change.replacement.value()["state"] == "RECOVERY_REQUIRED"
    with pytest.raises(s.Refusal):
        d.DocumentUpdate(
            change.documents[0].key, change.documents[0].replacement_wire, change.documents[0].expected_wire
        )


@pytest.mark.parametrize(
    "fault", ["authority", "running", "wrong_observation", "wrong_target", "wrong_intent"]
)
def test_recovery_settlement_rejects_ack_only_or_unqualified_terminal_projection(fault: str) -> None:
    values = recovery_reserved_inputs()
    inputs = values.pop("inputs")
    if fault == "authority":
        values["qualification"] = None
    elif fault == "running":
        values["root"], values["pair"], _ = pair_for(
            values["root"],
            (inputs["origin_journal"], values["journal"]),
            (inputs["managed_resource"],),
            (replace(inputs["origin_generation"], status="RETIRED", revision=2),),
        )
    elif fault == "wrong_intent":
        values["recovery_intent"] = replace(
            values["recovery_intent"],
            wire=s.canonical({**values["recovery_intent"].to_wire(), "origin_intent_sha256": "b" * 64}),
        )
    else:
        resource = values["managed"][0]
        resource = (
            replace(resource, observation_sha256="b" * 64)
            if fault == "wrong_observation"
            else replace(resource, provider_resource_id=v.TASK + "other")
        )
        values["managed"] = (resource,)
        values["settlement"] = replace(
            values["settlement"], managed_resource_registry_sha256=s.digest(s.canonical([resource.to_wire()]))
        )
    with pytest.raises((s.Refusal, c.ContractError)):
        d.settle(**values)


@pytest.mark.parametrize(
    "status,qualified,expected",
    [("RUNNING", True, True), ("STOPPED", True, False), ("RUNNING", False, False)],
)
def test_current_authenticated_launch_response_can_settle_live_only_from_fresh_qualified_readiness(
    status: str, qualified: bool, expected: bool
) -> None:
    journal = c.parse("JournalObservation", s.canonical(v.journal("RESERVED")))
    record = root(
        epoch=journal.epoch,
        run_id=journal.logical_run_id,
        current_generation_id=journal.generation_id,
        state="CANDIDATE_ADMITTED",
        pending_operation_ids=[journal.external_operation_id],
    )
    outcome = v.sample("ProviderOutcome")
    outcome.update(returned_resource_ids=[v.TASK], failures=[])
    spy = BindingOnlyQualification()
    change = d.record_outcome(
        record,
        journal,
        c.parse("ProviderOutcome", s.canonical(outcome)),
        revision=3,
        now_ms=300,
        qualification=spy,
    )
    acknowledged = change.journals[0].replacement
    assert acknowledged.state == "ACKNOWLEDGED"
    assert change.replacement.value()["state"] == "CANDIDATE_ADMITTED"
    record, pair, _ = pair_for(
        change.replacement, (acknowledged,), task_status=status, unregistered_tasks=True
    )
    resource = c.parse(
        "ManagedResource", s.canonical(v.control_observation()["payload"]["managed_resources"][0])
    )
    settlement = v.sample("Settlement")
    settlement.update(
        settlement_class="CURRENT_LIVE_TRACKED",
        resource_proofs=[],
        managed_resource_registry_sha256=s.digest(s.canonical([resource.to_wire()])),
    )
    typed = c.parse("Settlement", s.canonical(settlement))
    if expected:
        settled, final = d.settle(record, acknowledged, typed, pair, 300, (resource,), qualification=spy)
        assert settled.state == "SETTLED" and final.replacement.value()["pending_operation_ids"] == []
        assert final.documents[0].expected_wire is None
        assert final.replacement.value()["current_generation_id"] == journal.generation_id
    else:
        with pytest.raises(s.Refusal):
            d.settle(
                record, acknowledged, typed, pair, 300, (resource,), qualification=spy if qualified else None
            )


def test_unknown_prior_stop_cannot_be_bypassed_by_fresh_operation_uuid() -> None:
    values = recovery_reserved_inputs()
    inputs = values["inputs"]
    current = values["root"]
    root_value = current.value()
    carrier = inputs["intent"].to_wire()
    carrier.update(external_operation_id=U[:-1] + "4", expected_root_sha256=current.digest())
    carrier["expected_precondition"].update(
        controller_revision=root_value["revision"],
        journal_revision=root_value["journal_revision"],
        pending_index_sha256=root_value["pending_index_sha256"],
    )
    lineage = inputs["lineage"].value()
    lineage.update(
        root_bytes=s.encode64(current.wire),
        root_sha256=current.digest(),
        strong_read_revision=root_value["revision"],
        reread_revision=root_value["revision"],
    )
    inputs.update(
        root=current,
        pair=values["pair"],
        intent=s.RecoveryStopTaskIntent(s.canonical(carrier)),
        lineage=s.Document.create("OwnerLineageRead", lineage),
        pending_intents=(values["recovery_intent"].canonical_bytes(),),
    )
    with pytest.raises(s.Refusal, match="PENDING_UNKNOWN"):
        d.append_recovery_stop(**inputs)


@pytest.mark.parametrize("current", d.STATES[:-1])
def test_each_normal_transition_keeps_order_and_missing_qualification_closed(current: str) -> None:
    record, pair, _ = pair_for(root(state=current))
    following = d.STATES[d.STATES.index(current) + 1]
    evidence = s.canonical(
        dict(
            scope=record.value()["scope"],
            run_id=U,
            epoch=9,
            previous_state=current,
            target_state=following,
            root_sha256=record.digest(),
            observation_pair_sha256=pair.digest(),
            stage_evidence_bytes=s.encode64(b"{}"),
            stage_evidence_sha256=s.digest(b"{}"),
        )
    )
    with pytest.raises(s.Refusal, match="UNAVAILABLE"):
        d.advance(record, following, pair, now_ms=300, evidence_wire=evidence, qualification=None)
    with pytest.raises(s.Refusal, match="PRECONDITION_MISMATCH"):
        d.advance(
            record,
            "REVIEWED",
            pair,
            now_ms=300,
            evidence_wire=evidence,
            qualification=BindingOnlyQualification(),
        )


def frozen_lifecycle_inputs(task_count: int = 10) -> dict[str, Any]:
    """Acyclic source construction with real crypto; SQL/authority ports are finite fixtures."""
    from tests.unit.platform.engine_bootstrap.test_d7_control_migration_contract import (
        prepare_through_actual_owner_source,
    )

    initial = initial_completion_inputs()
    frozen_profile = initial["pair"].profile_sha256
    frozen_bounds = v.sample("InventoryBounds")
    owner = initial["owner"]
    record = d.complete_initial_reconciliation(**initial).replacement
    generation_id, reserved = d.reserve_generation(record, owner, 300)
    assert generation_id == 1
    raw_intent = v.intent()
    raw_intent["request"]["count"] = task_count
    raw_intent.update(
        request_bytes=s.encode64(s.canonical(raw_intent["request"])),
        request_sha256=s.digest(s.canonical(raw_intent["request"])),
    )
    task_ids = tuple(v.TASK if i == 0 else v.TASK + "-" + str(i) for i in range(task_count))
    task_inventory = tuple(
        dict(v.control_observation()["payload"]["tasks"][0], task_arn=task_id) for task_id in task_ids
    )
    prepared, connection = prepare_through_actual_owner_source(
        reserved.replacement,
        reserved.append[0][1],
        credential_binding_sha256=s.digest(s.canonical(raw_intent["credential_binding"])),
    )
    core = c.parse("GenerationCore", s.canonical(prepared.value()["generation_core"]))
    assert core.login_oid == prepared.value()["principal"]["login_oid"] == 17
    assert any(isinstance(call, tuple) and "pg_catalog.pg_roles" in call[0] for call in connection.calls)
    decision_wire = v.decision(core.purpose)
    decision_wire.update(
        generation_core=core.to_wire(),
        trust_profile_sha256=frozen_profile,
        scope=record.value()["scope"],
        epoch=1,
        run_id=record.value()["run_id"],
    )
    decision = c.parse("CandidateDecision", s.canonical(decision_wire))
    generation = c.parse(
        "RuntimeAdmissionGeneration",
        s.canonical(
            dict(
                binding=core.to_wire(),
                activation_decision_sha256=decision.digest(),
                status="PREPARED",
                revision=1,
            )
        ),
    )
    c.check_generation_decision(generation, decision)
    issuer_wire = v.issuer("candidate", "PREPARED")
    issuer_wire.update(
        binding_sha256=core.digest(),
        activation_decision_sha256=decision.digest(),
        scope=core.scope.to_wire(),
        epoch=1,
        generation_id=1,
        revision=1,
    )
    issuer = c.parse("IssuerReceipt", s.canonical(issuer_wire))
    verifier_once = None
    signed_digests: list[str] = []

    def observe(
        current: s.Document,
        journals: tuple[c.JournalObservation, ...] = (),
        managed: tuple[c.ManagedResource, ...] = (),
        *,
        tasks: bool = False,
    ) -> d.ObservationPair:
        nonlocal verifier_once
        same_root, pair, verifier = pair_for(
            current, journals, managed, (generation,), unregistered_tasks=tasks, task_inventory=task_inventory
        )
        assert same_root.wire == current.wire  # No per-snapshot ROOT/profile rewrite.
        assert verifier.profile.digest() == frozen_profile == decision.trust_profile_sha256
        assert verifier.bounds.to_wire() == frozen_bounds
        if verifier_once is None:
            verifier_once = verifier
        assert verifier.profile.canonical_bytes() == verifier_once.profile.canonical_bytes()
        verified = verifier_once.verify(
            pair.control,
            pair.database,
            challenge=pair.control.challenge,
            request_sha256=pair.control.request_sha256,
            now_ms=200,
            deadline_ms=900,
        )
        verified.current(current, 300)
        signed_digests.append(verified.digest())
        return verified

    record = reserved.replacement
    pair = observe(record)
    spy = BindingOnlyQualification()
    published = d.publish_generation(
        record, reserved.append[0][1], generation, decision, issuer, pair, 300, qualification=spy
    )
    assert published.documents[0].expected_wire == reserved.append[0][1]
    assert s.parse_wire(published.documents[0].replacement_wire)["core"]["login_oid"] == core.login_oid
    record = published.replacement

    def stage(
        current: s.Document,
        target: str,
        journals: tuple[c.JournalObservation, ...] = (),
        managed: tuple[c.ManagedResource, ...] = (),
    ) -> s.Document:
        pair = observe(current, journals, managed)
        evidence = s.canonical(
            dict(
                scope=current.value()["scope"],
                run_id=current.value()["run_id"],
                epoch=1,
                previous_state=current.value()["state"],
                target_state=target,
                root_sha256=current.digest(),
                observation_pair_sha256=pair.digest(),
                stage_evidence_bytes=s.encode64(b"{}"),
                stage_evidence_sha256=s.digest(b"{}"),
            )
        )
        return d.advance(
            current,
            target,
            pair,
            now_ms=300,
            evidence_wire=evidence,
            qualification=spy,
            decision=decision if target == "CANDIDATE_ADMITTED" else None,
        ).replacement

    for target in d.STATES[1 : d.STATES.index("CANDIDATE_ADMITTED") + 1]:
        record = stage(record, target)
    generation = replace(generation, status="OPEN", revision=2)
    pair = observe(record)
    raw_intent.update(owner_subject=owner["owner_subject"], logical_run_id=owner["run_id"], epoch=1)
    raw_intent["expected_precondition"].update(
        controller_revision=record.value()["revision"],
        db_fence_revision=pair.database.payload.fence.revision,
        journal_revision=record.value()["journal_revision"],
        pending_index_sha256=record.value()["pending_index_sha256"],
        target_generation_status="OPEN",
    )
    intent = c.parse("RunTaskIntent", s.canonical(raw_intent))
    append_change = d.append_intent(record, intent, pair, 300, generation=generation, decision=decision)
    record = append_change.replacement
    row = v.journal("INTENT")
    row.update(intent_sha256=intent.digest(), logical_run_id=owner["run_id"], epoch=1)
    journal = c.parse("JournalObservation", s.canonical(row))
    reservation_wire = v.sample("DispatcherReservation")
    reservation_wire["intent_sha256"] = intent.digest()
    journal, change = d.reserve_dispatch(
        record,
        journal,
        c.parse("DispatcherReservation", s.canonical(reservation_wire)),
        observe(record, (journal,)),
        300,
        qualification=spy,
    )
    dispatch_change = change
    record = change.replacement
    outcome = v.sample("ProviderOutcome")
    outcome.update(returned_resource_ids=list(task_ids), failures=[])
    change = d.record_outcome(
        record,
        journal,
        c.parse("ProviderOutcome", s.canonical(outcome)),
        revision=record.value()["journal_revision"] + 1,
        now_ms=300,
        qualification=spy,
    )
    ack_change = change
    record, journal = change.replacement, change.journals[0].replacement
    managed = tuple(
        c.parse(
            "ManagedResource",
            s.canonical(
                dict(
                    v.control_observation()["payload"]["managed_resources"][0],
                    provider_resource_id=task_id,
                    observation_sha256=s.digest(s.canonical(task)),
                )
            ),
        )
        for task_id, task in zip(task_ids, task_inventory, strict=True)
    )
    settlement_wire = v.sample("Settlement")
    settlement_wire.update(
        settlement_class="CURRENT_LIVE_TRACKED",
        resource_proofs=[],
        managed_resource_registry_sha256=s.digest(s.canonical([m.to_wire() for m in managed])),
    )
    settlement_wire["dispatch_terminal"]["intent_sha256"] = intent.digest()
    return dict(
        root=record,
        journal=journal,
        settlement=c.parse("Settlement", s.canonical(settlement_wire)),
        managed=managed,
        qualification=spy,
        now_ms=300,
        observe=observe,
        stage=stage,
        intent=intent,
        changes=(append_change, dispatch_change, ack_change),
        frozen_profile=frozen_profile,
        generation=generation,
        decision=decision,
        signed_digests=signed_digests,
    )


@pytest.mark.parametrize("task_count", [1, 8, 10])
def test_one_frozen_profile_supports_owner_preparation_publication_and_live_journal_lifecycle(
    task_count: int,
) -> None:
    values = frozen_lifecycle_inputs(task_count)
    journal, change = d.settle(
        values["root"],
        values["journal"],
        values["settlement"],
        values["observe"](values["root"], (values["journal"],), tasks=True),
        300,
        values["managed"],
        qualification=values["qualification"],
    )
    assert len(change.documents) == task_count
    from tests.unit.platform.engine_bootstrap.test_controller_dynamodb import binding

    from maezo.platform.engine_bootstrap import controller_dynamodb as dynamo

    if task_count <= 8:
        physical = dynamo.transaction(binding(), change)
        assert len(physical["TransactItems"]) == 3 + task_count
    else:
        # Logical ten-task settlement is valid; thirteen physical actions remain forbidden.
        with pytest.raises(s.Refusal):
            dynamo.transaction(binding(), change)
    record = values["stage"](change.replacement, "CANDIDATE_VALIDATED", (journal,), values["managed"])
    assert record.value()["state"] == "CANDIDATE_VALIDATED" and record.value()["pending_operation_ids"] == []
    assert record.value()["trust_profile_sha256"] == values["frozen_profile"]
    assert values["generation"].activation_decision_sha256 == values["decision"].digest()
    assert len(set(values["signed_digests"])) > 5


def test_v2_full_signature_and_separate_source_census_both_remain_required() -> None:
    record, original, verifier = pair_for(root(state="LEASED"))
    payload = replace(original.control.payload, task_definitions=())
    tampered = replace(original.control, payload=payload)
    with pytest.raises(s.Refusal, match="AUTH_REFUSED"):
        verifier.verify(
            tampered,
            original.database,
            challenge=tampered.challenge,
            request_sha256=tampered.request_sha256,
            now_ms=200,
            deadline_ms=900,
        )
    # A legitimate key could sign an incomplete census: the source qualification
    # boundary, not a recomputed caller hash or signature, must reject that case.
    signed_prefix = replace(
        tampered,
        signature=s.encode64(
            Ed25519PrivateKey.from_private_bytes(b"c" * 32).sign(c.signature_input(tampered))
        ),
    )
    pair = verifier.verify(
        signed_prefix,
        original.database,
        challenge=signed_prefix.challenge,
        request_sha256=signed_prefix.request_sha256,
        now_ms=200,
        deadline_ms=900,
    )

    class SourceCensusPort:
        """Finite source-census fixture, not an implemented production observer."""

        def check(self, **kwargs: Any) -> None:
            retained = s.parse_wire(kwargs["evidence_wire"])
            observed = c.parse("ControlObservation", s.decode64(retained["control_observation_bytes"]))
            s.require(
                observed.payload.task_definitions == original.control.payload.task_definitions,
                "PENDING_UNKNOWN",
            )

    evidence = s.canonical(dict(control_observation_bytes=s.encode64(signed_prefix.canonical_bytes())))
    wrapped = s.canonical(
        dict(
            scope=record.value()["scope"],
            run_id=U,
            epoch=9,
            previous_state="LEASED",
            target_state="START_FENCED",
            root_sha256=record.digest(),
            observation_pair_sha256=pair.digest(),
            stage_evidence_bytes=s.encode64(evidence),
            stage_evidence_sha256=s.digest(evidence),
        )
    )
    with pytest.raises(s.Refusal, match="PENDING_UNKNOWN"):
        d.advance(
            record, "START_FENCED", pair, now_ms=300, evidence_wire=wrapped, qualification=SourceCensusPort()
        )
