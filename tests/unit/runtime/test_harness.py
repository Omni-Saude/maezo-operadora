"""Unit tests for maezo.runtime.harness — LangGraph harness.

ADR-0001: LangGraph + checkpointer PostgreSQL = agent runtime.
ADR-0002: Working memory via LangGraph checkpointer.

GREEN phase: Harness with FastAPI health endpoint, async start/stop lifecycle.
"""

from __future__ import annotations

import contextlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maezo.runtime.harness import HarnessConfig, MaezoHarness
from maezo.runtime.inference import InferenceClient, ModelConfig, NoopProvider


class TestHarnessConfig:
    """HarnessConfig — runtime harness configuration."""

    def test_defaults(self) -> None:
        config = HarnessConfig()
        assert "maezo" in config.db_url
        assert config.health_port == 8000
        assert config.search_path == "agents, public"

    def test_custom_db_url(self) -> None:
        config = HarnessConfig(db_url="postgresql://user:***@host:5432/db")
        assert config.db_url == "postgresql://user:***@host:5432/db"


class TestMaezoHarness:
    """MaezoHarness — the main runtime entry point."""

    def test_harness_initialization(self) -> None:
        harness = MaezoHarness()
        assert harness.is_ready is False
        assert harness.checkpointer is None
        assert harness.inference is None
        assert harness.metrics is not None
        assert harness.app is not None

    def test_health_endpoint_returns_degraded_before_start(self) -> None:
        """Before start(), health check should return degraded (503)."""
        harness = MaezoHarness()
        client = TestClient(harness.app)
        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["checkpointer"] == "disconnected"
        assert data["inference"] == "not_configured"

    def test_metrics_endpoint_exists(self) -> None:
        harness = MaezoHarness()
        client = TestClient(harness.app)
        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_harness_app_is_fastapi(self) -> None:
        harness = MaezoHarness()
        assert isinstance(harness.app, FastAPI)
        assert harness.app.title == "MAEZO Agent Runtime"

    def test_set_inference_updates_health(self) -> None:
        """After set_inference(), health reports inference as ready."""
        harness = MaezoHarness()
        model = ModelConfig(model_id="noop", provider="noop")
        client = InferenceClient(default_model=model, providers={"noop": NoopProvider()})
        harness.set_inference(client)

        assert harness.inference is not None
        # Health still degraded because checkpointer is not connected
        test_client = TestClient(harness.app)
        response = test_client.get("/health")
        data = response.json()
        assert data["inference"] == "ready"
        assert data["checkpointer"] == "disconnected"

    @pytest.mark.asyncio
    async def test_start_without_database_raises_connection_error(self) -> None:
        """start() raises ConnectionError when no database is available."""
        harness = MaezoHarness(HarnessConfig(db_url="postgresql://test:***@localhost:5432/nonexistent"))
        with pytest.raises(ConnectionError):
            await harness.start()

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_safe(self) -> None:
        """stop() is safe to call even when never started."""
        harness = MaezoHarness()
        await harness.stop()  # Should not raise
        assert harness.is_ready is False

    @pytest.mark.asyncio
    async def test_start_then_stop(self) -> None:
        """start() fails gracefully without DB; stop() cleans up."""
        harness = MaezoHarness(HarnessConfig(db_url="postgresql://test:***@localhost:5432/nonexistent"))
        with contextlib.suppress(ConnectionError):
            await harness.start()

        await harness.stop()  # Cleanup should succeed
        assert harness.is_ready is False
        assert harness.checkpointer is None
