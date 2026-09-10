"""Fixed PostgreSQL ingress and identity lock/read boundaries for reviewed W6A.

Pools and installation public keys are gateway composition inputs. This module
never constructs credentials, chooses an operator authority or calls a resolver
while retaining its session locks. No transaction spans source-to-engine I/O.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from maezo.portal.api.auth import digest as secret_digest
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.api.session import ResolvedHumanSession
from maezo.portal.contracts.models import HumanPrincipal

from .authority import InstalledAuthority
from .models import (
    MAX,
    CaseDetail,
    CasePage,
    CheckpointIngressReceipt,
    CheckpointPacket,
    ExternalCaseError,
    FinalizationReceipt,
    Scope,
    SourceIngressReceipt,
    SourcePacket,
    bind_ingress,
    now_utc,
    parse,
    require_publication_capacity,
    revision,
    timestamp,
)


def sql_failure(error: BaseException) -> Literal["invalid", "conflict", "unavailable", "denied", "uncertain"]:
    """Classify only our closed SQLSTATEs, never driver message/cause/notes text."""
    states: dict[str, Literal["invalid", "conflict", "unavailable", "denied"]] = {
        "P7E01": "invalid",
        "P7E02": "conflict",
        "P7E03": "unavailable",
        "P7E04": "denied",
    }
    current: Any = error
    seen: set[int] = set()
    for _ in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        state = getattr(current, "sqlstate", None)
        if type(state) is str and state in states:
            return states[state]
        current = getattr(current, "orig", None) or getattr(current, "__cause__", None)
    return "uncertain"


async def close_transaction(connection: AsyncConnection, tx: AsyncTransaction | None, seconds: float) -> None:
    """Await rollback/release even on cancellation; invalidate before pool reuse on failure."""

    async def cleanup() -> None:
        failed = False
        try:
            async with asyncio.timeout(seconds):
                if tx is not None and tx.is_active:
                    await tx.rollback()
                await connection.close()
        except BaseException:
            failed = True
        if failed:
            invalidation_failed = False
            try:
                async with asyncio.timeout(seconds):
                    # Never close/return a connection whose invalidation has not completed.
                    await connection.invalidate()
                    await connection.close()
            except BaseException:
                invalidation_failed = True
            if invalidation_failed:
                raise ExternalCaseError("unavailable")

    task = asyncio.create_task(cleanup())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


@asynccontextmanager
async def _transaction(engine: AsyncEngine, seconds: float) -> AsyncIterator[AsyncConnection]:
    """One finite transaction, including pool acquisition and acknowledged commit.

    Cleanup is awaited before reuse. Driver causes/contexts/notes are discarded;
    an unknown commit is uncertainty, never an accepted receipt.
    """
    if (
        not 0 < seconds <= 10
        or engine.dialect.name != "postgresql"
        or engine.echo
        or not engine.sync_engine.hide_parameters
    ):
        raise ExternalCaseError("unavailable")
    connection = None
    tx = None
    failure = None
    try:
        async with asyncio.timeout(seconds):
            connection = await engine.connect()
            tx = await connection.begin()
            if not connection.in_transaction():
                raise ExternalCaseError("unavailable")
            ms = str(max(1, int(seconds * 1000)))
            await connection.execute(
                text(
                    "SELECT set_config('statement_timeout',:ms,true), "
                    "set_config('lock_timeout',:ms,true), "
                    "set_config('idle_in_transaction_session_timeout',:ms,true)"
                ),
                {"ms": ms},
            )
            yield connection
            await tx.commit()
    except asyncio.CancelledError:
        raise
    except ExternalCaseError as exc:
        failure = exc.code
    except Exception as exc:
        failure = sql_failure(exc)
    finally:
        if connection is not None:
            await close_transaction(connection, tx, seconds)
    if failure is not None:
        raise ExternalCaseError(failure)


class _SafeTransaction(AbstractAsyncContextManager[AsyncConnection]):
    """Remove contextlib's injected body exception at the actual exit boundary."""

    def __init__(self, context: AbstractAsyncContextManager[AsyncConnection]) -> None:
        self.context = context

    async def __aenter__(self) -> AsyncConnection:
        return await self.context.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            return await self.context.__aexit__(exc_type, exc, traceback)
        except ExternalCaseError as safe:
            # Bare re-raise preserves this cleaned exception; a new raise here
            # would attach the provider exception again during async-with exit.
            safe.__context__ = None
            safe.__cause__ = None
            if hasattr(safe, "__notes__"):
                del safe.__notes__
            raise


