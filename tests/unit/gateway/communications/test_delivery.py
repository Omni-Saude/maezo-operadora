"""Synthetic authority and SQL transaction seam; no live PostgreSQL/PHI qualification."""

import copy
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from tests.unit.gateway.intake.test_postgres import inputs

from maezo.gateway.communications.admission import PostgresCommunicationAdmission, Publication, packed
from maezo.gateway.communications.content import (
    PhiCommunicationService,
    PhiContentKeys,
    PostgresPhiCommunicationContent,
)
from maezo.gateway.communications.models import (
    HISTORY_FIELDS,
    MESSAGE_FIELDS,
    CommunicationAuthority,
    CommunicationGrant,
    CommunicationScope,
    IntendedRecipient,
    identity,
)
from maezo.gateway.communications.postgres import PostgresCommunicationStore
from maezo.gateway.communications.service import PAGE_FIELDS, CommunicationService
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.human.read_profile import digest
from maezo.portal.api.auth import digest as secret_digest
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.api.session import ResolvedHumanSession
from maezo.portal.contracts.communication_content import CommunicationContentSubmission
from maezo.portal.contracts.communications import CommunicationSubmission
from maezo.portal.contracts.models import SubjectBinding

REF = "a" * 32
OTHER = "b" * 32
THIRD = "c" * 32
SECRET = "s" * 43


