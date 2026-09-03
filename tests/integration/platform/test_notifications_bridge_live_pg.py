"""LIVE-PG fenced-start audit-row proof (T2.6-EB3 TESTS section) — port 5647 by default.

Reuses the T2.6-7 fenced-start proof pattern
(`tests/integration/processes/test_sp_op_nip_001.py::
test_nip_handoff_end_to_end_starts_ans_submit_via_live_bridge`) but scoped to the AUDIT lane
ONLY, per this task's explicit boundary: "NO cibseven engine needed for the bridge-start unit
level (the start is mocked/fenced-proven); if you want a real process start, that needs
cibseven — scope to PG-level fenced-start proof + document." A `FakeCibSevenTransport` stands in
for the engine side (no real process is started — just as the unit suite already proves);
`PostgresAuditSink` is REAL, against a real Postgres with migrations 0001->0005 applied to a
per-run tenant schema. This proves the fenced starter
(`build_cibseven_process_starter` -> `start_process_idempotent`) durably persists the ADR-0007
audit row for a REAL bridge handoff (not a synthetic call site), and that a redelivery of the
SAME event is idempotent at the audit layer (`emit_once`'s exactly-once dedup — no second chain
link for a repeated business key).

Reachability convention (mirrors `tests/unit/gateway/test_audit_postgres.py` /
`tests/integration/conftest.py`'s own DSN resolution): `MAEZO_TEST_DATABASE_URL` wins; otherwise
`postgresql://maezo:maezo@localhost:${MAEZO_PG_HOST_PORT:-5647}/maezo` — 5647 (not the 5433
dev-default / 5432 CI-default) so this suite never collides with an already-running Postgres on
a shared dev machine. This directory's `conftest.py` already overrides the parent integration
suite's CIB-Seven-engine autouse gate (this suite needs Postgres only), so a missing broker/engine
elsewhere never blocks this file — only Postgres unreachability does, and that SKIPS LOUDLY
("COULD NOT VERIFY", ADR-0011 posture), never fakes a database.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, schema_for_tenant
from maezo.platform.integrations.notifications_bridge import (
    NOTIFICATIONS_TOPIC,
    REASON_MISSING_TYPE,
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
    run_consumer_loop,
)
from maezo.platform.notification_bridge import (
    CONTAS_COMPLETED_EVENT,
    NotificationBridge,
    build_cibseven_process_starter,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.integration.conftest import _apply_migrations, _pg_reachable

pytestmark = pytest.mark.integration

_DEFAULT_PORT = "5647"


def _pg_dsn() -> str:
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", _DEFAULT_PORT)
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


@pytest.fixture
def pg_tenant_schema() -> Iterator[tuple[str, str]]:
    """Per-test throwaway tenant schema with REAL migrations 0001->0005 applied. Skips LOUDLY
    when Postgres is unreachable (never fakes it).

    Deliberately a PLAIN (non-async) fixture using `asyncio.run()` per operation — mirrors
    `tests/integration/conftest.py`'s own `audit_pg` fixture shape exactly, and avoids nesting
    `asyncio.run()` inside pytest-asyncio's already-running per-test loop (`_pg_reachable`
    itself calls `asyncio.run()`).
    """
    dsn = _pg_dsn()
    if not _pg_reachable(dsn):
        pytest.skip(
            f"COULD NOT VERIFY: no reachable Postgres at {dsn!r} (override with "
            "MAEZO_TEST_DATABASE_URL / MAEZO_PG_HOST_PORT). T2.6-EB3's LIVE-PG fenced-start "
            "proof needs a real Postgres with migrations 0001-0005 applied — bring one up, e.g.:\n"
            "  docker run -d -p 5647:5432 -e POSTGRES_USER=maezo -e POSTGRES_PASSWORD=maezo "
            "-e POSTGRES_DB=maezo pgvector/pgvector:pg16\n"
            "to run this for real."
        )

    tenant_id = f"eb3{uuid.uuid4().hex[:10]}"  # [a-z][a-z0-9]* — no cross-run dedup residue

    async def _create_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}"')
        finally:
            await conn.close()

    async def _drop_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_id}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(_create_schema())
    _apply_migrations(dsn, tenant_id)

    yield dsn, tenant_id

    asyncio.run(_drop_schema())


async def _fetch_chain_rows(dsn: str, tenant_id: str) -> list[Any]:
    schema = schema_for_tenant(tenant_id)
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        return await conn.fetch(f'SELECT action, agent_id FROM "{schema}".audit_chain ORDER BY id')
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_bridge_handoff_through_fenced_starter_emits_durable_audit_row(
    pg_tenant_schema: tuple[str, str],
) -> None:
    """A REAL bridge handoff (CONTAS->RECURSO, the reconciled EB-4 `agents.events.contas.
    completed`/`desfecho=glosa_aplicada_humano` payload — this test previously drove the
    pre-reconciliation `contas.glosa_confirmed` literal, which `notification_bridge.py` no longer
    registers at all) through the fenced starter durably persists the ADR-0007 audit row BEFORE
    the (faked) engine effect — proven by querying `audit_chain` directly, not merely by
    `emit_once` not raising. The payload is a SYNTHETIC enriched one (carries `numero_guia_tiss`,
    the business-key anchor today's real minimal `event_payload_vars` does not yet emit — EB-4
    "arming" follow-up note in `notification_bridge.py`)."""
    dsn, tenant_id = pg_tenant_schema
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    transport = FakeCibSevenTransport()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    try:
        results = await bridge.on_event(
            event_type=CONTAS_COMPLETED_EVENT,
            payload={
                "tenant_id": tenant_id,
                "desfecho": "glosa_aplicada_humano",
                "glosa_id": "GLOSA-LIVE-001",
                "numero_guia_tiss": "GUIA-LIVE-001",
            },
        )
        assert results[0].handoff_triggered is True
        assert results[0].process_instance_id  # the fenced starter actually returned an instance

        rows = await _fetch_chain_rows(dsn, tenant_id)
        assert len(rows) == 1
        assert rows[0]["action"] == "start_process:SP-OP-RECURSO-001"
        assert rows[0]["agent_id"] == "notification_bridge"
    finally:
        await audit_sink.aclose()
        await transport.close()


@pytest.mark.asyncio
async def test_bridge_handoff_redelivery_is_idempotent_at_the_audit_layer(
    pg_tenant_schema: tuple[str, str],
) -> None:
    """Two `on_event` calls for the identical event (a Kafka redelivery) converge on the SAME
    process_instance_id and write exactly ONE audit_chain row — `emit_once`'s exactly-once dedup
    holds for a genuine bridge call, not just the chokepoint's own isolated unit suite."""
    dsn, tenant_id = pg_tenant_schema
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    transport = FakeCibSevenTransport()
    bridge = NotificationBridge(cibseven_starter=build_cibseven_process_starter(transport, audit_sink))

    payload = {
        "tenant_id": tenant_id,
        "desfecho": "glosa_aplicada_humano",
        "glosa_id": "GLOSA-LIVE-002",
        "numero_guia_tiss": "GUIA-LIVE-002",
    }
    try:
        first = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=dict(payload))
        second = await bridge.on_event(event_type=CONTAS_COMPLETED_EVENT, payload=dict(payload))

        assert first[0].handoff_triggered is True
        assert second[0].handoff_triggered is True
        assert first[0].process_instance_id == second[0].process_instance_id

        rows = await _fetch_chain_rows(dsn, tenant_id)
        assert len(rows) == 1, "a redelivered handoff must not write a second audit-chain link"
    finally:
        await audit_sink.aclose()
        await transport.close()


