#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verificador de DI (Diagram Interchange) para BPMN 2.0 — missão funil 2026-11.

Código determinístico, sem rede, fail-closed. Execução de referência para
diagramas BPMN 2.0 descritivos (isExecutable=false) de colaboração (pools/lanes).

Checks (cada um = função independente; achado fail => exit 1):
  1  XSD      — cada .bpmn validado contra BPMN20.xsd OMG (lxml XMLSchema,
               imports resolvidos localmente; sha256 dos XSDs pinados conferido).
  2a/2b/2c    — cobertura DI bidirecional (imprime as DUAS contagens):
               (a) ids semânticos desenháveis - bpmnElement referenciados = vazio;
               (b) nenhum bpmnElement de BPMNShape/Edge/Label aponta p/ id inexistente;
               (c) 1 BPMNShape por desenhável, 1 BPMNEdge por flow com >=2 waypoints.
  3a..3h      — geometria: sobreposição (>1px², pares não-containment), contenção
               em lane/pool (boundary: centro sobre a borda do host, <=2px),
               waypoints na borda (<=5px), ortogonalidade, inteiros não negativos,
               grade 10 (warning), tamanhos canônicos, flags DI (isHorizontal/
               isMarkerVisible/isExpanded explícito — atributos do BPMNShape).
  4a..4d      — domínio: custódia clínica isolada de lanes comerciais/medição/
               mensagens/recuperação/retenção; timeDuration ISO 8601 (${...} ok);
               gateway de portão com default + annotation; messageFlow entre
               participants distintos e vertical na travessia.
  5  LINT     — subprocesso `npx --no-install bpmnlint --config bpmnlintrc-di.json`;
               indisponível => UNVERIFIED (não PASS, não fail).
  6  RENDER   — campo "render": "pending" no JSON (harness bpmn-js é do orquestrador).

Uso:
  python3 di_verifier.py arquivo.bpmn [mais.bpmn ...] [--json saida.json]
  python3 di_verifier.py --dir <path> [--json saida.json]
Opções: --xsd-dir, --tools-dir, --skip-lint, --fail-on-warn.
Exit codes: 0 = sem achados fail; 1 = há achados fail; 2 = erro de uso/interno.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from lxml import etree

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}
B = "{%s}" % NS["bpmn"]
DI_ = "{%s}" % NS["bpmndi"]
DC = "{%s}" % NS["dc"]
DDI = "{%s}" % NS["di"]  # namespace do DI genérico (waypoints)

# sha256 pinados na missão — o esquema só é compilado se baterem.
XSD_PINADOS = {
    "BPMN20.xsd": "a07c159cb0594573dd7c97b1370dd116112378f377e43c89a8bf512ac5030705",
    "Semantic.xsd": "c4318842f7d2bbc262d7954c9452c501db16f0868eac0b8732ec5d7fb384d9a7",
}

# §7 check 2a: ids semânticos desenháveis.
TASK_TAGS = {
    "task", "userTask", "serviceTask", "businessRuleTask", "sendTask",
    "receiveTask", "manualTask", "scriptTask", "callActivity",
}
SUBPROCESS_TAGS = {"subProcess"}
GATEWAY_TAGS = {
    "exclusiveGateway", "parallelGateway", "inclusiveGateway",
    "eventBasedGateway", "complexGateway",
}
EVENT_TAGS = {
    "startEvent", "endEvent", "intermediateCatchEvent",
    "intermediateThrowEvent", "boundaryEvent",
}
DATA_TAGS = {
    "dataObject", "dataObjectReference", "dataStore", "dataStoreReference",
}
DESENHAVEL_TAGS = (
    {"participant", "lane"}
    | TASK_TAGS | SUBPROCESS_TAGS | GATEWAY_TAGS | EVENT_TAGS
    | DATA_TAGS | {"textAnnotation", "group"}
)
# §7 check 2c: flows que exigem BPMNEdge com >= 2 waypoints.
FLOW_TAGS = {
    "sequenceFlow", "messageFlow", "association",
    "dataInputAssociation", "dataOutputAssociation",
}
# §7 check 3b: flowNodes (tFlowNode no esquema) — dataObject/DataStoreReference
# estendem tFlowElement, NÃO tFlowNode, e ficam fora deste conjunto.
FLOWNODE_TAGS = TASK_TAGS | SUBPROCESS_TAGS | GATEWAY_TAGS | EVENT_TAGS
# Fins de edge que se referenciam por ATRIBUTO sourceRef/targetRef vs. por
# ELEMENTOS filho (data associations usam elementos IDREF).
FLOW_ATTR_REFS = {"sequenceFlow", "messageFlow", "association"}

# §7 check 3g: tamanhos canônicos (exatos / mínimos).
TAM_EXATO: dict[str, tuple[float, float]] = {}
for _t in TASK_TAGS:
    TAM_EXATO[_t] = (100.0, 80.0)
for _t in EVENT_TAGS:
    TAM_EXATO[_t] = (36.0, 36.0)
for _t in GATEWAY_TAGS:
    TAM_EXATO[_t] = (50.0, 50.0)
TAM_EXATO["dataObjectReference"] = (36.0, 50.0)
TAM_EXATO["dataStoreReference"] = (50.0, 50.0)
TAM_MINIMO: dict[str, tuple[float, float]] = {t: (100.0, 80.0) for t in SUBPROCESS_TAGS}

# §9 check 4a: lanes proibidas para custódia clínica (nomes sem acento, minúsculos).
LANES_PROIBIDAS_4A = ("comercial", "medicao", "mensagem", "recuperacao", "retencao")

# §9 check 4b: timeDuration aceitável = ISO 8601 duration ou expressão ${...}.
REGEX_ISO = re.compile(r"^-?P([0-9]+[YMDWH])+$")
REGEX_EXPR = re.compile(r"^\$\{.*\}$")

# §9 check 4c: portão citado em documentation de gateway com 'Gate' no id.
REGEX_PORTAO = re.compile(r"(RL-SF-\d+|A[0-9]+|OS-\d+)")

