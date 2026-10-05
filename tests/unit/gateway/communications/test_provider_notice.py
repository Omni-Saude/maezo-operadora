"""TestOnly source/identity fixtures; no production act, receipt or PostgreSQL qualification."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from tests.unit.portal.test_human_session import ISSUER, ORIGIN, SUBJECT, config, membership

from maezo.gateway.capabilities.models import CapabilityContractError, DeliveryEvidenceReceipt
from maezo.gateway.communications.models import CommunicationScope
from maezo.gateway.communications.provider_notice import (
    NOTICE_FUNCTIONS,
    NOTICE_TABLES,
    FunctionPin,
    NoticeRecipientObservation,
    NoticeRelationPin,
    ProviderNoticeInstallation,
    ProviderNoticeRecipientService,
    require_provider_delivery,
)
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.portal.api.auth import AuthenticationError, digest
from maezo.portal.api.records import SessionRecord
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.communications import (
    CommunicationReceipt,
    ProviderNoticeAcknowledgement,
    ProviderNoticeReceipt,
    ProviderNoticeSummary,
)
from maezo.portal.contracts.models import SubjectBinding

NOTICE = "notice-synthetic"
PROVIDER = "provider-synthetic"
CONTENT = "a" * 64


def installation(**changes: Any) -> ProviderNoticeInstallation:
    values = dict(
        schema_version="provider-notice-installation.v1",
        scope=CommunicationScope(tenant="test-tenant", environment="testonly"),
        database_oid=123,
        schema_name="testonly_notices",
        schema_oid=234,
        owner_role="testonly_owner",
        authority_validator_role="testonly_validator",
        producer_role="testonly_producer",
        recipient_role="testonly_recipient",
        installation_receipt_ref="testonly-install",
        identity_source_ref="testonly-identity",
        identity_installation_receipt_ref="testonly-id-install",
        content_relation_oid=345,
        content_owner_role="testonly_phi_owner",
        session_lock_oid=456,
        session_lock_owner_role="testonly_identity_owner",
        session_lock_sha256="b" * 64,
        relations=tuple(NoticeRelationPin(name=n, oid=i + 1000) for i, n in enumerate(sorted(NOTICE_TABLES))),
        functions=tuple(
            FunctionPin(name=n, oid=i + 2000, sha256="c" * 64) for i, n in enumerate(sorted(NOTICE_FUNCTIONS))
        ),
        valid_until=datetime.now(UTC) + timedelta(minutes=10),
    )
    values.update(changes)
    return ProviderNoticeInstallation(**values)


class TestOnlyJournal:
    """Explicit in-memory UNIT port. Persistent/restart qualification belongs to live PG tests."""

    __test__ = False

    def __init__(self) -> None:
        self.scope = CommunicationScope(tenant="test-tenant", environment="testonly")
        self.calls = 0
        self.enabled = True
        self.before_commit: Any = None
        self.after_commit: Any = None
        self.receipt = ProviderNoticeReceipt(
            notice_ref=NOTICE,
            notice_revision="revision1",
            delivery_status="pending",
            metadata_publication_receipt_ref="metadata-testonly",
        )
        self.command: str | None = None

    async def recipient(
        self,
        *,
        secret: str,
        session: ResolvedHumanSession,
        notice_ref: str,
        acknowledgement: ProviderNoticeAcknowledgement | None,
    ) -> NoticeRecipientObservation:
        self.calls += 1
        if self.before_commit is not None:
            await self.before_commit()
        if (
            not self.enabled
            or notice_ref != NOTICE
            or not any(
                b.kind == "provider" and b.resource_ref == PROVIDER
                for b in session.principal.subject_bindings
            )
        ):
            raise ExternalCaseError("denied")
        if acknowledgement is None:
            result = ProviderNoticeSummary(
                notice_ref=NOTICE,
                notice_revision="revision1",
                body_ref="protected-body-testonly",
                content_digest=CONTENT,
                receipt=self.receipt,
            )
            return NoticeRecipientObservation(
                result=result, valid_until=datetime.now(UTC) + timedelta(minutes=2)
            )
        if acknowledgement.content_digest != CONTENT or acknowledgement.notice_revision != "revision1":
            raise ExternalCaseError("denied")
        if self.command is not None and self.command != acknowledgement.command_id:
            raise ExternalCaseError("conflict")
        self.command = acknowledgement.command_id
        self.receipt = ProviderNoticeReceipt(
            notice_ref=NOTICE,
            notice_revision="revision1",
            delivery_status="delivered",
            metadata_publication_receipt_ref="metadata-testonly",
            provider_delivery_receipt_ref="explicit-ack-testonly",
        )
        if self.after_commit is not None:
            await self.after_commit()
        return NoticeRecipientObservation(
            result=self.receipt, valid_until=datetime.now(UTC) + timedelta(minutes=2)
        )


@pytest.fixture
async def synthetic() -> tuple[ProviderNoticeRecipientService, TestOnlyJournal, LocalTestIdentityStore, str]:
    identities = LocalTestIdentityStore("test-tenant")
    identities.memberships[(ISSUER, SUBJECT)] = membership(
        audience="provider", subject_bindings=(SubjectBinding(kind="provider", resource_ref=PROVIDER),)
    )
    token = secrets.token_urlsafe(32)
    await identities.put_session(
        SessionRecord(
            secret_hash=digest(token),
            session_ref="session-testonly",
            csrf_token="csrf-testonly",
            issuer=ISSUER,
            subject=SUBJECT,
            principal_ref="human-internal-1",
            membership_revision=1,
            authenticated_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ),
        None,
    )
    journal = TestOnlyJournal()
    return (
        ProviderNoticeRecipientService(HumanSessionResolver(config(), identities), journal),
        journal,
        identities,
        token,
    )


def ack(**changes: Any) -> ProviderNoticeAcknowledgement:
    values = dict(command_id="ack-testonly-00001", notice_revision="revision1", content_digest=CONTENT)
    values.update(changes)
    return ProviderNoticeAcknowledgement(**values)


async def test_get_and_metadata_receipt_do_not_deliver(synthetic: Any) -> None:
    service, journal, _, token = synthetic
    raw = await service.operation(token, notice_ref=NOTICE, body=None, freeze=lambda x: x)
    assert json.loads(raw)["receipt"]["delivery_status"] == "pending"
    assert journal.command is None and journal.receipt.provider_delivery_receipt_ref is None
    old = CommunicationReceipt(
        communication_ref="old-ref-testonly0001", command_id="old-command-testonly0001"
    )
    assert old.disposition == "inbox_available"


async def test_provider_ack_and_same_command_replay(synthetic: Any) -> None:
    service, journal, _, token = synthetic
    first = await service.operation(
        token, notice_ref=NOTICE, body=ack(), csrf="csrf-testonly", origin=ORIGIN, freeze=lambda x: x
    )
    second = await service.operation(
        token, notice_ref=NOTICE, body=ack(), csrf="csrf-testonly", origin=ORIGIN, freeze=lambda x: x
    )
    assert first == second
    assert json.loads(first)["provider_delivery_receipt_ref"] == "explicit-ack-testonly"
    assert journal.receipt.delivery_status == "delivered"


@pytest.mark.parametrize("changes", [{"content_digest": "b" * 64}, {"notice_revision": "revision2"}])
async def test_content_or_revision_mismatch_denies_before_receipt(synthetic: Any, changes: Any) -> None:
    service, journal, _, token = synthetic
    with pytest.raises(ExternalCaseError):
        await service.operation(
            token,
            notice_ref=NOTICE,
            body=ack(**changes),
            csrf="csrf-testonly",
            origin=ORIGIN,
            freeze=lambda x: x,
        )
    assert journal.receipt.delivery_status == "pending"


@pytest.mark.parametrize(
    "csrf,origin", [(None, ORIGIN), ("forged", ORIGIN), ("csrf-testonly", "https://evil.test")]
)
async def test_ack_requires_csrf_and_origin(synthetic: Any, csrf: Any, origin: Any) -> None:
    service, journal, _, token = synthetic
    with pytest.raises(AuthenticationError):
        await service.operation(
            token, notice_ref=NOTICE, body=ack(), csrf=csrf, origin=origin, freeze=lambda x: x
        )
    assert journal.calls == 0


async def test_staff_cannot_ack(synthetic: Any) -> None:
    service, journal, identities, token = synthetic
    identities.memberships[(ISSUER, SUBJECT)] = membership()
    with pytest.raises(AuthenticationError):
        await service.operation(
            token, notice_ref=NOTICE, body=ack(), csrf="csrf-testonly", origin=ORIGIN, freeze=lambda x: x
        )
    assert journal.calls == 0


async def test_source_revocation_before_commit_restricts_only_effect(synthetic: Any) -> None:
    service, journal, _, token = synthetic

    async def revoke() -> None:
        journal.enabled = False

    journal.before_commit = revoke
    with pytest.raises(ExternalCaseError):
        await service.operation(
            token, notice_ref=NOTICE, body=ack(), csrf="csrf-testonly", origin=ORIGIN, freeze=lambda x: x
        )
    assert journal.command is None


async def test_source_revocation_after_ack_rechecks_before_disclosure(synthetic: Any) -> None:
    service, journal, _, token = synthetic

    async def revoke() -> None:
        journal.enabled = False

    journal.after_commit = revoke
    with pytest.raises(ExternalCaseError):
        await service.operation(
            token, notice_ref=NOTICE, body=ack(), csrf="csrf-testonly", origin=ORIGIN, freeze=lambda x: x
        )
    assert journal.receipt.delivery_status == "delivered"


@pytest.mark.parametrize("status", ["pending", "attempted", "unknown", "failed"])
def test_metadata_result_presence_cannot_satisfy_obligation(status: str) -> None:
    result = DeliveryEvidenceReceipt(
        delivery_status=status,
        attempt_revision="revision1",
        metadata_publication_receipt_ref="metadata-testonly",
    )
    with pytest.raises(CapabilityContractError):
        require_provider_delivery(result)


async def test_membership_revoked_after_committed_ack_prevents_disclosure_preserves_fact(
    synthetic: Any,
) -> None:
    service, journal, identities, token = synthetic

    async def revoke() -> None:
        member = identities.memberships[(ISSUER, SUBJECT)]
        identities.memberships[(ISSUER, SUBJECT)] = member.model_copy(update={"revoked": True})

    journal.after_commit = revoke
    with pytest.raises(AuthenticationError):
        await service.operation(
            token, notice_ref=NOTICE, body=ack(), csrf="csrf-testonly", origin=ORIGIN, freeze=lambda x: x
        )
    assert journal.receipt.delivery_status == "delivered"


async def test_lost_response_reconciles_same_original_command(synthetic: Any) -> None:
    service, journal, _, token = synthetic

    async def disconnect() -> None:
        raise TimeoutError("TESTONLY_LOST_RESPONSE")

    journal.after_commit = disconnect
    with pytest.raises(TimeoutError):
        await service.operation(
            token, notice_ref=NOTICE, body=ack(), csrf="csrf-testonly", origin=ORIGIN, freeze=lambda x: x
        )
    journal.after_commit = None
    raw = await service.operation(
        token, notice_ref=NOTICE, body=ack(), csrf="csrf-testonly", origin=ORIGIN, freeze=lambda x: x
    )
    assert json.loads(raw)["provider_delivery_receipt_ref"] == "explicit-ack-testonly"


@pytest.mark.parametrize(
    "field", ["actor_ref", "principal_ref", "audience", "provider_ref", "receipt_ref", "body"]
)
def test_ack_actor_and_receipt_cannot_come_from_json(field: str) -> None:
    with pytest.raises(ValidationError):
        ProviderNoticeAcknowledgement.model_validate_json(
            json.dumps({**ack().model_dump(mode="json"), field: "CANARY"})
        )


@pytest.mark.parametrize(
    "schema", ["public", "maezo_native", "portal_auth", "portal_communication", "cibseven"]
)
def test_installer_cannot_target_existing_owner_namespace(schema: str) -> None:
    with pytest.raises(ValidationError):
        installation(schema_name=schema)


def test_installer_separates_all_actors_and_source_owners() -> None:
    with pytest.raises(ValidationError):
        installation(recipient_role="testonly_producer")
    with pytest.raises(ValidationError):
        installation(session_lock_owner_role="testonly_owner")


@pytest.mark.parametrize("status,receipt", [("delivered", None), ("pending", "inbox-is-not-delivery")])
def test_receipt_status_cannot_fabricate_delivery(status: str, receipt: str | None) -> None:
    with pytest.raises(ValidationError):
        ProviderNoticeReceipt(
            notice_ref=NOTICE,
            notice_revision="revision1",
            delivery_status=status,
            metadata_publication_receipt_ref="metadata",
            provider_delivery_receipt_ref=receipt,
        )
