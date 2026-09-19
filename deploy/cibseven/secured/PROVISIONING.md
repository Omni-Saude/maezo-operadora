# D7-D provisioning prerequisite and proposed native execution

**Status: compiler implemented; native execution design proposed, not accepted or deployed.**
This document consumes the frozen B v1 manifest and grants from `README.md`. It does not change
B's authentication, routes, schema, native command fence, or runtime authority.

## Implemented read-only operator entrypoint

```sh
python -m maezo.platform.engine_bootstrap.secured_plan_cli \
  --boundary-file /absolute/canonical/reviewed-boundary.json \
  --boundary-sha256 <sha256-of-exact-public-file-bytes>
```

The command requires explicit input and prints `maezo.engine-provisioning-plan.v1` JSON.
It does not read secrets, access an engine, execute a grant, generate certificates, write files,
change database roles, create users, deploy BPMN/DMN, or activate runtime callers. Every output
has `execution_authorized: false`. There is no `--apply`. Legacy
`python -m maezo.platform.engine_bootstrap` retains its existing behavior and is **not** a D7
provisioner: it uses raw administrative REST, which the secured B image refuses.

The compiler verifies the public manifest's exact digest and closed structural shape, exact
A-registered profile documents/digests and source-attestation mappings. It requires separate
bootstrap/deployment/runtime principals; sharing a key or subject across engine users is refused.
Certificate rotation may repeat an engine user only with identical identity, purpose and complete
capability bindings, including source mappings outside A's digest. This is stricter deployment
hygiene, not a change to B's wire contract. It does not claim PKIX, actual mount or current-time
verification: those are actual deployment readbacks still required.

Output grants are only the exact native minima implemented in B
`WorkloadPlugin.authorizeGrants`: definition-key READ, operation-specific CREATE_INSTANCE /
READ_INSTANCE / READ_HISTORY / UPDATE_INSTANCE, and required source-definition reads. The sole
wildcard is **CREATE on PROCESS_INSTANCE `*` for a START capability**, required by the CIB native
resource model before an instance exists. It never gives CREATE on arbitrary definitions, UPDATE
on tasks, broad variable writes, ALL, group administrator membership, or raw REST access. Native
key-level grants must always remain behind B's tenant/version/topic/field/source command checks.

Each grant lists its contributing exact capability digests in `grant_sources`. Bootstrap and
deployment peers receive **no runtime grant**. Known human-relay/observer users are listed for
preservation; that list is not a complete inventory of preexisting database users/grants.
All existing D5/D6 identities, groups, grants, receipt tables and history remain unchanged.
The future executor must inventory the full existing state and preserve it separately.

## Why an operational execution interface is still needed

B allows bootstrap/deployment peers only authenticated version reading, not user/grant/deploy
REST. `WorkloadEngineIT` demonstrates a **test-only** native API setup, followed by closing the
setup engine and reopening with B/human plugins. It uses a disposable schema and temporarily
disabled native authorization. `prepare_fixture.py::seed` is explicitly a disposable, loopback
legacy-image fixture. Neither is an approved operational authority mechanism.

The canonical plan and ADR-0049 order bootstrap before runtime, but do not define an enforceable
runtime stop/start fence, privileged database-role lifecycle, one-shot idempotency receipt or
artifact-to-definition binding protocol. A local marker or PostgreSQL advisory lock is
insufficient: B does not participate in that lock, so it cannot exclude another runtime engine.
Those missing interfaces must be qualified before implementing privileged native execution.

## Proposed ownership and activation interface

The following is a concrete design for security review. It is not an installed API, signature,
accepted decision, authority token or claim of current AWS/DB state.

### Controller and independent database owner

- The deployment controller owns the existing Maezo ECS service/task stop/start operation and
  deployment lease. Infrastructure changes remain in the Terraform state that already owns
  each service, network and database resource; no imported/shared-Aurora shortcut is implied.
- The owning database administrator/controller independently owns runtime login fences and the
  short-lived provisioning DB role. Runtime and application task roles cannot issue those actions,
  change LOGIN, create roles, grant themselves membership, read provisioning secrets, or restart
  services while the deployment lease is held.
- The bootstrap JVM owns only its process and exact planned engine objects while the controller
  holds the external fence. It exposes no network listener and is never shipped as an executable
  path in an application/worker/agent request flow.
- Separate bootstrap and deployment identities authorize separate commands. Their authentication
  is the controller's short-lived workload identity plus the DB role bound to the operation.
  Merely setting B peer `purpose` or an `engine_user` string does not establish that authority.

### Enforceable stop/start fence and independent readback

1. Controller acquires an exclusive deployment lease in its authoritative control store using a
   conditional write, bound to tenant/environment, engine DB/schema, candidate artifact digest,
   operator identity and run id. Conditional acquisition/renewal and start permissions must be
   enforced in that control plane; a caller-written lease file is insufficient.
2. Controller suspends automatic rescheduling/scaling and stops **all** engine instances and
   other database writers capable of deployment/authorization changes. It reads ECS/control-plane
   task state until each owned task is STOPPED and enumerates unexpected tasks as a refusal.
3. DB owner fences every runtime login principal using an independently authorized mechanism such
   as `NOLOGIN`, after inventorying connection-pool/proxy identities and role memberships. It
   terminates their existing sessions under explicit ownership and reads `pg_roles`, memberships
   and `pg_stat_activity` back. Applications must not have superuser/CREATEROLE or another login
   path that can assume the fenced role. A pooler using a different login identity must be fenced
   too. Stopping tasks alone, revoking CONNECT while PUBLIC/inherited CONNECT survives, or checking
   one `application_name` is insufficient.
