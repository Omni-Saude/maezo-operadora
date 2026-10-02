"""Caso B03 (bateria de 02/10/2026): sincope grave escala como emergencia na tabela adulta.

A r7 ignorava a intensidade (sincope -> P2 enfermagem em qualquer caso), e o resultado do B03 variava
com o que o classificador lia. A r7a separa o grave: P1 emergencia. As demais intensidades seguem na r7.
"""

from __future__ import annotations

import pytest

from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_ADULT = DMN_DIR / "triage_redflag_adult.dmn"


def test_sincope_grave_escala_p1_emergencia() -> None:
    verdict = evaluate(
        read_live_table(_ADULT), {"sintoma_codigo": "sincope", "intensidade": "grave", "idade_anos": 40}
    )
    assert verdict.regra == "r7a"
    assert verdict.saidas["red_flag"] is True
    assert verdict.saidas["prioridade"] == "P1"
    assert verdict.saidas["conduta"] == "ESCALATE_EMERGENCY"


@pytest.mark.parametrize("intensidade", ["leve", "moderada", "desconhecida"])
def test_sincope_nao_grave_segue_na_enfermagem(intensidade: str) -> None:
    verdict = evaluate(
        read_live_table(_ADULT), {"sintoma_codigo": "sincope", "intensidade": intensidade, "idade_anos": 40}
    )
    assert verdict.regra == "r7"
    assert verdict.saidas["prioridade"] == "P2"
    assert verdict.saidas["conduta"] == "ESCALATE_NURSE"
