"""W8 budgeted, idempotency-aware retry for the BR-resident PHI adapter (Onda 2, W2 leg 4).

Companion to ``test_inference_br_resident.py`` (leg 2 — construction gates, residency refusals,
PHI-safe logging), ``test_phi_inference_canary.py`` (leg 3 — the synthetic-canary safety harness),
and ``test_inference_capabilities.py`` (the capability schema). This file proves the retry budget's
SAFETY PROPERTIES empirically, against the in-process :class:`LabeledFakeBrRegionalTransport` and a
handful of test-local deterministic transports, with a RED CONTROL for every safety guard — a
canary that cannot fail is vacuous.

THE ONE RULE THAT MATTERS MOST (idempotency). A completion whose prompt has ALREADY been
transmitted (a read timeout AFTER bytes-sent) must NEVER be blindly re-sent: re-transmitting PHI is
a data-exposure + double-spend hazard. The budget models this as a ``committed`` signal on the
failure and refuses to re-dial a committed call even when it is otherwise ``retryable``. The RED
control neuters that guard and demonstrates the harm — PHI re-sent N times.

SINGLE SOURCE OF TRUTH (no drift). The retry classification is DERIVED, never re-authored: it reads
``InferenceProviderError.retryable`` (the leg-3 ``_DISPOSITIONS`` truth — ONLY an OUTAGE is
retryable) and composes it with the new ``committed`` distinction by logical AND. A dedicated test
pins that the two notions agree with the LIVE exceptions the adapter raises, so the before/after
-commit notion can never silently diverge from ``_DISPOSITIONS``.

DETERMINISTIC. No wall clock, no RNG, no real sleep: the backoff ``sleep`` and the token-bucket
``now`` clock are INJECTED, and jitter is a seeded SHA-256 fraction. A test pins the exact schedule.

INERT. Nothing here selects ``br_resident`` in a shipped config; the process default stays ``noop``
(``test_inertness_the_shipped_default_provider_is_untouched``). The DEFAULT retry budget is a single
attempt — the retry machinery is present but dormant, so a directly-constructed default adapter
behaves byte-for-byte as it did before this leg (``test_inertness_default_budget_is_no_retry``).
Runs in CI with the rest of ``tests/unit``.
"""

from __future__ import annotations

import pytest
import structlog

from maezo.runtime import inference as inf
from maezo.runtime.inference import (
    BR_REGIONAL_ATTESTED_REGION,
    ENV_PHI_API_KEY,
    ENV_PHI_ENDPOINT_URL,
    ENV_PHI_VENDOR_DPA_REF,
    FAKE_BR_REGIONAL_COMPLETION_PREFIX,
    RETRY_STOP_ATTEMPTS_EXHAUSTED,
    RETRY_STOP_COMMITTED,
    RETRY_STOP_NOT_RETRYABLE,
    RETRY_STOP_RATE_BUDGET_EXHAUSTED,
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
    PhiZoneRoutingError,
    RetryBudget,
    retry_denial_reason,
)
from maezo.runtime.inference import br_resident_provider as br_resident_impl

# =================================================================================================
# PART 0 — Harness support. Same canary strings as legs 2/3 (NOT derived from any constant under
# test), the sanctioned direct-construction builder, injected sleep/clock, and the deterministic
# test-local transports (the established `_LyingUsageTransport` pattern from leg 3).
# =================================================================================================

_APPROVED_ENDPOINT = "https://fake-labeled-endpoint.br-sao-paulo.phi.maezo.internal/v1/generate"
_MODEL = "br-model-under-test"
_DPA_REF = "DPA-TEST-NOT-A-REAL-CONTRACT"
_CREDENTIAL = "phi-key-CANARY-must-never-appear-anywhere"

#: Stands in for PHI-bearing prompt text. If it surfaces in ANY retry log event or exception on the
#: retry path, PHI has leaked through that channel.
_PROMPT_CANARY = "PROMPT-CANARY-beneficiario-Jose-da-Silva-CPF-000-dor-toracica"


