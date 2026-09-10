"""Source-derived relation obligations; none of these records is execution proof.

Stage 3 schema mandate, successor-f37-current-execution-review/STAGE-3-MANDATES.md.
Only plan_current_relations reads authoritative inputs. There is intentionally no
JSON loader, supplied-plan adapter, current-hash override, or execution consumer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from scripts.ci import check_evidence_ledger_current as selection
from scripts.ci import check_evidence_ledger_hashes as legacy
from scripts.ci import ledger_current_execution as source
from scripts.ci import ledger_invalid_declarations as history

Kind = Literal["V1_RECIPE_EQUALITY", "V2_INVALID_SOURCE_DECLARATION", "V3_VERIFIED_OWN_LOCK"]
Eligibility = Literal["FIXED_ONLY", "FIXED_OR_LEGACY"]
KINDS = frozenset({"V1_RECIPE_EQUALITY", "V2_INVALID_SOURCE_DECLARATION", "V3_VERIFIED_OWN_LOCK"})
SCHEMA: Literal["maezo-ledger-current-relations/v1"] = "maezo-ledger-current-relations/v1"


class RelationSchemaError(ValueError):
    """An unresolved source/graph binding, never permission for ordinary fallback."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RelationSchemaError(reason)


def _hash(value: str, size: int = 64) -> None:
    _require(type(value) is str and len(value) == size, "HASH_SHAPE")
    _require(all(char in "0123456789abcdef" for char in value), "HASH_SHAPE")


def _paths(paths: tuple[str, ...]) -> None:
    _require(type(paths) is tuple and paths == tuple(sorted(set(paths))), "DEPENDENCY_IDENTITIES")
    for path in paths:
        history.safe_path(path)


@dataclass(frozen=True, slots=True)
class Candidate:
    commit: str
    tree: str
    ledger_sha256: str
    lock_sha256: str
    source_sha256: str

    def __post_init__(self) -> None:
        _hash(self.commit, 40)
        _hash(self.tree, 40)
        for value in (self.ledger_sha256, self.lock_sha256, self.source_sha256):
            _hash(value)


@dataclass(frozen=True, slots=True)
class PhysicalRow:
    line: int
    raw_line: str
    row_sha256: str
    task_id: str
    test_path: str
    declared_hash: str
    row_date: str

    def __post_init__(self) -> None:
        _require(type(self.line) is int and self.line > 0, "PHYSICAL_LINE")
        _require(
            type(self.raw_line) is str
            and "\n" not in self.raw_line
            and "\r" not in self.raw_line
            and bool(self.raw_line),
            "PHYSICAL_BYTES",
        )
        parsed = legacy.select_rows([self.raw_line]).declared
        _require(len(parsed) == 1, "PHYSICAL_DECLARATION")
        row = parsed[0]
        _require(legacy.is_safe_test_path(self.test_path), "PHYSICAL_PATH")
        _require(
            (self.row_sha256, self.task_id, self.test_path, self.declared_hash, self.row_date)
            == (legacy.row_sha256(row), row.task_id, row.test_path, row.declared_hash, row.row_date),
            "PHYSICAL_BINDING",
        )

    @property
    def identity(self) -> str:
        return f"{self.line}:{self.row_sha256}"


def _physical(line: int, raw: str) -> PhysicalRow:
    rows = legacy.select_rows([raw]).declared
    _require(len(rows) == 1, "PHYSICAL_DECLARATION")
    row = rows[0]
    _require(row.row_date is not None, "PHYSICAL_DATE")
    return PhysicalRow(
        line, raw, legacy.row_sha256(row), row.task_id, row.test_path, row.declared_hash, row.row_date or ""
    )


@dataclass(frozen=True, slots=True)
class HistoricalClaim:
    kind: Kind
    target: PhysicalRow
    source_commit: str
    historical_line: int
    source_ledger_sha256: str
    source_test_sha256: str
    source_lock_sha256: str
    historical_recipe_sha256: str

    def __post_init__(self) -> None:
        _require(self.kind in KINDS and type(self.target) is PhysicalRow, "HISTORY_KIND")
        _hash(self.source_commit, 40)
        _require(type(self.historical_line) is int and self.historical_line > 0, "HISTORICAL_LINE")
        for value in (
            self.source_ledger_sha256,
            self.source_test_sha256,
            self.source_lock_sha256,
            self.historical_recipe_sha256,
        ):
            _hash(value)
        if self.kind == "V2_INVALID_SOURCE_DECLARATION":
            _require(self.target.declared_hash == self.source_test_sha256, "INVALID_SOURCE_DECLARATION")
            _require(self.target.declared_hash != self.historical_recipe_sha256, "INVALIDITY_ERASED")
        else:
            _require(self.target.declared_hash == self.historical_recipe_sha256, "HISTORICAL_RECIPE")

    @property
    def historical_claim_verified(self) -> Literal[False] | None:
        # A static valid-equality requirement is not a freshly verified claim.
        return False if self.kind == "V2_INVALID_SOURCE_DECLARATION" else None


