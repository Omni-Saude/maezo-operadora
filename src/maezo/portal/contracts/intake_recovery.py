"""Pointer-only recovery; approved E04 intake recovery construction contract v1."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .intake import Closed, ResourceRef


class IntakeRecoveryItem(Closed):
    command_id: ResourceRef
    intake_ref: ResourceRef


class IntakeCommandObservation(Closed):
    schema_version: Literal[1] = 1
    command_id: ResourceRef
    observation: Literal["observed", "not_observed"]
    intake_ref: ResourceRef | None = None

    @model_validator(mode="after")
    def observed_pointer(self) -> Self:
        if (self.observation == "observed") != (self.intake_ref is not None):
            raise ValueError("observed admission required")
        return self


class IntakeRecoveryPage(Closed):
    schema_version: Literal[1] = 1
    scope: Literal["actor_admissions"] = "actor_admissions"
    items: tuple[IntakeRecoveryItem, ...] = Field(max_length=50)
    next_cursor: ResourceRef | None = None

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len({i.command_id for i in self.items}) != len(self.items) or len(
            {i.intake_ref for i in self.items}
        ) != len(self.items):
            raise ValueError("distinct admissions required")
        return self
