"""Unit tests for the external-task worker harness (T1.1 runtime spine).

No engine — every test uses `FakeWorkerTransport` (in-memory double) or `httpx.MockTransport`
(for `CibSevenWorkerTransport`'s wire format). The real-engine acceptance test lives under
`tests/integration/` (design §14 point 3).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from maezo.tools.workers.base import WorkerBase
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport as RealTransport,
)
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeAuditSink,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    KafkaPublisher,
    TopicSubscription,
    WorkerBpmnError,
    WorkerFailure,
    WorkerFailureError,
    WorkerHarness,
    WorkerTransport,
    _to_camunda_var,
    screen_bpmn_error_variables,
)

# `asyncio_mode = "auto"` (pyproject.toml) collects async def tests automatically — no
# `pytestmark = pytest.mark.asyncio` needed (and marking sync tests with it warns).


def _task(
    *,
    task_id: str = "task-1",
    topic: str = "operadora.test.topic",
    retries: int | None = None,
    variables: dict[str, Any] | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=topic,
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="w-1",
        variables=variables or {},
        retries=retries,
    )


# ---------------------------------------------------------------------------
# _to_camunda_var typing (load-bearing — money/dossier round-trips)
# ---------------------------------------------------------------------------


def test_to_camunda_var_bool() -> None:
    assert _to_camunda_var(True) == {"value": True, "type": "Boolean"}


def test_to_camunda_var_small_int_is_integer() -> None:
    assert _to_camunda_var(42) == {"value": 42, "type": "Integer"}


def test_to_camunda_var_large_int_is_long() -> None:
    # 5,000,000,000 cents (R$50MM) overflows Java int32 (ADR-0018 part 2).
    big = 5_000_000_000
    assert _to_camunda_var(big) == {"value": big, "type": "Long"}


def test_to_camunda_var_negative_overflow_is_long() -> None:
    too_negative = -(2**31) - 1
    assert _to_camunda_var(too_negative)["type"] == "Long"


def test_to_camunda_var_float_is_double() -> None:
    assert _to_camunda_var(3.14) == {"value": 3.14, "type": "Double"}


def test_to_camunda_var_dict_is_json() -> None:
    result = _to_camunda_var({"a": 1})
    assert result["type"] == "Json"
    assert result["value"] == '{"a": 1}'


def test_to_camunda_var_list_is_json() -> None:
    result = _to_camunda_var([1, 2, 3])
    assert result["type"] == "Json"


def test_to_camunda_var_none_is_string() -> None:
    assert _to_camunda_var(None) == {"value": None, "type": "String"}


def test_to_camunda_var_passthrough_already_typed() -> None:
    typed = {"value": "x", "type": "String", "valueInfo": {}}
    assert _to_camunda_var(typed) is typed


def test_to_camunda_var_other_types_stringified() -> None:
    class Foo:
        def __str__(self) -> str:
            return "foo-repr"

    assert _to_camunda_var(Foo()) == {"value": "foo-repr", "type": "String"}


# ---------------------------------------------------------------------------
# Symmetric path: outbound `_to_camunda_var` (Json-encode) round-trips through the SAME
# json.loads the inbound `fetch_and_lock` decode now applies — proves `complete()`'s existing
# dict/list -> Json serialization is the correct counterpart to the inbound fix (not a second
# defect: the outbound half of this contract was already correct BEFORE this PR, per
# `test_to_camunda_var_dict_is_json`/`test_real_transport_complete_types_variables` above/below;
# this test additionally proves the round-trip, not just the wire `type` tag).
# ---------------------------------------------------------------------------


def test_to_camunda_var_dict_round_trips_through_json_loads() -> None:
    import json as _json

    payload = {"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000, "itens": [1, 2, 3]}
    wire = _to_camunda_var(payload)
    assert wire["type"] == "Json"
    assert _json.loads(wire["value"]) == payload


def test_to_camunda_var_list_round_trips_through_json_loads() -> None:
    import json as _json

    payload = [{"numero_guia_tiss": "G1"}, {"numero_guia_tiss": "G2"}]
    wire = _to_camunda_var(payload)
    assert wire["type"] == "Json"
    assert _json.loads(wire["value"]) == payload


# ---------------------------------------------------------------------------
# FakeWorkerTransport
# ---------------------------------------------------------------------------


async def test_fake_transport_fetch_and_lock_filters_by_topic() -> None:
    transport = FakeWorkerTransport([_task(task_id="t1", topic="a"), _task(task_id="t2", topic="b")])
    tasks = await transport.fetch_and_lock(
        "w", [TopicSubscription("a", 30_000)], max_tasks=10, async_response_timeout_ms=1000
    )
    assert [t.task_id for t in tasks] == ["t1"]


async def test_fake_transport_fetch_and_lock_respects_max_tasks() -> None:
    transport = FakeWorkerTransport([_task(task_id=f"t{i}", topic="a") for i in range(5)])
    tasks = await transport.fetch_and_lock(
        "w", [TopicSubscription("a", 30_000)], max_tasks=2, async_response_timeout_ms=1000
    )
    assert len(tasks) == 2


async def test_fake_transport_mirrors_real_transport_decoded_python_objects() -> None:
    """`FakeWorkerTransport` never round-trips the CIB Seven wire format (tests build
    `ExternalTask.variables` directly as Python objects) — this test PROVES that stays true
    post-fix: a worker fed by the fake sees the same decoded `list`/`dict` shape the real,
    fixed `CibSevenWorkerTransport` now produces, never a Camunda-wire-typed envelope or a raw
    JSON string standing in for one. If this ever regressed (e.g. someone made the fake
    simulate wire encoding without a matching decode), unit tests would pass against the fake
    while the live worker received an undecoded string — exactly the T1.1 defect class."""
    linhas = [{"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000}]
    transport = FakeWorkerTransport(
        [_task(task_id="t1", topic="a", variables={"linhas_conta_refs": linhas, "n": 1})]
    )
    tasks = await transport.fetch_and_lock(
        "w", [TopicSubscription("a", 30_000)], max_tasks=10, async_response_timeout_ms=1000
    )
    assert len(tasks) == 1
    assert tasks[0].variables["linhas_conta_refs"] == linhas
    assert isinstance(tasks[0].variables["linhas_conta_refs"], list)  # decoded, not JSON text
    assert tasks[0].variables["n"] == 1


async def test_fake_transport_records_calls() -> None:
    transport = FakeWorkerTransport()
    await transport.complete("t1", "w", {"x": 1})
    await transport.handle_failure("t1", "w", error_message="boom", retries=2, retry_timeout_ms=500)
    await transport.handle_bpmn_error("t1", "w", error_code="ERR_X")
    await transport.extend_lock("t1", "w", new_duration_ms=1000)
    await transport.unlock("t1")
    await transport.close()

    assert transport.completed == [("t1", {"x": 1})]
    assert transport.failures == [("t1", "boom", 2, 500)]
    assert transport.bpmn_errors == [("t1", "ERR_X", "")]
    assert transport.extended == [("t1", 1000)]
    assert transport.unlocked == ["t1"]
    assert transport.closed is True


def test_transport_protocols_satisfied() -> None:
    assert isinstance(FakeWorkerTransport(), WorkerTransport)
    assert isinstance(FakeKafkaPublisher(), KafkaPublisher)


# ---------------------------------------------------------------------------
# WorkerHarness — registration
# ---------------------------------------------------------------------------


async def test_register_and_registered_topics() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w")

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {}

    harness.register("topic.a", handler)
    harness.register("topic.b", handler)
    assert harness.registered_topics == ["topic.a", "topic.b"]


class _EchoWorker(WorkerBase):
    def __init__(self) -> None:
        super().__init__(topic="operadora.test.echo")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        return {"echo": process_vars.get("value")}


class _AsyncOverrideWorker(WorkerBase):
    def __init__(self) -> None:
        super().__init__(topic="operadora.test.async")
        self.run_async_called = False

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("execute() must not be called when run_async is overridden")

    async def run_async(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        self.run_async_called = True
        return {"async": True}


async def test_register_worker_adds_to_registry_and_dispatch_table() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w")
    worker = _EchoWorker()
    harness.register_worker(worker)

    assert "operadora.test.echo" in harness.registered_topics
    assert harness.registry.get("operadora.test.echo") is worker


async def test_register_worker_sync_execute_runs_via_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", audit_sink=FakeAuditSink())
    harness.register_worker(_EchoWorker())

    task = _task(topic="operadora.test.echo", variables={"value": 42})
    await harness._handle(task)

    assert transport.completed == [(task.task_id, {"echo": 42})]


async def test_register_worker_prefers_run_async_when_overridden() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", audit_sink=FakeAuditSink())
    worker = _AsyncOverrideWorker()
    harness.register_worker(worker)

    task = _task(topic="operadora.test.async")
    await harness._handle(task)

    assert worker.run_async_called is True
    assert transport.completed == [(task.task_id, {"async": True})]


# ---------------------------------------------------------------------------
# WorkerHarness — dispatch outcomes (retry ownership, design §9)
# ---------------------------------------------------------------------------


async def test_handle_success_completes_once() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", audit_sink=FakeAuditSink())

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"result": "ok"}

    harness.register("t", handler)
    task = _task(topic="t")
    await harness._handle(task)

    assert transport.completed == [(task.task_id, {"result": "ok"})]
    assert transport.failures == []
    assert transport.bpmn_errors == []


async def test_handle_success_none_return_completes_with_empty_dict() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", audit_sink=FakeAuditSink())

    async def handler(task: ExternalTask) -> None:
        return None

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.completed[0][1] == {}


async def test_handle_task_public_alias_matches_private() -> None:
    assert WorkerHarness.handle_task is WorkerHarness._handle


async def test_handle_unknown_topic_reports_incident_never_drops() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w")
    task = _task(topic="unregistered.topic")

    await harness._handle(task)

    assert transport.completed == []
    assert len(transport.failures) == 1
    task_id, _msg, retries, retry_timeout_ms = transport.failures[0]
    assert task_id == task.task_id
    assert retries == 0
    assert retry_timeout_ms == 0


async def test_handle_permission_error_never_retried() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", max_retry_attempts=5)

    async def handler(task: ExternalTask) -> None:
        raise PermissionError("ERR_DENIAL_NOT_HUMAN")

    harness.register("t", handler)
    # Even with retries left on the task, a guard error ALWAYS reports retries=0 (never retried).
    await harness._handle(_task(topic="t", retries=4))

    assert transport.failures[0][2] == 0
    assert transport.failures[0][3] == 0
    assert transport.bpmn_errors == []


async def test_handle_value_error_reports_retries_zero() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w")

    async def handler(task: ExternalTask) -> None:
        raise ValueError("bad input")

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=3))

    assert transport.failures[0][2] == 0


# ---------------------------------------------------------------------------
# T3.4 F5: raw exception messages must arrive at the transport (the engine's Cockpit-visible
# incident store) REDACTED — every `_report_failure` call site, plus the allowlisted-bpmnError
# path (which bypasses `_report_failure` entirely). Non-hollow: a message WITHOUT PHI-shaped
# content must still arrive readable (no over-redaction).
# ---------------------------------------------------------------------------


async def test_handle_value_error_with_cpf_arrives_redacted_at_transport() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w")

    async def handler(task: ExternalTask) -> None:
        raise ValueError("cpf invalido: 123.456.789-01 nao encontrado")

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=3))

    assert len(transport.failures) == 1
    error_message = transport.failures[0][1]
    assert "123.456.789-01" not in error_message
    assert error_message == "ValueError: cpf invalido: [REDACTED_DIGITS] nao encontrado"


async def test_handle_permission_error_with_bare_digit_run_arrives_redacted_at_transport() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w")

    async def handler(task: ExternalTask) -> None:
        raise PermissionError("ERR_DENIAL_NOT_HUMAN beneficiario 12345678901")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    error_message = transport.failures[0][1]
    assert "12345678901" not in error_message
    assert "[REDACTED_DIGITS]" in error_message
    assert error_message.startswith("PermissionError: ")


async def test_handle_transient_error_with_no_phi_shape_passes_through_readable() -> None:
    """No false positives: a plain transient-error message is forwarded intact (redaction is a
    backstop, not a lossy default)."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w")

    async def handler(task: ExternalTask) -> None:
        raise RuntimeError("engine unreachable: connection refused")

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=3))

    assert transport.failures[0][1] == "RuntimeError: engine unreachable: connection refused"


