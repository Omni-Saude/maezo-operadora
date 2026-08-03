"""Unit tests for maezo.tools.workers.pagto (SP-OP-PAGTO-001).

TDD London School: tests verify value-driven tier routing and payment release guard.
"""

import asyncio
import inspect

import pytest
import structlog.testing

from maezo.a2a import DelegationResult, RejectionReason
from maezo.tools.workers import pagto as pagto_module
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import ExternalTask, WorkerHarness
from maezo.tools.workers.pagto import (
    ERR_PAGTO_ORDEM_INVALIDA,
    ERR_PAYMENT_REFUSAL_NOT_HUMAN,
    ERR_PAYMENT_RELEASE_NOT_HUMAN,
    PagtoError,
    assess_admissibility,
    execute_pagto,
    make_prepare_approval_dossier_handler,
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
# route_aprovacao — ceiling-fact PROPAGATION (item-9 bucket-3 Class-C, module FINDING 1 of
# test_sp_op_pagto_001): the COMPUTED dentro_teto_l2 must be returned as an output variable so
# the NATIVE BRT_AlcadaRouting reads the resolver's fail-closed fact, never the raw start seed.
# ---------------------------------------------------------------


class _NeverWithinCeilingResolver:
    """Test double: the resolver refuses the ceiling (e.g. value above teto / unloadable matrix)."""

    def within_l2_ceiling(self, *, tenant: str, action: str, param: str, value_cents: int) -> bool:
        del tenant, action, param, value_cents
        return False


def test_route_aprovacao_propagates_computed_ceiling_fact_overriding_false_seed() -> None:
    """Seed dentro_teto_l2=False + resolver says WITHIN -> the returned output variable is the
    COMPUTED True (pre-fix: the key was absent, so the seed survived into BRT_AlcadaRouting and
    a within-ceiling payment failed to auto-release)."""
    fake = _pagto_alcada_fake(faixa_valor="DENTRO_TETO_L2", grupo_aprovador="", tier_minimo=0)
    result = route_aprovacao(
        {"valor_pagamento_cents": 5_000_000, "dentro_teto_l2": False},
        _AlwaysWithinCeilingResolver(),  # type: ignore[arg-type]
        dmn=fake,
    )
    assert result["dentro_teto_l2"] is True


def test_route_aprovacao_propagates_computed_ceiling_fact_overriding_true_seed() -> None:
    """Seed dentro_teto_l2=True + resolver says NOT within -> the returned output variable is the
    COMPUTED False (fail-closed: an inflated seed can never smuggle a payment into the
    auto-release band; L1 direction of the same propagation)."""
    fake = _pagto_alcada_fake(
        faixa_valor="ALCADA_L1", grupo_aprovador="aprovacao-financeira-l1", tier_minimo=1
    )
    result = route_aprovacao(
        {"valor_pagamento_cents": 15_000_000, "dentro_teto_l2": True},
        _NeverWithinCeilingResolver(),  # type: ignore[arg-type]
        dmn=fake,
    )
    assert result["dentro_teto_l2"] is False
    # And the DMN evaluation itself received the computed fact, not the seed.
    assert fake.calls == [
        (
            "pagto_alcada",
            {"valor_pagamento_cents": 15_000_000, "dentro_teto_l2": False, "tipo_pagamento": ""},
        )
    ]


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
# release_high_value_payment — whitespace-bypass vectors (t3.1-guard-input-hardening,
# closing the #133-audit-flagged gap at pagto.py:283,285: the guard's original bare
# `if not aprovador_id` / `if not justificativa` checks let WHITESPACE-ONLY accountability
# fields through, exactly the same class the c1377fa fix closed for
# register_payment_refusal. Every vector below MUST refuse with ERR_PAYMENT_RELEASE_NOT_HUMAN
# -- whitespace-only is the SAME as absent (ADR-0007: a release must carry an identifying
# human approver + a real justification).
# ---------------------------------------------------------------


def _release_high_value_baseline(**overrides: object) -> dict[str, object]:
    """Happy-path baseline for release_high_value_payment -- tier-match satisfied so any
    guard failure observed in a test is attributable ONLY to the field under test."""
    base: dict[str, object] = {
        "decisao_pagamento": "APROVAR",
        "aprovador_id": "fin-001",
        "aprovador_tier": 3,
        "justificativa_aprovacao": "Contrato emergencial aprovado pelo comite",
        "valor_aprovado_cents": 500_000_000,
        "faixa_valor": "ALCADA_L3",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("whitespace", [" ", "   ", "\t", "\n", "\t\n ", "\r\n"])
def test_release_high_value_whitespace_only_aprovador_id_refuses(whitespace: str) -> None:
    """Bare-truthiness bypass (pre-fix): whitespace-only aprovador_id must refuse."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_release_high_value_baseline(aprovador_id=whitespace))
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "aprovador_id" in excinfo.value.message


@pytest.mark.parametrize("non_string", [123, True, 0.5, ["fin-001"], {"id": "fin-001"}])
def test_release_high_value_non_string_aprovador_id_refuses(non_string: object) -> None:
    """A NON-string aprovador_id normalizes to '' and refuses -- the pre-fix bare truthiness
    check (`if not aprovador_id`) would have silently PASSED a truthy non-string (e.g. 123),
    releasing a high-value payment with a non-identifying approver."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_release_high_value_baseline(aprovador_id=non_string))
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "aprovador_id" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "   ", "\t", "\n", "\t\n ", "\r\n"])
def test_release_high_value_whitespace_only_justificativa_refuses(whitespace: str) -> None:
    """Bare-truthiness bypass (pre-fix): whitespace-only justificativa_aprovacao must refuse."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_release_high_value_baseline(justificativa_aprovacao=whitespace))
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "justificativa_aprovacao" in excinfo.value.message


@pytest.mark.parametrize("non_string", [123, True, 0.5, ["ok"], {"j": "ok"}])
def test_release_high_value_non_string_justificativa_refuses(non_string: object) -> None:
    """A NON-string justificativa_aprovacao normalizes to '' and refuses -- same sibling hole
    as aprovador_id (a truthy non-string would previously have silently PASSED)."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_release_high_value_baseline(justificativa_aprovacao=non_string))
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "justificativa_aprovacao" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "   ", "\t", "\n", "\t\n ", "\r\n"])
def test_release_high_value_both_accountability_fields_whitespace_refuses(whitespace: str) -> None:
    """Both aprovador_id AND justificativa_aprovacao whitespace-only -- both missing fields
    named in the guard's error message."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(
            _release_high_value_baseline(aprovador_id=whitespace, justificativa_aprovacao=whitespace)
        )
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "aprovador_id" in excinfo.value.message
    assert "justificativa_aprovacao" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_release_high_value_whitespace_only_decisao_refuses(whitespace: str) -> None:
    """Whitespace-only decisao_pagamento normalizes to '' -> != APROVAR -> refuses (the
    engine's own gateway default routing is not itself a human decision)."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_release_high_value_baseline(decisao_pagamento=whitespace))
    assert excinfo.value.code == ERR_PAYMENT_RELEASE_NOT_HUMAN
    assert "decisao_pagamento" in excinfo.value.message


def test_release_high_value_padded_valid_literal_normalizes_and_releases() -> None:
    """A whitespace-PADDED but otherwise exact literal/id/justificativa normalizes via
    `_norm_str` and still releases (pins the normalization behavior -- this is NOT a bypass,
    it is the documented, intentional consequence of `.strip()`)."""
    result = release_high_value_payment(
        _release_high_value_baseline(
            decisao_pagamento=" APROVAR ",
            aprovador_id=" fin-001 ",
            justificativa_aprovacao=" Contrato emergencial aprovado pelo comite ",
        )
    )
    assert result["pagamento_liberado"] is True
    assert result["tipo_liberacao"] == "humano_alcada"


@pytest.mark.parametrize("decision", ["aprovar", "Aprovar", "APROVAR_X", "XAPROVAR", "RECUSAR "])
def test_release_high_value_non_exact_decisao_literal_still_refuses(decision: str) -> None:
    """Exact-match discipline survives normalization: case variants/substrings of the decision
    literal never satisfy Guard 1."""
    with pytest.raises(PagtoError) as excinfo:
        release_high_value_payment(_release_high_value_baseline(decisao_pagamento=decision))
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
# register_payment_refusal — whitespace-bypass vectors (R1 live-validation finding,
# t2.5-p2b-round2 wave2a: 3 of 12 vectors REGISTERED with whitespace-only accountability
# fields because the guard lacked .strip(), unlike its mirror recurso.register_desistencia).
# Every vector below MUST refuse with ERR_PAYMENT_REFUSAL_NOT_HUMAN — whitespace-only is
# the SAME as absent (ADR-0007: a refusal must carry an identifying human approver).
# ---------------------------------------------------------------


@pytest.mark.parametrize("whitespace", [" ", "   ", "\t", "\n", "\t\n ", "\r\n"])
def test_register_payment_refusal_v4_whitespace_only_aprovador_id_refuses(whitespace: str) -> None:
    """Bypass vector V4 (previously REGISTERED): whitespace-only aprovador_id must refuse."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": "RECUSAR",
                "aprovador_id": whitespace,
                "justificativa_recusa": "Lastro inconsistente — devolver para revisao",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN
    assert "aprovador_id" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "   ", "\t", "\n", "\t\n ", "\r\n"])
def test_register_payment_refusal_v5_whitespace_only_justificativa_refuses(whitespace: str) -> None:
    """Bypass vector V5 (previously REGISTERED): whitespace-only justificativa_recusa must refuse."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": "RECUSAR",
                "aprovador_id": "fin-001",
                "justificativa_recusa": whitespace,
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN
    assert "justificativa_recusa" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "   ", "\t", "\n", "\t\n ", "\r\n"])
