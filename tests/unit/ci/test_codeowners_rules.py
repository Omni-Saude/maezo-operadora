"""Fence: `.github/CODEOWNERS` really owns the paths this repository CLAIMS it owns.

WHY THIS FENCE EXISTS (adversarial-review finding, 2026-09-04; owner decisions R-021 and R-053)
------------------------------------------------------------------------------------------------
`.github/CODEOWNERS` is a list of ENUMERATED rules, not a set of directory globs. That is deliberate
(its own header explains why), and it has one recurring failure mode: prose elsewhere in the tree
asserts that some file is "CODEOWNED because it is under `.github/`" (or under `deploy/`, or under
any other tree) when no rule actually matches it. The claim looks true, reads true, and is false —
and the file it protects is precisely the one whose silent edit widens a gate.

Two live instances were found and closed on 2026-09-04:

* `.github/production-approvers.yaml` (R-021) — three artefacts said `.github/**` was CODEOWNED. It
  is not: the `.github/` rules are `/.github/CODEOWNERS`, `/.github/workflows/`,
  `/.github/osv-allowlist.json` and (since that date) the approvers file itself. Before the rule was
  added, the ONE file whose edit would widen who may ship to production was the one escaping review.
* `deploy/` (R-053) — the owner's decision: "SIM — confirmar por escrito e, no mesmo ato, acrescentar
  `/deploy/` ao `.github/CODEOWNERS` para que a maquina passe a gatear o que hoje so a disciplina
  gateia."

THE METHOD, AND WHY IT IS NOT A REGEX OVER THE FILE. Every assertion here resolves a path through
THIS REPOSITORY'S OWN CODEOWNERS matcher — `parse_codeowners` + `owners_for_path` from
`scripts/ci/check_flip_path_review.py`, the same code the merge-path gate uses to decide whether a
PR touched an owned path. So a green here means exactly "the flip-path gate would consider this path
owned", not "some line in the file starts with the right prefix". A test that asserted the latter is
what let the false claim stand: `any(line.startswith("/.github/") …)` is satisfied by
`/.github/CODEOWNERS` and says nothing about the file it was written to defend.

WHAT THIS FENCE DOES NOT CLAIM. Being owned REQUESTS a reviewer; it does not REQUIRE one. The
`main-protection` ruleset carries `require_code_owner_review: false` and
`required_approving_review_count: 0` (measured read-only 2026-09-04), and `@Omni-Saude/security-team`
does not resolve on this repository, so today the mechanical enforcement comes from the
`flip-path-review-gate` check reading this same file. Closing that gap is owner decision R-051
(deadline 2026-09-14), and no assertion here may be read as evidence that it happened.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.ci.check_flip_path_review import (
    Owner,
    OwnerKind,
    Rule,
    owners_for_path,
    parse_codeowners,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CODEOWNERS = _REPO_ROOT / ".github" / "CODEOWNERS"

_SECURITY_TEAM = "@Omni-Saude/security-team"
_OWNER_LOGIN = "@rodaquino-OMNI"

#: A path under `.github/` that NO rule owns. Its presence in this file is the non-vacuity anchor for
#: every "is owned" assertion below: if `.github/` were blanket-owned (or if the matcher matched
#: everything), this would resolve to a rule and the suite would say so.
_UNOWNED_GITHUB_PATH = ".github/ISSUE_TEMPLATE/bug_report.md"


def _rules() -> tuple[Rule, ...]:
    """Parse the real file. A parse error here is a hard failure, exactly as it is in the gate."""
    return parse_codeowners(_CODEOWNERS.read_text(encoding="utf-8"))


def _owner_tokens(rule: Rule) -> set[str]:
    return {owner.raw for owner in rule.owners}


def _carries_team(owners: tuple[Owner, ...]) -> bool:
    return any(
        owner.kind is OwnerKind.TEAM and owner.raw == _SECURITY_TEAM for owner in owners
    )


def test_the_matcher_can_return_unowned_so_every_assertion_below_is_falsifiable() -> None:
    """Non-vacuity, and the direct refutation of the old `startswith("/.github/")` assertion.

    `.github/` is NOT blanket-owned in this repository; its rules are enumerated. A path under it
    that no rule names resolves to `None`. If this ever starts resolving to a rule, the "is owned"
    assertions below have stopped discriminating and must be re-derived.
    """
    assert owners_for_path(_rules(), _UNOWNED_GITHUB_PATH) is None, (
        f"{_UNOWNED_GITHUB_PATH!r} resolved to an owner — either a blanket `.github/` rule was "
        "added (then this fence's anchor must move) or the matcher is matching everything."
    )


def test_the_production_approvers_file_resolves_to_a_real_owning_rule() -> None:
    """R-021's own text requires the reviewer list to live "em arquivo CODEOWNED".

    Resolved through the repository's matcher, so a rename of the file, a narrowing of the rule or a
    deletion of the line all turn this RED. Prefix coincidence cannot satisfy it.
    """
    rule = owners_for_path(_rules(), ".github/production-approvers.yaml")
    assert rule is not None, (
        "`.github/production-approvers.yaml` is not owned by any CODEOWNERS rule. It is the list of "
        "logins that may authorise a production promotion: an unowned reviewer list is a gate that "
        "widens itself in one unreviewed PR. Restore the "
        "`/.github/production-approvers.yaml` rule (owner decision R-021)."
    )
    assert _owner_tokens(rule) == {_OWNER_LOGIN, _SECURITY_TEAM}, (
        f"the owning rule is `{rule.describe()}` — the approvers list must carry the same owner pair "
        "as the rest of the gate apparatus, so that widening it needs the security reviewer."
    )


def test_the_deploy_tree_resolves_to_a_real_owning_rule() -> None:
    """Owner decision R-053, executed as the owner wrote it.

    Before the `/deploy/` rule existed, a PR touching only Helm or Terraform matched no rule, so
    `check_flip_path_review.py` computed "0 owned paths touched" and reported GREEN — the P0 deploy
    surface was gated by human discipline, not by machine. Every real file under `deploy/` is
    resolved (not a sample and not a literal list), so narrowing the rule to a subtree cannot pass
    unnoticed and a newly added subtree is covered the day it appears.

    Per the decision's own floor_note this asserts OWNERSHIP, never enforcement: with
    `require_code_owner_review: false` the rule requests a reviewer rather than requiring one.
    """
    rules = _rules()
    real_files = sorted(
        p.relative_to(_REPO_ROOT).as_posix()
        for p in (_REPO_ROOT / "deploy").rglob("*")
        if p.is_file()
    )
    assert len(real_files) > 10, (
        f"only {len(real_files)} files found under deploy/ — the derivation is not exercising the "
        "tree it claims to cover"
    )
    for path in real_files:
        rule = owners_for_path(rules, path)
        assert rule is not None, (
            f"{path} is owned by no CODEOWNERS rule. Owner decision R-053: 'acrescentar `/deploy/` "
            "ao `.github/CODEOWNERS` para que a maquina passe a gatear o que hoje so a disciplina "
            "gateia.' Restore the `/deploy/` rule."
        )
        assert _carries_team(rule.owners), (
            f"the rule owning {path} is `{rule.describe()}` — R-053 names the pair "
            f"`@rodaquino-OMNI {_SECURITY_TEAM}`, so that the security reviewer is requested on "
            "infrastructure changes and not only the repository owner."
        )


def test_the_expected_codeowners_error_count_in_the_header_matches_the_derivation_it_states() -> None:
    """The header says to DERIVE the expected `/codeowners/errors` count, never to memorise it —
    "exatamente UM por linha de regra que carrega o token do time". This keeps the stated number and
    the derivation in lockstep, so adding a rule without updating the header is RED rather than a
    silent drift that would later be read as "an error of another nature is hiding in there".

    It asserts nothing about the LIVE count: that is what the `codeowners-errors-check` job measures.
    """
    text = _CODEOWNERS.read_text(encoding="utf-8")
    match = re.search(r"CONTAGEM ESPERADA hoje:\s*(\d+)\s+erros", text)
    assert match, "the header no longer states a CONTAGEM ESPERADA — the derivation lost its anchor"
    stated = int(match.group(1))
    derived = sum(1 for rule in _rules() if _carries_team(rule.owners))
    assert derived > 0, "no rule carries the team token — the derivation would be vacuous"
    assert stated == derived, (
        f"the header states {stated} expected `/codeowners/errors` entries but {derived} rule lines "
        f"carry {_SECURITY_TEAM}. GitHub reports one 'Unknown owner' per rule line carrying the "
        "unresolvable team token, so the two must move together; update the header when you add or "
        "remove a rule."
    )
