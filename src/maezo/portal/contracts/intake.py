"""AUTH browser contract, SP-OP-AUTH-001 portal E01 and ADR0049 D3.

Resource references are selections, never authority. Protected guide content and
admissibility facts are resolved on the server. A receipt is not a clinical outcome.
"""

from datetime import datetime
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


class IntakeLink(Closed):
    """A projection of one server-verified delegation, never an authority the browser may assert.

    Every field is a projection of a published `resource_authority` head that the server
    re-reads and re-verifies on the next request. Selecting a link in a form is a selection;
    the submit path authorizes it again from the same published source.
    """

    schema_version: Literal[1] = 1
    beneficiary_ref: ResourceRef
    provider_ref: ResourceRef | None = None
    resource_kind: Literal["guide", "intake", "case"]
    resource_ref: ResourceRef
    action: Literal["auth.start"]
    valid_until: datetime
    # Derived exclusively from the publishing source's `publisher_ref`; never defaulted,
    # never inferred from the deployment mode and never supplied by the browser.
    provenance_kind: Literal["synthetic", "attested"]

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("aware deadline required")
        return value


class IntakeLinks(Closed):
    schema_version: Literal[1] = 1
    links: tuple[IntakeLink, ...] = ()

    @model_validator(mode="after")
    def unique_links(self) -> Self:
        keys = [(link.resource_kind, link.resource_ref, link.action) for link in self.links]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate link")
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
