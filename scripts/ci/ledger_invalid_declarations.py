"""Closed invalid-declaration planning (PFSU-02, accepted IC-R1 contract).

This module does not authenticate captured receipts. It binds committed inputs and
recomputes their canonical result stream, then refuses operational acceptance until
an independently reviewed fresh-replay adapter supplies environment/source proofs.
There is no record-controlled command, waiver, approval bit or fallback to v1.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal, NoReturn

from scripts.ci import check_evidence_ledger_hashes as v1
from scripts.ci import ledger_history_proofs as operational
from scripts.ci.check_ledger_row_cell_count import split_row_cells

MAX_JSON_BYTES = 262_144
MAX_CAPTURE_BYTES = 8_388_608
MAX_BLOB_BYTES = 33_554_432
MAX_LEDGER_BYTES = 16_777_216
MAX_RELATIONS = 128
MAX_RESULTS = 100_000
MAX_JSON_DEPTH = 12
MAX_JSON_ITEMS = 4096
MAX_GIT_ENTRIES = 25_000
EVIDENCE_ROOT = "docs/evidence-corrections/"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
# Conservative finite test/runtime dependency policy (ICV2-R1). Additions and
# deletions are dependencies too; no per-relation configuration map exists yet.
CONFIG_FILES = frozenset(
    {
        ".python-version",
        "pyproject.toml",
        "uv.lock",
        "uv.toml",
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
        "setup.py",
        "conftest.py",
        "requirements.txt",
        "requirements-dev.txt",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        ".env",
        ".env.example",
        "alembic.ini",
        "Makefile",
    }
)
SOURCE_PREFIXES = ("src/", "tests/", "spec/", "scripts/", "config/")
PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\Z")


class InvalidDeclarationError(v1.SupersessionError):
    """Stable category followed by bounded, non-secret diagnostics."""


def fail(code: str) -> NoReturn:
    raise InvalidDeclarationError(code)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def closed(value: Any, fields: Mapping[str, type]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        fail("IC_SCHEMA_FIELDS")
    if any(type(value[key]) is not kind for key, kind in fields.items()):
        fail("IC_SCHEMA_TYPE")
    return dict(value)


def bounded_json(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_JSON_BYTES:
        fail("IC_JSON_SIZE")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                fail("IC_JSON_DUPLICATE")
            result[key] = value
        return result

    def invalid_number(value: str) -> None:
        fail("IC_JSON_NUMBER")

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_float=invalid_number,
            parse_constant=invalid_number,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InvalidDeclarationError("IC_JSON_ENCODING") from exc
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > MAX_JSON_DEPTH or count > MAX_JSON_ITEMS:
            fail("IC_JSON_COMPLEXITY")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str) and (len(item) > 4096 or any(ord(c) < 32 for c in item)):
            fail("IC_JSON_STRING")
    if type(value) is not dict:
        fail("IC_SCHEMA_TYPE")
    return dict(value)


def safe_path(path: str) -> None:
    if not PATH.fullmatch(path) or any(part in ("", ".", "..") for part in path.split("/")):
        fail("IC_PATH")


def sha(value: str, *, commit: bool = False) -> None:
    if not (HEX40 if commit else HEX64).fullmatch(value):
        fail("IC_SHA")


def positive(value: int) -> None:
    if type(value) is not int or not 0 < value <= MAX_RESULTS:
        fail("IC_RESULT_COUNT")


def validate_marker_row(line: str) -> None:
    """Hints on orphan/legacy/malformed rows cannot evade global planning."""
    if v1._SUPERSESSION_HINT not in line:
        return
    marker = v1.parse_invalid_declaration_marker(line)
    if marker is None:
        v1.parse_supersession_claim(line)
    row = v1.parse_row_line(line)
    if row is None:
        fail("IC_MARKER_ROW: ledger-supersedes marker requires a declared successor")
    if not v1.is_date_qualified(row.row_date):
        fail("IC_MARKER_ROW: ledger-supersedes marker requires a convention-qualified successor Date")
    if marker is None:
        return
    cells = split_row_cells(line)
    if cells is None or len(cells) != 8 or not line.endswith("|"):
        fail("IC_ROW_CELLS")
    if not v1._DECLARED_HASH_RE.fullmatch(cells[6].strip()):
        fail("IC_TEST_HASH_CELL")
    if len(v1._DECLARED_HASH_RE.findall(line)) != 1:
        fail("IC_DECLARATION_AMBIGUOUS")
    try:
        if date.fromisoformat(row.row_date or "").isoformat() != row.row_date:
            fail("IC_ROW_DATE")
    except ValueError as exc:
        raise InvalidDeclarationError("IC_ROW_DATE") from exc


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str


@dataclass(frozen=True)
class Target:
    task_id: str
    row_sha256: str
    declared_recipe_sha256: str
    source_commit: str
    ledger_path: str
    ledger_sha256: str
    test_path: str
    test_sha256: str
    lock_sha256: str


@dataclass(frozen=True)
class HistoricalInputs:
    stdout: Artifact
    stderr: Artifact
    receipt: Artifact
    originals: tuple[Artifact, ...]
    original_origins: tuple[str, ...]
    recipe_sha256: str
    result_count: int


@dataclass(frozen=True)
class Current:
    task_id: str
    date: str
    path: str
    declared_recipe_sha256: str
    result_count: int
    row_template_sha256: str
    prior_correction: Target | None


@dataclass(frozen=True)
class Record:
    target: Target
    historical: HistoricalInputs
    current: Current


def target_record(value: Any) -> Target:
    fields = {name: str for name in Target.__dataclass_fields__}
    data = closed(value, fields)
    for key in ("row_sha256", "declared_recipe_sha256", "ledger_sha256", "test_sha256", "lock_sha256"):
        sha(data[key])
    sha(data["source_commit"], commit=True)
    if data["ledger_path"] != v1.DEFAULT_LEDGER_PATH or not v1.is_safe_test_path(data["test_path"]):
        fail("IC_TARGET_PATH")
    safe_path(data["test_path"])
    if not data["task_id"] or data["task_id"] != data["task_id"].strip():
        fail("IC_TASK_ID")
    return Target(**data)


def parse_record(data: bytes) -> Record:
    obj = closed(
        bounded_json(data),
        {
            "schema": str,
            "reason": str,
            "target": dict,
            "historical_evidence": dict,
            "current": dict,
        },
    )
    if obj["schema"] != "maezo-ledger-invalid-declaration/v1":
        fail("IC_SCHEMA_VERSION")
    if obj["reason"] != "declared-test-source-sha256":
        fail("IC_REASON")
    target = target_record(obj["target"])
    evidence_dir = f"{EVIDENCE_ROOT}evidence/{target.row_sha256}/"

    def artifact(value: Any) -> Artifact:
        ref = closed(value, {"path": str, "sha256": str})
        safe_path(ref["path"])
        sha(ref["sha256"])
        if not ref["path"].startswith(evidence_dir):
            fail("IC_EVIDENCE_DIRECTORY")
        return Artifact(**ref)

    hist = closed(
        obj["historical_evidence"],
        {
            "stdout": dict,
            "stderr": dict,
            "receipt": dict,
            "originals": list,
            "result_count": int,
            "passed": int,
            "failed": int,
            "recipe_version": str,
            "recipe_sha256": str,
        },
    )
    positive(hist["result_count"])
    if hist["passed"] != hist["result_count"] or hist["failed"] != 0 or hist["recipe_version"] != "fixed":
        fail("IC_HISTORICAL_EXPECTATION")
    sha(hist["recipe_sha256"])
    originals = hist["originals"]
    if not 2 <= len(originals) <= 8:
        fail("IC_ORIGINAL_REFERENCES")
    refs: list[Artifact] = []
    origins: list[str] = []
    roles: list[str] = []
    for original in originals:
        ref = closed(original, {"role": str, "origin": str, "artifact": dict})
        if ref["role"] not in {"report", "manifest", "packet"} or not ref["origin"]:
            fail("IC_ORIGINAL_REFERENCES")
        refs.append(artifact(ref["artifact"]))
        origins.append(ref["origin"])
        roles.append(ref["role"])
    if roles.count("report") != 1 or roles.count("manifest") != 1:
        fail("IC_ORIGINAL_REFERENCES")
    historical = HistoricalInputs(
        artifact(hist["stdout"]),
        artifact(hist["stderr"]),
        artifact(hist["receipt"]),
        tuple(refs),
        tuple(origins),
        hist["recipe_sha256"],
        hist["result_count"],
    )
    artifacts = (historical.stdout, historical.stderr, historical.receipt, *historical.originals)
    if len({ref.path for ref in artifacts}) != len(artifacts):
        fail("IC_DUPLICATE_EVIDENCE")
    cur = closed(
        obj["current"],
        {
            "task_id": str,
            "date": str,
            "path": str,
            "declared_recipe_sha256": str,
            "result_count": int,
            "row_template_sha256": str,
            "prior_correction": list,
        },
    )
    positive(cur["result_count"])
    sha(cur["declared_recipe_sha256"])
    sha(cur["row_template_sha256"])
    safe_path(cur["path"])
    if not v1.is_safe_test_path(cur["path"]) or len(cur["prior_correction"]) > 1:
        fail("IC_CURRENT_SCHEMA")
    prior = target_record(cur["prior_correction"][0]) if cur["prior_correction"] else None
    cur["prior_correction"] = prior
    return Record(target, historical, Current(**cur))


class FrozenGit:
    """Bounded read-only local Git; no refs, replacements or ambient Git config."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.candidate = self.git("rev-parse", "--verify", "HEAD").decode().strip()
        sha(self.candidate, commit=True)
        if self.git("rev-parse", "--is-shallow-repository").strip() != b"false":
            fail("IC_SHALLOW")
        if self.git("for-each-ref", "--format=%(refname)", "refs/replace").strip():
            fail("IC_REPLACEMENTS")
        graft = self.git("rev-parse", "--git-path", "info/grafts").decode().strip()
        graft_path = Path(graft) if Path(graft).is_absolute() else self.root / graft
        if graft_path.exists():
            fail("IC_GRAFTS")
        self.tree = self.git("rev-parse", f"{self.candidate}^{{tree}}").decode().strip()
        self.entries: dict[str, dict[str, tuple[str, str]]] = {}

    def git(self, *args: str, limit: int = MAX_BLOB_BYTES) -> bytes:
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                [
                    "git",
                    "--no-replace-objects",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    f"core.hooksPath={os.devnull}",
                    *args,
                ],
                cwd=self.root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert process.stdout is not None
            output = bytearray()
            deadline = time.monotonic() + 30
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        fail("IC_GIT_TIMEOUT")
                    for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                        chunk = os.read(key.fd, min(65536, limit - len(output) + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                        output.extend(chunk)
                        if len(output) > limit:
                            fail("IC_GIT_SIZE")
            if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
                fail("IC_GIT")
            return bytes(output)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InvalidDeclarationError("IC_GIT_IO") from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()

    def ancestor(self, commit: str) -> None:
        sha(commit, commit=True)
        if self.git("cat-file", "-t", commit).strip() != b"commit":
            fail("IC_COMMIT_TYPE")
        if self.git("rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip() != commit:
            fail("IC_COMMIT_IDENTITY")
        self.git("merge-base", "--is-ancestor", commit, self.candidate)

    def inventory(self, commit: str) -> dict[str, tuple[str, str]]:
        if commit not in self.entries:
            raw = self.git("ls-tree", "-rz", "--full-tree", commit, limit=MAX_LEDGER_BYTES)
            entries = raw.rstrip(b"\0").split(b"\0") if raw else []
            if len(entries) > MAX_GIT_ENTRIES:
                fail("IC_GIT_ENTRIES")
            result: dict[str, tuple[str, str]] = {}
            for entry in entries:
                meta, path = entry.split(b"\t", 1)
                mode, kind, oid = meta.decode("ascii").split()
                result[path.decode("utf-8")] = (mode if kind == "blob" else kind, oid)
            self.entries[commit] = result
        return self.entries[commit]

    def blob(self, commit: str, path: str, *, limit: int = MAX_BLOB_BYTES) -> bytes:
        safe_path(path)
        mode, oid = self.inventory(commit).get(path, ("", ""))
        if mode not in {"100644", "100755"}:
            fail("IC_BLOB_REGULAR")
        size = int(self.git("cat-file", "-s", oid).strip())
        if size > limit:
            fail("IC_BLOB_SIZE")
        return self.git("cat-file", "blob", oid, limit=limit)

    def current(self, path: str, *, limit: int = MAX_BLOB_BYTES) -> bytes:
        expected = self.blob(self.candidate, path, limit=limit)
        # Keep every directory open: a later pathname lookup must never redirect
        # the read. O_NONBLOCK ensures a substituted FIFO cannot hang at open.
        descriptors: list[int] = []
        bindings: list[tuple[int, str, os.stat_result]] = []
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            parent = os.open(self.root, flags | os.O_DIRECTORY)
            descriptors.append(parent)
            root_info = os.fstat(parent)
            for part in path.split("/")[:-1]:
                child = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
                descriptors.append(child)
                bindings.append((parent, part, os.fstat(child)))
                parent = child
            leaf = path.split("/")[-1]
            fd = os.open(leaf, flags, dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                fail("IC_WORKTREE_REGULAR")
            bindings.append((parent, leaf, info))
            self._current_bindings(root_info, bindings)
            data = bytearray()
            while True:
                chunk = os.read(fd, min(65536, limit - len(data) + 1))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    fail("IC_WORKTREE_SIZE")
            after = os.fstat(fd)
            if self._file_identity(info) != self._file_identity(after) or data != expected:
                fail("IC_WORKTREE_DRIFT")
            self._current_bindings(root_info, bindings)
        except OSError as exc:
            raise InvalidDeclarationError("IC_WORKTREE_MISSING") from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)
        return expected

    @staticmethod
    def _file_identity(info: os.stat_result) -> tuple[int, ...]:
        # atime can legitimately change on read; ctime/mtime and inode cannot.
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def _current_bindings(
        self, root_info: os.stat_result, bindings: Sequence[tuple[int, str, os.stat_result]]
    ) -> None:
        if self._file_identity(os.stat(self.root, follow_symlinks=False)) != self._file_identity(root_info):
            fail("IC_WORKTREE_DRIFT")
        for parent, name, expected in bindings:
            actual = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if self._file_identity(actual) != self._file_identity(expected):
                fail("IC_WORKTREE_DRIFT")

    def unchanged(self) -> None:
        if self.git("rev-parse", "HEAD").decode().strip() != self.candidate:
            fail("IC_CANDIDATE_DRIFT")
        if self.git("status", "--porcelain", "--untracked-files=no").strip():
            fail("IC_CANDIDATE_DIRTY")


@dataclass(frozen=True)
class Relation:
    target: v1.DeclaredRow
    correction: v1.DeclaredRow
    record: Record
    record_path: str
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    candidate: str
    tree: str
    relations: tuple[Relation, ...]


def exact_row(rows: Sequence[v1.DeclaredRow], target: Target) -> v1.DeclaredRow:
    found = [row for row in rows if row.task_id == target.task_id and v1.row_sha256(row) == target.row_sha256]
    if len(found) != 1:
        fail("IC_ROW_IDENTITY")
    return found[0]


def source_row(git: FrozenGit, target: Target, row: v1.DeclaredRow) -> None:
    git.ancestor(target.source_commit)
    source = git.blob(target.source_commit, target.ledger_path, limit=MAX_LEDGER_BYTES)
    if digest(source) != target.ledger_sha256:
        fail("IC_SOURCE_LEDGER")
    lines = source.decode("utf-8").splitlines()
    if lines.count(row.raw_line) != 1:
        fail("IC_SOURCE_ROW")
    if row.declared_hash != target.declared_recipe_sha256:
        fail("IC_DECLARED_BINDING")


def assert_disjoint(links: Sequence[tuple[str, str]], v1_links: Sequence[tuple[str, str]]) -> None:
    occupied = {endpoint for edge in v1_links for endpoint in edge}
    for left, right in links:
        if left == right or left in occupied or right in occupied:
            fail("IC_GRAPH_COLLISION")
        occupied.update((left, right))


def validate_raw_streams(stdout: bytes, stderr: bytes, test_path: str, expected: str, count: int) -> None:
    """Internal consistency only; fabricated matching bytes never authorize replay/acceptance."""
    if max(len(stdout), len(stderr)) > MAX_CAPTURE_BYTES:
        fail("IC_CAPTURE_SIZE")
    try:
        output = stdout.decode("utf-8")
        errors = stderr.decode("utf-8")
    except UnicodeError as exc:
        raise InvalidDeclarationError("IC_CAPTURE_ENCODING") from exc
    results = v1.extract_result_lines(output)
    if v1.extract_result_lines(errors):
        fail("IC_STDERR_RESULTS")
    if not 0 < len(results) == count <= MAX_RESULTS or len(set(results)) != len(results):
        fail("IC_CAPTURE_COUNT")
    if any(not line.startswith(test_path + "::") or not line.endswith(" PASSED") for line in results):
        fail("IC_CAPTURE_SCOPE_OUTCOME")
    if re.search(r"\b(?:SKIPPED|XFAIL(?:ED)?|XPASS(?:ED)?|ERROR|FAILED|INTERRUPTED)\b", output + errors):
        fail("IC_CAPTURE_INCOMPLETE")
    if v1.compute_recipe_hash(results) != "sha256:" + expected:
        fail("IC_CAPTURE_RECIPE")


def build_plan(
    root: Path, ledger: str, ordinary: v1.SupersessionPlan, ledger_path: str = v1.DEFAULT_LEDGER_PATH
) -> Plan:
    rows = v1.select_rows(ledger.splitlines()).declared
    corrections = [row for row in rows if row.invalid_declaration is not None]
    # No v2 operational files may become orphaned when a marker is removed.
    if not corrections and not (root / "docs/evidence-corrections").exists():
        return Plan("", "", ())
    if ledger_path != v1.DEFAULT_LEDGER_PATH or len(corrections) > MAX_RELATIONS:
        fail("IC_PLAN_SCOPE")
    git = FrozenGit(root)
    if git.current(ledger_path, limit=MAX_LEDGER_BYTES) != ledger.encode("utf-8"):
        fail("IC_LEDGER_BYTES")
    relations: list[Relation] = []
    referenced: set[str] = set()
    for correction in corrections:
        marker = correction.invalid_declaration
        assert marker is not None
        if marker.record_path in referenced:
            fail("IC_RECORD_REUSE")
        data = git.current(marker.record_path, limit=MAX_JSON_BYTES)
        if digest(data) != marker.record_sha256:
            fail("IC_RECORD_HASH")
        record = parse_record(data)
        target = exact_row(rows, record.target)
        source_row(git, record.target, target)
        # IC-R1's ordered checks precede evidence, cache lookup and all executions.
        if target.test_path.encode() != record.target.test_path.encode():
            fail("IC_R1_TARGET_PATH_BINDING")
        if not correction.test_path == record.current.path == record.target.test_path:
            fail("IC_R1_CURRENT_PATH_MISMATCH")
        if record.current.prior_correction is not None:
            prior = exact_row(rows, record.current.prior_correction)
            if not record.current.prior_correction.test_path == prior.test_path == target.test_path:
                fail("IC_R1_CITED_ROW_PATH_MISMATCH")
            source_row(git, record.current.prior_correction, prior)
            for path, bound in (
                (prior.test_path, record.current.prior_correction.test_sha256),
                ("uv.lock", record.current.prior_correction.lock_sha256),
            ):
                if digest(git.blob(record.current.prior_correction.source_commit, path)) != bound:
                    fail("IC_PRIOR_SOURCE_HASH")
        try:
            git.current(target.test_path)
        except InvalidDeclarationError as exc:
            raise InvalidDeclarationError("IC_R1_TARGET_PATH_UNAVAILABLE") from exc
        if digest(git.blob(record.target.source_commit, target.test_path)) != record.target.test_sha256:
            fail("IC_SOURCE_TEST")
        if record.target.declared_recipe_sha256 != record.target.test_sha256:
            fail("IC_REASON_INAPPLICABLE")
        if digest(git.blob(record.target.source_commit, "uv.lock")) != record.target.lock_sha256:
            fail("IC_SOURCE_LOCK")
        if (
            correction.task_id != record.current.task_id
            or correction.row_date != record.current.date
            or correction.declared_hash != record.current.declared_recipe_sha256
            or v1.invalid_declaration_template_sha256(correction.raw_line)
            != record.current.row_template_sha256
        ):
            fail("IC_CURRENT_BINDING")
        if sum(row.task_id == correction.task_id for row in rows) != 1:
            fail("IC_NEW_CORRECTION_TASK")
        refs = (
            record.historical.stdout,
            record.historical.stderr,
            record.historical.receipt,
            *record.historical.originals,
        )
        referenced.add(marker.record_path)
        relations.append(
            Relation(target, correction, record, marker.record_path, tuple(ref.path for ref in refs))
        )
    assert_disjoint(
        [(v1.row_sha256(rel.correction), v1.row_sha256(rel.target)) for rel in relations],
        [(key, v1.row_sha256(edge.target)) for key, edge in ordinary.by_successor_row_sha256.items()],
    )
    git.unchanged()
    for index, relation in enumerate(relations):
        hist = relation.record.historical
        refs = (hist.stdout, hist.stderr, hist.receipt, *hist.originals)
        payloads: dict[str, bytes] = {}
        for ref in refs:
            payload = git.current(ref.path, limit=MAX_CAPTURE_BYTES)
            if digest(payload) != ref.sha256:
                fail("IC_EVIDENCE_HASH")
            payloads[ref.path] = payload
            referenced.add(ref.path)
        # A capture manifest extends committed custody, never grants authenticity.
        receipt_data = bounded_json(payloads[hist.receipt.path])
        if receipt_data.get("schema") == "maezo-historical-recipe-proof/v1":
            members = operational.archive_members(git, relation)
            directory = hist.receipt.path.rsplit("/", 1)[0]
            paths = {directory + "/" + name for name in members}
            referenced.update(paths)
            relations[index] = replace(
                relation, evidence_paths=tuple(sorted(set(relation.evidence_paths) | paths))
            )
        validate_raw_streams(
            payloads[hist.stdout.path],
            payloads[hist.stderr.path],
            relation.target.test_path,
            hist.recipe_sha256,
            hist.result_count,
        )
        output = payloads[hist.stdout.path].decode("utf-8")
        recipes = [v1.compute_recipe_hash(v1.extract_result_lines(output))]
        if relation.target.row_date and relation.target.row_date < v1.LEGACY_NODE_ID_RECIPE_CUTOFF_DATE:
            legacy = v1.extract_result_lines(output, node_id_regex=v1._RESULT_LINE_RE_LEGACY)
            if legacy:
                recipes.append(v1.compute_recipe_hash(legacy))
        if "sha256:" + relation.target.declared_hash in recipes:
            fail("IC_REASON_INAPPLICABLE")
    operational_paths = {path for path in git.inventory(git.candidate) if path.startswith(EVIDENCE_ROOT)}
    if operational_paths != referenced:
        fail("IC_ORPHAN_EVIDENCE")
    git.unchanged()
    return Plan(git.candidate, git.tree, tuple(relations))


@dataclass(frozen=True)
class UnresolvedCorrection:
    target_row_sha256: str
    correction_row_sha256: str
    candidate: str
    tree: str
    reason: str
    historical_claim_verified: Literal[False] = False
    status: Literal["UNRESOLVED"] = "UNRESOLVED"
    schema: Literal["maezo-ledger-correction-result/v1"] = "maezo-ledger-correction-result/v1"

    def to_dict(self) -> dict[str, str | bool]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def selected_unresolved(
    root: Path, plan: Plan, selected: Sequence[v1.DeclaredRow], base: str | None
) -> tuple[UnresolvedCorrection, ...]:
    if not plan.relations:
        return ()
    git = FrozenGit(root)
    if git.candidate != plan.candidate or git.tree != plan.tree:
        fail("IC_CANDIDATE_DRIFT")
    git.unchanged()
    selected_hashes = {v1.row_sha256(row) for row in selected}
    changed = (
        set(git.git("diff", "--name-only", "-z", base, git.candidate).decode().split("\0")) if base else set()
    )
    results: list[UnresolvedCorrection] = []
    for relation in plan.relations:
        target_sha = v1.row_sha256(relation.target)
        correction_sha = v1.row_sha256(relation.correction)
        dependencies = {
            relation.record_path,
            *relation.evidence_paths,
            relation.target.test_path,
            "uv.lock",
            "pyproject.toml",
            "scripts/ci/check_evidence_ledger_hashes.py",
            "scripts/ci/ledger_invalid_declarations.py",
        }
        # Any project/config source delta invalidates a previous current proof identity.
        source_changed = bool(CONFIG_FILES & changed) or any(
            path.startswith(SOURCE_PREFIXES) for path in changed
        )
        if not ({target_sha, correction_sha} & selected_hashes or dependencies & changed or source_changed):
            continue
        reason = "IC_REPLAY_ADAPTER_UNREVIEWED"
        lock = git.current("uv.lock")
        if digest(lock) != relation.record.target.lock_sha256:
            reason = "IC_LOCK_MISMATCH"
        if v1.is_live_test_path(relation.target.test_path):
            reason = "IC_ROOT_LIVE_OWNERSHIP_REQUIRED"
        results.append(UnresolvedCorrection(target_sha, correction_sha, plan.candidate, plan.tree, reason))
    git.unchanged()
    return tuple(results)
