"""Unit tests for `maezo.platform.integrations.notifications_bridge` (T2.6-EB3 part 5).

TDD London School: `handle_bridge_message`/`run_consumer_loop` are exercised against
`FakeBridgeKafkaConsumer` and a spy `NotificationBridge` starter — no Kafka broker, no CIB
Seven engine. The module's own docstring documents the honest boundary this test file does
NOT (and cannot) cross: `AioKafkaBridgeConsumer` actually talking to a real broker — see
`tests/integration/platform/test_notifications_bridge_live_kafka.py` for that loud skip.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from maezo.gateway.audit import AuditRecord
from maezo.platform.integrations.notifications_bridge import (
    BRIDGE_DLQ_REASONS,
    DEFAULT_CONSUMER_GROUP_ID,
    NOTIFICATIONS_TOPIC,
    REASON_INVALID_JSON,
    REASON_MISSING_TYPE,
    REASON_NOT_A_JSON_OBJECT,
    SLA_ALERT_OUTCOME_ESCALATED,
    SLA_ALERT_OUTCOME_NOT_ANCHORED,
    SLA_ALERT_OUTCOME_UNRECOGNISED_SHAPE,
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
    MalformedBridgeMessageError,
    NotificationsBridgeSettings,
    _deserialize_json_value,
    _record_sla_alert_outcome,
    build_bridge,
    handle_bridge_message,
    run_consumer_loop,
)
from maezo.platform.notification_bridge import (
    PROCESS_KEY_ESCALATION,
    SLA_ALERT_CANAL,
    SLA_ALERT_MOTIVO_CATEGORIA,
    SLA_ALERT_NOTIFICATION_TYPES,
    SLA_ALERT_SEVERIDADE,
    SLA_ALERT_SOURCE_AGENT_ID,
    HandoffResult,
    NotificationBridge,
    NotificationBridgeHandoffFailedError,
)
from maezo.tools.workers.harness import FakeAuditSink

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StarterSpy:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._raises = raises

    async def __call__(self, process_key: str, variables: dict[str, Any]) -> str:
        self.calls.append((process_key, variables))
        if self._raises is not None:
            raise self._raises
        return f"instance-{process_key}-spy"


def _bridge_with_spy() -> tuple[NotificationBridge, _StarterSpy]:
    spy = _StarterSpy()
    return NotificationBridge(cibseven_starter=spy), spy


_INTAKE_RECURSO_MESSAGE = {
    "type": "agents.events.recurso.intake_recebido",  # ADR-0040: the intake event
    "tenant_id": "amh",
    "glosa_id": "GLOSA-1",
    "numero_guia_tiss": "GUIA-1",
}


# ---------------------------------------------------------------------------
# handle_bridge_message — message -> fenced start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_bridge_message_dispatches_matching_rule() -> None:
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, dict(_INTAKE_RECURSO_MESSAGE))

    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].target_process == "SP-OP-RECURSO-001"
    assert spy.calls[0][1]["business_key"] == "RECURSO-amh-GUIA-1-GLOSA-1"


@pytest.mark.asyncio
async def test_handle_bridge_message_no_matching_rule_is_not_an_error() -> None:
    """A well-formed message whose `type` matches no registered rule is a legitimate no-op —
    NOT a malformed message (distinct fail-closed categories)."""
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, {"type": "some.unregistered.event"})

    assert len(results) == 1
    assert results[0].handoff_triggered is False
    assert len(spy.calls) == 0


@pytest.mark.asyncio
async def test_handle_bridge_message_fails_closed_on_missing_type() -> None:
    bridge, _spy = _bridge_with_spy()
    with pytest.raises(MalformedBridgeMessageError):
        await handle_bridge_message(bridge, {"tenant_id": "amh"})


@pytest.mark.asyncio
async def test_handle_bridge_message_fails_closed_on_blank_type() -> None:
    bridge, _spy = _bridge_with_spy()
    with pytest.raises(MalformedBridgeMessageError):
        await handle_bridge_message(bridge, {"type": "   "})


@pytest.mark.asyncio
async def test_handle_bridge_message_fails_closed_on_non_mapping() -> None:
    bridge, _spy = _bridge_with_spy()
    with pytest.raises(MalformedBridgeMessageError):
        await handle_bridge_message(bridge, ["not", "a", "dict"])


@pytest.mark.asyncio
async def test_handle_bridge_message_propagates_genuine_handoff_failure() -> None:
    """A well-formed, rule-matching message whose starter genuinely fails propagates
    NotificationBridgeHandoffFailedError unchanged (EB-3 part 1) — handle_bridge_message adds
    no extra try/except around on_event."""
    spy = _StarterSpy(raises=RuntimeError("engine unreachable"))
    bridge = NotificationBridge(cibseven_starter=spy)
    with pytest.raises(NotificationBridgeHandoffFailedError):
        await handle_bridge_message(bridge, dict(_INTAKE_RECURSO_MESSAGE))


# ---------------------------------------------------------------------------
# SLA-risk alert -> a REAL human task (R-104, WP-ALERTA-SLA-CANAL)
#
# What these tests pin is the EFFECT, not a pure function: an SLA alert on
# `operadora.notifications.internal` must reach the fenced start chokepoint as an
# SP-OP-ESCALATION-001 start, because that process's `UT_TratarEscalonamento` carries
# `camunda:candidateGroups="${roteamento.grupo_atendimento}"` — the human queue the owner's
# decision names. Every assertion below is made THROUGH `handle_bridge_message`, i.e. through the
# production call path, so deleting the registration loop in `_register_default_handoffs` turns
# them RED instead of leaving an inert rule nobody calls.
# ---------------------------------------------------------------------------


class _MetricRecorder:
    """Records `record_sla_alert_human_task` calls instead of touching a Prometheus registry."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._raises = raises

    def __call__(self, *, alert_domain: str, outcome: str) -> None:
        self.calls.append((alert_domain, outcome))
        if self._raises is not None:
            raise self._raises


