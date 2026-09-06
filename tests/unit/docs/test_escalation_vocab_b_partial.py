"""PERSP-ESCALATION-VOCAB-b — the UNBLOCKED half only. The FINAL contract's 4 role rows
(`plantao-clinico`, `enfermagem-triagem`, `atendimento-humano`, `supervisao-atendimento`) are
re-marked `DRAFT/verify` with a pointer to R-034/`ORG-TAXONOMY-TABLE.md`. The actual RENAME across
6 sites (`escalation_routing.dmn`, `escalation.py::_SEVERITY_TO_GROUP`,
`test-specs/SP-OP-ESCALATION-001.md`, and the contract itself) stays BLOCKED on the R-034 naming
session — NOTHING is renamed here. This fence proves the marking landed for every candidateGroup
the Papeis table declares (tree-derived from the table's own `Grupo` column, never a hardcoded
list) and that no rename happened.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_CONTRACT = _REPO_ROOT / "docs" / "processes" / "contracts" / "SP-OP-ESCALATION-001.md"
_DMN = _REPO_ROOT / "spec" / "processes" / "dmn" / "escalation_routing.dmn"


def _papeis_table_groups() -> list[str]:
    """Tree-derived: every `Grupo` cell (first column) in the Papeis humanos table, parsed
    straight from the table rows rather than hardcoded."""
    text = _CONTRACT.read_text(encoding="utf-8")
    section_start = text.index("## Papeis humanos")
    section_end = text.find("\n## ", section_start + 1)
    section = text[section_start : section_end if section_end != -1 else None]
    rows = re.findall(r"^\|\s*`([a-z-]+)`\s*\|", section, re.MULTILINE)
    assert rows, "no candidateGroup rows found in the Papeis humanos table"
    return rows


#: `supervisao-atendimento` is reached via the SLA-breach escalation path (`UT_SupervisorAssume`),
#: never as a `grupo_atendimento` DMN output value -- it is deliberately excluded from the
#: DMN-presence check below, not an oversight.
_NOT_A_DMN_OUTPUT = frozenset({"supervisao-atendimento"})


def test_every_papeis_table_group_still_resolves_in_the_live_dmn() -> None:
    """Precondition: the groups this fence marks DRAFT/verify (that ARE DMN outputs) are the SAME
    ones the DMN actually outputs today (proving no rename happened anywhere, contract or DMN)."""
    dmn_text = _DMN.read_text(encoding="utf-8")
    for group in _papeis_table_groups():
        if group in _NOT_A_DMN_OUTPUT:
            continue
        assert group in dmn_text, f"{group!r} not found in escalation_routing.dmn (renamed?)"


def test_every_papeis_table_role_is_marked_draft_verify_with_r034_pointer() -> None:
    """The load-bearing fence: every role row in the Papeis humanos table must carry the
    `DRAFT/verify` marker with an `ORG-TAXONOMY-TABLE.md` pointer. Revert any one row and this
    goes RED."""
    text = _CONTRACT.read_text(encoding="utf-8")
    section_start = text.index("## Papeis humanos")
    section_end = text.find("\n## ", section_start + 1)
    section = text[section_start : section_end if section_end != -1 else None]
    rows = [line for line in section.splitlines() if re.match(r"^\|\s*`[a-z-]+`\s*\|", line)]
    assert len(rows) == 4, f"expected 4 role rows, found {len(rows)}: {rows}"
    missing_marker = [r for r in rows if "DRAFT/verify" not in r]
    missing_pointer = [r for r in rows if "ORG-TAXONOMY-TABLE.md" not in r]
    assert not missing_marker, f"row(s) missing DRAFT/verify: {missing_marker}"
    assert not missing_pointer, f"row(s) missing ORG-TAXONOMY-TABLE.md pointer: {missing_pointer}"


def test_no_candidate_group_was_renamed() -> None:
    """The negative half of 'do NOT rename anything': the exact 4 group tokens the row cited
    (from the GAP-REGISTER reproduction) must still be the ones in the table's `Grupo` column."""
    groups = set(_papeis_table_groups())
    expected = {
        "plantao-clinico",
        "enfermagem-triagem",
        "atendimento-humano",
        "supervisao-atendimento",
    }
    assert groups == expected, f"candidateGroups changed -- rename applied? got {sorted(groups)}"


def test_contract_declares_the_rename_still_blocked_by_r034() -> None:
    """The dossier's explicit ask: mark PARTIAL, name the blocker, do not silently imply the
    rename is done."""
    text = _CONTRACT.read_text(encoding="utf-8")
    assert "PERSP-ESCALATION-VOCAB-b" in text
    assert "parcial — rename bloqueado por R-034" in text
    assert "Nenhum rename foi aplicado" in text
