"""Document-plane material bundle: the out-of-band half of its trust chain.

Same posture as the human/decision/staff bundles (`production_materials.py`,
`decision_materials.py`): one fixed read-only directory, a closed manifest, and a pin
the deployment holds OUTSIDE the bundle. A bundle attests itself; the pin is the second,
independently configured channel that says WHICH bundle this deployment accepts, and it
is compared before any key material is read.

The `NativeDatabaseBinding` embedded in the manifest is not a credential — it is the
public relation/role/oid pin set the reader re-verifies against the live database on
every use (`NativeAuthReader.qualified`: roles, ownership, RLS flags, installation and
qualification digests, TLS). Reusing the installed AUTH reader is therefore not a trust
shortcut: the reader self-qualifies, and the connection's own role is the credential.
"""

from __future__ import annotations

import hashlib
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from maezo.gateway.documents.authority import PublishedDocumentAuthority
from maezo.gateway.documents.postgres import PostgresProtectedDocumentProvider
from maezo.gateway.documents.storage import DocumentScope, PhiDocumentKeys
from maezo.gateway.intake.native_authority import (
    NativeAuthReader,
    NativeDatabaseBinding,
    protected_bytes,
)
from maezo.portal.contracts.intake import Closed, ResourceRef
from maezo.portal.contracts.models import Sha256Digest

#: The single fixed document directory; no search path, no fallback.
DOCUMENT_MATERIAL_DIRECTORY = "/run/maezo-document-materials/current"


class DocumentMaterialError(Exception):
    """The bundle is absent, stale, unpinned or internally inconsistent: no plane."""


class DocumentPlanePin:
    """`tenant` + version + manifest digest, configured outside the bundle."""

    def __init__(self, *, tenant: str, plane_version_id: str, public_manifest_sha256: str) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}", tenant or "")
            or not re.fullmatch(r"[A-Za-z0-9-]{32,64}", plane_version_id or "")
            or not re.fullmatch(r"[0-9a-f]{64}", public_manifest_sha256 or "")
        ):
            raise DocumentMaterialError()
        self.tenant = tenant
        self.plane_version_id = plane_version_id
        self.public_manifest_sha256 = public_manifest_sha256


class DocumentPlaneManifest(Closed):
    """Everything the plane needs, signed by the deployment's own pin."""

    schema_: Literal["human-document-plane.v1"] = Field(alias="schema")
    plane_version_id: str = Field(pattern=r"^[A-Za-z0-9-]{32,64}$")
    tenant: ResourceRef
    environment: ResourceRef
    issued_at: datetime
    valid_until: datetime
    #: PHI byte role: owns `portal_document.object` / `.response`, never the BFF key.
    store_url: SecretStr = Field(repr=False)
    #: Read-only native AUTH head reader role.
    reader_url: SecretStr = Field(repr=False)
    native: NativeDatabaseBinding
    key_id: str
    key_path: Path = Field(repr=False)
    key_sha256: Sha256Digest = Field(repr=False)
    seconds: float = Field(default=5, gt=0, le=30)


@dataclass(frozen=True, repr=False)
class DocumentPlaneMaterials:
    """Loaded, verified bundle; no connection has been opened yet."""

    manifest: DocumentPlaneManifest
    keys: PhiDocumentKeys
    reader: NativeAuthReader
    store_engine: AsyncEngine
    engines: tuple[AsyncEngine, ...] = field(repr=False)

    @property
    def scope(self) -> DocumentScope:
        return DocumentScope(tenant=self.manifest.tenant, environment=self.manifest.environment)


@dataclass(frozen=True, repr=False)
class DocumentPlane:
    """The composed document plane and the two factories the deployments bind."""

    authority: PublishedDocumentAuthority
    provider: PostgresProtectedDocumentProvider
    materials: DocumentPlaneMaterials = field(repr=False)

    @property
    def tenant(self) -> str:
        return self.materials.manifest.tenant

    def service(self, resolver) -> object:  # type: ignore[no-untyped-def]
        """The General-zone `DocumentService` factory (metadata, never bytes)."""
        from maezo.gateway.documents.service import DocumentService

        if resolver.settings.tenant != self.tenant:
            raise DocumentMaterialError()
        return DocumentService(resolver, self.authority, self.provider)

    def phi_service(self, resolver) -> object:  # type: ignore[no-untyped-def]
        """The PHI application factory (the one route that serves verified bytes)."""
        from maezo.portal.api.document_phi import PhiDocumentService

        if resolver.settings.tenant != self.tenant:
            raise DocumentMaterialError()
        return PhiDocumentService(resolver, self.authority, self.provider)


