"""BR-resident, zero-retention PHI-zone transport contract (D2-02 split, step 5/8).

Moved verbatim out of ``maezo/runtime/inference.py`` (Onda 2 W2 leg 2) — cut-and-paste, never a
redefinition (``docs/reports/inference-split-plan.md`` §5 step 5). ``BrRegionalTransportUnavailableError``
itself already moved to :mod:`errors` in step 1 (it is the one class of this section the two
CODEOWNED chokepoint-fence dictionaries never reference, since it is an error type, not a
constructed effect class — its subclasses of :class:`InferenceProviderError` are unaffected by
this move). ``BedrockBrRegionalTransport`` is not in ``FORBIDDEN_CONSTRUCTION_NAMES`` today
(confirmed by grep this session, matching the plan's own §5 step-5 note), so this move needs no
chokepoint-fence update.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Final, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

import structlog

from maezo.runtime.inference.capabilities import DeploymentRegion
from maezo.runtime.inference.errors import BrRegionalTransportUnavailableError

logger = structlog.get_logger(__name__)

# ===========================================================================
# BR-resident PHI-zone adapter (Onda 2, W2 leg 2) — BUILT INERT
#
# An HTTP-CONTRACT adapter, NOT a vendor SDK integration. No vendor SDK for a
# BR-resident PHI endpoint exists to integrate against, and picking one would
# commit this repo to a counterparty nobody has chosen. What DOES exist to
# build is the wire contract such an endpoint must satisfy — request shape,
# the zero-retention/no-training flags, the residency attestation the response
# must carry, and the token fields leg 3 reconciles against. That contract is
# expressed below as an injectable transport seam, following this repo's
# established Protocol + refusing-real + labeled-fake triple
# (`tools/workers/ans_gateway.py`, `dmn_transport.DmnTransport`).
#
# WHY A SEAM AND NOT AN httpx CLIENT HERE. Onda 1 design §8.2 fences raw httpx
# client construction to five sanctioned transport modules;
# `runtime/inference.py` is deliberately NOT one of them
# (`scripts/ci/check_effect_chokepoint_fence.py:_HTTPX_DESIGN_MODULES`). This
# is not an obstacle worked around — it is the correct answer. The socket-owning
# half of a PHI transport belongs in a fenced transport module, wired through
# the gateway registry, and putting it there is a human decision with a network
# proof attached (ADR-0017 NetworkPolicy), not something this leg may grant
# itself. So the adapter owns the CONTRACT and the REFUSALS; it never opens a
# connection. `resolve_br_regional_transport(None)` yields the REFUSING
# transport, so an unwired adapter refuses rather than falling through to the
# fake — the same fail-closed default as `resolve_ans_gateway`.
# ===========================================================================

#: Host suffixes an inference endpoint must match to be considered BR-resident.
#:
#: PROVISIONAL AND DELIBERATELY NON-ROUTABLE. No vendor endpoint has been chosen, so this is not
#: a redaction of a real one — `.internal` is a private-use suffix that resolves nowhere on the
#: public internet. The consequence is a useful one and is the reason for the choice: even a
#: deployment that somehow satisfied all three owner gates could not reach a public vendor
#: through this allowlist. Widening it to a real hostname is an OWNER act that travels with the
#: DPA and the ADR-0017 NetworkPolicy, not an adapter edit.
#:
#: OPEN QUESTION FOR HUMANS (reported, not resolved here): the source of truth for this list.
#: A client-side tuple is the weakest place for it — it should plausibly be derived from the
#: same artifact that pins the NetworkPolicy egress CIDRs, so the client allowlist and the
#: network fence cannot drift apart. This leg does not invent that artifact.
BR_REGIONAL_ENDPOINT_HOST_SUFFIXES: Final[tuple[str, ...]] = (
    ".br-sao-paulo.phi.maezo.internal",
    # Existing configured AWS regional endpoint. URL admission is not execution-location
    # evidence or a new owner approval. Bedrock additionally checks the exact resolved
    # origin/region and binds model selection to a regional foundation-model ARN before
    # dispatch; cross-region profiles include geographic and application forms, not only
    # global.*. The contractual basis is reported separately from vendor response evidence.
    "bedrock-runtime.sa-east-1.amazonaws.com",
)

#: The ONLY scheme an approved endpoint may use. Plaintext `http` for PHI in transit is refused
#: structurally rather than left to deployment configuration.
BR_REGIONAL_ENDPOINT_SCHEME: Final[str] = "https"

#: Flags on the adapter transport-seam request. A generic HTTP transport can require
#: vendor echoes; Bedrock consumes the operator reference locally because Converse
#: neither sends these custom headers nor returns those echoes. Response evidence
#: sources distinguish those contracts. These are Maezo assertions, not AWS standards.
HEADER_ZERO_RETENTION: Final[str] = "X-Maezo-Zero-Retention"
HEADER_TRAINING_PROHIBITED: Final[str] = "X-Maezo-Training-Prohibited"
HEADER_DATA_CLASSIFICATION: Final[str] = "X-Maezo-Data-Classification"
HEADER_VENDOR_DPA_REF: Final[str] = "X-Maezo-Vendor-Dpa-Ref"
HEADER_CACHE_PREFIX_CHARS: Final[str] = "X-Maezo-Cache-Prefix-Chars"

#: Region eligibility value checked by the provider; evidence source is explicit (W8/ADR-0006).
BR_REGIONAL_ATTESTED_REGION: Final[str] = DeploymentRegion.BR_SAO_PAULO.value

#: The three OWNER ACTS that gate a BR-resident boot, read STRICTLY from the process environment.
#:
#: Grouped here rather than split across `InferenceSettings` on purpose. One of them is a
#: credential (leg-1 precedent: a credential never round-trips through a settings object that
#: might be logged or serialized), and the other two are the same KIND of fact — something a
#: human with authority must supply — so a single read point lets the refusal name exactly which
#: act is missing instead of surfacing as three unrelated config errors.
ENV_PHI_ENDPOINT_URL: Final[str] = "MAEZO_PHI_ENDPOINT_URL"
ENV_PHI_API_KEY: Final[str] = "MAEZO_PHI_API_KEY"
ENV_PHI_VENDOR_DPA_REF: Final[str] = "MAEZO_PHI_VENDOR_DPA_REF"

#: Stable reason codes for an endpoint rejected by the client-side allowlist. Enum-shaped for the
#: same reason as `PHI_DENIAL_*`: operators and tests match on them, so they must not be prose.
#: Each names a URL STRUCTURE fact — never the URL itself, which could carry a tenant hint.
ENDPOINT_DENIAL_EMPTY: Final[str] = "endpoint_url_not_configured"

#: The STORED URL is not what `urlsplit`+`urlunsplit` would produce from it — i.e. it carries
#: characters this module's own parse silently drops or rewrites. The load-bearing case (LEG3-A,
#: deferred from leg 2 to the leg-3 canary) is an INTERIOR control character: `MAEZO_PHI_ENDPOINT_URL`
#: is only `.strip()`-ed before storage, so a `\r`/`\n`/`\t` in the MIDDLE of the URL survives into
#: `self._endpoint_url` while `urlsplit` quietly removes it for the host parse — the host check then
#: passes on a sanitized string that is NOT the one a transport would put on the wire, and the CRLF
#: rides along into request-line / header-injection territory. Refused BEFORE any host/scheme fact is
#: trusted (early return below), because those facts are derived from the sanitized parse and would
#: be lying about the stored string. Also catches a non-canonical scheme case (`HTTPS://`), which is
#: the same "stored form ≠ normalized form" defect and equally safe to refuse.
ENDPOINT_DENIAL_NOT_NORMALIZED: Final[str] = "endpoint_url_not_urlsplit_normalized"

ENDPOINT_DENIAL_SCHEME: Final[str] = "endpoint_scheme_not_https"
ENDPOINT_DENIAL_USERINFO: Final[str] = "endpoint_url_carries_userinfo"
ENDPOINT_DENIAL_HOST: Final[str] = "endpoint_host_not_br_regional"
ENDPOINT_DENIAL_QUERY: Final[str] = "endpoint_url_carries_query_or_fragment"


def br_endpoint_denial_reasons(endpoint_url: str) -> tuple[str, ...]:
    """Every reason ``endpoint_url`` is not an approved BR-regional inference endpoint.

    Empty tuple == approved. Fixed declaration order, so a refusal message is deterministic and
    diffable — same discipline as :func:`phi_zone_denial_reasons`.

    PURE and side-effect-free: no DNS, no connection, no logging. It decides from the URL's
    STRUCTURE alone, which is what makes it usable both at construction (before any credential
    is exercised) and on the hot path of every call, and what makes it honest about its own
    limits — it proves a URL is well-formed and on the allowlist, never that whatever answers
    there is genuinely in São Paulo. That second claim needs the network-level proof ADR-0017
    and the leg-3 canary own.

    NORMALIZATION IS CHECKED FIRST, and it SHORT-CIRCUITS, because every other check below reads
    ``urlsplit``'s output — and ``urlsplit`` silently strips interior control characters (LEG3-A).
    A URL whose stored form differs from ``urlunsplit(urlsplit(...))`` is therefore one whose
    host/scheme facts would be parsed from a SANITIZED string that is not what a transport would
    dial. Returning host/scheme reasons for such a URL would be reporting facts about a string the
    adapter never stores; the honest answer is a single "this URL is not what we parsed" refusal.
    """
    stripped = endpoint_url.strip()
    if not stripped:
        return (ENDPOINT_DENIAL_EMPTY,)
    # LEG3-A: refuse before trusting any structural fact parsed from a sanitized string.
    if stripped != urlunsplit(urlsplit(stripped)):
        return (ENDPOINT_DENIAL_NOT_NORMALIZED,)

    reasons: list[str] = []
    parts = urlsplit(stripped)
    if parts.scheme != BR_REGIONAL_ENDPOINT_SCHEME:
        reasons.append(ENDPOINT_DENIAL_SCHEME)
    # `urlsplit` keeps userinfo in `netloc` but strips it from `hostname`; a credential smuggled
    # into the URL would otherwise pass the host check AND land in every log line that echoes it.
    if "@" in parts.netloc:
        reasons.append(ENDPOINT_DENIAL_USERINFO)
    hostname = (parts.hostname or "").lower()
    if not any(hostname.endswith(suffix) for suffix in BR_REGIONAL_ENDPOINT_HOST_SUFFIXES):
        reasons.append(ENDPOINT_DENIAL_HOST)
    if parts.query or parts.fragment:
        reasons.append(ENDPOINT_DENIAL_QUERY)
    return tuple(reasons)


def _fingerprint(text: str) -> str:
    """Short, stable, NON-REVERSIBLE correlation handle for prompt/completion text.

    The only thing this module ever derives from PHI-bearing content. Truncated SHA-256: enough
    to correlate "the same prompt" across two log lines, useless for recovering the prompt.
    Never a preview, never a prefix of the text itself (`NoopInferenceProvider` logs
    `prompt[:80]`; that is fine for a mock that never sees production PHI, and is exactly what
    this provider must not do).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class BrRegionalTokenUsage:
    """Token accounting from a BR-regional response.

    Field names ``input_tokens``/``output_tokens`` are NOT arbitrary: they match the shape
    :func:`_emit_llm_token_usage` already reads off the Anthropic SDK, so BR-resident traffic
    meters through the SAME single seam as general-zone traffic instead of growing a parallel
    metering path (T8: "never a parallel logging/telemetry system").

    ``cached_prefix_tokens`` is the W8 payoff and has no Anthropic-shape counterpart here: it is
    how many of ``input_tokens`` the vendor served from a cached stable prefix. Leg 3 reconciles
    it — a cache-aware prompt layout that never produces a non-zero value is a layout that is not
    actually being cached, and this field is what makes that falsifiable rather than assumed.
    """

    input_tokens: int
    output_tokens: int
    cached_prefix_tokens: int


