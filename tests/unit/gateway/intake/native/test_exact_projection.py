"""Exact arithmetic reaches the actual AUTH worker ceiling helper without float."""

from decimal import Decimal

import pytest

from maezo.gateway.human.auth_projection import projection_descriptor, projection_digest
from maezo.portal.engine.profile import canonicalize
from maezo.tools.workers.auth import _ceiling_valor_cents
from maezo.tools.workers.auth_exact_amount import TYPE, AuthExactAmount, hydrate_auth_amount
from maezo.tools.workers.harness import _from_camunda_var


def variables(cents=1):
    return dict(
        tenant_id="tenant",
        guide_ref="guide",
        beneficiario_pseudo_id="beneficiary",
        prestador_id="provider",
        codigo_procedimento_tuss="10101012",
        carater_atendimento="eletivo",
        categoria_procedimento="consulta",
        valor_estimado_brl=AuthExactAmount(cents),
        documentos_refs="[]",
        missing_docs="[]",
        requer_autorizacao=True,
        beneficiario_ativo=True,
        carencia_cumprida=True,
        documentacao_completa=True,
    )


@pytest.mark.parametrize(
    "cents,text", [(0, "0.00"), (1, "0.01"), (100, "1.00"), (2**63 - 1, "92233720368547758.07")]
)
def test_signed64_scale_two_end_to_end_worker_arithmetic(cents, text):
    projected = projection_descriptor(variables(cents))
    wire = projected["variables"]["valor_estimado_brl"]
    assert wire == {"type": TYPE, "value": text}
    hydrated = hydrate_auth_amount(wire, input_profile="portal-auth-intake.v1")
    assert hydrated.cents == cents and _ceiling_valor_cents(hydrated) == cents
    assert b'"type":"maezo-auth-exact-decimal.v1"' in canonicalize(projected)


@pytest.mark.parametrize(
    "value",
    [
        "01.00",
        "1.0",
        "+1.00",
        "-0.01",
        "1e2",
        "NaN",
        "Infinity",
        "92233720368547758.08",
        1.00,
        True,
        Decimal("1.00"),
    ],
)
def test_custom_wire_refuses_lossy_or_noncanonical_values(value):
    with pytest.raises(ValueError):
        hydrate_auth_amount({"type": TYPE, "value": value}, input_profile="portal-auth-intake.v1")


@pytest.mark.parametrize("cents", [True, -1, 1.0, Decimal(1), 2**63])
def test_exact_wrapper_refuses_noninteger_or_overflow(cents):
    with pytest.raises(ValueError):
        AuthExactAmount(cents)


def test_generic_worker_never_erases_exact_type():
    with pytest.raises(ValueError):
        _from_camunda_var({"type": TYPE, "value": "92233720368547758.07"})
    assert _from_camunda_var({"type": "String", "value": "500.001"}) == "500.001"
    assert _ceiling_valor_cents("500.001") == 50001
    assert _ceiling_valor_cents(Decimal("1.00")) is None


def test_projection_closed_types_and_stable_vector():
    import hashlib

    v = variables()
    descriptor = projection_descriptor(v)
    assert projection_digest(v) == hashlib.sha256(canonicalize(descriptor)).hexdigest()
    assert len(descriptor["variables"]) == 14
    for bad in [
        dict(v, extra=True),
        dict(v, valor_estimado_brl="0.01"),
        dict(v, beneficiario_ativo=1),
        dict(v, documentos_refs="[ ]"),
    ]:
        with pytest.raises(ValueError):
            projection_digest(bad)
    assert projection_digest(variables(1)) != projection_digest(variables(2))