@pytest.fixture(autouse=True)
def _clean_phi_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient owner act may reach a test here (legs 2/3 fixture, same reasoning)."""
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


def _set_owner_acts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PHI_ENDPOINT_URL, _APPROVED_ENDPOINT)
    monkeypatch.setenv(ENV_PHI_API_KEY, _CREDENTIAL)
    monkeypatch.setenv(ENV_PHI_VENDOR_DPA_REF, _DPA_REF)


class _ManualClock:
    """A monotonic clock a test drives by hand — the token bucket's injected ``now``."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _RecordingSleep:
    """An injected async ``sleep`` that records every backoff and never actually sleeps.

    When given a ``clock`` it also ADVANCES it by the backoff — modelling "time passed while we
    backed off", which is what lets the token-bucket refill be exercised deterministically.
    """

    def __init__(self, clock: _ManualClock | None = None) -> None:
        self.delays: list[float] = []
        self._clock = clock

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self._clock is not None:
            self._clock.advance(delay)


class _CountingOutageTransport:
    """Raises a BEFORE-commit OUTAGE (retryable, un-committed) for the first ``fail_times`` sends,
    then returns a valid synthetic ACCEPTED response.

    Deterministic; records every send so a test can count how many times the adapter dialled. The
    recovery response is well-formed and on-endpoint so it passes ``_validate_response`` — the point
    of this transport is the retry LOOP, not a validation failure.
    """

    def __init__(self, *, fail_times: int) -> None:
        self._fail_times = fail_times
        self.sent_requests: list[BrRegionalRequest] = []

    async def send(self, request: BrRegionalRequest) -> BrRegionalResponse:
        self.sent_requests.append(request)
        if len(self.sent_requests) <= self._fail_times:
            raise BrRegionalTransportUnavailableError(
                "synthetic before-commit outage", retryable=True, committed=False
            )
        return BrRegionalResponse(
            completion=f"{FAKE_BR_REGIONAL_COMPLETION_PREFIX} recovered-after-outages",
            model=request.model,
            usage=BrRegionalTokenUsage(input_tokens=1, output_tokens=1, cached_prefix_tokens=0),
            endpoint_url=request.endpoint_url,
            served_region=BR_REGIONAL_ATTESTED_REGION,
            zero_retention_acknowledged=True,
            training_prohibited_acknowledged=True,
            synthetic=True,
        )


class _PhiRoutingRaisingTransport:
    """A transport that raises :class:`PhiZoneRoutingError` (I-6) directly.

    Artificial — I-6 is really decided at the facade, never in a transport — but it lets us prove
    the budget's ``except InferenceProviderError`` can NEVER catch or retry an I-6 escape: a
    ``PhiZoneRoutingError`` is a `PermissionError`, structurally outside the retry branch.
    """

    def __init__(self) -> None:
        self.sent_requests: list[BrRegionalRequest] = []

    async def send(self, request: BrRegionalRequest) -> BrRegionalResponse:
        self.sent_requests.append(request)
        raise PhiZoneRoutingError("synthetic I-6 escape reached the transport")


def _retry_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: object,
    budget: RetryBudget,
    sleep: _RecordingSleep | None = None,
    now: _ManualClock | None = None,
) -> BrResidentInferenceProvider:
    """A fully-configured adapter wired to a test transport + an injected budget/sleep/clock.

    Leg 2's sanctioned direct construction (`transport=` kwarg + owner acts), extended with the
    leg-4 injectables so backoff is deterministic and the token bucket is clock-driven.
    """
    _set_owner_acts(monkeypatch)
    return BrResidentInferenceProvider(
        model=_MODEL,
        transport=transport,  # type: ignore[arg-type]  # duck-typed test transport (leg-3 pattern)
        retry_budget=budget,
        sleep=sleep,
        now=now,
    )


def _committed_blind_denial_reason(
    exc: InferenceProviderError, *, attempt: int, max_attempts: int, rate_ok: bool = True
) -> str | None:
    """RED-CONTROL neutering of :func:`retry_denial_reason`: the ``committed`` veto is REMOVED.

    Everything else is identical to the real function — this is exactly the one-branch edit whose
    consequence (a committed call gets re-dialled, re-sending PHI) the idempotency canary guards.
    """
    if not exc.retryable:
        return RETRY_STOP_NOT_RETRYABLE
    # NEUTERED: the `if exc.committed: return RETRY_STOP_COMMITTED` branch is gone.
    if attempt >= max_attempts:
        return RETRY_STOP_ATTEMPTS_EXHAUSTED
    if not rate_ok:
        return RETRY_STOP_RATE_BUDGET_EXHAUSTED
    return None


