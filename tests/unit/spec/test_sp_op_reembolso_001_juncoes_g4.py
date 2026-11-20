"""E-SF-29 (parte 2) — junções explícitas nos quatro gateways G4 de SP-OP-REEMBOLSO-001.

Os quatro gateways `GW_DecisaoReembolso`, `GW_DesfechoPagamento`, `GW_AvisoAprovado` e
`GW_AguardarDocs` juntavam e bifurcavam no mesmo nó (bpmnlint `no-gateway-join-fork`). A
correção põe uma junção EXCLUSIVA antes de cada um. Uma junção exclusiva só repassa o token que
chega; uma junção paralela ou inclusiva esperaria por tokens que nunca chegam e travaria a
instância. Estas cercas fixam a forma: junção exclusiva, uma entrada no gateway original, todas
as entradas antigas na junção, saídas, condições e default intocados.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_BPMN = (
    Path(__file__).resolve().parents[3]
    / "spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
)
_M = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"

#: gateway original -> (junção, entradas que passam pela junção, saídas do gateway, default)
_JUNCOES = {
    "GW_DecisaoReembolso": (
        "GW_JuncaoDecisaoReembolso",
        {"Flow_UTAnalista_GW", "Flow_UTCoord_GW", "Flow_UTAuditor_GW"},
        {
            "Flow_GWDec_Aprovar",
            "Flow_GWDec_Parcial",
            "Flow_GWDec_Auditor",
            "Flow_GWDec_Negar",
            "Flow_GWDec_SolicitarInfo",
        },
        None,
    ),
    "GW_DesfechoPagamento": (
        "GW_JuncaoDesfechoPagamento",
        {"Flow_AvisoPago_Nao", "Flow_AvisarPago_Next", "Flow_BEAvisoPago_Next"},
        {"Flow_Desfecho_Automatico", "Flow_Desfecho_Parcial", "Flow_Desfecho_Integral"},
        "Flow_Desfecho_Integral",
    ),
    "GW_AvisoAprovado": (
        "GW_JuncaoAvisoAprovado",
        {"Flow_GWDec_Aprovar", "Flow_RegistrarAuto_AvisoAprovado"},
        {"Flow_AvisoAprovado_Sim", "Flow_AvisoAprovado_Nao"},
        "Flow_AvisoAprovado_Nao",
    ),
    "GW_AguardarDocs": (
        "GW_JuncaoAguardarDocs",
        {"Flow_PubPended_WaitDocs", "Flow_GWPend_PrazoExtra"},
        {"Flow_Wait_DocsMsg", "Flow_Wait_Timer"},
        None,
    ),
}


#: Texto exato das condições das saídas dos quatro gateways (o refator não toca condição nenhuma).
_CONDICOES = {
    "Flow_GWDec_Aprovar": "${decisao_reembolso == 'APROVAR'}",
    "Flow_GWDec_Parcial": "${decisao_reembolso == 'APROVAR_PARCIAL'}",
    "Flow_GWDec_Auditor": "${decisao_reembolso == 'SOLICITAR_AUDITOR'}",
    "Flow_GWDec_Negar": "${decisao_reembolso == 'NEGAR'}",
    "Flow_GWDec_SolicitarInfo": "${decisao_reembolso == 'SOLICITAR_INFO'}",
    "Flow_AvisoAprovado_Sim": "${execution.getVariable('aviso_beneficiario_whatsapp') == true}",
    "Flow_AvisoAprovado_Nao": None,
    "Flow_Desfecho_Parcial": "${origem_aprovacao == 'HUMANA' && decisao_reembolso == 'APROVAR_PARCIAL'}",
    "Flow_Desfecho_Integral": None,
    "Flow_Desfecho_Automatico": "${origem_aprovacao == 'AUTO_L2'}",
    "Flow_Wait_DocsMsg": None,
    "Flow_Wait_Timer": None,
}


def _process() -> ET.Element:
    process = ET.parse(_BPMN).getroot().find(f"{_M}process")
    if process is None:
        pytest.fail("SP-OP-REEMBOLSO-001 sem bpmn:process")
    return process


def _flows(process: ET.Element) -> dict[str, tuple[str, str]]:
    return {
        f.get("id", ""): (f.get("sourceRef", ""), f.get("targetRef", ""))
        for f in process.iter(f"{_M}sequenceFlow")
    }


def _by_id(process: ET.Element) -> dict[str, ET.Element]:
    return {el.get("id", ""): el for el in process.iter() if el.get("id")}


@pytest.mark.parametrize("gateway", sorted(_JUNCOES))
def test_a_juncao_antes_do_gateway_e_exclusiva(gateway: str) -> None:
    process = _process()
    nodes, flows = _by_id(process), _flows(process)
    juncao, entradas, _saidas, _default = _JUNCOES[gateway]
    assert nodes[juncao].tag == f"{_M}exclusiveGateway", f"{juncao} tem de ser junção EXCLUSIVA"
    assert {f for f, (_, tgt) in flows.items() if tgt == juncao} == entradas
    saidas_juncao = [f for f, (src, _) in flows.items() if src == juncao]
    assert len(saidas_juncao) == 1
    assert flows[saidas_juncao[0]][1] == gateway
    assert nodes[saidas_juncao[0]].find(f"{_M}conditionExpression") is None


@pytest.mark.parametrize("gateway", sorted(_JUNCOES))
def test_o_gateway_original_nao_junta_mais_e_mantem_saidas_e_default(gateway: str) -> None:
    process = _process()
    nodes, flows = _by_id(process), _flows(process)
    juncao, _entradas, saidas, default = _JUNCOES[gateway]
    assert {f for f, (_, tgt) in flows.items() if tgt == gateway} == {
        f for f, (src, _) in flows.items() if src == juncao
    }
    assert {f for f, (src, _) in flows.items() if src == gateway} == saidas
    assert nodes[gateway].get("default") == default
    declared_in = {el.text for el in nodes[gateway].findall(f"{_M}incoming")}
    assert declared_in == {f for f, (src, _) in flows.items() if src == juncao}
    for saida in saidas:
        condition = nodes[saida].find(f"{_M}conditionExpression")
        texto = condition.text.strip() if condition is not None and condition.text else None
        assert texto == _CONDICOES[saida], saida


def test_o_processo_nao_tem_juncao_paralela_nem_inclusiva() -> None:
    process = _process()
    proibidos = [
        el.get("id")
        for el in process.iter()
        if el.tag in {f"{_M}parallelGateway", f"{_M}inclusiveGateway", f"{_M}complexGateway"}
    ]
    assert not proibidos


def test_nenhum_gateway_do_processo_junta_e_bifurca() -> None:
    """A regra bpmnlint `no-gateway-join-fork`, lida direto do XML (controle independente do harness)."""
    process = _process()
    flows = _flows(process)
    gateways = [el.get("id", "") for el in process.iter() if el.tag.endswith("Gateway")]
    assert len(gateways) >= 8  # controle: a varredura vê os gateways do processo
    for gw in gateways:
        entradas = sum(1 for _, tgt in flows.values() if tgt == gw)
        saidas = sum(1 for src, _ in flows.values() if src == gw)
        assert not (entradas > 1 and saidas > 1), f"{gw} junta e bifurca"