# ---------------------------------------------------------------------------
# GAP-SC-04-a — the dead-letter shunt's DURABLE half, against a real Postgres.
#
# The unit suite proves the shunt's control flow against `FakeAuditSink`. What it cannot prove is
# that the audit fact actually PERSISTS: `AuditRecord.details` must survive the real `decision_basis`
# jsonb column, and the dedup key must actually collapse a redelivery in the real
# `audit_emit_dedup` table (migration 0005) rather than only in a dict.
# ---------------------------------------------------------------------------


class _RecordingDlqPublisher:
    """In-memory `BridgeDlqPublisher` — the KAFKA leg is proven live in
    `test_events_kafka_producer_live.py`; this suite is scoped to the PG lane (module docstring's
    own boundary), so the publisher is a recorder and the AUDIT SINK is real."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish_dlq(self, topic: str, *, raw: bytes, key: bytes | None, headers: Any) -> None:
        self.published.append((topic, raw))


async def _fetch_dlq_rows(dsn: str, tenant_id: str) -> list[Any]:
    schema = schema_for_tenant(tenant_id)
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        return await conn.fetch(
            f'SELECT action, agent_id, decision, decision_basis FROM "{schema}".audit_chain '
            "WHERE action LIKE 'bridge_dlq:%' ORDER BY id"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_dlq_shunt_writes_a_durable_phi_safe_audit_row(
    pg_tenant_schema: tuple[str, str],
) -> None:
    """A poison message shunted through `run_consumer_loop` leaves a REAL `audit_chain` row whose
    `decision_basis` carries bounded tokens plus the one-way `raw_sha256` — and NOT a byte of the
    offending payload. Queried straight out of Postgres, not inferred from `emit_once` returning."""
    import hashlib

    dsn, tenant_id = pg_tenant_schema
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    publisher = _RecordingDlqPublisher()
    bridge = NotificationBridge(
        cibseven_starter=build_cibseven_process_starter(FakeCibSevenTransport(), audit_sink)
    )
    dlq = BridgeDlqShunt(publisher=publisher, audit_sink=audit_sink, tenant_id=tenant_id)

    poison = {"tenant_id": tenant_id, "sem_tipo": "GUIA-DLQ-LIVE-001"}
    message = BridgeMessage.from_value(poison, topic=NOTIFICATIONS_TOPIC, partition=0, offset=11)
    consumer = FakeBridgeKafkaConsumer([message])
    await consumer.start()

    try:
        await run_consumer_loop(consumer, bridge, dlq=dlq)

        assert consumer.commits == 1, "the offset advances ONLY after the shunt confirmed"
        assert publisher.published == [(f"{NOTIFICATIONS_TOPIC}.dlq", message.raw)]

        rows = await _fetch_dlq_rows(dsn, tenant_id)
        assert len(rows) == 1
        assert rows[0]["action"] == f"bridge_dlq:{NOTIFICATIONS_TOPIC}"
        assert rows[0]["agent_id"] == "notification_bridge"
        assert rows[0]["decision"] == "DENY"

        basis = rows[0]["decision_basis"]
        basis_text = basis if isinstance(basis, str) else json.dumps(basis)
        assert REASON_MISSING_TYPE in basis_text
        assert hashlib.sha256(message.raw).hexdigest() in basis_text
        assert "GUIA-DLQ-LIVE-001" not in basis_text, (
            "no byte of the quarantined payload may reach the durable chain — the sha256 is the "
            "evidence, the content is not"
        )
    finally:
        await audit_sink.aclose()


@pytest.mark.asyncio
async def test_dlq_shunt_redelivery_writes_exactly_one_audit_row(
    pg_tenant_schema: tuple[str, str],
) -> None:
    """The at-least-once cost of publish-then-audit, bounded against the REAL `audit_emit_dedup`
    table: three redeliveries of the SAME record re-publish to the DLQ three times and converge on
    ONE chain row, because the dedup key pins both the record's broker coordinates and its bytes."""
    dsn, tenant_id = pg_tenant_schema
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    publisher = _RecordingDlqPublisher()
    bridge = NotificationBridge(
        cibseven_starter=build_cibseven_process_starter(FakeCibSevenTransport(), audit_sink)
    )
    dlq = BridgeDlqShunt(publisher=publisher, audit_sink=audit_sink, tenant_id=tenant_id)
    message = BridgeMessage.from_value({"no": "type"}, topic=NOTIFICATIONS_TOPIC, partition=1, offset=42)

    try:
        for _attempt in range(3):
            consumer = FakeBridgeKafkaConsumer([message])
            await consumer.start()
            await run_consumer_loop(consumer, bridge, dlq=dlq)

        assert len(publisher.published) == 3
        rows = await _fetch_dlq_rows(dsn, tenant_id)
        assert len(rows) == 1, "a re-shunted record must not write a second audit-chain link"
        assert len(dlq.deduped_audits) == 2, (
            "and the two collapsed emits were OBSERVED via `emit_once_status`, not discarded"
        )
    finally:
        await audit_sink.aclose()


