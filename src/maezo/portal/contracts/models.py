"""Closed, immutable contracts for the first human-portal foundation.

Sources:

* ADR-0049 D2-D6 (accepted engineering direction; technical specification DRAFT/verify);
* SP-OP-AUTH-001, SP-OP-ESCALATION-001, SP-OP-PAGTO-001, SP-OP-CONTAS-001,
  SP-OP-RECURSO-001 and SP-OP-REEMBOLSO-001 at the catalog's pinned R6 revision;
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
* PAGTO ``UT_AnaliseAdmissibilidade`` -> ``pagto_admissibilidade``;
* CONTAS ``UT_AnalistaContas`` -> ``contas_decisao``;
* CONTAS ``UT_CoordenacaoContasAssume`` -> ``contas_coordenacao``;
* RECURSO ``UT_AnaliseRecursoAnalista`` -> ``recurso_decisao``;
* RECURSO ``UT_CoordenacaoRecursoAssume`` and ``UT_EscalonamentoPrazo`` ->
  ``recurso_coordenacao``;
* RECURSO ``UT_RevisaoAuditorMedico`` -> ``recurso_auditor``.
* REEMBOLSO ``UT_DecidirPendenciaExpirada`` -> ``reembolso_pendencia``;
* REEMBOLSO ``UT_AnaliseReembolso`` and ``UT_CoordenacaoReembolso`` ->
  ``reembolso_decisao``;
* REEMBOLSO ``UT_RevisaoAuditorMedico`` -> ``reembolso_auditor``.

PAGTO admissibility is based on explicit BPMN task documentation and the plan, while the SP-OP
output table does not enumerate ``decisao_admissibilidade`` and the BPMN has no ``formData``.  Its
``form_source_status`` therefore remains DRAFT/verify.  No generic variables map exists.

The two CONTAS bindings are likewise DRAFT/verify: their contract and BPMN task documentation
enumerate the human fields, but the BPMN has no ``formData`` and the source tree has no deployment
version catalog.  The local binding validates task/form shape; it does not authenticate a supplied
positive ``process_definition_version`` or make either form runtime-usable.

The four RECURSO bindings have the same DRAFT/verify limitation.  Their DTOs preserve the payer
vocabulary from ADR-0040 and deliberately exclude actor identities and engine BRL variables.  The
future trusted gateway must inject the actor and perform the reviewed centavo-to-engine conversion;
these source contracts do neither and do not authorize an adverse effect.

The four REEMBOLSO bindings are also DRAFT/verify.  The contract output table omits
``SOLICITAR_AUDITOR``, while the analyst/coordination task documentation and its gateway explicitly
route that value.  It remains available only in those two draft-bound forms.  Approved money crosses
the browser boundary as a canonical decimal integer string; comparison with the trusted requested
amount and conversion to the engine integer belong to the future gateway.  Actor identities,
calculated values, requested values, and clinical-routing facts are never browser authority here.
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
        pattern=r"^[^\s\x00-\x1f\x7f/?#]+$",
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

FormKey = Literal[
    "auth_decisao",
    "auth_junta",
    "escalation",
    "pagto_admissibilidade",
    "contas_decisao",
    "contas_coordenacao",
    "recurso_decisao",
    "recurso_coordenacao",
    "recurso_auditor",
    "reembolso_pendencia",
    "reembolso_decisao",
    "reembolso_auditor",
]
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
    "decisao_contas",
    "justificativa_glosa",
    "codigo_glosa_tiss",
    "valor_glosado_centavos",
    "valor_liberado_centavos",
    "justificativa_devolucao",
    "decisao_coordenacao",
    "decisao_recurso",
    "fundamentacao_indeferimento",
    "valor_glosa_mantido_centavos",
    "valor_deferido_centavos",
    "referencia_contratual",
    "desfecho_humano",
    "decisao_auditor_recurso",
    "parecer_auditor",
    "decisao_pendencia",
    "decisao_reembolso",
    "valor_reembolso_aprovado_cents",
    "justificativa",
    "fundamentacao_contratual",
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
        # Small decimal chunks avoid Python's global decimal-string conversion limit without
        # changing that process-wide protection or introducing a financial ceiling (ADR-0049 D3).
        negative = self.root.startswith("-")
        digits = self.root[1:] if negative else self.root
        result = 0
        for offset in range(0, len(digits), 9):
            chunk = digits[offset : offset + 9]
            result = result * 10 ** len(chunk) + int(chunk)
        return -result if negative else result


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
        # urlsplit strips some controls before parsing. Reject them in the original identity,
        # and detect forbidden delimiters even when their query/fragment/credentials are empty.
        if any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value):
            raise ValueError("issuer URL must not contain controls or whitespace")
        if "?" in value or "#" in value:
            raise ValueError("issuer URL must not contain query or fragment")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
            raise ValueError("issuer must be an absolute HTTP(S) issuer URL")
        if "@" in parsed.netloc:
            raise ValueError("issuer URL must not contain credentials")
        # Accessing port is necessary: urlsplit alone accepts non-numeric/out-of-range ports.
        if parsed.port is None and parsed.netloc.endswith(":"):
            raise ValueError("issuer URL must have a valid port when specified")
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


class _ContasDecisionFields(_FrozenContract):
    """Fields shared by the two human CONTAS decision points.

    The SP-OP/BPMN engine variables are BRL doubles, while ADR-0049 D3 requires browser/API money
    to cross as canonical integer-centavo strings. These DTOs stop at that trusted boundary; they
    do not implement or authorize the still-unreviewed conversion into engine variables.
    """

    decisao_contas: Literal["PAGAR", "GLOSAR", "PAGAR_PARCIAL", "DEVOLVER", "ENCAMINHAR_FRAUDE"]
    justificativa_glosa: RequiredText | None = None
    codigo_glosa_tiss: RequiredText | None = None
    valor_glosado_centavos: Centavos | None = None
    valor_liberado_centavos: Centavos | None = None
    justificativa_devolucao: RequiredText | None = None

    @model_validator(mode="after")
    def _contractual_fields_are_complete(self) -> Self:
        if self.decisao_contas in {"GLOSAR", "PAGAR_PARCIAL"}:
            for field, value in (
                ("justificativa_glosa", self.justificativa_glosa),
                ("codigo_glosa_tiss", self.codigo_glosa_tiss),
            ):
                if value is None or not value.strip():
                    raise ValueError(f"{self.decisao_contas} requires {field}")
            if self.valor_glosado_centavos is None or self.valor_glosado_centavos.as_int() <= 0:
                raise ValueError(f"{self.decisao_contas} requires positive valor_glosado_centavos")

        if self.decisao_contas in {"PAGAR", "PAGAR_PARCIAL"} and (
            self.valor_liberado_centavos is None or self.valor_liberado_centavos.as_int() <= 0
        ):
            raise ValueError(f"{self.decisao_contas} requires positive valor_liberado_centavos")

        if self.decisao_contas == "DEVOLVER" and (
            self.justificativa_devolucao is None or not self.justificativa_devolucao.strip()
        ):
            raise ValueError("DEVOLVER requires justificativa_devolucao")
        return self


class ContasDecisionInputs(_ContasDecisionFields):
    """CONTAS analyst form; actor identity is injected from the authoritative session."""

    kind: Literal["contas_decisao"]


class ContasCoordinationInputs(_ContasDecisionFields):
    """CONTAS SLA takeover with the same human decision plus its coordination disposition."""

    kind: Literal["contas_coordenacao"]
    decisao_coordenacao: Literal["assumir_analise", "prorrogar_prazo", "seguir_analise"]


class _RecursoDecisionFields(_FrozenContract):
    """Contract fields shared by payer-side RECURSO decisions.

    Conditional fields come from SP-OP-RECURSO-001 lines 98-104 and the analyst/auditor task
    documentation at BPMN lines 316-325 and 448-454.  Positive deferred centavos also preserve the
    contract's fail-closed handoff condition at line 308; this DTO does not execute that handoff.

    SP-OP-RECURSO-001 names BRL engine variables.  ADR-0049 D3 instead requires canonical
    integer-centavo strings at the browser boundary.  The original glosa amount stays trusted
    evidence, so the browser cannot submit it to make the permanent partial-sum guard true.
    """

    fundamentacao_indeferimento: RequiredText | None = None
    valor_glosa_mantido_centavos: Centavos | None = None
    valor_deferido_centavos: Centavos | None = None
    referencia_contratual: RequiredText | None = None

    def _validate_recurso_outcome(self, decision: str) -> None:
        if decision in {"INDEFERIR", "DEFERIR_PARCIAL"}:
            for field, value in (
                ("fundamentacao_indeferimento", self.fundamentacao_indeferimento),
                ("referencia_contratual", self.referencia_contratual),
            ):
                if value is None or not value.strip():
                    raise ValueError(f"{decision} requires {field}")
            if self.valor_glosa_mantido_centavos is None or self.valor_glosa_mantido_centavos.as_int() <= 0:
                raise ValueError(f"{decision} requires positive valor_glosa_mantido_centavos")

        if decision in {"DEFERIR", "DEFERIR_PARCIAL"} and (
            self.valor_deferido_centavos is None or self.valor_deferido_centavos.as_int() <= 0
        ):
            raise ValueError(f"{decision} requires positive valor_deferido_centavos")


class RecursoDecisionInputs(_RecursoDecisionFields):
    """Primary analyst decision (BPMN lines 313-329); the gateway injects actor identity."""

    kind: Literal["recurso_decisao"]
    decisao_recurso: Literal["DEFERIR", "DEFERIR_PARCIAL", "INDEFERIR", "SOLICITAR_INFO", "ESCALAR_AUDITOR"]

    @model_validator(mode="after")
    def _contractual_fields_are_complete(self) -> Self:
        self._validate_recurso_outcome(self.decisao_recurso)
        return self


class RecursoCoordinationInputs(_RecursoDecisionFields):
    """SLA/teto decision, including BPMN lines 370-372/424-426 human inadmissibility."""

    kind: Literal["recurso_coordenacao"]
    decisao_recurso: Literal["DEFERIR", "DEFERIR_PARCIAL", "INDEFERIR", "SOLICITAR_INFO", "ESCALAR_AUDITOR"]
    desfecho_humano: Literal["inadmissivel"] | None = None

    @model_validator(mode="after")
    def _contractual_fields_are_complete(self) -> Self:
        self._validate_recurso_outcome(self.decisao_recurso)
        if self.desfecho_humano is not None and self.decisao_recurso != "INDEFERIR":
            raise ValueError("desfecho_humano=inadmissivel requires decisao_recurso=INDEFERIR")
        return self


class RecursoAuditorInputs(_RecursoDecisionFields):
    """Auditor merit form (BPMN lines 445-458); no clinical fact or actor identity is inferred."""

    kind: Literal["recurso_auditor"]
    decisao_auditor_recurso: Literal["DEFERIR", "DEFERIR_PARCIAL", "INDEFERIR"]
    parecer_auditor: RequiredText

    @model_validator(mode="after")
    def _contractual_fields_are_complete(self) -> Self:
        if not self.parecer_auditor.strip():
            raise ValueError("parecer_auditor must not be blank")
        self._validate_recurso_outcome(self.decisao_auditor_recurso)
        return self


class ReembolsoPendingInputs(_FrozenContract):
    """Expired-document pendency decision (contract lines 92/214/233-236; BPMN 188-193)."""

    kind: Literal["reembolso_pendencia"]
    decisao_pendencia: Literal[
        "cancelar_solicitacao",
        "conceder_prazo_extra",
        "seguir_analise",
    ]


class _ReembolsoDecisionFields(_FrozenContract):
    """Shared reimbursement decision fields at the browser trust boundary.

    Contract lines 81 and 87-91 and BPMN lines 313-325 require human justification and
    contractual basis for denial/reduction, plus an approved amount for payment.  The worker's
    fail-closed money guard at contract line 224 requires that amount to be positive.

    The contract already names integer cents.  ADR-0049 D3 changes only their browser transport
    representation to a canonical decimal integer string.  This DTO neither compares a partial
    amount with trusted ``valor_solicitado_cents`` nor executes payment.
    """

    valor_reembolso_aprovado_cents: Centavos | None = None
    justificativa: RequiredText | None = None
    fundamentacao_contratual: RequiredText | None = None

    def _validate_reembolso_outcome(self, decision: str) -> None:
        if decision in {"NEGAR", "APROVAR_PARCIAL"}:
            for field, value in (
                ("justificativa", self.justificativa),
                ("fundamentacao_contratual", self.fundamentacao_contratual),
            ):
                if value is None or not value.strip():
                    raise ValueError(f"{decision} requires {field}")

        if decision in {"APROVAR", "APROVAR_PARCIAL"} and (
            self.valor_reembolso_aprovado_cents is None or self.valor_reembolso_aprovado_cents.as_int() <= 0
        ):
            raise ValueError(f"{decision} requires positive valor_reembolso_aprovado_cents")


class ReembolsoDecisionInputs(_ReembolsoDecisionFields):
    """Analyst/coordination form; identities and clinical merit stay server/auditor-owned."""

    kind: Literal["reembolso_decisao"]
    decisao_reembolso: Literal[
        "APROVAR",
        "NEGAR",
        "APROVAR_PARCIAL",
        "SOLICITAR_INFO",
        "SOLICITAR_AUDITOR",
    ]

    @model_validator(mode="after")
    def _contractual_fields_are_complete(self) -> Self:
        self._validate_reembolso_outcome(self.decisao_reembolso)
        return self


class ReembolsoAuditorInputs(_ReembolsoDecisionFields):
    """Medical-merit form (BPMN 383-388); no clinical content is inferred."""

    kind: Literal["reembolso_auditor"]
    decisao_reembolso: Literal["APROVAR", "NEGAR", "APROVAR_PARCIAL"]
    cid10_referencia: RequiredText | None = None
    parecer_auditor: RequiredText | None = None

    @model_validator(mode="after")
    def _contractual_fields_are_complete(self) -> Self:
        self._validate_reembolso_outcome(self.decisao_reembolso)
        if self.decisao_reembolso in {"NEGAR", "APROVAR_PARCIAL"}:
            for field, value in (
                ("cid10_referencia", self.cid10_referencia),
                ("parecer_auditor", self.parecer_auditor),
            ):
                if value is None or not value.strip():
                    raise ValueError(f"{self.decisao_reembolso} requires {field}")
        return self


DecisionInputs = Annotated[
    AuthDecisionInputs
    | AuthJuntaInputs
    | EscalationDecisionInputs
    | PagtoAdmissibilityInputs
    | ContasDecisionInputs
    | ContasCoordinationInputs
    | RecursoDecisionInputs
    | RecursoCoordinationInputs
    | RecursoAuditorInputs
    | ReembolsoPendingInputs
    | ReembolsoDecisionInputs
    | ReembolsoAuditorInputs,
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
    ("SP-OP-CONTAS-001", "UT_AnalistaContas"): (
        "contas_decisao",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-CONTAS-001", "UT_CoordenacaoContasAssume"): (
        "contas_coordenacao",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-RECURSO-001", "UT_AnaliseRecursoAnalista"): (
        "recurso_decisao",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-RECURSO-001", "UT_CoordenacaoRecursoAssume"): (
        "recurso_coordenacao",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-RECURSO-001", "UT_EscalonamentoPrazo"): (
        "recurso_coordenacao",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-RECURSO-001", "UT_RevisaoAuditorMedico"): (
        "recurso_auditor",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-REEMBOLSO-001", "UT_DecidirPendenciaExpirada"): (
        "reembolso_pendencia",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-REEMBOLSO-001", "UT_AnaliseReembolso"): (
        "reembolso_decisao",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-REEMBOLSO-001", "UT_CoordenacaoReembolso"): (
        "reembolso_decisao",
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    ),
    ("SP-OP-REEMBOLSO-001", "UT_RevisaoAuditorMedico"): (
        "reembolso_auditor",
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
    "contas_decisao": (
        "decisao_contas",
        "justificativa_glosa",
        "codigo_glosa_tiss",
        "valor_glosado_centavos",
        "valor_liberado_centavos",
        "justificativa_devolucao",
    ),
    "contas_coordenacao": (
        "decisao_contas",
        "justificativa_glosa",
        "codigo_glosa_tiss",
        "valor_glosado_centavos",
        "valor_liberado_centavos",
        "justificativa_devolucao",
        "decisao_coordenacao",
    ),
    "recurso_decisao": (
        "decisao_recurso",
        "fundamentacao_indeferimento",
        "valor_glosa_mantido_centavos",
        "valor_deferido_centavos",
        "referencia_contratual",
    ),
    "recurso_coordenacao": (
        "decisao_recurso",
        "fundamentacao_indeferimento",
        "valor_glosa_mantido_centavos",
        "valor_deferido_centavos",
        "referencia_contratual",
        "desfecho_humano",
    ),
    "recurso_auditor": (
        "decisao_auditor_recurso",
        "parecer_auditor",
        "fundamentacao_indeferimento",
        "valor_glosa_mantido_centavos",
        "valor_deferido_centavos",
        "referencia_contratual",
    ),
    "reembolso_pendencia": ("decisao_pendencia",),
    "reembolso_decisao": (
        "decisao_reembolso",
        "valor_reembolso_aprovado_cents",
        "justificativa",
        "fundamentacao_contratual",
    ),
    "reembolso_auditor": (
        "decisao_reembolso",
        "valor_reembolso_aprovado_cents",
        "justificativa",
        "fundamentacao_contratual",
        "cid10_referencia",
        "parecer_auditor",
    ),
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
