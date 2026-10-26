"""OP21 migration 0030 live: the closed anti-custody shape and the reversible round trip.

Real Alembic chain over a scratch tenant schema on the compose Postgres — no simulated DDL,
no reconstructed state (mold: `tests/unit/portal/test_commission_policies_migration_live_pg.py`).
Proofs (desenho §7, prova 5 — shard `gateway-platform`):

- `upgrade head` applies 0030 on top of the whole linear chain and the store round-trips a
  full receipt: criterion→result→typed reference (kind + locator + document_sha256) persist
  and read back EXACTLY, through `PostgresLegitimacyScreenStore` over the migrated schema;
- DOCUMENT BYTES ARE REFUSED LOUD at the DATABASE level: an extra `content` key inside an
  evidence ref, an over-teta locator, a non-hex digest, a canal-as-custodian `kind` and an
  imprecise citation form ("RN 659/2025, art. 23-A" / "RN 557/659") in a persisted anchor
  ALL violate the closed-shape CHECK — the table has no slot where a document could live;
- `status` is pinned to the closed enum: any other state violates the CHECK, on INSERT;
- the idempotency axes are structural: the same instance PK round-trips idempotently, and a
  second `screen_ref` over the same (opportunity, revision) hits the UNIQUE — refusal, never
  a second trail;
- the art. 39 anchor is a biconditional with the literal "RN 557/2022, art. 39" at the DB;
- `downgrade 0029` drops the table AND the shape function and a second `upgrade head`
  restores them — the discardable round trip the dark substrate promises.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from maezo.gateway.commercial_legitimacy_screen import (
    ART39_ANCHOR,
    LegitimacyCriterion,
    LegitimacyEvidenceRef,
    LegitimacyScreenRecord,
    LegitimacyScreenStoreError,
    PostgresLegitimacyScreenStore,
)
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture(autouse=True)
def _skip_if_postgres_unreachable() -> None:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL / "
            "MAEZO_PG_HOST_PORT) — OP21 legitimacy-screen migration live tests SKIPPED (visible, "
            "not silent). Start it with `docker compose --profile core up -d postgres` and re-run "
            "this file."
        )


async def _migrate(dsn: str, tenant: str, direction: str, revision: str) -> None:
    """The real CLI/env.py with the tenant schema; URL only in the child environment."""
    env = dict(os.environ, ALEMBIC_DATABASE_URL=dsn)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-x",
        f"tenant={tenant}",
        direction,
        revision,
        cwd=_ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 300)
        assert process.returncode == 0, output.decode(errors="replace")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def _recibo(tenant: str, screen_ref: str, revision: int, opportunity: str) -> LegitimacyScreenRecord:
    """Um recibo completo: critério provado com referência à custódia da operadora + art. 39."""
    now = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
    return LegitimacyScreenRecord(
        tenant=tenant,
        screen_ref=screen_ref,
        screen_revision=revision,
        legal_entity_ref="pj-contratante-op21",
        opportunity_ref=opportunity,
        contract_kind="empresarial",
        contracting_form="direta",
        criteria=(
            LegitimacyCriterion(
                criterion_id="tipo_e_forma_contratacao",
                result="proven",
                norm_anchor="RN 557/2022, art. 29",
                evidence_refs=(
                    LegitimacyEvidenceRef(
                        kind="operadora",
                        locator="custodia/2026/contrato-pj-1.pdf",
                        document_sha256="a" * 64,
                    ),
                ),
            ),
            LegitimacyCriterion(
                criterion_id="vinculo_empresarial",
                result="proven",
                norm_anchor="RN 557/2022, art. 5º",
                evidence_refs=(
                    LegitimacyEvidenceRef(
                        kind="administradora",
                        locator="adm/folhas/2026-10.csv",
                        document_sha256="b" * 64,
                    ),
                ),
            ),
            LegitimacyCriterion(
                criterion_id="penalidade_regime",
                result="not_applicable",
                norm_anchor="RN 489/2022, art. 23-A (redação dada pela RN 659/2025)",
                evidence_refs=(),
            ),
        ),
        status="valid",
        would_block=False,
        art39_signaled=True,
        art39_anchor=ART39_ANCHOR,
        reconfirmation_due_at=datetime(2027, 10, 26, 12, 0, 0, tzinfo=UTC),
        created_by="staff-op21",
        created_at=now,
        updated_at=now,
    )


async def test_forma_fechada_anti_custodia_e_ida_e_volta_reversivel() -> None:
    dsn = _default_test_dsn()
    tenant = "pf" + uuid.uuid4().hex[:20]
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE SCHEMA "{tenant}"')
    finally:
        await admin.close()
    try:
        await _migrate(dsn, tenant, "upgrade", "head")
        url = _default_test_dsn().replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(
            url, echo=False, hide_parameters=True, connect_args={"server_settings": {"search_path": tenant}}
        )
        store = PostgresLegitimacyScreenStore(engine)
        raw = await asyncpg.connect(dsn)
        try:
            await raw.execute(f'SET search_path TO "{tenant}"')

            # ── Prova 5a: critério→resultado→referência persiste e lê de volta EXATO ──
            ref1 = "screen-" + "a" * 16
            saved = await store.save(_recibo(tenant, ref1, 1, "opportunity-1"))
            read = await store.read(tenant, ref1, 1)
            assert read is not None and read == saved
            assert [c.result for c in read.criteria] == ["proven", "proven", "not_applicable"]
            assert read.criteria[0].evidence_refs[0].kind == "operadora"
            assert read.criteria[0].evidence_refs[0].document_sha256 == "a" * 64
            assert read.criteria[2].norm_anchor == "RN 489/2022, art. 23-A (redação dada pela RN 659/2025)"
            assert read.art39_anchor == "RN 557/2022, art. 39"
            latest_pj = await store.latest_for_legal_entity(tenant, "pj-contratante-op21")
            assert latest_pj is not None and latest_pj.screen_ref == ref1
            assert await store.read(tenant, "screen-" + "b" * 16, 1) is None  # ausência = None

            # ── Prova 5b: bytes de documento RECUSADOS LOUD no banco (anti-custódia) ──
            base = json.loads(_recibo(tenant, ref1, 1, "opportunity-1").model_dump_json())
            base["tenant"] = tenant

            async def recusado(criteria: list[dict[str, object]]) -> None:
                row = dict(base)
                row["criteria"] = json.dumps(criteria, ensure_ascii=False)
                row["screen_ref"] = "screen-" + uuid.uuid4().hex[:16]
                row["opportunity_ref"] = "opportunity-" + uuid.uuid4().hex[:8]
                # `match` prova que a CHECK da FORMA (criteria) é a que disparou — não outra.
                with pytest.raises(
                    asyncpg.CheckViolationError, match="portal_legitimacy_screens_criteria_check"
                ):
                    await raw.execute(
                        "INSERT INTO portal_legitimacy_screens (tenant, screen_ref, screen_revision,"
                        " legal_entity_ref, opportunity_ref, contract_kind, contracting_form, criteria,"
                        " status, would_block, art39_signaled, art39_anchor, reconfirmation_due_at,"
                        " created_by, created_at, updated_at)"
                        " VALUES ($1, $2, 1, $3, $4, 'empresarial', 'direta', CAST($5 AS jsonb),"
                        " 'valid', false, false, NULL, NULL, 'staff-op21', now(), now())",
                        row["tenant"],
                        row["screen_ref"],
                        row["legal_entity_ref"],
                        row["opportunity_ref"],
                        row["criteria"],
                    )

            await recusado([dict(base["criteria"][0], content="JVBERi0xLjcgYnl0ZXMgZG8gY29udHJhdG8")])
            # ↑ conteúdo de documento não cabe: chave extra na referência
            await recusado([dict(base["criteria"][0], norm_anchor="RN 659/2025, art. 23-A")])
            # ↑ forma de citação imprecisa (parecer §1)
            await recusado([dict(base["criteria"][0], norm_anchor="RN 557/659")])
            # ↑ normas juntas é impreciso (parecer §4)
            await recusado(
                [
                    dict(
                        base["criteria"][0],
                        evidence_refs=[dict(base["criteria"][0]["evidence_refs"][0], locator="x" * 257)],
                    )
                ]
            )
            # ↑ locator acima do teto — a cerca de abuso (desenho §3)
            await recusado(
                [
                    dict(
                        base["criteria"][0],
                        evidence_refs=[dict(base["criteria"][0]["evidence_refs"][0], document_sha256="zz")],
                    )
                ]
            )
            # ↑ digest não é hex-64
            await recusado(
                [
                    dict(
                        base["criteria"][0],
                        evidence_refs=[dict(base["criteria"][0]["evidence_refs"][0], kind="corretor")],
                    )
                ]
            )
            # ↑ canal nunca é custodiante (parecer §3)
            await recusado([])  # bloco de critérios vazio não é verificação

            # ── Prova 5c: o PIN do estado — nenhum outro estado é persistível ──
            with pytest.raises(asyncpg.CheckViolationError, match="portal_legitimacy_screens_status_check"):
                await raw.execute(
                    "INSERT INTO portal_legitimacy_screens (tenant, screen_ref, screen_revision,"
                    " legal_entity_ref, opportunity_ref, contract_kind, contracting_form, criteria,"
                    " status, would_block, art39_signaled, art39_anchor, reconfirmation_due_at,"
                    " created_by, created_at, updated_at)"
                    " VALUES ($1, 'screen-' || repeat('c', 16), 1, 'pj-2', 'opp-2', 'adesao',"
                    " 'administradora', CAST($2 AS jsonb), 'approved', false, false, NULL, NULL,"
                    " 'staff-op21', now(), now())",
                    tenant,
                    json.dumps([base["criteria"][0]], ensure_ascii=False),
                )
            with pytest.raises(asyncpg.CheckViolationError, match="portal_legitimacy_screens_status_check"):
                await raw.execute(
                    "UPDATE portal_legitimacy_screens SET status = 'expired' WHERE tenant = $1", tenant
                )

            # ── Prova 5d: a sinalização do art. 39 é par exclusivo no BANCO ──
            with pytest.raises(
                asyncpg.CheckViolationError, match="portal_legitimacy_screens_art39_par_exclusivo"
            ):
                await raw.execute(
                    "UPDATE portal_legitimacy_screens SET art39_anchor = 'RN 659/2025, art. 39'"
                    " WHERE tenant = $1",
                    tenant,
                )

            # ── Prova 5e: idempotência estrutural nos dois eixos ──
            again = await store.save(_recibo(tenant, ref1, 1, "opportunity-1"))
            assert again == saved  # reenvio = mesma linha, nunca segunda instância
            dupla = _recibo(tenant, "screen-" + "d" * 16, 1, "opportunity-1")  # mesma opp+revisão
            with pytest.raises(LegitimacyScreenStoreError) as exc:
                await store.save(dupla)
            assert exc.value.code == "contract_mismatch"
        finally:
            await raw.close()
            await engine.dispose()

        # ── Prova 5f: downgrade descartável e ida e volta reversível ──
        await _migrate(dsn, tenant, "downgrade", "0029")
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'SET search_path TO "{tenant}"')
            gone = await conn.fetchval("SELECT to_regclass('portal_legitimacy_screens')")
            assert gone is None, "downgrade must drop the dark substrate table"
            fn = await conn.fetchval("SELECT to_regproc('portal_legitimacy_criteria_ok')")
            assert fn is None, "downgrade must drop the closed-shape function too"
        finally:
            await conn.close()
        await _migrate(dsn, tenant, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'SET search_path TO "{tenant}"')
            back = await conn.fetchval("SELECT to_regclass('portal_legitimacy_screens')")
            assert back is not None, "the round trip must restore the substrate"
        finally:
            await conn.close()
    finally:
        admin = await asyncpg.connect(dsn)
        try:
            await admin.execute(f'DROP SCHEMA "{tenant}" CASCADE')
        finally:
            await admin.close()
