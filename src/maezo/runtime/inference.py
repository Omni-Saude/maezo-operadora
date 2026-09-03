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

from collections.abc import Callable, Mapping
from typing import Final

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
    br_endpoint_denial_reasons,
    resolve_br_regional_transport,
)
from maezo.runtime._inference_split.br_resident_provider import BrResidentInferenceProvider
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
from maezo.runtime._inference_split.providers import (
    AnthropicInferenceProvider,
    BaseInferenceProvider,
    BedrockInferenceProvider,
    NoopInferenceProvider,
    PhiZoneMockProvider,
)
from maezo.runtime._inference_split.retry_budget import (
    RETRY_STOP_ATTEMPTS_EXHAUSTED,
    RETRY_STOP_COMMITTED,
    RETRY_STOP_NOT_RETRYABLE,
    RETRY_STOP_RATE_BUDGET_EXHAUSTED,
    RetryBudget,
    retry_denial_reason,
)
from maezo.runtime._inference_split.settings import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_BEDROCK_MODEL,
    DEFAULT_BEDROCK_REGION,
    BedrockSettings,
    InferenceSettings,
)

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
