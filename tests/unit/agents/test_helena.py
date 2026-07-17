"""Unit tests for Helena's REAL graph (T1.11, defect B6 — AGJ-HELENA-TRIAGE).

Every node is exercised against fakes: `FakeDmnTransport` (never the local XML evaluator, ADR-
0028), `FakeCibSevenTransport` (never a fabricated process instance), and a small in-file fake
inference provider (no LLM SDK, no network). Live-engine acceptance lives in
`tests/integration/agents/test_helena_escalation.py` (ADR-0011: real engine, never mocked there).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.helena.graph import HelenaGraph, HelenaState, _business_key, _to_hash_from_state, build
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    """Deterministic, in-order fake — never a real LLM SDK call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, bool]] = []

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        self.calls.append((prompt, phi))
        return self._responses.pop(0) if self._responses else ""


class _FakeWhatsAppSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self._fail = fail

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("transport down")
        self.sent.append((to_hash, text))
        return {"ok": True}


def _classify_json(**overrides: Any) -> str:
    base = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(overrides)
    return json.dumps(base)


def _base_state(**overrides: Any) -> HelenaState:
    state: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-123",
        "message_body": "ola",
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _graph(
    *,
    inference: Any,
    dmn: FakeDmnTransport | None = None,
    cibseven: FakeCibSevenTransport | None = None,
    whatsapp: Any | None = None,
) -> HelenaGraph:
    return HelenaGraph(
        inference=inference,
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        whatsapp=whatsapp or _FakeWhatsAppSender(),
    )


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity (unchanged contract surface)
# ---------------------------------------------------------------------------


def test_helena_agent_yaml_exists() -> None:
    agent_path = _AGENTS_ROOT / "helena" / "agent.yaml"
    assert agent_path.exists(), f"Helena agent.yaml not found at {agent_path}"


def test_helena_agent_yaml_has_required_fields() -> None:
    agent_path = _AGENTS_ROOT / "helena" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)
    assert data is not None
    assert data["id"] == "helena"
    assert data["name"] == "Helena Moreira"
    assert "role" in data
    assert "tools" in data
    assert data.get("phase") == 0


# ---------------------------------------------------------------------------
# build(config) — fail-closed contract
# ---------------------------------------------------------------------------


def test_build_requires_all_dependencies() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({})


def test_build_with_full_config_compiles() -> None:
    graph = build(
        {
            "inference": _FakeInference([]),
            "dmn": FakeDmnTransport(),
            "cibseven": FakeCibSevenTransport(),
            "whatsapp": _FakeWhatsAppSender(),
        }
    )
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert {"receive", "classify", "inform", "schedule", "escalate", "respond"} <= node_names


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_business_key_format() -> None:
    state = _base_state(tenant_id="amh", conversation_id="wa:amh:abc123")
    assert _business_key(state) == "ESC-amh-wa:amh:abc123"


def test_to_hash_from_state_extracts_hash() -> None:
    state = _base_state(conversation_id="wa:amh:deadbeef")
    assert _to_hash_from_state(state) == "deadbeef"


def test_to_hash_from_state_falls_back_for_non_whatsapp_format() -> None:
    state = _base_state(conversation_id="portal-case-1")
    assert _to_hash_from_state(state) == "portal-case-1"


# ---------------------------------------------------------------------------
# receive
# ---------------------------------------------------------------------------


async def test_receive_missing_runtime_context_escalates() -> None:
    graph = _graph(inference=_FakeInference([]))
    result = await graph.receive({"message_body": "oi"})
    assert result["next_kind"] == "escalate"
    assert "error" in result


async def test_receive_with_context_passes_through() -> None:
    graph = _graph(inference=_FakeInference([]))
    result = await graph.receive(_base_state())
    assert result == {}


# ---------------------------------------------------------------------------
# classify — the 5 escalation triggers + the non-red-flag path
# ---------------------------------------------------------------------------


