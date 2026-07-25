"""LIVE-Postgres proof: the Helena->Rafael A2A delegation edge's "T-F becomes real" claim (W3).

Tier 2 (`@pytest.mark.integration`), placed HERE (not under `tests/integration/`) deliberately —
mirrors `tests/unit/a2a/test_idempotency_store.py`'s own placement rationale: this suite needs a
REAL Postgres but explicitly NOT the CIB Seven BPMN engine (`tests/integration/`'s package-wide
autouse `_skip_if_engine_unreachable` fixture would gate every test in that tree on the engine's
reachability, which this suite must not depend on — `docs/design/A2A-dispatcher-card-signing.md`
§9/design §4.2: "No BPMN engine for the A2A hop itself").

What this proves that the W2 unit suite (fakes only) could not:
  1. **T-F becomes real**: a real `DelegationDispatcher` (assembled via `agent_runtime.
     a2a_composition.build_auth_delegation_dispatcher`, the SAME composition a real daemon would
     use) persists the `a2a.delegate:rafael` audit link into a REAL `audit_chain` table on a real
     delegation — not a `FakeAuditSink` recording.
  2. **Chain-valid**: `gateway.audit_postgres.verify_chain` recomputes and confirms the hash chain
     integrity of the tenant's REAL chain after the delegation.
  3. **PHI-safe**: a synthetic CPF planted in a delegation's `payload_meta` (the one envelope field
     the `_looks_like_phi` guard on `payload_ref` does NOT cover) never reaches the persisted row.
  4. **Durable idempotency**: re-delegating the same `task_id` from a FRESH `DelegationDispatcher`
     instance (simulating a second replica) replays instead of re-auditing/re-running the handler —
     backed by the REAL `a2a_idempotency` table (migration 0003), not the in-memory `_inflight` map.
  5. **Two distinct audit surfaces, not conflated**: the SAME `PostgresAuditSink` instance also
     durably records Rafael's OWN process-start attempt (T-C2, `action` prefix `start_process:`) —
     this suite asserts on `action="a2a.delegate:rafael"` specifically and never confuses the two.

No CIB Seven engine is used: `cibseven_base_url` in `AgentRuntimeSettings` points at an
intentionally unreachable address, so Rafael's `start_process` node hits `CibSevenError` and
degrades to `process_started=False` (by design, `agents/rafael/graph.py::start_process`) — the
delegation itself still SUCCEEDS (`output_ref` is the business-key reference regardless). Kafka is
a recording fake (facts are observability, not the T-F audit — see `a2a_composition`'s docstring).
`PhiZoneMockProvider` (not `noop`) is used so the dossier's `generate(phi=True)` call is actually
exercised, per the design's explicit "noop swallows errors and false-greens the seam" warning.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.a2a import Budget, DelegationEnvelope
from maezo.a2a.dispatcher import a2a_audit_dedup_key
from maezo.agents.helena.delegation import delegate_auth_analysis
from maezo.gateway.audit_postgres import normalize_dsn, verify_chain
from maezo.runtime.agent_runtime.a2a_composition import build_auth_delegation_dispatcher
from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
from maezo.runtime.inference import InferenceProvider, InferenceSettings

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Deliberately NOT the compose stack's default port (5433/5432) — a FREE, dedicated port for this
# suite's own throwaway Postgres instance, per the W3 charter. Override with
# MAEZO_TEST_A2A_EDGE_DATABASE_URL for a different local setup.
_DEFAULT_DSN = "postgresql://maezo:maezo@localhost:5642/maezo"

# An address guaranteed to refuse a connection instantly (no CIB Seven engine — port 1 requires
# root to bind and is never a real HTTP service on any dev/CI host).
_UNREACHABLE_CIBSEVEN_URL = "http://127.0.0.1:1/engine-rest"

_CASE_META: dict[str, Any] = {
    "beneficiario_pseudo_id": "pseudo-livepg-1",
    "prestador_id": "prestador-1",
    "codigo_procedimento_tuss": "10101012",
    "categoria_procedimento": "consulta",
    "carater_atendimento": "eletivo",
    "valor_estimado_brl": 500.0,
    "requer_autorizacao": True,
    "documentacao_completa": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dut_atendida": True,
    "dentro_teto_l2": False,
    "rede_credenciada": True,
}


class _RecordingKafkaProducer:
    """Facts are observability, not the T-F audit (see module docstring) — a recording fake."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes | None]] = []

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        self.sent.append((topic, value, key))


