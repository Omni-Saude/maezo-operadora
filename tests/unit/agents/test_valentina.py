"""Unit tests for Valentina's REAL graph (T1.12 — SP-OP-PROGRAMA-001, CONSENT-GATED).

Every node is exercised against fakes: `FakeDmnTransport` (never the local XML evaluator,
ADR-0028), `FakeCibSevenTransport` (never a fabricated process instance), and a small in-file
fake inference provider (no LLM SDK, no network). Live-engine acceptance status is reported in
the PR body (ADR-0011: real engine, never mocked there; never fabricated here).

The suite proves the three contract invariants + the caller-planted-output sanitization class:
- (A) CONSENT CHOKEPOINT: no consent / revoked / ambiguous / unverifiable -> neutral terminal,
  ZERO PHI gather, ZERO DMN, ZERO process, ZERO LLM calls (asserting transports raise on touch).
- (B) revocation is a fail-safe STOP with precedence over an otherwise-active consent.
- (C) the adverse clinical decision is ALWAYS human: `Route` has no adverse variant, the DMN
  allowlist is closed (an adverse-looking DMN output routes human), and the engine-bound
  decision fields are always None.
- SANITIZATION (the fernando/carolina/marina R1 class, highest-stakes instance): the consent
  VERDICT is an output field — a caller-planted `consent_status="ativo"` with no real consent
  is wiped at `receive` and recomputed by `consent_gate`; the chokepoint holds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml

from maezo.agents.valentina.graph import (
    _CALLER_INPUT_FIELDS,
    Route,
    ValentinaGraph,
    ValentinaState,
    _business_key,
    _output_field_resets,
    build,
)
from maezo.runtime.harness import Harness
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    FakeCibSevenTransport,
    ProcessInstance,
)
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"

# Adversarial marker planted into output-only fields by the probes. Low-entropy on purpose
# (gitleaks hygiene — synthetic test value, not a secret).
_SENTINEL = "planted-sentinel"


# --- Fakes -------------------------------------------------------------------------------------


class _FakeInference:
    """Deterministic, in-order fake — never a real LLM SDK call. Records the `phi` flag."""

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


class _RaisingInference:
    """LLM that always fails — for the consented-path fail-safe probe. Also proves the
    no-consent paths never call the LLM (any call raises AND is recorded)."""

    def __init__(self) -> None:
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
        raise RuntimeError("LLM indisponivel")


class _FakePatientSummaryReader:
    def __init__(self, *, fail: bool = False, facts: dict[str, Any] | None = None) -> None:
        self._fail = fail
        self._facts = facts
        self.calls: list[str] = []

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        self.calls.append(patient_id)
        if self._fail:
            raise RuntimeError("HAPI FHIR unreachable")
        return self._facts or {"resourceType": "Patient", "id": patient_id}


class _AssertingPatientSummaryReader:
    """Raises on ANY read — for paths that must NEVER touch PHI (the consent chokepoint)."""

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        raise AssertionError("PHI gather must NEVER run without an active consent verdict")


class _RecordingCibSeven(FakeCibSevenTransport):
    """Records every engine-bound variable set (the leak surface the probes assert on)."""

    def __init__(self) -> None:
        super().__init__()
        self.started_variables: list[dict[str, Any]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.started_variables.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)


class _AssertingCibSeven(FakeCibSevenTransport):
    """Raises on ANY engine touch — for paths that must never reach the transport at all."""

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        raise AssertionError("this path must never touch the engine transport")

    async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
        raise AssertionError("this path must never start a process")


class _FailingCibSeven(FakeCibSevenTransport):
    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        raise CibSevenError("engine unreachable")


# --- Fixtures ----------------------------------------------------------------------------------


def _graph(
    *,
    inference: Any | None = None,
    dmn: FakeDmnTransport | None = None,
    cibseven: FakeCibSevenTransport | None = None,
    audit_sink: Any | None = None,
    fhir: Any | None = None,
) -> ValentinaGraph:
    return ValentinaGraph(
        inference=inference or _FakeInference(),
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=audit_sink or FakeStartAuditSink(),
        fhir=fhir,
    )


def _consented_state(**overrides: Any) -> ValentinaState:
    """A fully-consented, worker-pre-resolved care-program case (the legitimate input shape)."""
    state: ValentinaState = {
        "task": "stratify",
        "tenant_id": "amh",
        "canal": "a2a",
        "programa_id": "p1",
        "beneficiario_pseudo_id": "b1",
        "ciclo": "c1",
        "gatilho": "estratificacao_populacional",
        "consent_scope": "programa_cuidado",
        "consentimento_ativo": True,
        "consent_checked": True,
        "consent_revoked": False,
        "consent_event_ref": "consent-evt-1",
        "risco_estratificado": "moderado",
        "elegibilidade_criterios_atendidos": True,
        "criterio_alta_aparente": False,
        "patient_summary_ref": "fhir-b1",
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# Low-entropy synthetic fixture (gitleaks hygiene) — a fabricated test business key,
# not a credential.
_EXPECTED_BUSINESS_KEY = "PROG-amh-p1-b1-c1"


def _register_routing(
    dmn: FakeDmnTransport, *, elegivel: str = "ELEGIVEL", motivo: str = "criterios atendidos"
) -> None:
    dmn.register("programa_routing", [{"elegivel_programa": elegivel, "motivo": motivo}])


def _register_sla(dmn: FakeDmnTransport) -> None:
    dmn.register(
        "programa_sla",
        [{"sla_decisao": "P7D", "sla_alerta": "P5D", "fonte": "politica clinica interna (DRAFT)"}],
    )


def _planted_outputs() -> dict[str, Any]:
    """A sentinel in EVERY output-only field — the adversarial caller's full plant. Drift-guarded
    against `_output_field_resets` by `test_receive_resets_every_output_only_field`."""
    return {
        "consent_status": "ativo",  # THE critical plant — a forged consent VERDICT
        "gathered": True,
        "summary_facts": {"planted": _SENTINEL},
        "gather_notes": [_SENTINEL],
        "elegivel_programa": "ELEGIVEL",  # forged neutral outcome
        "motivo_estratificacao": _SENTINEL,
        "sla_decisao": _SENTINEL,
        "sla_alerta": _SENTINEL,
        "dmn_refs": {"programa_routing": f"forged#{_SENTINEL}"},
        "dmn_error": _SENTINEL,
        "dossier": {"narrativa": _SENTINEL},
        "route": "auto_route",  # the adversarial routing plant — must NEVER survive
        "motivo_humano": "outro",
        "grupo_humano": _SENTINEL,
        "process_started": True,
        # CC-01: campo novo (marcador de falha de start). Ele e OUTPUT-ONLY como todos os
        # demais, entao entra no plantio adversarial junto com os outros — plantar True e
        # exatamente o que um chamador faria para forjar "o start ja falhou".
        "start_failed": True,
        "business_key": f"PROG-{_SENTINEL}",
        "process_ref": {"instance_id": _SENTINEL},
        "desfecho": "enrollment_realizado",  # forged terminal
        "error": _SENTINEL,
    }


# --- agent.yaml / build contract ---------------------------------------------------------------


def test_valentina_agent_yaml_exists() -> None:
    assert (_AGENTS_ROOT / "valentina" / "agent.yaml").exists()


def test_valentina_agent_yaml_has_required_fields() -> None:
    with open(_AGENTS_ROOT / "valentina" / "agent.yaml") as f:
        data = yaml.safe_load(f)
    assert data["id"] == "valentina"
    assert "name" in data
    assert "role" in data
    assert "tools" in data
    assert data["security_zone"] == "phi"
    assert data["escalation"]["process"] == "SP-OP-PROGRAMA-001"


def test_build_fail_closed_on_missing_dependencies() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({"inference": _FakeInference()})
    with pytest.raises(ValueError, match="missing required dependencies"):
        build(None)


def test_build_compiles_with_expected_nodes() -> None:
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
    assert node_names == {
        "receive",
        "consent_gate",
        "no_consent",
        "stopped",
        "gather",
        "assess",
        "auto_route",
        "human_review",
        "start_process",
        "notify_start_failure",
        "finalize",
    }


def test_harness_create_graph_valentina_resolves_real_build() -> None:
    """`create_graph('valentina')` resolves the REAL `build(config)` via the harness's generic
    tool-deps wiring — exactly as helena/rafael are wired (T1.11 conventions)."""
    harness = Harness(
        inference=_FakeInference(),  # type: ignore[arg-type]
        tool_deps={
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
        },
    )
    graph = harness.create_graph("valentina")
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert "consent_gate" in node_names  # the real consent-gated graph, not the stub


def test_receive_is_the_single_graph_entry_node() -> None:
    """The receive-side sanitization closes the planted-output class ONLY if receive always runs
    first: exactly one edge out of START, into `receive` — no entry can skip it."""
    compiled = _graph().compile_graph().compile()
    start_targets = [e.target for e in compiled.get_graph().edges if e.source == "__start__"]
    assert start_targets == ["receive"]


def test_consent_terminals_wire_straight_to_end() -> None:
    """`no_consent`/`stopped` are TERMINAL: their only outgoing edge is END — `gather`/`assess`/
    `start_process` are structurally unreachable without an active consent verdict."""
    compiled = _graph().compile_graph().compile()
    edges = compiled.get_graph().edges
    assert {e.target for e in edges if e.source == "no_consent"} == {"__end__"}
    assert {e.target for e in edges if e.source == "stopped"} == {"__end__"}
    # And the ONLY edge into `gather` comes from the consent gate.
    assert {e.source for e in edges if e.target == "gather"} == {"consent_gate"}


def test_route_type_has_no_adverse_variant() -> None:
    """Invariant C, structural: the Route type admits ONLY the neutral and the human variant —
    no discharge/deny/disenroll variant exists to be routed to."""
    assert set(get_args(Route)) == {"auto_route", "human_review"}


# --- (A) Consent chokepoint --------------------------------------------------------------------


async def test_consented_happy_path_e2e_elegivel() -> None:
    """Consented E2E: gather -> assess (programa_routing=ELEGIVEL + programa_sla) -> auto_route
    -> idempotent start of SP-OP-PROGRAMA-001 -> finalize."""
    dmn = FakeDmnTransport()
    _register_routing(dmn, elegivel="ELEGIVEL")
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    fhir = _FakePatientSummaryReader()
    inference = _FakeInference()
    compiled = _graph(inference=inference, dmn=dmn, cibseven=cibseven, fhir=fhir).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert result["consent_status"] == "ativo"
    assert [t for t, _ in dmn.calls] == ["programa_routing", "programa_sla"]
    assert fhir.calls == ["fhir-b1"]
    assert result["route"] == "auto_route"
    assert result["desfecho"] == "enrollment_realizado"
    assert result["process_started"] is True
    assert result["business_key"] == _EXPECTED_BUSINESS_KEY

    assert cibseven.started_variables, "the consented path starts the real process"
    variables = cibseven.started_variables[0]
    assert variables["consent_status"] == "ativo"
    assert variables["valentina_route"] == "auto_route"
    assert variables["elegivel_programa"] == "ELEGIVEL"
    assert variables["dmn_decision_refs"]["programa_routing"].startswith("programa_routing#")
    assert variables["dossie_valentina"]["decisao_clinica"] is None
    assert variables["dossie_valentina"]["decisao_desligamento"] is None
    # The adverse clinical decision is ALWAYS the human's — never pre-filled by Valentina.
    assert variables["decisao_programa"] is None
    assert variables["motivo_desligamento_clinico"] is None
    assert variables["referencia_clinica"] is None
    assert variables["responsavel_clinico_id"] is None


async def test_consented_nao_elegivel_is_neutral_non_inclusion() -> None:
    """NAO_ELEGIVEL is informative non-inclusion (NOT a coverage denial): still the neutral
    auto_route, still starts the process (whose human User Task confirms any decision)."""
    dmn = FakeDmnTransport()
    _register_routing(dmn, elegivel="NAO_ELEGIVEL", motivo="criterios nao atendidos")
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state(risco_estratificado="baixo"))

    assert result["route"] == "auto_route"
    assert result["desfecho"] == "nao_elegivel"
    assert result["process_started"] is True
    assert cibseven.started_variables[0]["elegivel_programa"] == "NAO_ELEGIVEL"


async def test_no_consent_terminates_neutral_with_zero_phi_zero_dmn_zero_process() -> None:
    """Invariant A: without active consent the turn ends at the NEUTRAL `no_consent` terminal —
    zero FHIR reads, zero DMN evaluations, zero engine touches, zero LLM calls."""
    dmn = FakeDmnTransport()
    inference = _RaisingInference()
    compiled = (
        _graph(
            inference=inference,
            dmn=dmn,
            cibseven=_AssertingCibSeven(),
            fhir=_AssertingPatientSummaryReader(),
        )
        .compile_graph()
        .compile()
    )

    result = await compiled.ainvoke(_consented_state(consentimento_ativo=False))

    assert result["consent_status"] == "ausente"
    assert result["desfecho"] == "sem_consentimento"
    assert result["process_started"] is False
    assert dmn.calls == []
    assert inference.calls == []
    assert result["dossier"] == {}  # no dossier is assembled without consent


async def test_revocation_stops_processing_fail_safe() -> None:
    """Invariant B: a signalled revocation STOPS processing (neutral `stopped` terminal) — with
    precedence over an otherwise fully-active consent. Zero PHI/DMN/process/LLM."""
    dmn = FakeDmnTransport()
    inference = _RaisingInference()
    compiled = (
        _graph(
            inference=inference,
            dmn=dmn,
            cibseven=_AssertingCibSeven(),
            fhir=_AssertingPatientSummaryReader(),
        )
        .compile_graph()
        .compile()
    )

    result = await compiled.ainvoke(
        _consented_state(consent_revoked=True)  # consentimento_ativo/consent_checked still True
    )

    assert result["consent_status"] == "revogado"
    assert result["desfecho"] == "interrompido_revogacao"
    assert result["process_started"] is False
    assert dmn.calls == []
    assert inference.calls == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"consentimento_ativo": False}, "consent not active"),
        ({"consent_checked": False}, "consent not verified"),
        ({"consentimento_ativo": "true"}, "string 'true' is AMBIGUOUS, not the exact boolean"),
        ({"consentimento_ativo": 1}, "int 1 is AMBIGUOUS, not the exact boolean"),
        ({"consent_checked": "sim"}, "string consent_checked is AMBIGUOUS"),
        ({"consent_scope": "marketing"}, "foreign consent scope never authorizes programa PHI"),
    ],
)
async def test_ambiguous_or_absent_consent_fail_closes(overrides: dict[str, Any], reason: str) -> None:
    """Fail-closed everywhere: absent, false, type-ambiguous, or foreign-scope consent facts are
    ALL 'no consent' — neutral terminal, zero PHI/DMN/process."""
    dmn = FakeDmnTransport()
    compiled = (
        _graph(dmn=dmn, cibseven=_AssertingCibSeven(), fhir=_AssertingPatientSummaryReader())
        .compile_graph()
        .compile()
    )

    result = await compiled.ainvoke(_consented_state(**overrides))

    assert result["consent_status"] == "ausente", reason
    assert result["desfecho"] == "sem_consentimento"
    assert result["process_started"] is False
    assert dmn.calls == []


async def test_missing_consent_facts_entirely_fail_close() -> None:
    """A state that simply omits every consent fact (no flags at all) is NO consent."""
    state = _consented_state()
    for key in ("consent_scope", "consentimento_ativo", "consent_checked", "consent_revoked"):
        del state[key]  # type: ignore[misc]
    compiled = (
        _graph(cibseven=_AssertingCibSeven(), fhir=_AssertingPatientSummaryReader()).compile_graph().compile()
    )

    result = await compiled.ainvoke(state)

    assert result["consent_status"] == "ausente"
    assert result["desfecho"] == "sem_consentimento"
    assert result["process_started"] is False


async def test_ambiguous_revocation_signal_still_stops() -> None:
    """The revocation asymmetry: ANY truthy revocation signal (even a sloppy string) stops
    processing — the broadest reading is the fail-safe one."""
    compiled = (
        _graph(cibseven=_AssertingCibSeven(), fhir=_AssertingPatientSummaryReader()).compile_graph().compile()
    )
    result = await compiled.ainvoke(_consented_state(consent_revoked="true"))
    assert result["consent_status"] == "revogado"
    assert result["desfecho"] == "interrompido_revogacao"


async def test_missing_runtime_context_fail_closes_through_no_consent() -> None:
    """`receive`'s guard: without the contract identifiers there is no business key AND no way
    to verify consent for a concrete titular — inability to decide consent = NO consent
    (neutral terminal, zero PHI/DMN/process; never `{}`, never adverse)."""
    dmn = FakeDmnTransport()
    compiled = (
        _graph(dmn=dmn, cibseven=_AssertingCibSeven(), fhir=_AssertingPatientSummaryReader())
        .compile_graph()
        .compile()
    )

    result = await compiled.ainvoke(_consented_state(tenant_id=""))

    assert result["consent_status"] == "ausente"
    assert result["desfecho"] == "sem_consentimento"
    assert result["error"] == "contexto_de_runtime_ausente"  # bounded class token, internal-only
    assert result["process_started"] is False
    assert result["business_key"] == ""
    assert dmn.calls == []


# --- (C) Adverse decision is ALWAYS human ------------------------------------------------------


async def test_criterio_alta_aparente_always_routes_human_never_discharges() -> None:
    """An APPARENT discharge criterion NEVER discharges: it routes to the coordenacao clinica
    (precedence over automatic stratification — programa_routing is not even consulted)."""
    dmn = FakeDmnTransport()
    _register_routing(dmn)  # registered but must NOT be called
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state(criterio_alta_aparente=True))

    assert [t for t, _ in dmn.calls] == ["programa_sla"]  # SLA only; no auto-stratification
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "criterio_alta_aparente"
    assert result["grupo_humano"] == "coordenacao-clinica"
    assert result["desfecho"] == "analise_humana_clinica"
    variables = cibseven.started_variables[0]
    assert variables["valentina_route"] == "human_review"
    assert variables["motivo_encaminhamento"] == "criterio_alta_aparente"
    assert variables["decisao_programa"] is None  # the discharge is NOT taken — only instructed


async def test_dmn_analise_humana_routes_human() -> None:
    dmn = FakeDmnTransport()
    _register_routing(dmn, elegivel="ANALISE_HUMANA", motivo="risco alto")
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state(risco_estratificado="alto"))

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "estratificacao_analise_humana"
    assert result["desfecho"] == "analise_humana_clinica"
    assert cibseven.started_variables[0]["motivo_encaminhamento"] == "estratificacao_analise_humana"


@pytest.mark.parametrize("adverse_looking", ["DESLIGAR", "ALTA", "NEGAR_CUIDADO", "", "whatever"])
async def test_unknown_dmn_output_never_auto_routes(adverse_looking: str) -> None:
    """CLOSED allowlist: even a hypothetical adverse-looking (or empty/unknown) DMN output can
    never take the automatic path — everything not in {ELEGIVEL, NAO_ELEGIVEL} goes human."""
    dmn = FakeDmnTransport()
    _register_routing(dmn, elegivel=adverse_looking)
    _register_sla(dmn)
    compiled = _graph(dmn=dmn).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert result["route"] == "human_review"
    assert result["desfecho"] == "analise_humana_clinica"


async def test_dmn_unavailable_routes_human_and_error_text_never_reaches_engine() -> None:
    """DMN down -> human (never an adverse/automatic outcome by omission), and the raw transport
    error text stays OUT of engine-bound variables — only the bounded class token travels."""
    dmn = FakeDmnTransport()  # programa_routing NOT registered -> DmnEvaluationError
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "dmn_indisponivel"
    assert result["desfecho"] == "analise_humana_clinica"
    assert "not registered" in result["dmn_error"]  # raw text stays in internal state...
    variables = cibseven.started_variables[0]
    serialized = json.dumps(variables, ensure_ascii=False, default=str)
    assert "not registered" not in serialized  # ...never in engine variables
    assert "dmn_error" not in variables
    assert "error" not in variables
    assert variables["motivo_encaminhamento"] == "dmn_indisponivel"


async def test_llm_failure_on_auto_route_downgrades_to_human() -> None:
    """Charter hardening: a technical (LLM) failure on the consented AUTOMATIC path downgrades
    to human_review (falha_tecnica) — it never ships the automatic route without its dossier."""
    dmn = FakeDmnTransport()
    _register_routing(dmn, elegivel="ELEGIVEL")
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    inference = _RaisingInference()
    compiled = _graph(inference=inference, dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert inference.calls, "the dossier LLM call was attempted"
    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "falha_tecnica"
    assert result["desfecho"] == "analise_humana_clinica"
    variables = cibseven.started_variables[0]
    assert variables["valentina_route"] == "human_review"
    assert variables["motivo_encaminhamento"] == "falha_tecnica"
    assert variables["dossie_valentina"]["narrativa"] == ""  # deterministic minimal dossier


async def test_llm_failure_on_human_path_keeps_fail_safe_route() -> None:
    """On the already-human path an LLM failure only degrades the narrative — the route is
    already the fail-safe one and the case still reaches the human with the factual dossier."""
    dmn = FakeDmnTransport()
    _register_routing(dmn, elegivel="ANALISE_HUMANA")
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(inference=_RaisingInference(), dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "estratificacao_analise_humana"  # NOT overwritten
    assert result["dossier"]["narrativa"] == ""
    assert cibseven.started_variables[0]["motivo_encaminhamento"] == "estratificacao_analise_humana"


async def test_no_adverse_decision_key_anywhere_in_final_state() -> None:
    """Deep scan of a full consented turn: no output ever carries a non-None adverse clinical
    decision (decisao_programa / decisao_clinica / decisao_desligamento / registro de alta)."""
    dmn = FakeDmnTransport()
    _register_routing(dmn, elegivel="ELEGIVEL")
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state(task="enroll"))

    adverse_keys = {
        "decisao_programa",
        "decisao_clinica",
        "decisao_desligamento",
        "motivo_desligamento_clinico",
        "responsavel_clinico_id",
    }

    def _assert_adverse_always_none(data: Any, path: str = "root") -> None:
        if isinstance(data, dict):
            for key, value in data.items():
                if key in adverse_keys:
                    assert value is None, f"adverse field {key!r} set at {path}: {value!r}"
                _assert_adverse_always_none(value, f"{path}.{key}")
        elif isinstance(data, list):
            for i, item in enumerate(data):
                _assert_adverse_always_none(item, f"{path}[{i}]")

    _assert_adverse_always_none(dict(result))
    _assert_adverse_always_none(cibseven.started_variables[0])


# --- Sanitization: the caller-planted-output class ---------------------------------------------


async def test_receive_resets_every_output_only_field() -> None:
    """Structural core: `receive` overwrites every output-only field with its benign reset in
    its OWN state update — nothing caller-planted survives the first node."""
    graph = _graph()
    planted = _planted_outputs()
    resets = _output_field_resets()
    # Drift guard: the probe fixture and the production reset list cover the SAME key set.
    assert set(planted) == set(resets), "planted fixture must mirror _output_field_resets keys"

    result = await graph.receive(_consented_state(**planted))

    for key in planted:
        assert key in result, f"receive did not overwrite planted output field {key!r}"
    for key, reset_value in resets.items():
        if key == "business_key":
            continue  # re-derived from input identifiers below, not a static reset
        assert result[key] == reset_value, f"{key!r} not reset: {result[key]!r}"
    assert result["business_key"] == _EXPECTED_BUSINESS_KEY
    assert result["consent_status"] is None  # the forged verdict is GONE before consent_gate
    assert result["route"] == "human_review"  # fail-safe reset
    assert _SENTINEL not in json.dumps(result, ensure_ascii=False, default=str)


def test_output_field_partition_is_complete() -> None:
    """Completeness guard: every `ValentinaState` field is classified as exactly one of
    caller-input or output-only. Adding a state field without classifying it (and, if
    output-only, without a sanitized default) fails here — the class stays closed as the state
    evolves. The consent VERDICT must be on the OUTPUT side of the partition."""
    annotations = set(ValentinaState.__annotations__)
    outputs = set(_output_field_resets())
    assert _CALLER_INPUT_FIELDS & outputs == set()  # no field in both sets
    assert _CALLER_INPUT_FIELDS | outputs == annotations  # no field in neither set
    assert "consent_status" in outputs  # the verdict is NEVER a caller input
    assert "consent_status" not in _CALLER_INPUT_FIELDS


async def test_planted_consent_verdict_cannot_bypass_the_chokepoint() -> None:
    """THE critical probe (LGPD PHI-gate bypass attempt): a caller with NO real consent plants
    `consent_status="ativo"` + auto_route + a forged 'enrollment_realizado' desfecho + a
    pre-cooked dossier/process_ref. The chokepoint MUST hold: the plant is wiped at `receive`,
    `consent_gate` recomputes the verdict from the FACTS, and the turn terminates NEUTRAL with
    zero PHI gather, zero DMN, zero process, zero LLM calls — no sentinel anywhere."""
    dmn = FakeDmnTransport()
    _register_routing(dmn)  # registered — a bypass would surface as a call; must stay unused
    _register_sla(dmn)
    inference = _RaisingInference()
    compiled = (
        _graph(
            inference=inference,
            dmn=dmn,
            cibseven=_AssertingCibSeven(),
            fhir=_AssertingPatientSummaryReader(),
        )
        .compile_graph()
        .compile()
    )

    result = await compiled.ainvoke(
        _consented_state(
            consentimento_ativo=False,  # NO real consent
            consent_checked=False,
            **_planted_outputs(),  # forged verdict + route + desfecho + dossier + process_ref
        )
    )

    assert result["consent_status"] == "ausente"  # recomputed from FACTS — the plant is gone
    assert result["desfecho"] == "sem_consentimento"  # neutral terminal, not the forged one
    assert result["process_started"] is False
    assert result["business_key"] == _EXPECTED_BUSINESS_KEY  # re-derived, not the planted key
    assert dmn.calls == []  # zero DMN
    assert inference.calls == []  # zero LLM
    assert result["dossier"] == {}  # the pre-cooked dossier is gone
    assert result["process_ref"] == {}
    assert _SENTINEL not in json.dumps(result, ensure_ascii=False, default=str)


async def test_planted_consent_verdict_with_revoked_facts_still_stops() -> None:
    """Variant of the critical probe: real facts say REVOKED, the plant says active — the
    revocation wins (fail-safe stop), zero PHI/DMN/process."""
    compiled = (
        _graph(cibseven=_AssertingCibSeven(), fhir=_AssertingPatientSummaryReader()).compile_graph().compile()
    )

    result = await compiled.ainvoke(_consented_state(consent_revoked=True, **_planted_outputs()))

    assert result["consent_status"] == "revogado"
    assert result["desfecho"] == "interrompido_revogacao"
    assert result["process_started"] is False
    assert _SENTINEL not in json.dumps(result, ensure_ascii=False, default=str)


async def test_planted_error_and_route_cannot_bypass_dmn_on_consented_path() -> None:
    """The fernando/carolina/marina probe shape: on a legitimately-consented case, planting
    `error` + `route="auto_route"` + forged DMN outcome/refs must NEVER skip assess — the real
    DMN chain runs, the DMN-decided human route wins, and no forged value reaches the engine."""
    dmn = FakeDmnTransport()
    _register_routing(dmn, elegivel="ANALISE_HUMANA", motivo="risco alto")
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(
        _consented_state(
            error=_SENTINEL,
            route="auto_route",
            elegivel_programa="ELEGIVEL",  # forged neutral outcome
            desfecho="enrollment_realizado",  # forged terminal
            dmn_refs={"programa_routing": f"forged#{_SENTINEL}"},
            dossier={"narrativa": _SENTINEL},
        )
    )

    assert [t for t, _ in dmn.calls] == ["programa_routing", "programa_sla"]  # assess DID run
    assert result["route"] == "human_review"  # DMN-decided; the forged auto_route is gone
    assert result["elegivel_programa"] == "ANALISE_HUMANA"
    assert cibseven.started_variables, "the human-review route still starts the process"
    variables = cibseven.started_variables[0]
    serialized = json.dumps(variables, ensure_ascii=False, default=str)
    assert _SENTINEL not in serialized
    assert variables["valentina_route"] == "human_review"
    assert variables["dmn_decision_refs"]["programa_routing"].startswith("programa_routing#")


async def test_human_shortcut_never_carries_planted_facts_into_dossier() -> None:
    """Probe B shape (criterio-alta shortcut, where programa_routing is never consulted):
    planted stratification facts/refs must not survive into the dossier `fatos` or the engine's
    `dmn_decision_refs` beside the genuine motivo_humano."""
    dmn = FakeDmnTransport()
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(
        _consented_state(
            criterio_alta_aparente=True,
            elegivel_programa="ELEGIVEL",  # forged — the routing DMN never ran
            motivo_estratificacao=_SENTINEL,
            dmn_refs={"programa_routing": f"forged#{_SENTINEL}"},
        )
    )

    assert result["route"] == "human_review"
    assert result["motivo_humano"] == "criterio_alta_aparente"
    fatos = result["dossier"]["fatos"]
    assert fatos["elegivel_programa"] is None  # the forged outcome is gone
    assert fatos["motivo_estratificacao"] == ""
    assert _SENTINEL not in json.dumps(result["dossier"], ensure_ascii=False, default=str)
    variables = cibseven.started_variables[0]
    assert set(variables["dmn_decision_refs"]) == {"programa_sla"}  # only the genuinely-run table
    assert _SENTINEL not in json.dumps(variables, ensure_ascii=False, default=str)


async def test_missing_context_with_full_plant_never_touches_engine() -> None:
    """Missing runtime context + a fully planted output set: neutral no-consent terminal, no
    engine transport touch, no DMN, planted business_key wiped, no sentinel anywhere."""
    dmn = FakeDmnTransport()
    compiled = (
        _graph(dmn=dmn, cibseven=_AssertingCibSeven(), fhir=_AssertingPatientSummaryReader())
        .compile_graph()
        .compile()
    )

    result = await compiled.ainvoke(_consented_state(tenant_id="", **_planted_outputs()))

    assert result["consent_status"] == "ausente"
    assert result["desfecho"] == "sem_consentimento"
    assert result["process_started"] is False
    assert result["business_key"] == ""  # planted key reset; nothing re-derived without context
    assert dmn.calls == []
    assert _SENTINEL not in json.dumps(result, ensure_ascii=False, default=str)


# --- PHI discipline ----------------------------------------------------------------------------


async def test_every_llm_call_is_phi_tagged() -> None:
    """PHI discipline (ADR-0006/0017): every LLM call passes phi=True, on both routes."""
    for elegivel in ("ELEGIVEL", "ANALISE_HUMANA"):
        dmn = FakeDmnTransport()
        _register_routing(dmn, elegivel=elegivel)
        _register_sla(dmn)
        inference = _FakeInference()
        compiled = _graph(inference=inference, dmn=dmn).compile_graph().compile()
        await compiled.ainvoke(_consented_state())
        assert inference.calls, "the dossier LLM call must have happened"
        assert all(phi is True for _, phi in inference.calls)


async def test_raw_fhir_summary_never_reaches_dossier_or_engine_variables() -> None:
    """`gather`'s raw FHIR content stays in graph state: it never reaches the dossier `fatos`
    nor engine-bound variables (only gap notes travel)."""
    marker = "phi-marker-raw-fhir-content"
    dmn = FakeDmnTransport()
    _register_routing(dmn)
    _register_sla(dmn)
    cibseven = _RecordingCibSeven()
    fhir = _FakePatientSummaryReader(facts={"resourceType": "Patient", "name": marker})
    compiled = _graph(dmn=dmn, cibseven=cibseven, fhir=fhir).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert result["summary_facts"]["name"] == marker  # gathered in state (in-zone)
    assert marker not in json.dumps(result["dossier"], ensure_ascii=False, default=str)
    assert marker not in json.dumps(cibseven.started_variables[0], ensure_ascii=False, default=str)


async def test_fhir_failure_degrades_to_gap_note_never_blocks() -> None:
    dmn = FakeDmnTransport()
    _register_routing(dmn)
    _register_sla(dmn)
    compiled = _graph(dmn=dmn, fhir=_FakePatientSummaryReader(fail=True)).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert result["route"] == "auto_route"  # FHIR failure never blocks routing
    assert any("FHIR" in note for note in result["gather_notes"])
    assert result["dossier"]["fatos"]["lacunas_enriquecimento"] == result["gather_notes"]


# --- start_process -----------------------------------------------------------------------------


async def test_start_process_is_idempotent_by_business_key() -> None:
    dmn = FakeDmnTransport()
    _register_routing(dmn)
    _register_sla(dmn)
    cibseven = FakeCibSevenTransport()
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="pre-existing",
            process_key="SP-OP-PROGRAMA-001",
            business_key=_EXPECTED_BUSINESS_KEY,
            state="ACTIVE",
            already_existed=True,
        )
    )
    compiled = _graph(dmn=dmn, cibseven=cibseven).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert result["process_started"] is True
    assert result["process_ref"]["instance_id"] == "pre-existing"
    assert result["process_ref"]["already_existed"] is True


async def test_start_process_failure_records_error_and_keeps_route() -> None:
    dmn = FakeDmnTransport()
    _register_routing(dmn)
    _register_sla(dmn)
    compiled = _graph(dmn=dmn, cibseven=_FailingCibSeven()).compile_graph().compile()

    result = await compiled.ainvoke(_consented_state())

    assert result["process_started"] is False
    assert "start_process indisponivel" in result["error"]
    assert result["route"] == "auto_route"  # the case/route is never lost
    assert result["business_key"] == _EXPECTED_BUSINESS_KEY


# --- task variants / prompts -------------------------------------------------------------------


async def test_task_enroll_uses_care_plan_prompt_version() -> None:
    dmn = FakeDmnTransport()
    _register_routing(dmn)
    _register_sla(dmn)
    compiled = _graph(dmn=dmn).compile_graph().compile()
    result = await compiled.ainvoke(_consented_state(task="enroll"))
    assert result["dossier"]["prompt_version"] == "care-plan-v1"
    assert result["dossier"]["task"] == "enroll"


async def test_default_task_stratify_uses_stratification_prompt_version() -> None:
    dmn = FakeDmnTransport()
    _register_routing(dmn)
    _register_sla(dmn)
    compiled = _graph(dmn=dmn).compile_graph().compile()
    state = _consented_state()
    del state["task"]  # type: ignore[misc]
    result = await compiled.ainvoke(state)
    assert result["dossier"]["prompt_version"] == "stratification-v1"
    assert result["dossier"]["task"] == "stratify"


def test_business_key_contract_format() -> None:
    assert _business_key(_consented_state()) == _EXPECTED_BUSINESS_KEY
