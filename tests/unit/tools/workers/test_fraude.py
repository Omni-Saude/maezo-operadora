"""Unit tests for maezo.tools.workers.fraude (SP-OP-FRAUDE-001).

TDD London School: tests verify custody sealing (Merkle), accusation guard (L0-hard),
PHI detection, and inverted scoring (NEVER auto-accusation).
"""

import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from maezo.gateway.custody import CustodyBundle
from maezo.tools.mcp_cibseven.transport import (
    FakeCibSevenTransport,
    ProcessInstance,
    StartDedupGateUnavailableError,
    StartDedupPosture,
    start_dedup_key,
    start_dedup_posture,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnNoResultError, FakeDmnTransport
from maezo.tools.workers.fraude import (
    _SCORING_DECISIONS,
    _SCORING_INPUT_KEYS,
    _SCORING_NUMERIC_KEYS,
    CANCEL_PROCESS_KEY,
    CRED_PROCESS_KEY,
    ERR_CUSTODY_NOT_SEALED,
    ERR_FRAUD_ACCUSATION_NOT_HUMAN,
    ERR_FRAUDE_HANDOFF_SEM_ALVO,
    ERR_PHI_IN_CUSTODY,
    GAP_BEATRIZ_A2A_NAO_LIGADO,
    GAP_REFERRAL_JURIDICO_NAO_LIGADO,
    INADIMPLENCIA_PROCESS_KEY,
    FraudeError,
    _coerce_numeric,
    _collect_scoring_inputs,
    assemble_dossier,
    gather_evidence,
    intake,
    notify_sla_risk,
    refer_to_legal,
    register_fraud_accusation,
    register_fraude_workers,
    score_indicators,
    seal_custody_bundle,
    start_contratual,
    start_credenciamento,
)
from maezo.tools.workers.harness import AUDIT_AGENT_ID, WorkerHarness
from tests.support.audit_fakes import FakeStartAuditSink

# ---------------------------------------------------------------
# intake — neutral start
# ---------------------------------------------------------------


def _intake_vars() -> dict[str, object]:
    return {
        "numero_caso": "FRAUDE-001",
        "origem_encaminhamento": "contas",
        "encaminhado_por_id": "auditor-001",
    }


def test_intake_nao_afirma_registro_de_caso() -> None:
    """FAB-INTAKE-CASO-REGISTRADO: `caso_registrado=True` era um fato fabricado.

    O corpo da funcao tinha uma unica instrucao (`logger.info`) e nenhum sink: nenhum registro de
    caso e feito AQUI. O que de fato registra o caso sao dois mecanismos que NAO sao esta funcao —
    a linha ADR-0007 que a harness emite antes de todo `complete` (emit-before-complete,
    fail-closed) e a task seguinte do BPMN, `ST_PublishIntakeReceived`
    (`operadora.events.publish` -> `agents.events.fraude.intake_received`, com
    `event_payload_vars` carregando a procedencia). A constante afirmava para a instancia um
    registro que a propria funcao nunca realizou.
    """
    result = intake(_intake_vars())
    assert "caso_registrado" not in result


def test_intake_nao_afirma_timestamp_de_registro() -> None:
    """FAB-INTAKE: `intake_ts` era o literal `"now"` (o proprio codigo dizia `# placeholder`).

    Nao foi substituido por um relogio real: `intake_ts` tinha ZERO consumidores (nenhuma
    `conditionExpression`, `inputExpression`, worker a jusante, golden ou linha de contrato) e o
    instante do intake ja e um fato do engine (`historyState`/`activity-instance` de `ST_Intake`),
    entao um segundo carimbo no escopo do processo seria uma fonte de verdade redundante — nao
    uma correcao. A chave foi REMOVIDA.
    """
    result = intake(_intake_vars())
    assert "intake_ts" not in result
    assert "now" not in result.values()


def test_intake_retorno_tem_exatamente_as_chaves_honestas() -> None:
    """Particao fechada: `intake` nao produz variavel de processo alguma.

    Mesmo desfecho de `notify_sla_risk` neste mesmo modulo (FAB-SLA-RISK-NOTIFIED-SLICE4): a
    etapa roda, a observabilidade fica no `logger`, e o retorno e `{}` porque nao ha nenhum fato
    novo que esta funcao produza. Diferente de `gather_evidence`/`assemble_dossier` (BEA-09), que
    DECLARAM uma lacuna como variavel: la o registro alvo nao acontece em lugar nenhum, aqui ele
    acontece — na trilha ADR-0007 da harness e em `ST_PublishIntakeReceived` — so nao aqui. Um
    `intake_gap` seria afirmar uma lacuna inexistente, a mesma especie de defeito na direcao
    oposta.
    """
    assert intake(_intake_vars()) == {}


# ---------------------------------------------------------------
# gather_evidence
# ---------------------------------------------------------------


def test_gather_evidence_normaliza_refs_sem_afirmar_coleta() -> None:
    """BEA-09: a funcao NORMALIZA `evidencia_refs` e nada mais — nao coleta de lugar nenhum."""
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


def test_gather_evidence_nao_afirma_timestamp_de_coleta() -> None:
    """BEA-09: `evidencia_coletada_em` era um timestamp constante de uma coleta que nunca ocorreu.

    A funcao nao chama Beatriz, nao consulta CDC/TISS/feature store e nao contata nada: nenhuma
    coleta acontece, logo nenhum instante de coleta pode ser afirmado.
    """
    result = gather_evidence(
        {
            "numero_caso": "FRAUDE-001",
            "entidade_tipo": "prestador",
            "evidencia_refs": ["ref-001"],
        }
    )
    assert "evidencia_coletada_em" not in result
    assert "now" not in result.values()


def test_gather_evidence_declara_a_lacuna_de_beatriz() -> None:
    """BEA-09: a lacuna nao e silenciosa — sai como class-token declarado no contrato."""
    result = gather_evidence({"numero_caso": "FRAUDE-001", "evidencia_refs": []})
    assert result["evidencia_gap"] == GAP_BEATRIZ_A2A_NAO_LIGADO
    assert GAP_BEATRIZ_A2A_NAO_LIGADO == "beatriz_a2a_nao_ligado"


def test_gather_evidence_retorno_tem_exatamente_as_chaves_honestas() -> None:
    """Particao fechada: nenhuma chave alem do que a funcao de fato produz."""
    result = gather_evidence({"numero_caso": "FRAUDE-001", "evidencia_refs": ["r"]})
    assert set(result) == {"evidencia_refs", "evidencia_gap"}


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

    It WAS a list-per-convention field bound to a `string`-declared DMN input whose column was `-`
    (wildcard) in every rule of `unbundling_partial_bundles`; forwarding
    `str(["30101012"]) -> "['30101012']"` would silently flip any future `starts with(...)` rule,
    so T1.5 removed it from `_SCORING_INPUT_KEYS`. GAP-PERSP-DMN-DEAD-INPUTS then removed the dead
    column from the table itself, so today the key is unknown on BOTH ends — pinned here and by
    `test_scoring_input_keys_are_exactly_the_seven_tables_inputs` below.
    """
    assert "tuss_codes" not in _SCORING_INPUT_KEYS
    evidence = _collect_scoring_inputs({"tuss_codes": ["30101012", "30101020"], "risk_score": 10})
    assert "tuss_codes" not in evidence
    # and the repr-cast footgun never appears anywhere in the forwarded evidence
    assert "['30101012'" not in repr(evidence)


_DMN_DIR = Path(__file__).resolve().parents[4] / "spec" / "processes" / "dmn"


def _input_expressions(decision_id: str) -> set[str]:
    """The variable names the DEPLOYED table's `inputExpression`s actually reference."""
    root = ET.parse(_DMN_DIR / f"{decision_id}.dmn").getroot()
    names: set[str] = set()
    for element in root.iter():
        if element.tag.rpartition("}")[2] != "inputExpression":
            continue
        for child in element:
            if child.tag.rpartition("}")[2] == "text":
                names.add((child.text or "").strip())
    return names


def test_scoring_input_keys_are_exactly_the_seven_tables_inputs() -> None:
    """GAP-PERSP-DMN-DEAD-INPUTS. `_SCORING_INPUT_KEYS` and the 7 tables' `inputExpression`s are
    the SAME set — read from the real `.dmn` files, never a hand-copied list.

    Both directions are the regression this closes:

    * a key the worker sends that NO table reads is a worker paying to collect a signal nothing
      consumes (that was `encounter_class`'s status w.r.t. `frequency_zscore_threshold`, which
      declared it and then wildcarded it in all 5 rules);
    * an `inputExpression` no caller supplies is a table advertising a signal it can never receive
      (that was `tuss_codes`: declared by `unbundling_partial_bundles`, wildcarded in all 4 rules,
      and dropped by the worker since T1.5 — dead on both ends).

    Equality is the honest contract: every signal collected is read by some table, and every
    column declared is fed by the worker.
    """
    declared: set[str] = set()
    for decision_id in _SCORING_DECISIONS:
        declared |= _input_expressions(decision_id)

    assert declared == set(_SCORING_INPUT_KEYS), (
        f"only in the tables: {sorted(declared - set(_SCORING_INPUT_KEYS))}; "
        f"only in the worker: {sorted(set(_SCORING_INPUT_KEYS) - declared)}"
    )


def test_the_two_removed_dead_columns_stay_removed() -> None:
    """GAP-PERSP-DMN-DEAD-INPUTS, stated table-by-table so a regression names the table.

    `encounter_class` is NOT gone from the process — it is a real signal read by
    `upcoding_complexity_ceiling`, which is exactly why it stays in `_SCORING_INPUT_KEYS`. What is
    gone is `frequency_zscore_threshold`'s decorative declaration of it.
    """
    assert _input_expressions("frequency_zscore_threshold") == {"z_score"}
    assert _input_expressions("unbundling_partial_bundles") == {"bundle_group_id"}
    assert "encounter_class" in _input_expressions("upcoding_complexity_ceiling")
    assert "encounter_class" in _SCORING_INPUT_KEYS


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


def test_assemble_dossier_nao_afirma_dossie_montado() -> None:
    """BEA-09: `dossie_montado=True` era afirmado por uma funcao que nao monta dossie algum.

    O corpo pre-fix carregava o comentario "Placeholder: real implementation calls Beatriz via
    A2A" e nao havia chamada A2A nenhuma. A jusante estao `seal_custody_bundle` e a User Task
    L0-hard `UT_DecisaoInvestigador`: a constante entrava no escopo do processo (harness
    `complete`) como trilha de auditoria de um dossie inexistente.
    """
    result = assemble_dossier(
        {
            "numero_caso": "F-001",
            "evidencia_refs": ["ref-a", "ref-b"],
            "indicadores_presentes": ["evidencia_presente"],
        }
    )
    assert "dossie_montado" not in result
    assert True not in result.values()


def test_assemble_dossier_nao_conta_itens_de_dossie_inexistente() -> None:
    """`dossie_items` contava `evidencia_refs` como se fossem itens de um dossie montado."""
    result = assemble_dossier({"numero_caso": "F-001", "evidencia_refs": ["ref-a", "ref-b"]})
    assert "dossie_items" not in result


def test_assemble_dossier_declara_a_lacuna_de_beatriz() -> None:
    """A lacuna chega DECLARADA a UT humana em vez de uma afirmacao falsa (nada silencioso)."""
    result = assemble_dossier({"numero_caso": "F-001", "evidencia_refs": ["ref-a"]})
    assert result["dossie_gap"] == GAP_BEATRIZ_A2A_NAO_LIGADO


def test_assemble_dossier_retorno_tem_exatamente_a_chave_honesta() -> None:
    """Particao fechada: a funcao nao produz mais nada, entao nao retorna mais nada."""
    result = assemble_dossier({"numero_caso": "F-001", "evidencia_refs": ["ref-a", "ref-b"]})
    assert set(result) == {"dossie_gap"}


def test_assemble_dossier_nao_ecoa_evidencia_nem_indicadores() -> None:
    """Nao reescreve no escopo do processo o que ja esta la (eco = ruido de auditoria)."""
    result = assemble_dossier(
        {
            "numero_caso": "F-001",
            "evidencia_refs": ["ref-a"],
            "indicadores_presentes": ["upcoding_complexity_ceiling"],
        }
    )
    assert "evidencia_refs" not in result
    assert "indicadores_presentes" not in result


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


def test_notify_sla_risk_nao_afirma_notificacao() -> None:
    """FAB-SLA-RISK-NOTIFIED-SLICE4: retorna `{}` — NAO afirma `sla_risk_notified`.

    O `== {}` e' deliberado (nao `"sla_risk_notified" not in result`): so a igualdade exata pega
    uma fabricacao remontada chave-a-chave num local, que a cerca AST de
    `test_worker_handler_purity.py` documenta nao alcancar.
    """
    assert notify_sla_risk({"numero_caso": "F-001", "tenant_id": "amh"}) == {}


def test_notify_sla_risk_no_adverse_outcome() -> None:
    """The non-interruptive timer alert never produces or propagates an adverse decision.

    UT_DecisaoInvestigador stays open (cancelActivity=false) — score/SLA risk NEVER accuses;
    only the human decision at UT_DecisaoInvestigador (register_fraud_accusation's guard) does.
    """
    result = notify_sla_risk({"numero_caso": "F-001", "decisao_fraude": "ACUSAR_FRAUDE"})
    # FAB-SLA-RISK-NOTIFIED-SLICE4: `== {}` primeiro — sem ele os dois checks abaixo passariam
    # VACUAMENTE (um dict vazio nao tem chave nem valor a inspecionar).
    assert result == {}
    assert "decisao_fraude" not in result
    forbidden = {"ACUSAR_FRAUDE", "ACUSAR", "BLOQUEAR", "FRAUD_DETECTED"}
    for value in result.values():
        assert str(value).upper() not in forbidden


@pytest.mark.parametrize(
    "variables",
    [
        {},
        {"numero_caso": "F-001"},
        {"numero_caso": "F-001", "decisao_fraude": "ACUSAR_FRAUDE"},
    ],
)
def test_notify_sla_risk_nenhuma_entrada_produz_afirmacao(variables: dict) -> None:
    """`numero_caso` ausente degrada sem KeyError e NENHUMA entrada produz afirmacao — o alerta
    do timer nao-interruptivo nunca acusa; so `UT_DecisaoInvestigador` acusa."""
    assert notify_sla_risk(variables) == {}


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
    # FAB-PUBLISH-CONTACT: 10 spec-declared `operadora.fraude.*` topics and NOTHING else. The
    # 11th used to be the orphan `operadora.fraude.publish_completed` (a topic no `serviceTask`
    # declares), retired with its fabricated `evento_publicado: True` — see
    # `test_fraude_nao_registra_topico_orfao_publish_completed` below.
    fraude_topics = {t for t in topics if t.startswith("operadora.fraude.")}
    assert len(fraude_topics) == 10


# ---------------------------------------------------------------
# refer_to_legal
# ---------------------------------------------------------------


def _refer_vars() -> dict[str, object]:
    return {
        "numero_caso": "F-001",
        "destino_referral": {"juridico": True, "ans": True},
    }


def test_refer_to_legal_nao_afirma_execucao_do_referral() -> None:
    """FAB-REFER-TO-LEGAL: `referral_executado=True` era o pior fato fabricado do modulo.

    Caminho ADVERSO: o engine so alcanca `ST_ReferToLegal` a jusante da acusacao humana
    (`UT_DecisaoInvestigador`, L0 hard), do `bundle_root` selado e do SEGUNDO gate humano
    `UT_RevisaoReferral` (juridico/compliance aprovando os destinos). O corpo tinha uma unica
    instrucao (`logger.info`): nenhum canal juridico/ANS/civel/penal e contatado, `fraude.py` nao
    tem NENHUM call site de `kafka.publish(` (`register_fraude_workers` faz `del kafka  #
    unused`), e as proprias obrigacoes de referral (prazo/forma/autoridade) estao DRAFT/verify no
    contrato ("nao estao pinadas em nenhum repo"). A constante gravava na instancia — e portanto
    na trilha de auditoria de uma acusacao de fraude ja constituida — a execucao de um ato que
    nunca saiu do processo.
    """
    result = refer_to_legal(_refer_vars())
    assert "referral_executado" not in result


def test_refer_to_legal_declara_a_lacuna_da_integracao() -> None:
    """A lacuna nao e silenciosa: sai como class-token fechado declarado no contrato."""
    result = refer_to_legal(_refer_vars())
    assert result["referral_gap"] == GAP_REFERRAL_JURIDICO_NAO_LIGADO
    assert GAP_REFERRAL_JURIDICO_NAO_LIGADO == "referral_juridico_nao_ligado"


def test_refer_to_legal_nao_ecoa_os_destinos() -> None:
    """`destinos` era eco byte-a-byte de `destino_referral`, ja no escopo do processo.

    Reescrever o input sob um segundo nome cria uma segunda fonte de verdade para a decisao
    humana (`destino_referral` vem de `UT_DecisaoInvestigador` e e confirmado por
    `UT_RevisaoReferral`) sem acrescentar fato nenhum — mesmo tratamento que
    `notify_sla_risk` deu as suas chaves de eco (FAB-SLA-RISK-NOTIFIED-SLICE4).
    """
    result = refer_to_legal(_refer_vars())
    assert "destinos" not in result


def test_refer_to_legal_retorno_tem_exatamente_as_chaves_honestas() -> None:
    """Particao fechada: a UNICA saida e a lacuna declarada."""
    assert set(refer_to_legal(_refer_vars())) == {"referral_gap"}


def test_refer_to_legal_lacuna_independe_dos_destinos() -> None:
    """A lacuna e de DEPLOY (integracao inexistente), nao do caso: mesma saida para todo destino."""
    for destino in ({"juridico": True}, {"ans": True}, {}, None):
        assert refer_to_legal({"numero_caso": "F-002", "destino_referral": destino}) == {
            "referral_gap": GAP_REFERRAL_JURIDICO_NAO_LIGADO
        }


# ---------------------------------------------------------------
# start_credenciamento
# ---------------------------------------------------------------


def test_start_credenciamento_starts_cred_with_exact_key_and_audit() -> None:
    """start_credenciamento now REALLY starts SP-OP-CRED-001 through the fence (was a stub)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = start_credenciamento(
        {"tenant_id": "amh", "prestador_id": "P-001", "numero_caso": "CASO-1", "bundle_root": "r1"},
        engine=engine,
        audit_sink=sink,
    )
    assert result["handoff_credenciamento"] is True
    assert result["processo_destino"] == CRED_PROCESS_KEY
    assert result["cred_business_key"] == "CRED-amh-P-001"
    assert result["cred_already_existed"] is False
    started = asyncio.run(engine.find_active_instance("CRED-amh-P-001"))
    assert started is not None and started.process_key == CRED_PROCESS_KEY
    assert sink.dedup_keys == [start_dedup_key("amh", CRED_PROCESS_KEY, "CRED-amh-P-001")]
    [record] = sink.records
    assert record.agent_id == AUDIT_AGENT_ID
    assert record.action == f"start_process:{CRED_PROCESS_KEY}"
    assert record.details["entidade_tipo"] == "prestador"
    assert "prestador_id" not in record.details  # hash-bound only


def test_start_credenciamento_fail_closed_seams_and_target() -> None:
    with pytest.raises(RuntimeError, match="engine seam"):
        start_credenciamento({"tenant_id": "amh", "prestador_id": "P-1"}, audit_sink=FakeStartAuditSink())
    with pytest.raises(RuntimeError, match="audit sink"):
        start_credenciamento({"tenant_id": "amh", "prestador_id": "P-1"}, engine=FakeCibSevenTransport())
    with pytest.raises(FraudeError) as exc:
        start_credenciamento(
            {"tenant_id": "amh"}, engine=FakeCibSevenTransport(), audit_sink=FakeStartAuditSink()
        )
    assert exc.value.code == ERR_FRAUDE_HANDOFF_SEM_ALVO


@pytest.mark.parametrize(
    "variables",
    [
        {"tenant_id": "amh", "prestador_id": "   "},  # whitespace-only anchor
        {"tenant_id": "amh", "prestador_id": None},  # explicit None -> "None" must NOT slip through
        {"tenant_id": "", "prestador_id": "P-1"},  # blank tenant -> CRED--P-1 degenerate key
        {"tenant_id": "  ", "prestador_id": "P-1"},
        {"tenant_id": None, "prestador_id": "P-1"},
    ],
    ids=["prestador-ws", "prestador-none", "tenant-empty", "tenant-ws", "tenant-none"],
)
def test_start_credenciamento_refuses_degenerate_anchors_before_any_engine_call(
    variables: dict[str, object],
) -> None:
    """EB-4 R1 finding: whitespace-only / explicit-None prestador_id and blank/None tenant_id all
    refuse (`_non_blank` semantics, shared with the bridge) BEFORE any engine call — no start,
    no audit row, never a degenerate business key like `CRED-amh-None` / `CRED--P-1`."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(FraudeError) as exc:
        start_credenciamento(dict(variables), engine=engine, audit_sink=sink)
    assert exc.value.code == ERR_FRAUDE_HANDOFF_SEM_ALVO
    assert sink.dedup_keys == []  # emit-before-effect: refusal precedes ANY audit/engine call
    assert sink.records == []


def test_start_credenciamento_idempotent() -> None:
    engine = FakeCibSevenTransport()
    engine.seed_instance(
        ProcessInstance(
            instance_id="pre-cred",
            process_key=CRED_PROCESS_KEY,
            business_key="CRED-amh-P-9",
            state="ACTIVE",
            already_existed=True,
        )
    )
    result = start_credenciamento(
        {"tenant_id": "amh", "prestador_id": "P-9"},
        engine=engine,
        audit_sink=FakeStartAuditSink(),
    )
    assert result["cred_already_existed"] is True
    assert result["cred_instance_id"] == "pre-cred"


# ---------------------------------------------------------------
# start_contratual
# ---------------------------------------------------------------


def test_start_contratual_contrato_starts_cancel_and_inadimplencia() -> None:
    """entidade_tipo=contrato -> starts BOTH CANCEL-001 AND INADIMPLENCIA-001 (mirrors the two
    bridge rules), each under its own distinct business key, both audited."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = start_contratual(
        {"tenant_id": "amh", "numero_contrato": "C-001", "entidade_tipo": "contrato"},
        engine=engine,
        audit_sink=sink,
    )
    assert result["handoff_contratual"] is True
    assert result["cancel_business_key"] == "CANCEL-amh-C-001"
    assert result["inadimplencia_business_key"] == "INAD-amh-C-001"
    assert CANCEL_PROCESS_KEY in result["processo_destino"]
    assert INADIMPLENCIA_PROCESS_KEY in result["processo_destino"]
    assert asyncio.run(engine.find_active_instance("CANCEL-amh-C-001")) is not None
    assert asyncio.run(engine.find_active_instance("INAD-amh-C-001")) is not None
    assert sink.dedup_keys == [
        start_dedup_key("amh", CANCEL_PROCESS_KEY, "CANCEL-amh-C-001"),
        start_dedup_key("amh", INADIMPLENCIA_PROCESS_KEY, "INAD-amh-C-001"),
    ]


def test_start_contratual_beneficiario_starts_only_cancel() -> None:
    """entidade_tipo=beneficiario -> CANCEL-001 only (no INADIMPLENCIA), matching the bridge."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = start_contratual(
        {"tenant_id": "amh", "numero_contrato": "C-777", "entidade_tipo": "beneficiario"},
        engine=engine,
        audit_sink=sink,
    )
    assert result["cancel_business_key"] == "CANCEL-amh-C-777"
    assert "inadimplencia_business_key" not in result
    assert result["processo_destino"] == CANCEL_PROCESS_KEY
    assert sink.dedup_keys == [start_dedup_key("amh", CANCEL_PROCESS_KEY, "CANCEL-amh-C-777")]


