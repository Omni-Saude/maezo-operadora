#!/usr/bin/env python3
"""CI gate: a PR touching a CODEOWNERS-owned path needs an APPROVED review from a QUALIFIED owner.

Purpose (owner decision Q-1: "each enforcement flip is a PR under CODEOWNERS review")
-------------------------------------------------------------------------------------
Every path this repo declares in `.github/CODEOWNERS` is there for the same reason: flipping the
handful of ratification fields under `spec/policies/**` and `spec/processes/dmn/**` is a DATA change
with no code and no redeploy, which is precisely why the data needs the reviewer the code path would
otherwise have had (read `.github/CODEOWNERS`'s own comments — they say this at length, per artifact).

CODEOWNERS on its own requests a reviewer; it does not require one. Requiring one is GitHub's
`require_code_owner_review` ruleset knob. This script is the FALLBACK (and, where the knob works, the
defence-in-depth second opinion): an ordinary status check that computes the same contract itself and
goes RED when it is not met, so the guarantee does not depend on a server-side setting that lives
only in repo Settings and that this repo has been burned by before (`branch-protection-check.yml`'s
module header documents `main` carrying NO server-side protection at all as of 2026-08-10).

THE CONTRACT
------------
A PR whose diff touches any CODEOWNERS-owned path must carry an APPROVED review from a QUALIFIED
reviewer (never the PR author) before this check goes green. A PR touching no owned path passes
instantly. Everything else — parse failure, API opacity, unverifiable team membership, a malformed
event payload — is RED with a legible message. There is no silent green anywhere in this file.

DESIGN DECISION 1 — CODEOWNERS IS READ FROM THE PR'S **BASE** COMMIT, NEVER FROM THE HEAD
-----------------------------------------------------------------------------------------
This is load-bearing and deliberate. If the gate read the CODEOWNERS in the PR's own head tree, a PR
could delete (or narrow) the very lines that own the files it edits and thereby un-own itself in the
same commit — the gate would compute "0 owned paths touched" and wave the flip through. Reading the
BASE commit's CODEOWNERS means the rules that govern a PR are the rules that were already reviewed
and merged. A PR that edits CODEOWNERS is still governed by the OLD file, which owns
`.github/CODEOWNERS`... only if the old file says so — and as of the 2026-08-13 CODEOWNERS audit it
DOES (`/.github/CODEOWNERS` owns itself, alongside `/.github/workflows/`, `/scripts/ci/` and
`/Makefile`). The two mechanisms are complementary, not redundant: the base-ref rule stops a PR from
un-owning its own payload, and the self-ownership line stops a CODEOWNERS-only PR from un-owning the
NEXT one. `pull_request.base.sha` is used rather than the base branch NAME so a base branch that
moves mid-run cannot change the answer underneath us.

There is NO fallback if the base read fails. An unreadable base CODEOWNERS is RED, never "try the
head": resolving at the head is Decision 1 inverted, and it would hand every PR an un-own-yourself
route via a failed fetch.

Tested by `test_codeowners_is_read_from_base_not_head`,
`test_pr_cannot_unown_itself_by_deleting_codeowners_lines_in_its_own_head`,
`test_a_failed_base_codeowners_fetch_propagates_and_never_falls_back_to_head` and
`test_main_is_red_end_to_end_when_the_base_codeowners_fetch_fails`.

DESIGN DECISION 2 — CHANGES_REQUESTED FROM AN OWNER OF A TOUCHED OWNED PATH ⇒ RED
--------------------------------------------------------------------------------
ADOPTED (the brief asked for a ruling; this is it, with the reasoning, not a coin flip).

A reviewer's latest standing review being CHANGES_REQUESTED reddens this check even when a different
qualified owner has approved. Three reasons:

  1. It AGREES WITH GITHUB. GitHub's own required-review semantics block merge while any review
     requests changes; another reviewer's approval does not clear it — only the requester approving,
     or the review being dismissed, does. This gate may run ALONGSIDE `require_code_owner_review`. Two
     enforcement mechanisms that disagree about the same PR produce a permanently-red PR with no
     legitimate route to green, and a permanently-red required check is a bypass generator, not a
     control. Agreeing with GitHub is therefore a security property, not a compatibility nicety.
  2. An owner who read the diff and said "no" is the highest-information signal the gate can see.
     Letting a second owner's approval silently outrank it turns owner review into approval-shopping,
     which is exactly the failure mode Q-1 exists to prevent.
  3. It is the fail-closed direction, and it is CHEAPLY REVERSIBLE BY A HUMAN: the objector approves,
     or someone with write access dismisses the review. Both are one click and both leave an audit
     trail. The opposite default (stale approval outranks a live objection) is reversible only by
     noticing, which is not a control.

SCOPE of the rule: only owners OF A TOUCHED OWNED PATH block. A CHANGES_REQUESTED from someone who
owns nothing this PR touches is outside this gate's contract — a PR touching no owned path passes
instantly, so it would be incoherent for a non-owner to be able to redden an owned one here. (They
can still block the merge through GitHub's own review requirement; this gate is not the only control.)
For a TEAM-owned path whose membership the token cannot verify, an objector is treated as a POSSIBLE
owner and therefore BLOCKS — both directions of "we do not know" resolve toward RED, consistently
with the team-approval rule below.

Tested by `test_changes_requested_from_owner_reddens_despite_other_owner_approval`,
`test_changes_requested_from_non_owner_does_not_redden`, and
`test_changes_requested_superseded_by_later_approval_from_same_reviewer_is_green`.

DESIGN DECISION 3 — PER-PATH COVERAGE IS THE DEFAULT, NOT "ONE REVIEWER OWNS EVERYTHING"
----------------------------------------------------------------------------------------
Green requires that EVERY touched owned path is covered by at least one qualified approval; different
owners may cover different paths, which is GitHub's own semantics (same argument as reason 1 above:
a fallback that is stricter than the native knob invents false REDs that the native knob will never
explain to the person staring at them). It is also not weaker HERE: `@rodaquino-OMNI` is an owner of
every single owned path in this repo's CODEOWNERS, so "one reviewer owns all touched paths" and
"each touched path is covered" select the identical set of PRs today. (The 2026-08-13 audit added
`@lucasreisEvah` to the three LGPD lines, but as an ADDITIONAL owner — it widens who can unblock
those lines, it does not create an area `@rodaquino-OMNI` fails to cover.) The two readings can only
diverge once the file grows a genuinely disjointly-owned area.

For the stricter reading, `--require-single-reviewer-covers-all` demands that ONE reviewer satisfy
every touched owned path. It is off by default and is a flag rather than a rewrite precisely so the
orchestrator can flip the semantics from the workflow without touching this logic.

Tested by `test_two_owners_covering_disjoint_paths_is_green_by_default` and
`test_two_owners_covering_disjoint_paths_is_red_under_single_reviewer_mode`.

QUALIFIED REVIEWER — the fail-closed hierarchy
----------------------------------------------
For a review to count toward a path P:
  a. it is the reviewer's LATEST standing review (COMMENTED and PENDING never change a reviewer's
     approval state, so they are ignored; a DISMISSED review clears that reviewer entirely);
  b. its state is APPROVED;
  c. `reviewer.login != pull_request.user.login` — a self-approval NEVER qualifies, at any tier;
  d. the reviewer satisfies P's ownership:
       - P owned by explicit USERS  → reviewer login ∈ that user list (case-insensitive, as GitHub
         logins are);
       - P owned by a TEAM `@org/team` → `GET /orgs/{org}/teams/{team}/memberships/{login}` must
         return 200 with `state == "active"`. `"pending"` is an unaccepted invitation, NOT membership.
         A 403/404 — or any other API failure — is UNVERIFIABLE, and an unverifiable team NEVER
         satisfies a path. The check goes RED naming the opacity and the manual override: an
         explicitly-listed USER owner must approve. It is never downgraded to
         "any non-author approval will do" — that downgrade is the whole vulnerability.
       - P owned by an EMAIL (CODEOWNERS permits `user@example.com`) → PERMANENTLY UNVERIFIABLE from
         a review payload, which carries `user.login` and no email. Parsed, never satisfiable, and
         reported as such so nobody believes an email owner is enforcing anything here.
     A path listing several owners is satisfied by ANY ONE of them (GitHub semantics).

Every rejection is reported with the reason it was rejected. The RED output names: which touched paths
are owned and by whom, which reviews were considered and why each was rejected, and what unblocks.

CODEOWNERS PARSING
------------------
Last-matching-pattern wins (GitHub semantics — the real file's own comments rely on this, twice, and
say so). A matching rule with NO owners UNSETS ownership, as GitHub does. Supported pattern grammar is
deliberately bounded (this is not a gitignore engine): literal path segments, `*` (matches within one
segment), `**` (matches across segments), a leading `/` (anchors to the repo root), a trailing `/`
(directory — matches everything beneath it). A pattern containing anything else — `?`, `[...]`
character classes, `!` negation, backslash escapes, or any character outside `[A-Za-z0-9._/*-]` — is
an UNRECOGNIZED SHAPE and is a hard RED for the whole run. It is never skipped: silently ignoring a
pattern this parser does not understand is exactly how an owned path becomes invisible to the gate.
The RED message names the line and tells you to extend `_compile_pattern` (and its tests) deliberately.

An unrooted pattern with no internal slash (`docs/`, `*.py`) matches at ANY directory depth, per git.
A rooted pattern with no trailing slash matches the path exactly AND everything beneath it if it is a
directory — the more-ownership reading, which is the fail-closed one.

API SHELL
---------
`urllib.request` against api.github.com with the workflow's `GITHUB_TOKEN`, NOT `gh api`. One reason,
and it is the whole design: this gate's correctness turns on telling a 200/`active` from a 200/
`pending` from a 403/404, and `gh api` surfaces a non-2xx as an exit code plus a prose stderr line —
recovering the status code from it means parsing English. `urllib` hands over `HTTPError.code`. Same
credential `gh` would use, no CLI dependency, stdlib only.

The pure core (`parse_codeowners`, `owners_for_path`, `standing_reviews`, `decide`) takes plain data
and an injected membership resolver, so every unit test runs with fixture payloads and no network.

Usage (CI / local)
------------------
    python3 scripts/ci/check_flip_path_review.py                       # CI: reads $GITHUB_EVENT_PATH
    python3 scripts/ci/check_flip_path_review.py --repo o/r --pr 123   # local dry-run against a live
                                                                       # PR; needs $GITHUB_TOKEN
    python3 scripts/ci/check_flip_path_review.py --require-single-reviewer-covers-all

Deliberately NOT given a `make` target: every target in the Makefile is a zero-argument, repo-wide
static check that CI can run unattended, and this gate is neither (it needs a live PR number and a
token). A `flip-path-review-check` target would have to be the one target that cannot be run the way
all the others are, which is worse than no target.

STATE OF `.github/CODEOWNERS` (post-audit, 2026-08-13). The three findings this file originally
recorded as open were all acted on by the CODEOWNERS audit that lands before this gate; kept here,
updated, because they are the facts that determine what this gate actually buys:
  1. CLOSED — the gate apparatus now owns itself. `/.github/CODEOWNERS`, `/.github/workflows/`,
     `/scripts/ci/` and `/Makefile` are owned, so a PR that ONLY edits CODEOWNERS (touching no
     content path) can no longer sail through unreviewed and un-own the payload for the NEXT PR.
     Deliberately NOT owned, and recorded as such in that file: `/pyproject.toml` — a third
     suppression surface (ruff `select`, mypy `strict`), left out because it is this repo's
     dependency-bump file and gating it would flood the reviewer. Its compensating control (a
     config-pin test) is queued, not built.
  2. CLOSED, WITH THE REAL BLOCKER NOW VISIBLE — the phantom is gone. The file used to name
     `@rodrigotaquino`, which is not a GitHub account at all (GET /users → 404 with a full-scope
     token), so under `require_code_owner_review` every one of its lines would have been an EMPTY
     gate: the appearance of human ratification without the ratification. It now names
     `@rodaquino-OMNI`, the real account, plus `@Omni-Saude/security-team` (the real team handle;
     `@Omni-Saude/security` never existed either).
     WHAT REMAINS IS NOT A FILE PROBLEM, and no edit to CODEOWNERS can fix it:
       - `@rodaquino-OMNI` is the identity that AUTHORS the ratification PRs, and rule (c) forbids
         self-approval — so on those PRs the user-owner route to green does not exist;
       - `@Omni-Saude/security-team` has no access to this repo, and all 15 org teams have exactly
         one member, who is `@rodaquino-OMNI` — so the team route is unstaffed as well as invisible
         to the token. Team membership the token cannot read is UNVERIFIABLE ⇒ RED here, by design.
     Net: until the owner grants that team write access AND puts a second human in it, most lines
     are approvable only in principle. `@lucasreisEvah` (Legal/Compliance, write access) is on the
     three LGPD lines and is the one owner today who is not the PR author — those lines are the
     exception, pending the owner confirming the role. The gate names this case explicitly in its
     RED output rather than leaving the author staring at an unexplainable red check.
  3. UNCHANGED — the only pattern shapes present are rooted-directory (`/spec/policies/ans/`,
     `/scripts/ci/`) and rooted-exact-file (`/spec/processes/dmn/adequacao_gap.dmn`, `/Makefile`).
     No wildcards at all, before or after the audit. The parser supports more than that on purpose
     (reasonable evolutions), and REDs on anything beyond it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

GATE_NAME = "flip-path-review-gate"

#: GitHub looks for CODEOWNERS in these three locations, in this precedence order, and uses the
#: FIRST one it finds. Mirrored exactly so this gate and GitHub can never disagree about which file
#: is in force. Absent from all three at the base commit ⇒ RED (see `resolve_codeowners`).
CODEOWNERS_CANDIDATE_PATHS: tuple[str, ...] = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)

#: The only characters a pattern may contain. Anything else is an unrecognized shape ⇒ hard RED.
#: Notably excluded: `?` and `[`/`]` (globs this parser does not implement), `!` (negation, which
#: CODEOWNERS does not support anyway), `\` (escapes), and whitespace (patterns are whitespace-split).
_PATTERN_ALLOWED_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/*")

#: `@login` — GitHub logins: alphanumerics and single inner hyphens, 1..39 chars.
_USER_OWNER_RE = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
#: `@org/team-slug`.
_TEAM_OWNER_RE = re.compile(r"^@([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9][A-Za-z0-9._-]*)$")
#: `person@example.com` — permitted by CODEOWNERS, never verifiable from a review payload.
_EMAIL_OWNER_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GateError(Exception):
    """Any condition that must produce a RED verdict with a legible message.

    Every failure path in this module raises this (or returns a non-ok `Decision`); nothing is ever
    swallowed into a pass.
    """


class CodeownersParseError(GateError):
    """`.github/CODEOWNERS` could not be parsed with full confidence — fail-closed, never skipped."""


# ---------------------------------------------------------------------------------------------
# Pure core: CODEOWNERS parsing
# ---------------------------------------------------------------------------------------------


class OwnerKind(Enum):
    """How an owner token can (or cannot) be checked against a review's `user.login`."""

    USER = "user"
    TEAM = "team"
    EMAIL = "email"


