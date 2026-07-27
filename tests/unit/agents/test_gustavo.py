"""Unit tests for Gustavo's REAL graph (T1.12 — SP-OP-ANS-SUBMIT-001 + SP-OP-NIP-001).

Every node is exercised against fakes: `FakeDmnTransport`, `FakeCibSevenTransport`, and a small
in-file fake inference provider — same idiom as `tests/unit/agents/test_rafael.py` /
`test_carolina.py`. Live-engine acceptance was NOT attempted for this PR (host constraint +
SP-OP-ANS-SUBMIT-001 has no live trigger today, T2.6-7 — see the PR body's honest disclosure);
this file is Gustavo's checkpointed unit coverage.

Covers: both journeys E2E (J1 ANS-SUBMIT always -> human review of the filing; J2 NIP always ->
human authors/approves the response), one guard test per L0 invariant in the T1.12 charter
(always-human for the submit sign + the NIP response; `nip_manter_negativa` never auto),
the caller-planted-output probe family (unique sentinel in EVERY output field, both journeys +
every shortcut -> nothing reaches engine variables/dossier; a planted route can't skip the human
task), and the helena-class probes (assess-failure fail-closed; a PHI-bearing field value never
reaching engine class-token fields).

Replaces the pre-T1.12 stub (`classify_request`/`assemble_response`/`validate_compliance` —
hardcoded placeholder classification, no DMN, no process start).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.gustavo.graph import (
    DMN_ANS_ADMISSIBILITY,
    DMN_ANS_CALENDAR,
    DMN_NIP_CLASSIFICATION,
    DMN_NIP_ROUTING,
    DMN_NIP_SLA,
    PROCESS_KEY_ANS_SUBMIT,
    PROCESS_KEY_NIP,
    GustavoGraph,
    GustavoState,
    Route,
    _business_key,
    build,
)
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie factual sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


class _FailingInference:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        raise RuntimeError("LLM down")


class _FakeFhirReader:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[str] = []

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        self.calls.append(patient_id)
        if self._fail:
            raise RuntimeError("HAPI FHIR unreachable")
        return {"resourceType": "Patient", "id": patient_id}


class _RecordingCibSeven(FakeCibSevenTransport):
    """Records the engine-bound variables of every start (the audit surface under probe)."""

    def __init__(self) -> None:
        super().__init__()
        self.started_variables: list[dict[str, Any]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.started_variables.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)


def _ans_state(**overrides: Any) -> GustavoState:
    state: GustavoState = {
        "tenant_id": "amh",
        "fluxo": "ans_submit",
        "canal": "calendario",
        "report_type": "DIOPS_TRIMESTRAL",
        # Deliberately low-entropy synthetic competencia (gitleaks hygiene — the derived
        # business key "ANSSUB-amh-DIOPS_TRIMESTRAL-2026-Q1" sits next to a 'key' substring in
        # assertions; do not "improve" this to higher-entropy data).
        "competencia": "2026-Q1",
        "periodicidade": "trimestral",
        "origem_envio": "calendario",
        "dataset_complete": True,
        "schema_valid": True,
        "lgpd_anonimizado": True,
        "dataset_ref": "s3://synthetic/dataset-000",
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _nip_state(**overrides: Any) -> GustavoState:
    state: GustavoState = {
        "tenant_id": "amh",
        "fluxo": "nip",
        "canal": "a2a",
        # Low-entropy synthetic NIP number (gitleaks hygiene, same rationale as _ans_state).
        "numero_nip_ans": "000000042",
        "beneficiario_pseudo_id": "pseudo-123",
        "classificacao_nip": "assistencial",
        "tema_nip": "negativa_cobertura",
        "referencia_negativa_original": "AUTH-guia-000",
        "data_recebimento_nip_iso": "2026-07-10",
        "documentos_refs": [],
        "contesta_negativa": True,
        "documentacao_suficiente": True,
        "origem_a2a": True,
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
) -> GustavoGraph:
    return GustavoGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=audit_sink or FakeStartAuditSink(),
        fhir=fhir,
    )


def _register_calendar(dmn: FakeDmnTransport) -> None:
    dmn.register(
        DMN_ANS_CALENDAR,
        [
            {
                "due_date": "DRAFT_DUE_DATE",
                "sla_alerta": "DRAFT_ALERTA",
                "periodicidade": "trimestral",
                "fonte_regulatoria": "DIOPS — trimestral: DRAFT/verify",
            }
        ],
    )


def _register_admissibility(dmn: FakeDmnTransport, roteamento: str = "SEGUE_ENVIO") -> None:
    dmn.register(DMN_ANS_ADMISSIBILITY, [{"roteamento": roteamento, "motivo": "test"}])


def _register_classification(
    dmn: FakeDmnTransport,
    classificacao: str = "ASSISTENCIAL_CONTESTA_NEGATIVA",
    *,
    prazo_dias: int = 5,
    grupo_revisor: str = "juridico-regulatorio",
) -> None:
    dmn.register(
        DMN_NIP_CLASSIFICATION,
        [{"classificacao": classificacao, "prazo_dias": prazo_dias, "grupo_revisor": grupo_revisor}],
    )


def _register_sla(dmn: FakeDmnTransport) -> None:
    dmn.register(
        DMN_NIP_SLA,
        [
            {
                "prazo_resposta_iso": "P5D",
                "prazo_resposta_absoluto_iso": "2026-07-15T00:00:00",
                "sla_alerta_iso": "P3D",
                "sla_alerta_absoluto_iso": "2026-07-13T00:00:00",
                "fonte_regulatoria": "RN 388/2016 — DRAFT/verify",
            }
        ],
    )


def _register_routing(
    dmn: FakeDmnTransport, roteamento: str = "REVISAO_JURIDICA", grupo_humano: str = "juridico-regulatorio"
) -> None:
    dmn.register(DMN_NIP_ROUTING, [{"roteamento": roteamento, "grupo_humano": grupo_humano}])


def _register_all_nip(
    dmn: FakeDmnTransport,
    *,
    classificacao: str = "ASSISTENCIAL_CONTESTA_NEGATIVA",
    roteamento: str = "REVISAO_JURIDICA",
    grupo_humano: str = "juridico-regulatorio",
) -> None:
    _register_classification(dmn, classificacao)
    _register_sla(dmn)
    _register_routing(dmn, roteamento, grupo_humano)


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity
# ---------------------------------------------------------------------------


def test_gustavo_agent_yaml_exists() -> None:
    agent_path = _AGENTS_ROOT / "gustavo" / "agent.yaml"
    assert agent_path.exists()


def test_gustavo_agent_yaml_owns_both_processes() -> None:
    agent_path = _AGENTS_ROOT / "gustavo" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)
    assert data["id"] == "gustavo"
    assert data["name"] == "Gustavo Andrade"
    assert data.get("phase") == 2
    assert set(data["process_keys"]) == {"SP-OP-NIP-001", "SP-OP-ANS-SUBMIT-001"}
    assert "tools" in data


def test_gustavo_definition_loads() -> None:
    from maezo.agents import AgentLoader

    definition = AgentLoader().load(_AGENTS_ROOT / "gustavo" / "agent.yaml")
    assert definition.id == "gustavo"
    assert definition.autonomy_level == "L2"
    assert definition.security_zone == "general"


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
        "review_submission",
        "instruct_nip",
        "start_process",
        "finalize",
    } <= node_names


# ---------------------------------------------------------------------------
# receive / business key — fail-safe, class tokens only (never a raw value)
# ---------------------------------------------------------------------------


def test_business_key_ans_submit_format() -> None:
    assert _business_key(_ans_state()) == "ANSSUB-amh-DIOPS_TRIMESTRAL-2026-Q1"


def test_business_key_nip_format() -> None:
    assert _business_key(_nip_state()) == "NIP-amh-000000042"


async def test_receive_assigns_business_and_process_key_per_flow() -> None:
    graph = _graph()
    ans = await graph.receive(_ans_state())
    assert ans["business_key"] == "ANSSUB-amh-DIOPS_TRIMESTRAL-2026-Q1"
    assert ans["process_key"] == PROCESS_KEY_ANS_SUBMIT
    nip = await graph.receive(_nip_state())
    assert nip["business_key"] == "NIP-amh-000000042"
    assert nip["process_key"] == PROCESS_KEY_NIP


async def test_receive_missing_tenant_fails_safe_with_class_token() -> None:
    graph = _graph()
    result = await graph.receive(_nip_state(tenant_id=""))
    assert result["route"] == "instruct_nip"  # human destination, by flow
    assert result["motivo_humano"] == "falha_tecnica"
    assert result["error"] == "missing_tenant_id"
    assert result["business_key"] == ""  # non-derivable identity never fabricated


async def test_receive_invalid_fluxo_fails_safe_without_leaking_value() -> None:
    """An unrecognized `fluxo` must fail safe to the conservative human NIP-instruction route,
    with a bounded class token — the raw value is NEVER echoed anywhere in the update."""
    graph = _graph()
    result = await graph.receive(_nip_state(fluxo="transmitir_agora_urgente"))
    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "ambiguidade"
    assert result["error"] == "invalid_fluxo"
    assert "transmitir_agora_urgente" not in str(result)


async def test_receive_ans_submit_missing_identity_fails_safe() -> None:
    graph = _graph()
    result = await graph.receive(_ans_state(report_type=""))
    assert result["route"] == "review_submission"
    assert result["motivo_humano"] == "pendencia_envio"
    assert result["error"] == "missing_report_type_competencia"
    assert result["process_key"] == PROCESS_KEY_ANS_SUBMIT
    assert result["business_key"] == ""


async def test_receive_nip_missing_numero_fails_safe() -> None:
    graph = _graph()
    result = await graph.receive(_nip_state(numero_nip_ans=""))
    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "revisao_juridica_nip"
    assert result["error"] == "missing_numero_nip_ans"
    assert result["business_key"] == ""


# ---------------------------------------------------------------------------
# T1.12 mandatory hardening: single entry + output-field partition
# ---------------------------------------------------------------------------


def test_receive_is_the_single_graph_entry_node() -> None:
    """The receive-side sanitization closes the caller-planted class ONLY if receive always runs
    first: assert the compiled topology has exactly one edge out of START, into `receive` — the
    gather/assess early-bail guards are safe solely because of this."""
    compiled = _graph().compile_graph().compile()
    start_targets = [e.target for e in compiled.get_graph().edges if e.source == "__start__"]
    assert start_targets == ["receive"]


def test_output_field_partition_is_complete() -> None:
    """Completeness guard: every `GustavoState` field is classified as exactly one of
    caller-input or output-only. Adding a new state field without classifying it (and, if
    output-only, without a sanitized default) fails here — the root-cause class stays closed as
    the state evolves."""
    from maezo.agents.gustavo.graph import _CALLER_INPUT_FIELDS, _output_field_resets

    annotations = set(GustavoState.__annotations__)
    outputs = set(_output_field_resets())
    assert _CALLER_INPUT_FIELDS & outputs == set()  # no field in both sets
    assert _CALLER_INPUT_FIELDS | outputs == annotations  # no field in neither set


# ---------------------------------------------------------------------------
# J1 (ANS-SUBMIT) — assess ALWAYS consults the DMNs; ALWAYS routes to the human sign
# ---------------------------------------------------------------------------


async def test_assess_ans_submit_segue_envio_still_routes_human_review() -> None:
    """L0 GUARD (charter): even the PERFECT input (SEGUE_ENVIO — dataset complete, schema valid,
    anonymized) only ENABLES the human review — Gustavo NEVER transmits; there is no auto path."""
    dmn = FakeDmnTransport()
    _register_calendar(dmn)
    _register_admissibility(dmn, "SEGUE_ENVIO")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_ans_state())

    assert result["route"] == "review_submission"
    assert result["motivo_humano"] == "revisao_envio"
    assert result["admissibilidade_envio"] == "SEGUE_ENVIO"
    assert result["due_date"] == "DRAFT_DUE_DATE"
    called = {table for table, _ in dmn.calls}
    assert called == {DMN_ANS_CALENDAR, DMN_ANS_ADMISSIBILITY}


@pytest.mark.parametrize("roteamento", ["PENDENTE", "REVISAO_HUMANA"])
async def test_assess_ans_submit_pendencia_routes_human_review(roteamento: str) -> None:
    dmn = FakeDmnTransport()
    _register_calendar(dmn)
    _register_admissibility(dmn, roteamento)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_ans_state(dataset_complete=False, lgpd_anonimizado=False))

    assert result["route"] == "review_submission"
    assert result["motivo_humano"] == "pendencia_envio"
    assert result["admissibilidade_envio"] == roteamento


async def test_assess_ans_calendar_dmn_unavailable_fails_safe_to_human() -> None:
    """Calendar unavailable -> human immediately, never an invented deadline (and admissibility
    is not consulted on this path — donor parity)."""
    dmn = FakeDmnTransport()  # ans_calendar NOT registered
    _register_admissibility(dmn, "SEGUE_ENVIO")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_ans_state())

    assert result["route"] == "review_submission"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert "dmn_error" in result
    assert all(table != DMN_ANS_ADMISSIBILITY for table, _ in dmn.calls)


async def test_assess_ans_admissibility_dmn_unavailable_fails_safe_to_human() -> None:
    dmn = FakeDmnTransport()
    _register_calendar(dmn)  # admissibility NOT registered
    graph = _graph(dmn=dmn)

    result = await graph.assess(_ans_state())

    assert result["route"] == "review_submission"
    assert result["motivo_humano"] == "dmn_indisponivel"


async def test_assess_ans_admissibility_unknown_value_fails_safe_closed() -> None:
    """Fail-safe fechado: an out-of-allowlist admissibility value (e.g. a hostile 'TRANSMITIR')
    never 'passes through' — conservative human review with motivo ambiguidade."""
    dmn = FakeDmnTransport()
    _register_calendar(dmn)
    _register_admissibility(dmn, "TRANSMITIR")  # must never exist per contract; defensive
    graph = _graph(dmn=dmn)

    result = await graph.assess(_ans_state())

    assert result["route"] == "review_submission"
    assert result["motivo_humano"] == "ambiguidade"
    assert result.get("admissibilidade_envio") is None


async def test_full_turn_ans_submit_starts_process_with_contract_variables() -> None:
    dmn = FakeDmnTransport()
    _register_calendar(dmn)
    _register_admissibility(dmn, "SEGUE_ENVIO")
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_ans_state())

    assert result["route"] == "review_submission"
    assert result["desfecho"] == "envio_encaminhado_revisao_humana"
    assert result["process_started"] is True
    assert result["business_key"] == "ANSSUB-amh-DIOPS_TRIMESTRAL-2026-Q1"
    assert await cibseven.find_active_instance("ANSSUB-amh-DIOPS_TRIMESTRAL-2026-Q1") is not None
    variables = cibseven.started_variables[0]
    assert variables["report_type"] == "DIOPS_TRIMESTRAL"
    assert variables["competencia"] == "2026-Q1"
    assert variables["origem_envio"] == "calendario"
    assert variables["dataset_complete"] is True
    assert variables["dossie_gustavo"]["assinatura_envio"] is None
    assert variables["gustavo_route"] == "review_submission"
    assert variables["motivo_encaminhamento"] == "revisao_envio"


async def test_l0_guard_ans_submit_never_ships_a_signature_or_protocol() -> None:
    """L0 GUARD (charter: 'a human signs the binding submission — Gustavo NEVER transmits'):
    the engine variables NEVER carry an approval, a reviewer id, or an ANS protocol — the
    human-only output variable ships as the explicit None guardrail."""
    dmn = FakeDmnTransport()
    _register_calendar(dmn)
    _register_admissibility(dmn, "SEGUE_ENVIO")
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    await compiled.ainvoke(_ans_state())

    variables = cibseven.started_variables[0]
    assert variables["decisao_envio"] is None
    assert "revisor_id" not in variables
    assert "protocolo_ans" not in variables
    assert "APROVAR_ENVIO" not in json.dumps(variables, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# J2 (NIP) — assess ALWAYS consults the DMNs; ALWAYS routes to the human author/review
# ---------------------------------------------------------------------------


async def test_assess_nip_contesta_negativa_routes_revisao_juridica() -> None:
    dmn = FakeDmnTransport()
    _register_all_nip(dmn)  # ASSISTENCIAL_CONTESTA_NEGATIVA -> REVISAO_JURIDICA
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state())

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "revisao_juridica_nip"
    assert result["classificacao"] == "ASSISTENCIAL_CONTESTA_NEGATIVA"
    assert result["roteamento_nip"] == "REVISAO_JURIDICA"
    assert result["grupo_humano"] == "juridico-regulatorio"
    assert result["prazo_resposta_absoluto_iso"] == "2026-07-15T00:00:00"
    called = {table for table, _ in dmn.calls}
    assert called == {DMN_NIP_CLASSIFICATION, DMN_NIP_SLA, DMN_NIP_ROUTING}


async def test_assess_nip_assistencial_outro_routes_elaborar_resposta() -> None:
    dmn = FakeDmnTransport()
    _register_all_nip(
        dmn,
        classificacao="ASSISTENCIAL_OUTRO",
        roteamento="ELABORAR_RESPOSTA",
        grupo_humano="regulatorio-ans",
    )
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state(contesta_negativa=False, tema_nip="prazo_atendimento"))

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "elaborar_resposta_nip"
    assert result["grupo_humano"] == "regulatorio-ans"


async def test_assess_nip_nao_assistencial_routes_elaborar_resposta_nucleo() -> None:
    dmn = FakeDmnTransport()
    _register_all_nip(
        dmn, classificacao="NAO_ASSISTENCIAL", roteamento="ELABORAR_RESPOSTA", grupo_humano="nucleo-ans"
    )
    graph = _graph(dmn=dmn)

    result = await graph.assess(
        _nip_state(classificacao_nip="nao_assistencial", contesta_negativa=False, tema_nip="cobranca")
    )

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "elaborar_resposta_nip"
    assert result["grupo_humano"] == "nucleo-ans"


async def test_assess_nip_pendente_info_routes_human() -> None:
    dmn = FakeDmnTransport()
    _register_all_nip(dmn, roteamento="PENDENTE_INFO")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state(documentacao_suficiente=False))

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "pendente_info_nip"


async def test_assess_nip_classification_dmn_unavailable_fails_safe() -> None:
    dmn = FakeDmnTransport()  # nothing registered
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state())

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert "dmn_error" in result


async def test_assess_nip_classification_unknown_value_fails_safe_closed() -> None:
    dmn = FakeDmnTransport()
    _register_classification(dmn, "MANTER_NEGATIVA")  # hostile: must never exist per contract
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state())

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "ambiguidade"
    assert result.get("classificacao") is None


async def test_assess_nip_routing_unknown_value_fails_safe_closed() -> None:
    dmn = FakeDmnTransport()
    _register_classification(dmn)
    _register_sla(dmn)
    _register_routing(dmn, "RESPONDER_AUTOMATICAMENTE")  # hostile: must never exist
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state())

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "ambiguidade"
    assert result.get("roteamento_nip") is None


async def test_assess_nip_routing_dmn_unavailable_fails_safe() -> None:
    dmn = FakeDmnTransport()
    _register_classification(dmn)
    _register_sla(dmn)  # nip_routing NOT registered
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state())

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "dmn_indisponivel"


async def test_assess_nip_sla_failure_never_blocks_routing() -> None:
    """nip_sla is informative-only (the REAL timers are the BPMN's own BRT_NipSla) — its
    absence never flips route/motivo."""
    dmn = FakeDmnTransport()
    _register_classification(dmn)
    _register_routing(dmn)  # nip_sla NOT registered
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state())

    assert result["route"] == "instruct_nip"
    assert result["motivo_humano"] == "revisao_juridica_nip"
    assert "prazo_resposta_absoluto_iso" not in result


async def test_assess_nip_sla_receives_data_recebimento_anchor() -> None:
    """Donor divergence #1 (spec wins): the deployed nip_sla table's FEEL reads
    `data_recebimento_nip_iso` — assess must pass it alongside `classificacao`."""
    dmn = FakeDmnTransport()
    _register_all_nip(dmn)
    graph = _graph(dmn=dmn)

    await graph.assess(_nip_state(data_recebimento_nip_iso="2026-07-10"))

    sla_calls = [call for table, call in dmn.calls if table == DMN_NIP_SLA]
    assert sla_calls == [
        {"classificacao": "ASSISTENCIAL_CONTESTA_NEGATIVA", "data_recebimento_nip_iso": "2026-07-10"}
    ]


async def test_assess_nip_grupo_humano_out_of_allowlist_falls_back_juridico() -> None:
    """A DMN-returned group outside the closed set never reaches the dossier/engine — the same
    juridico-regulatorio fail-safe the BPMN's own BRT_Roteamento outputParameter applies."""
    dmn = FakeDmnTransport()
    _register_all_nip(dmn, roteamento="ELABORAR_RESPOSTA", grupo_humano="grupo-inventado")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_nip_state())

    assert result["grupo_humano"] == "juridico-regulatorio"


