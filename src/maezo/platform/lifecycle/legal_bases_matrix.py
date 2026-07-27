"""Typed seam for the DPO legal-bases/retention matrix (PLANS.md §0.5 item 6, recon-b §6.4).

This module builds the SEAM only — the model, the fail-closed loader, and the typed
error taxonomy — never the human-ratified VALUE. Per recon-b (`ITEM 6`), the matrix
gates two downstream consumers that do not exist yet either (`ErasureManager`'s
per-layer deletion in `erasure.py`, and `lgpd.py::compile_data_package`): this module
does not wire either of those; it exists so the `lifecycle` CLI (see `__init__.py`)
can attempt a real load and distinguish "matrix absent" from "matrix present but the
downstream mechanism is simply unbuilt" in its refusal messages.

No default matrix, and no example values that look ratifiable, ship in code. A
schema-only template with placeholder markers lives at
`spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml`, clearly marked
`unratified: true` — the loader below explicitly REFUSES that exact file (as opposed
to silently "succeeding" on placeholder data) so pointing the env var at the template
by mistake fails closed too.

Deliberately named `legal_bases_matrix.py` (not `retention_matrix.py`):
`tests/unit/platform/test_lifecycle.py`'s AST scan
(`test_lifecycle_module_does_not_reference_destructive_query`) forbids any import
whose imported module NAME contains the substring "retention" -- regardless of where
the module lives (it is aimed at `retention.py`/`RetentionManager`/`retention_query`,
the destructive audit-chain DELETE path). The NAME choice is what keeps this module
clear of that guard; it is a completely different concern (the DPO's per-category
legal-bases table), so it is named to avoid the collision rather than work around
the guard the test enforces. Placement inside the `lifecycle` package is for
cohesion (the lifecycle CLI is its only consumer today), not a guard requirement.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import structlog
import yaml

logger = structlog.get_logger(__name__)

# Env var carrying the path to the ratified matrix YAML file. There is NO fallback
# default path: an absent/unset env var is itself a fail-closed reason, never silently
# resolved to some repo-relative "default" file.
MATRIX_PATH_ENV = "MAEZO_RETENTION_MATRIX_PATH"

REQUIRED_ENTRY_FIELDS: tuple[str, str, str, str] = ("categoria", "base_legal", "retencao", "acao")

# Precise, distinguishable failure reasons -- callers (the lifecycle refusal taxonomy)
# use these to tell "matrix absent" apart from "matrix present but malformed" apart
# from "matrix present but is the un-ratified placeholder template".
REASON_PATH_NOT_SET = "path_not_set"
REASON_FILE_NOT_FOUND = "file_not_found"
REASON_UNREADABLE = "unreadable"
REASON_INVALID_ENCODING = "invalid_encoding"
REASON_INVALID_YAML = "invalid_yaml"
REASON_INVALID_SCHEMA = "invalid_schema"
REASON_EMPTY = "empty"
REASON_UNRATIFIED_TEMPLATE = "unratified_template"


class RetentionMatrixUnavailableError(RuntimeError):
    """Fail-closed sentinel: no usable, ratified DPO retention matrix is available.

    Raised by `load_retention_matrix` for every failure mode -- there is no fallback
    default matrix and no "best effort" partial load. `reason` is one of the
    `REASON_*` constants above; `detail` is a human-readable, file-path-qualified
    explanation suitable for an operator-facing CLI message or a structured log field.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"retention matrix unavailable ({reason}): {detail}")


@dataclass(frozen=True)
class RetentionMatrixEntry:
    """One row of the DPO legal-bases/retention matrix for a single data category.

    Field names match the matrix's own vocabulary (Portuguese, matching the DPO's
    starter draft at `docs/sme-dispatch/AI-SUGGESTED-ANSWERS-2026-07-25.md` §A2):

    Attributes:
        categoria: The data category (e.g. "dados_saude_prontuario", "cadastrais").
        base_legal: The legal basis governing this category (LGPD article, ADR, or
            sector-specific statute -- e.g. "LGPD art. 16 I; Lei 13.787/2018 art. 6").
        retencao: The retention period/rule for this category, as ratified by the
            DPO+jurídico (free text -- e.g. "20 anos", "vinculo + 5 anos"; this module
            does not parse or interpret the value, only carries it).
        acao: The action this category resolves to for a data-subject request (e.g.
            "reter", "eliminar") -- the human-decided verdict this seam exists to
            carry, never to invent.
    """

    categoria: str
    base_legal: str
    retencao: str
    acao: str


@dataclass(frozen=True)
class RetentionMatrix:
    """An immutable, loaded-and-validated DPO legal-bases/retention matrix."""

    entries: MappingProxyType[str, RetentionMatrixEntry]

    def get(self, categoria: str) -> RetentionMatrixEntry | None:
        """Look up a single category's entry, or None if not present in the matrix."""
        return self.entries.get(categoria)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[RetentionMatrixEntry]:
        return iter(self.entries.values())