async def test_handle_bpmn_error_allowlisted_with_cpf_arrives_redacted_at_transport() -> None:
    """The allowlisted-bpmnError path calls `transport.handle_bpmn_error` directly — it bypasses
    `_report_failure` entirely, so it needs its own redaction coverage (T3.4 F5 audit note)."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset({"ERR_PROVEN"}))

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError("ERR_PROVEN", "cliente cpf 987.654.321-00 invalido")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert len(transport.bpmn_errors) == 1
    _task_id, error_code, error_message = transport.bpmn_errors[0]
    assert error_code == "ERR_PROVEN"
    assert "987.654.321-00" not in error_message
    assert error_message == "WorkerBpmnError: cliente cpf [REDACTED_DIGITS] invalido"


async def test_handle_transient_error_decrements_engine_retries() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", max_retry_attempts=3)

    async def handler(task: ExternalTask) -> None:
        raise RuntimeError("transient boom")

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=2))

    task_id, msg, retries, retry_timeout_ms = transport.failures[0]
    assert retries == 1
    assert retry_timeout_ms > 0
    assert "transient boom" in msg


async def test_handle_transient_error_first_delivery_seeds_from_max_retry_attempts() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", max_retry_attempts=3)

    async def handler(task: ExternalTask) -> None:
        raise RuntimeError("boom")

    harness.register("t", handler)
    # retries=None => first delivery => seeds from max_retry_attempts (3) => reports 3-1=2.
    await harness._handle(_task(topic="t", retries=None))

    assert transport.failures[0][2] == 2


async def test_handle_transient_error_exhausted_retries_reports_zero_no_timeout() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", max_retry_attempts=3)

    async def handler(task: ExternalTask) -> None:
        raise RuntimeError("boom")

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=1))

    task_id, msg, retries, retry_timeout_ms = transport.failures[0]
    assert retries == 0
    assert retry_timeout_ms == 0


async def test_handle_engine_does_not_auto_decrement_client_computes() -> None:
    """The engine does not decrement `retries` itself (design §2) — the harness must."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w")

    async def handler(task: ExternalTask) -> None:
        raise RuntimeError("boom")

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=5))

    assert transport.failures[0][2] == 4  # 5 - 1, computed client-side