def _attempt_blind_denial_reason(
    exc: InferenceProviderError, *, attempt: int, max_attempts: int, rate_ok: bool = True
) -> str | None:
    """RED-CONTROL neutering of :func:`retry_denial_reason`: the ATTEMPT bound is REMOVED.

    Consequence: nothing forces escalation on exhaustion — the loop runs PAST ``max_attempts`` until
    the transport happens to recover. The budget-exhaustion canary guards exactly this.
    """
    if not exc.retryable:
        return RETRY_STOP_NOT_RETRYABLE
    if exc.committed:
        return RETRY_STOP_COMMITTED
    # NEUTERED: the `if attempt >= max_attempts: return RETRY_STOP_ATTEMPTS_EXHAUSTED` branch is gone.
    if not rate_ok:
        return RETRY_STOP_RATE_BUDGET_EXHAUSTED
    return None


# =================================================================================================
# PART 1 — Backoff mechanics: exponential, capped, deterministically jittered. Injected sleep, no
# real time. Schedules are HARDCODED (hand-computed from base/2^n), never derived from the code.
# =================================================================================================


async def test_backoff_schedule_is_exponential_and_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    """base=0.5, no jitter, 4 attempts -> the three inter-attempt backoffs are 0.5, 1.0, 2.0.

    Hand-computed from ``base * 2**(N-1)``; if the backoff formula drifts this fails loudly rather
    than silently re-deriving.
    """
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=4, base_backoff_s=0.5, max_backoff_s=30.0, jitter_ratio=0.0)
    adapter = _retry_adapter(
        monkeypatch,
        transport=LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.OUTAGE),
        budget=budget,
        sleep=sleep,
    )

    with pytest.raises(BrRegionalTransportUnavailableError):
        await adapter.generate(_PROMPT_CANARY)

    assert sleep.delays == [0.5, 1.0, 2.0]


async def test_backoff_is_capped_at_max_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """base=10, cap=15, no jitter, 5 attempts -> 10, 15, 15, 15 (the cap holds from attempt 2 on)."""
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=5, base_backoff_s=10.0, max_backoff_s=15.0, jitter_ratio=0.0)
    adapter = _retry_adapter(
        monkeypatch,
        transport=LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.OUTAGE),
        budget=budget,
        sleep=sleep,
    )

    with pytest.raises(BrRegionalTransportUnavailableError):
        await adapter.generate(_PROMPT_CANARY)

    assert sleep.delays == [10.0, 15.0, 15.0, 15.0]


async def test_jitter_is_deterministic_seeded_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeded jitter reproduces exactly across runs, stays within ``[capped, capped*(1+ratio))``,
    and actually depends on the seed (a different seed yields a different schedule)."""

    async def _delays_for_seed(seed: int) -> list[float]:
        sleep = _RecordingSleep()
        budget = RetryBudget(
            max_attempts=3, base_backoff_s=1.0, max_backoff_s=100.0, jitter_ratio=0.5, jitter_seed=seed
        )
        adapter = _retry_adapter(
            monkeypatch,
            transport=LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.OUTAGE),
            budget=budget,
            sleep=sleep,
        )
        with pytest.raises(BrRegionalTransportUnavailableError):
            await adapter.generate(_PROMPT_CANARY)
        return sleep.delays

    # Two identical-seed runs must be byte-identical (no RNG anywhere).
    run_a = await _delays_for_seed(42)
    run_b = await _delays_for_seed(42)
    assert run_a == run_b, "same seed must produce the same backoff schedule"

    # Bounded: attempt N (1-indexed) has capped = 1.0 * 2**(N-1) -> [1.0, 2.0] for two backoffs.
    for delay, capped in zip(run_a, [1.0, 2.0], strict=True):
        assert capped <= delay < capped * 1.5  # ratio = 0.5

    # Non-vacuous: the seed genuinely drives the jitter.
    assert await _delays_for_seed(7) != run_a


# =================================================================================================
# PART 2 — Idempotency: the safety-critical rule. A read-timeout AFTER bytes-sent is NEVER retried,
# even within budget. RED control: neuter the commit guard and PHI is re-sent (the harm).
# =================================================================================================


async def test_after_bytes_sent_read_timeout_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE SAFETY-CRITICAL CANARY. A read timeout after the prompt was transmitted is NOT re-dialled
    even with a 3-attempt budget: the PHI prompt reaches the wire EXACTLY ONCE.

    Distinguishes committed (bytes-sent) from before-commit: the failure IS retryable in the SDK
    sense, but ``committed`` vetoes the retry — because re-sending PHI is the hazard.
    """
    transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.READ_TIMEOUT_AFTER_SEND)
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=3, base_backoff_s=0.5)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep)

    with pytest.raises(BrRegionalTransportUnavailableError) as excinfo:
        await adapter.generate(_PROMPT_CANARY)

    assert len(transport.sent_requests) == 1, "a committed (bytes-sent) failure must never re-send PHI"
    assert sleep.delays == [], "no backoff was scheduled — the retry was refused before any wait"
    # It WAS classified retryable; the commit veto — not non-retryability — is what stopped the retry.
    assert excinfo.value.retryable is True
    assert excinfo.value.committed is True


