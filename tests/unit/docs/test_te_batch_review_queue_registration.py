"""R-075 (`OWNER-DECISIONS-REGISTER`, gap `ADR-0030-TE-INADIMPLENCIA`, 2026-09-04,
`APROVADO-APOS-REVISÃO-HUMANA`): registers the T-E-deferred code family (`inadimplencia`, `cred`,
`cancel`) as a SINGLE enablement batch in `docs/review-queue.md` — no code change, no allowlist
flip. This fence proves the registered code list is not a stale/hand-typed snapshot: it is
RE-DERIVED from the live gate (`scripts/ci/check_bpmn_error_allowlist.py::run_gate`) every run, so
a future code migration that changes the T-E-deferred set trips this fence instead of silently
leaving the review-queue row wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_REVIEW_QUEUE = _REPO_ROOT / "docs" / "review-queue.md"
_SCRIPTS_CI = _REPO_ROOT / "scripts" / "ci"


def _live_te_deferred_codes() -> frozenset[str]:
    if str(_SCRIPTS_CI) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_CI))
    import check_bpmn_error_allowlist as gate  # deliberate late import, sys.path setup above

    result = gate.run_gate(
        _REPO_ROOT / "spec" / "processes" / "bpmn",
        _REPO_ROOT / "src" / "maezo" / "tools" / "workers",
    )
    return result.te_deferred


def _review_queue_section() -> str:
    text = _REVIEW_QUEUE.read_text(encoding="utf-8")
    marker = "## FILA-T-E-LOTE-UNICO"
    assert marker in text, f"{marker} section not found in {_REVIEW_QUEUE}"
    start = text.index(marker)
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


def test_the_live_te_deferred_set_matches_the_three_named_families() -> None:
    """Ground truth precondition: R-075's batch is inadimplencia + cred + cancel. If a future PR
    adds or removes a T-E-deferred code (a new family, or one of these families completing T-E
    enablement on its own), this must be re-derived and the review-queue row updated."""
    codes = _live_te_deferred_codes()
    expected = {
        "ERR_CONTRACT_SUSPENSION_NOT_HUMAN",  # inadimplencia
        "ERR_CANCEL_MANTER_NOT_HUMAN",  # cancel
        "ERR_CRED_DENIAL_NOT_HUMAN",  # cred
        "ERR_DECRED_NOT_HUMAN",  # cred
    }
    assert codes == expected, (
        f"T-E-deferred set drifted from R-075's batch -- live: {sorted(codes)}, "
        f"expected: {sorted(expected)}. Update docs/review-queue.md's FILA-T-E-LOTE-UNICO row."
    )


def test_review_queue_row_lists_every_live_te_deferred_code() -> None:
    """The registered row must name every code the gate currently reports as T-E-deferred —
    proven by re-deriving the live set, never by re-reading the row's own hardcoded prose."""
    section = _review_queue_section()
    for code in _live_te_deferred_codes():
        assert code in section, f"{code!r} (live T-E-deferred) is not named in the FILA-T-E row"


def test_review_queue_row_cites_r075_verbatim_and_does_not_flip_the_allowlist() -> None:
    """`acao_seguinte`: register the batch + trigger; explicitly NO change to
    `check_bpmn_error_allowlist.py` nor the production allowlist in this row."""
    section = _review_queue_section()
    assert "R-075" in section
    assert "agrupar depois — manter `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` T-E-deferred" in section
    assert "Gatilho de habilitacao" in section


def test_no_code_changed_the_te_enabled_allowlists() -> None:
    """Sanity companion to the row's own claim: today's `TE_ENABLED_CODES` /
    `_TE_ENABLED_CODES` must still be exactly the pre-existing single entry — this task made no
    allowlist flip."""
    if str(_SCRIPTS_CI) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_CI))
    import check_bpmn_error_allowlist as gate

    assert frozenset({"ERR_AUTH_DENIAL_INCOMPLETE"}) == gate.TE_ENABLED_CODES
