"""Unit tests for SP-OP-ESCALATION-001 notify handlers — TDD London School.

DL-0034 (ratified by orchestrator 2026-07-26, built in t5): the escalation notify workers are RAW
ASYNC KAFKA HANDLERS (`make_notify_team_handler` / `make_notify_supervisor_handler`, registered via
`harness.register`), mirroring the #55 R-B precedent (`operadora.lgpd.request_additional_proof`) /
`events.py` — NOT the prior `WorkerBase` classes, which had no async Kafka seam and so NEVER
actually published (live-confirmed gap: `tests/integration/processes/test_sp_op_escalation_001.py`
`_NOTIFY_KAFKA_GAP_REASON`). These tests mirror `test_recurso.py`'s raw-handler tests:
publish-observed via `FakeKafkaPublisher`, and kafka=None completes anyway (never fabricates a
publish, never hangs the flow). Handlers must NEVER make adverse decisions — only notify.

GAP-ESC-SEVERITY-GROUP: these tests used to INJECT the English `severity` (the exact name the
worker read and nothing in the process ever set), which is what masked the defect — every case
"passed" while exercising a phantom variable. They now inject exactly what
`SP-OP-ESCALATION-001` puts on a notify task: the process variable `severidade`
(contract `docs/processes/contracts/SP-OP-ESCALATION-001.md:27`) and the `camunda:inputParameter`s
`grupo_atendimento`/`prioridade`, fed from `${roteamento.*}` — the `escalation_routing` DMN output
(BPMN `:96-99` on `ST_NotificarTime`, `:115-117` on `ST_NotificarFallback`, `:206-208` on
`ST_NotificarSupervisor`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pytest
import structlog

from maezo.tools.workers.escalation import (
    ESCALATION_BPMN_ERROR_ALLOWLIST,
    make_notify_supervisor_handler,
    make_notify_team_handler,
)
from maezo.tools.workers.harness import ExternalTask, FakeKafkaPublisher, WorkerBpmnError
from maezo.tools.workers.phi_vars import REDACTED_DIGITS, REDACTED_EMAIL, REDACTED_PHONE
from tests.support.dmn_first_hit import DMN_DIR, REPO_ROOT, read_live_table

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"
_ERR_ESC_NOTIFY_FAILED = "ERR_ESC_NOTIFY_FAILED"


class _FailingKafka:
    """A `KafkaPublisher` whose `publish` always raises — models a dead notification channel
    (ADR-0030 Tier-1 fault path). Mirrors the integration probe's `_FaultInjectingPublisher`
    but unconditional, for the unit-level raise assertion."""

    def __init__(self) -> None:
        self.attempts = 0
        self.best_effort_calls: list[bool | None] = []

    async def publish(
        self,
        topic: str,
        value: dict[str, Any],
        *,
        key: str | None = None,
        best_effort: bool | None = None,
    ) -> bool:
        self.attempts += 1
        self.best_effort_calls.append(best_effort)
        raise RuntimeError("canal de notificacao indisponivel (unit fault)")


def _task(
    *,
    topic: str = "operadora.escalation.notify_team",
    business_key: str = "ESC-amh-conv-1",
    variables: dict[str, Any] | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id="task-1",
        topic=topic,
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables=variables or {},
    )


def _team_vars(**overrides: Any) -> dict[str, Any]:
    """Exactly what the engine hands `ST_NotificarTime`: the process variables of the contract
    plus the four `camunda:inputParameter`s the BPMN declares at `:96-99`, for the canonical P1
    case (`motivo_categoria=red_flag_clinico` + `severidade=grave` -> `escalation_routing.dmn`
    rule `r1`: P1 / plantao-clinico / PT5M / PT30M)."""
    variables: dict[str, Any] = {
        "tenant_id": "amh",
        "source_agent_id": "helena",
        "conversation_id": "conv-1",
        "beneficiario_pseudo_id": "pseudo-abc123",
        "motivo_categoria": "red_flag_clinico",
        "severidade": "grave",
        # camunda:inputParameter <- ${roteamento.*} (DMN escalation_routing)
        "grupo_atendimento": "plantao-clinico",
        "prioridade": "P1",
        "sla_ack": "PT5M",
        "sla_resolucao": "PT30M",
    }
    variables.update(overrides)
    return variables


def _supervisor_vars(**overrides: Any) -> dict[str, Any]:
    """What the engine hands `ST_NotificarSupervisor` (BPMN `:206-208`): the same process
    variables plus `grupo_atendimento`/`prioridade` from the DMN and the literal
    `motivo=sla_ack_breached`. `ST_NotificarFallback` (`:115-117`) is the same minus `motivo`,
    plus `motivo_fallback=notificacao_primaria_falhou`."""
    variables = _team_vars(**overrides)
    variables.pop("sla_ack", None)
    variables.pop("sla_resolucao", None)
    variables.setdefault("motivo", "sla_ack_breached")
    return variables


# ---------------------------------------------------------------------------
# notify_team
# ---------------------------------------------------------------------------


async def test_notify_team_publishes_notification_and_returns_routing() -> None:
    """Happy path: emits the `escalation.notify_team` notification (observable via the probe's
    `notified_teams`) AND returns the routing metadata, in the CONTRACT's vocabulary."""
    kafka = FakeKafkaPublisher()
    handler = make_notify_team_handler(kafka)
    result = await handler(_task(variables=_team_vars()))

    assert result["status"] == "teams_notified"
    assert result["grupo_atendimento"] == "plantao-clinico"
    assert result["severidade"] == "grave"
    assert result["prioridade"] == "P1"
    assert result["event"] == "agents.events.escalation.requested"
    # The English keys the pre-fix worker emitted are GONE — they wrote a phantom `leve` /
    # `atendimento-humano` back into process scope on every single escalation.
    assert "severity" not in result
    assert "group" not in result

    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == _NOTIFICATIONS_TOPIC
    assert payload["type"] == "escalation.notify_team"
    assert payload["grupo_atendimento"] == "plantao-clinico"
    assert payload["severidade"] == "grave"
    assert payload["prioridade"] == "P1"
    assert payload["motivo_categoria"] == "red_flag_clinico"
    assert "severity" not in payload
    assert "group" not in payload
    assert key == "ESC-amh-conv-1"
    # NON-HOLLOW wiring: the notify publish MUST opt into propagate-on-failure so the real
    # best-effort-swallowing producer cannot silently drop a grave escalation notice.
    assert kafka.best_effort_calls == [False]


