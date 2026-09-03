"""T3.3 W1 — Suite A2: agent own-start idempotency matrix (docs/design/T3.3-chaos-resilience.md,
Class A row A2).

Every agent graph starts its own downstream BPMN process through the IDENTICAL chokepoint —
`start_process_idempotent` (`maezo.tools.mcp_cibseven.transport.py:1212` — its `def`) — never
`start_process_instance` directly (module docstring: "the SINGLE agent-side effect chokepoint").
Grepping every `start_process_idempotent(` call site under `src/maezo/agents/` gives the full
enumeration this suite proves a representative-and-complete matrix against:

    rafael      -> SP-OP-AUTH-001          (agents/rafael/graph.py:509)
    helena      -> SP-OP-ESCALATION-001    (agents/helena/graph.py:673)
    lucas       -> SP-OP-ESCALATION-001    (agents/lucas/graph.py:639 — SAME process/key format
                                             as helena; see the format-parity test below)
    gustavo     -> SP-OP-ANS-SUBMIT-001    (agents/gustavo/graph.py:697, fluxo=ans_submit)
    gustavo     -> SP-OP-NIP-001           (agents/gustavo/graph.py:697, fluxo=nip)
    valentina   -> SP-OP-PROGRAMA-001      (agents/valentina/graph.py:648)
    marina      -> SP-OP-CONTAS-001        (agents/marina/graph.py:704, flow=contas)
    marina      -> SP-OP-RECURSO-001       (agents/marina/graph.py:704, flow=recurso)
    andre       -> SP-OP-PAGTO-001         (agents/andre/graph.py:986, flow=pagto_dossier)
    carolina    -> SP-OP-CRED-001          (agents/carolina/graph.py:626)
    fernando    -> SP-OP-INADIMPLENCIA-001 (agents/fernando/graph.py:560 — the agent's OWN
                                             submission start; DISTINCT from A1's
                                             inadimplencia-WORKER handoff into CANCEL-001)

10 distinct process starts across 9 agents (every one of every agent's `start_process` graph
node is a THIN wrapper: derive `business_key`/`variables`/`provenance` from state, then call the
chokepoint — verified by reading every call site before writing this suite). The guarantee under
test lives ENTIRELY in the shared chokepoint, not in any per-agent orchestration logic (which
each family's own `tests/integration/processes/test_sp_op_*_001.py` suite already covers
end-to-end) — so this suite calls `start_process_idempotent` DIRECTLY with each agent's REAL
`process_key` constant and REAL, production `_business_key` derivation function (imported from
`src/`, never re-implemented — zero format drift risk), against the REAL deployed spec BPMN for
each process. `variables={}` is deliberate and verified-safe: every one of these 10 BPMNs' first
flow node after its start event is a generic `ST_Publish*` external service task whose
`camunda:inputOutput` parameters are STATIC STRING LITERALS (never a `${expression}`), so no
synchronous gateway/script evaluation can fail during the start call regardless of which process
variables are present (confirmed by inspecting each BPMN's XML before writing this suite).

GAP-D3-02 SCOPE NOTE (checked, not assumed). `SP-OP-CANCEL-001` became a GATED
(`StartDedupPosture.EXCLUSIVE`) start-dedup family, and it is deliberately ABSENT from this
matrix: CANCEL-001 has no AGENT start site — it is started by WORKERS
(`inadimplencia.handoff_rescisao`, `fraude.start_contratual`) and by the bridge's FRAUDE→CANCEL
rule, which is A1's territory, not this suite's "every agent's own `start_process` node"
enumeration. The one gated family this matrix DOES drive is `andre_pagto` (`PERMANENT`), and its
expectations are unchanged: the second same-key start lands on the still-ACTIVE instance
(`ALREADY_ACTIVE`), which is the branch both gated postures share. The `CibSevenHttpTransport` /
`PostgresAuditSink` pair this suite injects satisfies both gate seams.

HARNESS: placed under `tests/integration/processes/` for the same reason as A1 (real engine
required; see that file's module docstring) — reuses this package's `engine` fixture and the
parent `audit_sink`/`audit_pg`/`audit_tenant` fixtures verbatim, zero new fixture machinery.
Runs in the `integration` CI job (real engine + real Postgres), never the PG-only `chaos` job.

MUTATION-CHECK: shares the SAME `MAEZO_CHAOS_MUTATE=a1_a2` mutation as A1
(`tests/integration/chaos/mutations.py::broken_start_process_always_start`) — the design's own
table lists "A1/A2" as a single row sharing one mutation, since both funnel through the
identical chokepoint. ONE representative mutation-check test (rafael/AUTH) is enough to prove
non-vacuity for the whole matrix — the chokepoint is not per-process logic, so breaking it once
breaks it everywhere; parametrizing the mutation-check across all 10 entries would just be
repeating the same proof. Run explicitly:

    MAEZO_CHAOS_MUTATE=a1_a2 uv run pytest \\
        tests/integration/processes/test_t33_a2_agent_start_idempotency_matrix.py -k mutation -q

That run is EXPECTED TO FAIL — the failure IS the proof.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import maezo.tools.mcp_cibseven.transport as _transport_module
from maezo.agents.andre.graph import PROCESS_KEY_PAGTO as _PAGTO_PROCESS_KEY
from maezo.agents.andre.graph import _business_key as _andre_business_key
from maezo.agents.carolina.graph import PROCESS_KEY as _CRED_PROCESS_KEY
from maezo.agents.carolina.graph import _business_key as _carolina_business_key
from maezo.agents.fernando.graph import PROCESS_KEY as _INAD_PROCESS_KEY
from maezo.agents.fernando.graph import _business_key as _fernando_business_key
from maezo.agents.gustavo.graph import PROCESS_KEY_ANS_SUBMIT as _ANS_SUBMIT_PROCESS_KEY
from maezo.agents.gustavo.graph import PROCESS_KEY_NIP as _NIP_PROCESS_KEY
from maezo.agents.gustavo.graph import _business_key as _gustavo_business_key
from maezo.agents.helena.graph import PROCESS_KEY as _ESCALATION_PROCESS_KEY
from maezo.agents.helena.graph import _business_key as _helena_business_key
from maezo.agents.lucas.graph import PROCESS_KEY as _LUCAS_PROCESS_KEY
from maezo.agents.lucas.graph import _business_key as _lucas_business_key
from maezo.agents.marina.graph import PROCESS_KEY_CONTAS as _CONTAS_PROCESS_KEY
from maezo.agents.marina.graph import PROCESS_KEY_RECURSO as _RECURSO_PROCESS_KEY
from maezo.agents.marina.graph import _business_key as _marina_business_key
from maezo.agents.rafael.graph import PROCESS_KEY as _AUTH_PROCESS_KEY
from maezo.agents.rafael.graph import _business_key as _rafael_business_key
from maezo.agents.valentina.graph import PROCESS_KEY_PROGRAMA as _PROGRAMA_PROCESS_KEY
from maezo.agents.valentina.graph import _business_key as _valentina_business_key
from maezo.tools.mcp_cibseven.transport import AgentDecisionProvenance, CibSevenHttpTransport, ProcessInstance
from tests.integration.chaos.mutations import broken_start_process_always_start, mutation_active

from .conftest import CIBSEVEN_BASE_URL, count_chain_rows
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN_DIR = _REPO / "spec/processes/bpmn"
_DMN_DIR = _REPO / "spec/processes/dmn"


@dataclass(frozen=True)
class _MatrixEntry:
    label: str
    agent_id: str
    process_key: str
    deploy_paths: tuple[Path, ...]
    deploy_name: str
    state_factory: Callable[[str], dict[str, Any]]
    business_key_fn: Callable[[dict[str, Any]], str]


MATRIX: tuple[_MatrixEntry, ...] = (
    _MatrixEntry(
        label="rafael_auth",
        agent_id="rafael",
        process_key=_AUTH_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-AUTH-001_Autorizacao_Previa.bpmn",
            _DMN_DIR / "auth_admissibility.dmn",
            _DMN_DIR / "auth_auto_approval.dmn",
            _DMN_DIR / "auth_sla.dmn",
        ),
        deploy_name="SP-OP-AUTH-001-qa-t33-a2",
        state_factory=lambda disc: {"tenant_id": "amh", "numero_guia_tiss": f"GUIA-T33A2-{disc}"},
        business_key_fn=_rafael_business_key,
    ),
    _MatrixEntry(
        label="helena_escalation",
        agent_id="helena",
        process_key=_ESCALATION_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn",
            _DMN_DIR / "escalation_routing.dmn",
        ),
        deploy_name="SP-OP-ESCALATION-001-qa-t33-a2",
        state_factory=lambda disc: {"tenant_id": "amh", "conversation_id": f"wa:amh:t33a2{disc}"},
        business_key_fn=_helena_business_key,
    ),
    _MatrixEntry(
        label="carolina_cred",
        agent_id="carolina",
        process_key=_CRED_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-CRED-001_Descredenciamento.bpmn",
            _DMN_DIR / "cred_admissibility.dmn",
            _DMN_DIR / "cred_route.dmn",
            _DMN_DIR / "cred_prior_notice.dmn",
            _DMN_DIR / "cred_sla.dmn",
        ),
        deploy_name="SP-OP-CRED-001-qa-t33-a2",
        state_factory=lambda disc: {"tenant_id": "amh", "prestador_id": f"PRESTADOR-T33A2-{disc}"},
        business_key_fn=_carolina_business_key,
    ),
    _MatrixEntry(
        label="valentina_programa",
        agent_id="valentina",
        process_key=_PROGRAMA_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn",
            _DMN_DIR / "programa_routing.dmn",
            _DMN_DIR / "programa_sla.dmn",
        ),
        deploy_name="SP-OP-PROGRAMA-001-qa-t33-a2",
        state_factory=lambda disc: {
            "tenant_id": "amh",
            "programa_id": "PROG-DIABETES",
            "beneficiario_pseudo_id": f"BENEF-T33A2-{disc}",
            "ciclo": "2026-Q1",
        },
        business_key_fn=_valentina_business_key,
    ),
    _MatrixEntry(
        label="andre_pagto",
        agent_id="andre",
        process_key=_PAGTO_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn",
            _DMN_DIR / "pagto_admissibility.dmn",
            _DMN_DIR / "pagto_alcada.dmn",
            _DMN_DIR / "pagto_sla.dmn",
        ),
        deploy_name="SP-OP-PAGTO-001-qa-t33-a2",
        state_factory=lambda disc: {
            "flow": "pagto_dossier",
            "tenant_id": "amh",
            "ordem_pagamento_id": f"ORDEM-T33A2-{disc}",
        },
        business_key_fn=_andre_business_key,
    ),
    _MatrixEntry(
        label="marina_contas",
        agent_id="marina",
        process_key=_CONTAS_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn",
            _DMN_DIR / "glosa_reason_normalization.dmn",
            _DMN_DIR / "glosa_classification.dmn",
            _DMN_DIR / "glosa_triage.dmn",
            _DMN_DIR / "contas_sla.dmn",
        ),
        deploy_name="SP-OP-CONTAS-001-qa-t33-a2",
        state_factory=lambda disc: {
            "flow": "contas",
            "tenant_id": "amh",
            "numero_lote_tiss": f"LOTE-T33A2-{disc}",
        },
        business_key_fn=_marina_business_key,
    ),
    _MatrixEntry(
        label="marina_recurso",
        agent_id="marina",
        process_key=_RECURSO_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-RECURSO-001_Recurso_Glosa.bpmn",
            _DMN_DIR / "recurso_admissibility.dmn",
            _DMN_DIR / "recurso_eligibility.dmn",
            _DMN_DIR / "recurso_sla.dmn",
        ),
        deploy_name="SP-OP-RECURSO-001-qa-t33-a2",
        state_factory=lambda disc: {
            "flow": "recurso",
            "tenant_id": "amh",
            "numero_guia_tiss": f"GUIA-T33A2-{disc}",
            "glosa_id": f"GLOSA-T33A2-{disc}",
        },
        business_key_fn=_marina_business_key,
    ),
    _MatrixEntry(
        label="gustavo_ans_submit",
        agent_id="gustavo",
        process_key=_ANS_SUBMIT_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn",
            _DMN_DIR / "ans_calendar.dmn",
            _DMN_DIR / "ans_sla.dmn",
            _DMN_DIR / "ans_submission_admissibility.dmn",
            _DMN_DIR / "ans_retry_policy.dmn",
        ),
        deploy_name="SP-OP-ANS-SUBMIT-001-qa-t33-a2",
        state_factory=lambda disc: {
            "fluxo": "ans_submit",
            "tenant_id": "amh",
            "report_type": "RPS",
            "competencia": f"COMP-T33A2-{disc}",
        },
        business_key_fn=_gustavo_business_key,
    ),
    _MatrixEntry(
        label="gustavo_nip",
        agent_id="gustavo",
        process_key=_NIP_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-NIP-001_Resposta_NIP.bpmn",
            _DMN_DIR / "nip_classification.dmn",
            _DMN_DIR / "nip_routing.dmn",
            _DMN_DIR / "nip_sla.dmn",
        ),
        deploy_name="SP-OP-NIP-001-qa-t33-a2",
        state_factory=lambda disc: {
            "fluxo": "nip",
            "tenant_id": "amh",
            "numero_nip_ans": f"NIP-T33A2-{disc}",
        },
        business_key_fn=_gustavo_business_key,
    ),
    _MatrixEntry(
        label="fernando_inadimplencia",
        agent_id="fernando",
        process_key=_INAD_PROCESS_KEY,
        deploy_paths=(
            _BPMN_DIR / "SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn",
            _DMN_DIR / "inadimplencia_status.dmn",
            _DMN_DIR / "inadimplencia_purga.dmn",
            _DMN_DIR / "inadimplencia_sla.dmn",
        ),
        deploy_name="SP-OP-INADIMPLENCIA-001-qa-t33-a2",
        state_factory=lambda disc: {"tenant_id": "amh", "numero_contrato": f"CONTRATO-T33A2-{disc}"},
        business_key_fn=_fernando_business_key,
    ),
)


async def _start(
    transport: CibSevenHttpTransport,
    *,
    process_key: str,
    business_key: str,
    variables: dict[str, Any],
    audit_sink: Any,
    provenance: AgentDecisionProvenance,
) -> ProcessInstance:
    """Indirection through the MODULE object (not a bare-name import) so the mutation-check test
    can monkeypatch `_transport_module.start_process_idempotent` and have it actually take
    effect here — a `from ... import start_process_idempotent` would bind a separate name
    reference that patching the module attribute would never reach."""
    return await _transport_module.start_process_idempotent(
        transport,
        process_key=process_key,
        business_key=business_key,
        variables=variables,
        audit_sink=audit_sink,
        provenance=provenance,
    )


def _provenance(entry: _MatrixEntry, state: dict[str, Any]) -> AgentDecisionProvenance:
    return AgentDecisionProvenance(
        agent_id=entry.agent_id,
        agent_version="t33-a2-qa",
        tenant_id=state["tenant_id"],
        decision_basis={"route": "t33_a2_qa"},
    )


def test_a2_escalation_business_key_format_shared_by_helena_and_lucas() -> None:
    """helena and lucas are DISTINCT agent call sites into the SAME SP-OP-ESCALATION-001
    process. The engine-level idempotency proof below drives ESCALATION-001 once (via helena's
    `_business_key`) — that single proof covers BOTH call sites IFF the two independently
    maintained `_business_key` functions have not drifted apart. This is that discriminator."""
    assert _LUCAS_PROCESS_KEY == _ESCALATION_PROCESS_KEY
    state = {"tenant_id": "amh", "conversation_id": "wa:amh:t33a2paritycheck"}
    assert _lucas_business_key(state) == _helena_business_key(state)


@pytest.mark.parametrize("entry", MATRIX, ids=[e.label for e in MATRIX])
async def test_a2_same_business_key_yields_one_instance_and_one_audit_link(
    entry: _MatrixEntry,
    engine: EngineRest,
    audit_sink: Any,
    audit_pg: tuple[str, str],
    audit_tenant: str,
) -> None:
    """GREEN: driving `start_process_idempotent` TWICE with the SAME business key (the same
    fenced chokepoint every agent's own `start_process` node calls) must converge to exactly one
    active instance and exactly one audit chain link — never a double-start, never a double-audit."""
    await engine.deploy(*entry.deploy_paths, name=entry.deploy_name)

    disc = uuid.uuid4().hex[:8]
    state = entry.state_factory(disc)
    business_key = entry.business_key_fn(state)
    assert business_key, f"{entry.label}: business key derivation must not be empty"
    provenance = _provenance(entry, state)

    transport = CibSevenHttpTransport(CIBSEVEN_BASE_URL)
    try:
        chain_before = await count_chain_rows(audit_pg[0], audit_tenant)
        inst1 = await _start(
            transport,
            process_key=entry.process_key,
            business_key=business_key,
            variables={},
            audit_sink=audit_sink,
            provenance=provenance,
        )
        chain_mid = await count_chain_rows(audit_pg[0], audit_tenant)
        inst2 = await _start(
            transport,
            process_key=entry.process_key,
            business_key=business_key,
            variables={},
            audit_sink=audit_sink,
            provenance=provenance,
        )
        chain_after = await count_chain_rows(audit_pg[0], audit_tenant)

        assert inst1.already_existed is False, f"{entry.label}: first start must be genuine"
        assert inst2.already_existed is True, (
            f"{entry.label}: second start (same business key) must hit the existing instance"
        )
        assert inst1.instance_id == inst2.instance_id, (
            f"{entry.label}: re-delivery must resolve to the identical instance id"
        )
        assert chain_mid - chain_before == 1, f"{entry.label}: first start must add exactly one audit link"
        assert chain_after - chain_mid == 0, (
            f"{entry.label}: second (same-key) start must add ZERO new audit links"
        )

        active = await engine.find_active_instances(business_key)
        assert len(active) == 1, (
            f"{entry.label}: exactly one active {entry.process_key} instance expected for "
            f"{business_key!r}, found {len(active)}"
        )
        assert str(active[0]["id"]) == inst1.instance_id
    finally:
        await transport.close()


@pytest.mark.parametrize("entry", MATRIX, ids=[e.label for e in MATRIX])
async def test_a2_distinct_business_keys_yield_distinct_instances(
    entry: _MatrixEntry,
    engine: EngineRest,
    audit_sink: Any,
    audit_tenant: str,
) -> None:
    """Discriminator sibling: DISTINCT business keys must never collapse onto the same
    instance — proves the idempotency above is CAUSED by the shared key, not a side effect of
    always returning the first-ever instance for the process."""
    await engine.deploy(*entry.deploy_paths, name=entry.deploy_name)

    disc_a, disc_b = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    state_a, state_b = entry.state_factory(disc_a), entry.state_factory(disc_b)
    business_key_a = entry.business_key_fn(state_a)
    business_key_b = entry.business_key_fn(state_b)
    assert business_key_a != business_key_b

    transport = CibSevenHttpTransport(CIBSEVEN_BASE_URL)
    try:
        inst_a = await _start(
            transport,
            process_key=entry.process_key,
            business_key=business_key_a,
            variables={},
            audit_sink=audit_sink,
            provenance=_provenance(entry, state_a),
        )
        inst_b = await _start(
            transport,
            process_key=entry.process_key,
            business_key=business_key_b,
            variables={},
            audit_sink=audit_sink,
            provenance=_provenance(entry, state_b),
        )

        assert inst_a.already_existed is False
        assert inst_b.already_existed is False
        assert inst_a.instance_id != inst_b.instance_id, (
            f"{entry.label}: distinct business keys must yield distinct instances"
        )
    finally:
        await transport.close()


@pytest.mark.skipif(
    not mutation_active("a1_a2"),
    reason="only runs when MAEZO_CHAOS_MUTATE=a1_a2 (see module docstring for the explicit run command)",
)
async def test_a2_mutation_check_broken_idempotency_creates_a_second_instance(
    engine: EngineRest,
    audit_sink: Any,
    audit_tenant: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION-CHECK (must FAIL when run): patches `start_process_idempotent` to the A1/A2
    broken variant (always-start, `find_active_instance` skipped) at the SAME module every
    agent's own `start_process` node imports it from — one representative entry (rafael/AUTH) is
    enough: the chokepoint is shared, not per-process, so breaking it once breaks every entry in
    MATRIX identically."""
    monkeypatch.setattr(_transport_module, "start_process_idempotent", broken_start_process_always_start)

    entry = MATRIX[0]
    await engine.deploy(*entry.deploy_paths, name=f"{entry.deploy_name}-mutate")

    disc = uuid.uuid4().hex[:8]
    state = entry.state_factory(disc)
    business_key = entry.business_key_fn(state)
    provenance = _provenance(entry, state)

    transport = CibSevenHttpTransport(CIBSEVEN_BASE_URL)
    try:
        await _start(
            transport,
            process_key=entry.process_key,
            business_key=business_key,
            variables={},
            audit_sink=audit_sink,
            provenance=provenance,
        )
        await _start(
            transport,
            process_key=entry.process_key,
            business_key=business_key,
            variables={},
            audit_sink=audit_sink,
            provenance=provenance,
        )

        active = await engine.find_active_instances(business_key)
        # EXPECTED TO FAIL under the mutation: the broken variant never checks
        # find_active_instance, so the second call starts a SECOND instance.
        assert len(active) == 1, (
            f"MUTATION EXPECTED TO TURN THIS RED: exactly one active instance expected, found "
            f"{len(active)} (the broken always-start variant should have created a duplicate)"
        )
    finally:
        await transport.close()
