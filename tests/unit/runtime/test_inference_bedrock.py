"""Unit tests for the AWS Bedrock provider (W-BEDROCK, general zone only).

Companion to ``test_inference.py``, whose structure and SDK-boundary mocking discipline this
file mirrors deliberately: the Bedrock provider is the same ``anthropic`` SDK reaching the same
model family over a different transport, so its tests should be diffable against the 1P
provider's rather than inventing a second style. Every SDK call is mocked at the SDK boundary
(``client.messages.create``) — NO NETWORK, NO AWS CREDENTIAL, NO SIGNING, in any test here.

WHAT THIS FILE IS ANCHORED ON, since a "same as anthropic" test file could easily become
vacuous: the four facts that are genuinely NEW or genuinely DIFFERENT for Bedrock —

1. the provider stays in the GENERAL zone (``phi=True`` is refused, and the refusal happens
   before any client call);
2. the request carries the ``global.anthropic.*`` INFERENCE PROFILE id this account actually
   serves — not the first-party id, and not the bare foundation-model id, both of which 404 —
   and NOTHING the current models reject (``temperature``/``top_p``/``top_k``/``thinking``);
3. a botocore signing/credential failure — a class of error the 1P provider cannot produce —
   is wrapped, never leaked past this module;
4. metering runs through the SAME T8 seam with ``provider="bedrock"``.

Zone-contract facts (``ProviderCapabilities``, ``phi_capable``, registry membership) live in
``test_inference_capabilities.py``, which owns the hardcoded capability table.

D2-02 SPLIT NOTE (docs/reports/inference-split-plan.md §5 steps 6/8): the metering tests below
patch ``"maezo.runtime.inference.providers.logger"``, not ``"maezo.runtime.inference.logger"`` —
see ``test_inference.py``'s module docstring for why (``BedrockInferenceProvider`` lives in
``maezo/runtime/inference/providers.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest
from botocore.exceptions import NoCredentialsError

from maezo.runtime.inference import (
    DEFAULT_BEDROCK_MODEL,
    DEFAULT_BEDROCK_REGION,
    BedrockInferenceProvider,
    BedrockSettings,
    InferenceConfigError,
    InferenceProvider,
    InferenceProviderError,
    InferenceSettings,
    PhiZoneRoutingError,
)

# ---------------------------------------------------------------------------
# Helpers — same shapes as test_inference.py, so the two files stay diffable
# ---------------------------------------------------------------------------


def _fake_httpx_response(status_code: int) -> httpx.Response:
    request = httpx.Request(
        "POST", "https://bedrock-runtime.sa-east-1.amazonaws.com/model/global.anthropic.claude-opus-5/invoke"
    )
    return httpx.Response(status_code, request=request, json={"error": {"message": "boom"}})


def _fake_usage(input_tokens: object = 100, output_tokens: object = 40) -> SimpleNamespace:
    """Stand-in for the Bedrock response's ``usage``.

    Field names are ``input_tokens``/``output_tokens`` because that is what the Bedrock
    Messages endpoint returns — the SAME shape as the 1P API, which is exactly why
    ``_emit_llm_token_usage`` needs no Bedrock-specific branch.
    """
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


def _fake_message(
    text: str = "olá da bedrock",
    stop_reason: str = "end_turn",
    *,
    usage: SimpleNamespace | None = None,
    model: str | None = None,
    content: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    """Stand-in for the SDK ``Message`` response.

    ``content`` is overridable so the refusal test can assert against an EMPTY content list —
    the real shape of a classifier refusal, and the reason ``generate`` must check
    ``stop_reason`` before touching ``content``.
    """
    blocks = [SimpleNamespace(type="text", text=text)] if content is None else content
    message = SimpleNamespace(content=blocks, stop_reason=stop_reason)
    if usage is not None:
        message.usage = usage
    if model is not None:
        message.model = model
    return message


@pytest.fixture(autouse=True)
def _clean_bedrock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient AWS/Bedrock configuration may reach a test here.

    ``BedrockSettings`` is a pydantic-settings model reading ``MAEZO_BEDROCK_*``, so a
    developer's exported region or model id would otherwise silently change what the
    default-resolution tests below assert. The ``AWS_*`` names are cleared for a stronger
    reason: with a real credential in the environment, a test whose SDK mock was
    mis-wired could make an actual, billable, signed call to Bedrock.
    """
    for name in (
        "MAEZO_BEDROCK_MODEL_ID",
        "MAEZO_BEDROCK_REGION",
        "MAEZO_INFERENCE_MODEL",
        "MAEZO_INFERENCE_PROVIDER",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    ):
        monkeypatch.delenv(name, raising=False)


