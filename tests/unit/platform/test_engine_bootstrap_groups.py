"""Offline bootstrap request-contract proof, not a mocked engine integration."""

from __future__ import annotations

import json

import httpx
import pytest

from maezo.platform.engine_bootstrap import bootstrap as b


@pytest.mark.parametrize(
    "existing", [set(), {"plantao-clinico"}, {name for name, _ in b.GRUPOS_DE_ATENDIMENTO}]
)
def test_bootstrap_only_creates_missing_canonical_queues_without_grants_or_memberships(
    monkeypatch: pytest.MonkeyPatch, existing: set[str]
) -> None:
    monkeypatch.setenv("ENGINE_REST_URL", "http://bootstrap.invalid/engine-rest")
    groups = set(existing) | {b.GRUPO_ADMIN, b.GRUPO_LEITURA, "unrelated-queue", "plantaoClinico"}
    canonical = {name for name, _ in b.GRUPOS_DE_ATENDIMENTO}
    assert canonical == {
        "plantao-clinico",
        "enfermagem-triagem",
        "atendimento-humano",
        "supervisao-atendimento",
    }
    grants = [
        {
            "id": f"read-{kind}",
            "groupId": b.GRUPO_LEITURA,
            "type": 1,
            "resourceType": kind,
            "resourceId": target,
        }
        for kind, target, _ in b.GRANTS_DO_GRUPO_DE_LEITURA
    ]
    writes = []

    def respond(request: httpx.Request) -> httpx.Response:
        route = request.url.path.removeprefix("/engine-rest")
        if request.method == "GET":
            data = {
                "/user": [{"id": "existingadmin"}],
                "/group": [{"id": name} for name in groups],
                "/deployment": [{"id": "retained", "name": "maezo-spec-processes"}],
                "/authorization": grants,
                "/filter": [],
            }
            assert route in data
            return httpx.Response(200, json=data[route])
        writes.append((request.method, route, json.loads(request.content or "{}")))
        assert request.method == "POST" and route == "/group/create"
        body = json.loads(request.content)
        assert body["id"] in canonical - groups
        assert body["type"] == "WORKFLOW"
        groups.add(body["id"])
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        plan = b.planejar(http, admin="existingadmin", preservar="maezo-spec-processes")
        assert set(plan.filas_a_criar) == canonical - existing
        assert not plan.grants_de_leitura_a_criar
        assert not plan.grupos_a_apagar
        b._executar(http, plan, admin="existingadmin", senha="unused-synthetic")
        assert len(writes) == len(canonical - existing)
        writes.clear()
        plan = b.planejar(http, admin="existingadmin", preservar="maezo-spec-processes")
        assert plan.filas_a_criar == []
        b._executar(http, plan, admin="existingadmin", senha="unused-synthetic")
        assert writes == []
        assert {"unrelated-queue", "plantaoClinico"} <= groups
