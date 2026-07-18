"""Unit tests for the harness audit emit-before-complete wiring (T-C, T1.10; ADR-0007, L0).

Fast, no engine, no Postgres — every test uses `FakeWorkerTransport` + `FakeDmnTransport` + a fake
`AuditEmitter` sink (design §6.1). The REAL-Postgres acceptance + fail-closed kill-test live under
`tests/unit/gateway/test_harness_audit_integration.py` (§6.2/§6.3).

Coverage (design §6.1):
  1. exactly-once + right fields (PHI-safe `decision_basis`, `dmn_versions` from the collector);
  2. audit-BEFORE-complete ordering;
  3. fail-closed: sink raises -> `complete` never called -> failure/incident;
  4. dmn_versions capture across the sync/async thread bridge (`asyncio.to_thread`);
  5. idempotency: engine re-delivery dedups (no second chain link) and `complete` retries;
  + missing-sink fail-closed (MUST-FIX 2 belt-and-suspenders), PHI curation hard gate.
"""

from __future__ import annotations

import functools
from typing import Any

import pytest

from maezo.gateway.audit import AuditRecord
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import FakeDmnTransport, evaluate_sync, first_row
from maezo.tools.workers.harness import (
    AUDIT_AGENT_ID,
    AUDIT_DECISION_COMPLETE,
    AuditEmitError,
    ExternalTask,
    FakeAuditSink,
    FakeWorkerTransport,
    WorkerHarness,
)

# `asyncio_mode = "auto"` (pyproject.toml) collects async def tests automatically.


def _task(
    *,
    task_id: str = "task-1",
    topic: str = "operadora.test.topic",
    variables: dict[str, Any] | None = None,
    retries: int | None = None,
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


class _OrderRecordingTransport(FakeWorkerTransport):
    """Shares a call-order log with `_OrderRecordingSink` to prove emit precedes complete."""

    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        self._calls.append("complete")
        await super().complete(task_id, worker_id, variables)


class _OrderRecordingSink(FakeAuditSink):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        self._calls.append("emit")
        return await super().emit_once(record, dedup_key=dedup_key)


# ---------------------------------------------------------------------------
# 1. Exactly-once + right fields
# ---------------------------------------------------------------------------


async def test_success_emits_once_with_adr0007_tuple() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="pod-xyz", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"faixa_valor": "DENTRO_TETO_L2", "grupo_aprovador": "ALCADA_L1"}

    harness.register("operadora.pagto.calculate_facts", handler)
    await harness._handle(_task(topic="operadora.pagto.calculate_facts", variables={"cpf": "x"}))

    assert transport.completed and transport.completed[0][0] == "task-1"
    assert len(sink.emitted) == 1
    record, dedup_key = sink.emitted[0]
    # ADR-0007 tuple: stable SERVICE identity (NOT the per-replica worker_id), topic = action,
    # deterministic worker => no LLM provenance.
    assert record.agent_id == AUDIT_AGENT_ID != "pod-xyz"
    assert record.tenant_id == "amh"
    assert record.action == "operadora.pagto.calculate_facts"
    assert record.decision == AUDIT_DECISION_COMPLETE
    assert record.model_id is None and record.prompt_version is None
    assert dedup_key == "amh:task-1"  # {tenant}:{task_id}, matches emit_once's contract


async def test_decision_basis_is_phi_safe_curated_allowlist() -> None:
    """Raw PHI-bearing inputs are HASHED, never stored; only bounded enum/flag output tokens land
    in the clear; free text + minted identifiers are dropped (design §3.3, the L0 hard gate)."""
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {
            "faixa_valor": "ALCADA_L2",  # bounded enum -> kept
            "tier_minimo": 2,  # small int -> kept
            "pagamento_executado": True,  # bool -> kept
            "motivo": "paciente João, CID C50",  # FREE TEXT w/ PHI -> dropped
            "auth_number": "AUTH-amh-guia-abc123",  # minted identifier -> dropped
        }

    harness.register("t", handler)
    phi_vars = {"cpf": "12345678900", "numero_guia_tiss": "G-777", "nome": "João da Silva"}
    await harness._handle(_task(topic="t", variables=phi_vars))

    record, _ = sink.emitted[0]
    basis = record.details
    # input_sha256 present, deterministic, and the raw inputs are NOWHERE in the record.
    assert len(basis["input_sha256"]) == 64
    blob = str(basis)
    for leak in ("12345678900", "G-777", "João", "numero_guia_tiss", "cpf"):
        assert leak not in blob, f"PHI/identifier leaked into decision_basis: {leak!r}"
    # curated tokens kept; free text + identifiers dropped.
    assert basis["faixa_valor"] == "ALCADA_L2"
    assert basis["tier_minimo"] == 2
    assert basis["pagamento_executado"] is True
    assert "motivo" not in basis
    assert "auth_number" not in basis