def test_register_payment_refusal_v12_both_whitespace_devolver_refuses(whitespace: str) -> None:
    """Bypass vector V12 (previously REGISTERED): DEVOLVER channel + BOTH accountability fields
    whitespace-only must refuse (both missing fields named in the channel-aware message)."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_admissibilidade": "DEVOLVER",
                "aprovador_id": whitespace,
                "justificativa_recusa": whitespace,
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN
    assert "aprovador_id" in excinfo.value.message
    assert "justificativa_recusa" in excinfo.value.message


def test_register_payment_refusal_both_whitespace_alcada_refuses() -> None:
    """V12 variant on the alcada channel: RECUSAR + both fields whitespace-only must refuse."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": "RECUSAR",
                "aprovador_id": " \t ",
                "justificativa_recusa": "\n\n",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_register_payment_refusal_whitespace_only_decision_fields_refuse(whitespace: str) -> None:
    """Whitespace-only decision fields normalize to '' -> neither-channel refusal (the engine's
    gateway DEFAULTS can route here with an unset/omitted decision — fail-safe routing is not a
    human decision)."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": whitespace,
                "decisao_admissibilidade": whitespace,
                "aprovador_id": "fin-001",
                "justificativa_recusa": "x",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN
    assert "nenhum canal humano" in excinfo.value.message


@pytest.mark.parametrize("non_string", [123, True, ["fin-001"], {"id": "fin-001"}, 0.5])
def test_register_payment_refusal_non_string_aprovador_id_refuses(non_string: object) -> None:
    """A NON-string aprovador_id normalizes to '' and refuses — the pre-fix bare truthiness
    check (`if not aprovador_id`) would have silently PASSED a truthy non-string (e.g. 123),
    recording a refusal with a non-identifying approver (same ADR-0007 class as V4)."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": "RECUSAR",
                "aprovador_id": non_string,
                "justificativa_recusa": "Lastro inconsistente",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN
    assert "aprovador_id" in excinfo.value.message


