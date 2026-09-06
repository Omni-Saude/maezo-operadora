"""LIVE-Postgres tamper/restore drills for the external audit anchor (Onda 4, leg 3 of 3).

WHAT THIS PROVES THAT THE LEG-2 UNIT SUITE (fakes only) COULD NOT
=================================================================================================
`test_audit_anchor_verify.py` drives `verify_latest_anchor` through an in-memory
`_RowRecordSource` (rows in a Python list). That pins every branch of the decision tree, but the
row->record->recompute path it exercises is a *reconstruction* of what Postgres does. THIS suite
runs the SAME `verify_latest_anchor` against the REAL `PostgresChainRecordSource` reading a REAL
`audit_chain` table, seeded through the REAL `PostgresAuditSink.emit()`, anchored by leg 1's REAL
`write_anchor`, and then TAMPERED with raw SQL against the live database. Each drill is the audit
§4 / brief-§3 threat made real against a live DB, and each proves the anchor comparison reaches the
correct LOUD outcome — with a CONTROL (untampered) proving MATCH first, so a drill that only ever
goes red is impossible (a verifier that always alarms detects nothing).

THE SEVEN DRILLS AND THE GUARD THAT FIRES (observed live, hardcoded with provenance)
=================================================================================================
Every expected outcome below was observed against a live pinned `pgvector/pgvector:pg16` and is
asserted, not described. The row->record path RECOMPUTES `record_hash` from stored content
(`audit_postgres._row_to_record` -> `AuditRecord.__post_init__`), while `snapshot_from_rows` walks
the STORED links — the asymmetry that turns a lazy content edit into a detected divergence.

  1. ALTER (middle) — edit `decision_basis`, leave `record_hash` stale. The recomputed hash of the
     edited row no longer equals the STORED hash the next row's `prev_record_hash` points at, so
     `checkpoint_for_chain`'s contiguity precondition fails => DIVERGENCE / CHAIN_DISCONTINUITY.
     (The in-DB `verify_chain` ALSO catches this, as a hash mismatch — both guards fire.)
  2. ALTER (head) — same edit on the tail row. There is no downstream link to break, so the walk
     is intact and only the recomputed chain HEAD changes => DIVERGENCE / ROOT_MISMATCH.
  3. REMOVE (tail) — delete the newest record. The remaining rows are still perfectly hash-linked,
     so the in-DB `verify_chain` calls the truncated chain VALID; the anchor is the ONLY thing that
     sees the missing record => DIVERGENCE / RECORD_COUNT_SHORTFALL. This is the cheapest tamper.
  4. FORK — two rows sharing a `prev_record_hash`. Which layer catches it is asserted PRECISELY:
     with `UNIQUE(prev_record_hash)` (migration 0002) present the DB REFUSES the second insert
     (`UniqueViolationError` on `uq_audit_chain_prev_hash`) — the DB-level anti-fork guard. With
     that constraint dropped (a fork that bypassed it), the verify job's structural walk detects the
     duplicate predecessor => DIVERGENCE / CHAIN_FORK. Both guards are proven.
  5. RECONSTRUCT — the headline audit-§4 adversary: a wholesale, self-consistent rewrite. Every
     record_hash and link is recomputed by the real sink, so `verify_chain` declares the forged
     chain VALID (asserted FIRST — the non-vacuity bar: the DB-only check is genuinely fooled). The
     EXTERNAL anchor, signed and sealed before the rewrite, is the only thing that disagrees =>
     DIVERGENCE / ROOT_MISMATCH. That comparison is the entire security value of the anchor.
  6. THIN THE LEDGER (Unit B) — the first drill whose target is the EVIDENCE, not the database.
     Three chained anchors; the MIDDLE one is deleted from the store. The database stays honest and
     the object is genuinely gone (not hidden), so neither the content checks nor the corroborating
     probe fire. The surviving anchor's signed `prev_anchor_root` is the only witness =>
     ANCHOR_CHAIN_BROKEN / ANCHOR_CHAIN_LINK_MISSING, and no verdict about the database.
  7. SCHEMA DRIFT (G1b) — the live `<tenant>_alembic_version` row is moved after the anchor was
     sealed. Content is untouched, so both roots still agree; what no longer agrees is the schema
     the attestation was made under => SCHEMA_VERSION_MISMATCH. This is the only proof that the job
     reads the RIGHT table: the per-tenant version table's name and schema placement come from
     `platform/migrations/env.py`, and no fixture can stand in for that.

LIVE-PG ISOLATION / SKIP-LOUDLY (why this file never depends on an ad-hoc container)
=================================================================================================
Skip mechanism mirrors `tests/unit/a2a/test_a2a_edge_live_pg.py`: connect with a 2s timeout and
`pytest.skip` LOUDLY if the server is unreachable, so an environment without Postgres skips
cleanly and visibly. Isolation is per-TENANT-SCHEMA, not per-server: each drill gets its OWN
freshly-migrated `w4drill<hex>` schema (function-scoped, because every drill mutates the chain
destructively), dropped on teardown — that is what keeps concurrent suites off each other's rows.

GAP LIVE-SUITES-SILENT-SKIP-AUDIT (2026-09-04). The default DSN used to target a "DEDICATED,
NON-STANDARD port (5466) — deliberately NOT the compose stack's 5433/5432 nor the sibling live-PG
suites' 5643", on the theory that an unserved port stops another suite attacking a half-ready DB.
Nothing in this repo ever served 5466 — no compose service, no CI service, no documented bring-up
— so the real effect was that all 9 drills below reported "COULD NOT VERIFY" in every environment
that has ever run them, including CI: a default nobody serves is a silent skip, not a proof, and
the anchor's entire security value is the drills actually executing. The default is now the
compose Postgres (`${MAEZO_PG_HOST_PORT:-5433}`, `docker-compose.yml`'s own published port, the
one CI's lanes pin to 5432); `MAEZO_TEST_AUDIT_ANCHOR_DRILL_DATABASE_URL` still overrides it for a
throwaway server. Measured on the isolated compose stack: `9 passed` against 5433.

The LABELED-FAKE signer/store are used with the writer/verifier flags ON only inside these drills:
they are the dev/test seams leg 1 ships (`FAKE-KMS-NAO-VINCULATIVO` / `LABELED-FAKE-WORM`), never a
real KMS key or a real WORM bucket — those are the owner's wiring.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import pytest

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_anchor import (
    ANCHOR_ENABLED_ENV,
    GENESIS_PREV_ANCHOR_ROOT,
    LabeledFakeKmsAnchorSigner,
    LabeledFakeWormAnchorStore,
    checkpoint_for_chain,
    write_anchor,
)
from maezo.gateway.audit_anchor_verify import (
    ANCHOR_VERIFY_ENABLED_ENV,
    DEFAULT_FAKE_SECRET_ENV,
    EXIT_CODE_BY_STATUS,
    REASON_ANCHOR_CHAIN_LINK_MISSING,
    REASON_CHAIN_DISCONTINUITY,
    REASON_CHAIN_FORK,
    REASON_RECORD_COUNT_SHORTFALL,
    REASON_ROOT_MISMATCH,
    REASON_SCHEMA_VERSION_MISMATCH,
    STATUS_ANCHOR_CHAIN_BROKEN,
    STATUS_DIVERGENCE,
    STATUS_MATCH,
    STATUS_SCHEMA_VERSION_MISMATCH,
    AnchorVerificationOutcome,
    FilesystemAnchorKeyProbe,
    PostgresChainRecordSource,
    _cli,
    verify_latest_anchor,
)
from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, verify_chain

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Compose Postgres by default (gap LIVE-SUITES-SILENT-SKIP-AUDIT — see the module docstring's
# isolation section); overridable for a throwaway server.
_DSN_ENV: Final[str] = "MAEZO_TEST_AUDIT_ANCHOR_DRILL_DATABASE_URL"
#: Compose Postgres host-port variable — `docker-compose.yml` publishes
#: `ports: ["${MAEZO_PG_HOST_PORT:-5433}:5432"]` and CI's integration/chaos lanes pin it to 5432.
_PORT_ENV: Final[str] = "MAEZO_PG_HOST_PORT"
_DEFAULT_PORT: Final[str] = "5433"

# The tenant's migration revision is deliberately NOT a constant here — `_seal_anchor_from_db`
# reads it LIVE from `<tenant>_alembic_version`. A hardcoded one would drift the moment a migration
# lands (this file used to pin `0005_audit_emit_dedup` while `head` had moved to 0008), and G1b's
# cross-check would then report SCHEMA_VERSION_MISMATCH on every CONTROL run.
_FAKE_SECRET: Final[bytes] = b"labeled-fake-anchor-drill-secret-nao-vinculativo"
_FAKE_KEY_LABEL: Final[str] = "leg3-drill"
_UNIQUE_CONSTRAINT: Final[str] = "uq_audit_chain_prev_hash"  # migration 0002 anti-fork guard


# =================================================================================================
# Skip-loudly plumbing (mirrors tests/unit/a2a/test_a2a_edge_live_pg.py)
# =================================================================================================


def _default_test_dsn() -> str:
    """`_DSN_ENV` wins; otherwise the compose Postgres (gap LIVE-SUITES-SILENT-SKIP-AUDIT).

    Mirrors `tests/integration/conftest.py::_audit_pg_dsn` exactly, so the default names a server
    that actually exists in both environments that run tests: the local compose stack
    (`${MAEZO_PG_HOST_PORT:-5433}`) and a CI job that pins `MAEZO_PG_HOST_PORT=5432`.
    """
    explicit = os.environ.get(_DSN_ENV)
    if explicit:
        return explicit
    port = os.environ.get(_PORT_ENV, _DEFAULT_PORT)
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


def _apply_migrations(dsn: str, tenant_id: str) -> None:
    """Apply the REAL alembic migrations 0001->0005 to `tenant_id`'s schema.

    Duplicated from `tests/integration/conftest.py` (not imported) so this suite stays
    self-contained and never depends on the CIB-Seven-gated `tests/integration/` package — same
    rationale as `test_a2a_edge_live_pg.py::_apply_migrations`.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "src" / "maezo" / "platform" / "migrations"))
    async_dsn = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    cfg.set_main_option("sqlalchemy.url", async_dsn)
    cfg.cmd_opts = argparse.Namespace(x=[f"tenant={tenant_id}"])  # env.py: -x tenant=<id>
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def pg_dsn() -> str:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"COULD NOT VERIFY: Postgres not reachable at {dsn!r} (override with {_DSN_ENV} "
            f"or {_PORT_ENV}). Bring the project's own stack up with "
            "`docker compose --profile core up -d postgres` and re-run this file; each drill "
            "migrates and drops its OWN tenant schema, so it never collides with another "
            "suite's rows — see the module docstring's isolation section."
        )
    return dsn


