"""Source-owned publication, native CAS receipts and explicit lost-ack reconciliation.

A source provider is a missing operational dependency when no qualified implementation
is installed. This module never exposes a sign-supplied-facts endpoint.
"""

from __future__ import annotations

import base64
import secrets
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import OpaqueRef, Revision, Sha256Digest
from maezo.portal.engine.profile import canonicalize, strict_loads

from .models import Closed
from .queue import ReadRefusalError
from .read_credentials import unavailable
from .read_profile import (
    PAYLOAD_TYPES,
    CatalogDesignation,
    CatalogRevocation,
    KeyRevocation,
    MembershipProjection,
    PublicationKind,
    ResourceProjection,
    SourceProvenance,
    digest,
    parse_model,
    wire,
)
from .read_transport import PortalReadClient


def _safe[**P, R](call: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await call(*args, **kwargs)
        except ReadRefusalError:
            raise
        except Exception:
            raise unavailable() from None

    return wrapped


class PublicationReceipt(Closed):
    schema_: Literal["portal-read-publication-receipt.v1"] = Field(alias="schema")
    publication_id: OpaqueRef
    request_digest: Sha256Digest
    authority_revision: Revision
    kind: PublicationKind
    record_digest: Sha256Digest


@dataclass(frozen=True, slots=True, repr=False)
class SourceFreezeLease:
    provenance: SourceProvenance
    verify: Callable[[bytes], None] = field(repr=False)
    live: Callable[[], None] = field(repr=False)
    committed: Callable[[PublicationReceipt], None] = field(repr=False)
    uncertain: Callable[[], None] = field(repr=False)


class MembershipPublicationHandshake(ABC):
    @abstractmethod
    async def freeze(self, issuer: str, subject: str) -> SourceFreezeLease:
        """Freeze issuance under actual committed upstream identity provenance/DB role."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True, repr=False)
class SourceSnapshot:
    source: SourceProvenance
    payload: BaseModel = field(repr=False)
    lease: SourceFreezeLease = field(repr=False)


class PostgresMembershipPublicationSource:
    def __init__(self, *, store: PostgresIdentityStore, handshake: MembershipPublicationHandshake) -> None:
        if not isinstance(store, PostgresIdentityStore):
            raise unavailable()
        self._store, self._handshake = store, handshake

    @_safe
    async def read(self, issuer: str, subject: str) -> SourceSnapshot:
        lease = await self._handshake.freeze(issuer, subject)
        try:
            lease.live()
            record = await self._store.get_membership(issuer, subject)
            lease.live()
            if (
                record is None
                or record.tenant != self._store.tenant
                or record.issuer != issuer
                or record.subject != subject
            ):
                raise unavailable()
            # The actual uncached PostgreSQL result is compared to the frozen committed
            # source receipt. Independent old put/delete methods do not install this handshake.
            record = MembershipRecord.model_validate(record)
            lease.verify(canonicalize(wire(record)))
            projection = MembershipProjection(
                principal_ref=record.principal_ref,
                issuer=record.issuer,
                subject=record.subject,
                membership_revision=record.revision,
                audience=record.audience,
                memberships=record.memberships,
                subject_bindings=record.subject_bindings,
                state="revoked" if record.revoked else "active",
                reviewed_until=record.reviewed_until,
            )
            return SourceSnapshot(lease.provenance, projection, lease)
        except BaseException:
            lease.uncertain()
            raise


class DeploymentCatalogPublicationSource(ABC):
    @abstractmethod
    async def read(self, catalog_ref: str) -> SourceSnapshot:
        """Actual committed deployment receipt + exact designated artifacts, never local bytes alone."""
        raise NotImplementedError


class ResourcePolicyPublicationSource(ABC):
    @abstractmethod
    async def read(self, task_id: str) -> SourceSnapshot:
        """Complete authorized resource decision/classification source; no repo implementation exists."""
        raise NotImplementedError


class PagtoEvidencePublicationSource(ABC):
    @abstractmethod
    async def read(self, evidence_ref: str, revision: int, digest: str) -> BaseModel:
        """Qualified original typed evidence; never reconstruct lastro/clinical facts."""
        raise NotImplementedError


class ReadRevocationPublicationSource(ABC):
    @abstractmethod
    async def read_catalog(self, catalog_ref: str) -> SourceSnapshot:
        raise NotImplementedError

    @abstractmethod
    async def read_key(self, fingerprint: str) -> SourceSnapshot:
        raise NotImplementedError


@dataclass(frozen=True, slots=True, repr=False)
class _Pending:
    request_bytes: bytes = field(repr=False)
    source: SourceSnapshot = field(repr=False)
    expected_revision: int


class PortalReadPublisher:
    def __init__(
        self,
        *,
        client: PortalReadClient,
        membership: PostgresMembershipPublicationSource,
        catalog: DeploymentCatalogPublicationSource,
        resource: ResourcePolicyPublicationSource,
        pagto: PagtoEvidencePublicationSource,
        revocation: ReadRevocationPublicationSource,
    ) -> None:
        self._client = client
        self._membership, self._catalog, self._resource = membership, catalog, resource
        self._pagto, self._revocation = pagto, revocation
        self._pending: _Pending | None = None
        self._busy = False

    @_safe
    async def publish_membership(
        self, issuer: str, subject: str, *, expected_revision: int
    ) -> PublicationReceipt:
        return await self._begin(
            "membership", await self._membership.read(issuer, subject), expected_revision
        )

    @_safe
    async def publish_catalog(self, catalog_ref: str, *, expected_revision: int) -> PublicationReceipt:
        snapshot = await self._catalog.read(catalog_ref)
        if (
            not isinstance(snapshot.payload, CatalogDesignation)
            or snapshot.payload.catalog_ref != catalog_ref
        ):
            raise unavailable()
        return await self._begin("catalog-designate", snapshot, expected_revision)

    @_safe
    async def publish_resource(self, task_id: str, *, expected_revision: int) -> PublicationReceipt:
        snapshot = await self._resource.read(task_id)
        if not isinstance(snapshot.payload, ResourceProjection) or snapshot.payload.task_id != task_id:
            raise unavailable()
        p = snapshot.payload
        if p.read_only_evidence is not None:
            evidence = await self._pagto.read(p.evidence_ref, p.evidence_revision, p.evidence_digest)
            snapshot.lease.live()
            if wire(evidence) != wire(p.read_only_evidence):
                raise unavailable()
        return await self._begin("resource", snapshot, expected_revision)

    @_safe
    async def revoke_catalog(self, catalog_ref: str, *, expected_revision: int) -> PublicationReceipt:
        snapshot = await self._revocation.read_catalog(catalog_ref)
        if not isinstance(snapshot.payload, CatalogRevocation) or snapshot.payload.catalog_ref != catalog_ref:
            raise unavailable()
        return await self._begin("catalog-revoke", snapshot, expected_revision)

    @_safe
    async def revoke_key(self, fingerprint: str, *, expected_revision: int) -> PublicationReceipt:
        snapshot = await self._revocation.read_key(fingerprint)
        if not isinstance(snapshot.payload, KeyRevocation) or snapshot.payload.key_fingerprint != fingerprint:
            raise unavailable()
        return await self._begin("revoke-key", snapshot, expected_revision)

    async def _begin(
        self, kind: PublicationKind, source: SourceSnapshot, expected_revision: int
    ) -> PublicationReceipt:
        if (
            self._pending is not None
            or self._busy
            or type(expected_revision) is not int
            or not 0 <= expected_revision < 2**63 - 1
        ):
            source.lease.uncertain()
            raise unavailable()
        self._busy = True
        try:
            source.lease.live()
            parse_model(PAYLOAD_TYPES[kind], wire(source.payload))
            await self._client.partition.prepare("portal-read-publication")
            admission = self._client.partition.admission
            signing = self._client.partition.signing
            assert admission is not None and signing is not None
            a = admission.record
            if (
                source.source != source.lease.provenance
                or source.source.publisher_ref != signing.requester.issuer
            ):
                raise unavailable()
            request = {
                "schema": "portal-read-publication.v1",
                "scope": wire(a.scope),
                "engine_name": a.engine_name,
                "database_incarnation": a.database_incarnation,
                "read_deployment_ref": a.read_deployment_ref,
                "read_deployment_digest": a.read_deployment_digest,
                "publication_id": "publication-" + secrets.token_hex(32),
                "expected_authority_revision": str(expected_revision),
                "source": wire(source.source),
                "kind": kind,
                "payload": wire(source.payload),
            }
            self._pending = _Pending(canonicalize(request), source, expected_revision)
            return await self._submit()
        except BaseException:
            source.lease.uncertain()
            raise
        finally:
            self._busy = False

    @_safe
    async def reconcile(self) -> PublicationReceipt:
        """Retry only this owner's exact uncertain request. No CAS advance or source refresh."""
        if self._pending is None or self._busy:
            raise unavailable()
        self._busy = True
        try:
            return await self._submit()
        finally:
            self._busy = False

    async def _submit(self) -> PublicationReceipt:
        pending = self._pending
        if pending is None:
            raise unavailable()
        partition = self._client.partition
        request: dict[str, Any] = strict_loads(pending.request_bytes)
        await partition.prepare("portal-read-publication")
        now = partition.guard()
        signing, admission = partition.signing, partition.admission
        assert signing is not None and admission is not None
        pending.source.lease.live()
        until = min(
            signing.not_after,
            admission.record.valid_until,
            pending.source.source.valid_until,
            now + timedelta(seconds=signing.max_envelope_seconds),
        )
        expires = int(until.timestamp())
        if expires <= int(now.timestamp()):
            raise unavailable()
        outer = {
            "schema": "portal-read-envelope.v1",
            "purpose": "portal-read-publication",
            "algorithm": "Ed25519",
            "audience": signing.audience,
            "issuer": signing.requester.issuer,
            "tenant": partition.scope.tenant,
            "key_id": signing.requester.key_id,
            "issued_at": str(int(now.timestamp())),
            "expires_at": str(expires),
            "digest": digest(request),
            "request": request,
        }
        outer["signature"] = (
            base64.urlsafe_b64encode(signing.sign(canonicalize(outer), partition.guard()))
            .rstrip(b"=")
            .decode("ascii")
        )
        raw = canonicalize(outer)
        if len(raw) > 65536:
            raise unavailable()
        try:
            async with self._client._http.stream(
                "POST",
                self._client._origin + "/maezo-human-read/v1/publications",
                content=raw,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            ) as response:
                self._client._peer(response)
                if "set-cookie" in response.headers:
                    raise unavailable()
                if (
                    response.status_code != 200
                    or response.headers.get("cache-control") != "no-store"
                    or response.headers.get("content-type", "").split(";")[0] != "application/json"
                ):
                    raise unavailable()
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    result.extend(chunk)
                    if len(result) > 65536:
                        raise unavailable()
                    partition.guard()
                receipt = parse_model(PublicationReceipt, strict_loads(bytes(result)))
            partition.guard()
            pending.source.lease.live()
            if (
                receipt.publication_id != request["publication_id"]
                or receipt.request_digest != digest(request)
                or receipt.kind != request["kind"]
                or receipt.authority_revision != pending.expected_revision + 1
                or (receipt.kind != "resource" and receipt.record_digest != digest(request["payload"]))
            ):
                raise unavailable()
            # Resource stored revision is native post-flush; receipt digest deliberately
            # differs from signed observed precondition. Never predict its numeric value.
            pending.source.lease.committed(receipt)
            self._pending = None
            return receipt
        except BaseException:
            pending.source.lease.uncertain()
            raise
