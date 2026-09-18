"""The production `IntakeAuthority`: published `resource_authority` heads, nothing else.

`IntakeAuthority` (`models.py`) had no production implementer anywhere in `src/`, so
`intake_service_factory` / `intake_recovery_factory` could not be bound by the human
composition root (WP-J1-00 report §2.1). This is that implementer.

It decides nothing. The `ResourceAuthority` head IS the delegation contract — scope,
validity window, revocation, consent state and legal basis are all published fields — so
the authority's whole job is to *read* the contract and refuse whenever it does not cover
the exact request. Writing a grant rule here in Python would both re-decide a ratified
contract and put a business rule in code; neither happens.

Fail-closed everywhere: no authority, more than one matching authority, a mismatched
party, an expired window, a revoked consent, or a selected document without verified
custody all refuse. Absence of a published row is a refusal, never an allowance.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import ValidationError

from maezo.gateway.human.auth_profile import ResourceAuthority
from maezo.gateway.human.auth_transport import AuthUnavailableError
from maezo.gateway.human.read_profile import digest
from maezo.portal.contracts.intake import AuthIntakeSubmission, IntakeLinks
from maezo.portal.contracts.models import HumanPrincipal

from .links import Audience, IntakeLinkSource, actor_matches, project_links
from .models import AdmissionGrant, IntakeAuthority, IntakeError, request_bytes
from .native_authority import AuthorityAction, NativeAuthReader, PublishedAuthorities


class PublishedIntakeAuthority:
    """`IntakeAuthority` + `IntakeLinkSource` over the published AUTH input heads."""

    def __init__(self, reader: NativeAuthReader) -> None:
        if type(reader) is not NativeAuthReader:
            raise AuthUnavailableError()
        self.reader = reader

    @property
    def tenant(self) -> str:
        return self.reader.binding.scope.tenant

    def _principal(self, principal: HumanPrincipal) -> str:
        # A principal from another tenant is not weakly authorized, it is out of scope
        # for this installation entirely.
        if type(principal) is not HumanPrincipal or principal.tenant != self.tenant:
            raise IntakeError("operation_forbidden")
        return principal.principal_ref

    @staticmethod
    def _covers(authority: ResourceAuthority, principal: HumanPrincipal, now: datetime) -> bool:
        """The published contract's own validity — identical to the sanctioned effect path.

        Actor identity is the single `actor_matches` predicate `project_links` also uses
        (`links.py`), so the two consumers of a published `Actor` row can never drift.
        `admit`/`read` have no caller-supplied audience to match against — `HumanPrincipal`
        carries none — so the floor is `audience=None`: a row naming a `staff` actor is
        refused here exactly as it is structurally excluded from `links`, since staff hold
        no vínculo anywhere in this plane (ADR-0049 D4).
        """
        return (
            actor_matches(authority.actor, principal, None)
            and authority.state == "active"
            and authority.consent_state != "revoked"
            and not (authority.legal_basis == "consent" and authority.consent_state != "valid")
            and authority.valid_from <= now < authority.valid_until
        )

    async def _published(
        self, principal: HumanPrincipal, action: AuthorityAction, documents: tuple[str, ...] = ()
    ) -> PublishedAuthorities:
        try:
            return await self.reader.published_authorities(
                self._principal(principal), action=action, documents=documents
            )
        except IntakeError:
            raise
        except AuthUnavailableError:
            raise IntakeError() from None

    async def admit(self, principal: HumanPrincipal, request: AuthIntakeSubmission) -> AdmissionGrant:
        """Exactly one published `auth.start` authority must cover this exact submission."""
        if type(request) is not AuthIntakeSubmission:
            raise IntakeError("invalid_request")
        documents = tuple(request.document_refs)
        published = await self._published(principal, "auth.start", documents)
        now = datetime.now(UTC)
        matches = [
            (authority, ceiling)
            for authority, ceiling in published.authorities
            if authority.resource_kind == "guide"
            and authority.resource_ref == request.guide_ref
            and authority.beneficiary_ref == request.beneficiary_ref
            and authority.provider_ref == request.provider_ref
            and self._covers(authority, principal, now)
            and now < ceiling
        ]
        # Ambiguity is refused, never resolved by preference: two authorities over one
        # guide mean the installation is inconsistent, not that the principal has more power.
        if len(matches) != 1:
            raise IntakeError("operation_forbidden")
        authority, ceiling = matches[0]
        # Every selected document must carry verified, clean, available, unexpired custody.
        custody_by_ref = {
            custody.document.document_ref: (custody, deadline) for custody, deadline in published.custodies
        }
        if set(custody_by_ref) != set(documents):
            raise IntakeError("operation_forbidden")
        for custody, deadline in custody_by_ref.values():
            if (
                custody.custody_state != "available"
                or custody.screening_result != "clean"
                or now >= min(custody.valid_until, deadline)
            ):
                raise IntakeError("operation_forbidden")
            ceiling = min(ceiling, custody.valid_until, deadline)
        valid_until = min(ceiling, authority.valid_until, authority.source.valid_until, published.until)
        if now >= valid_until:
            raise IntakeError("operation_forbidden")
        try:
            return AdmissionGrant(
                principal=principal,
                request_digest=hashlib.sha256(request_bytes(request)).hexdigest(),
                guide_identity_ref=authority.resource_ref,
                authority_receipt_ref=authority.source.receipt_ref,
                authority_digest=digest(authority),
                valid_until=valid_until,
            )
        except ValidationError:
            # A published reference outside the closed contract shape is an installation
            # fault, not a weaker permission: refuse as a dependency, never as a grant.
            raise IntakeError() from None

    async def read(self, principal: HumanPrincipal, intake_ref: str) -> datetime:
        """Recheck the published read authority on every read; return its actual ceiling."""
        published = await self._published(principal, "auth.receipt.read")
        now = datetime.now(UTC)
        matches = [
            (authority, ceiling)
            for authority, ceiling in published.authorities
            if authority.resource_kind == "intake"
            and authority.resource_ref == intake_ref
            and self._covers(authority, principal, now)
            and now < ceiling
        ]
        if len(matches) != 1:
            raise IntakeError("operation_forbidden")
        authority, ceiling = matches[0]
        until = min(ceiling, authority.valid_until, authority.source.valid_until, published.until)
        if now >= until:
            raise IntakeError("operation_forbidden")
        return until

    async def links(self, principal: HumanPrincipal, audience: Audience) -> IntakeLinks:
        published = await self._published(principal, "auth.start")
        return project_links(principal, audience, published.authorities, datetime.now(UTC))


def _conformance(authority: PublishedIntakeAuthority) -> tuple[IntakeAuthority, IntakeLinkSource]:
    """Static proof that the production class satisfies both protocols; checked by mypy."""
    return authority, authority
