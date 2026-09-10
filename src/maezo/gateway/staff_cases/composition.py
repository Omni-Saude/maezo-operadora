"""Explicit first staff Cases composition; owner-installed capabilities required.

ROOT composes existing app/routes and native plugin registration separately. This
module constructs the actual service from qualified dependencies, without enabling
an absent source, inventing a policy grant, or falling back to external audiences.
"""
from __future__ import annotations

from dataclasses import dataclass

from maezo.portal.api.session import HumanSessionResolver

from .models import StaffCaseError
from .postgres import StaffSessionLease
from .publisher import StaffNativeClient, StaffWitnessSource
from .service import StaffCaseService


@dataclass(frozen=True)
class StaffCaseRuntime:
    sessions: StaffSessionLease
    witnesses: StaffWitnessSource
    native: StaffNativeClient

    def __post_init__(self) -> None:
        if (not isinstance(self.sessions, StaffSessionLease) or not isinstance(self.witnesses, StaffWitnessSource)
                or not isinstance(self.native, StaffNativeClient) or self.native.signer.role != "read_requester"):
            raise StaffCaseError("unavailable")
        if self.witnesses.signer.authority.installation_digest != self.native.authority.installation_digest:
            raise StaffCaseError("unavailable")

    def service(self, resolver: HumanSessionResolver) -> StaffCaseService:
        if not resolver.store.persistent:
            raise StaffCaseError("unavailable")
        return StaffCaseService(resolver, self.sessions, self.witnesses, self.native)

    async def close(self) -> None:
        # The HTTP pool is owned here. Shared identity/native SQL pools are borrowed
        # deployment resources, closed only by their ROOT lifecycle owner.
        await self.native.close()
