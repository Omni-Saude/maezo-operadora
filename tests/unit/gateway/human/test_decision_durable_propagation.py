"""Finite D6 real-method orchestration controls; no DB/engine integration claim."""

import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from tests.unit.gateway.human.test_durable_projection import receipt_payload
from tests.unit.gateway.human.test_durable_relay import rig
from tests.unit.gateway.human.test_receipt_recording_metadata import public_values
from tests.unit.portal.test_decision_engine_profile import payload as engine_payload

from maezo.gateway.human.decision import ClassifiedDecision, DecisionAdmission
from maezo.gateway.human.models import PendingAdmission, Scope
from maezo.gateway.human.outbox import HumanOutboxError, PostgresDecisionAdmission, PostgresHumanOutbox
from maezo.gateway.human.projection import ProjectionError, restore_command, verify_engine_receipt
from maezo.gateway.human.receipt import PublicReceipt
from maezo.portal.engine.decision import HumanDecisionCommand
from maezo.portal.engine.profile import canonicalize


def payload():
    value = engine_payload()
    value["scope"]["tenant"] = "tenant_test"
    value["audit_intent_ref"] = hashlib.sha256(
        canonicalize([value["scope"], value["target"]["task_id"], value["target"]["command_id"]])
    ).hexdigest()
    return value


def decision():
    return HumanDecisionCommand(canonicalize(payload()))


def scope():
    return Scope(**payload()["scope"])


def classified():
    p = payload()
    p["target"] = {k: int(v) if k.endswith(("revision", "version")) else v for k, v in p["target"].items()}
    return ClassifiedDecision.model_validate(p)


def receipt(**changes):
    return receipt_payload(
        decision(), schema="human-engine-receipt.v2", resulting_task_revision=None, **changes
    )


def test_restored_decision_and_receipt_keep_exact_identity_and_digest():
    c = restore_command(decision().canonical)
    r = verify_engine_receipt(receipt(), c)
    assert c.digest == classified().digest
    assert r.operation == "decision" and r.resulting_task_revision is None


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "claim"},
        {"payload_digest": "0" * 64},
        {"principal_ref": "other"},
        {"task_id": "successor"},
        {"command_id": "new"},
        {"consumed_task_revision": "9"},
    ],
)
def test_decision_receipt_mismatch_refuses(changes):
    with pytest.raises(ProjectionError):
        verify_engine_receipt(receipt(**changes), decision())


def test_decision_completion_receipt_is_distinct_from_assignment():
    with pytest.raises(ProjectionError):
        verify_engine_receipt(receipt_payload(decision(), resulting_task_revision=None), decision())
    with pytest.raises(ProjectionError):
        verify_engine_receipt(receipt_payload(decision(), schema="human-engine-receipt.v2"), decision())
    values = public_values(decision())
    values.update(
        schema_version="human-public-receipt.v2", operation="decision", resulting_task_revision=None
    )
    assert PublicReceipt.model_validate(values).model_dump()["operation"] == "decision"
    for change in (
        {"operation": None},
        {"schema_version": "human-public-receipt.v1"},
        {"resulting_task_revision": "3"},
    ):
        with pytest.raises(ValueError):
            PublicReceipt.model_validate({**values, **change})
    assert "operation" not in PublicReceipt.model_validate(public_values()).model_dump()


@pytest.mark.asyncio
async def test_lost_response_reconciles_same_classified_identity_without_second_dispatch():
    relay, store, transport = rig()
    store.scope = scope()
    store.lease = replace(store.lease, scope=scope(), command=decision())
    transport.scope = store.scope
    transport.error = RuntimeError("response unavailable")
    await relay.run_once()
    assert store.rows == 1 and store.events[-1] == "pending"
    transport.previous = receipt()
    store.events.clear()
    await relay.run_once()
    assert "dispatch" not in store.events and store.rows == 0
    assert all(c == decision().canonical for c in transport.commands)


@pytest.mark.asyncio
async def test_result_audit_failure_keeps_decision_pending_until_audited_recovery():
    relay, store, transport = rig()
    store.lease = replace(store.lease, command=decision())
    transport.previous = receipt()
    store.fail_result = True
    await relay.run_once()
    assert store.rows == 1 and store.events[-1] == "pending"
    store.fail_result = False
    await relay.run_once()
    assert store.rows == 0 and "dispatch" not in store.events


@pytest.mark.asyncio
async def test_admission_uses_existing_transactional_outbox_and_exact_classified_digest():
    store = PostgresHumanOutbox(scope=scope(), pool=None)
    adapter = PostgresDecisionAdmission(store)
    assert isinstance(adapter, DecisionAdmission)
    c = decision()
    ack = PendingAdmission(
        schema_version=1,
        tenant=c.tenant,
        task_id=c.task_id,
        command_id=c.command_id,
        principal_ref=c.principal_ref,
        workload_ref=c.workload_ref,
        audit_intent_ref=c.audit_intent_ref,
        outbox_ref="outbox-test",
        transaction_ref="transaction-test",
        committed_at=datetime.now(UTC),
    )
    store.persist = AsyncMock(return_value=ack)
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    result = await adapter.admit(classified(), valid_until=deadline)
    args, kwargs = store.persist.call_args
    assert args[0].canonical == classified().canonical
    assert kwargs == {"evidence_valid_until": deadline}
    assert (
        result.payload_digest == classified().digest and result.request_digest == classified().request_digest
    )
    store.persist.side_effect = RuntimeError("uncertain commit")
    with pytest.raises(HumanOutboxError, match="human decision admission unavailable"):
        await adapter.admit(classified(), valid_until=deadline)


