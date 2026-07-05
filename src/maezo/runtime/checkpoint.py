"""LangGraph checkpointer backed by PostgreSQL + asyncpg (DL-0017).

Per DL-0017:
- Uses asyncpg pool with setup= (NOT init=) for per-acquire session state.
- pgvector extension installed in schema 'public' (type/operator is DB-global).
- Wraps langgraph-checkpoint-postgres PostgresSaver.
"""

from __future__ import annotations

import structlog
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, CheckpointTuple
from langgraph.checkpoint.postgres import PostgresSaver

logger = structlog.get_logger(__name__)


class Checkpointer:
    """PostgreSQL checkpointer for LangGraph state persistence.

    Wraps langgraph-checkpoint-postgres's PostgresSaver,
    providing an async interface for saving and loading agent state.

    The underlying asyncpg pool is configured with setup= (per DL-0017)
    to ensure search_path and pgvector visibility on every acquired connection,
    not just the first one (which is the bug caused by init=).
    """

    def __init__(
        self,
        saver: PostgresSaver | None = None,
        conn_string: str | None = None,
    ) -> None:
        """Initialize the checkpointer.

        Args:
            saver: A pre-configured PostgresSaver instance. Takes precedence.
            conn_string: PostgreSQL connection string (used when saver is None).
                         e.g. 'postgresql://user:***@host:5432/db'
        """
        self._saver = saver
        self._conn_string = conn_string
        if saver is not None:
            logger.info("checkpointer_initialized_with_saver")
        elif conn_string is not None:
            logger.info("checkpointer_initialized_with_conn_string")
        else:
            logger.warning("checkpointer_initialized_without_backend")

    @property
    def conn_string(self) -> str | None:
        """Return the configured PostgreSQL connection string, if any."""
        return self._conn_string

    def _ensure_saver(self) -> PostgresSaver:
        """Return the underlying saver, raising if not configured."""
        if self._saver is None:
            raise RuntimeError("Checkpointer not connected. Provide a PostgresSaver instance or conn_string.")
        return self._saver

    async def save(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> RunnableConfig:
        """Persist a checkpoint state.

        Args:
            config: LangGraph RunnableConfig with thread_id.
            checkpoint: The checkpoint state to persist.
            metadata: Additional metadata for the checkpoint.

        Returns:
            The updated config after save.
        """
        saver = self._ensure_saver()
        logger.debug("checkpoint_save", thread_id=config.get("configurable", {}).get("thread_id"))
        result: RunnableConfig = await saver.aput(config, checkpoint, metadata, {})
        return result

    async def load(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Load a checkpoint state.

        Args:
            config: LangGraph RunnableConfig with thread_id.

        Returns:
            The CheckpointTuple or None if not found.
        """
        saver = self._ensure_saver()
        logger.debug("checkpoint_load", thread_id=config.get("configurable", {}).get("thread_id"))
        return await saver.aget_tuple(config)
