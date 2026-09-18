"""Pure schema invariants and read-only planning in tiny committed Git fixtures.

Constructed operational records are schema specimens, never catalogue or producer
qualification. No real test path is imported and no history/current test is run.
"""

from __future__ import annotations

import inspect
import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_evidence_ledger_current as selection
from scripts.ci import check_evidence_ledger_hashes as legacy
from scripts.ci import ledger_current_execution as current
from scripts.ci import ledger_current_relations as schema
from scripts.ci import ledger_history_proofs as operational

TEST = "tests/unit/test_never_import.py"
OLD = "a" * 64
NEW = "b" * 64
SOURCE = "c" * 64
LOCK = "d" * 64
COMMIT = "e" * 40


def raw(task: str = "OLD", recipe: str = OLD, evidence: str = "evidence") -> str:
    return (
        f"| {task} | 2026-09-09 | author | reviewer | source | {evidence} | sha256:{recipe} ({TEST}) | test |"
    )


def physical(line: int, task: str = "OLD", recipe: str = OLD) -> schema.PhysicalRow:
    return schema._physical(line, raw(task, recipe))


def candidate() -> schema.Candidate:
    return schema.Candidate(COMMIT, "f" * 40, "1" * 64, LOCK, "2" * 64)


def relation(
    target: schema.PhysicalRow, successor: schema.PhysicalRow, *, kind: schema.Kind = "V1_RECIPE_EQUALITY"
) -> schema.Relation:
    claim = schema.HistoricalClaim(
        kind,
        target,
        COMMIT,
        target.line + 7,
        "1" * 64,
        target.declared_hash if kind == "V2_INVALID_SOURCE_DECLARATION" else SOURCE,
        LOCK,
        "3" * 64 if kind == "V2_INVALID_SOURCE_DECLARATION" else target.declared_hash,
    )
    return schema.Relation(claim, successor, successor, tuple(sorted({TEST, "uv.lock", "pyproject.toml"})))


def expectation(row: schema.PhysicalRow, bound: schema.Candidate | None = None) -> schema.CurrentExpectation:
    return schema.CurrentExpectation(row, bound or candidate(), SOURCE, row.declared_hash, "FIXED_ONLY")


def scope_pair(kind: schema.Kind = "V1_RECIPE_EQUALITY") -> schema.RelationScope:
    target, successor = physical(2), physical(3, "NEW", NEW)
    edge = relation(target, successor, kind=kind)
    expectation_ = expectation(successor)
    requirements = tuple(
        schema.Requirement(row, expectation_, (edge.identity,)) for row in (target, successor)
    )
    return schema.RelationScope(
        candidate(),
        "0" * 40,
        (target, successor),
        (target.identity,),
        (),
        (edge,),
        (edge.identity,),
        requirements,
    )


@pytest.fixture(autouse=True)
def forbid_operational_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("schema planning must not execute tests or operational producers")

    monkeypatch.setattr(current.CurrentRunner, "run", forbidden)
    monkeypatch.setattr(legacy, "verify_historical_row", forbidden)
    monkeypatch.setattr(legacy, "_capture_historical_recipe", forbidden)
    monkeypatch.setattr(operational, "execute_selected", forbidden)
    monkeypatch.setattr(operational, "prove_relation", forbidden)


def test_old_claim_and_current_expectation_have_separate_exact_identities() -> None:
    result = scope_pair()
    old, new = result.requirements
    assert old.occurrence.declared_hash == OLD
    assert old.current.recipe_sha256 == new.current.recipe_sha256 == NEW
    assert old.occurrence.identity != new.occurrence.identity
    assert old.current.authoritative_row == new.occurrence
    assert result.relations[0].claim.historical_line == 9
    assert result.relations[0].claim.historical_recipe_sha256 == OLD
    assert result.relations[0].claim.historical_claim_verified is None
    assert result.operational_execution == result.current_execution == "NOT_IMPLEMENTED"
    assert "status" not in asdict(result) and "run_id" not in asdict(old)


