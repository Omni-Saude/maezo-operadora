"""Alembic environment — tenant-aware search_path (DL-0017).

Per DL-0017:
- pgvector extension lives in schema 'public' (type/operator is DB-global).
- Per-tenant schemas are isolated; DDL is NOT schema-qualified.
- search_path is set per-connection to "{tenant}, public" so that
  all tables are created in the correct tenant schema and the pgvector
  type/operator (in public) remains visible.

To run migrations targeting a specific tenant:
    alembic -x tenant=amh upgrade head

The tenant is extracted from the Alembic x-argument 'tenant'.
Without it, the default search_path is 'public'.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic Config object (provides access to .ini values)
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Tenant-aware search_path
# ---------------------------------------------------------------------------
# The tenant can be set via:
#   1. -x tenant=<name> CLI argument (highest priority)
#   2. MAEZO_TENANT_ID environment variable
#   3. Default: 'public'
# ---------------------------------------------------------------------------
_x_tenant: str | None = None
try:
    _x_tenant = context.get_x_argument(as_dictionary=True).get("tenant")
except Exception:
    _x_tenant = None

TENANT_ID: str = _x_tenant or os.environ.get("MAEZO_TENANT_ID", "public")

# search_path: tenant schema first, then public (for pgvector type/operator).
# This mirrors the pattern used by PostgresAuditSink and PostgresIdempotencyStore
# (setup=_set_search_path on asyncpg pool acquire — DL-0017).
#
# T1.10 fix: this used to be built as a single comma-joined STRING (e.g. "public, public")
# and passed to `SET search_path = '<that string>'` (see run_async_migrations below) — Postgres
# treats a single quoted value as ONE identifier, so search_path was actually being set to a
# schema literally named `public, public`, which never exists. Every real `alembic upgrade head`
# against a live Postgres therefore failed ("no schema has been selected to create in"); this was
# never caught because no task before T1.10 exercised migrations against a real server (discovered
# while building the kill-test for PostgresAuditSink). Kept as a list of schema names here;
# `run_async_migrations` renders each entry as its own quoted identifier.
SEARCH_PATH: tuple[str, ...] = tuple(dict.fromkeys((TENANT_ID, "public")))  # de-dup, preserve order

# ---------------------------------------------------------------------------
# URL do banco — ambiente ANTES do alembic.ini
# ---------------------------------------------------------------------------
# `alembic.ini` carrega `postgresql+asyncpg://maezo:maezo@postgres:5432/maezo`:
# o host `postgres` do docker-compose. Util em dev, fatal em qualquer outro lugar.
#
# Ate 2026-08-13 este env.py NAO lia variavel de ambiente nenhuma — sempre usava a
# URL do .ini. O Job de migrations do chart (job-migrations.yaml:100) exportava
# `ALEMBIC_DATABASE_URL` e o comentario dele afirmava "env.py le
# ALEMBIC_DATABASE_URL"; a afirmacao era falsa e a variavel morria sem leitor. Como
# a CD e' no-op sem AWS_ENABLED, isso nunca foi exercitado: as migrations JAMAIS
# teriam funcionado contra um banco real. Descoberto na primeira execucao real, na
# task ECS 65e0522fab004c4a937a2d2dca2b8dfc, com
# `socket.gaierror: [Errno -2] Name or service not known` ao tentar resolver
# `postgres` dentro da VPC.
#
# Precedencia: ALEMBIC_DATABASE_URL > DATABASE_URL > alembic.ini.
#
# Por que sobrescrever o dicionario de config e nao chamar
# `config.set_main_option("sqlalchemy.url", ...)`: a senha vem percent-encoded do
# cofre e pode conter `%`, que o ConfigParser do alembic trata como interpolacao e
# quebra na leitura. O dicionario nao passa por interpolacao nenhuma.
#
# O driver TEM de ser assincrono (`+asyncpg`): `run_async_migrations` usa
# `async_engine_from_config`. Uma URL `+psycopg` falha com "The asyncio extension
# requires an async driver".
_ENV_DB_URL: str | None = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _config_section_with_url() -> dict[str, str]:
    """Secao de config do alembic, com a URL do ambiente quando houver."""
    section = dict(config.get_section(config.config_ini_section) or {})
    if _ENV_DB_URL:
        section["sqlalchemy.url"] = _ENV_DB_URL
    return section


# ---------------------------------------------------------------------------
# Metadata target (None = use raw DDL in migrations, no model reflection)
# ---------------------------------------------------------------------------
target_metadata = None


def include_object(
    object: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Filter objects during autogenerate (not used — raw DDL only)."""
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without connecting).

    search_path is not injected in offline mode (no session).
    """
    url = _ENV_DB_URL or config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=f"{TENANT_ID}_alembic_version",
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations on a live connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table=f"{TENANT_ID}_alembic_version",
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Configure an async engine and run migrations online.

    Per DL-0017, the search_path is set via SET before executing migrations.
    """
    config_section = _config_section_with_url()
    connectable = async_engine_from_config(
        config_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # Set the tenant-aware search_path for this migration session.
        # Per DL-0017: tenant schema first, then public for pgvector.
        # Each schema is its own quoted identifier (T1.10 fix — see SEARCH_PATH comment above:
        # a single quoted string here would set search_path to one bogus schema name).
        search_path_sql = ", ".join(f'"{schema}"' for schema in SEARCH_PATH)
        await connection.execute(text(f"SET search_path TO {search_path_sql}"))
        # T1.10 fix: SQLAlchemy 2.x AsyncConnection autobegins a transaction on first
        # execute() ("commit as you go"). Handing that still-open transaction straight to
        # Alembic's own `context.begin_transaction()` nests inside it instead of owning it —
        # Alembic commits ITS transaction, but the OUTER one from this SET never gets an
        # explicit commit, so closing the connection at the end of this `async with` block
        # silently ROLLS BACK everything, DDL included. Every real `alembic upgrade head` run
        # against a live Postgres was therefore a no-op (verified: table count 0 after a
        # "successful" upgrade — caught while building the kill-test for PostgresAuditSink,
        # T1.10). Commit the SET before the migrations run so Alembic starts its own,
        # cleanly-owned transaction.
        await connection.commit()
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run online migrations with async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
