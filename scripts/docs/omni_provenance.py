#!/usr/bin/env python3
"""Guarda de proveniencia omni -> Maezo (EP-CORE-09a, epico E-SF-33; registro CORE-09).

O que a guarda afirma (aceite de EP-CORE-09a):

1. Nenhum arquivo rastreado do Maezo tem sha256 -- bruto OU normalizado (sem nenhum byte de espaco
   em branco ASCII) -- igual ao de um blob do manifesto `config/provenance/omni-sales-b3de4b4.blobs.json`
   sem trazer o cabecalho de proveniencia. Violacao -> `ProvenanceRefusal.UNLICENSED_OMNI_ORIGIN`.
2. Todo arquivo que traz o cabecalho `Provenance: Omni-Saude/omni_sales_platform@<sha>:<path>` tem de
   trazer `License-Act: <id>` e `<id>` tem de resolver para um registro em `docs/governance/licensing/`.
   Violacao -> `ProvenanceRefusal.LICENSE_ACT_NOT_FOUND`.

Estado fechado por construcao: `docs/governance/licensing/` esta vazio (nenhum ato de licenca existe;
o ato e de dono e juridico -- RATIFY-LATER-juridico de CORE-09; agente nao redige nem commita licenca).
Logo, hoje, NENHUM cabecalho resolve e toda copia (com ou sem cabecalho) e recusada.

Limites declarados (CORE-09, EP-CORE-09a): hash nao prova sala limpa contra parafrase -- isso e da
EP-CORE-09b (`scripts/docs/cleanroom_records.py`). Blobs triviais (normalizado com menos de
`MIN_NORMALIZADO` bytes, p.ex. arquivo vazio ou `{}`) ficam no manifesto mas nao geram recusa por hash:
igualdade de hash de conteudo trivial nao e evidencia de copia (um `__init__.py` vazio do Maezo tem o
mesmo sha256 que qualquer arquivo vazio do omni).

Por que este modulo mora em `scripts/docs/` e nao em `src/`: nao e codigo de runtime do produto (a regra
dura 3 de AGENTS.md reserva `src/maezo/` ao produto); e um verificador de governanca do repositorio, como
`scripts/docs/gerar_predeploy_findings.py`, importavel pelos testes (`pythonpath = ["."]`) e executavel
por `python -m scripts.docs.omni_provenance`. Os testes de guarda ficam em `tests/governance/`.

Uso:
    python -m scripts.docs.omni_provenance verificar
        # varre `git ls-files` do Maezo; exit 1 se houver recusa
    python -m scripts.docs.omni_provenance gerar --omni-git-dir <clone> --commit <sha40>
        # (re)gera o manifesto lendo SO objetos git (ls-tree + cat-file), sem checkout no clone do omni
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTO_REL = "config/provenance/omni-sales-b3de4b4.blobs.json"
COMPARTILHADOS_REL = "config/provenance/omni-sales-b3de4b4.shared-blobs.json"
LICENSING_REL = "docs/governance/licensing"
OMNI_REPO = "Omni-Saude/omni_sales_platform"
OMNI_COMMIT = "b3de4b447cdf2e6f5440716b4c79d7f49555f939"
SCHEMA_VERSION = "1.0.0"

#: Bytes removidos pela normalizacao: os seis de `string.whitespace` (ASCII).
_ESPACOS = b" \t\n\r\x0b\x0c"
#: Abaixo disto (em bytes, apos normalizar) a igualdade de hash nao conta como copia.
MIN_NORMALIZADO = 32
#: Quantas linhas do inicio do arquivo sao lidas a procura do cabecalho.
LINHAS_CABECALHO = 40

# Linha de cabecalho: prefixo de comentario opcional (#, //, /*, *, <!--, --, ;) e nada antes dele.
_PREFIXO = r"^[ \t]*(?:#|//|/\*+|\*|<!--|--|;)?[ \t]*"
_PROVENANCE_RE = re.compile(
    _PREFIXO + r"Provenance:[ \t]*Omni-Saude/omni_sales_platform@([0-9a-f]{7,40}):(\S+)",
    re.MULTILINE,
)
_LICENSE_ACT_RE = re.compile(_PREFIXO + r"License-Act:[ \t]*([A-Za-z0-9][A-Za-z0-9._-]*)", re.MULTILINE)


class ProvenanceRefusal(StrEnum):
    """Recusas tipadas da guarda (nomes fixados pela SPEC de EP-CORE-09a)."""

    UNLICENSED_OMNI_ORIGIN = "UNLICENSED_OMNI_ORIGIN"
    LICENSE_ACT_NOT_FOUND = "LICENSE_ACT_NOT_FOUND"


@dataclass(frozen=True)
class Recusa:
    motivo: ProvenanceRefusal
    caminho: str
    detalhe: str


@dataclass(frozen=True)
class Cabecalho:
    commit: str
    caminho_omni: str
    ato: str | None


@dataclass(frozen=True)
class Manifesto:
    commit: str
    por_bruto: dict[str, tuple[str, ...]]
    por_normalizado: dict[str, tuple[str, ...]]


def normalizar(conteudo: bytes) -> bytes:
    """Remove todo byte de espaco em branco ASCII (reindentacao, quebras de linha, CRLF)."""
    return conteudo.translate(None, _ESPACOS)


def sha256(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def ler_cabecalho(conteudo: bytes) -> Cabecalho | None:
    """Le o cabecalho de proveniencia nas primeiras `LINHAS_CABECALHO` linhas; None se ausente."""
    topo = b"\n".join(conteudo.splitlines()[:LINHAS_CABECALHO]).decode("utf-8", errors="replace")
    prov = _PROVENANCE_RE.search(topo)
    if prov is None:
        return None
    ato = _LICENSE_ACT_RE.search(topo)
    return Cabecalho(commit=prov.group(1), caminho_omni=prov.group(2), ato=ato.group(1) if ato else None)


def resolver_ato(ato: str, licensing_dir: Path) -> bool:
    """Um ato resolve sse `<licensing_dir>/<ato>.json` existe, e JSON e declara `act_id == ato`.

    O conteudo do registro de licenca e definido por EP-CORE-09d (ato juridico, fora do alcance de
    agente); esta guarda so exige a existencia do registro espelhado. Diretorio ausente = vazio.
    """
    alvo = licensing_dir / f"{ato}.json"
    if not alvo.is_file():
        return False
    try:
        dados = json.loads(alvo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(dados, dict) and dados.get("act_id") == ato


def origem_maezo(caminho: Path) -> frozenset[str]:
    """sha256 brutos de blobs que existiram no Maezo ANTES do omni (classe `maezo_origin`).

    Fonte: `config/provenance/omni-sales-b3de4b4.shared-blobs.json` (evidencia por historico completo dos
    dois repos). Esses blobs sao copia Maezo->omni (p.ex. o snapshot `docs/audits/P1-SOURCE-SNAPSHOT-2026-10/
    origin/` do omni) e nao sao de origem omni. A classe `omni_first_pending_adjudication` NAO entra aqui:
    continua recusada.
    """
    if not caminho.is_file():
        return frozenset()
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return frozenset(e["sha256_raw"] for e in dados["entries"] if e["class"] == "maezo_origin")


def carregar_manifesto(caminho: Path, excluir_bruto: frozenset[str] = frozenset()) -> Manifesto:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    por_bruto: dict[str, list[str]] = {}
    por_norm: dict[str, list[str]] = {}
    for blob in dados["blobs"]:
        if blob["normalized_size"] < MIN_NORMALIZADO or blob["sha256_raw"] in excluir_bruto:
            continue
        por_bruto.setdefault(blob["sha256_raw"], []).append(blob["path"])
        por_norm.setdefault(blob["sha256_normalized"], []).append(blob["path"])
    return Manifesto(
        commit=dados["source"]["commit"],
        por_bruto={k: tuple(v) for k, v in por_bruto.items()},
        por_normalizado={k: tuple(v) for k, v in por_norm.items()},
    )


def verificar_arquivo(rel: str, conteudo: bytes, manifesto: Manifesto, licensing_dir: Path) -> Recusa | None:
    cab = ler_cabecalho(conteudo)
    if cab is not None:
        if cab.ato is None:
            return Recusa(
                ProvenanceRefusal.LICENSE_ACT_NOT_FOUND, rel, "cabecalho Provenance sem License-Act"
            )
        if not resolver_ato(cab.ato, licensing_dir):
            return Recusa(
                ProvenanceRefusal.LICENSE_ACT_NOT_FOUND,
                rel,
                f"License-Act {cab.ato} nao resolve em {LICENSING_REL}/",
            )
        return None
    origem = manifesto.por_bruto.get(sha256(conteudo))
    tipo = "bruto"
    if origem is None:
        norm = normalizar(conteudo)
        if len(norm) >= MIN_NORMALIZADO:
            origem = manifesto.por_normalizado.get(sha256(norm))
            tipo = "normalizado"
    if origem is None:
        return None
    return Recusa(
        ProvenanceRefusal.UNLICENSED_OMNI_ORIGIN,
        rel,
        f"sha256 {tipo} igual ao blob omni@{manifesto.commit[:7]}:{origem[0]} sem cabecalho de proveniencia",
    )


def verificar_arvore(
    raiz: Path, arquivos: Iterable[str], manifesto: Manifesto, licensing_dir: Path
) -> list[Recusa]:
    recusas: list[Recusa] = []
    for rel in arquivos:
        alvo = raiz / rel
        if alvo.is_symlink() or not alvo.is_file():
            continue
        recusa = verificar_arquivo(rel, alvo.read_bytes(), manifesto, licensing_dir)
        if recusa is not None:
            recusas.append(recusa)
    return recusas


def arquivos_rastreados(raiz: Path) -> list[str]:
    saida = subprocess.run(["git", "-C", str(raiz), "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p.decode("utf-8") for p in saida.split(b"\0") if p]


# --------------------------------------------------------------------------- geracao do manifesto


def _git(omni: Path, *args: str, entrada: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(omni), *args], check=True, capture_output=True, input=entrada
    ).stdout


def gerar_manifesto(omni_git_dir: Path, commit: str) -> dict[str, Any]:
    """Le so objetos git do commit (ls-tree + cat-file --batch); nao faz checkout nem escreve no clone."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit precisa ser o sha1 completo (40 hex)")
    tipo = _git(omni_git_dir, "cat-file", "-t", commit).strip()
    if tipo != b"commit":
        raise ValueError(f"{commit} nao e commit ({tipo!r})")
    entradas: list[tuple[str, str, str]] = []
    for linha in _git(omni_git_dir, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not linha:
            continue
        meta, caminho_bruto = linha.split(b"\t", 1)
        modo, tipo_obj, oid = meta.decode().split(" ")
        if tipo_obj != "blob":
            raise ValueError(f"entrada nao-blob em {caminho_bruto!r}: {tipo_obj}")
        entradas.append((caminho_bruto.decode("utf-8"), modo, oid))
    lote = _git(
        omni_git_dir, "cat-file", "--batch", entrada="".join(f"{o}\n" for _, _, o in entradas).encode()
    )
    blobs: list[dict[str, Any]] = []
    pos = 0
    for caminho, modo, oid in entradas:
        fim_cab = lote.index(b"\n", pos)
        oid_lido, _tipo, tam = lote[pos:fim_cab].decode().split(" ")
        if oid_lido != oid:
            raise ValueError(f"cat-file fora de ordem: {oid_lido} != {oid}")
        corpo = lote[fim_cab + 1 : fim_cab + 1 + int(tam)]
        pos = fim_cab + 1 + int(tam) + 1
        norm = normalizar(corpo)
        blobs.append(
            {
                "path": caminho,
                "mode": modo,
                "git_blob": oid,
                "size": len(corpo),
                "sha256_raw": sha256(corpo),
                "normalized_size": len(norm),
                "sha256_normalized": sha256(norm),
            }
        )
    blobs.sort(key=lambda b: b["path"])
    return {
        "schema_version": SCHEMA_VERSION,
        "epic": "EP-CORE-09a (E-SF-33; registro CORE-09)",
        "source": {"repository": OMNI_REPO, "commit": commit},
        "content_policy": "So caminhos, modos, ids de blob git, tamanhos e sha256. Nenhum conteudo do omni.",
        "normalization": "remove todos os bytes de espaco em branco ASCII (0x20 0x09 0x0A 0x0D 0x0B 0x0C)",
        "min_normalized_size_for_refusal": MIN_NORMALIZADO,
        "generator": "python -m scripts.docs.omni_provenance gerar --omni-git-dir <clone> --commit <sha40>",
        "blob_count": len(blobs),
        "blobs": blobs,
    }


def _serializar(manifesto: dict[str, Any]) -> str:
    return json.dumps(manifesto, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.docs.omni_provenance")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verificar", help="varre a arvore rastreada do Maezo")
    g = sub.add_parser("gerar", help="(re)gera o manifesto a partir de objetos git do omni")
    g.add_argument("--omni-git-dir", type=Path, required=True)
    g.add_argument("--commit", default=OMNI_COMMIT)
    g.add_argument("--saida", type=Path, default=REPO_ROOT / MANIFESTO_REL)
    args = ap.parse_args(argv)
    if args.cmd == "gerar":
        args.saida.write_text(_serializar(gerar_manifesto(args.omni_git_dir, args.commit)), encoding="utf-8")
        return 0
    manifesto = carregar_manifesto(REPO_ROOT / MANIFESTO_REL, origem_maezo(REPO_ROOT / COMPARTILHADOS_REL))
    recusas = verificar_arvore(
        REPO_ROOT, arquivos_rastreados(REPO_ROOT), manifesto, REPO_ROOT / LICENSING_REL
    )
    for r in recusas:
        print(f"{r.motivo}: {r.caminho}: {r.detalhe}", file=sys.stderr)
    return 1 if recusas else 0


if __name__ == "__main__":
    raise SystemExit(main())
