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
* `resource` (H2, D-N) is the REAL source of `task_publication_source.py`: the live User Tasks
  the Q2 admission names in its `human` block, each with its evidence then its resource, by the
  vector contract; enabled only by the `tasks` block of the configuration. With it the catalog
  is the admitted one WITH the task entries (`catalog.artifact_file`), not the minimal one;
* `pagto` and `revocation` always refuse (out of this wave) — fail-closed, never a stub that
  answers;
* each staff principal goes to `/v1/authority` (`human-authority.v1`, operation
  `principal`, `AuthorityCommand.java`) BEFORE its membership, so the witness join
  (`mzo_portal_read_membership` x `mzo_human_principal`) never sees a membership without
  its principal. A revoked or expired record is published `active=false` once, and only
  if an active one was published before. Same tenant counter (`MZO_HUMAN_TENANT.REV_`)
  and the same ledger as the read plane, so the CAS rule is one;
* `PublicationLedger` is the job's CAS memory: a durable record of what was committed at
  which authority revision, so a second run with nothing changed publishes nothing;
* the tenant counter is SHARED (staff issuer, assignment relay, AUTH intake, other job runs):
  with `native_source` the job reads the real `MZO_HUMAN_TENANT.REV_` before each publication and
  follows it forward (D13 of C1: the ledger fell behind after the issuer and every renewal
  inside the cycle failed rc=2 until an operator rebased it). A counter BELOW the ledger is a
  different database or a restore: that refuses;
* RENEWAL: the membership source lives `observation_seconds` (<= 15 min) and the catalog
  designation `catalog.valid_seconds`; after that the engine refuses `/cases` (503). An entry
  committed with the same content is therefore "fresh" only while more than HALF its validity
  remains; past that the job republishes it with a new validity. A membership keeps its
  revision (the engine accepts a same-revision republish only with an identical payload and a
  later source `valid_until`); the catalog goes to revision + 1 with the same bytes/digest,
  because the engine keeps catalog revisions as insert-only history. Idempotency by content
  is kept inside the first half of the window.

CADENCE the deploy must run `publish` at: strictly less than half the shortest validity,
i.e. `interval < min(observation_seconds, catalog.valid_seconds) / 2` minus scheduling jitter.
With `observation_seconds=600` run it every 4-5 min (CronJob `*/4 * * * *` is the safe
default; `*/5` leaves 5 min of margin only if the run itself is punctual). A failed run exits
2: alert on it, because two consecutive misses let the membership source expire.

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
from maezo.portal.engine.profile import canonicalize, strict_loads

