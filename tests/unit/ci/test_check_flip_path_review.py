"""Unit tests for the flip-path CODEOWNERS review gate (owner decision Q-1).

Three layers, mirroring `test_check_start_process_fence.py`:

1. **Pure-core** tests drive `parse_codeowners` / `owners_for_path` / `standing_reviews` / `decide`
   with fixture payloads and an injected membership resolver — no network, no `gh`, no tokens.
2. **Shell** tests drive `resolve_codeowners`, `load_event_context`, `GitHubAPI.membership` and
   `main()` against a fake API and fixture Actions event payloads, so the wiring that decides WHICH
   commit's CODEOWNERS is read is itself under test (that is the un-own-yourself hole).
3. **Real-tree** tests parse this repo's actual `.github/CODEOWNERS` and assert the parser covers
   every pattern shape it really contains — a passing gate must not be passing because it silently
   understood nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_flip_path_review as gate
from scripts.ci.check_flip_path_review import (
    CODEOWNERS_CANDIDATE_PATHS,
    CodeownersParseError,
    GateError,
    MembershipState,
    OwnerKind,
    Review,
    decide,
    load_event_context,
    owners_for_path,
    parse_codeowners,
    resolve_codeowners,
    standing_reviews,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REAL_CODEOWNERS = _REPO_ROOT / ".github" / "CODEOWNERS"

AUTHOR = "flip-author"
OWNER_USER = "rodrigotaquino"
OTHER_OWNER = "second-owner"
OUTSIDER = "random-contributor"

#: A miniature CODEOWNERS in the same shape as the real one (rooted dirs + rooted exact files),
#: plus a second, disjointly-owned area so the per-path-coverage semantics can be exercised.
FIXTURE_CODEOWNERS = f"""\
# comment line, ignored
/spec/policies/autonomy/  @{OWNER_USER} @Omni-Saude/security
/spec/policies/autonomy/action-approvals.yaml  @{OWNER_USER} @Omni-Saude/security
/docs/adr/  @{OWNER_USER}
/src/other/  @{OTHER_OWNER}
"""

#: Team-only ownership: no explicit user owner at all, so the ONLY route to green is a verified
#: active team member.
TEAM_ONLY_CODEOWNERS = "/spec/policies/autonomy/  @Omni-Saude/security\n"


def all_unverifiable(org: str, team: str, login: str) -> MembershipState:
    """Membership resolver standing in for a token that cannot read the org's team rosters."""
    return MembershipState.UNVERIFIABLE


def nobody_is_a_member(org: str, team: str, login: str) -> MembershipState:
    return MembershipState.NOT_MEMBER


def members(*logins: str) -> gate.MembershipResolver:
    """Resolver where exactly `logins` are ACTIVE members of any team; everyone else is a clean no."""
    lowered = {login.lower() for login in logins}

    def resolve(org: str, team: str, login: str) -> MembershipState:
        return MembershipState.ACTIVE if login.lower() in lowered else MembershipState.NOT_MEMBER

    return resolve


def approval(login: str, *, when: str = "2026-08-13T10:00:00Z", review_id: int = 1) -> Review:
    return Review(login=login, state="APPROVED", submitted_at=when, review_id=review_id)


def run(
    *,
    codeowners: str = FIXTURE_CODEOWNERS,
    changed: tuple[str, ...],
    reviews: tuple[Review, ...] = (),
    author: str = AUTHOR,
    membership: gate.MembershipResolver | None = None,
    single_reviewer: bool = False,
) -> gate.Decision:
    return decide(
        codeowners_text=codeowners,
        codeowners_source="fixture",
        changed_paths=changed,
        reviews=reviews,
        author_login=author,
        membership=membership if membership is not None else nobody_is_a_member,
        require_single_reviewer_covers_all=single_reviewer,
    )


# =============================================================================================
# 1. The core contract: the scenario table
# =============================================================================================


def test_unowned_only_pr_is_green_instantly() -> None:
    """A PR touching no owned path passes without needing any review at all."""
    decision = run(changed=("README.md", "src/maezo/runtime/worker.py", "tests/unit/x.py"))
    assert decision.ok, decision.render()
    assert "no CODEOWNERS-owned path touched" in decision.headline


