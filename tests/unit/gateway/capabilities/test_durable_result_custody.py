"""Actual driver/adapter/DUR3 custody controls; synthetic ports, no provider/PG acceptance."""

import asyncio
from copy import deepcopy
from datetime import timedelta

import pytest

from maezo.gateway.capabilities.admission import result_digest
from maezo.gateway.capabilities.durability.models import CommandTechnicalState
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from tests.unit.gateway.capabilities.journeys.test_effect_execution import harness, run
from tests.unit.gateway.capabilities.test_admission import EXPIRY, NOW


def corrupt(result, proof, field):
    if field.startswith("binding."):
        key = field.removeprefix("binding.")
        proof.binding.__dict__[key] = "phi" if key == "security_zone" else "UNIT-unverified-substitution"
    elif field == "body_and_digest":
        result.__dict__["context_refs"] = ("UNIT-unverified-result-body",)
        proof.__dict__["result_sha256"] = result_digest(result)
    elif field == "body_extra":
        result.__dict__["unpublished_extra"] = "UNIT-unverified"
    elif field == "proof_extra":
        proof.__dict__["unpublished_extra"] = "UNIT-unverified"
    elif field == "checked_at":
        proof.__dict__[field] = NOW - timedelta(seconds=1)
    elif field == "valid_until":
        proof.__dict__[field] = EXPIRY + timedelta(seconds=1)
    else:
        proof.__dict__[field] = "f" * 64 if field.endswith("sha256") else "UNIT-unverified-substitution"


def delayed_preparation(h, field):
    original = h.preparation.prepare
    received = []

    async def prepare(b, descriptor, handle, result, proof):
        received.append((result, proof))
        await asyncio.sleep(0)
        if field is not None:
            corrupt(result, proof, field)
        return await original(b, descriptor, handle, result, proof)

    h.preparation.prepare = prepare
    return received


async def assert_reconcile_only(h, outcome):
    assert outcome.technical_refusal is not None
    assert outcome.verified_transition_ref is None
    assert h.source.calls == h.source.effects == 1
    assert h.legacy.calls == 0
    assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
    assert "record_verified_result" not in h.authority.events
    assert h.journal.recorded_waits == h.journal.recorded_outbox == ()
    handle = deepcopy(h.journal.snapshot.handle)
    dispatch = h.journal.snapshot.dispatch_ref
    key = h.journal.descriptor.envelope.idempotency_key
    await h.adapter.execute(
        h.binding,
        h.action,
        effect_authority=h.context,
        expected_journal_revision=h.journal.snapshot.journal_revision,
    )
    assert h.journal.snapshot.handle == handle
    assert h.journal.snapshot.dispatch_ref == dispatch
    assert h.journal.descriptor.envelope.idempotency_key == key
    assert h.source.calls == h.source.effects == 1 and h.legacy.calls == 0


