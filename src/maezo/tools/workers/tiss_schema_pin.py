"""TISS-schema-pin ratification gate — dark build (T2.6-2 companion seam, NOT wired this wave).

CALIBRATION SCOPE. This module is a validator built AHEAD of an SME-supplied artifact: it
validates ANS submission payloads against an XSD loaded from a ratification-gated artifact
registry (`spec/policies/ans/tiss-schema-pin.yaml`), but the pin manifest ships DRAFT
(`ratificado: false`, `status: DRAFT`) and the XSD it references
(`spec/policies/ans/synthetic-tiss-v1.xsd`) is an explicitly-labeled SYNTHETIC stand-in — NOT the
real ANS Padrão-TISS schema. Every fixture and every test body in
`tests/unit/tools/workers/test_tiss_schema_pin.py` is built and proven against that synthetic
artifact. The SME supplies only the real schema (and the ratification) later; nothing here
guesses, fabricates, or approximates real TISS wire content.

RELATIONSHIP TO THE ALREADY-SHIPPED T2.6-2 SEAM (`tools/workers/tiss_schema.py`). That module's
`TissSchemaValidator` is ALREADY wired into `ans_submit.validate_data`/`validate_entry` (via the
`tiss_validator` seam threaded through `register_ans_submit_workers`) and resolves its XSDs from
`MAEZO_TISS_SCHEMA_VERSION` + `<spec>/schemas/tiss/<version>/<report_type>.xsd`
(`resolve_spec_dir()`-based, per-report-type). It is UNCHANGED by this module. THIS module is a
SEPARATE, PARALLEL, ratification-gated pin over exactly ONE artifact — a different governance
concern (an auditable human ratification act, mirroring GAP-AUTH-4's
`auth-criteria-ratification.yaml`), not a replacement resolution scheme for the per-report-type
vendored set. Whether/how the two seams should eventually be reconciled (e.g. the ratified pin
becoming the sole per-version authority, or staying a narrower structural gate) is EXPLICITLY
UNDECIDED here — see "SCOPING DECISION" below.

WHAT THE 14-XFAIL ANALYSIS SHOWED (read before writing a line of this module, per task
instructions). `tests/integration/processes/test_sp_op_ans_submit_001.py` carries 14
`@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)` tests. Their CURRENT
cause (the reason constant's own text, corrected from a since-resolved historical "FINDING A"
registration gap) is that they run on the UNPINNED `ans_probe` fixture: `validate_data` resolves a
real `TissSchemaValidator()` with `MAEZO_TISS_SCHEMA_VERSION` unset and no vendored XSDs, which
computes `schema_valid=False` unconditionally — so the `ans_submission_admissibility` DMN's
`[-, false, -] -> PENDENTE` row fires for EVERY submission on that probe, pre-empting
`GW_Admissibilidade`'s `SEGUE_ENVIO`/`REVISAO_HUMANA` branches (and therefore `UT_RevisarEnvio`/
`UT_RevisarEnvioJuridico`) before any of those tests' real assertions run. A SIBLING
`ans_probe_tiss_pinned` fixture already exists and DOES reach `SEGUE_ENVIO` by injecting a pinned
`TissSchemaValidator` at a fixture XSD in `tmp_path` — proving the CAUSE is specifically the
UNPINNED regime, not a structural defect. **Consequence for this module:** the 14 xfails are
UNRELATED to this dark build (they block on the already-wired, already-unpinned seam) and this
module changes NOTHING about them — per task instructions their markers are untouched here; they
flip only when the real schema lands and is proven against the live engine. What this analysis
DOES inform is exactly what a schema-pin gate must validate structurally once wired: an XSD-valid
payload must pass, and each structural violation class a TISS submission can carry — a missing
required element, a wrong-typed value, a cardinality (occurs-count) violation, and a namespace
mismatch — must fail with a diagnostic bounded enough to log/audit without ever carrying payload
content (PHI discipline; the dataset is aggregated/anonymized per ADR-0006, but the schema-
violation diagnostic itself must not become a second channel for raw field values).

THE RATIFICATION RULE (mirrors `spec/processes/dmn/auth-criteria-ratification.yaml`/GAP-AUTH-4 and
the DPO retention-matrix loader, `maezo.platform.lifecycle.legal_bases_matrix.py`, which REFUSES
its own `unratified: true` placeholder template rather than "succeeding" on placeholder data). Full
rule and rationale live as comments in `spec/policies/ans/tiss-schema-pin.yaml` itself (the file a
human reviewer actually edits to ratify); in short, ALL FOUR of `status=="RATIFIED"`,
`ratificado is True`, non-blank `revisor`/`ratificado_em`, and `schema_artifact.synthetic is False`
must agree — one deliberately redundant with another (belt-and-suspenders: forging `ratificado:
true` while `status` stays `"DRAFT"` still refuses; flipping ratification without also swapping the
XSD still refuses) — before `load_tiss_schema_pin` returns anything instead of raising.

GK REVISE (post-GK-review repair) added TWO further, independent ARTIFACT-INTEGRITY gates that sit
below those four human-intent gates — both proven forgeable before this repair, both closed by
comparing the manifest's claims against the ARTIFACT ITSELF rather than trusting fields beside it:
gate 5, `schema_artifact.sha256` must equal a digest re-derived from the XSD's actual bytes on disk
on every load (`REASON_ARTIFACT_DIGEST_MISMATCH` on mismatch — MAJOR-1: a ratified pin previously
survived an in-place XSD swap, a placeholder sha256, and an arbitrary "deadbeef"); gate 6, the XSD's
own parsed `targetNamespace` must not start with `_SYNTHETIC_NAMESPACE_PREFIX`
(`REASON_ARTIFACT_SELF_DECLARES_SYNTHETIC` — MAJOR-2: a forged manifest with
`schema_artifact.synthetic: false` previously loaded while ratifying the verbatim SYNTHETIC XSD).
Both gates 5 and 6 are checked only once gates 1-4 already agree the pin is ratified.

FAIL-CLOSED SPLIT: RAISE vs RETURN. This module deliberately uses BOTH failure shapes the two
studied precedents use, for two DIFFERENT kinds of failure:

- **Governance/availability failures RAISE** `TissSchemaPinUnavailableError` (mirrors
  `RetentionMatrixUnavailableError` — legal_bases_matrix.py): unratified pin, missing/malformed
  manifest, missing/malformed ratified artifact. These are not data-quality outcomes about a
  particular payload — they are "the feature is not turned on yet" governance/config states, and a
  seam that cannot gate must say so loudly rather than silently answer "no". This is the "validator
  refuses with a typed error" the task specifies.
- **Payload-vs-schema outcomes RETURN** a `TissSchemaPinValidationResult` (mirrors
  `TissSchemaValidator`/`TissValidationResult` — tools/workers/tiss_schema.py): once the pin IS
  available, a payload simply conforming or not conforming to the pinned XSD is an ORDINARY,
  expected outcome, never an exception. `.validate()` therefore either raises (pin unavailable) or
  returns a result (pin available) — it is never ambiguous which shape a caller gets for which
  reason.

This split is a deliberate divergence from the auth_criteria.py precedent (which SWALLOWS every
failure to "nothing is ratified" because it is WIRED into a live worker where an exception would
incident a care-authorization request — strictly worse for the beneficiary than routing to human
review). This module is NOT wired into any live worker this wave (see below), so nothing downstream
can be starved by a raise; the typed-error contract is exactly what a future consumer needs to
distinguish "unavailable" from "available and happens to fail" — see `tiss_schema_pin_gate_entry`.

SCOPING DECISION (THIS WAVE). This module ships the validator, its ratification-gated loader, and
a fully tested (but UNREGISTERED) demonstration of how `ans_submit.py` would consume it —
`tiss_schema_pin_gate_entry`, shaped exactly like a `register_ans_submit_workers` dict-boundary
entry function. It is deliberately kept OUT of `ans_submit.py` itself: `ans_submit.py` is
UNTOUCHED by this change (no import, no new parameter, no new registered topic, no change to
`validate_data`/`validate_entry`/`register_ans_submit_workers`), and the 14 xfail markers are
UNTOUCHED. Actual wiring — deciding whether this gate's outcome folds into
`validate_data`'s existing `schema_valid`/`errors` keys, replaces the `TissSchemaValidator` path
entirely, or runs alongside it — is explicitly deferred to a future, separately reviewed change,
to be made once an SME ratifies `spec/policies/ans/tiss-schema-pin.yaml`. Building that seam now,
inert, is exactly what turns "blocked on an SME artifact" into "one data change away from usable"
instead of "not started".
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Hashable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from lxml import etree
from yaml.constructor import ConstructorError as _YamlConstructorError

from maezo.agents import resolve_spec_dir

logger = structlog.get_logger(__name__)

#: Test-pinning / operator escape hatch for the manifest path — mirrors
#: `auth_criteria.MANIFEST_PATH_ENV`. Unset resolves to the repo-committed default (below).
PIN_MANIFEST_PATH_ENV = "MAEZO_TISS_SCHEMA_PIN_MANIFEST_PATH"

# Ratification gate field names (manifest's own vocabulary — see tiss-schema-pin.yaml).
_STATUS = "status"
_STATUS_RATIFIED = "RATIFIED"
_RATIFICADO = "ratificado"
_REVISOR = "revisor"
_RATIFICADO_EM = "ratificado_em"
_SCHEMA_ARTIFACT = "schema_artifact"
_SYNTHETIC = "synthetic"
_XSD_FILENAME = "xsd_filename"
_SHA256 = "sha256"
_PADRAO_TISS_VERSAO = "padrao_tiss_versao"
_REPORT_TYPE = "report_type"

#: The complete, closed set of top-level keys this loader understands (GK-criteria minor-1
#: precedent, auth_criteria.py) — an unrecognized key (typo, or a section someone meant to remove
#: cleanly) refuses the WHOLE manifest rather than silently evaporating unread.
_KNOWN_TOP_LEVEL_KEYS = frozenset(
    {"version", _STATUS, _RATIFICADO, _REVISOR, _RATIFICADO_EM, _SCHEMA_ARTIFACT, "observacao"}
)

#: MINOR-4 (GK REVISE): the SAME wholesale-refusal fence as `_KNOWN_TOP_LEVEL_KEYS`, but for the
#: NESTED `schema_artifact` mapping — the top-level fence does not recurse, so a typo'd nested key
#: (e.g. `sintetico:` for `synthetic:`) previously sat unread beside an otherwise fully-ratified-
#: looking `schema_artifact` instead of refusing the whole manifest.
_KNOWN_SCHEMA_ARTIFACT_KEYS = frozenset(
    {_XSD_FILENAME, _SYNTHETIC, _SHA256, _PADRAO_TISS_VERSAO, _REPORT_TYPE}
)

#: Placeholder-value markers this loader refuses on `schema_artifact.sha256` (MAJOR-1, GK REVISE)
#: — mirrors `amh_inbox.py`'s `PLACEHOLDER_MARKERS`/`_looks_like_placeholder` exactly,
#: reimplemented locally rather than imported: this module already avoids a cross-package import
#: for `_local_name` below for the same stated reason (an odd cross-package import from one
#: worker-seam module into a sibling worker-seam module for one small helper).
_PLACEHOLDER_MARKERS: tuple[str, ...] = ("PENDING", "PLACEHOLDER", "TODO", "TBD", "XXX")

#: MAJOR-2 (GK REVISE): a ratified XSD whose OWN declared `targetNamespace` starts with this
#: prefix self-identifies as a synthetic stand-in — every synthetic fixture in this repo (the
#: shipped `spec/policies/ans/synthetic-tiss-v1.xsd` and this test suite's own fixtures) declares
#: a `targetNamespace` starting with exactly this string. `schema_artifact.synthetic=false` is the
#: manifest's OWN claim about itself; this constant is what lets the loader check that claim
#: against the artifact instead of trusting it unconditionally.
_SYNTHETIC_NAMESPACE_PREFIX = "urn:maezo:synthetic"

# Precise, distinguishable failure reasons (mirrors legal_bases_matrix.py's REASON_* taxonomy) —
# callers/telemetry can tell "manifest absent" from "malformed" from "not yet ratified" apart from
# "ratified but the artifact itself is missing/broken".
REASON_PATH_UNRESOLVED = "path_unresolved"
REASON_FILE_NOT_FOUND = "file_not_found"
REASON_UNREADABLE = "unreadable"
REASON_INVALID_ENCODING = "invalid_encoding"
REASON_INVALID_YAML = "invalid_yaml"
REASON_INVALID_SCHEMA = "invalid_schema"
REASON_STATUS_NOT_RATIFIED = "status_not_ratified"
REASON_RATIFICADO_FLAG_FALSE = "ratificado_flag_false"
REASON_ACCOUNTABILITY_INCOMPLETE = "accountability_incomplete"
REASON_ARTIFACT_STILL_SYNTHETIC = "artifact_still_synthetic"
REASON_ARTIFACT_MISSING = "artifact_missing"
REASON_ARTIFACT_MALFORMED = "artifact_malformed"
#: MAJOR-1 (GK REVISE): `schema_artifact.sha256` does not match the XSD's actual bytes on disk —
#: a ratified pin that only bound itself to a FILENAME survived an in-place swap of that file's
#: content; this is the reason a re-derived-from-disk digest mismatch reports.
REASON_ARTIFACT_DIGEST_MISMATCH = "artifact_digest_mismatch"
#: MAJOR-2 (GK REVISE): the ratified XSD's own `targetNamespace` starts with
#: `_SYNTHETIC_NAMESPACE_PREFIX` — the artifact self-identifies as synthetic regardless of what
#: `schema_artifact.synthetic` claims.
REASON_ARTIFACT_SELF_DECLARES_SYNTHETIC = "artifact_self_declares_synthetic"


class TissSchemaPinUnavailableError(Exception):
    """Fail-closed refusal — the TISS-schema-pin gate cannot be used to validate anything.

    Raised by `load_tiss_schema_pin` for EVERY failure mode (unratified pin; missing/unreadable/
    malformed manifest; ratified-but-missing/malformed artifact) and propagated unchanged by
    `TissSchemaPinValidator.validate`. This is the "typed error" half of the module's fail-closed
    contract — see the module docstring's RAISE-vs-RETURN section for why payload-vs-schema
    outcomes use a different (return) shape instead.

    `.code`/`.message` are plain `str` attributes mirroring `AnsGatewayUnavailableError`
    (`tools/workers/ans_gateway.py`, the sibling T2.6-1 seam in this same package): both are the
    exact duck-typed shape `tools/workers/base.py::reclassify_coded_exception` looks for, so IF a
    future change ever threads this through a `FunctionWorker` boundary, it would reclassify to
    `ValueError` -> `failure(retries=0)` (an engine-guaranteed, human-visible incident, never
    silently engine-retried) the same way `AnsGatewayUnavailableError` already does — see the
    module docstring's "SCOPING DECISION": no such wiring exists yet. `.reason` additionally
    carries one of the `REASON_*` constants above (mirrors `RetentionMatrixUnavailableError.reason`
    — `legal_bases_matrix.py`, the other studied precedent) for structured logging/telemetry
    granularity `.code` alone (one fixed string) cannot provide.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.code = "ERR_TISS_SCHEMA_PIN_UNAVAILABLE"
        self.reason = reason
        self.message = detail
        super().__init__(f"{self.code} ({reason}): {detail}")


