"""D6 EIR causal regressions: offline clock/HTTP/database doubles, never live proof."""

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from tests.unit.gateway.human.test_durable_projection import assignment, receipt_payload, wire
from tests.unit.gateway.human.test_durable_transport import transport

from maezo.gateway.audit import EmitOnceOutcome
from maezo.gateway.human import projection, receipt
from maezo.gateway.human.models import Scope
from maezo.gateway.human.outbox import (
    DeliveryLease,
    HumanOutboxError,
    LeaseLostError,
    PostgresHumanOutbox,
    _audit,
)
from maezo.gateway.human.projection import (
    EngineReceipt,
    ProjectionError,
    restore_command,
    verify_engine_receipt,
)
from maezo.gateway.human.receipt import PublicReceipt
from maezo.gateway.human.transport import EngineUnavailableError
from maezo.portal.engine.profile import canonicalize, strict_loads

# Exact synthetic bytes captured by SAME EIR delta-c8e9ecf5, not regenerated data.
CAUSAL_COMMAND = (
    b'{"assignee_ref":null,"audit_intent_ref":"b6c43beb1e0caa46e3186bacc70fc4299ef783a5668a'
    b'7aa89d097650c6bab97f","authority_revision":"12","command_id":"synthetic-command-3962e'
    b'c9b8c474b8785ec7aeae232d84e","evidence_digest":"073a94bc9ab2547b86fd55aedabd2270fdb68'
    b'7442e287392f03f7d741f6e6aae","evidence_ref":"synthetic-evidence-a2b70d83b3164c8ab0986'
    b'fb11259751a","evidence_revision":"12","form_digest":"96e5b97fc15cc94339e7dad25305cddd'
    b'fc1a572f97db62b1dda5efbd7321a94c","form_key":"maezo.synthetic-ack.v1","form_version":'
    b'"1","membership_revision":"11","operation":"claim","outcome":null,"principal_issuer":'
    b'"https://synthetic-identity.example.invalid","principal_ref":"synthetic-human-c0232b6'
    b'a12414be0a66b5f07357b9308","principal_subject":"synthetic-subject-62197fbe41c844e0b60'
    b'66d4b7ca1a7c8","process_definition_digest":"4faa91d6235c56f0e1d646f8d08183af4a9ec9dba'
    b'36d53ef1531113af1f29ab0","process_definition_id":"MZO-HUMAN-SYNTHETIC:1:c3a61672-abe8'
    b'-11f1-b00c-2af591699384","process_definition_key":"MZO-HUMAN-SYNTHETIC","process_defi'
    b'nition_version":"1","schema":"human-command.v1","task_definition_key":"UT_Acknowledge'
    b'","task_id":"c3aa352a-abe8-11f1-b00c-2af591699384","task_revision":"2","tenant":"rela'
    b'y_a8295289c392a7f52a0a2a43","workload_ref":"package-command-workload"}'
)
CAUSAL_RECEIPT = (
    b'{"audit_intent_ref":"b6c43beb1e0caa46e3186bacc70fc4299ef783a5668a7aa89d097650c6bab97f'
    b'","command_id":"synthetic-command-3962ec9b8c474b8785ec7aeae232d84e","consumed_task_re'
    b'vision":"2","engine_receipt_ref":"7f837cb1-6c51-4d20-8ff5-157c0d82ba66","operation":"'
    b'claim","payload_digest":"58b7579fd76acd432700ecba664948bad6fc9f8783aae26974b50b1ec9e2'
    b'b64c","principal_ref":"synthetic-human-c0232b6a12414be0a66b5f07357b9308","recorded_at'
    b'":"1788915163","resulting_task_revision":"3","schema":"human-engine-receipt.v1","stat'
    b'us":"committed","task_id":"c3aa352a-abe8-11f1-b00c-2af591699384","tenant":"relay_a829'
    b'5289c392a7f52a0a2a43","workload_ref":"package-command-workload"}'
)