@dataclass(frozen=True)
class Owner:
    """One owner token from a CODEOWNERS line."""

    raw: str
    kind: OwnerKind
    login: str | None = None  # USER only
    org: str | None = None  # TEAM only
    team: str | None = None  # TEAM only

    def describe(self) -> str:
        if self.kind is OwnerKind.TEAM:
            return f"{self.raw} (team)"
        if self.kind is OwnerKind.USER:
            return f"{self.raw} (user)"
        return f"{self.raw} (email — never verifiable from a review payload)"


@dataclass(frozen=True)
class Rule:
    """One parsed CODEOWNERS line: a pattern, its owners, and the compiled matcher."""

    pattern: str
    owners: tuple[Owner, ...]
    lineno: int
    regex: re.Pattern[str]

    def matches(self, path: str) -> bool:
        return self.regex.match(path) is not None

    def describe(self) -> str:
        owners = ", ".join(o.describe() for o in self.owners) if self.owners else "(no owners — UNSETS)"
        return f"L{self.lineno}  {self.pattern}  ->  {owners}"


def _translate_pattern_body(body: str) -> str:
    """Translate the slash-stripped pattern body into a regex fragment.

    Grammar (bounded on purpose — see the module docstring): literal characters, `*` (within one
    path segment), `**` (across segments), `**/` (zero or more whole directories).
    """
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        char = body[i]
        if char != "*":
            out.append(re.escape(char))
            i += 1
            continue
        if body.startswith("**", i):
            if body.startswith("**/", i):
                out.append("(?:.*/)?")  # zero or more complete directories
                i += 3
                continue
            out.append(".*")
            i += 2
            continue
        out.append("[^/]*")  # single-segment wildcard
        i += 1
    return "".join(out)


