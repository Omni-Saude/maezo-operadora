"""Tests for cryptographic signing of Agent Cards (T2.4 A2A W1 — ADR-0003/0007).

Ported (v2-adapted) from the donor `Maezo-Healthcare-Plan` reference implementation's
`tests/unit/a2a/test_card_signing.py` (22 test functions / ~31 cases, one parametrized x10) as part
of the T2.4 A2A W1 (card-signing) build wave — see `docs/design/A2A-dispatcher-card-signing.md`
§1.2/§5 for the full port rationale and `docs/reports/T2.4-a2a-agent-card-signing-gap.md` for the
original gap this closes: the `AgentCard` had no signature field, no sign/verify — any component
could forge a Card and a (future) dispatcher would trust it blind.

This suite proves the slice's security guarantees:

  1. **sign/verify** — a Card signed with key K verifies with K (and ONLY with K);
  2. **tamper detection** — changing ANY covered field invalidates the signature;
  3. **key fail-closed** — `CardSigner` refuses an empty key (no Card is ever silently signed
     under a default in production); the production path (`signer=`/`verifier=`) never admits an
     unsigned Card;
  4. **constant-time verification** — comparison routes through `hmac.compare_digest`, never a
     short-circuiting `==`;
  5. **registry verifier-gate rejection** — an `A2ARegistry(verifier=...)` refuses to admit an
     unsigned/tampered/wrong-key card, so a (future) dispatcher's `lookup` never sees one;
  6. **cross-tenant isolation** — a signed Card's tenant is part of what's authenticated, and the
     registry never leaks a card across tenants even under a shared verifier key.

Everything here is unit/engine-free: `build_agent_cards` only reads real `agent.yaml` files
(`maezo.agents.AgentLoader`, no Kafka/DB/BPMN engine). The signing key here is an OBVIOUS test
literal (`_KEY`/`_OTHER_KEY` below) — the REAL key is injected from vault/KMS (external/blocked
dependency, see `docs/design/A2A-dispatcher-card-signing.md` §6.2).

**W1 scope note:** the donor's suite also has 4 end-to-end tests that exercise
`DelegationEnvelope`/`build_dispatcher`/`AuditLog`/`FactProducer` — those components are W2 scope
(no dispatcher/envelope/facts/idempotency in W1, per the design's §5 wave split) and do not exist in
v2 yet. Their security property (a verifier-gated admission chokepoint rejects
unsigned/tampered/wrong-key cards before ANYTHING downstream can consult them) is preserved here at
the `A2ARegistry` level instead — see `test_registry_with_verifier_admits_only_signed_cards`,
`test_registry_rejects_tampered_card_under_verifier`, and
`test_registry_rejects_card_signed_with_wrong_key_under_verifier`.
"""

from __future__ import annotations

import dataclasses
import hmac
from hashlib import sha256

import pytest

from maezo.a2a import (
    A2ARegistry,
    AgentCard,
    CardSignatureError,
    CardSigner,
    build_agent_cards,
    card_signer_from_key,
    card_signing_key_from_env,
)
from maezo.a2a.assembly import CARD_SIGNING_KEY_ENV_VAR
from maezo.a2a.card import SIGNATURE_SCHEME
from maezo.a2a.signing import MIN_SIGNING_KEY_BYTES

TENANT = "amh"
OTHER_TENANT = "outra-operadora"
# Obvious test literals — NOT real secrets. The real key is injected from vault/KMS (external
# dependency, blocked for this build wave; see docs/design/A2A-dispatcher-card-signing.md §6.2).
_KEY = b"test-card-signing-key-0123456789"
_OTHER_KEY = b"a-completely-different-signing-key"


def _card(agent_id: str = "rafael", *, tenant: str = TENANT, **kw: object) -> AgentCard:
    base: dict[str, object] = {
        "agent_id": agent_id,
        "version": "abc123def456",
        "tenant": tenant,
        "security_zone": "phi",
        "capabilities": frozenset({"analyze_authorization_request"}),
        "skills": frozenset({"tiss_analysis"}),
        "accepted_task_types": frozenset({"authorization.analyze"}),
        "federation_layer": "L3",
        "endpoint": "https://rafael.amh.internal/a2a",
        "queue_ref": "agents.tasks.rafael",
    }
    base.update(kw)
    return AgentCard(**base)  # type: ignore[arg-type]


# --- guarantee 1: sign and verify (with the right key, and ONLY with it) -----------------


