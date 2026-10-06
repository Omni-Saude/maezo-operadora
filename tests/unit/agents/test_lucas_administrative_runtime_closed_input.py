"""Distinct RUNTIME-SOURCE-F01 repair tests: actual UNIT graph/saver, synthetic ingress/driver."""

from __future__ import annotations

import socket
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from maezo.agents.lucas.administrative.runtime import (
    AdministrativeJourneyRuntime,
    RuntimeResumeStimulus,
    RuntimeTurnStimulus,
)
from maezo.gateway.capabilities.journeys.contracts import (
    CurrentJourneyTurn,
    JourneyDispatchOutcome,
)
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.pseudonymizer import Pseudonymizer
from tests.unit.agents.test_lucas_administrative_runtime import (
    CountingSaver,
    assembled,
    resume_input,
    seed,
    state,
    turn_input,
)

MARKER = "SYNTHETIC_CLOSED_EXTRA_PAYLOAD"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def denied(*args, **kwargs):
        calls.append(True)
        raise AssertionError("UNIT counter harness must not use network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    yield
    assert calls == []


class CountingWritesSaver(CountingSaver):
    """Actual InMemorySaver plus explicit count of every write path."""

    def __init__(self):
        super().__init__()
        self.write_calls = 0

    async def aput(self, *args, **kwargs):
        self.write_calls += 1
        return await super().aput(*args, **kwargs)

    async def aput_writes(self, *args, **kwargs):
        self.write_calls += 1
        return await super().aput_writes(*args, **kwargs)


def harness():
    b, ingress, driver, _, _ = assembled()
    saver = CountingWritesSaver()
    runtime = AdministrativeJourneyRuntime(
        binding=b,
        driver=driver,
        currentness=ingress,
        checkpointer=saver,
        pseudonymizer=Pseudonymizer(key=b"UNIT-closed-input-third-repair"),
        enabled=True,
    )
    return b, ingress, driver, saver, runtime


def assert_input_refused(result, ingress, driver, saver):
    assert isinstance(result, CapabilityRefusalReason)
    assert ingress.calls == saver.reads == saver.write_calls == len(driver.calls) == 0
    assert MARKER not in str(result)


def forged_storage(value: BaseModel, storage: str, fields: dict[str, Any]):
    if storage == "dict":
        value.__dict__.update(deepcopy(fields))
    elif storage == "extra":
        object.__setattr__(value, "__pydantic_extra__", deepcopy(fields))
    elif storage == "private":
        object.__setattr__(value, "__pydantic_private__", deepcopy(fields))
    else:
        object.__setattr__(value, "__pydantic_fields_set__", set(value.__pydantic_fields_set__) | set(fields))
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier", ["model", "dict", "constructed", "copy"])
async def test_positive_qualified_counter_harness_keeps_exact_declared_carriers(carrier):
    b, ingress, driver, saver, runtime = harness()
    packet = turn_input(b)
    if carrier == "dict":
        packet = packet.model_dump(mode="python")
    elif carrier == "constructed":
        packet = RuntimeTurnStimulus.model_construct(**deepcopy(packet.__dict__))
    elif carrier == "copy":
        packet = packet.model_copy(deep=True)
    result = await runtime.accept_turn(packet)
    assert isinstance(result, JourneyDispatchOutcome)
    assert ingress.calls == 3 and len(driver.calls) == 1
    assert saver.reads > 0 and saver.write_calls > 0
    resumed = await runtime.resume(resume_input())
    assert isinstance(resumed, JourneyDispatchOutcome)
    assert [call[0] for call in driver.calls] == ["turn", "resume"]


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier", ["model", "construct", "copy"])
@pytest.mark.parametrize("location", ["outer", "nested_turn"])
@pytest.mark.parametrize("storage", ["dict", "extra", "private", "fields_set"])
async def test_actual_unknown_storage_rejected_recursively_before_any_auth_or_saver(
    carrier, location, storage
):
    b, ingress, driver, saver, runtime = harness()
    packet = turn_input(b)
    if carrier == "construct":
        packet = RuntimeTurnStimulus.model_construct(**deepcopy(packet.__dict__))
        packet = packet.model_copy(
            update={"turn": CurrentJourneyTurn.model_construct(**deepcopy(packet.turn.__dict__))}
        )
    elif carrier == "copy":
        packet = packet.model_copy(deep=True)
    target = packet if location == "outer" else packet.turn
    forged_storage(
        target, storage, {"caller_config": {"thread_id": MARKER}, "source_authority_proof": MARKER}
    )
    result = await runtime.accept_turn(packet)
    assert_input_refused(result, ingress, driver, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "caller_config",
        "raw_payload",
        "source_receipt",
        "invocation_checkpoint",
        "proof",
        "binding_override",
        "observation_ref",
        "kind",
    ],
)
async def test_hidden_extras_never_normalize_collisions_or_inactive_proof_fields(field):
    b, ingress, driver, saver, runtime = harness()
    packet = turn_input(b)
    forged_storage(packet, "extra", {field: "resume" if field == "kind" else MARKER})
    result = await runtime.accept_turn(packet)
    assert_input_refused(result, ingress, driver, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["extra", "private", "fields_set"])
