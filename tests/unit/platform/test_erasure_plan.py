"""ADR-0029 dark build: the per-layer erasure plan seam + the inert dry-run.

What these tests lock, in one line each:

* the shipped artifact is a DRAFT and every human value in it is a machine-detectable
  placeholder — nobody can mistake it for a decision;
* ratifying it is a DATA change: a tmp-path fixture with the SAME shape and different VALUES
  loads successfully with no code edit anywhere;
* a non-dry mode is refused in BOTH states, with two different reasons, and the refusal
  happens before any probe is issued;
* the dry-run reports counts only, never a titular reference and never row contents;
* the code enumeration and the artifact cover exactly the same relations, and the enumeration
  covers every table the migration chain creates (non-vacuity).
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.platform.lifecycle import erasure_plan as ep

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS = _REPO_ROOT / "src" / "maezo" / "platform" / "migrations" / "versions"
_SHIPPED_PLAN = _REPO_ROOT / ep.PLAN_RELPATH
_CODEOWNERS = _REPO_ROOT / ".github" / "CODEOWNERS"

# A reference value that must never appear in a report, a rendered line or a log field.
_SENTINEL_REF = "pat-sentinel-must-not-leak-0001"


def _shipped_raw() -> dict[str, Any]:
    return dict(yaml.safe_load(_SHIPPED_PLAN.read_text(encoding="utf-8")))


def _ratified_yaml(**overrides: Any) -> dict[str, Any]:
    """The shipped DRAFT with every placeholder replaced — the ratification act, in data."""
    data = _shipped_raw()
    data.update(
        {
            "status": "RATIFICADO",
            "ratificado": True,
            "dpo_review": "APPROVED",
            "dpo_reviewer": "DPO fixture reviewer",
            "dpo_review_date": "2026-01-01",
            "evidence_ref": "EL-FIXTURE-0001",
            "notes": "fixture ratification, unit test only",
            "review_packet": "docs/reviews/adr-0029-erasure-packet.md",
        }
    )
    for layer in data["camadas"]:
        layer["decisao_dpo"] = "RETER_COM_BASE_LEGAL"
        layer["base_legal"] = "LGPD art. 16 I (fixture value, not a legal opinion)"
        layer["retencao"] = "5 anos (fixture value)"
    data.update(overrides)
    return data


def _write_plan(tmp_path: Path, data: Mapping[str, Any], name: str = "plan.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(dict(data), allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def ratified_plan(tmp_path: Path) -> ep.ErasurePlan:
    return ep.load_erasure_plan(_write_plan(tmp_path, _ratified_yaml()))


# ---------------------------------------------------------------------------
# The shipped artifact is a DRAFT, and provably so
# ---------------------------------------------------------------------------


def test_shipped_artifact_exists_where_the_module_says_it_does() -> None:
    assert _SHIPPED_PLAN.is_file(), f"{ep.PLAN_RELPATH} must ship in the repo"


def test_shipped_artifact_is_a_draft_and_refuses_to_load() -> None:
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan(_SHIPPED_PLAN)
    assert excinfo.value.reason == ep.REASON_NOT_RATIFIED


def test_default_resolution_finds_the_shipped_draft_and_still_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var set: the loader must find the shipped artifact, not report `file_not_found`."""
    monkeypatch.delenv(ep.PLAN_PATH_ENV, raising=False)
    assert ep.resolve_plan_path() == _SHIPPED_PLAN
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan()
    assert excinfo.value.reason == ep.REASON_NOT_RATIFIED


def test_every_human_value_in_the_shipped_artifact_is_a_detectable_placeholder() -> None:
    """No retention value and no legal basis may be a real-looking string in the DRAFT."""
    data = _shipped_raw()
    assert data["ratificado"] is False
    assert data["dpo_review"] == "PENDENTE"
    for field in ("dpo_reviewer", "dpo_review_date", "evidence_ref", "notes"):
        assert ep._looks_like_placeholder(str(data[field])) is not None, field
    for layer in data["camadas"]:
        for field in ("decisao_dpo", "base_legal", "retencao"):
            marker = ep._looks_like_placeholder(str(layer[field]))
            assert marker is not None, f"{layer['tabela']}.{field} is not a placeholder"


