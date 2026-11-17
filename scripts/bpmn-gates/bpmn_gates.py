"""Harness dos gates 1-5 de BPMN (EP-PROC-04 / E-SF-31) — um comando, uma baseline, saida != 0 no defeito.

ORIGEM. DEC-PROC PROC-04, opcao A: "A CI do maezo roda os gates 1-5 com toolchain pinada (lockfile)".
Os criterios de aceite sao os do registro: G1 XSD 0 erros; G2 auditoria estatica 0 findings (perfil
"sp-op" para `spec/processes/bpmn/` e "corpus-doc" para `docs/processes/New_BPMN/Adapted_BPMN/` — o
corpus CA nao segue as convencoes SP-OP por desenho, D-02); G3 id<->name e correlacao por nome 0
divergencias; G4 bpmnlint 0 errors; G5 import bpmn-js 0 warnings. Versoes vao para o log.

O QUE CADA GATE E' (e de onde vem cada regra — nenhuma regra de negocio nasce aqui):

* G1 ``xsd`` — validacao contra o XSD BPMN 2.0 da OMG vendorizado em ``xsd/`` (URL e sha256 em
  ``xsd/PROVENANCE.json``; o sha e' conferido ANTES de usar: XSD adulterado = erro de ferramenta).
* G2 ``static`` — auditoria estatica:
    - G2-HOUSE (so' sp-op): o validador da casa, REUSADO sem copia
      (``maezo.platform.validation.bpmn.validate_file``: cobertura DI por plano de processo,
      ``camunda:decisionRef``, ENGINE-09005). Nao roda no corpus-doc: planos de collaboration
      estao fora do escopo declarado do ``validate_di`` (docstring dele).
    - G2-REF: toda referencia por id resolve (messageRef/signalRef/errorRef/escalationRef para a
      declaracao do tipo certo; sourceRef/targetRef de sequenceFlow no MESMO escopo; attachedToRef
      para atividade; ``default`` para um fluxo de saida do proprio elemento).
    - G2-IMPL (os dois perfis): serviceTask/sendTask/businessRuleTask de processo executavel com
      binding camunda (class/delegateExpression/expression/type; decisionRef em businessRuleTask)
      — regra de parse do cibseven 2.1.0, ENGINE-09005.
    - G2-INOUT: ``<incoming>``/``<outgoing>`` declarados batem com os fluxos reais (classe
      "ghost declaration", a mesma que ``tests/processes/test_ca2_v4_regression_f_e01.py`` fixa).
    - G2-3B: regra 3b — boundary interruptivo e o seu host nao alcancam o mesmo AND-join sem um
      exclusiveGateway de escape (algoritmo ADAPTADO do teste F-E01 citado acima).
    - G2-CONV (so' sp-op): convencoes da casa para SP-OP (brief do artesao BPMN, regra 2): nome
      ``SP-OP-<DOMINIO>-NNN_<Nome>.bpmn``; 1 processo por arquivo; sem lanes; namespaces prefixados
      (sem namespace default BPMN). ``isExecutable`` NAO entra: processo desligado e' decisao
      registrada (ex. ANS-CRON, R-007/DEC-03), nao defeito de forma.
* G3 ``contract`` — conjunto inteiro do perfil (MAPA-ELEMENTOS-V4 §7.D/§8, "Gate-3 set-level"):
    - G3-DUPID: id duplicado entre arquivos (corpus-doc: processos e flow nodes; sp-op: processos —
      ids de flow node se repetem entre processos SP-OP por convencao, ex. ``TD_AlertaSla``).
    - G3-IDNAME: cada id de message/signal/error/escalation tem UM nome (e UM errorCode) no
      conjunto; no corpus-doc, tambem cada nome tem UM id. Message/signal sem nome e' divergencia.
    - G3-CORR (corpus-doc): pareamento por NOME de signal e error (MAPA §7.D "[PAREADO]/[SEM-PAR]"):
      todo signal/error capturado tem emissor no corpus. Message NAO entra: varias chegam de
      participante externo (cliente, prestador) sem emissor no XML por desenho.
    - G3-CONTRACT (sp-op): todo nome de mensagem que um SP-OP correlaciona aparece no contrato
      ``docs/processes/contracts/<SP-OP-...>.md`` (regra dura 1 do AGENTS.md: rastreavel ao contrato).
* G4 ``lint`` — ``bpmnlint`` com ``.bpmnlintrc`` da raiz (``gate4_lint.mjs``); so' categoria ``error``
  e' finding (criterio "0 errors"); warnings sao contados no log.
* G5 ``import`` — ``gate5_import.mjs``: ``bpmn-moddle`` + ``camunda-bpmn-moddle`` e o
  ``BpmnTreeWalker`` do ``bpmn-js``; todo warning e' finding (criterio "0 warnings").

BASELINE (``baseline.json``). Achado PRE-EXISTENTE no head, que este harness nao pode corrigir (nao
toca ``.bpmn``), fica numa lista explicita e versionada. A baseline SO' ENCOLHE: achado novo fora
dela reprova; entrada da baseline que nao reproduz mais tambem reprova ("remova a entrada") — assim
a lista nao vira cemiterio. ``tests/processes/test_bpmn_gates_harness.py`` fixa o tamanho EXATO.
Arquivo tocado pelo PR (``--touched``) nao e' isento: os achados dele contam como novos.

SAIDA. 0 = sem finding novo e baseline exata; 1 = finding novo ou entrada obsoleta; 2 = erro de uso
ou de ferramenta (XSD adulterado, node ausente, saida do node ilegivel) — nunca "passou".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from lxml import etree

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
XSD_DIR = HERE / "xsd"
XSD_PROVENANCE = XSD_DIR / "PROVENANCE.json"
DEFAULT_BASELINE = HERE / "baseline.json"
BPMNLINTRC = REPO_ROOT / ".bpmnlintrc"
CONTRACTS_DIR = REPO_ROOT / "docs" / "processes" / "contracts"

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"

PROFILES: dict[str, Path] = {
    "sp-op": REPO_ROOT / "spec" / "processes" / "bpmn",
    "corpus-doc": REPO_ROOT / "docs" / "processes" / "New_BPMN" / "Adapted_BPMN",
}
GATES = ("1", "2", "3", "4", "5")
GATE_NAMES = {"1": "xsd", "2": "static", "3": "contract", "4": "lint", "5": "import"}

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL = 2

_SPOP_FILENAME = re.compile(r"^(SP-OP-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3})_[A-Za-z0-9_]+\.bpmn$")
_FLOW_NODE_TAGS = frozenset(
    {
        "startEvent",
        "endEvent",
        "intermediateCatchEvent",
        "intermediateThrowEvent",
        "boundaryEvent",
        "task",
        "serviceTask",
        "userTask",
        "businessRuleTask",
        "manualTask",
        "scriptTask",
        "sendTask",
        "receiveTask",
        "callActivity",
        "subProcess",
        "transaction",
        "exclusiveGateway",
        "inclusiveGateway",
        "parallelGateway",
        "complexGateway",
        "eventBasedGateway",
    }
)
_ACTIVITY_TAGS = frozenset(
    {
        "task",
        "serviceTask",
        "userTask",
        "businessRuleTask",
        "manualTask",
        "scriptTask",
        "sendTask",
        "receiveTask",
        "callActivity",
        "subProcess",
        "transaction",
    }
)
_REF_KINDS = {
    "messageRef": "message",
    "signalRef": "signal",
    "errorRef": "error",
    "escalationRef": "escalation",
}
_CATCH_TAGS = frozenset({"startEvent", "intermediateCatchEvent", "boundaryEvent"})
_THROW_TAGS = frozenset({"intermediateThrowEvent", "endEvent"})


class ToolError(RuntimeError):
    """Ferramenta indisponivel ou adulterada — o gate NAO pode dizer "passou"."""


@dataclass(frozen=True, slots=True)
class Finding:
    gate: str
    profile: str
    file: str
    code: str
    element: str
    message: str

    def key(self) -> tuple[str, str, str, str, str, str]:
        return (self.gate, self.profile, self.file, self.code, self.element, self.message)

    def render(self) -> str:
        return f"[G{self.gate} {self.code}] {self.file} :: {self.element} :: {self.message}"


def _q(tag: str) -> str:
    return f"{{{BPMN_NS}}}{tag}"


def _local(tag: object) -> str:
    text = str(tag)
    return text.split("}", 1)[1] if "}" in text else text


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _parse(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=False)
    return etree.parse(str(path), parser)


# --------------------------------------------------------------------------------------------
# G1 — XSD
# --------------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_xsd() -> tuple[etree.XMLSchema, dict[str, object]]:
    """Carrega o XSD OMG vendorizado DEPOIS de conferir o sha256 de cada arquivo."""
    try:
        provenance = json.loads(XSD_PROVENANCE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"G1: nao li {_rel(XSD_PROVENANCE)}: {exc}") from exc
    files = provenance.get("files", {})
    if not files:
        raise ToolError("G1: PROVENANCE.json sem 'files'")
    for name, meta in files.items():
        actual = _sha256(XSD_DIR / name)
        if actual != meta["sha256"]:
            raise ToolError(f"G1: {name} sha256 {actual} != registrado {meta['sha256']} — XSD adulterado")
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    schema_doc = etree.parse(str(XSD_DIR / provenance["entry"]), parser)
    return etree.XMLSchema(schema_doc), provenance


def gate1(paths: Sequence[Path], profile: str) -> list[Finding]:
    schema, _ = load_xsd()
    findings: list[Finding] = []
    for path in paths:
        try:
            doc = _parse(path)
        except etree.XMLSyntaxError as exc:
            findings.append(Finding("1", profile, _rel(path), "G1-XML", "<parse>", str(exc)))
            continue
        if schema.validate(doc):
            continue
        # lxml-stubs nao declara _ErrorLog iteravel; em runtime ele e' (lista de _LogEntry).
        for err in cast(Iterable[Any], schema.error_log):
            findings.append(Finding("1", profile, _rel(path), "G1-XSD", f"line {err.line}", err.message))
    return findings


# --------------------------------------------------------------------------------------------
# G2 — auditoria estatica
# --------------------------------------------------------------------------------------------


def _declarations(root: etree._Element) -> dict[str, dict[str, etree._Element]]:
    out: dict[str, dict[str, etree._Element]] = {k: {} for k in _REF_KINDS.values()}
    for child in root:
        kind = _local(child.tag)
        if kind in out:
            out[kind][child.get("id", "")] = child
    return out


def _scopes(root: etree._Element) -> Iterable[etree._Element]:
    yield from root.iter(_q("process"), _q("subProcess"), _q("transaction"))


def _flows_in(scope: etree._Element) -> list[etree._Element]:
    return [c for c in scope if c.tag == _q("sequenceFlow")]


def _nodes_in(scope: etree._Element) -> dict[str, etree._Element]:
    return {c.get("id", ""): c for c in scope if _local(c.tag) in _FLOW_NODE_TAGS}


def _check_refs(root: etree._Element, rel: str, profile: str) -> list[Finding]:
    found: list[Finding] = []
    decls = _declarations(root)
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        for attr, kind in _REF_KINDS.items():
            ref = element.get(attr)
            if ref is None:
                continue
            if ref not in decls[kind]:
                owner = element.getparent()
                eid = (owner.get("id") if owner is not None else None) or element.get("id") or "<sem-id>"
                found.append(
                    Finding("2", profile, rel, "G2-REF", eid, f"{attr}='{ref}' nao resolve para bpmn:{kind}")
                )
    for scope in _scopes(root):
        nodes = _nodes_in(scope)
        flows = _flows_in(scope)
        outgoing: dict[str, set[str]] = defaultdict(set)
        for flow in flows:
            fid = flow.get("id", "")
            outgoing[flow.get("sourceRef", "")].add(fid)
            for side in ("sourceRef", "targetRef"):
                ref = flow.get(side, "")
                if ref not in nodes:
                    found.append(
                        Finding(
                            "2",
                            profile,
                            rel,
                            "G2-REF",
                            fid,
                            f"{side}='{ref}' nao e' flow node do escopo '{scope.get('id', '')}'",
                        )
                    )
        for nid, node in nodes.items():
            if _local(node.tag) == "boundaryEvent":
                host = node.get("attachedToRef", "")
                if host not in nodes or _local(nodes[host].tag) not in _ACTIVITY_TAGS:
                    found.append(
                        Finding(
                            "2",
                            profile,
                            rel,
                            "G2-REF",
                            nid,
                            f"attachedToRef='{host}' nao e' atividade do escopo",
                        )
                    )
            default = node.get("default")
            if default is not None and default not in outgoing.get(nid, set()):
                found.append(
                    Finding(
                        "2",
                        profile,
                        rel,
                        "G2-REF",
                        nid,
                        f"default='{default}' nao e' fluxo de saida do elemento",
                    )
                )
    return found


def _check_in_out(root: etree._Element, rel: str, profile: str) -> list[Finding]:
    found: list[Finding] = []
    for scope in _scopes(root):
        inbound: dict[str, set[str]] = defaultdict(set)
        outbound: dict[str, set[str]] = defaultdict(set)
        for flow in _flows_in(scope):
            inbound[flow.get("targetRef", "")].add(flow.get("id", ""))
            outbound[flow.get("sourceRef", "")].add(flow.get("id", ""))
        for nid, node in _nodes_in(scope).items():
            for tag, actual in (("incoming", inbound[nid]), ("outgoing", outbound[nid])):
                declared = {(c.text or "").strip() for c in node.findall(_q(tag))}
                if declared and declared != actual:
                    found.append(
                        Finding(
                            "2",
                            profile,
                            rel,
                            "G2-INOUT",
                            nid,
                            f"<{tag}> declarado {sorted(declared)} != fluxos reais {sorted(actual)}",
                        )
                    )
    return found


def _reachable(adj: dict[str, list[str]], start: str, skip: str = "") -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(t for t in adj.get(node, []) if t != skip)
    return seen


def _check_rule_3b(root: etree._Element, rel: str, profile: str) -> list[Finding]:
    """Adaptado de tests/processes/test_ca2_v4_regression_f_e01.py (varredura (b)), por escopo."""
    found: list[Finding] = []
    for scope in _scopes(root):
        adj: dict[str, list[str]] = defaultdict(list)
        indegree: dict[str, int] = defaultdict(int)
        for flow in _flows_in(scope):
            adj[flow.get("sourceRef", "")].append(flow.get("targetRef", ""))
            indegree[flow.get("targetRef", "")] += 1
        nodes = _nodes_in(scope)
        joins = [nid for nid, n in nodes.items() if _local(n.tag) == "parallelGateway" and indegree[nid] >= 2]
        xors = [nid for nid, n in nodes.items() if _local(n.tag) == "exclusiveGateway"]
        for bid, b in nodes.items():
            if _local(b.tag) != "boundaryEvent" or b.get("cancelActivity", "true").strip().lower() == "false":
                continue
            host = b.get("attachedToRef", "")
            for join in joins:
                if join not in _reachable(adj, bid) or join not in _reachable(adj, host):
                    continue
                escape = [
                    x
                    for x in xors
                    if join not in _reachable(adj, bid, skip=x) and join not in _reachable(adj, host, skip=x)
                ]
                if not escape:
                    found.append(
                        Finding(
                            "2",
                            profile,
                            rel,
                            "G2-3B",
                            bid,
                            f"boundary interruptivo e host '{host}' alcancam o AND-join '{join}' "
                            "sem XOR de escape",
                        )
                    )
    return found


def _check_conventions(root: etree._Element, path: Path, rel: str, profile: str) -> list[Finding]:
    found: list[Finding] = []
    if not _SPOP_FILENAME.match(path.name):
        found.append(
            Finding("2", profile, rel, "G2-CONV", path.name, "nome fora de SP-OP-<DOMINIO>-NNN_<Nome>.bpmn")
        )
    processes = root.findall(_q("process"))
    if len(processes) != 1:
        found.append(
            Finding(
                "2",
                profile,
                rel,
                "G2-CONV",
                "<definitions>",
                f"{len(processes)} processos no arquivo (esperado 1)",
            )
        )
    for lane in root.iter(_q("lane"), _q("laneSet")):
        found.append(
            Finding(
                "2", profile, rel, "G2-CONV", lane.get("id", "<sem-id>"), f"bpmn:{_local(lane.tag)} em SP-OP"
            )
        )
    if root.nsmap.get(None) == BPMN_NS:
        found.append(
            Finding("2", profile, rel, "G2-CONV", "<definitions>", "namespace BPMN default (sem prefixo)")
        )
    return found


def _house_validator(path: Path, rel: str, profile: str) -> list[Finding]:
    """G2-HOUSE: reuso direto do validador da casa (src/maezo/platform/validation/bpmn.py)."""
    try:
        from maezo.platform.validation.bpmn import validate_file
        from maezo.platform.validation.result import Report
    except ImportError as exc:  # pragma: no cover - so' sem o pacote instalado
        raise ToolError(f"G2: validador da casa indisponivel (instale o projeto): {exc}") from exc
    report = Report()
    validate_file(path, report)
    return [Finding("2", profile, rel, "G2-HOUSE", "-", f.message) for f in report.findings]


#: Atributos de binding que o parser do cibseven 2.1.0 aceita numa tarefa de servico (ENGINE-09005).
_IMPL_ATTRS = ("class", "delegateExpression", "expression", "type")
_IMPL_TAGS = ("serviceTask", "sendTask", "businessRuleTask")


def _check_implementation(root: etree._Element, rel: str, profile: str) -> list[Finding]:
    """G2-IMPL (os dois perfis): todo serviceTask/sendTask/businessRuleTask de processo
    isExecutable="true" declara camunda:class, camunda:delegateExpression, camunda:expression,
    camunda:type ou (so' em businessRuleTask) camunda:decisionRef — regra de parse do cibseven
    2.1.0 (ENGINE-09005: "One of the attributes 'class', 'delegateExpression', 'type', or
    'expression' is mandatory on serviceTask"). A mensagem carrega a linha do elemento, a mesma
    que o motor cita, para o teste de engine cruzar a rejeicao com esta baseline."""
    found: list[Finding] = []
    for process in root.findall(_q("process")):
        if (process.get("isExecutable") or "").strip().lower() != "true":
            continue
        for task in process.iter(*(_q(t) for t in _IMPL_TAGS)):
            tag = _local(task.tag)
            attrs = list(_IMPL_ATTRS) + (["decisionRef"] if tag == "businessRuleTask" else [])
            if any((task.get(f"{{{CAMUNDA_NS}}}{a}") or "").strip() for a in attrs):
                continue
            found.append(
                Finding(
                    "2",
                    profile,
                    rel,
                    "G2-IMPL",
                    task.get("id") or "<sem-id>",
                    f"{tag} sem camunda:{'|'.join(attrs)} (ENGINE-09005), linha {task.sourceline}",
                )
            )
    return found


def gate2(paths: Sequence[Path], profile: str) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        rel = _rel(path)
        try:
            root = _parse(path).getroot()
        except etree.XMLSyntaxError as exc:
            findings.append(Finding("2", profile, rel, "G2-XML", "<parse>", str(exc)))
            continue
        if root.tag != _q("definitions"):
            findings.append(
                Finding("2", profile, rel, "G2-XML", "<root>", f"raiz {root.tag} nao e' bpmn:definitions")
            )
            continue
        if profile == "sp-op":
            findings.extend(_house_validator(path, rel, profile))
            findings.extend(_check_conventions(root, path, rel, profile))
        findings.extend(_check_implementation(root, rel, profile))
        findings.extend(_check_refs(root, rel, profile))
        findings.extend(_check_in_out(root, rel, profile))
        findings.extend(_check_rule_3b(root, rel, profile))
    return findings


# --------------------------------------------------------------------------------------------
# G3 — contrato id<->name e correlacao por nome (conjunto inteiro do perfil)
# --------------------------------------------------------------------------------------------


def _event_kind_refs(element: etree._Element) -> list[tuple[str, str]]:
    """(kind, ref) das definicoes de evento de um evento, mais messageRef de send/receiveTask."""
    out: list[tuple[str, str]] = []
    for definition in element:
        tag = _local(definition.tag)
        for attr, kind in _REF_KINDS.items():
            if tag == f"{kind}EventDefinition" and definition.get(attr):
                out.append((kind, definition.get(attr, "")))
    if _local(element.tag) in {"sendTask", "receiveTask"} and element.get("messageRef"):
        out.append(("message", element.get("messageRef", "")))
    return out


def gate3(paths: Sequence[Path], profile: str) -> list[Finding]:
    findings: list[Finding] = []
    roots: list[tuple[Path, str, etree._Element]] = []
    for path in paths:
        rel = _rel(path)
        try:
            roots.append((path, rel, _parse(path).getroot()))
        except etree.XMLSyntaxError as exc:
            findings.append(Finding("3", profile, rel, "G3-XML", "<parse>", str(exc)))

    # G3-DUPID
    owners: dict[str, list[str]] = defaultdict(list)
    for _, rel, root in roots:
        ids: set[str] = {p.get("id", "") for p in root.findall(_q("process"))}
        if profile == "corpus-doc":
            ids |= {e.get("id", "") for e in root.iter() if _local(e.tag) in _FLOW_NODE_TAGS}
        for eid in ids:
            owners[eid].append(rel)
    for eid, files in sorted(owners.items()):
        if len(files) > 1:
            for rel in files:
                findings.append(
                    Finding("3", profile, rel, "G3-DUPID", eid, f"id repetido em {sorted(files)}")
                )

    # G3-IDNAME
    names_of: dict[tuple[str, str], set[str]] = defaultdict(set)
    ids_of: dict[tuple[str, str], set[str]] = defaultdict(set)
    where: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, rel, root in roots:
        for kind_elements in _declarations(root).items():
            kind, elements = kind_elements
            for did, decl in elements.items():
                name = (decl.get("name") or "").strip()
                label = name if kind != "error" else f"{name}|{decl.get('errorCode', '')}"
                if kind in {"message", "signal"} and not name:
                    findings.append(Finding("3", profile, rel, "G3-IDNAME", did, f"bpmn:{kind} sem name"))
                    continue
                names_of[(kind, did)].add(label)
                ids_of[(kind, label)].add(did)
                where[(kind, did)].add(rel)
    for (kind, did), labels in sorted(names_of.items()):
        if len(labels) > 1:
            for rel in sorted(where[(kind, did)]):
                findings.append(
                    Finding(
                        "3",
                        profile,
                        rel,
                        "G3-IDNAME",
                        did,
                        f"bpmn:{kind} com nomes divergentes {sorted(labels)}",
                    )
                )
    # nome -> UM id: so' no corpus-doc, onde a correlacao ENTRE arquivos e' por nome e a mesma
    # declaracao se repete por arquivo (MAPA §7.F). No SP-OP cada arquivo e' um processo isolado e
    # ids sao locais: o mesmo errorCode com ids distintos em dois processos nao correlaciona nada.
    for (kind, label), dids in sorted(ids_of.items()):
        if profile == "corpus-doc" and len(dids) > 1:
            files = sorted({r for d in dids for r in where[(kind, d)]})
            for rel in files:
                findings.append(
                    Finding(
                        "3",
                        profile,
                        rel,
                        "G3-IDNAME",
                        label,
                        f"bpmn:{kind} '{label}' com ids divergentes {sorted(dids)}",
                    )
                )

    if profile == "corpus-doc":
        findings.extend(_correlation_by_name(roots, profile))
    else:
        findings.extend(_contract_messages(roots, profile))
    return findings


def _correlation_by_name(roots: Sequence[tuple[Path, str, etree._Element]], profile: str) -> list[Finding]:
    thrown: set[tuple[str, str]] = set()
    caught: list[tuple[str, str, str, str]] = []  # kind, name, rel, element id
    for _, rel, root in roots:
        decls = _declarations(root)

        def name_of(kind: str, ref: str, decls: dict[str, dict[str, etree._Element]] = decls) -> str:
            decl = decls[kind].get(ref)
            if decl is None:
                return f"<unresolved:{ref}>"
            if kind == "error":
                return decl.get("errorCode") or decl.get("name") or ref
            return decl.get("name") or ref

        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            tag = _local(element.tag)
            for kind, ref in _event_kind_refs(element):
                if kind not in {"signal", "error"}:
                    continue
                name = name_of(kind, ref)
                if tag in _THROW_TAGS or tag == "sendTask":
                    thrown.add((kind, name))
                elif tag in _CATCH_TAGS or tag == "receiveTask":
                    caught.append((kind, name, rel, element.get("id", "")))
    found: list[Finding] = []
    for kind, name, rel, eid in caught:
        if (kind, name) in thrown:
            continue
        found.append(
            Finding(
                "3", profile, rel, "G3-CORR", eid, f"bpmn:{kind} '{name}' capturado sem emissor no corpus"
            )
        )
    return found


def _contract_messages(roots: Sequence[tuple[Path, str, etree._Element]], profile: str) -> list[Finding]:
    found: list[Finding] = []
    for path, rel, root in roots:
        match = _SPOP_FILENAME.match(path.name)
        if match is None:
            found.append(
                Finding(
                    "3", profile, rel, "G3-CONTRACT", path.name, "nome sem chave SP-OP para achar o contrato"
                )
            )
            continue
        contract = CONTRACTS_DIR / f"{match.group(1)}.md"
        if not contract.is_file():
            found.append(
                Finding("3", profile, rel, "G3-CONTRACT", path.name, f"contrato ausente: {_rel(contract)}")
            )
            continue
        text = contract.read_text(encoding="utf-8")
        decls = _declarations(root)
        used = {
            ref
            for e in root.iter()
            if isinstance(e.tag, str)
            for kind, ref in _event_kind_refs(e)
            if kind == "message"
        }
        for ref in sorted(used):
            decl = decls["message"].get(ref)
            name = (decl.get("name") or "") if decl is not None else ""
            if not name:
                continue  # sem nome: ja' e' G3-IDNAME / G2-REF
            if f"`{name}`" not in text and name not in text:
                found.append(
                    Finding(
                        "3",
                        profile,
                        rel,
                        "G3-CONTRACT",
                        ref,
                        f"mensagem '{name}' nao aparece em {_rel(contract)}",
                    )
                )
    return found


# --------------------------------------------------------------------------------------------
# G4 / G5 — node
# --------------------------------------------------------------------------------------------


def _node() -> str:
    node = os.environ.get("NODE") or shutil.which("node")
    if not node:
        raise ToolError("G4/G5: node ausente no PATH — gate UNVERIFIED, nunca 'passou'")
    if not (HERE / "node_modules").is_dir():
        raise ToolError(f"G4/G5: {_rel(HERE / 'node_modules')} ausente — rode `npm ci` em {_rel(HERE)}")
    return node


def _run_node(script: str, args: Sequence[str]) -> list[dict[str, object]]:
    proc = subprocess.run(
        [_node(), str(HERE / script), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ToolError(f"{script} saiu {proc.returncode}: {proc.stderr.strip()[:2000]}")
    try:
        return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    except ValueError as exc:
        raise ToolError(f"{script}: saida ilegivel: {exc}") from exc


def gate4(paths: Sequence[Path], profile: str) -> list[Finding]:
    findings: list[Finding] = []
    warnings = 0
    for row in _run_node("gate4_lint.mjs", [str(BPMNLINTRC), *map(str, paths)]):
        rel = _rel(Path(str(row["file"])))
        reports = row.get("reports", [])
        assert isinstance(reports, list)
        for rep in reports:
            if rep.get("category") == "error":
                findings.append(
                    Finding(
                        "4",
                        profile,
                        rel,
                        f"G4-{rep.get('rule')}",
                        str(rep.get("id")),
                        str(rep.get("message")),
                    )
                )
            else:
                warnings += 1
    print(f"  G4 bpmnlint: warnings (nao bloqueiam, criterio e' '0 errors') = {warnings}")
    return findings


def gate5(paths: Sequence[Path], profile: str) -> list[Finding]:
    findings: list[Finding] = []
    for row in _run_node("gate5_import.mjs", [str(p) for p in paths]):
        rel = _rel(Path(str(row["file"])))
        items = row.get("warnings", [])
        assert isinstance(items, list)
        for w in items:
            findings.append(
                Finding("5", profile, rel, "G5-IMPORT", str(w.get("element")), str(w.get("message")))
            )
    return findings


RUNNERS = {"1": gate1, "2": gate2, "3": gate3, "4": gate4, "5": gate5}


# --------------------------------------------------------------------------------------------
# baseline, versoes, CLI
# --------------------------------------------------------------------------------------------


def load_baseline(path: Path) -> list[Finding]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Finding(e["gate"], e["profile"], e["file"], e["code"], e["element"], e["message"])
        for e in data["entries"]
    ]


def compare(
    findings: Sequence[Finding],
    baseline: Sequence[Finding],
    gates: Sequence[str],
    profile: str,
    touched: frozenset[str] = frozenset(),
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """(novos, conhecidos, obsoletos) — so' entradas da baseline dos gates/perfil que rodaram.

    Arquivo TOCADO pelo PR (``touched``, caminhos relativos ao repo) nao e' isento pela baseline:
    as entradas dele sao ignoradas e os achados dele contam como NOVO (criterio 2 do PROC-04,
    "0 findings nos arquivos tocados")."""
    relevant = {
        b.key(): b for b in baseline if b.gate in gates and b.profile == profile and b.file not in touched
    }
    produced = {f.key(): f for f in findings}
    new = [f for k, f in produced.items() if k not in relevant]
    known = [f for k, f in produced.items() if k in relevant]
    stale = [b for k, b in relevant.items() if k not in produced]
    return new, known, stale


def versions(gates: Sequence[str]) -> list[str]:
    lines = [f"python {platform.python_version()}", f"lxml {'.'.join(map(str, etree.LXML_VERSION))}"]
    if "1" in gates:
        _, prov = load_xsd()
        files = prov.get("files", {})
        assert isinstance(files, dict)
        for name, meta in sorted(files.items()):
            lines.append(f"xsd {name} sha256={meta['sha256']} origem={meta['url']}")
    if {"4", "5"} & set(gates):
        node = _node()
        lines.append(
            "node "
            + subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
        )
        lock = json.loads((HERE / "package-lock.json").read_text(encoding="utf-8"))
        for pkg in ("bpmnlint", "bpmn-moddle", "bpmn-js", "camunda-bpmn-moddle"):
            meta = lock["packages"].get(f"node_modules/{pkg}", {})
            lines.append(f"npm {pkg} {meta.get('version')} integrity={meta.get('integrity')}")
    return lines


def resolve_files(profile: str, files: Sequence[str]) -> list[Path]:
    if files:
        return [Path(f).resolve() for f in files]
    return sorted(PROFILES[profile].glob("*.bpmn"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gates 1-5 de BPMN (EP-PROC-04).")
    parser.add_argument("--gate", choices=[*GATES, "all"], default="all")
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--baseline", type=Path, help="baseline versionada (sem ela, todo finding reprova)")
    parser.add_argument("--json-out", type=Path, help="grava os findings (evidencia) neste arquivo")
    parser.add_argument(
        "--touched",
        type=Path,
        help="caminhos (relativos ao repo, um por linha) tocados pelo PR: sem isencao da baseline",
    )
    parser.add_argument("files", nargs="*", help="arquivos .bpmn (padrao: o diretorio do perfil)")
    args = parser.parse_args(argv)

    gates = list(GATES) if args.gate == "all" else [args.gate]
    paths = resolve_files(args.profile, args.files)
    if not paths:
        print(f"ERRO: nenhum .bpmn para o perfil {args.profile}", file=sys.stderr)
        return EXIT_TOOL
    try:
        print("== versoes ==")
        for line in versions(gates):
            print(f"  {line}")
        findings: list[Finding] = []
        for gate in gates:
            print(f"== G{gate} {GATE_NAMES[gate]} perfil={args.profile} arquivos={len(paths)} ==")
            got = RUNNERS[gate](paths, args.profile)
            print(f"  findings: {len(got)}")
            findings.extend(got)
    except ToolError as exc:
        print(f"ERRO DE FERRAMENTA: {exc}", file=sys.stderr)
        return EXIT_TOOL

    baseline = load_baseline(args.baseline) if args.baseline else []
    touched: frozenset[str] = frozenset()
    if args.touched:
        touched = frozenset(
            line.strip() for line in args.touched.read_text(encoding="utf-8").splitlines() if line.strip()
        )
        print(f"  arquivos tocados (sem isencao da baseline): {len(touched)}")
    new, known, stale = compare(findings, baseline, gates, args.profile, touched)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(
                {
                    "profile": args.profile,
                    "gates": gates,
                    "files": [_rel(p) for p in paths],
                    "new": [asdict(f) for f in new],
                    "baseline_known": [asdict(f) for f in known],
                    "baseline_stale": [asdict(f) for f in stale],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    for f in known:
        print(f"  BASELINE {f.render()}")
    for f in new:
        print(f"  NOVO     {f.render()}")
    for f in stale:
        print(f"  OBSOLETO {f.render()}  (nao reproduz mais: remova a entrada da baseline)")
    print(
        f"== resumo perfil={args.profile}: novos={len(new)} baseline={len(known)} obsoletos={len(stale)} =="
    )
    return EXIT_FINDINGS if new or stale else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
