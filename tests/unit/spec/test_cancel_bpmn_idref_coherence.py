"""F-107 (R-050): SP-OP-CANCEL-001 nao pode ter IDREF de `<incoming>`/`<outgoing>` quebrado.

`BRT_CancelSla` declarava `<outgoing>Flow_Sla_Dossie</outgoing>`, um sequenceFlow que nunca existiu
— o flow real e `Flow_Sla_Classificacao` (BRT_CancelSla -> BRT_Classificacao, contrato
SP-OP-CANCEL-001 §`cancel_routing`: "entre `BRT_CancelSla` e `ST_PrepareDossier`"). O documento era
invalido contra o XSD (IDREF sem alvo) e `validate-artifacts` nao via a classe.

Esta trava prova a coerencia de incidencia nos DOIS sentidos, para o processo inteiro:
- todo `<incoming>`/`<outgoing>` resolve para um sequenceFlow existente, cujo targetRef/sourceRef
  e o proprio no que o declara;
- todo sequenceFlow aparece como `<outgoing>` do seu sourceRef e `<incoming>` do seu targetRef.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from maezo.platform.xml_safe import parse_xml_without_dtd

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BPMN = _REPO_ROOT / "spec" / "processes" / "bpmn" / "SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn"
_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"


def _process() -> ET.Element:
    root = parse_xml_without_dtd(_BPMN.read_bytes())
    procs = root.findall(f"{_NS}process")
    assert len(procs) == 1, f"esperado 1 processo por arquivo, achei {len(procs)}"
    return procs[0]


def _flows(proc: ET.Element) -> dict[str, tuple[str, str]]:
    flows = {
        f.get("id", ""): (f.get("sourceRef", ""), f.get("targetRef", ""))
        for f in proc.iter(f"{_NS}sequenceFlow")
    }
    assert flows, "nenhum sequenceFlow lido — trava vazia"
    return flows


def _declared(proc: ET.Element, tag: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for node in proc.iter():
        for ref in node.findall(f"{_NS}{tag}"):
            out.append((node.get("id", ""), (ref.text or "").strip()))
    return out


def test_brt_cancel_sla_sai_por_flow_sla_classificacao() -> None:
    proc = _process()
    outs = [ref for nid, ref in _declared(proc, "outgoing") if nid == "BRT_CancelSla"]
    assert outs == ["Flow_Sla_Classificacao"]
    assert _flows(proc)["Flow_Sla_Classificacao"] == ("BRT_CancelSla", "BRT_Classificacao")


def test_todo_incoming_outgoing_resolve_para_o_flow_do_proprio_no() -> None:
    proc = _process()
    flows = _flows(proc)
    broken: list[str] = []
    for nid, ref in _declared(proc, "outgoing"):
        if ref not in flows:
            broken.append(f"{nid} <outgoing>{ref}</outgoing>: flow inexistente")
        elif flows[ref][0] != nid:
            broken.append(f"{nid} <outgoing>{ref}</outgoing>: sourceRef={flows[ref][0]}")
    for nid, ref in _declared(proc, "incoming"):
        if ref not in flows:
            broken.append(f"{nid} <incoming>{ref}</incoming>: flow inexistente")
        elif flows[ref][1] != nid:
            broken.append(f"{nid} <incoming>{ref}</incoming>: targetRef={flows[ref][1]}")
    assert not broken, "\n".join(broken)


def test_todo_flow_esta_declarado_nas_duas_pontas() -> None:
    proc = _process()
    outs = set(_declared(proc, "outgoing"))
    ins = set(_declared(proc, "incoming"))
    missing = [
        fid for fid, (src, tgt) in _flows(proc).items() if (src, fid) not in outs or (tgt, fid) not in ins
    ]
    assert not missing, f"flows sem declaracao de incidencia em alguma ponta: {missing}"