def test_shipped_artifact_records_no_decision_from_the_closed_vocabulary() -> None:
    """A DRAFT must not accidentally contain a real verdict token anywhere."""
    for layer in _shipped_raw()["camadas"]:
        assert layer["decisao_dpo"] not in ep.DECISION_VOCABULARY


# ---------------------------------------------------------------------------
# Ratification is a DATA change — proven against a tmp-path fixture
# ---------------------------------------------------------------------------


def test_a_ratified_fixture_loads_with_no_code_change(tmp_path: Path) -> None:
    """Same file shape, different values: the flip needs no edit under src/."""
    plan = ep.load_erasure_plan(_write_plan(tmp_path, _ratified_yaml()))
    assert len(plan) == len(ep.PERSISTENCE_LAYERS)
    assert plan.dpo_reviewer == "DPO fixture reviewer"
    for layer in ep.PERSISTENCE_LAYERS:
        decision = plan.get(layer.tabela)
        assert decision is not None, layer.tabela
        assert decision.decisao_dpo in ep.DECISION_VOCABULARY


def test_the_env_var_route_also_needs_no_code_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_plan(tmp_path, _ratified_yaml())
    monkeypatch.setenv(ep.PLAN_PATH_ENV, str(path))
    assert ep.resolve_plan_path() == path.resolve()
    assert ep.load_erasure_plan().dpo_reviewer == "DPO fixture reviewer"


