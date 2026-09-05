#!/usr/bin/env python3
"""Preflight gate: refuse a production promotion without PROOF of a named reviewer's approval.

WHY THIS EXISTS (owner decision R-021, option B — 2026-09-04)
-------------------------------------------------------------
`.github/workflows/cd.yml`'s `promote-production` job declares `environment: production`, and for a
long time three passages of that file said the environment gated the job on GitHub's manual-approval
reviewers. It never did. Measured, not presumed::

    GET /repos/Omni-Saude/maezo-operadora/environments/production
    -> {"name": "production", "protection_rules": [], "can_admins_bypass": true, ...}

An environment with no protection rules does not pause a run for anyone; it only names a scope for
variables and secrets. Applying the required-reviewers rule was attempted and refused twice with
HTTP 422: the `Omni-Saude` org is on the *team* plan with a private repository, a combination that
permits environments but NOT required-reviewer protection (that needs Enterprise Cloud). So the only
barrier in front of a real `helm upgrade` against production was `AWS_ENABLED` being unset — a gate
by OMISSION, which fails precisely on the day someone sets `AWS_ENABLED=true`, the day of most haste.

This script is the CONSTRUCTED gate that takes the required reviewers' place. It is fail-closed by
construction: it exits non-zero unless it can point at a specific, currently-standing `APPROVED`
review, submitted by a login listed in a CODEOWNED file, on the head commit of the pull request that
carries the commit being promoted.

THE FOUR PROPERTIES THAT MAKE THIS A GATE AND NOT THEATRE
---------------------------------------------------------
1. DEFAULT IS DENY. Every path that does not END in a matched approval returns non-zero: no
   approvers file, unparseable approvers file, no token, unresolvable promotion ref, no pull request
   carrying the commit, a pull request that was CLOSED WITHOUT MERGING (its approval describes a
   change the repository did not take), a merged pull request whose merge commit is not the promoted
   commit, an open pull request whose head is not the promoted commit, no reviews, a review from an
   unlisted login, a stale approval, an API error, an unexpected payload shape. There is no
   `except: pass`, no `or True`, no default-allow branch.

2. THE ENVIRONMENT PROBE IS TELEMETRY, NEVER A CONDITION. `GET /environments/production` is called
   and printed so the run's log records what the environment actually looked like at promotion time
   — and its result is computed AFTER the verdict and can only ever be logged. This is the whole
   point of the owner's `floor_note`: "a maquina nao pode se auto-certificar aprovada". If a future
   plan upgrade ever populates `protection_rules`, the honest response is to read this line and
   decide deliberately, not to have the machine quietly start trusting its own environment config.

3. THE APPROVAL BINDS TO A COMMIT, NOT TO A PERSON'S GENERAL BLESSING. A reviewer's `APPROVED`
   counts only when its `commit_id` equals the pull request's CURRENT head sha. An approval given
   before a later push is STALE and does not carry over — that is the same "approval that no longer
   describes the code" failure mode `check_flip_path_review.py` guards against on the merge path.

4. THE REVIEWER LIST IS CODEOWNED BY AN EXPLICIT RULE, AND ITS GRAMMAR IS NARROW.
   `.github/CODEOWNERS` carries the rule `/.github/production-approvers.yaml` by name, so widening
   the list is itself a reviewed change. The rule is named rather than inherited: this repository has
   NO `/.github/` glob — its `.github/` rules are enumerated one by one — and the first version of
   this docstring claimed the glob-shaped ownership it does not have (adversarial-review finding,
   corrected 2026-09-04 together with the missing rule). `test_the_approvers_file_lives_under_a_
   codeowned_path` now resolves the path against the parsed rules with this repository's own
   CODEOWNERS matcher, so a rename or a deleted rule turns it RED instead of passing on a prefix
   coincidence. Being owned REQUESTS a reviewer; requiring one is `require_code_owner_review` (false
   today — R-051) plus the `flip-path-review-gate` check, which reads the same file. The parser here
   accepts a deliberately tiny YAML subset and treats everything else as a hard parse error, because
   a gate that reads a list it did not fully understand is a gate that can approve by accident.

WHAT THIS GATE DOES *NOT* CLAIM
-------------------------------
* It does not make itself a required status check — AND that is not a missing step, it is a
  category error to attempt. `require-production-approval` runs only on `workflow_dispatch` with
  `promote_to_production`, so it never reports on a pull request; adding its context to
  `main-protection` would leave every PR at "Expected — waiting for status to be reported", because
  GitHub reads a never-reported required check as pending rather than red. The binding this gate
  actually has is the one that matters where promotion happens: `promote-production` carries
  `needs: [require-production-approval]` and an `if:` that requires
  `needs.require-production-approval.result == 'success'`, so no promotion runs without a green
  verdict, ruleset or no ruleset. Merge-time protection of this apparatus (`cd.yml`, this script,
  the approvers file) is CODEOWNERS plus the `flip-path-review-gate` check. The full reasoning, with
  the measurements behind it, is in `docs/review-queue.md`, section `## WF-BATCH`.
* It does not assert reviewer COMPETENCE. Being listed is authority to promote, not an attestation
  that the listed human reviewed clinical or regulatory content; that stays in `docs/review-queue.md`.
* It does not attempt to detect self-approval. GitHub itself refuses to record an author's approval
  of their own pull request, so a self-approval cannot appear in the reviews payload at all.

CONTEXT PRECEDENCE
------------------
1. Explicit flags (`--repo`, `--sha`, `--image-tag`, `--approvers-file`, `--api-root`) always win —
   this is how the unit tests and a local dry run drive the script.
2. Otherwise the GitHub Actions environment supplies them: `GITHUB_REPOSITORY`, `GITHUB_SHA`,
   `PROMOTION_IMAGE_TAG`. The token comes only from `GITHUB_TOKEN` (never a flag: a token on a
   command line lands in process listings and in the run log).

Exit codes: 0 = a matching approval was proven; 1 = denied (any reason, including every error);
2 = usage error. Anything other than 0 must block the promotion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

GATE_NAME: Final = "require-production-approval"

#: Repository-relative default for the CODEOWNED reviewer list.
DEFAULT_APPROVERS_FILE: Final = ".github/production-approvers.yaml"

#: The environment whose configuration is logged as telemetry (never as a condition).
TELEMETRY_ENVIRONMENT: Final = "production"

#: Review states GitHub treats as a standing decision. COMMENTED and PENDING are neither.
_STANDING_STATES: Final = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})

#: GitHub login grammar: alphanumerics separated by single hyphens, 1-39 chars, no leading or
#: trailing hyphen. Deliberately strict — an entry this does not match is a parse error, not a
#: silently-ignored line.
_LOGIN_RE: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")

#: The ONLY list-item shape the approvers file may use: exactly two spaces, `- `, one login, and an
#: optional trailing comment. Flow sequences, quotes, anchors and nested maps are all parse errors.
_ITEM_RE: Final = re.compile(rf"^  - (?P<login>{_LOGIN_RE.pattern})(?:[ \t]+#.*)?$")

_APPROVERS_KEY: Final = "approvers:"


class ApprovalGateError(Exception):
    """Any condition that prevents PROVING an approval. Always rendered as a denial, never a pass."""


class ApproversFileError(ApprovalGateError):
    """The reviewer list could not be read or did not match the accepted grammar."""


# ---------------------------------------------------------------------------------------------
# The reviewer list
# ---------------------------------------------------------------------------------------------


def parse_approvers(text: str) -> tuple[str, ...]:
    """Parse the narrow YAML subset of `.github/production-approvers.yaml`.

    Accepted, and nothing else: blank lines; whole-line `#` comments; the bare key `approvers:`
    exactly once; and `  - <login>` items with an optional trailing ` # comment`.

    Every rejection below is deliberate rather than defensive: a real YAML loader would happily
    accept an anchor, a flow sequence or a second top-level key and hand back a list that no human
    reviewing this file expected. The gate would then be enforcing a list nobody read. Refusing to
    parse is the fail-closed answer, because a parse error reaches `main` as a DENIAL.
    """
    approvers: list[str] = []
    seen_key = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        if line == _APPROVERS_KEY:
            if seen_key:
                raise ApproversFileError(
                    f"line {lineno}: a second `{_APPROVERS_KEY}` key — two lists mean the gate would "
                    "have to guess which one is authoritative. Fail-closed."
                )
            seen_key = True
            continue
        match = _ITEM_RE.match(line)
        if not match:
            raise ApproversFileError(
                f"line {lineno}: {line!r} is not one of the four accepted forms (blank, whole-line "
                "comment, the bare key `approvers:`, or `  - <login>`). The approvers grammar is "
                "deliberately narrow; anything else is a parse error, never an ignored line."
            )
        if not seen_key:
            raise ApproversFileError(
                f"line {lineno}: list item before the `{_APPROVERS_KEY}` key — the file must declare "
                "the key it is populating."
            )
        login = match.group("login")
        if any(login.lower() == existing.lower() for existing in approvers):
            raise ApproversFileError(
                f"line {lineno}: @{login} is listed twice — a duplicated entry is a sign the list "
                "was edited without being read. Fail-closed."
            )
        approvers.append(login)
    if not seen_key:
        raise ApproversFileError(
            f"no `{_APPROVERS_KEY}` key found — an approvers file without the key is not an empty "
            "list, it is an unreadable one."
        )
    if not approvers:
        raise ApproversFileError(
            "the approvers list is empty — with nobody able to approve, no promotion can ever be "
            "proven approved, so the gate denies rather than pretending the list is satisfied."
        )
    return tuple(approvers)


def load_approvers(path: Path) -> tuple[str, ...]:
    """Read and parse the reviewer list. A missing or unreadable file is a DENIAL, not an empty list."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApproversFileError(
            f"could not read the approvers file {path} ({exc}) — without the CODEOWNED reviewer "
            "list there is nothing to match an approval against. Fail-closed."
        ) from exc
    return parse_approvers(text)


