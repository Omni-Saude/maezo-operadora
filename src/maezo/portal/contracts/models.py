"""Closed, immutable contracts for the first human-portal foundation.

Sources:

* ADR-0049 D2-D6 (accepted engineering direction; technical specification DRAFT/verify);
* SP-OP-AUTH-001, SP-OP-ESCALATION-001 and SP-OP-PAGTO-001 at the catalog's pinned R6 revision;
* the frozen preparatory portal catalog (43 tasks in 15 families).

These DTOs validate shape.  They do not authenticate a human, establish tenant membership,
compare revisions with live engine state, verify a digest cryptographically, or prove a commit.
Those checks belong to the future server-side HumanGateway and engine plugin.  In particular,
``HumanPrincipal.model_validate`` is not an authentication operation: the future BFF must build
the object only after validating the server-side session and memberships.

The deliberately narrow executable form set is:

* AUTH ``UT_AnaliseMedicoAuditor`` and ``UT_CoordenacaoAssume`` -> ``auth_decisao``;
* AUTH ``UT_RegistrarParecerJunta`` -> ``auth_junta``;
* ESCALATION ``UT_TratarEscalonamento`` and ``UT_SupervisorAssume`` -> ``escalation``;
* PAGTO ``UT_AnaliseAdmissibilidade`` -> ``pagto_admissibilidade``.

PAGTO admissibility is based on explicit BPMN task documentation and the plan, while the SP-OP
output table does not enumerate ``decisao_admissibilidade`` and the BPMN has no ``formData``.  Its
``form_source_status`` therefore remains DRAFT/verify.  No generic variables map exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

OpaqueRef = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=512,
        pattern=r"^[^\s/?#]+$",
    ),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$"),
]
PositiveVersion = Annotated[StrictInt, Field(ge=1)]
Revision = Annotated[StrictInt, Field(ge=0)]
SchemaVersion = Annotated[StrictInt, Field(ge=1, le=1)]
RequiredText = Annotated[str, StringConstraints(strict=True, min_length=1)]
CanonicalCentavos = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$"),
]

FormKey = Literal["auth_decisao", "auth_junta", "escalation", "pagto_admissibilidade"]
FormSourceStatus = Literal["BPMN_FORMDATA", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"]
TaskAction = Literal["claim", "release", "decision"]
AllowedInput = Literal[
    "decisao_auditor",
    "justificativa_clinica",
    "cid10_referencia",
    "fundamentacao_dut",
    "resultado",
    "notas_resolucao",
    "decisao_admissibilidade",
    "justificativa_recusa",
]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        revalidate_instances="always",
    )


class Centavos(RootModel[CanonicalCentavos]):
    """Canonical browser representation of an exact integer number of centavos.

    There is intentionally no business ceiling and no signed-int64 restriction in this value
    object: SP-OP-PAGTO-001 requires an integer but does not declare either bound.  Conversion is
    direct from the canonical decimal string and never passes through float/JavaScript Number.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    def as_int(self) -> int:
        return int(self.root)


class MembershipBinding(_FrozenContract):
    """A server-resolved membership projection; values are not accepted as browser authority."""

    membership_ref: OpaqueRef
    roles: tuple[OpaqueRef, ...]
    groups: tuple[OpaqueRef, ...]


class SubjectBinding(_FrozenContract):
    """A server-verified beneficiary/provider relationship represented by an opaque reference."""

    kind: Literal["beneficiary", "provider"]
    resource_ref: OpaqueRef


class HumanPrincipal(_FrozenContract):
    """Internal principal DTO built from a validated server-side human session.

    Instantiating this model only validates its closed shape.  It never authenticates the issuer,
    subject, tenant, memberships, session, or subject relationships.
    """

    schema_version: SchemaVersion
    principal_ref: OpaqueRef
    issuer: RequiredText
    subject: OpaqueRef
    tenant: OpaqueRef
    membership_revision: Revision
    memberships: tuple[MembershipBinding, ...]
    session_ref: OpaqueRef
    authenticated_at: datetime
    subject_bindings: tuple[SubjectBinding, ...]

    @field_validator("issuer")
    @classmethod
    def _issuer_is_an_origin_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("issuer must be an absolute HTTP(S) issuer URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("issuer URL must not contain credentials, query, or fragment")
        return value

    @field_validator("authenticated_at")
    @classmethod
    def _authenticated_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, field="authenticated_at")


class AuthDecisionInputs(_FrozenContract):
    """AUTH auditor form; ``auditor_id`` is injected later from the trusted principal."""

    kind: Literal["auth_decisao"]
    decisao_auditor: Literal["APROVAR", "NEGAR", "SOLICITAR_INFO", "JUNTA_MEDICA"]
    justificativa_clinica: RequiredText | None = None
    cid10_referencia: RequiredText | None = None
    fundamentacao_dut: RequiredText | None = None

    @model_validator(mode="after")
    def _negative_has_complete_basis(self) -> Self:
        if self.decisao_auditor == "NEGAR" and not all(
            value is not None and value.strip()
            for value in (
                self.justificativa_clinica,
                self.cid10_referencia,
                self.fundamentacao_dut,
            )
        ):
            raise ValueError("NEGAR requires justificativa_clinica, cid10_referencia and fundamentacao_dut")
        return self


