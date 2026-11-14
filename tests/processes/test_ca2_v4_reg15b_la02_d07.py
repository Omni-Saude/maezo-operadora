"""Static pin: REG-1.5(b) LA-02/D-07 emenda do CA_2 v4.

Refusa file-wide as formas erradas do inciso (art. 12 nao tem VIII) e fixa a ancora
correta (Lei 9.656/1998, art. 12, VI — P30D), o conjunto LA-02 explicito e os
invariantes do wiring (default+condicao, join 2-inbound). Fonte: Planalto 2026-10-27.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
NS = {"bpmn": BPMN_NS}
BPMN_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "processes"
    / "New_BPMN"
    / "Adapted_BPMN"
    / "CA_2_UTILIZACAO_v4_adaptado.bpmn"
)

TIMER_ID = "U4_B_PrazoLegalReembolso"
HOST_ID = "U4_T_AnalisarReembolso"
JOIN_ID = "U4_GW_Join"
SPLIT_ID = "U4_GW_RotaPgto"
MERGE_ID = "U4_GW_MergePgto"
NEW_NODES = [
    "U4_GW_RotaPgto",
    "U4_T_Liquidar",
    "U4_T_AuditarContas",
    "U4_T_ApurarCoparticipacao",
    "U4_GW_MergePgto",
    "U4_T_ReceberDocReembolso",
    "U4_T_AnalisarReembolso",
    "U4_B_PrazoLegalReembolso",
    "U4_End_PrazoLegalVencido",
]
NEW_FLOWS = [
    "U4_F_RotaConta",
    "U4_F_LiqContas",
    "U4_F_ContasCopart",
    "U4_F_CopartMerge",
    "U4_F_RotaReembolso",
    "U4_F_RecDocAnalise",
    "U4_F_AnaliseMerge",
    "U4_F_PrazoVencido",
]
NEW_OBJECTS = [
    "DO_U4_ContaConferida",
    "DOR_U4_ContaConferida",
    "DO_U4_CoparticipacaoApurada",
    "DOR_U4_CoparticipacaoApurada",
    "DO_U4_DocReembolso",
    "DOR_U4_DocReembolso",
]
WRONG_FORMS = ["art. 12, VIII", "art. 12 VIII", "artigo 12, VIII", "artigo 12 VIII"]


def _load():
    assert BPMN_PATH.is_file(), f"missing BPMN under pin: {BPMN_PATH}"
    return ET.parse(BPMN_PATH).getroot()


def _by_id(root, el_id):
    return [el for el in root.iter() if el.get("id") == el_id]


def _docs(el):
    b = f"{{{BPMN_NS}}}documentation"
    return "".join(d.text or "" for d in el.findall(b))


def test_wrong_inciso_forms_are_absent_file_wide():
    text = BPMN_PATH.read_text(encoding="utf-8")
    for form in WRONG_FORMS:
        assert form not in text, f"forma errada do inciso presente: {form!r}"


def test_timer_anchor_and_shape():
    root = _load()
    (timer,) = _by_id(root, TIMER_ID)
    assert timer.tag.endswith("boundaryEvent")
    assert timer.get("attachedToRef") == HOST_ID
    assert timer.get("cancelActivity") == "false"
    dur = timer.find(".//bpmn:timeDuration", NS)
    assert dur is not None and dur.text == "P30D"
    doc = _docs(timer)
    assert "art. 12, inciso VI" in doc
    assert "planalto.gov.br/ccivil_03/leis/l9656.htm" in doc
    assert "2026-10-27" in doc


def test_la02_set_is_explicit():
    root = _load()
    for el_id in NEW_NODES + NEW_FLOWS + NEW_OBJECTS:
        found = _by_id(root, el_id)
        assert found, f"elemento LA-02 ausente: {el_id}"
        assert len(found) == 1, f"id duplicado: {el_id}"
    liquidar = _by_id(root, "U4_T_Liquidar")[0]
    assert "decisão pendente" not in _docs(liquidar)
    assert "D-07" in _docs(liquidar)


def test_wiring_invariants():
    root = _load()
    inbounds = {f.get("id") for f in root.iter(f"{{{BPMN_NS}}}sequenceFlow") if f.get("targetRef") == JOIN_ID}
    assert len(inbounds) == 2, f"{JOIN_ID} deve manter 2 inbound (F-E01)"
    split = _by_id(root, SPLIT_ID)[0]
    assert split.get("default") == "U4_F_RotaConta"
    conds = [
        f
        for f in root.iter(f"{{{BPMN_NS}}}sequenceFlow")
        if f.get("sourceRef") == SPLIT_ID and f.find("bpmn:conditionExpression", NS) is not None
    ]
    assert len(conds) == 1, "split deve ter exatamente 1 flow condicional (reembolso)"
    merge = _by_id(root, MERGE_ID)[0]
    merge_out = {el.text for el in merge.findall("bpmn:outgoing", NS)}
    merge_in = {el.text for el in merge.findall("bpmn:incoming", NS)}
    assert len(merge_in) == 2 and len(merge_out) == 1
    assert merge_out == {"F_CA_UTILIZACAO_U4_LiquidarAvaliar_12"}
    actual_in = {
        f.get("id") for f in root.iter(f"{{{BPMN_NS}}}sequenceFlow") if f.get("targetRef") == MERGE_ID
    }
    assert merge_in == actual_in
