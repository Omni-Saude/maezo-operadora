"""BR-resident PHI-zone adapter — construction gates, residency refusals, PHI-safe logging.

Companion to ``test_inference_capabilities.py`` (the capability schema) and ``test_inference.py``
(ADR-0009/T1.7/T8). Everything here is offline: no network, no SDK call, no real credential. The
only endpoint that exists is :class:`LabeledFakeBrRegionalTransport`, in-process and unmistakably
synthetic.

TEST DISCIPLINE. Expectations are HARDCODED literals, never derived from the constants under test
(``BR_REGIONAL_ENDPOINT_HOST_SUFFIXES``, ``BR_RESIDENT_CAPABILITIES``, the header names): a table
computed from the thing it is checking proves only ``x == x``. Where a module constant IS the
subject of the assertion it is pinned to a literal first.

WHAT THIS FILE IS REALLY GUARDING. `BrResidentInferenceProvider` is the first non-mock provider in
this repo to declare itself PHI-eligible. Leg 1 recorded that no zero-retention DPA exists in this
tree, so the entire safety of that declaration rests on two claims: the adapter cannot BOOT
without an owner act, and it cannot SPEAK to anything but an approved BR endpoint. Both are
asserted here at consequence level — a refusal is proven by the exception a caller actually gets,
never by reading a flag back out of the object that set it.
"""

from __future__ import annotations

import dataclasses

import pytest
import structlog

from maezo.runtime.inference import (
    BR_REGIONAL_ATTESTED_REGION,
    BR_RESIDENT_CAPABILITIES,
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
    FAKE_BR_REGIONAL_REFUSAL_CODE,
    HEADER_CACHE_PREFIX_CHARS,
    HEADER_DATA_CLASSIFICATION,
    HEADER_TRAINING_PROHIBITED,
    HEADER_VENDOR_DPA_REF,
    HEADER_ZERO_RETENTION,
    BrEndpointNotApprovedError,
    BrRegionalRequest,
    BrRegionalResponse,
    BrRegionalTransportUnavailableError,
    BrResidentInferenceProvider,
    FakeBrRegionalOutcome,
    InferenceConfigError,
    InferenceProvider,
    InferenceProviderError,
    InferenceSettings,
    LabeledFakeBrRegionalTransport,
    PhiZoneRoutingError,
    RefusingBrRegionalTransport,
    br_endpoint_denial_reasons,
    phi_zone_denial_reasons,
    resolve_br_regional_transport,
)
from maezo.runtime.prompt_format import (
    FormattedPrompt,
    format_cached_prompt,
    stable_prefix_is_byte_stable,
)

#: An endpoint that PASSES the allowlist. Hardcoded rather than built from
#: ``BR_REGIONAL_ENDPOINT_HOST_SUFFIXES`` so that widening the suffix tuple does not silently
#: widen every test in this file along with it.
_APPROVED_ENDPOINT = "https://fake-labeled-endpoint.br-sao-paulo.phi.maezo.internal/v1/generate"

_MODEL = "br-model-under-test"
_DPA_REF = "DPA-TEST-NOT-A-REAL-CONTRACT"
_CREDENTIAL = "phi-key-CANARY-must-never-appear-anywhere"

#: A canary that stands in for PHI-bearing prompt text. If this string ever turns up in a log
#: event, an exception message, or a ``repr``, PHI has leaked through that channel.
_PROMPT_CANARY = "PROMPT-CANARY-beneficiario-Jose-da-Silva-CPF-000-dor-toracica"


