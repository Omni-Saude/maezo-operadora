# Drill: A2A Agent Card Key Rotation (dev environment, end-to-end)

**Audience:** Security, platform, on-call
**Last updated:** 2026-08-11
**Cadence:** Quarterly (or after any suspected signing-key compromise)

**Purpose:** Rotate the Agent Card HMAC signing key end-to-end in a **dev** environment, proving
(a) Cards signed under the old key stop verifying once the registry is rebuilt with the new key
(expected cutover behavior, not a bug — see [`a2a-key-rotation.md`](../a2a-key-rotation.md) §3),
and (b) Cards re-derived and re-signed under the new key verify successfully. See
[`a2a-key-rotation.md`](../a2a-key-rotation.md) for what this mechanism is and is not.

> **Key custody: this drill never touches a real key.** `MAEZO_A2A_CARD_SIGNING_KEY` is set to a
> synthetic, hard-coded dev value in every step below. Real key generation, storage, and
> provisioning into a vault/KMS is owner/KMS-only and is explicitly **not** exercised here — see
> the honest marker at the end.

---

## Preconditions

- `uv sync --locked --extra dev` done.
- No running engine/compose stack required — this drill is entirely in-process Python
  (`A2ARegistry`, `CardSigner`, `build_agent_cards` are pure objects; no Kafka/DB/BPMN
  involved, per `src/maezo/a2a/assembly.py`'s own module docstring: "engine-free for
  `build_agent_cards`").
- Re-verify the env var name before running, in case it has changed:
  ```bash
  grep -n "CARD_SIGNING_KEY_ENV_VAR" src/maezo/a2a/assembly.py
  # expect: CARD_SIGNING_KEY_ENV_VAR = "MAEZO_A2A_CARD_SIGNING_KEY"
  ```
- Real agent ids available for this drill (`ls spec/agents/` — excluding `_template`): `andre`,
  `beatriz`, `carolina`, `fernando`, `gustavo`, `helena`, `lucas`, `marina`, `rafael`,
  `valentina`. The script below uses `helena` and `rafael`; substitute any that exist at drill
  time.

## Step-by-step

**1. Derive and sign Cards under an initial ("old") synthetic key; register with verification ON.**

```bash
uv run python - <<'PY'
import os
os.environ["MAEZO_A2A_CARD_SIGNING_KEY"] = "dev-drill-old-key-0123456789ab"  # synthetic, >=16 bytes

from maezo.a2a.assembly import build_agent_cards, card_signer_from_key, card_signing_key_from_env
from maezo.a2a.registry import A2ARegistry

old_signer = card_signer_from_key(card_signing_key_from_env())
assert old_signer is not None, "expected a real CardSigner from a set env var"

old_cards = build_agent_cards("amh", ["helena", "rafael"], signer=old_signer)
registry = A2ARegistry(verifier=old_signer)
for card in old_cards:
    registry.register(card)
print(f"registered {len(old_cards)} card(s) signed under the OLD key")
PY
```

**2. Rotate: provision a new synthetic value under the same env var** (stands in for "the
custody owner rotated the vault/KMS secret"), and confirm an OLD-key-signed Card fails
re-registration against a registry verifying under the NEW key:

```bash
uv run python - <<'PY'
import os
os.environ["MAEZO_A2A_CARD_SIGNING_KEY"] = "dev-drill-old-key-0123456789ab"
from maezo.a2a.assembly import build_agent_cards, card_signer_from_key, card_signing_key_from_env
from maezo.a2a.registry import A2ARegistry
from maezo.a2a.card import CardSignatureError

old_signer = card_signer_from_key(card_signing_key_from_env())
old_cards = build_agent_cards("amh", ["helena", "rafael"], signer=old_signer)

os.environ["MAEZO_A2A_CARD_SIGNING_KEY"] = "dev-drill-NEW-key-abcdef012345"
new_signer = card_signer_from_key(card_signing_key_from_env())
new_registry = A2ARegistry(verifier=new_signer)

failures = 0
for card in old_cards:
    try:
        new_registry.register(card)
    except CardSignatureError as exc:
        failures += 1
        print(f"EXPECTED failure re-registering old-key card {card.agent_id}: {exc}")
assert failures == len(old_cards), "an old-key-signed card verified under the new key — investigate"
print("confirmed: old-key Cards do not verify under the new key (expected cutover behavior)")
PY
```

**3. Complete the cutover: re-derive Cards under the NEW key and confirm clean registration.**

```bash
uv run python - <<'PY'
import os
os.environ["MAEZO_A2A_CARD_SIGNING_KEY"] = "dev-drill-NEW-key-abcdef012345"
from maezo.a2a.assembly import build_agent_cards, card_signer_from_key, card_signing_key_from_env
from maezo.a2a.registry import A2ARegistry

new_signer = card_signer_from_key(card_signing_key_from_env())
new_cards = build_agent_cards("amh", ["helena", "rafael"], signer=new_signer)
registry = A2ARegistry(verifier=new_signer)
for card in new_cards:
    registry.register(card)
print(f"registered {len(new_cards)} card(s) signed under the NEW key -- rotation complete")
PY
```

**4. Confirm the `verifier=None` fallback is understood, not relied on by accident.** Run the same
registration with no verifier at all, and confirm even a garbage "signature" is accepted — this
is the Phase-0/dev default, and the drill's point is to make sure whoever runs it recognizes it
rather than mistaking it for "rotation isn't needed here":

```bash
uv run python - <<'PY'
from maezo.a2a.assembly import build_agent_cards
from maezo.a2a.registry import A2ARegistry

unsigned_cards = build_agent_cards("amh", ["helena"])  # no signer -> unsigned
registry = A2ARegistry()  # verifier=None (default)
registry.register(unsigned_cards[0])  # succeeds -- no signature check at all
print("confirmed: A2ARegistry(verifier=None) accepts unsigned Cards by design (Phase-0/dev only)")
PY
```

## Expected evidence

- Step 2's output: `failures == len(old_cards)` — every old-key Card is rejected by the
  new-key-verifying registry (`CardSignatureError`, one line per card).
- Step 3's output: all new-key Cards register with no exception.
- Step 4's output: confirms the no-verifier fallback exists and behaves as documented — record
  this so the drill also serves as a check that nobody has quietly turned verification on/off
  without updating this expectation.
- Record the drill's date, operator, and console output wherever the owning team tracks drill
  evidence.

## Abort criteria

- Any old-key Card verifies successfully against the new-key registry in step 2 → **STOP.** This
  means the rotation did not actually take effect (e.g. a cached signer, an env var read before
  the rotation) — do not proceed to "declare rotation complete" against production key material
  until this is understood and fixed.
- A new-key Card fails to register in step 3 → **STOP** and check `MIN_SIGNING_KEY_BYTES` (16
  bytes post-whitespace-strip — `src/maezo/a2a/signing.py`) and whitespace handling on the new
  key value before assuming the mechanism itself is broken.

## Honest markers — what this drill does NOT cover

- **No real vault/KMS custody exercised.** Real key generation/storage/provisioning is
  owner/KMS-only (per `docs/design/A2A-dispatcher-card-signing.md` §6: "blocked secret, §6.2").
  This drill only exercises the code-side consumption of whatever value is in
  `MAEZO_A2A_CARD_SIGNING_KEY` — it says nothing about how that value gets there safely in a real
  environment.
- **No dual-key/grace-period rotation** — as documented in
  [`a2a-key-rotation.md`](../a2a-key-rotation.md) §3, the current mechanism is single-key; this
  drill's step 2 deliberately demonstrates the all-at-once cutover, not a gradual rollover,
  because a gradual rollover does not exist in the code to drill.
- **No envelope-signing key rotation** — that mechanism does not exist yet (§4 of
  `a2a-key-rotation.md`); nothing in this drill exercises it.
- **No production A2A registry targeted** — this drill is in-process only; it does not touch any
  running `agent-runtime`/`worker-daemon` deployment, and rotating a real environment's key
  requires restarting every process that builds a `CardSigner` from the env var (see
  `a2a-key-rotation.md` §3, step 2) — not exercised here.
