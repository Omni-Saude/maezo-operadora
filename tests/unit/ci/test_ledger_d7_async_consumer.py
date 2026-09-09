"""Finite D7 consumer: tiny real async captures, separate archive/current proofs.

Every substituted catalogue/review/runtime pin belongs to a synthetic fixture.
Production catalogues cannot be selected or expanded by ledger/CLI data.
"""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_evidence_ledger_hashes as checker
from scripts.ci import ledger_archived_catalog as archive
from scripts.ci import ledger_history_proofs as proof
from scripts.ci import ledger_invalid_declarations as static

ROOT = Path(__file__).resolve().parents[3]
TEST = "tests/unit/test_async.py"
OLD = (
    b"import asyncio,pytest\n@pytest.mark.asyncio\nasync def test_old():\n"
    b" await asyncio.sleep(0)\n assert True\n"
)
NEW = (
    b"import asyncio,pytest\n@pytest.mark.asyncio\nasync def test_current():\n"
    b" await asyncio.sleep(0)\n assert True\ndef test_sync(): assert True\n"
)
HEADER = "| Task ID | Date | Author | Verifier | Commit | Evidence | Test hash | Status |\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.PIPE).decode().strip()


def write(repo: Path, path: str, data: bytes) -> None:
    dest = repo / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dest.chmod(0o600)


def row(task: str, digest: str, evidence: str = "synthetic", path: str = TEST) -> str:
    return f"| {task} | 2026-09-09 | A | V | source | {evidence} | sha256:{digest} ({path}) | unverified |"


def marker(digest: str) -> str:
    return (
        f"[ledger-supersedes:v2;kind=invalid-declaration;record=docs/evidence-corrections/{digest}.json;"
        f"record_sha256={digest}]"
    )


@pytest.fixture(scope="module")
def adapter() -> Any:
    with proof.async_producer_capsule(ROOT) as producer:
        yield producer


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory, adapter: Any) -> Any:
    repo = tmp_path_factory.mktemp("d7-tiny-source")
    write(repo, TEST, OLD)
    write(repo, "src/maezo/__init__.py", b"VALUE=7\n")
    write(
        repo,
        "pyproject.toml",
        b'[project]\nname="d7-tiny"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n[project.optional-dependencies]\ndev=["pytest==9.1.1","pytest-asyncio==1.4.0"]\n[tool.uv]\npackage=false\n',
    )
    write(repo, ".python-version", b"3.12\n")
    for path in (
        "scripts/ci/check_evidence_ledger_hashes.py",
        "scripts/ci/ledger_history_proofs.py",
        "scripts/ci/ledger_invalid_declarations.py",
        "scripts/ci/ledger_archived_catalog.py",
        *proof.D7_TOOL_SOURCES,
    ):
        write(repo, path, (ROOT / path).read_bytes())
    oldrow = row("TINY-ASYNC-OLD", static.digest(OLD))
    write(repo, "docs/evidence-ledger.md", (HEADER + oldrow + "\n").encode())
    subprocess.run(
        ["uv", "lock", "--offline", "--no-config"],
        cwd=repo,
        env=adapter.environment(),
        check=True,
        capture_output=True,
    )
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Tiny D7")
    git(repo, "config", "user.email", "tiny@example.invalid")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic async history")
    source = git(repo, "rev-parse", "HEAD")
    catalog = adapter.async_catalogue
    entry = catalog.SourceEntry(
        "tiny-d7",
        source,
        TEST,
        "TINY-ASYNC-OLD",
        "2026-09-09",
        static.digest(oldrow.encode()),
        static.digest(OLD),
        tuple(
            (path, static.digest((repo / path).read_bytes()))
            for path in (TEST, "docs/evidence-ledger.md", "uv.lock", "pyproject.toml", ".python-version")
        ),
    )
    out = tmp_path_factory.mktemp("d7-tiny-archive") / "outer"
    out.mkdir(mode=0o700)
    receipt = adapter.capture_source(
        repo,
        source,
        TEST,
        out / "capture",
        *[entry.proof_id, entry.task, entry.date],
        catalog.async_observations(adapter.original_producer, entry),
    )
    adapter.write(
        out / "successor.json", adapter.encode(catalog.async_envelope(adapter.original_producer, entry, out))
    )
    adapter.write(out / "manifest.json", adapter.encode(adapter.Packet(out).hashes()))
    return repo, source, out, receipt, oldrow, entry


