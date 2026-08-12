# ADR-0039: A2A Delegation-Envelope Signing — Canonical Digest, Key Rotation, Fail-Closed Verification

**Status:** Proposed — requires Security/crypto reviewer (R1) + orchestrator ratification (no
self-certification). Named approver-role fields below are left EMPTY; no agent, orchestrator, or
gatekeeper may fill them. · **Data:** 2026-08-11 · **Area:** Seguranca / A2A / Auditoria

| Role | Approver | Date |
|---|---|---|
| Security/crypto reviewer (R1) | _(pending)_ | _(pending)_ |
| Orchestrator (architecture ratification) | _(pending)_ | _(pending)_ |

Owner: envelope-adr-author (R2), leg 1/4 of Train C (Onda 3 — A2A distribution-grade),
`wave3-a2a-distribution`, created at `origin/main` = `29763e7`. Scope: docs-only — this ADR designs
envelope signing; it does **not** implement it and does **not** touch `src/`, `spec/`, `tests/`,
`deploy/`, or the effect-chokepoint fence allowlist. Legs 2-4 of this train build against the
constraints fixed here.

## Contexto

### 1. What exists today — two DIFFERENT signing surfaces, only one built

A2A already has cryptographic signing, but it covers the **Agent Card** (per-agent identity),
never the **delegation envelope** (the per-message instruction). Keeping the two distinct is
load-bearing for the rest of this ADR:

