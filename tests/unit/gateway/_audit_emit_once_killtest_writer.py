"""emit_once kill-test writer subprocess (T-A, T1.10) — NOT a pytest module (no `test_` prefix).

Extends the T1.10 `_audit_killtest_writer.py` from a bare `emit()` writer to the
`emit_once()` atomicity proof. Spawned by tests/unit/gateway/test_audit_postgres.py::
test_kill_test_emit_once_* to prove that the dedup-claim and the chain-insert commit ATOMICALLY:
a SIGKILL landing *between* them must roll back BOTH (no dangling claim → no gap; no orphan chain
link → no duplicate on re-delivery).

Two modes:

  --mode hang-before-chain
      Monkeypatch `PostgresAuditSink._insert_chain_row` to print `CLAIMED <dedup_key>` (flushed)
      and then hang forever. `emit_once()` therefore runs the advisory lock + the dedup-claim
      INSERT (inside its uncommitted transaction), then blocks at the chain insert — the exact
      point the test wants to interrupt. The parent reads `CLAIMED`, SIGKILLs, and Postgres rolls
      back the whole transaction (claim included) when the connection dies. Nothing is left behind.

  --mode complete
      Run the real `emit_once()` to completion (the re-delivery after the crash). Prints
      `DONE <record_hash>`.

Stdout protocol (one line per event, flushed immediately):
    CLAIMED <dedup_key>      (hang-before-chain: dedup claim executed, chain insert about to block)
    DONE <record_hash>       (complete: chain link committed)
    ERROR <message>
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import maezo.gateway.audit_postgres as audit_postgres
from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink


def _install_hang_before_chain(dedup_key: str) -> None:
    """Replace `_insert_chain_row` so it announces the committed-in-txn claim, then blocks forever.

    The claim INSERT has already run (inside the still-open transaction) by the time emit_once
    reaches `_insert_chain_row`; hanging here holds that transaction open — and its dedup claim
    uncommitted — until the parent SIGKILLs, which forces a Postgres rollback of both.
    """

    async def _hang(self: PostgresAuditSink, conn: object, record: AuditRecord) -> None:
        print(f"CLAIMED {dedup_key}", flush=True)  # noqa: T201 — IPC protocol, not logging
        await asyncio.sleep(3600)  # wait to be SIGKILLed mid-transaction

    audit_postgres.PostgresAuditSink._insert_chain_row = _hang  # type: ignore[method-assign]


async def _run(dsn: str, tenant: str, dedup_key: str, mode: str) -> None:
    if mode == "hang-before-chain":
        _install_hang_before_chain(dedup_key)

    sink = PostgresAuditSink(dsn, tenant)
    try:
        record = AuditRecord(
            agent_id="emit-once-killtest",
            tenant_id=tenant,
            agent_version="killtest-1.0.0",
            action="emit_once_killtest",
            decision="ALLOW",
            details={"dedup_key": dedup_key},
        )
        record_hash = await sink.emit_once(record, dedup_key=dedup_key)
        # Only reached in --mode complete (hang-before-chain never returns).
        print(f"DONE {record_hash}", flush=True)  # noqa: T201 — IPC protocol, not logging
    finally:
        await sink.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--dedup-key", required=True)
    parser.add_argument("--mode", choices=("hang-before-chain", "complete"), required=True)
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.dsn, args.tenant, args.dedup_key, args.mode))
    except Exception as exc:  # noqa: BLE001 — report to parent via stdout protocol, then exit non-zero
        print(f"ERROR {exc}", flush=True)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
