"""Offline SQL/lock model executes production transaction, publisher and mutation code.

Synthetic source/identity/DB only. This is NOT PostgreSQL, writer-role or real source
qualification. The lock model releases the same connection's locks at commit/rollback.
"""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from tests.unit.gateway.communications.test_delivery import (
    OTHER,
    REF,
    SECRET,
    Authority,
    Database,
    Resolver,
)

from maezo.gateway.communications.admission import (
    CommunicationPublicationSource,
    FrozenAuthority,
    PostgresCommunicationAdmission,
    PostgresCommunicationAuthority,
    PostgresCommunicationPublisher,
    Publication,
)
from maezo.gateway.communications.content import (
    PhiCommunicationService,
    PhiContentKeys,
    PostgresPhiCommunicationContent,
)
from maezo.gateway.communications.models import CommunicationAccess, CommunicationScope, fingerprint
from maezo.gateway.communications.postgres import PostgresCommunicationStore
from maezo.gateway.communications.service import CommunicationService
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.read_profile import digest, parse_model
from maezo.portal.api.auth import digest as secret_digest
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.contracts.communication_content import CommunicationContentSubmission
from maezo.portal.contracts.communications import CommunicationSubmission
from maezo.portal.engine.profile import strict_loads


class Lock:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.readers = set()
        self.writer = None

    async def take(self, owner, exclusive):
        async with self.condition:
            await self.condition.wait_for(
                lambda: self.writer in (None, owner) and (not exclusive or not self.readers - {owner})
            )
            if exclusive:
                self.writer = owner
            else:
                self.readers.add(owner)

    async def release(self, owner):
        async with self.condition:
            self.readers.discard(owner)
            if self.writer is owner:
                self.writer = None
            self.condition.notify_all()


def result(rows):
    return SimpleNamespace(
        mappings=lambda: SimpleNamespace(
            one_or_none=lambda: rows[0] if rows else None,
            all=lambda: rows,
        )
    )


class Connection:
    def __init__(self, db):
        self.db = db
        self.active = False
        self.held = []
        self.local = Database(db.clock)
        self.local.resolver = db.resolver
        self.local.calls = db.calls
        for name in db.names:
            setattr(self.local, name, copy.deepcopy(getattr(db, name)))
        self.heads, self.publications = {}, {}
        self.mutation = False
        self.identity_delete = False

    async def begin(self):
        self.active = True
        return self

    @property
    def is_active(self):
        return self.active

    def in_transaction(self):
        return self.active

    async def lock(self, key, exclusive=False):
        lock = self.db.locks.setdefault(key, Lock())
        if exclusive:
            self.db.writer_attempt.set()
        await lock.take(self, exclusive)
        self.held.append(lock)

    async def release(self):
        self.active = False
        for lock in self.held:
            await lock.release(self)
        self.held.clear()

    async def commit(self):
        if self.mutation and self.db.pause_commit:
            self.db.committing.set()
            if self.db.lose_connection:
                await self.release()
            await self.db.commit_continue.wait()
            if self.db.lose_connection:
                raise RuntimeError("synthetic connection loss")
        if self.mutation:
            for name in self.db.names:
                setattr(self.db, name, getattr(self.local, name))
        self.db.authorities.update(self.heads)
        self.db.publications.update(self.publications)
        if self.identity_delete:
            self.db.identity_present = False
        self.db.order.append("mutation_commit" if self.mutation else "other_commit")
        await self.release()
        if self.db.unknown:
            self.db.unknown = False
            raise RuntimeError("synthetic lost commit acknowledgement")

    async def rollback(self):
        await self.release()

    async def close(self):
        await self.release()

    async def invalidate(self):
        await self.release()

    async def execute(self, sql, params):
        q = " ".join(str(sql).split())
        self.db.trace.append((id(self), q))
        key = params.get("access")
        if "set_config" in q:
            return result([])
        if q.startswith("INSERT INTO portal_communication.authority_head"):
            if key not in self.db.authorities:
                await self.lock(("authority", key), True)
                self.heads[key] = dict(revision="0", publication_ref=None, payload=None)
            return result([])
        if q.startswith("SELECT revision,publication_ref,payload"):
            if q.endswith("FOR SHARE") or q.endswith("FOR UPDATE"):
                await self.lock(("authority", key), q.endswith("FOR UPDATE"))
            row = self.heads.get(key, self.db.authorities.get(key))
            return result([row] if row else [])
        if q.startswith("SELECT request_digest,revision FROM portal_communication.authority_publication"):
            row = self.db.publications.get(params["publication"])
            return result([row] if row else [])
        if q.startswith("UPDATE portal_communication.authority_head"):
            self.heads[key] = dict(
                revision=params["revision"], publication_ref=params["publication"], payload=params["payload"]
            )
            return result([])
        if q.startswith("INSERT INTO portal_communication.authority_publication"):
            self.publications[params["publication"]] = dict(
                request_digest=params["digest"], revision=params["revision"]
            )
            return result([])
        if q.startswith("SELECT * FROM portal_communication.lock_session"):
            await self.lock("identity")
            if not self.db.identity_present:
                return result([])
            return await self.local.execute(sql, params)
        if q.startswith("DELETE FROM portal_sessions"):
            await self.lock("identity", True)
            self.identity_delete = True
            return result([])
        if q.startswith("UPDATE public.portal_memberships"):
            await self.lock("identity", True)
            self.identity_delete = True  # unavailable updated membership at next admission
            return result([])
        if q.startswith("INSERT INTO portal_communication.inbox") and self.db.pause_inbox:
            self.db.inbox_entered.set()
            await self.db.inbox_continue.wait()
        if q.startswith("INSERT INTO portal_communication."):
            self.mutation = True
        self.local.fail_inbox = self.db.fail_inbox
        value = await self.local.execute(sql, params)
        if q.startswith("INSERT INTO portal_communication.inbox") and self.db.dead_after_inbox:
            self.active = False
        return value


