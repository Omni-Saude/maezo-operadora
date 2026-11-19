#!/usr/bin/env python3
"""CI gate: na chancela de regras, só se ACRESCENTA (RF-08, RNF-16; v3.1 §7.4).

Chancela de regras, onda 1 (ADR-0080 e DL-0194, PROPOSTOS; blueprint §3.12).

Compara a árvore atual com o merge-base do `main` (`--base`, padrão `origin/main`; no próprio `main`,
com o primeiro pai). REPROVA:
  1. regra que existia na base e sumiu (remover é rodada de remoção: a entrada fica como `removida`);
  2. `id` que mudou de processo (ID nunca é reaproveitado por outro processo);
  3. `aprovacoes`, `liberacoes`, `comprovacoes` ou `historico_de_mudanca` da base que não são PREFIXO
     exato da lista atual (item editado, removido ou reordenado);
  4. `sha256` alterado sem linha nova em `historico_de_mudanca`;
  5. transição de `estado_declarado` fora do grafo permitido; regra `removida` que mudou qualquer coisa;
  6. recibo em `spec/regras/recibos/` removido ou com bytes alterados;
  7. linha do controle de alterações da base que sumiu ou mudou.

Base sem `spec/regras/` (primeiro PR do catálogo) = nada a comparar: passa e diz isso.

Uso:
    python scripts/ci/check_regras_so_acrescimo.py [--base origin/main]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from maezo.regras.canonico import canonicalizar  # noqa: E402
from maezo.regras.catalogo.carregar import CatalogoYamlError, carregar_yaml_texto  # noqa: E402
from maezo.regras.catalogo.repositorio import (  # noqa: E402
    ARQUIVO_CONTROLE,
    PASTA_CATALOGO,
    PASTA_RECIBOS,
    PASTA_REGRAS,
)

#: Listas que só crescem pelo fim.
LISTAS_SO_ACRESCIMO: Final[tuple[str, ...]] = (
    "aprovacoes",
    "liberacoes",
    "comprovacoes",
    "historico_de_mudanca",
)

#: Transições de `estado_declarado` (mesmo estado é sempre permitido).
TRANSICOES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("legado_pendente", "ativa"),
        ("legado_pendente", "em_remocao"),
        ("ativa", "em_remocao"),
        ("em_remocao", "ativa"),  # remoção recusada: a regra segue
        ("em_remocao", "removida"),
    }
)


def _estavel(valor: Any) -> str:
    """Forma comparável de um item (datas do YAML viram texto ISO)."""

    def normalizar(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(k): normalizar(v) for k, v in item.items()}
        if isinstance(item, list):
            return [normalizar(v) for v in item]
        if hasattr(item, "isoformat"):
            return item.isoformat()
        if type(item) is float:
            return repr(item)
        return item

    return canonicalizar(normalizar(valor)).decode("utf-8")


def _regras_por_id(catalogos: Mapping[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    saida: dict[str, tuple[str, dict[str, Any]]] = {}
    for arquivo, dados in catalogos.items():
        if not isinstance(dados, dict) or not isinstance(dados.get("regras"), list):
            continue
        for regra in dados["regras"]:
            if isinstance(regra, dict) and isinstance(regra.get("id"), str):
                saida.setdefault(regra["id"], (arquivo, regra))
    return saida


def comparar(
    base_catalogos: Mapping[str, Any],
    head_catalogos: Mapping[str, Any],
    base_recibos: Mapping[str, bytes],
    head_recibos: Mapping[str, bytes],
    base_controle: Any,
    head_controle: Any,
) -> list[str]:
    """Núcleo puro: base × head já carregados. Achados vazios = só acréscimo."""
    achados: list[str] = []
    base = _regras_por_id(base_catalogos)
    head = _regras_por_id(head_catalogos)
    for rid, (arq_base, rb) in sorted(base.items()):
        if rid not in head:
            achados.append(
                f"{rid}: regra da base sumiu de {arq_base}. Regra nunca sai do catalogo: remocao e' rodada "
                f"de remocao e a entrada fica como `removida` para sempre."
            )
            continue
        arq_head, rh = head[rid]
        if arq_head != arq_base:
            achados.append(
                f"{rid}: mudou de {arq_base} para {arq_head} — "
                "ID nunca muda de processo nem e' reaproveitado."
            )
        for lista in LISTAS_SO_ACRESCIMO:
            antes = rb.get(lista) or []
            depois = rh.get(lista) or []
            if not isinstance(antes, list) or not isinstance(depois, list):
                continue
            if len(depois) < len(antes) or any(
                _estavel(a) != _estavel(d) for a, d in zip(antes, depois[: len(antes)], strict=True)
            ):
                achados.append(
                    f"{rid}: `{lista}` da base foi editada, reordenada ou encurtada — so' acrescimo no fim."
                )
        eb, eh = rb.get("estado_declarado"), rh.get("estado_declarado")
        if eb == "removida" and _estavel(rb) != _estavel(rh):
            achados.append(f"{rid}: regra `removida` nao muda mais (estado final).")
        elif eb != eh and (eb, eh) not in TRANSICOES:
            achados.append(f"{rid}: transicao de `estado_declarado` {eb!r} -> {eh!r} nao permitida.")
        if rb.get("sha256") != rh.get("sha256"):
            hist_b = rb.get("historico_de_mudanca") or []
            hist_h = rh.get("historico_de_mudanca") or []
            if not isinstance(hist_h, list) or not isinstance(hist_b, list) or len(hist_h) <= len(hist_b):
                achados.append(
                    f"{rid}: `sha256` mudou sem linha nova em `historico_de_mudanca` "
                    "({pr, o_que_mudou, motivo})."
                )
    for arquivo, conteudo in sorted(base_recibos.items()):
        if arquivo not in head_recibos:
            achados.append(f"{arquivo}: recibo removido — recibo nunca sai.")
        elif head_recibos[arquivo] != conteudo:
            achados.append(f"{arquivo}: recibo alterado — recibo nunca e' editado.")
    linhas_b = Counter(_estavel(x) for x in _linhas(base_controle))
    linhas_h = Counter(_estavel(x) for x in _linhas(head_controle))
    faltam = linhas_b - linhas_h
    if faltam:
        achados.append(
            f"{ARQUIVO_CONTROLE}: {sum(faltam.values())} linha(s) da base sumiram ou mudaram — "
            "o controle so' cresce."
        )
    return achados


def _linhas(controle: Any) -> list[Any]:
    if isinstance(controle, dict) and isinstance(controle.get("linhas"), list):
        return list(controle["linhas"])
    return []


def _git(raiz: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=raiz, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _git_bytes(raiz: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=raiz, check=True, capture_output=True).stdout


def resolver_base(raiz: Path, ref: str) -> str:
    """merge-base(HEAD, ref); se for o próprio HEAD (push no `main`), o primeiro pai."""
    head = _git(raiz, "rev-parse", "HEAD").strip()
    base = _git(raiz, "merge-base", "HEAD", ref).strip()
    if base == head:
        base = _git(raiz, "rev-parse", "HEAD^1").strip()
    return base


def _yaml(texto: str, onde: str) -> Any:
    try:
        return carregar_yaml_texto(texto)
    except CatalogoYamlError as exc:
        raise SystemExit(f"ERRO: {onde}: YAML ilegivel ({exc})") from exc


def carregar_base(raiz: Path, commit: str) -> tuple[dict[str, Any], dict[str, bytes], Any]:
    nomes = [
        n for n in _git(raiz, "ls-tree", "-r", "--name-only", commit, "--", PASTA_REGRAS).splitlines() if n
    ]
    catalogos = {
        n: _yaml(_git(raiz, "show", f"{commit}:{n}"), f"{commit[:12]}:{n}")
        for n in nomes
        if n.startswith(f"{PASTA_CATALOGO}/") and n.endswith(".yaml")
    }
    recibos = {
        n: _git_bytes(raiz, "show", f"{commit}:{n}") for n in nomes if n.startswith(f"{PASTA_RECIBOS}/")
    }
    controle = (
        _yaml(_git(raiz, "show", f"{commit}:{ARQUIVO_CONTROLE}"), ARQUIVO_CONTROLE)
        if ARQUIVO_CONTROLE in nomes
        else None
    )
    return catalogos, recibos, controle


def carregar_head(raiz: Path) -> tuple[dict[str, Any], dict[str, bytes], Any]:
    pasta = raiz / PASTA_CATALOGO
    catalogos = (
        {
            f"{PASTA_CATALOGO}/{p.name}": _yaml(p.read_text(encoding="utf-8"), p.name)
            for p in sorted(pasta.glob("*.yaml"))
        }
        if pasta.is_dir()
        else {}
    )
    recibos_dir = raiz / PASTA_RECIBOS
    recibos = (
        {
            p.relative_to(raiz).as_posix(): p.read_bytes()
            for p in sorted(recibos_dir.rglob("*"))
            if p.is_file()
        }
        if recibos_dir.is_dir()
        else {}
    )
    controle_arq = raiz / ARQUIVO_CONTROLE
    controle = (
        _yaml(controle_arq.read_text(encoding="utf-8"), ARQUIVO_CONTROLE) if controle_arq.is_file() else None
    )
    return catalogos, recibos, controle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chancela de regras: so' acrescimo contra a base.")
    parser.add_argument("--base", default="origin/main", help="ref cujo merge-base com HEAD e' a base")
    args = parser.parse_args(argv)
    try:
        commit = resolver_base(_RAIZ, args.base)
    except subprocess.CalledProcessError as exc:
        print(
            f"check_regras_so_acrescimo: base {args.base!r} irresolvivel ({exc.stderr.strip()}).",
            file=sys.stderr,
        )
        return 1
    base_cat, base_rec, base_ctl = carregar_base(_RAIZ, commit)
    if not base_cat and not base_rec and base_ctl is None:
        print(f"check_regras_so_acrescimo: base {commit[:12]} sem {PASTA_REGRAS}/ — nada a comparar. OK.")
        return 0
    head_cat, head_rec, head_ctl = carregar_head(_RAIZ)
    achados = comparar(base_cat, head_cat, base_rec, head_rec, base_ctl, head_ctl)
    if achados:
        print(f"check_regras_so_acrescimo: contra {commit[:12]}:", file=sys.stderr)
        for achado in achados:
            print(f"  - {achado}", file=sys.stderr)
        return 1
    print(f"check_regras_so_acrescimo: contra {commit[:12]}, so' acrescimo. OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