def test_env_override_pointing_nowhere_refuses_rather_than_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silently reading a different artifact than the operator named would be worse."""
    monkeypatch.setenv(ep.PLAN_PATH_ENV, str(tmp_path / "absent.yaml"))
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan()
    assert excinfo.value.reason == ep.REASON_FILE_NOT_FOUND


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ({"ratificado": "true"}, ep.REASON_NOT_RATIFIED),
        ({"ratificado": 1}, ep.REASON_NOT_RATIFIED),
        ({"dpo_review": "PENDENTE"}, ep.REASON_DPO_REVIEW_PENDING),
        # F-1: `status` is a THIRD independent switch. ratificado=true + dpo_review=APPROVED
        # alone must NOT be enough while status is still left as DRAFT.
        ({"status": "DRAFT"}, ep.REASON_STATUS_NOT_RATIFICADO),
        ({"dpo_reviewer": "PENDENTE-IDENTIDADE-DO-DPO"}, ep.REASON_PLACEHOLDER_VALUE),
        ({"evidence_ref": "TBD"}, ep.REASON_PLACEHOLDER_VALUE),
        ({"camadas": []}, ep.REASON_EMPTY),
        ({"camadas": "nope"}, ep.REASON_INVALID_SCHEMA),
    ],
)
def test_a_half_filled_ratification_is_not_a_ratification(
    tmp_path: Path, mutation: dict[str, Any], expected_reason: str
) -> None:
    path = _write_plan(tmp_path, _ratified_yaml(**mutation))
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan(path)
    assert excinfo.value.reason == expected_reason


def test_a_missing_required_field_refuses(tmp_path: Path) -> None:
    data = _ratified_yaml()
    del data["evidence_ref"]
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan(_write_plan(tmp_path, data))
    assert excinfo.value.reason == ep.REASON_INVALID_SCHEMA


def test_a_free_text_verdict_is_refused(tmp_path: Path) -> None:
    """A verdict nobody can enumerate is a verdict no mechanism could ever honour."""
    data = _ratified_yaml()
    data["camadas"][0]["decisao_dpo"] = "apagar tudo"
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan(_write_plan(tmp_path, data))
    assert excinfo.value.reason == ep.REASON_UNKNOWN_DECISION


def test_a_placeholder_legal_basis_survives_no_ratification(tmp_path: Path) -> None:
    """The DPO gate must block on VALUES: one un-decided legal basis refuses the whole plan."""
    data = _ratified_yaml()
    data["camadas"][3]["base_legal"] = "PENDENTE-BASE-LEGAL-DPO-JURIDICO"
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan(_write_plan(tmp_path, data))
    assert excinfo.value.reason == ep.REASON_PLACEHOLDER_VALUE


def test_a_plan_that_drops_a_relation_is_refused(tmp_path: Path) -> None:
    data = _ratified_yaml()
    dropped = data["camadas"].pop()["tabela"]
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan(_write_plan(tmp_path, data))
    assert excinfo.value.reason == ep.REASON_LAYER_SET_MISMATCH
    assert dropped in str(excinfo.value)


def test_a_plan_naming_an_unknown_relation_is_refused(tmp_path: Path) -> None:
    data = _ratified_yaml()
    extra = dict(data["camadas"][0])
    extra["tabela"] = "some_table_this_module_never_heard_of"
    data["camadas"].append(extra)
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan(_write_plan(tmp_path, data))
    assert excinfo.value.reason == ep.REASON_LAYER_SET_MISMATCH


def test_a_duplicate_relation_is_refused(tmp_path: Path) -> None:
    data = _ratified_yaml()
    data["camadas"].append(dict(data["camadas"][0]))
    with pytest.raises(ep.ErasurePlanUnavailableError) as excinfo:
        ep.load_erasure_plan(_write_plan(tmp_path, data))
    assert excinfo.value.reason == ep.REASON_INVALID_SCHEMA


# ---------------------------------------------------------------------------
# Fail-closed: no non-dry mode, in either state
# ---------------------------------------------------------------------------


def test_execute_mode_is_refused_while_the_plan_is_unratified() -> None:
    with pytest.raises(ep.ErasureExecutionRefusedError) as excinfo:
        ep.assert_dry_run_only(ep.ErasureRunMode.EXECUTE, None)
    assert excinfo.value.reason == ep.REASON_PLAN_UNRATIFIED


def test_execute_mode_is_still_refused_once_the_plan_is_ratified(
    ratified_plan: ep.ErasurePlan,
) -> None:
    """Ratification is a governance act, never a trigger — the reason changes, not the verdict."""
    with pytest.raises(ep.ErasureExecutionRefusedError) as excinfo:
        ep.assert_dry_run_only(ep.ErasureRunMode.EXECUTE, ratified_plan)
    assert excinfo.value.reason == ep.REASON_EXECUTION_MECHANISM_ABSENT


def test_dry_run_mode_is_the_only_mode_that_passes_the_gate(
    ratified_plan: ep.ErasurePlan,
) -> None:
    ep.assert_dry_run_only(ep.ErasureRunMode.DRY_RUN, None)
    ep.assert_dry_run_only(ep.ErasureRunMode.DRY_RUN, ratified_plan)


def test_execute_mode_refuses_before_any_probe_is_issued() -> None:
    """The counter must never be reached — the refusal precedes the work, not follows it."""
    calls: list[str] = []

    def counter(statement: str, params: Mapping[str, str]) -> int:
        calls.append(statement)
        return 1

    with pytest.raises(ep.ErasureExecutionRefusedError):
        ep.dry_run(
            subject_ref=_SENTINEL_REF,
            subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
            tenant_id="t1",
            counter=counter,
            mode=ep.ErasureRunMode.EXECUTE,
        )
    assert calls == []


# ---------------------------------------------------------------------------
# F-6: a blank subject_ref must never reach a real counter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_subject_ref_with_a_counter_is_refused(blank: str) -> None:
    """A count issued against an empty reference would read as an ordinary COUNTED result —
    indistinguishable from a genuine zero — instead of surfacing the missing input it actually
    is. The refusal must happen before the counter is ever reached.
    """
    calls: list[str] = []

    def counter(statement: str, params: Mapping[str, str]) -> int:
        calls.append(statement)
        return 1

    with pytest.raises(ValueError, match="blank"):
        ep.dry_run(
            subject_ref=blank,
            subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
            tenant_id="t1",
            counter=counter,
        )
    assert calls == []


def test_a_blank_subject_ref_without_a_counter_is_not_an_error() -> None:
    """No counter means nothing is ever probed regardless of subject_ref — the CLI's own
    inert path (no counter, subject_ref defaulting to "" when the env var is unset) must
    keep working exactly as before.
    """
    report = ep.dry_run(
        subject_ref="",
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
    )
    assert all(f.status is not ep.LayerFindingStatus.COUNTED for f in report.findings)


# ---------------------------------------------------------------------------
# F-7: the counter can never receive anything but a SELECT count(*) probe
# ---------------------------------------------------------------------------


def test_a_non_select_count_statement_is_refused_at_probe_time() -> None:
    """Runtime belt-and-suspenders alongside the static AST guard in test_lifecycle.py: even
    a `PersistenceLayer` built outside `PERSISTENCE_LAYERS` — the `_layers` override seam
    exists for exactly this — cannot reach the counter with a non-count statement.
    """
    poisoned = ep.PersistenceLayer(
        camada="trabalho",
        tabela="agent_memory",
        migracao="test-only, not a real migration citation",
        identificacao="test-only",
        resolucao=ep.IdentityResolution.PONTE_AUSENTE,
        ordem=1,
        subject_column="fhir_patient_id",
        count_statement="DELETE FROM agent_memory WHERE fhir_patient_id = :subject_ref",
    )
    calls: list[str] = []

    def counter(statement: str, params: Mapping[str, str]) -> int:
        calls.append(statement)
        return 1

    with pytest.raises(AssertionError):
        ep.dry_run(
            subject_ref=_SENTINEL_REF,
            subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
            tenant_id="t1",
            counter=counter,
            _layers=[poisoned],
        )
    assert calls == [], "the poisoned statement must never reach the counter"


def test_the_layers_override_seam_still_works_for_a_well_formed_layer() -> None:
    """The `_layers` seam itself is not the thing under test above — prove it still probes a
    single, well-formed override correctly (only the poisoned SHAPE is refused).
    """
    only_agent_memory = next(layer for layer in ep.PERSISTENCE_LAYERS if layer.tabela == "agent_memory")
    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
        counter=lambda statement, params: 5,
        _layers=[only_agent_memory],
    )
    assert len(report.findings) == 1
    assert report.findings[0].status is ep.LayerFindingStatus.COUNTED
    assert report.findings[0].row_count == 5


# ---------------------------------------------------------------------------
# The dry-run reports counts, and only counts
# ---------------------------------------------------------------------------


def test_a_pseudonym_reference_can_count_nothing_anywhere() -> None:
    """The missing titular_pseudo_id->fhir_patient_id bridge, made visible instead of assumed."""
    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.TITULAR_PSEUDO_ID,
        tenant_id="t1",
        counter=lambda statement, params: 7,
    )
    assert all(f.status is not ep.LayerFindingStatus.COUNTED for f in report.findings)
    bridge = [
        f.layer.tabela
        for f in report.findings
        if f.status is ep.LayerFindingStatus.NOT_COUNTED_IDENTITY_BRIDGE_ABSENT
    ]
    assert bridge == ["agent_memory", "agent_memory.embedding", "erasure_log"]
    assert report.counted_rows == 0
    assert len(report.uncounted_layers) == len(ep.PERSISTENCE_LAYERS)


def test_a_patient_reference_counts_exactly_the_subject_bearing_relations() -> None:
    seen: list[tuple[str, Mapping[str, str]]] = []

    def counter(statement: str, params: Mapping[str, str]) -> int:
        seen.append((statement, dict(params)))
        return 3

    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
        counter=counter,
    )
    counted = [f.layer.tabela for f in report.findings if f.status is ep.LayerFindingStatus.COUNTED]
    assert counted == ["agent_memory", "agent_memory.embedding", "erasure_log"]
    assert report.counted_rows == 9
    # The reference reaches a BIND parameter and never the statement text.
    for statement, params in seen:
        assert _SENTINEL_REF not in statement
        assert params["subject_ref"] == _SENTINEL_REF
        assert params["tenant_id"] == "t1"


def test_no_count_is_fabricated_when_no_counter_is_supplied() -> None:
    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
    )
    no_counter = [
        f.layer.tabela for f in report.findings if f.status is ep.LayerFindingStatus.NOT_COUNTED_NO_COUNTER
    ]
    assert no_counter == ["agent_memory", "agent_memory.embedding", "erasure_log"]
    assert all(f.row_count is None for f in report.findings)


def test_a_failing_counter_reports_failure_and_withholds_the_message() -> None:
    """A driver error can echo bind values; the report must not carry them onward."""

    def counter(statement: str, params: Mapping[str, str]) -> int:
        raise RuntimeError(f"boom while binding {params['subject_ref']}")

    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
        counter=counter,
    )
    failed = [f for f in report.findings if f.status is ep.LayerFindingStatus.COUNT_FAILED]
    assert len(failed) == 3
    for finding in failed:
        assert "RuntimeError" in finding.detail
        assert _SENTINEL_REF not in finding.detail
    assert report.counted_rows == 0


def test_the_report_never_carries_the_subject_reference() -> None:
    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
        counter=lambda statement, params: 2,
    )
    assert _SENTINEL_REF not in repr(report)
    assert _SENTINEL_REF not in ep.render_report(report, "UNRATIFIED (not_ratified)")
    assert not hasattr(report, "subject_ref")


def test_findings_carry_the_pending_sentinel_until_a_plan_is_ratified() -> None:
    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
    )
    assert report.plan_ratified is False
    assert {f.decisao_dpo for f in report.findings} == {ep.DECISION_PENDING}


def test_findings_carry_the_ratified_decision_once_a_plan_loads(
    ratified_plan: ep.ErasurePlan,
) -> None:
    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
        plan=ratified_plan,
    )
    assert report.plan_ratified is True
    assert {f.decisao_dpo for f in report.findings} == {"RETER_COM_BASE_LEGAL"}


def test_findings_are_reported_in_declared_order_of_operations() -> None:
    report = ep.dry_run(
        subject_ref=_SENTINEL_REF,
        subject_ref_kind=ep.SubjectRefKind.FHIR_PATIENT_ID,
        tenant_id="t1",
    )
    orders = [f.layer.ordem for f in report.findings]
    assert orders == sorted(orders)
    assert len(set(orders)) == len(ep.PERSISTENCE_LAYERS)


# ---------------------------------------------------------------------------
# The SQL surface is `SELECT count(*)` and nothing else
# ---------------------------------------------------------------------------


def test_every_probe_is_a_count_and_binds_its_values() -> None:
    destructive = ("DELETE", "DROP", "TRUNCATE", "UPDATE", "INSERT", "ALTER")
    probes = [layer.count_statement for layer in ep.PERSISTENCE_LAYERS if layer.count_statement]
    assert probes, "non-vacuity: at least one relation must be probeable"
    for statement in probes:
        assert statement.startswith("SELECT count(*)")
        upper = statement.upper()
        for verb in destructive:
            assert verb not in upper, f"{verb} appears in a probe: {statement}"
        assert ":tenant_id" in statement and ":subject_ref" in statement


def test_only_relations_with_a_subject_column_carry_a_probe() -> None:
    for layer in ep.PERSISTENCE_LAYERS:
        if layer.count_statement is None:
            continue
        assert layer.subject_column == "fhir_patient_id", layer.tabela
        assert layer.resolucao is ep.IdentityResolution.PONTE_AUSENTE, layer.tabela


# ---------------------------------------------------------------------------
# Anti-drift + non-vacuity
# ---------------------------------------------------------------------------


def test_artifact_and_code_enumerate_the_same_relations() -> None:
    """The two halves of the design cannot drift: one set, asserted in both directions."""
    from_artifact = {layer["tabela"] for layer in _shipped_raw()["camadas"]}
    assert from_artifact == set(ep.KNOWN_TABLES)


# F-4: broadened past the exact literal "CREATE TABLE IF NOT EXISTS " — case-insensitive,
# "IF NOT EXISTS" optional, identifier optionally double-quoted — PLUS a second, independent
# pass for Alembic's own `op.create_table("name", ...)` API. Every migration today uses raw
# SQL via `op.execute(...)`, so the second pass is currently vacuous on THIS chain, but a
# future migration reaching for the ORM-level API must not go unseen by this scan.
_CREATE_TABLE_SQL_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?",
    re.IGNORECASE,
)
_OP_CREATE_TABLE_RE = re.compile(r"op\.create_table\(\s*[\"'](\w+)[\"']")


def test_the_enumeration_covers_every_table_the_migration_chain_creates() -> None:
    """Non-vacuity: a future migration adding a table fails this until it is enumerated."""
    created: set[str] = set()
    for source in sorted(_MIGRATIONS.glob("0*.py")):
        text = source.read_text("utf-8")
        created.update(_CREATE_TABLE_SQL_RE.findall(text))
        created.update(_OP_CREATE_TABLE_RE.findall(text))
    assert created, "non-vacuity: the scan must find tables"
    assert created <= set(ep.KNOWN_TABLES), f"unenumerated: {sorted(created - set(ep.KNOWN_TABLES))}"


def test_the_create_table_scan_also_catches_the_op_create_table_api_shape(tmp_path: Path) -> None:
    """Negative control, on a SCRATCH file OUTSIDE the real migrations directory: a table
    created via `op.create_table(...)` (the Alembic ORM-level API, unused today but not
    forbidden) must be found by the same scan that finds raw-SQL CREATE TABLE.
    """
    scratch = tmp_path / "0099_hypothetical.py"
    scratch.write_text(
        "def upgrade() -> None:\n"
        "    op.create_table(\n"
        '        "a_future_relation_this_module_never_heard_of",\n'
        '        sa.Column("id", sa.Integer(), primary_key=True),\n'
        "    )\n",
        encoding="utf-8",
    )
    text = scratch.read_text("utf-8")
    found = set(_CREATE_TABLE_SQL_RE.findall(text)) | set(_OP_CREATE_TABLE_RE.findall(text))
    assert found == {"a_future_relation_this_module_never_heard_of"}


def test_the_create_table_scan_catches_case_and_quoting_variants(tmp_path: Path) -> None:
    """Negative control, on a SCRATCH file: lowercase `create table`, no `IF NOT EXISTS`, and
    a double-quoted identifier must all still be found — the old exact-literal match missed
    every one of these shapes.
    """
    scratch = tmp_path / "0098_hypothetical_variants.py"
    scratch.write_text(
        'op.execute("""\n'
        '    create table "another_future_relation" (\n'
        "        id uuid primary key\n"
        "    );\n"
        '""")\n',
        encoding="utf-8",
    )
    text = scratch.read_text("utf-8")
    found = set(_CREATE_TABLE_SQL_RE.findall(text)) | set(_OP_CREATE_TABLE_RE.findall(text))
    assert found == {"another_future_relation"}


