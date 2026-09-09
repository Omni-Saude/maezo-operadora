"""Finite SP-OP-PROGRAMA schema proposals; deliberately NOT installed in SCHEMAS.

The proposal version is not a native wire version. No profile, role, PHI permission,
acquisition, task projection or runnable worker is supplied here. Inputs describe the
selected worker's projection, not the full process start contract: omission remains
possible so consent/discharge guards keep their existing refusal paths. Present values
are strictly typed; malformed legacy values require disposition integration review.

EngineSchema.read_projection cannot express types, optionality or classification.
The input_fields below MUST accompany any future native resource mapping; flattening
these to names is not qualification. PRIOR_HUMAN labels record provenance requirements,
never prove that a human acted. Clinical text needs separate PHI-zone classification.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from maezo.gateway.engine_contracts import (
    EngineCapabilityError,
    EngineOperation,
    EngineRefusalCode,
    EngineSchema,
    FieldOrigin,
    ValueKind,
    VariableField,
)

_CONTRACT = "docs/processes/contracts/SP-OP-PROGRAMA-001.md"
_BPMN = "spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn"
_WORKER = "src/maezo/tools/workers/programa.py"
_ADR = "docs/adr/0030-worker-error-semantics-bpmn-boundary.md"
_LIFECYCLE = (
    EngineOperation.FETCH_LOCK,
    EngineOperation.FAILURE,
    EngineOperation.EXTEND_LOCK,
    EngineOperation.UNLOCK,
)


def _strings(names: str, *, origin: FieldOrigin = FieldOrigin.FACT) -> tuple[VariableField, ...]:
    return tuple(VariableField(name, ValueKind.STRING, origin=origin) for name in names.split())


def _fixed(name: str, kind: ValueKind, value: bytes, *, required: bool = True) -> VariableField:
    return VariableField(name, kind, required=required, fixed_json=value)


_BENEFICIARY = _strings("beneficiario_pseudo_id")
_IDENTIFIERS = _strings("tenant_id", origin=FieldOrigin.BOUND) + _strings("programa_id") + _BENEFICIARY
_CONSENT = (
    VariableField("consentimento_ativo", ValueKind.BOOLEAN),
    VariableField("consent_checked", ValueKind.BOOLEAN),
    _fixed("consent_scope", ValueKind.STRING, b'"programa_cuidado"', required=False),
)


@dataclass(frozen=True, slots=True)
class ProgramaWorkerSchema:
    """One existing worker's structural proposal and explicit qualification gaps."""

    complete: EngineSchema
    input_fields: tuple[VariableField, ...]
    modeled_error: EngineSchema | None = None
    # (field, unresolved requirement); also includes actual outputs missing a canonical
    # variable declaration. Keeping their shape visible is not accepting the missing contract.
    unqualified_fields: tuple[tuple[str, str], ...] = ()

    def validate_inputs(self, variables: dict[str, Any]) -> None:
        """Validate a selected variable projection, never engine task metadata/authority."""
        replace(self.complete, fields=self.input_fields).validate(variables)

    @property
    def rows(self) -> tuple[EngineSchema, ...]:
        lifecycle = tuple(
            replace(
                self.complete,
                schema_id=f"{self.complete.schema_id}.{operation.value}",
                operation=operation,
                fields=(),
                error_codes=(),
            )
            for operation in _LIFECYCLE
        )
        error = (self.modeled_error,) if self.modeled_error is not None else ()
        return (self.complete, *error, *lifecycle)


def _worker(
    topic: str,
    function: str,
    activities: tuple[str, ...],
    inputs: tuple[VariableField, ...],
    outputs: tuple[VariableField, ...],
    *,
    error_code: str = "",
    gaps: tuple[tuple[str, str], ...] = (),
    extra_sources: tuple[str, ...] = (),
) -> ProgramaWorkerSchema:
    complete = EngineSchema(
        f"programa.{topic}.complete.proposal.v1",
        EngineOperation.COMPLETE,
        "SP-OP-PROGRAMA-001",
        "worker_runtime",
        outputs,
        (_CONTRACT, f"{_WORKER}:{function}", _ADR)
        + tuple(f"{_BPMN}#{activity}" for activity in activities)
        + extra_sources,
        topic=f"operadora.programa.{topic}",
        audit_actor="operadora-worker",
        read_projection=tuple(rule.name for rule in inputs),
    )
    error = (
        replace(
            complete,
            schema_id=f"programa.{topic}.bpmn_error.proposal.v1",
            operation=EngineOperation.BPMN_ERROR,
            fields=(),
            error_codes=(error_code,),
            sources=(*complete.sources, f"{_BPMN}#BE_SemConsentimento"),
        )
        if error_code
        else None
    )
    return ProgramaWorkerSchema(complete, inputs, error, gaps)