def test_owned_path_with_no_review_is_red() -> None:
    decision = run(changed=("spec/policies/autonomy/matrix.yaml",))
    assert not decision.ok
    rendered = decision.render()
    assert "lack a qualified owner approval" in rendered
    assert "spec/policies/autonomy/matrix.yaml" in rendered
    # legibility: it must name the owner who could unblock it
    assert f"@{OWNER_USER}" in rendered
    assert "What unblocks this check" in rendered


def test_owned_path_with_author_self_approval_is_red() -> None:
    """A self-approval never qualifies, at any tier — this is the headline bypass."""
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(AUTHOR),),
    )
    assert not decision.ok
    assert "IS THE PR AUTHOR — self-approval" in decision.render()


def test_owned_path_with_qualified_user_owner_approval_is_green() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml", "README.md"),
        reviews=(approval(OWNER_USER),),
    )
    assert decision.ok, decision.render()
    assert "carry a qualified owner approval" in decision.headline


def test_owner_approval_matching_is_case_insensitive_like_github_logins() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(OWNER_USER.upper()),),
    )
    assert decision.ok, decision.render()


def test_non_owner_approval_never_satisfies_an_owned_path() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(OUTSIDER),),
    )
    assert not decision.ok
    assert "does not own this path" in decision.render()


# ---- team ownership: the fail-closed hierarchy ----------------------------------------------


def test_team_owned_path_with_unverifiable_membership_is_red_and_names_the_opacity() -> None:
    """The core anti-downgrade property: opacity is RED, never 'any non-author approval will do'."""
    decision = run(
        codeowners=TEAM_ONLY_CODEOWNERS,
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(OUTSIDER),),
        membership=all_unverifiable,
    )
    assert not decision.ok
    rendered = decision.render()
    assert "UNVERIFIABLE" in rendered
    assert "@Omni-Saude/security" in rendered
    assert "OPACITY NOTE" in rendered
    # the documented manual override must be stated
    assert "explicit @user owner" in rendered or "explicit user owner" in rendered


def test_team_owned_path_with_verified_active_member_is_green() -> None:
    decision = run(
        codeowners=TEAM_ONLY_CODEOWNERS,
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(OUTSIDER),),
        membership=members(OUTSIDER),
    )
    assert decision.ok, decision.render()


def test_team_membership_pending_is_not_membership() -> None:
    """A `pending` membership is an unaccepted invitation, not a member."""

    def pending(org: str, team: str, login: str) -> MembershipState:
        return MembershipState.PENDING

    decision = run(
        codeowners=TEAM_ONLY_CODEOWNERS,
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(OUTSIDER),),
        membership=pending,
    )
    assert not decision.ok
    assert "pending-invitation" in decision.render()


def test_team_verified_member_who_is_the_author_still_cannot_self_approve() -> None:
    """Author-exclusion outranks every ownership tier, including a verified team membership."""
    decision = run(
        codeowners=TEAM_ONLY_CODEOWNERS,
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(AUTHOR),),
        membership=members(AUTHOR),
    )
    assert not decision.ok
    assert "IS THE PR AUTHOR" in decision.render()


def test_unverifiable_team_does_not_block_a_path_a_user_owner_already_approved() -> None:
    """Opacity reddens only the paths it is load-bearing for — a user owner's approval still counts."""
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(OWNER_USER),),
        membership=all_unverifiable,
    )
    assert decision.ok, decision.render()


def test_email_owner_is_parsed_but_never_satisfiable() -> None:
    """CODEOWNERS permits `user@example.com`; a review payload has no email, so it can never match."""
    decision = run(
        codeowners="/spec/policies/autonomy/  compliance@example.com\n",
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(approval(OUTSIDER),),
    )
    assert not decision.ok
    rendered = decision.render()
    assert "email owner" in rendered
    assert "OPACITY NOTE" in rendered


def test_email_owner_token_parses_as_email_kind() -> None:
    rules = parse_codeowners("/x/  compliance@example.com\n")
    assert rules[0].owners[0].kind is OwnerKind.EMAIL