BOUNDARY = datetime(2026, 9, 9, 0, 52, 42, 983038, tzinfo=UTC)
RECORDED = datetime(2026, 9, 9, 0, 52, 43, tzinfo=UTC)


class ClientClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return BOUNDARY.astimezone(tz)


@pytest.fixture
def metadata_clock(monkeypatch):
    # Freeze only the two receipt metadata consumers, never authority or lease clocks.
    monkeypatch.setattr(projection, "datetime", ClientClock)
    monkeypatch.setattr(receipt, "datetime", ClientClock)


def public_values(c=None, recorded=RECORDED):
    c = c or restore_command(CAUSAL_COMMAND)
    return dict(
        tenant=c.tenant,
        task_id=c.task_id,
        command_id=c.command_id,
        payload_digest=c.digest,
        principal_ref=c.principal_ref,
        workload_ref=c.workload_ref,
        status="committed",
        audit_intent_ref=c.audit_intent_ref,
        audit_intent_hash="a" * 64,
        audit_result_ref="b" * 64,
        engine_receipt_ref="receipt-1",
        engine_recorded_at=recorded,
        consumed_task_revision=c.task_revision,
        resulting_task_revision="3",
    )


def test_exact_causal_receipt_accepts_registration_metadata_ahead_of_client(metadata_clock):
    c = restore_command(CAUSAL_COMMAND)
    assert c.canonical == CAUSAL_COMMAND
    assert c.digest == "58b7579fd76acd432700ecba664948bad6fc9f8783aae26974b50b1ec9e2b64c"
    assert hashlib.sha256(CAUSAL_RECEIPT).hexdigest() == (
        "93dd49a0134900b781582d77db2cfbc99a7f4892fc52e59838474a27f07c76d1"
    )
    assert timedelta(microseconds=16962) == RECORDED - BOUNDARY
    verified = verify_engine_receipt(CAUSAL_RECEIPT, c)
    assert verified.recorded_at == "1788915163"
    assert verified.engine_recorded_at == RECORDED
    assert canonicalize(verified.model_dump(by_alias=True)) == CAUSAL_RECEIPT


def test_public_receipt_accepts_same_registration_metadata_ahead_of_client(metadata_clock):
    # Independent downstream boundary: baseline fails here even if the first gate is bypassed.
    verified = EngineReceipt.model_validate(strict_loads(CAUSAL_RECEIPT))
    result = PublicReceipt.model_validate(public_values(recorded=verified.engine_recorded_at))
    assert result.status == "committed" and result.engine_recorded_at == RECORDED


@pytest.mark.parametrize("epoch", ["0", "253402300799"])
def test_engine_metadata_accepts_full_representable_nonnegative_range(metadata_clock, epoch):
    c = restore_command(CAUSAL_COMMAND)
    raw = json.dumps({**strict_loads(CAUSAL_RECEIPT), "recorded_at": epoch}).encode()
    verified = verify_engine_receipt(raw, c)
    assert verified.engine_recorded_at == datetime.fromtimestamp(int(epoch), UTC)
    assert (
        PublicReceipt.model_validate(public_values(recorded=verified.engine_recorded_at)).status
        == "committed"
    )


@pytest.mark.parametrize(
    "epoch",
    [
        "",
        "01",
        "+1",
        "-1",
        "1.0",
        "1e2",
        " 1",
        "1\n",
        "١",
        None,
        True,
        False,
        1,
        1.5,
        "253402300800",
        "9223372036854775807",
        "9223372036854775808",
        "9" * 5000,
    ],
    ids=[
        "empty",
        "leading-zero",
        "plus",
        "negative",
        "fraction",
        "exponent",
        "space",
        "newline",
        "unicode",
        "null",
        "true",
        "false",
        "number",
        "float",
        "datetime-overflow",
        "int64-max",
        "int64-overflow",
        "oversized",
    ],
)
def test_engine_metadata_rejects_noncanonical_or_unrepresentable_epoch(metadata_clock, epoch):
    c = restore_command(CAUSAL_COMMAND)
    raw = json.dumps({**strict_loads(CAUSAL_RECEIPT), "recorded_at": epoch}).encode()
    with pytest.raises(ProjectionError, match="^receipt verification unavailable$"):
        verify_engine_receipt(raw, c)


