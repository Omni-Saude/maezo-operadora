"""A10 (vendor membership/canal, P6.5) registered vendor-channel admission — negative tests (D-16).

Executable regression for the planned negatives of admission A10 (GAP-CATALOG-A1-A10 §2,
transcribed from the pending dossier; the dossier stays OUT of `src/**` — A-INDEX rule 1,
pinned here by name) — the LAST admission of the D-16 queue (GAP-CATALOG §5, order 10):
(a) without A10 admitted, JR4 stays absent from the surface: `VendorJourneyConfig.enabled`
    defaults `False` ("`enabled` is the flag"), `vendor_journey`/`opportunity_projection`
    build NOTHING while the flag is off, the OP01/OP02/OP08 allowlist
    (`VENDOR_OPERATIONS`) stays exactly the registry intent set with OP03/OP04 refused at
    the binding before the driver ever sees them, and the OP16 submission route stays
    DOCUMENTED but unbound (403 capability / 503 unbound slot — `app.py` comment
    byte-intact; the GAP-CATALOG citation `app.py:335-338` refalls at `:342-346` on the
    tip, text byte-identical — lineage drift of packages after the catalog, disclosed);
(b) a vendor channel trying the seats it does not hold is refused `PURPOSE_DENIED` at the
    binding with ZERO downstream effect (only OP01+OP02 ever run);
(c) the OP16 machine stays fail-closed on the published-object ladder: a revoked membership
    or a binding that does not name the commanded channel is `AUTHORITY_UNPROVEN`; an
    absent channel store is `SOURCE_UNAVAILABLE` (UNKNOWN, never zero); a revoked channel
    row is `CHANNEL_STATUS_INELIGIBLE` — and the OP13 cadence publishes nothing unwired;
(d) the registered refusal (`refused`, the dossier's literal decision value — "o NO-GO
    persiste como recusa registrada") is the ONLY state the parser produces; the
    Forma-de-canal-publicado record refuses `admission_scope_invalid` BY CONSTRUCTION at
    this pin (the published object — grants per OP, published membership, `active` channel
    row, projected `vendor` capability — does not exist: "hoje **não** publicado"; bind
    "por sucessor versionado; JR4 permanece guard default-off até o bind qualificado");
    the owner pair is EXACTLY `engenharia origem` + `comercial` (no leg derives from the
    other — mold A6/A7); deferred never binds; the frozen contract carries ZERO vendor
    command (EIGHT vendor literals — six `vendor_channel` enum values, one
    `vendor_seat_refused`, the work-audience note — and seven "membership" occurrences,
    all staff/association/source semantics, never vendor membership).
Plus the closed portal stub `vendor-projection.proposed.v1` typed mirror
(`refuse_vendor_projection_payload`): `additionalProperties: false` over EXACTLY twelve
fields, three consts beyond the family marker and the nested closed channel object — a
payload naming the family with a mutated const, a shape/type divergence or a zeroed
channel attribute refuses, and stray `commercialProgress`/`attributionPolicy` fields (the
two names the dossier's RECUSAR fixes: "permanecem UNMEASURED") refuse with any value
other than `UNMEASURED`, family marker or not — admission-INDEPENDENT. And the A1–A9
records never open the vendor channel while the A10 record never opens theirs (both
directions, cross-parser); the loader re-validates on every read; a missing file is
absence (`FileNotFoundError`), never a refusal; and `commercial_admission.py` is
structurally untouched (A10's surface is the default-off vendor plane, never a C6-4
command — mold R8–R11; the matrix seeds JR4/OP13/OP15/NOTICE-VENDOR/HTTP-35/HTTP-36 are
non-binding, `mapping: at_admission` — A10 moves no line by similarity).
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.capabilities.models import CapabilityRefusalReason
from maezo.gateway.capabilities.vendor_journey import (
    CHANNEL_FORBIDDEN_SEAT_OPERATIONS,
    VENDOR_OPERATIONS,
    VENDOR_TASK_REF,
    VendorJourneyConfig,
    opportunity_projection,
    vendor_journey,
)
from maezo.gateway.commercial_acceptance_admission import (
    A3_CONTRACT_SHA256,
    parse_acceptance_record_admission,
)
from maezo.gateway.commercial_analytics_admission import A9_CONTRACT_SHA256, parse_analytics_admission
from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256, parse_catalog_source_admission
from maezo.gateway.commercial_commission_admission import A8_CONTRACT_SHA256, parse_commission_admission
from maezo.gateway.commercial_coverage_admission import A4_CONTRACT_SHA256, parse_coverage_read_admission
from maezo.gateway.commercial_portability_admission import A7_CONTRACT_SHA256, parse_portability_admission
from maezo.gateway.commercial_sales_admission import A2_CONTRACT_SHA256, parse_sales_session_admission
from maezo.gateway.commercial_support_admission import A5_CONTRACT_SHA256, parse_support_handoff_admission
from maezo.gateway.commercial_suppression_admission import A6_CONTRACT_SHA256, parse_suppression_admission
from maezo.gateway.commercial_vendor_admission import (
    A10_ADMITTED_COMMANDS,
    A10_CONTRACT_SHA256,
    A10_OWNER_ROLES,
    A10_REGISTERED_REFUSAL_DECISION,
    A10_UNLOCKS,
    VENDOR_PROJECTION_CHANNEL_FIELDS,
    VENDOR_PROJECTION_STUB_FIELDS,
    VENDOR_PROJECTION_STUB_SCHEMA,
    VENDOR_PROJECTION_STUB_STAGE_SEMANTICS,
    VENDOR_PROJECTION_STUB_UNMEASURED,
    VendorAdmissionError,
    VendorProjectionPayloadError,
    parse_vendor_admission,
    read_vendor_admission,
    refuse_vendor_projection_payload,
)
from maezo.gateway.human.read_credentials import ReadRefusalError
from maezo.gateway.vendor_submissions import VendorSubmissionError, VendorSubmissionRefusal
from maezo.tools.workers.vendor_admin import VENDOR_PUBLICATION_TOPIC, make_vendor_publication_handler
from tests.unit.agents.test_administrative_journeys import observation
from tests.unit.gateway.capabilities.journeys.helpers import Step, turn
from tests.unit.gateway.capabilities.vendor_journey.test_composition_e2e import VENDOR_TERMINAL_VIA_OP08_WAIT
from tests.unit.gateway.capabilities.vendor_journey.vendor_helpers import vendor_parts
from tests.unit.gateway.test_vendor_submissions import (
    CHANNEL,
    TENANT,
    ChannelDirectory,
    command,
    machine,
    vendor_membership,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def a10_record(**changes):
    """A VALID registered-refusal record — the only state the A10 parser can produce."""
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A10",
            "decision": A10_REGISTERED_REFUSAL_DECISION,
            "owners": ["engenharia origem", "comercial"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": A10_UNLOCKS,
            "admitted_commands": [],
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A10_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a10-registered-refusal.json",
                "object_sha256": "c" * 64,
                "origin_git_sha": "d" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a10_admission():
    return parse_vendor_admission(a10_record(), now=NOW)


# ----------------------------------------------------------------------------------
# (molde) Loader — o par engenharia origem + comercial decide; a recusa registrada é o
# ÚNICO estado produzido
# ----------------------------------------------------------------------------------


def test_the_registered_refusal_record_parses_and_round_trips() -> None:
    admission = a10_admission()
    assert admission.admission_id == "A10"
    assert admission.contract_sha256 == A10_CONTRACT_SHA256
    assert set(admission.owners) == A10_OWNER_ROLES
    assert len(admission.owners) == 2
    assert admission.valid_until > NOW


def test_the_registered_refusal_is_the_only_produced_state() -> None:
    # Forma-de-canal-publicado (`decision == "admitted"`) against THIS pin: scope-invalid
    # BY CONSTRUCTION — even with the exact owner pair, valid timestamps and the exact
    # (empty) scope. The dossier is literal: the object of the admission (grants per OP,
    # published membership, `active` channel row, projected `vendor` capability) is NOT
    # published ("hoje **não** publicado") and admitting only initiates P6.5 "por
    # sucessor versionado; JR4 permanece guard default-off até o bind qualificado". No
    # owner wording can make an unpublished channel carry "escopo exato (qual canal,
    # quais principals/tasks por grant, qual tenant)".
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(decision="admitted"), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_forma_de_canal_publicado_refusal_is_decision_level_not_shape_level() -> None:
    # The Forma-de-canal-publicado refusal fires BEFORE owner validation on purpose: no
    # owner set can launder a published-channel record through this pin. A successor
    # package re-pins the loader; this one never grows an admitted branch by owner
    # request alone.
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(decision="admitted", owners=["comercial"]), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


@pytest.mark.parametrize(
    "decision",
    [
        "refused ",
        " refused",
        "REFUSED",
        "no_go",
        "admitted_ceiling_orientation_only",
        "admitted_out_of_scope",
        "admitted_measurement_off",
        "",
    ],
)
def test_foreign_decision_values_are_structural_refusals(decision: str) -> None:
    # The A7/A8/A9 admitted-state literals are FOREIGN here: each admission's decision
    # vocabulary is its own dossier's, never the family's.
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(decision=decision), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_a_deferred_record_never_binds() -> None:
    # The dossier's ADIAR is "nenhum registro; nenhum código muda; pendência explícita":
    # a deferred RECORD is not a contemplated state and never registers a limit by
    # omission.
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(decision="deferred"), now=NOW)
    assert exc.value.reason_code == "admission_not_admitted"


def test_a_record_missing_either_owner_leg_never_parses() -> None:
    # Track B (grants por OP, origem) is the engenharia-origem leg; the canal decision is
    # the comercial leg — neither derives from the other (mold A6/A7).
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(owners=[]), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        ["comercial"],
        ["engenharia origem"],
        ["Engenharia origem", "comercial"],
        ["engenharia origem", "Comercial"],
        ["engenharia origem", "comercial", "DPO"],
        ["engenharia origem", "comercial", "comercial"],
        ["produto", "comercial"],
        ["engenharia", "comercial"],
        ["owner (origem)", "comercial"],
        ["engenharia origem", "legal"],
        [7],
    ],
)
def test_foreign_or_partial_owner_sets_refuse(owners: object) -> None:
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(owners=owners), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


def test_the_owner_role_constant_is_exactly_the_a_index_pair() -> None:
    assert A10_OWNER_ROLES == frozenset(  # noqa: SIM300 — constant under test reads first
        {"engenharia origem", "comercial"}
    )


@pytest.mark.parametrize(
    "commands",
    [
        ["access.resolve"],
        ["offer.compose"],
        ["external_wait.settle"],
        ["acceptance.record"],
        ["enrollment.request"],
        ["offer.quote"],
        ["access.resolve", "offer.compose"],
        "catalog.get",
        [{"operation": "offer.compose"}],
    ],
)
def test_the_registered_refusal_admits_exactly_no_surface(commands: object) -> None:
    # Any named command would claim a vendor-channel admission over a contract that
    # carries none: the refusal's scope is EXACTLY empty (pinned below).
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(admitted_commands=commands), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_the_admitted_scope_constant_is_exactly_empty() -> None:
    assert frozenset() == A10_ADMITTED_COMMANDS
    assert len(A10_ADMITTED_COMMANDS) == 0


# ----------------------------------------------------------------------------------
# (d) O contrato congelado NÃO tem comando vendor — o vocabulário vendor são OITO
#     literais não-superfície e "membership" nunca é membership vendor
# ----------------------------------------------------------------------------------


def test_the_frozen_contract_has_zero_vendor_commands() -> None:
    contract = json.loads((Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_bytes())
    commands = set(contract["x-commands"])
    assert len(commands) == 42
    assert not any("vendor" in command.lower() for command in commands)
    assert not any("membership" in command.lower() for command in commands)
    assert A10_ADMITTED_COMMANDS.isdisjoint(commands)


def test_the_contract_names_vendor_exactly_eight_times_all_non_surface() -> None:
    # The literal transcription of GAP-CATALOG §2/A10(i): six `vendor_channel` enum
    # values (:7148,:7637,:7911,:8390,:10167,:10606), one `vendor_seat_refused` (:8467)
    # and the work-audience note (:11069). A ninth occurrence would be surface.
    raw = (Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_text()
    lines = [line for line in raw.splitlines() if "vendor" in line.lower()]
    assert len(lines) == 8
    assert sum('"vendor_channel"' in line for line in lines) == 6
    assert sum('"vendor_seat_refused"' in line for line in lines) == 1
    assert sum("vendors/customers have no work commands" in line for line in lines) == 1


def test_no_contract_membership_literal_is_a_vendor_membership() -> None:
    # All seven "membership" occurrences are staff/association/source semantics — none
    # names a vendor membership (the admission object lives in the origin, not here).
    raw = (Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_text()
    membership_lines = [line for line in raw.splitlines() if "membership" in line.lower()]
    assert len(membership_lines) == 7
    assert not any("vendor" in line.lower() for line in membership_lines)


def test_the_frozen_contract_keeps_the_owner_acts_not_established() -> None:
    contract = json.loads((Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_bytes())
    status = contract["x-status"]
    assert status["human_acts"] == "NOT_ESTABLISHED"
    assert status["tasy_write_homologation"] == "NOT_ESTABLISHED"
    assert status["sales_admission"] == "NOT_ESTABLISHED"
    assert status["production"] == "NO_GO"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update({"schema": "omni-commercial-admission-record.v2"}),
        lambda record: record.update({"admission_id": "A9"}),
        lambda record: record.update({"admission_id": "a10"}),
        lambda record: record.update({"unlocks": "P6.5 (integracao real da superficie vendor S10)"}),
        lambda record: record.update({"unlocks": "p6.5"}),
        lambda record: record.update({"unlocks": "P6.i"}),
        lambda record: record.pop("decision"),
        lambda record: record.pop("admitted_commands"),
        lambda record: record.update({"extra_field": "x"}),
    ],
    ids=[
        "schema",
        "foreign-id-a9",
        "id-case",
        "unlocks-verbose",
        "unlocks-case",
        "unlocks-p6i",
        "decision-missing",
        "scope-missing",
        "extra-field",
    ],
)
def test_structural_divergences_refuse_as_record_invalid(mutate) -> None:
    record = json.loads(a10_record())
    mutate(record)
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_pin_divergent_refuses_loud() -> None:
    record = json.loads(a10_record())
    record["contract_pin"]["sha256"] = "e" * 64
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


@pytest.mark.parametrize("field", ["contract_id", "revision", "sha256"])
def test_pin_fields_are_closed(field: str) -> None:
    record = json.loads(a10_record())
    record["contract_pin"].pop(field)
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_contract_pin_extra_field_refuses() -> None:
    record = json.loads(a10_record())
    record["contract_pin"]["note"] = "x"
    with pytest.raises(VendorAdmissionError):
        parse_vendor_admission(json.dumps(record).encode(), now=NOW)


def test_expired_record_refuses() -> None:
    record = json.loads(a10_record())
    record["valid_until"] = "2026-10-25T00:00:00+00:00"
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_expired"


def test_expiry_never_reopens_the_channel() -> None:
    # "Membership revogada/canal inativo invalidam a admissão" runs the OTHER way too: an
    # expired refusal refuses loudly AND keeps the world state — the channel stays closed
    # with or without a parseable record.
    record = json.loads(a10_record())
    record["valid_until"] = "2026-10-25T00:00:00+00:00"
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code != "admission_scope_invalid"


def test_decided_in_the_future_refuses() -> None:
    record = json.loads(a10_record())
    record["decided_at"] = "2026-10-26T00:00:00+00:00"
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [("decided_at", "2026-10-24T00:00:00"), ("valid_until", "2099-01-01T00:00:00")],
)
def test_naive_timestamps_never_admit(field: str, value: str) -> None:
    record = json.loads(a10_record())
    record[field] = value
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize("field", ["decided_at", "valid_until"])
def test_non_timestamp_refuses(field: str) -> None:
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(**{field: "not-a-timestamp"}), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_naive_now_is_a_caller_bug_not_a_refusal() -> None:
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_vendor_admission(a10_record(), now=datetime(2026, 10, 25, 12, 0))


@pytest.mark.parametrize(
    "evidence",
    [
        {"object_url": "http://amh.invalid/a10.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a10 ", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a10.json", "object_sha256": "c" * 63, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a10.json", "object_sha256": "g" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a10.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 39},
        {"object_url": "https://amh.invalid/a10.json", "object_sha256": "c" * 64, "origin_git_sha": ""},
    ],
)
def test_evidence_shape_is_closed(evidence: dict) -> None:
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(a10_record(evidence=evidence), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_invalid_json_and_non_object_refuse() -> None:
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(b"{not json", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(b"[1,2]", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_missing_file_is_absence_not_refusal() -> None:
    with pytest.raises(FileNotFoundError):
        read_vendor_admission("/nonexistent/a10-admission-record.json", now=NOW)


def test_loader_revalidates_on_every_read_never_caches(tmp_path: Path) -> None:
    path = tmp_path / "A10-ADMISSION-RECORD.json"
    path.write_bytes(a10_record())
    assert read_vendor_admission(path, now=NOW).admission_id == "A10"
    record = json.loads(a10_record())
    record["contract_pin"]["sha256"] = "e" * 64
    path.write_bytes(json.dumps(record).encode())
    with pytest.raises(VendorAdmissionError) as exc:
        read_vendor_admission(path, now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


def test_pin_family_is_one_freeze_across_a1_a10_and_the_codec() -> None:
    from maezo.adapters.amh.commercial import CANDIDATE_SCHEMA_SHA256

    assert A10_CONTRACT_SHA256 == A1_CONTRACT_SHA256 == A2_CONTRACT_SHA256
    assert A10_CONTRACT_SHA256 == A3_CONTRACT_SHA256 == A4_CONTRACT_SHA256
    assert A10_CONTRACT_SHA256 == A5_CONTRACT_SHA256 == A6_CONTRACT_SHA256
    assert A10_CONTRACT_SHA256 == A7_CONTRACT_SHA256 == A8_CONTRACT_SHA256
    assert A10_CONTRACT_SHA256 == A9_CONTRACT_SHA256
    assert A10_CONTRACT_SHA256 == CANDIDATE_SCHEMA_SHA256


# ----------------------------------------------------------------------------------
# Independência A1–A10 nos dois sentidos (cross-parser) + superfície comercial intocada
# ----------------------------------------------------------------------------------


def _a1_to_a9_records():
    from tests.unit.test_commercial_admission_a1 import a1_record
    from tests.unit.test_commercial_admission_a2 import a2_record
    from tests.unit.test_commercial_admission_a3 import a3_record
    from tests.unit.test_commercial_admission_a4 import a4_record
    from tests.unit.test_commercial_admission_a5 import a5_record
    from tests.unit.test_commercial_admission_a6 import a6_record
    from tests.unit.test_commercial_admission_a7 import a7_record
    from tests.unit.test_commercial_admission_a8 import a8_record
    from tests.unit.test_commercial_admission_a9 import a9_record

    return [
        ("A1", a1_record()),
        ("A2", a2_record()),
        ("A3", a3_record()),
        ("A4", a4_record()),
        ("A5", a5_record()),
        ("A6", a6_record()),
        ("A7", a7_record()),
        ("A8", a8_record()),
        ("A9", a9_record()),
    ]


@pytest.mark.parametrize(("admission_id", "record_bytes"), _a1_to_a9_records())
def test_a1_a9_records_never_open_the_vendor_channel(admission_id: str, record_bytes: bytes) -> None:
    with pytest.raises(VendorAdmissionError) as exc:
        parse_vendor_admission(record_bytes, now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"
    del admission_id  # the family id is exactly what the record's own admission_id field carries


@pytest.mark.parametrize(
    "parse",
    [
        lambda record: parse_catalog_source_admission(record, now=NOW),
        lambda record: parse_sales_session_admission(record, now=NOW),
        lambda record: parse_acceptance_record_admission(record, now=NOW),
        lambda record: parse_coverage_read_admission(record, now=NOW),
        lambda record: parse_support_handoff_admission(record, now=NOW),
        lambda record: parse_suppression_admission(record, now=NOW),
        lambda record: parse_portability_admission(record, now=NOW),
        lambda record: parse_commission_admission(record, now=NOW),
        lambda record: parse_analytics_admission(record, now=NOW),
    ],
    ids=["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"],
)
def test_the_a10_record_never_opens_a1_a9(parse) -> None:
    # The registered-refusal value is not even in the A1–A9 decision vocabularies: a
    # foreign parser refuses it structurally, never by admission.
    with pytest.raises(Exception) as exc:
        parse(a10_record())
    assert type(exc.value).__name__.endswith("AdmissionError")
    assert not isinstance(exc.value, VendorAdmissionError)


def test_the_commercial_composition_has_no_a10_surface() -> None:
    # A10's surface is the default-off vendor plane, never a C6-4 command or a runtime
    # seam of the commercial composition: the composition grows no parameter for it (zero
    # regression on the A1–A9 gate matrix by construction).
    from maezo.gateway.commercial_admission import (
        compose_commercial_gateway,
        compose_public_catalog_gateway,
    )

    for compose in (compose_commercial_gateway, compose_public_catalog_gateway):
        parameters = set(inspect.signature(compose).parameters)
        assert parameters.isdisjoint({"vendor_admission", "vendor_channel_admission", "membership_admission"})


# ----------------------------------------------------------------------------------
# (a) Default-off É o estado — nenhum registro no repositório, JR4 ausente da superfície,
#     allowlist travada, dossiê pendente nunca em src/**
# ----------------------------------------------------------------------------------


def test_no_a10_admission_record_ships_in_the_repository() -> None:
    root = Path(__file__).resolve().parents[2]
    for base in ("docs", "src", "config", "tests", "spec"):
        assert not list((root / base).rglob("A10-ADMISSION-RECORD.json")), base


def test_the_pending_dossier_never_appears_in_src() -> None:
    # A-INDEX rule 1: "Dossiê status pending nunca aparece em código (src/**)".
    root = Path(__file__).resolve().parents[2]
    for path in (root / "src" / "maezo").rglob("*.py"):
        assert "A10-VENDOR-MEMBERSHIP" not in path.read_text(errors="replace"), path


def test_the_allowlist_stays_exactly_the_registry_intent_set() -> None:
    # The locked allowlist (dossier RECUSAR: "a allowlist vendor permanece travada") and
    # the seats the channel does not hold — OP03/OP04 refused at the binding before the
    # driver ever sees them. The OP01/OP02/OP08 consumption takes its qualification from
    # A2/A1/A5, never from an A10 record.
    assert VENDOR_TASK_REF == "journey.compras.step"
    assert VENDOR_OPERATIONS == frozenset(  # noqa: SIM300 — constant under test reads first
        {"access.resolve", "offer.compose", "external_wait.settle"}
    )
    assert CHANNEL_FORBIDDEN_SEAT_OPERATIONS == frozenset(  # noqa: SIM300 — constant under test reads first
        {"acceptance.record", "enrollment.request"}
    )
    assert CHANNEL_FORBIDDEN_SEAT_OPERATIONS.isdisjoint(VENDOR_OPERATIONS)


def test_the_vendor_config_defaults_off_by_construction() -> None:
    config = VendorJourneyConfig(
        tenant_ref="unit-tenant",
        legal_entity_ref="unit-legal",
        environment_ref="unit-environment",
        principal_ref="unit-vendor-channel",
        data_policy_ref="unit-data-policy",
        journey_ref="unit-journey",
        topology_contract_ref="unit-topology-contract",
        topology_version_ref="unit-topology-version",
        source_transition_contract_ref="unit-transition-contract",
    )
    assert config.enabled is False


def test_flag_off_builds_zero_vendor_surface_even_with_every_port_named() -> None:
    # (a) without A10 admitted, JR4 is absent from the surface: the flag-off short-circuit
    # precedes the qualified-port requirement, so NO driver/projection object exists.
    config = VendorJourneyConfig(
        tenant_ref="unit-tenant",
        legal_entity_ref="unit-legal",
        environment_ref="unit-environment",
        principal_ref="unit-vendor-channel",
        data_policy_ref="unit-data-policy",
        journey_ref="unit-journey",
        topology_contract_ref="unit-topology-contract",
        topology_version_ref="unit-topology-version",
        source_transition_contract_ref="unit-transition-contract",
    )
    assert vendor_journey(config, journal=None, preparation=None, transitions=None, currentness=None) is None
    assert opportunity_projection(config, journal=None, publication=None) is None


def test_the_loader_never_imports_the_vendor_substrate() -> None:
    # Isolation (mold A8/A9): the loader records the decision; the substrate modules keep
    # their own fences and are never consumed as admission surface.
    loader = (
        Path(__file__).resolve().parents[2] / "src" / "maezo" / "gateway" / "commercial_vendor_admission.py"
    ).read_text()
    for forbidden in (
        "from maezo.gateway.capabilities",
        "from maezo.gateway.human",
        "from maezo.gateway.vendor_submissions",
        "from maezo.gateway.vendor_suppression",
        "from maezo.portal",
        "from maezo.tools.workers",
    ):
        assert forbidden not in loader


# ----------------------------------------------------------------------------------
# (b) OP03/OP04 no binding: PURPOSE_DENIED com efeito downstream zero
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cursor", "operation"),
    [
        ("C2_acceptance", "acceptance.record"),
        ("C3_enrollment", "enrollment.request"),
    ],
    ids=["op03-acceptance", "op04-enrollment"],
)
async def test_channel_seats_stay_refused_purpose_denied_with_zero_downstream_effect(
    cursor: str, operation: str
) -> None:
    # The dossier's planned negative (b), re-pinned as regression: the refusal happens at
    # the binding guard BEFORE the driver — only OP01+OP02 ever run and nothing is
    # journaled, executed or replied.
    steps = list(VENDOR_TERMINAL_VIA_OP08_WAIT[:3]) + [Step(cursor, operation)]
    b, journal, _ingress, _oracle, executor, driver = vendor_parts(steps)
    first = await driver.accept_turn(turn(b, journal.revision))
    assert first.technical_refusal is None
    await driver.accept_observation(observation(b, journal, "unit-a10-seat-event"), journal.revision)
    result = await driver.resume("unit-a10-seat-event", journal.revision)
    assert result.technical_refusal == CapabilityRefusalReason.PURPOSE_DENIED
    assert result.source_completion_receipt_ref is None
    assert result.verified_transition_ref is None
    assert [action.envelope.operation_name for action in executor.effects] == [
        "access.resolve",
        "offer.compose",
    ]


# ----------------------------------------------------------------------------------
# (c) A escada fail-closed da OP16 sobre o objeto publicável ausente
# ----------------------------------------------------------------------------------


async def test_a_revoked_membership_is_authority_unproven_with_zero_store_effect() -> None:
    # Membership revogada não é membership publicada (dossier §"Objeto publicável").
    subject, store, _audit = machine()
    with pytest.raises(VendorSubmissionError) as exc:
        await subject.submit(TENANT, vendor_membership(revoked=True), command())
    assert exc.value.reason is VendorSubmissionRefusal.AUTHORITY_UNPROVEN
    assert store.count() == 0


async def test_a_binding_that_does_not_name_the_commanded_channel_is_authority_unproven() -> None:
    # "membership vendor publicada cujo vínculo único nomeia o canal comandado" — a
    # published membership bound to ANOTHER channel proves nothing here.
    subject, store, _audit = machine()
    with pytest.raises(VendorSubmissionError) as exc:
        await subject.submit(TENANT, vendor_membership(channel_ref="other-channel"), command())
    assert exc.value.reason is VendorSubmissionRefusal.AUTHORITY_UNPROVEN
    assert store.count() == 0


async def test_an_absent_channel_store_is_source_unavailable_never_zero() -> None:
    # "linha ausente ou store ausente é SOURCE_UNAVAILABLE (UNKNOWN, nunca zero)".
    subject, _store, _audit = machine(channels=ChannelDirectory(None))
    with pytest.raises(VendorSubmissionError) as exc:
        await subject.submit(TENANT, vendor_membership(), command())
    assert exc.value.reason is VendorSubmissionRefusal.SOURCE_UNAVAILABLE


async def test_a_revoked_channel_row_is_channel_status_ineligible() -> None:
    subject, store, _audit = machine(channels=ChannelDirectory({CHANNEL: (TENANT, "revoked")}))
    with pytest.raises(VendorSubmissionError) as exc:
        await subject.submit(TENANT, vendor_membership(), command())
    assert exc.value.reason is VendorSubmissionRefusal.CHANNEL_STATUS_INELIGIBLE
    assert store.count() == 0


def test_the_op16_refusal_vocabulary_is_exactly_the_registry_set() -> None:
    assert {member.value for member in VendorSubmissionRefusal} == {
        "AUTHORITY_UNPROVEN",
        "CHANNEL_STATUS_INELIGIBLE",
        "CONTRACT_MISMATCH",
        "SOURCE_UNAVAILABLE",
        "PHI_IN_COMMERCIAL_INPUT",
        "STALE_REVISION",
        "AUDIT_UNAVAILABLE",
    }


async def test_the_op13_cadence_refuses_unwired_and_publishes_nothing() -> None:
    # The administrative act stays the only provisioning path (ADR-0063); an unwired
    # cadence tick refuses typed instead of fabricating a publication.
    handler = make_vendor_publication_handler(None)
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await handler(None)  # type: ignore[arg-type]  # the wiring guard fires before the (unread) task


def test_the_op13_topic_is_the_membership_publication_cadence() -> None:
    assert VENDOR_PUBLICATION_TOPIC == "vendor.membership.publication"


# ----------------------------------------------------------------------------------
# (d) A rota OP16 segue DOCUMENTED but unbound na composição default
# ----------------------------------------------------------------------------------


def test_the_vendor_submission_route_comment_stays_byte_intact() -> None:
    # Documented-unbound, quoted: the GAP-CATALOG cites this comment as `app.py:335-338`;
    # on the tip it refalls at :342-346 (lineage drift of packages after the catalog —
    # the DSR import block; text byte-identical, disclosed in R12). No assertion pins a
    # line NUMBER, only the literal.
    app_source = (
        Path(__file__).resolve().parents[2] / "src" / "maezo" / "portal" / "api" / "app.py"
    ).read_text(errors="replace")
    assert "the vendor submission route ships DOCUMENTED but unbound" in app_source
    comment = "deployment projects the `vendor` capability and no default factory exists, so the route"
    assert comment in app_source
    assert "refuses (403 capability / 503 unbound slot) until the vendor plane is adopted." in app_source


def test_the_route_code_map_pins_the_documented_refusals() -> None:
    from maezo.portal.api.vendor_submissions import STATUS as ROUTE_STATUS

    assert ROUTE_STATUS["operation_forbidden"] == 403  # capability not projected by THIS deployment
    assert ROUTE_STATUS["AUTHORITY_UNPROVEN"] == 403  # no published membership naming the channel
    assert ROUTE_STATUS["CHANNEL_STATUS_INELIGIBLE"] == 403  # revoked/inactive channel row
    assert ROUTE_STATUS["SOURCE_UNAVAILABLE"] == 503  # absent store: UNKNOWN, never zero
    assert ROUTE_STATUS["resource_unavailable"] == 404


# ----------------------------------------------------------------------------------
# Guarda estrutural do stub fechado `vendor-projection.proposed.v1` — INDEPENDENTE de
# admissão
# ----------------------------------------------------------------------------------


def _clean_stub_payload(**extra: Any) -> dict[str, Any]:
    """The EXACT portal stub `vendor-projection.proposed.v1` — proposed placeholder only."""
    payload: dict[str, Any] = {
        "schemaVersion": VENDOR_PROJECTION_STUB_SCHEMA,
        "vendorRef": "unit-vendor-ref-1",
        "journeyRef": "unit-journey-1",
        "opportunityCommandRefs": ["offer.compose"],
        "pipelineStage": "C1_options",
        "stageSemantics": VENDOR_PROJECTION_STUB_STAGE_SEMANTICS,
        "pipelineRevision": "unit-revision-1",
        "channel": {"producerRef": "unknown", "channelClass": "unknown", "attributionBasisRef": None},
        "commercialProgress": VENDOR_PROJECTION_STUB_UNMEASURED,
        "attributionPolicy": VENDOR_PROJECTION_STUB_UNMEASURED,
        "sourcePublicationReceiptRef": "unit-publication-receipt-0001",
        "projectedAt": "2026-10-25T12:00:00+00:00",
    }
    payload.update(extra)
    return payload


def test_a_clean_stub_payload_passes_without_any_record() -> None:
    # The proposed stub ("Not a published Maezo source, not an admission, not a qualified
    # matrix row") is the legitimate placeholder: the guard kills channel FABRICATION,
    # never the stub shape itself, admission or no admission.
    assert refuse_vendor_projection_payload(_clean_stub_payload()) is None
    assert refuse_vendor_projection_payload({}) is None
    assert refuse_vendor_projection_payload({"event": "stage_entered"}) is None
    assert refuse_vendor_projection_payload({"commercialProgress": VENDOR_PROJECTION_STUB_UNMEASURED}) is None
    assert refuse_vendor_projection_payload({"attributionPolicy": VENDOR_PROJECTION_STUB_UNMEASURED}) is None


def test_the_stub_catalog_is_exactly_the_portal_twelve_fields() -> None:
    # Transcribed, not invented: `vendor-projection.proposed.v1` `additionalProperties:
    # false` over EXACTLY these twelve properties, three of which are `const`s.
    assert VENDOR_PROJECTION_STUB_FIELDS == frozenset(  # noqa: SIM300 — constant under test reads first
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
    assert VENDOR_PROJECTION_STUB_SCHEMA == "vendor-projection.proposed.v1"
    assert VENDOR_PROJECTION_STUB_STAGE_SEMANTICS == "PIPELINE_POSITION_ONLY_NOT_ACCEPTANCE"
    assert VENDOR_PROJECTION_STUB_UNMEASURED == "UNMEASURED"
    assert VENDOR_PROJECTION_CHANNEL_FIELDS == frozenset(  # noqa: SIM300 — constant under test reads first
        {"producerRef", "channelClass", "attributionBasisRef"}
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stageSemantics", "ACCEPTANCE_ASSERTED"),
        ("stageSemantics", "pipeline_position_only_not_acceptance"),
        ("stageSemantics", None),
        ("commercialProgress", "MEASURED"),
        ("commercialProgress", "P75"),
        ("commercialProgress", None),
        ("attributionPolicy", "FIRST_TOUCH"),
        ("attributionPolicy", "LAST_TOUCH"),
        ("attributionPolicy", None),
    ],
    ids=[
        "stage-acceptance",
        "stage-case",
        "stage-null",
        "progress-measured",
        "progress-percentile",
        "progress-null",
        "policy-first-touch",
        "policy-last-touch",
        "policy-null",
    ],
)
def test_a_mutated_stub_const_refuses(field: str, value: str) -> None:
    # A measured progress, a decided canal policy or an acceptance-asserting stage is
    # fabricated: the stub is PROPOSED and UNMEASURED is its const.
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(_clean_stub_payload(**{field: value}))
    assert exc.value.reason_code == "vendor_projection_stub_const_mutated"


def test_the_full_fabrication_refuses_with_one_typed_reason() -> None:
    payload = _clean_stub_payload(commercialProgress="MEASURED", attributionPolicy="FIRST_TOUCH")
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(payload)
    assert exc.value.reason_code == "vendor_projection_stub_const_mutated"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vendorRef", 42),
        ("vendorRef", ""),
        ("journeyRef", None),
        ("pipelineStage", ["C1_options"]),
        ("pipelineRevision", 7),
        ("sourcePublicationReceiptRef", ""),
        ("projectedAt", 1769000000),
        ("opportunityCommandRefs", "offer.compose"),
        ("opportunityCommandRefs", [42]),
        ("opportunityCommandRefs", None),
    ],
    ids=[
        "vendor-ref-int",
        "vendor-ref-empty",
        "journey-ref-null",
        "stage-list",
        "revision-int",
        "receipt-empty",
        "projected-at-int",
        "command-refs-scalar",
        "command-refs-int-member",
        "command-refs-null",
    ],
)
def test_a_declared_type_divergence_refuses(field: str, value: object) -> None:
    # The stub's own member declarations: `type: string, minLength: 1` over the refs,
    # `type: string, format: date-time` over `projectedAt`, array-of-strings over
    # `opportunityCommandRefs`.
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(_clean_stub_payload(**{field: value}))
    assert exc.value.reason_code == "vendor_projection_stub_type_invalid"


@pytest.mark.parametrize(
    "channel",
    [
        ["unknown"],
        "unknown",
        None,
        {},
        {"producerRef": "unknown"},
        {"producerRef": "unknown", "channelClass": "unknown"},
        {"producerRef": "unknown", "channelClass": "unknown", "attributionBasisRef": None, "score": "0.9"},
        {"producerRef": "unknown", "channelClass": "unknown", "basis": None},
        {"producerRef": "unknown", "channelClass": "unknown", "attributionBasisRef": 42},
    ],
    ids=[
        "list",
        "string",
        "null",
        "empty",
        "one-member",
        "two-members",
        "extra-member",
        "renamed-member",
        "basis-int",
    ],
)
def test_a_channel_outside_the_closed_shape_refuses(channel: object) -> None:
    # `additionalProperties: false` over EXACTLY producerRef/channelClass/attributionBasisRef.
    payload = _clean_stub_payload()
    payload["channel"] = channel
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(payload)
    assert exc.value.reason_code == "vendor_projection_channel_shape_invalid"


def test_a_null_basis_is_the_no_admitted_basis_value() -> None:
    # "Null means no admitted attribution basis is published; it is never a guessed or
    # zeroed basis" — null passes; a non-empty string basis passes.
    payload = _clean_stub_payload()
    payload["channel"]["attributionBasisRef"] = "unit-basis-ref"
    assert refuse_vendor_projection_payload(payload) is None


@pytest.mark.parametrize(
    ("member", "value"),
    [
        ("producerRef", "0"),
        ("producerRef", ""),
        ("producerRef", "   "),
        ("channelClass", "0"),
        ("channelClass", ""),
    ],
    ids=["producer-zero", "producer-empty", "producer-blank", "class-zero", "class-empty"],
)
def test_a_zeroed_channel_attribute_refuses(member: str, value: str) -> None:
    # "absent attributes are the literal 'unknown', never zero, never invented" — the
    # stub's own $comment and the projection's `unknown_is_never_zero` twin clause.
    payload = _clean_stub_payload()
    payload["channel"][member] = value
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(payload)
    assert exc.value.reason_code == "vendor_projection_channel_zero_fabricated"


def test_the_unknown_literal_passes() -> None:
    payload = _clean_stub_payload()
    assert payload["channel"]["producerRef"] == "unknown"
    assert refuse_vendor_projection_payload(payload) is None


@pytest.mark.parametrize(
    "extra",
    [{"schema": 1}, {"progressNote": "75%"}, {"score": "0.9"}, {"commercialProgressNote": "x"}],
    ids=["schema-field", "progress-note", "score", "progress-note-renamed"],
)
def test_an_extra_field_beyond_the_stub_shape_refuses(extra: dict) -> None:
    # `additionalProperties: false`: any field beyond the stub's twelve IS the value the
    # stub forbids adding before an admitted contract exists.
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(_clean_stub_payload(**extra))
    assert exc.value.reason_code == "vendor_projection_stub_shape_invalid"


@pytest.mark.parametrize("field", ["stageSemantics", "channel", "commercialProgress", "attributionPolicy"])
def test_a_family_named_payload_missing_a_stub_field_refuses(field: str) -> None:
    payload = _clean_stub_payload()
    payload.pop(field)
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(payload)
    assert exc.value.reason_code == "vendor_projection_stub_shape_invalid"


def test_family_membership_is_declared_by_the_marker_never_inferred() -> None:
    # Dropping `schemaVersion` drops the family claim itself: the remaining eleven stub
    # fields fabricate nothing (the shape clause binds payloads OF the family, and
    # membership is declared, not inferred).
    payload = _clean_stub_payload()
    payload.pop("schemaVersion")
    assert refuse_vendor_projection_payload(payload) is None


def test_a_stray_field_inside_the_family_refuses_on_shape_first() -> None:
    # Ordering (mold A8/A9): a family-named payload with an extra stray-looking field
    # diverges from the closed shape BEFORE any stray-value clause could fire.
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(_clean_stub_payload(progressNote="75%"))
    assert exc.value.reason_code == "vendor_projection_stub_shape_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commercialProgress", "MEASURED"),
        ("commercialProgress", "P75"),
        ("commercialProgress", None),
        ("commercialProgress", {"percent": 75}),
        ("attributionPolicy", "FIRST_TOUCH"),
        ("attributionPolicy", "LAST_TOUCH"),
        ("attributionPolicy", None),
        ("attributionPolicy", 7),
    ],
    ids=[
        "progress-measured",
        "progress-percentile",
        "progress-null",
        "progress-object",
        "policy-first-touch",
        "policy-last-touch",
        "policy-null",
        "policy-int",
    ],
)
def test_a_stray_dossier_named_field_refuses_without_family_marker(field: str, value: object) -> None:
    # The two names the dossier's RECUSAR fixes ("commercialProgress/attributionPolicy
    # permanecem UNMEASURED") have NO pre-successor value but the stub's const: carrying
    # them with any other value fabricates the channel surface with or without the family
    # marker — a successor schema binds it, never a re-declared field.
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload({"vendorRef": "unit-vendor", field: value})
    expected = (
        "vendor_commercial_progress_fabricated"
        if field == "commercialProgress"
        else ("vendor_attribution_policy_fabricated")
    )
    assert exc.value.reason_code == expected


@pytest.mark.parametrize("payload", [[], "vendor-projection", 7, None, {1: "x"}])
def test_non_object_payloads_and_non_string_keys_refuse(payload: object) -> None:
    # A vendor-projection payload IS a JSON object; anything else is not a stub payload
    # and refuses rather than passing silently.
    with pytest.raises(VendorProjectionPayloadError) as exc:
        refuse_vendor_projection_payload(payload)
    assert exc.value.reason_code == "vendor_projection_payload_not_object"


def test_the_guard_is_admission_independent_by_signature() -> None:
    # Structural, mold `registered ≠ honored`: the guard takes NO admission, and the
    # record never substitutes the structural clauses.
    parameters = inspect.signature(refuse_vendor_projection_payload).parameters
    assert set(parameters) == {"payload"}