def transaction(engine: AsyncEngine, seconds: float) -> AbstractAsyncContextManager[AsyncConnection]:
    return _SafeTransaction(_transaction(engine, seconds))


class PostgresExternalSourceImporter:
    """Verify original signed packets under installed currentness locks, then append."""

    def __init__(
        self,
        engine: AsyncEngine,
        scope: Scope,
        installation_key: Ed25519PublicKey | None,
        *,
        seconds: float = 5,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.engine, self.scope, self.installation_key = engine, scope, installation_key
        self.seconds, self.clock = seconds, clock

    async def _authority(
        self,
        connection: AsyncConnection,
        kind: str,
        ingress_id: str | None,
        request_digest: str | None,
    ) -> tuple[InstalledAuthority | None, bytes | None]:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT * FROM maezo_external.lock_external_ingress_authority("
                        "CAST(:scope AS jsonb),:kind,:ingress,:digest)"
                    ),
                    {
                        "scope": self.scope.canonical().decode(),
                        "kind": kind,
                        "ingress": ingress_id,
                        "digest": request_digest,
                    },
                )
            )
            .mappings()
            .one()
        )
        import base64

        from maezo.portal.engine.profile import canonicalize

        if set(row) != {
            "scope",
            "designation_digest",
            "canonical_designation",
            "installation_receipt",
            "authority_revision",
            "revoked_fingerprints",
            "historical_ingress_receipt",
        }:
            raise ExternalCaseError("unavailable")
        value = dict(row)
        for field in ("canonical_designation", "installation_receipt", "historical_ingress_receipt"):
            if value[field] is not None:
                value[field] = base64.b64encode(bytes(value[field])).decode("ascii")
        value["authority_revision"] = str(value["authority_revision"])
        if (
            value["scope"] != self.scope.wire()
            or value["canonical_designation"] is None
            or value["installation_receipt"] is None
            or len(canonicalize(value)) > MAX
        ):
            raise ExternalCaseError("unavailable")

        def decode(field: str) -> bytes:
            return base64.b64decode(value[field], validate=True)

        # Historical exact-id recovery is not new admission; SQL already bound its digest.
        historic = value["historical_ingress_receipt"]
        if historic is not None:
            receipt = decode("historical_ingress_receipt")
            parsed = (
                parse(SourceIngressReceipt, receipt)
                if kind == "case"
                else parse(CheckpointIngressReceipt, receipt)
            )
            if (
                parsed.scope != self.scope
                or parsed.ingress_id != ingress_id
                or parsed.request_digest != request_digest
            ):
                raise ExternalCaseError("conflict")
            return None, receipt
        pins = value["revoked_fingerprints"]
        if (
            not isinstance(pins, list)
            or pins != sorted(set(pins))
            or any(type(pin) is not str or not re.fullmatch(r"[0-9a-f]{64}", pin) for pin in pins)
        ):
            raise ExternalCaseError("unavailable")
        revision(value["authority_revision"])
        return InstalledAuthority.verify(
            bundle_bytes=decode("canonical_designation"),
            receipt_bytes=decode("installation_receipt"),
            expected_digest=value["designation_digest"],
            scope=self.scope,
            installation_key=self.installation_key,
            revoked_fingerprints=frozenset(pins),
            authority_revision=value["authority_revision"],
            now=self.clock(),
        ), None

    async def reserve(self, signed_upstream_resource_key: str) -> str:
        # Reservation conveys no access; the eventual independently signed packet must bind it.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}", signed_upstream_resource_key):
            raise ExternalCaseError("invalid")
        async with transaction(self.engine, self.seconds) as connection:
            authority, _ = await self._authority(connection, "reserve", None, None)
            assert authority is not None
            result = (
                await connection.execute(
                    text("SELECT maezo_external.reserve_external_case_ref(CAST(:scope AS jsonb),:key)"),
                    {"scope": self.scope.canonical().decode(), "key": signed_upstream_resource_key},
                )
            ).scalar_one()
            if type(result) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", result):
                raise ExternalCaseError("unavailable")
            authority.current(self.clock())
        return result

    async def accept_source(
        self, raw: bytes, ingress_id: str, *, expected_revision: str | None, expected_digest: str | None
    ) -> SourceIngressReceipt:
        result = await self._accept(raw, ingress_id, False, expected_revision, expected_digest)
        assert isinstance(result, SourceIngressReceipt)
        return result

    async def accept_checkpoint(
        self,
        raw: bytes,
        ingress_id: str,
        *,
        expected_epoch: str,
        expected_digest: str | None,
    ) -> CheckpointIngressReceipt:
        result = await self._accept(raw, ingress_id, True, expected_epoch, expected_digest)
        assert isinstance(result, CheckpointIngressReceipt)
        return result

    async def _accept(
        self,
        raw: bytes,
        ingress_id: str,
        checkpoint: bool,
        predecessor: str | None,
        predecessor_digest: str | None,
    ) -> SourceIngressReceipt | CheckpointIngressReceipt:
        if (
            type(raw) is not bytes
            or len(raw) > MAX
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}", ingress_id)
            or predecessor_digest is not None
            and not re.fullmatch(r"[0-9a-f]{64}", predecessor_digest)
        ):
            raise ExternalCaseError("invalid")
        expected = None if predecessor is None else revision(predecessor)
        packet = parse(CheckpointPacket, raw) if checkpoint else parse(SourcePacket, raw)
        if packet.statement.scope != self.scope:
            raise ExternalCaseError("denied")
        request_digest = hashlib.sha256(raw).hexdigest()
        async with transaction(self.engine, self.seconds) as connection:
            authority, historical = await self._authority(
                connection, "checkpoint" if checkpoint else "case", ingress_id, request_digest
            )
            if historical is not None:
                receipt = (
                    parse(CheckpointIngressReceipt, historical)
                    if checkpoint
                    else parse(SourceIngressReceipt, historical)
                )
            else:
                assert authority is not None
                until = (
                    authority.checkpoint(packet, self.clock())
                    if isinstance(packet, CheckpointPacket)
                    else authority.source(packet, self.clock())
                )
                operation = (
                    "accept_external_scope_checkpoint"
                    if checkpoint
                    else "revoke_external_case_source"
                    if isinstance(packet, SourcePacket) and packet.statement.state == "revoked"
                    else "accept_external_case_source"
                )
                result = (
                    await connection.execute(
                        text(
                            "SELECT maezo_external."
                            + operation
                            + "(CAST(:scope AS jsonb),:ingress,:expected,:digest,:packet)"
                        ),
                        {
                            "scope": self.scope.canonical().decode(),
                            "ingress": ingress_id,
                            "expected": expected,
                            "digest": predecessor_digest,
                            "packet": raw,
                        },
                    )
                ).scalar_one()
                receipt = (
                    parse(CheckpointIngressReceipt, bytes(result))
                    if checkpoint
                    else parse(SourceIngressReceipt, bytes(result))
                )
                if (
                    receipt.scope != self.scope
                    or receipt.ingress_id != ingress_id
                    or receipt.request_digest != request_digest
                ):
                    raise ExternalCaseError("conflict")
                authority.current(self.clock())
                if self.clock() >= until:
                    raise ExternalCaseError("unavailable")
            bind_ingress(packet, receipt)
            if historical is None:
                require_publication_capacity(packet, receipt)
        # The context exits only after commit acknowledgement; never return an in-tx receipt.
        return receipt


