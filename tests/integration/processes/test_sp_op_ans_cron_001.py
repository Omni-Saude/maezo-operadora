"""SP-OP-ANS-CRON-001 — suite de integracao executavel (engine CIB Seven REAL).

Test spec: `docs/processes/test-specs/SP-OP-ANS-CRON-001.md`.

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`,
`tests/integration/processes/test_sp_op_ans_submit_001.py`, lines ~1400-1557 — the "GAP-ANS-1
seam cron" section: `SP-OP-ANS-CRON-001 -> fato ans.cron_due -> bridge -> SP-OP-ANS-SUBMIT-001`)
— T3.1 phase 2 (13-family process-suite port). The donor's SUBMIT-001 tests proper are ported in
the sibling `test_sp_op_ans_submit_001.py` (own module docstring cross-references back here); THIS
file owns the CRON dispatch-by-fact seam only, matching the v2 worker-module boundary
(`ans_cron.py` vs `ans_submit.py`).

`ans_cron.py` schedules 5 sibling process DEFINITIONS, all deployed from ONE BPMN file
(`spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` — deploying it deploys all 5
process ids at once, see `deploy_artifacts` below): `SP-OP-ANS-CRON-001-RN124SIP` (mensal, start
`Start_CronRn124Sip`), `-RN209` (mensal), `-RN388` (anual), `-RN424TISS` (mensal), `-DIOPS`
(trimestral). Each has EXACTLY ONE TimerStartEvent (no REST `start_by_key` — see
`engine_rest.py`'s dedicated `start_timer_job_id`/`execute_job` helpers, built specifically for
this family: "timer-START jobs (SP-OP-ANS-CRON-001 dispatch por fato)"). This module SELF-CONTAINS
its fixtures (does NOT edit the shared `conftest.py`) — mirrors `test_sp_op_cancel_001.py`'s
convention; only the shared `engine` fixture + `drain_topics()` helper are reused from
`.conftest` (v2 convention, see `test_sp_op_ans_submit_001.py`'s own PORT NOTES for the same
choice).

Dados sinteticos: tenant do deployment (fixture `audit_tenant`), literais de `report_type`
RN-citation (`RN_124_SIP` ... `DIOPS_TRIMESTRAL`).

TOPOLOGIA ATUAL (ANS-CRON-DEAD-CODE / PERSP-B5-ANSCRON-TOPICS, fechados) — cada definition:

    TimerStartEvent -> ST_ResolverCompetencia* -> ST_PublishCronDue* -> End_Cron*

O primeiro task e servido por `ans_cron.trigger_submissions` (`operadora.ans_cron.
trigger_submissions`): le o literal LOCAL `ans_cron_report_type` e devolve `report_type`/
`periodicidade`/`competencia`/`competencia_referencia_iso` como variaveis de PROCESSO. O segundo e
o publicador generico (`operadora.events.publish`), que copia essas variaveis para o fato tipado
`ans.cron_due`.

HISTORICO (FINDING #1, RESOLVIDA por este WP — mantida aqui porque explica o formato dos testes
abaixo): ate ANS-CRON-DEAD-CODE, as 5 definitions ligavam `ST_PublishCronDue*` DIRETO ao topico
generico `operadora.events.publish` com `camunda:inputParameter` literais, e os 2 topicos que
`register_ans_cron_workers` registrava (`operadora.ans_cron.trigger_submissions` e
`.check_calendar`) nao apareciam em lugar nenhum do BPMN — `WorkerHarness` despacha por match
EXATO de string de topico, logo `_compute_competencia` era codigo morto e o fato viajava com o
literal `competencia="COMPETENCIA_PENDENTE"`. Hoje: `trigger_submissions` e alcancavel de verdade
(provado abaixo contra o engine real) e `check_calendar` foi DELETADA (re-implementacao Python da
`ans_calendar.dmn`, hoje avaliada engine-side por `BRT_Calendario` em SP-OP-ANS-SUBMIT-001; ver
`tests/integration/dmn/test_dmn_golden_parity.py`).

FINDING #2 (Step-3 fact #3, confirmada por leitura de `notification_bridge.py`; UPDATED por
T2.6-7, `docs/design/T2.6-ans-submission-rescope.md` §1.5/§5): a `NotificationBridge` TEM a regra
`ans.cron_due` -> SP-OP-ANS-SUBMIT-001 (predicado + business key deterministica + mapeamento de
variaveis), exercitada em `tests/unit/platform/test_notification_bridge.py`. O que continua
AUSENTE (fora do escopo deste WP) e um CONSUMIDOR VIVO que leia
`operadora.notifications.internal` e chame `NotificationBridge.on_event(...)` — por isso
`test_cron_dispara_fato_e_nao_inicia_submit_automaticamente` continua observando, contra o engine
real, ZERO instancias de SP-OP-ANS-SUBMIT-001 nascidas do tick.

Kafka-publish gap (Step-3 fact #1): as unicas assercoes de kafka aqui sao sobre o publicador
GENERICO `operadora.events.publish` (que de fato chama `kafka.publish`, `events.py:247`).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from maezo.platform.notification_bridge import NotificationBridge
from maezo.tools.workers.ans_cron import (
    _REPORT_PERIODICIDADE,
    COMPETENCIA_PENDENTE,
    _compute_competencia,
    register_ans_cron_workers,
)
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN_CRON = _REPO / "spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn"
_BPMN_SUBMIT = _REPO / "spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn"
_DMN_CALENDAR = _REPO / "spec/processes/dmn/ans_calendar.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/ans_sla.dmn"
_DMN_ADMISS = _REPO / "spec/processes/dmn/ans_submission_admissibility.dmn"
_DMN_RETRY = _REPO / "spec/processes/dmn/ans_retry_policy.dmn"

_PUBLISH_TOPIC = "operadora.events.publish"

#: O topico proprio de `ans_cron.py` — declarado por `ST_ResolverCompetencia*` nas 5 definitions.
_ANS_CRON_TRIGGER_TOPIC = "operadora.ans_cron.trigger_submissions"

#: Os DOIS topicos que este BPMN produz tasks para (ambos precisam ser drenados, em ordem).
_CRON_WORKER_TOPICS = [_ANS_CRON_TRIGGER_TOPIC, _PUBLISH_TOPIC]

# Topico interno de notificacoes (onde o fato tipado ans.cron_due e publicado).
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_PROCESS_KEY_ANS_SUBMIT = "SP-OP-ANS-SUBMIT-001"

#: `R/<ISO period>` do BPMN -> periodo ISO da taxonomia do worker. `R/P1Y` e a forma que o CIB
#: Seven aceita para o ciclo anual; a taxonomia Python usa `P12M` para o mesmo periodo.
_TIMECYCLE_PARA_ISO = {"R/P1M": "P1M", "R/P3M": "P3M", "R/P1Y": "P12M"}

#: (definition key, TimerStartEvent id, ST_ResolverCompetencia id, report_type) — os 5 tipos.
#: Cobre 3 periodicidades DISTINTAS (mensal/trimestral/anual), acima do minimo de 2 exigido pelo
#: AC de GAP-ANS-1.
_CRON_DISPATCH_CASES = [
    ("SP-OP-ANS-CRON-001-RN124SIP", "Start_CronRn124Sip", "ST_ResolverCompetenciaRn124Sip", "RN_124_SIP"),
    ("SP-OP-ANS-CRON-001-RN209", "Start_CronRn209", "ST_ResolverCompetenciaRn209", "RN_209_UTILIZACAO"),
    ("SP-OP-ANS-CRON-001-RN388", "Start_CronRn388", "ST_ResolverCompetenciaRn388", "RN_388_QUALIDADE"),
    (
        "SP-OP-ANS-CRON-001-RN424TISS",
        "Start_CronRn424Tiss",
        "ST_ResolverCompetenciaRn424Tiss",
        "RN_424_TISS_MONITORAMENTO",
    ),
    ("SP-OP-ANS-CRON-001-DIOPS", "Start_CronDiops", "ST_ResolverCompetenciaDiops", "DIOPS_TRIMESTRAL"),
]


def _bpmn_xml() -> str:
    return _BPMN_CRON.read_text(encoding="utf-8")


@dataclass
class CronEngineProbe:
    """Driva os workers reais do agendador (ans_cron + events.publish) contra o CIB Seven."""

    engine: EngineRest
    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    kafka: FakeKafkaPublisher
    worker_id: str

    @property
    def _captured(self) -> list[tuple[str, dict[str, Any], str | None]]:
        return self.kafka.published

    async def drain(self, *, rounds: int = 30) -> None:
        await drain_topics(self.transport, self.harness, self.worker_id, _CRON_WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya o agendador CRON (5 process definitions, 1 arquivo BPMN, ZERO DMN — o agendador nao
    tem `camunda:decisionRef` algum) no engine real."""
    return await engine.deploy(_BPMN_CRON, name="SP-OP-ANS-CRON-001-qa")