@pytest.fixture
def sla_metric_recorder(monkeypatch: pytest.MonkeyPatch) -> _MetricRecorder:
    recorder = _MetricRecorder()
    monkeypatch.setattr(
        "maezo.platform.integrations.notifications_bridge.record_sla_alert_human_task", recorder
    )
    return recorder


_SLA_ALERT_MESSAGES: dict[str, dict[str, Any]] = {
    # Exactly the payloads the four real publishers stamp (auth.py/recurso.py/programa.py/lgpd.py
    # `make_notify_sla_risk_handler`), so these are messages the bridge really can receive.
    # WP-J1-09 (owner decision #17) added `auth`: its alert used to reach nobody at all.
    "auth.notify_sla_risk": {
        "type": "auth.notify_sla_risk",
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-7",
        "beneficiario_pseudo_id": "PSEUDO-7",
    },
    "recurso.notify_sla_risk": {
        "type": "recurso.notify_sla_risk",
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "glosa_type": "tecnica",
    },
    "programa.notify_sla_risk": {
        "type": "programa.notify_sla_risk",
        "tenant_id": "amh",
        "programa_id": "PROG-1",
        "beneficiario_pseudo_id": "PSEUDO-1",
    },
    "lgpd.notify_sla_risk": {
        "type": "lgpd.notify_sla_risk",
        "tenant_id": "amh",
        "titular_pseudo_id": "PSEUDO-9",
        "tipo_requisicao": "acesso",
        "sla_breach_task_name": "UT_RevisaoDpo",
        "sla_breach_phase": "resolution",
    },
}

_SLA_ALERT_BUSINESS_KEYS = {
    # The key owner decision #17 ratified verbatim: `ESC-{tenant}-sla-auth-{numero_guia_tiss}`.
    "auth.notify_sla_risk": "ESC-amh-sla-auth-GUIA-7",
    "recurso.notify_sla_risk": "ESC-amh-sla-recurso-GUIA-1-GLOSA-1",
    "programa.notify_sla_risk": "ESC-amh-sla-programa-PROG-1",
    "lgpd.notify_sla_risk": "ESC-amh-sla-lgpd-PSEUDO-9-resolution",
}


@pytest.mark.asyncio
async def test_um_alerta_de_sla_conhecido_inicia_escalation_pela_cerca() -> None:
    """THE production wiring. A real `recurso.notify_sla_risk` message, driven through
    `handle_bridge_message`, must reach the starter as an SP-OP-ESCALATION-001 start with the
    contract's business key and the escalation input variables — that start is what puts a
    `candidateGroups`-routed User Task in a human's queue.

    Goes RED if the registration loop in `_register_default_handoffs` is removed (the rule stops
    matching), if the target process changes, or if any escalation variable stops being derived.
    """
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"]))

    assert len(spy.calls) == 1
    process_key, variables = spy.calls[0]
    assert process_key == PROCESS_KEY_ESCALATION
    assert variables["business_key"] == "ESC-amh-sla-recurso-GUIA-1-GLOSA-1"
    assert variables["conversation_id"] == "sla-recurso-GUIA-1-GLOSA-1"
    assert variables["tenant_id"] == "amh"
    assert variables["source_agent_id"] == SLA_ALERT_SOURCE_AGENT_ID
    assert variables["motivo_categoria"] == SLA_ALERT_MOTIVO_CATEGORIA
    assert variables["severidade"] == SLA_ALERT_SEVERIDADE
    assert variables["canal"] == SLA_ALERT_CANAL
    # A glosa appeal is prestador-side: no beneficiary in the payload, so none is fabricated.
    assert variables["beneficiario_pseudo_id"] == ""
    assert "SP-OP-RECURSO-001" in variables["resumo_contexto"]

    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].target_process == PROCESS_KEY_ESCALATION
    assert results[0].process_instance_id == f"instance-{PROCESS_KEY_ESCALATION}-spy"


@pytest.mark.asyncio
@pytest.mark.parametrize("sla_type", sorted(SLA_ALERT_NOTIFICATION_TYPES))
async def test_todo_alerta_de_sla_publicado_hoje_vira_task_humana(sla_type: str) -> None:
    """Each of the three `type`s a worker really publishes today opens its OWN escalation, keyed on
    that domain's business anchor — so two cases never collapse onto one human task."""
    bridge, spy = _bridge_with_spy()
    await handle_bridge_message(bridge, dict(_SLA_ALERT_MESSAGES[sla_type]))

    assert len(spy.calls) == 1
    process_key, variables = spy.calls[0]
    assert process_key == PROCESS_KEY_ESCALATION
    assert variables["business_key"] == _SLA_ALERT_BUSINESS_KEYS[sla_type]


@pytest.mark.asyncio
async def test_um_realerta_do_mesmo_caso_converge_na_mesma_chave_de_negocio() -> None:
    """Redelivery/repeat of the same alert derives the SAME business key, so
    `start_process_idempotent` converges on the ONE open escalation instead of flooding the
    queue — the reason this goes through the fenced chokepoint and not a raw POST."""
    bridge, spy = _bridge_with_spy()
    message = dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"])
    await handle_bridge_message(bridge, dict(message))
    await handle_bridge_message(bridge, dict(message))

    assert [key for key, _ in spy.calls] == [PROCESS_KEY_ESCALATION, PROCESS_KEY_ESCALATION]
    assert spy.calls[0][1]["business_key"] == spy.calls[1][1]["business_key"]


