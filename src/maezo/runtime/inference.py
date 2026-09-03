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
- ``bedrock`` — real calls to the SAME model family through **AWS Bedrock**, via the
  ``anthropic`` SDK's classic ``bedrock-runtime`` client (``anthropic[bedrock]`` extra;
  the Mantle endpoint 404s on this account — evidence in
  :class:`BedrockInferenceProvider`). GENERAL ZONE ONLY, exactly like ``anthropic``:
  ``phi_capable`` is False and the PHI seam is untouched. No credential lives in this
  repo — authentication is SigV4 through the standard AWS credential chain (env /
  ``AWS_PROFILE`` / IRSA), resolved by botocore at request time. The sanctioned model id
  is a ``global.*`` inference profile, which routes CROSS-REGION by construction — an
  owner-level LGPD consideration recorded in ``docs/runbooks/phi-inference-ops.md``.
- ``phi_zone_mock`` — explicitly-labeled mock standing in for the
  not-yet-provisioned BR-resident, zero-retention PHI-zone endpoint
  (ADR-0006/ADR-0017). Every response is marked synthetic.
- ``br_resident`` — the real BR-resident, zero-retention adapter (Onda 2 W2
  leg 2). BUILT INERT: no shipped config selects it, and it REFUSES TO
  CONSTRUCT until three owner-supplied environment values exist
  (:data:`ENV_PHI_ENDPOINT_URL`, :data:`ENV_PHI_API_KEY`,
  :data:`ENV_PHI_VENDOR_DPA_REF`). See :class:`BrResidentInferenceProvider`.

An unrecognized value is a fail-closed startup error (constraint 2) — the
process refuses to boot with a misconfigured provider rather than silently
degrading to ``noop``.

PHI routing (ADR-0006 "Zona PHI/Financeira", ADR-0017 network enforcement):
:meth:`InferenceProvider.generate` accepts ``phi=True`` for requests that
carry PHI-tagged content. Such a request may ONLY be served by a provider
explicitly marked ``phi_capable`` (:class:`PhiZoneMockProvider`, and — since
Onda 2 W2 leg 2 — :class:`BrResidentInferenceProvider`, which is PHI-eligible
by design but refuses to construct until an owner supplies a vendor DPA
reference, so no real BR-resident endpoint is reachable today either).
A PHI-tagged request against any other provider raises
:class:`PhiZoneRoutingError` — the caller must route to a human / incident,
NEVER silently falling back to the general-zone cloud provider.

Provider capabilities (Onda 2, audit W2 §2): ``phi_capable`` alone could not be
audited, so every provider now also declares a :class:`ProviderCapabilities` —
deployment region, retention policy, training prohibition, maximum data
classification, credential source and sanctioned model ids, over CLOSED
vocabularies. Two separate, non-overlapping fail-closed checks consume it:

- ``MAEZO_INFERENCE_PHI_ZONE_REQUIRED`` (default False, so INERT today) makes
  :class:`InferenceProvider` refuse to CONSTRUCT — with
  :class:`InferenceConfigError`, as loudly as a missing API key — when the
  configured provider's capabilities fall short of the PHI contract.
- the per-call ``phi=True`` refusal above is UNCHANGED and independent: it reads
  ``phi_capable`` and fires whether or not the new flag is set (invariant I-6).

Token-usage metering (T8): every REAL response that reaches
:meth:`AnthropicInferenceProvider.generate` (and, identically,
:meth:`BedrockInferenceProvider.generate` — the Bedrock Messages endpoint returns the
same ``usage.input_tokens``/``usage.output_tokens`` shape as the 1P API) passes through
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

import asyncio
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from typing import ClassVar, Final
from urllib.parse import urlsplit

import anthropic
import structlog

from maezo.runtime._inference_split.br_regional import (
    BR_REGIONAL_ATTESTED_REGION,
    BR_REGIONAL_ENDPOINT_HOST_SUFFIXES,
    BR_REGIONAL_ENDPOINT_SCHEME,
    ENDPOINT_DENIAL_EMPTY,
    ENDPOINT_DENIAL_HOST,
    ENDPOINT_DENIAL_NOT_NORMALIZED,
    ENDPOINT_DENIAL_QUERY,
    ENDPOINT_DENIAL_SCHEME,
    ENDPOINT_DENIAL_USERINFO,
    ENV_PHI_API_KEY,
    ENV_PHI_ENDPOINT_URL,
    ENV_PHI_VENDOR_DPA_REF,
    FAKE_BR_REGIONAL_COMPLETION_PREFIX,
    FAKE_BR_REGIONAL_REDIRECT_URL,
    FAKE_BR_REGIONAL_REFUSAL_CODE,
    HEADER_CACHE_PREFIX_CHARS,
    HEADER_DATA_CLASSIFICATION,
    HEADER_TRAINING_PROHIBITED,
    HEADER_VENDOR_DPA_REF,
    HEADER_ZERO_RETENTION,
    BedrockBrRegionalTransport,
    BrRegionalRequest,
    BrRegionalResponse,
    BrRegionalTokenUsage,
    BrRegionalTransport,
    FakeBrRegionalOutcome,
    LabeledFakeBrRegionalTransport,
    RefusingBrRegionalTransport,
    _fingerprint,
    br_endpoint_denial_reasons,
    resolve_br_regional_transport,
)
from maezo.runtime._inference_split.capabilities import (
    _CLASSIFICATION_ORDER,
    ANTHROPIC_CAPABILITIES,
    BEDROCK_CAPABILITIES,
    BR_RESIDENT_CAPABILITIES,
    NOOP_CAPABILITIES,
    PHI_DENIAL_CLASSIFICATION,
    PHI_DENIAL_NOT_ALLOWED,
    PHI_DENIAL_REGION,
    PHI_DENIAL_RETENTION,
    PHI_DENIAL_TRAINING,
    PHI_ELIGIBLE_REGIONS,
    PHI_ZONE_MOCK_CAPABILITIES,
    CredentialSource,
    DataClassification,
    DeploymentRegion,
    ProviderCapabilities,
    RetentionPolicy,
    phi_zone_denial_reasons,
)
from maezo.runtime._inference_split.errors import (
    BrEndpointNotApprovedError,
    BrRegionalTransportUnavailableError,
    InferenceConfigError,
    InferenceProviderError,
    PhiZoneRoutingError,
)
from maezo.runtime._inference_split.retry_budget import (
    RETRY_STOP_ATTEMPTS_EXHAUSTED,
    RETRY_STOP_COMMITTED,
    RETRY_STOP_NOT_RETRYABLE,
    RETRY_STOP_RATE_BUDGET_EXHAUSTED,
    RetryBudget,
    _backoff_delay,
    _RetryTokenBucket,
    retry_denial_reason,
)
from maezo.runtime._inference_split.settings import (
    _ANTHROPIC_API_KEY_ENV_VARS,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_BEDROCK_MODEL,
    DEFAULT_BEDROCK_REGION,
    BedrockSettings,
    InferenceSettings,
)
from maezo.runtime.prompt_format import FormattedPrompt

logger = structlog.get_logger(__name__)