class Database:
    def __init__(self, clock):
        self.clock = clock
        self.contents = []
        self.messages = []
        self.recipients = []
        self.inbox = []
        self.history = []
        self.cursors = []
        self.calls = []
        self.fail_inbox = False
        self.unknown_commit = False
        self.after = None
        self.authorities = {}
        self.resolver = None

    def in_transaction(self):
        return True

    @asynccontextmanager
    async def transaction(self, engine, seconds):
        snapshot = copy.deepcopy(
            (self.contents, self.messages, self.recipients, self.inbox, self.history, self.cursors)
        )
        try:
            yield self
        except BaseException:
            self.contents, self.messages, self.recipients, self.inbox, self.history, self.cursors = snapshot
            raise
        if self.after:
            self.after()
        if self.unknown_commit:
            raise ExternalCaseError("uncertain")

    async def execute(self, sql, p):
        q = " ".join(str(sql).split())
        self.calls.append((q, dict(p)))
        rows = []

        def matches(row, *names):
            return all(row.get(name) == p[name] for name in names)

        if q.startswith("SELECT revision,publication_ref,payload FROM portal_communication.authority_head"):
            rows = [self.authorities[p["access"]]] if p["access"] in self.authorities else []
        elif q.startswith("SELECT * FROM portal_communication.lock_session"):
            resolved = self.resolver.records()
            session, member = resolved.record, resolved.membership
            rows = [
                dict(
                    session_tenant=member.tenant,
                    membership_tenant=member.tenant,
                    secret_hash=session.secret_hash,
                    expires_at=session.expires_at,
                    issuer=member.issuer,
                    subject=member.subject,
                    principal_ref=member.principal_ref,
                    session_payload=session.model_dump_json(),
                    membership_payload=member.model_dump_json(),
                )
            ]
        elif "pg_advisory" in q:
            pass
        elif q.startswith("INSERT INTO portal_communication.content "):
            self.contents.append(dict(p, authored_at=self.clock[0]))
        elif q.startswith("SELECT body_ref,case_ref,request_digest FROM portal_communication.content"):
            rows = [
                r
                for r in self.contents
                if matches(r, "tenant", "environment", "sender_identity_digest", "command_id")
            ]
        elif q.startswith("SELECT authored_at FROM portal_communication.content_metadata"):
            rows = [
                r
                for r in self.contents
                if matches(r, "tenant", "environment")
                and r["case_ref"] == p["case"]
                and r["sender_identity_digest"] == p["identity"]
                and r["body_ref"] == p["body"]
            ]
        elif q.startswith("SELECT communication_ref,case_ref,request_digest,recipients_digest"):
            rows = [
                dict(r, case_ref=r["case"], communication_ref=r["communication"])
                for r in self.messages
                if matches(r, "tenant", "environment", "identity", "command")
            ]
        elif q.startswith("INSERT INTO portal_communication.message "):
            if any(r["body"] == p["body"] and matches(r, "tenant", "environment") for r in self.messages):
                raise ExternalCaseError("conflict")
            self.messages.append(dict(p, sequence=len(self.messages) + 1, inbox_available_at=self.clock[0]))
        elif q.startswith("INSERT INTO portal_communication.intended_recipient "):
            self.recipients.append(dict(p))
        elif q.startswith("INSERT INTO portal_communication.inbox "):
            if self.fail_inbox:
                raise ExternalCaseError("unavailable")
            self.inbox.append(dict(p, available_at=self.clock[0]))
        elif q.startswith("INSERT INTO portal_communication.history "):
            self.history.append(
                dict(
                    p,
                    sequence=len(self.history) + 1,
                    occurred_at=self.clock[0],
                    kind="communication_available" if ":communication" in q else "command_receipt_indexed",
                )
            )
        elif q.startswith("SELECT m.communication_ref"):
            rows = [
                dict(
                    communication_ref=r["communication"],
                    sequence=r["sequence"],
                    sender_kind=r["sender_kind"],
                    authored_at=r["authored_at"],
                    inbox_available_at=r["inbox_available_at"],
                    body_ref=r["body"],
                )
                for r in self.messages
                if matches(r, "tenant", "environment", "case")
                and r["sequence"] > p["after"]
                and any(
                    i["communication"] == r["communication"]
                    and i["recipient"] == p["identity"]
                    and i["audience"] == p["audience"]
                    for i in self.inbox
                )
            ]
            rows = rows[: p["fetch_limit"]]
        elif q.startswith("SELECT h.event_ref"):
            rows = [
                dict(
                    event_ref=r["event"],
                    sequence=r["sequence"],
                    occurred_at=r["occurred_at"],
                    kind=r["kind"],
                    communication_ref=r.get("communication"),
                    command_ref=r.get("command"),
                    receipt_ref=r.get("receipt"),
                )
                for r in self.history
                if matches(r, "tenant", "environment", "case")
                and r["sequence"] > p["after"]
                and (
                    r["kind"] == "command_receipt_indexed"
                    or any(
                        i["communication"] == r["communication"]
                        and i["recipient"] == p["identity"]
                        and i["audience"] == p["audience"]
                        for i in self.inbox
                    )
                )
            ]
            rows = rows[: p["fetch_limit"]]
        elif q.startswith("INSERT INTO portal_communication.cursor "):
            self.cursors.append(
                dict(
                    p,
                    case_ref=p["case"],
                    identity_digest=p["identity"],
                    membership_revision=p["membership"],
                    after_sequence=p["after"],
                )
            )
        elif q.startswith("SELECT * FROM portal_communication.cursor"):
            rows = [r for r in self.cursors if matches(r, "tenant", "environment", "cursor")]
        elif q.startswith("SELECT c.*"):
            messages = [
                m
                for m in self.messages
                if matches(m, "tenant", "environment", "communication")
                and m["case"] == p["case_ref"]
                and any(
                    i["communication"] == m["communication"]
                    and i["recipient"] == p["sender_identity_digest"]
                    and i["audience"] == p["audience"]
                    for i in self.inbox
                )
            ]
            rows = [
                r
                for r in self.contents
                if matches(r, "tenant", "environment", "case_ref")
                and any(m["body"] == r["body_ref"] for m in messages)
            ]
        elif q.startswith("SELECT event_ref,receipt_digest FROM portal_communication.history"):
            rows = [
                dict(r, event_ref=r["event"], receipt_digest=r["digest"])
                for r in self.history
                if matches(r, "tenant", "environment", "case", "command", "receipt")
            ]
        else:
            raise AssertionError(q)
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(one_or_none=lambda: rows[0] if rows else None, all=lambda: rows)
        )