async def test_worker_pre_resolved_facts_passed_verbatim_to_dmns_never_recomputed() -> None:
    """ADR-0012 guard: the worker-pre-resolved facts reach the DMN calls unchanged — Gustavo
    consumes them, never computes/overrides them (both journeys)."""
    dmn = FakeDmnTransport()
    _register_calendar(dmn)
    _register_admissibility(dmn)
    graph = _graph(dmn=dmn)
    await graph.assess(_ans_state(dataset_complete=False, schema_valid=True, lgpd_anonimizado=False))
    admis_call = next(call for table, call in dmn.calls if table == DMN_ANS_ADMISSIBILITY)
    assert admis_call == {"dataset_complete": False, "schema_valid": True, "lgpd_anonimizado": False}

    dmn2 = FakeDmnTransport()
    _register_all_nip(dmn2)
    graph2 = _graph(dmn=dmn2)
    await graph2.assess(_nip_state(contesta_negativa=True, documentacao_suficiente=False))
    clf_call = next(call for table, call in dmn2.calls if table == DMN_NIP_CLASSIFICATION)
    assert clf_call["contesta_negativa"] is True
    rot_call = next(call for table, call in dmn2.calls if table == DMN_NIP_ROUTING)
    assert rot_call["documentacao_suficiente"] is False


