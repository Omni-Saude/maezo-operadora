"""Unit tests for the A2A fact-outbox RELAY — the at-least-once delivery contract, no broker/DB.

The relay's guarantee is a property of ORDER, not of Postgres: `drain_once` publishes and THEN
marks, so a crash in between costs a redelivery and never a fact. That is provable — and
NEUTERABLE — without a server, which is why it is proven here against an in-memory store
implementing the same lease semantics as the SQL. The SQL-level half (real lease expiry, real
transactional rollback) is `tests/unit/a2a/test_outbox_live_pg.py`, against the real 0008 DDL.

NEUTERING MAP (which test reds when a defense is removed):

  * swap `drain_once` to mark-THEN-publish  -> `test_a_crash_between_publish_and_mark_redelivers`
    and `test_redelivery_carries_an_identical_dedup_key` go RED (the row is sealed before the
    crash, so the fact is lost forever — at-most-once).
  * stop guarding `mark_delivered` on `claimed_by` -> `test_a_stolen_lease_cannot_be_sealed_by_the
    _previous_owner` goes RED.
  * let `drain_once` continue past a publish failure -> `test_a_publish_failure_stops_the_batch_
    and_preserves_order` goes RED.
  * drop either fail-closed check in `build_relay` -> the matching
    `test_build_relay_refuses_without_*` goes RED.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from maezo.a2a import outbox_relay
from maezo.a2a.facts import DelegationFactKind, build_fact
from maezo.a2a.outbox import OutboxRecord, outbox_row_params
from maezo.a2a.outbox_relay import (
    DEFAULT_HEARTBEAT_PATH,
    MIN_HEARTBEAT_STALE_AFTER_S,
    AioKafkaFactPublisher,
    OutboxRelaySettings,
    _touch_heartbeat,
    build_arg_parser,
    build_relay,
    default_worker_id,
    drain_once,
    heartbeat_stale_after_s,
    run_relay_loop,
)


@pytest.fixture
def _reset_heartbeat_log_flag():
    """The once-per-process log guard `_touch_heartbeat` uses (gatekeeper finding G2) is module
    state, not per-call — reset it before AND after any test that exercises the failure path, so
    test order never lets one test's failure "use up" the once-only log another test expects."""
    outbox_relay._reset_heartbeat_write_failure_logged_for_tests()
    yield
    outbox_relay._reset_heartbeat_write_failure_logged_for_tests()


# ---------------------------------------------------------------------------
# In-memory doubles (never imported by production code)
# ---------------------------------------------------------------------------


@dataclass
class _Row:
    id: int
    tenant: str
    dedup_key: str
    topic: str
    partition_key: str | None
    payload: bytes
    status: str = "pending"
    attempts: int = 0
    claimed_by: str | None = None
    lease_until: float = 0.0
    last_error: str | None = None