@pytest.mark.parametrize(
    "recorded",
    [
        RECORDED.replace(tzinfo=None),
        RECORDED.astimezone(timezone(timedelta(hours=1))),
        "10000-01-01T00:00:00Z",
        "not-a-date",
    ],
)
def test_public_metadata_rejects_non_utc_or_unrepresentable_datetime(metadata_clock, recorded):
    with pytest.raises(ValidationError):
        PublicReceipt.model_validate(public_values(recorded=recorded))


@pytest.mark.parametrize(
    "changes",
    [
        dict(status="pending"),
        dict(status="conflict"),
        dict(audit_result_ref=None),
        dict(engine_receipt_ref=None),
        dict(engine_recorded_at=None),
        dict(consumed_task_revision=None),
        dict(resulting_task_revision=None),
        dict(technical_code="REVISION_CONFLICT"),
    ],
)
def test_ahead_metadata_does_not_relax_terminal_audit_shape(metadata_clock, changes):
    with pytest.raises(ValidationError):
        PublicReceipt.model_validate({**public_values(), **changes})


@pytest.mark.parametrize("invalid", [False, True])
async def test_transport_metadata_boundary_preserves_bytes_or_refuses(metadata_clock, monkeypatch, invalid):
    c = wire()
    raw = receipt_payload(c, recorded_at="253402300800" if invalid else "1788915163")
    adapter, _ = transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=raw, headers={"Content-Type": "application/json"}),
    )
    if invalid:
        with pytest.raises(EngineUnavailableError):
            await adapter.dispatch(c)
    else:
        assert await adapter.dispatch(c) == raw


class UnitDatabase:
    """Ports only: exercises actual finish/read/link/transaction exception boundaries.

    Does not claim PostgreSQL locking, rollback or authenticated engine execution.
    """

    def __init__(self, c, scope):
        self.c, self.scope = c, scope
        self.rows, self.claims = {}, {}
        self.row = dict(
            strict_loads(c.canonical),
            environment=scope.environment,
            canonical_payload=c.canonical,
            payload_digest=c.digest,
            status="pending",
            engine_receipt=None,
            technical_code=None,
            audit_result_hash=None,
        )
        self.row["audit_intent_hash"] = self.append(
            _audit(c, "intent", {}), "human:intent:" + c.audit_intent_ref
        )
        self.updates = []
        self.lease_present = True

    def append(self, record, dedup_key):
        record.record_hash = record._compute_hash()
        row = {
            key: getattr(record, key)
            for key in (
                "agent_id",
                "tenant_id",
                "agent_version",
                "action",
                "decision",
                "timestamp",
                "dmn_versions",
                "model_id",
                "prompt_version",
                "record_hash",
            )
        }
        row.update(
            decision_basis=record.details,
            prev_record_hash=record.prev_hash,
            input_hash=record.compute_input_hash(),
        )
        self.rows[record.record_hash] = row
        self.claims[dedup_key] = record.record_hash
        return record.record_hash

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        pass

    async def fetch(self, sql, record_hash):
        return [self.rows[record_hash]] if record_hash in self.rows else []

    async def fetchrow(self, sql, *args):
        if "FOR UPDATE" in sql and not self.lease_present:
            return None
        return self.row

    async def fetchval(self, sql, *args):
        if sql == "SELECT current_schema()":
            return self.scope.tenant
        if "audit_emit_dedup" in sql:
            return self.claims.get(args[1])
        assert sql.startswith("UPDATE human_command_delivery SET status=")
        self.updates.append(args)
        self.row.update(
            status=args[5], engine_receipt=args[6], technical_code=args[7], audit_result_hash=args[8]
        )
        return self.c.command_id

    async def emit_once_on(self, conn, record, *, dedup_key):
        assert conn is self
        return EmitOnceOutcome(self.append(record, dedup_key), False)


