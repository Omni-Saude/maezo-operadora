"""R-034 (ROW R-034, gap PERSP-ESCALATION-VOCAB-a) — the consolidated organizational-taxonomy
table the owner's approved decision asked for (`OWNER-DECISIONS-REGISTER` row R-034,
`resposta_sugerida`, status APROVADO-APOS-REVISAO-HUMANA): a single naming session covers every
`candidateGroups` marked `PROPOSTO` across the owner's 8-process scope (ESCALATION, CANCEL,
ADEQUACAO, CRED, NIP, PROGRAMA, AUTH, ANS-SUBMIT), fed by a consolidated table `grupo declarado ->
arquivo:linha -> processo -> SLA/ato`.

This module is a STATIC TEXT fence (mirrors `test_contract_bpmn_dmn_citations.py` / the
`test_ans_cron_contract_citations.py` family): it does not ratify any group name — `nome_real`
stays empty in the artefact itself — it only proves the machine-readable table
(`docs/sme-dispatch/po/org-taxonomy-table.yaml`) is internally honest: every `arquivo:linha`
citation resolves to a real file+line, and that line actually contains the declared group token,
so a future BPMN/DMN edit that moves a line without updating this table trips this fence.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parents[3]
_PO_DIR = _REPO_ROOT / "docs" / "sme-dispatch" / "po"
_YAML_PATH = _PO_DIR / "org-taxonomy-table.yaml"
_MD_PATH = _PO_DIR / "ORG-TAXONOMY-TABLE.md"
_PACKAGE_PATH = _PO_DIR / "PACKAGE.md"

_OWNER_SCOPE = {"ESCALATION", "CANCEL", "ADEQUACAO", "CRED", "NIP", "PROGRAMA", "AUTH", "ANS-SUBMIT"}

# The single row that legitimately names no real candidateGroups (SP-OP-ANS-CRON-001 is a pure
# scheduler with zero `camunda:candidateGroups` occurrences — included only for the "inventory
# all 16 processes" completeness requirement).
_NO_GROUP_SENTINEL = "(nenhum)"


def _load_yaml() -> dict:
    assert _YAML_PATH.is_file(), f"expected {_YAML_PATH} to exist"
    data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("rows"), list) and data["rows"], "expected a non-empty 'rows' list"
    return data


def test_artefacts_exist() -> None:
    assert _YAML_PATH.is_file()
    assert _MD_PATH.is_file()
    assert _PACKAGE_PATH.is_file()


def test_status_is_recommendation_pending_owner_naming_not_a_ratification() -> None:
    """Owner approval of the PROCEDURE (R-034) is not SME ratification of any NAME
    (BRIEF-COMMON's 'Owner approval != SME ratification' rule) — the artefact must say so and
    must not carry a filled-in `nome_real` anywhere yet."""
    data = _load_yaml()
    assert "pendente de nomeacao" in data["status"].lower()
    for row in data["rows"]:
        assert row["nome_real"] == "", f"nome_real must stay empty until the owner names it: {row}"
    md_text = _MD_PATH.read_text(encoding="utf-8")
    assert "recomendação" in md_text.lower() or "recomendacao" in md_text.lower()
    assert "pendente de nomeacao" in md_text.lower() or "pendente de nomeação" in md_text.lower()


def test_owner_scope_matches_r034_decision() -> None:
    data = _load_yaml()
    assert set(data["escopo_do_dono"]) == _OWNER_SCOPE


def test_every_row_arquivo_linha_citation_resolves_and_contains_the_group_token() -> None:
    """The load-bearing fence: for every row, `arquivo:linha` must be a real file+line, and
    (except the documented no-group sentinel row) that line's text must contain the declared
    `grupo_declarado` token verbatim — proving the citation is not fabricated and catching a
    future line-shift in the cited BPMN/DMN file."""
    data = _load_yaml()
    failures: list[str] = []
    for row in data["rows"]:
        rel_path = row["arquivo"]
        line_no = row["linha"]
        grupo = row["grupo_declarado"]
        abs_path = _REPO_ROOT / rel_path
        if not abs_path.is_file():
            failures.append(f"{rel_path}: file does not exist (row grupo={grupo!r})")
            continue
        lines = abs_path.read_text(encoding="utf-8").splitlines()
        if not (1 <= line_no <= len(lines)):
            failures.append(f"{rel_path}:{line_no}: line out of range (row grupo={grupo!r})")
            continue
        cited_line = lines[line_no - 1]
        if grupo == _NO_GROUP_SENTINEL:
            continue
        assert grupo, "grupo_declarado must not be empty"
        if grupo not in cited_line:
            failures.append(
                f"{rel_path}:{line_no}: cited line does not contain group token {grupo!r}: {cited_line!r}"
            )
    assert not failures, "stale/fabricated arquivo:linha citation(s):\n" + "\n".join(failures)


def test_every_row_has_the_required_columns_populated() -> None:
    data = _load_yaml()
    required = {
        "grupo_declarado",
        "arquivo",
        "linha",
        "processo",
        "em_escopo",
        "tarefa_ou_regra_dmn",
        "sla_ato",
        "proposto",
        "notas",
        "nome_real",
    }
    for row in data["rows"]:
        missing = required - row.keys()
        assert not missing, f"row missing columns {missing}: {row}"
        assert row["processo"].startswith("SP-OP-"), row
        assert isinstance(row["em_escopo"], bool)
        assert isinstance(row["proposto"], bool)


def test_all_16_processes_are_represented() -> None:
    """Task charter: 'inventory ALL 16 processes anyway and mark which are in scope.'"""
    data = _load_yaml()
    processos = {row["processo"] for row in data["rows"]}
    assert len(processos) == 16, f"expected 16 distinct processes, got {len(processos)}: {sorted(processos)}"


def test_in_scope_rows_match_owner_8_process_scope() -> None:
    data = _load_yaml()
    scope_suffixes = tuple(f"SP-OP-{name}-001" for name in _OWNER_SCOPE)
    for row in data["rows"]:
        expected_in_scope = row["processo"] in scope_suffixes
        assert row["em_escopo"] == expected_in_scope, (
            f"row {row['processo']}/{row['grupo_declarado']} em_escopo={row['em_escopo']} "
            f"disagrees with the owner's 8-process scope"
        )


def test_package_md_points_to_the_new_table() -> None:
    """The task requires appending a pointer paragraph at the END of PACKAGE.md."""
    text = _PACKAGE_PATH.read_text(encoding="utf-8")
    assert "ORG-TAXONOMY-TABLE.md" in text
    assert "R-034" in text
