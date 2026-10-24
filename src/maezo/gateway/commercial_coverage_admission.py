"""Registered coverage admission A4 (GAP-COVERAGE vigência) — the recorded act that binds.

The commercial coverage read dimension is fail-closed by construction even without this
module: the frozen contract carries no coverage command among the 42 (`x-commands`) —
only the nominal READ shape `CustomerCoverageRead` (`customer-coverage.proposed.v1`,
`contract-c6-4.json:8622-8663`) inside the journey snapshot, and the invariant
"Contract acceptance/issuance/enrollment/coverage remain independent" (`:454`). The piece
that was missing for A4 was the authority side: nothing in `src/` could say WHO may decide
that a source-asserted coverage fact (`coverageState` `active`/`revoked`) may cross the
Sales disclosure boundary at all — a bound session disclosed whatever the closed schema
let the source assert. The coverage SOURCE itself still does not exist: readiness CR-F4
stays `READ_ONLY_COVERAGE_PRESENT;ENROLLMENT_AGENDA_ABSENT_FROM_PIN`
(`docs/design/v21-capabilities/source-readiness.json`), veredito ABSENT
(`GAP-CATALOG-A1-A10.md`, admission A4) — the owner-published fonte de vigência
(issuer ≠ fonte de cobertura, `coverage_generation_ref`, `evidence_basis: coverage_source`)
is owner custody this record never substitutes (v3 §9.1: no simulated source).

That decision is an owner act (roles steward AMH/direito, admission A4 of
`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, the tracked maezo-side
mirror of the pending owner dossier), archived as an admission record and consumed
here — never a code change, feature flag, environment variable or process global.

The record field set is transcribed, not invented: objeto (projeção comercial de vigência
com identidade/linkage/source receipt/generation — nunca `issued→active`) + URL + SHA256 +
timestamp UTC + identidade do owner + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`,
seção A4 e §5). The machine-checkable admitted scope is exactly the nominal coverage read
projection `customer-coverage.proposed.v1` — a READ, never a command: no coverage command
exists in the frozen contract, and this admission creates none. The read-only clinical port
(`ClinicalContextPort.get_subject_coverage`, `adapters/amh/subject_context.py`) is
explicitly outside this scope: the dossier forbids it from ever becoming the commercial
projection, and nothing here reads or wraps it. The record must cite the frozen C6-4
contract pin below — a divergent sha is a loud refusal, because a successor contract
requires a new freeze and a new record, never a reused one.

No record ships in this repository and none is defaulted anywhere: absence keeps the
coverage dimension closed (the composition's gate), which is the fail-closed state.
Decision fields are owner-only (ADR-0007); agents never author them. Every call re-reads
and re-validates its record ("valida o sha do pin em toda leitura" — GAP-CATALOG-A1-A10.md,
admission A4); there is no cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A4_ADMISSION_ID: Final = "A4"
# Same closed record family as every A1–A10 admission (schema shared with A1/A2/A3); each
# admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A4_UNLOCKS: Final = "P6.3"
# The nominal coverage READ projection (`CustomerCoverageRead` schemaVersion,
# `contract-c6-4.json:8635,8660`) — the only coverage carrier of the frozen contract, and
# never a command: `x-commands` carries no coverage command at all.
A4_ADMITTED_SCOPE: Final = "customer-coverage.proposed.v1"
A4_OWNER_ROLES: Final = frozenset({"steward AMH", "direito"})
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the A1/A2/A3 pins and
# the codec pin `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A4_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

AdmissionRefusalReason = Literal[
    "admission_record_invalid",
    "admission_not_admitted",
    "admission_owner_invalid",
    "admission_scope_invalid",
    "admission_pin_divergent",
    "admission_expired",
]

_RECORD_FIELDS: Final = frozenset(
    {
        "schema",
        "admission_id",
        "decision",
        "owners",
        "decided_at",
        "valid_until",
        "unlocks",
        "admitted_scope",
        "contract_pin",
        "evidence",
    }
)
_PIN_FIELDS: Final = frozenset({"contract_id", "revision", "sha256"})
_EVIDENCE_FIELDS: Final = frozenset({"object_url", "object_sha256", "origin_git_sha"})


class CoverageReadAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never opens anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class CoverageReadAdmission:
    """Validated A4 admission value — the only registered key the coverage read opens."""

    admission_id: Literal["A4"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> CoverageReadAdmissionError:
    return CoverageReadAdmissionError(reason_code)


def _aware(value: object) -> datetime:
    """UTC-offset timestamp or refusal; naive timestamps are never admitted values."""
    if not isinstance(value, str):
        raise _refuse("admission_record_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _refuse("admission_record_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _refuse("admission_record_invalid")
    return parsed


def _string_fields(value: object, fields: frozenset[str]) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise _refuse("admission_record_invalid")
    if not all(isinstance(item, str) and item for item in value.values()):
        raise _refuse("admission_record_invalid")
    return cast("dict[str, str]", value)


def _hexed(value: str, length: int) -> bool:
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


def parse_coverage_read_admission(record: bytes, *, now: datetime) -> CoverageReadAdmission:
    """Validate admission record bytes against the frozen A4 admission.

    Every structural divergence refuses loudly; `refused`/`deferred` owner decisions
    refuse as `admission_not_admitted` — a closed coverage dimension is the only outcome
    this function can produce without `decision == "admitted"`, the exact frozen pin and
    the exact admitted projection. `now` must be timezone-aware. This is a pure per-call
    validation: callers that read from disk re-read per call
    (`read_coverage_read_admission`), never cache.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("commercial_admission_clock_invalid")
    try:
        value = json.loads(record)
    except ValueError:
        raise _refuse("admission_record_invalid") from None
    if not isinstance(value, dict) or set(value) != set(_RECORD_FIELDS):
        raise _refuse("admission_record_invalid")
    if (
        value["schema"] != ADMISSION_RECORD_SCHEMA
        or value["admission_id"] != A4_ADMISSION_ID
        or value["unlocks"] != A4_UNLOCKS
    ):
        raise _refuse("admission_record_invalid")
    decision = value["decision"]
    if decision not in ("admitted", "refused", "deferred"):
        raise _refuse("admission_record_invalid")
    if decision != "admitted":
        raise _refuse("admission_not_admitted")
    owners = value["owners"]
    if (
        not isinstance(owners, list)
        or not owners
        or not all(isinstance(owner, str) for owner in owners)
        or len(set(owners)) != len(owners)
        or not set(owners).issubset(A4_OWNER_ROLES)
    ):
        raise _refuse("admission_owner_invalid")
    scope = value["admitted_scope"]
    if not isinstance(scope, str) or scope != A4_ADMITTED_SCOPE:
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A4_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return CoverageReadAdmission(
        admission_id="A4",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_coverage_read_admission(path: str | Path, *, now: datetime) -> CoverageReadAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence of an
    admission, and callers keep the coverage read dimension closed (the composition's
    gate) without inventing a default.
    """
    return parse_coverage_read_admission(Path(path).read_bytes(), now=now)