def test_signed_card_verifies_with_same_key() -> None:
    signer = CardSigner(_KEY)
    card = _card()
    assert card.is_signed is False  # a raw Card is NOT signed

    signed = signer.sign(card)
    assert signed.is_signed is True
    assert signed.signature is not None and signed.signature.startswith(f"{SIGNATURE_SCHEME}:")
    assert signer.verify(signed) is True
    # `sign` only changes the signature — everything else on the Card is preserved.
    assert dataclasses.replace(signed, signature=None) == card


def test_signature_is_hmac_sha256_over_canonical_bytes() -> None:
    """The signature is exactly the HMAC-SHA256 hex of the canonical bytes (stable form, ADR-0007)."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    expected = f"{SIGNATURE_SCHEME}:" + hmac.new(_KEY, _card().signing_payload(), sha256).hexdigest()
    assert signed.signature == expected


def test_sign_is_idempotent() -> None:
    """Signing covers `signing_payload` (which EXCLUDES the signature) -> re-signing is stable."""
    signer = CardSigner(_KEY)
    once = signer.sign(_card())
    twice = signer.sign(once)
    assert twice.signature == once.signature
    assert signer.verify(twice) is True


def test_verify_fails_with_wrong_key() -> None:
    signed = CardSigner(_KEY).sign(_card())
    assert CardSigner(_OTHER_KEY).verify(signed) is False


def test_verify_uses_constant_time_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the implementation choice: verification MUST route through `hmac.compare_digest`
    (constant-time), never a short-circuiting `==` that could leak timing information — and both
    operands MUST be bytes (str operands make compare_digest raise TypeError on non-ASCII)."""
    calls: list[tuple[bytes, bytes]] = []
    real_compare_digest = hmac.compare_digest

    def _spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return bool(real_compare_digest(a, b))

    monkeypatch.setattr("maezo.a2a.signing.hmac.compare_digest", _spy)
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    assert signer.verify(signed) is True
    assert calls, "verify() must call hmac.compare_digest for constant-time comparison"
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls), (
        "verify() must compare BYTES operands (str operands raise TypeError on non-ASCII input)"
    )


def test_canonical_bytes_independent_of_set_order() -> None:
    """Reordering capabilities/skills/task types does NOT change the canonical bytes (nor the signature)."""
    a = _card(
        capabilities=frozenset({"x", "y", "z"}),
        accepted_task_types=frozenset({"t.one", "t.two"}),
    )
    b = _card(
        capabilities=frozenset({"z", "y", "x"}),
        accepted_task_types=frozenset({"t.two", "t.one"}),
    )
    assert a.signing_payload() == b.signing_payload()
    signer = CardSigner(_KEY)
    assert signer.sign(a).signature == signer.sign(b).signature


# --- guarantee 2: tamper detection (FAILS without the change) -----------------------------


def test_tampered_card_fails_verification() -> None:
    """Central slice: altering the Card after signing invalidates the signature."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    assert signer.verify(signed) is True

    tampered = dataclasses.replace(signed, tenant="tenant-malicioso")
    assert tampered.is_signed is True  # carries the OLD signature...
    assert signer.verify(tampered) is False  # ...which does not match the new content


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agent_id", "outro_agente"),
        ("version", "0000000deadbeef"),
        ("tenant", "outro-tenant"),
        ("security_zone", "general"),
        ("capabilities", frozenset({"escalate_privilege"})),
        ("skills", frozenset({"exfiltrate"})),
        ("accepted_task_types", frozenset({"authorization.analyze", "payment.approve"})),
        ("federation_layer", "L0"),
        ("endpoint", "https://evil.example/a2a"),
        ("queue_ref", "agents.tasks.attacker"),
    ],
)
def test_every_covered_field_is_authenticated(field: str, value: object) -> None:
    """Every covered field enters the payload: changing any one breaks verification."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    mutated = dataclasses.replace(signed, **{field: value})  # type: ignore[arg-type]
    assert signer.verify(mutated) is False


def test_swapped_signature_from_other_card_fails() -> None:
    """Pasting the valid signature of ANOTHER Card does not fool verification (binds to content)."""
    signer = CardSigner(_KEY)
    rafael = signer.sign(_card("rafael"))
    marina = signer.sign(_card("marina"))
    forged = dataclasses.replace(rafael, signature=marina.signature)
    assert signer.verify(forged) is False


# --- guarantee 3: key fail-closed (no silent unsigned card in production) -----------------


def test_empty_signing_key_raises() -> None:
    """`CardSigner` refuses an empty key — never signs under a default (mirrors the pseudonymizer)."""
    with pytest.raises(CardSignatureError):
        CardSigner(b"")


