# ADR-0057: Initial PostgreSQL readback and management qualification

**Status:** Accepted bounded engineering allocation for closed-contract authoring. Independent contract/source review and real installation qualification remain required before effects.
**Date:** 2026-09-15
**Area:** Gateway / isolated D installation

## Context

ADR-0055/0056 stop before initial reconciliation and reservation. The frozen D controller requires reconcile F1→F2/RECOVERY_REQUIRED, then close F2→F3/CLOSED, with the current F equal to the committed close receipt. Existing receipt reads require a permit; issuing that permit advances F and breaks the exact initial sequence. Observation completeness also includes genuine observer and issuer sessions; labelling them unowned does not make them disappear.

## Decision

Allocate the initial-only PostgreSQL profile in [D-INITIAL-PG-CONTRACT.md](../../deploy/cibseven/secured/D-INITIAL-PG-CONTRACT.md), with two separate namespaces and fixed read-only functions installed before original D baseline capture:

- The genuine nonlogin D schema owner owns an observer-only initial-state/committed-receipt helper. It reads through existing ownership; no login gets direct D-table SELECT and no permit or F mutation occurs.
- A separate nonlogin statistics owner owns a fixed census helper, with explicitly allocated `pg_read_all_stats` membership. It has no D/ACT SELECT or cross-helper EXECUTE. The actual scoped observer composes independently acquired initial-state and census results; census cannot authenticate caller-supplied installation bytes.

Original D SQL and manifest remain unchanged: **seven D tables and the complete object manifest (57 CREATE FUNCTION statements in the pinned source)**. Native-v2's separate six-table/nineteen-function ABI is not the D ABI. Original D grants/membership assertions include protected-role external grants in the genuine baseline. External helper definitions, owners, ACLs and statistics-owner membership are independently pinned and revalidated; grant capture alone does not pin their bodies.

The supported dedicated Linux/PG16 initial profile has real owner-controlled source, separate issuer/observer processes and private credentials, certificate-only routes, a sealed host admission interval and a bounded connection handoff. Complete raw signed inventories remain unchanged. A future initial-only qualifier may partition exact authenticated management rows only after proving their current authority and custody; generic/recovery closure stays strict. Parsing a record, matching a digest or setting a Python flag creates no authority.

## Scope and consequences

This slice defines inert closed records and negative controls only. It does not install SQL/helpers/roles, grant privileges, open PostgreSQL, sign observations, instantiate a qualifier, skip management rows or execute controller transitions. The receipt helper and census SQL implementation, host supervisor, actual role/source/readback qualification and cloud original-creation continuation remain source work. No producer or service is assumed to exist.

A separate versioned original-creation cloud profile must supply the actual L2 writer/observer authority. L1 v1 records, policies and no-reissue rules remain unchanged; this ADR grants no AWS action or L3 handoff. Controller orchestration, rotation, ACL amendment, restore, native/Q2 and production remain unbound/deferred.

## Traceability

ADR-0004 isolation, ADR-0006 PHI boundaries, ADR-0007 identity/provenance, ADR-0027 PostgreSQL durability, ADR-0049 D7, ADR-0055/0056 ownership remain binding. DL-0017 requires real pool/session behavior. The retained D stage1 P5 contract reserves independent harmlessness qualification while preserving complete census; no blanket exception follows. The reviewed L2 v2 architecture and V2-01/02 corrections establish the need for this explicit allocation. Predeploy credential-custody, actual rendered-identity and migration-order findings require genuine installed privilege/source readback, not expected configuration.

No existing accepted decision or migration is weakened. Independent contract review precedes source implementation; actual PG/cloud/host qualification precedes any runtime claim.