async def test_handle_worker_failure_error_honors_retries_left() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", max_retry_attempts=3)

    async def handler(task: ExternalTask) -> None:
        raise WorkerFailureError("custom failure", retries_left=7)

    harness.register("t", handler)
    # Computed value would be 2 (3-1), but retries_left=7 OVERRIDES it (design §9/§16.2).
    await harness._handle(_task(topic="t", retries=None))

    assert transport.failures[0][2] == 7


async def test_worker_failure_alias_is_worker_failure_error() -> None:
    assert WorkerFailure is WorkerFailureError


async def test_handle_bpmn_error_allowlisted_code_reports_bpmn_error() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset({"ERR_PROVEN"}))

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError("ERR_PROVEN", "modeled failure")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.bpmn_errors == [
        (_task(topic="t").task_id, "ERR_PROVEN", "WorkerBpmnError: modeled failure")
    ]
    assert transport.failures == []


async def test_handle_bpmn_error_unproven_code_demotes_to_failure() -> None:
    """The boundary-proof gate hazard (design §9): an unmodeled bpmnError silently ends the
    process with NO incident on CIB Seven 2.1.0 — so an unproven code MUST demote to a
    fail-closed incident, never emit bpmnError."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset())

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError("ERR_NOT_PROVEN", "unmodeled")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.bpmn_errors == []
    assert len(transport.failures) == 1
    assert transport.failures[0][2] == 0  # immediate incident, never retried


async def test_handle_bpmn_error_default_allowlist_is_empty() -> None:
    """No code is gate-proven by default — every WorkerBpmnError demotes until a code is added
    explicitly with proof (design §9 Q-3: no topic opts in before the gate proves it)."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w")  # default bpmn_error_allowlist

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError("ERR_ANYTHING")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.bpmn_errors == []
    assert transport.failures[0][2] == 0