def test_register_payment_refusal_padded_exact_decision_literal_registers() -> None:
    """A whitespace-PADDED but otherwise exact decision literal (' RECUSAR ') normalizes to the
    literal and selects its channel (directive-mandated normalization of decision fields,
    mirroring register_desistencia's strip treatment) — still requiring the stripped
    accountability fields. Case variants/substrings still refuse (next test)."""
    result = register_payment_refusal(
        {
            "decisao_pagamento": " RECUSAR ",
            "aprovador_id": " fin-001 ",
            "justificativa_recusa": " Lastro inconsistente ",
        }
    )
    assert result["recusa_registrada"] is True
    assert result["canal"] == "alcada"


@pytest.mark.parametrize("decision", ["recusar", "Recusar", "RECUSAR_X", "XRECUSAR", "DEVOLVER "])
def test_register_payment_refusal_non_exact_alcada_literal_still_refuses(decision: str) -> None:
    """Exact-match discipline survives normalization: case variants/substrings of the alcada
    literal never select a channel ('DEVOLVER ' here is on the WRONG field — decisao_pagamento —
    and must not cross-satisfy the admissibilidade channel)."""
    with pytest.raises(PagtoError) as excinfo:
        register_payment_refusal(
            {
                "decisao_pagamento": decision,
                "aprovador_id": "fin-001",
                "justificativa_recusa": "x",
            }
        )
    assert excinfo.value.code == ERR_PAYMENT_REFUSAL_NOT_HUMAN


