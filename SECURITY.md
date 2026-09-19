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
- **GHAS-substitute review paths (T2.4, added after the orchestrator ruling
  downgrading GHAS from a blocker to a toggle):** the two items above are no
  longer the only way to see findings on this private repository.
  - **CodeQL SARIF as a workflow artifact.** The `codeql` job's `analyze`
    step already produces a full SARIF file every run (see above); a new
    step publishes that SARIF as a downloadable `codeql-python-sarif`
    workflow artifact (`actions/upload-artifact`, 30-day retention) —
    independent of whether the GHAS-gated upload above succeeds or is
    skipped. A follow-up step parses the SARIF with the Python standard
    library only (no new dependency) and writes a compact table — counts by
    SARIF `level` (error/warning/note) and the highest-frequency rule IDs —
    into the run's Step Summary, so findings are visible directly in the
    Actions run UI without downloading anything or needing Code Scanning
    enabled.
  - **OSV dependency-scan lane (`osv-scan` job).** Runs
    [osv-scanner](https://github.com/google/osv-scanner) (pinned version,
    checksum-verified static-binary download — no floating tag, same
    install pattern as the gitleaks job below) against the real `uv.lock` on
    every trigger; this needs no GitHub Advanced Security entitlement at
    all and **complements, does not replace,** the `dependency-review` job
    above. Findings are grouped by osv-scanner's own alias grouping (a
    GHSA/PYSEC/CVE id triple for the same vulnerability counts once) and
    bucketed by severity (CVSS base score from the group's `max_severity`,
    widened by any member advisory's GHSA `database_specific.severity` of
    `CRITICAL`). **Fail-closed semantics:** a scan tool error (any
    osv-scanner exit code other than the two documented outcomes, `0` =
    clean and `1` = findings present) fails the job outright — no
    `|| true`, nothing swallowed. Findings themselves are not automatically
    fatal: only a non-allowlisted finding at or above the `CRITICAL`
    threshold (tunable via the job's `OSV_FAIL_ON_SEVERITY` env var) fails
    the job — **a red `osv-scan` job on a genuine critical finding is the
    intended, correct outcome**, surfaced for human triage rather than
    silently allowlisted away. Lower-severity findings do not fail the job
    but are always rendered into the run's Step Summary (package, version,
    ids, CVSS score, severity bucket, allowlist status) so they stay
    visible without GHAS's Dependabot-alerts UI. The allowlist
    (`.github/osv-allowlist.json`) starts **empty** and every entry must
    carry a non-empty `reason`; a malformed entry (missing id/reason) fails
    the job closed instead of being silently ignored. Known-exploited
    (CISA KEV) status is **not** independently cross-referenced — osv.dev
    data carries no KEV flag, and fabricating that signal would violate
    this task's no-fabricated-results constraint — CRITICAL CVSS severity
    is used as the documented, honest proxy instead.
- **L3 coverage of the Java engine plugin and of infrastructure-as-code
  (added 2026-09-18, closing the three gaps the security-reviewer audit of
  2026-09-18 recorded against the L3 gate).**
  - **`codeql / java-kotlin` job.** The CodeQL lane above scans `python`
    only, so `src/maezo/portal/engine/java/` (Maven) — the code that runs
    *inside* the CIB Seven engine — had no static analysis at all. The new
    job runs the same pinned `github/codeql-action` init/analyze pair
    (init and analyze always on the **same** SHA) with
    `languages: java-kotlin` and **`build-mode: manual`**: CodeQL's Java
    extractor only sees classes it observes being compiled, so `build-mode:
    none` would produce an empty database that reports zero findings and
    reads green. The traced build is
    `mvn -B -f src/maezo/portal/engine/java/pom.xml -DskipTests package` —
    a subset of the `mvn verify` that ci.yml's `java-plugin` job already
    runs green, so no new network dependency is introduced. SARIF is named
    `java.sarif` (codeql-action resolves the `java-kotlin` alias to the
    canonical `java`); the GHAS try-and-classify upload, the
    `codeql-java-sarif` artifact and the Step-Summary table work exactly as
    in the python lane.
  - **`pom.xml` added to the OSV lane.** `osv-scan` now scans both
    `uv.lock` and `src/maezo/portal/engine/java/pom.xml` (osv-scanner
    resolves the pom's transitive Maven tree itself) under the same
    fail-closed `HIGH` gate. Measured with the pinned osv-scanner 2.4.0
    when the flag was added: 6 packages, 1 finding — `GHSA-659m-px2c-25wj`,
    CVSS 3.7 **LOW**, `spring-core` 6.2.11 pulled in transitively by
    cibseven-engine, fixed in 6.2.19. Below the threshold, so the gate is
    green on a truthful count, with nothing allowlisted.
  - **`trivy / iac` job — WARNING-ONLY, by an explicit, dated decision.**
    `trivy config` (pinned action SHA + pinned trivy version) scans
    `deploy/` — which contains the live `deploy/aws-ecs/` Terraform, the
    Cloudflare/Identity-Center roots, every Dockerfile and the helm charts
    — at `HIGH,CRITICAL`, publishes SARIF (same GHAS try-and-classify path
    + `trivy-iac-sarif` artifact + Step Summary). The severity verdict step
    carries `continue-on-error: true` and emits `::warning::` **because the
    tree is already red**: 10 findings (3 CRITICAL `AWS-0104` unrestricted
    egress; 7 HIGH — `AWS-0031` mutable ECR tags ×3, `DS-0002` root image
    user ×4) measured on 2026-09-18. Making that a merge blocker in the
    same change that introduced the scanner would have blocked every
    unrelated PR or forced blind ignore-annotations across live infra.
    **Dropping `continue-on-error` — with no other edit — turns this into a
    real L3 blocker, and that is the intended end state once the owner
    dispositions each finding.**
- The SARIF Step-Summary renderer shared by all three SARIF lanes lives in
  `scripts/ci/summarize_sarif.py` (stdlib only) and is unit-tested
  off-runner by `tests/unit/ci/test_summarize_sarif.py`.
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

**GHAS's remaining scope, after T2.4's GHAS-substitute lanes above:** with
the SARIF artifact/summary and the `osv-scan` job in place, GitHub Advanced
Security is no longer required to *review* CodeQL or dependency-vulnerability
findings on this repository — it is now a pure **toggle** for two
conveniences: (1) the native Security tab / Code Scanning dashboard as the
CodeQL results surface, in place of the SARIF artifact + Step Summary; and
(2) the `dependency-review-action`'s PR-diff annotations, in place of
`osv-scan`'s Step Summary (osv-scanner also does not diff against the PR
base — it scans the full current `uv.lock` every run). Provisioning GHAS
still auto-activates both gated jobs with no workflow edit needed, per the
try-and-classify mechanism described above.

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
