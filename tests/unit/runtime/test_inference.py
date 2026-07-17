"""Unit tests for maezo.runtime.inference (ADR-0009, T1.7).

All SDK calls are mocked at the SDK boundary (``anthropic.AsyncAnthropic``
construction + ``messages.create``) — no network I/O. Live-API coverage
(when a real key is present) lives in ``test_inference_live.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pytest

from maezo.runtime.inference import (
    AnthropicInferenceProvider,
    InferenceConfigError,
    InferenceProvider,
    InferenceProviderError,
    InferenceSettings,
    NoopInferenceProvider,
    PhiZoneMockProvider,
    PhiZoneRoutingError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_httpx_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code, request=request, json={"error": {"message": "boom"}})


def _fake_message(text: str = "hello from claude", stop_reason: str = "end_turn") -> SimpleNamespace:
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason=stop_reason)


@pytest.fixture(autouse=True)
def _clean_anthropic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no ambient credentials leak into a test that doesn't want them."""
    monkeypatch.delenv("MAEZO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# noop provider — regression
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Provider registry / factory — fail-closed selection
# ---------------------------------------------------------------------------


def test_unknown_provider_raises_at_construction() -> None:
    """An unrecognized MAEZO_INFERENCE_PROVIDER value must raise at startup, never degrade."""
    settings = InferenceSettings(provider="openai")

    with pytest.raises(InferenceConfigError, match="Unknown MAEZO_INFERENCE_PROVIDER"):
        InferenceProvider(settings=settings)


def test_empty_provider_name_raises_at_construction() -> None:
    settings = InferenceSettings(provider="")

    with pytest.raises(InferenceConfigError):
        InferenceProvider(settings=settings)


# ---------------------------------------------------------------------------
# Anthropic provider — misconfiguration is fail-closed (no silent noop fallback)
# ---------------------------------------------------------------------------


def test_anthropic_provider_requires_api_key() -> None:
    """A misconfigured anthropic provider (no key) must FAIL, not silently degrade to noop."""
    settings = InferenceSettings(provider="anthropic")

    with pytest.raises(InferenceConfigError, match="API key"):
        InferenceProvider(settings=settings)


def test_anthropic_provider_accepts_generic_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-generic-test")

    provider = InferenceProvider(settings=InferenceSettings(provider="anthropic"))

    assert provider.provider_name == "anthropic"


def test_anthropic_provider_prefers_maezo_namespaced_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAEZO_ANTHROPIC_API_KEY must take precedence over ANTHROPIC_API_KEY."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-generic")
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-namespaced")

    captured: dict[str, object] = {}
    real_init = anthropic.AsyncAnthropic.__init__

    def _capture_init(self: anthropic.AsyncAnthropic, **kwargs: object) -> None:
        captured.update(kwargs)
        real_init(self, **kwargs)

    monkeypatch.setattr(anthropic.AsyncAnthropic, "__init__", _capture_init)

    InferenceProvider(settings=InferenceSettings(provider="anthropic"))

    assert captured["api_key"] == "sk-ant-namespaced"


def test_anthropic_provider_health_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")

    provider = InferenceProvider(settings=InferenceSettings(provider="anthropic"))
    result = provider.health_check()

    assert result["status"] == "ok"
    assert "anthropic" in result["message"].lower()


def test_inference_configured_provider_health_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health check must return ok for a properly-configured real provider."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-test")
    settings = InferenceSettings(provider="anthropic")
    provider = InferenceProvider(settings=settings)

    result = provider.health_check()

    assert result["status"] == "ok"


def test_anthropic_provider_model_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")

    impl = AnthropicInferenceProvider()

    assert impl._model  # a non-empty default model id is selected