# ---------------------------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Review:
    """One submitted review, normalized from the reviews API."""

    login: str
    state: str
    commit_id: str
    submitted_at: str
    review_id: int

    def sort_key(self) -> tuple[datetime, int]:
        """Chronological, tie-broken by the monotonically increasing review id."""
        try:
            when = datetime.fromisoformat(self.submitted_at)
        except (TypeError, ValueError) as exc:
            raise ApprovalGateError(
                f"review {self.review_id} by @{self.login} has an unparseable submitted_at "
                f"{self.submitted_at!r} ({exc}) — reviews cannot be ordered, fail-closed."
            ) from exc
        return (when, self.review_id)


def standing_reviews(reviews: Iterable[Review]) -> dict[str, Review]:
    """Reduce to the LATEST standing review per reviewer, keyed by lowercased login.

    A reviewer whose latest standing review is DISMISSED is dropped entirely: a dismissed approval
    must stop counting immediately, otherwise "approved once" would outlive the approval itself.
    Same reduction as `check_flip_path_review.standing_reviews`, kept separate on purpose — this
    gate must not acquire an import-time dependency on the merge-path gate, so that breaking one
    can never silently loosen the other.
    """
    latest: dict[str, Review] = {}
    for review in sorted(
        (r for r in reviews if r.state.upper() in _STANDING_STATES), key=lambda r: r.sort_key()
    ):
        latest[review.login.lower()] = review
    return {login: review for login, review in latest.items() if review.state.upper() != "DISMISSED"}