@pytest.mark.asyncio
async def test_as_duas_fases_de_sla_do_lgpd_abrem_tasks_distintas() -> None:
    """`lgpd.py` serves TWO service tasks on one topic — the internal P7D ack alert and the LGPD
    art. 19-II LEGAL-deadline breach. Anchoring on the titular alone would collapse the legal
    breach into the still-open ack escalation and raise no new task; the phase anchor prevents it.
    """
    bridge, spy = _bridge_with_spy()
    ack = dict(_SLA_ALERT_MESSAGES["lgpd.notify_sla_risk"], sla_breach_phase="ack")
    await handle_bridge_message(bridge, ack)
    await handle_bridge_message(bridge, dict(_SLA_ALERT_MESSAGES["lgpd.notify_sla_risk"]))

    keys = [variables["business_key"] for _, variables in spy.calls]
    assert keys == ["ESC-amh-sla-lgpd-PSEUDO-9-ack", "ESC-amh-sla-lgpd-PSEUDO-9-resolution"]


@pytest.mark.asyncio
async def test_duas_glosas_de_guias_diferentes_abrem_tasks_distintas() -> None:
    """§Delta D1. SP-OP-RECURSO-001's business key is `RECURSO-{tenant}-{guia}-{glosa}` — "one
    recurso per glosa PER GUIA TISS" — so `glosa_id` is NOT unique on its own. Two alerts about
    DIFFERENT recursos that share a `glosa_id` under different guias must open TWO escalations;
    anchoring on `glosa_id` alone would mint one key and, because the start is idempotent by
    business key, the SECOND alert would open no human task at all."""
    bridge, spy = _bridge_with_spy()
    await handle_bridge_message(bridge, dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"]))
    await handle_bridge_message(
        bridge, dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"], numero_guia_tiss="GUIA-2")
    )

    keys = [variables["business_key"] for _, variables in spy.calls]
    assert keys == ["ESC-amh-sla-recurso-GUIA-1-GLOSA-1", "ESC-amh-sla-recurso-GUIA-2-GLOSA-1"]


@pytest.mark.asyncio
async def test_um_alerta_de_recurso_sem_guia_fica_dormente_e_visivel(
    sla_metric_recorder: _MetricRecorder,
) -> None:
    """§Delta D1, lado fail-closed. Sem `numero_guia_tiss` a chave seria
    `ESC-amh-sla-recurso--GLOSA-1`, degenerada e colidente entre guias — a regra fica dormente e o
    buraco e CONTADO, nunca silencioso."""
    bridge, spy = _bridge_with_spy()
    message = dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"])
    del message["numero_guia_tiss"]
    results = await handle_bridge_message(bridge, message)

    assert len(spy.calls) == 0
    assert results[0].handoff_triggered is False
    assert sla_metric_recorder.calls == [("recurso", SLA_ALERT_OUTCOME_NOT_ANCHORED)]


def test_um_handoff_sem_id_de_instancia_nao_conta_como_escalonado(
    sla_metric_recorder: _MetricRecorder,
) -> None:
    """A handoff marked triggered but carrying a BLANK `process_instance_id` is not a human task
    that exists, so it must not be counted `escalated`.

    `execute_handoff`'s own fail-closed guard makes this shape unreachable through the normal
    pipeline (it raises instead), which is exactly why the clause needs a direct test: without one,
    dropping `and result.process_instance_id.strip()` from the predicate leaves the whole suite
    green and the defence silently rots. Driven straight at `_record_sla_alert_outcome` because the
    shape can only be constructed, never produced.
    """
    blank = HandoffResult(
        evaluated=True,
        handoff_triggered=True,
        target_process=PROCESS_KEY_ESCALATION,
        process_instance_id="   ",
    )
    _record_sla_alert_outcome("recurso.notify_sla_risk", [blank])

    assert sla_metric_recorder.calls == [("recurso", SLA_ALERT_OUTCOME_NOT_ANCHORED)]


@pytest.mark.asyncio
async def test_o_alerta_de_sla_escalonado_e_contado(sla_metric_recorder: _MetricRecorder) -> None:
    """The counter counts the REAL outcome. Goes RED if the increment is dropped, and — because it
    is fed by `handle_bridge_message`'s own result list — also if the escalation stops happening.
    """
    bridge, _spy = _bridge_with_spy()
    await handle_bridge_message(bridge, dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"]))

    assert sla_metric_recorder.calls == [("recurso", SLA_ALERT_OUTCOME_ESCALATED)]


@pytest.mark.asyncio
async def test_uma_mensagem_nao_sla_segue_intocada(sla_metric_recorder: _MetricRecorder) -> None:
    """An ordinary process-start message is BYTE-IDENTICALLY unaffected by this rule existing: the
    same rule matches, the same starter call is made, and NO SLA sample is emitted."""
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, dict(_INTAKE_RECURSO_MESSAGE))

    assert len(results) == 1
    assert results[0].target_process == "SP-OP-RECURSO-001"
    assert spy.calls[0][1]["business_key"] == "RECURSO-amh-GUIA-1-GLOSA-1"
    assert sla_metric_recorder.calls == []


@pytest.mark.asyncio
async def test_um_alerta_de_sla_sem_ancora_fica_dormente_e_visivel(
    sla_metric_recorder: _MetricRecorder,
) -> None:
    """FAIL-CLOSED, not silent. Without `glosa_id` the business key would be the degenerate
    `ESC-amh-sla-recurso-`, colliding every recurso case onto one escalation — so the rule stays
    dormant, and the gap is COUNTED (`not_anchored`) instead of vanishing as 'no rule matched'."""
    bridge, spy = _bridge_with_spy()
    message = dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"])
    del message["glosa_id"]
    results = await handle_bridge_message(bridge, message)

    assert len(spy.calls) == 0
    assert results[0].handoff_triggered is False
    assert sla_metric_recorder.calls == [("recurso", SLA_ALERT_OUTCOME_NOT_ANCHORED)]


@pytest.mark.asyncio
async def test_um_alerta_de_sla_sem_tenant_fica_dormente(sla_metric_recorder: _MetricRecorder) -> None:
    """`_anchored` requires `tenant_id` structurally for every rule — a tenant-less alert would
    mint `ESC--sla-recurso-GLOSA-1`, an orphan outside every tenant's cockpit."""
    bridge, spy = _bridge_with_spy()
    message = dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"])
    del message["tenant_id"]
    await handle_bridge_message(bridge, message)

    assert len(spy.calls) == 0
    assert sla_metric_recorder.calls == [("recurso", SLA_ALERT_OUTCOME_NOT_ANCHORED)]


@pytest.mark.asyncio
async def test_um_type_de_sla_desconhecido_falha_fechado(sla_metric_recorder: _MetricRecorder) -> None:
    """A tenth worker wired to publish before the spec table learns its `type` must NOT be
    swallowed as an ordinary unmatched event: it is counted `unrecognised_shape` under the
    `unknown` domain (the producer-controlled `type` is never a metric label) and warned about."""
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(
        bridge, {"type": "credenciamento.notify_sla_risk", "tenant_id": "amh"}
    )

    assert len(spy.calls) == 0
    assert results[0].handoff_triggered is False
    assert sla_metric_recorder.calls == [("unknown", SLA_ALERT_OUTCOME_UNRECOGNISED_SHAPE)]


@pytest.mark.asyncio
async def test_uma_falha_de_start_do_escalation_propaga_e_nao_e_contada_como_escalonada(
    sla_metric_recorder: _MetricRecorder,
) -> None:
    """FAIL-CLOSED. A genuine start failure propagates (EB-3 part 1) so `run_consumer_loop` never
    commits the offset, and NOTHING is counted — a failed escalation can never be reported as a
    human task that exists."""
    spy = _StarterSpy(raises=RuntimeError("engine unreachable"))
    bridge = NotificationBridge(cibseven_starter=spy)
    with pytest.raises(NotificationBridgeHandoffFailedError):
        await handle_bridge_message(bridge, dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"]))

    assert sla_metric_recorder.calls == []


@pytest.mark.asyncio
async def test_uma_falha_de_telemetria_nao_derruba_o_despacho(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telemetry is best-effort — but NARROWLY so: only the two failure modes the recorder really
    has (`ImportError` from `observability._get_metrics_collector`'s lazy `maezo.runtime.metrics`
    import, `ValueError` from prometheus_client) are
    absorbed. The escalation already started before this runs, so nothing is lost."""
    monkeypatch.setattr(
        "maezo.platform.integrations.notifications_bridge.record_sla_alert_human_task",
        _MetricRecorder(raises=ValueError("duplicated timeseries")),
    )
    bridge, spy = _bridge_with_spy()
    results = await handle_bridge_message(bridge, dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"]))

    assert spy.calls[0][0] == PROCESS_KEY_ESCALATION
    assert results[0].handoff_triggered is True


@pytest.mark.asyncio
async def test_uma_falha_inesperada_de_telemetria_nao_e_engolida(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is narrow ON PURPOSE (it replaced a blind `except Exception`): an error class the
    recorder has no business raising is a bug this module must not hide."""
    monkeypatch.setattr(
        "maezo.platform.integrations.notifications_bridge.record_sla_alert_human_task",
        _MetricRecorder(raises=RuntimeError("registry exploded")),
    )
    bridge, _spy = _bridge_with_spy()
    with pytest.raises(RuntimeError, match="registry exploded"):
        await handle_bridge_message(bridge, dict(_SLA_ALERT_MESSAGES["recurso.notify_sla_risk"]))


def test_a_tabela_de_alertas_de_sla_e_o_conjunto_real_de_publicadores() -> None:
    """Documents the spec table so a future edit is a REVIEWED diff, not a silent drift. Measured:
    `grep -rln "_NOTIFY_SLA_RISK_NOTIFICATION_TYPE" src/maezo/tools/workers/` -> auth, lgpd,
    programa, recurso.

    WP-J1-09 (owner decision #17) added `auth`. It is NOT a fourth entry in a list that happened
    to grow: `auth` was the ONE domain this table's own docstring named as carrying an SLA-risk
    step with NO publisher, and ratifying the AUTH->ESCALATION handoff is what gave it one. A
    fifth entry appearing without a matching `make_notify_sla_risk_handler` in `src/maezo/tools/
    workers/` is drift, and this assertion is what refuses it."""
    assert {
        "auth.notify_sla_risk",
        "lgpd.notify_sla_risk",
        "recurso.notify_sla_risk",
        "programa.notify_sla_risk",
    } == SLA_ALERT_NOTIFICATION_TYPES


# ---------------------------------------------------------------------------
# run_consumer_loop — drives FakeBridgeKafkaConsumer, commits only on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_consumer_loop_dispatches_and_commits_each_message() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE), {"type": "no.such.rule"}])
    await consumer.start()

    await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 2
    assert len(spy.calls) == 1  # only the matching message actually started a process


@pytest.mark.asyncio
async def test_run_consumer_loop_fails_closed_on_malformed_message_without_committing() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE), {"no": "type-field"}])
    await consumer.start()

    with pytest.raises(MalformedBridgeMessageError):
        await run_consumer_loop(consumer, bridge)

    # First (well-formed) message dispatched + committed; the malformed second one raised
    # BEFORE any commit for it.
    assert consumer.commits == 1
    assert len(spy.calls) == 1


