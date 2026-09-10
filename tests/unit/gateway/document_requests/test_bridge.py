"""Synthetic source/SQL controls. These do not qualify PostgreSQL, PHI placement or native runtime."""

import base64
import copy
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from maezo.gateway.communications.content import PhiContentKeys
from maezo.gateway.communications.models import alive
from maezo.gateway.document_requests import content as content_module
from maezo.gateway.document_requests import producer as producer_module
from maezo.gateway.document_requests.completion import CompletionOutcome
from maezo.gateway.document_requests.content import AuthenticatedBodyReceipt, PhiRequestContent
from maezo.gateway.document_requests.models import (
    NOTICE,
    BodyRefused,
    NewDelivery,
    PreserveBodyBinding,
    RequestBodyCommand,
    RequestIdentity,
    SystemAccess,
    SystemGrant,
    SystemProducer,
    SystemRecipient,
    assessment,
    child_id,
    packed,
    parsed,
    recipient_digest,
    selector,
)
from maezo.gateway.document_requests.producer import DocumentRequestProducer, ProducerJournal
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.human.auth_profile import Definition, DocumentPolicy, PublicationReceipt, Scope
from maezo.gateway.human.read_profile import ArtifactPin, SourceProvenance, digest
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore

NOW = datetime.now(UTC)
REF = "a" * 32
OTHER = "b" * 32
HASH = "a" * 64


def producer(ref=REF):
    fields = dict(
        producer_ref=ref,
        issuer="issuer" * 4,
        subject="subject" * 4,
        tenant="tenant" * 4,
        environment="test" * 4,
    )
    return SystemProducer(
        **fields,
        identity_revision="1",
        identity_receipt_ref=REF,
        identity_digest=digest(dict(schema="maezo.communication.system-sender.v1", **fields)),
    )


def request():
    p = producer()
    return RequestIdentity(
        scope=Scope(
            tenant=p.tenant,
            environment=p.environment,
            engine_name="engine",
            database_incarnation="db",
            installation_ref="installation",
            installation_revision=1,
        ),
        definition=Definition(
            process_key="SP-OP-AUTH-001",
            definition_id="definition",
            definition_digest=HASH,
            deployment_id="deployment",
            input_profile="portal-auth-intake.v1",
            profile_digest=HASH,
        ),
        case_ref=REF,
        request_ref=OTHER,
        generation="1",
        process_instance_id=REF,
        creator_execution_id=REF,
        producer_external_task_id=REF,
        created_at=NOW,
    )


def source(until):
    return SourceProvenance(
        publisher_ref="publisher",
        source_ref="source",
        source_revision=1,
        source_digest=HASH,
        receipt_ref=REF,
        observed_at=NOW - timedelta(seconds=1),
        valid_until=until,
    )


def recipient(ref=HASH, until=NOW + timedelta(hours=1)):
    return SystemRecipient(
        principal_ref=REF,
        identity_digest=ref,
        audience="beneficiary",
        source_revision="1",
        policy_digest=HASH,
        valid_until=until,
    )


def body():
    r, p = request(), producer()
    return RequestBodyCommand(
        schema="auth-document-request-body.v1",
        command_id=child_id("body", r, p.identity_digest, REF, HASH),
        message_command_id=child_id("message", r, p.identity_digest, REF, HASH),
        outer_command_id=REF,
        sender_identity_digest=p.identity_digest,
        request=r,
        request_revision="0",
        assessment_version_digest=HASH,
        recipient_set_digest=recipient_digest((recipient(),)),
        recipient_identity_digest=HASH,
        template=ArtifactPin(artifact_ref="template", digest=HASH),
        body=NOTICE,
    )


