"""Unit tests for maezo.tools.workers.fraude (SP-OP-FRAUDE-001).

TDD London School: tests verify custody sealing (Merkle), accusation guard (L0-hard),
PHI detection, and inverted scoring (NEVER auto-accusation).
"""

import pytest

from maezo.gateway.custody import CustodyBundle
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnNoResultError, FakeDmnTransport
from maezo.tools.workers.fraude import (
    _SCORING_INPUT_KEYS,
    _SCORING_NUMERIC_KEYS,
    ERR_CUSTODY_NOT_SEALED,
    ERR_FRAUD_ACCUSATION_NOT_HUMAN,
    ERR_PHI_IN_CUSTODY,
    FraudeError,
    _coerce_numeric,
    _collect_scoring_inputs,
    assemble_dossier,
    gather_evidence,
    intake,
    notify_sla_risk,
    publish_completed,
    refer_to_legal,
    register_fraud_accusation,
    register_fraude_workers,
    score_indicators,
    seal_custody_bundle,
    start_contratual,
    start_credenciamento,
)
from maezo.tools.workers.harness import WorkerHarness

# ---------------------------------------------------------------
# intake — neutral start
# ---------------------------------------------------------------


def test_intake_registers_case() -> None:
    result = intake(
        {
            "numero_caso": "FRAUDE-001",
            "origem_encaminhamento": "contas",
            "encaminhado_por_id": "auditor-001",
        }
    )
    assert result["caso_registrado"] is True


# ---------------------------------------------------------------
# gather_evidence
# ---------------------------------------------------------------


def test_gather_evidence_collects_refs() -> None:
    result = gather_evidence(
        {
            "numero_caso": "FRAUDE-001",
            "entidade_tipo": "prestador",
            "entidade_pseudo_id": "pseudo-p-001",
            "evidencia_refs": ["ref-001", "ref-002"],
        }
    )
    assert result["evidencia_refs"] == ["ref-001", "ref-002"]


def test_gather_evidence_empty_refs() -> None:
    result = gather_evidence(
        {
            "numero_caso": "FRAUDE-001",
            "entidade_tipo": "prestador",
            "entidade_pseudo_id": "p-p-001",
            "evidencia_refs": None,
        }
    )
    assert result["evidencia_refs"] == []


# ---------------------------------------------------------------
# score_indicators — engine-side fraude_scoring chain (T2.7 phase 2)
#
# Replaces the deleted `len(evidencia_refs) * 10` heuristic (defect B10). Fixture rows below
# mirror the v1 donor's own characterization payload (tests/unit/workers/test_fraude_guards.py,
# `test_score_indicators_computa_score_via_dmns_nunca_ecoa`), which is itself verified live
# against the compose engine's deployed `fraude_scoring/*` tables (see this PR's characterization
# table + `tests/integration/dmn/test_fraude_scoring_chain.py`).
# ---------------------------------------------------------------

# The 7 fraude_scoring/* decision ids, in the exact evaluation order `score_indicators` uses.
_ALL_SCORING_DECISIONS = (
    "risk_thresholds",
    "upcoding_complexity_ceiling",
    "frequency_zscore_threshold",
    "phantom_no_diagnosis",
    "phantom_suspicious_prefix",
    "provider_peer_deviation",
    "unbundling_partial_bundles",
)


def _full_scoring_fake(
    *,
    risk_score: int = 30,
    upcoding: int = 0,
    freq: int = 0,
    phantom_no_dx: int = 0,
    phantom_prefix: int = 0,
    peer_dev: int = 0,
    unbundling: int = 0,
) -> FakeDmnTransport:
    """A FakeDmnTransport with all 7 tables registered — no accidental DmnEvaluationError."""
    fake = FakeDmnTransport()
    fake.register("risk_thresholds", [{"indicador_score": risk_score, "indicador_label": "none"}])
    fake.register("upcoding_complexity_ceiling", [{"indicador_score": upcoding, "indicador_label": "none"}])
    fake.register("frequency_zscore_threshold", [{"indicador_score": freq, "indicador_label": "none"}])
    fake.register("phantom_no_diagnosis", [{"indicador_score": phantom_no_dx, "indicador_label": "none"}])
    fake.register(
        "phantom_suspicious_prefix", [{"indicador_score": phantom_prefix, "indicador_label": "none"}]
    )
    fake.register("provider_peer_deviation", [{"indicador_score": peer_dev, "indicador_label": "none"}])
    fake.register("unbundling_partial_bundles", [{"indicador_score": unbundling, "indicador_label": "none"}])
    return fake


