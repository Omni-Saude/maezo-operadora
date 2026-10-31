"""ADR-0068: os simbolos fantasmas nao voltam e a ADR nova so cita o que existe (R-020 / DEC-11).

ADR-0016 e ADR-0022 descreviam um `ToolRegistry` "unico caminho de saida", um `PhiZoneGateway` que
pseudonimizava resultados e um `register_tools(registry)` "load-bearing" — nenhum dos tres existia
como descrito (os `register_tools` dos servers MCP existiam sem nenhum chamador). A ADR-0068 documenta
o desenho real; esta cerca impede que:

  (1) alguem reintroduza, em `src/`, uma classe/funcao/metodo/chamada com esses nomes — uma segunda
      "autoridade" ao lado de `gateway/tool_registry.py` e o risco D-02 que a ADR-0068 fecha. A
      cerca olha a AST: docstring e comentario podem continuar contando a historia;
  (2) a ADR-0068 cite um simbolo/arquivo que nao resolve — reusa o gate R-089
      (`scripts/ci/check_doc_symbol_citations.py::run_gate`) sobre um diretorio com SO a ADR nova,
      sem nenhuma entrada de rot divulgado aplicavel;
  (3) as ADRs parcialmente superadas percam o adendo datado que aponta para a ADR-0068.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from typing import Final

import pytest
from scripts.ci import check_doc_symbol_citations as citation_gate

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SRC: Final[Path] = _REPO_ROOT / "src" / "maezo"
_ADR_DIR: Final[Path] = _REPO_ROOT / "docs" / "adr"
_ADR_0068: Final[str] = "0068-desenho-real-da-barreira-de-efeitos-e-cerca-de-zona.md"
_PHANTOMS: Final[frozenset[str]] = frozenset({"ToolRegistry", "PhiZoneGateway", "register_tools"})


def _phantom_hits(source: str, filename: str) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: set[str] = set()
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {node.name}
        elif isinstance(node, ast.Name):
            names = {node.id}
        elif isinstance(node, ast.Attribute):
            names = {node.attr}
        elif isinstance(node, ast.alias):
            names = set(node.name.split(".")) | ({node.asname} if node.asname else set())
        for name in sorted(names & _PHANTOMS):
            hits.append(f"{filename}:{getattr(node, 'lineno', '?')} {name}")
    return hits


def test_phantom_symbols_are_not_reintroduced_in_src() -> None:
    files = sorted(_SRC.rglob("*.py"))
    assert len(files) > 100, "varredura de src/ vazia — a cerca seria vacua"
    hits: list[str] = []
    for path in files:
        hits.extend(_phantom_hits(path.read_text(encoding="utf-8"), str(path.relative_to(_REPO_ROOT))))
    assert hits == [], (
        "simbolo fantasma reintroduzido (ADR-0068: a autoridade unica e gateway/tool_registry.py; "
        f"nao ha ToolRegistry/PhiZoneGateway/register_tools): {hits}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "class ToolRegistry:\n    pass\n",
        "class PhiZoneGateway:\n    pass\n",
        "class S:\n    def register_tools(self, registry):\n        pass\n",
        "server.register_tools(registry)\n",
        "from maezo.tools.registry import ToolRegistry\n",
        "import x.PhiZoneGateway as P\n",
    ],
)
def test_phantom_scanner_catches_each_shape(source: str) -> None:
    assert _phantom_hits(source, "f.py"), source


def test_phantom_scanner_ignores_docstrings_and_comments() -> None:
    source = (
        '"""ToolRegistry e PhiZoneGateway nunca existiram."""\n# register_tools era codigo morto\nx = 1\n'
    )
    assert _phantom_hits(source, "f.py") == []


def test_adr_0068_cites_only_resolvable_symbols(tmp_path: Path) -> None:
    adr = _ADR_DIR / _ADR_0068
    assert adr.is_file(), f"{_ADR_0068} ausente"
    isolated = tmp_path / "adr"
    isolated.mkdir()
    shutil.copy2(adr, isolated / _ADR_0068)
    assert not any(entry.doc == _ADR_0068 for entry in citation_gate._DISCLOSED_ROT), (
        "a ADR-0068 nao pode depender de rot divulgado"
    )
    result = citation_gate.run_gate(_REPO_ROOT, isolated)
    assert result.ok, result.render()
    assert result.disclosed == [], result.render()
    assert result.eligible_citation_count >= 10, (
        f"so {result.eligible_citation_count} citacoes checaveis — a ADR precisa ancorar o desenho em "
        "simbolos `path::Simbolo` resolviveis"
    )


def test_adr_0068_names_every_invariant() -> None:
    text = (_ADR_DIR / _ADR_0068).read_text(encoding="utf-8")
    for invariant in ("I-1", "I-2", "I-3", "I-4", "I-5", "I-6", "I-7"):
        assert f"**{invariant}" in text, f"ADR-0068 nao declara {invariant}"


@pytest.mark.parametrize(
    "adr",
    ["0016-process-key-allowlist-phi-invariant.md", "0022-mcp-in-process-boot.md"],
)
def test_superseded_adrs_carry_the_dated_addendum(adr: str) -> None:
    path = _ADR_DIR / adr
    assert path.is_file(), adr
    text = path.read_text(encoding="utf-8")
    assert "ADENDO DE STATUS 2026-10-31" in text, f"{adr} sem o adendo datado"
    assert "ADR-0068" in text, f"{adr} nao aponta para a ADR-0068"


def test_readme_index_lists_adr_0068() -> None:
    readme = (_ADR_DIR / "README.md").read_text(encoding="utf-8")
    assert "| 0068 |" in readme