def _provider_with_response(response: object, **kwargs: object) -> BedrockInferenceProvider:
    """A constructed provider whose SDK boundary is replaced by an ``AsyncMock``.

    THE SEAM IS ``client.messages.create``, mirroring ``test_inference.py``: the SDK client
    object itself is real (constructing it performs no I/O and resolves no credential), and
    only the one coroutine that would open a socket is replaced. That keeps the test honest
    about the client this provider actually builds — see
    ``test_uses_the_classic_bedrock_runtime_client_not_the_mantle_one``.
    """
    impl = BedrockInferenceProvider(**kwargs)  # type: ignore[arg-type]
    impl._client.messages.create = AsyncMock(return_value=response)  # type: ignore[method-assign]
    return impl


def _llm_token_samples(collector: object) -> list[Any]:
    """Every Prometheus sample for the T8 ``maezo_llm_tokens_total`` counter."""
    return [
        sample
        for metric in collector.registry.collect()  # type: ignore[attr-defined]
        for sample in metric.samples
        if sample.name == "maezo_llm_tokens_total"
    ]


# ---------------------------------------------------------------------------
# Selection — the provider is reachable by env, and nothing else moved
# ---------------------------------------------------------------------------


def test_bedrock_is_selectable_via_the_provider_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MAEZO_INFERENCE_PROVIDER=bedrock`` builds the Bedrock provider — through the ENV, not
    through a hand-constructed settings object, because the env var is the operator's surface."""
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", "bedrock")

    provider = InferenceProvider(settings=InferenceSettings())

    assert provider.provider_name == "bedrock"
    assert isinstance(provider._impl, BedrockInferenceProvider)  # noqa: SLF001


