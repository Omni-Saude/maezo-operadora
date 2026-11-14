"""Registered vendor-channel admission A10 (vendor membership/canal, P6.5) — the recorded act that binds.

A10 is the ONLY one of the ten admissions whose maezo substrate already EXISTS: the vendor
plan v21 ships default-off and complete on the maezo side — the closed allowlist
`VENDOR_OPERATIONS = {access.resolve, offer.compose, external_wait.settle}`
(`gateway/capabilities/vendor_journey/binding.py:63`, OP01/OP02/OP08 consumed by
composition) with `PURPOSE_DENIED` outside it and on every acceptance/enrollment seat
(`:149-153` — OP03 `acceptance.record`/OP04 `enrollment.request` refused at the binding
before the driver ever sees them), `VendorJourneyConfig` default-off by construction
(`:81-96` — "`enabled` is the flag: `False` (the default)"), the read-only OP15
projection (`gateway/capabilities/vendor_journey/projection.py`), the OP13 administrative
act (`tools/workers/vendor_admin.py` + `gateway/human/vendor_membership_administration.py`
— ADR-0063: only an operator's human administrative session provisions; never self-signup)
and the two stores (migrations `0019_vendor_channel_authority` — the ONLY accredited
eligibility source — and `0020_vendor_channel_submissions`). The veredito is therefore
PARTIAL (`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, admission A10 and §4):
what is missing is EXACTLY the OBJECT of the admission, published by the origin — the
Track B grants Portal per OP, the membership PUBLISHED (not `pending`: `application_status
pending` is not applied access), the channel row `active` in the only accredited source and
the `vendor` capability projected by the deployment (default deployments project none) —
and the dossier is literal that today that object does not exist: "Hoje a superfície é
default-off, sem login real nem membership publicada — ou seja, hoje **não** publicado"
(dossier §"Objeto publicável", item "O que conta como 'publicado'").

The frozen C6-4 contract carries NO vendor command for any of it: the whole vendor
vocabulary of `contract-c6-4.json` is EIGHT literals — six `vendor_channel` enum values
(`:7148,:7637,:7911,:8390,:10167,:10606`), one `vendor_seat_refused` value (`:8467`) and
the work-audience note "vendors/customers have no work commands" (`:11069`) — zero of the
42 `x-commands` names a vendor operation, and all seven "membership" occurrences of the
text are staff/association/source semantics, never vendor membership (checked live and
pinned by test). The A10 matrix seeds are NON-binding (`mapping: at_admission`) — JR4,
OP13, OP15, NOTICE-VENDOR, HTTP-35, HTTP-36 — and the dossier fixes the queue-closing
dependency: "O consumo vendor allowlistado de OP01/OP02/OP08 movimenta linhas cuja
qualificação pertence às admissões das respectivas capacidades (A2/A1/A5)"; A10 is order
10 of the GAP-CATALOG §5 sequence and CLOSES the D-16 queue, moving no matrix line by
similarity.

That decision is an owner act — roles EXACTLY `engenharia origem` + `comercial` (A-INDEX/
GAP-CATALOG literal; Track B publishes the per-OP grants from the ORIGIN repository with
its own PRs/gates, the canal decision is the comercial's, and no leg derives from the
other's acceptance — mold A6/A7; Maezo domain changes never ride a portal PR) — archived
as an admission record and consumed here, never a code change, feature flag, environment
variable or process global. The dossier's decision fork is machine-checked exactly:

- **Forma-de-canal-publicado (`decision == "admitted"`):** REFUSED AT THIS PIN, always,
  with `admission_scope_invalid`. The admit form demands an EXACT scope (qual canal,
  quais principals/tasks por grant, qual tenant) over the PUBLISHED object plus a NEW
  origin snapshot — and the dossier is literal that the object is NOT published today and
  that admitting only "inicia o incremento P6.5 (bind por sucessor versionado; JR4
  permanece guard default-off até o bind qualificado)". A Forma-de-canal-publicado record
  against `d1d796b8…` is therefore scope-invalid BY CONSTRUCTION — a published object +
  new snapshot + a new record + a re-pinned loader is the only road, never a reused one.
- **Registered refusal (`decision == "refused"`, the dossier's literal decision value):**
  the ONLY state this parser can produce. The dossier's RECUSAR is a REGISTERED NO-GO
  ("o NO-GO persiste como recusa registrada: `A10-ADMISSION-RECORD.json` com decisão
  `refused`"), and its machine meaning is exactly the shipped state: the allowlist stays
  locked (`VENDOR_OPERATIONS` untouched; OP03/OP04 refused at the binding), JR4 stays
  absent from the surface with the flag off, `commercialProgress`/`attributionPolicy`
  stay `UNMEASURED` (the closed portal stub's own consts, mirrored below) and P6.5 stays
  NOT started. The attested scope is EXACTLY empty — any named command would claim a
  vendor-channel admission over a contract that carries none — and it changes no runtime
  behavior: the default-off substrate is the state, never a toggle awaiting the record.
- **`deferred`:** refuse as `admission_not_admitted` — the dossier's ADIAR is "nenhum
  registro; nenhum código muda; pendência explícita", so a deferred RECORD is not a
  contemplated state and never binds.

Structural, admission-independent invariants mirrored here from the dossier's guardanapo
(anti-padrão v3 §9.1 — "nenhum flag/adapter que simule membership/canal publicado"; JR4
permanece default-off; "membership/canal jamais são auto-provisionados por browser ou
agente") and from the CLOSED portal stub `vendor-projection.proposed.v1`
("Not a published Maezo source, not an admission, not a qualified matrix row";
`additionalProperties: false` over EXACTLY twelve properties, three `const`s beyond the
family marker — `stageSemantics` `PIPELINE_POSITION_ONLY_NOT_ACCEPTANCE`,
`commercialProgress` `UNMEASURED`, `attributionPolicy` `UNMEASURED` — and a nested closed
channel object over EXACTLY `producerRef`/`channelClass`/`attributionBasisRef` where
"Null means no admitted attribution basis is published; it is never a guessed or zeroed
basis", the maezo-side typed mirror — proven without any record):
`refuse_vendor_projection_payload` refuses any payload that FABRICATES the channel
surface — a payload naming the stub family must carry EXACTLY the stub's twelve fields
with EXACTLY the three consts and the stub's declared member types, with the channel
object closed over its three members and absent channel attributes never zeroed (the
stub's own "$comment": "absent attributes are the literal 'unknown', never zero, never
invented"; the projection's `unknown_is_never_zero` validator is the same clause); and
independently of the family marker, a stray `commercialProgress`/`attributionPolicy`
field (the two names the dossier's RECUSAR fixes — "commercialProgress/attributionPolicy
permanecem UNMEASURED") with ANY value other than the stub's `UNMEASURED` const refuses:
those names have NO pre-successor value but the stub's own const, so a measured progress
or a decided canal policy binds only through a successor versioned schema, never by
re-declaring the fields. The guard holds with or without an admission (the A6 lesson:
the shape guard IS the refusal).

The record field set is transcribed, not invented: objeto + URL + SHA256 + timestamp
UTC + identidade dos owners + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`,
seção A10 e §5). The record must cite the frozen C6-4 contract pin below — a divergent
sha is a loud refusal, because a successor contract requires a new freeze and a new
record, never a reused one.

No record ships in this repository and none is defaulted anywhere: absence keeps the
vendor channel closed, which is the shipped state (PARTIAL substrate, default-off — JR4
absent, route unbound, store empty-unknown). Decision fields are owner-only (ADR-0007);
agents never publish grants, membership or canal state, and never provision the channel
by browser or automation. Every call re-reads and re-validates its record ("valida o sha
do pin em toda leitura" — GAP-CATALOG-A1-A10.md, admission A10); there is no cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A10_ADMISSION_ID: Final = "A10"
# Same closed record family as every A1–A10 admission (schema shared with A1–A9); each
# admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
# A-INDEX literal `unlocks` (the dossier's Identidade spells the same unlock as
# "P6.5 (integração real da superfície vendor S10)").
A10_UNLOCKS: Final = "P6.5"
# The PUBLISHED object is the gap and the registered refusal admits EXACTLY nothing: the
# frozen contract carries zero vendor command (eight non-surface vendor literals, pinned)
# and the allowlisted OP01/OP02/OP08 consumption takes its qualification from A2/A1/A5,
# never from this record.
A10_ADMITTED_COMMANDS: Final[frozenset[str]] = frozenset()
# A-INDEX/GAP-CATALOG literal roles: `owners: ["engenharia origem", "comercial"]`. The
# pair is EXACT — Track B (grants per OP, origin repository, own PRs/gates) is the
# engenharia-origem leg and the canal decision is the comercial leg, and no leg derives
# from the other's acceptance (mold A6/A7); a one-legged record never parses.
A10_OWNER_ROLES: Final = frozenset({"engenharia origem", "comercial"})
# The dossier's literal decision value for the registered NO-GO (dossiê A10 §"Opções de
# decisão", RECUSAR: "o NO-GO persiste como recusa registrada … com decisão `refused`").
# It is the ONLY state this parser can produce; the Forma-de-canal-publicado (`admitted`)
# is scope-invalid at this pin by construction (module docstring).
A10_REGISTERED_REFUSAL_DECISION: Final = "refused"
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the A1–A9 pins and
# the codec pin `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A10_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

# The closed portal stub `vendor-projection.proposed.v1` (`omni_sales_platform/
# src/contracts/schemas/vendor-projection.proposed.v1.json`), transcribed — not invented:
# `additionalProperties: false` over EXACTLY these twelve properties, three of which are
# `const`s beyond the family marker. The stub is a PROPOSED placeholder — "Not a published
# Maezo source, not an admission, not a qualified matrix row"; "progress and attribution
# are explicitly UNMEASURED and never fabricated"; its evolution is successor-versioned only.
VENDOR_PROJECTION_STUB_SCHEMA: Final = "vendor-projection.proposed.v1"
VENDOR_PROJECTION_STUB_FIELDS: Final = frozenset(
    {
        "schemaVersion",
        "vendorRef",
        "journeyRef",
        "opportunityCommandRefs",
        "pipelineStage",
        "stageSemantics",
        "pipelineRevision",
        "channel",
        "commercialProgress",
        "attributionPolicy",
        "sourcePublicationReceiptRef",
        "projectedAt",
    }
)
VENDOR_PROJECTION_STUB_STAGE_SEMANTICS: Final = "PIPELINE_POSITION_ONLY_NOT_ACCEPTANCE"
VENDOR_PROJECTION_STUB_UNMEASURED: Final = "UNMEASURED"
# The nested closed channel object (`additionalProperties: false` over EXACTLY these three
# members; `attributionBasisRef` is a non-empty string or null — "Null means no admitted
# attribution basis is published; it is never a guessed or zeroed basis").
VENDOR_PROJECTION_CHANNEL_FIELDS: Final = frozenset({"producerRef", "channelClass", "attributionBasisRef"})

AdmissionRefusalReason = Literal[
    "admission_record_invalid",
    "admission_not_admitted",
    "admission_owner_invalid",
    "admission_scope_invalid",
    "admission_pin_divergent",
    "admission_expired",
]

VendorProjectionPayloadRefusalReason = Literal[
    "vendor_projection_payload_not_object",
    "vendor_projection_stub_shape_invalid",
    "vendor_projection_stub_const_mutated",
    "vendor_projection_stub_type_invalid",
    "vendor_projection_channel_shape_invalid",
    "vendor_projection_channel_zero_fabricated",
    "vendor_commercial_progress_fabricated",
    "vendor_attribution_policy_fabricated",
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
# The stub's declared string members over the closed shape (`minLength: 1` in the stub;
# `projectedAt` is `type: string, format: date-time` — the timestamp FORMAT stays with the
# consuming schema, the stub-level promise checked here is the declared string type).
VENDOR_PROJECTION_STUB_STRING_FIELDS: Final = frozenset(
    {
        "vendorRef",
        "journeyRef",
        "pipelineStage",
        "pipelineRevision",
        "sourcePublicationReceiptRef",
        "projectedAt",
    }
)


class VendorAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


class VendorProjectionPayloadError(Exception):
    """Loud refusal of a payload that fabricates the vendor channel surface (anti-padrão v3 §9.1).

    Admission-independent by design: the stub's structural clauses hold before any record
    exists, while one is pending, and after a decision is registered.
    """

    def __init__(self, reason_code: VendorProjectionPayloadRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: VendorProjectionPayloadRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class VendorAdmission:
    """Validated A10 admission value — attests the REGISTERED REFUSAL; binds no command.

    The only state this parser can produce is the dossier's registered NO-GO
    (`decision == "refused"`): the Forma-de-canal-publicado is scope-invalid at this pin
    by construction, the attested scope is exactly empty and the substrate stays
    default-off with or without the record.
    """

    admission_id: Literal["A10"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> VendorAdmissionError:
    return VendorAdmissionError(reason_code)


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


def refuse_vendor_projection_payload(payload: object) -> None:
    """Refuse any payload that fabricates the vendor channel surface (anti-padrão v3 §9.1).

    The maezo-side typed mirror of the closed portal stub `vendor-projection.proposed.v1`
    (`additionalProperties: false` over exactly twelve fields, three consts, nested closed
    channel object): returns None ONLY for a JSON-object payload that fabricates nothing —
    a payload NOT naming the stub family and carrying no stray
    `commercialProgress`/`attributionPolicy` field (or carrying them exactly `UNMEASURED`)
    passes, while any of the following raises BEFORE any value could be displayed, stored
    or projected:

    - a payload naming the family (`schemaVersion == vendor-projection.proposed.v1`) that
      does not carry EXACTLY the stub's twelve fields (`vendor_projection_stub_shape_
      invalid` — the value `additionalProperties: false` forbids) or whose
      `stageSemantics`/`commercialProgress`/`attributionPolicy` consts diverge
      (`vendor_projection_stub_const_mutated` — a measured progress, a decided canal
      policy or an acceptance-asserting stage is fabricated: the stub is a PROPOSED
      placeholder, "not an admission, not a qualified matrix row") or whose declared
      string members / `opportunityCommandRefs` array diverge from the stub's types
      (`vendor_projection_stub_type_invalid`);
    - a family payload whose `channel` is not an object closed over EXACTLY
      `producerRef`/`channelClass`/`attributionBasisRef` with the declared member types
      (`vendor_projection_channel_shape_invalid`), or whose `producerRef`/`channelClass`
      is empty, whitespace or the literal `"0"` (`vendor_projection_channel_zero_
      fabricated` — "absent attributes are the literal 'unknown', never zero, never
      invented"; `attributionBasisRef` stays string-or-null, null = no admitted basis
      published, never guessed or zeroed);
    - ANY payload (family marker or not) carrying `commercialProgress` or
      `attributionPolicy` with a value other than `UNMEASURED`
      (`vendor_commercial_progress_fabricated`/`vendor_attribution_policy_fabricated`) —
      both names are fixed by the dossier's RECUSAR ("commercialProgress/attributionPolicy
      permanecem UNMEASURED") and their only pre-successor value is the stub's const: a
      measured/decided value binds only through a successor versioned schema, never by
      re-declaring these fields.

    Non-object payloads are not vendor-projection payload objects and refuse as well.
    This is admission-independent: it holds with no record on disk, exactly as it will
    hold with a registered refusal, because a channel surface fabricated under pendência
    is the same fabrication under any decision.
    """
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise VendorProjectionPayloadError("vendor_projection_payload_not_object")
    if payload.get("schemaVersion") == VENDOR_PROJECTION_STUB_SCHEMA:
        if set(payload) != set(VENDOR_PROJECTION_STUB_FIELDS):
            raise VendorProjectionPayloadError("vendor_projection_stub_shape_invalid")
        if (
            payload.get("stageSemantics") != VENDOR_PROJECTION_STUB_STAGE_SEMANTICS
            or payload.get("commercialProgress") != VENDOR_PROJECTION_STUB_UNMEASURED
            or payload.get("attributionPolicy") != VENDOR_PROJECTION_STUB_UNMEASURED
        ):
            raise VendorProjectionPayloadError("vendor_projection_stub_const_mutated")
        stub_strings_ok = all(
            isinstance(payload.get(field), str) and payload.get(field)
            for field in VENDOR_PROJECTION_STUB_STRING_FIELDS
        )
        if not stub_strings_ok:
            raise VendorProjectionPayloadError("vendor_projection_stub_type_invalid")
        refs = payload.get("opportunityCommandRefs")
        if not isinstance(refs, list) or not all(isinstance(item, str) and item for item in refs):
            raise VendorProjectionPayloadError("vendor_projection_stub_type_invalid")
        channel = payload.get("channel")
        if (
            not isinstance(channel, dict)
            or not all(isinstance(key, str) for key in channel)
            or set(channel) != set(VENDOR_PROJECTION_CHANNEL_FIELDS)
        ):
            raise VendorProjectionPayloadError("vendor_projection_channel_shape_invalid")
        for member in ("producerRef", "channelClass"):
            value = channel.get(member)
            if not isinstance(value, str) or not value.strip() or value.strip() == "0":
                raise VendorProjectionPayloadError("vendor_projection_channel_zero_fabricated")
        basis = channel.get("attributionBasisRef")
        if basis is not None and (not isinstance(basis, str) or not basis):
            raise VendorProjectionPayloadError("vendor_projection_channel_shape_invalid")
    if "commercialProgress" in payload and payload["commercialProgress"] != VENDOR_PROJECTION_STUB_UNMEASURED:
        raise VendorProjectionPayloadError("vendor_commercial_progress_fabricated")
    if "attributionPolicy" in payload and payload["attributionPolicy"] != VENDOR_PROJECTION_STUB_UNMEASURED:
        raise VendorProjectionPayloadError("vendor_attribution_policy_fabricated")


def parse_vendor_admission(record: bytes, *, now: datetime) -> VendorAdmission:
    """Validate admission record bytes against the frozen A10 admission.

    Every structural divergence refuses loudly; a `deferred` owner decision refuses as
    `admission_not_admitted` (the dossier's ADIAR is "nenhum registro"); a
    Forma-de-canal-publicado record (`decision == "admitted"`) refuses as
    `admission_scope_invalid` BY CONSTRUCTION at this pin (the published object — grants
    per OP, published membership, `active` channel row, projected `vendor` capability —
    does not exist, "hoje **não** publicado", and admitting binds "por sucessor
    versionado; JR4 permanece guard default-off até o bind qualificado"); the ONLY
    produced state is the registered refusal, which demands EXACTLY the
    `engenharia origem` + `comercial` owner pair and an EXACTLY empty admitted command
    set. `now` must be timezone-aware. This is a pure per-call validation: callers that
    read from disk re-read per call (`read_vendor_admission`), never cache.
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
        or value["admission_id"] != A10_ADMISSION_ID
        or value["unlocks"] != A10_UNLOCKS
    ):
        raise _refuse("admission_record_invalid")
    decision = value["decision"]
    if decision not in ("admitted", A10_REGISTERED_REFUSAL_DECISION, "deferred"):
        raise _refuse("admission_record_invalid")
    if decision == "deferred":
        raise _refuse("admission_not_admitted")
    if decision == "admitted":
        # Forma-de-canal-publicado against THIS pin: scope-invalid by construction. The
        # exact scope the dossier demands (qual canal, quais principals/tasks por grant,
        # qual tenant) needs the PUBLISHED object plus a new origin snapshot, and the
        # dossier is literal ("hoje **não** publicado"; bind "por sucessor versionado; JR4
        # permanece guard default-off até o bind qualificado"). Published object + new
        # snapshot + new record + re-pinned loader is the only road; never a reused one.
        raise _refuse("admission_scope_invalid")
    owners = value["owners"]
    if (
        not isinstance(owners, list)
        or not owners
        or not all(isinstance(owner, str) for owner in owners)
        or len(set(owners)) != len(owners)
        # EXACTLY the A-INDEX pair — the Track B grants leg and the canal leg both sign,
        # and no third authority rides a registered refusal (mold A6/A7/A9, one leg each).
        or set(owners) != A10_OWNER_ROLES
    ):
        raise _refuse("admission_owner_invalid")
    commands = value["admitted_commands"]
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) for command in commands)
        # EXACTLY empty: the registered refusal admits no vendor surface, and any named
        # command would be a command the frozen contract does not carry (zero vendor
        # commands — pinned by test).
        or set(commands) != A10_ADMITTED_COMMANDS
    ):
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A10_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return VendorAdmission(
        admission_id="A10",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_vendor_admission(path: str | Path, *, now: datetime) -> VendorAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence of
    an admission, and callers keep the vendor channel closed (the shipped PARTIAL state:
    substrate default-off, JR4 absent, route unbound, store unknown) without inventing a
    default.
    """
    return parse_vendor_admission(Path(path).read_bytes(), now=now)
