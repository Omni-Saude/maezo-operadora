"""Unit tests for Andre's REAL graph (T1.12 — SP-OP-PAGTO-001 risk dossier + analytics).

Every node is exercised against fakes: `FakeDmnTransport`, `FakeCibSevenTransport`, and small
in-file fake inference/FHIR/population providers — same idiom as `tests/unit/agents/
test_rafael.py`. Live-engine acceptance was NOT attempted for this PR (see the PR body for the
honest disclosure of why); this file is Andre's checkpointed unit coverage.

Covers: all THREE flows E2E (pagto_dossier clerical + every human faixa, population_analytics,
adequacao_dossier), one guard test per L0 invariant enumerated in the T1.12 charter, the
caller-planted-output probes (unique sentinel in EVERY output-only field, all flows + all
shortcut/fail paths — nothing may reach engine-bound variables/the dossier; a planted
route/faixa_valor must not skip the DMN gate nor start an instance), and the two helena-class
probes (assess-failure fail-closed; a PHI-bearing field value never reaching an engine-bound
CLASS-TOKEN failure-reason field).

Replaces the pre-T1.12 stub tests (`analyze_population`/`identify_risk_cohorts`/
`recommend_interventions` — the placeholder topology deleted by this task).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.andre.graph import (
    AndreGraph,
    AndreState,
    CohortAggregate,
    Route,
    _business_key,
    build,
)
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    FakeCibSevenTransport,
    ProcessInstance,
    StartClaimWithoutInstanceError,
    StartOutcome,
)
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie de risco sintetico"]
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


class _FakeSummaryReader:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[str] = []

    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        self.calls.append(patient_id)
        if self._fail:
            raise RuntimeError("HAPI FHIR unreachable")
        return {"resourceType": "Patient", "id": patient_id}


_CLEAN_AGGREGATE = CohortAggregate(
    cohort_id="cohort-amh-2026-06",
    dataset_ref="lake://ds/amh/2026-06/actuarial",
    metrics={"sinistro_agregado": 0.42},
    cohort_size=1200,
    k_anonymity=11,
)


class _FakePopulationClient:
    def __init__(
        self,
        *,
        actuarial: CohortAggregate | None = None,
        population: CohortAggregate | None = None,
        fail: bool = False,
    ) -> None:
        self._actuarial = actuarial or _CLEAN_AGGREGATE
        self._population = population or _CLEAN_AGGREGATE
        self._fail = fail
        self.calls: list[tuple[str, str]] = []

    async def actuarial_risk(self, cohort_id: str, *, features: list[str]) -> CohortAggregate:
        self.calls.append(("actuarial_risk", cohort_id))
        if self._fail:
            raise RuntimeError("lake unreachable")
        return self._actuarial

    async def population_metrics(self, cohort_id: str, *, features: list[str]) -> CohortAggregate:
        self.calls.append(("population_metrics", cohort_id))
        if self._fail:
            raise RuntimeError("lake unreachable")
        return self._population


def _pagto_state(**overrides: Any) -> AndreState:
    state: AndreState = {
        "flow": "pagto_dossier",
        "tenant_id": "amh",
        "canal": "a2a",
        # Synthetic, LOW-ENTROPY identifiers only (gitleaks hygiene): never use
        # realistic-looking ids/keys in fixtures — repeated zeros keep the business-key
        # format semantics without tripping the repo-wide secrets lane.
        "ordem_pagamento_id": "000000001",
        "prestador_id": "prestador-1",
        "tipo_pagamento": "prestador_rede",
        "valor_pagamento_cents": 85_000,  # R$ 850,00 — synthetic low value
        "moeda": "BRL",
        "competencia": "2026-06",
        "data_vencimento": "2026-08-01",
        "conta_origem_ref": "token-conta-1",
        "instrumento_pagamento": "pix",
        "dados_pagamento_validos": True,
        "lastro_confirmado": True,
        "dentro_teto_l2": True,
        "duplicidade_suspeita": False,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _population_state(**overrides: Any) -> AndreState:
    state: AndreState = {
        "flow": "population_analytics",
        "tenant_id": "amh",
        "canal": "a2a",
        "cohort_id": "cohort-amh-2026-06",
        "features": ["sinistro_agregado"],
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _adequacao_state(**overrides: Any) -> AndreState:
    state: AndreState = {
        "flow": "adequacao_dossier",
        "tenant_id": "amh",
        "canal": "a2a",
        "regiao_saude": "3550308",
        "especialidade": "cardiologia",
        "ciclo_avaliacao": "2026-Q2",
        "tipo_carater": "eletivo",
        "gap_adequacao": "GAP_CRITICO",
        "roteamento_remediacao": "ANALISE_HUMANA",
        "tempo_acesso_apurado_min": 95,
        "distancia_apurada_km": 62.5,
        "prestadores_disponiveis": 1,
        "cobertura_geo_suficiente": False,
        "dados_geo_completos": True,
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
    population: Any | None = None,
) -> AndreGraph:
    return AndreGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=audit_sink or FakeStartAuditSink(),
        fhir=fhir,
        population=population,
    )


def _register_admissibility(dmn: FakeDmnTransport, roteamento: str = "SEGUE_ROTEAMENTO") -> None:
    dmn.register("pagto_admissibility", [{"roteamento": roteamento, "motivo": "test"}])


def _register_alcada(
    dmn: FakeDmnTransport,
    faixa: str,
    grupo: str = "",
    *,
    tier_minimo: int = 0,
) -> None:
    dmn.register(
        "pagto_alcada",
        [
            {
                "faixa_valor": faixa,
                "grupo_aprovador": grupo,
                "tier_minimo": tier_minimo,
                "motivo": "test",
            }
        ],
    )


def _register_sla(dmn: FakeDmnTransport) -> None:
    dmn.register("pagto_sla", [{"sla_aprovacao": "P2D", "sla_alerta": "P1D", "fonte": "politica interna"}])


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity (R1 spec audit: andre's yaml is CORRECT — owns SP-OP-PAGTO-001)
# ---------------------------------------------------------------------------


def test_andre_agent_yaml_exists() -> None:
    agent_path = _AGENTS_ROOT / "andre" / "agent.yaml"
    assert agent_path.exists()


def test_andre_agent_yaml_owns_sp_op_pagto_001() -> None:
    # The R1 spec audit confirmed andre's agent.yaml is correct (owns SP-OP-PAGTO-001) — these
    # assertions pin that so a regression re-introducing a mismatch gets caught here.
    agent_path = _AGENTS_ROOT / "andre" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)
    assert data["id"] == "andre"
    assert data["security_zone"] == "phi"
    assert "SP-OP-PAGTO-001" in str(data)
    assert data["prompt_versions"] == {"system": "system-v1", "dossier": "dossier-v1"}


def test_andre_prompt_versions_match_agent_yaml() -> None:
    from maezo.agents.andre.graph import PROMPT_VERSIONS

    assert PROMPT_VERSIONS == {"system": "system-v1", "dossier": "dossier-v1"}


# ---------------------------------------------------------------------------
# build(config) — fail-closed contract; fhir/population are OPTIONAL
# ---------------------------------------------------------------------------


def test_build_requires_inference_dmn_cibseven() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({})


def test_build_without_fhir_and_population_still_compiles() -> None:
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
        "auto_route",
        "human_review",
        "start_process",
        "finalize",
    } <= node_names


# ---------------------------------------------------------------------------
# receive / business key
# ---------------------------------------------------------------------------


def test_business_key_pagto_by_ordem() -> None:
    assert _business_key(_pagto_state()) == "PAGTO-amh-000000001"


def test_business_key_pagto_by_lote_and_prestador() -> None:
    state = _pagto_state(ordem_pagamento_id="", numero_lote_tiss="lote-9")
    assert _business_key(state) == "PAGTO-amh-lote-9-prestador-1"


def test_business_key_adequacao_with_and_without_ciclo() -> None:
    assert _business_key(_adequacao_state()) == "ADEQ-amh-3550308-cardiologia-2026-Q2"
    assert (
        _business_key(_adequacao_state(ciclo_avaliacao="")) == "ADEQ-amh-3550308-cardiologia"
    )  # never a trailing empty segment


async def test_receive_assigns_pagto_business_key() -> None:
    result = await _graph().receive(_pagto_state())
    assert result["business_key"] == "PAGTO-amh-000000001"


async def test_receive_missing_tenant_routes_human_review() -> None:
    result = await _graph().receive(_pagto_state(tenant_id=""))
    assert result["route"] == "human_review"
    assert result["error"]
    # Sanitization: business_key resets to "" on the guard path — a caller-planted key never
    # survives, and start_process's error short-circuit stays closed.
    assert result["business_key"] == ""


async def test_receive_pagto_without_any_key_identifier_routes_human_review() -> None:
    result = await _graph().receive(_pagto_state(ordem_pagamento_id="", numero_lote_tiss="", prestador_id=""))
    assert result["route"] == "human_review"
    assert result["business_key"] == ""
    assert result["motivo_humano"] == "analise_humana"


async def test_receive_population_without_cohort_routes_human_review() -> None:
    result = await _graph().receive(_population_state(cohort_id=""))
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "ambiguidade"


async def test_receive_adequacao_without_cell_identity_routes_human_review() -> None:
    """Never a malformed payment key for an adequacao dossier missing its cell identity."""
    result = await _graph().receive(_adequacao_state(regiao_saude=""))
    assert result["route"] == "human_review"
    assert result["business_key"] == ""
    assert result["grupo_humano"] == "gestao-rede"


async def test_receive_unrecognized_flow_is_fail_neutral_never_a_payment() -> None:
    """An unrecognized `flow` gets conservative human review — no PAGTO business key, and (see
    the full-turn probe below) no process start."""
    result = await _graph().receive(_pagto_state(flow="exotic_flow"))
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "ambiguidade"
    assert result["business_key"] == ""


# ---------------------------------------------------------------------------
# Mandatory hardening: caller-planted output fields (DMN-gate bypass / audit forgery).
# Bakes in from the start the defect class R1 found on all four tranche-1 graphs.
# ---------------------------------------------------------------------------

_SENTINEL = "PLANTED_SENTINEL_7c2e"


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


def _planted_outputs() -> dict[str, Any]:
    """Adversarial overrides: EVERY output-only field pre-planted by the caller."""
    return {
        "gathered": True,
        "actuarial_aggregate": {"planted": _SENTINEL},
        "population_aggregate": {"planted": _SENTINEL},
        "aggregate_dataset_refs": [f"lake://{_SENTINEL}"],
        "egress_blocked": False,
        "gather_notes": [_SENTINEL],
        "admissibilidade": "SEGUE_ROTEAMENTO",
        "faixa_valor": "DENTRO_TETO_L2",
        "grupo_aprovador": _SENTINEL,
        "sla_aprovacao": _SENTINEL,
        "sla_alerta": _SENTINEL,
        "dmn_refs": {"pagto_alcada": f"forged#{_SENTINEL}"},
        "dmn_error": _SENTINEL,
        "dossier": {"narrativa": _SENTINEL, "decisao_pagamento": "APROVAR"},
        "route": "auto_route",
        "motivo_humano": "outro",
        "grupo_humano": _SENTINEL,
        "process_started": True,
        "business_key": f"PAGTO-{_SENTINEL}",
        "process_ref": {"instance_id": _SENTINEL},
        "desfecho": _SENTINEL,
        "error": _SENTINEL,
    }


def test_receive_is_the_single_graph_entry_node() -> None:
    """The receive-side sanitization closes the class ONLY if receive always runs first: assert
    the compiled topology has exactly one edge out of START, into `receive` — no entry can skip
    it (the gather/assess early-bail guards are safe solely because of this)."""
    compiled = _graph().compile_graph().compile()
    start_targets = [e.target for e in compiled.get_graph().edges if e.source == "__start__"]
    assert start_targets == ["receive"]


def test_output_field_partition_is_complete() -> None:
    """Completeness guard: every `AndreState` field is classified as exactly one of caller-input
    or output-only. Adding a new state field without classifying it (and, if output-only,
    without a sanitized default) fails here — the root-cause class stays closed as the state
    evolves."""
    from maezo.agents.andre.graph import _CALLER_INPUT_FIELDS, _output_field_resets

    annotations = set(AndreState.__annotations__)
    outputs = set(_output_field_resets())
    assert _CALLER_INPUT_FIELDS & outputs == set()  # no field in both sets
    assert _CALLER_INPUT_FIELDS | outputs == annotations  # no field in neither set


def test_output_field_resets_returns_fresh_containers() -> None:
    """The mutable `{}`/`[]` defaults must never be shared across turns."""
    from maezo.agents.andre.graph import _output_field_resets

    a, b = _output_field_resets(), _output_field_resets()
    a["dmn_refs"]["x"] = "y"
    a["gather_notes"].append("z")
    assert b["dmn_refs"] == {}
    assert b["gather_notes"] == []
    assert a is not b


async def test_planted_error_and_auto_route_never_bypass_dmn_gate() -> None:
    """Core probe: planting `error` + `route="auto_route"` + a forged clerical `faixa_valor` on
    an ABOVE-TETO payment. Without receive-side sanitization the planted error bails
    gather/assess (ZERO DMN calls) and the forged auto_route ships to the engine. With it: the
    DMN gate ACTUALLY runs and the DMN-driven route (human_review, ALCADA_L3) wins."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "ALCADA_L3", "aprovacao-financeira-l3", tier_minimo=3)
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(
        _pagto_state(
            valor_pagamento_cents=500_000_000,  # R$ 5MM — ALCADA_L3 territory
            dentro_teto_l2=False,
            error="planted",
            route="auto_route",
            faixa_valor="DENTRO_TETO_L2",
            dmn_refs={"pagto_alcada": "forged#rule1"},
        )
    )

    called_tables = {table for table, _ in dmn.calls}
    assert "pagto_admissibility" in called_tables  # the DMN gate ran (pre-fix: ZERO calls)
    assert "pagto_alcada" in called_tables
    assert result["route"] == "human_review"  # forged auto_route never survives
    assert result["faixa_valor"] == "ALCADA_L3"  # DMN-produced, not the planted clerical faixa
    assert cibseven.started_variables
    engine_vars = cibseven.started_variables[0]
    assert engine_vars["andre_route"] == "human_review"
    assert engine_vars["faixa_valor"] == "ALCADA_L3"
    assert engine_vars["motivo_encaminhamento"] == "aprovacao_alcada"
    assert "forged" not in json.dumps(engine_vars, ensure_ascii=False, default=str)