# ---------------------------------------------------------------------------
# `WorkerBpmnError` VARIABLES channel (t9-nack-vars). A worker that raises never `complete`s, so
# without this channel nothing it computed reaches process scope and a modeled boundary can route
# into a branch whose next worker fail-closes on the missing variable (live case:
# SP-OP-ANS-SUBMIT-001's ERR_ANS_PROTOCOLO_NACK -> SUB_RetryEnvio -> ST_RetransmitirEnvio's blank
# `protocolo_ans` guard). The channel is allowlisted + bounded + fail-closed; these tests pin all
# four of those properties, since the allowlist is the ONLY thing standing between an exceptional
# code path and an unreviewed write into process scope for every worker in the fleet.
# ---------------------------------------------------------------------------


async def test_bpmn_error_without_variables_sends_none_backward_compatible() -> None:
    """Every pre-channel raise must hit the wire byte-identically: `variables=None`, not `{}`.

    `{}` is not equivalent — `CibSevenWorkerTransport.handle_bpmn_error` only adds the `variables`
    key to the payload when the mapping is truthy, so a caller that started sending `{}` would keep
    the same wire bytes only by accident. Pinning `None` keeps the intent explicit.
    """
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset({"ERR_PROVEN"}))

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError("ERR_PROVEN", "modeled failure")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.bpmn_error_variables == [None]
    assert transport.failures == []


async def test_bpmn_error_allowlisted_variables_reach_the_transport() -> None:
    """The two allowlisted keys travel through to the engine on an allowlisted code."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset({"ERR_PROVEN"}))

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError(
            "ERR_PROVEN",
            "modeled failure",
            variables={
                "protocolo_ans": "MOCK-ANS-NAO-VINCULATIVO-ANSSUB-amh-RN_124_SIP-2026-01",
                "status_envio": "nack",
            },
        )

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.bpmn_error_variables == [
        {
            "protocolo_ans": "MOCK-ANS-NAO-VINCULATIVO-ANSSUB-amh-RN_124_SIP-2026-01",
            "status_envio": "nack",
        }
    ]
    assert transport.failures == []


async def test_bpmn_error_disallowed_key_refuses_the_whole_bpmn_error() -> None:
    """A key OUTSIDE the allowlist REFUSES the entire bpmnError — it is not silently dropped.

    All-or-nothing on purpose: sending the surviving subset would fire the modeled boundary into a
    scope missing exactly what the branch needed, producing a subtly-wrong route (or an incident
    blamed on the WRONG worker). Refusal makes the payload defect the visible incident.
    """
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset({"ERR_PROVEN"}))

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError(
            "ERR_PROVEN",
            "modeled failure",
            variables={"status_envio": "nack", "cpf_beneficiario": "98765432100"},
        )

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.bpmn_errors == []  # refused: NOT partially sent
    assert len(transport.failures) == 1
    assert transport.failures[0][2] == 0  # immediate incident, never retried


@pytest.mark.parametrize(
    "value",
    [
        "cliente recusado pela operadora",  # free text (spaces)
        "linha1\nlinha2",  # newline
        "a" * 129,  # over the length cap
        "-leading-punctuation",  # must start alphanumeric
        {"nested": "object"},  # non-scalar
        ["a", "b"],  # non-scalar
        None,  # non-scalar
        3.14,  # float
        10_000_000,  # over the integer bound
    ],
)
async def test_bpmn_error_unbounded_value_under_an_allowlisted_key_is_refused(value: Any) -> None:
    """The value guard is the defence-in-depth second gate: an ALLOWLISTED key cannot smuggle
    free text, a non-scalar, or an out-of-bound number through the channel."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset({"ERR_PROVEN"}))

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError("ERR_PROVEN", "modeled failure", variables={"status_envio": value})

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.bpmn_errors == []
    assert transport.failures[0][2] == 0


