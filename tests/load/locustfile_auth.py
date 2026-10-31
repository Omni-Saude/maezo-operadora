"""Teste de carga Locust do caminho AUTH (SP-OP-AUTH-001) — SO' contra a stack LOCAL (R-086).

Rodar (comando completo e pre-requisitos em tests/load/README.md):

    uv run --extra dev --with 'locust==2.46.3' locust -f tests/load/locustfile_auth.py \
        --host http://localhost:8080/engine-rest --headless -u 10 -r 5 -t 60s

Locust NAO e' dependencia do projeto: ``uv run --with`` resolve o pin num ambiente efemero
RESPEITANDO o ``[tool.uv] exclude-newer`` do pyproject (2.46.3 e' anterior ao corte), sem tocar
``uv.lock`` nem as lanes de CI que fazem ``uv sync --all-extras``.

O que roda:

- ``test_start``: recusa host nao-local; implanta BPMN+DMNs do AUTH sob um tenant descartavel
  ``r086perf_<hex>`` (nada de outra suite e' tocado num engine compartilhado);
- usuarios: as quatro avaliacoes DMN do AUTH + o start de SP-OP-AUTH-001 (dados sinteticos);
- ``quitting``: p95 de cada requisicao AUTH comparado a PLANS.md:923 (< 200 ms); acima disso o
  processo sai com codigo 1;
- ``test_stop``: apaga a implantacao do tenant em cascata (instancias criadas pela carga inclusas).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import auth_scenarios as scn  # irmao; o locust poe o diretorio do locustfile no sys.path
import requests
from locust import HttpUser, between, events, task

_STATE: dict[str, str] = {}


def _engine(environment: Any) -> str:
    host = environment.host or ""
    return scn.assert_local_url(host).rstrip("/")


@events.test_start.add_listener
def _deploy_auth(environment: Any, **_kwargs: Any) -> None:
    base = _engine(environment)
    tenant = scn.synthetic_tenant()
    paths: list[Path] = scn.auth_artifact_paths()
    files = [(p.name, (p.name, p.read_bytes(), "application/xml")) for p in paths]
    data = {
        "deployment-name": f"r086-locust-{tenant}",
        "enable-duplicate-filtering": "true",
        "deploy-changed-only": "true",
        "tenant-id": tenant,
    }
    resp = requests.post(f"{base}/deployment/create", data=data, files=files, timeout=60)
    resp.raise_for_status()
    _STATE["tenant"] = tenant
    _STATE["deployment_id"] = resp.json()["id"]


@events.test_stop.add_listener
def _undeploy_auth(environment: Any, **_kwargs: Any) -> None:
    deployment_id = _STATE.get("deployment_id")
    if not deployment_id:
        return
    resp = requests.delete(
        f"{_engine(environment)}/deployment/{deployment_id}",
        params={"cascade": "true", "skipCustomListeners": "true", "skipIoMappings": "true"},
        timeout=300,
    )
    resp.raise_for_status()


@events.quitting.add_listener
def _assert_p95(environment: Any, **_kwargs: Any) -> None:
    breaches: list[str] = []
    for (name, _method), entry in environment.stats.entries.items():
        if not name.startswith("auth_") or entry.num_requests == 0:
            continue
        p95 = entry.get_response_time_percentile(0.95)
        print(f"R086_LOCUST {name} n={entry.num_requests} falhas={entry.num_failures} p95_ms={p95}")
        if p95 >= scn.P95_BOUND_MS or entry.num_failures:
            breaches.append(f"{name}: p95={p95} ms, falhas={entry.num_failures}")
    if breaches:
        print(
            f"R086_LOCUST meta p95 < {scn.P95_BOUND_MS:.0f} ms ({scn.P95_BOUND_SOURCE}) VIOLADA: {breaches}"
        )
        environment.process_exit_code = 1


class AuthUser(HttpUser):
    wait_time = between(0.05, 0.2)

    def _evaluate(self, key: str) -> None:
        tenant = _STATE["tenant"]
        payload = {"variables": scn.camunda_variables(dict(scn.DMN_SCENARIOS[key]))}
        with self.client.post(
            f"/decision-definition/key/{key}/tenant-id/{tenant}/evaluate",
            json=payload,
            name=f"auth_dmn:{key}",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200 or len(resp.json()) != 1:
                resp.failure(f"status={resp.status_code}")

    @task
    def dmn_admissibility(self) -> None:
        self._evaluate("auth_admissibility")

    @task
    def dmn_sla(self) -> None:
        self._evaluate("auth_sla")

    @task
    def dmn_auto_approval(self) -> None:
        self._evaluate("auth_auto_approval")

    @task
    def dmn_criteria_contratual(self) -> None:
        self._evaluate("auth_criteria_contratual")

    @task
    def start_auth(self) -> None:
        tenant = _STATE["tenant"]
        business_key = scn.auth_business_key(tenant, scn.synthetic_guia())
        with self.client.post(
            f"/process-definition/key/{scn.AUTH_PROCESS_KEY}/tenant-id/{tenant}/start",
            json={"businessKey": business_key, "variables": scn.camunda_variables(scn.start_variables())},
            name="auth_start",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200 or resp.json().get("ended") is not False:
                resp.failure(f"status={resp.status_code}")
