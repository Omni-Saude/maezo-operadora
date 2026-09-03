"""Synthetic-canary harness for the BR-resident PHI inference adapter (Onda 2, W2 leg 3).

Companion to ``test_inference_br_resident.py`` (leg 2 — construction gates, per-call refusals,
pinned-fields logging) and ``test_inference_capabilities.py`` (the capability schema). This file
is the AUDIT §2 canary bar: it proves the adapter's SAFETY PROPERTIES empirically, against the
in-process :class:`LabeledFakeBrRegionalTransport`, with a RED CONTROL for every defense — a
canary that cannot fail is vacuous.

WHAT A "CANARY" MEANS HERE. Each property below is a claim the PHI zone's safety rests on. A
canary is a test that goes RED the instant that claim stops holding — so for every one we also
demonstrate the NEUTERED defense (monkeypatch the guard off, or feed a lying transport) and assert
the bad observable appears. Two of them (no-fallback, token-reconciliation) are additionally
demonstrated red->green with captured pytest output in the leg-3 report.

EVERYTHING IS OFFLINE AND INERT. No network, no SDK call, no real credential, no real endpoint.
The only endpoint that exists is the labeled fake, which is unmistakably synthetic and unreachable
in production (`resolve_br_regional_transport(None)` is the refusing default). Nothing in this file
selects ``br_resident`` in a shipped config; the process default stays ``noop``
(``test_inertness_the_shipped_default_is_untouched``). Runs in CI with the rest of ``tests/unit``.

HOW THE STACK IS COMPOSED — SANCTIONED PATHS ONLY, NEVER A BYPASS.
  * Adapter-level canaries construct ``BrResidentInferenceProvider(transport=fake)`` DIRECTLY — the
    exact test seam leg 2 built (`LabeledFakeBrRegionalTransport` + the ``transport=`` kwarg +
    `resolve_br_regional_transport`; "tests reach the labeled fake by constructing the provider
    directly").
  * Seam-composed canaries wrap the REAL :class:`InferenceProvider` facade in
    :func:`gate_inference` — the ONLY sanctioned constructor of a `GatedInferenceProvider`
    (`gateway/tool_registry` calls it). To exercise the fake THROUGH the facade+seam we monkeypatch
    the module-global ``resolve_br_regional_transport`` so the sanctioned ``_build_br_resident``
    factory wires the fake — substituting the socket-owning transport is the entire designed
    purpose of that seam; the adapter still runs every residency/attestation check and the gate
    still gates. No private ``_impl`` is swapped, no facade is hand-rolled.

THE HUMAN-ROUTING SEAM THIS HARNESS COMPOSES WITH (property 4). The existing path is NOT reinvented
here. The production PHI call site is `agents/helena/graph.py::_classify_llm`, which does
``raw = await self._llm.generate(prompt, phi=True, ...)`` inside a bare ``except Exception`` that
returns ``(None, reason)``; `classify` then routes ``extraction is None`` to
``next_kind="escalate", escalation_motivo="falha_tecnica"`` -> SP-OP-ESCALATION-001's
``UT_TratarEscalonamento`` (a human task). The load-bearing consequence: an adverse outcome must be
RAISED (so the bare-except catches it and escalates to a human), NEVER RETURNED as a completion
string (which `_classify_llm` would parse as a normal, non-adverse turn — a fail-OPEN silent
mis-route). Property 4 pins exactly that: each outcome raises its terminal disposition, none
returns text, and the refusal messages carry no PHI (so the bounded ``str(exc)[:200]`` reason that
flows into the escalation is content-free — extends property 2).

WHERE LEG 4 (retry budget) PLUGS IN. This harness is retry-budget-READY but adds no retry. The one
retryable disposition is ``OUTAGE`` (`BrRegionalTransportUnavailableError(retryable=True)`); every
other adverse disposition is terminal (refusal ``retryable=False``, malformed ``retryable=False``,
and the residency `BrEndpointNotApprovedError`s are `PermissionError`s with no retry semantics at
all). Leg 4 wraps `_send` and keys its budget on ``InferenceProviderError.retryable``; the
disposition table below (with its ``retryable`` column) is the contract it builds against — a
retry that ever re-dials on a residency escape would flip a pinned ``retryable`` here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog
import yaml

from maezo.gateway.seams import SeamContext, is_gated_seam
from maezo.gateway.seams.inference import GatedInferenceProvider, gate_inference
from maezo.runtime._inference_split import br_resident_provider as br_resident_impl
from maezo.runtime.inference import (
    BR_REGIONAL_ATTESTED_REGION,
    BR_REGIONAL_ENDPOINT_HOST_SUFFIXES,
    ENDPOINT_DENIAL_NOT_NORMALIZED,
    ENV_PHI_API_KEY,
    ENV_PHI_ENDPOINT_URL,
    ENV_PHI_VENDOR_DPA_REF,
    FAKE_BR_REGIONAL_COMPLETION_PREFIX,
    FAKE_BR_REGIONAL_REFUSAL_CODE,
    BrEndpointNotApprovedError,
    BrRegionalRequest,
    BrRegionalResponse,
    BrRegionalTokenUsage,
    BrRegionalTransportUnavailableError,
    BrResidentInferenceProvider,
    FakeBrRegionalOutcome,
    InferenceProvider,
    InferenceProviderError,
    InferenceSettings,
    LabeledFakeBrRegionalTransport,
    NoopInferenceProvider,
    PhiZoneRoutingError,
)

# =================================================================================================
# PART 0 — Harness support (reusable). Canary strings, sanctioned stack builders, the token
# reconciliation predicate, the lying-usage transport, and the neutering helpers the RED controls
# use. Kept separate from the canaries so the machinery is importable and obviously the same across
# every property.
# =================================================================================================

#: Hardcoded, provenance-pinned (same literals as leg 2's `test_inference_br_resident.py`), NOT
#: derived from any constant under test — a table computed from the thing it checks proves x == x.
_APPROVED_ENDPOINT = "https://fake-labeled-endpoint.br-sao-paulo.phi.maezo.internal/v1/generate"
_MODEL = "br-model-under-test"
_DPA_REF = "DPA-TEST-NOT-A-REAL-CONTRACT"
_CREDENTIAL = "phi-key-CANARY-must-never-appear-anywhere"

#: Stands in for PHI-bearing prompt text. If it surfaces in ANY log event, metric, exception or
#: repr on the generate path, PHI has leaked through that channel.
_PROMPT_CANARY = "PROMPT-CANARY-beneficiario-Jose-da-Silva-CPF-000-dor-toracica"


@pytest.fixture(autouse=True)
def _clean_phi_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient owner act may reach a canary here (leg-2's fixture, same reasoning).

    A developer with a leftover ``MAEZO_PHI_*`` exported would otherwise turn a "refuses to
    construct" assertion green for the wrong reason.
    """
    for name in (
        ENV_PHI_ENDPOINT_URL,
        ENV_PHI_API_KEY,
        ENV_PHI_VENDOR_DPA_REF,
        "MAEZO_INFERENCE_PROVIDER",
        "MAEZO_INFERENCE_MODEL",
        "MAEZO_INFERENCE_PHI_ZONE_REQUIRED",
        "MAEZO_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _set_owner_acts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    endpoint: str | None = _APPROVED_ENDPOINT,
    credential: str | None = _CREDENTIAL,
    dpa_ref: str | None = _DPA_REF,
) -> None:
    """Set the three owner acts, omitting any passed as ``None`` (leg-2 helper)."""
    for name, value in (
        (ENV_PHI_ENDPOINT_URL, endpoint),
        (ENV_PHI_API_KEY, credential),
        (ENV_PHI_VENDOR_DPA_REF, dpa_ref),
    ):
        if value is not None:
            monkeypatch.setenv(name, value)


def _br_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: FakeBrRegionalOutcome = FakeBrRegionalOutcome.ACCEPTED,
) -> tuple[BrResidentInferenceProvider, LabeledFakeBrRegionalTransport]:
    """A fully-configured adapter wired to a labeled fake — leg 2's sanctioned direct construction."""
    _set_owner_acts(monkeypatch)
    transport = LabeledFakeBrRegionalTransport(outcome=outcome)
    return BrResidentInferenceProvider(model=_MODEL, transport=transport), transport


def _canary_seam() -> SeamContext:
    """A minimal, VALID `SeamContext` (validated through the real `EffectCall` at construction).

    ``principal="rafael"`` mirrors the seam-proof suite's shipped context. The gate it drives is
    INERT today (no class is enforced), so it subtracts nothing — which is precisely what lets the
    I-6 proof show the refusal firing THROUGH a gate that can only ever subtract.
    """
    return SeamContext(tenant="amh", principal="rafael")


def _gated_br_stack(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: FakeBrRegionalOutcome = FakeBrRegionalOutcome.ACCEPTED,
) -> tuple[GatedInferenceProvider, LabeledFakeBrRegionalTransport]:
    """The FULL sanctioned stack: ``gate_inference(InferenceProvider(br_resident), seam)`` over the fake.

    The facade is built by its REAL constructor through the REAL registry (`_build_br_resident`);
    only the socket-owning transport is substituted, via the module-global
    ``resolve_br_regional_transport`` — the designed transport seam. No ``_impl`` swap, no
    hand-rolled facade. This is "the seam wraps whatever provider you exercise".
    """
    _set_owner_acts(monkeypatch)
    transport = LabeledFakeBrRegionalTransport(outcome=outcome)
    # D2-02 split (docs/reports/inference-split-plan.md §5 step 7): `BrResidentInferenceProvider`
    # resolves `resolve_br_regional_transport` as a free variable from ITS OWN defining module
    # (`_inference_split.br_resident_provider`, since step 7), not from the `inf` (=
    # `maezo.runtime.inference`) alias — patching the old module object stopped intercepting
    # anything the moment the class moved (same LEGB finding as step 6's logger fix).
    monkeypatch.setattr(br_resident_impl, "resolve_br_regional_transport", lambda _t: transport)
    facade = InferenceProvider(settings=InferenceSettings(provider="br_resident", model=_MODEL))
    gated = gate_inference(facade, _canary_seam())
    return gated, transport


