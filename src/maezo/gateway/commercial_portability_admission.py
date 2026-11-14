"""Registered portability admission A7 (GAP-PORTABILITY, NR-11) — the recorded act that binds.

The portability dimension is fail-closed by ABSENCE, even more than by construction: the
frozen C6-4 contract carries ZERO portability surface (case-insensitive `portab` over
`contract-c6-4.json` = 0 hits; none of the 42 `x-commands` names portability or a plan
cancellation — checked live and pinned by test), the journey snapshot carries no
portability arm, and `src/` carries no commercial read/intake for portabilidade de
carências. Veredito ABSENT
(`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, admission A7 and §4).
The ONLY "portabilidade" in `src/` is the LGPD data-subject right of data portability
(`tools/workers/lgpd.py:198` — `tipo_requisicao` `portabilidade` -> direito
`exportacao`, and the same member in `portal/contracts/dsr.py`) — a NAME COLLISION,
never the commercial substrate: the art. 18 right over a person's data is a different
object from NR-11 portability of waiting periods between health plans, and this module
shares no import, symbol or evidence with it (isolation pinned by test; the DSR mapping
itself is untouched by this package).

That decision is an owner act — roles EXACTLY `produto` + `legal` (A-INDEX/GAP-CATALOG
literal; the dossier adds a `fonte própria de portabilidade` authority *to be designated*
and a DPO only when personal data exists, which enter through the Forma-1 successor
package, never through this pin) — archived as an admission record and consumed here,
never a code change, feature flag, environment variable or process global. The dossier
transcribes TWO forms of the publicable object and this loader machine-checks the fork:

- **Forma 1 — admitted source (`decision == "admitted"`):** REFUSED AT THIS PIN, always,
  with `admission_scope_invalid`. The source form demands an EXACT scope (read and/or
  intake, which documents/prazos, which separate cancellation origin — dossier §"Objeto
  publicável"), and the frozen contract has no command for any of it (zero portability
  commands; the CR-F5 case kind it would ride on is unpublished — `DeclaredCaseKind` has
  no members). The dossier is literal that the source form binds "por sucessor
  versionado; o schema atual não possui variante de comando — a extensão exige diff de
  contrato revisado": a successor freeze + a new record + a re-pinned loader, never a
  reused one. A Forma-1 record against `d1d796b8…` is therefore scope-invalid BY
  CONSTRUCTION — there is no owner wording that can make an empty command universe
  carry "escopo exato (read e/ou intake...)".
- **Forma 2 — registered ceiling (`decision ==
  "admitted_ceiling_orientation_only"`, the dossier's literal decision value):** the
  ONLY admitted state this parser can produce. The ceiling admits NOTHING machine-
  checkable — `admitted_commands` must be EXACTLY empty (any non-empty scope names a
  command the frozen contract does not have) — and it changes no runtime behavior
  maezo-side: orientation content is portal custody (`contentRef` +
  `sourcePublicationRef` + `publishedAt`, optional `validUntil`), and the ceiling's
  maezo-side effect is precisely that no surface may ever grow a portability verdict.
- **`refused` / `deferred`:** refuse as `admission_not_admitted` — orientation stays a
  PENDING gap, never silently promoted to a ceiling.

Structural, admission-independent invariants mirrored here from the dossier (the portal
schema `portability.proposed.v1` is born `orientation_only` with `not` clauses against
verdicts; this is the maezo-side typed mirror, proven without any record):
`PORTABILITY_VERDICT_FIELDS_PROHIBITED` = the dossier's exact seven fields
(`compatibilityVerdict`, `eligible`, `remainingWaitingDays`, `cancellationRequested`,
`cancellationConfirmed`, `requestSubmitted`, `protocolRef`) — the GAP-CATALOG's
planned-negative list is the same clause minus `cancellationConfirmed`; the binding
list is the dossier's, and it is the stricter one. `refuse_portability_verdict_payload`
refuses any payload object carrying any of them (REG-006 included: an origin-plan
cancellation can never be requested, confirmed or protocolized through a portability
payload), before any disclosure could be derived — the A6 lesson applied: the shape
guard IS the refusal, and it holds with or without an admission.

The record field set is transcribed, not invented: objeto + URL + SHA256 + timestamp
UTC + identidade dos owners + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`,
seção A7 e §5). The record must cite the frozen C6-4 contract pin below — a divergent
sha is a loud refusal, because a successor contract requires a new freeze and a new
record, never a reused one.

No record ships in this repository and none is defaulted anywhere: absence keeps the
portability dimension closed, which is the shipped state (ABSENT — nothing binds,
nothing displays, nothing cancels). Decision fields are owner-only (ADR-0007); agents
never author them. Every call re-reads and re-validates its record ("valida o sha do
pin em toda leitura" — GAP-CATALOG-A1-A10.md, admission A7); there is no cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A7_ADMISSION_ID: Final = "A7"
# Same closed record family as every A1–A10 admission (schema shared with A1–A6); each
# admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A7_UNLOCKS: Final = "NR-11 (decisão)"
# The ABSENCE is the gap: zero commands of the frozen contract name portability (grep
# `-i "portab"` over `contract-c6-4.json` = 0 hits; none of the 42 `x-commands` either).
# The admitted scope of an A7 record is EXACTLY the empty set — the ceiling admits no
# command, and the source form cannot exist at this pin (module docstring, Forma 1).
A7_ADMITTED_COMMANDS: Final[frozenset[str]] = frozenset()
# A-INDEX/GAP-CATALOG literal roles: `owners: ["produto", "legal"]`. The pair is EXACT —
# NR-11 is explicitly a produto+legal decision, and neither leg derives from the other's
# acceptance (mold A6). The dossier's `fonte própria de portabilidade` authority is *a
# designar* and rides the Forma-1 successor package (re-pinned loader), never this pin.
A7_OWNER_ROLES: Final = frozenset({"produto", "legal"})
# The dossier's literal decision value for the ceiling form (`admitted_ceiling_
# orientation_only` — dossiê A7 §"Opções de decisão"). Forma 1 (`admitted`) and the
# no-go/deferral values complete the transcribed vocabulary.
A7_CEILING_DECISION: Final = "admitted_ceiling_orientation_only"
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the A1–A6 pins and
# the codec pin `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A7_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

# The dossier's exact seven structurally-prohibited verdict fields (portal schema
# `portability.proposed.v1` `not` clauses, transcribed): no portability surface may ever
# carry a compatibility verdict, an eligibility, a waiting-period computation, a
# cancellation request/confirmation (REG-006), a submitted request or a protocol ref.
# The GAP-CATALOG planned-negative (iv)(b) lists the same clauses minus
# `cancellationConfirmed`; the dossier list is binding and stricter.
PORTABILITY_VERDICT_FIELDS_PROHIBITED: Final = frozenset(
    {
        "compatibilityVerdict",
        "eligible",
        "remainingWaitingDays",
        "cancellationRequested",
        "cancellationConfirmed",
        "requestSubmitted",
        "protocolRef",
    }
)

AdmissionRefusalReason = Literal[
    "admission_record_invalid",
    "admission_not_admitted",
    "admission_owner_invalid",
    "admission_scope_invalid",
    "admission_pin_divergent",
    "admission_expired",
]

PortabilityPayloadRefusalReason = Literal[
    "portability_payload_not_object",
    "portability_verdict_fields_prohibited",
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


class PortabilityAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


class PortabilityPayloadError(Exception):
    """Loud refusal of a portability payload that fabricates a verdict (REG-006 included).

    Admission-independent by design: the ceiling's structural clauses hold before any
    record exists, while one is pending, and after the ceiling is registered.
    """

    def __init__(self, reason_code: PortabilityPayloadRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: PortabilityPayloadRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class PortabilityAdmission:
    """Validated A7 admission value — attests the registered ceiling; binds no command.

    The only admitted state this parser can produce is the ceiling
    (`admitted_ceiling_orientation_only`): the source form is scope-invalid at this pin
    by construction, and the ceiling's admitted command set is exactly empty.
    """

    admission_id: Literal["A7"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> PortabilityAdmissionError:
    return PortabilityAdmissionError(reason_code)


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


def refuse_portability_verdict_payload(payload: object) -> None:
    """Refuse any portability payload that carries a structurally-prohibited verdict field.

    The maezo-side typed mirror of the portal schema's `not` clauses (dossier A7, Forma 2):
    returns None ONLY for a JSON-object payload carrying none of the seven prohibited
    fields — orientation content (`contentRef`/`sourcePublicationRef`/`publishedAt`/
    optional `validUntil`) passes; any `compatibilityVerdict`/`eligible`/
    `remainingWaitingDays`/`cancellationRequested`/`cancellationConfirmed`/
    `requestSubmitted`/`protocolRef` raises BEFORE any fact could be derived from it.
    Non-object payloads are not portability payload objects and refuse as well. This is
    admission-independent: it holds with no record on disk, exactly as it will hold with
    a registered ceiling, because a verdict fabricated under orientation is the same
    fabrication under any decision.
    """
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise PortabilityPayloadError("portability_payload_not_object")
    if PORTABILITY_VERDICT_FIELDS_PROHIBITED.intersection(payload):
        raise PortabilityPayloadError("portability_verdict_fields_prohibited")


def parse_portability_admission(record: bytes, *, now: datetime) -> PortabilityAdmission:
    """Validate admission record bytes against the frozen A7 admission.

    Every structural divergence refuses loudly; `refused`/`deferred` owner decisions
    refuse as `admission_not_admitted`; a Forma-1 source record (`decision == "admitted"`)
    refuses as `admission_scope_invalid` BY CONSTRUCTION at this pin (zero portability
    commands in the frozen contract — the dossier's successor-contract rule); the ONLY
    admitted state is the ceiling, which demands EXACTLY the `produto` + `legal` owner
    pair and an EXACTLY empty admitted command set. `now` must be timezone-aware. This
    is a pure per-call validation: callers that read from disk re-read per call
    (`read_portability_admission`), never cache.
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
        or value["admission_id"] != A7_ADMISSION_ID
        or value["unlocks"] != A7_UNLOCKS
    ):
        raise _refuse("admission_record_invalid")
    decision = value["decision"]
    if decision not in ("admitted", A7_CEILING_DECISION, "refused", "deferred"):
        raise _refuse("admission_record_invalid")
    if decision in ("refused", "deferred"):
        raise _refuse("admission_not_admitted")
    if decision == "admitted":
        # Forma 1 (fonte própria) against THIS pin: scope-invalid by construction. The
        # exact scope the dossier demands (read e/ou intake, documentos/prazos,
        # cancellation origin separada) has no command to live on — the CR-F5 case kind
        # is unpublished and the dossier is literal ("a extensão exige diff de contrato
        # revisado; bind por sucessor versionado"). A successor freeze + new record +
        # re-pinned loader is the only road; never a reused one.
        raise _refuse("admission_scope_invalid")
    owners = value["owners"]
    if (
        not isinstance(owners, list)
        or not owners
        or not all(isinstance(owner, str) for owner in owners)
        or len(set(owners)) != len(owners)
        # EXACTLY the A-INDEX pair — NR-11 is a produto+legal decision and neither leg
        # derives from the other; a one-legged record never parses (mold A6).
        or set(owners) != A7_OWNER_ROLES
    ):
        raise _refuse("admission_owner_invalid")
    commands = value["admitted_commands"]
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) for command in commands)
        # EXACTLY empty: the ceiling admits nothing, and any named command would be a
        # command the frozen contract does not carry (0 portability commands — pinned).
        or set(commands) != A7_ADMITTED_COMMANDS
    ):
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A7_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return PortabilityAdmission(
        admission_id="A7",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_portability_admission(path: str | Path, *, now: datetime) -> PortabilityAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence of
    an admission, and callers keep the portability dimension closed (the shipped ABSENT
    state: no surface displays compatibility/prazo, nothing cancels an origin plan)
    without inventing a default.
    """
    return parse_portability_admission(Path(path).read_bytes(), now=now)
