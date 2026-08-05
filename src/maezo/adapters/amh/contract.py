"""Runtime loader for the IMMUTABLE AMH contract pin — fail-closed (MZO-050a, ADR-0037 XRD-04).

**What this module is.** The typed, importable, production-side reader of
`config/integrations/amh/contracts.lock.json`. ADR-0037 XRD-04 makes the AMH Data Platform the sole
owner and editor of the canonical contract; this repository holds exactly ONE artifact about it —
that pin — and this module is how running code learns what was pinned (topics and their quarantine
siblings, the Glue registry coordinates and the three schema-version ids, the artifact/fixture
digests, the manifest digest, the 28-field envelope order, the closed `source_product` vocabulary,
the canonical schema version, the compatibility mode and the publication status).

**Fail-closed, and non-masking.** XRD-04 states the failure mode verbatim: "Digest divergente,
schema ausente, topico errado ou versao rebaixada falham fail-closed antes de merge/deploy de
adapter." Every check below therefore refuses rather than degrades, and — mirroring
`scripts/ci/verify_amh_contract_pin.py` — the checks COLLECT so one load reports the complete
violation set instead of the first symptom. There is no "load what you can" mode, no partial pin
and no default that papers over an absent file: an adapter that booted against an untrusted pin
would be deciding against a contract nobody verified.

**The exception must not cross a port boundary.** `AmhContractPinError` is an ADAPTER exception.
`maezo.ports.errors` is explicit that per-seam transport exceptions are legitimate in an adapter but
that "No adapter exception may cross a port boundary" — so an implementation of `WorkItemSource`,
`ConsentDecisionSource` or `OutcomePublisherPort` that calls into this module MUST catch
`AmhAdapterError` and return `PortResult.refused(PortFailureReason.CONTRACT_VIOLATION, ...)`. That
reason code is the right one by construction: a pin that fails these checks is deterministic —
retrying identical bytes changes nothing, so the caller must quarantine, which is exactly what
`CONTRACT_VIOLATION` tells it to do.

**Why the frozen catalogue is duplicated here instead of imported from the CI gate.**
`scripts/ci/verify_amh_contract_pin.py` holds the same frozen constants, and importing them would
look like the DRY choice. It is not available: `scripts/__init__.py` declares "Repo automation
scripts (not part of the maezo package)", `scripts/ci/__init__.py` declares "stdlib only — no
project dependencies", and the wheel ships only `src/maezo` (`pyproject.toml`
`[tool.hatch.build.targets.wheel]`), so `scripts/` is absent from a packaged deployment — an import
from `src/` would raise `ModuleNotFoundError` in the container while passing every local test. The
duplication is therefore deliberate, and it is the same defence the CI gate documents for itself:
"an edit to the lock that renames a topic or reorders the envelope has to fight a second,
independent copy of the truth". `tests/contract/amh/test_amh_contract_loader.py` asserts every
constant here equals the gate's, so the two copies cannot drift silently. Importing the script in a
TEST is fine (pytest sets `pythonpath = ["."]`), and `tests/contract/amh/test_contract_pin.py`
already does it.

**Packaging.** `config/` sits outside the wheel, so a container would have no pin at all. Solved the
way ADR-0025 D2 already solved it for the autonomy matrix: a hatch `force-include` lays
`config/integrations/amh/` down as `maezo/config/integrations/amh/` inside the wheel, and
`resolve_contract_pin_path` checks both the repo-checkout and the package-adjacent location, with
the `MAEZO_AMH_CONTRACT_PIN` env override taking precedence (same posture as `MAEZO_SPEC_DIR` in
`maezo.agents.resolve_spec_dir`: an explicit-but-wrong override is a configuration error, never a
silent fallback).

**No PHI here, but the echo is BOUNDED.** The pin carries digests, topic names and vocabulary — no
subject data of any kind — so violation text may quote pinned values for the operator. That licence
stops at this module: see `maezo.adapters.amh.mapping`, where the values are wire data and nothing may
be quoted. It is also bounded, because `$MAEZO_AMH_CONTRACT_PIN` can point at ANY readable JSON: every
pin-derived value and JSON path goes through `_echo`/`_bounded` (`MAX_ECHOED_PIN_VALUE_CHARS`), so a
misconfigured override cannot paste an arbitrary file's strings into a log line unbounded. Below the
bound the rendering is byte-identical to `repr`, so a correct pin's violations are unchanged.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

# ---------------------------------------------------------------------------
# Pin location
# ---------------------------------------------------------------------------

#: Environment variable that overrides the resolved pin FILE path. Named for the repo's
#: `MAEZO_`-prefixed convention (`MAEZO_SPEC_DIR`, `MAEZO_ANTHROPIC_API_KEY`). Authoritative when
#: set: pointing it at a missing file fails closed rather than falling back to a default, because a
#: deployment that explicitly declared where its pin lives and is wrong must not silently run
#: against a different pin.
MAEZO_AMH_CONTRACT_PIN_ENV: Final[str] = "MAEZO_AMH_CONTRACT_PIN"

#: Repo-relative location of the one pin file this repository is allowed to hold (XRD-04). Same
#: value as the CI gate's `DEFAULT_LOCK_PATH`; equality is asserted by test.
CONTRACT_PIN_RELATIVE_PATH: Final[str] = "config/integrations/amh/contracts.lock.json"

# ---------------------------------------------------------------------------
# Frozen catalogue — ADR-0037 "Catalogo canonico" + "Baseline congelado"
# (independent second copy of the CI gate's constants; see the module docstring)
# ---------------------------------------------------------------------------

#: `(name, direction, schema_path, quarantine)` — deliberately the CI gate's exact tuple shape so
#: the cross-check test is a direct equality rather than a re-spelling.
FROZEN_TOPICS: Final[tuple[tuple[str, str, str, str], ...]] = (
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

FROZEN_ARTIFACT_PATHS: Final[tuple[str, ...]] = (
    "schemas/avro/integration/maezo/v1/amh_maezo_work_item.avsc",
    "schemas/avro/integration/maezo/v1/amh_maezo_consent.avsc",
    "schemas/avro/integration/maezo/v1/maezo_amh_outcome.avsc",
    "schemas/openapi/maezo/v1/subject-context.openapi.yaml",
    "schemas/openapi/maezo/v1/population-features.openapi.yaml",
)

FROZEN_GLUE_SCHEMA_KEYS: Final[tuple[str, ...]] = (
    "amh_maezo_work_item",
    "amh_maezo_consent",
    "maezo_amh_outcome",
)

#: ADR-0037 "Baseline congelado de campos do envelope comum" — EXACT, ordered, 28 fields. A test
#: asserts this equals BOTH the CI gate's copy AND `maezo.ports.envelope.ENVELOPE_FIELD_ORDER`: the
#: pin, the gate and the ports must agree, or the boundary means three different things.
FROZEN_ENVELOPE_FIELD_ORDER: Final[tuple[str, ...]] = (
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

FROZEN_SOURCE_PRODUCT_VOCABULARY: Final[tuple[str, ...]] = ("tasy_hospital", "tasy_healthcare_plan")

FROZEN_STATUS: Final[str] = "PUBLISHED"
FROZEN_CONTRACT_NAME: Final[str] = "amh-maezo-boundary"
FROZEN_COMPATIBILITY_MODE: Final[str] = "BACKWARD"
FROZEN_TOPIC_MAJOR: Final[int] = 1

#: Glue's own verdict on the three published schema versions. The ONE frozen constant the CI gate
#: carried that this loader did not (`_check_equals(... "glue_registration.schema_version_status",
#: FROZEN_SCHEMA_VERSION_STATUS)`), which made the runtime loader accept a pin declaring `PENDING` or
#: `FAILURE` that the gate refuses. A schema version that is not `AVAILABLE` cannot be resolved by
#: phase B's decoder at all, so a pin claiming otherwise describes a registry state in which this
#: adapter cannot read a single event.
FROZEN_SCHEMA_VERSION_STATUS: Final[str] = "AVAILABLE"

#: The provider-side compatibility run's verdict. Same reasoning: the whole unknown-field tolerance in
#: `maezo.adapters.amh.mapping` (decision 3) rests on BACKWARD compatibility having been DEMONSTRATED,
#: not merely declared. A pin carrying `FAILED` here says the demonstration did not pass — the gate
#: refuses it (`_check_equals(... "compatibility_report.result", "PASSED")`) and so must the loader.
FROZEN_COMPATIBILITY_RESULT: Final[str] = "PASSED"

#: Evidence sections the pin must still carry. The gate's `REQUIRED_SECTIONS` says why in its own
#: words: "a pin that has quietly lost its compatibility evidence or its XRG-3 record is not a pin".
#: Deleting either section is a strictly EASIER edit than tampering with a value inside it, and before
#: this the loader read a pin with both sections deleted as valid.
REQUIRED_EVIDENCE_SECTIONS: Final[tuple[str, ...]] = ("compatibility_report", "xrg3_verification")

#: Longest pin-derived value echoed into a violation string. Violation text quotes pin content by
#: design (there is no PHI in a pin — see the module docstring), but `$MAEZO_AMH_CONTRACT_PIN` can
#: point at ANY readable JSON, and an unbounded echo would let a misconfigured override paste an
#: arbitrary file's strings into a log line. Comfortably above every legitimate pinned value (the
#: longest is a 64-hex digest), so a correct pin's violations read exactly as before.
MAX_ECHOED_PIN_VALUE_CHARS: Final[int] = 120

#: The canonical schema version this adapter was written against. A pin declaring a LOWER version is
#: the "versao rebaixada" XRD-04 refuses: the running code would be newer than the contract it
#: claims to serve. A higher version is accepted here (the pin moved forward); whether a given EVENT
#: is readable is a separate, per-event check in `maezo.adapters.amh.mapping`.
MINIMUM_CANONICAL_SCHEMA_VERSION: Final[tuple[int, int, int]] = (1, 0, 0)

#: Unfinished-publication markers that must never survive into a pin a running adapter trusts.
PLACEHOLDER_SUBSTRINGS: Final[tuple[str, ...]] = (
    "SET-AT-PUBLICATION",
    "SELF-AT-PUBLICATION",
    "PLACEHOLDER",
    "CHANGEME",
)
_PLACEHOLDER_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\bTBD\b", re.IGNORECASE)

_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
#: STRICT semver: ASCII digits only, and no leading zeros. `re.ASCII` matters — bare `\d` in a `str`
#: pattern also matches every Unicode decimal digit, so the previous spelling read fullwidth `１.０.０`
#: and Arabic-Indic `1.0.٠` as version 1.0.0. `01.0.0` is likewise refused: semver forbids leading
#: zeros, and accepting it would let two spellings of one version compare unequal as strings while
#: comparing equal as tuples — a divergence between the loader's downgrade check (tuple) and the
#: `provenance` vs `envelope` cross-check (string equality) in the SAME file.
_SEMVER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", re.ASCII
)
_TOPIC_MAJOR_RE: Final[re.Pattern[str]] = re.compile(r"\.v(\d+)$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AmhAdapterError(Exception):
    """Base class for every failure raised inside `maezo.adapters.amh`.

    **This type may NEVER cross a port boundary.** `maezo.ports.errors` permits a per-seam transport
    exception hierarchy inside an adapter, but requires the adapter to map it onto the closed
    `PortFailureReason` taxonomy before returning: an exception escaping a `WorkItemSource` /
    `ConsentDecisionSource` / `OutcomePublisherPort` method would be exactly the control flow a
    broad `except` can swallow, which the taxonomy exists to remove.
    """


class AmhContractPinError(AmhAdapterError):
    """The pinned contract is absent, unparseable, unpublished or not the frozen contract.

    Deterministic by nature — the bytes on disk will not become trustworthy on a retry — so the
    port-boundary mapping is `PortFailureReason.CONTRACT_VIOLATION`, never
    `UPSTREAM_UNAVAILABLE`/`TIMEOUT`.

    Carries the COMPLETE violation set (`.violations`), not the first symptom, so one boot reports
    everything wrong with the pin.
    """

    def __init__(self, violations: Sequence[str]) -> None:
        self.violations: tuple[str, ...] = tuple(violations)
        joined = "; ".join(self.violations) if self.violations else "unspecified"
        super().__init__(f"AMH contract pin refused ({len(self.violations)} violation(s)): {joined}")


# ---------------------------------------------------------------------------
# Typed, frozen pin values
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class TopicPin:
    """One pinned topic and its quarantine sibling.

    `quarantine` is carried next to `name` on purpose: `WorkItemSource.nack` is contractually
    obliged to route a non-absorbable delivery to "the source's quarantine", so an adapter that
    knew a topic without knowing its quarantine could not honour the port at all.
    """

    name: str
    direction: str
    schema_path: str
    quarantine: str
    major_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class GlueRegistryPin:
    """Glue Schema Registry coordinates and the three pinned schema-version ids.

    Coordinates only — no client, no credential, no `boto3`. Phase B's decoder needs the ids to
    resolve a writer schema; this module's job is to state which ids were pinned.
    """

    environment: str
    region: str
    registry_name: str
    schema_version_status: str
    schema_version_ids: Mapping[str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class AmhContractPin:
    """The verified pin, as frozen typed values. Constructed ONLY by `load_contract_pin`.

    Every mapping is a read-only view (`MappingProxyType`), so a caller cannot mutate the pinned
    truth in place and change what a later validation compares against.
    """

    contract_name: str
    canonical_schema_version: str
    compatibility_mode: str
    status: str
    amh_commit_sha: str
    evidence_id: str
    manifest_digest: str
    manifest_path: str
    envelope_field_order: tuple[str, ...]
    envelope_field_count: int
    source_product_vocabulary: tuple[str, ...]
    payload_hash_canonicalization: str
    topics: tuple[TopicPin, ...]
    glue: GlueRegistryPin
    artifact_digests: Mapping[str, str]
    fixture_digests: Mapping[str, str]
    source_path: Path

    @property
    def canonical_schema_major(self) -> int:
        """MAJOR component of the pinned canonical schema version.

        The per-event compatibility rule in `maezo.adapters.amh.mapping` is expressed against this:
        the contract owner guarantees compatibility WITHIN a major (every topic is `.v1` and the pin
        declares `major_version: 1`), and guarantees nothing across one.
        """
        parsed = parse_semver(self.canonical_schema_version)
        if parsed is None:  # pragma: no cover - load_contract_pin refuses a non-semver version
            raise AmhContractPinError(
                [f"canonical_schema_version is not semver: {_echo(self.canonical_schema_version)}"]
            )
        return parsed[0]

    def topic(self, name: str) -> TopicPin:
        """The pinned topic called `name`. Fail-closed: an unpinned topic name is a violation, not
        a `None` a caller might treat as "no routing needed"."""
        for entry in self.topics:
            if entry.name == name:
                return entry
        raise AmhContractPinError([f"topic {_echo(name)} is not in the pinned catalogue"])

    def glue_schema_version_id(self, schema_key: str) -> str:
        """The pinned Glue schema-version id for `schema_key` (e.g. `amh_maezo_work_item`)."""
        try:
            return self.glue.schema_version_ids[schema_key]
        except KeyError:
            raise AmhContractPinError([f"no pinned Glue schema-version id for {_echo(schema_key)}"]) from None

    def is_known_source_product(self, value: object) -> bool:
        """Membership in the CLOSED `source_product` vocabulary ("Nenhum outro valor de
        `source_product` existe" — ADR-042:186 via the pin)."""
        return isinstance(value, str) and value in self.source_product_vocabulary


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def parse_semver(value: object) -> tuple[int, int, int] | None:
    """`MAJOR.MINOR.PATCH` -> the integer triple, or `None` when `value` is not a strict semver.

    **The ONE version validator in `maezo.adapters.amh`.** `maezo.adapters.amh.mapping` imports this
    rather than keeping its own: two validators for one grammar drifted apart on four inputs
    (`01.0.0`, `１.０.０`, `1.0.٠` accepted by both when they should be refused, and `².0.0` raising a
    bare `ValueError` out of the mapping copy because `"²".isdigit()` is `True` while `int("²")` is
    not). Total by construction — it never raises, so a caller cannot leak an exception by using it.
    """
    if not isinstance(value, str):
        return None
    match = _SEMVER_RE.match(value)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _echo(value: object, limit: int = MAX_ECHOED_PIN_VALUE_CHARS) -> str:
    """`repr(value)` for a violation message, bounded to `limit` characters.

    Below the bound the output is EXACTLY `repr(value)`, so every legitimate violation reads as it did
    before; above it the tail is replaced by an explicit elision marker naming the true length, which
    is what an operator needs in order to know the value was long rather than mangled.
    """
    return _bounded(repr(value), limit)


def _bounded(text: str, limit: int = MAX_ECHOED_PIN_VALUE_CHARS) -> str:
    """`text` truncated to `limit` characters with an explicit elision marker naming the true length.

    Applied to the JSON PATHS the placeholder walk reports as well as to values: a path is assembled
    from the scanned file's own keys, so `$.<64KB key>` is just as unbounded as a 64KB value.
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<{len(text)} chars, elided>"


def _topic_major(name: object) -> int | None:
    if not isinstance(name, str):
        return None
    match = _TOPIC_MAJOR_RE.search(name)
    return int(match.group(1)) if match else None


def _iter_strings(node: object, trail: str = "$") -> Iterator[tuple[str, str]]:
    """Yield `(json-path, value)` for every string anywhere in the parsed pin."""
    if isinstance(node, str):
        yield (trail, node)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_strings(value, f"{trail}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_strings(value, f"{trail}[{index}]")


def _get(obj: object, dotted: str) -> object | None:
    """Fetch a dotted path, or `None` when any segment is absent/not a mapping."""
    current: object = obj
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _require_str(violations: list[str], root: object, dotted: str) -> str:
    value = _get(root, dotted)
    if not isinstance(value, str) or not value.strip():
        violations.append(f"{dotted}: required non-empty string, got {_echo(value)}")
        return ""
    return value


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _default_pin_path_candidates() -> tuple[Path, ...]:
    """Ordered default locations for the pin FILE.

    1. **Repo checkout** — `<repo_root>/config/integrations/amh/contracts.lock.json`. This module
       lives at `<repo_root>/src/maezo/adapters/amh/contract.py`, so `<repo_root>` is four parents
       up. The dev/CI case.
    2. **Installed wheel** — `<site-packages>/maezo/config/integrations/amh/contracts.lock.json`,
       laid down by the hatch `force-include` in `pyproject.toml` (the same mechanism ADR-0025 D2
       uses to ship the autonomy matrix). Without this candidate the shipped pin would be inert and
       every packaged deployment would fail closed at boot with no pin at all.

    A layout too shallow for a candidate omits it rather than raising `IndexError` — the same
    defensive shape as `maezo.agents._default_spec_dir_candidates`.
    """
    parents = Path(__file__).resolve().parents
    candidates: list[Path] = []
    if len(parents) > 4:
        candidates.append(parents[4] / CONTRACT_PIN_RELATIVE_PATH)
    if len(parents) > 2:
        candidates.append(parents[2] / CONTRACT_PIN_RELATIVE_PATH)
    return tuple(candidates)


def resolve_contract_pin_path() -> Path:
    """Resolve the pin file, honouring `MAEZO_AMH_CONTRACT_PIN`.

    Fail-closed in both directions: an env override pointing at a missing file raises immediately
    (no fallback — an explicit-but-wrong override is a configuration error), and an unresolvable
    default raises rather than returning a path that does not exist. There is deliberately no
    "return the path anyway and let the open fail" behaviour: the error message here can name every
    candidate it tried, which is what an operator debugging a container needs.

    Raises:
        AmhContractPinError: the override is missing, or no default candidate exists.
    """
    override = os.environ.get(MAEZO_AMH_CONTRACT_PIN_ENV)
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise AmhContractPinError(
                [
                    f"pin file not found at {path} (resolved from ${MAEZO_AMH_CONTRACT_PIN_ENV}); "
                    "an explicit override must point at the pin JSON"
                ]
            )
        return path

    candidates = _default_pin_path_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise AmhContractPinError(
        [
            "pin file not found at any default candidate: "
            + ", ".join(str(c) for c in candidates)
            + f" — set ${MAEZO_AMH_CONTRACT_PIN_ENV} to the pin JSON "
            f"({CONTRACT_PIN_RELATIVE_PATH})"
        ]
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _check_provenance(violations: list[str], raw: object) -> None:
    status = _require_str(violations, raw, "provenance.status")
    if status and status != FROZEN_STATUS:
        violations.append(
            f"provenance.status: must be {FROZEN_STATUS!r}, got {_echo(status)} — an adapter never runs "
            "against an unpublished contract"
        )

    name = _require_str(violations, raw, "provenance.contract_name")
    if name and name != FROZEN_CONTRACT_NAME:
        violations.append(f"provenance.contract_name: must be {FROZEN_CONTRACT_NAME!r}, got {_echo(name)}")

    mode = _require_str(violations, raw, "provenance.compatibility_mode")
    if mode and mode != FROZEN_COMPATIBILITY_MODE:
        violations.append(
            f"provenance.compatibility_mode: must be {FROZEN_COMPATIBILITY_MODE!r}, got {_echo(mode)}"
        )

    _require_str(violations, raw, "provenance.amh_commit_sha")
    _require_str(violations, raw, "provenance.evidence_id")

    prov_version = _get(raw, "provenance.canonical_schema_version")
    env_version = _get(raw, "envelope.canonical_schema_version")
    parsed = parse_semver(prov_version)
    if parsed is None:
        violations.append(
            f"provenance.canonical_schema_version: must be MAJOR.MINOR.PATCH, got {_echo(prov_version)}"
        )
    elif parsed < MINIMUM_CANONICAL_SCHEMA_VERSION:
        violations.append(
            "provenance.canonical_schema_version: downgrade "
            f"{'.'.join(map(str, parsed))} < {'.'.join(map(str, MINIMUM_CANONICAL_SCHEMA_VERSION))} — "
            "a pin never moves backwards (ADR-0037 XRD-04)"
        )
    if prov_version != env_version:
        violations.append(
            "provenance.canonical_schema_version != envelope.canonical_schema_version "
            f"({_echo(prov_version)} vs {_echo(env_version)})"
        )


def _check_evidence(violations: list[str], raw: object) -> None:
    """The pin's published EVIDENCE, not just its declarations (LOW-3 / gate parity).

    Everything else in this file checks what the pin CLAIMS about the contract. These three checks are
    about whether the claim was demonstrated: the compatibility run passed, and the XRG-3 verification
    record is still attached. Both sections are trivially deletable from a JSON file, and without these
    checks the loader read a pin with either one removed — or with `result: FAILED` — as valid, while
    `scripts/ci/verify_amh_contract_pin.py` refuses all three. A runtime loader that is a strict SUBSET
    of the CI gate is a loader that will one day boot against a pin CI would have rejected.
    """
    for section in REQUIRED_EVIDENCE_SECTIONS:
        if not isinstance(_get(raw, section), dict):
            violations.append(
                f"{section}: required evidence section absent or not an object — a pin that has lost "
                "its compatibility evidence or its XRG-3 record is not a pin"
            )

    result = _get(raw, "compatibility_report.result")
    if result is not None and result != FROZEN_COMPATIBILITY_RESULT:
        violations.append(
            f"compatibility_report.result: must be {FROZEN_COMPATIBILITY_RESULT!r}, got {_echo(result)} — "
            "the BACKWARD tolerance in maezo.adapters.amh.mapping rests on compatibility having been "
            "demonstrated, not merely declared"
        )

    # The XRG-3 record must be more than an empty object: the gate requires both of these strings.
    if isinstance(_get(raw, "xrg3_verification"), dict):
        _require_str(violations, raw, "xrg3_verification.verified_at_utc")
        _require_str(violations, raw, "xrg3_verification.verified_by")


def _check_placeholders(violations: list[str], raw: object) -> None:
    for raw_trail, value in _iter_strings(raw):
        trail = _bounded(raw_trail)
        upper = value.upper()
        for token in PLACEHOLDER_SUBSTRINGS:
            if token in upper:
                violations.append(f"{trail}: unfinished-publication placeholder {token!r}")
        if _PLACEHOLDER_WORD_RE.search(value):
            violations.append(f"{trail}: unfinished-publication placeholder 'TBD'")


def _check_manifest(violations: list[str], raw: object) -> None:
    digest = _require_str(violations, raw, "manifest_pin.sha256")
    if digest and _SHA256_RE.match(digest) is None:
        violations.append(f"manifest_pin.sha256: not a 64-hex sha256, got {_echo(digest)}")
    _require_str(violations, raw, "manifest_pin.path")


def _check_envelope(violations: list[str], raw: object) -> None:
    order = _get(raw, "envelope.field_order")
    if not isinstance(order, list) or not all(isinstance(f, str) for f in order):
        violations.append("envelope.field_order: must be a list of strings")
    elif tuple(order) != FROZEN_ENVELOPE_FIELD_ORDER:
        detail = "envelope.field_order: not byte-exact to the ADR-0037 frozen 28-field baseline"
        expected = list(FROZEN_ENVELOPE_FIELD_ORDER)
        for index in range(max(len(order), len(expected))):
            got = order[index] if index < len(order) else "<absent>"
            want = expected[index] if index < len(expected) else "<absent>"
            if got != want:
                detail += f"; first divergence at index {index}: expected {want!r}, got {_echo(got)}"
                break
        violations.append(detail)

    count = _get(raw, "envelope.field_count")
    if not isinstance(count, int) or isinstance(count, bool):
        violations.append(f"envelope.field_count: must be an integer, got {_echo(count)}")
    elif count != len(FROZEN_ENVELOPE_FIELD_ORDER):
        violations.append(f"envelope.field_count: must be {len(FROZEN_ENVELOPE_FIELD_ORDER)}, got {count}")

    vocabulary = _get(raw, "envelope.source_product_vocabulary")
    if not isinstance(vocabulary, list) or tuple(vocabulary) != FROZEN_SOURCE_PRODUCT_VOCABULARY:
        violations.append(
            "envelope.source_product_vocabulary: must be exactly "
            f"{list(FROZEN_SOURCE_PRODUCT_VOCABULARY)}, got {_echo(vocabulary)}"
        )

    canonicalization = _require_str(violations, raw, "envelope.payload_hash_canonicalization")
    if canonicalization and "sorted keys" not in canonicalization:
        violations.append(
            "envelope.payload_hash_canonicalization: the pinned canonicalisation no longer declares "
            f"sorted keys ({_echo(canonicalization)}) — the recomputation in maezo.adapters.amh.mapping "
            "would no longer reproduce a producer's hash"
        )


def _check_topics(violations: list[str], raw: object) -> list[TopicPin]:
    entries = _get(raw, "topics")
    if not isinstance(entries, list):
        violations.append("topics: must be a list")
        return []

    by_name: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            violations.append(f"topics[{index}]: must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            violations.append(f"topics[{index}]: missing/invalid name")
            continue
        if name in by_name:
            violations.append(f"topics: duplicate topic name {_echo(name)}")
        by_name[name] = entry

    expected_names = {t[0] for t in FROZEN_TOPICS}
    for unexpected in sorted(set(by_name) - expected_names):
        violations.append(f"topics: {_echo(unexpected)} is not in the ADR-0037 frozen catalogue")

    pins: list[TopicPin] = []
    for name, direction, schema_path, quarantine in FROZEN_TOPICS:
        entry = by_name.get(name)
        if entry is None:
            violations.append(f"topics: frozen topic {name!r} is absent")
            continue
        if entry.get("direction") != direction:
            violations.append(
                f"topics[{name}].direction: expected {direction!r}, got {_echo(entry.get('direction'))}"
            )
        if entry.get("schema_path") != schema_path:
            violations.append(
                f"topics[{name}].schema_path: expected {schema_path!r}, got {_echo(entry.get('schema_path'))}"
            )
        if entry.get("quarantine") != quarantine:
            violations.append(
                f"topics[{name}].quarantine: expected {quarantine!r}, got {_echo(entry.get('quarantine'))}"
            )
        major = entry.get("major_version")
        if major != FROZEN_TOPIC_MAJOR:
            violations.append(
                f"topics[{name}].major_version: expected {FROZEN_TOPIC_MAJOR}, got {_echo(major)}"
            )
        for label in ("name", "quarantine"):
            suffix_major = _topic_major(entry.get(label))
            if suffix_major is None:
                violations.append(
                    f"topics[{name}].{label}: {_echo(entry.get(label))} has no trailing .vN major"
                )
            elif suffix_major != FROZEN_TOPIC_MAJOR:
                violations.append(
                    f"topics[{name}].{label}: major v{suffix_major}, expected v{FROZEN_TOPIC_MAJOR}"
                )
        # `major_version` is the FROZEN value, not `entry`'s: any divergence was just recorded as a
        # violation, and a violation aborts the load — so this pin object never escapes carrying a
        # major the pin file disagreed with.
        pins.append(
            TopicPin(
                name=name,
                direction=direction,
                schema_path=schema_path,
                quarantine=quarantine,
                major_version=FROZEN_TOPIC_MAJOR,
            )
        )
    return pins


def _check_glue(violations: list[str], raw: object) -> GlueRegistryPin:
    environment = _require_str(violations, raw, "glue_registration.environment")
    region = _require_str(violations, raw, "glue_registration.region")
    registry = _require_str(violations, raw, "glue_registration.registry_name")
    status = _require_str(violations, raw, "glue_registration.schema_version_status")
    if status and status != FROZEN_SCHEMA_VERSION_STATUS:
        violations.append(
            f"glue_registration.schema_version_status: must be {FROZEN_SCHEMA_VERSION_STATUS!r}, got "
            f"{_echo(status)} — phase B's decoder cannot resolve a writer schema whose registry "
            "version is not available"
        )

    ids_raw = _get(raw, "glue_registration.schema_version_ids")
    ids: dict[str, str] = {}
    if not isinstance(ids_raw, dict):
        violations.append("glue_registration.schema_version_ids: must be an object")
    else:
        for unexpected in sorted(set(ids_raw) - set(FROZEN_GLUE_SCHEMA_KEYS)):
            violations.append(f"glue_registration.schema_version_ids: unknown key {_echo(unexpected)}")
        seen: dict[str, str] = {}
        for key in FROZEN_GLUE_SCHEMA_KEYS:
            value = ids_raw.get(key)
            if value is None:
                violations.append(f"glue_registration.schema_version_ids: missing key {key!r}")
                continue
            if not isinstance(value, str) or _UUID_RE.match(value) is None:
                violations.append(
                    f"glue_registration.schema_version_ids.{key}: not a UUID, got {_echo(value)}"
                )
                continue
            if value in seen:
                violations.append(
                    f"glue_registration.schema_version_ids: {key!r} reuses the id of {seen[value]!r}"
                )
            seen[value] = key
            ids[key] = value

    return GlueRegistryPin(
        environment=environment,
        region=region,
        registry_name=registry,
        schema_version_status=status,
        schema_version_ids=MappingProxyType(ids),
    )


def _digest_map(violations: list[str], raw: object, section: str, *, key: str) -> dict[str, str]:
    entries = _get(raw, section)
    out: dict[str, str] = {}
    if not isinstance(entries, list):
        violations.append(f"{section}: must be a list")
        return out
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            violations.append(f"{section}[{index}]: must be an object")
            continue
        path = entry.get(key)
        digest = entry.get("sha256")
        if not isinstance(path, str) or not path:
            violations.append(f"{section}[{index}].{key}: missing/invalid")
            continue
        if not isinstance(digest, str) or _SHA256_RE.match(digest) is None:
            violations.append(f"{section}[{index}].sha256: not a 64-hex sha256, got {_echo(digest)}")
            continue
        out[path] = digest
    return out


def _check_artifacts(violations: list[str], raw: object) -> dict[str, str]:
    digests = _digest_map(violations, raw, "artifacts", key="path")
    for unexpected in sorted(set(digests) - set(FROZEN_ARTIFACT_PATHS)):
        violations.append(f"artifacts: {_echo(unexpected)} is not in the ADR-0037 frozen catalogue")
    for path in FROZEN_ARTIFACT_PATHS:
        if path not in digests:
            violations.append(f"artifacts: frozen artifact absent -> {path}")
    return digests


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_contract_pin(path: Path | None = None) -> AmhContractPin:
    """Load, verify and freeze the AMH contract pin. Fail-closed.

    Args:
        path: explicit pin file. Omit to resolve via `resolve_contract_pin_path`
            (`$MAEZO_AMH_CONTRACT_PIN`, then repo checkout, then the wheel's package-adjacent copy).

    Returns:
        The verified `AmhContractPin`.

    Raises:
        AmhContractPinError: the pin is missing, unreadable, not JSON, not an object, not
            `PUBLISHED`, carries a placeholder token, declares an envelope order that is not the
            frozen 28 names in order, names a topic/quarantine/major outside the frozen catalogue,
            is missing a Glue schema-version id, or declares a canonical-schema-version downgrade.
            `.violations` carries the complete set.

    Deliberately NOT cached: caching would make the fail-closed guarantee time-dependent (a pin
    replaced under a long-lived process would keep serving the old verdict), and the file is a few
    kilobytes read at composition time. A caller that wants one instance holds one instance.
    """
    pin_path = path if path is not None else resolve_contract_pin_path()

    if not pin_path.is_file():
        raise AmhContractPinError([f"pin file not found: {pin_path}"])
    try:
        text = pin_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AmhContractPinError([f"{pin_path}: unreadable ({exc})"]) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AmhContractPinError([f"{pin_path}: not valid JSON ({exc})"]) from exc
    if not isinstance(raw, dict):
        raise AmhContractPinError([f"{pin_path}: top level must be a JSON object"])

    violations: list[str] = []
    _check_provenance(violations, raw)
    _check_evidence(violations, raw)
    _check_placeholders(violations, raw)
    _check_manifest(violations, raw)
    _check_envelope(violations, raw)
    topics = _check_topics(violations, raw)
    glue = _check_glue(violations, raw)
    artifacts = _check_artifacts(violations, raw)
    fixtures = _digest_map(violations, raw, "fixtures", key="vendored_path")

    if violations:
        raise AmhContractPinError(violations)

    envelope = raw["envelope"]
    provenance = raw["provenance"]
    return AmhContractPin(
        contract_name=provenance["contract_name"],
        canonical_schema_version=provenance["canonical_schema_version"],
        compatibility_mode=provenance["compatibility_mode"],
        status=provenance["status"],
        amh_commit_sha=provenance["amh_commit_sha"],
        evidence_id=provenance["evidence_id"],
        manifest_digest=raw["manifest_pin"]["sha256"],
        manifest_path=raw["manifest_pin"]["path"],
        envelope_field_order=tuple(envelope["field_order"]),
        envelope_field_count=envelope["field_count"],
        source_product_vocabulary=tuple(envelope["source_product_vocabulary"]),
        payload_hash_canonicalization=envelope["payload_hash_canonicalization"],
        topics=tuple(topics),
        glue=glue,
        artifact_digests=MappingProxyType(artifacts),
        fixture_digests=MappingProxyType(fixtures),
        source_path=pin_path,
    )


__all__ = [
    "CONTRACT_PIN_RELATIVE_PATH",
    "FROZEN_ARTIFACT_PATHS",
    "FROZEN_COMPATIBILITY_MODE",
    "FROZEN_COMPATIBILITY_RESULT",
    "FROZEN_CONTRACT_NAME",
    "FROZEN_ENVELOPE_FIELD_ORDER",
    "FROZEN_GLUE_SCHEMA_KEYS",
    "FROZEN_SCHEMA_VERSION_STATUS",
    "FROZEN_SOURCE_PRODUCT_VOCABULARY",
    "FROZEN_STATUS",
    "FROZEN_TOPICS",
    "FROZEN_TOPIC_MAJOR",
    "MAEZO_AMH_CONTRACT_PIN_ENV",
    "MAX_ECHOED_PIN_VALUE_CHARS",
    "MINIMUM_CANONICAL_SCHEMA_VERSION",
    "PLACEHOLDER_SUBSTRINGS",
    "REQUIRED_EVIDENCE_SECTIONS",
    "AmhAdapterError",
    "AmhContractPin",
    "AmhContractPinError",
    "GlueRegistryPin",
    "TopicPin",
    "load_contract_pin",
    "parse_semver",
    "resolve_contract_pin_path",
]
