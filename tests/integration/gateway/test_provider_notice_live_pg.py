"""Real PG16 notice/ack journal in an isolated TestOnly DB, with verified TLS.

ROOT/CI executes an own portable, disposable PostgreSQL with verified TLS.
The helper validates Docker ownership and its actual loopback published port.
All authorities, identities and acts are synthetic. The identity helper's
stable-return contract here is not qualification of a production AUTH-SL1 helper.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from tests.support.provider_tls_pg import owned_tls_postgres
from tests.unit.gateway.communications.test_provider_notice import CONTENT, NOTICE, PROVIDER, ack
from tests.unit.portal.test_human_session import ISSUER, SUBJECT, config, membership

from maezo.gateway.capabilities.models import NoticeIntent
from maezo.gateway.communications.admission import PostgresCommunicationAdmission
from maezo.gateway.communications.models import CommunicationScope
from maezo.gateway.communications.provider_notice import (
    NOTICE_FUNCTIONS,
    NOTICE_TABLES,
    FunctionPin,
    NoticeRelationPin,
    PostgresProviderNoticeStore,
    ProviderNoticeInstallation,
    ProviderNoticeScope,
)
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.portal.api.auth import digest
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import SessionRecord
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.communications import ProviderNoticeReceipt
from maezo.portal.contracts.models import SubjectBinding

pytestmark = pytest.mark.integration


@dataclass
class TestOnlyPgNotices:
    __test__ = False
    control: asyncpg.Connection
    producer: PostgresProviderNoticeStore
    recipient: PostgresProviderNoticeStore
    notice_scope: ProviderNoticeScope
    intent: NoticeIntent
    resolver: HumanSessionResolver
    token: str

    async def prepare(self) -> ProviderNoticeReceipt:
        return await self.producer.prepare(
            self.notice_scope, self.intent, command_ref="prepare-command-testonly", digest="d" * 64
        )

    async def acknowledge(self, *, body: object | None = None) -> object:
        session = await self.resolver.resolve(self.token)
        observation = await self.recipient.recipient(
            secret=self.token,
            session=session,
            notice_ref=NOTICE,
            acknowledgement=ack() if body is None else body,
        )
        return observation.result


@pytest.fixture
async def pg_notices(tmp_path) -> AsyncIterator[TestOnlyPgNotices]:
    async with owned_tls_postgres(tmp_path, owner="provider-notice") as pg:
        url = pg.url_for("postgres", pg.password)
        context = pg.tls_context
        admin = pg.admin
        suffix = uuid.uuid4().hex[:12]
        database = f"testonly_notice_{suffix}"
        owner, validator, producer, recipient, phi_owner, id_owner = (
            f"pno_{suffix}",
            f"pnv_{suffix}",
            f"pnp_{suffix}",
            f"pnr_{suffix}",
            f"pnphi_{suffix}",
            f"pnid_{suffix}",
        )
        roles = [owner, validator, producer, recipient, phi_owner, id_owner]
        passwords = {producer: secrets.token_hex(24), recipient: secrets.token_hex(24)}
        control = None
        engines = []
        created_db = False
        try:
            for role in roles:
                if role in passwords:
                    await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{passwords[role]}'")
                else:
                    await admin.execute(f"CREATE ROLE {role} NOLOGIN")
            await admin.execute(f"CREATE DATABASE {database}")
            created_db = True
            db_url = url.set(drivername="postgresql", database=database, query={})
            control = await asyncpg.connect(
                db_url.render_as_string(hide_password=False), ssl=context, timeout=5
            )
            await control.execute(f"REVOKE TEMPORARY ON DATABASE {database} FROM PUBLIC")
            await control.execute(
                f"CREATE SCHEMA portal_communication AUTHORIZATION {phi_owner};"
                f"CREATE SCHEMA portal_provider_notice AUTHORIZATION {owner}"
            )
            await control.execute(f"SET ROLE {phi_owner}")
            protected = await asyncio.to_thread(Path("src/maezo/gateway/communications/schema.sql").read_text)
            await control.execute(protected.replace("CREATE SCHEMA portal_communication;", ""))
            await control.execute(
                await asyncio.to_thread(
                    Path("src/maezo/gateway/communications/authority_schema.sql").read_text
                )
            )
            await control.execute("RESET ROLE")
            await control.execute(
                "CREATE TABLE public.portal_sessions(tenant text NOT NULL,secret_hash text NOT NULL,"
                "expires_at timestamptz NOT NULL,payload text NOT NULL,PRIMARY KEY(tenant,secret_hash));"
                "CREATE TABLE public.portal_memberships(tenant text NOT NULL,issuer text NOT NULL,"
                "subject text NOT NULL,"
                "principal_ref text NOT NULL,payload text NOT NULL,PRIMARY KEY(tenant,issuer,subject));"
                "REVOKE ALL ON public.portal_sessions,public.portal_memberships FROM PUBLIC"
            )
            await control.execute(
                f"ALTER FUNCTION portal_communication.lock_session(text) OWNER TO {id_owner};"
                f"GRANT USAGE ON SCHEMA portal_communication TO {id_owner},{owner},{recipient};"
                f"GRANT SELECT ON portal_communication.admission_login TO {id_owner};"
                "GRANT SELECT,UPDATE(payload) ON public.portal_sessions,public.portal_memberships "
                f"TO {id_owner};"
                f"GRANT EXECUTE ON FUNCTION portal_communication.lock_session(text) TO {owner},{recipient};"
                f"GRANT SELECT(tenant,environment,case_ref,body_ref,content_digest),UPDATE(body_ref)"
                f" ON portal_communication.content TO {owner}"
            )
            await control.execute(
                "INSERT INTO portal_communication.admission_login VALUES($1,'test-tenant')", recipient
            )
            await control.execute(f"SET ROLE {owner}")
            await control.execute(
                await asyncio.to_thread(
                    Path("src/maezo/gateway/communications/provider-notice-schema.sql").read_text
                )
            )
            await control.execute("RESET ROLE")
            await control.execute(
                f"GRANT USAGE ON SCHEMA portal_provider_notice TO {validator},{producer},{recipient};"
                f"GRANT SELECT,INSERT,UPDATE ON portal_provider_notice.authority TO {validator}"
            )
            for name, args in NOTICE_FUNCTIONS.items():
                actor = (
                    producer
                    if name == "prepare_notice"
                    else recipient
                    if name in {"acknowledge_notice", "inspect_notice"}
                    else None
                )
                if actor:
                    await control.execute(
                        f"GRANT EXECUTE ON FUNCTION portal_provider_notice.{name}({args}) TO {actor}"
                    )
            until = datetime.now(UTC) + timedelta(minutes=10)
            token = secrets.token_urlsafe(32)
            record = membership(
                audience="provider",
                subject_bindings=(SubjectBinding(kind="provider", resource_ref=PROVIDER),),
            )
            session = SessionRecord(
                secret_hash=digest(token),
                session_ref="session-testonly",
                csrf_token="csrf-testonly",
                issuer=ISSUER,
                subject=SUBJECT,
                principal_ref=record.principal_ref,
                membership_revision=1,
                authenticated_at=datetime.now(UTC),
                expires_at=until,
            )
            await control.execute(
                "INSERT INTO public.portal_sessions VALUES($1,$2,$3,$4)",
                record.tenant,
                session.secret_hash,
                session.expires_at,
                session.model_dump_json(),
            )
            await control.execute(
                "INSERT INTO public.portal_memberships VALUES($1,$2,$3,$4,$5)",
                record.tenant,
                ISSUER,
                SUBJECT,
                record.principal_ref,
                record.model_dump_json(),
            )
            await control.execute(
                "INSERT INTO portal_communication.content VALUES($1,'testonly','case-testonly',"
                "$2,'body-command-testonly','protected-body-testonly',$3,$4,'key-testonly',$5,$6,"
                "'testonly-content-authority',$3,clock_timestamp())",
                record.tenant,
                "e" * 64,
                "f" * 64,
                CONTENT,
                secrets.token_bytes(12),
                secrets.token_bytes(32),
            )
            notice_scope = ProviderNoticeScope(
                tenant_ref=record.tenant,
                environment="testonly",
                legal_entity_ref="operator",
                principal_ref="workload-testonly",
                task_ref="task-testonly",
                purpose_ref="provider_notice",
                source_authority_ref="testonly-source",
                policy_revision="policy1",
                contract_revision="contract1",
                data_classification="opaque_references",
                business_revision="revision1",
                producer_role=producer,
            )
            intent = NoticeIntent(
                notice_ref=NOTICE,
                notice_class="mandatory",
                authorized_content_ref="protected-body-testonly",
                recipient_authority_ref="recipient-authority-testonly",
                confirmed_channel_ref="portal-ack-testonly",
                delivery_policy_ref="explicit-ack-policy-testonly",
            )
            await control.execute(f"SET ROLE {validator}")
            await control.execute(
                "INSERT INTO portal_provider_notice.authority VALUES($1,'testonly',$2,"
                "'prepare-command-testonly',$3,$4::jsonb,$5::jsonb,'case-testonly',$6,$7,$8,$9,1,"
                "'protected-body-testonly',$10,'revision1','source-receipt-testonly','explicit-ack-policy-testonly',"
                "'protected_portal_ack','enabled',clock_timestamp()-interval '1 second',$11)",
                record.tenant,
                NOTICE,
                "d" * 64,
                json.dumps(notice_scope.model_dump(mode="json")),
                json.dumps(intent.model_dump(mode="json")),
                PROVIDER,
                record.principal_ref,
                ISSUER,
                SUBJECT,
                CONTENT,
                until,
            )
            await control.execute("RESET ROLE")
            relations = []
            for name in sorted(NOTICE_TABLES):
                oid = await control.fetchval("SELECT to_regclass($1)::oid", f"portal_provider_notice.{name}")
                relations.append(NoticeRelationPin(name=name, oid=oid))
            functions = []
            for name, args in sorted(NOTICE_FUNCTIONS.items()):
                oid = await control.fetchval(
                    "SELECT to_regprocedure($1)::oid", f"portal_provider_notice.{name}({args})"
                )
                definition = await control.fetchval("SELECT pg_get_functiondef($1)", oid)
                functions.append(
                    FunctionPin(name=name, oid=oid, sha256=hashlib.sha256(definition.encode()).hexdigest())
                )
            lock_oid = await control.fetchval(
                "SELECT to_regprocedure('portal_communication.lock_session(text)')::oid"
            )
            lock_body = await control.fetchval("SELECT pg_get_functiondef($1)", lock_oid)
            descriptor = ProviderNoticeInstallation(
                schema_version="provider-notice-installation.v1",
                scope=CommunicationScope(tenant=record.tenant, environment="testonly"),
                database_oid=await control.fetchval(
                    "SELECT oid FROM pg_database WHERE datname=current_database()"
                ),
                schema_name="portal_provider_notice",
                schema_oid=await control.fetchval(
                    "SELECT oid FROM pg_namespace WHERE nspname='portal_provider_notice'"
                ),
                owner_role=owner,
                authority_validator_role=validator,
                producer_role=producer,
                recipient_role=recipient,
                installation_receipt_ref="testonly-install",
                identity_source_ref="testonly-identity-source",
                identity_installation_receipt_ref="testonly-id-install",
                content_relation_oid=await control.fetchval(
                    "SELECT 'portal_communication.content'::regclass::oid"
                ),
                content_owner_role=phi_owner,
                session_lock_oid=lock_oid,
                session_lock_owner_role=id_owner,
                session_lock_sha256=hashlib.sha256(lock_body.encode()).hexdigest(),
                relations=tuple(relations),
                functions=tuple(functions),
                valid_until=until,
            )
            for actor in (producer, recipient):
                actor_url = db_url.set(
                    drivername="postgresql+asyncpg", username=actor, password=passwords[actor]
                )
                engines.append(create_async_engine(actor_url, connect_args={"ssl": context}))
            identity_store = PostgresIdentityStore(record.tenant, engines[1])
            admission = PostgresCommunicationAdmission(identity_store, scope=descriptor.scope, issuer=ISSUER)
            # Explicit TestOnly IdP/session fixture used for software mechanics, not production factory.
            identities = LocalTestIdentityStore(record.tenant)
            identities.memberships[(ISSUER, SUBJECT)] = record
            await identities.put_session(session, None)
            resolver = HumanSessionResolver(config(), identities)
            yield TestOnlyPgNotices(
                control,
                PostgresProviderNoticeStore(engines[0], descriptor, role="producer"),
                PostgresProviderNoticeStore(
                    engines[1], descriptor, role="recipient", identity_admission=admission
                ),
                notice_scope,
                intent,
                resolver,
                token,
            )
        finally:
            for engine in engines:
                await engine.dispose()
            if control is not None:
                await control.close()
            if created_db:
                await admin.execute(f"DROP DATABASE {database}")
            for role in reversed(roles):
                await admin.execute(f"DROP ROLE IF EXISTS {role}")


async def test_real_pg_prepare_is_pending_and_concurrent_replay_is_one_fact(
    pg_notices: TestOnlyPgNotices,
) -> None:
    results = await asyncio.gather(*(pg_notices.prepare() for _ in range(5)))
    assert all(r == results[0] for r in results)
    assert results[0].delivery_status == "pending" and results[0].provider_delivery_receipt_ref is None
    assert await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.notice") == 1
    assert await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.audit") == 1


async def test_real_pg_ack_receipt_survives_new_store_instance(pg_notices: TestOnlyPgNotices) -> None:
    await pg_notices.prepare()
    result = await pg_notices.acknowledge()
    old = pg_notices.recipient
    pg_notices.recipient = PostgresProviderNoticeStore(
        old.engine, old.installation, role="recipient", identity_admission=old.identity_admission
    )
    assert await pg_notices.acknowledge() == result
    assert result.delivery_status == "delivered"
    assert (
        await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.acknowledgement") == 1
    )
    assert await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.audit") == 2
    assert (await pg_notices.prepare()).provider_delivery_receipt_ref == result.provider_delivery_receipt_ref


async def test_real_pg_wrong_digest_and_source_revocation_do_not_create_ack(
    pg_notices: TestOnlyPgNotices,
) -> None:
    await pg_notices.prepare()
    with pytest.raises(ExternalCaseError):
        await pg_notices.acknowledge(body=ack(content_digest="b" * 64))
    await pg_notices.control.execute("UPDATE portal_provider_notice.authority SET state='revoked'")
    with pytest.raises(ExternalCaseError):
        await pg_notices.acknowledge()
    assert (
        await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.acknowledgement") == 0
    )


async def test_real_pg_source_revocation_while_prepare_waits_fences_effect(
    pg_notices: TestOnlyPgNotices,
) -> None:
    tx = pg_notices.control.transaction()
    await tx.start()
    await pg_notices.control.execute("UPDATE portal_provider_notice.authority SET state='revoked'")
    prepare = asyncio.create_task(pg_notices.prepare())
    await asyncio.sleep(0.05)
    await tx.commit()
    with pytest.raises(ExternalCaseError):
        await prepare
    assert await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.notice") == 0


async def test_real_pg_acl_or_identity_function_hash_drift_is_rejected(pg_notices: TestOnlyPgNotices) -> None:
    descriptor = pg_notices.producer.installation
    pg_notices.producer.installation = descriptor.model_copy(update={"session_lock_sha256": "0" * 64})
    with pytest.raises(ExternalCaseError):
        await pg_notices.prepare()
    pg_notices.producer.installation = descriptor
    await pg_notices.control.execute(
        "GRANT EXECUTE ON FUNCTION portal_provider_notice.prepare_notice("
        + NOTICE_FUNCTIONS["prepare_notice"]
        + ") TO PUBLIC"
    )
    with pytest.raises(ExternalCaseError):
        await pg_notices.prepare()
    assert await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.notice") == 0


async def test_real_pg_direct_sql_cannot_forge_actor_or_receipt(pg_notices: TestOnlyPgNotices) -> None:
    await pg_notices.prepare()
    async with pg_notices.recipient.engine.connect() as db:
        from sqlalchemy import text

        with pytest.raises(DBAPIError) as denied:
            await db.execute(text("INSERT INTO portal_provider_notice.acknowledgement DEFAULT VALUES"))
        assert denied.value.orig.sqlstate == "42501"
        await db.rollback()
        with pytest.raises(DBAPIError) as denied:
            await db.execute(
                text(
                    "SELECT portal_provider_notice.acknowledge_notice("
                    ":tenant,'testonly',:notice,'forged-command',:digest,'revision1',:content,:secret,"
                    "'forged-receipt','forged-audit',clock_timestamp()+interval '1 minute')"
                ),
                {
                    "tenant": "test-tenant",
                    "notice": NOTICE,
                    "digest": "d" * 64,
                    "content": CONTENT,
                    "secret": "0" * 64,
                },
            )
        assert denied.value.orig.sqlstate == "P7E04"
    assert (
        await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.acknowledgement") == 0
    )


async def test_validator_truncate_drift_rejected_and_authority_history_cannot_be_rebound(
    pg_notices: TestOnlyPgNotices,
) -> None:
    p = pg_notices
    await p.prepare()
    await p.acknowledge()
    original = await p.control.fetchval("SELECT to_jsonb(a)::text FROM portal_provider_notice.authority a")
    old_receipt = await p.control.fetchval("SELECT receipt_ref FROM portal_provider_notice.acknowledgement")
    validator = p.producer.installation.authority_validator_role
    await p.control.execute(f"GRANT TRUNCATE ON portal_provider_notice.authority TO {validator}")
    with pytest.raises(ExternalCaseError):
        await p.prepare()
    with pytest.raises(ExternalCaseError):
        await p.acknowledge()
    await p.control.execute(f"SET ROLE {validator}")
    try:
        with pytest.raises(asyncpg.PostgresError) as denied:
            await p.control.execute("TRUNCATE portal_provider_notice.authority")
        assert denied.value.sqlstate == "P7E04"
    finally:
        await p.control.execute("RESET ROLE")
    assert (
        await p.control.fetchval("SELECT to_jsonb(a)::text FROM portal_provider_notice.authority a")
        == original
    )
    assert (
        await p.control.fetchval("SELECT receipt_ref FROM portal_provider_notice.acknowledgement")
        == old_receipt
    )
    await p.control.execute(f"REVOKE TRUNCATE ON portal_provider_notice.authority FROM {validator}")
    assert (await p.prepare()).provider_delivery_receipt_ref == old_receipt


async def test_all_notice_history_tables_reject_truncate_even_to_privileged_owner(
    pg_notices: TestOnlyPgNotices,
) -> None:
    p = pg_notices
    await p.prepare()
    await p.acknowledge()
    for table in NOTICE_TABLES:
        before = await p.control.fetchval(f"SELECT count(*) FROM portal_provider_notice.{table}")
        with pytest.raises(asyncpg.PostgresError) as denied:
            await p.control.execute(f"TRUNCATE portal_provider_notice.{table}")
        assert denied.value.sqlstate == "P7E04"
        assert await p.control.fetchval(f"SELECT count(*) FROM portal_provider_notice.{table}") == before


async def test_validator_exact_privilege_and_schema_function_delegation_matrix(
    pg_notices: TestOnlyPgNotices,
) -> None:
    p = pg_notices
    d = p.producer.installation
    for privilege in ("DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
        await p.control.execute(
            f"GRANT {privilege} ON portal_provider_notice.authority TO {d.authority_validator_role}"
        )
        with pytest.raises(ExternalCaseError):
            await p.prepare()
        await p.control.execute(
            f"REVOKE {privilege} ON portal_provider_notice.authority FROM {d.authority_validator_role}"
        )
    await p.control.execute(
        f"GRANT SELECT ON portal_provider_notice.authority TO {d.authority_validator_role} WITH GRANT OPTION"
    )
    with pytest.raises(ExternalCaseError):
        await p.prepare()
    await p.control.execute(
        "REVOKE GRANT OPTION FOR SELECT ON portal_provider_notice.authority "
        f"FROM {d.authority_validator_role}"
    )
    await p.control.execute(
        f"GRANT UPDATE(state) ON portal_provider_notice.authority TO {d.authority_validator_role}"
    )
    with pytest.raises(ExternalCaseError):
        await p.prepare()
    await p.control.execute(
        f"REVOKE UPDATE(state) ON portal_provider_notice.authority FROM {d.authority_validator_role}"
    )
    for role in (d.authority_validator_role, d.producer_role, d.recipient_role):
        await p.control.execute(f"GRANT USAGE ON SCHEMA portal_provider_notice TO {role} WITH GRANT OPTION")
        with pytest.raises(ExternalCaseError):
            await p.prepare()
        await p.control.execute(f"REVOKE GRANT OPTION FOR USAGE ON SCHEMA portal_provider_notice FROM {role}")
    prepare_sig = NOTICE_FUNCTIONS["prepare_notice"]
    await p.control.execute(
        f"GRANT EXECUTE ON FUNCTION portal_provider_notice.prepare_notice({prepare_sig}) "
        f"TO {d.producer_role} WITH GRANT OPTION"
    )
    with pytest.raises(ExternalCaseError):
        await p.prepare()
    await p.control.execute(
        f"REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION portal_provider_notice.prepare_notice({prepare_sig}) "
        f"FROM {d.producer_role}"
    )
    await p.control.execute(
        f"GRANT EXECUTE ON FUNCTION portal_provider_notice.prepare_notice({prepare_sig}) "
        f"TO {d.recipient_role}"
    )
    with pytest.raises(ExternalCaseError):
        await p.prepare()
    await p.control.execute(
        f"REVOKE EXECUTE ON FUNCTION portal_provider_notice.prepare_notice({prepare_sig}) "
        f"FROM {d.recipient_role}"
    )
    assert await p.control.fetchval("SELECT count(*) FROM portal_provider_notice.notice") == 0


async def test_producer_catalog_qualification_needs_no_identity_schema_usage_or_helper_execute(pg_notices):
    descriptor = pg_notices.producer.installation
    privileges = await pg_notices.control.fetchrow(
        "SELECT has_schema_privilege($1,n.oid,'USAGE') AS usage,"
        "has_function_privilege($1,$2::oid,'EXECUTE') AS execute "
        "FROM pg_catalog.pg_namespace n WHERE n.nspname='portal_communication'",
        descriptor.producer_role,
        descriptor.session_lock_oid,
    )
    assert privileges is not None
    assert privileges["usage"] is False and privileges["execute"] is False
    async with pg_notices.producer.engine.connect() as db:
        await pg_notices.producer.qualify(db)
    assert await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.notice") == 0


@pytest.mark.parametrize(
    "object_change", ["lock_name", "lock_namespace", "content_name", "content_namespace"]
)
async def test_cross_owner_pinned_oid_does_not_accept_renamed_or_moved_object(pg_notices, object_change):
    # Own disposable DB only. No source privilege, authority or helper is added.
    original = pg_notices.producer.installation
    descriptor = original
    if object_change == "lock_name":
        await pg_notices.control.execute(
            "ALTER FUNCTION portal_communication.lock_session(text) RENAME TO synthetic_wrong_lock"
        )
    elif object_change == "lock_namespace":
        await pg_notices.control.execute(
            "ALTER FUNCTION portal_communication.lock_session(text) SET SCHEMA public"
        )
    elif object_change == "content_name":
        await pg_notices.control.execute(
            "ALTER TABLE portal_communication.content RENAME TO synthetic_wrong_content"
        )
    else:
        await pg_notices.control.execute("ALTER TABLE portal_communication.content SET SCHEMA public")
    if object_change.startswith("lock_"):
        body = await pg_notices.control.fetchval(
            "SELECT pg_catalog.pg_get_functiondef($1::oid)", original.session_lock_oid
        )
        # Match the changed TestOnly object's body so rejection proves name/schema,
        # independently of the hash guard or immutable descriptor object check.
        descriptor = original.model_copy(
            update={"session_lock_sha256": hashlib.sha256(body.encode()).hexdigest()}
        )
    store = PostgresProviderNoticeStore(pg_notices.producer.engine, descriptor, role="producer")
    with pytest.raises(ExternalCaseError):
        async with store.engine.connect() as db:
            await store.qualify(db)
    assert await pg_notices.control.fetchval("SELECT count(*) FROM portal_provider_notice.notice") == 0