def _compile_pattern(pattern: str, lineno: int) -> re.Pattern[str]:
    """Compile one CODEOWNERS pattern, or raise `CodeownersParseError` (⇒ RED for the whole run).

    Refusing an unrecognized shape rather than skipping it is the point: a pattern this parser does
    not understand would otherwise turn an owned path invisible, which is a silent green.
    """
    if not pattern:
        raise CodeownersParseError(f"L{lineno}: empty pattern")

    illegal = sorted(set(pattern) - _PATTERN_ALLOWED_CHARS)
    if illegal:
        raise CodeownersParseError(
            f"L{lineno}: pattern {pattern!r} contains unsupported character(s) {illegal!r}. "
            f"{GATE_NAME} implements a bounded CODEOWNERS glob grammar (literals, `*`, `**`, leading "
            "`/`, trailing `/`) and fails CLOSED on anything else rather than silently ignoring a "
            "rule it cannot evaluate. Either express the rule within that grammar, or extend "
            "`_compile_pattern` in scripts/ci/check_flip_path_review.py (with tests) deliberately."
        )
    if "***" in pattern:
        raise CodeownersParseError(
            f"L{lineno}: pattern {pattern!r} contains `***`, which has no defined meaning — fail-closed."
        )

    anchored = "/" in pattern.rstrip("/")  # a slash anywhere but the tail anchors to the repo root
    dir_only = pattern.endswith("/")

    body = pattern[1:] if pattern.startswith("/") else pattern
    if body.endswith("/"):
        body = body[:-1]
    if not body:
        raise CodeownersParseError(f"L{lineno}: pattern {pattern!r} has no path body — fail-closed.")

    prefix = "" if anchored else "(?:.*/)?"
    # dir_only: must have something BENEATH the directory (a diff path is never a bare directory).
    # otherwise: the exact path, or — if it happens to be a directory — everything beneath it. The
    # more-ownership reading, which is the fail-closed one.
    suffix = "/.+" if dir_only else "(?:/.+)?"
    return re.compile(f"^{prefix}{_translate_pattern_body(body)}{suffix}$")


