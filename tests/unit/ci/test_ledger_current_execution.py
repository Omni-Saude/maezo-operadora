"""Actual tiny pytest executions; G2 composition is a separately reviewed gate."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.ci import check_evidence_ledger_current as consumer
from scripts.ci import check_evidence_ledger_hashes as legacy
from scripts.ci import ledger_current_execution as current

ROOT = Path(__file__).resolve().parents[3]
TEST = "tests/unit/test_case.py"


def commit(root: Path, message: str) -> str:
    for args in (["add", "."], ["commit", "-qm", message]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return current.git(root, "rev-parse", "HEAD").decode().strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for key, value in [("user.name", "Current verdict fixture"), ("user.email", "current@example.invalid")]:
        subprocess.run(["git", "config", key, value], cwd=root, check=True)
    (root / TEST).parent.mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    (root / "uv.lock").write_bytes((ROOT / "uv.lock").read_bytes())
    (root / "docs/evidence-ledger.md").write_text("# Tiny private fixture\n")
    base = commit(root, "empty ledger")

    # This seam classifies ONLY this generated private source. It does not test or
    # replace production G2. Missing real G2 is separately proven to refuse.
    def admission(candidate: Path, test: str, **kwargs: Any) -> Any:
        assert candidate == root and test == TEST
        source = current.regular_bytes(root / test)
        return SimpleNamespace(
            status="OFFLINE",
            reason="private fixture seam",
            source_sha256=current.digest(source),
            dependencies=((test, current.digest(source)),),
        )

    monkeypatch.setattr(legacy, "classify_test_admission", admission, raising=False)
    # This suite isolates the observer behind its original explicit G2 seam.
    # Actual loaded-policy controls live in test_ledger_current_consumer.py.
    monkeypatch.setattr(current, "_g2_policy", lambda: {"private-fixture": "observer-only"})

    def build(source: str, expected: list[str] | None = None, *, conftest: str | None = None) -> Any:
        (root / TEST).write_text(source)
        if conftest is not None:
            (root / "conftest.py").write_text(conftest)
        result = expected or [f"{TEST}::test_case PASSED"]
        sha = legacy.compute_recipe_hash(result)
        raw = f"| CURRENT | 2026-09-09 | author | reviewer | source | evidence | {sha} ({TEST}) | test |"
        (root / "docs/evidence-ledger.md").write_text("# Tiny private fixture\n" + raw + "\n")
        commit(root, "fixture source")
        return root, current.Occurrence(2, raw), base

    return build


def execute(root: Path, occurrence: current.Occurrence, output: Path, *, timeout: float = 60) -> Any:
    runner = current.CurrentRunner(root, output, timeout=timeout)
    verdict = runner.run(occurrence)
    return runner, verdict, json.loads((Path(verdict.packet) / "receipt.json").read_text())


def test_real_pass_and_opaque_single_consumption(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo("def test_case(): assert 2 + 2 == 4\n")
    runner, verdict, receipt = execute(root, occurrence, tmp_path / "proof")
    assert verdict.status == "ACCEPTED", receipt
    forged = replace(verdict)
    assert not runner.consume(forged, occurrence)
    # A forged request consumes no other handle; this run cannot be replayed.
    assert not runner.consume(verdict, occurrence)
    assert receipt["returncode"] == 0
    assert receipt["recipe_hash"] == "sha256:" + occurrence.row.declared_hash


@pytest.mark.parametrize(
    "source, expected, reason",
    [
        ("def test_case(): assert False\n", [f"{TEST}::test_case FAILED"], "TERMINAL_BINDING"),
        (
            "import pytest\n@pytest.fixture\ndef fail(): raise RuntimeError('setup')\n"
            "def test_case(fail): pass\n",
            None,
            "TERMINAL_BINDING",
        ),
        (
            "import pytest\n@pytest.fixture\ndef fail():\n    yield\n    raise RuntimeError('teardown')\n"
            "def test_case(fail): pass\n",
            None,
            "TERMINAL_BINDING",
        ),
        ("import pytest\ndef test_case(): pytest.skip('unexpected')\n", None, "PHASE_NOT_COMPLETE_PASS"),
        (
            "import pytest\n@pytest.mark.xfail(reason='unexpected')\ndef test_case(): assert False\n",
            None,
            "PHASE_NOT_COMPLETE_PASS",
        ),
        (
            "import pytest\n@pytest.mark.xfail(reason='unexpected')\ndef test_case(): pass\n",
            None,
            "PHASE_NOT_COMPLETE_PASS",
        ),
        ("raise RuntimeError('collection')\n", None, "EMPTY_OR_INVALID_COLLECTION"),
        ("VALUE = 1\n", None, "EMPTY_OR_INVALID_COLLECTION"),
    ],
)
def test_real_nonpassing_execution_never_gets_current_credit(
    repo: Any, tmp_path: Path, source: str, expected: list[str] | None, reason: str
) -> None:
    root, occurrence, _ = repo(source, expected)
    runner, verdict, receipt = execute(root, occurrence, tmp_path / "proof")
    assert verdict.status != "ACCEPTED"
    assert reason in verdict.reasons, receipt
    assert not runner.consume(verdict, occurrence)
    assert (Path(verdict.packet) / "stdout").is_file()
    assert (Path(verdict.packet) / "stderr").is_file()
    if expected:
        assert receipt["recipe_hash"] == "sha256:" + occurrence.row.declared_hash
        assert receipt["returncode"] == 1


@pytest.fixture(scope="module")
def observed_pass(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A real observation captured once; pure adversarial controls mutate copies."""
    # Source setup uses the same private fixture discipline without global monkeypatch.
    root = tmp_path_factory.mktemp("observed-source")
    source = root / TEST
    source.parent.mkdir(parents=True)
    source.write_text("def test_case(): assert True\n")
    tool = ROOT / "scripts/ci/ledger_current_observer.py"
    output = tmp_path_factory.mktemp("observed-output")
    environment = current.minimal_environment(output)
    request = {
        "root": str(root),
        "test_path": TEST,
        "run_id": "private-observation",
        "observation": str(output / "observation.json"),
        "pytest_argv": [TEST, "-v", "--tb=no", "--disable-plugin-autoload", "-p", "no:cacheprovider"],
    }
    (output / "request.json").write_bytes(current.canonical(request))
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(tool), str(output / "request.json")],
        cwd=root,
        env=environment,
        capture_output=True,
        check=True,
    )
    observation = json.loads((output / "observation.json").read_text())
    binding = {
        **request,
        "environment": environment,
        "pytest_version": observation["runtime"]["pytest_version"],
        "allowed_sources": {path: current.digest(Path(path).read_bytes()) for path in observation["sources"]},
    }
    assert current.validate_observation(observation, binding, result.returncode) == ()
    return observation, binding