async def test_red_control_neutered_commit_guard_re_sends_phi_after_bytes_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED CONTROL (idempotency): with the commit veto removed, the SAME read-timeout is re-dialled
    up to the budget — re-transmitting the PHI prompt 3 times. This IS the harm the guard prevents.

    Proves the canary above is load-bearing: the ONLY thing standing between a committed timeout and
    a triple PHI re-send is the ``committed`` branch of :func:`retry_denial_reason`.
    """
    transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.READ_TIMEOUT_AFTER_SEND)
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=3, base_backoff_s=0.5, jitter_ratio=0.0)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep)
    # D2-02 split (docs/reports/inference-split-plan.md §5): `BrResidentInferenceProvider` resolves
    # `retry_denial_reason` as a free variable from ITS OWN defining module,
    # `maezo.runtime.inference.br_resident_provider` — patching an attribute on the FACADE package
    # (`maezo.runtime.inference` itself, the `inf` alias) does not intercept anything (Python's
    # normal LEGB lookup).
    monkeypatch.setattr(br_resident_impl, "retry_denial_reason", _committed_blind_denial_reason)

    with pytest.raises(BrRegionalTransportUnavailableError):
        await adapter.generate(_PROMPT_CANARY)

    assert len(transport.sent_requests) == 3, "neutered commit guard re-sent PHI up to the budget"
    # The harm made concrete: every re-send carried the SAME PHI prompt onto the wire.
    assert all(r.variable_suffix == _PROMPT_CANARY for r in transport.sent_requests)
    assert sleep.delays == [0.5, 1.0], "and it backed off between the harmful re-sends"


# =================================================================================================
# PART 3 — Before-bytes-sent OUTAGE: retried within budget, backoff observed, escalates on
# exhaustion. RED control: neuter the attempt bound and escalation never happens (runs past budget).
# =================================================================================================


async def test_before_bytes_sent_outage_is_retried_then_escalates_on_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A before-commit OUTAGE IS retried within a 3-attempt budget (3 dials, 2 backoffs), then the
    last outage propagates — that raise IS the SP-OP escalation to a human (never an infinite loop).
    """
    transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.OUTAGE)
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=3, base_backoff_s=0.5, jitter_ratio=0.0)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep)

    with pytest.raises(BrRegionalTransportUnavailableError) as excinfo:
        await adapter.generate(_PROMPT_CANARY)

    assert len(transport.sent_requests) == 3, "1 initial + 2 retries within the 3-attempt budget"
    assert sleep.delays == [0.5, 1.0], "backoff observed between the retries"
    # Escalates AS a retryable, un-committed outage — the disposition is preserved, not rewritten.
    assert excinfo.value.retryable is True
    assert excinfo.value.committed is False


