"""Exact fourteen-variable AUTH projection agreed with native AuthValues.

Trace: approved E04 mechanical amendment v1, DECIMAL-PROJECTION.md. Selects
already-qualified facts; it never computes clinical facts or documentary policy.
"""

from datetime import datetime
from typing import Any

from maezo.portal.contracts.intake import AuthIntakeSubmission
from maezo.portal.engine.profile import canonicalize, strict_loads
from maezo.tools.workers.auth_exact_amount import TYPE, AuthExactAmount

from .auth_profile import (
    Actor,
    AuditIntent,
    Definition,
    DocumentCustody,
    DocumentPolicy,
    DocumentRef,
    GuideIdentity,
    HumanStartCommand,
    Pin,
    ResourceAuthority,
    Scope,
    StartFacts,
    validate_documents,
)
from .read_profile import digest, parse_model, wire

_STRINGS = (
    "tenant_id",
    "guide_ref",
    "beneficiario_pseudo_id",
    "prestador_id",
    "codigo_procedimento_tuss",
    "carater_atendimento",
    "categoria_procedimento",
)
_BOOLS = ("requer_autorizacao", "beneficiario_ativo", "carencia_cumprida", "documentacao_completa")
_JSON = ("documentos_refs", "missing_docs")


def start_variables(tenant: str, facts: StartFacts) -> dict[str, Any]:
    facts = parse_model(StartFacts, wire(facts))
    return dict(
        tenant_id=tenant,
        guide_ref=facts.guide_identity_ref,
        beneficiario_pseudo_id=facts.beneficiary_pseudo_id,
        prestador_id=facts.provider_ref,
        codigo_procedimento_tuss=facts.procedure_code,
        carater_atendimento=facts.character,
        categoria_procedimento=facts.category,
        valor_estimado_brl=AuthExactAmount(facts.claimed_amount_cents),
        documentos_refs=canonicalize(wire(facts.document_refs)).decode(),
        missing_docs=canonicalize(list(facts.missing_requirement_codes)).decode(),
        requer_autorizacao=facts.requer_autorizacao,
        beneficiario_ativo=facts.beneficiario_ativo,
        carencia_cumprida=facts.carencia_cumprida,
        documentacao_completa=facts.documentacao_completa,
    )


def projection_descriptor(variables: dict[str, Any]) -> dict[str, Any]:
    if type(variables) is not dict or variables.keys() != set(
        _STRINGS + _BOOLS + _JSON + ("valor_estimado_brl",)
    ):
        raise ValueError("invalid AUTH projection")
    result = {}
    for name in _STRINGS:
        value = variables[name]
        if type(value) is not str or not value:
            raise ValueError("invalid AUTH string")
        result[name] = {"type": "String", "value": value}
    for name in _BOOLS:
        if type(variables[name]) is not bool:
            raise ValueError("invalid AUTH boolean")
        result[name] = {"type": "Boolean", "value": variables[name]}
    for name in _JSON:
        value = variables[name]
        if type(value) is not str:
            raise ValueError("invalid AUTH JSON")
        parsed = strict_loads(value.encode())
        if type(parsed) is not list or canonicalize(parsed).decode() != value:
            raise ValueError("invalid AUTH JSON")
        if name == "documentos_refs":
            validate_documents(tuple(parse_model(DocumentRef, d) for d in parsed))
        elif any(type(v) is not str for v in parsed) or len(set(parsed)) != len(parsed):
            raise ValueError("invalid AUTH requirements")
        result[name] = {"type": "Json", "value": value}
    amount = variables["valor_estimado_brl"]
    if type(amount) is not AuthExactAmount:
        raise ValueError("invalid AUTH exact money")
    result["valor_estimado_brl"] = {"type": TYPE, "value": amount.decimal_text}
    return {"schema": "human-auth-start-projection.v1", "variables": result}


def projection_digest(variables: dict[str, Any]) -> str:
    return digest(projection_descriptor(variables))


