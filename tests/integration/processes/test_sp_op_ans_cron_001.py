"""SP-OP-ANS-CRON-001 — suite de integracao executavel (engine CIB Seven REAL).

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

Synthetic data mirrors the donor's obviously-fake identifiers (competencia sentinel
`COMPETENCIA_PENDENTE`, report_type literals `RN_124_SIP`/`DIOPS_TRIMESTRAL`).

PORT NOTES (fixture adaptation only — port rule 1):
  - artifact path -> `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn`. NO DMN:
    `grep -o 'camunda:decisionRef="[^"]*"' spec/processes/bpmn/SP-OP-ANS-CRON-001_*.bpmn` returns
    ZERO matches — every one of the 5 process definitions has exactly ONE service task
    (`ST_PublishCronDue*`), and its `report_type`/`periodicidade`/`origem_envio`/`competencia`
    values are BAKED IN as literal `camunda:inputParameter`s directly on that task (verified by
    reading the BPMN in full) — no business-rule task, no DMN evaluation of any kind for this
    family. `deploy_artifacts` therefore deploys the BPMN alone.
  - `deploy_cron_and_submit_artifacts` (used ONLY by the seam test below) additionally deploys
    SP-OP-ANS-SUBMIT-001's BPMN + its 4 DMNs, mirroring the donor's own `deploy_cron_artifacts`
    fixture — needed so `engine.instance_ids_of_definition("SP-OP-ANS-SUBMIT-001")` queries a
    genuinely-deployed definition (a meaningful "zero new instances" proof, not a vacuous one
    against an undeployed key).
  - `plan_start`/`PROCESS_KEY_ANS_SUBMIT`/`_competencia_from_anchor`/`NOTIFICATIONS_TOPIC`
    (imported by the donor from `maezo.platform.integrations.notifications_bridge.consumer`) do
    NOT exist anywhere on v2 (`find`/`grep` confirm no such module path, no such symbols anywhere
    under `src/`). v2's equivalent is `maezo.platform.notification_bridge.NotificationBridge` — a
    much simpler pure rule-registry with NO competencia-computation helper and (see FINDING #2
    below) no rule for this fact at all. The donor's seam test (which calls `plan_start` itself,
    acting as the bridge invoker, then starts SP-OP-ANS-SUBMIT-001 and asserts it reaches
    `UT_CorrigirPendenciaEnvio` fail-closed) is NOT portable as-is: there is neither a real
    competencia-computation function nor a live auto-start path to invoke or observe. Rather than
    fabricate either (which would test behavior no production code implements), the ported seam
    test below asserts what v2 ACTUALLY does: the fact publishes correctly (verbatim assertions
    on `report_type`/`periodicidade`/`origem_envio`/`competencia`/`ans_cron_reference_date_iso`),
    and — replacing the donor's `plan_start`+start-by-key steps — statically proves via
    `engine.instance_ids_of_definition("SP-OP-ANS-SUBMIT-001")` diffed before/after that NOTHING
    auto-starts a SUBMIT instance from this fact today (FINDING #2). This is an honest adaptation
    of a cross-process CHOREOGRAPHY/seam test to the real v2 architecture (constraint 1's
    "never weaken" applies to L0/L1 HITL GUARD tests specifically; this is not one — no guard
    exists to weaken, since the mechanism it would guard is simply absent).

FINDINGS (grep-confirmed on this v2 main). #1 is the NEW, prominent finding for this family —
see the PR body / evidence-ledger for the full write-up.

  FINDING #1 (NEW — topic-registration/dispatch-binding gap, DISTINCT from any governance or
  DMN-taxonomy gap): `spec/processes/bpmn/SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn` declares
  `camunda:topic="operadora.events.publish"` for ALL FIVE of its service tasks (one per process
  definition) — verified:
      grep -o 'camunda:topic="[^"]*"' spec/processes/bpmn/SP-OP-ANS-CRON-001_*.bpmn | sort -u
      -> operadora.events.publish   (ONLY this one topic, 5 occurrences collapsed by sort -u)
  BUT `ans_cron.py`'s `register_ans_cron_workers` (near the bottom of that module) registers its
  two `FunctionWorker`s under topic keys `"operadora.ans_cron.trigger_submissions"` and
  `"operadora.ans_cron.check_calendar"` — topic names that appear NOWHERE in the BPMN (confirmed
  by the same grep). `WorkerHarness` dispatches by exact topic-string match (`harness.py`'s
  `_handlers` dict is topic-keyed, one handler per topic). CONSEQUENCE: `ans_cron.py`'s OWN
  business logic — `trigger_submissions`'s `_compute_competencia`/periodicidade computation,
  `check_calendar`'s `deve_enviar`/`motivo` computation — is a registration that can NEVER be
  reached by the live engine's actual external tasks for this BPMN; ONLY the generic
  `operadora.events.publish` handler (`register_events_workers`, wired into `cron_probe` below)
  ever drains SP-OP-ANS-CRON-001's tasks. Reading `events.py` (the generic handler) shows it has a
  SPECIFIC carve-out for this family: when `event_type == "ans.cron_due"` it stamps
  `payload["ans_cron_reference_date_iso"]` (the tick-instant date) but does NOT compute/set
  `periodicidade`/`competencia`/`deve_enviar`/`motivo` — those come only from `ans_cron.py`'s own
  unreachable functions. Net effect, verified by reading the BPMN's literal `inputParameter`s
  (`ST_PublishCronDueRn124Sip` etc.): the external task itself completes NORMALLY via the generic
  handler (no incident — a handler for `operadora.events.publish` genuinely exists); the fact it
  publishes carries `report_type`/`periodicidade`/`origem_envio`/`competencia` as LITERAL BPMN
  values (e.g. `competencia="COMPETENCIA_PENDENTE"`, a fixed sentinel, NEVER computed — see
  `test_cron_dispara_fato_e_nao_inicia_submit_automaticamente` below, which asserts exactly this)
  plus the worker-stamped `ans_cron_reference_date_iso`; any test that instead asserted
  `deve_enviar`/`motivo` (check_calendar's outputs) would fail, since nothing reachable ever sets
  them — the donor's own test in this section never asserts those fields either (it only checks
  `report_type`/`periodicidade`/`origem_envio`/`competencia`/`ans_cron_reference_date_iso`), so no
  xfail is needed for that specific gap in THIS file; `test_ans_cron_registered_topics_
  unreachable_from_bpmn` below proves the registration/topic mismatch directly and precisely
  (concrete, always-green evidence, not just prose).

  FINDING #2 (Step-3 fact #3, confirmed by reading `notification_bridge.py` directly): v2's
  `NotificationBridge._register_default_handoffs` registers EXACTLY 5 rules — CONTAS->RECURSO,
  CONTAS->FRAUDE, FRAUDE->CRED, FRAUDE->CANCEL, FRAUDE->INADIMPLENCIA — NONE keyed on
  `event_type="ans.cron_due"` and none targeting `SP-OP-ANS-SUBMIT-001`
  (`NotificationBridge().get_handoff("ans.cron_due") == []`, asserted statically below). Combined
  with FINDING #1 (the fact's `ans.cron_due` payload never carries a computed competencia either),
  this means the `ans.cron_due` fact -> SP-OP-ANS-SUBMIT-001 auto-start choreography the donor's
  `plan_start` implemented does NOT exist live in v2 today, from EITHER direction (no bridge rule,
  no competencia to key a business key on even if there were one).

  ans_calendar DMN taxonomy mismatch (Step-3 fact #4, PRIOR ART — NOT re-ported here): already
  guarded by `tests/integration/dmn/test_dmn_golden_parity.py::test_ans_calendar_domain_mismatch_
  evidence`. Doubly moot for this file: `ans_cron.py`'s `check_calendar` (the function whose
  hand-rolled `_REPORT_PERIODICIDADE` literals collide with the DMN's RN-citation literals) is
  ALSO unreachable per FINDING #1 — this BPMN never evaluates any DMN at all (zero decisionRefs),
  so the mismatch cannot even be exercised from this file's tests.

  Kafka-publish gap (Step-3 fact #1): not cited per-test here — this file's only kafka-publish
  assertions are on the GENERIC `operadora.events.publish` handler's output (which DOES call
  `kafka.publish`, `events.py:247`), not on any per-worker notification pattern.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from maezo.platform.notification_bridge import NotificationBridge
from maezo.tools.workers.ans_cron import register_ans_cron_workers
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

# The two topics ans_cron.py's OWN register_ans_cron_workers registers — UNREACHABLE from this
# BPMN (FINDING #1). Registered on the harness below for fidelity (mirrors what a production
# register_all_workers bootstrap composition would wire), but deliberately EXCLUDED from
# `_CRON_WORKER_TOPICS` (the drain list): unlike auth's `_ANALYZE_TOPIC` exclusion (a donor FIXTURE
# choice for a topic a REAL v2 worker also serves elsewhere), these two are excluded because the
# BPMN genuinely never produces a task on either — there is nothing to drain.
_ANS_CRON_TRIGGER_TOPIC = "operadora.ans_cron.trigger_submissions"
_ANS_CRON_CHECK_CALENDAR_TOPIC = "operadora.ans_cron.check_calendar"

# Topico servido pelo drain generico — o UNICO topico que este BPMN de fato produz tasks para.
_CRON_WORKER_TOPICS = [_PUBLISH_TOPIC]

# Topico interno de notificacoes (onde o fato tipado ans.cron_due e publicado).
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_PROCESS_KEY_ANS_SUBMIT = "SP-OP-ANS-SUBMIT-001"

# (cron_definition_key, activity_id do TimerStartEvent, report_type, periodicidade) esperados por
# tipo — mirrors o donor's `_CRON_DISPATCH_CASES` exatamente (2 dos 5 process ids, provando
# periodicidades distintas: mensal R/P1M e trimestral R/P3M).
_CRON_DISPATCH_CASES = [
    ("SP-OP-ANS-CRON-001-RN124SIP", "Start_CronRn124Sip", "RN_124_SIP", "mensal"),
    ("SP-OP-ANS-CRON-001-DIOPS", "Start_CronDiops", "DIOPS_TRIMESTRAL", "trimestral"),
]

# FINDING #1 (see module docstring for full grep evidence) — used as the assertion message in
# test_ans_cron_registered_topics_unreachable_from_bpmn so the constant has a real code usage
# (not just documentation) and any assertion failure surfaces the full citation inline.
_ANS_CRON_TOPIC_BINDING_GAP_REASON = (
    "T3.1 phase-2 FINDING #1 (new): SP-OP-ANS-CRON-001_Agendador_Envios_ANS.bpmn declares "
    "camunda:topic='operadora.events.publish' for ALL 5 of its service tasks (grep -o "
    "'camunda:topic=\"[^\"]*\"' ... | sort -u returns exactly that one topic) — but ans_cron.py's "
    "register_ans_cron_workers registers its two FunctionWorkers under "
    "'operadora.ans_cron.trigger_submissions'/'operadora.ans_cron.check_calendar', topic names "
    "that appear NOWHERE in the BPMN. WorkerHarness dispatches by exact topic-string match, so "
    "trigger_submissions'/check_calendar's own periodicidade/competencia/deve_enviar/motivo "
    "computation can never be reached by the live engine — only the generic operadora.events."
    "publish handler ever drains this BPMN's tasks."
)


@dataclass
class CronEngineProbe:
    """Driva o worker generico (events.publish) + ans_cron.py contra o engine CIB Seven."""

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
    """Deploya o agendador CRON (5 process definitions, 1 arquivo BPMN, ZERO DMN — verificado por
    grep no docstring do modulo) no engine real."""
    return await engine.deploy(_BPMN_CRON, name="SP-OP-ANS-CRON-001-qa")


@pytest_asyncio.fixture
async def deploy_cron_and_submit_artifacts(engine: EngineRest) -> str:
    """Deploya o agendador CRON + o processo de envio (SUBMIT) + suas 4 DMNs num unico deployment.

    Usado APENAS pelo teste de seam abaixo — precisa de SP-OP-ANS-SUBMIT-001 genuinamente
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
    # Registered for fidelity (mirrors a production register_all_workers composition) — genuinely
    # UNREACHABLE from this BPMN (FINDING #1); NOT in _CRON_WORKER_TOPICS, so cron_probe.drain()
    # never subscribes to either topic key.
    register_ans_cron_workers(harness, kafka)
    # The ONLY worker this BPMN's tasks actually reach.
    register_events_workers(harness, kafka)
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
# FINDING #1 — proof by static introspection (no engine; always green)
# ===========================================================================


