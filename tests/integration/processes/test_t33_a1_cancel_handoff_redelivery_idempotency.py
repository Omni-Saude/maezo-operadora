"""T3.3 W1 — Suite A1: INADIMPLENCIA->CANCEL re-delivery idempotency (docs/design/
T3.3-chaos-resilience.md, Class A row A1).

The ONE real fenced cross-process WORKER handoff in this codebase (design §0/§1.1):
`inadimplencia.handoff_rescisao` (`src/maezo/tools/workers/inadimplencia.py:492`) idempotently
starts SP-OP-CANCEL-001 via the shared `start_process_idempotent` chokepoint
(`mcp_cibseven/transport.py:560`) — the SAME "10th start site" precedent proven live in
`test_sp_op_inadimplencia_001.py::test_happy_path_encaminhar_rescisao_handoff_neutro_nao_rescinde`
(business key `CANCEL-{tenant}-{numero_contrato}`, #93/#108).

WHAT THIS SUITE ADDS (not already covered): a REDELIVERY assertion. The precedent test drives a
single INADIMPLENCIA-001 instance to `handoff_rescisao` exactly once. This suite calls the SAME
production worker function TWICE with byte-identical input variables — the exact shape of a
CIB Seven external-task RE-DELIVERY after a lock expires mid-flight (design §1.2's "the real
risk": engine-side start is not business-key-unique; idempotency comes entirely from the
query-then-act `find_active_instance` TOCTOU). The invariant under proof: a re-delivered handoff
converges to EXACTLY ONE active CANCEL-001 instance and EXACTLY ONE `start:SP-OP-CANCEL-001:...`
audit chain link (never a double-effect, never a double-audit).

HARNESS: placed under `tests/integration/processes/` (NOT `tests/integration/chaos/`) because A1
needs the REAL CIB Seven engine — unlike the PG-only seam-fault suites in
`tests/integration/chaos/` (B1a/B1b/C1-down), which explicitly override the engine-reachability
skip and never touch a live engine. This file reuses this package's OWN `engine`/`drain_topics`
fixtures and the parent `tests/integration/conftest.py`'s `audit_pg`/`audit_tenant` fixtures —
zero new fixture machinery (T3.3 harness-reuse mandate). It runs in the `integration` CI job
(real engine + real Postgres), NEVER the PG-only `chaos` job (`ci.yml`'s `chaos` job starts
ONLY postgres — an engine-dependent test collected there would fail/skip for the wrong reason).

`handoff_rescisao` is a SYNCHRONOUS function that internally bridges to the engine via its own
fresh `asyncio.run(...)` (module docstring, cibseven_engine.py) — invoking it directly from an
`async def` test would raise `RuntimeError: asyncio.run() cannot be called from a running event
loop`. `asyncio.to_thread(...)` is used here to dispatch it on a fresh OS thread — the EXACT
mechanism the real worker harness uses (`WorkerHarness._handle` -> `asyncio.to_thread`,
`cibseven_engine.py` module docstring) — so this is a faithful re-delivery simulation, not a
synthetic shortcut.

MUTATION-CHECK (design §4 verification protocol, shared A1/A2 row): gated behind
`MAEZO_CHAOS_MUTATE=a1_a2` (`tests/integration/chaos/mutations.py`, extended by this branch to
add `broken_start_process_always_start` — the same chokepoint every A1/A2 call site shares, so
ONE mutation covers both suites per the design's own table). Run explicitly to PROVE
non-vacuity:

    MAEZO_CHAOS_MUTATE=a1_a2 uv run pytest \\
        tests/integration/processes/test_t33_a1_cancel_handoff_redelivery_idempotency.py -k mutation -q

That run is EXPECTED TO FAIL (the broken variant creates a SECOND active CANCEL-001 instance) —
the failure IS the proof this suite would catch the same defect in the real code.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.audit_postgres import FreshSinkAuditEmitter, verify_chain
from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport
from maezo.tools.workers.inadimplencia import handoff_rescisao
from tests.integration.chaos.mutations import broken_start_process_always_start, mutation_active

from .conftest import CIBSEVEN_BASE_URL, count_chain_rows
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]

_CANCEL_ARTIFACTS = (
    _REPO / "spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn",
    _REPO / "spec/processes/dmn/cancel_admissibility.dmn",
    _REPO / "spec/processes/dmn/cancel_routing.dmn",
    _REPO / "spec/processes/dmn/cancel_sla.dmn",
)


def _unique_contrato() -> str:
    return f"CONTRATO-TESTE-T33A1-{uuid.uuid4().hex[:8].upper()}"


def _redelivery_variables(*, tenant_id: str, numero_contrato: str) -> dict[str, Any]:
    """The variable shape `handoff_rescisao` sees on ENCAMINHAR_RESCISAO (mirrors
    `test_sp_op_inadimplencia_001.py`'s `_CAMPOS_SUSP` + base INAD instance vars) — built
    directly here (not via a running INADIMPLENCIA-001 instance) because the point of THIS suite
    is the re-delivery of the handoff itself, not re-proving the BPMN drives to the same point
    (already proven by `test_happy_path_encaminhar_rescisao_handoff_neutro_nao_rescinde`)."""
    return {
        "decisao_inadimplencia": "ENCAMINHAR_RESCISAO",
        "tenant_id": tenant_id,
        "numero_contrato": numero_contrato,
        "matricula_beneficiario": "BENEF-TESTE-0001",
        "tipo_plano": "individual",
        "notificacao_previa_feita": True,
        "fundamentacao_contratual": "Fundamentacao sintetica da rescisao (T3.3 A1 teste)",
        "referencia_regulatoria": "RN 593 — DRAFT/verify (teste)",
        "comprovacao_notificacao_previa": "ref-comprovante-notificacao-teste-t33-a1",
        "comprovacao_periodo_minimo": "ref-comprovacao-periodo-minimo-teste-t33-a1",
        "responsavel_id": "juridico-sintetico-t33-a1",
        "documentos_refs": "[]",
    }


async def test_a1_redelivered_handoff_creates_exactly_one_cancel_instance_and_audit_link(
    engine: EngineRest,
    audit_pg: tuple[str, str],
    audit_tenant: str,
) -> None:
    """GREEN: re-running `handoff_rescisao` with byte-identical variables (simulated
    external-task redelivery) must converge to exactly one active CANCEL-001 instance and add
    exactly one durable audit chain link overall (the second call must add ZERO new links)."""
    await engine.deploy(*_CANCEL_ARTIFACTS, name="SP-OP-CANCEL-001-qa-t33-a1")

    tenant_id = "amh"
    contrato = _unique_contrato()
    cancel_bk = f"CANCEL-{tenant_id}-{contrato}"
    variables = _redelivery_variables(tenant_id=tenant_id, numero_contrato=contrato)

    engine_seam = FreshClientCibSevenTransport(CIBSEVEN_BASE_URL)
    handoff_sink = FreshSinkAuditEmitter(audit_pg[0], audit_tenant)

    # Pre-condition: no leftover instance under this (fresh, uuid-suffixed) business key.
    assert not await engine.find_active_instances(cancel_bk)

    chain_before = await count_chain_rows(audit_pg[0], audit_tenant)

    # First delivery.
    result1 = await asyncio.to_thread(
        handoff_rescisao, dict(variables), engine=engine_seam, audit_sink=handoff_sink
    )
    chain_after_first = await count_chain_rows(audit_pg[0], audit_tenant)

    # Re-delivery: SAME variables, SAME business key (the external-task lock-expiry scenario).
    result2 = await asyncio.to_thread(
        handoff_rescisao, dict(variables), engine=engine_seam, audit_sink=handoff_sink
    )
    chain_after_second = await count_chain_rows(audit_pg[0], audit_tenant)

    assert result1["handoff_executado"] is True
    assert result2["handoff_executado"] is True
    assert result1["cancel_business_key"] == cancel_bk
    assert result2["cancel_business_key"] == cancel_bk

    # Exactly-once START: first call is a genuine start, the re-delivery is an idempotent hit —
    # NOT a second start (the CANCEL-001 anti-dupla / A1 core invariant).
    assert result1["cancel_already_existed"] is False, "first delivery must be a genuine start"
    assert result2["cancel_already_existed"] is True, "re-delivery must hit the SAME active instance"
    assert result1["cancel_instance_id"] == result2["cancel_instance_id"], (
        "re-delivery must resolve to the identical CANCEL-001 instance id, never a second one"
    )

    # Exactly-once AUDIT: the first call adds exactly one durable chain link; the re-delivery
    # (dedup key `{tenant}:start:SP-OP-CANCEL-001:{cancel_bk}` collides) adds ZERO new links.
    assert chain_after_first - chain_before == 1, "first delivery must add exactly one audit_chain row"
    assert chain_after_second - chain_after_first == 0, (
        "re-delivery must add ZERO new audit_chain rows (emit_once dedup, exactly-once emit)"
    )

    # Engine-history proof: exactly ONE ACTIVE CANCEL-001 instance for this business key.
    active = await engine.find_active_instances(cancel_bk)
    assert len(active) == 1, (
        f"exactly one ACTIVE CANCEL-001 instance expected for {cancel_bk!r}, found {len(active)}"
    )
    assert str(active[0]["id"]) == result1["cancel_instance_id"]

    # Audit-sink proof: the durable chain (genesis-recomputed, not trusted from stored flags)
    # remains valid — no gap/fork introduced by the redelivery.
    verification = await verify_chain(audit_pg[0], audit_tenant)
    assert verification.valid, f"audit chain must remain valid after redelivery: {verification.reason}"


@pytest.mark.skipif(
    not mutation_active("a1_a2"),
    reason="only runs when MAEZO_CHAOS_MUTATE=a1_a2 (see module docstring for the explicit run command)",
)
async def test_a1_mutation_check_broken_idempotency_creates_a_second_instance(
    engine: EngineRest,
    audit_pg: tuple[str, str],
    audit_tenant: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION-CHECK (must FAIL when run): patches `inadimplencia.start_process_idempotent` to
    the A1/A2 broken variant (always-start, `find_active_instance` skipped) — the SAME
    re-delivery that converges to one instance in the green test above must now create a SECOND
    active CANCEL-001 instance, turning the "exactly one instance" assertion red. This is the
    non-vacuity proof: it demonstrates the green test above WOULD have caught this defect."""
    import maezo.tools.workers.inadimplencia as inadimplencia_module

    monkeypatch.setattr(inadimplencia_module, "start_process_idempotent", broken_start_process_always_start)

    await engine.deploy(*_CANCEL_ARTIFACTS, name="SP-OP-CANCEL-001-qa-t33-a1-mutate")

    tenant_id = "amh"
    contrato = _unique_contrato()
    cancel_bk = f"CANCEL-{tenant_id}-{contrato}"
    variables = _redelivery_variables(tenant_id=tenant_id, numero_contrato=contrato)

    engine_seam = FreshClientCibSevenTransport(CIBSEVEN_BASE_URL)
    handoff_sink = FreshSinkAuditEmitter(audit_pg[0], audit_tenant)

    await asyncio.to_thread(handoff_rescisao, dict(variables), engine=engine_seam, audit_sink=handoff_sink)
    await asyncio.to_thread(handoff_rescisao, dict(variables), engine=engine_seam, audit_sink=handoff_sink)

    active = await engine.find_active_instances(cancel_bk)
    # EXPECTED TO FAIL under the mutation: the broken variant never checks find_active_instance,
    # so the re-delivery starts a SECOND instance — proving the real code's guard is load-bearing.
    assert len(active) == 1, (
        f"MUTATION EXPECTED TO TURN THIS RED: exactly one ACTIVE CANCEL-001 instance expected, "
        f"found {len(active)} (the broken always-start variant should have created a duplicate)"
    )
