"""E-SF-26 (EP-REG-13a): subprocesso "Declaração de Saúde e CPT" no C2 do CA_1 — testes estáticos sobre o XML.

O corpus CA é documento não implantável (decisão da coordenação): nada aqui roda na engine. Testes nomeados
pelo plano: `test_ca1_cpt_decision_is_human_task` e `test_ds_node_fail_closed_until_vw0_d16`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

CA1 = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "processes"
    / "New_BPMN"
    / "Adapted_BPMN"
    / "CA_1_COMPRA (com suporte)_v4_adaptado.bpmn"
)
NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "camunda": "http://camunda.org/schema/1.0/bpmn",
}
CAMUNDA = "{http://camunda.org/schema/1.0/bpmn}"
IMPLEMENTACAO = ("class", "delegateExpression", "expression", "type", "topic", "formKey")


@pytest.fixture(scope="module")
def raiz() -> etree._Element:
    return etree.parse(str(CA1)).getroot()


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


def test_ca1_cpt_decision_is_human_task(raiz: etree._Element) -> None:
    decisao = _por_id(raiz, "C2DS_T_DecidirCPT")
    assert _local(decisao) == "userTask"
    assert decisao.get(f"{CAMUNDA}candidateGroups") == "governanca-clinica"
    assert not any(decisao.get(f"{CAMUNDA}{a}") for a in IMPLEMENTACAO)
    assert decisao.find("bpmn:extensionElements", NS) is None  # nenhum listener que decida por ela


def test_ds_node_fail_closed_until_vw0_d16(raiz: etree._Element) -> None:
    gateway = _por_id(raiz, "C2DS_G_Ratificada")
    assert _local(gateway) == "exclusiveGateway"
    padrao = _por_id(raiz, gateway.get("default"))
    assert padrao.get("targetRef") == "C2DS_End_NaoRatificada"
    assert padrao.find("bpmn:conditionExpression", NS) is None
    fim = _por_id(raiz, "C2DS_End_NaoRatificada")
    erro = _um(raiz, f"//bpmn:error[@id='{fim.find('bpmn:errorEventDefinition', NS).get('errorRef')}']")
    assert erro.get("errorCode") == "DS_NAO_RATIFICADO"
    # O único outro caminho depende da ratificação declarada.
    saidas = raiz.xpath("//bpmn:sequenceFlow[@sourceRef='C2DS_G_Ratificada']", namespaces=NS)
    condicionais = [f for f in saidas if f is not padrao]
    assert len(condicionais) == 1
    cond = condicionais[0].find("bpmn:conditionExpression", NS).text
    # Constante falsa: nenhuma variável gravada por tarefa ou chamador abre o caminho.
    assert cond.strip() == "${false}"
    assert condicionais[0].get("targetRef") == "C2DS_T_Declarar"
    # O erro é capturado na borda do subprocesso e termina SEM efeito (sem listener, sem mensagem).
    borda = _por_id(raiz, "C2_B_DSNaoRatificada")
    assert borda.get("attachedToRef") == "C2_SP_DeclaracaoSaude"
    assert borda.find("bpmn:errorEventDefinition", NS).get("errorRef") == erro.get("id")
    fim_externo = _por_id(
        raiz, _fluxo(raiz, "C2_B_DSNaoRatificada", "C2_End_DSIndisponivel").get("targetRef")
    )
    assert fim_externo.find("bpmn:extensionElements", NS) is None
    assert len(fim_externo) == 1  # só o incoming: nenhuma definição de evento, nenhum efeito


def test_oferta_com_cpt_percorre_carta_declaracao_e_decisao(raiz: etree._Element) -> None:
    oferta = _por_id(raiz, "C2_G_OfertaCPT")
    com_cpt = _fluxo(raiz, "C2_G_OfertaCPT", "C2_SP_DeclaracaoSaude")
    # Forma tolerante: variável ausente não vira incidente no gateway (o padrão segue o caminho de antes).
    esperado = '${execution.getVariable("ofertaComCpt") == true}'
    assert com_cpt.find("bpmn:conditionExpression", NS).text.strip() == esperado
    assert oferta.get("default") != com_cpt.get("id")
    for origem, destino in [
        ("C2DS_Start", "C2DS_T_Carta"),
        ("C2DS_T_Carta", "C2DS_G_Ratificada"),
        ("C2DS_G_Ratificada", "C2DS_T_Declarar"),
        ("C2DS_T_Declarar", "C2DS_T_DecidirCPT"),
        ("C2DS_T_DecidirCPT", "C2DS_End_Concluida"),
    ]:
        _fluxo(raiz, origem, destino)


def test_sem_cpt_segue_o_caminho_de_antes_e_a_declaracao_vem_antes_do_aceite(raiz: etree._Element) -> None:
    _fluxo(raiz, "C2_Start", "C2_G_OfertaCPT")
    oferta = _por_id(raiz, "C2_G_OfertaCPT")
    padrao = _por_id(raiz, oferta.get("default"))
    assert padrao.get("targetRef") == "C2_G_Junta"
    _fluxo(raiz, "C2_SP_DeclaracaoSaude", "C2_G_Junta")
    _fluxo(raiz, "C2_G_Junta", "C2_T_Aceite")
    entradas_do_aceite = raiz.xpath("//bpmn:sequenceFlow[@targetRef='C2_T_Aceite']", namespaces=NS)
    assert [f.get("sourceRef") for f in entradas_do_aceite] == ["C2_G_Junta"]


def test_subprocesso_so_tem_tarefa_humana_gateway_e_evento(raiz: etree._Element) -> None:
    # Nenhum motivo novo de recusa no motor: nada de tarefa de serviço, script ou regra.
    sub = _por_id(raiz, "C2_SP_DeclaracaoSaude")
    tipos = {
        _local(n) for n in sub if _local(n) not in {"documentation", "incoming", "outgoing", "sequenceFlow"}
    }
    assert tipos == {"startEvent", "userTask", "exclusiveGateway", "endEvent"}
    for tarefa in sub.findall("bpmn:userTask", NS):
        assert not any(tarefa.get(f"{CAMUNDA}{a}") for a in IMPLEMENTACAO if a != "formKey")


def test_sem_agravo_e_sem_prazo_no_subprocesso(raiz: etree._Element) -> None:
    ids = [
        "C2_G_OfertaCPT",
        "C2_G_Junta",
        "C2_SP_DeclaracaoSaude",
        "C2_B_DSNaoRatificada",
        "C2_End_DSIndisponivel",
    ]
    textos = [etree.tostring(_por_id(raiz, i), encoding="unicode") for i in ids]
    for texto in textos:
        sem_negacao = texto.replace("Sem Agravo", "").replace("sem Agravo", "")
        assert "agravo" not in sem_negacao.casefold()
        assert "timerEventDefinition" not in texto