# --- Token reconciliation ------------------------------------------------------------------------
#
# WHAT RECONCILIATION MEANS, PRECISELY. The adapter deliberately does NOT reconcile tokens (leg 2);
# reconciling provider-REPORTED usage against a LOCALLY-computable expectation is a canary /
# monitoring concern, and this is where it lives. "Locally computable" cannot mean a token count —
# this codebase owns no tokenizer, on purpose (`prompt_format.stable_prefix_chars` docstring). So
# reconciliation is the set of invariants that hold for ANY honest provider REGARDLESS of tokenizer:
#
#   * cached_prefix_tokens <= input_tokens — the cached region is a PREFIX OF the input; reporting
#     more cache-served tokens than total input tokens is arithmetically impossible.
#   * every count >= 0.
#   * a non-empty prompt meters to >= 1 input token (an honest tokenizer never returns 0 for text).
#
# WHAT A DISCREPANCY SIGNALS. `RECON_CACHED_EXCEEDS_INPUT` means the usage report is internally
# impossible: the endpoint is misreporting (a trust/attestation problem — see LEG3-C, a dishonest
# counterparty is not client-closable) or the reconciliation wiring is broken. Either way the
# completion's usage is untrustworthy and the call routes to incident rather than being metered as
# truth. `RECON_INPUT_NONPOSITIVE_FOR_NONEMPTY` means a non-empty PHI prompt metered as zero — a
# billing/observability blind spot. Both are LOUD canary failures, never soft-passed.

RECON_NEGATIVE_TOKENS = "recon_negative_token_count"
RECON_CACHED_EXCEEDS_INPUT = "recon_cached_prefix_exceeds_input"
RECON_INPUT_NONPOSITIVE_FOR_NONEMPTY = "recon_input_nonpositive_for_nonempty_prompt"


def reconcile_token_usage(*, usage: BrRegionalTokenUsage, prompt_is_empty: bool) -> tuple[str, ...]:
    """Every tokenizer-independent invariant ``usage`` violates. Empty tuple == reconciled.

    Fixed declaration order, so a failure message is deterministic and diffable — same discipline
    as :func:`br_endpoint_denial_reasons`.
    """
    reasons: list[str] = []
    if usage.input_tokens < 0 or usage.output_tokens < 0 or usage.cached_prefix_tokens < 0:
        reasons.append(RECON_NEGATIVE_TOKENS)
    if usage.cached_prefix_tokens > usage.input_tokens:
        reasons.append(RECON_CACHED_EXCEEDS_INPUT)
    if not prompt_is_empty and usage.input_tokens < 1:
        reasons.append(RECON_INPUT_NONPOSITIVE_FOR_NONEMPTY)
    return tuple(reasons)


class _LyingUsageTransport:
    """A BR-regional transport that is well-formed, on-endpoint and fully attested — but reports an
    IMPOSSIBLE usage (``cached_prefix_tokens`` > ``input_tokens``).

    This is the reconciliation RED control and the concrete face of LEG3-C: the adapter's
    residency/attestation checks all PASS (a dishonest counterparty defeats them), so the completion
    is returned — and ONLY the token-reconciliation canary catches the lie. Deterministic; no clock,
    no randomness.
    """

    def __init__(self) -> None:
        self.sent_requests: list[BrRegionalRequest] = []

    async def send(self, request: BrRegionalRequest) -> BrRegionalResponse:
        self.sent_requests.append(request)
        return BrRegionalResponse(
            completion=f"{FAKE_BR_REGIONAL_COMPLETION_PREFIX} lying-usage",
            model=request.model,
            usage=BrRegionalTokenUsage(input_tokens=1, output_tokens=1, cached_prefix_tokens=999),
            endpoint_url=request.endpoint_url,
            served_region=BR_REGIONAL_ATTESTED_REGION,
            zero_retention_acknowledged=True,
            training_prohibited_acknowledged=True,
            synthetic=True,
        )


def _neuter_endpoint_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the endpoint allowlist into an always-approve — the shape a fallback would need."""
    # D2-02 split note: see `_seam_over_fake`'s comment above — same module-move reasoning.
    monkeypatch.setattr(br_resident_impl, "br_endpoint_denial_reasons", lambda _url: ())


# =================================================================================================
# PART 1 — Property 1: requests reach ONLY the approved regional endpoint; ZERO general-zone
# fallback. Two independent defenses: the endpoint allowlist (BR adapter) and the phi=/phi_capable
# refusal (facade). Each with its neutered-defense RED control.
# =================================================================================================


async def test_p1_every_request_reaches_only_the_approved_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The happy path puts exactly one request on the wire, to the approved endpoint and nowhere else."""
    adapter, transport = _br_adapter(monkeypatch)

    await adapter.generate(_PROMPT_CANARY)

    assert [r.endpoint_url for r in transport.sent_requests] == [_APPROVED_ENDPOINT]


