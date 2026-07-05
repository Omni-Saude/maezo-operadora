"""Inference — LLM provider abstraction.

ADR-0009: Portfolio de modelos com abstração de provider.
No agent ever imports an SDK directly — all inference goes through this module.

Supports:
- Task-based routing (classification → cheap, reasoning → frontier, batch → tier).
- Provider-agnostic interface (swapping models is a config change, not a code change).
- Capability detection (caching, native tool-use, structured output).
- Noop provider for testing without real API keys.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class InferenceTier(StrEnum):
    """Routing tiers for model selection (ADR-0009 §2)."""

    CHEAP = "cheap"  # Classification, routing, small tasks
    FRONTIER = "frontier"  # Critical reasoning, complex decisions
    BATCH = "batch"  # High-volume, latency-tolerant


class ModelCapability(StrEnum):
    """Optional capabilities that providers may or may not support (ADR-0009 §5)."""

    CACHING = "caching"
    TOOL_USE = "tool_use"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"
    STREAMING = "streaming"


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a specific model in the portfolio.

    Per-tenant+per-agent override via Agent Definition (ADR-0009 §3).
    """

    model_id: str
    provider: str
    tier: InferenceTier = InferenceTier.CHEAP
    capabilities: frozenset[ModelCapability] = field(default_factory=frozenset)
    max_tokens: int = 4096
    temperature: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceRequest:
    """Request to the inference abstraction — provider-agnostic."""

    messages: list[dict[str, Any]]
    model: ModelConfig
    tools: list[dict[str, Any]] | None = None
    response_format: dict[str, Any] | None = None
    stop: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceResponse:
    """Response from the inference abstraction — normalized across providers."""

    content: str | None
    tool_calls: list[dict[str, Any]] | None
    model: str
    usage: dict[str, int] | None
    finish_reason: str | None


# ============================================================================
# Provider interface
# ============================================================================


class InferenceProvider(ABC):
    """Abstract base for LLM providers — no SDK ever leaks to agents."""

    @abstractmethod
    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        """Execute inference against the provider's backend."""
        ...


class NoopProvider(InferenceProvider):
    """No-op provider for testing — returns echo responses without API keys.

    Used when no real LLM provider is configured. Agents can exercise
    the full inference pipeline without hitting external APIs.
    """

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        """Echo the last user message as a noop response."""
        last_msg = ""
        for msg in reversed(request.messages):
            if msg.get("role") == "user":
                last_msg = str(msg.get("content", ""))
                break

        return InferenceResponse(
            content=f"[NOOP] Echo: {last_msg[:100]}",
            tool_calls=None,
            model=request.model.model_id,
            usage={"input_tokens": 0, "output_tokens": 0},
            finish_reason="stop",
        )


# ============================================================================
# Inference client
# ============================================================================


class InferenceClient:
    """Provider-agnostic inference client (ADR-0009).

    Usage:
        noop = NoopProvider()
        model = ModelConfig(model_id="noop", provider="noop")
        client = InferenceClient(default_model=model, providers={"noop": noop})
        response = await client.infer(request)
    """

    def __init__(
        self,
        default_model: ModelConfig,
        *,
        providers: dict[str, InferenceProvider] | None = None,
    ) -> None:
        self._default_model = default_model
        self._providers: dict[str, InferenceProvider] = providers or {}

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        """Execute inference, routing based on model provider.

        Args:
            request: The inference request with messages and model config.

        Returns:
            Normalized InferenceResponse across all providers.

        Raises:
            ValueError: If the requested model's provider is not registered.
        """
        model = request.model or self._default_model
        provider = self._providers.get(model.provider)

        if provider is None:
            raise ValueError(
                f"No provider registered for '{model.provider}'. "
                f"Registered providers: {list(self._providers.keys())}"
            )

        start = time.monotonic()
        response = await provider.infer(request)
        elapsed = time.monotonic() - start

        logger.debug(
            "inference completed",
            extra={
                "model": model.model_id,
                "provider": model.provider,
                "tier": model.tier.value,
                "latency_s": round(elapsed, 3),
                "finish_reason": response.finish_reason,
            },
        )

        return response

    def register_provider(self, name: str, provider: InferenceProvider) -> None:
        """Register a new provider.

        Args:
            name: Provider identifier (e.g., "openai", "anthropic").
            provider: Provider implementation.
        """
        self._providers[name] = provider

    @property
    def default_model(self) -> ModelConfig:
        return self._default_model

    @property
    def providers(self) -> dict[str, InferenceProvider]:
        return dict(self._providers)