@dataclass(frozen=True, slots=True, repr=False)
class BrRegionalRequest:
    """One request on the BR-regional inference wire contract.

    THE REPR REDACTS, and the two mechanisms below do DIFFERENT jobs — stated precisely because
    the obvious reading of this decorator line is wrong. This object holds BOTH a credential and
    PHI-bearing prompt text, and a dataclass's generated repr spills both verbatim into any log
    line, ``assert`` message, traceback frame or debugger session that touches it (verified: with
    both mechanisms removed, ``repr()`` renders the credential and the full prompt in plaintext).

    * The hand-written :meth:`__repr__` is what actually redacts. It wins ON ITS OWN, with or
      without ``repr=False``: ``dataclasses`` installs its generated ``__repr__`` via
      ``_set_new_attribute``, which declines to overwrite a name already present in the class
      body. Deleting this method is therefore the ONLY edit that can un-redact this class.
    * ``repr=False`` is the FAILSAFE for exactly that edit. With it, deleting the method degrades
      to ``object.__repr__`` — type and address, no fields. Without it, the same deletion would
      silently restore a field-dumping repr. It buys nothing today and everything on the day
      somebody removes the method below.

    Both are asserted in ``tests/unit/runtime/test_inference_br_resident.py``.
    """

    endpoint_url: str
    model: str

    #: The cache boundary (W8). Split rather than concatenated so the transport can declare a
    #: provider-side cache breakpoint at exactly ``len(stable_prefix)``; the model still receives
    #: ``stable_prefix + variable_suffix``, unchanged (see `runtime/prompt_format.py`).
    stable_prefix: str
    variable_suffix: str

    max_tokens: int

    #: Bearer credential. Present because a real HTTP transport needs it on the wire; kept out of
    #: the repr, out of every log line, and out of every error message this module raises.
    credential: str

    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        """The full prompt as the model receives it."""
        return self.stable_prefix + self.variable_suffix

    def __repr__(self) -> str:
        """Counts, enums and a fingerprint. No credential, no prompt bytes, no endpoint path."""
        return (
            f"BrRegionalRequest(endpoint_host={urlsplit(self.endpoint_url).hostname!r}, "
            f"model={self.model!r}, stable_prefix_chars={len(self.stable_prefix)}, "
            f"variable_suffix_chars={len(self.variable_suffix)}, max_tokens={self.max_tokens}, "
            f"prompt_fingerprint={_fingerprint(self.prompt)!r}, "
            f"credential=<redacted>, header_names={sorted(self.headers)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class BrRegionalResponse:
    """One response on the BR-regional inference wire contract.

    The existing eligibility fields remain checked by the provider. Their provenance
    is explicit: a generic transport can report vendor-response assertions, while
    Bedrock exposes a resolved SDK endpoint and a direct regional resource contract.
    Converse does NOT return a per-response execution region or retention/training
    acknowledgements. Its region eligibility is therefore an inference from the
    admitted request routing contract, never a vendor-observed execution location;
    retention/training acknowledgements derive from the operator contract reference.
    These sources do not establish model availability, vendor/legal approval or
    network-level execution evidence. Synthetic transports label their evidence.

    Repr redacts for the same reason as the request — ``completion`` is model output over PHI —
    and by the same two mechanisms, with the same division of labour (see
    :class:`BrRegionalRequest`: the hand-written method redacts, ``repr=False`` is the failsafe
    for the day it is deleted).
    """

    completion: str
    model: str
    usage: BrRegionalTokenUsage

    #: Endpoint checked by the provider; see endpoint_evidence_source for its provenance.
    endpoint_url: str

    #: Region eligibility assertion, whose basis is region_evidence_source.
    served_region: str

    zero_retention_acknowledged: bool
    training_prohibited_acknowledged: bool

    #: True when this response came from a labeled fake. A real transport must NEVER set it, and
    #: the adapter logs loudly when it sees it — the `is_mock`/`synthetic` discipline of
    #: `ans_gateway.AnsProtocol` (constraint 3: a synthetic result stays self-describing at the
    #: process boundary).
    synthetic: bool = False

    #: A vendor refusal (safety classifier, policy). Carries a CODE, never the refused content.
    refusal_code: str = ""

    # Defaults preserve the generic vendor-response contract; concrete SDK/fake transports
    # must identify their weaker/different evidence instead of claiming an observed echo.
    region_evidence_source: str = "vendor_response"
    endpoint_evidence_source: str = "vendor_response"
    retention_evidence_source: str = "vendor_response"

    def __repr__(self) -> str:
        """Counts, enums and a fingerprint. No completion bytes."""
        return (
            f"BrRegionalResponse(model={self.model!r}, "
            f"completion_chars={len(self.completion)}, "
            f"completion_fingerprint={_fingerprint(self.completion)!r}, "
            f"served_region={self.served_region!r}, "
            f"region_evidence_source={self.region_evidence_source!r}, "
            f"endpoint_evidence_source={self.endpoint_evidence_source!r}, "
            f"retention_evidence_source={self.retention_evidence_source!r}, "
            f"zero_retention_acknowledged={self.zero_retention_acknowledged}, "
            f"training_prohibited_acknowledged={self.training_prohibited_acknowledged}, "
            f"synthetic={self.synthetic}, refusal_code={self.refusal_code!r})"
        )


@runtime_checkable
class BrRegionalTransport(Protocol):
    """The BR-regional inference wire seam — Protocol half of the repo's transport triple.

    ASYNC, unlike `AnsGatewayTransport.submit`: every caller is
    :meth:`BrResidentInferenceProvider.generate`, which is already async, and a real
    implementation performs network I/O — so there is no reason to build in a sync-to-async
    bridge that would have to be removed later.

    NO OUTCOME-STEERING PARAMETER, deliberately, and this is a TIGHTENING of the
    `AnsGatewayTransport` precedent rather than a copy of it. That Protocol carries a
    dev/test-only ``requested_outcome`` argument which every production implementation must
    remember to ignore — a discipline enforced by docstring. Here the failure modes a test needs
    live in :class:`LabeledFakeBrRegionalTransport`'s CONSTRUCTOR instead, so no caller can ask
    ANY transport for a particular outcome: the contract simply has no channel for it.
    """

    async def send(self, request: BrRegionalRequest) -> BrRegionalResponse: ...


# Closed foundation-model resource grammar from AWS GetFoundationModel. Converse
# accepts foundation-model ARNs; profiles, routers and other resource kinds remain
# outside this transport contract. Passing the qualified ARN (even for short IDs)
# prevents an ambiguous bare selector from being interpreted as an inference profile.
# https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetFoundationModel.html
# https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
_BEDROCK_FOUNDATION_ARN_PREFIX: Final[str] = "arn:aws:bedrock:sa-east-1::foundation-model/"
_BEDROCK_FOUNDATION_MODEL_ID = re.compile(
    r"[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63}){1,3}(?::[a-z0-9-]{1,63}){0,2}"
)
# Defense in depth for known profile IDs. This is NOT the admission boundary:
# the closed resource-kind/partition/region and the ARN sent on the wire are.
_BEDROCK_PROFILE_NAMESPACES: Final[frozenset[str]] = frozenset({"global", "us", "eu", "apac", "us-gov"})
_BEDROCK_REGIONAL_ENDPOINT: Final[str] = "https://bedrock-runtime.sa-east-1.amazonaws.com"


def _bedrock_direct_model_arn(model: str) -> str:
    """Bind a canonical direct selector to a regional resource; never approve a model.

    Syntax proves the selected resource class, not its existence, availability or
    suitability for PHI. The existing explicit model and owner gates still apply.
    No decoding, whitespace normalization, arbitrary prefix removal or fallback is allowed.
    """
    identifier = model.removeprefix(_BEDROCK_FOUNDATION_ARN_PREFIX) if isinstance(model, str) else ""
    if (
        not _BEDROCK_FOUNDATION_MODEL_ID.fullmatch(identifier)
        or identifier.split(".", 1)[0] in _BEDROCK_PROFILE_NAMESPACES
    ):
        raise BrRegionalTransportUnavailableError(
            "bedrock_phi_model_not_direct_regional_foundation_model", retryable=False
        )
    return _BEDROCK_FOUNDATION_ARN_PREFIX + identifier


class BedrockBrRegionalTransport:
    """Bedrock Converse restricted to direct foundation models in sa-east-1.

    Before the SDK sees the prompt, admit the complete requested AND resolved SDK
    endpoint, resolved region, operator DPA reference and a closed regional resource
    selector. Exact endpoint equality includes scheme/host/port/path/userinfo/query;
    unknown, malformed or normalized aliases refuse rather than being rewritten.

    The response distinguishes SDK configuration, contractual direct-resource routing
    and the operator's retention/training instrument. Neither client.meta.region_name
    nor the DPA reference is vendor-observed execution evidence. Converse does not
    return that evidence, and X-Maezo headers are NOT sent/echoed by the Converse API.
    This source policy supplies no approved vendor/model or legal/privacy ratification.

    SigV4 uses the AWS credential chain. Existing timeout and retry settings remain
    unchanged; any failure after dispatch remains committed to prevent PHI replay.
    """

    #: O provedor consulta isto para saber se o portão de `MAEZO_PHI_API_KEY` se aplica.
    usa_credencial_propria: ClassVar[bool] = True

    #: A única região aceita. Duplicado em relação ao allowlist de host de propósito: o host
    #: carrega a região no nome, e esta constante é o que a compara com o cliente REAL.
    REGIAO: ClassVar[str] = "sa-east-1"

    def __init__(self, *, timeout_s: float = 60.0, client: Any = None) -> None:
        if client is not None:
            self._client = client
        else:
            import boto3  # type: ignore[import-untyped]  # extra [bedrock] opcional
            from botocore.config import Config  # type: ignore[import-untyped]

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.REGIAO,
                config=Config(
                    read_timeout=timeout_s,
                    connect_timeout=min(timeout_s, 10.0),
                    # Configuracao SDK existente, preservada por este reparo de admissao.
                    # O budget externo recusa reenviar uma chamada marcada committed.
                    retries={"max_attempts": 1, "mode": "standard"},
                ),
            )

    def _admit_endpoint(self, request: BrRegionalRequest, *, committed: bool = False) -> None:
        meta = getattr(self._client, "meta", None)
        if (
            request.endpoint_url != _BEDROCK_REGIONAL_ENDPOINT
            or getattr(meta, "endpoint_url", None) != _BEDROCK_REGIONAL_ENDPOINT
            or getattr(meta, "region_name", None) != self.REGIAO
        ):
            raise BrRegionalTransportUnavailableError(
                "bedrock_phi_resolved_endpoint_or_region_not_admitted",
                retryable=False,
                committed=committed,
            )

    async def send(self, request: BrRegionalRequest) -> BrRegionalResponse:
        """Refuse unsafe routing before dispatch; keep evidence provenance explicit."""
        model_arn = _bedrock_direct_model_arn(request.model)
        self._admit_endpoint(request)
        dpa_ref = (
            request.headers.get(HEADER_VENDOR_DPA_REF, "") if isinstance(request.headers, Mapping) else ""
        )
        if not isinstance(dpa_ref, str) or not dpa_ref.strip():
            raise BrRegionalTransportUnavailableError(
                "bedrock_phi_operator_contract_reference_missing", retryable=False
            )

        # boto3 é síncrono; `to_thread` mantém o loop livre sem introduzir um cliente async
        # paralelo que teria de ser mantido em sincronia com este.
        try:
            bruto = await asyncio.to_thread(
                self._client.converse,
                modelId=model_arn,
                messages=[{"role": "user", "content": [{"text": request.prompt}]}],
                inferenceConfig={"maxTokens": request.max_tokens, "temperature": 0.2},
            )
        except Exception as exc:  # nenhum erro de SDK escapa deste módulo
            nome = type(exc).__name__
            # `ThrottlingException` e afins são a única classe re-tentável; o resto não é.
            retryable = "Throttl" in nome or "TooManyRequests" in nome
            raise BrRegionalTransportUnavailableError(
                f"Bedrock regional indisponível ({nome}) para o modelo {request.model!r} em "
                f"{self.REGIAO}. Nenhuma parte da resposta é aproveitada.",
                retryable=retryable,
                # O prompt foi transmitido: o provedor NÃO deve re-enviar PHI.
                committed=True,
            ) from exc

        try:
            # Configuration drift after dispatch is still a committed failure; never
            # normalize a mismatched endpoint into the requested one or retry elsewhere.
            self._admit_endpoint(request, committed=True)
            if 300 <= (bruto.get("ResponseMetadata") or {}).get("HTTPStatusCode", 200) < 400:
                raise BrRegionalTransportUnavailableError(
                    "bedrock_phi_redirect_response_refused", retryable=False, committed=True
                )
            blocos = (bruto.get("output") or {}).get("message", {}).get("content") or []
            texto = next((b["text"] for b in blocos if isinstance(b, dict) and "text" in b), "")
            uso = bruto.get("usage") or {}
            parada = str(bruto.get("stopReason") or "")

            return BrRegionalResponse(
                completion=texto,
                model=str(bruto.get("modelId") or request.model),
                usage=BrRegionalTokenUsage(
                    input_tokens=int(uso.get("inputTokens") or 0),
                    output_tokens=int(uso.get("outputTokens") or 0),
                    # O Bedrock reporta cache de prompt em `cacheReadInputTokens` quando há; ausente
                    # significa zero, não desconhecido.
                    cached_prefix_tokens=int(uso.get("cacheReadInputTokens") or 0),
                ),
                endpoint_url=_BEDROCK_REGIONAL_ENDPOINT,
                # Eligibility from admitted direct regional routing, NOT a response field.
                served_region=BR_REGIONAL_ATTESTED_REGION,
                zero_retention_acknowledged=True,
                training_prohibited_acknowledged=True,
                region_evidence_source="regional_direct_model_contract",
                endpoint_evidence_source="sdk_resolved_endpoint",
                retention_evidence_source="operator_contract_reference",
                # NUNCA sintético: isto é uma chamada real. `RefusingBrRegionalTransport` é quem
                # recusa, e `LabeledFakeBrRegionalTransport` é quem fabrica.
                synthetic=False,
                # `stopReason` normal (end_turn, max_tokens) NAO e' recusa; guardrail e'.
                refusal_code=("guardrail_intervened" if parada == "guardrail_intervened" else ""),
            )
        except BrRegionalTransportUnavailableError:
            raise
        except Exception as exc:
            raise BrRegionalTransportUnavailableError(
                "bedrock_phi_invalid_response_after_dispatch", retryable=False, committed=True
            ) from exc


