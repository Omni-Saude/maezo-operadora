"""Builder for a COMPLETE, valid human material bundle used by the WP-J1-00 tests.

This builds deployment material (keys, certificates, DSNs, a signed manifest) — the
same shape the operator provisions — plus the out-of-band `HumanMaterialPin` the
deployment configures separately. It builds no provider and no port: the tests
exercise the production providers in `src/`, never a stand-in for one.

It lives under `tests/support/` rather than `tests/unit/` because the live-engine
proof (`tests/integration/gateway/test_human_relay_live_cib.py`) composes the real
production root over a bundle whose surfaces and DSNs point at the live CIB Seven and
its PostgreSQL, and the integration lane may only import from `tests.support`.

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
    HumanMaterialPin,
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


def build_bundle(
    *,
    directory: Path,
    overrides: dict | None = None,
    replacements: dict[str, bytes] | None = None,
    scope: dict | None = None,
    assignment_workload: str | None = None,
    engine: dict | None = None,
    window: tuple[datetime, datetime] | None = None,
    key_designations: dict[str, dict] | None = None,
) -> tuple[HumanPublicManifest, dict]:
    """Write the material files under `directory` and return (manifest, file bytes).

    `replacements` substitutes real deployment bytes (a live CA, client certificate,
    client key, signing key or DSN) BEFORE the manifest digests are computed, so the
    manifest still attests exactly what is on disk.

    `scope`, `assignment_workload`, `engine`, `window` and `key_designations` describe
    a real deployment. They are separate from `overrides` because they are also signed
    into the read admission or recomputed into a fingerprint, so patching the finished
    payload would leave the bundle internally inconsistent.

    `overrides` patches the finished manifest payload (dict values are merged one level
    deep, so a surface can be repointed without discarding its recomputed digests).
    That is how a test builds a deliberately invalid manifest.
    """
    root = Ed25519PrivateKey.generate()
    read_key = Ed25519PrivateKey.generate()
    assignment_key = Ed25519PrivateKey.generate()
    command_key = Ed25519PrivateKey.generate()

    read_certificate, read_private, read_client_spki = _certificate("portal-read-client")
    command_certificate, command_private, command_client_spki = _certificate("portal-command-client")

    scope = dict(scope or {"tenant": TENANT, "environment": ENVIRONMENT, "workload_ref": WORKLOAD})
    assignment_workload = assignment_workload or ASSIGNMENT_WORKLOAD
    engine = dict(engine or {"engine_name": ENGINE_NAME, "database_incarnation": INCARNATION})

    cursor_material = bytes(range(32, 64))
    cursor_previous = bytes(range(64, 96))
    cursor_keys = {
        "cursor-current": base64.b64encode(cursor_material).decode("ascii"),
        "cursor-previous": base64.b64encode(cursor_previous).decode("ascii"),
    }
    issued_at, valid_until = window or (
        datetime.now(UTC) - timedelta(minutes=5),
        datetime.now(UTC) + timedelta(hours=6),
    )

    admission_record = {
        "scope": scope,
        "engine_name": engine["engine_name"],
        "database_incarnation": engine["database_incarnation"],
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
    for name, raw in (replacements or {}).items():
        assert name in FILES, name
        files[name] = raw
    # Re-derive every attested digest from what is actually on disk, so a replacement
    # produces a manifest that is still exactly true about the bundle it describes.
    read_client_spki = _certificate_spki(files["read-client-certificate.pem"])
    command_client_spki = _certificate_spki(files["command-client-certificate.pem"])
    read_key = _load_signing(files["read-signing-key.pem"])
    assignment_key = _load_signing(files["assignment-signing-key.pem"])
    command_key = _load_signing(files["command-signing-key.pem"])

    payload: dict = {
        "schema": "portal-human-material.v1",
        "material_version_id": VERSION_ID,
        "scope": scope,
        "issuer": "https://identity.invalid",
        "issued_at": _iso(issued_at),
        "valid_until": _iso(valid_until),
        "root_key_fingerprint": hashlib.sha256(_spki(root)).hexdigest(),
        "engine_name": engine["engine_name"],
        "database_incarnation": engine["database_incarnation"],
        "assignment_workload_ref": assignment_workload,
        # The engine BASE url: the transport appends `/v1/commands` and
        # `/v1/receipts/...` itself, and the deployed surface is mounted at the root.
        "command_endpoint": COMMAND_ORIGIN,
        "catalog_ref": "catalog-auth-v3",
        "publisher_ref": "publisher-deployment-1",
        "keys": [
            {
                "purpose": "portal-task-read",
                "key_id": "kms-human-read-1",
                "workload_ref": scope["workload_ref"],
                "audience": "engine-read",
                "fingerprint": fingerprint_of(read_key),
                "max_envelope_seconds": "5",
            },
            {
                "purpose": "human-assignment-read",
                "key_id": "kms-human-assignment-1",
                "workload_ref": assignment_workload,
                "audience": "engine-assignment",
                "fingerprint": fingerprint_of(assignment_key),
                "max_envelope_seconds": "5",
            },
            {
                "purpose": "human-command",
                "key_id": "kms-human-command-1",
                "workload_ref": scope["workload_ref"],
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
        "revocation_snapshot": {
            "scope": scope,
            "source_ref": "revocation-observer-1",
            "revision": "4",
            "observed_at": _iso(issued_at),
            "valid_until": _iso(valid_until),
            "revoked_fingerprints": [],
        },
        "files": {
            **{name: hashlib.sha256(files[name]).hexdigest() for name in PUBLIC_FILES},
            **{name: None for name in FILES - PUBLIC_FILES},
        },
    }
    for designation in payload["keys"]:
        designation.update((key_designations or {}).get(designation["purpose"], {}))
    for key, value in (overrides or {}).items():
        current = payload.get(key)
        payload[key] = (
            {**current, **value} if isinstance(current, dict) and isinstance(value, dict) else value
        )

    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        (directory / name).write_bytes(raw)
    return parse_model(HumanPublicManifest, payload), files


def pin_for(manifest: HumanPublicManifest) -> HumanMaterialPin:
    """The out-of-band anchor an operator would configure for this exact manifest."""
    return HumanMaterialPin(
        tenant=manifest.scope.tenant,
        material_version_id=manifest.material_version_id,
        public_manifest_sha256=hashlib.sha256(manifest.canonical()).hexdigest(),
    )


def build_materials(directory: Path, *, pin: HumanMaterialPin | None = None, **kwargs) -> HumanMaterials:
    manifest, files = build_bundle(directory=directory, **kwargs)
    return verify_materials(
        pin or pin_for(manifest), manifest, files, now=datetime.now(UTC), directory=str(directory)
    )


def _certificate_spki(pem: bytes) -> str:
    certificate = x509.load_pem_x509_certificate(pem)
    return hashlib.sha256(
        certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()


def _load_signing(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    assert isinstance(key, Ed25519PrivateKey)
    return key


def _pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