async def test_full_turn_nip_starts_process_with_contract_variables() -> None:
    dmn = FakeDmnTransport()
    _register_all_nip(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_nip_state())

    assert result["route"] == "instruct_nip"
    assert result["desfecho"] == "nip_encaminhada_instrucao_humana"
    assert result["process_started"] is True
    assert result["business_key"] == "NIP-amh-000000042"
    assert await cibseven.find_active_instance("NIP-amh-000000042") is not None
    variables = cibseven.started_variables[0]
    assert variables["numero_nip_ans"] == "000000042"
    assert variables["contesta_negativa"] is True
    assert variables["referencia_negativa_original"] == "AUTH-guia-000"
    assert variables["dossie_gustavo"]["decisao_merito"] is None
    assert variables["gustavo_route"] == "instruct_nip"
    assert variables["motivo_encaminhamento"] == "revisao_juridica_nip"


# ---------------------------------------------------------------------------
# L0 structural guardrails (one per charter invariant)
# ---------------------------------------------------------------------------


def test_l0_guard_route_type_admits_no_adverse_variant() -> None:
    """Structural proof (no engine needed): the `Route` type admits only two literals — BOTH
    human-instruction destinations; no transmit/maintain/deny variant is expressible."""
    allowed = set(Route.__args__)  # type: ignore[attr-defined]
    assert allowed == {"review_submission", "instruct_nip"}
    for forbidden in ("transmitir", "manter_negativa", "negar", "auto_submit", "auto_responder"):
        assert forbidden not in allowed