def build_start_command(
    *,
    scope: Scope,
    workload_ref: str,
    actor: Actor,
    intake_ref: str,
    request: AuthIntakeSubmission,
    admission: AuditIntent,
    definition: Definition,
    facts: StartFacts,
    guide: GuideIdentity,
    authority: ResourceAuthority,
    policy: DocumentPolicy,
    custody: tuple[DocumentCustody, ...],
    pins: tuple[Pin, ...],
    now: datetime,
) -> HumanStartCommand:
    """Bind actual qualified source projections to the immutable browser admission.

    AMH beneficiary reference to pseudonym mapping is attested by the designated facts
    source under this exact admitted digest; those two identifiers are never equated.
    """
    from maezo.gateway.intake.models import request_bytes
    from maezo.portal.engine.profile import strict_loads

    from .auth_transport import AuthUnavailableError

    admitted = digest(
        {
            "schema": "human-auth-admission.v1",
            "request": strict_loads(request_bytes(request)),
            "guide_identity_ref": guide.guide_identity_ref,
        }
    )
    if (
        admission.admitted_digest != admitted
        or admission.admitted_command_id != request.command_id
        or (facts.intake_ref, facts.guide_identity_ref, facts.request_digest)
        != (intake_ref, guide.guide_identity_ref, admitted)
        or (
            facts.provider_ref,
            facts.procedure_code,
            facts.category,
            facts.character,
            facts.claimed_amount_cents,
        )
        != (
            request.provider_ref,
            request.codigo_procedimento_tuss,
            request.categoria_procedimento,
            request.carater_atendimento,
            int(request.valor_estimado_centavos),
        )
        or tuple(d.document_ref for d in facts.document_refs) != tuple(sorted(request.document_refs))
        or guide.legacy_state not in ("absent_at_cutover", "existing")
        or (
            authority.actor,
            authority.beneficiary_ref,
            authority.provider_ref,
            authority.action,
            authority.resource_kind,
            authority.resource_ref,
        )
        != (
            actor,
            request.beneficiary_ref,
            request.provider_ref,
            "auth.start",
            "guide",
            guide.guide_identity_ref,
        )
        or authority.state != "active"
        or authority.consent_state == "revoked"
        or (
            policy.resource_kind,
            policy.resource_ref,
            policy.assessment_ref,
            policy.effective_document_refs,
            policy.complete,
            policy.missing_codes,
        )
        != (
            "intake",
            intake_ref,
            facts.documentary_assessment_ref,
            facts.document_refs,
            facts.documentacao_completa,
            facts.missing_requirement_codes,
        )
        or not authority.valid_from
        <= now
        < min(
            authority.valid_until, policy.valid_until, guide.source.valid_until, admission.source.valid_until
        )
    ):
        raise AuthUnavailableError()
    expected = {
        ("actor", actor.principal_ref): None,
        ("guide", guide.guide_identity_ref): digest(guide),
        ("resource_authority", authority.authority_ref): digest(authority),
        ("start_facts", facts.facts_ref): digest(facts),
        ("document_policy", policy.assessment_ref): digest(policy),
    }
    if len(custody) != len(facts.document_refs):
        raise AuthUnavailableError()
    for document, record in zip(facts.document_refs, custody, strict=True):
        if (
            record.document != document
            or (record.resource_kind, record.resource_ref, record.screening_result, record.custody_state)
            != ("intake", intake_ref, "clean", "available")
            or now >= record.valid_until
        ):
            raise AuthUnavailableError()
        expected[("document_custody", document.document_ref)] = digest(record)
    if {(p.kind, p.resource_ref) for p in pins} != expected.keys():
        raise AuthUnavailableError()
    for pin in pins:
        if now >= pin.source.valid_until or (
            expected[(pin.kind, pin.resource_ref)] is not None
            and pin.payload_digest != expected[(pin.kind, pin.resource_ref)]
        ):
            raise AuthUnavailableError()
    return HumanStartCommand(
        schema="human-auth-start.v1",
        scope=scope,
        workload_ref=workload_ref,
        actor=actor,
        intake_ref=intake_ref,
        command_id=request.command_id,
        admission=admission,
        guide_identity_ref=guide.guide_identity_ref,
        definition=definition,
        input_pins=pins,
        start_facts_ref=facts.facts_ref,
        start_facts_digest=digest(facts),
        projected_variables_digest=projection_digest(start_variables(scope.tenant, facts)),
    )