async def test_before_bytes_sent_outage_recovers_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient outage that clears within budget yields the recovered completion — no escalation.

    Two failures then success, under a 5-attempt budget: the adapter returns the (synthetic) text on
    the 3rd dial, after backing off 0.5 then 1.0.
    """
    transport = _CountingOutageTransport(fail_times=2)
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=5, base_backoff_s=0.5, jitter_ratio=0.0)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep)

    completion = await adapter.generate(_PROMPT_CANARY)

    assert completion.startswith(FAKE_BR_REGIONAL_COMPLETION_PREFIX)
    assert len(transport.sent_requests) == 3, "2 outages + 1 success"
    assert sleep.delays == [0.5, 1.0]


async def test_budget_exhaustion_escalates_rather_than_looping(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE BUDGET-EXHAUSTION CANARY. A never-clearing outage under a 3-attempt budget escalates
    (raises) after EXACTLY 3 dials — a bounded resource, not an infinite loop.

    Provenance: ``RetryBudget().max_attempts == 1`` is the default; this test pins the injected
    ``max_attempts=3`` as the bound that forces escalation.
    """
    transport = _CountingOutageTransport(fail_times=10)  # would keep failing well past the budget
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=3, base_backoff_s=0.0, jitter_ratio=0.0)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep)

    with pytest.raises(BrRegionalTransportUnavailableError):
        await adapter.generate(_PROMPT_CANARY)

    assert len(transport.sent_requests) == 3, "escalated at the budget, did not loop"


async def test_red_control_neutered_attempt_bound_runs_past_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED CONTROL (budget exhaustion): with the attempt bound removed, nothing forces escalation —
    the loop runs PAST ``max_attempts=3`` until the transport recovers on the 11th dial, RETURNING a
    completion instead of escalating. Proves the attempt bound is load-bearing.

    Terminates because ``_CountingOutageTransport`` recovers after a finite count — the intact bound
    is what turns "eventually succeed after 11 dials" into "escalate after 3".
    """
    transport = _CountingOutageTransport(fail_times=10)
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=3, base_backoff_s=0.0, jitter_ratio=0.0)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep)
    # D2-02 split note: see the RED-control test above for why this targets `br_resident_impl`.
    monkeypatch.setattr(br_resident_impl, "retry_denial_reason", _attempt_blind_denial_reason)

    completion = await adapter.generate(_PROMPT_CANARY)

    assert completion.startswith(FAKE_BR_REGIONAL_COMPLETION_PREFIX)
    assert len(transport.sent_requests) == 11, "neutered attempt bound ran far past the budget of 3"


# =================================================================================================
# PART 4 — The rate/token budget: a SECOND, independent bound so a retry storm can't itself become a
# rate-limit incident. Clock-driven refill, exercised deterministically.
# =================================================================================================


async def test_rate_budget_bounds_retries_below_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a 2-token retry budget and NO refill, a never-clearing outage stops after 2 retries even
    though ``max_attempts`` is 10 — the token budget, not the attempt budget, is what binds."""
    transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.OUTAGE)
    sleep = _RecordingSleep()
    clock = _ManualClock()  # never advanced -> no refill
    budget = RetryBudget(
        max_attempts=10, base_backoff_s=0.0, retry_token_capacity=2, retry_token_refill_per_s=0.0
    )
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep, now=clock)

    with structlog.testing.capture_logs() as logs, pytest.raises(BrRegionalTransportUnavailableError):
        await adapter.generate(_PROMPT_CANARY)

    assert len(transport.sent_requests) == 3, "1 initial + 2 token-funded retries, then rate-stopped"
    stops = [e for e in logs if e.get("event") == "inference_br_resident_retry_stop"]
    assert stops and stops[-1]["stop_reason"] == RETRY_STOP_RATE_BUDGET_EXHAUSTED


