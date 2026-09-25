"""Bounded fixed-path human-plane deployment materials (ADR-0049 D4/D5).

The human command, Q2 read and assignment-read planes are provisioned exactly like the
approved staff plane: one read-only, root-owned directory holding a signed public
manifest plus purpose-separated key material. Nothing here derives a key from a
constant, reads a credential from the environment, or accepts a browser-supplied
locator. Each purpose owns a distinct Ed25519 key and a distinct workload reference;
`PHI_HMAC_KEY`, the OIDC secret and the A2A key have no representation in this module.

The parity with staff is load-bearing and includes its *second channel*: a bundle does
not attest itself. `HumanMaterialPin` carries the tenant, the material version and the
manifest digest from deployment configuration — facts the directory cannot amend — and
`manifest_matches` compares them before a single byte of key material is read, exactly
as `manifest_matches` does for staff (`gateway/staff_cases/materials.py:63-95`, digest
pin at `:93`). The manifest carries a `HumanRevocationSnapshot` with its own observation
window (mirror of `gateway/staff_cases/production_config.py:161-176`, consumed at
`gateway/staff_cases/materials.py:89` and `:120-129`), so a withdrawn bundle is refused
and every lease minted here is clamped by that observation, not only by the issue window.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import Field, StringConstraints, model_validator
from sqlalchemy.engine import URL, make_url

from maezo.portal.contracts.models import OpaqueRef, Revision, Sha256Digest
from maezo.portal.engine.profile import canonicalize, strict_loads

from .models import Closed, Scope
from .read_credentials import ReadAdmission
from .read_profile import b64decode, parse_model, wire

MATERIAL_PARENT = "/run/maezo-human-materials"
MATERIAL_DIRECTORY = MATERIAL_PARENT + "/current"
MAX_BUNDLE = 262144

#: Key material and connection strings. Their bytes are never hashed into the public
#: manifest: a public digest of a secret is a dictionary-attack oracle.
PRIVATE_FILES = frozenset(
    {
        "read-signing-key.pem",
        "assignment-signing-key.pem",
        "command-signing-key.pem",
        "cursor-keys.json",
        "read-client-key.pem",
        "command-client-key.pem",
        "outbox-dsn.txt",
        "source-dsn.txt",
    }
)
PUBLIC_FILES = frozenset(
    {
        "installation-root.der",
        "read-admission.json",
        "read-ca.pem",
        "read-client-certificate.pem",
        "command-ca.pem",
        "command-client-certificate.pem",
    }
)
FILES = PRIVATE_FILES | PUBLIC_FILES

#: One Ed25519 key per purpose. The file is pinned by the manifest so that a rotation
#: cannot silently repoint a purpose at another purpose's key.
KEY_FILES: dict[str, str] = {
    "portal-task-read": "read-signing-key.pem",
    "human-assignment-read": "assignment-signing-key.pem",
    "human-command": "command-signing-key.pem",
}

Purpose = Literal["portal-task-read", "human-assignment-read", "human-command"]

MaterialVersion = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9-]{32,64}$")]
Seconds = Annotated[int, Field(strict=True, ge=1, le=600)]


class HumanMaterialError(RuntimeError):
    """Fail-closed: the human plane never starts on partial or unattested material."""

    def __init__(self) -> None:
        super().__init__("human_material_unavailable")


def fingerprint(key: Ed25519PublicKey) -> str:
    return hashlib.sha256(key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).hexdigest()


def _parse[T: Closed](model: type[T], raw: bytes) -> T:
    """Parse a material document in the human plane's own wire encoding.

    `parse_model` round-trips the result back through `wire()` and refuses anything
    non-canonical, so a document cannot mean one thing to the loader and another to
    whoever signed or hashed it.
    """
    try:
        return parse_model(model, strict_loads(raw))
    except Exception:
        raise HumanMaterialError() from None


class KeyDesignation(Closed):
    """A purpose's key: its identity, its holder workload and its exact fingerprint."""

    purpose: Purpose
    key_id: OpaqueRef
    workload_ref: OpaqueRef
    audience: OpaqueRef
    fingerprint: Sha256Digest
    max_envelope_seconds: Seconds


class CursorKeyDesignation(Closed):
    key_id: OpaqueRef
    #: The 16-byte AEAD cursor tag, distinct from the material pin below: a tag
    #: travels on the wire, a pin never does.
    key_tag: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{32}$")]
    material_sha256: Sha256Digest
    generation: Revision
    current: bool


