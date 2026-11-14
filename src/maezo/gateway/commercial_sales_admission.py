"""Registered session admission A2 (CR-F1 auth Sales) — the recorded act that binds.

The Sales POST path is fail-closed by construction even without this module:
composition returns an unbound gateway without its authority ports
(`commercial_admission.compose_commercial_gateway`) and the route answers 503
`binding_unqualified` without a bound gateway (`portal/api/commercial.py`). The piece
that was missing for A2 was the authority side: nothing in `src/` could say WHO may
decide that a concrete Sales identity/session provider binds at all — the abstract
layer (`SalesCallerVerifier`/`SalesScopeAuthority`) ships with zero concrete providers
in `src/`, and source readiness keeps CR-F1 at
`PARTIAL_IMPLEMENTED_LIVE_UNVERIFIED`, `published_for_new_scope: false`
(`docs/design/v21-capabilities/source-readiness.json`).

That decision is an owner act (roles identity/channel/security/DPO, admission A2 of
`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, the tracked maezo-side
mirror of the pending owner dossier), archived as an admission record and consumed
here — never a code
change, feature flag, environment variable or process global, and never a
"prepared" provider that simulates the admission (v3 §9.1): no simulated login, no
lying session, no auth feature flag, no adapter that anticipates the admitted source.

The record field set is transcribed, not invented: objeto CR-F1 (caller Origin/BFF,
sessão/delegação, tenant/legal entity/subject, memberships/purpose, CSRF/origin,
expiry/revoke, lifecycle/recovery) + URL + SHA256 + timestamp UTC + identidade do
owner + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`, seção A2 e §5). The
machine-checkable admitted scope is exactly the single fixed Sales transport
`POST /api/v1/sales/commercial` (the frozen contract's `x-transports.sales.maezo`;
`x-status.sales_admission` stays `NOT_ESTABLISHED` in the frozen bytes and is
established only by this record). A2 admits the session dimension and no contract
command: the effect surface keeps its own admissions (A3 and later records). The
record must cite the frozen C6-4 contract pin below — a divergent sha is a loud
refusal, because a successor contract requires a new freeze and a new record, never
a reused one.

No record ships in this repository and none is defaulted anywhere: absence keeps
every path closed, which is the shipped state. Decision fields are owner-only
(ADR-0007); agents never author them. Every call re-reads and re-validates its
record ("valida o sha do pin em toda leitura" — GAP-CATALOG-A1-A10.md, admission A2); there is no
cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A2_ADMISSION_ID: Final = "A2"
# Same closed record family as every A1–A10 admission (schema shared with A1's
# `commercial_catalog_admission`); each admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A2_UNLOCKS: Final = "P6.2 (sessão)"
A2_ADMITTED_SURFACE: Final = "POST /api/v1/sales/commercial"
A2_OWNER_ROLES: Final = frozenset({"identity", "channel", "security", "DPO"})
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json` — the same frozen contract
# A1 pins; pinned equal by test.
A2_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

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


class SalesSessionAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class SalesSessionAdmission:
    """Validated A2 admission value — the only registered key a composition may bind."""

    admission_id: Literal["A2"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> SalesSessionAdmissionError:
    return SalesSessionAdmissionError(reason_code)


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


def parse_sales_session_admission(record: bytes, *, now: datetime) -> SalesSessionAdmission:
    """Validate admission record bytes against the frozen A2 admission.

    Every structural divergence refuses loudly; `refused`/`deferred` owner decisions
    refuse as `admission_not_admitted` — a closed path is the only outcome this function can
    produce without `decision == "admitted"` and the exact frozen pin and admitted
    surface. `now` must be timezone-aware. This is a pure per-call validation: callers
    that read from disk re-read per call (`read_sales_session_admission`), never cache.
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
        or value["admission_id"] != A2_ADMISSION_ID
        or value["unlocks"] != A2_UNLOCKS
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
        or not set(owners).issubset(A2_OWNER_ROLES)
    ):
        raise _refuse("admission_owner_invalid")
    surface = value["admitted_scope"]
    if not isinstance(surface, str) or surface != A2_ADMITTED_SURFACE:
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A2_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return SalesSessionAdmission(
        admission_id="A2",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_sales_session_admission(path: str | Path, *, now: datetime) -> SalesSessionAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence
    of an admission, and callers keep the gateway unbound (route 503
    `binding_unqualified`) without inventing a default.
    """
    return parse_sales_session_admission(Path(path).read_bytes(), now=now)