async def test_rate_budget_refill_is_clock_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refill is governed by the INJECTED clock. With a 1-token bucket that refills 1/s and a 2s
    backoff advancing the clock, the bucket keeps up and ``max_attempts`` (4) is the bound -> 4 dials.
    With the SAME shape but no refill, the bucket empties and rate-stops after 2 dials.

    The contrast is the proof the refill is load-bearing AND time-driven, not a constant.
    """

    async def _dials_for_refill(refill_per_s: float) -> int:
        transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.OUTAGE)
        clock = _ManualClock()
        sleep = _RecordingSleep(clock=clock)  # each backoff advances the injected clock
        budget = RetryBudget(
            max_attempts=4,
            base_backoff_s=2.0,
            jitter_ratio=0.0,
            retry_token_capacity=1,
            retry_token_refill_per_s=refill_per_s,
        )
        adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep, now=clock)
        with pytest.raises(BrRegionalTransportUnavailableError):
            await adapter.generate(_PROMPT_CANARY)
        return len(transport.sent_requests)

    # A bucket that refills over the backoff keeps up, so max_attempts (4) binds; without refill the
    # 1-token bucket empties and binds after a single retry (2 dials).
    assert await _dials_for_refill(1.0) == 4
    assert await _dials_for_refill(0.0) == 2


# =================================================================================================
# PART 5 — Single-source: the retry classification is DERIVED from `retryable` + `committed`, never
# re-authored. The composition (AND) and the agreement with the LIVE disposition truth are pinned.
# =================================================================================================


@pytest.mark.parametrize(
    ("retryable", "committed", "expected_reason"),
    [
        (False, False, RETRY_STOP_NOT_RETRYABLE),
        (False, True, RETRY_STOP_NOT_RETRYABLE),  # retryable dominates; committed only ever vetoes
        (True, False, None),  # the ONLY retried shape: retryable AND not committed
        (True, True, RETRY_STOP_COMMITTED),  # retryable but committed -> the idempotency veto
    ],
)
def test_retry_decision_is_the_and_of_retryable_and_uncommitted(
    retryable: bool, committed: bool, expected_reason: str | None
) -> None:
    """The decision is exactly ``retry iff (retryable and not committed)`` within budget.

    Hardcoded truth table, NOT derived from the function under test. ``committed`` never RESCUES a
    non-retryable failure (row 2) and never REPLACES ``retryable`` — it only ADDS a veto (row 4).
    """
    exc = InferenceProviderError("br_resident", "synthetic", retryable=retryable, committed=committed)
    assert retry_denial_reason(exc, attempt=1, max_attempts=99) == expected_reason


def test_retry_reads_retryable_from_the_exception_not_a_second_table() -> None:
    """Flipping ONLY ``exc.retryable`` flips the decision — proof the classifier reads the exception,
    with no independent notion of retryability that could drift from ``_DISPOSITIONS``."""
    retryable = BrRegionalTransportUnavailableError("x", retryable=True, committed=False)
    terminal = BrRegionalTransportUnavailableError("x", retryable=False, committed=False)
    assert retry_denial_reason(retryable, attempt=1, max_attempts=99) is None
    assert retry_denial_reason(terminal, attempt=1, max_attempts=99) == RETRY_STOP_NOT_RETRYABLE


async def test_retry_classification_agrees_with_the_live_disposition_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGREEMENT / composition test. For EVERY fake outcome, run it through the adapter (no-retry
    budget) and confirm the retry decision is derivable from the raised exception's own
    ``retryable``/``committed`` — the before/after-commit notion composes with the live
    ``_DISPOSITIONS`` truth and cannot silently drift from it.

    Residency escapes are `PermissionError`s (not `InferenceProviderError`), so they are
    structurally outside the retry branch and can never be retried — asserted explicitly.
    """
    retried_shapes: set[str] = set()
    for outcome in FakeBrRegionalOutcome:
        if outcome is FakeBrRegionalOutcome.ACCEPTED:
            continue
        adapter = _retry_adapter(
            monkeypatch,
            transport=LabeledFakeBrRegionalTransport(outcome=outcome),
            budget=RetryBudget(),  # single attempt: capture the raw disposition, don't loop
        )
        try:
            await adapter.generate(_PROMPT_CANARY)
        except InferenceProviderError as exc:
            would_retry = retry_denial_reason(exc, attempt=1, max_attempts=99) is None
            assert would_retry == (exc.retryable and not exc.committed), outcome.value
            if would_retry:
                retried_shapes.add(outcome.value)
        except BrEndpointNotApprovedError as exc:
            # A residency escape: outside the retry branch by TYPE, so it is never retried.
            assert not isinstance(exc, InferenceProviderError)

    # The ONLY retried outcome is the before-commit OUTAGE — agreeing with leg-3's `_DISPOSITIONS`
    # (only OUTAGE is retryable) AND excluding READ_TIMEOUT_AFTER_SEND (retryable BUT committed).
    assert retried_shapes == {FakeBrRegionalOutcome.OUTAGE.value}


