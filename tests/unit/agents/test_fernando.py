"""Unit tests for Fernando's REAL graph (T1.12 — SP-OP-INADIMPLENCIA-001 notify/escalate).

Every node is exercised against fakes: `FakeDmnTransport`, `FakeCibSevenTransport`, a small
in-file fake inference provider, and a fake `WhatsAppSender`. Live-engine acceptance is out of
scope for this file — see the PR body for compose-stack status.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.fernando.graph import (
    DMN_PURGA,
    DMN_SLA,
    DMN_STATUS,
    FernandoGraph,
    FernandoState,
    Route,
    _business_key,
    build,
)
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["texto sintetico"]
        self.calls: list[tuple[str, bool]] = []

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


class _FailingInference:
    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        raise RuntimeError("LLM provider unavailable")


class _FakeWhatsApp:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.sent: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("whatsapp gateway unreachable")
        self.sent.append((to_hash, text))
        return {"ok": True}


class _FailingCibSeven(FakeCibSevenTransport):
    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        raise CibSevenError("engine unreachable")


def _base_state(**overrides: Any) -> FernandoState:
    state: FernandoState = {
        "tenant_id": "amh",
        "intencao": "notificacao_previa",
        "canal": "whatsapp",
        "numero_contrato": "CONTRATO-001",
        "matricula_beneficiario": "MAT-001",
        "beneficiario_pseudo_id": "pseudo-123",
        "to_hash": "hash-abc",
        "tipo_plano": "individual",
        "origem_solicitacao": "cobranca",
        "competencias_em_aberto": ["2026-05", "2026-06"],
        "meses_inadimplencia": 2,
        "valor_total_devido_cents": 45000,
        "dentro_periodo_minimo": True,
        "notificacao_previa_feita": False,
        "dentro_janela_purga": True,
        "ja_em_rescisao_cancel": False,
        "documentos_refs": [],
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _graph(
    *,
    inference: Any | None = None,
    dmn: FakeDmnTransport | None = None,
    cibseven: FakeCibSevenTransport | None = None,
    whatsapp: Any | None = None,
) -> FernandoGraph:
    return FernandoGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        whatsapp=whatsapp or _FakeWhatsApp(),
    )


def _register_status(dmn: FakeDmnTransport, roteamento: str, motivo: str = "test") -> None:
    dmn.register(DMN_STATUS, [{"roteamento": roteamento, "motivo": motivo}])


def _register_purga(dmn: FakeDmnTransport) -> None:
    dmn.register(
        DMN_PURGA,
        [
            {
                "prazo_purga": "P10D",
                "prazo_notificacao_previa": "P50D",
                "periodo_minimo": "P60D",
                "fonte_regulatoria": "RN 593",
            }
        ],
    )


def _register_sla(dmn: FakeDmnTransport) -> None:
    dmn.register(DMN_SLA, [{"sla_analise": "P10D", "sla_alerta": "P7D", "fonte_regulatoria": "RN 593"}])


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity
# ---------------------------------------------------------------------------


def test_fernando_agent_yaml_exists() -> None:
    agent_path = _AGENTS_ROOT / "fernando" / "agent.yaml"
    assert agent_path.exists()


def test_fernando_agent_yaml_has_required_fields() -> None:
    agent_path = _AGENTS_ROOT / "fernando" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)
    assert data is not None
    assert data["id"] == "fernando"
    assert "name" in data
    assert "role" in data
    assert "tools" in data


def test_fernando_definition_loads() -> None:
    from maezo.agents import AgentLoader

    definition = AgentLoader().load(_AGENTS_ROOT / "fernando" / "agent.yaml")
    assert definition.id == "fernando"


# ---------------------------------------------------------------------------
# build(config) — fail-closed contract; all four deps required
# ---------------------------------------------------------------------------


def test_build_requires_inference_dmn_cibseven_whatsapp() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({})


def test_build_missing_whatsapp_fails_closed() -> None:
    with pytest.raises(ValueError, match="whatsapp"):
        build({"inference": _FakeInference(), "dmn": FakeDmnTransport(), "cibseven": FakeCibSevenTransport()})


def test_build_compiles_with_all_deps() -> None:
    graph = build(
        {
            "inference": _FakeInference(),
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "whatsapp": _FakeWhatsApp(),
        }
    )
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {"receive", "assess", "notify", "escalate", "start_process"} <= node_names


# ---------------------------------------------------------------------------
# receive / business key — fail-closed, class tokens only (never a raw value)
# ---------------------------------------------------------------------------


def test_business_key_uses_numero_contrato() -> None:
    state = _base_state(tenant_id="amh", numero_contrato="C-42")
    assert _business_key(state) == "INAD-amh-C-42"


def test_business_key_falls_back_to_matricula() -> None:
    state = _base_state(tenant_id="amh", numero_contrato="", matricula_beneficiario="MAT-9")
    assert _business_key(state) == "INAD-amh-MAT-9"


async def test_receive_assigns_business_key() -> None:
    graph = _graph()
    result = await graph.receive(_base_state())
    assert result["business_key"] == "INAD-amh-CONTRATO-001"


async def test_receive_missing_tenant_id_fails_closed() -> None:
    graph = _graph()
    result = await graph.receive(_base_state(tenant_id=""))
    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "falha_tecnica"
    assert result["error"] == "missing_tenant_id"


async def test_receive_missing_contract_key_fails_closed() -> None:
    graph = _graph()
    result = await graph.receive(_base_state(numero_contrato="", matricula_beneficiario=""))
    assert result["route"] == "escalate"
    assert result["error"] == "missing_contract_key"


async def test_receive_invalid_intencao_fails_closed_without_leaking_value() -> None:
    """The 'classify-failure fail-closed' analog for Fernando: `intencao` has no LLM-classify
    step (unlike Helena), but plays the exact same structural role — a closed-enum routing
    field. An unrecognized value must fail CLOSED to escalate, never a silent `notify`."""
    graph = _graph()
    result = await graph.receive(_base_state(intencao="quero_cancelar_agora"))
    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "ambiguidade"
    assert result["error"] == "invalid_intencao"
    assert "quero_cancelar_agora" not in str(result)


# ---------------------------------------------------------------------------
# J1 (notificacao_previa) — informational only, never adverse
# ---------------------------------------------------------------------------


async def test_assess_notificacao_previa_aguarda_purga_routes_notify() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "AGUARDA_PURGA")
    _register_purga(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(_base_state(intencao="notificacao_previa"))
    assert result["route"] == "notify"
    assert result["status_inadimplencia"] == "AGUARDA_PURGA"
    assert result["prazo_purga_iso"] == "P10D"


async def test_assess_notificacao_previa_pendente_notificacao_routes_notify() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "PENDENTE_NOTIFICACAO")
    _register_purga(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(_base_state(intencao="notificacao_previa", notificacao_previa_feita=False))
    assert result["route"] == "notify"
    assert result["status_inadimplencia"] == "PENDENTE_NOTIFICACAO"


async def test_full_turn_notify_sends_whatsapp_no_process_started() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "PENDENTE_NOTIFICACAO")
    _register_purga(dmn)
    whatsapp = _FakeWhatsApp()
    cibseven = FakeCibSevenTransport()
    graph = _graph(dmn=dmn, whatsapp=whatsapp, cibseven=cibseven).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(intencao="notificacao_previa"))

    assert result["route"] == "notify"
    assert result["mensagem_enviada"] is True
    assert len(whatsapp.sent) == 1
    assert whatsapp.sent[0][0] == "hash-abc"
    assert "process_started" not in result  # start_process node never reached
    assert await cibseven.find_active_instance("INAD-amh-CONTRATO-001") is None


async def test_notify_message_never_carries_suspension_or_rescission_communication() -> None:
    """L0 GUARD: J1's notification INFORMS — it NEVER communicates an adverse outcome."""
    dmn = FakeDmnTransport()
    _register_status(dmn, "AGUARDA_PURGA")
    _register_purga(dmn)
    graph = _graph(dmn=dmn)
    state = _base_state(intencao="notificacao_previa")
    state.update(await graph.assess(state))  # type: ignore[typeddict-item]
    result = await graph.notify(state)
    assert result["mensagem"]["comunicacao_suspensao"] is None
    assert result["mensagem"]["comunicacao_rescisao"] is None


