#!/usr/bin/env python3
"""Preflight de vivacidade do Tier B (evals ao vivo) no `evals-nightly` do ci.yml.

Por que
-------
Os grafos ao vivo ENGOLEM falha de API/credencial/rede (degradam para `falha_tecnica`), e o teste entao
reprova "por limiar" — uma API morta pareceria queda de qualidade. Ate' os testes de eval distinguirem a
falha da chamada, este preflight faz UMA chamada minima pelo MESMO caminho dos evals
(`InferenceProvider(InferenceSettings(provider="anthropic"))`, modelo padrao) antes da suite.

Contrato
--------
* Sem `MAEZO_ANTHROPIC_API_KEY`/`ANTHROPIC_API_KEY`: nao chama nada, sai 0 (o resumo do Tier B dira'
  "NAO RODOU").
* Com chave: chamada falhou ou resposta vazia => "Tier B QUEBRADO: preflight ..." no resumo, `::error::` e
  saida 1, nos dois modos. A mensagem traz SO' a classe do erro e o status HTTP — nunca a chave, nunca o
  corpo da resposta nem a mensagem do provedor.
* Pega a API morta no INICIO da noite; um 429 no meio da suite continua dependendo do conserto dos evals.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

PROMPT = "Responda apenas com a palavra: ok"


def _status(erro: BaseException) -> str:
    for e in (erro, erro.__cause__):
        codigo = getattr(e, "status_code", None)
        if isinstance(codigo, int):
            return str(codigo)
    return "sem status"


def _provedor_padrao() -> Any:
    from maezo.runtime.inference import InferenceProvider
    from maezo.runtime.inference.settings import InferenceSettings

    return InferenceProvider(settings=InferenceSettings(provider="anthropic"))


def _escrever_resumo(texto: str) -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def main(argv: Sequence[str] | None = None, *, fabrica: Callable[[], Any] = _provedor_padrao) -> int:
    del argv
    if not (os.environ.get("MAEZO_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        print("preflight do Tier B: sem chave, nenhuma chamada feita")
        return 0
    try:
        resposta = asyncio.run(fabrica().generate(PROMPT, agent_id="ci-tier-b-preflight"))
    except Exception as erro:  # qualquer falha do provedor = raia quebrada; so' classe e status saem
        manchete = f"Tier B QUEBRADO: preflight falhou ({type(erro).__name__}, status {_status(erro)})"
    else:
        if isinstance(resposta, str) and resposta.strip():
            print("preflight do Tier B: provedor respondeu")
            return 0
        manchete = "Tier B QUEBRADO: preflight sem resposta do provedor (texto vazio)"
    _escrever_resumo(f"## evals-nightly — {manchete}")
    print(f"::error::{manchete}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