@pytest.mark.parametrize("kind", ["V2_INVALID_SOURCE_DECLARATION", "V3_VERIFIED_OWN_LOCK"])
def test_operational_discriminants_are_obligations_never_fresh_verification(kind: schema.Kind) -> None:
    result = scope_pair(kind)
    claim = result.relations[0].claim
    assert claim.historical_claim_verified is (False if kind == "V2_INVALID_SOURCE_DECLARATION" else None)
    assert result.requirements[0].occurrence.declared_hash == OLD
    assert result.requirements[0].current.recipe_sha256 == NEW
    assert claim.source_lock_sha256 == LOCK
    changed_current_lock = replace(candidate(), lock_sha256="9" * 64)
    changed = replace(result.requirements[0].current, candidate=changed_current_lock)
    with pytest.raises(schema.RelationSchemaError, match="STALE_CURRENT_CANDIDATE"):
        replace(
            result, requirements=(replace(result.requirements[0], current=changed), result.requirements[1])
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"line": False},
        {"line": 0},
        {"row_sha256": NEW},
        {"task_id": "FORGED"},
        {"declared_hash": NEW},
        {"test_path": "../test.py"},
        {"raw_line": raw() + "\nextra"},
        {"row_date": "2020-01-01"},
    ],
)
def test_malformed_or_forged_physical_fields_fail(changes: dict[str, Any]) -> None:
    with pytest.raises((schema.RelationSchemaError, legacy.SupersessionError)):
        replace(physical(2), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_commit": "HEAD"},
        {"historical_line": True},
        {"source_lock_sha256": "not-a-hash"},
        {"historical_recipe_sha256": NEW},
        {"kind": "ACCEPTED"},
    ],
)
def test_malformed_historical_fields_fail(changes: dict[str, Any]) -> None:
    with pytest.raises(schema.RelationSchemaError):
        replace(scope_pair().relations[0].claim, **changes)


def test_invalid_history_cannot_be_changed_to_verified_by_a_current_recipe() -> None:
    claim = scope_pair("V2_INVALID_SOURCE_DECLARATION").relations[0].claim
    with pytest.raises(schema.RelationSchemaError, match="INVALIDITY_ERASED"):
        replace(claim, historical_recipe_sha256=OLD)
    with pytest.raises(schema.RelationSchemaError, match="HISTORICAL_RECIPE"):
        replace(claim, kind="V3_VERIFIED_OWN_LOCK")
    with pytest.raises(TypeError):
        schema.HistoricalClaim(**asdict(claim), historical_claim_verified=True)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "changes",
    [{"recipe_sha256": OLD}, {"recipe_eligibility": "FIXED_OR_LEGACY"}, {"test_source_sha256": "wrong"}],
)
def test_current_expectation_rejects_override_or_bad_bindings(changes: dict[str, Any]) -> None:
    with pytest.raises(schema.RelationSchemaError):
        replace(scope_pair().requirements[0].current, **changes)


def test_legacy_eligibility_is_derived_from_authoritative_row_only() -> None:
    line = raw("BEFORE").replace("2026-09-09", "2026-09-05")
    row = schema._physical(5, line)
    assert schema._eligibility(row) == "FIXED_OR_LEGACY"
    with pytest.raises(schema.RelationSchemaError, match="CURRENT_RECIPE_ELIGIBILITY"):
        schema.CurrentExpectation(row, candidate(), SOURCE, OLD, "FIXED_ONLY")


@pytest.mark.parametrize("side", [0, 1])
def test_target_only_and_successor_only_include_both_physical_endpoints(side: int) -> None:
    result = scope_pair()
    selected = (result.rows[side].identity,)
    revised = replace(result, selected=selected)
    assert len(revised.requirements) == 2 and len(revised.required_relations) == 1
    with pytest.raises(schema.RelationSchemaError, match="PARTIAL_PHYSICAL_SCOPE"):
        replace(revised, requirements=(revised.requirements[side],))


