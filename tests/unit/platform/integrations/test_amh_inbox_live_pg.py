"""LIVE-Postgres proof for MZO-060: migration 0007 applies, reverses, re-applies, and the AMH
inbox repository's SQL does on a real server exactly what its return types claim.

Tier 2 (`@pytest.mark.integration`), placed HERE rather than under `tests/integration/` for the
same reason `tests/unit/a2a/test_a2a_edge_live_pg.py` is: this suite needs a REAL Postgres but
explicitly NOT the CIB Seven BPMN engine, and `tests/integration/`'s package-wide autouse
`_skip_if_engine_unreachable` fixture would gate it on an engine it has nothing to do with. It
SKIPS LOUDLY when no Postgres is reachable — never silently passes.

    docker run -d --rm --name maezo-inbox-pg \\
      -e POSTGRES_USER=maezo -e POSTGRES_PASSWORD=maezo -e POSTGRES_DB=maezo \\
      -p 5647:5432 pgvector/pgvector:pg16

Port 5647 is deliberately NOT the compose stack's 5432/5433 and not the A2A suite's 5643: a
throwaway inbox database must never be able to touch a developer's real dev-stack state. Override
with `MAEZO_TEST_AMH_INBOX_DATABASE_URL`.

**What this proves that the DB-free suite cannot.**

1.  *The migration is reversible in both directions on a real server* — `upgrade head` ->
    `downgrade 0006` -> `upgrade head`, with the table's presence/absence asserted at each step.
    This is the evidence a DBA's rollback procedure rests on; a `downgrade()` that has never been
    executed is a rollback plan nobody has tested.
2.  *The lifecycle is genuinely monotonic* — settling a quarantined row and quarantining a settled
    one are refused by the DATABASE's WHERE predicate, not by an in-memory guard a test double
    could satisfy.
3.  *The CHECK constraints reject out-of-band writes* — a free-text `quarantine_reason`, an unknown
    status, a `SETTLED` row with no `settled_at`. These are the defences against a writer that is
    not this repository, so only a real server can demonstrate them.
4.  *No PHI reaches a stored row* — every excluded envelope field carries a sentinel, and the proof
    reads the persisted rows back out of Postgres rather than inspecting bind parameters.
5.  *The pending scan uses the partial index* — asserted from a real `EXPLAIN`, so an index that
    stops being usable by its own query fails here instead of in production.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
import yaml

from maezo.gateway.audit_postgres import normalize_dsn
from maezo.platform.integrations.amh_inbox import (
    _INSERT_SQL,
    AmhInboxUnavailableError,
    InboxRatification,
    InboxRecordOutcome,
    InboxSettleOutcome,
    InboxStream,
    PostgresAmhInbox,
    _stored_row,
    build_amh_inbox_repository,
    migration_digest,
)
from maezo.ports.envelope import CanonicalEnvelope, SourcePosition
from maezo.ports.errors import PortFailureReason

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DRAFT_ARTIFACT = _REPO_ROOT / "spec" / "policies" / "amh" / "inbox-ratification.yaml"

#: A FREE, dedicated port for this suite's throwaway Postgres — never the compose stack's.
_DEFAULT_DSN = "postgresql://maezo:maezo@localhost:5647/maezo"

_DIGEST = "d" * 64
_PHI_SENTINEL = "PHI-SENTINEL-52dfe1-DO-NOT-PERSIST"


def _default_test_dsn() -> str:
    return os.environ.get("MAEZO_TEST_AMH_INBOX_DATABASE_URL", _DEFAULT_DSN)


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


def _alembic_config(dsn: str, tenant_id: str) -> Any:
    from alembic.config import Config

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "src" / "maezo" / "platform" / "migrations"))
    async_dsn = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    cfg.set_main_option("sqlalchemy.url", async_dsn)
    cfg.cmd_opts = argparse.Namespace(x=[f"tenant={tenant_id}"])  # env.py: -x tenant=<id>
    return cfg


def _upgrade(dsn: str, tenant_id: str, revision: str = "head") -> None:
    from alembic import command

    command.upgrade(_alembic_config(dsn, tenant_id), revision)


def _downgrade(dsn: str, tenant_id: str, revision: str) -> None:
    from alembic import command

    command.downgrade(_alembic_config(dsn, tenant_id), revision)


async def _table_exists(dsn: str, schema: str, table: str) -> bool:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        found = await conn.fetchval("SELECT to_regclass($1)", f'"{schema}".{table}')
    finally:
        await conn.close()
    return found is not None


def _new_schema(dsn: str) -> str:
    tenant_id = f"mzo060{uuid.uuid4().hex[:12]}"  # [a-z][a-z0-9_]* per schema_for_tenant

    async def _create() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}"')
        finally:
            await conn.close()

    asyncio.run(_create())
    return tenant_id


def _drop_schema(dsn: str, tenant_id: str) -> None:
    async def _drop() -> None:
        conn = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_id}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(_drop())


@pytest.fixture(scope="module")
def pg_dsn() -> str:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"COULD NOT VERIFY: Postgres not reachable at {dsn!r} (override with "
            "MAEZO_TEST_AMH_INBOX_DATABASE_URL). This suite needs a FREE, dedicated Postgres on "
            "port 5647 — see the module docstring for the one-line docker command."
        )
    return dsn


@pytest.fixture(scope="module")
def tenant_schema(pg_dsn: str) -> Iterator[str]:
    """A throwaway per-run schema with migrations 0001..0007 applied."""
    tenant_id = _new_schema(pg_dsn)
    _upgrade(pg_dsn, tenant_id)
    yield tenant_id
    _drop_schema(pg_dsn, tenant_id)


@pytest.fixture
def ratified_artifact(tmp_path: Path) -> Path:
    """A ratified fixture — the ONLY difference from the shipped DRAFT is the human fields.

    Deliberately a fixture written here rather than an edit to `spec/`: this suite must never be
    able to leave a ratified artifact on disk, and a test that ratified the real file would be
    fabricating a DBA approval.
    """
    path = tmp_path / "inbox-ratification.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "status": "RATIFIED",
                "ratificado": True,
                "dba_review": "APPROVED",
                "dba_reviewer": "test fixture — NOT a real ratification",
                "dba_review_date": "2026-08-09",
                "evidence_ref": "mzo-060",
                "notes": "live-pg suite fixture",
                "migration_revision": "0007",
                "migration_sha256": migration_digest(),
                "review_packet": "docs/reviews/mzo-060-dba-review-packet.md",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
async def inbox(pg_dsn: str, tenant_schema: str, ratified_artifact: Path) -> AsyncIterator[PostgresAmhInbox]:
    repo = build_amh_inbox_repository(dsn=pg_dsn, tenant=tenant_schema, ratification_path=ratified_artifact)
    yield repo
    await repo.aclose()


def _envelope(
    event_id: str, *, payload_hash: str = "a" * 64, amh_tenant: str = "amh-tenant-a"
) -> CanonicalEnvelope:
    """An envelope whose six EXCLUDED fields all carry the PHI sentinel."""
    return CanonicalEnvelope(
        event_id=event_id,
        event_type="work_item.created",
        canonical_schema_version="1.0.0",
        occurred_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        ingested_at=datetime(2026, 8, 9, 12, 0, 1, tzinfo=UTC),
        source_vendor="philips",
        source_product="tasy_healthcare_plan",
        source_instance="inst-a",
        source_tenant="srct-a",
        source_entity="authorization_request",
        protected_source_record_ref=_PHI_SENTINEL,
        source_position=SourcePosition(kind="scn", value="99001"),
        amh_tenant=amh_tenant,
        legal_entity="le-a",
        portable_subject_ref=_PHI_SENTINEL,
        amh_mpi_ref=_PHI_SENTINEL,
        beneficiary_ref=_PHI_SENTINEL,
        correlation_id="corr-1",
        causation_id="caus-1",
        idempotency_key=f"idem-{event_id}",
        consent_decision_ref=_PHI_SENTINEL,
        purpose_of_use="treatment",
        data_classification="restricted",
        trace_id="trace-1",
        producer_version="1.2.3",
        contract_manifest_digest=_DIGEST,
        payload_hash=payload_hash,
        replay_count=0,
    )


# ===========================================================================
# 1. The migration is reversible on a real server
# ===========================================================================


def test_upgrade_downgrade_upgrade_roundtrip(pg_dsn: str) -> None:
    """THE rollback evidence: 0007 applies, reverses cleanly, and re-applies onto its own ruins.

    Runs in its own throwaway schema so the roundtrip cannot disturb the module-scoped one every
    other test in this file shares.
    """
    tenant_id = _new_schema(pg_dsn)
    try:
        _upgrade(pg_dsn, tenant_id)
        assert asyncio.run(_table_exists(pg_dsn, tenant_id, "amh_inbox")), (
            "upgrade head did not create amh_inbox"
        )

        _downgrade(pg_dsn, tenant_id, "0006")
        assert not asyncio.run(_table_exists(pg_dsn, tenant_id, "amh_inbox")), (
            "downgrade 0006 left amh_inbox behind — the rollback procedure is not honest"
        )
        # 0006's own tables must survive: 0007 is additive and its downgrade touches nothing else.
        assert asyncio.run(_table_exists(pg_dsn, tenant_id, "audit_chain"))
        assert asyncio.run(_table_exists(pg_dsn, tenant_id, "a2a_idempotency"))
        assert asyncio.run(_table_exists(pg_dsn, tenant_id, "custody_bundles"))

        _upgrade(pg_dsn, tenant_id)
        assert asyncio.run(_table_exists(pg_dsn, tenant_id, "amh_inbox")), (
            "re-upgrade did not restore amh_inbox"
        )
    finally:
        _drop_schema(pg_dsn, tenant_id)


def test_migration_creates_the_three_indexes_and_the_pk(pg_dsn: str, tenant_schema: str) -> None:
    async def _indexes() -> list[str]:
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            rows = await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE schemaname = $1 AND tablename = 'amh_inbox' "
                "ORDER BY indexname",
                tenant_schema,
            )
        finally:
            await conn.close()
        return [row["indexname"] for row in rows]

    assert asyncio.run(_indexes()) == [
        "ix_amh_inbox_idempotency",
        "ix_amh_inbox_pending",
        "ix_amh_inbox_quarantined",
        "pk_amh_inbox",
    ]


# ===========================================================================
# 2. The gate holds against a REAL database
# ===========================================================================


def test_the_shipped_draft_refuses_even_with_a_live_database(pg_dsn: str, tenant_schema: str) -> None:
    """A reachable, migrated database changes nothing: the DBA gate is the artifact, not the DDL."""
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        build_amh_inbox_repository(dsn=pg_dsn, tenant=tenant_schema, ratification_path=_DRAFT_ARTIFACT)
    assert excinfo.value.reason == "not_ratified"


async def test_a_hand_built_ratification_cannot_settle_rows_on_a_real_server(
    pg_dsn: str, tenant_schema: str
) -> None:
    """The defect this test was written FROM, pinned where it actually happened.

    `InboxRatification` is a dataclass and `__post_init__` checks only the two human switches, so
    one built by hand with `migration_revision="0001"` and `migration_sha256="deadbeef"` used to
    produce a repository that recorded and SETTLED rows against this very database — an `ack`
    licensed by an approval covering DDL nobody had ever seen. The constructor's digest re-check is
    what stops it, and a live server is where the claim is worth anything.
    """
    forged = InboxRatification(
        status="RATIFIED",
        ratificado=True,
        dba_review="APPROVED",
        dba_reviewer="not a DBA",
        dba_review_date="1999-01-01",
        evidence_ref="none",
        notes="hand-built, bound to nothing",
        migration_revision="0001",
        migration_sha256="deadbeef",
        review_packet="p.md",
    )
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        PostgresAmhInbox(dsn=pg_dsn, tenant=tenant_schema, ratification=forged)
    assert excinfo.value.reason == "migration_revision_mismatch"

    object.__setattr__(forged, "migration_revision", "0007")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        PostgresAmhInbox(dsn=pg_dsn, tenant=tenant_schema, ratification=forged)
    assert excinfo.value.reason == "migration_digest_mismatch"

    # Non-vacuity: with the REAL digest the same object builds, so the refusal above is the digest
    # check doing its job rather than the constructor being broken.
    object.__setattr__(forged, "migration_sha256", migration_digest())
    repo = PostgresAmhInbox(dsn=pg_dsn, tenant=tenant_schema, ratification=forged)
    await repo.aclose()


# ===========================================================================
# 3. Dedup + conflict detection
# ===========================================================================


async def test_record_is_idempotent_and_detects_a_mutated_replay(inbox: PostgresAmhInbox) -> None:
    first = await inbox.record(_envelope("evt-dedup"), stream=InboxStream.WORK_ITEM)
    assert first.outcome is InboxRecordOutcome.RECORDED
    assert first.redelivery_count == 1

    again = await inbox.record(_envelope("evt-dedup"), stream=InboxStream.WORK_ITEM)
    assert again.outcome is InboxRecordOutcome.DUPLICATE
    assert again.redelivery_count == 2

    mutated = await inbox.record(_envelope("evt-dedup", payload_hash="b" * 64), stream=InboxStream.WORK_ITEM)
    assert mutated.outcome is InboxRecordOutcome.CONFLICT
    assert mutated.redelivery_count == 2, "a detected conflict must not mutate state"

    cross_tenant = await inbox.record(
        _envelope("evt-dedup", amh_tenant="amh-tenant-b"), stream=InboxStream.WORK_ITEM
    )
    assert cross_tenant.outcome is InboxRecordOutcome.CONFLICT

    assert await inbox.is_duplicate(contract_manifest_digest=_DIGEST, event_id="evt-dedup") is True
    assert await inbox.is_duplicate(contract_manifest_digest=_DIGEST, event_id="never") is False


@pytest.mark.parametrize(
    ("label", "rollback", "expected"),
    [
        ("commit", False, InboxRecordOutcome.DUPLICATE),
        ("rollback", True, InboxRecordOutcome.RECORDED),
    ],
)
async def test_a_conflicting_uncommitted_insert_makes_record_wait_not_return_concurrent(
    inbox: PostgresAmhInbox, pg_dsn: str, tenant_schema: str, label: str, rollback: bool, expected: Any
) -> None:
    """The concurrency claim this module's docs used to get backwards, measured instead of assumed.

    `ON CONFLICT DO NOTHING` does NOT return nothing for an in-flight conflicting insert: Postgres
    speculative insertion BLOCKS on the conflicting transaction's xid until it ends, and only then
    resolves — DUPLICATE if that writer committed, RECORDED if it rolled back. `CONCURRENT` is
    therefore not the outcome of this race at READ COMMITTED, which is what the docstrings now say.

    The other half, stated because it is the DBA's problem and not this test's: the wait is
    UNBOUNDED. This pool sets no `lock_timeout` and no `command_timeout`, so the ceiling is another
    replica's transaction. Packet decision D-6.
    """
    event_id = f"evt-race-{label}"
    hold_seconds = 2.0

    async def hold_uncommitted(started: asyncio.Event) -> None:
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(f'SET search_path TO "{tenant_schema}"')
            tx = conn.transaction()
            await tx.start()
            await conn.fetchrow(_INSERT_SQL, *_stored_row(_envelope(event_id), InboxStream.WORK_ITEM))
            started.set()
            await asyncio.sleep(hold_seconds)
            await (tx.rollback() if rollback else tx.commit())
        finally:
            await conn.close()

    started = asyncio.Event()
    holder = asyncio.create_task(hold_uncommitted(started))
    await asyncio.wait_for(started.wait(), timeout=10.0)

    began = time.monotonic()
    result = await inbox.record(_envelope(event_id), stream=InboxStream.WORK_ITEM)
    waited = time.monotonic() - began
    await holder

    assert result.outcome is expected, f"expected {expected} after the other writer {label}d"
    assert result.outcome is not InboxRecordOutcome.CONCURRENT
    assert waited > hold_seconds / 2, (
        f"record() returned in {waited:.2f}s — it did NOT block on the conflicting transaction, so "
        "the speculative-insertion wait this suite documents is not happening"
    )


async def test_this_repositorys_own_pool_declares_no_lock_or_statement_timeout(
    inbox: PostgresAmhInbox,
) -> None:
    """Disclosure, not a defence: the unbounded wait above is a property the DBA has to decide on.

    Read through the repository's OWN pool (`setup=` runs on every acquire), so this is the session
    a `record` call actually runs in — not a fresh connection that merely shares the server default.
    Asserted rather than described so that setting a timeout becomes a deliberate change to this
    test and to packet decision D-6, instead of a behaviour change nobody reviewed.
    """
    pool = await inbox._ensure_pool()  # noqa: SLF001 - the session under test IS the pool's
    async with pool.acquire() as conn:
        lock_timeout = await conn.fetchval("SHOW lock_timeout")
        statement_timeout = await conn.fetchval("SHOW statement_timeout")
    assert lock_timeout == "0", f"lock_timeout is {lock_timeout!r} — update packet D-6"
    assert statement_timeout == "0", f"statement_timeout is {statement_timeout!r} — update packet D-6"


# ===========================================================================
# 4. Monotonic lifecycle, enforced by the database
# ===========================================================================


async def test_settlement_is_idempotent_and_terminal(inbox: PostgresAmhInbox) -> None:
    await inbox.record(_envelope("evt-settle"), stream=InboxStream.WORK_ITEM)
    key = {"contract_manifest_digest": _DIGEST, "event_id": "evt-settle"}

    assert await inbox.mark_processed(**key) is InboxSettleOutcome.APPLIED
    assert await inbox.mark_processed(**key) is InboxSettleOutcome.ALREADY
    assert await inbox.mark_settled(**key) is InboxSettleOutcome.APPLIED
    # THE `ack` idempotency guarantee, durably: a second ack of the same handle succeeds again.
    assert await inbox.mark_settled(**key) is InboxSettleOutcome.ALREADY
    # And a settled row is never rewritten into a quarantine.
    refused = await inbox.mark_quarantined(**key, reason=PortFailureReason.CONTRACT_VIOLATION)
    assert refused.outcome is InboxSettleOutcome.REFUSED_TERMINAL
    # The biconditional CHECK keeps a SETTLED row's quarantine columns NULL, so there is no stored
    # referral to report — and none is invented.
    assert refused.stored_reason is None
    assert refused.reason_diverged is False


async def test_quarantine_is_terminal_and_never_becomes_a_settlement(inbox: PostgresAmhInbox) -> None:
    await inbox.record(_envelope("evt-quar"), stream=InboxStream.CONSENT)
    key = {"contract_manifest_digest": _DIGEST, "event_id": "evt-quar"}

    applied = await inbox.mark_quarantined(
        **key,
        reason=PortFailureReason.CONTRACT_VIOLATION,
        quarantine_topic="amh.maezo.consent.v1.quarantine.v1",
    )
    assert applied.outcome is InboxSettleOutcome.APPLIED
    assert applied.stored_reason == "contract_violation"
    assert applied.stored_topic == "amh.maezo.consent.v1.quarantine.v1"

    again = await inbox.mark_quarantined(**key, reason=PortFailureReason.CONTRACT_VIOLATION)
    assert again.outcome is InboxSettleOutcome.ALREADY
    assert again.reason_diverged is False

    # The invariant that matters: a quarantined event can never come back as a settlement, so
    # `ack` can never report success for something that was routed to quarantine.
    assert await inbox.mark_settled(**key) is InboxSettleOutcome.REFUSED_TERMINAL


async def test_a_second_nack_with_a_divergent_reason_is_reported_not_dropped(
    inbox: PostgresAmhInbox, pg_dsn: str, tenant_schema: str
) -> None:
    """Quarantine is terminal, so the second reason is DISCARDED — the bug was not saying so.

    Two `nack`s classifying the same event differently is a real disagreement between deliveries
    (or between replicas). Returning bare `ALREADY` told the caller its `TIMEOUT` had been applied
    when `CONTRACT_VIOLATION` was on the row — a retryable classification silently standing in for
    an unretryable one.
    """
    await inbox.record(_envelope("evt-quar-diverge"), stream=InboxStream.WORK_ITEM)
    key = {"contract_manifest_digest": _DIGEST, "event_id": "evt-quar-diverge"}

    first = await inbox.mark_quarantined(
        **key,
        reason=PortFailureReason.CONTRACT_VIOLATION,
        quarantine_topic="amh.maezo.work_item.v1.quarantine.v1",
    )
    assert first.outcome is InboxSettleOutcome.APPLIED

    second = await inbox.mark_quarantined(
        **key, reason=PortFailureReason.TIMEOUT, quarantine_topic="somewhere.else"
    )
    assert second.outcome is InboxSettleOutcome.ALREADY
    assert second.requested_reason is PortFailureReason.TIMEOUT
    assert second.stored_reason == "contract_violation", "the STORED reason must be reported back"
    assert second.stored_topic == "amh.maezo.work_item.v1.quarantine.v1"
    assert second.reason_diverged is True

    # And the row really was not rewritten — read it back out of Postgres.
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        row = await conn.fetchrow(
            "SELECT quarantine_reason, quarantine_topic FROM amh_inbox WHERE event_id = $1",
            "evt-quar-diverge",
        )
    finally:
        await conn.close()
    assert row["quarantine_reason"] == "contract_violation"
    assert row["quarantine_topic"] == "amh.maezo.work_item.v1.quarantine.v1"


async def test_quarantining_an_unrecorded_event_is_not_found(inbox: PostgresAmhInbox) -> None:
    """Never an upsert, and nothing is invented to report."""
    result = await inbox.mark_quarantined(
        contract_manifest_digest=_DIGEST,
        event_id="evt-quar-ghost",
        reason=PortFailureReason.TIMEOUT,
    )
    assert result.outcome is InboxSettleOutcome.NOT_FOUND
    assert result.stored_reason is None
    assert result.reason_diverged is False


async def test_settling_an_unrecorded_event_is_not_found_never_an_upsert(
    inbox: PostgresAmhInbox,
) -> None:
    outcome = await inbox.mark_settled(contract_manifest_digest=_DIGEST, event_id="evt-ghost")
    assert outcome is InboxSettleOutcome.NOT_FOUND
    assert await inbox.is_duplicate(contract_manifest_digest=_DIGEST, event_id="evt-ghost") is False


# ===========================================================================
# 5. Pending scan
# ===========================================================================


async def test_pending_returns_only_non_terminal_rows_for_the_tenant(inbox: PostgresAmhInbox) -> None:
    await inbox.record(_envelope("evt-pend-1"), stream=InboxStream.WORK_ITEM)
    await inbox.record(_envelope("evt-pend-2"), stream=InboxStream.WORK_ITEM)
    await inbox.record(_envelope("evt-pend-other", amh_tenant="amh-tenant-z"), stream=InboxStream.WORK_ITEM)
    await inbox.mark_settled(contract_manifest_digest=_DIGEST, event_id="evt-pend-1")

    ids = {entry.event_id for entry in await inbox.pending(amh_tenant="amh-tenant-a")}
    assert "evt-pend-2" in ids
    assert "evt-pend-1" not in ids, "a settled row must leave the pending scan"
    assert "evt-pend-other" not in ids, "the scan must be scoped to one amh_tenant"

    with pytest.raises(AmhInboxUnavailableError):
        await inbox.pending(amh_tenant="amh-tenant-a", limit=0)


def test_pending_scan_uses_the_partial_index(pg_dsn: str, tenant_schema: str) -> None:
    """An index nothing plans against is write amplification with no read benefit."""

    async def _plan() -> str:
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(f'SET search_path TO "{tenant_schema}"')
            rows = await conn.fetch(
                "EXPLAIN SELECT contract_manifest_digest, event_id FROM amh_inbox "
                "WHERE amh_tenant = 'amh-tenant-a' AND status IN ('RECEIVED','PROCESSED') "
                "ORDER BY received_at ASC LIMIT 100"
            )
        finally:
            await conn.close()
        return "\n".join(row[0] for row in rows)

    assert "ix_amh_inbox_pending" in asyncio.run(_plan())


# ===========================================================================
# 6. PHI never reaches a persisted row
# ===========================================================================


async def test_no_phi_sentinel_survives_into_any_stored_column(
    inbox: PostgresAmhInbox, pg_dsn: str, tenant_schema: str
) -> None:
    """Read back from POSTGRES, not from bind parameters — the fence proven where it counts."""
    await inbox.record(_envelope("evt-phi"), stream=InboxStream.WORK_ITEM)

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        rows = await conn.fetch("SELECT * FROM amh_inbox")
        assert rows, "non-vacuity: nothing was persisted, so nothing was checked"
        rendered = " | ".join(str(value) for row in rows for value in row.values())
        assert _PHI_SENTINEL not in rendered
        # And the positive half: the row really is there, under its dedup key.
        assert "evt-phi" in rendered
    finally:
        await conn.close()


# ===========================================================================
# 7. The CHECK constraints defend against writers that are not this repository
# ===========================================================================


@pytest.mark.parametrize(
    ("label", "sql", "constraint"),
    [
        (
            "free-text quarantine reason",
            "UPDATE amh_inbox SET status='QUARANTINED', quarantined_at=now(), "
            "quarantine_reason='because I said so' WHERE event_id=$1",
            "ck_amh_inbox_quarantine_reason_vocabulary",
        ),
        (
            "settled with no settled_at",
            "UPDATE amh_inbox SET status='SETTLED' WHERE event_id=$1",
            "ck_amh_inbox_settled_at",
        ),
        (
            "settled_at with no SETTLED status",
            "UPDATE amh_inbox SET settled_at=now() WHERE event_id=$1",
            "ck_amh_inbox_settled_at",
        ),
        (
            "unknown status",
            "UPDATE amh_inbox SET status='DONE' WHERE event_id=$1",
            "ck_amh_inbox_status",
        ),
        (
            "unknown inbox_stream",
            "UPDATE amh_inbox SET inbox_stream='outcome' WHERE event_id=$1",
            "ck_amh_inbox_stream",
        ),
        (
            "negative replay_count",
            "UPDATE amh_inbox SET replay_count=-1 WHERE event_id=$1",
            "ck_amh_inbox_replay_count",
        ),
    ],
)
async def test_out_of_band_write_is_rejected_by_a_named_check(
    inbox: PostgresAmhInbox, pg_dsn: str, tenant_schema: str, label: str, sql: str, constraint: str
) -> None:
    event_id = f"evt-check-{constraint}-{label.replace(' ', '-')}"
    await inbox.record(_envelope(event_id), stream=InboxStream.WORK_ITEM)

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        with pytest.raises(asyncpg.exceptions.CheckViolationError) as excinfo:
            await conn.execute(sql, event_id)
        assert excinfo.value.constraint_name == constraint
    finally:
        await conn.close()
