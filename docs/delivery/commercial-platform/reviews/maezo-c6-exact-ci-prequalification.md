# Maezo C6 exact CI/source prequalification

Reviewer: `commercial_arch_security_verifier`. Readback completed 2026-10-22 22:24 UTC. **PASS_EXACT_RUN_PREQUALIFICATION — NOT MERGE_READY.** Scope is the recorded candidate and CI executions below, including the sanitized diagnostic publication. No new blocking issue was found in this bounded qualification. Security findings and nonexecutions are explicitly retained; this is not a clean-bill-of-health claim for all repository code.

## Exact source and tested tree

- Requested candidate HEAD: `5f64f0e07f8cd13ee2ef6607c7f7652e1507ba80`.
- `SOURCE-FREEZE-C6-4-MAEZO-R2.json`: SHA-256 `cd18c7a041a331901c753ab7a0ede2b65afc36541ab3824a5715facbf50417b3`; **52/52 local curated paths match bytes/hash**, rechecked before verdict.
- Actual CI checkout is PR #907 merge commit **`b087524b74fe0c534350ca295cd3dbfcb6e64824`**, tree `8069c7c45c30b58695220128baab21cf0a2d8618`, with parents base `4fbdc3c86562cfd0f080bc5d0e9765dc70b18190` and candidate `5f64f0e07f8cd13ee2ef6607c7f7652e1507ba80`. Checkout logs and GitHub commit metadata agree. This is merge-tree execution associated with the exact candidate HEAD, not raw-HEAD-tree execution.
- Independently fetched the **same 52 curated files from that tested merge ref through the GitHub contents API**: all hashes and sizes match the freeze. No restricted file or its history was fetched. No local Git operation, checkout, build, test, server or port action was performed.
- Contract digest remains `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea`. The manifest retains 35 requirements, prior 122 mandatory subchecks and eight additional original-plan checks; no denominator was reduced here.

## Completed exact-run CI

[CI run 39858315833](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39858315833), attempt 1, pull_request event, head SHA above, completed **SUCCESS** at `2026-10-22T22:17:36Z`. Final inventory: **29 jobs: 28 success, one scheduled-only nightly skipped**; returned step inventory: **483 steps, 448 success and 35 skipped**. All 28 executed job logs were read for their outcome/qualification summaries. Step numbers have gaps because they are GitHub step numbers, not proof of hidden execution.

| Unit shard | Passed | Skipped | Executed xfail |
|---|---:|---:|---:|
| ledger-execution | 60 | 0 | 0 |
| ledger-consumer | 61 | 0 | 0 |
| ci | 2234 | 2 | 0 |
| ledger-resto | 560 | 0 | 1 |
| gateway-platform | 10807 | 0 | 0 |
| ci-pesado | 333 | 0 | 0 |
| dev | 523 | 0 | 1 |
| restante | 22368 | 133 | 3 |
| dev-runner | 18 | 0 | 0 |
| **Total** | **36964** | **135** | **5** |

The aggregate JUnit artifact contains **255 commercial test cases, zero failures/errors/skips**, in `unit-junit-restante.xml`. This demonstrates commercial unit/conformance execution, not a qualified external Tasy/PSP commercial sale. Aggregate coverage reports 89%; the combine tool's three identical-file skips are coverage-data deduplication, not three unexecuted test shards.

Portal web ran 31 files / **670 tests**, all passed, and verify/types/build; portal E2E ran **3 passed**. The Java job reports unit 746 tests (one skipped), engine integration 156/0 skipped, read-provider unit 42/0 skipped, and read-provider integration 79/0 skipped. `RuntimeDefinitionObservationTest` specifically executed **61 tests, zero failure/error/skip**. The runner shard executed all **18** tests. These are the final CI observations needed after the bounded fixture rechecks; they do not retroactively prove the cause of earlier CI failures.

### Existing engine regressions, qualified by artifacts

All five shards ran their actual Integration tests step, with real-engine regression reports and zero report validation errors. The aggregate `integration tests (real engine)` job succeeded after all five, not by an empty/skipped shortcut.

| Shard | Passed | Executed xfail | Inactive companion |
|---|---:|---:|---:|
| restante | 653 | 2 | 0 |
| reembolso | 37 | 4 | 0 |
| cancel-contas | 112 | 0 | 0 |
| recurso-nip | 123 | 0 | 2 |
| cred-ans | 76 | 20 | 0 |
| **Total** | **1001** | **26** | **2** |

Chaos PG-only separately reports **13 passed / four inactive mutation companions**. The validators explicitly preserve inactive companions as requiring separate RED evidence; they are not successes or silently removed cases. Deployment of the whole canonical artifact set is conditional on the designated `restante` shard; its skipped counterpart steps in the other four shards are not missing execution of the actual test steps.

