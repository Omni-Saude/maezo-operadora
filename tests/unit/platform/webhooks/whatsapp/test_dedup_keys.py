"""Key derivation for the `wamid` dedup (gap `WEBHOOK-WAMID-DEDUP`, owner decision R-071).

The dedup only works if the key is DETERMINISTIC for the same wamid (or a redelivery is not
recognised) and only stays LGPD-safe if the key is a KEYED pseudonym (or a durable table starts
carrying beneficiary phone numbers). Both halves are pinned here, plus the fact that motivates
the second one: a raw wamid is not opaque.
"""

from __future__ import annotations

import base64

from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX, Pseudonymizer
from maezo.platform.driver_idempotency import inbound_dedup_key, outbound_dedup_key
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.security import hash_message_id
from tests.support.dedup_fakes import FakeDedupRegistry

_TENANT = "amh"
_PHONE = "5511999999999"
#: A real-SHAPED Cloud API message id: `wamid.` + base64 of a protobuf-ish envelope whose first
#: field is the counterpart phone number in ASCII. Not a captured production value — assembled
#: below from `_PHONE` so the test proves the SHAPE, never ships someone's real wamid.
_WAMID = "wamid.HBgNNTUxMTk5OTk5OTk5ORUCABIYFDNBMEE1NkY3RUUxQjA5RkQyNkE5AA=="


def _pseudonymizer() -> Pseudonymizer:
    return Pseudonymizer.from_settings(
        phi_hmac_key="unit-test-key-not-a-secret", production=False, tenant_id=_TENANT
    )


def _guard() -> WhatsAppDedupGuard:
    return WhatsAppDedupGuard(registry=FakeDedupRegistry(), pseudonymizer=_pseudonymizer(), tenant=_TENANT)


def test_a_real_shaped_wamid_leaks_the_phone_number_in_base64() -> None:
    """THE REASON the key is pseudonymized at all: a wamid is NOT an opaque token.

    If this ever stops holding, the `hash_message_id` indirection could be argued away — so the
    fact is asserted rather than asserted-in-a-comment. `_WAMID`'s base64 payload decodes to bytes
    that contain the sender's phone number verbatim, which is why storing a raw wamid in
    `driver_idempotency` would make the LGPD inventory's `SEM_COLUNA_DE_TITULAR` classification
    false.
    """
    payload = _WAMID.split(".", 1)[1]
    decoded = base64.b64decode(payload + "=" * (-len(payload) % 4))
    assert _PHONE.encode() in decoded


def test_the_dedup_key_never_contains_the_wamid_or_the_phone_number() -> None:
    key = _guard().inbound_key(_WAMID)
    assert _WAMID not in key
    assert _PHONE not in key
    # ...and not a fragment of the base64 payload either (a prefix match would still be a leak).
    assert _WAMID.split(".", 1)[1][:16] not in key


def test_the_dedup_key_is_a_keyed_pseudonym_and_says_so() -> None:
    """`hk1_` is the ADR-0035 marker for "keyed HMAC", the same one `hash_phone` stamps."""
    key = _guard().inbound_key(_WAMID)
    assert key.startswith(f"wa:inbound:{_TENANT}:{KEYED_PSEUDONYM_PREFIX}")


def test_the_same_wamid_always_produces_the_same_key() -> None:
    """Determinism IS the dedup: a redelivery must collide with the original claim."""
    assert _guard().inbound_key(_WAMID) == _guard().inbound_key(_WAMID)


def test_different_wamids_produce_different_keys() -> None:
    other = _WAMID.replace("HBgN", "HBgM")
    assert _guard().inbound_key(_WAMID) != _guard().inbound_key(other)


def test_the_same_wamid_under_a_different_tenant_produces_a_different_key() -> None:
    """Tenant isolation: the schema already separates the rows, and so does the key itself."""
    other = WhatsAppDedupGuard(
        registry=FakeDedupRegistry(),
        pseudonymizer=Pseudonymizer.from_settings(
            phi_hmac_key="unit-test-key-not-a-secret", production=False, tenant_id="outro"
        ),
        tenant="outro",
    )
    assert _guard().inbound_key(_WAMID) != other.inbound_key(_WAMID)


def test_the_inbound_and_outbound_legs_never_share_a_key() -> None:
    """Same delivery, two legs: an outbound claim must not look like the inbound one, or sealing
    the send would suppress the very message it answered."""
    guard = _guard()
    assert guard.inbound_key(_WAMID) != guard.outbound_key(_WAMID)


def test_each_send_of_one_turn_gets_its_own_outbound_key() -> None:
    """A turn that ever sent two messages must not have its SECOND suppressed by its own first."""
    guard = _guard()
    assert guard.outbound_key(_WAMID, occurrence=1) != guard.outbound_key(_WAMID, occurrence=2)


def test_the_key_builders_do_no_hashing_of_their_own() -> None:
    """`inbound_dedup_key`/`outbound_dedup_key` are pure formatting over an ALREADY-pseudonymized
    identifier: exactly one place in the repo (`hash_message_id`) may turn a raw wamid into a
    stored value, and it is the one holding the vault key."""
    pseudonym = hash_message_id(_WAMID, _TENANT, _pseudonymizer())
    assert (
        inbound_dedup_key(tenant=_TENANT, message_pseudonym=pseudonym) == f"wa:inbound:{_TENANT}:{pseudonym}"
    )
    assert (
        outbound_dedup_key(tenant=_TENANT, message_pseudonym=pseudonym, occurrence=3)
        == f"wa:outbound:{_TENANT}:{pseudonym}:3"
    )
