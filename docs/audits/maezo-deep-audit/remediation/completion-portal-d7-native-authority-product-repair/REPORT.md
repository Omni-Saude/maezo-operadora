# D7B-NATIVE-GUARD-01 product author handoff

The narrow product repair is authored and **163 offline Java unit invocations pass** (141 existing + 22 added, zero failures/errors/skips). All original Java tests, including the unchanged 43 WorkloadEngineIT invocations, compile. **No PostgreSQL/CIB engine execution, native race acceptance, D5 preservation acceptance, image result, ledger credit or independent approval is claimed.**

Exact baseline: `8de77e92f9782dff69365fd06d5eca6b4899d051`, tree `2df25b429f54ff304cd6f2ee53b54a03fad0dea2`.
Frozen source commit: `89930624aaf0d09e641d289ca67ea5fbbbbf2d72`, tree `107727e187bc457be510ac7a03d17fc3b1fd4103`.
Branch: `completion/portal-d7-native-authority-product-repair`.
Worktree: `/Users/familia/code/maezo-completion-wt/portal-d7-native-authority-product-repair`.
The subsequent evidence-only commit packages this report and custody artifacts; it preserves this exact product/test source tree. The final evidence commit SHA/tree is delivered separately to ROOT to avoid a self-referential report hash.

## Scope and traceability

The frozen independent diagnosis and product mandate are copied unchanged into `input-REPORT.md` and `input-PRODUCT-REPAIR-MANDATE.md`. They record ROOT's real baseline result, 35 PASS / 8 FAIL, and delimit this author to WorkloadCommand + WorkloadPlugin. The existing wrong-definition and three native-grant failures remain the required regressions. The four second-wait observation failures belong to the separate author/mandate; no original native test or helper was edited.

Read progressively before implementation: AGENTS.md and PLANS.md current gate state; ADR-0049 D2/D4/D5/D7 (pinned definitions, current authority, enlisted transaction and closed typed boundary), ADR-0007 (versioned identity/provenance), ADR-0018 (human effect provenance); SP-OP-CONTAS-001 L0 invariant and human decision fields; DL-0006 (CIB 2.1.0 pin), DL-0048 (authorized portal architecture), DL-0023 (combined-tree verification); predeploy findings `no-denial-boundary-escape-reachability-blindspot`, `lgpd-negar-fundamentado-classified-neutral` and `audit-chain-from-rows-ts-reorder-verify-flap`. No business policy, grant, schema, consent/payment activation, ADR or ledger was changed.

Product diff paths, relative to the exact baseline:

- `src/maezo/portal/engine/java/src/main/java/br/com/maezo/workload/WorkloadCommand.java`
- `src/maezo/portal/engine/java/src/main/java/br/com/maezo/workload/WorkloadPlugin.java`

One new offline unit file: `src/maezo/portal/engine/java/src/test/java/br/com/maezo/workload/NativeAuthorityUnitTest.java`. `source.diff` contains the complete three-path delta (216 insertions / 14 deletions). `preservation.json` verifies 137 original Java test/resource, spec and config files have identical Git blobs; the complete source inventory proves every other original tracked file is unchanged as well.

## What changed and why

`current()` now rechecks the selected capability's exact native permission requirements after blocking points and at its existing COMMITTING listener. The external-task row lock immediately rechecks before the authorization-filtered task lookup/native mutation. Shared grant enumeration preserves the complete existing requirement set, including source READ_HISTORY in the reviewed automatic empty-human-evidence branch.

The recheck uses a fresh **native CIB 2.1.0 AuthorizationManager**, resolved against the same current CommandContext and its existing enlisted DbSqlSession. It invalidates only the MyBatis query-result cache with `SqlSession.clearCache()`. It does not clear the engine entity cache, flush, create a connection, change authentication, change permissions, commit or disable tenant/authorization checks. A fresh native manager avoids that manager's cached `isRevokeAuthCheckUsed` flag. Disabled required permissions refuse rather than inherit the manager's disabled-permission shortcut. Only the existing PROCESS_DEFINITION / PROCESS_INSTANCE requirements are evaluated; no custom grant SQL or policy substitutes native semantics.