PROGRAMA_WORKER_SCHEMAS: tuple[ProgramaWorkerSchema, ...] = (
    _worker(
        "check_consent",
        "check_consent",
        ("ST_CheckConsent",),
        _BENEFICIARY + _CONSENT,
        (_fixed("consentimento_ativo", ValueKind.BOOLEAN, b"true"),),
        error_code="ERR_PROGRAMA_NO_CONSENT",
    ),
    _worker(
        "build_care_plan",
        "enroll_beneficiario",
        ("ST_BuildCarePlan", "ST_PrepareDossier"),
        _strings("programa_id") + _BENEFICIARY,
        # Contract explicitly makes gap presence conditional on the missing integration.
        (_fixed("enrollment_gap", ValueKind.STRING, b'"enroll_a2a_nao_ligado"', required=False),),
    ),
    _worker(
        "register_program_discharge",
        "register_program_discharge",
        ("ST_RegisterDischarge",),
        _BENEFICIARY
        + _strings(
            "decisao_programa motivo_desligamento_clinico referencia_clinica responsavel_clinico_id",
            origin=FieldOrigin.PRIOR_HUMAN,
        ),
        (
            _fixed("desligamento_clinico_registrado", ValueKind.BOOLEAN, b"true"),
            _fixed("desfecho", ValueKind.STRING, b'"desligamento_clinico_humano"'),
        ),
        gaps=(
            ("desligamento_clinico_registrado", "Actual worker output lacks canonical variable declaration"),
            ("decisao_programa", "Authenticated completed human decision provenance required"),
            ("motivo_desligamento_clinico", "Clinical text needs PHI classification and human provenance"),
            ("referencia_clinica", "Clinical reference needs PHI classification and human provenance"),
            ("responsavel_clinico_id", "Authenticated clinician identity binding required"),
        ),
    ),
    _worker(
        "stratify_risk",
        "make_stratify_risk_handler",
        ("ST_StratifyRisk",),
        _IDENTIFIERS + _CONSENT + _strings("risco_estratificado"),
        (
            VariableField("risco_estratificado", ValueKind.STRING, required=True),
            VariableField("risco_estratificado_origem", ValueKind.STRING, required=True),
        ),
        gaps=(
            ("risco_estratificado", "Clinical risk derivation/taxonomy remains DRAFT and in-zone"),
            ("risco_estratificado_origem", "Actual worker output lacks canonical variable declaration"),
        ),
        extra_sources=("spec/processes/dmn/programa_routing.dmn",),
    ),
    _worker(
        "stop_processing",
        "make_stop_processing_handler",
        ("ST_StopProcessing",),
        _IDENTIFIERS,
        (
            _fixed("processamento_parado", ValueKind.BOOLEAN, b"true"),
            _fixed("motivo", ValueKind.STRING, b'"revogacao_consentimento"'),
        ),
        gaps=(
            ("processamento_parado", "Actual output lacks canonical variable declaration/effect proof"),
            ("motivo", "Actual stop-worker output lacks canonical variable declaration"),
        ),
    ),
    _worker(
        "proactive_contact",
        "make_proactive_contact_handler",
        ("ST_ProactiveContact",),
        _IDENTIFIERS + _CONSENT,
        (_fixed("contato_gap", ValueKind.STRING, b'"contato_beneficiario_nao_ligado"', required=False),),
    ),
    _worker(
        "notify_sla_risk",
        "make_notify_sla_risk_handler",
        ("ST_NotifySlaRisk",),
        _IDENTIFIERS,
        (),
        extra_sources=("spec/processes/dmn/programa_sla.dmn",),
    ),
)

PROGRAMA_SCHEMA_PROPOSALS: tuple[EngineSchema, ...] = tuple(
    row for worker in PROGRAMA_WORKER_SCHEMAS for row in worker.rows
)


def programa_schema_proposal(topic: str, operation: EngineOperation, *, error_code: str = "") -> EngineSchema:
    """Resolve only the finite proposal; never participates in installed profile lookup."""
    if type(topic) is not str or type(operation) is not EngineOperation or type(error_code) is not str:
        raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
    for row in PROGRAMA_SCHEMA_PROPOSALS:
        if row.topic == topic and row.operation is operation:
            if (operation is EngineOperation.BPMN_ERROR and error_code not in row.error_codes) or (
                operation is not EngineOperation.BPMN_ERROR and error_code
            ):
                raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
            return row
    raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