class Resolver:
    def __init__(self, clock):
        self.clock = clock
        self.principal = inputs()[0].principal.model_copy(
            update={"subject_bindings": (SubjectBinding(kind="provider", resource_ref=REF),)}
        )
        self.settings = SimpleNamespace(
            tenant=self.principal.tenant,
            public_origin="https://portal.example.test",
            issuer=self.principal.issuer,
        )
        self.session_until = clock[0] + timedelta(minutes=2)
        self.member_until = self.session_until
        self.audience = "provider"
        self.after = None

    def records(self):
        p = self.principal
        return ResolvedHumanSession(
            p,
            SessionRecord(
                secret_hash=secret_digest(SECRET),
                session_ref=p.session_ref,
                csrf_token="csrf",
                issuer=p.issuer,
                subject=p.subject,
                principal_ref=p.principal_ref,
                membership_revision=p.membership_revision,
                authenticated_at=p.authenticated_at,
                expires_at=self.session_until,
            ),
            MembershipRecord(
                tenant=p.tenant,
                issuer=p.issuer,
                subject=p.subject,
                principal_ref=p.principal_ref,
                revision=p.membership_revision,
                audience=self.audience,
                memberships=p.memberships,
                subject_bindings=p.subject_bindings,
                reviewed_until=self.member_until,
            ),
        )

    async def resolve(self, secret):
        if self.after:
            self.after()
        return self.records()


class Authority(CommunicationAuthority):
    def __init__(self, resolver, scope, clock):
        self.resolver = resolver
        self.scope = scope
        self.clock = clock
        self.until = clock[0] + timedelta(minutes=2)
        self.policy_until = self.until
        self.targets = [identity(resolver.principal)]
        self.denied = set()
        self.withhold = set()
        self.after = None

    async def authorize(self, access):
        if self.after:
            self.after()
        if access.operation in self.denied or access.resource_ref in self.denied:
            raise ExternalCaseError("denied")
        fields = (
            {
                "create_content": {"body"},
                "read_content": {"body", "communication_ref"},
                "read_message": MESSAGE_FIELDS,
                "read_history": HISTORY_FIELDS,
                "list_messages": PAGE_FIELDS,
                "list_history": PAGE_FIELDS | {"history_scope"},
            }.get(access.operation, set())
        ) - self.withhold
        recipients = (
            tuple(
                IntendedRecipient(
                    identity_digest=r,
                    audience="provider",
                    source_revision="1",
                    policy_digest="a" * 64,
                    valid_until=self.until,
                )
                for r in self.targets
            )
            if access.operation == "publish"
            else ()
        )
        grant = CommunicationGrant(
            access=access,
            authority_receipt_ref=REF,
            authority_digest="a" * 64,
            policy_valid_until=self.policy_until,
            valid_until=self.until,
            permitted_fields=tuple(sorted(fields)),
            intended_recipients=recipients,
        )
        publication = Publication(
            publication_ref=REF,
            access=access,
            expected_revision="0",
            source_receipt_ref=REF,
            source_digest="a" * 64,
            valid_until=self.until,
            grant=grant,
        )
        self.db.authorities[digest(access)] = dict(
            revision="1", publication_ref=REF, payload=packed(publication)
        )
        return grant


@pytest.fixture
def setup(monkeypatch):
    clock = [datetime.now(UTC)]
    monkeypatch.setattr("maezo.gateway.communications.models.now", lambda: clock[0])
    db = Database(clock)
    monkeypatch.setattr("maezo.gateway.communications.postgres.transaction", db.transaction)
    monkeypatch.setattr("maezo.gateway.communications.content.transaction", db.transaction)
    monkeypatch.setattr("maezo.gateway.communications.admission.transaction", db.transaction)
    resolver = Resolver(clock)
    scope = CommunicationScope(tenant=resolver.principal.tenant, environment="synthetic")
    authority = Authority(resolver, scope, clock)
    authority.db, db.resolver = db, resolver
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    resolver.store = PostgresIdentityStore(scope.tenant, engine)
    admission = PostgresCommunicationAdmission(resolver.store, scope=scope, issuer=resolver.settings.issuer)
    keys = PhiContentKeys(
        scope=scope,
        active_key_id="synthetic",
        keys={"synthetic": b"a" * 32},
        valid_until=clock[0] + timedelta(minutes=2),
    )
    phi = PhiCommunicationService(
        resolver,
        authority,
        PostgresPhiCommunicationContent(engine, scope=scope, keys=keys, admission=admission),
    )
    service = CommunicationService(
        resolver, authority, PostgresCommunicationStore(engine, scope=scope, admission=admission)
    )
    return SimpleNamespace(
        clock=clock, db=db, resolver=resolver, authority=authority, keys=keys, phi=phi, service=service
    )


