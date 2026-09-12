"""GET /api/v1/portal/intakes/auth/links — authority is never identity, and never the browser's.

Real ASGI app, real route class, real `IntakeLinkService`, real `project_links` projection and
real closed DTO. Only the head-reading source is a double: reading `mzo_auth_input_head` needs
PostgreSQL and is covered by the engine lane. Everything a browser can reach is exercised here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.unit.portal.test_human_session import ISSUER, PREFIX, SUBJECT, Harness, h, membership

from maezo.gateway.human.auth_profile import Actor, ResourceAuthority
from maezo.gateway.human.read_profile import SourceProvenance
from maezo.gateway.intake.links import SYNTHETIC_PUBLISHER_REF, IntakeLinkSource, project_links
from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.service import IntakeService
from maezo.portal.contracts.intake import IntakeLink, IntakeLinks
from maezo.portal.contracts.models import HumanPrincipal, SubjectBinding

__all__ = ["h"]
pytestmark = pytest.mark.asyncio

LINKS = PREFIX + "/intakes/auth/links"
HASH = "a" * 64
PRINCIPAL = "human-internal-1"
OTHER_PRINCIPAL = "human-internal-2"
BENEFICIARY = "beneficiary_synthetic_00001"
OTHER_BENEFICIARY = "beneficiary_synthetic_00002"
PROVIDER = "provider_synthetic_000001"
OTHER_PROVIDER = "provider_synthetic_000002"
GUIDE = "guide_synthetic_0000000001"
OTHER_GUIDE = "guide_synthetic_0000000002"


def now() -> datetime:
    return datetime.now(UTC)


def provenance(publisher: str = SYNTHETIC_PUBLISHER_REF, **changes: Any) -> SourceProvenance:
    values: dict[str, Any] = dict(
        publisher_ref=publisher,
        source_ref="synthetic-fixture-source",
        source_revision=1,
        source_digest=HASH,
        receipt_ref="synthetic-receipt",
        observed_at=now() - timedelta(minutes=5),
        valid_until=now() + timedelta(hours=2),
    )
    values.update(changes)
    return SourceProvenance(**values)


def actor(**changes: Any) -> Actor:
    values: dict[str, Any] = dict(
        principal_ref=PRINCIPAL,
        issuer=ISSUER,
        subject=SUBJECT,
        membership_revision=1,
        audience="provider",
    )
    values.update(changes)
    return Actor(**values)


def authority(**changes: Any) -> ResourceAuthority:
    values: dict[str, Any] = dict(
        authority_ref="authority-1",
        actor=actor(),
        beneficiary_ref=BENEFICIARY,
        provider_ref=PROVIDER,
        resource_kind="guide",
        resource_ref=GUIDE,
        action="auth.start",
        request_ref=None,
        relationship_revision=1,
        consent_revision=1,
        grant_ref="grant-1",
        basis_ref="basis-1",
        legal_basis="other_qualified_basis",
        consent_state="not_required",
        state="active",
        valid_from=now() - timedelta(hours=1),
        valid_until=now() + timedelta(hours=1),
        source=provenance(),
    )
    values.update(changes)
    return ResourceAuthority(**values)


class HeadSource:
    """Stands in for the PostgreSQL head read only; the projection under test is the real one."""

    def __init__(self, *authorities: ResourceAuthority, ceiling: datetime | None = None) -> None:
        self.rows = authorities
        self.ceiling = ceiling or (now() + timedelta(hours=4))
        self.calls: list[tuple[str, str]] = []

    async def admit(self, principal: Any, request: Any) -> Any:  # pragma: no cover - unused here
        raise IntakeError("operation_forbidden")

    async def read(self, principal: Any, intake_ref: str) -> datetime:  # pragma: no cover - unused
        raise IntakeError("operation_forbidden")

    async def links(self, principal: HumanPrincipal, audience: Any) -> IntakeLinks:
        self.calls.append((principal.principal_ref, audience))
        return project_links(principal, audience, tuple((a, self.ceiling) for a in self.rows), now())


class BlindAuthority:
    """A lawful `IntakeAuthority` that cannot read vínculos: a dependency gap, not an empty list."""

    async def admit(self, principal: Any, request: Any) -> Any:  # pragma: no cover - unused here
        raise IntakeError("operation_forbidden")

    async def read(self, principal: Any, intake_ref: str) -> datetime:  # pragma: no cover - unused
        raise IntakeError("operation_forbidden")


class Store:
    async def admit(self, grant: Any, request: Any) -> Any:  # pragma: no cover - unused here
        raise IntakeError("resource_unavailable")

    async def read(self, tenant: str, principal_ref: str, intake_ref: str) -> Any:  # pragma: no cover
        raise IntakeError("resource_unavailable")


def install(h: Harness, source: Any) -> None:
    h.app.state.intake_service_factory = lambda resolver: IntakeService(resolver, source, Store())


def as_provider(h: Harness, **changes: Any) -> None:
    h.store.memberships[(ISSUER, SUBJECT)] = membership(
        audience="provider",
        subject_bindings=(SubjectBinding(kind="provider", resource_ref=PROVIDER),),
        **changes,
    )


def as_beneficiary(h: Harness, **changes: Any) -> None:
    h.store.memberships[(ISSUER, SUBJECT)] = membership(
        audience="beneficiary",
        subject_bindings=(SubjectBinding(kind="beneficiary", resource_ref=BENEFICIARY),),
        **changes,
    )


async def test_staff_audience_gets_operation_forbidden(h: Harness) -> None:
    # The default membership is staff with the `medico-auditor` group: maximal internal role.
    source = HeadSource(authority())
    install(h, source)
    await h.login()
    response = await h.client.get(LINKS)
    assert response.status_code == 403
    assert response.json() == {"code": "operation_forbidden"}
    # The source was never consulted: staff hold no vínculo to read.
    assert source.calls == []


async def test_only_own_bindings_are_listed(h: Harness) -> None:
    foreign_principal = authority(actor=actor(principal_ref=OTHER_PRINCIPAL))
    foreign_subject = authority(actor=actor(subject="00000000-0000-4000-8000-000000000009"))
    foreign_issuer = authority(actor=actor(issuer="https://other.example.test"))
    unbound_provider = authority(provider_ref=OTHER_PROVIDER, resource_ref=OTHER_GUIDE)
    mine = authority()
    install(h, HeadSource(foreign_principal, foreign_subject, foreign_issuer, unbound_provider, mine))
    as_provider(h)
    await h.login()
    response = await h.client.get(LINKS)
    assert response.status_code == 200
    body = response.json()
    assert [link["resource_ref"] for link in body["links"]] == [GUIDE]
    assert body["links"][0]["provider_ref"] == PROVIDER
    # Neither another principal's row nor a provider the principal is not bound to appears.
    assert OTHER_GUIDE not in response.text and OTHER_PRINCIPAL not in response.text


async def test_membership_group_alone_creates_no_link(h: Harness) -> None:
    # A provider membership whose groups name the beneficiary's own resource still yields
    # nothing: groups are roles, not a verified relationship (test_human_session.py:439).
    from maezo.portal.contracts.models import MembershipBinding

    h.store.memberships[(ISSUER, SUBJECT)] = membership(
        audience="provider",
        memberships=(MembershipBinding(membership_ref="m-1", roles=("provider",), groups=(OTHER_PROVIDER,)),),
        subject_bindings=(SubjectBinding(kind="provider", resource_ref=PROVIDER),),
    )
    install(h, HeadSource(authority(provider_ref=OTHER_PROVIDER)))
    await h.login()
    response = await h.client.get(LINKS)
    assert response.status_code == 200 and response.json()["links"] == []


async def test_beneficiary_sees_only_its_own_beneficiary_binding(h: Harness) -> None:
    mine = authority(actor=actor(audience="beneficiary"))
    other = authority(
        actor=actor(audience="beneficiary"),
        beneficiary_ref=OTHER_BENEFICIARY,
        resource_ref=OTHER_GUIDE,
    )
    install(h, HeadSource(mine, other))
    as_beneficiary(h)
    await h.login()
    body = (await h.client.get(LINKS)).json()
    assert [link["beneficiary_ref"] for link in body["links"]] == [BENEFICIARY]


async def test_audience_of_the_row_must_match_the_session_audience(h: Harness) -> None:
    # A provider-audience authority must not surface in a beneficiary session even when
    # the beneficiary reference matches the beneficiary's own binding.
    install(h, HeadSource(authority(actor=actor(audience="provider"))))
    as_beneficiary(h)
    await h.login()
    assert (await h.client.get(LINKS)).json()["links"] == []


async def test_stale_membership_revision_is_not_a_link(h: Harness) -> None:
    install(h, HeadSource(authority(actor=actor(membership_revision=99))))
    as_provider(h)
    await h.login()
    assert (await h.client.get(LINKS)).json()["links"] == []


async def test_revoked_or_expired_authority_disappears(h: Harness) -> None:
    as_provider(h)
    await h.login()
    live = HeadSource(authority())
    install(h, live)
    assert len((await h.client.get(LINKS)).json()["links"]) == 1
    for gone in (
        authority(state="revoked"),
        authority(valid_until=now() - timedelta(seconds=1)),
        authority(valid_from=now() + timedelta(hours=1)),
        authority(consent_state="revoked"),
        authority(legal_basis="consent", consent_state="not_required"),
    ):
        install(h, HeadSource(gone))
        response = await h.client.get(LINKS)
        assert response.status_code == 200 and response.json()["links"] == []
    # Re-read per request: the same session that saw a link stops seeing it once revoked.
    install(h, HeadSource(authority(state="revoked")))
    assert (await h.client.get(LINKS)).json()["links"] == []


async def test_head_ceiling_caps_the_published_window(h: Harness) -> None:
    ceiling = now() + timedelta(minutes=3)
    install(h, HeadSource(authority(), ceiling=ceiling))
    as_provider(h)
    await h.login()
    body = (await h.client.get(LINKS)).json()
    assert datetime.fromisoformat(body["links"][0]["valid_until"]) <= ceiling
    # A head whose own ceiling already passed yields nothing at all.
    install(h, HeadSource(authority(), ceiling=now() - timedelta(seconds=1)))
    assert (await h.client.get(LINKS)).json()["links"] == []


async def test_closed_dto_and_query_string_refused(h: Harness) -> None:
    install(h, HeadSource(authority()))
    as_provider(h)
    await h.login()
    response = await h.client.get(LINKS)
    assert response.status_code == 200
    assert set(response.json()) == {"schema_version", "links"}
    assert set(response.json()["links"][0]) == {
        "schema_version",
        "beneficiary_ref",
        "provider_ref",
        "resource_kind",
        "resource_ref",
        "action",
        "valid_until",
        "provenance_kind",
    }
    for query in ("?x=1", "?tenant=PRIVATE_CANARY", "?provenance_kind=attested"):
        refused = await h.client.get(LINKS + query)
        assert refused.status_code == 400 and refused.json() == {"code": "invalid_request"}
        assert "PRIVATE_CANARY" not in refused.text
    # The DTO itself is closed: an extra or renamed field is not constructible.
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        IntakeLinks(links=(), tenant="x")  # type: ignore[call-arg]
    with pytest.raises(pydantic.ValidationError):
        IntakeLink.model_validate({**_one(response), "tenant": "x"})


def _one(response: Any) -> dict[str, Any]:
    return dict(response.json()["links"][0])


async def test_no_phi_and_no_authority_input_at_the_boundary(h: Harness) -> None:
    install(h, HeadSource(authority()))
    as_provider(h)
    await h.login()
    # The route takes no path parameter, no query parameter and no body: there is no
    # position in the request where a reference, tenant or actor could be supplied.
    from maezo.portal.api.intakes import intake_router

    route = next(r for r in intake_router.routes if getattr(r, "path", None) == LINKS)
    assert route.param_convertors == {} and set(route.methods) == {"GET"}
    body = (await h.client.get(LINKS)).json()
    # Only opaque references cross the boundary: no name, document number or clinical field.
    assert set(body["links"][0]) & {"cpf", "numero_guia_tiss", "cid10", "nome"} == set()
    assert (await h.client.request("GET", LINKS, content=b"{}")).status_code == 400


async def test_provenance_kind_is_derived_from_publisher_ref_never_defaulted(h: Harness) -> None:
    as_provider(h)
    await h.login()
    install(h, HeadSource(authority(source=provenance(SYNTHETIC_PUBLISHER_REF))))
    assert (await h.client.get(LINKS)).json()["links"][0]["provenance_kind"] == "synthetic"
    for attested in ("amh-delegation-publisher", SYNTHETIC_PUBLISHER_REF + "-lookalike", "x"):
        install(h, HeadSource(authority(source=provenance(attested))))
        body = (await h.client.get(LINKS)).json()
        assert body["links"][0]["provenance_kind"] == "attested"
    # There is no default: the DTO cannot be built without an explicit label.
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        IntakeLink.model_validate(
            {
                "beneficiary_ref": BENEFICIARY,
                "provider_ref": PROVIDER,
                "resource_kind": "guide",
                "resource_ref": GUIDE,
                "action": "auth.start",
                "valid_until": now().isoformat(),
            }
        )
    # And a publisher-less provenance is not projectable at all.
    with pytest.raises(pydantic.ValidationError):
        provenance("")


async def test_reference_outside_the_closed_contract_is_dropped(h: Harness) -> None:
    install(h, HeadSource(authority(beneficiary_ref="short"), authority()))
    as_provider(h)
    await h.login()
    body = (await h.client.get(LINKS)).json()
    assert [link["beneficiary_ref"] for link in body["links"]] == [BENEFICIARY]


async def test_authority_that_cannot_read_links_is_a_dependency_not_an_empty_list(h: Harness) -> None:
    as_provider(h)
    await h.login()
    install(h, BlindAuthority())
    response = await h.client.get(LINKS)
    assert response.status_code == 503 and response.json() == {"code": "dependency_unavailable"}
    h.app.state.intake_service_factory = None
    assert (await h.client.get(LINKS)).status_code == 503


async def test_unauthenticated_and_revoked_sessions_are_refused(h: Harness) -> None:
    install(h, HeadSource(authority()))
    assert (await h.client.get(LINKS)).status_code == 401
    as_provider(h)
    await h.login()
    assert (await h.client.get(LINKS)).status_code == 200
    h.store.memberships.pop((ISSUER, SUBJECT))
    assert (await h.client.get(LINKS)).status_code == 401


async def test_the_protocol_is_not_satisfied_by_a_plain_intake_authority() -> None:
    assert not isinstance(BlindAuthority(), IntakeLinkSource)
    assert isinstance(HeadSource(), IntakeLinkSource)


class NonCallableLinksSource:
    """`links` exists but is not callable: satisfies `isinstance(..., IntakeLinkSource)`
    on this interpreter regardless, because `runtime_checkable` only checks presence."""

    links = "not-a-method"

    async def admit(self, principal: Any, request: Any) -> Any:  # pragma: no cover - unused here
        raise IntakeError("operation_forbidden")

    async def read(self, principal: Any, intake_ref: Any) -> Any:  # pragma: no cover - unused here
        raise IntakeError("operation_forbidden")


async def test_a_links_attribute_that_is_not_callable_is_refused_at_composition(h: Harness) -> None:
    from fastapi import Request

    from maezo.portal.api.intakes import link_service

    fake = NonCallableLinksSource()
    # The structural gap this guard exists to close: isinstance alone says yes.
    assert isinstance(fake, IntakeLinkSource)
    install(h, fake)
    request = Request({"type": "http", "app": h.app, "headers": []})
    with pytest.raises(IntakeError):
        link_service(request)
