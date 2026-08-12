"""A2A assembly — derive Agent Cards from `agent.yaml`, the signing-key seam, and `build_dispatcher`.

Ported from the donor `Maezo-Healthcare-Plan` reference implementation (`src/maezo/a2a/
assembly.py:1-192`, minus `RouterInferenceProvider`) across the T2.4 A2A W1 (card-signing,
`build_agent_cards`/`card_signer_from_key`) and W2 (delegation runtime, `build_dispatcher`) build
waves — see `docs/design/A2A-dispatcher-card-signing.md` §1.2/§2/§5.

`RouterInferenceProvider` deliberately does NOT port here (design §2 friction row 5 / §5 W2 scope
note): it adapts `runtime.inference.InferenceRouter`/`TaskKind`/`SecurityZone` to the agent-graph
`InferenceProvider` contract, none of which exist in v2's `runtime.inference` (a materially
different `InferenceProvider`/`BaseInferenceProvider` abstraction, `inference.py:129-390`) — wiring
a REAL agent handler through a real inference provider is W3 scope (Helena->Rafael proof edge), not
W2 (envelope/dispatcher/facts/idempotency runtime, no real agent edge yet).

This module is engine-free for `build_agent_cards` (only reads real `agent.yaml` files via
`maezo.agents.AgentLoader`, no Kafka/DB/BPMN) and Kafka/DB-free for `build_dispatcher` itself (it
only wires injected collaborators — `cards`/`handlers`/`audit`/`facts`/`idempotency`/`verifier` are
all passed in; no fake handler resolution or engine bring-up happens here).

Key-management seam (ADR-0003/0007): the REAL Card-signing key lives in a vault/KMS and its
provisioning is an EXTERNAL dependency (blocked secret — see
`docs/design/A2A-dispatcher-card-signing.md` §6.2). `card_signing_key_from_env` is the v2-side half
of that seam: it resolves the key from the process environment (where a vault/KMS sidecar or
deployment secret-injector would place it), mirroring the precedent in
`maezo.runtime.inference.AnthropicInferenceProvider._resolve_api_key` (repo-namespaced env var,
never hard-coded, never logged). `card_signer_from_key` then gates construction of a real
`CardSigner` purely on the presence of that key, exactly like the donor.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from maezo.a2a.card import AgentCard, FederationLayer
from maezo.a2a.dispatcher import AgentHandler, AuditEmitter, DelegationDispatcher, FactProducer
from maezo.a2a.idempotency import IdempotencyStore
from maezo.a2a.registry import A2ARegistry, RegistryError
from maezo.a2a.signing import CardSigner
from maezo.agents import AgentLoader

if TYPE_CHECKING:
    from maezo.a2a.envelope_signing import EnvelopeSigner, EnvelopeVerifier

#: Repo-namespaced env var carrying the Agent Card HMAC signing key (vault/KMS injection seam).
#: Absent -> `card_signing_key_from_env()` returns `None` -> `card_signer_from_key` builds no
#: signer -> Cards are derived unsigned (dev fail-safe path). NEVER hard-code a real key here.
CARD_SIGNING_KEY_ENV_VAR = "MAEZO_A2A_CARD_SIGNING_KEY"


def card_signing_key_from_env() -> bytes | None:
    """Resolve the Card-signing key from the environment, or `None` if unset/empty.

    This is the injection seam's v2-side half: it does not itself talk to a vault/KMS — a real
    deployment's secret-injector (or a local `.env` for dev) is expected to populate
    `MAEZO_A2A_CARD_SIGNING_KEY` in the process environment before this is called. Real key
    PROVISIONING in the vault/KMS remains an external/infra dependency (BLOCKED for this build wave
    — see `docs/design/A2A-dispatcher-card-signing.md` §6.2); this function only reads whatever is
    already there. UTF-8 encoded (matches how a secret-manager-injected env var is materialized).

    The value is whitespace-STRIPPED before use: leading/trailing whitespace (most commonly a
    trailing newline from `echo secret > file` / a mounted secret file) is a TRANSPORT artifact,
    not key material — stripping here keeps the signature stable regardless of how the same secret
    was materialized. A whitespace-only value is treated as unset -> `None` (dev fail-safe, same
    as absent), so a blank-but-present env var can never construct a signer. A present-but-too-
    short value is NOT filtered here: it flows to `CardSigner`, which refuses it fail-closed
    (`CardSignatureError`, `MIN_SIGNING_KEY_BYTES`) — a present-but-garbage key must fail loudly,
    never silently downgrade production to unsigned Cards.
    """
    value = os.environ.get(CARD_SIGNING_KEY_ENV_VAR, "").strip()
    return value.encode("utf-8") if value else None


def card_signer_from_key(signing_key: bytes | None) -> CardSigner | None:
    """Build a `CardSigner` from an INJECTED key (vault/KMS), or `None` if absent.

    When the key is present, `CardSigner` REQUIRES it to be well-formed (refuses empty,
    whitespace-only, and shorter-than-`MIN_SIGNING_KEY_BYTES` keys, fail-closed with
    `CardSignatureError`) and the production path signs/verifies Cards. When absent
    (`None`/empty), returns `None`: the dev path keeps producing unsigned Cards, fail-safe,
    without loosening production. Feature-gated on key presence, mirroring
    `gateway.pseudonymizer`. Note the asymmetry is deliberate: ABSENT -> `None` (dev fail-safe),
    but PRESENT-and-garbage (whitespace/too short) -> raise — a deployment that tried to configure
    signing and got the key wrong must fail loudly, never silently run unsigned.
    """
    if not signing_key:
        return None
    return CardSigner(signing_key)


def build_agent_cards(
    tenant: str,
    agent_ids: Iterable[str],
    *,
    federation_layer: FederationLayer = "L3",
    signer: CardSigner | None = None,
) -> list[AgentCard]:
    """Derive the (tenant-scoped) Agent Cards for `agent_ids` from their real `agent.yaml` files.

    Each Card is DERIVED from the effective Agent Definition (`maezo.agents.AgentLoader`,
    `spec/agents/<agent_id>/agent.yaml`) — capabilities/skills/`accepted_task_types` come from the
    `a2a:` block. There is no parallel card file (ADR-0003). The Card's `version` is a content hash
    of the definition (`card._definition_content_version` — see `card.py`'s module docstring for
    the W1 rationale).

    When `signer` (vault/KMS key, REQUIRED) is injected, every Card comes back SIGNED at
    construction over its canonical form. Without a `signer`, Cards come back unsigned (dev
    fail-safe path). Real key population in the vault/KMS is BLOCKED (external dependency, §6.2 of
    the design doc) — see `card_signer_from_key`/`card_signing_key_from_env`.
    """
    loader = AgentLoader()
    cards: list[AgentCard] = []
    for agent_id in agent_ids:
        definition = loader.load_by_id(agent_id)
        cards.append(
            AgentCard.from_definition(
                definition,
                tenant=tenant,
                federation_layer=federation_layer,
                signer=signer,
            )
        )
    return cards


def build_dispatcher(
    *,
    tenant: str,
    cards: Iterable[AgentCard],
    handlers: Mapping[str, AgentHandler],
    audit: AuditEmitter,
    facts: FactProducer,
    idempotency: IdempotencyStore | None = None,
    verifier: CardSigner | None = None,
    envelope_verifier: EnvelopeVerifier | None = None,
    origin_envelope_signer: EnvelopeSigner | None = None,
) -> DelegationDispatcher:
    """Assemble the production `DelegationDispatcher` (mirrors the donor's `assembly.build_dispatcher`).

    Registers `cards` (tenant-scoped) into an `A2ARegistry` and wires `handlers`, `audit` (the
    `emit_once` seam, T-F — see `dispatcher.AuditEmitter`/`_audit_delegation`), `facts`, and the
    durable `idempotency` store (when present -> cross-replica Guard 4; `None` -> in-memory
    fallback).

    Rejects a card whose `tenant` does not match the assembly's tenant (the registry is
    tenant-scoped, ADR-0004).

    With `verifier` (a `CardSigner`) injected, the registry REFUSES admission of any card without a
    valid signature (ADR-0003/0007) — the dispatcher only ever `lookup`s verified cards (T-G
    signed-Card half). Without `verifier`, dev behavior is preserved (unsigned cards accepted):
    verification is gated on the presence of the key.

    `envelope_verifier`/`origin_envelope_signer` (ADR-0039 §4.4, leg E3) are the ENVELOPE-signing
    pair — a DISTINCT surface from the Card `verifier` above (§4.1). Present -> the dispatcher
    fail-closes on any unsigned/tampered envelope as its first `delegate` check, and the origin
    signer rides along so the edge's per-request builders can sign at construction. Both `None`
    (dev / opt-out) -> envelope verification is off (unsigned envelopes accepted), W2 behaviour.
    """
    registry = A2ARegistry(verifier=verifier)
    for card in cards:
        if card.tenant != tenant:
            raise RegistryError(
                f"card for tenant {card.tenant!r} does not match the assembly's tenant {tenant!r} "
                "(the registry is tenant-scoped, ADR-0004)"
            )
        registry.register(card)
    return DelegationDispatcher(
        registry=registry,
        handlers=handlers,
        audit=audit,
        facts=facts,
        idempotency=idempotency,
        envelope_verifier=envelope_verifier,
        origin_envelope_signer=origin_envelope_signer,
    )
