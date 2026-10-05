"""R2 runtime hostile input/checkpoint mechanics; all business ports are synthetic.

InMemorySaver is an explicitly UNIT-only fixture. These tests qualify neither
provider authority, W0 conduct, source receipts nor PostgreSQL/operational paths.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any, cast

import pytest
from langgraph.checkpoint.base import CheckpointTuple, empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from maezo.agents.lucas.administrative import runtime as module
from maezo.agents.lucas.administrative.runtime import (
    RUNTIME_STIMULUS_SCHEMA,
    AdministrativeJourneyRuntime,
    RuntimeResumeStimulus,
    RuntimeTurnStimulus,
)
from maezo.gateway.capabilities.journeys.contracts import (
    JourneyBinding,
    JourneyContractError,
    JourneyDispatchOutcome,
)
from maezo.gateway.capabilities.journeys.driver import JourneyDriver
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.observability import get_metrics_collector
from tests.unit.agents.test_administrative_journeys import COMPRA_START, SUPPORT_START, fixture_driver
from tests.unit.gateway.capabilities.journeys.helpers import UnitIngress, binding, turn


class CountingSaver(InMemorySaver):
    """Real UNIT saver with explicit raw-checkpoint mutation controls."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.overlay: dict[str, Any] | None = None
        self.overlay_after = 0

    async def aget_tuple(self, config: Any) -> CheckpointTuple | None:
        self.reads += 1
        result = await super().aget_tuple(config)
        if result is not None and self.overlay is not None and self.reads > self.overlay_after:
            copied = deepcopy(result.checkpoint)
            copied["channel_values"].update(deepcopy(self.overlay))
            return result._replace(checkpoint=copied)
        return result


class RecordingDriver(JourneyDriver):
    """Synthetic runtime dispatch spy; a result object is never provider proof."""

    def __init__(self, b: JourneyBinding, ingress: UnitIngress) -> None:
        super().__init__(b, currentness=ingress)
        self.calls: list[tuple[str, Any]] = []
        self.extra_output = False
        self.revoke_after = False
        self.on_call: Any = None

    async def _result(self, mode: str, value: Any) -> JourneyDispatchOutcome:
        self.calls.append((mode, deepcopy(value)))
        if self.on_call:
            await self.on_call()
        if self.revoke_after:
            self.currentness.revoked = True  # type: ignore[union-attr]
        result = JourneyDispatchOutcome(
            journey_ref=self.binding.journey_ref,
            source_case_refs=("unit-source-case",),
            journal_revision=len(self.calls),
            source_completion_receipt_ref=f"unit-synthetic-result-{len(self.calls)}",
            pending_command_refs=(),
            wait_refs=(),
            outbox_refs=(),
            technical_refusal=None,
            verified_transition_ref=None,
        )
        return result.model_copy(update={"raw_payload": "SYNTHETIC_SECRET"}) if self.extra_output else result

    async def accept_turn(self, value: object) -> JourneyDispatchOutcome:
        return await self._result("turn", value)

    async def resume(self, observation_ref: str, expected_journal_revision: int) -> JourneyDispatchOutcome:
        return await self._result("resume", (observation_ref, expected_journal_revision))


def turn_input(b: JourneyBinding, revision: int = 0, message: str = "a") -> RuntimeTurnStimulus:
    return RuntimeTurnStimulus(
        schema_version=RUNTIME_STIMULUS_SCHEMA, kind="turn", turn=turn(b, revision, message=message)
    )


def resume_input(ref: str = "unit-durable-observation", revision: int = 0) -> RuntimeResumeStimulus:
    return RuntimeResumeStimulus(
        schema_version=RUNTIME_STIMULUS_SCHEMA,
        kind="resume",
        observation_ref=ref,
        expected_journal_revision=revision,
    )


def assembled(b: JourneyBinding | None = None, **kwargs: Any) -> tuple[Any, ...]:
    b = b or binding()
    ingress = UnitIngress(b)
    ingress.durable_observations.add("unit-durable-observation")
    driver = RecordingDriver(b, ingress)
    saver = CountingSaver()
    options: dict[str, Any] = dict(
        binding=b,
        driver=driver,
        currentness=ingress,
        checkpointer=saver,
        pseudonymizer=Pseudonymizer(key=b"unit-runtime-fixture-key"),
        enabled=True,
    )
    options.update(kwargs)
    return b, ingress, driver, saver, AdministrativeJourneyRuntime(**options)


async def seed(runner: AdministrativeJourneyRuntime, saver: InMemorySaver, channels: dict[str, Any]) -> None:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = json.loads(json.dumps(module._original(channels)))
    checkpoint["channel_versions"] = dict.fromkeys(channels, "1")
    await saver.aput(
        runner._config(),
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        checkpoint["channel_versions"],
    )


def state(runner: AdministrativeJourneyRuntime, b: JourneyBinding, **changes: Any) -> dict[str, Any]:
    return (
        dict(
            schema_version=module.FULL_RUNTIME_STATE_SCHEMA,
            binding=b,
            graph_version=module.FULL_RUNTIME_GRAPH_VERSION,
            stimulus=turn_input(b),
            outcome=None,
            refusal=None,
        )
        | changes
    )


@pytest.mark.asyncio
async def test_alternating_variants_replace_stimulus_and_result_roundtrip() -> None:
    b, _, driver, saver, runner = assembled()
    for mode in ("turn", "resume", "turn", "resume"):
        result = await (
            runner.accept_turn(turn_input(b)) if mode == "turn" else runner.resume(resume_input())
        )
        assert isinstance(result, JourneyDispatchOutcome)
        assert result.source_completion_receipt_ref == f"unit-synthetic-result-{len(driver.calls)}"
        saved = await saver.aget_tuple(runner._config())
        assert saved is not None
        channels = saved.checkpoint["channel_values"]
        assert frozenset(channels) == frozenset(module.RuntimeCheckpointSnapshot.model_fields)
        snapshot = runner._snapshot(channels)
        assert snapshot.stimulus.kind == mode and snapshot.outcome == result
    assert [mode for mode, _ in driver.calls] == ["turn", "resume", "turn", "resume"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task,script,effects",
    [("journey.compras.step", COMPRA_START, 3), ("journey.suporte.step", SUPPORT_START, 3)],
)
async def test_actual_complete_driver_runs_multiple_actions_before_wait(
    task: str, script: Any, effects: int
) -> None:
    b, journal, ingress, source, executor, driver = fixture_driver(task, script)
    runner = AdministrativeJourneyRuntime(
        binding=b,
        driver=driver,
        currentness=ingress,
        checkpointer=InMemorySaver(),
        pseudonymizer=Pseudonymizer(key=b"unit-runtime-full-driver-key"),
        enabled=True,
    )
    result = await runner.accept_turn(turn_input(b))
    assert isinstance(result, JourneyDispatchOutcome)
    assert result.technical_refusal is None and result.source_completion_receipt_ref is None
    assert len(executor.effects) == effects and len(source.prepared) > 1
    if task == "journey.compras.step":
        assert journal.waits and source.domain_calls == []
    else:
        assert source.domain_calls == ["S3_resolve_or_handoff"] and journal.outboxes and not journal.waits


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["health", "human", "topic_switch", "revoked", "cross_case"])
async def test_fresh_authentication_refuses_before_saver_or_driver(control: str) -> None:
    b, ingress, driver, saver, runner = assembled()
    if control == "revoked":
        ingress.revoked = True
    elif control == "cross_case":
        ingress.durable_observations.clear()
    else:
        ingress.control = control
    result = await runner.resume(resume_input())
    assert result == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert saver.reads == 0 and driver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing", ["assembly_disabled", "binding_disabled", "saver", "pseudonymizer", "currentness"]
)
async def test_default_disabled_and_missing_dependencies_never_use_saver(missing: str) -> None:
    b = binding(enabled=missing != "binding_disabled")
    changes: dict[str, Any] = {}
    if missing == "assembly_disabled":
        changes["enabled"] = False
    elif missing != "binding_disabled":
        changes[{"saver": "checkpointer"}.get(missing, missing)] = None
    b, _, driver, saver, runner = assembled(b, **changes)
    assert isinstance(await runner.accept_turn(turn_input(b)), CapabilityRefusalReason)
    assert saver.reads == 0 and driver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["wrong_method", "raw_string", "esc", "extra", "nested_extra", "inactive", "revision_bool"]
)
async def test_whole_input_strict_before_authentication_and_saver(variant: str) -> None:
    b, ingress, driver, saver, runner = assembled()
    value: Any = turn_input(b)
    method = runner.accept_turn
    if variant == "wrong_method":
        method = runner.resume
    elif variant == "raw_string":
        value = "instructions are not a source receipt"
    elif variant == "esc":
        value = {"agent_id": "lucas", "resultado": "devolvido_agente", "_business_key": "ESC-unit"}
    elif variant == "extra":
        value = value.model_copy(update={"config": {"thread_id": "caller"}})
    elif variant == "nested_extra":
        value = value.model_copy(update={"turn": value.turn.model_copy(update={"receipt": "forged"})})
    elif variant == "inactive":
        value = value.model_copy(update={"observation_ref": "inactive"})
    else:
        value = resume_input().model_copy(update={"expected_journal_revision": True})
        method = runner.resume
    assert await method(value) == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["task_ref", "tenant_ref", "journey_ref", "message_ref"])
