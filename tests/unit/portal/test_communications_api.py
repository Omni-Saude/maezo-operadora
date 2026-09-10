"""Separate ASGI planes with real service/SQL adapter code and synthetic transaction/identity."""

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from tests.unit.gateway.communications.test_delivery import OTHER, REF, Authority, setup
from tests.unit.portal.test_human_session import (
    ISSUER,
    ORIGIN,
    PREFIX,
    SESSION_COOKIE,
    SUBJECT,
    Harness,
    config,
    h,
    membership,
)

from maezo.gateway.communications.admission import PostgresCommunicationAdmission
from maezo.gateway.communications.content import (
    PhiCommunicationService,
    PhiContentKeys,
    PostgresPhiCommunicationContent,
)
from maezo.gateway.communications.models import CommunicationScope
from maezo.gateway.communications.postgres import PostgresCommunicationStore
from maezo.gateway.communications.service import CommunicationService
from maezo.portal.api.communication_phi import create_phi_communication_app
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.contracts.models import SubjectBinding

__all__ = ["h", "setup"]


@pytest.mark.asyncio
async def test_authorized_two_plane_content_to_actual_inbox_visibility(h: Harness, setup):
    h.store.memberships[(ISSUER, SUBJECT)] = membership(
        audience="provider", subject_bindings=(SubjectBinding(kind="provider", resource_ref=OTHER),)
    )
    await h.login()
    csrf = (await h.client.get(PREFIX + "/session")).json()["csrf_token"]
    resolver = h.app.state.human_session_resolver
    resolved = await resolver.resolve(h.client.cookies[SESSION_COOKIE])
    principal = resolved.principal
    scope = CommunicationScope(tenant=principal.tenant, environment="synthetic")
    authority = Authority(SimpleNamespace(principal=principal), scope, setup.clock)
    authority.db = setup.db
    setup.db.resolver = SimpleNamespace(records=lambda: resolved)
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    identity_store = PostgresIdentityStore(scope.tenant, engine)
    identity_store.get_session = h.store.get_session
    identity_store.get_membership = h.store.get_membership
    resolver.store = identity_store
    admission = PostgresCommunicationAdmission(identity_store, scope=scope, issuer=resolver.settings.issuer)
    store = PostgresCommunicationStore(engine, scope=scope, admission=admission)
    keys = PhiContentKeys(
        scope=scope,
        active_key_id="synthetic",
        keys={"synthetic": b"a" * 32},
        valid_until=setup.clock[0] + timedelta(minutes=2),
    )
    content = PostgresPhiCommunicationContent(engine, scope=scope, keys=keys, admission=admission)
    h.app.state.communication_service_factory = lambda actual: CommunicationService(actual, authority, store)
    phi = create_phi_communication_app(
        config(),
        identity_store=identity_store,
        content_service_factory=lambda actual: PhiCommunicationService(actual, authority, content),
    )
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(phi), base_url=ORIGIN, cookies=h.client.cookies
    ) as client:
        preserved = await client.post(
            "/api/v1/phi/cases/" + REF + "/communication-content",
            json={"command_id": REF, "body": "PRIVATE_BODY_CANARY"},
            headers=headers,
        )
        assert preserved.status_code == 200
        assert "PRIVATE_BODY_CANARY" not in preserved.text
        published = await h.client.post(
            PREFIX + "/cases/" + REF + "/communications",
            json={"command_id": REF, "body_ref": preserved.json()["body_ref"], "recipient_set_ref": OTHER},
            headers=headers,
        )
        assert published.status_code == 200 and published.json()["disposition"] == "inbox_available"
        inbox = await h.client.get(PREFIX + "/cases/" + REF + "/communications")
        assert inbox.status_code == 200 and len(inbox.json()["items"]) == 1
        assert "PRIVATE_BODY_CANARY" not in inbox.text
        assert inbox.json()["items"][0]["communication_ref"] == published.json()["communication_ref"]
        url = (
            "/api/v1/phi/cases/"
            + REF
            + "/communications/"
            + published.json()["communication_ref"]
            + "/content"
        )
        result = await client.get(url)
        assert result.status_code == 200 and result.json()["body"] == "PRIVATE_BODY_CANARY"
        assert result.headers["cache-control"] == "no-store"
        assert (await h.client.get(url)).status_code == 404
        history = await h.client.get(PREFIX + "/cases/" + REF + "/history")
        assert history.status_code == 200 and history.json()["history_scope"] == "portal_events"
        assert "PRIVATE_BODY_CANARY" not in history.text
        authority.denied.add("read_content")
        assert (await client.get(url)).status_code == 403
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
