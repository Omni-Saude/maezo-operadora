"""Rafael E2E acceptance (T1.11, defect B6 — G1 gate): auth request -> Rafael -> dossier ->
`UT_AnaliseMedicoAuditor` user task visible for the `medico-auditor` candidate group, in the
REAL engine (ADR-0011: never a mocked engine here).

Runs Rafael's REAL graph (`agents.rafael.graph.build`) with the REAL `CibSevenDmnTransport`
(`auth_admissibility`/`auth_sla`/`auth_auto_approval`, engine-side) and the REAL
`CibSevenHttpTransport` (starting `SP-OP-AUTH-001`). Between `GW_AutoAprovacao`'s default branch
and `UT_AnaliseMedicoAuditor`, the BPMN's OWN `ST_PrepararDossie` external service task
(`operadora.auth.analyze_request`) must be serviced for the process to ever reach the user task —
this suite runs the REAL, UNMODIFIED `AnalyzeRequestWorker` (via `register_auth_workers`,
`tools/workers/auth.py` — forbidden territory for this charter, used here strictly as-is, never
edited) through a lightweight `WorkerHarness`/`CibSevenWorkerTransport` probe, mirroring
`tests/integration/test_worker_runtime_spine.py`'s own pattern for driving a real external task
against the real engine.

Rafael's own `dentro_teto_l2` is a state field HE CONSUMES, never computes (contract SP-OP-
AUTH-001 + `graph.py`'s module docstring) — this suite sets it `False`, which also matches the
REAL tenant ceiling (`tenants-amh.yaml`: `authorization_approval.max_value_brl: 0` — nothing is
"within" a zero ceiling), so the human-review path exercised here is not an artificial choice.

If the engine is unreachable, every test SKIPS via the session-scoped `_skip_if_engine_unreachable`
autouse fixture (`tests/integration/conftest.py`) — never a silent pass, never a fabricated
result (constraint 3).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from maezo.agents.rafael.graph import build
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.workers.auth import register_auth_workers
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.harness import CibSevenWorkerTransport, WorkerHarness

from ._engine_helpers import active_instances, candidate_groups, noop_events_publish, wait_for_task

pytestmark = pytest.mark.integration

_RUN_ID = uuid.uuid4().hex[:8]


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        assert phi is True, "Rafael's dossier LLM call must be phi=True (security_zone: phi)"
        return self._responses.pop(0) if self._responses else ""


@pytest.fixture
async def auth_worker_probe(engine_base_url: str, audit_sink: Any, audit_tenant: str) -> AsyncIterator[None]:
    """A background `WorkerHarness` servicing the REAL `SP-OP-AUTH-001` external tasks
    (`operadora.auth.*`, `tools/workers/auth.py` — unmodified) so the BPMN can actually progress
    from `ST_PrepararDossie` to `UT_AnaliseMedicoAuditor` during this test. Mirrors
    `tests/integration/test_worker_runtime_spine.py`'s own probe pattern."""
    transport = CibSevenWorkerTransport(engine_base_url, timeout=30.0)
    harness = WorkerHarness(
        transport,
        worker_id=f"it-rafael-auth-probe-{_RUN_ID}",
        tenant=audit_tenant,
        audit_sink=audit_sink,
        async_response_timeout_ms=5_000,
        # Fast local polling for test turnaround — see the sibling Helena suite's identical
        # tuning note (`test_helena_escalation.py`).
        poll_interval_ms=250,
    )
    register_auth_workers(harness)
    harness.register("operadora.events.publish", noop_events_publish)
    run_task = asyncio.create_task(harness.run(), name="rafael-it-auth-probe")
    try:
        yield
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
        await transport.close()