class PrivateSurface(Closed):
    """One mTLS surface of the deployed engine; no browser-selectable URL exists."""

    origin: str
    server_spki_sha256: Sha256Digest
    ca_file: Literal["read-ca.pem", "command-ca.pem"]
    certificate_file: Literal["read-client-certificate.pem", "command-client-certificate.pem"]
    private_key_file: Literal["read-client-key.pem", "command-client-key.pem"]
    client_spki_sha256: Sha256Digest
    timeout_seconds: Seconds

    @model_validator(mode="after")
    def fixed_surface(self) -> Self:
        from urllib.parse import urlsplit

        parsed = urlsplit(self.origin)
        prefix = self.ca_file.removesuffix("-ca.pem")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or self.origin != f"https://{parsed.netloc}"
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in self.origin)
            or any(c in self.origin for c in "?#\\")
            or not self.certificate_file.startswith(prefix)
            or not self.private_key_file.startswith(prefix)
        ):
            raise HumanMaterialError()
        return self


class ConnectionDesignation(Closed):
    host: str
    port: Annotated[int, Field(strict=True, ge=1, le=65535)]
    database: str
    login: str

    @model_validator(mode="after")
    def fixed_connection(self) -> Self:
        import re

        if (
            not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", self.host)
            or ".." in self.host
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.database)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.login)
        ):
            raise HumanMaterialError()
        return self


class HumanRevocationSnapshot(Closed):
    """A fresh observation that nothing in this bundle has been withdrawn.

    Without it the bundle's own issue window is the only bound, so a rolled-back
    snapshot of a revoked bundle stays acceptable for the remainder of that window.
    Mirrors the staff `RevocationSnapshot`
    (`gateway/staff_cases/production_config.py:161-176`): sorted, de-duplicated
    fingerprints and a closed observation window of its own.
    """

    scope: Scope
    source_ref: OpaqueRef
    revision: Revision
    observed_at: datetime
    valid_until: datetime
    revoked_fingerprints: tuple[Sha256Digest, ...]

    @model_validator(mode="after")
    def closed_snapshot(self) -> Self:
        if (
            tuple(sorted(set(self.revoked_fingerprints))) != self.revoked_fingerprints
            or self.observed_at.tzinfo is None
            or self.valid_until.tzinfo is None
            or self.observed_at >= self.valid_until
        ):
            raise HumanMaterialError()
        return self