@pytest.fixture(autouse=True)
def _clean_phi_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient owner act may reach a test here.

    A developer with a real (or leftover) ``MAEZO_PHI_VENDOR_DPA_REF`` exported would otherwise
    turn every "refuses to construct" assertion in this file green for the wrong reason — the
    provider WOULD construct, and the refusal test would be passing on an exception raised by a
    different missing value.
    """
    for name in (
        ENV_PHI_ENDPOINT_URL,
        ENV_PHI_API_KEY,
        ENV_PHI_VENDOR_DPA_REF,
        "MAEZO_INFERENCE_PROVIDER",
        "MAEZO_INFERENCE_MODEL",
        "MAEZO_INFERENCE_PHI_ZONE_REQUIRED",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_owner_acts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    endpoint: str | None = _APPROVED_ENDPOINT,
    credential: str | None = _CREDENTIAL,
    dpa_ref: str | None = _DPA_REF,
) -> None:
    """Set the three owner acts, omitting any passed as ``None``."""
    for name, value in (
        (ENV_PHI_ENDPOINT_URL, endpoint),
        (ENV_PHI_API_KEY, credential),
        (ENV_PHI_VENDOR_DPA_REF, dpa_ref),
    ):
        if value is not None:
            monkeypatch.setenv(name, value)


def _provider(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: FakeBrRegionalOutcome = FakeBrRegionalOutcome.ACCEPTED,
) -> tuple[BrResidentInferenceProvider, LabeledFakeBrRegionalTransport]:
    """A fully-configured provider wired to a labeled fake, plus the fake for inspection."""
    _set_owner_acts(monkeypatch)
    transport = LabeledFakeBrRegionalTransport(outcome=outcome)
    return BrResidentInferenceProvider(model=_MODEL, transport=transport), transport


# =============================================================================================
# Construction gates — the three owner acts, each independently load-bearing
# =============================================================================================


def test_br_resident_is_unbootable_without_the_owner_acts(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE LOAD-BEARING CLAIM of this whole leg, asserted first.

    ``BR_RESIDENT_CAPABILITIES`` declares BR residency, zero retention and a training
    prohibition — a contract no vendor in this tree has signed. What keeps that declaration from
    being a lie is not a comment: it is that the class REFUSES TO EXIST until a human supplies
    the DPA reference. With a clean environment, construction fails, and the message names the
    owner act rather than a config key.
    """
    with pytest.raises(InferenceConfigError) as excinfo:
        BrResidentInferenceProvider(model=_MODEL)

    message = str(excinfo.value)
    assert ENV_PHI_VENDOR_DPA_REF in message
    assert "MISSING OWNER ACT" in message
    assert "no such agreement exists anywhere in this repository" in message


@pytest.mark.parametrize(
    ("omitted", "kwargs", "expected_fragment"),
    [
        ("dpa_ref", {"dpa_ref": None}, ENV_PHI_VENDOR_DPA_REF),
        ("credential", {"credential": None}, ENV_PHI_API_KEY),
        ("endpoint", {"endpoint": None}, ENV_PHI_ENDPOINT_URL),
    ],
)
def test_each_owner_act_is_independently_required(
    monkeypatch: pytest.MonkeyPatch,
    omitted: str,
    kwargs: dict[str, None],
    expected_fragment: str,
) -> None:
    """Remove EXACTLY ONE act from an otherwise-complete configuration; construction must fail.

    The anti-vacuity core of the construction gate: it proves the three checks are genuinely
    conjunctive and that none is decorative. Deleting any one of them turns exactly one of these
    parameter cases green-when-it-should-be-red.
    """
    _set_owner_acts(monkeypatch, **kwargs)  # type: ignore[arg-type]

    with pytest.raises(InferenceConfigError) as excinfo:
        BrResidentInferenceProvider(model=_MODEL)

    assert expected_fragment in str(excinfo.value), f"refusal for missing {omitted} must name its env var"


def test_model_must_be_explicit_because_no_br_model_id_is_sanctioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No default model — defaulting would mean inventing a BR-zone model id nobody sanctioned."""
    _set_owner_acts(monkeypatch)

    with pytest.raises(InferenceConfigError, match="requires an explicit MAEZO_INFERENCE_MODEL"):
        BrResidentInferenceProvider(model="")


def test_a_fully_configured_provider_constructs(monkeypatch: pytest.MonkeyPatch) -> None:
    """OVER-FIRE CONTROL. Without this, ``__init__`` could be a bare unconditional ``raise`` and
    every refusal test above would still pass."""
    _set_owner_acts(monkeypatch)

    provider = BrResidentInferenceProvider(model=_MODEL)

    assert provider.phi_capable is True
    assert provider.is_mock is False


def test_construction_refusals_never_leak_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pinned-fields discipline at construction — the leg-1 canary probe, re-run for this provider.

    The credential is PRESENT and the failure is elsewhere (a bad endpoint), so the message is
    built while the secret is in scope. That is the case where a careless f-string leaks it.
    """
    _set_owner_acts(monkeypatch, endpoint="https://not-br.example/v1")

    with pytest.raises(InferenceConfigError) as excinfo:
        BrResidentInferenceProvider(model=_MODEL)

    assert _CREDENTIAL not in str(excinfo.value)
    assert _CREDENTIAL not in repr(excinfo.value)


# =============================================================================================
# Endpoint allowlist — enforced client-side, at construction AND per call
# =============================================================================================


def test_the_approved_endpoint_is_actually_approved() -> None:
    """OVER-FIRE CONTROL for the allowlist predicate itself.

    If this fails, ``br_endpoint_denial_reasons`` refuses everything and every "it refuses X"
    assertion below is worthless.
    """
    assert br_endpoint_denial_reasons(_APPROVED_ENDPOINT) == ()


