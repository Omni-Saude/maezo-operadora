"""AGJ-VENDOR (JR4) candidate: default-off vendor binding over the journey motor.

Consumes the existing ``journeys`` motor by reference (registry section 4);
nothing here is a second machine, driver, cursor map or provider. No production
root mounts this package while the flag stays off.
"""

from .binding import (
    CHANNEL_FORBIDDEN_SEAT_OPERATIONS,
    VENDOR_JOURNEY_BINDING_ID,
    VENDOR_OPERATIONS,
    VENDOR_TASK_REF,
    VendorCompositionGuard,
    VendorJourneyConfig,
    vendor_journey,
    vendor_journey_binding,
)
from .projection import (
    CHANNEL_ATTRIBUTE_KEYS,
    CHANNEL_UNKNOWN,
    PROJECTED_COMPOSITION_OPERATION,
    PROJECTION_SCHEMA_VERSION,
    OpportunityChannelView,
    OpportunityProjection,
    OpportunityProjectionIntent,
    OpportunityProjectionRefusal,
    VendorChannelAttributePort,
    VendorOpportunityLedgerPort,
    VendorOpportunityProjection,
    VendorOpportunityStagePort,
    VendorStageReading,
    opportunity_projection,
)

__all__ = [
    "CHANNEL_ATTRIBUTE_KEYS",
    "CHANNEL_FORBIDDEN_SEAT_OPERATIONS",
    "CHANNEL_UNKNOWN",
    "PROJECTED_COMPOSITION_OPERATION",
    "PROJECTION_SCHEMA_VERSION",
    "VENDOR_JOURNEY_BINDING_ID",
    "VENDOR_OPERATIONS",
    "VENDOR_TASK_REF",
    "OpportunityChannelView",
    "OpportunityProjection",
    "OpportunityProjectionIntent",
    "OpportunityProjectionRefusal",
    "VendorChannelAttributePort",
    "VendorCompositionGuard",
    "VendorJourneyConfig",
    "VendorOpportunityLedgerPort",
    "VendorOpportunityProjection",
    "VendorOpportunityStagePort",
    "VendorStageReading",
    "opportunity_projection",
    "vendor_journey",
    "vendor_journey_binding",
]
