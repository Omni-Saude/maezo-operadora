"""LLM provider abstraction (ADR-0009).

This is the SINGLE import point for any LLM SDK in the codebase.
All other modules MUST go through this interface — never import an LLM SDK directly.

Uses pydantic-settings for configuration. Defaults to "noop" provider
when no API key is configured, returning deterministic mock responses.

Provider selection (T1.7) is via ``MAEZO_INFERENCE_PROVIDER``:

- ``noop`` (default) — deterministic mock, no network calls, no credentials.
- ``anthropic`` — real calls via the official ``anthropic`` SDK. Requires an
  API key from the environment (never committed); see
  :class:`AnthropicInferenceProvider` for precedence.
- ``phi_zone_mock`` — explicitly-labeled mock standing in for the
  not-yet-provisioned BR-resident, zero-retention PHI-zone endpoint
  (ADR-0006/ADR-0017). Every response is marked synthetic.

An unrecognized value is a fail-closed startup error (constraint 2) — the
process refuses to boot with a misconfigured provider rather than silently
degrading to ``noop``.

PHI routing (ADR-0006 "Zona PHI/Financeira", ADR-0017 network enforcement):
:meth:`InferenceProvider.generate` accepts ``phi=True`` for requests that
carry PHI-tagged content. Such a request may ONLY be served by a provider
explicitly marked ``phi_capable`` (today, only :class:`PhiZoneMockProvider`
— no real BR-resident endpoint exists yet, tracked as blocked(external)).
A PHI-tagged request against any other provider raises
:class:`PhiZoneRoutingError` — the caller must route to a human / incident,
NEVER silently falling back to the general-zone cloud provider.

Token-usage metering (T8): every REAL response that reaches
:meth:`AnthropicInferenceProvider.generate` passes through
:func:`_emit_llm_token_usage`, which reads the raw Anthropic SDK's
``response.usage`` (``input_tokens``/``output_tokens`` — NOT LangChain's
``AIMessage.usage_metadata`` shape; this codebase's single provider does
not use LangChain's chat-model wrapper) and emits it through the platform's
existing structlog + Prometheus mechanisms (:mod:`maezo.platform.observability`)
— never a parallel logging/telemetry system. Emission is PHI-safe (model id
+ token COUNTS only, never prompt/response content) and fail-safe (a
metering defect degrades to a skipped emission, never an exception that
could break or stall the LLM call — see :func:`_emit_llm_token_usage`).
Mock providers (:class:`NoopInferenceProvider`, :class:`PhiZoneMockProvider`)
never emit metering: they make no real API call, so there is nothing to
meter (constraint 3 — never fabricate).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

import anthropic
import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)

#: Default Anthropic model id used when neither MAEZO_ANTHROPIC_MODEL nor
#: MAEZO_INFERENCE_MODEL is set. Overridable — see AnthropicInferenceProvider.
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"

#: Env vars consulted for the Anthropic API key, in precedence order.
#: MAEZO_ANTHROPIC_API_KEY (repo-namespaced, preferred) wins over the
#: SDK-conventional ANTHROPIC_API_KEY. Neither is ever read from
#: InferenceSettings / pydantic-settings — credentials are STRICTLY
#: environment-sourced and never committed.
_ANTHROPIC_API_KEY_ENV_VARS = ("MAEZO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


# ---------------------------------------------------------------------------
# Errors — typed, never a silent fallback (constraint 2 / constraint 3)
# ---------------------------------------------------------------------------


class InferenceConfigError(ValueError):
    """Raised when the inference provider configuration is invalid.

    Fail-closed: an unknown provider name, or a real provider missing
    required credentials, MUST raise here rather than silently falling
    back to a mock/noop provider. Raised at construction time (startup),
    not on first use.
    """


class InferenceProviderError(RuntimeError):
    """Raised when a configured provider fails to produce a completion.

    Wraps SDK-level errors (timeouts, rate limits, auth failures, refusals,
    etc.) with a provider-agnostic type so callers never need to import —
    or catch — a specific LLM SDK's exception classes (module docstring:
    no SDK leaks past this module).
    """

    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


class PhiZoneRoutingError(PermissionError):
    """Raised when a PHI-tagged inference request has no PHI-zone provider.

    Per ADR-0006 (duas zonas) / ADR-0017 (egress enforcement), PHI-tagged
    inference must NEVER silently fall back to the general-zone cloud
    provider. This mirrors the ``PermissionError`` subclass pattern used
    elsewhere in the codebase for structural, fail-closed denials (e.g.
    ADR-0016 ``ProcessKeyNotAllowedError``) — callers must route to a
    human / incident, never retry against a different provider.
    """


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class InferenceSettings(BaseSettings):
    """Configuration for the inference provider.

    Environment variables prefixed with MAEZO_INFERENCE_ (default).

    Note: the LLM API key is intentionally NOT a field here. It is read
    directly from the environment inside the concrete provider (see
    ``_ANTHROPIC_API_KEY_ENV_VARS``) so it never round-trips through a
    settings object that might be logged, serialized, or defaulted.
    """

    model_config = {"env_prefix": "MAEZO_INFERENCE_", "extra": "ignore"}

    provider: str = "noop"
    model: str = ""
    timeout_s: float = 60.0
    max_retries: int = 2


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class BaseInferenceProvider(ABC):
    """Abstract interface every concrete LLM provider implements.

    Concrete providers are internal to this module — only the
    :class:`InferenceProvider` facade below is imported by other code
    (module docstring: single-import-point rule, ADR-0009).
    """

    #: True only for a provider explicitly designated to serve the
    #: BR-resident PHI zone (ADR-0006) — real or an explicitly-labeled
    #: mock standing in for it. False for every general-zone / test
    #: provider, including ``noop``: a PHI-tagged request must never be
    #: silently absorbed by whatever happens to be configured.
    phi_capable: ClassVar[bool] = False

    #: True for a provider whose responses are synthetic, not real model
    #: output (constraint 3 — never fabricate a real completion).
    is_mock: ClassVar[bool] = False

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Generate a completion for ``prompt``.

        ``agent_id``/``tenant_id`` are OPTIONAL correlation identifiers a caller may
        supply for observability (T8 token-metering) — see
        :func:`AnthropicInferenceProvider.generate` for how the real provider uses them.
        Every concrete provider must accept these kwargs (even the mocks, which ignore
        them) so :meth:`InferenceProvider.generate` can pass them through uniformly
        regardless of which concrete provider is active.
        """

    @abstractmethod
    def health_check(self) -> dict[str, str]:
        """Return this provider's health status (never a fabricated 'ok')."""