@pytest.mark.parametrize("plant_error", [True, False])
async def test_planted_dmn_refs_never_reach_engine_vars_or_dossier(plant_error: bool) -> None:
    """A caller-planted `dmn_refs` must never reach the engine's `dmn_decision_refs` variable
    nor the dossier — forged decision references in the ADR-0007 audit trail. `plant_error=True`
    is the bail-path variant (the planted error would have skipped the overwrite pre-fix);
    `plant_error=False` is the defense-in-depth variant. Only transport-produced refs appear on
    BOTH variants."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "DENTRO_TETO_L2", "clerical-pagamentos")
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    state = _pagto_state(dmn_refs={"pagto_alcada": "forged#rule99", "pagto_sla": "forged#rule1"})
    if plant_error:
        state["error"] = "planted"
    result = await compiled.ainvoke(state)

    # The refs that DO appear are the ones the (fake) transport actually produced.
    assert result["dmn_refs"] == {
        "pagto_admissibility": "pagto_admissibility#pagto_admissibility:1:test",
        "pagto_alcada": "pagto_alcada#pagto_alcada:1:test",
    }
    engine_vars = json.dumps(cibseven.started_variables[0], ensure_ascii=False, default=str)
    dossier = json.dumps(result["dossier"], ensure_ascii=False, default=str)
    assert "forged" not in engine_vars
    assert "forged" not in dossier
    assert result["dossier"]["dmn_decision_refs"] == result["dmn_refs"]


@pytest.mark.parametrize(
    "scenario",
    [
        "pagto_clerical",
        "pagto_pendente_dados",
        "pagto_dmn_indisponivel",
        "pagto_missing_context",
        "population",
        "adequacao",
        "unknown_flow",
    ],
)
async def test_planted_output_sentinels_cleared_on_every_path(scenario: str) -> None:
    """Sentinels planted in EVERY output-only field must never survive into the final state's
    engine-bound variables or dossier — on the clerical auto path AND on every fail-closed
    shortcut (pending data, DMN unavailable, missing runtime context) AND on the non-pagto flows
    (which must additionally never start a process, even with `process_*` planted)."""
    dmn = FakeDmnTransport()
    base_state: AndreState
    if scenario == "pagto_clerical":
        _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
        _register_alcada(dmn, "DENTRO_TETO_L2", "clerical-pagamentos")
        base_state = _pagto_state()
    elif scenario == "pagto_pendente_dados":
        _register_admissibility(dmn, "PENDENTE_DADOS")
        base_state = _pagto_state(dados_pagamento_validos=False)
    elif scenario == "pagto_dmn_indisponivel":
        base_state = _pagto_state()  # nothing registered -> DmnEvaluationError -> fail-closed
    elif scenario == "pagto_missing_context":
        base_state = _pagto_state(tenant_id="")
    elif scenario == "population":
        base_state = _population_state()
    elif scenario == "adequacao":
        base_state = _adequacao_state()
    else:
        base_state = _pagto_state(flow="exotic_flow")
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    state: dict[str, Any] = dict(base_state)
    state.update(_planted_outputs())
    result = await compiled.ainvoke(state)

    pagto_flow = scenario.startswith("pagto")
    if pagto_flow and scenario != "pagto_missing_context":
        assert cibseven.started_variables
        engine_vars = json.dumps(cibseven.started_variables[0], ensure_ascii=False, default=str)
        assert _SENTINEL not in engine_vars
        assert "forged" not in engine_vars
    elif scenario == "pagto_missing_context":
        # Sanitized empty business key + receive-guard error -> start short-circuits: nothing
        # reaches the engine at all (the planted key/vars never start an instance).
        assert cibseven.started_variables == []
        assert result["process_started"] is False
    else:
        # Non-pagto flows NEVER start a process — even with process_started/process_ref planted.
        assert cibseven.started_variables == []
        assert result["process_started"] is False
        assert result["process_ref"] == {}
    dossier = json.dumps(result.get("dossier", {}), ensure_ascii=False, default=str)
    assert _SENTINEL not in dossier
    assert "forged" not in dossier
    # The planted adverse decision NEVER survives into the dossier.
    if result.get("dossier"):
        assert result["dossier"]["decisao_pagamento"] is None
        assert result["dossier"]["preco_recomendado"] is None
    # The forged route never survives: clerical/population re-derive auto_route from the REAL
    # DMN/flow; every other scenario lands on the fail-safe human_review.
    if scenario == "pagto_clerical":
        assert result["route"] == "auto_route"
        assert result["desfecho"] == "dossie_pronto_clerical"
    elif scenario == "population":
        assert result["route"] == "auto_route"
        assert result["desfecho"] == "analytics_pronto"
    else:
        assert result["route"] == "human_review"


async def test_planted_business_key_never_reaches_the_engine_on_guard_paths() -> None:
    """A planted business key on a missing-key turn must not produce a start under the forged
    key (the sanitized empty key + error short-circuit closes start_process)."""
    cibseven = _RecordingCibSeven()
    compiled = _graph(cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(
        _pagto_state(
            ordem_pagamento_id="",
            numero_lote_tiss="",
            prestador_id="",
            business_key=f"PAGTO-{_SENTINEL}",
        )
    )

    assert cibseven.started_variables == []
    assert result["process_started"] is False
    assert result["business_key"] == ""


# ---------------------------------------------------------------------------
# gather — population aggregates (egress-gated), FHIR probe, fail-safe bail
# ---------------------------------------------------------------------------


async def test_gather_error_bail_reasserts_fail_safe_route() -> None:
    """The bail returns the fail-safe route, NEVER `{}` (mandatory hardening item 3)."""
    graph = _graph()
    result = await graph.gather(_pagto_state(error="contexto de runtime ausente (tenant_id)"))
    assert result == {"route": "human_review"}


async def test_assess_error_bail_reasserts_fail_safe_route() -> None:
    graph = _graph()
    result = await graph.assess(_pagto_state(error="contexto de runtime ausente (tenant_id)"))
    assert result == {"route": "human_review"}


async def test_gather_without_population_client_notes_the_gap() -> None:
    result = await _graph().gather(_population_state())
    assert result["gathered"] is True
    assert any("nao injetado" in note for note in result["gather_notes"])
    assert result["actuarial_aggregate"] == {}
    assert result["egress_blocked"] is False


async def test_gather_with_population_client_collects_kanon_aggregates() -> None:
    client = _FakePopulationClient()
    result = await _graph(population=client).gather(_population_state())
    assert result["gathered"] is True
    assert result["actuarial_aggregate"]["k_anonymity"] == 11
    assert result["population_aggregate"]["dataset_ref"] == "lake://ds/amh/2026-06/actuarial"
    assert result["aggregate_dataset_refs"] == [
        "lake://ds/amh/2026-06/actuarial",
        "lake://ds/amh/2026-06/actuarial",
    ]
    assert result["egress_blocked"] is False
    assert {name for name, _ in client.calls} == {"actuarial_risk", "population_metrics"}


async def test_gather_pagto_flow_collects_actuarial_only() -> None:
    client = _FakePopulationClient()
    result = await _graph(population=client).gather(_pagto_state(cohort_id="cohort-amh-2026-06"))
    assert result["actuarial_aggregate"]
    assert result["population_aggregate"] == {}
    assert {name for name, _ in client.calls} == {"actuarial_risk"}


async def test_gather_population_client_failure_is_best_effort_never_raises() -> None:
    client = _FakePopulationClient(fail=True)
    result = await _graph(population=client).gather(_population_state())
    assert result["gathered"] is True
    assert any("indisponivel" in note or "indisponiveis" in note for note in result["gather_notes"])
    assert result["egress_blocked"] is False


async def test_gather_blocks_aggregate_with_resolvable_phi_indication() -> None:
    """EGRESS GATE: a `Patient/...`-ref'd aggregate is suppressed (never emitted), marks
    egress_blocked, and the suppression note carries a CLASS TOKEN only — never the suspect
    value itself."""
    tainted = CohortAggregate(
        cohort_id="cohort-amh-2026-06",
        dataset_ref="Patient/12345",  # resolvable FHIR prefix — must be blocked
        metrics={"x": 1.0},
        cohort_size=100,
        k_anonymity=5,
    )
    client = _FakePopulationClient(actuarial=tainted)
    result = await _graph(population=client).gather(_pagto_state(cohort_id="cohort-amh-2026-06"))
    assert result["egress_blocked"] is True
    assert result["actuarial_aggregate"] == {}
    assert result["aggregate_dataset_refs"] == []
    notes = " ".join(result["gather_notes"])
    assert "PHI resolvivel" in notes
    assert "Patient/12345" not in notes  # class token only — the suspect value never echoed


async def test_gather_blocks_aggregate_without_k_anonymity() -> None:
    unanonymized = CohortAggregate(cohort_id="cohort-amh-2026-06", dataset_ref="lake://ds/x", k_anonymity=0)
    client = _FakePopulationClient(actuarial=unanonymized)
    result = await _graph(population=client).gather(_pagto_state(cohort_id="cohort-amh-2026-06"))
    assert result["egress_blocked"] is True
    assert result["actuarial_aggregate"] == {}


async def test_assess_egress_blocked_routes_human_phi_egress_risk() -> None:
    result = await _graph().assess(_population_state(egress_blocked=True))
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "phi_egress_risk"
    assert result["desfecho"] == "egresso_bloqueado"


async def test_gather_without_fhir_reader_notes_the_gap_on_pagto_flow() -> None:
    result = await _graph(fhir=None).gather(_pagto_state())
    assert result["gathered"] is True
    assert any("FHIR" in note for note in result["gather_notes"])


async def test_gather_fhir_failure_is_best_effort_and_raw_summary_never_stored() -> None:
    ok_reader = _FakeSummaryReader()
    result_ok = await _graph(fhir=ok_reader).gather(_pagto_state(patient_summary_ref="pseudo-1"))
    assert ok_reader.calls == ["pseudo-1"]
    # Egress chokepoint: the raw summary NEVER enters state — no output field carries it.
    assert "resourceType" not in json.dumps(result_ok, ensure_ascii=False, default=str)

    failing_reader = _FakeSummaryReader(fail=True)
    result_fail = await _graph(fhir=failing_reader).gather(_pagto_state(patient_summary_ref="pseudo-1"))
    assert result_fail["gathered"] is True
    assert any("FHIR indisponivel" in note for note in result_fail["gather_notes"])


# ---------------------------------------------------------------------------
# assess — pagto_dossier: clerical auto-route + every human faixa
# ---------------------------------------------------------------------------


async def test_assess_pagto_clerical_dentro_teto_l2_auto_routes() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "DENTRO_TETO_L2", "clerical-pagamentos")
    result = await _graph(dmn=dmn).assess(_pagto_state())

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "dossie_pronto_clerical"
    assert result["faixa_valor"] == "DENTRO_TETO_L2"
    assert result["grupo_aprovador"] == "clerical-pagamentos"  # DMN output echoed verbatim
    called_tables = {table for table, _ in dmn.calls}
    # BPMN sequence (divergence #2): no pagto_sla on the clerical path.
    assert called_tables == {"pagto_admissibility", "pagto_alcada"}


@pytest.mark.parametrize(
    ("faixa", "grupo_dmn", "expected_grupo"),
    [
        ("ALCADA_L1", "aprovacao-financeira-l1", "aprovacao-financeira-l1"),
        ("ALCADA_L2", "aprovacao-financeira-l2", "aprovacao-financeira-l2"),
        ("ALCADA_L3", "aprovacao-financeira-l3", "aprovacao-financeira-l3"),
        ("ANALISE_HUMANA", "comite-financeiro", "comite-financeiro"),
    ],
)
async def test_assess_pagto_above_teto_faixas_route_human_with_tier_group(
    faixa: str, grupo_dmn: str, expected_grupo: str
) -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, faixa, grupo_dmn)
    _register_sla(dmn)
    result = await _graph(dmn=dmn).assess(
        _pagto_state(valor_pagamento_cents=500_000_000, dentro_teto_l2=False)
    )

    assert result["route"] == "human_review"
    assert result["faixa_valor"] == faixa
    assert result["grupo_humano"] == expected_grupo
    expected_motivo = "analise_humana" if faixa == "ANALISE_HUMANA" else "aprovacao_alcada"
    assert result["motivo_humano"] == expected_motivo
    # BPMN sequence (divergence #2): SLA evaluated on the human branch with the REAL faixa.
    sla_call = next(v for t, v in dmn.calls if t == "pagto_sla")
    assert sla_call["faixa_valor"] == faixa
    assert result["sla_aprovacao"] == "P2D"


async def test_assess_pagto_unknown_alcada_group_falls_back_to_closed_map() -> None:
    """A DMN-emitted group outside the closed set never becomes a human destination — the
    faixa->group map wins (ADR-0018 part 3)."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "ALCADA_L2", "grupo-desconhecido")
    _register_sla(dmn)
    result = await _graph(dmn=dmn).assess(_pagto_state(dentro_teto_l2=False))
    assert result["grupo_humano"] == "aprovacao-financeira-l2"