@pytest.mark.asyncio
async def test_run_consumer_loop_fails_closed_on_genuine_handoff_failure_without_committing() -> None:
    spy = _StarterSpy(raises=RuntimeError("simulated engine failure"))
    bridge = NotificationBridge(cibseven_starter=spy)
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE)])
    await consumer.start()

    with pytest.raises(NotificationBridgeHandoffFailedError):
        await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 0  # never committed a message whose handoff failed to start


@pytest.mark.asyncio
async def test_run_consumer_loop_exhausts_fake_consumer_cleanly_when_all_messages_are_no_ops() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([{"type": "a.b"}, {"type": "c.d"}, {"type": "e.f"}])
    await consumer.start()

    await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 3
    assert len(spy.calls) == 0


# ---------------------------------------------------------------------------
# _deserialize_json_value — the aiokafka value_deserializer hook
# ---------------------------------------------------------------------------


def test_deserialize_json_value_parses_valid_payload() -> None:
    raw = b'{"type": "ans.cron_due", "report_type": "DIOPS"}'
    value = _deserialize_json_value(raw)
    assert value == {"type": "ans.cron_due", "report_type": "DIOPS"}


def test_deserialize_json_value_fails_closed_on_invalid_json() -> None:
    with pytest.raises(MalformedBridgeMessageError):
        _deserialize_json_value(b"{not valid json")


