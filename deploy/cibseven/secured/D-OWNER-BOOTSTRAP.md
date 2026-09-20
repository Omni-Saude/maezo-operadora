# Inert synthetic D owner bootstrap

ADR-0055 records the allocation before implementation. No command or runtime
entrypoint invokes this package. `gateway.d7_external_owner` must receive an
independently configured HTTPS/mTLS owner endpoint, Ed25519 public key and actual
AWS client/principal binding. There is no local-file approval fallback. A remote
owner producer is not included: without a genuinely implemented owner boundary,
positive execution remains unavailable. That service must independently observe
actual policy/current DB ownership, not echo the challenge request's facts.

`owner_authority_contracts` defines every closed field and uppercase key. The
installation UUID is unique for one enrollment/scope/bootstrap operation. Anchor,
permanent scope reservation, original ROOT and result are one absent-only write.
No record update/delete or alternate operation retry exists. An ambiguous result
is reconciled by strong transactional readback of all four exact expected items.
A later ROOT transition is outside this initial-only replay contract.

The actual table must match ARN, TableId, ACTIVE, nonreplicated PK/SK string schema.
The authenticated owner response names exact principal, operation, request digest,
DB session/catalog and installed operational identities. Readback is direct and
nonrecursive. Startup, prepared generations and native admission stay unbound.

The external service and credential gateway are deployment prerequisites, not
implied by implementing their verifier. Missing/expired/untrusted service proof
refuses before mutation. Installation uses the existing SQL owner installer;
this adapter grants no CREATE ROLE, LOGIN, CONNECT or runtime authority itself.

## Closed wire and key allocation

Enrollment fields are exactly: protocol, mode=PUBLIC_SYNTHETIC, enrollment_id,
installation_id, control_scope_id, bootstrap_operation_id, scope, table_arn,
table_id, writer_arn, observer_arn, owner_login, owner_oid,
source_manifest_sha256, cib_abi_sha256, operational_principals,
initial_root_sha256. UUIDs must be canonical lowercase with hyphens. Scope is the
unchanged closed D Scope, including complete DatabaseBinding. Operational rows
are exact purpose/native_actor/login_name/login_oid, unique sorted receipt_read
identities; this initial slice has no one-shot actor allocation.

