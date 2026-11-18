#!/usr/bin/env python3
"""A arvore do push em `main` ja foi verificada, byte a byte, pelo CI verde do PR? (L1a, custo de Actions)

Por que
-------
Todo merge em `main` dispara de novo o workflow inteiro no evento `push` (~100 min de runner no `ci.yml`).
Quando a `main` nao andou entre a ultima run verde do PR e o merge, o commit de merge tem EXATAMENTE a mesma
arvore git que o merge de teste (`refs/pull/<n>/merge`) que aquela run verificou: sao os mesmos bytes, ja
verdes. Repetir a suite sobre eles nao verifica nada novo. Medido em 11-18/11/2026: 41% dos merges.

Contrato (fail-safe: na duvida, roda tudo)
------------------------------------------
* No `pull_request`, o workflow grava a arvore que testou (`git rev-parse HEAD^{tree}` do merge de teste) num
  artefato pequeno (`--gravar`).
* No `push` em `main`, este script decide (`--decidir`). Diz `verificado=true` SO' quando TUDO vale:
    1. a API associa o commit do push a exatamente UM PR mesclado em `main` cujo `merge_commit_sha` e' ele;
    2. a run MAIS RECENTE deste workflow em `pull_request` para o head final do PR terminou `success`;
    3. essa run tem o artefato da arvore, legivel, com um hash de arvore valido;
    4. esse hash e' IGUAL a arvore do commit do push.
  Qualquer outra coisa (PR nao achado, varios PRs, run nao verde ou em andamento, artefato ausente, expirado
  ou ilegivel, arvore diferente, erro de API) da' `verificado=false` e o workflow roda inteiro, como antes.
  O script nunca sai != 0 por causa da decisao: uma falha aqui nunca vira check vermelho nem verde falso.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARQUIVO_ARVORE = "arvore.txt"
_HASH = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Decisao:
    verificado: bool
    motivo: str
    run_id: int | None = None
    pr: int | None = None


def _roda_tudo(motivo: str, **extra: Any) -> Decisao:
    return Decisao(False, motivo, **extra)


def decidir(
    *,
    sha_push: str,
    arvore_push: str,
    prs: Sequence[Mapping[str, Any]],
    runs_do_head: Callable[[str], Sequence[Mapping[str, Any]]],
    arvore_da_run: Callable[[int], str | None],
) -> Decisao:
    """Decisao pura (as consultas entram por parametro). So' devolve verificado=True com as 4 condicoes."""
    if not _HASH.match(arvore_push):
        return _roda_tudo(f"arvore do push ilegivel: {arvore_push!r}")
    mesclados = [
        p
        for p in prs
        if p.get("merged_at")
        and p.get("merge_commit_sha") == sha_push
        and (p.get("base") or {}).get("ref") == "main"
    ]
    if len(mesclados) != 1:
        return _roda_tudo(
            f"commit associado a {len(mesclados)} PR(s) mesclado(s) em main com este merge_commit_sha"
        )
    pr = mesclados[0]
    numero = pr.get("number")
    head = (pr.get("head") or {}).get("sha")
    if not isinstance(head, str) or not head:
        return _roda_tudo("PR sem head.sha", pr=numero)
    runs = list(runs_do_head(head))
    if not runs:
        return _roda_tudo(f"nenhuma run em pull_request para o head {head[:12]}", pr=numero)
    ultima = max(runs, key=lambda r: int(r["id"]))
    run_id = int(ultima["id"])
    if ultima.get("status") != "completed" or ultima.get("conclusion") != "success":
        return _roda_tudo(
            f"a run mais recente do head ({run_id}) esta {ultima.get('status')}/{ultima.get('conclusion')}",
            run_id=run_id,
            pr=numero,
        )
    arvore_testada = arvore_da_run(run_id)
    if arvore_testada is None or not _HASH.match(arvore_testada):
        return _roda_tudo(f"run {run_id} sem artefato de arvore legivel", run_id=run_id, pr=numero)
    if arvore_testada != arvore_push:
        return _roda_tudo(
            f"arvore diferente: testada {arvore_testada[:12]} x push {arvore_push[:12]} (a main andou)",
            run_id=run_id,
            pr=numero,
        )
    return Decisao(
        True, f"arvore {arvore_push[:12]} identica a testada pela run verde {run_id}", run_id, numero
    )


# --------------------------------------------------------------------------------------------------- E/S


def _gh_json(caminho: str) -> Any:
    saida = subprocess.run(["gh", "api", caminho], check=True, capture_output=True, text=True).stdout
    return json.loads(saida)


def _arvore_local() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _ler_artefato(repo: str, run_id: int, artefato: str) -> str | None:
    lista = _gh_json(f"repos/{repo}/actions/runs/{run_id}/artifacts?name={artefato}&per_page=10")
    vivos = [a for a in lista.get("artifacts", []) if a.get("name") == artefato and not a.get("expired")]
    if len(vivos) != 1:
        return None
    zipado = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/artifacts/{vivos[0]['id']}/zip"], check=True, capture_output=True
    ).stdout
    with zipfile.ZipFile(io.BytesIO(zipado)) as z:
        return z.read(ARQUIVO_ARVORE).decode("ascii").strip()


def _escrever_saida(decisao: Decisao) -> None:
    linhas = [
        f"verificado={'true' if decisao.verificado else 'false'}",
        f"run_verificadora={decisao.run_id or ''}",
    ]
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")
    resumo = os.environ.get("GITHUB_STEP_SUMMARY")
    texto = (
        "## arvore verificada: "
        + ("SIM — lanes pesadas nao repetem" if decisao.verificado else "NAO — roda tudo")
        + "\n\n"
        f"{decisao.motivo}" + (f" (PR #{decisao.pr})" if decisao.pr else "") + "\n"
    )
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write(texto)
    print(texto)


def _decidir_no_runner(workflow: str, artefato: str) -> Decisao:
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    try:
        return decidir(
            sha_push=sha,
            arvore_push=_arvore_local(),
            prs=_gh_json(f"repos/{repo}/commits/{sha}/pulls"),
            runs_do_head=lambda head: _gh_json(
                f"repos/{repo}/actions/workflows/{workflow}/runs?event=pull_request&head_sha={head}&per_page=30"
            ).get("workflow_runs", []),
            arvore_da_run=lambda run_id: _ler_artefato(repo, run_id, artefato),
        )
    except Exception as erro:  # noqa: BLE001 — qualquer falha de consulta vira "roda tudo", nunca vermelho
        return _roda_tudo(f"erro ao consultar ({type(erro).__name__}: {erro})")


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument("--gravar", type=Path, metavar="DIR", help="grava a arvore do HEAD em DIR/arvore.txt")
    modo.add_argument("--decidir", action="store_true", help="decide no push (saidas em GITHUB_OUTPUT)")
    p.add_argument("--workflow", help="arquivo do workflow (ex.: ci.yml)")
    p.add_argument("--artefato", help="nome do artefato da arvore")
    a = p.parse_args(argv)
    if a.gravar:
        a.gravar.mkdir(parents=True, exist_ok=True)
        (a.gravar / ARQUIVO_ARVORE).write_text(_arvore_local() + "\n", encoding="ascii")
        return 0
    if not a.workflow or not a.artefato:
        p.error("--decidir exige --workflow e --artefato")
    _escrever_saida(_decidir_no_runner(a.workflow, a.artefato))
    return 0


if __name__ == "__main__":
    sys.exit(main())