@pytest.fixture
def candidate(tmp_path: Path, history: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    source_repo, source, capture, receipt, oldrow, entry = history
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(source_repo), str(repo)], check=True)
    git(repo, "config", "user.name", "Tiny D7")
    git(repo, "config", "user.email", "tiny@example.invalid")
    target_sha = static.digest(oldrow.encode())
    evidence = "docs/evidence-corrections/evidence/" + target_sha + "/"
    prefix = evidence + "archive/"
    for path in capture.rglob("*"):
        if path.is_file():
            write(repo, prefix + path.relative_to(capture).as_posix(), path.read_bytes())
    review_pins = {}
    for name in archive.D7_REVIEW_FILES:
        data = ("Synthetic D7 review fixture " + name).encode()
        write(repo, evidence + "review/" + name, data)
        review_pins[name] = static.digest(data)
    monkeypatch.setattr(archive, "D7_REVIEW_FILES", review_pins)
    monkeypatch.setattr(
        archive,
        "D7_INNER_PINS",
        {name: static.digest((capture / "capture" / name).read_bytes()) for name in archive.D7_INNER_PINS},
    )
    monkeypatch.setattr(archive, "D7_ARCHIVED_REPOSITORY", str(source_repo))
    monkeypatch.setattr(
        archive,
        "D7_PRODUCER_RUNTIMES",
        {
            "python": (
                receipt["environment"]["inventory"]["interpreter"]["path"],
                receipt["environment"]["inventory"]["interpreter"]["sha256"],
            ),
            "uv": (
                receipt["environment"]["preparation"]["uv_path"],
                receipt["environment"]["preparation"]["uv_sha256"],
            ),
        },
    )
    monkeypatch.setattr(adapter.async_catalogue, "SOURCE_CATALOG_V1", (entry,))

    def ref(name: str) -> dict[str, str]:
        return {"path": prefix + name, "sha256": static.digest((capture / name).read_bytes())}

    current_hash = checker.compute_recipe_hash([TEST + "::test_current PASSED", TEST + "::test_sync PASSED"])[
        7:
    ]
    record = {
        "schema": "maezo-ledger-invalid-declaration/v1",
        "reason": "declared-test-source-sha256",
        "target": {
            "task_id": entry.task,
            "row_sha256": target_sha,
            "declared_recipe_sha256": receipt["source"]["declaration"],
            "source_commit": source,
            "ledger_path": "docs/evidence-ledger.md",
            "ledger_sha256": receipt["source"]["ledger_sha256"],
            "test_path": TEST,
            "test_sha256": static.digest(OLD),
            "lock_sha256": receipt["source"]["config"]["uv.lock"],
        },
        "historical_evidence": {
            "stdout": ref("capture/pytest.stdout"),
            "stderr": ref("capture/pytest.stderr"),
            "receipt": ref("successor.json"),
            "originals": [{"role": "manifest", "origin": "synthetic-outer", "artifact": ref("manifest.json")}]
            + [
                {
                    "role": "report" if name == "actual-REPORT.md" else "packet",
                    "origin": "synthetic-review-" + name,
                    "artifact": {"path": evidence + "review/" + name, "sha256": pin},
                }
                for name, pin in review_pins.items()
            ],
            "result_count": 1,
            "passed": 1,
            "failed": 0,
            "recipe_version": "fixed",
            "recipe_sha256": receipt["coverage"]["recipe_sha256"][7:],
        },
        "current": {
            "task_id": "TINY-ASYNC-CURRENT",
            "date": "2026-09-09",
            "path": TEST,
            "declared_recipe_sha256": current_hash,
            "result_count": 2,
            "row_template_sha256": checker.operational_template_sha256(
                row("TINY-ASYNC-CURRENT", current_hash, marker("0" * 64))
            ),
            "prior_correction": [],
        },
    }
    write(repo, TEST, NEW)

    def publish() -> None:
        data = json.dumps(record, sort_keys=True).encode()
        digest = static.digest(data)
        for old in (repo / "docs/evidence-corrections").glob("*.json"):
            old.unlink()
        write(repo, "docs/evidence-corrections/" + digest + ".json", data)
        correction = row(
            record["current"]["task_id"], current_hash, marker(digest), record["current"]["path"]
        )
        write(repo, "docs/evidence-ledger.md", (HEADER + oldrow + "\n" + correction + "\n").encode())
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "synthetic async candidate", "--allow-empty")

    publish()
    reviewed = proof.ReviewedHistory(
        entry.proof_id,
        source,
        TEST,
        entry.task,
        entry.date,
        ref("manifest.json")["sha256"],
        ref("successor.json")["sha256"],
        record["historical_evidence"]["recipe_sha256"],
        1,
        record["target"]["lock_sha256"],
        prefix_commit=source,
        report_sha256=review_pins["actual-REPORT.md"],
    )
    monkeypatch.setattr(proof, "D7_CATALOG", (reviewed,))

    def plan() -> static.Plan:
        ledger = (repo / "docs/evidence-ledger.md").read_text()
        return static.build_plan(repo, ledger, checker.build_supersession_plan(repo, ledger))

    return repo, reviewed, plan, record, publish, tmp_path, prefix, entry