def test_default_provider_is_still_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESSION CONTROL. Registering a new factory must not change the shipped default
    (constraint 2) — a deployment that names no provider still gets the mock, not AWS."""
    assert InferenceSettings().provider == "noop"
    assert InferenceProvider().provider_name == "noop"


def test_unknown_provider_still_fails_closed_and_now_lists_bedrock() -> None:
    """The fail-closed startup error is unchanged, and its message names the new valid value."""
    with pytest.raises(InferenceConfigError, match="Unknown MAEZO_INFERENCE_PROVIDER") as excinfo:
        InferenceProvider(settings=InferenceSettings(provider="aws"))

    assert "bedrock" in str(excinfo.value)


def test_bedrock_needs_no_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the provider: no 1P vendor key is issued, held or required.

    Constructed with BOTH Anthropic key variables absent — the exact configuration that makes
    ``AnthropicInferenceProvider`` refuse to start.
    """
    monkeypatch.delenv("MAEZO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert InferenceProvider(settings=InferenceSettings(provider="bedrock")).provider_name == "bedrock"


# ---------------------------------------------------------------------------
# Model / region resolution
# ---------------------------------------------------------------------------


def test_model_defaults_to_the_global_inference_profile_id() -> None:
    """The default is the id the live probe PROVED this account serves — both prefixes.

    Asserted as a literal rather than only against ``DEFAULT_BEDROCK_MODEL``: comparing the
    constant to itself would keep ``x == x`` true while the id drifted to something Bedrock
    rejects, which is exactly what happened here. Both prefixes are separately load-bearing and
    both were established live (profile ``amh-data-dev``, ``sa-east-1``, 2026-08-12):

    * dropping ``anthropic.`` yields a first-party id Bedrock does not know at all;
    * dropping ``global.`` yields the bare foundation-model id that returned **404 "The model
      … does not exist"** on this account, while the ``global.*`` inference profile succeeded.
    """
    impl = BedrockInferenceProvider()

    assert impl._model == "global.anthropic.claude-opus-5" == DEFAULT_BEDROCK_MODEL  # noqa: SLF001
    assert impl._model.startswith("global.anthropic.")  # noqa: SLF001


def test_bedrock_model_id_env_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5")

    assert BedrockInferenceProvider()._model == "global.anthropic.claude-haiku-4-5"  # noqa: SLF001


def test_inference_model_wins_over_bedrock_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Precedence, asserted where the two DISAGREE — the only configuration that can prove an
    order. The provider-agnostic ``MAEZO_INFERENCE_MODEL`` (threaded in as ``model=`` by the
    factory) outranks the Bedrock-specific variable."""
    monkeypatch.setenv("MAEZO_BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5")

    impl = BedrockInferenceProvider(model="global.anthropic.claude-opus-5")

    assert impl._model == "global.anthropic.claude-opus-5"  # noqa: SLF001


def test_inference_model_reaches_the_provider_through_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end plumbing: the factory really does thread ``MAEZO_INFERENCE_MODEL`` through.

    Without this, the precedence test above would be proving something about a constructor
    argument nobody passes in production.
    """
    monkeypatch.setenv("MAEZO_INFERENCE_MODEL", "global.anthropic.claude-sonnet-5")

    provider = InferenceProvider(settings=InferenceSettings(provider="bedrock"))

    assert provider._impl._model == "global.anthropic.claude-sonnet-5"  # noqa: SLF001


def test_region_defaults_to_sa_east_1_and_reaches_the_client() -> None:
    """The default region is applied AND actually handed to the SDK client — the second half
    matters because a region stored on the provider but never passed on would leave every
    request going to the SDK's own default region."""
    impl = BedrockInferenceProvider()

    assert impl._region == "sa-east-1" == DEFAULT_BEDROCK_REGION  # noqa: SLF001
    assert impl._client.aws_region == "sa-east-1"  # noqa: SLF001


def test_bedrock_region_env_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_BEDROCK_REGION", "us-east-1")

    impl = BedrockInferenceProvider()

    assert impl._region == "us-east-1"  # noqa: SLF001
    assert impl._client.aws_region == "us-east-1"  # noqa: SLF001


def test_explicitly_blank_region_refuses_to_construct(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed on a STATED intent, not on an absent one.

    A blank region cannot happen by omission (there is a default), so it can only come from an
    operator setting the variable to "". The module answers that with a refusal at startup
    rather than silently substituting a region — which would send traffic somewhere the
    operator explicitly declined to name.
    """
    monkeypatch.setenv("MAEZO_BEDROCK_REGION", "   ")

    with pytest.raises(InferenceConfigError, match="requires an AWS region"):
        BedrockInferenceProvider()


def test_blank_region_refusal_is_the_same_error_type_as_a_missing_anthropic_key() -> None:
    """ "As loud as a missing credential", asserted as type identity rather than as a comment —
    mirroring ``test_capability_mismatch_uses_the_same_error_type_as_a_missing_credential``."""
    with pytest.raises(InferenceConfigError) as blank_region:
        BedrockInferenceProvider(region="  ")

    assert type(blank_region.value) is InferenceConfigError


def test_bedrock_settings_reads_the_operator_facing_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env var SPELLINGS are part of the operator contract (.env.example, runbook), so pin
    them here rather than only asserting the resolved behaviour."""
    monkeypatch.setenv("MAEZO_BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5")
    monkeypatch.setenv("MAEZO_BEDROCK_REGION", "us-west-2")

    settings = BedrockSettings()

    assert settings.model_id == "global.anthropic.claude-sonnet-5"
    assert settings.region == "us-west-2"


# ---------------------------------------------------------------------------
# The client — classic bedrock-runtime, and credential-free at construction
# ---------------------------------------------------------------------------


def test_uses_the_classic_bedrock_runtime_client_not_the_mantle_one() -> None:
    """The client choice is PINNED because it is deliberate and counter-recommendation.

    ``AnthropicBedrockMantle`` is normally the recommended Bedrock client, and this provider
    does NOT use it: on this account the Mantle endpoint returns **404 "The model … does not
    exist"** for both ``anthropic.claude-opus-5`` and ``global.anthropic.claude-opus-5``
    (request reached the endpoint, SigV4 verified — request_id ``req_wg2absqs…``), while classic
    ``bedrock-runtime`` served the same model successfully. See the provider docstring.

    Pinned by TYPE and BASE URL together, because the difference is invisible at the
    ``messages.create`` call site — both clients expose it identically — and shows up only as a
    different endpoint. A well-meant "use the recommended client" edit would therefore pass every
    other test in this file while every real request 404s; this is the test that catches it.
    """
    impl = BedrockInferenceProvider()

    assert type(impl._client) is anthropic.AsyncAnthropicBedrock  # noqa: SLF001
    assert type(impl._client) is not anthropic.AsyncAnthropicBedrockMantle  # noqa: SLF001
    assert "bedrock-runtime" in str(impl._client.base_url)  # noqa: SLF001
    assert "bedrock-mantle" not in str(impl._client.base_url)  # noqa: SLF001


def test_construction_resolves_no_aws_credential() -> None:
    """The documented DIFFERENCE from the 1P provider, asserted rather than asserted-in-prose.

    Every ``AWS_*`` variable is cleared by the autouse fixture, so if construction resolved the
    credential chain eagerly this would raise instead of returning a usable provider. It does
    not: botocore resolves lazily, per request, which is why ``credential_source`` is declared
    ``AWS_DEFAULT_CHAIN`` and not ``ENVIRONMENT``.
    """
    impl = BedrockInferenceProvider()

    assert impl.health_check()["status"] == "ok"


def test_health_check_does_not_claim_a_verified_credential() -> None:
    """Constraint 3 at the health seam: never a fabricated 'ok' beyond what was verified.

    The provider read no credential, so the message must not imply one was checked. Asserted
    positively (the disclosure is present) AND negatively (the 1P provider's "credential
    present" claim is absent), so a future rewrite cannot quietly upgrade the claim.
    """
    result = BedrockInferenceProvider().health_check()

    assert result["status"] == "ok"
    assert "NOT verified" in result["message"]
    assert "credential present" not in result["message"]
    assert "sa-east-1" in result["message"]


# ---------------------------------------------------------------------------
# PHI routing — the general zone stays general (ADR-0006 / ADR-0017)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phi_request_is_refused_for_bedrock() -> None:
    """THE LOAD-BEARING TEST OF THIS WHOLE CHANGE. A new real provider must not become a new
    way for PHI to leave the zone."""
    provider = InferenceProvider(settings=InferenceSettings(provider="bedrock"))

    with pytest.raises(PhiZoneRoutingError):
        await provider.generate("dados PHI do beneficiário", phi=True)


@pytest.mark.asyncio
async def test_phi_request_never_reaches_the_bedrock_client() -> None:
    """The refusal is STRUCTURAL, not merely an exception raised somewhere along the way: no
    request is attempted, so no PHI is signed, transmitted or billed. Mirrors
    ``test_phi_routing_never_reaches_anthropic_client``."""
    provider = InferenceProvider(settings=InferenceSettings(provider="bedrock"))
    create_mock = AsyncMock(return_value=_fake_message("should never be called"))
    provider._impl._client.messages.create = create_mock  # type: ignore[attr-defined]  # noqa: SLF001

    with pytest.raises(PhiZoneRoutingError):
        await provider.generate("dados PHI", phi=True)

    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_phi_zone_required_refuses_to_boot_against_bedrock() -> None:
    """The startup half of the same guarantee: a deployment declaring PHI intent cannot boot
    wired to Bedrock, and the refusal names the capability facts that fell short."""
    settings = InferenceSettings(provider="bedrock", phi_zone_required=True)

    with pytest.raises(InferenceConfigError, match="PHI zone required") as excinfo:
        InferenceProvider(settings=settings)

    assert "phi_allowed=False" in str(excinfo.value)


@pytest.mark.asyncio
async def test_general_zone_traffic_is_not_blocked() -> None:
    """OVER-FIRE CONTROL for the two refusals above: with ``phi=False`` the provider serves
    normally. Without this, an unconditional raise would pass every PHI test in this file."""
    provider = InferenceProvider(settings=InferenceSettings(provider="bedrock"))
    provider._impl._client.messages.create = AsyncMock(  # type: ignore[attr-defined]  # noqa: SLF001
        return_value=_fake_message("resposta geral")
    )

    assert await provider.generate("pergunta geral", phi=False) == "resposta geral"


# ---------------------------------------------------------------------------
# generate() — happy path and the request shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_happy_path() -> None:
    impl = _provider_with_response(_fake_message("oi"))

    assert await impl.generate("olá") == "oi"
    impl._client.messages.create.assert_awaited_once()  # type: ignore[attr-defined]  # noqa: SLF001


@pytest.mark.asyncio
async def test_generate_concatenates_only_text_blocks() -> None:
    """A response may interleave non-text blocks; only ``text`` is joined, and a non-text block
    must not crash the join or leak into the returned string."""
    impl = _provider_with_response(
        _fake_message(
            content=[
                SimpleNamespace(type="text", text="parte 1 "),
                SimpleNamespace(type="thinking", thinking="não deve aparecer"),
                SimpleNamespace(type="text", text="parte 2"),
            ]
        )
    )

    assert await impl.generate("olá") == "parte 1 parte 2"


@pytest.mark.asyncio
async def test_request_carries_the_bedrock_model_id_and_the_prompt() -> None:
    impl = _provider_with_response(_fake_message(), model="global.anthropic.claude-sonnet-5")

    await impl.generate("prompt exato")

    kwargs = impl._client.messages.create.await_args.kwargs  # type: ignore[attr-defined]  # noqa: SLF001
    assert kwargs["model"] == "global.anthropic.claude-sonnet-5"
    assert kwargs["messages"] == [{"role": "user", "content": "prompt exato"}]
    assert kwargs["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_request_sends_none_of_the_parameters_the_current_models_reject() -> None:
    """PINNED AS A WHOLE-KEYWORD-SET EQUALITY, not as four ``not in`` checks.

    ``temperature``/``top_p``/``top_k`` are removed on the current Claude models (sending one is
    a 400), and ``thinking`` must be OMITTED so adaptive thinking — the default on
    ``claude-opus-5`` — applies without a ``budget_tokens`` field that no longer exists. A
    set-equality assertion fails on ANY added keyword, including one nobody thought to forbid
    here, which an allow-list of forbidden names cannot do.
    """
    impl = _provider_with_response(_fake_message())

    await impl.generate("olá")

    kwargs = impl._client.messages.create.await_args.kwargs  # type: ignore[attr-defined]  # noqa: SLF001
    assert set(kwargs) == {"model", "max_tokens", "messages"}


# ---------------------------------------------------------------------------
# Error taxonomy — mirrored from the 1P provider, plus the botocore clause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_is_retryable() -> None:
    impl = _provider_with_response(_fake_message())
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        side_effect=anthropic.RateLimitError("rate limited", response=_fake_httpx_response(429), body=None)
    )

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("olá")

    assert excinfo.value.provider == "bedrock"
    assert excinfo.value.retryable is True
    assert excinfo.value.committed is False


@pytest.mark.asyncio
async def test_authentication_error_is_not_retryable() -> None:
    impl = _provider_with_response(_fake_message())
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        side_effect=anthropic.AuthenticationError("bad sigv4", response=_fake_httpx_response(401), body=None)
    )

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("olá")

    assert excinfo.value.retryable is False


@pytest.mark.asyncio
async def test_timeout_is_retryable() -> None:
    impl = _provider_with_response(_fake_message())
    request = httpx.Request(
        "POST", "https://bedrock-runtime.sa-east-1.amazonaws.com/model/global.anthropic.claude-opus-5/invoke"
    )
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        side_effect=anthropic.APITimeoutError(request=request)
    )

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("olá")

    assert excinfo.value.retryable is True
    assert "timed out" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_connection_error_is_retryable() -> None:
    impl = _provider_with_response(_fake_message())
    request = httpx.Request(
        "POST", "https://bedrock-runtime.sa-east-1.amazonaws.com/model/global.anthropic.claude-opus-5/invoke"
    )
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        side_effect=anthropic.APIConnectionError(request=request)
    )

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("olá")

    assert excinfo.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_retryable"),
    [(500, True), (503, True), (400, False), (403, False), (404, False)],
)
async def test_status_errors_split_on_the_5xx_boundary(status_code: int, expected_retryable: bool) -> None:
    """The ``>= 500`` disposition, exercised on BOTH sides of the boundary.

    ``403``/``404`` are the realistic Bedrock misconfigurations (no IAM permission to invoke
    the model; the model id is not enabled in this region) and must NOT be retried — retrying
    a permission failure just burns the budget.
    """
    impl = _provider_with_response(_fake_message())
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        side_effect=anthropic.APIStatusError("boom", response=_fake_httpx_response(status_code), body=None)
    )

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("olá")

    assert excinfo.value.retryable is expected_retryable


