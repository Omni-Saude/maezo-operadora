"""Private owner correlation and actual conditional-store call boundary."""

import hashlib
import ssl
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID

from maezo.gateway.communications.identity_owner import (
    IdentityMismatchOwner,
    IdentityMismatchResult,
    IdentityMismatchRevocation,
    IdentityOwnerClient,
    request_digest,
)
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.postgres import PostgresIdentityStore

NOW = datetime.now(UTC)
HASH = "a" * 64


def request() -> IdentityMismatchRevocation:
    return IdentityMismatchRevocation(
        schema="identity-mismatch-revocation.v1",
        request_id="request-0123456789abcdef",
        identity_source_ref="identity-source",
        tenant="tenant",
        session_hash=HASH,
        observed_session_digest="b" * 64,
        observed_membership_digest="c" * 64,
        observed_at=NOW,
        valid_until=NOW + timedelta(seconds=5),
    )


def owner() -> tuple[IdentityMismatchOwner, AsyncMock]:
    store = object.__new__(PostgresIdentityStore)
    store.tenant = "tenant"
    conditional = AsyncMock(return_value="revoked")
    store.revoke_mismatched_session = conditional
    value = IdentityMismatchOwner(
        store,
        tenant="tenant",
        identity_source_ref="identity-source",
        requester_spki_sha256=HASH,
        requester_valid_until=NOW + timedelta(minutes=1),
        seconds=5,
        clock=lambda: NOW + timedelta(seconds=1),
    )
    return value, conditional


@pytest.mark.asyncio
async def test_owner_correlates_terminal_conditional_revocation():
    value, conditional = owner()
    command = request()
    result = await value.revoke(
        command,
        peer_spki_sha256=HASH,
        purpose="identity-mismatch-revocation.v1",
    )
    assert result.request_id == command.request_id
    assert result.request_digest == request_digest(command)
    assert result.disposition == "revoked"
    conditional.assert_awaited_once_with(
        HASH,
        expected_session_digest="b" * 64,
        expected_membership_digest="c" * 64,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["peer", "purpose", "source", "tenant", "expired"])
async def test_owner_refuses_uninstalled_or_expired_request_before_store(change):
    value, conditional = owner()
    command = request()
    peer, purpose = HASH, "identity-mismatch-revocation.v1"
    if change == "peer":
        peer = "d" * 64
    elif change == "purpose":
        purpose = "other"
    elif change == "source":
        command = command.model_copy(update={"identity_source_ref": "other-source"})
    elif change == "tenant":
        command = command.model_copy(update={"tenant": "other-tenant"})
    else:
        value.clock = lambda: NOW + timedelta(seconds=6)
    with pytest.raises(AuthenticationError):
        await value.revoke(command, peer_spki_sha256=peer, purpose=purpose)
    conditional.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_client_verifies_socket_peer_and_exact_response_correlation():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "identity-owner")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    der = certificate.public_bytes(Encoding.DER)
    pin = hashlib.sha256(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()

    def handle(http_request: httpx.Request) -> httpx.Response:
        command = IdentityMismatchRevocation.model_validate_json(http_request.content)
        assert http_request.url.path == "/internal/v1/identity/revoke-mismatched-session"
        assert "maezo-purpose" not in http_request.headers
        result = IdentityMismatchResult(
            schema="identity-mismatch-revocation-result.v1",
            request_id=command.request_id,
            request_digest=request_digest(command),
            identity_source_ref=command.identity_source_ref,
            disposition="revoked",
            observed_at=NOW + timedelta(seconds=1),
            valid_until=command.valid_until,
        )
        peer = SimpleNamespace(
            get_extra_info=lambda name: (
                SimpleNamespace(getpeercert=lambda *, binary_form: der) if name == "ssl_object" else None
            )
        )
        return httpx.Response(
            200,
            content=result.model_dump_json(by_alias=True),
            headers={"content-type": "application/json", "cache-control": "no-store"},
            extensions={"network_stream": peer},
        )

    context = ssl.create_default_context()
    client = IdentityOwnerClient(
        origin="https://identity-owner.internal",
        tenant="tenant",
        identity_source_ref="identity-source",
        server_spki_sha256=pin,
        tls_context=context,
        valid_until=NOW + timedelta(minutes=1),
        seconds=5,
        transport=httpx.MockTransport(handle),
        clock=lambda: NOW + timedelta(seconds=1),
    )
    result = await client.revoke(
        session_hash=HASH,
        observed_session_digest="b" * 64,
        observed_membership_digest="c" * 64,
        observed_at=NOW,
    )
    assert result.disposition == "revoked"
    await client.close()
