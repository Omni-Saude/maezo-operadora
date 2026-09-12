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

from maezo.agents.helena.graph import (
    HELENA_INPUT_FIELDS,
    HelenaGraph,
    HelenaState,
    _business_key,
    _coerce_age,
    _is_explicitly_false,
    _to_hash_from_state,
    _validate_extraction,
    build,
)
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"


class _FakeInference:
    """Deterministic, in-order fake — never a real LLM SDK call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
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
    audit_sink: Any | None = None,
    whatsapp: Any | None = None,
) -> HelenaGraph:
    return HelenaGraph(
        inference=inference,
        dmn=dmn or FakeDmnTransport(),
        cibseven=cibseven or FakeCibSevenTransport(),
        audit_sink=audit_sink or FakeStartAuditSink(),
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


def test_helena_agent_yaml_marks_scheduling_out_of_scope_with_escalation_trigger() -> None:
    """GAP 9.2: the role no longer claims Helena performs scheduling — it is an explicit,
    tracked out-of-scope item with its own escalation trigger, mirroring what `graph.py`'s
    `schedule()` node actually does now (a real SP-OP-ESCALATION-001 handoff, not a silent
    dead end after an honest-but-unrouted refusal)."""
    agent_path = _AGENTS_ROOT / "helena" / "agent.yaml"
    with open(agent_path) as f:
        data = yaml.safe_load(f)

    assert "agendamento" not in data["role"] or "fora de escopo" in data["role"], (
        "role must not claim Helena performs scheduling directly without qualifying it as out of scope"
    )
    out_of_scope_ids = {item["id"] for item in data.get("out_of_scope", [])}
    assert "agendamento_direto" in out_of_scope_ids

    triggers = data["escalation"]["triggers"]
    assert {"intent": "scheduling"} in triggers


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
            "audit_sink": FakeStartAuditSink(),
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


async def test_receive_with_context_resets_all_output_fields_to_neutral() -> None:
    """T1.11 layer-1 entry sanitization: with valid runtime context, `receive` no longer
    passes through untouched — it resets EVERY output-only field to its neutral default so a
    caller-planted value can never be read downstream. `error` must be neutral (falsy) so the
    classify bail only ever fires on errors THIS graph set after the reset."""
    graph = _graph(inference=_FakeInference([]))
    result = await graph.receive(_base_state())
    assert frozenset(result) == frozenset(HelenaState.__annotations__) - HELENA_INPUT_FIELDS
    assert not result["error"]
    assert result["next_kind"] == "inform"
    assert result["escalation_started"] is False


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


async def test_classify_llm_call_declares_task_kind_task_default() -> None:
    """CC-12/BEA-01 (ADR-0009 §2): `_classify_llm` is structured extraction (JSON), not
    reasoning over already-decided facts — `task_default`."""
    inference = _FakeInference([_classify_json()])
    graph = _graph(inference=inference)
    await graph.classify(_base_state())
    assert inference.task_kinds == ["task_default"]


async def test_inform_respond_llm_call_declares_task_kind_task_default() -> None:
    """CC-12/BEA-01 (ADR-0009 §2): `_respond_llm` phrases facts already decided (DMN motivo,
    escalation severidade) for the beneficiary — `task_default`, same rationale as lucas's
    `_build_message`."""
    inference = _FakeInference(["resposta"])
    graph = _graph(inference=inference)
    await graph.inform(_base_state())
    assert inference.task_kinds == ["task_default"]


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


async def test_classify_greeting_informs_and_opens_nothing() -> None:
    """11/09/2026: uma saudacao sem pedido nao e' trabalho para ninguem.

    Antes de `classify-v3` nao havia intencao para saudacao, e o modelo pegava a vizinha mais
    proxima: "Opa" saiu `human_request` e abriu SP-OP-ESCALATION-001 com P3 e prazo de 4h,
    enquanto "Ola, bom dia" saiu `information` e resolveu sozinha. Duas saudacoes, dois desfechos.
    """
    inference = _FakeInference([_classify_json(intent="greeting")])
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="opa"))

    assert result["next_kind"] == "inform"
    assert result["intent"] == "greeting"
    # Nada de escalonamento: nem motivo, nem severidade.
    assert not result.get("escalation_motivo")
    assert not result.get("escalation_severidade")


async def test_classify_greeting_with_symptom_goes_through_the_dmn_instead() -> None:
    """Saudacao COM sintoma nao e' saudacao — e a rota nao confia no prompt para isso.

    O prompt manda o modelo usar a outra intencao quando ha pedido junto, mas se ele desobedecer
    e devolver `greeting` com um codigo de sintoma, o turno tem de passar pela DMN em vez de
    terminar em resposta automatica.
    """
    dmn = FakeDmnTransport()
    dmn.register(
        "triage_redflag_adult",
        [{"red_flag": True, "conduta": "ESCALATE_URGENTE", "prioridade": "P1", "motivo": "sintetico"}],
    )
    inference = _FakeInference(
        [
            _classify_json(
                intent="greeting",
                sintoma_codigo="dor_toracica",
                intensidade="grave",
                population="adult",
            )
        ]
    )
    graph = _graph(inference=inference, dmn=dmn)

    result = await graph.classify(_base_state(message_body="bom dia, dor no peito"))

    assert result["intent"] == "symptom", "a saudacao com sintoma tem de ser reclassificada"
    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "red_flag_clinico"


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

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
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


# ---------------------------------------------------------------------------
# age-field input gate (t2.7) — an LLM-sourced age feeds the red-flag DMN as a
# typed integer; a malformed value must escalate DETERMINISTICALLY, never reach
# the DMN where a wrong/ambiguous age could MISS a red flag.
# ---------------------------------------------------------------------------


def test_coerce_age_accepts_int_numeric_string_and_none() -> None:
    assert _coerce_age(5) == (True, 5)
    assert _coerce_age("5") == (True, 5)  # quoted JSON number -> coerced to int
    assert _coerce_age(" 12 ") == (True, 12)
    assert _coerce_age(0) == (True, 0)
    assert _coerce_age(None) == (True, None)  # absent age is valid


def test_coerce_age_rejects_non_coercible_and_ambiguous_values() -> None:
    # Fail-closed on ANY ambiguous/malformed shape — never int(float(...)) a fraction.
    assert _coerce_age([5]) == (False, None)
    assert _coerce_age("abc") == (False, None)
    assert _coerce_age("5.9") == (False, None)
    assert _coerce_age(5.9) == (False, None)
    assert _coerce_age("-5") == (False, None)
    assert _coerce_age(-5) == (False, None)
    assert _coerce_age(True) == (False, None)  # bool is an int subclass — reject


def test_coerce_age_unicode_digit_glyph_fails_closed_never_raises() -> None:
    """A Unicode digit glyph that `str.isdigit()` accepts but `int()` cannot parse (superscript
    `"²"`, circled digits) fails CLOSED to (False, None) — never raises ValueError.

    Regression guard for the `isdigit()` -> `isdecimal()` fix: `"²".isdigit()` is True yet
    `int("²")` raises; the call site (`_validate_extraction`) does not wrap `_coerce_age`, so a
    raise here would escape unwrapped. `isdecimal()` admits only int()-parseable base-10 digits.
    """
    # Demonstrate the footgun the fix closes: a superscript is isdigit() (old guard) but not
    # isdecimal() (new guard), and int() raises on it.
    assert "²".isdigit() and not "²".isdecimal()
    with pytest.raises(ValueError):
        int("²")
    for glyph in ("²", "⁵", "①", "5²", "½"):
        assert _coerce_age(glyph) == (False, None), glyph


def test_validate_extraction_coerces_valid_numeric_string_age_in_place() -> None:
    data = {
        "intent": "symptom",
        "population": "adult",
        "psychosocial_risk": False,
        "sintoma_codigo": "febre",
        "intensidade": "leve",
        "idade_anos": "5",
    }
    assert _validate_extraction(data) is None
    assert data["idade_anos"] == 5  # coerced str -> int for the DMN's `integer` type


async def test_classify_malformed_age_list_escalates_falha_tecnica_never_reaches_dmn() -> None:
    """A non-integer age (list) must fail closed DETERMINISTICALLY — not rely on an incidental
    downstream engine error — and never be handed to the red-flag DMN."""
    dmn = FakeDmnTransport()  # register the table so a leak-through would NOT error incidentally
    dmn.register("triage_redflag_adult", [{"red_flag": False, "conduta": "CONTINUE"}])
    inference = _FakeInference(
        [_classify_json(intent="symptom", population="adult", sintoma_codigo="febre", idade_anos=[5])]
    )
    graph = _graph(inference=inference, dmn=dmn)

    result = await graph.classify(_base_state(message_body="febre, tenho 5 anos"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "invalid_age" in result["error"]
    assert dmn.calls == []  # the bad age NEVER reached the DMN


async def test_classify_non_numeric_string_age_escalates_falha_tecnica() -> None:
    inference = _FakeInference(
        [_classify_json(intent="symptom", population="pediatric", sintoma_codigo="febre", idade_meses="abc")]
    )
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="bebe com febre"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "invalid_age" in result["error"]


async def test_classify_fractional_age_string_fails_closed_never_truncates() -> None:
    """`"5.9"` must NOT be silently truncated to 5 (a plausible-but-wrong age) — fail closed."""
    inference = _FakeInference(
        [_classify_json(intent="symptom", population="adult", sintoma_codigo="febre", idade_anos="5.9")]
    )
    graph = _graph(inference=inference)

    result = await graph.classify(_base_state(message_body="febre"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert "invalid_age" in result["error"]


async def test_classify_valid_numeric_string_age_passes_coerced_int_to_dmn() -> None:
    """A valid quoted age reaches the DMN as a real integer (not the raw string)."""
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "conduta": "CONTINUE"}])
    inference = _FakeInference(
        [_classify_json(intent="symptom", population="adult", sintoma_codigo="febre", idade_anos="42")]
    )
    graph = _graph(inference=inference, dmn=dmn)

    result = await graph.classify(_base_state(message_body="febre, 42 anos"))

    assert result["next_kind"] == "inform"  # no red flag
    assert dmn.calls[0][1]["idade_anos"] == 42
    assert isinstance(dmn.calls[0][1]["idade_anos"], int)


async def test_classify_absent_age_still_works() -> None:
    """Many turns carry no age — absence is valid and must classify normally."""
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "conduta": "CONTINUE"}])
    inference = _FakeInference([_classify_json(intent="symptom", population="adult", sintoma_codigo="febre")])
    graph = _graph(inference=inference, dmn=dmn)

    result = await graph.classify(_base_state(message_body="febre"))

    assert result["next_kind"] == "inform"
    assert dmn.calls[0][1].get("idade_anos") is None


# ---------------------------------------------------------------------------
# risco_imediato coercion (t2.7 adjacent) — `bool("false") == True` bug fixed:
# only an EXPLICIT false disables immediate-risk; unknowns stay fail-safe.
# ---------------------------------------------------------------------------


def test_is_explicitly_false_only_for_native_false_and_string_false() -> None:
    assert _is_explicitly_false(False) is True
    assert _is_explicitly_false("false") is True
    assert _is_explicitly_false("False") is True
    assert _is_explicitly_false(True) is False
    assert _is_explicitly_false("true") is False
    assert _is_explicitly_false(None) is False
    assert _is_explicitly_false("maybe") is False
    assert _is_explicitly_false([]) is False


async def test_evaluate_dmn_risco_imediato_string_false_is_not_immediate_risk() -> None:
    """The prior `bool("false")` coerced the string `"false"` to True — fixed."""
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_mental_health", [{"red_flag": True, "conduta": "ESCALATE"}])
    graph = _graph(inference=_FakeInference([]), dmn=dmn)

    await graph._evaluate_dmn(
        {"population": "mental_health", "risco_imediato": "false"},
        force_population="mental_health",
    )

    assert dmn.calls[0][1]["risco_imediato"] is False


async def test_evaluate_dmn_risco_imediato_unknown_defaults_to_immediate_risk() -> None:
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_mental_health", [{"red_flag": True, "conduta": "ESCALATE"}])
    graph = _graph(inference=_FakeInference([]), dmn=dmn)

    # None (absent) and an unparseable value both stay conservatively immediate-risk.
    await graph._evaluate_dmn({"population": "mental_health"}, force_population="mental_health")
    await graph._evaluate_dmn(
        {"population": "mental_health", "risco_imediato": "maybe"},
        force_population="mental_health",
    )

    assert dmn.calls[0][1]["risco_imediato"] is True
    assert dmn.calls[1][1]["risco_imediato"] is True


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


async def test_escalate_declares_task_kind_reasoning_then_task_default() -> None:
    """CC-12/BEA-01 (ADR-0009 §2): `escalate` drafts TWO LLM calls, in order — `_resumo_contexto`
    (what the human attendant reads before taking over: `reasoning`, same rationale as lucas's
    escalation dossier) then `_respond_llm` (the beneficiary-facing handoff text, phrasing
    already-decided facts: `task_default`)."""
    inference = _FakeInference(["resumo do caso", "um humano vai continuar"])
    graph = _graph(inference=inference)

    state = _base_state(
        escalation_motivo="red_flag_clinico",
        escalation_severidade="grave",
        dmn_decision_ref="triage_redflag_adult#1",
    )
    await graph.escalate(state)

    assert inference.task_kinds == ["reasoning", "task_default"]


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


async def test_escalate_never_fabricates_leve_when_severidade_absent() -> None:
    """HELENA-SEVERIDADE-DEFAULT: mirrors GAP-ESC-SEVERITY-GROUP's own principle one layer up.
    `escalate`'s ONLY reachable caller without a real, gatilho-assigned `escalation_severidade`
    is `receive`'s missing-runtime-context escalate (`_base_state` here has no
    `escalation_severidade` key at all, exactly like that path leaves it after the neutral-output
    reset). A severity that was never determined is UNKNOWN, never the mildest `leve` — this must
    ride through as `None` verbatim into the engine payload, never fabricated by Helena.

    §Delta-3 (regressao P-12) — CORRECAO: this docstring used to end "so the ALREADY-fixed worker
    boundary (`escalation.py::_exigir_severidade`) is the one that fails closed on it (refuse ->
    supervisor fallback -> mandatory HITL)". The live engine proved that refusal did NOT reach the
    HITL. This state carries `escalation_motivo="falha_tecnica"` (derived from `error`) — the one
    motivo the contract declares `null` for — so the worker now ACCEPTS the `None` and the
    notification goes out. What this test pins is unchanged, and is Helena's half alone: `None`
    leaves this graph verbatim, never a fabricated `leve`."""
    recording: list[dict[str, Any]] = []

    class _RecordingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            recording.append(dict(variables))
            return await super().start_process_instance(process_key, business_key, variables)

    inference = _FakeInference(["resumo", "resposta"])
    graph = _graph(inference=inference, cibseven=_RecordingCibSeven())

    result = await graph.escalate(_base_state(error="tool failure upstream"))

    assert result["escalation_severidade"] is None
    assert recording, "escalate must still start SP-OP-ESCALATION-001 (never a dead end)"
    assert recording[0]["severidade"] is None


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
# schedule — GAP 9.2: a real human handoff, not a dead end after an honest refusal
# ---------------------------------------------------------------------------


async def test_schedule_starts_escalation_with_solicitacao_humano() -> None:
    """Pre-fix, `schedule()` only drafted a reply promising a human follow-up and never started
    one — this proves the promise is now backed by a real SP-OP-ESCALATION-001 start."""
    cibseven = FakeCibSevenTransport()
    inference = _FakeInference(["resumo do agendamento", "um humano vai continuar"])
    graph = _graph(inference=inference, cibseven=cibseven)

    result = await graph.schedule(_base_state(message_body="quero remarcar minha consulta"))

    assert result["escalation_started"] is True
    assert result["escalation_motivo"] == "solicitacao_humano"
    assert result["escalation_severidade"] == "leve"
    assert result["escalation_business_key"] == "ESC-amh-wa:amh:deadbeef"
    assert result["response_kind"] == "schedule"
    assert result["response_text"] == "um humano vai continuar"


async def test_schedule_is_idempotent_on_active_instance() -> None:
    cibseven = FakeCibSevenTransport()
    business_key = "ESC-amh-wa:amh:deadbeef"
    cibseven.seed_instance(
        ProcessInstance(
            instance_id="existing-sched-1",
            process_key="SP-OP-ESCALATION-001",
            business_key=business_key,
            state="ACTIVE",
            already_existed=True,
        )
    )
    inference = _FakeInference(["resumo", "resposta"])
    graph = _graph(inference=inference, cibseven=cibseven)

    result = await graph.schedule(_base_state())

    assert result["escalation_process_ref"]["instance_id"] == "existing-sched-1"
    assert result["escalation_process_ref"]["already_existed"] is True


async def test_schedule_records_error_on_cibseven_failure_but_still_responds() -> None:
    """Mirrors `test_escalate_records_error_on_cibseven_failure_but_still_responds` — an engine
    outage never blocks the turn, but it must never be silently dropped either."""

    class _FailingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(self, *args: Any, **kwargs: Any) -> ProcessInstance:
            raise CibSevenError("engine unreachable")

    inference = _FakeInference(["resumo", "resposta"])
    graph = _graph(inference=inference, cibseven=_FailingCibSeven())

    result = await graph.schedule(_base_state())

    assert result["escalation_started"] is False
    assert "error" in result
    assert result["response_kind"] == "schedule"
    assert result["response_text"] == "resposta"


async def test_full_turn_scheduling_reaches_real_escalation_never_a_dead_end() -> None:
    """Full-graph version: `intent=scheduling` must reach an ACTUALLY STARTED escalation, not
    just the `schedule` response node."""
    cibseven = FakeCibSevenTransport()
    # Call order: classify, then (inside schedule -> _start_escalation) resumo, then respond.
    inference = _FakeInference(
        [_classify_json(intent="scheduling"), "resumo do agendamento", "um humano vai continuar"]
    )
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=inference, cibseven=cibseven, whatsapp=sender).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(message_body="preciso remarcar minha consulta"))

    assert result["next_kind"] == "schedule"
    assert result["response_kind"] == "schedule"
    assert result["escalation_motivo"] == "solicitacao_humano"
    assert result["escalation_started"] is True
    assert sender.sent, "the beneficiary must still receive the scheduling-specific reply"


# ---------------------------------------------------------------------------
# respond
# ---------------------------------------------------------------------------


async def test_respond_sends_via_whatsapp_using_hash_not_raw_number() -> None:
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=_FakeInference([]), whatsapp=sender)

    result = await graph.respond(_base_state(response_text="oi, tudo bem?"))

    # NEW-10: `desfecho` e' agora gravado no estado tambem no ramo de sucesso (o mesmo valor
    # rotulado na telemetria CC-09) -- antes deste WP `HelenaState.desfecho` era campo morto
    # em qualquer turno que nao falhasse ao iniciar o processo.
    assert result == {"desfecho": "resolvido_automatico"}
    assert sender.sent == [("deadbeef", "oi, tudo bem?")]


async def test_respond_records_escalado_humano_desfecho_when_escalation_started() -> None:
    """NEW-10: o mesmo campo, agora wired no OUTRO ramo de sucesso -- quando a escalacao
    realmente abriu, `desfecho` reflete `escalado_humano`, nunca a string vazia."""
    sender = _FakeWhatsAppSender()
    graph = _graph(inference=_FakeInference([]), whatsapp=sender)

    result = await graph.respond(_base_state(response_text="oi", escalation_started=True))

    assert result["desfecho"] == "escalado_humano"


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


# ---------------------------------------------------------------------------
# CC-06 / HEL-05 — the free-text chain into `resumo_contexto`, at BOTH ends
# ---------------------------------------------------------------------------


def _neutralize_start_chokepoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the SHARED chokepoint scrub into the identity function for the duration of one test.

    CC-06 §Delta (REVISE-2). Helena scrubs `resumo_contexto` TWICE by design: once at the
    PRODUCER (`_start_escalation`, defense in depth) and once at the shared chokepoint
    (`start_process_idempotent` -> `redact_start_variables`). Both these regressions observe the
    variables dict handed to `start_process_instance`, which is DOWNSTREAM of both — so with the
    chokepoint live they pass even when the producer's own scrub is deleted (proven by the
    verifier: removing `redact_free_text` from `_start_escalation` left the whole lane green).
    Neutralizing the chokepoint here is what makes these tests observe the PRODUCER: the only
    scrub left standing is Helena's own.

    Patched on `maezo.tools.mcp_cibseven.transport`, which is the module where
    `start_process_idempotent` resolves the name (`transport.py::start_process_idempotent` calls
    the module-global `redact_start_variables`), NOT on the agent module — Helena never imports
    it. `dict(...)` (not the input object) so a test can still tell a mutation from a copy.
    """
    monkeypatch.setattr(
        "maezo.tools.mcp_cibseven.transport.redact_start_variables",
        lambda variables: dict(variables),
    )


