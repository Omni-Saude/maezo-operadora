"""Unit tests for Rafael's REAL graph (T1.11, defect B6 — SP-OP-AUTH-001 prep).

Every node is exercised against fakes: `FakeDmnTransport`, `FakeCibSevenTransport`, and a small
in-file fake inference provider. Live-engine acceptance (dossier + `UT_AnaliseMedicoAuditor`
visible to `medico-auditor`) is Rafael's checkpointed integration coverage — see
`tests/integration/agents/test_rafael_auth_dossier.py` and the PR body for completeness status.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.rafael.graph import RafaelGraph, RafaelState, _business_key, build
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie factual sintetico"]
        self.calls: list[tuple[str, bool]] = []
        #: AF-12 (CC-12): the `task_kind` of each call, in order.
        self.task_kinds: list[str | None] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        self.task_kinds.append(task_kind)
        return self._responses.pop(0) if self._responses else ""


class _FakeFhirReader:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.patient_calls: list[str] = []
        self.coverage_calls: list[str] = []

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        self.patient_calls.append(patient_id)
        if self._fail:
            raise RuntimeError("HAPI FHIR unreachable")
        return {"resourceType": "Patient", "id": patient_id}

    async def search_coverage(self, patient_id: str) -> Any:
        self.coverage_calls.append(patient_id)
        if self._fail:
            raise RuntimeError("HAPI FHIR unreachable")
        return [{"resourceType": "Coverage"}]


def _base_state(**overrides: Any) -> RafaelState:
    state: RafaelState = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-001",
        "beneficiario_pseudo_id": "pseudo-123",
        "prestador_id": "prestador-1",
        "canal": "portal_tiss",
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "carater_atendimento": "eletivo",
        "valor_estimado_brl": 250.0,
        "documentos_refs": [],
        "requer_autorizacao": True,
        "documentacao_completa": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
        "dut_atendida": True,
        "dentro_teto_l2": True,
        "rede_credenciada": True,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _graph(
    *,
    inference: Any | None = None,
    dmn: FakeDmnTransport | None = None,
    cibseven: FakeCibSevenTransport | None = None,
    audit_sink: Any | None = None,
    fhir: Any | None = None,
) -> RafaelGraph:
    return RafaelGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=audit_sink or FakeStartAuditSink(),
        fhir=fhir,
    )


def _register_admissibility(dmn: FakeDmnTransport, resultado: str = "SEGUE_ANALISE") -> None:
    dmn.register("auth_admissibility", [{"resultado": resultado, "motivo": "test"}])


def _register_sla(dmn: FakeDmnTransport) -> None:
    dmn.register("auth_sla", [{"sla_analise": "P5D", "sla_alerta": "P3D", "fonte_regulatoria": "RN 395"}])


def _register_auto_approval(dmn: FakeDmnTransport, recomendacao: str) -> None:
    dmn.register("auth_auto_approval", [{"recomendacao": recomendacao, "motivo": "test"}])


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity
# ---------------------------------------------------------------------------


def test_rafael_agent_yaml_exists() -> None:
    agent_path = _AGENTS_ROOT / "rafael" / "agent.yaml"
    assert agent_path.exists()


def test_rafael_agent_yaml_has_required_fields() -> None:
    agent_path = _AGENTS_ROOT / "rafael" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)
    assert data["id"] == "rafael"
    assert data["name"] == "Rafael Nogueira"
    assert data.get("phase") == 1


def test_rafael_definition_loads() -> None:
    from maezo.agents import AgentLoader

    definition = AgentLoader().load(_AGENTS_ROOT / "rafael" / "agent.yaml")
    assert definition.id == "rafael"
    assert definition.phase == 1


# ---------------------------------------------------------------------------
# build(config) — fail-closed contract; fhir is OPTIONAL
# ---------------------------------------------------------------------------


def test_build_requires_inference_dmn_cibseven() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({})


def test_build_without_fhir_still_compiles() -> None:
    graph = build(
        {
            "inference": _FakeInference(),
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
        }
    )
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {
        "receive",
        "gather",
        "assess",
        "auto_approve",
        "human_auditor",
        "start_process",
        "complete",
    } <= node_names


# ---------------------------------------------------------------------------
# receive / business key
# ---------------------------------------------------------------------------


def test_business_key_format() -> None:
    state = _base_state(tenant_id="amh", numero_guia_tiss="GUIA-42")
    assert _business_key(state) == "AUTH-amh-GUIA-42"


async def test_receive_assigns_business_key() -> None:
    graph = _graph()
    result = await graph.receive(_base_state())
    assert result["business_key"] == "AUTH-amh-GUIA-001"


# ---------------------------------------------------------------------------
# gather — best-effort FHIR, never blocks routing
# ---------------------------------------------------------------------------


async def test_gather_without_fhir_reader_notes_the_gap() -> None:
    graph = _graph(fhir=None)
    result = await graph.gather(_base_state())
    assert result["gathered"] is True
    assert result["gather_notes"]  # explicit gap note, not a silent empty


async def test_gather_with_fhir_reader_populates_facts() -> None:
    fhir = _FakeFhirReader()
    graph = _graph(fhir=fhir)
    result = await graph.gather(_base_state(coverage_ref="pseudo-123", patient_ref="patient-1"))
    assert result["gathered"] is True
    assert result["gather_notes"] == []
    assert fhir.coverage_calls == ["pseudo-123"]
    assert fhir.patient_calls == ["patient-1"]


async def test_gather_fhir_failure_is_best_effort_never_raises() -> None:
    fhir = _FakeFhirReader(fail=True)
    graph = _graph(fhir=fhir)
    result = await graph.gather(_base_state(patient_ref="patient-1"))
    assert result["gathered"] is True
    assert any("indisponivel" in note for note in result["gather_notes"])


# ---------------------------------------------------------------------------
# assess — auth_admissibility -> (auth_sla informative) -> auth_auto_approval
# ---------------------------------------------------------------------------


async def test_assess_auto_aprovar_routes_auto_approve() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_sla(dmn)
    _register_auto_approval(dmn, "AUTO_APROVAR")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "auto_approve"
    assert result["desfecho"] == "aprovacao_automatica_solicitada"
    assert result["recomendacao_auto"] == "AUTO_APROVAR"


async def test_assess_analise_humana_routes_human_auditor() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_sla(dmn)
    _register_auto_approval(dmn, "ANALISE_HUMANA")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(dentro_teto_l2=False))

    assert result["route"] == "human_auditor"
    assert result["motivo_auditor"] == "dmn_analise_humana"
    assert result["desfecho"] == "encaminhado_auditor"


async def test_assess_pendente_documentacao_routes_human_never_calls_auto_approval() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "PENDENTE_DOCUMENTACAO")
    _register_sla(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(documentacao_completa=False))

    assert result["route"] == "human_auditor"
    assert result["motivo_auditor"] == "documentacao_pendente"
    assert not any(table == "auth_auto_approval" for table, _ in dmn.calls)


async def test_assess_nao_requer_routes_human_with_nao_requer_desfecho() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "NAO_REQUER")
    _register_sla(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(requer_autorizacao=False))

    assert result["route"] == "human_auditor"
    assert result["motivo_auditor"] == "outro"
    assert result["desfecho"] == "nao_requer"


async def test_assess_admissibility_dmn_unavailable_routes_human_never_auto_approves() -> None:
    """ADR-0008/contract L0 hard: DMN unavailable NEVER auto-approves by omission."""
    dmn = FakeDmnTransport()  # nothing registered -> DmnEvaluationError
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "human_auditor"
    assert result["motivo_auditor"] == "dmn_indisponivel"
    assert "dmn_error" in result


async def test_assess_auto_approval_dmn_unavailable_routes_human() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_sla(dmn)
    # auth_auto_approval deliberately NOT registered.
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "human_auditor"
    assert result["motivo_auditor"] == "dmn_indisponivel"


def test_route_fail_safe_defaults_to_human_on_missing_route() -> None:
    assert RafaelGraph._route({}) == "human_auditor"
    assert RafaelGraph._route({"route": "auto_approve"}) == "auto_approve"


# ---------------------------------------------------------------------------
# auto_approve / human_auditor — dossier structural guardrail (L0 hard)
# ---------------------------------------------------------------------------


async def test_auto_approve_dossier_never_carries_a_coverage_decision() -> None:
    graph = _graph()
    result = await graph.auto_approve(_base_state(route="auto_approve"))
    assert result["dossier"]["decisao_cobertura"] is None
    assert result["dossier"]["route"] == "auto_approve"


async def test_human_auditor_dossier_carries_motivo_never_a_decision() -> None:
    graph = _graph()
    result = await graph.human_auditor(
        _base_state(route="human_auditor", motivo_auditor="dmn_analise_humana")
    )
    assert result["dossier"]["decisao_cobertura"] is None
    assert result["dossier"]["motivo_auditor"] == "dmn_analise_humana"


async def test_dossier_narrative_llm_call_is_phi_tagged() -> None:
    inference = _FakeInference(["narrativa"])
    graph = _graph(inference=inference)
    await graph.auto_approve(_base_state(route="auto_approve"))
    assert inference.calls
    assert all(phi is True for _, phi in inference.calls)


async def test_dossier_narrative_llm_call_declares_task_kind_reasoning() -> None:
    """CC-12/BEA-01 (ADR-0009 §2): the coverage dossier narrative is for the human auditor's
    decision — `reasoning`, not `task_default`."""
    inference = _FakeInference(["narrativa"])
    graph = _graph(inference=inference)
    await graph.auto_approve(_base_state(route="auto_approve"))
    assert inference.task_kinds == ["reasoning"]


async def test_dossier_llm_failure_never_blocks_the_route() -> None:
    class _FailingInference:
        async def generate(
            self,
            prompt: str,
            *,
            phi: bool = False,
            agent_id: str | None = None,
            tenant_id: str | None = None,
            task_kind: str | None = None,
        ) -> str:
            raise RuntimeError("LLM down")

    graph = _graph(inference=_FailingInference())
    result = await graph.auto_approve(_base_state(route="auto_approve"))
    assert result["dossier"]["narrativa"] == ""
    assert result["dossier"]["decisao_cobertura"] is None


# ---------------------------------------------------------------------------
# start_process — idempotent, contract variables
# ---------------------------------------------------------------------------


async def test_start_process_starts_with_contract_variables() -> None:
    cibseven = FakeCibSevenTransport()
    graph = _graph(cibseven=cibseven)

    state = _base_state(
        route="auto_approve", business_key="AUTH-amh-GUIA-001", dossier={"route": "auto_approve"}
    )
    result = await graph.start_process(state)

    assert result["process_started"] is True
    assert result["business_key"] == "AUTH-amh-GUIA-001"
    assert result["process_ref"]["already_existed"] is False


async def test_start_process_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    business_key = "AUTH-amh-GUIA-001"
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-auth-1",
            process_key="SP-OP-AUTH-001",
            business_key=business_key,
            state="ACTIVE",
            already_existed=True,
        )
    )
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(_base_state(business_key=business_key, route="human_auditor"))

    assert result["process_ref"]["instance_id"] == "existing-auth-1"
    assert result["process_ref"]["already_existed"] is True


async def test_start_process_records_error_on_cibseven_failure() -> None:
    class _FailingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
            raise CibSevenError("engine unreachable")

    graph = _graph(cibseven=_FailingCibSeven())
    result = await graph.start_process(_base_state(route="human_auditor"))
    assert result["process_started"] is False
    assert "error" in result


def test_contract_variables_include_dossier_and_route() -> None:
    graph = _graph()
    variables = graph._contract_variables(
        _base_state(route="human_auditor", motivo_auditor="dmn_analise_humana", dossier={"narrativa": "x"})
    )
    assert variables["rafael_route"] == "human_auditor"
    assert variables["motivo_encaminhamento"] == "dmn_analise_humana"
    assert variables["dossie_rafael"] == {"narrativa": "x"}
    assert variables["source_agent_id"] == "rafael"


def test_contract_variables_never_include_motivo_encaminhamento_on_auto_approve() -> None:
    graph = _graph()
    variables = graph._contract_variables(_base_state(route="auto_approve"))
    assert "motivo_encaminhamento" not in variables


# ---------------------------------------------------------------------------
# Full-graph turns (compiled, in-memory — no engine; see integration for the real engine)
# ---------------------------------------------------------------------------


async def test_full_turn_auto_approve_starts_process_no_denial_path_exists() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_sla(dmn)
    _register_auto_approval(dmn, "AUTO_APROVAR")
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state())

    assert result["route"] == "auto_approve"
    assert result["process_started"] is True
    assert result["business_key"] == "AUTH-amh-GUIA-001"
    assert result["dossier"]["decisao_cobertura"] is None


async def test_full_turn_human_auditor_starts_process_dossier_has_no_decision() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_sla(dmn)
    _register_auto_approval(dmn, "ANALISE_HUMANA")
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(dentro_teto_l2=False))

    assert result["route"] == "human_auditor"
    assert result["process_started"] is True
    assert result["dossier"]["decisao_cobertura"] is None
    assert result["desfecho"] == "encaminhado_auditor"


def test_allowed_routes_never_include_a_denial_variant() -> None:
    """Structural proof (no engine needed): the `Route` type admits only two literals."""
    from maezo.agents.rafael.graph import Route

    allowed = set(Route.__args__)  # type: ignore[attr-defined]
    assert allowed == {"auto_approve", "human_auditor"}
    assert "deny" not in allowed
    assert "negar" not in allowed