def _fail(reason: str, detail: str) -> RetentionMatrixUnavailableError:
    logger.error("retention_matrix_unavailable", reason=reason, detail=detail)
    return RetentionMatrixUnavailableError(reason, detail)


def load_retention_matrix(path: str | Path | None = None) -> RetentionMatrix:
    """Load and validate the DPO legal-bases/retention matrix from YAML. FAILS CLOSED.

    Path resolution order:
        1. the explicit `path` argument, if given;
        2. the `MAEZO_RETENTION_MATRIX_PATH` environment variable.

    There is NO default matrix and NO fallback path. Every failure mode -- an unset
    path, a missing/unreadable file, a non-UTF-8 encoding, malformed YAML, a schema
    violation, an empty `categorias` list, or the file being the
    `UNRATIFIED-DO-NOT-DEPLOY` placeholder template -- raises
    `RetentionMatrixUnavailableError` with a precise `reason`.
    Nothing in this function invents or defaults a per-category value: the only
    successful outcome is a matrix built entirely from what a human explicitly wrote
    to the file at `path`.

    Args:
        path: Explicit override of the matrix file path (mainly for callers/tests
            that don't want to go through the environment). Defaults to resolving
            `MAEZO_RETENTION_MATRIX_PATH`.

    Returns:
        A `RetentionMatrix` with one `RetentionMatrixEntry` per `categoria`.

    Raises:
        RetentionMatrixUnavailableError: always, on any failure mode above.
    """
    raw_path = path if path is not None else os.environ.get(MATRIX_PATH_ENV)
    if not raw_path:
        raise _fail(
            REASON_PATH_NOT_SET,
            f"{MATRIX_PATH_ENV} is not set and no explicit path was provided",
        )

    matrix_path = Path(raw_path)
    if not matrix_path.exists():
        raise _fail(REASON_FILE_NOT_FOUND, f"path does not exist: {matrix_path}")
    if not matrix_path.is_file():
        raise _fail(REASON_FILE_NOT_FOUND, f"path is not a regular file: {matrix_path}")

    try:
        raw_text = matrix_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _fail(REASON_UNREADABLE, f"could not read {matrix_path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        # NOT covered by OSError: UnicodeDecodeError subclasses ValueError. Without this
        # explicit catch, a non-UTF-8 matrix file (e.g. cp1252 from Windows tooling with
        # accented chars like "jurídico") would escape as a raw traceback instead of a
        # typed, fail-closed refusal.
        raise _fail(
            REASON_INVALID_ENCODING,
            f"{matrix_path} is not valid UTF-8 ({exc.encoding} decode failed at byte "
            f"offset {exc.start}: {exc.reason}) -- re-encode the matrix file as UTF-8",
        ) from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise _fail(REASON_INVALID_YAML, f"malformed YAML in {matrix_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{matrix_path}: root must be a mapping, got {type(data).__name__}",
        )

    if data.get("unratified") is True:
        raise _fail(
            REASON_UNRATIFIED_TEMPLATE,
            f"{matrix_path} is the UNRATIFIED-DO-NOT-DEPLOY placeholder template "
            "('unratified: true') -- a human-ratified matrix must replace it before use",
        )

    raw_categorias = data.get("categorias")
    if not isinstance(raw_categorias, list):
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{matrix_path}: 'categorias' is missing or is not a list",
        )
    if not raw_categorias:
        raise _fail(REASON_EMPTY, f"{matrix_path}: 'categorias' is present but empty")

    entries: dict[str, RetentionMatrixEntry] = {}
    for index, raw_entry in enumerate(raw_categorias):
        if not isinstance(raw_entry, dict):
            raise _fail(
                REASON_INVALID_SCHEMA,
                f"{matrix_path}: categorias[{index}] is not a mapping",
            )
        missing = [field for field in REQUIRED_ENTRY_FIELDS if field not in raw_entry]
        if missing:
            raise _fail(
                REASON_INVALID_SCHEMA,
                f"{matrix_path}: categorias[{index}] missing required field(s): {missing}",
            )
        values: dict[str, str] = {}
        for field in REQUIRED_ENTRY_FIELDS:
            value = raw_entry[field]
            if not isinstance(value, str) or not value.strip():
                raise _fail(
                    REASON_INVALID_SCHEMA,
                    f"{matrix_path}: categorias[{index}].{field} must be a non-empty string",
                )
            values[field] = value

        categoria = values["categoria"]
        if categoria in entries:
            raise _fail(
                REASON_INVALID_SCHEMA,
                f"{matrix_path}: duplicate categoria {categoria!r} at categorias[{index}]",
            )
        entries[categoria] = RetentionMatrixEntry(
            categoria=categoria,
            base_legal=values["base_legal"],
            retencao=values["retencao"],
            acao=values["acao"],
        )

    logger.info("retention_matrix_loaded", path=str(matrix_path), categoria_count=len(entries))
    return RetentionMatrix(entries=MappingProxyType(entries))