@dataclass(frozen=True, slots=True)
class Relation:
    claim: HistoricalClaim
    successor: PhysicalRow
    terminal: PhysicalRow
    dependencies: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(type(self.claim) is HistoricalClaim, "CLAIM_TYPE")
        _require(type(self.successor) is PhysicalRow and type(self.terminal) is PhysicalRow, "ENDPOINT_TYPE")
        _require(self.claim.target.identity != self.successor.identity, "SELF_EDGE")
        _require(
            self.claim.target.test_path == self.successor.test_path == self.terminal.test_path,
            "UNSUPPORTED_PATH_MIGRATION",
        )
        _paths(self.dependencies)
        _require(
            {self.claim.target.test_path, self.successor.test_path, "uv.lock", "pyproject.toml"}
            <= set(self.dependencies),
            "DEPENDENCY_OMISSION",
        )

    @property
    def identity(self) -> str:
        return source.digest(
            source.canonical(
                [
                    self.claim.kind,
                    self.claim.target.identity,
                    self.successor.identity,
                    self.claim.source_commit,
                ]
            )
        )


def _eligibility(row: PhysicalRow) -> Eligibility:
    return "FIXED_OR_LEGACY" if row.row_date < legacy.LEGACY_NODE_ID_RECIPE_CUTOFF_DATE else "FIXED_ONLY"


@dataclass(frozen=True, slots=True)
class CurrentExpectation:
    authoritative_row: PhysicalRow
    candidate: Candidate
    test_source_sha256: str
    recipe_sha256: str
    recipe_eligibility: Eligibility

    def __post_init__(self) -> None:
        _require(
            type(self.authoritative_row) is PhysicalRow and type(self.candidate) is Candidate, "CURRENT_TYPE"
        )
        _hash(self.test_source_sha256)
        _require(self.recipe_sha256 == self.authoritative_row.declared_hash, "CURRENT_RECIPE_OVERRIDE")
        _require(
            self.recipe_eligibility == _eligibility(self.authoritative_row), "CURRENT_RECIPE_ELIGIBILITY"
        )


@dataclass(frozen=True, slots=True)
class Requirement:
    occurrence: PhysicalRow
    current: CurrentExpectation
    required_relations: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(
            type(self.occurrence) is PhysicalRow and type(self.current) is CurrentExpectation,
            "REQUIREMENT_TYPE",
        )
        _require(self.occurrence.test_path == self.current.authoritative_row.test_path, "CURRENT_PATH_SWAP")
        _require(
            type(self.required_relations) is tuple
            and self.required_relations == tuple(sorted(set(self.required_relations))),
            "REQUIRED_RELATION_IDENTITIES",
        )


def _graph(rows: tuple[PhysicalRow, ...], relations: tuple[Relation, ...]) -> dict[str, Relation]:
    _require(type(rows) is tuple and all(type(row) is PhysicalRow for row in rows), "ROW_TYPES")
    _require(
        tuple(row.line for row in rows) == tuple(sorted({row.line for row in rows})),
        "DUPLICATE_PHYSICAL_LINE",
    )
    indexed = {row.identity: row for row in rows}
    _require(type(relations) is tuple and all(type(rel) is Relation for rel in relations), "RELATION_TYPES")
    _require(len({rel.identity for rel in relations}) == len(relations), "DUPLICATE_RELATION")
    by_target: dict[str, Relation] = {}
    successors: set[str] = set()
    for rel in relations:
        for row in (rel.claim.target, rel.successor, rel.terminal):
            _require(indexed.get(row.identity) == row, "UNCOMMITTED_ENDPOINT")
        key = rel.claim.target.identity
        _require(key not in by_target and rel.successor.identity not in successors, "AMBIGUOUS_EDGE")
        by_target[key] = rel
        successors.add(rel.successor.identity)
    for rel in relations:
        seen: set[str] = set()
        cursor = rel.claim.target
        while cursor.identity in by_target:
            _require(cursor.identity not in seen, "CYCLIC_EDGE")
            seen.add(cursor.identity)
            following = by_target[cursor.identity]
            if following is not rel:
                _require(
                    rel.claim.kind == following.claim.kind == "V1_RECIPE_EQUALITY",
                    "OPERATIONAL_GRAPH_OVERLAP",
                )
            cursor = following.successor
        _require(cursor == rel.terminal, "INCORRECT_TERMINAL")
    return by_target


