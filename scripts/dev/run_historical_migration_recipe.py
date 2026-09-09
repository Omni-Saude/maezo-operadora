#!/usr/bin/env python3
"""D6a92-only historical migration observation within the pinned runner lifecycle.

Companion orchestration, not run_suite flag mutation. It reuses the unchanged
canonical lease, runtime, context, service/deployment and process cleanup
primitives. Receipt consistency never grants source review or ledger acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import tempfile
import tomllib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HELPER_PIN = "25a412499cd64ae663de1358e488480cb6b6a5c0e772583ccc0bece45380877c"
SOURCE_SHA = "a92bf6f751d2fa81a2b2d8b555d120101ab829b2"
TEST = "tests/integration/gateway/test_human_outbox_migration_live_pg.py"
TASK = "PLAN-PORTAL-D6-MIGRATION-FIXTURE-REPAIR"
DATE = "2026-09-08"
PROOF = "D6migrationa92"
ORIGINAL_MANIFEST = "0fc37de9a53bc846cfe94a5a390c30f328c8e1926912373df63bf7a59cd5b9b6"
ORIGINAL_QUIET = "a169f8aacf5d636a86287e684812d1454901d2d078ddf1946b471de7171f2e8f"
# The companion's reviewed helper is immutable, including its original runner
# and checker. Its inert source capsule is NOT the owned execution checkout.
_tool_path = ROOT / "scripts/dev/historical_tool_sources.py"
_tool_fd = os.open(_tool_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
with os.fdopen(_tool_fd, "rb") as _tool_stream:
    _tool_info = os.fstat(_tool_stream.fileno())
    if not stat.S_ISREG(_tool_info.st_mode) or _tool_info.st_nlink != 1:
        raise ValueError("nonregular historical tool-source loader")
    _tool_data = _tool_stream.read(1024 * 1024 + 1)
    # Access time may change because of this read; content identity may not.
    if any(
        getattr(current, field) != getattr(_tool_info, field)
        for current in (os.fstat(_tool_stream.fileno()), os.stat(_tool_path, follow_symlinks=False))
        for field in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    ):
        raise ValueError("historical tool-source loader changed during read")
# New loader pin; the original helper/runner/checker pins remain untouched.
if _tool_path.is_symlink() or hashlib.sha256(_tool_data).hexdigest() != (
    "0c9f10f5b2a7a8e5a290ca43fb204bddfdc090462d3a02b318fe6e5297ab7c25"
):
    raise ValueError("historical tool-source loader pin mismatch")
_tool_spec = importlib.util.spec_from_file_location("historical_tool_sources", _tool_path)
_tool_sources = importlib.util.module_from_spec(_tool_spec)
exec(compile(_tool_data, str(_tool_path), "exec"), _tool_sources.__dict__)
_tool_capsule = _tool_sources.ToolSourceCapsule(ROOT)
h = _tool_capsule.load("scripts/dev/run_historical_unit_recipe.py", "migration_capture_helper")
if hashlib.sha256(Path(h.__file__).read_bytes()).hexdigest() != HELPER_PIN:
    raise ValueError("approved historical capture helper changed")
r = h.runner

# Exact canonical core policy: integration/conftest's autouse fixture requires
# real CIB; canonical core starts Kafka before CIB even for this PG-focused test.
DEPENDENCIES = {
    "postgres_required": True,
    "engine_required": True,
    "engine_evidence": ["tests/integration/conftest.py"],
    "kafka_required": True,
    "kafka_evidence": [],
    "hapi_required": False,
    "hapi_evidence": [],
}
SERVICES = ["postgres", "kafka", "cibseven"]
IMAGES = {
    "postgres": "postgres:16",
    "kafka": "confluentinc/cp-kafka:7.7.0",
    "cibseven": "cibseven/cibseven:2.1.0",
}
PORTS = {"postgres": ("5432/tcp", "15433"), "kafka": ("9092/tcp", "19092"), "cibseven": ("8080/tcp", "18080")}
DSN_IDENTITY = {
    "category": "canonical-owned-loopback",
    "host": "127.0.0.1",
    "port": 15433,
    "database": "maezo",
}
MANDATORY = (
    TEST,
    "tests/conftest.py",
    "tests/integration/conftest.py",
    "tests/unit/a2a/test_outbox_live_pg.py",
    "tests/unit/portal/test_foundation_migrations_live_pg.py",
    "tests/unit/gateway/human/test_durable_projection.py",
    "src/maezo/gateway/human/projection.py",
    "src/maezo/gateway/human/outbox.py",
    "src/maezo/platform/migrations/env.py",
    "alembic.ini",
    "docker-compose.yml",
    "scripts/dev/docker-compose.engine-integration.yml",
    "uv.lock",
    "pyproject.toml",
    ".python-version",
)
NORMAL_EVENTS = [
    "acquired",
    "source_prepared",
    "context_validated",
    "empty_project",
    "services_starting",
    "services_ready",
    "deployment_verified",
    "schema_before",
    "pytest_running",
    "pytest_verified",
    "schema_after",
    "teardown",
    "resources_removed",
    "source_removed",
    "released",
]
SCHEMA_QUERY = (
    "SELECT json_build_object('schemas', COALESCE((SELECT json_agg(nspname ORDER BY nspname) "
    "FROM pg_namespace WHERE nspname LIKE 'human\\_mig\\_%' ESCAPE '\\'), '[]'::json), "
    "'active_clients', (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
    "AND pid<>pg_backend_pid() AND backend_type='client backend' AND application_name "
    "NOT IN ('psql','PostgreSQL JDBC Driver')))::text"
)
PG_RUNTIME_QUERY = (
    "SELECT json_build_object('database',current_database(),'server_version_num',"
    "current_setting('server_version_num')::int,'server_port',current_setting('port')::int)::text"
)
INSPECT = (
    '{"id":{{json .Id}},"image_id":{{json .Image}},"image":{{json .Config.Image}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"working_dir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},'
    '"ports":{{json .HostConfig.PortBindings}},"running":{{json .State.Running}}}'
)


def original_observations(path: Path) -> dict:
    packet = h.Packet(path)
    if h.digest(packet.read("MANIFEST.json")) != ORIGINAL_MANIFEST:
        raise h.CaptureRefusedError("wrong original review packet")
    manifest = h.load(path / "MANIFEST.json")
    result = {"MANIFEST.json": ORIGINAL_MANIFEST}
    for entry in manifest["entries"]:
        h.keys(entry, {"path": str, "bytes": int, "sha256": str})
        data = packet.read(entry["path"])
        if len(data) != entry["bytes"] or h.digest(data) != entry["sha256"]:
            raise h.CaptureRefusedError("original observation changed")
        result[entry["path"]] = entry["sha256"]
    if result.get("d6migration-original-quiet.log") != ORIGINAL_QUIET:
        raise h.CaptureRefusedError("missing original quiet observation")
    return result


def recipe_argv(checkout: Path) -> list[str]:
    return [
        str(checkout / ".venv/bin/python"),
        "-P",
        "-m",
        "pytest",
        TEST,
        "-v",
        "--tb=no",
        "-p",
        "no:cacheprovider",
        "-p",
        "ledger_source_guard",
        "-p",
        "pytest_asyncio.plugin",
    ]


def source_claim(repo: Path, sources: dict) -> dict:
    for path in MANDATORY:
        if path not in sources:
            raise h.CaptureRefusedError("incomplete migration source closure")
    migrations = sorted(
        p for p in sources if re.fullmatch(r"src/maezo/platform/migrations/versions/\d{4}_[^/]+\.py", p)
    )
    if len(migrations) != 13 or [Path(p).name[:4] for p in migrations] != [f"{n:04}" for n in range(1, 14)]:
        raise h.CaptureRefusedError("wrong historical migration chain")
    ledger = h.git(repo, "show", f"{SOURCE_SHA}:docs/evidence-ledger.md")
    rows = [line for line in ledger.decode().splitlines() if line.startswith(f"| {TASK} | {DATE} |")]
    if len(rows) != 1:
        raise h.CaptureRefusedError("migration row identity is ambiguous")
    declarations = re.findall(r"sha256:([0-9a-f]{64}) \(" + re.escape(TEST) + r"\)", rows[0])
    if len(declarations) != 1:
        raise h.CaptureRefusedError("migration declaration not exact")
    return {
        "commit": SOURCE_SHA,
        "tree": h.git(repo, "rev-parse", f"{SOURCE_SHA}^{{tree}}").decode().strip(),
        "task": TASK,
        "date": DATE,
        "test": TEST,
        "test_sha256": sources[TEST]["sha256"],
        "ledger_sha256": h.digest(ledger),
        "row_sha256": h.digest(rows[0].encode()),
        "declaration": declarations[0],
        "physical_occurrences": 1,
        "full_map_sha256": h.digest(h.encode(sources)),
        "required_closure": {p: sources[p] for p in sorted({*MANDATORY, *migrations})},
        "tooling": {
            **h.PINS,
            "capture_helper": HELPER_PIN,
            "companion": h.digest(Path(__file__).read_bytes()),
            "guard": h.digest(h.GUARD.encode()),
        },
    }


def require_owner(lock) -> None:
    if not lock.owns() or r._pending_groups or lock.owner.get("subprocess_quiescent") is False:
        raise h.CaptureRefusedError("lease ownership/quiescence not proven")


def _prepare_owned_checkout(
    repo: Path, sha: str, out: Path, env: dict[str, str], scratch: Path, checkout: Path
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Exact pinned helper preparation operations, without its unconditional generator finalizer."""
    h.successful(
        h.capture(
            [
                "git",
                "--no-replace-objects",
                "clone",
                "--shared",
                "--no-checkout",
                "--quiet",
                "--",
                str(repo),
                str(checkout),
            ],
            scratch,
            env,
            out,
            "clone",
            120,
        )
    )
    h.successful(
        h.capture(
            [
                "git",
                "--no-replace-objects",
                "-c",
                "core.hooksPath=/dev/null",
                "checkout",
                "--quiet",
                "--detach",
                sha,
            ],
            checkout,
            env,
            out,
            "checkout",
            120,
        )
    )
    if h.git(checkout, "rev-parse", "HEAD").strip().decode() != sha:
        raise h.CaptureRefusedError("checkout SHA mismatch")
    before = h.tracked(checkout, sha)
    for required in ("uv.lock", "pyproject.toml", ".python-version"):
        if required not in before:
            raise h.CaptureRefusedError("missing locked configuration")
    config = tomllib.loads((checkout / "pyproject.toml").read_text())
    pytest_config = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    if pytest_config.get("addopts") or pytest_config.get("pythonpath", []) not in ([], ["."]):
        raise h.CaptureRefusedError("source pytest selectors/path injection refused")
    if any((checkout / name).exists() for name in ("pytest.ini", "setup.cfg", "tox.ini", "uv.toml")):
        raise h.CaptureRefusedError("alternative tool configuration refused")
    h.write(out / "source-before.json", h.encode(before))
    uv = Path(shutil.which("uv", path=env["PATH"])).resolve()  # type: ignore[arg-type]  # pinned preparation parity
    uv_version = h.capture([str(uv), "--version"], checkout, env, out, "uv-version", 20)
    h.successful(uv_version)
    command = r._uv_python(checkout, "-c", "import sys; print(sys.prefix)")
    command[0] = str(uv)
    command.insert(2, "--offline")
    prep = h.capture(command, checkout, env, out, "prepare", 180)
    h.successful(prep)
    if h.tracked(checkout, sha) != before:
        raise h.CaptureRefusedError("source changed during preparation")
    return (
        checkout,
        before,
        {
            "uv_path": str(uv),
            "uv_sha256": h.runtime_digest("uv", str(uv)),
            "uv_version": h.read(out / uv_version["stdout"], root=out).decode().strip(),
            "prepare": prep,
        },
    )