def test_score_indicators_dmn_unwired_raises_dmn_evaluation_error() -> None:
    """Fail-closed: no dmn= seam -> DmnEvaluationError (transient, engine retry) — NEVER a
    zero-score-and-continue fallback."""
    with pytest.raises(DmnEvaluationError):
        score_indicators({"evidencia_refs": []}, dmn=None)


def test_score_indicators_aggregates_via_dmn_chain_never_placeholder() -> None:
    """score_indicadores is the SUM of indicador_score across all 7 tables — computed by the
    DMNs, never `len(evidencia_refs) * 10` (the deleted placeholder would give 0 here for 8 refs
    with no evidence signals attached; the DMN chain gives 120 regardless of evidencia_refs)."""
    fake = FakeDmnTransport()
    fake.register("risk_thresholds", [{"indicador_score": 30, "indicador_label": "risk_thresholds_alto"}])
    fake.register(
        "upcoding_complexity_ceiling",
        [{"indicador_score": 30, "indicador_label": "upcoding_complexity_ceiling"}],
    )
    fake.register(
        "frequency_zscore_threshold",
        [{"indicador_score": 20, "indicador_label": "frequency_zscore_threshold"}],
    )
    fake.register(
        "phantom_no_diagnosis", [{"indicador_score": 40, "indicador_label": "phantom_no_diagnosis"}]
    )
    fake.register("phantom_suspicious_prefix", [{"indicador_score": 0, "indicador_label": "none"}])
    fake.register("provider_peer_deviation", [{"indicador_score": 0, "indicador_label": "none"}])
    fake.register("unbundling_partial_bundles", [{"indicador_score": 0, "indicador_label": "none"}])

    variables = {
        "numero_caso": "FRAUDE-001",
        "evidencia_refs": [f"ref-{i}" for i in range(8)],  # placeholder would have scored 80
        "risk_score": 85,
        "encounter_class": "ambulatorio",
        "code_tier": 3,
        "z_score": 2.5,
        "has_tuss_codes": True,
        "has_cid10_codes": False,
    }
    result = score_indicators(variables, dmn=fake)

    assert result["score_indicadores"] == 120, "30+30+20+40 — the 3 zero-score tables contribute 0"
    assert result["score_indicadores"] != 80, "never the deleted len(evidencia_refs)*10 placeholder"
    assert result["indicadores_presentes"] == [
        "risk_thresholds_alto",
        "upcoding_complexity_ceiling",
        "frequency_zscore_threshold",
        "phantom_no_diagnosis",
    ]
    assert "intensidade_investigacao" not in result, (
        "intensidade_investigacao is fraude_indicadores' output — an engine-native "
        "businessRuleTask downstream, never computed by this worker"
    )
    assert set(result["dmn_versions"]) == set(_ALL_SCORING_DECISIONS)

    # All 7 tables evaluated, in order, each receiving ONLY the evidence signals (never
    # identity/case variables like numero_caso or evidencia_refs).
    assert [c[0] for c in fake.calls] == list(_ALL_SCORING_DECISIONS)
    expected_evidence = {
        "risk_score": 85,
        "encounter_class": "ambulatorio",
        "code_tier": 3,
        "z_score": 2.5,
        "has_tuss_codes": True,
        "has_cid10_codes": False,
    }
    for _decision_id, passed_vars in fake.calls:
        assert passed_vars == expected_evidence
        assert "numero_caso" not in passed_vars
        assert "evidencia_refs" not in passed_vars


def test_score_indicators_none_label_excluded() -> None:
    """indicador_label == 'none' is the ABSENCE of an indicator — never collected."""
    fake = _full_scoring_fake()  # risk_score=30 (default), all other tables score 0
    result = score_indicators({"risk_score": 10}, dmn=fake)
    assert result["score_indicadores"] == 30  # sum of the fixture's registered rows
    assert result["indicadores_presentes"] == []


def test_score_indicators_deduplicates_labels() -> None:
    """The same label from two different tables is collected only once (order-preserving)."""
    fake = FakeDmnTransport()
    for decision_id in _ALL_SCORING_DECISIONS:
        fake.register(decision_id, [{"indicador_score": 5, "indicador_label": "indicador_repetido"}])
    result = score_indicators({}, dmn=fake)
    assert result["indicadores_presentes"] == ["indicador_repetido"]
    assert result["score_indicadores"] == 35


def test_score_indicators_bool_score_never_summed() -> None:
    """A stray boolean indicador_score (bool is an int subclass in Python) is never summed."""
    fake = _full_scoring_fake()
    fake.register("risk_thresholds", [{"indicador_score": True, "indicador_label": "none"}])
    result = score_indicators({}, dmn=fake)
    assert result["score_indicadores"] == 0


def test_score_indicators_boolean_evidence_coerced_from_string() -> None:
    """has_tuss_codes/has_cid10_codes accept engine string 'true'/'false' (mirrors contas._is_true).

    Every table receives the SAME collected evidence dict (no per-table filtering — see
    `_evaluate_scoring_chain` docstring), so the coercion is visible on every call.
    """
    fake = _full_scoring_fake()
    variables = {"has_tuss_codes": "true", "has_cid10_codes": "false"}
    score_indicators(variables, dmn=fake)
    assert len(fake.calls) == len(_ALL_SCORING_DECISIONS)
    for _decision_id, passed_vars in fake.calls:
        assert passed_vars["has_tuss_codes"] is True
        assert passed_vars["has_cid10_codes"] is False


# ---------------------------------------------------------------
# T1.5 DMN-input hardening: numeric coerce-or-drop (fail-closed) + tuss_codes drop
# ---------------------------------------------------------------


def test_coerce_numeric_valid_values_typed_to_declared_typeref() -> None:
    """A valid numeric coerces to the DMN-declared type: integer -> int, double -> float."""
    # integer typeRef inputs (risk_score/code_tier/provider_volume)
    assert _coerce_numeric(85, "int") == (True, 85)
    assert _coerce_numeric("85", "int") == (True, 85)
    assert _coerce_numeric(80.0, "int") == (True, 80)  # integral float -> int
    assert _coerce_numeric("80.0", "int") == (True, 80)  # integral decimal string -> int
    # double typeRef inputs (z_score/deviation_pct)
    ok, val = _coerce_numeric(2.5, "double")
    assert ok and isinstance(val, float) and val == 2.5
    ok, val = _coerce_numeric("2.5", "double")
    assert ok and isinstance(val, float) and val == 2.5
    ok, val = _coerce_numeric(60, "double")  # int coerces UP to float for a double input
    assert ok and isinstance(val, float) and val == 60.0


def test_coerce_numeric_non_coercible_fails_closed() -> None:
    """A non-numeric value fails closed to (False, None) — the caller then OMITS the key so NO
    wrong-typed value reaches the DMN (its missing-input catch-all row handles the absence)."""
    for bad in ("abc", "", "  ", "8o", None, [30], {"v": 1}, object()):
        assert _coerce_numeric(bad, "int") == (False, None), bad
        assert _coerce_numeric(bad, "double") == (False, None), bad
    # bool is an int subclass but is NEVER a numeric fraud signal
    assert _coerce_numeric(True, "int") == (False, None)
    assert _coerce_numeric(False, "double") == (False, None)
    # NaN / inf never valid
    assert _coerce_numeric(float("nan"), "double") == (False, None)
    assert _coerce_numeric(float("inf"), "double") == (False, None)
    # a FRACTIONAL value for an integer input fails closed (never int("80.5") -> 80, which could
    # silently flip a threshold rule) — strictly worse than fail-closed
    assert _coerce_numeric(80.5, "int") == (False, None)
    assert _coerce_numeric("80.5", "int") == (False, None)


def test_coerce_numeric_int_input_preserves_precision_past_2_pow_53() -> None:
    """A large integer input is typed WITHOUT a float round-trip (no precision loss)."""
    big = 2**53 + 1  # not representable as a float
    assert _coerce_numeric(big, "int") == (True, big)


def test_collect_scoring_inputs_coerces_each_numeric_field() -> None:
    """Every numeric signal is coerced to its declared type; a valid numeric string is typed."""
    evidence = _collect_scoring_inputs(
        {
            "risk_score": "85",  # integer typeRef
            "code_tier": "3",  # integer typeRef
            "provider_volume": 15,  # integer typeRef
            "z_score": "2.5",  # double typeRef
            "deviation_pct": 60,  # double typeRef (int -> float)
            "encounter_class": "ambulatorio",  # string pass-through, untouched
        }
    )
    assert evidence["risk_score"] == 85 and type(evidence["risk_score"]) is int
    assert evidence["code_tier"] == 3 and type(evidence["code_tier"]) is int
    assert evidence["provider_volume"] == 15 and type(evidence["provider_volume"]) is int
    assert evidence["z_score"] == 2.5 and type(evidence["z_score"]) is float
    assert evidence["deviation_pct"] == 60.0 and type(evidence["deviation_pct"]) is float
    assert evidence["encounter_class"] == "ambulatorio"  # string input forwarded unchanged


