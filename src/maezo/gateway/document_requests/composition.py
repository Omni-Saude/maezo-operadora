"""Concrete PHI-local bridge construction; installed identities/keys/sources are required.

No environment fallback, human session, system HTTP route or synthetic source grant.
The host owns supplied engines; the returned worker owns its two native lifetimes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.communications.content import PhiContentKeys
from maezo.gateway.communications.models import alive
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.auth_publisher import AuthInputPublisher
from maezo.gateway.intake.native_store import PostgresAuthDispatchStore
from maezo.gateway.native_fetch.adapter import AcquiredInputs
from maezo.gateway.native_fetch.models import FetchProfile
from maezo.gateway.native_fetch.transport import NativeAdmissionProvider, NativeTLS

from .admission import SystemAdmission, SystemPublicationSource, SystemPublisher, one
from .completion import CompletionAuthority, CompletionClient
from .content import PhiRequestContent
from .models import SystemProducer, require
from .postgres import RequestInboxStore
from .producer import DocumentRequestPolicySource, DocumentRequestProducer, ProducerJournal, ProducerResult
from .transport import ProducerContextClient


@dataclass(frozen=True, slots=True, repr=False)
class DatabaseRole:
    engine: AsyncEngine = field(repr=False)
    login: str
    login_oid: int


@dataclass(frozen=True, slots=True, repr=False)
class BridgePlacement:
    zone: Literal["phi"]
    database: str
    database_oid: int
    server_address: str
    server_port: int
    phi: DatabaseRole
    metadata: DatabaseRole
    phi_publisher: DatabaseRole
    metadata_publisher: DatabaseRole
    journal: DatabaseRole
    valid_until: datetime
    verify: Callable[[bytes], None] = field(repr=False)
    current: Callable[[], None] = field(repr=False)

    def live(self) -> None:
        self.current()
        alive(self.valid_until)
        require(self.zone == "phi" and self.database_oid > 0 and self.server_port > 0, "unavailable")


async def verify_database_roles(placement: BridgePlacement) -> None:
    """Verify actual current TLS/database/login and forbidden cross-role privileges.

    Owner qualification still covers migrations, all invalidating writers, key
    residency and row policies; a successful connection never qualifies them.
    """
    placement.live()
    roles = {
        name: getattr(placement, name)
        for name in ("phi", "metadata", "phi_publisher", "metadata_publisher", "journal")
    }
    require(
        len({r.login for r in roles.values()}) == len(roles)
        and len({r.login_oid for r in roles.values()}) == len(roles),
        "unavailable",
    )
    observed = {}
    for name, role in roles.items():
        async with transaction(role.engine, 5) as c:
            row = await one(
                c,
                "SELECT current_database() AS database,d.oid AS database_oid,session_user AS login,"
                "r.oid AS login_oid,inet_server_addr()::text AS server_address,inet_server_port() AS "
                "server_port,"
                "ssl.ssl,r.rolsuper,r.rolcreaterole,r.rolcreatedb,r.rolreplication,r.rolbypassrls "
                "FROM pg_database d JOIN pg_roles r ON r.rolname=session_user "
                "JOIN pg_stat_ssl ssl ON ssl.pid=pg_backend_pid() "
                "WHERE d.datname=current_database() AND current_user=session_user",
                {},
            )
            require(
                row is not None
                and row["ssl"] is True
                and row["login"] == role.login
                and row["login_oid"] == role.login_oid
                and all(
                    row[k] == getattr(placement, k)
                    for k in ("database", "database_oid", "server_address", "server_port")
                )
                and not any(
                    row[k]
                    for k in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")
                ),
                "unavailable",
            )
            for other in roles.values():
                if other is role:
                    continue
                membership = await one(
                    c, "SELECT pg_has_role(current_user,:other,'MEMBER') AS member", {"other": other.login}
                )
                require(membership is not None and not membership["member"], "unavailable")
            forbidden = ["portal_document_request.system_identity"]
            if name not in {"phi_publisher", "metadata_publisher"}:
                forbidden += [
                    "portal_document_request.authority_head",
                    "portal_document_request.authority_publication",
                ]
            for table in forbidden:
                allowed = await one(
                    c,
                    "SELECT has_table_privilege(current_user,:table,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') "
                    "OR has_any_column_privilege(current_user,:table,'INSERT,UPDATE,REFERENCES') AS writable",
                    {"table": table},
                )
                require(allowed is not None and not allowed["writable"], "unavailable")
            if name != "phi":
                forbidden_read = await one(
                    c,
                    "SELECT has_table_privilege(current_user,'portal_communication.content','SELECT') "
                    "OR "
                    "has_any_column_privilege(current_user,'portal_communication.content','SELECT') "
                    "AS readable",
                    {},
                )
                require(forbidden_read is not None and not forbidden_read["readable"], "unavailable")
            if name == "metadata":
                journal = await one(
                    c,
                    "SELECT "
                    "has_table_privilege(current_user,'portal_document_request.producer_journal','SELECT') "
                    "OR "
                    "has_any_column_privilege(current_user,"
                    "'portal_document_request.producer_journal','SELECT') AS readable",
                    {},
                )
                require(journal is not None and not journal["readable"], "unavailable")
            placement.live()
            observed[name] = dict(row)
        placement.live()
    # Only the qualification owner can bind the exact observed installation.
    from maezo.portal.engine.profile import canonicalize

    placement.verify(
        canonicalize({"schema": "maezo.auth-document-request.db-placement.v1", "roles": observed})
    )
    placement.live()


@dataclass(frozen=True, slots=True, repr=False)
class InstalledProducer:
    worker: DocumentRequestProducer
    placement: BridgePlacement

    async def run(self, inputs: AcquiredInputs) -> ProducerResult:
        await verify_database_roles(self.placement)
        result = await self.worker.run(inputs)
        self.placement.live()
        result.current()
        return result

    async def recover(self, request_ref: str) -> ProducerResult:
        await verify_database_roles(self.placement)
        result = await self.worker.recover(request_ref)
        self.placement.live()
        result.current()
        return result

    async def successor(
        self, inputs: AcquiredInputs, predecessor_command_id: str, invocation_ref: str
    ) -> ProducerResult:
        await verify_database_roles(self.placement)
        result = await self.worker.successor(inputs, predecessor_command_id, invocation_ref)
        self.placement.live()
        result.current()
        return result

    async def recover_successor(self, request_ref: str, invocation_ref: str) -> ProducerResult:
        await verify_database_roles(self.placement)
        result = await self.worker.recover_successor(request_ref, invocation_ref)
        self.placement.live()
        result.current()
        return result

    async def close(self, timeout: float | None = None) -> bool:  # noqa: ASYNC109
        # The host retains engine/credential ownership; close only worker-owned native lifetimes.
        loop = asyncio.get_running_loop()
        end = None if timeout is None else loop.time() + timeout
        context = await self.worker.context.channel.close(
            None if end is None else max(0.0, end - loop.time())
        )
        completion = await self.worker.completion.channel.close(
            None if end is None else max(0.0, end - loop.time())
        )
        return context and completion


async def compose(
    *,
    placement: BridgePlacement,
    producer: SystemProducer,
    tls: NativeTLS,
    fetch_profile: FetchProfile,
    designation_digest: str,
    fetch_authority: NativeAdmissionProvider,
    completion_authority: CompletionAuthority,
    completion_capability: bytes,
    completion_catalog: bytes,
    completion_catalog_digest: str,
    policy_source: DocumentRequestPolicySource,
    policy_publisher: AuthInputPublisher,
    phi_source: SystemPublicationSource,
    metadata_source: SystemPublicationSource,
    body_keys: dict[str, bytes],
    active_body_key_id: str,
    body_valid_until: datetime,
    provenance_key_id: str,
    provenance_key: bytes,
    journal_key_id: str,
    journal_key: bytes,
) -> InstalledProducer:
    """All credentials and policy come from positively selected deployment inputs.

    General metadata login receives no plaintext or ciphertext/key access. The
    orchestration, policy evaluation, PHI body custody and encrypted recovery
    journal reside in the PHI worker. No BFF pool may be supplied as its host.
    """
    placement.live()
    require(
        isinstance(policy_source, DocumentRequestPolicySource)
        and isinstance(phi_source, SystemPublicationSource)
        and isinstance(metadata_source, SystemPublicationSource),
        "unavailable",
    )
    require(provenance_key_id != journal_key_id and active_body_key_id in body_keys, "unavailable")
    key_bytes = list(body_keys.values()) + [provenance_key, journal_key]
    require(
        all(type(k) is bytes and len(k) == 32 for k in key_bytes) and len(set(key_bytes)) == len(key_bytes),
        "unavailable",
    )
    await verify_database_roles(placement)
    from maezo.gateway.communications.models import CommunicationScope

    scope = CommunicationScope(tenant=producer.tenant, environment=producer.environment)
    phi = SystemAdmission(placement.phi.engine, scope)
    metadata = SystemAdmission(placement.metadata.engine, scope)
    provenance = PostgresAuthDispatchStore(
        placement.phi.engine, tenant=producer.tenant, key_id=provenance_key_id, key=provenance_key
    )
    protected_journal = PostgresAuthDispatchStore(
        placement.journal.engine, tenant=producer.tenant, key_id=journal_key_id, key=journal_key
    )
    keys = PhiContentKeys(
        scope=scope, active_key_id=active_body_key_id, keys=body_keys, valid_until=body_valid_until
    )
    # Construct completion first: an empty/unselected catalog fails before any activation.
    completion = CompletionClient(
        tls, completion_authority, completion_capability, completion_catalog, completion_catalog_digest
    )
    try:
        context = ProducerContextClient(tls, fetch_authority, fetch_profile, designation_digest)
        require(
            completion.cap["identity"] == fetch_profile.value()["identity"]
            and completion.cap["target"] == fetch_profile.value()["target"]
            and completion.cap["worker_id"] == fetch_profile.value()["worker_id"],
            "unavailable",
        )
        worker = DocumentRequestProducer(
            producer=producer,
            context=context,
            policy_source=policy_source,
            policy_publisher=policy_publisher,
            phi_publisher=SystemPublisher(SystemAdmission(placement.phi_publisher.engine, scope), phi_source),
            inbox_publisher=SystemPublisher(
                SystemAdmission(placement.metadata_publisher.engine, scope), metadata_source
            ),
            content=PhiRequestContent(phi, keys, provenance),
            inbox=RequestInboxStore(metadata),
            journal=ProducerJournal(protected_journal, producer, placement.live),
            completion=completion,
        )
        placement.live()
        return InstalledProducer(worker, placement)
    except BaseException:
        await completion.channel.close()
        if "context" in locals():
            await context.channel.close()
        raise
