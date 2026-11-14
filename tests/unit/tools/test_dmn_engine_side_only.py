"""Cerca pos-poda do avaliador DMN in-process (R-097 / F-115, ADR-0028 §"Fate").

ADR-0028 deprecou o `DmnServer` local (parser XML que nao avalia FEEL de comparacao/faixa/lista)
e fixou a avaliacao de DMN em runtime como ENGINE-SIDE ONLY, via
`maezo.tools.workers.dmn_transport` (CIB Seven REST, fail-closed). O ciclo de deprecacao terminou:
o modulo do avaliador foi podado. O que precisa continuar verdadeiro depois da poda:

1. nenhuma classe `DmnServer` volta a existir em `src/` (sem segundo motor FEEL a derivar);
2. nenhum pacote do catalogo MCP (`src/maezo/tools/mcp_*`) importa parser XML — um parser XML
   dentro de um pacote MCP e exatamente a forma do avaliador in-process removido;
3. o servidor `dmn` continua no catalogo MCP fechado (`known_mcp_servers`), porque os agentes
   declaram `mcp-dmn.evaluate` e a validacao `validate-artifacts` cruza `tools` x `mcp_*`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from maezo.platform.validation.agent_def import known_mcp_servers

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
_TOOLS_ROOT = _SRC / "maezo" / "tools"

# Modulos de parsing XML (stdlib + terceiros + o wrapper anti-XXE da casa). Qualquer um deles
# importado dentro de um pacote MCP reintroduziria avaliacao local de artefato DMN.
_XML_PARSER_MODULES = (
    "xml",
    "lxml",
    "defusedxml",
    "maezo.platform.xml_safe",
)


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.lineno))
            if node.module == "maezo.platform":
                found.extend((f"maezo.platform.{alias.name}", node.lineno) for alias in node.names)
    return found


def _is_xml_parser(module: str) -> bool:
    return any(module == m or module.startswith(f"{m}.") for m in _XML_PARSER_MODULES)


def test_no_dmnserver_class_defined_anywhere_in_src() -> None:
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}:{node.lineno}"
        for path in _SRC.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and node.name == "DmnServer"
    ]
    assert offenders == [], (
        "o avaliador DMN in-process foi podado (R-097/ADR-0028); avaliacao de DMN e engine-side "
        f"via maezo.tools.workers.dmn_transport: {offenders}"
    )


def test_no_mcp_package_imports_an_xml_parser() -> None:
    mcp_packages = sorted(d for d in _TOOLS_ROOT.iterdir() if d.is_dir() and d.name.startswith("mcp_"))
    assert mcp_packages, "catalogo MCP vazio — a cerca ficaria vacua"
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}:{lineno} imports {module}"
        for package in mcp_packages
        for path in sorted(package.rglob("*.py"))
        for module, lineno in _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
        if _is_xml_parser(module)
    ]
    assert offenders == [], (
        "pacote MCP importa parser XML — forma do avaliador DMN in-process podado (R-097); "
        f"DMN e avaliada pelo engine (ADR-0028): {offenders}"
    )


def test_xml_parser_detection_is_not_vacuous() -> None:
    """A cerca acima reconhece as formas de import que o avaliador podado usava."""
    probe = ast.parse(
        "import xml.etree.ElementTree as ET\n"
        "from defusedxml import ElementTree\n"
        "from maezo.platform.xml_safe import parse_trusted_xml\n"
        "from maezo.platform import xml_safe\n"
        "import json\n"
    )
    flagged = [m for m, _ in _imported_modules(probe) if _is_xml_parser(m)]
    assert flagged == [
        "xml.etree.ElementTree",
        "defusedxml",
        "maezo.platform.xml_safe",
        "maezo.platform.xml_safe",
    ]


def test_dmn_server_stays_in_the_closed_mcp_catalog() -> None:
    """Os agentes declaram `mcp-dmn.evaluate`; a poda do avaliador nao pode tirar o servidor
    `dmn` do catalogo derivado de `src/maezo/tools/mcp_*`."""
    assert "dmn" in known_mcp_servers(_TOOLS_ROOT)
