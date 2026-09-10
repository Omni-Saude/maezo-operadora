"""Synthetic injected SQL UNIT controls; never PostgreSQL or network-zone proof."""

import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from tests.unit.gateway.human.test_durable_projection import assignment

from maezo.gateway.human.decision import AuthorizedDecision
from maezo.gateway.human.decision_custody import (
    CurrentPhiDecisionAuthorization,
    CustodyAuthorization,
    PostgresHumanDecisionCustody,
)
from maezo.gateway.human.decision_custody_connection import (
    DecisionCustodyConflictError,
    DecisionCustodyError,
    PhiDatabaseDeployment,
    PhiPostgresConnection,
)
from maezo.gateway.human.transport import HumanTLSIdentity
from maezo.portal.contracts.models import TaskDecision

PRIVATE = "SYNTHETIC PHI αã\n  unchanged ${literal} "


def request(text=PRIVATE):
    a = assignment()
    return AuthorizedDecision(
        scope=a.scope,
        principal=a.principal,
        authority_revision=7,
        binding_digest="b" * 64,
        evidence_ref="evidence-1",
        decision=TaskDecision(
            **a.command.model_dump(exclude={"operation", "expected_authority_revision"}),
            inputs=dict(
                kind="auth_decisao",
                decisao_auditor="NEGAR",
                justificativa_clinica=text,
                fundamentacao_dut=text,
                cid10_referencia="SYNTHETIC-X",
            ),
        ),
    )


def deployment():
    return PhiDatabaseDeployment(
        scope=request().scope,
        host="phi.example.test",
        port=5432,
        database="phi_test",
        writer_role="phi_writer",
        owner_role="phi_owner",
        client_certificate_sha256="a" * 64,
        active_key_id="key1",
        readable_key_ids=("key1",),
        valid_until=datetime.now(UTC) + timedelta(minutes=10),
        evidence_digest="e" * 64,
    )


class Authorization(CurrentPhiDecisionAuthorization):
    def __init__(self):
        self.scope = request().scope
        self.calls = 0
        self.refuse_at = None
        self.mismatch = False
        self.until = datetime.now(UTC) + timedelta(minutes=1)

    async def authorize(self, access):
        self.calls += 1
        if self.calls == self.refuse_at:
            raise RuntimeError(PRIVATE)
        if self.mismatch:
            access = access.model_copy(
                update={"operation": "resolve" if access.operation == "preserve" else "preserve"}
            )
        return CustodyAuthorization(access=access, valid_until=self.until)


class SqlUnitFixture:
    """Transaction outcome model for UNIT orchestration, no PostgreSQL proof."""

    def __init__(self):
        self.deployment = deployment()
        self.rows = {}
        self.lost_ack = False
        self.before_read = None

    def current(self):
        if datetime.now(UTC) >= self.deployment.valid_until:
            raise DecisionCustodyError()
        return self.deployment.valid_until

    @asynccontextmanager
    async def transaction(self):
        before = copy.deepcopy(self.rows)
        try:
            yield self
        except Exception:
            self.rows = before
            raise
        if self.lost_ack:
            self.lost_ack = False
            raise RuntimeError(PRIVATE)

    async def execute(self, sql, *args):
        assert "ON CONFLICT" in sql and "DO NOTHING" in sql
        names = (
            "tenant",
            "environment",
            "workload_ref",
            "task_id",
            "command_id",
            "principal_ref",
            "request_digest",
            "content_digest",
            "custody_ref",
            "key_id",
            "nonce",
            "ciphertext",
        )
        self.rows.setdefault(args[:5], dict(zip(names, args, strict=True)))

    async def fetchrow(self, sql, *args):
        assert "maezo_phi_decision.human_decision" in sql
        if self.before_read:
            self.before_read()
        return self.rows.get(args)