async def test_p1_a_mutated_endpoint_puts_nothing_on_the_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-fallback, per call: an endpoint mutated off the allowlist refuses BEFORE dialling.

    The refusal is `BrEndpointNotApprovedError` (a residency escape), and — critically — the
    transport saw ZERO requests: PHI never left for the wrong host.
    """
    adapter, transport = _br_adapter(monkeypatch)
    adapter._endpoint_url = "https://api.anthropic.com/v1/messages"  # noqa: SLF001 — the mutation IS the probe

    with pytest.raises(BrEndpointNotApprovedError):
        await adapter.generate(_PROMPT_CANARY)

    assert transport.sent_requests == [], "the refusal must fire before anything reaches the wire"


async def test_p1_red_control_neutered_allowlist_would_dial_the_wrong_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED CONTROL (no-fallback): with the allowlist neutered, the mutated endpoint IS dialled.

    This is what proves the previous canary is not vacuous — with the ONE defense removed, PHI
    reaches ``api.anthropic.com``. The demonstrated red->green run in the leg-3 report neuters the
    real `br_endpoint_denial_reasons` and shows `test_p1_a_mutated_endpoint_puts_nothing_on_the_wire`
    turn red.
    """
    adapter, transport = _br_adapter(monkeypatch)
    adapter._endpoint_url = "https://api.anthropic.com/v1/messages"  # noqa: SLF001
    _neuter_endpoint_allowlist(monkeypatch)

    # `br_endpoint_denial_reasons` guards BOTH the pre-dial check AND the redirect check, so neutering
    # it removes every endpoint defense at once: the call now SUCCEEDS and PHI reaches the wrong host.
    # That completed leak — a completion returned from api.anthropic.com — is the whole point of the
    # control: it is exactly what the intact allowlist prevents.
    completion = await adapter.generate(_PROMPT_CANARY)
    assert completion.startswith(FAKE_BR_REGIONAL_COMPLETION_PREFIX)
    assert [r.endpoint_url for r in transport.sent_requests] == ["https://api.anthropic.com/v1/messages"]


