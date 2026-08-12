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
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

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
# Provider capability schema (Onda 2, audit W2 §2)
#
# `phi_capable: bool` answered ONE question ("may this provider see PHI?") and
# silently conflated the four independent facts that question actually depends
# on: WHERE inference runs, WHAT the vendor retains, WHETHER our input trains
# their model, and HOW HIGH a data classification the provider is sanctioned
# for. A single boolean cannot be audited against ADR-0006/ADR-0017 — it can
# only be trusted. This schema makes each fact separately declarable, separately
# false-ifiable, and separately checkable.
#
# CLOSED vocabularies where the domain is closed (region, retention,
# classification, credential source) — a free string would let a provider
# declare "brazil-ish" or "zero retention (mostly)" and pass a `==` check that
# means nothing. `supported_model_versions` stays an open set of ids because
# model ids genuinely are an open vocabulary.
# ---------------------------------------------------------------------------


class DeploymentRegion(StrEnum):
    """Where a provider's inference actually EXECUTES (ADR-0006 data residency)."""

    #: BR-resident inference (sa-east-1 / São Paulo) — the ADR-0006 PHI zone proper.
    #: No provider in this repo declares it yet: the real BR-resident endpoint is
    #: blocked(external) (T1.7 charter). Declared here so the vocabulary is complete
    #: BEFORE the adapter exists, not retrofitted around whatever gets built.
    BR_SAO_PAULO = "br-sao-paulo"

    #: Vendor-managed multi-region cloud with no BR-residency guarantee — the
    #: general zone (ADR-0006). Cross-border transfer is possible by construction.
    GLOBAL_MULTI_REGION = "global-multi-region"

    #: Executes entirely inside this Python process; nothing is transmitted anywhere.
    #: See `PHI_ELIGIBLE_REGIONS` for why this is residency-eligible and what stops it
    #: from being mistaken for a production PHI endpoint.
    LOCAL_NO_EGRESS = "local-no-egress"


class RetentionPolicy(StrEnum):
    """What the provider persists of request/response payloads."""

    #: Nothing is persisted past the request. Required for the PHI zone.
    ZERO_RETENTION = "zero-retention"

    #: The vendor's standard retention window applies. NOT zero — an explicit,
    #: honest "we retain", never a hopeful blank.
    VENDOR_DEFAULT = "vendor-default"

    #: No retention terms are contractually established in-repo. Treated exactly
    #: as harshly as VENDOR_DEFAULT: unknown is never assumed to be zero.
    UNSPECIFIED = "unspecified"


class DataClassification(StrEnum):
    """Highest sensitivity of data a provider is SANCTIONED to receive.

    Ordered least→most sensitive by :data:`_CLASSIFICATION_ORDER`; compare with
    :meth:`admits`, never with ``<``/``>`` (``StrEnum`` would compare the string
    values alphabetically, which is not the sensitivity order).
    """

    PUBLIC = "public"
    INTERNAL = "internal"

    #: LGPD "dado pessoal" that is not health data.
    PERSONAL = "personal"

    #: LGPD "dado pessoal sensível" (saúde) — the ADR-0006 PHI zone's data class.
    PHI = "phi"

    def admits(self, data: DataClassification) -> bool:
        """True if a provider capped at ``self`` may receive ``data``."""
        return _CLASSIFICATION_ORDER.index(self) >= _CLASSIFICATION_ORDER.index(data)


#: Sensitivity order, least→most. Completeness against ``DataClassification`` is
#: guarded by ``test_classification_order_covers_every_member`` — a member added
#: without a rank would otherwise raise ``ValueError`` deep inside ``admits()``.
_CLASSIFICATION_ORDER: Final[tuple[DataClassification, ...]] = (
    DataClassification.PUBLIC,
    DataClassification.INTERNAL,
    DataClassification.PERSONAL,
    DataClassification.PHI,
)


