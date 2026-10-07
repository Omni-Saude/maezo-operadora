"""OP15 ``opportunity.project``: typed inertia, unknown≠zero, PHI chokepoint, stage semantics.

The projection consumes the internal ``ContractPublicationLedger`` seam as its
second consumer (compras is the first, exercised in ``test_publication.py``);
this module pins that both consumers run against the same seam object and that
the unpublished-núcleo inertia precedes every downstream port call.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from maezo.gateway.capabilities.durability.models import (
    CommandHandle,
    CommandSnapshot,
    CommandTechnicalState,
)
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CONTRACT_STATE_PUBLICATION_WITHDRAWN,
    CONTRACT_STATE_PUBLISHED,
    CapabilityRefusalReason,
)
from maezo.gateway.capabilities.publication import ContractPublicationLedger
from maezo.gateway.capabilities.vendor_journey import (
    OpportunityChannelView,
    OpportunityProjection,
    OpportunityProjectionIntent,
    OpportunityProjectionRefusal,
    VendorOpportunityProjection,
    VendorStageReading,
    opportunity_projection,
    vendor_journey_binding,
)
from tests.unit.gateway.capabilities.journeys.helpers import NOW, UnitJournal
from tests.unit.gateway.capabilities.vendor_journey.vendor_helpers import (
    ScriptedChannelAttributes,
    ScriptedOpportunityLedger,
    ScriptedStageSource,
    vendor_config,
)

PUBLISHED_AT = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)
PUBLICATION_RECEIPT = "unit-publication-receipt-0001"
WITHDRAWAL_RECEIPT = "unit-withdrawal-receipt-0001"
STAGE = VendorStageReading(cursor_ref="C1_options", pipeline_revision="unit-revision-1")


def ledger() -> ContractPublicationLedger:
    return ContractPublicationLedger(clock=lambda: PUBLISHED_AT)


def projection(
    *,
    publication: ContractPublicationLedger | None = None,
    refs: tuple[str, ...] | None = ("unit-journey",),
    stage: VendorStageReading | CapabilityRefusalReason = STAGE,
    attributes: dict[str, object] | None = None,
    enabled: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> VendorOpportunityProjection:
    config = vendor_config(enabled=enabled)
    journal = UnitJournal(vendor_journey_binding(config))
    built = opportunity_projection(
        config,
        journal=journal,
        publication=publication if publication is not None else ledger(),
        opportunity_ledger=ScriptedOpportunityLedger(refs),
        stage_source=ScriptedStageSource(stage),
        channel_attributes=ScriptedChannelAttributes(attributes),
        clock=clock,
    )
    assert built is not None
    return built


def test_disabled_flag_keeps_the_projection_off_the_surface() -> None:
    config = vendor_config(enabled=False)
    assert opportunity_projection(config, journal=None, publication=None) is None  # type: ignore[arg-type]


async def test_unpublished_nucleo_refuses_typed_before_any_port_call() -> None:
    subject = projection()
    outcome = await subject.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1"))
    assert outcome == OpportunityProjectionRefusal(reason="SOURCE_UNAVAILABLE")
    assert subject._ledger.calls == []  # type: ignore[attr-defined]
    assert subject._stage_source.calls == []  # type: ignore[attr-defined]
    assert subject._channel_attributes.calls == []  # type: ignore[attr-defined]


async def test_published_nucleo_projects_by_reference_with_unknown_channel() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    subject = projection(publication=publication)
    outcome = await subject.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1"))
    assert isinstance(outcome, OpportunityProjection)
    assert outcome.journey_ref == "unit-journey"
    assert outcome.opportunity_command_refs == ()
    assert outcome.pipeline_stage == "C1_options"
    assert outcome.stage_semantics == "PIPELINE_POSITION_ONLY_NOT_ACCEPTANCE"
    assert outcome.pipeline_revision == "unit-revision-1"
    assert outcome.source_publication_receipt_ref == PUBLICATION_RECEIPT
    assert outcome.channel.producer_ref == "unknown"
    assert outcome.channel.channel_class == "unknown"
    assert outcome.channel.attribution_basis_ref is None
    assert outcome.channel.producer_ref != "0"  # unknown is never zero


async def test_read_is_revision_stamped_and_idempotent() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    subject = projection(publication=publication, clock=lambda: PUBLISHED_AT)
    intent = OpportunityProjectionIntent(
        vendor_ref="unit-vendor-ref-1", expected_business_revision="unit-revision-1"
    )
    first = await subject.project(intent)
    second = await subject.project(intent)
    assert first == second and isinstance(first, OpportunityProjection)
    stale = await subject.project(
        OpportunityProjectionIntent(
            vendor_ref="unit-vendor-ref-1", expected_business_revision="unit-revision-0"
        )
    )
    assert stale == OpportunityProjectionRefusal(reason="STALE_REVISION")


async def test_withdrawal_restores_the_typed_inertia_without_deleting_records() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    subject = projection(publication=publication)
    assert isinstance(
        await subject.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1")),
        OpportunityProjection,
    )
    withdrawal = publication.withdraw(CANDIDATE_SCHEMA_VERSION, withdrawal_receipt_ref=WITHDRAWAL_RECEIPT)
    assert publication.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE_PUBLICATION_WITHDRAWN
    assert withdrawal.contract_sha256 == publication.history(CANDIDATE_SCHEMA_VERSION)[0].contract_sha256
    refused = await subject.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1"))
    assert refused == OpportunityProjectionRefusal(reason="SOURCE_UNAVAILABLE")
    assert len(subject._stage_source.calls) == 1  # type: ignore[attr-defined]


async def test_command_refs_are_a_by_reference_journal_read() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    config = vendor_config()
    journal = UnitJournal(vendor_journey_binding(config))
    handle = CommandHandle(binding=journal.binding, command_ref="unit-command-7", request_sha256="a" * 64)
    journal.commands["unit-command-7"] = CommandSnapshot(
        handle=handle,
        journal_revision=1,
        technical_state=CommandTechnicalState.RECORDED,
        recorded_at=NOW,
        last_observed_at=NOW,
    )
    subject = opportunity_projection(
        config,
        journal=journal,
        publication=publication,
        opportunity_ledger=ScriptedOpportunityLedger(("unit-journey",)),
        stage_source=ScriptedStageSource(STAGE),
        channel_attributes=ScriptedChannelAttributes(),
    )
    assert subject is not None
    outcome = await subject.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1"))
    assert isinstance(outcome, OpportunityProjection)
    assert outcome.opportunity_command_refs == ("unit-command-7",)


@pytest.mark.parametrize(
    ("refs", "expected"),
    [
        (None, "SOURCE_UNAVAILABLE"),
        ((), "SOURCE_UNAVAILABLE"),
        (("unit-journey-a", "unit-journey-b"), "AUTHORITY_UNPROVEN"),
    ],
)
async def test_ownership_is_resolved_fail_closed(refs: tuple[str, ...] | None, expected: str) -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    subject = projection(publication=publication, refs=refs)
    outcome = await subject.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1"))
    assert outcome == OpportunityProjectionRefusal(reason=expected)  # type: ignore[arg-type]


async def test_stage_source_refusals_propagate_typed() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    unavailable = projection(publication=publication, stage=CapabilityRefusalReason.SOURCE_UNAVAILABLE)
    assert await unavailable.project(
        OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1")
    ) == OpportunityProjectionRefusal(reason="SOURCE_UNAVAILABLE")
    unproven = projection(publication=publication, stage=CapabilityRefusalReason.AUTHORITY_UNPROVEN)
    assert await unproven.project(
        OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1")
    ) == OpportunityProjectionRefusal(reason="AUTHORITY_UNPROVEN")


async def test_cursor_outside_the_published_nucleo_topology_is_unproven() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    ghost = projection(
        publication=publication,
        stage=VendorStageReading(cursor_ref="X9_ghost_cursor", pipeline_revision="unit-revision-1"),
    )
    outcome = await ghost.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1"))
    assert outcome == OpportunityProjectionRefusal(reason="AUTHORITY_UNPROVEN")


async def test_channel_attribute_chokepoint_refuses_unvetted_keys_and_values() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    clinical_key = projection(publication=publication, attributes={"cid10_code": "unit-clinical-code"})
    assert await clinical_key.project(
        OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1")
    ) == OpportunityProjectionRefusal(reason="PHI_IN_COMMERCIAL_INPUT")
    shapeless_value = projection(publication=publication, attributes={"producer_ref": 0})
    assert await shapeless_value.project(
        OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1")
    ) == OpportunityProjectionRefusal(reason="PHI_IN_COMMERCIAL_INPUT")
    blank_value = projection(publication=publication, attributes={"channel_class": "   "})
    assert await blank_value.project(
        OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1")
    ) == OpportunityProjectionRefusal(reason="PHI_IN_COMMERCIAL_INPUT")


async def test_vetted_channel_attributes_are_projected_with_their_basis() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    subject = projection(
        publication=publication,
        attributes={
            "producer_ref": "unit-producer-1",
            "channel_class": "unit-channel-class",
            "attribution_basis_ref": "unit-op14-observation",
        },
    )
    outcome = await subject.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1"))
    assert isinstance(outcome, OpportunityProjection)
    assert outcome.channel.producer_ref == "unit-producer-1"
    assert outcome.channel.channel_class == "unit-channel-class"
    assert outcome.channel.attribution_basis_ref == "unit-op14-observation"


def test_unknown_channel_view_is_never_a_zero_placeholder() -> None:
    view = OpportunityChannelView(producer_ref="unknown", channel_class="unknown")
    assert view.producer_ref == "unknown" and view.channel_class == "unknown"
    with pytest.raises(ValidationError):
        OpportunityChannelView(producer_ref="0", channel_class="unknown")
    with pytest.raises(ValidationError):
        OpportunityChannelView(producer_ref="  ", channel_class="unknown")


def test_projection_intent_is_closed_and_never_carries_content() -> None:
    OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1")
    with pytest.raises(ValidationError):
        OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1", clinical_note="SYNTH")  # type: ignore[call-arg]
    forged = OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1").model_copy(
        update={"clinical_note": "SYNTHETIC_CLINICAL_BODY_90210"}
    )
    assert forged.__dict__["clinical_note"] == "SYNTHETIC_CLINICAL_BODY_90210"


async def test_forged_intent_instance_is_refused_not_parsed() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    subject = projection(publication=publication)
    forged = OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1").model_copy(
        update={"clinical_note": "SYNTHETIC_CLINICAL_BODY_90210"}
    )
    outcome = await subject.project(forged)
    assert outcome == OpportunityProjectionRefusal(reason="AUTHORITY_UNPROVEN")


async def test_acceptance_stage_stays_pipeline_position_only() -> None:
    publication = ledger()
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    subject = projection(
        publication=publication,
        stage=VendorStageReading(cursor_ref="C2_acceptance", pipeline_revision="unit-revision-1"),
    )
    outcome = await subject.project(OpportunityProjectionIntent(vendor_ref="unit-vendor-ref-1"))
    assert isinstance(outcome, OpportunityProjection)
    assert outcome.pipeline_stage == "C2_acceptance"
    assert outcome.stage_semantics == "PIPELINE_POSITION_ONLY_NOT_ACCEPTANCE"
    assert frozenset(OpportunityProjection.model_fields).isdisjoint({"acceptance", "enrollment"})


def test_projection_surface_shares_the_one_publication_seam() -> None:
    """Second-consumer clause: both consumers bind the same seam type and state."""

    publication = ledger()
    assert type(publication) is ContractPublicationLedger
    assert publication.state(CANDIDATE_SCHEMA_VERSION) == "PROPOSED_INTERNAL_CONTRACT_NOT_PUBLISHED"
    publication.publish(CANDIDATE_SCHEMA_VERSION, publication_receipt_ref=PUBLICATION_RECEIPT)
    assert publication.state(CANDIDATE_SCHEMA_VERSION) == CONTRACT_STATE_PUBLISHED