def test_start_contratual_fail_closed_seams_and_target() -> None:
    with pytest.raises(RuntimeError, match="engine seam"):
        start_contratual({"tenant_id": "amh", "numero_contrato": "C-1"}, audit_sink=FakeStartAuditSink())
    with pytest.raises(RuntimeError, match="audit sink"):
        start_contratual({"tenant_id": "amh", "numero_contrato": "C-1"}, engine=FakeCibSevenTransport())
    with pytest.raises(FraudeError) as exc:
        start_contratual(
            {"tenant_id": "amh"}, engine=FakeCibSevenTransport(), audit_sink=FakeStartAuditSink()
        )
    assert exc.value.code == ERR_FRAUDE_HANDOFF_SEM_ALVO


@pytest.mark.parametrize(
    "variables",
    [
        {"tenant_id": "amh", "numero_contrato": "   "},  # whitespace-only anchor
        {"tenant_id": "amh", "numero_contrato": None},  # explicit None -> "None" must NOT slip
        {"tenant_id": "", "numero_contrato": "C-1"},  # blank tenant -> CANCEL--C-1 degenerate key
        {"tenant_id": "  ", "numero_contrato": "C-1"},
        {"tenant_id": None, "numero_contrato": "C-1"},
    ],
    ids=["contrato-ws", "contrato-none", "tenant-empty", "tenant-ws", "tenant-none"],
)
def test_start_contratual_refuses_degenerate_anchors_before_any_engine_call(
    variables: dict[str, object],
) -> None:
    """EB-4 R1 finding: whitespace-only / explicit-None numero_contrato and blank/None tenant_id
    all refuse (`_non_blank` semantics, shared with the bridge) BEFORE any engine call — no
    CANCEL/INADIMPLENCIA start, no audit row, never a degenerate business key like
    `CANCEL-amh-None` / `INAD--C-1`."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(FraudeError) as exc:
        start_contratual(dict(variables) | {"entidade_tipo": "contrato"}, engine=engine, audit_sink=sink)
    assert exc.value.code == ERR_FRAUDE_HANDOFF_SEM_ALVO
    assert sink.dedup_keys == []  # emit-before-effect: refusal precedes ANY audit/engine call
    assert sink.records == []


class _HistoryBlindCibSevenTransport:
    """`CibSevenTransport` WITHOUT `find_any_instance` — not a `HistoryQueryingTransport`.

    The `runtime_checkable` probe is attribute-presence, so the capability must be genuinely
    absent (not stubbed) for this double to model a decorator that dropped it."""

    def __init__(self) -> None:
        self.starts: list[str] = []

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        return None

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, object]
    ) -> ProcessInstance:
        self.starts.append(business_key)
        return ProcessInstance(
            instance_id=f"blind-{business_key}",
            process_key=process_key,
            business_key=business_key,
            state="ACTIVE",
        )

    async def correlate_message(self, *a: object, **k: object) -> None:
        return None

    async def get_process_status(self, business_key: str) -> object:
        raise NotImplementedError

    async def close(self) -> None:
        return None


def test_start_contratual_cancel_leg_is_gated_and_inad_cred_legs_are_not() -> None:
    """GAP-D3-02 scope, at the caller. Of this worker's three handoff targets only CANCEL-001 is a
    gated (`EXCLUSIVE`) family; INADIMPLENCIA-001 and CRED-001 stay `NON_STRICT`. Pinned here so a
    future widening of the policy cannot silently add a gate to this worker's other legs."""
    assert start_dedup_posture(CANCEL_PROCESS_KEY) is StartDedupPosture.EXCLUSIVE
    assert start_dedup_posture(INADIMPLENCIA_PROCESS_KEY) is StartDedupPosture.NON_STRICT
    assert start_dedup_posture(CRED_PROCESS_KEY) is StartDedupPosture.NON_STRICT


