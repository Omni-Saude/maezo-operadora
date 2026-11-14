# Maezo C6 main-origin and exact-head recheck

Reviewer: `commercial_arch_security_verifier`. Readback: 2026-10-22 22:56 UTC. **SOURCE_ORIGIN_PIN_PASS; CI_HOLD; NOT_MERGE_READY.** This additive review is for published consumer HEAD `6a7c0d9bb093a438d27f0fd7dfdb253c81d41722`; it does not reuse the earlier 5f64/b087 CI approval.

## Source publication and consumer pin

Independently read the full UTF-8 schema from the connected GitHub source at AMH main-origin commit `a4f2ac237f02ea54873e245de3dceac922b8891d`, path `schemas/proposals/maezo/commercial-v1/shared-contract-c6-4.proposed.v1.json`. The **320163 bytes** hash to SHA-256 `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea`; blob `08e4d70dae5605d9000ad23db7df355eaa5fc940`. A separate GitHub contents read returned the same bytes/hash/blob. Parsed vocabulary remains 42 commands / 41 recoverable commands and the proposed public surfaces.

GitHub metadata independently confirms [AMH PR #282](https://github.com/Omni-Saude/amh-data-platform/pull/282) merged into main at that commit. Its tree `11ee7dd7367e608284cc9e00492c91d5bd421e35` matches candidate `56beb20d6be8eb240884ed4fba75f657dcd6e038` exactly. This is publication of the proposed extension in the source repository, not canonical human/source ratification, Tasy/PSP homologation or runtime admission.

The additive consumer source lock is SHA-256 `e969e5ff32532f30ae3d4bae60bd26cb08ad6093b19e946ba379cdbae7764228`; `AMH-C6-4-MAIN-ORIGIN.json` is `6db52d995f753dda6515dbe5e31abbdbf01cdf0bc87df225684d74c15a73b61d`. Their commit/tree/blob/path/length/digest fields agree with the independent source reads. The lock explicitly keeps runtime **UNBOUND** and requires independent current identity/representation, tenant, purpose, lifecycle and source authority. No runtime binding was installed by these documents.

Historical `config/integrations/amh/contracts.lock.json` remains SHA-256 `098ff7b5077792ee5bc8126cf01fec6b9de89bd3f78d5bc0f63f8b5f73f6e55a`. Candidate TEST-only provenance remains historical and unchanged; its old NOT_PUBLISHED label is not silently rewritten. The new source lock supplies later origin evidence without promoting the test artifact into runtime authority.

## Exact freeze and additive delta

`SOURCE-FREEZE-C6-4-MAEZO-R3.json` matches SHA-256 `1f82ba54b8af19720761caa56e7ed11bdf9b0e5906163984802be9e4d26d6128`. **54/54 local paths and 54/54 remote paths at the published consumer HEAD match bytes/hash.** All prior 52 R2 paths remain byte-identical; the added freeze entries are the new source lock and the unchanged historical canonical lock.

The manifest's `checkpoint_head` records `0f1602d2259cb5e374d8163df1bbd5540fff255d`, the pre-publication checkpoint. It is not mislabeled here as final HEAD; the later consumer commit above was independently verified by its own remote content read. API comparison from that checkpoint to the published consumer HEAD lists exactly eight publication/coordination paths: PLANS, the new source lock, main-origin JSON/Markdown, R2/R3 freezes and the preceding CI prequalification JSON/Markdown. It contains no changes to the prior 52 source/dependency paths.

## New exact-head CI and Gitleaks finding

New [CI run 39867833806](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39867833806) was still running at readback. Evidence Ledger `39867833815` and flip gate `39867833805` had completed successfully. New [Security run 39867833818](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39867833818) completed **FAILURE**. No old run is substituted for these gates.

The failing Gitleaks job `120134412477` reports one `generic-api-key` finding at `docs/delivery/commercial-platform/reviews/maezo-c6-exact-ci-prequalification.json:110`, exit 1. Its exact fingerprint is retained in the sibling JSON and private original log. **Independent classification: false positive for a public Git revision comparison, not a credential.** The field is named `api_compare`; its value contains only the already-verified tested-base revision and abbreviated incoming-main revision, separated by Git comparison dots. Both revisions were independently verified through GitHub during the earlier CI qualification. The report file itself remains byte-identical at SHA-256 `3194a26656ddb104963d2ba704b3e1c3dd1be76b028081bdb89dda684815bb33`.

The log's matched-value field is redacted; this review does not claim to recover an unredacted detector capture. Classification is based on the exact located field, immutable report bytes and independently established Git provenance. No token was printed or assumed safe merely for looking hexadecimal. The review author originally wrote this report; classification relies on external commit/CI evidence and the independent gatekeeper's matching rule/path/fingerprint, not self-asserted provenance.

Original failure log is preserved privately under ignored `outputs/maezo-c6-main-origin-security/6a-gitleaks-job-120134412477.log`: SHA-256 `8a8161691183e6d3d7fd1c5b339f500fd66c508c0a3e5add8028dcfaf5c15e71`, 67920 bytes, file mode 0600 / parent 0700. It was downloaded as evidence only. The original report and scanner configuration were not edited by this reviewer.

Minimal repair recommendation: a third party may narrowly classify only the exact benign rule/path/field/literal combination, preserving the immutable report and all unrelated detection. Do not ignore the entire path or generic rule. ROOT has separately assigned native before/after and seeded-real-secret controls plus examination of five legacy dummy-only allowlist entries. Those native results and final configuration are **pending independent recheck**; this report does not claim that broader scanner policy is already sound or that the baseline is globally clean.

## Limits and next gate

No source/scanner/test/build/port/infrastructure or Git mutation was performed. Reads used the connector and read-only GitHub APIs. Only these two review files and the explicitly authorized ignored private failure log were written. No restricted provider file/history was read or copied.

The origin/pin addition is internally consistent and source-qualified as Git publication evidence. Merge readiness remains blocked on the repaired scanner configuration and a new exact-head freeze/CI/readback, as well as ROOT's independent final integration gate. Any new configuration adds a source path and invalidates the relevant freeze/gates. Commercial external source operation, human authority and production remain **NOT QUALIFIED**, with runtime defaults still UNBOUND.