async def test_notify_team_grave_p1_flows_through_unchanged() -> None:
    """(a) GAP-ESC-SEVERITY-GROUP acceptance: `severidade=grave` + the DMN's `plantao-clinico`
    reach the clinical team VERBATIM. Under the pre-fix worker this exact task produced
    `severity=leve, group=atendimento-humano` — the P1 art. 35-C PT5M case announced as the
    mildest one, to the wrong queue."""
    kafka = FakeKafkaPublisher()
    result = await make_notify_team_handler(kafka)(_task(variables=_team_vars()))
    _topic, payload, _key = kafka.published[0]

    assert (result["severidade"], result["grupo_atendimento"]) == ("grave", "plantao-clinico")
    assert (payload["severidade"], payload["grupo_atendimento"]) == ("grave", "plantao-clinico")
    assert payload["severidade"] != "leve"
    assert payload["grupo_atendimento"] != "atendimento-humano"


@pytest.mark.parametrize(
    ("severidade", "grupo", "prioridade"),
    [
        ("grave", "plantao-clinico", "P1"),
        # DMN r2: risco_psicossocial is P1/plantao-clinico for ANY severidade — the deleted
        # private `_SEVERITY_TO_GROUP` would have sent `leve` here to `atendimento-humano`,
        # actively CONTRADICTING the DMN and the User Task's candidateGroups.
        ("leve", "plantao-clinico", "P1"),
        ("moderada", "enfermagem-triagem", "P2"),
        ("leve", "atendimento-humano", "P3"),
    ],
)
async def test_notify_team_never_re_derives_the_dmn_group(
    severidade: str, grupo: str, prioridade: str
) -> None:
    """(c) The notification can never contradict `grupo_atendimento`: the worker passes the DMN's
    output through for every (severidade, grupo) pair the DMN can actually emit, including the
    pairs a severidade->grupo table would get WRONG."""
    kafka = FakeKafkaPublisher()
    result = await make_notify_team_handler(kafka)(
        _task(variables=_team_vars(severidade=severidade, grupo_atendimento=grupo, prioridade=prioridade))
    )
    _topic, payload, _key = kafka.published[0]
    assert result["grupo_atendimento"] == grupo
    assert result["severidade"] == severidade
    assert payload["grupo_atendimento"] == grupo
    assert payload["severidade"] == severidade


