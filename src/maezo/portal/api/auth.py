"""Cognito Code + S256 PKCE and fixed-key-source ID token verification (ADR-0049 D4)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from maezo.portal.api.config import PortalSettings
from maezo.portal.api.records import LoginTransaction


class AuthenticationError(Exception):
    """Static exception; never attach token, code, claims, endpoint response or chained error."""


@dataclass(frozen=True, repr=False)
class VerifiedIdentity:
    issuer: str
    subject: str
    authenticated_at: datetime
    expires_at: datetime


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def opaque_secret() -> str:
    return secrets.token_urlsafe(32)


def authorization_url(settings: PortalSettings, transaction: LoginTransaction, state: str) -> str:
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(transaction.verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{settings.cognito_origin}/oauth2/authorize?" + urlencode(
        {
            "client_id": settings.client_id,
            "response_type": "code",
            "scope": "openid",
            "redirect_uri": settings.callback_url,
            "state": state,
            "nonce": transaction.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


class CognitoAuthenticator:
    """No token-driven jku/x5u/discovery. HTTPS and redirects/proxies disabled by factory.

    Client injection is for explicit local ASGI/crypto tests only. Returned OAuth tokens are
    verified or discarded server-side; refresh is deliberately unsupported and expiry requires
    a new authorization transaction. No bearer token is persisted or sent to the browser.
    """

    def __init__(self, settings: PortalSettings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self._client = client

    async def _json(self, method: str, url: str, data: dict[str, str] | None = None) -> object:
        allowed = {self.settings.jwks_url, f"{self.settings.cognito_origin}/oauth2/token"}
        if url not in allowed:
            raise AuthenticationError()
        try:
            async with self._client.stream(
                method,
                url,
                data=data,
                follow_redirects=False,
                headers={"Accept": "application/json"},
                timeout=10.0,
            ) as response:
                if response.status_code != 200:
                    raise AuthenticationError()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise AuthenticationError()
                return json.loads(body)
        except (httpx.HTTPError, ValueError):
            raise AuthenticationError() from None

    async def exchange(self, code: str, transaction: LoginTransaction) -> VerifiedIdentity:
        try:
            tokens = await self._json(
                "POST",
                f"{self.settings.cognito_origin}/oauth2/token",
                {
                    "grant_type": "authorization_code",
                    "client_id": self.settings.client_id,
                    "redirect_uri": self.settings.callback_url,
                    "code": code,
                    "code_verifier": transaction.verifier,
                },
            )
            if not isinstance(tokens, dict) or not isinstance(tokens.get("id_token"), str):
                raise AuthenticationError()
            token = tokens["id_token"]
            if len(token) > 16384:
                raise AuthenticationError()
            header = jwt.get_unverified_header(token)
            if (
                header.get("alg") != "RS256"
                or not isinstance(header.get("kid"), str)
                or not header["kid"]
                or any(k in header for k in ("jku", "x5u", "jwk", "crit"))
            ):
                raise AuthenticationError()
            jwks = await self._json("GET", self.settings.jwks_url)
            if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
                raise AuthenticationError()
            keys = [k for k in jwks["keys"] if isinstance(k, dict) and k.get("kid") == header["kid"]]
            if len(keys) != 1:
                raise AuthenticationError()
            key = keys[0]
            if (
                key.get("kty") != "RSA"
                or key.get("use") != "sig"
                or key.get("alg") != "RS256"
                or "d" in key
                or key.get("key_ops", ["verify"]) != ["verify"]
            ):
                raise AuthenticationError()
            public_key = jwt.PyJWK.from_dict(key, algorithm="RS256")
            if not isinstance(public_key.key, RSAPublicKey) or public_key.key.key_size < 2048:
                raise AuthenticationError()
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                issuer=self.settings.issuer,
                audience=self.settings.client_id,
                options={
                    "require": ["iss", "aud", "sub", "exp", "iat", "auth_time", "nonce", "token_use"],
                    "strict_aud": True,
                },
            )
            now = datetime.now(UTC).timestamp()
            if (
                claims["iss"] != self.settings.issuer
                or claims["aud"] != self.settings.client_id
                or claims["token_use"] != "id"
                or not isinstance(claims["nonce"], str)
                or not secrets.compare_digest(claims["nonce"], transaction.nonce)
                or not isinstance(claims["sub"], str)
                or not claims["sub"]
                or len(claims["sub"]) > 512
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "/?#" for c in claims["sub"])
                or ("azp" in claims and claims["azp"] != self.settings.client_id)
                or any(type(claims[k]) is not int for k in ("exp", "iat", "auth_time"))
                or not 0 < claims["auth_time"] <= claims["iat"] <= now
                or not claims["iat"] < claims["exp"]
                or claims["iat"] < transaction.created_at.timestamp() - 60
                or ("nbf" in claims and type(claims["nbf"]) is not int)
            ):
                raise AuthenticationError()
            return VerifiedIdentity(
                self.settings.issuer,
                claims["sub"],
                datetime.fromtimestamp(claims["auth_time"], UTC),
                datetime.fromtimestamp(claims["exp"], UTC),
            )
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise AuthenticationError() from None

    async def close(self) -> None:
        await self._client.aclose()
