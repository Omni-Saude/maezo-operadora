"""A6 (GP11 não-contato) registered-suppression admission — negative tests (D-16).

Executable regression for the planned negatives of admission A6 (GAP-CATALOG-A1-A10 §2):
(a) `registered ≠ honored`: a `honrado` clock transition refuses without the honored-list
    evidence (`HonoredListEvidence` — explicit `REGISTRO_HONRADO` route + `sourceReceiptRef`
    + `listPlanRevision`) BEFORE any I/O, and the evidence never substitutes for anything
    else (the store API takes no admission; the A6 record is not the evidence);
(b) store absent/unshaped ⇒ the typed refusal on EVERY tick (UNKNOWN, never zero) is the
    shipped state the runtime keeps by default — and a composition that threads a store
    without the registered A6 admission refuses LOUD at boot
    (`commercial_suppression_admission_invalid`), never a silent serve;
(c) `start_process`/`EffectCall` naming `SP-OP-SUPP-001` stays refused: the key stays OUT
    of both allowlists BY DESIGN (ADR-0016 stays at 15 — the process is started by the
    application plan on an EXTERNAL holder request, never by an agent), while the
    publication-source set keeps it as a SOURCE only;
(d) a record without a computable deadline refuses `DEADLINE_UNCOMPUTABLE` before any I/O
    (re-pin of the shipped clock discipline).
Plus the admission invariants around them: the record requires EXACTLY the dossier pair of
owner roles (`owner (origem)` AND `DPO (origem)` — the owner's acceptance of design inputs
is not the DPO countersignature, so one leg alone never parses); the admitted scope is
exactly the OP20 registry command `suppression.record` (which is NOT one of the 42 frozen
C6-4 commands and names no OP-matrix id); refused/deferred owner decisions never bind;
expired records, foreign admission ids, divergent pins and closed-structure violations
refuse; the A1–A5 records never open the suppression dimension and the A6 record never
opens theirs (both directions, cross-parser); the loader re-validates on every read; a
missing file is absence (`FileNotFoundError`), never a refusal; and the commercial
composition is structurally untouched by A6 (the GP11 surface is the runtime seam, not a
C6-4 command).
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from maezo.gateway.commercial_acceptance_admission import (
    A3_CONTRACT_SHA256,
    parse_acceptance_record_admission,
)
from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256, parse_catalog_source_admission
from maezo.gateway.commercial_coverage_admission import A4_CONTRACT_SHA256, parse_coverage_read_admission
from maezo.gateway.commercial_sales_admission import A2_CONTRACT_SHA256, parse_sales_session_admission
from maezo.gateway.commercial_support_admission import A5_CONTRACT_SHA256, parse_support_handoff_admission
from maezo.gateway.commercial_suppression_admission import (
    A6_ADMITTED_COMMANDS,
    A6_CONTRACT_SHA256,
    A6_OWNER_ROLES,
    SuppressionAdmissionError,
    parse_suppression_admission,
    read_suppression_admission,
)
from maezo.gateway.vendor_suppression import (
    EscalationClock,
    HonoredListEvidence,
    PostgresSuppressionStore,
    SuppressionError,
    SuppressionRefusalReason,
    SuppressionStore,
    suppression_ref_of,
)
from maezo.runtime.worker_runtime.service import _expected_worker_topics, register_default_workers
from maezo.tools.workers.harness import WorkerBpmnError, WorkerHarness
from maezo.tools.workers.suppression import (
    ERR_SUPP_ROUTING_UNAVAILABLE,
    ERR_SUPP_SUBJECT_UNRESOLVED,
    SUPPRESSION_VERIFY_TOPIC,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
_T0 = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def a6_record(**changes):
    record = dict(
        {
            "schema": "omni-commercial-admission-record.v1",
            "admission_id": "A6",
            "decision": "admitted",
            "owners": ["owner (origem)", "DPO (origem)"],
            "decided_at": "2026-10-24T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "unlocks": "P6.4 (honra); NR-08",
            "admitted_commands": sorted(A6_ADMITTED_COMMANDS),
            "contract_pin": {
                "contract_id": "omni-commercial.proposed.v1",
                "revision": "C6-4",
                "sha256": A6_CONTRACT_SHA256,
            },
            "evidence": {
                "object_url": "https://amh.invalid/admissions/a6-object.json",
                "object_sha256": "c" * 64,
                "origin_git_sha": "d" * 40,
            },
        }
    )
    record.update(changes)
    return json.dumps(record).encode()


def a6_admission():
    return parse_suppression_admission(a6_record(), now=NOW)


# ----------------------------------------------------------------------------------
# Fakes — store do Protocol, engine que prova "antes de qualquer I/O", task do tópico
# ----------------------------------------------------------------------------------


class _IOMarkerError(Exception):
    """Raised by the fake engine the moment ANY I/O would be attempted."""


class _NoIoEngine:
    """Engine-shaped fake whose FIRST I/O attempt raises — the guard under test must fire first."""

    def __init__(self) -> None:
        self.dialect = SimpleNamespace(name="postgresql")
        self.echo = False

    def begin(self):  # pragma: no cover - executed only if the guard under test failed
        raise _IOMarkerError("set_status reached I/O — the honored-evidence guard did not fire first")

    def connect(self):  # pragma: no cover
        raise _IOMarkerError("store reached I/O — the guard under test did not fire first")


class _FakeTask:
    def __init__(self, variables: dict[str, Any] | None = None) -> None:
        self.id = "a6-probe-task"
        self.variables: dict[str, Any] = variables or {}
        self.business_key = "SUPP-a6-probe"


class _FakeStore:
    """Minimal `SuppressionStore` — resolves the canonical digest like migration 0021 would."""

    def __init__(self, record: Any) -> None:
        self.record = record
        self.resolved_with: tuple[str, str] | None = None

    async def resolve(self, tenant: str, suppression_ref: str):
        self.resolved_with = (tenant, suppression_ref)
        return self.record


def _suppression_record():
    clock = EscalationClock.compute(_T0)
    from maezo.gateway.vendor_suppression import SuppressionRecord

    return SuppressionRecord(
        tenant="t-1",
        suppression_ref=suppression_ref_of("subj-1", "chan-A"),
        subject_ref="subj-1",
        contact_channel="chan-A",
        canal="whatsapp",
        categoria_sujeito="vendedor",
        t_recorded=clock.t_recorded,
        t_lembrete=clock.t_lembrete,
        t_escalonado=clock.t_escalonado,
        t_deadline=clock.t_deadline,
        status="pendente",
        motivo="oposicao art. 18 §2o",
    )


def _verify_handler(harness: WorkerHarness):
    return harness._handlers[SUPPRESSION_VERIFY_TOPIC]  # type: ignore[attr-defined]  # raw-handler registry, test introspection


# ----------------------------------------------------------------------------------
# (mold) Loader — o par EXATO de owners é a perna DPO; sem as duas pernas não há estado
# ----------------------------------------------------------------------------------


def test_valid_record_parses_and_round_trips() -> None:
    admission = a6_admission()
    assert admission.admission_id == "A6"
    assert admission.contract_sha256 == A6_CONTRACT_SHA256
    assert admission.owners == ("owner (origem)", "DPO (origem)")
    assert set(admission.owners) == A6_OWNER_ROLES
    assert admission.valid_until > NOW


def test_record_without_the_dpo_role_never_parses() -> None:
    # The owner's acceptance of design inputs is NOT the countersignature (dossier §Owners
    # externos) — a one-legged record would make the whole mechanism injectable without the
    # DPO decision, so the pair is exact.
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(a6_record(owners=["owner (origem)"]), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


def test_record_without_the_owner_role_never_parses() -> None:
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(a6_record(owners=["DPO (origem)"]), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize(
    "owners",
    [
        [],
        ["owner"],
        ["DPO"],
        ["case owner", "channel owner"],
        ["owner (origem)", "DPO (origem)", "case owner"],
        ["owner (origem)", "owner (origem)"],
        ["owner (origem)", 7],
    ],
)
def test_foreign_or_partial_owner_sets_refuse(owners: object) -> None:
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(a6_record(owners=owners), now=NOW)
    assert exc.value.reason_code == "admission_owner_invalid"


@pytest.mark.parametrize("decision", ["refused", "deferred"])
def test_refused_and_deferred_owner_decisions_never_bind(decision: str) -> None:
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(a6_record(decision=decision), now=NOW)
    assert exc.value.reason_code == "admission_not_admitted"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update({"decision": "ADMITTED"}),
        lambda record: record.pop("decision"),
        lambda record: record.update({"schema": "omni-commercial-admission-record.v2"}),
        lambda record: record.update({"admission_id": "A5"}),
        lambda record: record.update({"admission_id": "a6"}),
        lambda record: record.update({"unlocks": "P6.4 (honra)"}),
        lambda record: record.update({"unlocks": "NR-08"}),
        lambda record: record.update({"unlocks": "P6.4 (honra), NR-08"}),
        lambda record: record.pop("admitted_commands"),
        lambda record: record.update({"extra_field": "x"}),
    ],
    ids=[
        "decision-case",
        "decision-missing",
        "schema",
        "foreign-id",
        "id-case",
        "unlocks-honra-only",
        "unlocks-nr-only",
        "unlocks-comma",
        "scope-missing",
        "extra-field",
    ],
)
def test_structural_divergences_refuse_as_record_invalid(mutate) -> None:
    record = json.loads(a6_record())
    mutate(record)
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    "commands",
    [
        [],
        ["suppression.record", "work.list"],
        ["support.handoff"],
        ["acceptance.record"],
        ["OP20"],
        ["suppression.record "],
        "suppression.record",
    ],
)
def test_admitted_scope_is_exactly_the_op20_command(commands: object) -> None:
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(a6_record(admitted_commands=commands), now=NOW)
    assert exc.value.reason_code == "admission_scope_invalid"


def test_the_op20_command_is_not_a_frozen_contract_command() -> None:
    # GAP-NO-CONTACT IS the absence: 0 suppression commands among the 42 (`x-commands`). The
    # admitted scope names the OP20 registry command, never a C6-4 one.
    contract = json.loads((Path("tests/candidate_artifacts/commercial/contract-c6-4.json")).read_bytes())
    commands = set(contract["x-commands"])
    assert len(commands) == 42
    assert not any("suppression" in command for command in commands)
    assert A6_ADMITTED_COMMANDS.isdisjoint(commands)


def test_pin_divergent_refuses_loud() -> None:
    record = json.loads(a6_record())
    record["contract_pin"]["sha256"] = "e" * 64
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


@pytest.mark.parametrize("field", ["contract_id", "revision", "sha256"])
def test_pin_fields_are_closed(field: str) -> None:
    record = json.loads(a6_record())
    record["contract_pin"].pop(field)
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_contract_pin_extra_field_refuses() -> None:
    record = json.loads(a6_record())
    record["contract_pin"]["note"] = "x"
    with pytest.raises(SuppressionAdmissionError):
        parse_suppression_admission(json.dumps(record).encode(), now=NOW)


def test_expired_record_refuses() -> None:
    record = json.loads(a6_record())
    record["valid_until"] = "2026-10-25T00:00:00+00:00"
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_expired"


def test_decided_in_the_future_refuses() -> None:
    record = json.loads(a6_record())
    record["decided_at"] = "2026-10-26T00:00:00+00:00"
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [("decided_at", "2026-10-24T00:00:00"), ("valid_until", "2099-01-01T00:00:00")],
)
def test_naive_timestamps_never_admit(field: str, value: str) -> None:
    record = json.loads(a6_record())
    record[field] = value
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(json.dumps(record).encode(), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


@pytest.mark.parametrize("field", ["decided_at", "valid_until"])
def test_non_timestamp_refuses(field: str) -> None:
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(a6_record(**{field: "not-a-timestamp"}), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_naive_now_is_a_caller_bug_not_a_refusal() -> None:
    with pytest.raises(ValueError, match="commercial_admission_clock_invalid"):
        parse_suppression_admission(a6_record(), now=datetime(2026, 10, 25, 12, 0))


@pytest.mark.parametrize(
    "evidence",
    [
        {"object_url": "http://amh.invalid/a6.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a6.json ", "object_sha256": "c" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a6.json", "object_sha256": "c" * 63, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a6.json", "object_sha256": "g" * 64, "origin_git_sha": "d" * 40},
        {"object_url": "https://amh.invalid/a6.json", "object_sha256": "c" * 64, "origin_git_sha": "d" * 39},
        {"object_url": "https://amh.invalid/a6.json", "object_sha256": "c" * 64, "origin_git_sha": ""},
    ],
)
def test_evidence_shape_is_closed(evidence: dict) -> None:
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(a6_record(evidence=evidence), now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_invalid_json_and_non_object_refuse() -> None:
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(b"{not json", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(b"[1,2]", now=NOW)
    assert exc.value.reason_code == "admission_record_invalid"


def test_missing_file_is_absence_not_refusal() -> None:
    with pytest.raises(FileNotFoundError):
        read_suppression_admission("/nonexistent/a6-admission-record.json", now=NOW)


def test_loader_revalidates_on_every_read_never_caches(tmp_path: Path) -> None:
    path = tmp_path / "A6-ADMISSION-RECORD.json"
    path.write_bytes(a6_record())
    assert read_suppression_admission(path, now=NOW).admission_id == "A6"
    record = json.loads(a6_record())
    record["contract_pin"]["sha256"] = "e" * 64
    path.write_bytes(json.dumps(record).encode())
    with pytest.raises(SuppressionAdmissionError) as exc:
        read_suppression_admission(path, now=NOW)
    assert exc.value.reason_code == "admission_pin_divergent"


def test_pin_family_is_one_freeze_across_a1_a6_and_the_codec() -> None:
    from maezo.adapters.amh.commercial import CANDIDATE_SCHEMA_SHA256

    assert A6_CONTRACT_SHA256 == A1_CONTRACT_SHA256 == A2_CONTRACT_SHA256
    assert A6_CONTRACT_SHA256 == A3_CONTRACT_SHA256 == A4_CONTRACT_SHA256 == A5_CONTRACT_SHA256
    assert A6_CONTRACT_SHA256 == CANDIDATE_SCHEMA_SHA256


# ----------------------------------------------------------------------------------
# Independência A1–A6 nos dois sentidos (cross-parser) + superfície comercial intocada
# ----------------------------------------------------------------------------------


def _a1_to_a5_records():
    from tests.unit.test_commercial_admission_a1 import a1_record
    from tests.unit.test_commercial_admission_a2 import a2_record
    from tests.unit.test_commercial_admission_a3 import a3_record
    from tests.unit.test_commercial_admission_a4 import a4_record
    from tests.unit.test_commercial_admission_a5 import a5_record

    return [
        ("A1", a1_record()),
        ("A2", a2_record()),
        ("A3", a3_record()),
        ("A4", a4_record()),
        ("A5", a5_record()),
    ]


@pytest.mark.parametrize(("admission_id", "record_bytes"), _a1_to_a5_records())
def test_a1_a5_records_never_open_the_suppression_dimension(admission_id: str, record_bytes: bytes) -> None:
    with pytest.raises(SuppressionAdmissionError) as exc:
        parse_suppression_admission(record_bytes, now=NOW)
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
    ],
    ids=["A1", "A2", "A3", "A4", "A5"],
)
def test_the_a6_record_never_opens_a1_a5(parse) -> None:
    with pytest.raises(Exception) as exc:
        parse(a6_record())
    assert type(exc.value).__name__.endswith("AdmissionError")
    assert not isinstance(exc.value, SuppressionAdmissionError)


def test_the_commercial_composition_has_no_a6_surface() -> None:
    # A6 lives on the GP11 runtime seam, never on a C6-4 command: the commercial composition
    # grows no parameter for it (zero regression on the A1–A5 gate matrix by construction).
    from maezo.gateway.commercial_admission import (
        compose_commercial_gateway,
        compose_public_catalog_gateway,
    )

    for compose in (compose_commercial_gateway, compose_public_catalog_gateway):
        parameters = set(inspect.signature(compose).parameters)
        assert parameters.isdisjoint({"suppression_admission", "suppression_store"})


# ----------------------------------------------------------------------------------
# (b) Gate de comando no runtime — sem registro ⇒ fail-closed existente; loja sem
# registro ⇒ boot loud; registro nunca fabrica fonte
# ----------------------------------------------------------------------------------


def _fresh_harness() -> WorkerHarness:
    return WorkerHarness(_NoIoTransport(), worker_id="a6-probe")


class _NoIoTransport:
    def complete(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("nenhum teste A6 completa tarefa — os handlers recusam ou resolvem")


def test_default_composition_never_threads_the_store() -> None:
    harness = _fresh_harness()
    register_default_workers(harness)
    with pytest.raises(WorkerBpmnError) as exc:
        _run(
            _verify_handler(harness)(
                _FakeTask({"tenant_id": "t-1", "subject_ref": "s", "contact_channel": "c"})
            )
        )
    assert exc.value.error_code == ERR_SUPP_SUBJECT_UNRESOLVED


def test_route_stays_a_separate_fail_closed_surface_by_default() -> None:
    harness = _fresh_harness()
    register_default_workers(harness)
    route = harness._handlers["vendor.suppression.route"]  # type: ignore[attr-defined]  # test introspection
    with pytest.raises(WorkerBpmnError) as exc:
        _run(route(_FakeTask({"tenant_id": "t-1"})))
    assert exc.value.error_code == ERR_SUPP_ROUTING_UNAVAILABLE


def test_store_without_the_registered_admission_refuses_loud_at_boot() -> None:
    harness = _fresh_harness()
    with pytest.raises(ValueError, match="commercial_suppression_admission_invalid"):
        register_default_workers(harness, suppression_store=_FakeStore(_suppression_record()))
    # The guard fires BEFORE any registration: a composition bug never half-boots.
    assert harness.registered_topics == []


def test_a_foreign_admission_object_never_threads_the_store() -> None:
    harness = _fresh_harness()
    with pytest.raises(ValueError, match="commercial_suppression_admission_invalid"):
        register_default_workers(
            harness,
            suppression_admission=SimpleNamespace(admission_id="A5"),  # type: ignore[arg-type]
            suppression_store=_FakeStore(_suppression_record()),
        )
    assert harness.registered_topics == []


def test_registered_admission_threads_the_seam_and_the_handler_resolves() -> None:
    harness = _fresh_harness()
    store = _FakeStore(_suppression_record())
    register_default_workers(harness, suppression_admission=a6_admission(), suppression_store=store)
    outcome = _run(
        _verify_handler(harness)(
            _FakeTask({"tenant_id": "t-1", "subject_ref": "subj-1", "contact_channel": "chan-A"})
        )
    )
    assert store.resolved_with == ("t-1", suppression_ref_of("subj-1", "chan-A"))
    assert outcome["subject_resolved"] is True
    assert outcome["t_deadline_iso"] == _suppression_record().t_deadline.isoformat()


def test_admission_without_a_store_keeps_the_per_tick_refusal() -> None:
    # The record opens the SOURCE seam only — it never fabricates a source.
    harness = _fresh_harness()
    register_default_workers(harness, suppression_admission=a6_admission())
    with pytest.raises(WorkerBpmnError) as exc:
        _run(
            _verify_handler(harness)(
                _FakeTask({"tenant_id": "t-1", "subject_ref": "s", "contact_channel": "c"})
            )
        )
    assert exc.value.error_code == ERR_SUPP_SUBJECT_UNRESOLVED


def test_the_gate_changes_seams_never_topics() -> None:
    default = _fresh_harness()
    register_default_workers(default)
    wired = _fresh_harness()
    register_default_workers(wired, suppression_admission=a6_admission(), suppression_store=_FakeStore(None))
    assert set(default.registered_topics) == set(wired.registered_topics)
    # The readiness probe derives from the SAME default composition — no seam, byte-identical.
    assert frozenset(_expected_worker_topics()) == frozenset(default.registered_topics)


# ----------------------------------------------------------------------------------
# (a) registered ≠ honored — evidência da operação de lista honrada, antes de qualquer I/O
# ----------------------------------------------------------------------------------


def _evidence(**changes: object) -> HonoredListEvidence:
    values: dict[str, object] = {
        "route": "REGISTRO_HONRADO",
        "source_receipt_ref": "rcpt-src-1",
        "list_plan_revision": "list-plan-r7",
    }
    values.update(changes)
    return HonoredListEvidence.model_validate(values)


def _refuse_reason(call) -> SuppressionRefusalReason:
    with pytest.raises(SuppressionError) as exc:
        _run(call())
    return exc.value.reason


def test_honrado_without_evidence_refuses_before_any_io() -> None:
    store = PostgresSuppressionStore(_NoIoEngine())  # type: ignore[arg-type]
    reason = _refuse_reason(
        lambda: store.set_status("t-1", suppression_ref_of("subj-1", "chan-A"), "honrado", "baixa na lista")
    )
    assert reason is SuppressionRefusalReason.CONTRACT_MISMATCH


def test_honrado_with_complete_evidence_passes_the_guard_into_io() -> None:
    store = PostgresSuppressionStore(_NoIoEngine())  # type: ignore[arg-type]
    # The fake engine raises `_IOMarkerError`, which the store wraps as SOURCE_UNAVAILABLE: reaching
    # that reason (and NOT CONTRACT_MISMATCH) proves the honored-evidence guard passed.
    reason = _refuse_reason(
        lambda: store.set_status(
            "t-1",
            suppression_ref_of("subj-1", "chan-A"),
            "honrado",
            "baixa na lista",
            honored_evidence=_evidence(),
        )
    )
    assert reason is SuppressionRefusalReason.SOURCE_UNAVAILABLE


def test_honrado_with_a_non_model_evidence_object_refuses_before_any_io() -> None:
    store = PostgresSuppressionStore(_NoIoEngine())  # type: ignore[arg-type]
    # FAIL-1 (blind Tier-2 gate): `is not None` alone was defeatable by duck-typed evidence
    # (dict/`object()`/bare string) that crossed into I/O and would mark the record honored
    # without a valid receipt/revision. The shape guard is the refusal itself — only a REAL
    # `HonoredListEvidence` instance passes.
    reason = _refuse_reason(
        lambda: store.set_status(
            "t-1",
            suppression_ref_of("subj-1", "chan-A"),
            "honrado",
            "baixa na lista",
            honored_evidence={"route": "QUALQUER"},  # type: ignore[arg-type]
        )
    )
    assert reason is SuppressionRefusalReason.CONTRACT_MISMATCH


def test_evidence_on_a_non_honrado_transition_is_contract_drift() -> None:
    store = PostgresSuppressionStore(_NoIoEngine())  # type: ignore[arg-type]
    reason = _refuse_reason(
        lambda: store.set_status(
            "t-1",
            suppression_ref_of("subj-1", "chan-A"),
            "escalado",
            "escala ao encarregado",
            honored_evidence=_evidence(),
        )
    )
    assert reason is SuppressionRefusalReason.CONTRACT_MISMATCH


def test_blank_motivo_still_refuses_first() -> None:
    store = PostgresSuppressionStore(_NoIoEngine())  # type: ignore[arg-type]
    reason = _refuse_reason(
        lambda: store.set_status("t-1", suppression_ref_of("subj-1", "chan-A"), "honrado", "   ")
    )
    assert reason is SuppressionRefusalReason.CONTRACT_MISMATCH


@pytest.mark.parametrize(
    "changes",
    [
        {"route": "RECLAMACAO_ENCARREGADO"},
        {"route": "REGISTRO_HONRADO "},
        {"route": None},
        {"source_receipt_ref": ""},
        {"source_receipt_ref": "   "},
        {"source_receipt_ref": None},
        {"list_plan_revision": ""},
        {"list_plan_revision": None},
    ],
)
def test_honored_evidence_shape_is_closed(changes: dict) -> None:
    values: dict[str, object] = {
        "route": "REGISTRO_HONRADO",
        "source_receipt_ref": "rcpt-src-1",
        "list_plan_revision": "list-plan-r7",
    }
    values.update(changes)
    with pytest.raises(ValueError):
        HonoredListEvidence.model_validate(values)


def test_evidence_structure_is_closed_to_extra_fields() -> None:
    with pytest.raises(ValueError):
        HonoredListEvidence.model_validate(
            {
                "route": "REGISTRO_HONRADO",
                "source_receipt_ref": "rcpt-src-1",
                "list_plan_revision": "list-plan-r7",
                "honor_state": "honored",
            }
        )


def test_the_store_takes_no_admission_the_record_is_not_the_evidence() -> None:
    # `registered ≠ honored` is structural, not admission-conditional: even an admitted A6
    # record never substitutes the honored-list evidence.
    parameters = inspect.signature(PostgresSuppressionStore.set_status).parameters
    assert not any("admission" in name for name in parameters)
    assert "honored_evidence" in parameters


def test_the_protocol_carries_the_evidence_keyword() -> None:
    protocol_parameters = inspect.signature(SuppressionStore.set_status).parameters
    implementation_parameters = inspect.signature(PostgresSuppressionStore.set_status).parameters
    assert "honored_evidence" in protocol_parameters
    assert set(protocol_parameters) == set(implementation_parameters)


# ----------------------------------------------------------------------------------
# (c) A key fora das allowlists É o desenho — nunca perna de flip; (d) deadline fail-closed
# ----------------------------------------------------------------------------------


def test_the_pep_universe_drops_the_suppression_key_even_when_declared() -> None:
    from maezo.gateway.effect_pep import AgentCapabilities

    caps = AgentCapabilities.of(principal="p", tools=(), process_keys=("SP-OP-SUPP-001",))
    assert caps.process_keys == frozenset()
    assert caps.allows_process_key("SP-OP-SUPP-001") is False


def test_both_process_allowlists_stay_fifteen_without_the_key() -> None:
    from maezo.gateway.effect_pep import KNOWN_PROCESS_KEYS as PEP_KEYS
    from maezo.tools.process_allowlist import KNOWN_PROCESS_KEYS as ADR0016_KEYS

    assert len(PEP_KEYS) == 15 and "SP-OP-SUPP-001" not in PEP_KEYS
    assert len(ADR0016_KEYS) == 15 and "SP-OP-SUPP-001" not in ADR0016_KEYS
    assert PEP_KEYS == ADR0016_KEYS


def test_the_publication_source_set_keeps_the_key_as_source_only() -> None:
    # Started by the application plan on an EXTERNAL holder request — never by an agent.
    from maezo.platform.privacy.program_publication import _PUBLICATION_PROCESS_KEYS

    assert "SP-OP-SUPP-001" in _PUBLICATION_PROCESS_KEYS


def test_naive_clock_refuses_deadline_uncomputable_before_any_io() -> None:
    with pytest.raises(SuppressionError) as exc:
        EscalationClock.compute(datetime(2026, 10, 7, 10, 0))
    assert exc.value.reason is SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE


def test_negative_business_days_refuse_deadline_uncomputable() -> None:
    from maezo.gateway.vendor_suppression import add_business_days

    with pytest.raises(SuppressionError) as exc:
        add_business_days(_T0, -1)
    assert exc.value.reason is SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE


# ----------------------------------------------------------------------------------


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