def fixture():
    db, auth = SqlUnitFixture(), Authorization()
    store = PostgresHumanDecisionCustody(
        connection=db, keys={"key1": AESGCM(AESGCM.generate_key(bit_length=256))}, authorization=auth
    )
    return store, db, auth


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [PRIVATE, PRIVATE * 2000, " e\u0301  é 😀\r\n\t "])
async def test_exact_canonical_encrypted_and_retry_stable(text, caplog):
    store, db, auth = fixture()
    req = request(text)
    original = req.canonical
    first = await store.preserve(req)
    second = await store.preserve(req)
    assert first.custody_ref == second.custody_ref
    assert len(db.rows) == 1
    row = next(iter(db.rows.values()))
    assert text.encode() not in row["ciphertext"]
    result = await store.resolve(first, principal=req.principal)
    assert result.canonical == original == req.canonical
    assert result.record.request_digest == req.request_digest
    assert text not in repr(result) + repr(store) + caplog.text
    assert auth.calls == 9


@pytest.mark.asyncio
async def test_restart_and_rotation_preserve_original_ciphertext_and_reference():
    store, db, auth = fixture()
    req = request()
    first = await store.preserve(req)
    prior = copy.deepcopy(db.rows)
    db.deployment = replace(db.deployment, active_key_id="key2", readable_key_ids=("key1", "key2"))
    restarted = PostgresHumanDecisionCustody(
        connection=db,
        keys={**store._keys, "key2": AESGCM(AESGCM.generate_key(bit_length=256))},
        authorization=auth,
    )
    assert (await restarted.preserve(req)).custody_ref == first.custody_ref
    assert (await restarted.resolve(first, principal=req.principal)).canonical == req.canonical
    assert db.rows == prior
    db.deployment = replace(db.deployment, readable_key_ids=("key2",))
    removed = PostgresHumanDecisionCustody(
        connection=db, keys={"key2": restarted._keys["key2"]}, authorization=auth
    )
    with pytest.raises(DecisionCustodyError):
        await removed.resolve(first, principal=req.principal)


@pytest.mark.asyncio
async def test_unknown_commit_requires_same_identity_then_recovers():
    store, db, _ = fixture()
    db.lost_ack = True
    with pytest.raises(DecisionCustodyError) as caught:
        await store.preserve(request())
    assert PRIVATE not in str(caught.value)
    assert len(db.rows) == 1
    reference = next(iter(db.rows.values()))["custody_ref"]
    assert (await store.preserve(request())).custody_ref == reference


@pytest.mark.asyncio
async def test_same_key_changed_complete_form_conflicts_without_overwrite():
    store, db, _ = fixture()
    await store.preserve(request())
    original = copy.deepcopy(db.rows)
    with pytest.raises(DecisionCustodyConflictError):
        await store.preserve(request(PRIVATE + " changed"))
    assert db.rows == original


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["principal_ref", "issuer", "subject", "tenant"])
async def test_resolve_rejects_cross_principal_and_tenant(field):
    store, _, _ = fixture()
    req = request()
    record = await store.preserve(req)
    changed = "https://other.example.test" if field == "issuer" else "other"
    with pytest.raises(DecisionCustodyError):
        await store.resolve(record, principal=req.principal.model_copy(update={field: changed}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["task_id", "command_id", "request_digest", "content_digest", "custody_ref"]
)
async def test_resolve_exact_binding(field):
    store, _, _ = fixture()
    req = request()
    record = await store.preserve(req)
    changed = "f" * 64 if "digest" in field else "other"
    with pytest.raises(DecisionCustodyError):
        await store.resolve(record.model_copy(update={field: changed}), principal=req.principal)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["nonce", "ciphertext", "request_digest", "principal_ref", "key_id"])
async def test_tamper_integrity(field):
    store, db, _ = fixture()
    req = request()
    record = await store.preserve(req)
    row = next(iter(db.rows.values()))
    row[field] = b"x" * 12 if field in ("nonce", "ciphertext") else "x" * 64
    with pytest.raises(DecisionCustodyError):
        await store.resolve(record, principal=req.principal)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [1, 2, 3])
@pytest.mark.parametrize("operation", ["preserve", "resolve"])
async def test_current_revocation_at_every_disclosure_boundary(phase, operation):
    store, db, auth = fixture()
    req = request()
    record = await store.preserve(req)
    auth.refuse_at = auth.calls + phase
    with pytest.raises(DecisionCustodyError):
        if operation == "preserve":
            await store.preserve(req)
        else:
            await store.resolve(record, principal=req.principal)
    assert len(db.rows) == 1