def test_ans_cron_registered_topics_unreachable_from_bpmn() -> None:
    """Prova estatica e concreta da FINDING #1: os topicos que `register_ans_cron_workers`
    registra NAO tem overlap algum com os topicos que o BPMN de fato declara.

    Varredura do XML (sem engine) + introspeccao real de `harness.registered_topics` (o MESMO
    harness real usado pelos testes de seam abaixo, nao um mock).
    """
    import re

    from maezo.tools.workers.harness import FakeWorkerTransport

    bpmn_xml = _BPMN_CRON.read_text(encoding="utf-8")
    bpmn_topics = set(re.findall(r'camunda:topic="([^"]*)"', bpmn_xml))
    assert bpmn_topics == {_PUBLISH_TOPIC}, (
        f"BPMN deveria declarar EXATAMENTE {{{_PUBLISH_TOPIC!r}}}; encontrado: {bpmn_topics}"
    )

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="qa-static-probe")
    register_ans_cron_workers(harness, FakeKafkaPublisher())
    ans_cron_registered = {t for t in harness.registered_topics if t.startswith("operadora.ans_cron.")}
    assert ans_cron_registered == {_ANS_CRON_TRIGGER_TOPIC, _ANS_CRON_CHECK_CALENDAR_TOPIC}, (
        f"register_ans_cron_workers deveria registrar exatamente estes 2 topicos; "
        f"encontrado: {ans_cron_registered}"
    )

    overlap = ans_cron_registered & bpmn_topics
    assert not overlap, _ANS_CRON_TOPIC_BINDING_GAP_REASON


