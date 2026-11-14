#!/usr/bin/env python3
"""Gera `docs/reports/predeploy-findings.json` a partir do relatorio canonico (R-073, F-111, PEND-551).

Fonte unica: `docs/reports/predeploy-audit-report.md` (rastreado), tabelas da secao 3 ("Findings
table -- all 60 confirmed, grouped by dimension", subsecoes 3.1..3.7) e da secao 4 ("Killed-findings
table (18)"). Nenhum registro e inventado: cada linha de tabela vira exatamente um registro, com as
celulas copiadas literalmente (so `\\|` vira `|` e espacos das bordas sao removidos). O gerador e
deterministico (ordem do documento, `json.dumps(..., indent=2, ensure_ascii=False)`) e falha alto se
a contagem parseada divergir dos totais que o proprio relatorio declara nos titulos das secoes.

Uso:
    python -m scripts.docs.gerar_predeploy_findings           # (re)escreve o JSON
    python -m scripts.docs.gerar_predeploy_findings --check   # exit 1 se o JSON versionado divergir
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FONTE_REL = "docs/reports/predeploy-audit-report.md"
SAIDA_REL = "docs/reports/predeploy-findings.json"
GERADOR_REL = "scripts/docs/gerar_predeploy_findings.py"

_SEC3_RE = re.compile(r"^## 3\. Findings table .*?all (\d+) confirmed", re.MULTILINE)
_SEC4_RE = re.compile(r"^## 4\. Killed-findings table \((\d+)\)", re.MULTILINE)
_SUBSEC_RE = re.compile(r"^### 3\.\d+ (\S+) \((\d+) confirmed\)\s*$")
_ID_RE = re.compile(r"^`([a-z0-9][a-z0-9-]*)`$")


class FonteInvalidaError(ValueError):
    """O relatorio canonico nao tem o formato esperado ou seus totais nao conferem."""


def _celulas(linha: str) -> list[str]:
    """Divide uma linha de tabela markdown em celulas, respeitando `\\|` escapado."""
    corpo = linha.strip()
    if not (corpo.startswith("|") and corpo.endswith("|")):
        raise FonteInvalidaError(f"linha de tabela malformada: {linha!r}")
    partes = re.split(r"(?<!\\)\|", corpo[1:-1])
    return [p.strip().replace("\\|", "|") for p in partes]


def _id(celula: str) -> str:
    m = _ID_RE.match(celula)
    if m is None:
        raise FonteInvalidaError(f"celula de ID fora do formato `kebab-id`: {celula!r}")
    return m.group(1)


def _linhas_de_tabela(linhas: list[str], inicio: int) -> tuple[list[list[str]], int]:
    """A partir de `inicio`, acha a proxima tabela e devolve (linhas de dados, indice apos a tabela)."""
    i = inicio
    while i < len(linhas) and not linhas[i].startswith("|"):
        if linhas[i].startswith("#"):
            raise FonteInvalidaError(f"secao sem tabela antes da linha {i + 1}")
        i += 1
    if i + 1 >= len(linhas) or not re.match(r"^\|(\s*-+\s*\|)+\s*$", linhas[i + 1]):
        raise FonteInvalidaError(f"tabela sem cabecalho/separador na linha {i + 1}")
    i += 2
    dados: list[list[str]] = []
    while i < len(linhas) and linhas[i].startswith("|"):
        dados.append(_celulas(linhas[i]))
        i += 1
    return dados, i


def extrair_registros(texto: str) -> dict[str, Any]:
    """Extrai os registros das secoes 3 e 4 do relatorio. Puro (sem I/O)."""
    m3 = _SEC3_RE.search(texto)
    m4 = _SEC4_RE.search(texto)
    if m3 is None or m4 is None:
        raise FonteInvalidaError("secoes '## 3. Findings table' / '## 4. Killed-findings table' ausentes")
    total_conf_declarado = int(m3.group(1))
    total_ref_declarado = int(m4.group(1))
    sec3 = texto[m3.start() : m4.start()].splitlines()
    sec4 = texto[m4.start() :].splitlines()

    registros: list[dict[str, str]] = []
    i = 0
    while i < len(sec3):
        sub = _SUBSEC_RE.match(sec3[i])
        if sub is None:
            i += 1
            continue
        dimensao, declarado = sub.group(1), int(sub.group(2))
        dados, i = _linhas_de_tabela(sec3, i + 1)
        if len(dados) != declarado:
            raise FonteInvalidaError(
                f"subsecao {dimensao}: titulo declara {declarado} confirmados, tabela tem {len(dados)}"
            )
        for cel in dados:
            if len(cel) != 5:
                raise FonteInvalidaError(f"linha confirmada com {len(cel)} celulas (esperado 5): {cel!r}")
            registros.append(
                {
                    "id": _id(cel[0]),
                    "dimensao": dimensao,
                    "status": "confirmado",
                    "severidade": cel[1],
                    "afirmacao": cel[2],
                    "evidencia": cel[3],
                    "disposicao": cel[4],
                }
            )
    n_conf = len(registros)
    if n_conf != total_conf_declarado:
        raise FonteInvalidaError(f"secao 3 declara {total_conf_declarado} confirmados, parseados {n_conf}")

    dados4, _ = _linhas_de_tabela(sec4, 1)
    for cel in dados4:
        if len(cel) != 4:
            raise FonteInvalidaError(f"linha refutada com {len(cel)} celulas (esperado 4): {cel!r}")
        registros.append(
            {
                "id": _id(cel[1]),
                "dimensao": cel[0],
                "status": "refutado",
                "refutado_por": cel[2],
                "motivo": cel[3],
            }
        )
    n_ref = len(registros) - n_conf
    if n_ref != total_ref_declarado:
        raise FonteInvalidaError(f"secao 4 declara {total_ref_declarado} refutados, parseados {n_ref}")

    ids = [r["id"] for r in registros]
    duplicados = sorted({x for x in ids if ids.count(x) > 1})
    if duplicados:
        raise FonteInvalidaError(f"IDs duplicados no relatorio: {duplicados}")

    return {
        "schema_version": 1,
        "fonte": FONTE_REL,
        "gerador": GERADOR_REL,
        "nota": (
            "Checklist historico 'nao repetir' (auditoria WS-5 de 2026-07-04). Registros refutados "
            "ficam listados como evidencia do refutador; nenhum registro e diagnostico do estado atual."
        ),
        "totais": {"registros": len(registros), "confirmados": n_conf, "refutados": n_ref},
        "registros": registros,
    }


def renderizar(dados: dict[str, Any]) -> str:
    return json.dumps(dados, indent=2, ensure_ascii=False) + "\n"


def gerar(raiz: Path = REPO_ROOT) -> str:
    return renderizar(extrair_registros((raiz / FONTE_REL).read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="falha se o JSON versionado divergir")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    esperado = gerar(args.root)
    saida = args.root / SAIDA_REL
    if args.check:
        atual = saida.read_text(encoding="utf-8") if saida.exists() else None
        if atual != esperado:
            print(
                f"{SAIDA_REL} divergente de {FONTE_REL}; "
                "rode: python -m scripts.docs.gerar_predeploy_findings"
            )
            return 1
        print(f"{SAIDA_REL} confere com {FONTE_REL}")
        return 0
    saida.write_text(esperado, encoding="utf-8")
    print(f"escrito {SAIDA_REL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
