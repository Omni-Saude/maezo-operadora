"""Unit tests for `FunctionWorker` and `pick_fields` (T1.2/ADR-0026 Decisao §1/§2/§5).

TDD London School: exercises the adapter contract in isolation from any real domain module —
`run()` calls the wrapped `fn`, returns its dict, emits `record_worker_execution`/
`record_worker_error` with the right `{worker,topic}` labels, and reclassifies the six modules'
bespoke `Exception(.code, .message)` guard/validation errors into `ValueError` so the harness's
existing fail-closed dispatch (retries=0) applies uniformly — never a third, silently-retried
outcome for an L0 guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from maezo.tools.workers.base import (
    FunctionWorker,
    WorkerBase,
    pick_fields,
    reclassify_coded_exception,
)

# ---------------------------------------------------------------------------
# FunctionWorker — basic adapter contract
# ---------------------------------------------------------------------------


def test_function_worker_is_a_worker_base() -> None:
    worker = FunctionWorker("operadora.test.echo", lambda variables: dict(variables))
    assert isinstance(worker, WorkerBase)


def test_function_worker_topic_is_explicit() -> None:
    worker = FunctionWorker("operadora.test.echo", lambda variables: {})
    assert worker.topic == "operadora.test.echo"


def test_function_worker_default_max_retries_is_1() -> None:
    """ADR-0026 §1: the engine owns durable retry; FunctionWorker executes once by default."""
    worker = FunctionWorker("operadora.test.echo", lambda variables: {})
    assert worker.max_retries == 1


def test_function_worker_max_retries_overridable() -> None:
    worker = FunctionWorker("operadora.test.echo", lambda variables: {}, max_retries=3)
    assert worker.max_retries == 3


def test_function_worker_execute_calls_wrapped_fn_and_returns_its_dict() -> None:
    def fn(variables: dict) -> dict:
        return {"echo": variables.get("value")}

    worker = FunctionWorker("operadora.test.echo", fn)
    result = worker.execute({"value": 42})
    assert result == {"echo": 42}


def test_function_worker_run_calls_wrapped_fn_and_returns_its_dict() -> None:
    """run() (retry/metrics wrapper) delegates to execute() -> the wrapped fn."""
    calls: list[dict] = []

    def fn(variables: dict) -> dict:
        calls.append(variables)
        return {"status": "ok"}

    worker = FunctionWorker("operadora.test.echo", fn)
    result = worker.run({"tenant_id": "amh"})

    assert result == {"status": "ok"}
    assert calls == [{"tenant_id": "amh"}]


def test_function_worker_run_emits_execution_metric_with_worker_and_topic_labels() -> None:
    with patch("maezo.platform.observability.record_worker_execution") as mock_record:
        worker = FunctionWorker("operadora.test.echo", lambda variables: {"status": "ok"})
        worker.run({})

    mock_record.assert_called_once()
    _args, kwargs = mock_record.call_args
    assert kwargs["worker_name"] == "FunctionWorker"
    assert kwargs["topic"] == "operadora.test.echo"


def test_function_worker_run_emits_error_metric_on_failure() -> None:
    def fn(variables: dict) -> dict:
        raise RuntimeError("boom")

    with patch("maezo.platform.observability.record_worker_error") as mock_record:
        worker = FunctionWorker("operadora.test.echo", fn)
        with pytest.raises(RuntimeError, match="boom"):
            worker.run({})

    mock_record.assert_called_once()
    _args, kwargs = mock_record.call_args
    assert kwargs["worker_name"] == "FunctionWorker"
    assert kwargs["topic"] == "operadora.test.echo"
    assert kwargs["error_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Error reclassification (ADR-0026 Decisao §5 — the classify_worker_error provision)
# ---------------------------------------------------------------------------


class _CodedError(Exception):
    """Shape used by adequacao/credenciamento/fraude/inadimplencia/pagto/programa's guard
    errors: a plain Exception subclass with .code/.message (ADR-0026 census)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def test_function_worker_reclassifies_coded_exception_as_value_error() -> None:
    """A bespoke Exception(.code, .message) guard error (e.g. AdequacaoError) must surface as a
    ValueError from execute() — the harness's EXISTING classification then routes it to
    failure(retries=0), never the generic 'unclassified -> transient retry' path (fail-closed,
    ADR-0008: an L0 guard must never be auto-retried into an adverse action)."""

    def fn(variables: dict) -> dict:
        raise _CodedError("ERR_FALLBACK_COMMITMENT_NOT_HUMAN", "responsavel_id ausente")

    worker = FunctionWorker("operadora.adequacao.register_fallback_commitment", fn)
    with pytest.raises(ValueError, match="ERR_FALLBACK_COMMITMENT_NOT_HUMAN") as exc_info:
        worker.execute({})

    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, _CodedError)


@pytest.mark.parametrize("exc_type", [PermissionError, ValueError, RuntimeError, OSError, TimeoutError])
def test_function_worker_never_reclassifies_already_harness_classified_exceptions(exc_type: type) -> None:
    """PermissionError/ValueError/RuntimeError/OSError/TimeoutError already have a correct
    harness-side classification (T1.1 §9) — FunctionWorker must re-raise them UNCHANGED, never
    wrap them a second time."""

    def fn(variables: dict) -> dict:
        raise exc_type("already classified")

    worker = FunctionWorker("operadora.test.echo", fn)
    with pytest.raises(exc_type, match="already classified"):
        worker.execute({})


