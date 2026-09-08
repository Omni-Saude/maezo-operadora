"""R-031 (`OWNER-DECISIONS-REGISTER`, gap `9.1`, Bold-Decision Review v2 CEO 2026-09-04,
`APROVADO-APOS-REVISÃO-HUMANA`) registration: this is a CLASS-A docs task, not a code fix — it
registers an ALREADY-DECIDED owner answer in the three artefacts `acao_seguinte` names. This fence
proves the registration actually landed in all three places with the decisive quoted phrase, not
just that files exist.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_README = _REPO_ROOT / "src" / "maezo" / "platform" / "testchannel" / "README.md"
_REVIEW_QUEUE = _REPO_ROOT / "docs" / "review-queue.md"
_PLANS = _REPO_ROOT / "PLANS.md"

_DECISIVE_PHRASE = "demo sem autenticação própria"


def test_testchannel_readme_exists_and_declares_demo_no_own_auth() -> None:
    """`acao_seguinte`: 'nota \"demo sem autenticação própria\" no
    src/maezo/platform/testchannel/README.md'. This file did not exist before R-031."""
    assert _README.is_file(), f"{_README} must exist (R-031 acao_seguinte)"
    text = _README.read_text(encoding="utf-8")
    assert _DECISIVE_PHRASE in text
    assert "R-031" in text


def test_review_queue_row_cites_r031_verbatim_and_leaves_draft() -> None:
    """`acao_seguinte`: 'a linha de docs/review-queue.md sai de DRAFT com a decisão A+D
    registrada'. The row must quote the owner's resposta_sugerida (the sign-off evidence, per
    R-031's own floor_note) and must NOT carry a DRAFT status."""
    text = _REVIEW_QUEUE.read_text(encoding="utf-8")
    assert "SUPERFICIE-HUMANA-OPERADOR" in text
    section_start = text.index("## SUPERFICIE-HUMANA-OPERADOR")
    section_end = text.find("\n## ", section_start + 1)
    section = text[section_start : section_end if section_end != -1 else None]
    assert "R-031" in section
    assert _DECISIVE_PHRASE in section
    # the exact verbatim resposta_sugerida opening clause, proving it is quoted not paraphrased
    assert "Manter o SIM (Cockpit" in section
    # the Status cell (last `...` span before the trailing ` |`) must not itself be DRAFT -- the
    # word legitimately appears earlier in the row, quoted inside the verbatim resposta_sugerida
    # ("a linha de docs/review-queue.md sai de DRAFT"), so this checks the STATUS CELL specifically.
    status_cell = section.rstrip().rsplit("|", 2)[-2].strip()
    assert status_cell.startswith("`") and not status_cell.startswith("`DRAFT"), (
        f"R-031's floor_note requires this row's Status cell to leave DRAFT, not stay in it: {status_cell!r}"
    )


def test_plans_md_echoes_the_r031_decision() -> None:
    """`acao_seguinte`: 'atualização de PLANS.md' — echoes the decision, does not reopen it."""
    text = _PLANS.read_text(encoding="utf-8")
    assert "0.5.5" in text and "R-031" in text
    heading_marker = "R-031, 2026-09-04)"
    heading_idx = text.index(heading_marker)
    section_start = text.rindex("## 0.5.5", 0, heading_idx)
    section_end = text.find("\n## ", section_start + 1)
    section = text[section_start : section_end if section_end != -1 else None]
    assert _DECISIVE_PHRASE in section
    assert "RESOLVIDA" in section or "RESOLVIDO" in section, (
        "PLANS.md's R-031 section must mark itself resolved, not left as an open item"
    )