@dataclass(frozen=True)
class PullRequest:
    """The pull request that carries the commit being promoted.

    `state` and `merged` are not decoration: `decide` reads them, because an approval on a pull
    request that was CLOSED WITHOUT MERGING is an approval of a change the repository rejected.
    """

    number: int
    head_sha: str
    state: str
    merged: bool
    merge_commit_sha: str | None = None


def carriage_miss(pull: PullRequest, promotion_sha: str) -> str | None:
    """Why `pull` cannot authorise promoting `promotion_sha`, or `None` when it can.

    Only two shapes carry a promotion, and both are stated positively so that anything unforeseen
    falls through to a denial:

    * MERGED, and the promoted commit is its `merge_commit_sha` — the ordinary path. The commit that
      lands on `main` (and that CD builds an image from) is the merge/squash/rebase commit GitHub
      created, not the branch head.
    * OPEN, and the promoted commit is the pull request's current head — promoting a branch build
      whose head is still under review, which the reviews check then binds to that same head.

    Everything else is refused, and the case that made this function necessary is the third one:
    CLOSED and NOT merged. A change that was reviewed, approved and then abandoned or rejected still
    has a standing `APPROVED` review on its head; without this check that review authorised
    promoting its head sha to production (fail-open found in adversarial review, 2026-09-04). A
    merged pull request whose HEAD sha (rather than its merge commit) is being promoted is refused
    for the same reason the stale-approval rule exists: the artifact being promoted is then not the
    artifact the merge produced.
    """
    if pull.merged:
        if pull.merge_commit_sha == promotion_sha:
            return None
        return (
            f"PR #{pull.number}: merged, but its merge commit is "
            f"{(pull.merge_commit_sha or '(none)')[:12]}, not the promoted {promotion_sha[:12]} — "
            "only the commit the merge produced is what this pull request put on the base branch."
        )
    if pull.state.lower() == "open":
        if pull.head_sha == promotion_sha:
            return None
        return (
            f"PR #{pull.number}: open, but its head is {pull.head_sha[:12]}, not the promoted "
            f"{promotion_sha[:12]} — an open pull request authorises only the commit it currently "
            "carries at its head."
        )
    return (
        f"PR #{pull.number}: state={pull.state!r} and NOT merged — an approval on a pull request "
        "that was closed without merging describes a change this repository did not take. It "
        "authorises no promotion."
    )


