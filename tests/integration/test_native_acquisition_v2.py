"""ROOT-only actual secured native v2 HTTP surface; no service lifecycle or engine mock.

The owner prepares MAEZO_NATIVE_V2_IT_FIXTURE with qualified D/native migrations,
capabilities, dedicated synthetic process instances, observer access and client TLS.
Absence of these prerequisites is a failure when this integration suite is selected.
"""

from __future__ import annotations

import json
import os
import secrets
import ssl
from pathlib import Path

import httpx
import pytest

# `root_fixture`: PRIVATE ROOT-supplied materials; deselected from the global `-m integration`
# lane unless MAEZO_ROOT_FIXTURES=1 (tests/integration/conftest.py). Allowlisted with its reason
# in tests/unit/ci/test_root_fixture_deselection.py.
pytestmark = [pytest.mark.integration, pytest.mark.root_fixture]


def real_client() -> tuple[httpx.Client, dict]:
    configured = os.environ.get("MAEZO_NATIVE_V2_IT_FIXTURE")
    assert configured, "explicit owner-prepared real native v2 fixture required"
    root = Path(configured)
    assert root.is_absolute() and root.resolve() == root
    metadata = json.loads((root / "native-v2-fixture.json").read_bytes())
    assert metadata["protocol"] == "maezo.native-v2-real-fixture.v1"
    assert metadata["fixture_only"] is True
    context = ssl.create_default_context(cafile=root / "ca.crt")
    context.load_cert_chain(root / "client.crt", root / "client.key")
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    return httpx.Client(
        base_url=metadata["base_url"],
        verify=context,
        trust_env=False,
        follow_redirects=False,
        timeout=10,
    ), metadata


@pytest.mark.parametrize("suffix", ["/", "/../operations", ";x=1", "?x=1"])
def test_native_aliases_never_reach_an_effect(suffix: str) -> None:
    connection, metadata = real_client()
    with connection:
        # Use raw URL bytes so HTTPX cannot silently normalize the path tested.
        base = httpx.URL(metadata["base_url"])
        body = dict(metadata["requests"]["alias_denial"])
        body["command_id"] = secrets.token_urlsafe(32)
        request = httpx.Request(
            "POST",
            base.copy_with(raw_path=("/maezo-workload/v2/operations" + suffix).encode()),
            content=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = connection.send(request)
    assert response.status_code in {400, 403, 404}
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "body",
    [
        b'{"protocol":"maezo.engine-operation.v2","protocol":"maezo.engine-operation.v1"}',
        b'{"acquisition_ref":"fabricated"}',
        b'{"lease_revision":true}',
        b'{"lease_revision":9223372036854775808}',
        b'{"lease_revision":1.0}',
    ],
)
def test_native_malformed_profile_refuses_without_native_result(body: bytes) -> None:
    connection, _ = real_client()
    with connection:
        response = connection.post(
            "/maezo-workload/v2/operations",
            content=body,
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code in {400, 403}
    assert response.json() == {
        "protocol": "maezo.engine-refusal.v2",
        "code": "invalid_request" if response.status_code == 400 else "denied",
    }


def test_v2_readiness_has_separate_version_and_authoritative_incarnation() -> None:
    connection, metadata = real_client()
    with connection:
        response = connection.get("/maezo-workload/v2/readiness")
    assert response.status_code == 200
    result = response.json()
    assert result["protocol"] == "maezo.engine-readiness.v2"
    assert result["ready"] is True and result["capabilities"]
    assert result["database_incarnation"] == metadata["scope"]["database_incarnation"]
    assert result["activation_ref"] == metadata["activation_ref"]
    assert response.headers["cache-control"] == "no-store"
