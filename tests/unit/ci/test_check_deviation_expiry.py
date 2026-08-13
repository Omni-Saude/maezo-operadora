"""Behavioural proofs for the ratified-shadow-deviation expiry gate (PLANS.md §0.8, 2ª leva).

The claim under test is narrow and total: **a ratified `shadow` deviation cannot outlive its date
without turning CI red.** Everything here is one of its faces —

  (a) GREEN TODAY against the real shipped manifest, so wiring this into the required
      `validate-artifacts` check does not break the tree the day it lands;
  (b) RED once `--today` passes the deadline while the value is still `shadow`;
  (c) RED when the block is missing or malformed while the value is `shadow` — deleting the
      deadline must not be cheaper than honouring it;
  (d) WARNING (green, loud) inside the 14-day window;
  (e) EXEMPT once the value is flipped to `enforcing` — the deviation ended;
  (f) the RENEWAL PROPERTY: a tree carrying a NEW date is green on a date that was red before it.
      This is what makes the gate honest rather than merely obstructive.

DATES ARE HARDCODED HERE, ON PURPOSE. These tests pin what the owner ratified on 2026-08-13; they
must be an INDEPENDENT statement of those values, not a restatement of whatever the YAML currently
says. Parametrizing them over the manifest would make the pin self-satisfying — the file could
drift and the test would follow it, cheerfully green. Provenance is on every constant below.

Sibling of `tests/unit/ci/test_generate_xfail_census.py`: same posture of asserting the gate
against the REAL tree, not only against fixtures.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "ci"))

import check_deviation_expiry as gate  # noqa: E402 - path shim above, mirrors the sibling CI tests

from maezo.gateway import action_execution  # noqa: E402
from maezo.gateway.action_execution import load_action_approvals  # noqa: E402

_MANIFEST = _REPO_ROOT / "spec" / "policies" / "autonomy" / "action-approvals.yaml"

# ---------------------------------------------------------------------------------------------
# THE RATIFIED VALUES, restated independently.
#
# Provenance: owner's 2nd ratification batch (PLANS.md §0.8 "Decisões do dono — 2ª leva"), values
# confirmed 2026-08-13. Q-2 is the unmapped-ref default; Q-10 is the C2 PHI-read class, which is
# `leitura_phi_clinica` — design §10 Q-10 anchors the question on "a denied FHIR read degrades to a
# dossier gap note (rafael/graph.py:44-48)", i.e. `SHAPE_LACUNA_DECLARADA`, which is that class's
# denial shape and no other C2 class's.
#
# If one of these ever has to change, the change is a RE-RATIFICATION and belongs in the same
# reviewed PR as the YAML — which is exactly the friction the gate exists to create.
# ---------------------------------------------------------------------------------------------

_Q2_SLOT = "enforcement_padrao_nao_mapeado"
_Q2_OWNER_ROLE = "Security/crypto R1 reviewer (interim: dono)"
_Q2_DEADLINE_FIELD = "expires"
_Q2_EXPIRES = date(2026, 11, 11)
_Q2_CHECKPOINT = date(2026, 9, 12)

_C2_CLASS = "leitura_phi_clinica"
_C2_SLOT = "acoes.leitura_phi_clinica"
_C2_OWNER_ROLE = "Diretor(a) Médico(a) (interim, deadline-enforcement only: dono)"
_C2_DEADLINE_FIELD = "review_by"
_C2_REVIEW_BY = date(2027, 2, 9)
_C2_CHECKPOINT = date(2026, 11, 11)

_RATIFIED_ON = date(2026, 8, 13)
_RATIFIED_BY = "dono (2ª leva + confirmação 2026-08-13)"
_CRITERIA_REF = "PLANS.md §0.8 — 2ª leva Q-2/Q-10 (valores confirmados 2026-08-13)"

#: A date comfortably before both deadlines — "the day this landed".
_LANDING_DAY = date(2026, 8, 13)


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> Any:
    """`action_approvals` is lru_cached on the resolved path; a stale entry would fake a pass."""
    action_execution._load_cached.cache_clear()
    yield
    action_execution._load_cached.cache_clear()


def _levels(manifest_path: Path, today: date) -> dict[str, str]:
    findings = gate.evaluate(load_action_approvals(manifest_path), today)
    return {f.slot: f.level for f in findings}


def _shipped_dict() -> dict[str, Any]:
    return yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))


def _write(tmp_path: Path, manifest: dict[str, Any], name: str = "action-approvals.yaml") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# (0) The shipped record carries exactly the ratified values
# ---------------------------------------------------------------------------------------------


def test_the_shipped_manifest_carries_the_ratified_q2_deviation() -> None:
    """Independent pin of the Q-2 values. Failing here means the record moved without this test."""
    record = load_action_approvals(_MANIFEST).deviations[_Q2_SLOT]
    assert record.owner_role == _Q2_OWNER_ROLE
    assert record.deadline_field == _Q2_DEADLINE_FIELD
    assert record.deadline == _Q2_EXPIRES
    assert record.checkpoint == _Q2_CHECKPOINT
    assert record.ratified_on == _RATIFIED_ON
    assert record.ratified_by == _RATIFIED_BY
    assert record.criteria_ref == _CRITERIA_REF


def test_the_shipped_manifest_carries_the_ratified_c2_deviation() -> None:
    """Independent pin of the Q-10 values, on the class the design question is actually about."""
    approvals = load_action_approvals(_MANIFEST)
    assert _C2_CLASS in approvals.declared, "the C2 PHI-read class must still be declared"
    record = approvals.deviations[_C2_SLOT]
    assert record.owner_role == _C2_OWNER_ROLE
    assert record.deadline_field == _C2_DEADLINE_FIELD
    assert record.deadline == _C2_REVIEW_BY
    assert record.checkpoint == _C2_CHECKPOINT
    assert record.ratified_on == _RATIFIED_ON
    assert record.ratified_by == _RATIFIED_BY
    assert record.criteria_ref == _CRITERIA_REF


def test_the_deviation_blocks_did_not_approve_anything() -> None:
    """Declaring a deadline is not approving a class. The additive posture, asserted.

    A `deviation` block sits inside `acoes.leitura_phi_clinica` alongside `aprovacoes`. If adding
    governance metadata could ever move the approved set, that would be the forgery surface the
    whole manifest is built to deny.
    """
    approvals = load_action_approvals(_MANIFEST)
    assert approvals.approved == frozenset()
    assert approvals.mode == "shadow"
    assert approvals.default_enforcement == "shadow"
    assert approvals.class_enforcement[_C2_CLASS] == "shadow"
    assert approvals.deviation_defects == {}


# ---------------------------------------------------------------------------------------------
# (a) GREEN TODAY — the gate can be wired into a required check without breaking the tree
# ---------------------------------------------------------------------------------------------


def test_the_gate_is_green_against_the_shipped_manifest_today() -> None:
    assert gate.main(["--manifest", str(_MANIFEST)]) == 0


def test_the_gate_is_green_on_the_day_the_owner_ratified() -> None:
    levels = _levels(_MANIFEST, _LANDING_DAY)
    assert levels == {_Q2_SLOT: gate.LEVEL_OK, _C2_SLOT: gate.LEVEL_OK}


# ---------------------------------------------------------------------------------------------
# (b) RED once the deadline passes — the whole point
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "expected_red"),
    [
        pytest.param(date(2026, 11, 12), {_Q2_SLOT}, id="day-after-q2-expires"),
        pytest.param(date(2027, 2, 10), {_Q2_SLOT, _C2_SLOT}, id="day-after-c2-review-by"),
        pytest.param(date(2030, 1, 1), {_Q2_SLOT, _C2_SLOT}, id="years-later"),
    ],
)
def test_an_expired_deviation_turns_the_gate_red(today: date, expected_red: set[str]) -> None:
    levels = _levels(_MANIFEST, today)
    assert {slot for slot, level in levels.items() if level == gate.LEVEL_FAIL} == expected_red
    assert gate.main(["--manifest", str(_MANIFEST), "--today", today.isoformat()]) == 1


def test_the_deadline_itself_is_still_green_and_the_next_day_is_not() -> None:
    """`today <= deadline` passes. Pinning BOTH sides is what makes the boundary a fact."""
    assert _levels(_MANIFEST, _Q2_EXPIRES)[_Q2_SLOT] != gate.LEVEL_FAIL
    assert _levels(_MANIFEST, _Q2_EXPIRES.replace(day=12))[_Q2_SLOT] == gate.LEVEL_FAIL


def test_the_red_message_names_the_deviation_owner_date_and_renewal_procedure() -> None:
    """A red that does not say who owns it and how to close it teaches people to route around it."""
    findings = gate.evaluate(load_action_approvals(_MANIFEST), date(2026, 11, 12))
    red = next(f for f in findings if f.level == gate.LEVEL_FAIL)
    rendered = red.render()
    assert "Q-2" in rendered
    assert _Q2_OWNER_ROLE in rendered
    assert _Q2_EXPIRES.isoformat() in rendered
    assert "CODEOWNERS" in rendered
    assert "RE-RATIFICAR" in rendered
    assert "`enforcing`" in rendered


# ---------------------------------------------------------------------------------------------
# (c) RED when the record is missing or broken while the value is shadow
# ---------------------------------------------------------------------------------------------


def test_deleting_the_deviation_block_is_red_not_a_free_pass(tmp_path: Path) -> None:
    """The cheapest way to silence an expiry gate must not be to delete the deadline."""
    manifest = _shipped_dict()
    del manifest["deviation"]
    path = _write(tmp_path / "no-root", manifest)
    assert _levels(path, _LANDING_DAY)[_Q2_SLOT] == gate.LEVEL_FAIL
    assert gate.main(["--manifest", str(path), "--today", _LANDING_DAY.isoformat()]) == 1


def test_deleting_the_class_deviation_block_is_red(tmp_path: Path) -> None:
    manifest = _shipped_dict()
    del manifest["acoes"][_C2_CLASS]["deviation"]
    path = _write(tmp_path / "no-class", manifest)
    assert _levels(path, _LANDING_DAY)[_C2_SLOT] == gate.LEVEL_FAIL


@pytest.mark.parametrize(
    ("mutation", "case_id"),
    [
        pytest.param({"expires": "amanhã"}, "non-iso-date", id="non-iso-date"),
        pytest.param({"owner_role": "PENDENTE"}, "placeholder-owner", id="placeholder-owner"),
        pytest.param({"owner_role": ""}, "blank-owner", id="blank-owner"),
        pytest.param({"criteria_ref": None}, "null-criteria", id="null-criteria"),
    ],
)
def test_a_malformed_deviation_block_is_red(tmp_path: Path, mutation: dict[str, Any], case_id: str) -> None:
    manifest = _shipped_dict()
    manifest["deviation"].update(mutation)
    path = _write(tmp_path / case_id, manifest)
    assert _levels(path, _LANDING_DAY)[_Q2_SLOT] == gate.LEVEL_FAIL


def test_removing_the_deadline_field_entirely_is_red(tmp_path: Path) -> None:
    """A deviation with no end date is the permanent deviation the owner refused, in disguise."""
    manifest = _shipped_dict()
    del manifest["deviation"]["expires"]
    path = _write(tmp_path / "no-deadline", manifest)
    assert _levels(path, _LANDING_DAY)[_Q2_SLOT] == gate.LEVEL_FAIL


def test_swapping_the_deadline_field_name_is_red(tmp_path: Path) -> None:
    """`expires` and `review_by` are different ratified commitments, not spelling variants."""
    manifest = _shipped_dict()
    manifest["deviation"]["review_by"] = manifest["deviation"].pop("expires")
    path = _write(tmp_path / "swapped", manifest)
    assert _levels(path, _LANDING_DAY)[_Q2_SLOT] == gate.LEVEL_FAIL


def test_an_unreadable_manifest_is_red_not_vacuously_green(tmp_path: Path) -> None:
    """No shadow values found is not the same fact as nothing to enforce."""
    path = tmp_path / "action-approvals.yaml"
    path.write_text("this: [is, not, a, manifest\n", encoding="utf-8")
    assert gate.main(["--manifest", str(path), "--today", _LANDING_DAY.isoformat()]) == 1


def test_dropping_the_tracked_class_is_red(tmp_path: Path) -> None:
    """A tracked deviation whose subject vanished is unanchored — say so, do not track nothing."""
    manifest = _shipped_dict()
    del manifest["acoes"][_C2_CLASS]
    path = _write(tmp_path / "no-class-at-all", manifest)
    assert _levels(path, _LANDING_DAY)[_C2_SLOT] == gate.LEVEL_FAIL


# ---------------------------------------------------------------------------------------------
# (d) The warning window — loud, early, and green
# ---------------------------------------------------------------------------------------------


def test_the_warning_window_is_loud_but_does_not_fail(capsys: pytest.CaptureFixture[str]) -> None:
    inside = date(2026, 10, 28)  # 14 days before 2026-11-11, hardcoded per the values above
    assert _levels(_MANIFEST, inside)[_Q2_SLOT] == gate.LEVEL_WARN
    assert gate.main(["--manifest", str(_MANIFEST), "--today", inside.isoformat()]) == 0
    assert "::warning" in capsys.readouterr().out


def test_the_day_before_the_window_opens_is_plain_green() -> None:
    """The control for the test above: 15 days out is OK, 14 is WARN. Isolates the boundary."""
    assert _levels(_MANIFEST, date(2026, 10, 27))[_Q2_SLOT] == gate.LEVEL_OK


def test_the_step_summary_is_written_when_github_asks_for_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    gate.main(["--manifest", str(_MANIFEST), "--today", date(2026, 10, 28).isoformat()])
    body = summary.read_text(encoding="utf-8")
    assert _Q2_SLOT in body and "⚠️" in body


# ---------------------------------------------------------------------------------------------
# (e) The exemption — a flipped value ends its deviation
# ---------------------------------------------------------------------------------------------


def test_a_flipped_value_is_exempt_even_with_a_long_expired_block(tmp_path: Path) -> None:
    """Metadata for a deviation that ENDED is history. It must not haunt the build forever."""
    manifest = _shipped_dict()
    manifest["enforcement_padrao_nao_mapeado"] = "enforcing"
    manifest["deviation"]["expires"] = "2001-01-01"
    path = _write(tmp_path / "flipped", manifest)
    levels = _levels(path, date(2030, 1, 1))
    assert levels[_Q2_SLOT] == gate.LEVEL_INFO, "stale-but-harmless is a note, never a failure"
    assert gate.main(["--manifest", str(path), "--today", "2030-01-01"]) == 1, (
        "the OTHER deviation is still expired on that date — this asserts the exemption is "
        "per-deviation, not a global mute"
    )


def test_a_flipped_class_with_no_block_at_all_is_simply_ok(tmp_path: Path) -> None:
    manifest = _shipped_dict()
    manifest["acoes"][_C2_CLASS]["enforcement"] = "enforcing"
    del manifest["acoes"][_C2_CLASS]["deviation"]
    path = _write(tmp_path / "flipped-clean", manifest)
    assert _levels(path, date(2030, 1, 1))[_C2_SLOT] == gate.LEVEL_OK


def test_both_flipped_is_green_forever(tmp_path: Path) -> None:
    """The end state the rollout is aiming at: no deviations left, no dates left to blow."""
    manifest = _shipped_dict()
    manifest["enforcement_padrao_nao_mapeado"] = "enforcing"
    del manifest["deviation"]
    manifest["acoes"][_C2_CLASS]["enforcement"] = "enforcing"
    del manifest["acoes"][_C2_CLASS]["deviation"]
    path = _write(tmp_path / "both-flipped", manifest)
    assert gate.main(["--manifest", str(path), "--today", "2099-12-31"]) == 0


# ---------------------------------------------------------------------------------------------
# (f) The renewal property — extending is possible, extending QUIETLY is not
# ---------------------------------------------------------------------------------------------


def test_a_re_ratified_date_is_green_on_a_day_that_was_red_before_it(tmp_path: Path) -> None:
    """The mechanism's honesty test.

    On 2026-11-12 the shipped tree is RED. A tree carrying a NEW owner-ratified date is GREEN on
    that same day — so the renewal PR can actually land, and no override was needed to land it.
    Both halves matter: without the red the deadline is decorative, and without the green the gate
    would be a trap with no legitimate exit.
    """
    day = "2026-11-12"
    assert gate.main(["--manifest", str(_MANIFEST), "--today", day]) == 1

    renewed = _shipped_dict()
    renewed["deviation"]["expires"] = "2027-05-11"
    renewed["deviation"]["checkpoint"] = "2027-02-11"
    renewed["deviation"]["ratified_by"] = "dono (re-ratificação hipotética)"
    path = _write(tmp_path / "renewed", renewed)
    assert gate.main(["--manifest", str(path), "--today", day]) == 0


# ---------------------------------------------------------------------------------------------
# Non-vacuity of the gate itself — AND OF THE SELF-CHECK, which is the watchman here.
#
# `self_check()` is what the gate runs before trusting any real measurement, so its own emptiness is
# not something the rest of the suite would notice: `assert self_check() == []` is satisfied just as
# well by `def self_check(): return []`. That gut was demonstrated: it turned ZERO tests red.
#
# The repair follows the in-repo precedent at `tests/unit/ci/test_generate_release_floor.py`
# (`SELF_CHECK_SCENARIOS` + `test_self_check_scenarios_are_internally_consistent`): pin the SCENARIO
# SET, not just the aggregate verdict. Four tests, each closing a different way the watchman could
# go quiet —
#   1. the declared set matches a hardcoded expectation and is non-empty (fixtures cannot be
#      deleted, renamed or silently reduced to the cases that happen to pass);
#   2. the built cases cover exactly the declared set (the builder cannot return fewer);
#   3. every declared scenario really produces its level through the REAL `evaluate`, driven here
#      rather than taken on the self-check's word — and every RED one yields >=1 FAIL finding;
#   4. `self_check` actually REPORTS: against a deliberately broken evaluator it must name every
#      scenario whose verdict changed. This is the one a `return []` gut cannot survive.
# ---------------------------------------------------------------------------------------------

#: Restated independently of the gate, exactly as the ratified dates above are: the point of a pin
#: is defeated if it is read out of the thing being pinned. Provenance: the seven fixtures
#: `check_deviation_expiry.self_check` has driven since it was written — four RED (the four distinct
#: ways a shadow deviation escapes its date), one WARN, two OK. Adding an eighth scenario is a
#: deliberate act and belongs in the same reviewed PR as this line.
_EXPECTED_SELF_CHECK_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("vencido", gate.LEVEL_FAIL),
    ("ausente", gate.LEVEL_FAIL),
    ("malformado", gate.LEVEL_FAIL),
    ("janela de aviso", gate.LEVEL_WARN),
    ("em dia", gate.LEVEL_OK),
    ("valor virado", gate.LEVEL_OK),
    ("manifesto ilegível", gate.LEVEL_FAIL),
)


def test_the_gates_own_self_check_passes() -> None:
    assert gate.self_check() == []


def test_the_declared_self_check_scenario_set_is_pinned() -> None:
    """(1) The fixture set itself, against a hardcoded expectation. Non-empty, in order, exact."""
    assert gate.SELF_CHECK_SCENARIOS, "the self-check declares no scenarios at all — it proves nothing"
    assert gate.SELF_CHECK_SCENARIOS == _EXPECTED_SELF_CHECK_SCENARIOS
    levels = {level for _, level in gate.SELF_CHECK_SCENARIOS}
    assert levels == {gate.LEVEL_FAIL, gate.LEVEL_WARN, gate.LEVEL_OK}, (
        "the self-check must exercise RED, WARN and GREEN — a set that only contains one of them "
        f"cannot show that the comparator can reach the others (got {sorted(levels)})"
    )


def test_the_built_self_check_cases_cover_exactly_the_declared_scenarios() -> None:
    """(2) The builder cannot quietly serve fewer cases than the constant declares."""
    built = gate.build_self_check_cases()
    assert [(name, expected) for name, _, expected in built] == list(gate.SELF_CHECK_SCENARIOS)


def test_every_declared_self_check_scenario_behaves_through_the_real_evaluator() -> None:
    """(3) Drive each scenario ourselves, through the real `evaluate`.

    Scoped to the finding for the scenario's OWN slot: these synthetic views declare a record for
    `TRACKED_DEVIATIONS[0]` only, so the second tracked deviation is legitimately RED in all of them
    (missing block) and would drown out the distinction being tested. The unreadable-manifest case
    reports one finding for the FILE and none per slot, hence the `or findings[:1]` fallback.
    """
    tracked_slot = gate.TRACKED_DEVIATIONS[0].slot
    for name, view, expected in gate.build_self_check_cases():
        findings = gate.evaluate(view, gate.SELF_CHECK_TODAY)
        assert findings, f"self-check scenario {name!r} produced no findings at all"
        own = [f for f in findings if f.slot == tracked_slot] or findings[:1]
        assert own[0].level == expected, (
            f"self-check scenario {name!r}: expected {expected}, got {own[0].level}"
        )
        expected_exit = 1 if expected == gate.LEVEL_FAIL else 0
        assert gate.exit_code_for(own) == expected_exit, (
            f"scenario {name!r} is declared {expected} but its own finding "
            f"{'does not fail' if expected_exit else 'fails'} the build"
        )


def test_the_self_check_reports_every_scenario_a_broken_evaluator_gets_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(4) THE WATCHMAN PIN. Swap in an evaluator that always says OK; `self_check` must object.

    Every scenario whose declared level is not OK has to be named in the returned problems. A
    `self_check` that had been gutted to `return []` — or that had quietly stopped iterating its
    fixtures — returns nothing here and fails this test, which is the whole point.
    """
    tracked_slot = gate.TRACKED_DEVIATIONS[0].slot
    always_ok = [gate.Finding(level=gate.LEVEL_OK, slot=tracked_slot, headline="broken evaluator")]
    monkeypatch.setattr(gate, "evaluate", lambda approvals, today: always_ok)

    problems = gate.self_check()

    should_be_caught = [name for name, level in gate.SELF_CHECK_SCENARIOS if level != gate.LEVEL_OK]
    assert should_be_caught, "fixture assumes at least one non-OK scenario exists to be caught"
    assert len(problems) == len(should_be_caught), problems
    for name in should_be_caught:
        assert any(f"self-check '{name}'" in p for p in problems), (
            f"a broken evaluator went unreported for scenario {name!r} — the self-check is not "
            f"exercising it. Problems reported: {problems}"
        )


def test_the_tracked_set_covers_every_ratified_deviation_in_the_shipped_manifest() -> None:
    """A deviation in the data that the gate does not track would expire unwatched.

    This is the drift that would quietly re-open the hole: someone adds a third `deviation:` block
    (a new time-boxed exception) and the gate, whose table is code-frozen, never looks at it.
    """
    shipped = set(load_action_approvals(_MANIFEST).deviations)
    tracked = {t.slot for t in gate.TRACKED_DEVIATIONS}
    assert shipped == tracked, (
        "every ratified deviation block must be in TRACKED_DEVIATIONS, and vice versa; "
        f"untracked={sorted(shipped - tracked)} tracked-but-absent={sorted(tracked - shipped)}"
    )
