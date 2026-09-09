"""Offline D6 companion protocol tests; no historical import, PG, Docker or fake-engine integration."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "migration_adapter", ROOT / "scripts/dev/run_historical_migration_recipe.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
h, r = m.h, m.r


@pytest.fixture
def owned(tmp_path, monkeypatch):
    lock = r.EngineLock.create(
        tmp_path / "fixture.lock", checkout="offline-fixture", sha="fixture", suite="protocol-fixture"
    )
    assert lock.acquire(0)
    monkeypatch.setattr(r, "_process_owner", lock)
    monkeypatch.setattr(r, "_pending_groups", set())
    out = tmp_path / "proof"
    out.mkdir(mode=0o700)
    yield lock, m.Journal(out, lock), out
    if lock.acquired:
        # Only this test's private lock, restoring a deliberately corrupted fixture.
        lock.owner_path.write_bytes(h.encode(lock.owner))
        r._pending_groups.clear()
        lock.owner["subprocess_quiescent"] = True
        lock.update("fixture_cleanup", subprocess_quiescent=True)
        assert lock.release()


def test_fixed_recipe_has_full_selector_and_explicit_async_plugin():
    argv = m.recipe_argv(Path("/fixture/checkout"))
    assert argv == [
        "/fixture/checkout/.venv/bin/python",
        "-P",
        "-m",
        "pytest",
        m.TEST,
        "-v",
        "--tb=no",
        "-p",
        "no:cacheprovider",
        "-p",
        "ledger_source_guard",
        "-p",
        "pytest_asyncio.plugin",
    ]
    assert not {"-q", "-k", "-m integration", "--allow-live"} & set(argv)
    assert m.DEPENDENCIES["engine_required"] and m.DEPENDENCIES["kafka_required"]
    assert not m.DEPENDENCIES["hapi_required"]


@pytest.mark.parametrize(
    "extra",
    [
        ["OTHER"],
        [m.PROOF, "--sha", "f" * 40],
        [m.PROOF, "--test-file", "other.py"],
        [m.PROOF, "--allow-live"],
    ],
)
def test_cli_has_no_generic_live_override(extra):
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/dev/run_historical_migration_recipe.py"), *extra],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 2


@pytest.mark.parametrize("fault", ["lost", "pending", "unconfirmed"])
def test_lease_or_quiescence_loss_stops_before_effect(owned, monkeypatch, fault):
    lock, journal, out = owned
    if fault == "lost":
        disk = dict(lock.owner, token="fixture-other-owner")
        lock.owner_path.write_bytes(h.encode(disk))
    elif fault == "pending":
        r._pending_groups.add(123456789)
    else:
        lock.owner["subprocess_quiescent"] = False
    effects = []
    monkeypatch.setattr(r, "_compose", lambda *a, **kw: effects.append(a))
    with pytest.raises(h.CaptureRefusedError, match="ownership/quiescence"):
        m.finish_owned(lock, journal, None, None, False, None, source_safe=False)
    assert effects == [] and not (out / "receipt.json").exists()
    assert lock.acquired


@pytest.mark.parametrize("failure", [None, "down", "residual", "release", "source"])
def test_owned_cleanup_order_and_failure_retains_no_success(owned, tmp_path, monkeypatch, failure):
    lock, journal, out = owned
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []

    class Context:
        def __exit__(self, *args):
            assert lock.owns()
            calls.append("source_removed")
            checkout.rmdir()

    class ServiceFixture:
        env = {}
        ready = False
        container_labels = []

        def inventory(self, phase):
            assert lock.owns()
            calls.append(phase)
            return {
                "container": ["residual"] if failure == "residual" and phase == "after-teardown" else [],
                "volume": [],
                "network": [],
            }

        def containers(self, value, complete):
            assert lock.owns()
            calls.append("identity_checked")
            return []

    def down(*args, **kwargs):
        assert lock.owns()
        calls.append("down")
        assert args[1] == ["down", "-v", "--remove-orphans"]
        if failure == "down":
            raise r.RunnerError("offline injected failure")

    monkeypatch.setattr(r, "_compose", down)

    def verify_source(checkout):
        calls.append("source_verified")
        if failure == "source":
            raise r.RunnerError("offline source drift")

    monkeypatch.setattr(r, "_assert_execution_source", verify_source)
    if failure == "release":
        monkeypatch.setattr(lock, "release", lambda: False)
    if failure:
        with pytest.raises((h.CaptureRefusedError, r.RunnerError)):
            m.finish_owned(lock, journal, checkout, ServiceFixture(), True, Context(), source_safe=True)
        assert lock.acquired and not (out / "receipt.json").exists()
        if failure == "release":
            monkeypatch.undo()
    else:
        result = m.finish_owned(lock, journal, checkout, ServiceFixture(), True, Context(), source_safe=True)
        assert result == {
            "services_removed": True,
            "source_removed": True,
            "lease_released": True,
            "pending_pgids": [],
            "source_verified": True,
        }
        assert not lock.path.exists()
        assert calls == [
            "before-teardown",
            "identity_checked",
            "down",
            "after-teardown",
            "source_verified",
            "source_removed",
        ]
        assert [e["phase"] for e in journal.events] == [
            "teardown",
            "resources_removed",
            "source_removed",
            "released",
        ]


def test_busy_real_fixture_lock_prevents_preparation(tmp_path, monkeypatch):
    lock_path = tmp_path / "fixture.lock"
    blocker = r.EngineLock.create(lock_path, checkout="other-fixture", sha="other", suite="other")
    assert blocker.acquire(0)
    monkeypatch.setattr(r, "LOCK_DIR", lock_path)
    called = []
    monkeypatch.setattr(h, "execution_checkout", lambda *a: called.append(a))
    try:
        with pytest.raises(h.CaptureRefusedError, match="lease busy"):
            m.capture_migration(tmp_path, tmp_path / "out", {})
        assert called == [] and blocker.owns()
    finally:
        blocker.release()


def test_lock_precedes_preparation_and_failure_releases(tmp_path, monkeypatch):
    lock_path = tmp_path / "fixture.lock"
    monkeypatch.setattr(r, "LOCK_DIR", lock_path)

    @contextmanager
    def refuse(*args):
        assert r._process_owner.owns() and lock_path.exists()
        raise h.CaptureRefusedError("offline preparation refused")
        yield

    monkeypatch.setattr(h, "execution_checkout", refuse)
    with pytest.raises(h.CaptureRefusedError, match="preparation refused"):
        m.capture_migration(tmp_path, tmp_path / "out", {})
    assert not lock_path.exists()
    assert (tmp_path / "out/unresolved.json").exists()
    assert not (tmp_path / "out/receipt.json").exists()


def container_record(service, checkout, number):
    port, host_port = m.PORTS[service]
    return {
        "id": f"{number:064x}",
        "image_id": "sha256:" + "a" * 64,
        "image": m.IMAGES[service],
        "project": r.PROJECT,
        "service": service,
        "working_dir": str(checkout),
        "ports": {port: [{"HostIp": "127.0.0.1", "HostPort": host_port}]},
        "running": True,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("project", "other"),
        ("working_dir", "/borrowed"),
        ("image", "postgres:latest"),
        ("running", 1),
        ("service", "hapi-fhir"),
        ("ports", {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "15433"}]}),
    ],
)
def test_container_boundary_refuses_borrowing(field, value):
    checkout = Path("/fixture/source")
    data = container_record("postgres", checkout, 1)
    data[field] = value
    with pytest.raises(h.CaptureRefusedError):
        m.check_container(data, checkout, data["id"])


def test_owned_stopped_container_may_be_cleaned():
    checkout = Path("/fixture/source")
    data = container_record("postgres", checkout, 1)
    data["running"] = False
    m.check_container(data, checkout, data["id"], running_required=False)
    with pytest.raises(h.CaptureRefusedError):
        m.check_container(data, checkout, data["id"])


@pytest.fixture
def service_packet(tmp_path, monkeypatch):
    """Synthetic Docker/SQL TEXT fixtures test the parser; they prove no live service."""
    checkout = tmp_path / "synthetic-checkout"
    out = tmp_path / "synthetic-evidence"
    out.mkdir(mode=0o700)
    service = m.Services(checkout, out, None)
    records = [container_record(name, checkout, n + 1) for n, name in enumerate(m.SERVICES)]
    # Distinct 12-character prefixes like real Docker IDs.
    for n, record in enumerate(records):
        record["id"] = str(n + 1) * 64
    current = {"container": [], "volume": [], "network": []}
    commands = {}

    def transport(argv, prefix):
        label = f"{prefix}-{len(commands):03}"
        service.last_label = label
        if prefix == "resources":
            data = ("\n".join(current[argv[3]]) + ("\n" if current[argv[3]] else "")).encode()
        elif prefix == "container":
            data = h.encode(next(x for x in records if x["id"].startswith(argv[-1])))
        elif argv[-1] == m.PG_RUNTIME_QUERY:
            data = h.encode({"database": "maezo", "server_version_num": 160009, "server_port": 5432})
        else:
            data = h.encode({"schemas": [], "active_clients": 0})
        h.write(out / f"{label}.stdout", data)
        h.write(out / f"{label}.stderr", b"")
        c = {
            "argv": argv,
            "cwd": str(checkout),
            "started_at": "2026-09-09T00:00:00+00:00",
            "finished_at": "2026-09-09T00:00:01+00:00",
            "rc": 0,
            "status": "completed",
            "quiescent": True,
            "pgid": 123,
            "stdout": f"{label}.stdout",
            "stderr": f"{label}.stderr",
            "stdout_sha256": h.digest(data),
            "stderr_sha256": h.digest(b""),
            "combined_sha256": h.digest(data),
        }
        commands[label] = c
        h.write(out / f"{label}.command.json", h.encode(c))
        return data

    monkeypatch.setattr(service, "command", transport)
    service.inventory("before-start")
    current.update(
        container=sorted(x["id"] for x in records),
        volume=[r.PROJECT + "_pgdata"],
        network=[r.PROJECT + "_default"],
    )
    for phase in ("started", "before-teardown"):
        inv = service.inventory(phase)
        got = service.containers(inv, complete=True)
        h.write(out / f"containers-{phase}.binding.json", h.encode({"labels": service.container_labels}))
        if phase == "started":
            h.write(out / "containers-started.json", h.encode(got))
    service.pg_runtime()
    for phase in ("before", "after", "cleanup"):
        service.schemas(phase)
    current.update(container=[], volume=[], network=[])
    service.inventory("after-teardown")
    return out, checkout, commands


def test_service_raw_bindings_positive(service_packet):
    out, checkout, commands = service_packet
    for label in commands:
        assert m.command_record(out, label) == commands[label]
    m.validate_service_captures(out, checkout, commands)


@pytest.mark.parametrize(
    "case",
    [
        "resource-copy",
        "resource-raw",
        "schema-copy-bool",
        "schema-raw-bool",
        "command-project",
        "binding-reuse",
        "container-copy",
        "extra-command",
    ],
)
def test_service_raw_bindings_reject_resealed_mismatch(service_packet, case):
    out, checkout, commands = service_packet
    if case == "resource-copy":
        (out / "resources-before-start.json").write_bytes(
            h.encode({"container": ["unexpected"], "volume": [], "network": []})
        )
    elif case == "resource-raw":
        (out / "resources-000.stdout").write_bytes(b"unexpected\n")
    elif case == "schema-copy-bool":
        (out / "schemas-after.json").write_bytes(h.encode({"schemas": [], "active_clients": False}))
    elif case == "schema-raw-bool":
        binding = h.load(out / "schemas-after.binding.json")
        (out / (binding["label"] + ".stdout")).write_bytes(h.encode({"schemas": [], "active_clients": False}))
    elif case == "command-project":
        commands["resources-000"]["argv"][commands["resources-000"]["argv"].index("--filter") + 1] = (
            "label=com.docker.compose.project=other"
        )
    elif case == "binding-reuse":
        (out / "schemas-after.binding.json").write_bytes((out / "schemas-before.binding.json").read_bytes())
    elif case == "container-copy":
        data = h.load(out / "containers-started.json")
        data[0]["running"] = 1
        (out / "containers-started.json").write_bytes(h.encode(data))
    else:
        commands["resources-999"] = commands["resources-000"]
    # Re-sealed manifest: failure must come from semantics/raw binding, not old hashes.
    h.write(out / "manifest.json", h.encode(h.Packet(out).hashes()))
    with pytest.raises(h.CaptureRefusedError):
        m.validate_service_captures(out, checkout, commands)


def test_tiny_actual_async_guarded_recipe_and_fresh_lock(tmp_path, monkeypatch):
    """Real local Python/pytest async execution; deliberately no engine or PG fixture."""
    repo = tmp_path / "tiny-git"
    repo.mkdir()
    (repo / "src/maezo").mkdir(parents=True)
    (repo / "src/maezo/__init__.py").write_text("VALUE=7\n")
    test = repo / m.TEST
    test.parent.mkdir(parents=True)
    test.write_text(
        "import asyncio\nfrom maezo import VALUE\nasync def test_local_async():\n"
        "    await asyncio.sleep(0)\n    assert VALUE == 7\n"
    )
    (repo / "pyproject.toml").write_text(
        '[project]\nname="tiny-async"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n[project.optional-dependencies]\ndev=["pytest==9.1.1","pytest-asyncio==1.4.0"]\n[tool.uv]\npackage=false\n[tool.pytest.ini_options]\nasyncio_mode="auto"\n'
    )
    (repo / ".python-version").write_text("3.12\n")

    def command(*argv):
        return subprocess.run(argv, cwd=repo, env=h.environment(), check=True, capture_output=True).stdout

    command("uv", "lock", "--offline", "--no-config")
    command("git", "init", "-q")
    command("git", "add", ".")
    command(
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "tiny async fixture",
    )
    sha = command("git", "rev-parse", "HEAD").decode().strip()
    out = tmp_path / "async-proof"
    out.mkdir(mode=0o700)
    lock = r.EngineLock.create(
        tmp_path / "async.lock", checkout=str(repo), sha=sha, suite="tiny-async-fixture"
    )
    assert lock.acquire(0)
    monkeypatch.setattr(r, "_process_owner", lock)
    try:
        with h.execution_checkout(repo, sha, out, h.environment()) as (checkout, sources, _):
            assert lock.owns()
            (out / "guard").mkdir(mode=0o700)
            h.write(out / "guard/ledger_source_guard.py", h.GUARD.encode())
            env = dict(
                h.environment(),
                PYTHONPATH=os.pathsep.join([str(out / "guard"), str(checkout / "src"), str(checkout)]),
                LEDGER_ARCHIVE_ROOT=str(checkout),
                LEDGER_SOURCE_GUARD_ROOT=str(out / "guard"),
                LEDGER_SOURCE_GUARD_MARKER=str(out / "guard-base.json"),
                HISTORICAL_PHASE_RECEIPT=str(out / "phase.json"),
            )
            c = h.capture(m.recipe_argv(checkout), checkout, env, out, "pytest")
            result = h.validate_run(out, checkout, m.TEST, sources, c)
            assert result["nodes"] == [m.TEST + "::test_local_async"]
            assert len(h.load(out / "phase.json")["coverage"]["phases"]) == 3
        assert h.load(out / "checkout-cleanup.json")["removed"]
        assert not r._pending_groups
    finally:
        assert lock.release()


@pytest.mark.parametrize(
    "case",
    [
        "positive",
        "bool-pid",
        "integer-owned",
        "wrong-root",
        "token-change",
        "missing-release",
        "order",
        "bad-time",
        "outside-command",
    ],
)
def test_lifecycle_closed_types_order_and_interval(tmp_path, case):
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 9, 9, tzinfo=UTC)
    events = []
    for i, phase in enumerate(m.NORMAL_EVENTS):
        events.append(
            {
                "phase": phase,
                "at": (start + timedelta(seconds=i)).isoformat(),
                "pid": 1,
                "token_sha256": "a" * 64,
                "lock_path": str(r.LOCK_DIR),
                "owned": phase != "released",
                "pending_pgids": [],
            }
        )
    commands = {
        "fixture": {
            "started_at": (start + timedelta(seconds=1)).isoformat(),
            "finished_at": (start + timedelta(seconds=2)).isoformat(),
        }
    }
    if case == "bool-pid":
        events[0]["pid"] = True
    elif case == "integer-owned":
        events[0]["owned"] = 1
    elif case == "wrong-root":
        events[0]["lock_path"] = "/other/engine.lock"
    elif case == "token-change":
        events[3]["token_sha256"] = "b" * 64
    elif case == "missing-release":
        events.pop()
    elif case == "order":
        events[2], events[3] = events[3], events[2]
    elif case == "bad-time":
        events[-1]["at"] = "tomorrow"
    elif case == "outside-command":
        commands["fixture"]["started_at"] = "2026-09-08T00:00:00+00:00"
    for i, event in enumerate(events):
        h.write(tmp_path / f"lease-{i:02}.json", h.encode(event))
    if case == "positive":
        m.validate_lifecycle(tmp_path, events, commands)
    else:
        with pytest.raises(h.CaptureRefusedError):
            m.validate_lifecycle(tmp_path, events, commands)


@pytest.mark.parametrize("failure", ["timeout", "oversized"])
def test_real_owned_child_quiescence_before_release(owned, failure):
    lock, _, out = owned
    code = (
        "import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-c',"
        "'import time;time.sleep(10)']);time.sleep(10)"
        if failure == "timeout"
        else "import os;os.write(1,b'x'*4096)"
    )
    command = h.capture(
        [sys.executable, "-I", "-c", code], out, h.environment(), out, "owned-child", timeout=0.15, limit=128
    )
    assert command["status"] == failure and command["quiescent"] is True
    assert lock.owns() and lock.owner["subprocess_quiescent"] is True
    assert not r._pending_groups and not r._group_exists(command["pgid"])
    assert (out / "owned-child.stdout").stat().st_size <= 128
    assert lock.release()


@pytest.mark.parametrize(
    "field,value",
    [("server_port", True), ("server_port", 5433), ("server_version_num", 150012), ("database", "borrowed")],
)
def test_actual_pg_identity_requires_exact_runtime(field, value):
    data = {"database": "maezo", "server_version_num": 160009, "server_port": 5432}
    m.check_pg_runtime(data)
    data[field] = value
    with pytest.raises(h.CaptureRefusedError):
        m.check_pg_runtime(data)
