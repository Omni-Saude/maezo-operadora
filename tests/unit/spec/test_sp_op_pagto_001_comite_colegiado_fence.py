"""Fence R-092 (F-081/F-042): `UT_AprovacaoComite` modelada no BPMN de SP-OP-PAGTO-001.

Le os artefatos REAIS (BPMN + DMN `pagto_alcada` + registro de aprovacoes + review-queue) e prende
o desenho colegiado contra regressao silenciosa:

1. o comite existe e e colegiado de verdade: `UT_AprovacaoComite` (grupo `comite-financeiro`)
   dentro de `SP_AprovacaoComite`, multi-instancia SEQUENCIAL com cardinalidade `quorum_comite`
   (fato da DMN) e encerramento no primeiro voto que nao aprova;
2. os campos do voto sao LOCAIS a cada instancia (um membro nunca herda o APROVAR do anterior) e
   achatados de volta ao processo ao fim de cada instancia;
3. DARK: `GW_TipoAprovacao` so abre o comite com a flag da implantacao e `quorum_comite > 1`; o
   default continua `UT_AprovacaoAlcada`; a prorrogacao de SLA volta ao MESMO gateway;
4. nenhum caminho novo de liberacao: o comite desemboca em `GW_DecisaoPagamento` (APROVAR so via
   o worker gated; CANCELAR -> `End_PagamentoCancelado`); timers vao ao caminho humano;
5. o quorum vem SO da DMN, cada regra emite `quorum_comite`, e o valor do comite e DRAFT declarado.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element

from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.tools.workers.pagto import (
    COMITE_ULTIMO_VOTO_VAR,
    PAGTO_COMITE_COLEGIADO_VAR,
)

_ROOT = Path(__file__).resolve().parents[3]
_BPMN = _ROOT / "spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn"
_DMN = _ROOT / "spec/processes/dmn/pagto_alcada.dmn"
_B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_C = "{http://camunda.org/schema/1.0/bpmn}"
_D = {"dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
#: Campos de DECISAO do voto. As justificativas (texto livre) ficam de fora de proposito: um nome
#: de texto livre numa superficie BPMN exige disposicao do DPO no manifesto CODEOWNED da cerca PHI.
_CAMPOS_DO_VOTO = (
    "decisao_pagamento",
    "aprovador_id",
    "aprovador_tier",
    "valor_aprovado_cents",
)

_ROOT_EL = parse_xml_without_dtd(_BPMN.read_bytes())
_PROCESS = _ROOT_EL.find(f"{_B}process")
assert _PROCESS is not None


def _por_id(eid: str) -> Element:
    el = next((e for e in _PROCESS.iter() if e.get("id") == eid), None)
    assert el is not None, eid
    return el


def _flows() -> dict[str, Element]:
    return {f.get("id", ""): f for f in _PROCESS.iter(f"{_B}sequenceFlow")}


def _flows_de(source: str) -> list[Element]:
    return [f for f in _flows().values() if f.get("sourceRef") == source]


def test_ut_aprovacao_comite_existe_dentro_do_subprocesso_multi_instancia() -> None:
    sub = _por_id("SP_AprovacaoComite")
    assert sub.tag == f"{_B}subProcess"
    ut = sub.find(f"{_B}userTask")
    assert ut is not None and ut.get("id") == "UT_AprovacaoComite"
    # Mesmo binding value-driven de UT_AprovacaoAlcada (conjunto fechado; nenhum grupo novo).
    assert ut.get(f"{_C}candidateGroups") == "${pagto_alcada.grupo_aprovador}"
    mi = sub.find(f"{_B}multiInstanceLoopCharacteristics")
    assert mi is not None
    assert mi.get("isSequential") == "true"
    assert (mi.findtext(f"{_B}loopCardinality") or "").strip() == "${quorum_comite}"
    cond = (mi.findtext(f"{_B}completionCondition") or "").strip()
    assert cond == f"${{{COMITE_ULTIMO_VOTO_VAR} != 'APROVAR'}}"


def test_cada_voto_passa_pelo_livro_antes_de_encerrar_a_instancia() -> None:
    sub = _por_id("SP_AprovacaoComite")
    st = sub.find(f"{_B}serviceTask")
    assert st is not None and st.get("id") == "ST_RegistrarVotoComite"
    assert st.get(f"{_C}topic") == "operadora.pagto.register_committee_vote"
    internos = {f.get("sourceRef"): f.get("targetRef") for f in sub.findall(f"{_B}sequenceFlow")}
    assert internos == {
        "Start_VotoComite": "UT_AprovacaoComite",
        "UT_AprovacaoComite": "ST_RegistrarVotoComite",
        "ST_RegistrarVotoComite": "End_VotoComiteRegistrado",
    }


def test_campos_do_voto_sao_locais_e_achatados_ao_fim_de_cada_instancia() -> None:
    # O engine recusa camunda:outputParameter no construto multi-instancia (ENGINE-09005): o mapeamento
    # mora na UT interna, nunca no subprocesso.
    assert _por_id("SP_AprovacaoComite").find(f"{_B}extensionElements/{_C}inputOutput") is None
    io = _por_id("UT_AprovacaoComite").find(f"{_B}extensionElements/{_C}inputOutput")
    assert io is not None
    entradas = {p.get("name"): (p.text or "").strip() for p in io.findall(f"{_C}inputParameter")}
    saidas = {p.get("name"): (p.text or "").strip() for p in io.findall(f"{_C}outputParameter")}
    assert set(entradas) == set(_CAMPOS_DO_VOTO)
    # Local vazio: nunca o valor do membro anterior nem do processo.
    assert all(v in {"", '${""}'} for v in entradas.values()), entradas
    assert saidas == {c: f"${{{c}}}" for c in _CAMPOS_DO_VOTO}


def test_gateway_dark_default_e_o_caminho_de_sempre() -> None:
    gw = _por_id("GW_TipoAprovacao")
    assert gw.tag == f"{_B}exclusiveGateway"
    flows = _flows()
    default = flows[gw.get("default", "")]
    assert default.get("targetRef") == "UT_AprovacaoAlcada"
    assert default.find(f"{_B}conditionExpression") is None
    comite = flows["Flow_GWTipo_Comite"]
    assert comite.get("sourceRef") == "GW_TipoAprovacao"
    assert comite.get("targetRef") == "SP_AprovacaoComite"
    cond = (comite.findtext(f"{_B}conditionExpression") or "").strip()
    assert f"execution.getVariable('{PAGTO_COMITE_COLEGIADO_VAR}') == true" in cond
    assert "quorum_comite > 1" in cond
    # Os dois caminhos de entrada na aprovacao passam pelo mesmo gateway.
    entradas = {f.get("sourceRef") for f in flows.values() if f.get("targetRef") == "GW_TipoAprovacao"}
    assert entradas == {"ST_PrepareApprovalDossier", "GW_DecisaoCoordenacao"}
    assert flows["Flow_GWCoord_Prorrogar"].get("targetRef") == "GW_TipoAprovacao"
    alcada_in = {f.get("sourceRef") for f in flows.values() if f.get("targetRef") == "UT_AprovacaoAlcada"}
    assert alcada_in == {"GW_TipoAprovacao"}


def test_comite_desemboca_na_mesma_decisao_e_nunca_libera_sozinho() -> None:
    saidas = _flows_de("SP_AprovacaoComite")
    assert [f.get("targetRef") for f in saidas] == ["GW_DecisaoPagamento"]
    decisao = {f.get("targetRef") for f in _flows_de("GW_DecisaoPagamento")}
    assert decisao == {
        "ST_ReleaseHighValue",
        "ICE_AguardarDadosCorrigidos",
        "ST_RegisterPaymentRefusal",
        "ST_PublishCancelado",
    }
    cancelar = _flows()["Flow_GWDec_Cancelar"]
    assert "'CANCELAR'" in (cancelar.findtext(f"{_B}conditionExpression") or "")
    assert {f.get("targetRef") for f in _flows_de("ST_PublishCancelado")} == {"End_PagamentoCancelado"}


def test_timers_do_comite_levam_ao_caminho_humano() -> None:
    timers = {
        b.get("id"): b
        for b in _PROCESS.findall(f"{_B}boundaryEvent")
        if b.get("attachedToRef") == "SP_AprovacaoComite"
    }
    assert set(timers) == {"BT_AlertaSlaComite", "BT_SlaComite"}
    assert timers["BT_AlertaSlaComite"].get("cancelActivity") == "false"
    assert timers["BT_SlaComite"].get("cancelActivity") == "true"
    assert [f.get("targetRef") for f in _flows_de("BT_AlertaSlaComite")] == ["ST_NotificarRiscoSla"]
    assert [f.get("targetRef") for f in _flows_de("BT_SlaComite")] == ["ST_PublishSlaBreach"]


def test_brt_alcada_achata_o_quorum_da_dmn() -> None:
    io = _por_id("BRT_AlcadaRouting").find(f"{_B}extensionElements/{_C}inputOutput")
    assert io is not None
    saidas = {p.get("name"): (p.text or "").strip() for p in io.findall(f"{_C}outputParameter")}
    assert saidas["quorum_comite"] == "${pagto_alcada.quorum_comite}"


def _quoruns() -> dict[str, str]:
    table = parse_xml_without_dtd(_DMN.read_bytes()).find(".//dmn:decisionTable", _D)
    assert table is not None
    nomes = [o.get("name", "") for o in table.findall("dmn:output", _D)]
    out = {}
    for rule in table.findall("dmn:rule", _D):
        textos = [(e.findtext("dmn:text", "", _D) or "").strip() for e in rule.findall("dmn:outputEntry", _D)]
        out[rule.get("id", "")] = dict(zip(nomes, textos, strict=True))["quorum_comite"]
    return out


def test_toda_regra_da_dmn_emite_quorum_e_so_o_comite_passa_de_1() -> None:
    table = parse_xml_without_dtd(_DMN.read_bytes()).find(".//dmn:decisionTable", _D)
    assert table is not None
    saida = next(o for o in table.findall("dmn:output", _D) if o.get("name") == "quorum_comite")
    assert saida.get("typeRef") == "integer"
    quoruns = _quoruns()
    assert quoruns.pop("r_catchall") == "2"
    assert set(quoruns.values()) == {"1"}


def test_quorum_do_comite_e_draft_declarado_para_humano() -> None:
    """O valor do quorum nao foi decidido pelo dono: DRAFT na DMN, no registro e na fila humana."""
    assert "VALOR DRAFT, NAO DECIDIDO" in _DMN.read_text(encoding="utf-8")
    registro = (_ROOT / "spec/aprovacoes-de-regras.yaml").read_text(encoding="utf-8")
    assert "quorum_comite" in registro
    fila = (_ROOT / "docs/review-queue.md").read_text(encoding="utf-8")
    assert "quorum_comite" in fila and "R-092" in fila