@pytest_asyncio.fixture
async def deploy_cron_and_submit_artifacts(engine: EngineRest) -> str:
    """Deploya o agendador CRON + o processo de envio (SUBMIT) + suas 4 DMNs num unico deployment.

    Usado APENAS pelos testes de seam abaixo — precisa de SP-OP-ANS-SUBMIT-001 genuinamente
    deployado para que a prova "nenhuma instancia nova" (`instance_ids_of_definition`) seja
    significativa (nao vacua contra uma definition-key inexistente). Mirrors o donor's
    `deploy_cron_artifacts`.
    """
    return await engine.deploy(
        _BPMN_CRON,
        _BPMN_SUBMIT,
        _DMN_CALENDAR,
        _DMN_SLA,
        _DMN_ADMISS,
        _DMN_RETRY,
        name="SP-OP-ANS-CRON-001-seam-qa",
    )


@pytest_asyncio.fixture
async def cron_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[CronEngineProbe]:
    """Probe que serve as external tasks do agendador CRON com os workers reais."""
    worker_id = f"qa-anscron-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # T1.10 wave: emit-before-complete is FAIL-CLOSED (harness.py _emit_audit) — a real
    # PostgresAuditSink (lane PG, migrations 0001->0005) is REQUIRED or the harness refuses
    # to complete. `tenant` scopes the durable audit chain / dedup key to the per-run schema.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    # ST_ResolverCompetencia* — o topico proprio de ans_cron.py, hoje REALMENTE alcancavel.
    # Deliberadamente registrado SEM o seam `tenant_id` (composicao legada): prova que o tenant do
    # fato vem do carimbo do publicador generico, nao deste worker.
    register_ans_cron_workers(harness, kafka)
    # ST_PublishCronDue*. `tenant_id` seam threaded for production fidelity: a composicao viva
    # (`worker_runtime/service.py::register_default_workers`) passa `tenant_id=settings.tenant_id`
    # para todos os bootstraps; o publicador generico carimba (setdefault) o tenant nos payloads
    # que as variaveis de processo deixaram sem tenant — exatamente o caso deste BPMN (o seu
    # `event_payload_vars` nao carrega tenant, e nao pode carregar — ver o fence abaixo).
    register_events_workers(harness, kafka, tenant_id=audit_tenant)
    probe = CronEngineProbe(
        engine=engine,
        harness=harness,
        transport=transport,
        kafka=kafka,
        worker_id=worker_id,
    )
    try:
        yield probe
    finally:
        await transport.close()