@pytest.mark.asyncio
async def test_botocore_credential_failure_is_wrapped_not_leaked() -> None:
    """THE CLAUSE THE 1P PROVIDER DOES NOT NEED, exercised with the REAL botocore exception.

    SigV4 signing happens inside the request, in botocore, whose exception hierarchy is neither
    an ``anthropic`` error nor something a caller may be asked to import (module docstring: no
    SDK leaks past this module). Without the catch-all clause, a missing AWS credential would
    reach an agent graph as a raw ``NoCredentialsError``.
    """
    impl = _provider_with_response(_fake_message())
    impl._client.messages.create = AsyncMock(side_effect=NoCredentialsError())  # type: ignore[method-assign]  # noqa: SLF001

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("olá")

    assert excinfo.value.provider == "bedrock"
    assert excinfo.value.retryable is False
    assert "NoCredentialsError" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, NoCredentialsError)


@pytest.mark.asyncio
async def test_wrapped_aws_error_message_carries_only_the_exception_type() -> None:
    """Pinned-fields discipline on the wrap: a botocore message can name a profile or an
    assumed-role ARN — deployment detail this error has no need to publish. Only the TYPE name
    crosses the boundary, plus the operator-actionable hint."""

    # The name is botocore's REAL spelling (`botocore.exceptions.ProfileNotFound`), which is the
    # whole point of the double — renaming it to satisfy N818's `*Error` convention would make
    # the assertion below about a type name that never occurs in production.
    class ProfileNotFound(Exception):  # noqa: N818
        pass

    impl = _provider_with_response(_fake_message())
    impl._client.messages.create = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        side_effect=ProfileNotFound("profile 'amh-data-dev' with arn:aws:iam::123456789012:role/x")
    )

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("olá")

    message = str(excinfo.value)
    assert "ProfileNotFound" in message
    assert "amh-data-dev" not in message
    assert "arn:aws:iam" not in message


