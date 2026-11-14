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
- **Version pin (R-068, F-066/PEND-050; owner position DEC-17 -> R-036, issue #1081,
  2026-10-29).** The worker pins the Padrao TISS edition Setembro/2026, Componente de Comunicacao
  `04.03.00` and `01.06.00` (`PINNED_TISS_SCHEMA_VERSIONS`). `MAEZO_TISS_SCHEMA_VERSION` only
  SELECTS inside that closed set; any other environment value fails closed. `04.03.00` shares its
  literal and source-pin digest with the provider gateway (`gateway/tiss/provider_schema.py`), so
  there is one version path, not two. `01.06.00` (Monitoramento TISS) is vendored byte-for-byte
  from the official ANS zip in `<spec>/schemas/tiss/01.06.00/` with provenance and sha256 in
  `ans-source-pin.json`. A pinned version binds validation to BYTES: the provenance manifest
  must match the code digest, every file it lists must match on disk, and the `<report_type>.xsd`
  must be one of those files. The env var stays UNSET by default (dark), so production keeps
  the fail-closed posture below.
- **What the pin does NOT do (declared limits).** No report_type of the contract maps to an
  official XSD today (`RN_424_TISS_MONITORAMENTO` -> `tissMonitoramentoV1_06_00.xsd` is an
  SME/regulatory mapping, not decided here), and the official `01.06.00` set is itself
  incomplete: `tissComplexTypesMonitoramentoV1_06_00.xsd` includes
  `tissSimpleTypesMonitoramentoV1_05_01.xsd`, absent from the zip (recorded in the manifest; no
  alias, remap or fallback). So even pinned, `schema_valid` stays False for real payloads. The
  ratification-gated pin in `spec/policies/ans/tiss-schema-pin.yaml` is a separate human act.
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

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import structlog
from lxml import etree

from maezo.agents import resolve_spec_dir
from maezo.gateway.tiss.provider_schema import CATALOGUE_VERSION as BILATERAL_CATALOGUE_VERSION
from maezo.gateway.tiss.provider_schema import SOURCE_PIN_SHA256 as BILATERAL_SOURCE_PIN_SHA256

logger = structlog.get_logger(__name__)

#: Config seam (design §2.B: "Store the pinned version in config, not hardcoded in the
#: worker"). Since R-068 the value only SELECTS inside `PINNED_TISS_SCHEMA_VERSIONS`; unset =>
#: fail-closed (no schema resolved, never a guessed version).
TISS_SCHEMA_VERSION_ENV = "MAEZO_TISS_SCHEMA_VERSION"

#: R-068: Componente de Comunicacao Monitoramento TISS, edicao Setembro/2026 (catalogo ANS
#: "04.03.00 e 01.06.00"). Bytes vendorizados em `<spec>/schemas/tiss/01.06.00/`.
MONITORAMENTO_CATALOGUE_VERSION = "01.06.00"
MONITORAMENTO_SOURCE_PIN_SHA256 = "33e3afa7ee5fe6bfd5bba7dc901d269f9923b7ebe44ca33247d5a18ea743a48a"

#: R-068 (DEC-17 -> R-036, posicao do dono na issue #1081): versao -> (manifesto de proveniencia
#: dentro de `<root>/<versao>/`, sha256 desse manifesto). Lista FECHADA; trocar a edicao e um
#: change revisado, nunca um valor de ambiente.
PINNED_TISS_SCHEMA_VERSIONS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        BILATERAL_CATALOGUE_VERSION: ("provider-source-pin.json", BILATERAL_SOURCE_PIN_SHA256),
        MONITORAMENTO_CATALOGUE_VERSION: ("ans-source-pin.json", MONITORAMENTO_SOURCE_PIN_SHA256),
    }
)

#: Teto por arquivo do conjunto pinado (o maior XSD oficial tem ~174 KB).
_MAX_PINNED_FILE_BYTES = 2 * 1024 * 1024

#: Vendored-schema-root override — test-pinning hook, mirrors `ceilings.py`'s
#: `AUTONOMY_CORE_PATH`. Default resolves to `<spec>/schemas/tiss/` via `resolve_spec_dir()`.
#: Official ANS bytes for the pinned versions live there (R-068); no report_type XSD is mapped yet.
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


def _hardened_parser() -> etree.XMLParser:
    """Same posture as `gateway/tiss/provider_schema.py::_parser()`: no entity resolution, no DTD
    loading, no network, no huge trees."""
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
        remove_comments=False,
        strip_cdata=False,
    )


