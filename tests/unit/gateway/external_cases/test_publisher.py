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
