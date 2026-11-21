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
O resumo SEMPRE diz se o Tier B rodou, quantos casos rodaram de quantos coletados, e o resultado de cada um
(so' o id do caso — nenhuma mensagem de falha, entrada ou saida do modelo vai para o resumo).

QUEBRADO (exit 1 nos DOIS modos):
  * junit ausente ou ilegivel; codigo do pytest fora de {0, 1}; zero casos coletados;
  * qualquer `<error>` no junit (fixture/setup);
  * chave PRESENTE e nenhum caso rodou (pulo total com chave = raia quebrada, nao "nao rodou");
  * qualquer falha que NAO seja de limiar (excecao de API, credencial, rede, vazamento, outra assercao);
  * 100% dos casos rodados reprovados (sinal de falha sistemica, nao de qualidade);
  * pytest exit 1 sem reprovacao no junit (inconsistente).
Reprovacao DE LIMIAR em parte dos casos: `TIER_B_BLOQUEANTE=true` falha; `false` (observacao) sai
`::warning::` e o passo sai 0. Sem a chave e com todos os casos pulados: "NAO RODOU", passo 0. Com a chave e
pulo parcial: "N de M rodaram" e `::warning::`. Sem a variavel `TIER_B_BLOQUEANTE`, o modo e' bloqueante.
Para voltar a bloquear: um PR de uma linha, `TIER_B_BLOQUEANTE: "true"` no passo.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Assinatura das reprovacoes DE LIMIAR, ancorada no INICIO da mensagem da falha (atributo `message` do
#: junit): `_harness.assert_live_score` e o limiar da extracao ao vivo. Ancorar impede que uma assercao
#: qualquer cuja mensagem carregue estado/saida do modelo contendo o texto seja rebaixada a "limiar".
_FALHA_DE_LIMIAR = re.compile(
    r"^AssertionError: (TH violation: live score [\d.]+ below threshold [\d.]+$"
    r"|extracao em [\d.]+, abaixo do limiar de partida )"
)

APROVADO, PULADO, LIMIAR, FALHA, ERRO = "aprovado", "pulado", "abaixo do limiar", "falha", "erro"


@dataclass
class Contagem:
    casos: list[tuple[str, str]] = field(default_factory=list)  # (id, resultado)

    def total(self, *resultados: str) -> int:
        return sum(1 for _, r in self.casos if not resultados or r in resultados)


def ler_junit(caminho: Path) -> Contagem:
    raiz = ET.parse(caminho).getroot()
    c = Contagem()
    for caso in raiz.iter("testcase"):
        ident = f"{caso.get('classname', '')}::{caso.get('name', '')}"
        falha = caso.find("failure")
        if caso.find("error") is not None:
            resultado = ERRO
        elif falha is not None:
            resultado = LIMIAR if _FALHA_DE_LIMIAR.match(falha.get("message") or "") else FALHA
        elif caso.find("skipped") is not None:
            resultado = PULADO
        else:
            resultado = APROVADO
        c.casos.append((ident, resultado))
    return c


@dataclass(frozen=True)
class Veredito:
    saida: int
    manchete: str
    avisos: tuple[str, ...] = ()


def decidir(codigo: int, contagem: Contagem | None, *, chave_presente: bool, bloqueante: bool) -> Veredito:
    """Pura, para os testes. Ver o contrato no topo do modulo."""
    if contagem is None:
        return Veredito(1, f"Tier B QUEBRADO: junit ausente ou ilegivel (pytest exit {codigo})")
    total = contagem.total()
    if codigo not in (0, 1):
        return Veredito(1, f"Tier B QUEBRADO: pytest exit {codigo} ({total} caso(s) no junit)")
    if total == 0:
        return Veredito(1, f"Tier B QUEBRADO: nenhum caso coletado (pytest exit {codigo})")
    erros = contagem.total(ERRO)
    if erros:
        return Veredito(1, f"Tier B QUEBRADO: {erros} caso(s) com erro de fixture/setup")
    rodados = total - contagem.total(PULADO)
    if rodados == 0:
        if chave_presente:
            return Veredito(1, f"Tier B QUEBRADO: chave presente e nenhum dos {total} caso(s) rodou")
        return Veredito(
            0, f"Tier B NAO RODOU: segredo MAEZO_ANTHROPIC_API_KEY ausente ({total} caso(s) pulado(s))"
        )
    avisos: tuple[str, ...] = ()
    if rodados < total:
        avisos = (f"pulo parcial: {rodados} de {total} caso(s) rodaram",)
    falhas, limiar = contagem.total(FALHA), contagem.total(LIMIAR)
    if falhas:
        return Veredito(
            1,
            f"Tier B QUEBRADO: {falhas} caso(s) com falha que nao e' de limiar (API, credencial, assercao)",
            avisos,
        )
    if limiar == rodados:
        return Veredito(
            1, f"Tier B QUEBRADO: todos os {rodados} caso(s) rodados reprovaram (falha sistemica)", avisos
        )
    if codigo == 1 and limiar == 0:
        return Veredito(1, "Tier B QUEBRADO: pytest exit 1 sem reprovacao no junit", avisos)
    rodaram = f"{rodados} de {total} rodaram"
    if limiar == 0:
        return Veredito(0, f"Tier B RODOU: {rodaram}, todos aprovados", avisos)
    if bloqueante:
        return Veredito(1, f"Tier B RODOU: {rodaram}, {limiar} abaixo do limiar — BLOQUEANTE", avisos)
    return Veredito(
        0,
        f"Tier B RODOU: {rodaram}, {limiar} abaixo do limiar — observacao, nao bloqueia",
        (*avisos, f"{limiar} caso(s) abaixo do limiar (modo observacao)"),
    )


def _resumo(v: Veredito, contagem: Contagem | None, codigo: int, bloqueante: bool) -> str:
    modo = "bloqueante" if bloqueante else "observacao (TIER_B_BLOQUEANTE=false)"
    linhas = [f"## evals-nightly — {v.manchete}", "", f"modo: {modo}; pytest exit {codigo}"]
    linhas += [f"- aviso: {a}" for a in v.avisos]
    if os.environ.get("TIER_B_NOITES_CONTAM", "false").strip().lower() != "true":
        linhas.append(
            "- as 7 noites de observacao NAO contam enquanto o conserto dos evals vivos nao entrar "
            "(TIER_B_NOITES_CONTAM=false)"
        )
    if contagem is not None:
        rodados = contagem.total() - contagem.total(PULADO)
        linhas += [
            "",
            f"coletados {contagem.total()} · rodados {rodados} · aprovados {contagem.total(APROVADO)} · "
            f"abaixo do limiar {contagem.total(LIMIAR)} · falha {contagem.total(FALHA)} · "
            f"erro {contagem.total(ERRO)} · pulados {contagem.total(PULADO)}",
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
    v = decidir(a.codigo, contagem, chave_presente=chave_presente, bloqueante=bloqueante)
    texto = _resumo(v, contagem, a.codigo, bloqueante)
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto)
    print(texto)
    for aviso in v.avisos:
        print(f"::warning::Tier B: {aviso}")
    print(f"::{'error' if v.saida else 'notice'}::{v.manchete}")
    return v.saida


if __name__ == "__main__":
    sys.exit(main())
