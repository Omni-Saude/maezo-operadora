"""R-075 (`OWNER-DECISIONS-REGISTER`, gap `ADR-0030-TE-INADIMPLENCIA`, 2026-09-04,
`APROVADO-APOS-REVISÃO-HUMANA`): registers the T-E-deferred code family (`inadimplencia`, `cred`,
`cancel`) as a SINGLE enablement batch in `docs/review-queue.md`. The batch was EXECUTED on
2026-10-31 (owner decision "T-E lote unico: preparar o PR do lote agora"): the four codes moved from
`te_deferred` to `TE_ENABLED_CODES` (gate) + `_TE_ENABLED_CODES` (service.py) in ONE PR, exactly the
trigger the row registered. This fence re-derives the live state from the gate
(`scripts/ci/check_bpmn_error_allowlist.py::run_gate`) every run, so the row cannot claim an
enablement the code does not carry (or vice versa).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

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


_LOTE = frozenset(
    {
        "ERR_CONTRACT_SUSPENSION_NOT_HUMAN",  # inadimplencia
        "ERR_CANCEL_MANTER_NOT_HUMAN",  # cancel
        "ERR_CRED_DENIAL_NOT_HUMAN",  # cred
        "ERR_DECRED_NOT_HUMAN",  # cred
    }
)


def _gate() -> Any:
    if str(_SCRIPTS_CI) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_CI))
    import check_bpmn_error_allowlist as gate  # deliberate late import, sys.path setup above

    return gate


def test_the_batch_was_enabled_as_one_unit_and_nothing_is_left_deferred() -> None:
    """R-075's trigger, executed: the whole batch left `te_deferred` together (no code left behind,
    no partial boundary-by-boundary enablement)."""
    assert _live_te_deferred_codes() == frozenset(), (
        f"T-E-deferred set should be empty after the batch -- live: {sorted(_live_te_deferred_codes())}"
    )
    gate = _gate()
    assert _LOTE <= gate.TE_ENABLED_CODES


def test_review_queue_row_lists_every_code_of_the_batch() -> None:
    section = _review_queue_section()
    for code in _LOTE:
        assert code in section, f"{code!r} (lote T-E) is not named in the FILA-T-E row"


def test_review_queue_row_cites_r075_verbatim_and_records_the_enablement() -> None:
    section = _review_queue_section()
    assert "R-075" in section
    assert "agrupar depois — manter `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` T-E-deferred" in section
    assert "Gatilho de habilitacao" in section
    assert "HABILITACAO EXECUTADA" in section
    assert "2026-10-31" in section


def test_the_two_te_enabled_lists_carry_exactly_auth_plus_the_batch() -> None:
    """The gate list and its runtime mirror are identical and hold exactly the pre-existing AUTH
    entry plus the batch — no other `*_NOT_HUMAN` rode along."""
    from maezo.runtime.worker_runtime.service import _TE_ENABLED_CODES

    gate = _gate()
    expected = _LOTE | {"ERR_AUTH_DENIAL_INCOMPLETE"}
    assert expected == gate.TE_ENABLED_CODES
    assert expected == _TE_ENABLED_CODES
