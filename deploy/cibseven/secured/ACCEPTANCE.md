# ROOT acceptance: real-resource D7-B repair

This is an executable acceptance package, **not a live PASS**. The author operates no
Docker/engine/PostgreSQL services. ROOT serializes service execution under the canonical
`f99e19ea48a04f0a74b03d3e12edea253be0bee4` source lease. Use a clean frozen candidate,
retain argv/stdout/stderr/exit/JUnit and exact source/image hashes, and stop on the first
unexpected failure. Do not accept 404/405 or boot/collection as authorization evidence.

The original three WorkloadEngineIT methods remain, with additional genuine D5 receipt
and history preconditions. The original 37 D5 Java tests, original five image assertions,
Dockerfile.human, D5/D6 product code and A schemas remain unchanged. ROOT separately runs
the original three/37 image baseline on `a71a14e1` as diagnostic history; its 37-case
result cannot certify this expanded matrix.

## Prepare the exact disposable fixture

The following shell variables are explicit ROOT inputs: `CANDIDATE` is the clean source
checkout leased by the canonical runner; `PRIVATE_ROOT` is the existing canonical 0700
private root outside source and public evidence; `EVIDENCE_ROOT` is an existing public
evidence directory; `PINNED_FILES` is the qualified extracted `/camunda` directory from
image `9f0ba266…`; `FIXTURE` is a **new direct child** of `PRIVATE_ROOT`; `D7_PROJECT` is a
unique owned name matching `d7-[a-z0-9-]{8,64}`. Never echo secret files. All commands
execute from `CANDIDATE`, with the repository's locked Python environment.

```sh
export JAVA_HOME=/Users/familia/.cache/maezo-portal-jakarta-abi-repair-tooling/jdk-17.0.17+10/Contents/Home
MAVEN=/Users/familia/.cache/maezo-portal-jakarta-abi-repair-tooling/apache-maven-3.9.9/bin/mvn
"$MAVEN" -o -B -f src/maezo/portal/engine/java/pom.xml package
docker build -f deploy/cibseven/Dockerfile.secured -t "$D7_PROJECT" .
SECURED_IMAGE=$(docker image inspect --format '{{.Id}}' "$D7_PROJECT")
PYTHONPATH=src:. python deploy/cibseven/secured/prepare_fixture.py prepare \
  --checkout "$CANDIDATE" --sha "$(git rev-parse HEAD)" \
  --base-files "$PINNED_FILES" --private-root "$PRIVATE_ROOT" \
  --evidence-root "$EVIDENCE_ROOT" --output "$FIXTURE" \
  --bootstrap-image sha256:9f0ba266d1c3f5712da455560883340451bb59c30bae0d10abc4f2d5f0116c5b \
  --secured-image "$SECURED_IMAGE"
docker compose -p "$D7_PROJECT" -f "$FIXTURE/bootstrap-compose.json" up -d postgres engine
PYTHONPATH=src:. python deploy/cibseven/secured/prepare_fixture.py seed --fixture "$FIXTURE"
docker compose -p "$D7_PROJECT" -f "$FIXTURE/bootstrap-compose.json" stop engine
docker compose -p "$D7_PROJECT" -f "$FIXTURE/secured-compose.json" up -d --no-deps --force-recreate engine
```

Wait for actual bootstrap readiness before `seed`. Seed additionally requires the exact
D5 malformed-command 400 `INVALID_COMMAND` control before any seeding. The bootstrap pin
is the qualified `maezo-human-local:maezo-human-pkg-5b70a6e9-9e7469` image, confirmed by
ROOT; it is not the vendor base image `20a2135b…`. Do not silently substitute it.

Seed binds exact native definition IDs/versions, a locked external task and three
separate lifecycle instances. It reuses `LiveRelayFixture` and the existing real D6
migrations to issue a genuine signed D5 claim, commit its actual tenant audit/outbox and
receipt, and persist one legitimate pending release. It seeds an active human task,
global/local/binary variables and identity links through real bootstrap APIs. Nonsecret
IDs are in private `definitions.json`/`resources.json`; keys, config and signed synthetic
commands stay private. No runtime administrator endpoint or production bootstrap is added.
The real target models are explicitly synthetic; no canonical business/clinical outcome
is inferred. Do not recreate PostgreSQL during secure cutover.

## Execute the native transaction and image lanes

