# D7B-NATIVE-GUARD-01 — distinct product repair from actual 8de77 failures

REVISE the product. ROOT's exact real PostgreSQL/CIB run has reproduced the cases below; no further reproduction service is needed before assigning a distinct narrow author. SAME does not implement its recommendations.

Proposed owned product paths: `src/maezo/portal/engine/java/src/main/java/br/com/maezo/workload/WorkloadCommand.java` and `WorkloadPlugin.java` in that same directory. Preserve all other product paths, A schemas, capabilities/grants, POM, D5/D6, existing Java tests and original image fixtures. The four race-observation failures have a separate test-only mandate; do not edit those tests as a product fix. Existing 43 native assertions are the regressions to satisfy, not assertions to weaken.

## Current native grants after blocking SQL and at COMMITTING

Actual failures:

- `afterNativeFlushWaitAuthorityIsRecheckedAtCommitting(String)[3]` = `native-grant-revoked`: native user-task INSERT was observed waiting, UPDATE_INSTANCE was removed and committed, and the pending external completion returned successfully instead of Refused503. Assertion stops at line316 before the later no-effect comparison.
- `realSourceAuthorityCannotExpireAfterLastTargetSql(String)[3]` = `native-history-revoked`: actual target external-task INSERT was observed waiting, required source READ_HISTORY was removed and committed, and the pending source-attested start returned successfully instead of Refused503. Assertion stops at line508 before the later no-effect comparison.

`WorkloadCommand.execute` runs `authorizeCapability` once before attestation/blocking work. Its later `current()` and COMMITTING listener revalidate identity, policy/config flags and lock deadlines but never actual capability grants. Repair the existing authorization lifecycle so the current native permissions for the already-selected exact capability/source are checked after blocking points and at the last precommit boundary. Preserve the existing requirement set, including source READ_HISTORY even when this explicitly reviewed automatic branch permits empty human evidence. Do not remove requirements, broaden grants, disable native authorization/tenant checks, or mistake an authorization-filtered empty result for authorized absence.

The check must observe current committed native authority rather than a stale preflight value or cached authorization entity. Use the pinned native engine authorization semantics; do not invent a replacement grant policy. If native services are used during COMMITTING, preserve the narrow internal guard scope and prior ThreadLocal state: the normal fence must not reject the legitimate internal check, and no unrelated/background/raw command may gain bypass. Preserve same native JDBC transaction, D5 enlisted writes/listener order, authenticated identity and rollback. Do not assert instantaneous atomicity with external identity systems or silently redesign authorization serialization.

## Safe typed refusal when native permission changes after a row wait

`authorityChangesWhileActualExternalRowIsBlockedRollback(String)[5]` = `native-grant-revoked` did throw native `AuthorizationException`, so this case is not a successful unauthorized operation. It failed the exact native typed contract: expected `Refused`, 503 `engine_profile_unavailable`, got the native exception. The snapshot and same-pending positive checks after that assertion were not reached and receive no PASS credit.

Rechecking current grants after the wait should refuse before native mutation, with the existing safe typed result. Preserve fail-closed handling of native authorization failures at this workload boundary without broad catch-all success, leaking exception details, changing unrelated native/human routes, or replacing the strict expected result with an accepted exception/status set. `WorkloadServlet` currently maps a native AuthorizationException to safe403; that existing HTTP fallback does not satisfy this specific required native503 freshness contract. No PHI exposure is demonstrated by the current native exception.

## Exact historical source definition/version

`priorHumanEvidenceRequiresActualTaskDecisionProvenance(String)[5]` = `wrong-definition` returned successfully at line431 instead of Refused403 `engine_resource_mismatch`. The fixture has a genuine completed source task/variable history and explicitly hostile copied receipt input, then changes that task's historic definition ID to the real CONTASv1 ID while the approved locked source is CONTASv2.

The prior-human branch in `attest()` selects finished task by process instance, tenant and task-definition key, then checks receipt/assignee and the recorded variable update; it omits the exact `cap.sourceTarget.definition_id` consistency check. The direct completed_human branch already checks it. Bind the prior-human task and associated historical evidence to the exact approved source definition/version, tenant, process and task identity before treating the value as provenance. Do not accept key-only or any-version matches, remove the wrong-definition assertion, grant a missing schema, or fabricate a positive PAGTO/consent receipt. Preserve the existing receipt operation/identity checks and the seven other negative provenance controls.

## Required verification and handoff

Preserve frozen 8de77 actual35PASS/8FAIL and all original bodies/parameters. Author uses an isolated exact-base worktree, ordinary narrow offline compilation/focal checks and full source/output custody; author starts no services. ROOT performs the serialized actual unchanged43 suite after SAME exact-delta qualification. The two after-SQL grant cases, row-wait typed refusal and wrong-definition case must pass with their original exact assertions, including now-reached no-effect/readiness/recovery controls; preserve the other35 actual passes and the independently repaired race cases. A compiled successor is not native acceptance. Provide clean SHA/tree and report/manifest; no ledger, GitHub, full-unit or image work is implied for this author.
