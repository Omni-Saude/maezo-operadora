"""T3.3 A3 — NotificationBridge negative-certification: fail-closed-under-fault pins.

Extends the T3.3 W0/W1 seam-fault harness (`tests/integration/chaos/conftest.py`,
`test_sink_down_failclosed.py`'s C1-down precedent) to the bridge's FOUR real, LIVE, fenced-start
in-flow handoff workers (`contas.start_recurso`, `contas.start_fraude` [T4 Phase-3 leg],
`fraude.start_credenciamento`, `fraude.start_contratual`) and to the bridge's own fenced starter
(`notification_bridge.build_cibseven_process_starter`):

  - **audit sink down** (a real `PostgresAuditSink` pointed at `dead_dsn`, a syntactically valid
    but unreachable Postgres — same C1-down approximation as `test_sink_down_failclosed.py`):
    each handoff worker/starter must raise `AuditPersistenceError` BEFORE any engine start is
    attempted (the fence's structural emit-before-effect guarantee, ADR-0007/T-C2).

  - **bridge dedup table absent** (a real, REACHABLE Postgres with migrations 0001->0004 applied
    but 0005 (`audit_emit_dedup`) dropped afterward): `emit_once`'s dedup-claim INSERT hits a
    missing table and `PostgresAuditSink` re-raises it as `AuditPersistenceError` — proving the
    bridge's fenced starter fails closed even when the AUDIT CONNECTION itself is healthy but the
    idempotency-claim table specifically is gone, a DISTINCT fault from "PG down" (an unreachable
    DSN would also raise, but for the wrong reason — this isolates the missing-table case).

All 3 workers + the bridge starter are proven functions of the SAME structural fence
(`start_process_idempotent`) — pinning all 4 call sites here (rather than just one) is
non-redundant: it proves the fence's fail-closed guarantee holds at every one of the bridge
program's real effect-producing entry points, not just one representative.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.gateway.audit_postgres import AuditPersistenceError, PostgresAuditSink, normalize_dsn
from maezo.platform.notification_bridge import build_cibseven_process_starter
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers import contas, fraude

pytestmark = [pytest.mark.integration, pytest.mark.chaos]


class _CountingCibSevenTransport(FakeCibSevenTransport):
    """Counts every start_process_instance attempt — the "was the engine ever touched despite
    the audit sink being down" surface every pin below asserts is exactly zero."""

    def __init__(self) -> None:
        super().__init__()
        self.start_call_count = 0

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        self.start_call_count += 1
        return await super().start_process_instance(process_key, business_key, variables)


# ---------------------------------------------------------------------------------------------
# Audit-sink-down (C1-down) — the 3 in-flow fenced-start workers.
# ---------------------------------------------------------------------------------------------


def test_contas_start_recurso_fail_closed_when_audit_sink_down(
    chaos_tenant_schema: str, dead_dsn: str
) -> None:
    """`contas.start_recurso` (operadora.contas.start_recurso, CONTAS bpmn :267) must raise
    `AuditPersistenceError` when the audit sink is down and NEVER attempt the RECURSO-001 engine
    start — the ADR-0007 durable-audit-before-effect fence, proven at THIS real handoff worker
    (not just the generic `start_process_idempotent` unit fence)."""
    dead_sink = PostgresAuditSink(dead_dsn, chaos_tenant_schema)
    transport = _CountingCibSevenTransport()
    variables = {
        "tenant_id": chaos_tenant_schema,
        "glosa_id": "GLOSA-A3-C1",
        "numero_guia_tiss": "GUIA-A3-C1",
        "glosa_type": "tecnica",
        "documentacao_anexa": True,
        "numero_lote_tiss": "LOTE-A3-C1",
    }

    try:
        with pytest.raises(AuditPersistenceError):
            contas.start_recurso(variables, engine=transport, audit_sink=dead_sink)
    finally:
        # aclose() is async; start_recurso itself uses asyncio.run internally and has already
        # returned/raised by the time we get here, so a fresh asyncio.run is safe (no nested loop).
        import asyncio

        asyncio.run(dead_sink.aclose())

    assert transport.start_call_count == 0, (
        "contas.start_recurso attempted an engine start despite the audit sink being down "
        "(un-audited RECURSO-001 start — ADR-0007 L0 violation)"
    )