@dataclass
class _MemoryOutbox:
    """In-memory `OutboxClaimStore` mirroring the SQL's lease semantics, with a VIRTUAL clock.

    `now` is advanced explicitly by the tests, so lease expiry is exercised deterministically
    instead of by sleeping. `fail_mark_delivered_once` simulates the process dying at the exact
    instant between a successful publish and the row being sealed.
    """

    rows: list[_Row] = field(default_factory=list)
    now: float = 0.0
    fail_mark_delivered_once: bool = False
    _next_id: int = 1

    def enqueue(self, topic: str, value: bytes, key: bytes | None = None) -> int:
        tenant, dedup_key, topic_value, partition_key, payload = outbox_row_params(topic, value, key)
        row = _Row(
            id=self._next_id,
            tenant=tenant,
            dedup_key=dedup_key,
            topic=topic_value,
            partition_key=partition_key,
            payload=payload,
        )
        self._next_id += 1
        self.rows.append(row)
        return row.id

    async def claim_batch(
        self, *, claimed_by: str, batch_size: int = 100, claim_ttl_s: float = 60.0
    ) -> list[OutboxRecord]:
        claimable = [
            row
            for row in self.rows
            if row.status == "pending" or (row.status == "claimed" and row.lease_until <= self.now)
        ]
        claimed: list[OutboxRecord] = []
        for row in claimable[:batch_size]:
            row.status = "claimed"
            row.claimed_by = claimed_by
            row.lease_until = self.now + claim_ttl_s
            row.attempts += 1
            claimed.append(
                OutboxRecord(
                    id=row.id,
                    tenant=row.tenant,
                    dedup_key=row.dedup_key,
                    topic=row.topic,
                    partition_key=row.partition_key,
                    payload=row.payload,
                    attempts=row.attempts,
                )
            )
        return claimed

    async def mark_delivered(self, ids, *, claimed_by: str) -> int:  # type: ignore[no-untyped-def]
        if self.fail_mark_delivered_once:
            self.fail_mark_delivered_once = False
            raise _SimulatedCrashError("process died between publish and mark")
        sealed = 0
        for row in self.rows:
            if row.id in set(ids) and row.status == "claimed" and row.claimed_by == claimed_by:
                row.status = "delivered"
                row.claimed_by = None
                row.lease_until = 0.0
                sealed += 1
        return sealed

    async def mark_failed(self, ids, *, claimed_by: str, error: str) -> int:  # type: ignore[no-untyped-def]
        released = 0
        for row in self.rows:
            if row.id in set(ids) and row.status == "claimed" and row.claimed_by == claimed_by:
                row.status = "pending"
                row.claimed_by = None
                row.lease_until = 0.0
                row.last_error = error
                released += 1
        return released

    def statuses(self) -> list[str]:
        return [row.status for row in self.rows]


class _SimulatedCrashError(RuntimeError):
    """Stands in for the process dying — deliberately NOT an exception `drain_once` catches."""


class _RecordingPublisher:
    """In-memory `FactBrokerPublisher`. `fail_from_index` makes send raise from that row onward."""

    def __init__(self, *, fail_from_index: int | None = None) -> None:
        self.sent: list[tuple[str, bytes, bytes | None]] = []
        self.started = False
        self.stopped = False
        self._fail_from_index = fail_from_index

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        if self._fail_from_index is not None and len(self.sent) >= self._fail_from_index:
            raise ConnectionError("broker unreachable")
        self.sent.append((topic, value, key))

    def dedup_keys(self) -> list[str]:
        return [json.loads(v.decode())["kind"] for (_t, v, _k) in self.sent]


def _fact(kind: DelegationFactKind, task_id: str) -> bytes:
    return build_fact(
        kind,
        task_id=task_id,
        task_type="authorization.analyze",
        tenant="amh",
        origin="helena",
        target="rafael",
        delegation_chain=("helena", "rafael"),
    ).to_value()


def _seeded(count: int = 3) -> _MemoryOutbox:
    outbox = _MemoryOutbox()
    for i in range(count):
        outbox.enqueue("agents.events.delegation.requested", _fact(DelegationFactKind.REQUESTED, f"t{i}"))
    return outbox


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_drain_publishes_every_claimed_row_in_order_and_seals_them() -> None:
    outbox, publisher = _seeded(3), _RecordingPublisher()
    report = await drain_once(outbox, publisher, claimed_by="w1")

    assert (report.claimed, report.delivered, report.sealed) == (3, 3, 3)
    assert report.publish_error is None
    assert outbox.statuses() == ["delivered"] * 3
    assert [json.loads(v.decode())["task_id"] for (_t, v, _k) in publisher.sent] == ["t0", "t1", "t2"]


async def test_the_partition_key_survives_the_round_trip_to_the_broker() -> None:
    outbox, publisher = _MemoryOutbox(), _RecordingPublisher()
    outbox.enqueue("agents.events.delegation.requested", _fact(DelegationFactKind.REQUESTED, "t"), b"amh")
    await drain_once(outbox, publisher, claimed_by="w1")
    assert publisher.sent[0][2] == b"amh"


async def test_the_published_bytes_are_byte_identical_to_what_was_enqueued() -> None:
    """Verbatim delivery — the property `payload bytea` exists for (ADR-0039 signing)."""
    value = _fact(DelegationFactKind.COMPLETED, "t")
    outbox, publisher = _MemoryOutbox(), _RecordingPublisher()
    outbox.enqueue("agents.events.delegation.completed", value)
    await drain_once(outbox, publisher, claimed_by="w1")
    assert publisher.sent[0][1] == value