@dataclass(frozen=True)
class Verdict:
    """The gate's decision. `approved` is set ONLY on a proven, commit-bound approval."""

    approved: bool
    headline: str
    detail: tuple[str, ...] = ()

    def render(self) -> str:
        status = "GREEN" if self.approved else "RED"
        lines = [f"[{GATE_NAME}] {status} — {self.headline}"]
        lines.extend(f"    {line}" for line in self.detail)
        return "\n".join(lines)


def decide(
    *,
    promotion_sha: str,
    approvers: Sequence[str],
    pull_requests: Sequence[PullRequest],
    reviews_by_pr: dict[int, Sequence[Review]],
) -> Verdict:
    """Pure core: does a currently-standing, commit-bound approval by a listed login exist?

    Separated from every API call so the whole decision table is unit-testable without a network,
    and so the telemetry probe in `main` is structurally incapable of reaching it.
    """
    allowed = {login.lower(): login for login in approvers}
    if not pull_requests:
        return Verdict(
            approved=False,
            headline=f"no pull request carries commit {promotion_sha[:12]}.",
            detail=(
                "An approval is proven against the review record of the PR that introduced the",
                "commit. A commit with no such PR (direct push, or a PR in another repository)",
                "has no review record here, so the promotion cannot be proven approved.",
            ),
        )
    misses: list[str] = []
    for pull in pull_requests:
        carriage = carriage_miss(pull, promotion_sha)
        if carriage is not None:
            misses.append(carriage)
            continue
        standing = standing_reviews(reviews_by_pr.get(pull.number, ()))
        for login_lower, review in sorted(standing.items()):
            if login_lower not in allowed:
                misses.append(
                    f"PR #{pull.number}: @{review.login} is {review.state} but is not listed in the "
                    "approvers file."
                )
                continue
            if review.state.upper() != "APPROVED":
                misses.append(
                    f"PR #{pull.number}: @{review.login} is listed, but their standing review is "
                    f"{review.state}, not APPROVED."
                )
                continue
            if review.commit_id != pull.head_sha:
                misses.append(
                    f"PR #{pull.number}: @{review.login} APPROVED commit "
                    f"{review.commit_id[:12] or '(none)'}, which is not the PR's head "
                    f"{pull.head_sha[:12]} — a stale approval does not carry over a later push."
                )
                continue
            return Verdict(
                approved=True,
                headline=(
                    f"@{review.login} (listed approver) APPROVED PR #{pull.number} at its head "
                    f"{pull.head_sha[:12]}, which carries the promoted commit {promotion_sha[:12]}."
                ),
                detail=(f"review id {review.review_id}, submitted at {review.submitted_at}.",),
            )
        if not standing:
            misses.append(f"PR #{pull.number}: no standing review at all.")
    return Verdict(
        approved=False,
        headline=(
            f"no standing APPROVED review by a listed approver on the head of any pull request "
            f"carrying commit {promotion_sha[:12]}."
        ),
        detail=(
            *misses,
            f"Listed approvers: {', '.join('@' + login for login in approvers)} "
            f"({DEFAULT_APPROVERS_FILE}, CODEOWNED).",
        ),
    )


# ---------------------------------------------------------------------------------------------
# API shell
# ---------------------------------------------------------------------------------------------