def test_contas_start_fraude_fail_closed_when_audit_sink_down(
    chaos_tenant_schema: str, dead_dsn: str
) -> None:
    """`contas.start_fraude` (operadora.contas.start_fraude — the T4 Phase-3 CONTAS→FRAUDE in-flow
    worker) must raise `AuditPersistenceError` when the audit sink is down and NEVER attempt the
    FRAUDE-001 engine start — the same ADR-0007 durable-audit-before-effect fence, proven at the
    new handoff worker."""
    dead_sink = PostgresAuditSink(dead_dsn, chaos_tenant_schema)
    transport = _CountingCibSevenTransport()
    variables = {
        "tenant_id": chaos_tenant_schema,
        "prestador_id": "PREST-A3-C1",
        "numero_lote_tiss": "LOTE-A3-C1",
        "analista_id": "analista-a3-c1",
    }

    import asyncio

    try:
        with pytest.raises(AuditPersistenceError):
            contas.start_fraude(variables, engine=transport, audit_sink=dead_sink)
    finally:
        asyncio.run(dead_sink.aclose())

    assert transport.start_call_count == 0, (
        "contas.start_fraude attempted an engine start despite the audit sink being down "
        "(un-audited FRAUDE-001 start — ADR-0007 L0 violation)"
    )


def test_fraude_start_credenciamento_fail_closed_when_audit_sink_down(
    chaos_tenant_schema: str, dead_dsn: str
) -> None:
    """`fraude.start_credenciamento` (operadora.fraude.start_credenciamento, FRAUDE bpmn :341)
    must raise `AuditPersistenceError` when the audit sink is down and NEVER attempt the
    CRED-001 engine start."""
    dead_sink = PostgresAuditSink(dead_dsn, chaos_tenant_schema)
    transport = _CountingCibSevenTransport()
    variables = {
        "tenant_id": chaos_tenant_schema,
        "prestador_id": "PREST-A3-C1",
        "numero_caso": "CASO-A3-C1",
        "bundle_root": "bundle-a3-c1",
    }

    import asyncio

    try:
        with pytest.raises(AuditPersistenceError):
            fraude.start_credenciamento(variables, engine=transport, audit_sink=dead_sink)
    finally:
        asyncio.run(dead_sink.aclose())

    assert transport.start_call_count == 0, (
        "fraude.start_credenciamento attempted an engine start despite the audit sink being down "
        "(un-audited CRED-001 start — ADR-0007 L0 violation)"
    )


def test_fraude_start_contratual_fail_closed_when_audit_sink_down(
    chaos_tenant_schema: str, dead_dsn: str
) -> None:
    """`fraude.start_contratual` (operadora.fraude.start_contratual, FRAUDE bpmn :364) must raise
    `AuditPersistenceError` when the audit sink is down and NEVER attempt the CANCEL-001 (nor,
    transitively, the INADIMPLENCIA-001) engine start."""
    dead_sink = PostgresAuditSink(dead_dsn, chaos_tenant_schema)
    transport = _CountingCibSevenTransport()
    variables = {
        "tenant_id": chaos_tenant_schema,
        "numero_contrato": "CTR-A3-C1",
        "entidade_tipo": "contrato",
        "beneficiario_pseudo_id": "pseudo-a3-c1",
        "numero_caso": "CASO-A3-C1",
        "bundle_root": "bundle-a3-c1",
    }

    import asyncio

    try:
        with pytest.raises(AuditPersistenceError):
            fraude.start_contratual(variables, engine=transport, audit_sink=dead_sink)
    finally:
        asyncio.run(dead_sink.aclose())

    assert transport.start_call_count == 0, (
        "fraude.start_contratual attempted an engine start (CANCEL-001 and/or "
        "INADIMPLENCIA-001) despite the audit sink being down (un-audited start — ADR-0007 L0 "
        "violation)"
    )


