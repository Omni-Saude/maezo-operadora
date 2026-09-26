"""Server-only records; membership is provisioned by an authorized human administration plane."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from maezo.portal.contracts.models import MembershipBinding, OpaqueRef, SubjectBinding


class PrivateRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class MembershipRecord(PrivateRecord):
    tenant: OpaqueRef
    issuer: str = Field(repr=False)
    subject: OpaqueRef = Field(repr=False)
    principal_ref: OpaqueRef
    revision: int = Field(ge=0)
    audience: Literal["staff", "beneficiary", "provider"]
    memberships: tuple[MembershipBinding, ...]
    subject_bindings: tuple[SubjectBinding, ...]
    # A reviewed record expires. There is no auto-ratification or browser provisioning.
    reviewed_until: datetime
    revoked: bool = False

    @model_validator(mode="after")
    def _require_authority(self) -> Self:
        if not self.memberships or not any(m.roles for m in self.memberships):
            raise ValueError("membership required")
        if self.reviewed_until.tzinfo is None:
            raise ValueError("aware review deadline required")
        if self.audience != "staff" and not any(
            binding.kind == self.audience for binding in self.subject_bindings
        ):
            raise ValueError("verified subject relationship required")
        if self.audience == "beneficiary" and any(b.kind != "beneficiary" for b in self.subject_bindings):
            raise ValueError("incompatible subject relationship")
        if self.audience == "provider" and any(b.kind != "provider" for b in self.subject_bindings):
            raise ValueError("incompatible subject relationship")
        return self


class LoginTransaction(PrivateRecord):
    state_hash: str = Field(repr=False)
    browser_hash: str = Field(repr=False)
    nonce: str = Field(repr=False)
    verifier: str = Field(repr=False)
    created_at: datetime
    expires_at: datetime
    return_path: Literal["/", "/portal", "/portal/"]


class SessionRecord(PrivateRecord):
    secret_hash: str = Field(repr=False)
    session_ref: str
    csrf_token: str = Field(repr=False)
    issuer: str = Field(repr=False)
    subject: str = Field(repr=False)
    principal_ref: str
    membership_revision: int
    authenticated_at: datetime
    expires_at: datetime


class LogoutDTO(PrivateRecord):
    """Local session is gone; the browser still has to end the Hosted UI session at the IdP.

    `idp_logout_url` is built from deployment configuration only (Cognito origin, human client id,
    public origin) and carries no secret.
    """

    schema_version: Literal[1] = 1
    idp_logout_url: str


class SessionDTO(PrivateRecord):
    """Minimal browser projection, never a principal accepted back as authority."""

    schema_version: Literal[1] = 1
    principal_ref: str
    audience: Literal["staff", "beneficiary", "provider"]
    roles: tuple[str, ...]
    expires_at: datetime
    csrf_token: str = Field(repr=False)
    #: Canonical order; informative only, never an authorization input.
    capabilities: tuple[Literal["identity", "staff_cases", "human"], ...]