# =================================================================================================
# PART 6 — Residency escape (P4 stays green) and I-6 are NEVER retried — both `PermissionError`s
# outside the budget's `except InferenceProviderError`.
# =================================================================================================


async def test_residency_escape_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect off the approved endpoint (P4) raises `BrEndpointNotApprovedError` after ONE dial
    and is never retried, even under a 3-attempt budget — keeping leg-3's P4 canary green."""
    transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.REDIRECTED_OFF_REGION)
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=3, base_backoff_s=0.5)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep)

    with pytest.raises(BrEndpointNotApprovedError) as excinfo:
        await adapter.generate(_PROMPT_CANARY)

    assert len(transport.sent_requests) == 1, "the escape is detected post-send and NOT re-dialled"
    assert sleep.delays == []
    assert not isinstance(excinfo.value, InferenceProviderError), "structurally outside the retry branch"


async def test_i6_reaching_the_retry_loop_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """I-6 stays independent of the budget: a `PhiZoneRoutingError` that (artificially) reaches the
    retry loop is dialled EXACTLY ONCE and never retried — under a 3-attempt budget.

    DISCLOSURE: the realistic I-6 site is the FACADE, before any transport (leg-3 `test_p6`); a
    transport raising `PhiZoneRoutingError` is artificial. When one does, `_send`'s pre-existing
    broad ``except Exception`` (a LEG-2 wrapper, not this leg's) surfaces it as a non-retryable
    `BrRegionalTransportUnavailableError` with the `PhiZoneRoutingError` preserved as ``__cause__``.
    Either way the load-bearing BUDGET property holds: it is NOT retried. The type-masking is noted
    as an open question for the real-transport train, not silently accepted as correct.
    """
    transport = _PhiRoutingRaisingTransport()
    budget = RetryBudget(max_attempts=3, base_backoff_s=0.5)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget)

    with pytest.raises(BrRegionalTransportUnavailableError) as excinfo:
        await adapter.generate(_PROMPT_CANARY)

    assert len(transport.sent_requests) == 1, "I-6 is not retried by the budget"
    assert not excinfo.value.retryable, "surfaced as terminal — never a retryable disposition"
    assert isinstance(excinfo.value.__cause__, PhiZoneRoutingError), "the I-6 cause is preserved"


def test_permission_errors_are_structurally_outside_the_retry_branch() -> None:
    """Both fail-closed denials are `PermissionError`s, NOT `InferenceProviderError`s — so the
    budget's ``except InferenceProviderError`` can never catch, retry, or soften either."""
    assert not issubclass(PhiZoneRoutingError, InferenceProviderError)
    assert not issubclass(BrEndpointNotApprovedError, InferenceProviderError)
    assert issubclass(PhiZoneRoutingError, PermissionError)
    assert issubclass(BrEndpointNotApprovedError, PermissionError)


async def test_i6_facade_refusal_is_untouched_by_this_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real I-6 path (a PHI request against a non-PHI provider) still refuses at the facade —
    composing with leg-3's harness, unchanged by the retry leg."""
    facade = InferenceProvider(settings=InferenceSettings(provider="noop"))
    with pytest.raises(PhiZoneRoutingError):
        await facade.generate(_PROMPT_CANARY, phi=True)


# =================================================================================================
# PART 7 — Content discipline on the retry path (extends leg-3 property 2): attempt/backoff/outcome
# enums only, NEVER the prompt, and never re-logged content across attempts.
# =================================================================================================


