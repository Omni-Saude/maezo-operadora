"""Fixture `repo` da chancela de regras (fábrica em `fabrica.py`, reusada pelos testes de CI)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.regras.fabrica import criar_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return criar_repo(tmp_path)