def run(candidate: Any, adapter: Any, name: str = "result") -> Any:
    repo, reviewed, plan_fn, _, _, tmp, _, _ = candidate
    plan = plan_fn()
    out = tmp / name
    out.mkdir(mode=0o700)
    return proof.prove_relation(repo, plan, plan.relations[0], adapter, out, reviewed)


def test_d7_real_new_history_and_current_use_explicit_owned_async_plugin(
    candidate: Any, adapter: Any
) -> None:
    result = run(candidate, adapter)
    assert result.status == "CORRECTED_WITH_INVALID_HISTORY"
    assert result.historical_claim_verified is False
    assert result.historical["source"]["commit"] == candidate[1].source
    assert result.current["source"]["commit"] == result.candidate
    assert result.historical["coverage"]["selected_count"] == 1
    assert result.current["coverage"]["selected_count"] == 2
    assert (
        len(
            {item["environment"]["checkout"] for item in (result.archived, result.historical, result.current)}
        )
        == 3
    )
    for item in (result.historical, result.current):
        assert item["schema"] == "maezo-historical-async-recipe-proof/v1"
        assert item["execution"]["argv"][-2:] == ["-p", "pytest_asyncio.plugin"]
        assert item["plugin"]["distribution"]["name"] == "pytest-asyncio"
        assert item["plugin"]["imports"]
    assert result.archive_validation == "D7_ASYNC_ARCHIVED_PRODUCER_IDENTITY_BOUND"
    assert result.successor_provenance["candidate_tool_sources"] == proof.D7_TOOL_SOURCES
    assert set(result.producer_sources) == set(proof.TOOL_SOURCES) | set(proof.D7_TOOL_SOURCES)


