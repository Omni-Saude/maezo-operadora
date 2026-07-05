"""Unit tests for maezo.tools.workers.pagto (SP-OP-PAGTO-001).

TDD London School: tests verify value-driven tier routing and payment release guard.
"""

import pytest

from maezo.tools.workers.pagto import (
    ERR_PAGTO_ORDEM_INVALIDA,
    ERR_PAYMENT_RELEASE_NOT_HUMAN,
    PagtoError,
    assess_admissibility,
    execute_pagto,
    publish_completed,
    release_high_value_payment,
    route_aprovacao,
    validate_pagto,
)

# ---------------------------------------------------------------
# validate_pagto
# ---------------------------------------------------------------


def test_validate_pagto_valid() -> None:
    result = validate_pagto(
        {
            "ordem_pagamento_id": "OP-001",
            "lastro_confirmado": True,
            "duplicidade_suspeita": False,
        }
    )
    assert result["dados_pagamento_validos"] is True
    assert result["lastro_confirmado"] is True


def test_validate_pagto_ordem_invalida() -> None:
    with pytest.raises(PagtoError) as excinfo:
        validate_pagto({"ordem_pagamento_id": ""})
    assert excinfo.value.code == ERR_PAGTO_ORDEM_INVALIDA


# ---------------------------------------------------------------
# assess_admissibility
# ---------------------------------------------------------------


def test_assess_admissibility_segue_roteamento() -> None:
    result = assess_admissibility(
        {
            "dados_pagamento_validos": True,
            "lastro_confirmado": True,
            "duplicidade_suspeita": False,
        }
    )
    assert result["roteamento"] == "SEGUE_ROTEAMENTO"


def test_assess_admissibility_duplicidade() -> None:
    result = assess_admissibility(
        {
            "dados_pagamento_validos": True,
            "lastro_confirmado": True,
            "duplicidade_suspeita": True,
        }
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


# ---------------------------------------------------------------
# route_aprovacao — value-driven tier
# ---------------------------------------------------------------


def test_pagto_tier_match_dentro_teto() -> None:
    """Low-value payment below threshold → auto L2 path."""
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 5_000_000,  # R$ 50k
            "dentro_teto_l2": True,
        }
    )
    assert result["faixa_valor"] == "DENTRO_TETO_L2"
    assert result["grupo_aprovador"] == ""


def test_pagto_tier_match_alcada_l1() -> None:
    """Payment between R$100k-500k → ALCADA_L1."""
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 25_000_000,  # R$ 250k
            "dentro_teto_l2": False,
        }
    )
    assert result["faixa_valor"] == "ALCADA_L1"
    assert result["grupo_aprovador"] == "aprovacao-financeira-l1"


def test_pagto_tier_match_alcada_l2() -> None:
    """Payment between R$500k-2MM → ALCADA_L2."""
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 100_000_000,  # R$ 1MM
            "dentro_teto_l2": False,
        }
    )
    assert result["faixa_valor"] == "ALCADA_L2"
    assert result["grupo_aprovador"] == "aprovacao-financeira-l2"


def test_pagto_tier_match_alcada_l3() -> None:
    """Payment between R$2MM-10MM → ALCADA_L3."""
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 500_000_000,  # R$ 5MM
            "dentro_teto_l2": False,
        }
    )
    assert result["faixa_valor"] == "ALCADA_L3"
    assert result["grupo_aprovador"] == "aprovacao-financeira-l3"


def test_pagto_tier_match_comite() -> None:
    """Payment above R$10MM → ANALISE_HUMANA (comite)."""
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 2_000_000_000,  # R$ 20MM
            "dentro_teto_l2": False,
        }
    )
    assert result["faixa_valor"] == "ANALISE_HUMANA"
    assert result["grupo_aprovador"] == "comite-financeiro"


# ---------------------------------------------------------------
# execute_pagto — low-value auto
# ---------------------------------------------------------------


def test_execute_pagto_dentro_teto() -> None:
    result = execute_pagto(
        {
            "faixa_valor": "DENTRO_TETO_L2",
            "ordem_pagamento_id": "OP-001",
            "valor_pagamento_cents": 50_000,
        }
    )
    assert result["pagamento_executado"] is True
    assert result["tipo_liberacao"] == "auto_L2"


def test_execute_pagto_fora_teto() -> None:
    """Outside threshold should not auto-execute."""
    result = execute_pagto(
        {
            "faixa_valor": "ALCADA_L3",
        }
    )
    assert result["pagamento_executado"] is False


# ---------------------------------------------------------------
# release_high_value_payment — GUARD + tier-match
# ---------------------------------------------------------------


def test_release_high_value_happy_path() -> None:
    result = release_high_value_payment(
        {
            "decisao_pagamento": "APROVAR",
            "aprovador_id": "fin-001",
            "aprovador_tier": 3,
            "justificativa_aprovacao": "Contrato emergencial aprovado pelo comite",
            "valor_aprovado_cents": 500_000_000,
            "faixa_valor": "ALCADA_L3",
        }
    )
    assert result["pagamento_liberado"] is True
    assert result["tipo_liberacao"] == "humano_alcada"


def test_release_high_value_rejects_recusar() -> None:
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(
            {
                "decisao_pagamento": "RECUSAR",
                "aprovador_id": "fin-001",
                "aprovador_tier": 1,
                "justificativa_aprovacao": "x",
                "faixa_valor": "ALCADA_L1",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN


def test_release_high_value_tier_mismatch() -> None:
    """aprovador_tier=1 (L1) trying to approve ALCADA_L3 → rejected."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(
            {
                "decisao_pagamento": "APROVAR",
                "aprovador_id": "fin-001",
                "aprovador_tier": 1,
                "justificativa_aprovacao": "Fora de alcada",
                "faixa_valor": "ALCADA_L3",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "tier-match" in excinfo.value.message.lower()


def test_release_high_value_missing_aprovador() -> None:
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(
            {
                "decisao_pagamento": "APROVAR",
                "aprovador_id": "",
                "aprovador_tier": 2,
                "justificativa_aprovacao": "x",
                "faixa_valor": "ALCADA_L2",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN


# ---------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------


def test_publish_completed_auto() -> None:
    result = publish_completed(
        {
            "faixa_valor": "DENTRO_TETO_L2",
        }
    )
    assert result["desfecho"] == "liberado_automatico"


def test_publish_completed_humano() -> None:
    result = publish_completed(
        {
            "faixa_valor": "ALCADA_L1",
            "decisao_pagamento": "APROVAR",
        }
    )
    assert result["desfecho"] == "liberado_humano"


def test_publish_completed_recusado() -> None:
    result = publish_completed(
        {
            "faixa_valor": "ALCADA_L2",
            "decisao_pagamento": "RECUSAR",
        }
    )
    assert result["desfecho"] == "recusado_humano"