@pytest.mark.parametrize(
    "path",
    [
        "src/unrelated.py",
        "conftest.py",
        "uv.lock",
        "pytest.ini",
        "tests/new.py",
        "scripts/ci/pytest_metadata_admission.py",
        "config/deleted.json",
        "spec/new.dmn",
    ],
)
def test_source_config_added_deleted_paths_trigger_existing_rule(path: str) -> None:
    result = scope_pair()
    changed = replace(result, selected=(), changed_paths=(path,))
    assert len(changed.requirements) == 2
    with pytest.raises(schema.RelationSchemaError, match="PARTIAL_RELATION_SCOPE"):
        replace(changed, required_relations=())


def test_evidence_only_triggers_referring_relation_without_global_source_change() -> None:
    result = scope_pair()
    edge = replace(
        result.relations[0],
        dependencies=tuple(sorted((*result.relations[0].dependencies, "docs/evidence-history/a.json"))),
    )
    changed = replace(result, relations=(edge,), selected=(), changed_paths=("docs/evidence-history/a.json",))
    assert len(changed.requirements) == 2
    empty = replace(
        result, selected=(), requirements=(), required_relations=(), changed_paths=("docs/narrative.md",)
    )
    assert not empty.requirements and empty.operational_execution == "NOT_IMPLEMENTED"


def test_unrelated_ordinary_addition_cannot_mask_relation_source_change() -> None:
    result = scope_pair()
    ordinary = physical(4, "UNRELATED", SOURCE)
    added = schema.Requirement(ordinary, expectation(ordinary), ())
    revised = replace(
        result,
        rows=(*result.rows, ordinary),
        selected=(ordinary.identity,),
        changed_paths=("src/new.py",),
        requirements=(*result.requirements, added),
    )
    assert len(revised.requirements) == 3 and len(revised.required_relations) == 1


def test_duplicate_bytes_and_same_task_different_bytes_remain_physical() -> None:
    a, b, c = physical(2), physical(3), physical(4, recipe=NEW)
    requirements = tuple(schema.Requirement(row, expectation(row), ()) for row in (a, b, c))
    result = schema.RelationScope(
        candidate(), "0" * 40, (a, b, c), (a.identity, b.identity, c.identity), (), (), (), requirements
    )
    assert len(result.requirements) == 3
    assert a.raw_line == b.raw_line and a.identity != b.identity
    assert a.task_id == c.task_id and a.row_sha256 != c.row_sha256
    with pytest.raises(schema.RelationSchemaError, match="DUPLICATE_PHYSICAL_LINE"):
        replace(result, rows=(a, a, c))
    with pytest.raises(schema.RelationSchemaError, match="PARTIAL_PHYSICAL_SCOPE"):
        replace(result, requirements=(requirements[0], requirements[0], requirements[2]))


def test_wrong_current_endpoint_stale_candidate_and_omitted_history_fail() -> None:
    result = scope_pair()
    target, successor = result.rows
    with pytest.raises(schema.RelationSchemaError, match="CURRENT_ENDPOINT_SWAP"):
        replace(
            result,
            requirements=(
                replace(result.requirements[0], current=expectation(target)),
                result.requirements[1],
            ),
        )
    with pytest.raises(schema.RelationSchemaError, match="STALE_CURRENT_CANDIDATE"):
        replace(result, candidate=replace(candidate(), commit="9" * 40))
    with pytest.raises(schema.RelationSchemaError, match="MISSING_HISTORY_OBLIGATION"):
        replace(
            result,
            requirements=(replace(result.requirements[0], required_relations=()), result.requirements[1]),
        )
    with pytest.raises(schema.RelationSchemaError, match="ORPHAN_SELECTION"):
        replace(result, selected=(physical(30).identity,))
    with pytest.raises(schema.RelationSchemaError, match="UNCOMMITTED_ENDPOINT"):
        replace(result, relations=(replace(result.relations[0], successor=replace(successor, line=99)),))