async def test_no_content_reaches_the_retry_log_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sweep the WHOLE log stream of a multi-retry run: no prompt, credential or DPA ref anywhere,
    across every retry AND the final stop event."""
    transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.OUTAGE)
    sleep = _RecordingSleep()
    budget = RetryBudget(max_attempts=3, base_backoff_s=0.5, jitter_ratio=0.0)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=budget, sleep=sleep)

    with structlog.testing.capture_logs() as logs, pytest.raises(BrRegionalTransportUnavailableError):
        await adapter.generate(_PROMPT_CANARY)

    serialized = repr(logs)
    assert _PROMPT_CANARY not in serialized
    assert _CREDENTIAL not in serialized
    assert _DPA_REF not in serialized
    # The retry path really fired (2 retries + 1 stop) — "absent" is not "nothing was logged".
    retries = [e for e in logs if e.get("event") == "inference_br_resident_retry"]
    stops = [e for e in logs if e.get("event") == "inference_br_resident_retry_stop"]
    assert len(retries) == 2
    assert len(stops) == 1
    # No content-bearing field on any retry/stop event — only counts, enums, and codes.
    for event in retries + stops:
        assert set(event) <= {
            "event",
            "log_level",
            "attempt",
            "max_attempts",
            "next_delay_s",
            "stop_reason",
            "outcome",
            "retryable",
            "committed",
        }
    # NON-VACUITY of the sweep: it WOULD catch the canary if a field carried it.
    assert _PROMPT_CANARY in repr([{"event": "leak", "prompt": _PROMPT_CANARY}])


async def test_retry_stop_reason_is_a_content_free_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal (non-retryable) outcome logs a content-free ``stop_reason`` code — the same value
    that flows into the human-escalation reason, so it must never carry PHI (ties leg-3 property 2)."""
    transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.VENDOR_REFUSAL)
    adapter = _retry_adapter(monkeypatch, transport=transport, budget=RetryBudget(max_attempts=3))

    with structlog.testing.capture_logs() as logs, pytest.raises(InferenceProviderError):
        await adapter.generate(_PROMPT_CANARY)

    (stop,) = [e for e in logs if e.get("event") == "inference_br_resident_retry_stop"]
    assert stop["stop_reason"] == RETRY_STOP_NOT_RETRYABLE
    assert _PROMPT_CANARY not in repr(logs)


# =================================================================================================
# PART 8 — Inertness differential: the retry machinery is present but DORMANT by default, so nothing
# a shipped process does changes (leg-3 method).
# =================================================================================================


def test_inertness_default_budget_is_no_retry() -> None:
    """Provenance-pinned: the DEFAULT ``RetryBudget`` is a single attempt. A PHI path does not
    silently re-dial — retry is an owner-configured opt-in, like every other gate on this adapter."""
    assert RetryBudget().max_attempts == 1


async def test_inertness_a_default_adapter_makes_exactly_one_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE INERTNESS DIFFERENTIAL. A directly-constructed adapter with NO budget injected behaves
    byte-for-byte as it did before this leg: a single dial, the original terminal disposition, no
    backoff. The retry machinery exists but is dormant.
    """
    _set_owner_acts(monkeypatch)
    transport = LabeledFakeBrRegionalTransport(outcome=FakeBrRegionalOutcome.OUTAGE)
    adapter = BrResidentInferenceProvider(model=_MODEL, transport=transport)  # no budget/sleep/clock

    with pytest.raises(BrRegionalTransportUnavailableError) as excinfo:
        await adapter.generate(_PROMPT_CANARY)

    assert len(transport.sent_requests) == 1, "default = one attempt = pre-leg behaviour"
    assert excinfo.value.retryable is True, "the disposition is unchanged (leg-3 P4 contract)"


def test_inertness_the_shipped_default_provider_is_untouched() -> None:
    """Nothing in this leg selects ``br_resident`` or enables retry in a shipped config."""
    assert InferenceSettings().provider == "noop"
    assert InferenceProvider().provider_name == "noop"


def test_inertness_the_production_construction_path_has_retry_off_and_no_real_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production construction path (no injected transport, no injected budget) resolves to the
    REFUSING transport AND a no-retry budget — the retry leg wired ``br_resident`` into nothing."""
    _set_owner_acts(monkeypatch)
    adapter = BrResidentInferenceProvider(model=_MODEL)

    assert adapter._retry_budget.max_attempts == 1  # noqa: SLF001 — the inertness fact IS the probe
    assert isinstance(adapter._transport, inf.RefusingBrRegionalTransport)  # noqa: SLF001
