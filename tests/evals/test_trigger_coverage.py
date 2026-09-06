"""Coverage fence: every `escalation.triggers` entry an agent's `agent.yaml` declares has >= 1
golden case (`tests/evals/golden/<agent>/*.json`) tagged with it via a `"triggers"` list
(GOLDENS-MISSING, closes AND-06/BEA-11/FER-06/GUS-04/RAF-14/VAL-06 -- the GOLDEN-MISSING-SCENARIO
pattern family: "declared trigger/failure-path has no ADR-0009 golden").

WHY THIS EXISTS. CC-01/CC-08 (2026-09-04) each added ONE golden per agent as a side effect of
fixing THEIR OWN cross-fleet pattern (start-failure / DMN-down) -- closing the *infrastructure*
sub-shape of this gap fleet-wide, but leaving each agent's own *domain-specific* declared triggers
(a fraud-signal, a fail-safe route, a consent-revocation boundary, ...) to accumulate silently:
nobody owned reconciling agent.yaml's `escalation.triggers` list against the golden directory
end-to-end, per agent, until this WP. This fence is what stops that from recurring: a NEW trigger
appended to any enforced agent's `agent.yaml` with no matching golden goes RED here, at collection
time, rather than being noticed months later by another audit.

`triggers` SCHEMA. A golden case's OPTIONAL top-level `"triggers"` key is a list of canonical
strings, one per declared `escalation.triggers` entry it exercises BEHAVIORALLY (not merely
mentions in its description): `"dmn:<decision_key>=<value>"` for a `- dmn: <key>=<value>` YAML
entry, `"signal:<name>"` for a `- signal: <name>` entry (`"intent:<name>"` for helena's own
`- intent: <name>` entries, not used by this fence's 6 enforced agents). A case may tag zero,
one, or several triggers (e.g. EVL-VALENTINA-04 tags BOTH `dmn:programa_routing=ANALISE_HUMANA`
and `signal:ambiguity` -- the two agent.yaml lines are, honestly, the SAME `assess()` branch:
`elegivel not in ("ELEGIVEL", "NAO_ELEGIVEL")`). Absent entirely on a case (every pre-GOLDENS-
MISSING golden that this WP did not itself author or retroactively tag) means "proves no
declared trigger by itself" -- a CE/PL/GC case pinning the neutral/happy path, or a bespoke
FHIR-leak/audit-fence proof, is not thereby uncovered-trigger evidence.

SCOPE (deliberate, disclosed -- read before adding an agent to either set below). This fence
enforces the 6 agents GOLDENS-MISSING actually closed (`_AGENTS_ENFORCED`): every declared
trigger of andre/beatriz/fernando/gustavo/marina/valentina was traced by hand, this session,
through the REAL `graph.py` branch that fires it (module docstrings + `assess()`/`receive()`
reading, not grep), and either already had a golden or was given one
(`tests/evals/golden/<agent>/EVL-*-{07..13}.json`, see each new case's own `description`). The
other 4 fleet agents (carolina/helena/lucas/rafael) are NOT enforced -- `_AGENTS_NOT_YET_ENFORCED`
names each with why, per this file's own non-negotiable: no bare, unjustified exclusion.
Extending enforcement to them requires the SAME manual trigger-to-branch trace this WP performed
for its own 6; doing that honestly for 4 more agents is out of a single WP's scope and is future
work, not silently declared "already covered".
"""

from __future__ import annotations

import pytest

from maezo.agents import AgentLoader

from .conftest import GOLDEN_ROOT, load_golden

#: The 6 agents whose full `escalation.triggers` list this WP traced end-to-end against the real
#: graph code. Every entry here MUST have zero uncovered triggers (`test_every_declared_trigger_
#: has_a_golden` is parametrized over exactly this tuple).
_AGENTS_ENFORCED: tuple[str, ...] = ("andre", "beatriz", "fernando", "gustavo", "marina", "valentina")

#: Every OTHER agent under `tests/evals/golden/`, each with why it is not (yet) enforced above --
#: never a bare/silent exclusion (this file's own `test_not_yet_enforced_agents_are_all_justified`
#: fails collection if an agent directory exists in neither set, or if a reason here is blank).
_AGENTS_NOT_YET_ENFORCED: dict[str, str] = {
    "carolina": (
        "5 of carolina's 8 declared triggers (cred_admissibility=ANALISE_HUMANA, "
        "cred_route=ANALISE_CREDENCIAMENTO, cred_route=ANALISE_HUMANA, indicio_irregularidade, "
        "ambiguity) are closed by the UNMERGED sibling branch fleet2/carolina-catchall "
        "(EVL-CAROLINA-06/07/08 -- confirmed present on that branch by `git show "
        "fleet2/carolina-catchall:tests/evals/golden/carolina/EVL-CAROLINA-0{6,7,8}.json` during "
        "this WP's propagation-map step) but NOT YET in this branch's own tree. CAROLINA-CATCHALL "
        "owns carolina's goldens (CAR-05); GOLDENS-MISSING does not touch them. Move carolina "
        "into _AGENTS_ENFORCED once that branch merges and its 3 new goldens carry `triggers` "
        "tags (they do not yet, as of this WP -- tagging them is that merge's own follow-up, not "
        "retroactively done here against a branch this worktree does not contain)."
    ),
    "helena": (
        "Believed fully covered -- this WP's author manually mapped all 6 declared triggers "
        "(triage_redflag_*, clinical_question, human_request, scheduling, tool_failure, "
        "psychosocial_risk) to an existing EVL-HELENA-{01,03,04,05,07,08,09} golden by reading "
        "each case's description (round-5 triage separately marked the GOLDEN-MISSING-SCENARIO "
        "finding for helena, HEL-10, CLOSED) -- but that mapping was NOT re-derived branch-by-"
        "branch against graph.py the way the 6 GOLDENS-MISSING agents were in this WP, and no "
        "`triggers` tags were retroactively added to helena's 17 goldens (out of this WP's "
        "6-id scope: AND-06/BEA-11/FER-06/GUS-04/RAF-14/VAL-06). Tagging them is future work."
    ),
    "lucas": (
        "Not independently re-derived in this WP (out of the AND-06/BEA-11/FER-06/GUS-04/"
        "RAF-14/VAL-06 scope) -- lucas's own agent.yaml-vs-golden trigger audit, and tagging its "
        "7 existing goldens, is future work for whichever WP owns lucas's findings."
    ),
    "rafael": (
        "Not independently re-derived in this WP (out of scope, as above) -- rafael's own "
        "agent.yaml-vs-golden trigger audit, and tagging its 6 existing goldens, is future work "
        "for whichever WP owns rafael's findings."
    ),
}