def compose_document_plane(materials: DocumentPlaneMaterials) -> DocumentPlane:
    """Pure wiring over an already-verified bundle; no I/O, no refusal deferred.

    Every disagreement this function can detect is refused here, at composition time —
    a plane whose keys, reader and store disagree about the tenant must never start.
    """
    manifest = materials.manifest
    if (
        manifest.native.scope.tenant != manifest.tenant
        or materials.keys.scope.tenant != manifest.tenant
        or materials.keys.scope.environment != manifest.environment
        or materials.reader.binding.scope != manifest.native.scope
    ):
        raise DocumentMaterialError()
    authority = PublishedDocumentAuthority(materials.reader)
    provider = PostgresProtectedDocumentProvider(
        materials.store_engine,
        scope=materials.scope,
        keys=materials.keys,
        heads=materials.reader,
        seconds=manifest.seconds,
    )
    return DocumentPlane(authority=authority, provider=provider, materials=materials)


def load_document_materials(directory_setting: str | None, pin: DocumentPlanePin) -> DocumentPlaneMaterials:
    """Read the single fixed document directory; no search path, no fallback.

    `pin` is not optional: without it the directory would be the only authority on its
    own contents. The manifest digest, the plane version and the tenant are all compared
    BEFORE the key file is opened.
    """
    try:
        if directory_setting != DOCUMENT_MATERIAL_DIRECTORY:
            raise DocumentMaterialError()
        root = Path(directory_setting)
        raw = protected_bytes(root / "manifest.json")
        if hashlib.sha256(raw).hexdigest() != pin.public_manifest_sha256:
            raise DocumentMaterialError()
        manifest = DocumentPlaneManifest.model_validate_json(raw, strict=True)
        if (
            manifest.plane_version_id != pin.plane_version_id
            or manifest.tenant != pin.tenant
            or manifest.key_path.parent != root
            or manifest.native.scope.tenant != manifest.tenant
            or not manifest.issued_at <= datetime.now(UTC) < manifest.valid_until
        ):
            raise DocumentMaterialError()
        key = protected_bytes(manifest.key_path, manifest.key_sha256)
        if len(key) != 32:
            raise DocumentMaterialError()
        keys = PhiDocumentKeys(
            scope=DocumentScope(tenant=manifest.tenant, environment=manifest.environment),
            active_key_id=manifest.key_id,
            keys={manifest.key_id: key},
            valid_until=manifest.valid_until,
        )
        store_engine, reader_engine = _engines(manifest)
        reader = NativeAuthReader(reader_engine, manifest.native)
        materials = DocumentPlaneMaterials(
            manifest=manifest,
            keys=keys,
            reader=reader,
            store_engine=store_engine,
            engines=(store_engine, reader_engine),
        )
    except DocumentMaterialError:
        raise
    except Exception:
        # A malformed or unreadable bundle is a deployment fault with no degraded mode.
        raise DocumentMaterialError() from None
    return materials


def _engines(manifest: DocumentPlaneManifest) -> tuple[AsyncEngine, AsyncEngine]:
    from sqlalchemy.engine import make_url

    engines = []
    for url in (manifest.store_url, manifest.reader_url):
        parsed = make_url(url.get_secret_value())
        if (
            parsed.drivername != "postgresql+asyncpg"
            or not parsed.host
            or not parsed.database
            or parsed.query
        ):
            raise DocumentMaterialError()
        engines.append(
            create_async_engine(
                parsed,
                hide_parameters=True,
                echo=False,
                pool_size=2,
                max_overflow=0,
                connect_args={"ssl": ssl.create_default_context()},
            )
        )
    return engines[0], engines[1]


__all__ = [
    "DOCUMENT_MATERIAL_DIRECTORY",
    "DocumentMaterialError",
    "DocumentPlane",
    "DocumentPlaneManifest",
    "DocumentPlaneMaterials",
    "DocumentPlanePin",
    "compose_document_plane",
    "load_document_materials",
]
