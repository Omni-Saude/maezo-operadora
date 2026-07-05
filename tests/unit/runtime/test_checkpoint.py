"""Unit tests for maezo.runtime.checkpoint (DL-0017)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from maezo.runtime.checkpoint import Checkpointer


@pytest.mark.asyncio
async def test_checkpoint_save_calls_aput() -> None:
    """Checkpointer.save should delegate to the underlying PostgresSaver.aput."""
    mock_saver = MagicMock()
    mock_saver.aput = AsyncMock(return_value={"configurable": {"thread_id": "t1"}})

    checkpointer = Checkpointer(saver=mock_saver)
    config = {"configurable": {"thread_id": "t1"}}
    checkpoint_data = {"state": {"key": "value"}}
    metadata = {"source": "test"}

    result = await checkpointer.save(config, checkpoint_data, metadata)

    mock_saver.aput.assert_awaited_once()
    assert result == {"configurable": {"thread_id": "t1"}}


@pytest.mark.asyncio
async def test_checkpoint_load_calls_aget_tuple() -> None:
    """Checkpointer.load should delegate to the underlying PostgresSaver.aget_tuple."""
    mock_saver = MagicMock()
    expected = MagicMock()
    mock_saver.aget_tuple = AsyncMock(return_value=expected)

    checkpointer = Checkpointer(saver=mock_saver)
    config = {"configurable": {"thread_id": "t1"}}

    result = await checkpointer.load(config)

    mock_saver.aget_tuple.assert_awaited_once_with(config)
    assert result is expected


def test_checkpointer_accepts_conn_string() -> None:
    """Checkpointer should accept a conn_string for pool creation (DL-0017 setup=)."""
    checkpointer = Checkpointer(conn_string="postgresql://test:test@localhost/test")

    assert checkpointer.conn_string == "postgresql://test:test@localhost/test"