TOL_BORDA_WAYPOINT = 5.0    # §7 3c: waypoint a <= 5px da borda
TOL_BORDA_BOUNDARY = 2.0    # §7 3b: centro do boundary a <= 2px da borda do host
TOL_SOBREPOSICAO_PX2 = 1.0  # §7 3a: interseção > 1px² conta como sobreposição


# ---------------------------------------------------------------------------
# Estruturas
# ---------------------------------------------------------------------------

@dataclass
class Achado:
    """Um achado de verificação, conforme contrato da missão §7/§9."""
    arquivo: str
    elemento: str
    check: str            # "1", "2a" ... "5"
    detalhe: str
    severidade: str = "fail"  # "fail" | "warn" | "unverified"

    def como_dict(self) -> dict[str, str]:
        return {
            "arquivo": self.arquivo,
            "elemento": self.elemento,
            "check": self.check,
            "detalhe": self.detalhe,
            "severidade": self.severidade,
        }


@dataclass
class Retangulo:
    """dc:Bounds com coordenadas float."""
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def contem(self, r: "Retangulo") -> bool:
        """Contenção total (bordas podem tocar)."""
        return (self.x <= r.x and self.y <= r.y
                and r.x2 <= self.x2 and r.y2 <= self.y2)

    def area_intersecao(self, r: "Retangulo") -> float:
        ix = max(0.0, min(self.x2, r.x2) - max(self.x, r.x))
        iy = max(0.0, min(self.y2, r.y2) - max(self.y, r.y))
        return ix * iy

    def contem_ponto(self, p: tuple[float, float]) -> bool:
        px, py = p
        return self.x <= px <= self.x2 and self.y <= py <= self.y2

    def centro(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def dist_borda(self, p: tuple[float, float]) -> float:
        """Distância do ponto à FRONTEIRA do retângulo.

        Dentro: profundidade até a borda mais próxima.
        Fora: distância euclidiana ao ponto mais próximo.
        Usada por 3b (boundary <=2px) e 3c (waypoint <=5px).
        """
        px, py = p
        if self.contem_ponto(p):
            return min(px - self.x, self.x2 - px, py - self.y, self.y2 - py)
        dx = max(self.x - px, 0.0, px - self.x2)
        dy = max(self.y - py, 0.0, py - self.y2)
        return (dx * dx + dy * dy) ** 0.5


@dataclass
class ResultadoArquivo:
    """Resultado consolidado da verificação de um arquivo."""
    arquivo: str
    achados: list[Achado] = field(default_factory=list)
    total_semantico_desenhavel: int = 0
    total_di_referenciado: int = 0
    diferenca_a: int = 0  # desenháveis sem DI (2a) — deve ser 0
    diferenca_b: int = 0  # refs DI para ids inexistentes (2b) — deve ser 0
    xsd_valido: bool = False
    lint: str = "unverified"  # "pass" | "fail" | "unverified"
    lint_detalhe: str = ""
    render: str = "pending"   # check 6: orquestrador roda o harness depois

    @property
    def tem_fail(self) -> bool:
        return any(a.severidade == "fail" for a in self.achados)

    def resumo_checks(self) -> dict[str, int]:
        r: dict[str, int] = {}
        for a in self.achados:
            r[a.check] = r.get(a.check, 0) + 1
        return r

    def como_dict(self) -> dict[str, Any]:
        return {
            "arquivo": self.arquivo,
            "xsd_valido": self.xsd_valido,
            "contagens": {
                "total_semantico_desenhavel": self.total_semantico_desenhavel,
                "total_di_referenciado": self.total_di_referenciado,
                "diferenca_a": self.diferenca_a,
                "diferenca_b": self.diferenca_b,
            },
            "lint": self.lint,
            "lint_detalhe": self.lint_detalhe,
            "render": self.render,
            "achados": [a.como_dict() for a in self.achados],
            "achados_por_check": self.resumo_checks(),
            "tem_fail": self.tem_fail,
        }


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def norm(txt: str | None) -> str:
    """Minúsculas sem acentos — para comparações de nomes de lane (§9 4a)."""
    if not txt:
        return ""
    decomposta = unicodedata.normalize("NFD", txt)
    sem_acento = "".join(c for c in decomposta if unicodedata.category(c) != "Mn")
    return sem_acento.lower()


def sha256_arq(p: Path) -> str:
    """sha256 hex de um arquivo."""
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for bloco in iter(lambda: fh.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def carregar_esquema(xsd_dir: Path, achados: list[Achado]) -> etree.XMLSchema | None:
    """Check 1 (pré-requisito): confere sha256 pinados e compila BPMN20.xsd.

    Retorna None (e registra erro interno) se pin não bater ou schema não compilar.
    """
    for nome, esperado in XSD_PINADOS.items():
        p = xsd_dir / nome
        if not p.exists() or sha256_arq(p) != esperado:
            achados.append(Achado(nome, nome, "1",
                                  f"XSD pinado ausente ou sha256 divergente em {p}",
                                  "unverified"))
            return None
    try:
        doc = etree.parse(str(xsd_dir / "BPMN20.xsd"))
        return etree.XMLSchema(doc)
    except etree.LxmlError as exc:
        achados.append(Achado("BPMN20.xsd", "BPMN20.xsd", "1",
                              f"falha ao compilar esquema: {exc}", "unverified"))
        return None


def parse_bounds(el: etree._Element) -> Retangulo | None:
    """Lê dc:Bounds filho de uma BPMNShape/BPMNLabel."""
    b = el.find(f"{DC}Bounds")
    if b is None:
        return None
    try:
        return Retangulo(float(b.get("x")), float(b.get("y")),
                         float(b.get("width")), float(b.get("height")))
    except (TypeError, ValueError):
        return None


def waypoints(edge: etree._Element) -> list[tuple[float, float]]:
    """Lê di:waypoint (dc:Point) de uma BPMNEdge."""
    pts: list[tuple[float, float]] = []
    for w in edge.findall(f"{DDI}waypoint"):
        try:
            pts.append((float(w.get("x")), float(w.get("y"))))
        except (TypeError, ValueError):
            continue
    return pts


class Contexto:
    """Índices semânticos/DI de um arquivo já parseado."""

    def __init__(self, raiz: etree._Element) -> None:
        self.raiz = raiz
        # semântico: id -> (tag local, elemento)
        self.sem: dict[str, tuple[str, etree._Element]] = {}
        for el in raiz.iter():
            tag = etree.QName(el).localname
            eid = el.get("id")
            if eid and tag in (DESENHAVEL_TAGS | FLOW_TAGS):
                self.sem[eid] = (tag, el)
        # DI
        self.shapes: dict[str, list[etree._Element]] = {}
        self.edges: dict[str, list[etree._Element]] = {}
        self.labels: list[etree._Element] = []
        for el in raiz.iter(f"{DI_}BPMNShape"):
            ref = el.get("bpmnElement")
            if ref:
                self.shapes.setdefault(ref, []).append(el)
        for el in raiz.iter(f"{DI_}BPMNEdge"):
            ref = el.get("bpmnElement")
            if ref:
                self.edges.setdefault(ref, []).append(el)
        self.labels = list(raiz.iter(f"{DI_}BPMNLabel"))
        # mapa fluxo -> shape de origem/destino (via semantic refs)
        # lanes: lane id -> (nome, [flowNodeRef...])
        self.lanes: dict[str, tuple[str, list[str]]] = {}
        for lane in raiz.iter(f"{B}lane"):
            lid = lane.get("id") or ""
            refs = [r.text or "" for r in lane.findall(f"{B}flowNodeRef")]
            self.lanes[lid] = (lane.get("name") or "", refs)
        # lane de cada flowNode (primeira lane que o referencia)
        self.lane_de: dict[str, str] = {}
        for lid, (_nome, refs) in self.lanes.items():
            for r in refs:
                self.lane_de.setdefault(r, lid)
        # processo de cada elemento (ancestral bpmn:process)
        self.processo_de: dict[str, str] = {}
        self.proc_participantes: dict[str, list[str]] = {}
        for proc in raiz.iter(f"{B}process"):
            pid = proc.get("id") or ""
            for el in proc.iter():
                eid = el.get("id")
                if eid:
                    self.processo_de.setdefault(eid, pid)
        for part in raiz.iter(f"{B}participant"):
            pref = part.get("processRef") or ""
            pid_ = part.get("id") or ""
            self.proc_participantes.setdefault(pref, []).append(pid_)

    def shape_rect(self, eid: str) -> Retangulo | None:
        """Bounds da primeira BPMNShape do elemento."""
        shapes = self.shapes.get(eid)
        if not shapes:
            return None
        return parse_bounds(shapes[0])

    def tag_de(self, eid: str) -> str | None:
        item = self.sem.get(eid)
        return item[0] if item else None

    def refs_fluxo(self, el: etree._Element) -> tuple[list[str], list[str]]:
        """(sources, targets) de um elemento de flow, lendo atributo ou filhos."""
        tag = etree.QName(el).localname
        if tag in FLOW_ATTR_REFS:
            return ([el.get("sourceRef") or ""], [el.get("targetRef") or ""])
        srcs = [r.text or "" for r in el.findall(f"{B}sourceRef")]
        tgt = [r.text or "" for r in el.findall(f"{B}targetRef")]
        return (srcs, tgt)


def eh_portao_clinico(tag: str, el: etree._Element) -> bool:
    """§9 4a: dataStore(Reference) de custódia clínica.

    id prefixo 'DataStore_' OU nome contendo 'clinica'/'custodia' (sem acento).
    """
    eid = el.get("id") or ""
    if eid.startswith("DataStore_"):
        return True
    nome = norm(el.get("name"))
    return "clinica" in nome or "custodia" in nome


# ---------------------------------------------------------------------------
# Check 1 — XSD
# ---------------------------------------------------------------------------

def check_xsd(caminho: Path, doc: etree._ElementTree, esquema: etree.XMLSchema | None,
              achados: list[Achado]) -> bool:
    """Check 1: cada .bpmn válido contra o esquema OMG BPMN 2.0 (§7).

    O esquema é compilado uma vez (imports locais em xsd/, sha256 pinado).
    """
    if esquema is None:
        return False
    if esquema.validate(doc):
        return True
    for erro in esquema.error_log:
        achados.append(Achado(caminho.name, "definitions", "1",
                              f"XSD inválido: {erro.message}"))
    return False


# ---------------------------------------------------------------------------
# Checks 2a/2b/2c — cobertura DI bidirecional
# ---------------------------------------------------------------------------

def check_cobertura(caminho: Path, ctx: Contexto,
                    achados: list[Achado]) -> tuple[int, int, int, int]:
    """Checks 2a/2b/2c — cobertura DI bidirecional (§7).

    Retorna (total_semantico_desenhavel, total_di_referenciado, diferenca_a,
    diferenca_b); diferenca_a/diferenca_b devem ser 0.
    """
    desenhaveis = {eid for eid, (tag, _el) in ctx.sem.items()
                   if tag in DESENHAVEL_TAGS}
    referenciados = set(ctx.shapes.keys()) | set(ctx.edges.keys())
    total_sem = len(desenhaveis)
    total_di = len(referenciados)

    # 2a: todo desenhável deve ser referenciado por DI (diferença vazia).
    faltando = desenhaveis - referenciados
    for eid in sorted(faltando):
        tag = ctx.tag_de(eid)
        achados.append(Achado(caminho.name, f"{tag}@{eid}", "2a",
                              "elemento desenhável sem BPMNShape/BPMNEdge no DI"))

    # 2b: nenhum bpmnElement de BPMNShape/BPMNEdge/BPMNLabel pode ser inexistente.
    dif_b = 0
    for ref, els in list(ctx.shapes.items()) + list(ctx.edges.items()):
        if ref not in ctx.sem:
            for _ in els:
                achados.append(Achado(caminho.name, f"bpmnElement={ref}", "2b",
                                      "DI referencia id semântico inexistente"))
                dif_b += 1
    for lab in ctx.labels:
        ref = lab.get("bpmnElement")
        if ref and ref not in ctx.sem:
            achados.append(Achado(caminho.name, f"BPMNLabel@{ref}", "2b",
                                  "BPMNLabel referencia id inexistente"))
            dif_b += 1

    # 2c: 1 shape por desenhável; 1 edge por flow com >=2 waypoints.
    for eid in sorted(desenhaveis):
        tag = ctx.tag_de(eid) or ""
        n = len(ctx.shapes.get(eid, []))
        if n == 0:
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "2c",
                                  "sem BPMNShape (obrigatório 1 por desenhável)"))
        elif n > 1:
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "2c",
                                  f"{n} BPMNShape para o mesmo bpmnElement"))
    for eid, el_t in ctx.sem.items():
        tag, el = el_t
        if tag not in FLOW_TAGS:
            continue
        edges = ctx.edges.get(eid, [])
        if len(edges) == 0:
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "2c",
                                  "flow sem BPMNEdge (obrigatório 1 por flow)"))
        elif len(edges) > 1:
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "2c",
                                  f"{len(edges)} BPMNEdge para o mesmo flow"))
        for edge in edges:
            if len(waypoints(edge)) < 2:
                achados.append(Achado(caminho.name, f"{tag}@{eid}", "2c",
                                      "BPMNEdge com menos de 2 waypoints"))
    # flow sem id não é referenciável em DI — achado fail-closed.
    for el in ctx.raiz.iter():
        tag = etree.QName(el).localname
        if tag in FLOW_TAGS and not el.get("id"):
            achados.append(Achado(caminho.name, tag, "2c",
                                  "flow sem id: impossível referenciar em DI"))
    return (total_sem, total_di, len(faltando), dif_b)


