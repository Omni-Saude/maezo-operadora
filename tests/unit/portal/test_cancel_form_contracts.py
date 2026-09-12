"""Closed CANCEL portal form for both contract-termination User Tasks.

Contract anchors:

* SP-OP-CANCEL-001 lines 7-23 keep contract termination and member-request denial L0-hard;
* its output table at lines 75-84 defines the five human decisions and conditional basis fields;
* BPMN lines 260-282 define ``UT_AnaliseRescisao`` and its form conditions;
* BPMN lines 318-323 make ``UT_CoordenacaoCancelamento`` inherit the same outputs/conditions;
* BPMN lines 511-529 keep ``tipo_solicitacao`` as trusted runtime routing context;
* ADR-0049 D2-D3 requires explicit task bindings and frozen, closed browser payloads.

These tests prove source DTO parsing only. They do not authenticate a human or deployment version,
make ``tipo_solicitacao`` browser-writable, inject ``responsavel_id``, validate evidence against an
authoritative store, execute a task, or authorize a cancellation, suspension, denial, or effect.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import CancelDecisionInputs, TaskDecision, TaskSnapshot

NOW = datetime(2026, 9, 9, 18, 0, tzinfo=UTC)
DIGEST = "c" * 64
CANCEL_INPUTS = (
    "decisao_cancelamento",
    "fundamentacao_contratual",
    "referencia_regulatoria",
    "comprovacao_notificacao_previa",
)
CANCEL_TASKS = ("UT_AnaliseRescisao", "UT_CoordenacaoCancelamento")


def _decision(*, task_key: str, form_key: str, inputs: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": "command-cancel-1",
        "task_id": "task-cancel-1",
        "process_definition_key": "SP-OP-CANCEL-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-CANCEL-001:7:deployment",
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


def _snapshot(
    *,
    process_key: str = "SP-OP-CANCEL-001",
    task_key: str,
    form_key: str,
    allowed_inputs: tuple[str, ...],
    source_status: str = "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    group = {
        "UT_AnaliseRescisao": "juridico-contratos",
        "UT_CoordenacaoCancelamento": "coordenacao-contratos",
    }.get(task_key, "existing-group")
    return {
        "schema_version": 1,
        "snapshot_at": NOW,
        "task_id": "task-contract-1",
        "process_definition_key": process_key,
        "process_definition_version": 7,
        "process_definition_id": f"{process_key}:7:deployment",
        "process_definition_digest": DIGEST,
        "task_definition_key": task_key,
        "form_key": form_key,
        "form_version": 1,
        "form_digest": DIGEST,
        "form_source_status": source_status,
        "task_revision": 11,
        "assignee_ref": None,
        "eligible_candidate_groups": (group,),
        "evidence_revision": 4,
        "evidence_digest": DIGEST,
        "engine_due_at": None,
        "allowed_actions": ("claim", "decision"),
        "allowed_inputs": allowed_inputs,
        "read_only_evidence": evidence,
    }


OUTCOME_INPUTS = {
    "RESCINDIR": {
        "fundamentacao_contratual": "Cláusula contratual aplicável",
        "referencia_regulatoria": "Referência regulatória revisada pelo humano",
        "comprovacao_notificacao_previa": "evidence-ref-notificacao-1",
    },
    "MANTER": {"fundamentacao_contratual": "Cláusula para manutenção do vínculo"},
    "SUSPENDER": {
        "fundamentacao_contratual": "Cláusula contratual aplicável",
        "referencia_regulatoria": "Referência regulatória revisada pelo humano",
        "comprovacao_notificacao_previa": "evidence-ref-notificacao-2",
    },
    "EFETIVAR_PEDIDO": {},
    "SOLICITAR_INFO": {},
}


@pytest.mark.parametrize("task_key", CANCEL_TASKS)
@pytest.mark.parametrize("outcome", tuple(OUTCOME_INPUTS))
def test_each_cancel_task_accepts_each_exact_human_outcome(task_key: str, outcome: str) -> None:
    inputs: dict[str, object] = {
        "kind": "cancel_decisao",
        "decisao_cancelamento": outcome,
        **OUTCOME_INPUTS[outcome],
    }

    decision = TaskDecision.model_validate_json(
        json.dumps(_decision(task_key=task_key, form_key="cancel_decisao", inputs=inputs))
    )

    assert isinstance(decision.inputs, CancelDecisionInputs)
    assert decision.inputs.decisao_cancelamento == outcome


@pytest.mark.parametrize("task_key", CANCEL_TASKS)
def test_each_cancel_task_has_exact_draft_binding(task_key: str) -> None:
    snapshot = TaskSnapshot.model_validate(
        _snapshot(task_key=task_key, form_key="cancel_decisao", allowed_inputs=CANCEL_INPUTS)
    )

    assert snapshot.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    assert snapshot.allowed_inputs == CANCEL_INPUTS
    assert snapshot.read_only_evidence is None


@pytest.mark.parametrize("outcome", ["ANALISE_HUMANA", "SEGUE_ANALISE", "CANCELAR", "NEGAR", ""])
def test_cancel_form_rejects_dmn_and_invented_outcomes(outcome: str) -> None:
    with pytest.raises(ValidationError):
        CancelDecisionInputs.model_validate({"kind": "cancel_decisao", "decisao_cancelamento": outcome})


@pytest.mark.parametrize(
    ("outcome", "required_field"),
    [
        ("RESCINDIR", "fundamentacao_contratual"),
        ("RESCINDIR", "referencia_regulatoria"),
        ("RESCINDIR", "comprovacao_notificacao_previa"),
        ("SUSPENDER", "fundamentacao_contratual"),
        ("SUSPENDER", "referencia_regulatoria"),
        ("SUSPENDER", "comprovacao_notificacao_previa"),
        ("MANTER", "fundamentacao_contratual"),
    ],
)
@pytest.mark.parametrize("invalid", [None, "", "   "])
def test_conditional_basis_rejects_null_empty_and_blank(
    outcome: str, required_field: str, invalid: object
) -> None:
    payload: dict[str, object] = {
        "kind": "cancel_decisao",
        "decisao_cancelamento": outcome,
        **OUTCOME_INPUTS[outcome],
        required_field: invalid,
    }

    with pytest.raises(ValidationError, match=required_field):
        CancelDecisionInputs.model_validate(payload)


@pytest.mark.parametrize(
    ("outcome", "required_field"),
    [
        ("RESCINDIR", "fundamentacao_contratual"),
        ("RESCINDIR", "referencia_regulatoria"),
        ("RESCINDIR", "comprovacao_notificacao_previa"),
        ("SUSPENDER", "fundamentacao_contratual"),
        ("SUSPENDER", "referencia_regulatoria"),
        ("SUSPENDER", "comprovacao_notificacao_previa"),
        ("MANTER", "fundamentacao_contratual"),
    ],
)
def test_conditional_basis_rejects_missing_field(outcome: str, required_field: str) -> None:
    payload: dict[str, object] = {
        "kind": "cancel_decisao",
        "decisao_cancelamento": outcome,
        **OUTCOME_INPUTS[outcome],
    }
    del payload[required_field]

    with pytest.raises(ValidationError, match=required_field):
        CancelDecisionInputs.model_validate(payload)


@pytest.mark.parametrize("value", [1, True, 1.0, [], {}])
def test_cancel_decision_rejects_non_string_outcome_coercion(value: object) -> None:
    with pytest.raises(ValidationError):
        CancelDecisionInputs.model_validate({"kind": "cancel_decisao", "decisao_cancelamento": value})


@pytest.mark.parametrize(
    "forged",
    [
        "actor",
        "responsavel_id",
        "assignee",
        "tenant",
        "tier",
        "human_approved",
        "engine_variables",
        "tipo_solicitacao",
        "origem_solicitacao",
        "notificacao_previa_feita",
        "titularidade_confirmada",
        "vinculo_ativo",
        "data_efeito_iso",
        "efeito_confirmado",
        "autorizado",
    ],
)
def test_browser_cannot_supply_identity_trusted_routing_or_effect_facts(forged: str) -> None:
    payload: dict[str, object] = {
        "kind": "cancel_decisao",
        "decisao_cancelamento": "SOLICITAR_INFO",
        forged: "forged",
    }

    with pytest.raises(ValidationError, match=forged):
        CancelDecisionInputs.model_validate(payload)


def test_cancel_binding_rejects_wrong_form_source_inputs_and_projection() -> None:
    payload = _snapshot(
        task_key="UT_AnaliseRescisao",
        form_key="cancel_decisao",
        allowed_inputs=CANCEL_INPUTS,
    )

    for field, value in (
        ("form_key", "reembolso_decisao"),
        ("form_source_status", "BPMN_FORMDATA"),
        ("allowed_inputs", (*CANCEL_INPUTS, "responsavel_id")),
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


@pytest.mark.parametrize("task_key", CANCEL_TASKS)
def test_cancel_task_rejects_cross_bound_existing_form(task_key: str) -> None:
    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(
            _decision(
                task_key=task_key,
                form_key="reembolso_decisao",
                inputs={"kind": "reembolso_decisao", "decisao_reembolso": "SOLICITAR_INFO"},
            )
        )


def test_existing_task_rejects_cancel_form() -> None:
    payload = _decision(
        task_key="UT_AnaliseRescisao",
        form_key="cancel_decisao",
        inputs={"kind": "cancel_decisao", "decisao_cancelamento": "SOLICITAR_INFO"},
    )
    payload.update(
        process_definition_key="SP-OP-REEMBOLSO-001",
        process_definition_id="SP-OP-REEMBOLSO-001:7:deployment",
        task_definition_key="UT_AnaliseReembolso",
    )

    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(payload)


def test_cancel_dto_json_roundtrip_is_exact_and_immutable() -> None:
    inputs = CancelDecisionInputs(
        kind="cancel_decisao",
        decisao_cancelamento="RESCINDIR",
        fundamentacao_contratual="Cláusula 13",
        referencia_regulatoria="Referência revisada",
        comprovacao_notificacao_previa="evidence-ref-notificacao-3",
    )

    assert CancelDecisionInputs.model_validate_json(inputs.model_dump_json()) == inputs
    assert inputs.model_dump(mode="json") == {
        "kind": "cancel_decisao",
        "decisao_cancelamento": "RESCINDIR",
        "fundamentacao_contratual": "Cláusula 13",
        "referencia_regulatoria": "Referência revisada",
        "comprovacao_notificacao_previa": "evidence-ref-notificacao-3",
    }
    with pytest.raises(ValidationError):
        inputs.decisao_cancelamento = "MANTER"


PREEXISTING_BINDINGS = [
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
    (
        "SP-OP-REEMBOLSO-001",
        "UT_DecidirPendenciaExpirada",
        "reembolso_pendencia",
        ("decisao_pendencia",),
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        None,
    ),
    (
        "SP-OP-REEMBOLSO-001",
        "UT_AnaliseReembolso",
        "reembolso_decisao",
        ("decisao_reembolso", "valor_reembolso_aprovado_cents", "justificativa", "fundamentacao_contratual"),
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        None,
    ),
    (
        "SP-OP-REEMBOLSO-001",
        "UT_CoordenacaoReembolso",
        "reembolso_decisao",
        ("decisao_reembolso", "valor_reembolso_aprovado_cents", "justificativa", "fundamentacao_contratual"),
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        None,
    ),
    (
        "SP-OP-REEMBOLSO-001",
        "UT_RevisaoAuditorMedico",
        "reembolso_auditor",
        (
            "decisao_reembolso",
            "valor_reembolso_aprovado_cents",
            "justificativa",
            "fundamentacao_contratual",
            "cid10_referencia",
            "parecer_auditor",
        ),
        "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        None,
    ),
]


@pytest.mark.parametrize(
    ("process_key", "task_key", "form_key", "allowed_inputs", "source_status", "evidence"),
    PREEXISTING_BINDINGS,
)
def test_all_sixteen_preexisting_task_bindings_remain_executable(
    process_key: str,
    task_key: str,
    form_key: str,
    allowed_inputs: tuple[str, ...],
    source_status: str,
    evidence: dict[str, object] | None,
) -> None:
    snapshot = TaskSnapshot.model_validate(
        _snapshot(
            process_key=process_key,
            task_key=task_key,
            form_key=form_key,
            allowed_inputs=allowed_inputs,
            source_status=source_status,
            evidence=evidence,
        )
    )

    assert snapshot.task_definition_key == task_key
    assert snapshot.form_key == form_key
