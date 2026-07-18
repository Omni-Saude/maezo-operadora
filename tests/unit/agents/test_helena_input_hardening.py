"""Regression tests for the caller-planted OUTPUT-FIELD read-through class in Helena (T1.11).

An R1 adversarial probe (`scratchpad/probe_helena.py`) proved that a caller/upstream who plants
values into OUTPUT-ONLY `HelenaState` fields could forge escalation routing/provenance, or —
the clinically worst case — SUPPRESS a red-flag escalation. These tests port that probe into
proper, bite-proven regressions:

  * H1  — planted `error`+`next_kind='escalate'`+`escalation_motivo`/`escalation_severidade`/
          `dmn_decision_ref` sentinels must NOT reach the SP-OP-ESCALATION-001 engine payload.
  * H2  — planted `error`+`next_kind='inform'` on a RED-FLAG message must STILL escalate (the
          classify-SKIP anti-escalation; crown jewel).
  * H3  — planted `dmn_decision_ref` (forged ADR-0007 provenance, no `error`) must NOT reach the
          engine on any escalate path where classify did not itself run the DMN.

Every sentinel carries a UNIQUE, greppable token (some embedding a fake CPF) so a leak is
unambiguous. The fix has TWO layers; these tests exercise the per-graph `receive` reset by
driving the REAL compiled graph end to end, and the input-boundary gate directly.

BITE-PROOF: with the fix stashed (clean origin/main) every `*_planted_*` assertion below fails
(the tokens reach the engine; H2 does not escalate). See the PR body for the stash/run evidence.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maezo.agents.helena.graph import (
    _HELENA_NEUTRAL_OUTPUTS,
    HELENA_INPUT_FIELDS,
    HelenaGraph,
    HelenaState,
    gate_inbound_state,
    new_helena_state,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


class _RecordingCibSeven(FakeCibSevenTransport):
    """Captures every `start_process_instance` payload so a test can grep the engine variables."""

    def __init__(self) -> None:
        super().__init__()
        self.started: list[dict[str, Any]] = []

    async def start_process_instance(self, process_key: str, business_key: str, variables: dict[str, Any]):  # type: ignore[override]
        self.started.append({"business_key": business_key, "variables": dict(variables)})
        return await super().start_process_instance(process_key, business_key, variables)


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        return self._responses.pop(0) if self._responses else "DRAFTED_TEXT"


class _FakeWhatsAppSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
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


# The red-flag message the anti-escalation probe uses — a genuine clinical emergency.
_RED_FLAG_MSG = "nao consigo respirar e sinto dor forte no peito"

_BASE_INPUT: HelenaState = {
    "tenant_id": "amh",
    "conversation_id": "wa:amh:deadbeef",
    "canal": "whatsapp",
    "beneficiario_pseudo_id": "pseudo-123",
    "message_body": _RED_FLAG_MSG,
}

# Sentinels planted into OUTPUT-ONLY fields — each a unique token, several embedding a fake CPF.
_MOTIVO_SENTINEL = "SUSPENDER_TKN_CPF=123.456.789-00"
_SEVERIDADE_SENTINEL = "TKN_severidade_CPF=222.333.444-55"
_DMN_REF_SENTINEL = "TKN_dmnref#FORGED-ADR0007-CPF=999.888.777-66"


def _redflag_dmn() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    for table in (
        "triage_redflag_adult",
        "triage_redflag_pediatric",
        "triage_redflag_gestante",
        "triage_redflag_mental_health",
    ):
        dmn.register(
            table,
            [{"red_flag": True, "conduta": "ESCALATE_NOW", "prioridade": "P1", "motivo": "sinal de alarme"}],
            version=3,
        )
    return dmn


def _compiled(*, inference: _FakeInference, dmn: FakeDmnTransport, cib: _RecordingCibSeven):
    return (
        HelenaGraph(
            inference=inference,
            dmn=dmn,
            cibseven=cib,
            audit_sink=FakeStartAuditSink(),
            whatsapp=_FakeWhatsAppSender(),
        )
        .compile_graph()
        .compile()
    )


def _engine_blob(cib: _RecordingCibSeven) -> str:
    assert cib.started, "expected SP-OP-ESCALATION-001 to be started (fail-closed escalation)"
    return json.dumps(cib.started[0]["variables"], ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# H1 — planted error + next_kind='escalate' + escalation/dmn sentinels
# ---------------------------------------------------------------------------


async def test_h1_planted_escalation_fields_never_reach_engine_payload() -> None:
    cib = _RecordingCibSeven()
    compiled = _compiled(inference=_FakeInference(["RESUMO", "DRAFT_ESC"]), dmn=_redflag_dmn(), cib=cib)
    planted: dict[str, Any] = {
        **_BASE_INPUT,
        "error": "planted-bypass",
        "next_kind": "escalate",
        "escalation_motivo": _MOTIVO_SENTINEL,
        "escalation_severidade": _SEVERIDADE_SENTINEL,
        "dmn_decision_ref": _DMN_REF_SENTINEL,
    }

    result = await compiled.ainvoke(planted)

    # Still escalates (fail-closed), but via a graph-owned motivo — not the planted one.
    assert result["escalation_started"] is True
    blob = _engine_blob(cib)
    for token in (_MOTIVO_SENTINEL, _SEVERIDADE_SENTINEL, _DMN_REF_SENTINEL):
        assert token not in blob, f"planted sentinel leaked into engine payload: {token!r}"
    engine_vars = cib.started[0]["variables"]
    assert engine_vars["motivo_categoria"] != _MOTIVO_SENTINEL
    assert engine_vars["severidade"] != _SEVERIDADE_SENTINEL
    assert "dmn_decision_ref" not in engine_vars  # planted forged provenance dropped


# ---------------------------------------------------------------------------
# H2 — CROWN JEWEL: planted error+next_kind='inform' must NOT suppress a red-flag escalation
# ---------------------------------------------------------------------------


async def test_h2_red_flag_escalates_despite_planted_error_and_inform_routing() -> None:
    """The classify-SKIP anti-escalation. On clean main the planted `error` makes `classify`
    early-bail and `_route` reads the planted `next_kind='inform'` -> administrative answer, NO
    human (clinically worst case). Post-fix `receive` clears the planted `error`, `classify` runs
    the real red-flag DMN path, and the message escalates."""
    cib = _RecordingCibSeven()
    inference = _FakeInference(
        [
            _classify_json(
                intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
            ),
            "RESUMO",
            "DRAFT_ESC",
        ]
    )
    compiled = _compiled(inference=inference, dmn=_redflag_dmn(), cib=cib)

    result = await compiled.ainvoke({**_BASE_INPUT, "error": "planted-benign", "next_kind": "inform"})

    assert result["next_kind"] == "escalate", "red-flag message MUST escalate, never inform"
    assert result["response_kind"] == "escalate"
    assert result["escalation_started"] is True
    assert result["escalation_motivo"] == "red_flag_clinico"
    assert cib.started, "SP-OP-ESCALATION-001 must be started for a red flag"


async def test_h2_red_flag_escalates_even_when_classifier_also_fails() -> None:
    """Faithful port of the probe's own responses (unparseable classify output): even then the
    planted `next_kind='inform'` must not win — the failure fail-closes to escalate."""
    cib = _RecordingCibSeven()
    compiled = _compiled(inference=_FakeInference(["DRAFT_INFORM"]), dmn=_redflag_dmn(), cib=cib)

    result = await compiled.ainvoke({**_BASE_INPUT, "error": "planted-benign", "next_kind": "inform"})

    assert result["next_kind"] == "escalate"
    assert result["escalation_motivo"] == "falha_tecnica"
    assert cib.started


# ---------------------------------------------------------------------------
# H3 — planted dmn_decision_ref (forged ADR-0007 provenance) never reaches the engine
# ---------------------------------------------------------------------------


async def test_h3_planted_dmn_ref_dropped_on_clinical_question_escalate() -> None:
    cib = _RecordingCibSeven()
    inference = _FakeInference([_classify_json(intent="clinical_question"), "RESUMO", "DRAFT_ESC"])
    compiled = _compiled(inference=inference, dmn=_redflag_dmn(), cib=cib)

    result = await compiled.ainvoke(
        {**_BASE_INPUT, "message_body": "isso e grave?", "dmn_decision_ref": _DMN_REF_SENTINEL}
    )

    assert result["escalation_motivo"] == "intencao_clinica"
    blob = _engine_blob(cib)
    assert _DMN_REF_SENTINEL not in blob
    assert "dmn_decision_ref" not in cib.started[0]["variables"]


async def test_h3_planted_dmn_ref_dropped_on_dmn_down_symptom() -> None:
    cib = _RecordingCibSeven()
    inference = _FakeInference(
        [
            _classify_json(
                intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
            ),
            "RESUMO",
            "DRAFT_ESC",
        ]
    )
    # Empty DMN -> _evaluate_dmn raises -> falha_tecnica escalate; the planted ref must not survive.
    compiled = _compiled(inference=inference, dmn=FakeDmnTransport(), cib=cib)

    result = await compiled.ainvoke({**_BASE_INPUT, "dmn_decision_ref": _DMN_REF_SENTINEL})

    assert result["escalation_motivo"] == "falha_tecnica"
    blob = _engine_blob(cib)
    assert _DMN_REF_SENTINEL not in blob
    assert "dmn_decision_ref" not in cib.started[0]["variables"]


# ---------------------------------------------------------------------------
# Input-boundary gate (layer 2) + field-split completeness
# ---------------------------------------------------------------------------


def test_input_output_field_split_partitions_every_state_key() -> None:
    """ "Any missed key is a hole": INPUT + neutral-output sets must exactly cover HelenaState."""
    all_fields = HELENA_INPUT_FIELDS | frozenset(_HELENA_NEUTRAL_OUTPUTS)
    assert all_fields == frozenset(HelenaState.__annotations__)
    assert HELENA_INPUT_FIELDS.isdisjoint(frozenset(_HELENA_NEUTRAL_OUTPUTS))


def test_new_helena_state_yields_only_input_fields() -> None:
    state = new_helena_state(
        tenant_id="amh",
        conversation_id="wa:amh:deadbeef",
        canal="whatsapp",
        beneficiario_pseudo_id="pseudo-123",
        message_body="ola",
    )
    assert frozenset(state) == HELENA_INPUT_FIELDS


def test_new_helena_state_cannot_pass_through_output_fields() -> None:
    """The typed constructor's keyword-only signature makes a planted output key a TypeError."""
    with pytest.raises(TypeError):
        new_helena_state(  # type: ignore[call-arg]
            tenant_id="amh",
            conversation_id="wa:amh:deadbeef",
            canal="whatsapp",
            beneficiario_pseudo_id="pseudo-123",
            message_body="ola",
            next_kind="escalate",  # output-only — must be rejected
        )


def test_gate_inbound_state_drops_planted_output_fields() -> None:
    raw = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-123",
        "message_body": "ola",
        # Planted output-only fields an A2A/delegation payload must never be allowed to set:
        "next_kind": "inform",
        "error": "planted",
        "escalation_motivo": _MOTIVO_SENTINEL,
        "dmn_decision_ref": _DMN_REF_SENTINEL,
    }
    gated = gate_inbound_state(raw)
    assert frozenset(gated) == HELENA_INPUT_FIELDS
    for planted in ("next_kind", "error", "escalation_motivo", "dmn_decision_ref"):
        assert planted not in gated