async def send(s, command=REF, body="PRIVATE_TEXT_CANARY", case=REF):
    receipt = json.loads(
        await s.phi.preserve(
            SECRET,
            csrf="csrf",
            origin="https://portal.example.test",
            case_ref=case,
            body=CommunicationContentSubmission(command_id=command, body=body),
            freeze=lambda raw: raw,
        )
    )
    return json.loads(
        await s.service.publish(
            SECRET,
            csrf="csrf",
            origin="https://portal.example.test",
            case_ref=case,
            body=CommunicationSubmission(
                command_id=command, body_ref=receipt["body_ref"], recipient_set_ref=OTHER
            ),
            freeze=lambda raw: raw,
        )
    )


@pytest.mark.asyncio
async def test_real_adapters_preserve_encrypt_publish_and_display_recipient_inbox(setup):
    s = setup
    first = await send(s)
    again = await send(s)
    assert first == again and first["disposition"] == "inbox_available"
    assert (
        len(s.db.contents)
        == len(s.db.messages)
        == len(s.db.recipients)
        == len(s.db.inbox)
        == len(s.db.history)
        == 1
    )
    page = json.loads(
        await s.service.page(SECRET, operation="list_messages", case_ref=REF, freeze=lambda raw: raw)
    )
    assert page["items"][0]["communication_ref"] == first["communication_ref"]
    assert page["items"][0]["delivery_state"] == "inbox_available"
    assert "PRIVATE_TEXT_CANARY" not in json.dumps(page)
    content = json.loads(
        await s.phi.read_content(
            SECRET, case_ref=REF, communication_ref=first["communication_ref"], freeze=lambda raw: raw
        )
    )
    assert content["body"] == "PRIVATE_TEXT_CANARY"
    assert b"PRIVATE_TEXT_CANARY" not in s.db.contents[0]["ciphertext"]
    assert all("PRIVATE_TEXT_CANARY" not in repr(p) for _, p in s.db.calls)
    history = json.loads(
        await s.service.page(SECRET, operation="list_history", case_ref=REF, freeze=lambda raw: raw)
    )
    assert history["history_scope"] == "portal_events"
    assert history["items"][0]["kind"] == "communication_available"
    assert history["items"][0]["receipt_ref"] is None


@pytest.mark.asyncio
async def test_inbox_failure_rolls_back_message_recipients_and_history(setup):
    s = setup
    s.db.fail_inbox = True
    with pytest.raises(ExternalCaseError):
        await send(s)
    assert len(s.db.contents) == 1
    assert not s.db.messages and not s.db.recipients and not s.db.inbox and not s.db.history
    s.db.fail_inbox = False
    await send(s)
    assert len(s.db.inbox) == 1


@pytest.mark.asyncio
async def test_lost_commit_ack_recovers_without_duplicate_delivery(setup):
    s = setup
    s.db.unknown_commit = True
    with pytest.raises(ExternalCaseError):
        await send(s)
    assert len(s.db.contents) == 1
    s.db.unknown_commit = False
    first = await send(s)
    s.db.unknown_commit = True
    with pytest.raises(ExternalCaseError):
        await send(s)
    s.db.unknown_commit = False
    assert await send(s) == first
    assert len(s.db.inbox) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["payload", "case", "recipients"])
async def test_conflicts_never_mutate_existing_delivery(setup, change):
    s = setup
    await send(s)
    if change == "recipients":
        s.authority.targets = [identity(s.resolver.principal), "b" * 64]
    with pytest.raises(ExternalCaseError) as error:
        await send(
            s,
            body="CHANGED" if change == "payload" else "PRIVATE_TEXT_CANARY",
            case=OTHER if change == "case" else REF,
        )
    assert error.value.code == "conflict"
    assert len(s.db.inbox) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["identity", "case", "tenant", "ciphertext", "policy"])