# ===========================================================================
# Fences estaticos (sem engine; sempre verdes) — PERSP-B5-ANSCRON-TOPICS
# ===========================================================================


def test_ans_cron_registered_topics_reachable_from_bpmn() -> None:
    """PERSP-B5-ANSCRON-TOPICS (substitui `..._unreachable_from_bpmn`): TODO topico
    `operadora.ans_cron.*` que `register_ans_cron_workers` registra e declarado por algum
    serviceTask do BPMN — e o BPMN nao declara nenhum topico `operadora.ans_cron.*` sem worker.

    Varredura do XML (sem engine) + introspeccao real de `harness.registered_topics` (o MESMO
    harness real usado pelos testes de seam abaixo, nao um mock).
    """
    from maezo.tools.workers.harness import FakeWorkerTransport

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
    import xml.etree.ElementTree as ET

    ns = {
        "b": "http://www.omg.org/spec/BPMN/20100524/MODEL",
        "c": "http://camunda.org/schema/1.0/bpmn",
    }
    root = ET.fromstring(_bpmn_xml())
    processes = root.findall("b:process", ns)
    assert len(processes) == len(_REPORT_PERIODICIDADE) == 5, (
        "uma process definition por report_type (GAP-ANS-1): "
        f"{len(processes)} definitions vs {len(_REPORT_PERIODICIDADE)} tipos"
    )

    vistos: dict[str, str] = {}
    for proc in processes:
        starts = proc.findall("b:startEvent", ns)
        assert len(starts) == 1, f"{proc.get('id')}: exatamente 1 TimerStartEvent por definition"
        cycles = starts[0].findall("b:timerEventDefinition/b:timeCycle", ns)
        assert len(cycles) == 1, f"{proc.get('id')}: TimerStartEvent sem timeCycle"
        time_cycle = (cycles[0].text or "").strip()

        resolvers = [
            st
            for st in proc.findall("b:serviceTask", ns)
            if st.get(f"{{{ns['c']}}}topic") == _ANS_CRON_TRIGGER_TOPIC
        ]
        assert len(resolvers) == 1, f"{proc.get('id')}: exatamente 1 ST_ResolverCompetencia*"
        literais = {
            p.get("name"): (p.text or "").strip()
            for p in resolvers[0].findall("b:extensionElements/c:inputOutput/c:inputParameter", ns)
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


def test_notification_bridge_ans_cron_rule_now_wired() -> None:
    """FINDING #2 UPDATED (T2.6-7, `docs/design/T2.6-ans-submission-rescope.md` §1.5/§5):
    NotificationBridge tem exatamente uma regra para `event_type=ans.cron_due`, alvo
    SP-OP-ANS-SUBMIT-001.

    Este teste intencionalmente NAO prova o seam ao vivo ponta a ponta: nenhum consumidor rodando
    neste codebase le `operadora.notifications.internal` e chama `NotificationBridge.on_event(...)`
    — e por isso que `test_cron_dispara_fato_e_nao_inicia_submit_automaticamente` (contra o engine
    real) continua observando zero instancias de SP-OP-ANS-SUBMIT-001 auto-iniciadas. O
    predicado/business-key/mapeamento da propria regra e unit-testado em
    `tests/unit/platform/test_notification_bridge.py`.
    """
    bridge = NotificationBridge()
    rules = bridge.get_handoff("ans.cron_due")
    assert len(rules) == 1, (
        f"NotificationBridge deveria ter EXATAMENTE 1 regra para ans.cron_due pos-T2.6-7; encontrado: {rules}"
    )
    target_process, predicate = rules[0]
    assert target_process == _PROCESS_KEY_ANS_SUBMIT
    assert callable(predicate)
    # Anchor (t2-notify-integrity item 3): a regra exige tenant_id ALEM de report_type (as 7
    # regras do bridge exigem non_blank(tenant_id) — simetrico com os workers in-flow). Na
    # producao o tenant chega no fato via o stamp de deployment do publicador generico
    # (`register_events_workers` -> `make_publish_event_handler`), entao a regra ARMA ao vivo.
    assert predicate({"tenant_id": "amh", "report_type": "DIOPS_TRIMESTRAL"}) is True
    # Fail-closed: fato sem tenant_id (ou sem report_type) NAO dispara — nenhuma business key sensata.
    assert predicate({"report_type": "DIOPS_TRIMESTRAL"}) is False
    assert predicate({}) is False

    targets = {h["target_process"] for h in bridge.list_handoffs()}
    assert _PROCESS_KEY_ANS_SUBMIT in targets
    # Regressao: as 5 regras pre-T2.6-7 continuam intactas + as 2 novas (NIP + cron) = 7.
    assert bridge.count_handoffs() == 7


# ===========================================================================
# Seam: TimerStartEvent -> ST_ResolverCompetencia -> fato ans.cron_due (engine REAL)
# ===========================================================================


async def _tick_e_drena(
    engine: EngineRest,
    cron_probe: CronEngineProbe,
    cron_key: str,
    activity_id: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Executa o job do TimerStartEvent, drena os 2 tasks e devolve (instance_id, fatos)."""
    # Tick do timer: diff antes/depois isola a instancia criada por ESTA execucao (robusto a
    # leftovers de runs anteriores contra um engine de dev compartilhado).
    before_cron = await engine.instance_ids_of_definition(cron_key)
    job_id = await engine.start_timer_job_id(activity_id)
    await engine.execute_job(job_id)
    after_cron = await engine.instance_ids_of_definition(cron_key)

    novas = after_cron - before_cron
    assert len(novas) == 1, (
        f"executar o timer-start {activity_id} deveria criar EXATAMENTE 1 instancia de {cron_key}; "
        f"criadas={novas}"
    )
    instance_id = novas.pop()

    await cron_probe.drain()

    fatos = [
        payload
        for (topic, payload, _key) in cron_probe._captured
        if topic == _NOTIFICATIONS_TOPIC and payload.get("type") == "ans.cron_due"
    ]
    return instance_id, fatos


@pytest.mark.parametrize(
    ("cron_key", "activity_id", "resolver_id", "report_type"),
    _CRON_DISPATCH_CASES,
    ids=[c[3] for c in _CRON_DISPATCH_CASES],
)
async def test_cron_competencia_computada_por_report_type(
    engine: EngineRest,
    deploy_artifacts: str,
    cron_probe: CronEngineProbe,
    cron_key: str,
    activity_id: str,
    resolver_id: str,
    report_type: str,
) -> None:
    """ANS-CRON-DEAD-CODE (AC principal): o tick de CADA report_type percorre
    `ST_ResolverCompetencia*` no engine REAL, e o fato `ans.cron_due` publicado carrega uma
    `competencia` REALMENTE COMPUTADA a partir da periodicidade daquele tipo — nunca mais o
    literal sentinela `COMPETENCIA_PENDENTE` que o BPMN embutia.

    Cobre os 5 tipos (3 periodicidades distintas: mensal, trimestral, anual), acima do minimo de
    2 exigido pelo AC de GAP-ANS-1. Os valores esperados sao derivados pela MESMA funcao pura
    (`_compute_competencia`) a partir da ancora que o proprio fato carrega
    (`competencia_referencia_iso`) — nao ha data de hoje hardcoded, e a asserção continua valida
    se o tick cruzar a meia-noite UTC.

    DRAFT/verify regulatorio: a periodicidade de cada tipo e o mapeamento periodo->competencia
    (mes/trimestre/ano ANTERIOR ao da ancora) seguem NAO confirmados com o regulatorio.
    """
    periodicidade_ptbr, periodicidade_iso = _REPORT_PERIODICIDADE[report_type]

    instance_id, fatos = await _tick_e_drena(engine, cron_probe, cron_key, activity_id)

    # O resolver REALMENTE executou (topico alcancavel — a prova viva de PERSP-B5-ANSCRON-TOPICS).
    ended = await engine.activity_instances_ended(instance_id)
    assert resolver_id in ended, f"{resolver_id} deveria ter executado antes do publish; ended={ended}"
    assert any(a.startswith("End_Cron") for a in ended), (
        f"o agendador {cron_key} deveria concluir o ciclo apos publicar o fato. ended={ended}"
    )

    meus = [f for f in fatos if f.get("report_type") == report_type]
    assert meus, f"o tick de {cron_key} deveria publicar o fato ans.cron_due de {report_type}"
    fact = meus[0]

    assert fact["periodicidade"] == periodicidade_ptbr
    assert fact["origem_envio"] == "calendario"

    ancora = fact["competencia_referencia_iso"]
    assert date.fromisoformat(ancora)
    esperada = _compute_competencia(ancora, periodicidade_iso)
    assert esperada != COMPETENCIA_PENDENTE, "vetor de teste degenerado"
    assert fact["competencia"] == esperada, (
        f"{report_type}: competencia do fato {fact['competencia']!r} != computada {esperada!r} "
        f"a partir da ancora {ancora!r} (periodicidade {periodicidade_iso})"
    )
    # A sentinela deixou de ser o valor de regime para os tipos conhecidos.
    assert fact["competencia"] != COMPETENCIA_PENDENTE
    # Ancora mecanica do publicador generico (instante da publicacao) segue presente e distinta.
    assert date.fromisoformat(fact["ans_cron_reference_date_iso"])


@pytest.mark.parametrize(
    ("cron_key", "activity_id", "resolver_id", "report_type"),
    [_CRON_DISPATCH_CASES[0], _CRON_DISPATCH_CASES[-1]],
    ids=[_CRON_DISPATCH_CASES[0][3], _CRON_DISPATCH_CASES[-1][3]],
)
async def test_cron_dispara_fato_e_nao_inicia_submit_automaticamente(
    engine: EngineRest,
    deploy_cron_and_submit_artifacts: str,
    cron_probe: CronEngineProbe,
    audit_tenant: str,
    cron_key: str,
    activity_id: str,
    resolver_id: str,
    report_type: str,
) -> None:
    """O tick faz os WORKERS REAIS emitirem o fato tipado `ans.cron_due` — mas NENHUMA instancia
    de SP-OP-ANS-SUBMIT-001 nasce disso automaticamente (FINDING #2): a `NotificationBridge` TEM a
    regra, mas nenhum consumidor rodando le `operadora.notifications.internal` e chama
    `bridge.on_event(...)` — este `cron_probe` drena apenas os topicos de external task, nunca
    invoca a ponte.

    Tecnica de job-execution do test-spec (NUNCA sleep): o timeCycle agenda o primeiro tick
    adiante; localizamos o job-start pendente e o executamos na marra
    (`engine.start_timer_job_id`/`execute_job`, construidos especificamente para esta familia —
    ver `engine_rest.py`).
    """
    del resolver_id
    before_submit = await engine.instance_ids_of_definition(_PROCESS_KEY_ANS_SUBMIT)
    _instance_id, fatos = await _tick_e_drena(engine, cron_probe, cron_key, activity_id)
    after_submit = await engine.instance_ids_of_definition(_PROCESS_KEY_ANS_SUBMIT)

    assert after_submit == before_submit, (
        f"FINDING #2 violada: uma instancia de {_PROCESS_KEY_ANS_SUBMIT} apareceu sem nenhum "
        f"consumidor vivo da ponte. novas={after_submit - before_submit}"
    )

    meus = [f for f in fatos if f.get("report_type") == report_type]
    assert meus, f"o tick de {cron_key} deveria publicar o fato ans.cron_due de {report_type}"
    fact = meus[0]
    # O publicador generico CARIMBA o tenant do DEPLOYMENT (seam `tenant_id` de
    # `register_events_workers`) num payload que as variaveis de processo deixaram sem tenant —
    # verdade do deployment, nunca fabricacao (setdefault: um tenant vindo de variavel de processo
    # jamais e sobrescrito). O fato viaja COM tenant, entao a regra cron->ANSSUB da ponte arma com
    # chave `ANSSUB-{tenant}-{report_type}-{competencia}` — nunca a degenerada `ANSSUB--...`.
    assert fact["tenant_id"] == audit_tenant, (
        "o fato deveria carregar o tenant do deployment (seam tenant_id do register_events_workers)"
    )
