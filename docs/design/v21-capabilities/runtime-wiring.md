# v2.1 shared runtime candidate — AW1

State: **PROPOSED / no production bindings / no source acceptance**.

Baseline: `e7b14522a4f70242504d2b152a57ee5269ee66e2`. Admitted planning
bundle remains at the absolute ROOT path
`/Users/familia/code/maezo-operadora/docs/audits/BPMN-CA-2026-10/`.
The implementation checkout is
`/Users/familia/.codex/worktrees/v21-capability-execution/maezo-operadora`.

## Implemented candidate interface

`gateway/capabilities/models.py` mirrors the exact twelve-field internal envelope
and eleven operation request/result field maps in `plan.json.internal_operations`.
`v21-capabilities.proposed.v1` identifies this engineering candidate; it is not a
provider contract or AMH wire version. Models reject extra fields, coercion,
unknown operations, duplicate JSON names and absent required references. Internal
immutable tuples serialize as the proposed reference arrays. Optional references
remain absent/`None`, without fabricated boolean or numeric substitutes.

`DeclaredCaseKind`, `DeclaredCaseStatus` and `DeclaredMilestoneKind` have no shipped
members. Trusted composition can supply immutable source-declared memberships only
after the affected W0 contract is published and verified. Parsing that vocabulary
does not prove its publication; admission must verify the source contract.

`CapabilityService.execute(envelope, payload)` shares the dispatcher across
consumers. It reparses input, selects an explicitly injected source, obtains an
admission lease, revalidates before source I/O, parses the source result, requires
independent source-result attestation, and revalidates before disclosure. Missing
authority/source, a denied admission, malformed/no-op output, outage or technical
timeout yields a bounded refusal. A returned pending/unknown/declined domain result
retains that exact status. Technical success never means business completion.

Authority, publication, effective enforcing policy, currentness and audit ports
are injected. There is no permissive default. Unit doubles demonstrate mechanics;
they are not source acceptance or real receipts. The source contract must own
atomic business revision, receipt-first replay, durable uncertainty and recovery.
The dispatcher never retries commands; a technical timeout does not settle a
domain wait, release a hold or assert an absent effect. No local idempotency-key
compositor is introduced while W0's compositor is pending.

## Existing roots to extend only after affected W0 gates

| Existing mechanism | Exact integration site | Outstanding qualification |
|---|---|---|
| Sanctioned agent construction | `gateway/tool_registry.py:build_agent_seams` and `build_agent_seam_context` | Agent task/tool/actions, source owner contract, per-operation policy and strict admission; shadow denial never authorizes a new binding |
| Runtime construction | `runtime/agent_runtime/service.py:_build_tool_deps` | Explicit source/admission dependencies and actual graph consumer |
| Number-one ingress | `platform/webhooks/service.py:_build_lucas_turno` | Ratified administrative handoff/conduta, authenticated tenant and identity scope, clinical/human priority |
| AMH context | `gateway/tool_registry.py:build_amh_context`; `gateway/amh.py:AmhSubjectContextExecutor` | Current allowed clinical purpose, current consent, consumer and actual injected `AmhRuntime`; no commercial-purpose alias or legacy FHIR fallback |
| Native human recovery | `gateway/intake/native_dispatch.py:AuthDispatcher` | Reuse the receipt/currentness mechanism under a separately published domain contract; AUTH DTOs and receipts remain AUTH |
| Staff case source | `gateway/staff_cases/service.py:StaffCaseService`; `gateway/intake/cases.py:CaseService` | Published case kind/status, requester and lifecycle authority; reads are not generic case writes |
| Metadata communications | `gateway/communications/service.py:CommunicationService`; `portal/api/production.py` | Qualified factory, protected content/recipient and sender/delivery receipts; inbox availability is not delivered service |
| Reply/resume | `platform/integrations/agent_resume.py:ResumeHandler`; `platform/webhooks/whatsapp/lucas_retomada.py:LucasRetomada` | Published domain event/result mapping, current recipient/window, source revision and deduplicated reply |
| Milestones | `tools/workers/events.py`; notification bridge | Closed source-owned event catalogue, enlisted state/outbox and genuine publication evidence |

No root, registry, effect class, AgentCard, agent YAML, policy, pin, flag, engine
process or published wire is extended by this package. The candidate does not
claim AW1's production admission, durability or ingress-to-source gates passed.

## Governing evidence and validation

Trace: admitted V2.1 §5/AW1 and `COMPATIBILITY-AND-CONTRACTS.md` OP01–OP11;
W0 proposal in this directory. Preserve DL-0042's scoped identity authorization,
DL-0045's pending effect approvals and DL-0053's current billing-only handoff.
Authoritative predeploy findings read from the baseline Git object include
`cred-business-key-missing-collision-fallback`,
`redact-payload-force-key-gap-operator-named-flow-fields`,
`matricula-raw-to-general-zone-notifications-topic` and
`lucas-marina-glosa-a2a-origination-never-wired`: no identity guessing, raw payload
logging, PHI egress or claimed-but-unconsumed production binding is introduced.

Focused tests cover closed field maps, unresolved memberships, forged model copies,
operation dispatch, absent sources/authority, stale/revoked currentness, malformed
or unattested output, technical timeout without retry and shared consumers using
the same module. Real provider, durable replay/concurrency and CIB integration
qualification remain separate gates. No engine fixture or live acceptance is
manufactured by unit tests.