@dataclass(frozen=True, slots=True)
class TissSchemaPin:
    """An immutable, already-ratified view of the TISS-schema-pin manifest.

    Returned ONLY when every gate in `load_tiss_schema_pin`'s docstring passes — INCLUDING the two
    artifact-integrity gates added by GK REVISE: `sha256` is re-derived from the XSD's actual
    bytes on disk on every load and MUST match the manifest's own claimed
    `schema_artifact.sha256` (MAJOR-1 — a mismatch raises `REASON_ARTIFACT_DIGEST_MISMATCH`), and
    the XSD's own parsed `targetNamespace` MUST NOT self-identify as a synthetic stand-in (MAJOR-2
    — `_SYNTHETIC_NAMESPACE_PREFIX`; a self-declaring artifact raises
    `REASON_ARTIFACT_SELF_DECLARES_SYNTHETIC` even when `schema_artifact.synthetic` claims
    `false`). A `TissSchemaPin`'s mere existence is therefore proof the referenced XSD is bound to
    this exact ratification by its own reviewed CONTENT — not merely by filename, and not merely
    by the manifest's own unverified self-declaration.
    """

    xsd_path: Path
    sha256: str
    padrao_tiss_versao: str | None
    report_type: str | None
    revisor: str
    ratificado_em: str


