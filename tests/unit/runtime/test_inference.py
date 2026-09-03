"""Unit tests for maezo.runtime.inference (ADR-0009, T1.7).

All SDK calls are mocked at the SDK boundary (``anthropic.AsyncAnthropic``
construction + ``messages.create``) — no network I/O. Live-API coverage
(when a real key is present) lives in ``test_inference_live.py``.

D2-02 SPLIT NOTE (docs/reports/inference-split-plan.md §5 step 6): the metering tests below
patch ``"maezo.runtime._inference_split.providers.logger"``, not
``"maezo.runtime.inference.logger"``. A ``structlog`` call inside a function resolves the free
variable ``logger`` from that FUNCTION's own defining module's globals (Python's normal LEGB
lookup, unaffected by where the function is later imported/re-exported from) — since
``AnthropicInferenceProvider``/``_emit_llm_token_usage`` physically moved to ``providers.py`` in
step 6, patching ``inference.py``'s own (now separate) ``logger`` object no longer intercepts
anything, and a mock-logger assertion would either fail loudly (as it did, reproduced live this
session before this fix) or — worse — silently stop proving what it claims for a test that only
asserts an event's ABSENCE. This target moves again to ``maezo.runtime.inference.providers.logger``
at step 8, when ``_inference_split`` is promoted to the real package.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


def _fake_usage(input_tokens: object = 100, output_tokens: object = 40) -> SimpleNamespace:
    """A stand-in for the raw Anthropic SDK's ``Message.usage`` (T8).

    Accepts non-int values too (``object`` typing) so tests can exercise the
    "present but malformed" degrade-gracefully path (e.g. ``output_tokens=None``).
    """
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


def _fake_message(
    text: str = "hello from claude",
    stop_reason: str = "end_turn",
    *,
    usage: SimpleNamespace | None = None,
    model: str | None = None,
) -> SimpleNamespace:
    """A stand-in for the raw Anthropic SDK's ``Message`` response.

    ``usage``/``model`` default to unset (attribute absent entirely, not merely
    ``None``) — every pre-existing caller of this helper (the ~20 tests below that
    predate T8) exercises the "usage absent" metering path unintentionally, which is
    exactly the case `_emit_llm_token_usage` must degrade gracefully on.
    """
    block = SimpleNamespace(type="text", text=text)
    message = SimpleNamespace(content=[block], stop_reason=stop_reason)
    if usage is not None:
        message.usage = usage
    if model is not None:
        message.model = model
    return message


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


# ---------------------------------------------------------------------------
# Token-usage metering (T8) — capture at the single seam, PHI-safe, fail-safe
# ---------------------------------------------------------------------------


def _llm_token_samples(collector: object) -> list[object]:
    """Collect every Prometheus sample for the T8 `maezo_llm_tokens_total` counter."""
    return [
        sample
        for metric in collector.registry.collect()  # type: ignore[attr-defined]
        for sample in metric.samples
        if sample.name == "maezo_llm_tokens_total"
    ]


@pytest.mark.asyncio
async def test_generate_with_usage_emits_correct_prometheus_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock response WITH usage -> the metering emission carries the right counts.

    Proven against the platform's EXISTING Prometheus telemetry mechanism
    (`maezo.runtime.metrics.MetricsCollector` / `maezo.platform.observability`), the
    same one `tests/unit/platform/test_observability.py` already exercises for worker
    metrics — not a parallel test-only mechanism.
    """
    from maezo.runtime.metrics import MetricsCollector

    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message("hi there", usage=_fake_usage(input_tokens=123, output_tokens=45))
    )

    collector = MetricsCollector()
    with patch("maezo.platform.observability._get_metrics_collector", return_value=collector):
        result = await impl.generate("hello")

    assert result == "hi there"
    samples = _llm_token_samples(collector)
    by_type = {s.labels["token_type"]: s for s in samples}  # type: ignore[attr-defined]
    assert by_type["input"].value == 123  # type: ignore[attr-defined]
    assert by_type["output"].value == 45  # type: ignore[attr-defined]
    assert by_type["input"].labels["provider"] == "anthropic"  # type: ignore[attr-defined]
    assert by_type["input"].labels["model"] == "claude-opus-4-8"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_generate_with_usage_emits_structured_log_with_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The structlog `llm_token_usage` event carries model id, input/output, and total."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message(usage=_fake_usage(input_tokens=10, output_tokens=7))
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime._inference_split.providers.logger", mock_logger)

    await impl.generate("hello")

    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert len(usage_calls) == 1
    kwargs = usage_calls[0].kwargs
    assert kwargs["provider"] == "anthropic"
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["input_tokens"] == 10
    assert kwargs["output_tokens"] == 7
    assert kwargs["total_tokens"] == 17