The installation anchor key is (D7OWNER#INSTALLATION#<installation>, ANCHOR).
Permanent scope key is (D7OWNER#SCOPE#<scope>, RESERVATION).
The globally unique operation receipt key is
(D7OWNER#OPERATION#<bootstrap-operation>, BOOTSTRAP), so a different installation
cannot reuse the operation UUID. The fourth item is unchanged (D7#<scope>, ROOT).
All owner rows have exactly PK/SK/kind/document_bytes/document_sha256. Anchor and
reservation contain the closed scope binding: protocol, enrollment_sha256,
enrollment_id, installation_id, control_scope_id, bootstrap_operation_id, scope,
table_arn, table_id, initial_root_sha256. Receipt contains protocol, operation_id,
request_sha256, binding and root_sha256. No authority comes from these hashes
without current authenticated enrollment/resource qualification.

One installation has one bootstrap operation and one scope. Initial readback and
SQL installation may repeat against the same exact initial records; later ROOT
mutation or catalog overlays are unsupported here. Initial partition reads are
bounded to exactly ROOT (Limit2); any extra item or pagination continuation
refuses rather than pretending a partial query was complete.

Owner HTTP response is closed by OwnerEnrollmentClient.acquire. Its signed
policy_receipt_ref/digest must identify a genuine current protected owner
verification operation; the client does not provide that producer. Same-session
PG transport checking requires explicit verify-full TLS1.3, pinned root CA bytes,
host/port/server identity, actual backend/session/schema OIDs. Current incarnation
and policy are independently verified by the external owner, not inferred from
PostgreSQL names or a caller-provided UUID.

## Remaining source dependency S-D-OWNER-PRODUCER

No existing owner service or producer was found in the inspected repository/donor.
The HTTPS client is one concrete authenticated acquisition transport; **a new
network service is not an architectural prerequisite**. This slice implements
its verifier and callers, not the genuine fact-producing authority. Supplying an
AWS manifest/profile does not make that missing implementation exist. Do not
activate this path against a response generator which signs the request's hashes.

The minimum next independently reviewed source package is a finite, local
**gateway-owned owner qualifier**, kept separate from the bootstrap request
caller. It can execute inside the explicitly authorized fixture owner process;
no always-on service or public endpoint is required. Its concrete composition is:

1. Acquire an independently protected synthetic enrollment from the owner-selected
   trust/custody boundary; close and authenticate its actual origin, scope and
   operation restrictions. The caller cannot choose a signer/profile in its wire
   request. Distinguish expected review pins from observed ownership.
2. Construct actual AWS clients from the explicitly authorized gateway credentials,
   qualify current STS identity/session and collect the complete supported IAM
   role inline/attached/boundary/session-policy and table resource-policy context.
   Resolve actual policy bytes/versions and compare their complete closed approved
   capability model, including prevention of controller/readback owner writes and
   re-enrollment/overwrite paths. If an organization/session policy or delegation
   affects the supported proof and cannot be established, refuse rather than
   infer effective authority from one DescribeTable/GetCallerIdentity call.
   No broad arbitrary IAM-policy interpreter or AWS simulation is claimed here.
3. Strongly read the actual table installation/reservation/operation/ROOT state,
   verify full identities and exact nonreuse/conflict rules, and enforce the
   initial-only authorized operation. For bootstrap, absence plus protected
   enrollment and the atomic permanent reservation establishes the new scope;
   absence alone never authorizes it.
4. Observe the SAME actual owner PG connection, TLS endpoint/CA/server identity,
   backend, DB/schema/incarnation and complete role/ACL/native operational-principal
   inventory. Post-DDL catalog is still uncommitted in the installer transaction;
   a separate remote connection cannot observe it. A local qualifier can consume
   that same cursor and reconstruct the allowed exact initial delta from the
   reviewed migration/source, without committing or manufacturing rows. A remote
   transport cannot sign caller-supplied catalog bytes and call that independent
   observation. This is a material composition boundary to resolve before live use.
5. Produce a closed bounded operation authorization/receipt from those actual
   observations and independently reviewed intent. Bind operation/request/session,
   policy observation and installation provenance; reserve/commit exact outcomes
   through the fixed writer and retain private evidence. No endpoint may offer
   "sign these facts". Missing credential-broker/native operation facts still
   remain unavailable and cannot be filled by metadata.

To use this local qualifier, replace the current concrete HTTP-client dependency
at the composition root with its independently reviewed concrete acquisition
class (or a sealed union of the two concrete implementations), preserving exact
request/response validation and no generic accepting callback. Its source and
refusal tests must land before declaring the initial installer runnable. The
current HTTP path is usable only if an actual conforming independently qualified
producer exists and can establish the same-session ownership requirement; none
has been verified here. Do not deploy a stub simply to satisfy that condition.

Actual owner-approved AWS account/resources/profile and DB/credential custody are
subsequent environment inputs. The qualifier implementation is a separate source
dependency. Neither category is supplied by the offline protocol tests.

## Initial acquisition refusal and uncertainty boundaries

The enrolled authority and installer must own the same physical connection,
checked at construction and before beginning a transaction. Equal role/database
session tuples from different connections do not satisfy this condition.

Before the four-item bootstrap write, a strong partition read must show no scope
rows or continuation token. A visible orphan refuses without creating permanent
anchors. This preflight is not a DynamoDB predicate lock; exclusive writer
qualification remains a producer prerequisite. The four absent-item conditions
and post-write exact inventory checks remain in force. A query transport failure
after submitting a write returns UNKNOWN without resubmission; pre-write failure
refuses without a write.

A signed grant's actual deadline is projected from the pre-request wall/monotonic
clock pair. A shorter signed interval stays shorter even when wall time moves
backward. Both clocks must remain within the interval before use; response time
consumes that same budget.
