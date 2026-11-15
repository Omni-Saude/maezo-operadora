"""MZO-1c — `UNIQUE` hit-policy multi-match is a PERMANENT DMN failure (ADR-0075 §(c)).

Before: the engine's HTTP 500 for two overlapping `UNIQUE` rules became `DmnEvaluationError`
(TRANSIENT) and was engine-retried until the budget ran out. Now it is
`DmnHitPolicyViolationError`: no retry (`failure(retries=0)` -> incident, human-visible), an
audited refusal row in the ADR-0007 chain carrying the offending table's version, and every OTHER
engine error keeps today's transient classification.

No engine here: `httpx.MockTransport` replays the wire shape the Camunda 7 engine-rest code
produces (documented on `dmn_transport._UNIQUE_MULTIMATCH_TYPES`). The real-engine proof is
`tests/integration/dmn/test_unique_multimatch_engine.py` (marked `integration`).
"""

from __future__ import annotations

import functools
from typing import Any

import httpx
import pytest

from maezo.platform.evidence.dmn_sweep import avaliar
from maezo.tools.workers._audit_ctx import collect_dmn_versions
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import (
    ERR_DMN_HIT_POLICY_VIOLATION,
    CibSevenDmnTransport,
    DmnEvaluationError,
    DmnHitPolicyViolationError,
    DmnNoResultError,
    DmnVersion,
    FakeDmnTransport,
    evaluate_sync,
    first_row,
)
from maezo.tools.workers.harness import (
    AUDIT_DECISION_REFUSED,
    ExternalTask,
    FakeAuditSink,
    FakeWorkerTransport,
    WorkerHarness,
)

_KEY = "teste_rco_unique_sobreposicao"
_DEF_ID = f"{_KEY}:4:def-abc"

#: The exact engine body (CIB Seven 2.1.0 / Camunda 7 engine-rest): `RestException` wrapping
#: `DmnHitPolicyException`, message prefixed by `DecisionDefinitionResourceImpl.evaluateDecision`.
#: The matched-rules dump carries OUTPUT VALUES (case data) — it must never reach the error text.
_ENGINE_MESSAGE = (
    f"Cannot evaluate decision {_DEF_ID}: DMN-03001 Hit policy 'UNIQUE' only allows a single rule "
    "to match. Actually match rules: '[DmnEvaluatedDecisionRuleImpl{id='Regra_A', outputEntries="
    "{decisao=SEGREDO_DA_LINHA_A}}, DmnEvaluatedDecisionRuleImpl{id='Regra_B', outputEntries="
    "{decisao=SEGREDO_DA_LINHA_B}}]'."
)
_MULTIMATCH_BODY: dict[str, Any] = {"type": "RestException", "message": _ENGINE_MESSAGE}


def _definition_body() -> dict[str, Any]:
    return {"id": _DEF_ID, "version": 4, "deploymentId": "dep-rco-1", "key": _KEY}