#: Explicit, EXHAUSTIVE public re-export surface of `maezo.runtime.inference`, introduced in
#: D2-02 split step 2/8 the moment a name FIRST becomes a cross-module import rather than a local
#: definition (a `class`/`def`/assignment statement is never flagged "unused" by ruff/pyflakes —
#: only an unreferenced IMPORT is — so `__all__` is what keeps an import that exists purely to
#: re-export a symbol from being auto-stripped as dead code, without a `noqa`). Content is FIXED
#: for the rest of the split (docs/reports/inference-split-plan.md §5): every name below existed
#: on `maezo.runtime.inference` before step 1 (re-derived by AST-walking the pre-split file this
#: session — see the step-2 commit body for the exact command), and `from maezo.runtime.inference
#: import X` for any of them must keep resolving, whether `X` is still DEFINED here or has moved
#: to a submodule and is RE-IMPORTED here. `logger` and every leading-underscore helper are
#: absent EXCEPT the two a test imports by name today (re-derived by grepping every
#: `from maezo.runtime.inference import _*` across `tests/` this session, not assumed):
#: `_PROVIDER_FACTORIES` and `_CLASSIFICATION_ORDER`, each already carrying its own
#: private-import lint waiver on the test's own import line — this module's `__all__` only
#: needs to keep the name from being pruned as a dead import once it stops being locally
#: defined, never to grant the test's access.
__all__ = [
    "_CLASSIFICATION_ORDER",
    "ANTHROPIC_CAPABILITIES",
    "BEDROCK_CAPABILITIES",
    "BR_REGIONAL_ATTESTED_REGION",
    "BR_REGIONAL_ENDPOINT_HOST_SUFFIXES",
    "BR_REGIONAL_ENDPOINT_SCHEME",
    "BR_RESIDENT_CAPABILITIES",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_BEDROCK_MODEL",
    "DEFAULT_BEDROCK_REGION",
    "DEFAULT_TASK_KIND",
    "ENDPOINT_DENIAL_EMPTY",
    "ENDPOINT_DENIAL_HOST",
    "ENDPOINT_DENIAL_NOT_NORMALIZED",
    "ENDPOINT_DENIAL_QUERY",
    "ENDPOINT_DENIAL_SCHEME",
    "ENDPOINT_DENIAL_USERINFO",
    "ENV_PHI_API_KEY",
    "ENV_PHI_ENDPOINT_URL",
    "ENV_PHI_VENDOR_DPA_REF",
    "FAKE_BR_REGIONAL_COMPLETION_PREFIX",
    "FAKE_BR_REGIONAL_REDIRECT_URL",
    "FAKE_BR_REGIONAL_REFUSAL_CODE",
    "HEADER_CACHE_PREFIX_CHARS",
    "HEADER_DATA_CLASSIFICATION",
    "HEADER_TRAINING_PROHIBITED",
    "HEADER_VENDOR_DPA_REF",
    "HEADER_ZERO_RETENTION",
    "MODEL_TASK_KINDS",
    "MODEL_TIERS",
    "NOOP_CAPABILITIES",
    "PHI_DENIAL_CLASSIFICATION",
    "PHI_DENIAL_NOT_ALLOWED",
    "PHI_DENIAL_REGION",
    "PHI_DENIAL_RETENTION",
    "PHI_DENIAL_TRAINING",
    "PHI_ELIGIBLE_REGIONS",
    "PHI_ZONE_MOCK_CAPABILITIES",
    "RETRY_STOP_ATTEMPTS_EXHAUSTED",
    "RETRY_STOP_COMMITTED",
    "RETRY_STOP_NOT_RETRYABLE",
    "RETRY_STOP_RATE_BUDGET_EXHAUSTED",
    "TIER_NO_MAP",
    "TIER_RESOLUTIONS",
    "TIER_UNDECLARED",
    "AnthropicInferenceProvider",
    "BaseInferenceProvider",
    "BedrockBrRegionalTransport",
    "BedrockInferenceProvider",
    "BedrockSettings",
    "BrEndpointNotApprovedError",
    "BrRegionalRequest",
    "BrRegionalResponse",
    "BrRegionalTokenUsage",
    "BrRegionalTransport",
    "BrRegionalTransportUnavailableError",
    "BrResidentInferenceProvider",
    "CredentialSource",
    "DataClassification",
    "DeploymentRegion",
    "FakeBrRegionalOutcome",
    "InferenceConfigError",
    "InferenceProvider",
    "InferenceProviderError",
    "InferenceSettings",
    "LabeledFakeBrRegionalTransport",
    "NoopInferenceProvider",
    "PhiZoneMockProvider",
    "PhiZoneRoutingError",
    "ProviderCapabilities",
    "RefusingBrRegionalTransport",
    "RetentionPolicy",
    "RetryBudget",
    "br_endpoint_denial_reasons",
    "phi_zone_denial_reasons",
    "resolve_br_regional_transport",
    "retry_denial_reason",
]


# ---------------------------------------------------------------------------
# Model-tier routing (AF-12, ADR-0009 §2 "Routing por tarefa")
# ---------------------------------------------------------------------------

#: The CLOSED tier vocabulary, read straight off ADR-0009 §2: "classificacao -> modelo
#: rapido/barato; raciocinio critico -> fronteira; lote -> batch tier". `fast` and `frontier` are
#: what every shipped `spec/agents/*/agent.yaml` declares; `batch` is accepted so an agent that
#: declares it is not refused, but NO agent declares one today — recorded in
#: `docs/review-queue.md` rather than invented here, because introducing a batch task kind is a
#: product decision about which work may be deferred, not an implementation detail.
MODEL_TIERS: Final[frozenset[str]] = frozenset({"fast", "frontier", "batch"})

#: The CLOSED task-kind vocabulary — the keys an `agent.yaml` `model:` block may use. Exactly the
#: two every agent ships, plus `batch` for symmetry with the tier above.
MODEL_TASK_KINDS: Final[frozenset[str]] = frozenset({"task_default", "reasoning", "batch"})

#: The task kind a call that names none is routed as. `task_default` IS the declared default in
#: every `agent.yaml`; picking anything else here would silently contradict the spec.
DEFAULT_TASK_KIND: Final[str] = "task_default"

#: Sentinel `tier` label for a call whose provider was built with NO tier map at all (a
#: composition root that has no `AgentDefinition` — today `platform/webhooks/service.py`'s
#: bootstrap before it loads one, and every ad-hoc `InferenceProvider()` in a test).
TIER_NO_MAP: Final[str] = "sem_mapa"

#: Sentinel `tier` label for a call naming a task kind the agent's `model:` block does not declare.
TIER_UNDECLARED: Final[str] = "nao_declarado"

