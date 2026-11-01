"""Motor REAL: `pagto_lastro.resolve_lastro` (pacote PAGTO PR-D) sobre a costura de PR-C.

Prova contra o CIB Seven de verdade (nada de motor simulado neste arquivo) o que os testes
unitarios so' afirmam sobre o duble:

  1. a busca da origem de CONTAS pela identidade da PROPRIA ordem (filtro `eq` de duas variaveis,
     `numero_lote_tiss` + `prestador_id`) acha a instancia certa, e o terminal vem preenchido mesmo
     quando o motor devolve `endActivityId: null` (a particularidade documentada em PR-C);
  2. os tipos que o motor grava voltam como o classificador espera: `has_glosas` Boolean separa as
     pernas automaticas, valor String (`"1500.00"`) e Double viram centavos;
  3. a busca de RECURSO pela chave contratual `RECURSO-{tenant}-{guia}-{glosa}`;
  4. em curso -> `NAO_CONCLUIDO`; terminal sem obrigacao / inexistente / outro credor ->
     `NAO_ENCONTRADO`; duas origens -> `INDISPONIVEL`; acima do valor da origem -> `DIVERGENTE_VALOR`;
  5. o formato de producao: chamada SINCRONA numa thread propria (`asyncio.to_thread`, como o
     harness despacha um `FunctionWorker`) sobre o `FreshClientCibSevenTransport` do daemon.

Processos AD HOC (nunca em `spec/`, e com chaves proprias — redeployar `SP-OP-CONTAS-001` num motor
compartilhado contaminaria as outras suites): mesmos ids de evento final dos processos reais, e as
mesmas `FonteLastro` de producao com so' a `process_key` trocada (`resolve_lastro(fontes=...)`). Que
os ids de terminal sao os do BPMN real e' a cerca unitaria
`test_terminais_de_*_sao_exatamente_os_alcancados_pelos_handoffs`.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import httpx
import pytest

from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport
from maezo.tools.workers.pagto_lastro import FONTE_CONTAS, FONTE_RECURSO, resolve_lastro

from .conftest import RUN_ID

pytestmark = pytest.mark.integration

_TENANT = "amh"
_WORKER = f"it-pagto-lastro-{RUN_ID}"


def _bpmn(process_key: str, topic: str, finais: dict[str, str], padrao: str) -> str:
    """start -> tarefa externa(`topic`) -> gateway por `desfecho` -> evento final nomeado."""
    fluxos = "".join(
        f'<bpmn:sequenceFlow id="F_{fim}" sourceRef="GW" targetRef="{fim}">'
        f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">'
        f"${{desfecho == '{valor}'}}</bpmn:conditionExpression></bpmn:sequenceFlow>"
        for valor, fim in finais.items()
        if fim != padrao
    )
    eventos = "".join(
        f'<bpmn:endEvent id="{fim}"><bpmn:incoming>F_{fim}</bpmn:incoming></bpmn:endEvent>'
        for fim in finais.values()
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  id="Definitions_{process_key}" targetNamespace="http://maezo.health/test/pagto-d">
  <bpmn:process id="{process_key}" isExecutable="true" camunda:historyTimeToLive="1">
    <bpmn:startEvent id="Start"><bpmn:outgoing>F_Start</bpmn:outgoing></bpmn:startEvent>
    <bpmn:serviceTask id="Tarefa" camunda:type="external" camunda:topic="{topic}">
      <bpmn:incoming>F_Start</bpmn:incoming><bpmn:outgoing>F_Tarefa</bpmn:outgoing>
    </bpmn:serviceTask>
    <bpmn:exclusiveGateway id="GW" default="F_{padrao}">
      <bpmn:incoming>F_Tarefa</bpmn:incoming>
    </bpmn:exclusiveGateway>
    <bpmn:sequenceFlow id="F_Start" sourceRef="Start" targetRef="Tarefa"/>
    <bpmn:sequenceFlow id="F_Tarefa" sourceRef="Tarefa" targetRef="GW"/>
    <bpmn:sequenceFlow id="F_{padrao}" sourceRef="GW" targetRef="{padrao}"/>
    {fluxos}
    {eventos}
  </bpmn:process>
</bpmn:definitions>
"""


