#!/usr/bin/env python3
"""A trilha de CI de uma mudanca (ADR-0067 fase 4): plataforma, logica de agente ou conteudo de agente.

Por que
-------
Antes, todo PR passava pela suite inteira do Maezo (~37 mil testes, ~11 min de unit + ~22 min de integracao),
inclusive um PR que so' troca o modelo do roteador no pacote da Helena. O objetivo do ADR-0067 e' que mudar
um agente nao passe pelo deploy nem pela suite do Maezo. Esta classificacao decide, pelo que o PR TOCA:

  * conteudo de agente — `spec/agents/<id>/pacote/**`: so' o pacote (validado por `agentes-pacote.yml`);
  * logica de agente   — `src/maezo/agents/<id>/**` e os testes do agente: lint/tipo do agente + os testes que
    importam o agente + as cercas de fronteira (job `agentes (trilha)` do `ci.yml`);
  * plataforma         — QUALQUER outro arquivo, inclusive `maezo.contratos` (o contrato muda os dois
    lados), o framework de agentes (`src/maezo/agents/*.py`) e o proprio CI: a suite inteira, como sempre.

Na duvida, plataforma: um arquivo que nao casa com nenhuma regra de agente leva o PR para a suite inteira.

Uso
---
  git diff --name-only <base>...HEAD | python3 scripts/ci/trilha.py            # JSON: plataforma, agentes...
  python3 scripts/ci/trilha.py --testes-do-agente helena                     # os testes que importam a Helena
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
_PACOTE = re.compile(r"^spec/agents/(?P<id>[a-z][a-z0-9_]*)/pacote/.+")
_LOGICA = re.compile(r"^src/maezo/agents/(?P<id>[a-z][a-z0-9_]*)/.+")
_TESTE = re.compile(r"^tests/unit/agents/test_(?P<id>[a-z][a-z0-9]*)(?:_[a-z0-9_]+)?\.py$")


def agentes_existentes(raiz: Path = _RAIZ) -> set[str]:
    return {p.parent.name for p in (raiz / "src" / "maezo" / "agents").glob("*/__init__.py")}


def classificar(arquivos: Iterable[str], agentes: set[str]) -> dict[str, object]:
    """`plataforma` (bool), `agentes` (ids com mudanca de logica), `conteudo` (so' de pacote)."""
    plataforma = False
    logica: set[str] = set()
    conteudo: set[str] = set()
    vazio = True
    for bruto in arquivos:
        arquivo = bruto.strip().replace("\\", "/")
        if not arquivo:
            continue
        vazio = False
        if (m := _PACOTE.match(arquivo)) and m["id"] in agentes:
            conteudo.add(m["id"])
        elif (
            (m := _LOGICA.match(arquivo))
            and m["id"] in agentes
            or (m := _TESTE.match(arquivo))
            and m["id"] in agentes
        ):
            logica.add(m["id"])
        else:
            plataforma = True
    if vazio:
        plataforma = True  # sem diff legivel: a suite inteira
    return {
        "plataforma": plataforma,
        "agentes": sorted(logica),
        "conteudo": sorted(conteudo - logica),
    }


def testes_do_agente(agente: str, raiz: Path = _RAIZ) -> list[str]:
    """Os arquivos de teste UNITARIOS e de contrato que importam o agente (ou o citam pelo caminho), mais os
    testes da fronteira. Integracao (motor real) e evals ficam com a trilha da plataforma e o noturno."""
    marcas = (f"maezo.agents.{agente}", f"agents/{agente}/", f"maezo.agents import {agente}")
    saida = []
    candidatos = [
        *(raiz / "tests" / "unit").rglob("test_*.py"),
        *(raiz / "tests" / "contract").rglob("test_*.py"),
    ]
    for p in sorted(candidatos):
        texto = p.read_text(encoding="utf-8", errors="replace")
        if any(m in texto for m in marcas):
            saida.append(p.relative_to(raiz).as_posix())
    fixos = ["tests/unit/ci/test_check_fronteira_agentes.py", "tests/unit/runtime/test_pacote_do_agente.py"]
    return sorted(set(saida) | {f for f in fixos if (raiz / f).is_file()})


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--testes-do-agente", metavar="ID")
    args = p.parse_args(argv)
    if args.testes_do_agente:
        print("\n".join(testes_do_agente(args.testes_do_agente)))
        return 0
    print(json.dumps(classificar(sys.stdin.read().splitlines(), agentes_existentes())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
