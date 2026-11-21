#!/usr/bin/env python3
"""Resumo e veredito do Tier B (eval ao vivo contra modelo real) no `evals-nightly` do ci.yml.

Por que
-------
O Tier B (`-m "eval and llm_live"`) so' roda com opt-in de execucao (`TIER_B_EXECUCAO` = noturno | manual) e
credencial pretendida (`tests/evals/_regra_viva.py`); senao cada caso e' pulado pelo `live_key_skip`. Os
grafos chamam o modelo com `phi=True`: so' um provedor da zona PHI responde, e o CI nao tem um — no estado
normal o noturno diz "NAO RODOU: nao roda no CI" e aponta a execucao manual. Um caso abaixo do limiar NAO pode
virar job vermelho nas noites de observacao (o `red-main-alarm.yml` le o noturno), suspensas ate' existir
provedor na zona PHI no CI.

Contrato (nunca "verde por pular")
----------------------------------
O resumo SEMPRE diz se o Tier B rodou, quantos casos rodaram de quantos coletados, e o resultado de cada um
(so' o id do caso — nenhuma mensagem de falha, entrada ou saida do modelo vai para o resumo).

QUEBRADO (exit 1 nos DOIS modos):
  * junit ausente ou ilegivel; codigo do pytest fora de {0, 1}; zero casos coletados;
  * qualquer `<error>` no junit (fixture/setup);
  * sem opt-in de execucao (o passo do noturno/manual tem de definir `TIER_B_EXECUCAO`);
  * credencial PRESENTE e nenhum caso rodou (pulo total com credencial = raia quebrada, nao "nao rodou");
  * qualquer falha que NAO seja de limiar: `LIVE CALL FAILED` (a chamada ao provedor falhou) e as OUTRAS
    (vazamento, outra assercao) contadas e nomeadas em separado — uma nunca engole a outra;
  * 100% dos casos rodados reprovados (sinal de falha sistemica, nao de qualidade);
  * pytest exit 1 sem reprovacao no junit (inconsistente).
Reprovacao DE LIMIAR em parte dos casos: `TIER_B_BLOQUEANTE=true` falha; `false` (observacao) sai
`::warning::` e o passo sai 0. Sem credencial e com todos os casos pulados: "NAO RODOU" (nao roda no CI;
execucao manual), passo 0 — sem convidar a criar segredo. Com credencial e pulo parcial: "N de M rodaram" e
`::warning::`. Sem a variavel `TIER_B_BLOQUEANTE`, o modo e' bloqueante. Para voltar a bloquear: um PR de uma
linha, `TIER_B_BLOQUEANTE: "true"` no passo.
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

_RAIZ = str(Path(__file__).resolve().parents[2])
if _RAIZ not in sys.path:  # `python -I` nao poe o diretorio do script nem o cwd no sys.path
    sys.path.insert(0, _RAIZ)

# A REGRA (opt-in + credencial) e' importada, nunca copiada: `_regra_viva` so' usa a biblioteca padrao, entao
# o resumo reporta mesmo com o pacote `maezo` quebrado.
from tests.evals._regra_viva import (  # noqa: E402
    EXECUCAO_MANUAL,
    credencial_viva_presente,
    execucao_viva,
)

#: Assinatura das reprovacoes DE LIMIAR, ancorada no INICIO da mensagem da falha (atributo `message` do
#: junit): `_harness.assert_live_score` e o limiar da extracao ao vivo. Ancorar impede que uma assercao
#: qualquer cuja mensagem carregue estado/saida do modelo contendo o texto seja rebaixada a "limiar".
#: O fim da linha do TH aceita `\n` (mensagem com explicacao do assert rewriting do pytest), nao so' `$`.
_FALHA_DE_LIMIAR = re.compile(
    r"^AssertionError: (TH violation: live score [\d.]+ below threshold [\d.]+(?:\n|$)"
    r"|extracao em [\d.]+, abaixo do limiar de partida )"
)
#: Mensagem estavel de `tests/evals/_vivo.py`: a CHAMADA ao provedor falhou (nunca e' "limiar").
_FALHA_VIVA = re.compile(r"^AssertionError: LIVE CALL FAILED: (\w+)")
ERRO_ZONA_PHI = "PhiZoneRoutingError"
#: Linhas estaveis do resumo (o preflight usa as mesmas).
#: Onde esta' o roteiro da execucao manual do Tier B na zona PHI do dev (fora do CI).
ROTEIRO_MANUAL = "tests/evals/README.md#tier-b-execucao-manual-na-zona-phi-do-dev"
LINHA_ZONA_PHI = (
    "Tier B nao roda no CI: exige provedor na zona PHI (o provedor configurado nao e' phi_capable); "
    f"use a execucao manual ({ROTEIRO_MANUAL})"
)
LINHA_NOITES_NAO_CONTAM = (
    "as noites de observacao ficam suspensas ate existir provedor na zona PHI no CI "
    "(TIER_B_NOITES_CONTAM=false)"
)
LINHA_EXECUCAO_MANUAL = (
    "execucao MANUAL na zona PHI do dev (scripts/dev/tier_b_manual.py), fora do CI: "
    "nao e' noite de observacao"
)
MANCHETE_NAO_RODOU_CI = (
    f"Tier B NAO RODOU: nao roda no CI — exige provedor na zona PHI; execucao manual em {ROTEIRO_MANUAL}"
)


def execucao_manual() -> bool:
    return execucao_viva() == EXECUCAO_MANUAL


def noites_contam() -> bool:
    return os.environ.get("TIER_B_NOITES_CONTAM", "false").strip().lower() == "true"


#: Ha' credencial pretendida para um eval vivo: o MESMO objeto de `tests/evals/_regra_viva.py`.
chave_presente = credencial_viva_presente


APROVADO, PULADO, LIMIAR, FALHA, ERRO = "aprovado", "pulado", "abaixo do limiar", "falha", "erro"
#: Falha `LIVE CALL FAILED: <Classe>` (a chamada ao provedor falhou): contada a parte de FALHA.
VIVA = "LIVE CALL FAILED"


@dataclass
class Contagem:
    casos: list[tuple[str, str]] = field(default_factory=list)  # (id, resultado)
    classes_vivas: set[str] = field(default_factory=set)  # classes de "LIVE CALL FAILED: <classe>"

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
            mensagem = falha.get("message") or ""
            viva = _FALHA_VIVA.match(mensagem)
            if viva:
                c.classes_vivas.add(viva.group(1))
            resultado = VIVA if viva else LIMIAR if _FALHA_DE_LIMIAR.match(mensagem) else FALHA
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


def decidir(
    codigo: int,
    contagem: Contagem | None,
    *,
    chave_presente: bool,
    bloqueante: bool,
    execucao: str | None = None,
) -> Veredito:
    """Pura, para os testes. Ver o contrato no topo do modulo."""
    if execucao is None:
        return Veredito(1, "Tier B QUEBRADO: sem opt-in de execucao (TIER_B_EXECUCAO=noturno|manual ausente)")
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
            return Veredito(1, f"Tier B QUEBRADO: credencial presente e nenhum dos {total} caso(s) rodou")
        if execucao == EXECUCAO_MANUAL:
            return Veredito(
                0, f"Tier B NAO RODOU na execucao manual: sem credencial viva ({total} pulado(s))"
            )
        return Veredito(0, f"{MANCHETE_NAO_RODOU_CI} ({total} caso(s) pulado(s))")
    avisos: tuple[str, ...] = ()
    if rodados < total:
        avisos = (f"pulo parcial: {rodados} de {total} caso(s) rodaram",)
    vivas, falhas, limiar = contagem.total(VIVA), contagem.total(FALHA), contagem.total(LIMIAR)
    outras = f"{falhas} caso(s) com outra falha que nao e' de limiar (vazamento, assercao)"
    if vivas:
        classes = ", ".join(sorted(contagem.classes_vivas))
        manchete = f"Tier B QUEBRADO: {vivas} caso(s) com LIVE CALL FAILED ({classes})"
        return Veredito(1, f"{manchete} e {outras}" if falhas else manchete, avisos)
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
    if contagem is not None and ERRO_ZONA_PHI in contagem.classes_vivas:
        linhas.append(f"- {LINHA_ZONA_PHI}")
    if execucao_manual():
        linhas.append(f"- {LINHA_EXECUCAO_MANUAL}")
    elif not noites_contam():
        linhas.append(f"- {LINHA_NOITES_NAO_CONTAM}")
    if contagem is not None:
        rodados = contagem.total() - contagem.total(PULADO)
        linhas += [
            "",
            f"coletados {contagem.total()} · rodados {rodados} · aprovados {contagem.total(APROVADO)} · "
            f"abaixo do limiar {contagem.total(LIMIAR)} · live call failed {contagem.total(VIVA)} · "
            f"falha {contagem.total(FALHA)} · "
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
    try:
        contagem: Contagem | None = ler_junit(a.junit)
    except (OSError, ET.ParseError):
        contagem = None
    v = decidir(
        a.codigo, contagem, chave_presente=chave_presente(), bloqueante=bloqueante, execucao=execucao_viva()
    )
    texto = _resumo(v, contagem, a.codigo, bloqueante)
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto)
    print(texto)
    for aviso in v.avisos:
        print(f"::warning::Tier B: {aviso}")
    print(f"::{'error' if v.saida else 'notice'}::{v.manchete}")
    if contagem is not None and ERRO_ZONA_PHI in contagem.classes_vivas:
        print(f"::error::{LINHA_ZONA_PHI}")
    return v.saida


if __name__ == "__main__":
    sys.exit(main())
