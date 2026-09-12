"""Builder for a COMPLETE, valid human material bundle used by the WP-J1-00 tests.

This builds deployment material (keys, certificates, DSNs, a signed manifest) — the
same shape the operator provisions. It builds no provider and no port: the tests
exercise the production providers in `src/`, never a stand-in for one.

Every secret here is a freshly generated test key that never leaves the process.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID

from maezo.gateway.human.production_materials import (
    FILES,
    PUBLIC_FILES,
    HumanMaterials,
    HumanPublicManifest,
    verify_materials,
)
from maezo.gateway.human.read_credentials import ReadAdmission
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.engine.profile import canonicalize

TENANT = "tenant_j1"
ENVIRONMENT = "producao"
WORKLOAD = "portal-human"
ASSIGNMENT_WORKLOAD = "portal-human-assignment"
ENGINE_NAME = "engine-cibseven-1"
INCARNATION = "incarnation-1"
VERSION_ID = "j1material" + "0" * 26
READ_ORIGIN = "https://read.engine.invalid"
COMMAND_ORIGIN = "https://commands.engine.invalid"


def _spki(key: object) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)  # type: ignore[attr-defined]


def fingerprint_of(key: Ed25519PrivateKey) -> str:
    return hashlib.sha256(_spki(key)).hexdigest()


def _certificate(common_name: str) -> tuple[bytes, bytes, str]:
    """A self-signed client certificate; the CA file is the certificate itself."""
    key = generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    pem_certificate = certificate.public_bytes(Encoding.PEM)
    pem_key = key.private_bytes(
        Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pem_certificate, pem_key, hashlib.sha256(_spki(key)).hexdigest()


def build_bundle(*, directory: Path, overrides: dict | None = None) -> tuple[HumanPublicManifest, dict]:
    """Write the material files under `directory` and return (manifest, file bytes)."""
    root = Ed25519PrivateKey.generate()
    read_key = Ed25519PrivateKey.generate()
    assignment_key = Ed25519PrivateKey.generate()
    command_key = Ed25519PrivateKey.generate()

    read_certificate, read_private, read_client_spki = _certificate("portal-read-client")
    command_certificate, command_private, command_client_spki = _certificate("portal-command-client")

    cursor_material = bytes(range(32, 64))
    cursor_previous = bytes(range(64, 96))
    cursor_keys = {
        "cursor-current": base64.b64encode(cursor_material).decode("ascii"),
        "cursor-previous": base64.b64encode(cursor_previous).decode("ascii"),
    }
    issued_at = datetime.now(UTC) - timedelta(minutes=5)
    valid_until = datetime.now(UTC) + timedelta(hours=6)

    admission_record = {
        "scope": {"tenant": TENANT, "environment": ENVIRONMENT, "workload_ref": WORKLOAD},
        "engine_name": ENGINE_NAME,
        "database_incarnation": INCARNATION,
        "read_deployment_ref": "read-deployment-1",
        "read_deployment_digest": "b" * 64,
        "runtime_admission_generation": "3",
        "capability_digest": "c" * 64,
        "observed_at": _iso(issued_at),
        "valid_until": _iso(valid_until),
        "provider_ref": "deployment-attestation",
        "provider_revision": "2",
    }
    record = parse_model(ReadAdmission, admission_record)
    signature = root.sign(canonicalize(wire(record)))
    read_admission = json.dumps(
        {
            "schema": "portal-human-read-admission.v1",
            "record": wire(record),
            "signature": base64.b64encode(signature).decode("ascii"),
        }
    ).encode()

    files: dict[str, bytes] = {
        "installation-root.der": _spki(root),
        "read-admission.json": read_admission,
        "read-ca.pem": read_certificate,
        "read-client-certificate.pem": read_certificate,
        "command-ca.pem": command_certificate,
        "command-client-certificate.pem": command_certificate,
        "read-signing-key.pem": _pem(read_key),
        "assignment-signing-key.pem": _pem(assignment_key),
        "command-signing-key.pem": _pem(command_key),
        "cursor-keys.json": json.dumps(
            {
                "schema": "portal-human-cursor-keys.v1",
                "not_before": _iso(issued_at),
                "not_after": _iso(valid_until),
                "keys": cursor_keys,
            }
        ).encode(),
        "read-client-key.pem": read_private,
        "command-client-key.pem": command_private,
        "outbox-dsn.txt": b"postgresql+asyncpg://human_outbox:pw@db.invalid:5432/maezo",
        "source-dsn.txt": b"postgresql+asyncpg://human_source:pw@db.invalid:5432/maezo",
    }
    assert set(files) == FILES

    payload: dict = {
        "schema": "portal-human-material.v1",
        "material_version_id": VERSION_ID,
        "scope": {"tenant": TENANT, "environment": ENVIRONMENT, "workload_ref": WORKLOAD},
        "issuer": "https://identity.invalid",
        "issued_at": _iso(issued_at),
        "valid_until": _iso(valid_until),
        "root_key_fingerprint": hashlib.sha256(_spki(root)).hexdigest(),
        "engine_name": ENGINE_NAME,
        "database_incarnation": INCARNATION,
        "assignment_workload_ref": ASSIGNMENT_WORKLOAD,
        "command_endpoint": COMMAND_ORIGIN + "/v1/commands",
        "catalog_ref": "catalog-auth-v3",
        "publisher_ref": "publisher-deployment-1",
        "keys": [
            {
                "purpose": "portal-task-read",
                "key_id": "kms-human-read-1",
                "workload_ref": WORKLOAD,
                "audience": "engine-read",
                "fingerprint": fingerprint_of(read_key),
                "max_envelope_seconds": "5",
            },
            {
                "purpose": "human-assignment-read",
                "key_id": "kms-human-assignment-1",
                "workload_ref": ASSIGNMENT_WORKLOAD,
                "audience": "engine-assignment",
                "fingerprint": fingerprint_of(assignment_key),
                "max_envelope_seconds": "5",
            },
            {
                "purpose": "human-command",
                "key_id": "kms-human-command-1",
                "workload_ref": WORKLOAD,
                "audience": "engine-command",
                "fingerprint": fingerprint_of(command_key),
                "max_envelope_seconds": "5",
            },
        ],
        "cursor_keys": [
            {
                "key_id": "cursor-current",
                "key_tag": "a" * 32,
                "material_sha256": hashlib.sha256(cursor_material).hexdigest(),
                "generation": "2",
                "current": True,
            },
            {
                "key_id": "cursor-previous",
                "key_tag": "b" * 32,
                "material_sha256": hashlib.sha256(cursor_previous).hexdigest(),
                "generation": "1",
                "current": False,
            },
        ],
        "read_surface": {
            "origin": READ_ORIGIN,
            "server_spki_sha256": "d" * 64,
            "ca_file": "read-ca.pem",
            "certificate_file": "read-client-certificate.pem",
            "private_key_file": "read-client-key.pem",
            "client_spki_sha256": read_client_spki,
            "timeout_seconds": "10",
        },
        "command_surface": {
            "origin": COMMAND_ORIGIN,
            "server_spki_sha256": "e" * 64,
            "ca_file": "command-ca.pem",
            "certificate_file": "command-client-certificate.pem",
            "private_key_file": "command-client-key.pem",
            "client_spki_sha256": command_client_spki,
            "timeout_seconds": "10",
        },
        "outbox_connection": {
            "host": "db.invalid",
            "port": "5432",
            "database": "maezo",
            "login": "human_outbox",
        },
        "source_connection": {
            "host": "db.invalid",
            "port": "5432",
            "database": "maezo",
            "login": "human_source",
        },
        "relay_lease_seconds": "30",
        "relay_retry_seconds": "1",
        "relay_poll_seconds": "1",
        "files": {
            **{name: hashlib.sha256(files[name]).hexdigest() for name in PUBLIC_FILES},
            **{name: None for name in FILES - PUBLIC_FILES},
        },
    }
    for key, value in (overrides or {}).items():
        payload[key] = value

    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        (directory / name).write_bytes(raw)
    return parse_model(HumanPublicManifest, payload), files


def build_materials(directory: Path, *, overrides: dict | None = None) -> HumanMaterials:
    manifest, files = build_bundle(directory=directory, overrides=overrides)
    return verify_materials(manifest, files, now=datetime.now(UTC), directory=str(directory))


def _pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
