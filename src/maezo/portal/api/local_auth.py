"""Fixed-identity OIDC stub for the local synthetic browser journey (WP-J1-10).

WHAT THIS IS. The missing local half of the `HumanAuthenticator` port
(`maezo.gateway.oidc.HumanAuthenticator`): a stub that implements exactly the same
Protocol — `exchange`/`close`, returning a `VerifiedIdentity`, never a token — so the
e2e journey (`scripts/dev/run_portal_journey.py`) can drive a real browser through the
real BFF login/callback/session flow without a Cognito tenant. Only the *identity
source* is synthetic: everything downstream of `exchange` is the production code path
(transaction state/browser cookies, code single-claim, session issuance, membership
resolution), which is precisely the slice the journey has to prove.

WHAT THIS IS NOT. It is not a second identity provider, not a development Cognito, and
not a production fallback: the constructor refuses any settings whose `mode` is not
`local-test`, and `create_production_app` (`maezo.portal.api.production`) requires
`mode == "production"` before it composes anything, so no production composition can
ever bind this class. It never reads a secret, opens a network client, or attaches a
token to anything it returns — the same boundary `CognitoAuthenticator` documents, held
by construction here. Membership still has to exist for the returned identity: the
journey seeds an explicit `LocalTestIdentityStore` membership, and `HumanSessionService`
refuses a login whose identity has no live reviewed membership.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from maezo.gateway.oidc import AuthenticationError, VerifiedIdentity
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.records import LoginTransaction

#: Deterministic, deliberately LOW-ENTROPY construction (never a token-shaped literal).
#: The code is worthless outside the process that seeded the matching membership.
STUB_AUTHORIZATION_CODE = "code-" + "q" * 28

#: Fixed synthetic identity the stub vouches for. The issuer is the same synthetic pool
#: shape the unit suite uses; it is not a Cognito tenant and resolves no network call.
STUB_ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SyntheticJourneyPool"
STUB_SUBJECT = "00000000-0000-4000-8000-00000000000a10"

#: Verified-identity lifetime handed to the session service, which clamps the issued
#: session to `min(identity.expires_at, membership.reviewed_until, session_seconds)`.
STUB_IDENTITY_LIFETIME = timedelta(hours=8)


class StubAuthenticator:
    """`HumanAuthenticator` for the local journey; fixed code in, fixed identity out."""

    def __init__(
        self,
        settings: PortalSettings,
        *,
        code: str = STUB_AUTHORIZATION_CODE,
        issuer: str = STUB_ISSUER,
        subject: str = STUB_SUBJECT,
        lifetime: timedelta = STUB_IDENTITY_LIFETIME,
    ) -> None:
        if settings.mode != "local-test":
            raise ValueError("stub authenticator requires local-test mode")
        if not code or len(code) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in code):
            raise ValueError("invalid stub authorization code")
        if lifetime <= timedelta(0):
            raise ValueError("stub identity lifetime must be positive")
        self._code = code
        self._lifetime = lifetime
        self._identity = VerifiedIdentity(
            issuer=issuer,
            subject=subject,
            authenticated_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + lifetime,
        )
        self.exchanges = 0

    async def exchange(self, code: str, transaction: LoginTransaction) -> VerifiedIdentity:
        if not secrets.compare_digest(code, self._code):
            raise AuthenticationError()
        self.exchanges += 1
        return self._identity

    async def close(self) -> None:
        return None
