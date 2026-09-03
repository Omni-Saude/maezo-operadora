# Release process

**Status:** DRAFT — this file did not exist before D14-02 (docs-hygiene gap closure,
2026-09-03). No release has ever been cut from this repository: there is no git tag, no
`CHANGELOG.md`, and no published artifact registry entry to point to. This document records
what exists TODAY (a single version string, hand-maintained) and the minimum process a real
release needs before the first production deploy (G4) — it does not claim any of that process
already runs.

## Where the version lives today

The only version identifier in the repository is `pyproject.toml`'s `[project].version`
(currently `0.2.0`), echoed in `README.md`'s badges and in
`src/maezo/tools/workers/harness.py`'s `version("maezo-operadora")` lookup (falls back to the
literal `"unknown"` — never fabricated — when the distribution metadata is absent, e.g. running
from a source checkout without `pip install`/`uv sync` having registered it). There is no
`__version__` module attribute and no build-time version injection.

**No tags, no releases, no changelog exist yet** (`git tag` — empty; no `CHANGELOG.md` at repo
root; no GitHub Releases). This is consistent with the platform's own status: `README.md` and
`PLANS.md` both describe `v0.2.0 alpha-dev`, pre-G4 (no real production deploy has happened —
see `PLANS.md` §0 gate table).

## Why this matters before G4 (regulatory traceability)

Once a real tenant is live, an incident or an ANS/LGPD audit needs to answer "which build was
running for tenant X on date Y" — a question a bare `pyproject.toml` version cannot answer on
its own (nothing currently binds a version string to a commit SHA, a container image digest, or
a deploy timestamp per tenant). `deploy/aws-ecs/` deploys by ECR image tag (commit-SHA-based, per
`docs/runbooks/aws-ecs.md`), which is closer to what an audit needs than the `pyproject.toml`
string — but the two are not currently cross-referenced anywhere.

## Minimum process (not yet built — recorded as a requirement, not a claim)

1. **Tag on release.** Cut an annotated git tag (`vMAJOR.MINOR.PATCH`) at the commit that gets
   deployed, matching `pyproject.toml`'s version at that commit.
2. **Changelog entry.** Append a dated section to a `CHANGELOG.md` (Keep a Changelog format)
   summarizing what shipped, referencing the PRs/commits — this file does not exist yet; creating
   it is a follow-up to this one, once there is a first tagged release to log.
3. **Version-to-deploy binding.** The ECS/Fargate deploy path (`deploy/aws-ecs/`,
   `docs/runbooks/aws-ecs.md`) already tags images by commit SHA; a release should record which
   tag/SHA/tenant combination is live, so an auditor can answer "which build, which tenant, which
   date" from a single durable record (candidate location: `docs/evidence-ledger.md` or a
   dedicated `docs/releases/` log — not decided here).
4. **Semver discipline.** `0.x` releases may break compatibility between minors; `1.0.0` (first
   real production tenant) is the point at which semver compatibility guarantees should start
   being taken literally.

None of the above is implemented by this commit — this file is the documentation-hygiene fix for
D14-02 (no `RELEASE.md`/`CHANGELOG`/versioning process existed at all); building the tagging/CI
automation is separate, owner-gated work (would touch `.github/workflows/`, out of scope here).
