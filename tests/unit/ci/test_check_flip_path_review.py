"""Unit tests for the flip-path CODEOWNERS review gate (owner decision Q-1).

Three layers, mirroring `test_check_start_process_fence.py`:

1. **Pure-core** tests drive `parse_codeowners` / `owners_for_path` / `standing_reviews` / `decide`
   with fixture payloads and an injected membership resolver — no network, no `gh`, no tokens.
2. **Shell** tests drive `resolve_codeowners`, `load_event_context`, `GitHubAPI.membership` and
   `main()` against a fake API and fixture Actions event payloads, so the wiring that decides WHICH
   commit's CODEOWNERS is read is itself under test (that is the un-own-yourself hole).
3. **Real-tree** tests parse this repo's actual `.github/CODEOWNERS` and assert the parser covers
   every pattern shape it really contains — a passing gate must not be passing because it silently
   understood nothing. They DERIVE the owner they approve as from the parsed file (so a CODEOWNERS
   audit that renames the owner cannot silently hollow them out) and separately PIN the file's
   user-owner roster against hardcoded literals (so derivation cannot hollow itself out). Read the
   header of section 4 before adding one.
"""

from __future__ import annotations

import json
import os
import re
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
_GATE_SOURCE = _REPO_ROOT / "scripts" / "ci" / "check_flip_path_review.py"
_GATE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "flip-path-review-gate.yml"

# =============================================================================================
# AMBIENT ENVIRONMENT ISOLATION — read this before adding a test that calls `main()`.
#
# This gate's entrypoint reads the environment, and the environment it reads is exactly the one
# GitHub Actions exports around the pytest process. A test that leaves `$GITHUB_EVENT_PATH` in place
# therefore runs against a DIFFERENT context in CI than on a laptop: the real event payload of the
# job's own PR. That is not hypothetical — PR #249 went red in CI while green locally, because
# `test_main_dry_run_mode_...` picked up the job's real base SHA instead of its synthetic fixture.
#
# So: every test in this file starts from a SCRUBBED environment, autouse, no opt-in required. Tests
# that want an ambient variable set it themselves, explicitly, and thereby say so.
# =============================================================================================

#: Every environment variable the gate script reads. Pinned as a literal and checked against the
#: script's source by `test_the_ambient_env_scrub_list_covers_every_variable_the_script_reads`, so a
#: new `os.environ` read cannot be added to the gate without being added to the scrub as well —
#: which is the only way this class of CI-only divergence stays fixed.
_AMBIENT_GITHUB_ENV_VARS: tuple[str, ...] = (
    "GH_TOKEN",
    "GITHUB_EVENT_PATH",
    "GITHUB_STEP_SUMMARY",
    "GITHUB_TOKEN",
)


