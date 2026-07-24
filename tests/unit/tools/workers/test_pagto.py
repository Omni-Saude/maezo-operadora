"""Unit tests for maezo.tools.workers.pagto (SP-OP-PAGTO-001).

TDD London School: tests verify value-driven tier routing and payment release guard.
"""

import pytest

from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import WorkerHarness
from maezo.tools.workers.pagto import (
    ERR_PAGTO_ORDEM_INVALIDA,
    ERR_PAYMENT_REFUSAL_NOT_HUMAN,
    ERR_PAYMENT_RELEASE_NOT_HUMAN,
    PagtoError,
    assess_admissibility,
    execute_pagto,
    notify_sla_risk,
    publish_completed,
    register_pagto_workers,
    register_payment_refusal,
    release_high_value_payment,
    route_aprovacao,
    validate_pagto,
)


def _pagto_admissibility_fake(*, roteamento: str, motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("pagto_admissibility", [{"roteamento": roteamento, "motivo": motivo}])
    return fake


def _pagto_alcada_fake(*, faixa_valor: str, grupo_aprovador: str, tier_minimo: int = 0) -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register(
        "pagto_alcada",
        [{"faixa_valor": faixa_valor, "grupo_aprovador": grupo_aprovador, "tier_minimo": tier_minimo}],
    )
    return fake


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
    fake = _pagto_admissibility_fake(roteamento="SEGUE_ROTEAMENTO")
    result = assess_admissibility(
        {
            "dados_pagamento_validos": True,
            "lastro_confirmado": True,
            "duplicidade_suspeita": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "SEGUE_ROTEAMENTO"
    assert fake.calls == [
        (
            "pagto_admissibility",
            {
                "dados_pagamento_validos": True,
                "lastro_confirmado": True,
                "duplicidade_suspeita": False,
            },
        )
    ]


def test_assess_admissibility_duplicidade() -> None:
    fake = _pagto_admissibility_fake(roteamento="ANALISE_HUMANA")
    result = assess_admissibility(
        {
            "dados_pagamento_validos": True,
            "lastro_confirmado": True,
            "duplicidade_suspeita": True,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


def test_assess_admissibility_order_divergence_duplicidade_wins_over_dados_invalidos() -> None:
    """golden-parity finding (T1.5): DMN checks duplicidade_suspeita BEFORE
    dados_pagamento_validos (FIRST hit policy) — dados_pagamento_validos=False AND
    duplicidade_suspeita=True now yields ANALISE_HUMANA (the old Python would have said
    PENDENTE_DADOS, since it checked dados_pagamento_validos first). Both conservative/
    non-releasing; live-verified against the compose engine before cutover."""
    fake = _pagto_admissibility_fake(roteamento="ANALISE_HUMANA")
    result = assess_admissibility(
        {
            "dados_pagamento_validos": False,
            "lastro_confirmado": True,
            "duplicidade_suspeita": True,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


def test_assess_admissibility_dmn_unwired_raises_dmn_evaluation_error() -> None:
    with pytest.raises(DmnEvaluationError):
        assess_admissibility({"dados_pagamento_validos": True}, dmn=None)


# ---------------------------------------------------------------
# route_aprovacao — value-driven tier
# ---------------------------------------------------------------


def test_pagto_tier_match_dentro_teto() -> None:
    """Low-value payment below threshold → auto L2 path."""
    fake = _pagto_alcada_fake(faixa_valor="DENTRO_TETO_L2", grupo_aprovador="", tier_minimo=0)
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 5_000_000,  # R$ 50k
            "dentro_teto_l2": True,
        },
        dmn=fake,
    )
    assert result["faixa_valor"] == "DENTRO_TETO_L2"
    assert result["grupo_aprovador"] == ""
    assert result["tier_minimo"] == 0


def test_pagto_tier_match_alcada_l1() -> None:
    """Payment between R$100k-500k → ALCADA_L1."""
    fake = _pagto_alcada_fake(
        faixa_valor="ALCADA_L1", grupo_aprovador="aprovacao-financeira-l1", tier_minimo=1
    )
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 25_000_000,  # R$ 250k
            "dentro_teto_l2": False,
        },
        dmn=fake,
    )
    assert result["faixa_valor"] == "ALCADA_L1"
    assert result["grupo_aprovador"] == "aprovacao-financeira-l1"


def test_pagto_tier_match_alcada_l2() -> None:
    """Payment between R$500k-2MM → ALCADA_L2."""
    fake = _pagto_alcada_fake(
        faixa_valor="ALCADA_L2", grupo_aprovador="aprovacao-financeira-l2", tier_minimo=2
    )
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 100_000_000,  # R$ 1MM
            "dentro_teto_l2": False,
        },
        dmn=fake,
    )
    assert result["faixa_valor"] == "ALCADA_L2"
    assert result["grupo_aprovador"] == "aprovacao-financeira-l2"


def test_pagto_tier_match_alcada_l3() -> None:
    """Payment between R$2MM-10MM → ALCADA_L3."""
    fake = _pagto_alcada_fake(
        faixa_valor="ALCADA_L3", grupo_aprovador="aprovacao-financeira-l3", tier_minimo=3
    )
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 500_000_000,  # R$ 5MM
            "dentro_teto_l2": False,
        },
        dmn=fake,
    )
    assert result["faixa_valor"] == "ALCADA_L3"
    assert result["grupo_aprovador"] == "aprovacao-financeira-l3"


def test_pagto_tier_match_comite() -> None:
    """Payment above R$10MM → ANALISE_HUMANA (comite)."""
    fake = _pagto_alcada_fake(
        faixa_valor="ANALISE_HUMANA", grupo_aprovador="comite-financeiro", tier_minimo=4
    )
    result = route_aprovacao(
        {
            "valor_pagamento_cents": 2_000_000_000,  # R$ 20MM
            "dentro_teto_l2": False,
        },
        dmn=fake,
    )
    assert result["faixa_valor"] == "ANALISE_HUMANA"
    assert result["grupo_aprovador"] == "comite-financeiro"


class _AlwaysWithinCeilingResolver:
    """Test double simulating a FUTURE tenant overlay whose ceiling exceeds the DMN's hardcoded
    R$100k gate (e.g. a real teto of R$500k) — `within_l2_ceiling` returns True for a value the
    real `CeilingResolver` (today, R$100k) would reject."""

    def within_l2_ceiling(self, *, tenant: str, action: str, param: str, value_cents: int) -> bool:
        del tenant, action, param, value_cents
        return True


def test_pagto_alcada_divergence_dentro_teto_but_above_dmn_hardcoded_gate() -> None:
    """golden-parity finding (T1.5, live-verified): the deployed pagto_alcada DMN's
    DENTRO_TETO_L2 row ALSO requires valor_pagamento_cents <= 10_000_000 (hardcoded ~R$100k) in
    addition to dentro_teto_l2=true — the old Python trusted the resolver's boolean alone. If a
    future tenant overlay raises the resolver's ceiling above the DMN's hardcoded R$100k gate
    (simulated here via a resolver double, since today's L0-core.yaml ceiling is exactly R$100k
    and would not itself exercise the gap), valor_pagamento_cents=15_000_000 with
    dentro_teto_l2=True (from the resolver) -> ALCADA_L1, not DENTRO_TETO_L2 — live-verified
    against the compose engine. Flagged for finance sign-off (the DMN's own description
    already requires it)."""
    fake = _pagto_alcada_fake(
        faixa_valor="ALCADA_L1", grupo_aprovador="aprovacao-financeira-l1", tier_minimo=1
    )
    result = route_aprovacao(
        {"valor_pagamento_cents": 15_000_000},
        _AlwaysWithinCeilingResolver(),  # type: ignore[arg-type]
        dmn=fake,
    )
    assert result["faixa_valor"] == "ALCADA_L1"
    assert fake.calls == [
        (
            "pagto_alcada",
            {"valor_pagamento_cents": 15_000_000, "dentro_teto_l2": True, "tipo_pagamento": ""},
        )
    ]


def test_route_aprovacao_dmn_unwired_raises_dmn_evaluation_error() -> None:
    with pytest.raises(DmnEvaluationError):
        route_aprovacao({"valor_pagamento_cents": 100, "dentro_teto_l2": True}, dmn=None)


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
# notify_sla_risk — informational, never adverse (t2.5-p2b-round2)
# ---------------------------------------------------------------


def test_notify_sla_risk_informational() -> None:
    result = notify_sla_risk({"ordem_pagamento_id": "OP-001", "faixa_valor": "ALCADA_L2"})
    assert result["sla_risk_notified"] is True
    assert result["grupo_alertado"] == "coordenacao-financeira"
    assert result["ordem_pagamento_id"] == "OP-001"