class GitHubAPI:
    """Thin, stdlib-only REST client. Every non-2xx becomes a raised error — never a pass.

    `urllib` rather than `gh api` for the same reason `check_flip_path_review.py` gives: the
    verdict turns on distinguishing statuses, and `HTTPError.code` gives that directly.
    """

    def __init__(self, repo: str, token: str, api_root: str = "https://api.github.com") -> None:
        self.repo = repo
        self._token = token
        self._api_root = api_root.rstrip("/")

    def _request(self, path: str) -> tuple[int, Any]:
        """GET `path`. Returns `(status, parsed_json)`; a non-2xx returns `(status, None)`."""
        # Fixed https API root joined with a quoted path — never a user-supplied scheme.
        request = urllib.request.Request(
            f"{self._api_root}/{path.lstrip('/')}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": GATE_NAME,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ApprovalGateError(
                f"GET {path} failed at the transport layer ({exc}) — fail-closed."
            ) from exc

    def _paginate(self, path: str) -> list[Any]:
        """Page a list endpoint explicitly. A non-200 raises; it is never read as an empty list."""
        items: list[Any] = []
        for page in range(1, 51):
            joiner = "&" if "?" in path else "?"
            status, payload = self._request(f"{path}{joiner}per_page=100&page={page}")
            if status != 200 or not isinstance(payload, list):
                raise ApprovalGateError(
                    f"GET {path} (page {page}) returned HTTP {status} — the gate cannot enumerate "
                    "this promotion's approval state, so it denies rather than assuming an empty list."
                )
            items.extend(payload)
            if len(payload) < 100:
                return items
        raise ApprovalGateError(f"GET {path} exceeded 50 pages — refusing to guess, fail-closed.")

    def resolve_commit(self, ref: str) -> str:
        """Resolve a ref (sha, abbreviated sha, tag or branch) to a FULL commit sha.

        An image tag that does not name a commit is a hard denial rather than a warning: the whole
        gate rests on binding an approval to the artifact being promoted, and an opaque tag severs
        that binding. Promoting such a tag needs a deliberate, separately reviewed change here.
        """
        status, payload = self._request(f"repos/{self.repo}/commits/{urllib.parse.quote(ref)}")
        if status != 200 or not isinstance(payload, dict):
            raise ApprovalGateError(
                f"GET commits/{ref} returned HTTP {status} — the promoted ref does not resolve to a "
                "commit in this repository, so no approval can be bound to it. Fail-closed."
            )
        sha = payload.get("sha")
        if not isinstance(sha, str) or not sha:
            raise ApprovalGateError(f"commits/{ref} came back without a `sha` — fail-closed.")
        return sha

    def pulls_for_commit(self, sha: str) -> list[PullRequest]:
        """PRs that carry `sha` — accepted only when the commit is the PR's head or merge commit.

        `GET /commits/{sha}/pulls` also lists PRs that merely CONTAIN the commit somewhere in their
        branch. Accepting those would let an approval of an unrelated, larger PR authorize the
        promotion of a commit nobody approved at head, so they are filtered out here.
        """
        out: list[PullRequest] = []
        for entry in self._paginate(f"repos/{self.repo}/commits/{urllib.parse.quote(sha)}/pulls"):
            if not isinstance(entry, dict):
                raise ApprovalGateError(f"unexpected entry in commits/{sha}/pulls: {entry!r} — fail-closed.")
            head = entry.get("head") or {}
            head_sha = head.get("sha") if isinstance(head, dict) else None
            number = entry.get("number")
            state = entry.get("state")
            if not isinstance(number, int) or not isinstance(head_sha, str) or not isinstance(state, str):
                raise ApprovalGateError(
                    f"a pull request entry for commit {sha} has no readable number/head.sha/state "
                    "— fail-closed."
                )
            merge_commit_sha = entry.get("merge_commit_sha")
            if sha not in {head_sha, merge_commit_sha}:
                continue
            out.append(
                PullRequest(
                    number=number,
                    head_sha=head_sha,
                    state=state,
                    merged=bool(entry.get("merged_at")),
                    merge_commit_sha=merge_commit_sha if isinstance(merge_commit_sha, str) else None,
                )
            )
        return out

    def reviews(self, pr_number: int) -> list[Review]:
        """Every submitted review, normalized. A malformed entry denies rather than being skipped."""
        out: list[Review] = []
        for entry in self._paginate(f"repos/{self.repo}/pulls/{pr_number}/reviews"):
            if not isinstance(entry, dict):
                raise ApprovalGateError(
                    f"unexpected entry in pulls/{pr_number}/reviews: {entry!r} — fail-closed."
                )
            user = entry.get("user") or {}
            login = user.get("login") if isinstance(user, dict) else None
            state = entry.get("state")
            if not isinstance(login, str) or not isinstance(state, str):
                raise ApprovalGateError(
                    f"review {entry.get('id')!r} has no readable user.login/state — fail-closed."
                )
            if state.upper() not in _STANDING_STATES:
                continue
            submitted = entry.get("submitted_at")
            commit_id = entry.get("commit_id")
            if not isinstance(submitted, str):
                raise ApprovalGateError(
                    f"review {entry.get('id')!r} by @{login} is {state} with no submitted_at — "
                    "reviews cannot be ordered, fail-closed."
                )
            if not isinstance(commit_id, str):
                raise ApprovalGateError(
                    f"review {entry.get('id')!r} by @{login} is {state} with no commit_id — the "
                    "approval cannot be bound to a commit, fail-closed."
                )
            out.append(
                Review(
                    login=login,
                    state=state.upper(),
                    commit_id=commit_id,
                    submitted_at=submitted.replace("Z", "+00:00"),
                    review_id=int(entry.get("id") or 0),
                )
            )
        return out

    def environment_telemetry(self, name: str) -> str:
        """Read `GET /environments/{name}` FOR THE LOG ONLY.

        Returns a human-readable line and never raises: this call is telemetry, and telemetry that
        can change a verdict is not telemetry. `main` calls it strictly after the verdict is fixed.
        """
        try:
            status, payload = self._request(f"repos/{self.repo}/environments/{urllib.parse.quote(name)}")
        except ApprovalGateError as exc:
            return f"environment {name!r}: unreadable ({exc})"
        if status != 200 or not isinstance(payload, dict):
            return f"environment {name!r}: HTTP {status} (not read as any kind of approval)"
        rules = payload.get("protection_rules")
        rule_types = (
            [r.get("type") for r in rules if isinstance(r, dict)] if isinstance(rules, list) else rules
        )
        return (
            f"environment {name!r}: protection_rules={rule_types!r}, "
            f"can_admins_bypass={payload.get('can_admins_bypass')!r} "
            "(TELEMETRY ONLY — never a pass condition; see this module's docstring)"
        )


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed preflight for production promotion: proves a listed reviewer approved the "
            "commit being promoted. Exits non-zero unless the proof exists."
        )
    )
    parser.add_argument("--repo", help="owner/name; defaults to $GITHUB_REPOSITORY.")
    parser.add_argument("--sha", help="commit being promoted; defaults to $GITHUB_SHA.")
    parser.add_argument(
        "--image-tag",
        help=(
            "the workflow's image_tag input; defaults to $PROMOTION_IMAGE_TAG. When non-empty it "
            "must resolve to a commit in this repository, and that commit — not --sha — is what the "
            "approval is bound to."
        ),
    )
    parser.add_argument(
        "--approvers-file",
        default=DEFAULT_APPROVERS_FILE,
        help=f"path to the CODEOWNED reviewer list (default: {DEFAULT_APPROVERS_FILE}).",
    )
    parser.add_argument("--api-root", default="https://api.github.com", help=argparse.SUPPRESS)
    return parser