class Engine:
    dialect = SimpleNamespace(name="postgresql")
    echo = False
    sync_engine = SimpleNamespace(hide_parameters=True)

    def __init__(self, db):
        self.db = db

    async def connect(self):
        return Connection(self.db)

    @asynccontextmanager
    async def begin(self):
        async with transaction(self, 5) as conn:
            yield conn


class LockedDatabase(Database):
    names = ("contents", "messages", "recipients", "inbox", "history", "cursors")

    def __init__(self, clock, resolver):
        super().__init__(clock)
        self.resolver = resolver
        self.publications, self.locks = {}, {}
        self.trace, self.order = [], []
        self.identity_present = True
        self.dead_after_inbox = False
        self.pause_inbox = self.pause_commit = self.lose_connection = self.unknown = False
        self.inbox_entered, self.inbox_continue = asyncio.Event(), asyncio.Event()
        self.committing, self.commit_continue = asyncio.Event(), asyncio.Event()
        self.writer_attempt = asyncio.Event()


class Source(CommunicationPublicationSource):
    """Explicit test-only authority source, never installed by application composition."""

    def __init__(self, db, policy):
        self.db, self.policy = db, policy
        self.revoked = False
        self.acks, self.uncertain_count, self.freeze_count = [], 0, 0
        self.invalid = False
        self.until = self.db.clock[0] + timedelta(minutes=2)

    async def freeze(self, access):
        self.freeze_count += 1
        row = self.db.authorities.get(digest(access))
        grant = None if self.revoked else await self.policy.authorize(access)

        def verify(raw):
            item = parse_model(Publication, strict_loads(raw))
            if self.invalid or item.access != access or item.grant != grant:
                raise ExternalCaseError("unavailable")

        def uncertain():
            self.uncertain_count += 1

        return FrozenAuthority(
            expected_revision=row["revision"] if row else "0",
            source_receipt_ref=REF,
            source_digest="c" * 64,
            valid_until=self.until,
            grant=grant,
            verify=verify,
            live=lambda: None,
            committed=self.acks.append,
            uncertain=uncertain,
        )


@pytest.fixture
def system(monkeypatch):
    clock = [datetime.now(UTC)]
    monkeypatch.setattr("maezo.gateway.communications.models.now", lambda: clock[0])
    resolver = Resolver(clock)
    scope = CommunicationScope(tenant=resolver.principal.tenant, environment="synthetic")
    db = LockedDatabase(clock, resolver)
    engine = Engine(db)
    resolver.store = PostgresIdentityStore(scope.tenant, engine)
    admission = PostgresCommunicationAdmission(resolver.store, scope=scope, issuer=resolver.settings.issuer)
    policy = Authority(resolver, scope, clock)
    policy.db = SimpleNamespace(authorities={})  # fixture facts do not populate the tested SQL database
    source = Source(db, policy)
    authority = PostgresCommunicationAuthority(engine, scope=scope)
    publisher = PostgresCommunicationPublisher(engine, scope=scope, source=source)
    store = PostgresCommunicationStore(engine, scope=scope, admission=admission)
    keys = PhiContentKeys(
        scope=scope, active_key_id="synthetic", keys={"synthetic": b"a" * 32}, valid_until=policy.until
    )
    content = PostgresPhiCommunicationContent(engine, scope=scope, keys=keys, admission=admission)
    return SimpleNamespace(
        clock=clock,
        db=db,
        engine=engine,
        resolver=resolver,
        scope=scope,
        source=source,
        publisher=publisher,
        authority=authority,
        store=store,
        content=content,
        admission=admission,
        phi=PhiCommunicationService(resolver, authority, content),
        service=CommunicationService(resolver, authority, store),
    )