def _parse_owner(token: str, lineno: int) -> Owner:
    """Classify one owner token, or raise `CodeownersParseError` (⇒ RED)."""
    team_match = _TEAM_OWNER_RE.match(token)
    if team_match:
        return Owner(raw=token, kind=OwnerKind.TEAM, org=team_match.group(1), team=team_match.group(2))
    if _USER_OWNER_RE.match(token):
        return Owner(raw=token, kind=OwnerKind.USER, login=token[1:])
    if _EMAIL_OWNER_RE.match(token):
        return Owner(raw=token, kind=OwnerKind.EMAIL)
    raise CodeownersParseError(
        f"L{lineno}: owner token {token!r} is not a recognized `@user`, `@org/team` or email — "
        "fail-closed. An owner this gate cannot classify is an owner it cannot enforce."
    )


def parse_codeowners(text: str) -> tuple[Rule, ...]:
    """Parse CODEOWNERS text into rules in FILE ORDER (last match wins downstream).

    Blank lines and `#` comments are skipped. Every other line must yield a compilable pattern and
    classifiable owners, or the whole parse fails closed.
    """
    rules: list[Rule] = []
    for index, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        pattern, owner_tokens = fields[0], fields[1:]
        rules.append(
            Rule(
                pattern=pattern,
                owners=tuple(_parse_owner(token, index) for token in owner_tokens),
                lineno=index,
                regex=_compile_pattern(pattern, index),
            )
        )
    return tuple(rules)


def owners_for_path(rules: Sequence[Rule], path: str) -> Rule | None:
    """Return the LAST rule matching `path`, or `None` when the path is unowned.

    Last-match-wins is GitHub's documented precedence, and `.github/CODEOWNERS` explicitly relies on
    it twice (the `action-approvals.yaml` and `erasure-plan.template.yaml` comments both say a file is
    listed explicitly "porque CODEOWNERS resolve por ULTIMO-match"). A last match with NO owners
    UNSETS ownership, as GitHub does — so an intentional exclusion line is honoured, not overridden.
    """
    winner: Rule | None = None
    for rule in rules:
        if rule.matches(path):
            winner = rule
    if winner is None or not winner.owners:
        return None
    return winner


# ---------------------------------------------------------------------------------------------
# Pure core: reviews
# ---------------------------------------------------------------------------------------------

#: Review states that change a reviewer's standing. COMMENTED and PENDING deliberately do not:
#: GitHub does not treat either as an approval decision, and a PENDING review has not been submitted
#: at all (its `submitted_at` is null).
_STANDING_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})


@dataclass(frozen=True)
class Review:
    """One submitted review, normalized from the reviews API."""

    login: str
    state: str
    submitted_at: str
    review_id: int

    def sort_key(self) -> tuple[datetime, int]:
        """Chronological, tie-broken by the monotonically increasing review id."""
        try:
            when = datetime.fromisoformat(self.submitted_at)
        except (TypeError, ValueError) as exc:
            raise GateError(
                f"review {self.review_id} by @{self.login} has an unparseable submitted_at "
                f"{self.submitted_at!r} ({exc}) — cannot order reviews, fail-closed."
            ) from exc
        return (when, self.review_id)


def standing_reviews(reviews: Iterable[Review]) -> dict[str, Review]:
    """Reduce all reviews to the LATEST standing review per reviewer, keyed by lowercased login.

    A reviewer whose latest standing review is DISMISSED is dropped entirely: a dismissed approval
    must re-redden the check, which is exactly why the workflow also fires on
    `pull_request_review: [dismissed]`.
    """
    latest: dict[str, Review] = {}
    for review in sorted(
        (r for r in reviews if r.state.upper() in _STANDING_STATES), key=lambda r: r.sort_key()
    ):
        latest[review.login.lower()] = review
    return {login: review for login, review in latest.items() if review.state.upper() != "DISMISSED"}


# ---------------------------------------------------------------------------------------------
# Pure core: the decision
# ---------------------------------------------------------------------------------------------


class MembershipState(Enum):
    """Result of a team-membership probe. `UNVERIFIABLE` never satisfies anything."""

    ACTIVE = "active"
    NOT_MEMBER = "not-a-member"
    PENDING = "pending-invitation"
    UNVERIFIABLE = "UNVERIFIABLE"


#: `(org, team, login) -> MembershipState`. Injected so the core never touches the network.
MembershipResolver = Callable[[str, str, str], MembershipState]


@dataclass(frozen=True)
class OwnershipVerdict:
    """Why one reviewer does (or does not) satisfy one owned path."""

    satisfied: bool
    reason: str
    opaque: bool = False  # True when the answer is "we could not verify", not "no"


