"""AUTH browser contract, SP-OP-AUTH-001 portal E01 and ADR0049 D3.

Resource references are selections, never authority. Protected guide content and
admissibility facts are resolved on the server. A receipt is not a clinical outcome.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ResourceRef = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
DecimalRevision = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
Centavos = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)$")]
Category = Literal[
    "consulta", "exame_simples", "exame_especial", "terapia", "internacao", "opme", "alta_complexidade"
]
Character = Literal["urgencia", "eletivo"]


class Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class AuthIntakeSubmission(Closed):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    command_id: ResourceRef
    beneficiary_ref: ResourceRef
    provider_ref: ResourceRef
    guide_ref: ResourceRef
    codigo_procedimento_tuss: Annotated[str, StringConstraints(min_length=1)] = Field(repr=False)
    categoria_procedimento: Category
    carater_atendimento: Character
    valor_estimado_centavos: Centavos = Field(repr=False)
    document_refs: tuple[ResourceRef, ...]

    @field_validator("document_refs", mode="before")
    @classmethod
    def http_array(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def unique_documents(self) -> Self:
        if len(set(self.document_refs)) != len(self.document_refs):
            raise ValueError("duplicate document reference")
        return self


class IntakeReceipt(Closed):
    schema_version: Literal[1] = 1
    intake_ref: ResourceRef
    command_id: ResourceRef
    revision: DecimalRevision
    disposition: Literal["admitted", "dispatching", "reconciling", "started", "rejected"]
    case_ref: ResourceRef | None = None
    start_receipt_ref: ResourceRef | None = None

    @model_validator(mode="after")
    def proven_start_only(self) -> Self:
        if (self.disposition == "started") != (
            self.case_ref is not None and self.start_receipt_ref is not None
        ):
            raise ValueError("start evidence required")
        if self.disposition != "started" and (
            self.case_ref is not None or self.start_receipt_ref is not None
        ):
            raise ValueError("unproven start")
        return self


class PortalIntakeError(Closed):
    code: Literal[
        "invalid_request",
        "authentication_unavailable",
        "operation_forbidden",
        "resource_unavailable",
        "conflict",
        "dependency_unavailable",
    ]