class OwnedExecutionCheckout:
    """A lease-bound source whose deletion requires explicit successful cleanup.

    There is deliberately no generator/destructor cleanup: stack refusal, GC and
    process unwind retain the actual source on disk. Preparation uses the same
    pinned helper primitives and argv; the standalone helper remains unchanged.
    """

    def __init__(self, repo: Path, sha: str, out: Path, env: dict[str, str], lock: Any) -> None:
        self.repo, self.sha, self.out, self.env, self.lock = repo, sha, out, env, lock
        self.scratch: Path | None = None
        self.checkout: Path | None = None
        self.cleanup_authorized = False

    def __enter__(self) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        if self.scratch is not None:
            raise h.CaptureRefusedError("owned source already prepared")
        require_owner(self.lock)
        h.ancestry(self.repo, self.sha)
        self.scratch = Path(tempfile.mkdtemp(prefix="maezo-historical-proof-")).resolve()
        self.checkout = self.scratch / "checkout"
        h.write(
            self.out / "owned-source-lifetime.json",
            h.encode(
                {
                    "scratch": str(self.scratch),
                    "checkout": str(self.checkout),
                    "lock": str(self.lock.path),
                    "token_sha256": h.digest(self.lock.token.encode()),
                    "cleanup_authorized_at_preparation": False,
                }
            ),
        )
        return _prepare_owned_checkout(self.repo, self.sha, self.out, self.env, self.scratch, self.checkout)

    def authorize_cleanup(self, lock: Any) -> None:
        if lock is not self.lock:
            raise h.CaptureRefusedError("different source lease")
        require_owner(self.lock)
        self.cleanup_authorized = True

    def __exit__(self, *args: object) -> None:
        if self.scratch is None:
            return
        removed = False
        try:
            if self.cleanup_authorized:
                require_owner(self.lock)
                if self.checkout in r._source_digests:
                    r._assert_execution_source(self.checkout)
                require_owner(self.lock)
                shutil.rmtree(self.scratch)
                removed = True
        finally:
            h.write(
                self.out / "checkout-cleanup.json",
                h.encode(
                    {
                        "path": str(self.scratch),
                        "removed": removed,
                        "pending_pgids": sorted(r._pending_groups),
                    }
                ),
            )


