"""Unit tests for maezo.runtime.inference — provider-agnostic LLM abstraction.

ADR-0009: Model portfolio with provider abstraction — no agent imports SDK directly.

GREEN phase: InferenceClient with NoopProvider for testing.
"""

from __future__ import annotations

import pytest

from maezo.runtime.inference import (
    InferenceClient,
    InferenceRequest,
    InferenceResponse,
    InferenceTier,
    ModelCapability,
    ModelConfig,
    NoopProvider,
)


class TestModelConfig:
    """ModelConfig — frozen dataclass representing a model in the portfolio."""

    def test_default_tier_is_cheap(self) -> None:
        config = ModelConfig(model_id="gpt-4o-mini", provider="openai")
        assert config.tier == InferenceTier.CHEAP

    def test_default_capabilities_empty(self) -> None:
        config = ModelConfig(model_id="gpt-4o-mini", provider="openai")
        assert config.capabilities == frozenset()

    def test_explicit_capabilities(self) -> None:
        config = ModelConfig(
            model_id="claude-sonnet-4",
            provider="anthropic",
            tier=InferenceTier.FRONTIER,
            capabilities=frozenset([ModelCapability.TOOL_USE, ModelCapability.STREAMING]),
        )
        assert ModelCapability.TOOL_USE in config.capabilities
        assert ModelCapability.STREAMING in config.capabilities
        assert ModelCapability.VISION not in config.capabilities

    def test_frozen_prevents_mutation(self) -> None:
        config = ModelConfig(model_id="gpt-4o-mini", provider="openai")
        with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError from dataclasses
            config.temperature = 1.0  # type: ignore[misc]

    def test_extra_field_for_provider_specific_config(self) -> None:
        config = ModelConfig(
            model_id="claude-sonnet-4",
            provider="anthropic",
            extra={"max_thinking_tokens": 16000},
        )
        assert config.extra["max_thinking_tokens"] == 16000


class TestInferenceRequest:
    """InferenceRequest — provider-agnostic request envelope."""

    def test_minimal_request(self) -> None:
        model = ModelConfig(model_id="gpt-4o-mini", provider="openai")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "Hello"}],
            model=model,
        )
        assert len(req.messages) == 1
        assert req.tools is None
        assert req.response_format is None

    def test_request_with_tools(self) -> None:
        model = ModelConfig(model_id="gpt-4o-mini", provider="openai")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "What is 2+2?"}],
            model=model,
            tools=[
                {
                    "type": "function",
                    "function": {"name": "calculator", "parameters": {}},
                }
            ],
        )
        assert req.tools is not None
        assert len(req.tools) == 1

    def test_request_with_response_format(self) -> None:
        model = ModelConfig(model_id="gpt-4o-mini", provider="openai")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "List colors"}],
            model=model,
            response_format={"type": "json_object"},
        )
        assert req.response_format == {"type": "json_object"}


class TestInferenceTier:
    """InferenceTier — routing tiers for model selection."""

    def test_tier_values(self) -> None:
        assert InferenceTier.CHEAP.value == "cheap"
        assert InferenceTier.FRONTIER.value == "frontier"
        assert InferenceTier.BATCH.value == "batch"


class TestNoopProvider:
    """NoopProvider — echo responses for testing without API keys."""

    @pytest.mark.asyncio
    async def test_echo_response(self) -> None:
        provider = NoopProvider()
        model = ModelConfig(model_id="noop", provider="noop")
        req = InferenceRequest(
            messages=[{"role": "user", "content": "Hello, world!"}],
            model=model,
        )
        resp = await provider.infer(req)
        assert resp.content is not None
        assert "Hello, world!" in resp.content
        assert resp.model == "noop"
        assert resp.finish_reason == "stop"
        assert resp.usage is not None
        assert resp.usage["input_tokens"] == 0
        assert resp.usage["output_tokens"] == 0

    @pytest.mark.asyncio
    async def test_echo_last_user_message(self) -> None:
        provider = NoopProvider()
        model = ModelConfig(model_id="noop", provider="noop")
        req = InferenceRequest(
            messages=[
                {"role": "system", "content": "You are a bot."},
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "I'll calculate that."},
                {"role": "user", "content": "Actually, never mind."},
            ],
            model=model,
        )
        resp = await provider.infer(req)
        assert "never mind" in resp.content.lower()


class TestInferenceClient:
    """InferenceClient — the provider-agnostic inference interface."""

    def test_client_holds_default_model(self) -> None:
        model = ModelConfig(model_id="gpt-4o-mini", provider="openai")
        client = InferenceClient(default_model=model)
        assert client.default_model == model

    def test_register_provider(self) -> None:
        model = ModelConfig(model_id="noop", provider="noop")
        client = InferenceClient(default_model=model)
        noop = NoopProvider()
        client.register_provider("noop", noop)
        assert "noop" in client.providers
        assert client.providers["noop"] is noop

    @pytest.mark.asyncio
    async def test_infer_with_noop_provider(self) -> None:
        """GREEN: InferenceClient.infer() works with NoopProvider."""
        model = ModelConfig(model_id="noop", provider="noop")
        noop = NoopProvider()
        client = InferenceClient(default_model=model, providers={"noop": noop})

        req = InferenceRequest(
            messages=[{"role": "user", "content": "Test inference"}],
            model=model,
        )
        resp = await client.infer(req)
        assert isinstance(resp, InferenceResponse)
        assert resp.model == "noop"
        assert resp.content is not None
        assert "Test inference" in resp.content

    @pytest.mark.asyncio
    async def test_infer_raises_for_unregistered_provider(self) -> None:
        """GREEN: infer raises ValueError when provider is not registered."""
        model = ModelConfig(model_id="unknown-model", provider="nonexistent")
        client = InferenceClient(default_model=model)

        req = InferenceRequest(
            messages=[{"role": "user", "content": "Hello"}],
            model=model,
        )
        with pytest.raises(ValueError, match="No provider registered"):
            await client.infer(req)

    def test_providers_property_returns_copy(self) -> None:
        model = ModelConfig(model_id="noop", provider="noop")
        client = InferenceClient(default_model=model, providers={"noop": NoopProvider()})
        providers = client.providers
        providers["new"] = NoopProvider()  # mutate copy
        assert "new" not in client.providers  # original untouched