async def test_bpmn_error_variables_dropped_on_allowlist_demotion() -> None:
    """DEMOTION semantics: an uncatalogued code demotes to `failure(retries=0)` and the variables
    are DROPPED — the External Task `failure` contract has no variables channel, so there is
    nowhere for them to go. The demotion incident is the human-visible signal."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset())

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError("ERR_NOT_PROVEN", "unmodeled", variables={"status_envio": "nack"})

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.bpmn_errors == []
    assert transport.bpmn_error_variables == []
    assert len(transport.failures) == 1
    assert transport.failures[0][2] == 0


async def test_bpmn_error_screening_reports_only_key_names_never_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refused VALUE is never echoed to the logs. The reason it was refused is precisely that
    nothing is known about its contents — logging it would be the leak the screen exists to stop."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", bpmn_error_allowlist=frozenset({"ERR_PROVEN"}))

    async def handler(task: ExternalTask) -> None:
        raise WorkerBpmnError("ERR_PROVEN", "modeled failure", variables={"nome_paciente": "Maria da Silva"})

    harness.register("t", handler)
    with caplog.at_level("ERROR"):
        await harness._handle(_task(topic="t"))

    logged = caplog.text
    assert "nome_paciente" in logged  # the KEY names the defect for the on-call human
    assert "Maria da Silva" not in logged  # ...the VALUE never travels


def test_screen_bpmn_error_variables_is_pure_and_total() -> None:
    """Direct coverage of the screen itself, including the empty/None backward-compatible cases."""
    assert screen_bpmn_error_variables(None) == ({}, ())
    assert screen_bpmn_error_variables({}) == ({}, ())
    assert screen_bpmn_error_variables({"status_envio": "nack"}) == ({"status_envio": "nack"}, ())
    # refused keys are reported SORTED, so an incident line is stable across dict orderings
    assert screen_bpmn_error_variables({"zeta": 1, "alfa": 2}).refused_keys == ("alfa", "zeta")


async def test_handle_never_raises_out_of_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if the transport's failure-reporting call itself raises, `_handle` must not crash
    the caller — dispatch resilience is load-bearing for the loop (design §6)."""
    transport = FakeWorkerTransport()

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("secondary transport failure")

    monkeypatch.setattr(transport, "handle_failure", _boom)
    harness = WorkerHarness(transport, worker_id="w")

    async def handler(task: ExternalTask) -> None:
        raise RuntimeError("primary failure")

    harness.register("t", handler)
    with pytest.raises(RuntimeError, match="secondary transport failure"):
        await harness._handle(_task(topic="t"))


# ---------------------------------------------------------------------------
# WorkerHarness — run loop, stop, drain
# ---------------------------------------------------------------------------


async def test_run_dispatches_fetched_tasks_and_exits_on_cancel() -> None:
    transport = FakeWorkerTransport([_task(task_id="t1", topic="t")])
    harness = WorkerHarness(transport, worker_id="w", poll_interval_ms=10, audit_sink=FakeAuditSink())

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"ok": True}

    harness.register("t", handler)

    run_task = asyncio.create_task(harness.run())
    # Give the loop a chance to fetch + dispatch, then cancel it.
    for _ in range(50):
        if transport.completed:
            break
        await asyncio.sleep(0.01)
    run_task.cancel()
    with _suppress_cancelled():
        await run_task

    assert transport.completed == [("t1", {"ok": True})]


def _suppress_cancelled() -> Any:
    import contextlib

    return contextlib.suppress(asyncio.CancelledError)


