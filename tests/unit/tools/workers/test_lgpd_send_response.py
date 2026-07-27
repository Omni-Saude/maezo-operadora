"""Unit tests for `maezo.tools.workers.lgpd`'s `make_send_response_handler` (#55 R-F, T2.8) and
`make_request_additional_proof_handler` (#55 R-B — its fail-closed publish-posture pins live here
too; the R-B handler has no dedicated test module).

No engine — every test drives the handlers/`register_lgpd_workers` directly against
`ExternalTask` + `FakeKafkaPublisher` (or `kafka=None`), mirroring `test_events.py`'s
pattern for `make_publish_event_handler`. The real-engine acceptance tests live under
`tests/integration/processes/test_sp_op_lgpd_dsr_001.py` (currently `_gap_topic_stub`-served —
the 3 happy-path xfails there also need DPO-gated R-C/R-D before they can flip).
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import (
    _NOTIFICATIONS_TOPIC,
    _REQUEST_PROOF_NOTIFICATION_TYPE,
    _REQUEST_PROOF_TOPIC,
    _SEND_RESPONSE_NOTIFICATION_TYPE,
    _SEND_RESPONSE_TOPIC,
    make_request_additional_proof_handler,
    make_send_response_handler,
    register_lgpd_workers,
)

# `asyncio_mode = "auto"` (pyproject.toml) collects async def tests automatically.


def _task(
    *,
    task_id: str = "task-1",
    business_key: str = "DSR-amh-PSEUDO-001-confirmacao_acesso-2026-06-12",
    process_instance_id: str = "proc-1",
    variables: dict[str, Any] | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id=task_id,
        topic=_SEND_RESPONSE_TOPIC,
        process_instance_id=process_instance_id,
        business_key=business_key,
        worker_id="w-1",
        variables=variables or {},
    )


# ---------------------------------------------------------------------------
# Happy path — APROVAR_ENVIO / EXECUTAR_E_ENVIAR (no fundamentacao flag)
# ---------------------------------------------------------------------------


async def test_send_response_publishes_notification_aprovar_envio() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_send_response_handler(kafka)
    task = _task(
        variables={
            "tenant_id": "amh",
            "canal": "portal",
            "titular_pseudo_id": "PSEUDO-001",
            "tipo_requisicao": "confirmacao_acesso",
            "decisao_dsr": "APROVAR_ENVIO",
        }
    )

    result = await handler(task)

    assert result == {}
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == _NOTIFICATIONS_TOPIC
    assert key == task.business_key
    assert payload["type"] == _SEND_RESPONSE_NOTIFICATION_TYPE
    assert payload["tenant_id"] == "amh"
    assert payload["canal"] == "portal"
    assert payload["titular_pseudo_id"] == "PSEUDO-001"
    assert payload["tipo_requisicao"] == "confirmacao_acesso"
    assert payload["decisao_dsr"] == "APROVAR_ENVIO"
    assert "tem_fundamentacao" not in payload  # only present for NEGAR_FUNDAMENTADO
    # NON-HOLLOW (t2-notify-integrity item 1): forced propagate-on-failure — the titular's
    # legally mandated response dispatch must never be silently swallowed (terminal leg,
    # zero backstop).
    assert kafka.best_effort_calls == [False]


async def test_send_response_executar_e_enviar_has_no_fundamentacao_flag() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_send_response_handler(kafka)
    task = _task(variables={"decisao_dsr": "EXECUTAR_E_ENVIAR"})

    await handler(task)

    payload = kafka.published[0][1]
    assert payload["decisao_dsr"] == "EXECUTAR_E_ENVIAR"
    assert "tem_fundamentacao" not in payload


# ---------------------------------------------------------------------------
# NEGAR_FUNDAMENTADO — presence-only flag, NEVER the raw legal-justification text (no PHI/free
# text leaves this worker — mirrors #55 R-B's detalhes_requisicao exclusion, ADR-0006).
# ---------------------------------------------------------------------------


async def test_send_response_negar_fundamentado_includes_presence_flag_only() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_send_response_handler(kafka)
    task = _task(
        variables={
            "decisao_dsr": "NEGAR_FUNDAMENTADO",
            "fundamentacao_legal": "Retencao legal obrigatoria - Lei 13.787/2018 (texto livre)",
        }
    )

    await handler(task)

    payload = kafka.published[0][1]
    assert payload["tem_fundamentacao"] is True
    assert "fundamentacao_legal" not in payload
    assert "Retencao legal obrigatoria" not in repr(payload)


async def test_send_response_negar_fundamentado_missing_fundamentacao_flag_is_false() -> None:
    """Defense-in-depth: even though `GW_GuardFundamentacao` (engine-side, GAP-LGPD-3) should
    guarantee non-empty `fundamentacao_legal` on this branch, the worker never assumes it — it
    reflects whatever it actually received, never fabricating presence."""
    kafka = FakeKafkaPublisher()
    handler = make_send_response_handler(kafka)
    task = _task(variables={"decisao_dsr": "NEGAR_FUNDAMENTADO"})  # fundamentacao_legal absent

    await handler(task)

    payload = kafka.published[0][1]
    assert payload["tem_fundamentacao"] is False


# ---------------------------------------------------------------------------
# kafka=None — never blocks, never raises, never fabricates identity/response state
# ---------------------------------------------------------------------------


async def test_send_response_kafka_none_completes_without_publishing() -> None:
    handler = make_send_response_handler(None)
    task = _task(variables={"decisao_dsr": "APROVAR_ENVIO"})

    result = await handler(task)

    assert result == {}


async def test_send_response_kafka_none_never_raises_even_with_missing_vars() -> None:
    handler = make_send_response_handler(None)
    task = _task(variables={})  # no decisao_dsr / tenant_id / etc.

    # Must not raise (would trade "unregistered topic" incident for "crashing handler" incident).
    result = await handler(task)

    assert result == {}


# ---------------------------------------------------------------------------
# Missing input — defensive defaults, never crashes the dispatch worker
# ---------------------------------------------------------------------------


async def test_send_response_missing_decisao_dsr_still_publishes() -> None:
    """A missing `decisao_dsr` is a defensive-default case here (never crashes the worker) — the
    actual FAIL-CLOSED guard against a missing `decisao_dsr` lives at the ENGINE (`GW_DecisaoDsr`,
    GAP-LGPD-4), which never routes to `ST_EnviarResposta` without one of the three explicit
    values in the first place."""
    kafka = FakeKafkaPublisher()
    handler = make_send_response_handler(kafka)
    task = _task(variables={"tenant_id": "amh"})  # no decisao_dsr

    result = await handler(task)

    assert result == {}
    payload = kafka.published[0][1]
    assert payload["decisao_dsr"] == ""
    assert "tem_fundamentacao" not in payload


# ---------------------------------------------------------------------------
# Fail-safe on publish failure — propagates RAW (t2-notify-integrity item 1): no BPMN error
# boundary is declared on ST_EnviarResposta, so a broker failure rides the harness
# retry/incident ladder; the DSR must never reach End_RequisicaoConcluida with the response
# undelivered.
# ---------------------------------------------------------------------------


class _FailingPublisher:
    def __init__(self) -> None:
        self.best_effort_calls: list[bool | None] = []

    async def publish(
        self,
        topic: str,
        value: dict[str, Any],
        *,
        key: str | None = None,
        best_effort: bool | None = None,
    ) -> bool:
        del topic, value, key
        self.best_effort_calls.append(best_effort)
        raise RuntimeError("kafka unavailable (test)")


async def test_send_response_publish_failure_propagates_raw_exception() -> None:
    kafka = _FailingPublisher()
    handler = make_send_response_handler(kafka)
    task = _task(variables={"decisao_dsr": "APROVAR_ENVIO"})

    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)
    # NON-HOLLOW: the publish opts into propagate-on-failure so the best-effort-swallowing
    # producer cannot let the process reach a TERMINAL false success with the titular's
    # response never dispatched (LGPD Art. 18/19).
    assert kafka.best_effort_calls == [False]


# ---------------------------------------------------------------------------
# request_additional_proof (#55 R-B) — fail-closed publish posture (t2-notify-integrity item 1)
# ---------------------------------------------------------------------------


def _proof_task(*, variables: dict[str, Any] | None = None) -> ExternalTask:
    return ExternalTask(
        task_id="task-proof-1",
        topic=_REQUEST_PROOF_TOPIC,
        process_instance_id="proc-1",
        business_key="DSR-amh-PSEUDO-001-confirmacao_acesso-2026-06-12",
        worker_id="w-1",
        variables=variables or {},
    )


async def test_request_additional_proof_publishes_with_forced_propagate() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_request_additional_proof_handler(kafka)
    task = _proof_task(
        variables={
            "tenant_id": "amh",
            "canal": "portal",
            "titular_pseudo_id": "PSEUDO-001",
            "tipo_requisicao": "confirmacao_acesso",
        }
    )

    result = await handler(task)

    assert result == {}
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == _NOTIFICATIONS_TOPIC
    assert key == task.business_key
    assert payload["type"] == _REQUEST_PROOF_NOTIFICATION_TYPE
    assert payload["titular_pseudo_id"] == "PSEUDO-001"
    # NON-HOLLOW (t2-notify-integrity item 1): the challenge ask is this task's ONLY effect on
    # the MAIN path — a swallowed publish parks the DSR 10 days then kills it as
    # `expirada_identidade` with the titular never asked.
    assert kafka.best_effort_calls == [False]


async def test_request_additional_proof_publish_failure_propagates_raw_exception() -> None:
    kafka = _FailingPublisher()
    handler = make_request_additional_proof_handler(kafka)
    task = _proof_task(variables={"tenant_id": "amh", "canal": "portal"})

    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)
    # No BPMN boundary on ST_PedirProvaAdicional -> raw propagate (harness retry/incident,
    # ADR-0030), holding the token AT the task instead of the un-asked P10D death.
    assert kafka.best_effort_calls == [False]


async def test_request_additional_proof_kafka_none_still_completes() -> None:
    """kafka=None (no producer wired) is a DIFFERENT deployment reality from a broker failure —
    unchanged: completes loudly so the flow reaches GW_AguardarProva."""
    handler = make_request_additional_proof_handler(None)
    result = await handler(_proof_task(variables={"tenant_id": "amh"}))
    assert result == {}


# ---------------------------------------------------------------------------
# register_lgpd_workers wiring — raw handler, NOT in the WorkerRegistry
# ---------------------------------------------------------------------------


def test_register_lgpd_workers_registers_send_response_as_raw_handler() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_lgpd_workers(harness, FakeKafkaPublisher())

    assert _SEND_RESPONSE_TOPIC in harness.registered_topics
    assert harness.registry.get(_SEND_RESPONSE_TOPIC) is None


def test_register_lgpd_workers_send_response_defaults_kafka_to_none() -> None:
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_lgpd_workers(harness)  # kafka omitted — must not raise

    assert _SEND_RESPONSE_TOPIC in harness.registered_topics
