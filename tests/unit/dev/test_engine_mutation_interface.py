"""MUI-01: interface fechada e controlador local; nenhum servico real."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from scripts.dev import run_engine_integration as runner

ROOT = Path(__file__).resolve().parents[3]

NODEID = (
    "tests/integration/chaos/test_crash_between_seams.py::"
    "test_b1a_mutation_check_chain_insert_outside_lock_turns_suite_red"
)


def test_cli_optin_accepts_only_canonical_selection() -> None:
    args = runner.build_parser().parse_args(
        ["run-mutation", "--checkout", ".", "--sha", "a" * 40, "--results-dir", "results", "--nodeid", NODEID]
    )
    assert args.nodeid == NODEID


@pytest.mark.parametrize("selection", ["b1a", NODEID + ";echo injected", NODEID + "[x]", "../x"])
def test_cli_rejects_free_selection(selection: str) -> None:
    with pytest.raises(SystemExit) as error:
        runner.build_parser().parse_args(
            [
                "run-mutation",
                "--checkout",
                ".",
                "--sha",
                "a" * 40,
                "--results-dir",
                "results",
                "--nodeid",
                selection,
            ]
        )
    assert error.value.code == 2


@pytest.mark.parametrize(
    "extra", [["--token", "b1a"], ["--suite", "core"], ["--", "-k", "x"], ["--test-file", "x"]]
)
def test_cli_cannot_override_token_suite_or_pytest_args(extra: list[str]) -> None:
    with pytest.raises(SystemExit):
        runner.build_parser().parse_args(
            [
                "run-mutation",
                "--checkout",
                ".",
                "--sha",
                "a" * 40,
                "--results-dir",
                "results",
                "--nodeid",
                NODEID,
                *extra,
            ]
        )


def _fixture(root: Path, monkeypatch: pytest.MonkeyPatch, nodeid: str, body: str) -> None:
    """Git real, fontes fixas e pytest real; corpo sintetico nao simula engine."""
    monkeypatch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
    test_file, function = nodeid.split("::")
    target = root / test_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import os,pytest\npytestmark=[pytest.mark.integration,pytest.mark.chaos]\n"
        f"def {function}():\n" + textwrap.indent(body, "    ") + "\n"
    )
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration", "chaos"]\n')
    (root / ".gitignore").write_text("__pycache__/\n")
    core = root / runner.EVIDENCE_RELATIVE
    core.parent.mkdir(parents=True, exist_ok=True)
    core.write_bytes((ROOT / runner.EVIDENCE_RELATIVE).read_bytes())
    (root / "scripts/dev").mkdir(parents=True)
    (root / "scripts/dev/docker-compose.engine-integration.yml").write_text("services: {}\n")
    (root / "docker-compose.yml").write_text("services: {}\n")
    for argv in [
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture"],
    ]:
        subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)
    sha = runner._git(root, "rev-parse", "HEAD")
    digests = {p: runner._sha256(root / p) for p in runner._git(root, "ls-files").splitlines()}
    runner._source_digests[root] = (sha, digests)


@pytest.mark.parametrize("nodeid", tuple(runner.MUTATIONS))
def test_derived_token_is_present_before_conftest_import_and_only_selected_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nodeid: str
) -> None:
    _fixture(tmp_path, monkeypatch, nodeid, "assert True")
    suite, test_file, token = runner._mutation_selection(nodeid)
    assert suite == ("chaos" if "/chaos/" in nodeid else "core")
    assert test_file == nodeid.split("::")[0]
    # Um conftest nao rastreado deve ser recusado; so o commit autorizado pode importar.
    conftest = tmp_path / "conftest.py"
    conftest.write_text(f"import os\nassert os.environ.get('MAEZO_CHAOS_MUTATE') == {token!r}\n")
    with pytest.raises(runner.RunnerError):
        runner._collect(tmp_path, [nodeid], tmp_path / "blocked.log", mutation_nodeid=nodeid)
    subprocess.run(["git", "add", "conftest.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid", "commit", "-qm", "Conftest"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    sha, digests = runner._source_digests[tmp_path]
    runner._source_digests[tmp_path] = (
        runner._git(tmp_path, "rev-parse", "HEAD"),
        {**digests, "conftest.py": runner._sha256(conftest)},
    )
    monkeypatch.setenv("MAEZO_CHAOS_MUTATE", "injected")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--invalid-option")
    selected = runner._collect(tmp_path, [nodeid], tmp_path / "active.log", mutation_nodeid=nodeid)
    assert selected == {nodeid}
    assert "MAEZO_CHAOS_MUTATE" not in runner._runtime_env()
    assert "MAEZO_CHAOS_MUTATE" not in runner._subprocess_env({"MAEZO_CHAOS_MUTATE": token})
    with pytest.raises(runner.RunnerError, match="coleta pytest falhou"):
        runner._collect(tmp_path, [nodeid], tmp_path / "normal.log")


@pytest.mark.parametrize(
    "defect", ["missing", "duplicate", "token", "xfail", "inactive", "identity", "extra", "source"]
)
def test_active_collection_rejects_identity_and_guard_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    item: dict[str, Any] = {
        "nodeid": NODEID,
        "source_sha256": "a" * 64,
        "xfail": None,
        "inactive_companion": {"mutation": "b1a", "obligation": "separate_opt_in_RED_required"},
    }
    inactive = [copy.deepcopy(item)]
    active = [{**item, "inactive_companion": None}]
    if defect == "missing":
        inactive = []
    if defect == "duplicate":
        inactive *= 2
    if defect == "token":
        inactive[0]["inactive_companion"]["mutation"] = "b1b"
    if defect == "xfail":
        inactive[0]["xfail"] = {"strict": True}
    if defect == "inactive":
        active[0]["inactive_companion"] = item["inactive_companion"]
    if defect == "identity":
        active[0]["nodeid"] = NODEID + "[other]"
    if defect == "extra":
        active *= 2
    if defect == "source":
        active[0]["source_sha256"] = "b" * 64

    def collect(_root: Path, _args: list[str], log: Path, **_kw: object) -> set[str]:
        runner._collected_items[log] = active
        return {i["nodeid"] for i in active}

    monkeypatch.setattr(runner, "_collect", collect)
    with pytest.raises(runner.RunnerError):
        runner._collect_mutation(tmp_path, NODEID, inactive, tmp_path)


@pytest.mark.parametrize(
    "body,pytest_rc,status",
    [
        ("assert 1 == 0", 1, "failure"),
        ("assert True", 0, "passed"),
        ("pytest.skip('unavailable')", 0, "skipped"),
        ("pytest.xfail('unexpected')", 0, "skipped"),
        ("raise ConnectionError('synthetic infra')", 1, "failure"),
    ],
)
def test_negative_evidence_never_becomes_pass_and_keeps_raw_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, pytest_rc: int, status: str
) -> None:
    body = "print('password=' + '_'.join(('MUI','PRIVATE','SENTINEL')))\n" + body
    _fixture(tmp_path, monkeypatch, NODEID, body)
    log = tmp_path / "collect.log"
    runner._collect(tmp_path, [NODEID], log, mutation_nodeid=NODEID)
    out = tmp_path / "output"
    result = runner._run_pytest(
        tmp_path,
        "chaos",
        NODEID.split("::")[0],
        runner._runtime_env(),
        out,
        1,
        runner._collected_items[log],
        mutation_nodeid=NODEID,
    )
    assert result["return_code"] == 1 and result["pytest_return_code"] == pytest_rc
    assert result["positive_validator_return_code"] == (0 if status == "passed" else 1)
    assert result["oracle_review_required"] is True
    assert result["cases"][0]["status"] == status
    assert bool(result["errors"]) == (status != "passed")
    public = "\n".join(p.read_text() for p in out.iterdir())
    assert "_".join(("MUI", "PRIVATE", "SENTINEL")) not in public
    evidence = json.loads((out / "pytest-execution.json").read_text())
    assert evidence["pytest_exitstatus"] == pytest_rc and evidence["finished"]
    if "assert 1" in body:
        assert "assert 1 == 0" in (out / "junit.xml").read_text()
        call = next(r for r in evidence["reports"] if r["phase"] == "call")
        assert call["outcome"] == "failed" and call["body_entered"] is True


def test_validator_exception_is_closed_and_artifacts_are_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(tmp_path, monkeypatch, NODEID, "assert 1 == 0")
    log = tmp_path / "collect.log"
    runner._collect(tmp_path, [NODEID], log, mutation_nodeid=NODEID)
    api = runner._evidence_api(tmp_path)

    def malformed(*_args: object) -> None:
        raise KeyError("outcome")

    api.validate_execution = malformed
    monkeypatch.setattr(runner, "_evidence_api", lambda _root: api)
    out = tmp_path / "output"
    with pytest.raises(runner.RunnerError, match="falhou fechado: KeyError"):
        runner._run_pytest(
            tmp_path,
            "chaos",
            NODEID.split("::")[0],
            runner._runtime_env(),
            out,
            1,
            runner._collected_items[log],
            mutation_nodeid=NODEID,
        )
    assert (out / "junit.xml").is_file() and (out / "pytest-execution.json").is_file()


LIFECYCLE = r"""
import argparse,contextlib,json,pathlib,sys
from scripts.dev import run_engine_integration as r
out=pathlib.Path(sys.argv[1]); mode=sys.argv[2]; root=pathlib.Path.cwd()
nodeid=next(iter(r.MUTATIONS)); suite,file,_=r._mutation_selection(nodeid)
r.LOCK_DIR=out/'engine.lock'
r.validate_checkout=lambda *_:(root,str(root/'src/maezo/__init__.py'))
r.execution_checkout=lambda *_:contextlib.nullcontext((root,str(root/'src/maezo/__init__.py')))
r._bind_execution_source=lambda *_:None
r._assert_execution_source=lambda *_:None
r.discover=lambda *_a,**_kw:{'execution_manifest':[{'suite':suite,'test_file':file,
 'expected_count':8,'items':[],'dependencies':{'engine_required':False}}]}