def _required_scope(
    rows: tuple[PhysicalRow, ...],
    relations: tuple[Relation, ...],
    selected: tuple[str, ...],
    changed: tuple[str, ...],
) -> tuple[set[str], set[str]]:
    _require(type(selected) is tuple and len(set(selected)) == len(selected), "DUPLICATE_SELECTION")
    _require(set(selected) <= {row.identity for row in rows}, "ORPHAN_SELECTION")
    _paths(changed)
    active = set(selected)
    required: set[str] = set()
    source_changed = bool(history.CONFIG_FILES & set(changed)) or any(
        path.startswith(history.SOURCE_PREFIXES) for path in changed
    )
    for rel in relations:
        if source_changed or set(rel.dependencies) & set(changed):
            active.update((rel.claim.target.identity, rel.successor.identity))
    while True:
        before = len(active)
        for rel in relations:
            endpoints = {rel.claim.target.identity, rel.successor.identity}
            if endpoints & active:
                active.update(endpoints)
                required.add(rel.identity)
        if len(active) == before:
            break
    return active, required


def _component_relations(occurrence: str, relations: tuple[Relation, ...]) -> tuple[str, ...]:
    component = {occurrence}
    result: set[str] = set()
    while True:
        before = len(component)
        for rel in relations:
            endpoints = {rel.claim.target.identity, rel.successor.identity}
            if component & endpoints:
                component.update(endpoints)
                result.add(rel.identity)
        if len(component) == before:
            return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class RelationScope:
    candidate: Candidate
    base: str
    rows: tuple[PhysicalRow, ...]
    selected: tuple[str, ...]
    changed_paths: tuple[str, ...]
    relations: tuple[Relation, ...]
    required_relations: tuple[str, ...]
    requirements: tuple[Requirement, ...]
    schema: Literal["maezo-ledger-current-relations/v1"] = field(default=SCHEMA, init=False)

    def __post_init__(self) -> None:
        _require(type(self.candidate) is Candidate, "CANDIDATE_TYPE")
        _hash(self.base, 40)
        by_target = _graph(self.rows, self.relations)
        active, required = _required_scope(self.rows, self.relations, self.selected, self.changed_paths)
        _require(self.required_relations == tuple(sorted(required)), "PARTIAL_RELATION_SCOPE")
        _require(type(self.requirements) is tuple, "REQUIREMENTS_TYPE")
        _require(
            tuple(req.occurrence.identity for req in self.requirements)
            == tuple(row.identity for row in self.rows if row.identity in active),
            "PARTIAL_PHYSICAL_SCOPE",
        )
        indexed = {row.identity: row for row in self.rows}
        for req in self.requirements:
            _require(indexed.get(req.occurrence.identity) == req.occurrence, "OCCURRENCE_SWAP")
            _require(req.current.candidate == self.candidate, "STALE_CURRENT_CANDIDATE")
            relation = by_target.get(req.occurrence.identity)
            terminal = relation.terminal if relation else req.occurrence
            _require(req.current.authoritative_row == terminal, "CURRENT_ENDPOINT_SWAP")
            _require(
                req.required_relations == _component_relations(req.occurrence.identity, self.relations),
                "MISSING_HISTORY_OBLIGATION",
            )

    @property
    def operational_execution(self) -> Literal["NOT_IMPLEMENTED"]:
        return "NOT_IMPLEMENTED"

    @property
    def current_execution(self) -> Literal["NOT_IMPLEMENTED"]:
        return "NOT_IMPLEMENTED"


def _exact(rows: tuple[PhysicalRow, ...], row: legacy.DeclaredRow) -> PhysicalRow:
    found = [item for item in rows if item.raw_line == row.raw_line]
    _require(len(found) == 1, "AMBIGUOUS_PHYSICAL_ENDPOINT")
    return found[0]


def _historical(
    reader: history.FrozenGit,
    target: PhysicalRow,
    kind: Kind,
    commit: str,
    test_sha: str,
    lock_sha: str,
    recipe_sha: str,
) -> HistoricalClaim:
    reader.ancestor(commit)
    ledger = reader.blob(commit, legacy.DEFAULT_LEDGER_PATH, limit=history.MAX_LEDGER_BYTES)
    positions = [
        index for index, line in enumerate(ledger.decode().splitlines(), 1) if line == target.raw_line
    ]
    _require(len(positions) == 1, "HISTORICAL_PHYSICAL_BINDING")
    _require(source.digest(reader.blob(commit, target.test_path)) == test_sha, "HISTORICAL_TEST_BINDING")
    _require(source.digest(reader.blob(commit, "uv.lock")) == lock_sha, "HISTORICAL_LOCK_BINDING")
    return HistoricalClaim(
        kind, target, commit, positions[0], source.digest(ledger), test_sha, lock_sha, recipe_sha
    )


