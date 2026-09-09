"""Offline D6 companion protocol tests; no historical import, PG, Docker or fake-engine integration."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
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
            "source_verified",
            "before-teardown",
            "identity_checked",
            "source_verified",
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
    monkeypatch.setattr(m.OwnedExecutionCheckout, "__enter__", lambda *a: called.append(a))
    try:
        with pytest.raises(h.CaptureRefusedError, match="lease busy"):
            m.capture_migration(tmp_path, tmp_path / "out", {})
        assert called == [] and blocker.owns()
    finally:
        blocker.release()


def test_lock_precedes_preparation_and_failure_releases(tmp_path, monkeypatch):
    lock_path = tmp_path / "fixture.lock"
    monkeypatch.setattr(r, "LOCK_DIR", lock_path)

    def refuse(*args):
        assert r._process_owner.owns() and lock_path.exists()
        raise h.CaptureRefusedError("offline preparation refused")

    monkeypatch.setattr(m.OwnedExecutionCheckout, "__enter__", refuse)
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
            guard_base = out / "guard-base.json"
            h.write(guard_base, b"")
            assert guard_base.stat().st_mode & 0o777 == 0o600
            env = dict(
                h.environment(),
                PYTHONPATH=os.pathsep.join([str(out / "guard"), str(checkout / "src"), str(checkout)]),
                LEDGER_ARCHIVE_ROOT=str(checkout),
                LEDGER_SOURCE_GUARD_ROOT=str(out / "guard"),
                LEDGER_SOURCE_GUARD_MARKER=str(out / "guard-base.json"),
                HISTORICAL_PHASE_RECEIPT=str(out / "phase.json"),
            )
            c = h.capture(m.recipe_argv(checkout), checkout, env, out, "pytest")
            assert guard_base.read_bytes()
            phase = h.load(out / "phase.json")
            assert h.load(guard_base) == {key: phase[key] for key in ("archived", "escaped")}
            result = h.validate_run(out, checkout, m.TEST, sources, c)
            assert result["nodes"] == [m.TEST + "::test_local_async"]
            assert len(phase["coverage"]["phases"]) == 3
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


@pytest.fixture
def lifetime_source(owned, tmp_path):
    """Actual tiny Git + locked offline uv; no historical source or service runs."""
    import gc
    import shutil

    lock, journal, out = owned
    repo = tmp_path / "lifetime-git"
    (repo / "scripts/dev").mkdir(parents=True)
    (repo / "src/maezo").mkdir(parents=True)
    (repo / "src/maezo/__init__.py").write_text("VALUE=7\n")
    (repo / "docker-compose.yml").write_text("services: {}\nvolumes:\n  pgdata: {}\n")
    (repo / "scripts/dev/docker-compose.engine-integration.yml").write_text("services: {}\n")
    (repo / ".python-version").write_text("3.12\n")
    (repo / ".gitignore").write_text("__pycache__/\n.venv/\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname="lifetime-fixture"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n'
        "[project.optional-dependencies]\ndev=[]\n[tool.uv]\npackage=false\n"
    )

    def command(*argv):
        return subprocess.check_output(argv, cwd=repo, env=h.environment()).decode().strip()

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
        "lifetime fixture",
    )
    sha = command("git", "rev-parse", "HEAD")
    context = m.OwnedExecutionCheckout(repo, sha, out, h.environment(), lock)
    checkout, sources, _ = context.__enter__()
    r._source_digests[checkout] = (sha, {p: v["sha256"] for p, v in sources.items()})
    contexts = [context]
    del context
    try:
        yield checkout, lock, journal, out, contexts, repo, sha
    finally:
        contexts.clear()
        gc.collect()
        r._source_digests.pop(checkout, None)
        shutil.rmtree(checkout.parent, ignore_errors=True)


class LifetimeServiceFixture:
    """Offline inventory only; real canonical Compose/source operations are exercised."""

    env = {}
    ready = False
    container_labels = []

    def inventory(self, phase):
        return {"container": [], "volume": [], "network": []}

    def containers(self, inventory, complete):
        return []


@pytest.mark.parametrize("mode", ["success", "drift", "lost-lease", "down-failure"])
def test_real_source_cleanup_survives_reference_release(lifetime_source, monkeypatch, mode):
    import gc
    import weakref

    checkout, lock, journal, out, contexts, _, _ = lifetime_source
    reference = weakref.ref(contexts[0])
    effects = []
    real_checked = r._checked

    def transport(command, **kwargs):
        if command[0] != "docker":
            return real_checked(command, **kwargs)
        effects.append(command)
        if mode == "down-failure":
            raise r.RunnerError("offline Docker down failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(r, "_checked", transport)
    if mode == "drift":
        (checkout / "docker-compose.yml").write_text(
            "services: {}\nvolumes:\n  pgdata:\n    name: foreign-fixture-volume\n"
        )
    if mode == "lost-lease":
        lock.owner_path.write_bytes(h.encode(dict(lock.owner, token="different-fixture-owner")))
    if mode == "success":
        original_release = lock.release

        def release():
            assert not checkout.exists()
            assert checkout not in r._source_digests
            return original_release()

        monkeypatch.setattr(lock, "release", release)
        result = m.finish_owned(
            lock, journal, checkout, LifetimeServiceFixture(), True, contexts[0], source_safe=True
        )
        assert result["source_removed"] and result["lease_released"]
        assert len(effects) == 1
    else:
        with pytest.raises((h.CaptureRefusedError, r.RunnerError)):
            m.finish_owned(
                lock, journal, checkout, LifetimeServiceFixture(), True, contexts[0], source_safe=True
            )
        contexts.clear()
        gc.collect()
        assert reference() is None
        assert checkout.exists() and lock.path.exists()
        assert checkout in r._source_digests
        assert not (out / "receipt.json").exists()
        assert len(effects) == (1 if mode == "down-failure" else 0)
    h.write(
        out / "lifetime-observation.json",
        h.encode(
            {
                "mode": mode,
                "source_retained": checkout.exists(),
                "lease_retained": lock.path.exists(),
                "compose_dispatch_count": len(effects),
            }
        ),
    )


def test_actual_drift_refuses_schema_query_before_transport(lifetime_source, monkeypatch):
    checkout, lock, _, out, _, _, _ = lifetime_source
    service = m.Services(checkout, out, lock)
    (checkout / "scripts/dev/docker-compose.engine-integration.yml").write_text("services: {} # drift\n")
    effects = []

    def forbidden(*args, **kwargs):
        effects.append(args)
        raise AssertionError("Docker transport must not be called")

    monkeypatch.setattr(h, "capture", forbidden)
    with pytest.raises(r.RunnerError):
        service.schemas("cleanup")
    assert not effects and checkout.exists() and lock.owns()


def test_adapter_unwind_without_cleanup_permission_keeps_source(lifetime_source):
    checkout, lock, _, out, contexts, _, _ = lifetime_source
    contexts[0].__exit__(RuntimeError, RuntimeError("unwind"), None)
    assert checkout.exists() and lock.owns()
    assert h.load(out / "checkout-cleanup.json")["removed"] is False


def test_adapter_preparation_ast_matches_original_pinned_helper():
    """Prove the extracted preparation statements retain the original helper semantics."""
    import ast
    import copy

    old = ast.parse((ROOT / "scripts/dev/run_historical_unit_recipe.py").read_text())
    old_function = next(
        n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == "execution_checkout"
    )
    old_body = next(n for n in old_function.body if isinstance(n, ast.Try)).body
    new = ast.parse((ROOT / "scripts/dev/run_historical_migration_recipe.py").read_text())
    new_function = next(
        n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == "_prepare_owned_checkout"
    )

    class Normalize(ast.NodeTransformer):
        def visit_Attribute(self, node):
            node = self.generic_visit(node)
            if isinstance(node.value, ast.Name) and node.value.id == "h":
                return ast.Name(id=node.attr, ctx=node.ctx)
            return node

        def visit_Name(self, node):
            if node.id == "r":
                node.id = "runner"
            return node

        def visit_Expr(self, node):
            if isinstance(node.value, ast.Yield):
                return ast.Return(value=node.value.value)
            return self.generic_visit(node)

    def canonical(body):
        return ast.dump(
            Normalize().visit(ast.Module(body=copy.deepcopy(body), type_ignores=[])), include_attributes=False
        )

    assert canonical(old_body) == canonical(new_function.body[1:])


@pytest.mark.parametrize("mode", ["lost-lease", "down-failure"])
def test_source_survives_actual_child_process_unwind(lifetime_source, mode):
    """The adapter object and interpreter both disappear; recovery source still exists."""
    import shutil

    _, _, _, out, _, repo, sha = lifetime_source
    child_out = out / ("child-" + mode)
    child_out.mkdir(mode=0o700)
    script = r"""