4. A separate short-lived provisioning login is issued only to the one-shot task. The issuer reads
   back its exact database/schema/table privileges, expiry, role memberships and effective identity.
   Native schema migrations are complete already. Existing human receipt/audit objects and grants
   are included in the immutable before-state digest. No password appears in argv, stdout, plan,
   Git, receipt, artifact manifest or exception. Secret retrieval must use the gateway-owned
   credential boundary; the native JVM receives only a protected ephemeral credential mount.
5. The independent controller/DB observations are bound to the run id and candidate digest and
   reread immediately before native execution. The design requires a verifiable control-plane
   observation identity/trust store and freshness window selected by the owners. No signature,
   timestamp, trust root or acceptable freshness value is invented by this compiler.
6. Runtime startup remains denied until all execution receipts, resulting files and final B
   readiness checks are accepted. Failure, lease loss, ambiguous outcome or incomplete readback
   keeps the login/start fence closed. The controller may recover via receipt queries; it must
   never undo data blindly or reopen legacy raw REST as fallback.

This deliberately separates **evidence of observed shutdown** from an **enforced prohibition on
restart/new runtime login**. Both are required. The future implementation must test the controller
and DB fence, not trust a self-reported `runtime_stopped: true` boolean.

## Proposed native executor and transaction/receipt boundaries

Use an isolated non-listening JVM pinned to the same CIB 2.1 native ABI and reviewed artifact
hashes. `databaseSchemaUpdate=false`, tenant checks enabled, job executor inactive, no demo
plugin and no HTTP server. A schema-missing error is fatal; provisioning does not auto-migrate.
The executable must explicitly validate the controller/DB ownership interface above before any
native call. The test fixture's anonymous/auth-disabled helper is not copied into production.
The exact privileged native entrypoint/authentication design remains for security review.

Two independently idempotent commands avoid inventing definition IDs before deployment:

1. **Deployment command**, under deployment identity: the request pins the full BPMN/DMN resource
   set, bytes/hashes, deployment tenant/name, expected existing deployment/version digest and run
   id. In one native transaction, check the precondition, deploy only those resources, resolve the
   actual generated definition IDs/versions and persist a deployment receipt containing resource
   hashes and resulting definition identities. Never guess an ID or call `latest` during activation.
   A repeated run id with identical digest returns the committed receipt. Different bytes/digest
   under the same run id conflict. A lost reply is recovered by exact receipt query, not redeploy.
2. **Grant command**, under bootstrap identity: bind the reviewed final B manifest (using actual
   deployment receipt IDs) and the compiler's exact grant plan. In one native transaction, require
   the same deployed definitions and preexisting authorization digest, create only missing exact
   workload grants, verify the result and persist a grant receipt. Existing exact grants are
   idempotent. Extra/broader grants on a workload identity are a refusal needing explicit separate
   remediation; they are never silently accepted as the minimum. No existing human/other-principal
   grant is removed, widened or rewritten. No password/admin membership is created for runtime.

A transaction receipt table/migration is an explicit prerequisite owned with database migration
review. Proposed uniqueness is `(tenant, operation_kind, run_id)` with an immutable request digest
and committed result; this proposal must be frozen before implementation. Both native changes and
receipt use one `CommandContext`/enlisted database transaction. A local receipt file cannot replace
that atomic record. Human command receipts, audit chains and D5/D6 grants are not repurposed.

After commit, the controller publishes exact final boundary bytes and hash atomically to the
candidate configuration. Publication failure leaves runtime fenced and is recoverable from the
committed receipts. Bootstrap credentials are revoked/expired by their issuer, DB sessions are
closed, and mounts removed by the owning task runtime. Disposal and role expiry are read back.
Sensitive files are never archived in the public evidence packet.

For activation, start one B candidate under the still-restricted deployment controller. Its
controller-restricted validation DB login has only runtime database privileges, cannot assume the
provisioning role, and is inaccessible to normal tasks; normal runtime logins remain fenced. Verify
exact image/mount/policy digests, certificate/native grant readiness and positive/negative smoke
flows, then restore only the reviewed runtime login/start privileges. Failed candidate validation
keeps the normal runtime fenced. Rollback uses the same controller with a compatible prior image
and preserved receipts/history; it does not delete deployment evidence or reverse migrations.

## Acceptance and decision status

Before native source implementation: independently review this proposed stop/start/DB fence,
its observation trust interface, separate identities, failure transitions and native privilege
entrypoint; freeze the request/receipt schema and migration ownership. These are real dependencies,
not reasons to declare D complete after compiling a plan.

Before activation: ROOT schedules real PostgreSQL/CIB proofs of atomic deployment/grants/receipt,
rollback on failures, repeated/conflicting run ids, preserved D5/D6 state, login/lease loss races,
credential disposal, exact B readiness and runtime inability to exercise provisioning authority.
C's worker lifecycle/source-lock propagation and actual caller cutover remain separately pending.

**ADR assessment (proposed):** this approach appears to elaborate ADR-0049 D7/D8's already-required
separate deployment identity and bootstrap-before-runtime ordering, rather than contradict an
accepted ADR. Security/architecture review must decide whether a dedicated ADR/addendum is required
for the new control-plane/DB authority and receipt protocol. No ADR is amended or marked Accepted
here. Any design that instead admits runtime administrative REST, disables B, uses anonymous setup
as operational authority, or grants a break-glass bypass would change the accepted boundary and
requires an explicit new decision; this proposal does none of those things.
