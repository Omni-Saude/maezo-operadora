"""Fence: the OSV dependency lane's fail threshold, and the fail-closed contract of its allowlist.

WHY THIS FENCE EXISTS (gap D6-05; owner decision R-042, 2026-09-04)
-------------------------------------------------------------------
`.github/workflows/security.yml` ran its OSV lane at `OSV_FAIL_ON_SEVERITY: "CRITICAL"`, which was
originally recorded as a deliberate tech-lead preference ("failing on HIGH adds friction"). The
premise that justified it — "the other lane is already stricter" — does not hold: the
`dependency-review` job that uses `fail-on-severity: high` sits behind the GHAS probe, and the same
file says in its own words that the step "has never actually executed" because
`code_security.status='disabled'`. So the OSV lane is the ONLY LIVE dependency gate in this
repository, and it was at its loosest setting: every HIGH went to a job summary nobody is obliged
to read, which is fail-open by construction rather than a neutral preference.

The owner's decision was to lower it to HIGH in the same change that enumerates and dispositions the
pre-existing HIGH findings. Enumerated at the time with osv-scanner v2.4.0 — the pinned version and
the exact command the job runs — over this repository's `uv.lock`: 122 packages, ZERO findings at any
severity, so no allowlist entry was needed and the allowlist stayed empty. (The 2 HIGHs recorded
historically in `docs/evidence-ledger.md`'s T2.4 row, against langgraph 0.6.11 and
langgraph-checkpoint 2.1.2, are gone because both dependencies were upgraded since — langgraph 1.2.9
and langgraph-checkpoint 4.1.1 in the lockfile this fence reads.)

WHAT THIS FENCE PROTECTS. A threshold is one word in a YAML file. Loosening it back is a smaller,
quieter edit than any of the reasoning above, and the resulting job still reports GREEN — that is
precisely the "continues green, only blind" failure mode `.github/CODEOWNERS` describes for the
suppression surfaces. Pinning it here makes the relaxation loud.

WHAT IT DELIBERATELY DOES NOT DO. It does not run osv-scanner (no network in the unit lane) and
therefore makes no claim about today's advisory database. It asserts configuration, not absence of
vulnerabilities.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SECURITY_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "security.yml"
_ALLOWLIST = _REPO_ROOT / ".github" / "osv-allowlist.json"

_OSV_JOB = "osv-scan"
_GATE_STEP_PREFIX = "Gate on severity"

#: The owner-decided threshold (R-042). Changing it is a decision, not a refactor: update this
#: constant IN THE SAME CHANGE that records who decided and why, or leave it alone.
_DECIDED_THRESHOLD = "HIGH"

#: Severities strictly below the threshold, ordered as the gate script orders them. Used to prove
#: the pin is not vacuously satisfied by any string at all.
_BELOW_THRESHOLD = ("UNKNOWN", "LOW", "MEDIUM")


def _gate_step() -> dict[str, Any]:
    workflow = yaml.safe_load(_SECURITY_WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"][_OSV_JOB]["steps"]:
        if (step.get("name") or "").startswith(_GATE_STEP_PREFIX):
            return step
    raise AssertionError(
        f"no step named {_GATE_STEP_PREFIX!r} in job {_OSV_JOB!r} — has security.yml been restructured?"
    )


def test_the_osv_lane_fails_at_the_owner_decided_threshold() -> None:
    threshold = _gate_step()["env"]["OSV_FAIL_ON_SEVERITY"]
    assert threshold == _DECIDED_THRESHOLD, (
        f"the OSV lane's threshold is {threshold!r}, not the owner-decided {_DECIDED_THRESHOLD!r} "
        "(R-042 / D6-05). This lane is the only LIVE dependency gate in the repo — the "
        "`dependency-review` job behind the GHAS probe has never executed. Loosening this is a "
        "decision that needs its own record, not a silent edit."
    )
    assert threshold not in _BELOW_THRESHOLD, "the pin must not accept a below-threshold severity"


def test_the_scripts_fallback_threshold_is_not_looser_than_the_step_sets() -> None:
    """If the `env:` block is ever dropped, the fallback inside the gate script is what runs. A
    fallback of CRITICAL would silently restore the old, fail-open behaviour."""
    run = _gate_step()["run"]
    match = re.search(r'os\.environ\.get\("OSV_FAIL_ON_SEVERITY",\s*"([A-Z]+)"\)', run)
    assert match, "could not find the fallback threshold in the gate script — has it been rewritten?"
    order = ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert order.index(match.group(1)) <= order.index(_DECIDED_THRESHOLD), (
        f"the script's fallback threshold {match.group(1)!r} is LOOSER than the decided "
        f"{_DECIDED_THRESHOLD!r}; a dropped env var must never relax the gate."
    )


def test_the_allowlist_is_well_formed_and_every_entry_carries_a_reason() -> None:
    """The owner's floor for R-042: "cada HIGH silenciado exige `reason` explicito na allowlist".

    The workflow's own gate step enforces this at CI time and fails closed on a malformed entry; this
    asserts the same contract at unit time, where the failure is cheap to read. It is NOT a claim
    that the list should stay empty — it is a claim that nothing gets silenced anonymously."""
    doc = json.loads(_ALLOWLIST.read_text(encoding="utf-8"))
    assert isinstance(doc, dict) and isinstance(doc.get("entries"), list), (
        "the allowlist must be an object with a top-level `entries` list — the workflow's gate "
        "step fails closed on anything else."
    )
    for index, entry in enumerate(doc["entries"]):
        assert isinstance(entry, dict), f"allowlist entry #{index} is not an object"
        assert isinstance(entry.get("id"), str) and entry["id"].strip(), (
            f"allowlist entry #{index} has no non-empty `id`"
        )
        assert isinstance(entry.get("reason"), str) and entry["reason"].strip(), (
            f"allowlist entry #{index} (id={entry.get('id')!r}) silences a finding with no written "
            "justification. Every suppression carries its reason, or it is not a suppression, it is "
            "a blind spot."
        )


@pytest.mark.parametrize("marker", ["osv-results.json", "--lockfile=uv.lock"])
def test_the_lane_still_scans_the_real_lockfile(marker: str) -> None:
    """Non-vacuity: a threshold pinned on a lane that scans nothing would prove nothing."""
    workflow = _SECURITY_WORKFLOW.read_text(encoding="utf-8")
    assert marker in workflow, f"{marker!r} vanished from security.yml — is the OSV lane still real?"
    assert (_REPO_ROOT / "uv.lock").exists(), "uv.lock is gone; the scanned artifact must exist"