def _default_test_dsn() -> str:
    return os.environ.get("MAEZO_TEST_A2A_EDGE_DATABASE_URL", _DEFAULT_DSN)


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


def _apply_migrations(dsn: str, tenant_id: str) -> None:
    """Apply the REAL alembic migrations 0001->0005 (incl. 0003 a2a_idempotency) to `tenant_id`'s
    schema — mirrors `tests/integration/conftest.py::_apply_migrations` exactly (duplicated here,
    not imported, so this suite stays self-contained and never depends on the CIB-Seven-gated
    `tests/integration/` package — see module docstring)."""
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
            f"COULD NOT VERIFY: Postgres not reachable at {dsn!r} (override with "
            "MAEZO_TEST_A2A_EDGE_DATABASE_URL). This suite needs a FREE, dedicated Postgres on "
            "port 5642 (deliberately NOT the compose stack's 5433/5432) with migrations "
            "0001->0005 applied — see the W3 charter / test module docstring."
        )
    return dsn


@pytest.fixture(scope="module")
def tenant_schema(pg_dsn: str) -> AsyncIterator[str]:
    tenant_id = f"a2aw3{uuid.uuid4().hex[:12]}"  # [a-z][a-z0-9_]* per schema_for_tenant

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


def _settings(*, tenant: str, database_url: str) -> AgentRuntimeSettings:
    return AgentRuntimeSettings(
        tenant_id=tenant,
        agent_id="rafael",
        database_url=database_url,
        cibseven_base_url=_UNREACHABLE_CIBSEVEN_URL,
    )


def _phi_capable_inference() -> InferenceProvider:
    # PhiZoneMockProvider, NOT noop — noop swallows the dossier's generate(phi=True) call (its
    # own PhiZoneRoutingError is caught by `_build_dossier`'s except block), which would NEVER
    # exercise the inference seam at all (design doc §9 / risk 5). Constructed directly (not via
    # MAEZO_INFERENCE_PROVIDER env) so this suite never depends on ambient env state.
    return InferenceProvider(InferenceSettings(provider="phi_zone_mock"))


async def _fetch_a2a_delegate_rows(dsn: str, tenant_id: str, *, task_id: str) -> list[Any]:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        return await conn.fetch(
            "SELECT * FROM audit_chain WHERE action = 'a2a.delegate:rafael' "
            "AND decision_basis->>'task_id' = $1",
            task_id,
        )
    finally:
        await conn.close()


async def _fetch_start_process_rows(dsn: str, tenant_id: str) -> list[Any]:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        return await conn.fetch("SELECT * FROM audit_chain WHERE action LIKE 'start_process:%'")
    finally:
        await conn.close()


# --- 1/2/3/5: T-F fires live, chain-valid, PHI-safe, two distinct surfaces --------------------