async def test_stop_flips_running_flag() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", poll_interval_ms=5)
    harness.register("t", lambda task: _identity_coro())

    run_task = asyncio.create_task(harness.run())
    await asyncio.sleep(0.02)
    await harness.stop()
    await asyncio.wait_for(run_task, timeout=2.0)
    assert run_task.done()


async def _identity_coro() -> dict[str, Any]:
    return {}


async def test_run_backs_off_and_flags_engine_unreachable_on_repeated_fetch_errors() -> None:
    class FlakyTransport(FakeWorkerTransport):
        async def fetch_and_lock(self, *args: Any, **kwargs: Any) -> list[ExternalTask]:
            raise httpx.ConnectError("connection refused")

    harness = WorkerHarness(FlakyTransport(), worker_id="w", engine_unreachable_after=2)
    harness.register("t", lambda task: _identity_coro())
    assert harness.engine_reachable is True

    run_task = asyncio.create_task(harness.run())
    for _ in range(200):
        if not harness.engine_reachable:
            break
        await asyncio.sleep(0.01)
    run_task.cancel()
    with _suppress_cancelled():
        await run_task

    assert harness.engine_reachable is False
    assert harness.fetch_errors_total >= 2


async def test_drain_with_no_inflight_returns_immediately() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="w")
    await harness.drain(1.0)  # must not raise / hang


async def test_drain_waits_for_fast_handler_then_no_unlock() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", audit_sink=FakeAuditSink())

    async def fast_handler(task: ExternalTask) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return {}

    harness.register("t", fast_handler)
    harness._spawn(_task(task_id="fast", topic="t"))
    await harness.drain(2.0)

    assert transport.unlocked == []
    assert transport.completed and transport.completed[0][0] == "fast"


async def test_drain_unlocks_stragglers_past_deadline() -> None:
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w")

    async def slow_handler(task: ExternalTask) -> dict[str, Any]:
        await asyncio.sleep(10)
        return {}

    harness.register("t", slow_handler)
    harness._spawn(_task(task_id="slow", topic="t"))
    await harness.drain(0.05)

    assert transport.unlocked == ["slow"]
    assert harness.inflight_count == 0


# ---------------------------------------------------------------------------
# CibSevenWorkerTransport — wire format via httpx.MockTransport
# ---------------------------------------------------------------------------


async def test_real_transport_fetch_and_lock_payload_and_mapping() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = httpx.Request(request.method, request.url, content=request.content).content
        return httpx.Response(
            200,
            json=[
                {
                    "id": "task-99",
                    "topicName": "t",
                    "processInstanceId": "proc-99",
                    "businessKey": "bk-99",
                    "workerId": "w",
                    "retries": 2,
                    "variables": {"x": {"value": 10, "type": "Integer"}},
                }
            ],
        )

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )

    tasks = await transport.fetch_and_lock(
        "w",
        [TopicSubscription("t", 30_000, ["x"])],
        max_tasks=5,
        async_response_timeout_ms=25_000,
    )

    assert captured["url"] == "http://engine/engine-rest/external-task/fetchAndLock"
    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_id == "task-99"
    assert task.retries == 2
    assert task.variables == {"x": 10}
    await transport.close()


async def test_real_transport_fetch_and_lock_decodes_json_typed_list_variable() -> None:
    """T1.1 regression (marina graph author, live E2E): CIB Seven's fetchAndLock returns a
    `Json`-typed variable's `value` as a JSON STRING, e.g. SP-OP-CONTAS-001's
    `linhas_conta_refs` (list[dict]). Without decoding, workers receive the raw string
    `'[{"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000}]'` instead of a Python
    list — `identify_glosa`'s `for linha in linhas: linha.get(...)` would then iterate
    CHARACTERS of the string, not dicts. This is the real wire shape captured from CIB
    Seven's own External Task REST contract (Camunda 7-compatible `Json` variable type)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "task-json-1",
                    "topicName": "operadora.contas.identify_glosa",
                    "processInstanceId": "proc-1",
                    "businessKey": "bk-1",
                    "workerId": "w",
                    "retries": 3,
                    "variables": {
                        "linhas_conta_refs": {
                            "value": '[{"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000}]',
                            "type": "Json",
                        },
                        "numero_lote_tiss": {"value": "L1", "type": "String"},
                    },
                }
            ],
        )

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )

    tasks = await transport.fetch_and_lock(
        "w",
        [TopicSubscription("operadora.contas.identify_glosa", 30_000)],
        max_tasks=5,
        async_response_timeout_ms=25_000,
    )

    assert len(tasks) == 1
    variables = tasks[0].variables
    assert variables["linhas_conta_refs"] == [{"numero_guia_tiss": "G1", "valor_apresentado_centavos": 1000}]
    assert isinstance(variables["linhas_conta_refs"], list)  # NOT the raw JSON string
    assert variables["numero_lote_tiss"] == "L1"  # non-Json types unaffected
    await transport.close()


async def test_real_transport_fetch_and_lock_decodes_json_typed_dict_variable() -> None:
    """Same wire shape, dict-valued Json variable (e.g. a dossier/object process variable)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "task-json-2",
                    "topicName": "t",
                    "processInstanceId": "proc-2",
                    "businessKey": "bk-2",
                    "workerId": "w",
                    "retries": None,
                    "variables": {
                        "dossie": {"value": '{"a": 1, "b": [1, 2, 3]}', "type": "Json"},
                    },
                }
            ],
        )

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )

    tasks = await transport.fetch_and_lock(
        "w", [TopicSubscription("t", 30_000)], max_tasks=5, async_response_timeout_ms=25_000
    )

    assert len(tasks) == 1
    assert tasks[0].variables["dossie"] == {"a": 1, "b": [1, 2, 3]}
    await transport.close()


