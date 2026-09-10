"""Durable lease unit seams. Native receipt/coverage SQL authenticity needs real PostgreSQL."""

import asyncio
from datetime import timedelta

import pytest
from tests.unit.gateway.external_cases.test_models import scenario
from tests.unit.gateway.external_cases.test_postgres import Engine, Result

from maezo.gateway.external_cases.models import (
    CasePublicationReceipt,
    ExternalCaseError,
    PublicationRequest,
    parse,
)
from maezo.gateway.external_cases.publisher import (
    ExternalCasePublisher,
    PostgresExternalCasePublicationSource,
    bind_receipt,
)
from maezo.portal.engine.profile import canonicalize


def receipt_for(s, request):
    request = parse(PublicationRequest, request)
    ingress = request.ingress_receipt
    return CasePublicationReceipt(
        schema="portal-external-case-publication-receipt.v1",
        kind="case",
        scope=request.scope,
        publication_id=request.publication_id,
        request_digest=__import__("hashlib").sha256(request.canonical()).hexdigest(),
        requester_fingerprint=request.requester_fingerprint,
        source_ref=ingress.source_ref,
        source_revision=ingress.source_revision,
        source_digest=ingress.source_digest,
        source_generation=ingress.source_generation,
        upstream_receipts_digest=ingress.upstream_receipts_digest,
        projection_digest="a" * 64,
        coverage_digest="b" * 64,
        coverage_count="0",
        authority_revision="7",
        designation_digest=s["authority"].designation_digest,
        committed_at=s["ingress"]["committed_at"],
    )


class CaptureConnection:
    def __init__(self, engine):
        self.engine = engine
        self.active = False
        self.is_active = False

    async def begin(self):
        self.active = True
        self.is_active = True
        return self

    def in_transaction(self):
        return self.active

    async def execute(self, sql, params):
        assert self.active
        self.engine.events.append((str(sql), params))
        if "transition_external" in str(sql):
            self.engine.calls.append((str(sql), params, self))
            return Result(self.engine.receipt)
        if "capture_external" in str(sql):
            return Result(self.engine.capture_row)
        if "persist_external" in str(sql):
            self.engine.persisted = params["request"]
        return Result(None)

    async def commit(self):
        self.engine.events.append(("capture_commit", {}))
        self.is_active = False
        self.active = False

    async def rollback(self):
        self.is_active = False
        self.active = False
        self.engine.events.append(("rollback", {}))

    async def close(self):
        self.active = False
        self.engine.events.append(("close", {}))

    async def invalidate(self):
        self.active = False


class PublisherEngine(Engine):
    def __init__(self, s):
        super().__init__(s)
        self.events = []
        self.persisted = None
        self.connection = CaptureConnection(self)
        self.capture_row = self.row | {
            "packet_bytes": canonicalize(s["packet"]),
            "ingress_bytes": self.receipt,
            "canonical_request": None,
            "native_receipt": None,
            "covering_receipt": None,
            "delivery_state": "pending",
            "claim_epoch": 1,
            "claim_expires_at": s["now"] + timedelta(seconds=5),
        }
        self.receipt = True

    async def connect(self):
        return self.connection


def source(s, engine):
    return PostgresExternalCasePublicationSource(
        engine, s["authority"].bundle.scope, s["root"].public_key(), clock=lambda: s["now"]
    )


class Transport:
    requester_fingerprint = "f" * 64

    def __init__(self, s, engine):
        self.s = s
        self.engine = engine
        self.failure = None
        self.recovered = None
        self.sent = []

    async def publish(self, request):
        assert not self.engine.connection.active
        assert self.engine.persisted == request
        self.sent.append(request)
        if self.failure:
            raise self.failure
        return receipt_for(self.s, request).canonical()

    async def receipt(self, request):
        assert not self.engine.connection.active
        return self.recovered


