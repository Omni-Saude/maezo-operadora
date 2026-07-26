"""AgentCard — A2A identity and capability card (ADR-0003, ADR-0007, ADR-0015).

Ported from the donor `Maezo-Healthcare-Plan` reference implementation
(`src/maezo/a2a/registry.py:44-181`) as part of the T2.4 A2A W1 (card-signing) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §1.2/§5 for the full port rationale.

An ``AgentCard`` is a FROZEN, typed declaration of an agent's identity + announced capabilities,
scoped to a tenant. `accepted_task_types` is the delegation contract: the (future, W2) dispatcher
rejects any delegation whose `task_type` is not declared here. `signing_payload()` produces the
canonical, deterministic, signature-EXCLUDED bytes that `CardSigner` (`maezo.a2a.signing`) signs —
see that module for the HMAC-SHA256 scheme.

Per ADR-0015, the Card is DERIVED from an `AgentDefinition` (never configured standalone) —
`from_definition()` is that derivation path, sourcing `capabilities`/`skills`/`accepted_task_types`
from the agent's optional `a2a:` block in `agent.yaml` (`maezo.agents.AgentDefinition.a2a`).

**Breaking change from the prior (Phase-0) shape** (documented in the design doc §5 W1
"Breaking-change note"): the old `AgentCard` was a plain mutable class with exactly
`agent_id, capabilities: list[str], endpoint, public_key: str` and `to_dict`/`from_dict`. This
version is a frozen dataclass with `capabilities`/`skills`/`accepted_task_types` as `frozenset[str]`
(for deterministic, order-independent canonical serialization) and replaces the unused
`public_key: str` field with a real `signature: str | None` produced by `CardSigner`. There are
ZERO production call sites for the old shape (ADR-0032), so the blast radius is this module plus
`tests/unit/a2a/test_card.py` (updated in the same change). `agent_id`, `capabilities`, and
`endpoint` are preserved by name and meaning; `public_key` is intentionally dropped (it was
stored-but-unused dead weight — no sign/verify ever consumed it) in favor of the `signature` field,
which is the actual cryptographic guarantee this card now carries.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maezo.a2a.signing import CardSigner
    from maezo.agents import AgentDefinition

# Federation layers of the Agent Definition (ADR-0004): L0 core -> L3 tenant override.
FederationLayer = str
_VALID_LAYERS: frozenset[str] = frozenset({"L0", "L1", "L2", "L3"})

# Card-signature envelope scheme (ADR-0003/0007). `v1` = HMAC-SHA256 over the canonical bytes of
# the signable payload (see `AgentCard.signing_payload`). Shared with `maezo.a2a.signing.CardSigner`
# — bump this if the algorithm/shape changes; `CardSigner.verify` rejects an unknown scheme
# (fail-closed).
SIGNATURE_SCHEME = "v1"


class RegistryError(LookupError):
    """An Agent Card is missing, or a registration/construction is invalid."""


class CardSignatureError(RegistryError):
    """An Agent Card signature is missing, invalid, or was produced by the wrong key (ADR-0003/0007)."""


@dataclass(frozen=True, slots=True)
class AgentCard:
    """Agent Card A2A v1.0 (typed, immutable).

    Identity + announced capabilities of one agent, scoped to one tenant. `accepted_task_types` is
    the delegation contract. `endpoint`/`queue_ref` reference where the agent receives tasks
    (HTTP/JSON-RPC + mTLS in production; a logical queue in Phase 1).
    """

    agent_id: str
    version: str  # Effective Agent Definition version (content hash — see `from_definition`).
    tenant: str
    security_zone: str  # "general" | "phi" (ADR-0006)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    skills: frozenset[str] = field(default_factory=frozenset)
    accepted_task_types: frozenset[str] = field(default_factory=frozenset)
    federation_layer: FederationLayer = "L3"
    endpoint: str | None = None
    queue_ref: str | None = None
    # Cryptographic signature of the Card (ADR-0003/0007). `None` = card is NOT signed (dev
    # fail-safe path, when no signing key was injected). In production, `CardSigner` (vault/KMS
    # key, REQUIRED) fills this field; the registry's `verifier=` gate refuses to admit a card
    # without a valid signature. The signature covers the canonical `signing_payload()` bytes,
    # which EXCLUDE this field itself — so signing is idempotent and `signed_copy` only ever
    # changes this one field.
    signature: str | None = None

    def __post_init__(self) -> None:
        if not self.agent_id:
            raise RegistryError("AgentCard requires agent_id")
        if not self.tenant:
            raise RegistryError("AgentCard requires tenant (registry is tenant-scoped, ADR-0004)")
        if self.federation_layer not in _VALID_LAYERS:
            raise RegistryError(f"invalid federation_layer: {self.federation_layer!r} (use L0-L3, ADR-0004)")

    @property
    def is_signed(self) -> bool:
        """True if the Card carries a signature (not yet verified — use `CardSigner.verify`)."""
        return self.signature is not None

    def signing_payload(self) -> bytes:
        """Canonical bytes the signature is computed over (stable form, ADR-0007).

        Mirrors the audit-chain canonicalization (`json.dumps` with `sort_keys` + compact
        separators): reordering the underlying sets/dict keys produces the SAME bytes, so the
        signature depends ONLY on the Card's content, never on iteration order. Includes `version`
        (== the effective Agent Definition's content hash) — signing this payload signs the
        definition's identity too. The `signature` field itself is EXCLUDED (signing the Card
        cannot depend on the signature it is producing).
        """
        payload = {
            "agent_id": self.agent_id,
            "version": self.version,
            "tenant": self.tenant,
            "security_zone": self.security_zone,
            "capabilities": sorted(self.capabilities),
            "skills": sorted(self.skills),
            "accepted_task_types": sorted(self.accepted_task_types),
            "federation_layer": self.federation_layer,
            "endpoint": self.endpoint,
            "queue_ref": self.queue_ref,
            "scheme": SIGNATURE_SCHEME,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def signed_copy(self, signature: str) -> AgentCard:
        """Return an immutable copy of the Card with `signature` set (everything else unchanged)."""
        return replace(self, signature=signature)

    def accepts(self, task_type: str) -> bool:
        """True if the agent announces it accepts this task type.

        A Card with no `accepted_task_types` declared accepts ANY task (compat: Phase-0 agents
        without an `a2a:` block). Declaring the list makes the contract restrictive.
        """
        if not self.accepted_task_types:
            return True
        return task_type in self.accepted_task_types

    @classmethod
    def from_definition(
        cls,
        definition: AgentDefinition,
        *,
        tenant: str,
        federation_layer: FederationLayer = "L3",
        endpoint: str | None = None,
        queue_ref: str | None = None,
        signer: CardSigner | None = None,
    ) -> AgentCard:
        """Derive the Card from an Agent Definition (source of truth, `maezo.agents.AgentLoader`).

        `agent_id`/`security_zone` come from the `AgentDefinition`; `version` is a deterministic
        content hash of the definition (see `_definition_content_version` — v2's `AgentDefinition`
        has no pre-existing `agent_version`/`.raw` hash field like the donor's, so this is a small,
        load-bearing decision made here in W1; see `docs/design/A2A-dispatcher-card-signing.md`
        §4/§7.2). `capabilities`/`skills`/`accepted_task_types` come from the optional `a2a:`
        section of `agent.yaml` (already parsed into `definition.a2a` by `AgentLoader`):

            a2a:
              capabilities: [analyze_authorization_request]
              skills: [tiss_analysis]
              accepted_task_types: [authorization.analyze]
              endpoint: "https://rafael.amh.internal/a2a"   # optional
              queue_ref: "agents.tasks.rafael"               # optional

        When a `signer` (vault/KMS key, REQUIRED) is injected, the Card is signed over its
        canonical bytes (`signing_payload`) at construction time — exactly where `version` is
        fixed. Without a `signer`, the Card comes back unsigned (dev fail-safe path); the (future)
        dispatcher verifies before trusting (verification gated on key presence).
        """
        a2a_map: Mapping[str, object] = definition.a2a if isinstance(definition.a2a, Mapping) else {}
        card = cls(
            agent_id=definition.id,
            version=_definition_content_version(definition),
            tenant=tenant,
            security_zone=definition.security_zone,
            capabilities=_strset(a2a_map.get("capabilities")),
            skills=_strset(a2a_map.get("skills")),
            accepted_task_types=_strset(a2a_map.get("accepted_task_types")),
            federation_layer=federation_layer,
            endpoint=_opt_str(a2a_map.get("endpoint")) or endpoint,
            queue_ref=_opt_str(a2a_map.get("queue_ref")) or queue_ref,
        )
        return signer.sign(card) if signer is not None else card


def _definition_content_version(definition: AgentDefinition) -> str:
    """Deterministic, content-addressed `version` for a Card derived from an `AgentDefinition`.

    v2's `AgentDefinition` (unlike the donor's) carries no `agent_version`/`.raw` hash field — the
    harness only sets an unrelated `f"{agent_id}@v0"` config default at graph-build time
    (`runtime/harness.py:170`), which is not tied to the definition's actual content. Since
    `version` is part of what gets SIGNED (`AgentCard.signing_payload`), it must change whenever
    the agent's contract (capabilities, security_zone, tools, ...) changes — a stable literal like
    `"v0"` would let a materially different agent.yaml sign under an unchanged, stale version.
    This hashes the definition's own canonical JSON dump (`model_dump(mode="json")`, sorted keys,
    compact separators — same recipe as `signing_payload`) and truncates to 16 hex chars. This is
    the "small design decision inside W1" flagged in
    `docs/design/A2A-dispatcher-card-signing.md` §4/§7.2 ("Card `version` semantics"); a future ADR
    on ADR-0007's "sob-qual-versao" question may replace this convention without changing the
    Card's public contract (`version: str`).
    """
    dump = definition.model_dump(mode="json")
    canonical = json.dumps(dump, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _strset(value: object) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, Iterable):
        return frozenset(str(item) for item in value)
    raise RegistryError(f"expected a list of strings, got {type(value)!r}")


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)
