"""Trusted technical attachment tests; synthetic ports do not qualify live channels."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from maezo.gateway.capabilities.journeys.contracts import JourneyBinding, JourneyDispatchOutcome
from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.service import WebhookState, _attach_administrative_runtime
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from tests.unit.agents.test_lucas_administrative_runtime import assembled, turn_input


def dispatcher(tenant: str) -> HelenaDispatcher:
    return HelenaDispatcher(
        tenant_id=tenant,
        inference=cast(Any, None),
        dmn=cast(Any, None),
        cibseven=cast(Any, None),
        whatsapp_client=cast(Any, None),
        pseudonymizer=Pseudonymizer(key=b"unit-root-attachment-key"),
        audit_sink=cast(Any, None),
    )


@pytest.mark.asyncio
async def test_default_root_has_no_administrative_handler_or_effect() -> None:
    b, ingress, driver, saver, _ = assembled()
    target = dispatcher(b.tenant_ref)
    assert target.administrative_runtime is None and target.administrative_binding is None
    assert (
        await target.accept_administrative_turn(turn_input(b)) == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    )
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []
    assert target.lucas_turno is None and target.roteador is None


@pytest.mark.asyncio
async def test_saver_unavailable_never_attaches_or_calls_driver() -> None:
    b, ingress, driver, saver, _ = assembled()
    target = dispatcher(b.tenant_ref)
    state = WebhookState(
        WhatsAppWebhookSettings(tenant_id=b.tenant_ref, app_secret="unit", verify_token="unit"),
        dispatcher=target,
    )
    assert (
        _attach_administrative_runtime(state, binding=b, driver=driver, currentness=ingress, enabled=True)
        is None
    )
    assert state.administrative_runtime is None and target.administrative_runtime is None
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []


@pytest.mark.asyncio
async def test_default_attachment_retains_authority_denial_before_saver() -> None:
    b, ingress, driver, saver, _ = assembled()
    target = dispatcher(b.tenant_ref)
    state = WebhookState(
        WhatsAppWebhookSettings(tenant_id=b.tenant_ref, app_secret="unit", verify_token="unit"),
        dispatcher=target,
    )
    state.checkpointer = cast(Any, SimpleNamespace(saver=saver))
    runtime = _attach_administrative_runtime(state, binding=b, driver=driver, currentness=ingress)
    assert runtime is target.administrative_runtime is state.administrative_runtime
    assert (
        await target.accept_administrative_turn(turn_input(b)) == CapabilityRefusalReason.AUTHORITY_UNPROVEN
    )
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []


@pytest.mark.asyncio
async def test_qualified_technical_attachment_uses_existing_registry_and_complete_runtime() -> None:
    b, ingress, driver, saver, _ = assembled()
    target = dispatcher(b.tenant_ref)
    state = WebhookState(
        WhatsAppWebhookSettings(tenant_id=b.tenant_ref, app_secret="unit", verify_token="unit"),
        dispatcher=target,
    )
    state.checkpointer = cast(Any, SimpleNamespace(saver=saver))
    runtime = _attach_administrative_runtime(
        state, binding=b, driver=driver, currentness=ingress, enabled=True
    )
    assert runtime is not None and runtime.is_bound_to(b)
    result = await target.accept_administrative_turn(turn_input(b))
    assert isinstance(result, JourneyDispatchOutcome)
    assert ingress.calls == 3 and saver.reads == 3 and len(driver.calls) == 1
    assert target.lucas_turno is None and target.roteador is None


@pytest.mark.asyncio
async def test_foreign_root_tenant_cannot_attach_valid_other_tenant_runtime() -> None:
    b, ingress, driver, saver, _ = assembled()
    target = dispatcher("other-root")
    state = WebhookState(
        WhatsAppWebhookSettings(tenant_id="other-root", app_secret="unit", verify_token="unit"),
        dispatcher=target,
    )
    state.checkpointer = cast(Any, SimpleNamespace(saver=saver))
    assert (
        _attach_administrative_runtime(state, binding=b, driver=driver, currentness=ingress, enabled=True)
        is None
    )
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []


@pytest.mark.parametrize("field", list(JourneyBinding.model_fields))
def test_whole_binding_inspection_is_exact_without_auth_or_saver(field: str) -> None:
    b, ingress, driver, saver, runtime = assembled()
    value = getattr(b, field)
    if field == "journal":
        value = value.model_copy(update={"journey_ref": "unit-other-journey"})
    elif type(value) is bool:
        value = not value
    else:
        value = "unit-other-scope"
    assert runtime.is_bound_to(b)
    assert runtime.is_bound_to(b.model_copy(update={field: value})) is False
    assert ingress.calls == 0 and saver.reads == 0 and driver.calls == []