def test_collect_scoring_inputs_drops_non_numeric_fields_no_wrong_typed_passthrough() -> None:
    """A non-numeric value on any numeric field is DROPPED (never passed wrong-typed to the DMN)."""
    evidence = _collect_scoring_inputs(
        {
            "risk_score": "not-a-number",
            "code_tier": [3],  # list — never repr-cast
            "z_score": "abc",
            "deviation_pct": {"value": 60.0},  # engine-shaped dict is not a decoded numeric here
            "provider_volume": True,  # bool never a numeric signal
        }
    )
    for key in _SCORING_NUMERIC_KEYS:
        assert key not in evidence, f"{key} must be dropped, never passed wrong-typed to the DMN"


def test_collect_scoring_inputs_never_forwards_tuss_codes() -> None:
    """`tuss_codes` is dropped from the scoring inputs entirely — never str()/repr-cast.

    It is a list-per-convention field bound to a `string`-declared DMN input whose column is `-`
    (wildcard) in every rule; forwarding `str(["30101012"]) -> "['30101012']"` would silently flip
    any future `starts with(...)` rule. It is deliberately absent from `_SCORING_INPUT_KEYS`.
    """
    assert "tuss_codes" not in _SCORING_INPUT_KEYS
    evidence = _collect_scoring_inputs({"tuss_codes": ["30101012", "30101020"], "risk_score": 10})
    assert "tuss_codes" not in evidence
    # and the repr-cast footgun never appears anywhere in the forwarded evidence
    assert "['30101012'" not in repr(evidence)


def test_score_indicators_drops_non_numeric_end_to_end_no_wrong_type_to_dmn() -> None:
    """End-to-end: a garbage risk_score never reaches any of the 7 DMN calls (fail-closed omit)."""
    fake = _full_scoring_fake()
    score_indicators({"risk_score": "garbage", "tuss_codes": ["30101012"]}, dmn=fake)
    assert len(fake.calls) == len(_ALL_SCORING_DECISIONS)
    for _decision_id, passed_vars in fake.calls:
        assert "risk_score" not in passed_vars, "non-numeric risk_score must be dropped"
        assert "tuss_codes" not in passed_vars, "tuss_codes must never be forwarded"


def test_score_indicators_one_table_unavailable_fails_closed_no_partial_score() -> None:
    """DIVERGENCE FROM v1 DONOR (deliberate, root-caused — see fraude.py docstring): the donor
    SKIPPED an unavailable table and summed the rest. This worker propagates DmnEvaluationError
    for ANY unavailable table — no partial aggregation, no fabricated fallback score."""
    fake = FakeDmnTransport()
    fake.register("risk_thresholds", [{"indicador_score": 30, "indicador_label": "risk_thresholds_alto"}])
    fake.register(
        "phantom_no_diagnosis", [{"indicador_score": 40, "indicador_label": "phantom_no_diagnosis"}]
    )
    # The other 5 tables are NOT registered -> FakeDmnTransport raises DmnEvaluationError.
    with pytest.raises(DmnEvaluationError):
        score_indicators({"risk_score": 85}, dmn=fake)


def test_score_indicators_empty_result_raises_dmn_no_result_error() -> None:
    """A table returning zero rows (should not happen — every table has a catch-all — but
    defense-in-depth per ADR-0028 §3) raises DmnNoResultError, never an implicit zero score."""
    fake = FakeDmnTransport()
    fake.register("risk_thresholds", [])  # empty result
    with pytest.raises(DmnNoResultError):
        score_indicators({"risk_score": 85}, dmn=fake)


def test_score_indicators_never_echoes_inbound_score() -> None:
    """A forged/stale score_indicadores or indicadores_presentes already present on process
    variables (e.g. from a re-delivered task) is NEVER echoed — always recomputed from the DMNs."""
    fake = _full_scoring_fake(risk_score=0)
    variables = {
        "score_indicadores": 999,
        "indicadores_presentes": ["indicador_forjado_inbound"],
    }
    result = score_indicators(variables, dmn=fake)
    assert result["score_indicadores"] != 999
    assert "indicador_forjado_inbound" not in result["indicadores_presentes"]


def test_score_indicators_never_produces_verdict_keys() -> None:
    """Grep-level L0 guard: no accusation/verdict vocabulary ever appears in the worker's output."""
    fake = _full_scoring_fake(risk_score=85, upcoding=30, freq=20, phantom_no_dx=40)
    result = score_indicators({"risk_score": 85}, dmn=fake)
    forbidden = {"FRAUD_DETECTED", "ACUSAR", "ACUSAR_FRAUDE", "BLOQUEAR", "CONFIRMAR"}
    assert not (forbidden & set(result.keys()))
    for value in result.values():
        if isinstance(value, str):
            assert value.upper() not in forbidden
        if isinstance(value, list):
            assert not any(str(v).upper() in forbidden for v in value)