def test_chain_terminal_and_cycles_are_independent_graph_invariants() -> None:
    a, b, c = physical(2), physical(3, "MIDDLE", NEW), physical(4, "TERMINAL", SOURCE)
    ab, bc = relation(a, b), relation(b, c)
    ab = replace(ab, terminal=c)
    assert len(schema._graph((a, b, c), (ab, bc))) == 2
    with pytest.raises(schema.RelationSchemaError, match="INCORRECT_TERMINAL"):
        schema._graph((a, b, c), (replace(ab, terminal=b), bc))
    with pytest.raises(schema.RelationSchemaError, match="CYCLIC_EDGE"):
        schema._graph((a, b, c), (ab, bc, relation(c, a)))
    with pytest.raises(schema.RelationSchemaError, match="AMBIGUOUS_EDGE"):
        schema._graph((a, b, c), (ab, relation(a, c)))
    with pytest.raises(schema.RelationSchemaError, match="DUPLICATE_RELATION"):
        schema._graph((a, b, c), (ab, ab))


def test_operational_chain_and_cross_path_migration_have_no_invented_semantics() -> None:
    a, b, c = physical(2), physical(3, "MIDDLE", NEW), physical(4, "TERMINAL", SOURCE)
    ab = replace(relation(a, b, kind="V2_INVALID_SOURCE_DECLARATION"), terminal=c)
    with pytest.raises(schema.RelationSchemaError, match="OPERATIONAL_GRAPH_OVERLAP"):
        schema._graph((a, b, c), (ab, relation(b, c)))
    other = schema._physical(5, raw("OTHER").replace(TEST, "tests/unit/elsewhere.py"))
    with pytest.raises(schema.RelationSchemaError, match="UNSUPPORTED_PATH_MIGRATION"):
        relation(a, other)


def commit(root: Path, message: str) -> str:
    for argv in (["git", "add", "."], ["git", "commit", "-qm", message]):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    return current.git(root, "rev-parse", "HEAD").decode().strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    for key, value in (("user.name", "Schema fixture"), ("user.email", "schema@example.invalid")):
        subprocess.run(["git", "config", key, value], cwd=root, check=True, capture_output=True)
    (root / TEST).parent.mkdir(parents=True)
    (root / TEST).write_text("raise AssertionError('planning imported the test')\n")
    (root / "docs").mkdir()
    (root / legacy.DEFAULT_LEDGER_PATH).write_text("# fixture\n")
    (root / "uv.lock").write_text("# schema-only fixture lock, never an installed runtime claim\n")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    return root, commit(root, "empty")


def add_edge(root: Path) -> tuple[str, str]:
    target = raw()
    (root / legacy.DEFAULT_LEDGER_PATH).write_text("# fixture\n" + target + "\n")
    old = commit(root, "historical target")
    marker = (
        f"[ledger-supersedes:v1;target=OLD;source_commit={old};"
        f"source_row_sha256={current.digest(target.encode())};"
        f"source_test_sha256={current.digest((root / TEST).read_bytes())};"
        f"source_lock_sha256={current.digest((root / 'uv.lock').read_bytes())}]"
    )
    successor = raw("NEW", NEW, marker)
    with (root / legacy.DEFAULT_LEDGER_PATH).open("a") as stream:
        stream.write(successor + "\n")
    return old, commit(root, "committed edge")


def test_actual_v1_planner_derives_changed_current_expectation_without_test_import(
    repo: tuple[Path, str],
) -> None:
    root, base = repo
    historical, head = add_edge(root)
    result = schema.plan_current_relations(root, base)
    assert result.candidate.commit == head
    assert len(result.relations) == 1 and len(result.requirements) == 2
    claim = result.relations[0].claim
    assert claim.source_commit == historical and claim.historical_line == 2
    assert claim.target.declared_hash == OLD
    assert result.requirements[0].current.recipe_sha256 == NEW
    assert result.requirements[0].current.test_source_sha256 == current.digest((root / TEST).read_bytes())
    assert result.candidate.lock_sha256 == current.digest((root / "uv.lock").read_bytes())
    successor_only = schema.plan_current_relations(root, historical)
    assert len(successor_only.selected) == 1 and len(successor_only.requirements) == 2