def test_each_enumerated_relation_cites_its_source() -> None:
    for layer in ep.PERSISTENCE_LAYERS:
        assert layer.migracao.strip(), layer.tabela
        assert layer.identificacao.strip(), layer.tabela


def test_the_artifact_rows_cite_a_migration_and_carry_a_resolution() -> None:
    vocabulary = {member.value for member in ep.IdentityResolution}
    for layer in _shipped_raw()["camadas"]:
        assert str(layer["migracao"]).strip()
        assert layer["resolucao_identidade"] in vocabulary, layer["tabela"]


# F-3: the SET-equality check above (`test_artifact_and_code_enumerate_the_same_relations`)
# is not enough — a relation could keep the right `tabela` while carrying a
# `resolucao_identidade` or `ordem` the code enumeration disagrees with, and the SET check
# would still pass. Both are pinned per relation here, factored into a reusable helper so the
# negative-control test below can prove the check actually fails closed instead of trusting
# it by inspection.
def _resolution_and_order_drift(camadas: list[dict[str, Any]]) -> list[str]:
    """Return one message per relation where the artifact disagrees with the code
    enumeration on `resolucao_identidade` or `ordem`. Empty means the two halves agree
    exactly, on both fields, for every relation.
    """
    by_tabela = {layer.tabela: layer for layer in ep.PERSISTENCE_LAYERS}
    mismatches: list[str] = []
    for entry in camadas:
        tabela = entry["tabela"]
        layer = by_tabela.get(tabela)
        if layer is None:
            mismatches.append(f"{tabela}: unknown to the code enumeration")
            continue
        if entry["resolucao_identidade"] != layer.resolucao.value:
            mismatches.append(
                f"{tabela}: resolucao_identidade={entry['resolucao_identidade']!r} != "
                f"code {layer.resolucao.value!r}"
            )
        if entry["ordem"] != layer.ordem:
            mismatches.append(f"{tabela}: ordem={entry['ordem']!r} != code {layer.ordem!r}")
    return mismatches


