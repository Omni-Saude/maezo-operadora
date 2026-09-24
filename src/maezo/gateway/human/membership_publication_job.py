"""T1.5 — publish the staff Q2 authority (catalog once, memberships per revision) to the engine.

`/cases` needs two things in the native read plane before any staff operation passes:
the designated catalog (`PortalReadStore.catalog` refuses without one) and each staff
principal's membership. Nobody published either. This module supplies the concrete
sources `PortalReadPublisher` requires and the job that drives it:

* `PostgresMembershipHandshake` freezes one live `amh.portal_memberships` row and states
  the provenance exactly as the Java provider re-derives it (T1.7b contract, plan §3.1):
  `source_digest = SHA-256(JCS(MembershipProjection))`,
  `receipt_ref = portal-identity:<tenant>:membership:<principal_ref>@<revision>`,
  `valid_until = observed_at + observation_seconds`;
* `StaffCatalogPublicationSource` designates the MINIMAL staff catalog (`entries=[]`,
  `policies=[]`, `forms=[]`) and refuses unless its digest is the one admitted;
* `resource`, `pagto` and `revocation` always refuse (Onda 8) — fail-closed, never a stub
  that answers;
* each staff principal goes to `/v1/authority` (`human-authority.v1`, operation
  `principal`, `AuthorityCommand.java`) BEFORE its membership, so the witness join
  (`mzo_portal_read_membership` x `mzo_human_principal`) never sees a membership without
  its principal. A revoked or expired record is published `active=false` once, and only
  if an active one was published before. Same tenant counter (`MZO_HUMAN_TENANT.REV_`)
  and the same ledger as the read plane, so the CAS rule is one;
* `PublicationLedger` is the job's CAS memory: a durable record of what was committed at
  which authority revision, so a second run with nothing changed publishes nothing.

Every refusal is `ReadRefusalError` (`unavailable()`); nothing here logs a DSN, key or row.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import OpaqueRef, Revision, Sha256Digest
from maezo.portal.engine.profile import canonicalize

from .models import Closed
from .queue import ReadRefusalError
from .read_credentials import unavailable
from .read_profile import (
    CatalogDesignation,
    ReadCatalogArtifact,
    SourceProvenance,
    digest,
    parse_model,
    wire,
)
from .read_publisher import (
    DeploymentCatalogPublicationSource,
    MembershipPublicationHandshake,
    PagtoEvidencePublicationSource,
    PostgresMembershipPublicationSource,
    PublicationReceipt,
    ReadRevocationPublicationSource,
    ResourcePolicyPublicationSource,
    SourceFreezeLease,
    SourceSnapshot,
    membership_projection,
)

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def _prefix(value: str) -> str:
    # Same rule as the provider's admission (`AdmissionRecord.java:102-105`): the prefix ends
    # at a segment separator, so `...:amh:` never admits `...:amhx:...`.
    if not value or not value.endswith(":") or any(c.isspace() or c in "/?#" for c in value):
        raise unavailable()
    return value


# --- membership -------------------------------------------------------------------------


class PostgresMembershipHandshake(MembershipPublicationHandshake):
    """Freeze one committed `portal_memberships` row; the source re-reads and compares it."""

    def __init__(
        self,
        *,
        store: PostgresIdentityStore,
        publisher_ref: str,
        source_ref_prefix: str,
        observation_seconds: int,
        clock: Clock = _now,
    ) -> None:
        if (
            not isinstance(store, PostgresIdentityStore)
            or type(observation_seconds) is not int
            or not 60 <= observation_seconds <= 900
        ):
            raise unavailable()
        self._store, self._publisher = store, publisher_ref
        self._prefix, self._seconds, self._clock = _prefix(source_ref_prefix), observation_seconds, clock

    async def freeze(self, issuer: str, subject: str) -> SourceFreezeLease:
        record = await self._store.get_membership(issuer, subject)
        if (
            record is None
            or record.tenant != self._store.tenant
            or record.issuer != issuer
            or record.subject != subject
        ):
            raise unavailable()
        try:
            record = MembershipRecord.model_validate(record)
        except Exception:
            raise unavailable() from None
        frozen = canonicalize(wire(record))
        projection = membership_projection(record)
        observed = self._clock()
        until = observed + timedelta(seconds=self._seconds)
        provenance = SourceProvenance(
            publisher_ref=self._publisher,
            source_ref=self._prefix + record.principal_ref,
            source_revision=record.revision,
            source_digest=digest(projection),
            receipt_ref=f"portal-identity:{record.tenant}:membership:{record.principal_ref}@{record.revision}",
            observed_at=observed,
            valid_until=until,
        )
        state: dict[str, Any] = {"dead": False}

        def verify(raw: bytes) -> None:
            # The row read again after the freeze must be byte-identical to the frozen one.
            if state["dead"] or raw != frozen:
                state["dead"] = True
                raise unavailable()

        def live() -> None:
            if state["dead"] or not observed <= self._clock() < until:
                raise unavailable()

        def committed(receipt: PublicationReceipt) -> None:
            state["dead"] = True

        def uncertain() -> None:
            state["dead"] = True

        return SourceFreezeLease(provenance, verify, live, committed, uncertain)


# --- catalog ----------------------------------------------------------------------------


def staff_catalog_artifact(
    *, catalog_ref: str, publisher_ref: str, deployment_receipt_ref: str, deployment_receipt_digest: str
) -> bytes:
    """The minimal staff catalog, JCS bytes. Its SHA-256 is what the admission must carry."""
    artifact = parse_model(
        ReadCatalogArtifact,
        {
            "schema": "portal-read-catalog.v1",
            "catalog_ref": catalog_ref,
            "publisher_ref": publisher_ref,
            "entries": [],
            "policies": [],
            "forms": [],
            "deployment_receipt_ref": deployment_receipt_ref,
            "deployment_receipt_digest": deployment_receipt_digest,
        },
    )
    return canonicalize(wire(artifact))


class StaffCatalogConfig(Closed):
    catalog_ref: OpaqueRef
    catalog_revision: Revision = Field(ge=1)
    admitted_catalog_digest: Sha256Digest
    deployment_receipt_ref: OpaqueRef
    deployment_receipt_digest: Sha256Digest
    source_ref_prefix: OpaqueRef
    valid_seconds: int = Field(ge=60, le=14 * 86400)


class StaffCatalogPublicationSource(DeploymentCatalogPublicationSource):
    def __init__(self, *, config: StaffCatalogConfig, publisher_ref: str, clock: Clock = _now) -> None:
        self._config, self._publisher, self._clock = config, publisher_ref, clock
        self._prefix = _prefix(config.source_ref_prefix)
        self.raw = staff_catalog_artifact(
            catalog_ref=config.catalog_ref,
            publisher_ref=publisher_ref,
            deployment_receipt_ref=config.deployment_receipt_ref,
            deployment_receipt_digest=config.deployment_receipt_digest,
        )
        # Fail closed at construction: a catalog the admission does not name is never offered.
        if hashlib.sha256(self.raw).hexdigest() != config.admitted_catalog_digest:
            raise unavailable()

    async def read(self, catalog_ref: str) -> SourceSnapshot:
        c = self._config
        if catalog_ref != c.catalog_ref:
            raise unavailable()
        observed = self._clock()
        until = observed + timedelta(seconds=c.valid_seconds)
        payload = CatalogDesignation(
            catalog_ref=c.catalog_ref,
            catalog_revision=c.catalog_revision,
            catalog_digest=c.admitted_catalog_digest,
            catalog_artifact_base64=base64.b64encode(self.raw).decode("ascii"),
            deployment_receipt_ref=c.deployment_receipt_ref,
            deployment_receipt_digest=c.deployment_receipt_digest,
            valid_until=until,
        )
        provenance = SourceProvenance(
            publisher_ref=self._publisher,
            source_ref=self._prefix + c.catalog_ref,
            source_revision=c.catalog_revision,
            # Stable across runs (valid_until is not in it): the ledger's idempotency key is
            # (catalog_ref, catalog_revision, catalog digest). Renewal = bump catalog_revision.
            source_digest=c.admitted_catalog_digest,
            receipt_ref=f"{c.deployment_receipt_ref}@{c.catalog_revision}",
            observed_at=observed,
            valid_until=until,
        )
        state = {"dead": False}

        def verify(raw: bytes) -> None:
            raise unavailable()  # the catalog source never verifies a membership row

        def live() -> None:
            if state["dead"] or not observed <= self._clock() < until:
                raise unavailable()

        def end(*_: object) -> None:
            state["dead"] = True

        return SourceSnapshot(provenance, payload, SourceFreezeLease(provenance, verify, live, end, end))


# --- Onda 8: always refuse ----------------------------------------------------------------


class RefusingResourceSource(ResourcePolicyPublicationSource):
    async def read(self, task_id: str) -> SourceSnapshot:
        raise unavailable()


class RefusingPagtoSource(PagtoEvidencePublicationSource):
    async def read(self, evidence_ref: str, revision: int, digest: str) -> Any:
        raise unavailable()


class RefusingRevocationSource(ReadRevocationPublicationSource):
    async def read_catalog(self, catalog_ref: str) -> SourceSnapshot:
        raise unavailable()

    async def read_key(self, fingerprint: str) -> SourceSnapshot:
        raise unavailable()


# --- ledger (CAS memory) ------------------------------------------------------------------


class AuthorityReceipt(Closed):
    schema_: Literal["human-authority-receipt.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    revision: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    digest: Sha256Digest


def principal_body(record: MembershipRecord, *, now: datetime, grace_seconds: int) -> dict[str, Any]:
    """The `principal` fields `AuthorityCommand` accepts, minus the CAS envelope fields.

    `groups` is the sorted union of the membership bindings' groups: exactly what the witness
    compares (`staff_cases/postgres.py`, `set(groups) == union(binding.groups)`). An inactive
    principal still needs `valid_until > now` on the engine, so it carries a short grace window.
    """
    active = not record.revoked and record.reviewed_until > now
    until = record.reviewed_until if active else now + timedelta(seconds=grace_seconds)
    return dict(
        principal_ref=record.principal_ref,
        issuer=record.issuer,
        subject=record.subject,
        active=active,
        valid_until=str(int(until.timestamp())),
        groups=sorted({group for binding in record.memberships for group in binding.groups}),
    )


def principal_digest(body: dict[str, Any]) -> str:
    """Idempotency key: the body, minus the `valid_until` of an inactive one (it moves each run)."""
    stable = body if body["active"] else {k: v for k, v in body.items() if k != "valid_until"}
    return hashlib.sha256(canonicalize(stable)).hexdigest()


class LedgerEntry(Closed):
    kind: Literal["membership", "catalog-designate", "principal"]
    source_ref: OpaqueRef
    source_revision: Revision
    source_digest: Sha256Digest
    publication_id: OpaqueRef
    authority_revision: Revision


class LedgerState(Closed):
    schema_: Literal["portal-read-publication-ledger.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    authority_revision: Revision
    entries: tuple[LedgerEntry, ...]


class PublicationLedger:
    """Durable JSON ledger, replaced atomically (tmp + fsync + rename), mode 0600."""

    def __init__(self, path: Path, tenant: str) -> None:
        self.path, self.tenant = Path(path), tenant
        try:
            if self.path.exists():
                if not stat.S_ISREG(self.path.stat().st_mode):
                    raise ValueError
                value = json.loads(self.path.read_bytes())
            else:
                value = {
                    "schema": "portal-read-publication-ledger.v1",
                    "tenant": tenant,
                    "authority_revision": "0",
                    "entries": [],
                }
            state = parse_model(LedgerState, value)
        except Exception:
            raise unavailable() from None
        if state.tenant != tenant:
            raise unavailable()
        self.state = state

    @property
    def authority_revision(self) -> int:
        return self.state.authority_revision

    def rebase(self, revision: int) -> None:
        """Operator override after an out-of-band advance (other publishers share the counter)."""
        self._write(self.state.model_copy(update={"authority_revision": revision}))

    def published(self, source: SourceProvenance, kind: str) -> bool:
        return self.has(kind, source.source_ref, source.source_revision, source.source_digest)

    def has(self, kind: str, ref: str, revision: int, source_digest: str) -> bool:
        return any(
            e.kind == kind
            and e.source_ref == ref
            and e.source_revision == revision
            and e.source_digest == source_digest
            for e in self.state.entries
        )

    def entry(self, kind: str, ref: str) -> LedgerEntry | None:
        return next((e for e in self.state.entries if (e.kind, e.source_ref) == (kind, ref)), None)

    def record_principal(
        self, ref: str, revision: int, source_digest: str, receipt: AuthorityReceipt
    ) -> None:
        """`/v1/authority` answers `human-authority-receipt.v1`: same CAS rule as the read plane."""
        if int(receipt.revision) != self.state.authority_revision + 1:
            raise unavailable()
        entry = LedgerEntry(
            kind="principal",
            source_ref=ref,
            source_revision=revision,
            source_digest=source_digest,
            publication_id="authority-" + receipt.digest,
            authority_revision=int(receipt.revision),
        )
        kept = tuple(e for e in self.state.entries if (e.kind, e.source_ref) != ("principal", ref))
        self._write(
            self.state.model_copy(
                update={"authority_revision": entry.authority_revision, "entries": (*kept, entry)}
            )
        )

    def record(self, kind: str, source: SourceProvenance, receipt: PublicationReceipt) -> None:
        if receipt.kind != kind or receipt.authority_revision != self.state.authority_revision + 1:
            raise unavailable()
        entry = LedgerEntry(
            kind=kind,  # type: ignore[arg-type]
            source_ref=source.source_ref,
            source_revision=source.source_revision,
            source_digest=source.source_digest,
            publication_id=receipt.publication_id,
            authority_revision=receipt.authority_revision,
        )
        kept = tuple(e for e in self.state.entries if (e.kind, e.source_ref) != (kind, source.source_ref))
        self._write(
            self.state.model_copy(
                update={"authority_revision": receipt.authority_revision, "entries": (*kept, entry)}
            )
        )

    def _write(self, state: LedgerState) -> None:
        raw = canonicalize(wire(state))
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ledger-")
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.state = state


# --- job ----------------------------------------------------------------------------------


class Publisher(Protocol):
    def publish_membership(
        self, issuer: str, subject: str, *, expected_revision: int
    ) -> Awaitable[PublicationReceipt]: ...

    def publish_catalog(
        self, catalog_ref: str, *, expected_revision: int
    ) -> Awaitable[PublicationReceipt]: ...

    def publish_principal(self, raw: bytes) -> Awaitable[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class JobResult:
    catalog_published: bool
    memberships_published: int
    memberships_unchanged: int
    authority_revision: int
    principals_published: int


class MembershipPublicationJob:
    """Catalog first (once), then every staff membership whose revision/digest is not committed."""

    def __init__(
        self,
        *,
        publisher: Publisher,
        ledger: PublicationLedger,
        catalog: StaffCatalogPublicationSource,
        handshake: PostgresMembershipHandshake,
        store: PostgresIdentityStore,
        engine: AsyncEngine,
        workload_ref: str,
        grace_seconds: int = 300,
        clock: Clock = _now,
    ) -> None:
        if ledger.tenant != store.tenant or not workload_ref or not 60 <= grace_seconds <= 900:
            raise unavailable()
        self._publisher, self._ledger, self._catalog = publisher, ledger, catalog
        self._handshake, self._store, self._engine = handshake, store, engine
        self._workload_ref, self._grace, self._clock = workload_ref, grace_seconds, clock

    async def _principal(self, record: MembershipRecord) -> bool:
        """Publish the principal to `/v1/authority` unless this exact state is committed."""
        ledger = self._ledger
        body = principal_body(record, now=self._clock(), grace_seconds=self._grace)
        key = principal_digest(body)
        if ledger.has("principal", record.principal_ref, record.revision, key):
            return False
        if not body["active"] and ledger.entry("principal", record.principal_ref) is None:
            return False  # nothing to deactivate: an inactive principal is never created
        raw = canonicalize(
            dict(
                schema="human-authority.v1",
                tenant=self._store.tenant,
                workload_ref=self._workload_ref,
                operation="principal",
                expected_revision=str(ledger.authority_revision),
                **body,
            )
        )
        try:
            receipt = parse_model(AuthorityReceipt, await self._publisher.publish_principal(raw))
        except ReadRefusalError:
            raise
        except Exception:
            raise unavailable() from None
        if receipt.tenant != self._store.tenant or receipt.digest != hashlib.sha256(raw).hexdigest():
            raise unavailable()
        ledger.record_principal(record.principal_ref, record.revision, key, receipt)
        return True

    async def _principals(self) -> list[tuple[str, str]]:
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT issuer, subject FROM portal_memberships WHERE tenant=:tenant "
                    "ORDER BY issuer, subject"
                ),
                {"tenant": self._store.tenant},
            )
            return [(r[0], r[1]) for r in rows]

    async def run(self) -> JobResult:
        ledger = self._ledger
        catalog_ref = self._catalog._config.catalog_ref
        catalog = await self._catalog.read(catalog_ref)
        catalog_done = ledger.published(catalog.source, "catalog-designate")
        catalog.lease.uncertain()  # this snapshot was only the idempotency probe
        if not catalog_done:
            receipt = await self._publisher.publish_catalog(
                catalog_ref, expected_revision=ledger.authority_revision
            )
            ledger.record("catalog-designate", catalog.source, receipt)
        published = unchanged = principals = 0
        for issuer, subject in await self._principals():
            record = await self._store.get_membership(issuer, subject)
            if record is None or record.audience != "staff":
                continue  # only the staff plane is published here (Onda 1)
            principals += await self._principal(record)
            probe = await self._handshake.freeze(issuer, subject)
            probe.uncertain()
            if ledger.published(probe.provenance, "membership"):
                unchanged += 1
                continue
            receipt = await self._publisher.publish_membership(
                issuer, subject, expected_revision=ledger.authority_revision
            )
            ledger.record("membership", probe.provenance, receipt)
            published += 1
        return JobResult(not catalog_done, published, unchanged, ledger.authority_revision, principals)


# --- __main__ -----------------------------------------------------------------------------


class JobConfig(Closed):
    schema_: Literal["portal-membership-publication-job.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    identity_dsn_file: str
    ledger_file: str
    membership_source_ref_prefix: OpaqueRef
    observation_seconds: int = Field(ge=60, le=900)
    catalog: StaffCatalogConfig
    authority: AuthorityKeyConfig


class AuthorityKeyConfig(Closed):
    """The `human-authority` key: its OWN key, registered in the engine trust for this workload's
    TLS client SPKI (`Envelope.verify` binds key to peer). Never the read or assignment key."""

    key_file: str
    key_id: OpaqueRef
    audience: OpaqueRef
    fingerprint: Sha256Digest
    not_after: datetime
    max_envelope_seconds: int = Field(ge=1, le=60)


class JobPublisher:
    """Read-plane publications through `PortalReadPublisher`; the principal through `/v1/authority`."""

    def __init__(self, read: Any, authority: Any) -> None:
        self._read, self._authority = read, authority

    def publish_membership(self, issuer: str, subject: str, *, expected_revision: int):  # type: ignore[no-untyped-def]
        return self._read.publish_membership(issuer, subject, expected_revision=expected_revision)

    def publish_catalog(self, catalog_ref: str, *, expected_revision: int):  # type: ignore[no-untyped-def]
        return self._read.publish_catalog(catalog_ref, expected_revision=expected_revision)

    def publish_principal(self, raw: bytes):  # type: ignore[no-untyped-def]
        return self._authority.publish_principal(raw)


def authority_lease(config: AuthorityKeyConfig, scope: Any, lifetime: Any) -> Any:
    """Build the `human-authority` signing lease; any mismatch refuses before a request exists."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from .credentials import AssignmentSigningLease

    try:
        key = serialization.load_pem_private_key(_secret_file(config.key_file), password=None)
    except Exception:
        raise unavailable() from None
    if not isinstance(key, Ed25519PrivateKey):
        raise unavailable()
    spki = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    if hashlib.sha256(spki).hexdigest() != config.fingerprint or config.not_after.tzinfo is None:
        raise unavailable()
    return AssignmentSigningLease(
        scope=scope,
        key_id=config.key_id,
        purpose="human-authority",
        audience=config.audience,
        fingerprint=config.fingerprint,
        not_before=_now() - timedelta(seconds=1),
        not_after=config.not_after,
        max_envelope_seconds=config.max_envelope_seconds,
        _key=key,
        _live=lifetime.bounded(config.not_after),
    )


