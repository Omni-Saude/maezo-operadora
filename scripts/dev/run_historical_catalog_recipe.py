#!/usr/bin/env python3
"""Bounded successor source catalogue; private observations, never ledger credit.

The original babe producer/catalogue and its receipts are immutable. This new
orchestrator selects D7 explicitly, invokes the literal producer's capture seam,
and binds both identities in a separate envelope. No result hash/count is assumed.
New catalogue members require reviewed source changes, never record/CLI options.
The legacy capture/readback path remains literal and cannot execute D7 async tests.
Use capture-async/readback-async for the separately pinned explicit async successor.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import signal
import stat
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[2]
LOADER = "scripts/dev/historical_tool_sources.py"
LOADER_SHA256 = "ba40c7ac860fb85016ede71e94cbb9014258130a4b56d882654ea239620df63f"
ENTRYPOINT = "scripts/dev/run_historical_catalog_recipe.py"
ASYNC_PRODUCER = "scripts/dev/historical_async_recipe.py"
ASYNC_SHA256 = "241e0c9a9f636abe50f6765f148d0668c78ea3f26ab90f91725368434f83d4af"


class CatalogueRefusedError(ValueError):
    """Unbound source, tooling, envelope or recipe evidence."""


class SourceEntry(NamedTuple):
    proof_id: str
    source: str
    test: str
    task: str
    date: str
    row_sha256: str
    declared_sha256: str
    files: tuple[tuple[str, str], ...]

    def identity(self) -> tuple[str, str, str, str, str]:
        return self.proof_id, self.source, self.test, self.task, self.date


# Independent successor namespace: do not append to the original babe CATALOG.
SOURCE_CATALOG_V1 = (
    SourceEntry(
        "D7unit802",
        "80206e29ab4bb4f71556b6f119ba9dd777370fe0",
        "tests/unit/gateway/test_engine_capability_contracts.py",
        "PLAN-PORTAL-D7-A-CAPABILITY-CONTRACTS",
        "2026-09-08",
        "d23cc70d8c726081a0a72129c01cf66c214b3df02b2b75006fbd1657071a5f4f",
        "4ba1dd5ad03d07efbb0335e6d11d3a3e8fd0be765c88c4de7a78cc2f6de5a3a8",
        (
            (
                "tests/unit/gateway/test_engine_capability_contracts.py",
                "4ba1dd5ad03d07efbb0335e6d11d3a3e8fd0be765c88c4de7a78cc2f6de5a3a8",
            ),
            ("docs/evidence-ledger.md", "9b57f241e42b27167ff644e4d1a6f1c1b71edcba48fe9636365b53229de1a79a"),
            ("uv.lock", "c3be627ffa9ec3e7448343dbcfda4e7734077a4a8d87a392f222026a2acd7272"),
            ("pyproject.toml", "383d8ba589bac285aa641bb497a1dfbba9aaf770c0e51adbf0a82c1eeafe9e6f"),
            (".python-version", "7b55f8e67b5623c4bef3fa691288da9437d79d3aba156de48d481db32ac7d16d"),
        ),
    ),
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_source(path: Path) -> bytes:
    """Bounded nonfollowing read before importing any loader-selected code."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1024 * 1024:
            raise CatalogueRefusedError("nonregular tooling source")
        data = stream.read(1024 * 1024 + 1)
        fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(current, field) != getattr(before, field)
            for current in (os.fstat(stream.fileno()), os.stat(path, follow_symlinks=False))
            for field in fields
        ):
            raise CatalogueRefusedError("tooling source changed during read")
    return data


def load_producer(repo: Path) -> ModuleType:
    path = ROOT / LOADER
    payload = regular_source(path)
    if digest(payload) != LOADER_SHA256:
        raise CatalogueRefusedError("source loader pin mismatch")
    spec = importlib.util.spec_from_file_location("successor_original_tool_sources", path)
    assert spec is not None
    loader = importlib.util.module_from_spec(spec)
    exec(compile(payload, str(path), "exec"), loader.__dict__)
    capsule = loader.ToolSourceCapsule(repo)
    producer: ModuleType = capsule.load(
        "scripts/dev/run_historical_unit_recipe.py", "successor_literal_babe_producer"
    )
    return producer


def select(proof_id: str) -> SourceEntry:
    matches = [entry for entry in SOURCE_CATALOG_V1 if entry.proof_id == proof_id]
    if len(matches) != 1:
        raise CatalogueRefusedError("source is not uniquely catalogued")
    return matches[0]