class HumanPublicManifest(Closed):
    schema_: Literal["portal-human-material.v1"] = Field(alias="schema")
    material_version_id: MaterialVersion
    scope: Scope
    issuer: str
    issued_at: datetime
    valid_until: datetime
    root_key_fingerprint: Sha256Digest
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    assignment_workload_ref: OpaqueRef
    command_endpoint: str
    #: Inventory #9 — the deployed form catalog's designation, a material fact and
    #: never a literal typed into the composition root.
    catalog_ref: OpaqueRef
    publisher_ref: OpaqueRef
    keys: tuple[KeyDesignation, ...]
    cursor_keys: tuple[CursorKeyDesignation, ...]
    read_surface: PrivateSurface
    command_surface: PrivateSurface
    outbox_connection: ConnectionDesignation
    source_connection: ConnectionDesignation
    relay_lease_seconds: Seconds
    relay_retry_seconds: Seconds
    relay_poll_seconds: Seconds
    revocation_snapshot: HumanRevocationSnapshot
    files: dict[str, Sha256Digest | None]

    @model_validator(mode="after")
    def exact_manifest(self) -> Self:
        from urllib.parse import urlsplit

        by_purpose = {key.purpose: key for key in self.keys}
        endpoint = urlsplit(self.command_endpoint)
        command = by_purpose.get("human-command")
        assignment = by_purpose.get("human-assignment-read")
        read = by_purpose.get("portal-task-read")
        if (
            len(by_purpose) != len(self.keys)
            or set(by_purpose) != set(KEY_FILES)
            or command is None
            or assignment is None
            or read is None
            # Three purposes, three distinct key identities and three distinct keys:
            # no purpose may be served by another purpose's material (ADR-0049 D5).
            or len({key.key_id for key in self.keys}) != 3
            or len({key.fingerprint for key in self.keys}) != 3
            # The assignment read plane must NOT run as the command workload: the
            # native client refuses a shared workload reference by construction.
            or assignment.workload_ref != self.assignment_workload_ref
            or command.workload_ref != self.scope.workload_ref
            or read.workload_ref != self.scope.workload_ref
            or self.assignment_workload_ref == self.scope.workload_ref
            or endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.query
            or endpoint.fragment
            or endpoint.username
            or endpoint.password
            # The transport appends its own `/v1/...` paths to this value
            # (`transport.py:243-244`), so the manifest carries the engine's BASE URL —
            # the bare origin when the engine is mounted at the root, which is how the
            # deployed CIB Seven human surface is served. Reconstructing the URL from
            # the surface origin plus the path proves same-origin with nothing else in
            # it, and a base already inside the transport's own `/v1` namespace is
            # refused here instead of posting to `/v1/commands/v1/commands` at runtime.
            or self.command_endpoint != self.command_surface.origin + endpoint.path
            or (endpoint.path and not re.fullmatch(r"(/[A-Za-z0-9._~-]+)+", endpoint.path))
            or endpoint.path.startswith("/v1")
            # The withdrawal channel must speak for THIS bundle and must not attest
            # material it simultaneously declares revoked.
            or self.revocation_snapshot.scope != self.scope
            or bool(self.attested_digests() & set(self.revocation_snapshot.revoked_fingerprints))
            or self.read_surface.ca_file != "read-ca.pem"
            or self.command_surface.ca_file != "command-ca.pem"
            or sum(1 for key in self.cursor_keys if key.current) != 1
            or len({key.key_id for key in self.cursor_keys}) != len(self.cursor_keys)
            or len({key.key_tag for key in self.cursor_keys}) != len(self.cursor_keys)
            or set(self.files) != FILES
            or any(self.files[name] is None for name in PUBLIC_FILES)
            or any(self.files[name] is not None for name in PRIVATE_FILES)
            or self.outbox_connection.login == self.source_connection.login
            or self.issued_at.tzinfo is None
            or self.valid_until.tzinfo is None
            or self.issued_at >= self.valid_until
        ):
            raise HumanMaterialError()
        return self

    def canonical(self) -> bytes:
        return canonicalize(wire(self))

    def attested_digests(self) -> frozenset[str]:
        """Every digest this manifest vouches for; none of them may be revoked."""
        surfaces = (self.read_surface, self.command_surface)
        return frozenset(
            {self.root_key_fingerprint}
            | {key.fingerprint for key in self.keys}
            | {key.material_sha256 for key in self.cursor_keys}
            | {surface.client_spki_sha256 for surface in surfaces}
            | {surface.server_spki_sha256 for surface in surfaces}
        )

    def key(self, purpose: Purpose) -> KeyDesignation:
        return next(key for key in self.keys if key.purpose == purpose)

    def current_cursor(self) -> CursorKeyDesignation:
        return next(key for key in self.cursor_keys if key.current)


ADMISSION_SCHEMA = "portal-human-read-admission.v1"


def verify_read_admission(raw: bytes, root: Ed25519PublicKey, manifest: HumanPublicManifest) -> ReadAdmission:
    """The deployed read installation's capability, signed by the installation root.

    The controller's own DTO is explicitly insufficient (`ReadDeploymentAdmission`
    says so); this is the attestation the deployment signs. It is carried in the
    plane's own wire encoding and its signature is checked over the canonical bytes,
    so a re-serialization cannot quietly change what was attested.
    """
    try:
        document = strict_loads(raw)
        if (
            not isinstance(document, dict)
            or set(document) != {"schema", "record", "signature"}
            or document["schema"] != ADMISSION_SCHEMA
        ):
            raise HumanMaterialError()
        record = parse_model(ReadAdmission, document["record"])
        signature = b64decode(document["signature"])
        root.verify(signature, canonicalize(wire(record)))
    except HumanMaterialError:
        raise
    except Exception:
        raise HumanMaterialError() from None
    if (
        record.scope != manifest.scope
        or record.engine_name != manifest.engine_name
        or record.database_incarnation != manifest.database_incarnation
        or record.observed_at >= record.valid_until
    ):
        raise HumanMaterialError()
    return record


class CursorKeyBundle(Closed):
    schema_: Literal["portal-human-cursor-keys.v1"] = Field(alias="schema")
    not_before: datetime
    not_after: datetime
    keys: dict[str, str] = Field(repr=False)

    @model_validator(mode="after")
    def aware_window(self) -> Self:
        if (
            self.not_before.tzinfo is None
            or self.not_after.tzinfo is None
            or self.not_before >= self.not_after
        ):
            raise HumanMaterialError()
        return self


