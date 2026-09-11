"""Operational invalid-history proofs: authenticated archive, then TWO fresh runs.

The fixed catalogue is a reviewed source/custody boundary, never an input option.
The original byte-exact producer validator remains separate from current policy.
No source file in the real historical catalogue is executed by unit tests.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import tempfile
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from scripts.ci import check_evidence_ledger_hashes as checker
from scripts.ci import ledger_invalid_declarations as static

BASE = "7189bcb0b3532a48401bf86f376cf37e876adabf"
PRODUCER = "babecc190d4fc6987165f6f0899c886d2d0a4e71"
TOOL_SOURCES = {
    "scripts/dev/run_historical_unit_recipe.py": (
        PRODUCER,
        "25a412499cd64ae663de1358e488480cb6b6a5c0e772583ccc0bece45380877c",
    ),
    "scripts/ci/check_evidence_ledger_hashes.py": (
        BASE,
        "8ac5085b95b6d4e3cbaee4decfa1315306ae3747eb77a54a57024096c38bfcac",
    ),
    "scripts/dev/run_engine_integration.py": (
        BASE,
        "02810ce8ecda0c7d609c1f58a3b3da8816a1cc5190c37e746593bcd39f3b80d3",
    ),
}


@dataclass(frozen=True)
class ReviewedHistory:
    proof_id: str
    source: str
    test: str
    task: str
    date: str
    manifest_sha256: str
    receipt_sha256: str
    recipe_sha256: str
    result_count: int
    lock_sha256: str
    current_lock_sha256: str = ""
    prefix_commit: str = "5b70a6e9890c71a2f6a6851c89531eb8c89fd4c1"
    report_sha256: str = "6ac90678ca814b64109315dfd0fb7622a8cb50b107406ae837e188246aacb18f"

    def identity(self) -> tuple[str, str, str, str, str]:
        return (self.proof_id, self.source, self.test, self.task, self.date)


# The only invalid-source-hash member of the accepted PFV/D6 source catalogue.
# PFV equality under its older lock requires the separately reviewed v3 design.
INVALID_CATALOG: tuple[ReviewedHistory, ...] = (
    ReviewedHistory(
        "D6unit136d",
        "136d3edbda3976b91e406a7bc537e82639d97e21",
        "tests/unit/gateway/human/test_durable_projection.py",
        "PLAN-PORTAL-D6-DURABLE-HUMAN-COMMAND",
        "2026-09-08",
        "a73fa721e56910a2177ecbad5cfa179838b65ad8a543578fb23ae2d44015871f",
        "2ca01f8fa81b7eb502c768d1c1543c9045a3a9c459c38fb45c14665e99245d1a",
        "e010ef3ae703c67ada7a9fc3ec75a220e4c57682633ba092bf0a2fd367872f6a",
        30,
        "c3be627ffa9ec3e7448343dbcfda4e7734077a4a8d87a392f222026a2acd7272",
    ),
)


VALID_CATALOG: tuple[ReviewedHistory, ...] = (
    ReviewedHistory(
        "PFV8821",
        "8821f675b369db5de20b0c846d007015ec40fd1e",
        "tests/unit/platform/test_migration_0010_webhook_wamid_dedup.py",
        "DRIVER-IDEMPOTENCY-ORPHAN-TABLE",
        "2026-09-04",
        "6532d00f17c6e66ccd206d720ee35024b85b13e14c9a7f1e991535a514e71965",
        "6abed75319a208ecd785b50340b886ea648134720ac9c252997946a1326e2295",
        "87d98f6a6e748cc50149cfdee46dd1c874c6beb91e98e53196a945247e2cd306",
        19,
        "b2204cb37affc44306a05c3c2b24af2241bb6b3aaede74dbde4bbd46e86ef94f",
        "c3be627ffa9ec3e7448343dbcfda4e7734077a4a8d87a392f222026a2acd7272",
    ),
)


# Separately qualified explicit-async successor. The original two catalogues,
# their receipt schemas, and their own-lock rules are unchanged.
D7_CATALOG: tuple[ReviewedHistory, ...] = (
    ReviewedHistory(
        "D7unit802",
        "80206e29ab4bb4f71556b6f119ba9dd777370fe0",
        "tests/unit/gateway/test_engine_capability_contracts.py",
        "PLAN-PORTAL-D7-A-CAPABILITY-CONTRACTS",
        "2026-09-08",
        "4a8d780b958cf653d61e226f1ab0aa6cfeec4fe863545f8b8ce617a402c96e36",
        "ddee1f4089012b7605b822b10e8ed0adf94607c84ba59971483e4780f7c0c933",
        "1b40d6d8f30ead07b089417e02b0ff08a52fb3c3e9ad6540a7dad5fb5ec68b75",
        159,
        "c3be627ffa9ec3e7448343dbcfda4e7734077a4a8d87a392f222026a2acd7272",
        prefix_commit="315af58a496eac608a32ad66eca18783ed209d50",
        report_sha256="cb967991a03fe053fce982c75e917ec7697a3fde1aba688bd4a54204f8783f93",
    ),
)
D7_SOURCE_COMMIT = "63f1f1ca33e3887a545fc85ec27673feb44210d0"
D7_SOURCE_TREE = "1ccc563fef4fdd102d968ee7c4ba49a7f3447579"
D7_TOOL_SOURCES = {
    "scripts/dev/historical_async_recipe.py": (
        "241e0c9a9f636abe50f6765f148d0668c78ea3f26ab90f91725368434f83d4af"
    ),
    "scripts/dev/run_historical_catalog_recipe.py": (
        "a814ab8dacce1e6e5541af01e601aaa183d6e6917da014145a46d47ab54c9be4"
    ),
    "scripts/dev/historical_tool_sources.py": (
        "0c9f10f5b2a7a8e5a290ca43fb204bddfdc090462d3a02b318fe6e5297ab7c25"
    ),
}


def d7_reviewed(relation: static.Relation) -> ReviewedHistory | None:
    if relation.record.kind != "invalid-declaration":
        return None
    return next(
        (
            item
            for item in D7_CATALOG
            if (
                relation.record.target.source_commit,
                relation.target.test_path,
                relation.target.task_id,
                relation.target.row_date,
            )
            == (item.source, item.test, item.task, item.date)
        ),
        None,
    )


@contextmanager
def async_producer_capsule(repository: Path) -> Iterator[ModuleType]:
    """Candidate-owned exact 63f1 bytes, composed with the original pinned capsule.

    The reviewed branch is not asserted to be an ancestor of an integrated
    cherry-pick. Its byte identities are verified against BOTH running policy and
    the committed candidate before import; provenance retains its separate SHA.
    """
    policy = static.FrozenGit(Path(__file__).resolve().parents[2])
    candidate = static.FrozenGit(repository)
    payloads = {}
    for relative, expected in D7_TOOL_SOURCES.items():
        data = candidate.current(relative)
        if data != policy.current(relative) or static.digest(data) != expected:
            static.fail("D7_CURRENT_PRODUCER_SOURCE_BINDING")
        payloads[relative] = data
    with (
        producer_capsule(repository) as original,
        tempfile.TemporaryDirectory(prefix="maezo-d7-tools-") as tmp,
    ):
        root = Path(tmp)
        for relative, payload in payloads.items():
            dest = root / relative
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            dest.write_bytes(payload)
            dest.chmod(0o600)
        modules = []
        for relative in (
            "scripts/dev/historical_async_recipe.py",
            "scripts/dev/run_historical_catalog_recipe.py",
        ):
            path = root / relative
            spec = importlib.util.spec_from_file_location("ledger_d7_" + path.stem, path)
            if spec is None or spec.loader is None:
                static.fail("D7_PRODUCER_LOAD")
            module = importlib.util.module_from_spec(spec)
            exec(compile(payloads[relative], str(path), "exec"), module.__dict__)
            modules.append(module)
        recipe, catalog = modules
        adapter = ModuleType("ledger_finite_d7_execution")
        adapter.__dict__.update(original.__dict__)
        adapter.__dict__.update(
            original_producer=original,
            async_recipe=recipe,
            async_catalogue=catalog,
            capture_source=partial(recipe.capture_source, original),
            validate_receipt=partial(recipe.validate_receipt, original),
        )
        yield adapter


def recipe_closure(data: bytes) -> dict[str, str]:
    """Exact transitive module binding ASTs used by the frozen recipe and guard."""
    definitions: dict[str, ast.AST] = {}
    for node in ast.parse(data).body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            definitions[node.name] = node
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for imported in node.names:
                definitions[imported.asname or imported.name.split(".")[0]] = node
        elif isinstance(node, ast.Assign):
            for target_name in node.targets:
                if isinstance(target_name, ast.Name):
                    definitions[target_name.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            definitions[node.target.id] = node
    pending = [
        "extract_result_lines",
        "compute_recipe_hash",
        "recipe_environment",
        "_SOURCE_GUARD_PLUGIN",
        "_RESULT_LINE_RE_LEGACY",
        "LEGACY_NODE_ID_RECIPE_CUTOFF_DATE",
    ]
    result: dict[str, str] = {}
    while pending:
        current_name = pending.pop()
        if current_name in result:
            continue
        definition = definitions[current_name]
        result[current_name] = ast.dump(definition, include_attributes=False)
        pending.extend(
            child.id
            for child in ast.walk(definition)
            if isinstance(child, ast.Name) and child.id in definitions and child.id not in result
        )
    return result


@contextmanager
def producer_capsule(repository: Path) -> Iterator[ModuleType]:
    """Load the immutable approved producer and dependencies without rewriting pins."""
    git = static.FrozenGit(Path(__file__).resolve().parents[2])
    candidate_git = static.FrozenGit(repository)
    for relative in (
        "scripts/ci/ledger_history_proofs.py",
        "scripts/ci/ledger_invalid_declarations.py",
        "scripts/ci/ledger_archived_catalog.py",
    ):
        if candidate_git.current(relative) != git.current(relative):
            static.fail("IC_CURRENT_CONSUMER_SOURCE_BINDING")
    with tempfile.TemporaryDirectory(prefix="maezo-ledger-producer-") as temp:
        root = Path(temp)
        for relative, (commit, expected) in TOOL_SOURCES.items():
            git.ancestor(commit)
            payload = git.blob(commit, relative)
            if static.digest(payload) != expected:
                static.fail("IC_PRODUCER_SOURCE_PIN")
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(payload)
            path.chmod(0o600)
        original = (root / "scripts/ci/check_evidence_ledger_hashes.py").read_bytes()
        current_path = repository / "scripts/ci/check_evidence_ledger_hashes.py"
        if recipe_closure(
            candidate_git.current(current_path.relative_to(repository).as_posix())
        ) != recipe_closure(original):
            static.fail("IC_CURRENT_RECIPE_CLOSURE")
        path = root / "scripts/dev/run_historical_unit_recipe.py"
        spec = importlib.util.spec_from_file_location("ledger_original_producer", path)
        if spec is None or spec.loader is None:
            static.fail("IC_PRODUCER_LOAD")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


def archive_members(git: static.FrozenGit, relation: static.Relation) -> dict[str, bytes]:
    """Traverse a pinned capture manifest using the reviewed current fd/Git reader."""
    if d7_reviewed(relation) is not None:
        from scripts.ci import ledger_archived_catalog

        return ledger_archived_catalog.d7_archive_members(git, relation)
    receipt = relation.record.historical.receipt
    directory = receipt.path.rsplit("/", 1)[0]
    manifest_path = directory + "/manifest.json"
    refs = relation.record.historical.originals
    ref = next((item for item in refs if item.path == manifest_path), None)
    if ref is None:
        static.fail("IC_CAPTURE_MANIFEST_REQUIRED")
    raw = git.current(manifest_path, limit=static.MAX_JSON_BYTES)
    if static.digest(raw) != ref.sha256:
        static.fail("IC_CAPTURE_MANIFEST_HASH")
    manifest = static.bounded_json(raw)
    if not 1 <= len(manifest) <= 256:
        static.fail("IC_CAPTURE_MEMBERS")
    result = {"manifest.json": raw}
    for name, expected in manifest.items():
        if type(expected) is not str or name == "manifest.json":
            static.fail("IC_CAPTURE_MEMBERS")
        static.safe_path(name)
        static.sha(expected)
        payload = git.current(directory + "/" + name, limit=static.MAX_CAPTURE_BYTES)
        if static.digest(payload) != expected:
            static.fail("IC_CAPTURE_MEMBER_HASH")
        result[name] = payload
    if "receipt.json" not in result or receipt.path != directory + "/receipt.json":
        static.fail("IC_CAPTURE_RECEIPT_IDENTITY")
    return result


def materialize(directory: Path, members: Mapping[str, bytes]) -> None:
    directory.mkdir(mode=0o700)
    for relative, payload in members.items():
        static.safe_path(relative)
        target = directory / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(payload)


def validate_locked_inventory(lock_bytes: bytes, inventory: dict[str, Any]) -> None:
    """Every actually resolved distribution must belong to the immutable source lock."""
    lock = tomllib.loads(lock_bytes.decode("utf-8"))
    packages = lock.get("package", [])
    if not packages:
        static.fail("IC_LOCK_PACKAGES")
    allowed = {(re.sub(r"[-_.]+", "-", item["name"].lower()), item["version"]) for item in packages}
    seen: set[str] = set()
    for distribution in inventory["distributions"]:
        name = re.sub(r"[-_.]+", "-", distribution["name"].lower())
        if name in seen or (name, distribution["version"]) not in allowed:
            static.fail("IC_RESOLVED_DISTRIBUTION_LOCK")
        seen.add(name)
    if "pytest" not in seen:
        static.fail("IC_PYTEST_DISTRIBUTION_MISSING")


def recompute(
    producer: ModuleType,
    packet: Path,
    repo: Path,
    identity: tuple[str, str, str, str, str],
    observations: dict[str, str],
) -> dict[str, Any]:
    receipt: dict[str, Any] = producer.validate_receipt(packet, repo, identity, observations)
    validate_locked_inventory(
        producer.git(repo, "show", identity[1] + ":uv.lock"), receipt["environment"]["inventory"]
    )
    raw = producer.read(packet / "pytest.stdout", root=packet).decode("utf-8")
    raw += producer.read(packet / "pytest.stderr", root=packet).decode("utf-8")
    lines = checker.extract_result_lines(raw)
    if checker.compute_recipe_hash(lines) != receipt["coverage"]["recipe_sha256"]:
        static.fail("IC_CURRENT_RECIPE_RECOMPUTE")
    if len(lines) != receipt["coverage"]["selected_count"]:
        static.fail("IC_CURRENT_RECIPE_COUNT")
    return receipt


def require_fresh(receipt: dict[str, Any], since: datetime, until: datetime, prior: dict[str, Any]) -> None:
    started = datetime.fromisoformat(receipt["execution"]["started_at"])
    finished = datetime.fromisoformat(receipt["execution"]["finished_at"])
    if (
        started.tzinfo is None
        or finished.tzinfo is None
        or not since <= started <= finished <= until
        or receipt["environment"]["checkout"] == prior["environment"]["checkout"]
    ):
        static.fail("IC_FRESH_EXECUTION_REQUIRED")


@dataclass(frozen=True)
class CompleteCorrection:
    target_row_sha256: str
    correction_row_sha256: str
    candidate: str
    tree: str
    historical: dict[str, Any]
    current: dict[str, Any]
    archived: dict[str, Any]
    artifacts: dict[str, dict[str, str]]
    producer_sources: dict[str, tuple[str, str]]
    validator_sha256: str
    checker_sha256: str
    consumer_sources: dict[str, str]
    historical_claim_verified: bool = False
    historical_status: str = "INVALID_HISTORICAL_DECLARATION"
    current_status: Literal["CORRECTION_CURRENT_VERIFIED"] = "CORRECTION_CURRENT_VERIFIED"
    status: str = "CORRECTED_WITH_INVALID_HISTORY"
    schema: str = "maezo-ledger-correction-result/v1"
    matching_algorithm: str = "none"
    archive_validation: str = "literal-producer-local-runtime"
    archive_review_report_sha256: str = ""
    archive_review_manifest_sha256: str = ""
    successor_provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prove_relation(
    repo: Path,
    plan: static.Plan,
    relation: static.Relation,
    producer: ModuleType,
    out: Path,
    reviewed: ReviewedHistory,
) -> CompleteCorrection:
    """Internal execution seam. Production calls only after fixed catalogue lookup."""
    git = static.FrozenGit(repo)
    if git.candidate != plan.candidate or git.tree != plan.tree:
        static.fail("IC_CANDIDATE_DRIFT")
    git.unchanged()
    ledger_before = git.current(checker.DEFAULT_LEDGER_PATH, limit=static.MAX_LEDGER_BYTES).decode()
    verified_plan = static.build_plan(
        repo, ledger_before, checker.build_supersession_plan(repo, ledger_before)
    )
    if verified_plan != plan or relation not in verified_plan.relations:
        static.fail("IC_PRE_EXECUTION_PLAN_DRIFT")
    target = relation.record.target
    valid_history = relation.record.kind == "verified-history"
    git.ancestor(reviewed.prefix_commit)
    prefix = git.blob(reviewed.prefix_commit, checker.DEFAULT_LEDGER_PATH, limit=static.MAX_LEDGER_BYTES)
    if not ledger_before.encode().startswith(prefix):
        static.fail("IC_QUALIFIED_LEDGER_PREFIX")
    if (target.source_commit, target.test_path, target.task_id, relation.target.row_date) != (
        reviewed.source,
        reviewed.test,
        reviewed.task,
        reviewed.date,
    ):
        static.fail("IC_REVIEWED_SOURCE_IDENTITY")
    if (
        static.digest(git.current("uv.lock"))
        != (reviewed.current_lock_sha256 if valid_history else target.lock_sha256)
        or target.lock_sha256 != reviewed.lock_sha256
    ):
        static.fail("IC_LOCK_MISMATCH")
    record_bytes = static.bounded_json(git.current(relation.record_path, limit=static.MAX_JSON_BYTES))
    report_refs = [
        ref["artifact"] for ref in record_bytes["historical_evidence"]["originals"] if ref["role"] == "report"
    ]
    if len(report_refs) != 1 or report_refs[0]["sha256"] != reviewed.report_sha256:
        static.fail("IC_REVIEWED_REPORT_CUSTODY")
    members = archive_members(git, relation)
    if (
        static.digest(members["manifest.json"]) != reviewed.manifest_sha256
        or static.digest(members["successor.json" if reviewed in D7_CATALOG else "receipt.json"])
        != reviewed.receipt_sha256
    ):
        static.fail("IC_REVIEWED_ARCHIVE_CUSTODY")
    is_async = reviewed in D7_CATALOG
    archived = out / "archived"
    materialize(archived, members)
    # Exact receipt bytes are authenticated by the independently reviewed catalog pin.
    inner_archive = archived / "capture" if is_async else archived
    observations = producer.load(inner_archive / "receipt.json")["scope"]["original_observations"]
    archive_validation = "literal-producer-local-runtime"
    if is_async:
        from scripts.ci import ledger_archived_catalog

        old = ledger_archived_catalog.validate_d7_archived_receipt(
            producer, archived, repo, reviewed.identity()
        )
        validate_locked_inventory(git.blob(reviewed.source, "uv.lock"), old["environment"]["inventory"])
        archive_validation = "D7_ASYNC_ARCHIVED_PRODUCER_IDENTITY_BOUND"
    elif reviewed.proof_id in {"D6unit136d", "PFV8821"}:
        from scripts.ci import ledger_archived_catalog

        old = ledger_archived_catalog.validate_archived_catalog_receipt(
            producer, archived, repo, reviewed.identity()
        )
        validate_locked_inventory(git.blob(reviewed.source, "uv.lock"), old["environment"]["inventory"])
        archive_validation = "ARCHIVED_PRODUCER_IDENTITY_BOUND"
    else:
        old = recompute(producer, archived, repo, reviewed.identity(), observations)
    if (
        old["source"]["row_sha256"] != target.row_sha256
        or old["source"]["declaration"] != target.declared_recipe_sha256
    ):
        static.fail("IC_ARCHIVE_ROW_BINDING")
    if (
        old["coverage"]["recipe_sha256"] != "sha256:" + reviewed.recipe_sha256
        or old["coverage"]["selected_count"] != reviewed.result_count
    ):
        static.fail("IC_ARCHIVE_EXPECTATION")
    if (
        relation.record.historical.recipe_sha256 != reviewed.recipe_sha256
        or relation.record.historical.result_count != reviewed.result_count
    ):
        static.fail("IC_RECORD_EXPECTATION")
    # These are required NEW executions, never substituted by the archived receipt.
    historical_dir = out / "fresh-historical"
    historical_started = datetime.now(UTC)
    producer.capture_source(
        repo,
        reviewed.source,
        reviewed.test,
        historical_dir,
        reviewed.proof_id,
        reviewed.task,
        reviewed.date,
        observations,
    )
    historical = recompute(producer, historical_dir, repo, reviewed.identity(), observations)
    require_fresh(historical, historical_started, datetime.now(UTC), old)
    if (
        historical["coverage"]["recipe_sha256"] != "sha256:" + reviewed.recipe_sha256
        or historical["coverage"]["selected_count"] != reviewed.result_count
    ):
        static.fail("IC_FRESH_HISTORY_MISMATCH")
    raw = (
        producer.read(historical_dir / "pytest.stdout", root=historical_dir)
        + producer.read(historical_dir / "pytest.stderr", root=historical_dir)
    ).decode()
    eligible = [checker.compute_recipe_hash(checker.extract_result_lines(raw))]
    if reviewed.date < checker.LEGACY_NODE_ID_RECIPE_CUTOFF_DATE:
        legacy = checker.extract_result_lines(raw, node_id_regex=checker._RESULT_LINE_RE_LEGACY)
        if legacy:
            eligible.append(checker.compute_recipe_hash(legacy))
    equality = "sha256:" + target.declared_recipe_sha256 in eligible
    if valid_history:
        if not equality:
            static.fail("VH_FRESH_HISTORY_EQUALITY")
    elif equality or target.declared_recipe_sha256 != historical["source"]["test_sha256"]:
        static.fail("IC_REASON_INAPPLICABLE")
    git.unchanged()
    current_dir = out / "fresh-current"
    identity = (
        "current-correction",
        git.candidate,
        reviewed.test,
        relation.correction.task_id,
        relation.correction.row_date or "",
    )
    current_started = datetime.now(UTC)
    producer.capture_source(
        repo, identity[1], identity[2], current_dir, identity[0], identity[3], identity[4], observations
    )
    current = recompute(producer, current_dir, repo, identity, observations)
    require_fresh(current, current_started, datetime.now(UTC), historical)
    if (
        current["coverage"]["recipe_sha256"] != "sha256:" + relation.correction.declared_hash
        or current["coverage"]["selected_count"] != relation.record.current.result_count
    ):
        static.fail("IC_CURRENT_PROOF_MISMATCH")
    if current["source"]["row_sha256"] != checker.row_sha256(relation.correction):
        static.fail("IC_CURRENT_ROW_IDENTITY")
    # Rebuild all static input bindings against the same frozen candidate after both runs.
    ledger = git.current(checker.DEFAULT_LEDGER_PATH, limit=static.MAX_LEDGER_BYTES).decode()
    checked = static.build_plan(repo, ledger, checker.build_supersession_plan(repo, ledger))
    if checked != plan:
        static.fail("IC_POST_EXECUTION_PLAN_DRIFT")
    git.unchanged()
    result = CompleteCorrection(
        target.row_sha256,
        checker.row_sha256(relation.correction),
        git.candidate,
        git.tree,
        historical,
        current,
        old,
        {
            label: producer.Packet(directory).hashes()
            for label, directory in (
                ("archived", archived),
                ("historical", historical_dir),
                ("current", current_dir),
            )
        },
        {
            **TOOL_SOURCES,
            **({path: (git.candidate, pin) for path, pin in D7_TOOL_SOURCES.items()} if is_async else {}),
        },
        static.digest(Path(__file__).read_bytes()),
        static.digest(git.current("scripts/ci/check_evidence_ledger_hashes.py"))
        if "scripts/ci/check_evidence_ledger_hashes.py" in git.inventory(git.candidate)
        else static.digest(Path(checker.__file__).read_bytes()),
        {
            relative: static.digest((Path(__file__).resolve().parents[2] / relative).read_bytes())
            for relative in (
                "scripts/ci/ledger_history_proofs.py",
                "scripts/ci/ledger_archived_catalog.py",
                "scripts/ci/ledger_invalid_declarations.py",
            )
        },
    )
    result = replace(result, archive_validation=archive_validation)
    if archive_validation == "ARCHIVED_PRODUCER_IDENTITY_BOUND":
        result = replace(
            result,
            archive_review_report_sha256=ledger_archived_catalog.REVIEW_REPORT_SHA256,
            archive_review_manifest_sha256=ledger_archived_catalog.REVIEW_MANIFEST_SHA256,
        )
    if is_async:
        result = replace(
            result,
            archive_review_report_sha256=ledger_archived_catalog.D7_REVIEW_FILES["actual-REPORT.md"],
            archive_review_manifest_sha256=ledger_archived_catalog.D7_REVIEW_FILES["actual-MANIFEST.json"],
            successor_provenance={
                "reviewed_source_commit": D7_SOURCE_COMMIT,
                "reviewed_source_tree": D7_SOURCE_TREE,
                "candidate_tool_sources": D7_TOOL_SOURCES,
                "review_closure": ledger_archived_catalog.D7_REVIEW_FILES,
                "recipe": producer.async_recipe.RECIPE,
            },
        )
    if valid_history:
        result = replace(
            result,
            historical_claim_verified=True,
            historical_status="VERIFIED_HISTORY_OWN_LOCK",
            status="VERIFIED_HISTORY_RELATION",
            schema="maezo-ledger-verified-history-result/v1",
            matching_algorithm="fixed"
            if "sha256:" + target.declared_recipe_sha256 == eligible[0]
            else "legacy-date-eligible",
        )
    producer.write(out / "result.json", producer.encode(result.to_dict()))
    return result


def recorded_attempts(producer: ModuleType, directory: Path, test_path: str) -> int | None:
    """Diagnostic capture records only; never successful-proof or truth credit.

    Missing/corrupt partial captures are unknown, not invented zero executions.
    Failed or timed-out pytest command records count as observed attempts.
    """
    count = 0
    for name in ("fresh-historical", "fresh-current"):
        stage = directory / name
        try:
            stage.lstat()
        except FileNotFoundError:
            continue
        try:
            packet = producer.Packet(stage)
            command = static.bounded_json(packet.read("pytest.command.json"))
            if (
                type(command.get("argv")) is not list
                or type(command.get("cwd")) is not str
                or type(command.get("rc")) is not int
                or type(command.get("started_at")) is not str
                or command["argv"]
                != [
                    command["cwd"] + "/.venv/bin/python",
                    "-P",
                    "-m",
                    "pytest",
                    test_path,
                    "-v",
                    "--tb=no",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "ledger_source_guard",
                    *(["-p", "pytest_asyncio.plugin"] if hasattr(producer, "async_recipe") else []),
                ]
            ):
                return None
            count += 1
        except (OSError, ValueError, RuntimeError):
            return None
    return count


def execute_selected(
    repo: Path, plan: static.Plan, selected: tuple[static.UnresolvedCorrection, ...], output_root: Path | None
) -> tuple[CompleteCorrection | static.UnresolvedCorrection, ...]:
    results: list[CompleteCorrection | static.UnresolvedCorrection] = []
    for pending in selected:
        relation = next(
            rel
            for rel in plan.relations
            if checker.row_sha256(rel.correction) == pending.correction_row_sha256
        )
        reviewed = next(
            (
                item
                for item in (
                    VALID_CATALOG
                    if relation.record.kind == "verified-history"
                    else INVALID_CATALOG + D7_CATALOG
                )
                if (item.source, item.test, item.task, item.date)
                == (
                    relation.record.target.source_commit,
                    relation.target.test_path,
                    relation.target.task_id,
                    relation.target.row_date,
                )
            ),
            None,
        )
        if reviewed is None or output_root is None or pending.reason != "IC_REPLAY_ADAPTER_UNREVIEWED":
            results.append(pending)
            continue
        directory = output_root / pending.correction_row_sha256
        producer = None
        try:
            capsule = async_producer_capsule if reviewed in D7_CATALOG else producer_capsule
            with capsule(repo) as producer:
                producer.Packet(output_root)
                directory.mkdir(mode=0o700)
                results.append(prove_relation(repo, plan, relation, producer, directory, reviewed))
        except (OSError, ValueError, RuntimeError) as exc:
            results.append(
                replace(
                    pending,
                    reason="IC_OPERATIONAL_PROOF_REFUSED:" + type(exc).__name__,
                    recorded_fresh_attempts=recorded_attempts(producer, directory, relation.target.test_path)
                    if producer is not None
                    else None,
                )
            )
    return tuple(results)
