"""TestOnly PKI mechanics and mocked lifecycle, never PostgreSQL qualification."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import ssl
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from tests.support import provider_tls_pg as helper
from tests.unit.support.test_provider_tls_pg import descriptor

OWNER = "native-auth-ca"


@pytest.fixture
def material(tmp_path):
    return helper.database_tls_server_material(tmp_path.resolve() / "database-pki", owner=OWNER)


def admit(material):
    return helper.validate_database_tls_server_material(material, expected_owner=OWNER)


def repin(material, field, raw):
    path = getattr(material, field + "_path")
    path.write_bytes(raw)
    path.chmod(0o600)
    return replace(material, **{field + "_sha256": hashlib.sha256(raw).hexdigest()})


def handshake(material, hostname, client=None):
    """Exercise OpenSSL verification using MemoryBIO only, without sockets."""
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(material.server_path), str(material.key_path))
    client_context = admit(material) if client is None else client
    client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client_peer = client_context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server_peer = server_context.wrap_bio(server_in, server_out, server_side=True)
    client_done = server_done = False
    for _ in range(20):
        if not client_done:
            try:
                client_peer.do_handshake()
                client_done = True
            except ssl.SSLWantReadError:
                pass
        server_in.write(client_out.read())
        if not server_done:
            try:
                server_peer.do_handshake()
                server_done = True
            except ssl.SSLWantReadError:
                pass
        client_in.write(server_out.read())
        if client_done and server_done:
            return client_peer.getpeercert()
    raise AssertionError("In-memory TLS handshake failed to finish")


@pytest.mark.parametrize("hostname", ["localhost", "127.0.0.1"])
def test_generated_material_has_real_ca_only_trust_and_working_loopback_chain(material, hostname):
    context = admit(material)
    assert context.cert_store_stats() == {"x509": 1, "crl": 0, "x509_ca": 1}
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert handshake(material, hostname)["subjectAltName"] == (
        ("DNS", "localhost"),
        ("IP Address", "127.0.0.1"),
    )
    assert {p.name for p in material.directory.iterdir()} == {"database-ca.crt", "server.crt", "server.key"}
    assert material.directory.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in material.directory.iterdir())
    assert str(material.key_path) not in repr(material) and material.key_sha256 not in repr(material)


def test_foreign_hostname_and_unrelated_ca_fail_in_actual_tls_verification(material, tmp_path):
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(material, "foreign-host.example")
    other = helper.database_tls_server_material(tmp_path.resolve() / "other-ca", owner=OWNER)
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(material, "localhost", admit(other))


@pytest.mark.parametrize("mutation", ["owner", "uid", "bool_uid", "ca_path", "key_path", "not_after"])
def test_closed_input_rejects_foreign_owner_paths_and_unsupported_metadata(material, mutation):
    changes = {
        "owner": {"owner": "foreign-owner"},
        "uid": {"uid": material.uid + 1},
        "bool_uid": {"uid": True},
        "ca_path": {"ca_path": material.server_path},
        "key_path": {"key_path": material.directory.parent / "foreign.key"},
        "not_after": {"not_after": material.not_after + timedelta(seconds=1)},
    }
    with pytest.raises(helper.FixtureProvenanceError):
        admit(replace(material, **changes[mutation]))


@pytest.mark.parametrize("field", ["ca", "server", "key"])
@pytest.mark.parametrize("mutation", ["tamper", "mode", "symlink", "hardlink", "oversized", "empty"])
def test_unsafe_or_changed_files_fail_before_use(material, tmp_path, field, mutation):
    path = getattr(material, field + "_path")
    if mutation == "tamper":
        path.write_bytes(path.read_bytes() + b"tamper")
    elif mutation == "mode":
        path.chmod(0o644)
    elif mutation == "symlink":
        target = tmp_path / "external-file"
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)
        path.unlink()
        path.symlink_to(target)
    elif mutation == "hardlink":
        os.link(path, tmp_path / "additional-link")
    elif mutation == "oversized":
        material = repin(material, field, b"A" * 16385)
    else:
        material = repin(material, field, b"")
    with pytest.raises(helper.FixtureProvenanceError) as error:
        admit(material)
    assert str(path) not in str(error.value) and material.key_sha256 not in str(error.value)


def test_directory_permissions_and_symlink_are_not_trust(material, tmp_path):
    material.directory.chmod(0o755)
    with pytest.raises(helper.FixtureProvenanceError):
        admit(material)
    material.directory.chmod(0o700)
    link = tmp_path / "pki-link"
    link.symlink_to(material.directory, target_is_directory=True)
    with pytest.raises(helper.FixtureProvenanceError):
        admit(
            replace(
                material,
                directory=link,
                ca_path=link / "database-ca.crt",
                server_path=link / "server.crt",
                key_path=link / "server.key",
            )
        )


def test_generator_does_not_overwrite_existing_materials(material):
    original = material.key_path.read_bytes()
    with pytest.raises(FileExistsError):
        helper.database_tls_server_material(material.directory, owner=OWNER)
    assert material.key_path.read_bytes() == original


def independent_pair(material, mutation):
    """Construct a separately signed profile with one deliberate semantic defect."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = serialization.load_pem_private_key(material.key_path.read_bytes(), password=None)
    now = datetime.now(UTC).replace(microsecond=0)
    before, after = now - timedelta(minutes=1), now + timedelta(hours=1)
    if mutation == "expired":
        before, after = now - timedelta(hours=1), now - timedelta(seconds=1)
    elif mutation == "future":
        before, after = now + timedelta(minutes=1), now + timedelta(hours=1)
    elif mutation == "long_validity":
        after = now + timedelta(days=1)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Independent TestOnly CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(before)
        .not_valid_after(after)
        .add_extension(
            x509.BasicConstraints(
                ca=mutation != "ca_false", path_length=None if mutation == "ca_false" else 0
            ),
            True,
        )
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, mutation != "ca_no_sign", True, False, False),
            True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    public_key = server_key.public_key()
    if mutation == "key_mismatch":
        public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    if mutation == "wildcard_san":
        san[0] = x509.DNSName("*.example")
    elif mutation == "foreign_san":
        san.append(x509.DNSName("foreign.example"))
    elif mutation == "missing_ip":
        san = san[:1]
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(name)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(before)
        .not_valid_after(after)
        .add_extension(
            x509.BasicConstraints(ca=mutation == "leaf_ca", path_length=0 if mutation == "leaf_ca" else None),
            True,
        )
        .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    ExtendedKeyUsageOID.CLIENT_AUTH
                    if mutation == "client_eku"
                    else ExtendedKeyUsageOID.SERVER_AUTH
                ]
            ),
            False,
        )
        .add_extension(x509.SubjectAlternativeName(san), False)
        .sign(ca_key, hashes.SHA256())
    )
    material = repin(material, "ca", ca.public_bytes(serialization.Encoding.PEM))
    material = repin(material, "server", server.public_bytes(serialization.Encoding.PEM))
    return replace(material, not_before=before, not_after=after)


