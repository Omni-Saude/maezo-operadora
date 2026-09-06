"""BR-resident, zero-retention PHI-zone provider (D2-02 split, step 7/8).

Moved verbatim out of ``maezo/runtime/inference.py`` (Onda 2 W2 leg 2) — cut-and-paste, never a
redefinition (``docs/reports/inference-split-plan.md`` §5 step 7). Same fence posture as step 6:
``BrResidentInferenceProvider`` is never a chokepoint-fenced construction NAME
(``FORBIDDEN_CONSTRUCTION_NAMES`` only lists ``AnthropicInferenceProvider``,
``BedrockInferenceProvider`` and ``InferenceProvider`` — confirmed by grep this session, not
assumed), so this move needs no `scripts/ci/check_effect_chokepoint_fence.py` edit; `_build_br_resident`/
`_build_bedrock_br` (the calls) stay in `runtime/inference.py` until step 8.

D2-02 SPLIT NOTE for any future test edit here: as with step 6's logger finding, code that resolves
a free variable (``logger``, or any symbol imported into this module) does so from THIS module's
own globals, not from wherever it is re-exported. A test that monkeypatches
``"maezo.runtime.inference.br_endpoint_denial_reasons"`` or an attribute on the
``maezo.runtime.inference`` module object to intercept this class's behaviour must target THIS
module instead — fixed in the same commit for the occurrences this move broke (see the commit body).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import ClassVar
from urllib.parse import urlsplit

import structlog

from maezo.runtime.inference.br_regional import (
    BR_REGIONAL_ATTESTED_REGION,
    BR_REGIONAL_ENDPOINT_HOST_SUFFIXES,
    BR_REGIONAL_ENDPOINT_SCHEME,
    ENV_PHI_API_KEY,
    ENV_PHI_ENDPOINT_URL,
    ENV_PHI_VENDOR_DPA_REF,
    HEADER_CACHE_PREFIX_CHARS,
    HEADER_DATA_CLASSIFICATION,
    HEADER_TRAINING_PROHIBITED,
    HEADER_VENDOR_DPA_REF,
    HEADER_ZERO_RETENTION,
    BrRegionalRequest,
    BrRegionalResponse,
    BrRegionalTransport,
    RefusingBrRegionalTransport,
    _fingerprint,
    br_endpoint_denial_reasons,
    resolve_br_regional_transport,
)
from maezo.runtime.inference.capabilities import (
    BR_RESIDENT_CAPABILITIES,
    DataClassification,
    ProviderCapabilities,
)
from maezo.runtime.inference.errors import (
    BrEndpointNotApprovedError,
    BrRegionalTransportUnavailableError,
    InferenceConfigError,
    InferenceProviderError,
)
from maezo.runtime.inference.providers import BaseInferenceProvider, _emit_llm_token_usage
from maezo.runtime.inference.retry_budget import (
    RETRY_STOP_RATE_BUDGET_EXHAUSTED,
    RetryBudget,
    _backoff_delay,
    _RetryTokenBucket,
    retry_denial_reason,
)
from maezo.runtime.prompt_format import FormattedPrompt

logger = structlog.get_logger(__name__)


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
        except Exception as exc:  # no transport-level type may leak past this module.
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
