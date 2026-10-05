"""Portable, self-provisioned TestOnly PostgreSQL with verified TLS and own cleanup.

Docker cp uses the local client's bytes even when the daemon runs in Colima. No
macOS bind mount or arbitrary DSN is used. Password and private key never enter
argv or diagnostic output. This fixture establishes software mechanics only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import secrets
import ssl
import subprocess
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
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
async def owned_tls_postgres(directory: Path, *, owner: str) -> AsyncIterator[OwnedTlsPostgres]:
    """Create→copy local cert bytes→start→inspect loopback→verified TLS; no DSN fallback."""
    if re.fullmatch(r"[a-z][a-z0-9-]{1,60}", owner) is None:
        raise FixtureProvenanceError("TestOnly owner invalid")
    token = uuid4().hex
    name = CONTAINER_NAME_PREFIX + token
    files = directory / name
    files.mkdir(mode=0o700)
    context = server_certificate(files)
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
        yield OwnedTlsPostgres(published, files / "server.crt", context, password, admin)
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
