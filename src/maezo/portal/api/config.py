"""Deployment-owned Cognito configuration. No discovery or browser-selected endpoints."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PortalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAEZO_PORTAL_", extra="forbid", frozen=True)

    tenant: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    issuer: str
    cognito_origin: str
    client_id: str = Field(pattern=r"^[a-z0-9]{1,128}$")
    # Explicit deployment assertion, checked against a different configured M2M client.
    client_purpose: Literal["dedicated-human-code-pkce"]
    machine_client_id: str = Field(min_length=1)
    public_origin: str
    database_url: SecretStr | None = Field(default=None, repr=False)
    # Protected owner-bound AUTH participant, never a store/provider supplied by BFF.
    auth_lifecycle_binding_path: Path | None = Field(default=None, repr=False)
    mode: Literal["production", "local-test"] = "production"
    session_seconds: int = Field(default=1800, ge=60, le=3600)
    transaction_seconds: int = Field(default=300, ge=30, le=300)
    #: INTERIM (`docs/decisions-log.md` DL-0049). Turns on
    #: `POST /api/v1/portal/tasks/{id}/completion`, which closes an ESCALATION task over the
    #: engine's REST surface instead of the signed durable relay ADR-0049 D5/D6/D7 designs.
    #: OFF by default, and `scripts/ci/check_portal_direct_completion.py` reproves any
    #: `deploy/**` that turns it on outside `dev-sa-east-1`. Deleting this flag — with the
    #: relay of #427 in its place — is the intended end state.
    direct_completion: bool = False
    #: INTERIM (DL-0049). Exact HTTPS origins, comma separated, that may call this BFF with
    #: credentials. EMPTY by default, which is "no cross-origin caller at all": the portal is
    #: same-origin software and the declared-demo test channel
    #: (`platform/testchannel/paginas/escalonamento.html`) is the only reason this exists.
    #: Same CI fence as the flag above. Never a wildcard: `Access-Control-Allow-Origin: *`
    #: and credentials are mutually exclusive in the browser, and an echoed Origin would make
    #: any site a caller.
    cors_origins: str = ""
    #: The deployed capability profile, the same `MAEZO_PORTAL_CAPABILITIES` the production
    #: bootstrap reads. Projected on the session so the browser can tell "this area is not
    #: enabled here" from a real 503. It grants nothing: every route still refuses on its own.
    capabilities: Literal["identity", "identity,staff_cases", "identity,staff_cases,human"] = "identity"

    @model_validator(mode="after")
    def _deployment_boundaries(self) -> Self:
        if not re.fullmatch(
            r"https://cognito-idp\.[a-z]{2}(?:-[a-z]+)+-\d\.amazonaws\.com/"
            r"[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+",
            self.issuer,
        ):
            raise ValueError("invalid Cognito issuer")
        issuer_region = urlsplit(self.issuer).netloc.split(".")[1]
        if urlsplit(self.issuer).path[1:].split("_")[0] != issuer_region:
            raise ValueError("Cognito region mismatch")
        for origin in (self.public_origin, self.cognito_origin):
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or origin != f"https://{parsed.netloc}"
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in origin)
                or any(c in origin for c in "?#\\")
            ):
                raise ValueError("invalid fixed HTTPS origin")
        if self.client_id == self.machine_client_id:
            raise ValueError("human client must be separate")
        if self.mode == "production" and self.database_url is None:
            raise ValueError("persistent session database required")
        if (
            self.auth_lifecycle_binding_path is not None
            and not self.auth_lifecycle_binding_path.is_absolute()
        ):
            raise ValueError("absolute protected AUTH binding path required")
        # Fail at boot, not at the first preflight: a malformed allowlist is a deployment
        # defect, and a portal that starts with an unreadable one would be a portal whose
        # cross-origin policy nobody can state.
        self.allowed_cross_origins()
        return self

    def allowed_cross_origins(self) -> tuple[str, ...]:
        """The parsed `cors_origins` allowlist, in declaration order, or `()`.

        Every entry is held to the SAME shape as `public_origin` (HTTPS, DNS only, no path,
        port, query, fragment or credential), must be distinct, must not be the portal's own
        origin (that one is always allowed and does not need CORS) and must not be the Cognito
        origin (the IdP does not call this BFF). A wildcard, a scheme-only value, `null` or an
        empty element is a configuration error, not an "allow everything".
        """
        raw = self.cors_origins.strip()
        if not raw:
            return ()
        entries = tuple(part.strip() for part in raw.split(","))
        if len(entries) > 4 or len(set(entries)) != len(entries):
            raise ValueError("invalid portal CORS allowlist")
        for origin in entries:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or "*" in origin
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or origin != f"https://{parsed.netloc}"
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in origin)
                or any(c in origin for c in "?#\\")
                or origin in (self.public_origin, self.cognito_origin)
            ):
                raise ValueError("invalid portal CORS allowlist")
        return entries

    @property
    def callback_url(self) -> str:
        return f"{self.public_origin}/api/v1/portal/auth/callback"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"