async def test_an_empty_outbox_is_a_no_op_sweep() -> None:
    report = await drain_once(_MemoryOutbox(), _RecordingPublisher(), claimed_by="w1")
    assert (report.claimed, report.delivered, report.sealed) == (0, 0, 0)


# ---------------------------------------------------------------------------
# AT-LEAST-ONCE — the crash window
# ---------------------------------------------------------------------------


async def test_a_crash_between_publish_and_mark_redelivers() -> None:
    """THE at-least-once proof.

    The publish SUCCEEDS, then the process dies before the row is sealed. The row stays `claimed`
    with a lease; once that lease expires it is claimable again by anyone, so the next sweep
    RE-PUBLISHES it. A duplicate on the wire is the intended cost; a lost fact is not an option.

    Neuter `drain_once` to mark-then-publish and this goes red: the row would already be sealed
    when the crash happens, and the fact would be gone.
    """
    outbox = _seeded(1)
    outbox.fail_mark_delivered_once = True
    publisher = _RecordingPublisher()

    with pytest.raises(_SimulatedCrashError):
        await drain_once(outbox, publisher, claimed_by="w1", claim_ttl_s=60.0)

    # Published once; NOT sealed — the row is still leased by the dead worker.
    assert len(publisher.sent) == 1
    assert outbox.statuses() == ["claimed"]

    # Before the lease expires, a second relay must NOT steal it (no thundering redelivery).
    outbox.now = 59.0
    early = await drain_once(outbox, publisher, claimed_by="w2")
    assert (early.claimed, early.delivered) == (0, 0)
    assert len(publisher.sent) == 1

    # After expiry: re-claimed and RE-PUBLISHED, with the attempt counter telling the story.
    outbox.now = 61.0
    report = await drain_once(outbox, publisher, claimed_by="w2")
    assert (report.claimed, report.delivered, report.sealed) == (1, 1, 1)
    assert len(publisher.sent) == 2
    assert outbox.statuses() == ["delivered"]
    assert outbox.rows[0].attempts == 2


async def test_redelivery_carries_an_identical_dedup_key() -> None:
    """What makes the duplicate harmless: the consumer can collapse it because the key is derived
    from the payload, not from the delivery."""
    outbox = _seeded(1)
    outbox.fail_mark_delivered_once = True
    publisher = _RecordingPublisher()
    with pytest.raises(_SimulatedCrashError):
        await drain_once(outbox, publisher, claimed_by="w1", claim_ttl_s=10.0)
    outbox.now = 11.0
    await drain_once(outbox, publisher, claimed_by="w2")

    assert len(publisher.sent) == 2
    first, second = publisher.sent[0][1], publisher.sent[1][1]
    assert first == second
    assert outbox.rows[0].dedup_key == "amh:a2a:delegate:t0:requested"


async def test_a_stolen_lease_cannot_be_sealed_by_the_previous_owner() -> None:
    """If a slow worker's lease expired and another relay re-claimed the row, the slow worker's
    mark must match nothing — otherwise it would seal a row the new owner is still publishing."""
    outbox = _seeded(1)
    await outbox.claim_batch(claimed_by="w1", claim_ttl_s=10.0)
    outbox.now = 11.0
    await outbox.claim_batch(claimed_by="w2", claim_ttl_s=10.0)

    assert await outbox.mark_delivered([1], claimed_by="w1") == 0
    assert outbox.statuses() == ["claimed"]
    assert await outbox.mark_delivered([1], claimed_by="w2") == 1


# ---------------------------------------------------------------------------
# Publish failure
# ---------------------------------------------------------------------------


async def test_a_publish_failure_stops_the_batch_and_preserves_order() -> None:
    """Row 0 published and sealed; rows 1-2 released to `pending` with `last_error`. Continuing
    past the failure would put later facts on the wire ahead of an earlier one."""
    outbox, publisher = _seeded(3), _RecordingPublisher(fail_from_index=1)
    report = await drain_once(outbox, publisher, claimed_by="w1")

    assert (report.claimed, report.delivered, report.sealed, report.released) == (3, 1, 1, 2)
    assert report.publish_error is not None and "ConnectionError" in report.publish_error
    assert outbox.statuses() == ["delivered", "pending", "pending"]
    assert outbox.rows[1].last_error is not None and "ConnectionError" in outbox.rows[1].last_error
    assert len(publisher.sent) == 1