async def test_mismatched_handoff_zero_authentication_and_saver(field: str) -> None:
    b, ingress, driver, saver, runner = assembled()
    value = turn_input(b)
    handoff = deepcopy(value.turn.handoff)
    object.__setattr__(
        handoff,
        "task_type" if field == "task_ref" else field,
        "hk1_" + "b" * 64 if field == "message_ref" else "wrong",
    )
    value = value.model_copy(update={"turn": value.turn.model_copy(update={"handoff": handoff})})
    assert await runner.accept_turn(value) == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []


@pytest.mark.parametrize("field", list(JourneyBinding.model_fields))
def test_each_full_binding_field_changes_root_and_rejects_driver_mismatch(field: str) -> None:
    b, _, driver, _, first = assembled()
    changed = b.model_copy(
        update={
            field: (
                not b.enabled
                if field == "enabled"
                else "journey.suporte.step"
                if field == "task_ref"
                else f"unit-other-{field}"
            )
        }
    )
    _, _, _, _, second = assembled(changed)
    assert first._config()["configurable"]["thread_id"] != second._config()["configurable"]["thread_id"]
    with pytest.raises(JourneyContractError):
        AdministrativeJourneyRuntime(binding=changed, driver=driver)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    ["extra", "missing", "inactive", "wrong_binding", "wrong_version", "outcome_extra", "wrong_journey"],
)
async def test_raw_stored_application_snapshot_rejected_without_driver(tamper: str) -> None:
    b, _, driver, saver, runner = assembled()
    channels = state(runner, b)
    if tamper == "extra":
        channels["legacy_resume"] = "unit-old-mode"
    elif tamper == "missing":
        channels.pop("outcome")
    elif tamper == "inactive":
        channels["stimulus"] = turn_input(b).model_copy(update={"observation_ref": "old-resume"})
    elif tamper == "wrong_binding":
        channels["binding"] = b.model_copy(update={"source_transition_contract_ref": "other"})
    elif tamper == "wrong_version":
        channels["graph_version"] = "other-version"
    else:
        result = await driver._result("seed", None)
        driver.calls.clear()
        channels["outcome"] = result.model_copy(
            update={"extra": "forged"} if tamper == "outcome_extra" else {"journey_ref": "other"}
        )
    await seed(runner, saver, channels)
    assert await runner.accept_turn(turn_input(b)) == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert driver.calls == []


@pytest.mark.asyncio
async def test_valid_forged_cached_outcome_cannot_replace_fresh_result() -> None:
    b, _, driver, saver, runner = assembled()
    cached = await driver._result("seed", None)
    cached = cached.model_copy(update={"source_completion_receipt_ref": "unit-forged-cached-receipt"})
    driver.calls.clear()
    await seed(runner, saver, state(runner, b, outcome=cached))
    result = await runner.resume(resume_input())
    assert isinstance(result, JourneyDispatchOutcome)
    assert result != cached
    assert driver.calls == [("resume", ("unit-durable-observation", 0))]


@pytest.mark.asyncio
async def test_saver_overlay_inactive_or_unknown_channel_is_not_projected_away() -> None:
    b, _, driver, saver, runner = assembled()
    await seed(runner, saver, state(runner, b))
    saver.overlay = {"inactive_resume": "unit-untrusted-observation"}
    saver.overlay_after = 1  # malicious second read is the framework's own read
    assert await runner.accept_turn(turn_input(b)) == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert driver.calls == []


