"""TISS/XSD schema-validation seam for SP-OP-ANS-SUBMIT-001 (T2.6-2, design §2.B).

**The defect this replaces.** `ans_submit.validate_data` used to perform NO XSD parse at all —
it echoed the inbound `submission.schema_valid` flag straight through (same non-computing
pattern as the `lgpd_anonimizado` echo, GAP-ANS-5, `docs/review-queue.md:115`). The BPMN
`ST_ValidateSchema` and the admissibility DMN input `in_schema_valid`
(`ans_submission_admissibility.dmn:32-33`) both assume a REAL schema validation produced
`schema_valid` — nothing computed it. This module is the real computation.

**Design (docs/design/T2.6-ans-submission-rescope.md §2.B):**

- **Version pin lives in config, not hardcoded** (`TISS_SCHEMA_VERSION_ENV`,
  `MAEZO_TISS_SCHEMA_VERSION`). The exact padrão-TISS (Componente de Comunicação, RN 501/2022)
  version in force is an OPEN SME QUESTION (design §7, regulatório) — this module NEVER guesses
  a version. Unset => fail-closed (no schema resolved, `schema_valid=False`).
- **Vendored XSDs** are expected at `<schema_root>/<version>/<report_type>.xsd`
  (`<schema_root>` defaults to `<spec>/schemas/tiss/`, resolved via the single T0.3 mechanism
  `maezo.agents.resolve_spec_dir` — same resolution `ceilings.py`'s `CeilingResolver` uses for
  the autonomy matrix; override via `MAEZO_TISS_SCHEMA_ROOT`, a test-pinning hook mirroring
  `ceilings.py`'s `AUTONOMY_CORE_PATH`).
- **EXTERNAL-DEPENDENCY FINDING (report this to the R1 verifier — not fabricated here):** the
  real ANS "Padrão TISS" XSD set is NOT vendored in this repo today. ANS publishes it as a
  downloadable ZIP bundle (public, per design §2.B), but sourcing + checksum-pinning the
  concrete version is out of scope for this task (SME-gated: which version, and whether DIOPS
  even flows through TISS XSD at all — design §2.B/§7). This module ships the SEAM — loader +
  validator + fail-closed wiring — proven against a minimal, clearly-labeled FIXTURE XSD in
  `tests/unit/tools/workers/test_tiss_schema.py` (NOT a real TISS schema). Until the real
  vendored set + version land, `MAEZO_TISS_SCHEMA_VERSION` stays unset in every environment, so
  `TissSchemaValidator` fails closed everywhere — the correct posture per design §2.B ("Because
  assemble is a stub today ... there is no real XML to validate → validation stays False →
  human").
- **`dataset_ref` as a local-file resolution (provisional, documented).** The contract describes
  `dataset_ref` as a "ponteiro de storage" (`SP-OP-ANS-SUBMIT-001.md:60`) — in a fully-wired
  future it would resolve via the same blocked `mcp-regdata` channel `prepare_submission`
  delegates to (AWS-blocked, issue #16). Fetching from a real object store is out of scope here
  (that channel does not exist yet). This validator resolves `dataset_ref` as a LOCAL FILESYSTEM
  PATH to the assembled dataset's XML — the simplest honest interpretation available without
  fabricating a network fetch. Today's stub `prepare_submission` produces a synthetic string
  (`f"dataset-{report_type}-{competencia}"`), which is never a real path, so this branch
  legitimately fails closed in production until `assemble` is real. Tests exercise the real
  validation path by pointing `dataset_ref` at an actual temp XML file.

**Fail-closed rules (never raises for a data problem — `validate_data` routes False to
`UT_CorrigirPendenciaEnvio`, never a reject):**

1. No version pinned (config unset) -> `schema_valid=False`.
2. No vendored XSD for `(version, report_type)` -> `schema_valid=False`.
3. `dataset_ref` blank, or does not resolve to an existing file -> `schema_valid=False`.
4. `dataset_ref` resolves but is not well-formed XML -> `schema_valid=False`.
5. Well-formed XML that fails schema validation -> `schema_valid=False` + structured errors
   (`lxml`'s `error_log`, stringified).
6. Well-formed XML that validates cleanly against the pinned schema -> `schema_valid=True`.

**The TWO RUNTIME REGIMES this produces (live-confirmed against the real engine — R1
validate3 report):**

- **UNPINNED (production TODAY, every environment):** `MAEZO_TISS_SCHEMA_VERSION` unset =>
  rule 1 fires => `schema_valid` is ALWAYS False => the admissibility DMN's
  `[-, false, -] -> PENDENTE` row routes EVERY submission — including `origem_envio=nip_filing`
  — to `UT_CorrigirPendenciaEnvio` (human pendency). This pre-empts the SEGUE_ENVIO branch, so
  the nip_filing juridical review (`UT_RevisarEnvioJuridico`) is UNREACHABLE until a version is
  pinned + XSDs vendored. That re-routing is the intended fail-closed posture (never
  auto-transmits, never rejects), NOT a defect — but any consumer expecting the SEGUE_ENVIO
  paths must account for it.
- **PINNED (dev/test today; production once SME unblocks):** a version is pinned and the XSD
  set resolves => `schema_valid` is genuinely computed (rules 4-6), SEGUE_ENVIO becomes
  reachable for schema-valid payloads, and schema-invalid payloads still route PENDENTE. Proven
  live via the `ans_probe_tiss_pinned` fixture
  (`tests/integration/processes/test_sp_op_ans_submit_001.py`, `test_tiss_pinned_*`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog
from lxml import etree

from maezo.agents import resolve_spec_dir

logger = structlog.get_logger(__name__)

#: Config seam (design §2.B: "Store the pinned version in config, not hardcoded in the
#: worker"). The padrão-TISS version in force is an OPEN SME QUESTION (design §7) — unset =>
#: fail-closed (no schema resolved, never a guessed version).
TISS_SCHEMA_VERSION_ENV = "MAEZO_TISS_SCHEMA_VERSION"

#: Vendored-schema-root override — test-pinning hook, mirrors `ceilings.py`'s
#: `AUTONOMY_CORE_PATH`. Default resolves to `<spec>/schemas/tiss/` via `resolve_spec_dir()`.
#: Real ANS TISS XSDs are NOT vendored there today (external dependency — see module docstring).
TISS_SCHEMA_ROOT_ENV = "MAEZO_TISS_SCHEMA_ROOT"


def _default_schema_root() -> Path:
    """`<spec>/schemas/tiss/` via the single T0.3 mechanism (no second resolution scheme)."""
    return resolve_spec_dir() / "schemas" / "tiss"


@dataclass(frozen=True, slots=True)
class TissValidationResult:
    """Outcome of a single TISS/XSD validation attempt (design §2.B)."""

    schema_valid: bool
    errors: tuple[str, ...]
    tiss_schema_version: str | None


@lru_cache(maxsize=64)
def _load_schema_cached(xsd_path: str) -> etree.XMLSchema | None:
    """Parse + compile an XSD (cached by resolved path — schemas are static vendored files).

    `None` on any load failure (missing/malformed XSD) — the caller fails closed. Mirrors
    `ceilings.py`'s `_load_matrix_cached` "swallow-to-None, cache the failure" posture: static
    config, so a persisted `None` reflects reality until a process restart.
    """
    try:
        return etree.XMLSchema(etree.parse(xsd_path))
    except OSError as exc:
        logger.warning("tiss_schema.xsd_unreadable", xsd_path=xsd_path, error=str(exc))
        return None
    except etree.XMLSyntaxError as exc:
        logger.warning("tiss_schema.xsd_malformed", xsd_path=xsd_path, error=str(exc))
        return None
    except etree.XMLSchemaParseError as exc:
        logger.warning("tiss_schema.xsd_schema_invalid", xsd_path=xsd_path, error=str(exc))
        return None


class TissSchemaValidator:
    """Resolves the pinned padrão-TISS XSD for a `report_type` and validates an assembled
    dataset's XML against it (design §2.B).

    Stateless besides the configured overrides; safe to build once per worker registration
    (mirrors `CeilingResolver`'s shape — a local, deterministic resolver, not a networked
    transport, so there is no Real/Fake/Refusing triple here: one real implementation,
    test-pinned via explicit constructor overrides). `ans_submit.validate_data` defaults to a
    fresh instance when no `tiss_validator` seam is injected.
    """

    def __init__(self, *, schema_root: str | Path | None = None, version: str | None = None) -> None:
        # Explicit overrides pin the resolution (tests). `None` => environment-driven default,
        # resolved LAZILY in `validate()` so constructing/importing this module never touches
        # `resolve_spec_dir()` (which fails closed by raising when `spec/` is absent).
        self._schema_root_override: str | None = str(schema_root) if schema_root is not None else None
        self._version_override = version

    def _resolved_version(self) -> str | None:
        if self._version_override:
            return self._version_override
        return os.environ.get(TISS_SCHEMA_VERSION_ENV) or None

    def _resolved_schema_root(self) -> Path:
        if self._schema_root_override:
            return Path(self._schema_root_override)
        env_override = os.environ.get(TISS_SCHEMA_ROOT_ENV)
        if env_override:
            return Path(env_override)
        return _default_schema_root()

    def validate(self, *, report_type: str, dataset_ref: str) -> TissValidationResult:
        """Validate `dataset_ref`'s XML against the pinned `report_type` XSD. Never raises for a
        data/config problem — every branch returns a `TissValidationResult` (fail-closed)."""
        version = self._resolved_version()
        if not version:
            return TissValidationResult(
                schema_valid=False,
                errors=(
                    f"versao padrao-TISS nao pinada (config {TISS_SCHEMA_VERSION_ENV} ausente) — "
                    "SME-gated (design §2.B/§7), fail-closed",
                ),
                tiss_schema_version=None,
            )

        try:
            schema_root = self._resolved_schema_root()
        except Exception as exc:  # fail-closed: unresolvable config never validates
            logger.warning("tiss_schema.root_unresolved", error=str(exc))
            return TissValidationResult(
                schema_valid=False,
                errors=(f"schema root nao resolvivel: {exc}",),
                tiss_schema_version=version,
            )

        xsd_path = schema_root / version / f"{report_type}.xsd"
        if not xsd_path.is_file():
            return TissValidationResult(
                schema_valid=False,
                errors=(
                    f"XSD nao vendorizado para report_type={report_type!r} versao={version!r} ({xsd_path})",
                ),
                tiss_schema_version=version,
            )

        schema = _load_schema_cached(str(xsd_path))
        if schema is None:
            return TissValidationResult(
                schema_valid=False,
                errors=(f"falha ao carregar XSD {xsd_path}",),
                tiss_schema_version=version,
            )

        if not dataset_ref.strip():
            return TissValidationResult(
                schema_valid=False,
                errors=("dataset_ref ausente para validacao XSD",),
                tiss_schema_version=version,
            )

        dataset_path = Path(dataset_ref)
        if not dataset_path.is_file():
            return TissValidationResult(
                schema_valid=False,
                errors=(f"dataset_ref nao resolve a um arquivo XML existente: {dataset_ref!r}",),
                tiss_schema_version=version,
            )

        try:
            xml_doc = etree.parse(str(dataset_path))
        except etree.XMLSyntaxError as exc:
            return TissValidationResult(
                schema_valid=False,
                errors=(f"XML malformado em {dataset_ref!r}: {exc}",),
                tiss_schema_version=version,
            )

        if schema.validate(xml_doc):
            return TissValidationResult(schema_valid=True, errors=(), tiss_schema_version=version)

        # `lxml-stubs`' `_ErrorLog` stub does not declare `__iter__` (attr-defined under mypy
        # strict) even though it IS iterable at runtime; `str(error_log)` is lxml's own documented
        # newline-joined rendering of every logged entry — split back into one string per entry.
        errors = tuple(str(schema.error_log).splitlines())
        return TissValidationResult(schema_valid=False, errors=errors, tiss_schema_version=version)
