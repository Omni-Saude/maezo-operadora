"""H2 (D-N) — the real Q2 `resource` source: the live User Tasks of the admitted catalog.

Replaces `RefusingResourceSource` in the T1.5 job (`membership_publication_job.py`). For every
live task (`ACT_RU_TASK`) whose (process definition, task key) the Q2 admission names in its
`human` block, the job publishes, in this order and in the tenant's one CAS sequence:

1. the task EVIDENCE (`human-authority.v1`, operation `evidence`): the engine keeps it in
   `MZO_HUMAN_EVIDENCE` and every resource must cite it by (ref, revision, digest);
2. the task RESOURCE (`portal-read-publication.v1`, kind `resource`), by the contract of the
   shared vector `tests/fixtures/portal_read/jcs-resource-vector.json` (read-provider README):
   ``source_ref = <admitted prefix> + task_id``, ``source_revision = resource_revision``,
   ``source_digest = SHA-256(JCS(payload))``,
   ``receipt_ref = portal-resource:<tenant>:task:<task_id>@<resource_revision>``,
   ``valid_until = observed_at + observation_seconds``.

`evidence_ref` and every `positive_grants[].decision_receipt_ref` are unique PER TASK: the read's
continuity ceilings are indexed by (kind, ref, revision, digest), and two resources sharing a ref
with different `observed_at` refuse a whole queue (measured in `ResourceQualificationJarIT`).

Who is granted: every live STAFF membership (not revoked, reviewed) holding one of the entry's
`required_roles` and one of its `group_domain.groups`. The resource grants the whole group
domain; what separates the queue of one group from another is the task's real candidate group,
checked by the engine (`verifyIdentityPolicy`), never re-implemented here (ADR-0012).

Domain functions are pure (`evidence_command`, `resource_projection`, `resource_provenance`,
`grants_for`); `NativeTaskSource` is the only I/O (one login, column grants of
`deploy/sql/portal-task-source-grants.sql`). Every refusal is `ReadRefusalError`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.portal.api.records import MembershipRecord
from maezo.portal.engine.profile import canonicalize, strict_loads

from .read_credentials import unavailable
from .read_profile import (
    FullTaskClassification,
    ReadCatalogArtifact,
    ReadCatalogEntry,
    ResourceIdentityGrant,
    ResourceProjection,
    SourceProvenance,
    digest,
    parse_model,
    wire,
)
from .read_publisher import ResourcePolicyPublicationSource, SourceFreezeLease, SourceSnapshot

_SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DECIMAL = re.compile(r"[1-9][0-9]{0,18}")
#: One job run never scans more than this many live tasks; more is a refusal, never a cut list.
MAX_TASKS = 1000


def _schema(value: str) -> str:
    if not _SCHEMA.fullmatch(value or ""):
        raise unavailable()
    return value


# --- admitted facts ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HumanEntry:
    process_definition_id: str
    task_definition_key: str
    classification: dict[str, str]
    identity_policy: dict[str, str]
    task_id_format: str
    candidate_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskAdmission:
    """What the signed Q2 admission (`portal-read-admission.v1` with `human`) lets the job publish."""

    tenant: str
    publisher_ref: str
    source_ref_prefix: str
    observation_seconds: int
    valid_until: datetime
    catalog_digest: str
    entries: dict[tuple[str, str], HumanEntry]

    @classmethod
    def from_record(cls, raw: bytes) -> TaskAdmission:
        try:
            value = strict_loads(raw)
            if canonicalize(value) != raw or value["schema"] != "portal-read-admission.v1":
                raise ValueError
            resource = [p for p in value["publishers"] if p["kind"] == "resource"]
            if len(resource) != 1 or not value["human"]["entries"]:
                raise ValueError
            entries = {}
            for item in value["human"]["entries"]:
                entry = HumanEntry(
                    item["process_definition_id"],
                    item["task_definition_key"],
                    dict(item["classification"]),
                    dict(item["identity_policy"]),
                    item["task_id_format"],
                    tuple(item["candidate_groups"]),
                )
                key = (entry.process_definition_id, entry.task_definition_key)
                if key in entries or entry.task_id_format not in ("uuid", "decimal"):
                    raise ValueError
                entries[key] = entry
            prefix = resource[0]["source_ref_prefix"]
            if not prefix.endswith(":"):
                raise ValueError
            until = datetime.fromisoformat(value["valid_until"].replace("Z", "+00:00"))
            return cls(
                tenant=value["scope"]["tenant"],
                publisher_ref=resource[0]["publisher_ref"],
                source_ref_prefix=prefix,
                observation_seconds=int(value["observation_seconds"]),
                valid_until=until,
                catalog_digest=value["catalog"]["catalog_digest"],
                entries=entries,
            )
        except Exception:
            raise unavailable() from None

    def task_id_ok(self, entry: HumanEntry, task_id: str) -> bool:
        pattern = _UUID if entry.task_id_format == "uuid" else _DECIMAL
        return bool(pattern.fullmatch(task_id))


def catalog_entries(raw: bytes, admission: TaskAdmission) -> dict[tuple[str, str], ReadCatalogEntry]:
    """The catalog artifact the admission pins (digest), indexed like the `human` block."""
    try:
        artifact = parse_model(ReadCatalogArtifact, strict_loads(raw))
    except Exception:
        raise unavailable() from None
    if hashlib.sha256(raw).hexdigest() != admission.catalog_digest:
        raise unavailable()
    entries = {(e.process_definition_id, e.task_definition_key): e for e in artifact.entries}
    if set(entries) != set(admission.entries):
        raise unavailable()  # a task published but never readable, or admitted but absent
    return entries


# --- native source (the only I/O) ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveTask:
    task_id: str
    revision: int
    process_definition_id: str
    task_definition_key: str


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    revision: int
    ref: str
    digest: str
    valid_until: int
    process_definition_id: str


class NativeTaskSource:
    """Read-only, column-granted view of the engine's own rows (tenant counter, tasks, evidence)."""

    def __init__(self, engine: AsyncEngine, *, tenant: str, native_schema: str, engine_schema: str) -> None:
        if not tenant:
            raise unavailable()
        self._engine, self.tenant = engine, tenant
        self._native, self._act = _schema(native_schema), _schema(engine_schema)

    async def _rows(self, sql: str, **params: Any) -> list[Any]:
        try:
            async with self._engine.connect() as connection:
                return list(await connection.execute(text(sql), params))
        except Exception:
            raise unavailable() from None

    async def authority_revision(self) -> int:
        rows = await self._rows(
            f"SELECT rev_ FROM {self._native}.mzo_human_tenant WHERE tenant_=:tenant", tenant=self.tenant
        )
        if len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] < 0:
            raise unavailable()
        return rows[0][0]

    async def live_tasks(self, keys: Iterable[str]) -> list[LiveTask]:
        rows = await self._rows(
            f"SELECT id_, rev_, proc_def_id_, task_def_key_ FROM {self._act}.act_ru_task "
            "WHERE tenant_id_=:tenant AND suspension_state_=1 AND task_def_key_ = ANY(:keys) "
            f'ORDER BY id_ COLLATE "C" LIMIT {MAX_TASKS + 1}',
            tenant=self.tenant,
            keys=sorted(set(keys)),
        )
        if len(rows) > MAX_TASKS:
            raise unavailable()
        return [LiveTask(str(r[0]), int(r[1]), str(r[2]), str(r[3])) for r in rows]

    async def task(self, task_id: str) -> LiveTask | None:
        rows = await self._rows(
            f"SELECT id_, rev_, proc_def_id_, task_def_key_ FROM {self._act}.act_ru_task "
            "WHERE tenant_id_=:tenant AND id_=:task AND suspension_state_=1",
            tenant=self.tenant,
            task=task_id,
        )
        return LiveTask(str(rows[0][0]), int(rows[0][1]), str(rows[0][2]), str(rows[0][3])) if rows else None

    async def evidence(self, task_id: str) -> EvidenceRow | None:
        rows = await self._rows(
            f"SELECT rev_, ref_, digest_, valid_until_, process_ FROM {self._native}.mzo_human_evidence "
            "WHERE tenant_=:tenant AND task_=:task",
            tenant=self.tenant,
            task=task_id,
        )
        if not rows:
            return None
        r = rows[0]
        return EvidenceRow(int(r[0]), str(r[1]), str(r[2]), int(r[3]), str(r[4]))


