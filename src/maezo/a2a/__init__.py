"""MAEZO A2A Collaboration — Agent Card signing, Registry, Anti-Loop (ADR-0003, ADR-0007, ADR-0015).

Public surface of the A2A workstream — the rest of the codebase imports only `maezo.a2a` (ADR-0003
"SDK encapsulado").

W1 (card-signing slice, this module set — see `docs/design/A2A-dispatcher-card-signing.md` §5):
- `AgentCard`: frozen identity + capability declaration for an agent, with a `signing_payload()`
  used by `CardSigner` (HMAC-SHA256, fail-closed, constant-time verify).
- `A2ARegistry`: tenant-scoped registry of AgentCards (ADR-0004), with a `verifier=` fail-closed
  admission gate.
- `card_signer_from_key`/`card_signing_key_from_env`/`build_agent_cards`: the vault/KMS key
  injection seam + `agent.yaml`-derived card assembly.
- `AntiLoopGuard`: structural loop prevention (max depth, cycle detection) — standalone today;
  W2 folds equivalent guards into the delegation envelope (see design doc §1.3).

Deliberately NOT here yet (W2/W3/W4 — see design doc §5): `DelegationEnvelope`/`Budget`,
`DelegationDispatcher`, `FactProducer`/Kafka topics, `IdempotencyStore`.
"""

from maezo.a2a.anti_loop import AntiLoopGuard, CyclicDelegationError, MaxDepthExceededError
from maezo.a2a.assembly import (
    CARD_SIGNING_KEY_ENV_VAR,
    build_agent_cards,
    card_signer_from_key,
    card_signing_key_from_env,
)
from maezo.a2a.card import AgentCard, CardSignatureError, RegistryError
from maezo.a2a.registry import A2ARegistry
from maezo.a2a.signing import CardSigner

__all__ = [
    "CARD_SIGNING_KEY_ENV_VAR",
    "A2ARegistry",
    "AgentCard",
    "AntiLoopGuard",
    "CardSignatureError",
    "CardSigner",
    "CyclicDelegationError",
    "MaxDepthExceededError",
    "RegistryError",
    "build_agent_cards",
    "card_signer_from_key",
    "card_signing_key_from_env",
]
