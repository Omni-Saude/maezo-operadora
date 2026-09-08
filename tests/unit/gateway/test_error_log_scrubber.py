"""Failure diagnostics preserve safe structure without interpreting untrusted values."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.gateway.log_scrubber import ErrorLogScrubber

_RAW = "sentinela paciente teste diagnostico"


def _scrub(**fields: Any) -> dict[str, Any]:
    return ErrorLogScrubber()(None, "error", {"event": "failed", **fields})


def test_nested_errors_keep_structure_and_do_not_mutate_inputs() -> None:
    fields = {"error": {"errors": [{"message": _RAW, "loc": (_RAW, 11111111111)}], _RAW: _RAW}}
    original = copy.deepcopy(fields)
    output = _scrub(details=fields, retry=2)
    assert fields == original
    assert _RAW not in repr(output)
    assert "11111111111" not in repr(output)
    assert output["retry"] == 2
    errors = output["details"]["error"]["errors"]
    assert isinstance(errors, list) and len(errors) == 1
    assert set(errors[0]) == {"message", "loc"}
    assert isinstance(errors[0]["loc"], tuple) and len(errors[0]["loc"]) == 2
    assert len(output["details"]["error"]) == 2


class _UnprintableError(Exception):
    def __str__(self) -> str:
        raise AssertionError("error rendering must not call str")

    def __repr__(self) -> str:
        raise AssertionError("error rendering must not call repr")


@pytest.mark.parametrize("field", ["error", "nested_error", "exc_info"])
def test_unprintable_exceptions_keep_class_without_interrupting_logging(field: str) -> None:
    exc = _UnprintableError(_RAW)
    output = _scrub(**{field: exc})
    assert "_UnprintableError" in repr(output)
    assert _RAW not in repr(output)


@pytest.mark.parametrize("info", [_RAW, [_RAW], {"message": _RAW}, (ValueError, _RAW, None)])
def test_malformed_exc_info_is_minimized(info: Any) -> None:
    output = _scrub(exc_info=info)
    assert "exc_info" not in output
    assert "exception" in output
    assert _RAW not in repr(output)


def test_exception_context_and_tuple_info_keep_frames_without_source() -> None:
    try:
        try:
            raise ValueError(_RAW)
        except ValueError:
            raise RuntimeError(_RAW) from None
    except RuntimeError as exc:
        result = _scrub(exc_info=(type(exc), exc, exc.__traceback__))
    assert "RuntimeError" in result["exception"]
    assert "ValueError" not in result["exception"]  # Python's explicit from-None semantics.
    assert "test_exception_context_and_tuple_info_keep_frames_without_source" in result["exception"]
    assert "raise RuntimeError" not in result["exception"]
    assert _RAW not in result["exception"]


def test_cycles_in_error_containers_and_exception_chains_are_bounded() -> None:
    cause = ValueError(_RAW)
    cause.__cause__ = cause
    value: dict[str, Any] = {"errors": []}
    value["errors"].append(value)
    output = _scrub(error=value, exc_info=cause)
    assert "TRUNCATED_ERROR_STRUCTURE" in repr(output)
    assert "TRUNCATED_ERROR_CHAIN" in repr(output)
    assert _RAW not in repr(output)
    assert len(repr(output)) < 5000


def test_stack_info_keeps_locations_without_source_lines_or_locals() -> None:
    output = _scrub(stack_info=True, operation="seal")
    assert "test_stack_info_keeps_locations_without_source_lines_or_locals" in output["stack"]
    assert "output = _scrub" not in output["stack"]
    assert _RAW not in output["stack"]
    assert output["operation"] == "seal"
