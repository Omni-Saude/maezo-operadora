# Inert original-creation cloud contract v2

ADR-0058 authorizes this **contract-first** slice only. The canonical field lists and pure comparisons live in `src/maezo/platform/engine_bootstrap/owner_local_v2_contracts.py`. `V2Record` is immutable canonical bytes, not a signed, authenticated or effect-capable owner. There is no SDK, credential access, signature consumer, persistent journal, pipe/process operation, PostgreSQL admission or production binding.

## Domain and pending topology

Profile `maezo.d7-local-owner.initial-l1-l2.v2`, mode `PUBLIC_SYNTHETIC`, topology **only** `UNQUALIFIED_OC_F01`. The earlier one-empty-ECS-cluster/no-service-linked-role proposal is contradictory. No live topology is selected or certified by this code; no service-role name is whitelisted. The separate NO_CLUSTER proposal, if later reviewed, needs its own exact observation semantics and source gate. Changing a string to QUALIFIED is refused.

The v2 intent contains the exact six v1 identity UUIDs, complete existing D Scope, source tuple, independently installed trust associations, resource declaration and positive interval <=900000ms. Its operation names describe only proposed initial bootstrap/install/reconcile/reserve, with no prepare/native/runtime operation. Old v1/E0 parsers and files remain unchanged and do not accept v2 intent. This source does not adapt E0 ingress or initial source review to v2.

The administrative parent remains an actual independently enrolled authority and finite custodian. New synthetic keys/resources are legitimate when genuinely installed under that authority. Supplied manifests, policy hashes, two equal snapshots and actor lists cannot prove exclusion of other administrators or STS sessions. Actual AWS environment/custody material and the concrete parent producer are still unavailable to this package. No account/Organizations change or new owner approval requirement is introduced.

## Closed values

All kinds require exact fields, canonical JSON bytes (no whitespace aliases), existing duplicate/depth/safe-integer controls and <=131072 bytes. `value()` reparses immutable bytes; changing its result does not mutate the retained record. A record shape is separate from `bind_to_intent`, which checks exact source, purpose, role, request and template associations. Neither check establishes provenance.

| Kind | Exact body fields beyond protocol |
|---|---|
| intent | mode profile topology enrollment_id installation_id run_id control_scope_id bootstrap_operation_id acquisition_id scope trust resources source not_before_ms deadline_ms operations |
| resources | installation_id account region control_table_arn writer_arn reader_arn continuation_arn control_observer_arn owner_policy_arn |
| managed_policy | intent_sha256 parent_interval_id source_artifact_sha256 policy_arn policy_id version_id created_at_ms observed_at_ms document document_sha256 |
| purpose | intent_sha256 parent_interval_id purpose role_arn request_sha256 source_artifact_sha256 child recorded_at_ms |
| session | intent_sha256 parent_interval_id purpose request request_sha256 source_artifact_sha256 |
| role_identity | intent_sha256 purpose role_arn role_id path role_name created_at_ms observed_at_ms |
| identity | intent_sha256 purpose request_sha256 role_arn role_id session_arn user_id caller_account caller_arn caller_user_id issued_at_ms expires_at_ms packed_policy_size |
| issuance | intent_sha256 purpose claim_sha256 revision state previous_sha256 evidence_sha256 recorded_at_ms |

Protocols are `maezo.d7-local-owner.<kind-with-hyphens>.v2`. Trust has the original exact v1 trust fields plus `cloud_reader_arn cloud_reader_role_id`, all role identities distinct and account-bound. Source keeps `git_sha tree_sha artifact_sha256 manifest_sha256 cib_abi_sha256 policy_profile_sha256`; source hashes are associations requiring later actual source/owner qualification. Purpose child has exactly `instance_id boot_id pid start_ticks uid executable_sha256 channel_id`; all three UUIDs are closed, process/start/UID positive. These values do not observe a real kernel or deliver a credential.

Role resource names refine the design's IAM path rendering: actual Path `/maezo-d7-v2/`, RoleName `w-I`, `r-I`, `l-I`, `c-I`, where I is installation UUID. This keeps installation-specific RoleNames across IAM paths and preserves reviewed ARN byte lengths. T is `maezo-d7-I-control`. Owner managed policy uses the same fixed path and PolicyName `owner-I`. Parent role ARN paths/names have explicit ASCII grammar and IAM 512/64 limits; full ARNs <=2048. Fixed resource declarations reject aliases, cross-account/region/installation substitution and alternate paths.

`role_identity` binds the actual prospective CreateRole/GetRole ARN/RoleId/Path/RoleName tuple; `identity` separately binds the returned STS tuple. **STS ARN excludes the IAM role path:** `arn:aws:sts::account:assumed-role/RoleName/SessionName`. Exact UserId is RoleId + ':' + SessionName. Caller account/ARN/UserId must equal that tuple; `compare_role_session` joins exact role and session. This is a value comparison only: actual same-handle GetCallerIdentity and API result provenance remain effect-source gates. A bare expected RoleId cannot authenticate a supplied packet.

## Exact policy and request templates

Purposes: W bootstrap writer, R independent receipt reader, L initial continuation writer, C0 control observer, Q parent-issued cloud inventory reader, O finite owner. W/R/L/C0 are fresh factory roles. Q remains a distinct parent-installed/issued role with no T data exception. No omitted observer or reassigned Q-as-C0 alias is accepted.

