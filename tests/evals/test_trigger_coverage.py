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
`- intent: <name>` entries). A case may tag zero,
one, or several triggers (e.g. EVL-VALENTINA-04 tags BOTH `dmn:programa_routing=ANALISE_HUMANA`
and `signal:ambiguity` -- the two agent.yaml lines are, honestly, the SAME `assess()` branch:
`elegivel not in ("ELEGIVEL", "NAO_ELEGIVEL")`). Absent entirely on a case (every pre-GOLDENS-
MISSING golden that this WP did not itself author or retroactively tag) means "proves no
declared trigger by itself" -- a CE/PL/GC case pinning the neutral/happy path, or a bespoke
FHIR-leak/audit-fence proof, is not thereby uncovered-trigger evidence.

SCOPE -- THE WHOLE FLEET (R-043/F-184, owner decision 2026-10-31 in #1084: "estender o
enforcement aos 4 agentes"). GOLDENS-MISSING (2026-09-05) enforced 6 agents (andre/beatriz/
fernando/gustavo/marina/valentina) and parked carolina/helena/lucas/rafael in an allowlist,
each with a written reason. R-043 performed the same manual trigger-to-branch trace for those 4
(module docstrings + `assess()`/`receive()`/`classify()` reading, not grep) and closed the
allowlist: every agent directory under `tests/evals/golden/` is now enforced, and the allowlist
no longer exists -- a NEW agent directory that is not enforced fails
`test_every_golden_agent_is_enforced` instead of being parked again by omission.

What the 4-agent trace found (each one is in the golden's own `description` or in the PR body):

* carolina -- 8/8 already had a behavioral golden (EVL-CAROLINA-02/03/05/06/07/08, the
  CAROLINA-CATCHALL branch the old allowlist was waiting for has merged); only the `triggers`
  tags were missing.
* rafael -- `dmn: auth_admissibility=PENDENTE_DOCUMENTACAO` had NO golden at all: EVL-RAFAEL-07
  is new (rule-faithful `__rules__` fixture, so only the input the graph really sends matches
  r4). `signal: ambiguity` has no code path of its own: in `RafaelGraph.assess` it IS the
  `auth_auto_approval` catch-all (`r99` -> ANALISE_HUMANA when the facts are not verified), so
  EVL-RAFAEL-02 (the rule-faithful catch-all case) tags both lines, as EVL-VALENTINA-04 does.
* lucas -- `signal: negociacao` had NO golden (the Rodada-12 `pedido_negociacao` branch):
  EVL-LUCAS-08 is new and also carries `dmn: lucas_escalation_routing=ATENDIMENTO_HUMANO`
  (asserting `roteamento_escalacao`, which EVL-LUCAS-07 does not -- its group coincides with the
  fail-safe group, so it could not prove the DMN outcome).
* helena -- `intent: scheduling` was a DECLARED TRIGGER THAT NO LONGER ESCALATES: DL-0052
  (01/10/2026) made scheduling an out-of-channel fixed reply with no SP-OP-ESCALATION-001 start,
  and the line's own comment already said "NAO escala mais". Tagging EVL-HELENA-08 (which proves
  the ABSENCE of escalation) would have been a false green, so the stale line was removed from
  `escalation.triggers` in `spec/agents/helena/agent.yaml` (the fix lives in the agent spec, not
  in this fence). The other 5 helena triggers were mapped to the graph branch and tagged.
"""

from __future__ import annotations

import pytest

from maezo.agents import AgentLoader

from .conftest import GOLDEN_ROOT, load_golden

#: EVERY agent with a golden directory. Every entry here MUST have zero uncovered triggers
#: (`test_every_declared_trigger_has_a_golden` is parametrized over exactly this tuple), and
#: `test_every_golden_agent_is_enforced` fails if a golden directory is missing from it.
_AGENTS_ENFORCED: tuple[str, ...] = (
    "andre",
    "beatriz",
    "carolina",
    "fernando",
    "gustavo",
    "helena",
    "lucas",
    "marina",
    "rafael",
    "valentina",
)


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
def test_every_golden_agent_is_enforced() -> None:
    """Every agent directory under `tests/evals/golden/` is enforced above -- there is no
    allowlist any more (R-043/F-184). A 11th agent added to the fleet later is RED here until
    its declared triggers are traced and tagged, never parked by omission."""
    all_agents = {p.name for p in GOLDEN_ROOT.iterdir() if p.is_dir()}
    assert all_agents == set(_AGENTS_ENFORCED), (
        f"golden agent(s) not enforced: {sorted(all_agents - set(_AGENTS_ENFORCED))!r}; "
        f"enforced entrie(s) with no golden directory: {sorted(set(_AGENTS_ENFORCED) - all_agents)!r}"
    )


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