class CredentialSource(StrEnum):
    """Where the provider's credential comes from, if it needs one."""

    #: Needs no credential at all (in-process provider).
    NONE = "none"

    #: STRICTLY read from the process environment at construction, never from a
    #: settings object, never committed (see ``_ANTHROPIC_API_KEY_ENV_VARS``).
    ENVIRONMENT = "environment"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """An inference provider's self-declared, auditable capability set.

    Deliberately a PURE DESCRIPTION with NO validation in ``__post_init__``:
    every combination — including self-inconsistent ones like
    ``phi_allowed=True`` with ``retention_policy=VENDOR_DEFAULT`` — must remain
    CONSTRUCTIBLE. Policy lives exclusively in
    :func:`phi_zone_denial_reasons`. If the type refused to model an
    inconsistent declaration, the startup check against that declaration would
    be unreachable, i.e. vacuous, and could never be proven to fire.
    """

    #: Whether this provider is designated to serve the ADR-0006 PHI zone. NOT
    #: sufficient on its own — see :func:`phi_zone_denial_reasons`, which also
    #: requires the region/retention/training facts to line up. A provider
    #: asserting this while failing the others is a self-inconsistent
    #: declaration that fails startup, LOUDLY, rather than being believed.
    phi_allowed: bool

    deployment_region: DeploymentRegion
    retention_policy: RetentionPolicy

    #: True when the provider is contractually barred from training on our input.
    training_on_input_prohibited: bool

    max_data_classification: DataClassification
    credential_source: CredentialSource

    #: Model ids THIS REPO has declared for the provider — the sanctioned set, not
    #: the vendor's catalogue. DECLARED, NOT ENFORCED in this leg: enforcing a
    #: per-provider model allowlist is a routing decision that belongs with the
    #: real BR-zone adapter (later leg), and wiring it now would silently break
    #: `MAEZO_INFERENCE_MODEL` overrides. Empty for a provider with no real model.
    supported_model_versions: frozenset[str]


#: Regions in which PHI may lawfully be processed under ADR-0006/ADR-0017.
#:
#: `LOCAL_NO_EGRESS` is in this set on purpose, and it is the one entry that
#: deserves an argument. ADR-0006's residency requirement is about CROSS-BORDER
#: TRANSFER; a provider that transmits nothing performs no transfer, so it cannot
#: violate residency. That is what makes today's `phi_zone_mock` a legitimate way
#: to exercise the PHI seam end-to-end. What stops it from being mistaken for a
#: production PHI endpoint is NOT this set — it is `is_mock=True`, the loud
#: `health_check()` warning, and the "[SYNTHETIC RESPONSE …]" prefix on every
#: completion. Residency eligibility and production readiness are different
#: claims, and this constant only makes the first one.
#:
#: `GLOBAL_MULTI_REGION` is deliberately ABSENT: that is the general zone.
PHI_ELIGIBLE_REGIONS: Final[frozenset[DeploymentRegion]] = frozenset(
    {DeploymentRegion.BR_SAO_PAULO, DeploymentRegion.LOCAL_NO_EGRESS}
)

#: Stable denial reason codes returned by :func:`phi_zone_denial_reasons`.
#: These are part of the module's contract — tests and operators match on them,
#: so they are enum-shaped constants, never prose that may be reworded. They
#: carry NO request/response content: each names a DECLARED CAPABILITY that
#: failed, never the data that would have been sent.
PHI_DENIAL_NOT_ALLOWED: Final[str] = "phi_allowed=False"
PHI_DENIAL_REGION: Final[str] = "deployment_region_not_phi_eligible"
PHI_DENIAL_RETENTION: Final[str] = "retention_policy_not_zero_retention"
PHI_DENIAL_TRAINING: Final[str] = "training_on_input_not_prohibited"
PHI_DENIAL_CLASSIFICATION: Final[str] = "max_data_classification_below_phi"


def phi_zone_denial_reasons(capabilities: ProviderCapabilities) -> tuple[str, ...]:
    """Every reason ``capabilities`` may NOT serve the ADR-0006 PHI zone.

    Empty tuple == eligible. Reasons come back in a FIXED order (declaration
    order below), so an error message is deterministic and diffable.

    ALL FOUR facts are checked independently and none short-circuits another:
    a provider that declares ``phi_allowed=True`` but retains payloads is
    refused on retention, and a provider with impeccable retention that never
    declared ``phi_allowed`` is still refused. That independence is the whole
    point of replacing the boolean — see the module-level schema comment.
    """
    reasons: list[str] = []
    if not capabilities.phi_allowed:
        reasons.append(PHI_DENIAL_NOT_ALLOWED)
    if capabilities.deployment_region not in PHI_ELIGIBLE_REGIONS:
        reasons.append(PHI_DENIAL_REGION)
    if capabilities.retention_policy is not RetentionPolicy.ZERO_RETENTION:
        reasons.append(PHI_DENIAL_RETENTION)
    if not capabilities.training_on_input_prohibited:
        reasons.append(PHI_DENIAL_TRAINING)
    if not capabilities.max_data_classification.admits(DataClassification.PHI):
        reasons.append(PHI_DENIAL_CLASSIFICATION)
    return tuple(reasons)


# --- Per-provider declarations ---------------------------------------------
# Each constant is the single source of truth for its provider: the class's
# `phi_capable` ClassVar is DERIVED from `.phi_allowed` below, and
# `BaseInferenceProvider.__init_subclass__` refuses at class-creation time to
# let the two drift apart.

