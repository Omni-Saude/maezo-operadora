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
OLD = b"import asyncio,pytest\n@pytest.mark.asyncio\nasync def test_old():\n await asyncio.sleep(0)\n assert True\n"
NEW = b"import asyncio,pytest\n@pytest.mark.asyncio\nasync def test_current():\n await asyncio.sleep(0)\n assert True\ndef test_sync(): assert True\n"
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
    return f"[ledger-supersedes:v2;kind=invalid-declaration;record=docs/evidence-corrections/{digest}.json;record_sha256={digest}]"


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