class NoopInferenceProvider(BaseInferenceProvider):
    """Deterministic mock provider — no network calls, no credentials.

    Default provider; suitable for tests and local development. Never
    PHI-capable (see :attr:`BaseInferenceProvider.phi_capable`): a
    PHI-tagged request must be explicitly routed to
    :class:`PhiZoneMockProvider` (or a future real PHI-zone provider),
    not silently absorbed by noop.
    """

    phi_capable: ClassVar[bool] = False
    is_mock: ClassVar[bool] = True

    async def generate(
        self,
        prompt: str,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        # agent_id/tenant_id unused: no real API call is made, so there is no token usage
        # to meter (constraint 3 — never fabricate a real completion or its usage).
        logger.info("inference_noop_generate", prompt_len=len(prompt))
        return f"[noop mock response] Received prompt ({len(prompt)} chars): {prompt[:80]}..."

    def health_check(self) -> dict[str, str]:
        return {
            "status": "warning",
            "message": (
                "Provider is 'noop' — all LLM calls return mock responses. "
                "Set MAEZO_INFERENCE_PROVIDER to a real provider for production."
            ),
        }


# ---------------------------------------------------------------------------
# Token-usage metering (T8) — best-effort, PHI-safe, COUNTS ONLY
# ---------------------------------------------------------------------------


def _emit_llm_token_usage(
    response: object,
    *,
    provider: str,
    fallback_model: str,
    agent_id: str | None,
    tenant_id: str | None,
) -> None:
    """Best-effort token-usage metering emission. NEVER raises.

    Called once per real LLM response, from :meth:`AnthropicInferenceProvider.generate`
    — the single seam every model response passes through (ADR-0009 single-import-point).

    Reads the raw Anthropic SDK's ``response.usage`` (``input_tokens``/``output_tokens``
    — see the claude-api skill: this is NOT the same shape as LangChain's
    ``AIMessage.usage_metadata``, which this codebase does not use). If ``usage`` is
    absent, or present but missing a field, this degrades to a silent skip (plus a debug
    log) rather than raising — some responses may lack it (a test double, a future SDK
    response shape, a defensive edge case), and a metering defect must NEVER break or
    stall the LLM call that already succeeded.

    Emits through the platform's EXISTING structured-logging + telemetry mechanisms —
    does not invent a parallel one:
      - structlog: an ``llm_token_usage`` event via this module's own logger (the same
        structlog instance every other event in this file already uses).
      - Prometheus: :func:`maezo.platform.observability.record_llm_token_usage`, which
        increments the ``maezo_llm_tokens_total`` counter (mirrors the
        ``record_worker_task_outcome`` pattern in the same module).

    PHI-safe by construction: reads only ``response.usage`` (token counts) and
    ``response.model`` (a model id, e.g. ``"claude-opus-4-8"``) — never
    ``response.content`` (the actual prompt/response text) and never any tenant PHI.

    COUNTS ONLY. EXTENSION POINT (not built here, deliberately): a future
    ``compute_cost(usage, pricing_table)`` could turn these counts into a dollar
    estimate, but pricing values are a finance-gated human decision — this function
    emits token counts and a model id, never a computed cost.
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            logger.debug("llm_token_usage_absent", provider=provider, model=fallback_model)
            return

        raw_input = getattr(usage, "input_tokens", None)
        raw_output = getattr(usage, "output_tokens", None)
        if not isinstance(raw_input, int) or not isinstance(raw_output, int):
            logger.debug(
                "llm_token_usage_incomplete",
                provider=provider,
                model=fallback_model,
                has_input_tokens=raw_input is not None,
                has_output_tokens=raw_output is not None,
            )
            return

        input_tokens: int = raw_input
        output_tokens: int = raw_output
        total_tokens = input_tokens + output_tokens
        model = str(getattr(response, "model", None) or fallback_model)

        logger.info(
            "llm_token_usage",
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            agent_id=agent_id,
            tenant_id=tenant_id,
        )

        # Local import (mirrors tools/workers/harness.py's `_emit_worker_task_outcome`):
        # avoids a hard import-time dependency of this module on the observability stack.
        from maezo.platform.observability import record_llm_token_usage  # noqa: PLC0415

        record_llm_token_usage(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except Exception:  # noqa: BLE001 — defensive: metering must never break/stall an LLM call.
        logger.debug("llm_token_usage_emit_failed", provider=provider, exc_info=True)


class AnthropicInferenceProvider(BaseInferenceProvider):
    """Real LLM provider backed by the official ``anthropic`` SDK.

    General-zone cloud provider (ADR-0006) — never PHI-capable. API key is
    STRICTLY environment-sourced, never committed, resolved with this
    precedence:

    1. ``MAEZO_ANTHROPIC_API_KEY`` (repo-namespaced; preferred)
    2. ``ANTHROPIC_API_KEY`` (SDK-conventional fallback)

    A missing key raises :class:`InferenceConfigError` at construction
    (startup) — a misconfigured provider FAILS, it does not silently
    degrade to noop (constraint 2/3).

    Model selection: ``MAEZO_INFERENCE_MODEL`` (via
    :class:`InferenceSettings`) if set, else :data:`DEFAULT_ANTHROPIC_MODEL`.

    Timeouts + bounded retries on 429/5xx are delegated to the SDK's own
    client (``timeout=``, ``max_retries=``) — the SDK already retries
    connection errors, 408/409/429/>=500 with exponential backoff, so no
    hand-rolled retry loop is added on top.
    """

    phi_capable: ClassVar[bool] = False
    is_mock: ClassVar[bool] = False

    def __init__(self, *, model: str = "", timeout_s: float = 60.0, max_retries: int = 2) -> None:
        api_key = self._resolve_api_key()
        if not api_key:
            raise InferenceConfigError(
                "AnthropicInferenceProvider requires an API key but none was "
                f"found. Set one of {_ANTHROPIC_API_KEY_ENV_VARS} in the "
                "environment (never commit it). Refusing to start with "
                "provider='anthropic' and no credentials — fail-closed, no "
                "silent fallback to noop."
            )

        self._model = model or DEFAULT_ANTHROPIC_MODEL
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_s,
            max_retries=max_retries,
        )
        logger.info(
            "inference_anthropic_configured",
            model=self._model,
            timeout_s=timeout_s,
            max_retries=max_retries,
        )

    @staticmethod
    def _resolve_api_key() -> str:
        for env_var in _ANTHROPIC_API_KEY_ENV_VARS:
            value = os.environ.get(env_var, "")
            if value:
                return value
        return ""

    async def generate(
        self,
        prompt: str,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError as exc:
            raise InferenceProviderError(
                "anthropic", f"authentication failed: {exc}", retryable=False
            ) from exc
        except anthropic.RateLimitError as exc:
            raise InferenceProviderError("anthropic", f"rate limited: {exc}", retryable=True) from exc
        except anthropic.APITimeoutError as exc:
            raise InferenceProviderError("anthropic", f"request timed out: {exc}", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise InferenceProviderError("anthropic", f"connection error: {exc}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code >= 500
            raise InferenceProviderError(
                "anthropic", f"API error ({exc.status_code}): {exc.message}", retryable=retryable
            ) from exc

        # T8: meter token usage for EVERY response that reaches this point — including a
        # refusal (still a genuine, billable-or-not API response with its own `usage`).
        # Best-effort/never-raising by construction (see `_emit_llm_token_usage`), so this
        # can never turn a successful API call into a failed `generate()` call.
        _emit_llm_token_usage(
            response,
            provider="anthropic",
            fallback_model=self._model,
            agent_id=agent_id,
            tenant_id=tenant_id,
        )

        if response.stop_reason == "refusal":
            raise InferenceProviderError(
                "anthropic", "request declined by safety classifiers (stop_reason=refusal)", retryable=False
            )

        text = "".join(block.text for block in response.content if block.type == "text")
        logger.info(
            "inference_anthropic_generate",
            model=self._model,
            prompt_len=len(prompt),
            response_len=len(text),
            stop_reason=response.stop_reason,
        )
        return text

    def health_check(self) -> dict[str, str]:
        # Credential-presence check, not a real network call: health_check()
        # is synchronous and is called from non-async contexts (e.g. readiness
        # probes). A cheap real call would need to be async and billed; a
        # missing/invalid key still surfaces loudly on the first real
        # generate() call via InferenceProviderError. Never fabricate 'ok'
        # beyond what was actually verified here (constraint 3).
        return {
            "status": "ok",
            "message": (
                f"Provider 'anthropic' configured (model={self._model}); credential present in environment."
            ),
        }


class PhiZoneMockProvider(BaseInferenceProvider):
    """Explicitly-labeled mock for the BR-resident PHI-zone endpoint.

    Stands in for the not-yet-provisioned, zero-retention, BR-resident
    inference endpoint required by ADR-0006 ("Zona PHI/Financeira") /
    ADR-0017 (network egress enforcement) — provisioning the real endpoint
    is blocked(external), see T1.7 charter. This provider exists so the
    PHI-zone routing SEAM can be exercised end-to-end today WITHOUT ever
    fabricating a real completion or silently routing PHI to the general
    cloud provider (constraint 3).

    Every response is unambiguously marked synthetic, and
    :meth:`health_check` reports the mock status loudly rather than a
    fabricated "ok".
    """

    phi_capable: ClassVar[bool] = True
    is_mock: ClassVar[bool] = True

    async def generate(
        self,
        prompt: str,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        # agent_id/tenant_id unused: no real API call is made, so there is no token usage
        # to meter (constraint 3 — never fabricate a real completion or its usage).
        logger.warning(
            "inference_phi_zone_mock_generate",
            message=(
                "PHI-zone inference served by an EXPLICITLY-LABELED MOCK — "
                "no real BR-resident PHI-zone endpoint is configured. This "
                "response is SYNTHETIC, not a real model completion."
            ),
            prompt_len=len(prompt),
        )
        return (
            "[SYNTHETIC RESPONSE — phi_zone_mock, NOT a real model completion] "
            f"received {len(prompt)} chars; no BR-resident PHI-zone provider "
            "is configured yet (blocked(external), see T1.7 charter). "
            f"prompt_preview={prompt[:80]!r}"
        )

    def health_check(self) -> dict[str, str]:
        return {
            "status": "warning",
            "message": (
                "Provider is 'phi_zone_mock' — an EXPLICITLY-LABELED MOCK "
                "standing in for the not-yet-provisioned BR-resident, "
                "zero-retention PHI-zone endpoint (ADR-0006/ADR-0017). ALL "
                "responses from this provider are SYNTHETIC. Do not treat "
                "this as production-ready; provisioning the real endpoint "
                "is blocked(external)."
            ),
        }


# ---------------------------------------------------------------------------
# Provider registry / factory — fail-closed selection (constraint 2)
# ---------------------------------------------------------------------------


def _build_noop(_: InferenceSettings) -> BaseInferenceProvider:
    return NoopInferenceProvider()


def _build_anthropic(settings: InferenceSettings) -> BaseInferenceProvider:
    return AnthropicInferenceProvider(
        model=settings.model,
        timeout_s=settings.timeout_s,
        max_retries=settings.max_retries,
    )


def _build_phi_zone_mock(_: InferenceSettings) -> BaseInferenceProvider:
    return PhiZoneMockProvider()


_PROVIDER_FACTORIES: dict[str, Callable[[InferenceSettings], BaseInferenceProvider]] = {
    "noop": _build_noop,
    "anthropic": _build_anthropic,
    "phi_zone_mock": _build_phi_zone_mock,
}


def _build_provider(settings: InferenceSettings) -> BaseInferenceProvider:
    factory = _PROVIDER_FACTORIES.get(settings.provider)
    if factory is None:
        raise InferenceConfigError(
            f"Unknown MAEZO_INFERENCE_PROVIDER={settings.provider!r}. Valid "
            f"values: {sorted(_PROVIDER_FACTORIES)}. Fail-closed: refusing to "
            "start with an unrecognized provider rather than silently "
            "degrading to noop."
        )
    return factory(settings)


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------


class InferenceProvider:
    """Abstract interface for LLM inference providers.

    Per ADR-0009, all LLM access MUST flow through this class.
    No other module imports any LLM SDK directly.

    The active concrete provider is selected once, at construction time,
    from ``settings.provider`` (fail-closed — see :func:`_build_provider`).
    The default "noop" provider returns deterministic mock responses and
    requires no API key — suitable for testing and development.
    """

    def __init__(self, settings: InferenceSettings | None = None) -> None:
        """Initialize the inference provider.

        Args:
            settings: Optional InferenceSettings; if None, uses defaults
                       (noop provider, no API key).

        Raises:
            InferenceConfigError: unknown provider, or a real provider
                missing required configuration (e.g. no API key).
        """
        self._settings = settings or InferenceSettings()
        self._impl = _build_provider(self._settings)
        logger.info(
            "inference_provider_initialized",
            provider=self._settings.provider,
            model=self._settings.model or "default",
            phi_capable=self._impl.phi_capable,
            is_mock=self._impl.is_mock,
        )
        if self._impl.is_mock:
            logger.warning(
                "inference_provider_is_mock",
                provider=self._settings.provider,
                message=(
                    f"LLM provider '{self._settings.provider}' is a MOCK — "
                    "all responses are synthetic, not real model output. Set "
                    "MAEZO_INFERENCE_PROVIDER to a real provider for "
                    "production."
                ),
            )

    @property
    def provider_name(self) -> str:
        """Return the active provider name (e.g. 'noop', 'anthropic')."""
        return self._settings.provider

    @property
    def model_id(self) -> str | None:
        """Effective LLM model identifier for ADR-0007 audit provenance, or None.

        Returns the configured concrete model (e.g. the Anthropic model id) so an agent can record
        which model drove a decision (the `model_id` leg of the audit tuple, consumed by the T-C2
        process-start provenance fence). None for a provider with no concrete model configured (the
        noop mock), which is the honest value — never a fabricated model id.
        """
        return self._settings.model or None

    def health_check(self) -> dict[str, str]:
        """Return provider health status.

        Delegates to the active concrete provider. Mock providers (noop,
        phi_zone_mock) always report a loud 'warning', never a fabricated
        'ok' (constraint 3).

        Returns:
            A dict with 'status' ('ok' or 'warning') and 'message'.
        """
        return self._impl.health_check()

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Generate a response for the given prompt.

        Args:
            prompt: The input text prompt.
            phi: True if this request carries PHI-tagged content that must
                stay inside the BR-resident PHI zone (ADR-0006/ADR-0017).
                Defaults to False (general zone).
            agent_id: Optional caller-supplied correlation id (e.g. which named
                agent — "helena", "rafael", ...) for token-usage metering (T8).
                Purely observational: never affects routing or provider selection.
                Not currently populated by any in-repo caller — see
                :func:`_emit_llm_token_usage` for how a real provider uses it
                when supplied.
            tenant_id: Optional caller-supplied tenant correlation id, same
                caveats as ``agent_id``.

        Returns:
            The generated response string. In noop mode, a deterministic
            mock response. In phi_zone_mock mode, an explicitly-labeled
            synthetic response.

        Raises:
            PhiZoneRoutingError: ``phi=True`` but the active provider is
                not PHI-capable. Fail-closed — never silently routes PHI
                to the general cloud provider; the caller must route to a
                human / incident instead of retrying.
            InferenceProviderError: the active provider failed to produce
                a completion (auth, rate limit, timeout, refusal, ...).
        """
        if phi and not self._impl.phi_capable:
            raise PhiZoneRoutingError(
                "PHI-tagged inference request cannot be served by provider "
                f"'{self._settings.provider}' — it is not PHI-capable. No "
                "BR-resident PHI-zone provider is configured. Route to a "
                "human / incident; NEVER falling back to the general cloud "
                "provider (ADR-0006/ADR-0017)."
            )
        return await self._impl.generate(prompt, agent_id=agent_id, tenant_id=tenant_id)
