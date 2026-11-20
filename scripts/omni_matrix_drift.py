#!/usr/bin/env python3
"""Detector de deriva da matriz de interfaces do portal (E-SF-34r; EP-CORE-12c, origem CORE-12).

O que o detector faz: compara duas versoes (base x alvo) do arquivo
`docs/compatibility/maezo-interface-matrix-p1-successor.json` do portal `omni_sales_platform` e relata,
por linha, `igual` / `alterada` / `ausente` / `nova`, com o sha256 da forma canonica da linha em cada lado.
Forma da matriz fixada na CD-218: listas `interfaces` (51) e `proposedMissingSources` (8), chave `id`
textual; denominador em `compatibilityObjective.denominatorIds` (51); qualificacao no campo `qualification`.

O que o detector NUNCA faz (variante A da linha 34r: "detector nunca promove linha"):

- nunca promove linha: `qualification` nao e lido como entrada de decisao nem escrito no relatorio; uma
  mudanca de qualificacao no alvo aparece so como `alterada` (o hash cobre a linha inteira);
- nunca gera o sucessor da matriz por janela de admissao (D-34: A1+A2+A3; D-38: A6+A10): o commit do
  sucessor e do dono do omni e os registros de admissao nao existem (A10 pendente, ato de dono).
  `sucessor_por_janela` recusa SEMPRE (`REGISTRO_DE_ADMISSAO_AUSENTE`) -- estado fechado por construcao;
- nunca copia conteudo de linha: o relatorio leva so ids e hashes (guarda de proveniencia
  `scripts/docs/omni_provenance.py`);
- nunca escreve no portal nem fora de `docs/delivery/commercial-platform/portal-mirror/`; sem rede (a
  leitura e `git cat-file` num clone local, sem checkout); determinismo: sem relogio no relatorio.

GAP declarado (CD-218 item 3): o relatorio `MATRIX-DRIFT-<sha>.json` nao e gerado no Maezo por esta linha;
quem tem o clone do portal executa:

    python -m scripts.omni_matrix_drift --omni-git-dir <clone> --base <sha40> --target <sha40> \
        [--out docs/delivery/commercial-platform/portal-mirror/MATRIX-DRIFT-<target_sha>.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
#: Janelas de admissao do sucessor (D-34, D-38). Transcritas do plano; nenhuma esta admitida.
JANELAS: Mapping[str, tuple[str, ...]] = {"D-34": ("A1", "A2", "A3"), "D-38": ("A6", "A10")}

_SHA40 = re.compile(r"[0-9a-f]{40}")


class DriftRefusal(StrEnum):
    SHA_INVALIDO = "SHA_INVALIDO"
    FORMA_DA_MATRIZ_DESCONHECIDA = "FORMA_DA_MATRIZ_DESCONHECIDA"
    SAIDA_FORA_DO_DIRETORIO = "SAIDA_FORA_DO_DIRETORIO"
    LEITURA_DO_CLONE_FALHOU = "LEITURA_DO_CLONE_FALHOU"
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
    """Sucessor da matriz por janela de admissao: FECHADO por construcao (linha 34r, variante A).

    Nao ha leitor de registro de admissao neste modulo: o registro e ato externo do dono (A10 pendente)
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


def caminho_de_saida(out: Path, target_sha: str, *, raiz: Path | None = None) -> Path:
    """So `<raiz>/portal-mirror/MATRIX-DRIFT-<target_sha>.json`; qualquer outro destino recusa."""
    _sha_valido(target_sha)
    raiz = REPO_ROOT if raiz is None else raiz
    esperado = (raiz / RELATORIO_DIR_REL / f"MATRIX-DRIFT-{target_sha}.json").resolve()
    destino = (out if out.is_absolute() else raiz / out).resolve()
    if destino != esperado:
        raise DriftRecusadoError(DriftRefusal.SAIDA_FORA_DO_DIRETORIO, f"destino {destino} != {esperado}")
    return destino


def ler_do_clone(git_dir: Path, sha: str) -> Any:
    """Le a matriz de um clone local so por objeto git (sem checkout, sem rede)."""
    _sha_valido(sha)
    proc = subprocess.run(
        ["git", "-C", str(git_dir), "cat-file", "blob", f"{sha}:{MATRIZ_PATH}"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise DriftRecusadoError(
            DriftRefusal.LEITURA_DO_CLONE_FALHOU, proc.stderr.decode("utf-8", "replace").strip()
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise _forma(f"matriz nao e JSON valido: {exc}") from exc


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
    else:
        destino.write_bytes(dados)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
