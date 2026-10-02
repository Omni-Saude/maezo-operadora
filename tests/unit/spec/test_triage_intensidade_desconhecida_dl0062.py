"""Bateria de 01/10/2026 (E02, E06, E07): sintoma que a regra cobre escala mesmo sem intensidade dita (DL-0062).

Decisao INTERINA, pelo principio do DL-0051: o custo de um caso a mais e' menor que o de um sintoma
sem atendimento. Nao ratificada pelo dono clinico; este teste fixa o comportamento para que a
ratificacao (ou a reversao) seja um ato consciente, nao uma mudanca silenciosa.
"""

from __future__ import annotations

import pytest

from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table


def _mesa(nome: str):
    return read_live_table(DMN_DIR / nome)


@pytest.mark.parametrize("intensidade", ["moderada", "grave", "desconhecida"])
def test_e02_febre_em_idoso_sem_intensidade_escala_p2(intensidade: str) -> None:
    veredito = evaluate(
        _mesa("triage_redflag_adult.dmn"),
        {"sintoma_codigo": "febre", "intensidade": intensidade, "idade_anos": 78},
    )
    assert (veredito.saidas["red_flag"], veredito.saidas["prioridade"]) == (True, "P2")


def test_e02_febre_em_adulto_jovem_sem_intensidade_nao_muda() -> None:
    veredito = evaluate(
        _mesa("triage_redflag_adult.dmn"),
        {"sintoma_codigo": "febre", "intensidade": "desconhecida", "idade_anos": 30},
    )
    assert veredito.saidas["red_flag"] is False


def test_e06_desidratacao_infantil_sem_intensidade_escala_p2() -> None:
    veredito = evaluate(
        _mesa("triage_redflag_pediatric.dmn"),
        {"sintoma_codigo": "sinais_desidratacao", "intensidade": "desconhecida", "idade_meses": 8},
    )
    assert (veredito.saidas["red_flag"], veredito.saidas["prioridade"]) == (True, "P2")


def test_e07_febre_na_gestacao_sem_intensidade_escala_p2() -> None:
    veredito = evaluate(
        _mesa("triage_redflag_gestante.dmn"),
        {"sintoma_codigo": "febre", "intensidade": "desconhecida", "idade_gestacional_semanas": 25},
    )
    assert (veredito.saidas["red_flag"], veredito.saidas["prioridade"]) == (True, "P2")


def test_o_que_nao_foi_tocado_continua_dependendo_da_intensidade() -> None:
    """Escopo do DL-0062: sangramento, dor abdominal e o fail-safe nao mapeado ficam como estavam."""
    for codigo in ("sangramento_ativo", "dor_abdominal", "sintoma_nao_mapeado"):
        veredito = evaluate(
            _mesa("triage_redflag_adult.dmn"),
            {"sintoma_codigo": codigo, "intensidade": "desconhecida", "idade_anos": 40},
        )
        assert veredito.saidas["red_flag"] is False, codigo
