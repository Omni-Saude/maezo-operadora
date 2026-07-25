"""A2A delegation dispatcher (ADR-0003, ADR-0007) — the SINGLE agent->agent delegation chokepoint.

Ported from the donor `Maezo-Healthcare-Plan` reference implementation (`src/maezo/a2a/
dispatcher.py:1-319`) as part of the T2.4 A2A W2 (delegation runtime) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §2/§3/§5 for the full port rationale, and this
module's `_audit_delegation` docstring for the AUDIT-SEAM REWRITE (the largest single port
friction — see design §2 friction table rows 1-3).

`DelegationDispatcher.delegate(envelope) -> DelegationResult` is the one place every agent->agent
delegation passes through. In this order:

  1. **Idempotency** (Guard 4): if `task_id` was already seen, return the prior result (or an
     in-flight handle) — NEVER re-execute the handler.
  2. **Anti-loop / contract**: validate the chain (guards 1-3 are already guaranteed at envelope
     construction) + that the target accepts the `task_type` (its Agent Card). Rejection ->
     a structured `DelegationResult` (never raised to the caller) + a `rejected` fact + an audit
     record.
  3. **Audit** the delegation (v2 `AuditRecord` via `emit_once`, T-F/design §3.2 "site 5") —
     delegation is an auditable external effect, audited BEFORE the effect.
  4. **Emit** the `requested` fact on Kafka before routing.
  5. **Route** to the target's handler (an injectable `agent_id -> handler` map). Success -> a
     `completed` fact; the `task_id` is cached for idempotency. A handler-internal `DelegationError`
     (e.g. a cyclic sub-delegation) -> a `rejected` fact AND a TERMINAL-outcome audit row (T-F
     follow-up, W4 — see `_audit_delegation_outcome`'s docstring): closes the gap where an ALLOWed
     delegation that then failed inside the handler left no audit trace distinguishing it from one
     still in flight.

The runtime wires real handlers (agent graphs) in W3; here the handler is an injectable callable
(`FakeAgentHandler` in tests). Handler and registry resolution are both injected.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from maezo.gateway.audit import AuditRecord

from .delegation import DelegationEnvelope, DelegationError
from .facts import DelegationFact, DelegationFactKind, build_fact
from .registry import A2ARegistry, RegistryError

if TYPE_CHECKING:
    from .idempotency import IdempotencyStore, StoredResult

# Handler of a target agent: receives the envelope, returns an output reference (output_ref). Like
# everything A2A, the output is a REFERENCE (FHIR/pseudonymized), never raw PHI.
AgentHandler = Callable[[DelegationEnvelope], Awaitable["HandlerOutput"]]


class AuditEmitter(Protocol):
    """The exactly-once audit-emit seam this dispatcher requires (T-F, design §3.2 "site 5").

    Structurally identical to `maezo.tools.mcp_cibseven.transport.AuditStartSink` — both are
    satisfied by the SAME production sink, `maezo.gateway.audit_postgres.PostgresAuditSink` — but
    declared LOCALLY rather than imported from `tools.mcp_cibseven`: per the design doc, A2A
    delegation is a DISTINCT effect surface (§2.1 site 5, "the second effect surface"), not a
    client of the process-start transport module, and the two seams should be free to evolve
    independently even though they share an implementation today. Typed as a Protocol (not the
    concrete class) so unit tests can inject a recording fake with no `asyncpg` import.
    """

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str: ...


def a2a_audit_dedup_key(tenant: str, task_id: str) -> str:
    """Exactly-once audit dedup key for an A2A delegation (design §3.2/§7.2 decision #4).

    ``f"{tenant}:a2a:delegate:{task_id}"`` — mirrors `tools.mcp_cibseven.transport.
    start_dedup_key`'s ``f"{tenant}:start:{process_key}:{business_key}"`` shape. Delegation is
    ALREADY `task_id`-idempotent (Guard 4), so keying the audit dedup the same way needs no extra
    business identifier. Exactly one `_audit_delegation` (pre-exec ALLOW/DENY) call happens per
    `_execute` invocation; the TERMINAL-outcome audit (`_audit_delegation_outcome`, W4) is a
    DISTINCT, second call with its own key (`a2a_audit_outcome_dedup_key`) — never the same key,
    so `emit_once` never collapses the two into one chain link.
    """
    return f"{tenant}:a2a:delegate:{task_id}"


def a2a_audit_outcome_dedup_key(tenant: str, task_id: str) -> str:
    """Exactly-once audit dedup key for the TERMINAL delegation-outcome audit (T-F follow-up, W4).

    ``f"{tenant}:a2a:delegate:{task_id}:outcome"`` — the `:outcome` suffix is what distinguishes
    this row from the pre-execution ALLOW row's key (`a2a_audit_dedup_key`), so the two audits
    are independently deduplicated and never collide under `emit_once`.
    """
    return f"{tenant}:a2a:delegate:{task_id}:outcome"


# v2 `AuditRecord.decision` follows the PEP ALLOW/DENY/REQUIRE_HUMAN convention (`gateway/
# audit.py`'s own docstring) — delegation admission is a binary allow/deny gate (no
# REQUIRE_HUMAN path exists in the dispatcher).
_DECISION_ALLOW = "ALLOW"
_DECISION_DENY = "DENY"

# Terminal-outcome decision (T-F follow-up, W4): distinct from the pre-exec ALLOW/DENY admission
# vocabulary above, so a chain reader never mistakes this SECOND, terminal row for a second
# admission decision — it records that an ALREADY-ALLOWED delegation's handler subsequently
# failed (see `_audit_delegation_outcome`).
_DECISION_FAILED = "FAILED"


class FactProducer:
    """Thin adapter over the injected Kafka producer, partitioned by tenant."""

    def __init__(self, producer: KafkaLike) -> None:
        self._producer = producer

    async def emit(self, fact: DelegationFact) -> None:
        await self._producer.send(
            fact.topic,
            fact.to_value(),
            key=fact.tenant.encode("utf-8"),
        )


class KafkaLike(Protocol):
    """Structural protocol for the producer (same shape as `gateway.audit.KafkaProducer`)."""

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None: ...


class RejectionReason(StrEnum):
    UNKNOWN_TARGET = "unknown_target"
    TASK_TYPE_NOT_ACCEPTED = "task_type_not_accepted"
    NO_HANDLER = "no_handler"
    EXPIRED = "expired"
    ANTI_LOOP = "anti_loop"


@dataclass(frozen=True, slots=True)
class HandlerOutput:
    """A target agent handler's output: a result reference (no PHI) + optional meta."""

    output_ref: str
    meta: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DelegationResult:
    """Structured result of `delegate`. Success or rejection — never a raise out of the dispatcher.

    `idempotent_replay=True` indicates this response came from the `task_id` cache (Guard 4): the
    handler did NOT run again.
    """

    task_id: str
    success: bool
    output_ref: str | None = None
    rejection_reason: RejectionReason | None = None
    detail: str | None = None
    idempotent_replay: bool = False

    @classmethod
    def ok(cls, task_id: str, output_ref: str, *, replay: bool = False) -> DelegationResult:
        return cls(task_id=task_id, success=True, output_ref=output_ref, idempotent_replay=replay)

    @classmethod
    def rejected(
        cls, task_id: str, reason: RejectionReason, *, detail: str | None = None
    ) -> DelegationResult:
        return cls(task_id=task_id, success=False, rejection_reason=reason, detail=detail)


@dataclass
class _InflightEntry:
    """Per-`task_id` state for idempotency. A lock guarantees a single concurrent execution."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    result: DelegationResult | None = None


def _as_replay(prev: DelegationResult) -> DelegationResult:
    """Mark a prior result as an idempotent replay (Guard 4), without re-executing the handler."""
    return DelegationResult(
        task_id=prev.task_id,
        success=prev.success,
        output_ref=prev.output_ref,
        rejection_reason=prev.rejection_reason,
        detail=prev.detail,
        idempotent_replay=True,
    )


def _stored_to_result(stored: StoredResult) -> DelegationResult:
    """Reconstruct a `DelegationResult` (replay) from the durable `StoredResult`.

    The persisted `rejection_reason` (a string) maps back to the `RejectionReason` enum when
    recognized; if it doesn't map (a future/unknown shape), the outcome is preserved without a
    typed reason (`detail` still carries the reason). Always `idempotent_replay=True` — the
    handler did NOT run on this delivery.
    """
    reason: RejectionReason | None = None
    if stored.rejection_reason is not None:
        try:
            reason = RejectionReason(stored.rejection_reason)
        except ValueError:
            reason = None
    return DelegationResult(
        task_id=stored.task_id,
        success=stored.success,
        output_ref=stored.output_ref,
        rejection_reason=reason,
        detail=stored.detail,
        idempotent_replay=True,
    )


class DelegationDispatcher:
    """Validates (anti-loop), audits, emits a fact, and routes a delegation to its target agent.

    Injected dependencies: `registry` (Agent Cards), `handlers` (agent_id->handler map), `audit`
    (the `emit_once` seam, T-F) and `facts` (fact producer). Idempotency by `task_id`:

      - `idempotency=None` (default): IN-MEMORY backing store (`_inflight`). Correct for a single
        process/test; the landmine is that it does NOT survive replica restarts (re-delegation).
      - `idempotency` set (`IdempotencyStore`): DURABLE cross-replica backing store (R9). The
        atomic per-`task_id` claim guarantees single execution even across replicas/restarts; a
        replay returns the persisted terminal result (`idempotent_replay=True`).
    """

    def __init__(
        self,
        *,
        registry: A2ARegistry,
        handlers: Mapping[str, AgentHandler],
        audit: AuditEmitter,
        facts: FactProducer,
        idempotency: IdempotencyStore | None = None,
    ) -> None:
        self._registry = registry
        self._handlers = dict(handlers)
        self._audit = audit
        self._facts = facts
        self._idempotency = idempotency
        self._inflight: dict[str, _InflightEntry] = {}
        self._guard = asyncio.Lock()  # protects the in-flight dict (entry creation)

    async def delegate(self, envelope: DelegationEnvelope) -> DelegationResult:
        """Dispatch a delegation. Idempotent by `task_id`; rejection = a structured result."""
        if self._idempotency is not None:
            return await self._delegate_durable(self._idempotency, envelope)
        return await self._delegate_inflight(envelope)

    async def _delegate_inflight(self, envelope: DelegationEnvelope) -> DelegationResult:
        """IN-MEMORY path (idempotency=None): Guard 4 via `_inflight` + a per-task_id lock."""
        entry = await self._entry_for(envelope.task_id)
        async with entry.lock:
            if entry.result is not None:
                # Guard 4 — re-delivery of the same task_id: return the prior result, without
                # re-executing the handler.
                return _as_replay(entry.result)
            result = await self._execute(envelope)
            entry.result = result
            return result

    async def _delegate_durable(
        self, store: IdempotencyStore, envelope: DelegationEnvelope
    ) -> DelegationResult:
        """DURABLE path (R9): atomic cross-replica claim -> execute -> seal the result.

        - `claim_or_get` WON (`None`) -> execute the handler and seal with `complete` (success OR
          rejection — BOTH are terminal; a rejection never retries, ADR-0003).
        - `claim_or_get` returns a `StoredResult` -> replay: the handler does NOT run; the
          persisted outcome comes back with `idempotent_replay=True`.
        """
        stored = await store.claim_or_get(tenant=envelope.tenant, task_id=envelope.task_id)
        if stored is not None:
            return _stored_to_result(stored)
        result = await self._execute(envelope)
        await store.complete(tenant=envelope.tenant, task_id=envelope.task_id, result=result)
        return result

    async def _entry_for(self, task_id: str) -> _InflightEntry:
        async with self._guard:
            entry = self._inflight.get(task_id)
            if entry is None:
                entry = _InflightEntry()
                self._inflight[task_id] = entry
            return entry

    async def _execute(self, envelope: DelegationEnvelope) -> DelegationResult:
        # --- Contract validation (chain/hops/budget already guaranteed at construction) ---
        validation = self._validate(envelope)
        if validation is not None:
            await self._audit_delegation(
                envelope,
                decision=_DECISION_DENY,
                basis=f"A2A:reject:{validation.rejection_reason}",
            )
            await self._emit(
                envelope,
                DelegationFactKind.REJECTED,
                reason=validation.detail or str(validation.rejection_reason),
            )
            return validation

        # --- Audit BEFORE the effect (delegation is an auditable external effect, ADR-0007) ---
        await self._audit_delegation(envelope, decision=_DECISION_ALLOW, basis="A2A:delegate:allow")
        await self._emit(envelope, DelegationFactKind.REQUESTED)

        # --- Route to the target's handler ---
        handler = self._handlers[envelope.target]
        try:
            output = await handler(envelope)
        except DelegationError as exc:
            # Structural failure raised by the handler (e.g. a cyclic sub-delegation): rejection.
            reason = RejectionReason.ANTI_LOOP
            # TERMINAL-outcome audit (T-F follow-up, W4): the pre-exec ALLOW row above already
            # fired — without this, an ALLOWed-then-internally-failed delegation would leave no
            # audit trace distinguishing it from one still silently in flight (see
            # `_audit_delegation_outcome`'s docstring).
            await self._audit_delegation_outcome(
                envelope,
                basis=f"A2A:handler_error:{reason}",
                detail=str(exc),
            )
            await self._emit(envelope, DelegationFactKind.REJECTED, reason=str(exc))
            return DelegationResult.rejected(envelope.task_id, reason, detail=str(exc))

        await self._emit(envelope, DelegationFactKind.COMPLETED, output_ref=output.output_ref)
        return DelegationResult.ok(envelope.task_id, output.output_ref)

    def _validate(self, envelope: DelegationEnvelope) -> DelegationResult | None:
        """Contract checks. Returns a rejection `DelegationResult`, or None (ok)."""
        if envelope.expired():
            return DelegationResult.rejected(
                envelope.task_id, RejectionReason.EXPIRED, detail="deadline has passed"
            )
        try:
            card = self._registry.lookup(envelope.target, tenant=envelope.tenant)
        except RegistryError as exc:
            return DelegationResult.rejected(
                envelope.task_id, RejectionReason.UNKNOWN_TARGET, detail=str(exc)
            )
        if not card.accepts(envelope.task_type):
            return DelegationResult.rejected(
                envelope.task_id,
                RejectionReason.TASK_TYPE_NOT_ACCEPTED,
                detail=f"{envelope.target} does not accept task_type={envelope.task_type!r}",
            )
        if envelope.target not in self._handlers:
            return DelegationResult.rejected(
                envelope.task_id,
                RejectionReason.NO_HANDLER,
                detail=f"no handler registered for {envelope.target}",
            )
        return None

    async def _audit_delegation(self, envelope: DelegationEnvelope, *, decision: str, basis: str) -> None:
        """Record the delegation in the audit hash chain (v2 `AuditRecord`) via `emit_once`.

        **AUDIT-SEAM REWRITE** (design §2 friction rows 1-3, §3.2 "site 5") — the donor audits via
        `self._audit.record(record)` against a donor-only `AuditLog` sink and a donor-only
        `AuditRecord(agent_id, agent_version, tenant, tool, input_hash, decision_basis,
        autonomy_level)`. v2 has NO `AuditLog` (only `AuditSink`/`PostgresAuditSink`, both exposing
        `emit_once`) and v2's `AuditRecord` has a DIFFERENT shape (`agent_id, tenant_id,
        agent_version, action, decision, details: dict, dmn_versions, model_id, prompt_version,
        prev_hash, record_hash`). This is a REWRITE against v2's shape, not a copy. Field mapping:

          donor `tool=f"a2a.delegate:{target}"`         -> v2 `action=f"a2a.delegate:{target}"`
          donor `agent_id=envelope.origin`               -> v2 `agent_id=envelope.origin`
                                                            (UNCHANGED: the chain originator is the
                                                            audited identity — design §3.3, ahead
                                                            of T-G's signed-Card identity binding)
          donor `decision_basis=basis` (free-form str)    -> v2 `details["decision_basis"]`; the
                                                            top-level `decision` field instead
                                                            takes v2's own ALLOW/DENY convention
                                                            (`gateway.audit.AuditRecord`'s own
                                                            docstring), computed by the caller from
                                                            the SAME allow/reject branch that used
                                                            to produce the donor's `basis` string
          donor `input_hash=hash_input({task_id, ...})`   -> v2 derives `input_hash` AUTOMATICALLY
                                                            from `details`
                                                            (`AuditRecord.compute_input_hash`) —
                                                            `details` carries the SAME PHI-safe
                                                            fields the donor hashed (task_id/
                                                            task_type/chain/payload_ref), plus
                                                            `target`/`decision_basis`
          donor `autonomy_level="a2a"`                    -> DROPPED (no v2 field); the `a2a.`
                                                            action prefix already marks the effect
                                                            surface
          donor `agent_version="a2a"` (a fixed marker,     -> PRESERVED verbatim: neither v2's
            never derived from anything real)                `AgentDefinition` nor the envelope
                                                              carries a per-hop "delegation runtime
                                                              version"; a signed Card's `version`
                                                              IS available via a registry lookup,
                                                              but binding it here would add a
                                                              second registry read to the audit hot
                                                              path for a field with no consumer yet
                                                              (deferred, not a T-F blocker)
          (no donor equivalent)                           -> v2 `dmn_versions={}` / `model_id=None`
                                                            / `prompt_version=None` (honest: no
                                                            DMN/LLM provenance at the dispatch
                                                            decision itself, mirrors
                                                            `AgentDecisionProvenance`'s
                                                            "else None — honest" convention)

        PHI safety: `details` deliberately EXCLUDES `envelope.payload_meta` — the one envelope
        field NOT covered by the `_looks_like_phi` guard on `payload_ref` — mirroring `facts.
        build_fact`'s own exclusion of `meta`. Only bounded, structural fields are persisted
        (task_id/task_type/chain/target/payload_ref/decision_basis); `payload_ref` itself is
        already a FHIR/pseudonymized reference, never raw PHI (ADR-0006, enforced at envelope
        construction, `delegation.py`'s `_looks_like_phi`).

        `dedup_key` (design §3.2/§7.2 decision #4): `a2a_audit_dedup_key(tenant, task_id)` ==
        `f"{tenant}:a2a:delegate:{task_id}"`, mirroring `tools.mcp_cibseven.transport.
        start_dedup_key`. This is belt-and-suspenders alongside Guard 4 (`delegate`'s own task_id
        idempotency): the in-memory `_inflight` path does NOT survive a replica restart (class
        docstring), so `emit_once`'s OWN durable dedup is what prevents a forked audit chain link
        if `_execute` is re-entered for the same `task_id` from a fresh process.
        """
        details: dict[str, Any] = {
            "task_id": envelope.task_id,
            "task_type": envelope.task_type,
            "chain": list(envelope.delegation_chain),
            "payload_ref": envelope.payload_ref,
            "target": envelope.target,
            "decision_basis": basis,
        }
        record = AuditRecord(
            agent_id=envelope.origin,
            tenant_id=envelope.tenant,
            agent_version="a2a",
            action=f"a2a.delegate:{envelope.target}",
            decision=decision,
            details=details,
        )
        await self._audit.emit_once(record, dedup_key=a2a_audit_dedup_key(envelope.tenant, envelope.task_id))

    async def _audit_delegation_outcome(
        self, envelope: DelegationEnvelope, *, basis: str, detail: str
    ) -> None:
        """TERMINAL delegation-outcome audit (T-F follow-up, W4 — closes the W2 completeness gap).

        Today, only the pre-execution ALLOW audit (`_audit_delegation`) fires before the handler
        runs. If the handler then raises `DelegationError` (e.g. a cyclic sub-delegation attempted
        internally), the dispatcher emitted a `rejected` Kafka FACT but no second AUDIT row — an
        ALLOWed-then-internally-failed delegation left no durable trace distinguishing it from one
        still silently in flight. This method emits that SECOND, TERMINAL row, called ONLY from
        the handler-error branch of `_execute` (the pre-exec ALLOW row already IS the terminal row
        for every other outcome: a validation rejection never reaches here, and a successful
        handler already returns a synchronous `output_ref` to the caller plus a `completed` fact —
        this closes specifically the one gap named "TERMINAL-REJECTION audit" in the charter).

        Distinct from `_audit_delegation` in three ways, all deliberate:
          - `action` gets a `:outcome` suffix (`f"a2a.delegate:{target}:outcome"`) so a chain
            reader can tell the pre-exec admission row apart from the terminal-outcome row at a
            glance, without inspecting `decision`.
          - `decision=_DECISION_FAILED` — NOT `_DECISION_DENY` — so this is never mistaken for a
            second ADMISSION decision (the delegation WAS allowed; it terminally failed after).
          - `dedup_key` is `a2a_audit_outcome_dedup_key(tenant, task_id)` (the `:outcome`-suffixed
            key), never the same key as the pre-exec row's `a2a_audit_dedup_key` — so `emit_once`
            treats them as two independent, individually-deduplicated chain links, and a re-entry
            of `_execute` for the same `task_id` (from a fresh process, mirroring the pre-exec
            row's own re-entry-safety rationale) can never fork either one.

        PHI safety: identical discipline to `_audit_delegation` — `details` excludes
        `envelope.payload_meta` entirely; `detail` (the exception message) is itself PHI-free by
        construction (`CyclicDelegationError`/`MaxHopsExceededError`/`BudgetExhaustedError` only
        ever name agent_ids, chain tuples, and counts — never envelope payload data).
        """
        details: dict[str, Any] = {
            "task_id": envelope.task_id,
            "task_type": envelope.task_type,
            "chain": list(envelope.delegation_chain),
            "payload_ref": envelope.payload_ref,
            "target": envelope.target,
            "decision_basis": basis,
            "detail": detail,
        }
        record = AuditRecord(
            agent_id=envelope.origin,
            tenant_id=envelope.tenant,
            agent_version="a2a",
            action=f"a2a.delegate:{envelope.target}:outcome",
            decision=_DECISION_FAILED,
            details=details,
        )
        await self._audit.emit_once(
            record, dedup_key=a2a_audit_outcome_dedup_key(envelope.tenant, envelope.task_id)
        )

    async def _emit(
        self,
        envelope: DelegationEnvelope,
        kind: DelegationFactKind,
        *,
        reason: str | None = None,
        output_ref: str | None = None,
    ) -> None:
        fact = build_fact(
            kind,
            task_id=envelope.task_id,
            task_type=envelope.task_type,
            tenant=envelope.tenant,
            origin=envelope.origin,
            target=envelope.target,
            delegation_chain=envelope.delegation_chain,
            reason=reason,
            output_ref=output_ref,
        )
        await self._facts.emit(fact)