def test_l0_guard_route_fail_safe_is_always_a_human_destination() -> None:
    assert GustavoGraph._route({}) == "instruct_nip"
    assert GustavoGraph._route({"fluxo": "ans_submit"}) == "review_submission"
    assert GustavoGraph._route({"fluxo": "nip"}) == "instruct_nip"
    assert GustavoGraph._route({"route": "review_submission"}) == "review_submission"
    assert GustavoGraph._route({"route": "garbage"}) == "instruct_nip"  # type: ignore[typeddict-item]


async def test_l0_guard_nip_manter_negativa_never_auto() -> None:
    """L0 GUARD (charter: `nip_manter_negativa` is a hard-deny PEP action — never exercised
    here): even for a NIP that CONTESTS a negativa with every fact unfavorable to the
    beneficiary, the engine variables ship decisao_nip=None and no fundamentacao — the
    maintain-denial can only be born in UT_RevisaoJuridicaNip (human)."""
    dmn = FakeDmnTransport()
    _register_all_nip(dmn)  # contesta-negativa -> REVISAO_JURIDICA (the only path that CAN maintain)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_nip_state())

    variables = cibseven.started_variables[0]
    assert variables["decisao_nip"] is None
    assert "fundamentacao_regulatoria" not in variables
    assert "texto_resposta_nip" not in variables
    assert "MANTER_NEGATIVA" not in json.dumps(variables, ensure_ascii=False, default=str)
    assert result["dossier"]["decisao_merito"] is None


