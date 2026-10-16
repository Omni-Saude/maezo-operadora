"""Formulario `cancel_execucao` (DL-0111, SP-OP-CANCEL-001 v0.2.0): a execucao do cancelamento no cadastro.

Uma tarefa de EXECUCAO (nao de decisao): um campo fechado (`execucao_cadastro` in EXECUTADO/IMPEDIDO),
declarado como `camunda:formField` no BPMN e espelhado no binding Python e no `COMPILED` do plugin Java.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

import pytest
from pydantic import TypeAdapter, ValidationError

from maezo.portal.contracts.models import _BINDINGS, _INPUTS_BY_FORM, CancelExecucaoInputs, DecisionInputs

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn"
_JAVA = _REPO / "src/maezo/portal/engine/java/src/main/java/br/com/maezo/human/PortalReadCommand.java"
_TAREFAS = ("UT_ExecutarCancelamentoCadastro", "UT_CoordenacaoExecucaoCadastro")
_NS = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL", "c": "http://camunda.org/schema/1.0/bpmn"}


@pytest.mark.parametrize("tarefa", _TAREFAS)
def test_binding_e_formdata_fechado(tarefa: str) -> None:
    assert _BINDINGS[("SP-OP-CANCEL-001", tarefa)] == ("cancel_execucao", "BPMN_FORMDATA")
    assert _INPUTS_BY_FORM["cancel_execucao"] == ("execucao_cadastro",)
    raiz = ElementTree.parse(_BPMN).getroot()
    ut = next(t for t in raiz.iter(f"{{{_NS['b']}}}userTask") if t.get("id") == tarefa)
    campos = ut.findall(".//c:formField", _NS)
    assert [c.get("id") for c in campos] == ["execucao_cadastro"]
    assert campos[0].get("type") == "enum"
    assert [v.get("id") for v in campos[0].findall("c:value", _NS)] == ["EXECUTADO", "IMPEDIDO"]


@pytest.mark.parametrize("tarefa", _TAREFAS)
def test_o_plugin_java_compila_a_mesma_forma(tarefa: str) -> None:
    java = _JAVA.read_text(encoding="utf-8")
    padrao = (
        rf'"SP-OP-CANCEL-001/{tarefa}",\s*List\.of\("cancel_execucao", "BPMN_FORMDATA", "execucao_cadastro"\)'
    )
    assert re.search(padrao, java)


@pytest.mark.parametrize("valor", ["EXECUTADO", "IMPEDIDO"])
def test_os_dois_valores_fechados(valor: str) -> None:
    entrada = TypeAdapter(DecisionInputs).validate_python(
        {"kind": "cancel_execucao", "execucao_cadastro": valor}
    )
    assert isinstance(entrada, CancelExecucaoInputs)


@pytest.mark.parametrize(
    "corpo",
    [
        {"kind": "cancel_execucao", "execucao_cadastro": "RESCINDIR"},
        {"kind": "cancel_execucao", "execucao_cadastro": "executado"},
        {"kind": "cancel_execucao"},
        {"kind": "cancel_execucao", "execucao_cadastro": "EXECUTADO", "decisao_cancelamento": "MANTER"},
        {"kind": "cancel_execucao", "execucao_cadastro": "EXECUTADO", "data_efeito_iso": "2026-10-15"},
    ],
)
def test_nada_fora_do_formulario(corpo: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DecisionInputs).validate_python(corpo)
