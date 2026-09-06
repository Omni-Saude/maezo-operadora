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

SECTION 2 (repair, §Delta F1, VERIFY-EVAL-HARNESS-SENDER.md): the independent verifier found that
the ORIGINAL fix above (`_harness.py` + `test_classifier_evals.py`) closed NEW-07 only for the
three `test_<agent>_eval_tier_a` functions -- `test_helena_journey_evals.py:73` and
`test_helena_clarity_evals.py:93` each ALSO build helena with a real `FakeWhatsAppSender` (wired
via their own `extra_config`) and call `assert_no_leak(result.state, ...)` directly, with no
`sender=`, exactly the pre-fix shape NEW-07 was written up for. Both call sites now pass
`sender=result.whatsapp` (§Delta fix, see the two files themselves -- no behaviour change for any
existing golden, since every EVL-HELENA-JOURNEY-*/EVL-HELENA-CLAREZA-* `leak_canaries` is `[]`
today). The AST fence below makes the CLASS of gap (a module that wires an outbound sender but has
a direct, state-shaped ABS check that forgets `sender=`) structurally impossible to reopen: any
NEW eval module -- or a new call site added to an existing one -- that wires
`FakeWhatsAppSender`/`"whatsapp"` and calls `assert_no_leak(<x>.state, ...)` without `sender=`
now fails collection-independent CI, not a future audit.