def _canonical_triggers(agent_id: str) -> list[str]:
    """Every `escalation.triggers` entry of `agent_id`'s `agent.yaml`, canonicalized to
    `"<kind>:<value>"` (e.g. `"dmn:pagto_alcada=ANALISE_HUMANA"`, `"signal:ambiguity"`) -- the
    SAME string shape every golden's own `"triggers"` list uses.

    Reads via the REAL `AgentLoader.load_by_id` (the production spec-loading path, honors
    `MAEZO_SPEC_DIR`), never a hand-rolled YAML re-parse -- `AgentDefinition.escalation` is an
    untyped `dict[str, Any]` (`src/maezo/agents/__init__.py`), so this function is what gives the
    raw `triggers:` list its canonical shape for this fence.
    """
    definition = AgentLoader().load_by_id(agent_id)
    raw_triggers = definition.escalation.get("triggers") or []
    canonical: list[str] = []
    for entry in raw_triggers:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(
                f"{agent_id}'s agent.yaml declares a malformed escalation.triggers entry "
                f"{entry!r} (expected a single-key mapping, e.g. {{'signal': 'ambiguity'}})"
            )
        ((kind, value),) = entry.items()
        canonical.append(f"{kind}:{value}")
    return canonical


def _golden_trigger_coverage(agent_id: str) -> dict[str, list[str]]:
    """Map every trigger an `agent_id` golden tags to the golden id(s) proving it."""
    coverage: dict[str, list[str]] = {}
    for case in load_golden(agent_id):
        for trigger in case.get("triggers") or []:
            coverage.setdefault(trigger, []).append(case["id"])
    return coverage


@pytest.mark.eval
@pytest.mark.parametrize("agent_id", _AGENTS_ENFORCED)
def test_every_declared_trigger_has_a_golden(agent_id: str) -> None:
    """RED for any `escalation.triggers` entry with zero golden `"triggers"` coverage.

    Non-vacuousness (pasted as a manual mutation probe in REPORT-GOLDENS-MISSING.md, per
    BRIEF-COMMON SS3 -- this meta-fence has no per-trigger companion of its own, mirroring
    `test_dataset_counts_match_ratified_design`'s direct-assertion shape): deleting a covered
    trigger's `"triggers"` tag (or its only golden entirely) makes this test fail, NAMING the
    exact uncovered trigger string -- never a silent pass.
    """
    declared = _canonical_triggers(agent_id)
    covered = _golden_trigger_coverage(agent_id)
    uncovered = [t for t in declared if t not in covered]
    assert not uncovered, (
        f"{agent_id}'s agent.yaml declares trigger(s) with ZERO golden coverage: {uncovered!r} "
        f"(declared={declared!r}, covered={sorted(covered)!r})"
    )


@pytest.mark.eval
def test_not_yet_enforced_agents_are_all_justified() -> None:
    """Every agent directory under `tests/evals/golden/` is EITHER enforced above OR named in
    `_AGENTS_NOT_YET_ENFORCED` with a non-blank reason -- an agent silently missing from both
    would defeat this fence's own completeness (a 5th, 6th, ... agent added to the fleet later
    must be triaged into one set or the other, not fall through by omission)."""
    all_agents = {p.name for p in GOLDEN_ROOT.iterdir() if p.is_dir()}
    accounted = set(_AGENTS_ENFORCED) | set(_AGENTS_NOT_YET_ENFORCED)
    assert all_agents == accounted, (
        f"agent(s) under tests/evals/golden/ neither enforced nor allowlisted: "
        f"{sorted(all_agents - accounted)!r}; allowlist entrie(s) with no matching directory: "
        f"{sorted(accounted - all_agents)!r}"
    )
    blank = [agent for agent, reason in _AGENTS_NOT_YET_ENFORCED.items() if not reason.strip()]
    assert not blank, f"allowlist entry with a blank/empty justification: {blank!r}"


@pytest.mark.eval
@pytest.mark.parametrize("agent_id", _AGENTS_ENFORCED)
def test_golden_triggers_are_declared_by_agent_yaml(agent_id: str) -> None:
    """The reverse direction: a golden's `"triggers"` tag must name a trigger the agent's OWN
    `agent.yaml` actually declares -- catches a typo/stale tag (e.g. after an `agent.yaml` trigger
    is renamed or removed) that would otherwise silently under-report coverage forever."""
    declared = set(_canonical_triggers(agent_id))
    for case in load_golden(agent_id):
        unknown = [t for t in (case.get("triggers") or []) if t not in declared]
        assert not unknown, (
            f"{case['id']} tags trigger(s) {unknown!r} that {agent_id}'s agent.yaml does not "
            f"declare (declared={sorted(declared)!r}) -- stale tag or typo"
        )
