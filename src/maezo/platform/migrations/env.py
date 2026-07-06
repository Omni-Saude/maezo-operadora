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
SEARCH_PATH: str = f"{TENANT_ID}, public"

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
    url = config.get_main_option("sqlalchemy.url")
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
    config_section = config.get_section(config.config_ini_section) or {}
    connectable = async_engine_from_config(
        config_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # Set the tenant-aware search_path for this migration session.
        # Per DL-0017: tenant schema first, then public for pgvector.
        await connection.execute(text(f"SET search_path = '{SEARCH_PATH}'"))
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run online migrations with async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