r._collect_mutation=lambda *_:[]
r._validate_docker_context=lambda *_:'synthetic-no-docker'
def start(*args):
 (out/'stack-started').touch();return ['synthetic-controller-no-services']
r._start_stack=start
original=r._run
r._run_pytest=lambda *_a,**_kw:{'return_code':1,'pytest_return_code':0 if mode=='survived' else 1}
if mode=='validator':
 def broken(*a,**kw): raise r.RunnerError('validador de execucao falhou fechado: KeyError')
 r._run_pytest=broken
if mode=='busy':
 lock=r.EngineLock.create(r.LOCK_DIR,checkout='fixture',sha='fixture',suite='fixture')
 assert lock.acquire(0)
def teardown(checkout,args,**kwargs):
 assert args==['down','-v','--remove-orphans']
 before=json.loads((out/'run-state.json').read_text())
 assert before['state']=='teardown' and before['return_code']!=0
 (out/'teardown-before.json').write_text(json.dumps(before))
 result=original([sys.executable,'-c',"import sys,time;print('controller teardown',flush=True);"+
  ("time.sleep(60)" if mode=='timeout' else "sys.exit(9)" if mode=='teardown' else "pass")],
  cwd=root,timeout=0.15 if mode=='timeout' else 5,log_path=out/'controller-teardown.log')
 if result.returncode: raise r.RunnerError('synthetic teardown rc='+str(result.returncode))
 return result