from .models import Closed
from .queue import ReadRefusalError
from .read_credentials import unavailable
from .read_profile import (
    CatalogDesignation,
    ReadCatalogArtifact,
    ReadCatalogEntry,
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
from .task_publication_source import (
    NativeTaskSource,
    TaskAdmission,
    TaskResourceSource,
    catalog_entries,
    evidence_command,
    evidence_digest,
    evidence_ref,
    grants_for,
    resource_key,
    resource_projection,
    resource_provenance,
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
    #: H2: the admitted catalog WITH the human task entries (JCS bytes of `portal-read-catalog.v1`,
    #: built from the deployed facts). Absent, the job designates the minimal staff catalog.
    artifact_file: str | None = None


def _task_catalog(raw: bytes, config: StaffCatalogConfig, publisher_ref: str) -> bytes:
    """The catalog file must be the canonical artifact of THIS catalog, receipt and publisher."""
    try:
        artifact = parse_model(ReadCatalogArtifact, strict_loads(raw))
    except Exception:
        raise unavailable() from None
    if (
        canonicalize(wire(artifact)) != raw
        or artifact.catalog_ref != config.catalog_ref
        or artifact.publisher_ref != publisher_ref
        or artifact.deployment_receipt_ref != config.deployment_receipt_ref
        or artifact.deployment_receipt_digest != config.deployment_receipt_digest
    ):
        raise unavailable()
    return raw


class StaffCatalogPublicationSource(DeploymentCatalogPublicationSource):
    def __init__(self, *, config: StaffCatalogConfig, publisher_ref: str, clock: Clock = _now) -> None:
        self._config, self._publisher, self._clock = config, publisher_ref, clock
        #: The revision offered; the job raises it (never below the configured one) to renew an
        #: unchanged catalog, because the engine keeps catalog revisions as insert-only history.
        self.revision = config.catalog_revision
        self._prefix = _prefix(config.source_ref_prefix)
        if config.artifact_file is None:
            self.raw = staff_catalog_artifact(
                catalog_ref=config.catalog_ref,
                publisher_ref=publisher_ref,
                deployment_receipt_ref=config.deployment_receipt_ref,
                deployment_receipt_digest=config.deployment_receipt_digest,
            )
        else:
            self.raw = _task_catalog(Path(config.artifact_file).read_bytes(), config, publisher_ref)
        # Fail closed at construction: a catalog the admission does not name is never offered.
        if hashlib.sha256(self.raw).hexdigest() != config.admitted_catalog_digest:
            raise unavailable()

    async def read(self, catalog_ref: str) -> SourceSnapshot:
        c = self._config
        if catalog_ref != c.catalog_ref or self.revision < c.catalog_revision:
            raise unavailable()
        revision = self.revision
        observed = self._clock()
        until = observed + timedelta(seconds=c.valid_seconds)
        payload = CatalogDesignation(
            catalog_ref=c.catalog_ref,
            catalog_revision=revision,
            catalog_digest=c.admitted_catalog_digest,
            catalog_artifact_base64=base64.b64encode(self.raw).decode("ascii"),
            deployment_receipt_ref=c.deployment_receipt_ref,
            deployment_receipt_digest=c.deployment_receipt_digest,
            valid_until=until,
        )
        provenance = SourceProvenance(
            publisher_ref=self._publisher,
            source_ref=self._prefix + c.catalog_ref,
            source_revision=revision,
            # Stable across runs (valid_until is not in it): the ledger's idempotency key is
            # (catalog_ref, catalog_revision, catalog digest) while the committed validity is in
            # its first half; past that the job re-designates the same bytes at revision + 1.
            source_digest=c.admitted_catalog_digest,
            receipt_ref=f"{c.deployment_receipt_ref}@{revision}",
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
    kind: Literal["membership", "catalog-designate", "principal", "evidence", "resource"]
    source_ref: OpaqueRef
    source_revision: Revision
    source_digest: Sha256Digest
    publication_id: OpaqueRef
    authority_revision: Revision
    #: The committed source validity (absent in ledgers written before renewal existed, which
    #: therefore renew on the next run).
    valid_until: datetime | None = None


def renew_margin(validity_seconds: int) -> timedelta:
    """Republish once no more than half the validity remains (see CADENCE in the module doc)."""
    return timedelta(seconds=validity_seconds / 2)


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
                for entry in value.get("entries", ()):
                    # pre-renewal ledgers have no validity: read it as "unknown" (renews next run)
                    if isinstance(entry, dict):
                        entry.setdefault("valid_until", None)
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

    def observe(self, current: int) -> None:
        """Follow the engine's real tenant counter forward (D13); never backwards.

        The ledger's entries record WHAT was committed; the counter is only the CAS token of the
        next publication. Another publisher advancing it does not invalidate any entry.
        """
        if type(current) is not int or current < self.state.authority_revision:
            raise unavailable()
        if current != self.state.authority_revision:
            self.rebase(current)

    def published(self, source: SourceProvenance, kind: str) -> bool:
        return self.has(kind, source.source_ref, source.source_revision, source.source_digest)

    def fresh(self, source: SourceProvenance, kind: str, *, now: datetime, margin: timedelta) -> bool:
        """Same content committed AND its validity still beyond `margin` from `now`."""
        entry = self.entry(kind, source.source_ref)
        return (
            entry is not None
            and (entry.source_revision, entry.source_digest) == (source.source_revision, source.source_digest)
            and entry.valid_until is not None
            and entry.valid_until - now > margin
        )

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
        self.record_authority("principal", ref, revision, source_digest, receipt)

    def record_authority(
        self,
        kind: str,
        ref: str,
        revision: int,
        source_digest: str,
        receipt: AuthorityReceipt,
        valid_until: datetime | None = None,
    ) -> None:
        if int(receipt.revision) != self.state.authority_revision + 1:
            raise unavailable()
        entry = LedgerEntry(
            kind=kind,  # type: ignore[arg-type]
            source_ref=ref,
            source_revision=revision,
            source_digest=source_digest,
            publication_id="authority-" + receipt.digest,
            authority_revision=int(receipt.revision),
            valid_until=valid_until,
        )
        kept = tuple(e for e in self.state.entries if (e.kind, e.source_ref) != (kind, ref))
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
            valid_until=source.valid_until,
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

    def publish_resource(self, task_id: str, *, expected_revision: int) -> Awaitable[PublicationReceipt]: ...

    def publish_evidence(self, raw: bytes) -> Awaitable[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class JobResult:
    catalog_published: bool
    memberships_published: int
    memberships_unchanged: int
    authority_revision: int
    principals_published: int
    tasks_published: int = 0
    tasks_unchanged: int = 0
    evidence_published: int = 0


@dataclass(frozen=True, slots=True)
class TaskPublication:
    """H2: what the job needs to publish the live tasks (built by `_publish`, or by a test)."""

    native: NativeTaskSource
    admission: TaskAdmission
    entries: dict[tuple[str, str], ReadCatalogEntry]
    source: TaskResourceSource
    #: Evidence validity; renewed once half of it is gone (same rule as the other sources).
    evidence_seconds: int = 6 * 3600


class MembershipPublicationJob:
    """Catalog, then every staff membership whose revision/digest is not committed or whose
    committed validity is in its second half (renewal, see CADENCE in the module doc)."""

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
        counter: Callable[[], Awaitable[int]] | None = None,
        tasks: TaskPublication | None = None,
    ) -> None:
        if ledger.tenant != store.tenant or not workload_ref or not 60 <= grace_seconds <= 900:
            raise unavailable()
        if tasks is not None and (
            counter is None
            or tasks.admission.tenant != store.tenant
            or tasks.admission.publisher_ref != workload_ref
            or tasks.admission.catalog_digest != catalog._config.admitted_catalog_digest
        ):
            raise unavailable()
        self._publisher, self._ledger, self._catalog = publisher, ledger, catalog
        self._handshake, self._store, self._engine = handshake, store, engine
        self._workload_ref, self._grace, self._clock = workload_ref, grace_seconds, clock
        self._counter, self._tasks = counter, tasks
        self._records: list[MembershipRecord] = []

    async def _expected(self) -> int:
        """The CAS token of the next publication: the real tenant counter when it is readable."""
        if self._counter is not None:
            self._ledger.observe(await self._counter())
        return self._ledger.authority_revision

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
                expected_revision=str(await self._expected()),
                **body,
            )
        )
        receipt = await self._authority(raw)
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
        config = self._catalog._config
        catalog_ref = config.catalog_ref
        prefix = self._catalog._prefix
        last = ledger.entry("catalog-designate", prefix + catalog_ref)
        if (
            last is not None
            and last.source_digest == config.admitted_catalog_digest
            and last.source_revision >= config.catalog_revision
        ):
            self._catalog.revision = last.source_revision  # same bytes: probe the committed one
        catalog = await self._catalog.read(catalog_ref)
        catalog_done = ledger.fresh(
            catalog.source,
            "catalog-designate",
            now=self._clock(),
            margin=renew_margin(self._catalog._config.valid_seconds),
        )
        catalog.lease.uncertain()  # this snapshot was only the idempotency probe
        if not catalog_done and ledger.published(catalog.source, "catalog-designate"):
            self._catalog.revision += 1  # renewal: same bytes, next revision (insert-only history)
            catalog = await self._catalog.read(catalog_ref)  # what the ledger records
            catalog.lease.uncertain()
        if not catalog_done:
            receipt = await self._publisher.publish_catalog(
                catalog_ref, expected_revision=await self._expected()
            )
            ledger.record("catalog-designate", catalog.source, receipt)
        published = unchanged = principals = 0
        for issuer, subject in await self._principals():
            record = await self._store.get_membership(issuer, subject)
            if record is None or record.audience != "staff":
                continue  # only the staff plane is published here (Onda 1)
            self._records.append(record)
            principals += await self._principal(record)
            probe = await self._handshake.freeze(issuer, subject)
            probe.uncertain()
            if ledger.fresh(
                probe.provenance,
                "membership",
                now=self._clock(),
                margin=renew_margin(self._handshake._seconds),
            ):
                unchanged += 1
                continue
            receipt = await self._publisher.publish_membership(
                issuer, subject, expected_revision=await self._expected()
            )
            ledger.record("membership", probe.provenance, receipt)
            published += 1
        tasks = await self._publish_tasks() if self._tasks is not None else (0, 0, 0)
        return JobResult(
            not catalog_done, published, unchanged, ledger.authority_revision, principals, *tasks
        )

    async def _authority(self, raw: bytes) -> AuthorityReceipt:
        try:
            publish = (
                self._publisher.publish_evidence
                if strict_loads(raw).get("operation") == "evidence"
                else self._publisher.publish_principal
            )
            receipt = parse_model(AuthorityReceipt, await publish(raw))
        except ReadRefusalError:
            raise
        except Exception:
            raise unavailable() from None
        if receipt.tenant != self._store.tenant or receipt.digest != hashlib.sha256(raw).hexdigest():
            raise unavailable()
        return receipt

    async def _publish_tasks(self) -> tuple[int, int, int]:
        """H2: evidence (when absent/changed/half-expired) then resource for every admitted live task."""
        t = self._tasks
        assert t is not None
        ledger, tenant = self._ledger, self._store.tenant
        published = unchanged = evidences = 0
        keys = {key for _, key in t.admission.entries}
        for task in await t.native.live_tasks(keys):
            key = (task.process_definition_id, task.task_definition_key)
            human, entry = t.admission.entries.get(key), t.entries.get(key)
            if human is None or entry is None or not t.admission.task_id_ok(human, task.task_id):
                continue  # another definition/version, or an id outside the admitted format
            now = self._clock()
            evidence = await t.native.evidence(task.task_id)
            wanted_digest = evidence_digest(task, entry, t.admission.catalog_digest)
            if (
                evidence is None
                or evidence.ref != evidence_ref(tenant, task.task_id)
                or evidence.digest != wanted_digest
                or evidence.process_definition_id != task.process_definition_id
                or evidence.valid_until - now.timestamp() <= t.evidence_seconds / 2
            ):
                until = min(now + timedelta(seconds=t.evidence_seconds), t.admission.valid_until)
                raw = evidence_command(
                    tenant=tenant,
                    workload_ref=self._workload_ref,
                    expected_revision=await self._expected(),
                    task=task,
                    entry=entry,
                    catalog_digest=t.admission.catalog_digest,
                    valid_until=until,
                )
                attested = await self._authority(raw)
                ledger.record_authority(
                    "evidence",
                    evidence_ref(tenant, task.task_id),
                    int(attested.revision),
                    wanted_digest,
                    attested,
                    until,
                )
                evidences += 1
                evidence = await t.native.evidence(task.task_id)
                if evidence is None or evidence.revision != int(attested.revision):
                    raise unavailable()
            current = await t.native.task(task.task_id)  # the evidence bumped the task revision
            if current is None or current.process_definition_id != task.process_definition_id:
                continue  # completed or migrated meanwhile: nothing to publish for it
            valid = min(t.admission.valid_until, datetime.fromtimestamp(evidence.valid_until, UTC))
            grants = grants_for(
                self._records, entry, tenant=tenant, task_id=task.task_id, now=now, valid_until=valid
            )
            source_ref = t.admission.source_ref_prefix + task.task_id
            content = resource_key(current, entry, evidence, grants)
            last = ledger.entry("resource", source_ref)
            if (
                last is not None
                and last.source_digest == content
                and last.valid_until is not None
                and last.valid_until - now > renew_margin(t.admission.observation_seconds)
            ):
                unchanged += 1
                continue
            # Insert-only upward revision (`source_revision` must exceed the stored one): the
            # observation instant in microseconds, above what this ledger already committed.
            revision = max(int(now.timestamp() * 1_000_000), (last.source_revision + 1) if last else 1)
            payload = resource_projection(
                tenant=tenant,
                task=current,
                entry=entry,
                human=human,
                evidence=evidence,
                grants=grants,
                resource_revision=revision,
                valid_until=valid,
            )
            provenance = resource_provenance(payload, tenant=tenant, admission=t.admission, observed_at=now)
            t.source.stage(provenance, payload)
            receipt = await self._publisher.publish_resource(
                task.task_id, expected_revision=await self._expected()
            )
            ledger.record("resource", provenance.model_copy(update={"source_digest": content}), receipt)
            published += 1
        return published, unchanged, evidences


# --- __main__ -----------------------------------------------------------------------------


class AuthorityKeyConfig(Closed):
    """The `human-authority` key: its OWN key, registered in the engine trust for this workload's
    TLS client SPKI (`Envelope.verify` binds key to peer). Never the read or assignment key."""

    key_file: str
    key_id: OpaqueRef
    audience: OpaqueRef
    fingerprint: Sha256Digest
    not_after: datetime
    max_envelope_seconds: int = Field(ge=1, le=60)


class PublicationKeyConfig(Closed):
    """The `portal-read-publication` key: its OWN key and `key_id`, never the portal read key.

    The engine's Q2 trust registers one key per purpose (F8 of C1); the read key in the human
    material signs only `portal-task-read`.
    """

    key_file: str
    key_id: OpaqueRef
    fingerprint: Sha256Digest
    not_after: datetime


class NativeSourceConfig(Closed):
    """D13/H2: the job's OWN read-only login to the engine's rows (column grants of
    `deploy/sql/portal-task-source-grants.sql`): tenant counter, live tasks, task evidence."""

    dsn_file: str
    native_schema: OpaqueRef
    engine_schema: OpaqueRef


class TaskPublicationConfig(Closed):
    """H2: publish the admitted live tasks. `admission_record_file` is the SIGNED Q2 record
    (`portal-read-admission.v1` with `human`, JCS) the engine has installed; its catalog digest
    must be the catalog's `admitted_catalog_digest`."""

    admission_record_file: str
    evidence_seconds: int = Field(ge=600, le=86400)


class JobConfig(Closed):
    schema_: Literal["portal-membership-publication-job.v1"] = Field(alias="schema")
    tenant: OpaqueRef
    identity_dsn_file: str
    ledger_file: str
    membership_source_ref_prefix: OpaqueRef
    observation_seconds: int = Field(ge=60, le=900)
    catalog: StaffCatalogConfig
    #: The job's OWN mTLS client identity towards the engine (F7/F8 of C1): the Q2 trust binds
    #: each key to a distinct peer SPKI, so the job cannot present the portal's read certificate.
    client_certificate_file: str
    client_key_file: str
    publication: PublicationKeyConfig
    authority: AuthorityKeyConfig
    native_source: NativeSourceConfig | None = None
    tasks: TaskPublicationConfig | None = None


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

    def publish_evidence(self, raw: bytes):  # type: ignore[no-untyped-def]
        return self._authority.publish_evidence(raw)

    def publish_resource(self, task_id: str, *, expected_revision: int):  # type: ignore[no-untyped-def]
        return self._read.publish_resource(task_id, expected_revision=expected_revision)


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


def publication_credentials(config: PublicationKeyConfig, material: Any, lifetime: Any) -> Any:
    """The job's `portal-read-publication` provider; any mismatch refuses before a request exists."""
    from cryptography.hazmat.primitives import serialization

    from .read_materials import MaterialPublicationCredentials

    try:
        key = serialization.load_pem_private_key(_secret_file(config.key_file), password=None)
    except Exception:
        raise unavailable() from None
    return MaterialPublicationCredentials(
        material,
        lifetime,
        key=key,  # type: ignore[arg-type]
        key_id=config.key_id,
        key_fingerprint=config.fingerprint,
        not_after=config.not_after,
    )


def job_tls_context(config: JobConfig, surface: Any, scope: Any, directory: Any) -> Any:
    """The engine CA from the attested material; the client certificate is the job's own."""
    import ssl

    from .transport import HumanTLSIdentity

    _secret_file(config.client_key_file)
    context = HumanTLSIdentity(
        scope=scope,
        ca_file=Path(directory) / surface.ca_file,
        certificate_file=Path(config.client_certificate_file),
        private_key_file=Path(config.client_key_file),
    ).context()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise unavailable()
    return context


def _secret_file(path: str) -> bytes:
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and info.st_mode & 0o077):
        raise unavailable()
    return Path(path).read_bytes()


