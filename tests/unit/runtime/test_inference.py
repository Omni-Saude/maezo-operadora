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


def test_inference_noop_warns() -> None:
    """Health check must return a warning when the provider is 'noop'."""
    provider = InferenceProvider()

    result = provider.health_check()

    assert isinstance(result, dict)
    assert result["status"] == "warning"
    assert "noop" in result["message"].lower()


def test_inference_configured_provider_health_check_ok() -> None:
    """Health check must return ok when provider is not 'noop'."""
    from maezo.runtime.inference import InferenceSettings

    settings = InferenceSettings(provider="openai", api_key="sk-test")
    provider = InferenceProvider(settings=settings)

    result = provider.health_check()

    assert result["status"] == "ok"
    assert "openai" in result["message"]
