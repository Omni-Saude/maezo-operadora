"""PW1-B real PostgreSQL source-owner transaction; all identities/approvals are synthetic.

ROOT/CI executes own disposable TLS PostgreSQL only. Isolated UUID schema/roles;
application uses its own EXECUTE-only login. Production identity/publication stays unqualified.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.integration.gateway.test_provider_authority_source_live_pg import docker, server_certificate

from maezo.gateway.human.provider_membership_administration import (
    ADMIN_ACTION,
    AdministrationBinding,
    AdministrationReason,
    ProviderAdministrationError,
    ProviderMembershipCommand,
    command_bytes,
    command_digest,
)
from maezo.gateway.human.provider_membership_administration_postgres import (
    FUNCTION_TYPES,
    RELATIONS,
    AdministrationRelationPin,
    AdministrationSourceDescriptor,
    PostgresProviderMembershipAdministration,
)
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

pytestmark = pytest.mark.integration


@dataclass
class PgSource:
    control: asyncpg.Connection
    engine: AsyncEngine
    store: PostgresProviderMembershipAdministration
    binding: AdministrationBinding
    schema: str
    writer: str
    owner: str
    publisher: str

    async def approve(self, command: ProviderMembershipCommand, *, enabled: bool = True) -> None:
        b = self.binding
        until = b.valid_until
        now = datetime.now(UTC) - timedelta(seconds=1)
        await self.control.execute(
            f"INSERT INTO {self.schema}.administrator_authority VALUES "
            "($1,$2,$3,$4,'human',$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,false)",
            b.tenant,
            b.actor_ref,
            b.actor_session_ref,
            command.command_id,
            ADMIN_ACTION,
            b.source_authority_ref,
            self.store.descriptor.installation_receipt_ref,
            b.policy_revision,
            command_digest(command),
            ["provider_case"],
            [],
            "synthetic-human-source-receipt",
            "enabled" if enabled else "received",
            now,
            until,
        )
        await self.control.execute(
            f"INSERT INTO {self.schema}.provider_relationship VALUES "
            "($1,$2,$3,$4,$5,$6,'enabled',$7,$8,false) ON CONFLICT DO NOTHING",
            b.tenant,
            command.record.issuer,
            command.record.subject,
            command.record.principal_ref,
            command.provider_ref,
            "synthetic-relationship-source-receipt",
            now,
            until,
        )


@pytest.fixture
async def pg_source(tmp_path) -> AsyncIterator[PgSource]:
    certificate = server_certificate(tmp_path)
    container = None
    control = None
    admin_password = secrets.token_hex(24)
    name = "maezo-provider-admin-it-" + uuid.uuid4().hex
    try:
        container = docker(
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            "maezo.test-only=provider-membership-administration",
            "--publish",
            "127.0.0.1::5432",
            "--volume",
            str(tmp_path) + ":/test-tls:ro",
            "--env",
            "POSTGRES_PASSWORD=" + admin_password,
            "postgres:16",
            "bash",
            "-ceu",
            "cp /test-tls/server.crt /tmp/server.crt; cp /test-tls/server.key /tmp/server.key; "
            "chown postgres:postgres /tmp/server.key /tmp/server.crt; chmod 600 /tmp/server.key; "
            "exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt "
            "-c ssl_key_file=/tmp/server.key",
        )
        port = int(docker("port", container, "5432/tcp").rsplit(":", 1)[1])
        for _ in range(120):
            try:
                control = await asyncpg.connect(
                    host="127.0.0.1",
                    port=port,
                    user="postgres",
                    password=admin_password,
                    database="postgres",
                    ssl=certificate,
                    timeout=2,
                )
                break
            except (OSError, asyncpg.PostgresError):
                await asyncio.sleep(0.25)
        if control is None:
            raise RuntimeError("TestOnly provider admin TLS PostgreSQL unavailable")
        # TEMP revocation belongs exclusively to this own disposable database.
        await control.execute("REVOKE TEMP ON DATABASE postgres FROM PUBLIC")
        async with installed_source(control, port, certificate) as source:
            yield source
    finally:
        if control is not None:
            await control.close()
        if container is not None:
            docker("rm", "--force", container)


@asynccontextmanager
async def installed_source(control, port, certificate) -> AsyncIterator[PgSource]:
    suffix = uuid.uuid4().hex[:12]
    schema, owner, writer, publisher = (f"pma_{suffix}", f"pmao_{suffix}", f"pmaw_{suffix}", f"pmap_{suffix}")
    password = secrets.token_hex(24)
    engine: AsyncEngine | None = None
    try:
        await control.execute(
            f"CREATE ROLE {owner} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS; "
            f"CREATE ROLE {writer} LOGIN PASSWORD '{password}' NOINHERIT NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS; "
            f"CREATE ROLE {publisher} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"NOREPLICATION NOBYPASSRLS; CREATE SCHEMA {schema} AUTHORIZATION {owner}"
        )
        await control.execute(f"SET ROLE {owner}")
        sql = await asyncio.to_thread(
            Path("src/maezo/gateway/human/provider-membership-administration-schema.sql").read_text
        )
        await control.execute(sql.replace("portal_provider_admin", schema))
        await control.execute("RESET ROLE")
        await control.execute(
            f"GRANT USAGE ON SCHEMA {schema} TO {writer}, {publisher}; "
            f"GRANT EXECUTE ON FUNCTION {schema}.record_provider_administration({FUNCTION_TYPES}) "
            f"TO {writer}; GRANT SELECT,INSERT,UPDATE ON {schema}.administrator_authority,"
            f"{schema}.provider_relationship TO {publisher}"
        )
        db_name, db_oid = await control.fetchrow(
            "SELECT current_database(),oid FROM pg_database WHERE datname=current_database()"
        )
        schema_oid = await control.fetchval("SELECT oid FROM pg_namespace WHERE nspname=$1", schema)
        pins = []
        for name in sorted(RELATIONS):
            oid = await control.fetchval("SELECT to_regclass($1)::oid", f"{schema}.{name}")
            pins.append(AdministrationRelationPin(name=name, oid=oid, owner=owner))
        function_oid = await control.fetchval(
            "SELECT to_regprocedure($1)::oid", f"{schema}.record_provider_administration({FUNCTION_TYPES})"
        )
        body = await control.fetchval("SELECT pg_get_functiondef($1)", function_oid)
        binding = AdministrationBinding(
            tenant="tenant-a",
            actor_ref="synthetic-human",
            actor_session_ref="synthetic-session",
            source_authority_ref="synthetic-operator-source",
            policy_revision="policy-v1",
            valid_until=datetime.now(UTC) + timedelta(minutes=10),
        )
        descriptor = AdministrationSourceDescriptor(
            schema_version="provider-membership-administration-source.v1",
            tenant=binding.tenant,
            database_name=db_name,
            database_oid=db_oid,
            schema_name=schema,
            schema_oid=schema_oid,
            owner_role=owner,
            writer_role=writer,
            authority_publisher_role=publisher,
            source_authority_ref=binding.source_authority_ref,
            installation_receipt_ref="synthetic-DBA-installation",
            valid_until=binding.valid_until,
            relations=tuple(pins),
            function_oid=function_oid,
            function_definition_digest=hashlib.sha256(body.encode()).hexdigest(),
        )
        url = URL.create(
            "postgresql+asyncpg",
            username=writer,
            password=password,
            host="127.0.0.1",
            port=port,
            database="postgres",
        )
        engine = create_async_engine(
            url,
            pool_size=5,
            max_overflow=5,
            echo=False,
            hide_parameters=True,
            connect_args={"ssl": certificate},
        )
        yield PgSource(
            control,
            engine,
            PostgresProviderMembershipAdministration(engine, descriptor),
            binding,
            schema,
            writer,
            owner,
            publisher,
        )
    finally:
        if engine is not None:
            await engine.dispose()
        await control.execute("RESET ROLE")
        await control.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        for role in (writer, publisher, owner):
            await control.execute(f"DROP ROLE IF EXISTS {role}")


def reviewed_command(
    *, command_id: str = "grant-v1", revision: int = 1, revoked: bool = False
) -> ProviderMembershipCommand:
    return ProviderMembershipCommand(
        schema_version="provider-membership-administration.v1",
        command_id=command_id,
        operation="revoke" if revoked else "grant",
        provider_ref="provider-a",
        expected_revision=revision - 1,
        record=MembershipRecord(
            tenant="tenant-a",
            issuer="https://idp.example",
            subject="subject-a",
            principal_ref="principal-a",
            revision=revision,
            audience="provider",
            memberships=(
                MembershipBinding(
                    membership_ref="provider-role-binding", roles=("provider_case",), groups=()
                ),
            ),
            subject_bindings=(SubjectBinding(kind="provider", resource_ref="provider-a"),),
            reviewed_until=datetime.now(UTC) + timedelta(minutes=5),
            revoked=revoked,
        ),
    )


async def test_real_source_receipt_atomic_cas_concurrent_replay_and_revocation(pg_source: PgSource) -> None:
    p = pg_source
    c = reviewed_command()
    await p.approve(c)
    receipts = await asyncio.gather(*(p.store.record(c, p.binding) for _ in range(5)))
    assert len({r.administrative_act_ref for r in receipts}) == 1
    assert receipts[0].proof_state == "validated" and receipts[0].application_status == "pending"
    for table in ("administrative_head", "administrative_act", "administrative_audit"):
        assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.{table}") == 1
    revoke = reviewed_command(command_id="revoke-v2", revision=2, revoked=True)
    revoke = revoke.model_copy(
        update={"record": revoke.record.model_copy(update={"reviewed_until": c.record.reviewed_until})}
    )
    await p.approve(revoke)
    # A withdrawn relationship must not prevent disabling the previously reviewed access.
    await p.control.execute(f"UPDATE {p.schema}.provider_relationship SET revoked=true,proof_state='revoked'")
    assert (await p.store.record(revoke, p.binding)).membership_revision == 2
    assert '"revoked": true' in await p.control.fetchval(
        f"SELECT payload FROM {p.schema}.administrative_head"
    )


async def test_absent_received_expired_revoked_wrong_session_and_wrong_provider_never_write(
    pg_source: PgSource,
) -> None:
    p = pg_source
    c = reviewed_command()
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding)
    await p.approve(c, enabled=False)
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding)
    await p.control.execute(
        f"UPDATE {p.schema}.administrator_authority SET proof_state='enabled',revoked=true"
    )
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding)
    await p.control.execute(
        f"UPDATE {p.schema}.administrator_authority "
        "SET revoked=false,valid_until=clock_timestamp()-interval '1 second'"
    )
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding)
    await p.control.execute(
        f"UPDATE {p.schema}.administrator_authority SET valid_until=clock_timestamp()+interval '10 minutes'"
    )
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding.model_copy(update={"actor_session_ref": "not-this-session"}))
    await p.control.execute(f"UPDATE {p.schema}.provider_relationship SET provider_ref='another-provider'")
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding)
    assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.administrative_act") == 0


async def test_writer_cannot_forge_authority_receipt_or_bypass_function(pg_source: PgSource) -> None:
    p = pg_source
    for table in RELATIONS:
        with pytest.raises(DBAPIError) as e:
            async with p.engine.begin() as db:
                await db.execute(text(f"SELECT * FROM {p.schema}.{table}"))
        assert getattr(e.value.orig, "sqlstate", None) == "42501"
        with pytest.raises(DBAPIError) as e:
            async with p.engine.begin() as db:
                await db.execute(text(f"DELETE FROM {p.schema}.{table}"))
        assert getattr(e.value.orig, "sqlstate", None) == "42501"
    # Direct callable still independently refuses absent human source approval.
    async with p.engine.begin() as db:
        outcome = (
            await db.execute(
                text(
                    f"SELECT {p.schema}.record_provider_administration("
                    ":tenant,:actor,:session,:source,:policy,:install,:until,:raw,'direct-act','direct-audit')"
                ),
                dict(
                    tenant=p.binding.tenant,
                    actor=p.binding.actor_ref,
                    session=p.binding.actor_session_ref,
                    source=p.binding.source_authority_ref,
                    policy=p.binding.policy_revision,
                    install=p.store.descriptor.installation_receipt_ref,
                    until=p.binding.valid_until,
                    raw=command_bytes(reviewed_command()).decode(),
                ),
            )
        ).scalar_one()
    assert outcome == "AUTHORITY_UNPROVEN"


async def test_source_acl_and_function_digest_drift_refused(pg_source: PgSource) -> None:
    p = pg_source
    c = reviewed_command()
    await p.approve(c)
    await p.control.execute(f"GRANT UPDATE ON {p.schema}.administrative_head TO {p.writer}")
    with pytest.raises(ProviderAdministrationError) as e:
        await p.store.record(c, p.binding)
    assert e.value.reason == AdministrationReason.SOURCE_UNAVAILABLE
    await p.control.execute(f"REVOKE UPDATE ON {p.schema}.administrative_head FROM {p.writer}")
    bad = p.store.descriptor.model_copy(update={"function_definition_digest": "b" * 64})
    with pytest.raises(ProviderAdministrationError):
        await PostgresProviderMembershipAdministration(p.engine, bad).record(c, p.binding)


async def raw_function(p, command, **changes):
    values = dict(
        tenant=p.binding.tenant,
        actor=p.binding.actor_ref,
        session=p.binding.actor_session_ref,
        source=p.binding.source_authority_ref,
        policy=p.binding.policy_revision,
        install=p.store.descriptor.installation_receipt_ref,
        until=p.binding.valid_until,
        raw=command_bytes(command).decode(),
        act="TestOnly-direct-act",
        audit="TestOnly-direct-audit",
    )
    values.update(changes)
    async with p.engine.begin() as db:
        return (
            await db.execute(
                text(
                    f"SELECT {p.schema}.record_provider_administration("
                    ":tenant,:actor,:session,:source,:policy,:install,:until,:raw,:act,:audit)"
                ),
                values,
            )
        ).scalar_one()


async def test_raw_sql_each_null_argument_and_ceiling_cannot_escape_approval(pg_source):
    p = pg_source
    c = reviewed_command()
    await p.approve(c)
    for field in (
        "tenant",
        "actor",
        "session",
        "source",
        "policy",
        "install",
        "until",
        "raw",
        "act",
        "audit",
    ):
        assert await raw_function(p, c, **{field: None}) == "AUTHORITY_UNPROVEN"
    assert (
        await raw_function(p, c, until=p.binding.valid_until + timedelta(seconds=1)) == "AUTHORITY_UNPROVEN"
    )
    for table in ("administrative_head", "administrative_act", "administrative_audit"):
        assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.{table}") == 0


async def test_effective_column_privilege_is_detected_before_source_function(pg_source):
    p = pg_source
    c = reviewed_command()
    await p.approve(c)
    columns = {
        "administrator_authority": "proof_state",
        "provider_relationship": "proof_state",
        "administrative_head": "payload",
        "administrative_act": "receipt",
        "administrative_audit": "actor_ref",
    }
    for table, column in columns.items():
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            await p.control.execute(f"GRANT {privilege}({column}) ON {p.schema}.{table} TO {p.writer}")
            # Native PG counterexample: frozen qualifier's table predicate remains false,
            # but column privilege is effective. No simulated qualifier/DB here.
            assert not await p.control.fetchval(
                "SELECT has_table_privilege($1,$2,$3)", p.writer, f"{p.schema}.{table}", privilege
            )
            assert await p.control.fetchval(
                "SELECT has_any_column_privilege($1,$2,$3)", p.writer, f"{p.schema}.{table}", privilege
            )
            with pytest.raises(ProviderAdministrationError):
                await p.store.record(c, p.binding)
            await p.control.execute(f"REVOKE {privilege}({column}) ON {p.schema}.{table} FROM {p.writer}")
    assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.administrative_act") == 0


async def test_direct_column_write_could_promote_source_but_qualifier_refuses(pg_source):
    p = pg_source
    c = reviewed_command()
    await p.approve(c, enabled=False)
    await p.control.execute(f"GRANT UPDATE(proof_state) ON {p.schema}.administrator_authority TO {p.writer}")
    async with p.engine.begin() as db:
        await db.execute(text(f"UPDATE {p.schema}.administrator_authority SET proof_state='enabled'"))
    assert (
        await p.control.fetchval(f"SELECT proof_state FROM {p.schema}.administrator_authority") == "enabled"
    )
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding)
    assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.administrative_act") == 0


async def test_cross_publisher_and_third_role_relation_or_function_acl_refused(pg_source):
    p = pg_source
    c = reviewed_command()
    await p.approve(c)
    third = "pmat_" + uuid.uuid4().hex[:12]
    await p.control.execute(f"CREATE ROLE {third} NOLOGIN NOINHERIT")
    try:
        for table in RELATIONS:
            await p.control.execute(f"GRANT SELECT ON {p.schema}.{table} TO {third}")
            with pytest.raises(ProviderAdministrationError):
                await p.store.record(c, p.binding)
            await p.control.execute(f"REVOKE SELECT ON {p.schema}.{table} FROM {third}")
        for table in ("administrative_head", "administrative_act", "administrative_audit"):
            await p.control.execute(f"GRANT SELECT ON {p.schema}.{table} TO {p.publisher}")
            with pytest.raises(ProviderAdministrationError):
                await p.store.record(c, p.binding)
            await p.control.execute(f"REVOKE SELECT ON {p.schema}.{table} FROM {p.publisher}")
        await p.control.execute(
            f"GRANT EXECUTE ON FUNCTION {p.schema}.record_provider_administration({FUNCTION_TYPES}) "
            f"TO {third}"
        )
        with pytest.raises(ProviderAdministrationError):
            await p.store.record(c, p.binding)
        await p.control.execute(
            f"REVOKE EXECUTE ON FUNCTION {p.schema}.record_provider_administration({FUNCTION_TYPES}) "
            f"FROM {third}"
        )
    finally:
        await p.control.execute(f"DROP ROLE {third}")


async def test_unsafe_role_temp_replication_or_inheritance_refused(pg_source):
    p = pg_source
    c = reviewed_command()
    await p.approve(c)
    for grant, revoke in [
        (f"ALTER ROLE {p.writer} REPLICATION", f"ALTER ROLE {p.writer} NOREPLICATION"),
        (f"ALTER ROLE {p.writer} INHERIT", f"ALTER ROLE {p.writer} NOINHERIT"),
        (
            f"GRANT TEMP ON DATABASE postgres TO {p.writer}",
            f"REVOKE TEMP ON DATABASE postgres FROM {p.writer}",
        ),
    ]:
        await p.control.execute(grant)
        with pytest.raises(ProviderAdministrationError):
            await p.store.record(c, p.binding)
        await p.control.execute(revoke)


async def test_owner_login_drift_and_writer_nologin_existing_session_are_refused(pg_source):
    p = pg_source
    c = reviewed_command()
    await p.approve(c)
    await p.control.execute(f"ALTER ROLE {p.owner} LOGIN")
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding)
    assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.administrative_act") == 0
    await p.control.execute(f"ALTER ROLE {p.owner} NOLOGIN")
    await p.store.record(c, p.binding)
    # Existing pooled sessions survive ALTER ROLE; qualifier must still enforce LOGIN.
    await p.control.execute(f"ALTER ROLE {p.writer} NOLOGIN")
    with pytest.raises(ProviderAdministrationError):
        await p.store.record(c, p.binding)
    assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.administrative_act") == 1
    await p.control.execute(f"ALTER ROLE {p.writer} LOGIN")


async def test_schema_usage_grant_option_and_role_admin_option_are_refused(pg_source):
    p = pg_source
    c = reviewed_command()
    await p.approve(c)
    for role in (p.writer, p.publisher):
        await p.control.execute(f"GRANT USAGE ON SCHEMA {p.schema} TO {role} WITH GRANT OPTION")
        with pytest.raises(ProviderAdministrationError):
            await p.store.record(c, p.binding)
        assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.administrative_act") == 0
        await p.control.execute(f"REVOKE GRANT OPTION FOR USAGE ON SCHEMA {p.schema} FROM {role}")
    group = "pmam_" + uuid.uuid4().hex[:12]
    await p.control.execute(f"CREATE ROLE {group} NOLOGIN NOINHERIT")
    try:
        await p.control.execute(f"GRANT {group} TO {p.writer} WITH ADMIN OPTION")
        with pytest.raises(ProviderAdministrationError):
            await p.store.record(c, p.binding)
        assert await p.control.fetchval(f"SELECT count(*) FROM {p.schema}.administrative_act") == 0
    finally:
        await p.control.execute(f"REVOKE {group} FROM {p.writer}")
        await p.control.execute(f"DROP ROLE {group}")
