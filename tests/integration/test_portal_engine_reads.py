"""ROOT-only packaged direct Tomcat mTLS + Q1 BFF acceptance.

Run against the isolated PUBLIC_SYNTHETIC real-engine fixtures. These tests do not
start services, mock an engine, install credentials or skip absent prerequisites.
The private fixture and admission are provided by ROOT's qualified runtime lane;
a local JSON file is never production admission or resource policy authority.
"""

from __future__ import annotations

import json
import os
import ssl
from pathlib import Path

import httpx
import pytest

from maezo.portal.contracts.queues import TaskQueuePage, TaskReadResponse
from tests.support.tls_oracle import (
    expect_pinned_jsse_missing_client_certificate_alert,
    server_authenticated_tls13_context,
)

pytestmark = pytest.mark.integration


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail("Explicit packaged Q2 integration configuration is missing", pytrace=False)
    return value


def private_fixture() -> dict[str, str]:
    # Only a path crosses environment/argv. The isolated fixture contains signed
    # envelopes prepared in the gateway boundary, plus ROOT-issued browser session.
    data = json.loads(Path(required("MAEZO_PORTAL_READ_PACKAGED_FIXTURE_FILE")).read_text())
    if set(data) != {
        "catalog_envelope_file",
        "task_envelope_file",
        "wrong_purpose_envelope_file",
        "bff_session_file",
        "expected_task_id",
    }:
        pytest.fail("Invalid packaged Q2 fixture", pytrace=False)
    return data


def native_tls() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=required("MAEZO_PORTAL_READ_IT_CA_FILE"))
    context.load_cert_chain(
        required("MAEZO_PORTAL_READ_IT_CERT_FILE"), required("MAEZO_PORTAL_READ_IT_KEY_FILE")
    )
    return context


def result(client: httpx.Client, origin: str, operation: str, file: str) -> httpx.Response:
    raw = Path(file).read_bytes()
    assert len(raw) <= 65536
    response = client.post(
        origin + "/maezo-human-read/v1/" + operation,
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert response.headers.get("cache-control") == "no-store"
    return response


def test_packaged_direct_mtls_native_task_and_purpose_separation() -> None:
    fixture = private_fixture()
    origin = required("MAEZO_PORTAL_READ_IT_ORIGIN").rstrip("/")
    assert origin.startswith("https://")
    with httpx.Client(verify=native_tls(), trust_env=False, follow_redirects=False, timeout=10) as client:
        assert result(client, origin, "catalog", fixture["catalog_envelope_file"]).status_code == 200
        task = result(client, origin, "task", fixture["task_envelope_file"])
        assert task.status_code == 200
        body = task.json()
        assert body["value"]["task"]["snapshot"]["task_id"] == fixture["expected_task_id"]
        assert body["value"]["task_continuity"]["claims"]["stage"] == "task"
        refused = result(client, origin, "task", fixture["wrong_purpose_envelope_file"])
        assert refused.status_code == 403
        assert refused.json() == {
            "schema": "portal-engine-read-error.v1",
            "code": "READ_AUTHENTICATION_DENIED",
        }


def test_no_forwarded_certificate_or_plaintext_admission() -> None:
    fixture = private_fixture()
    origin = required("MAEZO_PORTAL_READ_IT_ORIGIN").rstrip("/")
    assert origin.startswith("https://")
    context = server_authenticated_tls13_context(required("MAEZO_PORTAL_READ_IT_CA_FILE"))
    with (
        httpx.Client(verify=context, trust_env=False, follow_redirects=False, timeout=10) as client,
        expect_pinned_jsse_missing_client_certificate_alert(),
    ):
        client.post(
            origin + "/maezo-human-read/v1/task",
            content=Path(fixture["task_envelope_file"]).read_bytes(),
            headers={"Content-Type": "application/json", "X-Forwarded-Client-Cert": "PUBLIC_SYNTHETIC"},
        )


def test_real_q1_bff_queue_and_detail_keep_private_continuity_out_of_browser() -> None:
    fixture = private_fixture()
    origin = required("MAEZO_PORTAL_READ_IT_BFF_ORIGIN").rstrip("/")
    assert origin.startswith("https://")
    secret = Path(fixture["bff_session_file"]).read_text().strip()
    context = ssl.create_default_context(cafile=required("MAEZO_PORTAL_READ_IT_BFF_CA_FILE"))
    with httpx.Client(
        verify=context,
        trust_env=False,
        follow_redirects=False,
        timeout=10,
        headers={"Cookie": "__Host-maezo-session=" + secret},
    ) as client:
        response = client.get(origin + "/api/v1/portal/tasks?queue=team&limit=25")
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "no-store"
        page = TaskQueuePage.model_validate_json(response.content)
        assert any(row.task_id == fixture["expected_task_id"] for row in page.items)
        detail = client.get(origin + "/api/v1/portal/tasks/" + fixture["expected_task_id"])
        assert detail.status_code == 200
        TaskReadResponse.model_validate_json(detail.content)
        for forbidden in (
            b"task_continuity",
            b"authority_continuity",
            b"read_context_id",
            b"native_task_state_digest",
        ):
            assert forbidden not in response.content and forbidden not in detail.content