@pytest.mark.asyncio
async def test_expired_record_is_not_retention_but_requires_current_authorization():
    store, _, auth = fixture()
    req = request()
    record = await store.preserve(req)
    old = record.model_copy(update={"valid_until": datetime.now(UTC) - timedelta(days=10)})
    assert (await store.resolve(old, principal=req.principal)).canonical == req.canonical
    auth.until = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(DecisionCustodyError):
        await store.resolve(old, principal=req.principal)


@pytest.mark.asyncio
async def test_expiry_during_database_read_and_mismatched_authorization():
    store, db, auth = fixture()
    req = request()
    record = await store.preserve(req)
    db.before_read = lambda: setattr(auth, "until", datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(DecisionCustodyError):
        await store.resolve(record, principal=req.principal)
    db.before_read = None
    auth.until = datetime.now(UTC) + timedelta(minutes=1)
    auth.mismatch = True
    with pytest.raises(DecisionCustodyError):
        await store.preserve(req)


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "/tmp"),
        ("host", "x\n"),
        ("port", True),
        ("port", 0),
        ("writer_role", "phi_owner"),
        ("active_key_id", "absent"),
        ("evidence_digest", "x"),
    ],
)
def test_deployment_pins_invalid(field, value):
    with pytest.raises(DecisionCustodyError):
        replace(deployment(), **{field: value})


@pytest.mark.asyncio
async def test_real_connection_constructor_no_files_no_network(tmp_path, monkeypatch):
    identity = HumanTLSIdentity(
        deployment().scope, tmp_path / "absent", tmp_path / "absent-cert", tmp_path / "absent-key"
    )
    calls = []

    async def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("network must not occur")

    monkeypatch.setattr("maezo.gateway.human.decision_custody_connection.asyncpg.connect", forbidden)
    connection = PhiPostgresConnection(deployment=deployment(), identity=identity, timeout_seconds=1)
    with pytest.raises(DecisionCustodyError):
        async with connection.transaction():
            pytest.fail("missing certificates admitted")
    assert calls == []


