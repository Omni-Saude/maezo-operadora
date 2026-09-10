"""Synthetic private TLS stream controls; actual packaged servlet is an integration gate."""

import base64
import ssl
from datetime import timedelta

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from tests.unit.portal.test_queue_cursor_custody import setup

from maezo.gateway.human.queue import CatalogTrustAnchor, ReadRefusalError
from maezo.gateway.human.read_profile import digest, utc, wire
from maezo.gateway.human.read_transport import PortalReadClient
from maezo.portal.engine.profile import canonicalize, strict_loads


def client_fixture(change=None):
    clock, p, partition, cursor, binding = setup()
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic")]))
        .public_key(p.key.public_key())
        .serial_number(1)
        .not_valid_before(clock() - timedelta(days=1))
        .not_valid_after(clock() + timedelta(days=1))
        .sign(p.key, None)
    )

    class Stream:
        def get_extra_info(self, name):
            return self

        def getpeercert(self, binary_form):
            return cert.public_bytes(Encoding.DER)

    anchor = CatalogTrustAnchor(scope=p.scope, catalog_ref="catalog", publisher_ref="publisher")
    artifact = {
        "schema": "portal-read-catalog.v1",
        "catalog_ref": "catalog",
        "publisher_ref": "publisher",
        "entries": [],
        "policies": [],
        "forms": [],
        "deployment_receipt_ref": "deployment",
        "deployment_receipt_digest": "a" * 64,
    }
    captured = []

    async def handler(request):
        body = strict_loads(request.content)
        captured.append(body)
        signature = base64.urlsafe_b64decode(body.pop("signature") + "==")
        p.key.public_key().verify(signature, canonicalize(body))
        inner = body["request"]
        assert body["purpose"] == "portal-task-read"
        assert request.url.path == "/maezo-human-read/v1/catalog"
        until = clock() + timedelta(seconds=10)
        value = {
            "expectation": {
                "anchor": wire(anchor),
                "catalog_revision": "1",
                "catalog_digest": digest(artifact),
                "source_observed_at": utc(clock()),
                "valid_until": utc(until),
            },
            "catalog_artifact_base64": base64.b64encode(canonicalize(artifact)).decode(),
        }
        result = {
            "schema": "portal-engine-read-result.v1",
            "request_digest": digest(inner),
            **{
                k: inner[k]
                for k in (
                    "scope",
                    "engine_name",
                    "database_incarnation",
                    "read_deployment_ref",
                    "read_deployment_digest",
                    "operation",
                )
            },
            "source_observed_at": utc(clock()),
            "valid_until": utc(until),
            "value": value,
        }
        status = 200
        if change == "binding":
            result["database_incarnation"] = "other"
        if change == "digest":
            result["request_digest"] = "f" * 64
        if change == "time":
            result["valid_until"] = utc(clock() + timedelta(minutes=3))
        if change == "extra":
            result["extra"] = "secret"
        if change == "missing":
            del value["catalog_artifact_base64"]
        if change == "revoked":
            p.revoked = True
        if change in (404, 409, 403):
            status = change
            result = {
                "schema": "portal-engine-read-error.v1",
                "code": {
                    404: "RESOURCE_UNAVAILABLE",
                    409: "READ_REVISION_CONFLICT",
                    403: "READ_AUTHENTICATION_DENIED",
                }[change],
            }
        return httpx.Response(
            status,
            content=canonicalize(result),
            headers={"content-type": "application/json", "cache-control": "no-store"},
            extensions={} if change == "forwarded" else {"network_stream": Stream()},
        )

    client = PortalReadClient(
        origin="https://engine.example",
        tls_context=ssl.create_default_context(),
        server_spki_sha256=p.public,
        partition=partition,
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    return client, anchor, captured


@pytest.mark.asyncio
async def test_exact_signed_route_response_binding():
    client, anchor, captured = client_fixture()
    try:
        result = await client.catalog(anchor)
        assert result.value.verified_artifact(anchor).catalog_ref == anchor.catalog_ref
        assert captured[0]["request"]["read_context_id"] == client.read_context_id
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["binding", "digest", "time", "extra", "missing", "revoked", "forwarded", 404, 409, 403]
)
async def test_fail_closed_binding_and_native_taxonomy(change):
    client, anchor, _ = client_fixture(change)
    try:
        with pytest.raises(ReadRefusalError) as exc:
            await client.catalog(anchor)
        assert exc.value.code == {404: "resource_unavailable", 409: "refresh_required"}.get(
            change, "read_dependency_unavailable"
        )
        with pytest.raises(ReadRefusalError):
            await client.catalog(anchor)
    finally:
        await client.close()
