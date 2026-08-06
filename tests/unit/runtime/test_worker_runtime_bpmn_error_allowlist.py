"""Unit tests for the ADR-0030 Tier-0 production BPMN-error allowlist wired in
`maezo.runtime.worker_runtime.service`.

Proves the production allowlist is populated correctly (the systemic blocker ADR-0030 closes was
the empty `frozenset()`), that the T-E hard-gate (§4) actively EXCLUDES the business-outcome codes
pending audited-refusal, and that the runtime wiring agrees with the independently-computed output
of the boundary-proof gate (`scripts/ci/check_bpmn_error_allowlist.py`).
"""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_bpmn_error_allowlist import run_gate

from maezo.runtime.worker_runtime.service import (
    _GATE_PROVEN_BPMN_ERROR_CODES,
    PRODUCTION_BPMN_ERROR_ALLOWLIST,
    _is_te_gated,
)

# tests/unit/runtime/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BPMN_DIR = _REPO_ROOT / "spec" / "processes" / "bpmn"
_WORKERS_DIR = _REPO_ROOT / "src" / "maezo" / "tools" / "workers"

# The four business-outcome codes ADR-0030 §4 hard-gates on T-E (all consumption-covered or
# modeled, all MUST stay out of the production allowlist until audited-refusal lands).
_TE_GATED_CODES = (
    "ERR_CANCEL_MANTER_NOT_HUMAN",
    "ERR_DECRED_NOT_HUMAN",
    "ERR_CRED_DENIAL_NOT_HUMAN",
    "ERR_CONTRACT_SUSPENSION_NOT_HUMAN",
    "ERR_AUTH_DENIAL_INCOMPLETE",
)


def test_production_allowlist_is_exactly_the_non_adverse_failsafes() -> None:
    # SHARED FILE (T3.1 P2b flag for merge-time reconciliation): this exact-set assertion is the
    # ADR-0030 production allowlist census — every branch that wires a NEW non-T-E-gated
    # WorkerBpmnError code into service.py must extend this set. Not empty (the old blocker), not
    # more than what's gate-proven-and-not-T-E-gated today:
    #   - ERR_EVENT_PUBLISH_FAILED: a publish failure routed to a retry/fallback terminal.
    #   - ERR_DSR_IDENTITY_UNVERIFIED (T2.8): a mechanically-unverifiable LGPD titular routed to
    #     End_IdentidadeInverificavel (a neutral terminal).
    #   - ERR_RECURSO_INVALID_GLOSA (T3.1 P2b): recurso's G2-val origin/consistency guard
    #     (glosa_id absent/empty) routed to End_RecursoGlosaInvalidaOrigem (a neutral terminal).
    #   - ERR_ESC_NOTIFY_FAILED (t8-escalation-boundary, ADR-0030 Tier-1): escalation's G2-fs
    #     notify-channel fail-safe (notify_team/notify_supervisor publish failure) routed via
    #     BE_FalhaNotificacao/BE_NotifFallbackFailed/BE_NotifSupervisorFailed to the supervisor
    #     fallback + the mandatory HITL user task (a neutral, never-silently-dropped route).
    #   - ERR_NIP_PROTOCOLO_INVALIDO (item-9 bucket-3, Tier-2): nip's G2-val origin/consistency
    #     guard (blank protocolo_ans) routed to End_NipProtocoloInvalido (a neutral terminal).
    #   - ERR_PROGRAMA_NO_CONSENT (item-9 bucket-3, Tier-2): programa's G2-val consent chokepoint
    #     (check_consent) routed to End_SemConsentimento (a neutral LGPD fail-safe terminal).
    #   - ERR_CRED_INVALID_PRESTADOR (item-9 bucket-3 Class-C, Tier-2): cred's G2-val origin
    #     guard (blank/non-string prestador_id at verify_credentials) routed to
    #     End_CredPrestadorInvalido (a neutral "fail-safe, nao adverso" terminal).
    #   - ERR_ANS_PROTOCOLO_NACK (t2-ans-submit): ans_submit's transmission fail-safe — ANS refused
    #     the filing; BE_SubmitNack routes it into SUB_RetryEnvio (bounded retry/backoff) and, on
    #     exhaustion, to UT_TratarNack (human) via BE_RetryEsgotado. Never a silent end.
    #   - ERR_ANS_DATASET_INCOMPLETO (t2-ans-submit): ans_submit's origin-data guard (the upstream
    #     source could not assemble the dataset at all); BE_AssembleDatasetIncompleto routes it to
    #     UT_CorrigirPendenciaEnvio (human) — the contract's explicit "nunca auto-rejeita o envio".
    # All nine are NON-adverse (not a denial), so none is T-E-gated. The two ANS codes are doubly so:
    # SP-OP-ANS-SUBMIT-001 is operadora -> regulador and models no adverse output at all.
    assert (
        frozenset(
            {
                "ERR_EVENT_PUBLISH_FAILED",
                "ERR_DSR_IDENTITY_UNVERIFIED",
                "ERR_RECURSO_INVALID_GLOSA",
                "ERR_ESC_NOTIFY_FAILED",
                "ERR_NIP_PROTOCOLO_INVALIDO",
                "ERR_PROGRAMA_NO_CONSENT",
                "ERR_CRED_INVALID_PRESTADOR",
                "ERR_ANS_PROTOCOLO_NACK",
                "ERR_ANS_DATASET_INCOMPLETO",
            }
        )
        == PRODUCTION_BPMN_ERROR_ALLOWLIST
    )


def test_te_denial_code_is_proven_but_excluded_from_production() -> None:
    # ERR_AUTH_DENIAL_INCOMPLETE is gate-proven (present in the union) yet filtered OUT — this is
    # the live regression fence: drop the T-E filter and the denial-block code leaks into prod.
    assert "ERR_AUTH_DENIAL_INCOMPLETE" in _GATE_PROVEN_BPMN_ERROR_CODES
    assert "ERR_AUTH_DENIAL_INCOMPLETE" not in PRODUCTION_BPMN_ERROR_ALLOWLIST


def test_no_not_human_guard_code_in_production() -> None:
    assert not any(c.endswith("_NOT_HUMAN") for c in PRODUCTION_BPMN_ERROR_ALLOWLIST)


def test_all_business_outcome_codes_classified_te_gated() -> None:
    for code in _TE_GATED_CODES:
        assert _is_te_gated(code), code
        assert code not in PRODUCTION_BPMN_ERROR_ALLOWLIST, code


def test_production_allowlist_matches_gate_tier0_output() -> None:
    # Cross-validation: the runtime wiring (assembled from per-worker constants) equals the
    # boundary-proof gate's independently-computed Tier-0 set (from spec/** + worker AST).
    result = run_gate(_BPMN_DIR, _WORKERS_DIR)
    assert result.ok, result.render()
    assert result.tier0_enabled == PRODUCTION_BPMN_ERROR_ALLOWLIST