def _reviewer_satisfies(
    owners: Sequence[Owner], login: str, membership: MembershipResolver
) -> OwnershipVerdict:
    """Does `login` satisfy ANY of `owners`? Opacity is tracked separately from a clean "no"."""
    reasons: list[str] = []
    opaque = False
    for owner in owners:
        if owner.kind is OwnerKind.USER:
            if owner.login is not None and owner.login.lower() == login.lower():
                return OwnershipVerdict(True, f"explicit user owner {owner.raw}")
            reasons.append(f"not {owner.raw}")
        elif owner.kind is OwnerKind.TEAM:
            assert owner.org is not None and owner.team is not None  # guaranteed by _parse_owner
            state = membership(owner.org, owner.team, login)
            if state is MembershipState.ACTIVE:
                return OwnershipVerdict(True, f"verified ACTIVE member of {owner.raw}")
            if state is MembershipState.UNVERIFIABLE:
                opaque = True
                reasons.append(
                    f"{owner.raw} membership UNVERIFIABLE (GET /orgs/{owner.org}/teams/{owner.team}"
                    f"/memberships/{login} returned 403/404 or failed) — this token cannot see the "
                    "org's team rosters, so team ownership CANNOT be used to satisfy this path"
                )
            else:
                reasons.append(f"{owner.raw} membership is {state.value}, not active")
        else:  # EMAIL
            opaque = True
            reasons.append(
                f"{owner.raw} is an email owner — a review payload carries `user.login` and no "
                "email address, so an email owner can never be matched here"
            )
    return OwnershipVerdict(False, "; ".join(reasons) or "path has no owners", opaque=opaque)


@dataclass(frozen=True)
class PathOutcome:
    """Per-owned-path result."""

    path: str
    rule: Rule
    approved_by: tuple[str, ...] = ()
    rejections: tuple[str, ...] = ()
    opaque: bool = False

    @property
    def satisfied(self) -> bool:
        return bool(self.approved_by)


@dataclass
class Decision:
    """The gate verdict plus everything needed to render a legible message."""

    ok: bool
    headline: str
    detail_lines: list[str] = field(default_factory=list)

    def render(self) -> str:
        status = "GREEN" if self.ok else "RED"
        lines = [f"[{GATE_NAME}] {status} — {self.headline}"]
        lines.extend(self.detail_lines)
        return "\n".join(lines)


def _describe_owners(rule: Rule) -> str:
    return ", ".join(owner.describe() for owner in rule.owners)


def _unblock_hint(rule: Rule, author_login: str, opaque: bool) -> list[str]:
    """The 'what unblocks this' block for one unsatisfied path.

    When `opaque` (a team roster this token cannot read, or an email owner), the MANUAL OVERRIDE is
    stated explicitly: an explicitly-listed USER owner must approve. Naming it matters — a reader
    who only sees "team membership unverifiable" has been told why the check is red but not how to
    make it green, and an unactionable red check is how required checks end up bypassed.
    """
    hints: list[str] = []
    users = [o for o in rule.owners if o.kind is OwnerKind.USER]
    teams = [o for o in rule.owners if o.kind is OwnerKind.TEAM]
    non_author_users = [o for o in users if (o.login or "").lower() != author_login.lower()]
    for owner in non_author_users:
        hints.append(f"        an APPROVED review from {owner.raw} (explicit user owner)")
    for owner in teams:
        hints.append(
            f"        an APPROVED review from a VERIFIED ACTIVE member of {owner.raw} "
            "(requires this workflow's token to be able to read the org's team roster)"
        )
    if users and not non_author_users:
        hints.append(
            f"        NOTE: the ONLY explicit user owner(s) of this path — "
            f"{', '.join(o.raw for o in users)} — are the PR author. A self-approval never "
            "qualifies, so there is NO self-service route to green here: either a verified active "
            "team member approves, or a second user owner must be added to CODEOWNERS by a "
            "separate, reviewed PR."
        )
    # State the manual override whenever the only remaining route to green runs through an owner
    # this gate may not be able to verify: either opacity was actually OBSERVED while evaluating an
    # approval, or there is no non-author explicit user owner at all, so the reader's only hope is a
    # roster read that may well fail. The second case matters because `opaque` is only ever set by
    # evaluating a real approval — a team-owned path with ZERO reviews would otherwise say nothing.
    if opaque or (teams and not non_author_users):
        hints.append(
            "        MANUAL OVERRIDE (this path's team/email owners may be UNVERIFIABLE from this "
            "workflow's token): an explicit user owner must approve. If this path has no explicit "
            "user owner listed in CODEOWNERS, add one by a separate, reviewed PR — a team this "
            "gate cannot read is a team this gate will never accept, by design."
        )
    if not hints:
        hints.append(
            "        NOTHING this gate can verify. The path's only owners are unverifiable "
            "(email owners, or teams this token cannot read). Add an explicit user owner to "
            "CODEOWNERS, or grant the workflow a token that can read team membership."
        )
    return hints


