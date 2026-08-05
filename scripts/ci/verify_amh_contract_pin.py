#!/usr/bin/env python3
"""CI gate: the AMH contract pin is intact, complete and non-regressing (MZO-010, ADR-0037 XRD-04).

Purpose
-------
ADR-0037 makes the AMH Data Platform the sole owner and editor of the canonical AMH x Maezo
contract (three Avro schemas, two OpenAPI documents, one contract manifest). Maezo NEVER holds an
editable copy of any of them ("Proibicoes imutaveis" #4); it holds exactly one artifact — the
immutable pin `config/integrations/amh/contracts.lock.json` — and the AMH-published, synthetic,
non-PHI test fixtures whose bytes that pin gates by digest.

XRD-04 states the failure mode this gate implements verbatim: "Digest divergente, schema ausente,
topico errado ou versao rebaixada falham fail-closed antes de merge/deploy de adapter." Nothing
else in the repo enforces that — the lock is plain JSON and the fixtures are plain files, so
without this gate a one-character edit to a digest, a silently dropped topic, a rogue extra
"fixture", or a lock update that quietly rolls the contract backwards would all merge green.

Design
------
Mirrors `scripts/ci/check_start_process_fence.py` and `scripts/ci/check_bpmn_error_allowlist.py`:
a pure, stdlib-only core (`verify_lock` / `verify_candidate` / `verify_manifest`) that returns
violations instead of exiting, plus a thin CLI wrapper (`main`). Stdlib only, per this package's
contract (`scripts/ci/__init__.py`) — the default mode therefore needs no third-party parser and,
crucially, no network: it verifies bytes that are already in the tree.

Fail-closed and NON-MASKING: every check runs even after an earlier one fails, so one run reports
the complete violation set rather than the first symptom. Exit 0 only when the violation list is
empty.

The frozen catalog (topic names, quarantine names, directions, artifact paths, the 28-field
envelope order, the closed `source_product` vocabulary) is hardcoded HERE, from ADR-0037's
"Catalogo canonico" and "Baseline congelado de campos do envelope comum". Pinning it in the gate
rather than reading it from the lock is the point: an edit to the lock that renames a topic or
reorders the envelope has to fight a second, independent copy of the truth.

Modes
-----
    python scripts/ci/verify_amh_contract_pin.py
        Default CI mode. Structure + format + frozen-catalog checks on the lock, plus the vendored
        fixture digest gate (recompute every fixture's sha256, refuse unlisted files). No network.

    python scripts/ci/verify_amh_contract_pin.py --candidate <path>
        Steward mode for a PROPOSED new lock (a future AMH publication). Runs the full structural
        and frozen-catalog checks on the candidate, then refuses regressions against the current
        lock: canonical_schema_version downgrade, topic major downgrade, topic removal/rename,
        artifact digest change without a canonical_schema_version increase, evidence/publication-run
        regression to an already-superseded value, and status != PUBLISHED. The vendored fixture
        digest gate is deliberately NOT applied to a candidate — a candidate legitimately carries
        the digests of fixtures that have not been re-vendored yet; the default mode gates those
        once the new bytes land.

    python scripts/ci/verify_amh_contract_pin.py --manifest <path>
        Steward mode for freshly fetched manifest BYTES. Recomputes the manifest sha256 and
        requires it to equal the pinned `manifest_pin.sha256`, then structurally re-reads the
        manifest text and requires its amh_commit_sha, evidence_id, Glue schema-version IDs and
        artifact/fixture digests to equal the pinned values. Fetching the bytes is the steward's
        job (`gh api` / `git show`); this script never touches the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The one pin file this repository is allowed to hold (ADR-0037 XRD-04).
DEFAULT_LOCK_PATH = "config/integrations/amh/contracts.lock.json"

#: Where the AMH-published fixture bytes are vendored. Digest-gated, never edited locally.
DEFAULT_VENDOR_DIR = "tests/contract/amh/fixtures"

#: The only file allowed in the vendor directory that the lock does not list.
VENDOR_DIR_EXEMPT_FILES: frozenset[str] = frozenset({"PROVENANCE.md"})

#: Top-level sections the lock must carry. A missing section is a hard failure — a pin that has
#: quietly lost its compatibility evidence or its XRG-3 record is not a pin.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "lock_format",
    "provenance",
    "manifest_pin",
    "compatibility_report",
    "glue_registration",
    "topics",
    "artifacts",
    "fixtures",
    "envelope",
    "xrg3_verification",
)

#: Dotted paths that must be present, non-empty, non-null strings.
REQUIRED_STRING_FIELDS: tuple[str, ...] = (
    "provenance.amh_repo",
    "provenance.amh_commit_sha",
    "provenance.amh_manifest_commit_sha",
    "provenance.contract_name",
    "provenance.canonical_schema_version",
    "provenance.compatibility_mode",
    "provenance.status",
    "provenance.evidence_id",
    "manifest_pin.path",
    "manifest_pin.sha256",
    "manifest_pin.git_blob_sha",
    "manifest_pin.prepublication_sha256",
    "compatibility_report.result",
    "compatibility_report.provider_contract_tests",
    "compatibility_report.dry_run_id",
    "compatibility_report.dry_run_run_id",
    "compatibility_report.evidence_artifact_id",
    "glue_registration.environment",
    "glue_registration.region",
    "glue_registration.registry_name",
    "glue_registration.publication_run_id",
    "glue_registration.published_at_utc",
    "glue_registration.schema_version_status",
    "envelope.canonical_schema_version",
    "envelope.payload_hash_canonicalization",
    "xrg3_verification.verified_at_utc",
    "xrg3_verification.verified_by",
)

#: Placeholder markers that must never survive into a published pin. A lock still carrying one of
#: these is an unfinished publication masquerading as a verified one. `SELF-AT-PUBLICATION` is the
#: token the AMH manifest legitimately uses for its OWN digest (a manifest cannot contain its own
#: hash) — legitimate THERE, never here, because the whole job of this file is to carry that digest.
PLACEHOLDER_SUBSTRINGS: tuple[str, ...] = (
    "SET-AT-PUBLICATION",
    "SELF-AT-PUBLICATION",
    "PLACEHOLDER",
    "CHANGEME",
)

#: Matched as a standalone word so that a legitimate value merely containing the letters cannot
#: trip the gate.
PLACEHOLDER_WORD_RE = re.compile(r"\bTBD\b", re.IGNORECASE)

#: ADR-0037 "Catalogo canonico": three topics, three quarantine topics, three Avro schemas.
#: (name, direction, schema_path, quarantine)
FROZEN_TOPICS: tuple[tuple[str, str, str, str], ...] = (
    (
        "amh.maezo.work-items.v1",
        "amh-to-maezo",
        "schemas/avro/integration/maezo/v1/amh_maezo_work_item.avsc",
        "amh.maezo.work-items.v1.quarantine.v1",
    ),
    (
        "amh.maezo.consent.v1",
        "amh-to-maezo",
        "schemas/avro/integration/maezo/v1/amh_maezo_consent.avsc",
        "amh.maezo.consent.v1.quarantine.v1",
    ),
    (
        "maezo.amh.outcomes.v1",
        "maezo-to-amh",
        "schemas/avro/integration/maezo/v1/maezo_amh_outcome.avsc",
        "maezo.amh.outcomes.v1.quarantine.v1",
    ),
)

#: The five schema artifacts Maezo pins by digest. The sixth catalog artifact — the manifest
#: itself — is pinned separately under `manifest_pin`, because its digest is computed OVER the
#: published manifest and therefore cannot live inside it.
FROZEN_ARTIFACT_PATHS: tuple[str, ...] = (
    "schemas/avro/integration/maezo/v1/amh_maezo_work_item.avsc",
    "schemas/avro/integration/maezo/v1/amh_maezo_consent.avsc",
    "schemas/avro/integration/maezo/v1/maezo_amh_outcome.avsc",
    "schemas/openapi/maezo/v1/subject-context.openapi.yaml",
    "schemas/openapi/maezo/v1/population-features.openapi.yaml",
)

#: Glue schema-version IDs are keyed by the Avro schema basename (no extension).
FROZEN_GLUE_SCHEMA_KEYS: tuple[str, ...] = (
    "amh_maezo_work_item",
    "amh_maezo_consent",
    "maezo_amh_outcome",
)

#: ADR-0037 "Baseline congelado de campos do envelope comum" — EXACT, ordered, 28 fields.
#: "Este ADR nao inventa nenhum campo de wire alem deste baseline."
FROZEN_ENVELOPE_FIELD_ORDER: tuple[str, ...] = (
    "event_id",
    "event_type",
    "canonical_schema_version",
    "occurred_at",
    "ingested_at",
    "source_vendor",
    "source_product",
    "source_instance",
    "source_tenant",
    "source_entity",
    "protected_source_record_ref",
    "source_position",
    "amh_tenant",
    "legal_entity",
    "portable_subject_ref",
    "amh_mpi_ref",
    "beneficiary_ref",
    "correlation_id",
    "causation_id",
    "idempotency_key",
    "consent_decision_ref",
    "purpose_of_use",
    "data_classification",
    "trace_id",
    "producer_version",
    "contract_manifest_digest",
    "payload_hash",
    "replay_count",
)

#: Closed by ADR ("Nenhum outro valor de source_product existe") and enforced as an Avro enum.
FROZEN_SOURCE_PRODUCT_VOCABULARY: tuple[str, ...] = ("tasy_hospital", "tasy_healthcare_plan")

FROZEN_STATUS = "PUBLISHED"
FROZEN_SCHEMA_VERSION_STATUS = "AVAILABLE"
FROZEN_CONTRACT_NAME = "amh-maezo-boundary"
FROZEN_COMPATIBILITY_MODE = "BACKWARD"
FROZEN_TOPIC_MAJOR = 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_RUN_ID_RE = re.compile(r"^[0-9]+$")
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
#: Trailing `.vN` major suffix on a topic (or quarantine topic) name.
_TOPIC_MAJOR_RE = re.compile(r"\.v(\d+)$")

_MISSING = object()


@dataclass(frozen=True)
class Violation:
    """One fail-closed reason the pin cannot be trusted."""

    code: str
    detail: str

    def render(self) -> str:
        return f"[{self.code}] {self.detail}"


@dataclass(frozen=True)
class GateResult:
    """Outcome of a verification mode. Pure data — never exits."""

    ok: bool
    mode: str
    violations: tuple[Violation, ...] = ()
    checks: tuple[str, ...] = field(default=())

    def render(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        lines = [f"[amh-contract-pin:{self.mode}] {status}"]
        for check in self.checks:
            lines.append(f"  . {check}")
        if self.violations:
            lines.append(f"  violations ({len(self.violations)}):")
            lines.extend(f"    - {v.render()}" for v in self.violations)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """sha256 hex of a file's bytes (streamed — fixtures are small, but this stays honest)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dig(obj: Any, dotted: str) -> Any:
    """Fetch a dotted path out of nested mappings, returning the `_MISSING` sentinel if absent."""
    current: Any = obj
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def parse_semver(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _SEMVER_RE.match(value)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def topic_major(name: Any) -> int | None:
    if not isinstance(name, str):
        return None
    match = _TOPIC_MAJOR_RE.search(name)
    return int(match.group(1)) if match else None


def iter_strings(node: Any, trail: str = "$") -> Iterable[tuple[str, str]]:
    """Yield `(json-path, value)` for every string anywhere in the structure."""
    if isinstance(node, str):
        yield (trail, node)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from iter_strings(value, f"{trail}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_strings(value, f"{trail}[{index}]")


def _digest_map(entries: Any) -> dict[str, Any]:
    """`[{path, sha256}, ...]` -> `{path: sha256}`, tolerating malformed entries."""
    out: dict[str, Any] = {}
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            out[entry["path"]] = entry.get("sha256")
    return out


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_lock(path: Path) -> tuple[dict[str, Any] | None, list[Violation]]:
    """Read + parse the lock. Returns `(lock_or_None, violations)`; never raises on bad input."""
    if not path.is_file():
        return None, [Violation("lock-missing", f"lock file not found: {path}")]
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - unreadable file on a healthy checkout
        return None, [Violation("lock-unreadable", f"{path}: {exc}")]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [Violation("lock-unparseable", f"{path}: not valid JSON ({exc})")]
    if not isinstance(parsed, dict):
        return None, [Violation("lock-not-object", f"{path}: top level must be a JSON object")]
    return parsed, []


# ---------------------------------------------------------------------------
# (a) structure + placeholders
# ---------------------------------------------------------------------------


def check_structure(lock: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []

    for section in REQUIRED_SECTIONS:
        if section not in lock:
            violations.append(Violation("section-missing", f"required section absent: {section!r}"))

    for dotted in REQUIRED_STRING_FIELDS:
        value = dig(lock, dotted)
        if value is _MISSING:
            violations.append(Violation("field-missing", f"required field absent: {dotted}"))
        elif value is None:
            violations.append(Violation("field-null", f"required field is null: {dotted}"))
        elif not isinstance(value, str):
            violations.append(
                Violation(
                    "field-type", f"required field must be a string: {dotted} (got {type(value).__name__})"
                )
            )
        elif not value.strip():
            violations.append(Violation("field-empty", f"required field is empty: {dotted}"))

    return violations


def check_placeholders(lock: dict[str, Any]) -> list[Violation]:
    """No unfinished-publication markers anywhere in the pin."""
    violations: list[Violation] = []
    for trail, value in iter_strings(lock):
        upper = value.upper()
        for token in PLACEHOLDER_SUBSTRINGS:
            if token in upper:
                violations.append(
                    Violation("placeholder", f"{trail}: contains placeholder token {token!r} -> {value!r}")
                )
        if PLACEHOLDER_WORD_RE.search(value):
            violations.append(
                Violation("placeholder", f"{trail}: contains placeholder token 'TBD' -> {value!r}")
            )
    return violations


# ---------------------------------------------------------------------------
# (b) format checks
# ---------------------------------------------------------------------------


def _check_pattern(
    violations: list[Violation], lock: dict[str, Any], dotted: str, pattern: re.Pattern[str], what: str
) -> None:
    value = dig(lock, dotted)
    if value is _MISSING or not isinstance(value, str):
        return  # already reported by check_structure
    if pattern.match(value) is None:
        violations.append(Violation("format", f"{dotted}: not a valid {what} -> {value!r}"))


def _check_equals(violations: list[Violation], lock: dict[str, Any], dotted: str, expected: str) -> None:
    value = dig(lock, dotted)
    if value is _MISSING:
        return
    if value != expected:
        violations.append(Violation("frozen-value", f"{dotted}: must be {expected!r}, got {value!r}"))


def check_formats(lock: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []

    _check_pattern(violations, lock, "provenance.amh_commit_sha", _GIT_SHA_RE, "40-hex git commit sha")
    _check_pattern(
        violations, lock, "provenance.amh_manifest_commit_sha", _GIT_SHA_RE, "40-hex git commit sha"
    )
    _check_pattern(violations, lock, "manifest_pin.git_blob_sha", _GIT_SHA_RE, "40-hex git blob sha")
    _check_pattern(violations, lock, "manifest_pin.sha256", _SHA256_RE, "64-hex sha256")
    _check_pattern(violations, lock, "manifest_pin.prepublication_sha256", _SHA256_RE, "64-hex sha256")
    _check_pattern(violations, lock, "compatibility_report.dry_run_run_id", _RUN_ID_RE, "numeric run id")
    _check_pattern(
        violations, lock, "compatibility_report.evidence_artifact_id", _RUN_ID_RE, "numeric artifact id"
    )
    _check_pattern(violations, lock, "glue_registration.publication_run_id", _RUN_ID_RE, "numeric run id")
    _check_pattern(
        violations, lock, "glue_registration.published_at_utc", _ISO_UTC_RE, "ISO-8601 UTC timestamp"
    )
    _check_pattern(
        violations, lock, "xrg3_verification.verified_at_utc", _ISO_UTC_RE, "ISO-8601 UTC timestamp"
    )

    _check_equals(violations, lock, "provenance.status", FROZEN_STATUS)
    _check_equals(violations, lock, "provenance.contract_name", FROZEN_CONTRACT_NAME)
    _check_equals(violations, lock, "provenance.compatibility_mode", FROZEN_COMPATIBILITY_MODE)
    _check_equals(violations, lock, "glue_registration.schema_version_status", FROZEN_SCHEMA_VERSION_STATUS)
    _check_equals(violations, lock, "compatibility_report.result", "PASSED")

    for dotted in ("provenance.canonical_schema_version", "envelope.canonical_schema_version"):
        value = dig(lock, dotted)
        if value is not _MISSING and parse_semver(value) is None:
            violations.append(Violation("format", f"{dotted}: not a MAJOR.MINOR.PATCH version -> {value!r}"))

    prov_version = dig(lock, "provenance.canonical_schema_version")
    env_version = dig(lock, "envelope.canonical_schema_version")
    if _MISSING not in (prov_version, env_version) and prov_version != env_version:
        violations.append(
            Violation(
                "version-mismatch",
                "provenance.canonical_schema_version != envelope.canonical_schema_version "
                f"({prov_version!r} vs {env_version!r})",
            )
        )

    manifest_digest = dig(lock, "manifest_pin.sha256")
    prepub_digest = dig(lock, "manifest_pin.prepublication_sha256")
    if isinstance(manifest_digest, str) and manifest_digest == prepub_digest:
        violations.append(
            Violation(
                "manifest-digest-degenerate",
                "manifest_pin.sha256 equals manifest_pin.prepublication_sha256 — the published "
                "manifest must differ from its pre-publication form (it gains the publication record)",
            )
        )

    byte_size = dig(lock, "manifest_pin.byte_size")
    if byte_size is _MISSING:
        violations.append(Violation("field-missing", "required field absent: manifest_pin.byte_size"))
    elif not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size <= 0:
        violations.append(
            Violation("format", f"manifest_pin.byte_size: must be a positive integer -> {byte_size!r}")
        )

    return violations


# ---------------------------------------------------------------------------
# (c) frozen catalog
# ---------------------------------------------------------------------------


def check_topics(lock: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    topics = lock.get("topics")
    if not isinstance(topics, list):
        return [Violation("topics-shape", "topics: must be a list")]

    if len(topics) != len(FROZEN_TOPICS):
        violations.append(
            Violation(
                "topic-count", f"topics: expected exactly {len(FROZEN_TOPICS)} topics, got {len(topics)}"
            )
        )

    by_name: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(topics):
        if not isinstance(entry, dict):
            violations.append(Violation("topics-shape", f"topics[{index}]: must be an object"))
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            violations.append(Violation("topic-name", f"topics[{index}]: missing/invalid name"))
            continue
        if name in by_name:
            violations.append(Violation("topic-duplicate", f"topics: duplicate topic name {name!r}"))
        by_name[name] = entry

    expected_names = {t[0] for t in FROZEN_TOPICS}
    for unexpected in sorted(set(by_name) - expected_names):
        violations.append(
            Violation("topic-unknown", f"topics: {unexpected!r} is not in the ADR-0037 frozen catalog")
        )

    for name, direction, schema_path, quarantine in FROZEN_TOPICS:
        entry = by_name.get(name)
        if entry is None:
            violations.append(Violation("topic-missing", f"topics: frozen topic {name!r} is absent"))
            continue
        if entry.get("direction") != direction:
            violations.append(
                Violation(
                    "topic-direction",
                    f"topics[{name}].direction: expected {direction!r}, got {entry.get('direction')!r}",
                )
            )
        if entry.get("schema_path") != schema_path:
            violations.append(
                Violation(
                    "topic-schema",
                    f"topics[{name}].schema_path: expected {schema_path!r}, got {entry.get('schema_path')!r}",
                )
            )
        if entry.get("quarantine") != quarantine:
            violations.append(
                Violation(
                    "topic-quarantine",
                    f"topics[{name}].quarantine: expected {quarantine!r}, got {entry.get('quarantine')!r}",
                )
            )
        if entry.get("major_version") != FROZEN_TOPIC_MAJOR:
            violations.append(
                Violation(
                    "topic-major",
                    f"topics[{name}].major_version: expected {FROZEN_TOPIC_MAJOR}, "
                    f"got {entry.get('major_version')!r}",
                )
            )
        for label in ("name", "quarantine"):
            value = entry.get(label)
            major = topic_major(value)
            if major is None:
                violations.append(
                    Violation("topic-major", f"topics[{name}].{label}: {value!r} has no trailing .vN major")
                )
            elif major != FROZEN_TOPIC_MAJOR:
                violations.append(
                    Violation(
                        "topic-major",
                        f"topics[{name}].{label}: major v{major}, expected v{FROZEN_TOPIC_MAJOR}",
                    )
                )

    return violations


def check_artifacts(lock: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    artifacts = lock.get("artifacts")
    if not isinstance(artifacts, list):
        return [Violation("artifacts-shape", "artifacts: must be a list")]

    digests = _digest_map(artifacts)
    if len(artifacts) != len(FROZEN_ARTIFACT_PATHS) or len(digests) != len(artifacts):
        violations.append(
            Violation(
                "artifact-count",
                f"artifacts: expected exactly {len(FROZEN_ARTIFACT_PATHS)} well-formed entries, "
                f"got {len(artifacts)} ({len(digests)} with a usable path)",
            )
        )

    for unexpected in sorted(set(digests) - set(FROZEN_ARTIFACT_PATHS)):
        violations.append(
            Violation("artifact-unknown", f"artifacts: {unexpected!r} is not in the ADR-0037 frozen catalog")
        )

    for path in FROZEN_ARTIFACT_PATHS:
        if path not in digests:
            violations.append(Violation("artifact-missing", f"artifacts: frozen artifact absent -> {path}"))
            continue
        digest = digests[path]
        if not isinstance(digest, str) or _SHA256_RE.match(digest) is None:
            violations.append(
                Violation("artifact-digest", f"artifacts[{path}].sha256: not a 64-hex sha256 -> {digest!r}")
            )

    return violations


def check_glue(lock: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    ids = dig(lock, "glue_registration.schema_version_ids")
    if not isinstance(ids, dict):
        return [Violation("glue-shape", "glue_registration.schema_version_ids: must be an object")]

    if len(ids) != len(FROZEN_GLUE_SCHEMA_KEYS):
        violations.append(
            Violation(
                "glue-count",
                f"glue_registration.schema_version_ids: expected exactly {len(FROZEN_GLUE_SCHEMA_KEYS)} "
                f"entries, got {len(ids)}",
            )
        )

    for unexpected in sorted(set(ids) - set(FROZEN_GLUE_SCHEMA_KEYS)):
        violations.append(
            Violation("glue-unknown", f"glue_registration.schema_version_ids: unknown key {unexpected!r}")
        )

    seen: dict[str, str] = {}
    for key in FROZEN_GLUE_SCHEMA_KEYS:
        if key not in ids:
            violations.append(
                Violation("glue-missing", f"glue_registration.schema_version_ids: missing key {key!r}")
            )
            continue
        value = ids[key]
        if not isinstance(value, str) or _UUID_RE.match(value) is None:
            violations.append(
                Violation(
                    "glue-format", f"glue_registration.schema_version_ids.{key}: not a UUID -> {value!r}"
                )
            )
            continue
        if value in seen:
            violations.append(
                Violation(
                    "glue-duplicate",
                    f"glue_registration.schema_version_ids: {key!r} reuses the id of {seen[value]!r}",
                )
            )
        seen[value] = key

    return violations


def check_envelope(lock: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    envelope = lock.get("envelope")
    if not isinstance(envelope, dict):
        return [Violation("envelope-shape", "envelope: must be an object")]

    order = envelope.get("field_order")
    if not isinstance(order, list) or not all(isinstance(f, str) for f in order):
        violations.append(Violation("envelope-order", "envelope.field_order: must be a list of strings"))
        order = None

    count = envelope.get("field_count")
    if not isinstance(count, int) or isinstance(count, bool):
        violations.append(
            Violation("envelope-count", f"envelope.field_count: must be an integer -> {count!r}")
        )
    elif count != len(FROZEN_ENVELOPE_FIELD_ORDER):
        violations.append(
            Violation(
                "envelope-count",
                f"envelope.field_count: must be {len(FROZEN_ENVELOPE_FIELD_ORDER)}, got {count}",
            )
        )
    elif order is not None and count != len(order):
        violations.append(
            Violation("envelope-count", f"envelope.field_count ({count}) != len(field_order) ({len(order)})")
        )

    if order is not None and tuple(order) != FROZEN_ENVELOPE_FIELD_ORDER:
        expected = list(FROZEN_ENVELOPE_FIELD_ORDER)
        detail = "envelope.field_order: not byte-exact to the ADR-0037 frozen baseline"
        for index in range(max(len(order), len(expected))):
            got = order[index] if index < len(order) else "<absent>"
            want = expected[index] if index < len(expected) else "<absent>"
            if got != want:
                detail += f"; first divergence at index {index}: expected {want!r}, got {got!r}"
                break
        violations.append(Violation("envelope-order", detail))

    vocabulary = envelope.get("source_product_vocabulary")
    if not isinstance(vocabulary, list) or tuple(vocabulary) != FROZEN_SOURCE_PRODUCT_VOCABULARY:
        violations.append(
            Violation(
                "envelope-vocabulary",
                "envelope.source_product_vocabulary: must be exactly "
                f"{list(FROZEN_SOURCE_PRODUCT_VOCABULARY)}, got {vocabulary!r}",
            )
        )

    return violations


# ---------------------------------------------------------------------------
# (d) vendored fixture digest gate
# ---------------------------------------------------------------------------


def check_fixture_entries(lock: dict[str, Any]) -> list[Violation]:
    """Shape checks on `fixtures[]` — run in every mode, including `--candidate`."""
    violations: list[Violation] = []
    fixtures = lock.get("fixtures")
    if not isinstance(fixtures, list):
        return [Violation("fixtures-shape", "fixtures: must be a list")]
    if not fixtures:
        return [
            Violation(
                "fixtures-empty", "fixtures: must not be empty — a pin with no test vectors proves nothing"
            )
        ]

    seen_vendored: set[str] = set()
    for index, entry in enumerate(fixtures):
        if not isinstance(entry, dict):
            violations.append(Violation("fixtures-shape", f"fixtures[{index}]: must be an object"))
            continue
        for key in ("path", "role", "sha256", "vendored_path"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                violations.append(
                    Violation("fixture-field", f"fixtures[{index}].{key}: missing/empty -> {value!r}")
                )
        digest = entry.get("sha256")
        if isinstance(digest, str) and _SHA256_RE.match(digest) is None:
            violations.append(
                Violation("fixture-digest", f"fixtures[{index}].sha256: not a 64-hex sha256 -> {digest!r}")
            )
        role = entry.get("role")
        if isinstance(role, str) and role not in {"valid", "invalid", "readme"}:
            violations.append(
                Violation(
                    "fixture-role", f"fixtures[{index}].role: must be valid|invalid|readme, got {role!r}"
                )
            )
        vendored = entry.get("vendored_path")
        if isinstance(vendored, str):
            if vendored in seen_vendored:
                violations.append(
                    Violation("fixture-duplicate", f"fixtures: duplicate vendored_path {vendored!r}")
                )
            seen_vendored.add(vendored)
            if ".." in Path(vendored).parts or Path(vendored).is_absolute():
                violations.append(
                    Violation(
                        "fixture-path",
                        f"fixtures[{index}].vendored_path: must be a repo-relative path -> {vendored!r}",
                    )
                )
    return violations


def check_vendored_fixtures(lock: dict[str, Any], repo_root: Path, vendor_dir: Path) -> list[Violation]:
    """Recompute every vendored fixture digest; refuse missing, tampered or unlisted files."""
    violations: list[Violation] = []
    fixtures = lock.get("fixtures")
    if not isinstance(fixtures, list):
        return [Violation("fixtures-shape", "fixtures: must be a list")]

    if not vendor_dir.is_dir():
        return [Violation("vendor-dir-missing", f"vendored fixture directory not found: {vendor_dir}")]

    expected_files: set[Path] = set()
    for index, entry in enumerate(fixtures):
        if not isinstance(entry, dict):
            continue
        vendored = entry.get("vendored_path")
        locked = entry.get("sha256")
        if not isinstance(vendored, str) or not isinstance(locked, str):
            continue  # shape already reported by check_fixture_entries
        target = repo_root / vendored
        expected_files.add(target.resolve())
        if not target.is_file():
            violations.append(
                Violation("fixture-missing", f"fixtures[{index}]: vendored file absent -> {vendored}")
            )
            continue
        actual = sha256_file(target)
        if actual != locked:
            violations.append(
                Violation(
                    "fixture-digest-mismatch",
                    f"{vendored}: sha256 {actual} != pinned {locked} — the vendored bytes were "
                    "edited locally; AMH-published fixtures are immutable (ADR-0037 XRD-04)",
                )
            )

    exempt = {(vendor_dir / name).resolve() for name in VENDOR_DIR_EXEMPT_FILES}
    for present in sorted(vendor_dir.rglob("*")):
        if not present.is_file():
            continue
        resolved = present.resolve()
        if resolved in expected_files or resolved in exempt:
            continue
        violations.append(
            Violation(
                "fixture-unlisted",
                f"{present.relative_to(repo_root)}: present in the vendored fixture directory but not "
                "listed in the lock — every byte here must be a digest-pinned AMH artifact",
            )
        )

    for name in sorted(VENDOR_DIR_EXEMPT_FILES):
        if not (vendor_dir / name).is_file():
            violations.append(
                Violation("provenance-missing", f"{vendor_dir}: required provenance file absent -> {name}")
            )

    return violations


# ---------------------------------------------------------------------------
# Composite: default mode
# ---------------------------------------------------------------------------


def verify_lock(
    lock: dict[str, Any],
    *,
    repo_root: Path,
    vendor_dir: Path | None = None,
    check_vendored: bool = True,
) -> list[Violation]:
    """Run every non-comparative check. Collects — an early failure never masks a later one."""
    violations: list[Violation] = []
    violations.extend(check_structure(lock))
    violations.extend(check_placeholders(lock))
    violations.extend(check_formats(lock))
    violations.extend(check_topics(lock))
    violations.extend(check_artifacts(lock))
    violations.extend(check_glue(lock))
    violations.extend(check_envelope(lock))
    violations.extend(check_fixture_entries(lock))
    if check_vendored:
        target = vendor_dir if vendor_dir is not None else repo_root / DEFAULT_VENDOR_DIR
        violations.extend(check_vendored_fixtures(lock, repo_root, target))
    return violations


# ---------------------------------------------------------------------------
# (e) downgrade / drift guard  (--candidate)
# ---------------------------------------------------------------------------


def verify_candidate(current: dict[str, Any], candidate: dict[str, Any]) -> list[Violation]:
    """Refuse a proposed lock that rolls the contract backwards. Comparative checks only."""
    violations: list[Violation] = []

    # -- status ------------------------------------------------------------
    status = dig(candidate, "provenance.status")
    if status != FROZEN_STATUS:
        violations.append(
            Violation(
                "candidate-status", f"candidate provenance.status must be {FROZEN_STATUS!r}, got {status!r}"
            )
        )

    # -- contract identity -------------------------------------------------
    cur_name = dig(current, "provenance.contract_name")
    new_name = dig(candidate, "provenance.contract_name")
    if cur_name != new_name:
        violations.append(
            Violation(
                "candidate-contract-name",
                f"contract_name changed {cur_name!r} -> {new_name!r}: that is a DIFFERENT contract, "
                "not an update",
            )
        )

    # -- canonical_schema_version ------------------------------------------
    cur_version = parse_semver(dig(current, "provenance.canonical_schema_version"))
    new_version = parse_semver(dig(candidate, "provenance.canonical_schema_version"))
    if new_version is None:
        violations.append(
            Violation(
                "candidate-version",
                f"candidate canonical_schema_version is not MAJOR.MINOR.PATCH -> "
                f"{dig(candidate, 'provenance.canonical_schema_version')!r}",
            )
        )
    elif cur_version is not None and new_version < cur_version:
        violations.append(
            Violation(
                "candidate-version-downgrade",
                f"canonical_schema_version downgrade {'.'.join(map(str, cur_version))} -> "
                f"{'.'.join(map(str, new_version))} — a pin never moves backwards (ADR-0037 XRD-04)",
            )
        )

    # -- topics: no removal, no rename, no major downgrade -----------------
    def topic_index(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
        entries = lock.get("topics")
        if not isinstance(entries, list):
            return {}
        return {e["name"]: e for e in entries if isinstance(e, dict) and isinstance(e.get("name"), str)}

    cur_topics = topic_index(current)
    new_topics = topic_index(candidate)
    for name, cur_entry in cur_topics.items():
        new_entry = new_topics.get(name)
        if new_entry is None:
            violations.append(
                Violation(
                    "candidate-topic-removed",
                    f"topic {name!r} present in the current pin is absent from the candidate "
                    "(removal or rename) — a consumer cannot silently lose a topic",
                )
            )
            continue
        cur_major = topic_major(name)
        new_major = new_entry.get("major_version")
        if (
            isinstance(new_major, int)
            and not isinstance(new_major, bool)
            and cur_major is not None
            and new_major < cur_major
        ):
            violations.append(
                Violation(
                    "candidate-topic-major-downgrade",
                    f"topic {name!r}: major_version downgrade v{cur_major} -> v{new_major}",
                )
            )
        cur_quarantine = cur_entry.get("quarantine")
        new_quarantine = new_entry.get("quarantine")
        if cur_quarantine != new_quarantine:
            violations.append(
                Violation(
                    "candidate-quarantine-renamed",
                    f"topic {name!r}: quarantine renamed {cur_quarantine!r} -> {new_quarantine!r}",
                )
            )

    # -- artifact + fixture digests may only move with a version bump ------
    cur_digests = _digest_map(current.get("artifacts"))
    cur_digests.update(_digest_map(current.get("fixtures")))
    new_digests = _digest_map(candidate.get("artifacts"))
    new_digests.update(_digest_map(candidate.get("fixtures")))
    changed_artifacts = sorted(
        path for path, digest in cur_digests.items() if path in new_digests and new_digests[path] != digest
    )
    version_increased = cur_version is not None and new_version is not None and new_version > cur_version
    if changed_artifacts and not version_increased:
        violations.append(
            Violation(
                "candidate-silent-artifact-change",
                f"artifact digest(s) changed without a canonical_schema_version increase: "
                f"{changed_artifacts} — new bytes are a new contract version, never a silent re-pin",
            )
        )

    # -- provenance must move with the bytes -------------------------------
    cur_commit = dig(current, "provenance.amh_commit_sha")
    new_commit = dig(candidate, "provenance.amh_commit_sha")
    if changed_artifacts and cur_commit == new_commit:
        violations.append(
            Violation(
                "candidate-commit-regression",
                f"artifact digest(s) changed but amh_commit_sha is unchanged ({new_commit!r}): the "
                "digests are the bytes AT that commit, so this pin is internally impossible",
            )
        )

    # -- evidence / publication run may never regress ----------------------
    cur_manifest = dig(current, "manifest_pin.sha256")
    new_manifest = dig(candidate, "manifest_pin.sha256")
    cur_evidence = dig(current, "provenance.evidence_id")
    new_evidence = dig(candidate, "provenance.evidence_id")
    if cur_manifest != new_manifest and cur_evidence == new_evidence:
        violations.append(
            Violation(
                "candidate-evidence-reuse",
                f"the manifest digest changed but evidence_id is unchanged ({new_evidence!r}): a new "
                "publication cannot reuse a spent XRG-2 evidence id",
            )
        )

    cur_run = dig(current, "glue_registration.publication_run_id")
    new_run = dig(candidate, "glue_registration.publication_run_id")
    if (
        isinstance(cur_run, str)
        and isinstance(new_run, str)
        and _RUN_ID_RE.match(cur_run)
        and _RUN_ID_RE.match(new_run)
    ):
        if int(new_run) < int(cur_run):
            violations.append(
                Violation(
                    "candidate-evidence-regression",
                    f"glue_registration.publication_run_id regressed {cur_run} -> {new_run}: that is an "
                    "already-superseded publication",
                )
            )
        elif int(new_run) == int(cur_run) and (cur_manifest != new_manifest or cur_commit != new_commit):
            violations.append(
                Violation(
                    "candidate-evidence-regression",
                    f"glue_registration.publication_run_id is unchanged ({new_run}) but the pinned "
                    "manifest/commit moved: one publication run cannot produce two different pins",
                )
            )

    return violations


# ---------------------------------------------------------------------------
# (f) manifest byte verification  (--manifest)
# ---------------------------------------------------------------------------


def _strip_yaml_scalar(raw: str) -> str:
    """Normalise a YAML scalar: drop a trailing `# comment` on unquoted values, unquote quoted ones."""
    value = raw.strip()
    if value[:1] in {'"', "'"}:
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value[1:]
    hash_at = value.find(" #")
    if hash_at != -1:
        value = value[:hash_at]
    return value.strip()


def scan_manifest_scalars(text: str, key: str) -> list[str]:
    """Every value of a `key: value` line in the manifest text (any indentation, non-empty value)."""
    pattern = re.compile(rf"^\s*{re.escape(key)}:[ \t]+(\S.*)$", re.MULTILINE)
    return [_strip_yaml_scalar(m.group(1)) for m in pattern.finditer(text)]


def scan_manifest_path_digests(text: str) -> dict[str, str]:
    """Pair each `- path: X` list item with the `sha256: Y` that follows it before the next item."""
    pairs: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        path_match = re.match(r"^\s*-\s+path:[ \t]+(\S.*)$", line)
        if path_match is not None:
            current = _strip_yaml_scalar(path_match.group(1))
            continue
        if current is None:
            continue
        if re.match(r"^\s*-\s+\S", line):  # a new list item without a `path:` key — pairing ends
            current = None
            continue
        sha_match = re.match(r"^\s*sha256:[ \t]+(\S.*)$", line)
        if sha_match is not None:
            pairs[current] = _strip_yaml_scalar(sha_match.group(1))
            current = None
    return pairs


def _expect_single(violations: list[Violation], text: str, key: str, expected: Any, label: str) -> None:
    found = scan_manifest_scalars(text, key)
    if not found:
        violations.append(Violation("manifest-key-missing", f"manifest: no `{key}:` line found ({label})"))
        return
    if len(set(found)) != 1:
        violations.append(
            Violation(
                "manifest-key-ambiguous",
                f"manifest: `{key}:` appears with conflicting values {sorted(set(found))}",
            )
        )
        return
    if found[0] != expected:
        violations.append(
            Violation(
                "manifest-mismatch", f"manifest `{key}` = {found[0]!r}, lock pins {expected!r} ({label})"
            )
        )


def verify_manifest(lock: dict[str, Any], manifest_path: Path) -> list[Violation]:
    """Verify freshly fetched manifest BYTES against the pin. Stdlib only; no network."""
    violations: list[Violation] = []

    if not manifest_path.is_file():
        return [Violation("manifest-missing", f"manifest file not found: {manifest_path}")]
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:  # pragma: no cover - unreadable file
        return [Violation("manifest-unreadable", f"{manifest_path}: {exc}")]

    actual_digest = hashlib.sha256(raw).hexdigest()
    pinned_digest = dig(lock, "manifest_pin.sha256")
    if actual_digest != pinned_digest:
        violations.append(
            Violation(
                "manifest-digest-mismatch",
                f"{manifest_path}: sha256 {actual_digest} != pinned {pinned_digest!r} — these are NOT "
                "the published manifest bytes",
            )
        )

    pinned_size = dig(lock, "manifest_pin.byte_size")
    if isinstance(pinned_size, int) and not isinstance(pinned_size, bool) and len(raw) != pinned_size:
        violations.append(
            Violation("manifest-size-mismatch", f"{manifest_path}: {len(raw)} bytes != pinned {pinned_size}")
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        violations.append(Violation("manifest-encoding", f"{manifest_path}: not valid UTF-8 ({exc})"))
        return violations

    # -- scalar identity ---------------------------------------------------
    _expect_single(
        violations, text, "amh_commit_sha", dig(lock, "provenance.amh_commit_sha"), "artifact source commit"
    )
    _expect_single(violations, text, "evidence_id", dig(lock, "provenance.evidence_id"), "XRG-2 evidence id")
    _expect_single(violations, text, "contract_name", dig(lock, "provenance.contract_name"), "contract name")
    _expect_single(
        violations,
        text,
        "compatibility_mode",
        dig(lock, "provenance.compatibility_mode"),
        "compatibility mode",
    )
    _expect_single(violations, text, "status", FROZEN_STATUS, "publication status")
    _expect_single(
        violations, text, "schema_version_status", FROZEN_SCHEMA_VERSION_STATUS, "Glue version status"
    )
    _expect_single(
        violations,
        text,
        "publication_run_id",
        dig(lock, "glue_registration.publication_run_id"),
        "Glue publication run",
    )
    _expect_single(
        violations,
        text,
        "published_at_utc",
        dig(lock, "glue_registration.published_at_utc"),
        "Glue publication time",
    )
    _expect_single(
        violations,
        text,
        "prepublication_manifest_sha256",
        dig(lock, "manifest_pin.prepublication_sha256"),
        "pre-publication manifest digest",
    )

    # -- Glue schema version ids ------------------------------------------
    glue_ids = dig(lock, "glue_registration.schema_version_ids")
    if isinstance(glue_ids, dict):
        for key in FROZEN_GLUE_SCHEMA_KEYS:
            _expect_single(violations, text, key, glue_ids.get(key), f"Glue schema version id for {key}")

    # -- artifact + fixture digests ---------------------------------------
    manifest_digests = scan_manifest_path_digests(text)
    manifest_self_path = dig(lock, "manifest_pin.path")

    pinned_pairs: dict[str, Any] = dict(_digest_map(lock.get("artifacts")))
    pinned_pairs.update(_digest_map(lock.get("fixtures")))

    for path, expected in sorted(pinned_pairs.items()):
        found = manifest_digests.get(path)
        if found is None:
            violations.append(
                Violation("manifest-artifact-missing", f"manifest: no digest entry for pinned path {path}")
            )
        elif found != expected:
            violations.append(
                Violation(
                    "manifest-artifact-mismatch", f"manifest {path}: sha256 {found!r}, lock pins {expected!r}"
                )
            )

    for path, found in sorted(manifest_digests.items()):
        if path in pinned_pairs:
            continue
        if path == manifest_self_path:
            # The manifest cannot carry its own digest; the published file marks that entry with a
            # self-reference token and the REAL digest is the one verified above, over these bytes.
            if _SHA256_RE.match(found):
                violations.append(
                    Violation(
                        "manifest-self-digest",
                        f"manifest declares a concrete sha256 for itself ({path}) — impossible; the "
                        "authoritative digest is the one computed over the published bytes",
                    )
                )
            continue
        violations.append(
            Violation(
                "manifest-artifact-unpinned",
                f"manifest declares digest-bearing path {path} that the lock does not pin — "
                "the pin is incomplete",
            )
        )

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_amh_contract_pin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "CI gate (MZO-010, ADR-0037 XRD-04): verify the immutable AMH contract pin. "
            "Fail-closed — exit 0 only when every check passes; every violation is reported in one run."
        ),
        epilog=(
            "modes\n"
            "  (default)          structure, format, frozen-catalog and vendored-fixture digest checks\n"
            "                     on config/integrations/amh/contracts.lock.json. No network.\n"
            "  --candidate PATH   compare a PROPOSED new lock against the current one and REFUSE:\n"
            "                     canonical_schema_version downgrade (semver), topic major downgrade,\n"
            "                     topic removal/rename, quarantine rename, artifact digest change\n"
            "                     without a canonical_schema_version increase, evidence_id reuse or\n"
            "                     publication-run regression to an already-superseded value, and\n"
            "                     status != PUBLISHED. The candidate also runs the full structural and\n"
            "                     frozen-catalog checks; the vendored-fixture digest gate is skipped,\n"
            "                     because a candidate legitimately precedes re-vendoring.\n"
            "  --manifest PATH    verify freshly fetched manifest BYTES: recompute sha256 against the\n"
            "                     pin, then require the manifest's amh_commit_sha, evidence_id, Glue\n"
            "                     schema-version ids and artifact/fixture digests to match the lock.\n"
            "                     Fetching is the steward's job; this script never uses the network.\n"
        ),
    )
    parser.add_argument(
        "--lock", default=DEFAULT_LOCK_PATH, help=f"Path to the pin (default: {DEFAULT_LOCK_PATH})."
    )
    parser.add_argument(
        "--candidate", default=None, help="Path to a proposed new lock to compare against --lock."
    )
    parser.add_argument(
        "--manifest", default=None, help="Path to freshly fetched contract-manifest.yaml bytes."
    )
    parser.add_argument(
        "--vendor-dir",
        default=DEFAULT_VENDOR_DIR,
        help=f"Directory holding the vendored fixture bytes (default: {DEFAULT_VENDOR_DIR}).",
    )
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repository root for relative paths.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns 0 (pass) or 1 (fail); never raises on ordinary input."""
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    lock_path = Path(args.lock)
    if not lock_path.is_absolute():
        lock_path = repo_root / lock_path
    vendor_dir = Path(args.vendor_dir)
    if not vendor_dir.is_absolute():
        vendor_dir = repo_root / vendor_dir

    def rel(path: Path) -> str:
        return str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else str(path)

    lock, violations = load_lock(lock_path)
    if lock is None:
        result = GateResult(ok=False, mode="lock", violations=tuple(violations))
        print(result.render(), file=sys.stderr)
        return 1

    if args.candidate is not None and args.manifest is not None:
        print(
            "[amh-contract-pin] FAIL: --candidate and --manifest are separate steward modes; "
            "run them separately.",
            file=sys.stderr,
        )
        return 2

    if args.candidate is not None:
        candidate_path = Path(args.candidate)
        if not candidate_path.is_absolute():
            candidate_path = repo_root / candidate_path
        candidate, load_violations = load_lock(candidate_path)
        if candidate is None:
            result = GateResult(ok=False, mode="candidate", violations=tuple(load_violations))
            print(result.render(), file=sys.stderr)
            return 1
        found = verify_lock(candidate, repo_root=repo_root, vendor_dir=vendor_dir, check_vendored=False)
        found.extend(verify_candidate(lock, candidate))
        result = GateResult(
            ok=not found,
            mode="candidate",
            violations=tuple(found),
            checks=(
                f"current pin: {rel(lock_path)}",
                f"candidate:   {rel(candidate_path)}",
                "structure + format + frozen catalog on the candidate; "
                "downgrade/drift guard vs the current pin",
            ),
        )
    elif args.manifest is not None:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_absolute():
            manifest_path = repo_root / manifest_path
        found = verify_manifest(lock, manifest_path)
        result = GateResult(
            ok=not found,
            mode="manifest",
            violations=tuple(found),
            checks=(f"manifest bytes: {rel(manifest_path)}", "sha256 + structural identity against the pin"),
        )
    else:
        found = verify_lock(lock, repo_root=repo_root, vendor_dir=vendor_dir)
        fixtures = lock.get("fixtures")
        artifacts = lock.get("artifacts")
        result = GateResult(
            ok=not found,
            mode="lock",
            violations=tuple(found),
            checks=(
                f"pin: {rel(lock_path)}",
                f"{len(FROZEN_TOPICS)} frozen topics + quarantine, "
                f"{len(artifacts) if isinstance(artifacts, list) else 0} artifacts, "
                f"{len(fixtures) if isinstance(fixtures, list) else 0} fixture digests recomputed",
                f"{len(FROZEN_ENVELOPE_FIELD_ORDER)}-field frozen envelope order + "
                "closed source_product vocabulary",
            ),
        )

    print(result.render(), file=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