@pytest.fixture(autouse=True)
def _scrub_ambient_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete every ambient variable the gate reads, for EVERY test in this module."""
    for name in _AMBIENT_GITHUB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_the_ambient_env_scrub_list_covers_every_variable_the_script_reads() -> None:
    """Non-vacuity for the fixture above: the scrub cannot silently fall behind the script.

    Scanned from the source rather than trusted, because the failure mode is invisible locally — a
    newly-read variable that nobody scrubs is green on every laptop and only misbehaves inside
    Actions, which is the most expensive place to find out.
    """
    source = _GATE_SOURCE.read_text(encoding="utf-8")
    read = set(re.findall(r"""os\.environ(?:\.get)?[(\[]["']([A-Za-z0-9_]+)["']""", source))
    assert read, "found no os.environ reads at all — has the scan pattern rotted?"
    assert read <= set(_AMBIENT_GITHUB_ENV_VARS), (
        f"{sorted(read - set(_AMBIENT_GITHUB_ENV_VARS))} is read by the gate but not scrubbed by "
        "`_scrub_ambient_github_env`. Add it there, or this file's tests mean something different "
        "inside GitHub Actions than they do locally."
    )


def test_the_ambient_scrub_actually_took_effect_in_this_test() -> None:
    """Direct proof the autouse fixture RAN, not merely that its list is complete.

    Vacuous on a bare laptop, which is the point: it is the CI environment this pins. Measured cost
    of the fixture not running there — the suite writes its synthetic gate verdicts into the real
    `$GITHUB_STEP_SUMMARY`, 131 lines of fixture output landing in the job summary of whatever PR
    happens to be building.
    """
    leaked = [name for name in _AMBIENT_GITHUB_ENV_VARS if name in os.environ]
    assert leaked == [], f"{leaked} survived into a test body — `_scrub_ambient_github_env` did not run"


AUTHOR = "flip-author"
#: The login used by the SYNTHETIC fixtures below. It is the repo's real owner account
#: (`rodaquino-OMNI`) rather than an invented string so the fixtures keep the same shape as the real
#: file — including the mixed case, which is the interesting part for the case-insensitivity rules.
#:
#: PROVENANCE (2026-08-13 CODEOWNERS audit, sibling branch `codeowners-audit`): this constant used
#: to read `rodrigotaquino`, a handle that DOES NOT EXIST as a GitHub account (GET /users → 404 with
#: a full-scope token). The audit replaced it throughout `.github/CODEOWNERS` with `rodaquino-OMNI`,
#: the real account of the same human. The REAL-TREE tests below must NOT hardcode this constant —
#: they derive the expected owner from the parsed file (see `real_user_owners_of`) and pin the
#: file's user-owner ROSTER separately (see `_EXPECTED_FLIP_PATH_OWNER`).
OWNER_USER = "rodaquino-OMNI"
OTHER_OWNER = "second-owner"
OUTSIDER = "random-contributor"

#: A miniature CODEOWNERS in the same shape as the real one (rooted dirs + rooted exact files),
#: plus a second, disjointly-owned area so the per-path-coverage semantics can be exercised.
FIXTURE_CODEOWNERS = f"""\
# comment line, ignored
/spec/policies/autonomy/  @{OWNER_USER} @Omni-Saude/security-team
/spec/policies/autonomy/action-approvals.yaml  @{OWNER_USER} @Omni-Saude/security-team
/docs/adr/  @{OWNER_USER}
/src/other/  @{OTHER_OWNER}
"""

#: Team-only ownership: no explicit user owner at all, so the ONLY route to green is a verified
#: active team member.
TEAM_ONLY_CODEOWNERS = "/spec/policies/autonomy/  @Omni-Saude/security-team\n"


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
    assert "@Omni-Saude/security-team" in rendered
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


# ---- N10: COMMENTED never SUPERSEDES a standing review, in EITHER direction --------------------
#
# `test_commented_and_pending_reviews_never_count_as_approval` proves COMMENTED cannot CREATE an
# approval. It does not prove COMMENTED cannot DESTROY one, nor that it cannot CLEAR an objection —
# and those are the two directions that matter, because "reviewer left a comment after reviewing" is
# the single most common thing that happens on a real PR. Adding "COMMENTED" to `_STANDING_STATES`
# (a one-token change that looks like a completeness fix) flips both of these; nothing else notices.


def test_a_later_comment_does_not_clear_an_owners_standing_changes_requested() -> None:
    """Direction 1 — the objection survives. RED, and RED *for the objection*, not for absence."""
    decision = run(
        codeowners=f"/spec/policies/autonomy/  @{OWNER_USER} @{OTHER_OWNER}\n",
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            Review(OWNER_USER, "CHANGES_REQUESTED", "2026-08-13T10:00:00Z", 1),
            Review(OWNER_USER, "COMMENTED", "2026-08-13T11:00:00Z", 2),  # "ok, replying to your note"
            approval(OTHER_OWNER, when="2026-08-13T12:00:00Z", review_id=3),
        ),
    )
    assert not decision.ok, decision.render()
    assert "has requested changes" in decision.headline, (
        "the objection must still be the REASON — if this now reds merely for 'lacks approval', the "
        "comment silently downgraded a live CHANGES_REQUESTED into nothing"
    )
    # Directly at the reduction, so the pin does not depend on `decide`'s wording:
    latest = standing_reviews(
        [
            Review(OWNER_USER, "CHANGES_REQUESTED", "2026-08-13T10:00:00Z", 1),
            Review(OWNER_USER, "COMMENTED", "2026-08-13T11:00:00Z", 2),
        ]
    )
    assert latest[OWNER_USER.lower()].state == "CHANGES_REQUESTED"


def test_a_later_comment_does_not_revoke_an_owners_standing_approval() -> None:
    """Direction 2 — the approval survives. GREEN stays GREEN."""
    decision = run(
        changed=("spec/policies/autonomy/matrix.yaml",),
        reviews=(
            approval(OWNER_USER, when="2026-08-13T10:00:00Z", review_id=1),
            Review(OWNER_USER, "COMMENTED", "2026-08-13T11:00:00Z", 2),  # "one nit, non-blocking"
        ),
    )
    assert decision.ok, decision.render()
    latest = standing_reviews(
        [
            approval(OWNER_USER, when="2026-08-13T10:00:00Z", review_id=1),
            Review(OWNER_USER, "COMMENTED", "2026-08-13T11:00:00Z", 2),
        ]
    )
    assert latest[OWNER_USER.lower()].state == "APPROVED"


def test_the_standing_state_set_is_exactly_the_three_that_decide_anything() -> None:
    """The membership pin, stated once: COMMENTED and PENDING are OUTSIDE the set by design."""
    assert sorted(gate._STANDING_STATES) == ["APPROVED", "CHANGES_REQUESTED", "DISMISSED"]


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
        codeowners=f"/spec/policies/autonomy/  @{OWNER_USER} @Omni-Saude/security-team\n",
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
        codeowners=f"/spec/policies/autonomy/  @{OWNER_USER}\n/spec/[abc]/x.yaml  @someone\n",
        changed=("README.md",),  # would otherwise be an instant green
        reviews=(),
    )
    assert not decision.ok
    rendered = decision.render()
    assert "CODEOWNERS could not be parsed" in rendered
    assert "fail-closed" in rendered


@pytest.mark.parametrize("token", ["bare-login-with-no-at", "@", "@/team", "@@x", "not-an-owner"])
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
    assert gate.main(["--repo", "o/r", "--pr", "244"]) == 1
    assert api.refs_asked == [BASE_SHA]


def test_main_with_neither_event_path_nor_repo_and_pr_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert gate.main([]) == 1


# ---- CONTEXT PRECEDENCE: injected context beats the ambient Actions environment ----------------
#
# The bug these pin (PR #249, CI-red / laptop-green): `--event-path` carried
# `default=os.environ.get("GITHUB_EVENT_PATH")` and `main` tested it first, so an exported
# $GITHUB_EVENT_PATH outranked `--repo/--pr` with no way to opt out. Inside Actions that variable
# always exists, so the documented dry-run command judged whichever PR the surrounding job belonged
# to. Both directions are pinned below, because fixing precedence in one direction is exactly how
# you break the production path in the other.

#: A base SHA that no test fixture uses, standing in for "the PR the ambient job belongs to".
AMBIENT_BASE_SHA = "407de72f0000000000000000000000000000beef"


def _direct_mode_api() -> FakeAPI:
    """A fake wired for the direct (`--repo/--pr`) path: `pull_request` supplies the context."""
    api = FakeAPI(
        files_by_ref={
            BASE_SHA: {".github/CODEOWNERS": BASE_CODEOWNERS},
            AMBIENT_BASE_SHA: {".github/CODEOWNERS": HEAD_CODEOWNERS},
        },
        changed=["spec/policies/autonomy/matrix.yaml"],
        reviews=[],
    )
    api.pull_request = lambda pr: gate.EventContext(  # type: ignore[attr-defined]
        repo="o/r", pr_number=pr, base_sha=BASE_SHA, author_login=AUTHOR
    )
    return api


def test_injected_repo_and_pr_beat_an_ambient_actions_event_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE REGRESSION PIN for the #249 CI-only failure.

    A hostile ambient payload names a different repo, a different PR and a different base SHA — and
    stocks that SHA with a CODEOWNERS that owns nothing, so following it would also go GREEN. The
    run must still be governed entirely by the injected `--repo/--pr`.
    """
    hostile = write_event(
        tmp_path,
        repository={"full_name": "someone-else/other-repo"},
        pull_request={
            "number": 9999,
            "user": {"login": "ambient-author"},
            "base": {"sha": AMBIENT_BASE_SHA, "ref": "main"},
            "head": {"sha": HEAD_SHA, "ref": "ambient-branch"},
        },
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(hostile))
    api = _direct_mode_api()
    monkeypatch.setattr(gate, "GitHubAPI", lambda **kwargs: api)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    assert gate.main(["--repo", "o/r", "--pr", "244"]) == 1, (
        "the injected PR touches an owned path with no approval — following the ambient payload "
        "instead would have found nothing owned and gone GREEN"
    )
    assert api.refs_asked == [BASE_SHA], (
        f"the gate resolved CODEOWNERS at {api.refs_asked} — an exported $GITHUB_EVENT_PATH must "
        "not redirect a run the caller pointed at a specific PR."
    )


def test_the_zero_argument_workflow_invocation_still_uses_the_ambient_event_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: rule 3 is the PRODUCTION path and must not have been broken by rule 2."""
    api = FakeAPI(
        files_by_ref={BASE_SHA: {".github/CODEOWNERS": BASE_CODEOWNERS}},
        changed=["spec/policies/autonomy/matrix.yaml"],
        reviews=[],
    )
    monkeypatch.setattr(gate, "GitHubAPI", lambda **kwargs: api)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(write_event(tmp_path)))

    assert gate.main([]) == 1
    assert api.refs_asked == [BASE_SHA]