r._compose=teardown
args=argparse.Namespace(checkout=str(root),sha='a'*40,results_dir=str(out),lock_timeout=0,nodeid=nodeid)
raise SystemExit(r.run_mutation(args))
"""


@pytest.mark.parametrize("mode", ["red", "survived", "validator", "teardown", "timeout", "busy"])
def test_mutation_uses_same_lifecycle_and_never_publishes_green(tmp_path: Path, mode: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", LIFECYCLE, str(tmp_path), mode],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    state = json.loads((tmp_path / "run-state.json").read_text())
    assert result.returncode == state["return_code"] == (73 if mode == "busy" else 1)
    assert state["state"] != "passed"
    if mode == "busy":
        assert not (tmp_path / "stack-started").exists()
        assert not (tmp_path / "teardown-before.json").exists()
    else:
        assert (tmp_path / "stack-started").exists()
        assert (tmp_path / "teardown-before.json").exists()
    if mode in {"red", "survived", "validator"}:
        assert not (tmp_path / "engine.lock").exists()
        assert state.get("teardown_finished_at")
    else:
        assert (tmp_path / "engine.lock").exists()
    if mode == "survived":
        assert state["pytest_return_code"] == 0
    if mode in {"teardown", "timeout"}:
        owner = json.loads((tmp_path / "engine.lock/owner.json").read_text())
        assert owner["subprocess_quiescent"] is True
        assert state["state"] == "teardown_failed"
