"""Second distinct root repair: real local runtime/UNIT saver, no provider/engine acceptance."""

import socket
from copy import deepcopy

import pytest

from maezo.gateway.capabilities.journeys.contracts import JourneyBinding, JourneyDispatchOutcome
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from tests.unit.agents.test_lucas_administrative_runtime import assembled, resume_input, turn_input
from tests.unit.platform.test_administrative_root_epoch import call, root


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    calls = []

    def denied(*args, **kwargs):
        calls.append(True)
        raise AssertionError("UNIT root snapshot control must not access a provider")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    yield
    assert calls == []


class HookRef(str):
    """Strict-compatible value with hooks that must never run at these boundaries."""

    def __new__(cls, value, calls, mutate=lambda: None):
        result = str.__new__(cls, value)
        result.calls, result.mutate = calls, mutate
        return result

    def __deepcopy__(self, memo):
        self.calls.append("deepcopy")
        self.mutate()
        return "UNIT-rewritten-reference"

    def __eq__(self, other):
        self.calls.append("eq")
        self.mutate()
        return str.__eq__(self, other)

    __hash__ = str.__hash__

    def __str__(self):
        self.calls.append("str")
        self.mutate()
        return str.__str__(self)

    def __reduce_ex__(self, protocol):
        self.calls.append("serialize")
        self.mutate()
        raise AssertionError("raw reference serializer must never run")


@pytest.mark.parametrize("location", ["candidate", "runtime", "driver"])
@pytest.mark.parametrize("field", [f for f in JourneyBinding.model_fields if f != "enabled"])
@pytest.mark.parametrize("literal", ["same", "foreign"])
def test_ownership_uses_supported_original_primitives_without_any_leaf_hooks(location, field, literal):
    b, ingress, driver, saver, runtime = assembled()
    candidate = b.model_copy(deep=True)
    target = {
        "candidate": candidate,
        "runtime": runtime._AdministrativeJourneyRuntime__binding,
        "driver": driver.binding,
    }[location]
    calls = []
    actual = getattr(target, field) if literal == "same" else "UNIT-foreign-reference"
    target.__dict__[field] = HookRef(actual, calls)
    assert runtime.is_bound_to(candidate) is False
    assert calls == []
    assert ingress.calls == saver.reads == len(driver.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("field", [f for f in JourneyBinding.model_fields if f != "enabled"])
async def test_actual_root_foreign_str_carrier_refuses_before_all_dependencies(mode, field):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    calls = []
    original_tenant = target.tenant_id
    target.administrative_binding.__dict__[field] = HookRef(
        "UNIT-foreign-reference", calls, lambda: setattr(target, "tenant_id", "UNIT-hook-mutated-root")
    )
    result = await call(mode, target, b)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert calls == [] and target.tenant_id == original_tenant
    assert ingress.calls == saver.reads == len(driver.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize(
    "field", ["journey_ref", "source_completion_receipt_ref", "verified_transition_ref", "source_case_refs"]
)
async def test_returned_ref_hook_is_not_copied_or_serialized_after_root_guard(mode, field):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    calls = []

    async def forged(*args):
        result = await driver._result(mode, args)
        ref = HookRef(b.journey_ref, calls, lambda: setattr(target, "tenant_id", "UNIT-mutated-after-guard"))
        return result.model_copy(update={field: (ref,) if field == "source_case_refs" else ref})

    if mode == "webhook":
        runtime.accept_turn = forged
    else:
        runtime.resume = forged
    result = await call(mode, target, b)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert calls == [] and target.tenant_id == b.tenant_ref
    assert len(driver.calls) == 1 and ingress.calls == saver.reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("carrier", ["model", "dict", "construct", "copy"])
async def test_complete_plain_canonical_result_stays_positive_and_exact(mode, carrier):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)

    async def returned(*args):
        result = await driver._result(mode, args)
        if carrier == "dict":
            return deepcopy(result.__dict__)
        if carrier == "construct":
            return JourneyDispatchOutcome.model_construct(**deepcopy(result.__dict__))
        if carrier == "copy":
            return result.model_copy(deep=True)
        return result

    if mode == "webhook":
        runtime.accept_turn = returned
    else:
        runtime.resume = returned
    result = await call(mode, target, b)
    assert type(result) is JourneyDispatchOutcome
    assert type(result.journey_ref) is str and result.journey_ref == b.journey_ref
    assert target.tenant_id == b.tenant_ref and len(driver.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("change", ["missing", "extra", "private", "fieldset"])
async def test_complete_return_metadata_is_checked_without_filtering(mode, change):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)

    async def forged(*args):
        result = await driver._result(mode, args)
        if change == "missing":
            object.__delattr__(result, "__pydantic_private__")
        elif change == "fieldset":
            object.__setattr__(result, "__pydantic_fields_set__", {"hidden-output-field"})
        else:
            object.__setattr__(result, "__pydantic_" + change + "__", {"hidden-output-field": "PRIVATE"})
        return result

    if mode == "webhook":
        runtime.accept_turn = forged
    else:
        runtime.resume = forged
    assert await call(mode, target, b) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert len(driver.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
async def test_final_root_epoch_guard_occurs_after_canonical_result_validation(mode, monkeypatch):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)

    async def returned(*args):
        return await driver._result(mode, args)

    if mode == "webhook":
        runtime.accept_turn = returned
    else:
        runtime.resume = returned
    original = JourneyDispatchOutcome.model_validate

    def validated_then_mutate(value, *args, **kwargs):
        canonical = original(value, *args, **kwargs)
        target.tenant_id = "UNIT-root-changed-during-validation"
        return canonical

    monkeypatch.setattr(JourneyDispatchOutcome, "model_validate", validated_then_mutate)
    result = await call(mode, target, b)
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert len(driver.calls) == 1


@pytest.mark.parametrize("location", ["candidate", "driver", "runtime"])
def test_unsupported_mapping_key_never_runs_equality_or_copy_hooks(location):
    b, ingress, driver, saver, runtime = assembled()
    candidate = b.model_copy(deep=True)
    target = {
        "candidate": candidate,
        "driver": driver.binding,
        "runtime": runtime._AdministrativeJourneyRuntime__binding,
    }[location]
    calls = []
    key = HookRef("tenant_ref", calls)
    target.__dict__ = {
        key if name == "tenant_ref" else name: value for name, value in target.__dict__.items()
    }
    calls.clear()  # Construction outside the boundary may compare colliding dictionary keys.
    assert runtime.is_bound_to(candidate) is False
    assert calls == [] and ingress.calls == saver.reads == len(driver.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["webhook", "resume"])
@pytest.mark.parametrize("location", ["tenant", "stimulus", "nested_handoff"])
async def test_root_validates_complete_input_before_any_raw_leaf_hook(mode, location):
    b, ingress, driver, saver, runtime = assembled()
    target = root(mode, b, runtime)
    stimulus = turn_input(b) if mode == "webhook" else resume_input()
    calls = []
    ref = HookRef(b.tenant_ref, calls, lambda: setattr(target, "tenant_id", "UNIT-callback-root"))
    if location == "tenant":
        target.tenant_id = ref
    elif mode == "resume":
        stimulus.__dict__["observation_ref"] = ref
    elif location == "stimulus":
        stimulus.turn.__dict__["manifestation_evidence_ref"] = ref
    else:
        object.__setattr__(stimulus.turn.handoff, "tenant_ref", ref)
    result = await (
        target.accept_administrative_turn(stimulus)
        if mode == "webhook"
        else target.resume_administrative(stimulus)
    )
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert calls == [] and ingress.calls == saver.reads == len(driver.calls) == 0
