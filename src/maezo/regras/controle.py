"""Gera `spec/regras/controle-de-alteracoes.yaml` a partir dos catálogos (v3.1 §8; RF-43/RF-44).

O arquivo é GERADO e nunca editado à mão: uma linha por mudança de regra (`historico_de_mudanca`),
por aprovação, por liberação e por comprovação transcritas no catálogo. Determinístico: mesma
entrada, mesmos bytes (o CI regenera e compara — `scripts/ci/check_controle_de_alteracoes.py`).
Só contém o que o catálogo já tem; não cria nem infere decisão.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

import yaml

from maezo.regras.catalogo.repositorio import ARQUIVO_CONTROLE, carregar_catalogos
from maezo.regras.catalogo.schema import Catalogo

SCHEMA_CONTROLE: Final[str] = "maezo-regras-controle.v1"
CABECALHO: Final[str] = (
    "# CONTROLE DE ALTERACOES DAS REGRAS — ARQUIVO GERADO. NAO EDITE A MAO.\n"
    "# Fonte: spec/regras/catalogo/*.yaml. Gerar: python -m maezo.regras.controle gerar\n"
    "# O CI (scripts/ci/check_controle_de_alteracoes.py) reprova se este arquivo divergir do gerado.\n"
    "# ADR-0080 e DL-0194 (PROPOSTOS).\n"
)


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


def linhas(catalogos: Iterable[Catalogo]) -> list[dict[str, Any]]:
    """As linhas do controle, em ordem estável (processo, regra, evento, posição no catálogo)."""
    saida: list[dict[str, Any]] = []
    for cat in sorted(catalogos, key=lambda c: c.processo):
        for regra in cat.regras:
            base = {"processo": cat.processo, "regra_id": regra.id, "alias": _texto(regra.alias)}
            for i, h in enumerate(regra.historico_de_mudanca, start=1):
                saida.append(
                    {
                        **base,
                        "evento": "mudanca",
                        "seq": i,
                        "pr": h["pr"],
                        "o_que_mudou": _texto(h["o_que_mudou"]),
                    }
                )
            for i, a in enumerate(regra.aprovacoes, start=1):
                saida.append(
                    {
                        **base,
                        "evento": "aprovacao",
                        "seq": i,
                        "papel": _texto(a["papel"]),
                        "aprovador": _texto(a["aprovador"]),
                        "decisao": _texto(a["decisao"]),
                        "regra_sha256": _texto(a["regra_sha256"]),
                        "data": _texto(a["data"]),
                        "valida_ate": _texto(a["valida_ate"]),
                        "recibo": _texto((a.get("recibo") or {}).get("id")),
                    }
                )
            for i, lib in enumerate(regra.liberacoes, start=1):
                saida.append(
                    {
                        **base,
                        "evento": "liberacao",
                        "seq": i,
                        "liberador": _texto(lib["liberador"]),
                        "pr": lib["pr"],
                        "regra_sha256": _texto(lib["regra_sha256"]),
                        "data": _texto(lib["data"]),
                        "recibo": _texto((lib.get("recibo") or {}).get("id")),
                    }
                )
            for i, comp in enumerate(regra.comprovacoes, start=1):
                saida.append(
                    {
                        **base,
                        "evento": "comprovacao",
                        "seq": i,
                        "papel_da_condicao": _texto(comp["papel_da_condicao"]),
                        "comprovado_por": _texto(comp["comprovado_por"]),
                        "regra_sha256": _texto(comp["regra_sha256"]),
                        "data": _texto(comp["data"]),
                        "recibo": _texto((comp.get("recibo") or {}).get("id")),
                    }
                )
    return saida


def gerar(catalogos: Iterable[Catalogo]) -> str:
    """Texto completo do arquivo (LF, UTF-8, chaves ordenadas)."""
    corpo = yaml.safe_dump(
        {"schema": SCHEMA_CONTROLE, "linhas": linhas(catalogos)},
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )
    return CABECALHO + corpo


def main(argv: list[str] | None = None) -> int:
    """`gerar`: escreve o arquivo a partir dos catálogos; recusa se algum catálogo for inválido."""
    parser = argparse.ArgumentParser(description="Gera spec/regras/controle-de-alteracoes.yaml.")
    parser.add_argument("acao", choices=["gerar"])
    parser.add_argument("--raiz", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    catalogos, achados = carregar_catalogos(args.raiz)
    if achados:
        for achado in achados:
            print(f"  - {achado}", file=sys.stderr)
        print("controle: catalogo invalido; nada foi gerado.", file=sys.stderr)
        return 1
    (args.raiz / ARQUIVO_CONTROLE).write_text(gerar(catalogos), encoding="utf-8", newline="\n")
    print(f"controle: {ARQUIVO_CONTROLE} gerado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CABECALHO", "SCHEMA_CONTROLE", "gerar", "linhas", "main"]