@pytest.mark.asyncio
async def test_generate_prefers_response_model_over_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the API response carries its own `.model`, the emission uses THAT, not the
    provider's configured model — e.g. a fallback-served request answered by a
    different model than requested."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message(
            usage=_fake_usage(input_tokens=5, output_tokens=5),
            model="claude-haiku-4-5",
        )
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime._inference_split.providers.logger", mock_logger)

    await impl.generate("hello")

    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert usage_calls[0].kwargs["model"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_generate_without_usage_does_not_crash_and_emits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock response WITHOUT usage -> no crash, and NO bogus metering emission.

    `_fake_message()` with no `usage=` kwarg leaves the `.usage` attribute entirely
    absent (mirrors "some responses lack it" — the exact case the MUST list calls out).
    """
    from maezo.runtime.metrics import MetricsCollector

    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(return_value=_fake_message("hi there"))  # type: ignore[method-assign]
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime._inference_split.providers.logger", mock_logger)

    collector = MetricsCollector()
    with patch("maezo.platform.observability._get_metrics_collector", return_value=collector):
        result = await impl.generate("hello")

    assert result == "hi there"  # the actual LLM call was NOT broken or stalled
    assert _llm_token_samples(collector) == []  # no bogus emission
    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert usage_calls == []


@pytest.mark.asyncio
async def test_generate_with_partial_usage_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`usage` present but missing a required field (e.g. output_tokens=None) ->
    skip the emission, never raise, never emit a bogus/partial count."""
    from maezo.runtime.metrics import MetricsCollector

    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message(usage=_fake_usage(input_tokens=10, output_tokens=None))
    )

    collector = MetricsCollector()
    with patch("maezo.platform.observability._get_metrics_collector", return_value=collector):
        result = await impl.generate("hello")

    assert result == "hello from claude"
    assert _llm_token_samples(collector) == []


@pytest.mark.asyncio
async def test_generate_metering_failure_never_breaks_the_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the metering recorder itself raises, `generate()` must still succeed.

    Directly proves the hard fail-safe constraint: "metering must NEVER break or
    stall an LLM call" — simulated here as a defect in the Prometheus recorder
    (`record_llm_token_usage`), the last thing `_emit_llm_token_usage` calls.
    """
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message("hi there", usage=_fake_usage())
    )
    monkeypatch.setattr(
        "maezo.platform.observability.record_llm_token_usage",
        MagicMock(side_effect=RuntimeError("simulated metrics backend outage")),
    )

    result = await impl.generate("hello")  # must NOT raise

    assert result == "hi there"


@pytest.mark.asyncio
async def test_generate_still_meters_on_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal response still carries genuine `usage` -> still metered, even though
    `generate()` goes on to raise `InferenceProviderError` for the refusal itself."""
    from maezo.runtime.metrics import MetricsCollector

    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message(
            "", stop_reason="refusal", usage=_fake_usage(input_tokens=8, output_tokens=0)
        )
    )

    collector = MetricsCollector()
    with (
        patch("maezo.platform.observability._get_metrics_collector", return_value=collector),
        pytest.raises(InferenceProviderError, match="refus"),
    ):
        await impl.generate("hello")

    by_type = {s.labels["token_type"]: s for s in _llm_token_samples(collector)}  # type: ignore[attr-defined]
    assert by_type["input"].value == 8  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_generate_passes_through_correlation_ids_when_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional agent_id/tenant_id, when a caller supplies them, reach the structured
    log event (T8: "whatever correlation id is already available at that seam")."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message(usage=_fake_usage())
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime._inference_split.providers.logger", mock_logger)

    await impl.generate("hello", agent_id="helena", tenant_id="tenant-amh")

    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert usage_calls[0].kwargs["agent_id"] == "helena"
    assert usage_calls[0].kwargs["tenant_id"] == "tenant-amh"


# The exact, PHI-safe field set the `llm_token_usage` structlog event may carry (T8
# module docstring: "model id + token COUNTS only, never prompt/completion content").
# Keep this in sync with the `logger.info("llm_token_usage", ...)` call in
# `maezo.runtime.inference._emit_llm_token_usage` — any drift is exactly what
# `test_llm_token_usage_event_field_set_is_pinned` below exists to catch.
_LLM_TOKEN_USAGE_ALLOWED_FIELDS = frozenset(
    {"provider", "model", "input_tokens", "output_tokens", "total_tokens", "agent_id", "tenant_id"}
)


