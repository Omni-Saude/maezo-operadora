"""Registered support admission A5 (CR-F5/F6 suporte) — the recorded act that binds.

The support/work path is fail-closed by construction even without this module: the bound
Sales composition keeps `support.handoff` and the `work.*` queue commands unbound without
this record (`commercial_admission.compose_commercial_gateway` refuses that dimension with
the shipped fail-closed refusal before any source step), and the case-projection arm of
the journey snapshot (`CustomerSupportRead` `status: "projection"` — `caseKind`/
`caseStatus`/`caseRef`/`vocabularyRef`, `contract-c6-4.json:11031-11121` inside the
`journey.get` snapshot and the `support.handoff` result) stays behind the disclosure gate
(`SupportGatedCodec`), so a source-asserted case fact never crosses the disclosure
boundary — the contract only allows it "under its current published semantics and
receipt" (`x-customer-journey.support_readback`, `:11117`), and no semantics are published
today. The broker-consumer substrate the commands would drive does not exist in the AMH
adapter package: readiness CR-F5 stays
`FOUNDATION_PRESENT;BROKER_CONSUMER_ABSENT_FROM_AMH_ADAPTER_PACKAGE` and CR-F6
`ENVELOPE_PRESENT;NEW_DOMAIN_SEMANTICS_UNVERIFIED`
(`docs/design/v21-capabilities/source-readiness.json`). Veredito PARTIAL
(`GAP-CATALOG-A1-A10.md`, admission A5): `support.handoff`/`work.*` exist as codec
commands (payload/scope validation, no executor) and `SP-OP-ESCALATION-001` is the
human-escalation key allowlisted in `gateway/effect_pep.py`, but the published case
vocabulary does not exist — `DeclaredCaseKind`/`DeclaredCaseStatus` members, audience/role,
human ACK, live channel/sender and wait clocks are owner custody this record never
substitutes (v3 §9.1: no simulated protocol, no portal-computed prazo/SLA).

That decision is an owner act (roles case owner/channel owner/human owners, admission A5
of `docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, the tracked maezo-side
mirror of the pending owner dossier), archived as an admission record and consumed here —
never a code change, feature flag, environment variable or process global, and never a
case consumer that simulates the broker or a confirmation via OP03 (resolution is always
OP07/OP07-family receipt custody of the source).

The record field set is transcribed, not invented: objeto + URL + SHA256 + timestamp UTC +
identidade do owner + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`, seção A5 e
§5). The machine-checkable admitted scope is exactly the six support/work commands of the
frozen contract: `work.list` (scope `interop/sales.work.read`, `contract-c6-4.json:241`)
/ `work.claim`/`work.assign`/`work.handoff`/`work.reconcile` (scope
`interop/sales.work.manage`, `:247-270`) / `support.handoff` (scope
`interop/sales.support`, `:283`). The x-workflow audience "Authenticated qualified staff
only; vendors/customers have no work commands" (`:11067-11069`) stays enforced by the
gateway's scope checks, never by this record. OP07..OP11 and HTTP-04..HTTP-12 are
51-interface-matrix vocabulary, not C6-4 commands
(`admissions[4].candidate_matrix_ids_note`). A5 admits the support/work command dimension
and the disclosure of a source-asserted case projection — the session keeps its own A2
admission, the public catalog its A1, the acceptance effect its A3 and the coverage read
its A4: this record never binds any of those, and none of them opens this one. The
external clinical channel (`x-customer-journey.external_clinical`, `:11118`) and the
journey's `support_topics` stay outside this scope: their channel-origin approval is a
separate qualification. The record must cite the frozen C6-4 contract pin below — a
divergent sha is a loud refusal, because a successor contract requires a new freeze and a
new record, never a reused one.

No record ships in this repository and none is defaulted anywhere: absence keeps the
support/work dimension closed, which is the shipped state. Decision fields are owner-only
(ADR-0007); agents never author them. Every call re-reads and re-validates its record
("valida o sha do pin em toda leitura" — GAP-CATALOG-A1-A10.md, admission A5); there is no
cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A5_ADMISSION_ID: Final = "A5"
# Same closed record family as every A1–A10 admission (schema shared with A1–A4); each
# admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A5_UNLOCKS: Final = "P6.4 (suporte)"
# The exact support/work commands of the frozen contract (`x-commands`,
# `contract-c6-4.json:241-270,283`): the work queue family plus the single support
# handoff. No other command, transport or projection is in this scope.
A5_ADMITTED_COMMANDS: Final = frozenset(
    {"work.list", "work.claim", "work.assign", "work.handoff", "work.reconcile", "support.handoff"}
)
A5_OWNER_ROLES: Final = frozenset({"case owner", "channel owner", "human owners"})
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the A1–A4 pins and
# the codec pin `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A5_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

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


class SupportHandoffAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class SupportHandoffAdmission:
    """Validated A5 admission value — the only registered key the support/work dimension opens."""

    admission_id: Literal["A5"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> SupportHandoffAdmissionError:
    return SupportHandoffAdmissionError(reason_code)


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


def parse_support_handoff_admission(record: bytes, *, now: datetime) -> SupportHandoffAdmission:
    """Validate admission record bytes against the frozen A5 admission.

    Every structural divergence refuses loudly; `refused`/`deferred` owner decisions
    refuse as `admission_not_admitted` — a closed support/work dimension is the only
    outcome this function can produce without `decision == "admitted"`, the exact frozen
    pin and the exact admitted command set. `now` must be timezone-aware. This is a pure
    per-call validation: callers that read from disk re-read per call
    (`read_support_handoff_admission`), never cache.
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
        or value["admission_id"] != A5_ADMISSION_ID
        or value["unlocks"] != A5_UNLOCKS
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
        or not set(owners).issubset(A5_OWNER_ROLES)
    ):
        raise _refuse("admission_owner_invalid")
    commands = value["admitted_commands"]
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) for command in commands)
        or set(commands) != A5_ADMITTED_COMMANDS
    ):
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A5_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return SupportHandoffAdmission(
        admission_id="A5",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_support_handoff_admission(path: str | Path, *, now: datetime) -> SupportHandoffAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence of an
    admission, and callers keep the support/work dimension closed (the composition's
    shipped fail-closed refusal) without inventing a default.
    """
    return parse_support_handoff_admission(Path(path).read_bytes(), now=now)