@pytest.mark.asyncio
async def test_refusal_raises_a_typed_non_retryable_error_without_reading_content() -> None:
    """A classifier refusal is an HTTP 200 whose ``content`` may be EMPTY.

    The empty ``content`` is the point, and the failure it guards against is SILENT rather than
    loud: with no ``stop_reason`` check, the text join over an empty list yields ``""`` and
    ``generate`` would RETURN AN EMPTY STRING as if it were a completion — a refusal reported to
    the caller as a successful, blank answer. Mutation-checked: deleting the ``stop_reason ==
    "refusal"`` branch turns this RED (``DID NOT RAISE``), because the join swallows the empty
    content rather than raising an ``IndexError`` that would have surfaced the bug on its own.
    """
    impl = _provider_with_response(_fake_message(stop_reason="refusal", content=[]))

    with pytest.raises(InferenceProviderError) as excinfo:
        await impl.generate("olá")

    assert excinfo.value.provider == "bedrock"
    assert excinfo.value.retryable is False
    assert "refus" in str(excinfo.value)


# ---------------------------------------------------------------------------
# T8 metering — the SAME seam, labelled `bedrock`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metering_emits_real_usage_to_prometheus() -> None:
    """The REAL counts off the response reach the existing ``maezo_llm_tokens_total`` counter,
    labelled with this provider and its Bedrock model id — never a parallel metrics path."""
    from maezo.runtime.metrics import MetricsCollector

    impl = _provider_with_response(_fake_message("oi", usage=_fake_usage(input_tokens=321, output_tokens=54)))

    collector = MetricsCollector()
    with patch("maezo.platform.observability._get_metrics_collector", return_value=collector):
        assert await impl.generate("olá") == "oi"

    by_type = {s.labels["token_type"]: s for s in _llm_token_samples(collector)}
    assert by_type["input"].value == 321
    assert by_type["output"].value == 54
    assert by_type["input"].labels["provider"] == "bedrock"
    assert by_type["input"].labels["model"] == DEFAULT_BEDROCK_MODEL