def test_actual_source_only_change_schedules_existing_edge_even_with_unrelated_row(
    repo: tuple[Path, str],
) -> None:
    root, _ = repo
    _, base = add_edge(root)
    (root / "src").mkdir()
    (root / "src/new.py").write_text("raise AssertionError('do not import project source')\n")
    with (root / legacy.DEFAULT_LEDGER_PATH).open("a") as stream:
        stream.write(raw("UNRELATED", SOURCE) + "\n")
    commit(root, "source plus unrelated row")
    result = schema.plan_current_relations(root, base)
    assert len(result.selected) == 1 and len(result.requirements) == 3
    assert len(result.required_relations) == 1


def test_actual_duplicate_unlinked_rows_preserve_both_occurrences(repo: tuple[Path, str]) -> None:
    root, base = repo
    with (root / legacy.DEFAULT_LEDGER_PATH).open("a") as stream:
        stream.write(raw() + "\n" + raw() + "\n")
    commit(root, "duplicates")
    result = schema.plan_current_relations(root, base)
    assert len(result.selected) == len(result.requirements) == 2
    assert result.requirements[0].occurrence.raw_line == result.requirements[1].occurrence.raw_line
    assert result.requirements[0].occurrence.line != result.requirements[1].occurrence.line


def test_actual_duplicate_linked_target_is_ambiguous_and_cannot_fallback(repo: tuple[Path, str]) -> None:
    root, base = repo
    add_edge(root)
    with (root / legacy.DEFAULT_LEDGER_PATH).open("a") as stream:
        stream.write(raw() + "\n")
    commit(root, "ambiguous old target")
    with pytest.raises(legacy.SupersessionError, match="resolves to 2 rows"):
        schema.plan_current_relations(root, base)


@pytest.mark.parametrize(
    "mutation", ["source_commit", "source_test_sha256", "source_lock_sha256", "source_row_sha256"]
)
def test_actual_forged_historical_claim_is_rejected_before_any_consumer(
    repo: tuple[Path, str], mutation: str
) -> None:
    root, base = repo
    old, _ = add_edge(root)
    path = root / legacy.DEFAULT_LEDGER_PATH
    text = path.read_text()
    value = text.split(mutation + "=", 1)[1].split(";", 1)[0].split("]", 1)[0]
    replacement = "9" * (40 if mutation == "source_commit" else 64)
    assert value and old
    path.write_text(text.replace(mutation + "=" + value, mutation + "=" + replacement))
    commit(root, "forged claim")
    with pytest.raises((legacy.SupersessionError, schema.RelationSchemaError)):
        schema.plan_current_relations(root, base)


def test_actual_dirty_candidate_and_nonexact_base_fail(repo: tuple[Path, str]) -> None:
    root, base = repo
    add_edge(root)
    with pytest.raises(schema.RelationSchemaError, match="HASH_SHAPE"):
        schema.plan_current_relations(root, "HEAD~1")
    (root / "uv.lock").write_text("changed\n")
    with pytest.raises(ValueError, match="CANDIDATE_NOT_CLEAN"):
        schema.plan_current_relations(root, base)


def test_no_public_expected_hash_plan_or_json_proof_input() -> None:
    assert tuple(inspect.signature(schema.plan_current_relations).parameters) == ("root", "base")
    payload = json.loads(json.dumps(asdict(scope_pair())))
    with pytest.raises(TypeError):
        schema.plan_current_relations(Path("."), COMMIT, plan=payload)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        schema.plan_current_relations(Path("."), COMMIT, expected_hash=NEW)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        schema.RelationScope(**payload, status="ACCEPTED")  # type: ignore[call-arg]
    assert not hasattr(schema, "consume") and not hasattr(schema, "from_json")


