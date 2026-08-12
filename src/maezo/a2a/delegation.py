"""DelegationEnvelope — A2A v1.0 delegation message + structural anti-loop guards (ADR-0003).

Ported from the donor `Maezo-Healthcare-Plan` reference implementation (`src/maezo/a2a/
delegation.py:1-227`) as part of the T2.4 A2A W2 (delegation runtime) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §1.2/§1.3/§5/§7.2 for the full port rationale.

A delegation is a directed agent->agent command (Helena->Rafael). Unlike a Kafka fact, it carries
lifecycle, a deadline/budget, and an accumulated `delegation_chain`. The four ADR-0003 anti-loop
guards are enforced IN CODE (structurally, never by convention):

  1. `delegation_chain` is ACYCLIC        — `extend()` rejects a target already in the chain.
  2. `max_hops=3`                          — `extend()` rejects the 4th hop.
  3. budget decrements per hop             — `extend()` rejects once the budget is exhausted.
  4. `task_id` idempotency                 — the dispatcher (see `dispatcher.py`) returns the
                                             prior result/handle; it never re-executes.

PHI: the envelope NEVER carries raw PHI (ADR-0006). `payload_ref` references FHIR/pseudonymized
data (e.g. `Patient/abc` in the tenant's FHIR store), never the raw datum itself.

**Anti-loop consolidation (design §1.3/§7.2 decision #3):** v2 previously had a standalone
`AntiLoopGuard` (`maezo.a2a.anti_loop`) validating a bare `list[str]` chain, defining its OWN
`CyclicDelegationError(ValueError)`. The donor instead folds the acyclic + max-hops + budget
guards INTO the envelope's `root()`/`extend()` — the shape this module ports. To avoid two
divergent definitions of the same concept (design §1.3 "CyclicDelegationError name collision"),
THIS module's `CyclicDelegationError` (subclassing `DelegationError(ValueError)`, matching the
donor's hierarchy) is now the ONE canonical definition; `maezo.a2a.anti_loop` re-exports it rather
than redefining it (see that module's docstring). `AntiLoopGuard` itself is kept as a thin,
deprecated façade over a bare chain (backward-compatible, zero production call sites per
ADR-0032) — the acyclic + `max_depth=3` invariant it enforces is now ALSO, and primarily, enforced
structurally here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

# Delegation-chain depth limit (ADR-0003). The originator counts as hop 1; a 4th agent in the
# chain is rejected.
MAX_HOPS: int = 3


class DelegationError(ValueError):
    """Structural violation of an anti-loop guard (chain/hops/budget)."""


class CyclicDelegationError(DelegationError):
    """Guard 1 — the target is already in the `delegation_chain` (a cycle)."""


class MaxHopsExceededError(DelegationError):
    """Guard 2 — extending the `delegation_chain` would exceed `max_hops`."""


class BudgetExhaustedError(DelegationError):
    """Guard 3 — insufficient budget (tokens/time) for another hop."""


@dataclass(frozen=True, slots=True)
class Budget:
    """Budget for a delegation chain. Decrements per hop; rejects once exhausted.

    `tokens` and `time_ms` are cumulative limits remaining for the ENTIRE chain. `cost_per_hop`
    is the fixed cost charged on every `charge()`. Immutable: `charge()` returns a new `Budget`.
    """

    tokens: int
    time_ms: int
    cost_per_hop: int = 1

    def __post_init__(self) -> None:
        if self.tokens < 0 or self.time_ms < 0:
            raise DelegationError("Budget cannot be negative")
        if self.cost_per_hop <= 0:
            raise DelegationError("cost_per_hop must be positive")

    @property
    def exhausted(self) -> bool:
        """True if there is no budget left for another hop."""
        return self.tokens < self.cost_per_hop or self.time_ms < self.cost_per_hop

    def charge(self) -> Budget:
        """Charge one hop. Raises `BudgetExhaustedError` if there is no budget left."""
        if self.exhausted:
            raise BudgetExhaustedError(
                f"budget exhausted: tokens={self.tokens} time_ms={self.time_ms} "
                f"cost_per_hop={self.cost_per_hop}"
            )
        return replace(
            self,
            tokens=self.tokens - self.cost_per_hop,
            time_ms=self.time_ms - self.cost_per_hop,
        )


@dataclass(frozen=True, slots=True)
class EnvelopeSignature:
    """The signature metadata bound INTO the canonical digest (§4.2) + the MAC (ADR-0039 §4.1).

    Carried as the optional `DelegationEnvelope.signature`. All three §4.2 metadata fields
    (`scheme`/`key_id`/`replay_epoch`) PLUS the signing timestamp the verifier-side max-signature-age
    bound needs (`signed_at`, §4.3.2) are bound into the signed digest, so none can be swapped
    post-signing without invalidating `mac` (the JSON-signing analogue of JWT's "alg:none" /
    key-confusion class — see `maezo.a2a.envelope_signing`). An UNSIGNED (dev) envelope carries
    `signature=None`; a VERIFIED envelope requires this present-and-valid, fail-closed at the
    dispatcher (ADR-0039 §4.4). Frozen/slotted like the envelope itself so it cannot be mutated
    after signing.
    """

    scheme: str
    key_id: str
    replay_epoch: int
    signed_at: datetime
    mac: str


@dataclass(frozen=True, slots=True)
class DelegationEnvelope:
    """Typed A2A delegation message (idempotent, anti-loop, never raw PHI).

    - `task_id`: idempotent — re-delivery of the same `task_id` NEVER re-executes (see dispatcher).
    - `delegation_chain`: the agents already in the chain, from the originator to the current
      target (inclusive).
    - `origin`: the agent that started the chain (`delegation_chain[0]`).
    - `payload_ref`: a reference to FHIR/pseudonymized data (ADR-0006) — NEVER raw PHI.
    - `deadline`/`budget`: lifecycle limits; the budget decrements on every hop.

    Build the root envelope with `root()` and extend it with `extend()` (which applies the 3
    structural guards). Fields are immutable; `extend()` returns a new envelope.
    """

    task_id: str
    task_type: str
    origin: str
    target: str
    tenant: str
    delegation_chain: tuple[str, ...]
    budget: Budget
    payload_ref: str
    max_hops: int = MAX_HOPS
    deadline: datetime | None = None
    payload_meta: Mapping[str, str] = field(default_factory=dict)
    # Envelope signature (ADR-0039). `None` = the envelope is NOT signed (dev path / a freshly
    # built root before the signer runs / a sub-envelope from `extend()`). A signed envelope
    # carries the MAC over its canonical digest + the §4.2 metadata bound into that digest. The
    # signature covers the digest, which EXCLUDES this field itself, so signing is well-defined.
    signature: EnvelopeSignature | None = None

    def __post_init__(self) -> None:
        if not self.task_id:
            raise DelegationError("task_id is required (idempotency, ADR-0003)")
        if not self.tenant:
            raise DelegationError("tenant is required (delegation is tenant-scoped, ADR-0004)")
        if self.max_hops < 1:
            raise DelegationError("max_hops must be >= 1")
        if self.deadline is not None and self.deadline.tzinfo is None:
            raise DelegationError("deadline must be timezone-aware (UTC)")
        if _looks_like_phi(self.payload_ref):
            raise DelegationError(
                "payload_ref looks like raw PHI — use a FHIR/pseudonymized reference (ADR-0006)"
            )

    @property
    def hops(self) -> int:
        """Number of agents in the chain (current depth)."""
        return len(self.delegation_chain)

    def expired(self, *, now: datetime | None = None) -> bool:
        """True if the deadline has already passed."""
        if self.deadline is None:
            return False
        return (now or datetime.now(tz=UTC)) >= self.deadline

    def signed_copy(self, signature: EnvelopeSignature) -> DelegationEnvelope:
        """Return an immutable copy of the envelope with `signature` set (everything else unchanged).

        Mirrors `AgentCard.signed_copy` (`card.py`). The signer computes the MAC over the canonical
        digest (which does NOT read this field) and calls this to attach it — so signing is a pure
        function of the envelope's content, never of a prior signature. See
        `maezo.a2a.envelope_signing.EnvelopeSigner`.
        """
        return replace(self, signature=signature)

    @classmethod
    def root(
        cls,
        *,
        task_id: str,
        task_type: str,
        origin: str,
        target: str,
        tenant: str,
        budget: Budget,
        payload_ref: str,
        max_hops: int = MAX_HOPS,
        deadline: datetime | None = None,
        payload_meta: Mapping[str, str] | None = None,
    ) -> DelegationEnvelope:
        """Root envelope (1st hop: origin->target). The chain starts as [origin, target].

        Applies the anti-loop guards to the root itself: rejects origin==target (a trivial
        cycle), a chain that already exceeds max_hops, and charges 1 hop from the budget.
        """
        if origin == target:
            raise CyclicDelegationError(f"origin == target ({origin!r}) — trivial cycle")
        chain = (origin, target)
        if len(chain) > max_hops:
            raise MaxHopsExceededError(f"root chain {chain} exceeds max_hops={max_hops}")
        return cls(
            task_id=task_id,
            task_type=task_type,
            origin=origin,
            target=target,
            tenant=tenant,
            delegation_chain=chain,
            budget=budget.charge(),
            payload_ref=payload_ref,
            max_hops=max_hops,
            deadline=deadline,
            payload_meta=dict(payload_meta or {}),
        )

    def extend(
        self,
        *,
        target: str,
        task_id: str,
        task_type: str | None = None,
        payload_ref: str | None = None,
        payload_meta: Mapping[str, str] | None = None,
    ) -> DelegationEnvelope:
        """Extend the chain to a new `target` (sub-delegation). Applies the 3 structural guards:

        - Guard 1 (acyclic): `target` already in `delegation_chain` -> `CyclicDelegationError`.
        - Guard 2 (max_hops): the resulting chain > `max_hops` -> `MaxHopsExceededError`.
        - Guard 3 (budget):   exhausted budget -> `BudgetExhaustedError`.

        Preserves `origin`, `tenant`, `max_hops`, and `deadline`. The sub-envelope's `task_id` is
        new (every hop is its own idempotent task); idempotency is keyed by `task_id`.

        SIGNATURE IS DROPPED, NOT INHERITED (ADR-0039 §4.4 latent-trap fix). `replace` carries every
        un-overridden field verbatim — so without the explicit `signature=None` below, a MAC computed
        over THIS envelope's digest would ride onto a sub-envelope whose `task_id`/`target`/
        `delegation_chain`/`budget`/`payload_*` have all changed, i.e. a signature over a DIFFERENT
        digest. That could never verify (a confusing rejection), and is exactly the silent
        propagation that becomes a forgery the moment someone "fixes" the rejection by loosening the
        check. The sub-envelope is therefore returned UNSIGNED and must be re-signed by its
        originator (`EnvelopeSigner.sign`) before it crosses a verifying dispatcher.
        """
        if target in self.delegation_chain:
            raise CyclicDelegationError(
                f"target {target!r} is already in the chain {self.delegation_chain} — cyclic delegation"
            )
        new_chain = (*self.delegation_chain, target)
        if len(new_chain) > self.max_hops:
            raise MaxHopsExceededError(
                f"chain {new_chain} ({len(new_chain)} hops) exceeds max_hops={self.max_hops}"
            )
        charged = self.budget.charge()  # Guard 3 — raises before the envelope is constructed.
        return replace(
            self,
            task_id=task_id,
            task_type=task_type or self.task_type,
            target=target,
            delegation_chain=new_chain,
            budget=charged,
            payload_ref=payload_ref or self.payload_ref,
            payload_meta=dict(payload_meta) if payload_meta is not None else self.payload_meta,
            signature=None,  # ADR-0039 §4.4: NEVER inherit a signature onto a changed digest.
        )


# --- PHI guard ----------------------------------------------------------------------------


# Defense-in-depth (`docs/design/A2A-dispatcher-card-signing.md` §10, structural-field PHI-scrub
# assessment): a CPF/CNPJ embedded INSIDE an otherwise-legitimate URI-style `payload_ref` (e.g. a
# caller accidentally interpolating a raw CPF into a FHIR/process reference) bypasses the
# whole-string check below, which requires the ENTIRE string to be digits+separators. Two embedded
# shapes are caught:
#   1. CANONICALLY-PUNCTUATED CPF/CNPJ (dots/dash for CPF, dots/slash/dash for CNPJ, in the EXACT
#      canonical positions) — essentially never occurs by chance in a URI/reference scheme.
#   2. A BARE digit run whose length is EXACTLY a Brazilian identifier's (CPF=11, CNPJ=14, CNS=15),
#      isolated by non-digit boundaries (`_BARE_ID_RUN_RE`). This closes the gap the auditor named
#      (a raw 11-digit CPF interpolated into a URI-style ref, e.g. `fhir://Patient/12345678901`)
#      WITHOUT false-positive-rejecting legitimate long-but-not-PHI numeric ids: the length is
#      pinned to the exact PHI-identifier lengths (via the `(?<!\d)…(?!\d)` boundaries a LONGER run
#      never matches), so an all-digit UUID node (12 hex digits, e.g. `…-426614174000`) or any
#      other 12/13/16+-digit id stays admissible. A bare ELEVEN-digit id is genuinely
#      indistinguishable from a CPF, so flagging it is the fail-closed-correct call (ADR-0006).
_CPF_FORMATTED_RE = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
_CNPJ_FORMATTED_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
_BARE_ID_RUN_RE = re.compile(r"(?<!\d)(?:\d{15}|\d{14}|\d{11})(?!\d)")


# Defensive heuristic: rejects refs that "look like" raw Brazilian identifiers (11-digit CPF/CNS,
# 14-digit CNPJ) OR that embed a canonically-punctuated CPF/CNPJ substring OR an isolated bare
# CPF/CNPJ/CNS-length digit run. This is NOT PHI validation — it is a cheap fail-closed check
# against the obvious mistake of pasting a CPF as (or into) `payload_ref`. Real data travels as a
# FHIR reference.
def _looks_like_phi(payload_ref: str) -> bool:
    digits = [ch for ch in payload_ref if ch.isdigit()]
    only_digits_and_sep = all(ch.isdigit() or ch in {".", "-", "/", " "} for ch in payload_ref)
    if only_digits_and_sep and len(digits) in {11, 14, 15}:
        return True
    if _BARE_ID_RUN_RE.search(payload_ref):
        return True
    return bool(_CPF_FORMATTED_RE.search(payload_ref) or _CNPJ_FORMATTED_RE.search(payload_ref))


def origin_of(chain: Sequence[str]) -> str:
    """The originator of a delegation chain (the first agent)."""
    if not chain:
        raise DelegationError("empty delegation_chain has no origin")
    return chain[0]
