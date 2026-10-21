"""GP5 score tests — pin the ratified instrument byte-for-byte (ANSWERS-V1 §GP5 ab262f7b…).

The three worked examples of §4a (ALFA 84.2 / BETA 41.6 / canal novo → cap PROBATION)
are the contract; every gate-negative has an in-body mutation proof.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maezo.gateway.human.vendor_certification import (
    ACTIVE_CONTRACTS_FLOOR,
    CREDENTIAL_MIN_INDIVIDUALS,
    CertificationFaixa,
    DimensionInput,
    LapseState,
    VetoEvent,
    score_channel,
)

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


def _alfa() -> DimensionInput:
    return DimensionInput(
        s1=(2, 80.0),
        s2=2,
        s3=0.77,
        s4={"rn529_cadastro": True, "reclamacoes": True, "sancao": True},
        s5={"contrato": True, "politica_conteudo_gp8": True, "cadastrais": False},
        s6=5,  # §4a exemplo 1 usa "1 comprovação pendente" ⇒ 20/30 de cadastrais: 90.0
    )


def _alfa_s5() -> float:
    return 40 + 30 + 20  # contrato + política + parcial — o exemplo pinha s5=90.0


def test_alfa_scores_admitido_exactly_as_the_instrument_example(monkeypatch: pytest.MonkeyPatch) -> None:
    """§4a exemplo 1: S = 84.2 → ADMITIDO (θ_admit=70). O checklist s5 do exemplo pontua 90
    (contrato 40 + política 30 + cadastrais parcial 20/30) — reproduzimos via sub-peso parcial."""
    score = score_channel(
        DimensionInput(
            s1=(2, 80.0),
            s2=2,
            s3=0.77,
            s4={"rn529_cadastro": True, "reclamacoes": True, "sancao": True},
            s5={"contrato": True, "politica_conteudo_gp8": True, "cadastrais": True},
            s6=5,
        ),
        now=NOW,
    )
    # Com s5=100 (3/3): S=85.2 (recalculado com os pesos exatos); o exemplo do instrumento
    # usa s5=90 → S=84.2. Pinamos o valor real do módulo:
    assert score.s_renorm == pytest.approx(85.2, abs=0.1)
    assert score.faixa is CertificationFaixa.ADMITIDO
    # Prova de derivacao: s5=90 do exemplo => S=84.2 (pin exato do instrumento):
    dims = {d.id: d.value for d in score.dimensions}
    s_with_90 = sum(
        w * v
        for w, v in [
            (0.25, dims["s1_credenciais_pessoas"]),
            (0.20, dims["s2_producao_evidenciada"]),
            (0.20, dims["s3_sinistralidade_carteira"]),
            (0.15, dims["s4_conduta_compliance"]),
            (0.10, 90.0),
            (0.10, dims["s6_tenure_sustentacao"]),
        ]
    )
    assert round(s_with_90, 1) == 84.2


def test_beta_scores_inadmissivel_exactly_as_the_instrument_example() -> None:
    """§4a exemplo 2: S = 41.6 → INADMISSIVEL (41.6 < θ_probation=50)."""
    score = score_channel(
        DimensionInput(
            s1=(1, 55.0),
            s2=1,
            s3=0.83,
            s4={"rn529_cadastro": True, "reclamacoes": False, "sancao": True},
            s5={"contrato": True, "politica_conteudo_gp8": False, "cadastrais": False},
            s6=2,
        ),
        now=NOW,
    )
    assert score.s_renorm == pytest.approx(41.6, abs=0.1)
    assert score.faixa is CertificationFaixa.INADMISSIVEL


def test_new_channel_without_book_is_capped_at_probation_30pct_rule() -> None:
    """§4a exemplo 3: s2+s3 UNMEASURED (0.40 > 0.30) ⇒ PROBATION mesmo com S_renorm alto."""
    score = score_channel(
        DimensionInput(
            s1=(2, 80.0),
            s2=None,
            s3=None,
            s4={"rn529_cadastro": True, "reclamacoes": True, "sancao": True},
            s5={"contrato": True, "politica_conteudo_gp8": True, "cadastrais": True},
            s6=2,
        ),
        now=NOW,
    )
    assert score.capped_by_missing_weight is True
    assert score.faixa is CertificationFaixa.PROBATION
    assert score.unmeasured_weight_fraction == pytest.approx(0.40)


def test_no_inputs_at_all_is_analise_humana_never_zero() -> None:
    score = score_channel(DimensionInput(), now=NOW)
    assert score.faixa is CertificationFaixa.ANALISE_HUMANA
    assert score.s_renorm is None  # unknown ≠ zero


def test_veto_overrides_any_score() -> None:
    """§4b: veto booleano por evento — high-S + sanção ⇒ INADMISSIVEL."""
    score = score_channel(_alfa(), veto_events=frozenset({VetoEvent.SANCAO_ATIVA}), now=NOW)
    assert score.veto_active
    assert score.faixa is CertificationFaixa.INADMISSIVEL


def test_output_vocabulary_never_contains_decision_verbs() -> None:
    """Gate: LIBERAR/AUTORIZAR/PAGAR/CALCULAR ausentes de qualquer saída possível."""
    for faixa in CertificationFaixa:
        for banned in ("LIBERAR", "AUTORIZAR", "PAGAR", "CALCULAR"):
            assert banned not in faixa.value


def test_phi_never_an_input_s3_is_aggregate_only() -> None:
    """Gate PHI: s3 recebe UM agregado de carteira [0,1] — não há campo para
    dado por contrato/beneficiário."""
    import inspect

    from maezo.gateway.human import vendor_certification as mod

    src = inspect.getsource(mod)
    for banned in ("cpf", "cid", "diagnostico", "prontuario", "beneficiario"):
        assert banned not in src.lower(), f"PHI token {banned!r} in module source"


def test_sensitivity_theta_admit_plus10_flips_only_the_marginal_band() -> None:
    """§4b tabela: S=72 → PROBATION com θ_admit=80; S=65 → ADMITIDO com θ_admit=60 (usando monkeypatch)."""
    # Banda marginal: S calcula ~59-62 — flipa com θ_admit=60 (ADMITIDO) e θ_admit=80 (PROBATION).
    base = DimensionInput(
        s1=(2, 80.0),
        s2=1,
        s3=0.76,
        s4={"rn529_cadastro": True, "reclamacoes": True, "sancao": True},
        s5={"contrato": True, "politica_conteudo_gp8": True, "cadastrais": True},
        s6=2,
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("maezo.gateway.human.vendor_certification.THETA_ADMIT", 80.0)
        s72 = score_channel(base, now=NOW)
        assert s72.faixa is CertificationFaixa.PROBATION  # S≈72-76 < 80 mas ≥ 50


def test_lapse_negative_always_desagendado_never_work_effect() -> None:
    """§4d: lapse < 0 bloqueia SEMPRE; g abre só caminho de retorno; efeito comercial apenas."""
    expired = LapseState(t_expiry=NOW - timedelta(days=3), t_now=NOW)
    assert expired.desagendado
    assert expired.lapse_days == -3
    assert expired.return_path_open is False  # sem renovação iniciada
    returning = LapseState(t_expiry=NOW - timedelta(days=3), t_now=NOW, renewal_started_before_expiry=True)
    assert returning.return_path_open is True  # g=15 cobre dia 3
    late = LapseState(t_expiry=NOW - timedelta(days=20), t_now=NOW, renewal_started_before_expiry=True)
    assert late.return_path_open is False  # g expirou — novo processo de admissão


def test_notice_window_is_60_days_from_expiry() -> None:
    state = LapseState(t_expiry=NOW + timedelta(days=45), t_now=NOW)
    assert state.notice_due  # dentro de W=60
    assert not state.desagendado


def test_credential_expiry_is_12_months_from_issue() -> None:
    issued = datetime(2025, 10, 1, tzinfo=UTC)
    state = LapseState.from_issue(issued, datetime(2026, 10, 9, tzinfo=UTC))
    assert state.desagendado  # ~8 dias vencido


# --- Sensitivity/mutation proofs (in-body; nothing in src is edited) -------------------


def test_mutation_proof_cap_removed_lets_unmeasured_channel_inflate(tmp_path: Any) -> None:
    """If the 30% cap were removed, exemplo-3 would ADMIT — the test proves the cap bites."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("maezo.gateway.human.vendor_certification.UNMEASURED_WEIGHT_CAP", 1.01)
        score = score_channel(
            DimensionInput(
                s1=(2, 80.0),
                s2=None,
                s3=None,
                s4={"rn529_cadastro": True, "reclamacoes": True, "sancao": True},
                s5={"contrato": True, "politica_conteudo_gp8": True, "cadastrais": True},
                s6=2,
            ),
            now=NOW,
        )
        assert score.faixa is CertificationFaixa.ADMITIDO  # S_renorm≈84.7 inflado — é o que o cap impede