def test_start_contratual_fails_closed_behind_a_history_blind_engine_seam() -> None:
    """NEVER FALL BACK. CANCEL-001 is the FIRST leg `start_contratual` starts, so an engine seam
    that cannot answer the engine-history question refuses the whole handoff before any durable
    claim — no CANCEL start, and no INADIMPLENCIA start either (the CANCEL refusal propagates)."""
    engine = _HistoryBlindCibSevenTransport()
    sink = FakeStartAuditSink()

    with pytest.raises(StartDedupGateUnavailableError):
        start_contratual(
            {"tenant_id": "amh", "numero_contrato": "C-001", "entidade_tipo": "contrato"},
            engine=engine,
            audit_sink=sink,
        )

    assert engine.starts == []
    assert sink.calls == [], "the refusal must precede the durable claim (no orphan claim)"


def test_start_contratual_cancel_redelivery_returns_the_same_instance() -> None:
    """The `EXCLUSIVE` posture on the fraude leg: a re-fired accusation handoff converges on the
    SAME CANCEL-001 instance (one rescisao review per contract), never a second one."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    variables = {"tenant_id": "amh", "numero_contrato": "C-321", "entidade_tipo": "beneficiario"}

    first = start_contratual(dict(variables), engine=engine, audit_sink=sink)
    second = start_contratual(dict(variables), engine=engine, audit_sink=sink)

    assert first["cancel_already_existed"] is False
    assert second["cancel_already_existed"] is True
    assert second["cancel_instance_id"] == first["cancel_instance_id"]


def test_start_contratual_second_case_after_a_finished_cancel_is_not_swallowed() -> None:
    """ANTI-SWALLOW at the fraude leg: a NEW fraud case against a contract whose earlier CANCEL-001
    review ended (contract kept) must open a new review — `EXCLUSIVE` is not a permanent gate."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    variables = {"tenant_id": "amh", "numero_contrato": "C-321", "entidade_tipo": "beneficiario"}

    first = start_contratual(dict(variables), engine=engine, audit_sink=sink)
    engine.seed_instance(
        ProcessInstance(
            instance_id=first["cancel_instance_id"],
            process_key=CANCEL_PROCESS_KEY,
            business_key="CANCEL-amh-C-321",
            state="COMPLETED",
        )
    )

    second = start_contratual(dict(variables), engine=engine, audit_sink=sink)

    assert second["cancel_already_existed"] is False
    assert asyncio.run(engine.find_active_instance("CANCEL-amh-C-321")) is not None