def test_d7_selected_consumer_dispatch_and_cli_counts(
    candidate: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    repo, reviewed, _, _, _, tmp, _, entry = candidate
    real = proof.async_producer_capsule

    @contextmanager
    def fixture_capsule(path: Path) -> Any:
        with real(path) as module:
            module.async_catalogue.SOURCE_CATALOG_V1 = (entry,)
            yield module

    monkeypatch.setattr(proof, "async_producer_capsule", fixture_capsule)
    out = tmp / "cli"
    out.mkdir(mode=0o700)
    assert checker.main(["--base", reviewed.source, "--proof-output", str(out)], repo_root=repo) == 0
    assert "2 executions" in capsys.readouterr().out
    assert len(list(out.rglob("result.json"))) == 1


def test_no_fresh_execution_never_grants_credit(candidate: Any) -> None:
    repo, _, plan_fn, _, _, _, _, _ = candidate
    plan = plan_fn()
    pending = static.selected_unresolved(repo, plan, [plan.relations[0].correction], None)
    assert proof.execute_selected(repo, plan, pending, None)[0].status == "UNRESOLVED"


@pytest.mark.parametrize("name", list(archive.D7_REVIEW_FILES))
def test_missing_separate_review_closure_refused(candidate: Any, name: str) -> None:
    repo, _, plan, record, publish, _, _, _ = candidate
    ref = next(
        item
        for item in record["historical_evidence"]["originals"]
        if item["artifact"]["path"].endswith("/" + name)
    )
    (repo / ref["artifact"]["path"]).unlink()
    publish()
    with pytest.raises(static.InvalidDeclarationError):
        plan()


@pytest.mark.parametrize(
    "name",
    [
        "successor.json",
        "manifest.json",
        "capture/manifest.json",
        "capture/receipt.json",
        "capture/phase.json",
        "capture/pytest.stdout",
        "capture/guard/ledger_source_guard.py",
    ],
)
def test_missing_nested_archive_never_accepts(candidate: Any, name: str) -> None:
    repo, _, plan, _, publish, _, prefix, _ = candidate
    (repo / (prefix + name)).unlink()
    publish()
    with pytest.raises(static.InvalidDeclarationError):
        plan()


@pytest.mark.parametrize(
    "field", ["schema", "producer", "catalogue", "inner_schema", "recipe", "observations", "inner_producer"]
)
def test_resealed_outer_forgery_cannot_replace_reviewed_packet(candidate: Any, field: str) -> None:
    repo, _, plan, record, publish, _, prefix, _ = candidate
    path = repo / prefix / "successor.json"
    obj = json.loads(path.read_bytes())
    obj[field] = "forged"
    path.write_bytes(json.dumps(obj).encode())
    record["historical_evidence"]["receipt"]["sha256"] = static.digest(path.read_bytes())
    manifest_path = repo / prefix / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["successor.json"] = static.digest(path.read_bytes())
    manifest_path.write_bytes(json.dumps(manifest).encode())
    record["historical_evidence"]["originals"][0]["artifact"]["sha256"] = static.digest(
        manifest_path.read_bytes()
    )
    publish()
    with pytest.raises(static.InvalidDeclarationError, match="D7_REVIEW_CLOSURE_REQUIRED"):
        plan()


@pytest.mark.parametrize(
    "source",
    [
        b"import pytest\n@pytest.mark.asyncio\nasync def test_current(): assert False\n",
        b"# deleted all tests\n",
        b"import pytest\n@pytest.mark.skip\ndef test_current(): pass\n",
    ],
)
def test_actual_current_failure_cannot_be_rescued(candidate: Any, adapter: Any, source: bytes) -> None:
    repo, _, _, _, publish, tmp, _, _ = candidate
    write(repo, TEST, source)
    publish()
    with pytest.raises((ValueError, RuntimeError)):
        run(candidate, adapter, "failed")
    assert (tmp / "failed/fresh-historical/receipt.json").is_file()
    assert not (tmp / "failed/result.json").exists()


@pytest.mark.parametrize("mutation", ["unrelated", "deleted", "renamed", "symlink", "prior-unrelated"])
def test_same_file_ordered_refusal_precedes_execution(candidate: Any, adapter: Any, mutation: str) -> None:
    repo, _, plan, record, publish, tmp, _, _ = candidate
    if mutation == "unrelated":
        record["current"]["path"] = "tests/unit/unrelated.py"
        write(repo, "tests/unit/unrelated.py", NEW)
    elif mutation == "prior-unrelated":
        record["current"]["prior_correction"] = [{**record["target"], "test_path": "tests/unit/unrelated.py"}]
    else:
        (repo / TEST).unlink()
        if mutation == "renamed":
            write(repo, "tests/unit/renamed.py", NEW)
        elif mutation == "symlink":
            write(repo, "tests/unit/replacement.py", NEW)
            (repo / TEST).symlink_to("replacement.py")
    publish()
    with pytest.raises(static.InvalidDeclarationError):
        plan()
    assert not (tmp / "result").exists()


def test_candidate_mutation_refused_before_replay(candidate: Any, adapter: Any) -> None:
    repo, reviewed, plan_fn, _, _, tmp, _, _ = candidate
    plan = plan_fn()
    write(repo, TEST, b"changed")
    out = tmp / "drift"
    out.mkdir(mode=0o700)
    with pytest.raises(static.InvalidDeclarationError):
        proof.prove_relation(repo, plan, plan.relations[0], adapter, out, reviewed)
    assert not list(out.iterdir())


@pytest.mark.parametrize("path", list(proof.D7_TOOL_SOURCES))
def test_current_candidate_producer_pin_refused(candidate: Any, path: str) -> None:
    repo, _, _, _, publish, _, _, _ = candidate
    write(repo, path, (repo / path).read_bytes() + b"\n# altered candidate producer\n")
    publish()
    with (
        pytest.raises(static.InvalidDeclarationError, match="D7_CURRENT_PRODUCER_SOURCE_BINDING"),
        proof.async_producer_capsule(repo),
    ):
        pytest.fail("changed candidate tooling must refuse")


@pytest.mark.parametrize(
    "mutation",
    ["schema", "producer", "source", "row", "lock", "plugin", "phase", "source-map", "foreign-plugin"],
)
def test_resealed_inner_cannot_evade_semantic_validation(
    candidate: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    repo, reviewed, _, _, _, tmp, prefix, _ = candidate
    packet = tmp / "forged-inner"
    proof.materialize(
        packet,
        {
            path.relative_to(repo / prefix).as_posix(): path.read_bytes()
            for path in (repo / prefix).rglob("*")
            if path.is_file()
        },
    )
    receipt = adapter.load(packet / "capture/receipt.json")
    if mutation == "schema":
        receipt["schema"] = "maezo-historical-recipe-proof/v1"
    elif mutation == "producer":
        receipt["source"]["tooling"]["helper"] = archive.PRODUCER_SHA256
    elif mutation == "source":
        receipt["source"]["commit"] = git(repo, "rev-parse", "HEAD")
    elif mutation == "row":
        receipt["source"]["row_sha256"] = "a" * 64
    elif mutation == "lock":
        receipt["source"]["config"]["uv.lock"] = "b" * 64
    elif mutation == "plugin":
        receipt["plugin"]["distribution"]["version"] = "0"
    elif mutation == "phase":
        phase = adapter.load(packet / "capture/phase.json")
        phase["coverage"]["phases"].pop()
        (packet / "capture/phase.json").write_bytes(adapter.encode(phase))
    elif mutation == "source-map":
        before = adapter.load(packet / "capture/source-before.json")
        before[TEST]["sha256"] = "a" * 64
        (packet / "capture/source-before.json").write_bytes(adapter.encode(before))
    else:
        phase = adapter.load(packet / "capture/phase.json")
        paths = phase["archived"]
        paths[:] = [p for p in paths if "pytest_asyncio" not in p]
        paths.extend(
            ["/foreign/plugin/pytest_asyncio/__init__.py", "/foreign/plugin/pytest_asyncio/plugin.py"]
        )
        (packet / "capture/phase.json").write_bytes(adapter.encode(phase))
    (packet / "capture/receipt.json").write_bytes(adapter.encode(receipt))
    # Deliberately re-pin only the synthetic fixture so this test reaches the
    # downstream semantics, independently of the immutable production hash wall.
    inner = packet / "capture"
    (inner / "manifest.json").write_bytes(
        adapter.encode(adapter.Packet(inner).hashes(exclude="manifest.json"))
    )
    pins = {name: static.digest((inner / name).read_bytes()) for name in archive.D7_INNER_PINS}
    monkeypatch.setattr(archive, "D7_INNER_PINS", pins)
    envelope = adapter.load(packet / "successor.json")
    envelope["capture"] = pins
    (packet / "successor.json").write_bytes(adapter.encode(envelope))
    (packet / "manifest.json").write_bytes(
        adapter.encode(adapter.Packet(packet).hashes(exclude="manifest.json"))
    )
    monkeypatch.setattr(
        proof,
        "D7_CATALOG",
        (
            replace(
                reviewed,
                manifest_sha256=static.digest((packet / "manifest.json").read_bytes()),
                receipt_sha256=static.digest((packet / "successor.json").read_bytes()),
            ),
        ),
    )
    with pytest.raises((ValueError, RuntimeError)):
        archive.validate_d7_archived_receipt(adapter, packet, repo, reviewed.identity())


def test_archive_portability_does_not_mutate_native_runtime_refusal(
    candidate: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, reviewed, _, _, _, tmp, prefix, _ = candidate
    packet = tmp / "portable-archive"
    proof.materialize(
        packet,
        {
            path.relative_to(repo / prefix).as_posix(): path.read_bytes()
            for path in (repo / prefix).rglob("*")
            if path.is_file()
        },
    )
    literal = adapter.original_producer

    def refused(*args: Any) -> str:
        raise ValueError("foreign runtime sentinel")

    monkeypatch.setattr(literal, "runtime_digest", refused)
    observations = adapter.load(packet / "capture/receipt.json")["scope"]["original_observations"]
    with pytest.raises(ValueError, match="foreign runtime sentinel"):
        adapter.validate_receipt(packet / "capture", repo, reviewed.identity(), observations)
    result = archive.validate_d7_archived_receipt(adapter, packet, repo, reviewed.identity())
    assert result["scope"]["status"] == "observation-only"
    assert result["scope"]["current_proof_disposition"] == "unresolved"
    assert literal.runtime_digest is refused
    with pytest.raises(ValueError, match="foreign runtime sentinel"):
        adapter.validate_receipt(packet / "capture", repo, reviewed.identity(), observations)


@pytest.mark.parametrize("stage", ["fresh-historical", "fresh-current"])
def test_replayed_stale_capture_refused_after_real_execution(
    candidate: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    repo, _, _, _, _, tmp, _, _ = candidate
    capture = adapter.capture_source

    def stale(*args: Any) -> Any:
        result = capture(*args)
        out = args[3]
        if out.name == stage:
            command = adapter.load(out / "pytest.command.json")
            command["started_at"] = "2000-01-01T00:00:00+00:00"
            command["finished_at"] = "2000-01-01T00:00:01+00:00"
            result["execution"] = command
            (out / "pytest.command.json").write_bytes(adapter.encode(command))
            (out / "receipt.json").write_bytes(adapter.encode(result))
            (out / "manifest.json").write_bytes(
                adapter.encode(adapter.Packet(out).hashes(exclude="manifest.json"))
            )
        return result

    monkeypatch.setattr(adapter, "capture_source", stale)
    with pytest.raises(static.InvalidDeclarationError, match="IC_FRESH_EXECUTION_REQUIRED"):
        run(candidate, adapter, "stale")
    assert (tmp / "stale" / stage / "receipt.json").is_file()
    assert not (tmp / "stale/result.json").exists()


@pytest.mark.parametrize("stage", ["fresh-historical", "fresh-current"])
def test_swapped_source_captures_refused(
    candidate: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    repo, reviewed, _, _, _, tmp, _, _ = candidate
    capture = adapter.capture_source

    def swapped(*args: Any) -> Any:
        values = list(args)
        if values[3].name == stage:
            values[1] = git(repo, "rev-parse", "HEAD") if stage == "fresh-historical" else reviewed.source
        return capture(*values)

    monkeypatch.setattr(adapter, "capture_source", swapped)
    with pytest.raises((ValueError, RuntimeError)):
        run(candidate, adapter, "swapped")
    assert not (tmp / "swapped/result.json").exists()


def test_d7_failed_new_history_does_not_use_old_pass(
    candidate: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing actual invocation is a refusal even though the archive authenticates.
    _, _, _, _, _, tmp, _, _ = candidate

    def unavailable(*args: Any) -> Any:
        raise RuntimeError("new historical capture unavailable")

    monkeypatch.setattr(adapter, "capture_source", unavailable)
    with pytest.raises(RuntimeError, match="new historical capture unavailable"):
        run(candidate, adapter, "missing-new")
    assert not (tmp / "missing-new/fresh-current").exists()
    assert not (tmp / "missing-new/result.json").exists()


def test_nested_evidence_diff_is_selected_without_row_delta(candidate: Any) -> None:
    repo, _, plan_fn, _, _, _, prefix, _ = candidate
    base = git(repo, "rev-parse", "HEAD")
    # Unrelated committed non-source documentation does not force execution.
    write(repo, "docs/unrelated.md", b"only documentation")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "unrelated docs")
    plan = plan_fn()
    assert static.selected_unresolved(repo, plan, [], base) == ()
    # Nested files are transitively relation-owned, not just outer JSON refs.
    assert prefix + "capture/source-before.json" in plan.relations[0].evidence_paths
    assert prefix + "capture/phase.json" in plan.relations[0].evidence_paths
    assert any("review/source-MANIFEST.json" in p for p in plan.relations[0].evidence_paths)
    # Selecting from before evidence existed forces the relation, with no row list.
    assert len(static.selected_unresolved(repo, plan, [], candidate[1].source)) == 1


@pytest.mark.parametrize(
    "mutation", ["duplicate-target", "duplicate-current", "orphan-inner", "orphan-review"]
)
def test_physical_occurrence_and_complete_evidence_closure(candidate: Any, mutation: str) -> None:
    repo, _, plan_fn, _, _, _, prefix, _ = candidate
    if mutation.startswith("duplicate"):
        path = repo / "docs/evidence-ledger.md"
        lines = path.read_text().splitlines()
        path.write_text(path.read_text() + lines[1 if mutation == "duplicate-target" else 2] + "\n")
    else:
        write(
            repo,
            prefix + ("capture/unlisted.txt" if mutation == "orphan-inner" else "../review/unlisted.txt"),
            b"orphan",
        )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "bad physical closure")
    with pytest.raises(static.InvalidDeclarationError):
        plan_fn()


def test_collision_cycle_and_valid_history_do_not_expand_d7(candidate: Any) -> None:
    _, _, _, record, _, _, _, _ = candidate
    with pytest.raises(checker.SupersessionError):
        static.assert_disjoint([("a", "b")], [("c", "b")])
    with pytest.raises(checker.SupersessionError):
        static.assert_disjoint([("a", "b"), ("b", "a")], [])
    value = dict(record, schema="maezo-ledger-verified-history/v1", proof_id="D7unit802")
    value.pop("reason")
    with pytest.raises(static.InvalidDeclarationError, match="IC_REASON"):
        static.parse_record(json.dumps(value).encode(), kind="verified-history")