All templates are canonical Version2012-10-17, fixed statement order, sorted Action/Resource lists, and only the exact conditions below. `compare_policy` requires exact bytes; it does not simulate IAM. Identity policies additionally require the reviewed NoDelegation denial and no unknown attached/boundary policy in later actual producer qualification; this slice renders session, trust and table templates only and does not claim complete live role installation.

- W metadata on T plus GetItem/Query restricted to the four original bootstrap PKs K. Transactional Put K requires EnclosingOperation=TransactWriteItems, ForAllValues:StringEquals LeadingKeys K and Null=false.
- R same metadata and K reads, no writes.
- L metadata, GetItem/Query and transactional Put restricted to P=`D7#control_scope_id` with the same conditions.
- C0 metadata and P reads only.
- Q exact read-only IAM/ECS/scheduler/autoscaling API list frozen in `CLOUD_READS`, Resource `*` for complete account inventory. No DynamoDB data, mutation, delegation or PassRole action. Full query/response/pagination/source-to-authority closure is **not** implemented or approved by rendering these actions.
- O exact seven-statement owner document: E metadata/GetItem/Query; E transactional Put; exact four-role CreateRole/PutRolePolicy; exact O/ingress/enrollment-reader/four-child IAM role reads; T create/policy-at-create/metadata; four-child AssumeRole/SetSourceIdentity; E ConditionCheckItem without EnclosingOperation. The future source permits PutResourcePolicy only as the actual CreateTable dependent permission, never a later policy rewrite.

T policy has exactly eight Deny statements: foreign data except W/R/L/C0; Put except W/L; nontransactional Put; W keys outside K; L keys outside P; missing write key; unconditional other data mutations including ConditionCheckItem; unused BatchGet/PartiQLSelect/Scan. No resource Allow. GetItem supports the actual adapter's TransactGet constituent; no fictitious IAM TransactWrite action. Metadata is separate from key-constrained data APIs. IAM does not enforce SK/body/ROOT CAS/absent-only semantics; those remain fixed producer and custody requirements.

Every child request uses the exact fixed RoleArn, RoleSessionName (`d7-A`, `d7-r-A`, `d7-l-A`, `d7-c-A`, `d7-q-A`), inherited owner SourceIdentity, DurationSeconds900 and compact Policy. No tags/MFA/caller extras/managed child policy. Trust is one exact issuer ARN principal, AssumeRole/SetSourceIdentity and exact principal/session/source conditions. O and Q trust the enrolled ingress; W/R/L/C0 trust O. Actual resolved RoleIds and issuer custody remain mandatory.

O request uses `d7-o-A` and exactly one same-account managed PolicyArns entry, no inline Policy. The fixed specimen remains **2376 bytes**, refused as inline; W/R/L/C0 sample lengths1322/769/982/599 and Q915 fit. Actual parameters are rendered and each supplied child ASCII policy is refused above2048; packed policy size must later come from genuine service response and be <=100. The managed policy record compares exact actual PolicyId/version/create time/ARN/document/source/parent interval; `compare_managed_snapshots` rejects any changed tuple or backwards observation time. A later equal observation is not proof no transient policy edit occurred. The finite parent's real no-edit interval, original creation evidence, complete version inventory and actual session application remain unimplemented gates.

## Permanent keys and consumed state

Thirteen reservation keys retain the **same v1 collision namespaces**: seven installation/run/full Scope/control-scope/enrollment/bootstrap/acquisition anchors plus six exact resource ARN digest reservations (T/W/R/L/C0/owner-policy). v2 cannot evade a v1 collision by selecting a new prefix. The source returns logical keys only, with no transaction or store reader.

Purpose key is `(custody-domain, D7LOCAL2#ISSUE#I#purpose, CLAIM)`. O/Q use PARENT_INGRESS; W/R/L/C0 use ENROLLMENT. Altering acquisition UUID does not release the same installation/purpose key. Actual parent-store installation and claim bodies/absence transactions/current readers remain later source, not inferred from this tuple.

Issuance sequence is CLAIMED → ISSUE_STARTED → ISSUED → DELIVERY_STARTED → DELIVERED → DISPOSED; UNKNOWN is reachable from every nonterminal state and is terminal; DISPOSED is terminal. Exact intent/purpose/claim hash, next revision, previous canonical hash and nondecreasing time are required. Skips, wrong claims and transitions out of UNKNOWN/DISPOSED refuse. CLAIMED has revision1/no prior/evidence; success states require evidence digest; before-effect states have no outcome. Returned effect/credential/disposal evidence does not yet have an implemented authenticated consumer. No state record can wipe a leaked credential, commit a claim or authorize reissue.

## Remaining gates

Before effects: resolve OC-F01; freeze full current parent/Q/managed-policy installation and observation protocols, exact paginated/error/bounds mapping and all immutable claim/delivery/UNKNOWN/disposal payloads; implement actual protected source/credential/FD custody and no-edit/start exclusion. Author/reviewer separation and exact-head review remain required.

Goodall's separate L2 PG contract owns committed F1/F2/F3 readback, stats/management ownership, complete seven-table D catalog and actual barrier/census. This cloud slice grants no SQL authority and changes no migration. Preserve 120-second leases, 5-second requests and 15-second observation ages; actual timed feasibility remains unproved. L3 original-creation delegation, preparation/native qualification, Q2, full V11 and production acceptance remain separate unfinished requirements.
