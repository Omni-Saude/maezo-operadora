"""R-085 (owner 2026-10-31, #1084 Rodada 2): the engine-history L1 human-decision poller.

Behavioural tests against a scripted history reader (unit scope — the engine's own history shape
was measured on the local CIB Seven 2.1.0 and is reproduced here verbatim in field names), plus
the spec-trace fences: the closed task table must equal what the BPMN declares and what L0-core
classifies, so the table can never invent a vocabulary or an action class.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
import yaml

from maezo.platform.observability import get_metrics_collector
from maezo.platform.xml_safe import parse_xml_without_dtd
from maezo.runtime.worker_runtime.engine_metrics import (
    DEFAULT_PROCESS_KEYS,
    L1_DECISION_TASKS,
    EngineMetricsPoller,
    format_engine_date,
    label_sets,
)
from maezo.tools.workers.harness import HISTORY_READ_PATHS, CibSevenWorkerTransport

_REPO: Final[Path] = Path(__file__).resolve().parents[3]
_BPMN_DIR: Final[Path] = _REPO / "spec" / "processes" / "bpmn"
_NS: Final[str] = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
_T0: Final[datetime] = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)
_TENANT: Final[str] = "t-r085"


def _sample(**labels: str) -> float:
    value = get_metrics_collector().registry.get_sample_value("maezo_l1_decision_total", labels)
    return float(value or 0.0)


def _poll_sample(outcome: str) -> float:
    value = get_metrics_collector().registry.get_sample_value(
        "maezo_engine_metrics_poll_total", {"outcome": outcome}
    )
    return float(value or 0.0)


def _task(
    tid: str,
    *,
    end: datetime,
    process: str = "SP-OP-PAGTO-001",
    key: str = "UT_AprovacaoAlcada",
    reason: str = "completed",
) -> dict[str, Any]:
    return {
        "id": tid,
        "processDefinitionKey": process,
        "taskDefinitionKey": key,
        "processInstanceId": f"pi-{tid}",
        "activityInstanceId": f"{key}:ai-{tid}",
        "deleteReason": reason,
        "endTime": format_engine_date(end),
        # Free text the poller must never read or surface (engines put raw control chars here).
        "description": "texto livre\x01 que nunca vira rotulo",
    }


class _History:
    """Scripted engine history. `decisions[task_id] = value` for the task's own decision field."""

    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.decisions: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail_detail_for: set[str] = set()

    async def read_history(self, path: str, params: Mapping[str, str]) -> Any:
        assert path in HISTORY_READ_PATHS, path
        self.calls.append((path, dict(params)))
        if path == "/history/task":
            after = datetime.strptime(params["finishedAfter"], "%Y-%m-%dT%H:%M:%S.%f%z")
            keys = set(params["taskDefinitionKeyIn"].split(","))
            rows = [
                t
                for t in self.tasks
                if t["taskDefinitionKey"] in keys
                and datetime.strptime(t["endTime"], "%Y-%m-%dT%H:%M:%S.%f%z") > after
            ]
            rows.sort(key=lambda t: t["endTime"])
            first = int(params.get("firstResult", "0"))
            return rows[first : first + int(params["maxResults"])]
        if path == "/history/variable-instance":
            pid = params["processInstanceId"]
            tid = pid.removeprefix("pi-")
            if tid not in self.decisions:
                return []
            return [{"id": f"vi-{tid}", "name": params["variableName"]}]
        tid = params["variableInstanceId"].removeprefix("vi-")
        if tid in self.fail_detail_for:
            raise httpx.ConnectError("engine down")
        task = next(t for t in self.tasks if t["id"] == tid)
        assert params["activityInstanceId"] == task["activityInstanceId"]
        spec = next(s for s in L1_DECISION_TASKS if s.task_key == task["taskDefinitionKey"])
        return [
            {
                "variableName": spec.decision_variable,
                "value": self.decisions[tid],
                "time": task["endTime"],
                "activityInstanceId": task["activityInstanceId"],
            }
        ]


def _poller(history: _History, *, page_size: int = 100) -> EngineMetricsPoller:
    return EngineMetricsPoller(history, tenant=_TENANT, interval_s=60, page_size=page_size, clock=lambda: _T0)


_PAGTO: Final[dict[str, str]] = {"tenant": _TENANT, "agent_id": "andre", "action_class": "high_value_payment"}


async def test_counts_each_finished_l1_task_once_by_its_declared_decision_token() -> None:
    history = _History()
    history.tasks.append(_task("a1", end=_T0 + timedelta(seconds=5)))
    history.decisions["a1"] = "APROVAR"
    poller = _poller(history)
    before = _sample(**_PAGTO, decisao="APROVAR")

    assert await poller.poll_once() == 1
    assert _sample(**_PAGTO, decisao="APROVAR") == before + 1
    # Second round: same history, nothing new — never a double count.
    assert await poller.poll_once() == 0
    assert _sample(**_PAGTO, decisao="APROVAR") == before + 1