@pytest.mark.asyncio
async def test_persist_commit_release_before_transport_and_await_native_accounting():
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)
    result = await ExternalCasePublisher(source(s, engine), transport).publish(
        "case", "publication-test", "claim-test"
    )
    assert result.publication_id == "publication-test"
    names = [name for name, _ in engine.events]
    persist = next(i for i, name in enumerate(names) if "persist_external_case" in name)
    assert persist < names.index("capture_commit") < names.index("close")
    actions = [p["action"] for _, p, _ in engine.calls if p and "action" in p]
    assert actions == ["live", "committed"]


@pytest.mark.asyncio
async def test_lost_ack_records_uncertainty_and_restart_recovers_exact_persisted_request():
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)
    transport.failure = RuntimeError("synthetic-lost-ack")
    publisher = ExternalCasePublisher(source(s, engine), transport)
    with pytest.raises(ExternalCaseError, match="uncertain"):
        await publisher.publish("case", "publication-test", "claim-test")
    assert any(p and p.get("action") == "uncertain" for _, p, _ in engine.calls)
    original = engine.persisted
    engine.capture_row.update(canonical_request=original, delivery_state="uncertain", claim_epoch=2)
    transport.failure = None
    transport.recovered = receipt_for(s, original).canonical()
    result = await publisher.publish("case", "publication-test", "restarted-claim")
    assert result.request_digest == receipt_for(s, original).request_digest
    assert len(transport.sent) == 1 and engine.persisted == original


@pytest.mark.asyncio
async def test_cross_profile_native_receipt_never_becomes_committed():
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)

    async def wrong(_):
        return canonicalize({"schema": "portal-read-publication-receipt.v1"})

    transport.publish = wrong
    with pytest.raises(ExternalCaseError):
        await ExternalCasePublisher(source(s, engine), transport).publish(
            "case", "publication-test", "claim-test"
        )
    assert not any(p and p.get("action") == "committed" for _, p, _ in engine.calls)


@pytest.mark.asyncio
async def test_unprepared_superseded_event_uses_separate_coverage_transition():
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)
    covering = receipt_for(s, canonicalize(s["request"])).model_copy(
        update={"publication_id": "newer-publication", "source_revision": "2"}
    )
    engine.capture_row["covering_receipt"] = covering.canonical()
    result = await ExternalCasePublisher(source(s, engine), transport).publish(
        "case", "publication-test", "claim-test"
    )
    assert result.publication_id == "newer-publication" and transport.sent == [] and engine.persisted is None
    assert [p["action"] for _, p, _ in engine.calls if p and "action" in p] == ["covered"]


@pytest.mark.asyncio
async def test_capture_cannot_dispatch_without_durable_request():
    s = scenario()
    engine = PublisherEngine(s)
    async with await source(s, engine).capture("case", "publication-test", "claim-test") as lease:
        with pytest.raises(ExternalCaseError):
            await lease.release_capture()
    assert not engine.connection.active
    assert any(name == "rollback" for name, _ in engine.events)


@pytest.mark.asyncio
async def test_stale_claim_cannot_record_success():
    s = scenario()
    engine = PublisherEngine(s)
    engine.receipt = False
    with pytest.raises(ExternalCaseError, match="conflict"):
        await ExternalCasePublisher(source(s, engine), Transport(s, engine)).publish(
            "case", "publication-test", "claim-test"
        )
    assert not any(p and p.get("action") == "committed" for _, p, _ in engine.calls)


@pytest.mark.asyncio
async def test_cancellation_awaits_uncertain_persistence():
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)
    transport.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await ExternalCasePublisher(source(s, engine), transport).publish(
            "case", "publication-test", "claim-test"
        )
    assert any(p and p.get("action") == "uncertain" for _, p, _ in engine.calls)
    assert not engine.connection.active


