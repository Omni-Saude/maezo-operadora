"""Private h11 peer identity is socket-derived and connection-local."""

import hashlib
import ssl
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID
from uvicorn.protocols.http.h11_impl import H11Protocol

from maezo.gateway.communications.identity_owner import (
    IdentityMismatchOwner,
    IdentityMismatchRevocation,
)
from maezo.portal.api.identity_owner import create_identity_owner_app
from maezo.portal.api.identity_peer import (
    IdentityPeer,
    IdentityPeerH11Protocol,
    _PeerApplication,
    identity_peer,
    private_uvicorn_options,
)
from maezo.portal.api.postgres import PostgresIdentityStore

NOW = datetime.now(UTC)


def certificate(*, expired: bool = False) -> tuple[bytes, str, datetime, datetime]:
    key = ec.generate_private_key(ec.SECP256R1())
    before = NOW - timedelta(days=2 if expired else 1)
    after = NOW - timedelta(days=1) if expired else NOW + timedelta(days=1)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "private-requester")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(before)
        .not_valid_after(after)
        .sign(key, hashes.SHA256())
    )
    raw = cert.public_bytes(Encoding.DER)
    spki = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return raw, hashlib.sha256(spki).hexdigest(), before, after


class Transport:
    def __init__(self, raw: bytes | None, *, required: bool = True) -> None:
        self.closed = False
        self.tls = (
            None
            if raw is None
            else SimpleNamespace(
                context=SimpleNamespace(verify_mode=ssl.CERT_REQUIRED if required else ssl.CERT_OPTIONAL),
                getpeercert=lambda *, binary_form: raw,
            )
        )

    def get_extra_info(self, name: str):
        return self.tls if name == "ssl_object" else None

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_protocol_binds_distinct_socket_certificates_without_header_authority(monkeypatch):
    connected = []
    monkeypatch.setattr(
        H11Protocol, "connection_made", lambda protocol, transport: connected.append(protocol.app)
    )
    peers = []

    async def target(scope, receive, send):
        peers.append(identity_peer(scope))

    for _ in range(2):
        raw, expected, _, _ = certificate()
        protocol = object.__new__(IdentityPeerH11Protocol)
        protocol.app = target
        protocol.connection_made(Transport(raw))
        wrapped = connected[-1]
        await wrapped(
            {
                "type": "http",
                "headers": [(b"x-maezo-identity-peer", b"forged")],
                "extensions": {},
            },
            lambda: None,
            lambda message: None,
        )
        assert peers[-1].spki_sha256 == expected
    assert peers[0].spki_sha256 != peers[1].spki_sha256


@pytest.mark.parametrize("kind", ["missing", "optional", "expired"])
def test_protocol_closes_connection_without_current_required_peer_certificate(monkeypatch, kind):
    connected = []
    monkeypatch.setattr(H11Protocol, "connection_made", lambda protocol, transport: connected.append(True))
    raw = None if kind == "missing" else certificate(expired=kind == "expired")[0]
    transport = Transport(raw, required=kind != "optional")
    protocol = object.__new__(IdentityPeerH11Protocol)
    protocol.app = object()
    protocol.connection_made(transport)
    assert transport.closed
    assert connected == []


@pytest.mark.asyncio
async def test_conflicting_scope_descriptor_is_refused_before_application():
    raw, spki, before, after = certificate()
    peer = IdentityPeer(hashlib.sha256(raw).hexdigest(), spki, before, after)
    called = False
    sent = []

    async def target(scope, receive, send):
        nonlocal called
        called = True

    wrapped = _PeerApplication(target, peer)

    async def send(message):
        sent.append(message)

    await wrapped(
        {"type": "http", "extensions": {"maezo.identity-peer.v1": "forged"}},
        lambda: None,
        send,
    )
    assert not called
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_private_app_exposes_only_conditional_owner_route():
    raw, spki, before, after = certificate()
    peer = IdentityPeer(hashlib.sha256(raw).hexdigest(), spki, before, after)
    store = object.__new__(PostgresIdentityStore)
    store.tenant = "tenant"

    async def conditional(*args, **kwargs):
        return "revoked"

    store.revoke_mismatched_session = conditional
    owner = IdentityMismatchOwner(
        store,
        tenant="tenant",
        identity_source_ref="identity-source",
        requester_spki_sha256=spki,
        requester_valid_until=after,
        seconds=5,
        clock=lambda: NOW + timedelta(seconds=1),
    )
    app = _PeerApplication(create_identity_owner_app(owner), peer)
    command = IdentityMismatchRevocation(
        schema="identity-mismatch-revocation.v1",
        request_id="request-0123456789abcdef",
        identity_source_ref="identity-source",
        tenant="tenant",
        session_hash="a" * 64,
        observed_session_digest="b" * 64,
        observed_membership_digest="c" * 64,
        observed_at=NOW,
        valid_until=NOW + timedelta(seconds=5),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://owner") as client:
        response = await client.post(
            "/internal/v1/identity/revoke-mismatched-session",
            content=command.model_dump_json(by_alias=True),
            headers={"x-maezo-identity-peer": "forged", "content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert (await client.get("/openapi.json")).status_code == 404


def test_private_listener_options_require_mtls_and_no_proxy_or_access_log():
    value = private_uvicorn_options(
        ssl_keyfile="server-key.pem",
        ssl_certfile="server-cert.pem",
        ssl_ca_certs="requester-ca.pem",
        timeout_keep_alive=5,
    )
    assert value["http"] is IdentityPeerH11Protocol
    assert value["ssl_cert_reqs"] == ssl.CERT_REQUIRED
    assert value["proxy_headers"] is False
    assert value["access_log"] is False
