#!/usr/bin/env python3
"""CI gate: o catálogo de regras está bem formado e o hash declarado de cada regra é o recalculado.

Chancela de regras, onda 1 (ADR-0080 e DL-0194, PROPOSTOS; blueprint §3.2, §3.3, §3.12).

REPROVA:
  1. forma do `maezo-regras-catalogo.v1` fora da validação por campo (`maezo.regras.catalogo.schema`):
     chave desconhecida, papel fora do conjunto fechado, texto fora do tamanho, dado pessoal em
     exemplo, `editavel` incoerente, aprovação transcrita malformada;
  2. fonte irresolvível no checkout (arquivo, caminho YAML, <rule>, elemento, constante, seção);
  3. `sha256` declarado diferente do recalculado — a mensagem imprime o valor certo. Atualizar o hash
     é o jeito de dizer "esta regra mudou"; a aprovação do hash antigo deixa de valer (B4);
  4. `id` ou `alias` repetido em QUALQUER par de catálogos.

NÃO FAZ, de propósito: não aprova, não escreve no catálogo, não exige aprovação (isso é o
`check_chancela_regras.py` da onda 4). Uma regra `legado_pendente` sem aprovação passa — e aparece.

Uso:
    python scripts/ci/check_catalogo_regras.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from maezo.regras.catalogo.extratores import FonteError  # noqa: E402
from maezo.regras.catalogo.hash import regra_sha256  # noqa: E402
from maezo.regras.catalogo.repositorio import carregar_catalogos  # noqa: E402
from maezo.regras.catalogo.schema import Catalogo  # noqa: E402


def verificar(raiz: Path = _RAIZ, hoje: date | None = None) -> tuple[list[Catalogo], list[str]]:
    """(catálogos válidos, achados). Achados vazios = verde."""
    catalogos, achados = carregar_catalogos(raiz, hoje)
    donos_id: dict[str, str] = {}
    donos_alias: dict[str, str] = {}
    for cat in catalogos:
        for regra in cat.regras:
            if regra.id in donos_id and donos_id[regra.id] != cat.arquivo:
                achados.append(f"{regra.id}: `id` repetido em {donos_id[regra.id]} e {cat.arquivo}")
            donos_id.setdefault(regra.id, cat.arquivo)
            if regra.alias is not None:
                if regra.alias in donos_alias and donos_alias[regra.alias] != cat.arquivo:
                    achados.append(
                        f"{regra.alias}: `alias` repetido em {donos_alias[regra.alias]} e {cat.arquivo}"
                    )
                donos_alias.setdefault(regra.alias, cat.arquivo)
            try:
                atual = regra_sha256(raiz, regra)
            except FonteError as exc:
                achados.append(f"{cat.arquivo}: {regra.id}: fonte irresolvivel — {exc}")
                continue
            if atual != regra.sha256:
                achados.append(
                    f"{cat.arquivo}: {regra.id}: `sha256` declarado {regra.sha256[:12]}… "
                    f"!= recalculado {atual}. "
                    f"Se a mudanca e' intencional, declare o novo hash e acrescente a linha em "
                    f"`historico_de_mudanca`; a regra volta a validacao."
                )
    return catalogos, achados


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Chancela de regras: forma e hash do catalogo.").parse_args(argv)
    catalogos, achados = verificar()
    if achados:
        print("check_catalogo_regras: catalogo de regras com problema(s):", file=sys.stderr)
        for achado in achados:
            print(f"  - {achado}", file=sys.stderr)
        return 1
    total = sum(len(c.regras) for c in catalogos)
    sem_aprovacao = [
        f"{c.processo} {r.alias or r.id}" for c in catalogos for r in c.regras if not r.aprovacoes
    ]
    print(
        f"check_catalogo_regras: {len(catalogos)} catalogo(s), {total} regra(s), hash recalculado confere. "
        f"{len(sem_aprovacao)} regra(s) SEM aprovacao registrada (visiveis, nao bloqueiam ate' a onda 4). OK."
    )
    for item in sem_aprovacao:
        print(f"  sem aprovacao: {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