@dataclass(frozen=True, slots=True)
class TissSchemaPinDiagnostic:
    """A single BOUNDED schema-violation entry.

    PHI discipline: `kind` is one of a small, fixed vocabulary (never free text from the payload),
    `element` is a SCHEMA element/attribute name (vocabulary the XSD itself defines, never a value
    read out of the submitted document), and `line` is a structural locator. The raw libxml2
    message — which for a type-mismatch violation embeds the actual OFFENDING VALUE — is never
    carried past `_bound()`; see that function's docstring.
    """

    kind: str
    element: str | None
    line: int | None

    def bounded_message(self) -> str:
        parts = [self.kind]
        if self.element:
            parts.append(f"elemento={self.element!r}")
        if self.line:
            parts.append(f"linha={self.line}")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class TissSchemaPinValidationResult:
    """Outcome of one ORDINARY (pin-available) validation attempt.

    Never constructed for an unavailable pin — that path raises `TissSchemaPinUnavailableError`
    instead (module docstring, RAISE-vs-RETURN). `schema_version` surfaces the ratified pin's own
    `padrao_tiss_versao` field for the audit record, mirroring `TissValidationResult.
    tiss_schema_version` in the sibling `tiss_schema.py` seam.
    """

    schema_valid: bool
    diagnostics: tuple[TissSchemaPinDiagnostic, ...]
    schema_version: str | None


