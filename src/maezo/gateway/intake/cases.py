"""W6 case service: observe, freeze, current identity/native finalize, release bytes.

No general staff projection is inferred from external disclosure grants. The
qualified W6 native transport is a separate composition obligation.
"""

from datetime import datetime
from typing import Any, Protocol

from maezo.gateway.external_cases.models import (
    KINDS,
    MAX,
    CaseDetail,
    CasePage,
    ExternalCaseError,
    parse,
)
from maezo.gateway.external_cases.postgres import (
    ExternalCaseFinalizer,
    PostgresExternalFinalizationSessionLease,
)
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.contracts.models import HumanPrincipal
from maezo.portal.engine.profile import canonicalize, strict_loads


class CaseReader(ExternalCaseFinalizer, Protocol):
    async def observe(
        self,
        *,
        principal: HumanPrincipal,
        audience: str,
        session_valid_until: datetime,
        operation: str,
        query: dict[str, Any],
    ) -> bytes: ...


class CaseService:
    def __init__(
        self,
        resolver: HumanSessionResolver,
        lease: PostgresExternalFinalizationSessionLease,
        native: CaseReader,
    ) -> None:
        if resolver.settings.tenant != lease.tenant or resolver.settings.issuer != lease.issuer:
            raise ExternalCaseError("unavailable")
        self.resolver, self.lease, self.native = resolver, lease, native

    async def read(
        self,
        secret: str,
        *,
        case_ref: str | None = None,
        kind: str | None = None,
        limit: str = "25",
        cursor: str | None = None,
    ) -> bytes:
        session = await self.resolver.resolve(secret)
        audience = session.membership.audience
        if audience not in KINDS:
            raise ExternalCaseError("denied")
        if kind is not None and kind not in KINDS[audience]:
            raise ExternalCaseError("denied")
        operation = "detail" if case_ref is not None else "list"
        query: dict[str, Any] = (
            {"case_ref": case_ref}
            if case_ref is not None
            else {"kind": kind, "limit": limit, "cursor": cursor}
        )
        raw = await self.native.observe(
            principal=session.principal,
            audience=audience,
            session_valid_until=min(session.record.expires_at, session.membership.reviewed_until),
            operation=operation,
            query=query,
        )
        if type(raw) is not bytes or len(raw) > MAX:
            raise ExternalCaseError("invalid")
        value = strict_loads(raw)
        if (
            type(value) is not dict
            or set(value) != {"schema", "projection", "continuity_proof"}
            or value["schema"] != "portal-external-case-observation.v1"
        ):
            raise ExternalCaseError("invalid")
        proof = value["continuity_proof"]
        if type(proof) is not str or not proof or len(proof) > MAX:
            raise ExternalCaseError("invalid")
        frozen = canonicalize(value["projection"])
        public = parse(CaseDetail, frozen) if case_ref is not None else parse(CasePage, frozen)
        if isinstance(public, CaseDetail) and (
            public.case.case_ref != case_ref or public.case.kind not in KINDS[audience]
        ):
            raise ExternalCaseError("denied")
        if isinstance(public, CasePage) and (
            public.audience != audience
            or len(public.items) > int(limit)
            or any(kind is not None and item.kind != kind for item in public.items)
        ):
            raise ExternalCaseError("denied")
        return await self.lease.finalize_frozen(
            secret, session.principal, continuity_proof=proof, frozen_projection=frozen, native=self.native
        )
