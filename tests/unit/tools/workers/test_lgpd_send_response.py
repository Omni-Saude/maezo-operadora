"""Unit tests for `maezo.tools.workers.lgpd`'s `make_send_response_handler` (#55 R-F, T2.8).

No engine — every test drives `make_send_response_handler`/`register_lgpd_workers` directly
against `ExternalTask` + `FakeKafkaPublisher` (or `kafka=None`), mirroring `test_events.py`'s
pattern for `make_publish_event_handler`. The real-engine acceptance tests live under
`tests/integration/processes/test_sp_op_lgpd_dsr_001.py` (currently `_gap_topic_stub`-served —
the 3 happy-path xfails there also need DPO-gated R-C/R-D before they can flip).
"""

from __future__ import annotations

from typing import Any

from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerHarness,
)
from maezo.tools.workers.lgpd import (
    _NOTIFICATIONS_TOPIC,
    _SEND_RESPONSE_NOTIFICATION_TYPE,
    _SEND_RESPONSE_TOPIC,
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