async def test_l0_guard_dossier_never_carries_decision_or_signature() -> None:
    graph = _graph()
    j1 = await graph.review_submission(_ans_state(route="review_submission", motivo_humano="revisao_envio"))
    assert j1["dossier"]["decisao_merito"] is None
    assert j1["dossier"]["assinatura_envio"] is None
    j2 = await graph.instruct_nip(_nip_state(route="instruct_nip", motivo_humano="revisao_juridica_nip"))
    assert j2["dossier"]["decisao_merito"] is None
    assert j2["dossier"]["assinatura_envio"] is None


async def test_dossier_llm_call_is_phi_tagged() -> None:
    inference = _FakeInference(["narrativa"])
    graph = _graph(inference=inference)
    await graph.instruct_nip(_nip_state(route="instruct_nip", motivo_humano="revisao_juridica_nip"))
    assert inference.calls
    assert all(phi is True for _, phi in inference.calls)


async def test_dossier_llm_failure_never_blocks_the_human_route() -> None:
    graph = _graph(inference=_FailingInference())
    result = await graph.review_submission(_ans_state(route="review_submission"))
    assert result["dossier"]["narrativa"] == ""
    assert result["desfecho"] == "envio_encaminhado_revisao_humana"


# ---------------------------------------------------------------------------
# gather — best-effort J2 enrichment (never blocks routing)
# ---------------------------------------------------------------------------