def decide(
    *,
    codeowners_text: str,
    codeowners_source: str,
    changed_paths: Sequence[str],
    reviews: Sequence[Review],
    author_login: str,
    membership: MembershipResolver,
    require_single_reviewer_covers_all: bool = False,
    pr_label: str = "PR",
) -> Decision:
    """The whole contract, as a pure function. Never raises for ordinary input; RED instead.

    `codeowners_text` MUST come from the PR's BASE commit — see Decision 1 in the module docstring.
    """
    try:
        rules = parse_codeowners(codeowners_text)
    except CodeownersParseError as exc:
        return Decision(
            ok=False,
            headline="CODEOWNERS could not be parsed — fail-closed, never a silent green",
            detail_lines=[
                f"  source: {codeowners_source}",
                f"  error:  {exc}",
            ],
        )

    owned: list[tuple[str, Rule]] = []
    for path in sorted(set(changed_paths)):
        rule = owners_for_path(rules, path)
        if rule is not None:
            owned.append((path, rule))

    header = [
        f"  CODEOWNERS source: {codeowners_source} ({len(rules)} rule(s))",
        f"  {pr_label} author: @{author_login}",
        f"  changed paths: {len(set(changed_paths))}   owned: {len(owned)}",
    ]

    if not owned:
        return Decision(
            ok=True,
            headline="no CODEOWNERS-owned path touched — owner review is not required for this PR",
            detail_lines=header,
        )

    standing = standing_reviews(reviews)
    detail = list(header)
    detail.append("")
    detail.append("  Owned paths touched (last-matching CODEOWNERS rule wins):")

    outcomes: list[PathOutcome] = []
    coverage_by_reviewer: dict[str, set[str]] = {}
    for path, rule in owned:
        approvers: list[str] = []
        rejections: list[str] = []
        opaque = False
        for login, review in sorted(standing.items()):
            if review.state.upper() != "APPROVED":
                continue
            if login == author_login.lower():
                rejections.append(f"@{review.login}: APPROVED but IS THE PR AUTHOR — self-approval")
                continue
            verdict = _reviewer_satisfies(rule.owners, review.login, membership)
            opaque = opaque or verdict.opaque
            if verdict.satisfied:
                approvers.append(review.login)
                coverage_by_reviewer.setdefault(login, set()).add(path)
            else:
                rejections.append(f"@{review.login}: APPROVED but does not own this path — {verdict.reason}")
        outcomes.append(
            PathOutcome(
                path=path,
                rule=rule,
                approved_by=tuple(approvers),
                rejections=tuple(rejections),
                opaque=opaque,
            )
        )

    for outcome in outcomes:
        detail.append(f"    {outcome.path}")
        detail.append(f"        rule   {outcome.rule.describe()}")
        state = (
            f"SATISFIED by {', '.join('@' + a for a in outcome.approved_by)}"
            if outcome.satisfied
            else "UNSATISFIED"
        )
        detail.append(f"        status {state}")
        for rejection in outcome.rejections:
            detail.append(f"        reject {rejection}")

    detail.append("")
    detail.append("  Reviews considered (latest standing review per reviewer; COMMENTED/PENDING ignored):")
    if standing:
        for login in sorted(standing):
            review = standing[login]
            marker = " [PR AUTHOR]" if login == author_login.lower() else ""
            detail.append(
                f"    @{review.login:<24} {review.state}{marker}   (submitted {review.submitted_at})"
            )
    else:
        detail.append("    (none — no APPROVED / CHANGES_REQUESTED review stands on this PR)")
    dismissed = sorted(
        {r.login for r in reviews if r.state.upper() == "DISMISSED" and r.login.lower() not in standing}
    )
    for login in dismissed:
        detail.append(f"    @{login:<24} DISMISSED   (ignored — a dismissed review never counts)")

    # ---- Decision 2: a CHANGES_REQUESTED from an owner of a touched owned path blocks. ----
    blockers: list[str] = []
    for _login, review in sorted(standing.items()):
        if review.state.upper() != "CHANGES_REQUESTED":
            continue
        for path, rule in owned:
            verdict = _reviewer_satisfies(rule.owners, review.login, membership)
            if verdict.satisfied:
                blockers.append(f"@{review.login} requested CHANGES and owns {path} ({verdict.reason})")
                break
            if verdict.opaque:
                blockers.append(
                    f"@{review.login} requested CHANGES and MAY own {path} — {verdict.reason}. "
                    "Both directions of 'we cannot verify' resolve toward RED."
                )
                break

    if blockers:
        detail.append("")
        detail.append("  Blocking CHANGES_REQUESTED (an owner's live objection outranks any approval):")
        detail.extend(f"    - {b}" for b in blockers)
        detail.append("")
        detail.append("  What unblocks this check:")
        detail.append("    - the objecting owner submits an APPROVED review, OR someone with write")
        detail.append("      access dismisses their review (both leave an audit trail).")
        return Decision(
            ok=False,
            headline="an owner of a touched owned path has requested changes",
            detail_lines=detail,
        )

    unsatisfied = [o for o in outcomes if not o.satisfied]
    if unsatisfied:
        detail.append("")
        detail.append("  What unblocks this check:")
        for outcome in unsatisfied:
            detail.append(f"    - {outcome.path} needs ONE of:")
            detail.extend(_unblock_hint(outcome.rule, author_login, outcome.opaque))
        if any(o.opaque for o in unsatisfied):
            detail.append("")
            detail.append(
                "  OPACITY NOTE: at least one path could only have been satisfied by an owner this "
                "gate cannot verify (a team roster the token cannot read, or an email owner). That "
                "is reported as UNSATISFIED and never downgraded to 'any non-author approval will "
                "do' — the downgrade is the vulnerability this gate exists to prevent."
            )
        return Decision(
            ok=False,
            headline=(
                f"{len(unsatisfied)} of {len(outcomes)} touched owned path(s) lack a qualified owner approval"
            ),
            detail_lines=detail,
        )

    if require_single_reviewer_covers_all:
        all_paths = {path for path, _ in owned}
        covering = sorted(login for login, paths in coverage_by_reviewer.items() if paths == all_paths)
        if not covering:
            detail.append("")
            detail.append(
                "  --require-single-reviewer-covers-all is ON: every touched owned path is covered, "
                "but NO SINGLE reviewer covers all of them."
            )
            for login in sorted(coverage_by_reviewer):
                detail.append(f"    @{login} covers {len(coverage_by_reviewer[login])}/{len(all_paths)}")
            return Decision(
                ok=False,
                headline="no single qualified reviewer covers every touched owned path",
                detail_lines=detail,
            )

    return Decision(
        ok=True,
        headline=f"all {len(outcomes)} touched owned path(s) carry a qualified owner approval",
        detail_lines=detail,
    )


# ---------------------------------------------------------------------------------------------
# API shell
# ---------------------------------------------------------------------------------------------


