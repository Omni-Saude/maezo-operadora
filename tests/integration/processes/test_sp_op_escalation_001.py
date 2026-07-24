"""SP-OP-ESCALATION-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 1
(V2-COMPLETION-PLAN §3). SP-OP-ESCALATION-001 is the phase-1 ANCHOR family (escalation.py is
NOT one of ADR-0028's 9 migrated modules — no DMN-cutover collision).

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-ESCALATION-001.md) contra o
engine real (ADR-0011: sem mock de engine). Cada teste:

1. inicia a instancia via REST (`start_escalation`, payload canonico do spec);
2. drena as external tasks com o `probe` (publica eventos de dominio + serve notify_*);
3. avanca o HITL completando a User Task como humano sintetico (caminho permitido);
4. dispara timers via job execution (NUNCA sleep) — o spec exige job execution para timers.

Roteamento (P1/grupo/SLA) vem da DMN escalation_routing — regra deterministica, nunca do teste.
Dados sempre sinteticos (`Paciente Teste 001`, tenant `amh`, `PSEUDO-TESTE-001`).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions below are verbatim):
  - import paths: `maezo.tools.workers.escalation.register_escalation_workers` (v2's escalation
    family folds the donor's `phase0.register_phase0_workers` role — the generic WorkerBase
    classes NotifyTeamWorker/NotifySupervisorWorker — under `register_escalation_workers`;
    `FakeKafkaPublisher` moved to `maezo.tools.workers.harness` per T1.1, both preserved fixture
    surfaces, design §16.1). t2.5-p2b-round2 removed the dead `NotifyFallbackWorker` (topic
    `operadora.escalation.notify_fallback` had no matching `camunda:topic` anywhere in
    spec/ — `ST_NotificarFallback` actually declares `notify_supervisor`, now served by
    `NotifySupervisorWorker`); `_WORKER_TOPICS` below already excluded `notify_fallback`.
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (deploy via `engine.deploy`, constraint 5).
  - the donor kept this family's fixture quartet in a dedicated `conftest.py` (this file imported
    `from .conftest import EngineProbe`); ported here as a SELF-CONTAINED module (matching the
    donor's OWN convention for every other family, e.g. auth/cancel) because the probe
    construction contract (register_escalation_workers + the fault-injection wrapper) is
    escalation-specific, not identical across families (port rule 3) — only the truly-identical
    `engine` fixture and the `drain_topics()` transport-adaptation helper are hoisted into this
    package's shared `conftest.py`.
  - `drain()` uses the shared `drain_topics()` helper (v2's `WorkerTransport.fetch_and_lock` takes
    `list[TopicSubscription]` + `async_response_timeout_ms`, NOT v1's `list[str]` +
    `lock_duration_ms` — `harness.py` module docstring: "v2-specific, NOT preserved from v1").

FINDING, FIXED (T3.1 R2 — see PR body / evidence-ledger for detail): this suite originally
documented that every test calling `probe.drain()` and expecting progression past the FIRST
activity failed against the live v2 engine — confirmed live (docker compose core, CIB Seven
2.1.0), starting SP-OP-ESCALATION-001 and draining with the real `register_escalation_workers`
handlers immediately opened an engine incident at `ST_PublishRequested` — `"no handler registered
for topic 'operadora.events.publish'"` — because NO module in v2's 16-bootstrap
`register_all_workers` (`src/maezo/tools/workers/bootstrap.py`) registered a handler for the
generic `operadora.events.publish` topic every family's BPMN uses to emit domain events (v1's
donor served it via `register_phase0_workers`; v2 had no equivalent). T3.1 R2 ports the donor's
`make_publish_event_handler` into `maezo.tools.workers.events.register_events_workers` (a 17th
bootstrap, now in `ALL_WORKER_BOOTSTRAPS`) — this fixture's `probe` now ALSO registers it
(mirroring the donor's own `register_phase0_workers` composition), and `strict-xfail` markers
whose documented reason was exactly this gap are REMOVED below (per-test citation kept where a
DIFFERENT, still-open gap blocks a specific test).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from maezo.tools.workers.escalation import register_escalation_workers
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness

from .conftest import drain_topics
from .engine_rest import EngineRest

StartEscalation = Callable[..., Awaitable[dict[str, Any]]]

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn"
_DMN = _REPO / "spec/processes/dmn/escalation_routing.dmn"

_PUBLISH_TOPIC = "operadora.events.publish"
_NOTIFY_TEAM_TOPIC = "operadora.escalation.notify_team"
_NOTIFY_SUPERVISOR_TOPIC = "operadora.escalation.notify_supervisor"
_WORKER_TOPICS = [_PUBLISH_TOPIC, _NOTIFY_TEAM_TOPIC, _NOTIFY_SUPERVISOR_TOPIC]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_REQUESTED = "agents.events.escalation.requested"
_BREACHED = "agents.events.escalation.sla_breached"
_RESOLVED = "agents.events.escalation.resolved"
_PROCESS_COMPLETED = "agents.events.process_completed"

# NEW finding, live-confirmed AFTER T3.1 R2's events.publish fix (drift, NOT the publish gap —
# that reason string is retired, this one replaces it on the 3 tests it actually blocks):
# `NotifyTeamWorker`/`NotifySupervisorWorker` (`src/maezo/tools/workers/escalation.py`) never
# call `kafka.publish` — unlike the donor's `phase0.py` notify handlers, these `WorkerBase.execute
# ()` methods return a status dict WITHOUT touching the injected `kafka` seam
# (`register_escalation_workers` explicitly does `del kafka, seams  # unused`). Consequence:
# `probe.notified_teams`/`probe.notified_supervisors` (backed by `FakeKafkaPublisher.published`)
# can NEVER observe a notify_team/notify_supervisor execution, and `_FaultInjectingPublisher
# .fail_notification_types` can never trigger (nothing ever calls `kafka.publish` for these
# types) — so `ERR_ESC_NOTIFY_FAILED`/the fallback boundary path is never exercised either.
# Independent of the `operadora.events.publish` gap T3.1 R2 fixes; wiring escalation.py's notify
# workers to actually publish is `src/**` scope out of bounds for this PR (charter: "port the
# missing operadora.events.publish worker", not escalation.py's notify handlers).
_NOTIFY_KAFKA_GAP_REASON = (
    "v2 drift (finding, T3.1 R2 — NOT the events.publish gap, which this PR fixes): "
    "NotifyTeamWorker/NotifySupervisorWorker (src/maezo/tools/workers/escalation.py) never call "
    "kafka.publish (`register_escalation_workers` does `del kafka, seams  # unused`), unlike the "
    "donor's phase0.py notify handlers. probe.notified_teams/notified_supervisors (backed by "
    "FakeKafkaPublisher.published) can therefore never observe a notify_team/notify_supervisor "
    "execution, and fault-injection via _FaultInjectingPublisher.fail_notification_types can "
    "never trigger (nothing calls kafka.publish for these types) — live-confirmed (docker "
    "compose core, CIB Seven 2.1.0) after the events.publish fix landed. Not a fixture bug; "
    "src/** fix (wiring escalation.py's notify workers to actually publish) is out of scope for "
    "this PR."
)


@dataclass
class DomainEvent:
    """Evento de dominio publicado pelo worker real `operadora.events.publish`."""

    topic: str
    payload: dict[str, Any]
    fase: str | None = None


class _FaultInjectingPublisher:
    """Publisher que delega ao FakeKafkaPublisher mas pode falhar um TIPO de notificacao OU um
    TOPICO de evento de dominio.

    Usado por `test_falha_notificacao_usa_fallback`: faz o handler real `notify_team` levantar
    `ERR_ESC_NOTIFY_FAILED` (o handler real lanca o BPMN error quando `kafka.publish` falha).
    A falha e armada por TIPO de notificacao (lida do payload `type`), nao pelo topico Kafka,
    porque team e supervisor publicam no mesmo topico interno.

    `fail_topics` faz o MESMO por TOPICO de saida (o `event_topic` real que o serviceTask
    ST_Publish* passa a `make_publish_event_handler`).

    A falha e STICKY (vale enquanto o tipo/topico estiver armado), nao "1x": o handler real roda
    sob retry com backoff. Cada teste recebe uma `probe` (e portanto um
    `_FaultInjectingPublisher`) nova, entao nao ha vazamento entre testes.
    """

    def __init__(self, inner: FakeKafkaPublisher) -> None:
        self._inner = inner
        self.fail_notification_types: set[str] = set()
        self.fail_topics: set[str] = set()

    async def publish(self, topic: str, value: dict[str, Any], *, key: str | None = None) -> None:
        if topic in self.fail_topics:
            raise RuntimeError(f"topico de dominio indisponivel (fault injetada): {topic}")
        ntype = value.get("type") if isinstance(value, dict) else None
        if ntype in self.fail_notification_types:
            raise RuntimeError("canal de notificacao indisponivel (fault injetada)")
        await self._inner.publish(topic, value, key=key)


@dataclass
class EngineProbe:
    """Driva os workers REAIS de escalation contra o engine e expoe os eventos capturados.

    Nao reimplementa worker: registra `register_escalation_workers` num `WorkerHarness` real com
    um `FakeKafkaPublisher` (via `_FaultInjectingPublisher` para o teste de fallback). `drain()`
    faz ciclos bounded de fetch-and-lock+handle pelo transporte/handlers reais (shared
    `drain_topics()` helper — port rule 3).
    """

    engine: EngineRest
    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    kafka: FakeKafkaPublisher
    fault: _FaultInjectingPublisher
    worker_id: str

    @property
    def _captured(self) -> list[tuple[str, dict[str, Any], str | None]]:
        return self.kafka.published

    def _domain_events(self) -> list[DomainEvent]:
        out: list[DomainEvent] = []
        for topic, payload, _key in self._captured:
            if topic == _NOTIFICATIONS_TOPIC:
                continue  # notificacoes nao sao eventos de dominio
            out.append(DomainEvent(topic=topic, payload=payload, fase=payload.get("fase")))
        return out

    def events_on(self, topic: str) -> list[DomainEvent]:
        return [e for e in self._domain_events() if e.topic == topic]

    def has_event(self, topic: str, **match: Any) -> bool:
        return any(all(e.payload.get(k) == v for k, v in match.items()) for e in self.events_on(topic))

    @property
    def notified_teams(self) -> list[dict[str, Any]]:
        return [v for (_t, v, _k) in self._captured if v.get("type") == "escalation.notify_team"]

    @property
    def notified_supervisors(self) -> list[dict[str, Any]]:
        return [v for (_t, v, _k) in self._captured if v.get("type") == "escalation.notify_supervisor"]

    async def drain(self, *, rounds: int = 25) -> None:
        await drain_topics(self.transport, self.harness, self.worker_id, _WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + escalation_routing.dmn da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN, name="SP-OP-ESCALATION-001-qa")


@pytest_asyncio.fixture
async def probe(engine: EngineRest, audit_sink: Any, audit_tenant: str) -> AsyncIterator[EngineProbe]:
    """Probe que serve as external tasks com os WORKERS REAIS de escalation.

    T1.10 wave: completions são emit-before-complete contra o sink durável REAL da lane
    (PostgresAuditSink, migrações aplicadas) — um harness sem sink agora recusa completar.
    """
    worker_id = f"qa-escalation-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(
        os.environ.get("CIBSEVEN_BASE_URL", "http://localhost:8080/engine-rest")
    )
    # T3.1 R2: ERR_EVENT_PUBLISH_FAILED (GAP-ESC-5) is catchable by SP-OP-ESCALATION-001's own
    # boundaryEvents (Error_EscPublishFailed) ONLY when it's in the harness's allowlist (harness.py
    # `_bpmn_error_allowlist` — no code is gate-proven at the PRODUCTION default, empty). This
    # probe wires the ONE code this family's BPMN declares a matching boundary for, mirroring what
    # a gate-proven production allowlist for this family would contain.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        bpmn_error_allowlist=frozenset({"ERR_EVENT_PUBLISH_FAILED"}),
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    fault = _FaultInjectingPublisher(kafka)
    register_escalation_workers(harness, fault)
    # T3.1 R2: the generic operadora.events.publish worker — every ST_Publish* service task in
    # this BPMN routes through it. Internally opts the 4 escalation-only domain-event topics into
    # the boundary-catchable ERR_EVENT_PUBLISH_FAILED path (GAP-ESC-5, `events.py`'s own
    # `_ESCALATION_PUBLISH_BPMN_ERROR_TOPICS`); the allowlist above is what makes the harness
    # actually honor it (dispatch to `handle_bpmn_error` instead of demoting to a fail-closed
    # incident).
    register_events_workers(harness, fault)
    p = EngineProbe(
        engine=engine,
        harness=harness,
        transport=transport,
        kafka=kafka,
        fault=fault,
        worker_id=worker_id,
    )
    try:
        yield p
    finally:
        await transport.close()


@pytest_asyncio.fixture
async def start_escalation(engine: EngineRest, deploy_artifacts: str) -> StartEscalation:
    """Inicia uma instancia com business key `ESC-amh-{conversation_id}` e payload canonico."""

    async def _start(**overrides: Any) -> dict[str, Any]:
        conv = overrides.pop("conversation_id", f"conv-teste-{uuid.uuid4().hex[:8]}")
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "source_agent_id": "helena",
            "source_agent_version": "qa-1.0",
            "conversation_id": conv,
            "beneficiario_pseudo_id": "PSEUDO-TESTE-001",
            "canal": "whatsapp",
            "motivo_categoria": "red_flag_clinico",
            "severidade": "grave",
            "resumo_contexto": "Paciente Teste 001 — sintese sintetica para humano",
        }
        variables.update(overrides)
        business_key = f"ESC-amh-{conv}"
        return await engine.start_by_key("SP-OP-ESCALATION-001", business_key, variables)

    return _start


# --- Happy paths ---------------------------------------------------------------------


async def test_happy_path_resolvido_por_humano(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """Start -> requested; humano de plantao-clinico resolve; resolved; sem process_completed."""
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    iid = inst["id"]

    await probe.drain()  # publica requested + serve notify_team
    assert probe.has_event(_REQUESTED, motivo_categoria="red_flag_clinico")

    task = await engine.await_user_task(iid, "UT_TratarEscalonamento")
    assert task.candidate_groups == frozenset({"plantao-clinico"})  # DMN: P1

    await engine.complete_task_as_human(task.id, {"resultado": "resolvido_humano", "notas_resolucao": "ok"})
    await probe.drain()  # publica resolved

    assert probe.has_event(_RESOLVED, resultado="resolvido_humano")
    # GAP-ESC-3 (via GAP-XPHI-1): o fato `escalation.resolved` (Zona Geral) NUNCA carrega
    # `notas_resolucao` (texto livre humano, Zona PHI) — removida de event_payload_vars de
    # ST_PublishResolved; a correlacao viaja por `_business_key` (payload_ref, resolvido na
    # Zona PHI — espelha a2a/facts.py). O lint de validate-artifacts guarda a regressao.
    assert all("notas_resolucao" not in e.payload for e in probe.events_on(_RESOLVED))
    assert not probe.events_on(_PROCESS_COMPLETED)  # resolvido_humano NAO retoma o agente
    ended = await engine.activity_instances_ended(iid)
    assert "End_ResolvidoPorHumano" in ended
    assert not await engine.instance_is_active(iid)


async def test_happy_path_devolvido_ao_agente(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """devolvido_agente => resolved E process_completed, fim End_DevolvidoAoAgente.

    GAP-ESC-1: o fato `process_completed` (Zona Geral) carrega SOMENTE as chaves de
    correlacao que o consumidor universal de retomada (GAP-XHITL-4) precisa —
    conversation_id, agent_id, business_key (`_business_key`, injetado incondicionalmente
    pelo worker) — NUNCA `notas_resolucao` (texto livre humano, Zona PHI/ADR-0006).
    """
    inst = await start_escalation(motivo_categoria="solicitacao_humano", severidade="leve")
    iid = inst["id"]
    business_key = inst.get("businessKey") or inst.get("business_key")

    await probe.drain()
    task = await engine.await_user_task(iid, "UT_TratarEscalonamento")
    assert task.candidate_groups == frozenset({"atendimento-humano"})  # DMN: P3

    await engine.complete_task_as_human(
        task.id, {"resultado": "devolvido_agente", "notas_resolucao": "instrucoes do humano"}
    )
    await probe.drain()

    assert probe.has_event(_RESOLVED, resultado="devolvido_agente")
    # GAP-ESC-3: `escalation.resolved` tambem NUNCA carrega notas_resolucao (Zona PHI).
    assert all("notas_resolucao" not in e.payload for e in probe.events_on(_RESOLVED))
    assert probe.has_event(_PROCESS_COMPLETED, resultado="devolvido_agente")
    completed = probe.events_on(_PROCESS_COMPLETED)[0]
    assert completed.payload.get("conversation_id")
    assert completed.payload.get("agent_id") == "helena"
    if business_key:
        assert completed.payload.get("_business_key") == business_key
    assert "notas_resolucao" not in completed.payload  # GAP-ESC-1: PHI nunca no fato
    ended = await engine.activity_instances_ended(iid)
    assert "End_DevolvidoAoAgente" in ended


async def test_happy_path_emergencia_acionada(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """P1 risco_psicossocial; emergencia_acionada => resolved; fim End_ResolvidoPorHumano."""
    inst = await start_escalation(motivo_categoria="risco_psicossocial", severidade="grave")
    iid = inst["id"]

    await probe.drain()
    task = await engine.await_user_task(iid, "UT_TratarEscalonamento")
    await engine.complete_task_as_human(
        task.id, {"resultado": "emergencia_acionada", "notas_resolucao": "SAMU acionado"}
    )
    await probe.drain()

    assert probe.has_event(_RESOLVED, resultado="emergencia_acionada")
    ended = await engine.activity_instances_ended(iid)
    assert "End_ResolvidoPorHumano" in ended


# --- Roteamento DMN ------------------------------------------------------------------


async def test_dmn_routing_p1_plantao_clinico(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """red_flag_clinico+grave => UT em plantao-clinico (P1)."""
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    await probe.drain()
    task = await engine.await_user_task(inst["id"], "UT_TratarEscalonamento")
    assert task.candidate_groups == frozenset({"plantao-clinico"})


async def test_dmn_routing_catchall_fail_safe(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """motivo desconhecido => catch-all P2/atendimento-humano (nunca P3)."""
    inst = await start_escalation(motivo_categoria="categoria_inexistente", severidade="moderada")
    await probe.drain()
    task = await engine.await_user_task(inst["id"], "UT_TratarEscalonamento")
    assert task.candidate_groups == frozenset({"atendimento-humano"})


# --- Timers (job execution, sem sleep) -----------------------------------------------


@pytest.mark.xfail(reason=_NOTIFY_KAFKA_GAP_REASON, strict=True)
async def test_timer_ack_nao_interruptivo_alerta_supervisor(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """Timer ack (PT5M) nao-interruptivo: sla_breached(ack) + supervisor notificado; UT segue aberta."""
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    iid = inst["id"]

    await probe.drain()
    await engine.await_user_task(iid, "UT_TratarEscalonamento")

    job = await engine.await_timer_job(iid, "BT_SlaAck")
    await engine.execute_job(job.id)
    await probe.drain()

    assert probe.has_event(_BREACHED, fase="ack")
    assert probe.notified_supervisors  # worker notify_supervisor recebeu a task
    # Nao-interruptivo: a User Task de tratamento CONTINUA aberta.
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert "UT_TratarEscalonamento" in open_keys


async def test_timer_resolucao_interruptivo_supervisor_assume(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """Timer resolucao (PT30M) interruptivo: sla_breached(resolucao); UT cancelada; UT_SupervisorAssume."""
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    iid = inst["id"]

    await probe.drain()
    await engine.await_user_task(iid, "UT_TratarEscalonamento")

    job = await engine.await_timer_job(iid, "BT_SlaResolucao")
    await engine.execute_job(job.id)
    await probe.drain()

    assert probe.has_event(_BREACHED, fase="resolucao")
    sup = await engine.await_user_task(iid, "UT_SupervisorAssume")
    assert sup.candidate_groups == frozenset({"supervisao-atendimento"})
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert "UT_TratarEscalonamento" not in open_keys  # cancelada (interruptivo)


async def test_supervisor_resolve_apos_breach(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """Apos breach de resolucao, supervisor completa UT_SupervisorAssume => resolved."""
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    iid = inst["id"]

    await probe.drain()
    await engine.await_user_task(iid, "UT_TratarEscalonamento")
    job = await engine.await_timer_job(iid, "BT_SlaResolucao")
    await engine.execute_job(job.id)
    await probe.drain()

    sup = await engine.await_user_task(iid, "UT_SupervisorAssume")
    await engine.complete_task_as_human(
        sup.id, {"resultado": "resolvido_humano", "notas_resolucao": "supervisor resolveu"}
    )
    await probe.drain()

    assert probe.has_event(_RESOLVED, resultado="resolvido_humano")
    ended = await engine.activity_instances_ended(iid)
    assert "End_ResolvidoPorHumano" in ended


# --- Escalation / erros: fallback de canal -------------------------------------------


@pytest.mark.xfail(reason=_NOTIFY_KAFKA_GAP_REASON, strict=True)
async def test_falha_notificacao_usa_fallback(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """notify_team lanca ERR_ESC_NOTIFY_FAILED => fallback notifica supervisor e UT e criada mesmo assim.

    Falha o CANAL real (publish do FakeKafkaPublisher) uma vez para a notificacao de team. O
    handler REAL (`NotifyTeamWorker`) traduz isso em `ERR_ESC_NOTIFY_FAILED` (BPMN error) —
    exercitamos o caminho de erro do worker de verdade, sem injecao sintetica.
    """
    probe.fault.fail_notification_types.add("escalation.notify_team")
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    iid = inst["id"]

    await probe.drain()  # notify_team falha -> fallback notify_supervisor

    assert probe.notified_supervisors  # ST_NotificarFallback executou
    # Escalonamento nunca se perde por falha de canal: a User Task existe.
    task = await engine.await_user_task(iid, "UT_TratarEscalonamento")
    assert task.candidate_groups == frozenset({"plantao-clinico"})


async def test_falha_notificacao_ambos_canais_ainda_cria_ut(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """GAP-ESC-5: MESMO se team E o fallback de canal falharem, a User Task ainda e criada.

    `NotifySupervisorWorker` (ST_NotificarFallback) levanta ERR_ESC_NOTIFY_FAILED — o
    boundaryEvent `BE_NotifFallbackFailed` captura e continua para UT_TratarEscalonamento (mesmo
    alvo do caminho feliz). O HITL obrigatorio (ADR-0005) nunca fica preso por um glitch de canal
    duplo.
    """
    probe.fault.fail_notification_types.add("escalation.notify_team")
    probe.fault.fail_notification_types.add("escalation.notify_supervisor")
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    iid = inst["id"]

    await probe.drain()  # notify_team falha -> fallback -> fallback TAMBEM falha -> boundary continua

    assert not probe.notified_supervisors  # nem o fallback conseguiu publicar
    task = await engine.await_user_task(iid, "UT_TratarEscalonamento")
    assert task.candidate_groups == frozenset({"plantao-clinico"})


@pytest.mark.xfail(reason=_NOTIFY_KAFKA_GAP_REASON, strict=True)
async def test_falha_publish_requested_nao_bloqueia_roteamento(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """GAP-ESC-5: falha ao publicar escalation.requested (worker generico operadora.events.publish)
    NUNCA bloqueia o roteamento/HITL — `BE_PubReqFailed` continua para BRT_RotearEscalonamento
    (mesmo alvo do caminho feliz), exatamente como se o publish tivesse tido sucesso.
    """
    probe.fault.fail_topics.add(_REQUESTED)
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    iid = inst["id"]

    await probe.drain()

    # O evento de dominio requested NUNCA foi publicado (falhou de verdade)...
    assert not probe.has_event(_REQUESTED)
    # ...mas o processo seguiu incondicionalmente: notify_team rodou e a UT foi criada.
    assert probe.notified_teams
    task = await engine.await_user_task(iid, "UT_TratarEscalonamento")
    assert task.candidate_groups == frozenset({"plantao-clinico"})


# --- Idempotencia --------------------------------------------------------------------


async def test_business_key_idempotente(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """Segundo start com a mesma business key nao cria 2a instancia ativa (start idempotente).

    O contrato manda o CHAMADOR (mcp-cibseven.start_process) consultar a business key antes de
    iniciar. Aqui replicamos esse contrato: consultamos instancias ativas e so iniciamos se nao
    houver — e asserimos que so existe UMA instancia ativa para a chave. Nao depende de
    `probe.drain()` progredir alem do primeiro publish — sobrevive ao gap acima (nao marcado
    xfail).
    """
    conv = "conv-teste-idem-001"
    business_key = f"ESC-amh-{conv}"

    first = await start_escalation(conversation_id=conv)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1

    # Segundo "start" idempotente: ja existe ativa => NAO inicia outra.
    active_before = await engine.find_active_instances(business_key)
    assert len(active_before) == 1
    assert active_before[0]["id"] == first["id"]


# --- Auditoria: todo fim emite evento de dominio antes ------------------------------


async def test_todos_os_fins_emitem_evento_de_dominio(
    engine: EngineRest, probe: EngineProbe, start_escalation: StartEscalation
) -> None:
    """Cada caminho de fim tem evento Kafka correspondente publicado (sem fim silencioso)."""
    # Caminho 1: resolvido por humano.
    i1 = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    await probe.drain()
    t1 = await engine.await_user_task(i1["id"], "UT_TratarEscalonamento")
    await engine.complete_task_as_human(t1.id, {"resultado": "resolvido_humano", "notas_resolucao": "x"})
    await probe.drain()
    assert probe.has_event(_RESOLVED, resultado="resolvido_humano")

    # Caminho 2: supervisor alertado (ramo ack nao-interruptivo).
    i2 = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave")
    await probe.drain()
    await engine.await_user_task(i2["id"], "UT_TratarEscalonamento")
    job = await engine.await_timer_job(i2["id"], "BT_SlaAck")
    await engine.execute_job(job.id)
    await probe.drain()
    ended2 = await engine.activity_instances_ended(i2["id"])
    assert "End_SupervisorAlertado" in ended2
    assert probe.has_event(_BREACHED, fase="ack")  # evento publicado no ramo antes do fim