async def test_gather_ans_submit_is_a_noop_fetch() -> None:
    fhir = _FakeFhirReader()
    graph = _graph(fhir=fhir)
    result = await graph.gather(_ans_state())
    assert result["gathered"] is True
    assert fhir.calls == []


async def test_gather_nip_without_fhir_reader_notes_the_gap() -> None:
    graph = _graph(fhir=None)
    result = await graph.gather(_nip_state())
    assert result["gathered"] is True
    assert result["gather_notes"]  # explicit gap note, not a silent empty


async def test_gather_nip_fhir_failure_is_best_effort_never_raises() -> None:
    fhir = _FakeFhirReader(fail=True)
    graph = _graph(fhir=fhir)
    result = await graph.gather(_nip_state(patient_ref="pseudo-benef-1"))
    assert result["gathered"] is True
    assert any("indisponivel" in note for note in result["gather_notes"])


async def test_gather_nip_with_fhir_reader_populates_facts() -> None:
    fhir = _FakeFhirReader()
    graph = _graph(fhir=fhir)
    result = await graph.gather(_nip_state(patient_ref="pseudo-benef-1"))
    assert result["nip_facts"] == {"resourceType": "Patient", "id": "pseudo-benef-1"}
    assert fhir.calls == ["pseudo-benef-1"]


# ---------------------------------------------------------------------------
# Caller-planted output-field probes (T1.12 mandatory hardening — bite-proof family).
# Unique low-entropy sentinel per output field (gitleaks hygiene), planted into caller state.
# ---------------------------------------------------------------------------

_SENTINEL = "PLANTED-SENTINEL-000"

_PLANTED_OUTPUTS: dict[str, Any] = {
    "gathered": True,
    "nip_facts": {"planted": _SENTINEL},
    "gather_notes": [_SENTINEL],
    "admissibilidade_envio": "SEGUE_ENVIO",
    "due_date": "leak-due-date-000",
    "sla_alerta_data": "leak-sla-data-000",
    "periodicidade_calendario": "leak-periodicidade-000",
    "fonte_regulatoria": "leak-fonte-000",
    "classificacao": "NAO_ASSISTENCIAL",
    "prazo_dias": 99,
    "grupo_revisor": "leak-grupo-revisor-000",
    "roteamento_nip": "ELABORAR_RESPOSTA",
    "grupo_humano": "leak-grupo-humano-000",
    "prazo_resposta_iso": "leak-prazo-000",
    "prazo_resposta_absoluto_iso": "leak-prazo-abs-000",
    "sla_alerta_iso": "leak-alerta-000",
    "sla_alerta_absoluto_iso": "leak-alerta-abs-000",
    "dmn_refs": {"nip_routing": f"forged#{_SENTINEL}"},
    "dmn_error": "leak-dmn-error-000",
    "route": "review_submission",  # planted route trying to cross journeys / skip the human NIP path
    "motivo_humano": "revisao_envio",
    "dossier": {"narrativa": _SENTINEL, "decisao_merito": "MANTER_NEGATIVA"},
    "process_key": "SP-OP-LEAK-000",
    "process_started": True,
    "business_key": "LEAK-BK-000000",
    "process_ref": {"instance_id": "leak-instance-000"},
    "desfecho": "leak-desfecho-000",
    "error": "leak-error-000",
}

# Every string fragment that must never surface in engine-bound variables / the dossier.
_PLANTED_FRAGMENTS: tuple[str, ...] = (
    _SENTINEL,
    "leak-",
    "LEAK-BK-000000",
    "forged",
    "SP-OP-LEAK-000",
    "MANTER_NEGATIVA",
)