async def test_notify_whatsapp_failure_never_escalates() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "AGUARDA_PURGA")
    _register_purga(dmn)
    whatsapp = _FakeWhatsApp(fail=True)
    graph = _graph(dmn=dmn, whatsapp=whatsapp)
    state = _base_state(intencao="notificacao_previa")
    state.update(await graph.assess(state))  # type: ignore[typeddict-item]
    result = await graph.notify(state)
    assert result["mensagem_enviada"] is False
    assert "envio WhatsApp indisponivel" in result["mensagem"]["envio_nota"]


# ---------------------------------------------------------------------------
# J2 (acompanhamento_purga) — reads pre-resolved facts, follows the DMN (respond or escalate)
# ---------------------------------------------------------------------------


async def test_assess_acompanhamento_purga_open_routes_notify() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "AGUARDA_PURGA")
    _register_purga(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(_base_state(intencao="acompanhamento_purga"))
    assert result["route"] == "notify"


async def test_assess_acompanhamento_purga_elapsed_routes_escalate() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "SEGUE_ANALISE")
    _register_sla(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(
        _base_state(intencao="acompanhamento_purga", dentro_janela_purga=False, notificacao_previa_feita=True)
    )
    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "segue_analise"


async def test_worker_pre_resolved_facts_passed_verbatim_to_dmn_never_recomputed() -> None:
    """L0 GUARD: Fernando reads `meses_inadimplencia`/`dentro_janela_purga`/etc. pre-resolved by
    `operadora.inadimplencia.resolve_facts` — he never computes or invents them. Proven
    behaviorally: the exact input state values reach the DMN call unchanged."""
    dmn = FakeDmnTransport()
    _register_status(dmn, "AGUARDA_PURGA")
    _register_purga(dmn)
    graph = _graph(dmn=dmn)
    state = _base_state(
        intencao="acompanhamento_purga",
        meses_inadimplencia=7,
        dentro_periodo_minimo=True,
        notificacao_previa_feita=True,
        dentro_janela_purga=True,
    )
    await graph.assess(state)
    status_calls = [call for call in dmn.calls if call[0] == DMN_STATUS]
    assert len(status_calls) == 1
    _, dmn_input = status_calls[0]
    assert dmn_input["meses_inadimplencia"] == 7
    assert dmn_input["dentro_periodo_minimo"] is True
    assert dmn_input["notificacao_previa_feita"] is True
    assert dmn_input["dentro_janela_purga"] is True


# ---------------------------------------------------------------------------
# J3 (analise_inadimplencia | rescisao) — ALWAYS routes to human
# ---------------------------------------------------------------------------


async def test_assess_analise_inadimplencia_always_escalates_even_when_dmn_says_aguarda_purga() -> None:
    """L0 GUARD: J3 always human — an explicit analysis request escalates unconditionally, even
    when the DMN's own facts would otherwise permit a neutral notify (AGUARDA_PURGA)."""
    dmn = FakeDmnTransport()
    _register_status(dmn, "AGUARDA_PURGA")
    _register_sla(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(_base_state(intencao="analise_inadimplencia"))
    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "analise_solicitada"


async def test_assess_analise_inadimplencia_segue_analise_motivo() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "SEGUE_ANALISE")
    _register_sla(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(_base_state(intencao="analise_inadimplencia"))
    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "segue_analise"


async def test_assess_rescisao_shortcuts_without_consulting_status_dmn() -> None:
    """L0 GUARD: a rescission indicium/request NEVER passes through `inadimplencia_status` — it
    is always an unconditional human handoff (never a neutral, DMN-gated case)."""
    dmn = FakeDmnTransport()
    _register_sla(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(_base_state(intencao="rescisao"))
    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "indicio_rescisao"
    assert result["motivo_categoria"] == "rescisao"
    assert all(call[0] != DMN_STATUS for call in dmn.calls)


async def test_full_turn_analise_inadimplencia_starts_process_no_decision_ever_set() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "SEGUE_ANALISE")
    _register_sla(dmn)
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(dmn=dmn, cibseven=cibseven, inference=inference).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(intencao="analise_inadimplencia"))

    assert result["route"] == "escalate"
    assert result["process_started"] is True
    assert result["business_key"] == "INAD-amh-CONTRATO-001"
    assert result["dossier"]["decisao_inadimplencia"] is None
    assert result["dossier"]["decisao_recomendada"] is None


async def test_full_turn_rescisao_starts_process_motivo_rescisao() -> None:
    dmn = FakeDmnTransport()
    _register_sla(dmn)
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["dossie sintetico"])
    graph = _graph(dmn=dmn, cibseven=cibseven, inference=inference).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(intencao="rescisao"))

    assert result["process_started"] is True
    assert result["motivo_humano"] == "indicio_rescisao"
    assert result["desfecho"] == "encaminhado_analise_humana"