class AuthJuntaInputs(_FrozenContract):
    """AUTH medical-board form, whose outcome set is narrower than the auditor form."""

    kind: Literal["auth_junta"]
    decisao_auditor: Literal["APROVAR", "NEGAR"]
    justificativa_clinica: RequiredText | None = None
    cid10_referencia: RequiredText | None = None
    fundamentacao_dut: RequiredText | None = None

    @model_validator(mode="after")
    def _negative_has_complete_basis(self) -> Self:
        if self.decisao_auditor == "NEGAR" and not all(
            value is not None and value.strip()
            for value in (
                self.justificativa_clinica,
                self.cid10_referencia,
                self.fundamentacao_dut,
            )
        ):
            raise ValueError("NEGAR requires justificativa_clinica, cid10_referencia and fundamentacao_dut")
        return self


class EscalationDecisionInputs(_FrozenContract):
    kind: Literal["escalation"]
    resultado: Literal["resolvido_humano", "devolvido_agente", "emergencia_acionada"]
    notas_resolucao: RequiredText

    @field_validator("notas_resolucao")
    @classmethod
    def _notes_are_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("notas_resolucao must not be blank")
        return value


class PagtoAdmissibilityInputs(_FrozenContract):
    """PAGTO admissibility only; it cannot approve or release a payment."""

    kind: Literal["pagto_admissibilidade"]
    decisao_admissibilidade: Literal["PROSSEGUIR", "DEVOLVER"]
    justificativa_recusa: RequiredText | None = None

    @model_validator(mode="after")
    def _return_has_justification(self) -> Self:
        if self.decisao_admissibilidade == "DEVOLVER" and (
            self.justificativa_recusa is None or not self.justificativa_recusa.strip()
        ):
            raise ValueError("DEVOLVER requires justificativa_recusa")
        return self


class PagtoAdmissibilityEvidence(_FrozenContract):
    """Closed read-only PAGTO projection; evidence does not confer financial approval."""

    kind: Literal["pagto_admissibilidade"]
    valor_pagamento_cents: Centavos
    dados_pagamento_validos: StrictBool
    lastro_confirmado: StrictBool
    lastro_origem: (
        Literal[
            "contas_adjudicacao_automatica",
            "contas_adjudicacao_humana",
            "recurso_deferimento_humano",
        ]
        | None
    ) = None
    lastro_decisor_id: OpaqueRef | Literal[""] | None = None
    duplicidade_suspeita: StrictBool


DecisionInputs = Annotated[
    AuthDecisionInputs | AuthJuntaInputs | EscalationDecisionInputs | PagtoAdmissibilityInputs,
    Field(discriminator="kind"),
]