async def test_human_review_auth_request_reaches_medico_auditor_task(
    engine_base_url: str, engine_client: httpx.AsyncClient, auth_worker_probe: None, audit_sink: Any
) -> None:
    """The G1 gate's Rafael acceptance target: 'Rafael dossier task appears for medico-auditor'
    — verified as a REAL User Task, `medico-auditor` candidate group, in the real engine."""
    numero_guia = f"IT-RAFAEL-{_RUN_ID}"
    business_key = f"AUTH-amh-{numero_guia}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    inference = _FakeInference(
        [
            "Dossie factual: procedimento eletivo, documentacao completa; "
            "fora do teto de aprovacao automatica do tenant."
        ]
    )

    graph = build({"inference": inference, "dmn": dmn, "cibseven": cibseven, "audit_sink": audit_sink})
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "tenant_id": "amh",
                "numero_guia_tiss": numero_guia,
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}",
                "prestador_id": "prestador-it-1",
                "canal": "portal_tiss",
                "codigo_procedimento_tuss": "10101012",
                "categoria_procedimento": "consulta",
                "carater_atendimento": "eletivo",
                "valor_estimado_brl": 500.0,
                "documentos_refs": [],
                "requer_autorizacao": True,
                "documentacao_completa": True,
                "beneficiario_ativo": True,
                "carencia_cumprida": True,
                "dut_atendida": True,
                "dentro_teto_l2": False,  # matches the real tenant ceiling (max_value_brl: 0)
                "rede_credenciada": True,
            }
        )

        assert result["route"] == "human_auditor"
        assert result["process_started"] is True
        assert result["business_key"] == business_key
        assert result["dossier"]["decisao_cobertura"] is None  # L0 hard structural guardrail

        actives = await active_instances(engine_client, business_key)
        assert actives, (
            f"Rafael should have originated SP-OP-AUTH-001 for business_key={business_key!r} — "
            f"none found active in the engine"
        )
        instance_id = str(actives[0]["id"])

        task = await wait_for_task(engine_client, instance_id, attempts=40)
        assert task["name"] == "Analise do medico auditor"
        groups = await candidate_groups(engine_client, task["id"])
        assert groups == {"medico-auditor"}
    finally:
        await dmn.close()
        await cibseven.close()


async def test_auto_approve_path_starts_process_without_reaching_human_task(
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any
) -> None:
    """Structural counterpoint (no worker probe needed — `GW_AutoAprovacao` routes straight to
    `ST_EmitirAutorizacaoAuto`, bypassing `ST_PrepararDossie`/`UT_AnaliseMedicoAuditor`
    entirely): Rafael's own `route` is `auto_approve`, and the process starts, when the DMN says
    AUTO_APROVAR. This does NOT assert the auto-issuance completes (that needs
    `operadora.auth.issue_authorization` serviced, out of this suite's scope) — only that Rafael
    correctly originates the process for the L2 path without ever touching the human task."""
    numero_guia = f"IT-RAFAEL-AUTO-{_RUN_ID}"
    business_key = f"AUTH-amh-{numero_guia}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    inference = _FakeInference(["Dossie factual: dentro dos criterios de aprovacao automatica."])

    graph = build({"inference": inference, "dmn": dmn, "cibseven": cibseven, "audit_sink": audit_sink})
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "tenant_id": "amh",
                "numero_guia_tiss": numero_guia,
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}-auto",
                "prestador_id": "prestador-it-1",
                "canal": "portal_tiss",
                "codigo_procedimento_tuss": "10101012",
                "categoria_procedimento": "consulta",
                "carater_atendimento": "eletivo",
                "valor_estimado_brl": 100.0,
                "documentos_refs": [],
                "requer_autorizacao": True,
                "documentacao_completa": True,
                "beneficiario_ativo": True,
                "carencia_cumprida": True,
                "dut_atendida": True,
                "dentro_teto_l2": True,
                "rede_credenciada": True,
            }
        )

        assert result["route"] == "auto_approve"
        assert result["process_started"] is True
        assert result["dossier"]["decisao_cobertura"] is None

        actives = await active_instances(engine_client, business_key)
        assert actives
    finally:
        await dmn.close()
        await cibseven.close()
