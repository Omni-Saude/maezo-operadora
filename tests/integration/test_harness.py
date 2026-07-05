"""Integration tests for maezo.runtime.harness — against real PostgreSQL.

ADR-0001: LangGraph checkpointer with PostgreSQL.
ADR-0002: Working memory layer.

Requires: docker-compose profile core running (postgres on 5433).
Run: make dev-stack && uv run pytest tests/integration -m integration
"""

from __future__ import annotations

import os

import pytest

from maezo.runtime.harness import HarnessConfig, MaezoHarness
from maezo.runtime.inference import InferenceClient, ModelConfig, NoopProvider


def _check_postgres() -> bool:
    """Check if PostgreSQL is available for integration tests."""
    import socket

    host = os.environ.get("MAEZO_PG_HOST", "localhost")
    port = int(os.environ.get("MAEZO_PG_HOST_PORT", "5433"))
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


requires_postgres = pytest.mark.skipif(
    not _check_postgres(),
    reason="PostgreSQL not available — start with: make dev-stack",
)


@pytest.mark.integration
class TestHarnessIntegration:
    """Integration tests for MaezoHarness with real PostgreSQL."""

    @requires_postgres
    @pytest.mark.asyncio
    async def test_harness_start_stop(self) -> None:
        """Harness should connect to PostgreSQL, start, and stop cleanly."""
        db_url = os.environ.get(
            "MAEZO_DB_URL",
            "postgresql://maezo:maezo@localhost:5433/maezo",
        )
        harness = MaezoHarness(HarnessConfig(db_url=db_url))
        await harness.start()

        try:
            assert harness.is_ready
            assert harness.checkpointer is not None

            from fastapi.testclient import TestClient

            client = TestClient(harness.app)
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["checkpointer"] == "connected"
        finally:
            await harness.stop()

        assert not harness.is_ready
        assert harness.checkpointer is None

    @requires_postgres
    @pytest.mark.asyncio
    async def test_harness_with_inference(self) -> None:
        """Harness with inference client reports both as ready."""
        db_url = os.environ.get(
            "MAEZO_DB_URL",
            "postgresql://maezo:maezo@localhost:5433/maezo",
        )
        harness = MaezoHarness(HarnessConfig(db_url=db_url))
        model = ModelConfig(model_id="noop", provider="noop")
        inference = InferenceClient(default_model=model, providers={"noop": NoopProvider()})
        harness.set_inference(inference)

        await harness.start()

        try:
            from fastapi.testclient import TestClient

            client = TestClient(harness.app)
            response = client.get("/health")
            data = response.json()
            assert data["inference"] == "ready"
        finally:
            await harness.stop()


@pytest.mark.integration
class TestCheckpointerIntegration:
    """Integration tests for build_checkpointer with real PostgreSQL."""

    @requires_postgres
    @pytest.mark.asyncio
    async def test_checkpointer_connection(self) -> None:
        """build_checkpointer should connect to PostgreSQL."""
        from maezo.runtime.checkpoint import build_checkpointer

        db_url = os.environ.get(
            "MAEZO_DB_URL",
            "postgresql://maezo:maezo@localhost:5433/maezo",
        )
        async with build_checkpointer(db_url) as checkpointer:
            assert checkpointer is not None
            # Verify we can list checkpoints (empty at this point)
            # The checkpointer object should be a valid AsyncPostgresSaver
            assert hasattr(checkpointer, "get_tuple")
            assert hasattr(checkpointer, "put")
