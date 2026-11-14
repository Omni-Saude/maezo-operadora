"""A7 (GAP-PORTABILITY, NR-11) registered-portability admission — negative tests (D-16).

Executable regression for the planned negatives of admission A7 (GAP-CATALOG-A1-A10 §2,
transcribed from the pending dossier; the dossier stays OUT of `src/**` — A-INDEX rule 1,
pinned here by name):
(a) without A7 admitted, NO surface displays compatibility/prazo: the shipped state is
    ABSENT-by-absence — no record ships anywhere in the repository, the commercial
    composition grows no portability parameter, and the `src/` tree keeps EXACTLY the
    three pre-existing LGPD-DSR "portabilidade" occurrences plus this loader itself
    (the name collision is isolated, never consumed as substrate);
(b) the dossier's seven structurally-prohibited verdict fields
    (`compatibilityVerdict`/`eligible`/`remainingWaitingDays`/`cancellationRequested`/
    `cancellationConfirmed`/`requestSubmitted`/`protocolRef`) are refused by the typed
    mirror of the portal schema's `not` clauses — admission-INDEPENDENT (holds with no
    record, exactly as with a registered ceiling);
(c) REG-006: no origin-plan cancellation exists anywhere — the frozen contract carries
    zero portability/cancellation commands among the 42, the admitted scope is exactly
    EMPTY, and the payload guard kills `cancellationRequested`/`cancellationConfirmed`
    before any fact could be derived.
Plus the admission invariants around them: the ceiling
(`admitted_ceiling_orientation_only`, the dossier's literal decision value) is the ONLY
admitted state; the Forma-1 source record refuses `admission_scope_invalid` BY
CONSTRUCTION at this pin (zero portability commands in the frozen contract — the
dossier's successor-freeze rule); the owner pair is EXACTLY `produto` + `legal`
(NR-11 is a produto+legal decision; neither leg derives from the other); the ceiling's
admitted command set must be EXACTLY empty; refused/deferred never bind; expired,
foreign-id, divergent-pin and closed-structure violations refuse; the A1–A6 records
never open the portability dimension and the A7 record never opens theirs (both
directions, cross-parser); the loader re-validates on every read; a missing file is
absence (`FileNotFoundError`), never a refusal; and `commercial_admission.py` is
structurally untouched (A7 has no C6-4 surface and no runtime seam — mold R8).
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
from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256, parse_catalog_source_admission
from maezo.gateway.commercial_coverage_admission import A4_CONTRACT_SHA256, parse_coverage_read_admission
from maezo.gateway.commercial_portability_admission import (
    A7_ADMITTED_COMMANDS,
    A7_CEILING_DECISION,
    A7_CONTRACT_SHA256,
    A7_OWNER_ROLES,
    A7_UNLOCKS,
    PORTABILITY_VERDICT_FIELDS_PROHIBITED,
    PortabilityAdmissionError,
    PortabilityPayloadError,
    parse_portability_admission,
    read_portability_admission,
    refuse_portability_verdict_payload,
)
from maezo.gateway.commercial_sales_admission import A2_CONTRACT_SHA256, parse_sales_session_admission
from maezo.gateway.commercial_support_admission import A5_CONTRACT_SHA256, parse_support_handoff_admission
from maezo.gateway.commercial_suppression_admission import A6_CONTRACT_SHA256, parse_suppression_admission

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def a7_record(**changes):
    """A VALID CEILING record — the only admitted state the A7 parser can produce."""
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A7",
            "decision": A7_CEILING_DECISION,
            "owners": ["produto", "legal"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": A7_UNLOCKS,
            "admitted_commands": [],
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A7_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a7-ceiling-decision.json",
                "object_sha256": "c" * 64,
                "origin_git_sha": "d" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a7_admission():
    return parse_portability_admission(a7_record(), now=NOW)


# ----------------------------------------------------------------------------------
# (mold) Loader — o par EXATO produto+legal é a decisão NR-11; o teto é o ÚNICO estado
# ----------------------------------------------------------------------------------


def test_the_ceiling_record_parses_and_round_trips() -> None:
    admission = a7_admission()
    assert admission.admission_id == "A7"
    assert admission.contract_sha256 == A7_CONTRACT_SHA256
    assert admission.owners == ("produto", "legal")
    assert set(admission.owners) == A7_OWNER_ROLES
    assert admission.valid_until > NOW


def test_the_ceiling_is_the_only_admitted_state() -> None:
    # Forma 1 (fonte própria, `decision == "admitted"`) against THIS pin: scope-invalid
    # BY CONSTRUCTION — even with the exact owner pair, valid timestamps and the exact
    # (empty) scope. The dossier is literal: the source form binds "por sucessor
    # versionado; a extensão exige diff de contrato revisado". No owner wording can make
    # an empty command universe carry "escopo exato (read e/ou intake...)".
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(decision="admitted"), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_forma_1_refusal_is_decision_level_not_shape_level() -> None:
    # The Forma-1 refusal fires BEFORE owner validation on purpose: no owner set can
    # launder a source-form record through this pin. A later successor package re-pins
    # the loader; this one never grows a source branch by owner request alone.
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(decision="admitted", owners=["legal"]), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


@pytest.mark.parametrize(
    "decision",
    [
        "admitted_ceiling",
        "ceiling_orientation_only",
        "orientation_only",
        "ADMITTED_CEILING_ORIENTATION_ONLY",
        "admitted_ceiling_orientation_only ",
        " admitted_ceiling_orientation_only",
        "",
    ],
)
def test_foreign_decision_values_are_structural_refusals(decision: str) -> None:
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(decision=decision), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize("decision", ["refused", "deferred"])
def test_refused_and_deferred_owner_decisions_never_bind(decision: str) -> None:
    # A NO-GO keeps orientation a PENDING gap — never a silently-registered ceiling.
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(decision=decision), now=NOW)
    assert exc.value.reason_code == "admission_not_admitted"


def test_record_without_the_legal_leg_never_parses() -> None:
    # NR-11 is explicitly a produto+legal decision: neither leg derives from the other's
    # acceptance, so a one-legged record would make the mechanism injectable without the
    # second decision — the pair is exact.
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(owners=["produto"]), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


def test_record_without_the_produto_leg_never_parses() -> None:
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(owners=["legal"]), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        [],
        ["produto "],
        ["legal"],
        ["case owner", "channel owner"],
        ["owner (origem)", "DPO (origem)"],
        # The `fonte própria de portabilidade` authority is *a designar* and rides the
        # Forma-1 successor package (re-pinned loader) — never a third leg of a ceiling.
        ["produto", "legal", "fonte própria de portabilidade"],
        ["produto", "produto"],
        ["produto", 7],
    ],
)
def test_foreign_or_partial_owner_sets_refuse(owners: object) -> None:
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(owners=owners), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "commands",
    [
        ["portability.intake"],
        ["portability.read"],
        ["orientation.read"],
        ["suppression.record"],
        ["support.handoff"],
        ["portability.intake", "portability.read"],
        "portability.intake",
    ],
)
def test_the_ceiling_admits_exactly_nothing(commands: object) -> None:
    # Any non-empty scope names a command the frozen contract does not carry (0
    # portability commands — pinned below): the ceiling's scope is EXACTLY empty.
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(admitted_commands=commands), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_the_admitted_scope_constant_is_exactly_empty() -> None:
    assert frozenset() == A7_ADMITTED_COMMANDS
    assert len(A7_ADMITTED_COMMANDS) == 0


# ----------------------------------------------------------------------------------
# (a)+(c) O contrato congelado NÃO tem portabilidade — a ausência É a lacuna (pin vivo)
# ----------------------------------------------------------------------------------


def test_the_frozen_contract_has_zero_portability_commands() -> None:
    contract = json.loads((Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_bytes())
    commands = set(contract["x-commands"])
    assert len(commands) == 42
    assert not any("portab" in command.lower() for command in commands)
    assert not any("cancel" in command.lower() for command in commands)
    assert A7_ADMITTED_COMMANDS.isdisjoint(commands)


def test_the_frozen_contract_text_never_mentions_portability() -> None:
    raw = (Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_text()
    assert "portab" not in raw.lower()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update({"schema": "omni-commercial-admission-record.v2"}),
        lambda record: record.update({"admission_id": "A6"}),
        lambda record: record.update({"admission_id": "a7"}),
        lambda record: record.update({"unlocks": "NR-11"}),
        lambda record: record.update({"unlocks": "NR-11 (decisao)"}),
        lambda record: record.update({"unlocks": "P6.4 (decisão)"}),
        lambda record: record.pop("decision"),
        lambda record: record.pop("admitted_commands"),
        lambda record: record.update({"extra_field": "x"}),
    ],
    ids=[
        "schema",
        "foreign-id-a6",
        "id-case",
        "unlocks-bare",
        "unlocks-unaccented",
        "unlocks-p64",
        "decision-missing",
        "scope-missing",
        "extra-field",
    ],
)
def test_structural_divergences_refuse_as_record_invalid(mutate) -> None:
    record = json.loads(a7_record())
    mutate(record)
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_pin_divergent_refuses_loud() -> None:
    record = json.loads(a7_record())
    record["contract_pin"]["sha256"] = "e" * 64
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


@pytest.mark.parametrize("field", ["contract_id", "revision", "sha256"])
def test_pin_fields_are_closed(field: str) -> None:
    record = json.loads(a7_record())
    record["contract_pin"].pop(field)
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_contract_pin_extra_field_refuses() -> None:
    record = json.loads(a7_record())
    record["contract_pin"]["note"] = "x"
    with pytest.raises(PortabilityAdmissionError):
        parse_portability_admission(json.dumps(record).encode(), now=NOW)


def test_expired_record_refuses() -> None:
    record = json.loads(a7_record())
    record["valid_until"] = "2026-10-25T00:00:00+00:00"
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_expired"


def test_decided_in_the_future_refuses() -> None:
    record = json.loads(a7_record())
    record["decided_at"] = "2026-10-26T00:00:00+00:00"
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [("decided_at", "2026-10-24T00:00:00"), ("valid_until", "2099-01-01T00:00:00")],
)
def test_naive_timestamps_never_admit(field: str, value: str) -> None:
    record = json.loads(a7_record())
    record[field] = value
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize("field", ["decided_at", "valid_until"])
def test_non_timestamp_refuses(field: str) -> None:
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(**{field: "not-a-timestamp"}), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_naive_now_is_a_caller_bug_not_a_refusal() -> None:
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_portability_admission(a7_record(), now=datetime(2026, 10, 25, 12, 0))


@pytest.mark.parametrize(
    "evidence",
    [
        {"object_url": "http://amh.invalid/a7.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a7.json ", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a7.json", "object_sha256": "c" * 63, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a7.json", "object_sha256": "g" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a7.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 39},
        {"object_url": "https://amh.invalid/a7.json", "object_sha256": "c" * 64, "origin_git_sha": ""},
    ],
)
def test_evidence_shape_is_closed(evidence: dict) -> None:
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(a7_record(evidence=evidence), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_invalid_json_and_non_object_refuse() -> None:
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(b"{not json", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(b"[1,2]", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_missing_file_is_absence_not_refusal() -> None:
    with pytest.raises(FileNotFoundError):
        read_portability_admission("/nonexistent/a7-admission-record.json", now=NOW)


def test_loader_revalidates_on_every_read_never_caches(tmp_path: Path) -> None:
    path = tmp_path / "A7-ADMISSION-RECORD.json"
    path.write_bytes(a7_record())
    assert read_portability_admission(path, now=NOW).admission_id == "A7"
    record = json.loads(a7_record())
    record["contract_pin"]["sha256"] = "e" * 64
    path.write_bytes(json.dumps(record).encode())
    with pytest.raises(PortabilityAdmissionError) as exc:
        read_portability_admission(path, now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


def test_pin_family_is_one_freeze_across_a1_a7_and_the_codec() -> None:
    from maezo.adapters.amh.commercial import CANDIDATE_SCHEMA_SHA256

    assert A7_CONTRACT_SHA256 == A1_CONTRACT_SHA256 == A2_CONTRACT_SHA256
    assert A7_CONTRACT_SHA256 == A3_CONTRACT_SHA256 == A4_CONTRACT_SHA256
    assert A7_CONTRACT_SHA256 == A5_CONTRACT_SHA256 == A6_CONTRACT_SHA256
    assert A7_CONTRACT_SHA256 == CANDIDATE_SCHEMA_SHA256


# ----------------------------------------------------------------------------------
# Independência A1–A7 nos dois sentidos (cross-parser) + superfície comercial intocada
# ----------------------------------------------------------------------------------


def _a1_to_a6_records():
    from tests.unit.test_commercial_admission_a1 import a1_record
    from tests.unit.test_commercial_admission_a2 import a2_record
    from tests.unit.test_commercial_admission_a3 import a3_record
    from tests.unit.test_commercial_admission_a4 import a4_record
    from tests.unit.test_commercial_admission_a5 import a5_record
    from tests.unit.test_commercial_admission_a6 import a6_record

    return [
        ("A1", a1_record()),
        ("A2", a2_record()),
        ("A3", a3_record()),
        ("A4", a4_record()),
        ("A5", a5_record()),
        ("A6", a6_record()),
    ]


@pytest.mark.parametrize(("admission_id", "record_bytes"), _a1_to_a6_records())
def test_a1_a6_records_never_open_the_portability_dimension(admission_id: str, record_bytes: bytes) -> None:
    with pytest.raises(PortabilityAdmissionError) as exc:
        parse_portability_admission(record_bytes, now=NOW)
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
    ],
    ids=["A1", "A2", "A3", "A4", "A5", "A6"],
)
def test_the_a7_record_never_opens_a1_a6(parse) -> None:
    # The ceiling decision value is not even in the A1–A6 decision vocabulary: a foreign
    # parser refuses it structurally, never by admission.
    with pytest.raises(Exception) as exc:
        parse(a7_record())
    assert type(exc.value).__name__.endswith("AdmissionError")
    assert not isinstance(exc.value, PortabilityAdmissionError)


def test_the_commercial_composition_has_no_a7_surface() -> None:
    # A7 is ABSENT — no C6-4 command, no runtime seam: the commercial composition grows
    # no parameter for it (zero regression on the A1–A6 gate matrix by construction).
    from maezo.gateway.commercial_admission import (
        compose_commercial_gateway,
        compose_public_catalog_gateway,
    )

    for compose in (compose_commercial_gateway, compose_public_catalog_gateway):
        parameters = set(inspect.signature(compose).parameters)
        assert parameters.isdisjoint({"portability_admission", "portability_source", "portability_ceiling"})


# ----------------------------------------------------------------------------------
# (a) A ausência É o estado — nenhum registro no repositório, nenhuma superfície nova,
#     a colisão DSR LGPD isolada (nunca substrato comercial)
# ----------------------------------------------------------------------------------


def test_no_a7_admission_record_ships_in_the_repository() -> None:
    root = Path(__file__).resolve().parents[2]
    for base in ("docs", "src", "config", "tests", "spec"):
        assert not list((root / base).rglob("A7-ADMISSION-RECORD.json")), base


def test_the_pending_dossier_never_appears_in_src() -> None:
    # A-INDEX rule 1: "Dossiê status pending nunca aparece em código (src/**)".
    root = Path(__file__).resolve().parents[2]
    for path in (root / "src" / "maezo").rglob("*.py"):
        assert "A7-GAP-PORTABILITY-PORTABILIDADE" not in path.read_text(errors="replace"), path


def test_src_tree_keeps_exactly_the_lgpd_dsr_collision_plus_this_loader() -> None:
    # The ONLY "portabilidade"/"portability" occurrences in `src/` are the LGPD data-
    # subject right (a NAME COLLISION with NR-11 portability of waiting periods) and
    # this loader itself. This package adds no commercial portability read/intake
    # surface — and the pin makes any future appearance a conscious, gate-visible act.
    root = Path(__file__).resolve().parents[2] / "src" / "maezo"
    allowed = {
        (root / "tools" / "workers" / "lgpd.py").resolve(),
        (root / "portal" / "contracts" / "dsr.py").resolve(),
        (root / "gateway" / "commercial_portability_admission.py").resolve(),
    }
    found: dict[Path, list[int]] = {}
    for path in root.rglob("*.py"):
        lines = [
            number
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1)
            if "portabilidade" in line.lower() or "portability" in line.lower()
        ]
        if lines:
            found[path.resolve()] = lines
    assert set(found) == allowed, found
    # …and none of the collision occurrences lives in any other gateway file.
    assert all(path.name in {"lgpd.py", "dsr.py", "commercial_portability_admission.py"} for path in found)


def test_the_lgpd_dsr_collision_is_untouched_and_never_consumed_as_substrate() -> None:
    # The DSR right of data portability (art. 18) keeps its exact declared mapping —
    # this package renames, remaps or consumes nothing of it.
    from maezo.portal.contracts.dsr import DsrTipoRequisicao
    from maezo.tools.workers.lgpd import _TIPO_REQUISICAO_PARA_DIREITO

    assert _TIPO_REQUISICAO_PARA_DIREITO["portabilidade"] == "exportacao"
    assert len(_TIPO_REQUISICAO_PARA_DIREITO) == 6
    assert "portabilidade" in DsrTipoRequisicao.__args__  # type: ignore[attr-defined]
    # Isolation: the A7 loader shares NO import with the LGPD DSR modules.
    loader_path = Path(__file__).resolve().parents[2] / "src" / "maezo" / "gateway"
    loader = (loader_path / "commercial_portability_admission.py").read_text()
    assert "from maezo.tools.workers.lgpd" not in loader
    assert "from maezo.portal.contracts.dsr" not in loader
    assert "import maezo.tools.workers.lgpd" not in loader
    assert "import maezo.portal.contracts.dsr" not in loader


# ----------------------------------------------------------------------------------
# (b)+(c) Guarda estrutural dos 7 campos de veredito — INDEPENDENTE de admissão
# ----------------------------------------------------------------------------------


def _orientation_payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contentRef": "ans://conteudo/orientacao-portabilidade",
        "sourcePublicationRef": "https://gov.br/ans/orientacao-portabilidade",
        "publishedAt": "2026-09-01T00:00:00+00:00",
    }
    payload.update(extra)
    return payload


def test_a_clean_orientation_payload_passes_without_any_record() -> None:
    # Orientation with an explicit pending gap is the shipped state — the guard kills
    # verdict FABRICATION, never orientation content, admission or no admission.
    assert refuse_portability_verdict_payload(_orientation_payload()) is None
    valid_until = _orientation_payload(validUntil="2027-01-01T00:00:00+00:00")
    assert refuse_portability_verdict_payload(valid_until) is None
    assert refuse_portability_verdict_payload({}) is None


@pytest.mark.parametrize("field", sorted(PORTABILITY_VERDICT_FIELDS_PROHIBITED))
def test_each_prohibited_verdict_field_refuses_alone(field: str) -> None:
    with pytest.raises(PortabilityPayloadError) as exc:
        refuse_portability_verdict_payload(_orientation_payload(**{field: "qualquer"}))
    assert exc.value.reason_code == "portability_verdict_fields_prohibited"


def test_the_prohibited_catalog_is_exactly_the_dossier_seven() -> None:
    # Transcribed, not invented: the dossier's Forma-2 `not` clauses. The GAP-CATALOG
    # planned-negative (iv)(b) lists the same clauses minus `cancellationConfirmed`;
    # the binding list is the dossier's — and the stricter one.
    assert PORTABILITY_VERDICT_FIELDS_PROHIBITED == frozenset(  # noqa: SIM300 — constant under test reads first
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


def test_the_full_verdict_payload_refuses_with_one_typed_reason() -> None:
    payload = _orientation_payload(**{field: "x" for field in PORTABILITY_VERDICT_FIELDS_PROHIBITED})
    with pytest.raises(PortabilityPayloadError) as exc:
        refuse_portability_verdict_payload(payload)
    assert exc.value.reason_code == "portability_verdict_fields_prohibited"


@pytest.mark.parametrize("payload", [[], "orientation", 7, None, {1: "x"}])
def test_non_object_payloads_and_non_string_keys_refuse(payload: object) -> None:
    # A portability payload IS a JSON object; anything else is not an orientation payload
    # and refuses rather than passing silently.
    with pytest.raises(PortabilityPayloadError) as exc:
        refuse_portability_verdict_payload(payload)
    assert exc.value.reason_code == "portability_payload_not_object"


def test_a_none_valued_verdict_field_is_still_a_verdict_field() -> None:
    # A null verdict is a verdict slot: the object-shape check passes, the `not` clause
    # fires — a forbidden field never rides on a null value.
    with pytest.raises(PortabilityPayloadError) as exc:
        refuse_portability_verdict_payload({"compatibilityVerdict": None})
    assert exc.value.reason_code == "portability_verdict_fields_prohibited"


def test_reg006_cancellation_fields_never_pass_the_guard() -> None:
    # REG-006: an origin-plan cancellation can never be requested, confirmed or
    # protocolized through a portability payload — the cancellation clauses of the
    # dossier's `not` list are exactly what the guard kills.
    for field in ("cancellationRequested", "cancellationConfirmed", "protocolRef"):
        with pytest.raises(PortabilityPayloadError):
            refuse_portability_verdict_payload({field: True})


def test_the_guard_is_admission_independent_by_signature() -> None:
    # Structural, mold `registered ≠ honored`: the guard takes NO admission, and the
    # record never substitutes the structural clauses.
    parameters = inspect.signature(refuse_portability_verdict_payload).parameters
    assert set(parameters) == {"payload"}
