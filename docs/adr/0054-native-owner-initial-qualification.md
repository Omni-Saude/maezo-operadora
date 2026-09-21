# ADR-0054: Initial native owner qualification receipts

**Status:** Accepted for the explicitly authorized isolated engineering source allocation; source/runtime qualification and production acceptance remain pending.
**Data:** 2026-09-15
**Area:** Security / installation ownership

## Context

The frozen D7 contract reserves NativePrincipalQualificationBinding from a separately qualified native-owner operation. Native v2 has no durable producer for that operation. Its six runtime tables and nineteen functions are immutable installation resources. D's previous grant catalogue predates the initial native install, so its ordinary assertion cannot adopt the new exact native and ACT grants before the NATIVE_QUALIFIED event.

## Decision

Allocate an independently versioned `maezo_native_owner_v1` installation and immutable technical operation receipt store. Reuse authenticated inherited native/D migration-owner authority; create no runtime/issuer/PUBLIC grant or synthetic external authority. The store records initial qualification of an already installed, PREPARED/UNADMITTED principal and produces the existing closed D binding only from a committed operation with current owner-bound readback. Historical committed reconciliation does not assert present admission.

The D consumer uses its same enlisted owner cursor. Only the initial NATIVE_QUALIFIED event may adopt exact source-derived native grants and ACT schema USAGE plus the eleven frozen external-task column SELECT grants. Every other original guard invariant is preserved; the original SQL catalogue assertion runs after event insertion and before commit. No historical SQL changes, runtime activation, new lease, restore/rotation semantics or controller wiring.

The complete allocation, lock order, closed receipt contract and invariant equivalence matrix are in [NATIVE-OWNER-QUALIFICATION.md](../../deploy/cibseven/secured/NATIVE-OWNER-QUALIFICATION.md), sections 2–4.3. This implements the owner port reserved by `d7-control-0001.json` and traces to ADR-0004 tenant isolation, ADR-0006 PHI exclusion, ADR-0007 authenticated provenance, ADR-0020 no second audit chain, ADR-0027 Postgres durability and ADR-0049 owner migration boundaries. The technical idempotency ledger is not an audit/custody chain or substitute for canonical audit.

## Consequences

Independent source and runtime qualification remain required. Finite tests or local owner-store mechanics do not establish AWS resource ownership or D/native admission; genuine external prerequisites are mandatory for those lanes. Exact complete grant comparison and immutable source custody are intentionally stricter than accepting caller-provided catalogue digests. The separately versioned schema adds a migration artifact and a retained idempotency record without modifying frozen runtime allocation.

## Supersedes

None. No accepted ADR text is changed; no clinical, DPO or production decision is ratified.
