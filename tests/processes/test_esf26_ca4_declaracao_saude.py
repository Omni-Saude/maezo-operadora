"""E-SF-26 (EP-REG-13a): "Declaração de Saúde e CPT" no arquivo próprio CA_4 — testes estáticos sobre o XML.

O corpus CA é documento não implantável (decisão da coordenação): nada aqui roda na engine. O subprocesso
mora num arquivo NOVO para não tocar o CA_1 (que segue idêntico à main); a ligação a partir do C2 do CA_1
é LIGAÇÃO PENDENTE. Testes nomeados pelo plano: `test_ca1_cpt_decision_is_human_task` (aqui
`test_ca4_cpt_decision_is_human_task`, porque o nó mora no CA_4) e `test_ds_node_fail_closed_until_vw0_d16`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

ADAPTED = Path(__file__).resolve().parents[2] / "docs" / "processes" / "New_BPMN" / "Adapted_BPMN"
CA4 = ADAPTED / "CA_4_DECLARACAO_SAUDE_CPT_v4_adaptado.bpmn"
CA1 = ADAPTED / "CA_1_COMPRA (com suporte)_v4_adaptado.bpmn"
NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "camunda": "http://camunda.org/schema/1.0/bpmn",
}
CAMUNDA = "{http://camunda.org/schema/1.0/bpmn}"
IMPLEMENTACAO = ("class", "delegateExpression", "expression", "type", "topic", "formKey")
PROCESSO = "Process_CA_DECLARACAO_SAUDE"


@pytest.fixture(scope="module")
def raiz() -> etree._Element:
    return etree.parse(str(CA4)).getroot()


def _um(raiz: etree._Element, xpath: str) -> etree._Element:
    achados = raiz.xpath(xpath, namespaces=NS)
    assert len(achados) == 1, (xpath, len(achados))
    return achados[0]


def _por_id(raiz: etree._Element, ident: str) -> etree._Element:
    return _um(raiz, f"//*[@id='{ident}']")


def _fluxo(raiz: etree._Element, origem: str, destino: str) -> etree._Element:
    return _um(raiz, f"//bpmn:sequenceFlow[@sourceRef='{origem}' and @targetRef='{destino}']")


def _local(no: etree._Element) -> str:
    return etree.QName(no).localname


def test_ca4_cpt_decision_is_human_task(raiz: etree._Element) -> None:
    decisao = _por_id(raiz, "DSCS_T_DecidirCPT")
    assert _local(decisao) == "userTask"
    assert decisao.get(f"{CAMUNDA}candidateGroups") == "governanca-clinica"
    assert not any(decisao.get(f"{CAMUNDA}{a}") for a in IMPLEMENTACAO)
    assert decisao.find("bpmn:extensionElements", NS) is None  # nenhum listener que decida por ela


def test_ds_node_fail_closed_until_vw0_d16(raiz: etree._Element) -> None:
    gateway = _por_id(raiz, "DSCS_G_Ratificada")
    assert _local(gateway) == "exclusiveGateway"
    padrao = _por_id(raiz, gateway.get("default"))
    assert padrao.get("targetRef") == "DSCS_End_NaoRatificada"
    assert padrao.find("bpmn:conditionExpression", NS) is None
    fim = _por_id(raiz, "DSCS_End_NaoRatificada")
    erro = _um(raiz, f"//bpmn:error[@id='{fim.find('bpmn:errorEventDefinition', NS).get('errorRef')}']")
    assert erro.get("errorCode") == "DS_NAO_RATIFICADO"
    saidas = raiz.xpath("//bpmn:sequenceFlow[@sourceRef='DSCS_G_Ratificada']", namespaces=NS)
    condicionais = [f for f in saidas if f is not padrao]
    assert len(condicionais) == 1
    # Constante falsa: nenhuma variável gravada por tarefa ou chamador abre o caminho.
    assert condicionais[0].find("bpmn:conditionExpression", NS).text.strip() == "${false}"
    assert condicionais[0].get("targetRef") == "DSCS_T_Declarar"
    # O erro é capturado na borda do subprocesso e termina SEM efeito (sem listener, sem mensagem).
    borda = _por_id(raiz, "DSC_B_NaoRatificada")
    assert borda.get("attachedToRef") == "DSC_SP_DeclaracaoSaude"
    assert borda.find("bpmn:errorEventDefinition", NS).get("errorRef") == erro.get("id")
    fim_externo = _por_id(raiz, _fluxo(raiz, "DSC_B_NaoRatificada", "DSC_End_Indisponivel").get("targetRef"))
    assert fim_externo.find("bpmn:extensionElements", NS) is None
    assert len(fim_externo) == 1  # só o incoming: nenhuma definição de evento, nenhum efeito


def test_oferta_com_cpt_percorre_carta_declaracao_e_decisao(raiz: etree._Element) -> None:
    for origem, destino in [
        ("DSC_Start", "DSC_SP_DeclaracaoSaude"),
        ("DSCS_Start", "DSCS_T_Carta"),
        ("DSCS_T_Carta", "DSCS_G_Ratificada"),
        ("DSCS_G_Ratificada", "DSCS_T_Declarar"),
        ("DSCS_T_Declarar", "DSCS_T_DecidirCPT"),
        ("DSCS_T_DecidirCPT", "DSCS_End_Concluida"),
        ("DSC_SP_DeclaracaoSaude", "DSC_End_Concluida"),
    ]:
        _fluxo(raiz, origem, destino)


def test_subprocesso_so_tem_tarefa_humana_gateway_e_evento(raiz: etree._Element) -> None:
    # Nenhum motivo de recusa no motor: nada de tarefa de serviço, script, regra ou chamada.
    processo = _por_id(raiz, PROCESSO)
    proibidos = ("serviceTask", "scriptTask", "businessRuleTask", "sendTask", "callActivity", "receiveTask")
    for tipo in proibidos:
        assert not processo.xpath(f".//bpmn:{tipo}", namespaces=NS), tipo
    sub = _por_id(raiz, "DSC_SP_DeclaracaoSaude")
    tipos = {
        _local(n) for n in sub if _local(n) not in {"documentation", "incoming", "outgoing", "sequenceFlow"}
    }
    assert tipos == {"startEvent", "userTask", "exclusiveGateway", "endEvent"}
    for tarefa in sub.findall("bpmn:userTask", NS):
        assert not any(tarefa.get(f"{CAMUNDA}{a}") for a in IMPLEMENTACAO if a != "formKey")


def test_sem_agravo_e_sem_prazo(raiz: etree._Element) -> None:
    texto = etree.tostring(_por_id(raiz, PROCESSO), encoding="unicode")
    assert "agravo" not in texto.replace("Sem Agravo", "").replace("sem Agravo", "").casefold()
    assert "timerEventDefinition" not in texto


def test_ca1_intocado_e_ligacao_pendente_registrada(raiz: etree._Element) -> None:
    # O CA_1 não ganha nada da declaração de saúde: a ligação a partir do C2 é pendente, por decisão.
    ca1 = CA1.read_text(encoding="utf-8")
    assert "DSC_" not in ca1 and "DS_NAO_RATIFICADO" not in ca1 and PROCESSO not in ca1
    # Nenhum processo do corpus chama este: sem efeito fora do próprio arquivo.
    for bpmn in ADAPTED.glob("*.bpmn"):
        if bpmn != CA4:
            assert PROCESSO not in bpmn.read_text(encoding="utf-8"), bpmn.name
    doc = _um(raiz, "/bpmn:definitions/bpmn:collaboration/bpmn:documentation").text
    assert "LIGAÇÃO PENDENTE" in doc and "Process_CA_COMPRA_C2_DecidirContratar" in doc