class RefusingBrRegionalTransport:
    """PRODUCTION DEFAULT: refuses to talk to anything — no real BR-regional transport exists.

    What `resolve_br_regional_transport(None)` returns, mirroring
    `ans_gateway.resolve_ans_gateway`: an UNWIRED adapter refuses rather than silently reaching
    for the fake, so the labeled fake is unreachable in production by construction and not
    merely by convention.

    This is why `BrResidentInferenceProvider.is_mock` can honestly be ``False`` while no real
    endpoint exists. It is not a mock — it is a real adapter whose transport is missing, and a
    missing transport RAISES. It never fabricates a completion, which is precisely the
    difference between this class and `PhiZoneMockProvider` (constraint 3).
    """

    async def send(self, request: BrRegionalRequest) -> BrRegionalResponse:
        logger.warning(
            "br_regional_transport_refusing",
            # Structure only: host + counts + fingerprint. Never the prompt, never the credential.
            endpoint_host=urlsplit(request.endpoint_url).hostname,
            model=request.model,
            prompt_fingerprint=_fingerprint(request.prompt),
        )
        raise BrRegionalTransportUnavailableError(
            "no BR-regional inference transport is wired — RefusingBrRegionalTransport is the "
            "production default until a real one is built inside a §8.2-sanctioned transport "
            "module and wired through the gateway registry, with an ADR-0017 NetworkPolicy "
            "proof. Fail-closed: it issues no completion and never fabricates one.",
            retryable=False,
        )


