"""Unit tests for the production-promotion preflight gate (owner decision R-021, option B).

Three layers, mirroring `test_check_flip_path_review.py`:

1. **Pure-core** tests drive `parse_approvers`, `standing_reviews` and `decide` with fixture data —
   no network, no token, no `gh`.
2. **Shell** tests drive `main()` through a FAKE `urlopen`, so the HTTP layer itself (pagination,
   non-200 handling, payload normalization, and the order in which telemetry is read) is under test
   rather than stubbed away. Substituting `GitHubAPI` wholesale would leave exactly the code that
   turns an API answer into a verdict untested, and that is where a fail-open would hide.
3. **Real-tree** tests parse this repository's actual `.github/production-approvers.yaml` and read
   `.github/workflows/cd.yml`, so a passing gate cannot be passing because the real files drifted
   out from under it.

THE PROPERTY EVERY TEST HERE EXISTS TO DEFEND: the exit code is non-zero unless a specific,
currently-standing `APPROVED` review by a listed login, bound to the promoted commit, was proven.
Any test added to this file that asserts `main(...) == 0` must say, in words, which approval it
proved — otherwise it is asserting that the gate lets something through.
"""

from __future__ import annotations

import io
import json
import re
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_production_approval as gate
from scripts.ci.check_flip_path_review import owners_for_path, parse_codeowners
from scripts.ci.check_production_approval import (
    ApprovalGateError,
    ApproversFileError,
    PullRequest,
    Review,
    decide,
    load_approvers,
    parse_approvers,
    standing_reviews,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REAL_APPROVERS = _REPO_ROOT / ".github" / "production-approvers.yaml"
_GATE_SOURCE = _REPO_ROOT / "scripts" / "ci" / "check_production_approval.py"
_CD_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "cd.yml"

REPO = "Omni-Saude/maezo-operadora"
PROMOTED = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HEAD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
OLD_HEAD = "cccccccccccccccccccccccccccccccccccccccc"
LISTED = "rodaquino-OMNI"
UNLISTED = "some-other-human"


# =============================================================================================
# AMBIENT ENVIRONMENT ISOLATION — read before adding a test that calls `main()`.
#
# This gate reads the environment GitHub Actions exports around the pytest process. A test that
# leaves `$GITHUB_REPOSITORY` / `$GITHUB_SHA` in place therefore runs against the job's REAL commit
# in CI and against nothing on a laptop — the same CI-only divergence that turned PR #249 red for
# `check_flip_path_review.py`. So every test here starts from a scrubbed environment, autouse.
# =============================================================================================

_AMBIENT_GITHUB_ENV_VARS: tuple[str, ...] = (
    "GITHUB_REPOSITORY",
    "GITHUB_SHA",
    "GITHUB_STEP_SUMMARY",
    "GITHUB_TOKEN",
    "PROMOTION_IMAGE_TAG",
)


@pytest.fixture(autouse=True)
def _scrub_ambient_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _AMBIENT_GITHUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_the_ambient_env_scrub_list_covers_every_variable_the_script_reads() -> None:
    """Non-vacuity for the fixture above: the scrub cannot silently fall behind the script."""
    source = _GATE_SOURCE.read_text(encoding="utf-8")
    read = set(re.findall(r"""os\.environ(?:\.get)?[(\[]["']([A-Za-z0-9_]+)["']""", source))
    assert read, "found no os.environ reads at all — has the scan pattern rotted?"
    assert read <= set(_AMBIENT_GITHUB_ENV_VARS), (
        f"{sorted(read - set(_AMBIENT_GITHUB_ENV_VARS))} is read by the gate but not scrubbed. "
        "Add it, or this file means something different inside GitHub Actions than on a laptop."
    )


# =============================================================================================
# 1) Pure core — the approvers grammar
# =============================================================================================


def test_parses_the_minimal_accepted_document() -> None:
    assert parse_approvers("# a comment\n\napprovers:\n  - alice\n  - bob-2  # trailing note\n") == (
        "alice",
        "bob-2",
    )


@pytest.mark.parametrize(
    ("text", "because"),
    [
        ("approvers: [alice]\n", "flow sequence — a YAML shape the narrow grammar refuses"),
        ("approvers:\n  - alice\nother_key: 1\n", "a second top-level key"),
        ("approvers:\n  - alice\napprovers:\n  - bob\n", "two lists; which one is authoritative?"),
        ("approvers:\n- alice\n", "wrong indentation for the item"),
        ('approvers:\n  - "alice"\n', "quoted scalar"),
        ("approvers:\n  - alice\n  - ALICE\n", "the same login twice, differing only in case"),
        ("approvers:\n  - -alice\n", "leading hyphen is not a GitHub login"),
        ("approvers:\n  - ali ce\n", "a space inside the login"),
        ("  - alice\napprovers:\n", "an item before the key it populates"),
        ("# only comments\n", "no `approvers:` key at all"),
        ("approvers:\n", "an empty list — nobody could ever approve"),
        ("approvers:\n  - &anchor alice\n", "a YAML anchor"),
    ],
)
def test_every_shape_outside_the_narrow_grammar_is_a_hard_parse_error(text: str, because: str) -> None:
    """A parse error reaches `main` as a DENIAL — that is why refusing to parse is the safe answer."""
    with pytest.raises(ApproversFileError):
        parse_approvers(text)


def test_a_missing_approvers_file_is_a_denial_not_an_empty_list(tmp_path: Path) -> None:
    with pytest.raises(ApproversFileError, match="could not read"):
        load_approvers(tmp_path / "nope.yaml")


# =============================================================================================
# 1b) Pure core — standing reviews and the decision table
# =============================================================================================


def _review(login: str, state: str, commit: str, when: str, rid: int) -> Review:
    return Review(login=login, state=state, commit_id=commit, submitted_at=when, review_id=rid)


PR_AT_HEAD = PullRequest(number=42, head_sha=HEAD, state="closed", merged=True)


def test_a_dismissed_approval_stops_counting_immediately() -> None:
    reviews = [
        _review(LISTED, "APPROVED", HEAD, "2026-09-01T10:00:00+00:00", 1),
        _review(LISTED, "DISMISSED", HEAD, "2026-09-01T11:00:00+00:00", 2),
    ]
    assert standing_reviews(reviews) == {}
    verdict = decide(
        promotion_sha=PROMOTED,
        approvers=[LISTED],
        pull_requests=[PR_AT_HEAD],
        reviews_by_pr={42: reviews},
    )
    assert verdict.approved is False


def test_default_is_deny_when_no_pull_request_carries_the_commit() -> None:
    verdict = decide(promotion_sha=PROMOTED, approvers=[LISTED], pull_requests=[], reviews_by_pr={})
    assert verdict.approved is False
    assert "no pull request carries commit" in verdict.headline


def test_an_approval_by_an_unlisted_login_is_denied() -> None:
    verdict = decide(
        promotion_sha=PROMOTED,
        approvers=[LISTED],
        pull_requests=[PR_AT_HEAD],
        reviews_by_pr={42: [_review(UNLISTED, "APPROVED", HEAD, "2026-09-01T10:00:00+00:00", 1)]},
    )
    assert verdict.approved is False
    assert "is not listed in the approvers file" in verdict.render()


def test_an_approval_of_a_different_commit_is_denied_as_stale() -> None:
    """The whole point of property 3: an approval given before a later push does not carry over."""
    verdict = decide(
        promotion_sha=PROMOTED,
        approvers=[LISTED],
        pull_requests=[PR_AT_HEAD],
        reviews_by_pr={42: [_review(LISTED, "APPROVED", OLD_HEAD, "2026-09-01T10:00:00+00:00", 1)]},
    )
    assert verdict.approved is False
    assert "stale approval does not carry over" in verdict.render()


def test_a_listed_login_whose_standing_review_is_changes_requested_is_denied() -> None:
    verdict = decide(
        promotion_sha=PROMOTED,
        approvers=[LISTED],
        pull_requests=[PR_AT_HEAD],
        reviews_by_pr={
            42: [
                _review(LISTED, "APPROVED", HEAD, "2026-09-01T10:00:00+00:00", 1),
                _review(LISTED, "CHANGES_REQUESTED", HEAD, "2026-09-01T12:00:00+00:00", 2),
            ]
        },
    )
    assert verdict.approved is False
    assert "not APPROVED" in verdict.render()


def test_a_listed_login_approving_the_pull_requests_head_passes() -> None:
    """The ONE green path: @rodaquino-OMNI-shaped listed login, APPROVED, on the PR's current head."""
    verdict = decide(
        promotion_sha=PROMOTED,
        approvers=[LISTED],
        pull_requests=[PR_AT_HEAD],
        reviews_by_pr={42: [_review(LISTED.lower(), "APPROVED", HEAD, "2026-09-01T10:00:00+00:00", 7)]},
    )
    assert verdict.approved is True
    assert "review id 7" in verdict.render()


def test_an_unorderable_submitted_at_denies_rather_than_being_skipped() -> None:
    with pytest.raises(ApprovalGateError, match="unparseable submitted_at"):
        standing_reviews([_review(LISTED, "APPROVED", HEAD, "not-a-timestamp", 1)])


# =============================================================================================
# 2) Shell — `main()` over a fake `urlopen`
# =============================================================================================


class _FakeResponse(io.BytesIO):
    """Minimal stand-in for `http.client.HTTPResponse` as `urlopen` returns it."""

    def __init__(self, status: int, payload: Any) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _install_fake_api(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    *,
    statuses: dict[str, int] | None = None,
) -> list[str]:
    """Route `urlopen` by API path (query stripped). Returns the list of paths actually requested.

    An unrouted path raises `HTTPError(404)`, so a test that forgets to stub an endpoint sees the
    gate's own fail-closed behaviour instead of an accidental pass.
    """
    asked: list[str] = []
    statuses = statuses or {}

    def fake_urlopen(request: Any, timeout: int = 0) -> _FakeResponse:
        full = request.full_url if hasattr(request, "full_url") else str(request)
        path = full.split("https://api.github.com/", 1)[-1].split("?", 1)[0]
        asked.append(path)
        if path not in routes:
            raise urllib.error.HTTPError(full, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return _FakeResponse(statuses.get(path, 200), routes[path])

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    return asked


def _approvers_file(tmp_path: Path, *logins: str) -> Path:
    path = tmp_path / "production-approvers.yaml"
    path.write_text("approvers:\n" + "".join(f"  - {login}\n" for login in logins), encoding="utf-8")
    return path


def _routes(*, reviews: list[dict[str, Any]], pulls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        f"repos/{REPO}/commits/{PROMOTED}": {"sha": PROMOTED},
        f"repos/{REPO}/commits/{PROMOTED}/pulls": (
            pulls
            if pulls is not None
            else [
                {
                    "number": 42,
                    "state": "closed",
                    "merged_at": "2026-09-01T13:00:00Z",
                    "head": {"sha": HEAD},
                    "merge_commit_sha": PROMOTED,
                }
            ]
        ),
        f"repos/{REPO}/pulls/42/reviews": reviews,
        f"repos/{REPO}/environments/production": {
            "name": "production",
            "protection_rules": [],
            "can_admins_bypass": True,
        },
    }


def _api_review(
    login: str, state: str, commit: str, when: str = "2026-09-01T10:00:00Z", rid: int = 1
) -> dict[str, Any]:
    return {"id": rid, "user": {"login": login}, "state": state, "commit_id": commit, "submitted_at": when}


def _run(tmp_path: Path, *extra: str, approvers: tuple[str, ...] = (LISTED,)) -> int:
    return gate.main(
        [
            "--repo",
            REPO,
            "--sha",
            PROMOTED,
            "--approvers-file",
            str(_approvers_file(tmp_path, *approvers)),
            *extra,
        ]
    )


def test_main_denies_with_no_arguments_and_no_environment_at_all() -> None:
    """The default really is deny: nothing configured, nothing proven, exit 1."""
    assert gate.main([]) == 1


def test_main_denies_without_a_token(tmp_path: Path) -> None:
    assert _run(tmp_path) == 1


def test_main_denies_when_the_only_approval_is_from_an_unlisted_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    _install_fake_api(monkeypatch, _routes(reviews=[_api_review(UNLISTED, "APPROVED", HEAD)]))
    assert _run(tmp_path) == 1
    assert "RED" in capsys.readouterr().out


def test_main_denies_a_listed_approval_bound_to_a_different_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    _install_fake_api(monkeypatch, _routes(reviews=[_api_review(LISTED, "APPROVED", OLD_HEAD)]))
    assert _run(tmp_path) == 1
    assert "stale approval" in capsys.readouterr().out


def test_main_passes_only_on_a_listed_approval_of_the_promoted_commits_pull_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Proves: @rodaquino-OMNI (listed) APPROVED PR #42 at head `bbbb…`, whose merge commit is the
    promoted sha. That is the only approval this test asserts is sufficient."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    _install_fake_api(monkeypatch, _routes(reviews=[_api_review(LISTED, "APPROVED", HEAD, rid=7)]))
    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "GREEN" in out
    assert "review id 7" in out


def test_main_denies_when_the_reviews_endpoint_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An API error is a denial. A gate that cannot read the record has not read a clean record."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    routes = _routes(reviews=[])
    del routes[f"repos/{REPO}/pulls/42/reviews"]  # -> 404 from the fake transport
    _install_fake_api(monkeypatch, routes)
    assert _run(tmp_path) == 1
    assert "returned HTTP 404" in capsys.readouterr().out


def test_main_denies_when_the_promotion_ref_does_not_resolve_to_a_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An opaque image tag severs the approval-to-artifact binding, so it is refused outright."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    _install_fake_api(monkeypatch, _routes(reviews=[_api_review(LISTED, "APPROVED", HEAD)]))
    assert _run(tmp_path, "--image-tag", "latest") == 1
    assert "does not resolve to a commit" in capsys.readouterr().out


def test_main_ignores_a_pull_request_that_merely_contains_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/commits/{sha}/pulls` also lists PRs that contain the commit somewhere in their branch.
    Accepting those would let an approval of a bigger, unrelated PR authorize this promotion."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    _install_fake_api(
        monkeypatch,
        _routes(
            reviews=[_api_review(LISTED, "APPROVED", HEAD)],
            pulls=[{"number": 42, "state": "open", "head": {"sha": HEAD}, "merge_commit_sha": OLD_HEAD}],
        ),
    )
    assert _run(tmp_path) == 1


def test_the_environment_probe_cannot_turn_a_denial_into_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Property 2, proven adversarially: hand the gate an environment that claims a required-reviewer
    protection rule AND a denial-shaped review record. It must still exit 1 — the machine may not
    certify itself approved from its own environment config."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    routes = _routes(reviews=[_api_review(UNLISTED, "APPROVED", HEAD)])
    routes[f"repos/{REPO}/environments/production"] = {
        "name": "production",
        "protection_rules": [{"type": "required_reviewers", "reviewers": [{"type": "User"}]}],
        "can_admins_bypass": False,
    }
    _install_fake_api(monkeypatch, routes)
    assert _run(tmp_path) == 1
    assert "TELEMETRY ONLY" in capsys.readouterr().out


def test_the_environment_probe_is_read_after_the_verdict_and_never_blocks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telemetry that can fail the run is not telemetry: an unreachable environment endpoint must
    not turn a proven approval red, and must not be consulted before the verdict is fixed."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    routes = _routes(reviews=[_api_review(LISTED, "APPROVED", HEAD)])
    del routes[f"repos/{REPO}/environments/production"]  # -> 404 from the fake transport
    asked = _install_fake_api(monkeypatch, routes)
    assert _run(tmp_path) == 0
    env_path = f"repos/{REPO}/environments/production"
    # Both halves are load-bearing. `[-1]` alone is satisfied by a gate that ALSO probes the
    # environment first (measured: moving the probe above `decide` leaves it last as well), so the
    # count pins that it happens once, after the verdict — the only ordering in which the probe is
    # structurally incapable of informing the decision.
    assert asked.count(env_path) == 1, f"telemetry probed {asked.count(env_path)}x; got order {asked}"
    assert asked[-1] == env_path, f"telemetry must be the LAST call; got order {asked}"


def test_a_review_without_a_commit_id_denies_rather_than_being_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok-" + "q" * 28)
    review = _api_review(LISTED, "APPROVED", HEAD)
    del review["commit_id"]
    _install_fake_api(monkeypatch, _routes(reviews=[review]))
    assert _run(tmp_path) == 1
    assert "no commit_id" in capsys.readouterr().out


# =============================================================================================
# 3) Real tree — the files this gate actually reads in this repository
# =============================================================================================


def test_the_repositorys_own_approvers_file_parses_and_is_non_empty() -> None:
    """If the shipped file ever stops parsing, the gate denies EVERY promotion — loudly, by design,
    but the failure belongs here at unit time rather than at 3am during a promotion."""
    approvers = load_approvers(_REAL_APPROVERS)
    assert approvers, "the shipped approvers list is empty"
    assert all(re.fullmatch(gate._LOGIN_RE, login) for login in approvers)


def test_the_approvers_file_lives_under_a_codeowned_path() -> None:
    """Property 4: a reviewer list editable without review is a gate that can widen itself.

    RESOLVED, not assumed. The previous version of this test asserted that SOME `/.github/…` rule
    existed — which `/.github/CODEOWNERS` satisfies all by itself, while the approvers file was owned
    by nothing at all (adversarial-review finding, 2026-09-04). It now resolves the real path through
    this repository's own CODEOWNERS matcher, the same one `flip-path-review-gate` uses, so a green
    here means the merge-path gate would agree that this file is owned.

    The second assertion is the non-vacuity half: `.github/` is NOT blanket-owned here (its rules are
    enumerated), so an unnamed path under it must resolve to `None`. Without it, a matcher that
    matched everything would make the first assertion meaningless.
    """
    rules = parse_codeowners((_REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8"))
    relative = _REAL_APPROVERS.relative_to(_REPO_ROOT).as_posix()
    rule = owners_for_path(rules, relative)
    assert rule is not None, (
        f"{relative} is owned by no CODEOWNERS rule, so property 4 of the gate's docstring is false: "
        "the approvers list could be widened in an unreviewed PR. Restore the "
        "`/.github/production-approvers.yaml` rule."
    )
    assert owners_for_path(rules, ".github/ISSUE_TEMPLATE/bug_report.md") is None, (
        "a path under `.github/` that no rule names resolved to an owner — the assertion above has "
        "stopped discriminating (see tests/unit/ci/test_codeowners_rules.py for the same anchor)."
    )


def test_cd_yml_wires_the_gate_ahead_of_the_production_promotion() -> None:
    """The script is only a gate if the workflow actually depends on it. Reverting either half of
    the wiring (the job, or `promote-production`'s `needs:`) turns this test RED."""
    workflow = _CD_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/ci/check_production_approval.py" in workflow
    assert re.search(r"^  require-production-approval:$", workflow, re.MULTILINE)
    promote = workflow.split("\n  promote-production:\n", 1)
    assert len(promote) == 2, "job `promote-production` not found in cd.yml"
    assert re.search(r"^    needs: \[require-production-approval\]$", promote[1], re.MULTILINE)


def test_cd_yml_no_longer_claims_a_required_reviewer_gate_that_does_not_exist() -> None:
    """R-020: the three passages that asserted GitHub manual-approval reviewers are gone. The live
    environment carries `protection_rules: []` (org on the *team* plan; required reviewers 422)."""
    workflow = _CD_WORKFLOW.read_text(encoding="utf-8")
    for false_claim in (
        "gates\n# on GitHub's manual-approval reviewers",
        "gated by GitHub's\n  #    `environment: production` manual-approval reviewers",
        "configure required reviewers on the",
    ):
        assert false_claim not in workflow, f"cd.yml still claims a gate that does not exist: {false_claim!r}"
    assert "protection_rules: []" in workflow, "cd.yml must state the environment's REAL configuration"