def _assert_no_planted_fragment(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    for fragment in _PLANTED_FRAGMENTS:
        assert fragment not in text, f"planted fragment {fragment!r} leaked into: {text[:400]}"


async def test_receive_resets_every_output_field_on_success_paths() -> None:
    """Write-side unit proof: `receive` (success path, BOTH flows) returns a reset for EVERY
    planted output key, so downstream nodes can never observe a caller-planted output value."""
    graph = _graph()
    for base_state, expected_bk, expected_pk in (
        (_ans_state, "ANSSUB-amh-DIOPS_TRIMESTRAL-2026-Q1", PROCESS_KEY_ANS_SUBMIT),
        (_nip_state, "NIP-amh-000000042", PROCESS_KEY_NIP),
    ):
        result = await graph.receive(base_state(**_PLANTED_OUTPUTS))
        for key, planted in _PLANTED_OUTPUTS.items():
            assert key in result, f"receive did not return output field {key!r}"
            if key == "business_key":
                assert result[key] == expected_bk  # computed, never the planted sentinel
            elif key == "process_key":
                assert result[key] == expected_pk
            else:
                assert result[key] != planted, f"receive did not reset output field {key!r}"


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"tenant_id": ""}, "missing_tenant_id"),
        ({"fluxo": "fluxo-invalido-000"}, "invalid_fluxo"),
        ({"fluxo": "ans_submit", "report_type": "", "competencia": ""}, "missing_report_type_competencia"),
        ({"fluxo": "nip", "numero_nip_ans": ""}, "missing_numero_nip_ans"),
    ],
)
async def test_receive_fail_paths_reset_every_output_field(
    overrides: dict[str, Any], expected_error: str
) -> None:
    """Write-side unit proof for every receive-fail journey: `_fail_safe_min` resets ALL output
    fields (then sets its own route/motivo/error class token on top) — these paths are exactly
    where planted output fields would otherwise read through, because no downstream node
    overwrites them."""
    graph = _graph()
    result = await graph.receive(_nip_state(**{**_PLANTED_OUTPUTS, **overrides}))
    assert result["error"] == expected_error
    assert result["route"] in ("review_submission", "instruct_nip")  # a HUMAN destination, always
    for key, planted in _PLANTED_OUTPUTS.items():
        if key in ("route", "motivo_humano", "error", "process_key"):
            continue  # set to the graph's OWN values above (asserted separately)
        assert result[key] != planted, f"fail path did not reset output field {key!r}"
    assert result["business_key"] == ""  # never the planted key, never a fabricated identity
    assert result["motivo_humano"] != "revisao_envio" or expected_error == "missing_report_type_competencia"


@pytest.mark.parametrize("journey", ["ans_submit", "nip"])
async def test_caller_planted_outputs_never_reach_engine_vars_or_dossier(journey: str) -> None:
    """The charter's bite-proof probe: EVERY output field planted with a unique sentinel, full
    compiled turn on BOTH journeys — nothing planted reaches the engine variables, the dossier,
    or the ADR-0007 `dmn_decision_refs` audit trail; the DMN gate ACTUALLY runs."""
    dmn = FakeDmnTransport()
    if journey == "ans_submit":
        _register_calendar(dmn)
        _register_admissibility(dmn, "SEGUE_ENVIO")
        state = _ans_state(**_PLANTED_OUTPUTS)
        expected_bk = "ANSSUB-amh-DIOPS_TRIMESTRAL-2026-Q1"
    else:
        _register_all_nip(dmn)
        state = _nip_state(**_PLANTED_OUTPUTS)
        expected_bk = "NIP-amh-000000042"
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(state)

    assert dmn.calls, "the DMN gate must actually run (a planted error/route must not bail assess)"
    assert cibseven.started_variables
    _assert_no_planted_fragment(cibseven.started_variables[0])
    _assert_no_planted_fragment(result["dossier"])
    # Only transport-produced refs appear in the audit trail.
    assert all("forged" not in ref for ref in result["dmn_refs"].values())
    assert result["business_key"] == expected_bk
    assert await cibseven.find_active_instance(expected_bk) is not None
    assert await cibseven.find_active_instance("LEAK-BK-000000") is None


