"""Synthetic installed material, actual codecs/keys/TLS; no cloud or source issuance."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from maezo.gateway.external_cases.models import digest, instant
from maezo.gateway.staff_cases import materialize as init
from maezo.gateway.staff_cases import materials as m
from maezo.gateway.staff_cases.authority import fingerprint
from maezo.gateway.staff_cases.models import FIELDS, StaffCaseError
from maezo.gateway.staff_cases.production_config import (
    FILES,
    PRIVATE_FILES,
    PortalProductionSettings,
    PortalStaffBootstrapError,
)
from maezo.portal.engine.profile import canonicalize


def material_fixture() -> tuple[PortalProductionSettings, dict[str, Any], dict[str, bytes]]:
    now = datetime.now(UTC)
    start, end = instant(now - timedelta(minutes=1)), instant(now + timedelta(hours=1))
    scope = dict(tenant="tenant", environment="test", engine_name="engine", database_incarnation="inc")
    root, read, witness, tls = [Ed25519PrivateKey.generate() for _ in range(4)]
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic.invalid")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(tls.public_key())
        .serial_number(123)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(tls, None)
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)

    def private(key: Ed25519PrivateKey) -> bytes:
        return key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )

    entries = []
    for key, role, login, purposes in (
        (read, "read_requester", "read_native", ["staff-case-read.v1", "staff-case-finalize.v1"]),
        (witness, "identity_verifier", "witness_login", ["membership_current"]),
    ):
        entries.append(
            dict(
                entry_ref=role,
                role=role,
                source_namespace="namespace",
                source_ref="membership-source",
                key_fingerprint=fingerprint(key.public_key()),
                certificate_spki=fingerprint(tls.public_key()) if role == "read_requester" else None,
                public_key=base64.b64encode(
                    key.public_key().public_bytes(
                        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                ).decode(),
                login_role=login,
                purposes=purposes,
                projections=list(FIELDS) if role == "read_requester" else [],
                operations=["detail"] if role == "read_requester" else [],
                not_before=start,
                valid_until=end,
            )
        )
    designation = dict(
        schema="staff-case-designation.v1",
        scope=scope,
        designation_ref="designation",
        designation_revision="1",
        expected_previous_revision="0",
        authority_ref="authority",
        authority_revision="1",
        entries=entries,
        issued_at=start,
        valid_until=end,
        state="active",
    )
    proof = dict(
        schema="staff-case-proof.v1",
        purpose="installation",
        algorithm="Ed25519",
        key_fingerprint=fingerprint(root.public_key()),
        issued_at=start,
        expires_at=end,
        statement_digest=digest(designation),
    )
    proof["signature"] = base64.b64encode(root.sign(canonicalize(proof))).decode()
    files = {
        "designation.json": canonicalize(designation),
        "installation-proof.json": canonicalize(proof),
        "installation-root.der": root.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
        "read-signing-key.pem": private(read),
        "witness-signing-key.pem": private(witness),
        "native-ca.pem": pem,
        "read-client-certificate.pem": pem,
        "read-client-key.pem": private(tls),
        "session-lock-ca.pem": pem,
        "native-witness-ca.pem": pem,
        "session-lock-dsn.txt": b"postgresql+asyncpg://lock_login:synthetic@identity.invalid:5432/identity",
        "native-witness-dsn.txt": b"postgresql+asyncpg://witness_login:synthetic@native.invalid:5432/native",
    }
    version = "11111111-2222-3333-4444-555555555555"
    manifest = dict(
        schema="portal-staff-material.v2",
        material_version_id=version,
        scope=scope,
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_test",
        issued_at=start,
        valid_until=end,
        root_key_fingerprint=fingerprint(root.public_key()),
        designation_digest=digest(designation),
        native_configuration_digest="c" * 64,
        native_maximum_seconds="5",
        read_key_fingerprint=fingerprint(read.public_key()),
        witness_key_fingerprint=fingerprint(witness.public_key()),
        native_origin="https://native.invalid",
        native_server_spki_sha256="d" * 64,
        native_schema="maezo_native",
        engine_schema="cibseven",
        session_lock_connection=dict(
            host="identity.invalid",
            port="5432",
            database="identity",
            login="lock_login",
            tls_server_name="identity.invalid",
            ca_file="session-lock-ca.pem",
            function_pin=dict(oid="12", owner="portal_external_identity_reader", definition_sha256="e" * 64),
        ),
        native_witness_connection=dict(
            host="native.invalid",
            port="5432",
            database="native",
            login="witness_login",
            tls_server_name="native.invalid",
            ca_file="native-witness-ca.pem",
            function_pin=None,
        ),
        native_relation_pins={
            n: dict(oid=str(i), owner="native_owner")
            for i, n in enumerate(("mzo_portal_read_membership", "mzo_human_principal"), start=20)
        },
        revocation_snapshot=dict(
            scope=scope,
            designation_digest=digest(designation),
            source_ref="revocations",
            revision="1",
            observed_at=start,
            valid_until=instant(now + timedelta(minutes=5)),
            revoked_fingerprints=[],
        ),
        files={
            n: None if n in PRIVATE_FILES else hashlib.sha256(value).hexdigest() for n, value in files.items()
        },
    )
    return settings_for(manifest), manifest, files


def settings_for(manifest: dict[str, Any]) -> PortalProductionSettings:
    return PortalProductionSettings(
        capabilities="identity,staff_cases",
        tenant="tenant",
        issuer=manifest["issuer"],
        staff_material_directory="/run/maezo-staff-materials/current",
        staff_material_version_id=manifest["material_version_id"],
        staff_public_manifest_sha256=digest(manifest),
        staff_root_key_sha256=manifest["root_key_fingerprint"],
        staff_designation_sha256=manifest["designation_digest"],
        staff_native_configuration_sha256=manifest["native_configuration_digest"],
        staff_scope=manifest["scope"],
        staff_native_origin=manifest["native_origin"],
        staff_native_server_spki_sha256=manifest["native_server_spki_sha256"],
        staff_native_schema=manifest["native_schema"],
        staff_engine_schema=manifest["engine_schema"],
        staff_read_key_sha256=manifest["read_key_fingerprint"],
        staff_witness_key_sha256=manifest["witness_key_fingerprint"],
        staff_maximum_seconds=5,
    )


def bundle(manifest: dict[str, Any], files: dict[str, bytes]) -> bytes:
    return canonicalize(
        dict(
            schema="portal-staff-secret-bundle.v1",
            material_version_id=manifest["material_version_id"],
            public_manifest=manifest,
            files={n: base64.b64encode(v).decode() for n, v in files.items()},
        )
    )


def test_actual_keys_authority_and_snapshot_ceiling() -> None:
    settings, manifest, files = material_fixture()
    parsed, decoded = m.decode_bundle(bundle(manifest, files), settings)
    loaded = m.verify_materials(settings, parsed, decoded, now=datetime.now(UTC))
    assert loaded.authority.valid_until == m.timestamp(manifest["revocation_snapshot"]["valid_until"])
    assert loaded.read_signer.authority is loaded.witness_signer.authority is loaded.authority
    assert loaded.session_url.username == "lock_login" and loaded.witness_url.username == "witness_login"
    assert "synthetic" not in repr(loaded)
    expired = loaded.authority.valid_until
    with pytest.raises(StaffCaseError):
        replace(loaded.read_signer, clock=lambda: expired).guard("staff-case-read.v1")


@pytest.mark.parametrize(
    "mutation", ["root", "key", "certificate", "role", "tenant", "expired", "revoked", "dsn", "tls"]
)
def test_invalid_material_refuses(mutation: str) -> None:
    settings, manifest, files = material_fixture()
    if mutation == "root":
        settings = settings.model_copy(update={"staff_root_key_sha256": "0" * 64})
    elif mutation == "key":
        files["read-signing-key.pem"] = files["witness-signing-key.pem"]
    elif mutation == "certificate":
        files["read-client-key.pem"] = files["witness-signing-key.pem"]
    elif mutation == "role":
        manifest["read_key_fingerprint"] = manifest["witness_key_fingerprint"]
    elif mutation == "tenant":
        manifest["scope"]["tenant"] = "other"
    elif mutation == "expired":
        manifest["revocation_snapshot"]["valid_until"] = manifest["issued_at"]
    elif mutation == "revoked":
        manifest["revocation_snapshot"]["revoked_fingerprints"] = [manifest["read_key_fingerprint"]]
    elif mutation == "dsn":
        files["native-witness-dsn.txt"] = files["session-lock-dsn.txt"]
    elif mutation == "tls":
        manifest["native_witness_connection"]["tls_server_name"] = "other.invalid"
    if mutation in {"expired", "revoked", "tls"}:
        settings = settings.model_copy(update={"staff_public_manifest_sha256": digest(manifest)})
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$"):
        m.decode_bundle(bundle(manifest, files), settings)


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "oversize", "extra_file"])
def test_closed_bundle(mutation: str) -> None:
    settings, manifest, files = material_fixture()
    if mutation == "extra_file":
        files["../secret"] = b"no"
    raw = bundle(manifest, files)
    if mutation == "duplicate":
        raw = raw[:-1] + b',"schema":"portal-staff-secret-bundle.v1"}'
    elif mutation == "unknown":
        raw = raw[:-1] + b',"unexpected":null}'
    elif mutation == "oversize":
        raw = b" " * 65537
    with pytest.raises(PortalStaffBootstrapError):
        m.decode_bundle(raw, settings)


def test_native_schema_is_pinned_from_manifest_and_settings() -> None:
    settings, manifest, files = material_fixture()
    parsed, decoded = m.decode_bundle(bundle(manifest, files), settings)
    loaded = m.verify_materials(settings, parsed, decoded, now=datetime.now(UTC))
    assert loaded.manifest.native_schema == settings.staff_native_schema == "maezo_native"


@pytest.mark.parametrize("shape", ["v1_original", "v1_with_native_schema"])
def test_v1_manifest_is_refused_on_load(shape: str) -> None:
    # ADR-0060 D3: no v1 package was ever published, so there is nothing to migrate. The
    # second shape isolates the schema literal: every other field is the valid v2 one.
    settings, manifest, files = material_fixture()
    manifest["schema"] = "portal-staff-material.v1"
    if shape == "v1_original":
        del manifest["native_schema"]
    settings = settings.model_copy(update={"staff_public_manifest_sha256": digest(manifest)})
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$"):
        m.decode_bundle(bundle(manifest, files), settings)


@pytest.mark.parametrize(
    "schema",
    [
        "Maezo_native",
        "maezo-native",
        "1maezo",
        "a" * 64,
        "",
        "maezo_native;drop",
        '"maezo_native"',
        "maezo.native",
    ],
)
def test_native_schema_outside_the_identifier_regex_is_refused(schema: str) -> None:
    settings, manifest, files = material_fixture()
    manifest["native_schema"] = schema
    settings = settings.model_copy(update={"staff_public_manifest_sha256": digest(manifest)})
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$"):
        m.decode_bundle(bundle(manifest, files), settings)
    with pytest.raises(ValidationError):
        PortalProductionSettings.model_validate(
            {**settings.model_dump(), "staff_native_schema": schema, "staff_scope": manifest["scope"]}
        )


@pytest.mark.parametrize(
    "schema", ["public", "cibseven", "information_schema", "pg_catalog", "pg_temp", "pg_toast", "pg_temp_3"]
)
def test_shared_or_system_schema_is_never_the_native_schema(schema: str) -> None:
    settings, manifest, files = material_fixture()
    manifest["native_schema"] = schema
    settings = settings.model_copy(update={"staff_public_manifest_sha256": digest(manifest)})
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$"):
        m.decode_bundle(bundle(manifest, files), settings)
    with pytest.raises(ValidationError):
        PortalProductionSettings.model_validate(
            {**settings.model_dump(), "staff_native_schema": schema, "staff_scope": manifest["scope"]}
        )


@pytest.mark.parametrize(
    "schema", ["maezo_native_v2", "maezo_native_owner_v1", "public_native", "maezo_nativ"]
)
def test_native_schema_must_equal_the_deployment_pin_exactly(schema: str) -> None:
    # D5: equality only. A prefix-sharing native-v2 schema is a different pin.
    settings, manifest, files = material_fixture()
    settings = settings.model_copy(update={"staff_native_schema": schema})
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$"):
        m.decode_bundle(bundle(manifest, files), settings)


def test_engine_schema_is_pinned_from_manifest_and_settings() -> None:
    settings, manifest, files = material_fixture()
    parsed, decoded = m.decode_bundle(bundle(manifest, files), settings)
    loaded = m.verify_materials(settings, parsed, decoded, now=datetime.now(UTC))
    assert loaded.manifest.engine_schema == settings.staff_engine_schema == "cibseven"


@pytest.mark.parametrize(
    "schema",
    [
        "Cibseven", "cib-seven", "1cib", "a" * 64, "", "cibseven;drop", '"cibseven"', "cib.seven",
        "public", "information_schema", "maezo_native", "pg_catalog", "pg_temp", "pg_toast", "pg_temp_3",
    ],
)
def test_engine_schema_outside_the_rule_is_refused(schema: str) -> None:
    settings, manifest, files = material_fixture()
    manifest["engine_schema"] = schema
    settings = settings.model_copy(update={"staff_public_manifest_sha256": digest(manifest)})
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$"):
        m.decode_bundle(bundle(manifest, files), settings)
    with pytest.raises(ValidationError):
        PortalProductionSettings.model_validate(
            {**settings.model_dump(), "staff_engine_schema": schema, "staff_scope": manifest["scope"]}
        )


def test_engine_schema_is_never_the_pinned_native_schema() -> None:
    settings, manifest, files = material_fixture()
    manifest["native_schema"] = manifest["engine_schema"] = "shared_schema"
    settings = settings.model_copy(update={"staff_public_manifest_sha256": digest(manifest)})
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$"):
        m.decode_bundle(bundle(manifest, files), settings)
    values = {**settings.model_dump(), "staff_scope": manifest["scope"]}
    values["staff_native_schema"] = values["staff_engine_schema"] = "shared_schema"
    with pytest.raises(PortalStaffBootstrapError):
        PortalProductionSettings.model_validate(values)


@pytest.mark.parametrize("schema", ["cibseven_v2", "cibsevem", "cibseve"])
def test_engine_schema_must_equal_the_deployment_pin_exactly(schema: str) -> None:
    settings, manifest, files = material_fixture()
    settings = settings.model_copy(update={"staff_engine_schema": schema})
    with pytest.raises(PortalStaffBootstrapError, match="^portal_staff_bootstrap_unavailable$"):
        m.decode_bundle(bundle(manifest, files), settings)


def test_staff_profile_requires_the_engine_schema() -> None:
    settings, manifest, _ = material_fixture()
    values = {**settings.model_dump(), "staff_scope": manifest["scope"]}
    del values["staff_engine_schema"]
    with pytest.raises(PortalStaffBootstrapError):
        PortalProductionSettings.model_validate(values)


def test_staff_profile_requires_the_native_schema() -> None:
    settings, manifest, _ = material_fixture()
    values = {**settings.model_dump(), "staff_scope": manifest["scope"]}
    del values["staff_native_schema"]
    with pytest.raises(PortalStaffBootstrapError):
        PortalProductionSettings.model_validate(values)


def test_identity_profile_has_no_staff_defaults() -> None:
    with pytest.raises(ValidationError):
        PortalProductionSettings(tenant="tenant", issuer="issuer")  # type: ignore[call-arg]
    with pytest.raises(PortalStaffBootstrapError):
        PortalProductionSettings(
            capabilities="identity",
            tenant="tenant",
            issuer="issuer",
            staff_native_origin="https://native.invalid",
        )


def test_materialize_fixed_files_and_refuse_retry(tmp_path, monkeypatch) -> None:
    settings, manifest, files = material_fixture()
    parent = tmp_path / "materials"
    parent.mkdir(mode=0o700)
    monkeypatch.setattr(init, "MATERIAL_PARENT", str(parent))
    # macOS test uid differs from ECS1000; only identity metadata is substituted.
    actual_owned = m._owned

    def owned(st, mode, *, directory):
        values = {n: getattr(st, n) for n in ("st_mode", "st_nlink", "st_size")}
        actual_owned(SimpleNamespace(**values, st_uid=1000, st_gid=1000), mode, directory=directory)

    monkeypatch.setattr(init, "_owned", owned)
    init.materialize(bundle(manifest, files), settings)
    assert set(os.listdir(parent / "current")) == FILES | {"manifest.json"}
    assert (parent / "current" / "designation.json").read_bytes() == files["designation.json"]
    with pytest.raises(PortalStaffBootstrapError):
        init.materialize(bundle(manifest, files), settings)
    for path in (parent / "current").iterdir():
        assert path.stat().st_mode & 0o777 == 0o400
    (parent / "current").chmod(0o700)  # allow pytest-owned temp cleanup only


def test_no_follow_file_loader(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_text("synthetic")
    (tmp_path / "link").symlink_to(target)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            m._read_at(fd, "link")
    finally:
        os.close(fd)
