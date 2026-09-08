# Human engine commands — ADR0049 D5

This package implements the engine transaction mechanics and their dedicated wire
profile. It does not activate the six real DTO bindings or establish D6 durable
admission, D7 REST cutover, Cognito operation, production credentials or approval.
AUTH mandatory clinical narrative, ESC resolution narrative and PAGTO source/DRAFT
reconciliation remain with the PHI/binding packages. Required human justification
must remain in the PHI workflow; there is no silent deletion, ratification or
`human_approved` substitute here. The only decision projector is the explicitly
opted-in nonclinical synthetic test form. Production configuration sets
`enable_synthetic_fixture` to `false`; all decision forms then refuse activation.

## Wire profile and ports

`profile.py` supplies frozen `HumanCommand`, `SigningContext` and `seal`. The
Gateway supplies its purpose-scoped Ed25519 signing callable through its dedicated
credential partition. Nothing here reads a signing key, environment secret, OIDC
token, A2A key or PHI HMAC key. `seal` neither admits nor dispatches a command: the
D6 relay must dispatch only an already durable, authorized intent/outbox record.

`human-envelope.v1` fixes Ed25519 over RFC8785 canonical UTF-8 of every envelope
field except `signature`. The object fields are `schema`, `purpose`, `algorithm`,
`audience`, `issuer`, `tenant`, `key_id`, `issued_at`, `expires_at`, `digest`,
`command`, `signature`. The digest is SHA256 of canonical `command`; signature is
unpadded canonical base64url. All numeric tokens are forbidden. Epoch seconds,
versions/revisions and arbitrary precision integers are decimal strings; no float
rounding, duplicate names, nonfinite values, lone surrogates or normalization.
Sorting uses UTF-16 code units. Depth32 and incoming65536bytes are technical wire
limits, not monetary bounds. This is a versioned restricted JCS profile, separate
from the existing ADR0039 A2A canonicalizer.

`HumanCommand` lists every allowed field; it has no generic variable map. Its
semantic identity includes tenant/task/command, stable issuer/subject/principal,
workload, process definition id/key/version/digest, task/form key/version/digest,
expected assignee, task/authority/membership/evidence revisions, evidence ref/digest,
audit intent and typed operation/outcome. Claim/release have `outcome=null`.
The synthetic decision has `outcome=ACK`; the actual SP-OP forms are inactive.
A new transport envelope may refresh expiry; the original command/digest remains
unchanged. No refreshed envelope can make a stale decision current.

Private endpoints in the Tomcat webapp:

- `POST /maezo-human/v1/commands`: signed `purpose=human-command`, atomic effect+receipt.
- `POST /maezo-human/v1/authority`: separate `purpose=human-authority` publisher.
- `GET /maezo-human/v1/receipts/{task_id}/{command_id}`: `Authorization: Maezo-Human <base64url envelope>`;
  purpose `human-receipt`, using a trusted human-command key. Signed query fields:
  `schema=human-receipt-query.v1`, tenant, workload_ref, task_id, command_id,
  principal_ref, principal_issuer, principal_subject, payload_digest.

All require direct Tomcat mTLS with a peer SPKI SHA256 pinned to the same trusted
workload as the signing key. Forwarded headers cannot supply identity. Certificate
chain validation and `clientAuth=required` are configured on the Tomcat TLS
connector; network security groups admit only the dedicated workloads. Browser,
agent and public ingress do not gain this endpoint. Existing engine-rest is still
subject to the separate D7 migration; this package does not secure its19callers.

The receipt `human-engine-receipt.v1` is a minimized technical engine record. It
contains status, operation, identity/digest, principal/workload/audit-intent refs,
consumed/resulting task revision, engine receipt ref and recording epoch. It is
returned only after CommandExecutorTxRequired commits. It is not the final D6
`HumanCommandReceipt`: the relay must link a durable result audit before publishing
that contract. Receipts contain neither decision inputs nor clinical narratives.

## Trust, provisioning and revocation

`MAEZO_HUMAN_TRUST_FILE` must name a read-only trusted deployment file containing:

- `schema=human-trust.v1`; trusted `tenant`, `audience`, `engine_name`.
- Explicit positive decimal `max_lifetime_seconds`; no seven-day/default retention.
- Explicit boolean `enable_synthetic_fixture` (false for production).
- `keys`: nonempty array with both purposes `human-command` and `human-authority`.
  Each entry has id, purpose, workload, peer_spki_sha256,
  public_key_spki_base64 (Ed25519 X509 SubjectPublicKeyInfo), not_before, not_after.
  Validity uses explicit decimal epoch strings. Public key material, workload and
  peer identity cannot be shared across the two purposes. Key ids are unique.

The plugin fails startup without valid configuration, matching engine name, same
PostgreSQL datasource and preinstalled schema/tenant row. No runtime DDL/bootstrap
or fallback datasource exists. Apply `java/src/main/resources/human-schema-postgres.sql`
to the same actual engine schema with the deployment owner, then explicitly insert
the trusted tenant with revision0. Runtime code has no delete or receipt-update path.
Retention/expurgo must follow the separately approved security/DPO policy.

The authority publisher signs a closed `human-authority.v1` command: schema,
tenant, workload_ref, operation and expected_revision plus exactly the operation
fields below. Publication holds the same tenant serialization lock as execution,
compares the current tenant revision and increments it once atomically:

| operation | additional fields and effect |
| --- | --- |
| principal | principal_ref, issuer, subject, active boolean, valid_until, groups array. Immutable issuer/subject cannot be rebound; groups are authorized current human memberships. New membership revision is the new tenant revision. |
| evidence | task_id, process_definition_id, evidence_ref, evidence_digest, valid_until. Must match an active task in this tenant; bumps the task optimistic revision as well as the evidence revision. Snapshot is an immutable PHI/source reference, not raw evidence. |
| revoke-key | key_fingerprint (SHA256 of trusted Ed25519 SPKI). Append-only revocation tombstone survives key-id/config rotation; reusing the same key bytes under another id does not resurrect it. |

A valid signed publication is not evidence that upstream facts are true. The
publisher must consume the authorized source contract. Its operational protocol is:
freeze affected external authority/evidence before change; publish disable/new pin
with current engine CAS; wait for the engine commit acknowledgement; then acknowledge
external revocation/change and permit new snapshot issuance. If this handshake or
required source freshness cannot be established, stop issuing decisions and let
explicit valid_until expire. Grant publication occurs only after upstream grant
commit. Lost update acknowledgements require reading/reconciling current engine
state; never blindly refresh expected_revision and replay an old grant. Any retry
of an old CAS fails. This is no atomicity promise across IdP/FHIR/tenant databases.
Configuration/key rotation requires quiescence and revocation acknowledgement
before old material is removed; never clear engine tombstones. Missing publisher,
freshness contract or deployment network policy blocks production activation.

## Transaction and race boundary

`EngineStore` gets only `CommandContext.getDbSqlSession().getSqlSession().getConnection()`
and rejects autocommit/non-PostgreSQL. It never obtains another connection or calls
commit/rollback. Tenant row `FOR UPDATE` serializes authority updates, revoked key
checks and receipt identity. Task current revision, assignment, candidate group
membership, actual deployed process bytes and current accepted evidence are checked.
Claim/release force the task optimistic fence; completion uses the real task entity
and the normal deferred engine flush. A `COMMITTING` transaction listener reads the
actual resulting revision and inserts the receipt on the same connection after that
single flush and before persistence commit. A `COMMITTED` listener makes the result
available to the plugin, which unwraps it only after the command executor returns. A failed
insert, flush or final engine commit rolls all of them back. No remote call occurs
inside the transaction. Losing timers, reassignment or other task clients must fail
the CIB optimistic revision check. D7 must separately deny alternate REST writes to
human authority and decision variables before cutover.

The tenant lock stabilizes published authority, evidence and revocation state;
it does not stop wall-clock validity from expiring during a database wait. New
commands therefore recheck signed envelope/key, principal and evidence deadlines
immediately before mutation and again in `COMMITTING`, after the last receipt SQL.
Receipt reads and exact retries recheck the new envelope/key and current principal
after their reads; they do not require the original evidence or membership revision
to remain current. Authority publication rechecks its envelope/key and any published
deadline after its SQL and normal engine flush. These checks use the current clock,
not the transaction's start timestamp, and introduce no TTL. This is the application
check immediately before JDBC commit, not a claim that physical commit takes no time.