@pytest.mark.parametrize(
    "fault",
    [
        "drop_phase",
        "duplicate_phase",
        "orphan_phase",
        "drop_node",
        "duplicate_node",
        "deselect",
        "body_not_entered",
        "row_path",
        "root",
        "run_id",
        "runtime",
        "environment",
        "source_escape",
        "extra_field",
        "unfinished",
        "wrong_exit",
    ],
)
def test_observed_collection_phase_and_binding_counterexamples(observed_pass: Any, fault: str) -> None:
    observation, binding = copy.deepcopy(observed_pass)
    if fault == "drop_phase":
        observation["reports"].pop()
    elif fault == "duplicate_phase":
        observation["reports"].append(observation["reports"][0])
    elif fault == "orphan_phase":
        observation["reports"][0]["nodeid"] = "orphan"
    elif fault == "drop_node":
        observation["selected"] = []
    elif fault == "duplicate_node":
        observation["discovered"].append(observation["discovered"][0])
    elif fault == "deselect":
        observation["deselected"] = ["hidden"]
    elif fault == "body_not_entered":
        observation["reports"][1]["body_entered"] = False
    elif fault == "row_path":
        observation["test_path"] = "tests/unit/other.py"
    elif fault in {"root", "run_id"}:
        observation[fault] = "foreign"
    elif fault == "runtime":
        observation["runtime"]["version"] = "foreign"
    elif fault == "environment":
        observation["runtime"]["final_environment"]["EXTRA"] = "injected"
    elif fault == "source_escape":
        observation["sources"].append("/foreign/test.py")
    elif fault == "extra_field":
        observation["accepted"] = True
    elif fault == "unfinished":
        observation["finished"] = False
    elif fault == "wrong_exit":
        observation["exitstatus"] = 1
    assert current.validate_observation(observation, binding, 0)