This mechanism was derived from the exact installed engine jar: AuthorizationCheckCmd delegates to AuthorizationManager; its boolean grant queries use DbEntityManager.selectBoolean -> persistence/MyBatis; its revoke-discovery flag is cached per manager. The checked bytecode is preserved under `native-abi/`, with jar and tooling hashes in `toolchain.json`. MyBatis documents session-local query caching and explicit clearing via clearCache in its [Java API documentation](https://mybatis.org/mybatis-3/java-api.html). The offline controls use the installed CIB/MyBatis classes, not web-version assumptions.

PostgreSQL READ COMMITTED is now explicitly required at the existing connection preflight. Repeatable-read/serializable snapshots cannot promise visibility of grants committed after a wait; the command refuses such a profile rather than silently changing transaction isolation. This is a freshness prerequisite, not a claim of instantaneous atomicity with an IdP or concurrent authorization writes after the last check. Actual profile acceptance remains ROOT's run.

No native service/interceptor chain is entered by the COMMITTING grant recheck, so it needs no new GUARDED bypass and cannot append Freshness listeners while the transaction listener list is iterating. Existing D5 enlisted writes and listener ordering remain unchanged. The command/readiness guard now restores its prior true/false state on every exit. Native AuthorizationException at the typed workload executor boundary is normalized to the required safe Refused503 `engine_profile_unavailable`; underlying executor failure still rolls back.

The prior-human branch requires its finished historic task to match the exact approved source definition ID, tenant, source process and human-task definition. Exact source ID already resolves through the version/key checks in `definition()`. Its variable update must belong to that same definition, tenant, process and task. The decision-only receipt, assignee, variable/value and other checks are preserved. Offline positive object controls demonstrate equality checks only; they are not genuine human PAGTO/consent receipts or activation evidence.

## Executed verification and limits

Pinned Java: `/Users/familia/.cache/maezo-portal-jakarta-abi-repair-tooling/jdk-17.0.17+10/Contents/Home` (17.0.17).
Pinned Maven: sibling `apache-maven-3.9.9/bin/mvn`, offline `-o`, explicit empty user settings, existing `/Users/familia/.m2/repository`.

- Existing suite after product change: **141 PASS** (`offline-existing-tests.log`).
- Final suite after adding the focused units: **163 PASS** (`offline-final-163.log`), 2026-09-09 09:40:27–09:40:30 UTC approximately; exact argv/start/end/exit0 are in `offline-final-163-run.json`.
- The 22 additions prove warmed MyBatis grant results are stale in a control and rejected by recheck; stale native revoke-discovery is rejected; source READ_HISTORY remains required; disabled permission and wrong native contexts fail closed; no recheck listener append, SQL mutation/flush or enlisted-entity loss; previous guard state survives success/refusal; wrong task/update provenance dimensions refuse.
- These are explicitly **offline units** with actual CIB manager/context and actual MyBatis session/cache plus programmable query results. They do not execute a database or validate native authorization SQL against PostgreSQL. Original engine integration bodies are preserved, compiled and unexecuted.
- `git diff --check` passed. No full Python unit suite, global ledger, Docker, Postgres, engine, image, GitHub or other worktree operation was run by this author.

Earlier failures are preserved, not overwritten: `offline-original141.log` is an offline dependency-resolution failure using the smaller tooling cache missing REST Jakarta; `offline-focal-first.log` records two author-unit compilation mistakes (Runnable adaptation / wrong Refused field); `offline-focal-second.log` records one author-unit resource-name assertion mismatch. Correcting that assertion to the native Resources.resourceName value yielded the final pass; no existing test assertion changed. None of those author harness failures is presented as product/native RED or acceptance.

`source-manifest.json` records **1506 regular source files / 26,967,509 bytes**, each verified against the frozen source commit. `generated-target.tar.gz` preserves the complete final Maven target: **78 regular outputs / 682,724 bytes**, with per-file hashes in `generated-output-manifest.json`. Its contents include all compiled native test classes plus final JUnit reports. `MANIFEST.json` hashes every regular file in this evidence packet except itself; no symlinks are included.

## ROOT next mandate

1. Have the original SAME native specialist independently qualify this exact source/evidence delta. Author is not reviewer; this report grants no approval.
2. Combine the qualified product delta with the independently authored/qualified race-observation delta, static TempDir baseline and qualified HTTP a07269e0 delta, retaining exact source and prior failure custody.
3. After ROOT's serialized lane is free, use the already-qualified unchanged PG wrapper (SHA256 `c69df804ac7a7ea80606a937486407d815f7cb9e236614baefa77b67364bccac`) and canonical runner (SHA256 `e567c811b978c8c5c0d9135c92a9b951b73abf6bb1d2ce5ecc846c0776a72908`) for the entire workload43 lane on a fresh clean combined SHA/new output leaf. Do not broaden selectors, relax assertions or replace the wrapper.
4. Require all43 bodies and original exact refusal/status plus now-reached snapshot/no-effect/readiness/same-pending recovery assertions. Preserve all35 baseline passes and the independently repaired race outcomes. Retain any first actual failure for narrow diagnosis.
5. Then run the qualified original37 D5 preservation and required original fixtures/remaining lanes under ROOT's existing coordination. No native, image, operational-consumer, production or final-gate closure follows from the offline163 result alone.
