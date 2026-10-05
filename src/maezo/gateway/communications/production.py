"""Explicit qualified material composition; ROOT installs lifecycle/API slots after its gate."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.capabilities.admission import CapabilityAdmission
from maezo.gateway.capabilities.service import CapabilityService
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.portal.api.session import HumanSessionResolver

from .admission import PostgresCommunicationAdmission
from .provider_notice import (
    PostgresProviderNoticeStore,
    ProviderNoticeInstallation,
    ProviderNoticeRecipientService,
    ProviderNoticeSource,
)


@dataclass(frozen=True)
class ProviderNoticeRuntime:
    capabilities: CapabilityService
    recipient_factory: Callable[[HumanSessionResolver], ProviderNoticeRecipientService]


def compose_provider_notices(
    *,
    producer_engine: AsyncEngine,
    recipient_engine: AsyncEngine,
    installation: ProviderNoticeInstallation,
    admission: CapabilityAdmission,
    identity_admission: PostgresCommunicationAdmission,
) -> ProviderNoticeRuntime:
    """No credentials, authority rows or grants are installed by this factory.

    Admission must be the independently qualified enforcing OP09/signed workload
    boundary. Cross-owner identity/content installation and schema pins are verified
    again by PostgreSQL inside every operation; composition itself proves no source.
    """
    canonical = identity_admission.store._auth_composition
    if canonical is None:
        raise ExternalCaseError("unavailable")
    identity = canonical.source.binding
    if (
        installation.identity_source_ref != identity.source_ref
        or installation.identity_installation_receipt_ref != identity.installation_ref
        or installation.session_lock_owner_role != identity.owner_role
        or installation.database_oid != identity.database_oid
        or installation.scope.tenant != identity.tenant
        or installation.valid_until > identity.valid_until
    ):
        # Public identity copies and an old helper cannot silently stand in for AUTH-SL1.
        raise ExternalCaseError("unavailable")
    producer = PostgresProviderNoticeStore(producer_engine, installation, role="producer")
    recipient = PostgresProviderNoticeStore(
        recipient_engine, installation, role="recipient", identity_admission=identity_admission
    )
    source = ProviderNoticeSource(admission.binding, producer)
    service = CapabilityService(sources={"notice.prepare_or_send": source}, admission=admission)

    def factory(resolver: HumanSessionResolver) -> ProviderNoticeRecipientService:
        identity_admission.bind(resolver, recipient_engine)
        return ProviderNoticeRecipientService(resolver, recipient)

    return ProviderNoticeRuntime(service, factory)
