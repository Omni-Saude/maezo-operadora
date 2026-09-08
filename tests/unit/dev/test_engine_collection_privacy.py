"""EIR-CTX-01: coleta, discovery e execução preservam originais privados."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.dev import run_engine_integration as runner

from tests.unit.dev.test_engine_runner_final_repair import authenticate_fixture

SECRET = "private collection detail 815693"


def fixture(root: Path, origin: str = "parameter", secret: str = SECRET) -> None:
    root.chmod(0o700)
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration","chaos"]\n')
    decorator = '@pytest.mark.parametrize("value",[VALUE],ids=[VALUE])\n' if origin == "parameter" else ""
    params = "value, record_property" if origin == "parameter" else "record_property"
    (root / "test_subject.py").write_text(
        f"import pytest\nfrom pathlib import Path\npytestmark=pytest.mark.integration\nVALUE={secret!r}\n"
        + decorator
        + "@pytest.mark.xfail(strict=True,reason=VALUE)\n"
        + f"def test_subject({params}):\n    Path('body-observed').touch()\n"
        + "    record_property('build_label','useful public diagnostic')\n"
        + ("    record_property('password',VALUE)\n" if origin == "property" else "")
        + "    assert 1 == 0\n"
    )
    authenticate_fixture(root)


@pytest.mark.parametrize("origin", ["parameter", "property"])
@pytest.mark.parametrize(
    "secret", [SECRET, 'private "quote" \\ end\nálpha', "1"], ids=["literal", "escaped", "short"]
)
def test_selected_collection_private_before_body_and_original_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str, secret: str
) -> None:
    fixture(tmp_path, origin, secret)
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    out = tmp_path / "out"
    out.mkdir()
    run_original, load_api = runner._run, runner._evidence_api
    seen: dict[str, Any] = {}

    def observed_run(argv: list[str], **kwargs: Any) -> Any:
        if "--collect-only" not in argv and not any(arg.startswith("--junitxml=") for arg in argv):
            return run_original(argv, **kwargs)
        if "--collect-only" in argv:
            raw_log = kwargs["log_path"]
            assert raw_log.stat().st_mode & 0o777 == 0o600
            assert raw_log.parent.stat().st_mode & 0o777 == 0o700
            assert (raw_log.parent / "execution.json").stat().st_mode & 0o777 == 0o600
            assert not (out / "collect-selected.log").exists()
            assert not (out / "collect-selected.execution.json").exists()
            result = run_original(argv, **kwargs)
            seen["raw_collection"] = json.loads((raw_log.parent / "execution.json").read_text())["collection"]
            seen["collection_private"] = raw_log.parent
            assert not (out / "collect-selected.log").exists()
            return result
        assert not (tmp_path / "body-observed").exists()
        collected = json.loads((out / "collect-selected.execution.json").read_text())
        assert collected["collection"][0]["xfail"]["reason"] != secret
        if secret != "1":
            assert secret not in "".join(p.read_text() for p in out.iterdir() if p.is_file())
        return run_original(argv, **kwargs)

    def api(checkout: Path) -> Any:
        loaded = load_api(checkout)
        validate = loaded.validate_execution

        def observe(xml: Path, evidence: dict[str, Any], expected: list[dict[str, Any]], rc: int) -> Any:
            assert expected == evidence["collection"] == seen["raw_collection"]
            assert expected[0]["xfail"]["reason"] == secret
            seen["validated"] = True
            return validate(xml, evidence, expected, rc)

        loaded.validate_execution = observe
        return loaded

    monkeypatch.setattr(runner, "_run", observed_run)
    monkeypatch.setattr(runner, "_evidence_api", api)
    result = runner._run_pytest(tmp_path, "core", "test_subject.py", runner._runtime_env(), out, 1)
    assert result["return_code"] == 0 and result["case_counts"] == {"xfailed_executed": 1}
    assert seen["validated"] and not seen["collection_private"].exists()
    public = json.loads((out / "collect-selected.execution.json").read_text())
    execution = json.loads((out / "pytest-execution.json").read_text())
    assert public["collection"] == execution["collection"]
    assert (
        public["collection"][0]["xfail"]["reason_sha256"]
        == seen["raw_collection"][0]["xfail"]["reason_sha256"]
    )
    if secret != "1":
        text = "".join(p.read_text() for p in out.iterdir() if p.is_file())
        assert all(form not in text for form in {secret, repr(secret)[1:-1], json.dumps(secret)[1:-1]})
        assert "useful public diagnostic" in (out / "junit.xml").read_text()


@pytest.mark.parametrize(
    "fault",
    [
        "source-before",
        "source-after",
        "malformed",
        "missing",
        "empty",
        "duplicate",
        "exitstatus",
        "reports",
        "interrupt",
        "timeout",
        "jsonio",
        "logio",
    ],
)
def test_collection_failure_invalidates_cache_and_stale_then_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    fixture(tmp_path)
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    out = tmp_path / "out"
    out.mkdir()
    log = out / "collect-selected.log"
    evidence = log.with_suffix(".execution.json")
    assert runner._collect(tmp_path, ["test_subject.py"], log)
    source = tmp_path / "test_subject.py"
    original_source = source.read_bytes()
    original_run, write = runner._run, runner._json_write
    private: list[Path] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        if "--collect-only" not in argv:
            return original_run(argv, **kwargs)
        directory = kwargs["log_path"].parent
        private.append(directory)
        assert not log.exists() and not evidence.exists() and log not in runner._collected_items
        if fault == "timeout":
            kwargs["timeout"] = 0.00001
        result = original_run(argv, **kwargs)
        raw = directory / "execution.json"
        if fault == "source-after":
            source.write_bytes(original_source + b"\n# source drift\n")
        elif fault == "malformed":
            raw.write_text("{" + SECRET)
        elif fault == "missing":
            raw.unlink()
        elif fault in {"empty", "duplicate", "exitstatus", "reports"}:
            payload = json.loads(raw.read_text())
            if fault == "empty":
                payload["collection"] = []
            elif fault == "duplicate":
                payload["collection"] *= 2
            elif fault == "exitstatus":
                payload["pytest_exitstatus"] = 1
            else:
                payload["reports"] = [{"reason": SECRET}]
            raw.write_text(json.dumps(payload))
        elif fault == "interrupt":
            raise runner.RunnerInterrupted("synthetic signal")
        elif fault == "timeout":
            assert result.returncode == 124
        return result

    def refuse(path: Path, payload: object) -> None:
        if path == (evidence if fault == "jsonio" else log):
            raise OSError(SECRET)
        write(path, payload)

    if fault == "source-before":
        source.write_bytes(original_source + b"\n# source drift\n")
    with monkeypatch.context() as changes:
        changes.setattr(runner, "_run", run)
        if fault in {"jsonio", "logio"}:
            changes.setattr(runner, "_json_write", refuse)
        with pytest.raises(
            runner.RunnerInterrupted if fault == "interrupt" else runner.RunnerError
        ) as caught:
            runner._collect(tmp_path, ["test_subject.py"], log)
        assert SECRET not in str(caught.value)
    assert log not in runner._collected_items
    assert all(not path.exists() for path in private)
    assert not (tmp_path / "body-observed").exists()
    assert not list(out.glob(".*.tmp"))
    assert SECRET not in "".join(p.read_text() for p in out.iterdir() if p.is_file())
    if evidence.exists():
        assert json.loads(evidence.read_text())["finished"] is False
    source.write_bytes(original_source)
    assert runner._collect(tmp_path, ["test_subject.py"], log)
    assert runner._collected_items[log][0]["xfail"]["reason"] == SECRET


def discovery_fixture(root: Path) -> None:
    root.chmod(0o700)
    (root / ".gitignore").write_text("__pycache__/\n")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration","chaos"]\n')
    files = [
        "tests/integration/test_sp_op_lgpd_dsr_001.py",
        "tests/integration/test_sp_op_escalation_001.py",
        "tests/integration/chaos/test_chaos.py",
        "tests/unit/test_db.py",
    ]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "import pytest\nfrom pathlib import Path\npytestmark=[pytest.mark.integration"
            + (",pytest.mark.chaos" if "chaos" in name else "")
            + f"]\n@pytest.mark.xfail(strict=True,reason={SECRET!r})\n"
            + "def test_subject():\n    Path('body-observed').touch()\n    assert False\n"
        )
    authenticate_fixture(root)


def test_discover_returns_original_manifest_but_publishes_only_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovery_fixture(tmp_path)
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    out = tmp_path / "out"
    payload = runner.discover(tmp_path, out, imported_module="synthetic fixture")
    assert payload["integration_count"] == 4
    assert not payload["validation_errors"] and not (tmp_path / "body-observed").exists()
    public = json.loads((out / "discovery.json").read_text())
    for original, projected in zip(payload["execution_manifest"], public["execution_manifest"], strict=True):
        for raw, safe in zip(original["items"], projected["items"], strict=True):
            assert raw["xfail"]["reason"] == SECRET
            assert safe["xfail"]["reason"] != SECRET
            assert raw["xfail"]["reason_sha256"] == safe["xfail"]["reason_sha256"]
    assert SECRET not in "".join(p.read_text() for p in out.iterdir() if p.is_file())
    # A nova tentativa falha na primeira fonte; nenhuma seleção anterior sobrevive.
    with (tmp_path / "tests/integration/test_sp_op_lgpd_dsr_001.py").open("a") as stream:
        stream.write("\n# source drift\n")
    with pytest.raises(runner.RunnerError):
        runner.discover(tmp_path, out, imported_module="synthetic fixture")
    assert not (out / "discovery.json").exists()
    for label in ("integration-dir", "core", "chaos", "db-unit"):
        path = out / f"collect-{label}.log"
        assert path not in runner._collected_items
        assert not path.exists() and not path.with_suffix(".execution.json").exists()


def test_core_remains_canonical_and_immutable() -> None:
    path = Path(__file__).resolve().parents[3] / runner.EVIDENCE_RELATIVE
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "e48d07899db37702ebd001415e37c636d233ed609a86a46f5439bd66647db77c"
    )