async def test_content_requires_current_recipient_exact_case_and_verified_bytes(setup, change):
    s = setup
    result = await send(s)
    if change == "identity":
        s.resolver.principal = s.resolver.principal.model_copy(update={"principal_ref": "someone-else"})
    if change == "tenant":
        s.db.contents[0]["tenant"] = "other-tenant"
    if change == "ciphertext":
        s.db.contents[0]["ciphertext"] = b"x" * 32
    if change == "policy":
        s.authority.denied.add("read_content")
    with pytest.raises(ExternalCaseError):
        await s.phi.read_content(
            SECRET,
            case_ref=OTHER if change == "case" else REF,
            communication_ref=result["communication_ref"],
            freeze=lambda raw: raw,
        )


@pytest.mark.asyncio
async def test_pagination_is_recipient_bound_and_does_not_grant_withheld_body_ref(setup):
    s = setup
    await send(s)
    await send(s, command=OTHER)
    await send(s, command=THIRD)
    s.authority.withhold = {"body_ref"}
    first = json.loads(
        await s.service.page(SECRET, operation="list_messages", case_ref=REF, limit=1, freeze=lambda raw: raw)
    )
    assert first["next_cursor"] and first["items"][0]["body_ref"] is None
    second = json.loads(
        await s.service.page(
            SECRET,
            operation="list_messages",
            case_ref=REF,
            limit=1,
            cursor=first["next_cursor"],
            freeze=lambda raw: raw,
        )
    )
    assert second["items"][0]["communication_ref"] != first["items"][0]["communication_ref"]
    s.resolver.principal = s.resolver.principal.model_copy(update={"membership_revision": 2})
    with pytest.raises(ExternalCaseError):
        await s.service.page(
            SECRET,
            operation="list_messages",
            case_ref=REF,
            limit=1,
            cursor=first["next_cursor"],
            freeze=lambda raw: raw,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", [SECRET, "membership", "grant", "policy", "key"])
async def test_final_render_checks_original_ceiling_after_all_io(setup, deadline):
    s = setup
    result = await send(s)
    until = s.clock[0] + timedelta(seconds=1)
    if deadline == SECRET:
        s.resolver.session_until = until
    if deadline == "membership":
        s.resolver.member_until = until
    if deadline == "grant":
        s.authority.until = until
    if deadline == "policy":
        s.authority.policy_until = until
    if deadline == "key":
        s.keys.valid_until = until

    def slow_render(raw):
        s.clock[0] += timedelta(seconds=2)
        return raw

    with pytest.raises(ExternalCaseError):
        await s.phi.read_content(
            SECRET, case_ref=REF, communication_ref=result["communication_ref"], freeze=slow_render
        )


@pytest.mark.asyncio
async def test_metadata_commit_release_expiry_and_authority_io_expiry_are_not_acknowledged(setup):
    s = setup
    s.db.after = lambda: s.clock.__setitem__(0, s.clock[0] + timedelta(minutes=3))
    with pytest.raises(ExternalCaseError):
        await send(s)
    assert len(s.db.contents) == 1 and not s.db.messages


@pytest.mark.asyncio
async def test_existing_authenticated_receipt_index_never_promotes_pending_and_reuses_proof(
    setup, monkeypatch
):
    from maezo.gateway.human.gateway import HumanGateway
    from maezo.gateway.human.receipt import PublicReceipt

    s = setup
    s.resolver.audience = "staff"
    gateway = object.__new__(HumanGateway)
    gateway._resolver = s.resolver
    receipt = PublicReceipt(
        tenant=s.resolver.principal.tenant,
        task_id=THIRD,
        command_id=REF,
        payload_digest="a" * 64,
        principal_ref=s.resolver.principal.principal_ref,
        workload_ref="synthetic-worker",
        status="pending",
        audit_intent_ref="synthetic-audit",
        audit_intent_hash="b" * 64,
    )

    async def get_receipt(self, **kwargs):
        assert kwargs["task_id"] == THIRD and kwargs["command_id"] == REF
        return receipt

    monkeypatch.setattr(HumanGateway, "read_receipt", get_receipt)
    with pytest.raises(ExternalCaseError):
        await s.service.index_existing_receipt(
            SECRET, case_ref=REF, task_id=THIRD, command_id=REF, gateway=gateway
        )
    assert not s.db.history
    receipt = PublicReceipt.model_validate(
        dict(
            receipt.model_dump(),
            status="committed",
            audit_result_ref="c" * 64,
            engine_receipt_ref=OTHER,
            engine_recorded_at=s.clock[0],
            consumed_task_revision="1",
            resulting_task_revision="2",
        )
    )
    event = await s.service.index_existing_receipt(
        SECRET, case_ref=REF, task_id=THIRD, command_id=REF, gateway=gateway
    )
    assert (
        await s.service.index_existing_receipt(
            SECRET, case_ref=REF, task_id=THIRD, command_id=REF, gateway=gateway
        )
        == event
    )
    assert len(s.db.history) == 1 and s.db.history[0]["receipt"] == OTHER
    s.authority.denied.add("index_receipt")
    with pytest.raises(ExternalCaseError):
        await s.service.index_existing_receipt(
            SECRET, case_ref=REF, task_id=THIRD, command_id=REF, gateway=gateway
        )
    assert len(s.db.history) == 1


@pytest.mark.asyncio
async def test_metadata_renderer_and_shortened_final_policy_are_bounded(setup, monkeypatch):
    from maezo.gateway.external_cases.models import instant

    s = setup
    await send(s)
    original = s.authority.authorize
    calls = 0
    shortened = s.clock[0] + timedelta(seconds=10)

    async def changing(access):
        nonlocal calls
        calls += 1
        if calls >= 3:
            s.authority.until = shortened
        return await original(access)

    monkeypatch.setattr(s.authority, "authorize", changing)
    page = json.loads(
        await s.service.page(SECRET, operation="list_messages", case_ref=REF, freeze=lambda raw: raw)
    )
    assert page["valid_until"] == instant(shortened)

    def late_response(raw):
        s.clock[0] = shortened + timedelta(seconds=1)
        return raw

    with pytest.raises(ExternalCaseError):
        await s.service.page(SECRET, operation="list_messages", case_ref=REF, freeze=late_response)


@pytest.mark.asyncio
async def test_nonrecipient_and_different_case_cannot_discover_existing_message(setup):
    s = setup
    await send(s)
    page = json.loads(
        await s.service.page(SECRET, operation="list_messages", case_ref=OTHER, freeze=lambda raw: raw)
    )
    assert not page["items"]
    s.resolver.principal = s.resolver.principal.model_copy(update={"principal_ref": "unrelated-principal"})
    page = json.loads(
        await s.service.page(SECRET, operation="list_messages", case_ref=REF, freeze=lambda raw: raw)
    )
    assert not page["items"]


@pytest.mark.asyncio
async def test_wrong_case_body_reference_cannot_be_published(setup):
    s = setup
    preserved = json.loads(
        await s.phi.preserve(
            SECRET,
            csrf="csrf",
            origin="https://portal.example.test",
            case_ref=REF,
            body=CommunicationContentSubmission(command_id=REF, body="PRIVATE_CANARY"),
            freeze=lambda raw: raw,
        )
    )
    with pytest.raises(ExternalCaseError) as error:
        await s.service.publish(
            SECRET,
            csrf="csrf",
            origin="https://portal.example.test",
            case_ref=OTHER,
            body=CommunicationSubmission(
                command_id=OTHER, body_ref=preserved["body_ref"], recipient_set_ref=REF
            ),
            freeze=lambda raw: raw,
        )
    assert error.value.code == "denied"
    assert not s.db.messages and not s.db.inbox


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["same", "receipt_ref", "authority_digest"])
async def test_command_recovery_binds_immutable_publication_authority(setup, monkeypatch, change):
    first = await send(setup)
    original = setup.authority.authorize

    async def current(access):
        grant = await original(access)
        if access.operation == "publish":
            if change == "authority_digest":
                return grant.model_copy(update={"authority_digest": "b" * 64})
            if change == "receipt_ref":
                return grant.model_copy(update={"authority_receipt_ref": OTHER})
        return grant

    monkeypatch.setattr(setup.authority, "authorize", current)
    before = copy.deepcopy((setup.db.messages, setup.db.recipients, setup.db.inbox, setup.db.history))
    if change == "authority_digest":
        with pytest.raises(ExternalCaseError) as failure:
            await send(setup)
        assert failure.value.code == "conflict"
    else:
        assert await send(setup) == first
    assert (setup.db.messages, setup.db.recipients, setup.db.inbox, setup.db.history) == before
