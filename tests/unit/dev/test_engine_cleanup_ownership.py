"""Offline lifecycle countermodels: real Git/uv/source, Docker transport only stubbed."""

from __future__ import annotations

import gc
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.dev import run_engine_integration as r


@pytest.fixture
def tiny_repo(tmp_path):
    repo = tmp_path / "tiny-git"
    repo.mkdir()
    (repo / "scripts/dev").mkdir(parents=True)
    (repo / "src/maezo").mkdir(parents=True)
    (repo / "src/maezo/__init__.py").write_text("")
    (repo / ".gitignore").write_text("__pycache__/\n.venv/\n")
    (repo / "docker-compose.yml").write_text("services: {}\nvolumes:\n  pgdata: {}\n")
    (repo / "scripts/dev/docker-compose.engine-integration.yml").write_text("services: {}\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname="maezo"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n'
        '[project.optional-dependencies]\ndev=["pytest==9.1.1","pytest-asyncio==1.4.0"]\n'
        '[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n'
    )
    subprocess.run(["uv", "lock", "--offline", "--project", str(repo)], check=True)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Offline", "-c", "user.email=offline@example.invalid", "commit", "-qm", "fixture")
    sha = git("rev-parse", "HEAD")
    return repo, sha


@pytest.fixture
def owned(tmp_path, tiny_repo):
    repo, sha = tiny_repo
    results = tmp_path / "results"
    ctx = r.execution_checkout(repo, sha, results)
    checkout, _ = ctx.__enter__()
    lock = r.EngineLock.create(tmp_path / "fixture.lock", checkout=str(checkout), sha=sha, suite="offline")
    assert lock.acquire(0)
    r._process_owner = lock
    r._bind_execution_source(checkout, lock)
    contexts = [ctx]
    del ctx
    try:
        yield checkout, lock, contexts
    finally:
        contexts.clear()
        gc.collect()
        r._source_leases.pop(checkout, None)
        r._source_cleanup_ready.discard(checkout)
        r._source_digests.pop(checkout, None)
        # Only this synthetic fixture's resources; restore private ownership for disposal.
        lock.owner_path.write_text(json.dumps(lock.owner)) if lock.path.exists() else None
        lock.release()
        shutil.rmtree(checkout.parent, ignore_errors=True)
        r._process_owner = None


@pytest.mark.parametrize(
    "args", [["down", "-v", "--remove-orphans"], ["exec", "-T", "postgres", "psql"], ["ps"]]
)
def test_actual_compose_drift_blocks_every_dispatch(owned, monkeypatch, args):
    checkout, lock, _ = owned
    effects = []
    real_checked = r._checked

    def transport(command, **kwargs):
        if command[0] == "docker":
            effects.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_checked(command, **kwargs)

    monkeypatch.setattr(r, "_checked", transport)
    r._compose(checkout, args, env=r._runtime_env())
    assert len(effects) == 1
    effects.clear()
    (checkout / "docker-compose.yml").write_text(
        "services: {}\nvolumes:\n  pgdata:\n    name: foreign-offline-volume\n"
    )
    with pytest.raises(r.RunnerError):
        r._compose(checkout, args, env=r._runtime_env())
    assert effects == []
    assert lock.owns() and checkout.exists()


@pytest.mark.parametrize(
    "failure", ["lost-lease", "teardown-failed", "drift", "pending", "durable-nonquiescent"]
)
def test_context_finalization_retains_recovery_source(owned, failure):
    checkout, lock, contexts = owned
    if failure == "lost-lease":
        lock.owner_path.write_text(json.dumps(dict(lock.owner, token="other-fixture-owner")))
    elif failure == "drift":
        (checkout / "docker-compose.yml").write_text("services: {} # drift\n")
    elif failure == "pending":
        r._pending_groups.add(99999999)
    elif failure == "durable-nonquiescent":
        lock.update("synthetic-unconfirmed", subprocess_quiescent=False)
    contexts.clear()
    gc.collect()
    try:
        assert checkout.exists() and lock.path.exists()
        assert checkout in r._source_digests and checkout in r._source_leases
        assert not lock.release()
    finally:
        r._pending_groups.discard(99999999)
        lock.owner["subprocess_quiescent"] = True


def test_owned_success_removes_source_before_lease_release(owned):
    checkout, lock, contexts = owned
    assert not lock.release()
    r._source_cleanup_ready.add(checkout)
    contexts.pop().__exit__(None, None, None)
    assert not checkout.exists()
    assert checkout not in r._source_digests and checkout not in r._source_leases
    assert lock.owns()
    assert lock.release()


