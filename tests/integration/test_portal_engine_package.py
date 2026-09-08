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
    context = ssl.create_default_context(cafile=required("MAEZO_HUMAN_PACKAGE_CA_FILE"))
    with httpx.Client(verify=context, trust_env=False, follow_redirects=False) as client:
        try:
            response = client.post(endpoint(), json={})
        except httpx.TransportError as exc:
            # clientAuth=required may reject during TLS, before the servlet can return 403.
            assert "CERTIFICATE_REQUIRED" in str(exc).upper()
            return
    assert response.status_code == 403
    assert response.json() == {"error": "AUTHORITY_DENIED"}


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