@pytest.mark.asyncio
async def test_expired_and_cross_environment_decision_never_enter_transaction():
    store = PostgresHumanOutbox(scope=scope(), pool=None)
    with pytest.raises(HumanOutboxError):
        await store.persist(decision(), evidence_valid_until=datetime.now(UTC) - timedelta(seconds=1))
    other = PostgresHumanOutbox(scope=scope().model_copy(update={"environment": "other"}), pool=None)
    with pytest.raises(HumanOutboxError):
        other._scope(decision())


@pytest.mark.asyncio
async def test_actual_persist_body_checks_deadline_after_last_database_write():
    """Controlled SQL port, actual persist method; no PostgreSQL transaction proof."""
    store = PostgresHumanOutbox(scope=scope(), pool=None)
    events = []
    c = decision()
    deadline = datetime.now(UTC) + timedelta(minutes=1)

    class SQL:
        async def fetchrow(self, sql, *args):
            events.append("insert" if sql.startswith("INSERT") else "read")
            if sql.startswith("SELECT"):
                return None
            return {"outbox_ref": "outbox-test", "transaction_ref": "transaction-test"}

        async def execute(self, sql, *args):
            events.append("delivery")

    @asynccontextmanager
    async def tx(**kwargs):
        assert kwargs == {"audit_lock": True}
        events.append("begin")
        yield SQL()
        events.append("commit")

    store._transaction = tx
    from types import SimpleNamespace

    store._audit.emit_once_on = AsyncMock(return_value=SimpleNamespace(deduped=False, record_hash="a" * 64))
    ack = await store.persist(c, evidence_valid_until=deadline)
    assert events == ["begin", "read", "insert", "delivery", "commit"]
    assert ack.status == "pending"
    record = store._audit.emit_once_on.call_args.args[1]
    assert record.details["payload_digest"] == c.digest
    assert record.agent_version == "human-classified-decision.v1"
    assert "custody-test" not in str(record.details)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["deadline", "uncertain_commit"])
async def test_actual_persist_never_acknowledges_expired_or_uncertain_commit(monkeypatch, failure):
    from types import SimpleNamespace

    import maezo.gateway.human.outbox as module

    now = datetime.now(UTC)
    deadline = now + timedelta(seconds=5)
    clock = [now]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(module, "datetime", Clock)
    store = PostgresHumanOutbox(scope=scope(), pool=None)
    events = []

    class SQL:
        async def fetchrow(self, sql, *args):
            return (
                None
                if sql.startswith("SELECT")
                else {"outbox_ref": "outbox-test", "transaction_ref": "transaction-test"}
            )

        async def execute(self, sql, *args):
            events.append("delivery")
            if failure == "deadline":
                clock[0] = deadline

    @asynccontextmanager
    async def tx(**kwargs):
        try:
            yield SQL()
            events.append("commit_requested")
            raise HumanOutboxError("uncertain commit")
        except BaseException:
            events.append("uncertain" if failure == "uncertain_commit" else "rollback_requested")
            raise

    store._transaction = tx
    store._audit.emit_once_on = AsyncMock(return_value=SimpleNamespace(deduped=False, record_hash="a" * 64))
    with pytest.raises(HumanOutboxError):
        await store.persist(decision(), evidence_valid_until=deadline)
    assert events == (
        ["delivery", "rollback_requested"]
        if failure == "deadline"
        else ["delivery", "commit_requested", "uncertain"]
    )


@pytest.mark.asyncio
async def test_persist_same_command_identity_with_changed_custody_conflicts():
    from types import SimpleNamespace

    from maezo.gateway.human.outbox import CommandConflictError

    c = decision()
    row = {
        name: getattr(c, name)
        for name in (
            "tenant",
            "task_id",
            "command_id",
            "workload_ref",
            "principal_ref",
            "principal_issuer",
            "principal_subject",
            "audit_intent_ref",
        )
    }
    row.update(
        environment="test",
        payload_digest=c.digest,
        canonical_payload=c.canonical,
        audit_intent_hash="a" * 64,
        outbox_ref="outbox-test",
        transaction_ref="transaction-test",
    )
    store = PostgresHumanOutbox(scope=scope(), pool=None)

    class SQL:
        async def fetchrow(self, sql, *args):
            return row

    @asynccontextmanager
    async def tx(**kwargs):
        yield SQL()

    store._transaction = tx
    store._verify_link = AsyncMock()
    store._audit.emit_once_on = AsyncMock(return_value=SimpleNamespace(deduped=True, record_hash="a" * 64))
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    ack = await store.persist(c, evidence_valid_until=deadline)
    assert ack.outbox_ref == "outbox-test"
    p = payload()
    p["human_basis"]["custody_ref"] = "different-custody"
    with pytest.raises(CommandConflictError):
        await store.persist(HumanDecisionCommand(canonicalize(p)), evidence_valid_until=deadline)
