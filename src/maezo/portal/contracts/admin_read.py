"""Administrative READ contract (D-40) — closed projection of reviewed staff vínculos.

The staff `administration` area is READ-ONLY: this contract names what the browser may SEE
about the reviewed staff membership rows (`portal_memberships`, migration 0012) and NOTHING
it may change. Every field is a server-resolved reviewed fact — the projection re-reads the
same rows the session resolver already reads for every request — and the browser can never
assert any of them back:

- identity coordinates stay server-side: the IdP `issuer`/`subject` pair of a row is never
  projected (the same discipline as `SessionDTO`; `records.py` marks both `repr=False`);
- each vínculo is its principal reference plus the governed permissions the reviewed record
  already carries (`memberships` with roles/groups, `subject_bindings` per resource) and its
  review facts (`membership_revision`, `reviewed_until`, `revoked`);
- `state` is derived server-side from those facts at read time (`revoked` beats the review
  deadline; an unrevoked record past `reviewed_until` reads `expired`) — display only, never
  an authorization input: every route keeps refusing on its own published authority.

The fence stands: this contract never names a mutation, the BFF route that serves it lives in
`portal/api/` and never imports `maezo.portal/admin` ("Private reviewed human-administration
services; never public portal routes"), and the independently granted administrative
connection remains the only writer of membership facts (`portal/admin/assignments.py:1-8`).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from maezo.portal.contracts.models import OpaqueRef


class Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class StaffAdminMembership(Closed):
    """One governed permission block of the reviewed record, exactly as reviewed."""

    membership_ref: OpaqueRef
    roles: tuple[OpaqueRef, ...]
    groups: tuple[OpaqueRef, ...]


class StaffAdminBinding(Closed):
    """One server-verified resource relationship of the reviewed record (opaque reference)."""

    kind: Literal["beneficiary", "provider", "vendor"]
    resource_ref: OpaqueRef


class StaffAdminVinculo(Closed):
    """The reviewed vínculo of one staff principal, with its review facts. Read-only."""

    principal_ref: OpaqueRef
    membership_revision: int = Field(ge=0)
    memberships: tuple[StaffAdminMembership, ...]
    subject_bindings: tuple[StaffAdminBinding, ...]
    reviewed_until: datetime
    revoked: bool
    #: Server-derived at read time from `revoked` and `reviewed_until`. Never an input.
    state: Literal["active", "expired", "revoked"]


class StaffAdminVinculos(Closed):
    """The whole read-only roster answer: every reviewed staff vínculo of this tenant."""

    schema_version: Literal[1] = 1
    generated_at: datetime
    count: int = Field(ge=0)
    vinculos: tuple[StaffAdminVinculo, ...]

    @model_validator(mode="after")
    def _count_matches(self) -> "StaffAdminVinculos":
        if self.count != len(self.vinculos):
            raise ValueError("count must match the projected vínculos")
        refs = [vinculo.principal_ref for vinculo in self.vinculos]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate principal in roster")
        if refs != sorted(refs):
            raise ValueError("roster must be ordered by principal reference")
        return self


class PortalAdminReadError(Closed):
    code: Literal[
        "invalid_request",
        "authentication_unavailable",
        "operation_forbidden",
        "admin_read_unavailable",
    ]
