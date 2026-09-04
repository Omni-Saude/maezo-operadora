"""R-171 (rodada 5 de fechamento de gaps) — fence para
`docs/sme-dispatch/regulatorio/rn-currency-matrix.yaml`.

`R-171` (classe B, `agent_prep_task`) pediu uma matriz de currency de RN —
`norma citada -> arquivo:linha -> veredito do analista (a conferir) -> contratos afetados -> linhas
do registro` — derivada de `docs/compliance/rn-currency-review.md` §1 e do inventário próprio de
citações regulatórias em `docs/processes/contracts/*.md`, `spec/processes/dmn/*.dmn` e
`spec/policies/**`. A versão humana está em
`docs/sme-dispatch/regulatorio/RN-CURRENCY-MATRIX.md`; a versão machine-readable é o YAML que este
módulo verifica.

Sem este fence, a matriz poderia apodrecer silenciosamente: um `arquivo:linha` citado poderia deixar
de existir (arquivo movido/renomeado) ou a linha citada poderia não conter mais o token da norma
(ex.: alguém corrige a citação no contrato real sem atualizar a matriz), e nada acusaria a
divergência — a matriz é uma PREPARAÇÃO para uma sessão humana (regulatório + jurídico), não um
artefato ratificado; nada aqui aprova, corrige ou remove `DRAFT/verify` de nenhum contrato.

Este é um fence de TEXTO ESTÁTICO (mesmo espírito de `test_ans_cron_contract_citations.py` e
`test_contract_bpmn_dmn_citations.py`): confere que cada `arquivo:linha` declarado no YAML existe no
repositório e que a linha citada contém o token da norma (`norma_token`, ex. `"RN 259"`) —
reproduzindo mecanicamente o próprio método de inventário (`grep`) que construiu a matriz.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parents[3]
_MATRIX_YAML = _REPO_ROOT / "docs" / "sme-dispatch" / "regulatorio" / "rn-currency-matrix.yaml"
_MATRIX_MD = _REPO_ROOT / "docs" / "sme-dispatch" / "regulatorio" / "RN-CURRENCY-MATRIX.md"
_RN_CURRENCY_REVIEW = _REPO_ROOT / "docs" / "compliance" / "rn-currency-review.md"

_REQUIRED_ROW_KEYS = {
    "norma_citada",
    "arquivo",
    "linha",
    "norma_token",
    "arquivo_linha",
    "o_que_afirma",
    "status_rn_currency_review_1",
    "norma_corrente_indicada",
    "veredito_analista_a_conferir",
    "contratos_afetados",
    "linhas_do_registro",
    "acao_ao_ratificar",
}


def _load_rows() -> list[dict]:
    assert _MATRIX_YAML.is_file(), f"matrix yaml missing: {_MATRIX_YAML}"
    data = yaml.safe_load(_MATRIX_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "rows" in data, "matrix yaml must have a top-level 'rows' list"
    rows = data["rows"]
    assert isinstance(rows, list) and rows, "matrix yaml 'rows' must be a non-empty list"
    return rows


def test_matrix_files_exist() -> None:
    assert _MATRIX_YAML.is_file()
    assert _MATRIX_MD.is_file()
    assert _RN_CURRENCY_REVIEW.is_file()


def test_every_row_has_the_required_keys() -> None:
    rows = _load_rows()
    for i, row in enumerate(rows):
        missing = _REQUIRED_ROW_KEYS - set(row.keys())
        assert not missing, f"row {i} ({row.get('norma_citada')!r}) missing keys: {missing}"


def test_every_row_cites_a_file_that_exists_in_the_repo() -> None:
    """`arquivo` is a path relative to the repo root -- must resolve to a real tracked file."""
    rows = _load_rows()
    missing = []
    for row in rows:
        p = _REPO_ROOT / row["arquivo"]
        if not p.is_file():
            missing.append((row["norma_citada"], row["arquivo"]))
    assert not missing, f"row(s) cite a non-existent file: {missing}"


def test_every_row_line_number_is_in_range() -> None:
    rows = _load_rows()
    out_of_range = []
    for row in rows:
        p = _REPO_ROOT / row["arquivo"]
        n_lines = len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
        if not (1 <= row["linha"] <= n_lines):
            out_of_range.append((row["norma_citada"], row["arquivo"], row["linha"], n_lines))
    assert not out_of_range, f"row(s) cite a line number out of range: {out_of_range}"


def test_every_row_cited_line_actually_contains_the_norma_token() -> None:
    """The whole point of the fence: `arquivo:linha` must still say what the matrix claims it says.
    Proves the matrix cannot rot silently -- if a contract/DMN/policy citation is edited (line
    moves, RN number corrected, file renamed) without updating the matrix, this fails."""
    rows = _load_rows()
    drift = []
    for row in rows:
        p = _REPO_ROOT / row["arquivo"]
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        cited_line = lines[row["linha"] - 1]
        if row["norma_token"] not in cited_line:
            drift.append(
                (row["norma_citada"], row["arquivo"], row["linha"], row["norma_token"], cited_line[:120])
            )
    assert not drift, f"row(s) whose cited line no longer contains the norma token (rotted matrix): {drift}"


def test_veredito_analista_column_is_empty_for_every_row() -> None:
    """`R-171` (and BRIEF-COMMON's class-B rule) requires the human-verdict column to stay EMPTY --
    an agent never verifies/ratifies a normative citation. If this ever fails, some agent silently
    filled in a verdict that should have come from the regulatório+jurídico session."""
    rows = _load_rows()
    non_empty = [
        (r["norma_citada"], r["arquivo"], r["veredito_analista_a_conferir"])
        for r in rows
        if r["veredito_analista_a_conferir"] not in ("", None)
    ]
    assert not non_empty, f"veredito_analista_a_conferir must stay empty (pending SME session): {non_empty}"


def test_matrix_md_has_the_required_ratification_placeholders() -> None:
    """The human-facing matrix must carry the empty ratification signature line and the
    'recomendação — pendente de assinatura' label -- both required by R-171 / BRIEF-COMMON so the
    document cannot be mistaken for a ratified artifact."""
    text = _MATRIX_MD.read_text(encoding="utf-8")
    assert "Ratificação:" in text
    assert "______" in text
    assert "recomendação" in text.lower() and "pendente de assinatura" in text.lower()
    assert "R-171" in text


def test_matrix_row_count_matches_between_yaml_and_markdown_table() -> None:
    rows = _load_rows()
    md_text = _MATRIX_MD.read_text(encoding="utf-8")
    # the markdown table body rows start after the header + separator line; count '\n| ' occurrences
    # inside the table region delimited by the same header used by render.py.
    header_marker = "| norma citada | arquivo:linha |"
    assert header_marker in md_text, "matrix markdown table header not found"
    table_start = md_text.index(header_marker)
    table_region = md_text[table_start:]
    # every data row starts a new markdown line with "| " right after the header+separator (2 lines)
    body_lines = [
        line for line in table_region.splitlines()[2:] if line.startswith("| ") and line.strip() != "|"
    ]
    # stop counting once we leave the table (first blank line after it started)
    row_lines = []
    for line in body_lines:
        if line.strip() == "":
            break
        row_lines.append(line)
    assert len(row_lines) == len(rows), (
        f"markdown table has {len(row_lines)} data rows but yaml has {len(rows)} -- they must stay in sync"
    )


def test_rn_currency_review_carries_a_dated_pointer_to_the_matrix() -> None:
    """The only allowed edit to rn-currency-review.md for this task is a single dated addendum
    line pointing at the matrix -- confirms it is present, without asserting anything else about
    the file's content (this fence does not own rn-currency-review.md)."""
    text = _RN_CURRENCY_REVIEW.read_text(encoding="utf-8")
    assert "RN-CURRENCY-MATRIX.md" in text
    assert "R-171" in text
