#!/usr/bin/env python3
"""CI gate: nenhuma regra publicavel sai sem estar no registro de aprovacoes (DL-0148).

POR QUE ESTE ARQUIVO EXISTE
---------------------------
Pendencia 27 da planilha do diretor: *"Registro de quem aprovou: nao existe; o sistema publica regra
em rascunho sem conferir."* Medido em 19/10/2026: `python -m maezo.platform.deploy` envia ao motor
TODO `*.bpmn` de `spec/processes/bpmn/` e TODO `*.dmn` de `spec/processes/dmn/`
(`engine_deploy.collect_artifacts`), sem olhar status. O status de cada artefato vive em TEXTO LIVRE
(`status: DRAFT`, `DRAFT/verify`, `SINTETICO`, `NAO RATIFICADO`), e nenhum arquivo do repositorio
dizia, por artefato, quem aprovou o que e em que versao dos bytes.

O REGISTRO: `spec/aprovacoes-de-regras.yaml`. Cada artefato publicavel aparece em EXATAMENTE uma de
duas listas:

  * `aprovados` — `artefato`, `sha256` (dos bytes aprovados, fim de linha normalizado para LF),
    `aprovado_por`, `papel`, `aprovado_em` (AAAA-MM-DD), `evidencia`. A aprovacao vale para AQUELES
    bytes: mudou o arquivo, mudou o hash, a cerca reprova ate' que alguem aprove de novo.
  * `pendentes` — o que esta publicado SEM aprovacao registrada, dito em voz alta, com `responsavel`
    (pode ser "a definir"). Passa na cerca — o deploy do dev nao para — mas aparece em toda execucao.

O QUE ESTA CERCA REPROVA
------------------------
  1. artefato publicavel que nao esta em nenhuma das listas (o rascunho novo que entraria no motor
     calado). Apagar a linha de um pendente cai aqui: nao e' um jeito de sair da lista;
  2. artefato aprovado cujo hash atual nao bate com o aprovado (editaram regra aprovada);
  3. aprovacao incompleta (sem quem, sem papel conhecido, sem data, data no futuro, hash malformado);
  4. entrada que aponta para arquivo que nao e' publicavel (apagado/renomeado) e entrada duplicada.

O QUE ELA NAO FAZ, e e' deliberado: nao aprova nada, nao escreve no registro e nao bloqueia o deploy.
Aprovar e' ato humano registrado em PR revisado (ADR-0007: sem auto-certificacao). A cerca so' garante
que a pergunta "quem aprovou estes bytes?" tem resposta escrita — ou a confissao escrita de que nao tem.

LIMITE CONHECIDO: so' cobre o que o deploy publica (`.bpmn`/`.dmn`). Limiar que mora em configuracao
Python (ex.: `MAEZO_LUCAS_ATRASO_MESES_MIN`/`_DIAS_MIN`) nao passa por aqui.

Uso:
    python scripts/ci/check_aprovacao_de_regras.py
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_REGISTRO: Final[Path] = _REPO_ROOT / "spec" / "aprovacoes-de-regras.yaml"
_PROCESSES: Final[Path] = _REPO_ROOT / "spec" / "processes"

#: Mesmos papeis do gate de sign-off de contratos (`validation/signoff.py::VALID_ROLES`) mais `dono`,
#: que ja' ratificou regra neste repo (`triage-redflag-pediatric-neonatal-ratification.yaml`).
PAPEIS_VALIDOS: Final[frozenset[str]] = frozenset(
    {"medico-auditor", "juridico", "dpo", "regulatorio", "financas", "po", "dono"}
)

#: Marcas de rascunho no texto do artefato — so' para INFORMAR na saida; a regra da cerca nao
#: depende delas (todo publicavel tem de estar no registro, com ou sem marca).
_MARCA_RASCUNHO: Final[re.Pattern[str]] = re.compile(r"\bDRAFT\b|SINTETIC|NAO RATIFICAD")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


def artefatos_publicaveis(processes_dir: Path = _PROCESSES) -> list[str]:
    """O que o deploy publica — o mesmo glob de `engine_deploy.collect_artifacts`, como caminho
    relativo a raiz do repo (POSIX). Um teste prova que os dois conjuntos nao divergem."""
    raiz = processes_dir.parents[1]
    achados = sorted((processes_dir / "bpmn").glob("*.bpmn")) + sorted((processes_dir / "dmn").glob("*.dmn"))
    return [p.relative_to(raiz).as_posix() for p in achados]


def sha256_normalizado(caminho: Path) -> str:
    """sha256 dos bytes com CRLF -> LF: o checkout com `core.autocrlf=true` (Windows) e o do CI
    (Linux) tem de dar o MESMO hash para o mesmo commit."""
    return hashlib.sha256(caminho.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def carregar(registro: Path = _REGISTRO) -> dict[str, Any]:
    if not registro.exists():
        raise SystemExit(f"ERRO: {registro} nao existe.")
    dados = yaml.safe_load(registro.read_text(encoding="utf-8"))
    if not isinstance(dados, dict):
        raise SystemExit(f"ERRO: {registro} nao e' um mapa YAML.")
    return dados


def _lista(dados: dict[str, Any], chave: str, achados: list[str]) -> list[dict[str, Any]]:
    bruto = dados.get(chave)
    if bruto is None:
        return []
    if not isinstance(bruto, list) or not all(isinstance(e, dict) for e in bruto):
        achados.append(f"`{chave}` tem de ser uma lista de mapas.")
        return []
    return bruto


def verificar(dados: dict[str, Any], *, raiz: Path = _REPO_ROOT, hoje: date | None = None) -> list[str]:
    """Devolve a lista de achados; vazia = verde."""
    hoje = hoje or date.today()
    achados: list[str] = []
    publicaveis = set(artefatos_publicaveis(raiz / "spec" / "processes"))
    aprovados = _lista(dados, "aprovados", achados)
    pendentes = _lista(dados, "pendentes", achados)

    vistos: dict[str, str] = {}
    for lista_nome, entradas in (("aprovados", aprovados), ("pendentes", pendentes)):
        for entrada in entradas:
            artefato = str(entrada.get("artefato", "")).strip()
            if not artefato:
                achados.append(f"{lista_nome}: entrada sem `artefato`.")
                continue
            if artefato in vistos:
                achados.append(
                    f"{artefato}: aparece duas vezes ({vistos[artefato]} e {lista_nome}) — um artefato "
                    f"esta' aprovado OU pendente, nunca os dois."
                )
                continue
            vistos[artefato] = lista_nome
            if artefato not in publicaveis:
                achados.append(
                    f"{artefato}: esta' em `{lista_nome}` mas nao e' artefato publicavel (apagado, "
                    f"renomeado ou fora de spec/processes/{{bpmn,dmn}}). Tire a entrada no mesmo PR."
                )

    for entrada in aprovados:
        artefato = str(entrada.get("artefato", "")).strip()
        if not artefato or artefato not in publicaveis:
            continue
        for campo in ("aprovado_por", "papel", "aprovado_em", "evidencia"):
            if not str(entrada.get(campo) or "").strip():
                achados.append(f"{artefato}: aprovacao sem `{campo}` — aprovacao sem isso nao e' registro.")
        papel = str(entrada.get("papel") or "").strip()
        if papel and papel not in PAPEIS_VALIDOS:
            achados.append(f"{artefato}: papel {papel!r} desconhecido (validos: {sorted(PAPEIS_VALIDOS)}).")
        bruto_data = entrada.get("aprovado_em")
        if bruto_data:
            try:
                quando = date.fromisoformat(str(bruto_data))
            except ValueError:
                achados.append(f"{artefato}: `aprovado_em={bruto_data!r}` nao e' data ISO (AAAA-MM-DD).")
            else:
                if quando > hoje:
                    achados.append(f"{artefato}: `aprovado_em` {quando.isoformat()} esta' no futuro.")
        esperado = str(entrada.get("sha256") or "").strip().lower()
        if not _SHA256_RE.match(esperado):
            achados.append(f"{artefato}: `sha256` ausente ou malformado (64 hex).")
            continue
        atual = sha256_normalizado(raiz / artefato)
        if atual != esperado:
            achados.append(
                f"{artefato}: os bytes mudaram depois da aprovacao (aprovado {esperado[:12]}…, atual "
                f"{atual[:12]}…). Regra aprovada editada exige NOVA aprovacao registrada — ou volte a "
                f"entrada para `pendentes`. Nunca atualize so' o hash."
            )

    for entrada in pendentes:
        artefato = str(entrada.get("artefato", "")).strip()
        if artefato and not str(entrada.get("responsavel") or "").strip():
            achados.append(f'{artefato}: pendente sem `responsavel` (use "a definir" se ainda nao ha\').')

    for artefato in sorted(publicaveis - set(vistos)):
        marca = (
            " (o arquivo se declara rascunho)"
            if _MARCA_RASCUNHO.search((raiz / artefato).read_text(encoding="utf-8"))
            else ""
        )
        achados.append(
            f"{artefato}: publicavel e FORA do registro{marca}. O deploy o enviaria ao motor sem ninguem "
            f"responder por ele. Registre em `aprovados` (com quem aprovou) ou em `pendentes`."
        )
    return achados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DL-0148: regra publicavel so' com registro de aprovacao.")
    parser.parse_args(argv)

    dados = carregar()
    achados = verificar(dados)
    if achados:
        print("check_aprovacao_de_regras: registro de aprovacoes com problema(s):", file=sys.stderr)
        for achado in achados:
            print(f"  - {achado}", file=sys.stderr)
        return 1

    aprovados = dados.get("aprovados") or []
    pendentes = dados.get("pendentes") or []
    print(
        f"check_aprovacao_de_regras: {len(aprovados)} aprovado(s), {len(pendentes)} PUBLICADO(S) SEM "
        f"APROVACAO (pendentes visiveis, nao bloqueiam). OK."
    )
    for entrada in pendentes:
        print(f"  pendente: {entrada['artefato']}  (responsavel: {entrada['responsavel']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