async def test_classify_red_flag_adult_escalates_red_flag_clinico() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "dor toracica"}],
    )
    inference = _FakeInference(
        [
            _classify_json(
                intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
            )
        ]
    )
    graph = _graph(inference=inference, dmn=dmn)

    result = await graph.classify(_base_state(message_body="dor no peito"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "red_flag_clinico"
    assert result["escalation_severidade"] == "grave"  # P1 -> grave
    assert result["dmn_decision_ref"].startswith("triage_redflag_adult#")


async def test_classify_no_red_flag_routes_inform() -> None:
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE"}])
    inference = _FakeInference(
        [_classify_json(intent="symptom", population="adult", sintoma_codigo="febre", intensidade="leve")]
    )
    graph = _graph(inference=inference, dmn=dmn)

    result = await graph.classify(_base_state(message_body="febre leve"))

    assert result["next_kind"] == "inform"


async def test_classify_psychosocial_risk_always_escalates_regardless_of_intent() -> None:
    """Gatilho 5: psychosocial risk is evaluated FIRST, highest priority — even if intent looks
    administrative."""
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_mental_health",
        [
            {
                "red_flag": True,
                "prioridade": "P1",
                "conduta": "ESCALATE_EMERGENCY",
                "motivo": "ideacao suicida",
            }
        ],
    )
    inference = _FakeInference([_classify_json(intent="information", psychosocial_risk=True)])
    graph = _graph(inference=inference, dmn=dmn)

    result = await graph.classify(_base_state(message_body="quero morrer"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "risco_psicossocial"
    assert result["escalation_severidade"] == "grave"


async def test_classify_clinical_question_escalates_intencao_clinica_never_answers() -> None:
    inference = _FakeInference([_classify_json(intent="clinical_question")])
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="isso e grave?"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "intencao_clinica"


async def test_classify_human_request_escalates_solicitacao_humano() -> None:
    inference = _FakeInference([_classify_json(intent="human_request")])
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="quero falar com uma pessoa"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "solicitacao_humano"


async def test_classify_scheduling_routes_schedule() -> None:
    inference = _FakeInference([_classify_json(intent="scheduling")])
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="quero marcar uma consulta"))

    assert result["next_kind"] == "schedule"


async def test_classify_dmn_unavailable_escalates_falha_tecnica_never_silent_no_red_flag() -> None:
    """ADR-0028 §3 fail-safe: DMN failure must NEVER be read as 'no red flag'."""
    dmn = FakeDmnTransport()  # nothing registered -> DmnEvaluationError on evaluate()
    inference = _FakeInference(
        [_classify_json(intent="symptom", population="adult", sintoma_codigo="dor_toracica")]
    )
    graph = _graph(inference=inference, dmn=dmn)

    result = await graph.classify(_base_state(message_body="dor"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "error" in result


async def test_classify_already_errored_state_is_noop() -> None:
    graph = _graph(inference=_FakeInference([]))
    result = await graph.classify(_base_state(error="boom", next_kind="escalate"))
    assert result == {}


# ---------------------------------------------------------------------------
# classify — R1 cycle-1 regression: classifier failure is fail-CLOSED (escalate falha_tecnica),
# never a silent inform default. Covers the verifier's two live-reproduced cases plus
# schema-invalid variants.
# ---------------------------------------------------------------------------


class _RaisingInference:
    """LLM seam that always raises — the verifier's 'LLM exception' live case."""

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        raise RuntimeError("LLM provider unavailable")


async def test_classify_llm_exception_escalates_falha_tecnica_never_informs() -> None:
    """Verifier live case 2: LLM exception + 'dor no peito muito forte' must escalate — the
    pre-fix behavior silently defaulted to intent='information' -> inform (fail-OPEN)."""
    graph = _graph(inference=_RaisingInference())

    result = await graph.classify(_base_state(message_body="dor no peito muito forte"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "classify LLM call failed" in result["error"]


async def test_classify_unparseable_json_escalates_falha_tecnica_never_informs() -> None:
    """Verifier live case 1: malformed JSON + 'não consigo respirar' must escalate."""
    inference = _FakeInference(["this is {not valid json at all"])
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="não consigo respirar"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "unparseable JSON" in result["error"]


async def test_classify_unknown_intent_enum_escalates_falha_tecnica() -> None:
    """Schema-invalid: an intent value outside classify-v1's enum is a classify failure."""
    inference = _FakeInference([_classify_json(intent="diagnose")])
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="me diga o que eu tenho"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "invalid_intent" in result["error"]


async def test_classify_invented_sintoma_codigo_escalates_falha_tecnica() -> None:
    """Schema-invalid: a non-allow-listed sintoma_codigo would silently miss every DMN symptom
    rule and land on the no-red-flag catch-all — it must escalate instead."""
    inference = _FakeInference(
        [_classify_json(intent="symptom", population="adult", sintoma_codigo="dor_de_cotovelo")]
    )
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="dor no cotovelo"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "non_allowlisted_sintoma_codigo" in result["error"]


async def test_classify_invalid_population_escalates_falha_tecnica() -> None:
    inference = _FakeInference([_classify_json(intent="symptom", population="idoso")])
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="dor"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "invalid_population" in result["error"]


async def test_classify_missing_psychosocial_risk_escalates_falha_tecnica() -> None:
    """Schema-invalid: the always-active gatilho-5 signal must never be silently absent."""
    inference = _FakeInference(['{"intent": "information", "population": "none"}'])
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="oi"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "missing_or_invalid_psychosocial_risk" in result["error"]


async def test_classify_out_of_domain_intensidade_escalates_falha_tecnica() -> None:
    """Schema-invalid: an out-of-domain intensidade would miss the DMN's own 'grave' fail-safe
    rows the same way an invented code would."""
    inference = _FakeInference(
        [
            _classify_json(
                intent="symptom", population="adult", sintoma_codigo="febre", intensidade="gravissima"
            )
        ]
    )
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="febre muito alta"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "invalid_intensidade" in result["error"]


async def test_full_turn_malformed_json_reaches_escalation_never_inform() -> None:
    """Full-graph version of verifier live case 1: the escalation process must actually be
    STARTED (escalation_started True), not just routed."""
    cibseven = FakeCibSevenTransport()
    # Call order after the classify failure: _resumo_contexto, then _respond_llm.
    inference = _FakeInference(["{{{malformed", "resumo tecnico", "um humano vai continuar"])
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=inference, cibseven=cibseven, whatsapp=sender).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(message_body="não consigo respirar"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert result["escalation_started"] is True
    assert result["escalation_business_key"] == "ESC-amh-wa:amh:deadbeef"
    assert sender.sent, "the beneficiary must still be told a human is taking over"


async def test_full_turn_llm_exception_reaches_escalation_never_inform() -> None:
    """Full-graph version of verifier live case 2: even with the LLM fully down (classify,
    resumo, AND respond all raising), the escalation starts and a canned fail-safe reply is
    still sent."""
    cibseven = FakeCibSevenTransport()
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=_RaisingInference(), cibseven=cibseven, whatsapp=sender).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(message_body="dor no peito muito forte"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert result["escalation_started"] is True
    assert sender.sent


async def test_escalate_carries_classify_failure_reason_into_resumo_contexto() -> None:
    """R1 cycle-1 fix: the technical-failure reason reaches the human handoff variable
    (`resumo_contexto`) — bounded, no raw LLM output, no beneficiary text."""
    recording: list[dict[str, Any]] = []

    class _RecordingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            recording.append(dict(variables))
            return await super().start_process_instance(process_key, business_key, variables)

    graph = _graph(inference=_FakeInference(["resumo", "resposta"]), cibseven=_RecordingCibSeven())
    await graph.escalate(
        _base_state(
            escalation_motivo="falha_tecnica",
            escalation_severidade="leve",
            error="classify LLM returned unparseable JSON",
        )
    )

    assert recording
    assert "falha tecnica: classify LLM returned unparseable JSON" in recording[0]["resumo_contexto"]


async def test_cpf_bearing_field_value_never_reaches_engine_variables() -> None:
    """R1 cycle-2 regression (leak, the verifier's exact probe): the LLM copies a
    beneficiary-typed CPF into `sintoma_codigo` — schema-invalid -> escalate falha_tecnica, and
    the ENGINE-BOUND process variables must contain NO fragment of the offending value, only
    the class token. Pre-fix, `_short()`'s `repr(value)[:80]` shipped the CPF verbatim into
    `resumo_contexto` (live-proven by the verifier: instance c0cbc6d2...)."""
    leaked_value = "CPF 123.456.789-00 dor"
    recording: list[dict[str, Any]] = []

    class _RecordingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            recording.append(dict(variables))
            return await super().start_process_instance(process_key, business_key, variables)

    inference = _FakeInference(
        [
            _classify_json(intent="symptom", population="adult", sintoma_codigo=leaked_value),
            "resumo tecnico",
            "um humano vai continuar",
        ]
    )
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=inference, cibseven=_RecordingCibSeven(), whatsapp=sender).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(message_body="qualquer coisa"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert result["escalation_started"] is True
    assert "non_allowlisted_sintoma_codigo" in result["error"]

    assert recording, "the escalation must have been started"
    serialized = json.dumps(recording[0], ensure_ascii=False, default=str)
    for fragment in ("123.456.789-00", "123.456.789", "456.789", "CPF"):
        assert fragment not in serialized, (
            f"engine-bound variables leaked a fragment of the offending field value "
            f"({fragment!r}): {serialized}"
        )
    assert "non_allowlisted_sintoma_codigo" in recording[0]["resumo_contexto"]


async def test_evaluate_dmn_selects_table_by_population() -> None:
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_pediatric", [{"red_flag": False, "conduta": "CONTINUE"}])
    graph = _graph(inference=_FakeInference([]), dmn=dmn)

    out = await graph._evaluate_dmn({"population": "pediatric", "sintoma_codigo": "febre", "idade_meses": 2})

    assert out["dmn_table"] == "triage_redflag_pediatric"
    assert dmn.calls[0][0] == "triage_redflag_pediatric"
    assert dmn.calls[0][1]["idade_meses"] == 2


# ---------------------------------------------------------------------------
# escalate — idempotent start, business key, fail-open response
# ---------------------------------------------------------------------------


async def test_escalate_starts_process_with_contract_variables() -> None:
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["resumo do caso", "um humano vai continuar"])
    graph = _graph(inference=inference, cibseven=cibseven)

    state = _base_state(
        escalation_motivo="red_flag_clinico",
        escalation_severidade="grave",
        dmn_decision_ref="triage_redflag_adult#1",
    )
    result = await graph.escalate(state)

    assert result["escalation_started"] is True
    assert result["escalation_business_key"] == "ESC-amh-wa:amh:deadbeef"
    assert result["escalation_process_ref"]["already_existed"] is False
    assert result["response_kind"] == "escalate"


async def test_escalate_is_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    business_key = "ESC-amh-wa:amh:deadbeef"
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-1",
            process_key="SP-OP-ESCALATION-001",
            business_key=business_key,
            state="ACTIVE",
            already_existed=True,
        )
    )
    inference = _FakeInference(["resumo", "resposta"])
    graph = _graph(inference=inference, cibseven=cibseven)

    result = await graph.escalate(
        _base_state(escalation_motivo="red_flag_clinico", escalation_severidade="grave")
    )

    assert result["escalation_process_ref"]["instance_id"] == "existing-1"
    assert result["escalation_process_ref"]["already_existed"] is True


async def test_escalate_defaults_motivo_from_error_when_absent() -> None:
    inference = _FakeInference(["resumo", "resposta"])
    graph = _graph(inference=inference)
    result = await graph.escalate(_base_state(error="tool failure upstream"))
    assert result["escalation_motivo"] == "falha_tecnica"


async def test_escalate_records_error_on_cibseven_failure_but_still_responds() -> None:
    class _FailingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
            raise CibSevenError("engine unreachable")

    inference = _FakeInference(["resumo", "resposta"])
    graph = _graph(inference=inference, cibseven=_FailingCibSeven())

    result = await graph.escalate(_base_state(escalation_motivo="outro", escalation_severidade="leve"))

    assert result["escalation_started"] is False
    assert "error" in result
    assert result["response_text"] == "resposta"


# ---------------------------------------------------------------------------
# respond
# ---------------------------------------------------------------------------


async def test_respond_sends_via_whatsapp_using_hash_not_raw_number() -> None:
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=_FakeInference([]), whatsapp=sender)

    result = await graph.respond(_base_state(response_text="oi, tudo bem?"))

    assert result == {}
    assert sender.sent == [("deadbeef", "oi, tudo bem?")]


async def test_respond_surfaces_transport_failure_never_swallows_silently() -> None:
    sender = _FakeWhatsAppSender(fail=True)
    graph = _graph(inference=_FakeInference([]), whatsapp=sender)

    result = await graph.respond(_base_state(response_text="oi"))

    assert "error" in result


# ---------------------------------------------------------------------------
# Full-graph turns (compiled, in-memory — no engine; see integration tests for the real engine)
# ---------------------------------------------------------------------------


async def test_full_turn_red_flag_reaches_escalation_and_response() -> None:
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "prioridade": "P1", "conduta": "ESCALATE_EMERGENCY", "motivo": "dor toracica"}],
    )
    cibseven = FakeCibSevenTransport()
    sender = _FakeWhatsAppSender()
    inference = _FakeInference(
        [
            _classify_json(
                intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
            ),
            "resumo do caso para o humano",
            "um profissional vai continuar",
        ]
    )
    graph = _graph(inference=inference, dmn=dmn, cibseven=cibseven, whatsapp=sender).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(message_body="dor forte no peito"))

    assert result["escalation_started"] is True
    assert result["escalation_business_key"] == "ESC-amh-wa:amh:deadbeef"
    assert result["escalation_motivo"] == "red_flag_clinico"
    assert sender.sent, "Helena must always tell the beneficiary a human is taking over"


async def test_full_turn_non_symptom_never_escalates() -> None:
    inference = _FakeInference([_classify_json(intent="information"), "aqui esta a informacao"])
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=inference, whatsapp=sender).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(message_body="qual minha rede credenciada?"))

    assert result.get("escalation_started") is not True
    assert result["response_kind"] == "inform"
    assert sender.sent


async def test_full_turn_missing_runtime_context_still_ends_in_human_handoff() -> None:
    """L0 hard: every path ends in a human task or an explicit safe handoff — even a
    programming-error-adjacent missing-context case degrades to escalate, never a dead end."""
    inference = _FakeInference(["resumo", "um humano vai continuar"])
    cibseven = FakeCibSevenTransport()
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=inference, cibseven=cibseven, whatsapp=sender).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke({"message_body": "oi"})

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
