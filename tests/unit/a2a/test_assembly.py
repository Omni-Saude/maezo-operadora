"""Unit tests for maezo.a2a.assembly — the Card-signing seam + `build_dispatcher` (ADR-0003/0007).

This suite was missing entirely from the W1/W2 build waves (the design doc's own W2 test list named
`test_assembly.py`, but it was never created) — T2.4 A2A W4 fills that gap, focused on the T-G
signed-Card ENFORCEMENT proof: `build_dispatcher(..., verifier=...)` is the fail-closed chokepoint a
production composition (`runtime.agent_runtime.a2a_composition.build_auth_delegation_dispatcher`)
relies on. The underlying `A2ARegistry(verifier=...)` gate itself was already fully unit-tested in
W1's `test_card_signing.py`; this file proves the ASSEMBLY-level wiring (env-var key resolution +
`build_dispatcher`'s own verifier plumbing) so that an unsigned/tampered/wrong-key Card can never
even produce a `DelegationDispatcher` object — "cannot dispatch" is true by construction, since
there is nothing to call `.delegate()` on.
"""

from __future__ import annotations

import pytest

from maezo.a2a import (
    CARD_SIGNING_KEY_ENV_VAR,
    AgentCard,
    CardSignatureError,
    CardSigner,
    FactProducer,
    HandlerOutput,
    RegistryError,
    build_dispatcher,
    card_signer_from_key,
    card_signing_key_from_env,
)
from maezo.tools.workers.harness import FakeAuditSink

from .fakes import FakeAgentHandler, RecordingProducer

_KEY = b"test-assembly-signing-key-0123456789"
_OTHER_KEY = b"a-completely-different-assembly-key"
_TENANT = "amh"


def _card(*, tenant: str = _TENANT, signature: str | None = None) -> AgentCard:
    return AgentCard(
        agent_id="rafael",
        version="v0",
        tenant=tenant,
        security_zone="phi",
        accepted_task_types=frozenset({"authorization.analyze"}),
        signature=signature,
    )


def _dispatcher_kwargs(cards: list[AgentCard]) -> dict[str, object]:
    return {
        "tenant": _TENANT,
        "cards": cards,
        "handlers": {"rafael": FakeAgentHandler()},
        "audit": FakeAuditSink(),
        "facts": FactProducer(RecordingProducer()),
    }


# --- card_signing_key_from_env -----------------------------------------------------------------


def test_card_signing_key_from_env_absent_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CARD_SIGNING_KEY_ENV_VAR, raising=False)
    assert card_signing_key_from_env() is None


def test_card_signing_key_from_env_whitespace_only_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CARD_SIGNING_KEY_ENV_VAR, "   \n\t  ")
    assert card_signing_key_from_env() is None


def test_card_signing_key_from_env_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CARD_SIGNING_KEY_ENV_VAR, "  a-real-looking-signing-key-value  \n")
    assert card_signing_key_from_env() == b"a-real-looking-signing-key-value"


# --- card_signer_from_key: the absent->None vs present->enforce asymmetry ----------------------


def test_card_signer_from_key_none_when_absent() -> None:
    assert card_signer_from_key(None) is None
    assert card_signer_from_key(b"") is None


def test_card_signer_from_key_builds_signer_when_present() -> None:
    signer = card_signer_from_key(_KEY)
    assert isinstance(signer, CardSigner)


def test_card_signer_from_key_raises_on_present_but_garbage_key() -> None:
    """PRESENT-and-garbage (too short) MUST raise, never silently fall back to unsigned — the W1
    asymmetry this composition flip depends on."""
    with pytest.raises(CardSignatureError):
        card_signer_from_key(b"short")


# --- build_dispatcher(verifier=...): T-G enforcement fail-closed proof --------------------------


async def test_build_dispatcher_without_verifier_admits_unsigned_card_dev_path() -> None:
    """Dev/test path (unchanged, W1): no verifier -> unsigned Cards are admitted and dispatch."""
    dispatcher = build_dispatcher(**_dispatcher_kwargs([_card(signature=None)]))  # type: ignore[arg-type]
    result = await dispatcher.delegate(_envelope())
    assert result.success


async def test_build_dispatcher_with_verifier_rejects_unsigned_card() -> None:
    """T-G ENFORCEMENT: with a verifier injected, an UNSIGNED card is refused at `register()` —
    `build_dispatcher` raises before a dispatcher object is ever returned (fail-closed: there is
    nothing to `.delegate()` against)."""
    verifier = CardSigner(_KEY)
    with pytest.raises(CardSignatureError):
        build_dispatcher(verifier=verifier, **_dispatcher_kwargs([_card(signature=None)]))  # type: ignore[arg-type]


async def test_build_dispatcher_with_verifier_rejects_tampered_card() -> None:
    """T-G ENFORCEMENT: a validly-signed card, then TAMPERED (a field changed after signing), is
    refused — the signature no longer matches the (changed) canonical payload."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    tampered = AgentCard(
        agent_id=signed.agent_id,
        version=signed.version,
        tenant=signed.tenant,
        security_zone=signed.security_zone,
        accepted_task_types=frozenset({"authorization.analyze", "clinical.decision"}),  # tampered
        signature=signed.signature,
    )
    with pytest.raises(CardSignatureError):
        build_dispatcher(verifier=signer, **_dispatcher_kwargs([tampered]))  # type: ignore[arg-type]


async def test_build_dispatcher_with_verifier_rejects_wrong_key_card() -> None:
    """T-G ENFORCEMENT: a card signed by a DIFFERENT key than the assembly's verifier is refused
    (cross-key rejection)."""
    foreign_signed = CardSigner(_OTHER_KEY).sign(_card())
    verifier = CardSigner(_KEY)
    with pytest.raises(CardSignatureError):
        build_dispatcher(verifier=verifier, **_dispatcher_kwargs([foreign_signed]))  # type: ignore[arg-type]


async def test_build_dispatcher_with_verifier_admits_and_dispatches_validly_signed_card() -> None:
    """T-G ENFORCEMENT, the positive case: a card validly signed under the SAME key the verifier
    holds is admitted, and the resulting dispatcher actually routes/dispatches a delegation."""
    signer = CardSigner(_KEY)
    signed = signer.sign(_card())
    dispatcher = build_dispatcher(verifier=signer, **_dispatcher_kwargs([signed]))  # type: ignore[arg-type]

    result = await dispatcher.delegate(_envelope())

    assert result.success
    assert result.output_ref == "fhir://Task/out"


def _envelope() -> object:
    from maezo.a2a import Budget, DelegationEnvelope

    return DelegationEnvelope.root(
        task_id="assembly-t1",
        task_type="authorization.analyze",
        origin="helena",
        target="rafael",
        tenant=_TENANT,
        budget=Budget(tokens=100, time_ms=100),
        payload_ref="fhir://Patient/abc",
    )


# --- HandlerOutput sanity (imported above; keeps the import used/typed) -------------------------


def test_handler_output_import_is_the_real_dataclass() -> None:
    out = HandlerOutput(output_ref="fhir://Task/x")
    assert out.output_ref == "fhir://Task/x"


def test_registry_error_reexported() -> None:
    assert issubclass(CardSignatureError, RegistryError)
