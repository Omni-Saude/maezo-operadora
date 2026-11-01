"""Motor REAL: `resolve_historic_instance` (PAGTO PR-C, MPTT P2, padrao DL-0050).

Prova contra o CIB Seven de verdade (`docker compose --profile core up`, ADR-0011 — nada de
motor simulado neste arquivo) o que os testes unitarios so' podem afirmar sobre um fio simulado:

  1. a consulta `POST /history/process-instance` com filtro de variavel `eq` e `tenantIdIn` e'
     aceita pelo motor e devolve so' a instancia que casa;
  2. `endActivityId` vem preenchido para a instancia que terminou (o evento final do processo) e
     ausente para a que segue ativa;
  3. as variaveis nomeadas voltam do historico com o tipo certo;
  4. nenhuma / duas instancias casando viram `HistoricInstanceNotFoundError` /
     `HistoricInstanceAmbiguousError` — nunca a primeira linha.

Processo ad hoc de teste (nunca em `spec/`), com um topico que nenhum worker drena: o proprio teste
faz fetch-and-lock + complete pelo REST do motor para levar UMA instancia ao fim. Se o motor estiver
fora, o arquivo PULA pelo `_skip_if_engine_unreachable` do `conftest.py`, com motivo explicito.
"""

from __future__ import annotations

import httpx
import pytest

from maezo.tools.mcp_cibseven.transport import (
    CibSevenHttpTransport,
    HistoricInstanceAmbiguousError,
    HistoricInstanceNotFoundError,
)

from .conftest import RUN_ID, deploy_process

pytestmark = pytest.mark.integration

_TENANT = "amh"
_WORKER = f"it-history-resolution-{RUN_ID}"


async def _complete_external_task(client: httpx.AsyncClient, *, topic: str, business_key: str) -> None:
    """Leva a instancia `business_key` ao fim: o teste faz o papel do worker pelo REST do motor."""
    resp = await client.post(
        "/external-task/fetchAndLock",
        json={
            "workerId": _WORKER,
            "maxTasks": 1,
            "topics": [{"topicName": topic, "lockDuration": 60000, "businessKey": business_key}],
        },
    )
    if resp.status_code >= 300:
        raise AssertionError(f"fetchAndLock failed: {resp.status_code} {resp.text}")
    tasks = resp.json()
    assert len(tasks) == 1, f"expected exactly one locked task for {business_key}, got {tasks!r}"
    done = await client.post(f"/external-task/{tasks[0]['id']}/complete", json={"workerId": _WORKER})
    if done.status_code >= 300:
        raise AssertionError(f"complete failed: {done.status_code} {done.text}")


async def test_resolve_historic_instance_against_real_engine(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    process_key = f"pagto_c_it_hist_{RUN_ID}"
    topic = f"pagto.c.it.hist.{RUN_ID}"
    await deploy_process(
        engine_client,
        process_key=process_key,
        topic=topic,
        deployment_name=f"pagto-c-hist-{RUN_ID}",
        tenant_id=_TENANT,
    )
    bk_done = f"bk-hist-done-{RUN_ID}"
    bk_live = f"bk-hist-live-{RUN_ID}"
    bk_twin = f"bk-hist-twin-{RUN_ID}"
    prestador_unico = f"P-{RUN_ID}-unico"
    prestador_duplo = f"P-{RUN_ID}-duplo"

    transport = CibSevenHttpTransport(engine_base_url, tenant_id=_TENANT)
    try:
        done = await transport.start_process_instance(
            process_key,
            bk_done,
            {"prestador_id": prestador_unico, "valor_liberado_centavos": 150000, "numero_lote_tiss": "L1"},
        )
        await transport.start_process_instance(process_key, bk_live, {"prestador_id": prestador_duplo})
        await transport.start_process_instance(process_key, bk_twin, {"prestador_id": prestador_duplo})
        await _complete_external_task(engine_client, topic=topic, business_key=bk_done)

        # (1)+(2)+(3): por chave de negocio — terminou no evento final, variaveis tipadas.
        by_key = await transport.resolve_historic_instance(
            process_key, business_key=bk_done, names=("valor_liberado_centavos", "numero_lote_tiss")
        )
        assert by_key.instance_id == done.instance_id
        assert by_key.state == "COMPLETED"
        assert by_key.end_activity_id == "EndEvent_1"
        assert by_key.variables == {"valor_liberado_centavos": 150000, "numero_lote_tiss": "L1"}

        # por filtro de variavel (sem chave): a MESMA instancia, e o motor aplicou o filtro.
        by_filter = await transport.resolve_historic_instance(
            process_key, variable_equals={"prestador_id": prestador_unico}, names=("prestador_id",)
        )
        assert by_filter.instance_id == done.instance_id
        assert by_filter.variables == {"prestador_id": prestador_unico}

        # instancia ainda ativa: sem endActivityId.
        live = await transport.resolve_historic_instance(process_key, business_key=bk_live)
        assert live.state == "ACTIVE"
        assert live.end_activity_id is None

        # (4): duas casam -> ambiguo; nenhuma casa -> nao encontrado (chave, filtro e definicao).
        with pytest.raises(HistoricInstanceAmbiguousError):
            await transport.resolve_historic_instance(
                process_key, variable_equals={"prestador_id": prestador_duplo}
            )
        with pytest.raises(HistoricInstanceNotFoundError):
            await transport.resolve_historic_instance(process_key, business_key=f"bk-hist-nope-{RUN_ID}")
        with pytest.raises(HistoricInstanceNotFoundError):
            await transport.resolve_historic_instance(
                process_key, variable_equals={"prestador_id": f"P-{RUN_ID}-x"}
            )
        with pytest.raises(HistoricInstanceNotFoundError):
            await transport.resolve_historic_instance(f"{process_key}_outro", business_key=bk_done)
    finally:
        await transport.close()