# --- pure domain ---------------------------------------------------------------------------


def evidence_ref(tenant: str, task_id: str) -> str:
    return f"portal-evidence:{tenant}:task:{task_id}"


def evidence_digest(task: LiveTask, entry: ReadCatalogEntry, catalog_digest: str) -> str:
    """What the evidence attests: this task of this admitted definition, in this catalog."""
    return digest(
        dict(
            schema="portal-task-evidence.v1",
            task_id=task.task_id,
            process_definition_id=task.process_definition_id,
            process_definition_digest=entry.process_definition_digest,
            task_definition_key=task.task_definition_key,
            catalog_digest=catalog_digest,
        )
    )


def evidence_command(
    *,
    tenant: str,
    workload_ref: str,
    expected_revision: int,
    task: LiveTask,
    entry: ReadCatalogEntry,
    catalog_digest: str,
    valid_until: datetime,
) -> bytes:
    """The `evidence` operation `AuthorityCommand` accepts (JCS bytes, signed by the job)."""
    return canonicalize(
        dict(
            schema="human-authority.v1",
            tenant=tenant,
            workload_ref=workload_ref,
            operation="evidence",
            expected_revision=str(expected_revision),
            task_id=task.task_id,
            process_definition_id=task.process_definition_id,
            evidence_ref=evidence_ref(tenant, task.task_id),
            evidence_digest=evidence_digest(task, entry, catalog_digest),
            valid_until=str(int(valid_until.timestamp())),
        )
    )