def test_deserialize_json_value_fails_closed_on_undecodable_bytes() -> None:
    with pytest.raises(MalformedBridgeMessageError):
        _deserialize_json_value(b"\xff\xfe\x00\x01")


# ---------------------------------------------------------------------------
# NotificationsBridgeSettings — env-driven config
# ---------------------------------------------------------------------------


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "TENANT_ID",
        "CIBSEVEN_BASE_URL",
        "CIBSEVEN_AUTH_TOKEN",
        "DATABASE_URL",
        "KAFKA_BOOTSTRAP_SERVERS",
        "NOTIFICATIONS_BRIDGE_KAFKA_TOPIC",
        "NOTIFICATIONS_BRIDGE_KAFKA_GROUP_ID",
        "WORKER_CLIENT_TIMEOUT_S",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = NotificationsBridgeSettings(_env_file=None)
    assert settings.tenant_id == "amh"
    assert settings.database_url is None
    assert settings.kafka_bootstrap_servers == "localhost:9092"
    assert settings.kafka_topic == NOTIFICATIONS_TOPIC
    assert settings.kafka_group_id == DEFAULT_CONSUMER_GROUP_ID


def test_settings_reads_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_ID", "acme")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    monkeypatch.setenv("DATABASE_URL", "postgresql://maezo:maezo@localhost:5647/maezo")
    settings = NotificationsBridgeSettings(_env_file=None)
    assert settings.tenant_id == "acme"
    assert settings.kafka_bootstrap_servers == "kafka:9092"
    assert settings.database_url == "postgresql://maezo:maezo@localhost:5647/maezo"


# ---------------------------------------------------------------------------
# build_bridge — fail-closed composition root
# ---------------------------------------------------------------------------


def test_build_bridge_refuses_without_database_url() -> None:
    """FAIL-CLOSED (ADR-0007/T-C2): no DATABASE_URL -> refuse to construct the bridge at all,
    never a fake/no-op audit sink standing in for a real one in production."""
    settings = NotificationsBridgeSettings(_env_file=None, database_url=None)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_bridge(settings)


def test_build_bridge_constructs_bridge_with_fenced_starter() -> None:
    settings = NotificationsBridgeSettings(
        _env_file=None,
        database_url="postgresql://maezo:maezo@localhost:5647/maezo",
        tenant_id="amh",
    )
    # 3-tuple since GAP-SC-04-a (changed assertion): the root also returns the `PostgresAuditSink`
    # it constructs, so `build_dlq_shunt` reuses that ONE sink instead of opening a second pool and
    # splitting the daemon's audit chain across two instances.
    bridge, transport, audit_sink = build_bridge(settings)
    assert isinstance(bridge, NotificationBridge)
    # 10 = 5 pre-T2.6-7 + 2 T2.6-7 ANS-SUBMIT + 3 R-104 SLA-alert -> SP-OP-ESCALATION-001.
    assert bridge.count_handoffs() == 11
    assert transport is not None
    assert audit_sink is not None


# ---------------------------------------------------------------------------
# GAP-SC-04-a — the dead-letter / poison-message shunt (audit D5, gateway gvr-d05).
#
# The gap this closes: a single malformed message made `run_consumer_loop` re-raise and `main()`
# exit; the offset was never committed, so the restart re-delivered the SAME message and the
# daemon died again — head-of-line blocking every other message on the shared topic until an
# operator intervened.
#
# The shunt must not weaken fail-closed. These tests pin the four halves of that claim:
#   A. a malformed message is shunted (never dropped) and the loop CONTINUES;
#   B. a TRANSIENT failure still propagates and still blocks the offset (the A3 guarantee);
#   C. a failed DLQ publish OR a failed audit emit restores the original re-raise, no commit;
#   D. with no shunt wired, behaviour is byte-identical to before.
# ---------------------------------------------------------------------------