def test_function_worker_does_not_reclassify_plain_exception_without_code_message() -> None:
    """An exception that merely LOOKS unusual but lacks the .code/.message duck-type must pass
    through unchanged (never silently mask a genuinely unexpected bug as a business error)."""

    def fn(variables: dict) -> dict:
        raise KeyError("unexpected")

    worker = FunctionWorker("operadora.test.echo", fn)
    with pytest.raises(KeyError):
        worker.execute({})


# ---------------------------------------------------------------------------
# reclassify_coded_exception — the shared implementation (GK-w5 finding 3, single source of
# truth). `FunctionWorker.execute` above and `programa._call_guarded` (a raw-handler wrapper
# needing the SAME reclassification rule, item A/B Kafka seam) both call this ONE function —
# these tests pin that they cannot silently diverge.
# ---------------------------------------------------------------------------


def test_reclassify_coded_exception_wraps_coded_exception_as_value_error() -> None:
    def fn() -> dict:
        raise _CodedError("ERR_X", "detail")

    with pytest.raises(ValueError, match="ERR_X") as exc_info:
        reclassify_coded_exception(fn)
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, _CodedError)


@pytest.mark.parametrize(
    "exc_type", [PermissionError, ValueError, RuntimeError, OSError, TimeoutError, ConnectionError]
)
def test_reclassify_coded_exception_passes_through_harness_classified(exc_type: type) -> None:
    def fn() -> dict:
        raise exc_type("already classified")

    with pytest.raises(exc_type, match="already classified"):
        reclassify_coded_exception(fn)


def test_reclassify_coded_exception_passes_through_plain_exception_without_code_message() -> None:
    def fn() -> dict:
        raise KeyError("unexpected")

    with pytest.raises(KeyError):
        reclassify_coded_exception(fn)


def test_function_worker_execute_delegates_to_reclassify_coded_exception() -> None:
    """GK-w5 finding 3: `FunctionWorker.execute` must not hand-roll its own copy of the
    reclassification rule inline — a lightweight structural pin (fails if `execute` is ever
    reimplemented without delegating to the shared function)."""
    assert "reclassify_coded_exception" in FunctionWorker.execute.__code__.co_names


def test_function_worker_and_programa_call_guarded_reclassify_identically() -> None:
    """Cross-module equivalence pin (GK-w5 finding 3): `FunctionWorker.execute` (base.py) and
    `programa._call_guarded` (raw-handler wrapper, item A/B) must produce BYTE-IDENTICAL
    ValueError text + `__cause__` chaining for the SAME `ProgramaError` — proving they share ONE
    implementation, not two independently-maintained copies that could silently diverge."""
    from maezo.tools.workers import programa

    coded = programa.ProgramaError(programa.ERR_PROGRAMA_NO_CONSENT, "consentimento ausente")

    def raise_coded(variables: dict) -> dict:
        raise coded

    worker = FunctionWorker("operadora.test.probe", raise_coded)
    with pytest.raises(ValueError) as fw_exc:
        worker.execute({})

    with pytest.raises(ValueError) as pg_exc:
        programa._call_guarded(raise_coded, {})

    expected = f"{programa.ERR_PROGRAMA_NO_CONSENT}: consentimento ausente"
    assert str(fw_exc.value) == str(pg_exc.value) == expected
    assert fw_exc.value.__cause__ is coded
    assert pg_exc.value.__cause__ is coded


@pytest.mark.parametrize(
    "exc_type", [PermissionError, ValueError, RuntimeError, OSError, TimeoutError, ConnectionError]
)
def test_function_worker_and_programa_call_guarded_passthrough_identically(exc_type: type) -> None:
    """The SAME passthrough set (`_HARNESS_CLASSIFIED`, base.py — reached only via the shared
    `reclassify_coded_exception`) governs BOTH call sites — an already-classified exception type
    passes through UNCHANGED via either path."""
    from maezo.tools.workers import programa

    def raise_exc(variables: dict) -> dict:
        raise exc_type("already classified")

    worker = FunctionWorker("operadora.test.probe", raise_exc)
    with pytest.raises(exc_type, match="already classified"):
        worker.execute({})

    with pytest.raises(exc_type, match="already classified"):
        programa._call_guarded(raise_exc, {})


# ---------------------------------------------------------------------------
# pick_fields — explicit dict -> dataclass field selection (ADR-0026 §2b)
# ---------------------------------------------------------------------------


@dataclass
class _Sample:
    a: str = ""
    b: int = 0


def test_pick_fields_keeps_only_declared_fields() -> None:
    variables = {"a": "x", "b": 1, "c": "unrelated_process_variable"}
    picked = pick_fields(variables, _Sample)
    assert picked == {"a": "x", "b": 1}


def test_pick_fields_omits_missing_keys_rather_than_defaulting_explicitly() -> None:
    variables = {"a": "x"}
    picked = pick_fields(variables, _Sample)
    assert picked == {"a": "x"}
    # The dataclass's own default fills in "b" — pick_fields doesn't fabricate one.
    assert _Sample(**picked) == _Sample(a="x", b=0)


def test_pick_fields_then_construct_round_trips_via_the_dataclass_constructor() -> None:
    variables = {"a": "hello", "b": 7, "irrelevant": True}
    instance = _Sample(**pick_fields(variables, _Sample))
    assert instance == _Sample(a="hello", b=7)