@pytest.mark.parametrize("key", [b" ", b"   ", b"\n", b"\t\t", b" \n \t "])
def test_whitespace_only_signing_key_raises(key: bytes) -> None:
    """A whitespace-only key is as good as no key: same fail-closed error as empty (R1 finding 1)."""
    with pytest.raises(CardSignatureError):
        CardSigner(key)


@pytest.mark.parametrize(
    "key",
    [
        b"x",
        b"test",
        b"0123456789abcde",  # 15 bytes — one under the floor
        b"   0123456789abcde   ",  # 15 real bytes padded with whitespace — strip-then-measure
    ],
)
def test_too_short_signing_key_raises(key: bytes) -> None:
    """Keys under MIN_SIGNING_KEY_BYTES (16, a 128-bit floor) are refused fail-closed — a
    degenerate key gives no real HMAC security (R1 finding 1)."""
    with pytest.raises(CardSignatureError):
        CardSigner(key)


def test_minimum_length_signing_key_accepted() -> None:
    """Boundary: exactly MIN_SIGNING_KEY_BYTES after strip constructs a working signer."""
    key = b"0123456789abcdef"  # exactly 16 bytes
    assert len(key) == MIN_SIGNING_KEY_BYTES
    signer = CardSigner(key)
    assert signer.verify(signer.sign(_card())) is True


def test_unsigned_card_does_not_verify() -> None:
    """A Card without a signature (`signature is None`) is NEVER treated as verified."""
    assert CardSigner(_KEY).verify(_card()) is False


def test_require_valid_raises_on_unsigned_or_tampered() -> None:
    signer = CardSigner(_KEY)
    with pytest.raises(CardSignatureError):
        signer.require_valid(_card())  # unsigned
    tampered = dataclasses.replace(signer.sign(_card()), endpoint="https://evil/a2a")
    with pytest.raises(CardSignatureError):
        signer.require_valid(tampered)
    # The happy path does NOT raise and returns the Card itself.
    good = signer.sign(_card())
    assert signer.require_valid(good) is good


# --- key injection (vault/KMS seam) + feature-gate on presence ----------------------------


def test_card_signer_from_key_gates_on_key_presence() -> None:
    """No injected key -> None (dev fail-safe, unsigned); a key -> a real CardSigner."""
    assert card_signer_from_key(None) is None
    assert card_signer_from_key(b"") is None
    signer = card_signer_from_key(_KEY)
    assert isinstance(signer, CardSigner)
    assert signer.verify(signer.sign(_card())) is True


def test_card_signing_key_from_env_gates_on_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    """v2-side half of the vault/KMS seam: env var absent/empty -> None; present -> the key bytes."""
    monkeypatch.delenv(CARD_SIGNING_KEY_ENV_VAR, raising=False)
    assert card_signing_key_from_env() is None

    monkeypatch.setenv(CARD_SIGNING_KEY_ENV_VAR, "")
    assert card_signing_key_from_env() is None

    monkeypatch.setenv(CARD_SIGNING_KEY_ENV_VAR, "test-env-injected-signing-key")
    key = card_signing_key_from_env()
    assert key == b"test-env-injected-signing-key"
    signer = card_signer_from_key(key)
    assert isinstance(signer, CardSigner)
    assert signer.verify(signer.sign(_card())) is True


