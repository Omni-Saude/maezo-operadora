"""Capture exact PR356 ECS source custody from an explicit clean checkout into a fresh file."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any

from scripts.dev.verification_consumer_paths import (
    VerificationRefusedError,
    git,
    validate_inputs,
    verify_source,
    write_json_exclusive,
)

HEAD = "f38ec6ad2639ad288611dae240e227057754f93d"
EXPECTED_FILES = 1363


def capture(source: Path) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    listing = git(source, "ls-tree", "-rz", "--full-tree", "HEAD").decode().split("\0")
    for item in listing:
        if not item:
            continue
        metadata, name = item.split("\t", 1)
        mode, kind, blob = metadata.split()
        path = source / name
        raw = path.read_bytes()
        info = path.lstat()
        calculated_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if kind != "blob" or not stat.S_ISREG(info.st_mode) or calculated_blob != blob:
            raise AssertionError(f"source member differs from Git: {name}")
        rows[name] = {
            "git_blob": blob,
            "mode": mode,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if len(rows) != EXPECTED_FILES:
        raise AssertionError("tracked source membership changed")
    return {"head": HEAD, "tree": git(source, "rev-parse", "HEAD^{tree}").decode().strip(), "files": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", choices=("source-before", "source-after"), required=True)
    parser.add_argument("--before", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = validate_inputs(
            source=args.source, evidence=args.evidence, output=args.output, expected_head=HEAD
        )
        if args.label == "source-before" and args.before is not None:
            raise VerificationRefusedError("source-before does not accept --before")
        if args.label == "source-after" and args.before is None:
            raise VerificationRefusedError("source-after requires --before")
        value = capture(inputs.source)
        if args.before is not None:
            before_path = args.before.expanduser().resolve(strict=True)
            if not before_path.is_file() or before_path.is_symlink():
                raise VerificationRefusedError("before capture is unsafe")
            before = json.loads(before_path.read_text())
            if before.get("head") != HEAD or before.get("files") != value["files"]:
                raise AssertionError("source changed between custody captures")
        verify_source(inputs.source, HEAD)
        value.update(label=args.label, evidence=str(inputs.evidence))
        write_json_exclusive(inputs.output, value)
    except (AssertionError, OSError, ValueError, VerificationRefusedError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"head": HEAD, "tree": value["tree"], "files": len(value["files"]), "status": "CLEAN_EXACT_BLOBS"}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