async def test_assess_pagto_pendente_dados_routes_coordenacao_before_alcada() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "PENDENTE_DADOS")
    result = await _graph(dmn=dmn).assess(_pagto_state(dados_pagamento_validos=False))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "pendencia_dados"
    assert result["grupo_humano"] == "coordenacao-financeira"  # BPMN UT_AnaliseAdmissibilidade
    called_tables = {table for table, _ in dmn.calls}
    assert called_tables == {"pagto_admissibility"}  # terminates before alcada/sla


async def test_assess_pagto_duplicity_signal_is_dmn_decided_with_refined_class_token() -> None:
    """Divergence #1 from the donor: no hand-coded Python bypass — `duplicidade_suspeita=true`
    is passed as a DMN input and the table's own FIRST rule (`r_duplicidade_suspeita`) decides
    ANALISE_HUMANA. The bounded class token is then refined to `duplicidade_suspeita` from the
    state's own informative signal."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "ANALISE_HUMANA")  # what r_duplicidade_suspeita actually returns
    result = await _graph(dmn=dmn).assess(_pagto_state(duplicidade_suspeita=True))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "duplicidade_suspeita"
    admis_call = next(v for t, v in dmn.calls if t == "pagto_admissibility")
    assert admis_call["duplicidade_suspeita"] is True  # signal carried TO the DMN, never around it


async def test_assess_pagto_admissibility_analise_humana_without_duplicity() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "ANALISE_HUMANA")
    result = await _graph(dmn=dmn).assess(_pagto_state(lastro_confirmado=False))
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "analise_humana"
    assert result["grupo_humano"] == "coordenacao-financeira"


async def test_assess_pagto_out_of_allowlist_admissibility_routes_comite() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "LIBERAR")  # hostile/unexpected output — not in the allowlist
    result = await _graph(dmn=dmn).assess(_pagto_state())
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "ambiguidade"
    assert result["grupo_humano"] == "comite-financeiro"


async def test_assess_pagto_out_of_allowlist_faixa_never_auto_routes() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "LIBERAR_TUDO", "grupo-inexistente")  # hostile faixa
    result = await _graph(dmn=dmn).assess(_pagto_state())
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "ambiguidade"
    assert result["grupo_humano"] == "comite-financeiro"


async def test_assess_pagto_clerical_faixa_without_worker_teto_fact_routes_comite() -> None:
    """Defense in depth: a DENTRO_TETO_L2 faixa WITHOUT the worker-pre-resolved dentro_teto_l2
    fact is inconsistent — conservative comite, never auto (the fact is CONSUMED, not trusted
    from the DMN alone)."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "DENTRO_TETO_L2", "clerical-pagamentos")
    result = await _graph(dmn=dmn).assess(_pagto_state(dentro_teto_l2=False))
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "ambiguidade"