# ---------------------------------------------------------------------------------------------
# Audit-sink-down (C1-down) — the bridge's own fenced starter.
# ---------------------------------------------------------------------------------------------


async def test_bridge_fenced_starter_fail_closed_when_audit_sink_down(
    chaos_tenant_schema: str, dead_dsn: str
) -> None:
    """`build_cibseven_process_starter`'s inner `_start` must raise `AuditPersistenceError` (via
    `start_process_idempotent`) when the audit sink is down, and NEVER attempt the engine start —
    the bridge's own fenced-starter twin of the 3 in-flow-worker pins above."""
    dead_sink = PostgresAuditSink(dead_dsn, chaos_tenant_schema)
    transport = _CountingCibSevenTransport()
    starter = build_cibseven_process_starter(transport, dead_sink)

    try:
        with pytest.raises(AuditPersistenceError):
            await starter(
                "SP-OP-RECURSO-001",
                {
                    "tenant_id": chaos_tenant_schema,
                    "business_key": f"RECURSO-{chaos_tenant_schema}-GUIA-A3-C1-GLOSA-A3-C1",
                    "glosa_id": "GLOSA-A3-C1",
                },
            )
    finally:
        await dead_sink.aclose()

    assert transport.start_call_count == 0, (
        "the bridge's fenced starter attempted an engine start despite the audit sink being down"
    )


# ---------------------------------------------------------------------------------------------
# Bridge dedup table absent — a DISTINCT fault from "PG down": the connection is healthy, but
# `audit_emit_dedup` (0005_audit_emit_dedup.py) itself is missing from the tenant schema.
# ---------------------------------------------------------------------------------------------


async def test_bridge_fenced_starter_fail_closed_when_dedup_table_absent(
    chaos_pg_dsn: str, chaos_tenant_schema: str
) -> None:
    """`chaos_tenant_schema` is fully migrated (0001->0005) by the parent fixture; this test then
    DROPS `audit_emit_dedup` to simulate a partially-rolled-back/mid-migration schema — a REAL,
    reachable Postgres with everything else intact. `emit_once`'s dedup-claim INSERT against the
    missing table raises, `PostgresAuditSink` wraps it as `AuditPersistenceError` (never a raw
    asyncpg exception leaking past the sink's fail-closed contract), and the engine start must
    NEVER be attempted — same fail-closed guarantee as "sink down", proven under a DIFFERENT,
    non-connectivity fault class."""
    import asyncpg  # type: ignore[import-untyped]

    conn = await asyncpg.connect(normalize_dsn(chaos_pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{chaos_tenant_schema}"')
        await conn.execute("DROP TABLE audit_emit_dedup")
    finally:
        await conn.close()

    sink = PostgresAuditSink(chaos_pg_dsn, chaos_tenant_schema)
    transport = _CountingCibSevenTransport()
    starter = build_cibseven_process_starter(transport, sink)

    try:
        with pytest.raises(AuditPersistenceError):
            await starter(
                "SP-OP-RECURSO-001",
                {
                    "tenant_id": chaos_tenant_schema,
                    "business_key": f"RECURSO-{chaos_tenant_schema}-GUIA-A3-DEDUP-GLOSA-A3-DEDUP",
                    "glosa_id": "GLOSA-A3-DEDUP",
                },
            )
    finally:
        await sink.aclose()

    assert transport.start_call_count == 0, (
        "the bridge's fenced starter attempted an engine start despite the audit_emit_dedup "
        "table being absent (un-audited/un-deduped start)"
    )
