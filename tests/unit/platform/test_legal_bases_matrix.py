"""Unit tests for `maezo.platform.lifecycle.legal_bases_matrix` — the fail-closed,
typed seam for the DPO legal-bases/retention matrix (PLANS.md §0.5 item 6).

Covers every fail-closed branch of `load_retention_matrix` (absent path, missing
file, non-file path, non-UTF-8 encoding, malformed YAML, non-mapping root,
missing/non-list/empty `categorias`, per-entry schema violations, duplicate
categoria, the UNRATIFIED placeholder-template guard) plus the one successful,
valid-schema parse path — and proves the on-disk schema template under
`spec/policies/retention/` is itself refused (never silently usable).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from maezo.platform.lifecycle.legal_bases_matrix import (
    CANDIDATE_ONLY_CATEGORIES,
    LEAD_CATEGORIES,
    MATRIX_PATH_ENV,
    REASON_CATEGORY_UNRATIFIED,
    REASON_EMPTY,
    REASON_FILE_NOT_FOUND,
    REASON_INVALID_ENCODING,
    REASON_INVALID_SCHEMA,
    REASON_INVALID_YAML,
    REASON_LEAD_CATEGORIES_MISSING,
    REASON_PATH_NOT_SET,
    REASON_RETENTION_CLASS_UNRESOLVED,
    REASON_UNRATIFIED_TEMPLATE,
    REASON_USE_REFUSED,
    REFUSED_CATEGORY_USES,
    RetentionMatrix,
    RetentionMatrixEntry,
    RetentionMatrixUnavailableError,
    admit_category_use,
    load_commercial_matrix,
    load_retention_matrix,
    qualify_lead_intake,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/unit/platform -> repo root
_TEMPLATE_PATH = _REPO_ROOT / "spec" / "policies" / "retention" / "UNRATIFIED-retention-matrix.template.yaml"

_VALID_YAML = """
categorias:
  - categoria: "financeiros_faturamento"
    base_legal: "LGPD art. 16 I; CTN art. 173/174"
    retencao: "5 anos"
    acao: "reter"
  - categoria: "cadastrais_contratuais"
    base_legal: "LGPD art. 7 V; Codigo Civil art. 206"
    retencao: "vinculo + 5 anos"
    acao: "reter"
"""


# ---------------------------------------------------------------------------
# Fail-closed: path resolution
# ---------------------------------------------------------------------------


def test_absent_env_and_no_path_raises_path_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MATRIX_PATH_ENV, raising=False)
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_PATH_NOT_SET
    assert MATRIX_PATH_ENV in str(excinfo.value)


def test_empty_string_env_raises_path_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MATRIX_PATH_ENV, "")
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_PATH_NOT_SET


def test_explicit_path_argument_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicit `path=` argument takes priority over the environment variable."""
    monkeypatch.setenv(MATRIX_PATH_ENV, "/nonexistent/from-env.yaml")
    valid_path = tmp_path / "matrix.yaml"
    valid_path.write_text(_VALID_YAML, encoding="utf-8")

    matrix = load_retention_matrix(path=valid_path)
    assert len(matrix) == 2


# ---------------------------------------------------------------------------
# Fail-closed: missing / unreadable file
# ---------------------------------------------------------------------------


def test_nonexistent_file_raises_file_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    monkeypatch.setenv(MATRIX_PATH_ENV, str(missing))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_FILE_NOT_FOUND


def test_path_is_a_directory_raises_file_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a_dir = tmp_path / "a-directory"
    a_dir.mkdir()
    monkeypatch.setenv(MATRIX_PATH_ENV, str(a_dir))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_FILE_NOT_FOUND