async def test_notify_team_missing_severidade_fails_closed_never_defaults_to_leve() -> None:
    """(b) A missing clinical severity is UNKNOWN, never `leve`: refuse with the modeled
    `ERR_ESC_NOTIFY_FAILED` (fail-closed on the payload, fail-SAFE on the flow — the boundary
    routes to `ST_NotificarFallback` and still reaches the mandatory HITL) and publish NOTHING."""
    kafka = FakeKafkaPublisher()
    variables = _team_vars()
    del variables["severidade"]
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(kafka)(_task(variables=variables))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert "severidade" in str(excinfo.value)
    assert kafka.published == []  # no notification carrying a fabricated `leve`


@pytest.mark.parametrize("bruta", ["", "   ", "GRAVE", "critica", "high", None, 3])
async def test_notify_team_unusable_severidade_fails_closed(bruta: Any) -> None:
    """Blank, wrong-cased, out-of-domain and non-string `severidade` all refuse — the contract's
    domain is exactly {grave, moderada, leve} (`SP-OP-ESCALATION-001.md:27,:57`). None of them
    silently becomes the mildest value."""
    kafka = FakeKafkaPublisher()
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(kafka)(_task(variables=_team_vars(severidade=bruta)))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.published == []


async def test_notify_team_missing_grupo_atendimento_fails_closed() -> None:
    """No routing => no notification. The worker has no private table to fall back to any more,
    and inventing `atendimento-humano` would contradict `UT_TratarEscalonamento`'s
    `candidateGroups="${roteamento.grupo_atendimento}"` (BPMN `:134`)."""
    kafka = FakeKafkaPublisher()
    variables = _team_vars()
    del variables["grupo_atendimento"]
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(kafka)(_task(variables=variables))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert "grupo_atendimento" in str(excinfo.value)
    assert kafka.published == []


async def test_notify_team_grupo_outside_dmn_domain_fails_closed() -> None:
    """A group the `escalation_routing` DMN cannot emit is refused, not forwarded —
    `supervisao-atendimento` included (it is `UT_SupervisorAssume`'s group, contract `:72`, never
    a routing output)."""
    kafka = FakeKafkaPublisher()
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(kafka)(
            _task(variables=_team_vars(grupo_atendimento="supervisao-atendimento"))
        )
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.published == []


@pytest.mark.parametrize("prioridade", ["P9", "p1", "PRIORITARIO", "P9-LIVRE <script>"])
async def test_notify_team_prioridade_outside_domain_fails_closed(prioridade: str) -> None:
    """MINOR-2 (VERIFY-WP-ESC.md): `prioridade` is OPTIONAL but, when present, must be a non-blank
    string in the DMN's `{P1,P2,P3}` output domain. Before this fix `prioridade` was only
    `.strip()`-ed and forwarded verbatim — the gatekeeper's probe put `'P9-LIVRE <script>'`
    straight into the published notification through exactly this gap. (Blank/whitespace-only or
    non-string `prioridade` stays ABSENT — never "in" or "out of" domain — per `_rotulo_opcional`;
    covered by the omission tests, not here.)"""
    kafka = FakeKafkaPublisher()
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(kafka)(_task(variables=_team_vars(prioridade=prioridade)))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.published == []


async def test_notify_team_motivo_categoria_outside_domain_degrades_never_refuses() -> None:
    """ESC-D1-MOTIVO-STRICTER-THAN-R7: `motivo_categoria` is OPTIONAL, and a PRESENT out-of-domain
    value now DEGRADES (omitted from the notification, loudly logged) instead of refusing the
    whole notification — the `escalation_routing` DMN's own catch-all (`r7`) already tolerates an
    unknown motivo and safely routes the case (`grupo_atendimento`/`prioridade` here are the DMN's
    OWN output, read verbatim, so they are unaffected either way). Refusing the notification for a
    case the DMN had already safely routed was STRICTER than the routing authority itself (the
    residual D-1 of GAP-ESC-SEVERITY-GROUP, PR #272). The exact adversarial payload the gatekeeper
    probed with (a fake CPF riding inside a free-text-looking category) must still never reach the
    published notification — it is DROPPED, not refused, and never published verbatim either."""
    kafka = FakeKafkaPublisher()
    result = await make_notify_team_handler(kafka)(
        _task(variables=_team_vars(motivo_categoria="CPF 123.456.789-00 do Sr. Joao"))
    )
    assert result["status"] == "teams_notified"
    _topic, payload, _key = kafka.published[0]
    assert "motivo_categoria" not in result
    assert "motivo_categoria" not in payload
    assert "CPF 123.456.789-00" not in str(payload)
    # The DMN's OWN output is untouched by the degraded motivo — still routed correctly.
    assert payload["grupo_atendimento"] == "plantao-clinico"
    assert payload["severidade"] == "grave"