- **Agent Card signing (built, T-G, `t2.4-a2a-w1-card-signing` → merged)** —
  `AgentCard.signing_payload()` (`src/maezo/a2a/card.py:101-124`) canonicalizes the Card's
  identity/capability fields (`json.dumps(payload, sort_keys=True, separators=(",", ":"))`,
  UTF-8-encoded) and `CardSigner` (`src/maezo/a2a/signing.py:35-114`) HMAC-SHA256s that byte string
  under a single injected key, tagging the result `f"{scheme}:{hexdigest}"` with
  `SIGNATURE_SCHEME = "v1"` embedded in the signed payload itself (`card.py:50`). `A2ARegistry`
  (`src/maezo/a2a/registry.py:37`, class definition) takes an **optional** `verifier: CardSigner |
  None = None` — when absent, Cards are admitted regardless of signature state (the "Phase-0/dev
  fail-safe" the class docstring names explicitly, `registry.py:14-16`); `register()`
  (`registry.py:66-76`) only calls `self._verifier.require_valid(card)` when a verifier was
  injected. Production composition (`src/maezo/runtime/agent_runtime/a2a_composition.py`) closes
  this gap with a fail-closed gate, `_require_signer_or_fail_closed` (`:136-189`) — see §3 below.
- **Delegation-envelope signing (does NOT exist — this ADR's subject).**
  `DelegationEnvelope` (`src/maezo/a2a/delegation.py:98-99`, `@dataclass(frozen=True, slots=True)` /
  `class DelegationEnvelope:`) carries `task_id, task_type, origin, target, tenant,
  delegation_chain, budget, payload_ref, max_hops, deadline, payload_meta` (`:113-123`) — **no
  signature field of any kind**. Nothing in `root()` (`:150-187`) or `extend()` (`:189-226`) signs
  anything; the four ADR-0003 anti-loop guards (acyclic, `max_hops=3`, budget, `task_id`
  idempotency) are structural (raise-on-construction), not cryptographic. A forged or
  in-transit-tampered envelope that still satisfies those four structural guards passes unnoticed —
  there is no mechanism today that would catch it.

The two surfaces answer different questions: the Card answers "is this really Rafael's registered
capability set, signed by whoever holds the trust key?"; the envelope this ADR designs answers "is
this *specific* delegation — this `task_id`, this `budget`, this `deadline`, this `payload_ref` —
exactly what the signer authorized, unaltered?" Card signing does not imply envelope signing, and
neither today implies the other.

### 2. The gaps this ADR closes are already named in the code, not invented here

- **In-memory idempotency does not survive a restart.** `DelegationDispatcher`
  (`src/maezo/a2a/dispatcher.py:247`, class definition) documents its own limit
  (`:252-257`): `idempotency=None` (the default) backs Guard 4 with an in-memory
  `_inflight` dict that "does NOT survive replica restarts (re-delegation)". The durable
  alternative, `PostgresIdempotencyStore` (`src/maezo/a2a/idempotency.py:218-311`), exists and is
  fully implemented, but is wired **only when `database_url` is truthy** at both composition roots:
  `src/maezo/runtime/agent_runtime/a2a_composition.py:266` (`PostgresIdempotencyStore(dsn=...) if
  settings.database_url else None`) and `:369` (`... if database_url else None`). Neither site
  checks `runtime_mode` — unlike the Card-signing key gate (§3 below), **there is no fail-closed
  check today that refuses to compose a dispatcher with in-memory-only idempotency in a production
  runtime.** An operator who forgets `DATABASE_URL` in production gets silent, non-durable Guard 4
  with no raise and no warning log. This ADR's mandate in §Decisão 4.6 is exactly closing that gap.
- **Facts are observability, not audit — `_NoopKafkaProducer` is a named, intentional stand-in.**
  `class _NoopKafkaProducer` (`a2a_composition.py:192-201`) is constructed at both composition roots
  (`:264`, `:368`, `kafka_producer or _NoopKafkaProducer()`) whenever no real producer is injected.
  Its own docstring is explicit: "Facts are observability, never the T-F audit surface (that rides
  the real `PostgresAuditSink`...), so a dropped fact never compromises the delegation-audit proof."
  This is a **declared, by-name exception** in the effect-chokepoint fence's §8.4 allowlist
  (`scripts/ci/check_effect_chokepoint_fence.py:401-411`, entry at `:406`:
  `("runtime/agent_runtime/a2a_composition.py", "_NoopKafkaProducer")`) — the fence's
  `_is_test_double_name` (`:415-425`) would otherwise flag any `Noop*`/`Fake*`/`*Mock*` name
  constructed from a composition-root module; this one is allowlisted **by name with rationale**,
  never by loosening the pattern. §Decisão 4.6 below treats replacing `_NoopKafkaProducer` with a
  transactional outbox as a consequence for legs 2-3, and is explicit about what that means for this
  allowlist entry.
- **`RUNTIME_MODE`/`AGENT_RUNTIME_MODE` fail-closed discriminator — already used, already
  asymmetric.** `a2a_composition.py:104-128` is the block this ADR's verification-gate design
  reuses: `_LOCAL_RUNTIME_MODE = "local"` (`:104`), `ALLOW_UNSIGNED_CARDS_ENV_VAR` (`:111`),
  `WORKER_RUNTIME_MODE_ENV_VAR = "RUNTIME_MODE"` (`:123`), and
  `worker_runtime_mode_from_env()` (`:126-128`): `os.environ.get(WORKER_RUNTIME_MODE_ENV_VAR,
  "").strip() or "production"` — **absent → `"production"` (fail-closed)**. The
  agent-runtime edge instead reads `AgentRuntimeSettings.agent_runtime_mode`
  (`src/maezo/runtime/agent_runtime/settings.py:38`,
  `Field(default="local", alias="AGENT_RUNTIME_MODE")`) — **absent → `"local"` (fail-OPEN
  default)**. Both converge on `"production"` when the variable is *present but explicitly set to
  the empty string* (pydantic assigns the literal `""`, then `"" != "local"` → production; the
  worker path's own `.strip() or "production"` gives the same result for `""`) — that convergence
  is the "both-empty-string residual" this ADR does **not** touch. The asymmetry that *does* matter
  is the **absent-variable** case: the same two-runtime-mode idiom defaults permissively on one
  composition root and restrictively on the other. This is a **pre-existing, owner-level
  inconsistency**, not introduced by this ADR and not this ADR's to fix — §Decisão 4.4 states
  precisely how the envelope-verification gate inherits it (does not resolve it).
- **The gated seam pins the dispatcher's public surface to exactly `{delegate}`.**
  `GatedDelegationDispatcher` (`src/maezo/gateway/seams/a2a.py:54-62`) subclasses
  `DelegationDispatcher` rather than structurally decorating it (module docstring `:9-30` explains
  why: the class is spelled as a concrete annotation in eight call sites, so a `Protocol`
  introduction would be real churn for a wave whose whole claim is invisibility). The docstring
  states the safety argument explicitly (`:23-26`): "`delegate` is the ONLY public method of
  `DelegationDispatcher`... `test_delegation_dispatcher_public_surface_is_only_delegate` asserts
  exactly that, so the day someone adds a second public method the gate fails loudly instead of
  silently inheriting an ungated path." That test exists and is where it is claimed:
  `tests/unit/gateway/seams/test_seam_proofs.py:809`. §Decisão 4.4 below is explicit that envelope
  verification must be **internal** (a `_`-prefixed helper called from inside `delegate`/`_execute`,
  never a new public method) for exactly this reason.

### 3. Composition-root precedent this ADR's verification gate mirrors

`_require_signer_or_fail_closed` (`a2a_composition.py:136-189`) is the existing fail-closed gate for
Card signing, and its three-way structure is the template §Decisão 4.4 reuses for envelopes: key
present → sign/verify; key absent + production runtime → `RuntimeError` at composition (no
dispatcher is ever returned); key absent + non-production + **explicit**
`MAEZO_A2A_ALLOW_UNSIGNED_CARDS=1` opt-out → proceed unsigned, loudly warned (`logger.warning`,
`:178-188`); key absent + non-production + no opt-out → also raise. The function is already
generalized across both composition roots (`build_auth_delegation_dispatcher`,
`build_dossier_delegation_dispatcher`) via an injected `runtime_mode` parameter (`:156-160`
docstring), so the envelope-verification gate this ADR proposes (§4.4) can be built as a structural
sibling at the same two call sites without a new composition-root shape.

### 4. Non-interaction with the AMH boundary (ADR-0037)

ADR-0037 fixes the Maezo↔AMH contract boundary: Kafka work-item/consent/outcome topics, the
`config/integrations/amh/contracts.lock.json` pin, and the `ActionExecutionGateway` chokepoint for
external effects crossing into the AMH data plane. A2A delegation is **intra-Maezo**
agent-to-agent communication inside the payer's own runtime (Helena→Rafael, worker→Carolina/Andre)
— it never crosses into the AMH data plane, never touches a `cdc.*`/`amh.*` topic, and is not part
of ADR-0037's canonical envelope baseline (which is a *different* envelope — the AMH CDC
work-item/consent/outcome wire format, ADR-0037 "Baseline congelado" §, itself coincidentally
naming a `payload_hash` field for that unrelated wire). **One line, as instructed: no interaction —
envelope signing is entirely internal to the Maezo A2A runtime and does not touch, extend, or
constrain any AMH-boundary artifact.**

## Decisao

This ADR designs delegation-envelope signing. It ratifies the **design**; legs 2-4 of this train
build it. No code changes accompany this ADR.

### 4.1 Canonical digest — field set and canonicalization

The signed payload covers exactly the mandated field set
`{tenant, task_id, origin, target, payload_hash, deadline, budget, delegation_chain}`, mapped to a
canonical JSON object:

```
{
  "tenant": envelope.tenant,
  "task_id": envelope.task_id,
  "origin": envelope.origin,
  "target": envelope.target,
  "payload_hash": sha256(envelope.payload_ref.encode("utf-8")).hexdigest(),
  "deadline": envelope.deadline.astimezone(UTC).isoformat() if envelope.deadline else None,
  "budget": {"tokens": budget.tokens, "time_ms": budget.time_ms, "cost_per_hop": budget.cost_per_hop},
  "delegation_chain": list(envelope.delegation_chain),
  "scheme": <signature scheme tag, see §4.2>,
  "key_id": <signing key identifier, see §4.2>,
  "replay_epoch": <trust epoch, see §4.2>,
}
```

**Canonicalization: reuse the repo's existing convention — precisely, not by rounding two related
recipes together.** The repo already has TWO close-but-not-identical "canonical JSON" recipes, and
this ADR is explicit about which one it adopts. `AgentCard.signing_payload()`
(`card.py:124`, `json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")`) and the
standalone `hash_input()` helper (`audit.py:51-61`, `json.dumps(payload, sort_keys=True,
separators=(",", ":"), default=str)`, "SHA-256 of a canonical (sorted-key) JSON encoding") both use
**compact separators** (`,`/`:`, no whitespace). `AuditRecord._compute_hash()`'s own preimage
(`audit.py:260-284`) uses `json.dumps({...}, sort_keys=True, default=str)` — sorted keys, but
**Python's default (spaced) separators**, since it passes no `separators=` argument at all. These
two recipes produce different bytes for the same content and are NOT interchangeable; this ADR
adopts the **compact-separator variant** (`sort_keys=True, separators=(",", ":")`), matching
`card.signing_payload()` and `hash_input()` — the more directly relevant precedent, since it lives in
the same `a2a` package `signing_payload()` already signs, and compact separators leave no whitespace
ambiguity for a re-encoder to exploit (§ malleability argument below). Adopting a *third*,
differently-shaped canonicalization for envelopes (e.g. a literal
RFC 8785 JCS implementation, or a length-prefixed field-concatenation scheme) would buy no
additional guarantee for this field set — every value here is a string, a bounded int, `None`, or a
tuple/list of strings; there are no floats, no non-ASCII-normalization edge cases, and no nested
maps deep enough for RFC 8785's marginal guarantees (canonical float formatting, deep Unicode
normalization) to matter. `sort_keys=True` + compact separators is sufficient to make the byte
string a pure function of content, independent of dict/set iteration order — the same argument
`card.py:106-109`'s own docstring makes for the Card. **Why ambiguity = malleability, stated
precisely:** without `sort_keys` + fixed separators, two semantically-identical payloads (same
field values, different key order, or `", "` vs `","` whitespace) would serialize to different byte
strings and produce different MACs — an attacker who can influence serialization (e.g. relaying
through a JSON re-encoder) could then present a *re-encoded* copy of a validly-signed envelope with
a MAC that no longer matches, turning a legitimate signature verification into a denial-of-service
false-negative, or, worse, if a verifier naively re-canonicalizes before checking, opening a gap
where two byte-distinct-but-field-equal payloads are treated as interchangeable when they should
not be (classic JSON-canonicalization signature malleability). Fixing the canonicalization function
once, structurally, removes the ambiguity entirely rather than requiring every producer/consumer
pair to agree out-of-band.