# ---------------------------------------------------------------
# register_pagto_workers — registration + topic-registry drift guard
# ---------------------------------------------------------------


def test_register_pagto_workers_registers_new_topics() -> None:
    """`notify_sla_risk`/`register_payment_refusal` are registered on their exact BPMN-declared
    topics; `prepare_approval_dossier` is now a REAL Andre A2A raw async handler (DL-0033 closed).
    `registered_topics` reflects `_handlers`, which includes raw `harness.register()` handlers, so
    the topic is still present (its absence from the WorkerRegistry is asserted in
    `test_bootstrap_registration.py`)."""
    harness = WorkerHarness(None, worker_id="unit-test-pagto")  # type: ignore[arg-type]
    register_pagto_workers(harness, None, dmn=FakeDmnTransport())
    topics = set(harness.registered_topics)
    assert "operadora.pagto.notify_sla_risk" in topics
    assert "operadora.pagto.register_payment_refusal" in topics
    assert "operadora.pagto.prepare_approval_dossier" in topics
    # RAW async handler (Andre A2A) — populates `_handlers` only, NOT the WorkerRegistry.
    assert harness.registry.get("operadora.pagto.prepare_approval_dossier") is None


# ---------------------------------------------------------------
# prepare_approval_dossier — REAL Andre A2A delegation (raw async handler; DL-0033 closed, DL-0037)
# ---------------------------------------------------------------


class _FakeDossierDispatcher:
    """Records the envelope; returns a programmed `DelegationResult` (or raises)."""

    def __init__(self, result: DelegationResult | None = None, exc: Exception | None = None) -> None:
        self.envelopes: list = []
        self._result = result
        self._exc = exc

    async def delegate(self, envelope) -> DelegationResult:  # noqa: ANN001 — duck-typed fake
        self.envelopes.append(envelope)
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _dossier_task(variables: dict, *, business_key: str = "PAGTO-amh-OP-001") -> ExternalTask:
    """`business_key` is the ENGINE's authoritative key of the RUNNING instance — the handler
    threads it verbatim into the delegation (GK-dossier finding 1a)."""
    return ExternalTask(
        task_id="et-1",
        topic="operadora.pagto.prepare_approval_dossier",
        process_instance_id="pi-1",
        business_key=business_key,
        worker_id="w-1",
        variables=variables,
    )


_PAGTO_DOSSIER_VARS = {
    "tenant_id": "amh",
    "ordem_pagamento_id": "OP-001",
    "tipo_pagamento": "prestador_rede",
    "valor_pagamento_cents": 25_000_000,
    "dados_pagamento_validos": True,
    "lastro_confirmado": True,
    "dentro_teto_l2": False,
    # Never forwarded (not in the pagto allowlist):
    "observacoes_livres": "texto livre com PHI potencial",
}