Other visible limits: nightly eval job is schedule-only; FHIR/Tasy simulator smoke performed its guard and explicitly skipped the absent simulator implementation; the NetworkPolicy egress step was skipped; evals ran 292 passed / 18 skipped / three deselected. Unit skips include unavailable live-LLM/PG/environment-specific lanes and documented nonapplicable cases. None is counted as commercial real-source qualification. **Commercial Tasy/issuer/PSP integration remains NOT_RUN.**

## Artifacts and security gates

GitHub's connector returned only the first 30 CI artifacts; independent API inventory returned **31**, including the final restante artifact. Archive bytes were downloaded into memory only and SHA-256 compared with GitHub metadata. Verified content: five integration validation reports, chaos validation report, aggregate JUnit, portal evidence archive and three Security SARIF archives. Digests/IDs are in the sibling JSON. No raw archive/log/source dump was added to Git or to this report.

[Security run 39858315784](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39858315784) has six successful jobs with actual scan steps. Gitleaks PR diff reports no leaks; full-history scan is intentionally not run for this PR and was not fetched/executed by this reviewer. Dependency review actually ran and found no introduced high-or-higher vulnerable package. OSV reports **three findings, zero nonallowlisted at/above HIGH**; therefore the dependency result is threshold-qualified, not zero findings.

CodeQL Java SARIF has zero results; CodeQL Python has **three default-warning results**: `py/bad-tag-filter` at `tests/unit/platform/test_canal_escalonamento_js.py:565`, and `py/incomplete-url-substring-sanitization` at `src/maezo/conhecimento/base_saude.py:270` and `:276`. They lie outside the 52 curated paths and are reported without claiming this candidate introduced or repaired them. Trivy SARIF has zero results; its workflow severity lane is configured warning-only. Actual logs confirm SARIF uploads succeeded; old GHAS-deferred branch text echoed by shell is not evidence that these uploads were skipped.

[Flip review gate 39858315787](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39858315787) succeeded because no CODEOWNERS-owned path was touched, not because it supplied human approval. [Evidence gate 39858315794](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39858315794) and subsequent [39863374105](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39863374105) succeeded with their real reason: no task ID detected, so no ledger entry required. Their success is not evidence of a source/human sign-off. Other artifact/content/attribution/Terraform gates have completed steps; Terraform logs report 47 validations passed.

## Sanitized diagnostic successor

**PASS_EXACT_PROJECTION.** The private original `repair-preimages.local/c6-ci-lock-diagnosis-publication/original.json`, relative to this package, is SHA-256 `0243067531b560d99786245a368f38d6cd936ea7e4aecdfc8b0727a949717860` (6604 bytes). Public `reviews/maezo-ci-lock-diagnosis.json` is `6766af8f9187ec0d7c1e3d024605c9221285c5f374f4d00113bb90df545746b5`. Structural comparison proves: only original top-level `checkout` is removed, only `publication_projection` metadata is added, and **no common field changes**. The private checkout value is not reproduced here.

The original startup-repair manifest remains SHA-256 `b3f2c186dd9a6cd7c7e6bb6d34b4775e64a621d9f5a48bc104fb8c7d3c991378`; its `original_ci.diagnosis_json_sha256` still refers to **024306… private original**, not the sanitized successor. Public metadata explicitly identifies that distinction. There is no hidden rewrite of the original failure/diagnosis.

Existing original-reviewer receipts remain narrow: portal listener synchronization pins App.test `d4ef473b…`; lock startup pins runner test `20c25346…` and unchanged production runner `5b27e471…`; Java R2 pins `70124860…`. The final remote results above provide the previously pending whole-suite CI evidence at this candidate, without turning those fixture reviews into production acceptance.

## Current-main seam and next gate

During qualification ROOT reported a later normal merge of current main into owned candidate `0f1602d2259cb5e374d8163df1bbd5540fff255d`, with the AMH-main provenance receipt still pending. Independent API namespace comparison from the **actual tested base** `4fbdc3c8…` to `62a0754c` lists 11 paths under Helena, QA simulator, their unit tests and `docs/decisions-log.md`, with **zero overlap with these 52 frozen paths**. ROOT reported 13 incoming merge paths using its own merge comparison; these are different comparison scopes and this report does not claim to have independently reviewed those other PRs or all 13 merge deltas. No unrelated author review was performed.

The new merge and forthcoming AMH-origin receipt require a new exact-head CI/readback and final integration/source gates. This successful historical 5f64/b087 run cannot approve their later bytes. Merge readiness, published source admission, real commercial operation, human acceptance and production remain **NOT QUALIFIED**. No build, test, infrastructure, port, source or Git mutation was performed by this reviewer.
