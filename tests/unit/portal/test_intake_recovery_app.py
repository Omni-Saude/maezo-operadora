"""Recovery routes through the real BFF composition; no database qualification."""

import pytest
from pydantic import TypeAdapter
from tests.unit.portal.test_human_session import PREFIX, Harness, h

from maezo.portal.contracts.intake import ResourceRef

__all__ = ["h"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/commands/" + "a" * 32])
async def test_recovery_is_registered_and_refuses_absent_production_factory(h: Harness, suffix: str):
    response = await h.client.get(PREFIX + "/intake-recovery" + suffix)
    assert response.status_code == 503
    assert response.json() == {"code": "dependency_unavailable"}
    assert {value.strip() for value in response.headers["cache-control"].split(",")} == {"no-store"}


@pytest.mark.asyncio
async def test_recovery_host_refusal_uses_closed_product_error(h: Harness):
    response = await h.client.get(PREFIX + "/intake-recovery", headers={"host": "wrong.test"})
    assert response.status_code == 400
    assert response.json() == {"code": "invalid_request"}
    assert {value.strip() for value in response.headers["cache-control"].split(",")} == {"no-store"}


def test_recovery_openapi_keeps_existing_surfaces_and_closed_pointer_schemas(h: Harness):
    schema = h.app.openapi()
    assert PREFIX + "/intake-recovery" in schema["paths"]
    assert PREFIX + "/intake-recovery/commands/{command_id}" in schema["paths"]
    for path in (
        "/cases",
        "/intakes/auth",
        "/tasks/{task_id}/assignments",
        "/cases/{case_ref}/communications",
    ):
        assert PREFIX + path in schema["paths"]
    pointer = schema["components"]["schemas"]["IntakeRecoveryItem"]
    assert pointer["additionalProperties"] is False
    assert set(pointer["properties"]) == {"command_id", "intake_ref"}
    discovery = schema["paths"][PREFIX + "/intake-recovery"]["get"]
    assert discovery["parameters"] == [
        {
            "name": "cursor",
            "in": "query",
            "required": False,
            "schema": TypeAdapter(ResourceRef).json_schema(),
        }
    ]
    observation = schema["paths"][PREFIX + "/intake-recovery/commands/{command_id}"]["get"]
    assert not any(parameter["in"] == "query" for parameter in observation["parameters"])
