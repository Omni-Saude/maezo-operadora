"""WP-J1-09 — AUTH's SLA-risk alert becomes a REAL channel (owner decision #17, RATIFIED).

The gap these tests close, stated as the reviewer should check it: `auth.NotifySlaRiskWorker` was
the ONE domain the bridge's own spec table named as carrying an SLA-risk step with NO publisher —
a synchronous `WorkerBase` with no Kafka seam, so `BT_AlertaSla` fired, the step logged, and
nobody was ever told. `make_notify_sla_risk_handler` publishes `auth.notify_sla_risk` onto the
channel the bridge already consumes, from which it derives `ESC-{tenant}-sla-auth-{guia}`.

No engine: every test drives the handler / `register_auth_workers` directly against
`ExternalTask` + `FakeKafkaPublisher` (or `kafka=None`), mirroring `test_lgpd_notify_sla_risk.py`.
The live proof — a real ESCALATION instance visible in `/tasks` after the AUTH timer fires — is an
engine test, listed for the runner and NOT simulated here.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.platform.notification_bridge import (
    PROCESS_KEY_ESCALATION,
    SLA_ALERT_DOMAINS,
    _SLA_ALERT_SPECS,
    _sla_alert_predicate,
    _sla_alert_variables,
)
from maezo.tools.workers.auth import (
    _NOTIFICATIONS_TOPIC,
    _NOTIFY_SLA_RISK_NOTIFICATION_TYPE,
    _NOTIFY_SLA_RISK_TOPIC,
    NotifySlaRiskWorker,
    make_notify_sla_risk_handler,
    register_auth_workers,
)
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerHarness,
)


def _task(
    *,
    business_key: str = "AUTH-amh-GUIA-7",
    variables: dict[str, Any] | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id="task-1",
        topic=_NOTIFY_SLA_RISK_TOPIC,
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables=variables
        if variables is not None
        else {
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-7",
            "beneficiario_pseudo_id": "PSEUDO-7",
            "sla_percent": 75,
            "sla_analise": "PT2H",
        },
    )


# ---------------------------------------------------------------------------
# The publish — the whole point of the work package
# ---------------------------------------------------------------------------


async def test_o_alerta_de_sla_de_auth_agora_e_realmente_publicado() -> None:
    """Before WP-J1-09 this channel did not exist: the step logged and nobody was told."""
    kafka = FakeKafkaPublisher()

    result = await make_notify_sla_risk_handler(kafka)(_task())

    assert len(kafka.published) == 1
    topic, payload, _key = kafka.published[0]
    assert topic == _NOTIFICATIONS_TOPIC
    assert payload["type"] == _NOTIFY_SLA_RISK_NOTIFICATION_TYPE == "auth.notify_sla_risk"
    assert payload["tenant_id"] == "amh"
    assert payload["numero_guia_tiss"] == "GUIA-7"
    assert payload["beneficiario_pseudo_id"] == "PSEUDO-7"
    # FAB-SLA-RISK-NOTIFIED-SLICE4 holds: the publish is a REQUEST for an alert, never proof a
    # human was told, so no process variable is written and nothing is asserted.
    assert result == {}


async def test_o_publish_propaga_falha_de_broker_em_vez_de_engoli_la() -> None:
    """`best_effort=False`. The publish is this step's ONLY effect.

    `operadora.notifications.internal` is in the producer's BEST_EFFORT_TOPICS, so the DEFAULT
    posture would swallow a broker-down failure one layer below while the handler reported
    success — the hollow-notification species this repo has fixed three times already.
    """
    kafka = FakeKafkaPublisher()

    await make_notify_sla_risk_handler(kafka)(_task())

    assert kafka.best_effort_calls == [False]


async def test_a_chave_de_particao_vem_da_cadeia_compartilhada_nao_de_or_none() -> None:
    """GAP-SC-04-a: a blank business key must not degrade into an UNKEYED publish.

    An unkeyed publish round-robins across the topic's partitions, so two notifications about the
    same guia could reorder once the consumer is scaled past one replica.
    """
    kafka = FakeKafkaPublisher()

    await make_notify_sla_risk_handler(kafka)(_task())
    _topic, _payload, key = kafka.published[0]

    assert key is not None and key != ""


async def test_sem_produtor_o_alerta_nao_e_fabricado_e_a_etapa_ainda_completa() -> None:
    """`kafka=None`: log loudly, publish nothing, assert nothing — and STILL complete.

    Completing is load-bearing: the alert branch must reach `End_RiscoSlaNotificado`. Returning a
    notification status on the exact path where nothing was published is the fabrication
    FAB-SLA-RISK-NOTIFIED-SLICE4 removed, so both paths return `{}`.
    """
    assert await make_notify_sla_risk_handler(None)(_task()) == {}


async def test_o_handler_executa_a_etapa_pura_e_nao_escreve_variavel_de_processo() -> None:
    """The class survives as the pure step and is CALLED by the handler — its `{}` contract with
    the process scope is unchanged, including on the no-producer path."""
    assert NotifySlaRiskWorker().execute({"tenant_id": "amh"}) == {}
    assert await make_notify_sla_risk_handler(FakeKafkaPublisher())(_task()) == {}


async def test_nenhum_texto_livre_clinico_atravessa_a_fronteira() -> None:
    """The payload is a CLOSED set of business anchors. A clinical free-text variable in process
    scope must not ride along — the human-readable context is a per-spec CONSTANT on the bridge
    side, never bytes from this message."""
    kafka = FakeKafkaPublisher()
    task = _task(
        variables={
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-7",
            "beneficiario_pseudo_id": "PSEUDO-7",
            "justificativa_clinica": "NAO DEVE VAZAR",
            "cid10_referencia": "NAO DEVE VAZAR",
        }
    )

    await make_notify_sla_risk_handler(kafka)(task)
    _topic, payload, _key = kafka.published[0]

    assert set(payload) == {"type", "tenant_id", "numero_guia_tiss", "beneficiario_pseudo_id"}
    assert "NAO DEVE VAZAR" not in str(payload)


# ---------------------------------------------------------------------------
# Registration — exactly one handler on the topic
# ---------------------------------------------------------------------------


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="wp-j1-09-probe")


def test_o_topico_e_servido_pelo_handler_cru_e_nao_pela_classe() -> None:
    """Topic exclusivity. The class is no longer registered — it is called BY the handler — so
    the topic keeps exactly one server and the publish cannot be bypassed."""
    harness = _harness()
    register_auth_workers(harness, FakeKafkaPublisher())

    assert _NOTIFY_SLA_RISK_TOPIC in harness.registered_topics
    assert harness.registry.get(_NOTIFY_SLA_RISK_TOPIC) is None


async def test_o_kafka_da_bootstrap_chega_mesmo_ao_handler() -> None:
    """`register_auth_workers` used to `del kafka  # unused`. If the seam were dropped again the
    daemon would run permanently on the no-producer path and nobody would notice: this drives the
    REGISTERED handler and demands a real publish."""
    harness = _harness()
    kafka = FakeKafkaPublisher()
    register_auth_workers(harness, kafka)

    await harness._handlers[_NOTIFY_SLA_RISK_TOPIC](_task())

    assert len(kafka.published) == 1


# ---------------------------------------------------------------------------
# End-to-end derivation: the published payload really mints the RATIFIED key
# ---------------------------------------------------------------------------


async def test_o_payload_publicado_satisfaz_a_regra_do_bridge_e_gera_a_chave_ratificada() -> None:
    """The seam that decision #17 actually ratified, proven across the two modules.

    Rather than restating the key as a literal on both sides, this feeds the payload the AUTH
    handler REALLY publishes into the bridge rule that consumes it — so a change to either side
    that breaks the handoff fails here.
    """
    kafka = FakeKafkaPublisher()
    await make_notify_sla_risk_handler(kafka)(_task())
    _topic, payload, _key = kafka.published[0]

    spec = next(s for s in _SLA_ALERT_SPECS if s.domain == "auth")
    assert SLA_ALERT_DOMAINS[payload["type"]] == "auth"
    assert _sla_alert_predicate(spec)(dict(payload)) is True

    variables = _sla_alert_variables(spec)(dict(payload))
    assert variables["business_key"] == "ESC-amh-sla-auth-GUIA-7"
    assert variables["conversation_id"] == "sla-auth-GUIA-7"
    assert variables["beneficiario_pseudo_id"] == "PSEUDO-7"
    assert PROCESS_KEY_ESCALATION == "SP-OP-ESCALATION-001"


@pytest.mark.parametrize("faltando", ["tenant_id", "numero_guia_tiss"])
async def test_sem_ancora_a_regra_fica_dormente_em_vez_de_cunhar_chave_degenerada(
    faltando: str,
) -> None:
    """FAIL-CLOSED on the consumer side: a missing anchor must leave the rule DORMANT.

    A degenerate `ESC-amh-sla-auth-` would collapse every guia of the tenant onto ONE escalation,
    and because the start is idempotent by business key the second case would open NO human task
    at all — the silent loss this whole handoff exists to end.
    """
    kafka = FakeKafkaPublisher()
    variables = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-7",
        "beneficiario_pseudo_id": "PSEUDO-7",
    }
    del variables[faltando]
    await make_notify_sla_risk_handler(kafka)(_task(variables=variables))
    _topic, payload, _key = kafka.published[0]

    spec = next(s for s in _SLA_ALERT_SPECS if s.domain == "auth")
    assert _sla_alert_predicate(spec)(dict(payload)) is False