def _transport_answering(
    status: int, *, json_body: Any = None, text: str | None = None
) -> CibSevenDmnTransport:
    """`CibSevenDmnTransport` whose `/evaluate` answers `status` (+ body); version lookup is 200."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/evaluate"):
            if json_body is not None:
                return httpx.Response(status, json=json_body)
            return httpx.Response(status, text=text or "")
        return httpx.Response(200, json=_definition_body())

    base_url = "http://engine.example/engine-rest"
    transport = CibSevenDmnTransport(base_url=base_url)
    transport._new_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        base_url=base_url, transport=httpx.MockTransport(handler)
    )
    return transport


def _multimatch_transport() -> CibSevenDmnTransport:
    return _transport_answering(500, json_body=_MULTIMATCH_BODY)


def _task(*, retries: int | None = 3, topic: str = "operadora.teste.rco") -> ExternalTask:
    return ExternalTask(
        task_id="task-mm-1",
        topic=topic,
        process_instance_id="proc-1",
        business_key="bk-1",
        worker_id="w-1",
        variables={"cpf": "12345678900", "idade": 40},
        retries=retries,
    )


# ---------------------------------------------------------------------------
# 1. Detection — the exact engine shape becomes the permanent class
# ---------------------------------------------------------------------------


async def test_unique_multimatch_raises_permanent_hit_policy_error() -> None:
    with pytest.raises(DmnHitPolicyViolationError) as excinfo:
        await _multimatch_transport().evaluate(_KEY, {"idade": 40})

    exc = excinfo.value
    assert exc.code == ERR_DMN_HIT_POLICY_VIOLATION
    assert exc.decision_key == _KEY
    # provenance: the version resolved BEFORE the overlap was found travels with the error.
    assert exc.version == DmnVersion(key=_KEY, id=_DEF_ID, version=4, deployment_id="dep-rco-1")
    # never the transient class, never a RuntimeError (that is what made it retryable).
    assert not isinstance(exc, DmnEvaluationError)
    assert not isinstance(exc, RuntimeError)
    # no engine text: the matched-rules dump carries output values.
    assert "SEGREDO" not in str(exc)
    assert "SEGREDO" not in exc.message
    assert str(exc).startswith(f"{ERR_DMN_HIT_POLICY_VIOLATION}: ")


async def test_unwrapped_dmn_hit_policy_exception_type_also_matches() -> None:
    body = {"type": "DmnHitPolicyException", "message": _ENGINE_MESSAGE.split(": ", 1)[1]}
    with pytest.raises(DmnHitPolicyViolationError):
        await _transport_answering(500, json_body=body).evaluate(_KEY, {})


def test_hit_policy_error_is_in_the_no_result_family() -> None:
    """Every agent graph catches `(DmnEvaluationError, DmnNoResultError)` and takes its declared
    DMN-unavailable path to a human; the subclass keeps that true without touching any graph."""
    exc = DmnHitPolicyViolationError(_KEY)
    assert isinstance(exc, DmnNoResultError)
    try:
        raise exc
    except (DmnEvaluationError, DmnNoResultError) as caught:
        assert caught is exc
    assert exc.variables == {}  # inputs never ride on the error


@pytest.mark.parametrize(
    ("status", "json_body", "text"),
    [
        pytest.param(500, None, "internal error", id="500-plain-text"),
        pytest.param(
            500,
            {"type": "RestException", "message": "Cannot evaluate decision x: boom"},
            None,
            id="500-other-rest",
        ),
        pytest.param(
            500,
            {
                "type": "RestException",
                "message": "Cannot evaluate decision x: DMN-03002 Hit policy 'ANY' only allows multiple "
                "matching rules with equal output.",
            },
            None,
            id="500-any-hit-policy-03002",
        ),
        pytest.param(
            500,
            {"type": "ProcessEngineException", "message": _ENGINE_MESSAGE},
            None,
            id="500-unexpected-type",
        ),
        pytest.param(
            500,
            {"type": "RestException", "message": _ENGINE_MESSAGE.replace("'UNIQUE'", "'FIRST'")},
            None,
            id="500-code-without-unique",
        ),
        pytest.param(
            500,
            {"type": "RestException", "message": _ENGINE_MESSAGE.replace("DMN-03001", "DMN-030010")},
            None,
            id="500-longer-code",
        ),
        pytest.param(500, [_MULTIMATCH_BODY], None, id="500-non-object-body"),
        pytest.param(400, _MULTIMATCH_BODY, None, id="400-same-body"),
        pytest.param(503, _MULTIMATCH_BODY, None, id="503-same-body"),
    ],
)
async def test_every_other_engine_error_stays_transient(
    status: int, json_body: Any, text: str | None
) -> None:
    """Conservative matching: anything short of status 500 + known type + `DMN-03001` + `'UNIQUE'`
    keeps today's transient `DmnEvaluationError` (engine-retried)."""
    with pytest.raises(DmnEvaluationError) as excinfo:
        await _transport_answering(status, json_body=json_body, text=text).evaluate(_KEY, {})
    assert not isinstance(excinfo.value, DmnHitPolicyViolationError)


def test_evaluate_sync_records_the_offending_version_on_violation() -> None:
    with collect_dmn_versions() as collected, pytest.raises(DmnHitPolicyViolationError):
        evaluate_sync(_multimatch_transport(), _KEY, {"idade": 40})
    assert collected == {_KEY: {"version": 4, "id": _DEF_ID, "deploymentId": "dep-rco-1"}}


# ---------------------------------------------------------------------------
# 2. Harness — no retry, human-visible incident, audited refusal
# ---------------------------------------------------------------------------


def _entry_evaluating(variables: dict[str, Any], *, dmn: CibSevenDmnTransport) -> dict[str, Any]:
    rows, _version = evaluate_sync(dmn, _KEY, {"idade": variables.get("idade")})
    return dict(first_row(rows, _KEY))