@pytest.mark.asyncio
async def test_missing_transport_is_explicit_unavailable_without_capture():
    s = scenario()
    engine = PublisherEngine(s)
    with pytest.raises(ExternalCaseError, match="unavailable"):
        await ExternalCasePublisher(source(s, engine), None).publish("case", "publication-test", "claim-test")
    assert engine.events == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("publication_id", "other"),
        ("source_revision", "2"),
        ("source_generation", "2"),
        ("requester_fingerprint", "0" * 64),
        ("request_digest", "0" * 64),
    ],
)
def test_receipt_must_bind_exact_request_and_original_provenance(field, value):
    s = scenario()
    request = parse(PublicationRequest, canonicalize(s["request"]))
    receipt = receipt_for(s, request.canonical()).model_copy(update={field: value})
    with pytest.raises(ExternalCaseError):
        bind_receipt(request, receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_failure", ["expired", "cancelled"])
async def test_failed_entry_awaits_cleanup_before_publisher_refuses(entry_failure):
    s = scenario()
    engine = PublisherEngine(s)
    src = source(s, engine)
    transport = Transport(s, engine)
    original_capture = src.capture
    original_rollback = engine.connection.rollback
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    captured = []

    async def delayed_rollback():
        cleanup_started.set()
        await allow_cleanup.wait()
        await original_rollback()

    async def capture_then_fail(*args):
        lease = await original_capture(*args)
        captured.append(lease)
        if entry_failure == "expired":
            s["now"] += timedelta(seconds=6)
        else:

            def cancelled_clock():
                raise asyncio.CancelledError()

            src.clock = cancelled_clock
        return lease

    src.capture = capture_then_fail
    engine.connection.rollback = delayed_rollback
    task = asyncio.create_task(
        ExternalCasePublisher(src, transport).publish("case", "publication-test", "claim-test")
    )
    # A refusal cannot finish while its cleanup is still owned by the task.
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        assert not task.done() and engine.connection.active
    finally:
        allow_cleanup.set()
        expected = ExternalCaseError if entry_failure == "expired" else asyncio.CancelledError
        with pytest.raises(expected) as result:
            await task
    if entry_failure == "expired":
        assert result.value.code == "conflict"
    assert captured[0].state == "released"
    assert not engine.connection.active and not engine.connection.is_active
    names = [name for name, _ in engine.events]
    assert names.count("rollback") == names.count("close") == 1
    assert not transport.sent and engine.persisted is None
    await captured[0].release()
    assert [name for name, _ in engine.events] == names


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sqlstate,code",
    [
        ("P7E01", "invalid"),
        ("P7E02", "conflict"),
        ("P7E03", "unavailable"),
        ("P7E04", "denied"),
        ("XX000", "uncertain"),
    ],
)
async def test_persist_provider_failure_is_closed_after_real_lease_exit(sqlstate, code):
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)
    execute = engine.connection.execute

    class ProviderError(Exception):
        pass

    problem = ProviderError("SYNTHETIC_PROVIDER_PAYLOAD")
    problem.sqlstate = sqlstate
    problem.add_note("SYNTHETIC_PROVIDER_NOTE")
    problem.__cause__ = RuntimeError("SYNTHETIC_PROVIDER_CAUSE")

    async def fail_persist(sql, params):
        if "persist_external_" in str(sql):
            raise problem
        return await execute(sql, params)

    engine.connection.execute = fail_persist
    with pytest.raises(ExternalCaseError) as result:
        await ExternalCasePublisher(source(s, engine), transport).publish(
            "case", "publication-test", "claim-test"
        )
    error = result.value
    assert error is not problem and error.code == code
    assert error.__cause__ is None and error.__context__ is None
    assert not getattr(error, "__notes__", ())
    assert "SYNTHETIC" not in str(error)
    assert not engine.connection.active and not engine.connection.is_active
    names = [name for name, _ in engine.events]
    assert names.count("rollback") == names.count("close") == 1
    assert "capture_commit" not in names and not transport.sent
    assert engine.persisted is None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidation_fails", [False, True])