async def test_the_next_sweep_retries_from_the_failed_row() -> None:
    outbox = _seeded(3)
    await drain_once(outbox, _RecordingPublisher(fail_from_index=1), claimed_by="w1")
    healthy = _RecordingPublisher()
    report = await drain_once(outbox, healthy, claimed_by="w1")
    assert (report.claimed, report.sealed) == (2, 2)
    assert [json.loads(v.decode())["task_id"] for (_t, v, _k) in healthy.sent] == ["t1", "t2"]
    assert outbox.statuses() == ["delivered"] * 3


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


async def test_the_loop_runs_exactly_max_sweeps() -> None:
    outbox, publisher = _seeded(2), _RecordingPublisher()
    reports = await run_relay_loop(outbox, publisher, claimed_by="w1", max_sweeps=2, poll_interval_s=0.0)
    assert len(reports) == 2
    assert (reports[0].sealed, reports[1].claimed) == (2, 0)


async def test_a_set_stop_event_ends_the_loop_without_a_sweep() -> None:
    outbox, publisher = _seeded(2), _RecordingPublisher()
    stop = asyncio.Event()
    stop.set()
    reports = await run_relay_loop(outbox, publisher, claimed_by="w1", stop_event=stop)
    assert reports == []
    assert outbox.statuses() == ["pending", "pending"]


async def test_a_database_error_propagates_out_of_the_loop() -> None:
    """Fail-closed, same posture as `notifications_bridge.run_consumer_loop`: only a PUBLISH
    failure is handled; anything else ends the process rather than looping on a broken store."""

    class _BrokenOutbox(_MemoryOutbox):
        async def claim_batch(self, **_kwargs: object) -> list[OutboxRecord]:
            raise ConnectionResetError("postgres went away")

    with pytest.raises(ConnectionResetError):
        await run_relay_loop(_BrokenOutbox(), _RecordingPublisher(), claimed_by="w1", max_sweeps=1)


# ---------------------------------------------------------------------------
# The heartbeat (SC-01/F3) — the livenessProbe's real signal, not `pgrep`
# ---------------------------------------------------------------------------


def test_touch_heartbeat_updates_mtime(tmp_path) -> None:
    path = tmp_path / "heartbeat"
    _touch_heartbeat(str(path))
    assert path.is_file()
    first_mtime = path.stat().st_mtime
    _touch_heartbeat(str(path))
    assert path.stat().st_mtime >= first_mtime


def test_touch_heartbeat_is_a_no_op_when_path_is_none() -> None:
    _touch_heartbeat(None)  # must not raise


def test_touch_heartbeat_swallows_a_write_failure(_reset_heartbeat_log_flag) -> None:
    """A heartbeat write failure (e.g. an unwritable directory) must never crash the relay over an
    observability side-channel — proven by pointing at a path whose PARENT does not exist."""
    _touch_heartbeat("/this/directory/does/not/exist/heartbeat")  # must not raise


def test_touch_heartbeat_logs_the_write_failure_exactly_once(monkeypatch, _reset_heartbeat_log_flag) -> None:
    """Gatekeeper finding G2 (§Delta): on ECS the heartbeat write fails on EVERY sweep today
    (read-only `/tmp`, no writable mount declared anywhere in `deploy/aws-ecs/envs/dev-sa-east-1/`)
    — logging on every failure would be ~43k warning lines/day/task at the default
    `pollIntervalS=2`. The relay must log the failure ONCE for the life of the process, then keep
    retrying the write (cheap, self-healing if the mount ever becomes writable) silently."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(outbox_relay.logger, "warning", lambda *a, **kw: calls.append((a, kw)))
    bad_path = "/this/directory/does/not/exist/heartbeat"

    _touch_heartbeat(bad_path)
    _touch_heartbeat(bad_path)
    _touch_heartbeat(bad_path)

    assert len(calls) == 1, f"expected exactly one warning log, got {len(calls)}: {calls}"
    assert calls[0][1].get("path") == bad_path


def test_touch_heartbeat_logs_again_after_an_explicit_test_reset(monkeypatch) -> None:
    """Negative control for the once-only guard above: proves the flag genuinely gates future
    calls (rather than the mock just never being invoked) by resetting it mid-test."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(outbox_relay.logger, "warning", lambda *a, **kw: calls.append((a, kw)))
    bad_path = "/this/directory/does/not/exist/heartbeat"

    outbox_relay._reset_heartbeat_write_failure_logged_for_tests()
    _touch_heartbeat(bad_path)
    _touch_heartbeat(bad_path)
    assert len(calls) == 1

    outbox_relay._reset_heartbeat_write_failure_logged_for_tests()
    _touch_heartbeat(bad_path)
    assert len(calls) == 2


