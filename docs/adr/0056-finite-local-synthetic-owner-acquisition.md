# ADR-0056: Finite local synthetic owner acquisition

**Status:** Accepted bounded engineering allocation; this contract and its source require independent review before effect implementation. No live authority or production approval.
**Date:** 2026-09-15
**Area:** Gateway / installation ownership

## Context

ADR-0055 allocates protected initial D owner records and an authenticated owner port. Its HTTP client does not have an existing owner service behind it. The same installer connection must observe uncommitted PostgreSQL DDL; a remote observation or a second connection cannot substitute. The earlier allocation explicitly did not introduce role creation.

## Decision

Extend that allocation with an opt-in, finite, independently entrusted **local owner process**, including fresh synthetic IAM roles and a fresh DynamoDB control table. Its inputs are independently enrolled owner trust and signed resource intent. Actual identities, policies, issuance and database facts come from its own concrete operations. Caller-provided records, hashes, clients, cursors and signing callbacks grant no authority.

The closed contract is [D-LOCAL-OWNER-CONTRACT.md](../../deploy/cibseven/secured/D-LOCAL-OWNER-CONTRACT.md). Permanent installation, run, scope and resource-name reservations are committed before factory effects. A durable invocation claim precedes credential issuance. Ambiguous effects and crashes consume the invocation; recovery never reissues credentials or replaces resources. The ordinary application, CI and deployment paths do not select this factory.

This first source slice contains only canonical record parsers, deterministic key/state calculations and refusal tests. It does not acquire credentials, authenticate an owner, call AWS, open PostgreSQL, sign receipts or install anything. Subsequent reviewed source must implement the exact fixed producer, current readback and enforced process credential custody; there is no assumed external service.

The L1 producer ends at committed initial installation with the unchanged **UNRECONCILED** fence, without a prepared generation. L2 observation/reconcile/close/ROOT/reserve and L3 credential/deployment/grant/preparation/native continuation remain separate missing source. No Q2 purpose or runtime/controller activation is allocated here.

## Exact journal and no-TTL read permissions

The enrollment owner's immutable append transaction requires a separate ConditionCheck on the prior EVENT row. Its identity/session/resource profiles permit ConditionCheckItem only on the exact enrollment table; independent enrollment readers and control-table writers/readers retain their denials. ConditionCheckItem is not conditioned on unsupported EnclosingOperation; exact prior-row/payload comparisons remain fixed-producer obligations.

Qualifying no TTL requires actual DescribeTimeToLive readback for each exact table, with TimeToLiveStatus strictly DISABLED. Missing or transitional/unknown status refuses. This additive read is permitted to the exact owner/readback roles; no TTL mutation is granted. The inert description validator neither calls AWS nor authenticates a response. Both corrections preserve permanent claims, no reissue, scope/SourceIdentity, the 2048-byte session cap and same-session PostgreSQL requirements.

## Consequences and traceability

ADR-0004 tenant isolation, ADR-0006 PHI exclusion, ADR-0007 authenticated provenance, ADR-0020 audit boundaries, ADR-0027 PostgreSQL durability and ADR-0049 migration ownership remain binding. The new technical idempotency journal is not a second audit chain. DL-0032's source-versus-runtime distinction applies: parsable records are not qualified producers. The predeploy secret-custody and secret-injection findings motivate explicit private gateway handles rather than environment echoes.

No historical D SQL, manifest, record or accepted ADR is changed. ADR-0055's former no-role-creation scope remains true for that slice; this ADR explicitly adds the subsequent fresh factory. Actual owner identity, signing/store custody and resource authorization remain external inputs. Qualification still requires independent source review and genuine timed AWS/PostgreSQL evidence.

## E0 clarification — contract/source review pending

[D-LOCAL-OWNER-E0.md](../../deploy/cibseven/secured/D-LOCAL-OWNER-E0.md) defines the additive parent-installed ingress record, one-live-supervisor nonreuse boundary and initial-only source candidate/review domain. Fresh synthetic keys/UIDs are permitted when genuinely installed under the real external parent authority; self-asserted authority is refused. The inert values grant no effect permission. Original v1 policies and migrations remain unchanged. L1 does not supply the separately unallocated L2 writer or manufacture L3 CandidateDecision.