def test_notify_sla_risk_no_adverse_outcome() -> None:
    """The non-interruptive timer alert never produces or propagates a release/refusal decision
    -- UT_AprovacaoAlcada stays open, no adverse outcome ever arises from a timer."""
    result = notify_sla_risk({"ordem_pagamento_id": "OP-001", "decisao_pagamento": "APROVAR"})
    assert "decisao_pagamento" not in result
    forbidden = {"APROVAR", "RECUSAR", "CANCELAR"}
    for value in result.values():
        assert str(value).upper() not in forbidden


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
# register_payment_refusal — GUARD, dual human channel (t2.5-p2b-round2)
# ---------------------------------------------------------------


def test_register_payment_refusal_alcada_channel_happy_path() -> None:
    """decisao_pagamento=RECUSAR (UT_AprovacaoAlcada/UT_CoordenacaoAlcada) + required fields."""
    result = register_payment_refusal(
        {
            "decisao_pagamento": "RECUSAR",
            "justificativa_recusa": "Lastro inconsistente — devolver para revisao",
            "aprovador_id": "fin-001",
            "ordem_pagamento_id": "OP-001",
        }
    )
    assert result["recusa_registrada"] is True
    assert result["canal"] == "alcada"


def test_register_payment_refusal_admissibilidade_channel_happy_path() -> None:
    """decisao_admissibilidade=DEVOLVER (UT_AnaliseAdmissibilidade) + required fields — the
    SAME terminal/worker as the alcada RECUSAR channel (BPMN: ST_RegisterPaymentRefusal has TWO
    incoming flows, Flow_GWDec_Recusar and Flow_GWResol_Devolver)."""
    result = register_payment_refusal(
        {
            "decisao_admissibilidade": "DEVOLVER",
            "justificativa_recusa": "Duplicidade confirmada — devolver para revisao",
            "aprovador_id": "analista-003",
            "ordem_pagamento_id": "OP-002",
        }
    )
    assert result["recusa_registrada"] is True
    assert result["canal"] == "admissibilidade"


def test_register_payment_refusal_refuses_neither_channel() -> None:
    """Refuse-if-no-human (mirrors recurso.register_desistencia): neither decisao_pagamento==
    RECUSAR nor decisao_admissibilidade==DEVOLVER present -- e.g. an omitted/empty decision, or
    a forged direct dispatch of this topic."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": "",
                "decisao_admissibilidade": "",
                "aprovador_id": "fin-001",
                "justificativa_recusa": "x",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN


def test_register_payment_refusal_rejects_aprovar() -> None:
    """decisao_pagamento=APROVAR must never register a refusal (wrong channel value)."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": "APROVAR",
                "aprovador_id": "fin-001",
                "justificativa_recusa": "x",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN


def test_register_payment_refusal_alcada_missing_aprovador_id() -> None:
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": "RECUSAR",
                "aprovador_id": "",
                "justificativa_recusa": "Lastro inconsistente",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN
    assert "aprovador_id" in excinfo.value.message


def test_register_payment_refusal_alcada_missing_justificativa() -> None:
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": "RECUSAR",
                "aprovador_id": "fin-001",
                "justificativa_recusa": "",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN
    assert "justificativa_recusa" in excinfo.value.message


def test_register_payment_refusal_admissibilidade_missing_fields() -> None:
    """DEVOLVER without aprovador_id/justificativa_recusa still refuses -- the gateway's own
    conservative default routing here does NOT by itself satisfy the guard."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_admissibilidade": "DEVOLVER",
                "aprovador_id": "",
                "justificativa_recusa": "",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN
    assert "aprovador_id" in excinfo.value.message
    assert "justificativa_recusa" in excinfo.value.message


def test_register_payment_refusal_never_releases() -> None:
    """NAO e glosa, NAO libera (BPMN task doc) -- result never contains a release marker."""
    result = register_payment_refusal(
        {
            "decisao_pagamento": "RECUSAR",
            "justificativa_recusa": "x",
            "aprovador_id": "fin-001",
        }
    )
    assert "pagamento_liberado" not in result
    assert "glosa" not in str(result).lower()


# ---------------------------------------------------------------
# register_pagto_workers — registration + topic-registry drift guard
# ---------------------------------------------------------------


def test_register_pagto_workers_registers_new_topics() -> None:
    """`notify_sla_risk`/`register_payment_refusal` are registered on their exact BPMN-declared
    topics; `prepare_approval_dossier` (Andre A2A, gated) remains unregistered."""
    harness = WorkerHarness(None, worker_id="unit-test-pagto")  # type: ignore[arg-type]
    register_pagto_workers(harness, None, dmn=FakeDmnTransport())
    topics = set(harness.registered_topics)
    assert "operadora.pagto.notify_sla_risk" in topics
    assert "operadora.pagto.register_payment_refusal" in topics
    assert "operadora.pagto.prepare_approval_dossier" not in topics


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
