"""`_is_bounded_token` must validate the WHOLE string (forensic audit, F-129 bug class).

`re.match` with `^...$` accepts a trailing newline (`$` matches before a final `\\n`), so
`"APROVAR\\n"` used to be stored in the clear in `decision_basis` as if it were a bounded enum
token — a log/audit-injection primitive. Same class as tenant ids (#1080) and topics (#1087).
"""

from __future__ import annotations

import pytest

from maezo.tools.workers.harness import _ENUM_TOKEN_RE, _is_bounded_token


@pytest.mark.parametrize(
    "value",
    [
        "VALID_TOKEN\n",
        "\nVALID",
        "VALID\r\n",
        "APROVAR\n",
        "liberado_automatico\n",
        "VALID\nTOKEN",
    ],
)
def test_token_with_newline_is_refused(value: str) -> None:
    assert _is_bounded_token(value) is False
    assert _ENUM_TOKEN_RE.fullmatch(value) is None


@pytest.mark.parametrize(
    "value",
    ["APROVAR", "DENTRO_TETO_L2", "PENDENTE_DADOS", "liberado_automatico", "A", "A" + "b" * 39],
)
def test_valid_tokens_still_accepted(value: str) -> None:
    assert _is_bounded_token(value) is True


@pytest.mark.parametrize("value", ["A" + "b" * 40, "1ABC", "AUTH-amh-guia", "com espaco", ""])
def test_alphabet_and_length_unchanged(value: str) -> None:
    assert _is_bounded_token(value) is False
