"""Harness — LangGraph runtime harness with health check.

ADR-0001: LangGraph (Python) + checkpointer PostgreSQL = runtime dos agentes.
ADR-0002: Working memory via LangGraph checkpointer.
ADR-0009: Inference via provider abstraction.

The harness is the entry point for agent graph execution. It wires:
- Checkpointer (PostgreSQL, asyncpg — DL-0017)
- Inference client (provider-agnostic — ADR-0009)
- Tool registry (MCP servers in-process — ADR-0022)
- Health endpoint (FastAPI)
- Metrics (Prometheus — ADR-0010)
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from maezo.runtime.checkpoint import build_checkpointer
from maezo.runtime.metrics import RuntimeMetrics

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from maezo.runtime.inference import InferenceClient

logger = logging.getLogger(__name__)


@dataclass
class HarnessConfig:
    """Configuration for the MAEZO agent runtime harness."""

    db_url: str = "postgresql://maezo:maezo@localhost:5433/maezo"
    health_port: int = 8000
    search_path: str = "agents, public"


class MaezoHarness:
    """LangGraph runtime harness — health check, checkpointer, inference.

    The harness is the main entry point for the agent runtime (W-R0/R1).
    It provides a FastAPI health endpoint and manages the LangGraph checkpointer
    and inference client lifecycle.

    Lifecycle:
        harness = MaezoHarness(config)
        await harness.start()   # connect checkpointer, init inference
        # ... run agent graphs ...
        await harness.stop()    # graceful shutdown
    """

    def __init__(self, config: HarnessConfig | None = None) -> None:
        self._config = config or HarnessConfig()
        self._checkpointer: AsyncPostgresSaver | None = None
        self._inference: InferenceClient | None = None
        self._metrics = RuntimeMetrics()
        self._exit_stack = AsyncExitStack()
        self._ready = False
        self._app = self._build_app()

    def _build_app(self) -> FastAPI:
        """Build the FastAPI application with health check endpoint."""
        app = FastAPI(title="MAEZO Agent Runtime", version="0.1.0")

        @app.get("/health")
        async def health() -> JSONResponse:
            """Health check — returns 200 when checkpointer and inference are ready."""
            return JSONResponse(
                content={
                    "status": "ok" if self._ready else "degraded",
                    "checkpointer": "connected" if self._checkpointer is not None else "disconnected",
                    "inference": "ready" if self._inference is not None else "not_configured",
                },
                status_code=200 if self._ready else 503,
            )

        @app.get("/metrics")
        async def metrics() -> JSONResponse:
            """Prometheus metrics endpoint."""

            return JSONResponse(
                content={"status": "ok", "metrics_available": True},
                status_code=200,
            )

        return app

    @property
    def app(self) -> FastAPI:
        """The FastAPI application."""
        return self._app

    @property
    def metrics(self) -> RuntimeMetrics:
        return self._metrics

    @property
    def checkpointer(self) -> AsyncPostgresSaver | None:
        """The LangGraph checkpointer, available after start()."""
        return self._checkpointer

    @property
    def inference(self) -> InferenceClient | None:
        """The inference client, available after set_inference()."""
        return self._inference

    @property
    def is_ready(self) -> bool:
        """Whether the harness is fully initialized and ready."""
        return self._ready

    def set_inference(self, client: InferenceClient) -> None:
        """Register an inference client with the harness.

        Args:
            client: The InferenceClient to use for agent inference.

        After this call, health check reports inference as 'ready'.
        """
        self._inference = client
        logger.info("inference client registered", extra={"default_model": client.default_model.model_id})

    async def start(self) -> None:
        """Start the harness — connect checkpointer to PostgreSQL.

        Raises:
            ConnectionError: If database connection fails.
        """
        logger.info("harness starting", extra={"db_url": self._config.db_url[:50] + "..."})

        try:
            # build_checkpointer is an async context manager.
            # We enter it via the exit stack so it gets cleaned up on stop().
            checkpointer_ctx = build_checkpointer(self._config.db_url)
            self._checkpointer = await self._exit_stack.enter_async_context(checkpointer_ctx)
            self._ready = True
            self._metrics.health.set(1)
            logger.info("harness ready — checkpointer connected")
        except Exception as exc:
            self._metrics.health.set(0)
            logger.error("harness start failed", exc_info=exc)
            raise ConnectionError(f"Failed to connect checkpointer: {exc}") from exc

    async def stop(self) -> None:
        """Graceful shutdown — close checkpointer connection and cleanup."""
        logger.info("harness stopping")
        self._ready = False
        self._metrics.health.set(0)

        try:
            await self._exit_stack.aclose()
        except Exception as exc:
            logger.warning("error during harness shutdown", exc_info=exc)

        self._checkpointer = None
        logger.info("harness stopped")
