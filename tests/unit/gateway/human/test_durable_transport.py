"""Unit HTTP doubles and real Ed25519 signatures, not mTLS/engine integration."""

import base64
import hashlib
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.unit.gateway.human.test_durable_projection import assignment, receipt_payload, wire

from maezo.gateway.human.credentials import DedicatedHumanCredential, HumanCommandCredentialPartition
from maezo.gateway.human.transport import (
    EngineConflictError,
    EngineUnavailableError,
    HumanTLSIdentity,
    MTLSHumanEngineTransport,
    PartitionedEd25519Signer,
)
from maezo.portal.engine.profile import SigningContext, canonicalize, strict_loads

pytestmark = pytest.mark.asyncio


def signer(**changes):
    scope = assignment().scope
    partition = HumanCommandCredentialPartition(scope)
    credential = DedicatedHumanCredential(scope=scope, key_id="key-human-test", purpose="human-command")
    partition.install(credential)
    key = Ed25519PrivateKey.generate()
    values = dict(
        partition=partition,
        credential=credential,
        context=SigningContext(
            tenant=scope.tenant,
            audience="cib-human",
            workload_ref=scope.workload_ref,
            key_id=credential.key_id,
            max_lifetime_seconds=60,
        ),
        private_key=key,
        valid_from=datetime.now(UTC) - timedelta(minutes=1),
        valid_until=datetime.now(UTC) + timedelta(hours=1),
        envelope_seconds=30,
    )
    values.update(changes)
    return PartitionedEd25519Signer(**values), partition, key


@pytest.mark.parametrize("purpose", ["human-command", "human-receipt"])
async def test_current_signature_matches_d5_canonical_purpose_and_identity(purpose):
    signing, _, key = signer()
    raw = signing.envelope(wire(), purpose=purpose)
    body = strict_loads(raw)
    signature = base64.urlsafe_b64decode(body.pop("signature") + "==")
    key.public_key().verify(signature, canonicalize(body))
    assert body["purpose"] == purpose
    assert body["digest"] == hashlib.sha256(canonicalize(body["command"])).hexdigest()
    if purpose == "human-command":
        assert canonicalize(body["command"]) == wire().canonical
    else:
        assert body["command"]["schema"] == "human-receipt-query.v1"
        assert body["command"]["payload_digest"] == wire().digest
        assert "audit_intent_ref" not in body["command"]


async def test_key_rotation_refuses_old_signer_no_partition_fallback():
    signing, partition, _ = signer()
    partition.install(
        DedicatedHumanCredential(scope=signing.scope, key_id="new-human-key", purpose="human-command")
    )
    with pytest.raises(EngineUnavailableError):
        signing.envelope(wire(), purpose="human-command")


async def test_expired_key_refuses_signing():
    signing, _, _ = signer()
    signing._until = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(EngineUnavailableError):
        signing.envelope(wire(), purpose="human-receipt")


@pytest.mark.parametrize("seconds", [0, -1, True, 1.0, "30", 61])
async def test_signing_validity_requires_explicit_valid_config(seconds):
    with pytest.raises((EngineUnavailableError, RuntimeError)):
        signer(envelope_seconds=seconds)


def transport(monkeypatch, handler):
    signing, _, _ = signer()
    identity = HumanTLSIdentity(signing.scope, Path("/unit/ca"), Path("/unit/cert"), Path("/unit/key"))
    # TLS isn't exercised by this unit double. Real context constructor retains
    # fail-closed CA/cert/key loading; the real mTLS package owns live proof.
    monkeypatch.setattr(HumanTLSIdentity, "context", lambda self: ssl.create_default_context())
    original = httpx.AsyncClient
    options = []

    def client(**kwargs):
        options.append(kwargs)
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    value = MTLSHumanEngineTransport(
        scope=signing.scope,
        endpoint="https://engine.example/human",
        identity=identity,
        signer=signing,
        timeout_seconds=5,
    )
    return value, options


