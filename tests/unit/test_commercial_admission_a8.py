"""A8 (GAP-ATTRIBUTION-COMMISSION, NR-09) registered-commission admission — negative tests (D-16).

Executable regression for the planned negatives of admission A8 (GAP-CATALOG-A1-A10 §2,
transcribed from the pending dossier; the dossier stays OUT of `src/**` — A-INDEX rule 1,
pinned here by name):
(a) without A8 admitted, NO surface calculates or displays comissão, score, payout,
    ranking patrocinado or comparação de concorrentes ("ausência que não é flag", molde
    A9): the shipped state is ABSENT-by-absence — no record ships anywhere in the
    repository, the commercial composition grows no commission parameter, the `src/`
    tree carries ZERO "commission"/"comissão" occurrences beyond this loader itself, and
    the frozen contract carries ZERO commission/attribution menções (0 hits each over
    the contract text; none of the 42 `x-commands` names `commission.settle`,
    `value_transfer.register`, `opportunity.attribute` or any payout base; `x-status`
    keeps `human_acts`/`tasy_write_homologation` `NOT_ESTABLISHED` and `production`
    `NO_GO`; OP17/OP19 stay `candidate_not_live` OUTSIDE the contract);
(b) the closed portal stub `commission-policy.proposed.v1` remains a stub: the typed
    mirror of its `additionalProperties: false` + four `const`s refuses a payload that
    names the family with a mutated const (fabricated ratification/monetary content) or
    an extra value field, and refuses a stray `monetaryContent`/`ratification` field
    with any value other than the stub's markers — admission-INDEPENDENT (holds with no
    record, exactly as with a registered out-of-scope decision), and a clean stub
    payload passes (the stub is a legitimate proposed placeholder while sem valores);
(c) the only name-adjacent substrates in `src/` are NON-commission by kind and stay
    untouched: governance/custody "attribution" (ADR-0007 decision attribution, error-
    layer attribution, declared-capability governance) and the OP14 channel attribute
    `attribution_basis_ref` of the vendor projection (the A10 NR-09 dimension:
    membership/canal — an opaque Ref, never a rate, base or generating fact). Isolation
    pinned as a closed tree set; the loader imports none of them.
Plus the admission invariants around them: the out-of-scope decision
(`admitted_out_of_scope`, the dossier's literal decision value) is the ONLY admitted
state; the Forma-1 policy record refuses `admission_scope_invalid` BY CONSTRUCTION at
this pin (zero commission commands in the frozen contract — the dossier's successor-
schema rule); the owner trio is EXACTLY `comercial` + `legal` + `DPO` (no leg derives
from the others); the admitted command set must be EXACTLY empty; refused/deferred never
bind; expired, foreign-id, divergent-pin and closed-structure violations refuse; the
A1–A7 records never open the commission dimension and the A8 record never opens theirs
(both directions, cross-parser); the loader re-validates on every read; a missing file
is absence (`FileNotFoundError`), never a refusal; and `commercial_admission.py` is
structurally untouched (A8 has no C6-4 surface and no runtime seam — mold R8/R9).
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
from maezo.gateway.commercial_commission_admission import (
    A8_ADMITTED_COMMANDS,
    A8_CONTRACT_SHA256,
    A8_OUT_OF_SCOPE_DECISION,
    A8_OWNER_ROLES,
    A8_UNLOCKS,
    COMMISSION_POLICY_STUB_FIELDS,
    COMMISSION_POLICY_STUB_MONETARY_ABSENT,
    COMMISSION_POLICY_STUB_RATIFICATION,
    COMMISSION_POLICY_STUB_SCHEMA,
    COMMISSION_POLICY_STUB_STATUS,
    CommissionAdmissionError,
    CommissionPayloadError,
    parse_commission_admission,
    read_commission_admission,
    refuse_commission_policy_payload,
)
from maezo.gateway.commercial_coverage_admission import A4_CONTRACT_SHA256, parse_coverage_read_admission
from maezo.gateway.commercial_portability_admission import A7_CONTRACT_SHA256, parse_portability_admission
from maezo.gateway.commercial_sales_admission import A2_CONTRACT_SHA256, parse_sales_session_admission
from maezo.gateway.commercial_support_admission import A5_CONTRACT_SHA256, parse_support_handoff_admission
from maezo.gateway.commercial_suppression_admission import A6_CONTRACT_SHA256, parse_suppression_admission

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def a8_record(**changes):
    """A VALID out-of-scope record — the only admitted state the A8 parser can produce."""
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A8",
            "decision": A8_OUT_OF_SCOPE_DECISION,
            "owners": ["comercial", "legal", "DPO"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": A8_UNLOCKS,
            "admitted_commands": [],
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A8_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a8-out-of-scope-decision.json",
                "object_sha256": "c" * 64,
                "origin_git_sha": "d" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a8_admission():
    return parse_commission_admission(a8_record(), now=NOW)


def _clean_stub_payload(**extra: Any) -> dict[str, Any]:
    """The EXACT portal stub `commission-policy.proposed.v1` — proposed, sem valores."""
    payload: dict[str, Any] = {
        "schemaVersion": COMMISSION_POLICY_STUB_SCHEMA,
        "status": COMMISSION_POLICY_STUB_STATUS,
        "policyRef": "ans://politica/comissao-proposta",
        "sourceAuthorityRef": "ans://autoridade/fonte-comercial",
        "calculationBaseRef": "ans://base/calculo-proposto",
        "attributionPolicyRef": "ans://politica/atribuicao-proposta",
        "generatingFactRefs": ["ans://fato-gerador/proposto"],
        "ratification": COMMISSION_POLICY_STUB_RATIFICATION,
        "monetaryContent": COMMISSION_POLICY_STUB_MONETARY_ABSENT,
    }
    payload.update(extra)
    return payload


# ----------------------------------------------------------------------------------
# (mold) Loader — o trio EXATO comercial+legal+DPO é a decisão NR-09; fora-de-escopo é
# o ÚNICO estado admitido
# ----------------------------------------------------------------------------------


def test_the_out_of_scope_record_parses_and_round_trips() -> None:
    admission = a8_admission()
    assert admission.admission_id == "A8"
    assert admission.contract_sha256 == A8_CONTRACT_SHA256
    assert admission.owners == ("comercial", "legal", "DPO")
    assert set(admission.owners) == A8_OWNER_ROLES
    assert admission.valid_until > NOW


def test_the_out_of_scope_decision_is_the_only_admitted_state() -> None:
    # Forma 1 (política ratificada, `decision == "admitted"`) against THIS pin:
    # scope-invalid BY CONSTRUCTION — even with the exact owner trio, valid timestamps
    # and the exact (empty) scope. The dossier is literal: ratification "habilita a
    # evolução do stub NR-09 por sucessor versionado", preenchimento "somente por
    # sucessor versionado com diff revisado". No owner wording can make an empty command
    # universe carry "escopo exato (quais eventos, qual base, qual disclosure)".
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(decision="admitted"), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_forma_1_refusal_is_decision_level_not_shape_level() -> None:
    # The Forma-1 refusal fires BEFORE owner validation on purpose: no owner set can
    # launder a policy-form record through this pin. A later successor package re-pins
    # the loader; this one never grows a policy branch by owner request alone.
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(decision="admitted", owners=["legal"]), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


@pytest.mark.parametrize(
    "decision",
    [
        "admitted_out_of_scope ",
        " admitted_out_of_scope",
        "ADMITTED_OUT_OF_SCOPE",
        "out_of_scope",
        "admitted_ceiling_orientation_only",
        "admitted_out_of_scope_forever",
        "",
    ],
)
def test_foreign_decision_values_are_structural_refusals(decision: str) -> None:
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(decision=decision), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize("decision", ["refused", "deferred"])
def test_refused_and_deferred_owner_decisions_never_bind(decision: str) -> None:
    # A NO-GO keeps commission a PENDING gap — never a silently-registered out-of-scope
    # limit ("a lacuna permanece aberta como pendência explícita").
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(decision=decision), now=NOW)
    assert exc.value.reason_code == "admission_not_admitted"


def test_record_without_the_dpo_leg_never_parses() -> None:
    # NR-09 is explicitly a comercial+legal+DPO decision: no leg derives from the
    # others' acceptance, so a two-legged record would make the mechanism injectable
    # without the third decision — the trio is exact (mold A6, three roles).
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(owners=["comercial", "legal"]), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        ["comercial"],
        ["legal"],
        ["DPO"],
        ["produto", "legal"],
        ["case owner", "channel owner"],
        ["owner (origem)", "DPO (origem)"],
        ["comercial", "legal"],
        ["comercial", "DPO"],
        # The trio is EXACT — no fourth authority rides an out-of-scope decision.
        ["comercial", "legal", "DPO", "fonte própria de atribuição"],
        ["comercial", "comercial", "legal", "DPO"],
        ["comercial", "legal", "dpo"],
        ["comercial", "legal", 7],
    ],
)
def test_foreign_or_partial_owner_sets_refuse(owners: object) -> None:
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(owners=owners), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "commands",
    [
        ["commission.settle"],
        ["value_transfer.register"],
        ["opportunity.attribute"],
        ["suppression.record"],
        ["support.handoff"],
        ["commission.settle", "value_transfer.register"],
        "commission.settle",
    ],
)
def test_the_decision_admits_exactly_nothing(commands: object) -> None:
    # Any non-empty scope names a command the frozen contract does not carry (0
    # commission commands — pinned below): the decision's scope is EXACTLY empty.
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(admitted_commands=commands), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_the_admitted_scope_constant_is_exactly_empty() -> None:
    assert frozenset() == A8_ADMITTED_COMMANDS
    assert len(A8_ADMITTED_COMMANDS) == 0


# ----------------------------------------------------------------------------------
# (a)+(c) O contrato congelado NÃO tem comissão/attribution — a ausência É a lacuna
# ----------------------------------------------------------------------------------


def test_the_frozen_contract_has_zero_commission_commands() -> None:
    contract = json.loads((Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_bytes())
    commands = set(contract["x-commands"])
    assert len(commands) == 42
    assert not any("commission" in command.lower() for command in commands)
    assert not any("attribution" in command.lower() for command in commands)
    assert not any("settle" in command.lower() for command in commands)
    assert not any("value_transfer" in command.lower() for command in commands)
    assert not any("opportunity" in command.lower() for command in commands)
    assert A8_ADMITTED_COMMANDS.isdisjoint(commands)


def test_the_frozen_contract_text_never_mentions_commission_or_attribution() -> None:
    raw = (Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_text()
    assert "commission" not in raw.lower()
    assert "attribution" not in raw.lower()


def test_the_frozen_contract_keeps_the_owner_acts_not_established() -> None:
    # `x-status` literal: the commission dimension is gated by owner acts the contract
    # itself declares NOT_ESTABLISHED, with production NO_GO — nothing to bind at this pin.
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
        lambda record: record.update({"admission_id": "A7"}),
        lambda record: record.update({"admission_id": "a8"}),
        lambda record: record.update({"unlocks": "stub NR-09 (decisao)"}),
        lambda record: record.update({"unlocks": "NR-09"}),
        lambda record: record.update({"unlocks": "P6.5"}),
        lambda record: record.pop("decision"),
        lambda record: record.pop("admitted_commands"),
        lambda record: record.update({"extra_field": "x"}),
    ],
    ids=[
        "schema",
        "foreign-id-a7",
        "id-case",
        "unlocks-unaccented",
        "unlocks-bare",
        "unlocks-p65",
        "decision-missing",
        "scope-missing",
        "extra-field",
    ],
)
def test_structural_divergences_refuse_as_record_invalid(mutate) -> None:
    record = json.loads(a8_record())
    mutate(record)
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_pin_divergent_refuses_loud() -> None:
    record = json.loads(a8_record())
    record["contract_pin"]["sha256"] = "e" * 64
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


@pytest.mark.parametrize("field", ["contract_id", "revision", "sha256"])
def test_pin_fields_are_closed(field: str) -> None:
    record = json.loads(a8_record())
    record["contract_pin"].pop(field)
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_contract_pin_extra_field_refuses() -> None:
    record = json.loads(a8_record())
    record["contract_pin"]["note"] = "x"
    with pytest.raises(CommissionAdmissionError):
        parse_commission_admission(json.dumps(record).encode(), now=NOW)


def test_expired_record_refuses() -> None:
    record = json.loads(a8_record())
    record["valid_until"] = "2026-10-25T00:00:00+00:00"
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_expired"


def test_decided_in_the_future_refuses() -> None:
    record = json.loads(a8_record())
    record["decided_at"] = "2026-10-26T00:00:00+00:00"
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [("decided_at", "2026-10-24T00:00:00"), ("valid_until", "2099-01-01T00:00:00")],
)
def test_naive_timestamps_never_admit(field: str, value: str) -> None:
    record = json.loads(a8_record())
    record[field] = value
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize("field", ["decided_at", "valid_until"])
def test_non_timestamp_refuses(field: str) -> None:
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(**{field: "not-a-timestamp"}), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_naive_now_is_a_caller_bug_not_a_refusal() -> None:
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_commission_admission(a8_record(), now=datetime(2026, 10, 25, 12, 0))


@pytest.mark.parametrize(
    "evidence",
    [
        {"object_url": "http://amh.invalid/a8.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a8.json ", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a8.json", "object_sha256": "c" * 63, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a8.json", "object_sha256": "g" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a8.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 39},
        {"object_url": "https://amh.invalid/a8.json", "object_sha256": "c" * 64, "origin_git_sha": ""},
    ],
)
def test_evidence_shape_is_closed(evidence: dict) -> None:
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(a8_record(evidence=evidence), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_invalid_json_and_non_object_refuse() -> None:
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(b"{not json", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(b"[1,2]", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_missing_file_is_absence_not_refusal() -> None:
    with pytest.raises(FileNotFoundError):
        read_commission_admission("/nonexistent/a8-admission-record.json", now=NOW)


def test_loader_revalidates_on_every_read_never_caches(tmp_path: Path) -> None:
    path = tmp_path / "A8-ADMISSION-RECORD.json"
    path.write_bytes(a8_record())
    assert read_commission_admission(path, now=NOW).admission_id == "A8"
    record = json.loads(a8_record())
    record["contract_pin"]["sha256"] = "e" * 64
    path.write_bytes(json.dumps(record).encode())
    with pytest.raises(CommissionAdmissionError) as exc:
        read_commission_admission(path, now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


def test_pin_family_is_one_freeze_across_a1_a8_and_the_codec() -> None:
    from maezo.adapters.amh.commercial import CANDIDATE_SCHEMA_SHA256

    assert A8_CONTRACT_SHA256 == A1_CONTRACT_SHA256 == A2_CONTRACT_SHA256
    assert A8_CONTRACT_SHA256 == A3_CONTRACT_SHA256 == A4_CONTRACT_SHA256
    assert A8_CONTRACT_SHA256 == A5_CONTRACT_SHA256 == A6_CONTRACT_SHA256
    assert A8_CONTRACT_SHA256 == A7_CONTRACT_SHA256
    assert A8_CONTRACT_SHA256 == CANDIDATE_SCHEMA_SHA256


# ----------------------------------------------------------------------------------
# Independência A1–A8 nos dois sentidos (cross-parser) + superfície comercial intocada
# ----------------------------------------------------------------------------------


def _a1_to_a7_records():
    from tests.unit.test_commercial_admission_a1 import a1_record
    from tests.unit.test_commercial_admission_a2 import a2_record
    from tests.unit.test_commercial_admission_a3 import a3_record
    from tests.unit.test_commercial_admission_a4 import a4_record
    from tests.unit.test_commercial_admission_a5 import a5_record
    from tests.unit.test_commercial_admission_a6 import a6_record
    from tests.unit.test_commercial_admission_a7 import a7_record

    return [
        ("A1", a1_record()),
        ("A2", a2_record()),
        ("A3", a3_record()),
        ("A4", a4_record()),
        ("A5", a5_record()),
        ("A6", a6_record()),
        ("A7", a7_record()),
    ]


@pytest.mark.parametrize(("admission_id", "record_bytes"), _a1_to_a7_records())
def test_a1_a7_records_never_open_the_commission_dimension(admission_id: str, record_bytes: bytes) -> None:
    with pytest.raises(CommissionAdmissionError) as exc:
        parse_commission_admission(record_bytes, now=NOW)
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
    ],
    ids=["A1", "A2", "A3", "A4", "A5", "A6", "A7"],
)
def test_the_a8_record_never_opens_a1_a7(parse) -> None:
    # The out-of-scope decision value is not even in the A1–A7 decision vocabulary: a
    # foreign parser refuses it structurally, never by admission.
    with pytest.raises(Exception) as exc:
        parse(a8_record())
    assert type(exc.value).__name__.endswith("AdmissionError")
    assert not isinstance(exc.value, CommissionAdmissionError)


def test_the_commercial_composition_has_no_a8_surface() -> None:
    # A8 is ABSENT — no C6-4 command, no runtime seam: the commercial composition grows
    # no parameter for it (zero regression on the A1–A7 gate matrix by construction).
    from maezo.gateway.commercial_admission import (
        compose_commercial_gateway,
        compose_public_catalog_gateway,
    )

    for compose in (compose_commercial_gateway, compose_public_catalog_gateway):
        parameters = set(inspect.signature(compose).parameters)
        assert parameters.isdisjoint({"commission_admission", "commission_policy", "commission_source"})


# ----------------------------------------------------------------------------------
# (a) A ausência É o estado — nenhum registro no repositório, nenhuma superfície nova,
#     as colisões de nome isoladas (governança/custódia e canal OP14, nunca comissão)
# ----------------------------------------------------------------------------------


def test_no_a8_admission_record_ships_in_the_repository() -> None:
    root = Path(__file__).resolve().parents[2]
    for base in ("docs", "src", "config", "tests", "spec"):
        assert not list((root / base).rglob("A8-ADMISSION-RECORD.json")), base


def test_the_pending_dossier_never_appears_in_src() -> None:
    # A-INDEX rule 1: "Dossiê status pending nunca aparece em código (src/**)".
    root = Path(__file__).resolve().parents[2]
    for path in (root / "src" / "maezo").rglob("*.py"):
        assert "A8-GAP-ATTRIBUTION-COMISSAO" not in path.read_text(errors="replace"), path


def test_src_tree_keeps_zero_commission_vocabulary_beyond_this_loader() -> None:
    # The ONLY "commission"/"comissão"/"comissionamento" occurrences in `src/` are this
    # loader's: the package adds no commission calculation/display surface — 0 hits
    # before it (GAP-CATALOG §2/A8(ii) — "0 hits de 'commission' em src/"), and the pin
    # makes any future appearance a conscious, gate-visible act.
    root = Path(__file__).resolve().parents[2] / "src" / "maezo"
    allowed = {(root / "gateway" / "commercial_commission_admission.py").resolve()}
    found: dict[Path, list[int]] = {}
    for path in root.rglob("*.py"):
        lines = [
            number
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1)
            if "commission" in line.lower() or "comissão" in line.lower() or "comissionamento" in line.lower()
        ]
        if lines:
            found[path.resolve()] = lines
    assert set(found) == allowed, found


def test_src_tree_attribution_vocabulary_is_exactly_the_name_adjacent_non_commission_set() -> None:
    # The ONLY "attribution"/"atribuição" occurrences in `src/` python are five
    # pre-existing NON-commission substrates plus the A8 loader and the A10 loader:
    # ADR-0007 decision attribution (contas), error-layer attribution (effect_pep),
    # declared-capability governance attribution (a2a_composition) and the OP14 channel
    # attribute `attribution_basis_ref` (vendor projection — the A10 NR-09 canal
    # dimension), whose stub transcription carries the portal stub's `attributionPolicy`/
    # `attributionBasisRef` field names (R12 disclosure — mold of the R11 numeral fix in
    # this same file). None of them is a rate, base, generating fact or payout; the pin
    # makes any new occurrence a conscious, gate-visible act.
    root = Path(__file__).resolve().parents[2] / "src" / "maezo"
    allowed = {
        (root / "tools" / "workers" / "contas.py").resolve(),
        (root / "gateway" / "effect_pep.py").resolve(),
        (root / "runtime" / "agent_runtime" / "a2a_composition.py").resolve(),
        (root / "gateway" / "capabilities" / "vendor_journey" / "projection.py").resolve(),
        (root / "portal" / "api" / "app.py").resolve(),
        (root / "gateway" / "commercial_commission_admission.py").resolve(),
        (root / "gateway" / "commercial_vendor_admission.py").resolve(),
    }
    found: dict[Path, list[int]] = {}
    for path in root.rglob("*.py"):
        lines = [
            number
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1)
            if "attribution" in line.lower() or "atribuição" in line.lower()
        ]
        if lines:
            found[path.resolve()] = lines
    assert set(found) == allowed, found


def test_the_name_adjacent_substrates_are_untouched_and_never_consumed_as_substrate() -> None:
    # Governance attribution keeps its exact shape (ADR-0007: the decision is in the
    # analyst's CLEAR prefix); the vendor channel keeps its closed OP14 attribute
    # allowlist with `attribution_basis_ref` as an opaque Ref — this package renames,
    # remaps or consumes nothing of either.
    from maezo.gateway.capabilities.vendor_journey.projection import CHANNEL_ATTRIBUTE_KEYS

    assert CHANNEL_ATTRIBUTE_KEYS == frozenset(  # noqa: SIM300 — constant under test reads first
        {"producer_ref", "channel_class", "attribution_basis_ref"}
    )
    # Isolation: the A8 loader shares NO import with any name-adjacent module.
    loader_path = Path(__file__).resolve().parents[2] / "src" / "maezo" / "gateway"
    loader = (loader_path / "commercial_commission_admission.py").read_text()
    for forbidden in (
        "from maezo.tools.workers.contas",
        "from maezo.gateway.effect_pep",
        "from maezo.runtime.agent_runtime.a2a_composition",
        "from maezo.gateway.capabilities.vendor_journey",
    ):
        assert forbidden not in loader


def test_op17_op19_are_not_runtime_surface_anywhere() -> None:
    # OP17 `commission.settle` / OP19 `value_transfer.register` stay `candidate_not_live`
    # (GP5–GP10): no python module under `src/` carries them beyond this loader's
    # docstring — there is no settle/value-transfer effect, worker, route or seam.
    root = Path(__file__).resolve().parents[2] / "src" / "maezo"
    allowed = {(root / "gateway" / "commercial_commission_admission.py").resolve()}
    found = {
        path.resolve()
        for path in root.rglob("*.py")
        if "value_transfer" in path.read_text(errors="replace").lower()
    }
    assert found == allowed, found


# ----------------------------------------------------------------------------------
# (b) Guarda estrutural do stub fechado — INDEPENDENTE de admissão
# ----------------------------------------------------------------------------------


def test_a_clean_stub_payload_passes_without_any_record() -> None:
    # The proposed stub (sem valores) is the legitimate placeholder: the guard kills
    # policy FABRICATION, never the stub shape itself, admission or no admission.
    assert refuse_commission_policy_payload(_clean_stub_payload()) is None
    assert refuse_commission_policy_payload({}) is None
    stray_refs = {"policyRef": "ans://ref", "sourceAuthorityRef": "ans://src"}
    assert refuse_commission_policy_payload(stray_refs) is None


def test_the_stub_catalog_is_exactly_the_portal_nine_fields() -> None:
    # Transcribed, not invented: `commission-policy.proposed.v1`
    # `additionalProperties: false` over EXACTLY these nine properties.
    assert COMMISSION_POLICY_STUB_FIELDS == frozenset(  # noqa: SIM300 — constant under test reads first
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
    assert COMMISSION_POLICY_STUB_SCHEMA == "commission-policy.proposed.v1"
    assert COMMISSION_POLICY_STUB_STATUS == "PROPOSED_STUB_NO_ADMITTED_SOURCE"
    assert COMMISSION_POLICY_STUB_RATIFICATION == "REQUIRED_FROM_EXTERNAL_OWNERS_COMMERCIAL_LEGAL_DPO"
    assert COMMISSION_POLICY_STUB_MONETARY_ABSENT == "ABSENT_UNTIL_RATIFIED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "RATIFIED"),
        ("status", "ACTIVE"),
        ("status", "proposed_stub_no_admitted_source"),
        ("ratification", "RATIFIED_BY_OWNERS"),
        ("ratification", "REQUIRED"),
        ("monetaryContent", "BRL 100.00"),
        ("monetaryContent", "RATIFIED"),
        ("monetaryContent", "ABSENT_UNTIL_ratified"),
    ],
    ids=[
        "status-ratified",
        "status-active",
        "status-case",
        "ratification-ratified",
        "ratification-short",
        "monetary-value",
        "monetary-ratified",
        "monetary-case",
    ],
)
def test_a_mutated_stub_const_refuses(field: str, value: str) -> None:
    with pytest.raises(CommissionPayloadError) as exc:
        refuse_commission_policy_payload(_clean_stub_payload(**{field: value}))
    assert exc.value.reason_code == "commission_policy_stub_const_mutated"


def test_a_null_const_is_still_a_mutation() -> None:
    with pytest.raises(CommissionPayloadError) as exc:
        refuse_commission_policy_payload(_clean_stub_payload(monetaryContent=None))
    assert exc.value.reason_code == "commission_policy_stub_const_mutated"


@pytest.mark.parametrize(
    "extra",
    [
        {"commissionRate": "0.15"},
        {"payoutAmount": 100},
        {"generatingFactRefs": ["ans://fato-gerador/proposto"], "tableRow": {"rate": "0.15"}},
    ],
    ids=["rate-field", "amount-field", "table-row-field"],
)
def test_an_extra_value_field_beyond_the_stub_shape_refuses(extra: dict) -> None:
    # `additionalProperties: false`: any field beyond the stub's nine IS the value the
    # stub forbids adding before ratification.
    with pytest.raises(CommissionPayloadError) as exc:
        refuse_commission_policy_payload(_clean_stub_payload(**extra))
    assert exc.value.reason_code == "commission_policy_stub_shape_invalid"


@pytest.mark.parametrize("field", ["status", "policyRef", "monetaryContent"])
def test_a_family_named_payload_missing_a_stub_field_refuses(field: str) -> None:
    payload = _clean_stub_payload()
    payload.pop(field)
    with pytest.raises(CommissionPayloadError) as exc:
        refuse_commission_policy_payload(payload)
    assert exc.value.reason_code == "commission_policy_stub_shape_invalid"


def test_family_membership_is_declared_by_the_marker_never_inferred() -> None:
    # Dropping `schemaVersion` drops the family claim itself: the remaining eight
    # marker-valued fields fabricate nothing (the shape clause binds payloads OF the
    # family, and membership is declared, not inferred).
    payload = _clean_stub_payload()
    payload.pop("schemaVersion")
    assert refuse_commission_policy_payload(payload) is None


def test_the_full_fabrication_refuses_with_one_typed_reason() -> None:
    payload = _clean_stub_payload(status="RATIFIED", monetaryContent="BRL 100.00", ratification="RATIFIED")
    with pytest.raises(CommissionPayloadError) as exc:
        refuse_commission_policy_payload(payload)
    assert exc.value.reason_code == "commission_policy_stub_const_mutated"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("monetaryContent", "BRL 100.00"),
        ("monetaryContent", {"rate": "0.15"}),
        ("monetaryContent", None),
        ("ratification", "RATIFIED_BY_OWNERS"),
        ("ratification", True),
    ],
    ids=["monetary-str", "monetary-object", "monetary-null", "ratification-str", "ratification-bool"],
)
def test_a_stray_monetary_or_ratification_field_refuses_without_family_marker(
    field: str, value: object
) -> None:
    # Both names are transcribed from the stub and their only pre-successor values are
    # its consts: re-declaring them with new values is fabrication regardless of the
    # family marker — a ratified policy binds only through a successor versioned schema.
    with pytest.raises(CommissionPayloadError) as exc:
        refuse_commission_policy_payload({field: value})
    if field == "monetaryContent":
        assert exc.value.reason_code == "commission_monetary_content_fabricated"
    else:
        assert exc.value.reason_code == "commission_ratification_fabricated"


def test_stray_marker_valued_fields_pass_without_the_family_marker() -> None:
    # The absent-marker/required-marker values are the stub's own: carrying them alone
    # fabricates nothing.
    monetary_absent = {"monetaryContent": COMMISSION_POLICY_STUB_MONETARY_ABSENT}
    ratification_marker = {"ratification": COMMISSION_POLICY_STUB_RATIFICATION}
    assert refuse_commission_policy_payload(monetary_absent) is None
    assert refuse_commission_policy_payload(ratification_marker) is None


@pytest.mark.parametrize("payload", [[], "commission", 7, None, {1: "x"}])
def test_non_object_payloads_and_non_string_keys_refuse(payload: object) -> None:
    # A commission-policy payload IS a JSON object; anything else is not a stub payload
    # and refuses rather than passing silently.
    with pytest.raises(CommissionPayloadError) as exc:
        refuse_commission_policy_payload(payload)
    assert exc.value.reason_code == "commission_payload_not_object"


def test_the_guard_is_admission_independent_by_signature() -> None:
    # Structural, mold `registered ≠ honored`: the guard takes NO admission, and the
    # record never substitutes the structural clauses.
    parameters = inspect.signature(refuse_commission_policy_payload).parameters
    assert set(parameters) == {"payload"}
