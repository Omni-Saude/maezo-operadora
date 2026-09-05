"""Unit proof for `maezo.runtime.guards` (fleet audit ciclo 2, NONE-GUARDRAIL-MISSING:
BEA-04/FER-08). Pure-function contract: an invalid/absent value REJECTS to `None`, never a
default that reads like a real fact — see the module docstring for the family this closes."""

from __future__ import annotations

import pytest

from maezo.runtime.guards import require_iso8601_duration, require_number


class TestRequireNumber:
    def test_genuine_int_passes_through_unchanged(self) -> None:
        notes: list[str] = []
        assert require_number(97, field="score", notes=notes) == 97
        assert notes == []

    def test_zero_is_a_legitimate_value_not_a_rejection(self) -> None:
        """A real `0` (e.g. genuinely zero indicators) must survive — this module rejects the
        WRONG TYPE, never a legitimate falsy-looking real value."""
        notes: list[str] = []
        assert require_number(0, field="score", notes=notes) == 0
        assert notes == []

    def test_none_is_ausente_not_invalido(self) -> None:
        notes: list[str] = []
        assert require_number(None, field="score", notes=notes) is None
        assert notes == ["score_ausente"]

    @pytest.mark.parametrize(
        "bad_value",
        ["97", 97.0, True, False, {}, [], object()],
        ids=["numeric_string", "float", "bool_true", "bool_false", "dict", "list", "object"],
    )
    def test_wrong_type_is_invalido_never_a_silent_zero(self, bad_value: object) -> None:
        """Every one of these used to collapse to `0` at the ONE call site this module was
        extracted from (`beatriz/graph.py::_score_consumed`, BEA-04) — `bool` is explicitly
        included because it is an `int` subclass in Python and must NOT be treated as a
        legitimate 0/1 score."""
        notes: list[str] = []
        assert require_number(bad_value, field="score", notes=notes) is None
        assert notes == ["score_invalido"]

    def test_field_name_is_embedded_in_the_token_for_multi_field_reuse(self) -> None:
        notes: list[str] = []
        require_number(None, field="valor_teste", notes=notes)
        assert notes == ["valor_teste_ausente"]


class TestRequireIso8601Duration:
    @pytest.mark.parametrize(
        "value",
        ["P10D", "P15D", "P60D", "P7D", "P0D", "P1Y2M10D", "PT5H", "P1W", "PT1M30S"],
    )
    def test_valid_iso8601_durations_pass_through_unchanged(self, value: str) -> None:
        assert require_iso8601_duration(value, field="prazo") == value

    @pytest.mark.parametrize(
        "value",
        ["", None, "x", "P", "PT", "10 dias", "'; DROP TABLE prazos; --", 123, "2026-08-01"],
        ids=[
            "empty",
            "none",
            "bare_letter",
            "p_only",
            "pt_only",
            "free_text_days",
            "sql_injection_shape",
            "int",
            "calendar_date_not_a_duration",
        ],
    )
    def test_malformed_values_reject_to_none(self, value: object) -> None:
        assert require_iso8601_duration(value, field="prazo") is None

    def test_notes_are_optional_a_caller_without_a_lacunas_channel_still_gets_a_rejection(self) -> None:
        """`fernando/graph.py` has no `lacunas`-style state field (unlike beatriz) — the notes
        parameter must be safely omittable without raising."""
        assert require_iso8601_duration("not-a-duration", field="prazo") is None

    def test_notes_are_populated_when_the_caller_opts_in(self) -> None:
        notes: list[str] = []
        assert require_iso8601_duration(None, field="prazo") is None
        require_iso8601_duration(None, field="prazo", notes=notes)
        assert notes == ["prazo_ausente"]
        notes.clear()
        require_iso8601_duration("not-a-duration", field="prazo", notes=notes)
        assert notes == ["prazo_invalido"]

    def test_a_calendar_datetime_is_rejected_not_silently_accepted(self) -> None:
        """The precise divergence this helper exists to get right: `datetime.fromisoformat`
        would REJECT the fleet's real DMN output ("P10D" is not a datetime), while a naive
        "any non-empty string passes" guard would WRONGLY accept a calendar date where a
        duration is contractually required. Neither is what `sla_analise_iso`/`prazo_*_iso`
        actually are."""
        assert require_iso8601_duration("2026-08-01T00:00:00", field="prazo") is None