def _emit(text: str) -> None:
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"## {GATE_NAME}\n\n```\n{text}\n```\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    sha = args.sha or os.environ.get("GITHUB_SHA", "")
    raw_tag = args.image_tag if args.image_tag is not None else os.environ.get("PROMOTION_IMAGE_TAG", "")
    image_tag = raw_tag.strip()
    token = os.environ.get("GITHUB_TOKEN", "")

    if not repo:
        _emit(f"[{GATE_NAME}] RED — no repository (pass --repo or set GITHUB_REPOSITORY).")
        return 1
    if not token:
        _emit(
            f"[{GATE_NAME}] RED — no GITHUB_TOKEN. Without it the approval record cannot be read, "
            "and an unreadable record is a denial, never a pass."
        )
        return 1

    api = GitHubAPI(repo=repo, token=token, api_root=args.api_root)
    try:
        approvers = load_approvers(Path(args.approvers_file))
        ref = image_tag or sha
        if not ref:
            raise ApprovalGateError(
                "no commit to promote (pass --sha/--image-tag or set GITHUB_SHA) — fail-closed."
            )
        promotion_sha = api.resolve_commit(ref)
        pulls = api.pulls_for_commit(promotion_sha)
        reviews_by_pr = {pull.number: api.reviews(pull.number) for pull in pulls}
        verdict = decide(
            promotion_sha=promotion_sha,
            approvers=approvers,
            pull_requests=pulls,
            reviews_by_pr=reviews_by_pr,
        )
    except ApprovalGateError as exc:
        _emit(f"[{GATE_NAME}] RED — {exc}")
        return 1

    # Telemetry is read only AFTER the verdict is fixed, and only printed. See property 2 in the
    # module docstring: it can never move `verdict.approved`.
    _emit(verdict.render() + "\n    " + api.environment_telemetry(TELEMETRY_ENVIRONMENT))
    return 0 if verdict.approved else 1


if __name__ == "__main__":  # pragma: no cover — exercised through `main` in tests
    raise SystemExit(main())
