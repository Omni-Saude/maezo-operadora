"""Marina CONTAS live-engine acceptance (T1.12): real DMN evaluation + real process origination
against the CIB Seven engine (ADR-0011: never a mocked engine here).

SCOPE NOTE (disclosed, PR body): this suite verifies Marina's OWN engine integration — the REAL
`CibSevenDmnTransport` evaluating the deployed `glosa_reason_normalization`/`glosa_classification`/
`glosa_triage`/`contas_sla` decision tables, and the REAL `CibSevenHttpTransport` originating
`SP-OP-CONTAS-001` with a genuine ACTIVE engine instance (idempotency re-checked). It deliberately
does NOT drive the BPMN's remaining service tasks (`ST_IdentifyGlosa`/`ST_AnalyzeReason`/...) all
the way to `UT_AnalistaContas`, unlike the sibling Rafael suite
(`test_rafael_auth_dossier.py`) — attempting that surfaced a PRE-EXISTING, OUT-OF-SCOPE gap live:
`tools/workers/harness.py::CibSevenWorkerTransport.fetch_and_lock` (~line 309) reads every
external-task variable via `v.get("value")` only, WITHOUT checking `type == "Json"` and
`json.loads`-decoding it — so a contract-required list/dict variable (`linhas_conta_refs`, sent
correctly Json-encoded outbound by `mcp_cibseven/transport.py::_to_camunda_vars`) round-trips back
into `tools/workers/contas.py::identify_glosa` as a raw JSON STRING, not a Python list, crashing
with `AttributeError: 'str' object has no attribute 'get'` when the worker iterates it
(live-reproduced: business_key `CONTAS-amh-IT-MARINA-CONTAS-9ccc766f`, instance
`b57ff70e-8221-11f1-925f-162e7cd26dfd`, task `operadora.contas.identify_glosa` retried
indefinitely). This is NOT Marina-specific or CONTAS-specific — it would bite ANY worker across
the repo receiving a list/dict process variable — and `tools/workers/harness.py` is shared,
cross-cutting infrastructure explicitly out of this charter's touch-scope (worker modules
untouched). Filed as a residual finding in the PR body, not fixed here. RECURSO/REEMBOLSO live
acceptance is not attempted either, for the same reason plus their own additional setup needs
(a pre-confirmed `glosa_id` / an already-running REEMBOLSO-001 instance) — both remain
unit-tested only (`tests/unit/agents/test_marina.py`).

If the engine is unreachable, every test SKIPS via the session-scoped `_skip_if_engine_unreachable`
autouse fixture (`tests/integration/conftest.py`) — never a silent pass, never a fabricated
result (constraint 3).
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from maezo.agents.marina.graph import build
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

from ._engine_helpers import active_instances

pytestmark = pytest.mark.integration

_RUN_ID = uuid.uuid4().hex[:8]


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        assert phi is True, "Marina's dossier LLM call must be phi=True (security_zone: phi)"
        return self._responses.pop(0) if self._responses else ""


async def test_contas_assess_evaluates_real_deployed_dmn_and_originates_process(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    """Marina's `assess` evaluates the REAL, deployed `glosa_reason_normalization` ->
    `glosa_classification` -> `glosa_triage` (+ `contas_sla`) chain against the engine (never
    `FakeDmnTransport`), and `start_process` originates a genuine ACTIVE `SP-OP-CONTAS-001`
    instance. Uses reason_code `PROCEDIMENTO_NAO_INDICADO` — the deployed
    `glosa_reason_normalization` table maps it to `categoria_normalizada="tecnica"`, which
    `glosa_triage`'s `r_tecnica_humano` rule ALWAYS routes to `ANALISE_HUMANA` regardless of the
    other booleans (glosa tecnica/clinica never auto-routes, L0 hard) — a deterministic,
    non-flaky assertion that does not depend on any DRAFT threshold tuning. Deliberately omits
    `linhas_conta_refs` (empty) to sidestep the harness gap disclosed in this module's docstring
    — Marina's own `_assess_contas`/`_evaluate_dmn` never reads that field at all (only the
    downstream `identify_glosa` WORKER does), so this remains a faithful exercise of Marina's own
    code paths.
    """
    numero_lote = f"IT-MARINA-CONTAS-{_RUN_ID}"
    business_key = f"CONTAS-amh-{numero_lote}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    inference = _FakeInference(
        ["Dossie factual: glosa candidata classificada como tecnica pela DMN; analise humana obrigatoria."]
    )

    graph = build(
        {"inference": inference, "dmn": dmn, "cibseven": cibseven, "audit_sink": FakeStartAuditSink()}
    )
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "flow": "contas",
                "tenant_id": "amh",
                "canal": "portal_tiss",
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}",
                "prestador_id": "prestador-it-1",
                "numero_lote_tiss": numero_lote,
                "competencia": "2026-06",
                "valor_apresentado_brl": 500.0,
                "tipo_lote": "sadt",
                "reason_codes_tiss": ["PROCEDIMENTO_NAO_INDICADO"],
                "divergencia_valor": True,
                "item_conforme_tabela": True,
                "documentacao_anexa": True,
                "denial_ratio": 1.0,
                "indicio_fraude_sinalizado": False,
            }
        )

        # Real DMN evaluation (never a Fake): categoria_normalizada resolved by the REAL
        # glosa_reason_normalization table, and the route decided by the REAL glosa_triage table.
        assert result["categoria_normalizada"] == "tecnica"
        assert result["route"] == "human_review"
        assert result["motivo_humano"] == "triagem_analise_humana"
        assert result["grupo_humano"] == "auditoria-contas"
        # L0 hard structural guardrail — no branch of Marina's graph ever decides glosa merit.
        assert result["dossier"]["decisao_glosa"] is None

        # Auditable rule references (ADR-0007/0012) — real decision-definition ids, not fakes.
        dmn_refs = result["dmn_refs"]
        for table in (
            "glosa_reason_normalization",
            "glosa_classification",
            "glosa_triage",
            "contas_sla",
        ):
            assert table in dmn_refs, f"missing real dmn_refs entry for {table}: {dmn_refs}"
            assert dmn_refs[table].startswith(f"{table}#"), dmn_refs[table]

        assert result["process_started"] is True
        assert result["business_key"] == business_key

        actives = await active_instances(engine_client, business_key)
        assert actives, (
            f"Marina should have originated SP-OP-CONTAS-001 for business_key={business_key!r} — "
            f"none found active in the engine"
        )
        instance_id = str(actives[0]["id"])
        assert result["process_ref"]["instance_id"] == instance_id
        assert result["process_ref"]["already_existed"] is False

        # Idempotency (contract SP-OP-CONTAS-001 §Business key): a resend with the SAME state
        # returns the SAME active instance, never a duplicate.
        result_2 = await compiled.ainvoke(
            {
                "flow": "contas",
                "tenant_id": "amh",
                "canal": "portal_tiss",
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}",
                "prestador_id": "prestador-it-1",
                "numero_lote_tiss": numero_lote,
                "competencia": "2026-06",
                "valor_apresentado_brl": 500.0,
                "tipo_lote": "sadt",
                "reason_codes_tiss": ["PROCEDIMENTO_NAO_INDICADO"],
                "divergencia_valor": True,
                "item_conforme_tabela": True,
                "documentacao_anexa": True,
                "denial_ratio": 1.0,
                "indicio_fraude_sinalizado": False,
            }
        )
        assert result_2["process_ref"]["instance_id"] == instance_id
        assert result_2["process_ref"]["already_existed"] is True
    finally:
        await dmn.close()
        await cibseven.close()
