"""R-197 (`OWNER-DECISIONS-REGISTER`, gap `IDEMP-GENERATION-RESIDUAL`, 2026-09-04,
`APROVADO-APOS-REVISÃO-HUMANA`): registers the owner's acceptance of the generation>=2 residual
in `docs/review-queue.md`, with an explicit XRD-10/MZO-060 dependency — no code change, no
widening of `transport.py`'s module scope. This fence pins the citation against the LIVE source
(the docstring/comment block anchoring the acceptance, and the pinning test that anchors the
underlying mechanism) rather than trusting the row's own prose.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_REVIEW_QUEUE = _REPO_ROOT / "docs" / "review-queue.md"
_TRANSPORT = _REPO_ROOT / "src" / "maezo" / "tools" / "mcp_cibseven" / "transport.py"
_DEDUP_TEST = _REPO_ROOT / "tests" / "unit" / "tools" / "test_start_process_dedup_gate.py"


def _review_queue_section() -> str:
    text = _REVIEW_QUEUE.read_text(encoding="utf-8")
    marker = "## IDEMP-GENERATION-RESIDUAL"
    assert marker in text, f"{marker} section not found in {_REVIEW_QUEUE}"
    start = text.index(marker)
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


def test_pinning_test_for_the_underlying_mechanism_still_exists() -> None:
    """The row cites `test_the_durable_claim_is_minted_once_per_key_never_once_per_generation` as
    the live pin for the EXCLUSIVE-generation-1-only mechanism it accepts. If that test is ever
    removed or renamed, the acceptance row's citation goes stale silently unless this fence
    catches it."""
    assert _DEDUP_TEST.is_file()
    text = _DEDUP_TEST.read_text(encoding="utf-8")
    assert "def test_the_durable_claim_is_minted_once_per_key_never_once_per_generation" in text


def test_transport_still_discloses_the_generation_residual() -> None:
    """The acceptance is only honest while `transport.py` still says the residual out loud (never
    silently — the whole point of 'aceito e divulgado'). Pins the disclosure's key facts: no
    generation component in the dedup key, generation-1-only exclusion, XRD-10/MZO-060 as the
    only real close path."""
    text = _TRANSPORT.read_text(encoding="utf-8")
    assert "XRD-10/MZO-060" in text
    assert "GENERATION 1 ONLY" in text
    assert "test_the_durable_claim_is_minted_once_per_key_never_once_per_generation" in text


def test_review_queue_row_cites_r197_verbatim_and_the_xrd10_dependency() -> None:
    """`acao_seguinte`: register the acceptance + explicit XRD-10/MZO-060 dependency; no scope
    widening of this module."""
    section = _review_queue_section()
    assert "R-197" in section
    assert "A — aceita o resíduo divulgado e pinado" in section
    assert "XRD-10" in section and "MZO-060" in section
    assert "sem alargar o escopo deste módulo" in section


def test_review_queue_row_reports_the_gap_register_dependencies_text() -> None:
    """`GAP-REGISTER.yaml` is gitignored (docs-audits-live-edited convention) — this task must
    never edit it, only report the exact `dependencies:` text for the docs-finalizer to apply.
    Pins that the row actually carries that reportable text, not just a vague mention."""
    section = _review_queue_section()
    assert "GAP-REGISTER.yaml" in section
    assert "gitignored" in section
    assert "docs-finalizer" in section