def grants_for(
    records: Iterable[MembershipRecord],
    entry: ReadCatalogEntry,
    *,
    tenant: str,
    task_id: str,
    now: datetime,
    valid_until: datetime,
) -> tuple[ResourceIdentityGrant, ...]:
    """Staff memberships with a required role and a group of the entry's domain (sorted, unique)."""
    roles, groups = set(entry.required_roles), set(entry.group_domain.groups)
    found: dict[str, ResourceIdentityGrant] = {}
    for record in records:
        if (
            record.tenant != tenant
            or record.audience != "staff"
            or record.revoked
            or not now < record.reviewed_until
            or not any(set(b.roles) & roles and set(b.groups) & groups for b in record.memberships)
        ):
            continue
        receipt = f"portal-resource:{tenant}:task:{task_id}:grant:{record.principal_ref}@{record.revision}"
        until = min(valid_until, record.reviewed_until)
        found[record.principal_ref] = ResourceIdentityGrant(
            issuer=record.issuer,
            subject=record.subject,
            principal_ref=record.principal_ref,
            membership_revision=record.revision,
            consent_scopes=(),
            decision_receipt_ref=receipt,
            decision_digest=digest(
                dict(
                    schema="portal-task-grant-decision.v1",
                    receipt_ref=receipt,
                    task_id=task_id,
                    resource_policy=wire(entry.resource_policy),
                    groups=sorted(groups & {g for b in record.memberships for g in b.groups}),
                )
            ),
            valid_until=until,
        )
    return tuple(found[k] for k in sorted(found))


