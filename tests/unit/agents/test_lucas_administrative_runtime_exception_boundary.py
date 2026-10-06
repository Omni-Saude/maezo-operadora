"""Distinct CI exception repair: UNIT synthetic ports, exact closed values and error accounting."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from maezo.gateway.capabilities.journeys.contracts import JourneyContractError, JourneyDispatchOutcome
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS
from tests.unit.agents.test_lucas_administrative_runtime import (
    assembled,
    error_count,
    resume_input,
    turn_input,
)
from tests.unit.gateway.capabilities.journeys.test_effect_execution import harness as effect_harness

MARKER = "SYNTHETIC_PRIVATE_EXCEPTION_BODY_63728"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    calls = []

    def denied(*args, **kwargs):
        calls.append(True)
        raise AssertionError("UNIT exception fixture cannot use network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    yield
    assert calls == []


async def call(mode, runtime, b):
    return await (runtime.accept_turn(turn_input(b)) if mode == "turn" else runtime.resume(resume_input()))


def privacy_clean(result, saver):
    assert MARKER not in str(result)
    assert MARKER not in repr(saver.storage) and MARKER not in repr(saver.writes)


def inject_failure(stage, ingress, driver, saver, exc_type):
    async def broken(*args, **kwargs):
        raise exc_type(MARKER)

    if stage == "currentness":
        ingress.verify = broken
    elif stage == "saver":
        saver.aget_tuple = broken
    elif stage == "compiled_saver":
        original = saver.aget_tuple

        async def after_preflight(*args, **kwargs):
            result = await original(*args, **kwargs)
            if saver.reads == 2:
                raise exc_type(MARKER)
            return result

        saver.aget_tuple = after_preflight
    else:
        driver.on_call = broken


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["turn", "resume"])
@pytest.mark.parametrize("stage", ["currentness", "saver", "compiled_saver", "driver"])
@pytest.mark.parametrize("exc_type", PROGRAMMING_ERRORS)
async def test_each_programming_error_propagates_from_every_injected_boundary(mode, stage, exc_type):
    b, ingress, driver, saver, runtime = assembled()
    inject_failure(stage, ingress, driver, saver, exc_type)
    before = error_count()
    with pytest.raises(exc_type):
        await call(mode, runtime, b)
    assert error_count() == before + (1 if stage in {"compiled_saver", "driver"} else 0)
    privacy_clean(None, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["turn", "resume"])
@pytest.mark.parametrize("phase", [1, 2, 3])
@pytest.mark.parametrize("reason", list(CapabilityRefusalReason))
async def test_every_qualified_refusal_stays_the_exact_closed_value_at_all_authentication_stages(
    mode, phase, reason
):
    b, ingress, driver, saver, runtime = assembled()
    original = ingress.verify

    async def refuses(*args, **kwargs):
        result = await original(*args, **kwargs)
        return reason if ingress.calls == phase else result

    ingress.verify = refuses
    before = error_count()
    result = await call(mode, runtime, b)
    assert result is reason and type(result) is CapabilityRefusalReason
    assert error_count() == before + (1 if phase == 2 else 0)
    assert len(driver.calls) == (1 if phase == 3 else 0)
    if phase == 1:
        assert saver.reads == 0
    if phase == 3:
        saved = await saver.aget_tuple(runtime._config())
        assert (
            saved.checkpoint["channel_values"]["outcome"]["source_completion_receipt_ref"]
            == "unit-synthetic-result-1"
        )
    privacy_clean(result, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["turn", "resume"])
@pytest.mark.parametrize("stage", ["currentness", "saver", "driver"])
async def test_forged_typed_exception_reason_is_never_an_outward_value(mode, stage):
    b, ingress, driver, saver, runtime = assembled()

    async def forged(*args, **kwargs):
        failure = JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)
        failure.reason = MARKER
        failure.args = (MARKER,)
        raise failure

    if stage == "currentness":
        ingress.verify = forged
    elif stage == "saver":
        saver.aget_tuple = forged
    else:
        driver.on_call = forged
    before = error_count()
    result = await call(mode, runtime, b)
    assert result == CapabilityRefusalReason.CONTRACT_MISMATCH and type(result) is CapabilityRefusalReason
    assert error_count() == before + (1 if stage == "driver" else 0)
    privacy_clean(result, saver)


class UnexpectedPortFailureError(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["turn", "resume"])
@pytest.mark.parametrize("stage", ["currentness", "saver", "compiled_saver", "driver"])
@pytest.mark.parametrize("exc_type", [UnexpectedPortFailureError, ValueError])
async def test_unknown_and_unqualified_value_errors_rethrow_without_availability_downgrade(
    mode, stage, exc_type
):
    b, ingress, driver, saver, runtime = assembled()
    inject_failure(stage, ingress, driver, saver, exc_type)
    before = error_count()
    with pytest.raises(exc_type):
        await call(mode, runtime, b)
    assert error_count() == before + (1 if stage in {"compiled_saver", "driver"} else 0)
    privacy_clean(None, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["turn", "resume"])
@pytest.mark.parametrize("stage", ["currentness", "saver", "compiled_saver", "driver"])
@pytest.mark.parametrize("exc_type", [RuntimeError, OSError, TimeoutError, httpx.HTTPError])
async def test_declared_external_failures_keep_bounded_availability_and_exact_metric_count(
    mode, stage, exc_type
):
    b, ingress, driver, saver, runtime = assembled()
    inject_failure(stage, ingress, driver, saver, exc_type)
    before = error_count()
    result = await call(mode, runtime, b)
    assert result == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert error_count() == before + (1 if stage in {"compiled_saver", "driver"} else 0)
    privacy_clean(result, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["turn", "resume"])
@pytest.mark.parametrize("stage", ["currentness", "saver", "compiled_saver", "driver"])
async def test_actual_cancellation_always_propagates_without_error_counter(mode, stage):
    b, ingress, driver, saver, runtime = assembled()
    inject_failure(stage, ingress, driver, saver, asyncio.CancelledError)
    before = error_count()
    with pytest.raises(asyncio.CancelledError):
        await call(mode, runtime, b)
    assert error_count() == before
    privacy_clean(None, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["turn", "resume"])
async def test_unknown_code_fault_after_actual_valid_effect_preserves_fact_without_redispatch(mode):
    from maezo.agents.lucas.administrative.runtime import AdministrativeJourneyRuntime
    from maezo.gateway.pseudonymizer import Pseudonymizer
    from tests.unit.agents.test_lucas_administrative_runtime import CountingSaver

    h = effect_harness()
    saver = CountingSaver()
    runtime = AdministrativeJourneyRuntime(
        binding=h.binding,
        driver=h.driver,
        currentness=h.driver.currentness,
        checkpointer=saver,
        pseudonymizer=Pseudonymizer(key=b"UNIT-runtime-known-commit-exception"),
        enabled=True,
    )
    original = h.driver.accept_turn if mode == "turn" else h.driver.resume

    async def after_fact(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert isinstance(result, JourneyDispatchOutcome)
        raise UnexpectedPortFailureError(MARKER)

    if mode == "turn":
        h.driver.accept_turn = after_fact
    else:
        h.driver.resume = after_fact
    before = error_count()
    with pytest.raises(UnexpectedPortFailureError):
        await call(mode, runtime, h.binding)
    assert error_count() == before + 1
    assert (
        h.source.calls == h.source.effects == 1
        and h.journal.snapshot.technical_state.value == "RESPONSE_RECORDED"
    )
    assert h.journal.snapshot.source_receipt_ref == "unit-domain-receipt"
    assert h.journal.descriptor.envelope.idempotency_key == h.action.envelope.idempotency_key
    assert h.authority.events.count("begin_dispatch") == 1 and "mark_uncertain" not in h.authority.events
    privacy_clean(None, saver)


def error_labels():
    from maezo.platform.observability import get_metrics_collector

    return {
        sample.labels["error_type"]: sample.value
        for metric in get_metrics_collector().registry.collect()
        for sample in metric.samples
        if sample.name == "maezo_agent_errors_total" and sample.labels.get("agent") == "lucas"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_type",
    [
        *PROGRAMMING_ERRORS,
        UnexpectedPortFailureError,
        ValueError,
        RuntimeError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
    ],
)
async def test_compiled_error_label_matches_existing_exact_classifier_without_new_series(exc_type):
    from maezo.platform.error_types import classify_agent_error_type

    b, ingress, driver, saver, runtime = assembled()
    inject_failure("driver", ingress, driver, saver, exc_type)
    before = error_labels()
    raised = False
    result = None
    try:
        result = await runtime.accept_turn(turn_input(b))
    except exc_type:
        raised = True
    if exc_type in {RuntimeError, OSError, TimeoutError, httpx.HTTPError}:
        assert not raised and result == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    else:
        assert raised and result is None
    after = error_labels()
    delta = {label: after.get(label, 0) - before.get(label, 0) for label in set(before) | set(after)}
    assert {label: amount for label, amount in delta.items() if amount} == {
        classify_agent_error_type(exc_type(MARKER)): 1
    }
    assert MARKER not in repr(after)
    privacy_clean(None, saver)


@pytest.mark.asyncio
async def test_after_effect_qualified_refusal_preserves_actual_journal_fact_and_never_reexecutes():
    from maezo.agents.lucas.administrative.runtime import AdministrativeJourneyRuntime
    from maezo.gateway.pseudonymizer import Pseudonymizer
    from tests.unit.agents.test_lucas_administrative_runtime import CountingSaver

    h = effect_harness()
    calls = [0]
    original = h.driver.currentness.verify

    async def refused_at_disclosure(*args, **kwargs):
        calls[0] += 1
        result = await original(*args, **kwargs)
        if h.journal.snapshot is not None and h.journal.snapshot.technical_state.value == "RESPONSE_RECORDED":
            return CapabilityRefusalReason.AUDIT_UNAVAILABLE
        return result

    h.driver.currentness.verify = refused_at_disclosure
    saver = CountingSaver()
    runtime = AdministrativeJourneyRuntime(
        binding=h.binding,
        driver=h.driver,
        currentness=h.driver.currentness,
        checkpointer=saver,
        pseudonymizer=Pseudonymizer(key=b"UNIT-private-closed-disclosure-refusal"),
        enabled=True,
    )
    result = await runtime.accept_turn(turn_input(h.binding))
    assert result == CapabilityRefusalReason.AUDIT_UNAVAILABLE
    assert (
        h.source.calls == h.source.effects == 1
        and h.journal.snapshot.technical_state.value == "RESPONSE_RECORDED"
    )
    assert h.journal.snapshot.source_receipt_ref == "unit-domain-receipt"
    assert h.authority.events.count("begin_dispatch") == 1 and "mark_uncertain" not in h.authority.events
    privacy_clean(result, saver)
