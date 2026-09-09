#!/usr/bin/env python3
"""Private historical observations only; never an operational ledger acceptance gate.

Reuse the reviewed runner's checkout/materialization and process-group primitives,
and the ledger checker's exact guard/recipe. This adapter adds bounded raw streams,
phase/inventory evidence and receipts without changing either canonical v1 lane.
Only the two source commits below are exposed through the CLI. Source review is
an assumption: audit hooks and receipts are provenance, not an OS sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "scripts/dev/run_engine_integration.py": (
        "02810ce8ecda0c7d609c1f58a3b3da8816a1cc5190c37e746593bcd39f3b80d3"
    ),
    "scripts/ci/check_evidence_ledger_hashes.py": (
        "8ac5085b95b6d4e3cbaee4decfa1315306ae3747eb77a54a57024096c38bfcac"
    ),
}
CATALOG = {
    "PFV8821": (
        "8821f675b369db5de20b0c846d007015ec40fd1e",
        "tests/unit/platform/test_migration_0010_webhook_wamid_dedup.py",
        "DRIVER-IDEMPOTENCY-ORPHAN-TABLE",
        "2026-09-04",
    ),
    "D6unit136d": (
        "136d3edbda3976b91e406a7bc537e82639d97e21",
        "tests/unit/gateway/human/test_durable_projection.py",
        "PLAN-PORTAL-D6-DURABLE-HUMAN-COMMAND",
        "2026-09-08",
    ),
}
MAX_BYTES = 8 * 1024 * 1024


class CaptureRefusedError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def write(path: Path, data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise CaptureRefusedError("oversized structured evidence")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def load(path: Path, limit: int = MAX_BYTES) -> Any:
    if path.is_symlink() or path.stat().st_size > limit:
        raise CaptureRefusedError("nonregular or oversized evidence")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CaptureRefusedError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(CaptureRefusedError("nonfinite JSON")),
        )

        def depth(item, n=0):
            if n > 16:
                raise CaptureRefusedError("deep JSON")
            if isinstance(item, dict):
                for child in item.values():
                    depth(child, n + 1)
            elif isinstance(item, list):
                for child in item:
                    depth(child, n + 1)

        depth(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CaptureRefusedError("invalid JSON") from exc


def keys(value: Any, expected: dict[str, type]) -> None:
    if type(value) is not dict or set(value) != set(expected):
        raise CaptureRefusedError("unknown or missing receipt fields")
    if any(type(value[name]) is not kind for name, kind in expected.items()):
        raise CaptureRefusedError("wrong receipt scalar type")


def module(relative: str, name: str):
    path = ROOT / relative
    data = path.read_bytes()
    if digest(data) != PINS[relative]:
        raise CaptureRefusedError("reviewed tooling source changed")
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    exec(compile(data, str(path), "exec"), result.__dict__)
    return result


runner = module("scripts/dev/run_engine_integration.py", "historical_capture_runner")
checker = module("scripts/ci/check_evidence_ledger_hashes.py", "historical_capture_checker")


def environment() -> dict[str, str]:
    contaminated = [
        key
        for key in os.environ
        if key not in {"PYTEST_CURRENT_TEST", "PYTEST_VERSION"}
        and key.startswith(("PYTEST_", "PYTHON", "UV_"))
    ]
    if contaminated:
        raise CaptureRefusedError(
            "ambient Python/pytest/uv configuration present: " + ",".join(sorted(contaminated))
        )
    env = checker.recipe_environment()
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_NO_REPLACE_OBJECTS="1")
    return env


def capture(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    out: Path,
    label: str,
    timeout: float = 300,
    limit: int = MAX_BYTES,
) -> dict:
    """Bound each stream at first write, retain failure bytes and prove owned PG gone."""
    started = runner._now()
    paths = [out / f"{label}.stdout", out / f"{label}.stderr"]
    files = [os.fdopen(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") for p in paths]
    status = "completed"
    process = None
    cleanup = False
    sizes = [0, 0]
    with selectors.DefaultSelector() as selector:
        try:
            with runner._cleanup_signal_mask():
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                runner._record_pending(process.pid)
            for index, pipe in enumerate([process.stdout, process.stderr]):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, index)
            deadline = time.monotonic() + timeout
            while selector.get_map() or process.poll() is None:
                if time.monotonic() >= deadline:
                    status = "timeout"
                    break
                for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                    index = key.data
                    data = os.read(key.fileobj.fileno(), min(65536, limit - sizes[index] + 1))
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    permitted = data[: limit - sizes[index]]
                    files[index].write(permitted)
                    sizes[index] += len(permitted)
                    if len(permitted) != len(data):
                        status = "oversized"
                        break
                if status != "completed":
                    break
        except (KeyboardInterrupt, runner.RunnerInterrupted):
            status = "interrupted"
        finally:
            try:
                if process is not None:
                    # Only bounded OS pipe remainders remain. No output is rewritten.
                    for pipe in (process.stdout, process.stderr):
                        os.set_blocking(pipe.fileno(), True)
                    remaining = runner._quiesce_group(process, process.pid)
                    for index, data in enumerate(remaining):
                        if data:
                            permitted = data[: limit - sizes[index]]
                            files[index].write(permitted)
                            sizes[index] += len(permitted)
                            if len(permitted) != len(data):
                                status = "oversized"
                    cleanup = True
            except runner.ProcessGroupCleanupError:
                status = "cleanup-failed"
            finally:
                for stream in files:
                    stream.close()
                result = {
                    "argv": argv,
                    "cwd": str(cwd),
                    "started_at": started,
                    "finished_at": runner._now(),
                    "rc": process.returncode if process else -999,
                    "status": status,
                    "quiescent": cleanup,
                    "pgid": process.pid if process else 0,
                    "stdout": paths[0].name,
                    "stderr": paths[1].name,
                    "stdout_sha256": digest(paths[0].read_bytes()),
                    "stderr_sha256": digest(paths[1].read_bytes()),
                    "combined_sha256": digest(paths[0].read_bytes() + paths[1].read_bytes()),
                }
                write(out / f"{label}.command.json", encode(result))
    return result


def successful(command: dict) -> None:
    if command["rc"] != 0 or command["status"] != "completed" or not command["quiescent"]:
        raise CaptureRefusedError("capture did not complete cleanly")


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=repo,
        env=environment(),
        capture_output=True,
        timeout=30,
        check=True,
    )
    return result.stdout


def ancestry(repo: Path, sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise CaptureRefusedError("full commit required")
    if git(repo, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise CaptureRefusedError("shallow source refused")
    graft = Path(os.fsdecode(git(repo, "rev-parse", "--git-path", "info/grafts").strip()))
    if not graft.is_absolute():
        graft = repo / graft
    if graft.exists() and graft.stat().st_size:
        raise CaptureRefusedError("grafted source refused")
    if git(repo, "for-each-ref", "refs/replace").strip():
        raise CaptureRefusedError("replacement refs refused")
    if git(repo, "cat-file", "-t", sha).strip() != b"commit":
        raise CaptureRefusedError("source is not commit")
    git(repo, "merge-base", "--is-ancestor", sha, "HEAD")
    if (
        git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-only").strip()
        or git(repo, "diff", "--cached", "--name-only").strip()
    ):
        raise CaptureRefusedError("dirty source refused")


def tracked(repo: Path, sha: str) -> dict:
    # Same regular Git-blob materialization checks as execution_checkout; add durable map.
    result = {}
    for entry in filter(None, git(repo, "ls-tree", "-rz", sha).split(b"\0")):
        header, raw = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        relative = raw.decode("utf-8")
        path = repo / relative
        if kind != "blob" or mode not in {"100644", "100755"} or path.is_symlink() or not path.is_file():
            raise CaptureRefusedError("nonregular source")
        data = path.read_bytes()
        actual = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
        ).hexdigest()
        if actual != blob or path.name == ".env":
            raise CaptureRefusedError("source bytes differ or dotenv present")
        result[relative] = {"blob": blob, "sha256": digest(data)}
    return result


INVENTORY = r"""
import hashlib, importlib.metadata as m, json, pathlib, sys
items = []
for d in m.distributions():
    direct = d.read_text("direct_url.json")
    items.append({"name": d.metadata["Name"], "version": d.version,
                  "location": str(pathlib.Path(d.locate_file("")).resolve()),
                  "direct_url": json.loads(direct) if direct else None})