class _DlqPublisherSpy:
    """In-memory `BridgeDlqPublisher` double. Lives in tests/ (never in the module) — the same
    §8.4 discipline `a2a/outbox_relay.py` follows for its own composition root."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.published: list[tuple[str, bytes, bytes | None, dict[str, bytes]]] = []
        #: The header SEQUENCE as sent, duplicates intact. `published`'s `dict()` view collapses a
        #: repeated name onto its last value, which is exactly the distinction the forged-header
        #: test has to make (dropped vs merely re-stated later).
        self.header_lists: list[list[tuple[str, bytes]]] = []
        self.started = False
        self.stopped = False
        self._fail_with = fail_with

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def publish_dlq(
        self,
        topic: str,
        *,
        raw: bytes,
        key: bytes | None,
        headers: Any,
    ) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        self.header_lists.append(list(headers))
        self.published.append((topic, raw, key, dict(headers)))


def _shunt(*, publisher: _DlqPublisherSpy | None = None, sink: FakeAuditSink | None = None) -> BridgeDlqShunt:
    return BridgeDlqShunt(
        publisher=publisher or _DlqPublisherSpy(),
        audit_sink=sink or FakeAuditSink(),
        tenant_id="amh",
    )


_MALFORMED_NO_TYPE = {"tenant_id": "amh", "no": "type-field"}


# --- A. the poison path: shunted, audited, metered, and the loop keeps going -------------------


@pytest.mark.asyncio
async def test_malformed_message_is_shunted_to_the_dlq_and_the_loop_continues() -> None:
    """THE FIX, end to end: a poison message between two good ones no longer stops the loop, and
    every message — including the poison one — advances its offset."""
    bridge, spy = _bridge_with_spy()
    publisher, sink = _DlqPublisherSpy(), FakeAuditSink()
    dlq = _shunt(publisher=publisher, sink=sink)
    consumer = FakeBridgeKafkaConsumer(
        [dict(_INTAKE_RECURSO_MESSAGE), dict(_MALFORMED_NO_TYPE), dict(_INTAKE_RECURSO_MESSAGE)]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert consumer.commits == 3, "the poison message's offset must advance, like the good ones"
    assert len(spy.calls) == 2, "both well-formed messages still started their handoff"
    assert len(publisher.published) == 1
    assert publisher.published[0][0] == "operadora.notifications.internal.dlq"
    assert len(sink.emitted) == 1, "a shunt is never silent — it leaves a durable audit fact"


@pytest.mark.asyncio
async def test_shunted_message_carries_the_raw_bytes_verbatim() -> None:
    """A DLQ that re-encodes the parsed value is not evidence of what the producer sent — and for
    unparseable bytes it could not exist at all."""
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy()
    raw = b'{"this is": not json'
    consumer = FakeBridgeKafkaConsumer(
        [
            BridgeMessage(
                topic=NOTIFICATIONS_TOPIC,
                partition=2,
                offset=41,
                raw=raw,
                key=b"amh|GUIA-1|GLOSA-1",
                parse_error="invalid JSON payload: boom",
                parse_code=REASON_INVALID_JSON,
            )
        ]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher))

    _topic, published_raw, published_key, _headers = publisher.published[0]
    assert published_raw == raw
    assert published_key == b"amh|GUIA-1|GLOSA-1", (
        "the producer's partition key is carried through, so a quarantined record keeps the "
        "per-entity co-location GAP-SC-04-a's key derivation gave it"
    )


@pytest.mark.asyncio
async def test_dlq_headers_are_bounded_diagnostic_tokens_only() -> None:
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy()
    consumer = FakeBridgeKafkaConsumer(
        [BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), partition=1, offset=7)]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher))

    headers = publisher.published[0][3]
    assert headers["maezo_dlq_reason"] == REASON_MISSING_TYPE.encode()
    assert headers["maezo_dlq_source_topic"] == NOTIFICATIONS_TOPIC.encode()
    assert headers["maezo_dlq_source_partition"] == b"1"
    assert headers["maezo_dlq_source_offset"] == b"7"
    assert headers["maezo_dlq_tenant"] == b"amh"


@pytest.mark.asyncio
async def test_a_producer_forged_dlq_header_is_dropped_rather_than_carried_through() -> None:
    """`maezo_dlq_*` is the bridge's OWN namespace on this path. Kafka header lists admit
    duplicates and which copy a consumer keeps is consumer-dependent, so carrying a producer's
    forged `maezo_dlq_reason` through — even placed before ours — would let a first-wins reader
    attribute the producer's label to the bridge. Dropped; every other producer header survives."""
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy()
    consumer = FakeBridgeKafkaConsumer(
        [
            BridgeMessage.from_value(
                dict(_MALFORMED_NO_TYPE),
                partition=1,
                offset=7,
                headers=(
                    ("maezo_dlq_reason", b"forjado_pelo_produtor"),
                    ("maezo_dlq_tenant", b"outro_tenant"),
                    ("traceparent", b"00-abc-def-01"),
                ),
            )
        ]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher))

    sent = publisher.header_lists[0]
    assert [value for name, value in sent if name == "maezo_dlq_reason"] == [REASON_MISSING_TYPE.encode()], (
        "the forged copy is GONE from the list, not merely outranked by ours"
    )
    assert [value for name, value in sent if name == "maezo_dlq_tenant"] == [b"amh"]
    assert ("traceparent", b"00-abc-def-01") in sent, "non-reserved producer headers survive"


@pytest.mark.asyncio
async def test_audit_fact_is_phi_safe_and_content_free_beyond_a_one_way_hash() -> None:
    """The durable chain gets bounded class tokens plus a one-way `raw_sha256` — never the bytes.
    The hash is what makes the row EVIDENCE (an operator can match it to the DLQ record) without
    putting unvalidated, possibly-PHI-bearing content into the chain — the same discipline
    `build_start_audit_record` applies with its own `input_sha256`."""
    bridge, _spy = _bridge_with_spy()
    sink = FakeAuditSink()
    message = BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), partition=0, offset=3)
    consumer = FakeBridgeKafkaConsumer([message])
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(sink=sink))

    record, dedup_key = sink.emitted[0]
    assert isinstance(record, AuditRecord)
    assert record.agent_id == "notification_bridge"
    assert record.tenant_id == "amh"
    assert record.decision == "DENY"
    assert record.action == f"bridge_dlq:{NOTIFICATIONS_TOPIC}"
    assert record.details["reason_code"] == REASON_MISSING_TYPE
    assert record.details["raw_sha256"] == hashlib.sha256(message.raw).hexdigest()
    assert "no" not in record.details and "tenant_id" not in record.details
    serialized = repr(record.details)
    assert "type-field" not in serialized, "no byte of the offending payload reaches the chain"
    expected_sha = hashlib.sha256(message.raw).hexdigest()
    assert dedup_key == f"amh:bridge_dlq:{NOTIFICATIONS_TOPIC}:0:3:{expected_sha}"


