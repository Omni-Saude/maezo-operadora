"""Separate ASGI planes with real service/SQL adapter code and synthetic transaction/identity."""

import pytest
from tests.unit.gateway.communications.test_delivery import OTHER, REF, setup
from tests.unit.portal.test_human_session import (
    ORIGIN,
    PREFIX,
    Harness,
    h,
)

__all__ = ["h", "setup"]


# NOT LANDED IN PR-A (recorded, not hidden): the train's
# `test_authorized_two_plane_content_to_actual_inbox_visibility` drove the E05 two-plane HTTP flow
# (PHI content POST -> general-plane publish -> inbox -> PHI read -> 403 after denial) on the
# in-memory `LocalTestIdentityStore` harness. E05's admission contract
# (`PostgresCommunicationAdmission.acquire`/`bind`) requires the API resolver to run on the SAME
# `PostgresIdentityStore`/engine whose transaction re-verifies session and membership, so the
# test was already red on the tip (q2-FENCES.md) and cannot be repaired without a new harness:
# a fake engine exposing `connect()`/`begin()` and answering `SELECT payload FROM
# portal_sessions|portal_memberships` from `test_delivery.Resolver.records()`, plus the
# delivery fake's `lock_session`/communication tables. That harness is a follow-up WP; the
# admission-free assertion of the same test (general-plane OpenAPI never exposes PHI content
# schemas) is kept below, and the E05 behaviours are proven at service level by
# tests/unit/gateway/communications/test_delivery.py and test_admission.py.
def test_general_plane_openapi_never_exposes_phi_content_schemas(h: Harness) -> None:
    general = h.app.openapi()["components"]["schemas"]
    assert "CommunicationContent" not in general and "CommunicationContentSubmission" not in general
    assert "body" not in general["CommunicationSubmission"]["properties"]
    assert "subject" not in general["CommunicationSummary"]["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"body": "PRIVATE_BODY_CANARY"},
        {"tenant": "PRIVATE_BODY_CANARY"},
        {"command_id": REF, "body_ref": REF, "recipient_set_ref": OTHER, "audience": "provider"},
    ],
)
async def test_general_raw_text_and_client_authority_are_rejected(h: Harness, payload):
    response = await h.client.post(
        PREFIX + "/cases/" + REF + "/communications",
        json=payload,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "synthetic"},
    )
    assert response.status_code == 400 and response.json() == {"code": "invalid_request"}
    assert "PRIVATE_BODY_CANARY" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_metadata_query_host_and_missing_dependencies_are_safe(h: Harness):
    for suffix in ["/communications", "/history"]:
        path = PREFIX + "/cases/" + REF + suffix
        response = await h.client.get(path)
        assert response.status_code == 503 and response.json() == {"code": "dependency_unavailable"}
        for query in [
            "?limit=01",
            "?limit=101",
            "?cursor=" + REF + "&cursor=" + OTHER,
            "?body=PRIVATE_BODY_CANARY",
        ]:
            response = await h.client.get(path + query)
            assert response.status_code == 400 and "PRIVATE_BODY_CANARY" not in response.text
        response = await h.client.get(path, headers={"Host": "evil.example.test"})
        assert response.status_code == 400
