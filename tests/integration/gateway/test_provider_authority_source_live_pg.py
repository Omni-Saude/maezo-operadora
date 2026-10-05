"""ADR-0063 mechanics against own disposable TLS PostgreSQL and restricted roles.

Synthetic TestOnly publications/actors validate software mechanics, never an external
contract, professional opinion or production authority. ROOT/CI owns execution.
"""

import asyncio
import hashlib
import ipaddress
import secrets
import ssl
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import create_async_engine

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

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
DDL_PATH = (
    Path(__file__).resolve().parents[3] / "src/maezo/gateway/capabilities/provider-authority-schema.sql"
)


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise RuntimeError("TestOnly authority TLS container operation failed")
    return result.stdout.decode().strip()


def server_certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (directory / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    (directory / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return ssl.create_default_context(cafile=str(directory / "server.crt"))


@pytest.fixture
async def source_db(tmp_path):
    certificate = server_certificate(tmp_path)
    name = "maezo-provider-authority-it-" + uuid4().hex
    password = secrets.token_hex(20)
    container = None
    admin = None
    engines = []
    try:
        container = docker(
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            "maezo.test-only=provider-authority-source",
            "--publish",
            "127.0.0.1::5432",
            "--volume",
            str(tmp_path) + ":/test-tls:ro",
            "--env",
            "POSTGRES_PASSWORD=" + password,
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
                admin = await asyncpg.connect(
                    host="127.0.0.1",
                    port=port,
                    user="postgres",
                    password=password,
                    database="postgres",
                    ssl=certificate,
                )
                break
            except (asyncpg.PostgresError, OSError):
                await asyncio.sleep(0.25)
        if admin is None:
            raise RuntimeError("TestOnly TLS Postgres readiness failed")
        suffix = uuid4().hex[:12]
        roles = {
            kind: "authority_" + kind + "_" + suffix for kind in ("owner", "publisher", "validator", "reader")
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
            database_oid=await admin.fetchval("SELECT oid FROM pg_database WHERE datname=current_database()"),
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
                f"postgresql+asyncpg://{roles[kind]}:{password}@127.0.0.1:{port}/postgres",
                echo=False,
                hide_parameters=True,
                connect_args={"ssl": certificate},
            )
            engines.append(engine)
            sources[kind] = PostgresProviderAuthoritySource(engine, descriptor, publisher=kind == "publisher")
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
        if admin is not None:
            await admin.close()
        if container is not None:
            docker("rm", "--force", container)


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