async def test_prepare_approval_dossier_delegates_to_andre_and_returns_real_outputs() -> None:
    """Dispatcher present -> await delegate -> real dossier outputs: `dossier_prepared=True`, the
    `output_ref` reference, and Andre's agent-produced compact summary AS-IS (`dossier_summary` —
    the SME/PO-pending UT-form seam). The envelope uses the SHARED `analytics.population` type with
    the `pagto-worker` origin (his DEFAULT-flow disambiguator) and the PAGTO business key as
    task_id (Guard 4); free text never rides the seam."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok(
            "PAGTO-amh-OP-001",
            "process://PAGTO-amh-OP-001",
            meta={"route": "human_review", "grupo_destino": "aprovacao-financeira-l1"},
        )
    )
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]

    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))

    assert result is not None
    assert result["dossier_prepared"] is True
    assert result["dossier_ref"] == "process://PAGTO-amh-OP-001"
    assert result["dossier_summary"] == {"route": "human_review", "grupo_destino": "aprovacao-financeira-l1"}
    (envelope,) = dispatcher.envelopes
    assert envelope.task_id == "PAGTO-amh-OP-001"
    assert envelope.task_type == "analytics.population"
    assert envelope.origin == "pagto-worker"
    assert envelope.target == "andre"
    assert envelope.payload_ref == "process://PAGTO-amh-OP-001"
    assert envelope.payload_meta["valor_pagamento_cents"] == "25000000"
    assert envelope.payload_meta["dentro_teto_l2"] == "false"
    assert "observacoes_livres" not in envelope.payload_meta


async def test_prepare_approval_dossier_without_dispatcher_fail_neutrals_with_gap() -> None:
    """DL-0037 degradation posture: dispatcher absent (degraded runtime) -> the task still
    COMPLETES (UT_AprovacaoAlcada must open) with `dossier_prepared=False` + the bounded gap
    token — never a raise, never a fabricated dossier. This is the INTEGRATION-NEUTRAL path (the
    pagto integration suite registers pagto workers WITHOUT a dispatcher: the task still completes,
    unblocking the mechanical path to UT_AprovacaoAlcada exactly as the old stub did)."""
    handler = make_prepare_approval_dossier_handler(None)
    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))
    assert result is not None
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "dispatcher_unavailable"


async def test_prepare_approval_dossier_lote_prestador_business_key_delegates() -> None:
    """The CONTAS-001-adjudicated variant (no ordem) delegates on the lote+prestador business
    key — the SAME derivation `andre.graph._business_key` uses, and the SAME key the running
    instance carries (threaded verbatim from `task.business_key`, GK-dossier finding 1a)."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok("PAGTO-amh-LOTE-9-PREST-3", "process://PAGTO-amh-LOTE-9-PREST-3")
    )
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]
    result = await handler(
        _dossier_task(
            {"tenant_id": "amh", "numero_lote_tiss": "LOTE-9", "prestador_id": "PREST-3"},
            business_key="PAGTO-amh-LOTE-9-PREST-3",
        )
    )
    assert result["dossier_prepared"] is True
    (envelope,) = dispatcher.envelopes
    assert envelope.task_id == "PAGTO-amh-LOTE-9-PREST-3"


async def test_prepare_approval_dossier_threads_the_engine_business_key_verbatim() -> None:
    """GK-dossier finding 1a, PRODUCER half. The running instance is keyed with the contract's
    CONTAS variant while an `ordem_pagamento_id` is ALSO in scope: the ordem-first derivation
    would mint `PAGTO-amh-OP-001`, diverge from the live instance's key, miss Andre's idempotency
    lookup and open a SECOND SP-OP-PAGTO-001 instance. The handler threads `task.business_key`
    verbatim, so the envelope anchors the LIVE case and carries it on to his graph."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok("PAGTO-amh-L9-P3", "process://PAGTO-amh-L9-P3")
    )
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]

    result = await handler(
        _dossier_task(
            {**_PAGTO_DOSSIER_VARS, "numero_lote_tiss": "L9", "prestador_id": "P3"},
            business_key="PAGTO-amh-L9-P3",  # the ENGINE's authoritative key
        )
    )

    assert result["dossier_prepared"] is True
    (envelope,) = dispatcher.envelopes
    assert envelope.task_id == "PAGTO-amh-L9-P3"  # NOT the ordem-first `PAGTO-amh-OP-001`
    assert envelope.payload_ref == "process://PAGTO-amh-L9-P3"
    assert envelope.payload_meta["engine_business_key"] == "PAGTO-amh-L9-P3"


async def test_prepare_approval_dossier_blank_engine_key_falls_back_to_the_derivation() -> None:
    """A blank `task.business_key` (no engine key in scope) is not fatal — the pre-existing
    derivation still produces the anchor, and no `engine_business_key` rides the seam."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok("PAGTO-amh-OP-001", "process://PAGTO-amh-OP-001")
    )
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]
    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS, business_key=""))
    assert result["dossier_prepared"] is True
    (envelope,) = dispatcher.envelopes
    assert envelope.task_id == "PAGTO-amh-OP-001"
    assert "engine_business_key" not in envelope.payload_meta


