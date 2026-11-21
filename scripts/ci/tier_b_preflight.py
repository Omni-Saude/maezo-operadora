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
* Sem chave (`MAEZO_ANTHROPIC_API_KEY`/`ANTHROPIC_API_KEY`) E sem provedor real nomeado alem de `anthropic`
  (`MAEZO_INFERENCE_PROVIDER`): nao chama nada, sai 0 (o resumo dira' "NAO RODOU"). Provedor real nomeado
  sem credencial utilizavel (ex.: `bedrock_br` sem os portoes): QUEBRADO, como qualquer falha de chamada.
* Com credencial (chave ou provedor nomeado): chamada ou construcao do provedor falhou ou resposta vazia =>
  "Tier B QUEBRADO: preflight ..." no resumo, `::error::` e
  saida 1, nos dois modos; com `PhiZoneRoutingError`, tambem a linha estavel da zona PHI. So' a classe do
  erro e o status HTTP — nunca a chave, nunca o corpo da resposta nem a mensagem do provedor. O resumo
  tambem diz, enquanto `TIER_B_NOITES_CONTAM` nao for "true", que a noite nao conta para a observacao.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:  # `python -I` nao poe o diretorio do script nem o cwd no sys.path
    sys.path.insert(0, str(_RAIZ))

from scripts.ci.tier_b_resumo import LINHA_NOITES_NAO_CONTAM, LINHA_ZONA_PHI, noites_contam  # noqa: E402

PROMPT = "Responda apenas com a palavra: ok"
_FALHA_VIVA = re.compile(r"^LIVE CALL FAILED: (\w+)")
ERRO_ZONA_PHI = "PhiZoneRoutingError"


def _status(erro: BaseException) -> str:
    for e in (erro, erro.__cause__):
        codigo = getattr(e, "status_code", None)
        if isinstance(codigo, int):
            return str(codigo)
    return "sem status"


def _classe(erro: BaseException) -> str:
    # `provedor_vivo()` falha na CONSTRUCAO com `AssertionError("LIVE CALL FAILED: <Classe> ...")` (provedor
    # real nomeado sem credencial utilizavel, ou `ProvedorNaoVivo`): o preflight nomeia a MESMA classe que o
    # resumo le no junit. So' a classe — a mensagem do provedor fica na causa encadeada, nunca sai.
    viva = _FALHA_VIVA.match(str(erro)) if isinstance(erro, AssertionError) else None
    return viva.group(1) if viva else type(erro).__name__


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
    from tests.evals._vivo import credencial_viva_presente, provedor_configurado

    if not credencial_viva_presente():
        print("preflight do Tier B: sem chave, nenhuma chamada feita")
        return 0
    print(f"preflight do Tier B: provedor {provedor_configurado()}")
    zona_phi = False
    try:
        resposta = asyncio.run(fabrica().generate(PROMPT, phi=True, agent_id="ci-tier-b-preflight"))
    except Exception as erro:  # qualquer falha do provedor = raia quebrada; so' classe e status saem
        zona_phi = _classe(erro) == ERRO_ZONA_PHI
        manchete = f"Tier B QUEBRADO: preflight falhou ({_classe(erro)}, status {_status(erro)})"
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
