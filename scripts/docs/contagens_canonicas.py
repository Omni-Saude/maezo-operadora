#!/usr/bin/env python3
"""Contagens canonicas que AGENTS.md e README.md afirmam, recomputadas da arvore (R-073, F-111).

F-111 achou AGENTS.md/README.md com contagens escritas a mao e estaleis (24 ADRs vs 67; 34 DL vs
155+; 16 contratos vs 17; 3 itens `hard` vs 5; checklist `predeploy-findings.json` inexistente;
README com 59 ADRs). Este modulo recomputa cada contagem do sistema de arquivos e confere as frases
ancoradas abaixo — so frases com formato inequivoco de contagem ATUAL, nunca prosa historica.

Definicoes (cada uma reproduzivel por um comando de shell, citado ao lado):

- ADRs numerados: `docs/adr/[0-9][0-9][0-9][0-9]-*.md`
  (`ls docs/adr/00*.md | wc -l`).
- ADRs Accepted / Proposed: primeira palavra apos `**Status:**` (asteriscos removidos) na
  primeira linha `**Status:**` de cada ADR; `Proposto` conta como Proposed.
- DLs: IDs `DL-NNNN` distintos na 2a coluna das linhas de tabela de `docs/decisions-log.md`
  (adendos de um mesmo DL nao contam de novo).
- Contratos SP-OP: `docs/processes/contracts/SP-OP-*.md`.
- Processos BPMN: `spec/processes/bpmn/*.bpmn`; arquivos DMN: `spec/processes/dmn/*.dmn`.
- Itens hard: `hard_items[].action` de `spec/policies/autonomy/_hard_frozen.yaml`.
- Checklist pre-deploy: `totais` de `docs/reports/predeploy-findings.json`.

Uso: `python -m scripts.docs.contagens_canonicas` imprime as contagens e as divergencias (exit 1).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

_ADR_FILE_RE = re.compile(r"^(\d{4})-.+\.md$")
_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.*)$", re.MULTILINE)
_DL_ROW_RE = re.compile(r"^\|[^|]*\|\s*(DL-(\d+))\b", re.MULTILINE)
# README: "as N ações L0 congeladas em `..._hard_frozen.yaml`: `a`, `b`, ..." — a lista inteira
# tem de ser IGUAL (como conjunto) a `hard_items[].action`, nao so a contagem.
_L0_README_RE = re.compile(
    r"as (\d+) ações L0 congeladas em `spec/policies/autonomy/_hard_frozen\.yaml`: "
    r"((?:`[a-z_]+`(?:, )?)+)"
)

# AGENTS.md regra 7: "os N itens congelados[ em `..._hard_frozen.yaml`]: `a` (...), `b` ..." — os
# identificadores citados ate o fim da linha tem de ser IGUAIS ao conjunto congelado (F5: item
# extra/duplicado e vermelho, nao so item faltante).
_HARD_AGENTS_RE = re.compile(
    r"os \d+ itens congelados(?: em `spec/policies/autonomy/_hard_frozen\.yaml`)?: ([^\n]*)"
)


@dataclass(frozen=True)
class Contagens:
    adrs: int
    adr_primeiro: int
    adr_ultimo: int
    adrs_aceitos: int
    adrs_propostos: int
    dls: int
    dl_primeiro: int
    dl_ultimo: int
    contratos: int
    bpmn: int
    dmn: int
    itens_hard: tuple[str, ...]
    predeploy_registros: int
    predeploy_confirmados: int
    predeploy_refutados: int


def _status_adr(texto: str) -> str:
    m = _STATUS_RE.search(texto)
    if m is None:
        return "SEM-STATUS"
    palavra = m.group(1).replace("*", "").strip().split(maxsplit=1)
    return palavra[0] if palavra else "SEM-STATUS"


def contar(raiz: Path = REPO_ROOT) -> Contagens:
    adr_dir = raiz / "docs" / "adr"
    numeros: list[int] = []
    aceitos = propostos = 0
    for p in sorted(adr_dir.iterdir()):
        m = _ADR_FILE_RE.match(p.name)
        if m is None:
            continue
        numeros.append(int(m.group(1)))
        status = _status_adr(p.read_text(encoding="utf-8"))
        if status == "Accepted":
            aceitos += 1
        elif status in ("Proposed", "Proposto"):
            propostos += 1

    dl_texto = (raiz / "docs" / "decisions-log.md").read_text(encoding="utf-8")
    dl_nums = sorted({int(m.group(2)) for m in _DL_ROW_RE.finditer(dl_texto)})

    hard = yaml.safe_load((raiz / "spec/policies/autonomy/_hard_frozen.yaml").read_text(encoding="utf-8"))
    acoes = tuple(str(item["action"]) for item in hard["hard_items"])

    pre = raiz / "docs" / "reports" / "predeploy-findings.json"
    totais = json.loads(pre.read_text(encoding="utf-8"))["totais"] if pre.exists() else {}

    return Contagens(
        adrs=len(numeros),
        adr_primeiro=min(numeros, default=0),
        adr_ultimo=max(numeros, default=0),
        adrs_aceitos=aceitos,
        adrs_propostos=propostos,
        dls=len(dl_nums),
        dl_primeiro=min(dl_nums, default=0),
        dl_ultimo=max(dl_nums, default=0),
        contratos=len(list((raiz / "docs/processes/contracts").glob("SP-OP-*.md"))),
        bpmn=len(list((raiz / "spec/processes/bpmn").glob("*.bpmn"))),
        dmn=len(list((raiz / "spec/processes/dmn").glob("*.dmn"))),
        itens_hard=acoes,
        predeploy_registros=int(totais.get("registros", -1)),
        predeploy_confirmados=int(totais.get("confirmados", -1)),
        predeploy_refutados=int(totais.get("refutados", -1)),
    )


def _todas(padrao: str, texto: str) -> list[tuple[str, ...]]:
    return [m.groups() for m in re.finditer(padrao, texto)]


def _confere(
    erros: list[str], arquivo: str, rotulo: str, padrao: str, texto: str, esperado: tuple[int, ...]
) -> None:
    achados = _todas(padrao, texto)
    if not achados:
        erros.append(f"{arquivo}: alegacao '{rotulo}' ausente (padrao {padrao!r})")
    for grupos in achados:
        valores = tuple(int(g) for g in grupos)
        if valores != esperado:
            erros.append(f"{arquivo}: '{rotulo}' afirma {valores}, arvore tem {esperado}")


def verificar_agents(texto: str, c: Contagens) -> list[str]:
    """Divergencias entre as alegacoes de contagem de AGENTS.md e a arvore. Puro."""
    erros: list[str] = []
    a = "AGENTS.md"
    adr_faixa = (c.adrs, c.adr_primeiro, c.adr_ultimo, c.adrs_aceitos, c.adrs_propostos)
    _confere(
        erros,
        a,
        "N numerados (PPPP-UUUU): A Accepted, P Proposed",
        r"(\d+) numerados \((\d{4})–(\d{4})\): (\d+) \"Accepted\", (\d+) \"Proposed\"",
        texto,
        adr_faixa,
    )
    _confere(erros, a, "todos os N ADRs", r"todos os (\d+) ADRs", texto, (c.adrs,))
    _confere(erros, a, "todos os N contratos SP-OP", r"todos os (\d+) contratos SP-OP", texto, (c.contratos,))
    _confere(
        erros,
        a,
        "N entradas DL (DL-PPPP–DL-UUUU)",
        r"(\d+) entradas DL \(`DL-(\d{4})`–`DL-(\d{4})`\)",
        texto,
        (c.dls, c.dl_primeiro, c.dl_ultimo),
    )
    _confere(erros, a, "os N itens hard", r"os (\d+) itens congelados", texto, (len(c.itens_hard),))
    for acao in c.itens_hard:
        if f"`{acao}`" not in texto:
            erros.append(f"{a}: item hard `{acao}` de _hard_frozen.yaml nao citado")
    for m in _HARD_AGENTS_RE.finditer(texto):
        citadas = re.findall(r"`([a-z_]+)`", m.group(1))
        if sorted(citadas) != sorted(c.itens_hard):
            erros.append(
                f"{a}: lista de itens hard {citadas} difere de _hard_frozen.yaml {list(c.itens_hard)}"
            )
    _confere(
        erros,
        a,
        "N registros da auditoria pre-deploy (C confirmados + R refutados)",
        r"(\d+) registros da auditoria pré-deploy \((\d+) confirmados \+ (\d+) refutados\)",
        texto,
        (c.predeploy_registros, c.predeploy_confirmados, c.predeploy_refutados),
    )
    for estale in (
        r"\b(\d+) findings da auditoria",
        r"\b(\d+) DL entries",
        r"Greenfield Day 0",
        r"implementação a construir",
    ):
        if re.search(estale, texto):
            erros.append(f"{a}: frase de contagem antiga ainda presente ({estale!r})")
    if "docs/reports/predeploy-findings.json" in texto and c.predeploy_registros < 0:
        erros.append(f"{a}: cita docs/reports/predeploy-findings.json mas o arquivo nao existe")
    return erros


def verificar_readme(texto: str, c: Contagens) -> list[str]:
    """Divergencias entre as alegacoes de contagem de README.md e a arvore. Puro."""
    erros: list[str] = []
    r = "README.md"
    _confere(erros, r, "N processos BPMN", r"(\d+) processos BPMN", texto, (c.bpmn,))
    _confere(erros, r, "Processos BPMN SP-OP | N", r"\| Processos BPMN SP-OP \| (\d+) \|", texto, (c.bpmn,))
    _confere(erros, r, "N arquivos DMN", r"(\d+) arquivos DMN", texto, (c.dmn,))
    _confere(
        erros, r, "Arquivos DMN | N", r"\| Arquivos de decisão DMN \(`\.dmn`\) \| (\d+) \|", texto, (c.dmn,)
    )
    _confere(
        erros,
        r,
        "ADRs | N (PPPP–UUUU; A Accepted, P Proposed/Proposto)",
        r"\| ADRs \| (\d+) \((\d{4})–(\d{4}); (\d+) Accepted, (\d+) Proposed/Proposto\) \|",
        texto,
        (c.adrs, c.adr_primeiro, c.adr_ultimo, c.adrs_aceitos, c.adrs_propostos),
    )
    _confere(
        erros,
        r,
        "N ADRs numerados (PPPP–UUUU",
        r"(\d+) ADRs numerados \((\d{4})–(\d{4})",
        texto,
        (c.adrs, c.adr_primeiro, c.adr_ultimo),
    )
    _confere(erros, r, "N contratos SP-OP", r"(\d+) contratos SP-OP", texto, (c.contratos,))
    _confere(
        erros,
        r,
        "N decisões (DL-PPPP–DL-UUUU)",
        r"(\d+) decisões [a-z ]*\(DL-(\d{4})–DL-(\d{4})\)",
        texto,
        (c.dls, c.dl_primeiro, c.dl_ultimo),
    )
    _confere(
        erros,
        r,
        "as N ações L0 congeladas",
        r"as (\d+) ações L0 congeladas em `spec/policies/autonomy/_hard_frozen\.yaml`",
        texto,
        (len(c.itens_hard),),
    )
    for m in _L0_README_RE.finditer(texto):
        lista = m.group(2)
        citadas = re.findall(r"`([a-z_]+)`", lista)
        if sorted(citadas) != sorted(c.itens_hard):
            erros.append(f"{r}: lista de ações L0 {citadas} difere de _hard_frozen.yaml {list(c.itens_hard)}")
    if re.search(r"\d+ tabelas DMN|Tabelas de decisão DMN \| \d+", texto):
        erros.append(f"{r}: contagem de 'tabelas DMN' antiga ainda presente (use arquivos .dmn)")
    return erros


def main() -> int:
    c = contar()
    print(c)
    erros = verificar_agents((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"), c)
    erros += verificar_readme((REPO_ROOT / "README.md").read_text(encoding="utf-8"), c)
    for e in erros:
        print(f"DIVERGENCIA: {e}")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