async def test_assess_pagto_sla_failure_never_blocks_routing() -> None:
    """pagto_sla is informative-only (mirrors auth_sla) — its absence never flips
    route/motivo."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "ALCADA_L1", "aprovacao-financeira-l1")
    # pagto_sla deliberately NOT registered.
    result = await _graph(dmn=dmn).assess(_pagto_state(dentro_teto_l2=False))
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "aprovacao_alcada"
    assert "sla_aprovacao" not in result


# ---------------------------------------------------------------------------
# assess — population_analytics / adequacao_dossier (no pagto DMN ever)
# ---------------------------------------------------------------------------


async def test_assess_population_auto_routes_with_zero_dmn_calls() -> None:
    dmn = FakeDmnTransport()
    result = await _graph(dmn=dmn).assess(_population_state())
    assert result["route"] == "auto_route"
    assert result["desfecho"] == "analytics_pronto"
    assert dmn.calls == []  # no payment DMN for autonomous analytics


async def test_assess_adequacao_always_human_gestao_rede_with_zero_dmn_calls() -> None:
    dmn = FakeDmnTransport()
    result = await _graph(dmn=dmn).assess(_adequacao_state())
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dossie_remediacao"
    assert result["grupo_humano"] == "gestao-rede"
    assert result["desfecho"] == "dossie_remediacao_humano"
    assert dmn.calls == []  # gap/remediation already resolved by ADEQUACAO-001's own DMNs


async def test_assess_unrecognized_flow_never_touches_payment_dmns() -> None:
    dmn = FakeDmnTransport()
    result = await _graph(dmn=dmn).assess(_pagto_state(flow="exotic_flow"))
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "ambiguidade"
    assert dmn.calls == []


# ---------------------------------------------------------------------------
# helena-class probe #1: DMN unavailable -> fail-closed (never auto by omission)
# ---------------------------------------------------------------------------


async def test_helena_class_probe_admissibility_dmn_unavailable_never_auto_routes() -> None:
    """ADR-0008/contract L1 hard: DMN unavailable NEVER auto-routes by omission — Andre's
    routing is DMN-driven, so the analogous 'assess failure' is a DMN evaluation failure."""
    dmn = FakeDmnTransport()  # nothing registered -> DmnEvaluationError
    result = await _graph(dmn=dmn).assess(_pagto_state())
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert result["grupo_humano"] == "comite-financeiro"
    assert "dmn_error" in result


async def test_assess_alcada_dmn_unavailable_routes_human_review() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    # pagto_alcada deliberately NOT registered.
    result = await _graph(dmn=dmn).assess(_pagto_state())
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"


# ---------------------------------------------------------------------------
# helena-class probe #2: PHI-bearing field value never reaches engine-bound class-token fields
# ---------------------------------------------------------------------------


async def test_helena_class_probe_phi_bearing_error_text_never_reaches_engine_vars() -> None:
    """Engine-variable hygiene: a DMN transport failure whose exception text carries PHI-looking
    content must never be echoed into ANY engine-bound variable — the failure reason travels
    ONLY as the bounded class token (`motivo_encaminhamento=dmn_indisponivel`); `dmn_error`
    (raw text) stays state-only."""
    leaked = "paciente CPF 123.456.789-00 internado"

    class _LeakyDmn(FakeDmnTransport):
        async def evaluate(
            self,
            decision_key: str,
            variables: dict[str, Any],
            *,
            tenant: str | None = None,
        ) -> Any:
            from maezo.tools.workers.dmn_transport import DmnEvaluationError

            raise DmnEvaluationError(f"engine 500: {leaked}")

    graph = _graph(dmn=_LeakyDmn())
    state = _pagto_state()
    assess_result = await graph.assess(state)
    assert assess_result["route"] == "human_review"
    assert assess_result["motivo_humano"] == "dmn_indisponivel"
    assert leaked in assess_result["dmn_error"]  # state-only diagnostic

    merged: AndreState = {**state, **assess_result, "dossier": {}}  # type: ignore[typeddict-item]
    variables = graph._contract_variables(merged)

    assert variables["motivo_encaminhamento"] == "dmn_indisponivel"  # bounded class token only
    assert "dmn_error" not in variables  # never travels to the engine at all
    serialized = json.dumps(variables, ensure_ascii=False, default=str)
    for fragment in ("123.456.789-00", "CPF", "internado"):
        assert fragment not in serialized


# ---------------------------------------------------------------------------
# L0 guard tests (one per invariant in the T1.12 charter)
# ---------------------------------------------------------------------------


def test_l0_guard_route_type_admits_no_adverse_variant() -> None:
    """Structural proof: the `Route` type admits only two literals, neither of which is a
    release/approve/price variant."""
    allowed = set(Route.__args__)  # type: ignore[attr-defined]
    assert allowed == {"auto_route", "human_review"}
    for forbidden in ("liberar", "release", "aprovar", "approve", "pagar", "pay", "precificar", "price"):
        assert forbidden not in allowed


def test_l0_guard_fail_safe_defaults_to_human_review_on_missing_route() -> None:
    assert AndreGraph._route({}) == "human_review"
    assert AndreGraph._route({"route": "auto_route"}) == "auto_route"
    assert AndreGraph._route({"route": "anything_else"}) == "human_review"


@pytest.mark.parametrize("faixa", ["ALCADA_L1", "ALCADA_L2", "ALCADA_L3", "ANALISE_HUMANA"])
async def test_l0_guard_only_dentro_teto_l2_may_auto_route(faixa: str) -> None:
    """Everything above the L2 ceiling / ambiguous requires the human approver — auto_route is
    exclusively the clerical below-ceiling dossier."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, faixa, "comite-financeiro")
    _register_sla(dmn)
    result = await _graph(dmn=dmn).assess(_pagto_state(dentro_teto_l2=False))
    assert result["route"] != "auto_route"


