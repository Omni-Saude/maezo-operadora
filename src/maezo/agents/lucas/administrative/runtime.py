"""Disabled-by-default complete-driver runtime; checkpoints never grant authority.

Technical R2 consumer only. No channel, source, policy, reply sender or operational
binding is installed here. ROOT supplies the same qualified currentness instance
as the driver and the explicitly provisioned saver/keyed pseudonymizer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import Annotated, Any, Final, Literal, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from maezo.agents.lucas.administrative.graph import _in_administrative_context
from maezo.agents.lucas.administrative.handoff import AdministrativeHandoff
from maezo.gateway.capabilities.admission import AdmissionDTO
from maezo.gateway.capabilities.durability.models import LocalRevision
from maezo.gateway.capabilities.journeys.contracts import (
    CurrentJourneyTurn,
    JourneyBinding,
    JourneyContractError,
    JourneyCurrentnessPort,
    JourneyDispatchOutcome,
    JourneyStimulus,
    parse_turn,
)
from maezo.gateway.capabilities.journeys.driver import JourneyDriver
from maezo.gateway.capabilities.models import CapabilityRefusalReason, Ref
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp.security import hash_message_id
from maezo.runtime.checkpoint import checkpoint_thread_config
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.metrics import classify_agent_error_type

FULL_RUNTIME_GRAPH_VERSION: Final[str] = "lucas-administrative-full-driver.proposed.v1"
FULL_RUNTIME_STATE_SCHEMA: Final[str] = "v21-administrative-runtime-state.proposed.v1"
RUNTIME_STIMULUS_SCHEMA: Final[Literal["v21-administrative-runtime-stimulus.proposed.v1"]] = (
    "v21-administrative-runtime-stimulus.proposed.v1"
)


def _original(value: object) -> object:
    """Reparse supported carriers without discarding fields or hidden storage."""
    if isinstance(value, BaseModel):
        if type(value) not in {
            RuntimeTurnStimulus,
            RuntimeResumeStimulus,
            RuntimeCheckpointSnapshot,
            CurrentJourneyTurn,
            JourneyBinding,
            JourneyDispatchOutcome,
        }:
            raise JourneyContractError()
        # Frozen/extra-forbid records can still be forged via model_construct,
        # model_copy or object.__setattr__. Reject actual hidden storage before
        # copying the complete __dict__; model_dump would filter these fields.
        for storage in (value.__pydantic_extra__, value.__pydantic_private__):
            if storage is not None and (type(storage) is not dict or storage):
                raise JourneyContractError()
        # Actual unknown __dict__ keys remain visible to the strict parser.
        # Reject metadata-only unknown field claims instead of dropping them.
        fields_set = value.__pydantic_fields_set__
        if type(fields_set) is not set or any(
            type(key) is not str or (key not in type(value).model_fields and key not in value.__dict__)
            for key in fields_set
        ):
            raise JourneyContractError()
        return {key: _original(item) for key, item in value.__dict__.items()}
    if is_dataclass(value) and not isinstance(value, type):
        if type(value) is not AdministrativeHandoff:
            raise JourneyContractError()
        return _original(asdict(value))
    if isinstance(value, Mapping):
        return {key: _original(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_original(item) for item in value)
    if isinstance(value, list):
        return [_original(item) for item in value]
    return deepcopy(value)


class _RuntimeRecord(AdmissionDTO):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always", hide_input_in_errors=True
    )


class RuntimeTurnStimulus(_RuntimeRecord):
    schema_version: Literal["v21-administrative-runtime-stimulus.proposed.v1"]
    kind: Literal["turn"]
    turn: CurrentJourneyTurn

    @field_validator("turn", mode="before")
    @classmethod
    def exact_turn(cls, value: object) -> CurrentJourneyTurn:
        return parse_turn(_original(value))


class RuntimeResumeStimulus(_RuntimeRecord):
    schema_version: Literal["v21-administrative-runtime-stimulus.proposed.v1"]
    kind: Literal["resume"]
    observation_ref: Ref
    expected_journal_revision: LocalRevision


type RuntimeStimulus = Annotated[RuntimeTurnStimulus | RuntimeResumeStimulus, Field(discriminator="kind")]


def _stimulus(value: object) -> RuntimeTurnStimulus | RuntimeResumeStimulus:
    value = _original(value)
    if not isinstance(value, Mapping):
        raise JourneyContractError()
    if value.get("kind") == "turn":
        return RuntimeTurnStimulus.model_validate(value)
    if value.get("kind") == "resume":
        return RuntimeResumeStimulus.model_validate(value)
    raise JourneyContractError()


def _binding(value: object) -> JourneyBinding:
    return JourneyBinding.model_validate(_original(value))


def _outcome(value: object) -> JourneyDispatchOutcome:
    return JourneyDispatchOutcome.model_validate(_original(value))


class RuntimeCheckpointSnapshot(_RuntimeRecord):
    schema_version: str
    binding: JourneyBinding
    graph_version: str
    stimulus: RuntimeStimulus
    outcome: JourneyDispatchOutcome | None
    refusal: CapabilityRefusalReason | None

    @field_validator("binding", mode="before")
    @classmethod
    def exact_binding(cls, value: object) -> JourneyBinding:
        return _binding(value)

    @field_validator("stimulus", mode="before")
    @classmethod
    def exact_stimulus(cls, value: object) -> RuntimeTurnStimulus | RuntimeResumeStimulus:
        return _stimulus(value)

    @field_validator("outcome", mode="before")
    @classmethod
    def exact_outcome(cls, value: object) -> JourneyDispatchOutcome | None:
        return (
            None
            if value is None
            else JourneyDispatchOutcome.model_validate_json(json.dumps(_original(value)))
        )


class _RuntimeState(TypedDict):
    schema_version: str
    binding: dict[str, Any]
    graph_version: str
    stimulus: dict[str, Any]
    outcome: dict[str, Any] | None
    refusal: str | None


# Exact scheduler channels of this receive/drive/finish topology (LangGraph 1.x
# checkpoint layout). Unknown application channels are never projected away.
_SCHEDULER_CHANNELS: Final[frozenset[str]] = frozenset(
    {"__start__", "branch:to:receive", "branch:to:drive", "branch:to:finish"}
)
_APPLICATION_CHANNELS: Final[frozenset[str]] = frozenset(RuntimeCheckpointSnapshot.model_fields)


class _ScopedSaver(BaseCheckpointSaver[Any]):
    """Validate raw checkpoint channels before LangGraph can hide unknown keys."""

    def __init__(self, inner: BaseCheckpointSaver[Any], runtime: AdministrativeJourneyRuntime) -> None:
        super().__init__(serde=inner.serde)
        self.inner, self.runtime = inner, runtime

    def get_next_version[V: int | float | str](self, current: V | None, channel: None) -> V:
        return cast(V, self.inner.get_next_version(current, channel))

    def _channels(self, values: Mapping[str, Any]) -> None:
        self.runtime._scope()
        if set(values) - (_APPLICATION_CHANNELS | _SCHEDULER_CHANNELS):
            raise JourneyContractError()
        application = {key: item for key, item in values.items() if key in _APPLICATION_CHANNELS}
        if application:
            self.runtime._snapshot(application)
        elif "__start__" not in values:
            raise JourneyContractError()
        if "__start__" in values:
            self.runtime._snapshot(values["__start__"])
        for key in _SCHEDULER_CHANNELS - {"__start__"}:
            if key in values and values[key] is not None:
                raise JourneyContractError()

    def _tuple(self, result: CheckpointTuple | None) -> None:
        if result is None:
            return
        self.runtime._config_scope(result.config)
        if result.parent_config is not None:
            self.runtime._config_scope(result.parent_config)
        if set(result.checkpoint["channel_versions"]) - (_APPLICATION_CHANNELS | _SCHEDULER_CHANNELS):
            raise JourneyContractError()
        self._channels(result.checkpoint["channel_values"])
        for _, key, _ in result.pending_writes or ():
            if key not in _APPLICATION_CHANNELS | _SCHEDULER_CHANNELS:
                raise JourneyContractError()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        self.runtime._config_scope(config)
        result = await self.inner.aget_tuple(config)
        self.runtime._scope()
        self._tuple(result)
        return result

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            raise JourneyContractError()
        self.runtime._config_scope(config)
        async for result in self.inner.alist(config, filter=filter, before=before, limit=limit):
            self.runtime._scope()
            self._tuple(result)
            yield result

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        self.runtime._config_scope(config)
        self._channels(checkpoint["channel_values"])
        result = await self.inner.aput(config, checkpoint, metadata, new_versions)
        self.runtime._scope()
        self.runtime._config_scope(result)
        return result

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.runtime._config_scope(config)
        if any(key not in _APPLICATION_CHANNELS | _SCHEDULER_CHANNELS for key, _ in writes):
            # Never persist raw framework error/interrupt/return contents here.
            raise JourneyContractError()
        await self.inner.aput_writes(config, writes, task_id, task_path)
        self.runtime._scope()


class AdministrativeJourneyRuntime:
    """Server-only complete-driver consumer with fresh authenticated invocations."""

    def __init__(
        self,
        *,
        binding: JourneyBinding,
        driver: JourneyDriver,
        currentness: JourneyCurrentnessPort | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        pseudonymizer: Pseudonymizer | None = None,
        enabled: bool = False,
    ) -> None:
        try:
            if (
                type(binding) is not JourneyBinding
                or not isinstance(driver, JourneyDriver)
                or type(enabled) is not bool
            ):
                raise JourneyContractError()
            self.__binding = _binding(binding)
            if _binding(driver.binding) != self.__binding:
                raise JourneyContractError()
        except ValidationError:
            raise JourneyContractError() from None
        self.__driver = driver
        self.__currentness = currentness
        self.__checkpointer = checkpointer
        self.__pseudonymizer = pseudonymizer
        self.__enabled = enabled
        self.__graph_version, self.__schema = FULL_RUNTIME_GRAPH_VERSION, FULL_RUNTIME_STATE_SCHEMA
        self.__key: str | None = None
        if isinstance(pseudonymizer, Pseudonymizer):
            material = json.dumps(
                {
                    "binding": self.__binding.model_dump(mode="json"),
                    "graph_version": self.__graph_version,
                    "state_schema_version": self.__schema,
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            pseudonym = hash_message_id(material, self.__binding.tenant_ref, pseudonymizer)
            self.__key = f"lucas:administrative:full:{pseudonym}"
        # Only same-object serialization; cross-process conflicts remain journal CAS.
        self.__lock = asyncio.Lock()

    def _scope(self) -> None:
        if (
            _binding(self.__driver.binding) != self.__binding
            or (self.__graph_version, self.__schema)
            != (FULL_RUNTIME_GRAPH_VERSION, FULL_RUNTIME_STATE_SCHEMA)
            or self.__driver.currentness is not self.__currentness
        ):
            raise JourneyContractError(CapabilityRefusalReason.AUTHORITY_UNPROVEN)

    def _config(self) -> RunnableConfig:
        if self.__key is None:
            raise JourneyContractError(CapabilityRefusalReason.SOURCE_UNAVAILABLE)
        return checkpoint_thread_config(self.__key)

    def _config_scope(self, config: RunnableConfig) -> None:
        self._scope()
        configurable = config.get("configurable", {})
        if configurable.get("thread_id") != self.__key or configurable.get("checkpoint_ns", "") != "":
            raise JourneyContractError()

    def _snapshot(self, value: object) -> RuntimeCheckpointSnapshot:
        # Checkpoint channels use canonical JSON values only. JSON arrays are the
        # declared serialization of immutable reference tuples, not a Python-input
        # coercion. Unknown/forged nested keys survive this whole-record reparse.
        state = RuntimeCheckpointSnapshot.model_validate_json(json.dumps(_original(value)))
        if (
            state.binding != self.__binding
            or state.graph_version != self.__graph_version
            or state.schema_version != self.__schema
            or (state.outcome is not None and state.outcome.journey_ref != self.__binding.journey_ref)
        ):
            raise JourneyContractError()
        self._anchors(state.stimulus)
        return state

    def _anchors(self, stimulus: RuntimeTurnStimulus | RuntimeResumeStimulus) -> None:
        if isinstance(stimulus, RuntimeTurnStimulus):
            handoff = stimulus.turn.handoff
            if (handoff.task_type, handoff.tenant_ref, handoff.journey_ref, handoff.message_ref) != (
                self.__binding.task_ref,
                self.__binding.tenant_ref,
                self.__binding.journey_ref,
                stimulus.turn.current_message_ref,
            ):
                raise JourneyContractError()

    async def _authenticate(
        self, stimulus: RuntimeTurnStimulus | RuntimeResumeStimulus
    ) -> CapabilityRefusalReason | None:
        # Expected source refusals are closed values, never exception metadata.
        if self.__currentness is None or not self.is_bound_to(self.__binding):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        bound, copied = _binding(self.__binding), _stimulus(stimulus)
        current: JourneyStimulus = (
            copied.turn if isinstance(copied, RuntimeTurnStimulus) else copied.observation_ref
        )
        result = await self.__currentness.verify(bound, current)
        if not self.is_bound_to(self.__binding):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        try:
            if bound != self.__binding or _stimulus(copied) != stimulus:
                return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        except (ValidationError, JourneyContractError, AttributeError, TypeError):
            # Pure validation of a private copied record after an authority await.
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        if isinstance(result, CapabilityRefusalReason):
            return result
        if type(result) is not str or result != "administrative":
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return None

    async def accept_turn(
        self, value: RuntimeTurnStimulus
    ) -> JourneyDispatchOutcome | CapabilityRefusalReason:
        return await self.__invoke(value, RuntimeTurnStimulus)

    def is_bound_to(self, binding: JourneyBinding) -> bool:
        """Exact ownership check for trusted roots; never an authority or saver operation."""
        try:

            def canonical(value: object) -> JourneyBinding:
                if type(value) is JourneyBinding:
                    raw = object.__getattribute__(value, "__dict__")
                    if type(raw) is not dict or any(type(key) is not str for key in raw):
                        raise JourneyContractError()
                    for name in ("__pydantic_extra__", "__pydantic_private__"):
                        storage = object.__getattribute__(value, name)
                        if storage is not None and (type(storage) is not dict or storage):
                            raise JourneyContractError()
                    fields = object.__getattribute__(value, "__pydantic_fields_set__")
                    if type(fields) is not set or any(
                        type(key) is not str or (key not in JourneyBinding.model_fields and key not in raw)
                        for key in fields
                    ):
                        raise JourneyContractError()
                elif type(value) is dict:
                    raw = value
                else:
                    raise JourneyContractError()
                if type(raw) is not dict or any(type(key) is not str for key in raw):
                    raise JourneyContractError()
                # Exact primitive leaves have no copy/equality/serialization hooks.
                # Keep every key so validation, not projection, rejects unknown fields.
                if any(type(item) is not (bool if key == "enabled" else str) for key, item in raw.items()):
                    raise JourneyContractError()
                return JourneyBinding.model_validate(dict(raw))

            expected = canonical(binding)
            original = canonical(self.__binding)
            driver = canonical(self.__driver.binding)
            return (
                expected.__dict__ == original.__dict__ == driver.__dict__
                and type(self.__graph_version) is str
                and type(self.__schema) is str
                and (self.__graph_version, self.__schema)
                == (FULL_RUNTIME_GRAPH_VERSION, FULL_RUNTIME_STATE_SCHEMA)
                and self.__driver.currentness is self.__currentness
            )
        except (AttributeError, TypeError, ValueError):
            # Only local canonical carrier/metadata parsing is protected here.
            # Exact primitive validation invokes no external dependency or hook.
            return False

    async def resume(self, value: RuntimeResumeStimulus) -> JourneyDispatchOutcome | CapabilityRefusalReason:
        return await self.__invoke(value, RuntimeResumeStimulus)

    async def __invoke(
        self, value: object, expected: type[RuntimeTurnStimulus] | type[RuntimeResumeStimulus]
    ) -> JourneyDispatchOutcome | CapabilityRefusalReason:
        try:
            fresh = _stimulus(value)
            if type(fresh) is not expected:
                raise JourneyContractError()
            self._anchors(fresh)
        except (ValidationError, JourneyContractError, AttributeError, TypeError):
            # This try contains only closed input parsing, never an injected port.
            return CapabilityRefusalReason.CONTRACT_MISMATCH
        if not self.is_bound_to(self.__binding):
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        if not self.__enabled or not self.__binding.enabled or self.__currentness is None:
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        if not isinstance(self.__checkpointer, BaseCheckpointSaver) or not isinstance(
            self.__pseudonymizer, Pseudonymizer
        ):
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE
        try:
            async with self.__lock:
                # Authentication precedes every saver access, including framework reads.
                refusal = await self._authenticate(fresh)
                if refusal is not None:
                    return refusal
                return await _in_administrative_context(lambda: self.__run(fresh))
        except PROGRAMMING_ERRORS:
            raise
        except (ValidationError, JourneyContractError):
            # A forged error object cannot provide a value for outward disclosure.
            return (
                CapabilityRefusalReason.CONTRACT_MISMATCH
                if self.is_bound_to(self.__binding)
                else CapabilityRefusalReason.AUTHORITY_UNPROVEN
            )
        except EXTERNAL_DEPENDENCY_FAILURES:
            return CapabilityRefusalReason.SOURCE_UNAVAILABLE

    async def __run(
        self, fresh: RuntimeTurnStimulus | RuntimeResumeStimulus
    ) -> JourneyDispatchOutcome | CapabilityRefusalReason:
        assert self.__checkpointer is not None
        saver = _ScopedSaver(self.__checkpointer, self)
        config = self._config()
        await saver.aget_tuple(config)
        self._scope()
        produced: JourneyDispatchOutcome | None = None
        cancelled = False
        refusal: CapabilityRefusalReason | None = None

        class _AuthenticationRefusedError(ValueError):
            """Invocation-local graph stop; carries no source reason or payload."""

        def validate(value: object, *, finished: bool = False) -> RuntimeCheckpointSnapshot:
            self._scope()
            state = self._snapshot(value)
            if state.stimulus != fresh or state.refusal is not None:
                raise JourneyContractError()
            if (finished and (produced is None or state.outcome != produced)) or (
                not finished and state.outcome is not None
            ):
                raise JourneyContractError()
            return state

        async def receive(state: _RuntimeState) -> _RuntimeState:
            validate(state)
            return state

        async def drive(state: _RuntimeState) -> _RuntimeState:
            nonlocal produced, cancelled, refusal
            validate(state)
            try:
                refusal = await self._authenticate(fresh)
                if refusal is not None:
                    # The graph stops before driver/effect, preserving the exact
                    # qualified enum privately instead of storing it on an error.
                    raise _AuthenticationRefusedError()
                if isinstance(fresh, RuntimeTurnStimulus):
                    result = await self.__driver.accept_turn(deepcopy(fresh.turn))
                else:
                    result = await self.__driver.resume(
                        fresh.observation_ref, fresh.expected_journal_revision
                    )
            except asyncio.CancelledError:
                cancelled = True
                raise
            self._scope()
            produced = _outcome(result)
            if produced.journey_ref != self.__binding.journey_ref:
                raise JourneyContractError()
            return {**state, "outcome": produced.model_dump(mode="json")}

        async def finish(state: _RuntimeState) -> _RuntimeState:
            validate(state, finished=True)
            return state

        graph = StateGraph(_RuntimeState)
        graph.add_node("receive", receive)
        graph.add_node("drive", drive)
        graph.add_node("finish", finish)
        graph.add_edge(START, "receive")
        graph.add_edge("receive", "drive")
        graph.add_edge("drive", "finish")
        graph.add_edge("finish", END)
        compiled = graph.compile(checkpointer=saver)
        initial: _RuntimeState = {
            "schema_version": self.__schema,
            "binding": self.__binding.model_dump(mode="json"),
            "graph_version": self.__graph_version,
            "stimulus": fresh.model_dump(mode="json"),
            "outcome": None,
            "refusal": None,
        }
        try:
            try:
                actual = await compiled.ainvoke(initial, config)
            except Exception as exc:
                if cancelled:
                    raise asyncio.CancelledError() from None
                from maezo.platform.observability import record_agent_error

                # Existing category(a): all graph failures get one safe bounded
                # class label, then bare rethrow. Unknown/programming failures
                # cannot become a source-availability result here.
                record_agent_error(agent="lucas", error_type=classify_agent_error_type(exc))
                raise
        except _AuthenticationRefusedError:
            if refusal is None:
                raise
            return refusal
        if cancelled:
            raise asyncio.CancelledError()
        self._scope()
        validate(actual, finished=True)
        await saver.aget_tuple(config)
        self._scope()
        final_refusal = await self._authenticate(fresh)
        if final_refusal is not None:
            return final_refusal
        if produced is None:
            raise JourneyContractError()
        return deepcopy(produced)