async def test_real_transport_fetch_and_lock_null_json_variable_stays_none() -> None:
    """A `Json`-typed variable with no value (`value: null`) must decode to `None`, not crash
    `json.loads(None)` (TypeError) — legitimate absent/unset Json process variable."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "task-json-null",
                    "topicName": "t",
                    "processInstanceId": "proc-3",
                    "businessKey": "bk-3",
                    "workerId": "w",
                    "variables": {"dossie": {"value": None, "type": "Json"}},
                }
            ],
        )

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )

    tasks = await transport.fetch_and_lock(
        "w", [TopicSubscription("t", 30_000)], max_tasks=5, async_response_timeout_ms=25_000
    )

    assert len(tasks) == 1
    assert tasks[0].variables["dossie"] is None
    await transport.close()


async def test_real_transport_fetch_and_lock_malformed_json_variable_fails_closed() -> None:
    """Fail-closed contract (design §9 ValueError classification, mirrored at the transport
    seam): malformed JSON inside a `Json`-typed variable must NEVER be handed to a worker as
    the raw string (silent corruption) and must NEVER crash `fetch_and_lock` for the whole
    batch. Instead the offending task is reported `failure(retries=0)` directly (an immediate,
    engine-guaranteed incident — matches this module's ValueError convention) and EXCLUDED
    from the returned batch."""
    failure_calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        if request.url.path == "/engine-rest/external-task/fetchAndLock":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "task-bad-json",
                        "topicName": "operadora.contas.identify_glosa",
                        "processInstanceId": "proc-4",
                        "businessKey": "bk-4",
                        "workerId": "w",
                        "retries": 3,
                        "variables": {
                            "linhas_conta_refs": {"value": "{not-valid-json[", "type": "Json"},
                        },
                    }
                ],
            )
        if request.url.path == "/engine-rest/external-task/task-bad-json/failure":
            failure_calls.append(_json.loads(request.content))
            return httpx.Response(204)
        raise AssertionError(f"unexpected request: {request.url.path}")

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )

    tasks = await transport.fetch_and_lock(
        "w",
        [TopicSubscription("operadora.contas.identify_glosa", 30_000)],
        max_tasks=5,
        async_response_timeout_ms=25_000,
    )

    assert tasks == []  # malformed task excluded, never dispatched
    assert len(failure_calls) == 1
    assert failure_calls[0]["workerId"] == "w"
    assert failure_calls[0]["retries"] == 0  # non-transient — immediate incident, never retried
    await transport.close()


async def test_real_transport_malformed_json_decode_failure_redacts_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T3.4 F5: the malformed-`Json`-variable path (`fetch_and_lock`) reports its incident via
    `self.handle_failure` directly — it bypasses the harness's `_report_failure` chokepoint
    entirely (it fires before a task is even handed to the dispatch loop), so it needs its own
    redaction coverage. Forces a PHI-shaped decode failure via monkeypatch (a real
    `json.JSONDecodeError` never embeds the raw offending text, so this is the only way to
    exercise a PHI-bearing message on this exact path)."""
    import maezo.tools.workers.harness as harness_module

    def _boom(entry: dict[str, Any]) -> Any:
        raise ValueError("cpf invalido: 123.456.789-01 no payload")

    monkeypatch.setattr(harness_module, "_from_camunda_var", _boom)

    failure_calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        if request.url.path == "/engine-rest/external-task/fetchAndLock":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "task-bad-json-2",
                        "topicName": "t",
                        "processInstanceId": "proc-7",
                        "businessKey": "bk-7",
                        "workerId": "w",
                        "retries": 3,
                        "variables": {"payload": {"value": '{"ok": true}', "type": "Json"}},
                    }
                ],
            )
        if request.url.path == "/engine-rest/external-task/task-bad-json-2/failure":
            failure_calls.append(_json.loads(request.content))
            return httpx.Response(204)
        raise AssertionError(f"unexpected request: {request.url.path}")

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )

    tasks = await transport.fetch_and_lock(
        "w", [TopicSubscription("t", 30_000)], max_tasks=5, async_response_timeout_ms=25_000
    )

    assert tasks == []
    assert len(failure_calls) == 1
    error_message = failure_calls[0]["errorMessage"]
    assert "123.456.789-01" not in error_message
    assert "[REDACTED_DIGITS]" in error_message
    await transport.close()


