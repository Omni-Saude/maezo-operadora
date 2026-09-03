"""Fences ESTATICOS de SP-OP-ANS-CRON-001 — BPMN <-> taxonomia do worker (sem engine).

Por que este modulo existe (MINOR-5 do gatekeeper R1 de ANS-CRON-DEAD-CODE): estes tres fences
sao pura leitura de XML + introspeccao do harness — nao tocam engine, banco nem rede. Enquanto
viviam em `tests/integration/processes/test_sp_op_ans_cron_001.py` estavam sob
`pytestmark = pytest.mark.integration` E sob o autouse de sessao
`tests/integration/conftest.py::_skip_if_engine_unreachable`, logo NENHUM dos contextos de CI
obrigatorios os executava: uma regressao que reintroduzisse `tenant_id` em `event_payload_vars`
ou desalinhasse um `timeCycle` passaria VERDE nos quatro checks obrigatorios. Movidos para
`tests/unit/spec/` eles rodam no job unitario, que E obrigatorio.

O que cada um prova:

  1. `test_ans_cron_registered_topics_reachable_from_bpmn` — bijecao entre os topicos
     `operadora.ans_cron.*` que `register_ans_cron_workers` registra e os que o BPMN declara
     (PERSP-B5-ANSCRON-TOPICS: nem registro orfao, nem serviceTask sem worker).
  2. `test_ans_cron_timecycle_do_bpmn_bate_com_a_taxonomia_do_worker` — o `timeCycle` do
     TimerStartEvent de cada definition corresponde ao periodo ISO que `_REPORT_PERIODICIDADE`
     associa ao literal `ans_cron_report_type` do `ST_ResolverCompetencia*` da MESMA definition.
  3. `test_ans_cron_payload_vars_nao_carregam_tenant_id` — `tenant_id` nunca entra em
     `event_payload_vars` (o carimbo de tenant e do publicador generico, via `setdefault`).

DRAFT/verify regulatorio: nenhum teste aqui assere conteudo regulatorio. As cadencias
(`timeCycle`), as citacoes RN dos literais de `report_type` e o mapeamento periodo->competencia
seguem NAO confirmados com o regulatorio (`docs/review-queue.md`,
`docs/sme-dispatch/regulatorio/PACKAGE.md`); estes fences provam COERENCIA INTERNA entre
artefatos, nunca correcao regulatoria.

As metades que EXIGEM engine (o tick real do timer, a competencia computada no fato, a ausencia
de auto-start de SP-OP-ANS-SUBMIT-001) continuam em
`tests/integration/processes/test_sp_op_ans_cron_001.py`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from maezo.tools.workers.ans_cron import _REPORT_PERIODICIDADE, register_ans_cron_workers
from maezo.tools.workers.harness import FakeKafkaPublisher, FakeWorkerTransport, WorkerHarness

_REPO = Path(__file__).resolve().parents[3]
_BPMN_CRON = _REPO / "spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn"

_PUBLISH_TOPIC = "operadora.events.publish"

#: O topico proprio de `ans_cron.py` — declarado por `ST_ResolverCompetencia*` nas 5 definitions.
_ANS_CRON_TRIGGER_TOPIC = "operadora.ans_cron.trigger_submissions"

#: `R/<ISO period>` do BPMN -> periodo ISO da taxonomia do worker. `R/P1Y` e a forma que o CIB
#: Seven aceita para o ciclo anual; a taxonomia Python usa `P12M` para o mesmo periodo.
_TIMECYCLE_PARA_ISO = {"R/P1M": "P1M", "R/P3M": "P3M", "R/P1Y": "P12M"}

_NS = {
    "b": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "c": "http://camunda.org/schema/1.0/bpmn",
}


def _bpmn_xml() -> str:
    return _BPMN_CRON.read_text(encoding="utf-8")


def test_ans_cron_registered_topics_reachable_from_bpmn() -> None:
    """PERSP-B5-ANSCRON-TOPICS (substitui `..._unreachable_from_bpmn`): TODO topico
    `operadora.ans_cron.*` que `register_ans_cron_workers` registra e declarado por algum
    serviceTask do BPMN — e o BPMN nao declara nenhum topico `operadora.ans_cron.*` sem worker.

    Varredura do XML (sem engine) + introspeccao real de `harness.registered_topics` (o MESMO
    harness real usado pelos testes de seam contra o engine, nao um mock).
    """
    bpmn_topics = set(re.findall(r'camunda:topic="([^"]*)"', _bpmn_xml()))
    assert bpmn_topics == {_ANS_CRON_TRIGGER_TOPIC, _PUBLISH_TOPIC}, (
        f"topicos declarados pelo BPMN mudaram: {bpmn_topics}"
    )

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="qa-static-probe")
    register_ans_cron_workers(harness, FakeKafkaPublisher())
    ans_cron_registered = {t for t in harness.registered_topics if t.startswith("operadora.ans_cron.")}
    assert ans_cron_registered == {_ANS_CRON_TRIGGER_TOPIC}, (
        f"register_ans_cron_workers deveria registrar exatamente {_ANS_CRON_TRIGGER_TOPIC!r}; "
        f"encontrado: {ans_cron_registered}"
    )

    # Bidirecional: nenhum registro orfao, nenhum topico ans_cron do BPMN sem worker.
    assert ans_cron_registered <= bpmn_topics, (
        f"topico registrado sem serviceTask: {ans_cron_registered - bpmn_topics}"
    )
    bpmn_ans_cron = {t for t in bpmn_topics if t.startswith("operadora.ans_cron.")}
    assert bpmn_ans_cron <= ans_cron_registered, (
        f"serviceTask sem worker registrado: {bpmn_ans_cron - ans_cron_registered}"
    )


def test_ans_cron_timecycle_do_bpmn_bate_com_a_taxonomia_do_worker() -> None:
    """Fence anti-drift: o `timeCycle` do TimerStartEvent de cada definition e o periodo ISO que
    `_REPORT_PERIODICIDADE` associa ao `report_type` literal do `ST_ResolverCompetencia*` da MESMA
    definition. Sao as duas metades do agendamento per-report_type (GAP-ANS-1) — se uma mudar sem
    a outra, a competencia deixa de corresponder ao ciclo que a disparou.

    Todas as periodicidades permanecem **DRAFT/verify regulatorio** (nem os `timeCycle` nem o
    mapeamento periodo->competencia foram confirmados com o regulatorio — `docs/review-queue.md`);
    este teste prova COERENCIA INTERNA, nunca correcao regulatoria.
    """
    root = ET.fromstring(_bpmn_xml())
    processes = root.findall("b:process", _NS)
    assert len(processes) == len(_REPORT_PERIODICIDADE) == 5, (
        "uma process definition por report_type (GAP-ANS-1): "
        f"{len(processes)} definitions vs {len(_REPORT_PERIODICIDADE)} tipos"
    )

    vistos: dict[str, str] = {}
    for proc in processes:
        starts = proc.findall("b:startEvent", _NS)
        assert len(starts) == 1, f"{proc.get('id')}: exatamente 1 TimerStartEvent por definition"
        cycles = starts[0].findall("b:timerEventDefinition/b:timeCycle", _NS)
        assert len(cycles) == 1, f"{proc.get('id')}: TimerStartEvent sem timeCycle"
        time_cycle = (cycles[0].text or "").strip()

        resolvers = [
            st
            for st in proc.findall("b:serviceTask", _NS)
            if st.get(f"{{{_NS['c']}}}topic") == _ANS_CRON_TRIGGER_TOPIC
        ]
        assert len(resolvers) == 1, f"{proc.get('id')}: exatamente 1 ST_ResolverCompetencia*"
        literais = {
            p.get("name"): (p.text or "").strip()
            for p in resolvers[0].findall("b:extensionElements/c:inputOutput/c:inputParameter", _NS)
        }
        report_type = literais.get("ans_cron_report_type", "")

        assert report_type in _REPORT_PERIODICIDADE, (
            f"{proc.get('id')}: report_type {report_type!r} fora da taxonomia do worker"
        )
        _periodicidade_ptbr, iso = _REPORT_PERIODICIDADE[report_type]
        assert _TIMECYCLE_PARA_ISO.get(time_cycle) == iso, (
            f"{proc.get('id')} ({report_type}): timeCycle {time_cycle!r} nao corresponde a "
            f"periodicidade {iso!r} da taxonomia do worker"
        )
        vistos[report_type] = time_cycle

    assert set(vistos) == set(_REPORT_PERIODICIDADE)
    assert len(set(vistos.values())) >= 2, (
        "AC de GAP-ANS-1: pelo menos 2 periodicidades DISTINTAS entre os report_types"
    )


def test_ans_cron_payload_vars_nao_carregam_tenant_id() -> None:
    """Fence de regressao: `tenant_id` NAO pode entrar no `event_payload_vars` deste BPMN.

    O agendador e tenant-agnostico (TimerStartEvent, sem contexto de caso). O tenant do fato vem
    do carimbo `setdefault` do publicador generico (`events.py`, seam `deployment_tenant_id`). Se
    `tenant_id` fosse listado aqui, a variavel de processo `tenant_id` que
    `trigger_submissions` devolve (`""` numa composicao sem seam) viajaria no payload, o
    `setdefault` nao sobrescreveria, e a regra `ans.cron_due` da ponte — que exige tenant
    nao-vazio — ficaria permanentemente dormente (ou produziria a chave degenerada `ANSSUB--...`).
    """
    payload_var_lists = re.findall(r'name="event_payload_vars">([^<]*)</camunda:inputParameter>', _bpmn_xml())
    assert len(payload_var_lists) == 5, f"esperado 1 event_payload_vars por definition; {payload_var_lists}"
    for raw in payload_var_lists:
        nomes = [n.strip() for n in raw.split(",") if n.strip()]
        assert "tenant_id" not in nomes, (
            "tenant_id em event_payload_vars desarma o carimbo de tenant do publicador generico"
        )
        assert {"report_type", "periodicidade", "competencia"} <= set(nomes), (
            f"event_payload_vars perdeu identificadores do despacho: {nomes}"
        )