def grant(command, operation="preserve_request_body", until=NOW + timedelta(minutes=1), reader=None):
    p = reader or producer()
    access = SystemAccess(
        scope=command.request.communication_scope(),
        producer=p,
        operation=operation,
        request=command.request,
        request_revision="0",
        policy_ref=REF,
        policy_digest=HASH,
        policy_publication_ref=REF,
        policy_publication_digest=HASH,
        assessment_version_digest=HASH,
        context_query_digest=HASH,
        command_id=command.command_id,
        request_digest=digest(command),
    )
    binding = (
        PreserveBodyBinding(
            recipient_identity_digest=HASH,
            outer_command_id=REF,
            body_command_id=command.command_id,
            message_command_id=command.message_command_id,
            body_ref=None,
            body_request_digest=digest(command),
        )
        if operation == "preserve_request_body"
        else selector(command)
    )
    return SystemGrant(
        access=access,
        authority_receipt_ref=REF,
        authority_digest=HASH,
        source=source(until),
        valid_until=until,
        policy_valid_until=until,
        template=command.template,
        recipients=(recipient(),),
        body_bindings=(binding,),
    )


def test_child_keys_and_closed_body_never_rebind_recipient_or_sender():
    b = body()
    assert b.command_id != b.message_command_id
    assert b.command_id != child_id("body", b.request, b.sender_identity_digest, REF, "b" * 64)
    assert parsed(RequestBodyCommand, packed(b)) == b
    assert "Existe" not in repr(b)
    for changes in ({"body": NOTICE + "\n"}, {"recipient_identity_digest": "b" * 64}, {"actor": REF}):
        with pytest.raises(ValidationError):
            RequestBodyCommand.model_validate({**b.model_dump(), **changes})


def test_assessment_refresh_keeps_definition_and_distinct_publication_identity():
    r = request()
    until = NOW + timedelta(hours=1)
    p = DocumentPolicy(
        assessment_ref=REF,
        resource_kind="case",
        resource_ref=r.case_ref,
        request_ref=r.request_ref,
        request_revision=0,
        policy=ArtifactPin(artifact_ref="policy", digest=HASH),
        policy_revision=0,
        recipient_principal_refs=(REF,),
        required_codes=("required",),
        missing_codes=("required",),
        submitted_response_digest=None,
        effective_document_refs=(),
        document_set_digest=digest([]),
        complete=False,
        source=source(until),
        valid_until=until,
    )
    receipt = PublicationReceipt(
        schema="human-auth-input-receipt.v1",
        scope=r.scope,
        publication_id=REF,
        request_digest=HASH,
        kind="document_policy",
        resource_ref=REF,
        previous_generation=0,
        head_generation=1,
        state="active",
        payload_digest=digest(p),
        committed_at=NOW,
    )
    first = assessment(r, p, receipt)
    fresh = p.model_copy(update={"source": p.source.model_copy(update={"source_revision": 2})})
    second = assessment(
        r,
        fresh,
        receipt.model_copy(
            update={
                "publication_id": OTHER,
                "head_generation": 2,
                "previous_generation": 1,
                "payload_digest": digest(fresh),
            }
        ),
    )
    assert first.stable_definition_digest == second.stable_definition_digest
    assert first.request_revision == second.request_revision
    assert digest(first) != digest(second)
    with pytest.raises(ExternalCaseError):
        assessment(r, fresh, receipt)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class PhiDatabase:
    def __init__(self):
        self.contents = []
        self.provenance = []
        self.unknown = False
        self.after = None

    async def execute(self, sql, p):
        q = str(sql)
        if "pg_advisory" in q:
            return Rows([])
        if q.startswith("INSERT INTO portal_communication.content"):
            self.contents.append(dict(p, authored_at=NOW))
            return Rows([])
        if q.startswith("INSERT INTO portal_document_request.producer_journal"):
            self.provenance.append(dict(p))
            return Rows([])
        raise AssertionError(q)

    async def one(self, c, sql, p):
        matching = [
            r
            for r in self.contents
            if r["sender_identity_digest"] == p.get("sender", p.get("sender_identity_digest"))
            and r["command_id"] == p.get("command", p.get("command_id"))
        ]
        if sql.startswith("SELECT body_ref"):
            return matching[0] if matching else None
        if sql.startswith("SELECT c.*"):
            if not matching:
                return None
            original = matching[0]
            journal = next((r for r in self.provenance if r["command_id"] == original["command_id"]), None)
            if journal is None:
                return None
            return {
                **original,
                **{
                    k: journal[k]
                    for k in (
                        "provenance_key",
                        "provenance_nonce",
                        "provenance_ciphertext",
                        "provenance_digest",
                    )
                },
            }
        raise AssertionError(sql)

    @asynccontextmanager
    async def acquire(self, grant, deadline):
        alive(deadline, grant.ceiling())
        snapshot = copy.deepcopy((self.contents, self.provenance))
        try:
            yield self
        except BaseException:
            self.contents, self.provenance = snapshot
            raise
        if self.after:
            self.after()
        if self.unknown:
            raise ExternalCaseError("uncertain")
        alive(deadline, grant.ceiling())