# ---------------------------------------------------------------
# assemble_dossier
# ---------------------------------------------------------------


def test_assemble_dossier() -> None:
    result = assemble_dossier(
        {
            "numero_caso": "F-001",
            "evidencia_refs": ["ref-a", "ref-b"],
            "indicadores_presentes": ["evidencia_presente"],
        }
    )
    assert result["dossie_montado"] is True
    assert result["dossie_items"] == 2


# ---------------------------------------------------------------
# seal_custody_bundle — Merkle seal
# ---------------------------------------------------------------


def test_fraud_seal_custody() -> None:
    """Seal evidence bundle produces deterministic Merkle root."""
    evidencia_refs = ["ref-001", "ref-002", "ref-003"]
    result = seal_custody_bundle(
        {
            "numero_caso": "F-001",
            "evidencia_refs": evidencia_refs,
        }
    )
    assert result["custody_sealed"] is True
    assert result["bundle_root"] is not None
    assert len(result["bundle_root"]) == 64
    assert result["record_count"] == 3

    # Verify it's a valid Merkle root
    expected = CustodyBundle.seal_bundle(evidencia_refs)
    assert result["bundle_root"] == expected


def test_fraud_seal_custody_tamper_evident() -> None:
    """Different evidence produces different Merkle roots."""
    refs_a = ["ref-a-1", "ref-a-2"]
    refs_b = ["ref-b-1", "ref-b-2"]

    root_a = seal_custody_bundle({"evidencia_refs": refs_a})["bundle_root"]
    root_b = seal_custody_bundle({"evidencia_refs": refs_b})["bundle_root"]

    assert root_a != root_b


def test_fraud_seal_custody_rejects_phi() -> None:
    """Seal must reject evidence references containing PHI markers."""
    with pytest.raises(FraudeError) as excinfo:
        seal_custody_bundle(
            {
                "evidencia_refs": ["ref-ok", "ref-com-cpf-123"],
            }
        )
    assert excinfo.value.code == ERR_PHI_IN_CUSTODY


def test_fraud_seal_custody_empty() -> None:
    result = seal_custody_bundle({"evidencia_refs": []})
    assert result["custody_sealed"] is True
    assert len(result["bundle_root"]) == 64


# ---------------------------------------------------------------
# register_fraud_accusation — L0-hard GUARD
# ---------------------------------------------------------------


def test_fraud_accusation_guard_happy_path() -> None:
    refs = ["ref-001", "ref-002"]
    bundle_root = CustodyBundle.seal_bundle(refs)

    result = register_fraud_accusation(
        {
            "decisao_fraude": "ACUSAR_FRAUDE",
            "investigator_id": "inv-001",
            "tier": "senior",
            "fundamentacao_investigacao": "Evidencia de upcoding sistematico",
            "indicadores_fundamentantes": ["upcoding_pattern", "frequency_deviation"],
            "referencia_normativa": "RN 593, Lei 9656 art. 13",
            "destino_referral": {"juridico": True, "ans": True},
            "bundle_root": bundle_root,
            "evidencia_refs": refs,
        }
    )
    assert result["acusacao_registrada"] is True
    assert result["bundle_root_verificado"] == bundle_root


def test_fraud_accusation_guard_rejects_arquivar() -> None:
    """Arquivar should NOT pass the accusation guard."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "ARQUIVAR",
                "investigator_id": "inv-001",
                "tier": "senior",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"juridico": True},
                "bundle_root": "fake",
                "evidencia_refs": [],
            }
        )
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN


def test_no_auto_accusation_monitorar() -> None:
    """Monitorar should NOT trigger accusation — strictly human-gated."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "MONITORAR",
                "investigator_id": "inv-001",
                "tier": "x",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"ans": True},
                "bundle_root": "fake",
                "evidencia_refs": [],
            }
        )
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN


def test_fraud_accusation_guard_custody_not_sealed() -> None:
    """Missing bundle_root should trigger ERR_CUSTODY_NOT_SEALED (pure custody failure)."""
    refs = ["ref-001"]
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "ACUSAR_FRAUDE",
                "investigator_id": "inv-001",
                "tier": "senior",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"juridico": True},
                "bundle_root": "",
                "evidencia_refs": refs,
            }
        )
    assert excinfo.value.code == ERR_CUSTODY_NOT_SEALED