class Journal:
    def __init__(self, out: Path, lock):
        self.out, self.lock = out, lock
        self.events: list[dict] = []

    def event(self, phase: str, *, released: bool = False) -> None:
        if released:
            if self.lock.acquired or self.lock.path.exists() or r._pending_groups:
                raise h.CaptureRefusedError("lease release not proven")
        else:
            require_owner(self.lock)
            if not self.lock.update(phase):
                raise h.CaptureRefusedError("lease update refused")
        event = {
            "phase": phase,
            "at": r._now(),
            "pid": os.getpid(),
            "token_sha256": h.digest(self.lock.token.encode()),
            "lock_path": str(self.lock.path),
            "owned": not released,
            "pending_pgids": sorted(r._pending_groups),
        }
        h.write(self.out / f"lease-{len(self.events):02}.json", h.encode(event))
        self.events.append(event)


class Services:
    """Closed service calls; no arbitrary project, coordinates, selector or image API."""

    def __init__(self, checkout: Path, out: Path, lock):
        self.checkout, self.out, self.lock = checkout, out, lock
        self.env = {**h.environment(), **r._runtime_env()}
        self.counter = 0
        self.ready = False
        self.last_label = ""
        self.container_labels: list[str] = []

    def command(self, argv: list[str], prefix: str) -> bytes:
        require_owner(self.lock)
        if argv[:4] == ["docker", "--context", r.DOCKER_CONTEXT, "compose"]:
            r._assert_execution_source(self.checkout)
            require_owner(self.lock)
        label = f"{prefix}-{self.counter:03}"
        self.counter += 1
        self.last_label = label
        result = h.capture(argv, self.checkout, self.env, self.out, label, timeout=30)
        h.successful(result)
        require_owner(self.lock)
        return h.read(self.out / result["stdout"], root=self.out)

    def inventory(self, phase: str) -> dict:
        result = {}
        labels = []
        for kind in ("container", "volume", "network"):
            argv = ["docker", "--context", r.DOCKER_CONTEXT, kind, "ls"]
            if kind == "container":
                argv.extend(["--all", "--no-trunc"])
            argv += [
                "--filter",
                f"label=com.docker.compose.project={r.PROJECT}",
                "--format",
                "{{.ID}}" if kind == "container" else "{{.Name}}",
            ]
            rows = self.command(argv, "resources").decode().splitlines()
            labels.append(self.last_label)
            if (
                len(rows) != len(set(rows))
                or len(rows) > 16
                or any(not re.fullmatch(r"[A-Za-z0-9_-]+", v) for v in rows)
            ):
                raise h.CaptureRefusedError("malformed owned resource inventory")
            result[kind] = sorted(rows)
        if not set(result["volume"]) <= {r.PROJECT + "_pgdata"} or not set(result["network"]) <= {
            r.PROJECT + "_default"
        }:
            raise h.CaptureRefusedError("unexpected project volume/network")
        h.write(self.out / f"resources-{phase}.binding.json", h.encode({"labels": labels}))
        h.write(self.out / f"resources-{phase}.json", h.encode(result))
        return result

    def containers(self, inventory: dict, *, complete: bool) -> list[dict]:
        records = []
        self.container_labels = []
        for identity in inventory["container"]:
            data = json.loads(
                self.command(
                    ["docker", "--context", r.DOCKER_CONTEXT, "inspect", "--format", INSPECT, identity],
                    "container",
                )
            )
            self.container_labels.append(self.last_label)
            check_container(data, self.checkout, identity, running_required=complete)
            records.append(data)
        services = [item["service"] for item in records]
        if len(services) != len(set(services)) or complete and set(services) != set(SERVICES):
            raise h.CaptureRefusedError("owned service set mismatch")
        if complete and (
            inventory["volume"] != [r.PROJECT + "_pgdata"] or inventory["network"] != [r.PROJECT + "_default"]
        ):
            raise h.CaptureRefusedError("missing owned volume/network")
        return records

    def schemas(self, phase: str) -> dict:
        data = self.command(
            r._compose_base(self.checkout)
            + [
                "exec",
                "-T",
                "postgres",
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "maezo",
                "-d",
                "maezo",
                "-c",
                SCHEMA_QUERY,
            ],
            "schema",
        )
        value = json.loads(data)
        h.keys(value, {"schemas": list, "active_clients": int})
        if any(
            type(v) is not str or not re.fullmatch(r"human_mig_[0-9a-f]{32}", v) for v in value["schemas"]
        ):
            raise h.CaptureRefusedError("unexpected migration schema identity")
        h.write(self.out / f"schemas-{phase}.binding.json", h.encode({"label": self.last_label}))
        h.write(self.out / f"schemas-{phase}.json", h.encode(value))
        return value

    def pg_runtime(self) -> dict:
        data = self.command(
            r._compose_base(self.checkout)
            + [
                "exec",
                "-T",
                "postgres",
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "maezo",
                "-d",
                "maezo",
                "-c",
                PG_RUNTIME_QUERY,
            ],
            "schema",
        )
        value = json.loads(data)
        check_pg_runtime(value)
        h.write(self.out / "pg-runtime.binding.json", h.encode({"label": self.last_label}))
        h.write(self.out / "pg-runtime.json", h.encode(value))
        return value