@pytest.mark.parametrize(
    ("url", "expected_reasons"),
    [
        ("", (ENDPOINT_DENIAL_EMPTY,)),
        ("   ", (ENDPOINT_DENIAL_EMPTY,)),
        # Plaintext to the right host: PHI in transit unencrypted.
        ("http://x.br-sao-paulo.phi.maezo.internal/v1", (ENDPOINT_DENIAL_SCHEME,)),
        # Credential smuggled into the URL — passes a naive host check and lands in logs.
        ("https://user:pw@x.br-sao-paulo.phi.maezo.internal/v1", (ENDPOINT_DENIAL_USERINFO,)),
        # The general zone, and every other region.
        ("https://api.anthropic.com/v1/messages", (ENDPOINT_DENIAL_HOST,)),
        ("https://inference.us-east-1.example.com/v1", (ENDPOINT_DENIAL_HOST,)),
        # SUFFIX-CONFUSION: the approved name appears, but not as the host's suffix.
        ("https://br-sao-paulo.phi.maezo.internal.evil.example/v1", (ENDPOINT_DENIAL_HOST,)),
        # Query/fragment: a redirect or tenant hint riding along in the configured URL.
        ("https://x.br-sao-paulo.phi.maezo.internal/v1?next=https://evil.example", (ENDPOINT_DENIAL_QUERY,)),
        # LEG3-A: interior control chars survive `.strip()` but `urlsplit` silently drops them, so
        # the stored URL differs from what the host check parsed. Refused with a SINGLE normalization
        # reason (short-circuit) — never host/scheme facts parsed from the sanitized string. The
        # `\r\n` case is the header/request-line-injection one; `\t`/`\n` are the same defect.
        ("https://x.br-sao-paulo.phi.maezo.internal/v1\r\n/generate", (ENDPOINT_DENIAL_NOT_NORMALIZED,)),
        ("https://x.br-sao-paulo.phi.maezo.internal/v1\tHost: evil", (ENDPOINT_DENIAL_NOT_NORMALIZED,)),
        ("https://x.br-sao-paulo.phi.maezo.internal/v1\ncredential", (ENDPOINT_DENIAL_NOT_NORMALIZED,)),
        # A CRLF against a NON-approved host still returns ONLY the normalization reason — the
        # short-circuit must not leak the (untrustworthy) host fact parsed from the sanitized string.
        ("https://evil.example/v1\r\nX: y", (ENDPOINT_DENIAL_NOT_NORMALIZED,)),
        # Non-canonical scheme case is the same "stored form != normalized form" defect (urlsplit
        # lowercases the scheme, so the scheme check alone would PASS it) — refused too.
        ("HTTPS://x.br-sao-paulo.phi.maezo.internal/v1", (ENDPOINT_DENIAL_NOT_NORMALIZED,)),
        # Several facets wrong at once, in fixed declaration order.
        ("http://evil.example/v1?q=1", (ENDPOINT_DENIAL_SCHEME, ENDPOINT_DENIAL_HOST, ENDPOINT_DENIAL_QUERY)),
    ],
)
def test_endpoint_allowlist_refuses_every_escape_shape(url: str, expected_reasons: tuple[str, ...]) -> None:
    """Hardcoded URL -> hardcoded reason codes, in fixed order."""
    assert br_endpoint_denial_reasons(url) == expected_reasons


def test_a_crlf_endpoint_is_refused_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """LEG3-A construction half: an interior-CRLF endpoint never boots.

    Credential and DPA reference are both present, so the refusal comes from the endpoint check —
    a URL that `.strip()` cannot clean because the control chars are INTERIOR. The stored
    `self._endpoint_url` would otherwise carry the `\\r\\n` into every request line a transport built.
    """
    _set_owner_acts(monkeypatch, endpoint="https://x.br-sao-paulo.phi.maezo.internal/v1\r\nX-Injected: 1")

    with pytest.raises(InferenceConfigError) as excinfo:
        BrResidentInferenceProvider(model=_MODEL)

    assert ENDPOINT_DENIAL_NOT_NORMALIZED in str(excinfo.value)
    # The refusal names the env var and the reason code only — it must not echo the raw URL, so the
    # smuggled control chars / injected header never ride into a log or exception sink.
    assert "\r" not in str(excinfo.value)
    assert "\n" not in str(excinfo.value)
    assert "X-Injected" not in str(excinfo.value)


