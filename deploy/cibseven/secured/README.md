# D7-B: opt-in authenticated engine package

This package implements the 47 exact base/derived D7-A rows from
`gateway/engine_schemas.py`. It does not install operational identities, migrate callers,
activate the six real human forms, enable TISS, or change the deployed image. Build with
`docker build -f deploy/cibseven/Dockerfile.secured .` only in the separately authorized
ROOT engine window. The existing `Dockerfile.human` and its five image/TLS assertions are
unchanged. This image must remain an isolated opt-in until C/D/E and real acceptance pass.

The actual pinned native ABI requires `AuthenticationProvider` in the REST webapp loader.
`human-command-engine-1.0.0-rest-spi.jar` contains only that adapter; the common JAR excludes
it. Its public bridge is necessary because matching Java package names across loaders do
not grant package-private access. The REST API dependency is provided, never shaded into
the common JAR. Native `ProcessEngineAuthenticationFilter` populates user/empty-groups and
the one exact tenant. It has no Basic/JWT/pseudo or anonymous fallback.

`BoundaryFilter` is installed in actual Tomcat `conf/web.xml` for REQUEST, FORWARD, INCLUDE,
ERROR and ASYNC, including other webapps and native authentication whitelist routes. It
accepts only REQUEST. Every native raw mutation and alternate engine alias is denied.
`TraceRefusalValve` is the first Engine valve in the pinned secured `server.xml`.
The sole mTLS connector explicitly keeps `allowTrace="false"`. Tomcat 10.1.47's
[CoyoteAdapter](https://github.com/apache/tomcat/blob/10.1.47/java/org/apache/catalina/connector/CoyoteAdapter.java#L756-L780)
sets a suspended 405 response before filters, then invokes the Engine pipeline. This
terminal valve replaces that TRACE refusal with the same finite 403 JSON boundary
response; it never dispatches TRACE to a servlet or echoes request data. Earlier parser
errors and absent-context 404s retain their original status/handling. Other methods use
the unchanged pipeline. `SecureLayout` requires the explicit disabled connector and
first-valve placement before availability. The Tomcat API is a pinned provided dependency,
never shaded into the engine JAR. This is the ADR-0049 D7 transport refusal implementation;
TLS, authentication, method permissions and legacy-image configuration are unchanged.

The camunda webapp's old local filter/servlet stack is replaced by `ClosedServlet` in this
opt-in image (original descriptor retained outside webapps). Its Tasklist/Cockpit/Admin and
`/api/engine/*` routes cannot preempt the global filter via legacy login/session handlers.
Database history is retained. This is **not** applied to the existing human image or live UI.
The filter consumes the real `jakarta.servlet.request.X509Certificate` chain, verifies
PKIX against pinned roots, exact leaf certificate/SPKI/issuer/subject/URI SAN, client EKU,
key use and validity. Forwarded headers do not provide authority. Revocation removes the
leaf from the pinned deployment manifest and restarts with the new manifest digest; the
old running configuration fails when its mount changes, including established TLS sessions.
There is no stale-policy fallback. Rotation windows are explicit peer timestamps and may
overlap for the same identity and identical capabilities. This version does not claim
online OCSP/CRL revocation or uncoordinated hot policy reload.

## Frozen interface for C and D

Environment: `MAEZO_ENGINE_BOUNDARY_FILE` is an absolute canonical mounted path;
`MAEZO_ENGINE_BOUNDARY_SHA256` is SHA-256 of its exact bytes. No secret is passed in a
workload request. The file has exactly these keys:

| Key | Meaning |
| --- | --- |
| `protocol` | `maezo.engine-boundary.v1` |
| `tenant`, `environment`, `engine_name` | Exact deployment identity, no wildcards or defaults |
| `not_before`, `expires_at` | Explicit epoch seconds for the complete policy |
| `roots` | Nonempty list of `{path, sha256}` public PEM CA files |
| `files` | Exact `{path, sha256}` mounted files, checked on every request and at commit |
| `listener_port` | The sole HTTPS port in mounted Tomcat configuration |
| `max_tasks`, `max_lock_millis`, `max_poll_millis` | Explicit transport/resource bounds; no clinical timing policy |
| `peers` | Nonempty list of exact certificate identity bindings below |

Each peer has exactly `certificate_sha256`, `spki_sha256`, `issuer_dn`, `subject_dn`,
`uri_san`, `purpose`, `engine_user`, `identity`, `not_before`, `expires_at`, `capabilities`.
`identity` is the unmodified A `EngineIdentity.document` shape (tenant, environment,
workload, workload_version, issuer, subject, origin). Origin is `verified_mtls`, issuer
equals the X.500 issuer name, subject equals the single `spiffe://` URI SAN. The peer
window must lie within the certificate-chain validity window. Purpose is one of
`nonhuman`, `human-relay`, `bootstrap`, `deployment`, `observer`; only nonhuman may hold
A capabilities. Bootstrap/deployment have no administrative route in B: D must implement
and review its bounded one-shot provisioning/deploy mechanism. Runtime callers receive
neither identity. The human relay continues to require the separate D5 signed envelope
and human signing trust. It never authenticates to workload operations.

Each capability binding has exactly:

* `document`: the **unmodified** `EngineCapabilityProfile.document()` from A.
* `digest`: its A SHA-256 canonical JSON digest. The schema must exactly equal a packaged
  registered row, including sources, field origins, null markers and projections.
* `source_kind`: empty for no source; otherwise `locked_external` or `completed_human`.
* `source_worker_id`: exact current source lock owner for `locked_external`, empty otherwise.
* `attestations`: exact mappings `{name, source_variable, human_task_definition}` for every
  engine/prior-human field and non-tenant correlation key. No extras or duplicates.
  `@business_key` means the actual source process business key. These are trusted deployment
  bindings, not claims accepted from a caller. Human fields require a completed source
  task and committed decision receipt, plus the actual task's matching historical variable
  update (except business key, which comes from the source process). CONTAS automatic
  empty evidence uses only A's explicit conditional exception and still matches engine state.

The source binding is outside the A digest and inside the pinned boundary manifest digest.
C must propagate the actual source task reference; it cannot manufacture an `EngineAuthority`
or silently drop a required source field. Completing a source task before its handoff start
is invalid: the source lock must remain current through COMMITTING.

`POST https://<verified-host>:<port>/engine-rest/maezo/v1/operations` accepts exactly
`Content-Type: application/json`, no query parameters, one bounded strict JSON object:

```json
{
  "protocol": "maezo.engine-operation.v1",
  "capability_digest": "<A-profile-digest>",
  "operation": "<registered-A-operation>",
  "process_key": "<exact-process-key>",
  "resource_ref": "<business-key-or-external-task-id>",
  "variables": {},
  "correlation": {},
  "all_matching": false,
  "error_code": "",
  "topic": "",
  "message": "",
  "worker_id": "",
  "parameters": {},
  "source_ref": ""
}
```

`variables`, `correlation`, `parameters` are the parsed contents of A's corresponding
`*_json` fields, not native REST `type/value/valueInfo` envelopes. All other fields retain
A semantics. `source_ref` is the current source external task or completed human task
identifier when required. `resource_ref` is required for fetch too (A's existing contract),
but fetch selection is the exact topic/definition/tenant profile; it is not an instance filter.
No arbitrary process variables, local variables, startInstructions, binary data or Java
object deserialization are accepted. JSON is bounded to 1 MiB and depth 16, duplicates,
invalid UTF-8, surrogate escapes, NaN/infinity and integer overflow refuse. Monetary Long
typing by name (`total_glosado_candidato_centavos`) is preserved.

Responses are `{protocol: "maezo.engine-result.v1", capability_digest, result}`. Start
returns id/definition_id/tenant. Active/history reads return complete bounded lists of
id/definition_id/state; exceeding the bound refuses, never returns truncated absence.
Fetch returns task/definition/process/execution/topic/worker/lock/retries plus only the
requested registered variable projection (`{type, value}`, lower-case engine type).
Native optimistic-lock filtering is consumed **after commit**, so a losing fetch does not
return a task it failed to lock. Fetch long polling uses fresh native transactions under
the authenticated request up to `asyncResponseTimeout`; native asynchronous REST is closed.
Lifecycle operations return `{applied: true}` only after the command executor commits.
Correlation enumerates exact-tenant/version subscriptions and targets those process IDs,
never an unscoped `correlateAll`; it returns `{correlated: count}`. Engine values/receipt/
audits are never included in safe refusal messages.

`GET /engine-rest/maezo/v1/readiness` uses mTLS and native SPI and verifies nonempty
capabilities, exact deployed definitions and native grants. It returns the outer policy
digest plus all profile digests. `GET /engine-rest/version` is the sole permitted raw
REST route, authenticated, and is **not** capability readiness. Unknown paths/methods,
query strings, encoded/ambiguous paths, multipart/binary/form data and redispatch refuse.
Native provider may return 401; a TLS alert can occur earlier. JSON refusals are fixed
400/403/503 codes; native optimistic conflicts return 409. C must distinguish unavailable
authority from “no instance” and preserve durable dedup/audit/retry semantics.

## Native authorization and transaction boundary

Actual `ExternalTaskCmd` invokes `CommandChecker.checkUpdateProcessInstanceById`.
The actual checker accepts process-instance UPDATE or process-definition UPDATE_INSTANCE.
That same UPDATE_INSTANCE also permits native variable writes. Consequently every raw
mutation route stays closed, even for workers with legitimate external-task permission.
The plugin never impersonates an administrator or disables native authorization/tenant checks.
Its pre-command fence refuses direct native API commands for configured workload users
outside the typed command. Both TX_REQUIRED and REQUIRES_NEW chains are covered. Source
lookups and operations use native authorized services; exact external-task rows are locked
on the enlisted PostgreSQL connection. Fresh peer/policy/current-lock checks run after
blocking SQL, before mutation and in COMMITTING. A custom-post freshness interceptor adds
the D7 policy check **after** D5's receipt listener without changing D5/D6 source code.

Minimum definition grants for the typed rows (D still provisions and verifies them):

| Operation | Native grants |
| --- | --- |
| All target definitions | READ on the exact PROCESS_DEFINITION key |
| Start | CREATE_INSTANCE on that definition, plus CREATE on PROCESS_INSTANCE `*` (native resource has no existing id before create) |
| Active reads | READ_INSTANCE on the exact definition |
| Historical reads | READ_HISTORY on the exact definition |
| External lifecycle / correlation | READ_INSTANCE + UPDATE_INSTANCE on the exact definition |
| Source attestation | READ/READ_INSTANCE/READ_HISTORY as needed on the exact source definition; completed source receipt in the same tenant schema |

Native grants are key-scoped; tenant, exact definition ID/version, topic, worker, field and
source constraints therefore additionally remain enforced in the typed command. Missing
grant configuration keeps readiness false. Each typed execution repeats this exact grant
preflight, including source grants, so native query filtering cannot turn revoked
READ_INSTANCE/READ_HISTORY into an authoritative empty result. Bootstrap must not add blanket task UPDATE,
admin, wildcard definition grants or a caller-controlled tenant to make callers pass.

Required pinned files include actual `conf/server.xml`, `conf/web.xml`, `conf/bpm-platform.xml`
and engine-rest, human and camunda descriptors. The server must have exactly one
SSLEnabled HTTPS connector with `SSLHostConfig certificateVerification="required"`, no proxy
attributes and `Host autoDeploy="false"`. Tomcat itself loads the mounted keystore/truststore;
failure must remain fatal. No live listener, SG or certificate provisioning is established
by rendered XML tests.

## Honest delivery and ROOT verification

The A registry has 15 base schemas plus 32 derived schemas. It has **no DMN or READ_STATUS
row**, no André PAGTO start, and only two reviewed external topics. B cannot silently
grant the remaining workers/bridges/DMNs. Extend A through contract review, regenerate
`engine-schemas-v1.json` using `export_schemas.py`, then review and test the new operation
implementation. No permissive registry loading or synthetic production mode exists.

Real source-human correlation/PAGTO positives depend on legitimate completed source
decision receipts. D5 currently enables only its synthetic human form; inserting a fabricated
receipt does not close that prerequisite. Full C caller migration, D bootstrap/environment
wiring and E's 157 fixture dispositions remain dependent packages.

ROOT must run `WorkloadEngineIT` on explicit isolated PostgreSQL (same
`MAEZO_HUMAN_IT_JDBC_URL`, `...DB_USER`, `...DB_PASSWORD` inputs as the existing D5 real tests).
It is tagged integration, excluded from unit tests and fails on missing settings. Its
setup-only embedded engine bootstrap is isolated in a fresh uniquely owned schema; it
does not claim Tomcat or TLS coverage. Retain the 37 D5 tests and existing five real
image/TLS assertions unchanged. After independent source review, additionally run the actual
image bypass matrix in `ACCEPTANCE.md`, C/D/E tests, integrated CI and separately reviewed
operational cutover. This author package is neither final GK nor a production-ready portal.