@pytest.fixture
def tenant_schema(pg_dsn: str) -> Iterator[str]:
    """A fresh, migrated tenant schema per test — every drill mutates the chain destructively."""
    tenant_id = f"w4drill{uuid.uuid4().hex[:12]}"  # [a-z][a-z0-9_]* per schema_for_tenant

    async def _create_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}"')
        finally:
            await conn.close()

    async def _drop_schema() -> None:
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_id}" CASCADE')
        finally:
            await conn.close()

    asyncio.run(_create_schema())
    _apply_migrations(pg_dsn, tenant_id)
    yield tenant_id
    asyncio.run(_drop_schema())


@pytest.fixture
def store(tmp_path: Path) -> LabeledFakeWormAnchorStore:
    return LabeledFakeWormAnchorStore(tmp_path / "anchors")


@pytest.fixture
def probe(tmp_path: Path) -> FilesystemAnchorKeyProbe:
    return FilesystemAnchorKeyProbe(tmp_path / "anchors")


@pytest.fixture
def anchors_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both dark-build flags ON: leg 1's writer (to SEAL fixtures) and leg 2's verifier."""
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "1")
    monkeypatch.setenv(ANCHOR_VERIFY_ENABLED_ENV, "1")


# =================================================================================================
# Seed / seal / verify / tamper helpers
# =================================================================================================


def _signer() -> LabeledFakeKmsAnchorSigner:
    return LabeledFakeKmsAnchorSigner(key_label=_FAKE_KEY_LABEL, secret=_FAKE_SECRET)


async def _seed_chain(dsn: str, tenant: str, count: int, *, marker: str = "ok") -> list[AuditRecord]:
    """Seed `count` real chain links through the REAL `PostgresAuditSink.emit()`."""
    sink = PostgresAuditSink(dsn, tenant)
    records: list[AuditRecord] = []
    try:
        for index in range(count):
            record = AuditRecord(
                agent_id="drill-fixture",
                tenant_id=tenant,
                agent_version="1.0.0",
                action="fixture.acao",
                decision="ALLOW",
                details={"i": index, "marker": marker},
                timestamp=datetime(2026, 3, 1, 12, index, 0, tzinfo=UTC),
            )
            await sink.emit(record)
            records.append(record)
    finally:
        await sink.aclose()
    return records


async def _seal_anchor_from_db(
    dsn: str,
    tenant: str,
    store: LabeledFakeWormAnchorStore,
    *,
    count: int | None = None,
    prev_anchor_root: str = GENESIS_PREV_ANCHOR_ROOT,
) -> tuple[str, str]:
    """Read the live chain and seal a REAL signed anchor over it via leg 1's `write_anchor`.

    Built from the DB round-trip (the SAME `PostgresChainRecordSource` the verify job reads) so the
    control is a genuine MATCH: both sides derive the checkpoint from identical rows.

    Two things are read LIVE rather than hardcoded, and both are load-bearing:

      - `chain_schema_version` comes from the snapshot's `schema_version` — the tenant's actual
        `<tenant>_alembic_version.version_num` — not from a constant in this file. G1b's cross-check
        compares the SIGNED value against that live one, so a writer that sealed a descriptive label
        instead of the live revision would make every control run report SCHEMA_VERSION_MISMATCH.
        Sealing what the database actually says is exactly the owner behaviour leg 1's
        `AnchorCheckpoint` docstring prescribes, and this drill is where it becomes binding.
      - `prev_anchor_root` is REQUIRED by leg 1 (never defaulted, so an omitted link cannot
        masquerade as a fresh genesis), so a caller sealing a SECOND anchor must pass its
        predecessor's root here.

    Returns `(anchor_key, root)` so a caller can chain the next anchor onto this one.
    """
    snapshot = await PostgresChainRecordSource(dsn).read_chain(tenant)
    assert snapshot.schema_version is not None, "the live tenant schema reported no alembic revision"
    window = snapshot.records if count is None else snapshot.records[:count]
    checkpoint = checkpoint_for_chain(
        window,
        tenant_id=tenant,
        chain_schema_version=snapshot.schema_version,
        prev_anchor_root=prev_anchor_root,
    )
    outcome = write_anchor(checkpoint, signer=_signer(), store=store)
    assert outcome.written is True
    assert outcome.anchor_key is not None
    assert outcome.root is not None
    return outcome.anchor_key, outcome.root


async def _verify(
    dsn: str,
    tenant: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> AnchorVerificationOutcome:
    return await verify_latest_anchor(
        tenant_id=tenant,
        store=store,
        verifier=_signer(),
        key_probe=probe,
        records=PostgresChainRecordSource(dsn),
    )


def _delete_anchor(store: LabeledFakeWormAnchorStore, key: str) -> None:
    """Remove a stored anchor from BOTH enumerations — the WORM/retention lock having failed.

    A real unlink, not a listing filter: an anchor merely HIDDEN from the listing is the scenario
    the corroborating probe already catches. Sync (not `async`) on purpose — blocking filesystem
    calls inside a coroutine are what `ASYNC240` exists to stop, and this needs no event loop.
    """
    path = Path(store.root, *key.split("/"))
    path.chmod(0o644)  # the fake store chmods anchors 0o444; the owner may still remove them
    path.unlink()


async def _exec(dsn: str, tenant: str, sql: str, *args: Any) -> str:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant}"')
        return await conn.execute(sql, *args)
    finally:
        await conn.close()


_INSERT_ROW_SQL: Final[str] = (
    "INSERT INTO audit_chain "
    "(timestamp, tenant_id, agent_id, agent_version, action, decision, input_hash, "
    "record_hash, prev_record_hash) "
    "VALUES (now(), $1, 'forker', '1.0.0', 'fork.acao', 'ALLOW', $2, $3, $4)"
)


# =================================================================================================
# The drills
# =================================================================================================


async def test_control_untampered_chain_matches_the_anchor(
    anchors_enabled: None,
    pg_dsn: str,
    tenant_schema: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """CONTROL: the drills can say green. A live, honest chain, anchored, verifies to MATCH — the
    prerequisite that makes every DIVERGENCE below meaningful rather than a constant alarm."""
    records = await _seed_chain(pg_dsn, tenant_schema, 5)
    key, _root = await _seal_anchor_from_db(pg_dsn, tenant_schema, store)

    outcome = await _verify(pg_dsn, tenant_schema, store, probe)

    assert outcome.status == STATUS_MATCH
    assert outcome.reason is None
    assert outcome.is_clean is True
    assert outcome.anchor_key == key
    assert outcome.anchor_root == outcome.database_root
    assert outcome.anchored_record_count == outcome.database_record_count == 5
    assert outcome.database_head_hash == records[-1].record_hash
    assert outcome.signature_key_id == f"FAKE-KMS-NAO-VINCULATIVO:{_FAKE_KEY_LABEL}"

    # And the in-DB verifier agrees the honest chain is internally consistent.
    chain = await verify_chain(pg_dsn, tenant_schema)
    assert chain.valid is True
    assert chain.total_records == chain.verified_records == 5


async def test_alter_middle_record_is_a_chain_discontinuity(
    anchors_enabled: None,
    pg_dsn: str,
    tenant_schema: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """ALTER, middle position. Edit `decision_basis` of a middle row and leave `record_hash` stale.
    The recomputed hash of the edited row diverges from the STORED hash the next row points at, so
    the contiguity precondition fails => DIVERGENCE / CHAIN_DISCONTINUITY."""
    records = await _seed_chain(pg_dsn, tenant_schema, 5)
    await _seal_anchor_from_db(pg_dsn, tenant_schema, store)

    # CONTROL (same setup, no tamper): the anchor verifies clean.
    assert (await _verify(pg_dsn, tenant_schema, store, probe)).status == STATUS_MATCH

    middle_hash = records[2].record_hash
    await _exec(
        pg_dsn,
        tenant_schema,
        "UPDATE audit_chain SET decision_basis = $1::jsonb WHERE record_hash = $2",
        json.dumps({"i": 2, "marker": "EDITED-WITHOUT-REHASHING"}),
        middle_hash,
    )

    outcome = await _verify(pg_dsn, tenant_schema, store, probe)
    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_CHAIN_DISCONTINUITY

    # The in-DB verifier catches the same lazy edit as a hash mismatch — both guards fire.
    chain = await verify_chain(pg_dsn, tenant_schema)
    assert chain.valid is False


async def test_alter_head_record_is_a_root_mismatch(
    anchors_enabled: None,
    pg_dsn: str,
    tenant_schema: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """ALTER, head position. Editing the tail row breaks no downstream link (nothing follows it),
    so the walk stays intact and only the recomputed chain HEAD changes => DIVERGENCE /
    ROOT_MISMATCH, with both roots and heads reported (hashes only, never content)."""
    records = await _seed_chain(pg_dsn, tenant_schema, 5)
    await _seal_anchor_from_db(pg_dsn, tenant_schema, store)

    assert (await _verify(pg_dsn, tenant_schema, store, probe)).status == STATUS_MATCH

    head_hash = records[-1].record_hash
    await _exec(
        pg_dsn,
        tenant_schema,
        "UPDATE audit_chain SET decision_basis = $1::jsonb WHERE record_hash = $2",
        json.dumps({"i": 4, "marker": "HEAD-EDITED-WITHOUT-REHASHING"}),
        head_hash,
    )

    outcome = await _verify(pg_dsn, tenant_schema, store, probe)
    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_ROOT_MISMATCH
    assert outcome.anchored_record_count == outcome.database_record_count == 5
    assert outcome.anchor_root != outcome.database_root
    assert outcome.anchored_head_hash != outcome.database_head_hash


async def test_remove_tail_record_is_a_shortfall_the_in_db_verifier_calls_valid(
    anchors_enabled: None,
    pg_dsn: str,
    tenant_schema: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """REMOVE the tail. The remaining rows are still perfectly hash-linked, so the in-DB
    `verify_chain` calls the truncated chain VALID — deletion is the one tamper an in-DB verifier
    cannot see at all. The external anchor catches it => DIVERGENCE / RECORD_COUNT_SHORTFALL."""
    records = await _seed_chain(pg_dsn, tenant_schema, 5)
    await _seal_anchor_from_db(pg_dsn, tenant_schema, store)

    assert (await _verify(pg_dsn, tenant_schema, store, probe)).status == STATUS_MATCH

    await _exec(
        pg_dsn, tenant_schema, "DELETE FROM audit_chain WHERE record_hash = $1", records[-1].record_hash
    )

    outcome = await _verify(pg_dsn, tenant_schema, store, probe)
    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_RECORD_COUNT_SHORTFALL
    assert outcome.anchored_record_count == 5
    assert outcome.database_record_count == 4
    assert outcome.database_root is None  # there is no full window to recompute a root over

    # The whole point: the in-DB verifier is blind to a truncation — the anchor is the only catch.
    chain = await verify_chain(pg_dsn, tenant_schema)
    assert chain.valid is True
    assert chain.total_records == chain.verified_records == 4


async def test_fork_is_refused_by_the_unique_constraint_then_caught_by_the_verify_job(
    anchors_enabled: None,
    pg_dsn: str,
    tenant_schema: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """FORK, both guards, asserted PRECISELY.

    Part 1 — the DB-level anti-fork guard. `UNIQUE(prev_record_hash)` (migration 0002) REFUSES a
    second row that claims an already-used predecessor: the insert raises `UniqueViolationError` on
    `uq_audit_chain_prev_hash`. The fork never reaches the table.

    Part 2 — the verify-job guard. Drop that constraint (a fork that BYPASSED the DB guard) and the
    insert succeeds; the verify job's structural walk sees two rows sharing a `prev_record_hash`
    and refuses to pick an arm => DIVERGENCE / CHAIN_FORK.
    """
    records = await _seed_chain(pg_dsn, tenant_schema, 5)
    await _seal_anchor_from_db(pg_dsn, tenant_schema, store)

    assert (await _verify(pg_dsn, tenant_schema, store, probe)).status == STATUS_MATCH

    forking_prev = records[2].prev_hash  # already used by records[2]

    # Part 1: the DB refuses the fork at write time.
    with pytest.raises(asyncpg.UniqueViolationError) as excinfo:
        await _exec(pg_dsn, tenant_schema, _INSERT_ROW_SQL, tenant_schema, "a" * 64, "f" * 64, forking_prev)
    assert excinfo.value.constraint_name == _UNIQUE_CONSTRAINT

    # Part 2: bypass the DB guard, then the verify job catches the fork.
    await _exec(pg_dsn, tenant_schema, f"ALTER TABLE audit_chain DROP CONSTRAINT {_UNIQUE_CONSTRAINT}")
    await _exec(pg_dsn, tenant_schema, _INSERT_ROW_SQL, tenant_schema, "a" * 64, "f" * 64, forking_prev)

    outcome = await _verify(pg_dsn, tenant_schema, store, probe)
    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_CHAIN_FORK
    assert outcome.database_head_hash == forking_prev  # the shared predecessor, reported


async def test_reconstructed_chain_is_valid_in_db_but_caught_by_the_anchor(
    anchors_enabled: None,
    pg_dsn: str,
    tenant_schema: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """RECONSTRUCT — the headline audit-§4 adversary, end-to-end against a live DB.

    The chain is wholesale rewritten and re-hashed by the REAL sink, so it is internally
    self-consistent. `verify_chain` declares the forgery VALID — asserted FIRST, the non-vacuity
    bar: unless the DB-only check is genuinely fooled, catching it afterward proves nothing. The
    EXTERNAL anchor, sealed before the rewrite, is the only thing that disagrees => DIVERGENCE /
    ROOT_MISMATCH. That is the entire security value of external anchoring."""
    original = await _seed_chain(pg_dsn, tenant_schema, 5)
    await _seal_anchor_from_db(pg_dsn, tenant_schema, store)

    assert (await _verify(pg_dsn, tenant_schema, store, probe)).status == STATUS_MATCH

    # Privileged rewrite: same count, different content, all hashes recomputed by the real sink.
    await _exec(pg_dsn, tenant_schema, "TRUNCATE audit_chain")
    forged = await _seed_chain(pg_dsn, tenant_schema, 5, marker="REWRITTEN-BY-A-PRIVILEGED-ACTOR")
    assert forged[-1].record_hash != original[-1].record_hash

    # FIRST: the in-DB verifier is fooled — the forgery is internally valid.
    chain = await verify_chain(pg_dsn, tenant_schema)
    assert chain.valid is True
    assert chain.total_records == chain.verified_records == 5

    # THEN: the external anchor catches what the in-DB verifier cannot.
    outcome = await _verify(pg_dsn, tenant_schema, store, probe)
    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_ROOT_MISMATCH
    assert outcome.anchored_head_hash == original[-1].record_hash
    assert outcome.database_head_hash == forged[-1].record_hash
    assert outcome.anchor_root != outcome.database_root
    assert outcome.anchored_record_count == outcome.database_record_count == 5


async def test_a_historical_anchor_removed_from_the_store_is_caught_by_the_in_band_chain(
    anchors_enabled: None,
    pg_dsn: str,
    tenant_schema: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """THIN THE LEDGER (Unit B) — the tamper that targets the EVIDENCE rather than the database.

    Three anchors are sealed over a growing live chain, each committing to its predecessor's root.
    The retention lock then fails (or was never real) and the MIDDLE anchor is removed from the
    store. Nothing else is touched: the database is honest, so every content check passes, and the
    object is genuinely gone rather than hidden, so the listing and the corroborating probe agree
    and V3's defense has nothing to fire on.

    The only surviving witness that T2 ever existed is T3's SIGNED `prev_anchor_root`
    => ANCHOR_CHAIN_BROKEN / ANCHOR_CHAIN_LINK_MISSING, and NO verdict about the database.
    """
    await _seed_chain(pg_dsn, tenant_schema, 6)
    first_key, first_root = await _seal_anchor_from_db(pg_dsn, tenant_schema, store, count=2)
    middle_key, middle_root = await _seal_anchor_from_db(
        pg_dsn, tenant_schema, store, count=4, prev_anchor_root=first_root
    )
    tip_key, _tip_root = await _seal_anchor_from_db(
        pg_dsn, tenant_schema, store, prev_anchor_root=middle_root
    )

    # CONTROL: the whole chain verifies clean, against the TIP (structural selection).
    control = await _verify(pg_dsn, tenant_schema, store, probe)
    assert control.status == STATUS_MATCH
    assert control.anchor_key == tip_key
    assert control.anchored_record_count == 6

    _delete_anchor(store, middle_key)

    # Both enumerations agree the object is gone — the corroboration defense CANNOT fire here.
    assert middle_key not in store.list_keys()
    assert middle_key not in probe.probe_keys(tenant_schema)
    assert set(store.list_keys()) == set(probe.probe_keys(tenant_schema)) == {first_key, tip_key}

    outcome = await _verify(pg_dsn, tenant_schema, store, probe)
    assert outcome.status == STATUS_ANCHOR_CHAIN_BROKEN
    assert outcome.reason == REASON_ANCHOR_CHAIN_LINK_MISSING
    assert outcome.anchor_chain_break_keys == (tip_key,)
    assert outcome.listing_disagreement == ()  # V3 really did stay silent
    assert outcome.database_root is None  # no verdict about the database was issued
    assert outcome.is_clean is False

    # And the database was, throughout, entirely honest — this is an EVIDENCE incident.
    chain = await verify_chain(pg_dsn, tenant_schema)
    assert chain.valid is True
    assert chain.total_records == chain.verified_records == 6


async def test_a_migration_after_the_anchor_is_surfaced_against_the_live_alembic_version(
    anchors_enabled: None,
    pg_dsn: str,
    tenant_schema: str,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """SCHEMA DRIFT (G1b) — the one drill that binds the cross-check to a REAL `alembic_version`.

    The unit suite can only prove the comparison; it cannot prove the job reads the right table.
    Alembic's per-tenant version table is named by `platform/migrations/env.py`
    (`version_table=f"{TENANT_ID}_alembic_version"`) and lives inside the tenant schema because that
    env migrates under `search_path = "<tenant>", "public"` — none of which is visible from a
    fixture. Here the anchor is sealed against whatever `head` actually is, then the LIVE revision
    row is moved, and the job must notice.

    The chain CONTENT is untouched throughout, so the roots still agree: this is a stale-anchor
    finding (re-anchor), not a tamper => SCHEMA_VERSION_MISMATCH.
    """
    await _seed_chain(pg_dsn, tenant_schema, 5)
    await _seal_anchor_from_db(pg_dsn, tenant_schema, store)

    live_before = (await PostgresChainRecordSource(pg_dsn).read_chain(tenant_schema)).schema_version
    assert live_before is not None  # the drill would be vacuous against a schema with no revision

    control = await _verify(pg_dsn, tenant_schema, store, probe)
    assert control.status == STATUS_MATCH
    assert control.anchored_schema_version == control.database_schema_version == live_before

    # A migration the anchor never saw. Only the version ROW moves; no table is altered, so the
    # chain content — and therefore both roots — stay exactly as the anchor attests.
    await _exec(
        pg_dsn,
        tenant_schema,
        f'UPDATE "{tenant_schema}_alembic_version" SET version_num = $1',
        "0099_migracao_posterior",
    )

    outcome = await _verify(pg_dsn, tenant_schema, store, probe)
    assert outcome.status == STATUS_SCHEMA_VERSION_MISMATCH
    assert outcome.reason == REASON_SCHEMA_VERSION_MISMATCH
    assert outcome.anchored_schema_version == live_before
    assert outcome.database_schema_version == "0099_migracao_posterior"
    assert outcome.anchor_root == outcome.database_root  # content agreed; only the schema drifted
    assert outcome.is_clean is False
    assert outcome.exit_code == EXIT_CODE_BY_STATUS[STATUS_SCHEMA_VERSION_MISMATCH] == 18


# =================================================================================================
# End-to-end through leg 2's offline CLI — the §8 handoff: consume the JSON verdict + exit codes
# =================================================================================================


def test_cli_end_to_end_control_exit_zero_then_divergence_exit_ten(
    pg_dsn: str,
    tenant_schema: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The leg-2 CLI (`python -m maezo.gateway.audit_anchor_verify`) against the live DB: exit 0 +
    a `MATCH` last-line JSON verdict on the honest chain, then exit 10 + a `DIVERGENCE` verdict
    after removing the tail. Proves the drill also binds through the exact CLI contract §9.4 pins
    (verdict is the LAST line of stdout, one line, and the exit code encodes the outcome class)."""
    store_root = tmp_path / "cli-anchors"
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "1")
    monkeypatch.setenv(ANCHOR_VERIFY_ENABLED_ENV, "1")
    monkeypatch.setenv(DEFAULT_FAKE_SECRET_ENV, _FAKE_SECRET.decode("utf-8"))

    records = asyncio.run(_seed_chain(pg_dsn, tenant_schema, 5))
    asyncio.run(_seal_anchor_from_db(pg_dsn, tenant_schema, LabeledFakeWormAnchorStore(store_root)))

    argv = [
        "--dsn",
        pg_dsn,
        "--tenant",
        tenant_schema,
        "--store-root",
        str(store_root),
        "--fake-verifier-key-label",
        _FAKE_KEY_LABEL,
    ]

    # CONTROL: honest chain -> exit 0 (MATCH).
    control_code = _cli(argv)
    control_verdict = _last_json_line(capsys.readouterr().out)
    assert control_code == EXIT_CODE_BY_STATUS[STATUS_MATCH] == 0
    assert control_verdict["status"] == STATUS_MATCH

    # TAMPER: remove the tail, then the SAME CLI invocation -> exit 10 (DIVERGENCE / SHORTFALL).
    asyncio.run(
        _exec(
            pg_dsn, tenant_schema, "DELETE FROM audit_chain WHERE record_hash = $1", records[-1].record_hash
        )
    )
    divergence_code = _cli(argv)
    divergence_verdict = _last_json_line(capsys.readouterr().out)
    assert divergence_code == EXIT_CODE_BY_STATUS[STATUS_DIVERGENCE] == 10
    assert divergence_verdict["status"] == STATUS_DIVERGENCE
    assert divergence_verdict["reason"] == REASON_RECORD_COUNT_SHORTFALL


def _last_json_line(captured: str) -> dict[str, Any]:
    """Parse the CLI's verdict: the LAST non-empty line of stdout, one compact JSON object."""
    lines: Sequence[str] = [line for line in captured.splitlines() if line.strip()]
    assert lines, "the CLI produced no stdout"
    verdict: dict[str, Any] = json.loads(lines[-1])
    return verdict
