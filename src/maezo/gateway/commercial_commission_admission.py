"""Registered commission admission A8 (GAP-ATTRIBUTION-COMMISSION, NR-09) — the recorded act that binds.

The commission dimension is fail-closed by ABSENCE, even more than by construction: the
frozen C6-4 contract carries ZERO commission/attribution surface (case-insensitive
`commission` and `attribution` over `contract-c6-4.json` = 0 hits each; none of the 42
`x-commands` names `commission.settle`, `value_transfer.register`, `opportunity.attribute`
or any payout base — checked live and pinned by test), the contract's own `x-status`
keeps `human_acts` and `tasy_write_homologation` `NOT_ESTABLISHED` with `production:
NO_GO`, and `src/` carries no commission calculation, display, score, payout, sponsored
ranking or competitor comparison anywhere (veredito ABSENT —
`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, admission A8 and §4).
The registry candidates OP17 `commission.settle` / OP19 `value_transfer.register` are
NOT contract commands: they stay `candidate_not_live` (GP5–GP10), outside the 42 and
outside the matrix denominator 51, and `SP-OP-PAGTO-001` is a mold, never a source.
The only name-adjacent substrates in `src/` are NON-commission by kind and stay
untouched: governance/custody "attribution" (ADR-0007 decision attribution in
`tools/workers/contas.py`, error-layer attribution in `gateway/effect_pep.py`,
declared-capability governance in `runtime/agent_runtime/a2a_composition.py`), and the
OP14 channel attribute `attribution_basis_ref` of the vendor projection
(`gateway/capabilities/vendor_journey/projection.py`) — an opaque Ref of the channel
view (the A10 NR-09 dimension: membership/canal), never a rate, base or generating fact.
Isolation is pinned by test; none of them is consumed as commission substrate.

That decision is an owner act — roles EXACTLY `comercial` + `legal` + `DPO` (A-INDEX/
GAP-CATALOG literal; NR-09 attribution/commission is a three-role decision and no leg
derives from another's acceptance — mold A6/A7) — archived as an admission record and
consumed here, never a code change, feature flag, environment variable or process
global. The dossier transcribes TWO forms of the publicable object and this loader
machine-checks the fork:

- **Forma 1 — ratified policy (`decision == "admitted"`):** REFUSED AT THIS PIN, always,
  with `admission_scope_invalid`. The policy form demands an EXACT scope (which events,
  which base, which disclosure — dossier §"Opções de decisão"), and the frozen contract
  has no command for any of it (zero commission commands; GP5–GP10 remain
  `candidate_not_live`). The dossier is literal that ratification "habilita a evolução
  do stub NR-09 por sucessor versionado" and that the stub fills "somente por sucessor
  versionado com diff revisado": a successor freeze + a new record + a re-pinned loader,
  never a reused one. A Forma-1 record against `d1d796b8…` is therefore scope-invalid BY
  CONSTRUCTION — there is no owner wording that can make an empty command universe carry
  "escopo exato (quais eventos, qual base, qual disclosure)".
- **Forma 2 — registered out-of-scope decision (`decision == "admitted_out_of_scope"`,
  the dossier's literal decision value):** the ONLY admitted state this parser can
  produce. The decision admits NOTHING machine-checkable — `admitted_commands` must be
  EXACTLY empty (any non-empty scope names a command the frozen contract does not
  have) — and it changes no runtime behavior maezo-side: its maezo-side effect is
  precisely that no surface may ever grow a commission/score/payout/ranking/competitor-
  comparison fact ("ausência que não é flag": the absent surface is the recorded limit,
  never a toggle).
- **`refused` / `deferred`:** refuse as `admission_not_admitted` — the gap stays a
  PENDING pendência, never silently promoted to a registered out-of-scope limit.

Structural, admission-independent invariants mirrored here from the dossier's
guardanapo (anti-padrão v3 §9.1) and from the closed portal stub
`commission-policy.proposed.v1` (`additionalProperties: false` + four `const`s, the
maezo-side typed mirror — proven without any record):
`refuse_commission_policy_payload` refuses any payload that FABRICATES the family: a
payload naming the stub family must carry EXACTLY the stub's nine fields with EXACTLY
the four consts (`status` `PROPOSED_STUB_NO_ADMITTED_SOURCE`, `ratification`
`REQUIRED_FROM_EXTERNAL_OWNERS_COMMERCIAL_LEGAL_DPO`, `monetaryContent`
`ABSENT_UNTIL_RATIFIED`) — the stub is not a ratification and carries no values, so a
mutated const is fabricated ratification/monetary content and an extra field is the
value the stub's `additionalProperties: false` forbids; and independently of the family
marker, a stray `monetaryContent`/`ratification` field (both names transcribed from the
stub) with any value other than the stub's marker consts refuses — a successor policy
binds only through a new versioned schema, never by re-declaring these fields with new
values. The guard holds with or without an admission (the A6 lesson: the shape guard IS
the refusal).

The record field set is transcribed, not invented: objeto + URL + SHA256 + timestamp
UTC + identidade dos owners + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`,
seção A8 e §5). The record must cite the frozen C6-4 contract pin below — a divergent
sha is a loud refusal, because a successor contract requires a new freeze and a new
record, never a reused one.

No record ships in this repository and none is defaulted anywhere: absence keeps the
commission dimension closed, which is the shipped state (ABSENT — nothing calculates,
nothing displays, nothing ranks). Decision fields are owner-only (ADR-0007); agents
never author them, never a base, table or generating fact. Every call re-reads and
re-validates its record ("valida o sha do pin em toda leitura" — GAP-CATALOG-A1-A10.md,
admission A8); there is no cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A8_ADMISSION_ID: Final = "A8"
# Same closed record family as every A1–A10 admission (schema shared with A1–A7); each
# admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A8_UNLOCKS: Final = "stub NR-09 (decisão)"
# The ABSENCE is the gap: zero commands of the frozen contract name commission or
# attribution (grep `-i "commission"` and `-i "attribution"` over `contract-c6-4.json`
# = 0 hits each; none of the 42 `x-commands` either, and OP17/OP19 stay
# `candidate_not_live` outside the contract). The admitted scope of an A8 record is
# EXACTLY the empty set — the out-of-scope decision admits no command, and the policy
# form cannot exist at this pin (module docstring, Forma 1).
A8_ADMITTED_COMMANDS: Final[frozenset[str]] = frozenset()
# A-INDEX/GAP-CATALOG literal roles: `owners: ["comercial", "legal", "DPO"]`. The trio
# is EXACT — NR-09 attribution/commission is explicitly a comercial+legal+DPO decision,
# and no leg derives from the others' acceptance (mold A6). The portal stub's
# `ratification` const names the same trio, transcribed below in `COMMISSION_POLICY_…`.
A8_OWNER_ROLES: Final = frozenset({"comercial", "legal", "DPO"})
# The dossier's literal decision value for the out-of-scope form (`admitted_out_of_
# scope` — dossiê A8 §"Opções de decisão", Forma 2: "`A8-ADMISSION-RECORD.json` com
# decisão `admitted_out_of_scope`"). Forma 1 (`admitted`) and the no-go/deferral values
# complete the transcribed vocabulary.
A8_OUT_OF_SCOPE_DECISION: Final = "admitted_out_of_scope"
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the A1–A7 pins and
# the codec pin `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A8_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

# The closed portal stub `commission-policy.proposed.v1` (`omni_sales_platform/
# src/contracts/schemas/commission-policy.proposed.v1.json`), transcribed — not
# invented: `additionalProperties: false` over EXACTLY these nine properties, four of
# which are `const`s. The stub is a PROPOSED placeholder: it is not a ratification and
# carries no values; "no rate, amount, percentage, currency, threshold, table row or
# any other value exists in this shape, and none may be added before the owners
# ratify" — the evolution of the stub is successor-versioned only (dossier A8, Forma 1).
COMMISSION_POLICY_STUB_SCHEMA: Final = "commission-policy.proposed.v1"
COMMISSION_POLICY_STUB_FIELDS: Final = frozenset(
    {
        "schemaVersion",
        "status",
        "policyRef",
        "sourceAuthorityRef",
        "calculationBaseRef",
        "attributionPolicyRef",
        "generatingFactRefs",
        "ratification",
        "monetaryContent",
    }
)
COMMISSION_POLICY_STUB_STATUS: Final = "PROPOSED_STUB_NO_ADMITTED_SOURCE"
COMMISSION_POLICY_STUB_RATIFICATION: Final = "REQUIRED_FROM_EXTERNAL_OWNERS_COMMERCIAL_LEGAL_DPO"
COMMISSION_POLICY_STUB_MONETARY_ABSENT: Final = "ABSENT_UNTIL_RATIFIED"

AdmissionRefusalReason = Literal[
    "admission_record_invalid",
    "admission_not_admitted",
    "admission_owner_invalid",
    "admission_scope_invalid",
    "admission_pin_divergent",
    "admission_expired",
]

CommissionPayloadRefusalReason = Literal[
    "commission_payload_not_object",
    "commission_policy_stub_shape_invalid",
    "commission_policy_stub_const_mutated",
    "commission_monetary_content_fabricated",
    "commission_ratification_fabricated",
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


class CommissionAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


class CommissionPayloadError(Exception):
    """Loud refusal of a payload that fabricates a commission policy (anti-padrão v3 §9.1).

    Admission-independent by design: the stub's structural clauses hold before any
    record exists, while one is pending, and after a decision is registered.
    """

    def __init__(self, reason_code: CommissionPayloadRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: CommissionPayloadRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class CommissionAdmission:
    """Validated A8 admission value — attests the out-of-scope decision; binds no command.

    The only admitted state this parser can produce is the registered out-of-scope
    decision (`admitted_out_of_scope`): the policy form is scope-invalid at this pin by
    construction, and the decision's admitted command set is exactly empty.
    """

    admission_id: Literal["A8"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> CommissionAdmissionError:
    return CommissionAdmissionError(reason_code)


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


def refuse_commission_policy_payload(payload: object) -> None:
    """Refuse any payload that fabricates the commission-policy family (anti-padrão v3 §9.1).

    The maezo-side typed mirror of the closed portal stub
    `commission-policy.proposed.v1` (`additionalProperties: false` + four `const`s):
    returns None ONLY for a JSON-object payload that fabricates nothing — a payload NOT
    naming the stub family and carrying no stray `monetaryContent`/`ratification` field
    passes (unrelated payloads are not commission policy), while any of the following
    raises BEFORE any value could be displayed or computed from it:

    - a payload naming the family (`schemaVersion == commission-policy.proposed.v1`)
      that does not carry EXACTLY the stub's nine fields (`commission_policy_stub_
      shape_invalid` — the value `additionalProperties: false` forbids) or whose
      `status`/`ratification`/`monetaryContent` consts diverge (`commission_policy_
      stub_const_mutated` — a "ratified"/active/monetary claim is fabricated
      ratification: the stub is not a ratification and carries no values);
    - ANY payload (family marker or not) carrying `monetaryContent` with a value other
      than `ABSENT_UNTIL_RATIFIED` (`commission_monetary_content_fabricated`) or
      `ratification` with a value other than
      `REQUIRED_FROM_EXTERNAL_OWNERS_COMMERCIAL_LEGAL_DPO`
      (`commission_ratification_fabricated`) — both field names are transcribed from
      the stub and their only pre-successor values are its consts; a ratified policy
      binds only through a successor versioned schema, never by re-declaring these
      fields with new values.

    Non-object payloads are not commission-policy payload objects and refuse as well.
    This is admission-independent: it holds with no record on disk, exactly as it will
    hold with a registered out-of-scope decision, because a policy fabricated under
    pendência is the same fabrication under any decision.
    """
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise CommissionPayloadError("commission_payload_not_object")
    if payload.get("schemaVersion") == COMMISSION_POLICY_STUB_SCHEMA:
        if set(payload) != set(COMMISSION_POLICY_STUB_FIELDS):
            raise CommissionPayloadError("commission_policy_stub_shape_invalid")
        if (
            payload.get("status") != COMMISSION_POLICY_STUB_STATUS
            or payload.get("ratification") != COMMISSION_POLICY_STUB_RATIFICATION
            or payload.get("monetaryContent") != COMMISSION_POLICY_STUB_MONETARY_ABSENT
        ):
            raise CommissionPayloadError("commission_policy_stub_const_mutated")
    if "monetaryContent" in payload and payload["monetaryContent"] != COMMISSION_POLICY_STUB_MONETARY_ABSENT:
        raise CommissionPayloadError("commission_monetary_content_fabricated")
    if "ratification" in payload and payload["ratification"] != COMMISSION_POLICY_STUB_RATIFICATION:
        raise CommissionPayloadError("commission_ratification_fabricated")


def parse_commission_admission(record: bytes, *, now: datetime) -> CommissionAdmission:
    """Validate admission record bytes against the frozen A8 admission.

    Every structural divergence refuses loudly; `refused`/`deferred` owner decisions
    refuse as `admission_not_admitted`; a Forma-1 policy record (`decision ==
    "admitted"`) refuses as `admission_scope_invalid` BY CONSTRUCTION at this pin (zero
    commission commands in the frozen contract — the dossier's successor-schema rule);
    the ONLY admitted state is the out-of-scope decision, which demands EXACTLY the
    `comercial` + `legal` + `DPO` owner trio and an EXACTLY empty admitted command set.
    `now` must be timezone-aware. This is a pure per-call validation: callers that read
    from disk re-read per call (`read_commission_admission`), never cache.
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
        or value["admission_id"] != A8_ADMISSION_ID
        or value["unlocks"] != A8_UNLOCKS
    ):
        raise _refuse("admission_record_invalid")
    decision = value["decision"]
    if decision not in ("admitted", A8_OUT_OF_SCOPE_DECISION, "refused", "deferred"):
        raise _refuse("admission_record_invalid")
    if decision in ("refused", "deferred"):
        raise _refuse("admission_not_admitted")
    if decision == "admitted":
        # Forma 1 (política ratificada) against THIS pin: scope-invalid by construction.
        # The exact scope the dossier demands (quais eventos, qual base, qual disclosure)
        # has no command to live on — zero commission/attribution menções in the frozen
        # contract, OP17/OP19 remain candidate_not_live, and the dossier is literal
        # ("habilita a evolução do stub NR-09 por sucessor versionado"; "somente por
        # sucessor versionado com diff revisado"). A successor freeze + new record +
        # re-pinned loader is the only road; never a reused one.
        raise _refuse("admission_scope_invalid")
    owners = value["owners"]
    if (
        not isinstance(owners, list)
        or not owners
        or not all(isinstance(owner, str) for owner in owners)
        or len(set(owners)) != len(owners)
        # EXACTLY the A-INDEX trio — NR-09 attribution/commission is a
        # comercial+legal+DPO decision and no leg derives from the others; a one-legged
        # or two-legged record never parses (mold A6).
        or set(owners) != A8_OWNER_ROLES
    ):
        raise _refuse("admission_owner_invalid")
    commands = value["admitted_commands"]
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) for command in commands)
        # EXACTLY empty: the decision admits nothing, and any named command would be a
        # command the frozen contract does not carry (0 commission commands — pinned).
        or set(commands) != A8_ADMITTED_COMMANDS
    ):
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A8_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return CommissionAdmission(
        admission_id="A8",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_commission_admission(path: str | Path, *, now: datetime) -> CommissionAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence of
    an admission, and callers keep the commission dimension closed (the shipped ABSENT
    state: no surface calculates or displays comissão/score/payout/ranking/comparação
    de concorrentes) without inventing a default.
    """
    return parse_commission_admission(Path(path).read_bytes(), now=now)