def _public_file(path: str) -> bytes:
    """A deployed public artifact (the signed admission record): a regular file, nothing more."""
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise unavailable()
    return Path(path).read_bytes()


def load_config(path: str) -> JobConfig:
    value = json.loads(Path(path).read_bytes())
    # The optional blocks (D13/H2) absent from a staff-only file read as explicit nulls, so the
    # closed profile's canonical round trip still holds byte for byte.
    if isinstance(value, dict):
        value.setdefault("native_source", None)
        value.setdefault("tasks", None)
        if isinstance(value.get("catalog"), dict):
            value["catalog"].setdefault("artifact_file", None)
    return parse_model(JobConfig, value)


async def _publish(config: JobConfig, *, rebase: int | None) -> JobResult:  # pragma: no cover - composition
    from sqlalchemy.ext.asyncio import create_async_engine

    from .assignment_transport import AssignmentPrivateTransport
    from .models import Scope
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
    admission, _, _ = read_providers(material, lifetime)
    credentials = publication_credentials(config.publication, material, lifetime)
    context = job_tls_context(config, manifest.read_surface, scope, material.directory)
    dsn = _secret_file(config.identity_dsn_file).decode("utf-8").strip()
    import ssl

    tls = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)  # raizes RDS da imagem, verify-full
    if not tls.check_hostname or tls.verify_mode != ssl.CERT_REQUIRED:
        raise unavailable()
    engine = create_async_engine(
        dsn, hide_parameters=True, pool_size=1, max_overflow=0, connect_args={"ssl": tls}
    )
    if config.tasks is not None and (config.native_source is None or config.catalog.artifact_file is None):
        raise unavailable()  # tasks need the counter/task source AND the catalog that carries them
    native_engine = None
    native: NativeTaskSource | None = None
    if config.native_source is not None:
        native_engine = create_async_engine(
            _secret_file(config.native_source.dsn_file).decode("utf-8").strip(),
            hide_parameters=True,
            pool_size=1,
            max_overflow=0,
            connect_args={"ssl": tls},
        )
        native = NativeTaskSource(
            native_engine,
            tenant=config.tenant,
            native_schema=config.native_source.native_schema,
            engine_schema=config.native_source.engine_schema,
        )
    client = PortalReadClient(
        origin=manifest.read_surface.origin,
        tls_context=context,
        server_spki_sha256=manifest.read_surface.server_spki_sha256,
        partition=ReadCredentialPartition(
            scope=scope,
            engine_name=manifest.engine_name,
            key_id=config.publication.key_id,
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
        tasks: TaskPublication | None = None
        resource: ResourcePolicyPublicationSource = RefusingResourceSource()
        if config.tasks is not None:
            assert native is not None
            admission_raw = _public_file(config.tasks.admission_record_file)
            task_admission = TaskAdmission.from_record(admission_raw)
            source = TaskResourceSource(clock=_now)
            tasks = TaskPublication(
                native=native,
                admission=task_admission,
                entries=catalog_entries(catalog.raw, task_admission),
                source=source,
                evidence_seconds=config.tasks.evidence_seconds,
            )
            resource = source
        read_publisher = PortalReadPublisher(
            client=client,
            membership=membership,
            catalog=catalog,
            resource=resource,
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
            counter=native.authority_revision if native is not None else None,
            tasks=tasks,
        ).run()
    finally:
        await authority_client.aclose()
        await client.close()
        lifetime.close()
        await engine.dispose()
        if native_engine is not None:
            await native_engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m maezo.gateway.human.membership_publication_job")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("catalog-digest", help="print the SHA-256 the admission must carry")
    d.add_argument("--catalog-ref", required=True)
    d.add_argument("--publisher-ref", required=True)
    d.add_argument("--deployment-receipt-ref", required=True)
    d.add_argument("--deployment-receipt-digest", required=True)
    p = sub.add_parser(
        "publish",
        help="publish/renew the catalog and memberships; run every < min(validity)/2 (e.g. 4-5 min)",
    )
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
