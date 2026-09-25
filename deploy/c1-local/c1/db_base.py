"""Passo `db-base`: o layout do dev medido em M2 (plano §7.2), antes da T1.4.

* database `maezo`, dono `maezo_app`; schema `cibseven`, dono `cibseven_app` (os `ACT_*` nascem no
  `engine-bootstrap`, com a imagem viva);
* schema `amh` com as tabelas de identidade da migracao 0012, aplicadas pelo proprio texto da
  migracao (a 0001 exige pgvector, que o postgres:17-alpine nao tem: so a 0012 importa aqui);
* Onda 8 (plano humano do BFF): no mesmo schema, pelo texto das migracoes, a cadeia de auditoria
  (0002, 0005), o outbox humano (0013) e a fonte de atribuicao (0014) - as 6 relacoes que
  `deploy/sql/portal-human-plane-grants.sql` e `portal-assignment-admin-grants.sql` concedem.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import asyncpg

from .common import ADMIN, DATABASE, HARNESS_MARKER, HARNESS_MARKER_SCHEMA, TENANT, admin_dsn, read_text, step, tls_context

VERSIONS = Path("/repo/src/maezo/platform/migrations/versions")
MIGRATION = VERSIONS / "0012_portal_identity_session.py"
#: (migracao, tabela que prova que ela ja rodou) - a ordem e a das revisoes.
HUMAN_PLANE = (
    ("0002_audit_chain.py", "audit_chain"),
    ("0005_audit_emit_dedup.py", "audit_emit_dedup"),
    ("0013_human_command_outbox.py", "human_command_outbox"),
    ("0014_staff_assignment_authority.py", "portal_assignment_source"),
)


def _migration_statements(path: Path = MIGRATION) -> list[str]:
    statements: list[str] = []
    spec = importlib.util.spec_from_file_location("c1_" + path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["alembic"] = SimpleNamespace(op=SimpleNamespace(execute=statements.append))  # type: ignore[assignment]
    try:
        spec.loader.exec_module(module)
        module.upgrade()
    finally:
        del sys.modules["alembic"]
    return statements


async def main_async() -> None:
    ssl = tls_context()
    su = await asyncpg.connect(admin_dsn(database="postgres"), ssl=ssl, timeout=10)
    try:
        for role, file in (("cibseven_app", "cibseven-password"), ("maezo_app", "maezo-app-password")):
            secret = read_text(ADMIN / file)
            exists = await su.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", role)
            verb = "ALTER" if exists else "CREATE"
            await su.execute(f"{verb} ROLE {role} LOGIN PASSWORD '{secret}'")
        if not await su.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", DATABASE):
            await su.execute(f"CREATE DATABASE {DATABASE} OWNER maezo_app")
    finally:
        await su.close()
    su = await asyncpg.connect(admin_dsn(), ssl=ssl, timeout=10)
    try:
        await su.execute("CREATE SCHEMA IF NOT EXISTS cibseven AUTHORIZATION cibseven_app")
        # Marcador do Postgres DESCARTAVEL do harness: o passo `auth-fixture` recusa rodar sem ele.
        await su.execute(f"CREATE SCHEMA IF NOT EXISTS {HARNESS_MARKER_SCHEMA}")
        await su.execute(f"COMMENT ON SCHEMA {HARNESS_MARKER_SCHEMA} IS '{HARNESS_MARKER}'")
    finally:
        await su.close()
    app = await asyncpg.connect(
        admin_dsn(user="maezo_app", password_file="maezo-app-password"), ssl=ssl, timeout=10
    )
    try:
        await app.execute(f"CREATE SCHEMA IF NOT EXISTS {TENANT}")
        if not await app.fetchval("SELECT to_regclass($1)", f"{TENANT}.portal_memberships"):
            async with app.transaction():
                await app.execute(f"SET LOCAL search_path = {TENANT}")
                for statement in _migration_statements():
                    await app.execute(statement)
        for name, table in HUMAN_PLANE:
            if not await app.fetchval("SELECT to_regclass($1)", f"{TENANT}.{table}"):
                async with app.transaction():
                    await app.execute(f"SET LOCAL search_path = {TENANT}")
                    for statement in _migration_statements(VERSIONS / name):
                        await app.execute(statement)
        human = await app.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname=$1 AND tablename = ANY($2::text[])",
            TENANT, [t for _, t in HUMAN_PLANE] + ["human_command_delivery", "portal_assignment_publications"],
        )
        tables = await app.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname=$1 AND tablename LIKE 'portal_%'", TENANT
        )
    finally:
        await app.close()
    ok = tables == 4 + 3 and human == 6
    step("db-base", ok, f"maezo/cibseven/amh criados; {tables} tabelas portal_* em {TENANT} (0012+0014); "
         f"{human}/6 do plano humano (0002/0005/0013/0014)")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