def test_missing_g2_and_wrong_row_refuse_before_test_import(
    repo: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, occurrence, _ = repo("raise AssertionError('must not import')\n")
    monkeypatch.delattr(legacy, "classify_test_admission")
    _, verdict, _ = execute(root, occurrence, tmp_path / "proof")
    assert verdict.reasons == ("G2_ADMISSION_UNAVAILABLE",)
    assert not (Path(verdict.packet) / "stdout").exists()
    _, wrong, _ = execute(root, replace(occurrence, line=1), tmp_path / "other")
    assert wrong.reasons == ("ROW_SOURCE_BINDING",)


def test_real_timeout_retains_partial_output(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo("import time\ndef test_case():\n    time.sleep(10)\n")
    _, verdict, receipt = execute(root, occurrence, tmp_path / "proof", timeout=0.3)
    assert verdict.status != "ACCEPTED"
    assert "TIMEOUT_OR_OUTPUT_LIMIT" in verdict.reasons
    assert receipt["returncode"] != 0


@pytest.mark.parametrize("target", ["pyproject.toml", TEST, "uv.lock"])
def test_real_source_or_configuration_drift_refuses(repo: Any, tmp_path: Path, target: str) -> None:
    source = f"from pathlib import Path\ndef test_case():\n    Path({target!r}).write_text('# changed')\n"
    root, occurrence, _ = repo(source)
    _, verdict, _ = execute(root, occurrence, tmp_path / "proof")
    assert verdict.status != "ACCEPTED"
    assert "CANDIDATE_NOT_CLEAN" in verdict.reasons


def test_real_deselection_refuses(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo(
        "def test_case(): pass\n", conftest="def pytest_collection_modifyitems(items): items.clear()\n"
    )
    _, verdict, _ = execute(root, occurrence, tmp_path / "proof")
    assert verdict.status != "ACCEPTED"
    assert "COLLECTION_DUPLICATE_OR_CHANGED" in verdict.reasons


def test_failure_then_fresh_recovery_and_artifact_tamper(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo("def test_case(): assert False\n")
    _, failed, _ = execute(root, occurrence, tmp_path / "proof")
    frozen = (Path(failed.packet) / "receipt.json").read_bytes()
    root, recovered, _ = repo("def test_case(): assert True\n")
    runner, passed, _ = execute(root, recovered, tmp_path / "proof")
    assert passed.status == "ACCEPTED"
    assert failed.run_id != passed.run_id
    assert (Path(failed.packet) / "receipt.json").read_bytes() == frozen
    (Path(passed.packet) / "stdout").write_bytes(b"tampered")
    assert not runner.consume(passed, recovered)


def test_integrated_duplicate_occurrences_get_distinct_real_runs(repo: Any, tmp_path: Path) -> None:
    root, occurrence, base = repo("def test_case(): pass\n")
    with (root / "docs/evidence-ledger.md").open("a") as stream:
        stream.write(occurrence.raw + "\n")
    commit(root, "second identical physical occurrence")
    selected = consumer.selected_occurrences(root, base)
    assert [row.line for row in selected] == [2, 3]
    result = consumer.run_integrated(root, base, tmp_path / "proof")
    assert result["status"] == "ACCEPTED", result
    assert result["selected_count"] == 2
    assert len({row["current"]["run_id"] for row in result["rows"]}) == 2


def test_no_selected_occurrences_is_not_acceptance(repo: Any, tmp_path: Path) -> None:
    root, _, _ = repo("def test_case(): pass\n")
    head = current.git(root, "rev-parse", "HEAD").decode().strip()
    assert consumer.run_integrated(root, head, tmp_path / "proof")["status"] == "UNRESOLVED"


def test_real_async_body_is_observed(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo("import pytest\n@pytest.mark.asyncio\nasync def test_case(): assert True\n")
    runner, verdict, receipt = execute(root, occurrence, tmp_path / "proof")
    assert verdict.status == "ACCEPTED", receipt
    assert runner.consume(verdict, occurrence)


def test_matching_one_pass_hash_with_setup_error_is_not_accepted(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo(
        "import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError('setup')\n"
        "def test_case(): pass\ndef test_error(broken): pass\n"
    )
    _, verdict, receipt = execute(root, occurrence, tmp_path / "proof")
    assert receipt["recipe_hash"] == "sha256:" + occurrence.row.declared_hash
    assert receipt["returncode"] == 1
    assert verdict.status != "ACCEPTED"
    assert "PHASE_NOT_COMPLETE_PASS" in verdict.reasons


def test_real_interruption_is_not_acceptance(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo("import os, signal\ndef test_case(): os.kill(os.getpid(), signal.SIGINT)\n")
    _, verdict, receipt = execute(root, occurrence, tmp_path / "proof")
    assert verdict.status != "ACCEPTED"
    assert receipt["returncode"] != 0


def test_transient_source_write_restore_is_still_drift(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo(
        "from pathlib import Path\ndef test_case():\n"
        "    path = Path('pyproject.toml')\n    original = path.read_bytes()\n"
        "    path.write_bytes(b'# temporary change')\n    path.write_bytes(original)\n"
    )
    _, verdict, receipt = execute(root, occurrence, tmp_path / "proof")
    assert verdict.status != "ACCEPTED", receipt
    assert "SOURCE_DRIFT" in verdict.reasons


def test_legacy_spaced_recipe_preserved_with_complete_current_selection(repo: Any, tmp_path: Path) -> None:
    root, occurrence, _ = repo(
        "import pytest\n@pytest.mark.parametrize('x', [1, 2], ids=['ordinary', 'with space'])\n"
        "def test_case(x): assert x\n",
        [f"{TEST}::test_case[ordinary] PASSED"],
    )
    raw = occurrence.raw.replace("2026-09-09", "2026-09-04")
    (root / "docs/evidence-ledger.md").write_text("# Tiny private fixture\n" + raw + "\n")
    commit(root, "legacy date")
    occurrence = current.Occurrence(2, raw)
    runner, verdict, receipt = execute(root, occurrence, tmp_path / "proof")
    assert verdict.status == "ACCEPTED", receipt
    binding = json.loads((Path(verdict.packet) / "binding.json").read_text())
    assert binding["recipe_version"] == "legacy"
    assert runner.consume(verdict, occurrence)


def test_valid_history_obligations_cannot_be_replaced_by_current_pass(
    repo: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, occurrence, base = repo("def test_case(): pass\n")
    source = current.git(root, "rev-parse", "HEAD").decode().strip()
    marker = (
        f"[ledger-supersedes:v1;target=CURRENT;source_commit={source};"
        f"source_row_sha256={legacy.row_sha256(occurrence.row)};"
        f"source_test_sha256={current.digest((root / TEST).read_bytes())};"
        f"source_lock_sha256={current.digest((root / 'uv.lock').read_bytes())}]"
    )
    successor = occurrence.raw.replace("| CURRENT |", "| NEXT |", 1).replace("| evidence |", f"| {marker} |")
    with (root / "docs/evidence-ledger.md").open("a") as stream:
        stream.write(successor + "\n")
    commit(root, "bound history obligation")

    result = consumer.run_integrated(root, base, tmp_path / "proof")
    assert result["status"] == "UNRESOLVED"
    assert len(result["rows"]) == 2
    assert result["current_status"] == "ACCEPTED"
    assert len({row["current"]["run_id"] for row in result["rows"]}) == 2
    assert all(row["history"] == "UNRESOLVED" for row in result["rows"])


@pytest.mark.parametrize("returncode", [1, 2, 3, 4, 5, None, False])
def test_nonzero_or_untyped_terminal_status_never_accepts(observed_pass: Any, returncode: Any) -> None:
    observation, binding = copy.deepcopy(observed_pass)
    observation["exitstatus"] = returncode
    assert current.validate_observation(observation, binding, returncode)


def test_observation_schema_rejects_duplicate_keys_and_bad_phase_type(observed_pass: Any) -> None:
    with pytest.raises(ValueError, match="DUPLICATE_JSON_FIELD"):
        current.strict_json(b'{"schema":"one","schema":"two"}')
    observation, binding = copy.deepcopy(observed_pass)
    observation["reports"][0]["phase"] = []
    assert current.validate_observation(observation, binding, 0)


# Reuse the immutable producer's tiny fixture construction. These catalogues are
# explicitly synthetic representation seams, never production handle authority.
@pytest.fixture(scope="module")
def history_producer() -> Any:
    from scripts.ci import ledger_history_proofs as proof

    with proof.producer_capsule(ROOT) as producer:
        yield producer


@pytest.fixture(scope="module")
def adapter_historical(
    tmp_path_factory: pytest.TempPathFactory, history_producer: Any, request: pytest.FixtureRequest
) -> Any:
    from tests.unit.ci import test_ledger_operational_history as fixtures

    return fixtures.historical.__wrapped__(tmp_path_factory, history_producer, request)


@pytest.fixture
def adapter_candidate(tmp_path: Path, adapter_historical: Any, history_producer: Any) -> Any:
    from tests.unit.ci import test_ledger_operational_history as fixtures

    return fixtures.candidate.__wrapped__(tmp_path, adapter_historical, history_producer)


@pytest.mark.parametrize(
    "adapter_historical",
    [b"from maezo import VALUE\ndef test_old(): assert VALUE == 7\n", {"valid": True}],
    indirect=True,
)
def test_actual_finite_operational_adapter_fixture_scope(
    adapter_candidate: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.ci import ledger_current_relations as relations
    from scripts.ci import ledger_history_proofs as proof

    root, reviewed, _, _, _, _ = adapter_candidate
    base = reviewed.source
    runner = current.HistoryRunner(root, tmp_path / "adapter", base=base)
    edge = relations.plan_current_relations(root, base).relations[0]
    # The real production catalogue has no synthetic entry: no imports/runs/credit.
    missing = runner.run(edge.identity)
    assert missing.status == "UNRESOLVED", missing
    assert missing.execution_count == 0
    assert not runner.consume(missing, edge.identity)
    packet = tmp_path / "synthetic-operational"
    packet.mkdir(mode=0o700)
    own_lock = edge.claim.kind == "V3_VERIFIED_OWN_LOCK"
    monkeypatch.setattr(proof, "VALID_CATALOG" if own_lock else "INVALID_CATALOG", (reviewed,))
    # Actual unchanged capsule + prove_relation; only the finite catalogue is a
    # labelled fixture seam. The returned internal data is not an issued handle.
    data = runner._operational(edge, packet)
    assert data["status"] == ("VERIFIED_HISTORY_OWN_LOCK" if own_lock else "CORRECTED_WITH_INVALID_HISTORY")
    assert data["historical_claim_verified"] is own_lock
    assert data["execution_count"] == 2
    assert (packet / "producer-before.json").read_bytes() == (packet / "producer-after.json").read_bytes()
    report = json.loads((packet / "operational.json").read_text())
    assert report["historical"]["source"]["commit"] == reviewed.source
    assert report["current"]["source"]["commit"] != reviewed.source
    assert report["historical"]["environment"]["inventory"] != report["current"]["environment"]["inventory"]
    refused = runner.run(edge.identity)
    assert refused.status == "REFUSED"
    assert "HISTORY_LOADED_POLICY_DRIFT" in refused.reasons
    assert not runner.consume(refused, edge.identity)


@pytest.mark.parametrize(
    "adapter_historical",
    [b"import os\ndef test_old(): assert 'fresh-historical' not in os.environ['HISTORICAL_PHASE_RECEIPT']\n"],
    indirect=True,
)
def test_actual_operational_failed_fresh_history_retains_failure(
    adapter_candidate: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.ci import ledger_current_relations as relations
    from scripts.ci import ledger_history_proofs as proof

    root, reviewed, _, _, _, _ = adapter_candidate
    runner = current.HistoryRunner(root, tmp_path / "adapter", base=reviewed.source)
    edge = relations.plan_current_relations(root, reviewed.source).relations[0]
    monkeypatch.setattr(proof, "INVALID_CATALOG", (reviewed,))
    packet = tmp_path / "failed-history"
    packet.mkdir(mode=0o700)
    data = runner._operational(edge, packet)
    assert data["status"] == "FAILED"
    assert data["historical_claim_verified"] is False
    assert data["execution_count"] == 1
    command = packet / "producer" / edge.successor.row_sha256 / "fresh-historical/pytest.command.json"
    assert json.loads(command.read_text())["rc"] == 1
    assert not command.parent.parent.joinpath("fresh-current").exists()
    assert not (packet / "operational.json").exists()


@pytest.fixture(scope="module")
def async_history_producer() -> Any:
    from tests.unit.ci import test_ledger_d7_async_consumer as fixtures

    yield from fixtures.adapter.__wrapped__()


@pytest.fixture(scope="module")
def async_adapter_history(tmp_path_factory: pytest.TempPathFactory, async_history_producer: Any) -> Any:
    from tests.unit.ci import test_ledger_d7_async_consumer as fixtures

    return fixtures.history.__wrapped__(tmp_path_factory, async_history_producer)


def test_actual_finite_async_operational_adapter_fixture_scope(
    tmp_path: Path, async_adapter_history: Any, async_history_producer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    from scripts.ci import ledger_current_relations as relations
    from scripts.ci import ledger_history_proofs as proof

    from tests.unit.ci import test_ledger_d7_async_consumer as fixtures

    candidate = fixtures.candidate.__wrapped__(
        tmp_path, async_adapter_history, async_history_producer, monkeypatch
    )
    capsule = proof.async_producer_capsule

    @contextmanager
    def synthetic_catalogue_capsule(repository: Path) -> Any:
        # Carry the existing synthetic source catalogue into the fresh actual
        # capsule. Its pinned source bytes and both real captures are unchanged.
        with capsule(repository) as selected:
            selected.async_catalogue.SOURCE_CATALOG_V1 = (candidate[-1],)
            yield selected

    monkeypatch.setattr(proof, "async_producer_capsule", synthetic_catalogue_capsule)
    root, reviewed = candidate[:2]
    runner = current.HistoryRunner(root, tmp_path / "async-adapter", base=reviewed.source)
    edge = relations.plan_current_relations(root, reviewed.source).relations[0]
    packet = tmp_path / "synthetic-async-operational"
    packet.mkdir(mode=0o700)
    data = runner._operational(edge, packet)
    assert data["status"] == "CORRECTED_WITH_INVALID_HISTORY"
    assert data["historical_claim_verified"] is False
    assert data["execution_count"] == 2
    assert (packet / "producer-before.json").read_bytes() == (packet / "producer-after.json").read_bytes()
    result = json.loads((packet / "operational.json").read_text())
    assert result["archive_validation"] == "D7_ASYNC_ARCHIVED_PRODUCER_IDENTITY_BOUND"
    assert result["historical"]["coverage"]["selected_count"] == 1
    assert result["current"]["coverage"]["selected_count"] == 2
    # Synthetic archive/catalogue fixtures still cannot mint production handles.
    refused = runner.run(edge.identity)
    assert refused.status == "REFUSED"
    assert not runner.consume(refused, edge.identity)


# ---------------------------------------------------------------------------
# minimal_environment() forwards the ambient uv cache/install-dir (R6f, FLOOR-AN-389)
# ---------------------------------------------------------------------------
#
# PR #389 CI (two independent runs, byte-identical: 49 failed, 247 errors) — ci.yml's own
# "reject unmanaged Python and uv configuration" guard strips UV_CACHE_DIR/UV_PYTHON_INSTALL_DIR
# from the pytest process before it starts, but `uv sync` populated the ACTUAL cache at that
# non-default path. minimal_environment()'s subprocess env was built from scratch with no
# knowledge of it, so its own nested `uv lock --offline` fell back to uv's compiled-in default
# cache location — unpopulated in CI, and `--offline` forbids fetching anything to fill it.


def test_minimal_environment_forwards_uv_cache_dir_when_the_ci_mirror_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAEZO_CI_UV_CACHE_DIR", "/some/ci/cache")
    env = current.minimal_environment(tmp_path)
    assert env["UV_CACHE_DIR"] == "/some/ci/cache"


def test_minimal_environment_forwards_uv_python_install_dir_when_the_ci_mirror_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAEZO_CI_UV_PYTHON_INSTALL_DIR", "/some/ci/python-dir")
    env = current.minimal_environment(tmp_path)
    assert env["UV_PYTHON_INSTALL_DIR"] == "/some/ci/python-dir"


def test_minimal_environment_omits_uv_vars_when_the_ci_mirrors_are_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Local/dev baseline, unchanged by this fix: no `MAEZO_CI_UV_*` mirror set (the ordinary
    case outside CI) -> no `UV_CACHE_DIR`/`UV_PYTHON_INSTALL_DIR` key at all, exactly as before —
    this is an explicit allowlist forward, never a blanket `os.environ` passthrough that would
    defeat the "reject unmanaged config" guard PR-D itself introduced (S10b)."""
    monkeypatch.delenv("MAEZO_CI_UV_CACHE_DIR", raising=False)
    monkeypatch.delenv("MAEZO_CI_UV_PYTHON_INSTALL_DIR", raising=False)
    env = current.minimal_environment(tmp_path)
    assert "UV_CACHE_DIR" not in env
    assert "UV_PYTHON_INSTALL_DIR" not in env


def test_minimal_environment_never_forwards_the_bare_uv_names_themselves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setting the REAL `UV_CACHE_DIR` (not the `MAEZO_CI_` mirror) in the ambient environment
    must NOT leak into the subprocess env — only the two named `MAEZO_CI_UV_*` sources are ever
    read. A blanket `os.environ.get("UV_CACHE_DIR")` fallback would silently reintroduce exactly
    the ambient-config leak the guard exists to catch."""
    monkeypatch.setenv("UV_CACHE_DIR", "/leaked/ambient/cache")
    monkeypatch.delenv("MAEZO_CI_UV_CACHE_DIR", raising=False)
    env = current.minimal_environment(tmp_path)
    assert "UV_CACHE_DIR" not in env


def _real_uv_cache_dir() -> str:
    result = subprocess.run(
        [str(current._UV), "cache", "dir"],
        capture_output=True,
        text=True,
        check=True,
        env=current.minimal_environment(Path.home()),
    )
    return result.stdout.strip()


def _real_uv_python_install_dir() -> str:
    result = subprocess.run(
        [str(current._UV), "python", "dir"],
        capture_output=True,
        text=True,
        check=True,
        env=current.minimal_environment(Path.home()),
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    "discover,mirror",
    [
        (_real_uv_cache_dir, "MAEZO_CI_UV_CACHE_DIR"),
        (_real_uv_python_install_dir, "MAEZO_CI_UV_PYTHON_INSTALL_DIR"),
    ],
)
def test_uv_discovery_uses_ci_mirrors_after_bare_configuration_is_stripped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, discover: Any, mirror: str
) -> None:
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    directory = tmp_path / "qualified-ci-storage"
    monkeypatch.setenv(mirror, str(directory))
    assert discover() == str(directory)


def _write_tiny_lockable_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="tiny-uv-cache-repro"\nversion="0.0.0"\n'
        'requires-python=">=3.12,<3.13"\n'
        '[project.optional-dependencies]\ndev=["pytest==9.1.1"]\n'
        "[tool.uv]\npackage=false\n"
    )
    (root / ".python-version").write_text("3.12\n")


def test_actual_offline_lock_fails_with_a_fresh_home_and_no_forwarded_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RED reproduction of the actual PR #389 CI failure mode, locally, with a fresh HOME (per
    FLOOR-AN-389's own suggestion) — `uv lock --offline --no-config` cannot find `pytest==9.1.1`
    under a HOME whose default uv cache was never populated, and `--offline` forbids fetching it."""
    monkeypatch.delenv("MAEZO_CI_UV_CACHE_DIR", raising=False)
    monkeypatch.delenv("MAEZO_CI_UV_PYTHON_INSTALL_DIR", raising=False)
    fresh_home = tmp_path / "fresh-home"
    fresh_home.mkdir()
    project = tmp_path / "project"
    _write_tiny_lockable_project(project)
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            [str(current._UV), "lock", "--offline", "--no-config"],
            cwd=project,
            env=current.minimal_environment(fresh_home),
            check=True,
            capture_output=True,
        )


def test_actual_offline_lock_succeeds_with_a_fresh_home_once_the_real_cache_is_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GREEN counterpart: the IDENTICAL fresh-HOME setup as the RED test above, but with BOTH
    `MAEZO_CI_UV_CACHE_DIR` and `MAEZO_CI_UV_PYTHON_INSTALL_DIR` set to this machine's real
    (already-populated) uv cache/python-install dirs, using the CI step mirrors.
    Discovery must restore them before invoking uv because pytest has bare UV_* names stripped.
    `uv lock --offline` needs BOTH: the cache to resolve `pytest==9.1.1`
    from, and the managed-Python dir to find an interpreter matching `.python-version` without
    a network search (`--offline` forbids one) — confirmed empirically this session: forwarding
    only the cache still fails with `No interpreter found ... uv is set to offline mode`. The
    fresh HOME's own default cache/python dirs stay untouched (never populated, never needed)."""
    real_cache = _real_uv_cache_dir()
    real_python_dir = _real_uv_python_install_dir()
    assert Path(real_cache).is_dir(), f"this machine's uv cache is missing: {real_cache!r}"
    assert Path(real_python_dir).is_dir(), f"this machine's uv python dir is missing: {real_python_dir!r}"
    monkeypatch.setenv("MAEZO_CI_UV_CACHE_DIR", real_cache)
    monkeypatch.setenv("MAEZO_CI_UV_PYTHON_INSTALL_DIR", real_python_dir)
    fresh_home = tmp_path / "fresh-home"
    fresh_home.mkdir()
    project = tmp_path / "project"
    _write_tiny_lockable_project(project)
    env = current.minimal_environment(fresh_home)
    assert env["UV_CACHE_DIR"] == real_cache
    assert env["UV_PYTHON_INSTALL_DIR"] == real_python_dir
    subprocess.run(
        [str(current._UV), "lock", "--offline", "--no-config"],
        cwd=project,
        env=env,
        check=True,
        capture_output=True,
    )
    assert (project / "uv.lock").is_file()
    assert not (fresh_home / ".cache" / "uv").exists(), (
        "the fresh HOME's own default cache must stay untouched — only the forwarded "
        "MAEZO_CI_UV_CACHE_DIR path may be read"
    )