@pytest.mark.asyncio
async def test_a_reused_broker_coordinate_with_new_bytes_writes_a_second_audit_row(
    pg_tenant_schema: tuple[str, str],
) -> None:
    """The other half of the dedup contract, against the REAL `audit_emit_dedup` table.

    Broker coordinates are NOT unique over time: deleting and recreating a topic restarts its
    offsets at 0 while the dedup claims survive in Postgres. Under a coordinates-only dedup key
    the second, DIFFERENT poison message hit the first one's claim, `emit_once` no-op'd, the loop
    committed the offset and the quarantine left NO row in the chain. Same tenant, same topic,
    same `(partition, offset)`, different bytes must produce TWO chain rows."""
    dsn, tenant_id = pg_tenant_schema
    audit_sink = PostgresAuditSink(dsn, tenant_id)
    publisher = _RecordingDlqPublisher()
    bridge = NotificationBridge(
        cibseven_starter=build_cibseven_process_starter(FakeCibSevenTransport(), audit_sink)
    )
    dlq = BridgeDlqShunt(publisher=publisher, audit_sink=audit_sink, tenant_id=tenant_id)
    antes = BridgeMessage.from_value(
        {"tenant_id": tenant_id, "sem_tipo": "ANTES"}, topic=NOTIFICATIONS_TOPIC, partition=2, offset=0
    )
    depois = BridgeMessage.from_value(
        {"tenant_id": tenant_id, "sem_tipo": "DEPOIS"}, topic=NOTIFICATIONS_TOPIC, partition=2, offset=0
    )
    assert antes.raw != depois.raw

    try:
        for message in (antes, depois):
            consumer = FakeBridgeKafkaConsumer([message])
            await consumer.start()
            await run_consumer_loop(consumer, bridge, dlq=dlq)

        assert len(publisher.published) == 2
        assert dlq.deduped_audits == [], "neither emit may collapse onto the other's claim"
        rows = await _fetch_dlq_rows(dsn, tenant_id)
        assert len(rows) == 2, "a NEW poison message at a reused coordinate is a NEW ADR-0007 fact"
    finally:
        await audit_sink.aclose()
