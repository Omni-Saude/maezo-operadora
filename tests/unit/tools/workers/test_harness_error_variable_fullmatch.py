"""`_is_bounded_error_variable` must validate the WHOLE string (forensic audit, F-129 bug class).

`re.match` with `^...$` accepts a trailing newline (`$` matches before a final `\\n`), so a worker
value like `"enviado\\n"` used to cross the bpmn-error variables channel into process scope as if
it were a bounded token. Same class as tenant ids (#1080), topics (#1087) and enum tokens (#1094).
"""

from __future__ import annotations

import pytest

from maezo.tools.workers.harness import _ERROR_VAR_TOKEN_RE, _is_bounded_error_variable


@pytest.mark.parametrize(
    "value",
    [
        "VALID\n",
        "a.b-c\n",
        "enviado\n",
        "retransmitido\n",
        "MOCK-ANS-NAO-VINCULATIVO-ANSSUB-amh-RN_124_SIP-2026-01\n",
        "\nVALID",
        "VALID\r\n",
        "VALID\nTOKEN",
        "A" + "b" * 127 + "\n",
    ],
)
def test_error_variable_with_newline_is_refused(value: str) -> None:
    assert _is_bounded_error_variable(value) is False
    assert _ERROR_VAR_TOKEN_RE.fullmatch(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "enviado",
        "ack",
        "nack",
        "retransmitido",
        "a.b-c",
        "MOCK-ANS-NAO-VINCULATIVO-ANSSUB-amh-RN_124_SIP-2026-01",
        "0",
        "A",
        "A" + "b" * 127,
    ],
)
def test_valid_error_variables_still_accepted(value: str) -> None:
    assert _is_bounded_error_variable(value) is True


@pytest.mark.parametrize("value", ["A" + "b" * 128, "", "-lead", "has space", "a,b"])
def test_alphabet_and_length_unchanged(value: str) -> None:
    assert _is_bounded_error_variable(value) is False
