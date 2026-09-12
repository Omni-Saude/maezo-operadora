"""Exact zero-output profile controls; synthetic TLS fixture does not qualify a deployment."""

import copy

import pytest
from tests.unit.gateway.native_fetch.test_native_fetch import prepared as prepared_fixture
from tests.unit.gateway.native_fetch.test_native_fetch import tls_material as tls_material_fixture

from maezo.gateway.document_requests.completion import CompletionAuthority, CompletionClient
from maezo.gateway.external_cases.models import ExternalCaseError
from maezo.gateway.native_fetch.models import encode, sha

prepared = prepared_fixture
tls_material = tls_material_fixture


class NoNetworkAuthority(CompletionAuthority):
    async def acquire(self, selection_digest, binding, purpose):
        raise AssertionError("profile tests must never ask for native admission")


def capability(prepared):
    cap = copy.deepcopy(prepared.profile.value())
    cap["target"].update(process_key="SP-OP-AUTH-001", topic="operadora.auth.request_documents")
    cap["schema"].update(
        schema_id="unit-request-complete",
        operation="external_complete",
        process_key="SP-OP-AUTH-001",
        topic="operadora.auth.request_documents",
        read_projection=[],
    )
    cap["acquisition_policy"]["resource_requirement"] = "required"
    return cap


@pytest.mark.asyncio
async def test_empty_or_unselected_catalog_cannot_activate_completion(prepared, tls_material):
    tls, _ = tls_material
    cap = capability(prepared)
    empty = encode(dict(protocol="maezo.engine-schemas.v2", schemas=[]))
    with pytest.raises(ExternalCaseError):
        CompletionClient(tls, NoNetworkAuthority(), encode(cap), empty, sha(empty))
    catalog = encode(dict(protocol="maezo.engine-schemas.v2", schemas=[cap["schema"]]))
    client = CompletionClient(tls, NoNetworkAuthority(), encode(cap), catalog, sha(catalog))
    assert client.cap["schema"]["fields"] == []
    assert await client.channel.close()


@pytest.mark.parametrize("change", ["variables", "source", "wrong_topic", "duplicate_catalog"])
async def test_only_exact_positive_source_free_zero_output_schema(prepared, tls_material, change):
    tls, _ = tls_material
    cap = capability(prepared)
    if change == "variables":
        cap["schema"]["fields"] = [{"name": "documentacao_completa"}]
    if change == "source":
        cap["source_target"] = {"process_key": "AUTH"}
    if change == "wrong_topic":
        cap["target"]["topic"] = "agents.events.auth.pended"
    schemas = [cap["schema"]] * (2 if change == "duplicate_catalog" else 1)
    catalog = encode(dict(protocol="maezo.engine-schemas.v2", schemas=schemas))
    with pytest.raises(ExternalCaseError):
        CompletionClient(tls, NoNetworkAuthority(), encode(cap), catalog, sha(catalog))