async def test_function_worker_multimatch_is_incident_never_retried_and_audited() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, max_retry_attempts=3)
    harness.register_worker(
        FunctionWorker(
            "operadora.teste.rco", functools.partial(_entry_evaluating, dmn=_multimatch_transport())
        )
    )

    await harness._handle(_task(retries=3))

    # no retry: retries=0 on the FIRST delivery although the engine budget was 3 -> incident.
    assert len(transport.failures) == 1
    assert transport.failures[0][2] == 0
    assert transport.completed == []
    # audit: one REFUSED row, bounded code, the offending table's version, PHI-free.
    assert len(sink.emitted) == 1
    record, dedup_key = sink.emitted[0]
    assert record.decision == AUDIT_DECISION_REFUSED
    assert record.details["guard_code"] == ERR_DMN_HIT_POLICY_VIOLATION
    assert record.dmn_versions == {_KEY: {"version": 4, "id": _DEF_ID, "deploymentId": "dep-rco-1"}}
    assert dedup_key == "amh:refuse:task-mm-1"
    flat = repr(record.details)
    assert "12345678900" not in flat
    assert "SEGREDO" not in flat


async def test_raw_handler_multimatch_is_incident_never_retried_and_audited() -> None:
    """A raw handler gets no FunctionWorker reclassification; the plain-`Exception` subclass must
    still not fall into the generic branch's engine-computed retries."""
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, max_retry_attempts=3)

    async def handler(task: ExternalTask) -> dict[str, Any]:
        await _multimatch_transport().evaluate(_KEY, {})
        return {}

    harness.register("operadora.teste.rco", handler)
    await harness._handle(_task(retries=3))

    assert transport.failures[0][2] == 0
    assert len(sink.emitted) == 1
    assert sink.emitted[0][0].details["guard_code"] == ERR_DMN_HIT_POLICY_VIOLATION


async def test_audit_emitted_before_incident_and_sink_outage_never_retries() -> None:
    calls: list[str] = []

    class _Transport(FakeWorkerTransport):
        async def handle_failure(self, task_id: str, worker_id: str, **kwargs: Any) -> None:
            calls.append("failure")
            await super().handle_failure(task_id, worker_id, **kwargs)

    class _Sink(FakeAuditSink):
        async def emit_once(self, record: Any, *, dedup_key: str) -> str:
            calls.append("emit")
            return await super().emit_once(record, dedup_key=dedup_key)

    harness = WorkerHarness(_Transport(), worker_id="w", tenant="amh", audit_sink=_Sink())
    harness.register_worker(
        FunctionWorker(
            "operadora.teste.rco", functools.partial(_entry_evaluating, dmn=_multimatch_transport())
        )
    )
    await harness._handle(_task())
    assert calls == ["emit", "failure"]

    # no sink at all: the refusal audit is best-effort, the incident still happens with retries=0.
    bare = FakeWorkerTransport()
    sinkless = WorkerHarness(bare, worker_id="w", tenant="amh", audit_sink=None)
    sinkless.register_worker(
        FunctionWorker(
            "operadora.teste.rco", functools.partial(_entry_evaluating, dmn=_multimatch_transport())
        )
    )
    await sinkless._handle(_task())
    assert bare.failures[0][2] == 0


async def test_other_5xx_through_the_harness_is_still_retried_and_not_audited() -> None:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, max_retry_attempts=3)
    flaky = _transport_answering(500, text="internal error")
    harness.register_worker(
        FunctionWorker("operadora.teste.rco", functools.partial(_entry_evaluating, dmn=flaky))
    )

    await harness._handle(_task(retries=3))

    assert transport.failures[0][2] == 2  # engine-computed retry (3-1), unchanged
    assert sink.emitted == []


# ---------------------------------------------------------------------------
# 3. Other consumers of the transport
# ---------------------------------------------------------------------------


def test_dmn_sweep_reports_overlap_instead_of_crashing() -> None:
    class _Overlapping(FakeDmnTransport):
        async def evaluate(
            self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
        ) -> Any:
            raise DmnHitPolicyViolationError(decision_key)

    resultado = avaliar(_KEY, {}, dmn=_Overlapping())
    assert isinstance(resultado, str)
    assert resultado.startswith("SOBREPOSICAO DE REGRAS")