items.sort(key=lambda x: (x["name"].lower(), x["version"], x["location"]))
p = pathlib.Path(sys.executable).resolve()
print(json.dumps({"distributions": items, "interpreter": {"path": str(p),
 "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "version": sys.version,
 "implementation": sys.implementation.name, "cache_tag": sys.implementation.cache_tag,
 "prefix": sys.prefix, "base_prefix": sys.base_prefix}}, sort_keys=True))
"""


def inventory(command: dict, out: Path, checkout: Path) -> dict:
    successful(command)
    value = load(out / command["stdout"])
    keys(value, {"distributions": list, "interpreter": dict})
    keys(
        value["interpreter"],
        {
            k: str
            for k in ("path", "sha256", "version", "implementation", "cache_tag", "prefix", "base_prefix")
        },
    )
    if Path(value["interpreter"]["prefix"]).resolve() != checkout / ".venv":
        raise CaptureRefusedError("borrowed venv")
    interpreter = Path(value["interpreter"]["path"])
    if (
        interpreter.resolve() != interpreter
        or digest(interpreter.read_bytes()) != value["interpreter"]["sha256"]
    ):
        raise CaptureRefusedError("interpreter identity changed")
    if value["interpreter"]["prefix"] == value["interpreter"]["base_prefix"]:
        raise CaptureRefusedError("not a venv")
    editable = 0
    for item in value["distributions"]:
        if set(item) != {"name", "version", "location", "direct_url"} or any(
            type(item[k]) is not str for k in ("name", "version", "location")
        ):
            raise CaptureRefusedError("bad distribution")
        if not Path(item["location"]).is_relative_to(checkout / ".venv"):
            raise CaptureRefusedError("dependency outside owned venv")
        direct = item["direct_url"]
        if direct is not None:
            if type(direct) is not dict or type(direct.get("url")) is not str:
                raise CaptureRefusedError("invalid direct URL metadata")
            parsed = urlparse(direct["url"])
            if parsed.scheme == "file":
                local = Path(unquote(parsed.path)).resolve()
                if parsed.netloc or local != checkout or direct.get("dir_info") != {"editable": True}:
                    raise CaptureRefusedError("borrowed local/editable dependency")
                editable += 1
            elif direct.get("dir_info", {}).get("editable"):
                raise CaptureRefusedError("nonlocal editable dependency")
    if editable > 1:
        raise CaptureRefusedError("multiple editable projects")
    return value


# Explicit extension of the exact reviewed guard: path mechanism unchanged, receipt
# persists start/finish, full paths and phase observations. Never selects/alters items.
GUARD = (
    checker._SOURCE_GUARD_PLUGIN
    + r"""