@pytest.mark.asyncio
async def test_redelivered_poison_message_writes_one_audit_row() -> None:
    """The at-least-once cost of publish-then-audit, bounded: the dedup key pins the record's
    coordinates AND its bytes, so the SAME record redelivered collapses onto one chain row."""
    bridge, _spy = _bridge_with_spy()
    sink = FakeAuditSink()
    dlq = _shunt(sink=sink)
    message = BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), partition=0, offset=9)

    for _attempt in range(3):
        consumer = FakeBridgeKafkaConsumer([message])
        await consumer.start()
        await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert len(dlq.shunted) == 3, "each redelivery really did re-publish to the DLQ"
    assert len(sink.emitted) == 1, "and they converge on ONE durable audit row"
    assert dlq.deduped_audits == [dlq.dedup_key(message)] * 2, (
        "the two collapsed emits are OBSERVED (`emit_once_status`), not discarded — the whole "
        "point of MAJOR-2: a shunt must never commit on a dedup outcome it cannot see"
    )


@pytest.mark.asyncio
async def test_a_different_payload_at_a_reused_coordinate_writes_its_own_audit_row() -> None:
    """MAJOR-2's root cause, pinned. Broker coordinates are REUSED after a topic recreation
    (`docker compose down -v` in dev/CI; a DR recreate in production) while `audit_emit_dedup`
    survives. Under a coordinates-only dedup key the NEW poison message hit the OLD claim,
    `emit_once` no-op'd (`audit_postgres.py:317-320`), the loop committed the offset and the
    quarantine had NO row in the chain — a silent drop with extra steps.

    Same tenant, same topic, same `(partition, offset)`, DIFFERENT bytes -> TWO chain rows."""
    bridge, _spy = _bridge_with_spy()
    sink = FakeAuditSink()
    dlq = _shunt(sink=sink)
    first = BridgeMessage.from_value({"tenant_id": "amh", "sem": "tipo-1"}, partition=2, offset=0)
    second = BridgeMessage.from_value({"tenant_id": "amh", "sem": "tipo-2"}, partition=2, offset=0)
    assert (first.topic, first.partition, first.offset) == (second.topic, second.partition, second.offset)
    assert first.raw != second.raw

    for message in (first, second):
        consumer = FakeBridgeKafkaConsumer([message])
        await consumer.start()
        await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert len(sink.emitted) == 2, "a NEW poison message at a reused coordinate is a NEW fact"
    assert [key for _record, key in sink.emitted] == [dlq.dedup_key(first), dlq.dedup_key(second)]
    assert dlq.deduped_audits == [], "neither emit was a dedup no-op"
    assert {record.details["raw_sha256"] for record, _key in sink.emitted} == {
        hashlib.sha256(first.raw).hexdigest(),
        hashlib.sha256(second.raw).hexdigest(),
    }


def test_dedup_key_folds_the_content_hash_into_the_broker_coordinates() -> None:
    """The shape itself, stated once: `{tenant}:bridge_dlq:{topic}:{partition}:{offset}:{sha256}`,
    with the SAME digest `details["raw_sha256"]` carries — key and evidence name the same bytes."""
    dlq = _shunt()
    message = BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), partition=7, offset=13)

    assert dlq.dedup_key(message) == (
        f"amh:bridge_dlq:{NOTIFICATIONS_TOPIC}:7:13:{hashlib.sha256(message.raw).hexdigest()}"
    )


def test_shunt_refuses_a_sink_that_cannot_report_its_dedup_outcome() -> None:
    """FAIL-CLOSED at CONSTRUCTION. An emit-only sink cannot tell "chain row written" from "a prior
    claim already owned this key", so it would silently restore the defect the content-bound dedup
    key closes. Refused where the mistake is made, not on the first poison message."""

    class _EmitOnlySink:
        async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
            return "hash"

    emit_only: Any = _EmitOnlySink()
    with pytest.raises(TypeError, match="emit_once_status"):
        BridgeDlqShunt(publisher=_DlqPublisherSpy(), audit_sink=emit_only, tenant_id="amh")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_reason"),
    [
        ({"tenant_id": "amh"}, REASON_MISSING_TYPE),
        ({"type": "   "}, REASON_MISSING_TYPE),
        (["not", "a", "dict"], REASON_NOT_A_JSON_OBJECT),
    ],
)
async def test_every_malformation_class_reaches_the_dlq_with_its_bounded_reason_code(
    message: Any, expected_reason: str
) -> None:
    bridge, _spy = _bridge_with_spy()
    dlq = _shunt()
    consumer = FakeBridgeKafkaConsumer([message])
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert dlq.shunted == [("operadora.notifications.internal.dlq", expected_reason)]
    assert expected_reason in BRIDGE_DLQ_REASONS


@pytest.mark.asyncio
async def test_undecodable_bytes_reach_the_dlq_rather_than_killing_the_iterator() -> None:
    """Before GAP-SC-04-a a parse failure raised from INSIDE aiokafka's `value_deserializer`, i.e.
    out of the `async for` itself — no `try` the loop owned could see it, and the raw bytes were
    gone. The consumer now parses and records the failure on the message."""
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy()
    consumer = FakeBridgeKafkaConsumer(
        [
            BridgeMessage(
                topic=NOTIFICATIONS_TOPIC,
                partition=0,
                offset=1,
                raw=b"\xff\xfe\x00\x01",
                parse_error="invalid JSON payload: codec error",
                parse_code=REASON_INVALID_JSON,
            ),
            dict(_INTAKE_RECURSO_MESSAGE),
        ]
    )
    await consumer.start()

    await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher))

    assert publisher.published[0][1] == b"\xff\xfe\x00\x01"
    assert consumer.commits == 2


# --- B. the fail-closed half the shunt must NOT weaken -----------------------------------------