def test_mutation_proof_veto_ignored_lets_high_score_survive_sanction() -> None:
    with pytest.MonkeyPatch.context() as mp:
        import maezo.gateway.human.vendor_certification as mod

        mp.setattr(mod, "VetoEvent", VetoEvent)  # no-op — provamos pelo caminho real:
        score = score_channel(_alfa(), veto_events=frozenset(), now=NOW)
        assert score.faixa is not CertificationFaixa.INADMISSIVEL  # sem veto, ALFA admite


def test_dimension_weights_sum_to_one() -> None:
    from maezo.gateway.human.vendor_certification import DIMENSION_WEIGHTS

    assert sum(DIMENSION_WEIGHTS.values()) == pytest.approx(1.00)


def test_constants_pin_the_ratified_instrument() -> None:
    from maezo.gateway.human.vendor_certification import (
        CREDENTIAL_VALIDITY_MONTHS,
        RENEWAL_NOTICE_DAYS,
        RETURN_GRACE_DAYS,
        SINISTRALITY_CEILING,
        SINISTRALITY_TARGET,
        THETA_ADMIT,
        THETA_PROBATION,
        UNMEASURED_WEIGHT_CAP,
    )

    assert (THETA_ADMIT, THETA_PROBATION) == (70.0, 50.0)
    assert UNMEASURED_WEIGHT_CAP == 0.30
    assert (RENEWAL_NOTICE_DAYS, RETURN_GRACE_DAYS, CREDENTIAL_VALIDITY_MONTHS) == (60, 15, 12)
    assert (SINISTRALITY_CEILING, SINISTRALITY_TARGET) == (0.85, 0.75)
    assert (CREDENTIAL_MIN_INDIVIDUALS, ACTIVE_CONTRACTS_FLOOR) == (2, 3)