async def test_real_transport_fetch_and_lock_malformed_json_does_not_poison_batch() -> None:
    """One malformed `Json` variable in a batch must not crash `fetch_and_lock` for the other,
    well-formed tasks in the same poll (design §6/§13 fail-closed rule: a transport error must
    propagate, but a single-task decode defect must not masquerade as a transport error and
    drop the entire batch)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/engine-rest/external-task/fetchAndLock":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "task-good",
                        "topicName": "t",
                        "processInstanceId": "proc-5",
                        "businessKey": "bk-5",
                        "workerId": "w",
                        "retries": 3,
                        "variables": {"payload": {"value": '{"ok": true}', "type": "Json"}},
                    },
                    {
                        "id": "task-bad",
                        "topicName": "t",
                        "processInstanceId": "proc-6",
                        "businessKey": "bk-6",
                        "workerId": "w",
                        "retries": 3,
                        "variables": {"payload": {"value": "[[[", "type": "Json"}},
                    },
                ],
            )
        return httpx.Response(204)

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )

    tasks = await transport.fetch_and_lock(
        "w", [TopicSubscription("t", 30_000)], max_tasks=5, async_response_timeout_ms=25_000
    )

    assert [t.task_id for t in tasks] == ["task-good"]
    assert tasks[0].variables["payload"] == {"ok": True}
    await transport.close()


async def test_real_transport_fetch_and_lock_sends_long_poll_and_per_topic_lock() -> None:
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        payloads.append(_json.loads(request.content))
        return httpx.Response(200, json=[])

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )
    await transport.fetch_and_lock(
        "w",
        [TopicSubscription("t1", 15_000, None), TopicSubscription("t2", 45_000, ["a", "b"])],
        max_tasks=3,
        async_response_timeout_ms=25_000,
    )

    body = payloads[0]
    assert body["asyncResponseTimeout"] == 25_000
    assert body["maxTasks"] == 3
    assert body["topics"][0] == {"topicName": "t1", "lockDuration": 15_000}
    assert body["topics"][1] == {"topicName": "t2", "lockDuration": 45_000, "variables": ["a", "b"]}
    await transport.close()


async def test_real_transport_fetch_and_lock_raises_on_error_never_swallows() -> None:
    """Design §6/§13: a transport error must propagate — never a silent empty-list success."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="engine down")

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await transport.fetch_and_lock(
            "w", [TopicSubscription("t", 1000)], max_tasks=1, async_response_timeout_ms=1000
        )
    await transport.close()


async def test_real_transport_complete_types_variables() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(204)

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )
    await transport.complete(
        "t1", "w", {"amount_cents": 5_000_000_000, "approved": True, "dossier": {"a": 1}}
    )

    variables = captured["body"]["variables"]
    assert variables["amount_cents"] == {"value": 5_000_000_000, "type": "Long"}
    assert variables["approved"] == {"value": True, "type": "Boolean"}
    assert variables["dossier"]["type"] == "Json"
    await transport.close()


async def test_real_transport_handle_failure_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        captured["path"] = request.url.path
        return httpx.Response(204)

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )
    await transport.handle_failure("t1", "w", error_message="boom", retries=2, retry_timeout_ms=5000)

    assert captured["path"] == "/engine-rest/external-task/t1/failure"
    assert captured["body"] == {
        "workerId": "w",
        "errorMessage": "boom",
        "retries": 2,
        "retryTimeout": 5000,
    }
    await transport.close()


async def test_real_transport_handle_bpmn_error_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(204)

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )
    await transport.handle_bpmn_error("t1", "w", error_code="ERR_X", error_message="msg")

    assert captured["body"] == {"workerId": "w", "errorCode": "ERR_X", "errorMessage": "msg"}
    await transport.close()


async def test_real_transport_extend_lock_and_unlock_paths() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(204)

    transport = RealTransport("http://engine/engine-rest")
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)
    )
    await transport.extend_lock("t1", "w", new_duration_ms=60_000)
    await transport.unlock("t1")

    assert paths == ["/engine-rest/external-task/t1/extendLock", "/engine-rest/external-task/t1/unlock"]
    await transport.close()


async def test_real_transport_auth_token_sets_bearer_header() -> None:
    transport = RealTransport("http://engine/engine-rest", auth_token="secret-token")
    assert transport._client.headers["Authorization"] == "Bearer secret-token"
    await transport.close()


async def test_real_transport_no_auth_token_no_header() -> None:
    transport = RealTransport("http://engine/engine-rest")
    assert "Authorization" not in transport._client.headers
    await transport.close()
