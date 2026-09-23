"""SC1 actual identity-session locks and qualified native membership observations.

The existing identity function remains unchanged. Its separately installed staff
login mapping/EXECUTE grant and read-only native relation pins are deployment inputs.
No external audience is reinterpreted, and no membership revision is invented.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.gateway.external_cases.models import MAX, Scope, now_utc
from maezo.gateway.external_cases.postgres import transaction
from maezo.gateway.human.read_profile import MembershipProjection, SourceProvenance, digest, parse_model, utc
from maezo.portal.api.auth import digest as secret_digest
from maezo.portal.api.records import MembershipRecord, SessionRecord
from maezo.portal.api.session import ResolvedHumanSession
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import strict_loads

from .models import StaffCaseError
from .production_config import is_native_schema


def validate_session(
    row: Mapping[str, Any], *, tenant: str, issuer: str, hashed: str, expected: HumanPrincipal, now: datetime
) -> ResolvedHumanSession:
    try:
        for value in (row["session_payload"], row["membership_payload"]):
            if type(value) is not str or len(value.encode()) > MAX:
                raise ValueError

            # Session storage is its existing typed JSON, including integer revisions.
            def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, item in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = item
                return result

            json.loads(value, object_pairs_hook=unique)
        session = SessionRecord.model_validate_json(row["session_payload"])
        member = MembershipRecord.model_validate_json(row["membership_payload"])
        if (
            member.audience != "staff"
            or member.revoked
            or member.tenant != tenant
            or session.issuer != issuer
            or member.issuer != issuer
            or session.secret_hash != hashed
            or session.subject != member.subject
            or session.principal_ref != member.principal_ref
            or session.membership_revision != member.revision
            or session.expires_at <= now
            or member.reviewed_until <= now
            or row["session_tenant"] != tenant
            or row["membership_tenant"] != tenant
            or row["secret_hash"] != hashed
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
        return ResolvedHumanSession(principal, session, member)
    except Exception:
        raise StaffCaseError("denied") from None


@dataclass(frozen=True, repr=False)
class SessionLock:
    resolved: ResolvedHumanSession
    valid_until: datetime
    live: Callable[[], bool]

    def current(self, now: datetime) -> None:
        if not self.live() or now >= self.valid_until:
            raise StaffCaseError("unavailable")


class StaffSessionLease:
    def __init__(
        self,
        engine: AsyncEngine,
        tenant: str,
        issuer: str,
        *,
        seconds: float = 5,
        clock: Callable[[], datetime] = now_utc,
    ):
        qualify_engine(engine, seconds)
        self.engine, self.tenant, self.issuer, self.seconds, self.clock = (
            engine,
            tenant,
            issuer,
            seconds,
            clock,
        )

    @asynccontextmanager
    async def acquire(self, secret: str, expected: HumanPrincipal) -> AsyncIterator[SessionLock]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
            raise StaffCaseError("denied")
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
            resolved = validate_session(
                dict(row),
                tenant=self.tenant,
                issuer=self.issuer,
                hashed=hashed,
                expected=expected,
                now=self.clock(),
            )
            locked = SessionLock(
                resolved,
                min(resolved.record.expires_at, resolved.membership.reviewed_until),
                connection.in_transaction,
            )
            locked.current(self.clock())
            yield locked
            locked.current(self.clock())
        if self.clock() >= locked.valid_until:
            raise StaffCaseError("unavailable")


def qualify_engine(engine: AsyncEngine, seconds: float) -> None:
    if (
        engine.dialect.name != "postgresql"
        or engine.echo
        or not engine.sync_engine.hide_parameters
        or not 0 < seconds <= 10
    ):
        raise StaffCaseError("unavailable")


@dataclass(frozen=True)
class RelationPin:
    oid: int
    owner: str

    def __post_init__(self) -> None:
        if (
            type(self.oid) is not int
            or self.oid < 1
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.owner)
        ):
            raise StaffCaseError("unavailable")


class NativeMembershipSource:
    """Actual SELECT-only native observations; native independently verifies Q2 proof.

    This reader signs no source grant. The installed identity-verifier signs its
    complete observed witness; Q2's actual publication proof/source is rechecked
    in the same native CommandContext before any staff data can be returned.
    """

    TABLES = frozenset({"mzo_portal_read_membership", "mzo_human_principal"})

    def __init__(
        self,
        engine: AsyncEngine,
        scope: Scope,
        login: str,
        pins: Mapping[str, RelationPin],
        *,
        native_schema: str,
        seconds: float = 5,
        clock: Callable[[], datetime] = now_utc,
    ):
        qualify_engine(engine, seconds)
        if (
            set(pins) != self.TABLES
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", login)
            or not is_native_schema(native_schema)
        ):
            raise StaffCaseError("unavailable")
        self.engine, self.scope, self.login, self.pins = engine, scope, login, dict(pins)
        self.seconds, self.clock = seconds, clock
        # ADR-0060 D3: the pinned schema (manifest v2), compared exactly (D5). It enters the
        # read only as a quoted identifier of a regex-closed name, never as free text.
        self.native_schema = native_schema
        schema = '"' + native_schema + '"'
        self.read_sql = f"""
                SELECT m.payload_,m.source_,h.* FROM {schema}.mzo_portal_read_membership m
                JOIN {schema}.mzo_human_principal h ON h.tenant_=m.tenant_ AND h.principal_=m.principal_
                WHERE m.tenant_=:tenant AND m.environment_=:environment AND m.engine_=:engine
                  AND m.incarnation_=:incarnation AND m.principal_=:principal
            """

    async def require_pins(self, connection: Any) -> None:
        """Actual login and the exact pinned relations of the pinned schema, or refuse.

        ``relkind`` is the one-byte ``"char"`` type, which asyncpg decodes as ``bytes``
        (``b'r'``); cast to text or the comparison with ``"r"`` refuses every relation.

        The namespace is pinned as well: owned by the pinned relation owner (ADR-0060 D1,
        `maezo_native_schema_owner` owns the schema and its relations), with no CREATE for
        this login and none for PUBLIC.
        """
        users = (
            (
                await connection.execute(
                    text("SELECT session_user::text AS actual,current_user::text AS effective")
                )
            )
            .mappings()
            .one()
        )
        if users["actual"] != self.login or users["effective"] != self.login:
            raise StaffCaseError("unavailable")
        for name, pin in sorted(self.pins.items()):
            row = (
                (
                    await connection.execute(
                        text("""
                    SELECT c.oid, c.relkind::text AS relkind, c.relrowsecurity, c.relforcerowsecurity,
                      pg_get_userbyid(c.relowner) AS owner,
                      pg_has_role(session_user,c.relowner,'MEMBER') AS owner_member,
                      has_table_privilege(session_user,c.oid,'SELECT') AS rd,
                      has_table_privilege(session_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE') AS wr,
                      pg_get_userbyid(n.nspowner) AS schema_owner,
                      has_schema_privilege(session_user,n.oid,'CREATE') AS runtime_create,
                      EXISTS(SELECT 1 FROM aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
                             WHERE a.grantee=0 AND a.privilege_type='CREATE') AS public_create
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=:schema AND c.relname=:name
                """),
                        {"schema": self.native_schema, "name": name},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or row["oid"] != pin.oid
                or row["owner"] != pin.owner
                or row["relkind"] != "r"
                or row["relrowsecurity"]
                or row["relforcerowsecurity"]
                or row["owner_member"]
                or not row["rd"]
                or row["wr"]
                or row["schema_owner"] != pin.owner
                or row["runtime_create"] is not False
                or row["public_create"] is not False
            ):
                raise StaffCaseError("unavailable")

    async def observe(self, principal: HumanPrincipal) -> tuple[SourceProvenance, dict[str, Any], datetime]:
        if principal.tenant != self.scope.tenant:
            raise StaffCaseError("denied")
        args = dict(
            tenant=self.scope.tenant,
            environment=self.scope.environment,
            engine=self.scope.engine_name,
            incarnation=self.scope.database_incarnation,
            principal=principal.principal_ref,
        )
        async with transaction(self.engine, self.seconds) as connection:
            await self.require_pins(connection)
            # One statement snapshot binds current membership and actual native principal.
            row = (await connection.execute(text(self.read_sql), args)).mappings().one()
            member = parse_model(MembershipProjection, strict_loads(row["payload_"].encode()))
            source = parse_model(SourceProvenance, strict_loads(row["source_"].encode()))
            groups = strict_loads(row["groups_"].encode())
            valid = datetime.fromtimestamp(row["valid_until_"], UTC)
            native = dict(
                tenant=row["tenant_"],
                principal_ref=row["principal_"],
                issuer=row["issuer_"],
                subject=row["subject_"],
                revision=str(row["rev_"]),
                active=row["active_"],
                valid_until=utc(valid),
                groups=groups,
            )
            if (
                type(row["rev_"]) is not int
                or row["rev_"] < 0
                or row["active_"] is not True
                or type(groups) is not list
                or any(type(group) is not str for group in groups)
                or member.audience != "staff"
                or member.state != "active"
                or member.principal_ref != principal.principal_ref
                or member.issuer != principal.issuer
                or member.subject != principal.subject
                or member.membership_revision != principal.membership_revision
                or member.memberships != principal.memberships
                or member.subject_bindings != principal.subject_bindings
                or native["issuer"] != principal.issuer
                or native["subject"] != principal.subject
                or set(groups) != {group for binding in member.memberships for group in binding.groups}
            ):
                raise StaffCaseError("denied")
            until = min(valid, source.valid_until, member.reviewed_until)
            digest(native)  # Reject malformed or integer-leaking native codec before signing.
            if self.clock() >= until:
                raise StaffCaseError("unavailable")
        if self.clock() >= until:
            raise StaffCaseError("unavailable")
        return source, native, until