#: The CLOSED `resolution` vocabulary of `maezo_llm_tier_resolution_total`.
#:
#: `modelo_unico` is the ONLY value any call produces today, and that is the honest state of this
#: repo: `InferenceSettings` carries ONE `model` field and there is no per-tier model map anywhere
#: in the configuration, so `fast` and `frontier` both resolve to the same configured model. AF-12
#: asks for exactly this — a tier that resolves fail-closed to the single configured model with an
#: explicit log/metric, NEVER a silent default to a DIFFERENT model. `modelo_por_tier` exists so
#: the day an owner supplies a real per-tier map the two cases are distinguishable in the same
#: series rather than needing a new metric.
TIER_RESOLUTIONS: Final[frozenset[str]] = frozenset({"modelo_unico", "modelo_por_tier"})


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class BaseInferenceProvider(ABC):
    """Abstract interface every concrete LLM provider implements.

    Concrete providers are internal to this module — only the
    :class:`InferenceProvider` facade below is imported by other code
    (module docstring: single-import-point rule, ADR-0009).
    """

    #: This provider's full, auditable capability declaration (Onda 2 W2 §2).
    #: Annotation-only ON PURPOSE — there is no safe default capability set, so
    #: a subclass that forgets to declare one must not silently inherit
    #: somebody else's. ``__init_subclass__`` below turns "forgot" into a
    #: class-creation-time failure instead of a runtime ``AttributeError``.
    capabilities: ClassVar[ProviderCapabilities]

    #: True only for a provider explicitly designated to serve the
    #: BR-resident PHI zone (ADR-0006) — real or an explicitly-labeled
    #: mock standing in for it. False for every general-zone / test
    #: provider, including ``noop``: a PHI-tagged request must never be
    #: silently absorbed by whatever happens to be configured.
    #:
    #: DERIVED, never independently authored: each concrete provider spells it
    #: as ``<ITS>_CAPABILITIES.phi_allowed`` and ``__init_subclass__`` refuses
    #: any class where the two disagree. It stays a plain ``bool`` ClassVar
    #: (not a property) because ``InferenceProvider.generate``'s I-6 raise and
    #: existing class-level assertions both read it directly.
    phi_capable: ClassVar[bool] = False

    #: True for a provider whose responses are synthetic, not real model
    #: output (constraint 3 — never fabricate a real completion). ORTHOGONAL to
    #: :attr:`capabilities`: capabilities describe the ZONE contract a provider
    #: satisfies, ``is_mock`` describes whether its output is REAL. Today's
    #: PHI-eligible provider is a mock, which is precisely why both facts have
    #: to be stated separately rather than folded together.
    is_mock: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Fail-closed at CLASS CREATION: capabilities declared, and consistent.

        Two ways a provider could lie by omission, both refused here — before
        any instance exists, any config is read, or any request is served:

        1. no ``capabilities`` of its own — an undeclared provider would
           otherwise inherit whatever the MRO happened to offer;
        2. a ``phi_capable`` that disagrees with ``capabilities.phi_allowed``
           — the exact two-sources-of-truth drift that a capability schema is
           supposed to eliminate.

        SCOPE OF THE CLAIM, precisely: raising here makes the drift unrepresentable
        IN A CLASS BODY, AT CLASS-CREATION TIME. Runtime mutation of class or instance
        attributes remains possible, as with any Python attribute — an intermediate
        subclass may override ``__init_subclass__`` without calling ``super()``, a
        ``ClassVar`` may be reassigned after the class exists, an instance attribute may
        shadow the class one, and ``object.__setattr__`` defeats the frozen dataclass.
        These guards target AUTHORING DRIFT — the provider someone writes and reviews —
        not a hostile in-process actor, against whom no in-process check would hold
        anyway. This is not a regression from the ``phi_capable`` boolean it replaced,
        which was equally mutable; it is the honest boundary of what is being claimed.
        """
        super().__init_subclass__(**kwargs)
        declared = cls.__dict__.get("capabilities")
        if not isinstance(declared, ProviderCapabilities):
            raise TypeError(
                f"{cls.__name__} must declare its own `capabilities: ClassVar[ProviderCapabilities]`. "
                "Every inference provider states where it runs, what it retains, whether our input "
                "trains it, and how sensitive the data it may receive is — there is no safe default."
            )
        if cls.phi_capable is not declared.phi_allowed:
            raise TypeError(
                f"{cls.__name__}.phi_capable ({cls.phi_capable}) contradicts its "
                f"capabilities.phi_allowed ({declared.phi_allowed}). `phi_capable` must be DERIVED "
                "from the capability declaration (spell it `<NAME>_CAPABILITIES.phi_allowed`), never "
                "authored independently."
            )

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

    capabilities: ClassVar[ProviderCapabilities] = NOOP_CAPABILITIES
    phi_capable: ClassVar[bool] = NOOP_CAPABILITIES.phi_allowed
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

    capabilities: ClassVar[ProviderCapabilities] = ANTHROPIC_CAPABILITIES
    phi_capable: ClassVar[bool] = ANTHROPIC_CAPABILITIES.phi_allowed
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


class BedrockInferenceProvider(BaseInferenceProvider):
    """Real LLM provider backed by **AWS Bedrock**, via the ``anthropic`` SDK's Bedrock client.

    GENERAL-ZONE CLOUD PROVIDER (ADR-0006) — never PHI-capable, exactly like
    :class:`AnthropicInferenceProvider`. Adding this provider changes NOTHING about the PHI
    zone: ``phi_capable`` is ``False`` (derived from :data:`BEDROCK_CAPABILITIES`), so a
    ``phi=True`` request against it raises :class:`PhiZoneRoutingError` at the facade before
    any client is touched, and the ``br_resident`` leg is untouched.

    WHY THIS EXISTS ALONGSIDE ``anthropic``: the same model family, reached over an access path
    the organisation has already validated (AWS account + IAM), so no direct vendor API key has
    to be issued, held or rotated by this platform.

    **Client — ``anthropic.AsyncAnthropicBedrock``, THE CLASSIC ``bedrock-runtime`` PATH, CHOSEN
    AGAINST THE GENERAL RECOMMENDATION AND FOR A MEASURED REASON.** The recommended client is
    normally ``AnthropicBedrockMantle`` (the Messages-API Bedrock endpoint), and the pinned SDK
    (``anthropic`` 0.117.0, ``anthropic[bedrock]`` extra) exposes both. It is not used here
    because IT DOES NOT WORK ON THIS ACCOUNT — established by a live probe with the real session
    (profile ``amh-data-dev``, ``sa-east-1``, 2026-08-12), not inferred from documentation:

    * Mantle + ``anthropic.claude-opus-5`` → HTTP 404, "The model … does not exist". The request
      reached the Mantle endpoint and SigV4 verified, so this is not a credential or signing
      failure (request_id ``req_wg2absqs…``).
    * Mantle + ``global.anthropic.claude-opus-5`` → the same 404.
    * ``bedrock-runtime converse --model-id global.anthropic.claude-opus-5`` → SUCCESS
      (``end_turn``, 179 output tokens, genuine Opus-5 output).

    i.e. the account serves Claude through ``global.*`` inference profiles on classic
    ``bedrock-runtime``, and Mantle is not enabled for it. ``AsyncAnthropicBedrock`` targets
    ``bedrock-runtime`` and accepts inference-profile ids, so it is the path that actually
    reaches a model. This is a DEPLOYMENT fact, not a preference: if Mantle is later enabled for
    the account, switching back is a one-line change here plus the client pin in
    ``tests/unit/runtime/test_inference_bedrock.py``, and the evidence above is what a future
    reader needs in order to know the switch is safe to make.

    Everything downstream is unaffected by the choice: both clients expose the same
    ``messages.create`` surface and return the same ``usage``/``stop_reason`` shape, so T8
    metering and the error taxonomy below are shared with the first-party provider rather than
    duplicated.

    **Credentials — none in this repo, and none read here.** Authentication is SigV4 through the
    standard AWS credential chain (``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY``,
    ``AWS_PROFILE``, IRSA/web-identity, instance metadata), resolved by botocore AT REQUEST TIME.
    This class therefore has NO construction-time credential gate, which is a real difference
    from :class:`AnthropicInferenceProvider` and not an oversight: there is no credential for it
    to check, and a check that resolved the chain would be a network call in a synchronous
    constructor. A missing/invalid credential surfaces on the first :meth:`generate` as an
    :class:`InferenceProviderError` (see the catch-all clause), never as a silent degrade.
    :data:`BEDROCK_CAPABILITIES` declares ``credential_source=AWS_DEFAULT_CHAIN`` precisely so
    this difference is auditable rather than implied.

    **What IS fail-closed at construction:** an explicitly-blank region or model id. Both have
    defaults, so a blank one can only come from an operator setting the variable to ``""`` — a
    stated intent this module answers with a refusal rather than a guess.

    **Model selection**, first non-empty wins:

    1. ``MAEZO_INFERENCE_MODEL`` (via :class:`InferenceSettings`, i.e. the ``model=`` argument) —
       the provider-agnostic override every provider already honours;
    2. ``MAEZO_BEDROCK_MODEL_ID`` (via :class:`BedrockSettings`);
    3. :data:`DEFAULT_BEDROCK_MODEL`.

    **Region:** the ``region=`` argument, else ``MAEZO_BEDROCK_REGION``, else
    :data:`DEFAULT_BEDROCK_REGION`.

    **Request shape.** ``model`` + ``max_tokens`` + ``messages`` and nothing else. No
    ``temperature``/``top_p``/``top_k`` (removed on the current models — sending one is a 400)
    and no ``thinking`` block: adaptive thinking is the default on ``claude-opus-5`` when the
    parameter is omitted, and ``budget_tokens`` no longer exists.

    Timeouts and bounded retries on 429/5xx are delegated to the SDK client (``timeout=``,
    ``max_retries=``), mirroring :class:`AnthropicInferenceProvider` — no hand-rolled retry loop
    is added on top, and the W8 :class:`RetryBudget` is BR-resident-only and does not apply here.
    """

    capabilities: ClassVar[ProviderCapabilities] = BEDROCK_CAPABILITIES
    phi_capable: ClassVar[bool] = BEDROCK_CAPABILITIES.phi_allowed
    is_mock: ClassVar[bool] = False

    def __init__(
        self,
        *,
        model: str = "",
        region: str = "",
        timeout_s: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        bedrock_settings = BedrockSettings()

        self._region = (region or bedrock_settings.region).strip()
        if not self._region:
            raise InferenceConfigError(
                f"BedrockInferenceProvider requires an AWS region but {'MAEZO_BEDROCK_REGION'!r} "
                "resolved to an empty value. There IS a default "
                f"({DEFAULT_BEDROCK_REGION!r}), so an empty value can only come from an operator "
                "setting the variable blank — refusing to guess a region on their behalf. "
                "Fail-closed, no silent fallback to noop."
            )

        self._model = (model or bedrock_settings.model_id).strip() or DEFAULT_BEDROCK_MODEL

        # NO CREDENTIAL IS READ HERE — see the class docstring. Constructing the client performs
        # no network I/O and resolves no credential; botocore does both lazily, per request.
        # `AsyncAnthropicBedrock` (classic `bedrock-runtime`), NOT `…BedrockMantle`: the Mantle
        # endpoint 404s on this account — see the class docstring for the live evidence.
        self._client = anthropic.AsyncAnthropicBedrock(
            aws_region=self._region,
            timeout=timeout_s,
            max_retries=max_retries,
        )
        logger.info(
            "inference_bedrock_configured",
            # Model id, region and knobs only — no credential exists here to leak, and none of
            # these fields is content-bearing (T8 pinned-fields discipline).
            model=self._model,
            aws_region=self._region,
            timeout_s=timeout_s,
            max_retries=max_retries,
        )

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
        # The dispositions below are MIRRORED from `AnthropicInferenceProvider.generate`, clause
        # for clause, deliberately: the Bedrock client is the same `anthropic` SDK raising the
        # same exception classes, so a second, subtly-different retryability table would be
        # exactly the drift the `_DISPOSITIONS` discipline exists to prevent. `committed` is left
        # at its default (False) for the same reason it is on the Anthropic provider — the W8
        # commit veto is a BR-resident/PHI concern and no general-zone consumer reads the field;
        # inventing a value here would create a second notion of it in this module.
        except anthropic.AuthenticationError as exc:
            raise InferenceProviderError("bedrock", f"authentication failed: {exc}", retryable=False) from exc
        except anthropic.RateLimitError as exc:
            raise InferenceProviderError("bedrock", f"rate limited: {exc}", retryable=True) from exc
        except anthropic.APITimeoutError as exc:
            raise InferenceProviderError("bedrock", f"request timed out: {exc}", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise InferenceProviderError("bedrock", f"connection error: {exc}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code >= 500
            raise InferenceProviderError(
                "bedrock", f"API error ({exc.status_code}): {exc.message}", retryable=retryable
            ) from exc
        except Exception as exc:  # noqa: BLE001 — no SDK/transport type may leak past this module.
            # THE CLAUSE THE ANTHROPIC PROVIDER DOES NOT NEED, and the reason this one does:
            # SigV4 signing happens INSIDE the request, in botocore, which raises its own
            # exception hierarchy (`NoCredentialsError`, `ProfileNotFound`, `NoRegionError`, …)
            # that is neither an `anthropic` error nor something this module's callers may be
            # asked to import (module docstring: no SDK leaks past this module). Without this
            # clause a missing AWS credential would surface to an agent graph as a raw botocore
            # exception. Same shape as `BrResidentInferenceProvider._send`'s wrap, including
            # carrying only `type(exc).__name__`: a botocore message can name a profile or an
            # assumed-role ARN, which is deployment detail this error does not need to publish.
            raise InferenceProviderError(
                "bedrock",
                f"AWS call failed before or during signing: {type(exc).__name__}. Check the AWS "
                "credential chain (env / AWS_PROFILE / IRSA) and the IAM permission to invoke "
                f"model {self._model!r} in region {self._region!r}.",
                retryable=False,
            ) from exc

        # T8: metered through the SAME seam as every other real provider — Bedrock returns the
        # 1P `usage.input_tokens`/`usage.output_tokens` shape `_emit_llm_token_usage` already
        # reads. Best-effort/never-raising, so it cannot turn a successful call into a failure.
        _emit_llm_token_usage(
            response,
            provider="bedrock",
            fallback_model=self._model,
            agent_id=agent_id,
            tenant_id=tenant_id,
        )

        # BEFORE reading `content`. A refusal is a well-formed HTTP 200 whose `content` may be
        # empty, so indexing it unconditionally would raise an IndexError instead of the typed,
        # non-retryable provider error a caller can route on.
        if response.stop_reason == "refusal":
            raise InferenceProviderError(
                "bedrock", "request declined by safety classifiers (stop_reason=refusal)", retryable=False
            )

        text = "".join(block.text for block in response.content if block.type == "text")
        logger.info(
            "inference_bedrock_generate",
            model=self._model,
            aws_region=self._region,
            prompt_len=len(prompt),
            response_len=len(text),
            stop_reason=response.stop_reason,
        )
        return text

    def health_check(self) -> dict[str, str]:
        # CONFIGURATION status, and the message says so rather than implying more (constraint 3).
        # `AnthropicInferenceProvider.health_check` can honestly report "credential present"
        # because it read one at construction; this provider read none — the AWS chain resolves at
        # request time — so claiming a verified credential here would be exactly the fabricated
        # 'ok' that comment forbids. A missing/invalid credential still surfaces loudly on the
        # first `generate()` as an `InferenceProviderError`.
        return {
            "status": "ok",
            "message": (
                f"Provider 'bedrock' configured (model={self._model}, region={self._region}); "
                "credentials resolve from the standard AWS chain at request time and were NOT "
                "verified here."
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

    capabilities: ClassVar[ProviderCapabilities] = PHI_ZONE_MOCK_CAPABILITIES
    phi_capable: ClassVar[bool] = PHI_ZONE_MOCK_CAPABILITIES.phi_allowed
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


class BrResidentInferenceProvider(BaseInferenceProvider):
    """BR-resident, zero-retention PHI-zone adapter — PHI-eligible BY DESIGN, UN-BOOTABLE TODAY.

    Read :data:`BR_RESIDENT_CAPABILITIES` first: it explains, field by field, which parts of the
    PHI contract this class ENFORCES CLIENT-SIDE and which parts remain owed by an owner.

    THREE INDEPENDENT OWNER GATES, all checked at construction, each raising
    :class:`InferenceConfigError` naming the specific act that is missing:

    1. :data:`ENV_PHI_ENDPOINT_URL` — and it must clear
       :func:`br_endpoint_denial_reasons`, so a non-BR endpoint is refused at STARTUP, not on the
       first PHI request.
    2. :data:`ENV_PHI_API_KEY` — environment-sourced, never committed, never logged, never in an
       error message (same posture and the same canary test as the Anthropic key).
    3. :data:`ENV_PHI_VENDOR_DPA_REF` — the honesty gate. A reference to the signed
       zero-retention/BR-residency agreement with the vendor. Leg 1 established that NO SUCH
       AGREEMENT EXISTS IN THIS TREE, and this class does not pretend otherwise: it declares the
       contract it was BUILT to enforce, and then refuses to construct until a human states that
       the counterparty half exists. That is what keeps a capability declaration written by an
       agent from becoming a production PHI route.

    Plus a fourth, non-owner gate: ``MAEZO_INFERENCE_MODEL`` must be set explicitly. There is no
    default, because there is no sanctioned BR-zone model id to default TO — see
    `BR_RESIDENT_CAPABILITIES`' `supported_model_versions` note.

    ``is_mock = False`` AND ``phi_capable = True`` together, which no other provider in this
    module does, so the combination deserves its justification stated: this is a REAL adapter
    (it fabricates nothing — with no transport wired it RAISES, see
    :class:`RefusingBrRegionalTransport`) that is DESIGNATED for the PHI zone. `PhiZoneMockProvider`
    is the mirror image — PHI-designated but synthetic — and keeping both representable is exactly
    why leg 1 refused to fold `is_mock` into the capability set.

    RETRY IS BUDGETED, IDEMPOTENCY-AWARE, AND OFF BY DEFAULT (W8, leg 4). :class:`RetryBudget`
    defaults to a single attempt — a PHI path never silently re-dials — and any retry needs BOTH
    ``retryable`` (the ``_DISPOSITIONS`` truth: only an OUTAGE) AND ``not committed`` (the request
    never reached the bytes-sent point). A committed, non-idempotent call (a read timeout AFTER the
    prompt was transmitted) is NEVER re-sent: re-transmitting PHI is a data-exposure + double-spend
    hazard. Exhausting the budget RAISES (routes to the human/incident seam), never loops. See
    :func:`retry_denial_reason` and :meth:`_send_with_budget`.
    """

    capabilities: ClassVar[ProviderCapabilities] = BR_RESIDENT_CAPABILITIES
    phi_capable: ClassVar[bool] = BR_RESIDENT_CAPABILITIES.phi_allowed
    is_mock: ClassVar[bool] = False

    #: Response budget. Mirrors `AnthropicInferenceProvider.generate`'s literal 4096 rather than
    #: inventing a different number for the PHI zone.
    MAX_TOKENS: ClassVar[int] = 4096

    def __init__(
        self,
        *,
        model: str = "",
        timeout_s: float = 60.0,
        transport: BrRegionalTransport | None = None,
        retry_budget: RetryBudget | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        endpoint_url = os.environ.get(ENV_PHI_ENDPOINT_URL, "").strip()
        credential = os.environ.get(ENV_PHI_API_KEY, "").strip()
        dpa_ref = os.environ.get(ENV_PHI_VENDOR_DPA_REF, "").strip()

        # Ordered most-owner-ish first, so the FIRST message an operator sees names the act that
        # is hardest to satisfy, rather than sending them to fix a URL before discovering there
        # is no contract.
        if not dpa_ref:
            raise InferenceConfigError(
                f"BrResidentInferenceProvider requires {ENV_PHI_VENDOR_DPA_REF} — a reference to "
                "the SIGNED zero-retention / BR-residency agreement with the inference vendor. "
                "MISSING OWNER ACT: no such agreement exists anywhere in this repository, and "
                "this adapter will not route PHI on the strength of a capability declaration it "
                "wrote about itself. A human with authority must execute the DPA and set this "
                "variable to its reference. Refusing to start — fail-closed, exactly like a "
                "missing credential."
            )
        # O portão da chave não se aplica a um transporte que traz a própria credencial —
        # `BedrockBrRegionalTransport` autentica por SigV4 pela cadeia da AWS, e exigir uma
        # `MAEZO_PHI_API_KEY` inventada só para satisfazer o portão seria mentir para o portão.
        # Os outros dois (DPA e endpoint) continuam valendo INTEGRALMENTE.
        transporte_traz_credencial = bool(getattr(transport, "usa_credencial_propria", False))
        if not credential and not transporte_traz_credencial:
            raise InferenceConfigError(
                f"BrResidentInferenceProvider requires a credential but {ENV_PHI_API_KEY} is not "
                "set in the environment (never commit it). Refusing to start with a PHI-zone "
                "provider and no credentials — fail-closed, no silent fallback to noop."
            )
        endpoint_reasons = br_endpoint_denial_reasons(endpoint_url)
        if endpoint_reasons:
            raise InferenceConfigError(
                f"BrResidentInferenceProvider refuses its configured endpoint: {ENV_PHI_ENDPOINT_URL} "
                f"fails {list(endpoint_reasons)}. An approved endpoint is "
                f"{BR_REGIONAL_ENDPOINT_SCHEME}://, carries no userinfo/query/fragment, and has a "
                f"host ending in one of {list(BR_REGIONAL_ENDPOINT_HOST_SUFFIXES)}. Refusing to "
                "start — PHI must never leave the BR-resident zone (ADR-0006/ADR-0017), and that "
                "is decided here, before the first request, not after it."
            )
        if not model.strip():
            raise InferenceConfigError(
                "BrResidentInferenceProvider requires an explicit MAEZO_INFERENCE_MODEL. There is "
                "deliberately NO default: this repo sanctions no BR-zone model id "
                "(BR_RESIDENT_CAPABILITIES.supported_model_versions is empty because the vendor "
                "catalogue is unknown pending the DPA), and defaulting would mean inventing one. "
                "Refusing to start."
            )

        self._endpoint_url = endpoint_url
        self._credential = credential
        self._dpa_ref = dpa_ref
        self._model = model.strip()
        self._timeout_s = timeout_s
        self._transport = resolve_br_regional_transport(transport)

        # W8 leg 4: budgeted, idempotency-aware retry. Default = NO retry (see RetryBudget) — the
        # feature is INERT unless an owner injects a multi-attempt budget, exactly like every other
        # gate on this adapter. `sleep`/`now` are injected so tests drive backoff deterministically.
        self._retry_budget = retry_budget if retry_budget is not None else RetryBudget()
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._retry_now = now if now is not None else time.monotonic
        self._retry_bucket = _RetryTokenBucket(
            capacity=self._retry_budget.retry_token_capacity,
            refill_per_s=self._retry_budget.retry_token_refill_per_s,
            now=self._retry_now,
        )

        logger.info(
            "inference_br_resident_configured",
            # Host, not the full URL: a path could carry a tenant/deployment hint. No credential,
            # no DPA reference value (it names a contract, but it is still an owner's private
            # identifier) — presence only.
            endpoint_host=urlsplit(endpoint_url).hostname,
            model=self._model,
            timeout_s=timeout_s,
            deployment_region=BR_RESIDENT_CAPABILITIES.deployment_region,
            retention_policy=BR_RESIDENT_CAPABILITIES.retention_policy,
            dpa_ref_present=True,
            transport=type(self._transport).__name__,
        )

    def _build_request(self, formatted: FormattedPrompt) -> BrRegionalRequest:
        """Assemble the wire request, including the flags the capability declaration promises."""
        return BrRegionalRequest(
            endpoint_url=self._endpoint_url,
            model=self._model,
            stable_prefix=formatted.stable_prefix,
            variable_suffix=formatted.variable_suffix,
            max_tokens=self.MAX_TOKENS,
            credential=self._credential,
            headers={
                # Sent on EVERY request — this is the client-side half of
                # `BR_RESIDENT_CAPABILITIES`' zero-retention / no-training claim.
                HEADER_ZERO_RETENTION: "true",
                HEADER_TRAINING_PROHIBITED: "true",
                HEADER_DATA_CLASSIFICATION: DataClassification.PHI.value,
                HEADER_VENDOR_DPA_REF: self._dpa_ref,
                # W8: where the vendor may set its prompt-cache breakpoint.
                HEADER_CACHE_PREFIX_CHARS: str(formatted.stable_prefix_chars),
            },
        )

    def _validate_response(self, response: object) -> BrRegionalResponse:
        """Refuse any response that does not satisfy the contract this adapter enforces.

        Every branch here is reachable from a :class:`FakeBrRegionalOutcome` member — a refusal
        with no test that can trigger it would be decoration.

        NO BRANCH INCLUDES RESPONSE CONTENT IN ITS MESSAGE. Codes, enums and the declared region
        only; the completion is exactly the PHI-bearing thing an error message must not carry.
        """
        if not isinstance(response, BrRegionalResponse):
            raise BrRegionalTransportUnavailableError(
                "BR-regional endpoint returned a payload that does not match the wire contract "
                f"(expected BrRegionalResponse, got {type(response).__name__}). Refusing to treat "
                "an unrecognized payload as a completion.",
                retryable=False,
            )

        # RESIDENCY FIRST. Checked before the refusal/content branches on purpose: if PHI reached
        # a non-approved endpoint, that already happened and must be reported as itself, not
        # masked by whatever the wrong endpoint happened to answer.
        redirect_reasons = br_endpoint_denial_reasons(response.endpoint_url)
        if redirect_reasons or response.endpoint_url != self._endpoint_url:
            raise BrEndpointNotApprovedError(
                "BR-regional response came back from an endpoint this adapter did not dial "
                f"(host={urlsplit(response.endpoint_url).hostname!r}, allowlist_failures="
                f"{list(redirect_reasons)}). A redirect off the approved endpoint is a PHI "
                "residency escape (ADR-0006/ADR-0017): the completion is DISCARDED and never "
                "returned to the caller. Route to a human / incident; never retry elsewhere."
            )
        if response.served_region != BR_REGIONAL_ATTESTED_REGION:
            raise BrEndpointNotApprovedError(
                f"BR-regional endpoint attested served_region={response.served_region!r}, but "
                f"only {BR_REGIONAL_ATTESTED_REGION!r} is accepted for PHI-zone inference "
                "(ADR-0006). The completion is DISCARDED. Route to a human / incident."
            )
        if not response.zero_retention_acknowledged:
            raise BrEndpointNotApprovedError(
                f"BR-regional endpoint did not acknowledge the {HEADER_ZERO_RETENTION} flag it "
                "was sent. Without that acknowledgement the zero-retention half of "
                "BR_RESIDENT_CAPABILITIES is unmet for this request: the completion is DISCARDED "
                "rather than returned from an endpoint that may be persisting the prompt."
            )
        if not response.training_prohibited_acknowledged:
            raise BrEndpointNotApprovedError(
                f"BR-regional endpoint did not acknowledge the {HEADER_TRAINING_PROHIBITED} flag "
                "it was sent. The completion is DISCARDED rather than returned from an endpoint "
                "that may be training on PHI input."
            )
        if response.refusal_code:
            raise InferenceProviderError(
                "br_resident",
                f"request declined by the endpoint (refusal_code={response.refusal_code})",
                retryable=False,
            )
        return response

    async def generate(
        self,
        prompt: str,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Serve one BR-resident completion, refusing at every point the contract is not met.

        The endpoint allowlist is re-checked HERE, per call, and not merely trusted from
        construction. That is the canary-ready property: construction-time validation proves what
        was configured at boot, while a per-call check also covers an instance whose endpoint was
        mutated afterwards and — through :meth:`_validate_response` — where the response actually
        came from. Leg 3's network canary asserts against the same two refusals.
        """
        call_reasons = br_endpoint_denial_reasons(self._endpoint_url)
        if call_reasons:
            raise BrEndpointNotApprovedError(
                "BR-resident inference refused before dialling: the configured endpoint fails "
                f"{list(call_reasons)}. PHI must never leave the BR-resident zone "
                "(ADR-0006/ADR-0017). Route to a human / incident."
            )

        # W8, AND THE HONEST SPLIT FOR AN OPAQUE STRING. The caller handed us one pre-concatenated
        # prompt, so this adapter knows nothing about which part of it is static: every in-repo
        # assembly site builds `f"{instructions()}\n\n...{per_request_data}"` and hands over the
        # result. Declaring the whole thing stable would be false — it demonstrably contains
        # per-request content — and would ALSO tell the vendor to cache a prefix that changes
        # every request, which is worse than not caching. So: nothing is claimed stable, the
        # breakpoint is 0, and a caller that knows its own split uses `generate_formatted`.
        #
        # Built DIRECTLY rather than through `format_cached_prompt`, which joins segments with
        # `STABLE_SEPARATOR` and would therefore append "\n\n" to the caller's prompt. Prompt
        # bytes feed `PROMPT_VERSIONS` audit provenance (ADR-0007) and eval baselines; an adapter
        # that quietly rewrites them — even by two whitespace characters — is a defect, not an
        # optimisation. `test_generate_transmits_the_callers_prompt_byte_for_byte` pins it.
        formatted = FormattedPrompt(stable_prefix="", variable_suffix=prompt)
        return await self._send_with_budget(formatted, agent_id=agent_id, tenant_id=tenant_id)

    async def generate_formatted(
        self,
        formatted: FormattedPrompt,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Serve a completion for an ALREADY cache-formatted prompt (W8).

        The structured entry point: a caller that knows which part of its prompt is static passes
        the split, and the vendor gets a declared cache breakpoint at
        ``formatted.stable_prefix_chars``. :meth:`generate` cannot recover that split from a
        pre-concatenated string, which is the whole reason this second door exists.

        Not part of :class:`BaseInferenceProvider` and not reachable through the
        :class:`InferenceProvider` facade: adding a method to the facade would breach the seam
        proof that pins its public surface to exactly four names
        (`gateway/seams/inference.py`). Wiring a cache-aware path through the gateway is a
        separate, gated change.
        """
        return await self._send_with_budget(formatted, agent_id=agent_id, tenant_id=tenant_id)

    async def _send_with_budget(
        self,
        formatted: FormattedPrompt,
        *,
        agent_id: str | None,
        tenant_id: str | None,
    ) -> str:
        """Apply the W8 retry budget around :meth:`_send` (the single attempt).

        Only a ``retryable and not committed`` :class:`InferenceProviderError` — an OUTAGE, per the
        ``_DISPOSITIONS`` truth — is ever retried, and only within the attempt AND rate bounds. A
        residency escape (`BrEndpointNotApprovedError`, a `PermissionError`) is NOT an
        `InferenceProviderError`, so it is never caught here and never retried; nor is
        `PhiZoneRoutingError` (I-6), which is raised at the facade above this adapter and never
        reaches it. When retries are exhausted (or refused), the LAST terminal exception propagates
        UNCHANGED — that raise IS the SP-OP-ESCALATION to a human (leg-3's disposition contract is
        preserved: default budget == one attempt == exactly today's behaviour).
        """
        budget = self._retry_budget
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._send(formatted, agent_id=agent_id, tenant_id=tenant_id)
            except InferenceProviderError as exc:
                reason = retry_denial_reason(exc, attempt=attempt, max_attempts=budget.max_attempts)
                if reason is None and not self._retry_bucket.try_consume():
                    # Attempts + retryability + commit all cleared; the rate bucket is the last gate.
                    reason = RETRY_STOP_RATE_BUDGET_EXHAUSTED
                if reason is not None:
                    # Terminal: this attempt's exception escalates to a human. Content-free record —
                    # counts, enum names and reason code only, NEVER the prompt/completion.
                    logger.info(
                        "inference_br_resident_retry_stop",
                        attempt=attempt,
                        max_attempts=budget.max_attempts,
                        stop_reason=reason,
                        outcome=type(exc).__name__,
                        retryable=exc.retryable,
                        committed=exc.committed,
                    )
                    raise
                delay = _backoff_delay(budget, attempt)
                logger.info(
                    "inference_br_resident_retry",
                    attempt=attempt,
                    max_attempts=budget.max_attempts,
                    next_delay_s=delay,
                    # Enum class NAME only — never the prompt, and no content is re-logged across
                    # attempts (there is none here to re-log).
                    outcome=type(exc).__name__,
                )
                await self._sleep(delay)

    async def _send(
        self,
        formatted: FormattedPrompt,
        *,
        agent_id: str | None,
        tenant_id: str | None,
    ) -> str:
        request = self._build_request(formatted)
        try:
            raw = await self._transport.send(request)
        except InferenceProviderError:
            raise  # already this module's own error type; do not re-wrap and lose `retryable`.
        except Exception as exc:  # noqa: BLE001 — no transport-level type may leak past this module.
            raise BrRegionalTransportUnavailableError(
                f"BR-regional transport failed: {type(exc).__name__}", retryable=False
            ) from exc

        response = self._validate_response(raw)

        # T8: metered through the SAME seam as general-zone traffic, never a parallel path. The
        # helper reads `.usage.input_tokens`/`.usage.output_tokens`/`.model`, which
        # `BrRegionalResponse` provides by design.
        _emit_llm_token_usage(
            response,
            provider="br_resident",
            fallback_model=self._model,
            agent_id=agent_id,
            tenant_id=tenant_id,
        )

        if response.synthetic:
            logger.warning(
                "inference_br_resident_synthetic_response",
                message=(
                    "BR-resident completion came from a LABELED FAKE transport — this response is "
                    "SYNTHETIC, not real model output. Only an explicit test injection can reach "
                    "this path; production resolves to RefusingBrRegionalTransport."
                ),
                transport=type(self._transport).__name__,
            )

        logger.info(
            "inference_br_resident_generate",
            model=response.model,
            # COUNTS, ENUMS AND FINGERPRINTS ONLY (pinned-fields bar). No prompt, no completion,
            # no endpoint path, no credential — this is the PHI zone; the whole point is that
            # nothing content-bearing reaches a log sink.
            stable_prefix_chars=formatted.stable_prefix_chars,
            variable_suffix_chars=len(formatted.variable_suffix),
            prompt_fingerprint=_fingerprint(formatted.text),
            completion_chars=len(response.completion),
            cached_prefix_tokens=response.usage.cached_prefix_tokens,
            served_region=response.served_region,
            agent_id=agent_id,
            tenant_id=tenant_id,
        )
        return response.completion

    def health_check(self) -> dict[str, str]:
        """Configuration status — never a fabricated 'ok' for an unwired transport.

        Reports `warning` while the transport is the refusing default, because that is the truth:
        the adapter is configured but cannot serve anything. Reporting `ok` here would let a
        readiness probe pass for a PHI zone that has no endpoint (constraint 3).
        """
        if isinstance(self._transport, RefusingBrRegionalTransport):
            return {
                "status": "warning",
                "message": (
                    "Provider 'br_resident' is configured (endpoint approved, credential and DPA "
                    "reference present) but NO BR-regional transport is wired — every request "
                    "fails closed. Building the real transport is a §8.2-fenced change requiring "
                    "an ADR-0017 NetworkPolicy proof."
                ),
            }
        return {
            "status": "ok",
            "message": (
                f"Provider 'br_resident' configured (model={self._model}, "
                f"transport={type(self._transport).__name__}); credential present in environment."
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


def _build_bedrock(settings: InferenceSettings) -> BaseInferenceProvider:
    """Build the Bedrock provider. Region/model id come from ``MAEZO_BEDROCK_*`` inside it.

    Only the provider-agnostic `InferenceSettings` knobs are threaded through here, exactly as
    for `_build_anthropic`; the Bedrock-specific ones are read by :class:`BedrockSettings` at
    construction so a caller constructing the provider directly (a test) gets the same
    resolution as the registry does.
    """
    return BedrockInferenceProvider(
        model=settings.model,
        timeout_s=settings.timeout_s,
        max_retries=settings.max_retries,
    )


def _build_phi_zone_mock(_: InferenceSettings) -> BaseInferenceProvider:
    return PhiZoneMockProvider()


def _build_br_resident(settings: InferenceSettings) -> BaseInferenceProvider:
    """Build the BR-resident adapter. Injects NO transport, so it resolves to the refusing one.

    That omission is the production posture, not an oversight: a real transport is a §8.2-fenced
    module wired through the gateway registry, and this factory must not be the place one appears.
    Tests reach `LabeledFakeBrRegionalTransport` by constructing the provider directly.
    """
    return BrResidentInferenceProvider(model=settings.model, timeout_s=settings.timeout_s)


def _build_bedrock_br(settings: InferenceSettings) -> BaseInferenceProvider:
    """Zona PHI servida pelo Bedrock REGIONAL, pelo adaptador que já cobra o contrato.

    Nada aqui afrouxa `BrResidentInferenceProvider`: o allowlist do endpoint, as três atestações
    por chamada e o orçamento de retry ciente de idempotência continuam sendo dele. A única
    diferença em relação a `_build_br_resident` é o transporte injetado.
    """
    return BrResidentInferenceProvider(
        model=settings.model,
        timeout_s=settings.timeout_s,
        transport=BedrockBrRegionalTransport(timeout_s=settings.timeout_s),
    )


_PROVIDER_FACTORIES: dict[str, Callable[[InferenceSettings], BaseInferenceProvider]] = {
    "noop": _build_noop,
    "anthropic": _build_anthropic,
    # GENERAL ZONE, same as `anthropic`. Not the default: `noop` remains the shipped default
    # (constraint 2), so adding this key changes nothing for a deployment that does not name it.
    "bedrock": _build_bedrock,
    "phi_zone_mock": _build_phi_zone_mock,
    # SELECTABLE BUT UNREACHABLE IN PRACTICE (INERT): nothing in any shipped config sets
    # MAEZO_INFERENCE_PROVIDER=br_resident, the default remains `noop`, and even an operator who
    # set it would get an `InferenceConfigError` naming the missing DPA. Registered anyway
    # because leg 1's registry/hierarchy cross-check requires every defined provider to be
    # selectable — a provider that exists but cannot be named is exactly the drift that guard
    # was written to catch.
    "br_resident": _build_br_resident,
    # ZONA PHI SOBRE BEDROCK REGIONAL. Mesmo provedor do `br_resident` — portanto as mesmas
    # garantias — com o transporte que fala com `bedrock-runtime.sa-east-1`. Os três portões de
    # dono continuam: sem `MAEZO_PHI_ENDPOINT_URL` na allowlist e sem `MAEZO_PHI_VENDOR_DPA_REF`
    # nomeando o instrumento contratual, isto NÃO constrói. A chave de API é o único portão que
    # não se aplica, porque SigV4 não usa bearer token.
    "bedrock_br": _build_bedrock_br,
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


def _validated_model_tiers(model_tiers: Mapping[str, str] | None) -> dict[str, str]:
    """Validate an agent-declared `task_kind -> tier` map against the ADR-0009 vocabularies.

    FAIL-CLOSED AT CONSTRUCTION, on purpose and by precedent: this is the same posture, error type
    and moment as :meth:`InferenceProvider._assert_phi_zone_capability` and the missing-API-key
    refusal. A tier this module cannot honour is a misconfiguration of the deployment, and every
    composition root already isolates a construction failure into a red readiness check — so the
    failure mode is "replica not ready", never "replica ready and routing by a tier nobody
    implements".

    `None`/empty is NOT an error: most constructions (tests, the generic roots) legitimately have
    no agent definition. Their calls are counted under the `sem_mapa` sentinel instead.
    """
    if not model_tiers:
        return {}
    validated: dict[str, str] = {}
    for task_kind, tier in model_tiers.items():
        if task_kind not in MODEL_TASK_KINDS:
            raise InferenceConfigError(
                f"unknown model task kind {task_kind!r} in the agent's `model:` block — "
                f"ADR-0009 declares {sorted(MODEL_TASK_KINDS)}. Refusing to construct an "
                "inference provider that would route by a task kind it cannot honour."
            )
        if tier not in MODEL_TIERS:
            raise InferenceConfigError(
                f"unknown model tier {tier!r} declared for task kind {task_kind!r} — ADR-0009 §2 "
                f"declares {sorted(MODEL_TIERS)}. Refusing to construct: a tier this module does "
                "not know cannot be routed, and silently ignoring it is how ADR-0009's routing "
                "stayed decorative."
            )
        validated[task_kind] = tier
    return validated


class InferenceProvider:
    """Abstract interface for LLM inference providers.

    Per ADR-0009, all LLM access MUST flow through this class.
    No other module imports any LLM SDK directly.

    The active concrete provider is selected once, at construction time,
    from ``settings.provider`` (fail-closed — see :func:`_build_provider`).
    The default "noop" provider returns deterministic mock responses and
    requires no API key — suitable for testing and development.
    """

    def __init__(
        self,
        settings: InferenceSettings | None = None,
        *,
        model_tiers: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize the inference provider.

        Args:
            settings: Optional InferenceSettings; if None, uses defaults
                       (noop provider, no API key).
            model_tiers: AF-12 — this replica's agent-declared ``task_kind -> tier`` map, from
                :meth:`maezo.agents.AgentDefinition.model_tiers`. Supplied by the composition
                root that knows WHICH agent this process serves; ``None`` (every generic
                construction) means no tier map, which is reported explicitly on every call
                rather than silently treated as "default tier".

        Raises:
            InferenceConfigError: unknown provider; a real provider missing
                required configuration (e.g. no API key); ``phi_zone_required``
                set against a provider whose :class:`ProviderCapabilities` do
                not satisfy the ADR-0006 PHI contract (see
                :meth:`_assert_phi_zone_capability`); or a ``model_tiers`` entry
                naming a task kind or tier outside the ADR-0009 vocabularies —
                fail-closed at CONSTRUCTION, the same moment and the same error
                type as a missing credential.
        """
        self._settings = settings or InferenceSettings()
        self._model_tiers = _validated_model_tiers(model_tiers)
        self._impl = _build_provider(self._settings)
        self._assert_phi_zone_capability()
        capabilities = self._impl.capabilities
        logger.info(
            "inference_provider_initialized",
            provider=self._settings.provider,
            model=self._settings.model or "default",
            phi_capable=self._impl.phi_capable,
            is_mock=self._impl.is_mock,
            # Onda 2 W2 §2: the capability declaration is part of the startup record, so an
            # operator reading logs can see WHICH zone contract the process actually booted
            # under instead of inferring it from a boolean. Closed-vocabulary enum values and
            # the operator's own flag — no prompt, no completion, no tenant data (T8 discipline).
            deployment_region=capabilities.deployment_region,
            retention_policy=capabilities.retention_policy,
            max_data_classification=capabilities.max_data_classification,
            phi_zone_required=self._settings.phi_zone_required,
            # AF-12: WHICH tier map this process booted with. An empty map in a deployed agent
            # pod is the signal that a composition root forgot to pass the AgentDefinition.
            model_tiers=dict(sorted(self._model_tiers.items())),
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

    def _assert_phi_zone_capability(self) -> None:
        """Fail-closed startup check: PHI-declared deployment vs. provider capabilities.

        Private ON PURPOSE. ``GatedInferenceProvider`` (``gateway/seams/inference.py``)
        subclasses this facade and a seam proof pins its public surface to exactly
        ``{generate, health_check, model_id, provider_name}``; a public accessor added
        here would reach the raw provider un-gated. The check needs no public surface —
        its only observable is the refusal.

        Inert unless ``MAEZO_INFERENCE_PHI_ZONE_REQUIRED`` is set (default False).

        Deliberately raises the SAME :class:`InferenceConfigError`, at the SAME moment
        (construction/startup), as ``AnthropicInferenceProvider``'s missing-API-key
        refusal: a capability MISMATCH is not a lesser defect than a missing
        credential, and must not be a warning that a deployment can boot past.

        The message names only DECLARED CAPABILITY facts (closed-vocabulary reason
        codes) and the provider name — never a prompt, a completion, or any tenant
        data. Nothing PHI-shaped exists at construction time anyway; the discipline is
        stated so it survives the next edit.
        """
        if not self._settings.phi_zone_required:
            return
        reasons = phi_zone_denial_reasons(self._impl.capabilities)
        if not reasons:
            return
        raise InferenceConfigError(
            "PHI zone required but provider capabilities are insufficient: provider "
            f"{self._settings.provider!r} fails {list(reasons)}. "
            "MAEZO_INFERENCE_PHI_ZONE_REQUIRED declares this deployment serves "
            "PHI-tagged traffic (ADR-0006/ADR-0017), which demands a provider that is "
            "phi_allowed, in a PHI-eligible region, zero-retention, barred from "
            "training on our input, and sanctioned up to the PHI data classification. "
            "Refusing to start — fail-closed, exactly like a missing credential; there "
            "is no silent degrade to the general zone."
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

    def _resolve_task_model(self, task_kind: str | None) -> str:
        """Resolve `task_kind` to the model id this call WILL use, loudly (AF-12, ADR-0009 §2).

        THE HONEST SHAPE OF THIS FUNCTION, stated before the code so it cannot be mistaken for
        more than it is: this repo configures exactly ONE model (``InferenceSettings.model`` /
        the provider default) and no per-tier model map exists anywhere in its configuration.
        So every tier — `fast`, `frontier`, whatever an agent declares — resolves to that single
        model. AF-12 asks for precisely that: fail closed to the single configured model, with an
        explicit log and metric, and NEVER a silent default to a DIFFERENT model. This function
        therefore ROUTES nothing today; it makes the non-routing visible and counts it.

        The day an owner supplies a real per-tier map, the branch that consumes it goes here and
        `TIER_RESOLUTIONS` already distinguishes the two cases in the same metric series. Which
        model each tier should name is an owner/finance decision (ADR-0009's "precos variam 10x")
        and is recorded in `docs/review-queue.md`, not guessed here.

        Returns the effective model id (or the empty string for a provider with no concrete
        model, e.g. noop) — returned rather than passed down because the concrete providers bind
        their model at construction; there is nothing to override while one model exists, and
        fabricating a per-call override would be the silent-different-model failure itself.
        """
        kind = task_kind or DEFAULT_TASK_KIND
        tier = self._model_tiers.get(kind, TIER_UNDECLARED) if self._model_tiers else TIER_NO_MAP
        # Same source as the public `model_id` accessor, deliberately: one answer to "which model
        # is this process using", not two that can disagree. Empty means "the concrete provider's
        # own default" — the honest value, never a fabricated id (that accessor's own rule).
        effective_model = self._settings.model
        logger.info(
            "llm_tier_resolved",
            task_kind=kind,
            tier=tier,
            resolution="modelo_unico",
            model=effective_model or "default",
            provider=self._settings.provider,
        )
        try:
            from maezo.platform.observability import record_llm_tier_resolution  # noqa: PLC0415

            record_llm_tier_resolution(task_kind=kind, tier=tier, resolution="modelo_unico")
        except Exception:  # noqa: BLE001 — telemetry must never break a generation.
            logger.debug("llm_tier_resolution_metric_failed", task_kind=kind, exc_info=True)
        return effective_model

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
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
            task_kind: AF-12 — which ADR-0009 §2 task kind this call is
                (``"task_default"`` | ``"reasoning"`` | ``"batch"``). ``None``
                means ``task_default``, which is what every ``agent.yaml``
                declares as the default. It selects a TIER through this
                provider's agent-declared ``model_tiers`` map and is recorded
                on ``maezo_llm_tier_resolution_total``.

                IT CANNOT CHANGE THE PROVIDER, AND THAT IS LOAD-BEARING: the
                ADR-0006 PHI-zone refusal below runs FIRST and reads only
                ``phi`` against the active provider's ``phi_capable``. No value
                of ``task_kind`` can route a PHI call to a non-PHI provider,
                move a call between providers, or soften the refusal —
                ``tests/unit/runtime/test_model_tiering.py`` proves it for
                every tier and both zones.

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
        # ORDER IS THE INVARIANT (I-6): the zone refusal happens BEFORE any tier is resolved, so
        # tiering can never be reached by a call the PHI zoning would have refused, and can never
        # participate in the decision to refuse.
        if phi and not self._impl.phi_capable:
            raise PhiZoneRoutingError(
                "PHI-tagged inference request cannot be served by provider "
                f"'{self._settings.provider}' — it is not PHI-capable. No "
                "BR-resident PHI-zone provider is configured. Route to a "
                "human / incident; NEVER falling back to the general cloud "
                "provider (ADR-0006/ADR-0017)."
            )
        self._resolve_task_model(task_kind)
        return await self._impl.generate(prompt, agent_id=agent_id, tenant_id=tenant_id)
