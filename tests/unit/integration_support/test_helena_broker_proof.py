"""Nao-vacuidade do leitor de fatos Kafka, sem engine nem broker substitutos."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.integration.agents.test_helena_escalation_broker import (
    _NOTIFICATIONS,
    _REQUESTED,
    _assert_broker_records,
)
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table


def _records(primary_failed: bool) -> tuple[list[SimpleNamespace], dict[str, Any]]:
    route = dict(
        evaluate(
            read_live_table(DMN_DIR / "escalation_routing.dmn"),
            {"motivo_categoria": "falha_tecnica", "severidade": None},
        ).saidas
    )
    requested = {
        "_business_key": "ESC-test-conversation",
        "_process_instance_id": "process-test",
        "_worker_topic": "operadora.events.publish",
        "tenant_id": "test",
        "conversation_id": "conversation",
        "source_agent_id": "helena",
        "motivo_categoria": "falha_tecnica",
        "severidade": None,
    }
    notification = {
        "type": "escalation.notify_team",
        "tenant_id": "test",
        "severidade": None,
        "grupo_atendimento": route["grupo_atendimento"],
        "prioridade": route["prioridade"],
        "motivo_categoria": "falha_tecnica",
    }
    if primary_failed:
        notification.update(
            type="escalation.notify_supervisor",
            alert_to="supervisaoAtendimento",
            motivo_alerta="notificacao_primaria_falhou",
        )
    return [
        SimpleNamespace(topic=topic, key=b"ESC-test-conversation", value=json.dumps(payload).encode())
        for topic, payload in [(_REQUESTED, requested), (_NOTIFICATIONS, notification)]
    ], route


def _check(records: list[SimpleNamespace], route: dict[str, Any], primary_failed: bool) -> None:
    _assert_broker_records(
        records,
        business_key="ESC-test-conversation",
        instance_id="process-test",
        tenant="test",
        conversation_id="conversation",
        route=route,
        primary_failed=primary_failed,
    )


@pytest.mark.parametrize("primary_failed", [False, True])
def test_accepts_complete_wire_facts(primary_failed: bool) -> None:
    records, route = _records(primary_failed)
    _check(records, route, primary_failed)


@pytest.mark.parametrize(
    "mutation",
    [
        "no_delivery",
        "duplicate_delivery",
        "wrong_key",
        "foreign_instance",
        "missing_null",
        "fabricated_leve",
        "wrong_group",
        "wrong_priority",
        "raw_narrative",
        "wrong_channel",
    ],
)
@pytest.mark.parametrize("primary_failed", [False, True])
def test_refuses_missing_or_corrupt_wire_facts(mutation: str, primary_failed: bool) -> None:
    records, route = _records(primary_failed)
    requested = json.loads(records[0].value)
    notification = json.loads(records[1].value)
    if mutation == "no_delivery":
        records.pop()
    elif mutation == "duplicate_delivery":
        records.append(copy.copy(records[-1]))
    elif mutation == "wrong_key":
        records[1].key = b"another-case"
    elif mutation == "foreign_instance":
        requested["_process_instance_id"] = "another-process"
    elif mutation == "missing_null":
        del notification["severidade"]
    elif mutation == "fabricated_leve":
        notification["severidade"] = "leve"
    elif mutation == "wrong_group":
        notification["grupo_atendimento"] = "grupo-de-outro-caso"
    elif mutation == "wrong_priority":
        notification["prioridade"] = "prioridade-de-outro-caso"
    elif mutation == "raw_narrative":
        notification["resumo_contexto"] = "canario-de-narrativa"
    elif mutation == "wrong_channel":
        notification["type"] = "escalation.notify_team" if primary_failed else "escalation.notify_supervisor"
    records[0].value = json.dumps(requested).encode()
    if len(records) > 1:
        records[1].value = json.dumps(notification).encode()
    with pytest.raises(AssertionError):
        _check(records, route, primary_failed)