@pytest.mark.asyncio
async def test_actual_genuine_result_and_atomic_intents_commit_once():
    h = harness()
    h.preparation.with_intents = True
    received = delayed_preparation(h, None)
    result = await run(h)
    assert result.technical_refusal is None
    assert h.source.calls == h.source.effects == 1 and h.legacy.calls == 0
    assert h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert h.journal.snapshot.source_receipt_ref == "unit-domain-receipt"
    assert h.journal.snapshot.source_revision_ref == "unit-revision-2"
    assert h.journal.snapshot.result_sha256 == result_digest(received[0][0])
    assert h.authority.events.count("record_verified_result") == 1
    assert len(h.journal.recorded_waits) == len(h.journal.recorded_outbox) == 1
    recorded = deepcopy(h.journal.snapshot)
    await h.adapter.execute(
        h.binding,
        h.action,
        effect_authority=h.context,
        expected_journal_revision=recorded.journal_revision,
    )
    assert h.journal.snapshot == recorded and h.source.effects == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "source_receipt_ref",
        "source_revision_ref",
        "request_sha256",
        "result_sha256",
        "authorization_ref",
        "currentness_ref",
        "checked_at",
        "valid_until",
        "proof_extra",
        "body_extra",
        "body_and_digest",
        *[
            "binding." + name
            for name in (
                "principal_ref",
                "task_ref",
                "tenant_ref",
                "legal_entity_ref",
                "purpose_ref",
                "operation_name",
                "schema_version",
                "contract_revision",
                "source_authority_ref",
                "policy_revision",
                "data_classification",
                "autonomy_action",
                "security_zone",
            )
        ],
    ],
)
async def test_preparation_cannot_replace_any_original_result_or_attestation_pin(field):
    h = harness()
    h.preparation.with_intents = True
    delayed_preparation(h, field)
    originals = []
    private = h.admission._durable_source_result

    def capture(lease):
        value = private(lease)
        originals.append(deepcopy(value))
        return value

    h.admission._durable_source_result = capture
    result = await run(h)
    assert originals and all(proof == h.source_proof() for proof in originals)
    await assert_reconcile_only(h, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("retained", ["inputs", "prepared_observation", "wait", "outbox"])
async def test_retained_preparation_alias_mutation_during_currentness_cannot_reach_commit(retained):
    h = harness()
    h.preparation.with_intents = True
    received = delayed_preparation(h, None)
    current = h.admission._durable_before_result_write

    async def delayed(lease):
        await current(lease)
        await asyncio.sleep(0)
        commit = h.preparation.last_commit
        if retained == "inputs":
            corrupt(*received[0], "body_and_digest")
        elif retained == "prepared_observation":
            commit.observation.source_result.__dict__["source_receipt_ref"] = "UNIT-forged-later-receipt"
        elif retained == "wait":
            commit.wait_intents[0].intent.__dict__["correlation_ref"] = "UNIT-foreign-correlation"
        else:
            commit.outbox_intents[0].__dict__["target_binding_ref"] = "UNIT-unregistered-target"

    h.admission._durable_before_result_write = delayed
    await assert_reconcile_only(h, await run(h))


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["expired", "revoked", "renewed"])
async def test_currentness_after_preparation_blocks_stale_or_renewed_result_persistence(control):
    h = harness()
    original = h.preparation.prepare

    async def prepare(*args):
        commit = await original(*args)
        await asyncio.sleep(0)
        if control == "expired":
            h.tick = EXPIRY + timedelta(seconds=1)
        elif control == "revoked":
            h.authority.revoked = True
        else:
            h.authority.current_updates["valid_until"] = EXPIRY + timedelta(seconds=1)
        return commit

    h.preparation.prepare = prepare
    await assert_reconcile_only(h, await run(h))


@pytest.mark.asyncio
@pytest.mark.parametrize("later", ["retained_preparation_mutation", "prepared_observation", "expired"])
async def test_genuine_committed_fact_survives_later_refusal_without_disclosure_or_resubmit(later):
    h = harness()
    received = delayed_preparation(h, None)

    def after_result():
        if later == "expired":
            h.tick = EXPIRY + timedelta(seconds=1)
        elif later == "prepared_observation":
            h.preparation.last_commit.observation.source_result.__dict__["source_receipt_ref"] = (
                "UNIT-forged-after-commit"
            )
        else:
            corrupt(*received[0], "body_and_digest")

    h.journal.on_result = after_result
    outcome = await run(h)
    assert outcome.technical_refusal is not None and outcome.verified_transition_ref is None
    assert h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert h.journal.snapshot.source_receipt_ref == "unit-domain-receipt"
    assert h.journal.snapshot.result_sha256 == h.source_proof().result_sha256
    assert h.source.calls == h.source.effects == 1 and h.legacy.calls == 0
    historical = deepcopy(h.journal.snapshot)
    await h.adapter.execute(
        h.binding,
        h.action,
        effect_authority=h.context,
        expected_journal_revision=historical.journal_revision,
    )
    assert h.journal.snapshot == historical and h.source.effects == 1


@pytest.mark.asyncio
async def test_postcommit_read_proof_port_cannot_mutate_authentic_stored_head():
    h = harness()
    assert (await run(h)).technical_refusal is None
    historical = deepcopy(h.journal.snapshot)
    current = h.reads.current

    async def mutate(binding, descriptor, source):
        await asyncio.sleep(0)
        source.__dict__["source_receipt_ref"] = "UNIT-read-port-counterfeit"
        return await current(binding, descriptor, source)

    h.reads.current = mutate
    outcome = await h.adapter.execute(
        h.binding,
        h.action,
        effect_authority=h.context,
        expected_journal_revision=historical.journal_revision,
    )
    assert outcome == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert h.journal.snapshot == historical
    assert h.source.effects == 1 and h.legacy.calls == 0


@pytest.mark.asyncio
async def test_mutation_during_final_disclosure_currentness_does_not_disclose_or_erase_fact():
    h = harness()
    received = delayed_preparation(h, None)
    h.transition.hook = lambda phase: (
        corrupt(*received[0], "body_and_digest") if phase == "before_disclosure" else None
    )
    outcome = await run(h)
    assert outcome.technical_refusal is not None and outcome.verified_transition_ref is None
    assert h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert h.journal.snapshot.source_receipt_ref == "unit-domain-receipt"
    assert h.journal.snapshot.result_sha256 == h.source_proof().result_sha256
    assert h.source.calls == h.source.effects == 1 and h.legacy.calls == 0
