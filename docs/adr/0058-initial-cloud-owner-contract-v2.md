# ADR-0058: Original-creation L1/L2 cloud contract v2

**Status:** Accepted bounded contract-first allocation; independent source review required. Effects and topology are not approved.
**Date:** 2026-09-15

## Context

ADR-0056 v1 ends at initial D installation. Its W/R credentials cannot be reissued to perform L2 reconciliation. The separately reviewed original-creation design allocates distinct bootstrap writer W, receipt reader R, continuation writer L and control observer C0. Parent-issued cloud inventory reader Q remains distinct from C0. Expanding the exact owner session exceeds the inline STS policy limit in the reviewed specimen.

## Decision

Define a separate inert `maezo.d7-local-owner.initial-l1-l2.v2` protocol in [D-LOCAL-OWNER-V2.md](../../deploy/cibseven/secured/D-LOCAL-OWNER-V2.md), with closed intent, resource, managed-policy, purpose, session and identity records. Pure functions render exact policies, permanent reservation/issuance keys, actual IAM Path/RoleName decomposition and path-free assumed-role identity comparisons. They do not authenticate AWS observations or create a runtime capability.

The v2 owner request uses one exact same-account managed session-policy ARN. Its full policy bytes, policy identity/default version and actual parent no-edit custody require later genuine qualification. Child policies retain the 2048-character limit; actual PackedPolicySize is a separate gate. No wildcard permission broadening recovers an oversized policy.

The initial ECS topology remains **UNQUALIFIED_OC_F01**: the earlier empty-cluster plus no-service-linked-role proposal is contradictory. This contract selects no runnable topology, grants no exception for a named service role and has no effect admission path. Topology, complete observation/parent producer protocols and authority custody must be independently closed before effects. This does not require account or Organizations changes or a new user approval flow.

## Boundaries

All v1/E0 records, policies and parsers remain unchanged. Original D SQL/catalog and production configuration remain unchanged. No cloud SDK, credentials, signing, durable journal, subprocess/FD operation, PostgreSQL connection, factory or controller is added. Issuance/disposal records describe values; actual custody and disposal cannot be inferred from their states. Parent authority, L2 PG admission, L3 preparation, native qualification and Q2 are not established.

Traceability: ADR-0055 initial owner readback, ADR-0056 finite acquisition, ADR-0004 tenant isolation, ADR-0006/0007 provenance and custody, ADR-0027 durability, ADR-0049 migration ownership and DL-0032 amendment of accepted decisions through a new ADR. The independently reviewed original-creation design `06eefe4c…` approves only this contract-first allocation; its OC-F01 effect hold remains open.
