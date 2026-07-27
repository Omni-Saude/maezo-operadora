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
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["texto sintetico"]
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
        # Deliberately low-entropy synthetic value: `business_key=` literals derived from this
        # ("INAD-amh-000000001") sit next to a 'key' substring, and the previous higher-entropy
        # form ("CONTRATO-001", Shannon entropy 3.69) tripped the repo-wide gitleaks
        # generic-api-key rule as a false positive. Repeated zeros keep the
        # INAD-{tenant}-{contrato} format semantics while staying under the entropy threshold —
        # do not "improve" this to realistic-looking data.
        "numero_contrato": "000000001",
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
    audit_sink: Any | None = None,
    whatsapp: Any | None = None,
) -> FernandoGraph:
    return FernandoGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=audit_sink or FakeStartAuditSink(),
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
            "audit_sink": FakeStartAuditSink(),
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
    assert result["business_key"] == "INAD-amh-000000001"


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
    # start_process node never reached — the key IS present (receive's output-field reset,
    # R1 cycle-1 fix) but explicitly False, and no engine instance exists.
    assert result["process_started"] is False
    assert await cibseven.find_active_instance("INAD-amh-000000001") is None


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
    assert result["business_key"] == "INAD-amh-000000001"
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


# ---------------------------------------------------------------------------
# R1 cycle-1 regression family: caller-planted OUTPUT-field read-through (helena-cycle-2 class).
# The verifier planted values in "Filled by assess" output fields on journeys where the graph
# deliberately never computes them (rescisao / DMN-unavailable / receive-fail) and they read
# through verbatim into dossier facts -> dossie_fernando -> engine variables. Fixed by
# `receive`'s write-side `_output_field_resets()` + `_build_dossier`/`_build_message`'s
# read-side `_STATUS_ALLOW` re-validation. Sentinels below are deliberately low-entropy
# (gitleaks hygiene, same rationale as the numero_contrato fixture comment).
# ---------------------------------------------------------------------------

# Unique low-entropy sentinel per output field — planted into caller state by the probes.
_PLANTED_OUTPUTS: dict[str, Any] = {
    "status_inadimplencia": "SUSPENDER CPF=123.456.789-00",  # the verifier's exact probe value
    "prazo_purga_iso": "leak-prazo-purga-000",
    "prazo_notificacao_previa_iso": "leak-prazo-notif-000",
    "periodo_minimo_iso": "leak-periodo-min-000",
    "fonte_regulatoria_purga": "leak-fonte-purga-000",
    "sla_analise_iso": "leak-sla-analise-000",
    "sla_alerta_iso": "leak-sla-alerta-000",
    "fonte_regulatoria_sla": "leak-fonte-sla-000",
    "dmn_refs": {"leak-table-000": "leak-ref-000"},
    "dmn_error": "leak-dmn-error-000",
    "route": "notify",  # a planted route trying to force the neutral path on a fail journey
    "motivo_humano": "leak-motivo-000",
    "motivo_categoria": "leak-categoria-000",
    "error": "leak-error-000",
    "business_key": "LEAK-BK-000000",
    "mensagem": {"texto": "leak-mensagem-000"},
    "mensagem_enviada": True,
    "dossier": {"narrativa": "leak-narrativa-000"},
    "process_started": True,
    "process_ref": {"instance_id": "leak-instance-000"},
    "desfecho": "leak-desfecho-000",
}

# Every string fragment that must never surface in dossier facts / engine-bound variables.
_PLANTED_FRAGMENTS: tuple[str, ...] = (
    "SUSPENDER",
    "123.456.789-00",
    "leak-",
    "LEAK-BK-000000",
)


def _assert_no_planted_fragment(payload: Any) -> None:
    text = str(payload)
    for fragment in _PLANTED_FRAGMENTS:
        assert fragment not in text, f"planted fragment {fragment!r} leaked into: {text[:400]}"


async def test_caller_planted_status_never_reaches_engine_variables_on_rescisao() -> None:
    """The R1 verifier's EXACT probe: `status_inadimplencia='SUSPENDER CPF=...'` planted in
    caller state on the `rescisao` journey (where `assess` deliberately never classifies)
    previously landed verbatim in dossier["fatos"].status_inadimplencia -> dossie_fernando ->
    engine variables. Now: receive's reset overwrites it, and _build_dossier's read-side
    allowlist would reject it even if it survived."""
    dmn = FakeDmnTransport()
    _register_sla(dmn)
    cibseven = FakeCibSevenTransport()
    graph_instance = _graph(dmn=dmn, cibseven=cibseven)
    compiled = graph_instance.compile_graph().compile()

    result = await compiled.ainvoke(
        _base_state(intencao="rescisao", status_inadimplencia=_PLANTED_OUTPUTS["status_inadimplencia"])
    )

    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "indicio_rescisao"
    assert result["process_started"] is True
    assert result["dossier"]["fatos"]["status_inadimplencia"] is None
    _assert_no_planted_fragment(result["dossier"])
    _assert_no_planted_fragment(graph_instance._inadimplencia_variables(result))


