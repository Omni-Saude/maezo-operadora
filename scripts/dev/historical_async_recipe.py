"""Explicit async successor to the immutable babe historical capture primitives.

D7-ASYNC-R1/R2: recipe orchestration and complete receipt validation are owned
here, with a new identity/schema. The two routines below are derived from the
25a41249 helper's reviewed capture/validator, preserving its checks explicitly;
they never call/patch its capture_source or validate_receipt. Preparation, raw
capture, metadata boundary, inventory, guard and phase validation remain literal
byte-pinned dependencies supplied by historical_tool_sources.py. No CLI or
record-controlled source/plugin extension point exists in this module.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

SCHEMA = "maezo-historical-async-recipe-proof/v1"
RECIPE = {
    "id": "whole-file-verbose-explicit-asyncio/v1",
    "plugin": "pytest_asyncio.plugin",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    "result_extraction": "result-lines-fixed-v1",
}
COMMANDS = {"clone", "checkout", "uv-version", "prepare", "inventory-before", "inventory-after", "pytest"}
MEMBERS = {f"{name}.{suffix}" for name in COMMANDS for suffix in ("command.json", "stdout", "stderr")} | {
    "source-before.json",
    "source-after.json",
    "phase.json",
    "guard-base.json",
    "guard/ledger_source_guard.py",
    "checkout-cleanup.json",
    "receipt.json",
    "manifest.json",
}


def source_digest(p: ModuleType, path: Path | None = None) -> str:
    path = Path(__file__) if path is None else path
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1024 * 1024:
            raise p.CaptureRefusedError("nonregular async producer dependency")
        payload = stream.read(1024 * 1024 + 1)
        if any(
            p._identity(info) != p._identity(before)
            for info in (os.fstat(stream.fileno()), os.stat(path, follow_symlinks=False))
        ):
            raise p.CaptureRefusedError("async producer dependency changed during read")
    result: str = p.digest(payload)
    return result


def validate_plugin(
    p: ModuleType, repo: Path, sha: str, out: Path, checkout: Path, inventory: dict[str, Any]
) -> dict[str, Any]:
    """Join actual imported asyncio package to owned inventory and exact source lock."""
    lock = tomllib.loads(p.git(repo, "show", sha + ":uv.lock").decode())

    def normalized(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name.lower())

    allowed = {(normalized(item["name"]), item["version"]) for item in lock["package"]}
    seen = {}
    for item in inventory["distributions"]:
        name = normalized(item["name"])
        if name in seen or (name, item["version"]) not in allowed:
            raise p.CaptureRefusedError("resolved distribution outside own lock")
        seen[name] = item
    if not {"pytest", "pytest-asyncio"} <= seen.keys():
        raise p.CaptureRefusedError("missing locked pytest/pytest-asyncio distribution")
    distribution = seen["pytest-asyncio"]
    location = Path(distribution["location"])
    if (
        not location.is_absolute()
        or location != location.resolve()
        or not location.is_relative_to(checkout / ".venv")
    ):
        raise p.CaptureRefusedError("async plugin distribution outside owned venv")
    package = location / "pytest_asyncio"
    marker = p.load(out / "phase.json", root=out)
    paths = [Path(value) for value in marker["archived"] if "pytest_asyncio" in Path(value).parts]
    expected = {package / "__init__.py", package / "plugin.py"}
    if (
        not expected <= set(paths)
        or any(
            not path.is_absolute() or path != path.resolve() or not path.is_relative_to(package)
            for path in paths
        )
        or len(paths) != len(set(paths))
    ):
        raise p.CaptureRefusedError("missing or substituted owned async plugin import trace")
    return {"distribution": distribution, "imports": sorted(str(path) for path in paths)}


def capture_source(
    p: ModuleType,
    repo: Path,
    sha: str,
    test: str,
    out: Path,
    proof_id: str,
    task: str,
    date: str,
    observations: dict[str, str],
) -> dict[str, Any]:
    """Internal tiny-fixture seam; production entry is the closed D7 catalogue."""
    env = p.environment()
    if env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1":
        raise p.CaptureRefusedError("async recipe requires disabled plugin autoload")
    with p._directory(out.parent) as parent_fd:
        os.mkdir(out.name, mode=0o700, dir_fd=parent_fd)
    old_mask = os.umask(0o077)
    result = None
    try:
        with p.execution_checkout(repo, sha, out, env) as (checkout, sources, preparation):
            ledger = (checkout / "docs/evidence-ledger.md").read_bytes()
            rows = [line for line in ledger.decode().splitlines() if line.startswith(f"| {task} | {date} |")]
            if len(rows) != 1:
                raise p.CaptureRefusedError("historical row identity ambiguous")
            declaration = re.findall("sha256:([0-9a-f]{64}) \\(" + re.escape(test) + "\\)", rows[0])
            if len(declaration) != 1 or test not in sources:
                raise p.CaptureRefusedError("declaration/test not exact")
            python = str(checkout / ".venv/bin/python")
            pre = p.inventory(
                p.capture([python, "-I", "-c", p.INVENTORY], checkout, env, out, "inventory-before"),
                out,
                checkout,
            )
            guard = out / "guard"
            guard.mkdir(mode=0o700)
            p.write(guard / "ledger_source_guard.py", p.GUARD.encode())
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
                "-p",
                "pytest_asyncio.plugin",
            ]
            p.verify_guard(out)
            command = p.capture(argv, checkout, runtime, out, "pytest")
            p.verify_guard(out)
            post = p.inventory(
                p.capture([python, "-I", "-c", p.INVENTORY], checkout, env, out, "inventory-after"),
                out,
                checkout,
            )
            after = p.tracked(checkout, sha)
            p.write(out / "source-after.json", p.encode(after))
            if not p.same(pre, post) or not p.same(sources, after):
                raise p.CaptureRefusedError("inventory/source changed across recipe")
            run = p.validate_run(out, checkout, test, sources, command)
            plugin = validate_plugin(p, repo, sha, out, checkout, pre)
            result = {
                "schema": SCHEMA,
                "recipe": RECIPE,
                "plugin": plugin,
                "proof_id": proof_id,
                "source": {
                    "commit": sha,
                    "tree": p.git(checkout, "rev-parse", "HEAD^{tree}").strip().decode(),
                    "ledger_sha256": p.digest(ledger),
                    "row_sha256": p.digest(rows[0].encode()),
                    "task": task,
                    "date": date,
                    "declaration": declaration[0],
                    "test": test,
                    "test_sha256": sources[test]["sha256"],
                    "physical_occurrences": 1,
                    "before_sha256": p.digest(p.encode(sources)),
                    "after_sha256": p.digest(p.encode(after)),
                    "config": {
                        key: sources[key]["sha256"]
                        for key in ("uv.lock", "pyproject.toml", ".python-version")
                    },
                    "tooling": {
                        **p.PINS,
                        "helper": source_digest(p),
                        "original_helper": source_digest(p, Path(p.__file__ or "")),
                        "guard": p.digest(p.GUARD.encode()),
                    },
                },
                "environment": {
                    "checkout": str(checkout),
                    "venv": str(checkout / ".venv"),
                    "inventory_sha256": p.digest(p.encode(pre)),
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
        result["cleanup"] = p.load(out / "checkout-cleanup.json")
        if not result["cleanup"]["removed"] or result["cleanup"]["pending_pgids"]:
            raise p.CaptureRefusedError("checkout cleanup incomplete")
        p.verify_guard(out)
        p.write(out / "receipt.json", p.encode(result))
        manifest = p.Packet(out).hashes()
        p.write(out / "manifest.json", p.encode(manifest))
        return result
    except Exception as exc:
        p.write(out / "unresolved.json", p.encode({"status": "unresolved", "error_type": type(exc).__name__}))
        raise
    finally:
        os.umask(old_mask)


def validate_receipt(
    p: ModuleType,
    out: Path,
    repo: Path,
    identity: tuple[str, str, str, str, str],
    observations: dict[str, str],
) -> dict[str, Any]:
    """Recompute capture consistency. Does NOT confer ledger acceptance or source review.

    The reviewer supplies expected identity/original observations independently and
    freezes this packet's manifest. Self-asserted artifacts cannot certify themselves.
    """
    packet = p.Packet(out)
    if set(packet.files) != MEMBERS or set(packet.entries) != MEMBERS | {"guard"}:
        raise p.CaptureRefusedError("async packet member set mismatch")
    p.verify_guard(out)
    proof_id, sha, test, task, date = identity
    receipt = p.load(out / "receipt.json")
    p.keys(
        receipt,
        {
            "schema": str,
            "recipe": dict,
            "plugin": dict,
            "proof_id": str,
            "source": dict,
            "environment": dict,
            "execution": dict,
            "coverage": dict,
            "scope": dict,
            "cleanup": dict,
        },
    )
    if (
        receipt["schema"] != SCHEMA
        or receipt["proof_id"] != proof_id
        or not p.same(receipt["recipe"], RECIPE)
    ):
        raise p.CaptureRefusedError("wrong receipt identity")
    source = receipt["source"]
    p.keys(
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
    p.keys(source["config"], {k: str for k in ("uv.lock", "pyproject.toml", ".python-version")})
    p.keys(
        source["tooling"], {**{k: str for k in p.PINS}, "helper": str, "original_helper": str, "guard": str}
    )
    p.ancestry(repo, sha)
    expected_map = {}
    for entry in filter(None, p.git(repo, "ls-tree", "-rz", sha).split(b"\x00")):
        header, raw = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        if mode not in {"100644", "100755"} or kind != "blob":
            raise p.CaptureRefusedError("nonregular Git source")
        expected_map[raw.decode()] = {"blob": blob, "sha256": p.digest(p.git(repo, "cat-file", "blob", blob))}
    before, after = (p.load(out / "source-before.json"), p.load(out / "source-after.json"))
    if not p.same(expected_map, before) or not p.same(before, after):
        raise p.CaptureRefusedError("tracked source maps differ from Git")
    ledger = p.git(repo, "show", f"{sha}:docs/evidence-ledger.md")
    rows = [line for line in ledger.decode().splitlines() if line.startswith(f"| {task} | {date} |")]
    if len(rows) != 1:
        raise p.CaptureRefusedError("ambiguous ledger identity")
    decl = re.findall("sha256:([0-9a-f]{64}) \\(" + re.escape(test) + "\\)", rows[0])
    if len(decl) != 1:
        raise p.CaptureRefusedError("ambiguous declaration")
    expected_source = {
        "commit": sha,
        "tree": p.git(repo, "rev-parse", f"{sha}^{{tree}}").strip().decode(),
        "ledger_sha256": p.digest(ledger),
        "row_sha256": p.digest(rows[0].encode()),
        "task": task,
        "date": date,
        "declaration": decl[0],
        "test": test,
        "test_sha256": before[test]["sha256"],
        "physical_occurrences": 1,
        "before_sha256": p.digest(p.encode(before)),
        "after_sha256": p.digest(p.encode(after)),
        "config": {k: before[k]["sha256"] for k in ("uv.lock", "pyproject.toml", ".python-version")},
        "tooling": {
            **p.PINS,
            "helper": source_digest(p),
            "original_helper": source_digest(p, Path(p.__file__ or "")),
            "guard": p.digest(p.GUARD.encode()),
        },
    }
    if not p.same(source, expected_source):
        raise p.CaptureRefusedError("source receipt assertions differ from Git/tooling")
    e = receipt["environment"]
    p.keys(e, {"checkout": str, "venv": str, "inventory_sha256": str, "inventory": dict, "preparation": dict})
    checkout = Path(e["checkout"])
    if e["venv"] != str(checkout / ".venv"):
        raise p.CaptureRefusedError("wrong venv identity")
    commands = {}
    for name in sorted(packet.files):
        if "/" in name or not name.endswith(".command.json"):
            continue
        path = out / name
        c = p.load(path)
        p.keys(
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
            raise p.CaptureRefusedError("invalid argv")
        p.successful(c)
        started, finished = (datetime.fromisoformat(c[key]) for key in ("started_at", "finished_at"))
        if started.tzinfo is None or finished.tzinfo is None or started > finished or c["pgid"] <= 0:
            raise p.CaptureRefusedError("invalid command lifetime")
        for stream in ("stdout", "stderr"):
            if c[stream] != path.name.removesuffix(".command.json") + "." + stream:
                raise p.CaptureRefusedError("stream path injection")
            if p.digest(p.read(out / c[stream], root=out)) != c[stream + "_sha256"]:
                raise p.CaptureRefusedError("stream hash mismatch")
        if (
            p.digest(p.read(out / c["stdout"], root=out) + p.read(out / c["stderr"], root=out))
            != c["combined_sha256"]
        ):
            raise p.CaptureRefusedError("split capture hash mismatch")
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
        raise p.CaptureRefusedError("command record set mismatch")
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
        "-p",
        "pytest_asyncio.plugin",
    ]
    if (
        commands["pytest"]["argv"] != expected_argv
        or commands["pytest"]["cwd"] != str(checkout)
        or (not p.same(receipt["execution"], commands["pytest"]))
    ):
        raise p.CaptureRefusedError("wrong historical selector/command")
    pre = p.inventory(commands["inventory-before"], out, checkout)
    post = p.inventory(commands["inventory-after"], out, checkout)
    if (
        not p.same(pre, post)
        or not p.same(pre, e["inventory"])
        or p.digest(p.encode(pre)) != e["inventory_sha256"]
    ):
        raise p.CaptureRefusedError("inventory assertions changed")
    prep = e["preparation"]
    p.keys(prep, {"uv_path": str, "uv_sha256": str, "uv_version": str, "prepare": dict})
    if (
        not p.same(prep["prepare"], commands["prepare"])
        or prep["uv_sha256"] != p.runtime_digest("uv", prep["uv_path"])
        or prep["uv_version"] != p.read(out / "uv-version.stdout", root=out).decode().strip()
    ):
        raise p.CaptureRefusedError("preparation assertions changed")
    if (
        "--locked" not in commands["prepare"]["argv"]
        or commands["prepare"]["argv"][0] != prep["uv_path"]
        or commands["prepare"]["cwd"] != str(checkout)
    ):
        raise p.CaptureRefusedError("preparation not owned locked checkout")
    if (
        commands["uv-version"]["argv"] != [prep["uv_path"], "--version"]
        or commands["uv-version"]["cwd"] != str(checkout)
        or commands["clone"]["argv"]
        != [
            "git",
            "--no-replace-objects",
            "clone",
            "--shared",
            "--no-checkout",
            "--quiet",
            "--",
            str(repo),
            str(checkout),
        ]
        or commands["clone"]["cwd"] != str(checkout.parent)
        or commands["checkout"]["argv"]
        != [
            "git",
            "--no-replace-objects",
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            "--quiet",
            "--detach",
            sha,
        ]
        or commands["checkout"]["cwd"] != str(checkout)
    ):
        raise p.CaptureRefusedError("clone/checkout/runtime command changed")
    lock_options = tomllib.loads(p.git(repo, "show", f"{sha}:uv.lock").decode()).get("options", {})
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
        raise p.CaptureRefusedError("preparation argv differs from reviewed locked recipe")
    for label in ("inventory-before", "inventory-after"):
        if commands[label]["argv"] != [
            str(checkout / ".venv/bin/python"),
            "-I",
            "-c",
            p.INVENTORY,
        ] or commands[label]["cwd"] != str(checkout):
            raise p.CaptureRefusedError("inventory command differs from reviewed probe")
    plugin = validate_plugin(p, repo, sha, out, checkout, pre)
    if not p.same(receipt["plugin"], plugin):
        raise p.CaptureRefusedError("async plugin provenance changed")
    run = p.validate_run(out, checkout, test, before, commands["pytest"], check_files=False)
    if not p.same(receipt["coverage"], run):
        raise p.CaptureRefusedError("coverage assertions differ from actual phase/streams")
    expected_scope = {
        "original_observations": observations,
        "operational_validation": "not-performed",
        "historical_claim_truth": "equal" if run["recipe_sha256"] == "sha256:" + decl[0] else "mismatch",
        "current_proof_disposition": "unresolved",
        "status": "observation-only",
    }
    if not p.same(receipt["scope"], expected_scope):
        raise p.CaptureRefusedError("scope/claim assertions changed")
    cleanup = p.load(out / "checkout-cleanup.json")
    p.keys(cleanup, {"path": str, "removed": bool, "pending_pgids": list})
    if (
        not p.same(cleanup, receipt["cleanup"])
        or not cleanup["removed"]
        or cleanup["pending_pgids"]
        or (Path(cleanup["path"]) != checkout.parent)
        or checkout.exists()
    ):
        raise p.CaptureRefusedError("cleanup not proven")
    if not p.same(p.load(out / "manifest.json"), packet.hashes(exclude="manifest.json")):
        raise p.CaptureRefusedError("manifest mismatch")
    p.verify_guard(out)
    result: dict[str, Any] = receipt
    return result