Deliberately NARROWER than "every `assert_no_leak` call in a module that mentions
FakeWhatsAppSender/whatsapp": `test_classifier_evals.py` ALSO calls `assert_no_leak(status.
variables, ...)` at several sites (EVL-HELENA-07/09's engine-bound-variable checks) -- those scan
a DIFFERENT blob (CibSeven process variables, not the graph's returned state) that has no
sender-output angle to miss (`assert_no_leak`'s own docstring documents both blob shapes as
independently valid), and flagging them would force unrelated changes with zero coverage gain.
Symmetrically, `test_dossier_admin_evals.py`/`test_dossier_adverse_evals.py` are excluded at the
MODULE level: neither file mentions `FakeWhatsAppSender`/`"whatsapp"` anywhere (confirmed by
`grep -l FakeWhatsAppSender tests/evals/test_*.py` below) because none of the seven agents they
drive (andre/beatriz/carolina/gustavo/marina/rafael/valentina) has an outbound `WhatsAppSender`
seam at all -- `RunResult.whatsapp.sent` would always be `[]` for them, so requiring `sender=`
there would be pure churn, not a real fence.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Structural fence (§Delta F1): every direct, STATE-SHAPED `assert_no_leak(<x>.state, ...)` call
# in a module that ALSO wires an outbound sender must pass `sender=`. AST-based (survives
# reformatting/renaming) so a FUTURE eval module -- not just today's two named sites -- cannot
# reopen NEW-07 by omission. See the module docstring's "SECTION 2" for the exact scoping
# rationale (why `status.variables`-shaped calls and sender-less modules are excluded on purpose).
# ---------------------------------------------------------------------------

_EVALS_DIR = Path(__file__).resolve().parent


def _module_has_outbound_sender_seam(source: str) -> bool:
    """A module "drives an agent with an outbound sender" (this fence's own scope) when it
    references `FakeWhatsAppSender` (imports/builds the double) or the literal config key
    `"whatsapp"` (wires it via `extra_config`) anywhere in its source -- the same two markers
    the repair brief itself names as the enumeration signal."""
    return "FakeWhatsAppSender" in source or '"whatsapp"' in source


def _assert_no_leak_state_calls_missing_sender(source: str) -> list[str]:
    """Every `assert_no_leak(<x>.state, ...)`-shaped call (the PRIMARY ABS-check shape -- this
    file's own module docstring: "every existing call site passes `result.state`") with no
    `sender=` keyword, by line number. `assert_no_leak` is always imported unqualified in this
    suite (`from ._harness import assert_no_leak`, confirmed by
    `grep -n "from ._harness import" tests/evals/test_*.py` -- no module aliases it), so the
    call target is always a bare `ast.Name`.

    Deliberately does NOT flag `assert_no_leak(status.variables, ...)`,
    `assert_no_leak(result.state.get("dossier"), ...)`, or any other blob shape -- only a first
    positional argument that is a BARE `<name>.state` attribute access, mirroring exactly the
    shape the two repaired call sites (and every classifier-family tier_a call) share.
    """
    achados: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "assert_no_leak"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not (isinstance(first_arg, ast.Attribute) and first_arg.attr == "state"):
            continue
        if not any(kw.arg == "sender" for kw in node.keywords):
            achados.append(f"line {node.lineno}: assert_no_leak(<...>.state, ...) has no sender= kwarg")
    return achados


#: Every `tests/evals/test_*.py` module that wires an outbound sender seam, EXCEPT this file
#: itself: `test_assert_no_leak_sees_sender_output_only_when_given_sender` above deliberately
#: keeps ONE two-argument `assert_no_leak(result.state, [_SENDER_ONLY_CANARY])` call (comment
#: "(c)") to document the base call's unchanged, backward-compatible (blind) behaviour -- that IS
#: the test's whole point, not a coverage gap this fence should flag.
_TEST_MODULES_WITH_SENDER_SEAM: list[Path] = sorted(
    p
    for p in _EVALS_DIR.glob("test_*.py")
    if p.name != Path(__file__).name and _module_has_outbound_sender_seam(p.read_text(encoding="utf-8"))
)


@pytest.mark.eval
@pytest.mark.parametrize("arquivo", _TEST_MODULES_WITH_SENDER_SEAM, ids=lambda p: p.name)
def test_state_shaped_assert_no_leak_calls_in_sender_seam_modules_pass_sender(arquivo: Path) -> None:
    """§Delta F1 fence: any eval module that wires `FakeWhatsAppSender`/`"whatsapp"` (drives an
    agent that CAN send outbound text) must pass `sender=` on every direct, state-shaped
    `assert_no_leak(<x>.state, ...)` call it makes, or the exact NEW-07 blind spot (state-only ABS
    scan, sender output invisible) silently reopens for that module. `sender=None` is an accepted
    value (harmless no-op for a case that never sends) -- the fence only requires the KEYWORD to
    be present.

    Before this repair, this test was RED for `test_helena_journey_evals.py` and
    `test_helena_clarity_evals.py` (verified in the repair report by temporarily reverting the
    fix at one site: the fence fails again, `git checkout --` restores it, the fence is GREEN
    again)."""
    achados = _assert_no_leak_state_calls_missing_sender(arquivo.read_text(encoding="utf-8"))
    assert not achados, (
        f'{arquivo.name}: {achados}. This module wires FakeWhatsAppSender/"whatsapp" (it drives '
        "an agent that can send outbound text), so every direct, state-shaped "
        "assert_no_leak(<x>.state, ...) call must pass sender=result.whatsapp (or sender=None if "
        "this specific case intentionally never sends) -- see RunResult.whatsapp / "
        "assert_no_leak's sender kwarg in _harness.py (NEW-07)."
    )


@pytest.mark.eval
def test_fence_helpers_are_non_vacuous() -> None:
    """Direct probe of both fence helpers against synthetic source -- proves the fence logic
    itself catches a missing `sender=`, accepts an explicit `sender=None`, and correctly
    distinguishes the state-shaped call from other blob shapes, WITHOUT depending on any real
    file staying in its current (already-fixed) shape. Mirrors
    `test_llm_calls_declare_task_kind.py::test_achados_de_task_kind_recusa_batch`'s
    synthetic-source self-test pattern for an AST fence in this codebase."""
    missing = (
        "from ._harness import assert_no_leak\ndef test_x():\n    assert_no_leak(result.state, canaries)\n"
    )
    achados = _assert_no_leak_state_calls_missing_sender(missing)
    assert len(achados) == 1
    assert "line 3" in achados[0]

    with_sender = missing.replace(
        "assert_no_leak(result.state, canaries)",
        "assert_no_leak(result.state, canaries, sender=result.whatsapp)",
    )
    assert _assert_no_leak_state_calls_missing_sender(with_sender) == []

    with_sender_none = missing.replace(
        "assert_no_leak(result.state, canaries)",
        "assert_no_leak(result.state, canaries, sender=None)",
    )
    assert _assert_no_leak_state_calls_missing_sender(with_sender_none) == [], (
        "sender=None must be ACCEPTED -- the fence checks for the keyword's presence, not its "
        "value; a case that legitimately never sends passes sender=None or "
        "sender=result.whatsapp with an empty .sent list identically"
    )

    # A DIFFERENT blob shape (engine variables, a dossier sub-tree) is never flagged -- these
    # scan a surface `assert_no_leak`'s own docstring documents as independently valid, with no
    # sender-output angle to miss.
    other_blob_shapes = (
        "from ._harness import assert_no_leak\n"
        "def test_y():\n"
        "    assert_no_leak(status.variables, canaries)\n"
        "def test_z():\n"
        "    assert_no_leak(result.state.get('dossier'), canaries)\n"
    )
    assert _assert_no_leak_state_calls_missing_sender(other_blob_shapes) == []

    # The module-level seam detector fires on either marker independently, and on neither for a
    # module with no outbound-sender concept at all (e.g. the dossier-admin/adverse families).
    assert _module_has_outbound_sender_seam("from .conftest import FakeWhatsAppSender\n")
    assert _module_has_outbound_sender_seam('return {"whatsapp": Something()}\n')
    assert not _module_has_outbound_sender_seam(other_blob_shapes)
