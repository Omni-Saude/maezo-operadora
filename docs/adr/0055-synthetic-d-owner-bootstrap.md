# ADR-0055: Synthetic D owner bootstrap and authenticated readback

**Status:** Accepted for the explicitly authorized bounded engineering allocation; independent source/live qualification and production acceptance remain pending.
**Data:** 2026-09-15
**Area:** Security / installation ownership

## Context

D OwnerAuthority is distinct from ADR-0054's native owner. Its initial installer
requires actual external controller roots and operational-principal ownership.
Caller manifests and table metadata alone do not establish that authority. The
approved bounded design ends at bootstrap/install/readback, with the SQL fence
UNRECONCILED and generation preparation unavailable.

## Decision

Allocate closed versioned synthetic enrollment and external owner operation
records, acquired from an independently pinned authenticated owner boundary.
The owner must independently qualify current policy, principal and database
ownership; signing caller hashes is not a conforming owner implementation.
No local enrollment file, discovered credentials or accepting default is used.
If that external owner service/proof is unavailable, all operations refuse.
The client implementation does not claim that a real owner service is installed.
An HTTPS service is one transport choice, not an architectural requirement; the
minimum local gateway-owned producer and same-session composition remain an
explicit source dependency S-D-OWNER-PRODUCER in the referenced document.

Enrollment binds the exact synthetic scope, account/region, table ARN/TableId,
installation and operation IDs, DB incarnation, source/migration pins and actual
operational principal inventory. A fresh challenge and short authenticated owner
response bind each operation to its request and current actual session/catalog.
The response is verified with an independently injected public trust pin over
mTLS; the request cannot select its own trust root or owner endpoint.

Create permanent installation anchor, scope reservation, exact existing initial
ROOT and bootstrap receipt in one absent-only transaction. Installation identity
has one bootstrap operation and one fixed scope; changing operation ID cannot
reuse it. Direct strong transaction readback accepts only the exact complete
original four-record outcome. ROOT drift, loss or partial receipt is not a fresh
installation. Owner prefixes are separate from the unchanged D7 scope namespace.

Actual authenticated AWS table identity and schema are checked before writes.
The protected writer signs only fixed conditional transactions; callers receive
no generic request tunnel. IAM policy must exclude controller/readers from owner
mutations; immutable condition construction is additionally trusted writer code,
not an assertion that IAM enforces absence on PutItem. Actual deployment/policy
verification remains the external owner and live lane's responsibility.

The implementation and closed record fields are documented in
[D-OWNER-BOOTSTRAP.md](../../deploy/cibseven/secured/D-OWNER-BOOTSTRAP.md).
Traceability: ADR-0004 isolation, ADR-0006 secret/PHI custody, ADR-0007 provenance,
ADR-0020 no alternate audit chain and ADR-0049 installation ownership. Technical
operation records are idempotency/custody inputs, not canonical audit replacements.

## Consequences

No automatic application/CI/deployment entrypoint, role creation, runtime
orchestration, initial reconcile/close, generation reservation/preparation,
credential broker or CandidateDecision producer is introduced. Unsupported owner
methods refuse. Historical SQL, migration manifest and closed D grammar remain
unchanged. Real AWS/DB/owner material and independent review remain necessary;
offline protocol tests do not prove that authority exists.

## Supersedes

None. Existing ADRs remain unchanged; no clinical or production approval.
