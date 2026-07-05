"""Unit tests for maezo.tools.workers.adequacao (SP-OP-ADEQUACAO-001).

TDD London School: tests verify gap measurement and the human-gated fallback commitment.
"""

import pytest

from maezo.tools.workers.adequacao import (
    ERR_FALLBACK_COMMITMENT_NOT_HUMAN,
    AdequacaoError,
    execute_remediation,
    measure_gap,
    notify_coordenacao,
    register_fallback_commitment,
    route_remediation,
)

# ---------------------------------------------------------------
# measure_gap
# ---------------------------------------------------------------


def test_measure_gap_returns_facts() -> None:
    result = measure_gap(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
            "tipo_carater": "eletivo",
            "prestadores_disponiveis": 3,
        }
    )
    assert result["tempo_acesso_apurado_min"] == 45
    assert result["distancia_apurada_km"] == 15.5
    assert result["prestadores_disponiveis"] == 3
    assert result["cobertura_geo_suficiente"] is True
    assert result["dados_geo_completos"] is True


def test_measure_gap_insufficient_data() -> None:
    result = measure_gap(
        {
            "regiao_saude": "",
            "especialidade": "",
        }
    )
    assert result["dados_geo_completos"] is False


# ---------------------------------------------------------------
# route_remediation
# ---------------------------------------------------------------


def test_adequacao_gap_conforme() -> None:
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 15,
            "distancia_apurada_km": 5.0,
            "prestadores_disponiveis": 5,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        }
    )
    assert result["gap_adequacao"] == "CONFORME"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_adequacao_gap_leve() -> None:
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 45,
            "distancia_apurada_km": 10.0,
            "prestadores_disponiveis": 2,
            "cobertura_geo_suficiente": True,
            "dados_geo_completos": True,
        }
    )
    assert result["gap_adequacao"] == "GAP_LEVE"
    assert result["roteamento_remediacao"] == "MONITORAR"


def test_adequacao_gap_moderado() -> None:
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 75,
            "distancia_apurada_km": 30.0,
            "prestadores_disponiveis": 1,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": True,
        }
    )
    assert result["gap_adequacao"] == "GAP_MODERADO"
    assert result["roteamento_remediacao"] == "ENCAMINHAR_CREDENCIAMENTO"


def test_adequacao_gap_critico() -> None:
    result = route_remediation(
        {
            "tipo_carater": "eletivo",
            "tempo_acesso_apurado_min": 120,
            "distancia_apurada_km": 50.0,
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": False,
            "dados_geo_completos": True,
        }
    )
    assert result["gap_adequacao"] == "GAP_CRITICO"
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"


def test_adequacao_gap_dados_incompletos() -> None:
    result = route_remediation(
        {
            "dados_geo_completos": False,
            "tempo_acesso_apurado_min": 0,
            "distancia_apurada_km": 0.0,
            "prestadores_disponiveis": 0,
            "cobertura_geo_suficiente": False,
        }
    )
    assert result["gap_adequacao"] == "GAP_CRITICO"
    assert result["roteamento_remediacao"] == "ANALISE_HUMANA"


# ---------------------------------------------------------------
# notify_coordenacao
# ---------------------------------------------------------------


def test_notify_coordenacao() -> None:
    result = notify_coordenacao(
        {
            "regiao_saude": "R-001",
            "gap_adequacao": "GAP_CRITICO",
        }
    )
    assert result["notificacao_enviada"] is True


# ---------------------------------------------------------------
# execute_remediation
# ---------------------------------------------------------------


def test_execute_remediation() -> None:
    result = execute_remediation(
        {
            "regiao_saude": "R-001",
            "especialidade": "cardiologia",
        }
    )
    assert result["handoff_credenciamento"] is True
    assert result["processo_destino"] == "SP-OP-CRED-001"


# ---------------------------------------------------------------
# register_fallback_commitment — GUARD
# ---------------------------------------------------------------


def test_fallback_commitment_happy_path() -> None:
    result = register_fallback_commitment(
        {
            "decisao_remediacao": "COMPROMISSO_FALLBACK",
            "tipo_fallback": "livre_escolha",
            "justificativa_fallback": "Sem prestador na regiao — RN 259",
            "referencia_regulatoria": "RN 259",
            "responsavel_id": "gestor-001",
            "estimativa_custo_cents": 500000,
        }
    )
    assert result["compromisso_fallback_registrado"] is True


def test_fallback_commitment_rejects_wrong_decisao() -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(
            {
                "decisao_remediacao": "MONITORAR_OK",
                "tipo_fallback": "",
                "justificativa_fallback": "",
                "referencia_regulatoria": "",
                "responsavel_id": "",
            }
        )
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
    assert "MONITORAR_OK" in excinfo.value.message


def test_fallback_commitment_rejects_missing_justificativa() -> None:
    with pytest.raises(AdequacaoError) as excinfo:
        register_fallback_commitment(
            {
                "decisao_remediacao": "COMPROMISSO_FALLBACK",
                "tipo_fallback": "reembolso_garantido",
                "justificativa_fallback": "",
                "referencia_regulatoria": "RN 259",
                "responsavel_id": "gestor-001",
            }
        )
    assert excinfo.value.code == ERR_FALLBACK_COMMITMENT_NOT_HUMAN