@dataclass(frozen=True, repr=False)
class LockedExternalSession:
    resolved: ResolvedHumanSession
    valid_until: datetime
    _transaction_live: Callable[[], bool] | None = dataclass_field(default=None, repr=False, compare=False)

    def current(self, now: datetime) -> None:
        if self._transaction_live is None or not self._transaction_live():
            raise ExternalCaseError("unavailable")
        self.fresh(now)

    def fresh(self, now: datetime) -> None:
        if now >= self.valid_until:
            raise ExternalCaseError("denied")


def validate_locked_session(
    row: Any, *, tenant: str, issuer: str, secret_hash: str, expected: HumanPrincipal, now: datetime
) -> LockedExternalSession:
    """Pure resolver-equivalent validation; no revocation callback or second transaction."""
    try:

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ExternalCaseError("denied")
                value[key] = item
            return value

        for payload in (row["session_payload"], row["membership_payload"]):
            if type(payload) is not str or len(payload.encode("utf-8")) > MAX:
                raise ExternalCaseError("denied")
            json.loads(payload, object_pairs_hook=unique_object)
        session = SessionRecord.model_validate_json(row["session_payload"])
        member = MembershipRecord.model_validate_json(row["membership_payload"])
        if (
            member.tenant != tenant
            or session.issuer != issuer
            or member.issuer != issuer
            or session.secret_hash != secret_hash
            or member.revoked
            or session.subject != member.subject
            or session.principal_ref != member.principal_ref
            or session.membership_revision != member.revision
            or session.expires_at <= now
            or member.reviewed_until <= now
            or member.audience not in {"beneficiary", "provider"}
            or row["session_tenant"] != tenant
            or row["membership_tenant"] != tenant
            or row["secret_hash"] != secret_hash
            or row["expires_at"] != session.expires_at
            or row["issuer"] != member.issuer
            or row["subject"] != member.subject
            or row["principal_ref"] != member.principal_ref
        ):
            raise ValueError
        principal = HumanPrincipal(
            schema_version=1,
            principal_ref=member.principal_ref,
            issuer=session.issuer,
            subject=session.subject,
            tenant=tenant,
            membership_revision=member.revision,
            memberships=member.memberships,
            session_ref=session.session_ref,
            authenticated_at=session.authenticated_at,
            subject_bindings=member.subject_bindings,
        )
        if principal != expected:
            raise ValueError
        result = LockedExternalSession(
            ResolvedHumanSession(principal, session, member), min(session.expires_at, member.reviewed_until)
        )
    except Exception:
        result = None
    if result is None:
        raise ExternalCaseError("denied")
    return result