#: `noop` — in-process deterministic mock. Note it satisfies EVERY PHI facet
#: except `phi_allowed`: nothing is transmitted, nothing is retained, nothing is
#: trained on. It is still refused for PHI, which is the point — a provider does
#: not become PHI-eligible by being harmless, only by being DESIGNATED.
#: (`NoopInferenceProvider` docstring: "Never PHI-capable … not silently
#: absorbed by noop".)
NOOP_CAPABILITIES: Final[ProviderCapabilities] = ProviderCapabilities(
    phi_allowed=False,
    deployment_region=DeploymentRegion.LOCAL_NO_EGRESS,
    retention_policy=RetentionPolicy.ZERO_RETENTION,
    training_on_input_prohibited=True,
    max_data_classification=DataClassification.INTERNAL,
    credential_source=CredentialSource.NONE,
    supported_model_versions=frozenset(),
)

#: `anthropic` — the GENERAL-ZONE cloud provider (class docstring: "General-zone
#: cloud provider (ADR-0006) — never PHI-capable"). Retention is declared
#: UNSPECIFIED rather than ZERO_RETENTION because no zero-retention agreement
#: exists anywhere in this repo's tree; declaring one would be fabricating a
#: contract. `training_on_input_prohibited` is False for the same reason — not a
#: claim that training happens, a refusal to assert a prohibition nobody signed
#: here. `max_data_classification` is INTERNAL: ADR-0006 puts "dado pessoal
#: sensível" in the PHI zone, and nothing in-repo sanctions PERSONAL for the
#: general zone either, so INTERNAL is the conservative floor pending the
#: MZO-040 Medical/ANS review.
ANTHROPIC_CAPABILITIES: Final[ProviderCapabilities] = ProviderCapabilities(
    phi_allowed=False,
    deployment_region=DeploymentRegion.GLOBAL_MULTI_REGION,
    retention_policy=RetentionPolicy.UNSPECIFIED,
    training_on_input_prohibited=False,
    max_data_classification=DataClassification.INTERNAL,
    credential_source=CredentialSource.ENVIRONMENT,
    supported_model_versions=frozenset({DEFAULT_ANTHROPIC_MODEL}),
)

#: `phi_zone_mock` — explicitly-labeled in-process stand-in for the
#: not-yet-provisioned BR-resident endpoint (`PhiZoneMockProvider` docstring).
#: It is the ONLY provider that satisfies `phi_zone_denial_reasons`, and it does
#: so honestly: `LOCAL_NO_EGRESS` (not a fabricated `BR_SAO_PAULO`), and
#: `supported_model_versions=frozenset()` because it runs no model at all. Its
#: mock-ness stays visible through `is_mock=True`, NOT through the capability
#: set — capabilities describe the zone contract, `is_mock` describes whether
#: the output is real (constraint 3). Both are asserted per-provider in
#: `tests/unit/runtime/test_inference_capabilities.py`.
PHI_ZONE_MOCK_CAPABILITIES: Final[ProviderCapabilities] = ProviderCapabilities(
    phi_allowed=True,
    deployment_region=DeploymentRegion.LOCAL_NO_EGRESS,
    retention_policy=RetentionPolicy.ZERO_RETENTION,
    training_on_input_prohibited=True,
    max_data_classification=DataClassification.PHI,
    credential_source=CredentialSource.NONE,
    supported_model_versions=frozenset(),
)


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

    #: MAEZO_INFERENCE_PHI_ZONE_REQUIRED — declares that THIS deployment is
    #: expected to serve PHI-tagged traffic (ADR-0006 PHI zone).
    #:
    #: Default False, so this whole check is INERT until a deployment opts in;
    #: nothing about today's general-zone behaviour changes. When True,
    #: :class:`InferenceProvider` validates the configured provider's
    #: :class:`ProviderCapabilities` AT CONSTRUCTION and refuses to start on any
    #: shortfall — deliberately the same posture, error type and moment as a
    #: missing API key. A deployment that intends to handle PHI and is wired to a
    #: general-zone provider is a misconfiguration, and it must be caught at
    #: startup rather than at the first PHI request in production.
    #:
    #: This does NOT replace, gate or feed the per-call ``phi=True`` refusal in
    #: :meth:`InferenceProvider.generate` (invariant I-6). That raise reads
    #: ``phi_capable`` and fires regardless of this flag; this flag only adds an
    #: EARLIER, louder failure for a deployment that declared its intent up front.
    phi_zone_required: bool = False


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
           supposed to eliminate. Raising here makes the drift unrepresentable
           rather than merely tested-for.
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
            InferenceConfigError: unknown provider; a real provider missing
                required configuration (e.g. no API key); or
                ``phi_zone_required`` set against a provider whose
                :class:`ProviderCapabilities` do not satisfy the ADR-0006 PHI
                contract (see :meth:`_assert_phi_zone_capability`).
        """
        self._settings = settings or InferenceSettings()
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