# ---- review lifecycle ------------------------------------------------------------------------


def test_dismissed_approval_is_red() -> None:
    """A dismissed approval must re-redden — the reason the workflow also fires on `dismissed`."""
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(Review(OWNER_USER, "DISMISSED", "2026-08-13T10:00:00Z", 1),),
    )
    assert not decision.ok
    rendered = decision.render()
    assert "a dismissed review never counts" in rendered


def test_approval_then_dismissal_of_that_same_approval_is_red() -> None:
    """The realistic shape: the reviews list carries the approval, later re-stated as DISMISSED."""
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            approval(OWNER_USER, when="2026-08-13T10:00:00Z", review_id=1),
            Review(OWNER_USER, "DISMISSED", "2026-08-13T11:00:00Z", 2),
        ),
    )
    assert not decision.ok


def test_dismissal_then_fresh_approval_from_the_same_reviewer_is_green() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            Review(OWNER_USER, "DISMISSED", "2026-08-13T10:00:00Z", 1),
            approval(OWNER_USER, when="2026-08-13T11:00:00Z", review_id=2),
        ),
    )
    assert decision.ok, decision.render()


def test_commented_and_pending_reviews_never_count_as_approval() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            Review(OWNER_USER, "COMMENTED", "2026-08-13T10:00:00Z", 1),
            Review(OWNER_USER, "PENDING", "2026-08-13T11:00:00Z", 2),
        ),
    )
    assert not decision.ok


def test_standing_reviews_keeps_only_the_latest_per_reviewer() -> None:
    latest = standing_reviews(
        [
            Review(OWNER_USER, "CHANGES_REQUESTED", "2026-08-13T10:00:00Z", 1),
            Review(OWNER_USER, "APPROVED", "2026-08-13T12:00:00Z", 3),
            Review(OUTSIDER, "APPROVED", "2026-08-13T11:00:00Z", 2),
        ]
    )
    assert latest[OWNER_USER.lower()].state == "APPROVED"
    assert set(latest) == {OWNER_USER.lower(), OUTSIDER.lower()}


def test_reviews_with_an_unparseable_timestamp_fail_closed() -> None:
    with pytest.raises(GateError, match="unparseable submitted_at"):
        standing_reviews([Review(OWNER_USER, "APPROVED", "not-a-date", 1)])


# ---- Decision 2: CHANGES_REQUESTED ------------------------------------------------------------


def test_changes_requested_from_owner_reddens_despite_other_owner_approval() -> None:
    """THE RULING: a live owner objection outranks another owner's approval (see Decision 2)."""
    decision = run(
        codeowners=f"/spec/policies/autonomy/  @{OWNER_USER} @{OTHER_OWNER}\n",
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            approval(OTHER_OWNER, when="2026-08-13T12:00:00Z", review_id=2),
            Review(OWNER_USER, "CHANGES_REQUESTED", "2026-08-13T10:00:00Z", 1),
        ),
    )
    assert not decision.ok
    rendered = decision.render()
    assert "has requested changes" in decision.headline
    assert "outranks any approval" in rendered
    assert "dismisses their review" in rendered


def test_changes_requested_from_non_owner_does_not_redden() -> None:
    """Scoped to owners of a TOUCHED OWNED path — a non-owner cannot redden what they do not own."""
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            approval(OWNER_USER),
            Review(OUTSIDER, "CHANGES_REQUESTED", "2026-08-13T11:00:00Z", 2),
        ),
    )
    assert decision.ok, decision.render()


def test_changes_requested_superseded_by_later_approval_from_same_reviewer_is_green() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            Review(OWNER_USER, "CHANGES_REQUESTED", "2026-08-13T10:00:00Z", 1),
            approval(OWNER_USER, when="2026-08-13T12:00:00Z", review_id=2),
        ),
    )
    assert decision.ok, decision.render()