def test_the_artifact_resolution_and_order_match_the_code_exactly() -> None:
    """Anti-drift, per relation and on BOTH fields — not just the relation-name set."""
    mismatches = _resolution_and_order_drift(_shipped_raw()["camadas"])
    assert mismatches == [], mismatches


def test_a_resolution_drift_is_actually_caught_by_the_check_above(tmp_path: Path) -> None:
    """Negative control, on a SCRATCH COPY (never the shipped artifact): flip one relation's
    `resolucao_identidade` and prove `_resolution_and_order_drift` actually fails closed
    instead of passing vacuously. `audit_chain` is `SEM_COLUNA_DE_TITULAR` in the code; this
    mutates the scratch copy to the structurally different `RESOLVIVEL`.
    """
    data = _shipped_raw()
    for layer in data["camadas"]:
        if layer["tabela"] == "audit_chain":
            layer["resolucao_identidade"] = "RESOLVIVEL"
    mutated_path = tmp_path / "mutated-artifact.yaml"
    mutated_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    reloaded = yaml.safe_load(mutated_path.read_text(encoding="utf-8"))

    mismatches = _resolution_and_order_drift(reloaded["camadas"])
    assert any(m.startswith("audit_chain:") and "resolucao_identidade" in m for m in mismatches), mismatches


