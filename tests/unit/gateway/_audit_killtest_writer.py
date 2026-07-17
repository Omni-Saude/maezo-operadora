"""Kill-test writer subprocess (T1.10) — NOT a pytest test module (no `test_` prefix).

Spawned by tests/unit/gateway/test_audit_postgres.py::test_kill_test_* as a child process that
writes N audit records through `PostgresAuditSink`, printing an unbuffered confirmation line
after each COMMITTED write. The parent test reads these confirmations, SIGKILLs this process
mid-stream, and checks that every confirmed write actually survived in Postgres.

Usage:
    python _audit_killtest_writer.py --dsn <dsn> --tenant <id> --count <n> --label <batch-label>

Stdout protocol (one line per event, flushed immediately):
    COMMITTED <index> <record_hash>
    DONE <count>
    ERROR <message>
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import PostgresAuditSink


async def _run(dsn: str, tenant: str, count: int, label: str) -> None:
    sink = PostgresAuditSink(dsn, tenant)
    try:
        for i in range(count):
            record = AuditRecord(
                agent_id=f"{label}-{i}",
                tenant_id=tenant,
                agent_version="killtest-1.0.0",
                action="killtest_write",
                decision="ALLOW",
                details={"batch": label, "index": i},
            )
            record_hash = await sink.emit(record)
            print(f"COMMITTED {i} {record_hash}", flush=True)  # noqa: T201 — IPC protocol, not logging
        print(f"DONE {count}", flush=True)  # noqa: T201
    finally:
        await sink.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--label", default="killtest")
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.dsn, args.tenant, args.count, args.label))
    except Exception as exc:  # noqa: BLE001 — report to parent via stdout protocol, then exit non-zero
        print(f"ERROR {exc}", flush=True)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
