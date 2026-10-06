"""Distinct ROOT source repair: actual runtime/UNIT saver, synthetic authority and driver facts."""

from __future__ import annotations

import asyncio
import socket
from copy import deepcopy

import pytest
from pydantic import BaseModel, ConfigDict

from maezo.agents.lucas.administrative.runtime import AdministrativeJourneyRuntime
from maezo.gateway.capabilities.journeys.contracts import JourneyBinding, JourneyDispatchOutcome
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.pseudonymizer import Pseudonymizer
from tests.unit.agents.test_lucas_administrative_runtime import (
    CountingSaver,
    assembled,
    resume_input,
    turn_input,
)
from tests.unit.gateway.capabilities.journeys.test_effect_execution import harness as effect_harness
from tests.unit.platform.integrations.test_administrative_resume_attachment_disabled import handler
from tests.unit.platform.webhooks.test_administrative_attachment_disabled import dispatcher

MARKER = "UNIT_ROOT_EPOCH_PRIVATE_MARKER"
METADATA = ("__pydantic_extra__", "__pydantic_private__", "__pydantic_fields_set__")


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    calls = []

    def denied(*args, **kwargs):
        calls.append(True)
        raise AssertionError("UNIT root boundary must not contact network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    yield
    assert calls == []


def root(mode, b, runtime):
    target = dispatcher(b.tenant_ref) if mode == "webhook" else handler(b.tenant_ref)
    target.administrative_runtime = runtime
    target.administrative_binding = b.model_copy(deep=True)
    return target


async def call(mode, target, b):
    return await (
        target.accept_administrative_turn(turn_input(b))
        if mode == "webhook"
        else target.resume_administrative(resume_input())
    )


def change_field(binding, field):
    current = getattr(binding, field)
    if type(current) is bool:
        return not current
    if field == "task_ref":
        return "journey.suporte.step"
    return "UNIT-other-scope"


def install_await_hook(ingress, driver, saver, when, mutate):
    if when == "driver":

        async def hook():
            await asyncio.sleep(0)
            mutate()

        driver.on_call = hook
    elif when in {"first_currentness", "final_currentness"}:
        original = ingress.verify

        async def checked(*args, **kwargs):
            result = await original(*args, **kwargs)
            if ingress.calls == (1 if when == "first_currentness" else 3):
                mutate()
            return result

        ingress.verify = checked
    else:
        original = saver.aget_tuple

        async def checked(*args, **kwargs):
            result = await original(*args, **kwargs)
            if saver.reads == (1 if when == "first_saver" else 3):
                mutate()
            return result

        saver.aget_tuple = checked


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
async def test_unchanged_typed_root_returns_exact_once_current_result(mode):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    result = await call(mode, target, b)
    assert isinstance(result, JourneyDispatchOutcome)
    assert result.journey_ref == b.journey_ref and len(driver.calls) == 1
    assert ingress.calls == saver.reads == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize(
    "when", ["first_currentness", "first_saver", "driver", "final_currentness", "last_saver"]
)
@pytest.mark.parametrize(
    "change",
    [
        "tenant",
        "runtime_none",
        "runtime_equal_scope_other_object",
        "binding_equal_copy",
        "binding_none",
        "delete_tenant",
        "delete_runtime",
        "delete_binding",
    ],
)
async def test_root_scope_epoch_changes_during_every_forwarding_await_deny_protected_disclosure(
    mode, when, change
):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    original_binding = target.administrative_binding
    _, other_ingress, other_driver, other_saver, other_runtime = assembled()

    def mutate():
        if change == "tenant":
            target.tenant_id = "UNIT-other-root"
        elif change == "runtime_none":
            target.administrative_runtime = None
        elif change == "runtime_equal_scope_other_object":
            target.administrative_runtime = other_runtime
        elif change == "binding_equal_copy":
            target.administrative_binding = original_binding.model_copy(deep=True)
        elif change == "binding_none":
            target.administrative_binding = None
        else:
            delattr(
                target,
                {
                    "delete_tenant": "tenant_id",
                    "delete_runtime": "administrative_runtime",
                    "delete_binding": "administrative_binding",
                }[change],
            )

    install_await_hook(ingress, driver, saver, when, mutate)
    result = await call(mode, target, b)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN and len(driver.calls) == 1
    assert ingress.calls == 3 and saver.reads == 3
    assert other_ingress.calls == other_saver.reads == len(other_driver.calls) == 0
    # Genuine original UNIT runtime fact remains recorded in its original scoped saver.
    saved = await saver.aget_tuple(runtime._config())
    assert saved.checkpoint["channel_values"]["outcome"]["journey_ref"] == b.journey_ref
    assert (
        saved.checkpoint["channel_values"]["outcome"]["source_completion_receipt_ref"]
        == "unit-synthetic-result-1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("field", list(JourneyBinding.model_fields))
@pytest.mark.parametrize("carrier", ["retained_alias", "replacement"])
async def test_all11_original_binding_pins_survive_alias_mutation_and_replacement(mode, field, carrier):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    retained_alias = target.administrative_binding
    value = change_field(retained_alias, field)

    def mutate():
        if carrier == "retained_alias":
            retained_alias.__dict__[field] = value
        else:
            target.administrative_binding = retained_alias.model_copy(update={field: value})

    install_await_hook(ingress, driver, saver, "driver", mutate)
    result = await call(mode, target, b)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN and len(driver.calls) == 1
    assert ingress.calls == 3 and saver.reads == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("metadata", METADATA)
@pytest.mark.parametrize("change", ["missing", "invalid"])
async def test_root_binding_metadata_becomes_invalid_after_await_no_outcome_disclosure(
    mode, metadata, change
):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)

    def mutate():
        if change == "missing":
            object.__delattr__(target.administrative_binding, metadata)
        else:
            object.__setattr__(target.administrative_binding, metadata, {"caller_config": MARKER})

    install_await_hook(ingress, driver, saver, "driver", mutate)
    result = await call(mode, target, b)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN and len(driver.calls) == 1
    assert MARKER not in str(result)


@pytest.mark.parametrize("metadata", METADATA)
@pytest.mark.parametrize("location", ["candidate", "driver", "runtime_snapshot"])
@pytest.mark.parametrize("change", ["missing", "nonempty", "malformed"])
def test_ownership_query_is_total_false_for_actual_missing_or_forged_model_metadata(
    metadata, location, change
):
    b, ingress, driver, saver, runtime = assembled()
    candidate = b.model_copy(deep=True)
    target = {
        "candidate": candidate,
        "driver": driver.binding,
        "runtime_snapshot": runtime._AdministrativeJourneyRuntime__binding,
    }[location]
    if change == "missing":
        object.__delattr__(target, metadata)
    else:
        object.__setattr__(
            target, metadata, {"source_authority_proof": MARKER} if change == "nonempty" else [MARKER]
        )
    assert runtime.is_bound_to(candidate) is False
    assert ingress.calls == saver.reads == len(driver.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("metadata", METADATA)
@pytest.mark.parametrize("change", ["missing", "nonempty"])
async def test_root_malformed_captured_metadata_denies_before_any_auth_saver_driver(mode, metadata, change):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    if change == "missing":
        object.__delattr__(target.administrative_binding, metadata)
    else:
        object.__setattr__(target.administrative_binding, metadata, {"source_authority_proof": MARKER})
    assert await call(mode, target, b) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert ingress.calls == saver.reads == len(driver.calls) == 0


@pytest.mark.parametrize("carrier", ["model", "dict", "construct", "copy"])
def test_matching_ownership_query_remains_true_without_any_dependencies(carrier):
    b, ingress, driver, saver, runtime = assembled(enabled=False, checkpointer=None)
    candidate = b
    if carrier == "dict":
        candidate = b.model_dump(mode="python")
    elif carrier == "construct":
        candidate = JourneyBinding.model_construct(**deepcopy(b.__dict__))
    elif carrier == "copy":
        candidate = b.model_copy(deep=True)
    assert runtime.is_bound_to(candidate) is True
    assert ingress.calls == saver.reads == len(driver.calls) == 0


@pytest.mark.parametrize(
    "carrier",
    ["none", "foreign_model", "partial_dict", "dict_extra", "unknown_dict_fieldset", "explosive_value"],
)
def test_unsupported_or_unreadable_candidate_scope_query_false_without_exception_or_calls(carrier):
    b, ingress, driver, saver, runtime = assembled()
    candidate = None
    if carrier == "foreign_model":

        class Foreign(BaseModel):
            model_config = ConfigDict(extra="allow")

        candidate = Foreign.model_construct()
        candidate.__dict__.update(deepcopy(b.__dict__))
    elif carrier == "partial_dict":
        candidate = {"tenant_ref": b.tenant_ref}
    elif carrier in {"dict_extra", "unknown_dict_fieldset"}:
        candidate = b.model_dump(mode="python")
        candidate["caller_config" if carrier == "dict_extra" else "__pydantic_fields_set__"] = MARKER
    elif carrier == "explosive_value":

        class Unreadable:
            def __deepcopy__(self, memo):
                raise RuntimeError(MARKER)

        candidate = b.model_copy(update={"data_policy_ref": Unreadable()})
    assert runtime.is_bound_to(candidate) is False
    assert ingress.calls == saver.reads == len(driver.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("malformed", ["foreign_scope", "extra_output", "raw_string"])
async def test_root_revalidates_returned_scope_and_closed_output_before_disclosure(mode, malformed):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)

    async def malformed_return(*args):
        value = await driver._result(mode, args)
        if malformed == "foreign_scope":
            return value.model_copy(update={"journey_ref": "UNIT-foreign-return"})
        if malformed == "extra_output":
            object.__setattr__(value, "__pydantic_extra__", {"raw_payload": MARKER})
            return value
        return MARKER

    if mode == "webhook":
        runtime.accept_turn = malformed_return
    else:
        runtime.resume = malformed_return
    result = await call(mode, target, b)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN and len(driver.calls) == 1
    assert MARKER not in str(result) and ingress.calls == saver.reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
async def test_late_root_scope_refusal_preserves_actual_durable_unit_source_fact_without_resubmit(mode):
    h = effect_harness()
    saver = CountingSaver()
    runtime = AdministrativeJourneyRuntime(
        binding=h.binding,
        driver=h.driver,
        currentness=h.driver.currentness,
        checkpointer=saver,
        pseudonymizer=Pseudonymizer(key=b"UNIT-genuine-durable-root-fact"),
        enabled=True,
    )
    target = root(mode, h.binding, runtime)

    def after_commit():
        target.tenant_id = "UNIT-other-root"
        target.administrative_runtime = None

    h.journal.on_result = after_commit
    result = await call(mode, target, h.binding)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert h.source.effects == h.source.calls == 1
    assert h.journal.snapshot.technical_state.value == "RESPONSE_RECORDED"
    assert h.journal.snapshot.source_receipt_ref == "unit-domain-receipt"
    assert h.journal.descriptor.envelope.idempotency_key == h.action.envelope.idempotency_key
    assert "mark_uncertain" not in h.authority.events
    assert "begin_dispatch" in h.authority.events


class UnsupportedConvertingValue:
    """Strict field validation must happen before any arbitrary deepcopy hook."""

    def __init__(self, value):
        self.value, self.copy_calls = value, 0

    def __deepcopy__(self, memo):
        self.copy_calls += 1
        return self.value


@pytest.mark.parametrize("location", ["candidate", "driver", "runtime_snapshot"])
def test_unsupported_binding_value_cannot_normalize_into_valid_scope_or_execute_copy_hooks(location):
    b, ingress, driver, saver, runtime = assembled()
    candidate = b.model_copy(deep=True)
    value = UnsupportedConvertingValue(b.data_policy_ref)
    target = {
        "candidate": candidate,
        "driver": driver.binding,
        "runtime_snapshot": runtime._AdministrativeJourneyRuntime__binding,
    }[location]
    target.__dict__["data_policy_ref"] = value
    assert runtime.is_bound_to(candidate) is False
    assert value.copy_calls == ingress.calls == saver.reads == len(driver.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("when", ["before", "driver"])
async def test_root_malformed_binding_values_never_normalize_at_pre_or_post_forward_copy(mode, when):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    value = UnsupportedConvertingValue(b.data_policy_ref)

    def mutate():
        target.administrative_binding.__dict__["data_policy_ref"] = value

    if when == "before":
        mutate()
    else:
        install_await_hook(ingress, driver, saver, "driver", mutate)
    assert await call(mode, target, b) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert value.copy_calls == 0
    assert len(driver.calls) == (0 if when == "before" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
async def test_root_returned_reference_type_cannot_be_coerced_by_a_copy_hook(mode):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    value = UnsupportedConvertingValue(b.journey_ref)

    async def malformed_return(*args):
        result = await driver._result(mode, args)
        return result.model_copy(update={"journey_ref": value})

    if mode == "webhook":
        runtime.accept_turn = malformed_return
    else:
        runtime.resume = malformed_return
    assert await call(mode, target, b) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert value.copy_calls == 0 and len(driver.calls) == 1