def test_an_ordem_drift_is_actually_caught_by_the_check_above(tmp_path: Path) -> None:
    """Negative control, on a SCRATCH COPY: flip one relation's `ordem` and prove the same
    check catches an ordering drift too, independent of `resolucao_identidade`.
    """
    data = _shipped_raw()
    for layer in data["camadas"]:
        if layer["tabela"] == "erasure_log":
            layer["ordem"] = 999
    mutated_path = tmp_path / "mutated-ordem.yaml"
    mutated_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    reloaded = yaml.safe_load(mutated_path.read_text(encoding="utf-8"))

    mismatches = _resolution_and_order_drift(reloaded["camadas"])
    assert any(m.startswith("erasure_log:") and "ordem" in m for m in mismatches), mismatches


# ---------------------------------------------------------------------------
# Governance wiring
# ---------------------------------------------------------------------------


def test_the_plan_path_is_codeowners_gated() -> None:
    """Ratifying is a data change with no code path, so the DATA needs the code's reviewer.

    Both the directory line and the explicit file line are required: CODEOWNERS resolves by
    LAST match, so if the directory line is ever narrowed the artifact must not silently lose
    its owner (same reasoning as the `action-approvals.yaml` line above it).
    """
    text = _CODEOWNERS.read_text(encoding="utf-8")
    assert "/spec/policies/retention/" in text
    assert f"/{ep.PLAN_RELPATH}" in text


def test_the_artifact_points_at_the_review_packet_that_exists() -> None:
    packet = _REPO_ROOT / str(_shipped_raw()["review_packet"])
    assert packet.is_file(), f"{packet} must exist — the artifact cites it"


# ---------------------------------------------------------------------------
# The entrypoint, end to end
# ---------------------------------------------------------------------------


def test_the_cli_reports_the_unratified_state_and_has_no_effect() -> None:
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "maezo.platform.lifecycle.erasure_plan", "--tenant", "t1"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "UNRATIFIED (not_ratified)" in result.stdout
    assert ep.DECISION_PENDING in result.stdout
    for layer in ep.PERSISTENCE_LAYERS:
        assert layer.tabela in result.stdout


def test_the_cli_never_takes_a_subject_reference_on_argv() -> None:
    """argv shows up in `ps` and in shell history; the reference is read from the env instead."""
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [
            sys.executable,
            "-m",
            "maezo.platform.lifecycle.erasure_plan",
            "--tenant",
            "t1",
            "--subject-ref",
            _SENTINEL_REF,
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr
