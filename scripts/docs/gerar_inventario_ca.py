#!/usr/bin/env python3
"""Gera `INVENTARIO-CA.generated.json` contando os 4 `CA_*_v4_adaptado.bpmn` (EP-PROC-03, PROC-02/PROC-03).

Fonte unica: os quatro arquivos `docs/processes/New_BPMN/Adapted_BPMN/CA_*_v4_adaptado.bpmn`. O gerador
SO CONTA: nao classifica elemento algum (CASA/LACUNA/IMPL/DO e julgamento do MAPA-ELEMENTOS-V4.md, nao
deste script). Saida deterministica: ordem de documento do XML, arquivos em ordem lexicografica,
`json.dumps(..., indent=2, ensure_ascii=False)`, sem data, sem rede, stdlib + lxml.

Denominador (mesma convencao do MAPA-ELEMENTOS-V4.md §1): `process` + tasks de todos os tipos (incl.
`callActivity` e a `task` sem tipo) + gateways + events (start/end/catch/throw/boundary), a qualquer
profundidade sob `<process>`. O conteiner `subProcess` fica FORA do denominador (seus conteudos contam
um a um). Um flow node de tipo desconhecido (ex.: `transaction`, `complexGateway` fora da lista) falha
alto em vez de sumir da contagem.

Pertenca lane -> IDs: copiada literalmente de `<bpmn:flowNodeRef>` de cada `<bpmn:lane>`, sem
interpretacao (segunda fonte cega de lanes, PROC-03).

Uso:
    python -m scripts.docs.gerar_inventario_ca            # (re)escreve o JSON
    python -m scripts.docs.gerar_inventario_ca --check    # exit 1 se o JSON versionado divergir
    python -m scripts.docs.gerar_inventario_ca --resumo   # imprime o bloco do MAPA §7 (sem escrever)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parents[2]
DIR_REL = "docs/processes/New_BPMN/Adapted_BPMN"
GLOB_FONTE = "CA_*_v4_adaptado.bpmn"
SAIDA_REL = f"{DIR_REL}/INVENTARIO-CA.generated.json"
GERADOR_REL = "scripts/docs/gerar_inventario_ca.py"

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"
NS = {"bpmn": BPMN_NS, "bpmndi": BPMNDI_NS}

TASKS: tuple[str, ...] = (
    "userTask",
    "serviceTask",
    "sendTask",
    "receiveTask",
    "scriptTask",
    "manualTask",
    "businessRuleTask",
    "callActivity",
    "task",
)
GATEWAYS: tuple[str, ...] = (
    "exclusiveGateway",
    "parallelGateway",
    "inclusiveGateway",
    "eventBasedGateway",
    "complexGateway",
)
EVENTS: tuple[str, ...] = (
    "startEvent",
    "endEvent",
    "intermediateCatchEvent",
    "intermediateThrowEvent",
    "boundaryEvent",
)
# Flow nodes reconhecidos que NAO entram no denominador (conteiner; conteudo conta um a um).
CONTEINERES: tuple[str, ...] = ("subProcess",)

# Agregados "relatados a parte" (fora do denominador): (chave no JSON, namespace, localname).
AGREGADOS: tuple[tuple[str, str, str], ...] = (
    ("sequenceFlow", BPMN_NS, "sequenceFlow"),
    ("subProcess", BPMN_NS, "subProcess"),
    ("textAnnotation", BPMN_NS, "textAnnotation"),
    ("association", BPMN_NS, "association"),
    ("dataObject", BPMN_NS, "dataObject"),
    ("dataObjectReference", BPMN_NS, "dataObjectReference"),
    ("dataStore", BPMN_NS, "dataStore"),
    ("dataStoreReference", BPMN_NS, "dataStoreReference"),
    ("dataInputAssociation", BPMN_NS, "dataInputAssociation"),
    ("dataOutputAssociation", BPMN_NS, "dataOutputAssociation"),
    ("participant", BPMN_NS, "participant"),
    ("messageFlow", BPMN_NS, "messageFlow"),
    ("laneSet", BPMN_NS, "laneSet"),
    ("lane", BPMN_NS, "lane"),
    ("flowNodeRef", BPMN_NS, "flowNodeRef"),
    ("collaboration", BPMN_NS, "collaboration"),
    ("conditionExpression", BPMN_NS, "conditionExpression"),
    ("BPMNDiagram", BPMNDI_NS, "BPMNDiagram"),
    ("BPMNPlane", BPMNDI_NS, "BPMNPlane"),
    ("BPMNShape", BPMNDI_NS, "BPMNShape"),
    ("BPMNEdge", BPMNDI_NS, "BPMNEdge"),
)
DECLARACOES: tuple[str, ...] = ("message", "signal", "error", "escalation")

_DELEGATE_ATTR = f"{{{CAMUNDA_NS}}}delegateExpression"
_SUFIXOS_FLOW_NODE = ("Task", "Event", "Gateway", "Activity", "SubProcess")


class FonteInvalidaError(ValueError):
    """Um `.bpmn` da fonte nao tem o formato esperado (tipo desconhecido, elemento sem id, etc.)."""


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname


def _ns(el: etree._Element) -> str | None:
    return etree.QName(el).namespace


def _elementos_xml(raiz: etree._Element) -> list[etree._Element]:
    return [el for el in raiz.iter() if isinstance(el.tag, str)]


def _processo_de(el: etree._Element) -> str:
    atual: etree._Element | None = el
    while atual is not None:
        if isinstance(atual.tag, str) and _ns(atual) == BPMN_NS and _local(atual) == "process":
            return str(atual.get("id"))
        atual = atual.getparent()
    raise FonteInvalidaError(f"elemento fora de <process>: {el.get('id')!r}")


def _chave_arquivo(nome: str) -> str:
    # "CA_1_COMPRA (com suporte)_v4_adaptado.bpmn" -> "CA_1"
    partes = nome.split("_")
    if len(partes) < 2 or partes[0] != "CA" or not partes[1].isdigit():
        raise FonteInvalidaError(f"nome de arquivo fora do padrao CA_<n>_...: {nome!r}")
    return f"CA_{partes[1]}"


def inventariar_arquivo(nome: str, dados: bytes) -> dict[str, Any]:
    """Conta um `.bpmn`. Puro (sem I/O): recebe o nome e os bytes."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=True)
    raiz = etree.fromstring(dados, parser=parser)
    todos = _elementos_xml(raiz)

    elementos: list[dict[str, str]] = []
    por_tipo: Counter[str] = Counter()
    for el in todos:
        if _ns(el) != BPMN_NS:
            continue
        tipo = _local(el)
        if tipo == "process" or tipo in TASKS or tipo in GATEWAYS or tipo in EVENTS:
            el_id = el.get("id")
            if not el_id:
                raise FonteInvalidaError(f"{nome}: <{tipo}> sem id")
            elementos.append(
                {
                    "id": el_id,
                    "tipo": tipo,
                    "nome": el.get("name") or "",
                    "processo": el_id if tipo == "process" else _processo_de(el),
                }
            )
            por_tipo[tipo] += 1
        elif tipo in CONTEINERES:
            continue
        elif tipo.endswith(_SUFIXOS_FLOW_NODE) or tipo == "transaction":
            raise FonteInvalidaError(f"{nome}: flow node de tipo desconhecido <{tipo}> id={el.get('id')!r}")

    def _grupo(tipos: tuple[str, ...]) -> dict[str, Any]:
        return {"total": sum(por_tipo[t] for t in tipos), "por_tipo": {t: por_tipo[t] for t in tipos}}

    tasks, gateways, events = _grupo(TASKS), _grupo(GATEWAYS), _grupo(EVENTS)
    processos = por_tipo["process"]
    denominador = processos + tasks["total"] + gateways["total"] + events["total"]
    if denominador != len(elementos):
        raise FonteInvalidaError(f"{nome}: denominador {denominador} != elementos listados {len(elementos)}")

    contagem_tags: Counter[tuple[str | None, str]] = Counter((_ns(el), _local(el)) for el in todos)
    agregados = {chave: contagem_tags[(ns, local)] for chave, ns, local in AGREGADOS}
    planos_sem_id = sum(
        1 for el in todos if _ns(el) == BPMNDI_NS and _local(el) == "BPMNPlane" and not el.get("id")
    )
    declaracoes: dict[str, int] = {
        d: sum(1 for el in raiz if isinstance(el.tag, str) and _ns(el) == BPMN_NS and _local(el) == d)
        for d in DECLARACOES
    }
    declaracoes_total = sum(declaracoes.values())

    ids = [str(el.get("id")) for el in todos if el.get("id")]
    ids_duplicados = sorted(k for k, v in Counter(ids).items() if v > 1)

    delegates = [str(el.get(_DELEGATE_ATTR)) for el in todos if el.get(_DELEGATE_ATTR)]

    lanes: list[dict[str, Any]] = []
    for lane in raiz.iterfind(".//bpmn:lane", NS):
        refs = [(r.text or "").strip() for r in lane.iterfind("bpmn:flowNodeRef", NS)]
        lanes.append(
            {
                "id": lane.get("id") or "",
                "nome": lane.get("name") or "",
                "processo": _processo_de(lane),
                "flow_node_refs": refs,
            }
        )
    ids_denominador = {e["id"] for e in elementos}
    referenciados = [r for lane in lanes for r in lane["flow_node_refs"]]
    refs_fora_do_denominador = sorted({r for r in referenciados if r not in ids_denominador})
    sem_lane = [e["id"] for e in elementos if e["id"] not in set(referenciados)]
    refs_em_mais_de_uma_lane = sorted(k for k, v in Counter(referenciados).items() if v > 1)

    return {
        "arquivo": f"{DIR_REL}/{nome}",
        "chave": _chave_arquivo(nome),
        "sha256": hashlib.sha256(dados).hexdigest(),
        "denominador": {
            "total": denominador,
            "processos": processos,
            "tasks": tasks,
            "gateways": gateways,
            "events": events,
        },
        "agregados": agregados,
        "planos_di_sem_id": planos_sem_id,
        "declaracoes": {**declaracoes, "total": declaracoes_total},
        "ids_duplicados": ids_duplicados,
        "delegate_expressions": {
            "ocorrencias": len(delegates),
            "distintos": len(set(delegates)),
            "valores_distintos": sorted(set(delegates)),
        },
        "elementos": elementos,
        "lanes": lanes,
        "lanes_resumo": {
            "flow_node_refs": len(referenciados),
            "refs_fora_do_denominador": refs_fora_do_denominador,
            "refs_em_mais_de_uma_lane": refs_em_mais_de_uma_lane,
            "denominador_sem_lane": sem_lane,
        },
    }


def _somar(arquivos: list[dict[str, Any]], caminho: tuple[str, ...]) -> int:
    total = 0
    for arq in arquivos:
        valor: Any = arq
        for chave in caminho:
            valor = valor[chave]
        total += int(valor)
    return total


def montar_inventario(fontes: list[tuple[str, bytes]]) -> dict[str, Any]:
    """Monta o inventario a partir de (nome, bytes) ja ordenados. Puro (sem I/O)."""
    if not fontes:
        raise FonteInvalidaError(f"nenhum {GLOB_FONTE} encontrado em {DIR_REL}")
    arquivos = [inventariar_arquivo(nome, dados) for nome, dados in fontes]
    chaves = [a["chave"] for a in arquivos]
    if len(set(chaves)) != len(chaves):
        raise FonteInvalidaError(f"chaves de arquivo repetidas: {chaves}")

    ids_corpus = [e["id"] for a in arquivos for e in a["elementos"]]
    delegates_corpus = sorted({v for a in arquivos for v in a["delegate_expressions"]["valores_distintos"]})

    def _grupo_total(nome: str, tipos: tuple[str, ...]) -> dict[str, Any]:
        return {
            "total": _somar(arquivos, ("denominador", nome, "total")),
            "por_tipo": {t: _somar(arquivos, ("denominador", nome, "por_tipo", t)) for t in tipos},
        }

    totais = {
        "denominador": {
            "total": _somar(arquivos, ("denominador", "total")),
            "processos": _somar(arquivos, ("denominador", "processos")),
            "tasks": _grupo_total("tasks", TASKS),
            "gateways": _grupo_total("gateways", GATEWAYS),
            "events": _grupo_total("events", EVENTS),
        },
        "agregados": {chave: _somar(arquivos, ("agregados", chave)) for chave, _, _ in AGREGADOS},
        "planos_di_sem_id": _somar(arquivos, ("planos_di_sem_id",)),
        "declaracoes": {d: _somar(arquivos, ("declaracoes", d)) for d in (*DECLARACOES, "total")},
        "ids_duplicados_por_arquivo": sum(len(a["ids_duplicados"]) for a in arquivos),
        "ids_de_denominador_duplicados_no_corpus": sorted(k for k, v in Counter(ids_corpus).items() if v > 1),
        "delegate_expressions": {
            "ocorrencias": _somar(arquivos, ("delegate_expressions", "ocorrencias")),
            "distintos_no_corpus": len(delegates_corpus),
            "valores_distintos": delegates_corpus,
        },
    }
    return {
        "schema_version": 1,
        "gerador": GERADOR_REL,
        "fonte": f"{DIR_REL}/{GLOB_FONTE}",
        "nota": (
            "Inventario gerado, so contagem (nao julga CASA/LACUNA). Denominador = process + tasks "
            "(todos os tipos, incl. callActivity e task sem tipo) + gateways + events; subProcess fora. "
            "Lanes copiadas literalmente de <bpmn:flowNodeRef>. Regenerar: "
            "python -m scripts.docs.gerar_inventario_ca"
        ),
        "metodo": {
            "parser": "lxml.etree (resolve_entities=False, no_network=True)",
            "denominador_tipos": {
                "processos": ["process"],
                "tasks": list(TASKS),
                "gateways": list(GATEWAYS),
                "events": list(EVENTS),
            },
            "fora_do_denominador": list(CONTEINERES),
        },
        "arquivos": arquivos,
        "totais": totais,
    }


def ler_fontes(raiz: Path = REPO_ROOT) -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted((raiz / DIR_REL).glob(GLOB_FONTE))]


def renderizar(dados: dict[str, Any]) -> str:
    return json.dumps(dados, indent=2, ensure_ascii=False) + "\n"


def gerar(raiz: Path = REPO_ROOT) -> str:
    return renderizar(montar_inventario(ler_fontes(raiz)))


_ABREV_TASK = {
    "userTask": "uT",
    "serviceTask": "sT",
    "sendTask": "seT",
    "receiveTask": "rT",
    "scriptTask": "scT",
    "manualTask": "mT",
    "businessRuleTask": "brT",
    "callActivity": "cA",
    "task": "task",
}
_ABREV_GW = {
    "exclusiveGateway": "XOR",
    "parallelGateway": "AND",
    "inclusiveGateway": "OR",
    "eventBasedGateway": "EBG",
    "complexGateway": "CPX",
}
_ABREV_EV = {
    "startEvent": "start",
    "endEvent": "end",
    "intermediateCatchEvent": "catch",
    "intermediateThrowEvent": "throw",
    "boundaryEvent": "boundary",
}


def resumo_texto(inv: dict[str, Any]) -> str:
    """Bloco de texto que o MAPA-ELEMENTOS-V4.md §7 transcreve (o teste de sync exige igualdade)."""
    linhas = ["A. CONTAGENS POR ARQUIVO (gerador: python -m scripts.docs.gerar_inventario_ca)"]
    for arq in inv["arquivos"]:
        den = arq["denominador"]
        partes = [f"process={den['processos']}"]
        for grupo, abrev in (("tasks", _ABREV_TASK), ("gateways", _ABREV_GW), ("events", _ABREV_EV)):
            partes += [f"{abrev[t]}={n}" for t, n in den[grupo]["por_tipo"].items() if n]
        linhas.append(f"   {arq['chave']}: DENOMINADOR = {den['total']:<4} ({' '.join(partes)})")
    linhas.append(
        "   planos DI sem id: " + " ".join(f"{a['chave']}={a['planos_di_sem_id']}" for a in inv["arquivos"])
    )
    tot = inv["totais"]
    den = tot["denominador"]
    linhas += [
        "B. RECONCILIACAO - soma checada",
        "   por arquivo: " + " | ".join(f"{a['chave']}={a['denominador']['total']}" for a in inv["arquivos"]),
        (
            f"   TOTAL DENOMINADOR CORPUS = {den['total']} = {den['processos']} processos + "
            f"{den['tasks']['total']} tasks + {den['gateways']['total']} gateways + "
            f"{den['events']['total']} events"
        ),
        "C. AGREGADOS (fora do denominador)",
    ]
    for chave, _, _ in AGREGADOS:
        por_arq = "+".join(str(a["agregados"][chave]) for a in inv["arquivos"])
        linhas.append(f"   {chave} = {tot['agregados'][chave]} ({por_arq})")
    dec = tot["declaracoes"]
    linhas += [
        "D. IDS E DELEGATES",
        f"   ids duplicados por arquivo: {tot['ids_duplicados_por_arquivo']}",
        f"   ids de denominador duplicados no corpus: {len(tot['ids_de_denominador_duplicados_no_corpus'])}",
        (
            f"   camunda:delegateExpression: {tot['delegate_expressions']['ocorrencias']} ocorrencias, "
            f"{tot['delegate_expressions']['distintos_no_corpus']} distintos no corpus"
        ),
        (
            f"F. DECLARACOES: {dec['total']} = message {dec['message']} + signal {dec['signal']} + "
            f"error {dec['error']} + escalation {dec['escalation']}"
        ),
    ]
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--check", action="store_true", help="falha se o JSON versionado divergir")
    modo.add_argument("--resumo", action="store_true", help="imprime o bloco do MAPA §7 e sai")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    inventario = montar_inventario(ler_fontes(args.root))
    if args.resumo:
        sys.stdout.write(resumo_texto(inventario))
        return 0
    esperado = renderizar(inventario)
    saida = args.root / SAIDA_REL
    if args.check:
        atual = saida.read_text(encoding="utf-8") if saida.exists() else None
        if atual != esperado:
            print(f"{SAIDA_REL} divergente dos .bpmn; rode: python -m scripts.docs.gerar_inventario_ca")
            return 1
        print(f"{SAIDA_REL} confere com {DIR_REL}/{GLOB_FONTE}")
        return 0
    saida.write_text(esperado, encoding="utf-8")
    print(f"escrito {SAIDA_REL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