async def test_l0_guard_dossier_never_carries_payment_or_price_decision() -> None:
    graph = _graph()
    auto = await graph.auto_route(_pagto_state(route="auto_route"))
    human = await graph.human_review(_pagto_state(route="human_review", motivo_humano="aprovacao_alcada"))
    for result in (auto, human):
        assert result["dossier"]["decisao_pagamento"] is None
        assert result["dossier"]["preco_recomendado"] is None
        assert result["dossier"]["fhir_patient_id"] is None


async def test_l0_guard_dossier_llm_failure_never_blocks_the_route() -> None:
    graph = _graph(inference=_FailingInference())
    result = await graph.human_review(_pagto_state(route="human_review", motivo_humano="aprovacao_alcada"))
    assert result["dossier"]["narrativa"] == ""
    assert result["dossier"]["decisao_pagamento"] is None
    assert result["desfecho"] == "dossie_aprovacao_humana"


async def test_dossier_narrative_llm_call_is_phi_tagged() -> None:
    inference = _FakeInference(["narrativa"])
    graph = _graph(inference=inference)
    await graph.auto_route(_pagto_state(route="auto_route"))
    assert inference.calls
    assert all(phi is True for _, phi in inference.calls)


async def test_dossier_narrative_llm_call_declares_task_kind_reasoning() -> None:
    """CC-12/BEA-01 (ADR-0009 §2): the risk/adequacy dossier narrative is what a human reviewer
    reads before deciding — `reasoning`, not `task_default`."""
    inference = _FakeInference(["narrativa"])
    graph = _graph(inference=inference)
    await graph.auto_route(_pagto_state(route="auto_route"))
    assert inference.task_kinds == ["reasoning"]