def test_anthropic_provider_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")

    impl = AnthropicInferenceProvider(model="claude-haiku-4-5")

    assert impl._model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Anthropic provider — generate() happy / error paths (SDK-level mocks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_provider_generate_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider()
    impl._client.messages.create = AsyncMock(return_value=_fake_message("hi there"))

    result = await impl.generate("hello")

    assert result == "hi there"
    impl._client.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_anthropic_provider_generate_rate_limit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider()
    error = anthropic.RateLimitError("rate limited", response=_fake_httpx_response(429), body=None)
    impl._client.messages.create = AsyncMock(side_effect=error)

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("hello")

    assert excinfo.value.retryable is True
    assert excinfo.value.provider == "anthropic"


@pytest.mark.asyncio
async def test_anthropic_provider_generate_auth_error_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider()
    error = anthropic.AuthenticationError("bad key", response=_fake_httpx_response(401), body=None)
    impl._client.messages.create = AsyncMock(side_effect=error)

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("hello")

    assert excinfo.value.retryable is False


@pytest.mark.asyncio
async def test_anthropic_provider_generate_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider()
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APITimeoutError(request=request)
    impl._client.messages.create = AsyncMock(side_effect=error)

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("hello")

    assert excinfo.value.retryable is True
    assert "timed out" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_anthropic_provider_generate_connection_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider()
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    impl._client.messages.create = AsyncMock(side_effect=error)

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("hello")

    assert excinfo.value.retryable is True


@pytest.mark.asyncio
async def test_anthropic_provider_generate_server_error_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider()
    error = anthropic.InternalServerError("boom", response=_fake_httpx_response(500), body=None)
    impl._client.messages.create = AsyncMock(side_effect=error)

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("hello")

    assert excinfo.value.retryable is True


@pytest.mark.asyncio
async def test_anthropic_provider_generate_refusal_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider()
    impl._client.messages.create = AsyncMock(return_value=_fake_message("", stop_reason="refusal"))

    with pytest.raises(InferenceProviderError, match="refus"):
        await impl.generate("hello")


# ---------------------------------------------------------------------------
# PHI-zone mock provider — explicitly labeled, never fabricated as real
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phi_zone_mock_generate_labeled_synthetic() -> None:
    impl = PhiZoneMockProvider()

    response = await impl.generate("dados sensiveis do paciente")

    assert "SYNTHETIC" in response
    assert "phi_zone_mock" in response


def test_phi_zone_mock_health_check_loud() -> None:
    impl = PhiZoneMockProvider()

    result = impl.health_check()

    assert result["status"] == "warning"
    assert "MOCK" in result["message"]
    assert "PHI" in result["message"]


def test_phi_zone_mock_is_phi_capable() -> None:
    assert PhiZoneMockProvider.phi_capable is True
    assert NoopInferenceProvider.phi_capable is False
    assert AnthropicInferenceProvider.phi_capable is False


def test_inference_provider_warns_when_backing_provider_is_mock() -> None:
    """Constructing InferenceProvider with a mock-backed provider must log loudly (not silently ok)."""
    provider = InferenceProvider(settings=InferenceSettings(provider="phi_zone_mock"))

    result = provider.health_check()

    assert result["status"] == "warning"


# ---------------------------------------------------------------------------
# PHI routing — fail-closed (ADR-0006 / ADR-0017)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phi_routing_fails_closed_for_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    provider = InferenceProvider(settings=InferenceSettings(provider="anthropic"))

    with pytest.raises(PhiZoneRoutingError):
        await provider.generate("dados PHI", phi=True)


@pytest.mark.asyncio
async def test_phi_routing_fails_closed_for_noop() -> None:
    """Even the default noop provider must not silently absorb a PHI-tagged request."""
    provider = InferenceProvider()

    with pytest.raises(PhiZoneRoutingError):
        await provider.generate("dados PHI", phi=True)


@pytest.mark.asyncio
async def test_phi_routing_never_reaches_anthropic_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocked PHI request must never even attempt the general cloud provider call."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    provider = InferenceProvider(settings=InferenceSettings(provider="anthropic"))
    create_mock = AsyncMock(return_value=_fake_message("should never be called"))
    provider._impl._client.messages.create = create_mock  # type: ignore[attr-defined]

    with pytest.raises(PhiZoneRoutingError):
        await provider.generate("dados PHI", phi=True)

    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_phi_routing_succeeds_for_phi_zone_mock() -> None:
    provider = InferenceProvider(settings=InferenceSettings(provider="phi_zone_mock"))

    response = await provider.generate("dados PHI", phi=True)

    assert "SYNTHETIC" in response


@pytest.mark.asyncio
async def test_general_request_still_works_when_provider_is_phi_zone_mock() -> None:
    """phi=False traffic against phi_zone_mock is not blocked (only PHI misrouting is)."""
    provider = InferenceProvider(settings=InferenceSettings(provider="phi_zone_mock"))

    response = await provider.generate("pergunta geral", phi=False)

    assert isinstance(response, str)
    assert len(response) > 0


def test_inference_provider_error_message_includes_provider() -> None:
    err = InferenceProviderError("anthropic", "boom", retryable=True)

    assert "anthropic" in str(err)
    assert err.retryable is True


def test_phi_zone_routing_error_is_permission_error() -> None:
    """PhiZoneRoutingError must be a PermissionError subclass (fail-closed escalation pattern, ADR-0016)."""
    assert issubclass(PhiZoneRoutingError, PermissionError)


def test_inference_config_error_is_value_error() -> None:
    assert issubclass(InferenceConfigError, ValueError)