def tls_files(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unit-phi-writer")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    keyfile.chmod(0o600)
    identity = HumanTLSIdentity(deployment().scope, certfile, certfile, keyfile)
    return identity, replace(deployment(), client_certificate_sha256=cert.fingerprint(hashes.SHA256()).hex())


def sql_identity():
    row = dict(database="phi_test", role="phi_writer", schema_owner="phi_owner", table_owner="phi_owner")
    row.update(
        {
            key: False
            for key in (
                "rolsuper",
                "rolbypassrls",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
                "owner_member",
                "schema_create",
                "database_create",
                "can_mutate",
                "role_memberships",
                "other_data_access",
            )
        }
    )
    row.update({key: True for key in ("ssl", "client_certificate", "usage", "can_select", "can_insert")})
    return row


class QualifiedConnectionUnitFixture:
    def __init__(self):
        self.row = sql_identity()
        self.closed = False
        self.inside = False
        self.committed = False
        self.fail_commit = False

    @asynccontextmanager
    async def transaction(self, **kwargs):
        assert kwargs == {"isolation": "read_committed"}
        self.inside = True
        yield
        self.committed = True
        if self.fail_commit:
            raise RuntimeError(PRIVATE)

    async def fetchrow(self, sql, owner):
        assert "pg_stat_ssl" in sql and "has_table_privilege" in sql
        assert owner == "phi_owner"
        return self.row

    async def close(self, **kwargs):
        self.closed = True


def connection_fixture(tmp_path, monkeypatch):
    identity, descriptor = tls_files(tmp_path)
    db, calls = QualifiedConnectionUnitFixture(), []

    async def connect(**kwargs):
        calls.append(kwargs)
        return db

    monkeypatch.setattr("maezo.gateway.human.decision_custody_connection.asyncpg.connect", connect)
    return PhiPostgresConnection(deployment=descriptor, identity=identity, timeout_seconds=1), db, calls


@pytest.mark.asyncio
async def test_tls_client_fingerprint_and_explicit_no_ambient_credentials(tmp_path, monkeypatch):
    import ssl

    connection, db, calls = connection_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("PGHOST", "attacker.example.test")
    monkeypatch.setenv("PGPASSWORD", PRIVATE)
    async with connection.transaction():
        assert db.inside and not db.committed
    assert db.committed and db.closed
    options = calls[0]
    assert options["host"] == "phi.example.test"
    assert options["password"] == "" and options["passfile"] == "/dev/null"
    assert options["ssl"].check_hostname
    assert options["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert options["ssl"].minimum_version >= ssl.TLSVersion.TLSv1_2
    assert PRIVATE not in repr(options)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", list(sql_identity()))
async def test_each_database_role_acl_tls_binding_refuses(tmp_path, monkeypatch, field):
    connection, db, _ = connection_fixture(tmp_path, monkeypatch)
    db.row[field] = not db.row[field] if type(db.row[field]) is bool else "other"
    with pytest.raises(DecisionCustodyError):
        async with connection.transaction():
            pytest.fail("mismatched database admission")
    assert db.closed and not db.committed


@pytest.mark.asyncio
async def test_wrong_certificate_pin_never_connects(tmp_path, monkeypatch):
    connection, _, calls = connection_fixture(tmp_path, monkeypatch)
    connection.deployment = replace(connection.deployment, client_certificate_sha256="f" * 64)
    with pytest.raises(DecisionCustodyError):
        async with connection.transaction():
            pytest.fail("wrong identity")
    assert calls == []


@pytest.mark.asyncio
async def test_sql_commit_error_static_and_preserves_unknown_commit(tmp_path, monkeypatch):
    connection, db, _ = connection_fixture(tmp_path, monkeypatch)
    db.fail_commit = True
    with pytest.raises(DecisionCustodyError) as caught:
        async with connection.transaction():
            pass
    assert db.committed and db.closed
    assert str(caught.value) == "decision custody unavailable"
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant", "environment", "workload_ref"])
async def test_scope_cannot_be_rebound(field):
    store, db, _ = fixture()
    req = request()
    req = req.model_copy(update={"scope": req.scope.model_copy(update={field: "other"})})
    with pytest.raises(DecisionCustodyError):
        await store.preserve(req)
    assert not db.rows


@pytest.mark.asyncio
async def test_membership_mismatch_never_reaches_storage():
    store, db, _ = fixture()
    req = request()
    req = req.model_copy(update={"principal": req.principal.model_copy(update={"membership_revision": 2})})
    with pytest.raises(DecisionCustodyError):
        await store.preserve(req)
    assert not db.rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "form,outcome",
    [
        ("auth_decisao", "NEGAR"),
        ("auth_junta", "NEGAR"),
        ("escalation", "devolvido_agente"),
        ("pagto_admissibilidade", "DEVOLVER"),
    ],
)
async def test_every_frozen_phi_form_preserved_through_actual_adapter(form, outcome):
    from tests.unit.gateway.human.test_classified_decision import configured, submit

    gateway, decision, _, capture, *_ = await configured(form, outcome)
    await submit(gateway, decision)
    req = capture.requests[0]
    db, auth = SqlUnitFixture(), Authorization()
    db.deployment = replace(db.deployment, scope=req.scope)
    auth.scope = req.scope
    store = PostgresHumanDecisionCustody(
        connection=db, keys={"key1": AESGCM(AESGCM.generate_key(bit_length=256))}, authorization=auth
    )
    record = await store.preserve(req)
    assert (await store.resolve(record, principal=req.principal)).canonical == req.canonical


@pytest.mark.asyncio
async def test_key_id_is_authenticated_even_when_rotation_keys_share_test_material():
    store, db, auth = fixture()
    req = request()
    record = await store.preserve(req)
    db.deployment = replace(db.deployment, active_key_id="key2", readable_key_ids=("key1", "key2"))
    second = PostgresHumanDecisionCustody(
        connection=db, keys={"key1": store._keys["key1"], "key2": store._keys["key1"]}, authorization=auth
    )
    next(iter(db.rows.values()))["key_id"] = "key2"
    with pytest.raises(DecisionCustodyError):
        await second.resolve(record, principal=req.principal)