# ---------------------------------------------------------------------------
# DMN fail-closed (mirrors Helena's classify-failure discipline)
# ---------------------------------------------------------------------------


async def test_assess_status_dmn_unavailable_fails_closed_to_escalate() -> None:
    dmn = FakeDmnTransport()  # inadimplencia_status NOT registered -> DmnEvaluationError
    _register_sla(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(_base_state(intencao="notificacao_previa"))
    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert "dmn_error" in result


async def test_assess_status_unknown_roteamento_fails_closed_to_escalate() -> None:
    """Fail-safe fechado: an out-of-allowlist DMN value never 'passes through' as an implicit
    auto-response — treated as a contract violation and escalated conservatively."""
    dmn = FakeDmnTransport()
    _register_status(dmn, "SUSPENDER")  # must never exist per contract; defensive test anyway
    _register_sla(dmn)
    graph = _graph(dmn=dmn)
    result = await graph.assess(_base_state(intencao="notificacao_previa"))
    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "ambiguidade"


def test_route_fail_safe_defaults_to_escalate_on_missing_route() -> None:
    assert FernandoGraph._route({}) == "escalate"  # type: ignore[arg-type]
    assert FernandoGraph._route({"route": "notify"}) == "notify"  # type: ignore[typeddict-item]
    assert FernandoGraph._route({"route": "garbage"}) == "escalate"  # type: ignore[typeddict-item]


# ---------------------------------------------------------------------------
# L0 structural guardrails
# ---------------------------------------------------------------------------


def test_allowed_routes_never_include_an_adverse_variant() -> None:
    """Structural proof (no engine needed): the `Route` type admits only two literals — neither
    suspends, rescinds, nor denies."""
    allowed = set(Route.__args__)  # type: ignore[attr-defined]
    assert allowed == {"notify", "escalate"}
    assert "suspender" not in allowed
    assert "rescindir" not in allowed
    assert "negar" not in allowed


async def test_escalate_dossier_never_carries_a_decision() -> None:
    graph = _graph()
    state = _base_state(intencao="analise_inadimplencia", route="escalate", motivo_humano="segue_analise")
    result = await graph.escalate(state)
    assert result["dossier"]["decisao_inadimplencia"] is None
    assert result["dossier"]["decisao_recomendada"] is None


def test_engine_variables_never_carry_a_decision() -> None:
    graph = _graph()
    state = _base_state(
        intencao="analise_inadimplencia",
        route="escalate",
        motivo_humano="segue_analise",
        dossier={"narrativa": "fatos objetivos"},
    )
    variables = graph._inadimplencia_variables(state)
    assert variables["decisao_inadimplencia"] is None


async def test_phi_bearing_intencao_never_reaches_engine_variables() -> None:
    """The 'PHI-bearing field value never reaches engine-bound variables' probe: an
    (attacker-/upstream-LLM-)corrupted `intencao` carrying a CPF must never leak into the
    dossier facts or the SP-OP-INADIMPLENCIA-001 engine variables — class tokens only, exactly
    Helena's R1 cycle-2 lesson applied to Fernando's own routing-determinant field.

    Note: `result["intencao"]` (the raw graph STATE) legitimately still carries whatever the
    caller passed in — LangGraph merges the initial input verbatim for keys no node overwrites,
    and no node here ever overwrites `intencao`. That is expected and fine; the security
    property under test is narrower and load-bearing: the CPF must never be echoed into the
    DOSSIER FACTS or the ENGINE-BOUND `variables` dict that `start_process` actually sends.
    """
    planted_cpf = "123.456.789-00"
    dmn = FakeDmnTransport()
    _register_sla(dmn)
    cibseven = FakeCibSevenTransport()
    graph_instance = _graph(dmn=dmn, cibseven=cibseven)
    compiled = graph_instance.compile_graph().compile()

    result = await compiled.ainvoke(_base_state(intencao=planted_cpf))

    assert result["route"] == "escalate"
    assert result["error"] == "invalid_intencao"
    assert result["process_started"] is True
    assert result["dossier"]["fatos"]["intencao"] is None
    assert planted_cpf not in str(result["dossier"])

    # The authoritative check: reconstruct the EXACT variables dict `start_process` sent to the
    # engine (same method, same final state) and assert the CPF is absent everywhere in it.
    variables = graph_instance._inadimplencia_variables(result)
    assert planted_cpf not in str(variables)
    assert "intencao" not in variables  # never even a field in the contract's variable set


async def test_dmn_llm_calls_are_phi_tagged() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "AGUARDA_PURGA")
    _register_purga(dmn)
    inference = _FakeInference(["texto"])
    graph = _graph(dmn=dmn, inference=inference)
    state = _base_state(intencao="notificacao_previa")
    state.update(await graph.assess(state))  # type: ignore[typeddict-item]
    await graph.notify(state)
    assert len(inference.calls) == 1
    assert inference.calls[0][1] is True  # phi=True