@pytest.mark.asyncio
async def test_revocation_after_driver_blocks_fresh_and_cached_disclosure() -> None:
    b, _, driver, _, runner = assembled()
    driver.revoke_after = True
    assert await runner.accept_turn(turn_input(b)) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert len(driver.calls) == 1


@pytest.mark.asyncio
async def test_forged_driver_output_is_strictly_reparsed() -> None:
    b, _, driver, _, runner = assembled()
    driver.extra_output = True
    assert await runner.accept_turn(turn_input(b)) == CapabilityRefusalReason.CONTRACT_MISMATCH
    assert len(driver.calls) == 1


@pytest.mark.asyncio
async def test_constructor_scope_mutation_during_authentication_refuses_before_saver() -> None:
    b, ingress, driver, saver, runner = assembled()
    original = ingress.verify

    async def mutate(bound: Any, stimulus: Any) -> Any:
        control = await original(bound, stimulus)
        object.__setattr__(bound, "source_transition_contract_ref", "forged")
        return control

    ingress.verify = mutate
    assert await runner.accept_turn(turn_input(b)) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert saver.reads == 0 and driver.calls == []


@pytest.mark.asyncio
async def test_driver_scope_mutation_after_await_blocks_result() -> None:
    b, _, driver, _, runner = assembled()

    async def mutate() -> None:
        driver.binding = b.model_copy(update={"source_transition_contract_ref": "changed"})

    driver.on_call = mutate
    assert await runner.accept_turn(turn_input(b)) == CapabilityRefusalReason.AUTHORITY_UNPROVEN


@pytest.mark.asyncio
async def test_concurrent_fresh_stimuli_are_not_singleton_state() -> None:
    b, _, driver, _, runner = assembled()
    results = await asyncio.gather(
        runner.accept_turn(turn_input(b, message="a")),
        runner.resume(resume_input()),
        runner.accept_turn(turn_input(b, message="b")),
    )
    assert all(isinstance(result, JourneyDispatchOutcome) for result in results)
    assert [mode for mode, _ in driver.calls] == ["turn", "resume", "turn"]
    assert driver.calls[0][1].current_message_ref != driver.calls[2][1].current_message_ref


def test_runtime_records_refuse_coercion_and_unknown_fields() -> None:
    for revision in (True, -1, "1", 1.0):
        with pytest.raises(ValidationError):
            resume_input(revision=cast(Any, revision))


def test_source_graph_version_separates_root_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, _, _, first = assembled()
    monkeypatch.setattr(module, "FULL_RUNTIME_GRAPH_VERSION", "unit-server-version-2")
    _, _, _, _, second = assembled()
    assert first._config()["configurable"]["thread_id"] != second._config()["configurable"]["thread_id"]


def error_count() -> float:
    return sum(
        sample.value
        for metric in get_metrics_collector().registry.collect()
        for sample in metric.samples
        if sample.name == "maezo_agent_errors_total" and sample.labels.get("agent") == "lucas"
    )


@pytest.mark.asyncio
async def test_graph_error_counted_once_without_raw_error_checkpoint_or_result() -> None:
    b, _, driver, saver, runner = assembled()
    marker = "SYNTHETIC_SECRET_ERROR_NOT_METADATA"

    async def fail() -> None:
        raise RuntimeError(marker)

    driver.on_call = fail
    before = error_count()
    result = await runner.accept_turn(turn_input(b))
    assert isinstance(result, CapabilityRefusalReason) and error_count() == before + 1
    assert marker not in repr(result)
    assert marker not in repr(saver.storage) and marker not in repr(saver.writes)


@pytest.mark.asyncio
async def test_drained_cancellation_is_not_counted_as_agent_error() -> None:
    b, _, driver, _, runner = assembled()

    async def drain() -> None:
        raise asyncio.CancelledError()

    driver.on_call = drain
    before = error_count()
    with pytest.raises(asyncio.CancelledError):
        await runner.accept_turn(turn_input(b))
    assert error_count() == before


@pytest.mark.asyncio
async def test_mismatched_qualified_currentness_instance_refuses_before_saver() -> None:
    b, _, driver, saver, runner = assembled(currentness=UnitIngress(binding()))
    assert await runner.accept_turn(turn_input(b)) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert saver.reads == 0 and driver.calls == []
