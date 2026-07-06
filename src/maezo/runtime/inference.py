"""LLM provider abstraction (ADR-0009).

This is the SINGLE import point for any LLM SDK in the codebase.
All other modules MUST go through this interface — never import an LLM SDK directly.

Uses pydantic-settings for configuration. Defaults to "noop" provider
when no API key is configured, returning deterministic mock responses.
"""

from __future__ import annotations

import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)


class InferenceSettings(BaseSettings):
    """Configuration for the inference provider.

    Environment variables prefixed with MAEZO_INFERENCE_ (default).
    """

    model_config = {"env_prefix": "MAEZO_INFERENCE_", "extra": "ignore"}

    provider: str = "noop"
    api_key: str = ""
    model: str = ""


class InferenceProvider:
    """Abstract interface for LLM inference providers.

    Per ADR-0009, all LLM access MUST flow through this class.
    No other module imports any LLM SDK directly.

    The default "noop" provider returns deterministic mock responses
    and requires no API key — suitable for testing and development.
    """

    def __init__(self, settings: InferenceSettings | None = None) -> None:
        """Initialize the inference provider.

        Args:
            settings: Optional InferenceSettings; if None, uses defaults
                       (noop provider, no API key).
        """
        self._settings = settings or InferenceSettings()
        logger.info(
            "inference_provider_initialized",
            provider=self._settings.provider,
            model=self._settings.model or "default",
        )
        if self._settings.provider == "noop":
            logger.warning(
                "inference_provider_is_noop",
                message=(
                    "LLM provider is 'noop' — all inference calls will return "
                    "deterministic mock responses. Set MAEZO_INFERENCE_PROVIDER "
                    "to a real provider (e.g. 'openai') for production."
                ),
            )

    @property
    def provider_name(self) -> str:
        """Return the active provider name (e.g. 'noop', 'openai')."""
        return self._settings.provider

    def health_check(self) -> dict[str, str]:
        """Return provider health status.

        Returns a warning if the provider is 'noop' (mock mode),
        so operators and monitoring systems can detect misconfiguration
        in production environments.

        Returns:
            A dict with 'status' ('ok' or 'warning') and 'message'.
        """
        if self._settings.provider == "noop":
            return {
                "status": "warning",
                "message": (
                    "Provider is 'noop' — all LLM calls return mock responses. "
                    "Set MAEZO_INFERENCE_PROVIDER to a real provider for production."
                ),
            }
        return {"status": "ok", "message": f"Provider '{self._settings.provider}' is configured."}

    async def generate(self, prompt: str) -> str:
        """Generate a response for the given prompt.

        Args:
            prompt: The input text prompt.

        Returns:
            The generated response string.

        In noop mode, returns a deterministic mock response.
        """
        if self._settings.provider == "noop":
            logger.info("inference_noop_generate", prompt_len=len(prompt))
            return f"[noop mock response] Received prompt ({len(prompt)} chars): {prompt[:80]}..."

        raise NotImplementedError(
            f"Provider '{self._settings.provider}' not implemented. "
            f"Set MAEZO_INFERENCE_PROVIDER=noop for mock mode."
        )
