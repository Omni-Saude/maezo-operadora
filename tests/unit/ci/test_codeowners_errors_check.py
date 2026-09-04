"""Fence: the CODEOWNERS-resolution semaphore is a REAL red check, not an advisory printout.

WHY THIS FENCE EXISTS (gap MERGE-GATE-OWNER-REVIEW; owner decision R-051, 2026-09-04)
--------------------------------------------------------------------------------------
The owner dated a commitment: prerequisites (a) WRITE for `@Omni-Saude/security-team` on this repo,
(b) at least one human other than `rodaquino-OMNI` in that team, and (c) ZERO entries from
`GET /repos/{o}/{r}/codeowners/errors`, all within 10 calendar days (2026-09-14) — and only then
flip `require_code_owner_review: true`. The sentence the job exists to honour is "o estado do gate
passa a ser medido, nunca presumido".

(a) and (b) are acts of organization and staffing that no agent can perform or attest to. (c) is the
only one of the three a read-only API can measure, and — this is the part that makes measuring it
worth anything — it is the OBSERVABLE CONSEQUENCE of the other two rather than a fourth independent
requirement: every error this repository currently reports is an "Unknown owner" for the TEAM token,
and GitHub only stops reporting it once the team exists WITH write access here.

WHAT THIS FENCE PROTECTS. The failure mode being guarded is not "someone deletes the job" — that is
loud. It is the quiet kind: a `|| true`, a missing exit, or a `jq '.errors // [] | length'` that
reads a 404 body as zero errors and reports GREEN. The check would then be measuring nothing while
looking like it measures something, which is the exact species of the `require_code_owner_review`
finding it exists to prevent (a rule that exists on paper and gates nothing).

WHAT IT DELIBERATELY DOES NOT ASSERT. It makes no call to the API and no claim about today's error
count (29 when this was written). It asserts that the job, if run, cannot be green by accident.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "branch-protection-check.yml"
_FLIP_GATE = _REPO_ROOT / ".github" / "workflows" / "flip-path-review-gate.yml"

_JOB_ID = "codeowners-errors-check"


def _job() -> dict[str, Any]:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert _JOB_ID in workflow["jobs"], (
        f"job {_JOB_ID!r} is gone from {_WORKFLOW.name}. It is the measurement half of owner "
        "decision R-051; removing it returns the gate's state to being presumed."
    )
    return dict(workflow["jobs"][_JOB_ID])


def _script() -> str:
    steps = _job()["steps"]
    runs = [step["run"] for step in steps if "run" in step]
    assert len(runs) == 1, f"expected exactly one `run:` step in {_JOB_ID}, found {len(runs)}"
    return str(runs[0])


def test_the_job_reports_its_own_check_context() -> None:
    """A separate job, not a step inside `branch-protection-check`: two distinct governance facts
    must not collapse into one red context that cannot say which of them broke."""
    assert _job()["name"] == _JOB_ID
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert "branch-protection-check" in workflow["jobs"], "the sibling governance job vanished"


def test_it_queries_the_codeowners_errors_endpoint() -> None:
    assert "codeowners/errors" in _script(), (
        "the job no longer calls /codeowners/errors — there is nothing left to measure."
    )


def test_a_nonzero_error_count_is_red() -> None:
    """The `-eq 0` guard is what makes the count decide the verdict; anything else exits 1."""
    script = _script()
    assert re.search(r'\[ "\$error_count" -eq 0 \]', script), (
        "the green path must be gated on an error count of exactly zero"
    )
    assert "exit 0" in script and "exit 1" in script, "both verdicts must be reachable"


def test_the_verdict_is_never_swallowed() -> None:
    """`|| true`, a `continue-on-error`, or a missing exit would turn the gate into a printout."""
    script = _script()
    offenders = [line for line in script.splitlines() if "|| true" in line and "jq -r" not in line]
    assert offenders == [], f"swallowed failure(s) in the verdict path: {offenders}"
    assert _job().get("continue-on-error") in (None, False), (
        "`continue-on-error` would make this check advisory; R-051 asked for a red one."
    )
    for step in _job()["steps"]:
        assert step.get("continue-on-error") in (None, False)
        assert step.get("if") is None, (
            "an `if:` on the measuring step means the check can report nothing at all, and GitHub "
            "reads a never-reported required check as pending, not as red."
        )


def test_an_api_failure_is_red_rather_than_a_silent_zero() -> None:
    script = _script()
    assert re.search(r'\[ "\$api_status" -ne 0 \]', script), (
        "the API call's exit status must be checked; an unreachable API is a denial, not a clean read"
    )
    assert 'fail "GET /repos/${REPO}/codeowners/errors falhou' in script


def test_a_payload_without_an_errors_array_is_red_not_zero() -> None:
    """The fail-open shape this guards against is `jq '.errors // [] | length'`, which reads a 404
    body (`{"message": "Not Found"}`) as zero errors and reports GREEN."""
    script = _script()
    assert 'has("errors")' in script and 'type == "array"' in script, (
        "the script must PROVE the payload carries an `errors` array before counting it"
    )
    # Comment lines are excluded on purpose: the script's own comment QUOTES `.errors // [] | length`
    # as the anti-pattern it refuses, and a scan that cannot tell the warning from the offence would
    # punish the documentation.
    executable = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    assert not any("// []" in line for line in executable), (
        "a `// []` default on the errors field turns an unreadable response into a clean bill of "
        "health — the precise fail-open this job exists to avoid."
    )


def test_the_failure_message_names_the_three_owner_prerequisites() -> None:
    """A red check that does not say what would turn it green trains people to ignore it."""
    script = _script()
    for fragment in ("security-team", "rodaquino-OMNI", "require_code_owner_review", "2026-09-14"):
        assert fragment in script, f"the RED message no longer names {fragment!r}"


def test_it_does_not_run_on_pull_request_while_the_count_is_still_nonzero() -> None:
    """Deliberate, and the reason is in the workflow header: at a non-zero count a per-PR red check
    would block the very PRs that exist to satisfy the prerequisites. That is deadlock, not
    fail-closed — the same shape as an inert `require_code_owner_review`. Adding `pull_request`
    (or requiring the context) is the owner's act once the count reaches zero."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "pull_request" not in triggers, (
        "this workflow gained a `pull_request` trigger. That is only safe once "
        "/codeowners/errors reports zero; until then it deadlocks the queue. Remove it, or land it "
        "together with the evidence that the count is zero."
    )
    assert "schedule" in triggers and "workflow_dispatch" in triggers, (
        "the measurement must keep happening on its own — a governance fact that is only ever read "
        "when someone remembers to read it is the state R-051 was written to end."
    )


def test_the_flip_path_gate_is_left_untouched_by_this_job() -> None:
    """`flip-path-review-gate.yml` runs on every PR and its job name is a required-check candidate.
    Putting a currently-red probe inside it would redden every PR in the repository."""
    assert "codeowners/errors" not in _FLIP_GATE.read_text(encoding="utf-8")
