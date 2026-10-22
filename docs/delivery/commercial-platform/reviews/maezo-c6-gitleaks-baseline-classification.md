# Independent classification — current-tree Gitleaks baseline

Reviewer: `commercial_arch_security_verifier`. Date: 2026-10-22. Scope: the 13 additional findings in the authorized native scan of the complete current-HEAD snapshot, separate from the already classified new publication finding. **No literal credential identified in these 13 locations; baseline findings retained, not waived.** This is not a zero-findings scanner verdict and does not approve the pending configuration repair.

## Inputs and provenance

The native scan author reports Gitleaks 8.30.1 scanning the complete current-tree archive with the original configuration and CI-relative `dir .` invocation. Redacted result `r0-full-baseline-redacted.json`, under the package's private `repair-preimages.local/maezo-c6-public-revision-scan-repair-r1/`, independently hashes to `387eae1e205de89d08c0bcbd208b93f18db5542d519e571a29e9c8afa012ab67`. It contains **14 generic-rule findings**: the one new public revision-comparison finding plus the 13 examined below. The corresponding native log remains private; no matched raw value is reproduced here.

For every one of the eight files containing the 13 additional findings, independently fetched exact bytes from GitHub at all three known revisions: tested base `4fbdc3c86562cfd0f080bc5d0e9765dc70b18190`, prior candidate `5f64f0e07f8cd13ee2ef6607c7f7652e1507ba80`, and published consumer `6a7c0d9bb093a438d27f0fd7dfdb253c81d41722`. **All 24 remote comparisons match the current local file bytes.** All eight files are outside the curated 54-path freeze and outside the eight publication-delta paths. This establishes their pre-existing baseline status; it does not infer safety merely from age.

## Classification of the 13 locations

| File / lines | Count | Independently inspected semantics |
|---|---:|---|
| `deploy/aws-ecs/envs/dev-sa-east-1/dev-vivo.auto.tfvars:93,114` | 2 | UUID-shaped Secrets Manager version identifiers. Terraform consumes these through ECS `valueFrom` references together with the separately referenced secret resource. They select a secret version; they are not the secret payload or a bearer credential. No AWS lookup or raw UUID disclosure was performed. |
| `docs/gates/v21-durability-successor-20261005/aggregate-validator-qualification.json:11,12` | 2 | Explicit original-private and published artifact SHA-256 metadata in the API projection custody record. The published artifact digest was independently recomputed from its named local source file and matches. The private original artifact was not opened. |
| `docs/gates/v21-durability-successor-20261005/journey-runtime-qualification.json:23,126` | 2 | Named source-input digest inventory, including the same published API artifact reference and a test-source reference. The API inventory digest equals the independently verified qualification digest. These are audit hashes, not authentication material. |
| `docs/gates/v21-durability-successor-20261005/journey-runtime-qualification.json:162,164` | 2 | Explicit independent private-receipt digests, including scanner-classification and authentication-diagnostic receipt identities. Their containing schema/section gives digest custody semantics; no private receipt contents or credential values were retrieved. |
| `src/maezo/gateway/human/credentials.py:72` | 1 | Python annotated field, with `Ed25519PrivateKey` as a type and `field(...)` construction. AST inspection finds **no string literal** in the matched statement. The lexical type name is not a key value. |
| `src/maezo/gateway/document_requests/composition.py:267` | 1 | `PhiContentKeys` construction passing existing variables/keyword arguments. The containing assignment has **no string literal**. The match is source identifiers, not embedded material. |
| `src/maezo/gateway/human/auth_transport.py:65`, `human/read_credentials.py:108`, `staff_cases/publisher.py:48` | 3 | Annotated Ed25519 key fields with `field(...)`; each matched statement has **no string literal**. No private key bytes are present at these matched locations. |

The six JSON matches are classified by their actual schema/section and digest-reference role, not by hexadecimal appearance alone. Private artifact correctness and every historical receipt are not re-audited here. The two UUIDs are reference metadata, not a grant to retrieve secrets. The five code findings are syntax identifiers/type declarations, not credential literals; this does not qualify the unrelated human-integration implementation as a whole.

## Relationship to the new CI failure

The complete-tree scan legitimately reports 14, while the PR-delta Gitleaks job reports one new hit. Their scopes differ. The new hit is the immutable prequalification report's public revision comparison, independently classified in `maezo-c6-main-origin-exact-head-recheck.{md,json}`. Removing that one false-positive report hit from the native count would still leave these **13 baseline findings**. No claim of a zero native full-tree scan is authorized.

No additional waiver, path exclusion, credential rotation, unrelated production change or human-integration repair is recommended from these matched locations. Keep the raw/redacted baseline evidence and exact fingerprints. The authorized repair remains only the new exact public-revision classification plus the five legacy dummy-entry rule-targeting hardenings, with their separate native controls. Native results and seeded-real-secret detection must govern any later scanner verdict; baseline classification does not excuse a detector blind spot.

## Limits and next gate

Read-only metadata/source syntax inspection, AST parsing without imports, exact-file hashing and GitHub snapshot comparison only. No native scanner/test execution by this reviewer, no source/configuration edits, no cloud secret retrieval and no raw matched values/machine context printed. This review identified no actual-credential gating risk in the 13 matched locations. The original failed CI and the full-tree baseline remain failures/findings in the evidence record, not silently converted to scanner PASS.

The final configuration/native-proof recheck is reserved for the separately frozen author handoff. ROOT will preflight the complete staged publication, including all new reports, before the next commit and exact-head CI. Until then, scanner configuration acceptance and merge readiness remain pending.