@pytest.mark.parametrize("malformed", ["string", "list", "integer"])
async def test_malformed_metadata_carriers_rejected_without_interpreting_proof(storage, malformed):
    b, ingress, driver, saver, runtime = harness()
    packet = turn_input(b)
    value = {"string": MARKER, "list": [MARKER], "integer": 1}[malformed]
    attr = {
        "extra": "__pydantic_extra__",
        "private": "__pydantic_private__",
        "fields_set": "__pydantic_fields_set__",
    }[storage]
    object.__setattr__(packet, attr, value)
    assert_input_refused(await runtime.accept_turn(packet), ingress, driver, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", ["extra", "private"])
async def test_empty_hidden_storage_contains_no_authority_or_additional_field(metadata):
    b, ingress, driver, saver, runtime = harness()
    packet = turn_input(b)
    forged_storage(packet, metadata, {})
    result = await runtime.accept_turn(packet)
    assert isinstance(result, JourneyDispatchOutcome)
    assert ingress.calls == 3 and len(driver.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_policy", ["allow", "forbid", "ignore"])
@pytest.mark.parametrize("location", ["outer", "nested_turn"])
async def test_foreign_base_model_even_valid_shape_has_no_supported_runtime_provenance(
    extra_policy, location
):
    b, ingress, driver, saver, runtime = harness()

    class Foreign(BaseModel):
        model_config = ConfigDict(extra=extra_policy)

    packet = turn_input(b)
    original = packet if location == "outer" else packet.turn
    foreign = Foreign.model_construct()
    foreign.__dict__.update(deepcopy(original.__dict__))
    packet = foreign if location == "outer" else packet.model_copy(update={"turn": foreign})
    assert_input_refused(await runtime.accept_turn(packet), ingress, driver, saver)


@pytest.mark.asyncio
async def test_foreign_extra_allow_wrapper_original_negative_probe_reproduced_with_zero_calls():
    b, ingress, driver, saver, runtime = harness()

    class OpenWrapper(BaseModel):
        model_config = ConfigDict(extra="allow")
        schema_version: str
        kind: str
        turn: object

    value = OpenWrapper(**turn_input(b).model_dump(), caller_config={"thread_id": MARKER}, raw_payload=MARKER)
    assert_input_refused(await runtime.accept_turn(value), ingress, driver, saver)


@pytest.mark.asyncio
async def test_unsupported_model_subclass_is_not_admitted_by_exact_field_projection():
    b, ingress, driver, saver, runtime = harness()

    class Derived(RuntimeTurnStimulus):
        pass

    value = Derived.model_construct(**deepcopy(turn_input(b).__dict__))
    assert_input_refused(await runtime.accept_turn(value), ingress, driver, saver)


@pytest.mark.asyncio
async def test_foreign_dataclass_cannot_be_projected_to_valid_runtime_packet():
    b, ingress, driver, saver, runtime = harness()

    @dataclass
    class ForeignWrapper:
        schema_version: str
        kind: str
        turn: object

    packet = turn_input(b)
    value = ForeignWrapper(packet.schema_version, packet.kind, packet.turn)
    assert_input_refused(await runtime.accept_turn(value), ingress, driver, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["dict", "extra", "private", "fields_set"])
async def test_nested_driver_binding_unknown_storage_zero_authentication(storage):
    b, ingress, driver, saver, runtime = harness()
    driver.binding = forged_storage(
        driver.binding.model_copy(deep=True), storage, {"source_authority_proof": MARKER}
    )
    assert_input_refused(await runtime.accept_turn(turn_input(b)), ingress, driver, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["dict", "extra", "private", "fields_set"])
async def test_driver_output_hidden_storage_rejected_after_one_call_without_disclosure(storage):
    b, ingress, driver, saver, runtime = harness()
    original = driver._result

    async def output(*args, **kwargs):
        value = await original(*args, **kwargs)
        return forged_storage(value, storage, {"raw_payload": MARKER, "source_receipt": MARKER})

    driver._result = output
    result = await runtime.accept_turn(turn_input(b))
    assert isinstance(result, CapabilityRefusalReason) and len(driver.calls) == 1
    assert ingress.calls == 2 and saver.reads > 0
    assert (
        MARKER not in str(result) and MARKER not in repr(saver.storage) and MARKER not in repr(saver.writes)
    )


@pytest.mark.asyncio
async def test_foreign_driver_output_model_refuses_whole_carrier_not_selected_fields():
    b, ingress, driver, saver, runtime = harness()

    class ForeignOutcome(BaseModel):
        model_config = ConfigDict(extra="allow")

    original = driver._result

    async def output(*args, **kwargs):
        value = await original(*args, **kwargs)
        foreign = ForeignOutcome.model_construct()
        foreign.__dict__.update(deepcopy(value.__dict__))
        return foreign

    driver._result = output
    result = await runtime.accept_turn(turn_input(b))
    assert isinstance(result, CapabilityRefusalReason) and len(driver.calls) == 1
    assert MARKER not in repr(saver.storage)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["caller_config", "proof", "raw_payload", "__pydantic_extra__", "resume", "outcome"]
)
async def test_plain_dictionary_unknown_fields_remain_visible_to_exact_parser(field):
    b, ingress, driver, saver, runtime = harness()
    value = turn_input(b).model_dump(mode="python")
    value[field] = {"trusted": True, "payload": MARKER}
    assert_input_refused(await runtime.accept_turn(value), ingress, driver, saver)


@pytest.mark.asyncio
async def test_nested_plain_dictionary_proof_is_not_removed_by_serialization():
    b, ingress, driver, saver, runtime = harness()
    value = turn_input(b).model_dump(mode="python")
    value["turn"]["source_receipt_proof"] = {"trusted": True, "payload": MARKER}
    assert_input_refused(await runtime.accept_turn(value), ingress, driver, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["dict", "extra", "private", "fields_set"])
async def test_resume_forged_storage_refuses_before_qualified_reference_lookup(storage):
    _, ingress, driver, saver, runtime = harness()
    value = forged_storage(resume_input(), storage, {"turn": MARKER, "caller_config": MARKER})
    assert type(value) is RuntimeResumeStimulus
    assert_input_refused(await runtime.resume(value), ingress, driver, saver)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["outer_stimulus", "nested_turn", "binding", "serialized_stimulus"])
async def test_saved_hidden_or_serialized_extras_refuse_before_driver_without_cached_disclosure(channel):
    b, ingress, driver, saver, runtime = harness()
    await seed(runtime, saver, state(runtime, b))
    saver.write_calls = 0
    if channel == "binding":
        saver.overlay = {
            "binding": forged_storage(b.model_copy(deep=True), "extra", {"caller_config": MARKER})
        }
    else:
        packet = turn_input(b)
        if channel == "serialized_stimulus":
            value = packet.model_dump(mode="python")
            value["__pydantic_extra__"] = {"source_receipt": MARKER}
        else:
            target = packet if channel == "outer_stimulus" else packet.turn
            forged_storage(target, "extra", {"source_receipt": MARKER})
            value = packet
        saver.overlay = {"stimulus": value}
    result = await runtime.accept_turn(turn_input(b))
    assert isinstance(result, CapabilityRefusalReason)
    assert ingress.calls == 1 and saver.reads == 1 and len(driver.calls) == saver.write_calls == 0
    assert MARKER not in str(result)
