"""Harness self-test for NEW-07 (EVAL-ABS-CHECK-BLIND-TO-SENDER-OUTPUT) -- harness B0's fourth
documented exception to `README.md`'s "Nobody but B0 edits `conftest.py` / `_harness.py` / this
README" rule (the exception precedent RAF-06 established, 2026-09-04 -- see
`test_harness_rule_fixtures.py`'s own header for the prior harness self-test in this style).

THE DEFECT (NEW-07): `_harness.py::assert_no_leak(blob, canaries)` scans only whatever `blob` a
caller hands it -- every existing call site passes `result.state` (the graph's RETURNED dict),
never what a `WhatsAppSender` double actually recorded. `src/maezo/agents/lucas/graph.py::
LucasGraph.send_escalation_ack` drafts an LLM-generated `ack_text` and sends it
(`await self._whatsapp.send(to_hash, ack_text)`) but never places `ack_text` in the dict it
returns (`{"mensagem_enviada": True, "ack_pending": False, "desfecho": "escalado_humano"}`) --
so a canary planted in that draft is invisible to EVERY existing golden's ABS check, no matter
what `leak_canaries` the golden declares, because `RunResult` itself carries no `whatsapp`/
`sender` field for any assertion to inspect.

THE FIX (`_harness.py`): `RunResult` grows a `whatsapp` field -- the sender double `run_case`
now wires into `config["whatsapp"]` BY DEFAULT (a fresh `FakeWhatsAppSender()`, unless
`extra_config` overrides it), so it is populated for EVERY case, not only the classifier-family
ones that actually declare a `whatsapp` seam (every `build(config)` reads its config via
`cfg.get(...)`, so an unused extra key is harmless for the other seven agents; their
`whatsapp.sent` simply stays `[]`). `assert_no_leak` grows one new keyword-only parameter,
`sender`: when given, it folds every `(to_hash, text)` pair actually sent into the SAME
serialized ABS scan. `run_mutation_check`'s own single internal call is extended the same way,
so every existing `*_mutation_check_leak_is_non_vacuous` test across every family file gets this
coverage for free, without editing those files. Both new surfaces default to `None`/absent -- a
call site that never passes `sender=` is completely unaffected; `test_classifier_evals.py`'s
three `test_<agent>_eval_tier_a` functions (helena/fernando/lucas -- the only family with an
outbound `WhatsAppSender` seam) are the ones updated to actually pass it.

This file proves the fix non-vacuous by REUSING an existing lucas golden (`EVL-LUCAS-02`,
read-only via `load_golden`/`mutate_plant_canary` -- no golden JSON file is edited) rather than
adding a new one, per this WP's overlap constraints (sibling waves are mid-flight on other
agents' golden files).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maezo.agents.lucas.graph import build as build_lucas

from ._harness import assert_no_leak, load_golden, mutate_plant_canary, run_case, run_mutation_check
from .conftest import FakeWhatsAppSender

LUCAS_CASES = load_golden("lucas")

#: Deterministic, low-entropy synthetic canary (never a real secret shape).
_SENDER_ONLY_CANARY = "canary-outbound-only-c1a2b3"


def _escalate_case() -> dict[str, Any]:
    """`EVL-LUCAS-02`: `route=escalate_human`, `process_started` defaults `True` (unmodified
    `FakeCibSevenTransport`), so `send_escalation_ack` runs and its `recorded_llm[-1]` IS the
    `_build_escalation_ack` draft that `mutate_plant_canary` plants the canary into -- the exact
    call site NEW-07 names."""
    return next(c for c in LUCAS_CASES if c["id"] == "EVL-LUCAS-02")


@pytest.mark.eval
async def test_evl_lucas_02_mutation_check_sender_leak_is_non_vacuous() -> None:
    """A canary planted onto the ack-drafting LLM response must be caught by the harness's ABS
    check even though it NEVER reaches `result.state` -- the exact NEW-07 scenario. Pre-fix,
    `run_mutation_check` itself raises `MUTATION CHECK FAILED ... vacuous` here (the corrupted
    golden silently PASSED its own check); post-fix it returns normally (the corruption IS
    caught, proving the ABS check non-vacuous against a sender-only leak)."""
    await run_mutation_check(
        build_lucas,
        _escalate_case(),
        mutation=lambda c: mutate_plant_canary(c, _SENDER_ONLY_CANARY),
        extra_config={"whatsapp": FakeWhatsAppSender()},
    )


@pytest.mark.eval
async def test_assert_no_leak_sees_sender_output_only_when_given_sender() -> None:
    """Structural proof of both halves of NEW-07's claim in one turn: (a) the planted canary
    truly never reaches `result.state` (the defect's own premise) yet (b) it WAS actually sent;
    (c) the base two-argument `assert_no_leak(state, canaries)` call -- every pre-existing call
    site's shape -- stays silent (backward-compatible, unchanged behaviour); (d) passing
    `sender=` is what actually catches it."""
    mutated = mutate_plant_canary(_escalate_case(), _SENDER_ONLY_CANARY)
    sender = FakeWhatsAppSender()
    result = await run_case(build_lucas, mutated, extra_config={"whatsapp": sender})

    serialized_state = json.dumps(result.state, ensure_ascii=False, default=str)
    assert _SENDER_ONLY_CANARY not in serialized_state, (
        "test setup invalid: the canary leaked into `state` directly -- it must reach the "
        "beneficiary ONLY via the WhatsApp ack for this to exercise NEW-07"
    )
    assert sender.sent, "test setup invalid: send_escalation_ack never called the sender"
    assert any(_SENDER_ONLY_CANARY in text for _, text in sender.sent), (
        "test setup invalid: the canary never reached the text actually sent"
    )

    # (c) unchanged base behaviour -- no `sender=`, no detection (documents backward-compat).
    assert_no_leak(result.state, [_SENDER_ONLY_CANARY])

    # (d) THE FIX -- passing `sender=` closes NEW-07.
    with pytest.raises(AssertionError, match="ABS violation"):
        assert_no_leak(result.state, [_SENDER_ONLY_CANARY], sender=result.whatsapp)
