"""ADR-0063 mechanics against own disposable TLS PostgreSQL and restricted roles.

Synthetic TestOnly publications/actors validate software mechanics, never an external
contract, professional opinion or production authority. ROOT/CI owns execution.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from maezo.gateway.capabilities.authority_postgres import PostgresProviderAuthoritySource
from maezo.gateway.capabilities.authority_source import (
    TABLES,
    AuthoritySourceError,
    SourceBinding,
    SourceDescriptor,
    pack,
)
from maezo.gateway.capabilities.contract_authority import (
    ContractClauseGroup,
    ContractReadScope,
    ContractSnapshot,
)
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.support.provider_tls_pg import docker as docker
from tests.support.provider_tls_pg import owned_tls_postgres
from tests.support.provider_tls_pg import server_certificate as server_certificate

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
DDL_PATH = (
    Path(__file__).resolve().parents[3] / "src/maezo/gateway/capabilities/provider-authority-schema.sql"
)


@pytest.fixture
async def source_db(tmp_path):
    async with owned_tls_postgres(tmp_path, owner="provider-authority-source") as pg:
        certificate = pg.tls_context
        password = pg.password
        admin = pg.admin
        engines = []
        try:
            suffix = uuid4().hex[:12]
            roles = {
                kind: "authority_" + kind + "_" + suffix
                for kind in ("owner", "publisher", "validator", "reader")
            }
            for role in roles.values():
                await admin.execute(
                    f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}' "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
                )
            # This is own disposable database, never a shared PostgreSQL or application DB.
            await admin.execute("REVOKE TEMP ON DATABASE postgres FROM PUBLIC")
            schema = "provider_source_" + suffix
            await admin.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{roles["owner"]}"')
            await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
            await admin.execute(f'SET ROLE "{roles["owner"]}"')
            await admin.execute(f'SET search_path TO "{schema}"')
            ddl = await asyncio.to_thread(DDL_PATH.read_text)
            await admin.execute(ddl)
            await admin.execute("RESET ROLE")
            for role in roles.values():
                await admin.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            for kind in ("reader", "publisher"):
                await admin.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{roles[kind]}"')
            await admin.execute(f'GRANT INSERT ON "{schema}".evidence TO "{roles["publisher"]}"')
            await admin.execute(
                f'GRANT EXECUTE ON FUNCTION "{schema}".publish_validated(jsonb,text,text,bigint) '
                f'TO "{roles["publisher"]}"'
            )
            await admin.execute(
                f'GRANT SELECT,INSERT,UPDATE ON "{schema}".source_binding,"{schema}".authority_proof '
                f'TO "{roles["validator"]}"'
            )
            relation_pins = {
                record["relname"]: {"oid": record["oid"]}
                for record in await admin.fetch(
                    "SELECT c.relname,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=$1 AND c.relname=ANY($2::text[])",
                    schema,
                    list(TABLES),
                )
            }
            functions = {
                row["proname"]: row
                for row in await admin.fetch(
                    "SELECT p.proname,p.oid,pg_get_functiondef(p.oid) AS definition FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=$1",
                    schema,
                )
            }
            now = datetime.now(UTC)
            descriptor = SourceDescriptor(
                schema_version="provider-authority-source.v1",
                database_oid=await admin.fetchval(
                    "SELECT oid FROM pg_database WHERE datname=current_database()"
                ),
                schema_name=schema,
                schema_oid=await admin.fetchval("SELECT oid FROM pg_namespace WHERE nspname=$1", schema),
                owner_role=roles["owner"],
                publisher_role=roles["publisher"],
                validator_role=roles["validator"],
                reader_role=roles["reader"],
                relation_pins=relation_pins,
                immutable_function_oid=functions["immutable_source_record"]["oid"],
                immutable_function_sha256=hashlib.sha256(
                    functions["immutable_source_record"]["definition"].encode()
                ).hexdigest(),
                publication_function_oid=functions["publish_validated"]["oid"],
                publication_function_sha256=hashlib.sha256(
                    functions["publish_validated"]["definition"].encode()
                ).hexdigest(),
                history_function_oid=functions["protect_authority_history"]["oid"],
                history_function_sha256=hashlib.sha256(
                    functions["protect_authority_history"]["definition"].encode()
                ).hexdigest(),
                tenant_ref="TestOnly-tenant",
                legal_entity_ref="TestOnly-entity",
                source_authority_ref="TestOnly-source",
                source_contract_publication_ref="TestOnly-contract-publication",
                valid_until=now + timedelta(hours=1),
            )
            sources = {}
            for kind in ("reader", "publisher"):
                engine = create_async_engine(
                    pg.url_for(roles[kind], password),
                    echo=False,
                    hide_parameters=True,
                    connect_args={"ssl": certificate},
                )
                engines.append(engine)
                sources[kind] = PostgresProviderAuthoritySource(
                    engine, descriptor, publisher=kind == "publisher"
                )
            scope = ContractReadScope(
                tenant_ref=descriptor.tenant_ref,
                legal_entity_ref=descriptor.legal_entity_ref,
                principal_ref="TestOnly-actor",
                task_ref="TestOnly-task",
                source_authority_ref=descriptor.source_authority_ref,
                policy_revision="TestOnly-policy",
                data_classification="administrative",
                purpose_ref="TestOnly-contract",
                provider_ref="TestOnly-provider",
                contract_instrument_ref="TestOnly-instrument",
                expected_business_revision="1",
                clause_purpose="payment_prazo",
                purpose_policy_ref="TestOnly-purpose",
            )
            snapshot = ContractSnapshot(
                tenant_ref=scope.tenant_ref,
                legal_entity_ref=scope.legal_entity_ref,
                provider_ref=scope.provider_ref,
                contract_instrument_ref=scope.contract_instrument_ref,
                business_revision="1",
                source_revision_ref="TestOnly-source-revision",
                contract_status="vigente",
                admission_state="enabled",
                clause_groups=(
                    ContractClauseGroup(
                        clause_purpose="payment_prazo",
                        clause_refs=("TestOnly-payment-clause",),
                        declared_value_refs=("TestOnly-value",),
                    ),
                    ContractClauseGroup(
                        clause_purpose="tabela_valor",
                        clause_refs=("TestOnly-table-clause",),
                        declared_value_refs=("TestOnly-table",),
                    ),
                ),
                evidence_ref="TestOnly-evidence",
                declared_at=now,
                authority_receipt_ref="TestOnly-receipt",
                source_publication_ref="TestOnly-publication",
                currentness_ref="TestOnly-currentness",
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
            )
            binding = SourceBinding(
                binding_ref="TestOnly-binding",
                **{
                    k: getattr(scope, k)
                    for k in (
                        "tenant_ref",
                        "legal_entity_ref",
                        "provider_ref",
                        "principal_ref",
                        "task_ref",
                        "source_authority_ref",
                        "policy_revision",
                        "data_classification",
                        "purpose_ref",
                        "purpose_policy_ref",
                    )
                },
                clause_purposes=("payment_prazo", "tabela_valor"),
                source_contract_publication_ref=descriptor.source_contract_publication_ref,
                valid_until=now + timedelta(minutes=20),
            )
            yield dict(
                admin=admin,
                schema=schema,
                roles=roles,
                sources=sources,
                scope=scope,
                snapshot=snapshot,
                binding=binding,
            )
        finally:
            for engine in engines:
                await engine.dispose()


async def seed_validation(db, *, state="enabled", evidence_ref=None):
    # Fixture-only independent publisher mandate; no production or third-party act.
    conn = db["admin"]
    schema = db["schema"]
    binding = db["binding"]
    snapshot = db["snapshot"]
    await conn.execute(f'SET ROLE "{db["roles"]["validator"]}"')
    try:
        await conn.execute(
            f'INSERT INTO "{schema}".source_binding VALUES ($1,$2,$3,false)',
            binding.binding_ref,
            pack(binding),
            hashlib.sha256(pack(binding)).hexdigest(),
        )
        await conn.execute(
            f'INSERT INTO "{schema}".authority_proof VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)',
            "TestOnly-proof",
            binding.binding_ref,
            evidence_ref or snapshot.evidence_ref,
            snapshot.snapshot_sha256(),
            snapshot.source_revision_ref,
            snapshot.currentness_ref,
            state,
            datetime.now(UTC),
            snapshot.valid_until,
        )
    finally:
        await conn.execute("RESET ROLE")


async def admit(db):
    return await db["sources"]["publisher"].publish_validated(
        db["scope"],
        business_revision="1",
        proof_ref="TestOnly-proof",
        expected_head_revision=0,
        timeout_seconds=5,
    )


async def test_received_is_custody_not_authority_and_missing_proof_denies(source_db):
    db = source_db
    receipt = await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    assert receipt.state == "received"
    with pytest.raises(AuthoritySourceError):
        await db["sources"]["reader"].resolve(db["scope"], timeout_seconds=5)
    with pytest.raises(AuthoritySourceError):
        await admit(db)
    assert await db["admin"].fetchval(f'SELECT count(*) FROM "{db["schema"]}".publication') == 0


async def test_real_tls_roles_snapshot_replay_and_two_purpose_projection(source_db):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    results = await asyncio.gather(*(admit(db) for _ in range(6)))
    assert all(x.snapshot_key() == db["snapshot"].snapshot_key() for x in results)
    reader = db["sources"]["reader"]
    for purpose in ("payment_prazo", "tabela_valor"):
        scope = db["scope"].model_copy(update={"clause_purpose": purpose})
        observed = await reader.resolve(scope, timeout_seconds=5)
        proof = await reader.verify_snapshot(scope, observed)
        assert proof.scope.clause_purpose == purpose
        assert (await reader.check_current(scope, proof)).snapshot_sha256 == observed.snapshot_sha256()
    assert await db["admin"].fetchval(f'SELECT count(*) FROM "{db["schema"]}".publication') == 1


async def test_runtime_and_publisher_cannot_create_authority_or_mutate_history(source_db):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    await admit(db)
    for role in ("reader", "publisher"):
        await db["admin"].execute(f'SET ROLE "{db["roles"][role]}"')
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await db["admin"].execute(f"UPDATE \"{db['schema']}\".authority_proof SET state='enabled'")
        finally:
            await db["admin"].execute("RESET ROLE")
    with pytest.raises(asyncpg.PostgresError):
        await db["admin"].execute(f"UPDATE \"{db['schema']}\".evidence SET snapshot_digest='{'a' * 64}'")


async def test_revocation_and_actor_cross_scope_deny_replay_disclosure(source_db):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    await admit(db)
    reader = db["sources"]["reader"]
    with pytest.raises(AuthoritySourceError) as failure:
        await reader.resolve(
            db["scope"].model_copy(update={"principal_ref": "other-actor"}), timeout_seconds=5
        )
    assert failure.value.reason == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    await db["admin"].execute(f"UPDATE \"{db['schema']}\".authority_proof SET state='revoked'")
    with pytest.raises(AuthoritySourceError):
        await reader.resolve(db["scope"], timeout_seconds=5)
    with pytest.raises(AuthoritySourceError):
        await admit(db)


async def test_same_snapshot_key_conflicting_bytes_and_forged_result_refused(source_db):
    db = source_db
    publisher = db["sources"]["publisher"]
    await publisher.receive(db["snapshot"], timeout_seconds=5)
    forged = db["snapshot"].model_copy(update={"currentness_ref": "forged"})
    with pytest.raises(AuthoritySourceError) as failure:
        await publisher.receive(forged, timeout_seconds=5)
    assert failure.value.reason == CapabilityRefusalReason.CONTRACT_MISMATCH
    await seed_validation(db)
    await admit(db)
    with pytest.raises(AuthoritySourceError):
        await db["sources"]["reader"].verify_snapshot(db["scope"], forged)


async def test_reader_write_grant_drift_closes_source(source_db):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    await admit(db)
    await db["admin"].execute(
        f'GRANT UPDATE ON "{db["schema"]}".authority_proof TO "{db["roles"]["reader"]}"'
    )
    with pytest.raises(AuthoritySourceError):
        await db["sources"]["reader"].resolve(db["scope"], timeout_seconds=5)


async def test_table_replacement_same_name_owner_grants_does_not_match_oid(source_db):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    await admit(db)
    schema = db["schema"]
    admin = db["admin"]
    await admin.execute(f'SET ROLE "{db["roles"]["owner"]}"')
    try:
        await admin.execute(f'ALTER TABLE "{schema}".instrument_head RENAME TO previous_head')
        await admin.execute(
            f'CREATE TABLE "{schema}".instrument_head (LIKE "{schema}".previous_head INCLUDING ALL)'
        )
        for role in ("reader", "publisher"):
            await admin.execute(f'GRANT SELECT ON "{schema}".instrument_head TO "{db["roles"][role]}"')
    finally:
        await admin.execute("RESET ROLE")
    with pytest.raises(AuthoritySourceError):
        await db["sources"]["reader"].resolve(db["scope"], timeout_seconds=5)


async def test_expired_or_wrong_evidence_validation_never_creates_publication(source_db):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db, evidence_ref="other-evidence")
    with pytest.raises(AuthoritySourceError):
        await admit(db)
    assert await db["admin"].fetchval(f'SELECT count(*) FROM "{db["schema"]}".publication') == 0


@pytest.mark.parametrize(
    "table,trigger,field,revoked,active",
    [
        ("source_binding", "binding_history", "revoked", True, False),
        ("authority_proof", "proof_history", "state", "revoked", "enabled"),
    ],
)
async def test_when_false_can_corrupt_real_history_but_census_closes_source(
    source_db, table, trigger, field, revoked, active
):
    """Actual PG counterexample in TestOnly DB; no production/mandate assertion.

    Installed guard blocks reactivation. Owner replaces its definition with
    WHEN(FALSE), retaining name/function/mask/O; raw validator can then corrupt
    the source state. Reader/publisher must refuse qualification before acting.
    """
    db = source_db
    publisher = db["sources"]["publisher"]
    await publisher.receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    admin, schema = db["admin"], db["schema"]
    await admin.execute(f'SET ROLE "{db["roles"]["validator"]}"')
    try:
        await admin.execute(f'UPDATE "{schema}"."{table}" SET "{field}"=$1', revoked)
        with pytest.raises(asyncpg.PostgresError):
            await admin.execute(f'UPDATE "{schema}"."{table}" SET "{field}"=$1', active)
    finally:
        await admin.execute("RESET ROLE")
    await admin.execute(f'SET ROLE "{db["roles"]["owner"]}"')
    try:
        await admin.execute(f'DROP TRIGGER "{trigger}" ON "{schema}"."{table}"')
        await admin.execute(
            f'CREATE TRIGGER "{trigger}" BEFORE UPDATE ON "{schema}"."{table}" '
            f'FOR EACH ROW WHEN (FALSE) EXECUTE FUNCTION "{schema}".protect_authority_history()'
        )
    finally:
        await admin.execute("RESET ROLE")
    profile = await admin.fetchrow(
        "SELECT tgtype,tgenabled::text AS enabled,tgqual IS NOT NULL AS has_when "
        "FROM pg_trigger WHERE tgrelid=$1::regclass AND tgname=$2",
        f'"{schema}"."{table}"',
        trigger,
    )
    assert tuple(profile.values()) == (19, "O", True)
    await admin.execute(f'SET ROLE "{db["roles"]["validator"]}"')
    try:
        await admin.execute(f'UPDATE "{schema}"."{table}" SET "{field}"=$1', active)
    finally:
        await admin.execute("RESET ROLE")
    assert await admin.fetchval(f'SELECT "{field}" FROM "{schema}"."{table}"') == active
    # Raw source corruption is observed, never admitted as an authority result.
    for source in db["sources"].values():
        async with source.engine.connect() as connection:
            with pytest.raises(AuthoritySourceError) as failure:
                await source.qualify(connection)
        assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    with pytest.raises(AuthoritySourceError):
        await admit(db)
    with pytest.raises(AuthoritySourceError):
        await db["sources"]["reader"].resolve(db["scope"], timeout_seconds=5)
    for relation in ("publication", "instrument_head"):
        assert await admin.fetchval(f'SELECT count(*) FROM "{schema}"."{relation}"') == 0


@pytest.mark.parametrize("variant", ["args", "update_columns", "replica", "disabled", "missing", "function"])
async def test_real_trigger_profile_arguments_columns_flags_and_function_are_refused(source_db, variant):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    admin, schema = db["admin"], db["schema"]
    await admin.execute(f'SET ROLE "{db["roles"]["owner"]}"')
    try:
        if variant in {"replica", "disabled"}:
            operation = "ENABLE REPLICA" if variant == "replica" else "DISABLE"
            await admin.execute(f'ALTER TABLE "{schema}".source_binding {operation} TRIGGER binding_history')
        else:
            await admin.execute(f'DROP TRIGGER binding_history ON "{schema}".source_binding')
            if variant != "missing":
                event = "UPDATE OF revoked" if variant == "update_columns" else "UPDATE"
                function = "immutable_source_record" if variant == "function" else "protect_authority_history"
                args = "'TestOnly-extra-arg'" if variant == "args" else ""
                await admin.execute(
                    f'CREATE TRIGGER binding_history BEFORE {event} ON "{schema}".source_binding '
                    f'FOR EACH ROW EXECUTE FUNCTION "{schema}".{function}({args})'
                )
    finally:
        await admin.execute("RESET ROLE")
    with pytest.raises(AuthoritySourceError) as failure:
        await admit(db)
    assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert await admin.fetchval(f'SELECT count(*) FROM "{schema}".publication') == 0


@pytest.mark.parametrize("relation", sorted(TABLES))
async def test_extra_unregistered_trigger_on_any_owned_table_closes_source(source_db, relation):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    admin, schema = db["admin"], db["schema"]
    await admin.execute(f'SET ROLE "{db["roles"]["owner"]}"')
    try:
        await admin.execute(
            f'CREATE FUNCTION "{schema}".test_only_unregistered_trigger() RETURNS trigger '
            "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
        )
        await admin.execute(
            f'CREATE TRIGGER test_only_extra BEFORE INSERT ON "{schema}"."{relation}" '
            f'FOR EACH ROW EXECUTE FUNCTION "{schema}".test_only_unregistered_trigger()'
        )
    finally:
        await admin.execute("RESET ROLE")
    for source in db["sources"].values():
        async with source.engine.connect() as connection:
            with pytest.raises(AuthoritySourceError):
                await source.qualify(connection)
    with pytest.raises(AuthoritySourceError):
        await admit(db)
    assert await admin.fetchval(f'SELECT count(*) FROM "{schema}".publication') == 0


@pytest.mark.parametrize("kind", ["owner", "reader", "publisher", "validator"])
async def test_parameter_set_privilege_cannot_disable_source_guards(source_db, kind):
    db = source_db
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    await seed_validation(db)
    role = db["roles"][kind]
    await db["admin"].execute(f'GRANT SET ON PARAMETER session_replication_role TO "{role}"')
    assert await db["admin"].fetchval(
        "SELECT has_parameter_privilege($1,'session_replication_role','SET')", role
    )
    with pytest.raises(AuthoritySourceError) as failure:
        await admit(db)
    assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert await db["admin"].fetchval(f'SELECT count(*) FROM "{db["schema"]}".publication') == 0


async def test_replica_session_cannot_qualify_even_after_set_privilege_revoked(source_db):
    db = source_db
    role = db["roles"]["publisher"]
    await db["admin"].execute(f'GRANT SET ON PARAMETER session_replication_role TO "{role}"')
    source = db["sources"]["publisher"]
    async with source.engine.connect() as connection:
        await connection.execute(text("SET session_replication_role='replica'"))
        assert (await connection.execute(text("SHOW session_replication_role"))).scalar_one() == "replica"
        await db["admin"].execute(f'REVOKE SET ON PARAMETER session_replication_role FROM "{role}"')
        assert not await db["admin"].fetchval(
            "SELECT has_parameter_privilege($1,'session_replication_role','SET')", role
        )
        with pytest.raises(AuthoritySourceError) as failure:
            await source.qualify(connection)
        assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
        await connection.rollback()
        await connection.invalidate()  # never return a deliberately changed test session to the pool


async def test_local_session_preserves_origin_guard_semantics(source_db):
    db = source_db
    # Superuser creates the pool's initial local setting, then revokes SET; only
    # this TestOnly connection survives. PG treats origin/local guards equally.
    role = db["roles"]["publisher"]
    await db["admin"].execute(f'GRANT SET ON PARAMETER session_replication_role TO "{role}"')
    source = db["sources"]["publisher"]
    async with source.engine.connect() as connection:
        await connection.execute(text("SET session_replication_role='local'"))
        await db["admin"].execute(f'REVOKE SET ON PARAMETER session_replication_role FROM "{role}"')
        await source.qualify(connection)
        await connection.rollback()
        await connection.invalidate()


async def test_declared_fk_catalog_and_orphan_integrity_are_genuine(source_db):
    db = source_db
    admin = db["admin"]
    schema = db["schema"]
    publisher = db["sources"]["publisher"]
    await publisher.receive(db["snapshot"], timeout_seconds=5)
    rows = await admin.fetch(
        "SELECT oid,conrelid,confrelid FROM pg_constraint WHERE contype='f' AND connamespace=$1",
        publisher.descriptor.schema_oid,
    )
    assert len(rows) == 4
    ri = await admin.fetch(
        "SELECT tgtype,tgisinternal,tgconstraint FROM pg_trigger WHERE tgconstraint=ANY($1::oid[])",
        [x["oid"] for x in rows],
    )
    assert len(ri) == 16 and all(x["tgisinternal"] for x in ri)
    await admin.execute(f'SET ROLE "{db["roles"]["validator"]}"')
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await admin.execute(
                f'INSERT INTO "{schema}".authority_proof VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)',
                "TestOnly-orphan",
                "TestOnly-no-binding",
                db["snapshot"].evidence_ref,
                db["snapshot"].snapshot_sha256(),
                db["snapshot"].source_revision_ref,
                db["snapshot"].currentness_ref,
                "enabled",
                datetime.now(UTC),
                db["snapshot"].valid_until,
            )
    finally:
        await admin.execute("RESET ROLE")
    await seed_validation(db)
    await admit(db)
    assert await admin.fetchval(f'SELECT count(*) FROM "{schema}".publication') == 1


@pytest.mark.parametrize("variant", ["missing", "cascade", "deferred", "not_valid", "extra"])
async def test_altered_declared_fk_topology_never_becomes_blind_internal_admission(source_db, variant):
    db = source_db
    admin = db["admin"]
    schema = db["schema"]
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    name = await admin.fetchval(
        "SELECT quote_ident(conname) FROM pg_constraint WHERE contype='f' "
        "AND conrelid=$1::regclass AND confrelid=$2::regclass",
        f'"{schema}".authority_proof',
        f'"{schema}".source_binding',
    )
    await admin.execute(f'SET ROLE "{db["roles"]["owner"]}"')
    try:
        if variant == "extra":
            await admin.execute(
                f'ALTER TABLE "{schema}".authority_proof ADD CONSTRAINT test_only_extra_fk '
                f'FOREIGN KEY(evidence_ref) REFERENCES "{schema}".source_binding(binding_ref) NOT VALID'
            )
        else:
            await admin.execute(f'ALTER TABLE "{schema}".authority_proof DROP CONSTRAINT {name}')
            if variant != "missing":
                suffix = {
                    "cascade": "ON DELETE CASCADE",
                    "deferred": "DEFERRABLE INITIALLY DEFERRED",
                    "not_valid": "NOT VALID",
                }[variant]
                await admin.execute(
                    f'ALTER TABLE "{schema}".authority_proof ADD CONSTRAINT test_only_fk '
                    f'FOREIGN KEY(binding_ref) REFERENCES "{schema}".source_binding(binding_ref) {suffix}'
                )
    finally:
        await admin.execute("RESET ROLE")
    for source in db["sources"].values():
        async with source.engine.connect() as connection:
            with pytest.raises(AuthoritySourceError):
                await source.qualify(connection)
    assert await admin.fetchval(f'SELECT count(*) FROM "{schema}".publication') == 0


@pytest.mark.parametrize("state", ["DISABLE", "ENABLE REPLICA"])
async def test_actual_ri_trigger_disabled_or_replica_closes_source(source_db, state):
    db = source_db
    admin = db["admin"]
    schema = db["schema"]
    await db["sources"]["publisher"].receive(db["snapshot"], timeout_seconds=5)
    name = await admin.fetchval(
        "SELECT quote_ident(tgname) FROM pg_trigger WHERE tgrelid=$1::regclass "
        "AND tgisinternal AND tgtype=5 LIMIT 1",
        f'"{schema}".authority_proof',
    )
    # Only the own TestOnly superuser can change an RI trigger; no runtime grant is fabricated.
    await admin.execute(f'ALTER TABLE "{schema}".authority_proof {state} TRIGGER {name}')
    for source in db["sources"].values():
        async with source.engine.connect() as connection:
            with pytest.raises(AuthoritySourceError):
                await source.qualify(connection)
