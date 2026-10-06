"""Explicit technical resume reference path, with existing ESC/billing mapping retained."""

from typing import Any, cast

import pytest

from maezo.gateway.capabilities.journeys.contracts import JourneyDispatchOutcome
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.platform.integrations.agent_resume import ResumeHandler
from tests.unit.agents.test_lucas_administrative_runtime import assembled, resume_input


def handler(tenant: str) -> ResumeHandler:
    return ResumeHandler(
        tenant_id=tenant,
        instructions=cast(Any, None),
        recipients=cast(Any, None),
        resumers={},
        alerter=cast(Any, None),
    )


@pytest.mark.asyncio
async def test_missing_attachment_never_treats_generic_event_as_admin_receipt() -> None:
    b, ingress, driver, saver, _ = assembled()
    target = handler(b.tenant_ref)
    assert await target.resume_administrative(resume_input()) == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert target.resumers == {}
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []


@pytest.mark.asyncio
async def test_exact_receipt_reference_resume_uses_separate_trusted_attachment() -> None:
    b, ingress, driver, saver, runtime = assembled()
    target = handler(b.tenant_ref)
    target.administrative_runtime, target.administrative_binding = runtime, b.model_copy(deep=True)
    result = await target.resume_administrative(resume_input())
    assert isinstance(result, JourneyDispatchOutcome)
    assert ingress.calls == 3 and saver.reads == 3 and len(driver.calls) == 1
    assert target.resumers == {}  # Neither billing resumer nor ESC event parser replaced.


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", ["tenant", "journey", "source_contract"])
async def test_foreign_attachment_scope_is_denied_before_driver_or_saver(foreign: str) -> None:
    b, ingress, driver, saver, runtime = assembled()
    target = handler("other-root" if foreign == "tenant" else b.tenant_ref)
    captured = b.model_copy(deep=True)
    if foreign == "journey":
        captured = captured.model_copy(update={"journey_ref": "other"})
    elif foreign == "source_contract":
        captured = captured.model_copy(update={"source_transition_contract_ref": "other"})
    target.administrative_runtime, target.administrative_binding = runtime, captured
    assert await target.resume_administrative(resume_input()) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []
