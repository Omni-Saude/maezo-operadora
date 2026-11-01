"""Catalogo do portal para o SP-OP-REEMBOLSO-001 v0.2.0 (DL-0161): o script nao pode divergir do codigo."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from scripts.ops.staff_material_reembolso_catalogo import PROCESSO, TAREFAS, montar

from maezo.portal.contracts.models import _BINDINGS, _INPUTS_BY_FORM

BPMN = (
    Path(__file__).resolve().parents[3]
    / "spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
)
PROCESS_ID = "SP-OP-REEMBOLSO-001:2:" + "-".join(("1" * 8, "2" * 4, "3" * 4, "4" * 4, "5" * 12))


def _vivo() -> tuple[dict, dict]:  # type: ignore[type-arg]
    politica = {"artifact_ref": "SYN-x", "digest": "a" * 64}
    entrada = {
        "process_definition_id": "SP-OP-ESCALATION-001:2:x",
        "allowed_inputs": ["resultado"],
        "task_definition_key": "UT_TratarEscalonamento",
        "required_roles": ["r"],
        "disclosure_policy": politica,
    }
    humano = {
        "candidate_groups": ["g"],
        "classification": {"classification_ref": "SYN-c", "policy_ref": "SYN-x", "policy_digest": "a" * 64},
        "identity_policy": politica,
        "process_definition_id": "SP-OP-ESCALATION-001:2:x",
        "task_definition_key": "UT_TratarEscalonamento",
        "task_id_format": "uuid",
        "user_candidates": "refused",
    }
    vivo = {
        "entries": [entrada],
        "forms": [{"artifact_ref": "escalation", "bytes_base64": "eA==", "digest": "b" * 64}],
    }
    return vivo, {"human": {"entries": [humano]}}


def test_acrescenta_5_tarefas_e_4_formularios() -> None:
    vivo, admissao = _vivo()
    catalogo, entradas = montar(vivo, admissao, PROCESS_ID, "c" * 64)
    assert len(catalogo["entries"]) == 1 + len(TAREFAS) == 6 and len(entradas) == 5
    assert {f["artifact_ref"] for f in catalogo["forms"]} == {
        "escalation",
        "reembolso_decisao",
        "reembolso_auditor",
        "reembolso_pendencia",
        "reembolso_pagamento",
    }
    assert {e["task_definition_key"] for e in catalogo["entries"][1:]} == {t[0] for t in TAREFAS}


def test_cada_tarefa_bate_com_o_modelo_do_portal_e_com_o_bpmn() -> None:
    """Se o formulario, as entradas ou o grupo mudarem em um lugar so', este teste cai."""
    bpmn = BPMN.read_text(encoding="utf-8")
    for tarefa, form_key, status, entradas, grupos, _papel in TAREFAS:
        assert _BINDINGS[(PROCESSO, tarefa)] == (form_key, status)
        assert tuple(entradas) == _INPUTS_BY_FORM[form_key]
        achado = re.search(rf'<bpmn:userTask id="{tarefa}"[^>]*camunda:candidateGroups="([^"]+)"', bpmn)
        assert achado is not None, tarefa
        assert sorted(achado.group(1).split(",")) == sorted(grupos), tarefa


def test_o_catalogo_cobre_todas_as_user_tasks_do_bpmn() -> None:
    bpmn = BPMN.read_text(encoding="utf-8")
    tasks = set(re.findall(r'<bpmn:userTask id="([^"]+)"', bpmn))
    assert tasks == {t[0] for t in TAREFAS}


def test_recusa_id_ruim_e_catalogo_que_ja_tem_a_definicao() -> None:
    with pytest.raises(SystemExit):
        montar({"entries": [{}], "forms": []}, {"human": {"entries": [{}]}}, "SP-OP-CANCEL-001:1:x", "c" * 64)
    vivo, admissao = _vivo()
    vivo["entries"][0]["process_definition_id"] = PROCESS_ID
    with pytest.raises(SystemExit):
        montar(vivo, admissao, PROCESS_ID, "c" * 64)
