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
        return self

    @property
    def callback_url(self) -> str:
        return f"{self.public_origin}/api/v1/portal/auth/callback"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"