class GitHubAPI:
    """Thin, stdlib-only REST client. Every non-2xx becomes an explicit, named outcome — never a pass.

    `urllib` rather than `gh api` on purpose: this gate's correctness turns on distinguishing a
    200/`active` from a 200/`pending` from a 403/404, and `HTTPError.code` gives that directly where
    `gh api` would give an exit code and a prose stderr line.
    """

    def __init__(self, repo: str, token: str, api_root: str = "https://api.github.com") -> None:
        self.repo = repo
        self._token = token
        self._api_root = api_root.rstrip("/")
        self._membership_cache: dict[tuple[str, str, str], MembershipState] = {}

    def _request(self, path: str) -> tuple[int, Any]:
        """GET `path`. Returns `(status, parsed_json)`; a non-2xx returns `(status, None)`."""
        request = urllib.request.Request(  # noqa: S310 — fixed https api root, not user input
            f"{self._api_root}/{path.lstrip('/')}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": GATE_NAME,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GateError(f"GET {path} failed at the transport layer ({exc}) — fail-closed.") from exc

    def _paginate(self, path: str) -> list[Any]:
        """Page a list endpoint explicitly (deterministic; no dependence on `gh --paginate` quirks)."""
        items: list[Any] = []
        for page in range(1, 51):  # 50 * 100 = 5000 items; a PR beyond that is not a review problem
            joiner = "&" if "?" in path else "?"
            status, payload = self._request(f"{path}{joiner}per_page=100&page={page}")
            if status != 200 or not isinstance(payload, list):
                raise GateError(
                    f"GET {path} (page {page}) returned HTTP {status} — the gate cannot enumerate "
                    "this PR's state, so it fails CLOSED rather than assuming an empty list."
                )
            items.extend(payload)
            if len(payload) < 100:
                return items
        raise GateError(f"GET {path} exceeded 50 pages — refusing to guess, fail-closed.")

    def file_at(self, ref: str, path: str) -> str | None:
        """Fetch a file's text at a specific commit. `None` iff the API says 404 (absent)."""
        quoted = urllib.parse.quote(path)
        status, payload = self._request(f"repos/{self.repo}/contents/{quoted}?ref={ref}")
        if status == 404:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise GateError(
                f"GET contents/{path}@{ref} returned HTTP {status} — cannot read the base commit's "
                "CODEOWNERS, so the gate cannot prove which paths are owned. Fail-closed."
            )
        if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
            raise GateError(
                f"contents/{path}@{ref} came back in an unexpected encoding "
                f"({payload.get('encoding')!r}) — fail-closed."
            )
        return base64.b64decode(payload["content"]).decode("utf-8")

    def pull_request(self, pr_number: int) -> EventContext:
        """Fetch the PR's base sha + author directly, for the no-event-payload (local dry-run) mode."""
        status, payload = self._request(f"repos/{self.repo}/pulls/{pr_number}")
        if status != 200 or not isinstance(payload, dict):
            raise GateError(f"GET pulls/{pr_number} returned HTTP {status} — fail-closed.")
        base = payload.get("base") or {}
        user = payload.get("user") or {}
        base_sha = base.get("sha") if isinstance(base, dict) else None
        author = user.get("login") if isinstance(user, dict) else None
        if not base_sha or not author:
            raise GateError(f"pulls/{pr_number} has no readable base.sha / user.login — fail-closed.")
        return EventContext(
            repo=self.repo, pr_number=pr_number, base_sha=str(base_sha), author_login=str(author)
        )

    def changed_paths(self, pr_number: int) -> list[str]:
        """Every path the diff touches, INCLUDING a rename's previous path.

        A rename out of an owned directory is a change to an owned path; counting only the new name
        would let `git mv spec/policies/autonomy/x.yaml /tmp-ish/x.yaml` escape ownership.
        """
        paths: list[str] = []
        for entry in self._paginate(f"repos/{self.repo}/pulls/{pr_number}/files"):
            if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
                raise GateError(f"unexpected entry in pulls/{pr_number}/files: {entry!r} — fail-closed.")
            paths.append(entry["filename"])
            previous = entry.get("previous_filename")
            if isinstance(previous, str) and previous:
                paths.append(previous)
        return paths

    def reviews(self, pr_number: int) -> list[Review]:
        """Every submitted review, normalized. A malformed entry is RED, not a skip."""
        out: list[Review] = []
        for entry in self._paginate(f"repos/{self.repo}/pulls/{pr_number}/reviews"):
            if not isinstance(entry, dict):
                raise GateError(f"unexpected entry in pulls/{pr_number}/reviews: {entry!r} — fail-closed.")
            user = entry.get("user") or {}
            login = user.get("login") if isinstance(user, dict) else None
            state = entry.get("state")
            if not isinstance(login, str) or not isinstance(state, str):
                raise GateError(f"review {entry.get('id')!r} has no readable user.login/state — fail-closed.")
            if state.upper() not in _STANDING_STATES:
                continue  # COMMENTED / PENDING: never a standing approval decision
            submitted = entry.get("submitted_at")
            if not isinstance(submitted, str):
                raise GateError(
                    f"review {entry.get('id')!r} by @{login} is {state} with no submitted_at — "
                    "reviews cannot be ordered, fail-closed."
                )
            out.append(
                Review(
                    login=login,
                    state=state.upper(),
                    submitted_at=submitted.replace("Z", "+00:00"),
                    review_id=int(entry.get("id") or 0),
                )
            )
        return out

    def membership(self, org: str, team: str, login: str) -> MembershipState:
        """`GET /orgs/{org}/teams/{team}/memberships/{login}`, cached.

        403/404 (and anything else non-200) is UNVERIFIABLE, never "not a member": a 404 from this
        endpoint is genuinely ambiguous — it is returned both for a real non-member AND for a token
        that cannot see the org at all. Only a 200 with `state == "active"` is membership.
        """
        key = (org.lower(), team.lower(), login.lower())
        if key in self._membership_cache:
            return self._membership_cache[key]
        try:
            status, payload = self._request(
                f"orgs/{urllib.parse.quote(org)}/teams/{urllib.parse.quote(team)}"
                f"/memberships/{urllib.parse.quote(login)}"
            )
        except GateError:
            state = MembershipState.UNVERIFIABLE
        else:
            if status == 200 and isinstance(payload, dict):
                raw = str(payload.get("state", "")).lower()
                state = (
                    MembershipState.ACTIVE
                    if raw == "active"
                    else MembershipState.PENDING
                    if raw == "pending"
                    else MembershipState.UNVERIFIABLE
                )
            else:
                state = MembershipState.UNVERIFIABLE
        self._membership_cache[key] = state
        return state


def resolve_codeowners(fetch: Callable[[str, str], str | None], base_sha: str) -> tuple[str, str]:
    """Return `(source_label, text)` for the CODEOWNERS in force at `base_sha`.

    GitHub's precedence order is mirrored exactly (`.github/`, root, `docs/`) so the two can never
    disagree about which file governs. Absent from all three ⇒ RED: a repo with no CODEOWNERS at the
    base commit is a repo whose owned-path set this gate cannot compute, and "cannot compute" is
    never "nothing is owned".
    """
    for candidate in CODEOWNERS_CANDIDATE_PATHS:
        text = fetch(base_sha, candidate)
        if text is not None:
            return f"{candidate} @ base {base_sha[:12]}", text
    raise GateError(
        f"no CODEOWNERS found at base commit {base_sha} in any of "
        f"{', '.join(CODEOWNERS_CANDIDATE_PATHS)} — fail-closed. The gate cannot prove that this PR "
        "touches no owned path if it cannot read the ownership rules at all."
    )


@dataclass(frozen=True)
class EventContext:
    """The pull-request facts the gate needs, from the Actions event payload."""

    repo: str
    pr_number: int
    base_sha: str
    author_login: str


def load_event_context(event_path: Path, repo_override: str | None) -> EventContext:
    """Read `$GITHUB_EVENT_PATH`. Works for both `pull_request` and `pull_request_review` events.

    Both payloads carry the same `pull_request` object, which is why one script serves both triggers.
    Anything missing or malformed is RED — a gate that cannot identify the PR it is judging must not
    pass it.
    """
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"could not read the event payload at {event_path} ({exc}) — fail-closed.") from exc
    if not isinstance(payload, dict):
        raise GateError(f"event payload at {event_path} is not a JSON object — fail-closed.")

    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise GateError(
            "event payload has no `pull_request` object — this gate only runs on `pull_request` and "
            "`pull_request_review` events. Fail-closed."
        )

    number = pull_request.get("number")
    base = pull_request.get("base") or {}
    base_sha = base.get("sha") if isinstance(base, dict) else None
    user = pull_request.get("user") or {}
    author = user.get("login") if isinstance(user, dict) else None
    repo_full = repo_override or (payload.get("repository") or {}).get("full_name")

    missing = [
        name
        for name, value in (
            ("pull_request.number", number),
            ("pull_request.base.sha", base_sha),
            ("pull_request.user.login", author),
            ("repository.full_name", repo_full),
        )
        if not value
    ]
    if missing:
        raise GateError(f"event payload is missing {', '.join(missing)} — fail-closed.")

    return EventContext(
        repo=str(repo_full),
        pr_number=int(number),  # type: ignore[arg-type]
        base_sha=str(base_sha),
        author_login=str(author),
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_flip_path_review",
        description=(
            "Fail-closed CI gate for owner decision Q-1: a PR touching any path owned by the BASE "
            "commit's CODEOWNERS must carry an APPROVED review from a qualified owner who is not "
            "the PR author."
        ),
    )
    parser.add_argument(
        "--event-path",
        default=os.environ.get("GITHUB_EVENT_PATH"),
        help="Actions event payload JSON (default: $GITHUB_EVENT_PATH).",
    )
    parser.add_argument("--repo", default=None, help="owner/repo override (default: from the event payload).")
    parser.add_argument(
        "--pr", type=int, default=None, help="PR number override (default: from the event payload)."
    )
    parser.add_argument(
        "--require-single-reviewer-covers-all",
        action="store_true",
        help=(
            "Stricter than GitHub: demand that ONE reviewer satisfy EVERY touched owned path, rather "
            "than each path being covered by some owner. Off by default — see Decision 3."
        ),
    )
    return parser


