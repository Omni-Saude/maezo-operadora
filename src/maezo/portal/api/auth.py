"""Compatibility exports for the gateway-owned human authentication port (ADR-0049 D4)."""

from maezo.gateway.oidc import (
    AuthenticationError,
    CognitoAuthenticator,
    HumanAuthenticator,
    VerifiedIdentity,
    authorization_url,
    digest,
    opaque_ref,
    opaque_secret,
)

__all__ = [
    "AuthenticationError",
    "CognitoAuthenticator",
    "HumanAuthenticator",
    "VerifiedIdentity",
    "authorization_url",
    "digest",
    "opaque_ref",
    "opaque_secret",
]
