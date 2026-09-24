"""Bounded fixed-path material loading; no cloud lookup, issuer or credential fallback."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from dataclasses import dataclass, replace
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy.engine import URL, make_url

from maezo.gateway.external_cases.models import digest, now_utc, parse, timestamp

from .authority import InstalledStaffAuthority, fingerprint
from .models import Proof
from .production_config import (
    FILES,
    MATERIAL_DIRECTORY,
    MATERIAL_PARENT,
    MAX_BUNDLE,
    PUBLIC_FILES,
    Connection,
    PortalProductionSettings,
    PortalStaffBootstrapError,
    PublicManifest,
    SecretBundle,
)
from .publisher import StaffSigner


@dataclass(frozen=True, repr=False)
class StaffMaterials:
    manifest: PublicManifest
    authority: InstalledStaffAuthority
    read_signer: StaffSigner
    witness_signer: StaffSigner
    session_url: URL
    witness_url: URL


def connection_url(raw: bytes, expected: Connection) -> URL:
    value = raw.decode("utf-8")
    url = make_url(value)
    if (
        value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or url.drivername != "postgresql+asyncpg"
        or url.host != expected.host
        or (url.port or 5432) != int(expected.port)
        or url.database != expected.database
        or url.username != expected.login
        or not url.password
        or url.query
    ):
        raise PortalStaffBootstrapError()
    return url


def manifest_matches(settings: PortalProductionSettings, manifest: PublicManifest, now: datetime) -> None:
    expected = (
        settings.staff_material_version_id,
        settings.staff_scope,
        settings.issuer,
        settings.staff_root_key_sha256,
        settings.staff_designation_sha256,
        settings.staff_native_configuration_sha256,
        settings.staff_read_key_sha256,
        settings.staff_witness_key_sha256,
        settings.staff_native_origin,
        settings.staff_native_server_spki_sha256,
        settings.staff_native_schema,
        settings.staff_engine_schema,
    )
    actual = (
        manifest.material_version_id,
        manifest.scope,
        manifest.issuer,
        manifest.root_key_fingerprint,
        manifest.designation_digest,
        manifest.native_configuration_digest,
        manifest.read_key_fingerprint,
        manifest.witness_key_fingerprint,
        manifest.native_origin,
        manifest.native_server_spki_sha256,
        manifest.native_schema,
        manifest.engine_schema,
    )
    snapshot = manifest.revocation_snapshot
    if (
        settings.capabilities not in ("identity,staff_cases", "identity,staff_cases,human")
        or actual != expected
        or digest(manifest.wire()) != settings.staff_public_manifest_sha256
        or settings.staff_maximum_seconds is None
        or settings.staff_maximum_seconds > int(manifest.native_maximum_seconds)
        or not timestamp(manifest.issued_at) <= now < timestamp(manifest.valid_until)
        or not timestamp(snapshot.observed_at) <= now < timestamp(snapshot.valid_until)
    ):
        raise PortalStaffBootstrapError()


def verify_materials(
    settings: PortalProductionSettings, manifest: PublicManifest, files: dict[str, bytes], *, now: datetime
) -> StaffMaterials:
    manifest_matches(settings, manifest, now)
    if set(files) != FILES or sum(map(len, files.values())) > MAX_BUNDLE:
        raise PortalStaffBootstrapError()
    for name in PUBLIC_FILES:
        if hashlib.sha256(files[name]).hexdigest() != manifest.files[name]:
            raise PortalStaffBootstrapError()
    root = serialization.load_der_public_key(files["installation-root.der"])
    if not isinstance(root, Ed25519PublicKey) or fingerprint(root) != settings.staff_root_key_sha256:
        raise PortalStaffBootstrapError()
    authority = InstalledStaffAuthority.verify(
        designation_bytes=files["designation.json"],
        installation_proof=parse(Proof, files["installation-proof.json"]),
        expected_digest=manifest.designation_digest,
        expected_scope=manifest.scope,
        root=root,
        revoked_fingerprints=frozenset(manifest.revocation_snapshot.revoked_fingerprints),
        now=now,
    )
    authority = replace(
        authority,
        valid_until=min(
            authority.valid_until,
            timestamp(manifest.valid_until),
            timestamp(manifest.revocation_snapshot.valid_until),
        ),
    )
    signers: list[StaffSigner] = []
    for name, role, expected, purposes in (
        (
            "read-signing-key.pem",
            "read_requester",
            manifest.read_key_fingerprint,
            ("staff-case-read.v1", "staff-case-finalize.v1"),
        ),
        (
            "witness-signing-key.pem",
            "identity_verifier",
            manifest.witness_key_fingerprint,
            ("membership_current",),
        ),
    ):
        key = serialization.load_pem_private_key(files[name], password=None)
        if not isinstance(key, Ed25519PrivateKey) or fingerprint(key.public_key()) != expected:
            raise PortalStaffBootstrapError()
        signer = StaffSigner(authority, key, role)
        for purpose in purposes:
            signer.guard(purpose)
        signers.append(signer)
    read, witness = signers
    entry = authority.entries[manifest.read_key_fingerprint]
    if (
        # Conjunto exato e na ordem canonica (D-H.4): `/cases` exige `list`, o detalhe exige `detail`.
        entry.operations != ("detail", "list")
        or set(entry.projections)
        != {"staff_summary.v1", "staff_identity.v1", "staff_current_task.v1", "staff_escalation.v1"}
        or authority.entries[manifest.witness_key_fingerprint].login_role
        != manifest.native_witness_connection.login
    ):
        raise PortalStaffBootstrapError()
    certificate = x509.load_pem_x509_certificate(files["read-client-certificate.pem"])
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    tls_key = serialization.load_pem_private_key(files["read-client-key.pem"], password=None)
    if (
        hashlib.sha256(spki).hexdigest() != entry.certificate_spki
        or tls_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != spki
        or not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc
    ):
        raise PortalStaffBootstrapError()
    return StaffMaterials(
        manifest,
        authority,
        read,
        witness,
        connection_url(files["session-lock-dsn.txt"], manifest.session_lock_connection),
        connection_url(files["native-witness-dsn.txt"], manifest.native_witness_connection),
    )


def decode_bundle(raw: bytes, settings: PortalProductionSettings) -> tuple[PublicManifest, dict[str, bytes]]:
    try:
        if not 0 < len(raw) <= MAX_BUNDLE:
            raise PortalStaffBootstrapError()
        bundle = parse(SecretBundle, raw)
        decoded = {name: base64.b64decode(value, validate=True) for name, value in bundle.files.items()}
        if any(base64.b64encode(decoded[n]).decode("ascii") != bundle.files[n] for n in decoded):
            raise PortalStaffBootstrapError()
        verify_materials(settings, bundle.public_manifest, decoded, now=now_utc())
        return bundle.public_manifest, decoded
    except Exception:
        raise PortalStaffBootstrapError() from None


def _owned(st: os.stat_result, mode: int, *, directory: bool) -> None:
    correct_type = stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)
    if not correct_type or st.st_uid != 1000 or st.st_gid != 1000 or stat.S_IMODE(st.st_mode) != mode:
        raise PortalStaffBootstrapError()
    if not directory and (st.st_nlink != 1 or not 0 < st.st_size <= MAX_BUNDLE):
        raise PortalStaffBootstrapError()


def _read_at(directory: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        _owned(before, 0o400, directory=False)
        raw = bytearray()
        while len(raw) <= MAX_BUNDLE:
            block = os.read(descriptor, min(8192, MAX_BUNDLE + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if len(raw) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in stable):
            raise PortalStaffBootstrapError()
        return bytes(raw)
    finally:
        os.close(descriptor)


def load_materials(settings: PortalProductionSettings) -> StaffMaterials:
    try:
        if settings.staff_material_directory != MATERIAL_DIRECTORY:
            raise PortalStaffBootstrapError()
        parent = os.open(MATERIAL_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            directory = os.open("current", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            _owned(os.fstat(directory), 0o500, directory=True)
            if not os.fstatvfs(directory).f_flag & os.ST_RDONLY:
                raise PortalStaffBootstrapError()
            if set(os.listdir(directory)) != FILES | {"manifest.json"}:
                raise PortalStaffBootstrapError()
            manifest = parse(PublicManifest, _read_at(directory, "manifest.json"))
            files = {name: _read_at(directory, name) for name in FILES}
        finally:
            os.close(directory)
        return verify_materials(settings, manifest, files, now=now_utc())
    except Exception:
        raise PortalStaffBootstrapError() from None