def test_l0_guard_graph_never_computes_the_ceiling_fact() -> None:
    """`dentro_teto_l2` is CONSUMED (pure pass-through into DMN input / contract variables),
    never computed: the graph's CODE (AST — docstrings/comments excluded) contains no ceiling
    arithmetic — no comparison involving `valor_pagamento_cents`/`dentro_teto_l2`, no
    `CeilingResolver`/`within_l2_ceiling` reference. (The repo-wide AST scanner for the
    financial workers lives in `tests/unit/sec/test_dentro_teto_source.py`, untouched by this
    PR.)"""
    import ast

    import maezo.agents.andre.graph as andre_graph

    tree = ast.parse(Path(andre_graph.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            assert name not in ("CeilingResolver", "within_l2_ceiling"), ast.dump(node)
        if isinstance(node, ast.Compare):
            segment = ast.dump(node)
            assert "valor_pagamento_cents" not in segment, segment
            assert "dentro_teto_l2" not in segment, segment


def test_l0_guard_no_release_worker_is_ever_invoked_by_the_graph() -> None:
    """The adverse-effect workers (`release_high_value_payment`/`release_low_value_payment`)
    are engine-side, human-gated — this graph never imports/invokes them."""
    import maezo.agents.andre.graph as andre_graph

    source = Path(andre_graph.__file__).read_text(encoding="utf-8")
    assert "from maezo.tools.workers.pagto" not in source
    assert "from maezo.tools.workers import pagto" not in source
    for line in source.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "release_high_value_payment(" not in line
        assert "release_low_value_payment(" not in line


# ---------------------------------------------------------------------------
# start_process — idempotent, pagto-only, contract variables
# ---------------------------------------------------------------------------


async def test_start_process_starts_pagto_with_contract_variables() -> None:
    cibseven = _RecordingCibSeven()
    graph = _graph(cibseven=cibseven)

    state = _pagto_state(
        route="auto_route",
        business_key="PAGTO-amh-000000001",
        dossier={"route": "auto_route"},
        faixa_valor="DENTRO_TETO_L2",
        grupo_aprovador="clerical-pagamentos",
    )
    result = await graph.start_process(state)

    assert result["process_started"] is True
    assert result["business_key"] == "PAGTO-amh-000000001"
    assert result["process_ref"]["already_existed"] is False
    variables = cibseven.started_variables[0]
    assert variables["valor_pagamento_cents"] == 85_000
    assert variables["dentro_teto_l2"] is True  # pure pass-through of the worker fact
    assert variables["data_vencimento"] == "2026-08-01"  # GAP-PAGTO-7 seeded
    assert variables["source_agent_id"] == "andre"
    assert variables["dossie_andre"] == {"route": "auto_route"}
    assert variables["andre_route"] == "auto_route"
    assert "motivo_encaminhamento" not in variables  # auto path carries no human class token


async def test_start_process_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    business_key = "PAGTO-amh-000000001"
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-pagto-1",
            process_key="SP-OP-PAGTO-001",
            business_key=business_key,
            state="ACTIVE",
            already_existed=True,
        )
    )
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(_pagto_state(business_key=business_key, route="human_review"))

    assert result["process_ref"]["instance_id"] == "existing-pagto-1"
    assert result["process_ref"]["already_existed"] is True


