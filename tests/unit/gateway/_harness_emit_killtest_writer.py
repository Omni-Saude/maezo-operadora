"""Worker emit-before-complete kill-test writer subprocess (T-C, T1.10) — NOT a pytest module.

Extends the T-A `_audit_emit_once_killtest_writer.py` from a bare sink writer to the full
worker->emit->complete path (design §6.3). Spawned by
tests/unit/gateway/test_harness_audit_integration.py to prove the fail-closed + exactly-once
guarantee END-TO-END through the real `WorkerHarness._handle`:

  --mode hang-after-emit
      A `WorkerHarness` wired to a REAL `PostgresAuditSink` + a transport whose `complete` prints
      `EMITTED` (flushed) and then hangs forever. `_handle` therefore runs the handler, COMMITS the
      audit row (emit_once's own transaction commits BEFORE `complete` is invoked — audit-before-
      complete, §4.2), then blocks in `complete`. The parent reads `EMITTED`, SIGKILLs the process
      mid-complete. Net durable state: EXACTLY ONE committed audit_chain row, and the engine never
      saw the completion (simulated by the process dying before `complete` returns).

  --mode redeliver
      The engine re-delivers the SAME external-task id. `_handle` re-runs the handler, and
      emit_once DEDUPS (returns the prior chain link's hash, writes NO second row), then `complete`
      succeeds. Prints `COMPLETED <task_id>`.

Together they prove: a hard crash between the durable audit and the mechanical `complete` leaves
ZERO un-audited effects AND ZERO double-audits — the re-delivery self-heals through the dedup.

Stdout protocol (one line per event, flushed immediately):
    EMITTED <dedup_key>         (hang-after-emit: audit row committed, about to block in complete)
    COMPLETED <task_id>         (redeliver: dedup no-op + complete succeeded)
    ERROR <message>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from maezo.gateway.audit_postgres import PostgresAuditSink
from maezo.tools.workers.harness import ExternalTask, WorkerHarness


class _HangOnCompleteTransport:
    """Prints `EMITTED` (the audit is already committed by then) and hangs in `complete` forever."""

    def __init__(self, dedup_key: str) -> None:
        self._dedup_key = dedup_key

    async def fetch_and_lock(self, *a: Any, **k: Any) -> list[ExternalTask]:  # pragma: no cover
        raise NotImplementedError

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        print(f"EMITTED {self._dedup_key}", flush=True)  # noqa: T201 — IPC protocol
        await asyncio.sleep(3600)  # wait to be SIGKILLed AFTER the audit row committed

    async def handle_failure(self, *a: Any, **k: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    async def handle_bpmn_error(self, *a: Any, **k: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    async def extend_lock(self, *a: Any, **k: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    async def unlock(self, *a: Any, **k: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        return None


class _RecordingTransport(_HangOnCompleteTransport):
    """`complete` succeeds and records — the re-delivery after the crash."""

    def __init__(self, dedup_key: str, task_id: str) -> None:
        super().__init__(dedup_key)
        self._task_id = task_id

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        print(f"COMPLETED {self._task_id}", flush=True)  # noqa: T201 — IPC protocol


async def _run(dsn: str, tenant: str, task_id: str, topic: str, mode: str) -> None:
    sink = PostgresAuditSink(dsn, tenant)
    dedup_key = f"{tenant}:{task_id}"
    transport: Any = (
        _HangOnCompleteTransport(dedup_key)
        if mode == "hang-after-emit"
        else _RecordingTransport(dedup_key, task_id)
    )
    harness = WorkerHarness(transport, worker_id="killtest-worker", tenant=tenant, audit_sink=sink)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        return {"faixa_valor": "DENTRO_TETO_L2", "pagamento_executado": True}

    harness.register(topic, handler)
    task = ExternalTask(
        task_id=task_id,
        topic=topic,
        process_instance_id="proc-kt",
        business_key="bk-kt",
        worker_id="killtest-worker",
        variables={"ordem_pagamento_id": "OP-1"},
    )
    try:
        # `_handle` swallows/classifies exceptions internally; on the re-delivery path it drives
        # emit(dedup)->complete to completion. On the hang path it blocks inside complete.
        await harness._handle(task)
    finally:
        await sink.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--topic", default="operadora.pagto.release_low_value_payment")
    parser.add_argument("--mode", choices=("hang-after-emit", "redeliver"), required=True)
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.dsn, args.tenant, args.task_id, args.topic, args.mode))
    except Exception as exc:  # noqa: BLE001 — report to parent via stdout protocol, then exit non-zero
        print(f"ERROR {exc}", flush=True)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
