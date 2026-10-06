"""Actual bounded APIs with hostile synthetic exception ports; UNIT only, no DB/provider claims."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from maezo.gateway.capabilities.admission import AdmissionDeniedError
from maezo.gateway.capabilities.durability.models import (
    CommandTechnicalState,
    JournalCallTechnicalStatus,
)
from maezo.gateway.capabilities.journeys.contracts import JourneyContractError
from maezo.gateway.capabilities.models import CapabilityContractError, CapabilityRefusalReason
from tests.unit.gateway.capabilities.durability.fixtures import inbox
from tests.unit.gateway.capabilities.journeys.helpers import turn
from tests.unit.gateway.capabilities.journeys.test_domain_boundary import original
from tests.unit.gateway.capabilities.journeys.test_domain_boundary import setup as domain_setup
from tests.unit.gateway.capabilities.journeys.test_effect_execution import harness, run

pytestmark = pytest.mark.asyncio
_MARKER = "PRIVATE_SYNTHETIC_EXCEPTION_CUSTODY_MARKER_804"
_KINDS = [JourneyContractError, AdmissionDeniedError, CapabilityContractError]
_DURABLE_CASES = [
    (method, kind)
    for method in ("execute", "observe", "reconcile", "ingest")
    for kind in (_KINDS if method == "execute" else [AdmissionDeniedError, CapabilityContractError])
]
_BAD_REASONS = ["raw", "enum_text", "hostile_value", "missing", "forged_member"]


class HostileReason:
    def __init__(self, hits: list[str]) -> None:
        self.hits = hits

    def fail(self, operation: str) -> Any:
        self.hits.append(operation)
        raise AssertionError(_MARKER)

    def __str__(self) -> str:
        return self.fail("str")

    def __repr__(self) -> str:
        return self.fail("repr")

    def __eq__(self, other: object) -> bool:
        return self.fail("eq")

    def __bool__(self) -> bool:
        return self.fail("bool")


def error(
    kind: Any,
    reason: CapabilityRefusalReason | str,
    hits: list[str],
    *,
    trap_getters: bool = False,
) -> Exception:
    actual: Exception
    if trap_getters:

        def read(instance: BaseException) -> Any:
            hits.append("exception-property")
            raise AssertionError(_MARKER)

        def access(instance: BaseException, name: str) -> Any:
            if name in {"reason", "__dict__"}:
                hits.append("exception-getattribute")
                raise AssertionError(_MARKER)
            return BaseException.__getattribute__(instance, name)

        hostile = type(
            "HostileReasonRead",
            (kind,),
            {"reason": property(read), "__dict__": property(read), "__getattribute__": access},
        )
        actual = kind.__new__(hostile)
        BaseException.__init__(actual, CapabilityRefusalReason.CONTRACT_MISMATCH.value)
    else:
        actual = kind(CapabilityRefusalReason.CONTRACT_MISMATCH)
    storage = BaseException.__dict__["__dict__"].__get__(actual, BaseException)
    if type(reason) is CapabilityRefusalReason:
        storage["reason"] = reason
    elif reason == "missing":
        storage.pop("reason", None)
    elif reason == "hostile_value":
        storage["reason"] = HostileReason(hits)
    elif reason == "forged_member":
        forged = str.__new__(CapabilityRefusalReason, _MARKER)
        forged._name_ = "SOURCE_UNAVAILABLE"
        forged._value_ = _MARKER
        storage["reason"] = forged
    else:
        storage["reason"] = "SOURCE_UNAVAILABLE" if reason == "enum_text" else _MARKER
    return actual


async def awaited_failure(exc: BaseException, calls: list[int]) -> None:
    calls.append(1)
    release = asyncio.Event()
    asyncio.get_running_loop().call_soon(release.set)
    await release.wait()
    raise exc


async def durable_call(method: str, exc: BaseException) -> tuple[Any, Any, list[int]]:
    h = harness()
    calls: list[int] = []

    async def fail(*args: Any, **kwargs: Any) -> Any:
        await awaited_failure(exc, calls)

    if method == "execute":
        h.source.execute_under_authority = fail
        value = await h.service.execute_durable(
            h.action.envelope,
            h.action.request,
            effect_authority=h.context,
            predecessor_command_refs=(),
            expected_journal_revision=0,
        )
    else:
        assert (await run(h)).technical_refusal is None
        before = deepcopy(h.journal.snapshot)
        if method == "ingest":
            event = inbox(h.journal.snapshot.handle, h.preparation.last_commit.observation)
            h.reads.verify_inbox = fail
            value = await h.service.ingest_durable(
                event, expected_journal_revision=h.journal.snapshot.journal_revision
            )
        else:
            h.custody.restore_command = fail
            if method == "observe":
                value = await h.service.observe_durable(h.journal.snapshot.handle.command_ref)
            else:
                value = await h.service.reconcile_durable(
                    h.journal.snapshot.handle.command_ref,
                    expected_journal_revision=h.journal.snapshot.journal_revision,
                )
        assert h.journal.snapshot == before
        assert h.source.calls == h.source.effects == 1
    return value, h, calls


@pytest.mark.parametrize("method,kind", _DURABLE_CASES)
@pytest.mark.parametrize("bad", _BAD_REASONS)
async def test_all_actual_durable_apis_hide_malformed_exception_reason(
    method: str, kind: Any, bad: str
) -> None:
    hits: list[str] = []
    value, h, calls = await durable_call(method, error(kind, bad, hits))
    assert type(value) is CapabilityRefusalReason
    assert value is CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert calls == [1] and hits == []
    assert h.admission._leases == {}
    if method == "execute":
        assert h.journal.snapshot.technical_state == CommandTechnicalState.UNCERTAIN
        assert h.journal.snapshot.dispatch_ref is not None and h.journal.snapshot.fence_version == 1
        assert h.journal.descriptor.envelope.idempotency_key == h.action.envelope.idempotency_key
        assert h.source.effects == 0


@pytest.mark.parametrize("method,kind", _DURABLE_CASES)
@pytest.mark.parametrize("reason", list(CapabilityRefusalReason))
async def test_all_actual_durable_apis_preserve_every_authentic_closed_refusal(
    method: str, kind: Any, reason: CapabilityRefusalReason
) -> None:
    hits: list[str] = []
    value, _, calls = await durable_call(method, error(kind, reason, hits))
    assert value is reason
    assert calls == [1] and hits == []


@pytest.mark.parametrize("method,kind", _DURABLE_CASES)
@pytest.mark.parametrize("reason", [CapabilityRefusalReason.PURPOSE_DENIED, "raw"])
async def test_actual_durable_exception_getter_hooks_never_execute(
    method: str, kind: Any, reason: Any
) -> None:
    hits: list[str] = []
    value, _, calls = await durable_call(method, error(kind, reason, hits, trap_getters=True))
    assert value is (
        reason if type(reason) is CapabilityRefusalReason else CapabilityRefusalReason.SOURCE_UNAVAILABLE
    )
    assert calls == [1] and hits == []


async def native_call(exc: BaseException) -> tuple[Any, Any, list[int]]:
    _, _, _, target, boundary = domain_setup()
    b, snapshot, action, context = original()
    calls: list[int] = []

    async def fail(*args: Any, **kwargs: Any) -> Any:
        await awaited_failure(exc, calls)

    target.execute = fail
    value = await boundary.execute(b, snapshot, action, effect_authority=context)
    return value, target, calls


@pytest.mark.parametrize("bad", _BAD_REASONS)
async def test_actual_native_boundary_hides_malformed_reason(bad: str) -> None:
    hits: list[str] = []
    value, target, calls = await native_call(error(JourneyContractError, bad, hits))
    assert value is CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert target.effects == 0 and calls == [1] and hits == []


@pytest.mark.parametrize("reason", list(CapabilityRefusalReason))
async def test_actual_native_boundary_preserves_every_authentic_refusal(
    reason: CapabilityRefusalReason,
) -> None:
    hits: list[str] = []
    value, target, calls = await native_call(error(JourneyContractError, reason, hits))
    assert value is reason and target.effects == 0 and calls == [1] and hits == []


@pytest.mark.parametrize("reason", [CapabilityRefusalReason.PURPOSE_DENIED, "raw"])
async def test_actual_native_boundary_does_not_call_exception_getters(reason: Any) -> None:
    hits: list[str] = []
    value, target, calls = await native_call(error(JourneyContractError, reason, hits, trap_getters=True))
    assert value is (
        reason if type(reason) is CapabilityRefusalReason else CapabilityRefusalReason.AUTHORITY_UNPROVEN
    )
    assert target.effects == 0 and calls == [1] and hits == []


async def driver_call(method: str, exc: BaseException) -> tuple[Any, Any, list[int]]:
    h = harness()
    calls: list[int] = []

    async def fail(*args: Any, **kwargs: Any) -> Any:
        await awaited_failure(exc, calls)

    if method == "recover":
        h.journal.recover = fail
        value = await h.driver.recover(limit=1, cursor_ref=None)
    else:
        h.driver.currentness.verify = fail
        value = await (
            h.driver.accept_turn(turn(h.binding, 0))
            if method == "accept_turn"
            else h.driver.resume("unit-observation", 0)
        )
    return value, h, calls


@pytest.mark.parametrize("method", ["accept_turn", "resume", "recover"])
@pytest.mark.parametrize("bad", _BAD_REASONS)
async def test_actual_driver_outcome_does_not_escape_private_validation_input(method: str, bad: str) -> None:
    hits: list[str] = []
    value, h, calls = await driver_call(method, error(JourneyContractError, bad, hits))
    assert value.technical_refusal is CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert value.source_completion_receipt_ref is None and value.verified_transition_ref is None
    assert _MARKER not in value.model_dump_json()
    assert h.source.calls == h.source.effects == 0 and calls == [1] and hits == []


@pytest.mark.parametrize("method", ["accept_turn", "resume", "recover"])
@pytest.mark.parametrize("reason", list(CapabilityRefusalReason))
async def test_actual_driver_preserves_every_authentic_refusal(
    method: str, reason: CapabilityRefusalReason
) -> None:
    hits: list[str] = []
    value, _, calls = await driver_call(method, error(JourneyContractError, reason, hits))
    assert value.technical_refusal is reason and calls == [1] and hits == []


@pytest.mark.parametrize("method", ["accept_turn", "resume", "recover"])
@pytest.mark.parametrize("reason", [CapabilityRefusalReason.PURPOSE_DENIED, "raw"])
async def test_actual_driver_exception_getters_never_execute(method: str, reason: Any) -> None:
    hits: list[str] = []
    value, _, calls = await driver_call(method, error(JourneyContractError, reason, hits, trap_getters=True))
    assert value.technical_refusal is (
        reason if type(reason) is CapabilityRefusalReason else CapabilityRefusalReason.SOURCE_UNAVAILABLE
    )
    assert _MARKER not in value.model_dump_json() and calls == [1] and hits == []


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("bad", _BAD_REASONS)
async def test_post_result_commit_exception_preserves_fact_intents_and_no_second_execute(
    kind: Any, bad: str
) -> None:
    h = harness()
    h.preparation.with_intents = True
    accepted = h.transition.check_current
    hits: list[str] = []
    calls: list[int] = []
    exc = error(kind, bad, hits)

    async def after_result(*args: Any, **kwargs: Any) -> Any:
        if kwargs["phase"] == "before_disclosure":
            await awaited_failure(exc, calls)
        return await accepted(*args, **kwargs)

    h.transition.check_current = after_result
    result = await run(h)
    assert result.technical_refusal is CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert h.journal.snapshot.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert len(h.journal.recorded_waits) == len(h.journal.recorded_outbox) == 1
    snapshot = deepcopy(h.journal.snapshot)
    assert h.source.calls == h.source.effects == 1 and calls == [1] and hits == []
    duplicate = await h.service.execute_durable(
        h.action.envelope,
        h.action.request,
        effect_authority=h.context,
        predecessor_command_refs=(),
        expected_journal_revision=0,
    )
    assert duplicate.technical_status == JournalCallTechnicalStatus.UNCHANGED
    assert h.journal.snapshot == snapshot
    assert h.source.calls == h.source.effects == 1
    assert len(h.journal.recorded_waits) == len(h.journal.recorded_outbox) == 1
    assert h.admission._leases == {} and hits == []


@pytest.mark.parametrize("method", ["execute", "observe", "reconcile", "ingest"])
async def test_actual_durable_apis_preserve_cancellation(method: str) -> None:
    with pytest.raises(asyncio.CancelledError):
        await durable_call(method, asyncio.CancelledError())


@pytest.mark.parametrize("method", ["accept_turn", "resume", "recover"])
async def test_actual_driver_preserves_cancellation(method: str) -> None:
    with pytest.raises(asyncio.CancelledError):
        await driver_call(method, asyncio.CancelledError())


async def test_native_boundary_preserves_cancellation_and_programming_error_visibility() -> None:
    with pytest.raises(asyncio.CancelledError):
        await native_call(asyncio.CancelledError())
    failure = RuntimeError("unit-native-programming-failure")
    with pytest.raises(RuntimeError) as caught:
        await native_call(failure)
    assert caught.value is failure


class HostileStorage(dict[str, object]):
    def __init__(self, hits: list[str]) -> None:
        super().__init__(reason=CapabilityRefusalReason.PURPOSE_DENIED)
        self.hits = hits

    def get(self, *args: Any, **kwargs: Any) -> Any:
        self.hits.append("storage-get")
        raise AssertionError(_MARKER)

    def __getitem__(self, key: str) -> object:
        self.hits.append("storage-item")
        raise AssertionError(_MARKER)

    def __iter__(self) -> Any:
        self.hits.append("storage-iterate")
        raise AssertionError(_MARKER)

    def __contains__(self, key: object) -> bool:
        self.hits.append("storage-membership")
        raise AssertionError(_MARKER)


def storage_error(kind: Any, hits: list[str]) -> Exception:
    exc = kind(CapabilityRefusalReason.CONTRACT_MISMATCH)
    BaseException.__dict__["__dict__"].__set__(exc, HostileStorage(hits))
    return exc


@pytest.mark.parametrize("method,kind", _DURABLE_CASES)
async def test_actual_durable_apis_never_call_exception_storage_subclass_hooks(
    method: str, kind: Any
) -> None:
    hits: list[str] = []
    value, _, calls = await durable_call(method, storage_error(kind, hits))
    assert value is CapabilityRefusalReason.SOURCE_UNAVAILABLE and calls == [1] and hits == []


async def test_actual_native_boundary_never_calls_exception_storage_subclass_hooks() -> None:
    hits: list[str] = []
    value, _, calls = await native_call(storage_error(JourneyContractError, hits))
    assert value is CapabilityRefusalReason.AUTHORITY_UNPROVEN and calls == [1] and hits == []


@pytest.mark.parametrize("method", ["accept_turn", "resume", "recover"])
async def test_actual_driver_never_calls_exception_storage_subclass_hooks(method: str) -> None:
    hits: list[str] = []
    value, _, calls = await driver_call(method, storage_error(JourneyContractError, hits))
    assert value.technical_refusal is CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert calls == [1] and hits == []


class HostileReasonKey(str):
    __hash__ = str.__hash__

    def __new__(cls, hits: list[str]) -> HostileReasonKey:
        instance = str.__new__(cls, "reason")
        instance.hits = hits
        return instance

    def __eq__(self, other: object) -> bool:
        self.hits.append("storage-key-equality")
        raise AssertionError(_MARKER)


def key_error(kind: Any, hits: list[str]) -> Exception:
    exc = kind(CapabilityRefusalReason.CONTRACT_MISMATCH)
    BaseException.__dict__["__dict__"].__set__(
        exc, {HostileReasonKey(hits): CapabilityRefusalReason.PURPOSE_DENIED}
    )
    return exc


@pytest.mark.parametrize("method,kind", _DURABLE_CASES)
async def test_actual_durable_apis_do_not_compare_hostile_exception_storage_keys(
    method: str, kind: Any
) -> None:
    hits: list[str] = []
    value, _, calls = await durable_call(method, key_error(kind, hits))
    assert value is CapabilityRefusalReason.SOURCE_UNAVAILABLE and calls == [1] and hits == []


async def test_actual_native_boundary_does_not_compare_hostile_exception_storage_keys() -> None:
    hits: list[str] = []
    value, _, calls = await native_call(key_error(JourneyContractError, hits))
    assert value is CapabilityRefusalReason.AUTHORITY_UNPROVEN and calls == [1] and hits == []


@pytest.mark.parametrize("method", ["accept_turn", "resume", "recover"])
async def test_actual_driver_does_not_compare_hostile_exception_storage_keys(method: str) -> None:
    hits: list[str] = []
    value, _, calls = await driver_call(method, key_error(JourneyContractError, hits))
    assert (
        value.technical_refusal is CapabilityRefusalReason.SOURCE_UNAVAILABLE and calls == [1] and hits == []
    )
