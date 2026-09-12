"""Closed first staff Cases projection. SC1: no content or mutation capability."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from maezo.gateway.external_cases.models import CaseOutcome, CaseRef, Closed, Identity, Ref
from maezo.gateway.staff_cases.models import N, T


class ObservationTimes(Closed):
    observed_at: T
    source_observed_at: T
    valid_until: T


class StaffFreshness(ObservationTimes):
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


class StaffDetailShape[FreshnessT: ObservationTimes](Closed):
    schema_: Literal["portal-staff-case-detail.v1"] = Field(alias="schema")
    case: StaffSummary
    identity: Identity
    active_tasks: tuple[LinkedTask, ...] = Field(max_length=100)
    next_task_cursor: Ref | None
    tasks_complete: bool
    freshness: FreshnessT
    # Mesmo vocabulario fechado do publico externo (`CaseOutcome`): um so contrato de
    # desfecho. O colaborador nao ganha campo adicional aqui — o `numero_autorizacao`
    # real e o conteudo da decisao continuam no recibo de comando
    # (`GET /commands/{command_id}/receipt`), nao nesta projecao de leitura.
    outcome: CaseOutcome | None

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
        if (self.outcome is None) != (self.case.state == "active"):
            raise ValueError("inconsistent staff case outcome")
        return self


class StaffDetail(StaffDetailShape[StaffFreshness]):
    """Public JSON contract; cadence is numeric independently of native signing."""


class StaffPageShape[FreshnessT: ObservationTimes](Closed):
    schema_: Literal["portal-staff-case-page.v1"] = Field(alias="schema")
    items: tuple[StaffSummary, ...] = Field(max_length=100)
    next_cursor: Ref | None
    freshness: FreshnessT

    @model_validator(mode="after")
    def ordered_page(self) -> Self:
        refs = [item.case_ref for item in self.items]
        if refs != sorted(set(refs)):
            raise ValueError("inconsistent staff case page ordering")
        return self


class StaffPage(StaffPageShape[StaffFreshness]):
    """Authorized complete-checkpoint list page."""