# ---------------------------------------------------------------------------
# Checks 3a..3h — geometria
# ---------------------------------------------------------------------------

def _pares_shapes(ctx: Contexto) -> Iterator[tuple[str, str]]:
    """Todos os pares de ids com BPMNShape (para 3a)."""
    ids = sorted(ctx.shapes.keys())
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            yield a, b


def check_geometria(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Checks 3a..3h — geometria do DI (§7)."""
    _check_3a_sobreposicao(caminho, ctx, achados)
    _check_3b_contencao(caminho, ctx, achados)
    _check_3c_3d_waypoints(caminho, ctx, achados)
    _check_3e_3f_inteiros_grade(caminho, ctx, achados)
    _check_3g_tamanhos(caminho, ctx, achados)
    _check_3h_flags(caminho, ctx, achados)


def _check_3a_sobreposicao(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 3a: zero sobreposição de shapes (interseção >1px² entre pares
    não-containment); labels com dc:Bounds não contam, mas não podem cobrir
    o centro de outra shape."""
    rects: dict[str, tuple[Retangulo, str]] = {}
    for eid, shapes in ctx.shapes.items():
        r = parse_bounds(shapes[0])
        if r is not None:
            rects[eid] = (r, ctx.tag_de(eid) or "?")
    ids = sorted(rects.keys())
    for i, a in enumerate(ids):
        ra, tag_a = rects[a]
        for b in ids[i + 1:]:
            rb, tag_b = rects[b]
            # pares containment (pool/lane/subProcess x filhos) são isentos
            if ra.contem(rb) or rb.contem(ra):
                continue
            # boundary sobre a atividade hospedeira é isento por design
            if _eh_par_boundary_host(ctx, a, b) or _eh_par_boundary_host(ctx, b, a):
                continue
            area = ra.area_intersecao(rb)
            if area > TOL_SOBREPOSICAO_PX2:
                achados.append(Achado(
                    caminho.name, f"{tag_a}@{a} x {tag_b}@{b}", "3a",
                    f"sobreposição de shapes (área {area:.0f}px²)"))
    # labels não podem cobrir o centro de outra shape
    for lab in ctx.labels:
        lr = parse_bounds(lab)
        if lr is None:
            continue
        for eid, (r, tag) in rects.items():
            if lr.contem_ponto(r.centro()):
                achados.append(Achado(caminho.name, f"BPMNLabel x {tag}@{eid}", "3a",
                                      "label cobre o centro de outra shape"))


def _eh_par_boundary_host(ctx: Contexto, bid: str, hid: str) -> bool:
    """True se bid é boundaryEvent com attachedToRef == hid."""
    item = ctx.sem.get(bid)
    if not item or item[0] != "boundaryEvent":
        return False
    return (item[1].get("attachedToRef") or "") == hid


def _check_3b_contencao(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 3b: todo flowNode contido na lane/pool a que pertence (shape
    inteira dentro dos bounds do pai); boundary events são exceção: o centro
    deve estar SOBRE a borda do host (attachedToRef), distância <=2px."""
    tem_lanes = bool(ctx.lanes)
    for eid, (tag, el) in sorted(ctx.sem.items()):
        if tag not in FLOWNODE_TAGS:
            continue
        r = ctx.shape_rect(eid)
        if r is None:
            continue  # já reportado em 2a/2c
        if tag == "boundaryEvent":
            host_id = el.get("attachedToRef") or ""
            host = ctx.shape_rect(host_id)
            if host is None:
                achados.append(Achado(caminho.name, f"boundaryEvent@{eid}", "3b",
                                      f"host attachedToRef={host_id or '?'} sem shape"))
                continue
            d = host.dist_borda(r.centro())
            if d > TOL_BORDA_BOUNDARY:
                achados.append(Achado(
                    caminho.name, f"boundaryEvent@{eid}", "3b",
                    f"centro dista {d:.0f}px da borda do host {host_id} (tol {TOL_BORDA_BOUNDARY:.0f}px)"))
            continue
        # contenção no pool (participant do mesmo processo)
        pid = ctx.processo_de.get(eid, "")
        for part_id in ctx.proc_participantes.get(pid, []):
            pr = ctx.shape_rect(part_id)
            if pr is not None and not pr.contem(r):
                achados.append(Achado(caminho.name, f"{tag}@{eid}", "3b",
                                      f"shape fora dos bounds do participant {part_id}"))
        # contenção na lane declarada via flowNodeRef
        lane_id = ctx.lane_de.get(eid)
        if tem_lanes and lane_id is None:
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "3b",
                                  "flowNode sem flowNodeRef em lane alguma"))
            continue
        if lane_id is not None:
            lr = ctx.shape_rect(lane_id)
            if lr is not None and not lr.contem(r):
                achados.append(Achado(caminho.name, f"{tag}@{eid}", "3b",
                                      f"shape fora dos bounds da lane {lane_id}"))


def _check_3c_3d_waypoints(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 3c: primeiro/último waypoint na BORDA da shape de origem/destino
    (<=5px, não dentro >5px). Check 3d: segmentos ortogonais (dx==0 ou dy==0,
    tolerância 0)."""
    for eid, (tag, el) in sorted(ctx.sem.items()):
        if tag not in FLOW_TAGS:
            continue
        for edge in ctx.edges.get(eid, []):
            pts = waypoints(edge)
            srcs, tgts = ctx.refs_fluxo(el)
            if pts:
                _check_ponta(caminho, ctx, achados, f"{tag}@{eid}", pts[0], srcs, "origem")
                _check_ponta(caminho, ctx, achados, f"{tag}@{eid}", pts[-1], tgts, "destino")
            for i in range(len(pts) - 1):
                dx = abs(pts[i + 1][0] - pts[i][0])
                dy = abs(pts[i + 1][1] - pts[i][1])
                if dx != 0 and dy != 0:
                    achados.append(Achado(
                        caminho.name, f"{tag}@{eid}", "3d",
                        f"segmento diagonal entre {pts[i]} e {pts[i + 1]}"))


def _check_ponta(caminho: Path, ctx: Contexto, achados: list[Achado], rotulo: str,
                 ponto: tuple[float, float], refs: list[str], ponta: str) -> None:
    """Um extremo do edge deve estar na borda de UMA das shapes referenciadas."""
    candidatas = [r for r in (ctx.shape_rect(x) for x in refs if x) if r is not None]
    if not candidatas:
        return  # shape ausente já virou achado em 2b/2c
    melhor = min(r.dist_borda(ponto) for r in candidatas)
    if melhor > TOL_BORDA_WAYPOINT:
        achados.append(Achado(caminho.name, rotulo, "3c",
                              f"waypoint de {ponta} {ponto} dista {melhor:.0f}px "
                              f"da borda (tol {TOL_BORDA_WAYPOINT:.0f}px)"))


def _check_3e_3f_inteiros_grade(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 3e: coordenadas inteiras, não negativas. Check 3f: grade 10
    (x,y,w,h múltiplos de 10) — warning, não fail."""
    for eid, shapes in sorted(ctx.shapes.items()):
        tag = ctx.tag_de(eid) or "?"
        for sh in shapes:
            b = sh.find(f"{DC}Bounds")
            if b is None:
                achados.append(Achado(caminho.name, f"{tag}@{eid}", "3e",
                                      "BPMNShape sem dc:Bounds"))
                continue
            for attr in ("x", "y", "width", "height"):
                v = b.get(attr)
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    achados.append(Achado(caminho.name, f"{tag}@{eid}", "3e",
                                          f"Bounds.{attr}={v!r} não numérico"))
                    continue
                if f != int(f) or f < 0:
                    achados.append(Achado(caminho.name, f"{tag}@{eid}", "3e",
                                          f"Bounds.{attr}={v} não é inteiro não negativo"))
            vals = {a: b.get(a) for a in ("x", "y", "width", "height")}
            fora = [f"{a}={v}" for a, v in vals.items()
                    if v is not None and _f(v) is not None and _f(v) % 10 != 0]
            if fora:
                achados.append(Achado(caminho.name, f"{tag}@{eid}", "3f",
                                      "fora da grade 10: " + ", ".join(fora), "warn"))
    for eid, edges in sorted(ctx.edges.items()):
        tag = ctx.tag_de(eid) or "?"
        for edge in edges:
            for i, (x, y) in enumerate(waypoints(edge)):
                for nome, v in (("x", x), ("y", y)):
                    if v != int(v) or v < 0:
                        achados.append(Achado(
                            caminho.name, f"{tag}@{eid}", "3e",
                            f"waypoint[{i}].{nome}={v} não é inteiro não negativo"))


def _f(txt: str | None) -> float | None:
    try:
        return float(txt)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _check_3g_tamanhos(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 3g: tamanhos canônicos (task 100x80, evento 36x36, gateway 50x50,
    dataObjectReference 36x50, dataStoreReference 50x50, subProcess >=100x80);
    pool/lane com header 30 (warning se divergente)."""
    for eid, shapes in sorted(ctx.shapes.items()):
        tag = ctx.tag_de(eid)
        r = parse_bounds(shapes[0])
        if r is None:
            continue
        if tag in TAM_EXATO:
            w, h = TAM_EXATO[tag]
            if r.w != w or r.h != h:
                achados.append(Achado(caminho.name, f"{tag}@{eid}", "3g",
                                      f"tamanho {r.w:.0f}x{r.h:.0f} diverge do canônico {w:.0f}x{h:.0f}"))
        elif tag in TAM_MINIMO:
            w, h = TAM_MINIMO[tag]
            if r.w < w or r.h < h:
                achados.append(Achado(caminho.name, f"{tag}@{eid}", "3g",
                                      f"tamanho {r.w:.0f}x{r.h:.0f} abaixo do mínimo {w:.0f}x{h:.0f}"))
    # header 30: lane.x - participant.x == 30 (e participant -> 1a lane)
    for eid, shapes in sorted(ctx.shapes.items()):
        tag = ctx.tag_de(eid)
        if tag not in ("participant", "lane"):
            continue
        pr = parse_bounds(shapes[0])
        if pr is None:
            continue
        if tag == "participant":
            pid = ctx.processo_de.get(eid, "")
        else:
            # lane pertence ao processo em que está declarada
            pid = _processo_da_lane(ctx, eid)
        partes = ctx.proc_participantes.get(pid, [])
        if not partes:
            continue
        base = ctx.shape_rect(partes[0])
        if base is None:
            continue
        offset = pr.x - base.x
        if offset != 30:
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "3g",
                                  f"header {offset:.0f}px (esperado 30px)", "warn"))


def _processo_da_lane(ctx: Contexto, lane_id: str) -> str:
    """Processo que contém a lane declarada."""
    for proc in ctx.raiz.iter(f"{B}process"):
        for lane in proc.iter(f"{B}lane"):
            if lane.get("id") == lane_id:
                return proc.get("id") or ""
    return ""


def _check_3h_flags(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 3h: participant/lane isHorizontal=true; gateway XOR
    isMarkerVisible=true; subProcess isExpanded explícito.
    Os três são atributos do BPMNShape (BPMNDI.xsd pinado)."""
    for eid, shapes in sorted(ctx.shapes.items()):
        tag = ctx.tag_de(eid) or "?"
        sh = shapes[0]
        if tag in ("participant", "lane") and sh.get("isHorizontal") != "true":
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "3h",
                                  f"isHorizontal={sh.get('isHorizontal')!r} (exigido true)"))
        if tag == "exclusiveGateway" and sh.get("isMarkerVisible") != "true":
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "3h",
                                  f"isMarkerVisible={sh.get('isMarkerVisible')!r} (exigido true)"))
        if tag == "subProcess" and sh.get("isExpanded") is None:
            achados.append(Achado(caminho.name, f"{tag}@{eid}", "3h",
                                  "isExpanded ausente (exigido explícito)"))


# ---------------------------------------------------------------------------
# Checks 4a..4d — regras de domínio (hard fail)
# ---------------------------------------------------------------------------

def check_dominio(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Checks 4a..4d — regras de domínio da missão §9 (hard fail)."""
    _check_4a_custodia(caminho, ctx, achados)
    _check_4b_timers(caminho, ctx, achados)
    _check_4c_portoes(caminho, ctx, achados)
    _check_4d_messageflow(caminho, ctx, achados)


def _lane_proibida(ctx: Contexto, eid: str) -> str | None:
    """Nome (normalizado) da lane proibida em que o elemento reside, se houver."""
    lane_id = ctx.lane_de.get(eid)
    if lane_id is None:
        return None
    nome = norm(ctx.lanes.get(lane_id, ("", []))[0])
    for termo in LANES_PROIBIDAS_4A:
        if termo in nome:
            return nome
    return None


def _check_4a_custodia(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 4a: NENHUMA association/dataAssociation/messageFlow ligando
    dataStoreReference de custódia clínica (id prefixo DataStore_ ou nome com
    'clinica'/'custodia') a task/gateway/event em lane comercial/medição/
    mensagens/recuperação/retenção."""
    clinicos = {eid for eid, (tag, el) in ctx.sem.items()
                if tag in ("dataStore", "dataStoreReference")
                and eh_portao_clinico(tag, el)}
    for eid, (tag, el) in sorted(ctx.sem.items()):
        if tag not in FLOW_TAGS:
            continue
        srcs, tgts = ctx.refs_fluxo(el)
        pares = [(s, t) for s in srcs for t in tgts]
        for a, b in pares:
            for origem, destino in ((a, b), (b, a)):
                if origem not in clinicos:
                    continue
                item = ctx.sem.get(destino)
                if not item or item[0] not in FLOWNODE_TAGS:
                    continue
                proibida = _lane_proibida(ctx, destino)
                if proibida:
                    achados.append(Achado(
                        caminho.name, f"{tag}@{eid}", "4a",
                        f"dataStore clínico {origem} ligado a {item[0]}@{destino} "
                        f"em lane '{proibida}'"))


def _check_4b_timers(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 4b: nenhum timerEventDefinition com timeDuration cujo texto não
    seja ISO 8601 (^-?P([0-9]+[YMDWH])+$) nem expressão ${...}; literal tipo
    '25 dias' = achado."""
    for timer in ctx.raiz.iter(f"{B}timerEventDefinition"):
        rot = _rotulo_ancestral(ctx, timer)
        dur = timer.find(f"{B}timeDuration")
        if timer.find(f"{B}timeDate") is not None or timer.find(f"{B}timeCycle") is not None:
            continue  # missão restringe timeDuration; outras formas fora do escopo
        if dur is None:
            achados.append(Achado(caminho.name, rot, "4b",
                                  "timerEventDefinition sem timeDuration"))
            continue
        txt = " ".join(dur.itertext()).strip()
        if not (REGEX_ISO.match(txt) or REGEX_EXPR.match(txt)):
            achados.append(Achado(caminho.name, rot, "4b",
                                  f"timeDuration {txt!r} não é ISO 8601 nem ${{...}}"))


def _rotulo_ancestral(ctx: Contexto, el: etree._Element) -> str:
    """Rótulo 'tag@id' do elemento ou do ancestral com id (p/ timers)."""
    alvo: etree._Element | None = el
    while alvo is not None:
        eid = alvo.get("id")
        if eid:
            tag = etree.QName(alvo).localname
            return f"{tag}@{eid}"
        alvo = alvo.getparent()
    return "timerEventDefinition"


def _check_4c_portoes(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 4c: todo gateway XOR cujo id contém 'Gate' e cuja documentation
    menciona portão (RL-SF-n|An|OS-n) deve ter exatamente 1 default flow e
    1 textAnnotation associada contendo o id do portão."""
    for eid, (tag, el) in sorted(ctx.sem.items()):
        if tag != "exclusiveGateway" or "Gate" not in eid:
            continue
        doc = " ".join(" ".join(d.itertext()) for d in el.findall(f"{B}documentation"))
        portoes = REGEX_PORTAO.findall(doc)
        if not portoes:
            continue
        portao = portoes[0]
        # default flow: atributo default aponta para sequenceFlow saindo do gateway
        default_id = el.get("default") or ""
        saidas = {f.get("id") for f in ctx.raiz.iter(f"{B}sequenceFlow")
                  if (f.get("sourceRef") or "") == eid}
        if not default_id:
            achados.append(Achado(caminho.name, f"exclusiveGateway@{eid}", "4c",
                                  f"portão {portao} sem default flow"))
        elif default_id not in saidas:
            achados.append(Achado(caminho.name, f"exclusiveGateway@{eid}", "4c",
                                  f"default={default_id} não é sequenceFlow de saída"))
        # annotation associada citando o portão
        achou = False
        for aid, (atag, ael) in ctx.sem.items():
            if atag != "association":
                continue
            srcs, tgts = ctx.refs_fluxo(ael)
            if eid not in srcs and eid not in tgts:
                continue
            outro = tgts[0] if eid in srcs else srcs[0]
            item = ctx.sem.get(outro)
            if item and item[0] == "textAnnotation":
                txt = norm(" ".join(item[1].itertext()))
                if norm(portao) in txt:
                    achou = True
                    break
        if not achou:
            achados.append(Achado(caminho.name, f"exclusiveGateway@{eid}", "4c",
                                  f"sem textAnnotation associada citando o portão {portao}"))


def _check_4d_messageflow(caminho: Path, ctx: Contexto, achados: list[Achado]) -> None:
    """Check 4d: messageFlow só entre elementos de participants diferentes e
    vertical (|dx| < |dy| no segmento de travessia do limite)."""
    for eid, (tag, el) in sorted(ctx.sem.items()):
        if tag != "messageFlow":
            continue
        srcs, tgts = ctx.refs_fluxo(el)
        if not srcs or not tgts:
            continue
        p_src = _participant_de(ctx, srcs[0])
        p_tgt = _participant_de(ctx, tgts[0])
        if p_src is None or p_tgt is None:
            achados.append(Achado(caminho.name, f"messageFlow@{eid}", "4d",
                                  "participante de origem/destino não determinável"))
            continue
        if p_src == p_tgt:
            achados.append(Achado(caminho.name, f"messageFlow@{eid}", "4d",
                                  f"origem e destino no mesmo participant {p_src}"))
        edges = ctx.edges.get(eid, [])
        if not edges:
            continue  # 2c já reporta
        pts = waypoints(edges[0])
        if len(pts) < 2:
            continue
        travou = False
        for i in range(len(pts) - 1):
            dentro_a = _ponto_em_participant(ctx, pts[i])
            dentro_b = _ponto_em_participant(ctx, pts[i + 1])
            if dentro_a != dentro_b:
                travou = True
                dx = abs(pts[i + 1][0] - pts[i][0])
                dy = abs(pts[i + 1][1] - pts[i][1])
                if dx >= dy:
                    achados.append(Achado(
                        caminho.name, f"messageFlow@{eid}", "4d",
                        f"travessia não vertical no segmento {i} (|dx|={dx:.0f} >= |dy|={dy:.0f})"))
        if not travou:
            dx = abs(pts[-1][0] - pts[0][0])
            dy = abs(pts[-1][1] - pts[0][1])
            if dx >= dy:
                achados.append(Achado(caminho.name, f"messageFlow@{eid}", "4d",
                                      f"sem travessia vertical (|dx|={dx:.0f} >= |dy|={dy:.0f})"))


def _participant_de(ctx: Contexto, eid: str) -> str | None:
    """Participant de um elemento: o próprio participant, ou o participante
    do processo ancestral."""
    if ctx.tag_de(eid) == "participant":
        return eid
    pid = ctx.processo_de.get(eid, "")
    partes = ctx.proc_participantes.get(pid, [])
    return partes[0] if partes else None


def _ponto_em_participant(ctx: Contexto, p: tuple[float, float]) -> str | None:
    """Id do participant cuja shape contém o ponto (primeiro encontrado)."""
    for pid in sorted(k for k in ctx.sem if ctx.tag_de(k) == "participant"):
        r = ctx.shape_rect(pid)
        if r is not None and r.contem_ponto(p):
            return pid
    return None


# ---------------------------------------------------------------------------
# Check 5 — lint
# ---------------------------------------------------------------------------

def check_lint(caminho: Path, tools_dir: Path, skip: bool,
               achados: list[Achado]) -> tuple[str, str]:
    """Check 5: `npx --no-install bpmnlint --config bpmnlintrc-di.json <arquivo>`
    como subprocesso (cwd=tools/). 0 erros = pass; npx/config indisponíveis
    => UNVERIFIED (não PASS, não fail)."""
    if skip:
        return ("unverified", "lint ignorado via --skip-lint")
    npx = shutil.which("npx")
    if npx is None:
        return ("unverified", "npx indisponível")
    cfg = tools_dir / "bpmnlintrc-di.json"
    if not cfg.exists():
        return ("unverified", f"config {cfg} ausente")
    try:
        proc = subprocess.run(
            [npx, "--no-install", "bpmnlint", "--config", str(cfg), str(caminho)],
            cwd=str(tools_dir), capture_output=True, text=True, timeout=180,
            check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ("unverified", f"falha ao executar bpmnlint: {exc}")
    if proc.returncode == 0:
        return ("pass", "")
    detalhe = (proc.stderr or proc.stdout).strip().splitlines()
    resumo = "; ".join(detalhe[-5:]) if detalhe else f"exit {proc.returncode}"
    achados.append(Achado(caminho.name, "bpmnlint", "5", f"erros de lint: {resumo}"))
    return ("fail", resumo)


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------

def processar_arquivo(caminho: Path, esquema: etree.XMLSchema | None,
                      tools_dir: Path, xsd_dir: Path, skip_lint: bool,
                      achados_globais: list[Achado]) -> ResultadoArquivo:
    """Executa todos os checks de um arquivo e consolida o resultado."""
    res = ResultadoArquivo(arquivo=str(caminho))
    achados = res.achados
    try:
        doc = etree.parse(str(caminho))
    except etree.LxmlError as exc:
        achados.append(Achado(caminho.name, "definitions", "1",
                              f"XML mal-formado: {exc}"))
        return res
    res.xsd_valido = check_xsd(caminho, doc, esquema, achados)
    ctx = Contexto(doc.getroot())
    (res.total_semantico_desenhavel, res.total_di_referenciado,
     res.diferenca_a, res.diferenca_b) = check_cobertura(caminho, ctx, achados)
    check_geometria(caminho, ctx, achados)
    check_dominio(caminho, ctx, achados)
    res.lint, res.lint_detalhe = check_lint(caminho, tools_dir, skip_lint, achados)
    achados_globais.extend(achados)
    return res


def relatorio_texto(res: ResultadoArquivo) -> str:
    """Relatório textual por arquivo (stdout)."""
    linhas = [f"=== {res.arquivo} ==="]
    linhas.append(f"[1] XSD: {'OK' if res.xsd_valido else 'FALHA'}")
    linhas.append(
        f"[2] COBERTURA DI: desenháveis={res.total_semantico_desenhavel} | "
        f"referenciados={res.total_di_referenciado} | diferenca_a={res.diferenca_a} | "
        f"diferenca_b={res.diferenca_b}")
    por_check = res.resumo_checks()
    linhas.append(f"[3] GEOMETRIA: achados={sum(v for k, v in por_check.items() if k.startswith('3'))}")
    linhas.append(f"[4] DOMÍNIO:  achados={sum(v for k, v in por_check.items() if k.startswith('4'))}")
    linhas.append(f"[5] LINT: {res.lint}" + (f" ({res.lint_detalhe})" if res.lint_detalhe else ""))
    linhas.append(f"[6] RENDER: {res.render} (harness do orquestrador)")
    if res.achados:
        for a in res.achados:
            linhas.append(f"  [{a.severidade.upper():10s}] check {a.check} — "
                          f"{a.elemento}: {a.detalhe}")
    else:
        linhas.append("  sem achados")
    return "\n".join(linhas)


def json_consolidado(resultados: list[ResultadoArquivo]) -> dict[str, Any]:
    """JSON consolidado (--json): contagens e achados por categoria."""
    tot_sem = sum(r.total_semantico_desenhavel for r in resultados)
    tot_di = sum(r.total_di_referenciado for r in resultados)
    dif_a = sum(r.diferenca_a for r in resultados)
    dif_b = sum(r.diferenca_b for r in resultados)
    sever: dict[str, int] = {}
    checks: dict[str, int] = {}
    for r in resultados:
        for a in r.achados:
            sever[a.severidade] = sever.get(a.severidade, 0) + 1
            checks[a.check] = checks.get(a.check, 0) + 1
    return {
        "resumo": {
            "arquivos": len(resultados),
            "total_semantico_desenhavel": tot_sem,
            "total_di_referenciado": tot_di,
            "diferenca_a": dif_a,
            "diferenca_b": dif_b,
            "achados_por_severidade": sever,
            "achados_por_check": checks,
            "tem_fail": any(r.tem_fail for r in resultados),
        },
        "arquivos": [r.como_dict() for r in resultados],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI do verificador. Exit 0 sem fails; 1 com fails; 2 uso/erro interno."""
    ap = argparse.ArgumentParser(
        description="Verificador de DI para BPMN 2.0 (missão funil — checks §7/§9).")
    ap.add_argument("arquivos", nargs="*", help="arquivos .bpmn a verificar")
    ap.add_argument("--dir", help="processa todos os *.bpmn do diretório")
    ap.add_argument("--json", dest="json_out", help="caminho do relatório JSON consolidado")
    ap.add_argument("--xsd-dir", help="diretório dos XSDs (padrão: <tools>/xsd)")
    ap.add_argument("--tools-dir", help="diretório tools (padrão: o deste script)")
    ap.add_argument("--skip-lint", action="store_true", help="não roda bpmnlint")
    ap.add_argument("--fail-on-warn", action="store_true",
                    help="warnings também causam exit 1")
    args = ap.parse_args(argv)

    tools_dir = Path(args.tools_dir).resolve() if args.tools_dir \
        else Path(__file__).resolve().parent
    xsd_dir = Path(args.xsd_dir).resolve() if args.xsd_dir \
        else tools_dir / "xsd"

    alvos: list[Path] = [Path(a) for a in args.arquivos]
    if args.dir:
        alvos.extend(sorted(Path(args.dir).glob("*.bpmn")))
    if not alvos:
        ap.error("informe arquivos .bpmn ou --dir <path>")
        return 2

    achados_globais: list[Achado] = []
    esquema = carregar_esquema(xsd_dir, achados_globais)
    if esquema is None:
        print("ERRO INTERNO: esquema XSD pinado não confiável — abortando.",
              file=sys.stderr)
        for a in achados_globais:
            print(f"  {a.elemento}: {a.detalhe}", file=sys.stderr)
        return 2

    resultados: list[ResultadoArquivo] = []
    for alvo in alvos:
        if not alvo.exists():
            print(f"AVISO: {alvo} não existe — ignorado.", file=sys.stderr)
            continue
        resultados.append(processar_arquivo(
            alvo, esquema, tools_dir, xsd_dir, args.skip_lint, achados_globais))

    for res in resultados:
        print(relatorio_texto(res))
    consolidado = json_consolidado(resultados)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(consolidado, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"JSON consolidado gravado em {args.json_out}")

    tem_fail = any(r.tem_fail for r in resultados)
    if args.fail_on_warn:
        tem_fail = tem_fail or any(
            a.severidade == "warn" for r in resultados for a in r.achados)
    return 1 if tem_fail else 0


if __name__ == "__main__":
    sys.exit(main())