def test_fraud_accusation_guard_tampered_bundle() -> None:
    """Tampered evidence (bundle_root mismatch) triggers custody failure."""
    original_refs = ["ref-001", "ref-002"]
    bundle_root = CustodyBundle.seal_bundle(original_refs)
    tampered_refs = ["ref-001", "ref-002", "ref-003"]  # added ref

    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "ACUSAR_FRAUDE",
                "investigator_id": "inv-001",
                "tier": "senior",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"juridico": True},
                "bundle_root": bundle_root,
                "evidencia_refs": tampered_refs,
            }
        )
    assert excinfo.value.code == ERR_CUSTODY_NOT_SEALED


def test_fraud_accusation_guard_missing_investigator() -> None:
    """Missing investigator_id should be human-gate failure."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            {
                "decisao_fraude": "ACUSAR_FRAUDE",
                "investigator_id": "",
                "tier": "senior",
                "fundamentacao_investigacao": "x",
                "indicadores_fundamentantes": ["x"],
                "referencia_normativa": "x",
                "destino_referral": {"juridico": True},
                "bundle_root": "fake",
                "evidencia_refs": [],
            }
        )
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN


# ---------------------------------------------------------------
# register_fraud_accusation — whitespace-bypass vectors (t3.1-guard-input-hardening,
# closing the #133-audit-flagged gap at fraude.py:503,505,507,511: the guard's original bare
# `if not investigator_id` / `if not tier` / `if not fundamentacao` / `if not ref_normativa`
# checks let WHITESPACE-ONLY decision + accountability fields through Guard 1 -- the same
# class the c1377fa fix closed for pagto.register_payment_refusal. L0-ADJACENT: the L0
# invariant is that a fraud accusation NEVER registers without a genuine human decision behind
# it. Every vector below MUST refuse with ERR_FRAUD_ACCUSATION_NOT_HUMAN -- whitespace-only is
# the SAME as absent (ADR-0007: an accusation must carry an identifying human investigator +
# a real justification).
# ---------------------------------------------------------------

_WHITESPACE_VARIANTS = [" ", "   ", "\t", "\n", "\t\n ", "\r\n"]
_NON_STRING_VARIANTS: list[object] = [123, True, 0.5, ["x"], {"k": "v"}]


def _fraud_accusation_baseline(**overrides: object) -> dict[str, object]:
    """Happy-path baseline for register_fraud_accusation -- custody sealed + verifiable so any
    guard failure observed in a test is attributable ONLY to the field under test (Guard 1,
    ERR_FRAUD_ACCUSATION_NOT_HUMAN -- never Guard 2's ERR_CUSTODY_NOT_SEALED)."""
    refs = ["ref-001", "ref-002"]
    bundle_root = CustodyBundle.seal_bundle(refs)
    base: dict[str, object] = {
        "decisao_fraude": "ACUSAR_FRAUDE",
        "investigator_id": "inv-001",
        "tier": "senior",
        "fundamentacao_investigacao": "Evidencia de upcoding sistematico",
        "indicadores_fundamentantes": ["upcoding_pattern", "frequency_deviation"],
        "referencia_normativa": "RN 593, Lei 9656 art. 13",
        "destino_referral": {"juridico": True, "ans": True},
        "bundle_root": bundle_root,
        "evidencia_refs": refs,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fraud_accusation_whitespace_only_investigator_id_refuses(whitespace: str) -> None:
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(investigator_id=whitespace))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "investigator_id" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fraud_accusation_non_string_investigator_id_refuses(non_string: object) -> None:
    """A NON-string investigator_id normalizes to '' and refuses -- the pre-fix bare
    truthiness check (`if not investigator_id`) would have silently PASSED a truthy
    non-string (e.g. 123), registering an L0 fraud accusation with a non-identifying
    investigator."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(investigator_id=non_string))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "investigator_id" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fraud_accusation_whitespace_only_tier_refuses(whitespace: str) -> None:
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(tier=whitespace))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "tier" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fraud_accusation_non_string_tier_refuses(non_string: object) -> None:
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(tier=non_string))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "tier" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fraud_accusation_whitespace_only_fundamentacao_refuses(whitespace: str) -> None:
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(fundamentacao_investigacao=whitespace))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "fundamentacao_investigacao" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fraud_accusation_non_string_fundamentacao_refuses(non_string: object) -> None:
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(fundamentacao_investigacao=non_string))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "fundamentacao_investigacao" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fraud_accusation_whitespace_only_referencia_normativa_refuses(whitespace: str) -> None:
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(referencia_normativa=whitespace))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "referencia_normativa" in excinfo.value.message


@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_fraud_accusation_non_string_referencia_normativa_refuses(non_string: object) -> None:
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(referencia_normativa=non_string))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "referencia_normativa" in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fraud_accusation_both_investigator_and_fundamentacao_whitespace_refuses(
    whitespace: str,
) -> None:
    """Both investigator_id AND fundamentacao_investigacao whitespace-only -- both missing
    fields named in the guard's error message."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(
            _fraud_accusation_baseline(investigator_id=whitespace, fundamentacao_investigacao=whitespace)
        )
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "investigator_id" in excinfo.value.message
    assert "fundamentacao_investigacao" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_fraud_accusation_whitespace_only_decisao_refuses(whitespace: str) -> None:
    """Whitespace-only decisao_fraude normalizes to '' -> != ACUSAR_FRAUDE -> refuses (the
    engine's own gateway default routing is not itself a human decision)."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(decisao_fraude=whitespace))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "decisao_fraude" in excinfo.value.message


def test_fraud_accusation_padded_valid_literal_normalizes_and_registers() -> None:
    """Whitespace-PADDED but otherwise exact literal/fields normalize via `_norm_str` and
    still register (pins the normalization behavior -- this is NOT a bypass, it is the
    documented, intentional consequence of `.strip()`)."""
    result = register_fraud_accusation(
        _fraud_accusation_baseline(
            decisao_fraude=" ACUSAR_FRAUDE ",
            investigator_id=" inv-001 ",
            tier=" senior ",
            fundamentacao_investigacao=" Evidencia de upcoding sistematico ",
            referencia_normativa=" RN 593, Lei 9656 art. 13 ",
        )
    )
    assert result["acusacao_registrada"] is True


@pytest.mark.parametrize(
    "decision",
    ["acusar_fraude", "Acusar_Fraude", "ACUSAR_FRAUDE_X", "XACUSAR_FRAUDE", "MONITORAR "],
)
def test_fraud_accusation_non_exact_decisao_literal_still_refuses(decision: str) -> None:
    """Exact-match discipline survives normalization: case variants/substrings of the decision
    literal never satisfy Guard 1 (zero_auto_accusation -- L0 principle Rafael/Beatriz)."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(decisao_fraude=decision))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN


# ---------------------------------------------------------------
# register_fraud_accusation — indicadores_fundamentantes ELEMENT-level vectors
# (t3.1-guard-input-hardening follow-up): the contract defines the list's elements as
# accountability-bearing evidence citations (SP-OP-FRAUDE-001.md:91 — "quais indicadores do
# dossie sustentam a acusacao (citacao de evidencia, ADR-0007 decision_basis)"). A list of
# whitespace-only/non-string "citations" names NO indicator and must refuse exactly like an
# empty list — the pre-fix len()>0 check accepted ["   "] as a present citation.
# ---------------------------------------------------------------


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_fraud_accusation_whitespace_only_indicador_element_refuses(whitespace: str) -> None:
    """A single whitespace-only element normalizes away -> empty list -> refuses with the
    existing 'ausente/vazio' message (same error class, same message -- element-level
    normalization only)."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(indicadores_fundamentantes=[whitespace]))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "indicadores_fundamentantes" in excinfo.value.message


@pytest.mark.parametrize(
    "elements",
    [
        [" ", "\t", "\n"],
        ["   ", "   "],
        [123, True, 0.5],
        [None, [], {}],
        [" ", 123],
    ],
)
def test_fraud_accusation_all_empty_or_non_string_indicador_elements_refuse(
    elements: list[object],
) -> None:
    """Lists whose EVERY element is whitespace-only or non-string normalize to [] and refuse
    -- no fake citation ever counts toward the ADR-0007 decision_basis."""
    with pytest.raises(FraudeError) as excinfo:
        register_fraud_accusation(_fraud_accusation_baseline(indicadores_fundamentantes=elements))
    assert excinfo.value.code == ERR_FRAUD_ACCUSATION_NOT_HUMAN
    assert "indicadores_fundamentantes" in excinfo.value.message


def test_fraud_accusation_mixed_indicador_elements_keep_genuine_citation_and_register() -> None:
    """One GENUINE citation among whitespace/non-string noise still registers (the genuine
    element survives normalization; noise is dropped) -- pins that element normalization is
    a filter, not a rejection of the whole list."""
    result = register_fraud_accusation(
        _fraud_accusation_baseline(indicadores_fundamentantes=[" ", "upcoding_pattern", 123, "\t"])
    )
    assert result["acusacao_registrada"] is True


def test_fraud_accusation_padded_indicador_element_normalizes_and_registers() -> None:
    """A whitespace-PADDED but otherwise genuine citation normalizes via strip and registers
    (mirrors the scalar-field padded-literal pin)."""
    result = register_fraud_accusation(
        _fraud_accusation_baseline(indicadores_fundamentantes=[" upcoding_pattern "])
    )
    assert result["acusacao_registrada"] is True


# ---------------------------------------------------------------
# notify_sla_risk — informational, never adverse (t2.5-p2b-round2)
# ---------------------------------------------------------------


def test_notify_sla_risk_informational() -> None:
    result = notify_sla_risk({"numero_caso": "F-001", "tenant_id": "amh"})
    assert result["sla_risk_notified"] is True
    assert result["grupo_alertado"] == "coordenacao-investigacao"
    assert result["numero_caso"] == "F-001"


def test_notify_sla_risk_no_adverse_outcome() -> None:
    """The non-interruptive timer alert never produces or propagates an adverse decision.

    UT_DecisaoInvestigador stays open (cancelActivity=false) — score/SLA risk NEVER accuses;
    only the human decision at UT_DecisaoInvestigador (register_fraud_accusation's guard) does.
    """
    result = notify_sla_risk({"numero_caso": "F-001", "decisao_fraude": "ACUSAR_FRAUDE"})
    assert "decisao_fraude" not in result
    forbidden = {"ACUSAR_FRAUDE", "ACUSAR", "BLOQUEAR", "FRAUD_DETECTED"}
    for value in result.values():
        assert str(value).upper() not in forbidden


def test_notify_sla_risk_missing_numero_caso_defaults_empty() -> None:
    """Missing `numero_caso` degrades gracefully (no KeyError) — fail-safe, never adverse."""
    result = notify_sla_risk({})
    assert result["sla_risk_notified"] is True
    assert result["numero_caso"] == ""


# ---------------------------------------------------------------
# register_fraude_workers — registration + topic-registry drift guard
# ---------------------------------------------------------------


def test_register_fraude_workers_registers_notify_sla_risk() -> None:
    """`notify_sla_risk` is registered on the exact BPMN-declared topic (ST_NotifySlaRisk,
    SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn:247-248) -- closes the prior registry-drift gap
    (see tests/integration/processes/test_sp_op_fraude_001.py::
    test_bpmn_fraude_topics_vs_registered_workers)."""
    harness = WorkerHarness(None, worker_id="unit-test-fraude")  # type: ignore[arg-type]
    register_fraude_workers(harness, None, dmn=FakeDmnTransport())
    topics = set(harness.registered_topics)
    assert "operadora.fraude.notify_sla_risk" in topics
    assert "operadora.fraude.intake" in topics
    assert "operadora.fraude.register_fraud_accusation" in topics
    # 10 spec-declared operadora.fraude.* topics + the documented orphan publish_completed
    # (ACCEPT — folds into the generic events.publish task per BPMN, no distinct spec topic).
    fraude_topics = {t for t in topics if t.startswith("operadora.fraude.")}
    assert len(fraude_topics) == 11


# ---------------------------------------------------------------
# refer_to_legal
# ---------------------------------------------------------------


def test_refer_to_legal() -> None:
    result = refer_to_legal(
        {
            "numero_caso": "F-001",
            "destino_referral": {"juridico": True, "ans": True},
        }
    )
    assert result["referral_executado"] is True


# ---------------------------------------------------------------
# start_credenciamento
# ---------------------------------------------------------------


def test_start_credenciamento() -> None:
    result = start_credenciamento({"prestador_id": "P-001"})
    assert result["handoff_credenciamento"] is True
    assert result["processo_destino"] == "SP-OP-CRED-001"


# ---------------------------------------------------------------
# start_contratual
# ---------------------------------------------------------------


def test_start_contratual() -> None:
    result = start_contratual({"numero_contrato": "C-001"})
    assert result["handoff_contratual"] is True
    assert "CANCEL-001" in result["processo_destino"]


# ---------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------


def test_publish_completed_arquivado() -> None:
    result = publish_completed({"decisao_fraude": "ARQUIVAR"})
    assert result["desfecho"] == "arquivado_sem_indicio"


def test_publish_completed_acusado() -> None:
    result = publish_completed({"decisao_fraude": "ACUSAR_FRAUDE"})
    assert result["desfecho"] == "fraude_confirmada_humano"
