"""Closed PHI GET profile and fixed protected material verification."""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from maezo.gateway.communications.production_config import (
    FILES,
    PRIVATE_FILES,
    PUBLIC_FILES,
    PhiProductionError,
    PhiProductionSettings,
    PhiPublicManifest,
)
from maezo.gateway.communications.production_materials import verify_materials

NOW = datetime.now(UTC)


def certificate() -> tuple[bytes, bytes, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phi-owner-client")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    public = cert.public_bytes(serialization.Encoding.PEM)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return public, private, hashlib.sha256(spki).hexdigest()


def fixture():
    cert, key, client_spki = certificate()
    files = {
        "identity-reader-dsn.txt": b"postgresql+asyncpg://identity_reader:secret@db.test:5432/phi",
        "content-reader-dsn.txt": b"postgresql+asyncpg://content_reader:secret@db.test:5432/phi",
        "authority-reader-dsn.txt": b"postgresql+asyncpg://authority_reader:secret@db.test:5432/phi",
        "identity-owner-client-key.pem": key,
        "content-keys.json": json.dumps(
            {"content-key-1": base64.b64encode(b"k" * 32).decode()}, separators=(",", ":")
        ).encode(),
        "identity-reader-ca.pem": b"IDENTITY CA",
        "content-reader-ca.pem": b"CONTENT CA",
        "authority-reader-ca.pem": b"AUTHORITY CA",
        "identity-owner-ca.pem": b"OWNER CA",
        "identity-owner-client-certificate.pem": cert,
    }
    assert set(files) == FILES
    objects = []
    for index, name in enumerate(
        (
            "public.portal_sessions",
            "public.portal_memberships",
            "portal_communication.content",
            "portal_communication.message",
            "portal_communication.intended_recipient",
            "portal_communication.inbox",
            "portal_communication.authority_head",
            "portal_communication.authority_publication",
        ),
        start=10,
    ):
        schema, relation = name.split(".")
        objects.append(
            {
                "schema_name": schema,
                "name": relation,
                "oid": index,
                "owner": "phi_owner",
                "kind": "r",
                "definition_sha256": None,
            }
        )

    def reader(login, dsn, ca, names):
        return {
            "login": login,
            "host": "db.test",
            "port": 5432,
            "tls_server_name": "db.test",
            "dsn_file": dsn,
            "ca_file": ca,
            "objects": tuple(sorted(names)),
            "privileges": ("SELECT",),
        }

    manifest = PhiPublicManifest.model_validate(
        {
            "schema": "portal-phi-communications-material.v1",
            "profile": "content-read",
            "material_version_id": "version-0000000000000000000000000",
            "scope": {"tenant": "tenant", "environment": "production"},
            "issuer": "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool",
            "public_origin": "https://portal.example.test",
            "portal_settings_ref": "portal-settings-production",
            "portal_settings_digest": "a" * 64,
            "issued_at": NOW - timedelta(minutes=1),
            "valid_until": NOW + timedelta(minutes=5),
            "registered_database": {
                "identity_source_ref": "identity-source",
                "system_ref": "123456789",
                "database_name": "phi",
                "database_oid": 42,
                "database_incarnation": "database-incarnation",
                "registration_receipt_ref": "registration-receipt",
                "registration_digest": "b" * 64,
                "objects": tuple(objects),
            },
            "identity_reader": reader(
                "identity_reader",
                "identity-reader-dsn.txt",
                "identity-reader-ca.pem",
                ("public.portal_memberships", "public.portal_sessions"),
            ),
            "content_reader": reader(
                "content_reader",
                "content-reader-dsn.txt",
                "content-reader-ca.pem",
                (
                    "portal_communication.content",
                    "portal_communication.inbox",
                    "portal_communication.intended_recipient",
                    "portal_communication.message",
                ),
            ),
            "authority_reader": reader(
                "authority_reader",
                "authority-reader-dsn.txt",
                "authority-reader-ca.pem",
                (
                    "portal_communication.authority_head",
                    "portal_communication.authority_publication",
                ),
            ),
            "identity_owner": {
                "origin": "https://identity-owner.internal",
                "purpose": "identity-mismatch-revocation.v1",
                "identity_source_ref": "identity-source",
                "requester_spki_sha256": client_spki,
                "server_spki_sha256": "c" * 64,
                "ca_file": "identity-owner-ca.pem",
                "certificate_file": "identity-owner-client-certificate.pem",
                "private_key_file": "identity-owner-client-key.pem",
                "valid_until": NOW + timedelta(minutes=5),
            },
            "content_keys": {
                "source_ref": "key-source",
                "source_revision": "1",
                "source_digest": "d" * 64,
                "source_receipt_ref": "key-receipt",
                "observed_at": NOW - timedelta(minutes=1),
                "valid_until": NOW + timedelta(minutes=5),
                "active_key_id": "content-key-1",
                "key_ids": ("content-key-1",),
            },
            "files": {
                name: hashlib.sha256(files[name]).hexdigest() if name in PUBLIC_FILES else None
                for name in FILES
            },
        }
    )
    manifest_bytes = manifest.model_dump_json(by_alias=True).encode()
    settings = PhiProductionSettings(
        capabilities="content-read",
        material_directory="/run/maezo-phi-communication-materials/current",
        material_version_id=manifest.material_version_id,
        public_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        registered_database_digest=manifest.registered_database.registration_digest,
        identity_source_ref=manifest.registered_database.identity_source_ref,
        key_source_digest=manifest.content_keys.source_digest,
        maximum_seconds=5,
    )
    return settings, manifest, files, manifest_bytes


def test_three_distinct_read_profiles_and_current_key_material_are_verified():
    settings, manifest, files, raw = fixture()
    materials = verify_materials(settings, manifest, files, manifest_bytes=raw, now=NOW)
    assert materials.identity_url.username == "identity_reader"
    assert materials.content_url.username == "content_reader"
    assert materials.authority_url.username == "authority_reader"
    assert materials.content_keys == {"content-key-1": b"k" * 32}
    assert all(materials.manifest.files[name] is None for name in PRIVATE_FILES)


@pytest.mark.parametrize("change", ["host", "key", "manifest", "source"])
def test_wrong_private_or_registered_material_refuses(change):
    settings, manifest, files, raw = fixture()
    files = dict(files)
    if change == "host":
        files["identity-reader-dsn.txt"] = files["identity-reader-dsn.txt"].replace(b"db.test", b"other.test")
    elif change == "key":
        files["content-keys.json"] = json.dumps(
            {"content-key-1": base64.b64encode(b"short").decode()}
        ).encode()
    elif change == "manifest":
        raw += b" "
    else:
        settings = settings.model_copy(update={"key_source_digest": "e" * 64})
    with pytest.raises(PhiProductionError):
        verify_materials(settings, manifest, files, manifest_bytes=raw, now=NOW)