@pytest.mark.parametrize(
    "mutation",
    [
        "ca_false",
        "ca_no_sign",
        "leaf_ca",
        "client_eku",
        "wildcard_san",
        "foreign_san",
        "missing_ip",
        "key_mismatch",
        "expired",
        "future",
        "long_validity",
    ],
)
def test_repinning_does_not_admit_invalid_pki_semantics(material, mutation):
    material = independent_pair(material, mutation)
    with pytest.raises(helper.FixtureProvenanceError):
        admit(material)


def test_independently_issued_valid_profile_is_accepted(material):
    material = independent_pair(material, "valid")
    assert handshake(material, "localhost")["subjectAltName"]


def test_legacy_leaf_and_concatenated_ca_are_not_valid_ca_material(material, tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    helper.server_certificate(legacy)
    replacement = repin(material, "ca", (legacy / "server.crt").read_bytes())
    with pytest.raises(helper.FixtureProvenanceError):
        admit(replacement)
    material = helper.database_tls_server_material(tmp_path.resolve() / "second", owner=OWNER)
    replacement = repin(material, "ca", material.ca_path.read_bytes() * 2)
    with pytest.raises(helper.FixtureProvenanceError):
        admit(replacement)


def test_repinning_unrelated_ca_or_multiple_private_keys_is_refused(material, tmp_path):
    other = helper.database_tls_server_material(tmp_path.resolve() / "foreign-pki", owner=OWNER)
    material = repin(material, "ca", other.ca_path.read_bytes())
    with pytest.raises(helper.FixtureProvenanceError):
        admit(material)
    material = helper.database_tls_server_material(tmp_path.resolve() / "new-pki", owner=OWNER)
    material = repin(material, "key", material.key_path.read_bytes() * 2)
    with pytest.raises(helper.FixtureProvenanceError):
        admit(material)


def test_fifo_is_refused_without_open_wait(material):
    material.key_path.unlink()
    os.mkfifo(material.key_path, mode=0o600)
    with pytest.raises(helper.FixtureProvenanceError):
        admit(material)


def test_copy_consumes_admitted_byte_snapshot_not_reopened_key(material, tmp_path, monkeypatch):
    original_key = material.key_path.read_bytes()
    original_admit = helper._admit_database_material

    def admit_then_change(*args, **kwargs):
        result = original_admit(*args, **kwargs)
        material.key_path.write_bytes(b"changed-after-admission")
        return result

    monkeypatch.setattr(helper, "_admit_database_material", admit_then_change)
    copied = tmp_path.resolve() / "copied"
    copied.mkdir(mode=0o700)
    helper._database_server_files(copied, material, owner=OWNER)
    assert (copied / "server.key").read_bytes() == original_key


async def test_optional_profile_copies_issued_leaf_returns_ca_and_preserves_owned_cleanup(
    material, tmp_path, monkeypatch
):
    calls = []
    name = None
    closed = False

    def cli(*args):
        nonlocal name
        calls.append(args)
        if args[0] == "create":
            name = args[args.index("--name") + 1]
            assert "127.0.0.1::5432" in args
            assert helper.SELF_PROVISIONED_TLS_PG_CONTRACT_VERSION in " ".join(args)
            return "b" * 64
        if args[0] == "inspect":
            return json.dumps(
                [descriptor(token=name.removeprefix(helper.CONTAINER_NAME_PREFIX), owner=OWNER)]
            )
        return ""

    async def close():
        nonlocal closed
        closed = True

    async def connect(**kwargs):
        assert kwargs["ssl"].cert_store_stats()["x509_ca"] == 1
        assert kwargs["ssl"].check_hostname and kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
        return SimpleNamespace(close=close)

    monkeypatch.setattr(helper, "docker", cli)
    monkeypatch.setattr(helper.asyncpg, "connect", connect)
    async with helper.owned_tls_postgres(tmp_path, owner=OWNER, tls_server_material=material) as pg:
        assert pg.certificate_path == material.ca_path
        assert pg.certificate_path.read_bytes() != (tmp_path / name / "server.crt").read_bytes()
        assert (tmp_path / name / "server.key").read_bytes() == material.key_path.read_bytes()
    assert [c[0] for c in calls] == ["create", "cp", "cp", "start", "inspect", "inspect", "rm"]
    assert calls[-1] == ("rm", "--force", "b" * 64)
    assert closed and not (tmp_path / name / "server.key").exists()
    assert not (tmp_path / name / "postgres.env").exists()
    assert material.key_path.exists(), "Caller-owned material must not be deleted by helper cleanup"


async def test_invalid_optional_material_never_creates_container(material, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(helper, "docker", lambda *args: calls.append(args))
    material.key_path.chmod(0o644)
    with pytest.raises(helper.FixtureProvenanceError):
        async with helper.owned_tls_postgres(tmp_path, owner=OWNER, tls_server_material=material):
            pytest.fail("Invalid PKI profile was admitted")
    assert not calls
