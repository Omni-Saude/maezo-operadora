"""Awaited durable external publication leases. The Q2 callback/receipt API is unchanged."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from .authority import InstalledAuthority
from .models import (
    CasePublicationReceipt,
    CheckpointIngressReceipt,
    CheckpointPacket,
    CheckpointPublicationReceipt,
    ExternalCaseError,
    PublicationReceipt,
    PublicationRequest,
    Scope,
    SourceIngressReceipt,
    SourcePacket,
    digest,
    now_utc,
    parse,
)
from .postgres import close_transaction as _close
from .postgres import sql_failure, transaction

Kind = Literal["case", "checkpoint"]


@dataclass(frozen=True, slots=True, repr=False)
class ExternalSourceSnapshot:
    kind: Kind
    scope: Scope
    publication_id: str
    packet_bytes: bytes
    ingress_bytes: bytes
    persisted_request: bytes | None
    native_receipt: bytes | None
    covering_receipt: bytes | None
    delivery_state: str

    def request(self, requester_fingerprint: str) -> PublicationRequest:
        packet = (
            parse(SourcePacket, self.packet_bytes)
            if self.kind == "case"
            else parse(CheckpointPacket, self.packet_bytes)
        )
        ingress = (
            parse(SourceIngressReceipt, self.ingress_bytes)
            if self.kind == "case"
            else parse(CheckpointIngressReceipt, self.ingress_bytes)
        )
        result = PublicationRequest(
            schema="portal-external-publication-request.v1",
            scope=self.scope,
            publication_id=self.publication_id,
            requester_fingerprint=requester_fingerprint,
            captured_provenance_digest=digest(ingress.wire()),
            kind=self.kind,
            packet=packet,
            ingress_receipt=ingress,
        )
        if self.persisted_request is not None:
            old = parse(PublicationRequest, self.persisted_request)
            if old.canonical() != result.canonical():
                raise ExternalCaseError("conflict")
            return old
        return result


class ExternalPublicationTransport(Protocol):
    """Gateway-owned qualified external Ed25519/mTLS purpose; no implementation default.

    A production implementation must wrap these exact bytes in the external envelope,
    verify peer/response bindings, and support exact-id historical receipt recovery.
    It is never the Q2 _submit method or general engine REST transport.
    """

    requester_fingerprint: str

    async def publish(self, request: bytes) -> bytes: ...
    async def receipt(self, request: bytes) -> bytes | None: ...


def publication_receipt(kind: Kind, raw: bytes) -> PublicationReceipt:
    return parse(CasePublicationReceipt, raw) if kind == "case" else parse(CheckpointPublicationReceipt, raw)


def bind_receipt(request: PublicationRequest, receipt: PublicationReceipt) -> None:
    if (
        request.kind != receipt.kind
        or request.scope != receipt.scope
        or request.publication_id != receipt.publication_id
        or request.requester_fingerprint != receipt.requester_fingerprint
        or hashlib.sha256(request.canonical()).hexdigest() != receipt.request_digest
    ):
        raise ExternalCaseError("conflict")
    ingress = request.ingress_receipt
    fields = (
        ("source_ref", "source_revision", "source_digest", "source_generation", "upstream_receipts_digest")
        if receipt.kind == "case"
        else ("checkpoint_ref", "epoch", "checkpoint_digest", "designation_digest")
    )
    for field in fields:
        if getattr(ingress, field) != getattr(receipt, field):
            raise ExternalCaseError("conflict")
    if isinstance(receipt, CheckpointPublicationReceipt):
        assert isinstance(request.packet, CheckpointPacket)
        if (
            request.packet.statement.heads_count != receipt.heads_count
            or request.packet.statement.heads_digest != receipt.heads_digest
        ):
            raise ExternalCaseError("conflict")


class PostgresExternalCasePublicationSource:
    def __init__(
        self,
        engine: AsyncEngine,
        scope: Scope,
        installation_key: Ed25519PublicKey | None,
        *,
        seconds: float = 5,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        if (
            not 0 < seconds <= 10
            or engine.dialect.name != "postgresql"
            or engine.echo
            or not engine.sync_engine.hide_parameters
        ):
            raise ExternalCaseError("unavailable")
        self.engine, self.scope, self.installation_key = engine, scope, installation_key
        self.seconds, self.clock = seconds, clock

    async def capture(self, kind: Kind, publication_id: str, claimant_ref: str) -> ExternalPublicationLease:
        if kind not in {"case", "checkpoint"}:
            raise ExternalCaseError("invalid")
        connection = None
        tx = None
        failure: Literal["invalid", "conflict", "unavailable", "denied", "uncertain"] = "unavailable"
        try:
            async with asyncio.timeout(self.seconds):
                connection = await self.engine.connect()
                tx = await connection.begin()
                ms = str(max(1, int(self.seconds * 1000)))
                await connection.execute(
                    text(
                        "SELECT set_config('statement_timeout',:ms,true), "
                        "set_config('lock_timeout',:ms,true), "
                        "set_config('idle_in_transaction_session_timeout',:ms,true)"
                    ),
                    {"ms": ms},
                )
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT * FROM maezo_external.capture_external_"
                                + kind
                                + "(CAST(:scope AS jsonb),:publication,:claimant,:deadline)"
                            ),
                            {
                                "scope": self.scope.canonical().decode(),
                                "publication": publication_id,
                                "claimant": claimant_ref,
                                "deadline": self.clock() + timedelta(seconds=self.seconds),
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                snapshot = ExternalSourceSnapshot(
                    kind,
                    self.scope,
                    publication_id,
                    bytes(row["packet_bytes"]),
                    bytes(row["ingress_bytes"]),
                    _bytes(row["canonical_request"]),
                    _bytes(row["native_receipt"]),
                    _bytes(row["covering_receipt"]),
                    row["delivery_state"],
                )
                authority = None
                if (
                    snapshot.native_receipt is None
                    and snapshot.covering_receipt is None
                    and snapshot.delivery_state != "uncertain"
                ):
                    authority = InstalledAuthority.verify(
                        bundle_bytes=bytes(row["canonical_designation"]),
                        receipt_bytes=bytes(row["installation_receipt"]),
                        expected_digest=row["designation_digest"],
                        scope=self.scope,
                        installation_key=self.installation_key,
                        revoked_fingerprints=frozenset(row["revoked_fingerprints"]),
                        authority_revision=str(row["authority_revision"]),
                        now=self.clock(),
                    )
                    packet = (
                        parse(SourcePacket, snapshot.packet_bytes)
                        if kind == "case"
                        else parse(CheckpointPacket, snapshot.packet_bytes)
                    )
                    if isinstance(packet, SourcePacket):
                        authority.source(packet, self.clock())
                    else:
                        authority.checkpoint(packet, self.clock())
                return ExternalPublicationLease(
                    self,
                    connection,
                    tx,
                    snapshot,
                    claimant_ref,
                    int(row["claim_epoch"]),
                    row["claim_expires_at"],
                    authority,
                )
        except asyncio.CancelledError:
            if connection is not None:
                await _close(connection, tx, self.seconds)
            raise
        except ExternalCaseError as exc:
            failure = exc.code
        except Exception as exc:
            failure = sql_failure(exc)
        if connection is not None:
            await _close(connection, tx, self.seconds)
        raise ExternalCaseError(failure)


def _bytes(value: bytes | memoryview | None) -> bytes | None:
    return None if value is None else bytes(value)


class ExternalPublicationLease:
    def __init__(
        self,
        source: PostgresExternalCasePublicationSource,
        connection: AsyncConnection,
        tx: AsyncTransaction,
        snapshot: ExternalSourceSnapshot,
        claimant: str,
        epoch: int,
        expires: datetime,
        authority: InstalledAuthority | None,
    ) -> None:
        self.source, self._connection, self._tx = source, connection, tx
        self.snapshot, self.claimant, self.epoch, self.expires = snapshot, claimant, epoch, expires
        self.authority = authority
        self.state = "captured"
        self._request = snapshot.persisted_request

    async def __aenter__(self) -> ExternalPublicationLease:
        try:
            try:
                await self.verify_capture(self.snapshot)
            except BaseException:
                # capture() owns the transaction; failed entry never calls __aexit__.
                await self.release()
                raise
        except ExternalCaseError as safe:
            # Clear chains at the actual context boundary, including failed cleanup.
            safe.__context__ = None
            safe.__cause__ = None
            if hasattr(safe, "__notes__"):
                del safe.__notes__
            raise
        return self

    async def __aexit__(self, *unused: object) -> None:
        await self.release()

    def _params(self) -> dict[str, Any]:
        return {
            "scope": self.source.scope.canonical().decode(),
            "publication": self.snapshot.publication_id,
            "claimant": self.claimant,
            "epoch": self.epoch,
        }

    def _local(self) -> None:
        if self.source.clock() >= self.expires:
            raise ExternalCaseError("conflict")

    async def verify_capture(self, snapshot: ExternalSourceSnapshot) -> None:
        self._local()
        if (
            self.state != "captured"
            or not self._tx.is_active
            or not self._connection.in_transaction()
            or snapshot != self.snapshot
        ):
            raise ExternalCaseError("conflict")
        if self.authority is not None:
            packet = (
                parse(SourcePacket, snapshot.packet_bytes)
                if snapshot.kind == "case"
                else parse(CheckpointPacket, snapshot.packet_bytes)
            )
            if isinstance(packet, SourcePacket):
                self.authority.source(packet, self.source.clock())
            else:
                self.authority.checkpoint(packet, self.source.clock())

    async def persist_request(self, raw: bytes) -> None:
        await self.verify_capture(self.snapshot)
        request = parse(PublicationRequest, raw)
        if (
            request.scope != self.snapshot.scope
            or request.publication_id != self.snapshot.publication_id
            or request.kind != self.snapshot.kind
            or request.packet.canonical() != self.snapshot.packet_bytes
            or request.ingress_receipt.canonical() != self.snapshot.ingress_bytes
            or self._request is not None
            and self._request != raw
        ):
            raise ExternalCaseError("conflict")
        failure = None
        try:
            await self._connection.execute(
                text(
                    "SELECT maezo_external.persist_external_"
                    + self.snapshot.kind
                    + "(CAST(:scope AS jsonb),:publication,:claimant,:epoch,:request)"
                ),
                self._params() | {"request": raw},
            )
        except Exception as exc:
            failure = sql_failure(exc)
        if failure is not None:
            # Raise outside the provider handler, without its message, chains or notes.
            raise ExternalCaseError(failure)
        self._request = raw

    async def release_capture(self) -> None:
        await self.verify_capture(self.snapshot)
        if self._request is None and self.snapshot.covering_receipt is None:
            raise ExternalCaseError("conflict")
        failed = False
        try:
            async with asyncio.timeout(self.source.seconds):
                await self._tx.commit()
        except Exception:
            failed = True
        if failed:
            self.state = "uncertain"
            await _close(self._connection, self._tx, self.source.seconds)
            raise ExternalCaseError("uncertain")
        await _close(self._connection, self._tx, self.source.seconds)
        self.state = "dispatchable"

    async def _transition(
        self, action: str, receipt: bytes | None = None, covered_digest: str | None = None
    ) -> bool:
        self._local()
        if self.state not in {"dispatchable", "uncertain"}:
            raise ExternalCaseError("conflict")
        async with transaction(self.source.engine, self.source.seconds) as connection:
            result = (
                await connection.execute(
                    text(
                        "SELECT maezo_external.transition_external_"
                        + self.snapshot.kind
                        + "(CAST(:scope AS jsonb),:publication,:claimant,:epoch,"
                        ":action,:receipt,:covered_digest)"
                    ),
                    self._params() | {"action": action, "receipt": receipt, "covered_digest": covered_digest},
                )
            ).scalar_one()
            if result is not True:
                raise ExternalCaseError("conflict")
        return True

    async def live_claim(self) -> None:
        await self._transition("live")

    async def committed(self, receipt: PublicationReceipt) -> None:
        if self._request is None:
            raise ExternalCaseError("conflict")
        bind_receipt(parse(PublicationRequest, self._request), receipt)
        await self._transition("committed", receipt.canonical())
        self.state = "committed"

    async def covered(self, receipt: CasePublicationReceipt, covered_event_digest: str) -> None:
        if (
            self.snapshot.kind != "case"
            or receipt.scope != self.snapshot.scope
            or covered_event_digest != hashlib.sha256(self.snapshot.packet_bytes).hexdigest()
        ):
            raise ExternalCaseError("conflict")
        await self._transition("covered", receipt.canonical(), covered_event_digest)
        self.state = "covered"

    async def uncertain(self) -> None:
        if self._request is None:
            raise ExternalCaseError("conflict")
        await self._transition("uncertain")
        self.state = "uncertain"

    async def release(self) -> None:
        if self.state == "captured":
            await _close(self._connection, self._tx, self.source.seconds)
        elif self.state in {"dispatchable", "uncertain"}:
            # A lost/expired claim remains recoverable; this is not committed accounting.
            with suppress(ExternalCaseError):
                await self._transition("release")
        self.state = "released"


class ExternalCasePublisher:
    def __init__(
        self, source: PostgresExternalCasePublicationSource, transport: ExternalPublicationTransport | None
    ) -> None:
        self.source, self.transport = source, transport

    async def publish(self, kind: Kind, publication_id: str, claimant_ref: str) -> PublicationReceipt:
        try:
            return await self._publish(kind, publication_id, claimant_ref)
        except ExternalCaseError as safe:
            # async-with may reattach its body error when exit cleanup also refuses.
            # Clear only after the complete context has unwound, before exposing it.
            safe.__context__ = None
            safe.__cause__ = None
            if hasattr(safe, "__notes__"):
                del safe.__notes__
            raise

    async def _publish(self, kind: Kind, publication_id: str, claimant_ref: str) -> PublicationReceipt:
        if self.transport is None:
            raise ExternalCaseError("unavailable")
        async with await self.source.capture(kind, publication_id, claimant_ref) as lease:
            snapshot = lease.snapshot
            if snapshot.covering_receipt is not None:
                covering = parse(CasePublicationReceipt, snapshot.covering_receipt)
                await lease.release_capture()
                await lease.covered(covering, hashlib.sha256(snapshot.packet_bytes).hexdigest())
                return covering
            request = snapshot.request(self.transport.requester_fingerprint).canonical()
            await lease.persist_request(request)
            await lease.release_capture()
            await lease.live_claim()
            if snapshot.native_receipt is not None:
                receipt = publication_receipt(kind, snapshot.native_receipt)
                await lease.committed(receipt)
                return receipt
            try:
                async with asyncio.timeout(self.source.seconds):
                    raw = (
                        await self.transport.receipt(request)
                        if snapshot.delivery_state == "uncertain"
                        else None
                    )
                    if raw is None:
                        raw = await self.transport.publish(request)
                receipt = publication_receipt(kind, raw)
                await lease.committed(receipt)
                return receipt
            except asyncio.CancelledError:
                task = asyncio.create_task(lease.uncertain())
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                raise
            except Exception:
                await lease.uncertain()
        raise ExternalCaseError("uncertain")
