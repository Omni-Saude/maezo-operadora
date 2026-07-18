"""Audit emit dedup — per-tenant idempotency for exactly-once audit emission (T-A, T1.10).

ADR-0007 / ADR-0027: every effect on the world is recorded exactly once in the durable
`audit_chain` hash chain (0002_audit_chain.py). The worker harness completes an external task
*after* the audit row is written (audit-before-complete, `docs/design/audit-emit-path-wiring.md`
§4.2), and the engine re-delivers the SAME external task on lock-expiry / failure-with-retries.
Without a guard, that re-delivery re-enters the emit path and writes a SECOND chain link for one
logical effect — a double-audit.

`audit_emit_dedup` is the durable idempotency claim that closes that window. `PostgresAuditSink.
emit_once()` claims a `dedup_key` in this table INSIDE the same per-tenant advisory-lock
transaction that inserts the chain link (0002), so the claim and the link commit atomically:
a crash between them rolls back BOTH (no dangling claim that would suppress the retry → no gap;
no dangling chain link the retry would duplicate → no duplicate). A second emit with an
already-claimed key is a no-op that returns the prior chain link's `record_hash` — never an error.

Dedup-key shape (owned by the caller — T-C / T-C2, not this table):
  - worker completions:  ``{tenant}:{task_id}``                        (CIB Seven external-task id,
                                                                        stable across re-delivery)
  - process-start emits: ``{tenant}:start:{process_key}:{business_key}``

The key already carries the tenant as a prefix, and the platform is schema-per-tenant
(env.py — each tenant gets its own physical `audit_emit_dedup` table), so the composite
`PRIMARY KEY (tenant, dedup_key)` is unforgeably unique within a tenant's chain. This mirrors the
house durable-dedup pattern: `driver_idempotency` (`key text PRIMARY KEY` + a by-age index for the
retention sweep) and `a2a_idempotency` (composite `(task_id, tenant)` PK) from 0003 (ADR-0024 /
ADR-0015). `audit_chain` itself (an L0 table) is deliberately UNTOUCHED — the dedup claim lives in
this sibling table.

Retention: like `audit_chain` (DELETE-by-age, DL-0018/ADR-0027) and `driver_idempotency`
(ADR-0024 §4), stale claims are swept by a periodic
``DELETE FROM audit_emit_dedup WHERE created_at < now() - interval '<retention>'`` — the
`ix_audit_emit_dedup_created` index supports that sweep. A claim need only outlive the engine's
re-delivery window for its effect, so retention here can be far shorter than the chain's.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # audit_emit_dedup — exactly-once audit-emission claim (T-A, ADR-0007)
    # ------------------------------------------------------------------
    # DDL is unqualified (schema-per-tenant, env.py sets search_path) and idempotent
    # (IF NOT EXISTS), mirroring driver_idempotency / a2a_idempotency (0003).
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_emit_dedup (
            tenant       text NOT NULL,
            dedup_key    text NOT NULL,
            record_hash  text NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),

            PRIMARY KEY (tenant, dedup_key)
        )
    """)

    # Supports the by-age retention sweep (DELETE WHERE created_at < now() - interval ...),
    # mirroring ix_driver_idempotency_expires (0003) and audit_chain's DELETE-by-age retention.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_audit_emit_dedup_created
            ON audit_emit_dedup (created_at)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_emit_dedup CASCADE")