For the Java lane, ROOT supplies `MAEZO_HUMAN_IT_JDBC_URL`, `MAEZO_HUMAN_IT_DB_USER` and
`MAEZO_HUMAN_IT_DB_PASSWORD` securely. Each WorkloadEngineIT run creates one unique
`d7_it_<uuid>` schema and drops only that schema in `@AfterAll`. It installs the actual
D5 and D7 plugins into the same CIB 2.1 engine and the same JDBC transactions. The narrow
`D7HumanFixture` adapter reuses the unchanged D5 test helpers, without reflection or mocks.

```sh
"$MAVEN" -o -B -f src/maezo/portal/engine/java/pom.xml -Dtest=WorkloadEngineIT test
export MAEZO_D7_PACKAGE_FIXTURE="$FIXTURE"
export MAEZO_D7_COMPOSE_PROJECT="$D7_PROJECT"
export MAEZO_D7_READINESS_RUN_ID="$D7_PROJECT"
export MAEZO_D7_READINESS_SOURCE_SHA="$(git -C "$CANDIDATE" rev-parse HEAD)"
export MAEZO_D7_READINESS_SOURCE_TREE="$(git -C "$CANDIDATE" rev-parse 'HEAD^{tree}')"
PYTHONPATH=src:. python -m pytest -x -q tests/integration/test_portal_engine_d7_package.py \
  --junitxml="$EVIDENCE_ROOT/d7-real-resource.xml"
```

The readiness observation that `wait_ready()` writes (`readiness-attempts.json`) is attributed
to the operator's run: `MAEZO_D7_READINESS_RUN_ID` names it (`d7-[a-z0-9-]{8,64}`; the run's
unique owned `$D7_PROJECT` name qualifies) and `MAEZO_D7_READINESS_SOURCE_SHA`/
`MAEZO_D7_READINESS_SOURCE_TREE` pin the exact `CANDIDATE` revision under observation. Like
`MAEZO_D7_PACKAGE_FIXTURE`, they are explicit ROOT inputs with no default: the D7 cases fail
closed if any of them is missing or malformed.

The HTTP matrix requires all seeded resources and nonempty native runtime/history/detail/
variable/task/identity-link/receipt and actual tenant `audit_chain`, `human_command_outbox`,
`human_command_delivery`. Snapshot comparisons retain only scoped counts and SHA-256.
One raw target can be shared only while every prior denial's no-effect assertion passes;
`-x` prevents continuing after an unexpected mutation. Lifecycle tests use separate native
instances. The sole pending D6 release is consumed only by the final lost-response case;
rerunning that case requires a **new fixture**, not editing its identity or history.

`test_real_certificate_overlap_digest_restart_and_old_leaf_revocation`,
`test_known_leaf_metadata_mismatch_has_exact_refusal_and_restoration`,
`test_missing_dependency_or_inconsistent_identity_prevents_startup` and
`test_actual_human_commit_lost_response_then_secured_engine_restart_reconciles_same_pending_identity`
recreate **only the explicit owned engine**. They retain private argv/streams for each
recreation. Policy corruption uses truncate/write/fsync on the actual mounted inode.
Rotation tests an established old connection during digest loss, then exact old-leaf
403/new-leaf readiness after digest-bound restart; a dead TCP connection alone earns no
revocation credit. Startup tests first execute a same-capability start and verify its native runtime,
history and external task. They retain a separate valid operation unchanged through the
fault and recovery. Each fault must create a new exact owned container ID and StartedAt,
with the pinned image, matching project/service labels, running state and zero restarts.
Timestamped logs from that ID since StartedAt must identify `maezo-boundary` filter failure,
`br.com.maezo.workload.Refused: engine_profile_unavailable`, and failed `/engine-rest`
context startup, plus the pinned JSSE8443 connector startup before any HTTPS probe.
While the fault remains installed, the same verified mTLS agent requests
both the previously live readiness path and the pending operation path. Both attempts
and the fault snapshot are retained before classifying the outcomes; unexpected transport
failures are recorded separately and then fail, with no fallback acceptance.

The expected context-initialization failure outcome is exactly HTTP404, `text/html`,
with the pinned Tomcat `HTTP Status 404 – Not Found` title for **both** paths, bound to
those current-context logs and unchanged container identity. This certifies unavailable
deployment only. It earns no native authorization credit and does not relax any raw-route
403 assertion. A 503, 405, timeout, reset, missing context attribution or different body
fails for diagnosis; do not accept a broad fallback status set. This outcome remains a
strict source expectation until ROOT executes it on the real image. After restoration,
readiness and the exact pending bytes/identity must succeed with new committed runtime,
history and external task effects; the faulted nonempty snapshot and plaintext refusal
remain required. Private `startup-<dependency>-<uuid>` evidence separates pre-fault,
faulted and restored responses, logs, container identity and snapshot hashes.