def test_cleanup_authorization_does_not_survive_lease_loss(owned):
    checkout, lock, contexts = owned
    r._source_cleanup_ready.add(checkout)
    lock.owner_path.write_text(json.dumps(dict(lock.owner, token="other-fixture-owner")))
    contexts.pop().__exit__(None, None, None)
    gc.collect()
    assert checkout.exists() and lock.path.exists()


@pytest.mark.parametrize("mode", ["success", "drift", "lost-lease", "teardown-failed"])
def test_actual_run_suite_lifetime_with_only_docker_transport_replaced(
    tmp_path, tiny_repo, monkeypatch, mode
):
    repo, _ = tiny_repo
    (repo / "scripts/ci").mkdir()
    source = Path(r.__file__).resolve().parents[2] / r.EVIDENCE_RELATIVE
    (repo / r.EVIDENCE_RELATIVE).write_bytes(source.read_bytes())
    for path in (
        "tests/integration/test_sp_op_lgpd_dsr_001.py",
        "tests/integration/test_sp_op_escalation_001.py",
        "tests/unit/test_unit_offline.py",
        "tests/integration/chaos/test_offline.py",
    ):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        body = (
            "import pytest\npytestmark = [pytest.mark.integration"
            + (", pytest.mark.chaos" if "chaos" in path else "")
            + "]\ndef test_offline():\n    assert 1 == 1\n"
        )
        if "chaos" in path and mode == "drift":
            body += (
                "    from pathlib import Path\n"
                "    Path('docker-compose.yml').write_text('services: {} # source drift\\n')\n"
            )
        target.write_text(body)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Offline",
            "-c",
            "user.email=offline@example.invalid",
            "commit",
            "-qm",
            "actual runner fixture",
        ],
        cwd=repo,
        check=True,
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    out = tmp_path / "run"
    monkeypatch.setattr(r, "LOCK_DIR", tmp_path / "run.lock")
    real_run = r._run
    effects = []

    def transport(command, **kwargs):
        if command[0] != "docker":
            return real_run(command, **kwargs)
        effects.append(command)
        if "context" in command:
            return subprocess.CompletedProcess(
                command, 0, json.dumps("unix://" + str(Path.home() / ".colima/offline.sock")), ""
            )
        if "down" in command and sum("down" in item for item in effects) == 2 and mode == "teardown-failed":
            return subprocess.CompletedProcess(command, 9, "synthetic Docker failure", "")
        if "pg_isready" in command and mode == "lost-lease":
            owner_path = r.LOCK_DIR / "owner.json"
            owner_path.write_text(
                json.dumps(dict(json.loads(owner_path.read_text()), token="foreign-fixture-owner"))
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(r, "_run", transport)
    args = r.build_parser().parse_args(
        [
            "run",
            "--checkout",
            str(repo),
            "--sha",
            sha,
            "--suite",
            "chaos",
            "--test-file",
            "tests/integration/chaos/test_offline.py",
            "--results-dir",
            str(out),
        ]
    )
    try:
        rc = r.run_suite(args)
        checkout = Path(json.loads((out / "execution-source.json").read_text())["execution_checkout"])
        gc.collect()
        state = json.loads((out / "run-state.json").read_text())
        observation = {
            "mode": mode,
            "rc": rc,
            "state": state["state"],
            "source_retained": checkout.exists(),
            "lock_retained": r.LOCK_DIR.exists(),
            "compose_dispatches": effects,
        }
        (out / "offline-observation.json").write_text(json.dumps(observation, indent=2))
        print(json.dumps(observation))
        if mode == "success":
            assert rc == 0 and state["state"] == "passed"
            assert not checkout.exists() and not r.LOCK_DIR.exists()
        else:
            assert rc != 0 and state["state"] != "passed"
            assert checkout.exists() and r.LOCK_DIR.exists()
            assert checkout in r._source_digests and checkout in r._source_leases
        if mode in {"drift", "lost-lease"}:
            assert sum("down" in item for item in effects) == 1
    finally:
        lock = r._process_owner
        if lock is not None:
            for checkout in list(r._source_leases):
                if r._source_leases[checkout] is lock:
                    r._source_leases.pop(checkout)
                    r._source_digests.pop(checkout, None)
                    r._source_cleanup_ready.discard(checkout)
                    shutil.rmtree(checkout.parent)
            lock.owner_path.write_text(json.dumps(lock.owner))
            lock.release()
        r._process_owner = None