async def prepare(s):
    body = CommunicationContentSubmission(command_id=REF, body="SYNTHETIC_CONTENT")
    access = CommunicationAccess(
        scope=s.scope,
        principal=s.resolver.principal,
        operation="create_content",
        case_ref=REF,
        request_digest=fingerprint(body.model_dump(mode="json")),
    )
    await s.publisher.publish(access)
    receipt = json.loads(
        await s.phi.preserve(
            SECRET,
            csrf="csrf",
            origin=s.resolver.settings.public_origin,
            case_ref=REF,
            body=body,
            freeze=lambda raw: raw,
        )
    )
    submission = CommunicationSubmission(
        command_id=REF, body_ref=receipt["body_ref"], recipient_set_ref=OTHER
    )
    access = CommunicationAccess(
        scope=s.scope,
        principal=s.resolver.principal,
        operation="publish",
        case_ref=REF,
        resource_ref=submission.body_ref,
        request_digest=fingerprint(submission.model_dump(mode="json")),
    )
    await s.publisher.publish(access)
    grant = await s.authority.authorize(access)
    return submission, grant


async def mutate(s, body, grant):
    return await s.store.publish(
        grant,
        body,
        sender_kind="provider",
        deadline=grant.ceiling(),
        secret=SECRET,
        session=s.resolver.records(),
    )


@pytest.mark.asyncio
async def test_real_sql_publication_and_three_write_boundaries(system):
    s = system
    body, grant = await prepare(s)
    receipt = await mutate(s, body, grant)
    assert len(s.db.messages) == len(s.db.inbox) == 1
    access = CommunicationAccess(
        scope=s.scope,
        principal=s.resolver.principal,
        operation="index_receipt",
        case_ref=REF,
        resource_ref=REF,
        request_digest=fingerprint(dict(command_ref=REF, receipt_ref=OTHER, receipt_digest="a" * 64)),
    )
    await s.publisher.publish(access)
    indexed = await s.store.index_receipt(
        await s.authority.authorize(access),
        command_ref=REF,
        receipt_ref=OTHER,
        receipt_digest="a" * 64,
        deadline=grant.ceiling(),
        secret=SECRET,
        session=s.resolver.records(),
    )
    assert indexed and receipt.communication_ref
    for marker in (
        "INSERT INTO portal_communication.content ",
        "INSERT INTO portal_communication.message ",
        "INSERT INTO portal_communication.history ",
    ):
        writers = {conn for conn, sql in s.db.trace if sql.startswith(marker)}
        assert writers
        for conn in writers:
            statements = [q for c, q in s.db.trace if c == conn]
            assert any(q.endswith("FOR SHARE") for q in statements)
            assert "SELECT * FROM portal_communication.lock_session(:secret)" in statements


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", ["authority", "session", "membership"])
async def test_revocation_cannot_commit_during_inbox_io(system, writer):
    s = system
    body, grant = await prepare(s)
    s.db.pause_inbox = True
    s.db.writer_attempt.clear()
    mutation = asyncio.create_task(mutate(s, body, grant))
    await asyncio.wait_for(s.db.inbox_entered.wait(), 1)

    async def revoke():
        if writer == "authority":
            s.source.revoked = True
            return await s.publisher.publish(grant.access)
        if writer == "session":
            return await s.resolver.store.revoke_session(secret_digest(SECRET))
        async with transaction(s.engine, 5) as conn:
            await conn.execute(
                text("UPDATE public.portal_memberships SET payload=:payload"),
                {"payload": "synthetic revoked"},
            )

    revocation = asyncio.create_task(revoke())
    await asyncio.wait_for(s.db.writer_attempt.wait(), 1)
    assert not revocation.done()
    s.db.inbox_continue.set()
    await mutation
    await revocation
    assert len(s.db.messages) == len(s.db.inbox) == 1
    assert s.db.order[-2:] == ["mutation_commit", "other_commit"]
    with pytest.raises(ExternalCaseError):
        await mutate(s, body, grant)


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [False, True])
async def test_same_connection_commit_uncertainty_retains_or_releases_all_locks_together(system, lost):
    s = system
    body, grant = await prepare(s)
    s.db.pause_commit, s.db.lose_connection = True, lost
    s.db.writer_attempt.clear()
    mutation = asyncio.create_task(mutate(s, body, grant))
    await asyncio.wait_for(s.db.committing.wait(), 1)
    revocation = asyncio.create_task(s.resolver.store.revoke_session(secret_digest(SECRET)))
    await asyncio.wait_for(s.db.writer_attempt.wait(), 1)
    if lost:
        await revocation
        assert not s.db.messages and not s.db.inbox
    else:
        assert not revocation.done()
    s.db.commit_continue.set()
    if lost:
        with pytest.raises(ExternalCaseError, match="uncertain"):
            await mutation
        assert not s.db.messages and not s.db.inbox
    else:
        await mutation
        await revocation
        assert s.db.messages and s.db.inbox


