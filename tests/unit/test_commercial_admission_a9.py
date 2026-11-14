"""A9 (NR-10 lifecycle/analytics) registered-analytics admission — negative tests (D-16).

Executable regression for the planned negatives of admission A9 (GAP-CATALOG-A1-A10 §2,
transcribed from the pending dossier; the dossier stays OUT of `src/**` — A-INDEX rule 1,
pinned here by name):
(a) without A9 admitted, NO analytics event is emitted by any increment and UNMEASURED
    stays the state of P8 metrics ("ausência que não é flag"): the shipped state is
    ABSENT-by-absence — no record ships anywhere in the repository, the commercial
    composition grows no measurement parameter, the `src/` tree carries ZERO
    Sales-channel analytics surface (the only "analytics" occurrences are the operator's
    INTERNAL observability/telemetry/internal-actuarial modules, pinned as a closed tree
    set), and the frozen contract names analytics EXACTLY TWICE, both times inside a
    persistence prohibition (`contract-c6-4.json:446` intake and `:11016` terms — both
    literals end "analytics or browser persistence");
(b) the closed portal stub `measurement.proposed.v1` remains a stub: the typed mirror of
    its `additionalProperties: false` + three consts + synthetic-only identity enums
    refuses a payload that names the family with a mutated const, a non-synthetic
    tenant/journey or an extra field, and refuses a stray `journey_measurement_key`/
    `dedupe_key`/`variant_id` field with any value — admission-INDEPENDENT (holds with
    no record, exactly as with a registered refusal), and a clean synthetic stub payload
    passes (the stub is a legitimate proposed placeholder while sem valores);
(c) the browser never emits authoritative `accepted`/`enrolled`/`coverage_active` and no
    personal payload reaches URL/DOM/storage/logs: maezo-side this is pinned by the
    closed-tree isolation of the internal modules and by the egress note staying
    byte-intact in `portal/contracts/models.py`.
Plus the admission invariants around them: the registered refusal (`refused`, the
dossier's literal decision value — "o NO-GO persiste como recusa registrada") is the
ONLY state the parser produces; the Forma-de-medir record refuses
`admission_scope_invalid` BY CONSTRUCTION at this pin (the published lifecycle/analytics
contract does not exist — "hoje não publicado" — and enabling binds "por sucessor
versionado — e somente aí P8 pode medir"); the owner role is EXACTLY `DPO` (single leg —
coadjuvantes do not sign); the admitted command set must be EXACTLY empty; deferred
never binds; expired, foreign-id, divergent-pin and closed-structure violations refuse;
the A1–A8 records never open the measurement dimension and the A9 record never opens
theirs (both directions, cross-parser); the loader re-validates on every read; a missing
file is absence (`FileNotFoundError`), never a refusal; and `commercial_admission.py` is
structurally untouched (A9 has no C6-4 command, no event surface and no runtime seam —
mold R8/R9/R10; the transversal P8 measurement gate is a declared future/human leg).
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.commercial_acceptance_admission import (
    A3_CONTRACT_SHA256,
    parse_acceptance_record_admission,
)
from maezo.gateway.commercial_analytics_admission import (
    A9_ADMITTED_COMMANDS,
    A9_CONTRACT_SHA256,
    A9_DEDUPE_KEY_FIELD,
    A9_JOURNEY_MEASUREMENT_KEY_FIELD,
    A9_OWNER_ROLES,
    A9_REGISTERED_REFUSAL_DECISION,
    A9_UNLOCKS,
    A9_VARIANT_ID_FIELD,
    MEASUREMENT_STUB_FIELDS,
    MEASUREMENT_STUB_JOURNEYS,
    MEASUREMENT_STUB_SCHEMA,
    MEASUREMENT_STUB_STATUS,
    MEASUREMENT_STUB_TENANTS,
    MEASUREMENT_STUB_TRACK,
    AnalyticsAdmissionError,
    MeasurementPayloadError,
    parse_analytics_admission,
    read_analytics_admission,
    refuse_measurement_payload,
)
from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256, parse_catalog_source_admission
from maezo.gateway.commercial_commission_admission import A8_CONTRACT_SHA256, parse_commission_admission
from maezo.gateway.commercial_coverage_admission import A4_CONTRACT_SHA256, parse_coverage_read_admission
from maezo.gateway.commercial_portability_admission import A7_CONTRACT_SHA256, parse_portability_admission
from maezo.gateway.commercial_sales_admission import A2_CONTRACT_SHA256, parse_sales_session_admission
from maezo.gateway.commercial_support_admission import A5_CONTRACT_SHA256, parse_support_handoff_admission
from maezo.gateway.commercial_suppression_admission import A6_CONTRACT_SHA256, parse_suppression_admission

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def a9_record(**changes):
    """A VALID registered-refusal record — the only state the A9 parser can produce."""
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A9",
            "decision": A9_REGISTERED_REFUSAL_DECISION,
            "owners": ["DPO"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": A9_UNLOCKS,
            "admitted_commands": [],
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A9_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a9-registered-refusal.json",
                "object_sha256": "c" * 64,
                "origin_git_sha": "d" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a9_admission():
    return parse_analytics_admission(a9_record(), now=NOW)


def _clean_stub_payload(**extra: Any) -> dict[str, Any]:
    """The EXACT portal stub `measurement.proposed.v1` — synthetic example only."""
    payload: dict[str, Any] = {
        "schemaVersion": MEASUREMENT_STUB_SCHEMA,
        "status": MEASUREMENT_STUB_STATUS,
        "track": MEASUREMENT_STUB_TRACK,
        "tenant": "synthetic-tenant-a",
        "journey": "synthetic-journey-b",
        "event": "stage_entered",
        "elapsedMilliseconds": 0,
        "stage": "S01",
        "lifecycleStage": "discovery",
        "fieldCode": "NONE",
        "validationCode": "NONE",
        "attemptId": "NONE",
        "attemptNumber": 0,
        "rubric": "NOT_ASSESSED",
    }
    payload.update(extra)
    return payload


# ----------------------------------------------------------------------------------
# (mold) Loader — o papel único DPO é a decisão NR-10; a recusa registrada é o ÚNICO
# estado produzido
# ----------------------------------------------------------------------------------


def test_the_registered_refusal_record_parses_and_round_trips() -> None:
    admission = a9_admission()
    assert admission.admission_id == "A9"
    assert admission.contract_sha256 == A9_CONTRACT_SHA256
    assert admission.owners == ("DPO",)
    assert set(admission.owners) == A9_OWNER_ROLES
    assert admission.valid_until > NOW


def test_the_registered_refusal_is_the_only_produced_state() -> None:
    # Forma-de-medir (`decision == "admitted"`) against THIS pin: scope-invalid BY
    # CONSTRUCTION — even with the exact DPO owner, valid timestamps and the exact
    # (empty) scope. The dossier is literal: the lifecycle/analytics contract is "hoje
    # não publicado" (nenhum mecanismo qualificado, nenhuma base legal, prazo, piso de
    # célula ou destino decididos) and enabling binds "por sucessor versionado — e
    # somente aí P8 pode medir". No owner wording can make a pin that only PROHIBITS
    # analytics (two persistence clauses, zero commands) carry "escopo exato".
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(decision="admitted"), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_forma_de_medir_refusal_is_decision_level_not_shape_level() -> None:
    # The Forma-de-medir refusal fires BEFORE owner validation on purpose: no owner set
    # can launder a measurement-form record through this pin. A later successor package
    # re-pins the loader; this one never grows an admitted branch by owner request alone.
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(decision="admitted", owners=["security"]), now=NOW)
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
    # The A7/A8 admitted-state literals are FOREIGN here: each admission's decision
    # vocabulary is its own dossier's, never the family's.
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(decision=decision), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_a_deferred_record_never_binds() -> None:
    # The dossier's ADIAR is "nenhum registro; pendência explícita": a deferred RECORD is
    # not a contemplated state and never registers a limit by omission.
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(decision="deferred"), now=NOW)
    assert exc.value.reason_code == "admission_not_admitted"


def test_a_record_without_the_dpo_leg_never_parses() -> None:
    # NR-10 measurement is EXPLICITLY the DPO's decision; the dossier demotes every
    # other actor (controlador/records, identity/source owners, security, produto/
    # analytics owner) to coadjuvante — and coadjuvantes do not sign.
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(owners=[]), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        ["dpo"],
        ["DPO", "security"],
        ["identity", "channel", "security", "DPO"],
        ["comercial", "legal", "DPO"],
        ["produto", "legal"],
        ["owner (origem)", "DPO (origem)"],
        ["controlador"],
        ["DPO", "produto/analytics owner"],
        # The role is EXACT — no coadjuvante rides a registered refusal.
        ["DPO", "DPO"],
        [7],
    ],
)
def test_foreign_or_multi_leg_owner_sets_refuse(owners: object) -> None:
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(owners=owners), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "commands",
    [
        ["analytics.track"],
        ["catalog.get"],
        ["suppression.record"],
        ["support.handoff"],
        ["journey.get"],
        ["analytics.track", "catalog.get"],
        "catalog.get",
        [{"event": "stage_entered"}],
    ],
)
def test_the_registered_refusal_admits_exactly_no_measurement(commands: object) -> None:
    # Any named command would claim measurement over a pin that carries analytics ONLY
    # as prohibition: the refusal's scope is EXACTLY empty (pinned below).
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(admitted_commands=commands), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_the_admitted_scope_constant_is_exactly_empty() -> None:
    assert frozenset() == A9_ADMITTED_COMMANDS
    assert len(A9_ADMITTED_COMMANDS) == 0


# ----------------------------------------------------------------------------------
# (a)+(c) O contrato congelado NÃO tem analytics — só as DUAS proibições literais
# ----------------------------------------------------------------------------------


def test_the_frozen_contract_has_zero_analytics_commands() -> None:
    contract = json.loads((Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_bytes())
    commands = set(contract["x-commands"])
    assert len(commands) == 42
    assert not any("analytics" in command.lower() for command in commands)
    assert not any("measurement" in command.lower() for command in commands)
    assert A9_ADMITTED_COMMANDS.isdisjoint(commands)


def test_the_contract_names_analytics_exactly_twice_both_as_prohibition() -> None:
    # The literal transcription of GAP-CATALOG §2/A9(i): "analytics só aparece como
    # proibição" — intake `:446` and terms persistence `:11016`, both ending
    # "analytics or browser persistence". A third occurrence would be surface.
    raw = (Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_text()
    lines = [line for line in raw.splitlines() if "analytics" in line.lower()]
    assert len(lines) == 2
    assert all("analytics or browser persistence" in line for line in lines)
    assert any("never financial journal, logs, analytics or browser persistence" in line for line in lines)


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
        lambda record: record.update({"admission_id": "A8"}),
        lambda record: record.update({"admission_id": "a9"}),
        lambda record: record.update({"unlocks": "metricas/eventos (decisao)"}),
        lambda record: record.update({"unlocks": "métricas/eventos"}),
        lambda record: record.update({"unlocks": "P6.i"}),
        lambda record: record.pop("decision"),
        lambda record: record.pop("admitted_commands"),
        lambda record: record.update({"extra_field": "x"}),
    ],
    ids=[
        "schema",
        "foreign-id-a8",
        "id-case",
        "unlocks-unaccented",
        "unlocks-bare",
        "unlocks-p6i",
        "decision-missing",
        "scope-missing",
        "extra-field",
    ],
)
def test_structural_divergences_refuse_as_record_invalid(mutate) -> None:
    record = json.loads(a9_record())
    mutate(record)
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_pin_divergent_refuses_loud() -> None:
    record = json.loads(a9_record())
    record["contract_pin"]["sha256"] = "e" * 64
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


@pytest.mark.parametrize("field", ["contract_id", "revision", "sha256"])
def test_pin_fields_are_closed(field: str) -> None:
    record = json.loads(a9_record())
    record["contract_pin"].pop(field)
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_contract_pin_extra_field_refuses() -> None:
    record = json.loads(a9_record())
    record["contract_pin"]["note"] = "x"
    with pytest.raises(AnalyticsAdmissionError):
        parse_analytics_admission(json.dumps(record).encode(), now=NOW)


def test_expired_record_refuses() -> None:
    record = json.loads(a9_record())
    record["valid_until"] = "2026-10-25T00:00:00+00:00"
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_expired"


def test_expiry_never_reopens_measurement() -> None:
    # "Ausência que não é flag": an expired refusal refuses loudly AND keeps the world
    # state — nothing measures with or without a parseable record.
    record = json.loads(a9_record())
    record["valid_until"] = "2026-10-25T00:00:00+00:00"
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code != "admission_scope_invalid"


def test_decided_in_the_future_refuses() -> None:
    record = json.loads(a9_record())
    record["decided_at"] = "2026-10-26T00:00:00+00:00"
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [("decided_at", "2026-10-24T00:00:00"), ("valid_until", "2099-01-01T00:00:00")],
)
def test_naive_timestamps_never_admit(field: str, value: str) -> None:
    record = json.loads(a9_record())
    record[field] = value
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize("field", ["decided_at", "valid_until"])
def test_non_timestamp_refuses(field: str) -> None:
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(**{field: "not-a-timestamp"}), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_naive_now_is_a_caller_bug_not_a_refusal() -> None:
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_analytics_admission(a9_record(), now=datetime(2026, 10, 25, 12, 0))


@pytest.mark.parametrize(
    "evidence",
    [
        {"object_url": "http://amh.invalid/a9.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a9.json ", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a9.json", "object_sha256": "c" * 63, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a9.json", "object_sha256": "g" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a9.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 39},
        {"object_url": "https://amh.invalid/a9.json", "object_sha256": "c" * 64, "origin_git_sha": ""},
    ],
)
def test_evidence_shape_is_closed(evidence: dict) -> None:
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(a9_record(evidence=evidence), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_invalid_json_and_non_object_refuse() -> None:
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(b"{not json", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(b"[1,2]", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_missing_file_is_absence_not_refusal() -> None:
    with pytest.raises(FileNotFoundError):
        read_analytics_admission("/nonexistent/a9-admission-record.json", now=NOW)


def test_loader_revalidates_on_every_read_never_caches(tmp_path: Path) -> None:
    path = tmp_path / "A9-ADMISSION-RECORD.json"
    path.write_bytes(a9_record())
    assert read_analytics_admission(path, now=NOW).admission_id == "A9"
    record = json.loads(a9_record())
    record["contract_pin"]["sha256"] = "e" * 64
    path.write_bytes(json.dumps(record).encode())
    with pytest.raises(AnalyticsAdmissionError) as exc:
        read_analytics_admission(path, now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


def test_pin_family_is_one_freeze_across_a1_a9_and_the_codec() -> None:
    from maezo.adapters.amh.commercial import CANDIDATE_SCHEMA_SHA256

    assert A9_CONTRACT_SHA256 == A1_CONTRACT_SHA256 == A2_CONTRACT_SHA256
    assert A9_CONTRACT_SHA256 == A3_CONTRACT_SHA256 == A4_CONTRACT_SHA256
    assert A9_CONTRACT_SHA256 == A5_CONTRACT_SHA256 == A6_CONTRACT_SHA256
    assert A9_CONTRACT_SHA256 == A7_CONTRACT_SHA256 == A8_CONTRACT_SHA256
    assert A9_CONTRACT_SHA256 == CANDIDATE_SCHEMA_SHA256


# ----------------------------------------------------------------------------------
# Independência A1–A9 nos dois sentidos (cross-parser) + superfície comercial intocada
# ----------------------------------------------------------------------------------


def _a1_to_a8_records():
    from tests.unit.test_commercial_admission_a1 import a1_record
    from tests.unit.test_commercial_admission_a2 import a2_record
    from tests.unit.test_commercial_admission_a3 import a3_record
    from tests.unit.test_commercial_admission_a4 import a4_record
    from tests.unit.test_commercial_admission_a5 import a5_record
    from tests.unit.test_commercial_admission_a6 import a6_record
    from tests.unit.test_commercial_admission_a7 import a7_record
    from tests.unit.test_commercial_admission_a8 import a8_record

    return [
        ("A1", a1_record()),
        ("A2", a2_record()),
        ("A3", a3_record()),
        ("A4", a4_record()),
        ("A5", a5_record()),
        ("A6", a6_record()),
        ("A7", a7_record()),
        ("A8", a8_record()),
    ]


@pytest.mark.parametrize(("admission_id", "record_bytes"), _a1_to_a8_records())
def test_a1_a8_records_never_open_the_measurement_dimension(admission_id: str, record_bytes: bytes) -> None:
    with pytest.raises(AnalyticsAdmissionError) as exc:
        parse_analytics_admission(record_bytes, now=NOW)
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
    ],
    ids=["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
)
def test_the_a9_record_never_opens_a1_a8(parse) -> None:
    # The registered-refusal value is not even in the A1–A8 decision vocabularies: a
    # foreign parser refuses it structurally, never by admission.
    with pytest.raises(Exception) as exc:
        parse(a9_record())
    assert type(exc.value).__name__.endswith("AdmissionError")
    assert not isinstance(exc.value, AnalyticsAdmissionError)


def test_the_commercial_composition_has_no_a9_surface() -> None:
    # A9 is ABSENT — no C6-4 command, no event surface, no runtime seam, and the
    # transversal P8 measurement gate is a declared future/human leg: the commercial
    # composition grows no parameter for it (zero regression on the A1–A8 gate matrix
    # by construction).
    from maezo.gateway.commercial_admission import (
        compose_commercial_gateway,
        compose_public_catalog_gateway,
    )

    for compose in (compose_commercial_gateway, compose_public_catalog_gateway):
        parameters = set(inspect.signature(compose).parameters)
        assert parameters.isdisjoint({"analytics_admission", "measurement_admission", "analytics_source"})


# ----------------------------------------------------------------------------------
# (a) A ausência É o estado — nenhum registro no repositório, nenhuma superfície nova,
#     as colisões de nome isoladas (observabilidade/telemetria/atiário internos, nunca
#     medição do canal Sales)
# ----------------------------------------------------------------------------------


def test_no_a9_admission_record_ships_in_the_repository() -> None:
    root = Path(__file__).resolve().parents[2]
    for base in ("docs", "src", "config", "tests", "spec"):
        assert not list((root / base).rglob("A9-ADMISSION-RECORD.json")), base


def test_the_pending_dossier_never_appears_in_src() -> None:
    # A-INDEX rule 1: "Dossiê status pending nunca aparece em código (src/**)".
    root = Path(__file__).resolve().parents[2]
    for path in (root / "src" / "maezo").rglob("*.py"):
        assert "A9-NR10-LIFECYCLE-ANALYTICS" not in path.read_text(errors="replace"), path


def test_src_tree_analytics_vocabulary_is_exactly_the_internal_set_plus_this_loader() -> None:
    # The ONLY "analytics" occurrences in `src/` python are the operator's INTERNAL
    # modules (internal observability, turn telemetry, the A2A internal actuarial
    # task types `analytics.population`/`analytics.actuarial` over k-anon aggregates,
    # the AMH consent purpose vector, the QA workgroup string and the gateway egress
    # note) plus this loader — NONE is a Sales-channel NR-10 surface. The pin makes any
    # new occurrence a conscious, gate-visible act.
    root = Path(__file__).resolve().parents[2] / "src" / "maezo"
    allowed = {
        (root / "tools" / "workers" / "pagto.py").resolve(),
        (root / "tools" / "workers" / "adequacao.py").resolve(),
        (root / "portal" / "contracts" / "models.py").resolve(),
        (root / "platform" / "observability.py").resolve(),
        (root / "runtime" / "turn_telemetry.py").resolve(),
        (root / "runtime" / "agent_runtime" / "a2a_composition.py").resolve(),
        (root / "agents" / "andre" / "graph.py").resolve(),
        (root / "agents" / "andre" / "delegation.py").resolve(),
        (root / "agents" / "andre" / "prompts.py").resolve(),
        (root / "qa" / "simulador" / "roster.py").resolve(),
        (root / "adapters" / "amh" / "mapping.py").resolve(),
        (root / "gateway" / "commercial_analytics_admission.py").resolve(),
    }
    found: dict[Path, list[int]] = {}
    for path in root.rglob("*.py"):
        lines = [
            number
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1)
            if "analytics" in line.lower()
        ]
        if lines:
            found[path.resolve()] = lines
    assert set(found) == allowed, found


def test_the_internal_modules_are_untouched_and_never_consumed_as_surface() -> None:
    # The gateway egress note stays byte-intact (the internal boundary that already says
    # analytics/General-Zone egress needs a human/DPO derivation), and the loader shares
    # NO import with any internal module.
    models = (
        Path(__file__).resolve().parents[2] / "src" / "maezo" / "portal" / "contracts" / "models.py"
    ).read_text()
    assert (
        "analytics, logging or General-Zone egress. The gateway must derive human/DPO identity and enforce"
        in models
    )
    loader_path = Path(__file__).resolve().parents[2] / "src" / "maezo" / "gateway"
    loader = (loader_path / "commercial_analytics_admission.py").read_text()
    for forbidden in (
        "from maezo.platform.observability",
        "from maezo.runtime.turn_telemetry",
        "from maezo.runtime.agent_runtime.a2a_composition",
        "from maezo.agents.andre",
        "from maezo.adapters.amh.mapping",
        "from maezo.tools.workers.pagto",
        "from maezo.tools.workers.adequacao",
        "from maezo.qa.simulador.roster",
    ):
        assert forbidden not in loader


@pytest.mark.parametrize(
    "probe",
    ["analytics.track", "measurement.collect", "event.emit"],
)
def test_no_sales_channel_measurement_api_exists_anywhere(probe: str) -> None:
    # No emitter/recorder/collector API exists under `src/` for the Sales channel: the
    # probe names have zero occurrences (not even in this loader's module) — any future
    # appearance is a new, gate-visible file.
    root = Path(__file__).resolve().parents[2] / "src" / "maezo"
    found = {path.resolve() for path in root.rglob("*.py") if probe in path.read_text(errors="replace")}
    assert found == set(), (probe, found)


def test_the_admitted_envelope_names_exist_only_in_this_loader() -> None:
    # The three stray-guarded envelope names have ZERO pre-existing occurrences in `src/`
    # beyond this loader: they are transcribed from the dossier's admitted-analytics
    # envelope, not mined from existing code.
    root = Path(__file__).resolve().parents[2] / "src" / "maezo"
    allowed = {(root / "gateway" / "commercial_analytics_admission.py").resolve()}
    for name in (A9_JOURNEY_MEASUREMENT_KEY_FIELD, A9_DEDUPE_KEY_FIELD, A9_VARIANT_ID_FIELD):
        found = {path.resolve() for path in root.rglob("*.py") if name in path.read_text(errors="replace")}
        assert found == allowed, (name, found)


# ----------------------------------------------------------------------------------
# (b) Guarda estrutural do stub fechado — INDEPENDENTE de admissão
# ----------------------------------------------------------------------------------


def test_a_clean_synthetic_stub_payload_passes_without_any_record() -> None:
    # The proposed synthetic stub (disabled, sem valores) is the legitimate placeholder:
    # the guard kills measurement FABRICATION, never the stub shape itself, admission or
    # no admission.
    assert refuse_measurement_payload(_clean_stub_payload()) is None
    assert refuse_measurement_payload({}) is None
    assert refuse_measurement_payload({"event": "stage_entered"}) is None


def test_the_stub_catalog_is_exactly_the_portal_fourteen_fields() -> None:
    # Transcribed, not invented: `measurement.proposed.v1` `additionalProperties: false`
    # over EXACTLY these fourteen properties.
    assert MEASUREMENT_STUB_FIELDS == frozenset(  # noqa: SIM300 — constant under test reads first
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
    assert MEASUREMENT_STUB_SCHEMA == "measurement.proposed.v1"
    assert MEASUREMENT_STUB_STATUS == "PROPOSED"
    assert MEASUREMENT_STUB_TRACK == "SYNTHETIC_EXAMPLE_ONLY"
    assert MEASUREMENT_STUB_TENANTS == frozenset(  # noqa: SIM300 — constant under test reads first
        {"synthetic-tenant-a", "synthetic-tenant-b"}
    )
    assert MEASUREMENT_STUB_JOURNEYS == frozenset(  # noqa: SIM300 — constant under test reads first
        {"synthetic-journey-a", "synthetic-journey-b"}
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "ADMITTED"),
        ("status", "COLLECTING"),
        ("status", "proposed"),
        ("track", "LIVE"),
        ("track", "SYNTHETIC_EXAMPLE_ONLY".lower()),
        ("track", "PRODUCTION"),
    ],
    ids=[
        "status-admitted",
        "status-collecting",
        "status-case",
        "track-live",
        "track-case",
        "track-production",
    ],
)
def test_a_mutated_stub_const_refuses(field: str, value: str) -> None:
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(_clean_stub_payload(**{field: value}))
    assert exc.value.reason_code == "measurement_stub_const_mutated"


def test_a_null_const_is_still_a_mutation() -> None:
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(_clean_stub_payload(status=None))
    assert exc.value.reason_code == "measurement_stub_const_mutated"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant", "acme-corp"),
        ("tenant", "SYNTHETIC-TENANT-A"),
        ("journey", "compra-real"),
        ("journey", "SYNTHETIC-JOURNEY-A"),
    ],
    ids=["tenant-real", "tenant-case", "journey-real", "journey-case"],
)
def test_a_non_synthetic_identity_refuses(field: str, value: str) -> None:
    # The stub's identity enums are closed to SYNTHETIC values: a real tenant/journey in
    # a synthetic-only dictionary is a fabricated real observation ("No collection,
    # identity, real observations").
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(_clean_stub_payload(**{field: value}))
    assert exc.value.reason_code == "measurement_stub_not_synthetic"


@pytest.mark.parametrize(
    "extra",
    [
        {"eventName": "cta_clicked"},
        {"pageUrl": "https://portal.example/quote"},
        {"collector": "https://metrics.example/collect"},
    ],
    ids=["event-name-field", "page-url-field", "collector-field"],
)
def test_an_extra_field_beyond_the_stub_shape_refuses(extra: dict) -> None:
    # `additionalProperties: false`: any field beyond the stub's fourteen IS the value
    # the stub forbids adding before an admitted contract exists.
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(_clean_stub_payload(**extra))
    assert exc.value.reason_code == "measurement_stub_shape_invalid"


@pytest.mark.parametrize("field", ["status", "track", "rubric", "lifecycleStage"])
def test_a_family_named_payload_missing_a_stub_field_refuses(field: str) -> None:
    payload = _clean_stub_payload()
    payload.pop(field)
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(payload)
    assert exc.value.reason_code == "measurement_stub_shape_invalid"


def test_family_membership_is_declared_by_the_marker_never_inferred() -> None:
    # Dropping `schemaVersion` drops the family claim itself: the remaining thirteen
    # stub fields fabricate nothing (the shape clause binds payloads OF the family, and
    # membership is declared, not inferred).
    payload = _clean_stub_payload()
    payload.pop("schemaVersion")
    assert refuse_measurement_payload(payload) is None


def test_the_full_fabrication_refuses_with_one_typed_reason() -> None:
    payload = _clean_stub_payload(status="ADMITTED", track="LIVE")
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(payload)
    assert exc.value.reason_code == "measurement_stub_const_mutated"


def test_a_stray_envelope_field_inside_the_family_refuses_on_shape_first() -> None:
    # Ordering (mold A8): a family-named payload with an extra envelope field diverges
    # from the closed shape BEFORE the stray-field clause could fire.
    payload = _clean_stub_payload(**{A9_JOURNEY_MEASUREMENT_KEY_FIELD: "jm-key"})
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(payload)
    assert exc.value.reason_code == "measurement_stub_shape_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (A9_JOURNEY_MEASUREMENT_KEY_FIELD, "jm-key-123"),
        (A9_JOURNEY_MEASUREMENT_KEY_FIELD, None),
        (A9_JOURNEY_MEASUREMENT_KEY_FIELD, {"ephemeral": True}),
        (A9_DEDUPE_KEY_FIELD, "dedupe-1"),
        (A9_DEDUPE_KEY_FIELD, 42),
        (A9_VARIANT_ID_FIELD, "variant-b"),
        (A9_VARIANT_ID_FIELD, True),
    ],
    ids=[
        "journey-key-str",
        "journey-key-null",
        "journey-key-object",
        "dedupe-str",
        "dedupe-int",
        "variant-str",
        "variant-bool",
    ],
)
def test_a_stray_admitted_envelope_field_refuses_without_family_marker(field: str, value: object) -> None:
    # All three names are transcribed from the dossier's ADMITTED-analytics envelope and
    # have NO pre-successor value ("hoje OPEN, sem defaults inventados"): carrying any of
    # them with any value is fabrication regardless of the family marker — an admitted
    # envelope binds only through a successor versioned contract.
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload({field: value})
    expected = {
        A9_JOURNEY_MEASUREMENT_KEY_FIELD: "measurement_journey_key_fabricated",
        A9_DEDUPE_KEY_FIELD: "measurement_dedupe_key_fabricated",
        A9_VARIANT_ID_FIELD: "measurement_variant_id_fabricated",
    }[field]
    assert exc.value.reason_code == expected


@pytest.mark.parametrize("payload", [[], "measurement", 7, None, {1: "x"}])
def test_non_object_payloads_and_non_string_keys_refuse(payload: object) -> None:
    # A measurement payload IS a JSON object; anything else is not a stub payload and
    # refuses rather than passing silently.
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(payload)
    assert exc.value.reason_code == "measurement_payload_not_object"


def test_the_guard_is_admission_independent_by_signature() -> None:
    # Structural, mold `registered ≠ honored`: the guard takes NO admission, and the
    # record never substitutes the structural clauses.
    parameters = inspect.signature(refuse_measurement_payload).parameters
    assert set(parameters) == {"payload"}
