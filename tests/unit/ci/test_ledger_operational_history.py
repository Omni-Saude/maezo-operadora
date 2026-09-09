"""Tiny real Git/pytest proofs. Catalogue overrides are fixture-only, never record data."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import check_evidence_ledger_hashes as checker
from scripts.ci import ledger_history_proofs as proof
from scripts.ci import ledger_invalid_declarations as static

ROOT = Path(__file__).resolve().parents[3]
TEST = "tests/unit/test_tiny.py"
OLD = b"from maezo import VALUE\ndef test_old(): assert VALUE == 7\n"
NEW = b"from maezo import VALUE\ndef test_new(): assert VALUE == 7\ndef test_second(): assert True\n"
VALID_FIXTURE = {"valid": True}
HEADER = "| Task ID | Date | Author | Verifier | Commit | Evidence | Test hash | Status |\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def write(repo: Path, path: str, payload: bytes) -> None:
    dest = repo / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)


def row(task: str, digest: str, evidence: str = "synthetic") -> str:
    return f"| {task} | 2026-09-08 | A | V | source | {evidence} | sha256:{digest} ({TEST}) | unverified |"


def marker(digest: str, kind: str = "invalid-declaration") -> str:
    if kind == "verified-history":
        return (
            f"[ledger-supersedes:v3;kind=verified-history;record=docs/evidence-history/{digest}.json;"
            f"record_sha256={digest}]"
        )
    return (
        f"[ledger-supersedes:v2;kind=invalid-declaration;record=docs/evidence-corrections/{digest}.json;"
        f"record_sha256={digest}]"
    )


@pytest.fixture(scope="module")
def producer() -> Any:
    with proof.producer_capsule(ROOT) as helper:
        yield helper


@pytest.fixture(scope="module")
def historical(
    tmp_path_factory: pytest.TempPathFactory, producer: Any, request: pytest.FixtureRequest
) -> Any:
    repo = tmp_path_factory.mktemp("operational-historical-source")
    parameter = getattr(request, "param", OLD)
    valid_history = isinstance(parameter, dict)
    old_source = OLD if valid_history else parameter
    write(repo, TEST, old_source)
    write(repo, "src/maezo/__init__.py", b"VALUE=7\n")
    write(
        repo,
        "pyproject.toml",
        b'[project]\nname="tiny"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n[project.optional-dependencies]\ndev=["pytest==9.1.1"]\n[tool.uv]\npackage=false\n',
    )
    write(repo, ".python-version", b"3.12\n")
    for path in (
        "scripts/ci/check_evidence_ledger_hashes.py",
        "scripts/ci/ledger_history_proofs.py",
        "scripts/ci/ledger_invalid_declarations.py",
        "scripts/ci/ledger_archived_catalog.py",
    ):
        write(repo, path, (ROOT / path).read_bytes())
    oldrow = row(
        "TINY-OLD",
        checker.compute_recipe_hash([TEST + "::test_old PASSED"])[7:]
        if valid_history
        else static.digest(old_source),
    )
    write(repo, "docs/evidence-ledger.md", (HEADER + oldrow + "\n").encode())
    subprocess.run(
        ["uv", "lock", "--offline", "--no-config"],
        cwd=repo,
        env=producer.environment(),
        check=True,
        capture_output=True,
    )
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Tiny proof")
    git(repo, "config", "user.email", "tiny@example.invalid")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic source")
    source = git(repo, "rev-parse", "HEAD")
    out = tmp_path_factory.mktemp("operational-original") / "capture"
    receipt = producer.capture_source(
        repo, source, TEST, out, "fixture", "TINY-OLD", "2026-09-08", {"original": "0" * 64}
    )
    return repo, source, out, receipt, oldrow


@pytest.fixture
def candidate(tmp_path: Path, historical: Any, producer: Any) -> Any:
    source_repo, source, capture, receipt, oldrow = historical
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(source_repo), str(repo)], check=True)
    git(repo, "config", "user.name", "Tiny proof")
    git(repo, "config", "user.email", "tiny@example.invalid")
    target_sha = static.digest(oldrow.encode())
    valid_history = receipt["scope"]["historical_claim_truth"] == "equal"
    kind = "verified-history" if valid_history else "invalid-declaration"
    evidence_root = "docs/evidence-history/" if valid_history else "docs/evidence-corrections/"
    prefix = evidence_root + "evidence/" + target_sha + "/capture/"
    for path in capture.rglob("*"):
        if path.is_file():
            write(repo, prefix + path.relative_to(capture).as_posix(), path.read_bytes())

    def ref(name: str) -> dict[str, str]:
        return {"path": prefix + name, "sha256": static.digest((capture / name).read_bytes())}

    current_hash = checker.compute_recipe_hash([TEST + "::test_new PASSED", TEST + "::test_second PASSED"])[
        7:
    ]
    template = row("TINY-CURRENT", current_hash, marker("0" * 64, kind))
    reportpath = evidence_root + "evidence/" + target_sha + "/report.txt"
    write(repo, reportpath, b"SYNTHETIC source review fixture; no Maezo history")
    record: dict[str, Any] = {
        "schema": "maezo-ledger-invalid-declaration/v1",
        "reason": "declared-test-source-sha256",
        "target": {
            "task_id": "TINY-OLD",
            "row_sha256": target_sha,
            "declared_recipe_sha256": receipt["source"]["declaration"],
            "source_commit": source,
            "ledger_path": "docs/evidence-ledger.md",
            "ledger_sha256": receipt["source"]["ledger_sha256"],
            "test_path": TEST,
            "test_sha256": receipt["source"]["test_sha256"],
            "lock_sha256": receipt["source"]["config"]["uv.lock"],
        },
        "historical_evidence": {
            "stdout": ref("pytest.stdout"),
            "stderr": ref("pytest.stderr"),
            "receipt": ref("receipt.json"),
            "originals": [
                {
                    "role": "report",
                    "origin": "synthetic-review",
                    "artifact": {
                        "path": reportpath,
                        "sha256": static.digest((repo / reportpath).read_bytes()),
                    },
                },
                {"role": "manifest", "origin": "synthetic-capture", "artifact": ref("manifest.json")},
            ],
            "result_count": 1,
            "passed": 1,
            "failed": 0,
            "recipe_version": "fixed",
            "recipe_sha256": receipt["coverage"]["recipe_sha256"][7:],
        },
        "current": {
            "task_id": "TINY-CURRENT",
            "date": "2026-09-08",
            "path": TEST,
            "declared_recipe_sha256": current_hash,
            "result_count": 2,
            "row_template_sha256": checker.operational_template_sha256(template),
            "prior_correction": [],
        },
    }
    write(repo, TEST, NEW)
    if valid_history:
        record["schema"] = "maezo-ledger-verified-history/v1"
        del record["reason"]
        record["proof_id"] = "PFV8821"
        write(repo, "uv.lock", (repo / "uv.lock").read_bytes() + b"\n# synthetic separate current lock\n")

    def publish() -> None:
        data = json.dumps(record, sort_keys=True).encode()
        digest = static.digest(data)
        for old in (repo / evidence_root).glob("*.json"):
            old.unlink()
        write(repo, evidence_root + digest + ".json", data)
        write(
            repo,
            "docs/evidence-ledger.md",
            (
                HEADER + oldrow + "\n" + row("TINY-CURRENT", current_hash, marker(digest, kind)) + "\n"
            ).encode(),
        )
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "synthetic candidate", "--allow-empty")

    publish()
    reviewed = proof.ReviewedHistory(
        "fixture",
        source,
        TEST,
        "TINY-OLD",
        "2026-09-08",
        ref("manifest.json")["sha256"],
        ref("receipt.json")["sha256"],
        record["historical_evidence"]["recipe_sha256"],
        1,
        record["target"]["lock_sha256"],
        current_lock_sha256=static.digest((repo / "uv.lock").read_bytes()) if valid_history else "",
        prefix_commit=source,
    )

    def plan() -> static.Plan:
        ledger = (repo / "docs/evidence-ledger.md").read_text()
        return static.build_plan(repo, ledger, checker.build_supersession_plan(repo, ledger))

    return repo, reviewed, plan, record, publish, tmp_path


def test_actual_two_fresh_runs_and_false_historical_credit(candidate: Any, producer: Any) -> None:
    repo, reviewed, plan_fn, _, _, tmp = candidate
    plan = plan_fn()
    out = tmp / "proof"
    out.mkdir(mode=0o700)
    result = proof.prove_relation(repo, plan, plan.relations[0], producer, out, reviewed)
    assert result.status == "CORRECTED_WITH_INVALID_HISTORY"
    assert result.historical_claim_verified is False
    assert result.current_status == "CORRECTION_CURRENT_VERIFIED"
    assert result.historical["source"]["commit"] == reviewed.source
    assert result.current["source"]["commit"] == plan.candidate
    assert result.historical["coverage"]["selected_count"] == 1
    assert result.current["coverage"]["selected_count"] == 2
    assert result.historical["environment"]["checkout"] != result.current["environment"]["checkout"]
    assert result.historical["execution"]["started_at"] > result.historical["source"]["date"]
    assert (out / "fresh-historical/pytest.stdout").is_file()
    assert (out / "fresh-current/pytest.stdout").is_file()
    assert not isinstance(result, checker.RowVerification)


def test_cli_authorized_fixture_catalog_runs_both(
    candidate: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, reviewed, _, _, _, tmp = candidate
    monkeypatch.setattr(proof, "INVALID_CATALOG", (reviewed,))
    out = tmp / "results"
    out.mkdir(mode=0o700)
    assert checker.main(["--base", reviewed.source, "--proof-output", str(out)], repo_root=repo) == 0
    output = capsys.readouterr().out
    assert "ACCEPTED_WITH_INVALID_HISTORY: 1 current verified, 0 historical verified" in output
    assert "1 invalidated declarations, 1 corrected relations" in output
    assert "2 executions" in output


def test_archive_alone_no_output_or_non_catalog_cannot_accept(candidate: Any) -> None:
    repo, _, plan_fn, _, _, _ = candidate
    plan = plan_fn()
    pending = static.selected_unresolved(repo, plan, [plan.relations[0].correction], None)
    results = proof.execute_selected(repo, plan, pending, None)
    assert results[0].status == "UNRESOLVED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_sha256", "f" * 64),
        ("receipt_sha256", "f" * 64),
        ("source", "f" * 40),
        ("test", "tests/unit/unrelated.py"),
        ("lock_sha256", "f" * 64),
        ("recipe_sha256", "f" * 64),
        ("result_count", 3),
    ],
)
def test_reviewed_identity_cannot_be_supplied_by_record(
    candidate: Any, producer: Any, field: str, value: Any
) -> None:
    repo, reviewed, plan_fn, _, _, tmp = candidate
    plan = plan_fn()
    out = tmp / "refused"
    out.mkdir(mode=0o700)
    with pytest.raises(static.InvalidDeclarationError):
        proof.prove_relation(
            repo, plan, plan.relations[0], producer, out, replace(reviewed, **{field: value})
        )
    assert not (out / "fresh-historical").exists()
    assert not (out / "result.json").exists()


@pytest.mark.parametrize(
    "bad_current",
    [
        b"def test_new(): assert False\n",
        b"# no tests\n",
        b"import pytest\n@pytest.fixture(autouse=True)\ndef bad(): raise ValueError('setup')\n"
        b"def test_new(): pass\n",
        b"import pytest\n@pytest.mark.skip\ndef test_new(): pass\n",
    ],
)
def test_current_actual_failure_cannot_accept_history(
    candidate: Any, producer: Any, bad_current: bytes
) -> None:
    repo, reviewed, plan_fn, _, publish, tmp = candidate
    write(repo, TEST, bad_current)
    publish()
    plan = plan_fn()
    out = tmp / "failed-current"
    out.mkdir(mode=0o700)
    with pytest.raises((ValueError, static.InvalidDeclarationError)):
        proof.prove_relation(repo, plan, plan.relations[0], producer, out, reviewed)
    assert (out / "fresh-historical/receipt.json").is_file()
    assert not (out / "result.json").exists()


def test_candidate_drift_precedes_both_executions(candidate: Any, producer: Any) -> None:
    repo, reviewed, plan_fn, _, _, tmp = candidate
    plan = plan_fn()
    write(repo, TEST, b"modified")
    out = tmp / "drift"
    out.mkdir(mode=0o700)
    with pytest.raises(static.InvalidDeclarationError):
        proof.prove_relation(repo, plan, plan.relations[0], producer, out, reviewed)
    assert not list(out.iterdir())


def test_producer_pin_and_current_recipe_closure_are_distinct(producer: Any) -> None:
    original = git(ROOT, "show", proof.BASE + ":scripts/ci/check_evidence_ledger_hashes.py").encode()
    # git() strips trailing newlines; AST equality intentionally ignores that transport formatting.
    current = (ROOT / "scripts/ci/check_evidence_ledger_hashes.py").read_bytes()
    assert proof.recipe_closure(original) == proof.recipe_closure(current)
    assert static.digest(current) != producer.PINS["scripts/ci/check_evidence_ledger_hashes.py"]
    assert (
        static.digest(Path(producer.__file__).read_bytes())
        == proof.TOOL_SOURCES["scripts/dev/run_historical_unit_recipe.py"][1]
    )


@pytest.mark.parametrize(
    "historical",
    [b"import os\ndef test_old(): assert 'fresh-historical' not in os.environ['HISTORICAL_PHASE_RECEIPT']\n"],
    indirect=True,
)
def test_authentic_archive_cannot_replace_failed_fresh_history(candidate: Any, producer: Any) -> None:
    repo, reviewed, plan_fn, _, _, tmp = candidate
    plan = plan_fn()
    out = tmp / "actual-history-fails"
    out.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        proof.prove_relation(repo, plan, plan.relations[0], producer, out, reviewed)
    command = json.loads((out / "fresh-historical/pytest.command.json").read_text())
    assert command["rc"] == 1
    assert not (out / "fresh-current").exists()
    assert not (out / "result.json").exists()


def test_complete_current_count_mismatch_after_two_actual_runs(candidate: Any, producer: Any) -> None:
    repo, reviewed, plan_fn, record, publish, tmp = candidate
    record["current"]["result_count"] = 3
    publish()
    plan = plan_fn()
    out = tmp / "wrong-current-count"
    out.mkdir(mode=0o700)
    with pytest.raises(static.InvalidDeclarationError, match="IC_CURRENT_PROOF_MISMATCH"):
        proof.prove_relation(repo, plan, plan.relations[0], producer, out, reviewed)
    assert (out / "fresh-historical/receipt.json").is_file()
    assert (out / "fresh-current/receipt.json").is_file()
    assert not (out / "result.json").exists()


@pytest.mark.parametrize(
    "distributions",
    [
        [{"name": "pytest", "version": "0"}],
        [{"name": "pytest", "version": "9.1.1"}, {"name": "pytest", "version": "9.1.1"}],
        [{"name": "intruder", "version": "1"}],
        [],
    ],
)
def test_resolved_inventory_must_match_immutable_lock(distributions: list[dict[str, str]]) -> None:
    lock = b'[[package]]\nname="pytest"\nversion="9.1.1"\n'
    with pytest.raises(static.InvalidDeclarationError):
        proof.validate_locked_inventory(lock, {"distributions": distributions})


def test_resolved_inventory_positive_control() -> None:
    proof.validate_locked_inventory(
        b'[[package]]\nname="pytest"\nversion="9.1.1"\n',
        {"distributions": [{"name": "pytest", "version": "9.1.1"}]},
    )


@pytest.mark.parametrize("historical", [VALID_FIXTURE], indirect=True)
def test_valid_history_actual_own_lock_two_runs(candidate: Any, producer: Any) -> None:
    repo, reviewed, plan_fn, _, _, tmp = candidate
    plan = plan_fn()
    assert reviewed.current_lock_sha256 != reviewed.lock_sha256
    out = tmp / "valid-proof"
    out.mkdir(mode=0o700)
    result = proof.prove_relation(repo, plan, plan.relations[0], producer, out, reviewed)
    assert result.historical_claim_verified is True
    assert result.historical_status == "VERIFIED_HISTORY_OWN_LOCK"
    assert result.status == "VERIFIED_HISTORY_RELATION"
    assert result.matching_algorithm == "fixed"
    assert result.schema == "maezo-ledger-verified-history-result/v1"
    assert result.historical["source"]["config"]["uv.lock"] == reviewed.lock_sha256
    assert result.current["source"]["config"]["uv.lock"] == reviewed.current_lock_sha256


@pytest.mark.parametrize("historical", [VALID_FIXTURE], indirect=True)
def test_valid_history_cli_truth_and_counters(
    candidate: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, reviewed, _, _, _, tmp = candidate
    monkeypatch.setattr(proof, "VALID_CATALOG", (reviewed,))
    out = tmp / "results"
    out.mkdir(mode=0o700)
    assert checker.main(["--base", reviewed.source, "--proof-output", str(out)], repo_root=repo) == 0
    output = capsys.readouterr().out
    assert (
        "ACCEPTED_WITH_VERIFIED_HISTORY: 1 current verified, 1 historical verified, 0 invalidated" in output
    )
    assert "2 executions" in output


@pytest.mark.parametrize("historical", [VALID_FIXTURE], indirect=True)
@pytest.mark.parametrize("field", ["lock_sha256", "current_lock_sha256"])
def test_valid_history_non_catalog_lock_refused(candidate: Any, producer: Any, field: str) -> None:
    repo, reviewed, plan_fn, _, _, tmp = candidate
    plan = plan_fn()
    out = tmp / "bad-lock"
    out.mkdir(mode=0o700)
    with pytest.raises(static.InvalidDeclarationError, match="IC_LOCK_MISMATCH"):
        proof.prove_relation(
            repo, plan, plan.relations[0], producer, out, replace(reviewed, **{field: "f" * 64})
        )
    assert not (out / "fresh-historical").exists()


@pytest.mark.parametrize("historical", [VALID_FIXTURE], indirect=True)
def test_valid_history_failed_current_has_no_acceptance(candidate: Any, producer: Any) -> None:
    repo, reviewed, plan_fn, _, publish, tmp = candidate
    write(repo, TEST, b"def test_current(): assert False\n")
    publish()
    plan = plan_fn()
    out = tmp / "bad-current"
    out.mkdir(mode=0o700)
    with pytest.raises((RuntimeError, ValueError)):
        proof.prove_relation(repo, plan, plan.relations[0], producer, out, reviewed)
    assert (out / "fresh-historical/receipt.json").is_file()
    assert not (out / "result.json").exists()


def test_portable_archive_uses_pinned_producer_identity_without_local_runtime(
    historical: Any, producer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.ci import ledger_archived_catalog as archive

    repo, source, packet, receipt, _ = historical
    monkeypatch.setattr(
        archive,
        "ARCHIVES",
        {
            "fixture": (
                static.digest((packet / "manifest.json").read_bytes()),
                static.digest((packet / "receipt.json").read_bytes()),
            )
        },
    )
    monkeypatch.setattr(
        archive,
        "PRODUCER_RUNTIMES",
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
    identity = ("fixture", source, TEST, "TINY-OLD", "2026-09-08")
    observations = receipt["scope"]["original_observations"]

    def foreign_runtime(*args: Any) -> str:
        raise RuntimeError("foreign local runtime must refuse literal validator")

    monkeypatch.setattr(producer, "runtime_digest", foreign_runtime)
    with pytest.raises(RuntimeError, match="foreign local runtime"):
        producer.validate_receipt(packet, repo, identity, observations)
    # Archive-only readback never calls the local runtime opener. No fresh credit.
    actual = archive.validate_archived_catalog_receipt(producer, packet, repo, identity)
    assert actual == receipt
    assert actual["scope"]["operational_validation"] == "not-performed"
    assert actual["scope"]["current_proof_disposition"] == "unresolved"
    with pytest.raises(RuntimeError, match="foreign local runtime"):
        producer.validate_receipt(packet, repo, identity, observations)


@pytest.mark.parametrize("mutation", ["manifest", "receipt", "member", "runtime", "source", "noncatalog"])
def test_portable_archive_rejects_before_trusting_changed_custody(
    historical: Any, producer: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    from scripts.ci import ledger_archived_catalog as archive

    repo, source, original, receipt, _ = historical
    packet = tmp_path / "archive"
    proof.materialize(
        packet,
        {p.relative_to(original).as_posix(): p.read_bytes() for p in original.rglob("*") if p.is_file()},
    )
    pinned = (
        static.digest((packet / "manifest.json").read_bytes()),
        static.digest((packet / "receipt.json").read_bytes()),
    )
    monkeypatch.setattr(archive, "ARCHIVES", {"fixture": pinned})
    if mutation == "manifest":
        (packet / "manifest.json").write_bytes(b"{}")
    elif mutation == "receipt":
        monkeypatch.setattr(archive, "ARCHIVES", {"fixture": (pinned[0], "f" * 64)})
    elif mutation == "member":
        (packet / "phase.json").write_bytes(b"{}")
    elif mutation == "runtime":
        monkeypatch.setattr(
            archive,
            "PRODUCER_RUNTIMES",
            {"python": ("/unapproved/runtime", "f" * 64), "uv": ("/unapproved/uv", "f" * 64)},
        )
    elif mutation == "source":
        monkeypatch.setattr(archive, "PRODUCER_SHA256", "f" * 64)
    identity = (
        "noncatalog" if mutation == "noncatalog" else "fixture",
        source,
        TEST,
        "TINY-OLD",
        "2026-09-08",
    )
    with pytest.raises((RuntimeError, ValueError)):
        archive.validate_archived_catalog_receipt(producer, packet, repo, identity)


@pytest.mark.parametrize(
    "line",
    [
        "[ledger-supersedes:v3;kind=invalid-declaration;record=docs/evidence-history/"
        + "a" * 64
        + ".json;record_sha256="
        + "a" * 64
        + "]",
        marker("a" * 64, "verified-history") + marker("a" * 64),
        marker("a" * 64, "verified-history").replace(
            "record_sha256=" + "a" * 64, "record_sha256=" + "b" * 64
        ),
        marker("a" * 64, "verified-history").replace("docs/evidence-history", "docs/evidence-corrections"),
        marker("a" * 64, "verified-history") + checker.HISTORY_ROW_TEMPLATE_TOKEN,
    ],
)
def test_closed_v3_marker_rejections(line: str) -> None:
    with pytest.raises(checker.SupersessionError):
        checker.parse_operational_marker(line)


@pytest.mark.parametrize(
    "relative",
    [
        "scripts/ci/ledger_history_proofs.py",
        "scripts/ci/ledger_invalid_declarations.py",
        "scripts/ci/ledger_archived_catalog.py",
    ],
)
def test_candidate_consumer_source_must_equal_running_policy(candidate: Any, relative: str) -> None:
    repo, _, _, _, publish, _ = candidate
    write(repo, relative, (repo / relative).read_bytes() + b"\n# candidate-only unreviewed delta\n")
    publish()
    with (
        pytest.raises(static.InvalidDeclarationError, match="IC_CURRENT_CONSUMER_SOURCE_BINDING"),
        proof.producer_capsule(repo),
    ):
        pytest.fail("candidate must not choose another consumer")


def test_full_qualified_ledger_prefix_before_replay(candidate: Any, producer: Any) -> None:
    repo, reviewed, plan_fn, _, _, tmp = candidate
    path = repo / "docs/evidence-ledger.md"
    path.write_text(path.read_text().removeprefix(HEADER))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "tamper untouched prefix")
    plan = plan_fn()
    out = tmp / "prefix-refusal"
    out.mkdir(mode=0o700)
    with pytest.raises(static.InvalidDeclarationError, match="IC_QUALIFIED_LEDGER_PREFIX"):
        proof.prove_relation(repo, plan, plan.relations[0], producer, out, reviewed)
    assert not (out / "fresh-historical").exists()


def test_recipe_import_binding_is_part_of_frozen_closure() -> None:
    data = (ROOT / "scripts/ci/check_evidence_ledger_hashes.py").read_bytes()
    assert proof.recipe_closure(
        data.replace(b"import hashlib\n", b"import json as hashlib\n")
    ) != proof.recipe_closure(data)


def test_ci_retains_failed_gate_output_and_private_directory(tmp_path: Path) -> None:
    import os

    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    gate = next(
        step for step in steps if step.get("name", "").startswith("Evidence-ledger Test-hash recompute gate")
    )
    upload = next(
        step
        for step in steps
        if step.get("name") == "Retain operational ledger proofs and refusal diagnostics"
    )
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "${{ runner.temp }}/ledger-operational-proofs/"
    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text("#!/bin/sh\nprintf 'fixture refusal\\n' >&2\nexit 23\n")
    uv.chmod(0o700)
    env = {"PATH": str(binary) + os.pathsep + os.defpath, "RUNNER_TEMP": str(tmp_path)}
    result = subprocess.run(["bash", "-c", gate["run"]], env=env, capture_output=True, text=True)
    assert result.returncode == 23
    output = tmp_path / "ledger-operational-proofs"
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "checker.rc").read_text() == "23\n"
    assert (output / "checker.stderr").read_text() == "fixture refusal\n"
    assert "--proof-output" in (output / "invocation.txt").read_text()
    # Existing directory is never reused, preserving prior run custody.
    repeated = subprocess.run(["bash", "-c", gate["run"]], env=env, capture_output=True)
    assert repeated.returncode != 0
    assert (output / "checker.rc").read_text() == "23\n"