@pytest.mark.asyncio
async def test_source_unknown_commit_keeps_exact_publication_and_freeze_until_reconcile(system):
    s = system
    body, grant = await prepare(s)
    before = len(s.source.acks)
    s.source.revoked = True
    s.db.unknown = True
    with pytest.raises(ExternalCaseError, match="uncertain"):
        await s.publisher.publish(grant.access)
    pending = s.publisher._pending[0]
    row = copy.deepcopy(s.db.authorities[digest(grant.access)])
    assert len(s.source.acks) == before and s.source.uncertain_count == 1
    with pytest.raises(ExternalCaseError):
        await s.publisher.publish(grant.access)
    with pytest.raises(ExternalCaseError, match="denied"):
        await mutate(s, body, grant)
    receipt = await s.publisher.reconcile()
    assert receipt.publication_ref == pending.publication_ref
    assert s.db.authorities[digest(grant.access)] == row
    assert len(s.source.acks) == before + 1 and s.publisher._pending is None


@pytest.mark.asyncio
async def test_missing_source_or_malformed_source_proof_cannot_publish(system):
    s = system
    _, grant = await prepare(s)
    before = copy.deepcopy(s.db.authorities)
    missing = PostgresCommunicationPublisher(s.engine, scope=s.scope, source=None)
    with pytest.raises(ExternalCaseError, match="unavailable"):
        await missing.publish(grant.access)
    s.source.invalid = True
    with pytest.raises(ExternalCaseError, match="unavailable"):
        await s.publisher.publish(grant.access)
    assert s.db.authorities == before


@pytest.mark.asyncio
async def test_inbox_rollback_and_unguarded_store_refuse(system):
    s = system
    body, grant = await prepare(s)
    s.db.fail_inbox = True
    with pytest.raises(ExternalCaseError, match="unavailable"):
        await mutate(s, body, grant)
    assert not s.db.messages and not s.db.inbox
    unguarded = PostgresCommunicationStore(s.engine, scope=s.scope)
    with pytest.raises(ExternalCaseError, match="unavailable"):
        await unguarded.publish(
            grant,
            body,
            sender_kind="provider",
            deadline=grant.ceiling(),
            secret=SECRET,
            session=s.resolver.records(),
        )


def test_separate_identity_or_mutation_engine_is_unavailable(system):
    s = system
    other = Engine(s.db)
    with pytest.raises(ExternalCaseError, match="unavailable"):
        PostgresCommunicationStore(other, scope=s.scope, admission=s.admission)
    old_store = s.resolver.store
    s.resolver.store = PostgresIdentityStore(s.scope.tenant, other)
    with pytest.raises(ExternalCaseError, match="unavailable"):
        CommunicationService(s.resolver, s.authority, s.store)
    s.resolver.store = old_store


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["session", "membership", "publication"])
async def test_original_deadline_expiring_during_inbox_rolls_back(system, boundary):
    s = system
    until = s.clock[0] + timedelta(seconds=1)
    if boundary == "session":
        s.resolver.session_until = until
    elif boundary == "membership":
        s.resolver.member_until = until
    else:
        s.source.until = until
    body, grant = await prepare(s)
    s.db.pause_inbox = True
    mutation = asyncio.create_task(mutate(s, body, grant))
    await asyncio.wait_for(s.db.inbox_entered.wait(), 1)
    s.clock[0] += timedelta(seconds=2)
    s.db.inbox_continue.set()
    with pytest.raises(ExternalCaseError, match="denied"):
        await mutation
    assert not s.db.messages and not s.db.inbox