@pytest.mark.asyncio
async def test_metering_emits_the_structured_log_with_correlation_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impl = _provider_with_response(_fake_message(usage=_fake_usage(input_tokens=10, output_tokens=7)))
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime.inference.providers.logger", mock_logger)

    await impl.generate("olá", agent_id="helena", tenant_id="tenant-amh")

    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert len(usage_calls) == 1
    kwargs = usage_calls[0].kwargs
    assert kwargs["provider"] == "bedrock"
    assert kwargs["model"] == DEFAULT_BEDROCK_MODEL
    assert kwargs["total_tokens"] == 17
    assert kwargs["agent_id"] == "helena"
    assert kwargs["tenant_id"] == "tenant-amh"


@pytest.mark.asyncio
async def test_metering_prefers_the_model_the_response_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the response carries its own ``.model``, the emission uses THAT — the configured id
    is only a fallback, so a request served by a different model is metered honestly."""
    impl = _provider_with_response(
        _fake_message(
            usage=_fake_usage(input_tokens=5, output_tokens=5), model="global.anthropic.claude-sonnet-5"
        )
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime.inference.providers.logger", mock_logger)

    await impl.generate("olá")

    usage_calls = [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"]
    assert usage_calls[0].kwargs["model"] == "global.anthropic.claude-sonnet-5"


@pytest.mark.asyncio
async def test_metering_still_runs_on_a_refusal() -> None:
    """A refusal is a genuine API response with genuine ``usage`` — metered, even though
    ``generate`` goes on to raise for the refusal itself."""
    from maezo.runtime.metrics import MetricsCollector

    impl = _provider_with_response(
        _fake_message(
            "", stop_reason="refusal", content=[], usage=_fake_usage(input_tokens=8, output_tokens=0)
        )
    )

    collector = MetricsCollector()
    with (
        patch("maezo.platform.observability._get_metrics_collector", return_value=collector),
        pytest.raises(InferenceProviderError, match="refus"),
    ):
        await impl.generate("olá")

    by_type = {s.labels["token_type"]: s for s in _llm_token_samples(collector)}
    assert by_type["input"].value == 8


@pytest.mark.asyncio
async def test_no_metering_when_the_response_carries_no_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent ``usage`` degrades to a skipped emission — never a crash, never a fabricated count
    (constraint 3). ``_fake_message()`` with no ``usage=`` leaves the attribute entirely absent."""
    from maezo.runtime.metrics import MetricsCollector

    impl = _provider_with_response(_fake_message("oi"))
    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime.inference.providers.logger", mock_logger)

    collector = MetricsCollector()
    with patch("maezo.platform.observability._get_metrics_collector", return_value=collector):
        assert await impl.generate("olá") == "oi"  # the call itself was not broken

    assert _llm_token_samples(collector) == []
    assert [c for c in mock_logger.info.call_args_list if c.args and c.args[0] == "llm_token_usage"] == []