def test_card_signing_key_from_env_normalizes_transport_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1 finding 1, env-seam half: a whitespace-only env value is UNSET (-> None, dev fail-safe,
    never a signer); leading/trailing whitespace (e.g. the trailing newline of a mounted secret
    file) is a transport artifact and is stripped, so the same secret signs identically however it
    was materialized."""
    for blank in (" ", "   ", "\n", "\t", " \n "):
        monkeypatch.setenv(CARD_SIGNING_KEY_ENV_VAR, blank)
        assert card_signing_key_from_env() is None

    monkeypatch.setenv(CARD_SIGNING_KEY_ENV_VAR, "test-env-injected-signing-key\n")
    assert card_signing_key_from_env() == b"test-env-injected-signing-key"


def test_whitespace_key_through_seam_raises_not_silently_unsigned() -> None:
    """A PRESENT-but-garbage key must fail loudly at the seam, never silently downgrade to the
    unsigned dev path: card_signer_from_key treats only None/empty as 'absent' (-> None); a
    whitespace-only or too-short key reaches CardSigner and raises (R1 finding 1)."""
    assert card_signer_from_key(None) is None
    assert card_signer_from_key(b"") is None
    with pytest.raises(CardSignatureError):
        card_signer_from_key(b"   ")
    with pytest.raises(CardSignatureError):
        card_signer_from_key(b"short")


# --- registry as the trust chokepoint (a dispatcher only ever looks up verified cards) ----


def test_registry_with_verifier_admits_only_signed_cards() -> None:
    signer = CardSigner(_KEY)
    reg = A2ARegistry(verifier=signer)
    reg.register(signer.sign(_card("rafael")))
    assert reg.lookup("rafael", tenant=TENANT).agent_id == "rafael"

    with pytest.raises(CardSignatureError):
        reg.register(_card("marina"))  # unsigned -> refused at admission
    with pytest.raises(CardSignatureError):
        reg.register(dataclasses.replace(signer.sign(_card("gustavo")), tenant="x"))  # tampered


def test_registry_without_verifier_is_backward_compatible() -> None:
    """Without a verifier (dev), the registry accepts unsigned Cards (Phase-0 fail-safe)."""
    reg = A2ARegistry()
    reg.register(_card("rafael"))  # does not raise
    assert reg.lookup("rafael", tenant=TENANT).is_signed is False


def test_registry_rejects_tampered_card_under_verifier() -> None:
    """A Card signed-then-tampered never enters the registry a dispatcher would consult."""
    signer = CardSigner(_KEY)
    reg = A2ARegistry(verifier=signer)
    signed = signer.sign(_card("rafael"))
    tampered = dataclasses.replace(signed, accepted_task_types=frozenset({"payment.approve"}))
    with pytest.raises(CardSignatureError):
        reg.register(tampered)
    assert reg.get("rafael", tenant=TENANT) is None


def test_registry_rejects_card_signed_with_wrong_key_under_verifier() -> None:
    """A Card signed by a foreign key is refused by the assembly's verifier (cross-key)."""
    foreign_signed = CardSigner(_OTHER_KEY).sign(_card("rafael"))
    reg = A2ARegistry(verifier=CardSigner(_KEY))
    with pytest.raises(CardSignatureError):
        reg.register(foreign_signed)
    assert reg.get("rafael", tenant=TENANT) is None


# --- guarantee 6: cross-tenant isolation ---------------------------------------------------


def test_cross_tenant_cards_isolated_under_shared_verifier() -> None:
    """Tenant is an authenticated field AND a registry key: no leakage across tenants even when
    both tenants' Cards are signed by the same key."""
    signer = CardSigner(_KEY)
    reg = A2ARegistry(verifier=signer)
    reg.register(signer.sign(_card("rafael", tenant=TENANT)))
    reg.register(signer.sign(_card("rafael", tenant=OTHER_TENANT)))

    assert reg.lookup("rafael", tenant=TENANT).tenant == TENANT
    assert reg.lookup("rafael", tenant=OTHER_TENANT).tenant == OTHER_TENANT
    assert len(reg) == 2

    # Re-tagging a signed Amh card as the OTHER tenant (a forged cross-tenant replay) invalidates
    # the signature — tenant is authenticated, not just a registry-key convenience.
    amh_signed = signer.sign(_card("rafael", tenant=TENANT))
    relabelled = dataclasses.replace(amh_signed, tenant=OTHER_TENANT)
    assert signer.verify(relabelled) is False
    with pytest.raises(CardSignatureError):
        reg.register(relabelled)


# --- from_definition / build_agent_cards: signs at construction (version = definition hash) -


def test_build_agent_cards_signs_when_signer_injected() -> None:
    """With `signer`, every Card derived from the real agent.yaml comes back signed over its
    canonical form."""
    signer = CardSigner(_KEY)
    cards = build_agent_cards(TENANT, ["rafael", "marina"], signer=signer)
    assert cards, "expected at least one card"
    for card in cards:
        assert card.is_signed is True
        assert signer.verify(card) is True
        # `version` (the Agent Definition's content hash) is part of what was signed: tampering
        # with it invalidates the signature.
        assert signer.verify(dataclasses.replace(card, version="forjada")) is False


def test_build_agent_cards_unsigned_without_signer() -> None:
    """Without a `signer` (dev), Cards come back unsigned — fail-safe path preserved."""
    cards = build_agent_cards(TENANT, ["rafael"])
    assert all(card.is_signed is False for card in cards)


# --- adversarial: stable against truncated-scheme confusion / a future scheme ---------------


