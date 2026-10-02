"""Caso H03 (bateria de 30/09/2026): falta de ar sem intensidade informada escala na tabela adulta.

Decisao do diretor de tecnologia: o custo de um caso a mais e' menor que o de uma dispneia sem
atendimento. A regra r2 passa a aceitar `desconhecida`; os outros codigos que dependem de
intensidade NAO mudam aqui.
"""

from __future__ import annotations

import pytest

from maezo.agents.helena import prompts
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_ADULT = DMN_DIR / "triage_redflag_adult.dmn"


@pytest.mark.parametrize("intensidade", ["grave", "moderada", "leve", "desconhecida"])
def test_dispneia_adulta_escala_p1_com_intensidade_conhecida_ou_nao(intensidade: str) -> None:
    verdict = evaluate(
        read_live_table(_ADULT), {"sintoma_codigo": "dispneia", "intensidade": intensidade, "idade_anos": 40}
    )
    assert verdict.regra == "r2"
    assert verdict.saidas["red_flag"] is True
    assert verdict.saidas["prioridade"] == "P1"


def test_outros_codigos_com_intensidade_desconhecida_nao_mudaram() -> None:
    # sangramento_ativo e febre continuam dependendo da intensidade: a mudanca e' so' da dispneia.
    for code in ("sangramento_ativo", "dor_abdominal"):
        verdict = evaluate(
            read_live_table(_ADULT), {"sintoma_codigo": code, "intensidade": "desconhecida", "idade_anos": 40}
        )
        assert verdict.saidas["red_flag"] is False, code


def test_estado_mental_alterado_e_ensinado_ao_classificador_como_deficit_neurologico() -> None:
    texto = prompts.classify_prompt()
    assert "confusao mental" in texto
    assert "deficit_neurologico" in texto.split("ESTADO MENTAL ALTERADO", 1)[1][:600]
    assert "deficit_neurologico" in prompts.SINTOMA_CODIGOS_BY_POPULATION["adult"]