def _manifest_default_path() -> Path:
    """`<spec>/policies/ans/tiss-schema-pin.yaml` via the single T0.3 mechanism
    (`resolve_spec_dir`) — the same resolution `auth_criteria.py`/`ceilings.py`/`tiss_schema.py`
    all use, never a second scheme."""
    return resolve_spec_dir() / "policies" / "ans" / "tiss-schema-pin.yaml"


def _is_nonblank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _looks_like_placeholder(value: str) -> str | None:
    """Return the placeholder marker `value` still carries, or `None` if it looks filled-in.

    MAJOR-1 (GK REVISE) — mirrors `amh_inbox.py`'s `_looks_like_placeholder` exactly. Applied to
    `schema_artifact.sha256` only: a literal placeholder string (the shipped manifest's own
    `"PLACEHOLDER-SHA256-sme-must-fill-when-real-xsd-lands"`, or any value containing one of
    `_PLACEHOLDER_MARKERS`) is not a digest, regardless of whether it happens to be non-blank.
    """
    upper = value.upper()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in upper:
            return marker
    return None


class _DuplicateKeySafeLoader(yaml.SafeLoader):
    """`yaml.SafeLoader` that RAISES on a duplicate mapping key instead of silently last-wins.

    Mirrors `auth_criteria.py`'s `_DuplicateKeySafeLoader` exactly (GK-criteria minor-1's
    precedent) — a duplicated `ratificado:`/`status:` key at ANY depth could otherwise let a
    later, unreviewed-looking occurrence silently win over an earlier one with no warning. Scoped
    to THIS loader only; no other `yaml.safe_load` call in the repo is affected.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise _YamlConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key: {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _fail(reason: str, detail: str) -> TissSchemaPinUnavailableError:
    """Log the refusal and return (never raise) the typed error — callers `raise _fail(...)`,
    mirroring `legal_bases_matrix.py`'s `_fail` helper exactly."""
    logger.error("tiss_schema_pin_unavailable", reason=reason, detail=detail)
    return TissSchemaPinUnavailableError(reason, detail)