async def test_tasks_finished_before_boot_are_not_counted() -> None:
    history = _History()
    history.tasks.append(_task("old", end=_T0 - timedelta(seconds=1)))
    history.decisions["old"] = "APROVAR"
    assert await _poller(history).poll_once() == 0


async def test_a_task_closed_without_completion_is_sem_decisao_and_reads_no_variable() -> None:
    history = _History()
    history.tasks.append(_task("d1", end=_T0 + timedelta(seconds=1), reason="deleted"))
    before = _sample(**_PAGTO, decisao="sem_decisao")
    assert await _poller(history).poll_once() == 1
    assert _sample(**_PAGTO, decisao="sem_decisao") == before + 1
    assert [p for p, _ in history.calls] == ["/history/task"]


async def test_a_value_outside_the_vocabulary_is_desconhecido_never_the_raw_value() -> None:
    history = _History()
    history.tasks.append(_task("x1", end=_T0 + timedelta(seconds=1)))
    history.decisions["x1"] = "aprovado pelo fulano, CPF 123"
    before = _sample(**_PAGTO, decisao="desconhecido")
    assert await _poller(history).poll_once() == 1
    assert _sample(**_PAGTO, decisao="desconhecido") == before + 1
    exposed = get_metrics_collector().registry.collect()
    labels = {
        v
        for metric in exposed
        if metric.name == "maezo_l1_decision"
        for s in metric.samples
        for v in s.labels.values()
    }
    assert not any("CPF" in v or "fulano" in v for v in labels)


async def test_nip_manter_negativa_is_counted_under_its_own_l0_hard_class() -> None:
    history = _History()
    history.tasks.append(
        _task("n1", end=_T0 + timedelta(seconds=1), process="SP-OP-NIP-001", key="UT_RevisaoJuridicaNip")
    )
    history.decisions["n1"] = "MANTER_NEGATIVA"
    labels = {"tenant": _TENANT, "agent_id": "gustavo", "action_class": "nip_manter_negativa"}
    before = _sample(**labels, decisao="MANTER_NEGATIVA")
    assert await _poller(history).poll_once() == 1
    assert _sample(**labels, decisao="MANTER_NEGATIVA") == before + 1


async def test_only_the_named_decision_variable_is_requested() -> None:
    history = _History()
    history.tasks.append(_task("v1", end=_T0 + timedelta(seconds=1)))
    history.decisions["v1"] = "RECUSAR"
    await _poller(history).poll_once()
    names = [p.get("variableName") for path, p in history.calls if path == "/history/variable-instance"]
    assert names == ["decisao_pagamento"]
    detail = [p for path, p in history.calls if path == "/history/detail"]
    assert detail and all(p["activityInstanceId"] == "UT_AprovacaoAlcada:ai-v1" for p in detail)


async def test_a_failed_round_keeps_the_cursor_at_the_last_counted_task() -> None:
    history = _History()
    history.tasks.append(_task("ok1", end=_T0 + timedelta(seconds=1)))
    history.tasks.append(_task("boom", end=_T0 + timedelta(seconds=2)))
    history.decisions.update({"ok1": "APROVAR", "boom": "CANCELAR"})
    history.fail_detail_for.add("boom")
    poller = _poller(history)
    aprovar, cancelar, erro = (
        _sample(**_PAGTO, decisao="APROVAR"),
        _sample(**_PAGTO, decisao="CANCELAR"),
        _poll_sample("erro"),
    )

    assert await poller.poll_once() == 1
    assert _poll_sample("erro") == erro + 1
    assert poller.cursor == _T0 + timedelta(seconds=1)

    history.fail_detail_for.clear()
    assert await poller.poll_once() == 1  # only `boom`; `ok1` is not recounted
    assert _sample(**_PAGTO, decisao="APROVAR") == aprovar + 1
    assert _sample(**_PAGTO, decisao="CANCELAR") == cancelar + 1


async def test_ties_at_the_cursor_instant_across_pages_are_counted_once_each() -> None:
    history = _History()
    same = _T0 + timedelta(seconds=3)
    for tid in ("t1", "t2", "t3"):
        history.tasks.append(_task(tid, end=same))
        history.decisions[tid] = "SOLICITAR_INFO"
    before = _sample(**_PAGTO, decisao="SOLICITAR_INFO")
    poller = _poller(history, page_size=2)
    total = await poller.poll_once()
    total += await poller.poll_once()
    assert total == 3
    assert _sample(**_PAGTO, decisao="SOLICITAR_INFO") == before + 3


def test_every_closed_label_set_is_pre_initialised_at_zero() -> None:
    _poller(_History())
    registry = get_metrics_collector().registry
    for agent_id, action_class, decisao in label_sets():
        value = registry.get_sample_value(
            "maezo_l1_decision_total",
            {"tenant": _TENANT, "agent_id": agent_id, "action_class": action_class, "decisao": decisao},
        )
        assert value is not None, (agent_id, action_class, decisao)


