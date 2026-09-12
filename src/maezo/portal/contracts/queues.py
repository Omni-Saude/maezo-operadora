"""ADR-0049 employee read projections; closed Q1 contract, no mutation authority."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

from .models import (
    AllowedInput,
    FormKey,
    FormSourceStatus,
    OpaqueRef,
    PagtoAdmissibilityEvidence,
    SchemaVersion,
    Sha256Digest,
    TaskSnapshot,
)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone required")
    return value.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(utc)]
DecimalRevision = Annotated[str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]*)$")]
DecimalVersion = Annotated[str, StringConstraints(strict=True, pattern=r"^[1-9][0-9]*$")]
OpaqueCursor = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=2048, pattern=r"^[A-Za-z0-9_-]+$")
]
QueueName = Literal["mine", "team"]
PageLimit = Annotated[StrictInt, Field(ge=1, le=100)]
ReadErrorCode = Literal[
    "invalid_request",
    "session_unavailable",
    "employee_access_required",
    "resource_unavailable",
    "refresh_required",
    "read_dependency_unavailable",
]


class ClosedRead(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, revalidate_instances="always", hide_input_in_errors=True
    )


class TaskQueueRequest(ClosedRead):
    queue: QueueName
    limit: PageLimit = 25
    cursor: OpaqueCursor | None = None


class QueueFreshness(ClosedRead):
    state: Literal["current"] = "current"
    observed_at: UTCDateTime
    source_observed_at: UTCDateTime
    valid_until: UTCDateTime
    refresh_after_seconds: Literal[10] = 10

    @model_validator(mode="after")
    def current(self) -> Self:
        if not self.source_observed_at <= self.observed_at < self.valid_until:
            raise ValueError("invalid freshness")
        return self


class TaskQueueItem(ClosedRead):
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    task_definition_key: OpaqueRef
    task_revision: DecimalRevision
    ownership: Literal["unassigned", "self", "other"]
    engine_due_at: UTCDateTime | None
    snapshot_at: UTCDateTime


class TaskQueuePage(ClosedRead):
    schema_: Literal["portal-task-queue.v1"] = Field(default="portal-task-queue.v1", alias="schema")
    queue: QueueName
    items: tuple[TaskQueueItem, ...] = Field(max_length=100)
    next_cursor: OpaqueCursor | None
    freshness: QueueFreshness

    @model_validator(mode="after")
    def ordered(self) -> Self:
        ids = tuple(item.task_id for item in self.items)
        if ids != tuple(sorted(set(ids))) or (not ids and self.next_cursor is not None):
            raise ValueError("invalid page")
        return self


class PublicTaskSnapshot(ClosedRead):
    schema_version: SchemaVersion
    snapshot_at: UTCDateTime
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: DecimalVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: DecimalVersion
    form_digest: Sha256Digest
    form_source_status: FormSourceStatus
    task_revision: DecimalRevision
    assignee_ref: OpaqueRef | None
    eligible_candidate_groups: tuple[OpaqueRef, ...]
    evidence_revision: DecimalRevision
    evidence_digest: Sha256Digest
    engine_due_at: UTCDateTime | None
    allowed_actions: tuple[()] = ()
    allowed_inputs: tuple[AllowedInput, ...]
    read_only_evidence: PagtoAdmissibilityEvidence | None

    @model_validator(mode="after")
    def supported_binding(self) -> Self:
        data = self.model_dump()
        for field in ("process_definition_version", "form_version", "task_revision", "evidence_revision"):
            data[field] = int(data[field])
        TaskSnapshot.model_validate(data)
        return self

    @classmethod
    def from_snapshot(cls, snapshot: TaskSnapshot) -> "PublicTaskSnapshot":
        # Validate independently, including instances made with unchecked model_copy.
        data = TaskSnapshot.model_validate(snapshot).model_dump()
        for field in ("process_definition_version", "form_version", "task_revision", "evidence_revision"):
            data[field] = str(data[field])
        data["allowed_actions"] = ()
        return cls.model_validate(data)


class TaskReadResponse(ClosedRead):
    schema_: Literal["portal-task-read.v1"] = Field(default="portal-task-read.v1", alias="schema")
    task: PublicTaskSnapshot
    freshness: QueueFreshness


class PortalReadError(ClosedRead):
    schema_: Literal["portal-read-error.v1"] = Field(default="portal-read-error.v1", alias="schema")
    code: ReadErrorCode