def _emit(decision_text: str) -> None:
    """Print, and mirror into the job summary when running under Actions."""
    print(decision_text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"## {GATE_NAME}\n\n```\n{decision_text}\n```\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. 0 = green, 1 = RED. Never exits non-1 on a policy failure, never exits 0 on doubt."""
    args = build_arg_parser().parse_args(argv)
    try:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            raise GateError(
                "no $GITHUB_TOKEN/$GH_TOKEN in the environment — the gate cannot read the PR's files "
                "or reviews. Fail-closed. (On a fork PR the workflow token is read-only but still "
                "present; a MISSING token means the workflow is misconfigured.)"
            )

        if args.event_path:
            # Normal CI path: everything comes from the Actions event payload.
            context = load_event_context(Path(args.event_path), args.repo)
            pr_number = args.pr or context.pr_number
            api = GitHubAPI(repo=context.repo, token=token)
        elif args.repo and args.pr:
            # Local dry-run path: no event payload, so the PR's base sha and author are fetched
            # directly. Same evaluation, same fail-closed rules — only the context source differs.
            api = GitHubAPI(repo=args.repo, token=token)
            context = api.pull_request(args.pr)
            pr_number = args.pr
        else:
            raise GateError(
                "no --event-path / $GITHUB_EVENT_PATH, and no --repo + --pr to fall back on — "
                "the gate cannot identify the PR it is judging. Fail-closed."
            )

        source, codeowners_text = resolve_codeowners(api.file_at, context.base_sha)
        decision = decide(
            codeowners_text=codeowners_text,
            codeowners_source=source,
            changed_paths=api.changed_paths(pr_number),
            reviews=api.reviews(pr_number),
            author_login=context.author_login,
            membership=api.membership,
            require_single_reviewer_covers_all=args.require_single_reviewer_covers_all,
            pr_label=f"PR #{pr_number}",
        )
    except GateError as exc:
        _emit(f"[{GATE_NAME}] RED — {exc}")
        return 1

    _emit(decision.render())
    return 0 if decision.ok else 1


if __name__ == "__main__":
    sys.exit(main())