@pytest.mark.asyncio
async def test_missing_or_changed_identity_refuses_before_any_write(system):
    s = system
    body, grant = await prepare(s)
    initial = s.resolver.records()
    s.resolver.principal = s.resolver.principal.model_copy(update={"membership_revision": 2})
    with pytest.raises(ExternalCaseError, match="denied"):
        await s.store.publish(
            grant, body, sender_kind="provider", deadline=grant.ceiling(), secret=SECRET, session=initial
        )
    assert not s.db.messages and not s.db.inbox


@pytest.mark.asyncio
async def test_explicit_prior_revocation_refuses_preserve_and_index(system):
    s = system
    body, _ = await prepare(s)
    for operation in ("create_content", "index_receipt"):
        request = CommunicationContentSubmission(command_id=OTHER, body="SYNTHETIC")
        request_digest = (
            fingerprint(request.model_dump(mode="json"))
            if operation == "create_content"
            else fingerprint(dict(command_ref=REF, receipt_ref=OTHER, receipt_digest="a" * 64))
        )
        access = CommunicationAccess(
            scope=s.scope,
            principal=s.resolver.principal,
            operation=operation,
            case_ref=REF,
            request_digest=request_digest,
        )
        s.source.revoked = False
        await s.publisher.publish(access)
        grant = await s.authority.authorize(access)
        s.source.revoked = True
        await s.publisher.publish(access)
        before = copy.deepcopy((s.db.contents, s.db.history))
        with pytest.raises(ExternalCaseError, match="denied"):
            if operation == "create_content":
                await s.content.preserve(
                    grant, request, deadline=grant.ceiling(), secret=SECRET, session=s.resolver.records()
                )
            else:
                await s.store.index_receipt(
                    grant,
                    command_ref=REF,
                    receipt_ref=OTHER,
                    receipt_digest="a" * 64,
                    deadline=grant.ceiling(),
                    secret=SECRET,
                    session=s.resolver.records(),
                )
        assert (s.db.contents, s.db.history) == before


@pytest.mark.asyncio
async def test_mutation_unknown_commit_recovers_same_immutable_receipt(system):
    s = system
    body, grant = await prepare(s)
    s.db.unknown = True
    with pytest.raises(ExternalCaseError, match="uncertain"):
        await mutate(s, body, grant)
    before = copy.deepcopy((s.db.messages, s.db.recipients, s.db.inbox, s.db.history))
    recovered = await mutate(s, body, grant)
    assert recovered.communication_ref == s.db.messages[0]["communication"]
    assert (s.db.messages, s.db.recipients, s.db.inbox, s.db.history) == before


@pytest.mark.asyncio
async def test_held_grant_cannot_bypass_new_authority_digest(system):
    s = system
    body, grant = await prepare(s)
    original = s.source.policy.authorize

    async def changed(access):
        return (await original(access)).model_copy(update={"authority_digest": "b" * 64})

    s.source.policy.authorize = changed
    await s.publisher.publish(grant.access)
    with pytest.raises(ExternalCaseError, match="conflict"):
        await mutate(s, body, grant)
    assert not s.db.messages and not s.db.inbox


@pytest.mark.asyncio
async def test_publisher_cas_conflict_retains_exact_pending_request(system):
    s = system
    _, grant = await prepare(s)
    original = s.source.freeze

    async def stale(access):
        from dataclasses import replace

        return replace(await original(access), expected_revision="0")

    s.source.freeze = stale
    before = copy.deepcopy(s.db.authorities)
    with pytest.raises(ExternalCaseError, match="conflict"):
        await s.publisher.publish(grant.access)
    pending = s.publisher._pending[0]
    assert s.source.uncertain_count == 1
    with pytest.raises(ExternalCaseError, match="conflict"):
        await s.publisher.reconcile()
    assert s.publisher._pending[0] == pending and s.db.authorities == before


@pytest.mark.asyncio
async def test_dead_transaction_cannot_commit_pending_inbox(system):
    s = system
    body, grant = await prepare(s)
    s.db.dead_after_inbox = True
    with pytest.raises(ExternalCaseError, match="unavailable"):
        await mutate(s, body, grant)
    assert not s.db.messages and not s.db.inbox
