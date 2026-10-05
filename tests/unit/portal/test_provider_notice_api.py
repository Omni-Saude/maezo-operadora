"""ASGI notice/ack wire compatibility with explicit TestOnly identity and source."""

import pytest
from tests.unit.gateway.communications.test_provider_notice import (
    CONTENT,
    NOTICE,
    PROVIDER,
    TestOnlyJournal,
    ack,
)
from tests.unit.portal.test_human_session import ISSUER, ORIGIN, PREFIX, SUBJECT, Harness, h, membership

from maezo.gateway.communications.provider_notice import ProviderNoticeRecipientService
from maezo.portal.contracts.models import SubjectBinding

__all__ = ["h"]


async def test_missing_notice_source_does_not_install_truth(h: Harness) -> None:
    result = await h.client.get(PREFIX + "/provider-notices/" + NOTICE)
    assert result.status_code == 503 and result.json() == {"code": "dependency_unavailable"}


async def test_explicit_ack_http_flow_keeps_metadata_pending(h: Harness) -> None:
    h.store.memberships[(ISSUER, SUBJECT)] = membership(
        audience="provider", subject_bindings=(SubjectBinding(kind="provider", resource_ref=PROVIDER),)
    )
    source = TestOnlyJournal()
    h.app.state.provider_notice_service_factory = lambda resolver: ProviderNoticeRecipientService(
        resolver, source
    )
    assert (await h.login()).status_code == 303
    csrf = (await h.client.get(PREFIX + "/session")).json()["csrf_token"]
    path = PREFIX + "/provider-notices/" + NOTICE
    metadata = await h.client.get(path)
    assert metadata.status_code == 200
    assert metadata.json()["content_digest"] == CONTENT
    assert metadata.json()["receipt"]["delivery_status"] == "pending"
    result = await h.client.post(
        path + "/acknowledgements",
        json=ack().model_dump(mode="json"),
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )
    assert result.status_code == 200 and result.json()["delivery_status"] == "delivered"
    assert result.headers["cache-control"] == "no-store"
    assert source.receipt.provider_delivery_receipt_ref == "explicit-ack-testonly"


@pytest.mark.parametrize("extra", ["actor_ref", "principal_ref", "provider_ref", "receipt_ref", "body"])
async def test_ack_http_rejects_actor_content_and_receipt_claims(h: Harness, extra: str) -> None:
    result = await h.client.post(
        PREFIX + "/provider-notices/" + NOTICE + "/acknowledgements",
        json={**ack().model_dump(mode="json"), extra: "PRIVATE_NOTICE_CANARY"},
        headers={"Origin": ORIGIN, "X-CSRF-Token": "synthetic"},
    )
    assert result.status_code == 400
    assert "PRIVATE_NOTICE_CANARY" not in result.text
    assert result.headers["cache-control"] == "no-store"


def test_old_communication_wire_and_phi_perimeter_are_preserved(h: Harness) -> None:
    schemas = h.app.openapi()["components"]["schemas"]
    assert set(schemas["CommunicationReceipt"]["properties"]) == {
        "schema_version",
        "communication_ref",
        "command_id",
        "disposition",
    }
    assert "body" not in schemas["ProviderNoticeSummary"]["properties"]
    assert "actor_ref" not in schemas["ProviderNoticeAcknowledgement"]["properties"]
