"""Trusted resolver: deployment + server session + live reviewed membership, never browser DTO."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from maezo.portal.api.auth import (
    AuthenticationError,
    HumanAuthenticator,
    authorization_url,
    digest,
    opaque_ref,
    opaque_secret,
)
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.records import LoginTransaction, MembershipRecord, SessionDTO, SessionRecord
from maezo.portal.api.store import IdentityStore
from maezo.portal.contracts.models import HumanPrincipal


@dataclass(frozen=True, repr=False)
class ResolvedHumanSession:
    principal: HumanPrincipal
    record: SessionRecord
    membership: MembershipRecord

    def projection(self) -> SessionDTO:
        return SessionDTO(
            principal_ref=self.principal.principal_ref,
            audience=self.membership.audience,
            roles=tuple(sorted({r for m in self.membership.memberships for r in m.roles})),
            expires_at=self.record.expires_at,
            csrf_token=self.record.csrf_token,
        )


class HumanSessionResolver:
    """Gateway must call resolve again immediately before authorizing a command.

    This resolver proves current session/membership only. It grants no task action, clinical
    authority, resource ACL, credential or engine transport. Callers must compare the current
    membership revision to their expected revision and retain the separate enforcing gateway.
    """

    def __init__(self, settings: PortalSettings, store: IdentityStore) -> None:
        if store.tenant != settings.tenant or (settings.mode == "production" and not store.persistent):
            raise ValueError("invalid identity store boundary")
        self.settings = settings
        self.store = store

    async def membership(self, issuer: str, subject: str) -> MembershipRecord:
        record = await self.store.get_membership(issuer, subject)
        if (
            record is None
            or record.revoked
            or record.reviewed_until <= datetime.now(UTC)
            or record.tenant != self.settings.tenant
            or record.issuer != issuer
            or record.subject != subject
        ):
            raise AuthenticationError()
        return record

    async def resolve(self, session_secret: str) -> ResolvedHumanSession:
        if len(session_secret) != 43 or not all(c.isalnum() or c in "-_" for c in session_secret):
            raise AuthenticationError()
        session = await self.store.get_session(digest(session_secret), datetime.now(UTC))
        if (
            session is None
            or session.issuer != self.settings.issuer
            or session.secret_hash != digest(session_secret)
            or session.expires_at <= datetime.now(UTC)
        ):
            raise AuthenticationError()
        membership = await self.membership(session.issuer, session.subject)
        if (
            membership.revision != session.membership_revision
            or membership.principal_ref != session.principal_ref
        ):
            await self.store.revoke_session(session.secret_hash)
            raise AuthenticationError()
        principal = HumanPrincipal(
            schema_version=1,
            principal_ref=membership.principal_ref,
            issuer=session.issuer,
            subject=session.subject,
            tenant=self.settings.tenant,
            membership_revision=membership.revision,
            memberships=membership.memberships,
            session_ref=session.session_ref,
            authenticated_at=session.authenticated_at,
            subject_bindings=membership.subject_bindings,
        )
        return ResolvedHumanSession(principal, session, membership)

    def require_subject(self, resolved: ResolvedHumanSession, kind: str, resource_ref: str) -> None:
        """Resource handlers must use a freshly resolved session, then resource-specific ACLs."""
        if not any(
            b.kind == kind and b.resource_ref == resource_ref for b in resolved.principal.subject_bindings
        ):
            raise AuthenticationError()


class HumanSessionService:
    def __init__(self, resolver: HumanSessionResolver, authenticator: HumanAuthenticator) -> None:
        self.resolver = resolver
        self.settings = resolver.settings
        self.store = resolver.store
        self.authenticator = authenticator

    async def start(self, return_path: str) -> tuple[str, str]:
        if return_path not in ("/", "/portal", "/portal/"):
            raise AuthenticationError()
        state, browser = opaque_secret(), opaque_secret()
        now = datetime.now(UTC)
        tx = LoginTransaction.model_validate(
            {
                "state_hash": digest(state),
                "browser_hash": digest(browser),
                "nonce": opaque_secret(),
                "verifier": opaque_secret(),
                "created_at": now,
                "expires_at": now + timedelta(seconds=self.settings.transaction_seconds),
                "return_path": return_path,
            }
        )
        await self.store.purge_expired(now)
        await self.store.put_transaction(tx)
        return authorization_url(self.settings, tx, state), browser

    async def finish(self, state: str, code: str, browser: str, old_secret: str | None) -> tuple[str, str]:
        if (
            len(state) != 43
            or len(browser) != 43
            or not code
            or len(code) > 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in code)
        ):
            raise AuthenticationError()
        now = datetime.now(UTC)
        tx = await self.store.consume_transaction(digest(state), digest(browser), now)
        if tx is None:
            raise AuthenticationError()
        # Claim before network I/O. Failed/uncertain exchange burns this transaction and code.
        if not await self.store.claim_code(digest(code), now + timedelta(days=1)):
            raise AuthenticationError()
        identity = await self.authenticator.exchange(code, tx)
        membership = await self.resolver.membership(identity.issuer, identity.subject)
        secret = opaque_secret()
        session = SessionRecord(
            secret_hash=digest(secret),
            session_ref=opaque_ref(),
            csrf_token=opaque_secret(),
            issuer=identity.issuer,
            subject=identity.subject,
            principal_ref=membership.principal_ref,
            membership_revision=membership.revision,
            authenticated_at=identity.authenticated_at,
            expires_at=min(
                identity.expires_at,
                membership.reviewed_until,
                now + timedelta(seconds=self.settings.session_seconds),
            ),
        )
        if session.expires_at <= datetime.now(UTC):
            raise AuthenticationError()
        await self.store.put_session(session, digest(old_secret) if old_secret else None)
        return secret, tx.return_path

    async def logout(self, session_secret: str, csrf_token: str, origin: str) -> None:
        resolved = await self.resolver.resolve(session_secret)
        if (
            origin != self.settings.public_origin
            or not csrf_token
            or not secrets.compare_digest(csrf_token, resolved.record.csrf_token)
        ):
            raise AuthenticationError()
        await self.store.revoke_session(resolved.record.secret_hash)