async def test_notify_team_tolerated_motivo_never_logs_the_rejected_value_raw(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gap `ESC-TOLERANT-LOG-RAW-VALUE`: a NOTIFICACAO ja omitia o valor rejeitado; o LOG nao.

    `_rotulo_opcional_tolerante` mandava `valor=<motivo_categoria bruto>` para as duas pernas de
    log (structlog e stdlib), e o valor rejeitado e — por construcao — um texto que a DMN nao
    reconhece, ou seja, texto livre vindo de cima. O gatekeeper R1 de VERIFY-R4-DEFAULTS provou o
    vazamento com a sua propria sonda (`valor='CPF 123.456.789-00 do Sr. Joao'`), agora numa via
    que COMPLETA com sucesso em vez de recusar. O valor passa por `phi_vars.redact_free_text`
    antes de qualquer log.

    LIMITE DECLARADO, nao vendido como mais do que e: `redact_free_text` e uma rede determinista
    sobre as familias de IDENTIFICADORES (e-mail, CPF/CNPJ, telefone BR, corridas de 11+ digitos).
    Um NOME proprio solto continua passando — e a limitacao documentada do helper em todo lugar
    onde ele e usado, nao uma regressao introduzida aqui. Por isso as afirmacoes abaixo sao sobre
    os identificadores, e nao sobre "nenhum PHI".
    """
    payload = "CPF 123.456.789-00, tel (11) 98888-7777, joao@example.com, cns 123456789012345"
    kafka = FakeKafkaPublisher()

    with (
        caplog.at_level(logging.WARNING, logger="maezo.tools.workers.escalation"),
        structlog.testing.capture_logs() as logs,
    ):
        result = await make_notify_team_handler(kafka)(_task(variables=_team_vars(motivo_categoria=payload)))

    assert result["status"] == "teams_notified"
    tolerados = [entry for entry in logs if entry["event"] == "escalation_rotulo_fora_do_dominio_tolerado"]
    assert len(tolerados) == 1
    tolerado = tolerados[0]

    # A linha continua diagnostica: campo, dominio e identidade do caso permanecem inteiros.
    assert tolerado["campo"] == "motivo_categoria"
    assert tolerado["business_key"] == "ESC-amh-conv-1"
    assert sorted(tolerado["dominio"]) == tolerado["dominio"]

    # ...e nenhum identificador da sonda sobrevive, em NENHUMA das duas pernas de log.
    for leak in ("123.456.789-00", "98888-7777", "joao@example.com", "123456789012345"):
        assert leak not in repr(logs), f"{leak!r} vazou no structlog"
        assert leak not in caplog.text, f"{leak!r} vazou no logger stdlib"
    assert REDACTED_DIGITS in tolerado["valor"]
    assert REDACTED_EMAIL in tolerado["valor"]
    assert REDACTED_PHONE in tolerado["valor"]

    # E a notificacao publicada segue limpa (a metade que ja estava correta antes deste gap).
    _topic, published, _key = kafka.published[0]
    assert "motivo_categoria" not in published
    assert "123.456.789-00" not in str(published)


async def test_notify_team_prioridade_outside_domain_still_fails_closed_unlike_motivo() -> None:
    """`prioridade` is the DMN's OWN output (not an upstream-supplied category): an out-of-domain
    value there means the ENGINE is corrupted, a different failure class from an unrecognized
    `motivo_categoria` — it must NOT be tolerated the same way `motivo_categoria` now is."""
    kafka = FakeKafkaPublisher()
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(kafka)(_task(variables=_team_vars(prioridade="P9-LIVRE <script>")))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.published == []


@pytest.mark.parametrize("alias", ["severity", "group", "priority"])
async def test_notify_team_rejects_english_alias_of_contract_variable(alias: str) -> None:
    """(d) The English key is never consumed — and its mere PRESENCE is refused, because it means
    either the pre-fix worker wrote it back into process scope or a caller is speaking a
    vocabulary the contract does not define. A clinical severity is never disambiguated by guess.
    Note the refusal holds even though the Portuguese variables here are all perfectly valid."""
    kafka = FakeKafkaPublisher()
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(kafka)(_task(variables=_team_vars(**{alias: "grave"})))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert alias in str(excinfo.value)
    assert kafka.published == []


async def test_notify_team_english_severity_alone_is_not_a_severidade() -> None:
    """The precise shape of the masked defect: a task carrying ONLY the English `severity=grave`
    (what every pre-fix unit test injected) has NO contractual severity at all. It refuses; it
    does not quietly route a P1 as `leve`."""
    kafka = FakeKafkaPublisher()
    variables = _team_vars()
    variables["severity"] = variables.pop("severidade")
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(kafka)(_task(variables=variables))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.published == []


async def test_notify_team_omits_motivo_instead_of_inventing_outro() -> None:
    """An absent `motivo_categoria` is OMITTED, never reported as the real domain value `outro`
    (`SP-OP-ESCALATION-001.md:26`) — the same fabrication class as the `leve` default."""
    kafka = FakeKafkaPublisher()
    variables = _team_vars()
    del variables["motivo_categoria"]
    result = await make_notify_team_handler(kafka)(_task(variables=variables))
    _topic, payload, _key = kafka.published[0]
    assert "motivo_categoria" not in result
    assert "motivo_categoria" not in payload


async def test_notify_team_never_decides_adverse_action() -> None:
    """ONLY routing info — never a decision about the case (L0 hard)."""
    kafka = FakeKafkaPublisher()
    result = await make_notify_team_handler(kafka)(_task(variables=_team_vars()))
    assert "decisao" not in result
    assert "resultado" not in result
    assert "notas_resolucao" not in result
    _topic, payload, _key = kafka.published[0]
    assert "decisao" not in payload
    assert "resultado" not in payload


async def test_notify_team_kafka_none_completes_without_publish() -> None:
    """kafka=None (no producer wired): completes anyway so the flow reaches UT_TratarEscalonamento
    (a notification gap must never HANG the escalation), never fabricates a publish. Unchanged by
    GAP-ESC-SEVERITY-GROUP: a PRODUCER gap still completes; only unusable INPUT fails closed.
    MINOR-2/MINOR-3 (VERIFY-WP-ESC.md): the returned `status` is now HONEST about the zero
    publishes — `teams_notified` is reserved for an actual publish attempt that succeeded."""
    result = await make_notify_team_handler(None)(_task(variables=_team_vars()))
    assert result["status"] == "teams_notification_skipped_no_producer"
    assert result["grupo_atendimento"] == "plantao-clinico"
    assert result["severidade"] == "grave"


async def test_notify_team_kafka_none_still_fails_closed_on_missing_severidade() -> None:
    """The input refusal precedes the producer check: with kafka=None AND no `severidade`, the
    worker must NOT complete with fabricated routing just because there is nothing to publish —
    the completion payload itself would poison process scope."""
    variables = _team_vars()
    del variables["severidade"]
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_team_handler(None)(_task(variables=variables))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED


async def test_notify_team_publish_failure_raises_bpmn_error() -> None:
    """ADR-0030 Tier-1: a real publish ATTEMPT that FAILS raises the MODELED
    `WorkerBpmnError(ERR_ESC_NOTIFY_FAILED)` (not the raw exception) so `BE_FalhaNotificacao` fires
    and routes to the supervisor fallback + the mandatory HITL — never a silent drop / stalling
    incident. Contrast kafka=None (no attempt), which completes."""
    kafka = _FailingKafka()
    handler = make_notify_team_handler(kafka)
    with pytest.raises(WorkerBpmnError) as excinfo:
        await handler(_task(variables=_team_vars()))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.attempts == 1  # it DID attempt the publish (not a kafka=None short-circuit)
    assert kafka.best_effort_calls == [False]  # forced propagate — the non-hollow contract


# ---------------------------------------------------------------------------
# notify_supervisor (serves ST_NotificarSupervisor + ST_NotificarFallback)
# ---------------------------------------------------------------------------


async def test_notify_supervisor_publishes_notification_on_sla_breach() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_supervisor_handler(kafka)
    result = await handler(
        _task(topic="operadora.escalation.notify_supervisor", variables=_supervisor_vars())
    )

    assert result["status"] == "supervisor_notified"
    assert result["severidade"] == "grave"
    assert result["grupo_atendimento"] == "plantao-clinico"
    assert result["alert_to"] == "supervisao-atendimento"
    assert result["require_human_resolution"] is True
    # `sla_status` is GONE: no BPMN task ever set it, so it reported the literal "unknown" on
    # every supervisor alert. The BPMN's own `motivo` (`:208`) replaces it.
    assert "sla_status" not in result
    assert result["motivo_alerta"] == "sla_ack_breached"

    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == _NOTIFICATIONS_TOPIC
    assert payload["type"] == "escalation.notify_supervisor"
    assert payload["severidade"] == "grave"
    assert payload["grupo_atendimento"] == "plantao-clinico"
    assert payload["motivo_alerta"] == "sla_ack_breached"
    assert "severity" not in payload
    assert "sla_status" not in payload
    assert key == "ESC-amh-conv-1"
    assert kafka.best_effort_calls == [False]  # NON-HOLLOW: forced propagate-on-failure


async def test_notify_supervisor_reads_fallback_motivo_from_bpmn() -> None:
    """`ST_NotificarFallback` (BPMN `:115-117`) sets `motivo_fallback`, not `motivo` — the
    supervisor alert must say WHY it was raised in both of the two tasks this one handler serves."""
    kafka = FakeKafkaPublisher()
    variables = _supervisor_vars()
    del variables["motivo"]
    variables["motivo_fallback"] = "notificacao_primaria_falhou"
    result = await make_notify_supervisor_handler(kafka)(
        _task(topic="operadora.escalation.notify_supervisor", variables=variables)
    )
    _topic, payload, _key = kafka.published[0]
    assert result["motivo_alerta"] == "notificacao_primaria_falhou"
    assert payload["motivo_alerta"] == "notificacao_primaria_falhou"


async def test_notify_supervisor_grave_severity_reaches_supervisor_verbatim() -> None:
    """(a)/(c) for the supervisor leg: the DMN's group and the contract's severidade flow through
    unchanged. Pre-fix, an SLA-breached P1 reached the supervisor as `severity=leve`."""
    kafka = FakeKafkaPublisher()
    await make_notify_supervisor_handler(kafka)(
        _task(topic="operadora.escalation.notify_supervisor", variables=_supervisor_vars())
    )
    _topic, payload, _key = kafka.published[0]
    assert payload["severidade"] == "grave"
    assert payload["severidade"] != "leve"
    assert payload["grupo_atendimento"] == "plantao-clinico"


async def test_notify_supervisor_missing_severidade_fails_closed() -> None:
    """(b) for the supervisor leg. Fail-SAFE on the flow: `BE_NotifSupervisorFailed` ->
    `End_SupervisorAlertado` on the non-interruptive ack branch (the main case stays open in
    `UT_TratarEscalonamento`, and `ST_PublishAckBreach` has ALREADY published the breach event),
    `BE_NotifFallbackFailed` -> `UT_TratarEscalonamento` on the fallback branch."""
    kafka = FakeKafkaPublisher()
    variables = _supervisor_vars()
    del variables["severidade"]
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_supervisor_handler(kafka)(
            _task(topic="operadora.escalation.notify_supervisor", variables=variables)
        )
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.published == []


@pytest.mark.parametrize("prioridade", ["P9", "p1", "P9-LIVRE <script>"])
async def test_notify_supervisor_prioridade_outside_domain_fails_closed(prioridade: str) -> None:
    """MINOR-2 (VERIFY-WP-ESC.md), supervisor leg: same closed-domain check as notify_team's
    `prioridade` — the supervisor alert must not carry an unbounded string either."""
    kafka = FakeKafkaPublisher()
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_supervisor_handler(kafka)(
            _task(
                topic="operadora.escalation.notify_supervisor",
                variables=_supervisor_vars(prioridade=prioridade),
            )
        )
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.published == []


@pytest.mark.parametrize("alias", ["severity", "group", "priority"])
async def test_notify_supervisor_rejects_english_alias(alias: str) -> None:
    """(d) for the supervisor leg — same refusal, same reason."""
    kafka = FakeKafkaPublisher()
    with pytest.raises(WorkerBpmnError) as excinfo:
        await make_notify_supervisor_handler(kafka)(
            _task(
                topic="operadora.escalation.notify_supervisor",
                variables=_supervisor_vars(**{alias: "grave"}),
            )
        )
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.published == []


async def test_notify_supervisor_no_human_decision() -> None:
    kafka = FakeKafkaPublisher()
    result = await make_notify_supervisor_handler(kafka)(
        _task(topic="operadora.escalation.notify_supervisor", variables=_supervisor_vars())
    )
    assert "decision" not in result
    assert "approve" not in str(result).lower()
    assert "deny" not in str(result).lower()


async def test_notify_supervisor_kafka_none_completes_without_publish() -> None:
    """MINOR-2/MINOR-3 (VERIFY-WP-ESC.md): honest `status` for zero publishes — see the notify_team
    sibling test for the full rationale (no BPMN element reads this worker's `status`)."""
    result = await make_notify_supervisor_handler(None)(
        _task(topic="operadora.escalation.notify_supervisor", variables=_supervisor_vars())
    )
    assert result["status"] == "supervisor_notification_skipped_no_producer"
    assert result["severidade"] == "grave"


async def test_notify_supervisor_publish_failure_raises_bpmn_error() -> None:
    """ADR-0030 Tier-1: notify_supervisor serves BOTH ST_NotificarSupervisor and ST_NotificarFallback.
    A publish ATTEMPT that FAILS raises `WorkerBpmnError(ERR_ESC_NOTIFY_FAILED)` so
    `BE_NotifFallbackFailed` (double-channel failure) / `BE_NotifSupervisorFailed` (SLA branch) can
    fire — the HITL/close is never lost to a channel glitch."""
    kafka = _FailingKafka()
    handler = make_notify_supervisor_handler(kafka)
    with pytest.raises(WorkerBpmnError) as excinfo:
        await handler(_task(topic="operadora.escalation.notify_supervisor", variables=_supervisor_vars()))
    assert excinfo.value.error_code == _ERR_ESC_NOTIFY_FAILED
    assert kafka.attempts == 1
    assert kafka.best_effort_calls == [False]  # forced propagate — the non-hollow contract


def test_escalation_bpmn_error_allowlist_is_exactly_notify_failed() -> None:
    """The module allowlist constant (unioned into PRODUCTION_BPMN_ERROR_ALLOWLIST) is exactly the
    one gate-proven, non-T-E-gated notify fail-safe code — the value the boundary-proof gate
    computes as consumption-covered for this family. GAP-ESC-SEVERITY-GROUP's fail-closed path
    REUSES this code deliberately: no new error code, so the gate's coverage is unchanged."""
    assert frozenset({_ERR_ESC_NOTIFY_FAILED}) == ESCALATION_BPMN_ERROR_ALLOWLIST


def test_no_private_severity_to_group_table_survives() -> None:
    """Regression pin for the root cause: routing belongs to `escalation_routing.dmn` (ADR-0012/
    ADR-0028), evaluated by `BRT_RotearEscalonamento`. A worker-side severidade->grupo map is a
    SECOND, competing source of truth — it is what shipped `atendimento-humano` for a P1. It must
    not come back under any name."""
    from maezo.tools.workers import escalation as mod

    assert not hasattr(mod, "_SEVERITY_TO_GROUP")
    assert not hasattr(mod, "_DEFAULT_GROUP")
    assert not hasattr(mod, "_resolve_group")
    # What survives is a closed DOMAIN used to fail closed — never a mapping.
    assert isinstance(mod._GRUPOS_ATENDIMENTO_DMN, frozenset)
    assert isinstance(mod._SEVERIDADES_CONTRATUAIS, frozenset)
    assert isinstance(mod._PRIORIDADES_DMN, frozenset)
    assert isinstance(mod._MOTIVOS_CONTRATUAIS, frozenset)


_CONTRACT_PATH = REPO_ROOT / "docs" / "processes" / "contracts" / "SP-OP-ESCALATION-001.md"


def _contract_domain(text: str, *, line_prefix: str) -> frozenset[str]:
    """Extract the backtick-quoted domain tokens from the ONE contract line starting with
    `line_prefix` (MAJOR-1, VERIFY-WP-ESC.md's derivation step): the domain comes from PARSING the
    contract's own markdown table, never from retyping it by hand here. Raises (via the assert) on
    zero or more-than-one match, and on a matched line with no backtick-quoted tokens at all — a
    silent empty domain must never read as "matches"."""
    matches = [line for line in text.splitlines() if line.startswith(line_prefix)]
    assert len(matches) == 1, (
        f"expected exactly one contract line starting with {line_prefix!r}, found {len(matches)}"
    )
    tokens = frozenset(re.findall(r"`([^`]+)`", matches[0][len(line_prefix) :]))
    assert tokens, f"no backtick-quoted domain tokens found after {line_prefix!r}"
    return tokens


def test_contract_domains_match_the_artifacts() -> None:
    """MAJOR-1 (VERIFY-WP-ESC.md): the four validator domains are DERIVED here by PARSING the
    artifacts — never hand-typed alone against `escalation.py`'s constants — so any DMN or
    contract edit turns this test red before the two could silently diverge (the prior version of
    this test compared two hand-written literal sets and its own docstring's "read off the
    artifacts" claim was not yet true; it is now).

    `grupo_atendimento`/`prioridade` come from `escalation_routing.dmn`'s `out_grupo`/
    `out_prioridade` output columns, read via `tests.support.dmn_first_hit.read_live_table` — the
    repo's existing live-DMN-XML reader (`tests/unit/spec/test_glosa_triage_shadow_candidate.py`
    and its siblings already derive a domain from a live table the SAME way; no new spec-loading
    mechanism is introduced, matching VERIFY-WP-ESC.md's requirement not to invent one). `severidade`
    has no DMN OUTPUT column of its own (it is a DMN INPUT, matched mostly by the `-` wildcard —
    only rule `r1` pins a literal), so its domain — and the OPTIONAL `motivo_categoria`'s, which the
    routing DMN consumes upstream of these notify tasks — are parsed off the CONTRACT's own markdown
    tables instead (`SP-OP-ESCALATION-001.md:26,:57`). `supervisao-atendimento` (contract `:72`) is
    asserted absent from the DMN's group domain: it is `UT_SupervisorAssume`'s alert TARGET, never a
    routing output.
    """
    from maezo.tools.workers import escalation as mod

    table = read_live_table(DMN_DIR / "escalation_routing.dmn")
    grupo_idx = table.output_names.index("grupo_atendimento")
    prioridade_idx = table.output_names.index("prioridade")
    grupos_dmn = frozenset(rule.outputs[grupo_idx] for rule in table.rules)
    prioridades_dmn = frozenset(rule.outputs[prioridade_idx] for rule in table.rules)

    # Non-vacuity: a parse regression that silently returned nothing must not read as "matches".
    assert len(grupos_dmn) == 3
    assert len(prioridades_dmn) == 3

    assert grupos_dmn == mod._GRUPOS_ATENDIMENTO_DMN
    assert prioridades_dmn == mod._PRIORIDADES_DMN
    assert "supervisao-atendimento" not in grupos_dmn

    contract_text = _CONTRACT_PATH.read_text(encoding="utf-8")
    severidade_contrato = _contract_domain(contract_text, line_prefix="| in | `severidade` | string |")
    prioridade_contrato = _contract_domain(contract_text, line_prefix="| out | `prioridade` | string |")
    grupo_contrato = _contract_domain(contract_text, line_prefix="| out | `grupo_atendimento` | string |")
    motivo_contrato = _contract_domain(contract_text, line_prefix="| `motivo_categoria` | string | sim |")

    assert len(severidade_contrato) == 3
    assert len(motivo_contrato) == 6

    assert severidade_contrato == mod._SEVERIDADES_CONTRATUAIS
    assert prioridade_contrato == prioridades_dmn  # contract's DMN-reference table agrees w/ DMN
    assert grupo_contrato == grupos_dmn  # idem
    assert motivo_contrato == mod._MOTIVOS_CONTRATUAIS


# ---------------------------------------------------------------------------
# registration (DL-0034: raw handlers, not WorkerRegistry entries)
# ---------------------------------------------------------------------------


def test_register_escalation_workers_registers_both_topics_as_raw_handlers() -> None:
    from maezo.tools.workers.escalation import register_escalation_workers
    from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_escalation_workers(harness, FakeKafkaPublisher())
    topics = set(harness.registered_topics)
    for topic in ("operadora.escalation.notify_team", "operadora.escalation.notify_supervisor"):
        assert topic in topics, f"{topic} not registered"
        # DL-0034: raw handlers populate _handlers but NOT the WorkerRegistry.
        assert harness.registry.get(topic) is None
