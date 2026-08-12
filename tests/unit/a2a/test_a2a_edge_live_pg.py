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

from maezo.a2a import (
    AgentCard,
    Budget,
    CardSignatureError,
    DelegationEnvelope,
    FactProducer,
    HandlerOutput,
    build_dispatcher,
    card_signer_from_key,
    card_signing_key_from_env,
    per_tenant_key_env_var,
)
from maezo.a2a.dispatcher import a2a_audit_dedup_key, a2a_audit_outcome_dedup_key
from maezo.agents.helena.delegation import delegate_auth_analysis
from maezo.gateway.audit_postgres import PostgresAuditSink, normalize_dsn, verify_chain
from maezo.runtime.agent_runtime.a2a_composition import build_auth_delegation_dispatcher
from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
from maezo.runtime.inference import InferenceProvider, InferenceSettings

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Deliberately NOT the compose stack's default port (5433/5432) — a FREE, dedicated port for this
# suite's own throwaway Postgres instance, per the W3 charter. Override with
# MAEZO_TEST_A2A_EDGE_DATABASE_URL for a different local setup.
#
# NIT (W4): the charter specified 5643; this constant previously said 5642 (a copy/paste drift
# flagged by the independent R1 live-PG verification — `docs/evidence-ledger.md`'s t2.4/W3 row).
# Reconciled to 5643 for consistency; the env var above still overrides this for any other setup.
_DEFAULT_DSN = "postgresql://maezo:maezo@localhost:5643/maezo"

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
            "port 5643 (deliberately NOT the compose stack's 5433/5432) with migrations "
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
        # ADR-0039 Q7: EXPLICIT now. This suite exercises the dev/unsigned composition path, which
        # used to be inherited from `AgentRuntimeSettings`' pydantic default; that default is now
        # the fail-closed "production", where the unsigned-Cards opt-out is IGNORED. Passing it
        # here keeps these tests proving what they are named for. NOTE: this suite is skipped
        # without a live Postgres, so the flip would NOT have reddened CI — it would have surfaced
        # as a confusing local failure the next time someone ran the live-PG lane.
        agent_runtime_mode="local",
    )


def _phi_capable_inference() -> InferenceProvider:
    # PhiZoneMockProvider, NOT noop — noop swallows the dossier's generate(phi=True) call (its
    # own PhiZoneRoutingError is caught by `_build_dossier`'s except block), which would NEVER
    # exercise the inference seam at all (design doc §9 / risk 5). Constructed directly (not via
    # MAEZO_INFERENCE_PROVIDER env) so this suite never depends on ambient env state.
    return InferenceProvider(InferenceSettings(provider="phi_zone_mock"))