# ---------------------------------------------------------------
# FAB-PUBLISH-CONTACT (NEW-A2-1 / NEW-05)
# ---------------------------------------------------------------

_FRAUDE_BPMN = (
    Path(__file__).resolve().parents[4]
    / "spec"
    / "processes"
    / "bpmn"
    / "SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn"
)
_CAMUNDA_NS = "{http://camunda.org/schema/1.0/bpmn}"


def test_fraude_nao_registra_topico_orfao_publish_completed() -> None:
    """NEW-A2-1: `publish_completed` foi APOSENTADA — funcao E registro.

    Era um `FunctionWorker` num topico (`operadora.fraude.publish_completed`) que NENHUM
    `serviceTask` do BPMN declara — todo `ST_Publish*` deste processo roteia pelo generico
    `operadora.events.publish`, servido por `events.py`, o UNICO ponto do repo que publica de
    verdade e que reporta `event_published` a partir do bool de entrega real do produtor. O corpo
    da funcao tinha uma unica instrucao (`logger.info`) e mesmo assim devolvia
    `evento_publicado: True` (chave que a allowlist de escrita de escopo da harness deixa entrar na
    instancia) mais um `desfecho` recalculado em Python a partir de `decisao_fraude` — segunda
    fonte de verdade para um vocabulario que o BPMN ja fixa em cada `event_desfecho`, e ERRADA em
    4 dos 5 terminais (tudo que nao e `ACUSAR_FRAUDE` virava `arquivado_sem_indicio`, inclusive
    MONITORAR e os tres `encaminhado_*`).

    Mesma decisao de `operadora.lgpd.publish_completed` (LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC,
    R-103/R-H) e de `operadora.programa.monitor_programa` (PERSP-C5-MONITOR-PROGRAMA): remover, em
    vez de inventar um `serviceTask` para servir o orfao.
    """
    import maezo.tools.workers.fraude as fraude_module

    assert not hasattr(fraude_module, "publish_completed"), (
        "fraude.publish_completed voltou a existir — era um worker orfao que fabricava "
        "`evento_publicado: True` sem nenhuma costura de publish"
    )

    harness = WorkerHarness(None, worker_id="unit-test-fraude-orphan")  # type: ignore[arg-type]
    register_fraude_workers(harness, None, dmn=FakeDmnTransport())
    assert "operadora.fraude.publish_completed" not in set(harness.registered_topics)