class _PinnedFilesResolver(etree.Resolver):
    """Resolves `xs:include`/`xs:import` ONLY from the already-verified bytes of the pinned set."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        super().__init__()
        self._files = files

    def resolve(self, url: str, pubid: str | None, context: object) -> object:  # type: ignore[override]
        name = url.rsplit("/", 1)[-1]
        if name not in self._files:
            raise etree.XMLSchemaParseError(f"include fora do conjunto pinado: {name}")
        return self.resolve_string(self._files[name], context, base_url=f"tiss-pin:///{name}")


def _read_regular_file(path: Path) -> bytes | None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    with path.open("rb") as source:
        data = source.read(_MAX_PINNED_FILE_BYTES + 1)
    return data if len(data) <= _MAX_PINNED_FILE_BYTES else None


def _manifest_file_digests(manifest: Any) -> dict[str, str]:
    """`files` maps of a provenance manifest: top level (ans-source-pin.json) or per profile
    (provider-source-pin.json)."""
    maps: list[Any] = []
    if isinstance(manifest, dict):
        maps.append(manifest.get("files"))
        profiles = manifest.get("profiles")
        if isinstance(profiles, list):
            maps.extend(p.get("files") for p in profiles if isinstance(p, dict))
    digests: dict[str, str] = {}
    for mapping in maps:
        if isinstance(mapping, dict):
            for name, digest in mapping.items():
                if isinstance(name, str) and isinstance(digest, str):
                    digests[name] = digest
    return digests


def _verified_pinned_files(
    version_dir: Path, manifest_name: str, manifest_sha256: str
) -> dict[str, bytes] | None:
    """Bytes of every file the pinned provenance manifest lists, or `None` if the manifest is
    missing/altered or any listed file is missing/altered/not a regular file."""
    if version_dir.is_symlink():
        return None
    manifest_bytes = _read_regular_file(version_dir / manifest_name)
    if manifest_bytes is None or hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        return None
    try:
        digests = _manifest_file_digests(json.loads(manifest_bytes))
    except ValueError:
        return None
    if not digests:
        return None
    files: dict[str, bytes] = {}
    for name, digest in digests.items():
        if "/" in name or "\\" in name or name in {"", ".", ".."}:
            return None
        data = _read_regular_file(version_dir / name)
        if data is None or hashlib.sha256(data).hexdigest() != digest:
            return None
        files[name] = data
    return files


def _compile_pinned_schema(files: Mapping[str, bytes], entrypoint: str) -> etree.XMLSchema | None:
    """Compile from the verified in-memory bytes (no re-read from disk, no TOCTOU)."""
    parser = _hardened_parser()
    parser.resolvers.add(_PinnedFilesResolver(files))
    try:
        root = etree.fromstring(files[entrypoint], parser, base_url=f"tiss-pin:///{entrypoint}")
        return etree.XMLSchema(root)
    except (ValueError, etree.LxmlError) as exc:
        logger.warning("tiss_schema.pinned_xsd_unloadable", entrypoint=entrypoint, error=str(exc))
        return None


@lru_cache(maxsize=64)
def _load_schema_cached(xsd_path: str) -> etree.XMLSchema | None:
    """Parse + compile an XSD (cached by resolved path — schemas are static vendored files).

    Used only for versions OUTSIDE the pin passed through the explicit constructor test seam.
    `None` on any load failure (missing/malformed XSD) — the caller fails closed. Mirrors
    `ceilings.py`'s `_load_matrix_cached` "swallow-to-None, cache the failure" posture: static
    config, so a persisted `None` reflects reality until a process restart.
    """
    try:
        return etree.XMLSchema(etree.parse(xsd_path, _hardened_parser()))
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

    def _resolved_version(self) -> tuple[str | None, bool]:
        """(version, came_from_environment)."""
        if self._version_override:
            return self._version_override, False
        return (os.environ.get(TISS_SCHEMA_VERSION_ENV) or None), True

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
        version, from_env = self._resolved_version()
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

        pin = PINNED_TISS_SCHEMA_VERSIONS.get(version)
        if pin is None and from_env:
            # R-068: the environment only selects INSIDE the pinned edition.
            return TissValidationResult(
                schema_valid=False,
                errors=(
                    f"versao padrao-TISS {version!r} fora do pin R-068 "
                    f"({', '.join(sorted(PINNED_TISS_SCHEMA_VERSIONS))}), fail-closed",
                ),
                tiss_schema_version=version,
            )

        xsd_name = f"{report_type}.xsd"
        xsd_path = schema_root / version / xsd_name
        schema: etree.XMLSchema | None
        if pin is not None:
            manifest_name, manifest_sha256 = pin
            files = _verified_pinned_files(schema_root / version, manifest_name, manifest_sha256)
            if files is None:
                return TissValidationResult(
                    schema_valid=False,
                    errors=(
                        f"proveniencia do conjunto pinado {version!r} ausente ou alterada "
                        f"({manifest_name}), fail-closed",
                    ),
                    tiss_schema_version=version,
                )
            if xsd_name not in files:
                return TissValidationResult(
                    schema_valid=False,
                    errors=(
                        f"XSD para report_type={report_type!r} fora do manifesto pinado da versao "
                        f"{version!r} (nenhum XSD oficial mapeado), fail-closed",
                    ),
                    tiss_schema_version=version,
                )
            schema = _compile_pinned_schema(files, xsd_name)
        else:
            if not xsd_path.is_file():
                return TissValidationResult(
                    schema_valid=False,
                    errors=(
                        f"XSD nao vendorizado para report_type={report_type!r} "
                        f"versao={version!r} ({xsd_path})",
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
            xml_doc = etree.parse(str(dataset_path), _hardened_parser())
        except etree.XMLSyntaxError as exc:
            return TissValidationResult(
                schema_valid=False,
                errors=(f"XML malformado em {dataset_ref!r}: {exc}",),
                tiss_schema_version=version,
            )
        if xml_doc.docinfo.doctype:  # type: ignore[union-attr]
            return TissValidationResult(
                schema_valid=False,
                errors=(f"DOCTYPE recusado em {dataset_ref!r} (parser endurecido, sem DTD/entidades)",),
                tiss_schema_version=version,
            )

        if schema.validate(xml_doc):
            return TissValidationResult(schema_valid=True, errors=(), tiss_schema_version=version)

        # `lxml-stubs`' `_ErrorLog` stub does not declare `__iter__` (attr-defined under mypy
        # strict) even though it IS iterable at runtime; `str(error_log)` is lxml's own documented
        # newline-joined rendering of every logged entry — split back into one string per entry.
        errors = tuple(str(schema.error_log).splitlines())
        return TissValidationResult(schema_valid=False, errors=errors, tiss_schema_version=version)