@dataclass(frozen=True, repr=False)
class HumanMaterialPin:
    """The out-of-band half of the trust chain: configuration, never the bundle.

    Every other check in this module compares the bundle to itself — the root key to
    the fingerprint the same manifest declares, the admission to that same root. That
    closes a loop, so an adversary who can write the material directory supplies a
    coherent bundle of their own and nothing refuses it. The staff plane does not rely
    on filesystem custody alone: `manifest_matches`
    (`gateway/staff_cases/materials.py:63-95`) pins ten manifest facts against
    independently configured settings first, decisively the manifest digest at `:93`.
    This is the same second channel, carried as three deployment facts.
    """

    tenant: str
    material_version_id: str
    public_manifest_sha256: str

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}", self.tenant)
            or not re.fullmatch(r"[A-Za-z0-9-]{32,64}", self.material_version_id)
            or not re.fullmatch(r"[0-9a-f]{64}", self.public_manifest_sha256)
        ):
            raise HumanMaterialError()


def manifest_matches(pin: HumanMaterialPin, manifest: HumanPublicManifest, now: datetime) -> None:
    """Refuse an unpinned, stale or withdrawn manifest before anything is read or opened.

    This is the first statement of `verify_materials`, which is in turn reached before
    `human_runtime` opens its `AsyncExitStack` — so a bundle for the wrong tenant never
    obtains a connection, never runs a query and never starts the command relay.
    """
    snapshot = manifest.revocation_snapshot
    if (
        manifest.scope.tenant != pin.tenant
        or manifest.material_version_id != pin.material_version_id
        or hashlib.sha256(manifest.canonical()).hexdigest() != pin.public_manifest_sha256
        or not manifest.issued_at <= now < manifest.valid_until
        or not snapshot.observed_at <= now < snapshot.valid_until
    ):
        raise HumanMaterialError()


@dataclass(frozen=True, repr=False)
class HumanMaterials:
    """Everything the production composition root is allowed to build from."""

    manifest: HumanPublicManifest
    admission: ReadAdmission
    signing_keys: dict[str, Ed25519PrivateKey]
    cursor_keys: CursorKeyBundle
    cursor_material: dict[str, bytes]
    outbox_url: URL
    source_url: URL
    #: `min(manifest.valid_until, revocation_snapshot.valid_until)`. Every lease minted
    #: from this bundle is clamped by it, so a stale withdrawal observation shortens the
    #: material's life instead of being ignored (staff parity: `materials.py:122-129`).
    not_after: datetime
    directory: str = MATERIAL_DIRECTORY

    def private_key(self, purpose: Purpose) -> Ed25519PrivateKey:
        return self.signing_keys[purpose]


def connection_url(raw: bytes, expected: ConnectionDesignation) -> URL:
    value = raw.decode("utf-8").strip()
    url = make_url(value)
    if (
        any(ord(c) < 32 or ord(c) == 127 for c in value)
        or url.drivername != "postgresql+asyncpg"
        or url.host != expected.host
        or (url.port or 5432) != expected.port
        or url.database != expected.database
        or url.username != expected.login
        or not url.password
        or url.query
    ):
        raise HumanMaterialError()
    return url


def verify_materials(
    pin: HumanMaterialPin,
    manifest: HumanPublicManifest,
    files: dict[str, bytes],
    *,
    now: datetime,
    directory: str = MATERIAL_DIRECTORY,
) -> HumanMaterials:
    """Fail-closed at its own boundary: any parse failure of the bundle bytes (a tampered PEM
    raises the library's ValueError) is a refusal, not an exception type the caller must know."""
    try:
        return _verify_materials(pin, manifest, files, now=now, directory=directory)
    except HumanMaterialError:
        raise
    except Exception:
        raise HumanMaterialError() from None