def _secret_file(path: str) -> bytes:
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and info.st_mode & 0o077):
        raise unavailable()
    return Path(path).read_bytes()


def load_config(path: str) -> JobConfig:
    return parse_model(JobConfig, json.loads(Path(path).read_bytes()))


async def _publish(config: JobConfig, *, rebase: int | None) -> JobResult:  # pragma: no cover - composition
    from sqlalchemy.ext.asyncio import create_async_engine

    from .assignment_transport import AssignmentPrivateTransport
    from .models import Scope
    from .production import _surface_context
    from .production_materials import MATERIAL_DIRECTORY, HumanMaterialPin, load_human_materials
    from .read_credentials import ReadCredentialPartition
    from .read_materials import MaterialLifetime, read_providers
    from .read_publisher import PortalReadPublisher
    from .read_transport import PortalReadClient

    pin = HumanMaterialPin(
        tenant=config.tenant,
        material_version_id=os.environ["MAEZO_HUMAN_MATERIAL_VERSION_ID"],
        public_manifest_sha256=os.environ["MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256"],
    )
    lifetime = MaterialLifetime()
    material = load_human_materials(MATERIAL_DIRECTORY, pin)
    manifest = material.manifest
    scope = manifest.scope
    if scope.tenant != config.tenant:
        raise unavailable()
    admission, credentials, _ = read_providers(material, lifetime)
    context = _surface_context(manifest.read_surface, scope, material.directory)
    dsn = _secret_file(config.identity_dsn_file).decode("utf-8").strip()
    engine = create_async_engine(dsn, hide_parameters=True, pool_size=1, max_overflow=0)
    client = PortalReadClient(
        origin=manifest.read_surface.origin,
        tls_context=context,
        server_spki_sha256=manifest.read_surface.server_spki_sha256,
        partition=ReadCredentialPartition(
            scope=scope,
            engine_name=manifest.engine_name,
            key_id=manifest.key("portal-task-read").key_id,
            credentials=credentials,
            admission=admission,
        ),
        timeout_seconds=manifest.read_surface.timeout_seconds,
    )
    authority_client = AssignmentPrivateTransport(
        origin=manifest.read_surface.origin,
        tls_context=context,
        server_spki_sha256=manifest.read_surface.server_spki_sha256,
        signing=authority_lease(
            config.authority,
            Scope(tenant=scope.tenant, environment=scope.environment, workload_ref=scope.workload_ref),
            lifetime,
        ),
        timeout_seconds=manifest.read_surface.timeout_seconds,
    )
    try:
        store = PostgresIdentityStore(config.tenant, engine)
        handshake = PostgresMembershipHandshake(
            store=store,
            publisher_ref=scope.workload_ref,
            source_ref_prefix=config.membership_source_ref_prefix,
            observation_seconds=config.observation_seconds,
        )
        membership = PostgresMembershipPublicationSource(store=store, handshake=handshake)
        catalog = StaffCatalogPublicationSource(config=config.catalog, publisher_ref=scope.workload_ref)
        read_publisher = PortalReadPublisher(
            client=client,
            membership=membership,
            catalog=catalog,
            resource=RefusingResourceSource(),
            pagto=RefusingPagtoSource(),
            revocation=RefusingRevocationSource(),
        )
        publisher = JobPublisher(read_publisher, authority_client)
        ledger = PublicationLedger(Path(config.ledger_file), config.tenant)
        if rebase is not None:
            ledger.rebase(rebase)
        return await MembershipPublicationJob(
            publisher=publisher,
            ledger=ledger,
            catalog=catalog,
            handshake=handshake,
            store=store,
            engine=engine,
            workload_ref=scope.workload_ref,
        ).run()
    finally:
        await authority_client.aclose()
        await client.close()
        lifetime.close()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m maezo.gateway.human.membership_publication_job")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("catalog-digest", help="print the SHA-256 the admission must carry")
    d.add_argument("--catalog-ref", required=True)
    d.add_argument("--publisher-ref", required=True)
    d.add_argument("--deployment-receipt-ref", required=True)
    d.add_argument("--deployment-receipt-digest", required=True)
    p = sub.add_parser("publish", help="publish the catalog once and memberships per revision")
    p.add_argument("--config", required=True)
    p.add_argument("--authority-revision", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog-digest":
            raw = staff_catalog_artifact(
                catalog_ref=args.catalog_ref,
                publisher_ref=args.publisher_ref,
                deployment_receipt_ref=args.deployment_receipt_ref,
                deployment_receipt_digest=args.deployment_receipt_digest,
            )
            print(hashlib.sha256(raw).hexdigest())
            return 0
        result = asyncio.run(_publish(load_config(args.config), rebase=args.authority_revision))
        print(json.dumps(asdict(result)))
        return 0
    except Exception as exc:  # fail closed, never echo material
        print(f"publication refused: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