def storage():
    c = restore_command(CAUSAL_COMMAND)
    scope = Scope(tenant=c.tenant, environment="test", workload_ref=c.workload_ref)
    db = UnitDatabase(c, scope)
    outbox = PostgresHumanOutbox(scope=scope, pool=db)
    outbox._audit = SimpleNamespace(emit_once_on=db.emit_once_on)
    lease = DeliveryLease(scope, c, "lease-unit", 1, datetime.now(UTC) + timedelta(minutes=1))
    principal = assignment().principal.model_copy(
        update=dict(
            tenant=c.tenant,
            principal_ref=c.principal_ref,
            issuer=c.principal_issuer,
            subject=c.principal_subject,
        )
    )
    return db, outbox, lease, principal


async def test_finish_and_durable_read_preserve_causal_receipt_and_audit_link(metadata_clock):
    db, outbox, lease, principal = storage()
    await outbox.finish(lease, receipt=CAUSAL_RECEIPT)
    assert db.updates[0][6] is CAUSAL_RECEIPT
    assert db.row["status"] == "committed"
    result = await outbox.read_owned(principal, lease.command.task_id, lease.command.command_id)
    assert result.status == "committed" and result.engine_recorded_at == RECORDED
    assert result.audit_result_ref == db.row["audit_result_hash"]
    details = db.rows[result.audit_result_ref]["decision_basis"]
    assert details["engine_recorded_at"] == "1788915163"
    assert details["engine_receipt_digest"] == hashlib.sha256(CAUSAL_RECEIPT).hexdigest()


async def test_invalid_metadata_finish_and_read_keep_original_error_boundaries(metadata_clock):
    raw = canonicalize({**strict_loads(CAUSAL_RECEIPT), "recorded_at": "253402300800"})
    db, outbox, lease, principal = storage()
    with pytest.raises(ProjectionError, match="^receipt verification unavailable$"):
        await outbox.finish(lease, receipt=raw)
    assert not db.updates and len(db.rows) == 1
    db.row.update(status="committed", engine_receipt=raw, audit_result_hash="b" * 64)
    with pytest.raises(HumanOutboxError, match="^human durable transaction unavailable$"):
        await outbox.read_owned(principal, lease.command.task_id, lease.command.command_id)


@pytest.mark.parametrize("tamper", ["receipt", "audit_details", "audit_claim", "command"])
async def test_ahead_metadata_durable_read_still_requires_exact_audit_proof(metadata_clock, tamper):
    db, outbox, lease, principal = storage()
    await outbox.finish(lease, receipt=CAUSAL_RECEIPT)
    if tamper == "receipt":
        db.row["engine_receipt"] = canonicalize({**strict_loads(CAUSAL_RECEIPT), "recorded_at": "1788915164"})
    elif tamper == "audit_details":
        db.rows[db.row["audit_result_hash"]]["decision_basis"]["engine_recorded_at"] = "1788915164"
    elif tamper == "audit_claim":
        db.claims["human:result:" + lease.command.audit_intent_ref] = "f" * 64
    else:
        db.row["payload_digest"] = "f" * 64
    with pytest.raises(HumanOutboxError, match="linkage unavailable"):
        await outbox.read_owned(principal, lease.command.task_id, lease.command.command_id)


@pytest.mark.parametrize("fence", ["database", "client"])
async def test_ahead_metadata_cannot_bypass_completion_lease_fence(metadata_clock, fence):
    db, outbox, lease, _ = storage()
    if fence == "database":
        db.lease_present = False
    else:
        lease = DeliveryLease(
            lease.scope, lease.command, lease.lease_id, lease.fence, datetime.now(UTC) - timedelta(seconds=1)
        )
    with pytest.raises(LeaseLostError):
        await outbox.finish(lease, receipt=CAUSAL_RECEIPT)
