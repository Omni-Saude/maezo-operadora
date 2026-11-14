"""Registered source admission A1 (CR-F2 catalog) — the recorded act that binds.

The public catalog read path is fail-closed by construction even without this module:
composition returns an unbound gateway without its authority ports
(`commercial_admission.compose_public_catalog_gateway`) and the route answers 503
`source_unavailable` without a bound gateway (`portal/api/commercial.py`). The piece
that was missing for A1 was the authority side: nothing in `src/` could say WHO may
decide that the commercial catalog source is bound at all (`source-readiness.json`
CR-F2 = `COMMERCIAL_AUTHORITY_ABSENT_FROM_PIN`, `published_for_new_scope: false`).

That decision is an owner act (roles produto/comercial/legal, admission A1 of
`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, the tracked maezo-side
mirror of the pending owner dossier), archived as an admission record and consumed
here — never a code
change, feature flag, environment variable or process global, and never a
"prepared" source that simulates the admission (v3 §9.1).

The record field set is transcribed, not invented: objeto + URL + SHA256 + timestamp UTC +
identidade do owner + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`, seção A1 e §5,
que transcrevem as regras do shelf do portal). The machine-checkable
admitted scope is exactly the contract command `catalog.get` (the single fixed
transport `GET /api/v1/sales/public/catalog`); the record must cite the frozen C6-4
contract pin below — a divergent sha is a loud refusal, because a successor contract
requires a new freeze and a new record, never a reused one.

No record ships in this repository and none is defaulted anywhere: absence keeps
every path closed, which is the shipped state. Decision fields are owner-only
(ADR-0007); agents never author them. Every call re-reads and re-validates its
record ("valida o sha do pin em toda leitura" — GAP-CATALOG-A1-A10.md, admission A1); there is no
cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A1_ADMISSION_ID: Final = "A1"
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A1_UNLOCKS: Final = "P6.1"
A1_ADMITTED_COMMANDS: Final = frozenset({"catalog.get"})
A1_OWNER_ROLES: Final = frozenset({"produto", "comercial", "legal"})
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the codec pin
# `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A1_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

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


class CatalogSourceAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class CatalogSourceAdmission:
    """Validated A1 admission value — the only registered key a composition may bind."""

    admission_id: Literal["A1"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> CatalogSourceAdmissionError:
    return CatalogSourceAdmissionError(reason_code)


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


def parse_catalog_source_admission(record: bytes, *, now: datetime) -> CatalogSourceAdmission:
    """Validate admission record bytes against the frozen A1 admission.

    Every structural divergence refuses loudly; `refused`/`deferred` owner decisions
    refuse as `admission_not_admitted` — a closed path is the only outcome this function can
    produce without `decision == "admitted"` and the exact frozen pin. `now` must be
    timezone-aware. This is a pure per-call validation: callers that read from disk
    re-read per call (`read_catalog_source_admission`), never cache.
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
        or value["admission_id"] != A1_ADMISSION_ID
        or value["unlocks"] != A1_UNLOCKS
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
        or not set(owners).issubset(A1_OWNER_ROLES)
    ):
        raise _refuse("admission_owner_invalid")
    commands = value["admitted_commands"]
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) for command in commands)
        or set(commands) != A1_ADMITTED_COMMANDS
    ):
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A1_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return CatalogSourceAdmission(
        admission_id="A1",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_catalog_source_admission(path: str | Path, *, now: datetime) -> CatalogSourceAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence
    of an admission, and callers keep the gateway unbound (route 503
    `source_unavailable`) without inventing a default.
    """
    return parse_catalog_source_admission(Path(path).read_bytes(), now=now)