def phi(monkeypatch):
    db = PhiDatabase()
    b = body()
    scope = b.request.communication_scope()
    admission = SimpleNamespace(engine=db, scope=scope, acquire=db.acquire)
    keys = PhiContentKeys(
        scope=scope, active_key_id=REF, keys={REF: bytes(range(32))}, valid_until=NOW + timedelta(hours=1)
    )
    protected = PostgresAuthDispatchStore(db, tenant=scope.tenant, key_id=OTHER, key=b"p" * 32)
    service = PhiRequestContent(admission, keys, protected)
    monkeypatch.setattr(content_module, "one", db.one)
    return db, b, service


@pytest.mark.asyncio
async def test_body_lost_ack_recovers_original_before_any_inbox(monkeypatch):
    db, b, service = phi(monkeypatch)
    db.unknown = True
    with pytest.raises(ExternalCaseError):
        await service.preserve_proven(grant(b), b, NOW + timedelta(minutes=1))
    assert len(db.contents) == len(db.provenance) == 1
    db.unknown = False
    recovered = await service.recover_proven(
        grant(b, "read_request_body_receipt", reader=producer(OTHER)), selector(b), NOW + timedelta(minutes=1)
    )
    assert recovered.receipt.body_ref == db.contents[0]["body_ref"]
    assert recovered.receipt.sender_identity_digest == b.sender_identity_digest
    assert recovered.receipt.body_authority_receipt_ref == REF
    assert len(db.contents) == 1
    with pytest.raises(ExternalCaseError):
        AuthenticatedBodyReceipt(recovered.receipt, _mint=object())


@pytest.mark.asyncio
async def test_missing_provenance_is_refusal_not_no_effect(monkeypatch):
    db, b, service = phi(monkeypatch)
    await service.preserve(grant(b), b, NOW + timedelta(minutes=1))
    db.provenance.clear()
    result = await service.read_receipt(
        grant(b, "read_request_body_receipt"), selector(b), NOW + timedelta(minutes=1)
    )
    assert isinstance(result, BodyRefused)
    with pytest.raises(ExternalCaseError):
        await service.preserve(grant(b), b, NOW + timedelta(minutes=1))
    assert len(db.contents) == 1


@pytest.mark.asyncio
async def test_body_custody_tamper_never_mints_proof(monkeypatch):
    db, b, service = phi(monkeypatch)
    await service.preserve(grant(b), b, NOW + timedelta(minutes=1))
    db.contents[0]["request_digest"] = "f" * 64
    with pytest.raises(ExternalCaseError):
        await service.recover_proven(
            grant(b, "read_request_body_receipt"), selector(b), NOW + timedelta(minutes=1)
        )


class JournalDatabase:
    def __init__(self):
        self.rows = {}
        self.unknown = False

    @asynccontextmanager
    async def transaction(self, engine, seconds):
        snapshot = copy.deepcopy(self.rows)
        try:
            yield self
        except BaseException:
            self.rows = snapshot
            raise
        if self.unknown:
            raise ExternalCaseError("uncertain")

    async def execute(self, sql, p):
        q = str(sql)
        if "pg_advisory" in q:
            return Rows([])
        key = tuple(p[k] for k in ("tenant", "environment", "sender", "command", "kind"))
        if q.startswith("INSERT"):
            self.rows[key] = dict(p, key_id=p["key"], payload_digest=p["digest"], state="sealed")
            return Rows([])
        if q.startswith("UPDATE"):
            row = self.rows.get(key)
            if row is None:
                return Rows([])
            if "state='sealed'" in q:
                if row["state"] != "sealed":
                    return Rows([])
                row["state"] = "possibly_sent"
            else:
                row["state"] = "acknowledged"
            return Rows([row])
        if q.startswith("SELECT"):
            return Rows([self.rows[key]]) if key in self.rows else Rows([])
        raise AssertionError(q)


