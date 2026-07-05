"""Unit tests for maezo.runtime.harness."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from maezo.runtime.harness import Harness


def test_harness_create_graph() -> None:
    """Harness should create a LangGraph StateGraph."""
    harness = Harness()

    graph = harness.create_graph()

    assert graph is not None


@pytest.mark.asyncio
async def test_harness_invoke() -> None:
    """Harness.invoke should execute the graph with the given state and return a result."""
    harness = Harness()
    harness.create_graph()

    result = await harness.invoke({"messages": ["hello"]})

    assert isinstance(result, dict)
    assert "messages" in result


@pytest.mark.asyncio
async def test_harness_invoke_with_inference() -> None:
    """Harness.invoke should call inference provider when configured."""
    mock_inference = MagicMock()
    mock_inference.generate = AsyncMock(return_value="mocked response")

    harness = Harness(inference=mock_inference)
    harness.create_graph()

    result = await harness.invoke({"messages": ["hello"]})

    assert isinstance(result, dict)
    mock_inference.generate.assert_awaited_once_with(prompt="hello")
