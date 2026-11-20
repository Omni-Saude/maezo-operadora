#!/usr/bin/env python3
"""Detector de deriva da matriz de interfaces do portal (E-SF-34r; EP-CORE-12c, origem CORE-12).

O que o detector faz: compara duas versoes (base x alvo) do arquivo
`docs/compatibility/maezo-interface-matrix-p1-successor.json` do portal `omni_sales_platform` e relata,
por linha, `igual` / `alterada` / `ausente` / `nova`, com o sha256 da forma canonica da linha em cada lado.
Forma da matriz fixada na CD-218: listas `interfaces` (51) e `proposedMissingSources` (8), chave `id`
textual; denominador em `compatibilityObjective.denominatorIds` (51); qualificacao no campo `qualification`.

O que o detector NUNCA faz (E-SF-34r):

- nunca promove linha: `qualification` nao e lido como entrada de decisao nem escrito no relatorio; uma
  mudanca de qualificacao no alvo aparece so como `alterada` (o hash cobre a linha inteira);
- nunca gera o sucessor da matriz por janela de admissao (`JANELAS`): o commit do
  sucessor e do dono do omni e nenhum registro de admissao existe.
  `sucessor_por_janela` recusa SEMPRE (`REGISTRO_DE_ADMISSAO_AUSENTE`) -- estado fechado por construcao;
- nunca copia conteudo de linha: o relatorio leva so ids e hashes (guarda de proveniencia
  `scripts/docs/omni_provenance.py`);
- nunca escreve no portal nem fora de `docs/delivery/commercial-platform/portal-mirror/` (comparacao
  lexical do destino; symlink em qualquer componente entre a raiz e o destino recusa);
- sem rede: a leitura e `git cat-file` num clone local completo, sem checkout, com ambiente git
  saneado (nenhuma variavel `GIT_*` do chamador; sem config de sistema/global); clone parcial
  (`extensions.partialClone` ou `remote.*.promisor`, que buscariam blobs sob demanda) recusa;
- determinismo: sem relogio no relatorio.

GAP declarado (CD-218 item 3): o relatorio `MATRIX-DRIFT-<sha>.json` nao e gerado no Maezo por esta linha;
quem tem o clone do portal executa:

    python -m scripts.omni_matrix_drift --omni-git-dir <clone> --base <sha40> --target <sha40> \
        [--out docs/delivery/commercial-platform/portal-mirror/MATRIX-DRIFT-<target_sha>.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "maezo-omni-matrix-drift.v1"
MATRIZ_PATH = "docs/compatibility/maezo-interface-matrix-p1-successor.json"
LISTAS = ("interfaces", "proposedMissingSources")
CAMPO_ID = "id"
CAMPO_QUALIFICACAO = "qualification"
DENOMINADOR = ("compatibilityObjective", "denominatorIds")
RELATORIO_DIR_REL = "docs/delivery/commercial-platform/portal-mirror"
#: Janelas de admissao do sucessor, por id de item. Nenhuma esta admitida.
JANELAS: Mapping[str, tuple[str, ...]] = {"D-34": ("A1", "A2", "A3"), "D-38": ("A6", "A10")}

_SHA40 = re.compile(r"[0-9a-f]{40}")


class DriftRefusal(StrEnum):
    SHA_INVALIDO = "SHA_INVALIDO"
    FORMA_DA_MATRIZ_DESCONHECIDA = "FORMA_DA_MATRIZ_DESCONHECIDA"
    SAIDA_FORA_DO_DIRETORIO = "SAIDA_FORA_DO_DIRETORIO"
    LEITURA_DO_CLONE_FALHOU = "LEITURA_DO_CLONE_FALHOU"
    CLONE_PARCIAL_RECUSADO = "CLONE_PARCIAL_RECUSADO"
    ESCRITA_DA_SAIDA_FALHOU = "ESCRITA_DA_SAIDA_FALHOU"
    JANELA_DESCONHECIDA = "JANELA_DESCONHECIDA"
    REGISTRO_DE_ADMISSAO_AUSENTE = "REGISTRO_DE_ADMISSAO_AUSENTE"


class DriftRecusadoError(Exception):
    def __init__(self, motivo: DriftRefusal, detalhe: str) -> None:
        super().__init__(f"{motivo}: {detalhe}")
        self.motivo = motivo
        self.detalhe = detalhe


def _sha_valido(sha: str) -> str:
    if not isinstance(sha, str) or _SHA40.fullmatch(sha) is None:
        raise DriftRecusadoError(DriftRefusal.SHA_INVALIDO, f"esperado sha de 40 hex minusculos: {sha!r}")
    return sha


def _forma(detalhe: str) -> DriftRecusadoError:
    return DriftRecusadoError(DriftRefusal.FORMA_DA_MATRIZ_DESCONHECIDA, detalhe)


def _hash_linha(item: Mapping[str, Any]) -> str:
    canonico = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _linhas(matriz: Any) -> dict[str, tuple[str, str]]:
    """id -> (lista, sha256 canonico). Recusa forma diferente, id ausente, vazio ou duplicado."""
    if not isinstance(matriz, dict):
        raise _forma("matriz nao e objeto JSON")
    linhas: dict[str, tuple[str, str]] = {}
    for lista in LISTAS:
        itens = matriz.get(lista)
        if not isinstance(itens, list):
            raise _forma(f"lista {lista!r} ausente ou nao e lista")
        for item in itens:
            if not isinstance(item, dict):
                raise _forma(f"item de {lista!r} nao e objeto")
            ident = item.get(CAMPO_ID)
            if not isinstance(ident, str) or not ident:
                raise _forma(f"item de {lista!r} sem {CAMPO_ID!r} textual")
            if ident in linhas:
                raise _forma(f"{CAMPO_ID!r} duplicado: {ident!r}")
            # so presenca da chave (forma CD-218); o VALOR de qualificacao nunca e lido nem escrito
            if CAMPO_QUALIFICACAO not in item:
                raise _forma(f"item {ident!r} sem a chave de qualificacao")
            linhas[ident] = (lista, _hash_linha(item))
    return linhas


def _denominador(matriz: Mapping[str, Any], linhas: Mapping[str, tuple[str, str]]) -> list[str]:
    bloco = matriz.get(DENOMINADOR[0])
    ids = bloco.get(DENOMINADOR[1]) if isinstance(bloco, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        raise _forma("compatibilityObjective.denominatorIds ausente ou nao e lista de textos")
    if len(set(ids)) != len(ids):
        raise _forma("denominatorIds com id duplicado")
    fora = sorted(i for i in ids if linhas.get(i, ("", ""))[0] != LISTAS[0])
    if fora:
        raise _forma(f"denominatorIds fora de {LISTAS[0]!r}: {fora}")
    sem_denominador = sorted(set(i for i, (lista, _) in linhas.items() if lista == LISTAS[0]) - set(ids))
    if sem_denominador:
        raise _forma(f"ids de {LISTAS[0]!r} fora de denominatorIds: {sem_denominador}")
    return sorted(ids)


def comparar(base: Any, alvo: Any, *, base_sha: str, target_sha: str) -> dict[str, Any]:
    """Relatorio de deriva base x alvo. So ids e hashes; nenhuma promocao de linha."""
    _sha_valido(base_sha)
    _sha_valido(target_sha)
    linhas_base = _linhas(base)
    linhas_alvo = _linhas(alvo)
    den_base = _denominador(base, linhas_base)
    den_alvo = _denominador(alvo, linhas_alvo)

    relatorio_linhas: list[dict[str, Any]] = []
    for ident in sorted(set(linhas_base) | set(linhas_alvo)):
        b = linhas_base.get(ident)
        a = linhas_alvo.get(ident)
        if b is None:
            estado = "nova"
        elif a is None:
            estado = "ausente"
        elif b == a:
            estado = "igual"
        else:
            estado = "alterada"
        relatorio_linhas.append(
            {
                "id": ident,
                "lista_base": b[0] if b else None,
                "lista_alvo": a[0] if a else None,
                "estado": estado,
                "sha256_base": b[1] if b else None,
                "sha256_alvo": a[1] if a else None,
            }
        )
    totais = {
        e: sum(1 for x in relatorio_linhas if x["estado"] == e)
        for e in ("igual", "alterada", "ausente", "nova")
    }
    return {
        "schema": SCHEMA,
        "matriz": MATRIZ_PATH,
        "base_sha": base_sha,
        "target_sha": target_sha,
        "promove_linhas": False,
        "sucessor": {"estado": "RECUSADO", "motivo": str(DriftRefusal.REGISTRO_DE_ADMISSAO_AUSENTE)},
        "denominador": {"base": den_base, "alvo": den_alvo},
        "totais": totais,
        "linhas": relatorio_linhas,
        "reproducao": (
            "python -m scripts.omni_matrix_drift --omni-git-dir <clone> "
            f"--base {base_sha} --target {target_sha}"
        ),
    }


def sucessor_por_janela(janela: str) -> dict[str, Any]:
    """Sucessor da matriz por janela de admissao: FECHADO por construcao (E-SF-34r).

    Nao ha leitor de registro de admissao neste modulo: o registro e ato externo do dono
    e o commit do sucessor e do dono do omni. Toda chamada recusa.
    """
    if janela not in JANELAS:
        raise DriftRecusadoError(
            DriftRefusal.JANELA_DESCONHECIDA, f"janela {janela!r} fora de {sorted(JANELAS)}"
        )
    raise DriftRecusadoError(
        DriftRefusal.REGISTRO_DE_ADMISSAO_AUSENTE,
        f"janela {janela} exige registros {', '.join(JANELAS[janela])}; nenhum registro de admissao existe",
    )


def serializar(relatorio: Mapping[str, Any]) -> bytes:
    return (json.dumps(relatorio, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _saida_recusada(detalhe: str) -> DriftRecusadoError:
    return DriftRecusadoError(DriftRefusal.SAIDA_FORA_DO_DIRETORIO, detalhe)


def caminho_de_saida(out: Path, target_sha: str, *, raiz: Path | None = None) -> Path:
    """So `<raiz>/portal-mirror/MATRIX-DRIFT-<target_sha>.json`; qualquer outro destino recusa.

    Comparacao lexical (sem seguir symlink); symlink em qualquer componente entre a raiz e o destino
    recusa; o pai resolvido do destino tem de ficar dentro da raiz resolvida.
    """
    _sha_valido(target_sha)
    raiz_abs = Path(os.path.abspath(REPO_ROOT if raiz is None else raiz))
    esperado = Path(os.path.normpath(raiz_abs / RELATORIO_DIR_REL / f"MATRIX-DRIFT-{target_sha}.json"))
    destino = Path(os.path.normpath(out if out.is_absolute() else raiz_abs / out))
    if destino != esperado:
        raise _saida_recusada(f"destino {destino} != {esperado}")
    componente = raiz_abs
    for parte in destino.relative_to(raiz_abs).parts:
        componente = componente / parte
        if componente.is_symlink():
            raise _saida_recusada(f"componente do destino e symlink: {componente}")
    if not destino.parent.resolve().is_relative_to(raiz_abs.resolve()):
        raise _saida_recusada(f"pai resolvido de {destino} fora da raiz")
    return destino


def _escrever(destino: Path, dados: bytes) -> None:
    """Escreve sem seguir symlink no ultimo componente (fecha a janela entre a checagem e a escrita)."""
    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with open(fd, "wb") as arq:
        arq.write(dados)


def _recusa_constante(nome: str) -> Any:
    raise _forma(f"constante JSON nao finita: {nome}")


def _float_finito(texto: str) -> float:
    valor = float(texto)
    if not math.isfinite(valor):
        raise _forma(f"numero JSON nao finito: {texto}")
    return valor


def _objeto_sem_chave_duplicada(pares: list[tuple[str, Any]]) -> dict[str, Any]:
    objeto: dict[str, Any] = {}
    for chave, valor in pares:
        if chave in objeto:
            raise _forma(f"chave JSON duplicada: {chave!r}")
        objeto[chave] = valor
    return objeto


def carregar_matriz(dados: bytes) -> Any:
    """JSON estrito: chave duplicada, NaN/Infinity ou texto invalido recusam como forma desconhecida."""
    try:
        return json.loads(
            dados,
            object_pairs_hook=_objeto_sem_chave_duplicada,
            parse_constant=_recusa_constante,
            parse_float=_float_finito,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _forma(f"matriz nao e JSON valido: {exc}") from exc


#: Ambiente do git montado do zero: nenhuma variavel do chamador (`GIT_DIR`, `GIT_*`, proxy...) passa.
_AMBIENTE_GIT: Mapping[str, str] = {
    "PATH": os.defpath,
    "HOME": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_LAZY_FETCH": "1",
    "LC_ALL": "C",
}
_CHAVES_PROMISOR = re.compile(r"extensions\.partialclone|remote\..+\.promisor")


def _git(git_dir: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "--no-replace-objects", "-C", str(git_dir), *args],
        capture_output=True,
        check=False,
        env=dict(_AMBIENTE_GIT),
    )


def _falhou(proc: subprocess.CompletedProcess[bytes], contexto: str) -> DriftRecusadoError:
    erro = proc.stderr.decode("utf-8", "replace").strip()
    return DriftRecusadoError(DriftRefusal.LEITURA_DO_CLONE_FALHOU, f"{contexto}: {erro}")


def _validar_clone(git_dir: Path) -> None:
    topo = _git(git_dir, "rev-parse", "--show-toplevel")
    if topo.returncode != 0:
        raise _falhou(topo, "clone sem arvore de trabalho (bare?) ou fora de repositorio")
    raiz = Path(topo.stdout.decode("utf-8", "replace").strip()).resolve()
    if raiz != git_dir.resolve():
        raise DriftRecusadoError(
            DriftRefusal.LEITURA_DO_CLONE_FALHOU, f"{git_dir} nao e o topo do repositorio ({raiz})"
        )
    config = _git(git_dir, "config", "--local", "--list", "--name-only")
    if config.returncode != 0:
        raise _falhou(config, "config do clone ilegivel")
    promisores = sorted(
        chave
        for chave in config.stdout.decode("utf-8", "replace").splitlines()
        if _CHAVES_PROMISOR.fullmatch(chave.strip().lower())
    )
    if promisores:
        raise DriftRecusadoError(
            DriftRefusal.CLONE_PARCIAL_RECUSADO, f"clone parcial buscaria objetos na rede: {promisores}"
        )


def ler_do_clone(git_dir: Path, sha: str) -> Any:
    """Le a matriz de um clone local completo so por objeto git (sem checkout, sem rede)."""
    _sha_valido(sha)
    _validar_clone(git_dir)
    commit = _git(git_dir, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{sha}^{{commit}}")
    if commit.returncode != 0 or commit.stdout.decode("ascii", "replace").strip() != sha:
        raise _falhou(commit, f"{sha} nao e commit do clone")
    proc = _git(git_dir, "cat-file", "blob", f"{sha}:{MATRIZ_PATH}")
    if proc.returncode != 0:
        raise _falhou(proc, "matriz ausente no commit")
    return carregar_matriz(proc.stdout)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.omni_matrix_drift", description=__doc__)
    parser.add_argument("--omni-git-dir", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        destino = caminho_de_saida(args.out, args.target) if args.out else None
        relatorio = comparar(
            ler_do_clone(args.omni_git_dir, args.base),
            ler_do_clone(args.omni_git_dir, args.target),
            base_sha=args.base,
            target_sha=args.target,
        )
    except DriftRecusadoError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    dados = serializar(relatorio)
    if destino is None:
        sys.stdout.write(dados.decode("utf-8"))
        return 0
    try:
        _escrever(destino, dados)
    except OSError as exc:
        recusa = DriftRecusadoError(DriftRefusal.ESCRITA_DA_SAIDA_FALHOU, f"{destino}: {exc.strerror}")
        print(str(recusa), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