@pytest.mark.asyncio
async def test_durable_send_claim_cannot_be_repeated_after_unknown_commit(monkeypatch):
    db = JournalDatabase()
    p = producer()
    protected = PostgresAuthDispatchStore(db, tenant=p.tenant, key_id=REF, key=b"j" * 32)
    journal = ProducerJournal(protected, p, lambda: None)
    monkeypatch.setattr(producer_module, "transaction", db.transaction)
    await journal.seal(REF, "completion", {"exact_body": "original"})
    db.unknown = True
    with pytest.raises(ExternalCaseError):
        await journal.claim_send(REF, "completion")
    db.unknown = False
    restarted = ProducerJournal(protected, p, lambda: None)
    assert not await restarted.claim_send(REF, "completion")
    assert (await restarted.read(REF, "completion")).state == "possibly_sent"
    with pytest.raises(ExternalCaseError):
        await restarted.seal(REF, "completion", {"exact_body": "replacement"})


@pytest.mark.asyncio
async def test_possible_completion_not_observed_never_resends_or_reacquires(monkeypatch):
    db = JournalDatabase()
    p = producer()
    protected = PostgresAuthDispatchStore(db, tenant=p.tenant, key_id=REF, key=b"j" * 32)
    journal = ProducerJournal(protected, p, lambda: None)
    monkeypatch.setattr(producer_module, "transaction", db.transaction)
    await journal.seal(
        REF,
        "completion",
        {
            "body": base64.b64encode(b"original body").decode(),
            "binding": base64.b64encode(b"original binding").decode(),
        },
    )
    await journal.claim_send(REF, "completion")
    calls = []

    class Completion:
        async def exchange(self, prepared, *, recovery, before_send):
            before_send()
            calls.append((prepared, recovery))
            return CompletionOutcome("not_observed", None)

    worker = object.__new__(DocumentRequestProducer)
    worker.journal = journal
    worker.completion = Completion()
    result = await worker._complete(REF, "synthetic-inbox-receipt", grant(body()), None)
    assert result.completion.status == "not_observed" and calls[0][1] is True
    assert calls[0][0].body == b"original body"
    assert (await journal.read(REF, "completion")).state == "possibly_sent"


@pytest.mark.asyncio
async def test_current_body_read_can_outlive_original_preserve_grant(monkeypatch):
    from maezo.gateway.communications import models as communication_models

    db, b, service = phi(monkeypatch)
    clock = [NOW]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(communication_models, "datetime", Clock)
    origin = grant(b, until=NOW + timedelta(seconds=1))
    receipt = await service.preserve(origin, b, origin.ceiling())
    clock[0] = NOW + timedelta(seconds=2)
    with pytest.raises(ExternalCaseError):
        origin.ceiling()
    proof = await service.recover_proven(
        grant(b, "read_request_body_receipt", reader=producer(OTHER)), selector(b), NOW + timedelta(minutes=1)
    )
    assert proof.receipt == receipt
    assert len(db.contents) == 1


@pytest.mark.asyncio
async def test_expiry_during_transaction_cleanup_never_releases_body_proof(monkeypatch):
    from maezo.gateway.communications import models as communication_models

    db, b, service = phi(monkeypatch)
    clock = [NOW]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(communication_models, "datetime", Clock)
    origin = grant(b, until=NOW + timedelta(seconds=1))
    db.after = lambda: clock.__setitem__(0, NOW + timedelta(seconds=2))
    with pytest.raises(ExternalCaseError):
        await service.preserve_proven(origin, b, origin.ceiling())
    assert len(db.contents) == len(db.provenance) == 1