The byte-limit case sends a valid operation envelope with trailing JSON whitespace:
exactly 1,048,576 bytes and exactly 1,048,577 bytes, differing only by one final ASCII
space. A prior same-capability start establishes live authority; the oversized pending
request must receive exact 400 `engine_invalid_body`, no-store and leave the nonempty
native/D5/D6 snapshot and pending business key unchanged. The exact 1 MiB version then
must create its native runtime/history/external-task effect. Both requests explicitly
check Content-Length and absence of Transfer-Encoding; this test claims **Content-Length
boundary enforcement only**, not streamed/chunked enforcement. Readiness surrounds the
fault and successful causal controls. Actual wire lengths/hashes, responses and snapshots
are retained privately in `body-limit-<uuid>`. Signed human command limits remain separate.

The canonical runner's lease encloses execution, generated-artifact archival, cleanup and
source disposal. Archive `src/maezo/portal/engine/java/target` (JUnit, classes, JAR digests)
and remove only this run's generated target/cache files before authenticated cleanup.
Dispose the leased source before releasing its lease. **Do not reuse the private
`run_portal_java_pg_v4` driver**: it pins old runner `02810` and releases before checkout
cleanup. ROOT must use the current runner's source lifetime contract.

The native PG wrapper qualification does **not** cover these image tests. All source
examples above must run inside ROOT's separately reviewed secured-image wrapper, which
must authorize every child Docker invocation against the canonical lease, fixed local
Docker context and exact owned project/container. The added startup observations use
`compose -p <project> -f <secured-compose> ps -q engine`, limited-field `docker inspect
--format ... <id>`, and `docker logs --timestamps --since <StartedAt> <id>`; existing
engine recreation and admin-app presence inspection remain required. The test checks
identity after HTTPS so stale logs, reused containers and concurrent recreation fail.
Archive the private phase evidence under ROOT's custody without publishing credentials
or synthetic signed commands. Do not launch the snippets as an unleased standalone lane.

## Requirement-to-case map