@pytest.mark.asyncio
async def test_llm_token_usage_event_field_set_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the exact field set of the `llm_token_usage` event — PHI-safety, not vibes.

    This is a positive allow-list check, not a "no content-shaped key" heuristic: it
    fails on ANY field-set drift, added or removed, forcing a reviewer to look at
    `_LLM_TOKEN_USAGE_ALLOWED_FIELDS` (and this docstring) before widening what the
    event is allowed to carry. A field like `content`/`prompt`/`response_text` would
    fail this test the moment it's added — mutation-verified: temporarily adding
    `content="x"` to the `logger.info(...)` call in `_emit_llm_token_usage` flips this
    RED (extra key not in the allow-list); reverting flips it back GREEN.
    """
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message(usage=_fake_usage(input_tokens=10, output_tokens=7))
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime._inference_split.providers.logger", mock_logger)

    await impl.generate("hello", agent_id="helena", tenant_id="tenant-amh")

    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert len(usage_calls) == 1
    actual_fields = frozenset(usage_calls[0].kwargs)
    assert actual_fields == _LLM_TOKEN_USAGE_ALLOWED_FIELDS, (
        f"llm_token_usage event field set changed: {sorted(actual_fields)}. If this is an "
        "intentional, reviewed addition, confirm the new field is a count/id — NEVER "
        "prompt/completion content — before updating _LLM_TOKEN_USAGE_ALLOWED_FIELDS."
    )


@pytest.mark.asyncio
async def test_noop_and_phi_zone_mock_providers_emit_no_metering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock providers' usage shape (T8, constraint 3): NO metering emission at all.

    `NoopInferenceProvider` and `PhiZoneMockProvider` make no real API call, so there
    is no real `usage` to report — the module docstring is explicit that they "never
    emit metering". Proven against both channels: no `llm_token_usage` structlog event
    and no `maezo_llm_tokens_total` Prometheus sample, for either mock provider.
    """
    from maezo.runtime.metrics import MetricsCollector

    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime._inference_split.providers.logger", mock_logger)
    collector = MetricsCollector()

    with patch("maezo.platform.observability._get_metrics_collector", return_value=collector):
        await NoopInferenceProvider().generate("x", agent_id="a", tenant_id="t")
        await PhiZoneMockProvider().generate("x", agent_id="a", tenant_id="t")

    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert usage_calls == []
    assert _llm_token_samples(collector) == []


@pytest.mark.asyncio
async def test_generate_correlation_ids_default_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no caller supplies agent_id/tenant_id (today's reality for every in-repo
    caller), the emission honestly carries None rather than a fabricated value."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    impl = AnthropicInferenceProvider(model="claude-opus-4-8")
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_fake_message(usage=_fake_usage())
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime._inference_split.providers.logger", mock_logger)

    await impl.generate("hello")

    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert usage_calls[0].kwargs["agent_id"] is None
    assert usage_calls[0].kwargs["tenant_id"] is None


@pytest.mark.asyncio
async def test_facade_generate_passes_correlation_ids_through_to_impl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public `InferenceProvider.generate()` facade forwards agent_id/tenant_id to
    the active concrete provider unchanged (proves the plumbing end-to-end, not just
    at the `AnthropicInferenceProvider` level)."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    provider = InferenceProvider(settings=InferenceSettings(provider="anthropic"))
    generate_mock = AsyncMock(return_value="ok")
    provider._impl.generate = generate_mock  # type: ignore[attr-defined, method-assign]

    await provider.generate("hello", agent_id="rafael", tenant_id="tenant-x")

    generate_mock.assert_awaited_once_with("hello", agent_id="rafael", tenant_id="tenant-x")


@pytest.mark.asyncio
async def test_noop_and_phi_zone_mock_accept_correlation_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock providers accept the new optional kwargs (uniform ABC signature) without
    ever fabricating usage — no real API call means nothing to meter."""
    noop_result = await NoopInferenceProvider().generate("x", agent_id="a", tenant_id="t")
    phi_result = await PhiZoneMockProvider().generate("x", agent_id="a", tenant_id="t")

    assert "mock" in noop_result.lower()
    assert "SYNTHETIC" in phi_result
