"""Projection of published `resource_authority` heads into the closed browser link DTO.

A link is a *projection* of a delegation the server already holds, never a grant the
browser may assert back. Nothing here decides eligibility, coverage or carência: the
authority rows are the explicit delegation contract (scope, validity, revocation,
consent state) and this module only re-reads and filters them.

Two independent conditions must both hold before a delegation becomes a link:
the published authority must name this exact principal as its actor, **and** the
principal's own server-verified `subject_bindings` must cover the delegated party.
Identity alone (session, role, Cognito group, possession of a reference) is never a
vínculo — ADR-0049 D4.

The filter mirrors the sanctioned effect path (`NativeAuthReader.observation`) check for
check, so the route can never display a link that the submit path would refuse.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import TypeAdapter, ValidationError

from maezo.gateway.human.auth_profile import Actor, ResourceAuthority
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.intake import IntakeLink, IntakeLinks, ResourceRef
from maezo.portal.contracts.models import HumanPrincipal

from .models import IntakeError
from .service import session_ceiling

# The single fixed identity of the development-only synthetic input publisher.
# `scripts/dev/publish_auth_synthetic_inputs.py` stamps exactly this value into every
# `SourceProvenance.publisher_ref` it writes, and it is the ONLY value that may ever be
# projected as `provenance_kind="synthetic"`. It is deliberately a literal shared by the
# script and this projection so the label cannot drift between publisher and display.
SYNTHETIC_PUBLISHER_REF = "maezo-dev-synthetic-auth-input-publisher-v1"

_REFERENCE = TypeAdapter(ResourceRef)


def provenance_kind(publisher_ref: str) -> Literal["synthetic", "attested"]:
    """Derive the provenance label from the publisher alone — no default, no inference.

    An absent or blank publisher reference is not labellable, so it raises instead of
    falling back to either label. `synthetic` is reserved for the one development
    publisher; every other publisher is an attested source by construction, because the
    only way a head row exists at all is a `human-auth-input-publication` key whose
    installed source grant covers that exact (kind, source, publisher, resource).
    """
    if not publisher_ref:
        raise IntakeError()
    return "synthetic" if publisher_ref == SYNTHETIC_PUBLISHER_REF else "attested"


def _reference(value: str) -> str | None:
    try:
        return str(_REFERENCE.validate_python(value))
    except ValidationError:
        return None


Audience = Literal["beneficiary", "provider"]


def actor_matches(actor: Actor, principal: HumanPrincipal, audience: Audience | None) -> bool:
    """The single identity check every published-authority consumer must use.

    Both `admit`/`read` (`published_authority.py`) and this module's `project_links` need
    to decide whether a published `Actor` row names a given principal; before this helper
    they did it with two independently written comparisons, and only one of them compared
    `actor.audience` — a published row naming a `staff` actor was silently honoured by
    `admit`/`read` while `links` structurally could never receive `audience="staff"` to
    match against. One function now settles both:

    - `audience` given (the caller's own server-verified session audience, never
      `"staff"` — `links` is never called for a staff session): the actor's `audience`
      must equal it exactly, matching this exact delegation side.
    - `audience=None` (`admit`/`read`, which have no caller audience to compare — a
      `HumanPrincipal` carries none): the floor is that the actor must not be `"staff"`.
      Staff hold no vínculo anywhere in this plane (ADR-0049 D4); a row naming one is
      never a submit or read grant either.
    """
    if (actor.principal_ref, actor.issuer, actor.subject, actor.membership_revision) != (
        principal.principal_ref,
        principal.issuer,
        principal.subject,
        principal.membership_revision,
    ):
        return False
    if audience is None:
        return actor.audience != "staff"
    return actor.audience == audience


@runtime_checkable
class IntakeLinkSource(Protocol):
    async def links(self, principal: HumanPrincipal, audience: Audience) -> IntakeLinks:
        """Re-read the published authorities of this principal; never a cache, never a default.

        The audience is supplied by the caller from the server-verified membership record,
        never derived from the principal DTO and never chosen by the browser. Its type
        excludes `staff` structurally: staff hold no vínculo and are refused before this call.
        """
        ...


def project_links(
    principal: HumanPrincipal,
    audience: Audience,
    authorities: tuple[tuple[ResourceAuthority, datetime], ...],
    now: datetime,
) -> IntakeLinks:
    """Pure projection. A candidate that does not fully verify is omitted, never softened."""
    beneficiary_bindings = frozenset(
        b.resource_ref for b in principal.subject_bindings if b.kind == "beneficiary"
    )
    provider_bindings = frozenset(b.resource_ref for b in principal.subject_bindings if b.kind == "provider")
    links: list[IntakeLink] = []
    seen: set[tuple[str, str]] = set()
    for authority, ceiling in authorities:
        if not actor_matches(authority.actor, principal, audience):
            continue
        if (
            authority.state != "active"
            or authority.action != "auth.start"
            or authority.consent_state == "revoked"
            or (authority.legal_basis == "consent" and authority.consent_state != "valid")
            or not authority.valid_from <= now < authority.valid_until
            or now >= ceiling
        ):
            continue
        # The authority names the delegated parties; the principal's own verified
        # relationships must independently cover its own side of that delegation.
        if audience == "beneficiary":
            if authority.beneficiary_ref not in beneficiary_bindings:
                continue
        elif authority.provider_ref is None or authority.provider_ref not in provider_bindings:
            continue
        beneficiary_ref = _reference(authority.beneficiary_ref)
        resource_ref = _reference(authority.resource_ref)
        provider_ref = None if authority.provider_ref is None else _reference(authority.provider_ref)
        if (
            beneficiary_ref is None
            or resource_ref is None
            or (authority.provider_ref is not None and provider_ref is None)
        ):
            continue
        key = (authority.resource_kind, resource_ref)
        if key in seen:
            continue
        seen.add(key)
        links.append(
            IntakeLink(
                beneficiary_ref=beneficiary_ref,
                provider_ref=provider_ref,
                resource_kind=authority.resource_kind,
                resource_ref=resource_ref,
                action="auth.start",
                valid_until=min(authority.valid_until, ceiling),
                provenance_kind=provenance_kind(authority.source.publisher_ref),
            )
        )
    return IntakeLinks(links=tuple(sorted(links, key=lambda link: (link.resource_kind, link.resource_ref))))


class IntakeLinkService:
    """Session-bounded read of the current links; re-resolves the session around the read."""

    def __init__(self, resolver: HumanSessionResolver, source: IntakeLinkSource) -> None:
        self.resolver, self.source = resolver, source

    async def _session(self, secret: str) -> ResolvedHumanSession:
        try:
            session = await self.resolver.resolve(secret)
            if datetime.now(UTC) >= session_ceiling(session):
                raise IntakeError("authentication_unavailable")
            return session
        except Exception:
            raise IntakeError("authentication_unavailable") from None

    async def read(self, secret: str) -> IntakeLinks:
        return await self.read_frozen(secret, freeze=lambda value: value)

    async def read_frozen[T](self, secret: str, *, freeze: Callable[[IntakeLinks], T]) -> T:
        first = await self._session(secret)
        audience = first.membership.audience
        if audience == "staff":
            raise IntakeError("operation_forbidden")
        result = await self.source.links(first.principal, audience)
        if type(result) is not IntakeLinks:
            raise IntakeError()
        # Revalidated per request: a session, membership or authority revoked while the
        # rows were read must not be served (ADR-0049 D4; portal/api/session.py:65-66).
        current = await self._session(secret)
        if current.principal != first.principal or current.membership.audience != first.membership.audience:
            raise IntakeError("operation_forbidden")
        frozen = freeze(result)
        if datetime.now(UTC) >= min(session_ceiling(first), session_ceiling(current)):
            raise IntakeError("operation_forbidden")
        return frozen