@pytest.fixture(autouse=True)
def _no_ambient_card_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-G enforcement: this suite must never depend on an ambient `MAEZO_A2A_CARD_SIGNING_KEY`
    (same "never depend on ambient env state" rationale as `_phi_capable_inference` above) — the
    tests that need the key SET do so explicitly within their own body (a later
    `monkeypatch.setenv` in the same test overrides this fixture's `delenv` for the rest of that
    test).

    F2: the unsigned dev-path tests here run with an EXPLICIT `agent_runtime_mode="local"` (set in
    `_settings` above — it was the pydantic default until ADR-0039 Q7 made it fail-closed) and no
    key, and the composition root REFUSES to build an unsigned dispatcher unless the explicit
    non-production opt-out is set. Set it for the suite: these tests deliberately exercise the
    dev/unsigned path (`build_auth_delegation_dispatcher` without a key), so the opt-out is exactly
    the explicit signal F2 requires. It is IGNORED by the key-present T-G tests below (a present key
    always wins).

    ADR-0039 §4.4 (decision 6), SYMMETRIC to F2: the SAME dev/unsigned path also crosses the
    ENVELOPE-signing gate (`_require_envelope_signing_or_fail_closed`, reached via
    `build_auth_delegation_dispatcher`), which — exactly like the Card gate above — REFUSES to
    compose a verifier for a keyless tenant unless the explicit non-production opt-out is set. These
    non-signing dev-path tests (audit fires + chain-valid + PHI-safe; durable idempotency across
    dispatcher instances) prove subjects orthogonal to signing, so they take decision 6's
    dev-local-only escape hatch for the envelope surface too, with the identical rationale: deliberate
    dev/unsigned path, explicit opt-out, and IGNORED by the key-present T-G signed/unsigned tests
    below since a present per-tenant key satisfies BOTH gates. Signing enforcement itself stays proven
    by those key-present T-G tests and the a2a attack suite — this opt-out only lets the dev-path
    tests compose."""
    monkeypatch.delenv("MAEZO_A2A_CARD_SIGNING_KEY", raising=False)
    monkeypatch.setenv("MAEZO_A2A_ALLOW_UNSIGNED_CARDS", "1")
    monkeypatch.setenv("MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES", "1")


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


async def _fetch_a2a_delegate_and_outcome_rows(dsn: str, tenant_id: str, *, task_id: str) -> list[Any]:
    """Like `_fetch_a2a_delegate_rows`, but also matches the W4 terminal-outcome row
    (`a2a.delegate:rafael:outcome`) — used by the terminal-outcome LIVE proof below."""
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        return await conn.fetch(
            "SELECT * FROM audit_chain WHERE action LIKE 'a2a.delegate:rafael%' "
            "AND decision_basis->>'task_id' = $1 "
            "ORDER BY created_at",
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


async def _count_audit_rows(dsn: str, tenant_id: str) -> int:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        count = await conn.fetchval("SELECT count(*) FROM audit_chain")
        return int(count)
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


# --- T-G enforcement (W4, design doc §10): the signed-Card ENFORCEMENT proof, LIVE --------------
#
# Through the REAL production composition (`build_auth_delegation_dispatcher`) for the positive
# case; through the exact same `card_signing_key_from_env`/`card_signer_from_key`/`build_dispatcher`
# seam the composition itself calls for the fail-closed negative cases (unsigned/tampered/
# wrong-key) — a real `PostgresAuditSink` is wired in every case so the negative proofs also show
# that rejection happens BEFORE the audit-before-effect seam is ever reached (no audit row).

_TG_TEST_KEY = "test-live-pg-card-signing-key-0123456789"
_TG_OTHER_KEY = "a-completely-different-live-pg-signing-key"


async def test_live_tg_enforcement_signed_card_admits_and_dispatches(
    pg_dsn: str, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-G ENFORCEMENT, the positive case, through the REAL production composition: with this
    tenant's PER-TENANT key (`MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>`, ADR-0039 §4.4 leg E2) set,
    Cards come back signed AND the registry's verifier is wired (the composition flip this wave
    makes) — a validly-signed Card is admitted and the delegation actually dispatches, persisting a
    real audit row exactly like the dev-path (unsigned) proof earlier in this module."""
    monkeypatch.setenv(per_tenant_key_env_var(tenant_schema), _TG_TEST_KEY)
    settings = _settings(tenant=tenant_schema, database_url=pg_dsn)
    dispatcher = build_auth_delegation_dispatcher(
        settings, inference=_phi_capable_inference(), kafka_producer=_RecordingKafkaProducer()
    )

    result = await delegate_auth_analysis(
        dispatcher,
        tenant=tenant_schema,
        numero_guia_tiss="GUIA-LIVEPG-TG-SIGNED",
        coverage_ref="fhir://Coverage/livepg-tg-signed",
        case_meta=_CASE_META,
    )

    assert result.success
    assert result.output_ref == f"process://AUTH-{tenant_schema}-GUIA-LIVEPG-TG-SIGNED"
    task_id = f"auth-{tenant_schema}-GUIA-LIVEPG-TG-SIGNED"
    rows = await _fetch_a2a_delegate_rows(pg_dsn, tenant_schema, task_id=task_id)
    assert len(rows) == 1
    assert rows[0]["decision"] == "ALLOW"