def test_non_utf8_file_raises_invalid_encoding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A cp1252-encoded matrix (e.g. Windows tooling, accented 'jurídico') fails CLOSED.

    UnicodeDecodeError subclasses ValueError, NOT OSError — without an explicit catch
    it would escape `load_retention_matrix` as a raw traceback instead of the typed
    refusal. Literal cp1252 bytes are written here (í = 0xED), which are not valid
    UTF-8 in this byte context.
    """
    p = tmp_path / "cp1252.yaml"
    raw = (
        "categorias:\n"
        "  - categoria: 'dados_saude_prontuario'\n"
        "    base_legal: 'confirmar com jurídico'\n"
        "    retencao: '20 anos'\n"
        "    acao: 'reter'\n"
    ).encode("cp1252")
    assert b"\xed" in raw  # í as a single cp1252 byte — proves this is NOT valid UTF-8 text
    p.write_bytes(raw)
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))

    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_ENCODING
    assert "UTF-8" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Fail-closed: malformed YAML / invalid schema / empty
# ---------------------------------------------------------------------------


def test_malformed_yaml_raises_invalid_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("categorias: [this is not: valid: yaml:::", encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(bad))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_YAML


def test_non_mapping_root_raises_invalid_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "list-root.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_SCHEMA


def test_missing_categorias_key_raises_invalid_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = tmp_path / "no-categorias.yaml"
    p.write_text("something_else: true\n", encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_SCHEMA


def test_categorias_not_a_list_raises_invalid_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "categorias-dict.yaml"
    p.write_text("categorias: {not: a-list}\n", encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_SCHEMA


def test_empty_categorias_list_raises_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("categorias: []\n", encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_EMPTY


def test_entry_not_a_mapping_raises_invalid_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "entry-not-mapping.yaml"
    p.write_text("categorias:\n  - just a string\n", encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_SCHEMA


def test_entry_missing_required_field_raises_invalid_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = tmp_path / "missing-field.yaml"
    p.write_text(
        "categorias:\n"
        "  - categoria: 'x'\n"
        "    base_legal: 'y'\n"
        "    retencao: 'z'\n"
        # 'acao' missing
        "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_SCHEMA
    assert "acao" in excinfo.value.detail


def test_entry_empty_string_field_raises_invalid_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = tmp_path / "empty-field.yaml"
    p.write_text(
        "categorias:\n  - categoria: 'x'\n    base_legal: 'y'\n    retencao: ''\n    acao: 'reter'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_SCHEMA


def test_entry_non_string_field_raises_invalid_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    p = tmp_path / "non-string-field.yaml"
    p.write_text(
        "categorias:\n"
        "  - categoria: 'x'\n"
        "    base_legal: 'y'\n"
        "    retencao: 5\n"  # int, not str
        "    acao: 'reter'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_SCHEMA


def test_duplicate_categoria_raises_invalid_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "dup.yaml"
    p.write_text(
        "categorias:\n"
        "  - categoria: 'x'\n"
        "    base_legal: 'y'\n"
        "    retencao: 'z'\n"
        "    acao: 'reter'\n"
        "  - categoria: 'x'\n"
        "    base_legal: 'y2'\n"
        "    retencao: 'z2'\n"
        "    acao: 'eliminar'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_INVALID_SCHEMA
    assert "duplicate" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Fail-closed: the UNRATIFIED-DO-NOT-DEPLOY placeholder template
# ---------------------------------------------------------------------------


def test_unratified_flag_raises_unratified_template(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "unratified.yaml"
    p.write_text(
        "unratified: true\n"
        "categorias:\n"
        "  - categoria: 'x'\n"
        "    base_legal: 'y'\n"
        "    retencao: 'z'\n"
        "    acao: 'reter'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix()
    assert excinfo.value.reason == REASON_UNRATIFIED_TEMPLATE


def test_repo_template_file_exists_and_is_refused() -> None:
    """The actual on-disk schema template must exist and must be self-refusing.

    Guards against the template drifting into something that would silently load —
    pointing `MAEZO_RETENTION_MATRIX_PATH` at the template by mistake must never
    "succeed".
    """
    assert _TEMPLATE_PATH.exists(), f"expected schema template at {_TEMPLATE_PATH}"
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_retention_matrix(path=_TEMPLATE_PATH)
    assert excinfo.value.reason == REASON_UNRATIFIED_TEMPLATE


# ---------------------------------------------------------------------------
# The one successful path: a valid, ratified-shaped matrix
# ---------------------------------------------------------------------------


def test_valid_matrix_parses_successfully(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "valid.yaml"
    p.write_text(_VALID_YAML, encoding="utf-8")
    monkeypatch.setenv(MATRIX_PATH_ENV, str(p))

    matrix = load_retention_matrix()

    assert isinstance(matrix, RetentionMatrix)
    assert len(matrix) == 2
    entry = matrix.get("financeiros_faturamento")
    assert entry is not None
    assert isinstance(entry, RetentionMatrixEntry)
    assert entry.categoria == "financeiros_faturamento"
    assert entry.base_legal == "LGPD art. 16 I; CTN art. 173/174"
    assert entry.retencao == "5 anos"
    assert entry.acao == "reter"

    assert matrix.get("does-not-exist") is None
    assert {e.categoria for e in matrix} == {"financeiros_faturamento", "cadastrais_contratuais"}


def test_retention_matrix_entry_is_frozen() -> None:
    entry = RetentionMatrixEntry(categoria="a", base_legal="b", retencao="c", acao="d")
    with pytest.raises(AttributeError):
        entry.categoria = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# E-SF-10 — commercial profile (TB-E-25-a, EP-REG-04b, ADDENDUM-01 §6/§7 (e))
# ---------------------------------------------------------------------------
# Synthetic `SYN-*` values only: the tests prove the gate's SHAPE; no period, basis or action
# is proposed by them, and no repo file is ever made loadable.


def _commercial_yaml(categorias: tuple[str, ...]) -> str:
    rows = "".join(
        f'  - categoria: "{categoria}"\n'
        '    base_legal: "SYN-BASE"\n'
        '    retencao: "SYN-RETENCAO"\n'
        '    acao: "SYN-ACAO"\n'
        for categoria in categorias
    )
    return f"categorias:\n{rows}"


def _write_matrix(tmp_path: Path, categorias: tuple[str, ...]) -> Path:
    path = tmp_path / "commercial.yaml"
    path.write_text(_commercial_yaml(categorias), encoding="utf-8")
    return path


def test_template_lists_the_six_lead_categories_as_placeholders() -> None:
    """TB-E-25-a (1): the template carries (a)-(f) as placeholders and stays unratified."""
    data = yaml.safe_load(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert data["unratified"] is True
    rows = {row["categoria"]: row for row in data["categorias"]}
    assert len(LEAD_CATEGORIES) == 6
    for categoria in LEAD_CATEGORIES:
        row = rows.get(f"PLACEHOLDER-CATEGORIA-{categoria}")
        assert row is not None, f"template lacks the lead placeholder {categoria!r}"
        assert all(row[field].startswith("PLACEHOLDER-") for field in ("base_legal", "retencao", "acao"))
    # The addendum candidates are dossier rows, never template rows (nothing about them is ratifiable yet).
    assert not any(categoria in key for key in rows for categoria in CANDIDATE_ONLY_CATEGORIES)


def test_intake_refused_without_ratified_retention_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    """TB-E-25-a (3), lifecycle side: env pointed at the template -> `unratified_template`."""
    monkeypatch.setenv(MATRIX_PATH_ENV, str(_TEMPLATE_PATH))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        qualify_lead_intake(source_retention_classes={c: c for c in LEAD_CATEGORIES})
    assert excinfo.value.reason == REASON_UNRATIFIED_TEMPLATE
    monkeypatch.delenv(MATRIX_PATH_ENV)
    with pytest.raises(RetentionMatrixUnavailableError) as absent:
        qualify_lead_intake(source_retention_classes={c: c for c in LEAD_CATEGORIES})
    assert absent.value.reason == REASON_PATH_NOT_SET


@pytest.mark.parametrize("dropped", LEAD_CATEGORIES)
def test_matrix_without_lead_categories_refused_for_commercial_profile(tmp_path: Path, dropped: str) -> None:
    """TB-E-25-a (2): a ratified-shaped matrix without a lead row is `lead_categories_missing`."""
    platform_only = _write_matrix(tmp_path, ("cadastrais_contratuais",))
    assert len(load_retention_matrix(platform_only)) == 1  # the platform profile still loads it
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        load_commercial_matrix(platform_only)
    assert excinfo.value.reason == REASON_LEAD_CATEGORIES_MISSING

    partial = _write_matrix(tmp_path, tuple(c for c in LEAD_CATEGORIES if c != dropped))
    with pytest.raises(RetentionMatrixUnavailableError) as missing:
        load_commercial_matrix(partial)
    assert missing.value.reason == REASON_LEAD_CATEGORIES_MISSING
    assert dropped in missing.value.detail


def test_commercial_intake_without_retention_class_refused(tmp_path: Path) -> None:
    """EP-REG-04b: no source-resolved `retention_class` for a lead category -> refusal, no default."""
    path = _write_matrix(tmp_path, LEAD_CATEGORIES)
    complete = {categoria: categoria for categoria in LEAD_CATEGORIES}
    resolved = qualify_lead_intake(path, source_retention_classes=complete)  # control: the gate can qualify
    assert set(resolved) == set(LEAD_CATEGORIES)
    for violation in (
        None,  # the C6-4 wire: the source declares no class at all
        {},
        {k: v for k, v in complete.items() if k != "lead_intake"},
        complete | {"lead_intake": "SYN-CLASS-NOT-IN-MATRIX"},
        complete | {"supressao_lead": ""},
    ):
        with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
            qualify_lead_intake(path, source_retention_classes=violation)
        assert excinfo.value.reason == REASON_RETENTION_CLASS_UNRESOLVED


@pytest.mark.parametrize(
    ("categoria", "uso"),
    sorted((categoria, uso) for categoria, usos in REFUSED_CATEGORY_USES.items() for uso in usos),
)
def test_ex_beneficiary_closed_uses_refused_even_with_a_ratified_row(
    tmp_path: Path, categoria: str, uso: str
) -> None:
    """ADDENDUM-01 §7 (e) / RT-07 B / RT-D1: no proactive reactivation of an ex-beneficiary, the
    admission consent is not a marketing basis after cancellation, and the cancellation reason is
    never linked to the subject -- refused even when a ratified row for the category exists."""
    matrix = load_retention_matrix(_write_matrix(tmp_path, (categoria,)))
    with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
        admit_category_use(matrix, categoria, uso)
    assert excinfo.value.reason == REASON_USE_REFUSED
    assert {"reativacao_proativa", "marketing_consentimento_admissao"} <= REFUSED_CATEGORY_USES[
        "ex_beneficiario"
    ]
    assert "vinculo_ao_titular" in REFUSED_CATEGORY_USES["motivo_cancelamento"]


@pytest.mark.parametrize("categoria", CANDIDATE_ONLY_CATEGORIES + LEAD_CATEGORIES)
def test_candidate_rows_are_not_usable_without_a_ratified_matrix(tmp_path: Path, categoria: str) -> None:
    """The consumer refuses un-ratified rows: no matrix, or a matrix without the row."""
    for matrix in (None, load_retention_matrix(_write_matrix(tmp_path, ("cadastrais_contratuais",)))):
        with pytest.raises(RetentionMatrixUnavailableError) as excinfo:
            admit_category_use(matrix, categoria, "SYN-USO")
        assert excinfo.value.reason == REASON_CATEGORY_UNRATIFIED
    ratified = load_retention_matrix(_write_matrix(tmp_path, (categoria,)))
    assert admit_category_use(ratified, categoria, "SYN-USO").categoria == categoria