def check_pg_runtime(value: dict) -> None:
    h.keys(value, {"database": str, "server_version_num": int, "server_port": int})
    if (
        value["database"] != "maezo"
        or value["server_port"] != 5432
        or not 160000 <= value["server_version_num"] < 170000
    ):
        raise h.CaptureRefusedError("actual PostgreSQL runtime differs from declared service")


def check_container(data: dict, checkout: Path, identity: str, *, running_required: bool = True) -> None:
    h.keys(
        data,
        {
            "id": str,
            "image_id": str,
            "image": str,
            "project": str,
            "service": str,
            "working_dir": str,
            "ports": dict,
            "running": bool,
        },
    )
    service = data["service"]
    if (
        not data["id"].startswith(identity)
        or not re.fullmatch(r"[0-9a-f]{64}", data["id"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", data["image_id"])
        or service not in SERVICES
        or data["project"] != r.PROJECT
        or data["working_dir"] != str(checkout)
        or data["image"] != IMAGES[service]
        or running_required
        and not data["running"]
    ):
        raise h.CaptureRefusedError("container identity/ownership differs")
    port, host_port = PORTS[service]
    if not h.same(data["ports"].get(port), [{"HostIp": "127.0.0.1", "HostPort": host_port}]):
        raise h.CaptureRefusedError("service is not bound to intended loopback port")
    if any(value for name, value in data["ports"].items() if name != port):
        raise h.CaptureRefusedError("unexpected published service port")


@contextmanager
def lease(out: Path, repo: Path):
    """Canonical mutex; no configurable lock path or stale-owner takeover in the CLI."""
    lock = r.EngineLock.create(r.LOCK_DIR, checkout=str(repo), sha=SOURCE_SHA, suite=PROOF)
    if not lock.acquire(0):
        raise h.CaptureRefusedError("canonical engine lease busy")
    r._process_owner = lock
    journal = Journal(out, lock)
    journal.event("acquired")
    try:
        yield lock, journal
    finally:
        # Cleanup is orchestrated inside this lease. Never release an uncertain
        # stack or steal a lost lease; the owner remains visible for ROOT recovery.
        if lock.acquired and lock.owns():
            lock.update("unresolved_retained", pending_pgids=sorted(r._pending_groups))


def finish_owned(
    lock,
    journal: Journal,
    checkout: Path | None,
    services: Services | None,
    touched: bool,
    source_context,
    *,
    source_safe: bool,
) -> dict:
    """Called on all outcomes; destructive steps require the same lease and quiescence."""
    cleanup = {
        "services_removed": not touched,
        "source_removed": False,
        "lease_released": False,
        "pending_pgids": sorted(r._pending_groups),
    }
    require_owner(lock)
    if touched:
        if not source_safe or checkout is None:
            raise h.CaptureRefusedError("cleanup Compose source was never authenticated")
        r._assert_execution_source(checkout)
        require_owner(lock)
        journal.event("teardown")
        current = services.inventory("before-teardown")
        services.containers(current, complete=False)
        h.write(
            journal.out / "containers-before-teardown.binding.json",
            h.encode({"labels": services.container_labels}),
        )
        if services.ready:
            try:
                services.schemas("cleanup")
            except Exception as exc:
                h.write(
                    journal.out / "schema-cleanup-query-unresolved.json",
                    h.encode({"error_type": type(exc).__name__}),
                )
        require_owner(lock)
        r._assert_execution_source(checkout)
        require_owner(lock)
        r._compose(
            checkout,
            ["down", "-v", "--remove-orphans"],
            env=services.env,
            timeout=300,
            log_path=journal.out / "teardown.log",
        )
        remaining = services.inventory("after-teardown")
        if any(remaining.values()):
            raise h.CaptureRefusedError("owned resources remain after teardown")
        cleanup["services_removed"] = True
        if source_safe:
            r._assert_execution_source(checkout)
        journal.event("resources_removed")
    require_owner(lock)
    if source_context is not None:
        if isinstance(source_context, OwnedExecutionCheckout):
            source_context.authorize_cleanup(lock)
        source_context.__exit__(None, None, None)
        if checkout is not None and checkout.exists():
            raise h.CaptureRefusedError("owned source cleanup not proven")
    cleanup["source_removed"] = True
    if checkout is not None:
        r._source_digests.pop(checkout, None)
    journal.event("source_removed")
    # source_safe affects proof eligibility, never permits destroying foreign state.
    cleanup["source_verified"] = source_safe
    require_owner(lock)
    if not lock.release():
        raise h.CaptureRefusedError("canonical lease release failed")
    journal.event("released", released=True)
    cleanup["lease_released"] = True
    cleanup["pending_pgids"] = sorted(r._pending_groups)
    return cleanup


def _recipe(checkout: Path, out: Path, sources: dict, services: Services) -> tuple[dict, dict, dict]:
    python = str(checkout / ".venv/bin/python")
    before = h.inventory(
        h.capture([python, "-I", "-c", h.INVENTORY], checkout, h.environment(), out, "inventory-before"),
        out,
        checkout,
    )
    guard = out / "guard"
    guard.mkdir(mode=0o700)
    h.write(guard / "ledger_source_guard.py", h.GUARD.encode())
    env = dict(
        services.env,
        PYTHONPATH=os.pathsep.join((str(guard), str(checkout / "src"), str(checkout))),
        LEDGER_ARCHIVE_ROOT=str(checkout),
        LEDGER_SOURCE_GUARD_ROOT=str(guard),
        LEDGER_SOURCE_GUARD_MARKER=str(out / "guard-base.json"),
        HISTORICAL_PHASE_RECEIPT=str(out / "phase.json"),
    )
    require_owner(services.lock)
    command = h.capture(recipe_argv(checkout), checkout, env, out, "pytest", timeout=300)
    require_owner(services.lock)
    h.verify_guard(out)
    after = h.inventory(
        h.capture([python, "-I", "-c", h.INVENTORY], checkout, h.environment(), out, "inventory-after"),
        out,
        checkout,
    )
    post_sources = h.tracked(checkout, SOURCE_SHA)
    h.write(out / "source-after.json", h.encode(post_sources))
    if not h.same(before, after) or not h.same(sources, post_sources):
        raise h.CaptureRefusedError("inventory/source drift across migration")
    coverage = h.validate_run(out, checkout, TEST, sources, command)
    return command, coverage, before


def capture_migration(repo: Path, out: Path, observations: dict) -> dict:
    h.environment()  # Ambient selectors/configuration refuse before lock or service effects.
    with h._directory(out.parent) as parent:
        os.mkdir(out.name, mode=0o700, dir_fd=parent)
    old_mask = os.umask(0o077)
    source_context = None
    checkout = None
    services = None
    touched = False
    source_safe = False
    result = None
    cleanup = None
    failure = None
    try:
        with lease(out, repo) as (lock, journal):
            try:
                source_context = OwnedExecutionCheckout(repo, SOURCE_SHA, out, h.environment(), lock)
                checkout, sources, preparation = source_context.__enter__()
                claim = source_claim(checkout, sources)
                # Same canonical source registration, after the reviewed helper has
                # verified every Git blob and fresh locked preparation.
                r._source_digests[checkout] = (SOURCE_SHA, {p: v["sha256"] for p, v in sources.items()})
                source_safe = True
                journal.event("source_prepared")
                services = Services(checkout, out, lock)
                r._validate_docker_context(checkout, services.env)
                journal.event("context_validated")
                initial = services.inventory("before-start")
                if any(initial.values()):
                    raise h.CaptureRefusedError(
                        "existing canonical project refused; no borrowed service or takeover"
                    )
                journal.event("empty_project")
                touched = True
                journal.event("services_starting")
                started = r._start_stack(checkout, DEPENDENCIES, services.env, out, lock)
                if started != SERVICES:
                    raise h.CaptureRefusedError("canonical dependency policy differs")
                active = services.inventory("started")
                containers = services.containers(active, complete=True)
                h.write(
                    out / "containers-started.binding.json", h.encode({"labels": services.container_labels})
                )
                h.write(out / "containers-started.json", h.encode(containers))
                services.pg_runtime()
                services.ready = True
                journal.event("services_ready")
                r._deploy_and_verify(checkout, services.env, out)
                journal.event("deployment_verified")
                if not h.same(services.schemas("before"), {"schemas": [], "active_clients": 0}):
                    raise h.CaptureRefusedError("pre-existing migration schemas/clients")
                journal.event("schema_before")
                journal.event("pytest_running")
                command, coverage, inv = _recipe(checkout, out, sources, services)
                journal.event("pytest_verified")
                if not h.same(services.schemas("after"), {"schemas": [], "active_clients": 0}):
                    raise h.CaptureRefusedError("migration schema/client cleanup failed")
                journal.event("schema_after")
                r._assert_execution_source(checkout)
                result = {
                    "schema": "maezo-historical-migration-proof/v1",
                    "proof_id": PROOF,
                    "source": claim,
                    "environment": {
                        "checkout": str(checkout),
                        "venv": str(checkout / ".venv"),
                        "inventory": inv,
                        "preparation": preparation,
                        "dsn_identity": DSN_IDENTITY,
                    },
                    "execution": command,
                    "coverage": coverage,
                    "scope": {
                        "original_observations": observations,
                        "original_quiet_log": ORIGINAL_QUIET,
                        "operational_validation": "not-performed",
                        "current_proof_disposition": "unresolved",
                        "historical_claim_truth": "equal"
                        if coverage["recipe_sha256"] == "sha256:" + claim["declaration"]
                        else "mismatch",
                        "status": "observation-only",
                    },
                }
            except BaseException as exc:
                failure = exc
            finally:
                try:
                    cleanup = finish_owned(
                        lock, journal, checkout, services, touched, source_context, source_safe=source_safe
                    )
                except BaseException as exc:
                    failure = failure or exc
                    h.write(
                        out / "cleanup-unresolved.json",
                        h.encode(
                            {
                                "error_type": type(exc).__name__,
                                "lease_owned": lock.owns(),
                                "pending_pgids": sorted(r._pending_groups),
                            }
                        ),
                    )
            if failure is not None:
                raise failure
            if result is None or cleanup is None:
                raise h.CaptureRefusedError("migration lifecycle incomplete")
            result["cleanup"] = cleanup
            result["lifecycle"] = journal.events
        if (out / "schema-cleanup-query-unresolved.json").exists():
            raise h.CaptureRefusedError("schema cleanup query unresolved")
        h.verify_guard(out)
        h.write(out / "receipt.json", h.encode(result))
        h.write(out / "manifest.json", h.encode(h.Packet(out).hashes()))
        return result
    except BaseException as exc:
        h.write(out / "unresolved.json", h.encode({"status": "unresolved", "error_type": type(exc).__name__}))
        raise
    finally:
        os.umask(old_mask)


def command_record(out: Path, label: str) -> dict:
    command = h.load(out / f"{label}.command.json")
    h.keys(
        command,
        {
            "argv": list,
            "cwd": str,
            "started_at": str,
            "finished_at": str,
            "rc": int,
            "status": str,
            "quiescent": bool,
            "pgid": int,
            "stdout": str,
            "stderr": str,
            "stdout_sha256": str,
            "stderr_sha256": str,
            "combined_sha256": str,
        },
    )
    if any(type(arg) is not str for arg in command["argv"]) or command["pgid"] <= 0:
        raise h.CaptureRefusedError("invalid command identity")
    h.successful(command)
    raw = []
    for stream in ("stdout", "stderr"):
        if command[stream] != f"{label}.{stream}":
            raise h.CaptureRefusedError("command stream path differs")
        data = h.read(out / command[stream], root=out)
        if h.digest(data) != command[stream + "_sha256"]:
            raise h.CaptureRefusedError("command stream changed")
        raw.append(data)
    if h.digest(b"".join(raw)) != command["combined_sha256"]:
        raise h.CaptureRefusedError("combined stream changed")
    return command


def validate_service_captures(out: Path, checkout: Path, commands: dict) -> None:
    used = []

    def output(label: str, expected: list[str]) -> bytes:
        if type(label) is not str or label in used or label not in commands:
            raise h.CaptureRefusedError("missing/duplicate service command binding")
        command = commands[label]
        if command["argv"] != expected or command["cwd"] != str(checkout):
            raise h.CaptureRefusedError("service command differs from canonical ownership policy")
        used.append(label)
        return h.read(out / command["stdout"], root=out)

    inventory = {}
    for phase in ("before-start", "started", "before-teardown", "after-teardown"):
        binding = h.load(out / f"resources-{phase}.binding.json")
        h.keys(binding, {"labels": list})
        if len(binding["labels"]) != 3:
            raise h.CaptureRefusedError("resource command binding count differs")
        data = {}
        for kind, label in zip(("container", "volume", "network"), binding["labels"], strict=True):
            argv = ["docker", "--context", r.DOCKER_CONTEXT, kind, "ls"]
            if kind == "container":
                argv.extend(["--all", "--no-trunc"])
            argv += [
                "--filter",
                f"label=com.docker.compose.project={r.PROJECT}",
                "--format",
                "{{.ID}}" if kind == "container" else "{{.Name}}",
            ]
            rows = output(label, argv).decode().splitlines()
            if len(rows) != len(set(rows)) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", row) for row in rows):
                raise h.CaptureRefusedError("invalid resource identity")
            data[kind] = sorted(rows)
        if not h.same(h.load(out / f"resources-{phase}.json"), data):
            raise h.CaptureRefusedError("resource snapshot differs from raw capture")
        inventory[phase] = data
    if inventory["started"] != inventory["before-teardown"]:
        raise h.CaptureRefusedError("owned resource identities changed during test")
    empty = {"container": [], "volume": [], "network": []}
    if inventory["before-start"] != empty or inventory["after-teardown"] != empty:
        raise h.CaptureRefusedError("project freshness or teardown not proven")
    for phase in ("started", "before-teardown"):
        binding = h.load(out / f"containers-{phase}.binding.json")
        h.keys(binding, {"labels": list})
        ids = inventory[phase]["container"]
        if len(binding["labels"]) != len(ids) or len(ids) != 3:
            raise h.CaptureRefusedError("container binding count differs")
        actual = []
        for identity, label in zip(ids, binding["labels"], strict=True):
            data = json.loads(
                output(
                    label, ["docker", "--context", r.DOCKER_CONTEXT, "inspect", "--format", INSPECT, identity]
                )
            )
            check_container(data, checkout, identity)
            actual.append(data)
        if phase == "started" and not h.same(h.load(out / "containers-started.json"), actual):
            raise h.CaptureRefusedError("container snapshot differs from raw capture")
    for phase in ("before", "after", "cleanup"):
        binding = h.load(out / f"schemas-{phase}.binding.json")
        h.keys(binding, {"label": str})
        data = json.loads(
            output(
                binding["label"],
                r._compose_base(checkout)
                + [
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-X",
                    "-A",
                    "-t",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "maezo",
                    "-d",
                    "maezo",
                    "-c",
                    SCHEMA_QUERY,
                ],
            )
        )
        if not h.same(data, {"schemas": [], "active_clients": 0}) or not h.same(
            h.load(out / f"schemas-{phase}.json"), data
        ):
            raise h.CaptureRefusedError("schema/client proof differs from raw capture")
    binding = h.load(out / "pg-runtime.binding.json")
    h.keys(binding, {"label": str})
    pg_runtime = json.loads(
        output(
            binding["label"],
            r._compose_base(checkout)
            + [
                "exec",
                "-T",
                "postgres",
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "maezo",
                "-d",
                "maezo",
                "-c",
                PG_RUNTIME_QUERY,
            ],
        )
    )
    check_pg_runtime(pg_runtime)
    if not h.same(h.load(out / "pg-runtime.json"), pg_runtime):
        raise h.CaptureRefusedError("PostgreSQL runtime copy differs from raw capture")
    expected_labels = {
        label for label in commands if re.fullmatch(r"(?:resources|container|schema)-[0-9]{3}", label)
    }
    if set(used) != expected_labels:
        raise h.CaptureRefusedError("unused service command records")


def timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(None):
            raise ValueError("not UTC")
        return parsed
    except (TypeError, ValueError) as exc:
        raise h.CaptureRefusedError("invalid UTC capture timestamp") from exc


def validate_lifecycle(out: Path, asserted: list, commands: dict) -> None:
    packet = h.Packet(out)
    events = [
        h.load(out / name) for name in sorted(packet.files) if re.fullmatch(r"lease-[0-9]{2}\.json", name)
    ]
    if not h.same(asserted, events) or len(events) != len(NORMAL_EVENTS):
        raise h.CaptureRefusedError("lifecycle copies/set differ")
    for index, (event, phase) in enumerate(zip(events, NORMAL_EVENTS, strict=True)):
        h.keys(
            event,
            {
                "phase": str,
                "at": str,
                "pid": int,
                "token_sha256": str,
                "lock_path": str,
                "owned": bool,
                "pending_pgids": list,
            },
        )
        if (
            event["phase"] != phase
            or event["lock_path"] != str(r.LOCK_DIR)
            or event["owned"] is not (phase != "released")
            or event["pid"] <= 0
            or event["pending_pgids"]
            or not re.fullmatch(r"[0-9a-f]{64}", event["token_sha256"])
            or (event["pid"], event["token_sha256"]) != (events[0]["pid"], events[0]["token_sha256"])
            or index
            and event["at"] < events[index - 1]["at"]
        ):
            raise h.CaptureRefusedError("lease continuity/order differs")

    start, finish = timestamp(events[0]["at"]), timestamp(events[-1]["at"])
    previous = start
    for event in events:
        now = timestamp(event["at"])
        if now < previous:
            raise h.CaptureRefusedError("lifecycle timestamp order differs")
        previous = now
    for command in commands.values():
        if not start <= timestamp(command["started_at"]) <= timestamp(command["finished_at"]) <= finish:
            raise h.CaptureRefusedError("subprocess outside owned lease interval")


def validate_receipt(out: Path, repo: Path, observations: dict) -> dict:
    """Recompute fixed-source, raw recipe and lifecycle consistency only."""
    packet = h.Packet(out)
    h.verify_guard(out)
    if any(
        name in packet.files
        for name in ("unresolved.json", "cleanup-unresolved.json", "schema-cleanup-query-unresolved.json")
    ):
        raise h.CaptureRefusedError("unresolved lifecycle cannot carry a successful receipt")
    value = h.load(out / "receipt.json")
    h.keys(
        value,
        {
            "schema": str,
            "proof_id": str,
            "source": dict,
            "environment": dict,
            "execution": dict,
            "coverage": dict,
            "scope": dict,
            "cleanup": dict,
            "lifecycle": list,
        },
    )
    if value["schema"] != "maezo-historical-migration-proof/v1" or value["proof_id"] != PROOF:
        raise h.CaptureRefusedError("wrong migration receipt identity")
    h.ancestry(repo, SOURCE_SHA)
    sources = {}
    for entry in filter(None, h.git(repo, "ls-tree", "-rz", SOURCE_SHA).split(b"\0")):
        head, path = entry.split(b"\t", 1)
        mode, kind, blob = head.decode().split()
        if mode not in {"100644", "100755"} or kind != "blob":
            raise h.CaptureRefusedError("nonregular historical Git source")
        sources[path.decode()] = {"blob": blob, "sha256": h.digest(h.git(repo, "cat-file", "blob", blob))}
    if (
        not h.same(value["source"], source_claim(repo, sources))
        or not h.same(h.load(out / "source-before.json"), sources)
        or not h.same(h.load(out / "source-after.json"), sources)
    ):
        raise h.CaptureRefusedError("historical source binding changed")
    env = value["environment"]
    h.keys(env, {"checkout": str, "venv": str, "inventory": dict, "preparation": dict, "dsn_identity": dict})
    checkout = Path(env["checkout"])
    if (
        not checkout.is_absolute()
        or env["venv"] != str(checkout / ".venv")
        or not h.same(env["dsn_identity"], DSN_IDENTITY)
    ):
        raise h.CaptureRefusedError("wrong owned environment identity")
    commands = {
        name.removesuffix(".command.json"): command_record(out, name.removesuffix(".command.json"))
        for name in packet.files
        if "/" not in name and name.endswith(".command.json")
    }
    required = {"clone", "checkout", "uv-version", "prepare", "inventory-before", "inventory-after", "pytest"}
    if not required <= commands.keys() or any(
        not re.fullmatch(r"(?:resources|container|schema)-[0-9]{3}", label)
        for label in commands.keys() - required
    ):
        raise h.CaptureRefusedError("unexpected command set")
    c = commands["pytest"]
    if c["argv"] != recipe_argv(checkout) or c["cwd"] != str(checkout) or not h.same(value["execution"], c):
        raise h.CaptureRefusedError("wrong whole-file verbose migration command")
    pre = h.inventory(commands["inventory-before"], out, checkout)
    post = h.inventory(commands["inventory-after"], out, checkout)
    if not h.same(pre, post) or not h.same(pre, env["inventory"]):
        raise h.CaptureRefusedError("inventory drift or copied assertion changed")
    for label in ("inventory-before", "inventory-after"):
        if commands[label]["argv"] != [
            str(checkout / ".venv/bin/python"),
            "-I",
            "-c",
            h.INVENTORY,
        ] or commands[label]["cwd"] != str(checkout):
            raise h.CaptureRefusedError("wrong inventory probe")
    prep = env["preparation"]
    h.keys(prep, {"uv_path": str, "uv_sha256": str, "uv_version": str, "prepare": dict})
    if (
        not h.same(prep["prepare"], commands["prepare"])
        or prep["uv_sha256"] != h.runtime_digest("uv", prep["uv_path"])
        or prep["uv_version"] != h.read(out / "uv-version.stdout", root=out).decode().strip()
    ):
        raise h.CaptureRefusedError("preparation identity changed")
    cutoff = (
        tomllib.loads(h.git(repo, "show", f"{SOURCE_SHA}:uv.lock").decode())
        .get("options", {})
        .get("exclude-newer")
    )
    resolution = ["--exclude-newer", str(cutoff)] if cutoff is not None else []
    expected = [
        prep["uv_path"],
        "run",
        "--offline",
        "--locked",
        "--no-config",
        "--no-env-file",
        "--extra",
        "dev",
        *resolution,
        "--project",
        str(checkout),
        "python",
        "-I",
        "-c",
        "import sys; print(sys.prefix)",
    ]
    if commands["prepare"]["argv"] != expected or commands["prepare"]["cwd"] != str(checkout):
        raise h.CaptureRefusedError("preparation not own historical locked environment")
    coverage = h.validate_run(out, checkout, TEST, sources, c, check_files=False)
    if not h.same(value["coverage"], coverage):
        raise h.CaptureRefusedError("phase/recipe assertions changed")
    expected_scope = {
        "original_observations": observations,
        "original_quiet_log": ORIGINAL_QUIET,
        "operational_validation": "not-performed",
        "current_proof_disposition": "unresolved",
        "historical_claim_truth": "equal"
        if coverage["recipe_sha256"] == "sha256:" + value["source"]["declaration"]
        else "mismatch",
        "status": "observation-only",
    }
    if not h.same(value["scope"], expected_scope):
        raise h.CaptureRefusedError("scope assertions changed")
    validate_lifecycle(out, value["lifecycle"], commands)
    cleanup = {
        "services_removed": True,
        "source_removed": True,
        "lease_released": True,
        "pending_pgids": [],
        "source_verified": True,
    }
    source_cleanup = h.load(out / "checkout-cleanup.json")
    h.keys(source_cleanup, {"path": str, "removed": bool, "pending_pgids": list})
    if (
        not h.same(value["cleanup"], cleanup)
        or source_cleanup["removed"] is not True
        or source_cleanup["pending_pgids"]
        or Path(source_cleanup["path"]) != checkout.parent
        or checkout.exists()
    ):
        raise h.CaptureRefusedError("source/lease cleanup assertions differ")
    empty = {"container": [], "volume": [], "network": []}
    for label in ("before-start", "after-teardown"):
        if not h.same(h.load(out / f"resources-{label}.json"), empty):
            raise h.CaptureRefusedError("freshness/teardown resource inventory not empty")
    containers = h.load(out / "containers-started.json")
    active = h.load(out / "resources-started.json")
    h.keys(active, {"container": list, "volume": list, "network": list})
    if (
        type(containers) is not list
        or len(containers) != len(SERVICES)
        or set(x["service"] for x in containers) != set(SERVICES)
    ):
        raise h.CaptureRefusedError("owned container set differs")
    for item in containers:
        check_container(item, checkout, item["id"])
    if (
        active["container"] != sorted(item["id"] for item in containers)
        or active["volume"] != [r.PROJECT + "_pgdata"]
        or active["network"] != [r.PROJECT + "_default"]
    ):
        raise h.CaptureRefusedError("resource/container identity differs")
    for label in ("before", "after", "cleanup"):
        if not h.same(h.load(out / f"schemas-{label}.json"), {"schemas": [], "active_clients": 0}):
            raise h.CaptureRefusedError("schema/client cleanup unresolved")
    validate_service_captures(out, checkout, commands)
    deployment = h.load(out / "deployment-provenance.json")
    h.keys(
        deployment,
        {
            "verified_at": str,
            "engine": dict,
            "declared_image": str,
            "checkout_sha": str,
            "compose_sha256": str,
            "override_sha256": str,
            "definitions": list,
            "mismatches": list,
        },
    )
    h.keys(deployment["engine"], {"version": str})
    if deployment["declared_image"] != IMAGES["cibseven"] or not deployment["engine"]["version"]:
        raise h.CaptureRefusedError("wrong engine deployment identity")
    if (
        deployment.get("checkout_sha") != SOURCE_SHA
        or deployment.get("mismatches") != []
        or not deployment.get("definitions")
        or deployment.get("compose_sha256") != sources["docker-compose.yml"]["sha256"]
        or deployment.get("override_sha256")
        != sources["scripts/dev/docker-compose.engine-integration.yml"]["sha256"]
    ):
        raise h.CaptureRefusedError("deployment/source assertions differ")
    for definition in deployment["definitions"]:
        h.keys(
            definition,
            {
                "kind": str,
                "key": str,
                "definition_id": str,
                "deployment_id": str,
                "version": int,
                "resource": str,
                "source_sha256": str,
                "engine_xml_sha256": str,
                "exact_match": bool,
            },
        )
        if definition["kind"] not in {"process", "decision"} or definition["version"] <= 0:
            raise h.CaptureRefusedError("invalid deployed definition identity")
        if (
            definition.get("exact_match") is not True
            or definition.get("source_sha256") != sources.get(definition.get("resource"), {}).get("sha256")
            or definition.get("engine_xml_sha256") != definition["source_sha256"]
        ):
            raise h.CaptureRefusedError("deployment XML differs from historical source")
    if not h.same(h.load(out / "manifest.json"), packet.hashes(exclude="manifest.json")):
        raise h.CaptureRefusedError("manifest differs")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proof", choices=[PROOF])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--original-packet", type=Path, required=True)
    args = parser.parse_args()
    observations = original_observations(args.original_packet.absolute())

    def interrupted(signum, frame):
        raise r.RunnerInterrupted()

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    capture_migration(args.repo.resolve(), args.output.absolute(), observations)
    print("Historical migration observation captured; ledger acceptance not performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