def test_the_workflow_really_does_invoke_the_gate_with_no_arguments() -> None:
    """Ties rule 3's "this is the production path" claim to the workflow, instead of asserting it.

    If the workflow ever starts passing `--event-path` explicitly, that is rule 1 and the reasoning
    above needs rereading — so it should fail here rather than drift.
    """
    workflow = _GATE_WORKFLOW.read_text(encoding="utf-8")
    assert "python3 scripts/ci/check_flip_path_review.py\n" in workflow, (
        "the workflow no longer invokes the gate bare — re-check CONTEXT PRECEDENCE in the script "
        "docstring, which documents rule 3 as the only path production uses"
    )
    assert "--event-path" not in workflow


def test_a_lone_pr_override_still_applies_on_top_of_the_ambient_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--pr` alone is an OVERRIDE, not a mode switch: the ambient payload still supplies context."""
    api = FakeAPI(
        files_by_ref={BASE_SHA: {".github/CODEOWNERS": BASE_CODEOWNERS}},
        changed=["spec/policies/autonomy/matrix.yaml"],
        reviews=[],
    )
    seen: list[int] = []
    api.changed_paths = lambda pr_number: (seen.append(pr_number), ["spec/policies/autonomy/x.yaml"])[1]  # type: ignore[assignment]
    monkeypatch.setattr(gate, "GitHubAPI", lambda **kwargs: api)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(write_event(tmp_path)))

    assert gate.main(["--pr", "777"]) == 1
    assert api.refs_asked == [BASE_SHA], "context still comes from the payload"
    assert seen == [777], "but the PR number override was honoured"


def test_main_is_red_when_no_token_is_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert gate.main(["--event-path", str(write_event(tmp_path))]) == 1


def test_missing_codeowners_at_base_is_red_never_green() -> None:
    api = FakeAPI(files_by_ref={BASE_SHA: {}}, changed=["README.md"], reviews=[])
    with pytest.raises(GateError, match="no CODEOWNERS found at base"):
        resolve_codeowners(api.file_at, BASE_SHA)


# ---- the base-fetch FAILURE path: RED, never a fallback to HEAD --------------------------------
#
# `test_codeowners_is_read_from_base_not_head` and `test_pr_cannot_unown_itself_...` pin what happens
# when the base fetch SUCCEEDS. They say nothing about what happens when it FAILS — and the tempting
# repair for a flaky contents API ("couldn't read the base, try the head") is precisely Decision 1
# inverted: it hands every PR a way to un-own its own payload by making the base read fail. Nothing
# was pinning it: a faithful `except -> resolve at head` fallback inserted into `resolve_codeowners`
# left all 87 tests green. These three do the pinning.


class FailingBaseFetchAPI(FakeAPI):
    """Base-commit reads blow up the way a real HTTP 500 does. EVERY OTHER REF ANSWERS PERMISSIVELY.

    Ref-agnostic on the fallback side on purpose: a retry could be written against `HEAD_SHA`, the
    literal string `"HEAD"`, the base BRANCH name, or `main`, and all four are the same bug. Serving
    a CODEOWNERS that owns NOTHING to anything-but-the-base means ANY of them computes "no owned path
    touched" and goes GREEN — so the tests below fail loudly instead of passing because the neuter
    happened to name a ref the fixture did not stock.
    """

    def file_at(self, ref: str, path: str) -> str | None:
        self.refs_asked.append(ref)
        if ref == BASE_SHA:
            raise GateError(
                f"GET contents/{path}@{ref} returned HTTP 500 — cannot read the base commit's "
                "CODEOWNERS, so the gate cannot prove which paths are owned. Fail-closed."
            )
        return HEAD_CODEOWNERS if path == ".github/CODEOWNERS" else None


def _failing_base_api() -> FailingBaseFetchAPI:
    return FailingBaseFetchAPI(
        files_by_ref={},
        changed=["spec/policies/autonomy/matrix.yaml"],
        reviews=[],
    )


def test_a_failed_base_codeowners_fetch_propagates_and_never_falls_back_to_head() -> None:
    """The unit-level pin: the error escapes `resolve_codeowners`, and no other ref is consulted."""
    api = _failing_base_api()
    with pytest.raises(GateError, match="cannot read the base commit's CODEOWNERS"):
        resolve_codeowners(api.file_at, BASE_SHA)
    assert api.refs_asked == [BASE_SHA], (
        f"the gate consulted {api.refs_asked} — a base-fetch failure must NOT be retried at the head "
        "ref (or any other), because reading the head is exactly the un-own-yourself hole Decision 1 "
        "closes."
    )


def test_main_is_red_end_to_end_when_the_base_codeowners_fetch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end pin: HTTP 500 on the base read exits 1, and NO other ref is ever consulted.

    Non-vacuity is proved in-test: the very content served to every other ref WOULD be green.
    """
    api = _failing_base_api()
    monkeypatch.setattr(gate, "GitHubAPI", lambda **kwargs: api)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    assert gate.main(["--event-path", str(write_event(tmp_path))]) == 1
    assert api.refs_asked == [BASE_SHA], (
        f"the gate consulted {api.refs_asked} — falling back to ANY other ref after a failed base "
        "read is a silent-green bypass, whatever that ref is called."
    )

    would_be_green = decide(
        codeowners_text=HEAD_CODEOWNERS,
        codeowners_source="head",
        changed_paths=["spec/policies/autonomy/matrix.yaml"],
        reviews=[],
        author_login=AUTHOR,
        membership=nobody_is_a_member,
    )
    assert would_be_green.ok, "fixture proves nothing unless the head reading would have been green"


def test_the_api_layer_turns_a_non_404_contents_response_into_a_gate_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where that GateError comes from in production: only 404 means "absent"; 500/403 are RED.

    Without this, `file_at` could start returning `None` on a server error and `resolve_codeowners`
    would read it as "no CODEOWNERS here, try the next candidate" — a parse of the empty set of
    rules, i.e. a silent green, reached without any fallback code being written at all.
    """
    api = gate.GitHubAPI(repo="o/r", token="t")
    monkeypatch.setattr(api, "_request", lambda path: (404, None))
    assert api.file_at(BASE_SHA, ".github/CODEOWNERS") is None  # absent is the ONLY None

    for status in (403, 500, 502):
        monkeypatch.setattr(api, "_request", lambda path, _s=status: (_s, None))
        with pytest.raises(GateError, match="cannot read the base commit's CODEOWNERS"):
            api.file_at(BASE_SHA, ".github/CODEOWNERS")


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
    assert api.membership("Omni-Saude", "security-team", OUTSIDER) is expected


def test_membership_transport_failure_is_unverifiable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(path: str) -> tuple[int, Any]:
        raise GateError("network down")

    api = gate.GitHubAPI(repo="o/r", token="t")
    monkeypatch.setattr(api, "_request", boom)
    assert api.membership("Omni-Saude", "security-team", OUTSIDER) is MembershipState.UNVERIFIABLE


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
#
# HOW THESE TESTS NAME OWNERS — read before adding one.
# The satisfaction tests DERIVE the owner they approve as from the parse of the real file, so they
# survive a CODEOWNERS audit that renames the owner (2026-08-13 is exactly such an audit: the
# phantom `@rodrigotaquino` became the real `@rodaquino-OMNI`). Derivation alone is a known
# anti-pattern though — a test whose expectation comes entirely from its own input cannot notice the
# input losing all user owners, or gaining one nobody approved. So the derivation is PINNED by
# `test_the_real_codeowners_user_owner_roster_is_one_of_the_pinned_sets`, which compares the file's
# whole user-owner roster against hardcoded literal snapshots. Derive for behaviour, pin for
# identity; neither alone is sufficient.
# =============================================================================================

#: The complete set of explicit USER owners the real `.github/CODEOWNERS` is allowed to declare,
#: enumerated as literal snapshots with provenance. Exact-set (not subset) on purpose: an unexpected
#: login appearing, the roster emptying, or a login being typo'd all have to fail here.
#:
#:   PRE-AUDIT  — the tree on THIS branch today. `rodrigotaquino` is the PHANTOM: GET /users returns
#:                404 with a full-scope token, so under `require_code_owner_review` every line owned
#:                only by it would have been an EMPTY gate.
#:   POST-AUDIT — the 2026-08-13 CODEOWNERS audit (sibling branch, merges BEFORE this one).
#:                `rodaquino-OMNI` is the real account of the same human; `lucasreisEvah` was added
#:                to the three LGPD lines only (privacy + the two retention lines) as the DPO role
#:                the ADR-0029 trail needs, and is the sole valid owner in the file who is not the
#:                identity that authors the PRs.
#:
#: WHEN THE AUDIT HAS MERGED: delete the PRE-AUDIT snapshot. Leaving it is how a pin rots into a
#: rubber stamp — it would let the phantom come back unnoticed.
_PRE_AUDIT_USER_OWNERS = frozenset({"rodrigotaquino"})
_POST_AUDIT_USER_OWNERS = frozenset({"rodaquino-OMNI", "lucasreisEvah"})

#: Roster snapshot -> the literal login that must own the flip paths under it. Both sides hardcoded:
#: WHICH account owns the ratification paths is a fact to be pinned, not derived from the file being
#: checked. Delete the pre-audit row together with `_PRE_AUDIT_USER_OWNERS`.
_EXPECTED_FLIP_PATH_OWNER: dict[frozenset[str], str] = {
    _PRE_AUDIT_USER_OWNERS: "rodrigotaquino",
    _POST_AUDIT_USER_OWNERS: "rodaquino-OMNI",
}

#: The paths whose ratification IS the enforcement flip Q-1 exists for.
_FLIP_PATHS = (
    "spec/policies/autonomy/action-approvals.yaml",
    "spec/processes/dmn/auth-criteria-ratification.yaml",
    "spec/processes/dmn/adequacao_gap.dmn",
    "spec/policies/retention/erasure-plan.template.yaml",
    "spec/policies/privacy/phi-business-key-remediation.yaml",
    "spec/policies/ans/tiss-schema-pin.yaml",
)


def real_rules() -> tuple[gate.Rule, ...]:
    return parse_codeowners(_REAL_CODEOWNERS.read_text(encoding="utf-8"))


def real_user_owner_roster() -> frozenset[str]:
    return frozenset(o.login for r in real_rules() for o in r.owners if o.kind is OwnerKind.USER and o.login)


def real_user_owners_of(path: str) -> list[str]:
    """Every explicit USER owner of `path`, DERIVED from the real file's winning rule.

    Deriving rather than hardcoding is what lets these tests keep meaning something across a
    CODEOWNERS audit that renames the owner. It asserts non-emptiness so "the file lost its user
    owners" is a failure here too, not a silently-vacuous pass.
    """
    rule = owners_for_path(real_rules(), path)
    assert rule is not None, f"{path} is not owned by the real CODEOWNERS"
    logins = [o.login for o in rule.owners if o.kind is OwnerKind.USER and o.login]
    assert logins, f"{path} has no explicit user owner in the real CODEOWNERS"
    return logins


def test_the_real_codeowners_user_owner_roster_is_one_of_the_pinned_sets() -> None:
    """THE PIN behind the derivation (see the section header). Hardcoded literals on purpose.

    EXACT-set, not subset: the roster emptying, gaining a login nobody approved, or a login being
    typo'd all have to fail right here.
    """
    actual = real_user_owner_roster()
    assert actual in _EXPECTED_FLIP_PATH_OWNER, (
        f"the real CODEOWNERS declares user owners {sorted(actual)}, which is neither the pre-audit "
        f"snapshot {sorted(_PRE_AUDIT_USER_OWNERS)} nor the post-audit one "
        f"{sorted(_POST_AUDIT_USER_OWNERS)}. If this is a deliberate ownership change, update the "
        "snapshot AND confirm the new handle actually resolves (GET /users/<login>) — an "
        "unresolvable owner is an EMPTY gate under enforcement, which is the failure this pin exists "
        "to catch."
    )
    assert actual, "the real CODEOWNERS has no explicit user owner at all — every line is team-only"


def test_the_flip_paths_are_owned_by_the_pinned_literal_account() -> None:
    """The identity assertion the derivation cannot make for itself.

    Under the audited file this reads, literally: `@rodaquino-OMNI` — the account that actually
    exists — owns every flip path. Under this branch's still-pre-audit tree it reads the same way
    about the phantom it replaces, so the suite is honest on both sides of the merge instead of
    green-by-omission on one of them.
    """
    roster = real_user_owner_roster()
    expected = _EXPECTED_FLIP_PATH_OWNER.get(roster)
    assert expected is not None, (
        f"unpinned user-owner roster {sorted(roster)} — see the failure message on "
        "test_the_real_codeowners_user_owner_roster_is_one_of_the_pinned_sets, which explains what "
        "to check before updating the snapshot."
    )
    for path in _FLIP_PATHS:
        assert expected in real_user_owners_of(path), (
            f"{path} is not owned by @{expected} — the flip paths must all be owned by the one "
            "account the pinned roster names."
        )


def test_the_real_codeowners_file_parses_completely() -> None:
    rules = real_rules()
    assert len(rules) >= 20, "parser found suspiciously few rules — is it silently skipping lines?"
    assert all(rule.owners for rule in rules), "every real line declares at least one owner"


def test_the_real_codeowners_pattern_shapes_are_all_covered() -> None:
    """Non-vacuity: enumerate the shapes actually present, so a new shape shows up as a failure here
    rather than as a mysteriously-passing gate."""
    rules = real_rules()
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
    rules = real_rules()
    for path in _FLIP_PATHS:
        rule = owners_for_path(rules, path)
        assert rule is not None, f"{path} is not owned by the real CODEOWNERS"
        assert any(o.kind is OwnerKind.USER for o in rule.owners), f"{path} has no explicit user owner"


def test_a_real_flip_pr_shape_is_red_without_review_and_green_with_the_owner() -> None:
    """The end-to-end scenario the gate exists for, against the REAL ownership rules.

    The approver is DERIVED from the real file (see the section header) so a CODEOWNERS audit that
    renames the owner cannot silently turn this into a test of nothing — under the pre-audit file it
    approves as the phantom, under the audited one as `@rodaquino-OMNI`, and it means the same thing
    in both. Which login that is, is pinned by
    `test_the_flip_paths_are_owned_by_the_pinned_literal_account`.
    """
    real = _REAL_CODEOWNERS.read_text(encoding="utf-8")
    flip_paths = (
        "spec/processes/dmn/adequacao-gap-shadow-candidate.yaml",
        "spec/processes/dmn/adequacao_gap.dmn",
    )
    owner = real_user_owners_of(flip_paths[1])[0]
    assert real_user_owners_of(flip_paths[0])[0] == owner, "fixture assumes both halves share an owner"

    red = run(codeowners=real, changed=flip_paths, reviews=())
    assert not red.ok

    green = run(codeowners=real, changed=flip_paths, reviews=(approval(owner),))
    assert green.ok, green.render()

    self_approved = run(codeowners=real, changed=flip_paths, reviews=(approval(owner),), author=owner)
    assert not self_approved.ok, "the sole user owner must not be able to self-approve his own flip"
    assert "NO self-service route to green" in self_approved.render()
