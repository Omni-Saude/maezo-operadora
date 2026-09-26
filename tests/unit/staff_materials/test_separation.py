"""Regra D-F como fato de codigo: nenhum caminho de engenharia alcanca a raiz de instalacao.

Mede pela arvore de imports (AST), nao por convencao: se um modulo de engenharia passar a
importar `approver`, ou a citar o arquivo da chave raiz, este teste reprova.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

from tools.staff_materials.__main__ import build_parser

PACKAGE = Path(__file__).resolve().parents[3] / "tools/staff_materials"
APPROVER_ONLY = {"approver.py"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(("." * node.level) + module)
            names.update(f"{'.' * node.level}{module}.{alias.name}" for alias in node.names)
    return names


def test_engineering_modules_never_import_the_approver() -> None:
    engineering = sorted(p for p in PACKAGE.glob("*.py") if p.name not in APPROVER_ONLY)
    assert {p.name for p in engineering} >= {"__main__.py", "generate.py", "verify.py", "spec.py", "pki.py"}
    for path in engineering:
        imported = _imports(path)
        assert not any("approver" in name for name in imported), path.name
        text = path.read_text(encoding="utf-8")
        assert "installation-root-key" not in text, path.name


def test_engineering_cli_exposes_no_root_command() -> None:
    parser = build_parser()
    choices = {
        name
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name in action.choices
    }
    # `assemble` e `assignment-plane trust` recebem a raiz PUBLICA do aprovador; nenhum comando gera
    # ou assina com ela (o documento de dono e `approver sign-assignment-owner`).
    assert choices == {
        "generate",
        "assemble",
        "assignment-plane",
        "verify",
        "lock-sql",
        "native-secret",
        "human-bundle",
        "login-secrets",
        "next-designation",  # so o RASCUNHO N+1 (sem raiz); quem assina e `approver sign-designation`
    }
