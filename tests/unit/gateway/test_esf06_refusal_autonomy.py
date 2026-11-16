"""E-SF-06 — a commercial refusal of merit or by fraud is never an agent act.

EP-REG-29a (`test_merit_refusal_requires_human_review`) and TB-E-08-c
(`test_fraud_based_refusal_requires_l0_human`, target "testes de autonomia/PEP"). Molde
classificar-rotear-nunca-decidir: an agent that tries to emit a proposal refusal on the
merit (a denial — `authorization_denial`) or on fraud grounds (`fraud_accusation`) is
refused by the code-frozen L0 `HARD_ACTIONS` at both boundaries that exist today — the PEP
(`DENY`) and the capability admission binding (construction refused). Neither the C6-4
commercial plane nor the A5 support scope has a command through which a refusal could be
emitted: the source owns `refused`/`review_required`, and non-technical cases go to the
human `SP-OP-ESCALATION-001` queue through `support.handoff`/`work.*` (staff only).

GAP (TB-E-08-c "item de fila com prazo ≤ dia 25 do relógio"): the 25-day clock is
TB-E-09-a/E-SF-07 and the ratification of TB-08 is pending; no queue deadline is computed.

GAP (EP-REG-29a): `reason_ref` humano em `review_required` não existe no pin C6-4; sucessor C6-4.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from maezo.gateway.commercial_support_admission import A5_ADMITTED_COMMANDS
from maezo.gateway.pep import HARD_ACTIONS, Decision, build_pep
from tests.unit.gateway.capabilities.test_admission import binding
from tests.unit.test_commercial_http import schema_bytes as schema_bytes

REFUSAL_VERBS = ("refuse", "reject", "decline", "deny", "denial", "negativa", "recusa")


def test_merit_refusal_requires_human_review(schema_bytes):
    # Injected violation: an agent binding/action that emits a merit refusal.
    assert "authorization_denial" in HARD_ACTIONS
    assert build_pep().evaluate("authorization_denial", {"agent_id": "lucas"}) is Decision.DENY
    with pytest.raises(ValidationError, match="hard action requires its existing human boundary"):
        binding(autonomy_action="authorization_denial")
    # Merit outcomes exist only as source-owned states (`review_required` / `refused`),
    # never as a command: no C6-4 command, and none of the A5 scope, emits a refusal.
    schema = json.loads(schema_bytes)
    commands = set(schema["x-commands"])
    assert not [name for name in commands if any(verb in name for verb in REFUSAL_VERBS)]
    assert not [name for name in A5_ADMITTED_COMMANDS if any(verb in name for verb in REFUSAL_VERBS)]
    enrollment = schema["$defs"]["CustomerEnrollmentRead"]["oneOf"][0]["properties"]["enrollmentStatus"]
    assert "review_required" in enrollment["enum"]


def test_fraud_based_refusal_requires_l0_human():
    # Injected violation: the agent emits a refusal motivated by fraud.
    assert "fraud_accusation" in HARD_ACTIONS
    pep = build_pep()
    assert pep.evaluate("fraud_accusation", {"agent_id": "lucas", "level": "L2"}) is Decision.DENY
    with pytest.raises(ValidationError, match="hard action requires its existing human boundary"):
        binding(autonomy_action="fraud_accusation")
    # The matrix agrees with the code: fraud is L0 hard, not lowerable.
    policy = pep.matrix.policy_for("fraud_accusation")
    assert policy is not None and policy.level.value == "L0" and policy.hard
    # Control: a routing action that only classifies and hands off is not a refusal.
    assert binding(autonomy_action="scheduling").autonomy_action == "scheduling"