async def test_failed_entry_invalidation_is_awaited_and_failure_stays_unavailable(invalidation_fails):
    s = scenario()
    engine = PublisherEngine(s)
    src = source(s, engine)
    lease = await src.capture("case", "publication-test", "claim-test")
    s["now"] += timedelta(seconds=6)
    events = []

    async def failed_rollback():
        events.append("rollback_failed")
        raise RuntimeError("SYNTHETIC_ROLLBACK_PAYLOAD")

    async def invalidate():
        await asyncio.sleep(0)
        if invalidation_fails:
            events.append("invalidation_failed")
            raise RuntimeError("SYNTHETIC_INVALIDATION_PAYLOAD")
        events.append("invalidated")
        engine.connection.active = engine.connection.is_active = False

    engine.connection.rollback = failed_rollback
    engine.connection.invalidate = invalidate
    with pytest.raises(ExternalCaseError) as result:
        async with lease:
            pytest.fail("failed entry must not reach the body")
    error = result.value
    assert error.code == ("unavailable" if invalidation_fails else "conflict")
    assert error.__cause__ is None and error.__context__ is None
    assert not getattr(error, "__notes__", ()) and "SYNTHETIC" not in str(error)
    if invalidation_fails:
        assert events == ["rollback_failed", "invalidation_failed"]
        assert lease.state == "captured"
        assert not any(name == "close" for name, _ in engine.events)
    else:
        assert events == ["rollback_failed", "invalidated"]
        assert lease.state == "released" and not engine.connection.active
        assert sum(name == "close" for name, _ in engine.events) == 1


@pytest.mark.asyncio
async def test_persist_cancellation_rolls_back_without_dispatch():
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)
    execute = engine.connection.execute

    async def cancelled_persist(sql, params):
        if "persist_external_" in str(sql):
            raise asyncio.CancelledError()
        return await execute(sql, params)

    engine.connection.execute = cancelled_persist
    with pytest.raises(asyncio.CancelledError):
        await ExternalCasePublisher(source(s, engine), transport).publish(
            "case", "publication-test", "claim-test"
        )
    assert not transport.sent and engine.persisted is None
    names = [name for name, _ in engine.events]
    assert names.count("rollback") == names.count("close") == 1
    assert not engine.connection.active and not engine.connection.is_active


@pytest.mark.asyncio
async def test_capture_commit_failure_remains_uncertain_without_dispatch():
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)

    async def unknown_commit():
        raise RuntimeError("SYNTHETIC_LOST_COMMIT_ACK")

    engine.connection.commit = unknown_commit
    with pytest.raises(ExternalCaseError, match="uncertain") as result:
        await ExternalCasePublisher(source(s, engine), transport).publish(
            "case", "publication-test", "claim-test"
        )
    assert result.value.code == "uncertain"
    assert result.value.__cause__ is None and result.value.__context__ is None
    assert not transport.sent and engine.persisted is not None
    assert not engine.connection.active and not engine.connection.is_active
    assert not any(params and params.get("action") == "committed" for _, params, _ in engine.calls)


@pytest.mark.asyncio
async def test_persist_cleanup_failure_refuses_without_returning_suspect_connection():
    s = scenario()
    engine = PublisherEngine(s)
    transport = Transport(s, engine)
    execute = engine.connection.execute

    class ProviderError(Exception):
        sqlstate = "P7E02"

    async def failed_cleanup():
        raise RuntimeError("SYNTHETIC_CLEANUP_PAYLOAD")

    async def fail_persist(sql, params):
        if "persist_external_" in str(sql):
            problem = ProviderError("SYNTHETIC_PROVIDER_PAYLOAD")
            problem.add_note("SYNTHETIC_PROVIDER_NOTE")
            raise problem
        return await execute(sql, params)

    engine.connection.execute = fail_persist
    engine.connection.rollback = failed_cleanup
    engine.connection.invalidate = failed_cleanup
    with pytest.raises(ExternalCaseError) as result:
        await ExternalCasePublisher(source(s, engine), transport).publish(
            "case", "publication-test", "claim-test"
        )
    error = result.value
    assert error.code == "unavailable"
    assert error.__cause__ is None and error.__context__ is None
    assert not getattr(error, "__notes__", ()) and "SYNTHETIC" not in str(error)
    assert not any(name == "close" for name, _ in engine.events)
    assert not transport.sent and engine.persisted is None