@pytest.mark.asyncio
async def test_transient_handoff_failure_still_propagates_and_still_blocks_the_offset() -> None:
    """THE LOAD-BEARING NEGATIVE. `NotificationBridgeHandoffFailedError` is a TRANSIENT infra
    fault (engine unreachable), not malformation — retry can clear it, so it must keep blocking
    the offset. If the shunt ever caught bare `Exception`, this goes RED and a real handoff
    failure would be quietly quarantined as if it were poison."""
    spy = _StarterSpy(raises=RuntimeError("engine unreachable"))
    bridge = NotificationBridge(cibseven_starter=spy)
    publisher, sink = _DlqPublisherSpy(), FakeAuditSink()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE)])
    await consumer.start()

    with pytest.raises(NotificationBridgeHandoffFailedError):
        await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher, sink=sink))

    assert consumer.commits == 0
    assert publisher.published == [], "a transient failure must never be quarantined as poison"
    assert sink.emitted == []


@pytest.mark.asyncio
async def test_audit_persistence_style_failure_from_the_starter_still_propagates() -> None:
    """The A3 chaos certification's own fault class (`test_a3_bridge_fail_closed.py`: audit sink
    down / dedup table absent -> the fenced starter raises before any engine start). It must reach
    the caller unchanged — the shunt's `except` arm is typed on `MalformedBridgeMessageError` and
    never sees it."""

    class _AuditDownError(RuntimeError):
        pass

    spy = _StarterSpy(raises=_AuditDownError("audit sink down"))
    bridge = NotificationBridge(cibseven_starter=spy)
    dlq = _shunt()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE)])
    await consumer.start()

    with pytest.raises(NotificationBridgeHandoffFailedError):
        await run_consumer_loop(consumer, bridge, dlq=dlq)

    assert consumer.commits == 0
    assert dlq.shunted == []


# --- C. the shunt's own failures restore the original fail-closed outcome ----------------------


@pytest.mark.asyncio
async def test_failed_dlq_publish_re_raises_and_does_not_commit() -> None:
    """ "Confirmed before commit" as a test: if the DLQ publish fails, the offset must NOT advance,
    or the message would be lost — a silent drop wearing a DLQ's clothes."""
    bridge, _spy = _bridge_with_spy()
    publisher = _DlqPublisherSpy(fail_with=RuntimeError("dlq broker down"))
    sink = FakeAuditSink()
    consumer = FakeBridgeKafkaConsumer([dict(_MALFORMED_NO_TYPE)])
    await consumer.start()

    with pytest.raises(RuntimeError, match="dlq broker down"):
        await run_consumer_loop(consumer, bridge, dlq=_shunt(publisher=publisher, sink=sink))

    assert consumer.commits == 0
    assert sink.emitted == [], "no audit fact may assert a shunt that did not happen"


@pytest.mark.asyncio
async def test_failed_audit_emit_re_raises_and_does_not_commit() -> None:
    """The audit fact is as load-bearing as the publish: a quarantine nobody recorded is a drop
    the platform cannot account for later."""
    bridge, _spy = _bridge_with_spy()
    sink = FakeAuditSink()
    sink.always_fail = RuntimeError("audit sink down")
    consumer = FakeBridgeKafkaConsumer([dict(_MALFORMED_NO_TYPE)])
    await consumer.start()

    with pytest.raises(RuntimeError, match="audit sink down"):
        await run_consumer_loop(consumer, bridge, dlq=_shunt(sink=sink))

    assert consumer.commits == 0


@pytest.mark.asyncio
async def test_invalid_source_topic_fails_closed_rather_than_minting_a_dlq_name() -> None:
    """`dlq_topic_for` validates the SOURCE topic through the registry's convention on the hot
    path. A malformed topic raises here — no commit — instead of quarantining into a
    valid-looking name nobody registered."""
    from maezo.platform.topic_registry import TopicValidationError

    bridge, _spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer(
        [BridgeMessage.from_value(dict(_MALFORMED_NO_TYPE), topic="Not.A.Valid.Topic")]
    )
    await consumer.start()

    with pytest.raises(TopicValidationError):
        await run_consumer_loop(consumer, bridge, dlq=_shunt())

    assert consumer.commits == 0


# --- D. no shunt wired -> the pre-GAP-SC-04-a behaviour, byte for byte -------------------------


@pytest.mark.asyncio
async def test_without_a_shunt_a_malformed_message_still_re_raises_and_does_not_commit() -> None:
    bridge, spy = _bridge_with_spy()
    consumer = FakeBridgeKafkaConsumer([dict(_INTAKE_RECURSO_MESSAGE), dict(_MALFORMED_NO_TYPE)])
    await consumer.start()

    with pytest.raises(MalformedBridgeMessageError):
        await run_consumer_loop(consumer, bridge)

    assert consumer.commits == 1
    assert len(spy.calls) == 1


# --- BridgeMessage ------------------------------------------------------------------------------


def test_bridge_message_from_value_encodes_its_own_raw_bytes() -> None:
    import json

    message = BridgeMessage.from_value({"type": "ans.cron_due"})
    assert message.raw == json.dumps({"type": "ans.cron_due"}).encode("utf-8")
    assert message.value == {"type": "ans.cron_due"}
    assert message.parse_error == ""


def test_bridge_message_from_value_refuses_a_non_serializable_value() -> None:
    """No `repr()`-shaped `raw` invented for something that would not round-trip."""
    with pytest.raises(TypeError):
        BridgeMessage.from_value(object())


def test_malformed_error_codes_are_all_in_the_closed_vocabulary() -> None:
    """The metric label's cardinality bound, pinned: every code the module can raise is declared,
    so no unbounded parser text can reach `maezo_bridge_dlq_total{reason}`."""
    with pytest.raises(MalformedBridgeMessageError) as not_object:
        raise MalformedBridgeMessageError("x", [], code=REASON_NOT_A_JSON_OBJECT)
    assert not_object.value.code in BRIDGE_DLQ_REASONS

    with pytest.raises(MalformedBridgeMessageError) as bad_json:
        _deserialize_json_value(b"{nope")
    assert bad_json.value.code == REASON_INVALID_JSON
    assert {REASON_INVALID_JSON, REASON_NOT_A_JSON_OBJECT, REASON_MISSING_TYPE} == BRIDGE_DLQ_REASONS
