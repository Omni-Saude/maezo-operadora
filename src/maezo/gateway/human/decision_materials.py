"""Bounded fixed-path decision-plane deployment materials (WP-J1-06 Phase 0).

WP-J1-00 bound every human read, command, receipt and admission port, but left
`AssignmentRuntime(decision_ports=…)` at `None` — its build report §2.4 names the
reason: `PostgresDecisionBindingSource` needs an installation root plus a pinned
`BindingDatabase`, and `PostgresHumanDecisionCustody` needs its own PHI deployment
and vault keys, "a second material plane of comparable size". The consequence on
`main` is total: `HumanGateway.submit_decision` refuses every decision with
`form_projection_unavailable` (`gateway.py:986-988`), so no APROVAR and no NEGAR
can leave the portal.

This module is that second plane, built exactly like the first one
(`production_materials.py`): ONE read-only, root-owned directory holding a public
manifest plus purpose-separated material, read through the same hardened fixed-path
reader. Nothing here derives a key from a constant, reads a credential from the
environment, or accepts a caller-supplied locator; the binding plane and the PHI
plane get distinct roles, distinct certificates and distinct connections, and the
AES vault keys exist only to be handed to `PostgresHumanDecisionCustody`.

The plane is optional and complete-or-absent: without the directory the decision
ports stay unbound and the gateway keeps its historical refusal (fail closed), and
with it every port is a concrete provider — never a stand-in.

The parity with the human plane includes its *second channel*. A bundle does not
attest itself: `DecisionMaterialPin` carries the tenant, the material version and the
canonical manifest digest from deployment configuration — facts the directory cannot
amend — and `decision_manifest_matches` compares them before any key material is read,
exactly as `manifest_matches` does for the human plane (`production_materials.py`) and
the staff plane (`gateway/staff_cases/materials.py:63-95`). The manifest also carries a
`DecisionRevocationSnapshot` with its own observation window, so withdrawn material is
refused and every key handed out is clamped by that observation — not only by the
issue window. Without those, every check here compares the directory to itself and a
wholly substituted bundle (adversary's root key, binding host and vault keys) verifies.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field, StringConstraints, model_validator

from maezo.portal.contracts.models import OpaqueRef, Revision, Sha256Digest
from maezo.portal.engine.profile import canonicalize, strict_loads

from .decision_binding import BindingDatabase
from .decision_binding_qualification import RootDesignation, canonical, sha
from .decision_custody_connection import PhiDatabaseDeployment
from .models import Closed, Scope
from .production_materials import MaterialVersion, Seconds, read_material_directory
from .read_profile import b64decode, parse_model, wire

DECISION_MATERIAL_PARENT = "/run/maezo-decision-materials"
DECISION_MATERIAL_DIRECTORY = DECISION_MATERIAL_PARENT + "/current"

#: Key material. Its bytes are never hashed into the public manifest: a public
#: digest of a secret is a dictionary-attack oracle (same rule as the human plane).
PRIVATE_FILES = frozenset({"binding-client-key.pem", "phi-client-key.pem", "vault-keys.json"})
PUBLIC_FILES = frozenset(
    {
        "installation-root.json",
        "binding-ca.pem",
        "binding-client-certificate.pem",
        "phi-ca.pem",
        "phi-client-certificate.pem",
    }
)
FILES = PRIVATE_FILES | PUBLIC_FILES

VaultKeyId = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_.:@-]{1,255}$")]
Role = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,62}$")]
Host = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9.-]{1,253}$")]
Port = Annotated[int, Field(strict=True, ge=1, le=65535)]


class DecisionMaterialError(RuntimeError):
    """Fail-closed: the decision plane never binds on partial or unattested material."""

    def __init__(self) -> None:
        super().__init__("decision_material_unavailable")


def _parse[T: Closed](model: type[T], raw: bytes) -> T:
    """Parse a material document in the human plane's own wire encoding.

    `parse_model` round-trips the result back through `wire()` and refuses anything
    non-canonical, so a document cannot mean one thing to the loader and another to
    whoever pinned its digest.
    """
    try:
        return parse_model(model, strict_loads(raw))
    except Exception:
        raise DecisionMaterialError() from None


class VaultKeyDesignation(Closed):
    """One AES-GCM custody key: its identity, its pinned bytes and whether it is active.

    Retained non-active keys decrypt immutable old rows after a rotation; removing one
    fails closed (`PostgresHumanDecisionCustody` refuses a key set that does not equal
    the deployment's `readable_key_ids`), and nothing here reciphers anything.
    """

    key_id: VaultKeyId
    material_sha256: Sha256Digest
    current: bool


class PhiDeployment(Closed):
    """The PHI custody database as the deployment attests it.

    A closed mirror of `PhiDatabaseDeployment` (a frozen dataclass, so it cannot be a
    wire document itself). `deployment()` builds the real one, whose `__post_init__`
    re-checks every rule; this model exists so the manifest can be canonical bytes.
    """

    host: Host
    port: Port
    database: Role
    writer_role: Role
    owner_role: Role
    client_certificate_sha256: Sha256Digest
    active_key_id: VaultKeyId
    readable_key_ids: tuple[VaultKeyId, ...]
    valid_until: datetime
    evidence_digest: Sha256Digest

    @model_validator(mode="after")
    def separate_roles(self) -> Self:
        if (
            self.writer_role == self.owner_role
            or self.active_key_id not in self.readable_key_ids
            or len(set(self.readable_key_ids)) != len(self.readable_key_ids)
            or self.valid_until.tzinfo is None
        ):
            raise DecisionMaterialError()
        return self

    def deployment(self, scope: Scope) -> PhiDatabaseDeployment:
        return PhiDatabaseDeployment(
            scope=scope,
            host=self.host,
            port=self.port,
            database=self.database,
            writer_role=self.writer_role,
            owner_role=self.owner_role,
            client_certificate_sha256=self.client_certificate_sha256,
            active_key_id=self.active_key_id,
            readable_key_ids=self.readable_key_ids,
            valid_until=self.valid_until,
            evidence_digest=self.evidence_digest,
        )


class DecisionRevocationSnapshot(Closed):
    """A fresh observation that nothing in this bundle has been withdrawn.

    Mirror of the human plane's `HumanRevocationSnapshot` and of the staff
    `RevocationSnapshot` (`gateway/staff_cases/production_config.py:161-176`): sorted,
    de-duplicated fingerprints and a closed observation window of its own. Without it
    the issue window is the only bound, so a rolled-back snapshot of revoked vault keys
    stays acceptable for the remainder of that window.
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
            raise DecisionMaterialError()
        return self


class DecisionPublicManifest(Closed):
    """The public half of the decision plane: pins only, never a secret's digest."""

    schema_: Literal["portal-human-decision-material.v1"] = Field(alias="schema")
    material_version_id: MaterialVersion
    scope: Scope
    issuer: str
    issued_at: datetime
    valid_until: datetime
    #: sha256 of the installation root's raw Ed25519 public key, as pinned out of band.
    root_key_fingerprint: Sha256Digest
    binding_database: BindingDatabase
    phi: PhiDeployment
    vault_keys: tuple[VaultKeyDesignation, ...]
    binding_timeout_seconds: Seconds
    phi_timeout_seconds: Seconds
    revocation_snapshot: DecisionRevocationSnapshot
    files: dict[str, Sha256Digest | None]

    @model_validator(mode="after")
    def exact_manifest(self) -> Self:
        current = [key for key in self.vault_keys if key.current]
        if (
            # One plane, one scope: the binding database, the PHI database and the
            # human command workload must be the same deployment, or the gateway's
            # own scope check (`gateway.py:146-154`) would refuse the composition.
            self.binding_database.scope != self.scope
            or len(current) != 1
            or current[0].key_id != self.phi.active_key_id
            or {key.key_id for key in self.vault_keys} != set(self.phi.readable_key_ids)
            or len({key.key_id for key in self.vault_keys}) != len(self.vault_keys)
            or len({key.material_sha256 for key in self.vault_keys}) != len(self.vault_keys)
            # The binding reader and the PHI writer are separate deployment roles and
            # must not collapse into one: one plane reads qualification, the other
            # writes clinical text.
            or self.binding_database.reader_role == self.phi.writer_role
            or self.binding_database.database == self.phi.database
            or set(self.files) != FILES
            or any(self.files[name] is None for name in PUBLIC_FILES)
            or any(self.files[name] is not None for name in PRIVATE_FILES)
            or self.issued_at.tzinfo is None
            or self.valid_until.tzinfo is None
            or self.issued_at >= self.valid_until
            # Material may not outlive the PHI deployment assurance it describes.
            or self.valid_until > self.phi.valid_until
            # The withdrawal channel must speak for THIS plane and must not attest
            # material it simultaneously declares revoked.
            or self.revocation_snapshot.scope != self.scope
            or bool(self.attested_digests() & set(self.revocation_snapshot.revoked_fingerprints))
        ):
            raise DecisionMaterialError()
        return self

    def attested_digests(self) -> frozenset[str]:
        """Every digest this manifest vouches for; none of them may be revoked."""
        return frozenset(
            {self.root_key_fingerprint, self.phi.client_certificate_sha256, self.phi.evidence_digest}
            | {key.material_sha256 for key in self.vault_keys}
            | {
                self.binding_database.resource_publisher_fingerprint,
                self.binding_database.installer_certificate_digest,
                self.binding_database.reader_certificate_digest,
            }
        )

    def canonical(self) -> bytes:
        return canonicalize(wire(self))


class VaultKeyBundle(Closed):
    schema_: Literal["portal-human-decision-vault.v1"] = Field(alias="schema")
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
            raise DecisionMaterialError()
        return self


@dataclass(frozen=True, repr=False)
class DecisionMaterialPin:
    """The out-of-band half of the decision plane's trust chain: configuration, not bundle.

    `verify_root` compares the installation root to `manifest.root_key_fingerprint` and
    the installation digest to a hash of the manifest's own `binding_database` — the
    bundle vouching for the bundle. An adversary who can write the material directory
    supplies their own root keypair, their own binding host and their own AES vault
    keys, and every internal check passes. This is the second, independently
    configured channel that says WHICH bundle this deployment accepts.
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
            raise DecisionMaterialError()


def decision_manifest_matches(
    pin: DecisionMaterialPin, manifest: DecisionPublicManifest, now: datetime
) -> None:
    """Refuse an unpinned, stale or withdrawn manifest before anything is read or opened.

    First statement of `verify_decision_materials`, which `load_decision_materials`
    reaches before `human_runtime` enters its `AsyncExitStack` — so a decision bundle
    for the wrong tenant never contributes a connection, a cipher or a binding.
    """
    snapshot = manifest.revocation_snapshot
    if (
        manifest.scope.tenant != pin.tenant
        or manifest.material_version_id != pin.material_version_id
        or hashlib.sha256(manifest.canonical()).hexdigest() != pin.public_manifest_sha256
        or not manifest.issued_at <= now < manifest.valid_until
        or not snapshot.observed_at <= now < snapshot.valid_until
    ):
        raise DecisionMaterialError()


@dataclass(frozen=True, repr=False)
class DecisionMaterials:
    """Everything the production composition root may build the decision ports from."""

    manifest: DecisionPublicManifest
    root: RootDesignation
    vault: VaultKeyBundle
    vault_material: dict[str, bytes]
    #: `min(manifest.valid_until, revocation_snapshot.valid_until, root.valid_until)`.
    #: A stale withdrawal observation shortens the plane's life instead of being
    #: ignored — the clamp the human plane applies to every lease it mints.
    not_after: datetime = datetime.max.replace(tzinfo=UTC)
    directory: str = DECISION_MATERIAL_DIRECTORY

    def ciphers(self) -> dict[str, AESGCM]:
        """The custody key set, in the exact shape `PostgresHumanDecisionCustody` demands."""
        return {key_id: AESGCM(raw) for key_id, raw in self.vault_material.items()}


def verify_root(raw: bytes, manifest: DecisionPublicManifest, *, now: datetime) -> RootDesignation:
    """The out-of-band installation root pin, checked against the manifest it authorises.

    `BindingConnection` (`decision_binding.py:255-292`) refuses unless the database it
    is handed hashes to exactly `root.installation_digest`. That check is repeated here
    so a mismatched bundle fails at load time — before a connection is ever attempted —
    and so the failure names the material, not a transport.
    """
    root = _parse(RootDesignation, raw)
    public = b64decode(root.public_key, size=32)
    if (
        hashlib.sha256(public).hexdigest() != manifest.root_key_fingerprint
        or root.scope != manifest.scope
        or root.installation_id != manifest.binding_database.installation_id
        or root.installation_digest != sha(canonical(manifest.binding_database))
        or root.observed_at.tzinfo is None
        or root.valid_until.tzinfo is None
        or not root.observed_at <= now < root.valid_until
        # The root pin outlives the material that leans on it, never the other way.
        or manifest.valid_until > root.valid_until
    ):
        raise DecisionMaterialError()
    return root


def verify_decision_materials(
    pin: DecisionMaterialPin,
    manifest: DecisionPublicManifest,
    files: dict[str, bytes],
    *,
    now: datetime,
    directory: str = DECISION_MATERIAL_DIRECTORY,
) -> DecisionMaterials:
    """Prove the bundle, then hand back only what the composition root may use.

    The out-of-band pin is checked FIRST, before a byte of key material is read: the
    tenant cross-check is a precondition of trusting the directory, not a conclusion
    drawn after building from it.
    """
    decision_manifest_matches(pin, manifest, now)
    if set(files) != FILES:
        raise DecisionMaterialError()
    for name in PUBLIC_FILES:
        if hashlib.sha256(files[name]).hexdigest() != manifest.files[name]:
            raise DecisionMaterialError()
    root = verify_root(files["installation-root.json"], manifest, now=now)

    bundle = _parse(VaultKeyBundle, files["vault-keys.json"])
    material: dict[str, bytes] = {}
    for designation in manifest.vault_keys:
        encoded = bundle.keys.get(designation.key_id)
        if encoded is None:
            raise DecisionMaterialError()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 32 or hashlib.sha256(raw).hexdigest() != designation.material_sha256:
            raise DecisionMaterialError()
        material[designation.key_id] = raw
    if (
        set(bundle.keys) != set(material)
        or len({*material.values()}) != len(material)
        or not bundle.not_before <= now < bundle.not_after
    ):
        raise DecisionMaterialError()
    # Building the real deployment here (not at first use) makes every rule in
    # `PhiDatabaseDeployment.__post_init__` a load-time failure.
    manifest.phi.deployment(manifest.scope)
    return DecisionMaterials(
        manifest=manifest,
        root=root,
        vault=bundle,
        vault_material=material,
        not_after=min(manifest.valid_until, manifest.revocation_snapshot.valid_until, root.valid_until),
        directory=directory,
    )


def load_decision_materials(directory_setting: str | None, pin: DecisionMaterialPin) -> DecisionMaterials:
    """Read the single fixed read-only decision directory; no search path, no fallback.

    `pin` is the deployment's out-of-band anchor and is not optional: without it the
    directory would be the sole authority on its own contents.
    """
    try:
        if directory_setting != DECISION_MATERIAL_DIRECTORY:
            raise DecisionMaterialError()
        files = read_material_directory(DECISION_MATERIAL_PARENT, FILES)
        manifest = _parse(DecisionPublicManifest, files.pop("manifest.json"))
        return verify_decision_materials(pin, manifest, files, now=datetime.now(UTC))
    except Exception:
        raise DecisionMaterialError() from None


__all__ = [
    "DECISION_MATERIAL_DIRECTORY",
    "DECISION_MATERIAL_PARENT",
    "FILES",
    "PRIVATE_FILES",
    "PUBLIC_FILES",
    "DecisionMaterialError",
    "DecisionMaterialPin",
    "DecisionMaterials",
    "DecisionPublicManifest",
    "DecisionRevocationSnapshot",
    "PhiDeployment",
    "VaultKeyBundle",
    "VaultKeyDesignation",
    "decision_manifest_matches",
    "load_decision_materials",
    "verify_decision_materials",
    "verify_root",
]
