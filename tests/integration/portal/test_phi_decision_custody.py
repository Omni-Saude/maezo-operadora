"""REAL PostgreSQL/mTLS integration; deliberately unrun by this source author.

CI/operator must supply MAEZO_PHI_TEST_CONFIG: absolute path to a nonsecret JSON
fixture descriptor for an EMPTY disposable PHI test database. Required fields:
host, port, database, owner_role, writer_role, ca_file, owner_certificate_file,
owner_private_key_file, writer_certificate_file, writer_private_key_file.
Both roles must already exist, use client-certificate auth and have matching client
certificates. Writer has no database CREATE, role memberships or extra data grants.
Owner owns the database/schema; database is explicitly selected, never a default.
No cloud/engine call, plaintext credential or production authorization is provided.
The current-authorization fixture is synthetic: this validates durable storage and
actual TLS/SQL bindings, not authorization-provider or actual network-zone authority.
"""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from tests.unit.gateway.human.test_decision_custody import Authorization, deployment, request

from maezo.gateway.human.decision_custody import PostgresHumanDecisionCustody
from maezo.gateway.human.decision_custody_connection import (
    DecisionCustodyConflictError,
    DecisionCustodyError,
    PhiPostgresConnection,
    install_phi_decision_schema,
)
from maezo.gateway.human.transport import HumanTLSIdentity

# `root_fixture`: needs the dedicated PHI PostgreSQL/mTLS custody fixture from ROOT; deselected
# by the global lane unless MAEZO_ROOT_FIXTURES=1 (tests/integration/conftest.py) — never
# skipped, never faked. Fenced by tests/unit/ci/test_root_fixture_deselection.py.
pytestmark = [pytest.mark.integration, pytest.mark.root_fixture, pytest.mark.asyncio]


@pytest.fixture
async def live():
    location = os.environ.get("MAEZO_PHI_TEST_CONFIG")
    if not location:
        pytest.skip("actual dedicated PHI PostgreSQL/mTLS fixture not supplied")
    path = Path(location)
    assert path.is_absolute()
    config = json.loads(await asyncio.to_thread(path.read_text))
    expected = {
        "host",
        "port",
        "database",
        "owner_role",
        "writer_role",
        "ca_file",
        "owner_certificate_file",
        "owner_private_key_file",
        "writer_certificate_file",
        "writer_private_key_file",
    }
    assert set(config) == expected
    scope = request().scope
    owner_identity = HumanTLSIdentity(
        scope,
        Path(config["ca_file"]),
        Path(config["owner_certificate_file"]),
        Path(config["owner_private_key_file"]),
    )
    writer_identity = HumanTLSIdentity(
        scope,
        Path(config["ca_file"]),
        Path(config["writer_certificate_file"]),
        Path(config["writer_private_key_file"]),
    )
    certificate = x509.load_pem_x509_certificate(writer_identity.certificate_file.read_bytes())
    descriptor = replace(
        deployment(),
        host=config["host"],
        port=config["port"],
        database=config["database"],
        writer_role=config["writer_role"],
        owner_role=config["owner_role"],
        client_certificate_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
    )
    admin = await asyncpg.connect(
        host=descriptor.host,
        port=descriptor.port,
        database=descriptor.database,
        user=descriptor.owner_role,
        password="",
        passfile="/dev/null",
        ssl=owner_identity.context(),
        timeout=10,
        command_timeout=10,
    )
    installed = False
    try:
        # Refuse to touch a preexisting schema. Only this fixture's newly created schema
        # is removed in finally; installation never runs from the production adapter.
        assert await admin.fetchval("SELECT to_regnamespace('maezo_phi_decision')") is None
        await install_phi_decision_schema(admin, writer_role=descriptor.writer_role)
        installed = True
        connector = PhiPostgresConnection(deployment=descriptor, identity=writer_identity, timeout_seconds=10)
        keys = {"key1": AESGCM(AESGCM.generate_key(bit_length=256))}
        auth = Authorization()
        store = PostgresHumanDecisionCustody(connection=connector, keys=keys, authorization=auth)
        yield store, admin, connector, keys, auth
    finally:
        if installed:
            await admin.execute("DROP SCHEMA maezo_phi_decision CASCADE")
        await admin.close()


async def test_actual_pg_parallel_retries_restart_and_conflict(live):
    store, admin, connection, keys, auth = live
    req = request()
    req = req.model_copy(
        update={"decision": req.decision.model_copy(update={"command_id": "cmd-" + uuid4().hex})}
    )
    first, second = await asyncio.gather(store.preserve(req), store.preserve(req))
    assert first.custody_ref == second.custody_ref
    assert await admin.fetchval("SELECT count(*) FROM maezo_phi_decision.human_decision") == 1
    restart = PostgresHumanDecisionCustody(connection=connection, keys=keys, authorization=auth)
    assert (await restart.resolve(first, principal=req.principal)).canonical == req.canonical
    assert (await restart.preserve(req)).custody_ref == first.custody_ref
    changed = req.model_copy(
        update={
            "decision": req.decision.model_copy(
                update={
                    "inputs": req.decision.inputs.model_copy(
                        update={"justificativa_clinica": "SYNTHETIC changed"}
                    )
                }
            )
        }
    )
    with pytest.raises(DecisionCustodyConflictError):
        await restart.preserve(changed)
    row = await admin.fetchrow("SELECT ciphertext FROM maezo_phi_decision.human_decision")
    assert req.canonical not in bytes(row["ciphertext"])


async def test_actual_pg_append_only_privileges_and_revocation(live):
    store, admin, connection, _, auth = live
    req = request()
    record = await store.preserve(req)
    for statement in (
        "UPDATE maezo_phi_decision.human_decision SET key_id='other'",
        "DELETE FROM maezo_phi_decision.human_decision",
        "TRUNCATE maezo_phi_decision.human_decision",
    ):
        with pytest.raises(DecisionCustodyError):
            async with connection.transaction() as writer:
                await writer.execute(statement)
        # Owner is guarded by triggers as well, not merely writer grants.
        with pytest.raises(asyncpg.RaiseError):
            await admin.execute(statement)
    auth.refuse_at = auth.calls + 1
    with pytest.raises(DecisionCustodyError):
        await store.resolve(record, principal=req.principal)
    assert await admin.fetchval("SELECT count(*) FROM maezo_phi_decision.human_decision") == 1