async def test_caller_planted_outputs_never_reach_engine_variables_on_dmn_unavailable() -> None:
    """DMN-unavailable journey (status DMN unregistered -> dmn_indisponivel escalation): the
    graph never computes `status_inadimplencia` here either — planted output values must be
    reset, never read through. The graph's own `dmn_error` (a bounded transport message) must
    replace the planted one."""
    dmn = FakeDmnTransport()  # NOTHING registered: status AND sla both unavailable
    cibseven = FakeCibSevenTransport()
    graph_instance = _graph(dmn=dmn, cibseven=cibseven)
    compiled = graph_instance.compile_graph().compile()

    result = await compiled.ainvoke(_base_state(intencao="notificacao_previa", **_PLANTED_OUTPUTS))

    assert result["route"] == "escalate"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert result["dossier"]["fatos"]["status_inadimplencia"] is None
    assert result["sla_analise_iso"] == ""  # graph-computed reset, not the planted sentinel
    assert "leak-dmn-error-000" not in result["dmn_error"]  # graph's own transport message
    _assert_no_planted_fragment(result["dossier"])
    _assert_no_planted_fragment(graph_instance._inadimplencia_variables(result))


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"intencao": "intencao-invalida-000"}, "invalid_intencao"),
        ({"tenant_id": ""}, "missing_tenant_id"),
        ({"numero_contrato": "", "matricula_beneficiario": ""}, "missing_contract_key"),
    ],
)
async def test_caller_planted_outputs_cleared_on_receive_fail_paths(
    overrides: dict[str, Any], expected_error: str
) -> None:
    """Every receive-fail journey resets ALL output fields (the verifier found
    `sla_analise_iso`/`fonte_regulatoria_sla` leaking on exactly these paths). Also proves the
    observability outputs can't be fabricated by the caller: `process_ref`/`desfecho` are the
    graph's own, never the planted ones."""
    dmn = FakeDmnTransport()
    _register_sla(dmn)
    cibseven = FakeCibSevenTransport()
    graph_instance = _graph(dmn=dmn, cibseven=cibseven)
    compiled = graph_instance.compile_graph().compile()

    result = await compiled.ainvoke(_base_state(**{**_PLANTED_OUTPUTS, **overrides}))

    assert result["route"] == "escalate"
    assert result["error"] == expected_error
    assert result["dossier"]["fatos"]["status_inadimplencia"] is None
    assert result["desfecho"] == "encaminhado_analise_humana"  # graph's own, not planted
    assert result["process_ref"]["instance_id"] != "leak-instance-000"
    _assert_no_planted_fragment(result["dossier"])
    _assert_no_planted_fragment(graph_instance._inadimplencia_variables(result))


async def test_caller_planted_business_key_never_reaches_engine() -> None:
    """A planted `business_key` on a receive-fail journey would previously have become the
    ENGINE business key (`start_process`'s `state.get("business_key") or ...` fallback). Now:
    receive resets it, start_process recomputes from the (input) contract identity."""
    dmn = FakeDmnTransport()
    _register_sla(dmn)
    cibseven = FakeCibSevenTransport()
    graph_instance = _graph(dmn=dmn, cibseven=cibseven)
    compiled = graph_instance.compile_graph().compile()

    result = await compiled.ainvoke(
        _base_state(intencao="intencao-invalida-000", business_key="LEAK-BK-000000")
    )

    assert result["business_key"] == "INAD-amh-000000001"  # recomputed, never the planted key
    assert await cibseven.find_active_instance("INAD-amh-000000001") is not None
    assert await cibseven.find_active_instance("LEAK-BK-000000") is None


async def test_receive_resets_every_output_field() -> None:
    """Write-side unit proof: `receive` (success path) returns a reset for EVERY planted output
    key, so downstream nodes can never observe a caller-planted output value."""
    graph = _graph()
    result = await graph.receive(_base_state(**_PLANTED_OUTPUTS))
    for key, planted in _PLANTED_OUTPUTS.items():
        if key == "business_key":
            assert result[key] == "INAD-amh-000000001"  # computed, not the planted sentinel
        else:
            assert result[key] != planted, f"receive did not reset output field {key!r}"


async def test_escalate_min_resets_every_output_field() -> None:
    """Write-side unit proof for the fail paths: `_escalate_min`'s return also resets every
    output key (then sets its own route/motivo/error class token on top)."""
    result = FernandoGraph._escalate_min("falha_tecnica", "missing_tenant_id")
    for key, planted in _PLANTED_OUTPUTS.items():
        assert key in result, f"_escalate_min return is missing output field {key!r}"
        assert result[key] != planted, f"_escalate_min does not reset output field {key!r}"


async def test_build_dossier_read_side_allowlist_rejects_unknown_status() -> None:
    """Read-side defense in depth (the verifier's minimal fix), proven in isolation by calling
    `_build_dossier` directly with a hostile status ALREADY in state (bypassing receive's
    write-side reset): out-of-allowlist -> None; a legit allowlisted value survives."""
    graph = _graph()
    hostile = _base_state(intencao="rescisao", status_inadimplencia="SUSPENDER CPF=123.456.789-00")
    dossier = await graph._build_dossier(hostile)
    assert dossier["fatos"]["status_inadimplencia"] is None

    legit = _base_state(intencao="analise_inadimplencia", status_inadimplencia="SEGUE_ANALISE")
    dossier = await graph._build_dossier(legit)
    assert dossier["fatos"]["status_inadimplencia"] == "SEGUE_ANALISE"


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
    assert result["business_key"] == "INAD-amh-000000001"
    instance = await cibseven.find_active_instance("INAD-amh-000000001")
    assert instance is not None


async def test_start_process_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-1",
            process_key="SP-OP-INADIMPLENCIA-001",
            business_key="INAD-amh-000000001",
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