def test_changes_requested_from_a_possible_team_owner_blocks_when_membership_is_opaque() -> None:
    """Both directions of 'we cannot verify' resolve toward RED — documented in Decision 2."""
    decision = run(
        codeowners=f"/spec/policies/autonomy/  @{OWNER_USER} @Omni-Saude/security\n",
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            approval(OWNER_USER),
            Review(OUTSIDER, "CHANGES_REQUESTED", "2026-08-13T11:00:00Z", 2),
        ),
        membership=all_unverifiable,
    )
    assert not decision.ok
    assert "MAY own" in decision.render()


# ---- Decision 3: per-path coverage vs single-reviewer ------------------------------------------


def test_two_owners_covering_disjoint_paths_is_green_by_default() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml", "src/other/thing.py"),
        reviews=(
            approval(OWNER_USER, review_id=1),
            approval(OTHER_OWNER, review_id=2),
        ),
    )
    assert decision.ok, decision.render()


def test_two_owners_covering_disjoint_paths_is_red_under_single_reviewer_mode() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml", "src/other/thing.py"),
        reviews=(
            approval(OWNER_USER, review_id=1),
            approval(OTHER_OWNER, review_id=2),
        ),
        single_reviewer=True,
    )
    assert not decision.ok
    assert "no single qualified reviewer covers every touched owned path" in decision.headline


def test_one_of_two_disjointly_owned_paths_approved_is_red_even_by_default() -> None:
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml", "src/other/thing.py"),
        reviews=(approval(OWNER_USER),),
    )
    assert not decision.ok
    assert "1 of 2 touched owned path(s)" in decision.headline


# =============================================================================================
# 2. CODEOWNERS parsing
# =============================================================================================


def test_last_matching_pattern_wins() -> None:
    """GitHub precedence. The real file relies on this twice and says so in its own comments."""
    rules = parse_codeowners(
        "/spec/processes/dmn/  @broad-owner\n/spec/processes/dmn/adequacao_gap.dmn  @narrow-owner\n"
    )
    narrow = owners_for_path(rules, "spec/processes/dmn/adequacao_gap.dmn")
    broad = owners_for_path(rules, "spec/processes/dmn/other.dmn")
    assert narrow is not None and [o.raw for o in narrow.owners] == ["@narrow-owner"]
    assert broad is not None and [o.raw for o in broad.owners] == ["@broad-owner"]


def test_last_match_wins_even_when_the_narrower_rule_comes_first() -> None:
    """Order, not specificity — a later BROAD line really does override an earlier narrow one."""
    rules = parse_codeowners(
        "/spec/processes/dmn/adequacao_gap.dmn  @narrow-owner\n/spec/processes/dmn/  @broad-owner\n"
    )
    winner = owners_for_path(rules, "spec/processes/dmn/adequacao_gap.dmn")
    assert winner is not None and [o.raw for o in winner.owners] == ["@broad-owner"]


def test_last_matching_rule_with_no_owners_unsets_ownership() -> None:
    rules = parse_codeowners("/spec/policies/  @an-owner\n/spec/policies/public/\n")
    assert owners_for_path(rules, "spec/policies/x.yaml") is not None
    assert owners_for_path(rules, "spec/policies/public/x.yaml") is None


def test_unowned_path_yields_no_rule() -> None:
    rules = parse_codeowners(FIXTURE_CODEOWNERS)
    assert owners_for_path(rules, "README.md") is None