_BINDINGS: dict[tuple[str, str], tuple[FormKey, FormSourceStatus]] = {
    ("SP-OP-AUTH-001", "UT_AnaliseMedicoAuditor"): ("auth_decisao", "BPMN_FORMDATA"),
    ("SP-OP-AUTH-001", "UT_CoordenacaoAssume"): ("auth_decisao", "BPMN_FORMDATA"),
    ("SP-OP-AUTH-001", "UT_RegistrarParecerJunta"): ("auth_junta", "BPMN_FORMDATA"),
    ("SP-OP-ESCALATION-001", "UT_TratarEscalonamento"): ("escalation", "BPMN_FORMDATA"),
    ("SP-OP-ESCALATION-001", "UT_SupervisorAssume"): ("escalation", "BPMN_FORMDATA"),
    ("SP-OP-PAGTO-001", "UT_AnaliseAdmissibilidade"): (
        "pagto_admissibilidade",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
}

_INPUTS_BY_FORM: dict[FormKey, tuple[AllowedInput, ...]] = {
    "auth_decisao": (
        "decisao_auditor",
        "justificativa_clinica",
        "cid10_referencia",
        "fundamentacao_dut",
    ),
    "auth_junta": (
        "decisao_auditor",
        "justificativa_clinica",
        "cid10_referencia",
        "fundamentacao_dut",
    ),
    "escalation": ("resultado", "notas_resolucao"),
    "pagto_admissibilidade": ("decisao_admissibilidade", "justificativa_recusa"),
}


class TaskSnapshot(_FrozenContract):
    """Immutable, dated view; freshness is checked against authoritative engine state later."""

    schema_version: SchemaVersion
    snapshot_at: datetime
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: PositiveVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: PositiveVersion
    form_digest: Sha256Digest
    form_source_status: FormSourceStatus
    task_revision: Revision
    assignee_ref: OpaqueRef | None
    eligible_candidate_groups: tuple[OpaqueRef, ...]
    evidence_revision: Revision
    evidence_digest: Sha256Digest
    engine_due_at: datetime | None
    allowed_actions: tuple[TaskAction, ...]
    allowed_inputs: tuple[AllowedInput, ...]
    read_only_evidence: PagtoAdmissibilityEvidence | None

    @field_validator("snapshot_at", "engine_due_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "timestamp")
        return _require_utc(value, field=field_name)

    @model_validator(mode="after")
    def _closed_binding(self) -> Self:
        expected = _binding_for(self.process_definition_key, self.task_definition_key)
        if (self.form_key, self.form_source_status) != expected:
            raise ValueError("process/task/form/source binding does not match the executable catalog")

        expected_inputs = _INPUTS_BY_FORM[self.form_key]
        if len(self.allowed_inputs) != len(expected_inputs) or set(self.allowed_inputs) != set(
            expected_inputs
        ):
            raise ValueError("allowed_inputs do not match the closed form binding")
        if len(self.allowed_actions) != len(set(self.allowed_actions)):
            raise ValueError("allowed_actions must not contain duplicates")

        if self.form_key == "pagto_admissibilidade":
            if self.read_only_evidence is None:
                raise ValueError("PAGTO admissibility requires its closed read-only evidence")
        elif self.read_only_evidence is not None:
            raise ValueError("read-only evidence is not enumerated for this task binding")
        return self


class TaskDecision(_FrozenContract):
    """Closed browser decision DTO without actor, tenant, tier, approval flag, or variables map.

    Expected revisions and digests are claims to be compared with current authoritative state by
    the future HumanGateway/plugin.  Successful construction does not establish freshness.
    """

    schema_version: SchemaVersion
    command_id: OpaqueRef
    task_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: PositiveVersion
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: FormKey
    form_version: PositiveVersion
    form_digest: Sha256Digest
    expected_task_revision: Revision
    expected_evidence_revision: Revision
    expected_evidence_digest: Sha256Digest
    expected_membership_revision: Revision
    inputs: DecisionInputs

    @model_validator(mode="after")
    def _closed_binding(self) -> Self:
        expected_form, _ = _binding_for(self.process_definition_key, self.task_definition_key)
        if self.form_key != expected_form or self.inputs.kind != expected_form:
            raise ValueError("process/task/form/input binding does not match the executable catalog")
        return self


class HumanCommandReceipt(_FrozenContract):
    """Shape-checkable command receipt; authenticity and engine state need future verification.

    ``pending`` represents durable acceptance/intention only.  ``committed`` requires explicit
    engine receipt, commit, consumed revision, commit time, and audit-result references.  Validating
    this DTO alone is not cryptographic verification of any reference.
    """

    schema_version: SchemaVersion
    status: Literal["pending", "committed", "conflict", "failure"]
    operation: Literal["claim", "release", "decision"]
    command_id: OpaqueRef
    tenant: OpaqueRef
    task_id: OpaqueRef
    payload_digest: Sha256Digest
    principal_ref: OpaqueRef
    workload_ref: OpaqueRef
    audit_intent_ref: OpaqueRef
    audit_result_ref: OpaqueRef | None = None
    consumed_task_revision: Revision | None = None
    resulting_task_revision: Revision | None = None
    engine_receipt_ref: OpaqueRef | None = None
    engine_commit_ref: OpaqueRef | None = None
    recorded_at: datetime
    engine_committed_at: datetime | None = None
    technical_code: OpaqueRef | None = None

    @field_validator("recorded_at", "engine_committed_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime | None, info: object) -> datetime | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "timestamp")
        return _require_utc(value, field=field_name)

    @model_validator(mode="after")
    def _status_shape(self) -> Self:
        committed_fields = (
            self.consumed_task_revision,
            self.engine_receipt_ref,
            self.engine_commit_ref,
            self.engine_committed_at,
            self.audit_result_ref,
        )
        if self.status == "committed":
            if any(value is None for value in committed_fields):
                raise ValueError("committed receipt requires engine commit/receipt/revision and audit refs")
            if self.technical_code is not None:
                raise ValueError("committed receipt must not carry a failure/conflict code")
            return self

        if any(
            value is not None
            for value in (
                self.consumed_task_revision,
                self.resulting_task_revision,
                self.engine_receipt_ref,
                self.engine_commit_ref,
                self.engine_committed_at,
            )
        ):
            raise ValueError("non-committed receipt must not claim engine commit fields")

        if self.status == "pending":
            if self.audit_result_ref is not None or self.technical_code is not None:
                raise ValueError("pending receipt carries intent only")
        elif self.technical_code is None or self.audit_result_ref is None:
            raise ValueError("conflict/failure receipt requires technical_code and audit_result_ref")
        return self


def _binding_for(process_key: str, task_key: str) -> tuple[FormKey, FormSourceStatus]:
    try:
        return _BINDINGS[(process_key, task_key)]
    except KeyError as exc:
        raise ValueError("process/task binding is not executable in this portal slice") from exc


def _require_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")
    return value
