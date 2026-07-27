"""Unit tests for `maezo.platform.lifecycle.legal_bases_matrix` — the fail-closed,
typed seam for the DPO legal-bases/retention matrix (PLANS.md §0.5 item 6).

Covers every fail-closed branch of `load_retention_matrix` (absent path, missing
file, non-file path, malformed YAML, non-mapping root, missing/non-list/empty
`categorias`, per-entry schema violations, duplicate categoria, the UNRATIFIED
placeholder-template guard) plus the one successful, valid-schema parse path — and
proves the on-disk schema template under `spec/policies/retention/` is itself
refused (never silently usable).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.platform.lifecycle.legal_bases_matrix import (
    MATRIX_PATH_ENV,
    REASON_EMPTY,
    REASON_FILE_NOT_FOUND,
    REASON_INVALID_SCHEMA,
    REASON_INVALID_YAML,
    REASON_PATH_NOT_SET,
    REASON_UNRATIFIED_TEMPLATE,
    RetentionMatrix,
    RetentionMatrixEntry,
    RetentionMatrixUnavailableError,
    load_retention_matrix,
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