def test_actual_chain_uses_terminal_recipe_for_every_distinct_occurrence(repo: tuple[Path, str]) -> None:
    root, base = repo
    _, intermediate = add_edge(root)
    path = root / legacy.DEFAULT_LEDGER_PATH
    middle = path.read_text().splitlines()[2]
    marker = (
        f"[ledger-supersedes:v1;target=NEW;source_commit={intermediate};"
        f"source_row_sha256={current.digest(middle.encode())};"
        f"source_test_sha256={current.digest((root / TEST).read_bytes())};"
        f"source_lock_sha256={current.digest((root / 'uv.lock').read_bytes())}]"
    )
    with path.open("a") as stream:
        stream.write(raw("TERMINAL", SOURCE, marker) + "\n")
    commit(root, "terminal chain")
    result = schema.plan_current_relations(root, base)
    assert len(result.requirements) == 3 and len(result.required_relations) == 2
    assert [req.occurrence.declared_hash for req in result.requirements] == [OLD, NEW, SOURCE]
    assert {req.current.recipe_sha256 for req in result.requirements} == {SOURCE}
    assert {req.current.authoritative_row.line for req in result.requirements} == {4}
    assert len({req.occurrence.identity for req in result.requirements}) == 3
    assert all(len(req.required_relations) == 2 for req in result.requirements)


def test_actual_candidate_change_during_planning_is_refused(
    repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, base = repo
    add_edge(root)
    original = selection.selected_occurrences

    def changing(candidate_root: Path, candidate_base: str) -> tuple[current.Occurrence, ...]:
        selected = original(candidate_root, candidate_base)
        (root / "pyproject.toml").write_text("# changed candidate during planning\n")
        commit(root, "mid-planning drift")
        return selected

    monkeypatch.setattr(selection, "selected_occurrences", changing)
    with pytest.raises((schema.RelationSchemaError, legacy.SupersessionError), match="CANDIDATE_DRIFT"):
        schema.plan_current_relations(root, base)


def test_actual_orphan_record_and_missing_endpoint_are_unresolved(repo: tuple[Path, str]) -> None:
    root, base = repo
    add_edge(root)
    path = root / legacy.DEFAULT_LEDGER_PATH
    path.write_text(
        "\n".join(line for line in path.read_text().splitlines() if not line.startswith("| OLD |")) + "\n"
    )
    commit(root, "missing historical endpoint")
    with pytest.raises(legacy.SupersessionError, match="resolves to 0 rows"):
        schema.plan_current_relations(root, base)
    path.write_text("# no declaration\n")
    evidence = root / "docs/evidence-history/orphan.json"
    evidence.parent.mkdir()
    evidence.write_text('{"status":"VERIFIED_HISTORY_RELATION"}\n')
    commit(root, "orphan archived status")
    with pytest.raises(legacy.SupersessionError, match="IC_ORPHAN_EVIDENCE"):
        schema.plan_current_relations(root, base)


def test_failed_recipe_identity_never_has_current_success_semantics() -> None:
    failed = legacy.compute_recipe_hash([TEST + "::test_case FAILED"]).removeprefix("sha256:")
    target = physical(2, recipe=failed)
    successor = physical(3, "NEW", NEW)
    edge = relation(target, successor)
    assert edge.claim.historical_recipe_sha256 == failed
    assert edge.claim.target.declared_hash != expectation(successor).recipe_sha256
    assert edge.claim.historical_claim_verified is None
    assert not hasattr(edge, "status") and not hasattr(expectation(successor), "accepted")


def test_schema_is_closed_and_frozen_without_proof_handles() -> None:
    result = scope_pair()
    with pytest.raises((AttributeError, TypeError)):
        result.accepted = True  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        result.requirements = ()  # type: ignore[misc]
    with pytest.raises(schema.RelationSchemaError, match="DEPENDENCY_OMISSION"):
        replace(result.relations[0], dependencies=(TEST,))
    with pytest.raises(schema.RelationSchemaError, match="DUPLICATE_SELECTION"):
        replace(result, selected=(result.selected[0], result.selected[0]))
