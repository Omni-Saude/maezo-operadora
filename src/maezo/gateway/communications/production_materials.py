"""Version-pinned PHI GET material loading; no cloud lookup or value logging."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from sqlalchemy.engine import URL, make_url

from .production_config import (
    FILES,
    MATERIAL_DIRECTORY,
    MATERIAL_PARENT,
    MAX_BUNDLE,
    PRIVATE_FILES,
    PUBLIC_FILES,
    IdentityOwnerProfile,
    PhiProductionError,
    PhiProductionSettings,
    PhiPublicManifest,
    ReaderProfile,
)


@dataclass(frozen=True, repr=False)
class PhiMaterials:
    manifest: PhiPublicManifest
    identity_url: URL
    content_url: URL
    authority_url: URL
    database_ca: dict[str, bytes]
    owner_ca: bytes
    owner_certificate: bytes
    owner_private_key: bytes
    content_keys: dict[str, bytes]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _owned(value: os.stat_result, mode: int, *, directory: bool) -> None:
    right_type = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    if not right_type or value.st_uid != 1000 or value.st_gid != 1000 or stat.S_IMODE(value.st_mode) != mode:
        raise PhiProductionError()
    if not directory and (value.st_nlink != 1 or not 0 < value.st_size <= MAX_BUNDLE):
        raise PhiProductionError()


def _read_at(directory: int, name: str, *, private: bool) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        _owned(before, 0o400 if private else 0o444, directory=False)
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
            raise PhiProductionError()
        return bytes(raw)
    finally:
        os.close(descriptor)


def _url(raw: bytes, profile: ReaderProfile, database: str) -> URL:
    try:
        value = raw.decode("utf-8")
        result = make_url(value)
        if (
            value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or result.drivername != "postgresql+asyncpg"
            or result.host != profile.host
            or (result.port or 5432) != profile.port
            or result.database != database
            or result.username != profile.login
            or not result.password
            or result.query
            or profile.tls_server_name != result.host
        ):
            raise PhiProductionError()
        return result
    except Exception:
        raise PhiProductionError() from None


def _keys(raw: bytes, expected: tuple[str, ...]) -> dict[str, bytes]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise PhiProductionError()
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if type(value) is not dict or tuple(sorted(value)) != expected:
            raise PhiProductionError()
        decoded = {
            key: base64.b64decode(item, validate=True)
            for key, item in value.items()
            if type(key) is str and type(item) is str
        }
        if set(decoded) != set(value) or any(len(item) != 32 for item in decoded.values()):
            raise PhiProductionError()
        return decoded
    except Exception:
        raise PhiProductionError() from None


def _certificate(raw: bytes, private_key: bytes, owner: IdentityOwnerProfile, now: datetime) -> None:
    try:
        certificate = x509.load_pem_x509_certificate(raw)
        key = serialization.load_pem_private_key(private_key, password=None)
        spki = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if (
            _sha(spki) != owner.requester_spki_sha256
            or key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            != spki
            or not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc
        ):
            raise PhiProductionError()
    except Exception:
        raise PhiProductionError() from None


def verify_materials(
    settings: PhiProductionSettings,
    manifest: PhiPublicManifest,
    files: dict[str, bytes],
    *,
    manifest_bytes: bytes,
    now: datetime,
) -> PhiMaterials:
    try:
        if (
            settings.capabilities != "content-read"
            or settings.material_version_id != manifest.material_version_id
            or settings.public_manifest_sha256 != _sha(manifest_bytes)
            or settings.registered_database_digest != manifest.registered_database.registration_digest
            or settings.identity_source_ref != manifest.registered_database.identity_source_ref
            or settings.key_source_digest != manifest.content_keys.source_digest
            or not manifest.issued_at
            <= now
            < min(
                manifest.valid_until,
                manifest.identity_owner.valid_until,
                manifest.content_keys.valid_until,
            )
            or set(files) != FILES
            or sum(map(len, files.values())) > MAX_BUNDLE
        ):
            raise PhiProductionError()
        for name in PUBLIC_FILES:
            if _sha(files[name]) != manifest.files[name]:
                raise PhiProductionError()
        _certificate(
            files[manifest.identity_owner.certificate_file],
            files[manifest.identity_owner.private_key_file],
            manifest.identity_owner,
            now,
        )
        database = manifest.registered_database.database_name
        return PhiMaterials(
            manifest=manifest,
            identity_url=_url(files[manifest.identity_reader.dsn_file], manifest.identity_reader, database),
            content_url=_url(files[manifest.content_reader.dsn_file], manifest.content_reader, database),
            authority_url=_url(
                files[manifest.authority_reader.dsn_file], manifest.authority_reader, database
            ),
            database_ca={
                manifest.identity_reader.ca_file: files[manifest.identity_reader.ca_file],
                manifest.content_reader.ca_file: files[manifest.content_reader.ca_file],
                manifest.authority_reader.ca_file: files[manifest.authority_reader.ca_file],
            },
            owner_ca=files[manifest.identity_owner.ca_file],
            owner_certificate=files[manifest.identity_owner.certificate_file],
            owner_private_key=files[manifest.identity_owner.private_key_file],
            content_keys=_keys(files["content-keys.json"], manifest.content_keys.key_ids),
        )
    except Exception:
        raise PhiProductionError() from None


def load_materials(settings: PhiProductionSettings) -> PhiMaterials:
    try:
        if settings.material_directory != MATERIAL_DIRECTORY:
            raise PhiProductionError()
        parent = os.open(MATERIAL_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            directory = os.open("current", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            _owned(os.fstat(directory), 0o500, directory=True)
            if not os.fstatvfs(directory).f_flag & os.ST_RDONLY:
                raise PhiProductionError()
            if set(os.listdir(directory)) != FILES | {"manifest.json"}:
                raise PhiProductionError()
            manifest_bytes = _read_at(directory, "manifest.json", private=False)
            manifest = PhiPublicManifest.model_validate_json(manifest_bytes)
            files = {name: _read_at(directory, name, private=name in PRIVATE_FILES) for name in FILES}
        finally:
            os.close(directory)
        return verify_materials(
            settings,
            manifest,
            files,
            manifest_bytes=manifest_bytes,
            now=datetime.now(UTC),
        )
    except Exception:
        raise PhiProductionError() from None


def material_path(name: str) -> Path:
    if name not in FILES:
        raise PhiProductionError()
    return Path(MATERIAL_DIRECTORY) / name