def test_fraude_completion_events_carregam_os_tokens_de_lacuna() -> None:
    """NEW-05: todo evento `fraude.completed` leva os tokens de lacuna no payload.

    `evidencia_gap`/`dossie_gap` (BEA-09) e `referral_gap` (FAB-REFER-TO-LEGAL) viviam SO no
    escopo do processo: quem lia o desfecho publicado em `agents.events.fraude.completed` via
    `desfecho=encaminhado_juridico` sem saber que nenhuma autoridade foi notificada, nem que o
    dossie nunca foi montado. Os tokens sao vocabulario FECHADO e sem PHI (contrato,
    "Variaveis de saida"), entao entram no `event_payload_vars` das tasks de publicacao de
    desfecho. `events.py` so copia a variavel se ela existir no escopo (`if var_name in
    task.variables`), logo, quando as costuras reais forem ligadas, a chave simplesmente
    desaparece do payload — ausencia continua significando "ocorreu de verdade".

    `referral_gap` so e exigido em `ST_PublishEncaminhadoJuridico`: e a UNICA task de desfecho a
    jusante de `ST_ReferToLegal` (`Flow_GWReferral_Juridico` -> `Flow_Legal_Pub`).
    """
    tree = ET.parse(_FRAUDE_BPMN)
    faltando: dict[str, set[str]] = {}
    vistos: set[str] = set()

    for task in tree.getroot().iter():
        if not task.tag.endswith("serviceTask"):
            continue
        params = {
            el.get("name"): (el.text or "")
            for el in task.iter(f"{_CAMUNDA_NS}inputParameter")
        }
        if "event_desfecho" not in params:
            continue
        task_id = task.get("id") or "<sem id>"
        vistos.add(task_id)
        payload_vars = {v.strip() for v in params.get("event_payload_vars", "").split(",")}
        exigidos = {"evidencia_gap", "dossie_gap"}
        if task_id == "ST_PublishEncaminhadoJuridico":
            exigidos = exigidos | {"referral_gap"}
        if exigidos - payload_vars:
            faltando[task_id] = exigidos - payload_vars

    assert vistos, "nenhuma task com `event_desfecho` encontrada — o parse quebrou (nao-vacuidade)"
    assert len(vistos) == 6, f"composicao das tasks de desfecho mudou: {sorted(vistos)}"
    assert faltando == {}, (
        "tasks de publicacao de desfecho sem os tokens de lacuna no `event_payload_vars`: "
        f"{ {k: sorted(v) for k, v in faltando.items()} }"
    )