def _verify_materials(
    pin: HumanMaterialPin,
    manifest: HumanPublicManifest,
    files: dict[str, bytes],
    *,
    now: datetime,
    directory: str = MATERIAL_DIRECTORY,
) -> HumanMaterials:
    # The out-of-band pin is checked FIRST, before the bundle's own bytes are read or
    # any key material is loaded: the tenant cross-check is not a post-condition of a
    # composed plane, it is a precondition of trusting the directory at all.
    manifest_matches(pin, manifest, now)
    if set(files) != FILES or sum(map(len, files.values())) > MAX_BUNDLE:
        raise HumanMaterialError()
    for name in PUBLIC_FILES:
        if hashlib.sha256(files[name]).hexdigest() != manifest.files[name]:
            raise HumanMaterialError()
    root = serialization.load_der_public_key(files["installation-root.der"])
    if not isinstance(root, Ed25519PublicKey) or fingerprint(root) != manifest.root_key_fingerprint:
        raise HumanMaterialError()
    admission = verify_read_admission(files["read-admission.json"], root, manifest)
    if not admission.observed_at <= now < admission.valid_until:
        raise HumanMaterialError()

    signing: dict[str, Ed25519PrivateKey] = {}
    for purpose, filename in KEY_FILES.items():
        key = serialization.load_pem_private_key(files[filename], password=None)
        designation = manifest.key(purpose)  # type: ignore[arg-type]
        if not isinstance(key, Ed25519PrivateKey) or fingerprint(key.public_key()) != (
            designation.fingerprint
        ):
            raise HumanMaterialError()
        signing[purpose] = key
    # Distinct fingerprints were checked on the manifest; prove the loaded bytes agree.
    if len({fingerprint(k.public_key()) for k in signing.values()}) != len(signing):
        raise HumanMaterialError()

    bundle = _parse(CursorKeyBundle, files["cursor-keys.json"])
    material: dict[str, bytes] = {}
    for cursor in manifest.cursor_keys:
        encoded = bundle.keys.get(cursor.key_id)
        if encoded is None:
            raise HumanMaterialError()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 32 or hashlib.sha256(raw).hexdigest() != cursor.material_sha256:
            raise HumanMaterialError()
        material[cursor.key_id] = raw
    if set(bundle.keys) != set(material) or len({*material.values()}) != len(material):
        raise HumanMaterialError()
    if not bundle.not_before <= now < bundle.not_after:
        raise HumanMaterialError()

    for surface in (manifest.read_surface, manifest.command_surface):
        _verify_surface(surface, files, now=now)
    return HumanMaterials(
        manifest=manifest,
        admission=admission,
        signing_keys=signing,
        cursor_keys=bundle,
        cursor_material=material,
        outbox_url=connection_url(files["outbox-dsn.txt"], manifest.outbox_connection),
        source_url=connection_url(files["source-dsn.txt"], manifest.source_connection),
        not_after=min(manifest.valid_until, manifest.revocation_snapshot.valid_until),
        directory=directory,
    )


def _verify_surface(surface: PrivateSurface, files: dict[str, bytes], *, now: datetime) -> None:
    from cryptography import x509

    certificate = x509.load_pem_x509_certificate(files[surface.certificate_file])
    spki = certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    private = serialization.load_pem_private_key(files[surface.private_key_file], password=None)
    if (
        hashlib.sha256(spki).hexdigest() != surface.client_spki_sha256
        or private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo) != spki
        or not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc
    ):
        raise HumanMaterialError()


def _owned(info: os.stat_result, mode: int, *, directory: bool) -> None:
    correct = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct or info.st_uid != 1000 or info.st_gid != 1000 or stat.S_IMODE(info.st_mode) != mode:
        raise HumanMaterialError()
    if not directory and (info.st_nlink != 1 or not 0 < info.st_size <= MAX_BUNDLE):
        raise HumanMaterialError()


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
        if len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in stable):
            raise HumanMaterialError()
        return bytes(raw)
    finally:
        os.close(descriptor)


def read_material_directory(parent_path: str, names: frozenset[str]) -> dict[str, bytes]:
    """Read one fixed, read-only, root-owned material directory: `manifest.json` + `names`.

    The hardening — a `current` child opened with `O_NOFOLLOW` under an explicitly
    opened parent, a root-owned `0o500` directory on a read-only filesystem, an exact
    directory listing, and per-file `O_NOFOLLOW` reads whose stat is re-checked after
    the bytes are read — is the property, not an implementation detail, so both
    material planes (this one and `decision_materials.py`) share this one reader
    instead of each growing its own copy.

    Reading is custody, not trust: what makes the bytes authoritative is the caller's
    out-of-band anchor (`HumanMaterialPin` here), never this directory.
    """
    parent = os.open(parent_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory = os.open("current", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        _owned(os.fstat(directory), 0o500, directory=True)
        if not os.fstatvfs(directory).f_flag & os.ST_RDONLY:
            raise HumanMaterialError()
        if set(os.listdir(directory)) != names | {"manifest.json"}:
            raise HumanMaterialError()
        return {name: _read_at(directory, name) for name in names | {"manifest.json"}}
    finally:
        os.close(directory)


def load_human_materials(directory_setting: str | None, pin: HumanMaterialPin) -> HumanMaterials:
    """Read the single fixed read-only material directory; no search path, no fallback.

    `pin` is the deployment's out-of-band anchor and is not optional: without it the
    directory would be the sole authority on its own contents.
    """
    try:
        if directory_setting != MATERIAL_DIRECTORY:
            raise HumanMaterialError()
        files = read_material_directory(MATERIAL_PARENT, FILES)
        manifest = _parse(HumanPublicManifest, files.pop("manifest.json"))
        return verify_materials(pin, manifest, files, now=datetime.now(UTC))
    except Exception:
        raise HumanMaterialError() from None
