#!/usr/bin/env python3
"""Preflight de vivacidade do Tier B (evals ao vivo) no `evals-nightly` do ci.yml.

Por que
-------
Os grafos ao vivo ENGOLEM falha de API/credencial/rede/zona (degradam para `falha_tecnica`, reenvio ou
"narrativa indisponivel"). Este preflight faz UMA chamada minima pela MESMA rota da suite: o provedor de
`tests/evals/_vivo.py::provedor_vivo()` (a configuracao NAO e' duplicada aqui) e `phi=True`, como os grafos
chamam. Com o provedor `anthropic` atual (nao `phi_capable`) a fachada recusa antes da rede
(`PhiZoneRoutingError`, ADR-0006): o Tier B exige um provedor na zona PHI.

Contrato
--------
* Sempre importa o provedor dos evals (prova, ate' sem chave, que `python -I` acha `maezo` no venv do job).
* Sem `MAEZO_ANTHROPIC_API_KEY`/`ANTHROPIC_API_KEY`: nao chama nada, sai 0 (o resumo dira' "NAO RODOU").
* Com chave: chamada falhou ou resposta vazia => "Tier B QUEBRADO: preflight ..." no resumo, `::error::` e
  saida 1, nos dois modos; com `PhiZoneRoutingError`, tambem a linha estavel da zona PHI. So' a classe do
  erro e o status HTTP — nunca a chave, nunca o corpo da resposta nem a mensagem do provedor. O resumo
  tambem diz, enquanto `TIER_B_NOITES_CONTAM` nao for "true", que a noite nao conta para a observacao.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:  # `python -I` nao poe o diretorio do script nem o cwd no sys.path
    sys.path.insert(0, str(_RAIZ))

from scripts.ci.tier_b_resumo import LINHA_NOITES_NAO_CONTAM, LINHA_ZONA_PHI, noites_contam  # noqa: E402

PROMPT = "Responda apenas com a palavra: ok"
ERRO_ZONA_PHI = "PhiZoneRoutingError"


def _status(erro: BaseException) -> str:
    for e in (erro, erro.__cause__):
        codigo = getattr(e, "status_code", None)
        if isinstance(codigo, int):
            return str(codigo)
    return "sem status"


def _provedor_dos_evals() -> Callable[[], Any]:
    from tests.evals._vivo import provedor_vivo

    return provedor_vivo


def _escrever_resumo(linhas: list[str]) -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")


def main(argv: Sequence[str] | None = None, *, fabrica: Callable[[], Any] | None = None) -> int:
    del argv
    fabrica = fabrica or _provedor_dos_evals()
    if not (os.environ.get("MAEZO_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        print("preflight do Tier B: sem chave, nenhuma chamada feita")
        return 0
    zona_phi = False
    try:
        resposta = asyncio.run(fabrica().generate(PROMPT, phi=True, agent_id="ci-tier-b-preflight"))
    except Exception as erro:  # qualquer falha do provedor = raia quebrada; so' classe e status saem
        zona_phi = type(erro).__name__ == ERRO_ZONA_PHI
        manchete = f"Tier B QUEBRADO: preflight falhou ({type(erro).__name__}, status {_status(erro)})"
    except BaseException as erro:  # cancelamento do runner: mesma linha curta, e propaga
        print(f"::error::Tier B QUEBRADO: preflight interrompido ({type(erro).__name__})")
        raise
    else:
        if isinstance(resposta, str) and resposta.strip():
            print("preflight do Tier B: provedor respondeu")
            return 0
        manchete = "Tier B QUEBRADO: preflight sem resposta do provedor (texto vazio)"
    linhas = [f"## evals-nightly — {manchete}", ""]
    if zona_phi:
        linhas.append(f"- {LINHA_ZONA_PHI}")
    if not noites_contam():
        linhas.append(f"- {LINHA_NOITES_NAO_CONTAM}")
    _escrever_resumo(linhas)
    print(f"::error::{manchete}")
    if zona_phi:
        print(f"::error::{LINHA_ZONA_PHI}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
