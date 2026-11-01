"""DL-0159: formulario do registro de pagamento do reembolso (feito fora do sistema)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maezo.portal.contracts.models import _BINDINGS, _INPUTS_BY_FORM, ReembolsoPagamentoInputs

_KEY = ("SP-OP-REEMBOLSO-001", "UT_RegistrarPagamento")


def test_task_is_bound_to_the_payment_form_only() -> None:
    assert _BINDINGS[_KEY][0] == "reembolso_pagamento"
    assert _INPUTS_BY_FORM["reembolso_pagamento"] == ("data_pagamento",)


def test_valid_iso_date_parses() -> None:
    parsed = ReembolsoPagamentoInputs.model_validate(
        {"kind": "reembolso_pagamento", "data_pagamento": "2026-10-28"}
    )
    assert parsed.data_pagamento == "2026-10-28"


@pytest.mark.parametrize("value", ["28/10/2026", "2026-13-01", "2026-02-30", "", "hoje"])
def test_invalid_date_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        ReembolsoPagamentoInputs.model_validate({"kind": "reembolso_pagamento", "data_pagamento": value})


def test_form_carries_no_amount_or_decision() -> None:
    with pytest.raises(ValidationError):
        ReembolsoPagamentoInputs.model_validate(
            {
                "kind": "reembolso_pagamento",
                "data_pagamento": "2026-10-28",
                "valor_reembolso_aprovado_cents": "1",
            }
        )
