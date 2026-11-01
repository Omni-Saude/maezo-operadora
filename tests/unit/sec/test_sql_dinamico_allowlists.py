"""Provas das allowlists citadas pelas supressoes `nosec B608` / `nosemgrep` (R-110 / F-151).

Cada `-- <fonte>` de uma supressao em `src/maezo` aponta para uma allowlist fechada (constante de
codigo) ou para um validador ancorado de identificador. Este arquivo prova, por modulo, que:
  * a allowlist e' fechada e so' contem identificadores SQL simples; e
  * o validador recusa a carga de injecao (inclusive o `\\n` final que um `$` sem ancora aceitaria)
    ANTES de qualquer SQL ser montado.
"""

from __future__ import annotations

import re
from typing import Final

import pytest

# Identificador PostgreSQL simples, sem aspas: o unico formato que uma allowlist pode conter.
SIMPLES: Final[re.Pattern[str]] = re.compile(r"[a-z_][a-z0-9_]{0,62}")

INJECOES: Final[tuple[str, ...]] = (
    'x"; DROP TABLE t; --',
    "x; DROP TABLE t",
    "x\n",
    "x OR 1=1",
    'x"."y',
    "",
    "X",
)


# ---------------------------------------------------------------------------------------------
# gateway/audit_postgres.py (INSERT_SQL) e gateway/audit_anchor_verify.py (version_sql)
# ---------------------------------------------------------------------------------------------


def test_audit_postgres_insert_sql_so_interpola_colunas_literais() -> None:
    from maezo.gateway import audit_postgres

    assert all(SIMPLES.fullmatch(col) for col in audit_postgres._COLUMNS)
    n = len(audit_postgres._COLUMNS)
    assert audit_postgres.INSERT_SQL == (
        "INSERT INTO audit_chain (timestamp, tenant_id, agent_id, agent_version, action, decision, "
        "input_hash, decision_basis, dmn_versions, model_id, prompt_version, record_hash, "
        "prev_record_hash) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11, $12, $13)"
    )
    assert n == 13


@pytest.mark.parametrize("carga", INJECOES)
def test_schema_for_tenant_recusa_injecao(carga: str) -> None:
    """`schema_for_tenant` e' a fonte do identificador em audit_anchor_verify.version_sql."""
    from maezo.gateway.audit_postgres import schema_for_tenant

    with pytest.raises(ValueError, match="not a valid schema identifier"):
        schema_for_tenant(carga)
