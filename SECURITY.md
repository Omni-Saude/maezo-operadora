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

Currently disabled (jobs commented out in `.github/workflows/security.yml`):

- CodeQL static analysis — Disabled (CodeQL v3 autobuild incompatible with the pyproject.toml + `src/` layout) — re-enablement tracked as T2.4 in `docs/prompts/V2-COMPLETION-PLAN.md`
- Dependency risk checks on PRs (`dependency-review-action`) — Disabled (requires GitHub Advanced Security, not provisioned on this repository) — re-enablement tracked as T2.4 in `docs/prompts/V2-COMPLETION-PLAN.md`