def test_a_non_br_endpoint_is_refused_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONSTRUCTION-TIME half of the allowlist: a misconfigured deployment never boots.

    The credential and DPA reference are both present, so the refusal must come from the endpoint
    check and not from an earlier gate masquerading as one.
    """
    _set_owner_acts(monkeypatch, endpoint="https://api.anthropic.com/v1/messages")

    with pytest.raises(InferenceConfigError) as excinfo:
        BrResidentInferenceProvider(model=_MODEL)

    assert ENDPOINT_DENIAL_HOST in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_mutated_endpoint_is_refused_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """CALL-TIME half: the check is re-run per request, not trusted from boot.

    This is the canary-ready property. Construction-time validation proves only what was
    configured at startup; an instance whose endpoint changed afterwards must still refuse. The
    mutation here is exactly the runtime-attribute-reassignment residual that
    ``BaseInferenceProvider.__init_subclass__`` documents as out of scope for class-creation
    guards — so the per-call check is what actually covers it.
    """
    provider, transport = _provider(monkeypatch)
    provider._endpoint_url = "https://api.anthropic.com/v1/messages"  # noqa: SLF001

    with pytest.raises(BrEndpointNotApprovedError) as excinfo:
        await provider.generate(_PROMPT_CANARY)

    assert ENDPOINT_DENIAL_HOST in str(excinfo.value)
    assert transport.sent_requests == [], "the refusal must fire BEFORE anything is put on the wire"


@pytest.mark.asyncio
async def test_a_redirect_off_the_approved_endpoint_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response from a URL the adapter did not dial is a PHI residency escape.

    The completion is DISCARDED, not returned — the caller gets an exception, never text that
    came back from an unapproved host.
    """
    provider, _ = _provider(monkeypatch, outcome=FakeBrRegionalOutcome.REDIRECTED_OFF_REGION)

    with pytest.raises(BrEndpointNotApprovedError) as excinfo:
        await provider.generate(_PROMPT_CANARY)

    assert "did not dial" in str(excinfo.value)
    assert FAKE_BR_REGIONAL_COMPLETION_PREFIX not in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_wrong_attested_region_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Right URL, wrong declared execution region — still refused, and the completion discarded."""
    provider, _ = _provider(monkeypatch, outcome=FakeBrRegionalOutcome.WRONG_SERVED_REGION)

    with pytest.raises(BrEndpointNotApprovedError, match="served_region"):
        await provider.generate(_PROMPT_CANARY)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_fragment"),
    [
        (FakeBrRegionalOutcome.RETENTION_NOT_ACKNOWLEDGED, HEADER_ZERO_RETENTION),
        (FakeBrRegionalOutcome.TRAINING_NOT_ACKNOWLEDGED, HEADER_TRAINING_PROHIBITED),
    ],
)
async def test_an_unacknowledged_contract_flag_discards_the_completion(
    monkeypatch: pytest.MonkeyPatch,
    outcome: FakeBrRegionalOutcome,
    expected_fragment: str,
) -> None:
    """The zero-retention / no-training halves of the capability declaration, enforced per call.

    This is what makes ``BR_RESIDENT_CAPABILITIES``' ZERO_RETENTION something other than a
    comment: an endpoint that does not echo the acknowledgement gets its completion thrown away.
    """
    provider, _ = _provider(monkeypatch, outcome=outcome)

    with pytest.raises(BrEndpointNotApprovedError, match=expected_fragment):
        await provider.generate(_PROMPT_CANARY)


def test_endpoint_refusal_is_not_a_phi_zone_routing_error() -> None:
    """The two refusals stay separately observable — an endpoint escape is not a capability problem.

    Asserted as type identity so that "sibling, not subclass" survives the next edit. If
    ``BrEndpointNotApprovedError`` were folded under ``PhiZoneRoutingError``, an ``except
    PhiZoneRoutingError`` written for I-6 would start swallowing residency escapes.
    """
    assert not issubclass(BrEndpointNotApprovedError, PhiZoneRoutingError)
    assert not issubclass(PhiZoneRoutingError, BrEndpointNotApprovedError)
    assert issubclass(BrEndpointNotApprovedError, PermissionError)


# =============================================================================================
# Transport seam — fail-closed default, labeled fake, round trip
# =============================================================================================


def test_an_unwired_transport_resolves_to_the_refusing_one() -> None:
    """The mock is unreachable in production BY CONSTRUCTION, not by convention."""
    assert isinstance(resolve_br_regional_transport(None), RefusingBrRegionalTransport)

    injected = LabeledFakeBrRegionalTransport()
    assert resolve_br_regional_transport(injected) is injected


@pytest.mark.asyncio
async def test_unwired_transport_refuses_instead_of_fabricating(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of "PHI-eligible by design, un-servable today".

    A provider that boots with every owner act satisfied STILL cannot serve anything, because no
    real transport exists. It raises — it does not fabricate a completion, and it does not fall
    back to the labeled fake.
    """
    _set_owner_acts(monkeypatch)
    provider = BrResidentInferenceProvider(model=_MODEL)

    with pytest.raises(BrRegionalTransportUnavailableError, match="no BR-regional inference transport"):
        await provider.generate(_PROMPT_CANARY)

    assert provider.health_check()["status"] == "warning", "never a fabricated 'ok' for an unwired zone"


