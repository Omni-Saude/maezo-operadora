#!/usr/bin/env python3
"""Cerca de CI: a fronteira entre a plataforma e os agentes (ADR-0067), em modo catraca.

Por que
-------
Hoje a plataforma (receptor, gateway, runtime, worker, portal) importa o interior dos agentes
(`maezo.agents.helena.graph`, `maezo.agents.lucas.fonte_v4`, ...), e um agente importa outro. Com
isso, ajustar a Helena obriga a mexer, testar e reimplantar a plataforma: medido em 23/10/2026, 92
dos 186 commits da Helena em 60 dias tocaram codigo fora dela. O ADR-0067 decide que a plataforma
fala com o agente so' por um contrato (`maezo.contratos`) e que um agente nao importa outro.

O que esta cerca faz HOJE (fase 1 do ADR-0067: catraca)
-------------------------------------------------------
As dependencias proibidas que ja' existem estao listadas, uma a uma, em
`scripts/ci/fronteira_agentes_baseline.txt` — esse arquivo E' o inventario do que mover. A cerca:
  * reprova dependencia NOVA que nao esta' na baseline (a divida nao cresce);
  * reprova linha da baseline que nao existe mais no codigo (a divida so' diminui, e a baseline
    nunca guarda credito velho que permitiria reintroduzir o import).
Quando a baseline zerar, a regra vale inteira, sem excecao.

O que conta como dependencia proibida
-------------------------------------
Seja `<id>` um pacote de agente (`src/maezo/agents/<id>/__init__.py`). O framework de agentes
(`src/maezo/agents/*.py`: AgentLoader, resolve_spec_dir) NAO e' agente e pode ser importado.
  * plataforma -> agente: qualquer `.py` de `src/maezo/` fora de `agents/<id>/` e fora de `qa/`
    que importe `maezo.agents.<id>[...]` (import absoluto, relativo, ou string fora de docstring —
    o caminho do `importlib`);
  * agente -> agente: `agents/<x>/` importando `maezo.agents.<y>`, com x != y.
`maezo.qa` fica de fora: e' ferramenta de teste, ja' isolada de producao por `check_qa_isolada.py`.

Uso:
  python3 scripts/ci/check_fronteira_agentes.py                 # catraca (CI)
  python3 scripts/ci/check_fronteira_agentes.py --relatorio     # inventario em markdown
  python3 scripts/ci/check_fronteira_agentes.py --gerar-baseline  # so' ao REDUZIR a divida
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
BASELINE_PADRAO = _RAIZ / "scripts" / "ci" / "fronteira_agentes_baseline.txt"
PREFIXO_AGENTES = "maezo.agents."

_CABECALHO_BASELINE = """\
# Inventario da divida de fronteira plataforma x agentes (ADR-0067) — catraca de
# `scripts/ci/check_fronteira_agentes.py`. Uma linha por (arquivo importador -> modulo importado).
#
# NAO acrescente linha a mao: dependencia nova para dentro de um agente e' proibida. Este arquivo
# so' encolhe — ao mover codigo para o lugar certo, rode `--gerar-baseline` e commite a reducao.
"""


@dataclass(frozen=True, order=True)
class Dependencia:
    direcao: str  # "plataforma->agente" | "agente->agente"
    importador: str  # caminho posix relativo a raiz do repo
    alvo: str  # modulo importado (ou "string:<valor>")

    def chave(self) -> str:
        return f"{self.importador} -> {self.alvo}"


def ids_de_agentes(src_dir: Path) -> set[str]:
    raiz = src_dir / "agents"
    return {p.parent.name for p in raiz.glob("*/__init__.py")}


def _pacote_do_arquivo(caminho: Path, src_dir: Path) -> list[str]:
    relativo = caminho.relative_to(src_dir.parent).with_suffix("")
    # `maezo/platform/__init__.py` -> ["maezo", "platform"]: o `__init__` E' o pacote, e o `-1` o tira.
    return list(relativo.parts)[:-1]


def _resolver_relativo(pacote: list[str], nivel: int, modulo: str | None) -> str | None:
    if nivel - 1 > len(pacote):
        return None
    base = pacote[: len(pacote) - (nivel - 1)] if nivel > 1 else pacote
    return ".".join([*base, *([modulo] if modulo else [])])


def _docstrings(arvore: ast.AST) -> set[int]:
    ids: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            corpo = no.body
            if corpo and isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant):
                ids.add(id(corpo[0].value))
    return ids


def _agente_do_modulo(modulo: str | None, agentes: set[str]) -> str | None:
    if not modulo or not modulo.startswith(PREFIXO_AGENTES):
        return None
    candidato = modulo[len(PREFIXO_AGENTES) :].split(".", 1)[0]
    return candidato if candidato in agentes else None


def _e_submodulo(src_dir: Path, modulo: str) -> bool:
    caminho = src_dir.parent.joinpath(*modulo.split("."))
    return caminho.with_suffix(".py").is_file() or (caminho / "__init__.py").is_file()


def _dono(caminho: Path, src_dir: Path, agentes: set[str]) -> str | None:
    """`<id>` se o arquivo mora em agents/<id>/; "qa" para o QA; None para a plataforma."""
    partes = caminho.relative_to(src_dir).parts
    if partes[0] == "qa":
        return "qa"
    if partes[0] == "agents" and len(partes) > 2 and partes[1] in agentes:
        return partes[1]
    return None


def _alvos_do_arquivo(caminho: Path, src_dir: Path, agentes: set[str]) -> list[str]:
    """Modulos de agente que o arquivo importa (ou cita como string fora de docstring)."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    pacote = _pacote_do_arquivo(caminho, src_dir)
    docstrings = _docstrings(arvore)
    alvos: list[str] = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            alvos.extend(n.name for n in no.names if _agente_do_modulo(n.name, agentes))
        elif isinstance(no, ast.ImportFrom):
            base = no.module if no.level == 0 else _resolver_relativo(pacote, no.level, no.module)
            if _agente_do_modulo(base, agentes):
                # `from maezo.agents.helena import graph`: se `graph` e' submodulo, o alvo e' ele.
                alvos.extend(
                    f"{base}.{n.name}" if _e_submodulo(src_dir, f"{base}.{n.name}") else str(base)
                    for n in no.names
                )
            elif base:
                # `from maezo.agents import helena` importa o pacote do agente.
                alvos.extend(
                    f"{base}.{n.name}" for n in no.names if _agente_do_modulo(f"{base}.{n.name}", agentes)
                )
        elif isinstance(no, ast.Constant) and isinstance(no.value, str) and id(no) not in docstrings:
            valor = no.value.strip()
            if " " not in valor and _agente_do_modulo(valor, agentes):
                alvos.append(f"string:{valor}")
    return alvos