async def test_message_llm_failure_never_blocks_notify() -> None:
    dmn = FakeDmnTransport()
    _register_status(dmn, "AGUARDA_PURGA")
    _register_purga(dmn)
    graph = _graph(dmn=dmn, inference=_FailingInference())
    state = _base_state(intencao="notificacao_previa")
    state.update(await graph.assess(state))  # type: ignore[typeddict-item]
    result = await graph.notify(state)
    assert result["mensagem"]["texto"]  # deterministic fallback text, non-empty
    assert result["mensagem"]["comunicacao_suspensao"] is None


async def test_dossier_llm_failure_never_blocks_escalate() -> None:
    graph = _graph(inference=_FailingInference())
    state = _base_state(intencao="rescisao", route="escalate", motivo_humano="indicio_rescisao")
    result = await graph.escalate(state)
    assert result["dossier"]["narrativa"] == ""
    assert result["dossier"]["decisao_inadimplencia"] is None


# ---------------------------------------------------------------------------
# start_process
# ---------------------------------------------------------------------------


async def test_start_process_starts_with_contract_variables() -> None:
    cibseven = FakeCibSevenTransport()
    graph = _graph(cibseven=cibseven)
    state = _base_state(intencao="rescisao", route="escalate", motivo_humano="indicio_rescisao")
    result = await graph.start_process(state)
    assert result["process_started"] is True
    assert result["business_key"] == "INAD-amh-CONTRATO-001"
    instance = await cibseven.find_active_instance("INAD-amh-CONTRATO-001")
    assert instance is not None


async def test_start_process_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-1",
            process_key="SP-OP-INADIMPLENCIA-001",
            business_key="INAD-amh-CONTRATO-001",
            state="ACTIVE",
            already_existed=True,
        )
    )
    graph = _graph(cibseven=cibseven)
    state = _base_state(intencao="rescisao", route="escalate", motivo_humano="indicio_rescisao")
    result = await graph.start_process(state)
    assert result["process_ref"]["already_existed"] is True
    assert result["process_ref"]["instance_id"] == "existing-1"


async def test_start_process_records_error_on_cibseven_failure() -> None:
    graph = _graph(cibseven=_FailingCibSeven())
    state = _base_state(intencao="rescisao", route="escalate", motivo_humano="indicio_rescisao")
    result = await graph.start_process(state)
    assert result["process_started"] is False
    assert "start_process indisponivel" in result["error"]
