"""Protected communications v1; no free text in General-zone wire projections."""

from typing import Literal

from maezo.portal.contracts.intake import Closed, DecimalRevision, ResourceRef
from maezo.portal.contracts.models import OpaqueRef


class CommunicationSubmission(Closed):
    command_id: ResourceRef
    body_ref: ResourceRef
    recipient_set_ref: ResourceRef


class CommunicationReceipt(Closed):
    schema_version: Literal["portal-communication-receipt.v1"] = "portal-communication-receipt.v1"
    communication_ref: ResourceRef
    command_id: ResourceRef
    disposition: Literal["inbox_available"] = "inbox_available"


class CommunicationSummary(Closed):
    communication_ref: ResourceRef
    sender_kind: Literal["staff", "beneficiary", "provider", "system"] | None = None
    authored_at: str | None = None
    inbox_available_at: str | None = None
    delivery_state: Literal["inbox_available"] | None = None
    body_ref: ResourceRef | None = None


class CommunicationPage(Closed):
    schema_version: Literal["portal-communications.v1"] = "portal-communications.v1"
    case_ref: ResourceRef
    items: tuple[CommunicationSummary, ...]
    next_cursor: ResourceRef | None
    observed_at: str
    valid_until: str


class HistoryEntry(Closed):
    event_ref: ResourceRef
    sequence: DecimalRevision | None = None
    occurred_at: str | None = None
    kind: Literal["communication_available", "command_receipt_indexed"] | None = None
    communication_ref: ResourceRef | None = None
    command_ref: OpaqueRef | None = None
    receipt_ref: OpaqueRef | None = None


class HistoryPage(Closed):
    schema_version: Literal["portal-history.v1"] = "portal-history.v1"
    history_scope: Literal["portal_events"] = "portal_events"
    case_ref: ResourceRef
    items: tuple[HistoryEntry, ...]
    next_cursor: ResourceRef | None
    observed_at: str
    valid_until: str