def test_interval_must_be_positive() -> None:
    with pytest.raises(ValueError):
        EngineMetricsPoller(_History(), tenant=_TENANT, interval_s=0)


# --- the transport seam -----------------------------------------------------------------------


async def test_transport_history_read_refuses_any_path_outside_the_closed_set() -> None:
    transport = CibSevenWorkerTransport("http://engine.local/engine-rest")
    try:
        with pytest.raises(ValueError):
            await transport.read_history("/external-task/fetchAndLock", {})
        with pytest.raises(ValueError):
            await transport.read_history("/task/abc/complete", {})
    finally:
        await transport.close()


async def test_transport_history_read_is_a_get_and_tolerates_control_characters() -> None:
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b'[{"id": "1", "description": "a\x01b"}]')

    transport = CibSevenWorkerTransport("http://engine.local/engine-rest")
    await transport.close()
    transport._client = httpx.AsyncClient(
        base_url="http://engine.local/engine-rest", transport=httpx.MockTransport(_handler)
    )
    try:
        body = await transport.read_history("/history/task", {"finished": "true"})
    finally:
        await transport.close()
    assert body == [{"id": "1", "description": "a\x01b"}]
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/engine-rest/history/task"


# --- spec-trace fences: the table names only what the BPMN and L0-core already declare --------


def _bpmn_for(process_key: str) -> Any:
    for path in sorted(_BPMN_DIR.glob(f"{process_key}_*.bpmn")):
        return parse_xml_without_dtd(path.read_bytes())
    raise AssertionError(f"no BPMN for {process_key}")


@pytest.mark.parametrize("spec", L1_DECISION_TASKS, ids=lambda s: s.task_key)
def test_table_vocabulary_is_exactly_the_bpmn_declared_outputs_of_the_user_task(spec: Any) -> None:
    root = _bpmn_for(spec.process_key)
    (process,) = [p for p in root.iter(f"{_NS}process")]
    assert process.get("id") == spec.process_key
    (task,) = [t for t in root.iter(f"{_NS}userTask") if t.get("id") == spec.task_key]
    doc = " ".join((d.text or "") for d in task.iter(f"{_NS}documentation"))
    match = re.search(re.escape(spec.decision_variable) + r"\s+in\s+\{([^}]*)\}", doc)
    assert match, f"{spec.task_key}: no `{spec.decision_variable} in {{...}}` in its documentation"
    declared = {tok.strip() for tok in match.group(1).split(",") if tok.strip()}
    assert declared == set(spec.vocabulary), (spec.task_key, declared ^ set(spec.vocabulary))
    # Every condition on the gateway(s) right after this UT uses a declared token.
    used = set(
        re.findall(
            r"'?" + re.escape(spec.decision_variable) + r"'?\)?\s*==\s*'([A-Z_]+)'",
            _conditions_after(root, spec.task_key),
        )
    )
    assert used and used <= set(spec.vocabulary), (spec.task_key, used - set(spec.vocabulary))


def _conditions_after(root: Any, node: str, hops: int = 3) -> str:
    """Condition text of the sequence flows leaving `node` and the gateways it flows into."""
    flows = list(root.iter(f"{_NS}sequenceFlow"))
    frontier, texts = {node}, []
    for _ in range(hops):
        nxt: set[str] = set()
        for flow in flows:
            if flow.get("sourceRef") in frontier:
                cond = flow.find(f"{_NS}conditionExpression")
                if cond is not None:
                    texts.append(cond.text or "")
                nxt.add(str(flow.get("targetRef")))
        frontier = {n for n in nxt if n.startswith("GW_")}
    return " ".join(texts)


def test_table_action_classes_are_the_l1_keys_of_l0_core_and_overrides_are_l0_hard() -> None:
    matrix = yaml.safe_load((_REPO / "spec" / "policies" / "autonomy" / "L0-core.yaml").read_text())[
        "actions"
    ]
    for spec in L1_DECISION_TASKS:
        assert matrix[spec.action_class]["level"] == "L1", spec.action_class
        for token, klass in spec.action_class_by_token.items():
            assert token in spec.vocabulary
            assert matrix[klass] == {"level": "L0", "hard": True}, klass


def test_table_agents_exist_and_process_keys_are_derived() -> None:
    for spec in L1_DECISION_TASKS:
        assert (_REPO / "spec" / "agents" / spec.agent_id / "agent.yaml").is_file(), spec.agent_id
    assert frozenset(s.process_key for s in L1_DECISION_TASKS) == DEFAULT_PROCESS_KEYS
    keys = [(s.process_key, s.task_key) for s in L1_DECISION_TASKS]
    assert len(keys) == len(set(keys))