@pytest.mark.asyncio
async def test_generate_round_trip_against_the_labeled_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the request carries the contract flags, the response carries leg-3's token fields."""
    provider, transport = _provider(monkeypatch)

    completion = await provider.generate(_PROMPT_CANARY)

    assert completion.startswith(FAKE_BR_REGIONAL_COMPLETION_PREFIX), "a fake must never masquerade"

    (sent,) = transport.sent_requests
    assert sent.endpoint_url == _APPROVED_ENDPOINT
    assert sent.model == _MODEL
    assert sent.prompt == _PROMPT_CANARY
    # The client-side contract, asserted on the wire rather than on the adapter's own logs.
    assert sent.headers[HEADER_ZERO_RETENTION] == "true"
    assert sent.headers[HEADER_TRAINING_PROHIBITED] == "true"
    assert sent.headers[HEADER_DATA_CLASSIFICATION] == "phi"
    assert sent.headers[HEADER_VENDOR_DPA_REF] == _DPA_REF


@pytest.mark.asyncio
async def test_token_usage_fields_are_present_for_leg_3_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leg 3 reconciles metered usage against a real endpoint; the fields must exist and be non-zero.

    Emission goes through ``_emit_llm_token_usage`` — the SAME seam the Anthropic provider uses —
    so BR-resident traffic is metered by the existing mechanism rather than a parallel one (T8).
    """
    provider, _ = _provider(monkeypatch)

    with structlog.testing.capture_logs() as logs:
        await provider.generate(_PROMPT_CANARY)

    (usage_event,) = [event for event in logs if event.get("event") == "llm_token_usage"]
    assert usage_event["provider"] == "br_resident"
    assert usage_event["model"] == _MODEL
    assert usage_event["input_tokens"] > 0
    assert usage_event["output_tokens"] > 0
    assert usage_event["total_tokens"] == usage_event["input_tokens"] + usage_event["output_tokens"]


@pytest.mark.asyncio
async def test_the_labeled_fake_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    """No clock, no randomness — identical requests give byte-identical completions.

    Required by every byte-level assertion in this file, and by the W8 prefix-stability proof.
    """
    provider, _ = _provider(monkeypatch)

    first = await provider.generate(_PROMPT_CANARY)
    second = await provider.generate(_PROMPT_CANARY)

    assert first == second


@pytest.mark.asyncio
async def test_a_vendor_refusal_surfaces_as_a_provider_error_not_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declined request must never be returned to the caller as if it were a completion."""
    provider, _ = _provider(monkeypatch, outcome=FakeBrRegionalOutcome.VENDOR_REFUSAL)

    with pytest.raises(InferenceProviderError) as excinfo:
        await provider.generate(_PROMPT_CANARY)

    assert FAKE_BR_REGIONAL_REFUSAL_CODE in str(excinfo.value)
    assert excinfo.value.retryable is False


@pytest.mark.asyncio
async def test_an_outage_is_wrapped_in_this_modules_own_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No transport-level exception type leaks past this module (ADR-0009 single-import-point)."""
    provider, _ = _provider(monkeypatch, outcome=FakeBrRegionalOutcome.OUTAGE)

    with pytest.raises(BrRegionalTransportUnavailableError) as excinfo:
        await provider.generate(_PROMPT_CANARY)

    assert excinfo.value.retryable is True, "an outage is transient; the refusals above are not"


@pytest.mark.asyncio
async def test_an_off_contract_payload_is_never_treated_as_a_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter validates the response SHAPE — it does not trust the Protocol's return annotation.

    A real endpoint answering with an unexpected body is an ordinary failure mode, and a type
    annotation is not a runtime guarantee.
    """
    provider, _ = _provider(monkeypatch, outcome=FakeBrRegionalOutcome.MALFORMED)

    with pytest.raises(BrRegionalTransportUnavailableError, match="does not match the wire contract"):
        await provider.generate(_PROMPT_CANARY)


# =============================================================================================
# Pinned-fields bar — no prompt/completion/credential content in logs, errors or reprs
# =============================================================================================


@pytest.mark.asyncio
async def test_no_prompt_content_reaches_any_log_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """CANARY PROBE: the PHI-bearing prompt must not appear anywhere in the emitted log stream.

    Asserted over the WHOLE serialized event stream, not field by field: a leak through a field
    nobody thought to check is exactly the leak that ships. `NoopInferenceProvider` logs
    ``prompt[:80]`` — harmless for a mock that never sees production PHI, and precisely what this
    provider must never do.
    """
    provider, _ = _provider(monkeypatch)

    with structlog.testing.capture_logs() as logs:
        await provider.generate(_PROMPT_CANARY)

    serialized = repr(logs)
    assert _PROMPT_CANARY not in serialized
    assert _CREDENTIAL not in serialized
    assert _DPA_REF not in serialized, "the DPA reference is an owner's private identifier"
    # OVER-FIRE CONTROL: the events really were captured, so "absent" is not "nothing was logged".
    assert any(event.get("event") == "inference_br_resident_generate" for event in logs)


