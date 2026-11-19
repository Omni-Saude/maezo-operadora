#!/usr/bin/env python3
"""CI gate: `spec/regras/controle-de-alteracoes.yaml` é exatamente o gerado dos catálogos (RF-44).

Chancela de regras, onda 1 (ADR-0080 e DL-0194, PROPOSTOS; blueprint §3.12).

O controle de alterações nunca é editado à mão: é regenerado de `spec/regras/catalogo/*.yaml` por
`python -m maezo.regras.controle gerar`. REPROVA arquivo ausente, divergente (byte a byte, depois de
CRLF -> LF) ou catálogo inválido (não há o que gerar).

Uso:
    python scripts/ci/check_controle_de_alteracoes.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from maezo.regras.catalogo.repositorio import ARQUIVO_CONTROLE, carregar_catalogos  # noqa: E402
from maezo.regras.controle import gerar  # noqa: E402


def verificar(raiz: Path = _RAIZ) -> list[str]:
    catalogos, achados = carregar_catalogos(raiz)
    if achados:
        return [*achados, f"{ARQUIVO_CONTROLE}: catalogo invalido, nao ha' como regenerar"]
    caminho = raiz / ARQUIVO_CONTROLE
    if not caminho.is_file():
        return [f"{ARQUIVO_CONTROLE} nao existe — rode `python -m maezo.regras.controle gerar`"]
    atual = caminho.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    if atual != gerar(catalogos):
        return [
            f"{ARQUIVO_CONTROLE} diverge do gerado dos catalogos — ele nunca e' editado a mao. "
            f"Rode `python -m maezo.regras.controle gerar` e comite o resultado."
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Chancela de regras: controle de alteracoes gerado.").parse_args(argv)
    achados = verificar()
    if achados:
        print("check_controle_de_alteracoes:", file=sys.stderr)
        for achado in achados:
            print(f"  - {achado}", file=sys.stderr)
        return 1
    print(f"check_controle_de_alteracoes: {ARQUIVO_CONTROLE} confere com o gerado. OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
