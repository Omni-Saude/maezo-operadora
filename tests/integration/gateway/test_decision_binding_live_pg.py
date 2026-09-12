"""Actual PostgreSQL/mTLS installer proof; author does not run services.

ROOT supplies MAEZO_DECISION_BINDING_TEST_CONFIG, an absolute nonsecret JSON path
for a disposable CIB database with existing human/Q2 schema, three real deployed
process definitions and escalation_routing DMN for test-tenant. No ACT_* fixture
rows are manufactured and no engine is mocked. Config keys are listed in CONFIG.
Roles/certificates/schema already exist; installer/reader/engine roles are distinct,
nonprivileged, without memberships or schema/database CREATE. The migration owner
owns the schema and immutable tables. Existing tenant revision must be zero and
new qualification tables must be absent. This test preserves its immutable records
for ROOT evidence; disposal belongs to the fixture owner, not test cleanup.

Signers, classification/form and consumer receipts are explicitly SYNTHETIC. This
proves native storage, deployed-artifact matching, ACL/CAS and durable readback;
it does not qualify any production issuer, consumer, source freeze or activation.
"""

import asyncio
import json
import os
from pathlib import Path

import asyncpg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from tests.unit.gateway.human.test_decision_binding_qualification import NOW, SCOPE, artifact, fixture, sign

from maezo.gateway.human.decision_binding import (
    RELATIONS,
    BindingConnection,
    BindingDatabase,
    DecisionBindingInstaller,
    RelationPin,
    install_decision_qualification_schema,
)
from maezo.gateway.human.decision_binding_qualification import (
    BindingMaterial,
    BindingUnavailableError,
    SignedAuthorities,
    batch_member,
    canonical,
    sha,
)
from maezo.gateway.human.transport import HumanTLSIdentity
from maezo.portal.engine.decision import _LEGACY_BINDINGS as _BINDINGS

# `root_fixture`: needs MAEZO_DECISION_BINDING_TEST_CONFIG from ROOT; deselected by the global lane unless
# MAEZO_ROOT_FIXTURES=1 (tests/integration/conftest.py) — never skipped, never faked.
pytestmark = [pytest.mark.integration, pytest.mark.root_fixture, pytest.mark.asyncio]
CONFIG = {
    "disposable_test_database",
    "host",
    "port",
    "database",
    "schema",
    "owner_role",
    "installer_role",
    "reader_role",
    "engine_role",
    "ca_file",
    "owner_certificate_file",
    "owner_private_key_file",
    "installer_certificate_file",
    "installer_private_key_file",
    "reader_certificate_file",
    "reader_private_key_file",
    "process_definitions",
    "decision_definition_id",
}


def identity(config, role):
    return HumanTLSIdentity(
        SCOPE,
        Path(config["ca_file"]),
        Path(config[role + "_certificate_file"]),
        Path(config[role + "_private_key_file"]),
    )


def fingerprint(path):
    return x509.load_pem_x509_certificate(Path(path).read_bytes()).fingerprint(hashes.SHA256()).hex()