def test_notification_bridge_has_no_ans_cron_due_rule() -> None:
    """FINDING #2 (Step-3 fact #3): NotificationBridge nao tem NENHUMA regra para
    event_type=ans.cron_due (nem para SP-OP-ANS-SUBMIT-001 como target_process)."""
    bridge = NotificationBridge()
    assert bridge.get_handoff("ans.cron_due") == [], (
        "NotificationBridge NAO deve ter regra para ans.cron_due hoje (FINDING #2) — se este "
        "assert falhar, a ponte ans.cron_due -> SP-OP-ANS-SUBMIT-001 foi implementada e os testes "
        "de seam acima devem ser revisados (seriam candidatos a testar o auto-start de verdade)."
    )
    targets = {h["target_process"] for h in bridge.list_handoffs()}
    assert _PROCESS_KEY_ANS_SUBMIT not in targets, (
        f"NotificationBridge NAO deve ter nenhuma regra visando {_PROCESS_KEY_ANS_SUBMIT} hoje"
    )


# ===========================================================================
# Seam: TimerStartEvent -> fato ans.cron_due (engine REAL)
# ===========================================================================


@pytest.mark.parametrize(
    ("cron_key", "activity_id", "report_type", "periodicidade"),
    _CRON_DISPATCH_CASES,
    ids=[c[2] for c in _CRON_DISPATCH_CASES],
)
async def test_cron_dispara_fato_e_nao_inicia_submit_automaticamente(
    engine: EngineRest,
    deploy_cron_and_submit_artifacts: str,
    cron_probe: CronEngineProbe,
    cron_key: str,
    activity_id: str,
    report_type: str,
    periodicidade: str,
) -> None:
    """Executar o TimerStartEvent de um cron per-tipo faz o WORKER REAL (generic events.publish)
    emitir o fato tipado `ans.cron_due` com os valores LITERAIS que o BPMN embute como
    `inputParameter` (FINDING #1c) — mas NENHUMA instancia de SP-OP-ANS-SUBMIT-001 nasce disso
    automaticamente (FINDING #2): nao existe hoje nem uma regra de bridge para este fato, nem um
    `plan_start`-equivalente que computaria a business key/competencia real (ver PORT NOTES no
    docstring do modulo — a asserction de "instancia efetivamente iniciada" do donor foi
    substituida por esta prova estatica de ausencia, honestamente refletindo v2).

    Tecnica de job-execution do test-spec (NUNCA sleep): o timeCycle (R/P1M / R/P3M) agenda o
    primeiro tick adiante; localizamos o job-start pendente e o executamos na marra
    (`engine.start_timer_job_id`/`execute_job`, construidos especificamente para esta familia —
    ver `engine_rest.py`).
    """
    # 1) Tick do timer: diff antes/depois isola a instancia de cron criada por ESTA execucao
    #    (robusto a leftovers de runs anteriores contra um engine de dev compartilhado).
    before_cron = await engine.instance_ids_of_definition(cron_key)
    job_id = await engine.start_timer_job_id(activity_id)
    await engine.execute_job(job_id)
    after_cron = await engine.instance_ids_of_definition(cron_key)

    novas = after_cron - before_cron
    assert len(novas) == 1, (
        f"executar o timer-start {activity_id} deveria criar EXATAMENTE 1 instancia de {cron_key}; "
        f"criadas={novas}"
    )
    cron_instance_id = novas.pop()

    # 2) O worker REAL (operadora.events.publish) serve o ST_PublishCronDue* e publica o fato
    #    tipado no topico interno de notificacoes; o agendador entao TERMINA (ciclo concluido).
    #    Diff de instancias de SP-OP-ANS-SUBMIT-001 ao redor do drain: nada deve nascer (FINDING #2).
    before_submit = await engine.instance_ids_of_definition(_PROCESS_KEY_ANS_SUBMIT)
    await cron_probe.drain()
    after_submit = await engine.instance_ids_of_definition(_PROCESS_KEY_ANS_SUBMIT)
    assert after_submit == before_submit, (
        f"FINDING #2 violada: uma instancia de {_PROCESS_KEY_ANS_SUBMIT} apareceu sem nenhuma "
        f"ponte/regra viva para isso. novas={after_submit - before_submit}"
    )

    cron_ended = await engine.activity_instances_ended(cron_instance_id)
    assert any(a.startswith("End_Cron") for a in cron_ended), (
        f"o agendador {cron_key} deveria concluir o ciclo apos publicar o fato. ended={cron_ended}"
    )

    facts = [
        payload
        for (topic, payload, _key) in cron_probe._captured
        if topic == _NOTIFICATIONS_TOPIC
        and payload.get("type") == "ans.cron_due"
        and payload.get("report_type") == report_type
    ]
    assert facts, (
        f"o tick de {cron_key} deveria publicar o fato ans.cron_due de {report_type} "
        f"em {_NOTIFICATIONS_TOPIC}"
    )
    fact = facts[0]
    assert fact["periodicidade"] == periodicidade
    assert fact["origem_envio"] == "calendario"
    # FINDING #1c: o fato carrega o literal BPMN "COMPETENCIA_PENDENTE" — NAO computado por
    # ans_cron.py's trigger_submissions (inalcancavel); o worker generico so grava a ancora
    # mecanica (ans_cron_reference_date_iso).
    assert fact["competencia"] == "COMPETENCIA_PENDENTE"
    assert "tenant_id" not in fact, "o tenant NAO viaja no fato (evento nao inclui tenant_id)"
    assert date.fromisoformat(fact["ans_cron_reference_date_iso"])