async def test_p1_a_phi_request_never_reaches_the_general_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """No code path from a PHI request to the general (noop/anthropic) provider's model.

    The facade over the non-PHI-capable ``noop`` refuses ``phi=True`` structurally, and the general
    provider's ``generate`` is NEVER entered — proven with a spy, not by absence of output.
    """
    called: list[bool] = []
    real_generate = NoopInferenceProvider.generate

    async def _spy(self: NoopInferenceProvider, prompt: str, **kw: object) -> str:
        called.append(True)
        return await real_generate(self, prompt, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(NoopInferenceProvider, "generate", _spy)
    facade = InferenceProvider(settings=InferenceSettings(provider="noop"))

    with pytest.raises(PhiZoneRoutingError):
        await facade.generate(_PROMPT_CANARY, phi=True)
    assert called == [], "a PHI request must never reach the general provider's generate()"

    # OVER-FIRE CONTROL: the spy really is wired — a NON-PHI request DOES reach the provider.
    await facade.generate(_PROMPT_CANARY, phi=False)
    assert called == [True]


async def test_p1_red_control_neutered_phi_check_lets_phi_reach_general(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED CONTROL: make ``noop`` claim PHI capability, and the PHI request reaches its model.

    Neutering the ``phi_capable`` half of the facade's refusal is exactly the "could it fall back?"
    probe — with it neutered, ``phi=True`` flows straight into the general provider.
    """
    called: list[bool] = []

    async def _spy(self: NoopInferenceProvider, prompt: str, **kw: object) -> str:
        called.append(True)
        return "reached"

    monkeypatch.setattr(NoopInferenceProvider, "generate", _spy)
    monkeypatch.setattr(NoopInferenceProvider, "phi_capable", True)
    facade = InferenceProvider(settings=InferenceSettings(provider="noop"))

    await facade.generate(_PROMPT_CANARY, phi=True)
    assert called == [True], (
        "with phi_capable neutered, PHI reaches the general provider — canary is load-bearing"
    )


# =================================================================================================
# PART 2 — Property 2: ZERO content in logs AND metrics on the full PHI generate path. Extends
# leg 2's log-sweep to the seam-composed stack and to the token-metric surface.
# =================================================================================================


async def test_p2_no_content_reaches_any_log_event_through_the_composed_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full PHI generate through ``gate_inference(facade, seam)`` — sweep the WHOLE log stream.

    Asserted over the serialized events, not field-by-field: a leak through a field nobody thought
    to check is the leak that ships. The seam adds its own shadow line, so this covers the gate's
    surface too, not just the adapter's.
    """
    gated, _ = _gated_br_stack(monkeypatch)

    with structlog.testing.capture_logs() as logs:
        await gated.generate(_PROMPT_CANARY, phi=True)

    serialized = repr(logs)
    assert _PROMPT_CANARY not in serialized
    assert _CREDENTIAL not in serialized
    assert _DPA_REF not in serialized
    # OVER-FIRE CONTROL: the events really were captured, so "absent" is not "nothing was logged".
    assert any(e.get("event") == "inference_br_resident_generate" for e in logs)
    # NON-VACUITY of the sweep itself: it WOULD catch the canary if a field carried it.
    assert _PROMPT_CANARY in repr([{"event": "leak", "prompt": _PROMPT_CANARY}])


async def test_p2_no_content_reaches_the_token_metric_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Prometheus metric emitted on the PHI path carries counts + bounded labels only.

    Spy on the real ``record_llm_token_usage`` (imported locally by `_emit_llm_token_usage`, so we
    patch it at its source module) and sweep every argument for content. Plus: the metric's label
    set is pinned to a hardcoded literal — a per-instance label would recreate the leak in metrics.
    """
    captured: list[dict[str, object]] = []

    def _spy(**kwargs: object) -> None:
        captured.append(kwargs)

    monkeypatch.setattr("maezo.platform.observability.record_llm_token_usage", _spy)
    adapter, _ = _br_adapter(monkeypatch)

    await adapter.generate(_PROMPT_CANARY)

    (call,) = captured
    swept = repr(call)
    assert _PROMPT_CANARY not in swept
    assert _CREDENTIAL not in swept
    assert _DPA_REF not in swept
    assert FAKE_BR_REGIONAL_COMPLETION_PREFIX not in swept, "the completion is model output over PHI"
    # Only the bounded, content-free arguments are present.
    assert set(call) == {"provider", "model", "input_tokens", "output_tokens"}
    assert call["provider"] == "br_resident"
    assert call["model"] == _MODEL
    assert isinstance(call["input_tokens"], int) and isinstance(call["output_tokens"], int)


def test_p2_the_metric_labels_are_a_pinned_content_free_set() -> None:
    """The ``maezo_llm_tokens_total`` label set is exactly ``(provider, model, token_type)``.

    Hardcoded literal, with provenance: read off `platform/observability.py` — the only three labels
    it declares (module docstring: "Per-instance correlation is NEVER a label here"). A tenant/agent
    label appearing would recreate, in Prometheus, exactly the leak the pinned-fields discipline
    keeps out of the logs.
    """
    from maezo.platform.observability import _get_metrics_collector

    assert _get_metrics_collector().llm_tokens._labelnames == ("provider", "model", "token_type")


# =================================================================================================
# PART 3 — Property 3: token reconciliation. Provider-reported usage vs. the tokenizer-independent
# invariants that hold for any honest endpoint, plus an exact hardcoded-provenance check of the
# deterministic fake. RED control: a lying-usage transport.
# =================================================================================================


async def test_p3_reported_usage_reconciles_for_the_honest_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """The honest fake's usage satisfies every reconciliation invariant."""
    adapter, transport = _br_adapter(monkeypatch)

    await adapter.generate(_PROMPT_CANARY)
    (sent,) = transport.sent_requests
    # We reach the usage the SAME way the adapter did — replay the deterministic fake once more.
    response = await transport.send(sent)

    assert reconcile_token_usage(usage=response.usage, prompt_is_empty=False) == ()


async def test_p3_exact_reported_usage_matches_the_pinned_fake_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact usage for a KNOWN request, against a HARDCODED provenance table (not derived at runtime).

    Provenance: `LabeledFakeBrRegionalTransport` @372ead3 divides chars by ``_FAKE_CHARS_PER_TOKEN``
    (pinned to 4). A prompt of exactly 80 chars through ``generate`` (stable_prefix="") therefore
    reports input=80//4=20 and cached=0. The literals below are computed BY HAND from that contract;
    if the fake's divisor drifts, this canary fails loudly rather than silently re-deriving.
    """
    adapter, transport = _br_adapter(monkeypatch)

    await adapter.generate("x" * 80)
    (sent,) = transport.sent_requests
    usage = (await transport.send(sent)).usage

    assert usage.input_tokens == 20, "80 chars // divisor-4-@372ead3"
    assert usage.cached_prefix_tokens == 0, "generate() declares nothing stable, so no cached prefix"
    assert usage.output_tokens > 0, "output is derived from a constant under test; only its sign is pinned"


def test_p3_red_control_a_lying_usage_report_is_caught() -> None:
    """RED CONTROL (reconciliation): ``cached_prefix_tokens`` > ``input_tokens`` is impossible.

    The lying transport passes every residency/attestation check the adapter runs (LEG3-C), so only
    reconciliation stands between a misreported usage and it being metered as truth. The demonstrated
    red->green run neuters `reconcile_token_usage` and shows this canary turn red.
    """
    lying = BrRegionalTokenUsage(input_tokens=1, output_tokens=1, cached_prefix_tokens=999)

    assert reconcile_token_usage(usage=lying, prompt_is_empty=False) == (RECON_CACHED_EXCEEDS_INPUT,)


async def test_p3_a_lying_endpoint_defeats_the_adapter_but_not_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """END-TO-END LEG3-C: the adapter RETURNS the lying completion; reconciliation is what catches it.

    Demonstrates the disclosed residual — client-side residency/attestation cannot detect a
    counterparty that lies in the numbers — and that the canary layer above the adapter does.
    """
    _set_owner_acts(monkeypatch)
    lying = _LyingUsageTransport()
    adapter = BrResidentInferenceProvider(model=_MODEL, transport=lying)

    completion = await adapter.generate(_PROMPT_CANARY)  # adapter is fooled — no exception
    assert completion.startswith(FAKE_BR_REGIONAL_COMPLETION_PREFIX)

    (sent,) = lying.sent_requests
    usage = (await _LyingUsageTransport().send(sent)).usage  # replay the same deterministic lie
    assert reconcile_token_usage(usage=usage, prompt_is_empty=False) == (RECON_CACHED_EXCEEDS_INPUT,)


async def test_p3_cached_prefix_is_nonzero_for_a_genuinely_cached_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W8 tie-in: a real stable prefix yields a non-zero ``cached_prefix_tokens``.

    A cache-aware layout whose cached count is always zero is a layout that is not being cached —
    this is the reconciliation field that makes that falsifiable rather than assumed (over-fire:
    a 400-char prefix must reconcile to cached > 0 AND cached <= input).
    """
    from maezo.runtime.prompt_format import format_cached_prompt

    adapter, transport = _br_adapter(monkeypatch)

    await adapter.generate_formatted(format_cached_prompt(["a" * 400], [[("caso", "x")]]))
    (sent,) = transport.sent_requests
    usage = (await transport.send(sent)).usage

    assert usage.cached_prefix_tokens > 0
    assert reconcile_token_usage(usage=usage, prompt_is_empty=False) == ()


# =================================================================================================
# PART 4 — Property 4: provider refusal / outage / every adverse outcome -> the correct terminal
# disposition, RAISED (so the human-routing seam escalates), never RETURNED (which would silently
# read as a normal turn). Composed with the existing seam; the parallel escalation path is NOT
# reinvented.
# =================================================================================================

#: The pinned disposition table. Provenance: `BrResidentInferenceProvider._validate_response` /
#: `_send` and `LabeledFakeBrRegionalTransport.send` @372ead3 (+ LEG3-A). ``retryable`` is the
#: leg-4 contract column — ONLY the outage is retryable; every structural denial is terminal.
_DISPOSITIONS: tuple[tuple[FakeBrRegionalOutcome, type[Exception], bool | None], ...] = (
    (FakeBrRegionalOutcome.VENDOR_REFUSAL, InferenceProviderError, False),
    (FakeBrRegionalOutcome.OUTAGE, BrRegionalTransportUnavailableError, True),
    (FakeBrRegionalOutcome.MALFORMED, BrRegionalTransportUnavailableError, False),
    (FakeBrRegionalOutcome.REDIRECTED_OFF_REGION, BrEndpointNotApprovedError, None),
    (FakeBrRegionalOutcome.WRONG_SERVED_REGION, BrEndpointNotApprovedError, None),
    (FakeBrRegionalOutcome.RETENTION_NOT_ACKNOWLEDGED, BrEndpointNotApprovedError, None),
    (FakeBrRegionalOutcome.TRAINING_NOT_ACKNOWLEDGED, BrEndpointNotApprovedError, None),
)


@pytest.mark.parametrize(("outcome", "exc_type", "retryable"), _DISPOSITIONS)
async def test_p4_each_adverse_outcome_raises_its_terminal_disposition(
    monkeypatch: pytest.MonkeyPatch,
    outcome: FakeBrRegionalOutcome,
    exc_type: type[Exception],
    retryable: bool | None,
) -> None:
    """Every adverse outcome raises EXACTLY its pinned terminal type — and the ``retryable`` flag
    (leg-4's budget key) is what the table says it is."""
    adapter, _ = _br_adapter(monkeypatch, outcome=outcome)

    with pytest.raises(exc_type) as excinfo:
        await adapter.generate(_PROMPT_CANARY)

    if retryable is not None:
        assert isinstance(excinfo.value, InferenceProviderError)
        assert excinfo.value.retryable is retryable


async def test_p4_no_adverse_outcome_ever_returns_a_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE FAIL-OPEN GUARD, swept over every adverse outcome.

    A RETURNED completion would be read by `helena/graph._classify_llm` as a normal classification
    (`raw = await self._llm.generate(...)`) and routed as an ordinary turn — never escalated. Only a
    RAISED exception lands in that bare-except and becomes ``falha_tecnica`` -> a human. So every
    adverse outcome must raise; a new outcome that returned text would be caught here the moment it
    was added (the sweep is over the enum, not a per-outcome list).
    """
    returned: list[str] = []
    for outcome in FakeBrRegionalOutcome:
        if outcome is FakeBrRegionalOutcome.ACCEPTED:
            continue
        adapter, _ = _br_adapter(monkeypatch, outcome=outcome)
        try:
            result = await adapter.generate(_PROMPT_CANARY)
        except (BrEndpointNotApprovedError, InferenceProviderError):
            pass  # raised == routed to a human; correct
        else:
            returned.append(f"{outcome.value}->{result[:24]!r}")

    assert returned == [], "an adverse outcome that RETURNS text would be silently read as a normal turn"


def test_p4_every_disposition_is_a_human_escalatable_exception() -> None:
    """Each terminal type is an ``Exception`` the human-routing seam catches, with I-6 kept separate.

    `helena/graph._classify_llm` wraps generate in a bare ``except Exception`` -> escalate
    ``falha_tecnica``, so any of these reaches a human. And every residency escape stays
    `BrEndpointNotApprovedError` — a `PermissionError`, but NEVER a `PhiZoneRoutingError` subclass, so
    an ``except PhiZoneRoutingError`` written for I-6 can never start swallowing a residency escape.
    """
    for _outcome, exc_type, _retryable in _DISPOSITIONS:
        assert issubclass(exc_type, Exception)
    assert issubclass(BrEndpointNotApprovedError, PermissionError)
    assert not issubclass(BrEndpointNotApprovedError, PhiZoneRoutingError)
    assert not issubclass(PhiZoneRoutingError, BrEndpointNotApprovedError)


@pytest.mark.parametrize(
    ("outcome", "exc_type"),
    [
        (FakeBrRegionalOutcome.VENDOR_REFUSAL, InferenceProviderError),
        (FakeBrRegionalOutcome.REDIRECTED_OFF_REGION, BrEndpointNotApprovedError),
    ],
)
async def test_p4_refusals_propagate_unswallowed_through_the_composed_seam(
    monkeypatch: pytest.MonkeyPatch,
    outcome: FakeBrRegionalOutcome,
    exc_type: type[Exception],
) -> None:
    """COMPOSITION, not bypass: the SAME terminal type propagates through ``gate_inference``.

    A gate that wrapped the delegation in try/except could convert a structural refusal into a soft
    outcome (and defeat escalation). The delegation is BARE (I-6), so the refusal a caller gets from
    the gated stack is identical to the ungated one — proven here for both an `InferenceProviderError`
    and a residency `BrEndpointNotApprovedError`.
    """
    gated, _ = _gated_br_stack(monkeypatch, outcome=outcome)

    with pytest.raises(exc_type):
        await gated.generate(_PROMPT_CANARY, phi=True)


async def test_p4_vendor_refusal_carries_a_code_not_the_refused_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal surfaces a CODE; the bounded ``str(exc)`` that flows into the escalation reason is
    content-free (ties property 2 to the human seam)."""
    adapter, _ = _br_adapter(monkeypatch, outcome=FakeBrRegionalOutcome.VENDOR_REFUSAL)

    with pytest.raises(InferenceProviderError) as excinfo:
        await adapter.generate(_PROMPT_CANARY)

    assert FAKE_BR_REGIONAL_REFUSAL_CODE in str(excinfo.value)
    assert _PROMPT_CANARY not in str(excinfo.value)


async def test_p4_red_control_a_swallowed_refusal_would_return_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """RED CONTROL: neuter `_validate_response`, and a residency escape RETURNS a completion.

    This is the fail-open the disposition canaries guard: with validation neutered, the
    redirected-off-region response is handed back as text — which `_classify_llm` would read as a
    normal turn and never escalate. The neutered path returns; the intact path raises.
    """
    adapter, _ = _br_adapter(monkeypatch, outcome=FakeBrRegionalOutcome.REDIRECTED_OFF_REGION)
    monkeypatch.setattr(BrResidentInferenceProvider, "_validate_response", lambda self, response: response)

    result = await adapter.generate(_PROMPT_CANARY)
    assert result.startswith(FAKE_BR_REGIONAL_COMPLETION_PREFIX), (
        "neutered validation makes a residency escape RETURN text — the disposition canary is load-bearing"
    )


# =================================================================================================
# PART 5 — LEG3-A (CRLF end-to-end wire proof) and LEG3-B (client allowlist <-> NetworkPolicy).
# =================================================================================================


async def test_p5a_a_crlf_endpoint_produces_no_request_on_the_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """LEG3-A consequence: an interior-CRLF endpoint cannot put a request on the wire.

    The construction refusal is unit-tested in `test_inference_br_resident.py`; here is the
    end-to-end proof the harness owes. The provider boots on an approved endpoint, then its stored
    URL is mutated to carry an interior ``\\r\\n`` (exactly the `.strip()`-surviving case): generate
    refuses per-call and the transport sees NOTHING — the CRLF never reaches a request line.
    """
    adapter, transport = _br_adapter(monkeypatch)
    adapter._endpoint_url = (  # noqa: SLF001 — simulate the stored-URL control-char case
        "https://x.br-sao-paulo.phi.maezo.internal/v1\r\nX-Injected: 1"
    )

    with pytest.raises(BrEndpointNotApprovedError) as excinfo:
        await adapter.generate(_PROMPT_CANARY)

    assert ENDPOINT_DENIAL_NOT_NORMALIZED in str(excinfo.value)
    assert transport.sent_requests == [], "a CRLF endpoint must never produce a request"
    assert "\r" not in str(excinfo.value) and "\n" not in str(excinfo.value)


async def test_p5a_red_control_neutered_allowlist_would_send_the_crlf_on_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED CONTROL (LEG3-A): with the allowlist neutered, the CRLF endpoint IS dialled.

    Proves the CRLF canary is load-bearing — the interior control chars ride into the request the
    transport records.
    """
    adapter, transport = _br_adapter(monkeypatch)
    crlf = "https://x.br-sao-paulo.phi.maezo.internal/v1\r\nX-Injected: 1"
    adapter._endpoint_url = crlf  # noqa: SLF001
    _neuter_endpoint_allowlist(monkeypatch)

    # Neutering the pre-dial allowlist also neuters the redirect check (both call the same function),
    # so the neutered call now SUCCEEDS and the CRLF-bearing endpoint reached the wire.
    await adapter.generate(_PROMPT_CANARY)
    assert transport.sent_requests[0].endpoint_url == crlf, "the neutered defense let the CRLF onto the wire"


# --- LEG3-B: client allowlist <-> NetworkPolicy egress ------------------------------------------
#
# Provenance for the hosts below is read from the Helm values at scan time, NOT hardcoded, so the
# canary tracks the deployed artifacts rather than a snapshot of them.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PHI_VALUES_FILES = (
    _REPO_ROOT / "deploy/helm/maezo-tenant/values-amh.yaml",
    _REPO_ROOT / "deploy/helm/maezo-tenant/values-staging.yaml",
)

_LEG3B_DRIFT_REASON = (
    "LEG3-B: no shared source-of-truth artifact ties the client host-suffix allowlist "
    "(BR_REGIONAL_ENDPOINT_HOST_SUFFIXES) to the ADR-0017 NetworkPolicy egress fence, and the two "
    "places that DO name PHI endpoint hosts already DIVERGE. The client allowlist pins "
    "'.br-sao-paulo.phi.maezo.internal' (deliberately non-routable), while "
    "deploy/helm/.../values-{amh,staging}.yaml declare 'llm-phi.internal.amh.com.br' / "
    "'llm-phi.staging.internal.maezo.com.br'. Worse, the NetworkPolicy egress rule is IP/CIDR-based "
    "and CANNOT express a hostname allowlist at all (networkpolicy.yaml FIX 1), so the two artifacts "
    "are structurally incommensurable — the `host` in values.yaml is only an error-message hint, the "
    "rendered rule is a `cidr`. Faking agreement would be dishonest. This xfail(strict) documents the "
    "drift risk and turns RED the day someone reconciles them (remove the marker then). Deriving the "
    "allowlist from the CIDR-pinning artifact is the open question in runtime/inference.py's "
    "BR_REGIONAL_ENDPOINT_HOST_SUFFIXES comment; inventing that artifact is out of leg-3 scope."
)


def _declared_phi_endpoint_hosts() -> list[str]:
    """Every BR-resident PHI endpoint host declared in the Helm PHI-zone values."""
    hosts: list[str] = []
    for path in _PHI_VALUES_FILES:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        endpoints = (((data or {}).get("networkPolicy") or {}).get("phiZone") or {}).get(
            "brResidentEndpoints"
        ) or []
        hosts.extend(ep["host"] for ep in endpoints if isinstance(ep, dict) and ep.get("host"))
    return hosts


def test_leg3b_the_networkpolicy_hosts_are_actually_declared() -> None:
    """OVER-FIRE CONTROL for the xfail below: the values files really DO declare PHI hosts.

    Without this, the drift canary could 'pass' (find agreement) simply because it read zero hosts —
    a vacuous green. This pins that there is real content to disagree with.
    """
    hosts = _declared_phi_endpoint_hosts()
    assert "llm-phi.internal.amh.com.br" in hosts
    assert "llm-phi.staging.internal.maezo.com.br" in hosts


@pytest.mark.xfail(reason=_LEG3B_DRIFT_REASON, strict=True)
def test_leg3b_client_allowlist_and_networkpolicy_hosts_agree() -> None:
    """DOCUMENTED DRIFT (xfail, strict): every deployed PHI endpoint host matches the client allowlist.

    FAILS today, on purpose — the hosts diverge and the artifacts are incommensurable (see
    ``_LEG3B_DRIFT_REASON``). ``strict=True`` means the day someone reconciles the two sides this
    test XPASSes and the suite goes red, forcing the marker's removal — so the reconciliation cannot
    land silently while a stale "documented drift" marker lingers.
    """
    hosts = _declared_phi_endpoint_hosts()
    assert hosts, "over-fire is a separate test; this asserts agreement, not presence"
    for host in hosts:
        on_allowlist = any(
            host == suffix.lstrip(".") or host.endswith(suffix)
            for suffix in BR_REGIONAL_ENDPOINT_HOST_SUFFIXES
        )
        assert on_allowlist, f"declared PHI endpoint host {host!r} is not on the client allowlist"


# =================================================================================================
# PART 6 — Property 6: PhiZoneRoutingError (I-6) RE-PROVEN in the harness, independent of the PEP;
# and the seam demonstrably wraps the provider it exercises.
# =================================================================================================


async def test_p6_phi_routing_error_is_independent_of_the_pep() -> None:
    """I-6 re-proof against the REAL facade (not a FakeInference), in BOTH directions.

    ``phi=True`` against the non-PHI-capable ``noop`` refuses with the gate REMOVED and with the gate
    PRESENT — the chokepoint can only SUBTRACT permission, never restore any. Complements the
    seam-proof suite's `test_phi_zone_routing_fail_close_is_independent_of_the_pep`, which uses a fake.
    """
    ungated = InferenceProvider(settings=InferenceSettings(provider="noop"))
    with pytest.raises(PhiZoneRoutingError):
        await ungated.generate(_PROMPT_CANARY, phi=True)  # neutralized: no gate at all

    gated = gate_inference(InferenceProvider(settings=InferenceSettings(provider="noop")), _canary_seam())
    with pytest.raises(PhiZoneRoutingError):
        await gated.generate(_PROMPT_CANARY, phi=True)  # gated: the SAME refusal, unswallowed


async def test_p6_the_seam_wraps_the_br_adapter_it_exercises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The composed stack is a real gated seam AND serves the BR adapter's completion end-to-end.

    ``gate_inference`` is the sanctioned constructor, the result IS a gated seam, and a ``phi=True``
    generate flows gate -> facade (phi_capable) -> BR adapter -> labeled fake and back, returning the
    unmistakably-synthetic completion. That is "compose, never bypass" made concrete.
    """
    gated, transport = _gated_br_stack(monkeypatch)

    assert is_gated_seam(gated)
    assert isinstance(gated, GatedInferenceProvider)

    completion = await gated.generate(_PROMPT_CANARY, phi=True)
    assert completion.startswith(FAKE_BR_REGIONAL_COMPLETION_PREFIX)
    assert [r.endpoint_url for r in transport.sent_requests] == [_APPROVED_ENDPOINT]


# =================================================================================================
# Inertness — the harness changes nothing a shipped process does.
# =================================================================================================


def test_inertness_the_shipped_default_is_untouched() -> None:
    """Nothing in this harness selects ``br_resident`` or dials a real endpoint; default stays noop."""
    assert InferenceSettings().provider == "noop"
    assert InferenceProvider().provider_name == "noop"
