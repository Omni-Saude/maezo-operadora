# Security Policy

## Supported Versions

This repository follows a trunk-based flow. Security fixes are applied to `main`, and supported deployments should consume builds from the latest validated commit on `main`.

## Reporting a Vulnerability

Please do **not** open public issues for vulnerabilities.

Report vulnerabilities via one of the following private channels:

- GitHub Security Advisory ("Report a vulnerability" in the Security tab), or
- Email the security maintainers listed in `.github/CODEOWNERS` (`@Omni-Saude/security`).

When reporting, include:

1. Impacted component(s) and file paths
2. Reproduction steps / proof of concept
3. Impact assessment (confidentiality, integrity, availability, compliance)
4. Suggested mitigation (if available)

## Response Targets

- Initial acknowledgement: within 2 business days
- Triage and severity classification: within 5 business days
- Patch/mitigation plan: as soon as practical based on severity

## Security Automation in this Repository

Currently active:

- Secret scanning with Gitleaks (`.github/workflows/security.yml`)
- Weekly dependency update PRs (`.github/dependabot.yml`)
- CodeQL static analysis (`.github/workflows/security.yml`, `codeql` job) —
  re-enabled in T2.4 (remainder) without autobuild: explicit
  `github/codeql-action/init` (`build-mode: none`) + `github/codeql-action/analyze`,
  `languages: python`. Python needs no build step, so autobuild (the original
  blocker) is no longer in the picture. The **scan itself runs for real on
  every PR/push/weekly trigger** (a scan failure is a red job). Only the
  SARIF **upload** is gated by GitHub Advanced Security on private
  repositories: `analyze` runs with `upload: never` and a follow-up step
  attempts the real upload (`POST /repos/.../code-scanning/sarifs`) with the
  workflow's own token, then classifies the outcome — success means results
  are live in the Security tab; the exact GHAS-gate rejection ("Code
  Security must be enabled for this repository to use code scanning.",
  observed on this repo 2026-07-17) means a loud, visible skip; **any other
  error fails the job** (fail-closed). Because the real gated operation is
  attempted every run, provisioning GHAS auto-activates live results — no
  workflow edit needed.
- All third-party GitHub Actions across every workflow are pinned to full
  commit SHAs (a trailing comment names the version tag each SHA resolves
  to) — no floating `@vN` mutable tags remain, as of T2.4 (remainder).
- SBOM generation (`anchore/sbom-action`, syft-based) + cosign keyless
  (Sigstore OIDC) image signing/attestation are wired into
  `.github/workflows/cd.yml`'s `build-and-push` job, on the same
  `AWS_ENABLED` gate as the image build/push itself — authorable and
  syntax-valid now; real execution (an actual signed image) stays gated
  behind AWS execution being turned on, exactly like every other
  AWS-touching CD step.

Currently disabled (visible-skip guard job, not a silent `if: false`):

- Dependency risk checks on PRs (`dependency-review-action`,
  `.github/workflows/security.yml`, `dependency-review` job) — this job runs
  on every PR and **tries the exact API call the action is built on**
  (`GET /repos/.../dependency-graph/compare/{basehead}`) with its own token,
  then classifies the outcome: success runs the real, pinned action; the
  GHAS gate's exact bare-Forbidden 403 shape (`gh: Forbidden (HTTP 403)`,
  which the upstream action itself maps to `"Dependency review is not
  supported on this repository."`) produces a loud, visible skip; **any
  other error — deliberately including a rate-limit 403 — fails the job**
  (fail-closed, retry-able). GHAS is not provisioned on this (private) repository
  (admin-scoped API check on 2026-07-17: `code_security.status ==
  "disabled"`). Re-enablement tracked as T2.4 in
  `docs/prompts/V2-COMPLETION-PLAN.md`; because the probe exercises the real
  gated resource with the real token, this job auto-activates the real scan
  the moment GHAS is provisioned — no workflow edit needed.

Restoration gap (documented, not implemented — see
`docs/reports/T2.4-a2a-agent-card-signing-gap.md`):

- A2A Agent Card cryptographic signing (HMAC-SHA256 `CardSigner` +
  fail-closed registry verifier), present in the v1 donor
  (`Maezo-Healthcare-Plan`), was dropped when the A2A layer was ported to
  this repo. Restoring it requires porting the donor's `assembly.py` /
  `dispatcher.py` (which do not exist in this repo's `a2a/` package yet) and
  a breaking `AgentCard` / `A2ARegistry` shape change — beyond the scope of a
  CI-hardening change. Tracked as a follow-up in
  `docs/prompts/V2-COMPLETION-PLAN.md`.
