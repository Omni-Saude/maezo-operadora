"""Registered acceptance admission A3 (CR-F3 aceite/matrícula) — the recorded act that binds.

The acceptance/enrollment effect path is fail-closed by construction even without this
module: the bound Sales composition keeps `acceptance.record` unbound without this
record (`commercial_admission.compose_commercial_gateway` refuses the effect dimension
with the shipped fail-closed refusal before any source step) and the issuer runtime this
command would drive does not exist — readiness CR-F3 stays
`BUSINESS_WRITES_ABSENT_FROM_PIN;LIVE_OUTCOME_PUBLISHER_UNVERIFIED`, `published_for_new_scope:
false` (`docs/design/v21-capabilities/source-readiness.json`). The piece that was missing
for A3 was the authority side: nothing in `src/` could say WHO may decide that the
acceptance/contratação effect binds at all; the codec authority checks (C6-3
quote/linkage/acceptance/receipt, C6-4 `plan_choices`) and the 231 UNIT zero-skips ship
in main, but no issuer runtime, no OP03/OP04 wire and no published OP receipt timeline
exist (`GAP-CATALOG-A1-A10.md`, admission A3, veredito PARTIAL).

That decision is an owner act (roles cliente/issuer, admission A3 of
`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, the tracked maezo-side mirror
of the pending owner dossier), archived as an admission record and consumed here — never
a code change, feature flag, environment variable or process global, and never an
"issuer" that simulates the admission or fabricates a receipt (v3 §9.1): the record binds
the existing codec-checked path, it does not create a source, and `accepted` without a
source receipt stays refused by the C6-3 checks exactly as before.

The record field set is transcribed, not invented: objeto + URL + SHA256 + timestamp UTC +
identidade do owner + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`, seção A3 e §5).
The machine-checkable admitted scope is exactly the contract command `acceptance.record`
(scope `interop/sales.contract`, `contract-c6-4.json:187`) — the single aceite/matrícula
write of the frozen contract; OP03/OP04 are 51-interface-matrix vocabulary, never C6-4
commands (`admissions[2].candidate_matrix_ids_note`), and no enrollment command exists in
the 42. A3 admits the effect dimension only: the session keeps its own A2 admission and
the public catalog its A1 — this record never binds either. The vendor channel taking
`PURPOSE_DENIED` for OP03/OP04 at the journey binding (`gateway/capabilities/vendor_journey/
binding.py`) is a separate, already fail-closed surface this package does not touch. The
record must cite the frozen C6-4 contract pin below — a divergent sha is a loud refusal,
because a successor contract requires a new freeze and a new record, never a reused one.

No record ships in this repository and none is defaulted anywhere: absence keeps the
acceptance path closed, which is the shipped state. Decision fields are owner-only
(ADR-0007); agents never author them. Every call re-reads and re-validates its record
("valida o sha do pin em toda leitura" — GAP-CATALOG-A1-A10.md, admission A3); there is no
cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A3_ADMISSION_ID: Final = "A3"
# Same closed record family as every A1–A10 admission (schema shared with A1/A2); each
# admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A3_UNLOCKS: Final = "P6.2 (efeitos)"
A3_ADMITTED_COMMANDS: Final = frozenset({"acceptance.record"})
A3_OWNER_ROLES: Final = frozenset({"cliente", "issuer"})
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the A1/A2 pins and
# the codec pin `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A3_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

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
        "admitted_commands",
        "contract_pin",
        "evidence",
    }
)
_PIN_FIELDS: Final = frozenset({"contract_id", "revision", "sha256"})
_EVIDENCE_FIELDS: Final = frozenset({"object_url", "object_sha256", "origin_git_sha"})


class AcceptanceRecordAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class AcceptanceRecordAdmission:
    """Validated A3 admission value — the only registered key the acceptance effect binds."""

    admission_id: Literal["A3"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> AcceptanceRecordAdmissionError:
    return AcceptanceRecordAdmissionError(reason_code)


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


def parse_acceptance_record_admission(record: bytes, *, now: datetime) -> AcceptanceRecordAdmission:
    """Validate admission record bytes against the frozen A3 admission.

    Every structural divergence refuses loudly; `refused`/`deferred` owner decisions
    refuse as `admission_not_admitted` — a closed path is the only outcome this function can
    produce without `decision == "admitted"`, the exact frozen pin and the exact admitted
    command. `now` must be timezone-aware. This is a pure per-call validation: callers that
    read from disk re-read per call (`read_acceptance_record_admission`), never cache.
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
        or value["admission_id"] != A3_ADMISSION_ID
        or value["unlocks"] != A3_UNLOCKS
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
        or not set(owners).issubset(A3_OWNER_ROLES)
    ):
        raise _refuse("admission_owner_invalid")
    commands = value["admitted_commands"]
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) for command in commands)
        or set(commands) != A3_ADMITTED_COMMANDS
    ):
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A3_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return AcceptanceRecordAdmission(
        admission_id="A3",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_acceptance_record_admission(path: str | Path, *, now: datetime) -> AcceptanceRecordAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence of an
    admission, and callers keep the acceptance effect dimension closed (the composition's
    shipped fail-closed refusal) without inventing a default.
    """
    return parse_acceptance_record_admission(Path(path).read_bytes(), now=now)