@pytest.mark.parametrize(
    "pattern",
    [
        "/spec/**/*.dmn",  # `?` is fine to omit; this one is legal and must PARSE
    ],
)
def test_supported_glob_shapes_parse(pattern: str) -> None:
    assert parse_codeowners(f"{pattern}  @x\n")


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        # rooted directory — the dominant real shape
        ("/spec/policies/autonomy/", "spec/policies/autonomy/x.yaml", True),
        ("/spec/policies/autonomy/", "spec/policies/autonomy/deep/x.yaml", True),
        ("/spec/policies/autonomy/", "other/spec/policies/autonomy/x.yaml", False),
        ("/spec/policies/autonomy/", "spec/policies/autonomyx/x.yaml", False),
        # rooted exact file — the other real shape
        ("/spec/x.dmn", "spec/x.dmn", True),
        ("/spec/x.dmn", "spec/x.dmnn", False),
        ("/spec/x.dmn", "other/spec/x.dmn", False),
        # single-segment wildcard
        ("/spec/*/x.yaml", "spec/a/x.yaml", True),
        ("/spec/*/x.yaml", "spec/a/b/x.yaml", False),
        # `**` across segments
        ("/spec/**/x.yaml", "spec/a/b/x.yaml", True),
        ("/spec/**/x.yaml", "spec/x.yaml", True),
        # unrooted: matches at ANY depth (git semantics)
        ("docs/", "a/b/docs/x.md", True),
        ("docs/", "docs/x.md", True),
        ("*.dmn", "spec/processes/dmn/a.dmn", True),
        ("*.dmn", "spec/processes/dmn/a.yaml", False),
        # the global default-owner pattern
        ("*", "anything/at/all.txt", True),
    ],
)
def test_pattern_matching_semantics(pattern: str, path: str, expected: bool) -> None:
    rules = parse_codeowners(f"{pattern}  @x\n")
    assert (owners_for_path(rules, path) is not None) is expected


@pytest.mark.parametrize(
    "pattern",
    [
        "/spec/policies/?.yaml",  # `?` glob — not implemented
        "/spec/[abc]/x.yaml",  # character class — not implemented
        "!/spec/policies/",  # negation — CODEOWNERS has none, and silently ignoring it is fatal
        "/spec/pol\\ icies/",  # backslash escape — not implemented
        "/spec/***/x",  # meaningless
        "/spec/policies/(x)/",  # stray regex metacharacters
    ],
)
def test_unrecognized_pattern_shape_is_a_hard_parse_error(pattern: str) -> None:
    """Fail-closed: never skip a pattern this parser cannot evaluate — that hides an owned path."""
    with pytest.raises(CodeownersParseError):
        parse_codeowners(f"{pattern}  @x\n")


def test_unrecognized_pattern_reddens_the_whole_decision_not_just_that_line() -> None:
    decision = run(
        codeowners="/spec/policies/autonomy/  @rodrigotaquino\n/spec/[abc]/x.yaml  @someone\n",
        changed=("README.md",),  # would otherwise be an instant green
        reviews=(),
    )
    assert not decision.ok
    rendered = decision.render()
    assert "CODEOWNERS could not be parsed" in rendered
    assert "fail-closed" in rendered


@pytest.mark.parametrize("token", ["rodrigotaquino", "@", "@/team", "@@x", "not-an-owner"])
def test_unrecognized_owner_token_is_a_hard_parse_error(token: str) -> None:
    with pytest.raises(CodeownersParseError):
        parse_codeowners(f"/spec/x.yaml  {token}\n")


def test_comments_and_blank_lines_are_skipped() -> None:
    rules = parse_codeowners("# just a comment\n\n   \n/spec/x.yaml  @x  # trailing comment\n")
    assert len(rules) == 1
    assert [o.raw for o in rules[0].owners] == ["@x"]


# =============================================================================================
# 3. The shell: base-ref sourcing, event payloads, API classification
# =============================================================================================

BASE_SHA = "0000000000000000000000000000000000000ba5e"
HEAD_SHA = "1111111111111111111111111111111111111head"

#: What the base commit says. Owns the flip path.
BASE_CODEOWNERS = f"/spec/policies/autonomy/  @{OWNER_USER}\n"
#: What a malicious PR's HEAD says: the ownership line deleted, so the PR un-owns its own payload.
HEAD_CODEOWNERS = "# (ownership line deleted by this very PR)\n"


class FakeAPI:
    """Stands in for `GitHubAPI`: serves per-ref file content plus fixed files/reviews."""

    def __init__(
        self,
        *,
        files_by_ref: dict[str, dict[str, str]],
        changed: list[str],
        reviews: list[Review],
        membership_state: MembershipState = MembershipState.NOT_MEMBER,
    ) -> None:
        self.files_by_ref = files_by_ref
        self._changed = changed
        self._reviews = reviews
        self._membership_state = membership_state
        self.refs_asked: list[str] = []

    def file_at(self, ref: str, path: str) -> str | None:
        self.refs_asked.append(ref)
        return self.files_by_ref.get(ref, {}).get(path)

    def changed_paths(self, pr_number: int) -> list[str]:
        return self._changed

    def reviews(self, pr_number: int) -> list[Review]:
        return self._reviews

    def membership(self, org: str, team: str, login: str) -> MembershipState:
        return self._membership_state


