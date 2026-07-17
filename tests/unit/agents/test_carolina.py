"""Unit tests for Carolina's REAL graph (T1.12 — SP-OP-CRED-001, (des)credenciamento).

Every node is exercised against fakes: `FakeDmnTransport`, `FakeCibSevenTransport`, and a small
in-file fake inference provider — same idiom as `tests/unit/agents/test_rafael.py`. Live-engine
acceptance was NOT attempted for this PR (see the PR body for the honest disclosure of why); this
file is Carolina's checkpointed unit coverage.

Covers: both adverse directions (credenciamento denial-candidate + descredenciamento), the
clerical E2E happy path, one guard test per L0 invariant enumerated in the T1.12 charter, and the
two helena-class probes (assess-failure fail-closed; a PHI-adjacent free-text field value never
reaching an engine-bound CLASS-TOKEN failure-reason field).

Replaces the pre-T1.12 stub (`validate_payment`/`route_approval`/`execute_payment` — a leftover
of the mismatched `spec/agents/carolina/agent.yaml`, see this test's own spec-sanity section and
`agents/carolina/graph.py`'s module docstring divergence #7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.carolina.graph import CarolinaGraph, CarolinaState, Route, _business_key, build
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["dossie factual sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


class _FailingInference:
    async def generate(self, prompt: str, *, phi: bool = False) -> str:
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


def _base_state(**overrides: Any) -> CarolinaState:
    state: CarolinaState = {
        "tenant_id": "amh",
        "prestador_id": "prestador-1",
        "canal": "portal",
        "direcao": "credenciamento",
        "tipo_prestador": "clinica",
        "origem_solicitacao": "prestador",
        "data_solicitacao_iso": "2026-07-16",
        "documentos_refs": [],
        "licenca_valida": True,
        "documentacao_completa": True,
        "dentro_criterios_rede": True,
        "indicio_irregularidade_sinalizado": False,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _graph(
    *,
    inference: Any | None = None,
    dmn: FakeDmnTransport | None = None,
    cibseven: FakeCibSevenTransport | None = None,
    fhir: Any | None = None,
) -> CarolinaGraph:
    return CarolinaGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        fhir=fhir,
    )


def _register_admissibility(dmn: FakeDmnTransport, roteamento: str = "SEGUE_ANALISE") -> None:
    dmn.register("cred_admissibility", [{"roteamento": roteamento, "motivo": "test"}])


def _register_route(dmn: FakeDmnTransport, roteamento: str) -> None:
    dmn.register("cred_route", [{"roteamento": roteamento, "motivo": "test"}])


def _register_sla(dmn: FakeDmnTransport) -> None:
    dmn.register(
        "cred_sla", [{"sla_analise": "P20D", "sla_alerta": "P12D", "fonte_regulatoria": "RN 566/567"}]
    )


def _register_prior_notice(dmn: FakeDmnTransport) -> None:
    dmn.register(
        "cred_prior_notice",
        [
            {
                "exige_notificacao_previa": True,
                "exige_substituto_equivalente": False,
                "prazo_notificacao": "P30D",
                "fonte_regulatoria": "RN 567",
            }
        ],
    )


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity
# ---------------------------------------------------------------------------


def test_carolina_agent_yaml_exists() -> None:
    agent_path = _AGENTS_ROOT / "carolina" / "agent.yaml"
    assert agent_path.exists()


def test_carolina_agent_yaml_owns_sp_op_cred_001() -> None:
    # T1.12 spec correction (R1 spec-audit, applied on this branch — graph.py module docstring
    # divergence #7): the pre-T1.12 yaml mis-described Carolina as a "Revenue Cycle / Pagamentos"
    # analyst duplicating andre's SP-OP-PAGTO-001 ownership, leaving SP-OP-CRED-001 with no
    # owning agent. Now that the yaml is truthful (donor-verbatim port + v2 scaffold fields),
    # these assertions pin the corrected contract so a regression re-introducing the mismatch
    # gets caught here.
    agent_path = _AGENTS_ROOT / "carolina" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)
    assert data["id"] == "carolina"
    assert data["process_keys"] == ["SP-OP-CRED-001"]
    assert data["phase"] == 3
    assert "Credenciamento" in data["role"]
    assert "SP-OP-PAGTO" not in str(data)  # the duplicate-PAGTO-ownership defect never returns


def test_carolina_definition_loads() -> None:
    from maezo.agents import AgentLoader

    definition = AgentLoader().load(_AGENTS_ROOT / "carolina" / "agent.yaml")
    assert definition.id == "carolina"
    assert definition.process_keys == ["SP-OP-CRED-001"]
    assert definition.phase == 3
    assert definition.autonomy_level == "L2"
    assert definition.security_zone == "phi"


# ---------------------------------------------------------------------------
# build(config) — fail-closed contract; fhir is OPTIONAL
# ---------------------------------------------------------------------------


def test_build_requires_inference_dmn_cibseven() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({})


def test_build_without_fhir_still_compiles() -> None:
    graph = build(
        {"inference": _FakeInference(), "dmn": FakeDmnTransport(), "cibseven": FakeCibSevenTransport()}
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


def test_business_key_format_without_protocolo() -> None:
    state = _base_state(tenant_id="amh", prestador_id="prest-42")
    assert _business_key(state) == "CRED-amh-prest-42"


def test_business_key_format_with_protocolo() -> None:
    state = _base_state(tenant_id="amh", prestador_id="prest-42", protocolo_cred="ciclo-2")
    assert _business_key(state) == "CRED-amh-prest-42-ciclo-2"


async def test_receive_assigns_business_key() -> None:
    graph = _graph()
    result = await graph.receive(_base_state())
    assert result["business_key"] == "CRED-amh-prestador-1"


async def test_receive_missing_runtime_context_routes_human_review() -> None:
    """Guard test: no tenant/prestador -> fail-safe human_review, never a silent default."""
    graph = _graph()
    result = await graph.receive(_base_state(tenant_id="", prestador_id=""))
    assert result["route"] == "human_review"
    assert "error" in result
    assert "business_key" not in result


# ---------------------------------------------------------------------------
# gather — best-effort summary read, never blocks routing
# ---------------------------------------------------------------------------


async def test_gather_without_fhir_reader_notes_the_gap() -> None:
    graph = _graph(fhir=None)
    result = await graph.gather(_base_state())
    assert result["gathered"] is True
    assert result["gather_notes"]  # explicit gap note, not a silent empty


async def test_gather_with_fhir_reader_populates_facts() -> None:
    fhir = _FakeSummaryReader()
    graph = _graph(fhir=fhir)
    result = await graph.gather(_base_state(patient_summary_ref="pseudo-benef-1"))
    assert result["gathered"] is True
    assert result["gather_notes"] == []
    assert fhir.calls == ["pseudo-benef-1"]


async def test_gather_fhir_failure_is_best_effort_never_raises() -> None:
    fhir = _FakeSummaryReader(fail=True)
    graph = _graph(fhir=fhir)
    result = await graph.gather(_base_state(patient_summary_ref="pseudo-benef-1"))
    assert result["gathered"] is True
    assert any("indisponivel" in note for note in result["gather_notes"])


async def test_gather_noop_when_already_routed_by_receive_guard() -> None:
    graph = _graph()
    result = await graph.gather(_base_state(error="contexto de runtime ausente"))
    assert result == {}


# ---------------------------------------------------------------------------
# assess — direction B (credenciamento): clerical auto-route + human-review paths
# ---------------------------------------------------------------------------


async def test_assess_clerical_credenciamento_routes_auto_route_and_skips_cred_route() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "CLERICAL_CREDENCIAR")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "credenciamento_clerical"
    assert result["admissibilidade"] == "CLERICAL_CREDENCIAR"
    called_tables = {table for table, _ in dmn.calls}
    assert called_tables == {"cred_admissibility"}  # cred_route/cred_sla/cred_prior_notice never called


async def test_assess_documentacao_pendente_routes_human_review_and_skips_cred_route() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "PENDENTE_DOCUMENTACAO")
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(documentacao_completa=False))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "documentacao_pendente"
    assert result["desfecho"] == "documentacao_pendente"
    called_tables = {table for table, _ in dmn.calls}
    assert called_tables == {"cred_admissibility"}


async def test_assess_credenciamento_analise_routes_human_review_gestao_rede_no_prior_notice() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_route(dmn, "ANALISE_CREDENCIAMENTO")
    _register_sla(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(licenca_valida=False))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "analise_credenciamento"
    assert result["grupo_humano"] == "gestao-rede"
    assert result["roteamento_natureza"] == "ANALISE_CREDENCIAMENTO"
    called_tables = {table for table, _ in dmn.calls}
    assert called_tables == {"cred_admissibility", "cred_route", "cred_sla"}  # no prior_notice


# ---------------------------------------------------------------------------
# assess — direction A (descredenciamento): always human, prior_notice consulted
# ---------------------------------------------------------------------------


async def test_assess_descredenciamento_routes_human_review_juridico_rede_with_prior_notice() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_route(dmn, "ANALISE_DESCREDENCIAMENTO")
    _register_sla(dmn)
    _register_prior_notice(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(direcao="descredenciamento"))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "analise_descredenciamento"
    assert result["grupo_humano"] == "juridico-rede"
    assert result["roteamento_natureza"] == "ANALISE_DESCREDENCIAMENTO"
    assert result["exige_notificacao_previa"] is True
    called_tables = {table for table, _ in dmn.calls}
    assert called_tables == {"cred_admissibility", "cred_route", "cred_sla", "cred_prior_notice"}


async def test_assess_descredenciamento_never_clerical_even_with_all_facts_favorable() -> None:
    """L0 guard: descredenciamento can NEVER hit CLERICAL_CREDENCIAR/auto_route, even when every
    clerical-looking fact is true — direcao alone (not the facts) forecloses the auto path,
    mirroring `cred_admissibility.dmn`'s own `r_descred_segue` rule (precedes `r_cred_clerical`,
    hitPolicy FIRST)."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")  # exactly what the real DMN returns for descred.
    _register_route(dmn, "ANALISE_DESCREDENCIAMENTO")
    _register_sla(dmn)
    _register_prior_notice(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(
        _base_state(
            direcao="descredenciamento",
            licenca_valida=True,
            documentacao_completa=True,
            dentro_criterios_rede=True,
        )
    )

    assert result["route"] == "human_review"
    assert result["route"] != "auto_route"


# ---------------------------------------------------------------------------
# assess — indicio de irregularidade: DMN-driven precedence (divergence #1)
# ---------------------------------------------------------------------------


async def test_assess_indicio_irregularidade_routes_human_review_via_dmn_never_auto_route() -> None:
    """Divergence #1 from the donor: no hand-coded Python bypass — `indicio_irregularidade_
    sinalizado=true` is passed as a DMN input and the DMN's own FIRST rules (`r_indicio_segue` /
    `r_indicio_descred`) decide. This test fakes exactly what the real deployed tables return for
    that input to prove the graph routes correctly off THEIR output, not off its own branch."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")  # r_indicio_segue: NEVER clerical when indicio=true
    _register_route(dmn, "ANALISE_DESCREDENCIAMENTO")  # r_indicio_descred: always this, any direcao
    _register_sla(dmn)
    _register_prior_notice(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(
        _base_state(
            direcao="credenciamento",  # even the FAVORABLE direction — indicio still forecloses auto
            indicio_irregularidade_sinalizado=True,
            licenca_valida=True,
            documentacao_completa=True,
            dentro_criterios_rede=True,
        )
    )

    assert result["route"] == "human_review"
    assert result["grupo_humano"] == "juridico-rede"
    # the admissibility call actually carried the signal to the DMN (never hand-coded around it).
    admis_call = next(v for t, v in dmn.calls if t == "cred_admissibility")
    assert admis_call["indicio_irregularidade_sinalizado"] is True


# ---------------------------------------------------------------------------
# assess — cred_route ANALISE_HUMANA catch-all (divergence #2: co-review branch, GAP-CRED-4/6)
# ---------------------------------------------------------------------------


async def test_assess_cred_route_catchall_routes_coreview_with_prior_notice() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "ANALISE_HUMANA")
    _register_route(dmn, "ANALISE_HUMANA")
    _register_sla(dmn)
    _register_prior_notice(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "human_review"
    # Divergence #2: the catch-all ALWAYS lands on the co-review (descredenciamento) branch,
    # never conditioned on `direcao` — matches the BPMN's GW_Natureza fix (GAP-CRED-4/6).
    assert result["motivo_humano"] == "analise_descredenciamento"
    assert result["grupo_humano"] == "juridico-rede"
    called_tables = {table for table, _ in dmn.calls}
    assert "cred_prior_notice" in called_tables


# ---------------------------------------------------------------------------
# assess — fail-closed on DMN unavailability (helena-class probe #1)
# ---------------------------------------------------------------------------


async def test_helena_class_probe_admissibility_dmn_unavailable_never_auto_routes() -> None:
    """ADR-0008/contract L1 hard: DMN unavailable NEVER auto-routes by omission — the
    Carolina-equivalent of Rafael's `test_assess_admissibility_dmn_unavailable_routes_human_
    never_auto_approves` / Helena's classify-failure fail-closed discipline. Carolina's routing
    is DMN-driven (not LLM-driven), so the analogous 'assess failure' is a DMN evaluation
    failure, not an LLM one."""
    dmn = FakeDmnTransport()  # nothing registered -> DmnEvaluationError
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert "dmn_error" in result


async def test_assess_cred_route_dmn_unavailable_routes_human_review() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    # cred_route deliberately NOT registered.
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"


async def test_assess_sla_and_prior_notice_failure_never_blocks_routing() -> None:
    """cred_sla/cred_prior_notice are informative-only (mirrors auth_sla) — their absence never
    flips route/motivo, unlike cred_admissibility/cred_route."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_route(dmn, "ANALISE_DESCREDENCIAMENTO")
    # cred_sla / cred_prior_notice deliberately NOT registered.
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(direcao="descredenciamento"))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "analise_descredenciamento"
    assert "sla_analise" not in result
    assert "exige_notificacao_previa" not in result


# ---------------------------------------------------------------------------
# L0 guard tests (one per invariant in the T1.12 charter)
# ---------------------------------------------------------------------------


def test_l0_guard_route_type_admits_no_adverse_variant() -> None:
    """Structural proof (no engine needed): the `Route` type admits only two literals, neither
    of which is a denial/de-credentialing variant — mirrors Rafael's
    `test_allowed_routes_never_include_a_denial_variant`."""
    allowed = set(Route.__args__)  # type: ignore[attr-defined]
    assert allowed == {"auto_route", "human_review"}
    for forbidden in ("negar", "deny", "descredenciar", "recusar", "reject"):
        assert forbidden not in allowed


def test_l0_guard_fail_safe_defaults_to_human_review_on_missing_route() -> None:
    assert CarolinaGraph._route({}) == "human_review"
    assert CarolinaGraph._route({"route": "auto_route"}) == "auto_route"
    assert CarolinaGraph._route({"route": "anything_else"}) == "human_review"


async def test_l0_guard_credenciamento_denial_candidate_never_auto_routes() -> None:
    """direcao=credenciamento with an irregular-looking license still never reaches auto_route —
    only ANALISE_HUMANA from cred_admissibility, then human_review via cred_route."""
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "ANALISE_HUMANA")
    _register_route(dmn, "ANALISE_CREDENCIAMENTO")
    _register_sla(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state(licenca_valida=False))

    assert result["route"] == "human_review"
    assert result["route"] != "auto_route"


@pytest.mark.parametrize("admissibilidade", ["SEGUE_ANALISE", "PENDENTE_DOCUMENTACAO", "ANALISE_HUMANA"])
async def test_l0_guard_only_clerical_credenciar_may_auto_route(admissibilidade: str) -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, admissibilidade)
    _register_route(dmn, "ANALISE_CREDENCIAMENTO")
    _register_sla(dmn)
    graph = _graph(dmn=dmn)

    result = await graph.assess(_base_state())

    assert result["route"] != "auto_route"


async def test_l0_guard_direcao_orients_branch_never_flips_route_outcome() -> None:
    """`direcao` changes WHICH human group/motivo, never WHETHER the case auto-routes — both
    directions land on human_review for the equivalent non-clerical DMN outcome."""
    dmn_cred = FakeDmnTransport()
    _register_admissibility(dmn_cred, "SEGUE_ANALISE")
    _register_route(dmn_cred, "ANALISE_CREDENCIAMENTO")
    _register_sla(dmn_cred)
    graph_cred = _graph(dmn=dmn_cred)
    result_cred = await graph_cred.assess(_base_state(direcao="credenciamento"))

    dmn_descred = FakeDmnTransport()
    _register_admissibility(dmn_descred, "SEGUE_ANALISE")
    _register_route(dmn_descred, "ANALISE_DESCREDENCIAMENTO")
    _register_sla(dmn_descred)
    _register_prior_notice(dmn_descred)
    graph_descred = _graph(dmn=dmn_descred)
    result_descred = await graph_descred.assess(_base_state(direcao="descredenciamento"))

    assert result_cred["route"] == result_descred["route"] == "human_review"
    assert result_cred["grupo_humano"] != result_descred["grupo_humano"]


def test_l0_guard_graph_never_references_financial_ceiling_facts() -> None:
    """Carolina has no financial-ceiling concept (Rafael/Pagto's `dentro_teto_l2`/`teto_ok`) —
    a lightweight, agent-scoped guard proving this graph never imports/references it, keeping the
    domains structurally separate (the repo-wide AST scanner for the financial workers themselves
    lives in `tests/unit/sec/test_dentro_teto_source.py`, which this PR does not touch)."""
    import maezo.agents.carolina.graph as carolina_graph

    source = Path(carolina_graph.__file__).read_text(encoding="utf-8")
    assert "dentro_teto" not in source
    assert "teto_ok" not in source


# ---------------------------------------------------------------------------
# helena-class probe #2: PHI-adjacent free-text field never leaks into a CLASS-TOKEN field
# ---------------------------------------------------------------------------


async def test_helena_class_probe_free_text_field_never_leaks_into_failure_reason_fields() -> None:
    """Adapted helena-class probe (R1 cycle-2 leak class): unlike Helena, Carolina's routing is
    DMN-driven, never LLM-classified from free text — there is no `_validate_extraction`-style
    injection vector to reproduce verbatim. The equivalent engine-variable-hygiene discipline
    here: `motivo_informado` (free text, contractually 'sem PHI de beneficiario' but not
    code-enforced) must NEVER be echoed into the CLASS-TOKEN failure-reason fields
    (`motivo_encaminhamento`) even though it legitimately travels in its OWN dedicated contract
    variable slot (`motivo_informado`, by design — SP-OP-CRED-001's own input variable)."""
    leaked = "paciente CPF 123.456.789-00 relatou irregularidade no atendimento"
    dmn = FakeDmnTransport()  # unregistered -> forces the dmn_indisponivel failure-reason path
    graph = _graph(dmn=dmn)

    state = _base_state(motivo_informado=leaked, direcao="credenciamento")
    assess_result = await graph.assess(state)
    assert assess_result["route"] == "human_review"
    assert assess_result["motivo_humano"] == "dmn_indisponivel"

    merged: CarolinaState = {**state, **assess_result, "dossier": {}}  # type: ignore[typeddict-item]
    variables = graph._contract_variables(merged)

    assert variables["motivo_informado"] == leaked  # expected, contractual passthrough
    assert variables["motivo_encaminhamento"] == "dmn_indisponivel"  # bounded class token only
    assert "dmn_error" not in variables  # never travels to the engine at all (matches donor)
    for fragment in ("123.456.789-00", "123.456.789", "CPF"):
        assert fragment not in str(variables["motivo_encaminhamento"])
        assert fragment not in str(variables["grupo_destino"])


# ---------------------------------------------------------------------------
# auto_route / human_review — dossier structural guardrail (L1 hard)
# ---------------------------------------------------------------------------


async def test_auto_route_dossier_never_carries_an_adverse_decision() -> None:
    graph = _graph()
    result = await graph.auto_route(_base_state(route="auto_route"))
    assert result["dossier"]["decisao_credenciamento"] is None
    assert result["dossier"]["decisao_descredenciamento"] is None
    assert result["dossier"]["route"] == "auto_route"


async def test_human_review_dossier_never_carries_an_adverse_decision() -> None:
    graph = _graph()
    result = await graph.human_review(
        _base_state(
            route="human_review", motivo_humano="analise_descredenciamento", direcao="descredenciamento"
        )
    )
    assert result["dossier"]["decisao_credenciamento"] is None
    assert result["dossier"]["decisao_descredenciamento"] is None
    assert result["dossier"]["motivo_humano"] == "analise_descredenciamento"


async def test_dossier_narrative_llm_call_is_phi_tagged() -> None:
    inference = _FakeInference(["narrativa"])
    graph = _graph(inference=inference)
    await graph.auto_route(_base_state(route="auto_route"))
    assert inference.calls
    assert all(phi is True for _, phi in inference.calls)


async def test_dossier_llm_failure_never_blocks_the_route() -> None:
    graph = _graph(inference=_FailingInference())
    result = await graph.auto_route(_base_state(route="auto_route"))
    assert result["dossier"]["narrativa"] == ""
    assert result["dossier"]["decisao_credenciamento"] is None


# ---------------------------------------------------------------------------
# start_process — idempotent, contract variables
# ---------------------------------------------------------------------------


async def test_start_process_starts_with_contract_variables() -> None:
    cibseven = FakeCibSevenTransport()
    graph = _graph(cibseven=cibseven)

    state = _base_state(
        route="auto_route", business_key="CRED-amh-prestador-1", dossier={"route": "auto_route"}
    )
    result = await graph.start_process(state)

    assert result["process_started"] is True
    assert result["business_key"] == "CRED-amh-prestador-1"
    assert result["process_ref"]["already_existed"] is False


async def test_start_process_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    business_key = "CRED-amh-prestador-1"
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-cred-1",
            process_key="SP-OP-CRED-001",
            business_key=business_key,
            state="ACTIVE",
            already_existed=True,
        )
    )
    graph = _graph(cibseven=cibseven)

    result = await graph.start_process(_base_state(business_key=business_key, route="human_review"))

    assert result["process_ref"]["instance_id"] == "existing-cred-1"
    assert result["process_ref"]["already_existed"] is True


async def test_start_process_records_error_on_cibseven_failure() -> None:
    class _FailingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
            raise CibSevenError("engine unreachable")

    graph = _graph(cibseven=_FailingCibSeven())
    result = await graph.start_process(_base_state(route="human_review"))
    assert result["process_started"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# contract variables
# ---------------------------------------------------------------------------


def test_contract_variables_include_dossier_and_route() -> None:
    graph = _graph()
    variables = graph._contract_variables(
        _base_state(
            route="human_review",
            motivo_humano="analise_credenciamento",
            grupo_humano="gestao-rede",
            dossier={"narrativa": "x"},
        )
    )
    assert variables["carolina_route"] == "human_review"
    assert variables["motivo_encaminhamento"] == "analise_credenciamento"
    assert variables["grupo_destino"] == "gestao-rede"
    assert variables["dossie_carolina"] == {"narrativa": "x"}
    assert variables["source_agent_id"] == "carolina"


def test_contract_variables_never_include_motivo_encaminhamento_on_auto_route() -> None:
    graph = _graph()
    variables = graph._contract_variables(_base_state(route="auto_route"))
    assert "motivo_encaminhamento" not in variables
    assert "grupo_destino" not in variables


def test_contract_variables_descredenciamento_fields_only_for_that_direction() -> None:
    graph = _graph()
    cred_vars = graph._contract_variables(_base_state(direcao="credenciamento"))
    assert "notificacao_previa_feita" not in cred_vars
    assert "tem_beneficiarios_vinculados" not in cred_vars

    descred_vars = graph._contract_variables(
        _base_state(
            direcao="descredenciamento", notificacao_previa_feita=True, tem_beneficiarios_vinculados=True
        )
    )
    assert descred_vars["notificacao_previa_feita"] is True
    assert descred_vars["tem_beneficiarios_vinculados"] is True


def test_contract_variables_never_include_rafaels_ceiling_fact() -> None:
    graph = _graph()
    variables = graph._contract_variables(_base_state())
    assert "dentro_teto_l2" not in variables


# ---------------------------------------------------------------------------
# Full-graph turns (compiled, in-memory — no engine; see PR body for integration disclosure)
# ---------------------------------------------------------------------------


async def test_full_turn_clerical_credenciamento_auto_routes_and_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "CLERICAL_CREDENCIAR")
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state())

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "credenciamento_clerical"
    assert result["process_started"] is True
    assert result["business_key"] == "CRED-amh-prestador-1"
    assert result["dossier"]["decisao_credenciamento"] is None
    assert result["dossier"]["decisao_descredenciamento"] is None


async def test_full_turn_descredenciamento_routes_human_review_and_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "SEGUE_ANALISE")
    _register_route(dmn, "ANALISE_DESCREDENCIAMENTO")
    _register_sla(dmn)
    _register_prior_notice(dmn)
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(
        _base_state(direcao="descredenciamento", tem_beneficiarios_vinculados=True)
    )

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "analise_descredenciamento"
    assert result["process_started"] is True
    assert result["dossier"]["decisao_descredenciamento"] is None
    assert result["desfecho"] == "analise_descredenciamento"


async def test_full_turn_credenciamento_denial_candidate_routes_human_review_and_starts_process() -> None:
    dmn = FakeDmnTransport()
    _register_admissibility(dmn, "ANALISE_HUMANA")  # e.g. licenca aparentemente irregular
    _register_route(dmn, "ANALISE_CREDENCIAMENTO")
    _register_sla(dmn)
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(licenca_valida=False))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "analise_credenciamento"
    assert result["process_started"] is True
    assert result["dossier"]["decisao_credenciamento"] is None
    assert result["desfecho"] == "analise_credenciamento"
