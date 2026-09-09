"""ADR0049 D5/D6 joint proof: real tenant PG -> real relay/mTLS -> pinned CIB.

ROOT alone runs the disposable --relay-synthetic image fixture. Missing opt-in,
configuration, bootstrap or services FAILS; this file never skips or starts a
service. Synthetic authority is not D4 login/admission or D7 REST enforcement.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from tests.support.human_relay_live import LiveRelayFixture, RelayConfig, relay

from maezo.gateway.human.outbox import CommandConflictError, LeaseLostError, PostgresHumanOutbox
from maezo.gateway.human.transport import EngineConflictError, EngineUnavailableError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def artifact_directory(config, node_name):
    root = os.environ.get("MAEZO_HUMAN_RELAY_ARTIFACTS")
    assert root, "ROOT must provide an existing evidence directory"
    directory = Path(root)
    assert directory.is_absolute() and directory.resolve(strict=True) == directory
    assert not directory.is_relative_to(config.directory) and not config.directory.is_relative_to(directory)
    artifacts = directory / (node_name + "-" + uuid4().hex)
    artifacts.mkdir()
    return artifacts


@pytest.fixture
async def live(request):
    config = RelayConfig.load()
    artifacts = artifact_directory(config, request.node.name)
    fixture = LiveRelayFixture(config, artifacts)
    try:
        await fixture.open()
        yield fixture
    finally:
        await fixture.close()


async def test_intent_process_crash_before_dispatch_recovers_with_authenticated_get_first(live):
    command = await live.command()
    before = dict(await live.task())
    await live.child("persist-then-crash", command=command)
    await live.assert_pending(command)
    assert dict(await live.task()) == before
    directory = await live.child("run-once")
    observed = json.loads((directory / "transport-events.json").read_text())
    assert observed["events"] == ["GET.begin", "GET.missing", "POST.begin", "POST.committed"]
    receipt = await live.assert_result(command)
    task = await live.task()
    assert task["assignee_"] == command.principal_ref
    assert str(task["rev_"]) == receipt.resulting_task_revision
    assert len(await live.engine_receipts()) == 1


async def test_claim_release_roundtrip_has_exact_immutable_receipts_and_audit(live):
    claim = await live.command()
    await live.persist(claim)
    observer = live.transport()
    assert await relay(live.store, observer).run_once()
    claimed = await live.assert_result(claim)
    claim_bytes = bytes((await live.delivery(claim))["engine_receipt"])
    assert observer.events == ["GET.begin", "GET.missing", "POST.begin", "POST.committed"]
    assert (await live.task())["assignee_"] == claim.principal_ref
    release = await live.command("release")
    assert release.task_revision == claimed.resulting_task_revision
    await live.persist(release)
    assert await relay(live.store, observer).run_once()
    released = await live.assert_result(release)
    task = await live.task()
    assert task["assignee_"] is None and str(task["rev_"]) == released.resulting_task_revision
    assert len(await live.engine_receipts()) == 2
    # Authenticated receipt and exact POST replay remain the identical engine
    # bytes after assignment changed. They must not apply the old claim again.
    real = live.config.transport()
    assert await real.receipt(claim) == await real.dispatch(claim) == claim_bytes
    assert (await live.task())["assignee_"] is None
    assert str((await live.task())["rev_"]) == released.resulting_task_revision
    assert len(await live.rows("audit_chain")) == len(await live.rows("audit_emit_dedup")) == 4
    assert not await relay(live.store, observer).run_once()


async def test_actual_engine_commit_then_lost_response_reconciles_after_process_restart(live):
    command = await live.command()
    await live.persist(command)
    observer = live.transport()
    observer.drop_response = True
    assert await relay(live.store, observer).run_once()
    assert observer.events == [
        "GET.begin",
        "GET.missing",
        "POST.begin",
        "POST.committed",
        "response.dropped.after.actual.commit",
    ]
    await live.assert_pending(command, engine_committed=True)
    winner = dict(await live.task())
    assert winner["assignee_"] == command.principal_ref
    assert winner["rev_"] == int(command.task_revision) + 1
    original = (await live.engine_receipts())[0]["receipt_"].encode()
    assert observer.receipts == [original]
    await live.allow_retry()
    directory = await live.child("run-once")
    assert json.loads((directory / "transport-events.json").read_text())["events"] == [
        "GET.begin",
        "GET.committed",
    ]
    assert (directory / "engine-receipt-0.json").read_bytes() == original
    await live.assert_result(command)
    assert dict(await live.task()) == winner
    assert len(await live.engine_receipts()) == 1


@pytest.mark.parametrize("fault", ["result_audit", "terminal_mark", "result_commit"])
async def test_real_result_transaction_failure_stays_pending_and_recovers_without_second_effect(live, fault):
    command = await live.command()
    await live.persist(command)
    schema = live.scope.tenant
    await live.admin.execute(f'CREATE SEQUENCE "{schema}".d6_fault_called')
    table, condition = (
        ("audit_chain", "NEW.action='human_command.result'")
        if fault == "result_audit"
        else ("human_command_delivery", "NEW.status<>'pending'")
    )
    await live.admin.execute(
        f'CREATE FUNCTION "{schema}".d6_fault() RETURNS trigger LANGUAGE plpgsql AS $$ '
        f"BEGIN IF {condition} THEN PERFORM nextval('{schema}.d6_fault_called'); "
        "RAISE EXCEPTION 'synthetic D6 result abort'; END IF; RETURN NEW; END $$"
    )
    if fault == "result_commit":
        await live.admin.execute(
            f'CREATE CONSTRAINT TRIGGER d6_fault AFTER UPDATE ON "{schema}".{table} '
            f'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "{schema}".d6_fault()'
        )
    else:
        operation = "INSERT" if fault == "result_audit" else "UPDATE"
        await live.admin.execute(
            f'CREATE TRIGGER d6_fault BEFORE {operation} ON "{schema}".{table} '
            f'FOR EACH ROW EXECUTE FUNCTION "{schema}".d6_fault()'
        )
    observer = live.transport()
    assert await relay(live.store, observer).run_once()
    assert observer.events == ["GET.begin", "GET.missing", "POST.begin", "POST.committed"]
    assert await live.admin.fetchval(f'SELECT is_called FROM "{schema}".d6_fault_called')
    await live.assert_pending(command, engine_committed=True)
    winner = dict(await live.task())
    original = (await live.engine_receipts())[0]["receipt_"].encode()
    assert observer.receipts == [original]
    await live.admin.execute(f'DROP TRIGGER d6_fault ON "{schema}".{table}')
    await live.allow_retry()
    directory = await live.child("run-once")
    assert json.loads((directory / "transport-events.json").read_text())["events"] == [
        "GET.begin",
        "GET.committed",
    ]
    await live.assert_result(command)
    assert (directory / "engine-receipt-0.json").read_bytes() == original
    assert dict(await live.task()) == winner and len(await live.engine_receipts()) == 1


@pytest.mark.parametrize("pause", ["before_post", "after_engine_commit"])
async def test_two_relays_expired_lease_and_stale_worker_never_duplicate_engine_effect(live, pause):
    command = await live.command()
    await live.persist(command)
    first, second = live.transport(), live.transport()
    first.pause_after_missing = pause == "before_post"
    first.pause_after_commit = pause == "after_engine_commit"
    replica = PostgresHumanOutbox(scope=live.scope, pool=live.pool)
    worker = asyncio.create_task(relay(live.store, first, lease_seconds=5).run_once())
    try:
        await asyncio.wait_for(first.reached.wait(), 15)
        old = dict(await live.delivery(command))
        assert not await relay(replica, second).run_once(), "unexpired lease must exclude another relay"
        assert second.events == []
        # Observe PostgreSQL wall-clock expiry; no sleeping inside an engine TX
        # and no mutation of engine revisions or fixture lease timestamps here.
        for _ in range(160):
            if not await live.admin.fetchval(
                f'SELECT lease_until>clock_timestamp() FROM "{live.scope.tenant}".human_command_delivery'
            ):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("actual PostgreSQL lease failed to expire within eight seconds")
        assert await relay(replica, second).run_once()
        winner = dict(await live.task())
        finalized = dict(await live.delivery(command))
        assert finalized["fence"] == old["fence"] + 1
        first.resume.set()
        assert await worker
        await live.assert_result(command)
        assert dict(await live.delivery(command)) == finalized, "stale worker cannot overwrite/release winner"
        assert dict(await live.task()) == winner
        assert (
            winner["assignee_"] == command.principal_ref and winner["rev_"] == int(command.task_revision) + 1
        )
        assert len(await live.engine_receipts()) == 1
        if pause == "before_post":
            assert first.events == ["GET.begin", "GET.missing"]
            assert second.events == ["GET.begin", "GET.missing", "POST.begin", "POST.committed"]
        else:
            assert first.events == ["GET.begin", "GET.missing", "POST.begin", "POST.committed"]
            assert second.events == ["GET.begin", "GET.committed"]
            assert first.receipts == second.receipts
    finally:
        first.resume.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize("stale", ["task", "evidence", "authority"])
async def test_authoritative_changes_after_intent_return_conflict_without_changing_winner(live, stale):
    command = await live.command()
    await live.persist(command)
    if stale == "task":
        # Explicit synthetic out-of-band task owner mutation is an adversarial
        # race, not a claim that D7 has restricted REST credentials yet.
        response = await live.rest.post(
            f"/task/{command.task_id}/assignee", json={"userId": "synthetic-other"}
        )
        assert response.status_code == 204
    elif stale == "evidence":
        await live.publish_evidence()
    else:
        await live.publish_principal()
    winner = dict(await live.task())
    observer = live.transport()
    assert await relay(live.store, observer).run_once()
    assert observer.events == ["GET.begin", "GET.missing", "POST.begin"]
    await live.assert_result(command, conflict="REVISION_CONFLICT")
    assert dict(await live.task()) == winner and await live.engine_receipts() == []


@pytest.mark.parametrize("mismatch", ["principal", "certificate"])
async def test_unauthorized_receipt_query_keeps_relay_pending_and_prevents_post(live, mismatch):
    command = await live.command()
    if mismatch == "principal":
        command = replace(command, principal_subject="synthetic-wrong-subject")
    await live.persist(command)
    observer = live.transport(peer="authority" if mismatch == "certificate" else "command")
    before = dict(await live.task())
    assert await relay(live.store, observer).run_once()
    assert observer.events == ["GET.begin"]
    delivery = await live.delivery(command)
    assert delivery["status"] == "pending" and delivery["audit_result_hash"] is None
    assert delivery["engine_receipt"] is None and len(await live.rows("audit_chain")) == 1
    assert dict(await live.task()) == before and await live.engine_receipts() == []
    # A valid peer and stable identity can query the same real service. A generic
    # network outage cannot satisfy this negative-control case.
    assert await live.config.transport().receipt(replace(command, principal_subject=live.subject)) is None


@pytest.mark.parametrize("scope_field", ["tenant", "workload_ref"])
async def test_real_signed_transport_refuses_foreign_tenant_or_workload(live, scope_field):
    command = await live.command()
    before = dict(await live.task())
    foreign = "synthetic_foreign" if scope_field == "tenant" else "synthetic-foreign-workload"
    scope = live.scope.model_copy(update={scope_field: foreign})
    forged = replace(command, **{scope_field: foreign})
    real = live.config.transport(scope=scope)
    for invoke in (real.receipt, real.dispatch):
        with pytest.raises(EngineUnavailableError):
            await invoke(forged)
    assert dict(await live.task()) == before and await live.engine_receipts() == []
    assert await live.config.transport().receipt(command) is None


async def test_same_receipt_identity_mismatches_cannot_change_engine_or_durable_winner(live):
    command = await live.command()
    await live.persist(command)
    observer = live.transport()
    assert await relay(live.store, observer).run_once()
    await live.assert_result(command)
    winner, delivery = dict(await live.task()), dict(await live.delivery(command))
    original = bytes(delivery["engine_receipt"])
    changed = replace(command, evidence_digest="f" * 64)
    with pytest.raises(CommandConflictError):
        await live.persist(changed)
    real = live.config.transport()
    for invoke in (real.receipt, real.dispatch):
        with pytest.raises(EngineConflictError) as failure:
            await invoke(changed)
        assert failure.value.code == "REVISION_CONFLICT"
    assert await real.receipt(command) == await real.dispatch(command) == original
    assert dict(await live.task()) == winner and dict(await live.delivery(command)) == delivery
    assert len(await live.engine_receipts()) == 1 and len(await live.rows("audit_chain")) == 2
    with pytest.raises(asyncpg.RaiseError):
        await live.admin.execute(
            f'UPDATE "{live.scope.tenant}".human_command_outbox SET principal_ref=$1', "synthetic-other"
        )


async def test_stale_claim_cannot_finish_or_release_a_successor_after_actual_receipt(live):
    command = await live.command()
    await live.persist(command)
    stale = await live.store.claim(lease_seconds=30)
    assert stale is not None
    await live.allow_retry(expire=True)
    observer = live.transport()
    assert await relay(live.store, observer).run_once()
    delivery = dict(await live.delivery(command))
    raw = bytes(delivery["engine_receipt"])
    assert raw == (await live.engine_receipts())[0]["receipt_"].encode()
    for invoke in (
        lambda: live.store.require_lease(stale),
        lambda: live.store.retry_later(stale, retry_seconds=1),
        lambda: live.store.finish(stale, receipt=raw),
    ):
        with pytest.raises(LeaseLostError):
            await invoke()
    assert dict(await live.delivery(command)) == delivery
    await live.assert_result(command)
    assert (
        len(await live.engine_receipts()) == 1
        and (await live.task())["rev_"] == int(command.task_revision) + 1
    )
