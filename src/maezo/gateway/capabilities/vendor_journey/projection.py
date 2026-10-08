"""OP15 ``opportunity.project``: read-only vendor projection over the núcleo cycle.

Registry OP15 is the contract: ``OpportunityProjectionIntent → OpportunityProjection``
maps ``vendor_ref → opportunity/command_ref`` by reference. The pipeline stage is a
núcleo-owned reading of ``journeys/topology.py#JourneyStage`` consumed as it is —
a stage is pipeline position and NEVER an acceptance/enrollment assertion (C-10).
Refusals are typed and honest: ``SOURCE_UNAVAILABLE`` while the OP02/OP03 núcleo
contract is not published (this module is the second consumer of the internal
``ContractPublicationLedger`` seam; compras is the first), ``AUTHORITY_UNPROVEN``
for an unproven source/ownership/stage, ``STALE_REVISION`` for a superseded read
and ``PHI_IN_COMMERCIAL_INPUT`` at the attribute chokepoint — a projection never
carries clinical or uncontracted content. Channel attributes missing from OP14
observations are ``unknown``, never zero. The read is revision-stamped and pure:
the same (tenant, vendor_ref, revision) yields the same projection. No engine
instance, effect, route or worker is created here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Final, Literal, Protocol

from pydantic import ValidationError, field_validator

from maezo.gateway.capabilities.durability.models import (
    AwareUTCInstant,
    JournalCallTechnicalStatus,
    JourneySnapshot,
)
from maezo.gateway.capabilities.durability.ports import DurabilityJournalPort
from maezo.gateway.capabilities.journeys.contracts import JourneyBinding
from maezo.gateway.capabilities.journeys.topology import STAGES_BY_TASK
from maezo.gateway.capabilities.models import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateDTO,
    CapabilityContractError,
    CapabilityRefusalReason,
    Ref,
)
from maezo.gateway.capabilities.publication import ContractPublicationLedger

from .binding import VendorJourneyConfig, vendor_journey_binding

PROJECTION_SCHEMA_VERSION: Final = "v21-vendor-opportunity-projection.proposed.v1"
# Absent channel evidence is unknown, never zero (registry OP15).
CHANNEL_UNKNOWN: Final = "unknown"
# Closed allowlist of OP14-observed channel attributes; anything else is refused
# at this chokepoint instead of being carried into a commercial surface.
CHANNEL_ATTRIBUTE_KEYS: Final = frozenset({"producer_ref", "channel_class", "attribution_basis_ref"})
# The composition intent this projection reads on behalf of the vendor journey.
PROJECTED_COMPOSITION_OPERATION: Final = "offer.compose"

type ProjectionRefusalReason = Literal[
    "SOURCE_UNAVAILABLE", "AUTHORITY_UNPROVEN", "PHI_IN_COMMERCIAL_INPUT", "STALE_REVISION"
]


class OpportunityProjectionIntent(CandidateDTO):
    """Closed read intent; opaque refs only — never free content."""

    vendor_ref: Ref
    expected_business_revision: Ref | None = None


class OpportunityProjectionRefusal(CandidateDTO):
    """Bounded typed refusal; no detail text, no echo of refused input."""

    reason: ProjectionRefusalReason


class VendorStageReading(CandidateDTO):
    """Núcleo-owned pipeline position plus its revision stamp."""

    cursor_ref: Ref
    pipeline_revision: Ref


class OpportunityChannelView(CandidateDTO):
    """Channel attributes from OP14 observations; absent means unknown, never zero."""

    producer_ref: str
    channel_class: str
    attribution_basis_ref: Ref | None = None

    @field_validator("producer_ref", "channel_class")
    @classmethod
    def unknown_is_never_zero(cls, value: str) -> str:
        if value == CHANNEL_UNKNOWN:
            return value
        if not value.strip() or value.strip() == "0":
            raise ValueError("channel attribute is a real reference or unknown, never zero")
        return value


class OpportunityProjection(CandidateDTO):
    """One revision-stamped read; a stage never asserts aceite/matrícula."""

    schema_version: Literal["v21-vendor-opportunity-projection.proposed.v1"]
    vendor_ref: Ref
    journey_ref: Ref
    opportunity_command_refs: tuple[Ref, ...]
    pipeline_stage: Ref
    stage_semantics: Literal["PIPELINE_POSITION_ONLY_NOT_ACCEPTANCE"]
    pipeline_revision: Ref
    source_publication_receipt_ref: Ref
    channel: OpportunityChannelView
    projected_at: AwareUTCInstant


class VendorOpportunityLedgerPort(Protocol):
    """OP14-registered ``vendor_ref → live núcleo journey``; read-only seam."""

    async def resolve_journey_refs(self, tenant_ref: str, vendor_ref: str) -> tuple[str, ...] | None: ...


class VendorOpportunityStagePort(Protocol):
    """Núcleo-owned stage reading; the channel never derives or asserts position."""

    async def read_stage(
        self, tenant_ref: str, journey_ref: str
    ) -> VendorStageReading | CapabilityRefusalReason: ...


class VendorChannelAttributePort(Protocol):
    """OP14-observed channel attributes; unvetted keys never reach the projection."""

    async def channel_attributes(self, tenant_ref: str, vendor_ref: str) -> Mapping[str, object]: ...


def _refusal(reason: ProjectionRefusalReason) -> OpportunityProjectionRefusal:
    return OpportunityProjectionRefusal(reason=reason)


def _mapped(reason: CapabilityRefusalReason) -> ProjectionRefusalReason:
    if reason is CapabilityRefusalReason.SOURCE_UNAVAILABLE:
        return "SOURCE_UNAVAILABLE"
    if reason is CapabilityRefusalReason.STALE_REVISION:
        return "STALE_REVISION"
    return "AUTHORITY_UNPROVEN"


def _channel_view(attributes: Mapping[str, object]) -> OpportunityChannelView | OpportunityProjectionRefusal:
    producer = CHANNEL_UNKNOWN
    channel_class = CHANNEL_UNKNOWN
    basis: str | None = None
    for key, value in attributes.items():
        if key not in CHANNEL_ATTRIBUTE_KEYS or type(value) is not str or not value.strip():
            # An unvetted or shapeless attribute is refused here: the channel view
            # never becomes a carrier for clinical or uncontracted content.
            return _refusal("PHI_IN_COMMERCIAL_INPUT")
        if key == "producer_ref":
            producer = value
        elif key == "channel_class":
            channel_class = value
        else:
            basis = value
    try:
        return OpportunityChannelView(
            producer_ref=producer, channel_class=channel_class, attribution_basis_ref=basis
        )
    except ValidationError:
        return _refusal("PHI_IN_COMMERCIAL_INPUT")


class VendorOpportunityProjection:
    """The OP15 read side; consumes the núcleo journal, ledger and stage source."""

    def __init__(
        self,
        *,
        binding: JourneyBinding,
        journal: DurabilityJournalPort,
        publication: ContractPublicationLedger,
        opportunity_ledger: VendorOpportunityLedgerPort,
        stage_source: VendorOpportunityStagePort,
        channel_attributes: VendorChannelAttributePort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(binding) is not JourneyBinding or type(publication) is not ContractPublicationLedger:
            raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
        self._binding = binding
        self._journal = journal
        self._publication = publication
        self._ledger = opportunity_ledger
        self._stage_source = stage_source
        self._channel_attributes = channel_attributes
        # The núcleo topology map consumed as it is, keyed by the exact task.
        self._stages = STAGES_BY_TASK[binding.task_ref]
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _journey_command_refs(self) -> tuple[str, ...] | OpportunityProjectionRefusal:
        try:
            binding = self._binding.journal_binding()
            result = await self._journal.observe_journey(binding)
            if (
                result.technical_status
                not in {JournalCallTechnicalStatus.RECORDED, JournalCallTechnicalStatus.UNCHANGED}
                or result.snapshot is None
            ):
                return _refusal("SOURCE_UNAVAILABLE")
            snapshot = JourneySnapshot.model_validate(dict(result.snapshot.__dict__))
        except ValidationError:
            return _refusal("AUTHORITY_UNPROVEN")
        if snapshot.binding != self._binding.journal_binding():
            return _refusal("AUTHORITY_UNPROVEN")
        return snapshot.command_refs

    async def project(
        self, intent: OpportunityProjectionIntent
    ) -> OpportunityProjection | OpportunityProjectionRefusal:
        """Read one projection; refuse typed before any downstream port when honest."""

        # Publication precedes every port: the unpublished-núcleo inertia is the
        # registry §5.1 condition, and this module is its second consumer.
        try:
            self._publication.require_published(CANDIDATE_SCHEMA_VERSION, PROJECTED_COMPOSITION_OPERATION)
        except CapabilityContractError as exc:
            return _refusal(_mapped(exc.reason))
        published = self._publication.published_contract(CANDIDATE_SCHEMA_VERSION)
        if published is None:  # pragma: no cover - require_published just proved it exists
            return _refusal("SOURCE_UNAVAILABLE")
        try:
            intent = OpportunityProjectionIntent.model_validate(dict(intent.__dict__))
        except ValidationError:
            return _refusal("AUTHORITY_UNPROVEN")
        refs = await self._ledger.resolve_journey_refs(self._binding.tenant_ref, intent.vendor_ref)
        if not refs:
            # Never registered (or no live opportunity): honest unknown, not empty.
            return _refusal("SOURCE_UNAVAILABLE")
        if len(refs) != 1:
            # Ambiguous ownership is an unproven source, never a guessed position.
            return _refusal("AUTHORITY_UNPROVEN")
        journey_ref = refs[0]
        reading = await self._stage_source.read_stage(self._binding.tenant_ref, journey_ref)
        if isinstance(reading, CapabilityRefusalReason):
            return _refusal(_mapped(reading))
        try:
            reading = VendorStageReading.model_validate(dict(reading.__dict__))
        except ValidationError:
            return _refusal("AUTHORITY_UNPROVEN")
        if reading.cursor_ref not in self._stages:
            # A cursor outside the published núcleo topology is an unproven stage.
            return _refusal("AUTHORITY_UNPROVEN")
        if (
            intent.expected_business_revision is not None
            and intent.expected_business_revision != reading.pipeline_revision
        ):
            return _refusal("STALE_REVISION")
        command_refs = await self._journey_command_refs()
        if isinstance(command_refs, OpportunityProjectionRefusal):
            return command_refs
        view = _channel_view(
            await self._channel_attributes.channel_attributes(self._binding.tenant_ref, intent.vendor_ref)
        )
        if isinstance(view, OpportunityProjectionRefusal):
            return view
        return OpportunityProjection(
            schema_version=PROJECTION_SCHEMA_VERSION,
            vendor_ref=intent.vendor_ref,
            journey_ref=journey_ref,
            opportunity_command_refs=command_refs,
            pipeline_stage=reading.cursor_ref,
            stage_semantics="PIPELINE_POSITION_ONLY_NOT_ACCEPTANCE",
            pipeline_revision=reading.pipeline_revision,
            source_publication_receipt_ref=published.publication_receipt_ref,
            channel=view,
            projected_at=self._clock(),
        )


def opportunity_projection(
    config: VendorJourneyConfig,
    *,
    journal: DurabilityJournalPort | None = None,
    publication: ContractPublicationLedger | None = None,
    opportunity_ledger: VendorOpportunityLedgerPort | None = None,
    stage_source: VendorOpportunityStagePort | None = None,
    channel_attributes: VendorChannelAttributePort | None = None,
    clock: Callable[[], datetime] | None = None,
) -> VendorOpportunityProjection | None:
    """Build the OP15 projection — or nothing, while the flag is off."""

    if not config.enabled:
        return None
    if (
        journal is None
        or publication is None
        or opportunity_ledger is None
        or stage_source is None
        or channel_attributes is None
    ):
        raise CapabilityContractError(CapabilityRefusalReason.CONTRACT_MISMATCH)
    return VendorOpportunityProjection(
        binding=vendor_journey_binding(config),
        journal=journal,
        publication=publication,
        opportunity_ledger=opportunity_ledger,
        stage_source=stage_source,
        channel_attributes=channel_attributes,
        clock=clock,
    )
