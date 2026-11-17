#!/usr/bin/env python3
"""Eye-check mecânico v2 (substituto PARCIAL do olhar humano; §7 Layout da missão).

Só verifica NÓS (não containers): tarefas, eventos, gateways, dataStores, anotações, grupos.
  (1) nenhuma aresta ATRAVESSA o interior de um nó que não seja sua origem/destino;
  (2) nenhum rótulo (dc:Bounds) sobrepõe o interior de QUALQUER nó (o próprio incluso);
  (3) nenhum rótulo cobre segmento de aresta alheia.

Uso: python3 eye_check_mecanico.py arquivo.bpmn ... → exit 1 se qualquer achado.
"""
from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree

NS = {
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
}

CONTAINER_PREFIXES = ("Participant_", "Lane_")


def _bounds(el):
    b = el.find(f"{{{NS['dc']}}}Bounds")
    if b is None:
        return None
    return (float(b.get("x")), float(b.get("y")), float(b.get("w") or b.get("width")), float(b.get("h") or b.get("height")))


def shapes(doc):
    """id -> (rect, é_container). Container = elemento SEMÂNTICO participant/lane (não por prefixo de id)."""
    semantic_ids = {el.get("id"): etree.QName(el).localname for el in doc.getroot().iter() if el.get("id")}
    out = {}
    for sh in doc.iter(f"{{{NS['bpmndi']}}}BPMNShape"):
        el = sh.get("bpmnElement")
        r = _bounds(sh)
        if el and r:
            is_container = semantic_ids.get(el) in ("participant", "lane")
            out[el] = (r, is_container)
    return out


def labels(doc):
    out = {}
    for sh in doc.iter(f"{{{NS['bpmndi']}}}BPMNShape", f"{{{NS['bpmndi']}}}BPMNEdge"):
        el = sh.get("bpmnElement")
        lb = sh.find(f"{{{NS['bpmndi']}}}BPMNLabel/{{{NS['dc']}}}Bounds")
        if el and lb is not None:
            out[el] = (float(lb.get("x")), float(lb.get("y")), float(lb.get("width")), float(lb.get("height")))
    return out


def edges(doc):
    out = []
    for ed in doc.iter(f"{{{NS['bpmndi']}}}BPMNEdge"):
        el = ed.get("bpmnElement")
        wps = [(float(w.get("x")), float(w.get("y"))) for w in ed.findall(f"{{{NS['di']}}}waypoint")]
        src = tgt = None
        for tag in ("sequenceFlow", "messageFlow", "association", "dataInputAssociation", "dataOutputAssociation"):
            for f in doc.getroot().iter(f"{{{NS['bpmn']}}}{tag}"):
                if f.get("id") == el:
                    src, tgt = f.get("sourceRef"), f.get("targetRef")
        out.append((el, src, tgt, wps))
    return out


def seg_interior(p1, p2, r, tol=0.5):
    x, y, w, h = r
    x2, y2 = x + w, y + h
    for i in range(241):
        t = i / 240
        px, py = p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t
        if x + tol < px < x2 - tol and y + tol < py < y2 - tol:
            return True
    return False


def overlap(a, b, tol=1.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (min(ax + aw, bx + bw) - max(ax, bx)) > tol and (min(ay + ah, by + bh) - max(ay, by)) > tol


def main(paths):
    achados = []
    for p in paths:
        doc = etree.parse(str(p))
        S = shapes(doc)
        L = labels(doc)
        E = edges(doc)
        nodes = {k: v[0] for k, v in S.items() if not v[1]}
        NSB = "{%s}" % NS["bpmn"]
        textos = {}
        for tag in ("textAnnotation",):
            for el in doc.getroot().iter(f"{NSB}{tag}"):
                if el.get("id"):
                    textos[el.get("id")] = "".join(el.itertext()) or el.get("text", "") or ""
        for eid, src, tgt, wps in E:
            if len(wps) < 2:
                continue
            for i in range(len(wps) - 1):
                for nid, r in nodes.items():
                    if nid in (src, tgt):
                        continue
                    if seg_interior(wps[i], wps[i + 1], r):
                        achados.append(f"{Path(p).name}: aresta `{eid}` atravessa nó `{nid}` {r}")
        for lid, lr in L.items():
            for nid, r in nodes.items():
                if overlap(lr, r):
                    achados.append(f"{Path(p).name}: rótulo de `{lid}` {lr} sobrepõe nó `{nid}` {r}")
            for eid, src, tgt, wps in E:
                if eid == lid:
                    continue
                for i in range(len(wps) - 1):
                    if seg_interior(wps[i], wps[i + 1], lr):
                        achados.append(f"{Path(p).name}: rótulo de `{lid}` {lr} cobre aresta `{eid}`")
                        break
        # (4) rótulo × rótulo: dois labels não podem se sobrepor
        lids = sorted(L)
        for i in range(len(lids)):
            for j in range(i + 1, len(lids)):
                if overlap(L[lids[i]], L[lids[j]]):
                    achados.append(
                        f"{Path(p).name}: rótulo de `{lids[i]}` {L[lids[i]]} colide com rótulo de `{lids[j]}` {L[lids[j]]}"
                    )
        # (5) overflow de texto em anotação: área necessária (≈7px/char × 12px/linha) > área do bounds×1.2
        for aid, texto in textos.items():
            r = nodes.get(aid)
            if not r or not texto:
                continue
            need = len(texto.strip()) * 7 * 12
            have = r[2] * r[3] * 1.2
            if need > have:
                achados.append(
                    f"{Path(p).name}: anotação `{aid}` texto transborda bounds {r} (chars={len(texto.strip())}, área~{need//12}px-linha vs {int(r[2])}x{int(r[3])})"
                )
    for a in achados:
        print(f"[EYE] {a}")
    print(f"[EYE] {Path(p).name if len(paths)==1 else ''} total de achados: {len(achados)}")
    return 1 if achados else 0


if __name__ == "__main__":
    sys.exit(main([Path(a) for a in sys.argv[1:]]))
