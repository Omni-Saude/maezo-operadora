"""Server-only intake authority and custody inputs. DTO construction is not proof."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field, field_validator

from maezo.portal.contracts.intake import AuthIntakeSubmission, Closed, IntakeReceipt, ResourceRef
from maezo.portal.contracts.models import HumanPrincipal, Sha256Digest
from maezo.portal.engine.profile import canonicalize


def request_bytes(request: AuthIntakeSubmission) -> bytes:
    """Private number-free profile; public integer schema version maps to decimal.

    All other values preserve their exact validated type; monetary values never
    traverse JSON Number. This versioned representation is the admitted digest.
    """
    value = request.model_dump(mode="json")
    value["schema_version"] = str(request.schema_version)
    return canonicalize(value)


class IntakeError(RuntimeError):
    def __init__(
        self,
        code: Literal[
            "invalid_request",
            "authentication_unavailable",
            "operation_forbidden",
            "resource_unavailable",
            "conflict",
            "dependency_unavailable",
        ] = "dependency_unavailable",
    ) -> None:
        self.code = code
        super().__init__("portal_intake_" + code)


class AdmissionGrant(Closed):
    principal: HumanPrincipal = Field(repr=False)
    request_digest: Sha256Digest
    guide_identity_ref: ResourceRef = Field(repr=False)
    authority_receipt_ref: ResourceRef
    authority_digest: Sha256Digest
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("aware deadline required")
        return value


class IntakeAuthority(Protocol):
    async def admit(self, principal: HumanPrincipal, request: AuthIntakeSubmission) -> AdmissionGrant:
        """Verify current explicit submit authority and all selected protected resources."""
        ...

    async def read(self, principal: HumanPrincipal, intake_ref: str) -> datetime:
        """Recheck resource authority, including after case closure; return actual ceiling."""
        ...


class IntakeStore(Protocol):
    async def admit(self, grant: AdmissionGrant, request: AuthIntakeSubmission) -> IntakeReceipt: ...
    async def read(self, tenant: str, principal_ref: str, intake_ref: str) -> IntakeReceipt: ...