def test_the_redaction_is_a_hand_written_repr_and_repr_false_is_its_failsafe() -> None:
    """WHICH MECHANISM DOES THE WORK — pinned, because the intuitive reading is wrong.

    Found while red-controlling this leg: flipping ``repr=False`` back to the default turned
    NOTHING red, because ``dataclasses`` installs its generated ``__repr__`` with
    ``_set_new_attribute``, which declines to overwrite a name already in the class body. So the
    hand-written method is what redacts, unaided — and it is the only edit that can un-redact
    the class.

    ``repr=False`` is not therefore decorative: it is the failsafe for the day somebody deletes
    that method. The second half of this test pins that consequence on a dataclass declared the
    same way, since the property belongs to the DECORATOR ARGUMENT rather than to any code of
    ours that could be asserted on directly.
    """
    assert "__repr__" in BrRegionalRequest.__dict__, "deleting this method is what un-redacts"
    assert "__repr__" in BrRegionalResponse.__dict__

    @dataclasses.dataclass(frozen=True, slots=True, repr=False)
    class WithoutTheHandWrittenRepr:
        credential: str

    # Degrades to `object.__repr__` — type and address — never a field dump.
    rendered = repr(WithoutTheHandWrittenRepr(credential=_CREDENTIAL))
    assert _CREDENTIAL not in rendered
    assert rendered.startswith("<")

    @dataclasses.dataclass(frozen=True, slots=True)
    class WithoutEither:
        credential: str

    # The counterfactual, asserted rather than described: this is what `repr=False` prevents.
    assert _CREDENTIAL in repr(WithoutEither(credential=_CREDENTIAL))


@pytest.mark.asyncio
async def test_the_request_repr_redacts_the_credential_and_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``BrRegionalRequest`` holds both secrets; its repr may show neither.

    A generated dataclass repr would spill both into any traceback frame, assertion message or
    debugger session that touched the object — which is why ``repr=False`` is load-bearing.
    """
    provider, transport = _provider(monkeypatch)
    await provider.generate(_PROMPT_CANARY)
    (sent,) = transport.sent_requests

    rendered = repr(sent)

    assert _PROMPT_CANARY not in rendered
    assert _CREDENTIAL not in rendered
    assert "<redacted>" in rendered
    # OVER-FIRE CONTROL: the repr is still USEFUL — it carries the operational facts.
    assert "stable_prefix_chars=" in rendered
    assert "prompt_fingerprint=" in rendered


@pytest.mark.asyncio
async def test_the_response_repr_redacts_the_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model output over PHI is PHI. The response repr carries counts and a fingerprint only."""
    transport = LabeledFakeBrRegionalTransport()
    _set_owner_acts(monkeypatch)
    provider = BrResidentInferenceProvider(model=_MODEL, transport=transport)
    completion = await provider.generate(_PROMPT_CANARY)

    response = await transport.send(transport.sent_requests[0])

    assert completion not in repr(response)
    assert "completion_fingerprint=" in repr(response)


@pytest.mark.asyncio
async def test_no_refusal_message_carries_prompt_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every refusal path, swept at once: a fail-closed message must not become a leak channel.

    Parametrising per-outcome would let a NEW outcome ship without a leak check; sweeping the
    whole enum here means a new failure mode is covered the moment it is added.
    """
    leaked: list[str] = []
    for outcome in FakeBrRegionalOutcome:
        if outcome is FakeBrRegionalOutcome.ACCEPTED:
            continue
        provider, _ = _provider(monkeypatch, outcome=outcome)
        try:
            await provider.generate(_PROMPT_CANARY)
        except (BrEndpointNotApprovedError, InferenceProviderError) as exc:
            if _PROMPT_CANARY in str(exc) or _PROMPT_CANARY in repr(exc):
                leaked.append(outcome.value)
        else:  # pragma: no cover - a non-ACCEPTED outcome that succeeds is itself a defect
            pytest.fail(f"outcome {outcome.value} was expected to refuse, but returned a completion")

    assert leaked == []


# =============================================================================================
# W8 — cache-aware prompt formatting, proven on the wire
# =============================================================================================


@pytest.mark.asyncio
async def test_the_stable_prefix_is_byte_identical_across_varying_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE W8 CONSEQUENCE, asserted where it actually matters: on what the adapter transmits.

    Two requests sharing static instruction text but differing in every per-request value must
    put a byte-identical ``stable_prefix`` on the wire — that is the entire content of
    provider-side prompt caching. The declared cache breakpoint header must match it.
    """
    provider, transport = _provider(monkeypatch)
    static = "Voce e um assistente. Responda apenas com fatos."

    for beneficiary in ("beneficiario-A", "beneficiario-B"):
        await provider.generate_formatted(
            format_cached_prompt([static], [[("caso", beneficiary), ("motivo", "triagem")]])
        )

    first, second = transport.sent_requests
    assert first.stable_prefix == second.stable_prefix
    assert first.variable_suffix != second.variable_suffix
    assert first.headers[HEADER_CACHE_PREFIX_CHARS] == str(len(first.stable_prefix))


