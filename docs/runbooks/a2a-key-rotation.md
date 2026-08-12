# Runbook: A2A Key Rotation — Agent Card signing today, what's pending

**Audience:** Security, platform, on-call
**Last updated:** 2026-08-12
**Applies to:** Maezo Healthcare Plan, all environments

> **Key custody: owner/KMS, always.** Agents (human or automated) never handle real signing key
> material. This runbook's procedures operate on environment-variable **references** to a key a
> human/KMS process has already placed there; no step here generates, transmits, or logs a real
> key value. The key-rotation drill ([`drills/key-rotation-drill.md`](drills/key-rotation-drill.md))
> exercises this end-to-end in a **dev** environment only, with a synthetic key.

---

## Table of Contents

1. [What exists today — Agent Card HMAC signing](#1-what-exists-today--agent-card-hmac-signing)
2. [Registry verification is optional (Phase-0/dev gap)](#2-registry-verification-is-optional-phase-0dev-gap)
3. [Rotating the signing key today](#3-rotating-the-signing-key-today)
4. [Rotating the ENVELOPE signing key (per tenant, 7-day cadence)](#4-rotating-the-envelope-signing-key-per-tenant-7-day-cadence)

---

## 1. What exists today — Agent Card HMAC signing

**Code:** `src/maezo/a2a/signing.py` (`CardSigner`), `src/maezo/a2a/card.py`,
`src/maezo/a2a/assembly.py` (`card_signer_from_key`, `card_signing_key_from_env`)

Agent Cards are signed with **HMAC-SHA256** (ADR-0003/0007), ported exactly from the donor
reference implementation for its security guarantee — algorithm over
`AgentCard.signing_payload()`, fail-closed on an empty signing key, constant-time verification
(`hmac.compare_digest`). This repo's hardening on top of the donor additionally rejects
whitespace-only and too-short keys:

```python
#: Minimum accepted signing-key length in bytes, measured AFTER stripping ASCII whitespace.
#: 16 bytes = a 128-bit floor... RFC 2104 recommends keys >= the digest length (32 bytes for
#: SHA-256) — real vault/KMS keys SHOULD be 32+ random bytes; this constant is the fail-closed
#: FLOOR, not the target.
MIN_SIGNING_KEY_BYTES = 16
```

**HMAC is symmetric** — the same secret signs and verifies. This suits an in-process, co-located
A2A runtime where the Card's producer and verifier share the same tenant trust boundary; the
A2A remote boundary (mTLS, when a remote transport exists) orthogonally covers transport
authenticity. The versioned `scheme` tag on a signed Card (`v1` = this HMAC-SHA256 scheme) lets a
future migration to asymmetric signing happen without breaking the `signature` field's shape.

**The key-injection seam:**

```python
#: Repo-namespaced env var carrying the Agent Card HMAC signing key (vault/KMS injection seam).
#: Absent -> card_signing_key_from_env() returns None -> card_signer_from_key builds no
#: signer -> Cards are derived unsigned (dev fail-safe path). NEVER hard-code a real key here.
CARD_SIGNING_KEY_ENV_VAR = "MAEZO_A2A_CARD_SIGNING_KEY"
```

`card_signing_key_from_env()` reads `MAEZO_A2A_CARD_SIGNING_KEY` from the process environment,
UTF-8 encoded, whitespace-stripped (a trailing newline from `echo secret > file` / a mounted
secret file is a transport artifact, not key material). `card_signer_from_key(signing_key)`
gates construction of a real `CardSigner` purely on the presence of that value — absent key ⇒
`None` ⇒ Cards are derived unsigned (the dev fail-safe path, not a production posture).
**Real key provisioning in a vault/KMS is an external/infra dependency**, explicitly called out
as blocked-for-this-build-wave in `docs/design/A2A-dispatcher-card-signing.md` §6 ("External /
infra (SME)" bucket: "Real card-signing key + service-account cert provisioning in vault/KMS
(blocked secret, §6.2)").

## 2. Registry verification is optional (Phase-0/dev gap)

**Code:** `src/maezo/a2a/registry.py` (`A2ARegistry`)

```python
#: `verifier` (a CardSigner) is the trust gate (ADR-0003/0007): when injected, `register`
#: REFUSES (fail-closed) a Card without a valid signature — so a tampered or unsigned Card
#: never enters the registry that a (future, W2) dispatcher consults via `lookup`.
#: `verifier=None` (default) preserves Phase-0/dev behavior (unsigned Cards are accepted):
#: verification is gated on the PRESENCE of the key, keeping the local fail-safe without
#: loosening production.
```

`A2ARegistry(verifier=None)` (the default) accepts Cards **regardless of signature state** — this
is explicitly a Phase-0/dev behavior, not a production hardening gap that needs separate tracking:
whether verification is enforced follows directly from whether a real `CardSigner` was
constructed from `MAEZO_A2A_CARD_SIGNING_KEY` (§1) and threaded into the registry as `verifier=`.
**Operationally: confirm any environment that should be enforcing Card signatures actually has
`MAEZO_A2A_CARD_SIGNING_KEY` set and wired to `A2ARegistry(verifier=...)`** — an absent key
silently (by design) degrades to accept-anything, which is correct for local dev and wrong for
anywhere that should be trust-gating Cards.

## 3. Rotating the signing key today

Because the CARD scheme is symmetric HMAC with a single environment-sourced key (no `key-id`/epoch
support on that surface — the ENVELOPE surface has both; see §4), "rotation" today means: **provision a new key value under the same env
var, restart every process that builds a `CardSigner` from it, and re-derive/re-register every
Agent Card so it is signed under the new key.** There is no live dual-key/grace-period mechanism
in the current code — `CardSigner` is constructed with exactly one key, and `verify()` checks
against exactly that key.

1. **Custody:** the new key value is generated and placed in the vault/KMS secret backing
   `MAEZO_A2A_CARD_SIGNING_KEY` by the key-custody owner (human/KMS process) — never by an agent,
   never typed into a shell by hand for a real key.
2. **Roll the value forward.** Every process that reads `MAEZO_A2A_CARD_SIGNING_KEY` at
   `card_signing_key_from_env()` call time (i.e. at Card-derivation time, via
   `build_agent_cards(..., signer=card_signer_from_key(card_signing_key_from_env()))`) must be
   restarted after the secret is rotated, so it re-derives and re-signs Cards under the new key.
   Because HMAC is symmetric and single-key, **every Card signed under the old key becomes
   unverifiable the moment any verifier switches to the new key** — this is not a rolling/
   overlapping rotation; plan for a coordinated cutover, not a gradual one.
3. **Re-register.** Any `A2ARegistry` running with `verifier=CardSigner(<old key>)` must be
   rebuilt with `verifier=CardSigner(<new key>)`, and every Card re-registered (re-derived and
   signed under the new key) — a stale Card signed under the old key will now fail
   `register()`'s fail-closed signature check.
4. **Verify:** `CardSigner.verify()` never raises (ASCII-encodes both comparison operands so a
   non-ASCII signature returns `False` rather than leaking a `TypeError`) — confirm rotation
   succeeded by exercising `register()`/`lookup()` against a freshly-derived Card, not just by
   checking the process restarted.

See [`drills/key-rotation-drill.md`](drills/key-rotation-drill.md) for a concrete, runnable
walk-through of this sequence in a dev environment.

## 4. Rotating the ENVELOPE signing key (per tenant, 7-day cadence)

**Status: BUILT.** This section replaced a placeholder that said envelope signing did not exist.
ADR-0039 is **Accepted** (with seven owner decisions, 2026-08-12) and the mechanism ships in
`src/maezo/a2a/envelope_signing.py`. Envelope signing is a **different surface from Card signing**
(§1/§3): different keys, different code path, different rotation semantics. Do not apply §3's
procedure here, and do not assume rotating one rotates the other.

The two surfaces differ in the way that matters operationally: Card signing is single-key with an
**all-or-nothing cutover** (§3), while envelope signing carries a `key_id` and a `replay_epoch`
precisely so rotation can be **overlapping** — a superseded key stays trusted for a grace window
instead of invalidating every in-flight envelope at once.

### 4.1 Key custody and the env var convention

Custody is unchanged: the same vault/KMS injection seam that provisions `MAEZO_A2A_CARD_SIGNING_KEY`
(§1). Only the **namespacing** is new — keys are **per tenant**, never repo-wide (ADR-0039 §4.4,
owner decision 4: a leaked repo-wide key would forge across every tenant at once).

| Slot | Environment variable | Present when |
|---|---|---|
| **Active** — signs new envelopes, always trusted for verification | `MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>` | always |
| **Prior** — rotation grace only; verified, never signed with | `MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__<TENANT>` | only during a rotation grace window |

`<TENANT>` is the tenant id **upper-cased** (e.g. tenant `amh` → `MAEZO_A2A_CARD_SIGNING_KEY__AMH`,
`MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__AMH`). Derived by `per_tenant_key_env_var` /
`per_tenant_prior_key_env_var` (`src/maezo/a2a/keyset.py:102`, `:119`); the `_PRIOR` rides the
**prefix** and the tenant stays the `__`-delimited **suffix**, so the active and prior variable
spaces are structurally disjoint and can never collide for any pair of tenants (`keyset.py:84-92`).
A tenant id outside `^[a-z][a-z0-9_]*$` is **refused**, never coerced (`keyset.py:110-115`).

There is **no cross-tenant and no repo-wide fallback**: a tenant with no key of its own gets `None`,
never another tenant's key (`keyset.py:197-212`). Provisioning tenant A does not provision tenant B.

**There is no separate key-id variable to keep in sync.** `key_id` is *derived* from the key material
(`derive_key_id`, `envelope_signing.py:131` — `env-sha256:<first 16 hex of sha256(key)>`), so
provisioning a new key value automatically yields a new id. A mismatched hand-maintained id would be
its own fail-open footgun, which is why one does not exist.

### 4.2 Ordinary rotation — the 7-day cadence

Ordinary rotation runs on a **7-day cadence** (ADR-0039 §Decisoes do dono, row 3). Per tenant:

1. **Custody generates the new key.** The key-custody owner (human/KMS process) generates the new
   value. Never an agent, never typed into a shell for a real key. It must be at least
   `MIN_SIGNING_KEY_BYTES` after whitespace-stripping — a shorter value is **refused at composition**
   (see §4.5), not silently accepted.
2. **Move the current active key into the prior slot.** Set
   `MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__<TENANT>` to the value currently in
   `MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>`. Do this **first**: the grace window must be open before
   the active key changes, or envelopes signed seconds before the cutover are rejected.
3. **Install the new key as active.** Overwrite `MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>` with the new
   value.
4. **Restart the processes that compose the delegation edges.** The keyset is read at composition
   time, not per request, so a running process keeps its old keyset until restarted. Both edges must
   be restarted: `build_auth_delegation_dispatcher` (Helena→Rafael) and
   `build_dossier_delegation_dispatcher` (worker dossier), which call the gate at
   `src/maezo/runtime/agent_runtime/a2a_composition.py:571` and `:710`.
5. **Verify the overlap.** New envelopes are signed under the new key; envelopes signed under the
   prior key still verify. Both keys are in the trusted keyset under their own derived `key_id`s
   (`build_verification_keyset`, `envelope_signing.py:144-169`).
6. **Deprovision the prior key within 7 days.** Delete
   `MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__<TENANT>` and restart the edge processes again. **This step is
   not optional and its deadline is not advisory** — see §4.3.

### 4.3 How long the grace window actually lasts (two independent bounds — know which is which)

**The keyset has no clock.** `build_verification_keyset` trusts the prior key **iff the prior
variable is provisioned** (`envelope_signing.py:166-168`). Nothing expires it. The grace window is
open for exactly as long as an operator leaves that variable in place — a week, or a year.

**`MAX_SIGNATURE_AGE` is the independent time bound**, and it is **7 days**
(`envelope_signing.py:71`; pinned with its accept/reject boundary by
`tests/unit/a2a/test_keyset.py:489-494`). The verifier rejects any signature older than that
regardless of which key signed it, and regardless of `deadline` — `deadline` is sender-chosen, so it
cannot bound anything on its own (ADR-0039 §4.3 item 2).

The two compose as follows, and the distinction is the whole operational point:

- **Deprovisioning the prior key ends the window immediately.** It is the operator's instrument.
- **`MAX_SIGNATURE_AGE` bounds only how old an individual signature may be.** It does **not** expire
  the key. A key left in the prior slot for a year keeps accepting freshly-minted signatures made
  with it for that entire year.

**So the security property depends on step 6 being executed on time.** Leaving the prior variable
provisioned past the 7-day cadence is not a harmless slip: **the grace window is also a forgery
window.** Anyone holding the superseded key material can mint accepted envelopes for as long as the
variable remains. Treat an overdue prior-key deprovision as a live exposure, not as housekeeping.

### 4.4 Revocation / suspected key compromise — the `replay_epoch` bump

Removing the compromised `key_id` from the keyset is **not sufficient** if the compromise is severe
enough that the attacker could forge under a *still-trusted* key from the same epoch. ADR-0039 §4.3
requires both actions in the same response:

1. **Deprovision the compromised key** — clear whichever variable holds it (active or prior) and, if
   it was the active one, install a replacement per §4.2.
2. **Bump `replay_epoch`.** An epoch bump hard-rejects **every** prior-epoch signature regardless of
   key validity — the blunt "trust nothing signed before now" instrument. It dominates the key grace
   window: a prior-key signature is rejected by the epoch bump even while its key is still
   provisioned.

**Zero grace is the default, and it is the incident semantics.** A verifier built without an explicit
`prior_epoch_grace_until` rejects the immediately-prior epoch outright (`_epoch_accepted`,
`envelope_signing.py:321-330`). Owner decision 5 permits a **short** prior-epoch grace to avoid mass
rejection of in-flight envelopes during a *planned* bump — but during an incident you want the
default. A grace window is a bounded, deliberate replay window; do not open one while responding to a
suspected compromise.

> ⚠️ **GAP — the epoch bump has no operator interface today.** Both composition roots pass
> `current_epoch=DEFAULT_REPLAY_EPOCH` as a **hardcoded constant**
> (`a2a_composition.py:239`, `:244`), and neither passes `prior_epoch_grace_until` at all. The
> mechanism is built and tested, but **bumping the epoch currently requires a code change and a
> redeploy** — it is not a configuration action an on-call responder can take. Until an env-sourced
> epoch lands, the executable incident response is step 1 (deprovision + restart) alone, and the
> epoch bump must be raised as a code change. Do not write an incident plan that assumes an operator
> can bump the epoch from configuration.

### 4.5 Fail-closed behaviour to expect (this is the gate working, not an outage to route around)

`_require_envelope_signing_or_fail_closed` (`a2a_composition.py:195`) refuses to compose rather than
returning a degraded edge:

- **Active key absent + production runtime mode** → `RuntimeError` at composition. The
  `MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` opt-out is **ignored** in production; it is dev-local only
  (ADR-0039 §Decisoes do dono, row 6).
- **Active key absent + non-production + no opt-out** → also raises. There is no silent downgrade to
  unsigned, even in dev.
- **A degenerate (too short / whitespace-only) key in EITHER slot** → `EnvelopeSignatureError` at
  composition. The `MIN_SIGNING_KEY_BYTES` floor applies to the prior slot exactly as to the active
  one, so a misprovisioned rotation variable fails loudly instead of quietly installing a
  brute-forceable key as trusted for the whole grace window.

If a rotation causes a composition refusal, **fix the key material — do not set the opt-out.**

### 4.6 Downstream consequence: the `a2a_idempotency` retention floor

`MAX_SIGNATURE_AGE = 7 days` sets a **floor on durable idempotency retention: `a2a_idempotency` rows
must be retained for at least 7 days** (ADR-0039 §4.3 item 3). The reasoning is that replay protection
for a validly-signed captured envelope comes from the store remembering its `task_id`; if a row is
purged while its envelope's signature is still within the acceptable age, the replay it was
preventing re-opens.

`a2a_idempotency` has **no expiry column and no purge path today**
(`src/maezo/platform/migrations/versions/0003_a2a_idempotency.py`), so this is currently a **prohibition**: do
not introduce a retention or purge policy shorter than 7 days without changing `MAX_SIGNATURE_AGE`
in the same change. **MZO-060/DBA owns choosing the actual retention value against this floor** —
the ADR computes the floor; it does not choose the window.

### 4.7 Still pending (not covered by this section)

`docs/design/A2A-dispatcher-card-signing.md` §7 flags a separate, still-open decision: a new ADR for
T-G service-account/cert identity issuance (who mints it, at what granularity, rotation policy, where
it is verified). That is a hard blocker for that half of the A2A build and is **not** resolved here.
mTLS / service identity is likewise deferred by ADR-0039 §4.7.
