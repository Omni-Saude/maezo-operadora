"""PHI-only plaintext communication DTOs. Never mount in the General BFF."""

from typing import Literal

from pydantic import Field

from maezo.portal.contracts.intake import Closed, ResourceRef


class CommunicationContentSubmission(Closed):
    command_id: ResourceRef
    body: str = Field(min_length=1, repr=False)


class CommunicationContentReceipt(Closed):
    body_ref: ResourceRef
    command_id: ResourceRef
    disposition: Literal["preserved"] = "preserved"


class CommunicationContent(Closed):
    communication_ref: ResourceRef
    body: str = Field(repr=False)
    observed_at: str
    valid_until: str
