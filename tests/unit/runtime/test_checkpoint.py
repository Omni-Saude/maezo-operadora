"""Unit tests for maezo.runtime.checkpoint — LangGraph checkpointer.

ADR-0002: Working memory layer via LangGraph checkpointer.
DL-0017: pgvector in public, asyncpg setup= callback, search_path includes public.

GREEN phase: build_checkpointer() returns an async context manager for
AsyncPostgresSaver. The actual database connection requires docker-compose
(integration test). These unit tests validate the interface contract.
"""

from __future__ import annotations

import pytest

from maezo.runtime.checkpoint import build_checkpointer


class TestBuildCheckpointer:
    """build_checkpointer — factory for LangGraph AsyncPostgresSaver."""

    def test_returns_async_context_manager(self) -> None:
        """GREEN: build_checkpointer returns an async context manager."""
        cm = build_checkpointer("postgresql://test:test@localhost:5432/db")
        assert hasattr(cm, "__aenter__")
        assert hasattr(cm, "__aexit__")

    @pytest.mark.asyncio
    async def test_context_manager_fails_without_database(self) -> None:
        """When no database is available, context manager should raise connection error."""
        with pytest.raises(Exception):  # noqa: B017 — psycopg raises connection-specific errors
            async with build_checkpointer("postgresql://test:***@localhost:5432/nonexistent"):
                pass  # Should not reach here without a running database

    def test_accepts_db_url_string(self) -> None:
        """Factory should accept a connection string URL."""
        cm = build_checkpointer("postgresql://user:pass@host:5432/dbname")
        assert cm is not None