| Mandate | Executable case identity / witness |
|---|---|
| 1–3 real resources, all aliases/verbs | `test_raw_aliases_and_mutations_leave_engine_and_receipts_unchanged`: 41 native paths × 3 aliases × 8 methods. IDs are resolved from seeded metadata and independently read from native PG before each attack; exact 403 `engine_operation_denied`, no-store, unchanged nonempty snapshots. HEAD has protocol-required empty body paired with the same path's attributed GET refusal. |
| 2 UI/noncanonical routes | `test_closed_ui_and_noncanonical_paths_have_explicit_boundary_oracle`: 5 paths × 8 methods, exact boundary code. Manager/host-manager absent apps are not claimed as native policy enforcement. |
| 2 positive causal effects | `test_scoped_start_read_and_worker_lifecycle_have_native_causal_effects`, original start test, `scopedStartAndHistoryAreReal`, `externalLifecycleAndHumanMutationRefusal`, `explicitBpmnErrorOnlyWithinReviewedTopic`; actual scoped start/read, committed external ownership/variables/history/human task. |
| 4 all native grants | `everyNativeGrantGatesReadinessAndSamePendingOperation`: CREATE, READ, READ_INSTANCE, READ_HISTORY, UPDATE_INSTANCE removed/restored individually; same request and identity, readiness and execution require exact 503 `engine_profile_unavailable` (READ removal instead produces 403 `engine_resource_mismatch` because native authorization filters the bound definition lookup first), never unauthorized empty history. Original READ_HISTORY removal retained. |
| 4 native contexts | `actualNativeCommandFencesAndBackgroundContext`: actual TX_REQUIRED and REQUIRES_NEW executors; known workload cannot enter unguarded native command; unauthenticated native background context reaches the actual CommandContext. `typedRequestWithMissingNativeAuthenticationIsRefused`; HTTP missing certificate uses the unchanged typed JSSE oracle. |
| 5 actual blocked row | `authorityChangesWhileActualExternalRowIsBlockedRollback`: observed PG row lock, expired/changed owner, policy corruption/removal, native UPDATE_INSTANCE revocation; unrelated rows/receipts/history unchanged and same pending identity resumes after restoration. |
| 5 last native SQL/COMMITTING | `afterNativeFlushWaitAuthorityIsRecheckedAtCommitting`: actual native user-task INSERT trigger blocks, then policy or native grant is revoked; full rollback required. `realSourceAuthorityCannotExpireAfterLastTargetSql`: actual enlisted source lock and target INSERT, source expiry/policy loss/READ_HISTORY revocation, with a reachable automatic synthetic source control. |
| 5 D5 enlisted receipt | `policyLossDuringActualD5EnlistedReceiptRollsBackHumanEffect`: actual D5 signed release and enlisted receipt INSERT blocks; mounted policy removed/corrupted; task/history/receipt rollback and exact same command recovery. |
| 5 competing ownership | `competingNativeOwnershipActionAndTypedCompleteAreSerialized`: native complete/unlock/extend/actual timer job versus typed completion. `optimisticFetchReturnsOnlyActuallyCommittedOwnership`: two real competing fetches, exactly one committed ownership advertised and read back. |
| 5 target mismatches | `resourceAndIdentityMismatchesHavePositiveControlAndNoEffect`: real wrong task/process/topic/worker/tenant/version, precise resource or policy code and native positive control. |
| 6 mTLS/purpose | `test_invalid_client_chain_requires_typed_tls_or_exact_application_refusal`, `test_known_ca_unknown_or_wrong_san_leaf_is_attributed_denial`, `test_certificate_purpose_cannot_borrow_agent_capability`, `test_nonhuman_peers_cannot_enter_human_native_boundary`, `test_forwarded_identity_never_borrows_capability`; missing/unknown-CA/unknown-leaf/expired/no-EKU, fixed safe TLS reasons or exact attributed application error. |
| 6 rotation/metadata/startup | Named lifecycle cases above plus `test_actual_mounted_policy_corruption_denies_and_same_identity_recovers`; SPKI/SAN/issuer and tenant/environment mismatches. No host-unlink-only assertion. |
| 6 restart/lost response | Actual D6 pending release POST commits, observer drops the received real response, actual secured engine restarts, pending relay performs GET-only receipt reconciliation; same receipt bytes, one effect/receipt, committed tenant audit/outbox. |
| 7 human source negatives | `priorHumanEvidenceRequiresActualTaskDecisionProvenance`: missing decision, claim/release-only, wrong task/definition/assignee, copied actor without historical task update, wrong source case. Genuine D5 synthetic receipts are copied only as explicitly hostile negative DB inputs. No forged receipt is a successful control. |
| strict body | All existing malformed cases remain byte exact. Their old `size` label is a historical 131,081-byte invalid-schema case, not size-guard credit. `test_valid_operation_at_actual_byte_limit_executes_and_one_byte_over_has_no_effect` adds valid 1 MiB / 1 MiB+1 wire bodies, exact 400/no-store for oversized, unchanged nonempty snapshot, and native runtime/history/external-task causal success at the limit. Malformed-envelope rejection alone does not attribute an individual parser guard. |
| D7B-ACCEPT-02 startup HTTPS | `test_missing_dependency_or_inconsistent_identity_prevents_startup[ca-mount/tenant/environment]`: pre-fault start, authenticated faulted readiness and pending POST, exact failed-context HTTP404 bound to current container/StartedAt/native context logs, unchanged snapshot/plaintext refusal, then restored same-pending native effect. |

The source exposes strict regressions that may reveal product defects: native permission
checks currently appear to run before blocking SQL, while COMMITTING checks policy and
lock deadlines; historical source-task definition consistency also needs the actual
negative test. A failed real oracle must produce a separately scoped product repair.
Do not weaken the assertion, replace the target with an absent ID, or treat 503 as success.

## Preservation and remaining whole-goal work

ROOT must separately execute the unchanged original five image tests against their original
two-connector Dockerfile.human fixture, the original 37 D5 real tests, and the existing D6
lane under its original qualified fixtures. The secured image has only HTTPS, so its
plaintext case requires connection refusal plus valid TLS capability controls; it does
not replace the old image's exact HTTP403 check.

Canonical PAGTO/consent **positive human activation** is still separately required: D5
only activates its synthetic human form. This package's automatic synthetic source start
and hostile receipt attempts do not certify a financial, clinical or consent decision.
C typed callers, D production bootstrap, E all 157 legacy fixture dispositions, reviewed
missing A schemas (DMN/READ_STATUS/other topics), 43 portal forms/PHI/UI, integration review,
actual integrated-SHA CI and operational cutover remain required. No DPO/TISS/clinical/
fraud/production ratification is created by this fixture.