async def test_start_process_noop_for_population_adequacao_and_unknown_flows() -> None:
    cibseven = _RecordingCibSeven()
    graph = _graph(cibseven=cibseven)
    assert await graph.start_process(_population_state(route="auto_route")) == {}
    assert await graph.start_process(_adequacao_state(route="human_review")) == {}
    assert await graph.start_process(_pagto_state(flow="exotic_flow")) == {}
    assert cibseven.started_variables == []


async def test_start_process_records_error_on_cibseven_failure() -> None:
    class _FailingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
            raise CibSevenError("engine unreachable")

    graph = _graph(cibseven=_FailingCibSeven())
    result = await graph.start_process(_pagto_state(route="human_review"))
    assert result["process_started"] is False
    assert "error" in result


async def test_start_process_error_without_business_key_short_circuits() -> None:
    cibseven = _RecordingCibSeven()
    graph = _graph(cibseven=cibseven)
    result = await graph.start_process(
        _pagto_state(error="contexto de runtime ausente (tenant_id)", business_key="")
    )
    assert result == {"process_started": False}
    assert cibseven.started_variables == []


# ---------------------------------------------------------------------------
# F3 BLOCKER-1 — start_process must never report a start it did not cause
# ---------------------------------------------------------------------------


async def test_start_process_reports_the_typed_outcome_for_a_fresh_start() -> None:
    """`process_started` is now DERIVED, not hard-coded, and the token that produced it travels
    with it so the caller never has to re-derive the distinction from `already_existed`."""
    graph = _graph(cibseven=_RecordingCibSeven())
    result = await graph.start_process(_pagto_state(route="auto_route", business_key="PAGTO-amh-1"))

    assert result["process_started"] is True
    assert result["process_ref"]["start_outcome"] == StartOutcome.STARTED.value


async def test_start_process_reports_already_active_as_live_not_as_a_new_start() -> None:
    cibseven = FakeCibSevenTransport()
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="live-1",
            process_key="SP-OP-PAGTO-001",
            business_key="PAGTO-amh-1",
            state="ACTIVE",
        )
    )
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(_pagto_state(business_key="PAGTO-amh-1", route="human_review"))

    # A live instance exists for this key, so the case IS live — but it was not started here.
    assert result["process_started"] is True
    assert result["process_ref"]["start_outcome"] == StartOutcome.ALREADY_ACTIVE.value


async def test_start_process_never_reports_started_for_an_already_settled_order() -> None:
    """THE F3 BLOCKER, at the reporting site. The strict gate refuses to restart an already-paid
    order; `graph.py` used to answer that refusal with a hard-coded `process_started: True` and a
    blank instance id, and `delegation.py` shipped it over A2A as a success."""
    audit_sink = FakeStartAuditSink()
    cibseven = FakeCibSevenTransport()
    graph = _graph(cibseven=cibseven, audit_sink=audit_sink)
    state = _pagto_state(business_key="PAGTO-amh-1", route="human_review")

    first = await graph.start_process(state)
    assert first["process_started"] is True

    # The engine finishes the payment instance; a re-delivery arrives.
    cibseven.seed_instance(
        ProcessInstance(
            instance_id=first["process_ref"]["instance_id"],
            process_key="SP-OP-PAGTO-001",
            business_key="PAGTO-amh-1",
            state="COMPLETED",
        )
    )
    second = await graph.start_process(state)

    assert second["process_started"] is False, "an already-settled order was reported as started"
    assert second["process_ref"]["start_outcome"] == StartOutcome.ALREADY_COMPLETED.value
    assert second["process_ref"]["instance_id"], "the refusal must still name the real instance"


async def test_start_process_does_not_swallow_an_undecidable_strict_gate_hit() -> None:
    """`StartClaimWithoutInstanceError` is deliberately NOT a `CibSevenError`, so the graph's
    `except CibSevenError` cannot turn a wedged payment key into a routine "engine unavailable"
    (which a caller would retry forever while believing nothing was wrong)."""
    graph = _graph(cibseven=FakeCibSevenTransport(), audit_sink=FakeStartAuditSink(already_audited=True))

    with pytest.raises(StartClaimWithoutInstanceError):
        await graph.start_process(_pagto_state(business_key="PAGTO-amh-1", route="human_review"))


