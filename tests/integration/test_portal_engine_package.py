"""ADR0049 D5/D7: ROOT-only smoke of the actual Tomcat image and direct mTLS.

This is a packaging/load/TLS proof, separate from AtomicEngineIT transaction tests.
Supply an isolated running image with the human SQL, tenant row and explicit trust
file installed. No engine stub, service start, credential default or missing-config skip.
"""

import os
import ssl
from urllib.parse import urlparse

import httpx
import pytest

from tests.support.tls_oracle import (
    expect_pinned_jsse_missing_client_certificate_alert,
    server_authenticated_tls13_context,
)

pytestmark = pytest.mark.integration


def required(name: str) -> str:
    value = os.environ.get(name)
    assert value, f"explicit isolated package fixture setting missing: {name}"
    return value


def client_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=required("MAEZO_HUMAN_PACKAGE_CA_FILE"))
    context.load_cert_chain(
        required("MAEZO_HUMAN_PACKAGE_CLIENT_CERT"), required("MAEZO_HUMAN_PACKAGE_CLIENT_KEY")
    )
    return context


def endpoint() -> str:
    url = required("MAEZO_HUMAN_PACKAGE_HTTPS_URL")
    assert urlparse(url).scheme == "https"
    return url.rstrip("/") + "/maezo-human/v1/commands"


def test_actual_tomcat_plugin_loaded_after_schema_and_trust_bootstrap() -> None:
    with httpx.Client(verify=client_context(), trust_env=False, follow_redirects=False) as client:
        response = client.post(endpoint(), content=b"{}", headers={"Content-Type": "application/json"})
    # Servlet resolves the successfully started plugin before parsing this invalid envelope.
    # Missing plugin, bad SQL/datasource or failed trust bootstrap returns 503, not 400.
    assert response.status_code == 400
    assert response.json() == {"error": "INVALID_COMMAND"}
    assert response.headers["cache-control"] == "no-store"


def test_actual_tomcat_rejects_client_without_mutual_tls_identity() -> None:
    context = server_authenticated_tls13_context(required("MAEZO_HUMAN_PACKAGE_CA_FILE"))
    with (
        httpx.Client(verify=context, trust_env=False, follow_redirects=False) as client,
        expect_pinned_jsse_missing_client_certificate_alert(),
    ):
        client.post(endpoint(), json={})


def test_plaintext_and_forwarded_identity_headers_do_not_authenticate() -> None:
    url = required("MAEZO_HUMAN_PACKAGE_HTTP_URL")
    assert urlparse(url).scheme == "http"
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        response = client.post(
            url.rstrip("/") + "/maezo-human/v1/commands",
            json={},
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Client-Cert": "forged"},
        )
    assert response.status_code == 403
    assert response.json() == {"error": "AUTHORITY_DENIED"}


def test_missing_client_certificate_is_rejected_by_tls_before_http() -> None:
    """Application 403 alone cannot prove the connector requires client auth."""
    import socket

    url = urlparse(endpoint())
    context = server_authenticated_tls13_context(required("MAEZO_HUMAN_PACKAGE_CA_FILE"))
    with (
        expect_pinned_jsse_missing_client_certificate_alert(),
        socket.create_connection((url.hostname, url.port), timeout=5) as sock,
        context.wrap_socket(sock, server_hostname=url.hostname) as tls,
    ):
        tls.sendall(
            b"POST /maezo-human/v1/commands HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n{}"
        )
        tls.recv(4096)


def test_untrusted_client_certificate_is_rejected_by_tls() -> None:
    import socket

    url = urlparse(endpoint())
    context = ssl.create_default_context(cafile=required("MAEZO_HUMAN_PACKAGE_CA_FILE"))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(
        required("MAEZO_HUMAN_PACKAGE_UNTRUSTED_CERT"), required("MAEZO_HUMAN_PACKAGE_UNTRUSTED_KEY")
    )
    with (
        socket.create_connection((url.hostname, url.port), timeout=5) as sock,
        pytest.raises(ssl.SSLError, match="(?i)(CERTIFICATE_UNKNOWN|UNKNOWN_CA|BAD_CERTIFICATE)"),
        context.wrap_socket(sock, server_hostname=url.hostname) as tls,
    ):
        tls.sendall(
            b"POST /maezo-human/v1/commands HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n{}"
        )
        tls.recv(4096)