async def test_planted_route_cannot_cross_journeys_or_skip_the_human_task() -> None:
    """Charter probe: a caller-planted `route` must NEVER cause an auto-submission or an
    auto-NIP-response, and must never cross journeys. Planting route='review_submission' (+ a
    planted error, the pre-fix bail vector) on a NIP journey: receive wipes both, assess
    re-derives instruct_nip from the DMNs, and the engine start is the NIP process."""
    dmn = FakeDmnTransport()
    _register_all_nip(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(
        _nip_state(route="review_submission", error="planted", motivo_humano="revisao_envio")
    )

    assert result["route"] == "instruct_nip"  # DMN-derived, never the planted cross-journey route
    assert result["motivo_humano"] == "revisao_juridica_nip"
    assert result["desfecho"] == "nip_encaminhada_instrucao_humana"
    assert {t for t, _ in dmn.calls} == {DMN_NIP_CLASSIFICATION, DMN_NIP_SLA, DMN_NIP_ROUTING}
    variables = cibseven.started_variables[0]
    assert variables["gustavo_route"] == "instruct_nip"
    assert variables["decisao_nip"] is None

    # And the mirror probe: planted 'instruct_nip' on the ANS journey still lands on the human
    # filing review (never skips UT_RevisarEnvio's process, never starts the NIP process).
    dmn2 = FakeDmnTransport()
    _register_calendar(dmn2)
    _register_admissibility(dmn2, "SEGUE_ENVIO")
    cibseven2 = _RecordingCibSeven()
    compiled2 = _graph(dmn=dmn2, cibseven=cibseven2).compile_graph().compile()

    result2 = await compiled2.ainvoke(_ans_state(route="instruct_nip", error="planted"))

    assert result2["route"] == "review_submission"
    assert result2["desfecho"] == "envio_encaminhado_revisao_humana"
    assert cibseven2.started_variables[0]["gustavo_route"] == "review_submission"
    assert cibseven2.started_variables[0]["decisao_envio"] is None


@pytest.mark.parametrize("scenario", ["dmn_indisponivel_ans", "dmn_indisponivel_nip", "receive_fail"])
async def test_planted_output_sentinels_cleared_on_every_shortcut_path(scenario: str) -> None:
    """Shortcut journeys (DMN unavailable / receive-fail) deliberately never compute the assess
    outputs — planted sentinels must be reset, never read through into the dossier or engine
    variables (the exact class the R1 verifiers bit on fernando/carolina)."""
    dmn = FakeDmnTransport()  # NOTHING registered -> every DMN unavailable
    if scenario == "dmn_indisponivel_ans":
        state = _ans_state(**_PLANTED_OUTPUTS)
    elif scenario == "dmn_indisponivel_nip":
        state = _nip_state(**_PLANTED_OUTPUTS)
    else:
        state = _nip_state(**{**_PLANTED_OUTPUTS, "numero_nip_ans": ""})
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(state)

    _assert_no_planted_fragment(result["dossier"])
    assert result["dossier"]["fatos"].get("due_date") in ("", None)
    assert result["dossier"]["fatos"].get("prazo_resposta_absoluto_iso") in ("", None)
    if scenario == "receive_fail":
        # No derivable business key -> NO engine start (a garbage identity never reaches the
        # engine); the failure stays observable via the class token.
        assert result["process_started"] is False
        assert result["error"] == "missing_numero_nip_ans"
        assert not cibseven.started_variables
        assert await cibseven.find_active_instance("LEAK-BK-000000") is None
    else:
        assert result["motivo_humano"] == "dmn_indisponivel"
        assert "leak-dmn-error-000" not in result["dmn_error"]  # graph's own transport message
        _assert_no_planted_fragment(cibseven.started_variables[0])


# ---------------------------------------------------------------------------
# helena-class probes: PHI-bearing values never reach engine class-token fields
# ---------------------------------------------------------------------------


async def test_phi_bearing_fluxo_never_reaches_engine_variables() -> None:
    """A corrupted `fluxo` carrying a CPF (compromised upstream delegator / malformed A2A
    envelope) fails safe with a CLASS TOKEN — the CPF never reaches the dossier facts nor the
    engine-bound variables (helena R1 cycle-2 lesson applied to gustavo's journey selector)."""
    planted_cpf = "123.456.789-00"
    dmn = FakeDmnTransport()
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_nip_state(fluxo=f"nip {planted_cpf}"))

    assert result["error"] == "invalid_fluxo"
    assert result["route"] == "instruct_nip"  # human, conservative
    assert planted_cpf not in json.dumps(result["dossier"], ensure_ascii=False, default=str)
    # fluxo failed validation -> business key not derivable as ans_submit; NIP identity present,
    # so the start DID happen under the NIP contract — and the CPF is absent from its variables.
    if cibseven.started_variables:
        assert planted_cpf not in json.dumps(cibseven.started_variables[0], ensure_ascii=False, default=str)


async def test_free_text_tema_never_leaks_into_class_token_fields() -> None:
    """`tema_nip` (free text, contractually PHI-free but not code-enforced) travels ONLY in its
    own dedicated contract slot — never echoed into the bounded class-token fields
    (`motivo_encaminhamento`/`error`), even on the DMN-failure path."""
    leaked = "beneficiario CPF 123.456.789-00 contesta negativa"
    dmn = FakeDmnTransport()  # unregistered -> dmn_indisponivel failure-reason path
    graph = _graph(dmn=dmn)

    state = _nip_state(tema_nip=leaked)
    assess_result = await graph.assess(state)
    assert assess_result["motivo_humano"] == "dmn_indisponivel"

    merged: GustavoState = {**state, **assess_result, "dossier": {}}  # type: ignore[typeddict-item]
    variables = graph._contract_variables(merged)

    assert variables["tema_nip"] == leaked  # expected: contractual passthrough in its OWN slot
    assert variables["motivo_encaminhamento"] == "dmn_indisponivel"  # bounded class token only
    for fragment in ("123.456.789-00", "CPF"):
        assert fragment not in str(variables["motivo_encaminhamento"])
        assert fragment not in str(variables["gustavo_route"])


# ---------------------------------------------------------------------------
# start_process — idempotent, fail-observable
# ---------------------------------------------------------------------------


async def test_start_process_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-nip-1",
            process_key=PROCESS_KEY_NIP,
            business_key="NIP-amh-000000042",
            state="ACTIVE",
            already_existed=True,
        )
    )
    graph = _graph(cibseven=cibseven)
    state = _nip_state(
        route="instruct_nip", motivo_humano="revisao_juridica_nip", business_key="NIP-amh-000000042"
    )
    result = await graph.start_process(state)
    assert result["process_ref"]["instance_id"] == "existing-nip-1"
    assert result["process_ref"]["already_existed"] is True


async def test_start_process_records_error_on_cibseven_failure() -> None:
    class _FailingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
            raise CibSevenError("engine unreachable")

    graph = _graph(cibseven=_FailingCibSeven())
    result = await graph.start_process(_nip_state(route="instruct_nip"))
    assert result["process_started"] is False
    assert "start_process indisponivel" in result["error"]


async def test_start_process_refuses_non_derivable_identity() -> None:
    """receive-fail journeys (error set, business_key reset to ''): no engine start is attempted
    off a garbage identity — the case surfaces as process_started=False + the class token."""
    cibseven = _RecordingCibSeven()
    graph = _graph(cibseven=cibseven)
    result = await graph.start_process(
        _nip_state(error="missing_numero_nip_ans", business_key="", numero_nip_ans="")
    )
    assert result == {"process_started": False}
    assert not cibseven.started_variables
