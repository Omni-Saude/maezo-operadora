"""Provas das allowlists citadas pelas supressoes `nosec B608` / `nosemgrep` (R-110 / F-151).

Cada `-- <fonte>` de uma supressao em `src/maezo` aponta para uma allowlist fechada (constante de
codigo) ou para um validador ancorado de identificador. Este arquivo prova, por modulo, que:
  * a allowlist e' fechada e so' contem identificadores SQL simples; e
  * o validador recusa a carga de injecao (inclusive o `\\n` final que um `$` sem ancora aceitaria)
    ANTES de qualquer SQL ser montado.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Final

import pytest

# Identificador PostgreSQL simples, sem aspas: o unico formato que uma allowlist pode conter.
SIMPLES: Final[re.Pattern[str]] = re.compile(r"[a-z_][a-z0-9_]{0,62}")

_SRC: Final[Path] = Path(__file__).resolve().parents[3] / "src" / "maezo"

INJECOES: Final[tuple[str, ...]] = (
    'x"; DROP TABLE t; --',
    "x; DROP TABLE t",
    "x\n",
    "x OR 1=1",
    'x"."y',
    "",
    "X",
)


def _funcao(modulo: str, nome: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse((_SRC / modulo).read_bytes())
    achadas = [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == nome
    ]
    assert len(achadas) == 1, (modulo, nome)
    return achadas[0]


def iteraveis_literais_dos_lacos(modulo: str, funcao: str, variavel: str) -> list[Any]:
    """Valores literais dos iteraveis de cada `for` da funcao que liga `variavel` (o nome interpolado).

    Resolve o iteravel quando ele e' um nome local atribuido.

    `ast.literal_eval` so' aceita literal puro: se alguem trocar a tupla por algo computado ou vindo
    de fora, a prova falha em vez de passar calada.
    """
    fn = _funcao(modulo, funcao)
    locais = {
        alvo.id: no.value
        for no in ast.walk(fn)
        if isinstance(no, ast.Assign)
        for alvo in no.targets
        if isinstance(alvo, ast.Name)
    }
    valores = []
    for no in ast.walk(fn):
        if isinstance(no, ast.For | ast.AsyncFor) and variavel in {
            n.id for n in ast.walk(no.target) if isinstance(n, ast.Name)
        }:
            it = no.iter
            if isinstance(it, ast.Name):
                it = locais[it.id]
            valores.append(ast.literal_eval(it))
    return valores


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


@pytest.mark.asyncio
async def test_audit_anchor_verify_recusa_tenant_invalido_antes_de_conectar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maezo.gateway import audit_anchor_verify

    async def _nunca(*_a: object, **_k: object) -> None:
        raise AssertionError("conectou ao banco com tenant invalido")

    monkeypatch.setattr(audit_anchor_verify.asyncpg, "connect", _nunca)
    source = audit_anchor_verify.PostgresChainRecordSource("postgresql://u@h/db")
    with pytest.raises(ValueError, match="not a valid schema identifier"):
        await source.read_chain('amh"; DROP TABLE audit_chain; --')


# ---------------------------------------------------------------------------------------------
# gateway/capabilities/durability/postgres.py (observe_journey, recover)
# ---------------------------------------------------------------------------------------------


_DURABILITY: Final[str] = "gateway/capabilities/durability/postgres.py"


def test_durability_observe_journey_tabelas_e_colunas_sao_tupla_literal() -> None:
    assert iteraveis_literais_dos_lacos(_DURABILITY, "observe_journey", "table") == [
        (
            ("v21_capability_command", "command_ref"),
            ("v21_external_wait", "wait_ref"),
            ("v21_journal_outbox", "outbox_ref"),
        )
    ]


def test_durability_recover_entidades_sao_tupla_literal() -> None:
    (entidades,) = iteraveis_literais_dos_lacos(_DURABILITY, "recover", "table")
    assert [(tabela, coluna) for _, tabela, coluna, _ in entidades] == [
        ("v21_capability_command", "command_ref"),
        ("v21_journal_outbox", "outbox_ref"),
        ("v21_external_wait", "wait_ref"),
    ]
    assert all(SIMPLES.fullmatch(x) for _, t, c, _ in entidades for x in (t, c))


# ---------------------------------------------------------------------------------------------
# gateway/communications/admission.py e gateway/document_requests/admission.py (head + lock)
# ---------------------------------------------------------------------------------------------


class _ConexaoProibida:
    async def execute(self, *_a: object, **_k: object) -> None:
        raise AssertionError("SQL executado com modo de lock fora do mapa fechado")


def test_communications_head_sql_e_mapa_literal_fechado() -> None:
    from maezo.gateway.communications import admission

    base = (
        "SELECT revision,publication_ref,payload FROM portal_communication.authority_head "
        "WHERE tenant=:tenant AND environment=:environment AND access_digest=:access"
    )
    assert {
        "": base,
        " FOR SHARE": base + " FOR SHARE",
        " FOR UPDATE": base + " FOR UPDATE",
    } == admission._HEAD_SQL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lock", [" FOR SHARE; DROP TABLE t", "FOR UPDATE", " for update", " FOR NO KEY UPDATE"]
)
async def test_communications_head_recusa_lock_fora_do_mapa(lock: str) -> None:
    from maezo.gateway.communications import admission
    from maezo.gateway.external_cases.models import ExternalCaseError

    with pytest.raises(ExternalCaseError):
        await admission.head(_ConexaoProibida(), object(), lock=lock)  # type: ignore[arg-type]