async def test_actual_pg_six_batch_cas_grants_and_current_authority_readback():
    location = os.environ.get("MAEZO_DECISION_BINDING_TEST_CONFIG")
    if not location:
        pytest.skip("ROOT actual disposable CIB PostgreSQL/mTLS fixture not supplied")
    path = Path(location)
    assert path.is_absolute()
    config = json.loads(await asyncio.to_thread(path.read_text))
    assert set(config) == CONFIG and config["disposable_test_database"] is True
    # No secret values or caller DSN; same explicit certificate partition as production.
    owner = await asyncpg.connect(
        host=config["host"],
        port=config["port"],
        database=config["database"],
        user=config["owner_role"],
        password="",
        passfile="/dev/null",
        ssl=identity(config, "owner").context(),
        timeout=10,
        command_timeout=10,
    )
    try:
        import re

        assert re.fullmatch(r"[a-z][a-z0-9_]{0,62}", config["schema"])
        schema = '"' + config["schema"] + '"'
        assert (
            await owner.fetchval(f"SELECT rev_ FROM {schema}.mzo_human_tenant WHERE tenant_=$1", SCOPE.tenant)
            == 0
        )
        assert (
            await owner.fetchval("SELECT to_regclass($1)", config["schema"] + ".mzo_human_decision_authority")
            is None
        )
        await install_decision_qualification_schema(
            owner,
            schema=config["schema"],
            owner_role=config["owner_role"],
            installer_role=config["installer_role"],
            reader_role=config["reader_role"],
            engine_role=config["engine_role"],
        )
        pins = []
        for name in RELATIONS:
            row = await owner.fetchrow(
                """SELECT c.oid,pg_get_userbyid(c.relowner) AS owner FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=$1 AND c.relname=$2""",
                config["schema"],
                name,
            )
            assert row is not None
            pins.append(RelationPin(name=name, oid=row["oid"], owner=row["owner"]))
        database = BindingDatabase(
            scope=SCOPE,
            installation_id="test-installation",
            engine_name="cib-seven",
            database_incarnation="fixture-incarnation",
            resource_publisher_fingerprint="e" * 64,
            host=config["host"],
            port=config["port"],
            database=config["database"],
            database_oid=await owner.fetchval("SELECT oid FROM pg_database WHERE datname=current_database()"),
            schema_name=config["schema"],
            schema_oid=await owner.fetchval(
                "SELECT oid FROM pg_namespace WHERE nspname=$1", config["schema"]
            ),
            owner_role=config["owner_role"],
            installer_role=config["installer_role"],
            reader_role=config["reader_role"],
            engine_role=config["engine_role"],
            installer_certificate_digest=fingerprint(config["installer_certificate_file"]),
            reader_certificate_digest=fingerprint(config["reader_certificate_file"]),
            relations=tuple(pins),
        )
        verifier, authorities, _, keys = fixture(installation_digest=sha(canonical(database)))
        materials = []
        for pair in _BINDINGS:
            original = fixture(pair, keys=keys)[2].material
            process_id = config["process_definitions"][pair[0]]
            rows = await owner.fetch(
                f"""SELECT p.id_,p.key_,p.version_,p.tenant_id_,b.bytes_
                FROM {schema}.act_re_procdef p JOIN {schema}.act_ge_bytearray b
                ON b.deployment_id_=p.deployment_id_ AND b.name_=p.resource_name_ WHERE p.id_=$1""",
                process_id,
            )
            assert len(rows) == 1 and rows[0]["tenant_id_"] == SCOPE.tenant and rows[0]["key_"] == pair[0]
            row = rows[0]
            process = artifact(process_id, bytes(row["bytes_"]))
            entry = original.entry.model_copy(
                update=dict(
                    process_definition_id=process_id,
                    process_definition_version=row["version_"],
                    process_definition_digest=process.digest,
                )
            )
            dmn = original.group_dmn
            if dmn is not None:
                rows = await owner.fetch(
                    f"""SELECT p.id_,p.key_,p.version_,p.tenant_id_,b.bytes_
                    FROM {schema}.act_re_decision_def p JOIN {schema}.act_ge_bytearray b
                    ON b.deployment_id_=p.deployment_id_ AND b.name_=p.resource_name_ WHERE p.id_=$1""",
                    config["decision_definition_id"],
                )
                assert (
                    len(rows) == 1
                    and rows[0]["tenant_id_"] == SCOPE.tenant
                    and rows[0]["key_"] == "escalation_routing"
                )
                row = rows[0]
                dmn = artifact(row["id_"], bytes(row["bytes_"]))
                entry = entry.model_copy(
                    update={
                        "group_domain": entry.group_domain.model_copy(
                            update=dict(
                                dmn_definition_id=row["id_"],
                                dmn_definition_version=row["version_"],
                                dmn_resource_digest=dmn.digest,
                            )
                        )
                    }
                )
            materials.append(
                BindingMaterial.model_validate(
                    original.model_copy(update=dict(entry=entry, process=process, group_dmn=dmn))
                )
            )
        batch = tuple(
            sorted(
                (batch_member(m) for m in materials),
                key=lambda m: (m.process_definition_id, m.task_definition_key),
            )
        )

        def packets(operation):
            return tuple(
                fixture(pair, material=m, keys=keys, batch=batch, operation=operation)[2]
                for pair, m in zip(_BINDINGS, materials, strict=True)
            )

        installer = DecisionBindingInstaller(
            BindingConnection(
                database=database,
                identity=identity(config, "installer"),
                verifier=verifier,
                mode="installer",
                clock=lambda: NOW,
            )
        )
        await installer.designate(authorities)
        first, second = packets("batch-a"), packets("batch-b")
        results = await asyncio.gather(
            installer.install_batch(first), installer.install_batch(second), return_exceptions=True
        )
        assert sum(not isinstance(r, BaseException) for r in results) == 1
        assert sum(isinstance(r, BindingUnavailableError) for r in results) == 1
        winner = first if not isinstance(results[0], BaseException) else second
        # Discard original ACK; fresh authenticated reconciliation must recover same complete batch.
        receipt = await installer.reconcile_batch(winner)
        assert receipt.resulting_revision == 2 and len(receipt.packet_digests) == 6
        assert (
            await owner.fetchval(
                f"SELECT count(*) FROM {schema}.mzo_human_decision_binding "
                "WHERE tenant_=$1 AND authority_rev_=2",
                SCOPE.tenant,
            )
            == 6
        )
        assert (
            await owner.fetchval(
                f"SELECT count(*) FROM {schema}.mzo_human_decision_qualification "
                "WHERE tenant_=$1 AND authority_rev_=2",
                SCOPE.tenant,
            )
            == 6
        )
        reader = BindingConnection(
            database=database,
            identity=identity(config, "reader"),
            verifier=verifier,
            mode="reader",
            clock=lambda: NOW,
        )
        async with reader.transaction() as connection:
            assert await connection.fetchval(f"SELECT count(*) FROM {schema}.mzo_human_decision_binding") == 6
        raw_reader = await asyncpg.connect(
            host=config["host"],
            port=config["port"],
            database=config["database"],
            user=config["reader_role"],
            password="",
            passfile="/dev/null",
            ssl=identity(config, "reader").context(),
            timeout=10,
            command_timeout=10,
        )
        try:
            # Not READ ONLY: the actual role grants must themselves reject mutation.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with raw_reader.transaction():
                    await raw_reader.execute(f"UPDATE {schema}.mzo_human_decision_binding SET active_=false")
                    raise AssertionError("SELECT-only reader unexpectedly acquired UPDATE")
        finally:
            await raw_reader.close()
        # A root-signed signer removal bumps the native revision and makes old qualification unusable.
        doc = authorities.document.model_copy(
            update=dict(generation=1, expected_tenant_revision=2, operation_id="remove-signers", signers=())
        )
        await installer.designate(SignedAuthorities(document=doc, signature=sign(doc, "root", keys["root"])))
        with pytest.raises(BindingUnavailableError):
            await installer.reconcile_batch(winner)
        assert (
            await owner.fetchval(
                f"SELECT count(*) FROM {schema}.mzo_human_decision_binding WHERE tenant_=$1", SCOPE.tenant
            )
            == 6
        )
    finally:
        await owner.close()