def catalogue(entry: SourceEntry) -> dict[str, Any]:
    record = {**entry._asdict(), "files": [list(item) for item in entry.files]}
    return {"schema": "maezo-historical-unit-source-catalogue/v1", "entry": record}


def observations(producer: ModuleType, entry: SourceEntry) -> dict[str, str]:
    return {
        "successor-source-catalogue": digest(producer.encode(catalogue(entry))),
        "successor-producer": digest(regular_source(Path(__file__))),
        "immutable-source-loader": LOADER_SHA256,
    }


def preflight(producer: ModuleType, repo: Path, entry: SourceEntry) -> None:
    """Establish exact source identity before creating an environment or executing it."""
    producer.ancestry(repo, entry.source)
    source = {}
    for relative, expected in entry.files:
        mode = producer.git(repo, "ls-tree", entry.source, "--", relative).split()
        if len(mode) != 4 or mode[0] not in {b"100644", b"100755"} or mode[1] != b"blob":
            raise CatalogueRefusedError("nonregular catalogued Git source")
        data = producer.git(repo, "cat-file", "blob", mode[2].decode())
        if digest(data) != expected:
            raise CatalogueRefusedError("catalogued source pin mismatch")
        source[relative] = data
    rows = [
        row
        for row in source["docs/evidence-ledger.md"].decode().splitlines()
        if row.startswith(f"| {entry.task} | {entry.date} |")
    ]
    if len(rows) != 1 or digest(rows[0].encode()) != entry.row_sha256:
        raise CatalogueRefusedError("catalogued physical row mismatch")
    declarations = re.findall(r"sha256:([0-9a-f]{64}) \(" + re.escape(entry.test) + r"\)", rows[0])
    if declarations != [entry.declared_sha256] or digest(source[entry.test]) != entry.declared_sha256:
        raise CatalogueRefusedError("catalogued declaration/source mismatch")


def envelope(producer: ModuleType, entry: SourceEntry, out: Path) -> dict[str, Any]:
    return {
        "schema": "maezo-historical-unit-successor-capture/v1",
        "catalogue": catalogue(entry),
        "producer": {"path": ENTRYPOINT, "sha256": observations(producer, entry)["successor-producer"]},
        "original_producer_sources": {
            "scripts/dev/run_historical_unit_recipe.py": digest(
                regular_source(Path(producer.__file__ or ""))
            ),
            **producer.PINS,
        },
        "observations": observations(producer, entry),
        "capture": {
            name: digest(producer.Packet(out / "capture").read(name))
            for name in ("manifest.json", "receipt.json")
        },
        "scope": {"ledger_acceptance": False, "independent_review": False, "fresh_current_execution": False},
    }


def validate_receipt(repo: Path, out: Path, proof_id: str) -> dict[str, Any]:
    """Local readback, no archive-only credit or implicit independent approval."""
    producer = load_producer(repo)
    entry = select(proof_id)
    preflight(producer, repo, entry)
    packet = producer.Packet(out)
    inner = producer.Packet(out / "capture")
    expected_names = {"successor.json", "manifest.json"} | {"capture/" + name for name in inner.files}
    if set(packet.files) != expected_names:
        raise CatalogueRefusedError("successor packet has unexpected members")
    if not producer.same(producer.load(out / "successor.json"), envelope(producer, entry, out)):
        raise CatalogueRefusedError("successor identity/capture binding mismatch")
    if not producer.same(producer.load(out / "manifest.json"), packet.hashes(exclude="manifest.json")):
        raise CatalogueRefusedError("successor manifest mismatch")
    receipt: dict[str, Any] = producer.validate_receipt(
        out / "capture", repo, entry.identity(), observations(producer, entry)
    )
    # Full original validation plus membership of every installed distribution in
    # this source's own lock. No fixed result count or recipe equality is asserted.
    lock = tomllib.loads(producer.git(repo, "show", entry.source + ":uv.lock").decode())

    def normalized(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name.lower())

    allowed = {(normalized(item["name"]), item["version"]) for item in lock["package"]}
    seen = set()
    for item in receipt["environment"]["inventory"]["distributions"]:
        name = normalized(item["name"])
        if name in seen or (name, item["version"]) not in allowed:
            raise CatalogueRefusedError("resolved distribution outside own lock")
        seen.add(name)
    if "pytest" not in seen:
        raise CatalogueRefusedError("missing pytest distribution")
    return receipt