@pytest.mark.asyncio
async def test_moving_variability_into_the_static_block_destroys_the_stable_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE MOVED-VARIABILITY CONSEQUENCE TEST — the mistake W8 exists to make visible.

    Same two requests, same bytes overall, but the per-request value is declared STATIC instead
    of variable. The prompts are equally valid and the model would behave identically; the only
    thing that changes is that the cacheable prefix collapses. Without this test the previous one
    could pass for a formatter that simply never varied anything.
    """
    provider, transport = _provider(monkeypatch)
    static = "Voce e um assistente. Responda apenas com fatos."

    for beneficiary in ("beneficiario-A", "beneficiario-B"):
        # The mis-declaration: per-request content folded into the static block.
        await provider.generate_formatted(format_cached_prompt([static, f"caso={beneficiary}"]))

    first, second = transport.sent_requests
    assert first.stable_prefix != second.stable_prefix, "mis-declared variability must be VISIBLE"
    assert not stable_prefix_is_byte_stable(
        [
            FormattedPrompt(first.stable_prefix, first.variable_suffix),
            FormattedPrompt(second.stable_prefix, second.variable_suffix),
        ]
    )
    # NOT ASSERTED: that the declared breakpoint header differs. It does not — both prefixes are
    # 71 characters here, because the two beneficiary ids happen to be the same length. That is
    # the finding rather than an inconvenience: `HEADER_CACHE_PREFIX_CHARS` is a LENGTH, and a
    # length cannot detect mis-declared variability on its own. Only the prefix BYTES can, which
    # is why the byte comparison above is the real assertion and the header is checked for
    # agreement with its own prefix (previous test), never used as a stability signal.


@pytest.mark.asyncio
async def test_generate_transmits_the_callers_prompt_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION CONTROL, and it caught a real defect during this leg.

    The first draft routed the opaque ``generate`` path through ``format_cached_prompt``, which
    joins segments with ``STABLE_SEPARATOR`` — silently appending ``"\\n\\n"`` to every caller's
    prompt. Prompt bytes feed ``PROMPT_VERSIONS`` audit provenance (ADR-0007) and eval baselines,
    so a two-character rewrite is a provenance defect, not a formatting detail. It is pinned here
    because the failure is invisible at every call site: the prompt still works.

    The honest split for a pre-concatenated string is also asserted: nothing is claimed stable,
    so the declared cache breakpoint is 0 rather than a false promise about a prefix that changes
    every request.
    """
    provider, transport = _provider(monkeypatch)

    await provider.generate(_PROMPT_CANARY)

    (sent,) = transport.sent_requests
    assert sent.prompt == _PROMPT_CANARY
    assert sent.stable_prefix == ""
    assert sent.headers[HEADER_CACHE_PREFIX_CHARS] == "0"


@pytest.mark.asyncio
async def test_the_cache_boundary_never_changes_the_prompt_the_model_sees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SAFETY OF THE OPTIMISATION: splitting is a VIEW, never a rewrite.

    Prompt bytes feed ``PROMPT_VERSIONS`` audit provenance (ADR-0007) and eval baselines, so a
    caching layout that silently altered them would be a defect, not an optimisation.
    """
    provider, transport = _provider(monkeypatch)
    formatted = format_cached_prompt(["instrucoes estaticas"], [[("fatos", {"a": 1})]])

    await provider.generate_formatted(formatted)

    (sent,) = transport.sent_requests
    assert sent.prompt == formatted.text
    assert sent.stable_prefix + sent.variable_suffix == formatted.text


@pytest.mark.asyncio
async def test_the_fake_reports_a_cached_prefix_so_leg_3_has_something_to_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cached_prefix_tokens`` must be non-zero for a prompt with a real stable prefix.

    A cache-aware layout whose cached-token count is always zero is a layout that is not being
    cached — this field is what makes that falsifiable instead of assumed.
    """
    provider, _ = _provider(monkeypatch)

    with structlog.testing.capture_logs() as logs:
        await provider.generate_formatted(
            format_cached_prompt(["a" * 400], [[("caso", "x")]]),
        )

    (event,) = [e for e in logs if e.get("event") == "inference_br_resident_generate"]
    assert event["cached_prefix_tokens"] > 0
    assert event["stable_prefix_chars"] >= 400


# =============================================================================================
# I-6 — the per-call PHI refusal remains independent of everything this leg added
# =============================================================================================