# ---------------------------------------------------------------------------
# contract variables
# ---------------------------------------------------------------------------


def test_contract_variables_include_dossier_route_and_class_token_on_human() -> None:
    graph = _graph()
    variables = graph._contract_variables(
        _pagto_state(
            route="human_review",
            motivo_humano="aprovacao_alcada",
            grupo_humano="aprovacao-financeira-l2",
            faixa_valor="ALCADA_L2",
            grupo_aprovador="aprovacao-financeira-l2",
            dossier={"narrativa": "x"},
            dmn_refs={"pagto_alcada": "pagto_alcada#id1"},
            aggregate_dataset_refs=["lake://ds/1"],
        )
    )
    assert variables["andre_route"] == "human_review"
    assert variables["motivo_encaminhamento"] == "aprovacao_alcada"
    assert variables["grupo_destino"] == "aprovacao-financeira-l2"
    assert variables["faixa_valor"] == "ALCADA_L2"
    assert variables["dossie_andre"] == {"narrativa": "x"}
    assert variables["dmn_decision_refs"] == {"pagto_alcada": "pagto_alcada#id1"}
    assert variables["aggregate_dataset_refs"] == ["lake://ds/1"]
    assert variables["source_agent_id"] == "andre"


def test_contract_variables_sanitized_none_fields_become_empty_strings() -> None:
    """Post-sanitization `faixa_valor`/`grupo_aprovador`/`motivo_humano` EXIST with value
    None/"" — the `or`-based defaults must still apply (never the string 'None' in an engine
    variable)."""
    graph = _graph()
    state: dict[str, Any] = dict(_pagto_state())
    state.update({"faixa_valor": None, "grupo_aprovador": None, "motivo_humano": None, "grupo_humano": ""})
    state["route"] = "human_review"
    variables = graph._contract_variables(state)  # type: ignore[arg-type]
    assert variables["faixa_valor"] == ""
    assert variables["grupo_aprovador"] == ""
    assert variables["motivo_encaminhamento"] == "outro"
    assert variables["grupo_destino"] == "comite-financeiro"
    string_vars = {k: v for k, v in variables.items() if isinstance(v, str)}
    assert "None" not in json.dumps(string_vars, ensure_ascii=False)


def test_contract_variables_never_include_motivo_on_auto_route() -> None:
    graph = _graph()
    variables = graph._contract_variables(_pagto_state(route="auto_route"))
    assert "motivo_encaminhamento" not in variables
    assert "grupo_destino" not in variables


# ---------------------------------------------------------------------------
# Full-graph turns (compiled, in-memory — no engine; see PR body for integration disclosure)
# ---------------------------------------------------------------------------


async def test_full_turn_pagto_clerical_auto_routes_and_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "DENTRO_TETO_L2", "clerical-pagamentos")
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    compiled = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_pagto_state())

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "dossie_pronto_clerical"
    assert result["process_started"] is True
    assert result["business_key"] == "PAGTO-amh-000000001"
    assert result["dossier"]["decisao_pagamento"] is None
    assert result["dossier"]["preco_recomendado"] is None


async def test_full_turn_pagto_above_teto_routes_human_and_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ROTEAMENTO")
    _register_alcada(dmn, "ALCADA_L2", "aprovacao-financeira-l2", tier_minimo=2)
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    inference = _FakeInference(["dossie sintetico"])
    compiled = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_pagto_state(valor_pagamento_cents=100_000_000, dentro_teto_l2=False))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "aprovacao_alcada"
    assert result["desfecho"] == "dossie_aprovacao_humana"
    assert result["process_started"] is True
    variables = cibseven.started_variables[0]
    assert variables["grupo_destino"] == "aprovacao-financeira-l2"
    assert variables["faixa_valor"] == "ALCADA_L2"
    assert result["dossier"]["decisao_pagamento"] is None


async def test_full_turn_pagto_pendente_dados_routes_human_and_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "PENDENTE_DADOS")
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_pagto_state(dados_pagamento_validos=False))

    assert result["route"] == "human_review"
    assert result["desfecho"] == "pagamento_pendente_dados"
    assert result["process_started"] is True
    assert cibseven.started_variables[0]["motivo_encaminhamento"] == "pendencia_dados"


async def test_full_turn_population_analytics_auto_routes_without_process() -> None:
    client = _FakePopulationClient()
    cibseven = _RecordingCibSeven()
    inference = _FakeInference(["dossie de coorte"])
    compiled = _graph(inference=inference, cibseven=cibseven, population=client).compile_graph().compile()

    result = await compiled.ainvoke(_population_state())

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "analytics_pronto"
    assert result["process_started"] is False
    assert cibseven.started_variables == []
    assert result["dossier"]["fatos_agregados"]["actuarial_aggregate"]["k_anonymity"] == 11
    assert result["dossier"]["decisao_pagamento"] is None


async def test_full_turn_population_egress_block_routes_human_without_process() -> None:
    tainted = CohortAggregate(cohort_id="fhir:Patient/1", dataset_ref="lake://ds/x", k_anonymity=5)
    client = _FakePopulationClient(actuarial=tainted)
    cibseven = _RecordingCibSeven()
    compiled = _graph(cibseven=cibseven, population=client).compile_graph().compile()

    result = await compiled.ainvoke(_population_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "phi_egress_risk"
    assert result["desfecho"] == "egresso_bloqueado"
    assert cibseven.started_variables == []
    # The suspect value never survives anywhere in the dossier.
    assert "fhir:Patient/1" not in json.dumps(result["dossier"], ensure_ascii=False, default=str)


async def test_full_turn_adequacao_routes_human_gestao_rede_without_process() -> None:
    cibseven = _RecordingCibSeven()
    inference = _FakeInference(["dossie de remediacao"])
    compiled = _graph(inference=inference, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_adequacao_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dossie_remediacao"
    assert result["grupo_humano"] == "gestao-rede"
    assert result["desfecho"] == "dossie_remediacao_humano"
    assert result["business_key"] == "ADEQ-amh-3550308-cardiologia-2026-Q2"
    assert result["process_started"] is False
    assert cibseven.started_variables == []
    # The already-resolved adequacao facts are ECHOED in the dossier (never recomputed).
    assert result["dossier"]["fatos_agregados"]["adequacao"]["gap_adequacao"] == "GAP_CRITICO"
    assert result["dossier"]["decisao_pagamento"] is None
