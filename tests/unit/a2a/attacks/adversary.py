"""Modeled defenses + their NEUTERS for the Half-A adversarial suite (leg 4).

Every attack in Half A pins an observable HARM (a double effect, a cross-tenant serve, a duplicate
reaching downstream) and pairs the real defense with a NEUTERED twin that makes the harm manifest.
The neuters are the non-vacuity controls the gatekeeper enforces: an attack that "passes" against
an undefended system proves nothing. All the modeled doubles live here so each `test_*` module
reads as attack + expected-harm, not plumbing.

Two idempotency LAYERS are modeled distinctly because the honest crash-before-complete picture
needs both (leg-2 `outbox.py` "Failure posture" + `dispatcher._delegate_durable`):

  Layer 1 — the durable store (`claim_or_get` + `complete`): controls whether the HANDLER is
            re-executed. Sealed 'done' -> replay (no re-exec). Observable: `handler.calls`.
  Layer 2 — the engine business-key (`start_process_idempotent`): controls whether a handler CALL
            produces an EFFECT. Fires at most once per business key even when the handler DOES
            re-execute. Observable: `EffectSink.count`.

In the crash-before-complete window the row is 'processing' (complete never ran), so Layer 1
CANNOT prevent re-execution (`claim_or_get` returns `None` after its best-effort poll times out) —
Layer 2 is the load-bearing defense there. Each layer has its own neuter, so each attack names the
exact defense it targets.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable

from maezo.a2a import DelegationEnvelope, DelegationResult, HandlerOutput, StoredResult

# ---------------------------------------------------------------------------
# The observable harm — a downstream effect (a process start / a dossier)
# ---------------------------------------------------------------------------


class EffectSink:
    """The observable downstream effect surface. The HARM is a SECOND entry for the same logical
    delegation — a double process start / a duplicated dossier, exactly the consequence
    `_require_idempotency_store_or_fail_closed` names ("double process starts, duplicated dossiers").
    """

    def __init__(self) -> None:
        self.effects: list[Hashable] = []

    @property
    def count(self) -> int:
        return len(self.effects)

    def distinct(self) -> int:
        return len(set(self.effects))


# ---------------------------------------------------------------------------
# Layer 2 — the engine's business-key idempotency (start_process_idempotent)
# ---------------------------------------------------------------------------


class BusinessKeyEngine:
    """Models the CIB Seven engine's business-key idempotency (`start_process_idempotent`): the
    effect fires AT MOST ONCE per business key, no matter how many times a handler re-executes.
    This is the defense that makes a crash-before-complete re-execution SAFE."""

    def __init__(self, sink: EffectSink) -> None:
        self._sink = sink
        self._seen: set[Hashable] = set()

    def start_process_idempotent(self, business_key: Hashable) -> str:
        if business_key in self._seen:
            return "replayed"  # the engine already has this business key — no new effect
        self._seen.add(business_key)
        self._sink.effects.append(business_key)
        return "started"


class NonIdempotentEngine:
    """RED control — the engine business-key dedup NEUTERED. Every `start_process_idempotent`
    fires the effect. A re-executed handler therefore DOUBLE-STARTS the process: the observable
    harm the crash-before-complete window would cause without Layer 2."""

    def __init__(self, sink: EffectSink) -> None:
        self._sink = sink

    def start_process_idempotent(self, business_key: Hashable) -> str:
        self._sink.effects.append(business_key)
        return "started"


class BusinessKeyHandler:
    """An `AgentHandler` whose downstream effect is an engine process start keyed by a STABLE
    business key (a pure function of `tenant`+`task_id`, so a re-executed handler presents the
    SAME key and the engine can dedup it). Counts its own calls so the seal-vs-effect distinction
    is observable per layer."""

    def __init__(
        self,
        engine: BusinessKeyEngine | NonIdempotentEngine,
        *,
        output_ref: str = "process://dossier-out",
    ) -> None:
        self._engine = engine
        self._output_ref = output_ref
        self.calls = 0

    async def __call__(self, envelope: DelegationEnvelope) -> HandlerOutput:
        self.calls += 1
        self._engine.start_process_idempotent(f"bk:{envelope.tenant}:{envelope.task_id}")
        return HandlerOutput(output_ref=self._output_ref)


# ---------------------------------------------------------------------------
# Layer 1 — the durable idempotency store (claim_or_get + complete)
# ---------------------------------------------------------------------------


def real_pk(tenant: str, task_id: str) -> tuple[str, str]:
    """The REAL `a2a_idempotency` primary key — `(task_id, tenant)` (idempotency.py:38). Two
    tenants presenting the same `task_id` land on DISTINCT rows."""
    return (task_id, tenant)


def tenant_blind_pk(tenant: str, task_id: str) -> str:
    """RED control — the tenant component NEUTERED out of the key (the donor's single-column
    `PRIMARY KEY (task_id)` idempotency.py:38 warns against). Two tenants presenting the same
    `task_id` now COLLIDE on one row — the cross-tenant serve/leak."""
    _ = tenant
    return task_id


class ModelIdempotencyStore:
    """An in-memory `IdempotencyStore` whose PK is parameterised by `key_fn`, so the cross-tenant
    attack can swap the REAL `(task_id, tenant)` key for the tenant-blind neuter and observe the
    collision directly. Behaviour otherwise mirrors `PostgresIdempotencyStore`:

      - first `claim_or_get` for a key -> creates a 'processing' row, returns `None` (claim won);
      - a 'done' row -> returns the persisted `StoredResult` (replay; handler does NOT run);
      - a 'processing' row -> returns `None` (another replica; the real store's best-effort poll
        times out to the same `None`, `idempotency.py:289-297`);
      - `complete` seals 'processing' -> 'done' (idempotent, never reseals).

    A single shared instance models the ONE `a2a_idempotency` table both tenant replicas write to;
    `preseed_processing` models the crash-before-complete window (a row claimed, effect fired, then
    the process died before `complete` sealed it)."""

    def __init__(self, *, key_fn: Callable[[str, str], Hashable] = real_pk) -> None:
        self._key_fn = key_fn
        self.rows: dict[Hashable, dict[str, object]] = {}

    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | None:
        key = self._key_fn(tenant, task_id)
        row = self.rows.get(key)
        if row is None:
            self.rows[key] = {"status": "processing", "result": None, "requested_enqueued_at": None}
            return None  # claim won — the caller executes
        if row["status"] == "done":
            return row["result"]  # type: ignore[return-value]  # replay — handler does NOT run
        return None  # 'processing' — best-effort poll timeout (idempotency.py:289-297)

    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None:
        key = self._key_fn(tenant, task_id)
        row = self.rows.get(key)
        if row is not None and row["status"] == "processing":
            row["status"] = "done"
            row["result"] = StoredResult.from_result(result)

    async def requested_emitted(self, *, tenant: str, task_id: str) -> bool:
        """The `requested_enqueued_at` marker (migration 0011), per row — same PK as everything else."""
        row = self.rows.get(self._key_fn(tenant, task_id))
        return row is not None and row.get("requested_enqueued_at") is not None

    async def mark_requested(self, *, tenant: str, task_id: str) -> None:
        row = self.rows.get(self._key_fn(tenant, task_id))
        if row is not None and row.get("requested_enqueued_at") is None:
            row["requested_enqueued_at"] = "marked"  # COALESCE: keeps the FIRST observed emission

    def preseed_processing(self, *, tenant: str, task_id: str) -> None:
        """Crash-before-complete: a row claimed by the (now-dead) first execution, never sealed.

        The marker stays NULL on purpose: migration 0011 forbids backfilling it ("a legacy claim
        cannot establish that a requested fact existed"), so a preseeded crash row models the
        worst case the redelivery must survive — it re-emits `requested` (a duplicate the consumer
        collapses on `fact_dedup_key`) rather than assuming the dead attempt got the fact out.
        """
        self.rows[self._key_fn(tenant, task_id)] = {
            "status": "processing",
            "result": None,
            "requested_enqueued_at": None,
        }


class NeuteredClaimStore:
    """RED control — the durable claim NEUTERED to the pre-leg-3 posture: `claim_or_get` never
    dedups (always 'claim won') and `complete` is a no-op. Every delivery RE-EXECUTES the handler,
    which is exactly "neuter idempotency claim -> double execution" — the harm Layer 1 exists to
    prevent on the SEALED (`complete` ran) path."""

    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | None:
        _ = (tenant, task_id)
        return None

    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None:
        _ = (tenant, task_id, result)
        return None

    async def requested_emitted(self, *, tenant: str, task_id: str) -> bool:
        # Neutered like the rest of this control: no durable marker, so the seam degrades to the
        # pre-fix posture (every redelivery re-emits `requested`). Never to a LOSS.
        _ = (tenant, task_id)
        return False

    async def mark_requested(self, *, tenant: str, task_id: str) -> None:
        _ = (tenant, task_id)
        return None


# ---------------------------------------------------------------------------
# Downstream fact consumers — the at-least-once / consumer-dedup contract
# ---------------------------------------------------------------------------


class DedupConsumer:
    """A downstream consumer that dedups on the STABLE `fact_dedup_key`
    (`{tenant}:a2a:delegate:{task_id}:{kind}`, leg-2 proved stable across redelivery). At-least-once
    outbox redelivery collapses to ONE downstream effect."""

    def __init__(self) -> None:
        self.effects: list[bytes] = []
        self._seen: set[str] = set()

    def consume(self, *, dedup_key: str, payload: bytes) -> str:
        if dedup_key in self._seen:
            return "duplicate_collapsed"
        self._seen.add(dedup_key)
        self.effects.append(payload)
        return "processed"


class NoDedupConsumer:
    """The DISCLOSED at-least-once consequence (RED): a consumer that ignores the dedup key treats
    every redelivery as a fresh fact — so duplicates DO reach downstream. This is not a bug in the
    outbox; it is the outbox's honest at-least-once contract, made a tested expectation."""

    def __init__(self) -> None:
        self.effects: list[bytes] = []

    def consume(self, *, dedup_key: str, payload: bytes) -> str:
        _ = dedup_key
        self.effects.append(payload)
        return "processed"