@pytest.mark.asyncio
async def test_a_metering_defect_never_breaks_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hard fail-safe: a broken metrics backend must not turn a successful completion into
    a failed ``generate()``."""
    impl = _provider_with_response(_fake_message("oi", usage=_fake_usage()))
    monkeypatch.setattr(
        "maezo.platform.observability.record_llm_token_usage",
        MagicMock(side_effect=RuntimeError("simulated metrics backend outage")),
    )

    assert await impl.generate("olá") == "oi"


@pytest.mark.asyncio
async def test_mock_providers_still_emit_no_metering_alongside_bedrock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION CONTROL for the "mock/fake NEVER meters" rule, re-asserted from this file.

    A new real provider is exactly the change that could tempt a shared metering helper into
    firing for everything. The mocks make no API call, so there is nothing to meter — and the
    Bedrock provider metering in the same test proves the assertion is not vacuous.
    """
    from maezo.runtime.inference import NoopInferenceProvider, PhiZoneMockProvider
    from maezo.runtime.metrics import MetricsCollector

    mock_logger = MagicMock()
    monkeypatch.setattr("maezo.runtime.inference.providers.logger", mock_logger)
    collector = MetricsCollector()
    bedrock = _provider_with_response(_fake_message("oi", usage=_fake_usage(input_tokens=3, output_tokens=1)))

    with patch("maezo.platform.observability._get_metrics_collector", return_value=collector):
        await NoopInferenceProvider().generate("x", agent_id="a", tenant_id="t")
        await PhiZoneMockProvider().generate("x", agent_id="a", tenant_id="t")
        samples_from_mocks = _llm_token_samples(collector)
        await bedrock.generate("x")

    assert samples_from_mocks == []
    assert {s.labels["provider"] for s in _llm_token_samples(collector)} == {"bedrock"}


@pytest.mark.asyncio
async def test_facade_forwards_correlation_ids_to_the_bedrock_provider() -> None:
    """The public facade threads ``agent_id``/``tenant_id`` through unchanged."""
    provider = InferenceProvider(settings=InferenceSettings(provider="bedrock"))
    generate_mock = AsyncMock(return_value="ok")
    provider._impl.generate = generate_mock  # type: ignore[attr-defined, method-assign]  # noqa: SLF001

    await provider.generate("olá", agent_id="rafael", tenant_id="tenant-x")

    generate_mock.assert_awaited_once_with("olá", agent_id="rafael", tenant_id="tenant-x")