def test_unknown_signature_scheme_fails_closed() -> None:
    """A signature tagged with an unknown scheme (a future/forged shape) does NOT verify (fail-closed)."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    # Reuse the same digest but swap the scheme label -> no longer matches `_digest` (scheme v1).
    assert signed.signature is not None
    raw_mac = signed.signature.split(":", 1)[1]
    forged = dataclasses.replace(signed, signature=f"v999:{raw_mac}")
    assert signer.verify(forged) is False


def test_garbage_signature_value_fails_closed() -> None:
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    assert signed.signature is not None
    for junk in ("", "v1:", "v1:deadbeef", "not-a-signature", signed.signature + "0"):
        assert signer.verify(dataclasses.replace(signed, signature=junk)) is False


def test_non_ascii_signature_fails_closed_without_raising() -> None:
    """A non-ASCII signature is definitionally invalid (valid = ASCII `scheme:hexdigest`):
    verify() must return False — NOT leak the TypeError that `hmac.compare_digest` raises on
    non-ASCII str operands (R1 finding 2: a W2 caller catching only CardSignatureError must never
    see an uncaught TypeError). And the registry gate must surface it as CardSignatureError."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    assert signed.signature is not None
    for non_ascii in (
        "v1:déadbeef",  # accented latin inside the hex part
        "assinatura-🔑",  # emoji, no scheme
        "v1:" + "Δ" * 64,  # greek Delta payload, right length-ish
        signed.signature[:-1] + "ÿ",  # valid sig with one non-ASCII trailing char
    ):
        forged = dataclasses.replace(signed, signature=non_ascii)
        assert signer.verify(forged) is False  # returns, never raises

    # The fail-closed chokepoint surfaces it as the documented error type, not TypeError.
    reg = A2ARegistry(verifier=signer)
    with pytest.raises(CardSignatureError):
        reg.register(dataclasses.replace(signed, signature="v1:🚨forged🚨"))


#: What a deserializer drops into `AgentCard.signature` when it rebuilds a Card off a wire. The
#: field is typed `str | None` and validated nowhere, so each of these is reachable without a single
#: type violation in production code — and none of them has `.encode`.
_NON_STR_SIGNATURES: tuple[tuple[str, object], ...] = (
    ("dict", {"scheme": SIGNATURE_SCHEME, "mac": "deadbeef"}),
    ("int", 12345),
    ("list", [SIGNATURE_SCHEME, "deadbeef"]),
    ("bool", True),
    ("bytes", b"v1:deadbeef"),
)


@pytest.mark.parametrize(("label", "container"), _NON_STR_SIGNATURES)
def test_non_str_signature_container_fails_closed_without_raising(label: str, container: object) -> None:
    """TOTALITY over the signature slot's TYPE, not just its encoding (same family as the envelope
    surface's container guard). `verify` guarded `is None` and then called `.encode("ascii")` in a
    try catching ONLY `UnicodeEncodeError` — so a non-str signature raised `AttributeError` out of a
    method whose docstring promises it never raises. A non-str signature is definitionally not a
    valid signature: it REFUSES."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    forged = dataclasses.replace(signed, signature=container)  # type: ignore[arg-type]
    assert signer.verify(forged) is False, label


@pytest.mark.parametrize(("label", "container"), _NON_STR_SIGNATURES)
def test_registry_surfaces_a_non_str_signature_as_cardsignatureerror(label: str, container: object) -> None:
    """The blast radius: `A2ARegistry.register` is the fail-closed admission chokepoint and its
    contract is `CardSignatureError`. Before the widening it leaked the raw `AttributeError`, so a
    caller catching only `CardSignatureError` (exactly the caller the non-ASCII test above was
    written to protect) saw an unhandled crash instead of a refusal."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    reg = A2ARegistry(verifier=signer)
    with pytest.raises(CardSignatureError):
        reg.register(dataclasses.replace(signed, signature=container))  # type: ignore[arg-type]


def test_the_non_str_refusal_is_the_type_guard_not_a_broken_signature() -> None:
    """NON-VACUITY. The refusals above must come from the container TYPE, not from the values being
    junk. Pinned three ways: (a) the raw dereference the guard now absorbs still raises; (b) a Card
    whose signature is the VALID signature string still verifies True — the widening did not break
    the happy path; (c) that same valid string, tampered by one character, still returns False —
    the ordinary invalid-signature path is untouched."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    assert signed.signature is not None
    # (a) the pre-fix behaviour: a non-str container has no `.encode` at all.
    with pytest.raises(AttributeError, match="'dict' object has no attribute 'encode'"):
        {"scheme": SIGNATURE_SCHEME}.encode("ascii")  # type: ignore[attr-defined]
    # (b) the SAME signature value, in the right container (str), still verifies.
    assert signer.verify(signed) is True
    assert signer.verify(dataclasses.replace(signed, signature=str(signed.signature))) is True
    # (c) and an ordinary tampered str signature is still a plain False, not an error.
    assert signer.verify(dataclasses.replace(signed, signature=signed.signature[:-1] + "0")) is False