def write_event(tmp_path: Path, **overrides: Any) -> Path:
    """A realistic `pull_request` / `pull_request_review` Actions event payload fixture."""
    payload: dict[str, Any] = {
        "action": "submitted",
        "repository": {"full_name": "Omni-Saude/maezo-operadora"},
        "pull_request": {
            "number": 244,
            "user": {"login": AUTHOR},
            "base": {"sha": BASE_SHA, "ref": "main"},
            "head": {"sha": HEAD_SHA, "ref": "flip-branch"},
        },
    }
    payload.update(overrides)
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_codeowners_is_read_from_base_not_head() -> None:
    api = FakeAPI(
        files_by_ref={
            BASE_SHA: {".github/CODEOWNERS": BASE_CODEOWNERS},
            HEAD_SHA: {".github/CODEOWNERS": HEAD_CODEOWNERS},
        },
        changed=["spec/policies/autonomy/matrix.yaml"],
        reviews=[],
    )
    source, text = resolve_codeowners(api.file_at, BASE_SHA)
    assert text == BASE_CODEOWNERS
    assert BASE_SHA[:12] in source
    assert api.refs_asked == [BASE_SHA]  # the head ref is never even consulted


def test_pr_cannot_unown_itself_by_deleting_codeowners_lines_in_its_own_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: the head's CODEOWNERS says 'nothing is owned'; the gate must still go RED."""
    api = FakeAPI(
        files_by_ref={
            BASE_SHA: {".github/CODEOWNERS": BASE_CODEOWNERS},
            HEAD_SHA: {".github/CODEOWNERS": HEAD_CODEOWNERS},
        },
        changed=["spec/policies/autonomy/matrix.yaml", ".github/CODEOWNERS"],
        reviews=[],
    )
    monkeypatch.setattr(gate, "GitHubAPI", lambda **kwargs: api)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    event = write_event(tmp_path)

    assert gate.main(["--event-path", str(event)]) == 1

    # Sanity: had the gate read the HEAD instead, it would have found nothing owned and gone green.
    head_decision = decide(
        codeowners_text=HEAD_CODEOWNERS,
        codeowners_source="head",
        changed_paths=["spec/policies/autonomy/matrix.yaml", ".github/CODEOWNERS"],
        reviews=[],
        author_login=AUTHOR,
        membership=nobody_is_a_member,
    )
    assert head_decision.ok, "fixture is not proving anything unless the head reading would be green"


def test_main_is_green_end_to_end_with_a_qualified_owner_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeAPI(
        files_by_ref={BASE_SHA: {".github/CODEOWNERS": BASE_CODEOWNERS}},
        changed=["spec/policies/autonomy/matrix.yaml"],
        reviews=[approval(OWNER_USER)],
    )
    monkeypatch.setattr(gate, "GitHubAPI", lambda **kwargs: api)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gate.main(["--event-path", str(write_event(tmp_path))]) == 0