async def test_classify_llm_exception_text_never_reaches_engine_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HEL-05 (FEEDER): the inference provider raises, and its exception message echoes the
    request — which carries the beneficiary's own message body and the CPF they typed into it.
    That string becomes `state["error"]` (`classify`'s `falha_tecnica` return) and
    `_start_escalation` appends it to `resumo_contexto` as `[falha tecnica: ...]`, IN THE SAME
    TURN. Pre-fix, `str(exc)[:200]` bounded its LENGTH and nothing else. Synthetic CPF only.

    CC-06 §Delta: the shared chokepoint is neutralized so this observes the FEEDER
    (`_classify_llm`'s `redact_error_message`) and the SINK (`_start_escalation`'s suffix), not
    the chokepoint standing behind them."""
    _neutralize_start_chokepoint(monkeypatch)
    cpf = "123.456.789-09"

    class _ExplodingInference:
        async def generate(
            self,
            prompt: str,
            *,
            phi: bool = False,
            agent_id: str | None = None,
            tenant_id: str | None = None,
            task_kind: str | None = None,
        ) -> str:
            raise RuntimeError(f"provider 400 on request body: 'meu CPF e {cpf}, quero ajuda'")

    recording: list[dict[str, Any]] = []

    class _RecordingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            recording.append(dict(variables))
            return await super().start_process_instance(process_key, business_key, variables)

    graph = _graph(inference=_ExplodingInference(), cibseven=_RecordingCibSeven()).compile_graph()
    compiled = graph.compile()

    result = await compiled.ainvoke(_base_state(message_body=f"meu CPF e {cpf}, quero ajuda"))

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert result["escalation_started"] is True
    # The failure CLASS is still diagnosable by ops — only the identifier is gone.
    assert "classify LLM call failed" in result["error"]
    assert "RuntimeError" in result["error"]
    assert cpf not in result["error"]

    assert recording, "the escalation must have been started"
    serialized = json.dumps(recording[0], ensure_ascii=False, default=str)
    for fragment in (cpf, "123.456.789", "456.789-09"):
        assert fragment not in serialized, (
            f"engine-bound variables leaked {fragment!r} through the classify failure reason: {serialized}"
        )
    resumo = recording[0]["resumo_contexto"]
    assert "falha tecnica:" in resumo and "[REDACTED_DIGITS]" in resumo


async def test_falha_tecnica_suffix_is_redacted_at_the_producer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC-06 §Delta — the THIRD producer-side scrub, which nothing exercised until now.

    `_start_escalation` appends `[falha tecnica: {redact_error_message(state["error"])}]` to the
    handoff summary. With the chokepoint neutralized, the verifier-style probe (delete that
    `redact_error_message` call) left the WHOLE helena lane green: every existing test reaches
    this suffix through an `error` some OTHER scrub had already cleaned
    (`_classify_llm`/`respond`/`_evaluate_dmn` all call `redact_error_message` at their own
    write site), so the suffix's own net was structurally unobservable.

    `error` is CHECKPOINTED state (`HelenaState`, T4b live dispatch): it is read back on a LATER
    turn than the one that wrote it, and the writer is not necessarily the code shipping today.
    So it is fed here the way a checkpointer would hand it over — raw — which is precisely the
    case this scrub exists for. Synthetic identifiers only."""
    _neutralize_start_chokepoint(monkeypatch)
    cpf = "123.456.789-09"
    phone = "(11) 98765-4321"
    recording: list[dict[str, Any]] = []

    class _RecordingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            recording.append(dict(variables))
            return await super().start_process_instance(process_key, business_key, variables)

    inference = _FakeInference(["Resumo do atendimento.", "Um atendente humano vai continuar."])
    graph = _graph(inference=inference, cibseven=_RecordingCibSeven())

    # No `escalation_motivo` -> `escalate` derives `falha_tecnica` from the presence of `error`.
    await graph.escalate(_base_state(error=f"start_process indisponivel: CPF {cpf} fone {phone}"))

    assert recording
    resumo = recording[0]["resumo_contexto"]
    assert "falha tecnica:" in resumo, "ops must still see WHY the automated turn failed"
    for fragment in (cpf, phone, "123.456.789", "98765-4321"):
        assert fragment not in resumo, f"the falha-tecnica suffix leaked {fragment!r}: {resumo!r}"
    assert "[REDACTED_DIGITS]" in resumo and "[REDACTED_PHONE]" in resumo


async def test_resumo_contexto_identifiers_are_scrubbed_at_the_producer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HEL-05 (SINK): Helena is the direct producer of the contractual `resumo_contexto`, so the
    identifier net is applied at `_start_escalation` too, not only at the shared chokepoint. The
    summary SENTENCE must survive — SP-OP-ESCALATION-001 requires it pseudonimizado, not absent.

    CC-06 §Delta: the shared chokepoint is neutralized (`_neutralize_start_chokepoint`) so the
    only scrub between the LLM draft and the recorded variables is Helena's OWN — without that,
    deleting `redact_free_text` from `_start_escalation` left this test green (verifier probe
    (v)), i.e. it proved the chokepoint, not the producer it names."""
    _neutralize_start_chokepoint(monkeypatch)
    recording: list[dict[str, Any]] = []

    class _RecordingCibSeven(FakeCibSevenTransport):
        async def start_process_instance(
            self, process_key: str, business_key: str, variables: dict[str, Any]
        ) -> ProcessInstance:
            recording.append(dict(variables))
            return await super().start_process_instance(process_key, business_key, variables)

    inference = _FakeInference(
        [
            "Beneficiario quer atendente; informou CPF 123.456.789-09 e fone (11) 98765-4321.",
            "Um atendente humano vai continuar.",
        ]
    )
    graph = _graph(inference=inference, cibseven=_RecordingCibSeven())

    await graph.escalate(_base_state(escalation_motivo="solicitacao_humano", escalation_severidade="leve"))

    assert recording
    resumo = recording[0]["resumo_contexto"]
    assert "123.456.789-09" not in resumo and "98765-4321" not in resumo
    assert "[REDACTED_DIGITS]" in resumo and "[REDACTED_PHONE]" in resumo
    assert "Beneficiario quer atendente" in resumo