# ---------------------------------------------------------------------------
# 2. Ordering: emit BEFORE complete
# ---------------------------------------------------------------------------


async def test_emit_happens_before_complete() -> None:
    calls: list[str] = []
    transport = _OrderRecordingTransport(calls)
    sink = _OrderRecordingSink(calls)
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"desfecho": "liberado_automatico"}

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert calls == ["emit", "complete"]  # durable audit gates the effect


# ---------------------------------------------------------------------------
# 3. Fail-closed: sink failure => NO complete, failure reported
# ---------------------------------------------------------------------------


async def test_emit_failure_fails_closed_no_complete() -> None:
    from maezo.gateway.audit_postgres import AuditPersistenceError

    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    sink.always_fail = AuditPersistenceError("db down")
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, max_retry_attempts=3)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"result": "ok"}

    harness.register("t", handler)
    await harness._handle(_task(topic="t", retries=3))

    # The effect is NEVER committed without a durable audit row (ADR-0007).
    assert transport.completed == []
    assert len(transport.failures) == 1
    # AuditPersistenceError is a RuntimeError -> transient -> engine-computed retry (3-1=2).
    assert transport.failures[0][2] == 2


async def test_missing_sink_fails_closed_never_completes() -> None:
    """Belt-and-suspenders (MUST-FIX 2): a harness built without a sink must NEVER complete a
    task — it fails closed at dispatch (AuditEmitError -> transient -> incident/retry)."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh")  # NO audit_sink

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"result": "ok"}

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert transport.completed == []
    assert len(transport.failures) == 1


def test_audit_emit_error_is_runtime_error() -> None:
    # So it lands in _handle's transient branch (engine-computed retry / self-heal).
    assert issubclass(AuditEmitError, RuntimeError)


# ---------------------------------------------------------------------------
# 4. dmn_versions capture across the sync/async thread bridge
# ---------------------------------------------------------------------------


def _route_via_dmn(variables: dict[str, Any], *, dmn: FakeDmnTransport) -> dict[str, Any]:
    """A sync worker fn that evaluates a DMN table via evaluate_sync (the real worker pattern)."""
    rows, _version = evaluate_sync(dmn, "pagto_alcada", {"valor_pagamento_cents": 5000})
    row = first_row(rows, "pagto_alcada", variables)
    return {"faixa_valor": str(row["faixa_valor"]), "grupo_aprovador": str(row["grupo_aprovador"])}


async def test_dmn_versions_captured_across_to_thread_bridge() -> None:
    """A `FunctionWorker` (dispatched via `asyncio.to_thread`) that evaluates a DMN table must
    surface the consulted `DmnVersion` into `AuditRecord.dmn_versions` — the mutable-container
    ContextVar bridge (regression test for the T-B contract, design §3.2/§6.1(4))."""
    dmn = FakeDmnTransport()
    dmn.register(
        "pagto_alcada",
        [{"faixa_valor": "ALCADA_L1", "grupo_aprovador": "GRP_L1"}],
        version=7,
        definition_id="pagto_alcada:7:abc",
        deployment_id="dep-123",
    )
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)
    harness.register_worker(
        FunctionWorker("operadora.pagto.calculate_facts", functools.partial(_route_via_dmn, dmn=dmn))
    )

    await harness._handle(_task(topic="operadora.pagto.calculate_facts"))

    assert transport.completed  # succeeded
    record, _ = sink.emitted[0]
    assert record.dmn_versions == {
        "pagto_alcada": {"version": 7, "id": "pagto_alcada:7:abc", "deploymentId": "dep-123"}
    }


async def test_dmn_versions_empty_when_no_table_consulted() -> None:
    """A topic that consults no DMN table still audits — with an empty (never-null) dmn_versions."""
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"pagamento_executado": True}

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    record, _ = sink.emitted[0]
    assert record.dmn_versions == {}


async def test_collector_reset_per_task_no_cross_leak() -> None:
    """The collector is reset per task — a DMN consulted in task A never leaks into task B's
    record (reset-per-task invariant, design §3.2 / MUST-FIX 1)."""
    dmn = FakeDmnTransport()
    dmn.register("pagto_alcada", [{"faixa_valor": "ALCADA_L1", "grupo_aprovador": "G"}], version=1)
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)
    harness.register_worker(FunctionWorker("dmn.topic", functools.partial(_route_via_dmn, dmn=dmn)))

    async def plain(task: ExternalTask) -> dict[str, Any]:
        return {"ok": True}

    harness.register("plain.topic", plain)

    await harness._handle(_task(task_id="A", topic="dmn.topic"))
    await harness._handle(_task(task_id="B", topic="plain.topic"))

    record_a, _ = sink.emitted[0]
    record_b, _ = sink.emitted[1]
    assert "pagto_alcada" in record_a.dmn_versions
    assert record_b.dmn_versions == {}  # no leak from task A


# ---------------------------------------------------------------------------
# 5. Idempotency: engine re-delivery dedups, complete retries
# ---------------------------------------------------------------------------


class _CompleteFailsOnceTransport(FakeWorkerTransport):
    """`complete` raises a transient error on its FIRST call, then succeeds — models the
    'emit succeeded, complete failed, engine re-delivers' window (design §4.2/§4.3)."""

    def __init__(self) -> None:
        super().__init__()
        self._complete_calls = 0

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        self._complete_calls += 1
        if self._complete_calls == 1:
            raise RuntimeError("transient complete failure")
        await super().complete(task_id, worker_id, variables)


async def test_redelivery_dedups_audit_and_retries_complete() -> None:
    transport = _CompleteFailsOnceTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"faixa_valor": "ALCADA_L1"}

    harness.register("t", handler)
    task = _task(topic="t", task_id="task-42")

    # 1st delivery: emit succeeds, complete fails -> reported failure, NOT completed.
    await harness._handle(task)
    assert transport.completed == []
    assert len(transport.failures) == 1
    assert len(sink.emitted) == 1  # exactly one real chain link written

    # 2nd delivery (engine re-delivers the SAME task_id): emit dedups (no 2nd link), complete now
    # succeeds. Exactly-once audit, and the effect ultimately lands (no gap).
    await harness._handle(task)
    assert transport.completed and transport.completed[0][0] == "task-42"
    assert len(sink.emitted) == 1  # STILL one — the re-delivery was deduped (ALREADY_AUDITED)


async def test_two_distinct_tasks_two_audit_rows() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"desfecho": "liberado_automatico"}

    harness.register("t", handler)
    await harness._handle(_task(topic="t", task_id="a"))
    await harness._handle(_task(topic="t", task_id="b"))

    assert len(sink.emitted) == 2
    assert {k for _, k in sink.emitted} == {"amh:a", "amh:b"}


# ---------------------------------------------------------------------------
# Failure paths do NOT emit (T-E deferred) — only the success path audits
# ---------------------------------------------------------------------------


async def test_handler_error_does_not_emit() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        raise ValueError("bad input")

    harness.register("t", handler)
    await harness._handle(_task(topic="t"))

    assert sink.emitted == []  # audited-refusal on failure paths is T-E, deferred
    assert len(transport.failures) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("DENTRO_TETO_L2", True),
        ("liberado_automatico", True),
        ("APROVAR", True),
        ("AUTH-amh-guia-abc", False),  # minted identifier (hyphens)
        ("ordem invalida", False),  # free text (space)
        ("ANSPROTO-ABCDEF", False),  # protocolo (hyphen)
        (True, True),
        (2, True),
        (10**9, False),  # unbounded int (e.g. money cents)
        ({"a": 1}, False),
        (None, False),
    ],
)
def test_is_bounded_token_value_guard(value: Any, expected: bool) -> None:
    from maezo.tools.workers.harness import _is_bounded_token

    assert _is_bounded_token(value) is expected