async def _deploy(client: httpx.AsyncClient, process_key: str, xml: str) -> None:
    resp = await client.post(
        "/deployment/create",
        data={"deployment-name": f"pagto-d-{process_key}", "tenant-id": _TENANT},
        files={f"{process_key}.bpmn": (f"{process_key}.bpmn", xml, "text/xml")},
    )
    if resp.status_code >= 300:
        raise AssertionError(f"deployment failed: {resp.status_code} {resp.text}")


async def _concluir(client: httpx.AsyncClient, *, topic: str, business_key: str) -> None:
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
    tarefas = resp.json()
    assert len(tarefas) == 1, f"expected one locked task for {business_key}, got {tarefas!r}"
    done = await client.post(f"/external-task/{tarefas[0]['id']}/complete", json={"workerId": _WORKER})
    if done.status_code >= 300:
        raise AssertionError(f"complete failed: {done.status_code} {done.text}")


async def test_resolve_lastro_against_real_engine(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    contas_key = f"pagto_d_it_contas_{RUN_ID}"
    recurso_key = f"pagto_d_it_recurso_{RUN_ID}"
    contas_topic = f"pagto.d.it.contas.{RUN_ID}"
    recurso_topic = f"pagto.d.it.recurso.{RUN_ID}"
    await _deploy(
        engine_client,
        contas_key,
        _bpmn(
            contas_key,
            contas_topic,
            {
                "humano": "End_ContaAprovadaHumano",
                "auto": "End_ContaAprovadaIntegral",
                "glosa": "End_GlosaAplicadaHumano",
            },
            padrao="End_GlosaAplicadaHumano",
        ),
    )
    await _deploy(
        engine_client,
        recurso_key,
        _bpmn(
            recurso_key,
            recurso_topic,
            {"deferido": "End_RecursoDeferido", "indeferido": "End_RecursoIndeferido"},
            padrao="End_RecursoIndeferido",
        ),
    )
    fontes = {
        "contas_adjudicacao_humana": dataclasses.replace(FONTE_CONTAS, process_key=contas_key),
        "contas_adjudicacao_automatica": dataclasses.replace(FONTE_CONTAS, process_key=contas_key),
        "recurso_deferimento_humano": dataclasses.replace(FONTE_RECURSO, process_key=recurso_key),
    }
    # O transporte do daemon (fresh-client por chamada), tenant-bound como em producao.
    engine = FreshClientCibSevenTransport(engine_base_url, tenant_id=_TENANT)

    async def _lastro(**ordem: Any) -> dict[str, Any]:
        variaveis = {"tenant_id": _TENANT, **ordem}
        # Formato de producao: sincrono, numa thread com o seu proprio loop (asyncio.run interno).
        return await asyncio.to_thread(resolve_lastro, variaveis, engine=engine, fontes=fontes)

    prestador = f"prov:it-{RUN_ID}"
    lote = {nome: f"LOTE-{nome}-{RUN_ID}" for nome in ("humano", "auto", "glosa", "ativo", "nada", "duplo")}

    sequencia = iter(range(100))

    async def _contas(nome: str, *, desfecho: str, concluir: bool = True, **extra: Any) -> str:
        bk = f"bk-pagto-d-{nome}-{RUN_ID}-{next(sequencia)}"
        inst = await engine.start_process_instance(
            contas_key,
            bk,
            {"numero_lote_tiss": lote[nome], "prestador_id": prestador, "desfecho": desfecho, **extra},
        )
        if concluir:
            await _concluir(engine_client, topic=contas_topic, business_key=bk)
        return inst.instance_id

    id_humano = await _contas("humano", desfecho="humano", has_glosas=True, valor_liberado_brl="1500.00")
    id_auto = await _contas("auto", desfecho="auto", has_glosas=False, valor_apresentado_brl=800.0)
    await _contas("glosa", desfecho="glosa", has_glosas=True, valor_liberado_brl="10.00")
    id_ativo = await _contas("ativo", desfecho="humano", concluir=False, valor_liberado_brl="10.00")
    await _contas("duplo", desfecho="humano", valor_liberado_brl="10.00")
    await _contas("duplo", desfecho="humano", valor_liberado_brl="11.00", has_glosas=True)

    humano = {"lastro_origem": "contas_adjudicacao_humana", "prestador_id": prestador}

    # (1)+(2): humano, valor String -> centavos; terminal derivado mesmo com endActivityId null.
    out = await _lastro(numero_lote_tiss=lote["humano"], valor_pagamento_cents=150000, **humano)
    assert out == {
        "lastro_status": "CONFIRMADO",
        "lastro_origem_tipo": "contas_humana",
        "lastro_origem_instancia_id": id_humano,
        "lastro_origem_terminal": "End_ContaAprovadaHumano",
        "lastro_valor_origem_cents": 150000,
    }
    # (4) tolerancia do dono: abaixo segue, acima diverge.
    out = await _lastro(numero_lote_tiss=lote["humano"], valor_pagamento_cents=149000, **humano)
    assert out["lastro_status"] == "CONFIRMADO"
    out = await _lastro(numero_lote_tiss=lote["humano"], valor_pagamento_cents=150001, **humano)
    assert out["lastro_status"] == "DIVERGENTE_VALOR"

    # (2) perna automatica sem glosa: Boolean `has_glosas=false` + Double apresentado.
    out = await _lastro(
        numero_lote_tiss=lote["auto"],
        valor_pagamento_cents=80000,
        lastro_origem="contas_adjudicacao_automatica",
        prestador_id=prestador,
    )
    assert (out["lastro_status"], out["lastro_origem_tipo"]) == ("CONFIRMADO", "contas_auto_sem_glosa")
    assert out["lastro_origem_instancia_id"] == id_auto
    assert out["lastro_valor_origem_cents"] == 80000

    # (4) terminal sem obrigacao, em curso, inexistente, outro credor, duas origens.
    out = await _lastro(numero_lote_tiss=lote["glosa"], valor_pagamento_cents=1000, **humano)
    assert (out["lastro_status"], out["lastro_origem_terminal"]) == (
        "NAO_ENCONTRADO",
        "End_GlosaAplicadaHumano",
    )
    out = await _lastro(numero_lote_tiss=lote["ativo"], valor_pagamento_cents=1000, **humano)
    assert (out["lastro_status"], out["lastro_origem_instancia_id"]) == ("NAO_CONCLUIDO", id_ativo)
    out = await _lastro(numero_lote_tiss=lote["nada"], valor_pagamento_cents=1000, **humano)
    assert out["lastro_status"] == "NAO_ENCONTRADO"
    out = await _lastro(
        numero_lote_tiss=lote["humano"],
        valor_pagamento_cents=1000,
        lastro_origem="contas_adjudicacao_humana",
        prestador_id=f"prov:outro-{RUN_ID}",
    )
    assert out["lastro_status"] == "NAO_ENCONTRADO"
    out = await _lastro(numero_lote_tiss=lote["duplo"], valor_pagamento_cents=1000, **humano)
    assert out["lastro_status"] == "INDISPONIVEL"

    # (3) RECURSO pela chave contratual.
    guia, glosa = f"GUIA-{RUN_ID}", f"GLOSA-{RUN_ID}"
    bk_recurso = f"RECURSO-{_TENANT}-{guia}-{glosa}"
    inst = await engine.start_process_instance(
        recurso_key,
        bk_recurso,
        {"prestador_id": prestador, "valor_deferido_brl": "200.00", "desfecho": "deferido"},
    )
    await _concluir(engine_client, topic=recurso_topic, business_key=bk_recurso)
    recurso = {"lastro_origem": "recurso_deferimento_humano", "numero_guia_tiss": guia, "glosa_id": glosa}
    out = await _lastro(prestador_id=prestador, valor_pagamento_cents=20000, **recurso)
    assert out == {
        "lastro_status": "CONFIRMADO",
        "lastro_origem_tipo": "recurso_humano",
        "lastro_origem_instancia_id": inst.instance_id,
        "lastro_origem_terminal": "End_RecursoDeferido",
        "lastro_valor_origem_cents": 20000,
    }
    out = await _lastro(prestador_id=f"prov:outro-{RUN_ID}", valor_pagamento_cents=20000, **recurso)
    assert out["lastro_status"] == "NAO_ENCONTRADO"

    # Motor fora (porta fechada): falha fechada, nunca excecao nem "confirmado".
    fora = FreshClientCibSevenTransport("http://127.0.0.1:9/engine-rest", tenant_id=_TENANT)
    out = await asyncio.to_thread(
        resolve_lastro,
        {"tenant_id": _TENANT, "numero_lote_tiss": lote["humano"], "valor_pagamento_cents": 1, **humano},
        engine=fora,
        fontes=fontes,
    )
    assert out["lastro_status"] == "INDISPONIVEL"