def _parse(data: object, manifest_path: Path) -> TissSchemaPin:
    """Parse an already-YAML-loaded manifest into a ratified `TissSchemaPin`. NEVER returns a
    partial/best-effort reading — every branch either returns a fully-ratified `TissSchemaPin` or
    raises `TissSchemaPinUnavailableError`."""
    if not isinstance(data, dict):
        raise _fail(
            REASON_INVALID_SCHEMA, f"{manifest_path}: root must be a mapping, got {type(data).__name__}"
        )

    unknown_keys = sorted(str(k) for k in data if k not in _KNOWN_TOP_LEVEL_KEYS)
    if unknown_keys:
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{manifest_path}: unrecognized top-level key(s) {unknown_keys} (known: "
            f"{sorted(_KNOWN_TOP_LEVEL_KEYS)}) — refusing the whole manifest rather than risk a "
            "mistyped or unexpected section silently going unread",
        )

    # Gate 1 — status. Deliberately checked BEFORE `ratificado` (and independent of it): forging
    # `ratificado: true` while `status` is still the literal string "DRAFT" must still refuse.
    status = data.get(_STATUS)
    if status != _STATUS_RATIFIED:
        raise _fail(
            REASON_STATUS_NOT_RATIFIED,
            f"{manifest_path}: status={status!r}, must be exactly the string {_STATUS_RATIFIED!r}",
        )

    # Gate 2 — ratificado (boolean literal True only; a string "true"/1/other truthy value is
    # refused — the repo's fail-closed pin idiom, mirrors auth_criteria.py's `is not True` check).
    if data.get(_RATIFICADO) is not True:
        raise _fail(
            REASON_RATIFICADO_FLAG_FALSE,
            f"{manifest_path}: '{_RATIFICADO}' is not the boolean literal true",
        )

    # Gate 3 — accountability (ADR-0007: who, and when; a partial ratification is not one).
    revisor = data.get(_REVISOR)
    ratificado_em = data.get(_RATIFICADO_EM)
    missing = [
        name
        for name, value in ((_REVISOR, revisor), (_RATIFICADO_EM, ratificado_em))
        if not _is_nonblank_str(value)
    ]
    if missing:
        raise _fail(
            REASON_ACCOUNTABILITY_INCOMPLETE,
            f"{manifest_path}: missing/blank accountability field(s) {missing} — "
            "ratificado=true but who/when is absent is NOT a ratification",
        )

    # Gate 4a — schema_artifact must be a mapping, and (MINOR-4, GK REVISE) every key inside it
    # must be RECOGNIZED. The top-level unknown-key fence above does not recurse, so a typo'd
    # nested key (e.g. `sintetico:` for `synthetic:`) previously sat unread beside an otherwise
    # fully-ratified-looking `schema_artifact` instead of refusing the whole manifest.
    schema_artifact = data.get(_SCHEMA_ARTIFACT)
    if not isinstance(schema_artifact, dict):
        raise _fail(REASON_INVALID_SCHEMA, f"{manifest_path}: '{_SCHEMA_ARTIFACT}' must be a mapping")

    unknown_schema_artifact_keys = sorted(
        str(k) for k in schema_artifact if k not in _KNOWN_SCHEMA_ARTIFACT_KEYS
    )
    if unknown_schema_artifact_keys:
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{manifest_path}: unrecognized {_SCHEMA_ARTIFACT} key(s) {unknown_schema_artifact_keys} "
            f"(known: {sorted(_KNOWN_SCHEMA_ARTIFACT_KEYS)}) — refusing the whole manifest rather "
            "than risk a mistyped or unexpected nested key silently going unread",
        )

    # Gate 4b — schema_artifact.synthetic must be the boolean literal False: this is what
    # operationalizes "activation = SME replaces the synthetic XSD with the real one". Flipping
    # ratification alone, with the artifact still flagged synthetic, still refuses.
    if schema_artifact.get(_SYNTHETIC) is not False:
        raise _fail(
            REASON_ARTIFACT_STILL_SYNTHETIC,
            f"{manifest_path}: {_SCHEMA_ARTIFACT}.{_SYNTHETIC} is not the boolean literal false — "
            "still points at a structure-only synthetic stand-in, not a real vendored XSD",
        )

    xsd_filename = schema_artifact.get(_XSD_FILENAME)
    if not _is_nonblank_str(xsd_filename):
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{manifest_path}: {_SCHEMA_ARTIFACT}.{_XSD_FILENAME} must be a non-empty string",
        )

    # MINOR-3 (GK REVISE): xsd_filename must be a BARE filename inside this manifest's own
    # CODEOWNERS-gated directory. A `../../`-style probe previously LOADED a file outside that
    # directory (the naive `manifest_path.parent / xsd_filename` join never rejected traversal).
    # Reject path separators / '..' segments in the STRING outright, AND independently assert the
    # RESOLVED path is still contained in `manifest_path.parent` — belt-and-suspenders: the string
    # check alone cannot see a symlink placed inside the directory whose TARGET escapes it.
    xsd_filename_str = str(xsd_filename)
    if "/" in xsd_filename_str or "\\" in xsd_filename_str or ".." in xsd_filename_str:
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{manifest_path}: {_SCHEMA_ARTIFACT}.{_XSD_FILENAME} {xsd_filename_str!r} must be a "
            "bare filename inside this manifest's own directory — no path separators, no '..' "
            "traversal segments",
        )

    manifest_dir = manifest_path.parent.resolve()
    xsd_path = (manifest_path.parent / xsd_filename_str).resolve()
    if xsd_path.parent != manifest_dir:
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{manifest_path}: {_SCHEMA_ARTIFACT}.{_XSD_FILENAME} resolves to {xsd_path}, outside "
            f"this manifest's own directory {manifest_dir} — refusing",
        )
    if not xsd_path.is_file():
        raise _fail(REASON_ARTIFACT_MISSING, f"ratified pin names a missing XSD file: {xsd_path}")

    # Gate 5 — schema_artifact.sha256 (MAJOR-1, GK REVISE): binds this ratification to the XSD's
    # actual BYTES, not merely its filename. Proven live-forgeable before this repair: a ratified
    # pin survived an in-place swap of the XSD's content after ratification, a literal placeholder
    # sha256, and an arbitrary non-matching string ("deadbeef") — none of those mean "the
    # ratification covers this exact artifact". Mirrors amh_inbox.py's `migration_digest()` +
    # `load_inbox_ratification`'s re-derive-and-compare (the W2 standard for binding a human
    # approval to bytes, not a name): recomputed from disk on EVERY load, never trusted as-is.
    sha256_value = schema_artifact.get(_SHA256)
    if not _is_nonblank_str(sha256_value):
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{manifest_path}: {_SCHEMA_ARTIFACT}.{_SHA256} must be a non-empty string",
        )
    placeholder_marker = _looks_like_placeholder(str(sha256_value))
    if placeholder_marker is not None:
        raise _fail(
            REASON_INVALID_SCHEMA,
            f"{manifest_path}: {_SCHEMA_ARTIFACT}.{_SHA256} still carries the placeholder marker "
            f"{placeholder_marker!r} — a half-filled digest is not a ratification",
        )

    try:
        xsd_bytes = xsd_path.read_bytes()
    except OSError as exc:
        raise _fail(REASON_ARTIFACT_MISSING, f"could not read ratified XSD at {xsd_path}: {exc}") from exc

    actual_digest = hashlib.sha256(xsd_bytes).hexdigest()
    if str(sha256_value).strip().lower() != actual_digest:
        raise _fail(
            REASON_ARTIFACT_DIGEST_MISMATCH,
            f"{manifest_path}: {_SCHEMA_ARTIFACT}.{_SHA256} is {str(sha256_value)!r} but {xsd_path} "
            f"hashes to {actual_digest!r} — the ratification does not cover the XSD on disk",
        )

    # Gate 6 — the XSD's OWN declared targetNamespace must not self-identify as synthetic
    # (MAJOR-2, GK REVISE). Proven live-forgeable before this repair: a forged manifest with
    # `schema_artifact.synthetic: false` ratifying the verbatim SYNTHETIC XSD (all other gates
    # forged too) loaded. Every synthetic fixture in this repo declares
    # `targetNamespace="urn:maezo:synthetic:..."` precisely so this check can catch exactly that
    # forgery: the artifact self-identifies, so check it instead of trusting the flag beside it.
    try:
        xsd_root = etree.fromstring(xsd_bytes)
    except etree.XMLSyntaxError as exc:
        raise _fail(
            REASON_ARTIFACT_MALFORMED, f"ratified XSD at {xsd_path} is not well-formed XML: {exc}"
        ) from exc
    target_namespace = xsd_root.get("targetNamespace") or ""
    if target_namespace.startswith(_SYNTHETIC_NAMESPACE_PREFIX):
        raise _fail(
            REASON_ARTIFACT_SELF_DECLARES_SYNTHETIC,
            f"{manifest_path}: ratified XSD at {xsd_path} declares targetNamespace "
            f"{target_namespace!r}, which self-identifies as a synthetic stand-in (starts with "
            f"{_SYNTHETIC_NAMESPACE_PREFIX!r}) — {_SCHEMA_ARTIFACT}.{_SYNTHETIC}=false does not "
            "override what the artifact itself declares",
        )

    padrao_tiss_versao = schema_artifact.get(_PADRAO_TISS_VERSAO)
    report_type = schema_artifact.get(_REPORT_TYPE)

    logger.info(
        "tiss_schema_pin_ratified_and_loaded",
        path=str(manifest_path),
        xsd_path=str(xsd_path),
        xsd_sha256=actual_digest,
        revisor=str(revisor),
        ratificado_em=str(ratificado_em),
    )
    return TissSchemaPin(
        xsd_path=xsd_path,
        sha256=actual_digest,
        padrao_tiss_versao=padrao_tiss_versao if isinstance(padrao_tiss_versao, str) else None,
        report_type=report_type if isinstance(report_type, str) else None,
        revisor=str(revisor),
        ratificado_em=str(ratificado_em),
    )


