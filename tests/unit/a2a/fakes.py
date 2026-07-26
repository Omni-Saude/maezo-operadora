"""Injectable fakes for the A2A W2 (delegation runtime) unit tests — no Kafka/DB/engine.

Adapted from the donor `Maezo-Healthcare-Plan` reference implementation's
`tests/unit/a2a/fakes.py` for the T2.4 A2A W2 build wave. Deviations from the donor:
`A2ARegistry` (not `AgentCardRegistry`); the audit fake is v2's own `FakeAuditSink`
(`maezo.tools.workers.harness`, already public and reused across worker test modules) rather
than a donor-shaped `RecordingSink`/`AuditLog([sink])`, since v2's audit seam is `emit_once`
directly (no `AuditLog` wrapper to construct).
"""

from __future__ import annotations

from collections.abc import Mapping

from maezo.a2a import (
    A2ARegistry,
    AgentCard,
    DelegationDispatcher,
    DelegationEnvelope,
    FactProducer,
    HandlerOutput,
)
from maezo.a2a.dispatcher import AgentHandler
from maezo.tools.workers.harness import FakeAuditSink


class RecordingProducer:
    """Fake Kafka producer: records the `(topic, value, key)` of every `send`."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes | None]] = []

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        self.sent.append((topic, value, key))

    def topics(self) -> list[str]:
        return [t for (t, _, _) in self.sent]


class FakeAgentHandler:
    """Fake agent handler: counts calls, returns a fixed `output_ref`.

    If `subdelegate_to` is set, extends the chain (a REAL sub-delegation) — used to prove the
    anti-loop guards also fire on the handler's own path.
    """

    def __init__(
        self,
        *,
        output_ref: str = "fhir://Task/out",
        subdelegate_to: str | None = None,
        subtask_id: str = "subtask-1",
    ) -> None:
        self.calls: list[DelegationEnvelope] = []
        self._output_ref = output_ref
        self._subdelegate_to = subdelegate_to
        self._subtask_id = subtask_id

    async def __call__(self, envelope: DelegationEnvelope) -> HandlerOutput:
        self.calls.append(envelope)
        if self._subdelegate_to is not None:
            # Raises DelegationError if the sub-delegation violates a guard (chain/hops/budget).
            envelope.extend(target=self._subdelegate_to, task_id=self._subtask_id)
        return HandlerOutput(output_ref=self._output_ref)

    @property
    def call_count(self) -> int:
        return len(self.calls)


def make_card(
    agent_id: str,
    *,
    tenant: str = "amh",
    accepted: frozenset[str] | None = None,
    zone: str = "phi",
) -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        version="v0-test",
        tenant=tenant,
        security_zone=zone,
        accepted_task_types=(accepted if accepted is not None else frozenset({"authorization.analyze"})),
        queue_ref=f"agents.tasks.{agent_id}",
    )


def build_test_dispatcher(
    *,
    cards: list[AgentCard],
    handlers: Mapping[str, AgentHandler],
    audit: FakeAuditSink | None = None,
    producer: RecordingProducer | None = None,
) -> tuple[DelegationDispatcher, FakeAuditSink, RecordingProducer]:
    """Assemble a `DelegationDispatcher` with a registry + fake audit + fake facts."""
    registry = A2ARegistry()
    for card in cards:
        registry.register(card)
    audit_sink = audit if audit is not None else FakeAuditSink()
    kafka = producer if producer is not None else RecordingProducer()
    dispatcher = DelegationDispatcher(
        registry=registry,
        handlers=handlers,
        audit=audit_sink,
        facts=FactProducer(kafka),
    )
    return dispatcher, audit_sink, kafka
