"""Closed RECURSO portal forms from SP-OP-RECURSO-001 and its four User Tasks.

Contract anchors:

* SP-OP-RECURSO-001 ``Variaveis de saida`` defines the payer outcomes and conditional fields;
* BPMN ``UT_AnaliseRecursoAnalista`` and ``UT_RevisaoAuditorMedico`` document their distinct
  decision vocabularies and mandatory fields;
* BPMN ``UT_CoordenacaoRecursoAssume`` / ``UT_EscalonamentoPrazo`` permit the same payer decision
  plus human inadmissibility as ``INDEFERIR`` with ``desfecho_humano=inadmissivel``;
* ADR-0049 D2-D3 requires closed task bindings and canonical integer-centavo browser strings.

These tests prove source DTO parsing only. They do not authenticate a process version, inject an
actor, compare the partial sum with trusted evidence, convert centavos to engine BRL variables, or
authorize an adverse effect.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.portal.contracts import (
    RecursoAuditorInputs,
    RecursoCoordinationInputs,
    RecursoDecisionInputs,
    TaskDecision,
    TaskSnapshot,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
DIGEST = "d" * 64
DECISION_INPUTS = (
    "decisao_recurso",
    "fundamentacao_indeferimento",
    "valor_glosa_mantido_centavos",
    "valor_deferido_centavos",
    "referencia_contratual",
)
COORDINATION_INPUTS = (*DECISION_INPUTS, "desfecho_humano")
AUDITOR_INPUTS = (
    "decisao_auditor_recurso",
    "parecer_auditor",
    "fundamentacao_indeferimento",
    "valor_glosa_mantido_centavos",
    "valor_deferido_centavos",
    "referencia_contratual",
)


def _decision(*, task_key: str, form_key: str, inputs: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_id": "command-recurso-1",
        "task_id": "task-recurso-1",
        "process_definition_key": "SP-OP-RECURSO-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-RECURSO-001:7:deployment",
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
    if task_key == "UT_AnaliseRecursoAnalista":
        candidate_group = "analista-recurso-glosa"
    elif task_key == "UT_RevisaoAuditorMedico":
        candidate_group = "medico-auditor"
    else:
        candidate_group = "coordenacao-recurso"
    return {
        "schema_version": 1,
        "snapshot_at": NOW,
        "task_id": "task-recurso-1",
        "process_definition_key": "SP-OP-RECURSO-001",
        "process_definition_version": 7,
        "process_definition_id": "SP-OP-RECURSO-001:7:deployment",
        "process_definition_digest": DIGEST,
        "task_definition_key": task_key,
        "form_key": form_key,
        "form_version": 1,
        "form_digest": DIGEST,
        "form_source_status": "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        "task_revision": 11,
        "assignee_ref": None,
        "eligible_candidate_groups": (candidate_group,),
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
            "kind": "recurso_decisao",
            "decisao_recurso": "DEFERIR",
            "valor_deferido_centavos": "9007199254740993",
        },
        {
            "kind": "recurso_decisao",
            "decisao_recurso": "DEFERIR_PARCIAL",
            "fundamentacao_indeferimento": "Parte da glosa permanece procedente",
            "valor_glosa_mantido_centavos": "6000",
            "valor_deferido_centavos": "9000",
            "referencia_contratual": "Clausula 12.3",
        },
        {
            "kind": "recurso_decisao",
            "decisao_recurso": "INDEFERIR",
            "fundamentacao_indeferimento": "Elementos apresentados nao afastam a glosa",
            "valor_glosa_mantido_centavos": "15000",
            "referencia_contratual": "Clausula 12.3",
        },
        {"kind": "recurso_decisao", "decisao_recurso": "SOLICITAR_INFO"},
        {"kind": "recurso_decisao", "decisao_recurso": "ESCALAR_AUDITOR"},
    ],
)
def test_each_payer_analyst_outcome_parses_from_json(inputs: dict[str, object]) -> None:
    decision = TaskDecision.model_validate_json(
        json.dumps(
            _decision(
                task_key="UT_AnaliseRecursoAnalista",
                form_key="recurso_decisao",
                inputs=inputs,
            )
        )
    )

    assert isinstance(decision.inputs, RecursoDecisionInputs)
    assert decision.inputs.decisao_recurso == inputs["decisao_recurso"]


@pytest.mark.parametrize("task_key", ["UT_CoordenacaoRecursoAssume", "UT_EscalonamentoPrazo"])
def test_each_coordination_task_accepts_human_inadmissibility(task_key: str) -> None:
    inputs = {
        "kind": "recurso_coordenacao",
        "decisao_recurso": "INDEFERIR",
        "desfecho_humano": "inadmissivel",
        "fundamentacao_indeferimento": "Recurso inadmissivel por fundamento contratual revisado",
        "valor_glosa_mantido_centavos": "15000",
        "referencia_contratual": "Clausula de admissibilidade",
    }

    decision = TaskDecision.model_validate_json(
        json.dumps(_decision(task_key=task_key, form_key="recurso_coordenacao", inputs=inputs))
    )

    assert isinstance(decision.inputs, RecursoCoordinationInputs)
    assert decision.inputs.desfecho_humano == "inadmissivel"


@pytest.mark.parametrize(
    "inputs",
    [
        {
            "kind": "recurso_auditor",
            "decisao_auditor_recurso": "DEFERIR",
            "parecer_auditor": "A glosa tecnica nao procede",
            "valor_deferido_centavos": "15000",
        },
        {
            "kind": "recurso_auditor",
            "decisao_auditor_recurso": "DEFERIR_PARCIAL",
            "parecer_auditor": "Apenas parte da glosa tecnica procede",
            "fundamentacao_indeferimento": "Parte tecnicamente mantida",
            "valor_glosa_mantido_centavos": "5000",
            "valor_deferido_centavos": "10000",
            "referencia_contratual": "DUT aplicavel",
        },
        {
            "kind": "recurso_auditor",
            "decisao_auditor_recurso": "INDEFERIR",
            "parecer_auditor": "A glosa clinica procede",
            "fundamentacao_indeferimento": "Merito clinico confirma a glosa",
            "valor_glosa_mantido_centavos": "15000",
            "referencia_contratual": "DUT aplicavel",
        },
    ],
)
def test_each_auditor_outcome_parses_with_mandatory_opinion(inputs: dict[str, object]) -> None:
    decision = TaskDecision.model_validate_json(
        json.dumps(
            _decision(
                task_key="UT_RevisaoAuditorMedico",
                form_key="recurso_auditor",
                inputs=inputs,
            )
        )
    )

    assert isinstance(decision.inputs, RecursoAuditorInputs)
    assert decision.inputs.decisao_auditor_recurso == inputs["decisao_auditor_recurso"]


@pytest.mark.parametrize(
    ("decision", "present", "missing"),
    [
        ("DEFERIR", {"valor_deferido_centavos": "1"}, "valor_deferido_centavos"),
        (
            "INDEFERIR",
            {
                "fundamentacao_indeferimento": "Fundamento",
                "valor_glosa_mantido_centavos": "1",
                "referencia_contratual": "Contrato",
            },
            "fundamentacao_indeferimento",
        ),
        (
            "INDEFERIR",
            {
                "fundamentacao_indeferimento": "Fundamento",
                "valor_glosa_mantido_centavos": "1",
                "referencia_contratual": "Contrato",
            },
            "valor_glosa_mantido_centavos",
        ),
        (
            "INDEFERIR",
            {
                "fundamentacao_indeferimento": "Fundamento",
                "valor_glosa_mantido_centavos": "1",
                "referencia_contratual": "Contrato",
            },
            "referencia_contratual",
        ),
        (
            "DEFERIR_PARCIAL",
            {
                "fundamentacao_indeferimento": "Fundamento",
                "valor_glosa_mantido_centavos": "1",
                "valor_deferido_centavos": "1",
                "referencia_contratual": "Contrato",
            },
            "valor_deferido_centavos",
        ),
    ],
)
def test_analyst_conditional_fields_are_required(
    decision: str, present: dict[str, object], missing: str
) -> None:
    inputs = {"kind": "recurso_decisao", "decisao_recurso": decision, **present}
    del inputs[missing]

    with pytest.raises(ValidationError, match=missing):
        RecursoDecisionInputs.model_validate(inputs)


@pytest.mark.parametrize("field", ["valor_glosa_mantido_centavos", "valor_deferido_centavos"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_required_recurso_amounts_must_be_positive(field: str, value: str) -> None:
    inputs: dict[str, object] = {
        "kind": "recurso_decisao",
        "decisao_recurso": "DEFERIR_PARCIAL",
        "fundamentacao_indeferimento": "Fundamento",
        "valor_glosa_mantido_centavos": "1",
        "valor_deferido_centavos": "1",
        "referencia_contratual": "Contrato",
    }
    inputs[field] = value

    with pytest.raises(ValidationError, match=field):
        RecursoDecisionInputs.model_validate(inputs)


@pytest.mark.parametrize("value", [1, True, 1.0, "01", "+1", "1.0", "1e2", " 1", "NaN"])
def test_recurso_money_reuses_canonical_centavos_validation(value: object) -> None:
    with pytest.raises(ValidationError):
        RecursoDecisionInputs.model_validate(
            {
                "kind": "recurso_decisao",
                "decisao_recurso": "DEFERIR",
                "valor_deferido_centavos": value,
            }
        )


@pytest.mark.parametrize("legacy_outcome", ["RECORRER", "NAO_RECORRER", "ACEITAR_GLOSA", "MANTER_RECURSO"])
def test_provider_side_legacy_outcomes_are_rejected(legacy_outcome: str) -> None:
    with pytest.raises(ValidationError):
        RecursoDecisionInputs.model_validate({"kind": "recurso_decisao", "decisao_recurso": legacy_outcome})


def test_inadmissibility_is_only_coordination_indeferimento() -> None:
    with pytest.raises(ValidationError, match="requires decisao_recurso=INDEFERIR"):
        RecursoCoordinationInputs.model_validate(
            {
                "kind": "recurso_coordenacao",
                "decisao_recurso": "DEFERIR",
                "desfecho_humano": "inadmissivel",
                "valor_deferido_centavos": "1",
            }
        )

    with pytest.raises(ValidationError, match="desfecho_humano"):
        RecursoDecisionInputs.model_validate(
            {
                "kind": "recurso_decisao",
                "decisao_recurso": "INDEFERIR",
                "desfecho_humano": "inadmissivel",
                "fundamentacao_indeferimento": "Fundamento",
                "valor_glosa_mantido_centavos": "1",
                "referencia_contratual": "Contrato",
            }
        )


def test_auditor_requires_non_blank_opinion() -> None:
    for opinion in (None, "", "   "):
        payload: dict[str, object] = {
            "kind": "recurso_auditor",
            "decisao_auditor_recurso": "DEFERIR",
            "valor_deferido_centavos": "1",
        }
        if opinion is not None:
            payload["parecer_auditor"] = opinion
        with pytest.raises(ValidationError, match="parecer_auditor"):
            RecursoAuditorInputs.model_validate(payload)


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
        "valor_deferido_brl",
        "valor_glosa_mantido_brl",
        "valor_glosado_centavos",
    ],
)
def test_browser_cannot_supply_authority_engine_money_or_original_glosa(forged: str) -> None:
    inputs: dict[str, object] = {
        "kind": "recurso_decisao",
        "decisao_recurso": "DEFERIR",
        "valor_deferido_centavos": "1",
        forged: "forged",
    }

    with pytest.raises(ValidationError, match=forged):
        RecursoDecisionInputs.model_validate(inputs)


@pytest.mark.parametrize(
    ("task_key", "form_key", "allowed_inputs"),
    [
        ("UT_AnaliseRecursoAnalista", "recurso_decisao", DECISION_INPUTS),
        ("UT_CoordenacaoRecursoAssume", "recurso_coordenacao", COORDINATION_INPUTS),
        ("UT_EscalonamentoPrazo", "recurso_coordenacao", COORDINATION_INPUTS),
        ("UT_RevisaoAuditorMedico", "recurso_auditor", AUDITOR_INPUTS),
    ],
)
def test_each_recurso_task_has_an_exact_draft_binding(
    task_key: str, form_key: str, allowed_inputs: tuple[str, ...]
) -> None:
    snapshot = TaskSnapshot.model_validate(
        _snapshot(task_key=task_key, form_key=form_key, allowed_inputs=allowed_inputs)
    )

    assert snapshot.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    assert snapshot.read_only_evidence is None


def test_recurso_binding_rejects_wrong_form_source_inputs_and_projection() -> None:
    payload = _snapshot(
        task_key="UT_AnaliseRecursoAnalista",
        form_key="recurso_decisao",
        allowed_inputs=DECISION_INPUTS,
    )

    for field, value in (
        ("form_key", "recurso_auditor"),
        ("form_source_status", "BPMN_FORMDATA"),
        ("allowed_inputs", (*DECISION_INPUTS, "desfecho_humano")),
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
            "UT_AnaliseRecursoAnalista",
            "recurso_auditor",
            {
                "kind": "recurso_auditor",
                "decisao_auditor_recurso": "DEFERIR",
                "parecer_auditor": "Parecer",
                "valor_deferido_centavos": "1",
            },
        ),
        (
            "UT_RevisaoAuditorMedico",
            "recurso_decisao",
            {
                "kind": "recurso_decisao",
                "decisao_recurso": "DEFERIR",
                "valor_deferido_centavos": "1",
            },
        ),
    ],
)
def test_task_decision_rejects_cross_bound_recurso_forms(
    task_key: str, form_key: str, inputs: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="binding"):
        TaskDecision.model_validate(_decision(task_key=task_key, form_key=form_key, inputs=inputs))


def test_recurso_dtos_are_immutable_and_preserve_large_exact_centavos() -> None:
    exact = "9" * 100
    inputs = RecursoDecisionInputs(
        kind="recurso_decisao",
        decisao_recurso="DEFERIR",
        valor_deferido_centavos=exact,
    )

    assert inputs.valor_deferido_centavos is not None
    assert inputs.valor_deferido_centavos.as_int() == int(exact)
    assert inputs.model_dump(mode="json")["valor_deferido_centavos"] == exact
    with pytest.raises(ValidationError):
        inputs.decisao_recurso = "INDEFERIR"


@pytest.mark.parametrize(
    ("task_key", "form_key", "allowed_inputs", "read_only_evidence"),
    [
        (
            "UT_AnaliseMedicoAuditor",
            "auth_decisao",
            (
                "decisao_auditor",
                "justificativa_clinica",
                "cid10_referencia",
                "fundamentacao_dut",
            ),
            None,
        ),
        (
            "UT_CoordenacaoAssume",
            "auth_decisao",
            (
                "decisao_auditor",
                "justificativa_clinica",
                "cid10_referencia",
                "fundamentacao_dut",
            ),
            None,
        ),
        (
            "UT_RegistrarParecerJunta",
            "auth_junta",
            (
                "decisao_auditor",
                "justificativa_clinica",
                "cid10_referencia",
                "fundamentacao_dut",
            ),
            None,
        ),
        (
            "UT_TratarEscalonamento",
            "escalation",
            ("resultado", "notas_resolucao"),
            None,
        ),
        (
            "UT_SupervisorAssume",
            "escalation",
            ("resultado", "notas_resolucao"),
            None,
        ),
        (
            "UT_AnaliseAdmissibilidade",
            "pagto_admissibilidade",
            ("decisao_admissibilidade", "justificativa_recusa"),
            {
                "kind": "pagto_admissibilidade",
                "valor_pagamento_cents": "1",
                "dados_pagamento_validos": False,
                "lastro_confirmado": False,
                "duplicidade_suspeita": False,
            },
        ),
        (
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
            None,
        ),
        (
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
            None,
        ),
    ],
)
def test_all_eight_preexisting_task_bindings_remain_executable(
    task_key: str,
    form_key: str,
    allowed_inputs: tuple[str, ...],
    read_only_evidence: dict[str, object] | None,
) -> None:
    process_key = {
        "auth_decisao": "SP-OP-AUTH-001",
        "auth_junta": "SP-OP-AUTH-001",
        "escalation": "SP-OP-ESCALATION-001",
        "pagto_admissibilidade": "SP-OP-PAGTO-001",
        "contas_decisao": "SP-OP-CONTAS-001",
        "contas_coordenacao": "SP-OP-CONTAS-001",
    }[form_key]
    payload = _snapshot(task_key=task_key, form_key=form_key, allowed_inputs=allowed_inputs)
    payload["process_definition_key"] = process_key
    payload["process_definition_id"] = f"{process_key}:7:deployment"
    payload["form_source_status"] = (
        "BPMN_FORMDATA"
        if form_key in {"auth_decisao", "auth_junta", "escalation"}
        else "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
    )
    payload["read_only_evidence"] = read_only_evidence

    snapshot = TaskSnapshot.model_validate(payload)

    assert snapshot.task_definition_key == task_key