def load_tiss_schema_pin(path: str | Path | None = None) -> TissSchemaPin:
    """Load + ratification-gate-check the TISS-schema-pin manifest. FAILS CLOSED — always either
    returns a fully-ratified `TissSchemaPin` or raises `TissSchemaPinUnavailableError`; never a
    partial read, never a default-to-ratified.

    Path resolution: explicit `path` argument > `MAEZO_TISS_SCHEMA_PIN_MANIFEST_PATH` env var >
    `<spec>/policies/ans/tiss-schema-pin.yaml` (`resolve_spec_dir()`).

    Args:
        path: Explicit override (mainly for tests that don't want to go through the environment).

    Returns:
        A `TissSchemaPin` — ONLY when every ratification gate passes.

    Raises:
        TissSchemaPinUnavailableError: on every other outcome (see `REASON_*` constants).
    """
    raw_path = path if path is not None else os.environ.get(PIN_MANIFEST_PATH_ENV)
    if not raw_path:
        try:
            raw_path = _manifest_default_path()
        except Exception as exc:  # noqa: BLE001 - fail-closed: unresolvable spec/ ratifies nothing
            raise _fail(
                REASON_PATH_UNRESOLVED, f"could not resolve the default manifest path: {exc}"
            ) from exc

    manifest_path = Path(raw_path)
    if not manifest_path.is_file():
        raise _fail(REASON_FILE_NOT_FOUND, f"no readable manifest file at {manifest_path}")

    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # NOT covered by OSError (UnicodeDecodeError subclasses ValueError) — same explicit catch
        # legal_bases_matrix.py/auth_criteria.py document for non-UTF-8 files.
        raise _fail(REASON_INVALID_ENCODING, f"{manifest_path} is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise _fail(REASON_UNREADABLE, f"could not read {manifest_path}: {exc}") from exc

    try:
        data = yaml.load(raw_text, Loader=_DuplicateKeySafeLoader)
    except yaml.YAMLError as exc:
        # Catches both stock malformed YAML and `_DuplicateKeySafeLoader`'s duplicate-key raise
        # (a `yaml.constructor.ConstructorError` is a `yaml.YAMLError`).
        raise _fail(REASON_INVALID_YAML, f"malformed YAML in {manifest_path}: {exc}") from exc

    return _parse(data, manifest_path)


@lru_cache(maxsize=8)
def _compile_schema(xsd_path: str, digest: str) -> etree.XMLSchema | None:
    """Parse + compile the ratified XSD (cached by resolved path AND CONTENT DIGEST — MAJOR-1, GK
    REVISE: keying on `digest` too, not merely `xsd_path`, means an in-place XSD swap followed by
    a legitimate re-ratification (new bytes + a correctly re-derived sha256, same filename) always
    forces a fresh compile. Before this repair the cache was keyed on `xsd_path` alone, so a STALE
    compiled `XMLSchema` object from a prior ratification's bytes could silently persist across a
    re-ratification within the same long-lived process — the digest gate in `_parse` refuses a
    swap that ISN'T also re-ratified, but does not by itself stop this in-process cache from
    serving pre-swap compiled state for a swap that IS. `digest` does not otherwise participate in
    the compile (the file is still read fresh from `xsd_path`); it exists only to widen the key.

    `None` on any load failure — the caller (`TissSchemaPinValidator.validate`) turns that into a
    RAISE (`REASON_ARTIFACT_MALFORMED`), not a return, because a ratified-but-broken artifact is a
    governance/availability failure, not a payload outcome (module docstring, RAISE-vs-RETURN)."""
    del digest  # only widens the lru_cache key; the compile itself reads xsd_path fresh
    try:
        return etree.XMLSchema(etree.parse(xsd_path))
    except OSError as exc:
        logger.warning("tiss_schema_pin.xsd_unreadable", xsd_path=xsd_path, error=str(exc))
        return None
    except etree.XMLSyntaxError as exc:
        logger.warning("tiss_schema_pin.xsd_malformed", xsd_path=xsd_path, error=str(exc))
        return None
    except etree.XMLSchemaParseError as exc:
        logger.warning("tiss_schema_pin.xsd_schema_invalid", xsd_path=xsd_path, error=str(exc))
        return None


_QUOTED = re.compile(r"'([^']*)'")
#: libxml2's own "what the content model still expects here" clause — UNQUOTED, e.g.
#: `Expected is ( {urn:...}competenciaSintetica )` (occasionally a space-separated list of
#: alternatives). Always schema vocabulary (candidate next-element names), never a payload value.
_EXPECTED_CLAUSE = re.compile(r"Expected is \(\s*([^)]*?)\s*\)")


def _local_name(token: str) -> str:
    """Strip an XML Clark-notation `{namespace}` prefix, e.g. `{urn:...}elemento` -> `elemento`
    (mirrors `maezo.platform.validation._loaders.local_name`'s stripping, reimplemented locally
    to avoid an odd cross-package import from a CI-validation module into a worker seam)."""
    return token.rsplit("}", 1)[-1]


def _classify(entry: Any) -> str:
    """Classify a libxml2 error-log entry into one of the four violation classes this gate's
    fixtures exercise, using the STABLE `type_name` enum — never the free-text `message` — as the
    primary signal (empirically verified against this module's own synthetic schema; see
    `tests/unit/tools/workers/test_tiss_schema_pin.py`).

    `SCHEMAV_ELEMENT_CONTENT` covers BOTH "a required scalar element is entirely absent" and "a
    repeatable element occurs more times than its upper bound allows" — libxml2 does not assign
    these two cases distinct type codes. The message's `"Expected is ("` clause (present only when
    something the schema still expects is genuinely ABSENT at that point) is the tie-breaker —
    itself quoting only the SCHEMA's own element vocabulary, never a payload value.
    """
    type_name = getattr(entry, "type_name", None) or ""
    message = getattr(entry, "message", None) or ""
    if type_name == "SCHEMAV_CVC_DATATYPE_VALID_1_2_1":
        return "tipo_invalido"
    if type_name == "SCHEMAV_CVC_ELT_1":
        return "namespace_invalido"
    if type_name == "SCHEMAV_ELEMENT_CONTENT":
        return "elemento_ausente" if "Expected is (" in message else "cardinalidade"
    return "violacao_schema"


def _extract_element(entry: Any, kind: str) -> str | None:
    """Extract a BOUNDED, schema-vocabulary-only element name for a diagnostic.

    For `"elemento_ausente"` the USEFUL name is the MISSING element — which libxml2 reports
    inside the unquoted `"Expected is ( name )"` clause, NOT the first single-quoted token (for
    this specific error family, the first quoted token names the element that WAS present but
    out of place at that point in the sequence — e.g. (empirically observed, this module's own
    synthetic schema) `"Element '{urn:...}totalGuiasSintetico': This element is not expected.
    Expected is ( {urn:...}competenciaSintetica )."` — reporting `totalGuiasSintetico` as "the
    missing element" would be actively wrong). Every other violation class names the offending
    element as the first single-quoted token.
    """
    message = getattr(entry, "message", None) or ""
    if kind == "elemento_ausente":
        match = _EXPECTED_CLAUSE.search(message)
        if match:
            tokens = match.group(1).split()
            if tokens:
                return _local_name(tokens[0].rstrip(","))
    quoted = _QUOTED.findall(message)
    return _local_name(quoted[0]) if quoted else None


def _bound(entry: Any) -> TissSchemaPinDiagnostic:
    """Build a BOUNDED `TissSchemaPinDiagnostic` from a libxml2 error-log entry.

    PHI discipline — the raw `entry.message` is NEVER returned or stored: for a type-mismatch
    violation it embeds the actual offending VALUE from the payload (e.g. `"'PHI-CANARY...' is
    not a valid value of the atomic type 'xs:integer'"`). `_extract_element` reads only element
    NAMES — schema vocabulary the XSD itself declares, never a value out of the submitted
    document — and every other byte of `message` is dropped unconditionally. `entry.line` is a
    structural locator, not payload content.
    """
    kind = _classify(entry)
    element = _extract_element(entry, kind)
    return TissSchemaPinDiagnostic(kind=kind, element=element, line=getattr(entry, "line", None))


class TissSchemaPinValidator:
    """Validates a dataset's XML against the ratification-gated TISS-schema pin.

    Stateless besides the configured manifest-path override — safe to build once (mirrors
    `TissSchemaValidator`'s shape: a local, deterministic resolver, not a networked transport).

    `dataset_ref` is resolved as a LOCAL FILESYSTEM PATH to the assembled dataset's XML — the
    SAME convention `TissSchemaValidator.validate`'s `dataset_ref` uses (see that module's
    docstring for why: `dataset_ref` is a "ponteiro de storage" per the SP-OP-ANS-SUBMIT-001
    contract, and no real object-store fetch channel exists yet). Kept identical on purpose so a
    future consumer (`tiss_schema_pin_gate_entry` below) reads the exact same process variable
    either seam would.
    """

    def __init__(self, *, manifest_path: str | Path | None = None) -> None:
        self._manifest_path = manifest_path

    def validate(self, *, report_type: str, dataset_ref: str) -> TissSchemaPinValidationResult:
        """Validate `dataset_ref`'s XML against the ratified pin's XSD — but ONLY after confirming
        `report_type` matches the report_type the ratified pin itself declares
        (`schema_artifact.report_type`; MINOR-5, GK REVISE option 1).

        `TissSchemaPin` always carried `report_type`, but neither this method nor
        `tiss_schema_pin_gate_entry` used to read it: a direct future adoption would have silently
        dropped the report-type discrimination the ALREADY-WIRED sibling seam has —
        `tiss_schema.py`'s `TissSchemaValidator.validate` resolves a DIFFERENT XSD per
        `(version, report_type)` (`xsd_path = schema_root / version / f"{report_type}.xsd"`) — so
        validating a `report_type="internacao"` dataset against a pin ratified for
        `report_type="SP/SADT"` must not silently "pass" just because both happen to share one
        pinned XSD file. Mirrors that sibling's RETURN-not-RAISE posture for a report_type/schema
        mismatch (`TissSchemaValidator.validate` rule 2: "No vendored XSD for (version,
        report_type) -> schema_valid=False") — a report_type mismatch against this pin is an
        ORDINARY per-call outcome, not a governance/availability failure: the pin itself is
        perfectly available, just not applicable to THIS report_type.

        Raises `TissSchemaPinUnavailableError` if the pin is not ratified, or is ratified but its
        artifact is missing/malformed — the caller MUST treat this as UNAVAILABLE, never as a
        PASS (see `tiss_schema_pin_gate_entry` for the documented, not-yet-wired consumption
        contract). Once the pin IS available, every payload-level outcome (report_type mismatch,
        missing/malformed XML, schema-valid, schema-invalid-with-diagnostics) is a RETURNED
        result, never a raise.
        """
        pin = load_tiss_schema_pin(self._manifest_path)  # raises TissSchemaPinUnavailableError

        if report_type != pin.report_type:
            return TissSchemaPinValidationResult(
                schema_valid=False,
                diagnostics=(
                    TissSchemaPinDiagnostic(kind="report_type_incompativel_com_pin", element=None, line=None),
                ),
                schema_version=pin.padrao_tiss_versao,
            )

        schema = _compile_schema(str(pin.xsd_path), pin.sha256)
        if schema is None:
            raise _fail(REASON_ARTIFACT_MALFORMED, f"could not compile ratified XSD at {pin.xsd_path}")

        if not dataset_ref.strip():
            return TissSchemaPinValidationResult(
                schema_valid=False,
                diagnostics=(TissSchemaPinDiagnostic(kind="dataset_ref_ausente", element=None, line=None),),
                schema_version=pin.padrao_tiss_versao,
            )

        dataset_path = Path(dataset_ref)
        if not dataset_path.is_file():
            return TissSchemaPinValidationResult(
                schema_valid=False,
                diagnostics=(
                    TissSchemaPinDiagnostic(
                        kind="dataset_ref_nao_resolve_a_arquivo", element=None, line=None
                    ),
                ),
                schema_version=pin.padrao_tiss_versao,
            )

        try:
            xml_doc = etree.parse(str(dataset_path))
        except etree.XMLSyntaxError:
            return TissSchemaPinValidationResult(
                schema_valid=False,
                diagnostics=(TissSchemaPinDiagnostic(kind="xml_malformado", element=None, line=None),),
                schema_version=pin.padrao_tiss_versao,
            )

        if schema.validate(xml_doc):
            return TissSchemaPinValidationResult(
                schema_valid=True, diagnostics=(), schema_version=pin.padrao_tiss_versao
            )

        # `lxml-stubs`' `_ErrorLog` stub declares no members at all (not even `__iter__`), even
        # though it IS iterable-of-entries at runtime and each entry DOES carry `.message`/
        # `.type_name`/`.line` — the same gap `tiss_schema.py` documents (there worked around by
        # stringifying instead; here per-entry structure is required for `_bound`'s classification).
        diagnostics = tuple(_bound(entry) for entry in schema.error_log)  # type: ignore[attr-defined]
        return TissSchemaPinValidationResult(
            schema_valid=False, diagnostics=diagnostics, schema_version=pin.padrao_tiss_versao
        )


# ---------------------------------------------------------------------------------------------
# CONSUMPTION CONTRACT (demonstration only — NOT registered, NOT imported by ans_submit.py).
#
# See module docstring "SCOPING DECISION". This function is shaped exactly like a
# `register_ans_submit_workers` dict-boundary entry function (compare `ans_submit.validate_entry`)
# so a future, separately reviewed change could adopt it directly — but nothing in this repo calls
# it outside `tests/unit/tools/workers/test_tiss_schema_pin.py`. It is not passed to
# `harness.register_worker`, not threaded through `register_ans_submit_workers`'s `**seams`, and
# `ans_submit.py` does not import this module.
# ---------------------------------------------------------------------------------------------


def tiss_schema_pin_gate_entry(
    variables: dict[str, Any],
    *,
    pin_validator: TissSchemaPinValidator | None = None,
) -> dict[str, Any]:
    """DEMONSTRATION dict-boundary entry — traces how `ans_submit.py` WOULD consume this gate.

    Reads `dataset_ref` AND `report_type` off `variables` — the same fields
    `AnsSubmissionData.dataset_ref`/`AnsSubmissionData.report_type` carry (see
    `ans_submit.validate_entry`; `report_type` is threaded through per MINOR-5, GK REVISE —
    without it, this demonstration would silently drop the report-type discrimination
    `TissSchemaPinValidator.validate` now enforces). `pin_validator=None` (the default a real
    registration would pass in production) resolves a fresh `TissSchemaPinValidator()`; dev/test
    inject one pinned at a fixture manifest path, mirroring the `tiss_validator`/`ans_gateway`
    `**seams` convention already used by `register_ans_submit_workers`.

    **The UNAVAILABLE contract.** On `TissSchemaPinUnavailableError` (unratified pin, or any other
    refusal `load_tiss_schema_pin` raises) this function returns — it does NOT re-raise past this
    boundary, matching how an ordinary `FunctionWorker` dict-entry function is expected to
    complete the external task rather than fail it for an EXPECTED gate state — a result dict
    whose `tiss_schema_pin_available` is `False` and whose `tiss_schema_pin_schema_valid` is
    ALWAYS `False`. There is no code path in this function that can return
    `tiss_schema_pin_schema_valid=True` while `tiss_schema_pin_available=False`: unavailable can
    never read as PASS.

    **What is deliberately UNDECIDED here.** Whether/how `tiss_schema_pin_available`/
    `tiss_schema_pin_schema_valid`/`tiss_schema_pin_diagnostics` should fold into
    `ans_submit.validate_data`'s existing `schema_valid`/`errors` keys — AND-combine with the
    already-wired `TissSchemaValidator` outcome, replace it, or run informationally alongside it
    — is NOT decided by this function. That reconciliation is the future wiring change's job (see
    module docstring "SCOPING DECISION"); this function only proves the gate's own outcome can
    never be mistaken for a pass while unavailable.
    """
    validator = pin_validator if pin_validator is not None else TissSchemaPinValidator()
    dataset_ref = str(variables.get("dataset_ref") or "")
    report_type = str(variables.get("report_type") or "")
    try:
        result = validator.validate(report_type=report_type, dataset_ref=dataset_ref)
    except TissSchemaPinUnavailableError as exc:
        return {
            "tiss_schema_pin_available": False,
            "tiss_schema_pin_reason": exc.reason,
            "tiss_schema_pin_schema_valid": False,
            "tiss_schema_pin_diagnostics": [],
            "tiss_schema_pin_version": None,
        }
    return {
        "tiss_schema_pin_available": True,
        "tiss_schema_pin_reason": None,
        "tiss_schema_pin_schema_valid": result.schema_valid,
        "tiss_schema_pin_diagnostics": [d.bounded_message() for d in result.diagnostics],
        "tiss_schema_pin_version": result.schema_version,
    }