async def test_dispatch_uses_verified_mtls_context_no_proxy_no_redirect(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        body = strict_loads(request.content)
        assert canonicalize(body["command"]) == wire().canonical
        return httpx.Response(200, content=receipt_payload(), headers={"Content-Type": "application/json"})

    adapter, options = transport(monkeypatch, handler)
    assert await adapter.dispatch(wire()) == receipt_payload()
    assert seen[0].url == "https://engine.example/human/v1/commands"
    assert options[0]["verify"].verify_mode == ssl.CERT_REQUIRED
    assert options[0]["verify"].check_hostname
    assert options[0]["follow_redirects"] is False and options[0]["trust_env"] is False


async def test_receipt_query_is_signed_authenticated_get_no_query_payload(monkeypatch):
    def handler(request):
        assert request.method == "GET" and request.url.query == b"" and not request.content
        encoded = request.headers["Authorization"].removeprefix("Maezo-Human ")
        body = strict_loads(base64.urlsafe_b64decode(encoded + "=" * ((-len(encoded)) % 4)))
        assert body["purpose"] == "human-receipt"
        assert body["command"]["payload_digest"] == wire().digest
        return httpx.Response(404, json={"error": "RECEIPT_NOT_FOUND"})

    adapter, _ = transport(monkeypatch, handler)
    assert await adapter.receipt(wire()) is None


@pytest.mark.parametrize(
    "status,body",
    [
        (202, {"status": "accepted"}),
        (302, {"status": "committed"}),
        (404, {"error": "NOT_FOUND"}),
        (403, {"error": "AUTHORITY_DENIED"}),
        (409, {"error": "UNKNOWN"}),
        (503, {"error": "PRIVATE"}),
        (200, {"status": "committed"}),
    ],
)
async def test_unverified_or_nonreceipt_responses_cannot_claim_execution(monkeypatch, status, body):
    adapter, _ = transport(
        monkeypatch,
        lambda request: httpx.Response(status, json=body, headers={"Location": "https://foreign.example/"}),
    )
    with pytest.raises(EngineUnavailableError):
        await adapter.receipt(wire())


async def test_exact_conflict_is_a_typed_technical_refusal(monkeypatch):
    adapter, _ = transport(
        monkeypatch, lambda request: httpx.Response(409, json={"error": "REVISION_CONFLICT"})
    )
    with pytest.raises(EngineConflictError) as error:
        await adapter.dispatch(wire())
    assert error.value.code == "REVISION_CONFLICT"


async def test_response_body_limit_and_untrusted_mime_refuse(monkeypatch):
    for response in (
        httpx.Response(200, content=b" " * 65537, headers={"Content-Type": "application/json"}),
        httpx.Response(200, content=receipt_payload(), headers={"Content-Type": "text/html"}),
    ):
        with pytest.MonkeyPatch.context() as context:
            adapter, _ = transport(context, lambda request, response=response: response)
            with pytest.raises(EngineUnavailableError):
                await adapter.receipt(wire())


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://engine.example/human",
        "https://u:p@engine.example/human",
        "https://engine.example/human?",
        "https://engine.example/human#",
        "https://engine.example/\n",
    ],
)
async def test_endpoint_must_be_explicit_verified_https(monkeypatch, endpoint):
    signing, _, _ = signer()
    identity = HumanTLSIdentity(signing.scope, Path("/unit/ca"), Path("/unit/cert"), Path("/unit/key"))
    with pytest.raises(EngineUnavailableError):
        MTLSHumanEngineTransport(
            scope=signing.scope, endpoint=endpoint, identity=identity, signer=signing, timeout_seconds=5
        )


async def test_missing_actual_tls_material_refuses_configuration():
    signing, _, _ = signer()
    identity = HumanTLSIdentity(
        signing.scope, Path("/unit/missing-ca"), Path("/unit/cert"), Path("/unit/key")
    )
    with pytest.raises(EngineUnavailableError):
        MTLSHumanEngineTransport(
            scope=signing.scope,
            endpoint="https://engine.example/human",
            identity=identity,
            signer=signing,
            timeout_seconds=5,
        )
