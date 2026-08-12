# Runbook: A2A Key Rotation — Agent Card signing today, what's pending

**Audience:** Security, platform, on-call
**Last updated:** 2026-08-11
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
4. [What is pending — envelope signing](#4-what-is-pending--envelope-signing)

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

Because the scheme is symmetric HMAC with a single environment-sourced key (no `key-id`/epoch
support yet — see §4), "rotation" today means: **provision a new key value under the same env
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

## 4. What is pending — envelope signing

**Code:** `PLANS.md` (Onda 3 — A2A p/ distribuição)

The **envelope** (the A2A message wrapper carrying tenant/task_id/origin/target/payload_hash/
deadline/budget/chain) is a separate concern from Card signing (§1) and does not yet have a
signing ADR. Quoted from `PLANS.md`'s Onda-3 entry, which is currently being drafted in a
**parallel train**, not this one:

> ADR de assinatura do envelope (digest canônico: tenant, task_id, origin, target, payload_hash,
> deadline, budget, chain; key-id e epoch p/ rotação/replay) → idempotência durável OBRIGATÓRIA
> fora de dev-local → outbox transacional substituindo o no-op de facts → testes orientados a
> ataque (tamper, replay cross-tenant, expiry, chave stale, crash-before-complete) → mTLS/
> identidade quando houver transporte remoto. **Pré-condição DURA para qualquer A2A não-local.**

In plain terms: envelope signing is designed to carry a `key-id` and `epoch` specifically so that
rotation and replay-detection work without the all-or-nothing cutover §3 describes for Card
signing today — but that design is not yet an ADR, and the mechanism does not exist in code yet.
`docs/design/A2A-dispatcher-card-signing.md` §7 separately flags a related, still-open decision:
a **new ADR for T-G service-account/cert identity issuance** (who mints it, at what granularity,
rotation policy, and where it's verified) is a hard blocker for that half of the A2A build and is
explicitly not resolved by this runbook.

**Honest status:** there is no envelope-signing key-rotation procedure to document yet — this
section is a placeholder that will be filled in once the parallel-train ADR lands and the
mechanism is built. Do not treat §3's Card-signing rotation procedure as covering envelopes; they
are different keys, different code paths, and (once built) different rotation semantics.
