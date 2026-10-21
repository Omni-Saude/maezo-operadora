#!/usr/bin/env python3
"""Cerca de CI: nenhum codigo de producao importa `maezo.qa` (o simulador de beneficiario e afins).

Por que
-------
`maezo.qa.simulador` compoe o receptor REAL (`platform.webhooks.service.build_live_dispatcher`) e
troca as bordas de saida: a Meta vira captura, o motor vira interceptador de SP-OP-ESCALATION-001,
o consentimento de terceiros nao e' gravado. Tudo isso e' certo num teste e errado em producao. A
dependencia so' pode ir num sentido — o QA importa a plataforma, nunca o contrario —, senao um
import descuidado traria para o processo do receptor um caminho que finge escalar.

O que reprova
-------------
Em qualquer `.py` de `src/maezo/` FORA de `src/maezo/qa/`:
  * `import maezo.qa...` / `from maezo.qa... import ...`;
  * import relativo que resolve para `maezo.qa` (`from ...qa import x` dentro de `maezo/platform`);
  * string `maezo.qa` ou `maezo.qa.<algo>` fora de docstring (o caminho do `importlib`).
Arquivo que nao parseia reprova (nunca e' pulado). Nao-vacuidade: o pacote `maezo/qa` tem de existir
e a varredura tem de ter lido arquivos.

Uso: `python3 scripts/ci/check_qa_isolada.py [--src-dir src/maezo]`
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from pathlib import Path

PACOTE_QA = "maezo.qa"


def _e_qa(modulo: str | None) -> bool:
    return bool(modulo) and (modulo == PACOTE_QA or str(modulo).startswith(PACOTE_QA + "."))


def _modulo_do_arquivo(caminho: Path, src_dir: Path) -> list[str]:
    """As partes do nome do pacote do arquivo (`maezo/platform/x.py` -> ["maezo", "platform"])."""
    relativo = caminho.relative_to(src_dir.parent).with_suffix("")
    partes = list(relativo.parts)
    return partes[:-1]


def _resolver_relativo(pacote: list[str], nivel: int, modulo: str | None) -> str | None:
    if nivel - 1 > len(pacote):
        return None
    base = pacote[: len(pacote) - (nivel - 1)] if nivel > 1 else pacote
    return ".".join([*base, *([modulo] if modulo else [])])


def _docstrings(arvore: ast.AST) -> set[int]:
    ids: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            corpo = no.body
            if corpo and isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant):
                ids.add(id(corpo[0].value))
    return ids


def varrer_arquivo(caminho: Path, src_dir: Path) -> list[str]:
    try:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    except (SyntaxError, OSError, UnicodeDecodeError) as exc:
        return [f"{caminho}: nao parseia ({type(exc).__name__}) — reprovado por precaucao"]
    pacote = _modulo_do_arquivo(caminho, src_dir)
    docstrings = _docstrings(arvore)
    achados: list[str] = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for nome in no.names:
                if _e_qa(nome.name):
                    achados.append(f"{caminho}:{no.lineno}: import {nome.name}")
        elif isinstance(no, ast.ImportFrom):
            alvo = no.module if no.level == 0 else _resolver_relativo(pacote, no.level, no.module)
            candidatos = [alvo] + [f"{alvo}.{n.name}" for n in no.names if alvo]
            if any(_e_qa(c) for c in candidatos):
                achados.append(f"{caminho}:{no.lineno}: from {'.' * no.level}{no.module or ''} import ...")
        elif (
            isinstance(no, ast.Constant)
            and isinstance(no.value, str)
            and id(no) not in docstrings
            and _e_qa(no.value.strip())
        ):
            achados.append(f"{caminho}:{no.lineno}: string {no.value.strip()!r} (import dinamico?)")
    return achados


def executar(src_dir: Path) -> tuple[list[str], int]:
    """(achados, arquivos lidos). Achado tambem quando a varredura seria vacua."""
    if not (src_dir / "qa" / "__init__.py").is_file():
        return [f"{src_dir}/qa/__init__.py ausente — a cerca ficaria vacua"], 0
    achados: list[str] = []
    lidos = 0
    for caminho in sorted(src_dir.rglob("*.py")):
        if (src_dir / "qa") in caminho.parents:
            continue
        lidos += 1
        achados.extend(varrer_arquivo(caminho, src_dir))
    if lidos == 0:
        achados.append(f"{src_dir}: nenhum arquivo de producao lido — a cerca ficaria vacua")
    return achados, lidos


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--src-dir", default="src/maezo", type=Path)
    args = parser.parse_args(argv)
    achados, lidos = executar(args.src_dir)
    if achados:
        print(f"[qa-isolada] FAIL ({lidos} arquivos de producao lidos)")
        for achado in achados:
            print(f"  {achado}")
        return 1
    print(f"[qa-isolada] PASS ({lidos} arquivos de producao lidos; nenhum importa {PACOTE_QA})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