async def test_prepare_approval_dossier_missing_business_identifiers_gap_never_delegates() -> None:
    """No ordem and no complete lote+prestador (incl. whitespace-only, `non_blank` discipline) ->
    no degenerate PAGTO task_id is ever delegated; gap marker, UT still opens."""
    dispatcher = _FakeDossierDispatcher(result=DelegationResult.ok("x", "process://x"))
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]
    # ordem whitespace-only + lote present but NO prestador -> no complete key -> gap, no delegate.
    result = await handler(
        _dossier_task({"tenant_id": "amh", "ordem_pagamento_id": "  ", "numero_lote_tiss": "L9"})
    )
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "missing_business_identifiers"
    assert dispatcher.envelopes == []


async def test_prepare_approval_dossier_delegation_failure_fail_neutrals_never_raises() -> None:
    """ANY delegation exception -> gap marker + loud log; raw error text stays OUT of the engine
    variables (bounded class token only)."""
    handler = make_prepare_approval_dossier_handler(
        _FakeDossierDispatcher(exc=RuntimeError("pg down: dsn=secret"))  # type: ignore[arg-type]
    )
    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "delegation_failed"
    assert "secret" not in str(result.values())


async def test_prepare_approval_dossier_failure_log_redacts_phi_shaped_error_text() -> None:
    """GK-dossier finding 5: the failure log used raw `str(exc)`. This handler sits downstream of
    PHI-bearing case variables and the exceptions it catches (dispatcher/PG/engine) routinely echo
    the offending payload — a CPF in a driver error would land VERBATIM in the operator log.
    `redact_error_message` (T3.4 F5) scrubs it one-way while preserving the error CLASS."""
    leaky = RuntimeError("insert failed for beneficiario CPF 123.456.789-01 (cns 700123456789012)")
    handler = make_prepare_approval_dossier_handler(
        _FakeDossierDispatcher(exc=leaky)  # type: ignore[arg-type]
    )

    with structlog.testing.capture_logs() as logs:
        result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))

    assert result["dossier_gap"] == "delegation_failed"  # DL-0037 posture unchanged
    (failure_log,) = [e for e in logs if e["event"] == "pagto_prepare_approval_dossier_delegation_failed"]
    logged = failure_log["error"]
    assert "123.456.789-01" not in logged
    assert "700123456789012" not in logged
    assert "[REDACTED_DIGITS]" in logged
    assert logged.startswith("RuntimeError: ")  # the error CLASS survives for ops diagnosis
    # Belt: no PHI-shaped digit run anywhere in the emitted event.
    assert "123.456.789-01" not in str(logs)


class _HangingDossierDispatcher:
    """Never returns — models a wedged dispatcher (pool exhausted, engine/PG unreachable inside
    Andre's graph). Records whether its pending delegation was CANCELLED by the timeout."""

    def __init__(self) -> None:
        self.entered = False
        self.cancelled = False

    async def delegate(self, envelope) -> DelegationResult:  # noqa: ANN001 — duck-typed fake
        self.entered = True
        try:
            await asyncio.Event().wait()  # hangs forever
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")  # pragma: no cover