def test_start_projection_binds_claimed_request_and_qualified_sources_without_identity_guess():
    import json
    from datetime import timedelta

    from tests.unit.gateway.intake.native.test_wire_transport import HASH, NOW, command, source
    from tests.unit.portal.test_intake_documents import submission

    from maezo.gateway.human.auth_profile import (
        AuditIntent,
        DocumentPolicy,
        GuideIdentity,
        Pin,
        ResourceAuthority,
        StartFacts,
    )
    from maezo.gateway.human.auth_projection import build_start_command
    from maezo.gateway.human.auth_transport import AuthUnavailableError
    from maezo.gateway.human.read_profile import ArtifactPin, digest
    from maezo.gateway.intake.models import request_bytes
    from maezo.portal.contracts.intake import AuthIntakeSubmission
    from maezo.portal.engine.profile import strict_loads

    c = command()
    raw = submission()
    raw["valor_estimado_centavos"] = "12500"
    request = AuthIntakeSubmission.model_validate_json(json.dumps(raw))
    admitted = digest(
        {
            "schema": "human-auth-admission.v1",
            "request": strict_loads(request_bytes(request)),
            "guide_identity_ref": c.guide_identity_ref,
        }
    )
    admission = AuditIntent(
        intent_ref="intake", admitted_command_id=request.command_id, admitted_digest=admitted, source=source()
    )
    facts = StartFacts(
        facts_ref="facts",
        intake_ref="intake",
        guide_identity_ref="guide",
        beneficiary_pseudo_id="different-protected-pseudonym",
        provider_ref=request.provider_ref,
        procedure_code=request.codigo_procedimento_tuss,
        category=request.categoria_procedimento,
        character=request.carater_atendimento,
        claimed_amount_cents=12500,
        document_refs=(),
        requer_autorizacao=True,
        beneficiario_ativo=True,
        carencia_cumprida=True,
        documentacao_completa=True,
        missing_requirement_codes=(),
        documentary_assessment_ref="assessment",
        request_digest=admitted,
        factual_sources=(source(),),
        policy_artifacts=(ArtifactPin(artifact_ref="policy", digest=HASH),),
    )
    guide = GuideIdentity(
        guide_identity_ref="guide",
        source_ref="source",
        namespace_ref="namespace",
        source_guide_ref="protected-guide",
        cutover_ref="cutover",
        cutover_revision=1,
        legacy_state="absent_at_cutover",
        prior_instance_id=None,
        prior_case_ref=None,
        source=source(),
    )
    authority = ResourceAuthority(
        authority_ref="authority",
        actor=c.actor,
        beneficiary_ref=request.beneficiary_ref,
        provider_ref=request.provider_ref,
        resource_kind="guide",
        resource_ref="guide",
        action="auth.start",
        request_ref=None,
        relationship_revision=1,
        consent_revision=1,
        grant_ref="grant",
        basis_ref="basis",
        legal_basis="consent",
        consent_state="valid",
        state="active",
        valid_from=NOW,
        valid_until=NOW + timedelta(seconds=60),
        source=source(),
    )
    policy = DocumentPolicy(
        assessment_ref="assessment",
        resource_kind="intake",
        resource_ref="intake",
        request_ref=None,
        request_revision=0,
        policy=ArtifactPin(artifact_ref="policy", digest=HASH),
        policy_revision=1,
        recipient_principal_refs=(c.actor.principal_ref,),
        required_codes=(),
        missing_codes=(),
        submitted_response_digest=None,
        effective_document_refs=(),
        document_set_digest=digest(()),
        complete=True,
        source=source(),
        valid_until=NOW + timedelta(seconds=60),
    )
    pins = tuple(
        Pin(
            kind=k,
            resource_ref=r,
            head_generation=1,
            source=source(),
            payload_digest=digest(p) if p is not None else HASH,
        )
        for k, r, p in [
            ("actor", c.actor.principal_ref, None),
            ("document_policy", "assessment", policy),
            ("guide", "guide", guide),
            ("resource_authority", "authority", authority),
            ("start_facts", "facts", facts),
        ]
    )
    kwargs = dict(
        scope=c.scope,
        workload_ref=c.workload_ref,
        actor=c.actor,
        intake_ref="intake",
        request=request,
        admission=admission,
        definition=c.definition,
        facts=facts,
        guide=guide,
        authority=authority,
        policy=policy,
        custody=(),
        pins=pins,
        now=NOW,
    )
    result = build_start_command(**kwargs)
    assert result.admission.admitted_digest == admitted
    assert facts.beneficiary_pseudo_id != request.beneficiary_ref
    for changed in [
        dict(facts=facts.model_copy(update={"claimed_amount_cents": 1})),
        dict(authority=authority.model_copy(update={"beneficiary_ref": "other"})),
        dict(policy=policy.model_copy(update={"complete": False})),
        dict(guide=guide.model_copy(update={"legacy_state": "unreconciled"})),
    ]:
        with pytest.raises(AuthUnavailableError):
            build_start_command(**(kwargs | changed))
