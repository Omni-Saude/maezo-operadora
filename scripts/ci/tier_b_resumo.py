#!/usr/bin/env python3
"""Resumo e veredito do Tier B (eval ao vivo contra modelo real) no `evals-nightly` do ci.yml.

Por que
-------
O Tier B (`-m "eval and llm_live"`) so' roda com `MAEZO_ANTHROPIC_API_KEY`; sem ela cada caso e' pulado pelo
`live_key_skip` de `tests/evals/conftest.py`. Ate' 21/11/2026 o job passava outro nome de segredo e o Tier B
nunca rodou. Nas primeiras noites com a chave, um caso abaixo do limiar NAO pode virar job vermelho (o
`red-main-alarm.yml` le o noturno) — decisao da ROOT: observar 7 noites antes de bloquear.

Contrato (nunca "verde por pular")
----------------------------------
* O resumo do passo SEMPRE diz se o Tier B rodou, quantos casos foram coletados/rodados/aprovados/reprovados/
  pulados, e lista cada caso com o resultado.
* Erro de coleta ou interno do pytest (exit 2, 3, 4), nenhum caso coletado (exit 5 ou junit vazio) ou junit
  ilegivel: o passo FALHA nos dois modos. Uma raia quebrada nunca vira verde.
* Caso reprovado (exit 1): com `TIER_B_BLOQUEANTE=true` o passo falha; com `false` (observacao) sai um
  `::warning::` e o passo sai 0.
* Sem a chave: os casos aparecem como pulados e o resumo diz "NAO RODOU"; o passo sai 0, como antes.
Para voltar a bloquear: um PR de uma linha, `TIER_B_BLOQUEANTE: "true"` no passo.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

_ERROS_DO_PYTEST = {2, 3, 4, 5}


@dataclass
class Contagem:
    casos: list[tuple[str, str]] = field(default_factory=list)  # (id, resultado)

    def total(self, resultado: str | None = None) -> int:
        return sum(1 for _, r in self.casos if resultado is None or r == resultado)


def ler_junit(caminho: Path) -> Contagem:
    raiz = ET.parse(caminho).getroot()
    c = Contagem()
    for caso in raiz.iter("testcase"):
        ident = f"{caso.get('classname', '')}::{caso.get('name', '')}"
        if caso.find("failure") is not None or caso.find("error") is not None:
            resultado = "reprovado"
        elif caso.find("skipped") is not None:
            resultado = "pulado"
        else:
            resultado = "aprovado"
        c.casos.append((ident, resultado))
    return c


def decidir(
    codigo: int, contagem: Contagem | None, *, chave_presente: bool, bloqueante: bool
) -> tuple[int, str]:
    """Devolve (exit code do passo, manchete). Pura, para os testes."""
    if contagem is None:
        return 1, f"Tier B QUEBRADO: junit ausente ou ilegivel (pytest exit {codigo})"
    if codigo in _ERROS_DO_PYTEST or contagem.total() == 0:
        return 1, f"Tier B QUEBRADO: pytest exit {codigo}, {contagem.total()} caso(s) coletado(s)"
    rodados = contagem.total() - contagem.total("pulado")
    reprovados = contagem.total("reprovado")
    if rodados == 0:
        motivo = "segredo MAEZO_ANTHROPIC_API_KEY ausente" if not chave_presente else "todos os casos pulados"
        return 0, f"Tier B NAO RODOU: {motivo} ({contagem.total()} caso(s) pulado(s))"
    if codigo == 0 and reprovados == 0:
        return 0, f"Tier B RODOU: {rodados} caso(s), todos aprovados"
    if bloqueante:
        return 1, f"Tier B RODOU: {reprovados} de {rodados} caso(s) abaixo do limiar — BLOQUEANTE"
    return 0, f"Tier B RODOU: {reprovados} de {rodados} caso(s) abaixo do limiar — observacao, nao bloqueia"


def _resumo(manchete: str, contagem: Contagem | None, codigo: int, bloqueante: bool) -> str:
    linhas = [f"## evals-nightly — {manchete}", ""]
    modo = "bloqueante" if bloqueante else "observacao (TIER_B_BLOQUEANTE=false)"
    linhas.append(f"modo: {modo}; pytest exit {codigo}")
    if contagem is not None:
        linhas += [
            "",
            f"coletados {contagem.total()} · rodados {contagem.total() - contagem.total('pulado')} · "
            f"aprovados {contagem.total('aprovado')} · reprovados {contagem.total('reprovado')} · "
            f"pulados {contagem.total('pulado')}",
            "",
            "| caso | resultado |",
            "|---|---|",
            *[f"| `{i}` | {r} |" for i, r in contagem.casos],
        ]
    return "\n".join(linhas) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--junit", type=Path, required=True)
    p.add_argument("--codigo", type=int, required=True, help="exit code do pytest do Tier B")
    a = p.parse_args(argv)
    bloqueante = os.environ.get("TIER_B_BLOQUEANTE", "true").strip().lower() != "false"
    chave_presente = bool(os.environ.get("MAEZO_ANTHROPIC_API_KEY"))
    try:
        contagem: Contagem | None = ler_junit(a.junit)
    except (OSError, ET.ParseError):
        contagem = None
    saida, manchete = decidir(a.codigo, contagem, chave_presente=chave_presente, bloqueante=bloqueante)
    texto = _resumo(manchete, contagem, a.codigo, bloqueante)
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto)
    print(texto)
    if saida != 0:
        print(f"::error::{manchete}")
    elif "abaixo do limiar" in manchete:
        print(f"::warning::{manchete}")
    else:
        print(f"::notice::{manchete}")
    return saida


if __name__ == "__main__":
    sys.exit(main())
