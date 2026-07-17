"""Live-engine chain test for T2.7 phase 2 — `operadora.fraude.score_indicators`.

Runs the REAL `CibSevenDmnTransport` (ADR-0011 — never a mock) against the compose engine's
DEPLOYED `fraude_scoring/*` tables (T2.7 phase 1, PR #48) through the ACTUAL worker function
(`maezo.tools.workers.fraude.score_indicators`) end-to-end — not just `DmnTransport.evaluate` in
isolation. Every expected value below was captured from a REAL run against the compose engine
(never fabricated, constraint 3) — see this PR's body for the full characterization table
(old `len(evidencia_refs) * 10` heuristic vs. this chain) and the raw per-table probe output.

GOLDEN-PARITY IS N/A HERE (unlike `test_dmn_golden_parity.py`): the deleted heuristic was a
placeholder (`len(evidencia_refs) * 10`), never a rule source, so there is no "old logic" to
prove parity against — the whole point of T2.7 phase 2 is that the score is now DMN-COMPUTED
and looks nothing like the placeholder. This module instead pins down (a) the aggregation is
live-engine-correct for 3 realistic scenarios, and (b) the L0 invariant (never a verdict) holds
grep-level and behaviorally against REAL engine output, not just fixture data.

If the engine is unreachable, every test in this module SKIPS via the session-scoped
`_skip_if_engine_unreachable` autouse fixture (`tests/integration/conftest.py`) with an explicit,
loud reason — never a silent pass, never a fabricated result (constraint 3).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from maezo.tools.workers.dmn_transport import CibSevenDmnTransport, DmnVersion
from maezo.tools.workers.fraude import _SCORING_DECISIONS, score_indicators

# `score_indicators` is a SYNC function that internally bridges to the async `dmn.evaluate(...)`
# via `evaluate_sync`'s own `asyncio.run(...)` (`dmn_transport.py`) — calling it directly from an
# `async def` test would collide with pytest-asyncio's already-running loop (`asyncio.run()
# cannot be called from a running event loop`). `asyncio.to_thread` mirrors the REAL production
# dispatch path (`tools/workers/harness.py` runs every sync worker via `asyncio.to_thread`, per
# `dmn_transport.py`'s own module docstring) — a fresh OS thread gets its own fresh event loop.


async def _score(variables: dict[str, Any], *, dmn: CibSevenDmnTransport) -> dict[str, Any]:
    return await asyncio.to_thread(score_indicators, variables, dmn=dmn)


pytestmark = pytest.mark.integration

# Vocabulary that must NEVER appear anywhere in this worker's output — the L0 hard invariant
# (fraud_accusation is human-only; DMN outputs here are FACTS, never verdicts).
_FORBIDDEN_VERDICT_TOKENS = ("FRAUD_DETECTED", "ACUSAR", "ACUSAR_FRAUDE", "BLOQUEAR", "CONFIRMAR")

# Scenario B mirrors the v1 donor's own characterization payload (READ-ONLY reference,
# Maezo-Healthcare-Plan tests/unit/workers/test_fraude_guards.py,
# `test_score_indicators_computa_score_via_dmns_nunca_ecoa`), extended with the remaining 3
# tables' signals (tuss_prefix/deviation_pct/provider_volume/bundle_group_id/tuss_codes) so all 7
# tables have a concrete, non-catch-all signal to evaluate.
_SCENARIO_B_EVIDENCE: dict[str, Any] = {
    "risk_score": 85,
    "encounter_class": "ambulatorio",
    "code_tier": 3,
    "z_score": 2.5,
    "has_tuss_codes": True,
    "has_cid10_codes": False,
    "tuss_prefix": "9911",
    "deviation_pct": 60.0,
    "provider_volume": 15,
    "bundle_group_id": "partial",
    "tuss_codes": "99213,99214",
}

_SCENARIO_C_EVIDENCE: dict[str, Any] = {
    "risk_score": 35,
    "encounter_class": "ambulatorio",
    "code_tier": 2,
    "z_score": 1.6,
    "has_tuss_codes": True,
    "has_cid10_codes": True,
    "tuss_prefix": "12345",
    "deviation_pct": 20.0,
    "provider_volume": 5,
    "bundle_group_id": "complete",
    "tuss_codes": "10101012",
}


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    # timeout=30.0 mirrors test_dmn_golden_parity.py's rationale — the compose engine can be slow
    # right after this package's session-start full-tree deploy.
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


def _assert_no_verdict_vocabulary(payload: dict[str, Any]) -> None:
    """Grep-level L0 guard: no accusation/verdict token anywhere in the worker's output."""
    blob = json.dumps(payload, default=str).upper()
    for token in _FORBIDDEN_VERDICT_TOKENS:
        assert token not in blob, f"forbidden verdict token {token!r} found in worker output: {payload!r}"


# ---------------------------------------------------------------------------
# Scenario A — no evidence signals attached yet (gather_evidence/feature-store integration
# hasn't populated any of the 11 scoring keys). Every table falls to its own catch-all row.
# ---------------------------------------------------------------------------