async def test_prepare_approval_dossier_timeout_fail_neutrals_and_cancels_the_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GK-dossier finding 2: a HUNG dispatcher must never hold the external-task lock to expiry
    (which would let the engine re-deliver the SAME task to another worker alongside this still-
    awaiting one). The bounded `asyncio.wait_for` fires, CANCELS the pending delegation and the
    task COMPLETES with the disclosed `delegation_timeout` gap — DL-0037: the UT still opens, and
    this handler still never raises."""
    monkeypatch.setattr(pagto_module, "_DOSSIER_DELEGATION_TIMEOUT_S", 0.02)
    dispatcher = _HangingDossierDispatcher()
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]

    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))

    assert result == {"dossier_prepared": False, "dossier_gap": "delegation_timeout"}
    assert dispatcher.entered is True
    assert dispatcher.cancelled is True  # nothing is left running behind the return


def test_dossier_delegation_deadline_is_below_the_external_task_lock() -> None:
    """The deadline must stay SAFELY under the harness's 30s lock (`WORKER_LOCK_DURATION_MS`) —
    otherwise the bound buys nothing: the lock would expire first and the engine would re-deliver."""
    lock_ms = inspect.signature(WorkerHarness.__init__).parameters["lock_duration_ms"].default
    assert lock_ms == 30_000  # single-sourced against the harness's own default
    assert 0 < pagto_module._DOSSIER_DELEGATION_TIMEOUT_S <= (lock_ms / 1000) - 5


async def test_prepare_approval_dossier_structured_rejection_gap_bounded_reason() -> None:
    handler = make_prepare_approval_dossier_handler(
        _FakeDossierDispatcher(  # type: ignore[arg-type]
            result=DelegationResult.rejected(
                "PAGTO-amh-OP-001",
                RejectionReason.TASK_TYPE_NOT_ACCEPTED,
                detail="not accepted",
            )
        )
    )
    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))
    assert result["dossier_prepared"] is False
    assert result["dossier_gap"] == "delegation_rejected:task_type_not_accepted"


@pytest.mark.parametrize("token", ["dmn_indisponivel", "engine_inacessivel", "contexto_incompleto"])
async def test_prepare_approval_dossier_discloses_andres_internal_degradation(token: str) -> None:
    """GK-dossier finding 4: the delegation SUCCEEDED structurally but Andre ran degraded inside.
    The dossier exists (`dossier_prepared=True`, `dossier_ref` set) AND the degradation is
    disclosed alongside it as `degraded:<bounded token>` — the human approver must be able to tell
    an enriched dossier from a degraded one."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok(
            "PAGTO-amh-OP-001",
            "process://PAGTO-amh-OP-001",
            meta={"route": "human_review", "degraded": token},
        )
    )
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]

    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))

    assert result["dossier_prepared"] is True  # the dossier DOES exist
    assert result["dossier_ref"] == "process://PAGTO-amh-OP-001"
    assert result["dossier_gap"] == f"degraded:{token}"


async def test_prepare_approval_dossier_clean_success_has_no_gap() -> None:
    """No degradation token (or an empty one) -> no `dossier_gap` at all: the disclosure must not
    fire on the healthy path."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok(
            "PAGTO-amh-OP-001", "process://PAGTO-amh-OP-001", meta={"route": "human_review", "degraded": ""}
        )
    )
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]
    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))
    assert result["dossier_prepared"] is True
    assert "dossier_gap" not in result


async def test_prepare_approval_dossier_unbounded_degradation_token_is_clamped() -> None:
    """Engine-variable hygiene: an unexpected/unbounded token from the target NEVER reaches the
    engine verbatim — it is clamped to `degraded:unknown` (the gap is still disclosed)."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok(
            "PAGTO-amh-OP-001",
            "process://PAGTO-amh-OP-001",
            meta={"degraded": "postgres error: dsn=user:senha@host CPF 123.456.789-01"},
        )
    )
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]
    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))
    assert result["dossier_gap"] == "degraded:unknown"
    assert "senha" not in str(result["dossier_gap"])


async def test_prepare_approval_dossier_never_releases_or_decides() -> None:
    """L0/L1 hard: the dossier INSTRUCTS, never decides — no release/price/decision marker ever
    appears in the completion variables (the release is born SOLELY in UT_AprovacaoAlcada)."""
    dispatcher = _FakeDossierDispatcher(
        result=DelegationResult.ok(
            "PAGTO-amh-OP-001", "process://PAGTO-amh-OP-001", meta={"route": "human_review"}
        )
    )
    handler = make_prepare_approval_dossier_handler(dispatcher)  # type: ignore[arg-type]
    result = await handler(_dossier_task(_PAGTO_DOSSIER_VARS))
    assert "pagamento_liberado" not in result
    assert "decisao_pagamento" not in result
    for value in result.values():
        assert str(value).upper() not in {"APROVAR", "RECUSAR", "CANCELAR"}


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
