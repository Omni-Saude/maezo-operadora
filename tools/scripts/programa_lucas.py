"""Roda o programa de teste do Lucas isolado e grava um JSONL (plano lucas-numero-unico §6(a)).

Uso, na raiz do repo:

    uv run python tools/scripts/programa_lucas.py --saida programa_lucas.jsonl
    uv run python tools/scripts/programa_lucas.py --dmn engine --engine-url http://localhost:8080/engine-rest

Uma linha por caso de `tests/evals/lucas/casos.json`, na ordem do corpus, com o registro de
`tests/evals/lucas/programa.py::executar_caso` (rota, motivo, desfecho, DMN avaliadas, textos
enviados, divergencias). O registro nao carrega pseudonimo nem hash de destino. O JSONL alimenta o
Excel da onda (g).

`--dmn local` (padrao) le as DMN DRAFT do XML em `spec/`; `--dmn engine` avalia no CIB Seven, que
precisa estar no ar e com as tabelas implantadas. Caso que declara `"dmn": "indisponivel"` usa a
DMN fora do ar nos dois modos.

Saida 0 = todos os casos sem divergencia; 1 = pelo menos uma divergencia (o JSONL e' gravado do
mesmo jeito, para mostrar qual).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:
    # O programa mora em `tests/evals/lucas/` (os dubles dele sao de teste e nao entram em `src/`).
    sys.path.insert(0, str(_RAIZ))

from tests.evals.lucas.programa import DmnDraftLocal, executar_programa  # noqa: E402

from maezo.platform.deploy.engine_deploy import resolve_engine_rest_url  # noqa: E402
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport, DmnTransport  # noqa: E402


async def rodar(*, saida: Path, dmn_modo: str, engine_url: str | None) -> int:
    dmn: DmnTransport
    if dmn_modo == "engine":
        dmn = CibSevenDmnTransport(engine_url or resolve_engine_rest_url(), timeout=30.0)
    else:
        dmn = DmnDraftLocal()
    try:
        registros = await executar_programa(dmn=dmn)
    finally:
        await dmn.close()

    saida.parent.mkdir(parents=True, exist_ok=True)
    with saida.open("w", encoding="utf-8", newline="\n") as arquivo:
        for registro in registros:
            arquivo.write(json.dumps(registro, ensure_ascii=False, sort_keys=True) + "\n")

    falhas = [r for r in registros if not r["ok"]]
    ok = len(registros) - len(falhas)
    print(f"programa_lucas: {len(registros)} casos, {ok} ok, {len(falhas)} com divergencia")
    for r in falhas:
        print(f"  {r['caso']}: {'; '.join(r['divergencias'])}")
    print(f"programa_lucas: JSONL em {saida}")
    return 1 if falhas else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--saida", type=Path, default=Path("programa_lucas.jsonl"))
    parser.add_argument("--dmn", choices=("local", "engine"), default="local")
    parser.add_argument("--engine-url", default=None)
    args = parser.parse_args(argv)
    return asyncio.run(rodar(saida=args.saida, dmn_modo=args.dmn, engine_url=args.engine_url))


if __name__ == "__main__":
    raise SystemExit(main())