import gc, importlib.util, json, os, sys
from pathlib import Path
os.umask(0o077)
path,repo,sha,out,mode=sys.argv[1:]
spec=importlib.util.spec_from_file_location('child_lifetime',path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);h,r=m.h,m.r
out=Path(out);repo=Path(repo)
lock=r.EngineLock.create(out/'fixture.lock',checkout=str(repo),sha=sha,suite='offline-child')
assert lock.acquire(0);r._process_owner=lock
ctx=m.OwnedExecutionCheckout(repo,sha,out,h.environment(),lock)
checkout,sources,_=ctx.__enter__()
r._source_digests[checkout]=(sha,{p:v['sha256'] for p,v in sources.items()})
if mode=='lost-lease':
    lock.owner_path.write_bytes(h.encode(dict(lock.owner,token='other-child-fixture-owner')))
class Services:
    ready=False;env={};container_labels=[]
    def inventory(self,*a):return {'container':[],'volume':[],'network':[]}
    def containers(self,*a,**kw):return []
real=r._checked
calls=[]
def transport(command,**kwargs):
    if command[0]!='docker':return real(command,**kwargs)
    calls.append(command)
    raise r.RunnerError('offline down failure')
r._checked=transport
try:
    m.finish_owned(lock,m.Journal(out,lock),checkout,Services(),True,ctx,source_safe=True)