class MetadataDatabase:
    def __init__(self, contents):
        self.contents = contents
        self.tables = {
            k: {}
            for k in (
                "request",
                "definition",
                "version",
                "message",
                "recipient",
                "inbox",
                "history",
                "delivery",
                "command",
            )
        }
        self.fail_recipient = None

    @asynccontextmanager
    async def acquire(self, grant, deadline):
        alive(deadline, grant.ceiling())
        snapshot = copy.deepcopy(self.tables)
        try:
            yield self
        except BaseException:
            self.tables = snapshot
            raise
        alive(deadline, grant.ceiling())

    async def execute(self, sql, p):
        q = str(sql)
        if "pg_advisory" in q:
            return Rows([])
        for sql_table, key in (
            ("portal_document_request.request_definition_revision", "definition"),
            ("portal_document_request.assessment_version", "version"),
            ("portal_document_request.request", "request"),
            ("portal_document_request.delivery", "delivery"),
            ("portal_document_request.command", "command"),
            ("portal_communication.message", "message"),
            ("portal_communication.intended_recipient", "recipient"),
            ("portal_communication.inbox", "inbox"),
            ("portal_communication.history", "history"),
        ):
            if q.startswith("INSERT INTO " + sql_table + " "):
                if key == "inbox" and p["recipient"] == self.fail_recipient:
                    raise ExternalCaseError("conflict")
                identity = (
                    p.get("recipient")
                    if key == "delivery"
                    else p.get("communication")
                    if key in {"message", "recipient", "inbox", "history"}
                    else p.get("command")
                    if key == "command"
                    else p.get("version")
                    if key == "version"
                    else p["request"]
                )
                self.tables[key].setdefault(identity, dict(p))
                return Rows([])
        raise AssertionError(q)

    async def one(self, c, sql, p):
        if sql.startswith("SELECT identity_digest"):
            r = self.tables["request"].get(p["request"])
            return (
                None
                if r is None
                else dict(identity_digest=r["request_digest"], identity_payload=r["identity"])
            )
        if sql.startswith("SELECT stable_definition_digest"):
            r = self.tables["definition"].get(p["request"])
            return None if r is None else dict(stable_definition_digest=r["stable"])
        if sql.startswith("SELECT payload FROM"):
            r = self.tables["version"].get(p["version"])
            return None if r is None else dict(payload=r["payload"])
        if sql.startswith("SELECT request_digest,payload,receipt"):
            r = self.tables["command"].get(p["command"])
            return (
                None
                if r is None
                else dict(request_digest=r["digest"], payload=r["payload"], receipt=r["receipt"])
            )
        if sql.startswith("SELECT authored_at"):
            return next((r for r in self.contents if r["body_ref"] == p["body"]), None)
        if sql.startswith("SELECT inbox_available_at"):
            return dict(inbox_available_at=NOW)
        if sql.startswith("SELECT transaction_timestamp"):
            return dict(at=NOW)
        if sql.startswith("SELECT d.record"):
            r = self.tables["delivery"].get(p["recipient"])
            if r is None:
                return None
            m = self.tables["message"][r["communication"]]
            assert all(r["communication"] in self.tables[k] for k in ("recipient", "inbox", "history"))
            body = next(b for b in self.contents if b["body_ref"] == r["body"])
            return dict(
                record=r["record"],
                sender_identity_digest=m["sender"],
                sender_kind="system",
                command_id=m["message"],
                communication_ref=m["communication"],
                body_ref=m["body"],
                request_digest=m["message_digest"],
                recipients_digest=m["singleton"],
                recipient_set_ref=m["message"],
                authority_receipt_ref=m["authority_ref"],
                authority_digest=m["authority_digest"],
                inbox_available_at=NOW,
                audience=m["audience"],
                source_revision=m["source_revision"],
                policy_digest=m["policy_digest"],
                body_authority_ref=body["authority_receipt_ref"],
                body_authority_digest=body["authority_digest"],
            )
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_all_recipient_inbox_history_rollback_and_same_command_receipt(monkeypatch):
    from maezo.gateway.document_requests import postgres as store_module
    from maezo.gateway.document_requests.models import PolicyAssessmentVersion, RequestDeliveryCommand

    db, b, service = phi(monkeypatch)
    version = PolicyAssessmentVersion(
        schema="auth-document-request-assessment-version.v1",
        request_identity_digest=digest(b.request),
        request_revision="0",
        stable_definition_digest=HASH,
        policy_ref=REF,
        policy_digest=HASH,
        policy_publication_ref=REF,
        policy_publication_digest=HASH,
        publication_request_digest=HASH,
        publication_head_generation="1",
        source_receipt_ref=REF,
        source_provenance_digest=HASH,
    )
    recipients = (recipient(), recipient("b" * 64).model_copy(update={"principal_ref": OTHER}))
    proofs = []
    entries = []
    for r in recipients:
        current = b.model_copy(
            update={
                "command_id": child_id("body", b.request, b.sender_identity_digest, REF, r.identity_digest),
                "message_command_id": child_id(
                    "message", b.request, b.sender_identity_digest, REF, r.identity_digest
                ),
                "recipient_identity_digest": r.identity_digest,
                "recipient_set_digest": recipient_digest(recipients),
                "assessment_version_digest": digest(version),
            }
        )
        binding = PreserveBodyBinding(
            recipient_identity_digest=r.identity_digest,
            outer_command_id=REF,
            body_command_id=current.command_id,
            message_command_id=current.message_command_id,
            body_ref=None,
            body_request_digest=digest(current),
        )
        g = grant(current).model_copy(
            update={
                "recipients": recipients,
                "body_bindings": (binding,),
                "access": grant(current).access.model_copy(
                    update={"assessment_version_digest": digest(version)}
                ),
            }
        )
        proof = await service.preserve_proven(g, current, NOW + timedelta(minutes=1))
        proofs.append(proof)
        entries.append(
            NewDelivery(
                mode="new",
                recipient_identity_digest=r.identity_digest,
                body_command_id=current.command_id,
                message_command_id=current.message_command_id,
                body_ref=proof.receipt.body_ref,
                body_request_digest=digest(current),
            )
        )
    command = RequestDeliveryCommand(
        schema="auth-document-request-delivery.v1",
        command_id=REF,
        sender_identity_digest=b.sender_identity_digest,
        request=b.request,
        request_revision="0",
        policy_ref=REF,
        policy_digest=HASH,
        policy_publication_ref=REF,
        policy_publication_digest=HASH,
        assessment_version_digest=digest(version),
        context_query_digest=HASH,
        recipient_set_digest=recipient_digest(recipients),
        deliveries=tuple(entries),
    )
    access = grant(b).access.model_copy(
        update={
            "operation": "publish_request_inbox",
            "command_id": REF,
            "request_digest": digest(command),
            "assessment_version_digest": digest(version),
        }
    )
    admitted = SystemGrant(
        access=access,
        authority_receipt_ref=REF,
        authority_digest=HASH,
        source=source(NOW + timedelta(minutes=1)),
        valid_until=NOW + timedelta(minutes=1),
        policy_valid_until=NOW + timedelta(minutes=1),
        template=b.template,
        recipients=recipients,
        body_bindings=tuple(entries),
    )
    meta = MetadataDatabase(db.contents)
    meta.fail_recipient = recipients[1].identity_digest
    store = store_module.RequestInboxStore(SimpleNamespace(scope=access.scope, acquire=meta.acquire))
    monkeypatch.setattr(store_module, "one", meta.one)
    with pytest.raises(ExternalCaseError):
        await store.publish(admitted, command, version, tuple(proofs), admitted.ceiling())
    assert all(not rows for rows in meta.tables.values())
    assert len(db.contents) == 2
    meta.fail_recipient = None
    receipt = await store.publish(admitted, command, version, tuple(proofs), admitted.ceiling())
    duplicate = await store.publish(admitted, command, version, tuple(proofs), admitted.ceiling())
    assert receipt == duplicate
    assert all(len(meta.tables[k]) == 2 for k in ("message", "recipient", "inbox", "history", "delivery"))
    assert len({r.message_command_id for r in receipt.deliveries}) == 2
    assert all(r.inbox_available_at == NOW for r in receipt.deliveries)


def test_current_identity_revision_does_not_rewrite_historical_sender():
    worker = object.__new__(DocumentRequestProducer)
    old = producer()
    worker.producer = old.model_copy(update={"identity_revision": "2", "identity_receipt_ref": OTHER})
    worker._original_producer(old.model_dump())
    with pytest.raises(ExternalCaseError):
        worker._original_producer(producer(OTHER).model_dump())