def resource_projection(
    *,
    tenant: str,
    task: LiveTask,
    entry: ReadCatalogEntry,
    human: HumanEntry,
    evidence: EvidenceRow,
    grants: tuple[ResourceIdentityGrant, ...],
    resource_revision: int,
    valid_until: datetime,
) -> ResourceProjection:
    """`wire(ResourceProjection)` is the signed payload (vector `jcs-resource-vector.json`)."""
    ref = f"portal-resource:{tenant}:task:{task.task_id}"
    return ResourceProjection(
        task_id=task.task_id,
        process_definition_id=task.process_definition_id,
        process_definition_digest=entry.process_definition_digest,
        observed_task_revision=task.revision,
        evidence_ref=evidence.ref,
        evidence_revision=evidence.revision,
        evidence_digest=evidence.digest,
        resource_ref=ref,
        resource_revision=resource_revision,
        resource_digest=resource_key(task, entry, evidence, grants),
        resource_policy=entry.resource_policy,
        classification=FullTaskClassification.model_validate(
            {**human.classification, "valid_until": valid_until}
        ),
        required_subject_bindings=(),
        required_consent_scopes=(),
        positive_grants=grants,
        read_only_evidence=None,
        state="complete",
        valid_until=valid_until,
    )


def resource_key(
    task: LiveTask, entry: ReadCatalogEntry, evidence: EvidenceRow, grants: tuple[ResourceIdentityGrant, ...]
) -> str:
    """Stable content key (no times, no task revision): what decides a republish, and the
    resource digest itself. The engine bumps the task revision on every publication
    (`forceUpdate`), so the revision can never be part of "unchanged"."""
    return digest(
        dict(
            schema="portal-task-resource.v1",
            task_id=task.task_id,
            process_definition_id=task.process_definition_id,
            process_definition_digest=entry.process_definition_digest,
            resource_policy=wire(entry.resource_policy),
            evidence=dict(ref=evidence.ref, revision=str(evidence.revision), digest=evidence.digest),
            grants=[
                dict(
                    principal_ref=g.principal_ref,
                    membership_revision=str(g.membership_revision),
                    decision_digest=g.decision_digest,
                )
                for g in grants
            ],
        )
    )


def resource_provenance(
    payload: ResourceProjection, *, tenant: str, admission: TaskAdmission, observed_at: datetime
) -> SourceProvenance:
    return SourceProvenance(
        publisher_ref=admission.publisher_ref,
        source_ref=admission.source_ref_prefix + payload.task_id,
        source_revision=payload.resource_revision,
        source_digest=digest(payload),
        receipt_ref=f"portal-resource:{tenant}:task:{payload.task_id}@{payload.resource_revision}",
        observed_at=observed_at,
        valid_until=observed_at + timedelta(seconds=admission.observation_seconds),
    )


# --- the publisher's source port -----------------------------------------------------------


class TaskResourceSource(ResourcePolicyPublicationSource):
    """One staged snapshot per task: the job stages it, `PortalReadPublisher.publish_resource`
    reads it exactly once. A task nobody staged, or staged twice, refuses."""

    def __init__(self, *, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._staged: dict[str, tuple[SourceProvenance, ResourceProjection]] = {}

    def stage(self, provenance: SourceProvenance, payload: ResourceProjection) -> None:
        if payload.task_id in self._staged:
            raise unavailable()
        self._staged[payload.task_id] = (provenance, payload)

    async def read(self, task_id: str) -> SourceSnapshot:
        staged = self._staged.pop(task_id, None)
        if staged is None:
            raise unavailable()
        provenance, payload = staged
        state = {"dead": False}

        def verify(raw: bytes) -> None:
            raise unavailable()  # a resource never verifies a membership row

        def live() -> None:
            if state["dead"] or not provenance.observed_at <= self._clock() < provenance.valid_until:
                raise unavailable()

        def end(*_: object) -> None:
            state["dead"] = True

        return SourceSnapshot(provenance, payload, SourceFreezeLease(provenance, verify, live, end, end))
