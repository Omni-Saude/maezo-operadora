"""Registered suppression admission A6 (GP11 não-contato) — the recorded act that binds.

The GP11 non-contact path is fail-closed by construction even without this module: the
runtime composition never threads the `suppression_store` seam
(`runtime/worker_runtime/service.py#register_default_workers` passes only
`dmn/engine/audit_sink/denial_notice_host/tenant_id/kafka/dossier_dispatcher/
document_request_host_installed`), so `vendor.suppression.verify_subject` refuses its
modeled boundary error (`ERR_SUPP_SUBJECT_UNRESOLVED` — UNKNOWN, never zero) on EVERY
tick, and the effect PEP refuses any `EffectCall` naming `SP-OP-SUPP-001` with
`PROCESS_KEY_NAO_PERMITIDA` (`gateway/effect_pep.py` `KNOWN_PROCESS_KEYS` — 15 keys, the
key stays out BY DESIGN: `platform/privacy/program_publication.py` pins that the process
is started by the application plan on an EXTERNAL holder request (art. 7º IX + 18 §2º),
NEVER by an agent, so ADR-0016 stays at 15 even after any admission). The store, taxonomy
and clock substrate is strong (`gateway/vendor_suppression.py`: k-anon k=100 over the
C2/C6 tuple, taxonomy C1–C6, SLA 15 business days with the deadline computed at birth and
`DEADLINE_UNCOMPUTABLE` fail-closed, audience gate 300, presence-only KPI; migration 0021;
`PostgresSuppressionStore`), but the seams the SP-OP-SUPP-001 handlers need are not served
and the process contract stays DRAFT — veredito PARTIAL
(`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, admission A6).

That decision is an owner + DPO act (roles `owner (origem)` / `DPO (origem)` — the
A-INDEX/GAP-CATALOG literal transcription; the DPO countersignature over the design is a
formal act of the encarregado and NEVER an agent output, ADR-0007), archived as an
admission record and consumed here — never a code change, feature flag, environment
variable or process global. The dossier's Track-B chain this record attests (transcribed,
not invented) is: (1) PR-2 of the origin from the unmerged worktrees; (2) suppression
store/transport served (`PostgresSuppressionStore` readable over migration 0021; DMN
evaluated engine-side via `dmn=` — local evaluator forbidden); (3) the formal DPO
countersignature; (4) the honored-list operation. Until that record exists the runtime
keeps refusing (seam absent) and the PEP keeps refusing the key.

What this record binds, maezo-side: the runtime's ONE suppression seam. With a parsed
`SuppressionAdmission` the composition may thread a real store into the
`vendor.suppression.*` handlers; without one, threading a store refuses loud at boot
(`ValueError("commercial_suppression_admission_invalid")`) and the shipped per-tick
refusal is the state. The record never substitutes the honored-list evidence itself:
`registered ≠ honored` stays structural — a `honrado` transition refuses without the
`HonoredListEvidence` (explicit `REGISTRO_HONRADO` route + `sourceReceiptRef` +
`listPlanRevision`), exactly the dossier's anti-pattern clause (v3 §9.1: no pre-bind, no
honra por omissão).

The record field set is transcribed, not invented: objeto + URL + SHA256 + timestamp UTC +
identidade dos owners + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`, seção A6 e
§5). The machine-checkable admitted scope is exactly the OP20 registry command
`suppression.record` — the dossier's "admissão do contrato OP20 `suppression.record` no
main da origem". OP20 is NOT one of the 42 frozen C6-4 commands and names no
51-interface-matrix id (`candidate_matrix_ids` is empty — the affected matrix object is
the GAP-NO-CONTACT gap itself); the record still cites the frozen C6-4 pin below because
the decision is made against that frozen baseline — a divergent sha is a loud refusal,
because a successor contract requires a new freeze and a new record, never a reused one.
The record does not promote `docs/processes/contracts/SP-OP-SUPP-001.md` out of DRAFT
(promotion requires DPO review, `docs/review-queue.md`) and does not add the key to any
agent allowlist. The record never binds A1–A5 surfaces and none of them opens this one:
the session keeps its own A2 admission, the catalog its A1, the acceptance effect its A3,
the coverage read its A4 and the support/work dimension its A5.

No record ships in this repository and none is defaulted anywhere: absence keeps the
suppression execution dimension closed, which is the shipped state. Decision fields are
owner-only (ADR-0007); agents never author them. Every call re-reads and re-validates its
record ("valida o sha do pin em toda leitura" — GAP-CATALOG-A1-A10.md, admission A6);
there is no cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A6_ADMISSION_ID: Final = "A6"
# Same closed record family as every A1–A10 admission (schema shared with A1–A5); each
# admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A6_UNLOCKS: Final = "P6.4 (honra); NR-08"
# The exact machine-checkable scope: the OP20 registry command the dossier names. No C6-4
# command, no OP-matrix id and no transport is in this scope (`candidate_matrix_ids: []` —
# the affected matrix object is the GAP-NO-CONTACT gap itself).
A6_ADMITTED_COMMANDS: Final = frozenset({"suppression.record"})
# A-INDEX/GAP-CATALOG literal roles: `owners: ["owner (origem)", "DPO (origem)"]`. The DPO
# countersignature is one of the two legs — a record citing only the owner role is a loud
# refusal, because the owner's acceptance of design inputs is NOT the countersignature.
A6_OWNER_ROLES: Final = frozenset({"owner (origem)", "DPO (origem)"})
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the A1–A5 pins and
# the codec pin `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A6_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

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


class SuppressionAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class SuppressionAdmission:
    """Validated A6 admission value — the only registered key the suppression seam opens."""

    admission_id: Literal["A6"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> SuppressionAdmissionError:
    return SuppressionAdmissionError(reason_code)


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


def parse_suppression_admission(record: bytes, *, now: datetime) -> SuppressionAdmission:
    """Validate admission record bytes against the frozen A6 admission.

    Every structural divergence refuses loudly; `refused`/`deferred` owner decisions
    refuse as `admission_not_admitted` — a closed suppression execution dimension is the
    only outcome this function can produce without `decision == "admitted"`, both owner
    roles (`owner (origem)` AND `DPO (origem)` — the countersignature leg included), the
    exact frozen pin and the exact admitted scope. `now` must be timezone-aware. This is a
    pure per-call validation: callers that read from disk re-read per call
    (`read_suppression_admission`), never cache.
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
        or value["admission_id"] != A6_ADMISSION_ID
        or value["unlocks"] != A6_UNLOCKS
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
        # EXACTLY the dossier pair — the owner's acceptance of design inputs is NOT the DPO
        # countersignature (dossier A6 §"Owners externos"), so a record citing only one of
        # the two roles never parses: without both legs there is no admitted state at all.
        or set(owners) != A6_OWNER_ROLES
    ):
        raise _refuse("admission_owner_invalid")
    commands = value["admitted_commands"]
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) for command in commands)
        or set(commands) != A6_ADMITTED_COMMANDS
    ):
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A6_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return SuppressionAdmission(
        admission_id="A6",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_suppression_admission(path: str | Path, *, now: datetime) -> SuppressionAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence of an
    admission, and callers keep the suppression execution dimension closed (the
    composition's shipped fail-closed refusal) without inventing a default.
    """
    return parse_suppression_admission(Path(path).read_bytes(), now=now)
