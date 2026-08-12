"""MAEZO A2A Collaboration — Agent Card signing + the delegation runtime (ADR-0003/0004/0007/0015).

Public surface of the A2A workstream — the rest of the codebase imports only `maezo.a2a` (ADR-0003
"SDK encapsulado").

W1 (card-signing slice — see `docs/design/A2A-dispatcher-card-signing.md` §5):
- `AgentCard`: frozen identity + capability declaration for an agent, with a `signing_payload()`
  used by `CardSigner` (HMAC-SHA256, fail-closed, constant-time verify).
- `A2ARegistry`: tenant-scoped registry of AgentCards (ADR-0004), with a `verifier=` fail-closed
  admission gate.
- `card_signer_from_key`/`card_signing_key_from_env`/`build_agent_cards`: the vault/KMS key
  injection seam + `agent.yaml`-derived card assembly.
- `TenantKeyset`/`EnvTenantKeyset`/`per_tenant_key_env_var`: the PER-TENANT signing-key resolution
  seam (ADR-0039 §4.4, owner decision 4) — the single per-tenant key-custody point both Card
  signing (today, via the composition root) and envelope signing (leg E3) share. No cross-tenant
  fallback; fail-closed migration off the repo-wide key (`maezo.a2a.keyset`'s module docstring).

W2 (delegation runtime, this module set — see design doc §5 W2):
- `DelegationEnvelope`/`Budget`: the idempotent, anti-loop delegation message (ADR-0003 guards 1-3
  enforced structurally at `root()`/`extend()`).
- `DelegationDispatcher`/`DelegationResult`/`RejectionReason`/`HandlerOutput`/`AgentHandler`: the
  single agent->agent delegation chokepoint — audits (v2 `AuditRecord` via `emit_once`, T-F),
  emits Kafka facts, and routes to an injectable handler.
- `FactProducer` + the 3 `agents.events.delegation.*` topics (`DelegationFact`,
  `register_a2a_topics`).
- `IdempotencyStore`/`PostgresIdempotencyStore`/`StoredResult`: durable, cross-replica Guard 4.
- `build_dispatcher`: production assembly (cards + handlers + audit + facts + idempotency +
  verifier -> a wired `DelegationDispatcher`).
- `AntiLoopGuard`: kept as a thin, DEPRECATED façade — the real guards now live structurally in
  `DelegationEnvelope` (see `maezo.a2a.anti_loop`'s module docstring for the
  `CyclicDelegationError` consolidation, design §1.3/§7.2 decision #3).

Deliberately NOT here yet (W3/W4 — see design doc §5): a real agent handler edge
(Helena->Rafael), `RouterInferenceProvider` (needs v2's `runtime.inference` redesigned against it),
and the T-G cert/service-account identity half (ADR-gated).
"""

from maezo.a2a.anti_loop import AntiLoopGuard, MaxDepthExceededError
from maezo.a2a.assembly import (
    CARD_SIGNING_KEY_ENV_VAR,
    build_agent_cards,
    build_dispatcher,
    card_signer_from_key,
    card_signing_key_from_env,
)
from maezo.a2a.card import AgentCard, CardSignatureError, RegistryError
from maezo.a2a.delegation import (
    MAX_HOPS,
    Budget,
    BudgetExhaustedError,
    CyclicDelegationError,
    DelegationEnvelope,
    DelegationError,
    MaxHopsExceededError,
)
from maezo.a2a.dispatcher import (
    AgentHandler,
    AuditEmitter,
    DelegationDispatcher,
    DelegationResult,
    FactProducer,
    HandlerOutput,
    RejectionReason,
)
from maezo.a2a.facts import (
    TOPIC_COMPLETED,
    TOPIC_REJECTED,
    TOPIC_REQUESTED,
    DelegationFact,
    DelegationFactKind,
    register_a2a_topics,
)
from maezo.a2a.idempotency import IdempotencyStore, PostgresIdempotencyStore, StoredResult
from maezo.a2a.keyset import EnvTenantKeyset, TenantKeyset, per_tenant_key_env_var
from maezo.a2a.outbox import (
    MalformedFactError,
    OutboxRecord,
    PostgresFactOutbox,
    PostgresOutboxFactProducer,
    build_outbox_fact_producer,
    fact_dedup_key,
    outbox_transaction,
)
from maezo.a2a.registry import A2ARegistry
from maezo.a2a.signing import CardSigner

__all__ = [
    "CARD_SIGNING_KEY_ENV_VAR",
    "MAX_HOPS",
    "TOPIC_COMPLETED",
    "TOPIC_REJECTED",
    "TOPIC_REQUESTED",
    "A2ARegistry",
    "AgentCard",
    "AgentHandler",
    "AntiLoopGuard",
    "AuditEmitter",
    "Budget",
    "BudgetExhaustedError",
    "CardSignatureError",
    "CardSigner",
    "CyclicDelegationError",
    "DelegationDispatcher",
    "DelegationEnvelope",
    "DelegationError",
    "DelegationFact",
    "DelegationFactKind",
    "DelegationResult",
    "EnvTenantKeyset",
    "FactProducer",
    "HandlerOutput",
    "IdempotencyStore",
    "MalformedFactError",
    "MaxDepthExceededError",
    "MaxHopsExceededError",
    "OutboxRecord",
    "PostgresFactOutbox",
    "PostgresIdempotencyStore",
    "PostgresOutboxFactProducer",
    "RegistryError",
    "RejectionReason",
    "StoredResult",
    "TenantKeyset",
    "build_agent_cards",
    "build_dispatcher",
    "build_outbox_fact_producer",
    "card_signer_from_key",
    "card_signing_key_from_env",
    "fact_dedup_key",
    "outbox_transaction",
    "per_tenant_key_env_var",
    "register_a2a_topics",
]