**Why `payload_hash`, not `payload_ref` directly, in the digest.** The envelope's actual field is
`payload_ref` (a FHIR/pseudonymized reference string, never raw PHI — enforced by
`_looks_like_phi()`, `delegation.py:257-264`). Hashing it before inclusion (a) bounds the signed
payload's size and shape regardless of how long a future reference scheme's strings get, (b) mirrors
the ALREADY-established naming convention `payload_hash` from ADR-0037's canonical envelope
baseline (a coincidental but reassuring naming precedent for "a hash-of-payload field belongs in an
envelope's frozen field set"), and (c) keeps the digest's preimage shape stable if the "payload"
concept ever grows beyond a bare reference string (only the hash's *input* changes; the outer digest
schema does not). This is a design choice, not a requirement forced by any existing code — flagged
explicitly as a decision, open to legs 2-4 revisiting if they find a reason `payload_ref` should be
signed directly instead.

**Disclosed residual: `task_type` and `payload_meta` and `max_hops` are excluded from the mandated
field set, and that has a real consequence.** The mandated digest fields
(`{tenant, task_id, origin, target, payload_hash, deadline, budget, delegation_chain}`) do **not**
include `task_type`. `payload_meta`'s exclusion is *consistent* with an existing precedent — the
dispatcher's own audit `details` payload deliberately excludes `envelope.payload_meta`
(`dispatcher.py:451-456`, "`details` deliberately EXCLUDES `envelope.payload_meta`... mirroring
`facts.build_fact`'s own exclusion of `meta`") — so leaving it out of the signed digest too is the
same call, made once, applied twice. `max_hops` is a repo-wide structural constant
(`MAX_HOPS: int = 3`, `delegation.py:42`) shared by every envelope, not sender-supplied data, so its
omission carries little marginal risk. **`task_type`'s omission is different and is flagged as a
genuine gap, not a stylistic consistency call:** `task_type` determines which Agent Card capability
gate the delegation is checked against (`A2ARegistry.lookup(...).accepts(task_type)`,
`dispatcher.py:388`) and which handler branch a target routes into (see
`a2a_composition.py:88-98`'s comment on Andre's shared `analytics.population` type
disambiguated by *origin*, not `task_type`, into different flows — meaning a tampered `task_type`
on an otherwise-validly-signed envelope could, depending on the target's routing logic, redirect a
delegation to a different semantic outcome without invalidating the signature). This ADR follows the
mandated field set as given; it does **not** silently widen it. The residual is listed in
§Perguntas abertas below for a human to resolve (include `task_type` in the digest, or provide an
explicit argument for its exclusion beyond "it wasn't in the mandate").

### 4.2 Signature metadata — key-id, algorithm version, replay epoch

Three new fields, bound INTO the signed digest itself (§4.1's payload already includes them) so
that none of them can be swapped post-signing without invalidating the MAC — the same reasoning
`card.py:122` gives for embedding `"scheme": SIGNATURE_SCHEME` inside the Card's own signed payload
(an unbound `key_id`/`scheme`/`epoch` would let an attacker present a validly-MAC'd payload under a
*different* claimed key/scheme/epoch than the one that actually produced it — a
key-confusion/downgrade attack, the JSON-signing analogue of JWT's "alg:none" class of bugs):

- **`scheme`** — an algorithm+version tag, e.g. `"hmac-sha256-envelope-v1"` (distinct string space
  from the Card's `SIGNATURE_SCHEME = "v1"`, §4.1's note on the two surfaces being independent) so a
  future migration (asymmetric signing, a different digest algorithm) is representable without
  breaking the field's shape — mirrors `card.py:48-50`'s own rationale for `SIGNATURE_SCHEME`
  verbatim.
- **`key_id`** — identifies *which* trusted key produced the MAC. Card signing today has exactly one
  active key (`MAEZO_A2A_CARD_SIGNING_KEY`, `assembly.py:45`, a scalar, no rotation story). Envelope
  signing needs **more than one concurrently-trusted key** to rotate without an outage: the verifier
  resolves `key_id` against a small trusted-keyset (`dict[key_id, bytes]`), not a single scalar, and
  signing always uses the CURRENT/active `key_id`. A stale `key_id` (retired but not yet purged from
  the trusted set) is only accepted inside its explicit rotation grace window (below); a `key_id`
  the verifier has never heard of is rejected unconditionally.
- **`replay_epoch`** — a monotonically-increasing integer, distinct from ordinary key rotation.
  Ordinary scheduled rotation adds a new `key_id` under the **same** epoch (smooth overlap, both
  keys valid). An epoch bump is a **hard cutover**, reserved for suspected-compromise / incident
  response: every envelope claiming a prior epoch is rejected immediately regardless of whether its
  `key_id` is otherwise still in the trusted set — the blunt, fast instrument for "we do not trust
  *anything* signed before this moment," independent of which specific key produced it. This is
  deliberately a coarser, cheaper mechanism than per-key revocation: bumping the epoch does not
  require enumerating or purging every possibly-compromised key.

### 4.3 Key rotation and revocation

Ordinary rotation: publish a new `key_id`→key entry into the trusted set, start signing new
envelopes under it, retain the *previous* `key_id` in the trusted set for a **grace window bounded
by the longest `deadline` horizon actually in use** — because `DelegationEnvelope.deadline` already
upper-bounds how long any single envelope can remain "in flight" before `expired()`
(`delegation.py:144-148`) independently rejects it, the rotation grace window need never exceed that
horizon: once every envelope that could have been signed under the old key has either completed or
expired on its own, the old `key_id` can be purged with zero risk of rejecting a still-legitimate
in-flight delegation. This ties rotation cleanly to a bound the envelope already enforces, rather
than inventing a separate rotation-specific TTL.

Revocation (suspected key compromise): remove the compromised `key_id` from the trusted set
immediately AND bump `replay_epoch` in the same action — removing the `key_id` alone is
insufficient if the compromise is severe enough that the attacker could forge envelopes under a
*still-trusted* `key_id` from the same epoch; the epoch bump is the belt to the `key_id` removal's
suspenders, covering the case where the specific compromised key is not precisely known.

### 4.4 Verification points — where, and the fail-closed gate

**Signing** happens once, at envelope construction time, by whichever composition root holds the
signing key — mirroring `AgentCard.from_definition(..., signer=signer)`'s pattern
(`card.py:140-185`) of signing at the exact point identity is fixed. The precise call shape (a
`signer=` parameter threaded through `root()`/`extend()`, or a standalone `sign_envelope(envelope,
signer) -> DelegationEnvelope` step applied immediately after) is an implementation decision for
legs 2-4; this ADR fixes only that signing must happen before the envelope crosses the dispatcher
boundary and must cover exactly §4.1's canonical payload.

**Verification** happens inside `DelegationDispatcher`, as the **first** check —
before the idempotency claim/replay lookup (§4.6's `_delegate_inflight`/`_delegate_durable`,
`dispatcher.py:283-310`) and before `_validate()` (`:376-400`, registry/Card lookup, `task_type`
acceptance, expiry). Verifying first means a forged envelope never touches the idempotency store
(no poisoning risk on a `task_id` an attacker chose), never triggers a registry lookup, and is
rejected before any audit-relevant state changes. A failed verification returns a structured
`DelegationResult.rejected(...)` (never a raise — preserving the dispatcher's existing "never
raised to the caller" contract, `dispatcher.py:16`'s own module docstring; `DelegationResult`
itself is documented "never a raise out of the dispatcher", `dispatcher.py:154`), via a **new**
`RejectionReason` member
(e.g. `SIGNATURE_INVALID`) that legs 2-4 add to the existing enum (`dispatcher.py:136-141`) — new
vocabulary this ADR authorizes but does not itself add (no code changes here).

**This new check must be `_`-prefixed / internal, never a new public method** — §Contexto point 4
above is the reason restated as a hard constraint: `GatedDelegationDispatcher`
(`gateway/seams/a2a.py:54-62`) only forwards `delegate`, and
`test_delegation_dispatcher_public_surface_is_only_delegate`
(`tests/unit/gateway/seams/test_seam_proofs.py:809`) fails the build the moment
`DelegationDispatcher` grows a second public method. Verification logic belongs entirely inside
`delegate`/`_execute`'s existing call graph. **If legs 2-4 need to expose verification as a
standalone public entry point for any reason (e.g. a pre-flight check callable before `delegate`),
the `GatedDelegationDispatcher` wrapper MUST be updated in the SAME change** to forward it through
the `delegacao_a2a` gate — otherwise a new public method is a structurally ungated hole in the
action-execution-gateway chokepoint (ADR-0037 XRD-09), silently bypassing the seam whose entire job
is to be the *only* place `delegate` — or now potentially a sibling method — is reachable from.

**Fail-closed outside dev-local, reusing the existing discriminator — and inheriting its known
asymmetry.** The verification gate mirrors `_require_signer_or_fail_closed`
(`a2a_composition.py:136-189`) exactly, as a structural sibling at both composition roots: trusted
keyset present → verify; absent + production runtime → raise at composition (no dispatcher without
a verifier is ever returned); absent + non-production + an explicit, new
`MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES`-shaped opt-out → proceed unverified, loudly warned; absent +
non-production + no opt-out → also raise. Because this gate is built as a sibling at the SAME two
call sites, it inherits whichever runtime-mode discriminator that site already uses: the
Helena→Rafael edge (`build_auth_delegation_dispatcher`) reads `settings.agent_runtime_mode`
(`AGENT_RUNTIME_MODE`, **absent → `"local"`, permissive-by-default**); the worker dossier edge
(`build_dossier_delegation_dispatcher`) reads `worker_runtime_mode_from_env()` (`RUNTIME_MODE`,
**absent → `"production"`, restrictive-by-default**). This ADR does **not** resolve that asymmetry
— it is a pre-existing, owner-level inconsistency (§Contexto point 2) — and states plainly that the
"fail-closed outside dev-local" guarantee this ADR mandates is, at the Helena→Rafael edge,
**only as strong as `AGENT_RUNTIME_MODE` actually being set** in every real deployment; an
unset `AGENT_RUNTIME_MODE` in a genuinely-production agent-runtime pod would silently qualify as
"local" for BOTH the existing Card-signing gate and this ADR's new envelope-verification gate.
Fixing that default is out of scope here; legs 2-4 inherit it as a known residual, not a
newly-introduced one.

### 4.5 Threat table

| Attack | Defense |
|---|---|
| **Tamper** (any signed field altered in transit or in-process before reaching the dispatcher) | HMAC-SHA256 over the canonical digest (§4.1) fails; the dispatcher's verification step (§4.4, first check in `delegate`) rejects before idempotency claim, audit, or routing. |
| **Cross-tenant replay** (an envelope captured for one tenant re-presented against another) | `tenant` is inside the signed digest — retagging it invalidates the MAC. Defense-in-depth: `A2ARegistry` is tenant-scoped by construction (ADR-0004, `registry.py:8-10`) and `PostgresIdempotencyStore`'s primary key is `(task_id, tenant)` (`idempotency.py:38`), so even a same-`tenant`-field replay against a different registry/store partition finds no matching Card/claim. |
| **Expiry bypass** (`deadline` stripped or extended after signing) | `deadline` is inside the signed digest — altering it invalidates the MAC. Independently, `DelegationEnvelope.expired()` (`delegation.py:144-148`) is still checked by `_validate()` even for a validly-signed, correctly-dated envelope — two independent checks, neither a substitute for the other. |
| **Stale key** (an envelope signed under a retired or compromised key) | `key_id` and `replay_epoch` are inside the signed digest (§4.2) and separately checked against the CURRENT trusted keyset — a retired `key_id` fails outside its rotation grace window (§4.3); an epoch bump (§4.2) hard-rejects every prior-epoch signature regardless of `key_id` validity. |
| **Altered `payload_hash`** (`payload_ref` swapped for a different FHIR/pseudonymized reference after signing) | `payload_hash` is inside the signed digest — swapping `payload_ref` changes the hash, invalidating the MAC. Binds the specific PHI-safe reference to the exact authorized envelope; combined with the existing `_looks_like_phi()` guard (`delegation.py:257-264`), which independently rejects raw-PHI-shaped references at construction. |
| **Crash-before-complete** (dispatcher/replica crashes after admission but before the handler's effect completes) | **Signature verification alone does not defend this** — it is orthogonal. The actual defenses are DURABLE idempotency (Guard 4, `PostgresIdempotencyStore`, already implemented but only conditionally wired — §4.6) and a TRANSACTIONAL OUTBOX replacing `_NoopKafkaProducer`'s fire-and-forget emission — **both planned in this train, legs 2-3, not delivered by this ADR.** Referenced forward honestly: this ADR mandates them as consequences (§4.6); it does not build them. |

### 4.6 Consequences for legs 2-3: durable idempotency + transactional outbox are MANDATORY design constraints

**Durable idempotency MANDATORY outside dev-local.** Today, `PostgresIdempotencyStore` wiring is
gated purely on `database_url` truthiness (`a2a_composition.py:266`, `:369`) — with **no
runtime-mode check at all**, unlike the Card-signing gate. This is a real, currently-existing gap:
a production runtime with `DATABASE_URL` unset today silently falls back to in-memory-only Guard 4
with no raise, no warning. This ADR's design constraint for legs 2-3: build a
`_require_durable_idempotency_or_fail_closed`-shaped gate, structurally mirroring
`_require_signer_or_fail_closed`, at BOTH composition roots — absent `database_url` + production
runtime → raise at composition; absent + non-production + explicit opt-out → proceed in-memory,
loudly warned; absent + non-production + no opt-out → raise. This closes the exact gap §Contexto
point 2 names, and is a precondition for the "crash-before-complete" defense in §4.5's threat table
to actually hold outside dev-local.

**Transactional outbox replacing `_NoopKafkaProducer`'s facts — with an explicit fence-allowlist
consequence.** `_NoopKafkaProducer` is a **declared, by-name exception**
(`check_effect_chokepoint_fence.py:401-411`, entry `:406`) in the effect-chokepoint fence's §8.4
allowlist — the design doc's own instruction (`docs/design/wave1-effect-chokepoint.md:742-746`) is
"declare explicitly, not silently... allowlist it by name with the rationale, never by pattern."
If legs 2-3 replace `_NoopKafkaProducer` with a real, transactional-outbox-backed producer at
BOTH construction sites (`a2a_composition.py:264`, `:368`), the fence's declared exception entry
becomes unused once no construction of that literal name remains, and should be **removed, not
widened** — consistent with `_is_test_double_name`'s own comment (`:416-425`) that this gate never
loosens its `Fake*`/`*Mock*`/`Noop*` pattern match on its own authority. If a *new* named
local/dev-only fallback double is introduced in the replacement's place, it must be added to
`DECLARED_TEST_DOUBLE_EXCEPTIONS` (`:401`) **by name, with rationale, in the same change** — never
by pattern, and never silently. This ADR does not modify that allowlist; it records the constraint
for whichever leg does the replacement.

### 4.7 mTLS / service identity — DEFERRED

**Deferred, explicitly, until a remote transport exists.** The design doc for the existing A2A build
states this outright and unchanged across multiple waves: "Remote cross-pod A2A transport
(queue_ref/endpoint, HTTP/mTLS) remains explicitly out of scope"
(`docs/design/A2A-dispatcher-card-signing.md:320`). Today's ONLY built edge (Helena→Rafael, and the
worker→Carolina/Andre dossier edges) is **Option A: in-process co-located**
(`a2a_composition.py:1-10` module docstring) — there is no network hop, no serialization boundary,
and therefore no transport-authenticity question for mTLS to answer yet. Envelope signing as
designed here is transport-independent (it protects the envelope's *content*, not the channel it
travels over) and is valuable even in-process today (defense against a compromised/buggy in-process
caller constructing a spoofed envelope via direct dataclass construction outside `root()`/`extend()`
— nothing in the current code prevents that — plus strengthening non-repudiation for the audit
chain, ADR-0007), but it does **not** substitute for mTLS once a real remote transport exists.
**Trigger condition, stated explicitly for whoever eventually builds a remote transport:** mTLS
(or an equivalent transport-authenticity mechanism) becomes REQUIRED design work the moment any A2A
edge crosses a process/pod boundary over a network (i.e., the moment Option A's "in-process
co-located" premise stops holding for any edge) — this is not a recommendation, it is the condition
under which "explicitly out of scope" stops applying and a NEW ADR (or an amendment to this one)
must cover it before that transport ships.

## Consequencias

**Positivas:**
- Closes a real, currently-open gap: a forged or tampered delegation envelope that satisfies the
  four existing structural anti-loop guards passes today with no cryptographic check at all — this
  ADR gives the dispatcher a fail-closed, tamper-evident admission gate over the exact fields that
  matter (tenant, identity, payload reference, deadline, budget, chain).
- Reuses, rather than reinvents, three separate existing precedents (Card-signing HMAC/canonical-JSON
  pattern, the `_require_signer_or_fail_closed` composition-root gate shape, and the audit chain's
  own `sort_keys`+compact-separator canonicalization) — minimizing the number of NEW cryptographic
  primitives this repo has to reason about and review.
- Ties key-rotation grace windows to an already-enforced bound (`deadline`), rather than inventing a
  separate rotation-specific TTL policy.
- Makes the crash-before-complete gap's real fix (durable idempotency + transactional outbox)
  MANDATORY design constraints for legs 2-3, closing a durability gap that exists independently of
  signing (§4.6) and was previously silent.

**Negativas (aceitas):**
- New cryptographic surface: a compromised HMAC key (or, later, an asymmetric key) is a new risk the
  pure structural-guard design did not carry; key custody is explicitly a human/infra decision this
  ADR does not make (§Perguntas abertas).
- Inherits, rather than fixes, the `AGENT_RUNTIME_MODE`/`RUNTIME_MODE` absent-variable asymmetry
  (§4.4) — the fail-closed guarantee this ADR mandates is only as strong as that pre-existing
  discriminator at each composition root.
- `task_type` is excluded from the mandated digest field set despite being routing-significant
  (§4.1's disclosed residual) — a real, named gap left for human resolution, not closed by this ADR.
- No implementation ships with this ADR; legs 2-4 carry real build cost (new envelope fields, a new
  `RejectionReason` member, a keyset injection seam, two new fail-closed composition-root gates, a
  transactional outbox) before any of the threat-table defenses in §4.5 are real.

## Perguntas abertas (humano decide)

1. **`task_type` in the digest** — include it (closing §4.1's disclosed residual) or provide an
   explicit rationale for leaving it out beyond "it was not in the original mandate"?
2. **Rotation cadence** — how often should ordinary key rotation actually run (days? weeks?), given
   the grace-window design in §4.3 ties retirement safety to `deadline` horizons, not a calendar?
3. **Key custody** — who holds the signing key(s) day-to-day (same vault/KMS seam as
   `MAEZO_A2A_CARD_SIGNING_KEY`, or a separate secret), and is the keyset per-tenant or repo-wide
   (today's Card-signing key is repo-wide, single-key, no per-tenant scoping — should envelope
   signing diverge from that precedent, given a cross-tenant HMAC key leak would let an attacker
   forge envelopes across every tenant, arguably a sharper blast radius than a leaked Card key)?
4. **Epoch-bump grace window** — should a `replay_epoch` bump triggered by a suspected-compromise
   incident ever tolerate a short grace window for the prior epoch (to avoid mass in-flight
   rejection), or must an incident-triggered bump always be zero-grace (§4.2 leaves this
   unspecified)?
5. **`MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` naming/shape** — confirm the proposed opt-out env var
   name (§4.4) does not collide with any planned naming convention outside this ADR's visibility.
6. **`AGENT_RUNTIME_MODE` absent-default fix** — out of scope for this ADR (§Contexto point 2,
   §4.4), but flagged again here since it directly weakens this ADR's own fail-closed claim at the
   Helena→Rafael edge; a human owns the decision to change `AgentRuntimeSettings`'s default.

## Relacao com ADRs existentes

**ADR-0003 — extends, does not supersede.** ADR-0003 named the four anti-loop guards structurally;
this ADR adds a fifth, cryptographic guard (envelope-signature validity) operating alongside them,
not replacing any of the four.

**ADR-0007 — extends, does not supersede.** ADR-0007's non-repudiation clause ("Agent Card
assinado") is satisfied today by Card signing alone (§Contexto point 1). This ADR extends
non-repudiation from "who is Rafael" (the Card) to "exactly what was delegated to Rafael, unaltered"
(the envelope) — the same accountability goal, applied to the message rather than the identity.

**ADR-0015 — extends, does not supersede.** ADR-0015 formalized the envelope/dispatcher/registry
runtime as built in PR #17 and explicitly deferred a durable idempotency store to "Phase 1+"
(`0015-a2a-delegation-runtime.md:93-96`) — already delivered (`PostgresIdempotencyStore`) but only
conditionally wired (§4.6). This ADR adds the signature guard ADR-0015 never specified and tightens
the idempotency-wiring gap ADR-0015 flagged but left open.

**ADR-0033 — complementary, no dependency either direction.** ADR-0033 (Proposed, non-binding)
covers the OTHER half of T-G: verifiable **process/service** identity (a JWT or cert binding
`agent_id`/`tenant`/`agent_version` to the actual running process), explicitly orthogonal to Card
signing (`0033-tg-service-account-cert-identity.md:221-224`, "T-F... e ORTOGONAL a este ADR"). This
ADR's `key_id`/keyset design is independent of ADR-0033's eventual mechanism; if ADR-0033 is later
ratified, the process-identity token it designs COULD become a future source for envelope
`key_id` binding, but that is a future integration, not a dependency of this ADR.

**ADR-0037 — no interaction (§Contexto point 4).** One line, as required: A2A delegation is
intra-Maezo and never crosses the AMH boundary; this ADR touches nothing ADR-0037 governs.

## Supersedes

None. Amends/extends ADR-0003 (adds a fifth guard), ADR-0007 (extends non-repudiation from identity
to message), and ADR-0015 (specifies the signature guard it left unspecified; tightens the
idempotency-wiring gap it flagged). Does not supersede any ADR. Complementary to ADR-0033 (no
dependency either direction). Does not interact with ADR-0037.