Exact retries return the original receipt only after current key/workload/principal
validation. Changed digest, principal or workload for the same tenant/task/command
returns409. A revoked principal cannot retrieve even a previously committed receipt.
Missing receipt is404, malformed wire400, untrusted authority403, stale state409 and
unavailable engine503. Errors never echo inputs. A transport timeout is uncertain,
not evidence of rollback or completion.

## Reproduction levels (ROOT owns all live services)

From repository root, focused offline checks:

```sh
uv run --no-sync python -m pytest -q tests/unit/portal/test_engine_profile.py
uv run --no-sync ruff check src/maezo/portal/engine tests/unit/portal/test_engine_profile.py tests/integration/test_portal_engine_package.py
uv run --no-sync mypy src/maezo/portal/engine
mvn -B -f src/maezo/portal/engine/java/pom.xml clean package
```

The normal Maven test phase runs profile/servlet unit tests and compiles but excludes
`AtomicEngineIT`. Its13methods expand to15cases (three operation rollback cases).
To execute them, ROOT supplies explicit MAEZO_HUMAN_IT_JDBC_URL,
MAEZO_HUMAN_IT_DB_USER and MAEZO_HUMAN_IT_DB_PASSWORD for its isolated PostgreSQL
service. The URL must not contain currentSchema/options overrides. The fixture
creates a random owned schema before any engine SQL, checks the active schema on
every fixture connection and drops only that owned schema afterward. Missing
configuration fails; it never skips or silently connects to a default database.

```sh
mvn -B -f src/maezo/portal/engine/java/pom.xml -Dtest=AtomicEngineIT test
```

This runs actual pinned CIB2.1 embedded with PostgreSQL. It does **not** prove Tomcat
packaging/TLS. For the separate image lane, with repository-root build context:

```sh
docker build -f deploy/cibseven/Dockerfile.human -t maezo-human-engine:d5 .
```

The image installs the plugin jar in `/camunda/lib`, adds its entry to the existing
`/camunda/conf/bpm-platform.xml`, and deploys `/camunda/webapps/maezo-human`. It is
Tomcat, not CIB Run. ROOT must provide the trusted file, SQL/tenant bootstrap and
Tomcat TLS connector with CA and required client authentication. Keep HTTP bound
only to the isolated test network for the negative plaintext probe. Then set:
MAEZO_HUMAN_PACKAGE_HTTPS_URL, MAEZO_HUMAN_PACKAGE_HTTP_URL,
MAEZO_HUMAN_PACKAGE_CA_FILE, MAEZO_HUMAN_PACKAGE_CLIENT_CERT,
MAEZO_HUMAN_PACKAGE_CLIENT_KEY, and run:

```sh
uv run --no-sync python -m pytest -q -m integration tests/integration/test_portal_engine_package.py
```

These3smokes prove plugin startup/registration and direct-TLS boundary, not browser
journeys, a valid HTTP decision, production network policy or transaction races.
Normal pytest/CI does not invoke Maven or build this opt-in image. A follow-on
packaging/caller-migration package must wire tagged Maven and actual Tomcat/TLS
checks into integrated CI; this bounded package makes no CI execution claim.

Root must preserve logs, exact image digest, runtime Java/CIB version, actual SQL
schema,15case results and3smoke results as separate receipts. Additional integrated
D4/D6/REST and real SP-OP projections remain required before portal completion.

Primary implementation references: [CIB2.1 plugins](https://docs.cibseven.org/manual/2.1/user-guide/process-engine/process-engine-plugins/),
[CIB2.1 DbSqlSession source](https://github.com/cibseven/cibseven/blob/v2.1.0/engine/src/main/java/org/cibseven/bpm/engine/impl/db/sql/DbSqlSession.java),
[RFC8785](https://www.rfc-editor.org/rfc/rfc8785),
[Ed25519 RFC8032](https://www.rfc-editor.org/rfc/rfc8032).
Repo trace: ADR0049 D3–D7/D9/D10; ADR0006/0007/0013/0039; DL0006/0017/0030/0048;
SP-OP-AUTH-001, SP-OP-ESCALATION-001, SP-OP-PAGTO-001. Audit findings
`audit-chain-from-rows-ts-reorder-verify-flap`, `no-denial-boundary-escape-reachability-blindspot`
and `lgpd-negar-fundamentado-classified-neutral` remain no-repeat references; this
package does not alter the audit chain, introduce a denial route or classify a
clinical decision as technical success.
