"""Portable, self-provisioned TestOnly PostgreSQL with verified TLS and own cleanup.

Docker cp uses the local client's bytes even when the daemon runs in Colima. No
macOS bind mount or arbitrary DSN is used. Password and private key never enter
argv or diagnostic output. This fixture establishes software mechanics only.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
import ssl
import stat
import subprocess
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

import asyncpg
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, SignatureAlgorithmOID
from sqlalchemy.engine import URL

SELF_PROVISIONED_TLS_PG_CONTRACT_VERSION = "provider-self-provisioned-tls-postgres.v1"
CONTAINER_NAME_PREFIX = "maezo-provider-tls-it-"
OWNER_LABEL = "maezo.test-fixture-owner"
TOKEN_LABEL = "maezo.test-fixture-token"
CONTRACT_LABEL = "maezo.test-fixture-contract"
POSTGRES_IMAGE = "postgres:16"
_START_COMMAND = (
    "chown postgres:postgres /tmp/server.key /tmp/server.crt; chmod 600 /tmp/server.key; "
    "exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt "
    "-c ssl_key_file=/tmp/server.key"
)


class FixtureProvenanceError(RuntimeError):
    """Safe diagnostic containing no CLI output, credentials or private-key material."""


@dataclass(frozen=True)
class PublishedPostgres:
    container_id: str
    name: str
    owner: str
    token: str
    host: str
    port: int
    image: str


def validate_container_identity(
    inspected: Mapping[str, Any], *, expected_name: str, expected_owner: str, expected_token: str
) -> str:
    """Pure ownership check, also applicable after start/readiness failure."""
    container_id = inspected.get("Id")
    config = inspected.get("Config")
    if not isinstance(container_id, str) or re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise FixtureProvenanceError("TestOnly container identity invalid")
    if not isinstance(config, Mapping):
        raise FixtureProvenanceError("TestOnly container configuration absent")
    labels = config.get("Labels")
    if (
        re.fullmatch(r"[a-z][a-z0-9-]{1,60}", expected_owner) is None
        or re.fullmatch(r"[0-9a-f]{32}", expected_token) is None
        or expected_name != CONTAINER_NAME_PREFIX + expected_token
        or inspected.get("Name") != "/" + expected_name
        or not isinstance(labels, Mapping)
        or labels.get(OWNER_LABEL) != expected_owner
        or labels.get(TOKEN_LABEL) != expected_token
        or labels.get(CONTRACT_LABEL) != SELF_PROVISIONED_TLS_PG_CONTRACT_VERSION
        or labels.get("maezo.test-only") != expected_owner
        or config.get("Image") != POSTGRES_IMAGE
    ):
        raise FixtureProvenanceError("TestOnly container ownership mismatch")
    return container_id


def validate_inspected_container(
    inspected: Mapping[str, Any], *, expected_name: str, expected_owner: str, expected_token: str
) -> PublishedPostgres:
    """Validate actual Docker inspect provenance and exactly one loopback binding."""
    container_id = validate_container_identity(
        inspected, expected_name=expected_name, expected_owner=expected_owner, expected_token=expected_token
    )
    state, network = inspected.get("State"), inspected.get("NetworkSettings")
    if not isinstance(state, Mapping) or state.get("Running") is not True or not isinstance(network, Mapping):
        raise FixtureProvenanceError("TestOnly container is not running")
    ports = network.get("Ports")
    if not isinstance(ports, Mapping):
        raise FixtureProvenanceError("TestOnly PostgreSQL binding absent")
    bindings = ports.get("5432/tcp")
    if not isinstance(bindings, list) or len(bindings) != 1 or not isinstance(bindings[0], Mapping):
        raise FixtureProvenanceError("TestOnly PostgreSQL binding ambiguous")
    host, port_string = bindings[0].get("HostIp"), bindings[0].get("HostPort")
    if host != "127.0.0.1" or not isinstance(port_string, str) or not port_string.isdecimal():
        raise FixtureProvenanceError("TestOnly PostgreSQL binding is not loopback")
    port = int(port_string)
    if not 1 <= port <= 65535:
        raise FixtureProvenanceError("TestOnly PostgreSQL port invalid")
    if inspected["Config"].get("Cmd") != ["bash", "-ceu", _START_COMMAND]:
        raise FixtureProvenanceError("TestOnly PostgreSQL TLS command mismatch")
    return PublishedPostgres(
        container_id, expected_name, expected_owner, expected_token, host, port, POSTGRES_IMAGE
    )


def docker(*args: str) -> str:
    """Run own fixture CLI; never include stderr/stdout in errors."""
    operation = args[0] if args and args[0] in {"create", "cp", "start", "inspect", "rm"} else "other"
    try:
        result = subprocess.run(["docker", *args], capture_output=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise FixtureProvenanceError(f"TestOnly Docker {operation} unavailable") from None
    if result.returncode:
        raise FixtureProvenanceError(f"TestOnly Docker {operation} failed")
    return result.stdout.decode().strip()


def server_certificate(directory: Path) -> ssl.SSLContext:
    """Generate an isolated short-lived certificate and strict hostname-verifying context."""
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
    key_path = directory / "server.key"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    key_path.chmod(0o600)
    certificate_path = directory / "server.crt"
    certificate_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    context = ssl.create_default_context(cafile=str(certificate_path))
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise FixtureProvenanceError("TestOnly certificate context must verify peer and hostname")
    return context


@dataclass(frozen=True)
class DatabaseTlsServerMaterial:
    """Closed TestOnly loopback PKI input; it establishes no AUTH authority.

    Caller owns these files. The CA private key is never persisted. Loopback
    SANs do not qualify a Docker-network hostname or permit hostname overrides.
    """

    directory: Path
    owner: str
    uid: int
    ca_path: Path
    ca_sha256: str
    server_path: Path
    server_sha256: str
    key_path: Path = field(repr=False)
    key_sha256: str = field(repr=False)
    not_before: datetime
    not_after: datetime


def _protected_directory(directory: Path) -> int:
    if (
        not isinstance(directory, Path)
        or not directory.is_absolute()
        or directory.resolve(strict=True) != directory
    ):
        raise FixtureProvenanceError("TestOnly database PKI directory invalid")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(descriptor)
        raise FixtureProvenanceError("TestOnly database PKI directory invalid")
    return descriptor


def _private_write(directory_fd: int, name: str, raw: bytes) -> None:
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd
    )
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)


def database_tls_server_material(directory: Path, *, owner: str) -> DatabaseTlsServerMaterial:
    """Generate a genuine ephemeral CA and issued server in a new protected directory."""
    if (
        not isinstance(owner, str)
        or re.fullmatch(r"[a-z][a-z0-9-]{1,60}", owner) is None
        or not isinstance(directory, Path)
        or not directory.is_absolute()
        or directory.parent.resolve(strict=True) != directory.parent
    ):
        raise FixtureProvenanceError("TestOnly database PKI input invalid")
    directory.mkdir(mode=0o700)
    directory_fd = _protected_directory(directory)
    try:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(UTC).replace(microsecond=0)
        not_before, not_after = now - timedelta(minutes=1), now + timedelta(hours=2)
        ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TestOnly database CA")])
        ca = (
            x509.CertificateBuilder()
            .subject_name(ca_subject)
            .issuer_name(ca_subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
            .sign(ca_key, hashes.SHA256())
        )
        server = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_subject)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        ca_bytes = ca.public_bytes(serialization.Encoding.PEM)
        server_bytes = server.public_bytes(serialization.Encoding.PEM)
        key_bytes = server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        for name, raw in (
            ("database-ca.crt", ca_bytes),
            ("server.crt", server_bytes),
            ("server.key", key_bytes),
        ):
            _private_write(directory_fd, name, raw)
        return DatabaseTlsServerMaterial(
            directory,
            owner,
            os.getuid(),
            directory / "database-ca.crt",
            hashlib.sha256(ca_bytes).hexdigest(),
            directory / "server.crt",
            hashlib.sha256(server_bytes).hexdigest(),
            directory / "server.key",
            hashlib.sha256(key_bytes).hexdigest(),
            not_before,
            not_after,
        )
    finally:
        os.close(directory_fd)


def _pinned_file(directory_fd: int, name: str, expected_digest: str) -> bytes:
    if not isinstance(expected_digest, str) or re.fullmatch(r"[a-f0-9]{64}", expected_digest) is None:
        raise FixtureProvenanceError("TestOnly database PKI material invalid")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or not 0 < before.st_size <= 16384
        ):
            raise FixtureProvenanceError("TestOnly database PKI material invalid")
        raw = stream.read(16385)
        after = os.fstat(stream.fileno())
        if (
            (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_uid,
                before.st_mode,
                before.st_nlink,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_uid,
                after.st_mode,
                after.st_nlink,
            )
            or len(raw) != before.st_size
            or hashlib.sha256(raw).hexdigest() != expected_digest
        ):
            raise FixtureProvenanceError("TestOnly database PKI material invalid")
        return raw


def _certificate(raw: bytes) -> x509.Certificate:
    if (
        re.fullmatch(rb"-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]+-----END CERTIFICATE-----\n", raw)
        is None
    ):
        raise FixtureProvenanceError("TestOnly database PKI certificate invalid")
    return x509.load_pem_x509_certificate(raw)


def _admit_database_material(
    material: DatabaseTlsServerMaterial, *, expected_owner: str
) -> tuple[ssl.SSLContext, bytes, bytes]:
    """Return a verified byte snapshot, never reopen an admitted key to copy it."""
    try:
        if (
            type(material) is not DatabaseTlsServerMaterial
            or not isinstance(expected_owner, str)
            or re.fullmatch(r"[a-z][a-z0-9-]{1,60}", expected_owner) is None
            or material.owner != expected_owner
            or type(material.uid) is not int
            or material.uid != os.getuid()
            or material.ca_path != material.directory / "database-ca.crt"
            or material.server_path != material.directory / "server.crt"
            or material.key_path != material.directory / "server.key"
        ):
            raise FixtureProvenanceError("TestOnly database PKI input invalid")
        directory_fd = _protected_directory(material.directory)
        try:
            ca_raw = _pinned_file(directory_fd, "database-ca.crt", material.ca_sha256)
            server_raw = _pinned_file(directory_fd, "server.crt", material.server_sha256)
            key_raw = _pinned_file(directory_fd, "server.key", material.key_sha256)
        finally:
            os.close(directory_fd)
        ca, server = _certificate(ca_raw), _certificate(server_raw)
        ca_key, server_key = ca.public_key(), server.public_key()
        if (
            re.fullmatch(
                rb"-----BEGIN PRIVATE KEY-----\n[A-Za-z0-9+/=\n]+-----END PRIVATE KEY-----\n", key_raw
            )
            is None
        ):
            raise FixtureProvenanceError("TestOnly database PKI key invalid")
        private_key = serialization.load_pem_private_key(key_raw, password=None)
        if (
            not isinstance(ca_key, rsa.RSAPublicKey)
            or not isinstance(server_key, rsa.RSAPublicKey)
            or min(ca_key.key_size, server_key.key_size) < 2048
            or ca_key.public_numbers() == server_key.public_numbers()
        ):
            raise FixtureProvenanceError("TestOnly database PKI key invalid")
        if (
            not isinstance(private_key, rsa.RSAPrivateKey)
            or private_key.public_key().public_numbers() != server_key.public_numbers()
        ):
            raise FixtureProvenanceError("TestOnly database PKI key invalid")
        for cert, signer in ((ca, ca_key), (server, ca_key)):
            if (
                not isinstance(signer, rsa.RSAPublicKey)
                or cert.signature_algorithm_oid != SignatureAlgorithmOID.RSA_WITH_SHA256
            ):
                raise FixtureProvenanceError("TestOnly database PKI signature invalid")
            signer.verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), hashes.SHA256())
        ca_constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints)
        ca_usage = ca.extensions.get_extension_for_class(x509.KeyUsage)
        server_constraints = server.extensions.get_extension_for_class(x509.BasicConstraints)
        server_usage = server.extensions.get_extension_for_class(x509.KeyUsage)
        eku_extension = server.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        eku = eku_extension.value
        san_extension = server.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        san = san_extension.value
        now = datetime.now(UTC)
        if (
            ca.issuer != ca.subject
            or server.issuer != ca.subject
            or server.issuer == server.subject
            or ca_constraints.value != x509.BasicConstraints(ca=True, path_length=0)
            or not ca_constraints.critical
            or ca_usage.value != x509.KeyUsage(False, False, False, False, False, True, True, False, False)
            or not ca_usage.critical
            or server_constraints.value != x509.BasicConstraints(ca=False, path_length=None)
            or not server_constraints.critical
            or server_usage.value
            != x509.KeyUsage(True, False, True, False, False, False, False, False, False)
            or not server_usage.critical
            or eku_extension.critical
            or san_extension.critical
            or list(eku) != [ExtendedKeyUsageOID.SERVER_AUTH]
            or list(san) != [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            or len(ca.extensions) != 2
            or len(server.extensions) != 4
            or material.not_before != ca.not_valid_before_utc
            or material.not_before != server.not_valid_before_utc
            or material.not_after != ca.not_valid_after_utc
            or material.not_after != server.not_valid_after_utc
            or not material.not_before <= now < material.not_after
            or material.not_after - material.not_before > timedelta(hours=2, minutes=1)
        ):
            raise FixtureProvenanceError("TestOnly database PKI certificate invalid")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=ca_raw.decode("ascii"))
        if (
            context.verify_mode != ssl.CERT_REQUIRED
            or not context.check_hostname
            or context.get_ca_certs(binary_form=True) != [ca.public_bytes(serialization.Encoding.DER)]
        ):
            raise FixtureProvenanceError("TestOnly database PKI trust invalid")
        return context, server_raw, key_raw
    except (
        OSError,
        ValueError,
        TypeError,
        InvalidSignature,
        UnsupportedAlgorithm,
        x509.ExtensionNotFound,
        x509.DuplicateExtension,
        ssl.SSLError,
    ):
        raise FixtureProvenanceError("TestOnly database PKI material invalid") from None


def validate_database_tls_server_material(
    material: DatabaseTlsServerMaterial, *, expected_owner: str
) -> ssl.SSLContext:
    """Admit the closed PKI input, including chain, key, scope and protected files."""
    return _admit_database_material(material, expected_owner=expected_owner)[0]


def _database_server_files(files: Path, material: DatabaseTlsServerMaterial, *, owner: str) -> ssl.SSLContext:
    context, server_raw, key_raw = _admit_database_material(material, expected_owner=owner)
    directory_fd = _protected_directory(files)
    try:
        _private_write(directory_fd, "server.crt", server_raw)
        _private_write(directory_fd, "server.key", key_raw)
    finally:
        os.close(directory_fd)
    return context


@dataclass(frozen=True)
class OwnedTlsPostgres:
    published: PublishedPostgres
    certificate_path: Path
    tls_context: ssl.SSLContext = field(repr=False)
    password: str = field(repr=False)
    admin: Any = field(repr=False)

    @property
    def host(self) -> str:
        return self.published.host

    @property
    def port(self) -> int:
        return self.published.port

    def url_for(self, role: str, password: str) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=role,
            password=password,
            host=self.host,
            port=self.port,
            database="postgres",
        )


def _inspect(name: str) -> Mapping[str, Any]:
    inspected = json.loads(docker("inspect", name))
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], Mapping):
        raise FixtureProvenanceError("TestOnly container inspect ambiguous")
    return inspected[0]


@asynccontextmanager
async def owned_tls_postgres(
    directory: Path, *, owner: str, tls_server_material: DatabaseTlsServerMaterial | None = None
) -> AsyncIterator[OwnedTlsPostgres]:
    """Create→copy local cert bytes→start→inspect loopback→verified TLS; no DSN fallback."""
    if re.fullmatch(r"[a-z][a-z0-9-]{1,60}", owner) is None:
        raise FixtureProvenanceError("TestOnly owner invalid")
    token = uuid4().hex
    name = CONTAINER_NAME_PREFIX + token
    files = directory / name
    files.mkdir(mode=0o700)
    context = (
        server_certificate(files)
        if tls_server_material is None
        else _database_server_files(files, tls_server_material, owner=owner)
    )
    password = secrets.token_hex(24)
    env_file = files / "postgres.env"
    env_file.write_text("POSTGRES_PASSWORD=" + password + "\n")
    env_file.chmod(0o600)
    create_attempted = False
    admin = None
    try:
        # A timed-out create can still have taken effect. Inspect our UUID name in
        # finally and require exact ownership before cleanup in either outcome.
        create_attempted = True
        docker(
            "create",
            "--name",
            name,
            "--label",
            "maezo.test-only=" + owner,
            "--label",
            OWNER_LABEL + "=" + owner,
            "--label",
            TOKEN_LABEL + "=" + token,
            "--label",
            CONTRACT_LABEL + "=" + SELF_PROVISIONED_TLS_PG_CONTRACT_VERSION,
            "--publish",
            "127.0.0.1::5432",
            "--env-file",
            str(env_file),
            POSTGRES_IMAGE,
            "bash",
            "-ceu",
            _START_COMMAND,
        )
        docker("cp", str(files / "server.crt"), name + ":/tmp/server.crt")
        docker("cp", str(files / "server.key"), name + ":/tmp/server.key")
        docker("start", name)
        published = validate_inspected_container(
            _inspect(name), expected_name=name, expected_owner=owner, expected_token=token
        )
        for _ in range(120):
            try:
                admin = await asyncpg.connect(
                    host=published.host,
                    port=published.port,
                    user="postgres",
                    password=password,
                    database="postgres",
                    ssl=context,
                    timeout=2,
                )
                break
            except (asyncpg.PostgresError, OSError):
                await asyncio.sleep(0.25)
        if admin is None:
            raise FixtureProvenanceError("TestOnly verified TLS PostgreSQL readiness failed")
        certificate_path = (
            files / "server.crt" if tls_server_material is None else tls_server_material.ca_path
        )
        yield OwnedTlsPostgres(published, certificate_path, context, password, admin)
    finally:
        try:
            if admin is not None:
                await admin.close()
        finally:
            try:
                if create_attempted:
                    own_id = validate_container_identity(
                        _inspect(name), expected_name=name, expected_owner=owner, expected_token=token
                    )
                    docker("rm", "--force", own_id)
            finally:
                env_file.unlink(missing_ok=True)
                (files / "server.key").unlink(missing_ok=True)


# Optional, finite TestOnly supplement to NETWORK-PROFILE-PROPOSAL.md. This API
# does not qualify a receiver, engine, authority or namespace merely by creating
# Docker objects. ROOT supplies admitted image pins and observed namespace facts.
NATIVE_NETWORK_CONTRACT_VERSION = "provider-native-loopback-namespace.v1"
NATIVE_NETWORK_LABEL = "maezo.test-fixture-native-network"
NATIVE_CIB_NAME_PREFIX = "maezo-provider-native-cib-"


@dataclass(frozen=True)
class NativePostgresImagePin:
    image_id: str

    def __post_init__(self) -> None:
        _native_image_id(self.image_id)


def _native_image_id(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise FixtureProvenanceError("TestOnly native image pin invalid")


@dataclass(frozen=True)
class PublishedNativeNetwork:
    postgres: PublishedPostgres
    image_id: str
    https_port: int

    @property
    def https_origin(self) -> str:
        return f"https://127.0.0.1:{self.https_port}"

    @property
    def cib_network_arguments(self) -> tuple[str, str]:
        return ("--network", "container:" + self.postgres.container_id)


def _native_binding(ports: Mapping[str, Any], key: str) -> int:
    bindings = ports.get(key)
    if type(bindings) is not list or len(bindings) != 1 or not isinstance(bindings[0], Mapping):
        raise FixtureProvenanceError("TestOnly native binding ambiguous")
    binding = bindings[0]
    value = binding.get("HostPort")
    if (
        set(binding) != {"HostIp", "HostPort"}
        or binding.get("HostIp") != "127.0.0.1"
        or type(value) is not str
        or re.fullmatch(r"[1-9][0-9]{0,4}", value) is None
        or not 1 <= int(value) <= 65535
    ):
        raise FixtureProvenanceError("TestOnly native binding invalid")
    return int(value)


class _NativeIdentityArguments(TypedDict):
    expected_id: str
    expected_name: str
    expected_owner: str
    expected_token: str
    image_pin: NativePostgresImagePin


def _native_pg_identity(
    inspected: Mapping[str, Any],
    *,
    expected_id: str,
    expected_name: str,
    expected_owner: str,
    expected_token: str,
    image_pin: NativePostgresImagePin,
) -> None:
    if type(expected_id) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_id) is None:
        raise FixtureProvenanceError("TestOnly native PG ID invalid")
    if type(image_pin) is not NativePostgresImagePin or any(
        type(v) is not str for v in (expected_name, expected_owner, expected_token)
    ):
        raise FixtureProvenanceError("TestOnly native PG input invalid")
    _native_image_id(image_pin.image_id)
    actual_id = validate_container_identity(
        inspected, expected_name=expected_name, expected_owner=expected_owner, expected_token=expected_token
    )
    config = inspected["Config"]
    if (
        actual_id != expected_id
        or inspected.get("Image") != image_pin.image_id
        or config["Labels"].get(NATIVE_NETWORK_LABEL) != NATIVE_NETWORK_CONTRACT_VERSION
        or config.get("Cmd") != ["bash", "-ceu", _START_COMMAND]
    ):
        raise FixtureProvenanceError("TestOnly native PG provenance mismatch")


def validate_native_postgres(
    inspected: Mapping[str, Any],
    *,
    expected_id: str,
    expected_name: str,
    expected_owner: str,
    expected_token: str,
    image_pin: NativePostgresImagePin,
) -> PublishedNativeNetwork:
    """Admit exactly two actual loopback mappings and a pinned running PG."""
    _native_pg_identity(
        inspected,
        expected_id=expected_id,
        expected_name=expected_name,
        expected_owner=expected_owner,
        expected_token=expected_token,
        image_pin=image_pin,
    )
    state, network = inspected.get("State"), inspected.get("NetworkSettings")
    host_config = inspected.get("HostConfig")
    if (
        not isinstance(state, Mapping)
        or state.get("Running") is not True
        or not isinstance(network, Mapping)
        or not isinstance(host_config, Mapping)
        or host_config.get("NetworkMode") not in {"default", "bridge"}
    ):
        raise FixtureProvenanceError("TestOnly native PG network/state invalid")
    ports = network.get("Ports")
    requested = host_config.get("PortBindings")
    if (
        not isinstance(ports, Mapping)
        or set(ports) != {"5432/tcp", "8443/tcp"}
        or not isinstance(requested, Mapping)
        or set(requested) != set(ports)
    ):
        raise FixtureProvenanceError("TestOnly native PG port set invalid")
    for key in ports:
        # Docker retains the empty host port for an automatically assigned port.
        if requested[key] != [{"HostIp": "127.0.0.1", "HostPort": ""}]:
            raise FixtureProvenanceError("TestOnly native PG requested binding invalid")
    pg_port, https_port = _native_binding(ports, "5432/tcp"), _native_binding(ports, "8443/tcp")
    if pg_port == https_port:
        raise FixtureProvenanceError("TestOnly native PG host ports collide")
    return PublishedNativeNetwork(
        PublishedPostgres(
            expected_id, expected_name, expected_owner, expected_token, "127.0.0.1", pg_port, POSTGRES_IMAGE
        ),
        image_pin.image_id,
        https_port,
    )


@dataclass(frozen=True)
class NativeNamespaceConsumerPin:
    """ROOT pins one phase A or B container; this is not an approval receipt."""

    container_id: str
    name: str
    owner: str
    token: str
    phase: str
    image_reference: str
    image_id: str

    def __post_init__(self) -> None:
        if (
            type(self.container_id) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.container_id) is None
            or type(self.owner) is not str
            or re.fullmatch(r"[a-z][a-z0-9-]{1,60}", self.owner) is None
            or type(self.token) is not str
            or re.fullmatch(r"[0-9a-f]{32}", self.token) is None
            or type(self.phase) is not str
            or type(self.name) is not str
            or self.phase not in {"A", "B"}
            or self.name != NATIVE_CIB_NAME_PREFIX + self.token + "-" + self.phase.lower()
            or type(self.image_reference) is not str
            or re.fullmatch(r"[a-zA-Z0-9._/:+-]+@sha256:[0-9a-f]{64}", self.image_reference) is None
        ):
            raise FixtureProvenanceError("TestOnly native consumer pin invalid")
        _native_image_id(self.image_id)


@dataclass(frozen=True)
class NativeNamespaceObservation:
    """Actual /proc/<pid>/ns/net inode readbacks; Docker mode alone is insufficient.

    ROOT measures both PIDs/inodes in the daemon host, immediately adjacent to
    inspect. UnitOnly numbers are not runtime evidence or an alternative source.
    """

    postgres_id: str
    consumer_id: str
    postgres_pid: int
    consumer_pid: int
    postgres_net_inode: int
    consumer_net_inode: int


def _native_consumer_identity(
    inspected: Mapping[str, Any], pin: NativeNamespaceConsumerPin, pg: PublishedNativeNetwork
) -> None:
    if type(pin) is not NativeNamespaceConsumerPin or type(pg) is not PublishedNativeNetwork:
        raise FixtureProvenanceError("TestOnly native consumer input invalid")
    pin.__post_init__()
    config, host_config = inspected.get("Config"), inspected.get("HostConfig")
    if not isinstance(config, Mapping) or not isinstance(host_config, Mapping):
        raise FixtureProvenanceError("TestOnly native consumer inspect incomplete")
    labels = config.get("Labels")
    if (
        inspected.get("Id") != pin.container_id
        or inspected.get("Name") != "/" + pin.name
        or inspected.get("Image") != pin.image_id
        or config.get("Image") != pin.image_reference
        or not isinstance(labels, Mapping)
        or labels.get(OWNER_LABEL) != pin.owner
        or labels.get(TOKEN_LABEL) != pin.token
        or labels.get("maezo.test-only") != pin.owner
        or labels.get(NATIVE_NETWORK_LABEL) != NATIVE_NETWORK_CONTRACT_VERSION
        or labels.get("maezo.test-fixture-native-phase") != pin.phase
        or host_config.get("NetworkMode") != "container:" + pg.postgres.container_id
        or host_config.get("PortBindings") not in ({}, None)
        or host_config.get("PublishAllPorts") is not False
    ):
        raise FixtureProvenanceError("TestOnly native consumer provenance mismatch")
    network = inspected.get("NetworkSettings")
    if not isinstance(network, Mapping) or network.get("Ports") not in ({}, None):
        raise FixtureProvenanceError("TestOnly native consumer publishes ports")


def validate_native_namespace_consumer(
    consumer_inspected: Mapping[str, Any],
    postgres_inspected: Mapping[str, Any],
    *,
    postgres: PublishedNativeNetwork,
    consumer_pin: NativeNamespaceConsumerPin,
    observation: NativeNamespaceObservation,
) -> None:
    """Validate phase A/B sharing, running identities and measured PID/inode join."""
    if (
        type(postgres) is not PublishedNativeNetwork
        or type(postgres.postgres) is not PublishedPostgres
        or type(observation) is not NativeNamespaceObservation
        or type(observation.postgres_id) is not str
        or type(observation.consumer_id) is not str
    ):
        raise FixtureProvenanceError("TestOnly native namespace input invalid")
    fresh = validate_native_postgres(
        postgres_inspected,
        expected_id=postgres.postgres.container_id,
        expected_name=postgres.postgres.name,
        expected_owner=postgres.postgres.owner,
        expected_token=postgres.postgres.token,
        image_pin=NativePostgresImagePin(postgres.image_id),
    )
    if fresh != postgres:
        raise FixtureProvenanceError("TestOnly native PG mapping changed")
    _native_consumer_identity(consumer_inspected, consumer_pin, postgres)
    state = consumer_inspected.get("State")
    if (
        not isinstance(state, Mapping)
        or state.get("Running") is not True
        or observation.postgres_id != postgres.postgres.container_id
        or observation.consumer_id != consumer_pin.container_id
        or any(
            type(v) is not int or v <= 0
            for v in (
                observation.postgres_pid,
                observation.consumer_pid,
                observation.postgres_net_inode,
                observation.consumer_net_inode,
            )
        )
        or observation.postgres_pid != postgres_inspected["State"].get("Pid")
        or observation.consumer_pid != state.get("Pid")
        or observation.postgres_net_inode != observation.consumer_net_inode
    ):
        raise FixtureProvenanceError("TestOnly native namespace observation mismatch")


def _native_census() -> tuple[Mapping[str, Any], ...]:
    """Read every container, including stopped consumers; unknown means preserve."""
    ids = docker("ps", "--all", "--no-trunc", "--format", "{{.ID}}").splitlines()
    if len(ids) != len(set(ids)) or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in ids):
        raise FixtureProvenanceError("TestOnly native cleanup census invalid")
    return tuple(_inspect(value) for value in ids)


def ensure_native_postgres_unconsumed(
    postgres: PublishedNativeNetwork, inspected_containers: tuple[Mapping[str, Any], ...]
) -> None:
    """Conservatively preserve PG even for a stopped/short-ID/name namespace consumer."""
    seen: set[str] = set()
    for inspected in inspected_containers:
        identity, host_config = inspected.get("Id"), inspected.get("HostConfig")
        if (
            type(identity) is not str
            or re.fullmatch(r"[0-9a-f]{64}", identity) is None
            or identity in seen
            or not isinstance(host_config, Mapping)
            or type(host_config.get("NetworkMode")) is not str
        ):
            raise FixtureProvenanceError("TestOnly native cleanup census incomplete")
        seen.add(identity)
        mode = host_config["NetworkMode"]
        if mode.startswith("container:"):
            target = mode.removeprefix("container:")
            if (
                not target
                or target in {postgres.postgres.name, "/" + postgres.postgres.name}
                or postgres.postgres.container_id.startswith(target)
            ):
                raise FixtureProvenanceError("TestOnly native PG has namespace consumer")
    if postgres.postgres.container_id not in seen:
        raise FixtureProvenanceError("TestOnly native PG absent from cleanup census")


def remove_native_namespace_consumer(
    postgres: PublishedNativeNetwork,
    consumer_pin: NativeNamespaceConsumerPin,
    observation: NativeNamespaceObservation,
) -> None:
    """ROOT lane only: stop and remove one pinned consumer before removing PG."""
    validate_native_namespace_consumer(
        _inspect(consumer_pin.container_id),
        _inspect(postgres.postgres.container_id),
        postgres=postgres,
        consumer_pin=consumer_pin,
        observation=observation,
    )
    docker("stop", consumer_pin.container_id)
    stopped = _inspect(consumer_pin.container_id)
    _native_consumer_identity(stopped, consumer_pin, postgres)
    state = stopped.get("State")
    if not isinstance(state, Mapping) or state.get("Running") is not False:
        raise FixtureProvenanceError("TestOnly native consumer did not stop")
    docker("rm", consumer_pin.container_id)


def _remove_native_postgres(postgres: PublishedNativeNetwork) -> None:
    pg = postgres.postgres
    identity_arguments: _NativeIdentityArguments = dict(
        expected_id=pg.container_id,
        expected_name=pg.name,
        expected_owner=pg.owner,
        expected_token=pg.token,
        image_pin=NativePostgresImagePin(postgres.image_id),
    )
    _native_pg_identity(_inspect(pg.container_id), **identity_arguments)
    ensure_native_postgres_unconsumed(postgres, _native_census())
    _native_pg_identity(_inspect(pg.container_id), **identity_arguments)
    docker("stop", pg.container_id)
    # A newly discovered consumer or identity change preserves the stopped PG.
    ensure_native_postgres_unconsumed(postgres, _native_census())
    stopped = _inspect(pg.container_id)
    _native_pg_identity(stopped, **identity_arguments)
    if stopped.get("State", {}).get("Running") is not False:
        raise FixtureProvenanceError("TestOnly native PG did not stop")
    docker("rm", pg.container_id)


@dataclass(frozen=True)
class OwnedNativeTlsPostgres:
    database: OwnedTlsPostgres
    network: PublishedNativeNetwork


@asynccontextmanager
async def owned_native_tls_postgres(
    directory: Path,
    *,
    owner: str,
    tls_server_material: DatabaseTlsServerMaterial,
    image_pin: NativePostgresImagePin,
) -> AsyncIterator[OwnedNativeTlsPostgres]:
    """ROOT-exclusive finite native profile; consumer-first cleanup or preservation.

    This separate API leaves legacy publication and teardown unchanged. No CIB
    is created implicitly. Caller must remove all namespace consumers first.
    Docker does not provide an atomic census/removal lock: ROOT's exclusive
    lane and prohibition on concurrent creators remain mandatory.
    """
    if type(owner) is not str or re.fullmatch(r"[a-z][a-z0-9-]{1,60}", owner) is None:
        raise FixtureProvenanceError("TestOnly native owner invalid")
    if type(image_pin) is not NativePostgresImagePin:
        raise FixtureProvenanceError("TestOnly native image input invalid")
    _native_image_id(image_pin.image_id)
    token = uuid4().hex
    name = CONTAINER_NAME_PREFIX + token
    files = directory / name
    files.mkdir(mode=0o700)
    context = _database_server_files(files, tls_server_material, owner=owner)
    password = secrets.token_hex(24)
    env_file = files / "postgres.env"
    directory_fd = _protected_directory(files)
    try:
        _private_write(directory_fd, "postgres.env", ("POSTGRES_PASSWORD=" + password + "\n").encode())
    finally:
        os.close(directory_fd)
    container_id = None
    published = None
    admin = None
    removed = False
    try:
        container_id = docker(
            "create",
            "--name",
            name,
            "--label",
            "maezo.test-only=" + owner,
            "--label",
            OWNER_LABEL + "=" + owner,
            "--label",
            TOKEN_LABEL + "=" + token,
            "--label",
            CONTRACT_LABEL + "=" + SELF_PROVISIONED_TLS_PG_CONTRACT_VERSION,
            "--label",
            NATIVE_NETWORK_LABEL + "=" + NATIVE_NETWORK_CONTRACT_VERSION,
            "--publish",
            "127.0.0.1::5432",
            "--publish",
            "127.0.0.1::8443",
            "--env-file",
            str(env_file),
            POSTGRES_IMAGE,
            "bash",
            "-ceu",
            _START_COMMAND,
        )
        arguments: _NativeIdentityArguments = dict(
            expected_id=container_id,
            expected_name=name,
            expected_owner=owner,
            expected_token=token,
            image_pin=image_pin,
        )
        _native_pg_identity(_inspect(container_id), **arguments)
        docker("cp", str(files / "server.crt"), container_id + ":/tmp/server.crt")
        docker("cp", str(files / "server.key"), container_id + ":/tmp/server.key")
        docker("start", container_id)
        published = validate_native_postgres(_inspect(container_id), **arguments)
        for _ in range(120):
            try:
                admin = await asyncpg.connect(
                    host=published.postgres.host,
                    port=published.postgres.port,
                    user="postgres",
                    password=password,
                    database="postgres",
                    ssl=context,
                    timeout=2,
                )
                break
            except (asyncpg.PostgresError, OSError):
                await asyncio.sleep(0.25)
                if validate_native_postgres(_inspect(container_id), **arguments) != published:
                    raise FixtureProvenanceError("TestOnly native PG changed during readiness") from None
        if admin is None:
            raise FixtureProvenanceError("TestOnly native TLS PostgreSQL readiness failed")
        if validate_native_postgres(_inspect(container_id), **arguments) != published:
            raise FixtureProvenanceError("TestOnly native PG changed after readiness")
        yield OwnedNativeTlsPostgres(
            OwnedTlsPostgres(published.postgres, tls_server_material.ca_path, context, password, admin),
            published,
        )
    finally:
        try:
            if admin is not None:
                await admin.close()
        finally:
            if published is not None:
                _remove_native_postgres(published)
                removed = True
            # Unknown create/start outcomes are retained rather than inferred to
            # be absent. ROOT inventories them by UUID name and protected files.
            if removed:
                env_file.unlink(missing_ok=True)
                (files / "server.key").unlink(missing_ok=True)