def plan_current_relations(root: Path, base: str) -> RelationScope:
    """Derive obligations from this exact candidate; execute no tests or proof producers."""
    _hash(base, 40)
    before = source.source_inventory(root)
    reader = history.FrozenGit(root)
    _require((reader.candidate, reader.tree) == (before["commit"], before["tree"]), "CANDIDATE_DRIFT")
    reader.ancestor(base)
    ledger_bytes = reader.current(legacy.DEFAULT_LEDGER_PATH, limit=history.MAX_LEDGER_BYTES)
    ledger = ledger_bytes.decode()
    rows = tuple(
        _physical(index, raw)
        for index, raw in enumerate(ledger.splitlines(), 1)
        if legacy.select_rows([raw]).declared
    )
    ordinary = legacy.build_supersession_plan(root, ledger)
    operational = history.build_plan(root, ledger, ordinary)
    if operational.relations:
        _require(
            (operational.candidate, operational.tree) == (reader.candidate, reader.tree),
            "PLAN_CANDIDATE_DRIFT",
        )
    candidate = Candidate(
        reader.candidate,
        reader.tree,
        source.digest(ledger_bytes),
        source.digest(reader.current("uv.lock")),
        source.digest(source.canonical(before)),
    )
    common = {
        "uv.lock",
        "pyproject.toml",
        "scripts/ci/check_evidence_ledger_hashes.py",
        "scripts/ci/ledger_invalid_declarations.py",
    }
    relations: list[Relation] = []
    for edge in ordinary.by_target_row_sha256.values():
        target, successor = _exact(rows, edge.target), _exact(rows, edge.successor)
        terminal = successor
        seen = {target.identity}
        while terminal.row_sha256 in ordinary.by_target_row_sha256:
            _require(terminal.identity not in seen, "CYCLIC_EDGE")
            seen.add(terminal.identity)
            terminal = _exact(rows, ordinary.by_target_row_sha256[terminal.row_sha256].successor)
        claim = _historical(
            reader,
            target,
            "V1_RECIPE_EQUALITY",
            edge.claim.source_commit,
            edge.claim.source_test_sha256,
            edge.claim.source_lock_sha256,
            target.declared_hash,
        )
        relations.append(
            Relation(
                claim, successor, terminal, tuple(sorted(common | {target.test_path, successor.test_path}))
            )
        )
    for relation in operational.relations:
        target, successor = _exact(rows, relation.target), _exact(rows, relation.correction)
        record = relation.record
        _require(record.kind in {"invalid-declaration", "verified-history"}, "UNSUPPORTED_RELATION_KIND")
        kind: Kind = (
            "V2_INVALID_SOURCE_DECLARATION"
            if record.kind == "invalid-declaration"
            else "V3_VERIFIED_OWN_LOCK"
        )
        claim = _historical(
            reader,
            target,
            kind,
            record.target.source_commit,
            record.target.test_sha256,
            record.target.lock_sha256,
            record.historical.recipe_sha256,
        )
        _require(claim.source_ledger_sha256 == record.target.ledger_sha256, "HISTORICAL_LEDGER_BINDING")
        relations.append(
            Relation(
                claim,
                successor,
                successor,
                tuple(
                    sorted(
                        common
                        | {
                            target.test_path,
                            successor.test_path,
                            relation.record_path,
                            *relation.evidence_paths,
                        }
                    )
                ),
            )
        )
    edges = tuple(sorted(relations, key=lambda relation: relation.claim.target.line))
    by_target = _graph(rows, edges)
    selected = tuple(item.identity for item in selection.selected_occurrences(root, base))
    changed = tuple(
        sorted(
            filter(
                None,
                reader.git("diff", "--name-only", "--no-renames", "-z", base, reader.candidate)
                .decode()
                .split("\0"),
            )
        )
    )
    active, required = _required_scope(rows, edges, selected, changed)
    requirements: list[Requirement] = []
    for row in rows:
        if row.identity not in active:
            continue
        rel = by_target.get(row.identity)
        terminal = rel.terminal if rel else row
        expectation = CurrentExpectation(
            terminal,
            candidate,
            source.digest(reader.current(terminal.test_path)),
            terminal.declared_hash,
            _eligibility(terminal),
        )
        requirements.append(Requirement(row, expectation, _component_relations(row.identity, edges)))
    result = RelationScope(
        candidate, base, rows, selected, changed, edges, tuple(sorted(required)), tuple(requirements)
    )
    reader.unchanged()
    _require(source.source_inventory(root) == before, "CANDIDATE_DRIFT")
    return result
