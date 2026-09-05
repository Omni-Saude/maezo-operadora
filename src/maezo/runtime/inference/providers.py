"""Concrete LLM provider implementations (D2-02 split, step 6/8).

Moved verbatim out of ``maezo/runtime/inference.py`` — cut-and-paste, never a redefinition
(``docs/reports/inference-split-plan.md`` §5 step 6): :class:`BaseInferenceProvider` (the
provider contract), :class:`NoopInferenceProvider`, :func:`_emit_llm_token_usage` (T8 telemetry),
:class:`AnthropicInferenceProvider`, :class:`BedrockInferenceProvider`,
:class:`PhiZoneMockProvider`.

The physical DEFINITION of ``AnthropicInferenceProvider``/``BedrockInferenceProvider`` moves here,
but the CONSTRUCTION call sites (``_build_anthropic``/``_build_bedrock``) deliberately stay in
``runtime/inference.py`` until step 8 — a ``class`` statement is not a ``Call``, so a class's own
defining module never needs a chokepoint-fence allowlist entry for that reason alone
(``scripts/ci/check_effect_chokepoint_fence.py:144-147``'s own comment). This move therefore needs
NO chokepoint-fence edit; that edit is step 8's, when the CALLING module's path itself changes.

This SAME commit also generalizes the structural anti-drift guard in
``tests/unit/runtime/test_inference_capabilities.py`` (``_concrete_providers_defined_in_module``):
the filter that used to compare ``cls.__module__ == "maezo.runtime.inference"`` now compares
``cls.__module__.startswith("maezo.runtime.inference")``, with a paired negative control (reusing
the established pattern from ``test_the_provider_population_is_walked_transitively``) proving the
guard still excludes a class from outside the package — required in the SAME commit per the plan's
§1.2/§5 step 6, because the moment this file's classes report a different ``__module__`` than the
exact-equality filter expects, the completeness assertions fail loudly (reproduced live in the
plan's own analysis: ``3 failed, 49 passed``), not silently.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import ClassVar

import anthropic
import structlog

from maezo.runtime.inference.capabilities import (
    ANTHROPIC_CAPABILITIES,
    BEDROCK_CAPABILITIES,
    NOOP_CAPABILITIES,
    PHI_ZONE_MOCK_CAPABILITIES,
    ProviderCapabilities,
)
from maezo.runtime.inference.errors import InferenceConfigError, InferenceProviderError
from maezo.runtime.inference.settings import (
    _ANTHROPIC_API_KEY_ENV_VARS,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_BEDROCK_MODEL,
    DEFAULT_BEDROCK_REGION,
    BedrockSettings,
)

logger = structlog.get_logger(__name__)

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


def _texto_dos_blocos(blocos: object, *, provider: str) -> str:
    """Concatena os blocos `type="text"` de uma resposta, RECUSANDO qualquer item fora do schema.

    §Delta-D1 — POR QUE A GUARDA DE LISTA NAO BASTAVA. O reparo anterior conferia que
    `response.content` era uma LISTA e depois lia `bloco.type` / `bloco.text` com acesso direto de
    atributo. A guarda estava exatamente UM NIVEL raso demais: o SDK valida a resposta de forma
    NAO-ESTRITA por padrao, entao um 200 fora do schema produz uma lista com ITENS fora do schema,
    e as quatro formas abaixo — todas 100% EXTERNAS — vazavam uma classe de `PROGRAMMING_ERRORS`
    e DERRUBAVAM o turno, onde na base elas degradavam (medido com o SDK real, `MockTransport` so'
    no socket):

        content=[{"type": "text"}]            -> TypeError: expected str instance, NoneType found
        content=[{"type": "text", "text": 1}] -> TypeError: expected str instance, int found
        content=["ola"]                       -> AttributeError: 'str' object has no attribute 'type'
        content=[null]                        -> AttributeError: 'NoneType' object has no attribute 'type'

    O CONTRATO QUE ESTA FUNCAO APLICA, item a item: todo bloco tem `type` string; todo bloco
    `type="text"` tem `text` string. Um bloco de outro tipo (`thinking`, `tool_use`) e' IGNORADO
    sem exigir `text` — e' o que o `if block.type == "text"` original ja' fazia, e o que os duplos
    de `test_inference_bedrock.py` exercitam.

    NUNCA DEVOLVER `""` PARA UM CORPO PODRE. Filtrar o item invalido em silencio seria trocar um
    turno derrubado por uma NARRATIVA VAZIA indistinguivel da degradacao legitima — o silent
    fallback que este WP inteiro existe para fechar. O item invalido vira a falha DECLARADA, com o
    token de CLASSE e nenhum byte do corpo (a resposta de um LLM sobre um dossie e' PHI).

    NAO usa `isinstance(response, anthropic.types.Message)`: os ~20 duplos pre-existentes de
    `tests/unit/runtime/test_inference.py::_fake_message` sao `SimpleNamespace`, e a checagem
    estrita os deixaria vermelhos. Guardar a JUNCAO cobre a mesma superficie sem tocar neles.

    Args:
        blocos: o `content` cru da resposta — de proposito `object`, porque a pergunta "isto e'
            sequer uma lista?" faz parte do contrato que esta funcao aplica.
        provider: qual provedor nomear no erro (`"anthropic"` / `"bedrock"`).

    Returns:
        A concatenacao dos blocos de texto.

    Raises:
        InferenceProviderError: `content` nao e' lista, ou algum item esta fora do schema.
    """
    if not isinstance(blocos, list):
        raise InferenceProviderError(
            provider,
            f"resposta 200 fora do contrato: sem blocos de conteudo utilizaveis ({type(blocos).__name__})",
            retryable=True,
        )

    pedacos: list[str] = []
    for posicao, bloco in enumerate(blocos):
        tipo = getattr(bloco, "type", None)
        if not isinstance(tipo, str):
            raise InferenceProviderError(
                provider,
                f"resposta 200 fora do contrato: bloco {posicao} sem `type` utilizavel "
                f"(bloco e' {type(bloco).__name__}, `type` e' {type(tipo).__name__})",
                retryable=True,
            )
        if tipo != "text":
            continue
        trecho = getattr(bloco, "text", None)
        if not isinstance(trecho, str):
            raise InferenceProviderError(
                provider,
                f"resposta 200 fora do contrato: bloco {posicao} e' `type=text` mas o `text` "
                f"nao e' uma string ({type(trecho).__name__})",
                retryable=True,
            )
        pedacos.append(trecho)

    return "".join(pedacos)


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
        except (anthropic.AnthropicError, ValueError) as exc:
            # §Delta-F1, o MESMO defeito que `tools/mcp_fhir/server.py::_corpo_de_recurso` fecha,
            # do outro lado da fronteira. Os cinco `except` acima cobrem auth/rate-limit/timeout/
            # conexao/status, e NAO cobrem o corpo: o SDK faz `response.json()` sem guarda quando
            # o `content-type` termina em `json` (`anthropic/_response.py`), de modo que um 200
            # com corpo nao-JSON — proxy/WAF respondendo pelo backend — levanta
            # `json.JSONDecodeError`, subclasse de `ValueError`; e um corpo que nao casa com o
            # schema levanta `anthropic.APIResponseValidationError`, que NAO e' `APIStatusError`
            # e nao e' subclasse de `RuntimeError`/`OSError`/`httpx.HTTPError`. As duas escapavam
            # deste modulo com tipo de SDK/stdlib cru e, depois de NEW-12 estreitar os nos,
            # DERRUBARIAM o turno em vez de degradar. Ambas sao falha do FORNECEDOR: viram o tipo
            # declarado no `Raises:` de `InferenceProvider.generate`. Mesma postura ja' escrita em
            # `BedrockInferenceProvider.generate` ("no SDK/transport type may leak past this
            # module") e em `br_regional.py`. Um bug local continua propagando: o corpo do `try`
            # contem UMA chamada, a do SDK.
            raise InferenceProviderError(
                "anthropic",
                f"resposta ilegivel do provedor ({type(exc).__name__})",
                retryable=True,
            ) from exc

        # §Delta-F1/D1, a FORMA da resposta, validada ANTES de qualquer leitura de atributo. O
        # SDK so' devolve uma `Message` quando o corpo se parece com uma, e ha' tres vias em que
        # ele nao devolve: (a) `content-type` que nao termina em `json` com validacao NAO-ESTRITA
        # (o padrao) faz o SDK devolver o TEXTO CRU — uma `str` —; (b) um corpo JSON fora do
        # schema vira uma `Message` com `content=None`; e (c) um corpo JSON fora do schema com
        # `content` que E' lista mas cujos ITENS estao fora do schema. Nas tres, as leituras
        # seguintes (`response.stop_reason`, `bloco.type`, `bloco.text`) davam
        # `AttributeError`/`TypeError` — classes de `PROGRAMMING_ERRORS` — e DERRUBAVAM o turno
        # por causa de um proxy mal configurado. `_texto_dos_blocos` aplica o contrato inteiro,
        # item a item, e roda ANTES de `response.stop_reason` justamente por causa de (a).
        text = _texto_dos_blocos(getattr(response, "content", None), provider="anthropic")

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

        # §Delta-D1. A juncao deste provedor e' a MESMA de `AnthropicInferenceProvider.generate`
        # ("MIRRORED ... clause for clause", acima), e por isso tinha o MESMO defeito: `content`
        # lido sem checagem de forma e `block.type`/`block.text` lidos com acesso direto. Consertar
        # so' um dos dois espelhos e declarar o seam fechado seria repetir exatamente o erro que
        # este §Delta corrige, entao os dois passam pelo MESMO helper — uma definicao, nunca duas
        # tabelas sutilmente diferentes. Roda ANTES da metrica e do `stop_reason` pelo mesmo motivo
        # que no irmao: uma resposta que nem e' `Message` nao tem `stop_reason` para ler.
        text = _texto_dos_blocos(getattr(response, "content", None), provider="bedrock")

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