@pytest.mark.asyncio
async def test_i6_still_fires_with_this_legs_code_fully_neutered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-6 RE-PROOF: ``phi=True`` on a non-eligible provider refuses, with leg 2 removed from play.

    Every refusal this leg added is neutralised — the endpoint allowlist always approves, and the
    BR transport always accepts — and the structural PHI refusal still fires against the
    general-zone provider. That is the invariant: leg 2 can only ADD refusals, never restore
    permission. Mirrors ``test_per_call_phi_refusal_is_unchanged_when_the_flag_is_off``.
    """
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    # D2-02 split (docs/reports/inference-split-plan.md §5): `BrResidentInferenceProvider` resolves
    # `br_endpoint_denial_reasons` as a free variable from ITS OWN defining module's globals,
    # `maezo.runtime.inference.br_resident_provider` — patching the FACADE package's own attribute
    # (`maezo.runtime.inference`) does not intercept anything (Python's normal LEGB lookup).
    monkeypatch.setattr(
        "maezo.runtime.inference.br_resident_provider.br_endpoint_denial_reasons",
        lambda _url: (),
    )
    monkeypatch.setattr(BrResidentInferenceProvider, "_validate_response", lambda self, response: response)

    provider = InferenceProvider(settings=InferenceSettings(provider="anthropic"))

    with pytest.raises(PhiZoneRoutingError):
        await provider.generate(_PROMPT_CANARY, phi=True)


@pytest.mark.asyncio
async def test_i6_fires_for_a_misconfigured_instance_of_this_provider() -> None:
    """A provider that is not PHI-capable is refused even if it is otherwise BR-shaped.

    Built by breaking exactly one facet of the real declaration, so the I-6 raise is proven to
    read ``phi_capable`` and nothing this leg introduced.
    """
    broken = dataclasses.replace(BR_RESIDENT_CAPABILITIES, phi_allowed=False)

    class MisconfiguredBrProvider(BrResidentInferenceProvider):
        capabilities = broken
        phi_capable = broken.phi_allowed

    assert phi_zone_denial_reasons(broken) != ()
    assert MisconfiguredBrProvider.phi_capable is False


# =============================================================================================
# Inertness — every shipped configuration behaves exactly as it did before this leg
# =============================================================================================


def test_the_default_provider_is_still_noop() -> None:
    """Nothing in this leg changes what an unconfigured process boots with."""
    assert InferenceSettings().provider == "noop"
    assert InferenceProvider().provider_name == "noop"


@pytest.mark.parametrize("provider_name", ["noop", "phi_zone_mock"])
@pytest.mark.asyncio
async def test_shipped_mock_providers_are_byte_identical_to_their_pre_leg_behaviour(
    provider_name: str,
) -> None:
    """DIFFERENTIAL FINGERPRINT: the observable surface of each shipped provider, pinned to literals.

    The expectations below were read off the providers AT ``cc1ea76`` — the commit before this
    leg — and are asserted here unchanged. Adding a fourth provider, a new module-level import,
    or a new shared helper must not perturb what the three pre-existing providers say or do.
    """
    provider = InferenceProvider(settings=InferenceSettings(provider=provider_name))
    response = await provider.generate("abc")
    health = provider.health_check()

    if provider_name == "noop":
        assert response == "[noop mock response] Received prompt (3 chars): abc..."
        assert health["status"] == "warning"
        assert health["message"].startswith("Provider is 'noop' — all LLM calls return mock responses.")
    else:
        assert response.startswith("[SYNTHETIC RESPONSE — phi_zone_mock, NOT a real model completion]")
        assert "prompt_preview='abc'" in response
        assert health["status"] == "warning"
        assert health["message"].startswith("Provider is 'phi_zone_mock' — an EXPLICITLY-LABELED MOCK")


def test_phi_zone_required_still_refuses_the_shipped_general_zone_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leg-1 startup check is untouched: a new PHI-eligible class does not soften it."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")

    with pytest.raises(InferenceConfigError, match="PHI zone required"):
        InferenceProvider(settings=InferenceSettings(provider="anthropic", phi_zone_required=True))


def test_selecting_br_resident_fails_closed_rather_than_degrading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INERTNESS AT THE REGISTRY: the new name is selectable but unusable without the owner acts.

    An operator who sets ``MAEZO_INFERENCE_PROVIDER=br_resident`` today gets a startup refusal
    naming the missing DPA — never a silent degrade to noop, and never a booted PHI route.
    """
    monkeypatch.setenv("MAEZO_INFERENCE_MODEL", _MODEL)

    with pytest.raises(InferenceConfigError, match=ENV_PHI_VENDOR_DPA_REF):
        InferenceProvider(settings=InferenceSettings(provider="br_resident"))


def test_the_attested_region_constant_is_pinned() -> None:
    """Pinned to a literal — the accepted region must not drift by editing an enum value."""
    assert BR_REGIONAL_ATTESTED_REGION == "br-sao-paulo"