class ExternalCaseFinalizer(Protocol):
    """Qualified native transport is supplied by gateway composition, absent by default."""

    async def finalize(
        self,
        *,
        continuity_proof: str,
        principal: HumanPrincipal,
        audience: str,
        public_projection_digest: str,
        session_valid_until: datetime,
    ) -> bytes: ...


class PostgresExternalFinalizationSessionLease:
    """One identity transaction held across native finalize; no resolver re-entry."""

    def __init__(
        self,
        engine: AsyncEngine,
        tenant: str,
        issuer: str,
        *,
        seconds: float = 5,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.engine, self.tenant, self.issuer = engine, tenant, issuer
        self.seconds, self.clock = seconds, clock

    @asynccontextmanager
    async def acquire(self, secret: str, expected: HumanPrincipal) -> AsyncIterator[LockedExternalSession]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
            raise ExternalCaseError("denied")
        hashed = secret_digest(secret)
        async with transaction(self.engine, self.seconds) as connection:
            row = (
                (
                    await connection.execute(
                        text("SELECT * FROM portal_identity.lock_external_session(:secret_hash)"),
                        {"secret_hash": hashed},
                    )
                )
                .mappings()
                .one()
            )
            locked = validate_locked_session(
                row,
                tenant=self.tenant,
                issuer=self.issuer,
                secret_hash=hashed,
                expected=expected,
                now=self.clock(),
            )
            locked = replace(locked, _transaction_live=connection.in_transaction)
            locked.current(self.clock())
            yield locked
            locked.current(self.clock())
        locked.fresh(self.clock())

    async def finalize_frozen(
        self,
        secret: str,
        expected: HumanPrincipal,
        *,
        continuity_proof: str,
        frozen_projection: bytes,
        native: ExternalCaseFinalizer | None,
    ) -> bytes:
        """Validate once, hold identity lock through native confirmation, return frozen bytes.

        No serialization or network operation follows final confirmation. Pool release
        and a fresh local expiry check are awaited before releasing the original bytes.
        """
        if native is None or type(frozen_projection) is not bytes:
            raise ExternalCaseError("unavailable")
        from maezo.portal.engine.profile import strict_loads

        schema = strict_loads(frozen_projection).get("schema")
        public = (
            parse(CasePage, frozen_projection)
            if schema == "portal-external-case-page.v1"
            else parse(CaseDetail, frozen_projection)
        )
        projection_digest = hashlib.sha256(frozen_projection).hexdigest()
        async with self.acquire(secret, expected) as locked:
            if isinstance(public, CasePage) and public.audience != locked.resolved.membership.audience:
                raise ExternalCaseError("denied")
            raw = await native.finalize(
                continuity_proof=continuity_proof,
                principal=locked.resolved.principal,
                audience=locked.resolved.membership.audience,
                public_projection_digest=projection_digest,
                session_valid_until=locked.resolved.record.expires_at,
            )
            receipt = parse(FinalizationReceipt, raw)
            if receipt.public_projection_digest != projection_digest:
                raise ExternalCaseError("conflict")
            ceiling = min(
                locked.valid_until, timestamp(public.freshness.valid_until), timestamp(receipt.valid_until)
            )
            if timestamp(public.freshness.valid_until) > ceiling:
                raise ExternalCaseError("conflict")
            if self.clock() >= ceiling:
                raise ExternalCaseError("denied")
        if self.clock() >= ceiling:
            raise ExternalCaseError("denied")
        return frozen_projection