def capture_catalogued(repo: Path, out: Path, proof_id: str) -> dict[str, Any]:
    producer = load_producer(repo)
    entry = select(proof_id)
    preflight(producer, repo, entry)
    with producer._directory(out.parent) as parent:
        os.mkdir(out.name, mode=0o700, dir_fd=parent)
    producer.capture_source(
        repo,
        entry.source,
        entry.test,
        out / "capture",
        entry.proof_id,
        entry.task,
        entry.date,
        observations(producer, entry),
    )
    producer.write(out / "successor.json", producer.encode(envelope(producer, entry, out)))
    producer.write(out / "manifest.json", producer.encode(producer.Packet(out).hashes()))
    return validate_receipt(repo, out, proof_id)


def load_async_producer() -> ModuleType:
    """New recipe is pinned separately; original capsule is never modified."""
    path = ROOT / ASYNC_PRODUCER
    payload = regular_source(path)
    if digest(payload) != ASYNC_SHA256:
        raise CatalogueRefusedError("async producer pin mismatch")
    spec = importlib.util.spec_from_file_location("successor_explicit_async_producer", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    exec(compile(payload, str(path), "exec"), module.__dict__)
    return module


def async_observations(producer: ModuleType, entry: SourceEntry) -> dict[str, str]:
    return {**observations(producer, entry), "explicit-async-producer": ASYNC_SHA256}


def async_envelope(producer: ModuleType, entry: SourceEntry, out: Path) -> dict[str, Any]:
    module = load_async_producer()
    return {
        **envelope(producer, entry, out),
        "schema": "maezo-historical-unit-successor-async-capture/v1",
        "inner_producer": {"path": ASYNC_PRODUCER, "sha256": ASYNC_SHA256},
        "inner_schema": module.SCHEMA,
        "recipe": module.RECIPE,
        "observations": async_observations(producer, entry),
    }


def validate_async_receipt(repo: Path, out: Path, proof_id: str) -> dict[str, Any]:
    """Validate the actual explicit async producer, never normalize it to babe/v1."""
    producer = load_producer(repo)
    module = load_async_producer()
    entry = select(proof_id)
    preflight(producer, repo, entry)
    packet = producer.Packet(out)
    inner = producer.Packet(out / "capture")
    expected = {"successor.json", "manifest.json"} | {"capture/" + name for name in inner.files}
    if set(packet.files) != expected or set(packet.entries) != expected | {"capture", "capture/guard"}:
        raise CatalogueRefusedError("async successor packet has unexpected members")
    if not producer.same(producer.load(out / "successor.json"), async_envelope(producer, entry, out)):
        raise CatalogueRefusedError("async successor identity/capture binding mismatch")
    if not producer.same(producer.load(out / "manifest.json"), packet.hashes(exclude="manifest.json")):
        raise CatalogueRefusedError("async successor manifest mismatch")
    receipt: dict[str, Any] = module.validate_receipt(
        producer, out / "capture", repo, entry.identity(), async_observations(producer, entry)
    )
    return receipt


def capture_async_catalogued(repo: Path, out: Path, proof_id: str) -> dict[str, Any]:
    producer = load_producer(repo)
    module = load_async_producer()
    entry = select(proof_id)
    preflight(producer, repo, entry)
    with producer._directory(out.parent) as parent:
        os.mkdir(out.name, mode=0o700, dir_fd=parent)
    module.capture_source(
        producer,
        repo,
        entry.source,
        entry.test,
        out / "capture",
        entry.proof_id,
        entry.task,
        entry.date,
        async_observations(producer, entry),
    )
    producer.write(out / "successor.json", producer.encode(async_envelope(producer, entry, out)))
    producer.write(out / "manifest.json", producer.encode(producer.Packet(out).hashes()))
    return validate_async_receipt(repo, out, proof_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("preflight", "capture", "readback", "preflight-async", "capture-async", "readback-async"),
    )
    parser.add_argument("proof", choices=[entry.proof_id for entry in SOURCE_CATALOG_V1])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()

    def interrupted(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    if args.mode in {"preflight", "preflight-async"}:
        if args.mode == "preflight-async":
            load_async_producer()
        preflight(load_producer(repo), repo, select(args.proof))
        print(json.dumps(catalogue(select(args.proof)), sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output required for capture/readback")
    operation = {
        "capture": capture_catalogued,
        "readback": validate_receipt,
        "capture-async": capture_async_catalogued,
        "readback-async": validate_async_receipt,
    }[args.mode]
    receipt = operation(repo, args.output.absolute(), args.proof)
    print(json.dumps({"scope": "observation-only", "coverage": receipt["coverage"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
