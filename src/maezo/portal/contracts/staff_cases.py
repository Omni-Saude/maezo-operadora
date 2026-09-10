"""Closed first staff Cases projection. SC1: no content or mutation capability."""
from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from maezo.gateway.external_cases.models import CaseRef, Closed, Identity, Ref
from maezo.gateway.staff_cases.models import N, T


class Freshness(Closed):
    observed_at: T
    source_observed_at: T
    valid_until: T
    refresh_after_seconds: Literal[10]


class StaffSummary(Closed):
    case_ref: CaseRef
    kind: Literal["authorization"]
    state: Literal["active", "ended"]
    record_revision: N
    state_observed_at: T


class LinkedTask(Closed):
    task_id: Ref
    task_definition_key: Ref
    task_revision: N
    created_at: T
    due_at: T | None
    assignee_ref: Ref | None


class StaffDetail(Closed):
    schema_: Literal["portal-staff-case-detail.v1"] = Field(alias="schema")
    case: StaffSummary
    identity: Identity
    active_tasks: tuple[LinkedTask, ...] = Field(max_length=100)
    next_task_cursor: Ref | None
    tasks_complete: bool
    freshness: Freshness

    @model_validator(mode="after")
    def consistent_identity_and_page(self) -> Self:
        if self.identity.case_ref != self.case.case_ref or self.identity.kind != "authorization":
            raise ValueError("inconsistent staff case")
        if self.tasks_complete != (self.next_task_cursor is None):
            raise ValueError("inconsistent staff task page")
        ids = [task.task_id for task in self.active_tasks]
        if ids != sorted(set(ids)):
            raise ValueError("inconsistent staff task ordering")
        if self.case.state == "ended" and self.active_tasks:
            raise ValueError("inconsistent ended staff case")
        return self
