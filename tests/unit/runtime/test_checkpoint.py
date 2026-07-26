"""Unit tests for maezo.runtime.checkpoint (DL-0017; T3.4/F4 prod wiring)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from maezo.runtime.checkpoint import (
    Checkpointer,
    assert_phi_safe_thread_id,
    checkpoint_thread_config,
)


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


def test_saver_property_exposes_underlying_saver() -> None:
    """`.saver` returns the wrapped saver (what `StateGraph.compile(checkpointer=...)` needs)."""
    mock_saver = MagicMock()
    assert Checkpointer(saver=mock_saver).saver is mock_saver
    assert Checkpointer(conn_string="postgresql://x/y").saver is None


# --- PHI-safe thread-id discipline (T3.4/F4) --------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+5511999998888",  # E.164 phone
        "5511999998888",  # bare phone
        "12345678901",  # bare CPF (11 digits)
        "  0000000000  ",  # padded, still all-digits
    ],
)
def test_assert_phi_safe_thread_id_rejects_raw_numeric(raw: str) -> None:
    """A raw phone/CPF-like id is refused — checkpoint tables are PHI-bearing, keyed by thread_id."""
    with pytest.raises(ValueError, match="raw phone/CPF-like"):
        assert_phi_safe_thread_id(raw)


def test_assert_phi_safe_thread_id_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        assert_phi_safe_thread_id("   ")


@pytest.mark.parametrize(
    "safe",
    [
        # hashed conversation id (dispatch.py convention) — hex hash, not all-digits.
        "wa:amh:deadbeefcafe0123456789abcdef0123456789abcdef0123456789abcdef01",
        # process business key (helena/graph.py convention).
        "ESC-amh-wa:amh:feedfacecafe00112233445566778899aabbccddeeff",
        "amh:proc-instance-42",
    ],
)
def test_assert_phi_safe_thread_id_accepts_conventional_ids(safe: str) -> None:
    """The platform's hashed conversation ids / process business keys pass unchanged."""
    assert assert_phi_safe_thread_id(safe) == safe.strip()


def test_checkpoint_thread_config_shape() -> None:
    """`checkpoint_thread_config` builds a langgraph RunnableConfig with the validated thread id."""
    tid = "wa:amh:deadbeefcafe0123456789abcdef0123456789abcdef0123456789abcdef01"
    cfg = checkpoint_thread_config(tid, checkpoint_ns="sub")
    assert cfg["configurable"]["thread_id"] == tid
    assert cfg["configurable"]["checkpoint_ns"] == "sub"


def test_checkpoint_thread_config_rejects_raw_phi() -> None:
    """The builder fail-closes on a raw-numeric id, before any config is produced."""
    with pytest.raises(ValueError, match="raw phone/CPF-like"):
        checkpoint_thread_config("+5511999998888")