def _bare_rafael_card(tenant: str, *, signature: str | None = None) -> AgentCard:
    return AgentCard(
        agent_id="rafael",
        version="v0",
        tenant=tenant,
        security_zone="phi",
        accepted_task_types=frozenset({"authorization.analyze"}),
        signature=signature,
    )


async def _never_called_handler(envelope: DelegationEnvelope) -> HandlerOutput:
    raise AssertionError("handler must NEVER run — build_dispatcher should have raised first")


async def test_live_tg_enforcement_unsigned_card_cannot_dispatch(
    pg_dsn: str, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-G ENFORCEMENT, fail-closed negative proof: with the SAME key-resolution seam the
    production composition uses, an UNSIGNED Card can never even produce a dispatcher —
    `build_dispatcher`'s verifier-gated `register()` raises before anything is returned, so the
    delegation structurally CANNOT dispatch (nothing to call `.delegate()` on). No audit row is
    ever written — the rejection happens before the audit-before-effect seam is reached."""
    monkeypatch.setenv("MAEZO_A2A_CARD_SIGNING_KEY", _TG_TEST_KEY)
    signer = card_signer_from_key(card_signing_key_from_env())
    assert signer is not None

    audit = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        before = await _count_audit_rows(pg_dsn, tenant_schema)
        with pytest.raises(CardSignatureError):
            build_dispatcher(
                tenant=tenant_schema,
                cards=[_bare_rafael_card(tenant_schema, signature=None)],
                handlers={"rafael": _never_called_handler},
                audit=audit,
                facts=FactProducer(_RecordingKafkaProducer()),
                verifier=signer,
            )
        after = await _count_audit_rows(pg_dsn, tenant_schema)
        assert after == before
    finally:
        await audit.aclose()


async def test_live_tg_enforcement_tampered_card_cannot_dispatch(
    pg_dsn: str, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-G ENFORCEMENT, fail-closed negative proof: a validly-signed Card, then TAMPERED (a field
    changed after signing), is refused the same way."""
    monkeypatch.setenv("MAEZO_A2A_CARD_SIGNING_KEY", _TG_TEST_KEY)
    signer = card_signer_from_key(card_signing_key_from_env())
    assert signer is not None
    signed = signer.sign(_bare_rafael_card(tenant_schema))
    tampered = AgentCard(
        agent_id=signed.agent_id,
        version=signed.version,
        tenant=signed.tenant,
        security_zone=signed.security_zone,
        accepted_task_types=frozenset({"authorization.analyze", "clinical.decision"}),  # tampered
        signature=signed.signature,
    )

    audit = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        before = await _count_audit_rows(pg_dsn, tenant_schema)
        with pytest.raises(CardSignatureError):
            build_dispatcher(
                tenant=tenant_schema,
                cards=[tampered],
                handlers={"rafael": _never_called_handler},
                audit=audit,
                facts=FactProducer(_RecordingKafkaProducer()),
                verifier=signer,
            )
        after = await _count_audit_rows(pg_dsn, tenant_schema)
        assert after == before
    finally:
        await audit.aclose()


async def test_live_tg_enforcement_wrong_key_card_cannot_dispatch(
    pg_dsn: str, tenant_schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-G ENFORCEMENT, fail-closed negative proof: a Card signed by a DIFFERENT key than the
    assembly's verifier is refused (cross-key rejection)."""
    monkeypatch.setenv("MAEZO_A2A_CARD_SIGNING_KEY", _TG_TEST_KEY)
    from maezo.a2a import CardSigner

    verifier = card_signer_from_key(card_signing_key_from_env())
    assert verifier is not None
    foreign_signed = CardSigner(_TG_OTHER_KEY.encode("utf-8")).sign(_bare_rafael_card(tenant_schema))

    audit = PostgresAuditSink(pg_dsn, tenant_schema)
    try:
        before = await _count_audit_rows(pg_dsn, tenant_schema)
        with pytest.raises(CardSignatureError):
            build_dispatcher(
                tenant=tenant_schema,
                cards=[foreign_signed],
                handlers={"rafael": _never_called_handler},
                audit=audit,
                facts=FactProducer(_RecordingKafkaProducer()),
                verifier=verifier,
            )
        after = await _count_audit_rows(pg_dsn, tenant_schema)
        assert after == before
    finally:
        await audit.aclose()


# --- T-F terminal-outcome audit (W4): ALLOW-then-handler-error, BOTH rows, chain valid, LIVE ----


async def test_live_allow_then_handler_error_produces_both_audit_rows_chain_valid(
    pg_dsn: str, tenant_schema: str
) -> None:
    """T-F follow-up (W4): closes the completeness gap on a REAL audit chain — an ALLOWed
    delegation whose handler then raises `DelegationError` (simulating an internal cyclic
    sub-delegation attempt, exactly like the unit-level `FakeAgentHandler(subdelegate_to=...)`
    proof) produces BOTH the pre-exec ALLOW row and a distinct terminal FAILED `:outcome` row —
    and the whole tenant chain (both rows included) stays valid."""

    async def _cyclic_handler(envelope: DelegationEnvelope) -> HandlerOutput:
        envelope.extend(target="helena", task_id="subtask-cyclic-livepg-1")  # raises CyclicDelegationError
        raise AssertionError("unreachable — extend() must have raised")

    audit = PostgresAuditSink(pg_dsn, tenant_schema)
    task_id = "auth-terminal-outcome-livepg-1"
    try:
        dispatcher = build_dispatcher(
            tenant=tenant_schema,
            cards=[_bare_rafael_card(tenant_schema)],
            handlers={"rafael": _cyclic_handler},
            audit=audit,
            facts=FactProducer(_RecordingKafkaProducer()),
        )
        envelope = DelegationEnvelope.root(
            task_id=task_id,
            task_type="authorization.analyze",
            origin="helena",
            target="rafael",
            tenant=tenant_schema,
            budget=Budget(tokens=64, time_ms=60_000, cost_per_hop=1),
            payload_ref="fhir://Coverage/terminal-outcome-livepg-1",
        )

        result = await dispatcher.delegate(envelope)
        assert not result.success

        rows = await _fetch_a2a_delegate_and_outcome_rows(pg_dsn, tenant_schema, task_id=task_id)
        assert len(rows) == 2, "expected exactly the pre-exec ALLOW row and the terminal FAILED row"
        assert rows[0]["action"] == "a2a.delegate:rafael"
        assert rows[0]["decision"] == "ALLOW"
        assert rows[1]["action"] == "a2a.delegate:rafael:outcome"
        assert rows[1]["decision"] == "FAILED"

        # dedup_keys round-trip distinctly in audit_emit_dedup (0005) — never collapsed.
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(f'SET search_path TO "{tenant_schema}"')
            allow_dedup = await conn.fetchrow(
                "SELECT record_hash FROM audit_emit_dedup WHERE tenant = $1 AND dedup_key = $2",
                tenant_schema,
                a2a_audit_dedup_key(tenant_schema, task_id),
            )
            outcome_dedup = await conn.fetchrow(
                "SELECT record_hash FROM audit_emit_dedup WHERE tenant = $1 AND dedup_key = $2",
                tenant_schema,
                a2a_audit_outcome_dedup_key(tenant_schema, task_id),
            )
        finally:
            await conn.close()
        assert allow_dedup is not None
        assert outcome_dedup is not None
        assert allow_dedup["record_hash"] == rows[0]["record_hash"]
        assert outcome_dedup["record_hash"] == rows[1]["record_hash"]

        verification = await verify_chain(pg_dsn, tenant_schema)
        assert verification.valid, verification.reason
        assert verification.total_records == verification.verified_records
    finally:
        await audit.aclose()
