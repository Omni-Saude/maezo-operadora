"""Inference settings (D2-02 split, steps 2-3/8).

Moved verbatim out of ``maezo/runtime/inference.py`` — cut-and-paste, never a redefinition
(``docs/reports/inference-split-plan.md`` §5). Step 2 landed the 4 default/precedence constants
here ahead of schedule (pulled forward from the plan's step 3) because :mod:`capabilities` reads
:data:`DEFAULT_ANTHROPIC_MODEL`/:data:`DEFAULT_BEDROCK_MODEL` to declare each provider's
``supported_model_versions`` — a real forward dependency the plan's §4 table did not carry
(reconfirmed this session with ``ruff check``, not assumed). Step 3 (this addition) lands
:class:`InferenceSettings`/:class:`BedrockSettings` in the SAME file, per the plan's original
step-3 scope — the pydantic-settings configuration for the ``noop``/``anthropic``/``bedrock``
providers.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings

#: Default Anthropic model id used when neither MAEZO_ANTHROPIC_MODEL nor
#: MAEZO_INFERENCE_MODEL is set. Overridable — see AnthropicInferenceProvider.
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"

#: Env vars consulted for the Anthropic API key, in precedence order.
#: MAEZO_ANTHROPIC_API_KEY (repo-namespaced, preferred) wins over the
#: SDK-conventional ANTHROPIC_API_KEY. Neither is ever read from
#: InferenceSettings / pydantic-settings — credentials are STRICTLY
#: environment-sourced and never committed.
_ANTHROPIC_API_KEY_ENV_VARS = ("MAEZO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")

#: Default Bedrock model id (``MAEZO_BEDROCK_MODEL_ID`` overrides — see
#: :class:`BedrockInferenceProvider` for the full precedence).
#:
#: NEITHER PREFIX IS DECORATION, and the id is NOT the first-party one. Two layers:
#:
#:  * ``anthropic.`` — Bedrock namespaces third-party models by provider, so the first-party id
#:    (``claude-opus-5``, of which :data:`DEFAULT_ANTHROPIC_MODEL` holds the 4.8 spelling) is not
#:    a valid Bedrock id at all.
#:  * ``global.`` — this is an INFERENCE PROFILE id, not a bare foundation-model id. THIS ACCOUNT
#:    SERVES CLAUDE ONLY THROUGH ``global.*`` PROFILES, established by a live probe against the
#:    real session (profile ``amh-data-dev``, ``sa-east-1``, 2026-08-12), not by reading docs:
#:    ``bedrock-runtime converse --model-id global.anthropic.claude-opus-5`` SUCCEEDED
#:    (``end_turn``, 179 output tokens, genuine Opus-5 output), while the bare
#:    ``anthropic.claude-opus-5`` returned 404 "The model … does not exist" (request reached the
#:    endpoint, SigV4 verified — request_id ``req_wg2absqs…``).
#:
#: CONSEQUENCE THAT IS NOT COSMETIC: a ``global.*`` profile ROUTES CROSS-REGION BY CONSTRUCTION —
#: which is the first, factual reason :data:`BEDROCK_CAPABILITIES` declares
#: ``GLOBAL_MULTI_REGION`` rather than ``BR_SAO_PAULO``. Sanctioned for the GENERAL (pseudonymized)
#: zone only; the cross-border transfer it implies is an owner-level LGPD consideration for
#: production and is recorded as such in ``docs/runbooks/phi-inference-ops.md``.
#:
#: This constant and :data:`DEFAULT_ANTHROPIC_MODEL` are deliberately NOT derived from one
#: another: they name ids in two different catalogues, and a "DRY" edit computing one from the
#: other would silently produce an invalid id the moment either catalogue moved.
DEFAULT_BEDROCK_MODEL = "global.anthropic.claude-opus-5"

#: Default AWS region for the Bedrock endpoint (``MAEZO_BEDROCK_REGION`` overrides).
#:
#: ``sa-east-1`` (São Paulo) is the operational default because it is the region closest to this
#: platform's users, and it is the region the live probe above ran in. IT IS NOT A RESIDENCY
#: GUARANTEE — with a ``global.*`` inference profile it is the region the REQUEST is signed for,
#: not necessarily the region inference EXECUTES in. :data:`BEDROCK_CAPABILITIES` therefore does
#: NOT declare ``BR_SAO_PAULO`` on the strength of it; see that constant's comment.
DEFAULT_BEDROCK_REGION = "sa-east-1"


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


class BedrockSettings(BaseSettings):
    """Bedrock-specific configuration. Environment variables prefixed ``MAEZO_BEDROCK_``.

    A SEPARATE settings class rather than two more fields on :class:`InferenceSettings`,
    because these are provider-specific knobs and folding them in would spell them
    ``MAEZO_INFERENCE_BEDROCK_*`` — implying every provider reads them.

    Note what is NOT here, for the same reason :class:`InferenceSettings` has no API key
    field: no credential. Bedrock authenticates via SigV4 through the standard AWS
    credential chain, resolved by botocore at request time; nothing credential-shaped ever
    round-trips through a settings object that might be logged or serialized.
    """

    # ``protected_namespaces=()`` is REQUIRED, not cosmetic: pydantic v2 reserves the
    # ``model_`` prefix for its own API, and a field named ``model_id`` emits a
    # ``UserWarning`` at class-creation time without this. The name is fixed by the
    # operator-facing env var (``MAEZO_BEDROCK_MODEL_ID``), so the setting yields.
    model_config = {"env_prefix": "MAEZO_BEDROCK_", "extra": "ignore", "protected_namespaces": ()}

    #: ``MAEZO_BEDROCK_MODEL_ID``. Empty == fall back to :data:`DEFAULT_BEDROCK_MODEL`.
    model_id: str = ""

    #: ``MAEZO_BEDROCK_REGION``. An EXPLICITLY EMPTY value is not silently replaced by the
    #: default — :class:`BedrockInferenceProvider` refuses to construct, because an operator
    #: who blanked the region asked a question this module must not answer by guessing.
    region: str = DEFAULT_BEDROCK_REGION
