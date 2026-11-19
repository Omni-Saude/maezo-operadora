#!/usr/bin/env python3
"""CI gate: todo artefato coberto pelo escopo tem regra no catálogo (RF-07).

Chancela de regras, onda 1 (ADR-0080 e DL-0194, PROPOSTOS; blueprint §3.12).

`spec/regras/escopo.yaml` diz, por processo, o que está coberto: uma folha de caminho YAML, toda linha
de uma tabela DMN, um elemento BPMN, uma constante Python ou uma seção de documento. Cada unidade
coberta tem de aparecer na `fonte` de alguma regra do catálogo do MESMO processo — senão a chave
nova (ou a linha DMN nova) entraria em produção sem texto de negócio que alguém possa aprovar.

REPROVA: escopo malformado; processo no escopo sem catálogo válido; artefato coberto irresolvível
ou vácuo; unidade coberta sem regra.

Uso:
    python scripts/ci/check_regras_cobertura.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from maezo.regras.catalogo.escopo import carregar_escopo, verificar_cobertura  # noqa: E402
from maezo.regras.catalogo.repositorio import carregar_catalogos  # noqa: E402


def verificar(raiz: Path = _RAIZ) -> list[str]:
    escopo, achados = carregar_escopo(raiz)
    catalogos, erros = carregar_catalogos(raiz)
    achados += erros
    achados += verificar_cobertura(raiz, escopo, catalogos)
    return achados


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Chancela de regras: cobertura do escopo.").parse_args(argv)
    achados = verificar()
    if achados:
        print("check_regras_cobertura: escopo com artefato sem regra:", file=sys.stderr)
        for achado in achados:
            print(f"  - {achado}", file=sys.stderr)
        return 1
    escopo, _ = carregar_escopo(_RAIZ)
    for proc in escopo:
        print(
            f"check_regras_cobertura: {proc.processo} (portao {proc.portao}): "
            "todo artefato coberto tem regra. OK."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
