"""Registered analytics admission A9 (NR-10 lifecycle/analytics) — the recorded act that binds.

The Sales-channel measurement dimension is fail-closed by ABSENCE, and the absence is
EXPLICIT in the frozen contract: C6-4 carries ZERO analytics command or event among the
42 `x-commands`, and "analytics" appears in the whole contract text EXACTLY TWICE, both
times inside a persistence PROHIBITION — intake "never financial journal, logs, analytics
or browser persistence" (`contract-c6-4.json:446`) and terms persistence "…logs,
analytics or browser persistence" (`:11016`); both literals end in "analytics or browser
persistence" (checked live and pinned by test). There is no measurement surface to bind,
no runtime seam to gate and no browser telemetry to authorize: the veredito is ABSENT
(`docs/delivery/commercial-platform/GAP-CATALOG-A1-A10.md`, admission A9 and §4) and
"UNMEASURED" is a world STATE, never a toggle ("métricas somente com A9 admitido;
ausência de medição continua UNMEASURED" — dossier A9, cross-map; "UNMEASURED permanece o
estado das métricas em P8" — dossier §"Opções de decisão"). The maezo-side name-neighbours
are all INTERNAL by kind and stay untouched: `platform/observability.py` and
`runtime/turn_telemetry.py` (internal observability), the A2A internal task types
`analytics.population`/`analytics.actuarial` (`tools/workers/pagto.py`,
`tools/workers/adequacao.py`, `runtime/agent_runtime/a2a_composition.py`,
`agents/andre/*` — k-anon aggregate dossiers of the operator's own actuarial/payment-risk
flows, SP-OP-PAGTO-001/SP-OP-ADEQUACAO-001), the AMH consent purpose vector in
`adapters/amh/mapping.py`, the QA workgroup string in `qa/simulador/roster.py` and the
gateway egress note (`portal/contracts/models.py` — "does not authorize browser storage,
analytics, logging or General-Zone egress … derive human/DPO identity"): NONE of them is
a Sales-channel NR-10 surface, and this loader imports none of them (closed tree set,
pinned by test).

That dimension's decision is an owner act — role EXACTLY `DPO`, single-legged
(A-INDEX/GAP-CATALOG literal `owners: ["DPO"]`; the dossier names the DPO as THE decider
of the finalidade/base/lifecycle/analytics contract and lists identity/source owners,
security and produto/analytics owner as COADJUVANTES — "nenhum nome é presumido" — and
coadjuvantes do not sign; A-INDEX `candidate_matrix_ids: []` because measurement is a
TRANSVERSAL gate over every capability, never a denominator interface) — archived as an
admission record and consumed here, never a code change, feature flag, environment
variable or process global. The dossier's decision fork is machine-checked exactly:

- **Forma-de-medir (`decision == "admitted"`):** REFUSED AT THIS PIN, always, with
  `admission_scope_invalid`. The admit form demands an EXACT scope (quais
  eventos/finalidades, qual precisão de timestamp, qual piso de célula, quais estudos)
  under a PUBLISHED lifecycle/analytics contract — and the dossier is literal that the
  contract is NOT published ("nenhum mecanismo de lifecycle/DSR foi qualificado e nenhuma
  base legal, prazo, piso de célula ou destino de analytics foi decidido — ou seja, hoje
  **não** publicado") and that enabling binds "por sucessor versionado — e somente aí P8
  pode medir". At this pin there is additionally NO surface for the scope to live on: the
  frozen contract carries analytics only as the two prohibitions above. A Forma-de-medir
  record against `d1d796b8…` is therefore scope-invalid BY CONSTRUCTION — a successor
  freeze + a new record + a re-pinned loader is the only road, never a reused one.
- **Registered refusal (`decision == "refused"`, the dossier's literal decision value):**
  the ONLY state this parser can produce. The dossier's RECUSAR is a REGISTERED NO-GO
  ("o NO-GO persiste como recusa registrada: `A9-ADMISSION-RECORD.json` com decisão
  `refused`"), and its machine meaning is exactly the shipped state: analytics remains
  absent — "ausência que não é flag" — nenhum evento, nenhum default, nenhum
  baseline/meta; the attested scope is EXACTLY empty (any named command would claim
  measurement over a pin that only prohibits it) and it changes no runtime behavior:
  UNMEASURED stands with or without the record.
- **`deferred`:** refuse as `admission_not_admitted` — the dossier's ADIAR is "nenhum
  registro; pendência explícita", so a deferred RECORD is not a contemplated state and
  never binds.

Structural, admission-independent invariants mirrored here from the dossier's
guardanapo (anti-padrão v3 §9.1 — "analytics permanece ausente (não é flag) até a
admissão — desligado é um estado, não um toggle à espera"; "nenhum payload pessoal em
URL/DOM/storage/logs") and from the CLOSED portal stub `measurement.proposed.v1`
("isolated synthetic measurement example only … not an admitted analytics payload … No
collection, identity, real observations, measured baseline, transport or event recorder.
No legal basis, retention, cell floor or purpose policy is invented", the maezo-side
typed mirror — proven without any record): `refuse_measurement_payload` refuses any
payload that FABRICATES measurement — a payload naming the stub family must carry
EXACTLY the stub's fourteen fields with EXACTLY the three consts (`schemaVersion`
marker, `status` `PROPOSED`, `track` `SYNTHETIC_EXAMPLE_ONLY`) and with `tenant`/`journey`
inside the stub's closed SYNTHETIC enums (a real tenant/journey in a synthetic-only stub
is a fabricated real observation); and independently of the family marker, a stray
`journey_measurement_key`/`dedupe_key`/`variant_id` field (names transcribed from the
dossier's ADMITTED-analytics minimum envelope — "schema_version, event_name allowlisted,
stage, track, occurred_at …, journey_measurement_key efêmera, tenant isolado pelo
serviço, dedupe_key, variant_id somente em estudo autorizado …") with ANY value refuses:
those three names have NO pre-successor value at all (the envelope's
retenção/eliminação/owners/consentimento/piso de célula is "hoje OPEN, sem defaults
inventados"), so an admitted analytics envelope binds only through a successor versioned
contract, never by re-declaring its fields. The guard holds with or without an admission
(the A6 lesson: the shape guard IS the refusal).

The record field set is transcribed, not invented: objeto + URL + SHA256 + timestamp
UTC + identidade do owner + decisão + escopo + expiração (`GAP-CATALOG-A1-A10.md`,
seção A9 e §5). The record must cite the frozen C6-4 contract pin below — a divergent
sha is a loud refusal, because a successor contract requires a new freeze and a new
record, never a reused one. The TRANSVERSAL P8 measurement gate ("vem depois dos
incrementos de integração e ANTES de qualquer P8 medir" — GAP-CATALOG §5, line 9) is a
declared FUTURE package and a human leg: this module erects no gate because there is no
measurement to gate — the loader and the payload guard are the whole mechanical foot.

No record ships in this repository and none is defaulted anywhere: absence keeps the
measurement dimension closed, which is the shipped state (ABSENT — nothing measures,
nothing emits, UNMEASURED). Decision fields are owner-only (ADR-0007); agents never
author them, never a base legal, prazo, piso de célula or analytics destination. Every
call re-reads and re-validates its record ("valida o sha do pin em toda leitura" —
GAP-CATALOG-A1-A10.md, admission A9); there is no cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, cast

A9_ADMISSION_ID: Final = "A9"
# Same closed record family as every A1–A10 admission (schema shared with A1–A8); each
# admission validates its own exact field set.
ADMISSION_RECORD_SCHEMA: Final = "omni-commercial-admission-record.v1"
A9_UNLOCKS: Final = "métricas/eventos (decisão)"
# The ABSENCE is the gap and the admitted scope is EXACTLY empty: analytics is a
# TRANSVERSAL gate (A-INDEX `candidate_matrix_ids: []`), the frozen contract carries zero
# analytics command/event and names analytics only in its two persistence prohibitions —
# any named command would claim measurement over a pin that only prohibits it.
A9_ADMITTED_COMMANDS: Final[frozenset[str]] = frozenset()
# A-INDEX/GAP-CATALOG literal role: `owners: ["DPO"]`. The role set is EXACT and SINGLE —
# the dossier makes the DPO the decider and demotes every other NR-10 actor to coadjuvante
# ("nenhum nome é presumido"); a record signed by any other set never parses.
A9_OWNER_ROLES: Final = frozenset({"DPO"})
# The dossier's literal decision value for the registered NO-GO (dossiê A9 §"Opções de
# decisão", RECUSAR: "o NO-GO persiste como recusa registrada … com decisão `refused`").
# It is the ONLY state this parser can produce; the Forma-de-medir (`admitted`) is
# scope-invalid at this pin by construction (module docstring).
A9_REGISTERED_REFUSAL_DECISION: Final = "refused"
# CONTRACT-FREEZE-C6-4 (`CONTRACT-FREEZE-C6-4-REGISTER.json`), the sha256 of
# `tests/candidate_artifacts/commercial/contract-c6-4.json`. Equal to the A1–A8 pins and
# the codec pin `adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`; pinned equal by test.
A9_CONTRACT_SHA256: Final = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"

# The closed portal stub `measurement.proposed.v1` (`omni_sales_platform/
# src/contracts/schemas/measurement.proposed.v1.json`), transcribed — not invented:
# "isolated synthetic measurement example only", `additionalProperties: false` over
# EXACTLY these fourteen properties, three of which are `const`s and two more closed to
# SYNTHETIC enum values. The stub is a PROPOSED metric dictionary, disabled: "not an
# admitted analytics payload … No collection, identity, real observations, measured
# baseline, transport or event recorder. No legal basis, retention, cell floor or purpose
# policy is invented"; its evolution is successor-versioned only.
MEASUREMENT_STUB_SCHEMA: Final = "measurement.proposed.v1"
MEASUREMENT_STUB_FIELDS: Final = frozenset(
    {
        "schemaVersion",
        "status",
        "track",
        "tenant",
        "journey",
        "event",
        "elapsedMilliseconds",
        "stage",
        "lifecycleStage",
        "fieldCode",
        "validationCode",
        "attemptId",
        "attemptNumber",
        "rubric",
    }
)
MEASUREMENT_STUB_STATUS: Final = "PROPOSED"
MEASUREMENT_STUB_TRACK: Final = "SYNTHETIC_EXAMPLE_ONLY"
MEASUREMENT_STUB_TENANTS: Final = frozenset({"synthetic-tenant-a", "synthetic-tenant-b"})
MEASUREMENT_STUB_JOURNEYS: Final = frozenset({"synthetic-journey-a", "synthetic-journey-b"})

# The three envelope names of the dossier's ADMITTED-analytics minimum contract that have
# NO pre-successor value anywhere ("retenção/eliminação/owners/consentimento/piso de
# célula definidos (hoje OPEN, sem defaults inventados)"): carrying any of them with any
# value fabricates the admitted envelope. `event_name`/`occurred_at` are deliberately NOT
# stray-guarded (generic names); payloads OF the family are fully covered by the closed
# family path above.
A9_JOURNEY_MEASUREMENT_KEY_FIELD: Final = "journey_measurement_key"
A9_DEDUPE_KEY_FIELD: Final = "dedupe_key"
A9_VARIANT_ID_FIELD: Final = "variant_id"

AdmissionRefusalReason = Literal[
    "admission_record_invalid",
    "admission_not_admitted",
    "admission_owner_invalid",
    "admission_scope_invalid",
    "admission_pin_divergent",
    "admission_expired",
]

MeasurementPayloadRefusalReason = Literal[
    "measurement_payload_not_object",
    "measurement_stub_shape_invalid",
    "measurement_stub_const_mutated",
    "measurement_stub_not_synthetic",
    "measurement_journey_key_fabricated",
    "measurement_dedupe_key_fabricated",
    "measurement_variant_id_fabricated",
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


class AnalyticsAdmissionError(Exception):
    """Loud refusal of an admission record; a refused record never binds anything."""

    def __init__(self, reason_code: AdmissionRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: AdmissionRefusalReason = reason_code


class MeasurementPayloadError(Exception):
    """Loud refusal of a payload that fabricates NR-10 measurement (anti-padrão v3 §9.1).

    Admission-independent by design: the stub's structural clauses hold before any
    record exists, while one is pending, and after a decision is registered.
    """

    def __init__(self, reason_code: MeasurementPayloadRefusalReason) -> None:
        super().__init__(reason_code)
        self.reason_code: MeasurementPayloadRefusalReason = reason_code


@dataclass(frozen=True, slots=True)
class AnalyticsAdmission:
    """Validated A9 admission value — attests the REGISTERED REFUSAL; binds no command.

    The only state this parser can produce is the dossier's registered NO-GO
    (`decision == "refused"`): the measurement form is scope-invalid at this pin by
    construction, the attested scope is exactly empty and the world state stays
    UNMEASURED with or without the record.
    """

    admission_id: Literal["A9"]
    contract_sha256: str
    owners: tuple[str, ...]
    decided_at: datetime
    valid_until: datetime
    object_url: str
    object_sha256: str
    origin_git_sha: str


def _refuse(reason_code: AdmissionRefusalReason) -> AnalyticsAdmissionError:
    return AnalyticsAdmissionError(reason_code)


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


def refuse_measurement_payload(payload: object) -> None:
    """Refuse any payload that fabricates NR-10 measurement (anti-padrão v3 §9.1).

    The maezo-side typed mirror of the closed portal stub `measurement.proposed.v1`
    (`additionalProperties: false` over exactly fourteen fields, three consts, synthetic-
    only identity enums): returns None ONLY for a JSON-object payload that fabricates
    nothing — a payload NOT naming the stub family and carrying no stray
    `journey_measurement_key`/`dedupe_key`/`variant_id` field passes (unrelated payloads
    are not measurement payloads), while any of the following raises BEFORE any value
    could be displayed, stored or emitted:

    - a payload naming the family (`schemaVersion == measurement.proposed.v1`) that does
      not carry EXACTLY the stub's fourteen fields (`measurement_stub_shape_invalid` —
      the value `additionalProperties: false` forbids) or whose `status`/`track` consts
      diverge (`measurement_stub_const_mutated` — an admitted/collected claim: the stub
      is PROPOSED, disabled and "not an admitted analytics payload") or whose
      `tenant`/`journey` fall outside the closed SYNTHETIC enums
      (`measurement_stub_not_synthetic` — "No collection, identity, real observations":
      a real tenant/journey in a synthetic-only dictionary is a fabricated observation);
    - ANY payload (family marker or not) carrying `journey_measurement_key`,
      `dedupe_key` or `variant_id` with any value
      (`measurement_journey_key_fabricated`/`measurement_dedupe_key_fabricated`/
      `measurement_variant_id_fabricated`) — all three names are transcribed from the
      dossier's admitted-analytics envelope and have no pre-successor value ("hoje OPEN,
      sem defaults inventados"): an admitted envelope binds only through a successor
      versioned contract, never by re-declaring its fields.

    Non-object payloads are not measurement payload objects and refuse as well.
    This is admission-independent: it holds with no record on disk, exactly as it will
    hold with a registered refusal, because measurement fabricated under pendência is
    the same fabrication under any decision.
    """
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise MeasurementPayloadError("measurement_payload_not_object")
    if payload.get("schemaVersion") == MEASUREMENT_STUB_SCHEMA:
        if set(payload) != set(MEASUREMENT_STUB_FIELDS):
            raise MeasurementPayloadError("measurement_stub_shape_invalid")
        if payload.get("status") != MEASUREMENT_STUB_STATUS or payload.get("track") != MEASUREMENT_STUB_TRACK:
            raise MeasurementPayloadError("measurement_stub_const_mutated")
        tenants_ok = payload.get("tenant") in MEASUREMENT_STUB_TENANTS
        journeys_ok = payload.get("journey") in MEASUREMENT_STUB_JOURNEYS
        if not tenants_ok or not journeys_ok:
            raise MeasurementPayloadError("measurement_stub_not_synthetic")
    if A9_JOURNEY_MEASUREMENT_KEY_FIELD in payload:
        raise MeasurementPayloadError("measurement_journey_key_fabricated")
    if A9_DEDUPE_KEY_FIELD in payload:
        raise MeasurementPayloadError("measurement_dedupe_key_fabricated")
    if A9_VARIANT_ID_FIELD in payload:
        raise MeasurementPayloadError("measurement_variant_id_fabricated")


def parse_analytics_admission(record: bytes, *, now: datetime) -> AnalyticsAdmission:
    """Validate admission record bytes against the frozen A9 admission.

    Every structural divergence refuses loudly; a `deferred` owner decision refuses as
    `admission_not_admitted` (the dossier's ADIAR is "nenhum registro"); a Forma-de-medir
    record (`decision == "admitted"`) refuses as `admission_scope_invalid` BY
    CONSTRUCTION at this pin (the published lifecycle/analytics contract does not exist,
    enabling binds "por sucessor versionado — e somente aí P8 pode medir", and the frozen
    contract carries analytics only as its two persistence prohibitions); the ONLY
    produced state is the registered refusal, which demands EXACTLY the single `DPO`
    owner role and an EXACTLY empty admitted command set. `now` must be timezone-aware.
    This is a pure per-call validation: callers that read from disk re-read per call
    (`read_analytics_admission`), never cache.
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
        or value["admission_id"] != A9_ADMISSION_ID
        or value["unlocks"] != A9_UNLOCKS
    ):
        raise _refuse("admission_record_invalid")
    decision = value["decision"]
    if decision not in ("admitted", A9_REGISTERED_REFUSAL_DECISION, "deferred"):
        raise _refuse("admission_record_invalid")
    if decision == "deferred":
        raise _refuse("admission_not_admitted")
    if decision == "admitted":
        # Forma-de-medir against THIS pin: scope-invalid by construction. The exact scope
        # the dossier demands (quais eventos/finalidades, precisão de timestamp, piso de
        # célula, quais estudos) needs a PUBLISHED lifecycle/analytics contract with a
        # qualified mechanism ("hoje não publicado" — dossier literal) and binds "por
        # sucessor versionado — e somente aí P8 pode medir". A successor freeze + new
        # record + re-pinned loader is the only road; never a reused one.
        raise _refuse("admission_scope_invalid")
    owners = value["owners"]
    if (
        not isinstance(owners, list)
        or not owners
        or not all(isinstance(owner, str) for owner in owners)
        or len(set(owners)) != len(owners)
        # EXACTLY the A-INDEX single role — the DPO decides, coadjuvantes do not sign,
        # and no second authority rides a registered refusal (mold A6/A7/A8, one leg).
        or set(owners) != A9_OWNER_ROLES
    ):
        raise _refuse("admission_owner_invalid")
    commands = value["admitted_commands"]
    if (
        not isinstance(commands, list)
        or not all(isinstance(command, str) for command in commands)
        # EXACTLY empty: the registered refusal admits no measurement of anything, and
        # any named command would be measurement over a pin that only prohibits it.
        or set(commands) != A9_ADMITTED_COMMANDS
    ):
        raise _refuse("admission_scope_invalid")
    decided_at = _aware(value["decided_at"])
    valid_until = _aware(value["valid_until"])
    if decided_at > now:
        raise _refuse("admission_record_invalid")
    if valid_until <= now:
        raise _refuse("admission_expired")
    pin = _string_fields(value["contract_pin"], _PIN_FIELDS)
    if pin["sha256"] != A9_CONTRACT_SHA256:
        raise _refuse("admission_pin_divergent")
    evidence = _string_fields(value["evidence"], _EVIDENCE_FIELDS)
    if (
        not evidence["object_url"].startswith("https://")
        or any(character.isspace() for character in evidence["object_url"])
        or not _hexed(evidence["object_sha256"], 64)
        or not _hexed(evidence["origin_git_sha"], 40)
    ):
        raise _refuse("admission_record_invalid")
    return AnalyticsAdmission(
        admission_id="A9",
        contract_sha256=pin["sha256"],
        owners=tuple(owners),
        decided_at=decided_at,
        valid_until=valid_until,
        object_url=evidence["object_url"],
        object_sha256=evidence["object_sha256"],
        origin_git_sha=evidence["origin_git_sha"],
    )


def read_analytics_admission(path: str | Path, *, now: datetime) -> AnalyticsAdmission:
    """Read + validate a record file with no caching — every call re-reads it.

    A missing file raises `FileNotFoundError`: that is not a refusal but the absence of
    an admission, and callers keep the measurement dimension closed (the shipped ABSENT
    state: nothing measures, nothing emits, UNMEASURED) without inventing a default.
    """
    return parse_analytics_admission(Path(path).read_bytes(), now=now)
