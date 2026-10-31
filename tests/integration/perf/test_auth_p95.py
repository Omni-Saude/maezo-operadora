"""p95 de latencia do AUTH contra o engine REAL (R-086, achado F-120; ADR-0011: sem mock).

Meta: ``p95 < 200 ms`` — PLANS.md:923 (Verificacao do M13: "Latencia p95 < 200ms para AUTH").
Nenhum outro numero e' inventado aqui: tamanho de amostra e concorrencia sao PARAMETROS DE
MEDICAO, nao metas. Os niveis C=1 e C=10 repetem os tiers ja' medidos pela auditoria
(docs/audits/FORENSIC-COMPLETENESS-2026-10/PERFORMANCE.md) e nao sao afirmacao de capacidade —
nao existe baseline de volume por tenant (docs/observability/SLO.md:231-232, G3 aberto).

O que e' medido (escopo declarado em ``tests/load/auth_scenarios.py``):

- ``dmn:<chave>`` — as quatro DMNs do AUTH avaliadas pelo MESMO cliente que os workers usam,
  ``CibSevenDmnTransport.evaluate(..., tenant=...)`` (cliente httpx novo por chamada + resolucao
  de versao cacheada — o custo real de producao do trecho DMN, ADR-0028);
- ``start`` — ``POST /process-definition/key/SP-OP-AUTH-001/tenant-id/{t}/start`` com a business
  key contratual montada pelo compositor real; a resposta volta quando a instancia para na
  primeira external task (ST_PublishReceived).

Isolamento num engine compartilhado: os artefatos AUTH sao implantados sob um tenant
descartavel ``r086perf_<hex>`` e a implantacao inteira e' apagada em cascata no teardown — nada
deste modulo encosta em definicoes/instancias de outra suite. Marcadores: ``integration`` (fora
do ``make test``; cai no shard ``restante`` da lane de motor real) + ``performance`` (selecao
explicita com ``-m performance``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest

from maezo.platform.deploy.engine_deploy import EngineDeployClient
from maezo.tools.process_business_keys import auth_business_key
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from tests.load.auth_scenarios import (
    AUTH_ARTIFACTS,
    AUTH_PROCESS_KEY,
    DMN_SCENARIOS,
    P95_BOUND_MS,
    P95_BOUND_SOURCE,
    assert_local_url,
    auth_artifact_paths,
    camunda_variables,
    nearest_rank_percentile,
    start_variables,
    synthetic_guia,
    synthetic_tenant,
)
from tests.load.auth_scenarios import auth_business_key as auth_business_key_mirror

pytestmark = [pytest.mark.integration, pytest.mark.performance]

#: Requisicoes descartadas antes da medicao (compilacao/cache da DMN no engine, pool de conexoes).
WARMUP = 20
#: Amostras medidas por (cenario, tier). 200 da' ao p95 uma posicao de rank 190 — dez amostras
#: acima dele, nao uma so'.
SAMPLES = 200
TIERS = (1, 10)
SCENARIOS = (*(f"dmn:{key}" for key in DMN_SCENARIOS), "start")


@pytest.fixture(scope="module")
def auth_tenant(engine_base_url: str) -> Iterator[str]:
    """Implanta BPMN+DMNs do AUTH sob um tenant descartavel; apaga tudo (cascade) no fim."""
    assert_local_url(engine_base_url)
    tenant = synthetic_tenant()
    client = EngineDeployClient(base_url=engine_base_url)
    try:
        outcome = client.deploy(auth_artifact_paths(), name=f"r086-perf-{tenant}", tenant_id=tenant)
    finally:
        client.close()
    assert outcome.deployed_count == len(AUTH_ARTIFACTS), outcome
    try:
        yield tenant
    finally:
        resp = httpx.delete(
            f"{engine_base_url}/deployment/{outcome.deployment_id}",
            params={"cascade": "true", "skipCustomListeners": "true", "skipIoMappings": "true"},
            timeout=120.0,
        )
        resp.raise_for_status()


async def _measure(call: Callable[[], Awaitable[None]], *, concurrency: int) -> tuple[list[float], float]:
    """Roda WARMUP chamadas sem medir e SAMPLES medidas com ``concurrency`` em voo."""
    for _ in range(WARMUP):
        await call()
    gate = asyncio.Semaphore(concurrency)
    samples: list[float] = []

    async def one() -> None:
        async with gate:
            started = time.perf_counter()
            await call()
            samples.append((time.perf_counter() - started) * 1000.0)

    t0 = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(SAMPLES)))
    return samples, time.perf_counter() - t0


def _dmn_call(engine_base_url: str, tenant: str, key: str) -> Callable[[], Awaitable[None]]:
    transport = CibSevenDmnTransport(engine_base_url)
    variables = dict(DMN_SCENARIOS[key])

    async def call() -> None:
        rows, version = await transport.evaluate(key, variables, tenant=tenant)
        # hitPolicy FIRST nas quatro tabelas: exatamente uma linha, sempre (catch-all r99).
        assert len(rows) == 1, (key, rows)
        assert version.key == key

    return call


def _start_call(client: httpx.AsyncClient, tenant: str) -> Callable[[], Awaitable[None]]:
    async def call() -> None:
        guia = synthetic_guia()
        business_key = auth_business_key(tenant_id=tenant, numero_guia_tiss=guia)
        assert business_key == auth_business_key_mirror(tenant, guia)
        resp = await client.post(
            f"/process-definition/key/{AUTH_PROCESS_KEY}/tenant-id/{tenant}/start",
            json={"businessKey": business_key, "variables": camunda_variables(start_variables())},
        )
        assert resp.status_code == 200, resp.text[:300]
        body: dict[str, Any] = resp.json()
        # Parou na 1a external task: a instancia existe e NAO terminou.
        assert body.get("ended") is False, body

    return call


@pytest.mark.parametrize("concurrency", TIERS, ids=lambda c: f"c{c}")
@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_auth_p95_abaixo_da_meta(
    engine_base_url: str,
    auth_tenant: str,
    scenario: str,
    concurrency: int,
    record_property: Callable[[str, object], None],
) -> None:
    async with httpx.AsyncClient(base_url=engine_base_url, timeout=30.0) as client:
        if scenario == "start":
            call = _start_call(client, auth_tenant)
        else:
            call = _dmn_call(engine_base_url, auth_tenant, scenario.removeprefix("dmn:"))
        samples, wall_s = await _measure(call, concurrency=concurrency)

    assert len(samples) == SAMPLES
    p50 = nearest_rank_percentile(samples, 50)
    p95 = nearest_rank_percentile(samples, 95)
    p99 = nearest_rank_percentile(samples, 99)
    report = {
        "cenario": f"auth_{scenario}",
        "tier_concorrencia": concurrency,
        "n": SAMPLES,
        "rps": round(SAMPLES / wall_s, 1),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "max_ms": round(max(samples), 2),
        "meta_p95_ms": P95_BOUND_MS,
        "fonte_meta": P95_BOUND_SOURCE,
    }
    record_property("auth_p95", json.dumps(report))
    print("R086_P95 " + json.dumps(report))
    assert p95 < P95_BOUND_MS, (
        f"AUTH {scenario} C={concurrency}: p95 {p95:.1f} ms >= {P95_BOUND_MS:.0f} ms "
        f"({P95_BOUND_SOURCE}); relatorio: {report}"
    )
