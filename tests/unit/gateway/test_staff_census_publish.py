from types import SimpleNamespace

import pytest

from maezo.gateway.human.read_profile import digest, wire
from maezo.gateway.staff_cases.census import publish_plan
from maezo.gateway.staff_cases.census_source import OwnerManifestStaffCensusSource
from maezo.gateway.staff_cases.models import StaffCaseError
from tests.unit.gateway.test_staff_census_plan import prepared


@pytest.mark.asyncio
async def test_uncertain_send_stops_then_reconstruction_reuses_exact_original(monkeypatch):
    plan, authority, now = prepared()
    calls = []

    class Client:
        async def publish(self, raw):
            calls.append(raw)
            raise StaffCaseError("uncertain")

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            dependencies=SimpleNamespace(wire=lambda: {}, cut_digest=digest(plan.cut.wire()))
        ),
        authority=authority,
        source=OwnerManifestStaffCensusSource(authority, maximum_bytes=67108864, maximum_records=100000),
        clients={"policy": Client()},
        current=lambda: now,
        require_cut=lambda cut: None,
    )
    for _ in range(2):
        with pytest.raises(StaffCaseError):
            await publish_plan(plan, runtime)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert plan.publications[0].publication_id.encode() in calls[0]


@pytest.mark.asyncio
async def test_wrong_correlated_receipt_stops_before_later_publication(monkeypatch):
    plan, prepared_authority, now = prepared()
    calls = []

    class Client:
        async def publish(self, raw):
            calls.append(raw)
            p = plan.publications[0]
            return dict(
                schema="staff-case-publication-receipt.v1",
                publication_id="wrong",
                request_digest=digest(p.wire()),
                scope=wire(p.scope),
                source_ref=p.source_ref,
                source_revision=p.source_revision,
                payload_digest=p.payload_digest,
                disposition="committed",
                committed_at=p.observed_at,
                native_receipt_ref="receipt",
                valid_until=p.valid_until,
                proof=plan.proof.wire() | {"purpose": "native_result"},
            )

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            dependencies=SimpleNamespace(wire=lambda: {}, cut_digest=digest(plan.cut.wire()))
        ),
        authority=prepared_authority,
        source=OwnerManifestStaffCensusSource(
            prepared_authority, maximum_bytes=67108864, maximum_records=100000
        ),
        clients={"policy": Client()},
        current=lambda: now,
        require_cut=lambda cut: None,
    )
    with pytest.raises(StaffCaseError):
        await publish_plan(plan, runtime)
    assert len(calls) == 1