def test_main_dry_run_mode_fetches_the_pr_context_and_still_reads_codeowners_from_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--repo/--pr` with no event payload: same evaluation, context fetched from the API instead."""
    api = FakeAPI(
        files_by_ref={
            BASE_SHA: {".github/CODEOWNERS": BASE_CODEOWNERS},
            HEAD_SHA: {".github/CODEOWNERS": HEAD_CODEOWNERS},
        },
        changed=["spec/policies/autonomy/matrix.yaml"],
        reviews=[],
    )
    api.pull_request = lambda pr: gate.EventContext(  # type: ignore[attr-defined]
        repo="o/r", pr_number=pr, base_sha=BASE_SHA, author_login=AUTHOR
    )
    monkeypatch.setattr(gate, "GitHubAPI", lambda **kwargs: api)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gate.main(["--repo", "o/r", "--pr", "244"]) == 1
    assert api.refs_asked == [BASE_SHA]


def test_main_with_neither_event_path_nor_repo_and_pr_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gate.main([]) == 1


def test_main_is_red_when_no_token_is_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gate.main(["--event-path", str(write_event(tmp_path))]) == 1


def test_missing_codeowners_at_base_is_red_never_green() -> None:
    api = FakeAPI(files_by_ref={BASE_SHA: {}}, changed=["README.md"], reviews=[])
    with pytest.raises(GateError, match="no CODEOWNERS found at base"):
        resolve_codeowners(api.file_at, BASE_SHA)


def test_codeowners_location_precedence_matches_github() -> None:
    assert CODEOWNERS_CANDIDATE_PATHS == (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
    api = FakeAPI(
        files_by_ref={BASE_SHA: {"CODEOWNERS": "/a/  @x\n", "docs/CODEOWNERS": "/b/  @y\n"}},
        changed=[],
        reviews=[],
    )
    source, text = resolve_codeowners(api.file_at, BASE_SHA)
    assert text == "/a/  @x\n"
    assert source.startswith("CODEOWNERS @ base")


def test_event_context_reads_the_pull_request_object(tmp_path: Path) -> None:
    context = load_event_context(write_event(tmp_path), None)
    assert context.repo == "Omni-Saude/maezo-operadora"
    assert context.pr_number == 244
    assert context.base_sha == BASE_SHA
    assert context.author_login == AUTHOR


@pytest.mark.parametrize(
    "overrides",
    [
        {"pull_request": {}},  # no number/base/user
        {"pull_request": {"number": 1, "user": {"login": "x"}}},  # no base.sha
        {"pull_request": {"number": 1, "base": {"sha": BASE_SHA}}},  # no author
    ],
)
def test_malformed_event_payload_fails_closed(tmp_path: Path, overrides: dict[str, Any]) -> None:
    with pytest.raises(GateError):
        load_event_context(write_event(tmp_path, **overrides), None)


def test_event_payload_without_a_pull_request_object_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"repository": {"full_name": "o/r"}}), encoding="utf-8")
    with pytest.raises(GateError, match="no `pull_request` object"):
        load_event_context(path, None)


def test_unreadable_event_payload_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(GateError, match="could not read the event payload"):
        load_event_context(tmp_path / "does-not-exist.json", None)


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (200, {"state": "active"}, MembershipState.ACTIVE),
        (200, {"state": "pending"}, MembershipState.PENDING),
        (200, {"role": "member"}, MembershipState.UNVERIFIABLE),  # no state field
        (404, None, MembershipState.UNVERIFIABLE),
        (403, None, MembershipState.UNVERIFIABLE),
        (500, None, MembershipState.UNVERIFIABLE),
    ],
)
def test_membership_status_classification(
    monkeypatch: pytest.MonkeyPatch, status: int, payload: Any, expected: MembershipState
) -> None:
    """404 is UNVERIFIABLE, never 'not a member': it is returned for both a non-member AND a token
    that cannot see the org at all. Only 200/`active` is membership."""
    api = gate.GitHubAPI(repo="o/r", token="t")
    monkeypatch.setattr(api, "_request", lambda path: (status, payload))
    assert api.membership("Omni-Saude", "security", OUTSIDER) is expected


def test_membership_transport_failure_is_unverifiable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(path: str) -> tuple[int, Any]:
        raise GateError("network down")

    api = gate.GitHubAPI(repo="o/r", token="t")
    monkeypatch.setattr(api, "_request", boom)
    assert api.membership("Omni-Saude", "security", OUTSIDER) is MembershipState.UNVERIFIABLE


def test_changed_paths_include_a_renames_previous_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rename OUT of an owned directory is a change to an owned path."""
    api = gate.GitHubAPI(repo="o/r", token="t")
    monkeypatch.setattr(
        api,
        "_paginate",
        lambda path: [
            {"filename": "elsewhere/matrix.yaml", "previous_filename": "spec/policies/autonomy/matrix.yaml"},
            {"filename": "README.md"},
        ],
    )
    paths = api.changed_paths(1)
    rules = parse_codeowners(BASE_CODEOWNERS)
    # The NEW name escapes ownership; the PREVIOUS name does not — and it is the previous name that
    # makes this a change to an owned path, so it must be in the list.
    assert owners_for_path(rules, "elsewhere/matrix.yaml") is None
    assert "spec/policies/autonomy/matrix.yaml" in paths
    assert owners_for_path(rules, "spec/policies/autonomy/matrix.yaml") is not None
    assert paths == ["elsewhere/matrix.yaml", "spec/policies/autonomy/matrix.yaml", "README.md"]


def test_api_list_endpoint_failure_is_red_not_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The single most dangerous silent-green: an errored list read must never read as 'no files'."""
    api = gate.GitHubAPI(repo="o/r", token="t")
    monkeypatch.setattr(api, "_request", lambda path: (403, None))
    with pytest.raises(GateError, match="fails CLOSED"):
        api.changed_paths(1)


# =============================================================================================
# 4. Real-tree: the parser really does cover this repo's actual CODEOWNERS
# =============================================================================================


def test_the_real_codeowners_file_parses_completely() -> None:
    rules = parse_codeowners(_REAL_CODEOWNERS.read_text(encoding="utf-8"))
    assert len(rules) >= 20, "parser found suspiciously few rules — is it silently skipping lines?"
    assert all(rule.owners for rule in rules), "every real line declares at least one owner"


def test_the_real_codeowners_pattern_shapes_are_all_covered() -> None:
    """Non-vacuity: enumerate the shapes actually present, so a new shape shows up as a failure here
    rather than as a mysteriously-passing gate."""
    rules = parse_codeowners(_REAL_CODEOWNERS.read_text(encoding="utf-8"))
    shapes = {
        ("rooted-dir" if r.pattern.endswith("/") else "rooted-file")
        if r.pattern.startswith("/")
        else "unrooted"
        for r in rules
    }
    assert shapes == {"rooted-dir", "rooted-file"}, (
        f"the real CODEOWNERS grew a new pattern shape {shapes} — confirm _compile_pattern handles it "
        "and extend test_pattern_matching_semantics"
    )
    assert not any("*" in r.pattern for r in rules), "the real file grew a wildcard — add coverage"


def test_the_real_codeowners_owns_the_known_flip_paths() -> None:
    """Spot-check the paths whose ratification IS the enforcement flip Q-1 is about."""
    rules = parse_codeowners(_REAL_CODEOWNERS.read_text(encoding="utf-8"))
    for path in (
        "spec/policies/autonomy/action-approvals.yaml",
        "spec/processes/dmn/auth-criteria-ratification.yaml",
        "spec/processes/dmn/adequacao_gap.dmn",
        "spec/policies/retention/erasure-plan.template.yaml",
        "spec/policies/privacy/phi-business-key-remediation.yaml",
        "spec/policies/ans/tiss-schema-pin.yaml",
    ):
        rule = owners_for_path(rules, path)
        assert rule is not None, f"{path} is not owned by the real CODEOWNERS"
        assert any(o.kind is OwnerKind.USER for o in rule.owners), f"{path} has no explicit user owner"


def test_a_real_flip_pr_shape_is_red_without_review_and_green_with_the_owner() -> None:
    """The end-to-end scenario the gate exists for, against the REAL ownership rules."""
    real = _REAL_CODEOWNERS.read_text(encoding="utf-8")
    flip_paths = (
        "spec/processes/dmn/adequacao-gap-shadow-candidate.yaml",
        "spec/processes/dmn/adequacao_gap.dmn",
    )
    red = run(codeowners=real, changed=flip_paths, reviews=())
    assert not red.ok

    green = run(codeowners=real, changed=flip_paths, reviews=(approval(OWNER_USER),))
    assert green.ok, green.render()

    self_approved = run(
        codeowners=real, changed=flip_paths, reviews=(approval(OWNER_USER),), author=OWNER_USER
    )
    assert not self_approved.ok, "the sole user owner must not be able to self-approve his own flip"
    assert "NO self-service route to green" in self_approved.render()