except (h.CaptureRefusedError,r.RunnerError):pass
else:raise AssertionError('cleanup should refuse')
del ctx;gc.collect()
h.write(out/'process-observation.json',h.encode({'source':str(checkout),'source_retained':checkout.exists(),'lock_retained':lock.path.exists(),'dispatches':len(calls)}))
raise SystemExit(17)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(ROOT / "scripts/dev/run_historical_migration_recipe.py"),
            str(repo),
            sha,
            str(child_out),
            mode,
        ],
        capture_output=True,
        timeout=30,
        env={k: v for k, v in h.environment().items() if not k.startswith(("PYTHON", "PYTEST_", "UV_"))},
    )
    h.write(child_out / "supervisor.stdout", result.stdout)
    h.write(child_out / "supervisor.stderr", result.stderr)
    h.write(child_out / "supervisor.json", h.encode({"returncode": result.returncode}))
    assert result.returncode == 17, result.stderr.decode()
    observation = h.load(child_out / "process-observation.json")
    source = Path(observation["source"])
    try:
        assert observation["source_retained"] and observation["lock_retained"]
        assert source.exists() and (child_out / "fixture.lock").exists()
        assert observation["dispatches"] == (1 if mode == "down-failure" else 0)
    finally:
        # Only the offline child's expressly-created fixture state is disposed here.
        shutil.rmtree(source.parent)
        shutil.rmtree(child_out / "fixture.lock")


def test_capture_uses_retaining_adapter_after_actual_preparation(lifetime_source, monkeypatch):
    """Actual capture entry refuses before any source claim or service execution is accepted."""
    import gc
    import shutil

    _, parent_lock, _, out, _, repo, sha = lifetime_source
    target = out / "capture-refusal"
    lock_path = out / "capture-fixture.lock"
    monkeypatch.setattr(m, "SOURCE_SHA", sha)
    monkeypatch.setattr(r, "LOCK_DIR", lock_path)
    observed = []

    def refusal(checkout, sources):
        observed.append(checkout)
        assert sources and r._process_owner.owns()
        lock_path.joinpath("owner.json").write_bytes(
            h.encode(dict(r._process_owner.owner, token="other-capture-fixture-owner"))
        )
        raise h.CaptureRefusedError("offline source review refusal")

    monkeypatch.setattr(m, "source_claim", refusal)
    try:
        with pytest.raises(h.CaptureRefusedError, match="offline source review refusal"):
            m.capture_migration(repo, target, {})
        gc.collect()
        assert len(observed) == 1 and observed[0].exists()
        assert lock_path.exists() and not (target / "receipt.json").exists()
        assert h.load(target / "cleanup-unresolved.json")["lease_owned"] is False
        h.write(
            target / "capture-lifetime-observation.json",
            h.encode({"source_retained": observed[0].exists(), "lease_retained": lock_path.exists()}),
        )
    finally:
        r._process_owner = parent_lock
        for checkout in observed:
            shutil.rmtree(checkout.parent, ignore_errors=True)
        if lock_path.exists():
            shutil.rmtree(lock_path)
