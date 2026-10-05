"""Real PostgreSQL complete-driver checkpoint/journal component gates.

ROOT runs this file in its single admitted PG lane. Business/currentness/proof
fixtures are explicitly synthetic; no operational receipt, provider acceptance,
engine or channel policy is qualified. No skip/xfail and no engine mock. The two
parent engine fixtures are replaced only because this component calls PostgreSQL
and never calls CIB. All disposable schemas and connections are owned/cleaned.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo

from maezo.agents.lucas.administrative import runtime as module
from maezo.agents.lucas.administrative.runtime import AdministrativeJourneyRuntime
from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.gateway.capabilities.durability import postgres
from maezo.gateway.capabilities.durability.models import (
    CommandHandle,
    CommandTechnicalState,
    JournalCallTechnicalStatus,
)
from maezo.gateway.capabilities.durability.postgres import PostgresDurabilityJournal
from maezo.gateway.capabilities.journeys.contracts import JourneyBinding, JourneyDispatchOutcome
from maezo.gateway.capabilities.journeys.driver import JourneyDriver
from maezo.gateway.capabilities.journeys.returns import JourneyReplyBoundary
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.pseudonymizer import Pseudonymizer
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.agents.test_lucas_administrative_runtime import resume_input, turn_input
from tests.unit.gateway.capabilities.durability.fixtures import (
    SyntheticProofs,
    descriptor,
    dispatch_evidence,
    observation,
    outbox,
    wait,
)
from tests.unit.gateway.capabilities.journeys.helpers import UnitIngress, binding

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# The admitted synthetic fixture helpers are unannotated test-only callable ports.
synthetic_proofs = cast(Callable[..., Any], SyntheticProofs)
synthetic_descriptor = cast(Callable[..., Any], descriptor)
synthetic_dispatch = cast(Callable[..., Any], dispatch_evidence)
synthetic_observation = cast(Callable[..., Any], observation)
synthetic_wait = cast(Callable[..., Any], wait)
synthetic_outbox = cast(Callable[..., Any], outbox)


@pytest.fixture(scope="session", autouse=True)
def _skip_if_engine_unreachable() -> None:
    """Real PG-only component; CIB is neither required nor mocked."""


@pytest.fixture(scope="session", autouse=True)
def _deploy_spec_artifacts() -> None:
    """This PG component has no process/DMN deployment contract."""


def acknowledged(result: Any) -> Any:
    assert result.technical_status in {
        JournalCallTechnicalStatus.RECORDED,
        JournalCallTechnicalStatus.UNCHANGED,
    }
    assert result.snapshot is not None
    return result.snapshot


class PGObservationAdapter:
    """Synthetic boundary reads real journal; never resubmits a source effect."""

    def __init__(self, journal: PostgresDurabilityJournal, handles: dict[str, CommandHandle]) -> None:
        self.journal, self.handles = journal, handles
        self.execute_calls = 0

    async def execute(self, *args: Any, **kwargs: Any) -> CapabilityRefusalReason:
        self.execute_calls += 1
        raise AssertionError("graph recovery must not resubmit synthetic source effect")

    async def observe(self, b: JourneyBinding, command_ref: str) -> Any:
        handle = self.handles.get(command_ref)
        if handle is None or handle.binding != b.journal_binding():
            return CapabilityRefusalReason.AUTHORITY_UNPROVEN
        return await self.journal.observe_command(b.journal_binding(), handle)

    async def reconcile(self, *args: Any, **kwargs: Any) -> CapabilityRefusalReason:
        return CapabilityRefusalReason.SOURCE_UNAVAILABLE

    async def ingest(self, *args: Any, **kwargs: Any) -> CapabilityRefusalReason:
        return CapabilityRefusalReason.SOURCE_UNAVAILABLE


@pytest.fixture
async def pg_runtime() -> AsyncIterator[Any]:
    dsn = normalize_dsn(_default_test_dsn())
    tenant = "runtime_" + uuid4().hex
    b = binding().model_copy(update={"tenant_ref": tenant})
    schema, cp_schema = schema_for_tenant(tenant), "runtime_cp_" + uuid4().hex
    admin = await asyncpg.connect(dsn, server_settings={"search_path": "pg_catalog"})
    pool: Any = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'CREATE SCHEMA "{cp_schema}"')
        await admin.execute(f'SET search_path TO "{schema}",pg_catalog')
        await admin.execute(Path(postgres.__file__).with_name("schema.sql").read_text())
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=2, server_settings={"search_path": "pg_catalog"}
        )
        proofs = synthetic_proofs()
        handles: dict[str, CommandHandle] = {}
        journal = PostgresDurabilityJournal(
            pool=pool, binding=b.journal_binding(), proofs=proofs, enabled=True
        )
        ingress = UnitIngress(b)
        ingress.durable_observations.add("unit-durable-observation")
        cp_dsn = make_conninfo(dsn, options=f"-c search_path={cp_schema},pg_catalog")

        @asynccontextmanager
        async def saver_session() -> AsyncIterator[AsyncPostgresSaver]:
            async with AsyncPostgresSaver.from_conn_string(cp_dsn) as saver:
                await saver.setup()
                yield saver

        def assemble(saver: AsyncPostgresSaver, scoped: JourneyBinding | None = None) -> tuple[Any, ...]:
            selected = scoped or b
            selected_ingress = ingress if selected == b else UnitIngress(selected)
            selected_journal = (
                journal
                if selected.journal_binding() == b.journal_binding()
                else PostgresDurabilityJournal(
                    pool=pool, binding=selected.journal_binding(), proofs=proofs, enabled=True
                )
            )
            adapter = PGObservationAdapter(selected_journal, handles)
            driver = JourneyDriver(
                selected, journal=selected_journal, currentness=selected_ingress, execution=adapter
            )
            runner = AdministrativeJourneyRuntime(
                binding=selected,
                driver=driver,
                currentness=selected_ingress,
                checkpointer=saver,
                pseudonymizer=Pseudonymizer(key=b"synthetic-pg-runtime-fixture-key"),
                enabled=True,
            )
            return runner, driver, adapter

        yield b, journal, ingress, admin, pool, saver_session, assemble, cp_schema, handles
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{cp_schema}" CASCADE')
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def fenced(pg_runtime: Any) -> tuple[Any, ...]:
    b, journal, *_ = pg_runtime
    jb = b.journal_binding()
    command = synthetic_descriptor(jb, key="unit-original-literal-command-key")
    recorded = acknowledged(await journal.record_command(jb, command, 0))
    pg_runtime[8][recorded.handle.command_ref] = recorded.handle
    dispatch = acknowledged(
        await journal.begin_dispatch(jb, recorded.handle, synthetic_dispatch(jb, command), 1)
    )
    return command, recorded.handle, dispatch


async def test_pg_complete_driver_roundtrip_reconnect_alternating_modes(pg_runtime: Any) -> None:
    b, journal, _, _, _, session, assemble, _, _ = pg_runtime
    async with session() as saver:
        runner, _, adapter = assemble(saver)
        result = await runner.accept_turn(turn_input(b))
        assert isinstance(result, JourneyDispatchOutcome)
        assert result.technical_refusal == CapabilityRefusalReason.SOURCE_UNAVAILABLE
        key = runner._config()
        first = await saver.aget_tuple(key)
        assert (
            first is not None and runner._snapshot(first.checkpoint["channel_values"]).stimulus.kind == "turn"
        )
        assert adapter.execute_calls == 0
    async with session() as saver:
        restarted, _, adapter = assemble(saver)
        assert restarted._config() == key
        for variant in ("resume", "turn", "resume"):
            result = await (
                restarted.resume(resume_input())
                if variant == "resume"
                else restarted.accept_turn(turn_input(b))
            )
            assert isinstance(result, JourneyDispatchOutcome)
            assert result.source_completion_receipt_ref is None
            snapshot = await saver.aget_tuple(key)
            assert snapshot is not None
            saved = restarted._snapshot(snapshot.checkpoint["channel_values"])
            assert saved.binding == b and saved.stimulus.kind == variant and saved.outcome == result
        assert adapter.execute_calls == 0
    assert acknowledged(await journal.observe_journey(b.journal_binding())).journal_revision == 0


@pytest.mark.parametrize("field", list(JourneyBinding.model_fields))
async def test_pg_all_full_binding_fields_do_not_cross_load(pg_runtime: Any, field: str) -> None:
    b, _, _, _, _, session, assemble, _, _ = pg_runtime
    async with session() as saver:
        original, _, _ = assemble(saver)
        await original.accept_turn(turn_input(b))
        changed = b.model_copy(
            update={
                field: (
                    not b.enabled
                    if field == "enabled"
                    else "journey.suporte.step"
                    if field == "task_ref"
                    else f"unit-other-{field}"
                )
            }
        )
        other, _, _ = assemble(saver, changed)
        assert original._config() != other._config()
        assert await saver.aget_tuple(other._config()) is None


async def test_pg_graph_version_does_not_cross_load(pg_runtime: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, _, _, _, _, session, assemble, _, _ = pg_runtime
    async with session() as saver:
        original, _, _ = assemble(saver)
        await original.accept_turn(turn_input(b))
        monkeypatch.setattr(module, "FULL_RUNTIME_GRAPH_VERSION", "unit-pg-server-version-2")
        other, _, _ = assemble(saver)
        assert original._config() != other._config() and await saver.aget_tuple(other._config()) is None


@pytest.mark.parametrize("tamper", ["extra_channel", "nested_extra", "wrong_scope", "inactive_stimulus"])
async def test_pg_unknown_or_malformed_stored_snapshot_blocks_before_driver(
    pg_runtime: Any, tamper: str
) -> None:
    b, _, _, _, _, session, assemble, _, _ = pg_runtime
    async with session() as saver:
        runner, driver, _ = assemble(saver)
        await runner.accept_turn(turn_input(b))
        saved = await saver.aget_tuple(runner._config())
        assert saved is not None
        channels = saved.checkpoint["channel_values"]
        if tamper == "extra_channel":
            channels["legacy_instruction"] = "synthetic-protected-marker"
        elif tamper == "nested_extra":
            channels["outcome"]["invented_receipt"] = "synthetic-forgery"
        elif tamper == "wrong_scope":
            channels["binding"]["source_transition_contract_ref"] = "other"
        else:
            channels["stimulus"]["observation_ref"] = "inactive"
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = channels
        checkpoint["channel_versions"] = dict.fromkeys(channels, "999")
        await saver.aput(
            runner._config(),
            checkpoint,
            {"source": "input", "step": 0, "parents": {}},
            checkpoint["channel_versions"],
        )
        calls: list[object] = []
        original = driver.accept_turn

        async def spy(value: object) -> JourneyDispatchOutcome:
            calls.append(value)
            return cast(JourneyDispatchOutcome, await original(value))

        driver.accept_turn = spy
        assert await runner.accept_turn(turn_input(b)) == CapabilityRefusalReason.CONTRACT_MISMATCH
        assert calls == []


async def test_pg_crash_fence_survives_restart_no_effect_resubmit(pg_runtime: Any) -> None:
    b, journal, _, _, _, session, assemble, _, _ = pg_runtime
    command, handle, dispatch = await fenced(pg_runtime)
    async with session() as saver:
        runner, _, adapter = assemble(saver)
        first = await runner.accept_turn(turn_input(b, dispatch.journal_revision))
        assert isinstance(first, JourneyDispatchOutcome)
        assert first.pending_command_refs == (handle.command_ref,) and adapter.execute_calls == 0
    async with session() as saver:
        restarted, _, adapter = assemble(saver)
        resumed = await restarted.resume(resume_input(revision=dispatch.journal_revision))
        assert isinstance(resumed, JourneyDispatchOutcome)
        assert resumed.pending_command_refs == (handle.command_ref,)
        assert resumed.source_completion_receipt_ref is None and adapter.execute_calls == 0
    preserved = acknowledged(await journal.observe_command(b.journal_binding(), handle))
    assert preserved.technical_state == CommandTechnicalState.DISPATCH_FENCED
    assert preserved.fence_version == dispatch.fence_version and preserved.handle == handle
    replay = acknowledged(
        await journal.record_command(b.journal_binding(), command, preserved.journal_revision)
    )
    assert replay.handle == handle and replay.journal_revision == preserved.journal_revision


async def test_pg_source_commit_lost_graph_reply_preserves_wait_outbox_without_resubmit(
    pg_runtime: Any,
) -> None:
    b, journal, _, _, _, session, assemble, _, _ = pg_runtime
    command, handle, dispatch = await fenced(pg_runtime)
    jb = b.journal_binding()
    committed = acknowledged(
        await journal.record_verified_result(
            jb,
            handle,
            dispatch.dispatch_ref,
            synthetic_observation(jb, command, status="held"),
            (),
            (),
            dispatch.journal_revision,
        )
    )
    recorded_wait = acknowledged(
        await journal.record_wait(jb, synthetic_wait(handle), committed.journal_revision)
    )
    recorded_outbox = acknowledged(
        await journal.enqueue_outbox(jb, synthetic_outbox(handle), recorded_wait.journal_revision)
    )
    async with session() as saver:
        runner, _, adapter = assemble(saver)
        first = await runner.accept_turn(turn_input(b, recorded_outbox.journal_revision))
        assert isinstance(first, JourneyDispatchOutcome)
        assert first.pending_command_refs == () and first.wait_refs == ("unit-wait",)
        assert first.outbox_refs == ("unit-outbox",) and first.source_completion_receipt_ref is None
        assert adapter.execute_calls == 0
    async with session() as saver:
        runner, _, adapter = assemble(saver)
        restarted = await runner.resume(resume_input(revision=recorded_outbox.journal_revision))
        assert isinstance(restarted, JourneyDispatchOutcome)
        assert restarted.wait_refs == first.wait_refs and restarted.outbox_refs == first.outbox_refs
        assert adapter.execute_calls == 0
    source = acknowledged(await journal.observe_command(jb, handle))
    assert source.source_receipt_ref == committed.source_receipt_ref and source.handle == handle
    assert source.technical_state == CommandTechnicalState.RESPONSE_RECORDED
    assert (
        acknowledged(await journal.observe_journey(jb)).journal_revision == recorded_outbox.journal_revision
    )
    assert (
        await JourneyReplyBoundary(journal=journal).deliver(
            jb,
            outbox_ref="unit-outbox",
            worker_ref="unit-worker",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            expected_journal_revision=recorded_outbox.journal_revision,
        )
        == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    )
    assert acknowledged(await journal.observe_journey(jb)).outbox_refs == ("unit-outbox",)


async def test_pg_fresh_revocation_and_cross_case_resume_do_not_disclose_cached_result(
    pg_runtime: Any,
) -> None:
    b, _, ingress, _, _, session, assemble, _, _ = pg_runtime
    async with session() as saver:
        runner, _, _ = assemble(saver)
        result = await runner.accept_turn(turn_input(b))
        assert isinstance(result, JourneyDispatchOutcome)
        saved = await saver.aget_tuple(runner._config())
        ingress.revoked = True
        assert await runner.resume(resume_input()) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
        assert await saver.aget_tuple(runner._config()) == saved
        ingress.revoked = False
        assert (
            await runner.resume(resume_input("unit-other-case-unregistered"))
            == CapabilityRefusalReason.AUTHORITY_UNPROVEN
        )
        assert await saver.aget_tuple(runner._config()) == saved


async def test_pg_checkpoint_has_only_closed_reference_metadata(pg_runtime: Any) -> None:
    b, _, _, admin, _, session, assemble, cp_schema, _ = pg_runtime
    async with session() as saver:
        runner, _, _ = assemble(saver)
        await runner.accept_turn(turn_input(b))
        saved = await saver.aget_tuple(runner._config())
        assert saved is not None
        channels = saved.checkpoint["channel_values"]
        assert frozenset(channels) == frozenset(module.RuntimeCheckpointSnapshot.model_fields)
        serialized = json.dumps(channels)
        for forbidden in ("raw_to", "instructions", "source_request", "authorization_ref", "symptoms", "cpf"):
            assert forbidden not in serialized
        assert await admin.fetchval(f'SELECT count(*) FROM "{cp_schema}".checkpoints') >= 4


async def test_pg_concurrent_tenants_tasks_and_journeys_persist_separate_roots(pg_runtime: Any) -> None:
    b, _, _, admin, _, session, assemble, _, _ = pg_runtime
    other_tenant = "runtime_" + uuid4().hex
    schema = schema_for_tenant(other_tenant)
    scopes = [
        b,
        b.model_copy(update={"task_ref": "journey.suporte.step", "journey_ref": "unit-second-journey"}),
        b.model_copy(update={"tenant_ref": other_tenant, "journey_ref": "unit-third-journey"}),
    ]
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}",pg_catalog')
        await admin.execute(Path(postgres.__file__).with_name("schema.sql").read_text())
        async with session() as saver:
            runners = [assemble(saver, scope)[0] for scope in scopes]
            results = await asyncio.gather(
                *(
                    runner.accept_turn(turn_input(scope))
                    for runner, scope in zip(runners, scopes, strict=True)
                )
            )
            keys = {runner._config()["configurable"]["thread_id"] for runner in runners}
            assert len(keys) == len(scopes)
            for runner, scope, result in zip(runners, scopes, results, strict=True):
                assert isinstance(result, JourneyDispatchOutcome) and result.journey_ref == scope.journey_ref
                saved = await saver.aget_tuple(runner._config())
                assert saved is not None
                snapshot = runner._snapshot(saved.checkpoint["channel_values"])
                assert snapshot.binding == scope and snapshot.outcome == result
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