_BASE_START = pytest_sessionstart
_BASE_FINISH = pytest_sessionfinish
_PHASE = {"started": False, "finished": False, "collected": [], "selected": [],
          "deselected": [], "phases": [], "collection_errors": [], "exitstatus": -999}


def pytest_sessionstart(session):
    _BASE_START(session)
    _PHASE["started"] = True


def pytest_itemcollected(item):
    _PHASE["collected"].append(item.nodeid)


def pytest_collection_finish(session):
    _PHASE["selected"] = [item.nodeid for item in session.items]


def pytest_deselected(items):
    _PHASE["deselected"].extend(item.nodeid for item in items)


def pytest_collectreport(report):
    if report.failed:
        _PHASE["collection_errors"].append(report.nodeid)


def pytest_runtest_logreport(report):
    _PHASE["phases"].append({"nodeid": report.nodeid, "when": report.when,
                             "outcome": report.outcome, "wasxfail": hasattr(report, "wasxfail")})


def pytest_sessionfinish(session, exitstatus):
    _BASE_FINISH(session, exitstatus)
    _PHASE["finished"] = True
    _PHASE["exitstatus"] = int(exitstatus)
    paths = _module_paths()
    result = {"archived": [str(p) for p in _archived(paths)],
              "escaped": [str(p) for p in _escaped(paths)], "coverage": _PHASE}
    fd = os.open(os.environ["HISTORICAL_PHASE_RECEIPT"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(result, output, sort_keys=True)
"""
)


def validate_run(
    out: Path, checkout: Path, test: str, sources: dict, command: dict, *, check_files: bool = True
) -> dict:
    successful(command)
    raw = (out / command["stdout"]).read_bytes() + (out / command["stderr"]).read_bytes()
    if digest(raw) != command["combined_sha256"]:
        raise CaptureRefusedError("raw stream changed")
    try:
        lines = checker.extract_result_lines(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise CaptureRefusedError("non UTF-8 recipe") from exc
    marker = load(out / "phase.json")
    base = load(out / "guard-base.json")
    keys(base, {"archived": list, "escaped": list})
    if base != {key: marker.get(key) for key in ("archived", "escaped")}:
        raise CaptureRefusedError("guard receipts differ")
    keys(marker, {"archived": list, "escaped": list, "coverage": dict})
    phase = marker["coverage"]
    keys(
        phase,
        {
            "started": bool,
            "finished": bool,
            "collected": list,
            "selected": list,
            "deselected": list,
            "phases": list,
            "collection_errors": list,
            "exitstatus": int,
        },
    )
    nodes = phase["selected"]
    if (
        not phase["started"]
        or not phase["finished"]
        or phase["exitstatus"] != 0
        or not nodes
        or phase["deselected"]
        or phase["collection_errors"]
    ):
        raise CaptureRefusedError("incomplete phases/collection")
    if (
        any(type(n) is not str or not n.startswith(test + "::") for n in nodes)
        or len(set(nodes)) != len(nodes)
        or sorted(nodes) != sorted(phase["collected"])
    ):
        raise CaptureRefusedError("selection differs from whole file")
    expected = {(n, when) for n in nodes for when in ("setup", "call", "teardown")}
    observed = []
    for p in phase["phases"]:
        keys(p, {"nodeid": str, "when": str, "outcome": str, "wasxfail": bool})
        if p["outcome"] != "passed" or p["wasxfail"]:
            raise CaptureRefusedError("nonpassing pytest phase")
        observed.append((p["nodeid"], p["when"]))
    if set(observed) != expected or len(observed) != len(expected):
        raise CaptureRefusedError("missing/duplicate phase")
    if sorted(lines) != sorted(n + " PASSED" for n in nodes):
        raise CaptureRefusedError("raw recipe does not bind full selected identities")
    if marker["escaped"] or str(checkout / test) not in marker["archived"]:
        raise CaptureRefusedError("missing exact source test or escaped path")
    bindings = {}
    for raw_path in marker["archived"]:
        path = Path(raw_path)
        if not path.is_relative_to(checkout):
            raise CaptureRefusedError("escaped source trace")
        relative = path.relative_to(checkout).as_posix()
        # Dependency interpreter paths are classified trusted by the reviewed guard.
        if relative.startswith(".venv/"):
            continue
        if relative not in sources or (
            check_files and digest(path.read_bytes()) != sources[relative]["sha256"]
        ):
            raise CaptureRefusedError("unbound project execution path")
        bindings[relative] = sources[relative]
    return {
        "recipe_version": "result-lines-fixed-v1",
        "recipe_sha256": checker.compute_recipe_hash(lines),
        "nodes": nodes,
        "collected_count": len(phase["collected"]),
        "selected_count": len(nodes),
        "deselected_count": 0,
        "bindings": bindings,
        "guard_sha256": digest(GUARD.encode()),
    }


@contextmanager
def execution_checkout(repo: Path, sha: str, out: Path, env: dict):
    """Narrow extraction of runner.execution_checkout, preserving its Git checks.

    Separate preparation capture is needed because its existing _run intentionally
    redacts streams. No monkeypatch of the approved runner or lock comparator.
    """
    ancestry(repo, sha)
    scratch = Path(tempfile.mkdtemp(prefix="maezo-historical-proof-")).resolve()
    checkout = scratch / "checkout"
    try:
        successful(
            capture(
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
        successful(
            capture(
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
        if git(checkout, "rev-parse", "HEAD").strip().decode() != sha:
            raise CaptureRefusedError("checkout SHA mismatch")
        before = tracked(checkout, sha)
        for required in ("uv.lock", "pyproject.toml", ".python-version"):
            if required not in before:
                raise CaptureRefusedError("missing locked configuration")
        config = tomllib.loads((checkout / "pyproject.toml").read_text())
        pytest_config = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
        if pytest_config.get("addopts") or pytest_config.get("pythonpath", []) not in ([], ["."]):
            raise CaptureRefusedError("source pytest selectors/path injection refused")
        if any((checkout / name).exists() for name in ("pytest.ini", "setup.cfg", "tox.ini", "uv.toml")):
            raise CaptureRefusedError("alternative tool configuration refused")
        write(out / "source-before.json", encode(before))
        uv = Path(shutil.which("uv", path=env["PATH"])).resolve()
        uv_version = capture([str(uv), "--version"], checkout, env, out, "uv-version", 20)
        successful(uv_version)
        command = runner._uv_python(checkout, "-c", "import sys; print(sys.prefix)")
        command[0] = str(uv)
        command.insert(2, "--offline")
        prep = capture(command, checkout, env, out, "prepare", 180)
        successful(prep)
        if tracked(checkout, sha) != before:
            raise CaptureRefusedError("source changed during preparation")
        yield (
            checkout,
            before,
            {
                "uv_path": str(uv),
                "uv_sha256": digest(uv.read_bytes()),
                "uv_version": (out / uv_version["stdout"]).read_text().strip(),
                "prepare": prep,
            },
        )
    finally:
        removed = not runner._pending_groups
        if removed:
            shutil.rmtree(scratch)
        write(
            out / "checkout-cleanup.json",
            encode(
                {"path": str(scratch), "removed": removed, "pending_pgids": sorted(runner._pending_groups)}
            ),
        )


def capture_source(
    repo: Path,
    sha: str,
    test: str,
    out: Path,
    proof_id: str,
    task: str,
    date: str,
    observations: dict[str, str],
) -> dict:
    """Internal entry for tiny reviewed fixtures; CLI is restricted to CATALOG."""
    env = environment()
    out.mkdir(mode=0o700, exist_ok=False)
    old_mask = os.umask(0o077)
    result = None
    try:
        with execution_checkout(repo, sha, out, env) as (checkout, sources, preparation):
            ledger = (checkout / "docs/evidence-ledger.md").read_bytes()
            rows = [line for line in ledger.decode().splitlines() if line.startswith(f"| {task} | {date} |")]
            if len(rows) != 1:
                raise CaptureRefusedError("historical row identity ambiguous")
            declaration = re.findall(r"sha256:([0-9a-f]{64}) \(" + re.escape(test) + r"\)", rows[0])
            if len(declaration) != 1 or test not in sources:
                raise CaptureRefusedError("declaration/test not exact")
            python = str(checkout / ".venv/bin/python")
            pre = inventory(
                capture([python, "-I", "-c", INVENTORY], checkout, env, out, "inventory-before"),
                out,
                checkout,
            )
            guard = out / "guard"
            guard.mkdir(mode=0o700)
            write(guard / "ledger_source_guard.py", GUARD.encode())
            runtime = dict(env)
            runtime.update(
                PYTHONPATH=os.pathsep.join((str(guard), str(checkout / "src"), str(checkout))),
                LEDGER_ARCHIVE_ROOT=str(checkout),
                LEDGER_SOURCE_GUARD_ROOT=str(guard),
                LEDGER_SOURCE_GUARD_MARKER=str(out / "guard-base.json"),
                HISTORICAL_PHASE_RECEIPT=str(out / "phase.json"),
            )
            argv = [
                python,
                "-P",
                "-m",
                "pytest",
                test,
                "-v",
                "--tb=no",
                "-p",
                "no:cacheprovider",
                "-p",
                "ledger_source_guard",
            ]
            command = capture(argv, checkout, runtime, out, "pytest")
            post = inventory(
                capture([python, "-I", "-c", INVENTORY], checkout, env, out, "inventory-after"), out, checkout
            )
            after = tracked(checkout, sha)
            write(out / "source-after.json", encode(after))
            if pre != post or sources != after:
                raise CaptureRefusedError("inventory/source changed across recipe")
            run = validate_run(out, checkout, test, sources, command)
            result = {
                "schema": "maezo-historical-recipe-proof/v1",
                "proof_id": proof_id,
                "source": {
                    "commit": sha,
                    "tree": git(checkout, "rev-parse", "HEAD^{tree}").strip().decode(),
                    "ledger_sha256": digest(ledger),
                    "row_sha256": digest(rows[0].encode()),
                    "task": task,
                    "date": date,
                    "declaration": declaration[0],
                    "test": test,
                    "test_sha256": sources[test]["sha256"],
                    "physical_occurrences": 1,
                    "before_sha256": digest(encode(sources)),
                    "after_sha256": digest(encode(after)),
                    "config": {
                        key: sources[key]["sha256"]
                        for key in ("uv.lock", "pyproject.toml", ".python-version")
                    },
                    "tooling": {
                        **PINS,
                        "helper": digest(Path(__file__).read_bytes()),
                        "guard": digest(GUARD.encode()),
                    },
                },
                "environment": {
                    "checkout": str(checkout),
                    "venv": str(checkout / ".venv"),
                    "inventory_sha256": digest(encode(pre)),
                    "inventory": pre,
                    "preparation": preparation,
                },
                "execution": command,
                "coverage": run,
                "scope": {
                    "original_observations": observations,
                    "operational_validation": "not-performed",
                    "historical_claim_truth": "equal"
                    if run["recipe_sha256"] == "sha256:" + declaration[0]
                    else "mismatch",
                    "current_proof_disposition": "unresolved",
                    "status": "observation-only",
                },
            }
        result["cleanup"] = load(out / "checkout-cleanup.json")
        if not result["cleanup"]["removed"]:
            raise CaptureRefusedError("checkout cleanup incomplete")
        write(out / "receipt.json", encode(result))
        manifest = {
            str(p.relative_to(out)): digest(p.read_bytes()) for p in sorted(out.rglob("*")) if p.is_file()
        }
        write(out / "manifest.json", encode(manifest))
        return result
    except Exception as exc:
        write(out / "unresolved.json", encode({"status": "unresolved", "error_type": type(exc).__name__}))
        raise
    finally:
        os.umask(old_mask)


def validate_receipt(
    out: Path, repo: Path, identity: tuple[str, str, str, str, str], observations: dict[str, str]
) -> dict:
    """Recompute capture consistency. Does NOT confer ledger acceptance or source review.

    The reviewer supplies expected identity/original observations independently and
    freezes this packet's manifest. Self-asserted artifacts cannot certify themselves.
    """
    proof_id, sha, test, task, date = identity
    receipt = load(out / "receipt.json")
    keys(
        receipt,
        {
            "schema": str,
            "proof_id": str,
            "source": dict,
            "environment": dict,
            "execution": dict,
            "coverage": dict,
            "scope": dict,
            "cleanup": dict,
        },
    )
    if receipt["schema"] != "maezo-historical-recipe-proof/v1" or receipt["proof_id"] != proof_id:
        raise CaptureRefusedError("wrong receipt identity")
    source = receipt["source"]
    keys(
        source,
        {
            **{
                k: str
                for k in (
                    "commit",
                    "tree",
                    "ledger_sha256",
                    "row_sha256",
                    "task",
                    "date",
                    "declaration",
                    "test",
                    "test_sha256",
                    "before_sha256",
                    "after_sha256",
                )
            },
            "physical_occurrences": int,
            "config": dict,
            "tooling": dict,
        },
    )
    keys(source["config"], {k: str for k in ("uv.lock", "pyproject.toml", ".python-version")})
    keys(source["tooling"], {**{k: str for k in PINS}, "helper": str, "guard": str})
    ancestry(repo, sha)
    expected_map = {}
    for entry in filter(None, git(repo, "ls-tree", "-rz", sha).split(b"\0")):
        header, raw = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        if mode not in {"100644", "100755"} or kind != "blob":
            raise CaptureRefusedError("nonregular Git source")
        expected_map[raw.decode()] = {"blob": blob, "sha256": digest(git(repo, "cat-file", "blob", blob))}
    before, after = load(out / "source-before.json"), load(out / "source-after.json")
    if expected_map != before or before != after:
        raise CaptureRefusedError("tracked source maps differ from Git")
    ledger = git(repo, "show", f"{sha}:docs/evidence-ledger.md")
    rows = [line for line in ledger.decode().splitlines() if line.startswith(f"| {task} | {date} |")]
    if len(rows) != 1:
        raise CaptureRefusedError("ambiguous ledger identity")
    decl = re.findall(r"sha256:([0-9a-f]{64}) \(" + re.escape(test) + r"\)", rows[0])
    if len(decl) != 1:
        raise CaptureRefusedError("ambiguous declaration")
    expected_source = {
        "commit": sha,
        "tree": git(repo, "rev-parse", f"{sha}^{{tree}}").strip().decode(),
        "ledger_sha256": digest(ledger),
        "row_sha256": digest(rows[0].encode()),
        "task": task,
        "date": date,
        "declaration": decl[0],
        "test": test,
        "test_sha256": before[test]["sha256"],
        "physical_occurrences": 1,
        "before_sha256": digest(encode(before)),
        "after_sha256": digest(encode(after)),
        "config": {k: before[k]["sha256"] for k in ("uv.lock", "pyproject.toml", ".python-version")},
        "tooling": {**PINS, "helper": digest(Path(__file__).read_bytes()), "guard": digest(GUARD.encode())},
    }
    if source != expected_source:
        raise CaptureRefusedError("source receipt assertions differ from Git/tooling")
    e = receipt["environment"]
    keys(e, {"checkout": str, "venv": str, "inventory_sha256": str, "inventory": dict, "preparation": dict})
    checkout = Path(e["checkout"])
    if e["venv"] != str(checkout / ".venv"):
        raise CaptureRefusedError("wrong venv identity")
    commands = {}
    for path in out.glob("*.command.json"):
        c = load(path)
        keys(
            c,
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
        if any(type(arg) is not str for arg in c["argv"]):
            raise CaptureRefusedError("invalid argv")
        successful(c)
        for stream in ("stdout", "stderr"):
            if c[stream] != path.name.removesuffix(".command.json") + "." + stream:
                raise CaptureRefusedError("stream path injection")
            if digest((out / c[stream]).read_bytes()) != c[stream + "_sha256"]:
                raise CaptureRefusedError("stream hash mismatch")
        if (
            digest((out / c["stdout"]).read_bytes() + (out / c["stderr"]).read_bytes())
            != c["combined_sha256"]
        ):
            raise CaptureRefusedError("split capture hash mismatch")
        commands[path.name.removesuffix(".command.json")] = c
    if set(commands) != {
        "clone",
        "checkout",
        "uv-version",
        "prepare",
        "inventory-before",
        "inventory-after",
        "pytest",
    }:
        raise CaptureRefusedError("command record set mismatch")
    expected_argv = [
        str(checkout / ".venv/bin/python"),
        "-P",
        "-m",
        "pytest",
        test,
        "-v",
        "--tb=no",
        "-p",
        "no:cacheprovider",
        "-p",
        "ledger_source_guard",
    ]
    if (
        commands["pytest"]["argv"] != expected_argv
        or commands["pytest"]["cwd"] != str(checkout)
        or receipt["execution"] != commands["pytest"]
    ):
        raise CaptureRefusedError("wrong historical selector/command")
    pre = inventory(commands["inventory-before"], out, checkout)
    post = inventory(commands["inventory-after"], out, checkout)
    if pre != post or pre != e["inventory"] or digest(encode(pre)) != e["inventory_sha256"]:
        raise CaptureRefusedError("inventory assertions changed")
    prep = e["preparation"]
    keys(prep, {"uv_path": str, "uv_sha256": str, "uv_version": str, "prepare": dict})
    if (
        prep["prepare"] != commands["prepare"]
        or prep["uv_sha256"] != digest(Path(prep["uv_path"]).read_bytes())
        or prep["uv_version"] != (out / "uv-version.stdout").read_text().strip()
    ):
        raise CaptureRefusedError("preparation assertions changed")
    if (
        "--locked" not in commands["prepare"]["argv"]
        or commands["prepare"]["argv"][0] != prep["uv_path"]
        or commands["prepare"]["cwd"] != str(checkout)
    ):
        raise CaptureRefusedError("preparation not owned locked checkout")
    lock_options = tomllib.loads(git(repo, "show", f"{sha}:uv.lock").decode()).get("options", {})
    cutoff = lock_options.get("exclude-newer")
    resolution = ["--exclude-newer", str(cutoff)] if cutoff is not None else []
    expected_prep = [
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
    if commands["prepare"]["argv"] != expected_prep:
        raise CaptureRefusedError("preparation argv differs from reviewed locked recipe")
    for label in ("inventory-before", "inventory-after"):
        if commands[label]["argv"] != [str(checkout / ".venv/bin/python"), "-I", "-c", INVENTORY] or commands[
            label
        ]["cwd"] != str(checkout):
            raise CaptureRefusedError("inventory command differs from reviewed probe")
    run = validate_run(out, checkout, test, before, commands["pytest"], check_files=False)
    if receipt["coverage"] != run:
        raise CaptureRefusedError("coverage assertions differ from actual phase/streams")
    expected_scope = {
        "original_observations": observations,
        "operational_validation": "not-performed",
        "historical_claim_truth": "equal" if run["recipe_sha256"] == "sha256:" + decl[0] else "mismatch",
        "current_proof_disposition": "unresolved",
        "status": "observation-only",
    }
    if receipt["scope"] != expected_scope:
        raise CaptureRefusedError("scope/claim assertions changed")
    cleanup = load(out / "checkout-cleanup.json")
    keys(cleanup, {"path": str, "removed": bool, "pending_pgids": list})
    if (
        cleanup != receipt["cleanup"]
        or not cleanup["removed"]
        or cleanup["pending_pgids"]
        or Path(cleanup["path"]) != checkout.parent
        or checkout.exists()
    ):
        raise CaptureRefusedError("cleanup not proven")
    actual = {
        str(p.relative_to(out)): digest(p.read_bytes())
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    if load(out / "manifest.json") != actual:
        raise CaptureRefusedError("manifest mismatch")
    if out.stat().st_mode & 0o777 != 0o700 or any(
        p.is_symlink() or p.stat().st_mode & 0o777 != (0o700 if p.is_dir() else 0o600) for p in out.rglob("*")
    ):
        raise CaptureRefusedError("raw custody permissions invalid")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proof", choices=sorted(CATALOG))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--original-packet", type=Path, required=True)
    args = parser.parse_args()
    sha, test, task, date = CATALOG[args.proof]
    packet = args.original_packet.resolve()
    if (
        digest((packet / "MANIFEST.json").read_bytes())
        != "0fc37de9a53bc846cfe94a5a390c30f328c8e1926912373df63bf7a59cd5b9b6"
    ):
        raise CaptureRefusedError("original observation review packet is not the reviewed version")
    observation = {"MANIFEST.json": digest((packet / "MANIFEST.json").read_bytes())}
    for entry in load(packet / "MANIFEST.json")["entries"]:
        path = packet / entry["path"]
        if (
            not path.resolve().is_relative_to(packet)
            or path.is_symlink()
            or digest(path.read_bytes()) != entry["sha256"]
        ):
            raise CaptureRefusedError("original observation packet changed")
        observation[entry["path"]] = entry["sha256"]

    def interrupted(signum, frame):
        raise runner.RunnerInterrupted()

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    capture_source(args.repo.resolve(), sha, test, args.output.resolve(), args.proof, task, date, observation)
    print("Private historical observation captured; ledger acceptance not performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