async def test_the_loop_touches_the_heartbeat_file_once_per_sweep(tmp_path) -> None:
    """The mutation proof: delete the `_touch_heartbeat` call from `run_relay_loop` and this test
    goes RED (the file is never created)."""
    path = tmp_path / "heartbeat"
    outbox, publisher = _seeded(2), _RecordingPublisher()
    reports = await run_relay_loop(
        outbox,
        publisher,
        claimed_by="w1",
        max_sweeps=3,
        poll_interval_s=0.0,
        heartbeat_path=str(path),
    )
    assert len(reports) == 3
    assert path.is_file()


async def test_no_heartbeat_path_means_no_heartbeat_file(tmp_path) -> None:
    """Default (`heartbeat_path=None`, what every other loop test in this file uses) writes
    nothing — proves the heartbeat is opt-in at the loop level, not implicitly always-on."""
    outbox, publisher = _seeded(1), _RecordingPublisher()
    await run_relay_loop(outbox, publisher, claimed_by="w1", max_sweeps=1, poll_interval_s=0.0)
    assert not (tmp_path / "heartbeat").exists()


def test_default_heartbeat_path_is_under_tmp_matching_the_charts_emptydir_mount() -> None:
    assert DEFAULT_HEARTBEAT_PATH.startswith("/tmp/")


def test_settings_heartbeat_path_defaults_and_is_env_overridable() -> None:
    default_settings = OutboxRelaySettings(
        database_url="postgresql://m:m@localhost:5433/m", kafka_bootstrap_servers="k:9092"
    )
    assert default_settings.heartbeat_path == DEFAULT_HEARTBEAT_PATH

    overridden = OutboxRelaySettings(
        database_url="postgresql://m:m@localhost:5433/m",
        kafka_bootstrap_servers="k:9092",
        heartbeat_path="/tmp/custom-heartbeat",
    )
    assert overridden.heartbeat_path == "/tmp/custom-heartbeat"


# ---------------------------------------------------------------------------
# heartbeat_stale_after_s (gatekeeper finding G1) — pure threshold function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("poll_interval_s", "expected_threshold"),
    [
        (0.5, 5.0),  # 3*0.5=1.5, floored to MIN_HEARTBEAT_STALE_AFTER_S — the exact G1 repro
        (1.0, 5.0),  # 3*1=3, still floored
        (2.0, 6.0),  # 3*2=6, above the floor (today's default)
        (5.0, 15.0),  # 3*5=15, comfortably above the floor
    ],
)
def test_heartbeat_stale_after_s_matches_expected_threshold(
    poll_interval_s: float, expected_threshold: float
) -> None:
    assert heartbeat_stale_after_s(poll_interval_s) == expected_threshold


def test_heartbeat_stale_after_s_never_returns_a_non_positive_threshold() -> None:
    """G1's exact failure mode: a naive `3 * poll_interval_s` with a sub-second (or zero/negative)
    `poll_interval_s` can produce a threshold `<= 0`, making `(age) < threshold` false on every
    invocation — the probe would fail forever, restart-looping the pod. The floor makes that
    impossible regardless of input."""
    for poll_interval_s in (0.5, 0.0, -1.0, 0.001):
        assert heartbeat_stale_after_s(poll_interval_s) >= MIN_HEARTBEAT_STALE_AFTER_S


# ---------------------------------------------------------------------------
# Composition: fail-closed
# ---------------------------------------------------------------------------


