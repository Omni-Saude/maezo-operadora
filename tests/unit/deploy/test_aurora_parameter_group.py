"""Cercas sobre o parameter group do modulo Terraform `aurora-postgres`.

DU-03 (decisao do dono R-003): `shared_preload_libraries` so aceita bibliotecas
carregadas no start do servidor. `pgvector` e uma extensao SQL (`CREATE EXTENSION`),
nao uma preload lib — declara-la ali faz o boot do cluster RDS falhar no primeiro
`terraform apply`, exatamente no momento (G4) em que diagnosticar e mais caro.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_AURORA_MAIN_TF = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "terraform"
    / "modules"
    / "aurora-postgres"
    / "main.tf"
)

#: Bibliotecas que de fato sao carregadas no start do servidor Postgres e, portanto,
#: sao os unicos valores legitimos de `shared_preload_libraries`.
_REAL_PRELOAD_LIBRARIES = frozenset({"pg_stat_statements", "pg_cron", "auto_explain", "pgaudit"})


def _shared_preload_libraries() -> list[str]:
    """Extrai os tokens do parameter `shared_preload_libraries` do modulo Aurora."""
    source = _AURORA_MAIN_TF.read_text(encoding="utf-8")
    match = re.search(
        r'name\s*=\s*"shared_preload_libraries"\s*\n\s*value\s*=\s*"([^"]*)"',
        source,
    )
    assert match is not None, (
        f"parameter `shared_preload_libraries` nao encontrado em {_AURORA_MAIN_TF}"
    )
    return [token.strip() for token in match.group(1).split(",") if token.strip()]


def test_shared_preload_libraries_mantem_pg_stat_statements() -> None:
    """A remocao de pgvector nao pode levar junto `pg_stat_statements` (R-003)."""
    assert "pg_stat_statements" in _shared_preload_libraries()


@pytest.mark.parametrize("token", ["pgvector", "vector"])
def test_shared_preload_libraries_sem_pgvector(token: str) -> None:
    """pgvector nao e preload lib — sua presenca quebra o boot do cluster (DU-03)."""
    assert token not in _shared_preload_libraries()


def test_shared_preload_libraries_apenas_bibliotecas_reais() -> None:
    """Nenhuma extensao criada por `CREATE EXTENSION` pode voltar a este parameter."""
    tokens = set(_shared_preload_libraries())
    assert tokens <= _REAL_PRELOAD_LIBRARIES, (
        f"tokens nao reconhecidos como preload libs: {sorted(tokens - _REAL_PRELOAD_LIBRARIES)}"
    )
