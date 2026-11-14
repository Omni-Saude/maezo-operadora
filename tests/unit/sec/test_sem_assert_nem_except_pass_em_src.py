"""Cerca AST (R-109 / F-150): nenhum `assert` e nenhum `except` largo com corpo `pass` em `src/maezo`.

`python -O` remove todo `assert`: uma guarda escrita como `assert` some em producao otimizada e o
codigo segue com o estado invalido (bandit B101). Um `except Exception: pass` engole o defeito sem
rastro (bandit B110). O R-109 triou os 118 + 6 sitios um a um; esta cerca impede que voltem sem que
o bandit precise rodar no CI.

Ler por AST, nunca por `grep`: a palavra `assert` aparece em docstrings e comentarios.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_SRC: Final[Path] = Path(__file__).resolve().parents[3] / "src" / "maezo"
_LARGOS: Final[frozenset[str]] = frozenset({"Exception", "BaseException"})


def _fontes(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _asserts(root: Path) -> list[str]:
    achados: list[str] = []
    for path in _fontes(root):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        achados.extend(
            f"{path.relative_to(root)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
        )
    return achados


def _except_pass(root: Path) -> list[str]:
    achados: list[str] = []
    for path in _fontes(root):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            largo = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in _LARGOS)
            if largo and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                achados.append(f"{path.relative_to(root)}:{node.lineno}")
    return achados


def test_src_nao_tem_assert() -> None:
    assert _asserts(_SRC) == []


def test_src_nao_tem_except_largo_com_pass() -> None:
    assert _except_pass(_SRC) == []


def test_a_cerca_enxerga_os_dois_padroes(tmp_path: Path) -> None:
    """Nao-vacuidade: a mesma varredura acusa um `assert` e um `except Exception: pass` plantados."""
    (tmp_path / "plantado.py").write_text(
        "def f(x):\n    assert x is not None\n    try:\n        g()\n    except Exception:\n        pass\n"
        "    try:\n        g()\n    except:\n        pass\n    try:\n        g()\n    except KeyError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    assert _asserts(tmp_path) == ["plantado.py:2"]
    # `except KeyError: pass` e' estreito (fora do B110 padrao); so' os largos sao acusados.
    assert _except_pass(tmp_path) == ["plantado.py:5", "plantado.py:9"]
