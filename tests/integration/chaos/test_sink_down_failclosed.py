"""C1-down — audit sink / PG down fail-closed drill (T3.3 W1-seam-faults, BUILD-NOW 🟢).

Extends the T-E sink-down precedent (the audited-refusal chokepoint's "sink outage never fails
open" posture, `tests/unit/tools/workers/test_harness_audited_refusal.py`) to the two
effect-producing chokepoints named in the design's Class C table:

  - **handoff start** (`start_process_idempotent`, transport.py:560): a durable audit row is a
    STRUCTURAL precondition of every engine start (T-C2 fence) — a down sink must raise
    `AuditPersistenceError` and the engine must NEVER be touched.
  - **worker completion** (`WorkerHarness._handle`, harness.py): emit-before-complete (T-C) means
    a down sink raises inside `_emit_audit`, which `_handle` classifies as TRANSIENT and routes to
    `_report_failure` (engine retry/incident) — `transport.complete` must NEVER be called.

"PG down" is approximated here (design §2 Class-C note, honestly labeled) by pointing a real
`PostgresAuditSink` at `dead_dsn` — a syntactically valid but genuinely unreachable DSN
(connect-refusal). This proves the fail-closed-on-LOSS half; it is NOT a substitute for a true
failover/recovery drill (C2-failover, external-blocked — see docs/design/T3.3-chaos-resilience.md
§3/§6 EB-2).
"""

from __future__ import annotations

import pytest

from maezo.gateway.audit_postgres import AuditPersistenceError, PostgresAuditSink
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    FakeCibSevenTransport,
    start_process_idempotent,
)
from maezo.tools.workers.harness import ExternalTask, FakeWorkerTransport, WorkerHarness

from . import mutations

pytestmark = [pytest.mark.integration, pytest.mark.chaos]


def _provenance(tenant_id: str) -> AgentDecisionProvenance:
    return AgentDecisionProvenance(
        agent_id="chaos-c1down",
        agent_version="1.0.0",
        tenant_id=tenant_id,
        decision_basis={"route": "CANCEL"},
    )


async def _noop_handler(task: ExternalTask) -> dict[str, object]:
    return {"resultado": "ok"}


# ---------------------------------------------------------------------------------------------
# Handoff-start path.
# ---------------------------------------------------------------------------------------------


async def test_c1_down_handoff_start_fail_closed_no_unaudited_effect(
    chaos_tenant_schema: str, dead_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PG down during a handoff START: `AuditPersistenceError` must propagate and the engine
    start must NEVER be attempted."""
    dead_sink = PostgresAuditSink(dead_dsn, chaos_tenant_schema)
    transport = FakeCibSevenTransport()
    real_start = FakeCibSevenTransport.start_process_instance
    start_calls = {"n": 0}

    async def _counting_start(
        self: FakeCibSevenTransport, process_key: str, business_key: str, variables: dict[str, object]
    ) -> object:
        start_calls["n"] += 1
        return await real_start(self, process_key, business_key, variables)

    monkeypatch.setattr(FakeCibSevenTransport, "start_process_instance", _counting_start)

    try:
        with pytest.raises(AuditPersistenceError):
            await start_process_idempotent(
                transport,
                process_key="SP-OP-CANCEL-001",
                business_key=f"CANCEL-{chaos_tenant_schema}-down",
                variables={"numero_contrato": "down"},
                audit_sink=dead_sink,
                provenance=_provenance(chaos_tenant_schema),
            )
    finally:
        await dead_sink.aclose()

    assert start_calls["n"] == 0, "an engine start was attempted despite the audit sink being down"


# ---------------------------------------------------------------------------------------------
# Worker-completion path.
# ---------------------------------------------------------------------------------------------


async def test_c1_down_worker_completion_fail_closed_no_unaudited_complete(
    chaos_tenant_schema: str, dead_dsn: str
) -> None:
    """PG down during a worker task COMPLETION: `WorkerHarness._handle` must route to
    retry/incident (`transport.complete` NEVER called) — the emit-before-complete fence
    (T-C, harness.py `_emit_audit`) classifies the sink's `AuditPersistenceError` as transient and
    calls `_report_failure`, never `complete`."""
    dead_sink = PostgresAuditSink(dead_dsn, chaos_tenant_schema)
    transport = FakeWorkerTransport()
    harness = WorkerHarness(
        transport, worker_id="chaos-w1", tenant=chaos_tenant_schema, audit_sink=dead_sink
    )
    harness.register("operadora.chaos.c1down", _noop_handler)
    task = ExternalTask(
        task_id="chaos-task-1",
        topic="operadora.chaos.c1down",
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="chaos-w1",
        variables={},
    )

    try:
        await harness._handle(task)  # noqa: SLF001 — preserved v1 fixture surface (design §16.1)
    finally:
        await dead_sink.aclose()

    assert transport.completed == [], (
        "the task completed despite the audit sink being down (un-audited effect)"
    )
    assert len(transport.failures) == 1, (
        "a sink-down completion must route to retry/incident, not vanish silently"
    )


# ---------------------------------------------------------------------------------------------
# MUTATION-CHECK — "make emit_once swallow AuditPersistenceError (fail-open)".
# ---------------------------------------------------------------------------------------------


@pytest.mark.skipif(
    not mutations.mutation_active("c1_down"),
    reason="mutation-check only runs when MAEZO_CHAOS_MUTATE=c1_down "
    "(see docs/design/T3.3-chaos-resilience.md §4 / tests/integration/chaos/mutations.py)",
)
async def test_c1_down_mutation_check_fail_open_swallow_turns_suite_red(
    chaos_tenant_schema: str, dead_dsn: str
) -> None:
    """MUTATION-CHECK: reproduces the design's C1-down mutation ("make emit_once swallow
    AuditPersistenceError") via `mutations.broken_emit_once_fail_open_swallow` — a monkeypatched
    stand-in bound onto a real (down) `PostgresAuditSink` instance; `src/` is never edited. Under
    the mutation, a worker completion proceeds UN-AUDITED even though the sink is down — the
    exact fail-open hazard the green suite's fail-closed assertion forbids. This test is EXPECTED
    TO FAIL when actually run with `MAEZO_CHAOS_MUTATE=c1_down`.
    """
    dead_sink = PostgresAuditSink(dead_dsn, chaos_tenant_schema)

    async def _fail_open_emit_once(record: object, *, dedup_key: str) -> str:
        return await mutations.broken_emit_once_fail_open_swallow(
            dead_sink, record, dedup_key=dedup_key  # type: ignore[arg-type]
        )

    dead_sink.emit_once = _fail_open_emit_once  # type: ignore[method-assign]

    transport = FakeWorkerTransport()
    harness = WorkerHarness(
        transport, worker_id="chaos-w1-mutation", tenant=chaos_tenant_schema, audit_sink=dead_sink
    )
    harness.register("operadora.chaos.c1down.mutation", _noop_handler)
    task = ExternalTask(
        task_id="chaos-task-mutation",
        topic="operadora.chaos.c1down.mutation",
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="chaos-w1-mutation",
        variables={},
    )

    try:
        await harness._handle(task)  # noqa: SLF001
    finally:
        await dead_sink.aclose()

    # Fail-closed invariant the green suite enforces: never complete un-audited while the sink is
    # down. Under the fail-open mutation the completion proceeds anyway — this MUST fail:
    assert transport.completed == [], (
        "the fail-open mutation allowed the task to COMPLETE despite the audit sink being down "
        "(un-audited effect — exactly the hazard fail-closed forbids)"
    )