def coletar(src_dir: Path) -> tuple[list[Dependencia], list[str], int]:
    """(dependencias proibidas, erros, arquivos lidos)."""
    agentes = ids_de_agentes(src_dir)
    erros: list[str] = []
    if not agentes:
        return [], [f"{src_dir}/agents: nenhum pacote de agente — a cerca ficaria vacua"], 0
    deps: set[Dependencia] = set()
    lidos = 0
    for caminho in sorted(src_dir.rglob("*.py")):
        dono = _dono(caminho, src_dir, agentes)
        if dono == "qa":
            continue
        lidos += 1
        try:
            alvos = _alvos_do_arquivo(caminho, src_dir, agentes)
        except (SyntaxError, OSError, UnicodeDecodeError) as exc:
            erros.append(f"{caminho}: nao parseia ({type(exc).__name__}) — reprovado por precaucao")
            continue
        importador = caminho.relative_to(src_dir.parents[1]).as_posix()
        for alvo in alvos:
            agente_alvo = _agente_do_modulo(alvo.removeprefix("string:"), agentes)
            if dono is None:
                deps.add(Dependencia("plataforma->agente", importador, alvo))
            elif agente_alvo != dono:
                deps.add(Dependencia("agente->agente", importador, alvo))
    return sorted(deps), erros, lidos


def ler_baseline(caminho: Path) -> set[str]:
    if not caminho.is_file():
        return set()
    linhas = (linha.strip() for linha in caminho.read_text(encoding="utf-8").splitlines())
    return {linha for linha in linhas if linha and not linha.startswith("#")}


def escrever_baseline(caminho: Path, deps: Sequence[Dependencia]) -> None:
    corpo = [_CABECALHO_BASELINE]
    for direcao in ("plataforma->agente", "agente->agente"):
        grupo = [d for d in deps if d.direcao == direcao]
        corpo.append(f"\n# {direcao}: {len(grupo)}\n")
        corpo.extend(f"{d.chave()}\n" for d in grupo)
    caminho.write_text("".join(corpo), encoding="utf-8")


def catraca(deps: Sequence[Dependencia], baseline: set[str]) -> tuple[list[str], list[str]]:
    """(novas: no codigo e fora da baseline, velhas: na baseline e fora do codigo)."""
    atuais = {d.chave() for d in deps}
    return sorted(atuais - baseline), sorted(baseline - atuais)


