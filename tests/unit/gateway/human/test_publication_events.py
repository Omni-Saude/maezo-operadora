"""P1 (16/10/2026): the staff publication job never skips a live task in silence, and says WHY it refused.

The silent `continue` for a live task whose (process definition, task key) the Q2 admission does not
name made a redeployed SP-OP-ESCALATION-001 look like an empty queue with `T15_RESULT ok=true`.
These tests pin: the `human_task_skipped` event (closed reason, ids only), `JobResult.tasks_skipped`,
the closed refusal reason on `ReadRefusalError`, the `publication refused: <Type>:<reason>` line,
and the field allowlist that keeps PHI out of every event (also when `tools/staff_ops/job.py`
forwards them). Synthetic data only.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from tests.unit.gateway.human.test_task_publication_source import (
    NOW,
    PROCESS,
    TASK_KEY,
    UUID_A,
    UUID_B,
    Clock,
    TaskPublisher,
    _rows,
    _task_job,
)
from tools.staff_ops import job as staff_job

from maezo.gateway.human import membership_publication_job as mpj
from maezo.gateway.human.publication_events import (
    EVENT_FIELDS,
    EVENT_PUBLICATION_REFUSED,
    EVENT_TASK_SKIPPED,
    SKIP_REASONS,
    EventRefusedError,
    forwardable,
    publication_refused,
    render,
    task_skipped,
)
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_credentials import UNAVAILABLE_REASONS, unavailable
from maezo.gateway.human.task_publication_source import LiveTask

#: A redeploy: same task key, a process definition the admission does not name.
NEW_VERSION = "SP-OP-ESCALATION-001:2:7d1e0c55-0000-4000-8000-000000000002"


def _job_with_events(tmp_path: Any) -> tuple[Any, TaskPublisher, list[dict[str, object]]]:
    clock, publisher, rows = Clock(), TaskPublisher(), _rows()
    clock.now = NOW
    job = _task_job(rows, publisher, tmp_path / "l.json", clock)
    events: list[dict[str, object]] = []
    job._events = events.append
    return job, publisher, events


@pytest.mark.asyncio
async def test_a_live_task_of_a_definition_not_admitted_is_an_event_not_silence(tmp_path) -> None:
    job, publisher, events = _job_with_events(tmp_path)
    publisher.native.tasks[UUID_A] = LiveTask(UUID_A, 1, PROCESS, TASK_KEY)  # type: ignore[union-attr]
    publisher.native.tasks[UUID_B] = LiveTask(UUID_B, 1, NEW_VERSION, TASK_KEY)  # type: ignore[union-attr]
    result = await job.run()
    assert (result.tasks_published, result.tasks_skipped) == (1, 1)
    assert events == [
        {
            "event": EVENT_TASK_SKIPPED,
            "reason": "definition_not_admitted",
            "task_id": UUID_B,
            "task_definition_key": TASK_KEY,
            "process_definition_id": NEW_VERSION,
            "admitted_process_definition_ids": [PROCESS],
        }
    ]
    assert ("resource", UUID_B) not in publisher.calls  # still not published: only no longer silent


@pytest.mark.asyncio
async def test_a_task_id_outside_the_admitted_format_is_an_event(tmp_path) -> None:
    job, publisher, events = _job_with_events(tmp_path)
    publisher.native.tasks["10001"] = LiveTask("10001", 1, PROCESS, TASK_KEY)  # type: ignore[union-attr]
    result = await job.run()
    assert result.tasks_skipped == 1 and result.tasks_published == 0
    assert [(e["reason"], e["task_id"]) for e in events] == [("task_id_format_not_admitted", "10001")]


@pytest.mark.asyncio
async def test_a_task_completed_during_the_publication_is_an_event(tmp_path) -> None:
    job, publisher, events = _job_with_events(tmp_path)
    publisher.native.tasks[UUID_A] = LiveTask(UUID_A, 1, PROCESS, TASK_KEY)  # type: ignore[union-attr]

    async def gone(task_id: str) -> None:
        return None  # completed between the evidence and the resource

    publisher.native.task = gone  # type: ignore[union-attr,method-assign]
    result = await job.run()
    assert (result.tasks_published, result.tasks_skipped, result.evidence_published) == (0, 1, 1)
    assert [e["reason"] for e in events] == ["task_changed_during_publication"]


@pytest.mark.asyncio
async def test_every_event_the_job_emits_has_exactly_the_allowed_fields_and_renders(tmp_path) -> None:
    job, publisher, events = _job_with_events(tmp_path)
    publisher.native.tasks[UUID_B] = LiveTask(UUID_B, 1, NEW_VERSION, TASK_KEY)  # type: ignore[union-attr]
    publisher.native.tasks["10001"] = LiveTask("10001", 1, PROCESS, TASK_KEY)  # type: ignore[union-attr]
    await job.run()
    assert len(events) == 2
    for event in events:
        assert set(event) == EVENT_FIELDS[str(event["event"])]
        assert event["reason"] in SKIP_REASONS
        assert json.loads(render(event)) == event


def test_the_job_result_line_carries_tasks_skipped() -> None:
    assert "tasks_skipped" in {f for f in mpj.JobResult.__dataclass_fields__}


# --- the PHI fence of the events -------------------------------------------------------------


def _skip(**changes: Any) -> dict[str, object]:
    event = task_skipped(
        reason="definition_not_admitted",
        task_id=UUID_B,
        task_definition_key=TASK_KEY,
        process_definition_id=NEW_VERSION,
        admitted_process_definition_ids=[PROCESS],
    )
    event.update(changes)
    return event


@pytest.mark.parametrize(
    "event",
    [
        _skip(nome="Maria"),  # a field outside the list
        _skip(telefone="11987654321"),
        {k: v for k, v in _skip().items() if k != "task_id"},  # a field missing
        _skip(task_id="Maria da Silva"),  # free text where an id goes
        _skip(process_definition_id="maria@example.com"),
        _skip(task_definition_key="+55 11 98765-4321"),
        _skip(admitted_process_definition_ids=["dor no peito"]),
        _skip(reason="outro_motivo"),
        {"event": "human_task_published", "reason": "x"},
        {**publication_refused(ValueError()), "message": "CPF 123.456.789-09"},
    ],
)
def test_render_refuses_any_field_or_value_outside_the_closed_shape(event: dict[str, object]) -> None:
    with pytest.raises(EventRefusedError):
        render(event)


def test_forwarding_keeps_only_our_events_re_rendered() -> None:
    good = render(_skip())
    stderr = "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "x.py", line 1, in <module> CPF 123.456.789-09',
            json.dumps(_skip(nome="Maria")),
            good,
            json.dumps(publication_refused(unavailable("catalog_not_admitted"))),
            "publication refused: ReadRefusalError:catalog_not_admitted",
        ]
    )
    assert staff_job.forward_events(stderr) == [
        good,
        render(publication_refused(unavailable("catalog_not_admitted"))),
    ]
    assert forwardable("not json") is None and forwardable("[1]") is None


# --- the refusal reason --------------------------------------------------------------------


def test_unavailable_carries_a_closed_reason_and_keeps_the_wire_code() -> None:
    exc = unavailable("catalog_not_admitted")
    assert (exc.code, exc.reason) == ("read_dependency_unavailable", "catalog_not_admitted")
    assert unavailable().reason == "unclassified"
    assert unavailable("anything else").reason == "unclassified"  # type: ignore[arg-type]
    assert "unclassified" in UNAVAILABLE_REASONS
    assert ReadRefusalError("invalid_request").reason is None


@pytest.mark.parametrize(
    ("raised", "line"),
    [
        (unavailable("catalog_not_admitted"), "publication refused: ReadRefusalError:catalog_not_admitted"),
        (unavailable("too_many_live_tasks"), "publication refused: ReadRefusalError:too_many_live_tasks"),
        (
            ReadRefusalError("read_dependency_unavailable"),
            "publication refused: ReadRefusalError:unclassified",
        ),
        (KeyError("MAEZO_HUMAN_MATERIAL_VERSION_ID"), "publication refused: KeyError:unclassified"),
    ],
)
def test_main_prints_type_and_reason_last_and_the_refusal_event(
    raised: BaseException,
    line: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    async def refuse(config: Any, *, rebase: Any) -> Any:
        raise raised

    monkeypatch.setattr(mpj, "load_config", lambda path: object())
    monkeypatch.setattr(mpj, "_publish", refuse)
    assert mpj.main(["publish", "--config", str(tmp_path / "c.json")]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert err[-1] == line
    event = json.loads(err[0])
    assert (
        event["event"] == EVENT_PUBLICATION_REFUSED and set(event) == EVENT_FIELDS[EVENT_PUBLICATION_REFUSED]
    )
    assert event["reason"] == line.rsplit(":", 1)[1]
    assert "MAEZO_HUMAN_MATERIAL_VERSION_ID" not in "\n".join(err)  # the message never echoes


def test_staff_job_forwards_events_then_the_last_line_then_t15(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    skipped = render(_skip())
    monkeypatch.setenv("STAFF_JOB_LEDGER_BUCKET", "b")
    monkeypatch.setenv("STAFF_JOB_LEDGER_KEY", "k")
    monkeypatch.setenv("MAEZO_HUMAN_MATERIAL_VERSION_ID", "v")
    monkeypatch.setenv("MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256", "0" * 64)
    monkeypatch.setattr(staff_job, "_s3", lambda: object())
    monkeypatch.setattr(staff_job, "fetch_ledger", lambda *a, **k: "v1")
    monkeypatch.setattr(staff_job, "store_ledger", lambda *a, **k: "v2")
    result_line = json.dumps({"tasks_published": 0, "tasks_skipped": 1})
    monkeypatch.setattr(
        staff_job.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=result_line + "\n", stderr=skipped + "\n"),
    )
    assert staff_job.run_main() == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [skipped, result_line, "T15_RESULT ok=true ledger v1->v2"]
