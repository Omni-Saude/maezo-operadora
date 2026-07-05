"""Unit tests for maezo.runtime.inference (ADR-0009)."""

import pytest

from maezo.runtime.inference import InferenceProvider


def test_inference_provider_default_is_noop() -> None:
    """Default provider (no API key) should be 'noop' and not require external config."""
    provider = InferenceProvider()

    assert provider.provider_name == "noop"


@pytest.mark.asyncio
async def test_inference_provider_mock_response() -> None:
    """Noop provider should return a deterministic mock response without calling any API."""
    provider = InferenceProvider()

    response = await provider.generate(prompt="Olá, como vai?")

    assert isinstance(response, str)
    assert len(response) > 0
    assert "noop" in response.lower() or "mock" in response.lower()