def relatorio(deps: Sequence[Dependencia], src_dir: Path) -> str:
    """Inventario em markdown: o que mover, agrupado por agente alvo e por importador."""
    agentes = ids_de_agentes(src_dir)
    linhas = ["# Inventario da fronteira plataforma x agentes (ADR-0067)", ""]
    por_direcao = Counter(d.direcao for d in deps)
    linhas.append(
        f"Total: **{len(deps)}** dependencias proibidas — "
        f"plataforma->agente {por_direcao['plataforma->agente']}, "
        f"agente->agente {por_direcao['agente->agente']}."
    )
    for direcao in ("plataforma->agente", "agente->agente"):
        grupo = [d for d in deps if d.direcao == direcao]
        linhas += ["", f"## {direcao} ({len(grupo)})", ""]
        por_alvo: dict[str, list[Dependencia]] = defaultdict(list)
        for d in grupo:
            por_alvo[_agente_do_modulo(d.alvo.removeprefix("string:"), agentes) or "?"].append(d)
        for agente, itens in sorted(por_alvo.items(), key=lambda kv: -len(kv[1])):
            importadores = Counter(d.importador for d in itens)
            linhas.append(f"### `{agente}` — {len(itens)} dependencias, {len(importadores)} arquivos")
            linhas.append("")
            linhas.append("| Arquivo importador | Modulos do agente que ele usa |")
            linhas.append("|---|---|")
            for importador in sorted(importadores):
                modulos = sorted({d.alvo for d in itens if d.importador == importador})
                linhas.append(f"| `{importador}` | {', '.join(f'`{m}`' for m in modulos)} |")
            linhas.append("")
    linhas += ["## helena -> plataforma (informativo, sem catraca nesta fase)", ""]
    linhas += _helena_para_plataforma(src_dir)
    return "\n".join(linhas) + "\n"


def _helena_para_plataforma(src_dir: Path) -> list[str]:
    """Modulos internos da plataforma que a Helena usa: o candidato a 'SDK do runtime' (ADR-0067 §4)."""
    contagem: Counter[str] = Counter()
    for caminho in sorted((src_dir / "agents" / "helena").rglob("*.py")):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        pacote = _pacote_do_arquivo(caminho, src_dir)
        for no in ast.walk(arvore):
            modulos: list[str] = []
            if isinstance(no, ast.Import):
                modulos = [n.name for n in no.names]
            elif isinstance(no, ast.ImportFrom):
                base = no.module if no.level == 0 else _resolver_relativo(pacote, no.level, no.module)
                modulos = [base] if base else []
            for modulo in modulos:
                if modulo.startswith("maezo.") and not modulo.startswith(
                    ("maezo.agents.helena", "maezo.agents")
                ):
                    contagem[modulo] += 1
    linhas = ["| Modulo da plataforma | Imports |", "|---|---|"]
    linhas += [f"| `{m}` | {n} |" for m, n in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))]
    return linhas


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--src-dir", default=_RAIZ / "src" / "maezo", type=Path)
    parser.add_argument("--baseline", default=BASELINE_PADRAO, type=Path)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--relatorio", action="store_true", help="imprime o inventario em markdown")
    modo.add_argument("--gerar-baseline", action="store_true", help="reescreve a baseline com o estado atual")
    args = parser.parse_args(argv)

    deps, erros, lidos = coletar(args.src_dir)
    if erros:
        print(f"[fronteira-agentes] FAIL ({lidos} arquivos lidos)")
        for erro in erros:
            print(f"  {erro}")
        return 1
    if args.relatorio:
        print(relatorio(deps, args.src_dir), end="")
        return 0
    if args.gerar_baseline:
        escrever_baseline(args.baseline, deps)
        print(f"[fronteira-agentes] baseline gravada: {len(deps)} dependencias em {args.baseline}")
        return 0

    novas, velhas = catraca(deps, ler_baseline(args.baseline))
    if novas or velhas:
        print(f"[fronteira-agentes] FAIL ({lidos} arquivos lidos)")
        if novas:
            print(
                "  Dependencia NOVA para dentro de um agente (ADR-0067). Use o contrato em maezo.contratos:"
            )
            print("\n".join(f"    + {n}" for n in novas))
        if velhas:
            print("  Divida paga: remova da baseline (rode --gerar-baseline e commite a reducao):")
            print("\n".join(f"    - {v}" for v in velhas))
        return 1
    print(
        f"[fronteira-agentes] PASS ({lidos} arquivos lidos; "
        f"{len(deps)} dependencias na baseline, nenhuma nova)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