async def test_scenario_a_no_signal_falls_to_catchalls(dmn: CibSevenDmnTransport) -> None:
    result = await _score({"numero_caso": "FRAUDE-CHAIN-A"}, dmn=dmn)

    assert result["score_indicadores"] == 75
    assert result["indicadores_presentes"] == [
        "risk_thresholds_indeterminado",
        "frequency_zscore_threshold_indeterminado",
        "phantom_no_diagnosis_indeterminado",
        # LIVE-VERIFIED, FLAGGED FOR SME/ENGINEERING FOLLOW-UP (not fabricated, not a Python
        # bug): `phantom_suspicious_prefix`'s FEEL `starts with(tuss_prefix, "99")` on an
        # ABSENT `tuss_prefix` resolves to the SAME row as an explicit "99"-prefixed code
        # (indicador_score=25, label "phantom_suspicious_prefix") rather than that table's own
        # catch-all (indicador_score=10, "..._indeterminado") the way the other 5 tables do for
        # an absent signal. This is DMN/engine behavior (spec/ content, untouched per this
        # task's constraints) — reported here as a characterization finding, not "fixed."
        "phantom_suspicious_prefix",
        "provider_peer_deviation_indeterminado",
        "unbundling_partial_bundles_indeterminado",
    ]
    assert set(result["dmn_versions"]) == set(_SCORING_DECISIONS)
    for version_dict in result["dmn_versions"].values():
        assert version_dict["id"] != "unknown"  # ADR-0028's fix over the donor's fail-open gap
    _assert_no_verdict_vocabulary(result)


# ---------------------------------------------------------------------------
# Scenario B — every table's high/critical band signal present simultaneously.
# ---------------------------------------------------------------------------


async def test_scenario_b_high_risk_full_signals(dmn: CibSevenDmnTransport) -> None:
    result = await _score(dict(_SCENARIO_B_EVIDENCE, numero_caso="FRAUDE-CHAIN-B"), dmn=dmn)

    assert result["score_indicadores"] == 200, "30+30+20+40+25+30+25 across the 7 tables"
    assert result["indicadores_presentes"] == [
        "risk_thresholds_alto",
        "upcoding_complexity_ceiling",
        "frequency_zscore_threshold",
        "phantom_no_diagnosis",
        "phantom_suspicious_prefix",
        "provider_peer_deviation",
        "unbundling_partial_bundles",
    ]
    _assert_no_verdict_vocabulary(result)


# ---------------------------------------------------------------------------
# Scenario C — borderline/moderate signals only; several tables land on "none".
# ---------------------------------------------------------------------------


async def test_scenario_c_moderate_borderline(dmn: CibSevenDmnTransport) -> None:
    result = await _score(dict(_SCENARIO_C_EVIDENCE, numero_caso="FRAUDE-CHAIN-C"), dmn=dmn)

    assert result["score_indicadores"] == 35, "10 (risk) + 15 (upcoding) + 10 (freq); rest are 0/none"
    assert result["indicadores_presentes"] == [
        "risk_thresholds_moderado",
        "upcoding_complexity_ceiling_borderline",
        "frequency_zscore_threshold_moderado",
    ]
    _assert_no_verdict_vocabulary(result)


# ---------------------------------------------------------------------------
# Cross-scenario invariants
# ---------------------------------------------------------------------------


async def test_never_echoes_a_forged_inbound_score(dmn: CibSevenDmnTransport) -> None:
    """A forged/stale score_indicadores already on process variables (e.g. a re-delivered task
    carrying a prior run's value) is NEVER echoed — always recomputed from the live DMNs."""
    variables = dict(_SCENARIO_C_EVIDENCE, score_indicadores=999, indicadores_presentes=["forjado"])
    result = await _score(variables, dmn=dmn)
    assert result["score_indicadores"] == 35
    assert "forjado" not in result["indicadores_presentes"]


async def test_dmn_versions_are_real_engine_provenance(dmn: CibSevenDmnTransport) -> None:
    """`dmn_versions` carries the REAL resolved `DmnVersion.to_audit_dict()` shape per table —
    never the donor's `"unknown"` fallback (ADR-0028 §2's fix)."""
    result = await _score(dict(_SCENARIO_B_EVIDENCE), dmn=dmn)
    for decision_id in _SCORING_DECISIONS:
        entry = result["dmn_versions"][decision_id]
        assert entry.keys() == {"version", "id", "deploymentId"}
        assert entry["id"] != "unknown"


async def test_score_indicators_matches_direct_dmn_evaluate_for_scenario_b(
    dmn: CibSevenDmnTransport,
) -> None:
    """Sanity cross-check: the worker's aggregation matches a direct per-table
    `CibSevenDmnTransport.evaluate` call against the SAME evidence — proves `score_indicators`
    isn't silently diverging from the raw engine responses it wraps."""
    total = 0
    labels: list[str] = []
    for decision_id in _SCORING_DECISIONS:
        rows, version = await dmn.evaluate(decision_id, _SCENARIO_B_EVIDENCE)
        assert isinstance(version, DmnVersion)
        assert rows, f"empty result for `{decision_id}` — ADR-0028 §3 requires a catch-all row"
        row = rows[0]
        total += int(row["indicador_score"])
        label = row.get("indicador_label")
        if label and label != "none" and label not in labels:
            labels.append(str(label))

    result = await _score(dict(_SCENARIO_B_EVIDENCE), dmn=dmn)
    assert result["score_indicadores"] == total
    assert result["indicadores_presentes"] == labels
