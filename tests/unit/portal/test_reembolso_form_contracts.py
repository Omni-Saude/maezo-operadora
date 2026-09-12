"""Closed REEMBOLSO portal forms for all four human User Tasks.

Contract anchors:

* SP-OP-REEMBOLSO-001 lines 11-27 keep denial/reduction L0-hard and human-only;
* its output table at lines 81 and 87-92 defines decisions, approved cents, conditional
  justification/contract/clinical fields, and the expired-pendency decision;
* BPMN lines 188-193, 313-328, 364-369 and 383-388 define the four task-specific forms;
* BPMN lines 527-539 explicitly route SOLICITAR_AUDITOR, retained under DRAFT/verify because the
  contract output table does not list that value;
* ADR-0049 D2-D3 requires closed bindings and canonical decimal integer strings for browser money.

These tests prove immutable source DTO parsing only. They do not authenticate a deployment version,
inject a human identity, supply clinical facts, compare a partial amount with trusted requested
value, convert through float, issue payment, or authorize an adverse effect.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import (
    ReembolsoAuditorInputs,
    ReembolsoDecisionInputs,
    ReembolsoPendingInputs,
    TaskDecision,
    TaskSnapshot,
)

NOW = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
DIGEST = "e" * 64
DECISION_INPUTS = (
    "decisao_reembolso",
    "valor_reembolso_aprovado_cents",
    "justificativa",
    "fundamentacao_contratual",
)
AUDITOR_INPUTS = (*DECISION_INPUTS, "cid10_referencia", "parecer_auditor")
PENDING_INPUTS = ("decisao_pendencia",)


def _decision(*, task_key: str, form_key: str, inputs: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": "command-reembolso-1",
        "task_id": "task-reembolso-1",
        "process_definition_key": "SP-OP-REEMBOLSO-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-REEMBOLSO-001:7:deployment",
        "process_definition_digest": DIGEST,
        "task_definition_key": task_key,
        "form_key": form_key,
        "form_version": 1,
        "form_digest": DIGEST,
        "expected_task_revision": 11,
        "expected_evidence_revision": 4,
        "expected_evidence_digest": DIGEST,
        "expected_membership_revision": 3,
        "inputs": inputs,
    }


def _snapshot(*, task_key: str, form_key: str, allowed_inputs: tuple[str, ...]) -> dict[str, object]:
    group = {
        "UT_DecidirPendenciaExpirada": "analise-reembolso",
        "UT_AnaliseReembolso": "analise-reembolso",
        "UT_CoordenacaoReembolso": "coordenacao-reembolso",
        "UT_RevisaoAuditorMedico": "medico-auditor",
    }[task_key]
    return {
        "schema_version": 1,
        "snapshot_at": NOW,
        "task_id": "task-reembolso-1",
        "process_definition_key": "SP-OP-REEMBOLSO-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-REEMBOLSO-001:7:deployment",
        "process_definition_digest": DIGEST,
        "task_definition_key": task_key,
        "form_key": form_key,
        "form_version": 1,
        "form_digest": DIGEST,
        "form_source_status": "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        "task_revision": 11,
        "assignee_ref": None,
        "eligible_candidate_groups": (group,),
        "evidence_revision": 4,
        "evidence_digest": DIGEST,
        "engine_due_at": None,
        "allowed_actions": ("claim", "decision"),
        "allowed_inputs": allowed_inputs,
        "read_only_evidence": None,
    }


@pytest.mark.parametrize(
    "inputs",
    [
        {
            "kind": "reembolso_decisao",
            "decisao_reembolso": "APROVAR",
            "valor_reembolso_aprovado_cents": "9007199254740993",
        },
        {
            "kind": "reembolso_decisao",
            "decisao_reembolso": "NEGAR",
            "justificativa": "Pedido fora das condições contratuais",
            "fundamentacao_contratual": "Cláusula 5.2",
        },
        {
            "kind": "reembolso_decisao",
            "decisao_reembolso": "APROVAR_PARCIAL",
            "valor_reembolso_aprovado_cents": "8000",
            "justificativa": "Valor reduzido após análise humana",
            "fundamentacao_contratual": "Cláusula 6.1",
        },
        {"kind": "reembolso_decisao", "decisao_reembolso": "SOLICITAR_INFO"},
        {"kind": "reembolso_decisao", "decisao_reembolso": "SOLICITAR_AUDITOR"},
    ],
)
@pytest.mark.parametrize("task_key", ["UT_AnaliseReembolso", "UT_CoordenacaoReembolso"])
def test_each_analyst_and_coordination_outcome_parses_from_json(
    task_key: str, inputs: dict[str, object]
) -> None:
    decision = TaskDecision.model_validate_json(
        json.dumps(_decision(task_key=task_key, form_key="reembolso_decisao", inputs=inputs))
    )

    assert isinstance(decision.inputs, ReembolsoDecisionInputs)
    assert decision.inputs.decisao_reembolso == inputs["decisao_reembolso"]


@pytest.mark.parametrize(
    "outcome",
    ["cancelar_solicitacao", "conceder_prazo_extra", "seguir_analise"],
)
def test_each_expired_pendency_outcome_parses(outcome: str) -> None:
    decision = TaskDecision.model_validate(
        _decision(
            task_key="UT_DecidirPendenciaExpirada",
            form_key="reembolso_pendencia",
            inputs={"kind": "reembolso_pendencia", "decisao_pendencia": outcome},
        )
    )

    assert isinstance(decision.inputs, ReembolsoPendingInputs)
    assert decision.inputs.decisao_pendencia == outcome


@pytest.mark.parametrize(
    "inputs",
    [
        {
            "kind": "reembolso_auditor",
            "decisao_reembolso": "APROVAR",
            "valor_reembolso_aprovado_cents": "12000",
        },
        {
            "kind": "reembolso_auditor",
            "decisao_reembolso": "NEGAR",
            "justificativa": "Mérito clínico desfavorável",
            "fundamentacao_contratual": "Diretriz contratual",
            "cid10_referencia": "Z00.0",
            "parecer_auditor": "Parecer médico fundamentado",
        },
        {
            "kind": "reembolso_auditor",
            "decisao_reembolso": "APROVAR_PARCIAL",
            "valor_reembolso_aprovado_cents": "8000",
            "justificativa": "Redução após revisão médica",
            "fundamentacao_contratual": "Diretriz contratual",
            "cid10_referencia": "Z00.0",
            "parecer_auditor": "Parecer médico fundamentado",
        },
    ],
)
def test_each_auditor_outcome_parses(inputs: dict[str, object]) -> None:
    decision = TaskDecision.model_validate_json(
        json.dumps(
            _decision(
                task_key="UT_RevisaoAuditorMedico",
                form_key="reembolso_auditor",
                inputs=inputs,
            )
        )
    )

    assert isinstance(decision.inputs, ReembolsoAuditorInputs)
    assert decision.inputs.decisao_reembolso == inputs["decisao_reembolso"]


@pytest.mark.parametrize("outcome", ["AUTO_APROVAR", "ANALISE_HUMANA", "REDUZIR", ""])
def test_human_form_rejects_dmn_and_invented_outcomes(outcome: str) -> None:
    with pytest.raises(ValidationError):
        ReembolsoDecisionInputs.model_validate({"kind": "reembolso_decisao", "decisao_reembolso": outcome})


@pytest.mark.parametrize("decision", ["NEGAR", "APROVAR_PARCIAL"])
@pytest.mark.parametrize("missing", ["justificativa", "fundamentacao_contratual"])
def test_adverse_decision_requires_human_basis(decision: str, missing: str) -> None:
    payload: dict[str, object] = {
        "kind": "reembolso_decisao",
        "decisao_reembolso": decision,
        "justificativa": "Fundamento",
        "fundamentacao_contratual": "Cláusula",
    }
    if decision == "APROVAR_PARCIAL":
        payload["valor_reembolso_aprovado_cents"] = "1"
    del payload[missing]

    with pytest.raises(ValidationError, match=missing):
        ReembolsoDecisionInputs.model_validate(payload)


@pytest.mark.parametrize("decision", ["APROVAR", "APROVAR_PARCIAL"])
def test_payment_decision_requires_approved_cent_amount(decision: str) -> None:
    payload: dict[str, object] = {
        "kind": "reembolso_decisao",
        "decisao_reembolso": decision,
    }
    if decision == "APROVAR_PARCIAL":
        payload.update(
            justificativa="Fundamento",
            fundamentacao_contratual="Cláusula",
        )

    with pytest.raises(ValidationError, match="valor_reembolso_aprovado_cents"):
        ReembolsoDecisionInputs.model_validate(payload)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_required_approved_amount_must_be_positive(value: str) -> None:
    with pytest.raises(ValidationError, match="valor_reembolso_aprovado_cents"):
        ReembolsoDecisionInputs.model_validate(
            {
                "kind": "reembolso_decisao",
                "decisao_reembolso": "APROVAR",
                "valor_reembolso_aprovado_cents": value,
            }
        )


@pytest.mark.parametrize("value", [1, True, 1.0, "01", "+1", "1.0", "1e2", " 1", "NaN"])
def test_money_rejects_unsafe_browser_coercion(value: object) -> None:
    with pytest.raises(ValidationError):
        ReembolsoDecisionInputs.model_validate(
            {
                "kind": "reembolso_decisao",
                "decisao_reembolso": "APROVAR",
                "valor_reembolso_aprovado_cents": value,
            }
        )


@pytest.mark.parametrize("decision", ["NEGAR", "APROVAR_PARCIAL"])
@pytest.mark.parametrize(
    "missing",
    ["justificativa", "fundamentacao_contratual", "cid10_referencia", "parecer_auditor"],
)
def test_adverse_auditor_decision_requires_complete_clinical_basis(decision: str, missing: str) -> None:
    payload: dict[str, object] = {
        "kind": "reembolso_auditor",
        "decisao_reembolso": decision,
        "justificativa": "Fundamento",
        "fundamentacao_contratual": "Cláusula",
        "cid10_referencia": "Z00.0",
        "parecer_auditor": "Parecer",
    }
    if decision == "APROVAR_PARCIAL":
        payload["valor_reembolso_aprovado_cents"] = "1"
    del payload[missing]

    with pytest.raises(ValidationError, match=missing):
        ReembolsoAuditorInputs.model_validate(payload)


@pytest.mark.parametrize("outcome", ["SOLICITAR_INFO", "SOLICITAR_AUDITOR", "CANCELAR", ""])
def test_auditor_rejects_outcomes_outside_clinical_merit(outcome: str) -> None:
    with pytest.raises(ValidationError):
        ReembolsoAuditorInputs.model_validate({"kind": "reembolso_auditor", "decisao_reembolso": outcome})


@pytest.mark.parametrize("outcome", ["NEGAR", "APROVAR", "APROVAR_PARCIAL", "SOLICITAR_INFO", ""])
def test_expired_pendency_form_cannot_decide_reimbursement(outcome: str) -> None:
    with pytest.raises(ValidationError):
        ReembolsoPendingInputs.model_validate({"kind": "reembolso_pendencia", "decisao_pendencia": outcome})


@pytest.mark.parametrize(
    "forged",
    [
        "actor",
        "analista_id",
        "auditor_id",
        "tenant",
        "tier",
        "human_approved",
        "engine_variables",
        "valor_solicitado_cents",
        "valor_calculado_tabela_cents",
        "requer_avaliacao_clinica",
        "origem_pagamento",
        "valor_reembolso_aprovado_brl",
        "decisao_coordenacao",
    ],
)
def test_browser_cannot_supply_authority_or_trusted_financial_clinical_facts(forged: str) -> None:
    payload: dict[str, object] = {
        "kind": "reembolso_decisao",
        "decisao_reembolso": "APROVAR",
        "valor_reembolso_aprovado_cents": "1",
        forged: "forged",
    }

    with pytest.raises(ValidationError, match=forged):
        ReembolsoDecisionInputs.model_validate(payload)


@pytest.mark.parametrize("clinical_field", ["cid10_referencia", "parecer_auditor"])
def test_non_auditor_form_cannot_author_clinical_merit(clinical_field: str) -> None:
    with pytest.raises(ValidationError, match=clinical_field):
        ReembolsoDecisionInputs.model_validate(
            {
                "kind": "reembolso_decisao",
                "decisao_reembolso": "NEGAR",
                "justificativa": "Fundamento",
                "fundamentacao_contratual": "Cláusula",
                clinical_field: "forged",
            }
        )


@pytest.mark.parametrize(
    ("task_key", "form_key", "allowed_inputs"),
    [
        ("UT_DecidirPendenciaExpirada", "reembolso_pendencia", PENDING_INPUTS),
        ("UT_AnaliseReembolso", "reembolso_decisao", DECISION_INPUTS),
        ("UT_CoordenacaoReembolso", "reembolso_decisao", DECISION_INPUTS),
        ("UT_RevisaoAuditorMedico", "reembolso_auditor", AUDITOR_INPUTS),
    ],
)
def test_each_reembolso_task_has_exact_draft_binding(
    task_key: str, form_key: str, allowed_inputs: tuple[str, ...]
) -> None:
    snapshot = TaskSnapshot.model_validate(
        _snapshot(task_key=task_key, form_key=form_key, allowed_inputs=allowed_inputs)
    )

    assert snapshot.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    assert snapshot.read_only_evidence is None


def test_reembolso_binding_rejects_wrong_form_source_inputs_and_projection() -> None:
    payload = _snapshot(
        task_key="UT_AnaliseReembolso",
        form_key="reembolso_decisao",
        allowed_inputs=DECISION_INPUTS,
    )

    for field, value in (
        ("form_key", "reembolso_auditor"),
        ("form_source_status", "BPMN_FORMDATA"),
        ("allowed_inputs", (*DECISION_INPUTS, "parecer_auditor")),
        (
            "read_only_evidence",
            {
                "kind": "pagto_admissibilidade",
                "valor_pagamento_cents": "1",
                "dados_pagamento_validos": False,
                "lastro_confirmado": False,
                "duplicidade_suspeita": False,
            },
        ),
    ):
        malformed = dict(payload)
        malformed[field] = value
        with pytest.raises(ValidationError):
            TaskSnapshot.model_validate(malformed)


@pytest.mark.parametrize(
    ("task_key", "form_key", "inputs"),
    [
        (
            "UT_AnaliseReembolso",
            "reembolso_auditor",
            {
                "kind": "reembolso_auditor",
                "decisao_reembolso": "APROVAR",
                "valor_reembolso_aprovado_cents": "1",
            },
        ),
        (
            "UT_RevisaoAuditorMedico",
            "reembolso_decisao",
            {
                "kind": "reembolso_decisao",
                "decisao_reembolso": "APROVAR",
                "valor_reembolso_aprovado_cents": "1",
            },
        ),
        (
            "UT_DecidirPendenciaExpirada",
            "reembolso_decisao",
            {
                "kind": "reembolso_decisao",
                "decisao_reembolso": "SOLICITAR_INFO",
            },
        ),
    ],
)
def test_task_decision_rejects_cross_bound_forms(
    task_key: str, form_key: str, inputs: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(_decision(task_key=task_key, form_key=form_key, inputs=inputs))


def test_reembolso_dto_serializes_exact_large_centavos_and_is_immutable() -> None:
    exact = "9" * 100
    inputs = ReembolsoDecisionInputs(
        kind="reembolso_decisao",
        decisao_reembolso="APROVAR",
        valor_reembolso_aprovado_cents=exact,
    )

    assert inputs.valor_reembolso_aprovado_cents is not None
    assert inputs.valor_reembolso_aprovado_cents.as_int() == int(exact)
    assert inputs.model_dump(mode="json")["valor_reembolso_aprovado_cents"] == exact
    with pytest.raises(ValidationError):
        inputs.decisao_reembolso = "NEGAR"


@pytest.mark.parametrize(
    ("process_key", "task_key", "form_key", "allowed_inputs", "source_status", "evidence"),
    [
        (
            "SP-OP-AUTH-001",
            "UT_AnaliseMedicoAuditor",
            "auth_decisao",
            ("decisao_auditor", "justificativa_clinica", "cid10_referencia", "fundamentacao_dut"),
            "BPMN_FORMDATA",
            None,
        ),
        (
            "SP-OP-AUTH-001",
            "UT_CoordenacaoAssume",
            "auth_decisao",
            ("decisao_auditor", "justificativa_clinica", "cid10_referencia", "fundamentacao_dut"),
            "BPMN_FORMDATA",
            None,
        ),
        (
            "SP-OP-AUTH-001",
            "UT_RegistrarParecerJunta",
            "auth_junta",
            ("decisao_auditor", "justificativa_clinica", "cid10_referencia", "fundamentacao_dut"),
            "BPMN_FORMDATA",
            None,
        ),
        (
            "SP-OP-ESCALATION-001",
            "UT_TratarEscalonamento",
            "escalation",
            ("resultado", "notas_resolucao"),
            "BPMN_FORMDATA",
            None,
        ),
        (
            "SP-OP-ESCALATION-001",
            "UT_SupervisorAssume",
            "escalation",
            ("resultado", "notas_resolucao"),
            "BPMN_FORMDATA",
            None,
        ),
        (
            "SP-OP-PAGTO-001",
            "UT_AnaliseAdmissibilidade",
            "pagto_admissibilidade",
            ("decisao_admissibilidade", "justificativa_recusa"),
            "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            {
                "kind": "pagto_admissibilidade",
                "valor_pagamento_cents": "1",
                "dados_pagamento_validos": False,
                "lastro_confirmado": False,
                "duplicidade_suspeita": False,
            },
        ),
        (
            "SP-OP-CONTAS-001",
            "UT_AnalistaContas",
            "contas_decisao",
            (
                "decisao_contas",
                "justificativa_glosa",
                "codigo_glosa_tiss",
                "valor_glosado_centavos",
                "valor_liberado_centavos",
                "justificativa_devolucao",
            ),
            "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            None,
        ),
        (
            "SP-OP-CONTAS-001",
            "UT_CoordenacaoContasAssume",
            "contas_coordenacao",
            (
                "decisao_contas",
                "justificativa_glosa",
                "codigo_glosa_tiss",
                "valor_glosado_centavos",
                "valor_liberado_centavos",
                "justificativa_devolucao",
                "decisao_coordenacao",
            ),
            "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            None,
        ),
        (
            "SP-OP-RECURSO-001",
            "UT_AnaliseRecursoAnalista",
            "recurso_decisao",
            (
                "decisao_recurso",
                "fundamentacao_indeferimento",
                "valor_glosa_mantido_centavos",
                "valor_deferido_centavos",
                "referencia_contratual",
            ),
            "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            None,
        ),
        (
            "SP-OP-RECURSO-001",
            "UT_CoordenacaoRecursoAssume",
            "recurso_coordenacao",
            (
                "decisao_recurso",
                "fundamentacao_indeferimento",
                "valor_glosa_mantido_centavos",
                "valor_deferido_centavos",
                "referencia_contratual",
                "desfecho_humano",
            ),
            "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            None,
        ),
        (
            "SP-OP-RECURSO-001",
            "UT_EscalonamentoPrazo",
            "recurso_coordenacao",
            (
                "decisao_recurso",
                "fundamentacao_indeferimento",
                "valor_glosa_mantido_centavos",
                "valor_deferido_centavos",
                "referencia_contratual",
                "desfecho_humano",
            ),
            "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            None,
        ),
        (
            "SP-OP-RECURSO-001",
            "UT_RevisaoAuditorMedico",
            "recurso_auditor",
            (
                "decisao_auditor_recurso",
                "parecer_auditor",
                "fundamentacao_indeferimento",
                "valor_glosa_mantido_centavos",
                "valor_deferido_centavos",
                "referencia_contratual",
            ),
            "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            None,
        ),
    ],
)
def test_all_twelve_preexisting_task_bindings_remain_executable(
    process_key: str,
    task_key: str,
    form_key: str,
    allowed_inputs: tuple[str, ...],
    source_status: str,
    evidence: dict[str, object] | None,
) -> None:
    payload = _snapshot(
        task_key="UT_AnaliseReembolso",
        form_key="reembolso_decisao",
        allowed_inputs=DECISION_INPUTS,
    )
    payload.update(
        process_definition_key=process_key,
        process_definition_id=f"{process_key}:7:deployment",
        task_definition_key=task_key,
        form_key=form_key,
        form_source_status=source_status,
        allowed_inputs=allowed_inputs,
        read_only_evidence=evidence,
    )

    snapshot = TaskSnapshot.model_validate(payload)

    assert snapshot.task_definition_key == task_key
    assert snapshot.form_key == form_key