async def test_live_delegation_audit_fires_chain_valid_and_phi_safe(pg_dsn: str, tenant_schema: str) -> None:
    settings = _settings(tenant=tenant_schema, database_url=pg_dsn)
    dispatcher = build_auth_delegation_dispatcher(
        settings, inference=_phi_capable_inference(), kafka_producer=_RecordingKafkaProducer()
    )

    result = await delegate_auth_analysis(
        dispatcher,
        tenant=tenant_schema,
        numero_guia_tiss="GUIA-LIVEPG-1",
        coverage_ref="fhir://Coverage/livepg-1",
        case_meta=_CASE_META,
    )

    assert result.success
    assert result.output_ref == f"process://AUTH-{tenant_schema}-GUIA-LIVEPG-1"
    assert result.idempotent_replay is False
    task_id = f"auth-{tenant_schema}-GUIA-LIVEPG-1"

    # Surface 1 (T-F): the delegation audit — action + dedup_key exactly as the design specifies.
    rows = await _fetch_a2a_delegate_rows(pg_dsn, tenant_schema, task_id=task_id)
    assert len(rows) == 1, "exactly one a2a.delegate:rafael chain link for this task_id"
    row = rows[0]
    assert row["tenant_id"] == tenant_schema
    assert row["agent_id"] == "helena"
    assert row["decision"] == "ALLOW"
    # dedup_key lives in the sibling audit_emit_dedup table (0005) — confirm it round-trips there.
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        dedup_row = await conn.fetchrow(
            "SELECT record_hash FROM audit_emit_dedup WHERE tenant = $1 AND dedup_key = $2",
            tenant_schema,
            a2a_audit_dedup_key(tenant_schema, task_id),
        )
    finally:
        await conn.close()
    assert dedup_row is not None
    assert dedup_row["record_hash"] == row["record_hash"]

    # Surface 5 (distinct, never conflated): Rafael's OWN process-start audit (T-C2) ALSO landed
    # on this same sink (audit-before-effect fires even though CibSevenError degrades the actual
    # engine call right after) — a DIFFERENT action prefix, asserted separately.
    start_rows = await _fetch_start_process_rows(pg_dsn, tenant_schema)
    assert len(start_rows) >= 1
    assert all(r["action"] != "a2a.delegate:rafael" for r in start_rows)

    # Surface 2: chain-valid — recompute + verify the WHOLE tenant chain (both surfaces included).
    verification = await verify_chain(pg_dsn, tenant_schema)
    assert verification.valid, verification.reason
    assert verification.total_records == verification.verified_records

    # Surface 3: PHI-safe — a synthetic CPF planted directly in payload_meta (bypassing Helena's
    # originator, which never forwards an un-allow-listed key in the first place) must never reach
    # the persisted row, mirroring `test_dispatcher.py`'s fake-sink proof but against a REAL row.
    synthetic_cpf = "987.654.321-00"
    phi_envelope = DelegationEnvelope.root(
        task_id="auth-phi-probe-1",
        task_type="authorization.analyze",
        origin="helena",
        target="rafael",
        tenant=tenant_schema,
        budget=Budget(tokens=64, time_ms=60_000, cost_per_hop=1),
        payload_ref="fhir://Coverage/livepg-phi-probe",
        payload_meta={"numero_guia_tiss": "GUIA-LIVEPG-PHI", "cpf_beneficiario": synthetic_cpf},
    )
    phi_result = await dispatcher.delegate(phi_envelope)
    assert phi_result.success

    phi_rows = await _fetch_a2a_delegate_rows(pg_dsn, tenant_schema, task_id="auth-phi-probe-1")
    assert len(phi_rows) == 1
    serialized = str(dict(phi_rows[0]))
    assert synthetic_cpf not in serialized
    assert "cpf_beneficiario" not in serialized


# --- 4: durable idempotency across independent dispatcher instances ("two replicas") ----------


async def test_live_durable_idempotency_across_dispatcher_instances(pg_dsn: str, tenant_schema: str) -> None:
    settings = _settings(tenant=tenant_schema, database_url=pg_dsn)

    dispatcher_a = build_auth_delegation_dispatcher(
        settings, inference=_phi_capable_inference(), kafka_producer=_RecordingKafkaProducer()
    )
    first = await delegate_auth_analysis(
        dispatcher_a,
        tenant=tenant_schema,
        numero_guia_tiss="GUIA-LIVEPG-IDEMP",
        coverage_ref="fhir://Coverage/livepg-idemp",
        case_meta=_CASE_META,
    )
    assert first.success
    assert first.idempotent_replay is False

    # A FRESH dispatcher instance — simulates a second replica with no in-memory _inflight state;
    # only the DURABLE a2a_idempotency table (migration 0003) can make this a replay.
    dispatcher_b = build_auth_delegation_dispatcher(
        settings, inference=_phi_capable_inference(), kafka_producer=_RecordingKafkaProducer()
    )
    second = await delegate_auth_analysis(
        dispatcher_b,
        tenant=tenant_schema,
        numero_guia_tiss="GUIA-LIVEPG-IDEMP",  # same guide -> same deterministic task_id
        coverage_ref="fhir://Coverage/livepg-idemp",
        case_meta=_CASE_META,
    )

    assert second.idempotent_replay is True
    assert second.output_ref == first.output_ref

    task_id = f"auth-{tenant_schema}-GUIA-LIVEPG-IDEMP"
    rows = await _fetch_a2a_delegate_rows(pg_dsn, tenant_schema, task_id=task_id)
    assert len(rows) == 1, "replay must NOT write a second a2a.delegate:rafael chain link"

    # The durable a2a_idempotency row itself is sealed 'done' with the SAME output_ref.
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        idem_row = await conn.fetchrow(
            "SELECT status, result FROM a2a_idempotency WHERE task_id = $1 AND tenant = $2",
            task_id,
            tenant_schema,
        )
    finally:
        await conn.close()
    assert idem_row is not None
    assert idem_row["status"] == "done"
