"""Inspect explicit native-v2 qualification artifacts; never install or admit them.

Classpath roots must be the complete installed common classloader roots. A JAR
and an unpacked classes directory carrying the same resource is a refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

RESOURCE = "engine-schemas-v2.json"
LIMIT = 1048576


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            raise ValueError("duplicate_registry_member")
        out[key] = value
    return out


def registry(data: bytes, *, qualification: bool) -> dict[str, Any]:
    if len(data) > LIMIT:
        raise ValueError("oversized_registry")
    value = json.loads(data, object_pairs_hook=_pairs)
    if (
        type(value) is not dict
        or set(value) != {"protocol", "schemas"}
        or value["protocol"] != "maezo.engine-schemas.v2"
        or type(value["schemas"]) is not list
        or bool(value["schemas"]) != qualification
    ):
        raise ValueError("wrong_registry_profile")
    if qualification:
        names: set[str] = set()
        for row in value["schemas"]:
            if (
                type(row) is not dict
                or type(row.get("schema_id")) is not str
                or not row["schema_id"].startswith("qualification.native-v2.")
                or row["schema_id"] in names
                or not str(row.get("process_key", "")).startswith("MAEZO-NATIVE-V2-QUALIFICATION-")
                or not str(row.get("topic", "")).startswith("qualification.native-v2.")
                or row.get("workload") != "native-v2-qualification-worker"
                or row.get("fields") != []
                or row.get("read_projection") != []
            ):
                raise ValueError("nonqualification_schema")
            names.add(row["schema_id"])
    return value


def _resource(path: Path) -> bytes | None:
    if path.is_symlink() or not path.exists():
        raise ValueError("invalid_classpath_root")
    if path.is_dir():
        resource = path / RESOURCE
        if resource.is_symlink():
            raise ValueError("symlink_resource")
        if not resource.exists():
            return None
        if not resource.is_file() or resource.stat().st_size > LIMIT:
            raise ValueError("invalid_resource")
        return resource.read_bytes()
    with zipfile.ZipFile(path) as jar:
        entries = [entry for entry in jar.infolist() if entry.filename == RESOURCE]
        if len(entries) > 1:
            raise ValueError("duplicate_resource_in_jar")
        if not entries:
            return None
        if entries[0].file_size > LIMIT:
            raise ValueError("oversized_registry")
        return jar.read(entries[0])


def inspect(classpath: list[Path], *, qualification: bool, expected_registry_sha256: str) -> dict[str, Any]:
    """Return public technical evidence, never claims about DB/runtime authority."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_registry_sha256):
        raise ValueError("explicit_registry_sha256_required")
    roots = [path.resolve() for path in classpath]
    if not roots or len(set(roots)) != len(roots):
        raise ValueError("duplicate_or_empty_classpath")
    found = [(path, data) for path in classpath if (data := _resource(path)) is not None]
    if len(found) != 1:
        raise ValueError("registry_classpath_must_have_exactly_one_resource")
    path, data = found[0]
    if _sha(data) != expected_registry_sha256:
        raise ValueError("registry_digest_mismatch")
    value = registry(data, qualification=qualification)
    return {
        "protocol": "maezo.native-v2-artifact-inspection.v1",
        "qualification_only": qualification,
        "registry_sha256": _sha(data),
        "schema_count": len(value["schemas"]),
        "resource_count": 1,
        "resource_owner": str(path.resolve()),
        "runtime_admission_verified": False,
    }


def compare_classes(default_jar: Path, qualification_jar: Path) -> int:
    """Same production bytecode is necessary; it does not establish runtime acceptance."""

    def classes(path: Path) -> dict[str, str]:
        with zipfile.ZipFile(path) as jar:
            entries = [entry for entry in jar.infolist() if entry.filename.endswith(".class")]
            names = [entry.filename for entry in entries]
            if not entries or len(names) != len(set(names)):
                raise ValueError("missing_or_duplicate_classes")
            return {entry.filename: _sha(jar.read(entry)) for entry in entries}

    normal, qualification = classes(default_jar), classes(qualification_jar)
    if normal != qualification:
        raise ValueError("qualification_changed_production_classes")
    return len(normal)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=["default", "qualification"])
    parser.add_argument("--registry-sha256", required=True)
    parser.add_argument("--classpath", nargs="+", required=True, type=Path)
    parser.add_argument("--default-jar", type=Path)
    parser.add_argument("--qualification-jar", type=Path)
    args = parser.parse_args()
    result = inspect(
        args.classpath,
        qualification=args.mode == "qualification",
        expected_registry_sha256=args.registry_sha256,
    )
    if bool(args.default_jar) != bool(args.qualification_jar):
        parser.error("class comparison requires both JARs")
    if args.default_jar:
        result["identical_class_count"] = compare_classes(args.default_jar, args.qualification_jar)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