class FakeBrRegionalOutcome(StrEnum):
    """Response behaviours :class:`LabeledFakeBrRegionalTransport` can be built to produce.

    One member per branch the adapter can take on a response, so that every check in
    :meth:`BrResidentInferenceProvider._validate_response` has a test that can actually reach it.
    A check with no reachable failing input is a vacuous check.
    """

    ACCEPTED = "accepted"

    #: The vendor's safety classifier declined. A real, well-formed, attested response that
    #: carries no completion — the adapter must surface it as a provider error, not as text.
    VENDOR_REFUSAL = "vendor-refusal"

    #: The endpoint is unreachable. The transport raises instead of returning. A failure BEFORE any
    #: byte is sent (connection refused / DNS / pre-flight) — the ONE retryable, un-committed case.
    OUTAGE = "outage"

    #: The prompt was TRANSMITTED and then the read timed out / the stream dropped mid-response — a
    #: failure AFTER the committed point (bytes-sent). Transient in the SDK sense (``retryable``),
    #: but NOT safe to re-dial: the PHI prompt is already on the wire, so re-sending it would
    #: double-expose it (W8 idempotency rule, leg 4). Distinct from OUTAGE precisely by ``committed``.
    READ_TIMEOUT_AFTER_SEND = "read-timeout-after-send"

    #: The endpoint answered with something that is not the contract at all.
    MALFORMED = "malformed"

    #: The vendor followed a redirect and answered from a DIFFERENT, non-approved endpoint —
    #: the PHI residency escape this adapter exists to refuse.
    REDIRECTED_OFF_REGION = "redirected-off-region"

    #: Well-formed and on the right endpoint, but the vendor declares a different execution region.
    WRONG_SERVED_REGION = "wrong-served-region"

    #: The vendor did not acknowledge the zero-retention flag it was sent.
    RETENTION_NOT_ACKNOWLEDGED = "retention-not-acknowledged"

    #: The vendor did not acknowledge the training-prohibition flag it was sent.
    TRAINING_NOT_ACKNOWLEDGED = "training-not-acknowledged"


#: The unmistakably-synthetic completion prefix, mirroring `MOCK_ANS_PROTOCOL_PREFIX` and
#: `PhiZoneMockProvider`'s "[SYNTHETIC RESPONSE …]". Nothing that could read as model output.
FAKE_BR_REGIONAL_COMPLETION_PREFIX: Final[str] = (
    "[SYNTHETIC RESPONSE — LabeledFakeBrRegionalTransport, NOT a real model completion]"
)

#: The fake's refusal code. Deliberately self-labelling as a fake artifact rather than an
#: invented vendor code — the real vocabulary is unknown, and inventing one would be the
#: fabrication `MOCK_ANS_NACK_MOTIVO` refuses for the same reason.
FAKE_BR_REGIONAL_REFUSAL_CODE: Final[str] = "FAKE-BR-REGIONAL-REFUSAL-NOT-A-VENDOR-CODE"

#: Endpoint the fake answers from when asked to simulate a redirect escape. A `.example` host —
#: reserved by RFC 2606, resolves nowhere — and it fails `BR_REGIONAL_ENDPOINT_HOST_SUFFIXES`,
#: which is the entire point of it.
FAKE_BR_REGIONAL_REDIRECT_URL: Final[str] = "https://redirected-off-region.example/v1/generate"

#: Synthetic characters-per-token divisor. A ROUND, OBVIOUSLY-FAKE constant, not a calibrated
#: estimate of any tokenizer: these counts exist so leg 3 has non-zero fields to reconcile
#: against a shape, never so anyone reads a token number off a test run (constraint 3).
_FAKE_CHARS_PER_TOKEN: Final[int] = 4


class LabeledFakeBrRegionalTransport:
    """DEV/TEST ONLY: an in-process BR-regional endpoint that refuses to masquerade as real.

    Follows `LabeledMockAnsGatewayTransport`'s discipline exactly — every completion carries
    :data:`FAKE_BR_REGIONAL_COMPLETION_PREFIX`, every response sets ``synthetic=True``, and every
    token count is transparently synthetic. It is DETERMINISTIC: identical requests produce
    byte-identical responses, with no clock and no randomness anywhere, so a test can assert on
    exact bytes and the W8 prefix-stability proof has something stable to stand on.

    NEVER reachable in production: `resolve_br_regional_transport(None)` selects
    :class:`RefusingBrRegionalTransport`, so reaching this class requires an explicit injection
    that only a test performs.

    ``outcome`` is a CONSTRUCTOR argument, not a request field — see
    :class:`BrRegionalTransport` for why the wire contract deliberately has no channel a caller
    could use to request an outcome.

    ``sent_requests`` records what the adapter actually put on the wire, so a test can assert on
    the headers/flags the adapter claims to send rather than trusting the adapter's own logs.
    """

    def __init__(
        self,
        *,
        outcome: FakeBrRegionalOutcome = FakeBrRegionalOutcome.ACCEPTED,
        served_region: str = BR_REGIONAL_ATTESTED_REGION,
    ) -> None:
        self._outcome = outcome
        self._served_region = served_region
        self.sent_requests: list[BrRegionalRequest] = []

    async def send(self, request: BrRegionalRequest) -> BrRegionalResponse:
        self.sent_requests.append(request)

        if self._outcome is FakeBrRegionalOutcome.OUTAGE:
            # BEFORE the commit point: no byte reached the endpoint, so this IS safe to retry.
            raise BrRegionalTransportUnavailableError(
                "LabeledFakeBrRegionalTransport simulated endpoint outage (synthetic)",
                retryable=True,
                committed=False,
            )
        if self._outcome is FakeBrRegionalOutcome.READ_TIMEOUT_AFTER_SEND:
            # The request was appended to `sent_requests` ABOVE — the prompt is on the wire — and
            # only THEN does the read fail. `committed=True` is what the budget reads to refuse a
            # re-dial; `retryable=True` proves the refusal is the COMMIT guard, not mere
            # non-retryability. Neuter the guard and this WOULD be re-sent — the leg-4 RED control.
            raise BrRegionalTransportUnavailableError(
                "LabeledFakeBrRegionalTransport simulated read timeout AFTER the prompt was "
                "transmitted (synthetic)",
                retryable=True,
                committed=True,
            )
        if self._outcome is FakeBrRegionalOutcome.MALFORMED:
            # DELIBERATELY off-contract: a real endpoint returning a body that does not match the
            # agreed schema is a genuine failure mode, and the adapter must not trust the return
            # ANNOTATION to rule it out. Typed as the Protocol says, returned as something else.
            return {"unexpected": "shape"}  # type: ignore[return-value]

        prompt_chars = len(request.stable_prefix) + len(request.variable_suffix)
        usage = BrRegionalTokenUsage(
            input_tokens=prompt_chars // _FAKE_CHARS_PER_TOKEN,
            output_tokens=len(FAKE_BR_REGIONAL_COMPLETION_PREFIX) // _FAKE_CHARS_PER_TOKEN,
            # W8: the fake reports the whole declared stable prefix as cache-served, so a test can
            # prove the boundary the adapter transmitted is the one that got reused.
            cached_prefix_tokens=len(request.stable_prefix) // _FAKE_CHARS_PER_TOKEN,
        )

        if self._outcome is FakeBrRegionalOutcome.VENDOR_REFUSAL:
            return BrRegionalResponse(
                completion="",
                model=request.model,
                usage=usage,
                endpoint_url=request.endpoint_url,
                served_region=self._served_region,
                zero_retention_acknowledged=True,
                training_prohibited_acknowledged=True,
                synthetic=True,
                region_evidence_source="synthetic",
                endpoint_evidence_source="synthetic",
                retention_evidence_source="synthetic",
                refusal_code=FAKE_BR_REGIONAL_REFUSAL_CODE,
            )

        completion = (
            f"{FAKE_BR_REGIONAL_COMPLETION_PREFIX} "
            f"stable_prefix_chars={len(request.stable_prefix)} "
            f"variable_suffix_chars={len(request.variable_suffix)}"
        )
        return BrRegionalResponse(
            completion=completion,
            model=request.model,
            usage=usage,
            endpoint_url=(
                FAKE_BR_REGIONAL_REDIRECT_URL
                if self._outcome is FakeBrRegionalOutcome.REDIRECTED_OFF_REGION
                else request.endpoint_url
            ),
            served_region=(
                "us-east-1"
                if self._outcome is FakeBrRegionalOutcome.WRONG_SERVED_REGION
                else self._served_region
            ),
            zero_retention_acknowledged=(
                self._outcome is not FakeBrRegionalOutcome.RETENTION_NOT_ACKNOWLEDGED
            ),
            training_prohibited_acknowledged=(
                self._outcome is not FakeBrRegionalOutcome.TRAINING_NOT_ACKNOWLEDGED
            ),
            synthetic=True,
            region_evidence_source="synthetic",
            endpoint_evidence_source="synthetic",
            retention_evidence_source="synthetic",
        )


def resolve_br_regional_transport(transport: BrRegionalTransport | None) -> BrRegionalTransport:
    """Fail-closed default: an unwired seam resolves to the REFUSING transport, NEVER the fake.

    The load-bearing "the fake is unreachable in production by construction" guarantee, identical
    in shape and reasoning to `ans_gateway.resolve_ans_gateway`.
    """
    if transport is None:
        return RefusingBrRegionalTransport()
    return transport
