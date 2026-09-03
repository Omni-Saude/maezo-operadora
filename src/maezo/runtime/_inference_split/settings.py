"""Inference settings (D2-02 split, step 2/8 — constants; the ``BaseSettings`` classes land in
step 3, added to this SAME file rather than moved a second time).

Moved verbatim out of ``maezo/runtime/inference.py`` — cut-and-paste, never a redefinition
(``docs/reports/inference-split-plan.md`` §5). These 4 default/precedence constants are pulled
forward from the plan's step 3 into this step, ahead of the ``InferenceSettings``/
``BedrockSettings`` classes that will join them here, because :mod:`capabilities` (step 2's other
new module) reads :data:`DEFAULT_ANTHROPIC_MODEL`/:data:`DEFAULT_BEDROCK_MODEL` to declare each
provider's ``supported_model_versions`` — a real forward dependency the plan's §4 table did not
carry (written against an earlier tree state; reconfirmed this session with ``grep -n`` before
acting on it, not copied from the plan without reproduction). Keeping ``capabilities`` importing
FROM this module (never the reverse) avoids a cycle with ``runtime/inference.py`` in the interim.
"""

from __future__ import annotations

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