def test_build_relay_refuses_without_a_database_url() -> None:
    settings = OutboxRelaySettings(tenant_id="amh", database_url=None, kafka_bootstrap_servers="k:9092")
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        build_relay(settings)


def test_build_relay_refuses_without_a_broker() -> None:
    settings = OutboxRelaySettings(
        tenant_id="amh", database_url="postgresql://m:m@localhost:5433/m", kafka_bootstrap_servers=None
    )
    with pytest.raises(RuntimeError, match="KAFKA_BOOTSTRAP_SERVERS is required"):
        build_relay(settings)


def test_build_relay_constructs_both_halves_when_configured() -> None:
    outbox, publisher = build_relay(
        OutboxRelaySettings(
            tenant_id="amh",
            database_url="postgresql://m:m@localhost:5433/m",
            kafka_bootstrap_servers="k:9092",
        )
    )
    assert outbox.tenant == "amh"
    assert isinstance(publisher, AioKafkaFactPublisher)


async def test_the_real_publisher_refuses_to_send_before_start() -> None:
    """A send on an unstarted producer must raise, never no-op — a silent no-op would let
    `drain_once` seal rows for bytes that never left the process."""
    publisher = AioKafkaFactPublisher(bootstrap_servers="k:9092")
    with pytest.raises(RuntimeError, match="refusing to drop a fact"):
        await publisher.send("t", b"{}")


async def test_the_real_publisher_stop_is_safe_before_start() -> None:
    await AioKafkaFactPublisher(bootstrap_servers="k:9092").stop()


def test_worker_id_identifies_the_process() -> None:
    import os

    assert default_worker_id().endswith(f":{os.getpid()}")


def test_the_cli_exposes_once_and_defaults_to_looping() -> None:
    parser = build_arg_parser()
    assert parser.parse_args([]).once is False
    assert parser.parse_args(["--once"]).once is True


# ---------------------------------------------------------------------------
# "Never auto-started" is a structural property, not a promise
# ---------------------------------------------------------------------------


def test_no_production_module_imports_the_relay() -> None:
    """The relay must be reachable ONLY by an explicit human/scheduler invocation. If any module
    under `src/maezo` ever imports it, some daemon's bring-up can pull a broker publisher into a
    process that never needed one — the outage this module's docstring refuses to manufacture.

    AST-based, not a substring scan: several production modules legitimately NAME the relay in
    prose (`outbox.py` and migration 0008 explain who drains the table, `a2a_composition.py` says
    where facts go), and a fence that cannot tell a docstring from an `import` would be satisfiable
    only by deleting the explanation.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "src" / "maezo"
    relay = root / "a2a" / "outbox_relay.py"
    assert relay.is_file(), "non-vacuity: the relay module must exist for this scan to mean anything"

    def _imports_relay(source: str) -> bool:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                if any(alias.name.endswith("a2a.outbox_relay") for alias in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.endswith("a2a.outbox_relay"):
                    return True
                if module.endswith("maezo.a2a") and any(a.name == "outbox_relay" for a in node.names):
                    return True
        return False

    scanned = 0
    importers: list[str] = []
    for path in root.rglob("*.py"):
        if path == relay:
            continue
        scanned += 1
        if _imports_relay(path.read_text(encoding="utf-8")):
            importers.append(path.relative_to(root).as_posix())

    assert scanned > 100, f"non-vacuity: the scan must cover the tree, saw {scanned} files"
    assert importers == [], f"the relay is IMPORTED by production module(s): {importers}"


def test_the_import_scanner_would_catch_a_real_import() -> None:
    """Negative control for the scan above — otherwise it could be passing because the detector
    never matches anything."""
    import ast

    def _imports_relay(source: str) -> bool:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("a2a.outbox_relay"):
                return True
            if isinstance(node, ast.Import) and any(
                alias.name.endswith("a2a.outbox_relay") for alias in node.names
            ):
                return True
        return False

    assert _imports_relay("from maezo.a2a.outbox_relay import drain_once")
    assert _imports_relay("import maezo.a2a.outbox_relay")
    assert not _imports_relay('"""prose mentioning maezo.a2a.outbox_relay."""')
