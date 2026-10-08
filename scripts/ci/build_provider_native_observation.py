#!/usr/bin/env python3
"""TestOnly C2 assembly from real, already compiled/extracted inputs.

No compilation, Docker, engine, SQL, authority or receipt is performed here. ROOT
owns those effects and must prove the origin of every staged file. A successful
pure build proves byte correspondence only, never a CIB runtime qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import struct
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

PHASES = ("phase-a", "phase-b")
APPS = (
    "ROOT",
    "camunda",
    "docs",
    "engine-rest",
    "examples",
    "host-manager",
    "maezo-human",
    "manager",
    "webapp",
)
OWN = frozenset(("lib/maezo-human-command.jar", "webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar"))
OWNERS = ("br/com/maezo/human/ProviderAuthTestOwner", "br/com/maezo/human/ProviderAuthTestComposition")
MEASUREMENT_OWNER = "br/com/maezo/workload/ProviderNativeIndependentMeasurement"
MEASUREMENT_OWNERS = (*OWNERS, MEASUREMENT_OWNER)
MEASUREMENT_SCHEMA = "provider-native-independent-measurement-schema-v1.json"
MEASUREMENT_SCHEMA_SHA = "d55733ba7703f39c7c71f13da0eb20f14825cbff03c1106894aae610423cebfe"
MEASUREMENT_INPUT_SCHEMA = "provider-native-independent-measurement-build-inputs.v1"
MEASUREMENT_CLASSES = frozenset(
    (
        *(owner + ".class" for owner in OWNERS),
        MEASUREMENT_OWNER + ".class",
        *(
            MEASUREMENT_OWNER + "$" + name + ".class"
            for name in (
                "Budget",
                "Close",
                "Codec",
                "FinalOutcome",
                "Measurement",
                "SocketIdentity",
                "Stage",
                "State",
            )
        ),
    )
)
MEASUREMENT_SOURCES = frozenset(
    (
        *("src/test/java/" + owner + ".java" for owner in MEASUREMENT_OWNERS),
        "src/test/java/br/com/maezo/workload/ProviderNativeIndependentMeasurementTest.java",
        "src/test/java/br/com/maezo/workload/NativeMeasurementProtocolEngineIT.java",
    )
)
DESCRIPTORS = frozenset(
    (
        "conf/context.xml",
        "conf/web.xml",
        "conf/bpm-platform.xml",
        "conf/server.xml",
        *(f"webapps/{app}/WEB-INF/web.xml" for app in APPS),
    )
)
VENDOR_RESOURCES = tuple(f"provider-native/{phase}/secured-startup-vendor.sha256" for phase in PHASES)
DESCRIPTOR_RESOURCES = tuple(
    f"provider-native/{phase}/secured-startup-descriptors.sha256" for phase in PHASES
)
LIMIT = 256 * 1024 * 1024
DEFAULT_RESOURCES = ("secured-startup-vendor.sha256", "secured-startup-descriptors.sha256")
JAVA = Path(__file__).resolve().parents[2] / "src/maezo/portal/engine/java"


class NativeObservationBuildError(ValueError):
    """An input is absent, ambiguous, changed or outside the closed assembly."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise NativeObservationBuildError(message)


def canonical(path: Path, *, directory: bool) -> Path:
    require(
        path.is_absolute() and path == path.absolute() and path.resolve() == path,
        "Canonical absolute path required",
    )
    for part in (path, *path.parents):
        require(not part.is_symlink(), "Symlink input refused")
    mode = path.stat().st_mode
    require(stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode), "Input type differs")
    return path


def read(path: Path) -> bytes:
    canonical(path, directory=False)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(before.st_nlink == 1 and before.st_size <= LIMIT, "Input size/link refused")
        raw = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())

    def signature(s: os.stat_result) -> tuple[int, int, int, int, int]:
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    require(
        signature(before) == signature(after) == signature(path.stat(follow_symlinks=False)),
        "Input changed while reading",
    )
    require(len(raw) == before.st_size, "Input incomplete")
    return raw


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def tree(root: Path) -> dict[str, str]:
    canonical(root, directory=True)
    result: dict[str, str] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            require(not (Path(current) / name).is_symlink(), "Symlink tree refused")
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            require(
                bool(re.fullmatch(r"[A-Za-z0-9_./$@-]+", relative)) and ".." not in relative,
                "Inventory path refused",
            )
            result[relative] = sha(read(path))
    require(bool(result), "Empty input tree")
    return dict(sorted(result.items()))


def inventory(values: dict[str, str]) -> bytes:
    require(bool(values), "Empty inventory")
    return "".join(f"{digest} {name}\n" for name, digest in sorted(values.items())).encode("ascii")


def parse_inventory(raw: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    require(raw.endswith(b"\n") and len(raw) <= 1024 * 1024, "Inventory framing/size refused")
    for line in raw.decode("ascii").splitlines():
        require(
            bool(re.fullmatch(r"[a-f0-9]{64} [A-Za-z0-9_./$@-]+", line)) and ".." not in line,
            "Inventory line refused",
        )
        name = line[65:]
        require(name not in result and not name.startswith("/"), "Inventory duplicate/absolute path")
        result[name] = line[:64]
    require(raw == inventory(result), "Inventory must be canonical sorted LF")
    return result


def fresh_directory(path: Path) -> None:
    require(path.is_absolute() and path.resolve(strict=False) == path, "Canonical absolute output required")
    for parent in path.parents:
        require(not parent.is_symlink(), "Output parent symlink refused")
        if parent.exists():
            require(parent.is_dir(), "Output parent directory required")
    require(not path.exists() and not path.is_symlink(), "Fresh output directory required")


def write_new(path: Path, raw: bytes) -> None:
    require(not path.exists() and not path.is_symlink(), "Output already exists; preserve it")
    require(path.is_absolute() and path.resolve(strict=False) == path, "Canonical absolute output required")
    for parent in path.parents:
        require(not parent.is_symlink(), "Output parent symlink refused")
    path.parent.mkdir(parents=True, exist_ok=True)
    for part in (path.parent, *path.parent.parents):
        require(not part.is_symlink(), "Output parent symlink refused")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def json_object(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise NativeObservationBuildError("Manifest object required")
    return value


def load(path: Path) -> dict[str, Any]:
    raw = read(path)
    value = json_object(raw)
    require(raw == json_bytes(value), "Canonical build manifest required")
    return value


def support_entries(test_classes: dict[str, str]) -> dict[str, str]:
    entries = {
        name: digest
        for name, digest in test_classes.items()
        if any(
            name == owner + ".class" or name.startswith(owner + "$") and name.endswith(".class")
            for owner in OWNERS
        )
    }
    require(
        all(owner + ".class" in entries for owner in OWNERS), "Both actual TestOnly owner classes required"
    )
    return dict(sorted(entries.items()))


def class_metadata(raw: bytes) -> dict[str, Any]:
    """Read structural identity without loading or executing supplied bytecode."""
    cursor = 0

    def take(size: int) -> bytes:
        nonlocal cursor
        require(size >= 0 and cursor + size <= len(raw), "Truncated class metadata")
        value = raw[cursor : cursor + size]
        cursor += size
        return value

    def number(size: int) -> int:
        return int.from_bytes(take(size), "big")

    require(take(4) == b"\xca\xfe\xba\xbe", "Actual classfile required")
    require(number(2) == 0 and number(2) == 61, "Class release must be Java 17 without preview")
    pool: dict[int, tuple[int, Any]] = {}
    count = number(2)
    index = 1
    while index < count:
        tag = number(1)
        if tag == 1:
            pool[index] = (tag, take(number(2)))
        elif tag in (7, 8, 16, 19, 20):
            pool[index] = (tag, number(2))
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            take(4)
        elif tag in (5, 6):
            take(8)
            index += 1
        elif tag == 15:
            take(3)
        else:
            raise NativeObservationBuildError("Class constant pool tag refused")
        index += 1

    def utf(index: int) -> str:
        item = pool.get(index)
        require(item is not None and item[0] == 1, "Class UTF identity missing")
        assert item is not None
        raw_name = item[1]
        require(isinstance(raw_name, bytes), "Class UTF bytes required")
        assert isinstance(raw_name, bytes)
        try:
            return raw_name.decode("ascii")
        except UnicodeDecodeError as error:
            raise NativeObservationBuildError("Class identity must be ASCII") from error

    def typename(index: int) -> str:
        item = pool.get(index)
        require(item is not None and item[0] == 7, "Class type identity missing")
        assert item is not None
        return utf(item[1])

    def attributes() -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for _ in range(number(2)):
            name = utf(number(2))
            require(name not in result, "Duplicate class attribute")
            result[name] = take(number(4))
        return result

    access = number(2)
    name = typename(number(2))
    parent = typename(number(2))
    interfaces = [typename(number(2)) for _ in range(number(2))]
    fields = []
    for _ in range(number(2)):
        fields.append((number(2), utf(number(2)), utf(number(2))))
        attributes()
    constructors = []
    for _ in range(number(2)):
        flags, method, descriptor = number(2), utf(number(2)), utf(number(2))
        if method == "<init>":
            constructors.append((flags, descriptor))
        attributes()
    attrs = attributes()
    require(cursor == len(raw), "Trailing class metadata")
    source = attrs.get("SourceFile", b"")
    require(len(source) == 2, "Class source identity missing")
    return {
        "name": name,
        "access": access,
        "parent": parent,
        "interfaces": interfaces,
        "constructors": constructors,
        "fields": fields,
        "source": utf(struct.unpack(">H", source)[0]),
    }


def measurement_entries(test_classes: dict[str, str], expected: dict[str, str]) -> dict[str, str]:
    """ROOT's exact measured list is the authority for bytes, never a prefix wildcard."""
    require(
        isinstance(expected, dict) and set(expected) == MEASUREMENT_CLASSES,
        "Closed measurement class map required",
    )
    require(all(owner + ".class" in expected for owner in MEASUREMENT_OWNERS), "Three actual owners required")
    for name, digest in expected.items():
        require(isinstance(name, str) and isinstance(digest, str), "Measurement class pin type differs")
        require(re.fullmatch(r"[a-f0-9]{64}", digest) is not None, "Measurement class digest refused")
        require(
            logical_member(name) == name
            and name.endswith(".class")
            and name[:-6].split("$", 1)[0] in MEASUREMENT_OWNERS,
            "Measurement class outside exact owners",
        )
    actual = {}
    for name, digest in test_classes.items():
        logical = logical_member(name)
        while logical.startswith("META-INF/versions/"):
            logical = logical_member(logical)
        if logical.endswith(".class") and logical[:-6].split("$", 1)[0] in MEASUREMENT_OWNERS:
            actual[name] = digest
    require(actual == expected, "Closed measurement class census differs")
    return dict(sorted(expected.items()))


def pinned_record(reference: Any, label: str) -> dict[str, Any]:
    require(
        isinstance(reference, dict) and set(reference) == {"path", "sha256"}, label + " reference differs"
    )
    require(
        isinstance(reference["path"], str) and isinstance(reference["sha256"], str),
        label + " pin type differs",
    )
    require(re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]) is not None, label + " hash refused")
    raw = read(Path(reference["path"]))
    require(sha(raw) == reference["sha256"], label + " drift")
    return json_object(raw)


def root_compilation(
    reference: Any, tests: dict[str, str], main: dict[str, str], selected: Any
) -> dict[str, Any]:
    """Read ROOT custody; actual command execution/source gates remain external."""
    record = pinned_record(reference, "ROOT compilation")
    require(
        set(record)
        == {
            "schema",
            "scope",
            "candidate_sha",
            "source_files",
            "main_compilation",
            "test_compilation",
            "support_classes",
            "compilation_commands",
            "compile_counts",
            "compiler",
            "dependency_jars",
        },
        "ROOT compilation keys differ",
    )
    require(
        record["schema"] == "provider-native-independent-measurement-root-compilation.v1",
        "ROOT compilation schema differs",
    )
    require(
        record["scope"] == "ROOT_SINGLE_COMPILATION_CUSTODY_NOT_RUNTIME", "ROOT compilation scope differs"
    )
    require(
        isinstance(record["candidate_sha"], str)
        and re.fullmatch(r"[a-f0-9]{40}", record["candidate_sha"]) is not None,
        "ROOT candidate SHA differs",
    )
    counts = record["compile_counts"]
    require(
        type(counts) is dict
        and set(counts) == {"main", "test"}
        and all(type(value) is int and value == 1 for value in counts.values()),
        "ROOT single compilation required",
    )
    commands = record["compilation_commands"]
    require(
        isinstance(commands, dict) and set(commands) == {"main", "test"}, "ROOT compilation commands differ"
    )
    require(
        all(isinstance(command, str) and 1 <= len(command) <= 16384 for command in commands.values()),
        "ROOT actual commands required",
    )
    compiler = record["compiler"]
    require(
        isinstance(compiler, dict) and set(compiler) == {"release", "version", "executable"},
        "ROOT compiler keys differ",
    )
    require(
        type(compiler["release"]) is int
        and compiler["release"] == 17
        and isinstance(compiler["version"], str)
        and 1 <= len(compiler["version"]) <= 256,
        "ROOT compiler release/version differs",
    )
    executable = compiler["executable"]
    require(
        isinstance(executable, dict) and set(executable) == {"path", "sha256"},
        "ROOT compiler executable differs",
    )
    require(sha(read(Path(executable["path"]))) == executable["sha256"], "ROOT compiler executable drift")
    dependencies = record["dependency_jars"]
    require(
        isinstance(dependencies, dict) and 1 <= len(dependencies) <= 256, "ROOT dependency census required"
    )
    for name, digest in dependencies.items():
        require(
            isinstance(name, str) and name.endswith(".jar") and sha(read(Path(name))) == digest,
            "ROOT dependency drift",
        )
    actual_sources = {
        "src/maezo/portal/engine/java/pom.xml": sha(read(JAVA / "pom.xml")),
        **{
            "src/maezo/portal/engine/java/src/main/" + name: digest
            for name, digest in tree(JAVA / "src/main").items()
        },
        **{
            "src/maezo/portal/engine/java/src/test/" + name: digest
            for name, digest in tree(JAVA / "src/test").items()
        },
    }
    require(record["source_files"] == actual_sources, "ROOT complete composed source census differs")
    require(
        record["main_compilation"] == main and record["test_compilation"] == tests,
        "ROOT compiled snapshot differs",
    )
    require(record["support_classes"] == selected, "ROOT compiled support map differs")
    return record


def measurement_roots(spec: dict[str, Any]) -> tuple[Path, ...]:
    record = pinned_record(spec["root_compile_freeze"], "ROOT compilation")
    return (
        Path(spec["source_freeze"]["path"]),
        Path(spec["root_compile_freeze"]["path"]),
        JAVA / "src/main",
        JAVA / "src/test",
        Path(record["compiler"]["executable"]["path"]),
        *(Path(name) for name in record["dependency_jars"]),
    )


def measurement_inputs(path: Path, tests: dict[str, str], main: dict[str, str]) -> dict[str, Any]:
    value = load(path)
    require(
        set(value)
        == {
            "schema",
            "source_freeze",
            "root_compile_freeze",
            "protocol_schema",
            "sources",
            "support_classes",
        },
        "Measurement input keys differ",
    )
    require(value["schema"] == MEASUREMENT_INPUT_SCHEMA, "Measurement input schema differs")
    require(
        value["protocol_schema"] == {"name": MEASUREMENT_SCHEMA, "sha256": MEASUREMENT_SCHEMA_SHA},
        "Measurement protocol schema pin differs",
    )
    root_compilation(value["root_compile_freeze"], tests, main, value["support_classes"])
    frozen = pinned_record(value["source_freeze"], "Measurement source freeze")
    require(
        frozen.get("schema") == "provider-independent-instrument-source-freeze.v1",
        "Instrument freeze schema differs",
    )
    sources = value["sources"]
    require(
        isinstance(sources, dict) and set(sources) == MEASUREMENT_SOURCES,
        "Closed measurement source census differs",
    )
    instrument_sources = {
        "src/maezo/portal/engine/java/" + name: digest
        for name, digest in sources.items()
        if name not in {"src/test/java/" + owner + ".java" for owner in OWNERS}
    }
    require(frozen.get("source_hashes") == instrument_sources, "Instrument freeze/source map differs")
    require(
        frozen.get("input_hashes", {}).get(
            next(
                (
                    name
                    for name in frozen.get("input_hashes", {})
                    if name.endswith(
                        "/pw1-d-native-independent-measurement-terminal-third-repair/schema.json"
                    )
                ),
                "",
            )
        )
        == MEASUREMENT_SCHEMA_SHA,
        "Instrument freeze schema pin differs",
    )
    for name, digest in sources.items():
        require(sha(read(JAVA / name)) == digest, "Measurement source drift")
    require(
        sha(read(JAVA / "src/main/resources" / MEASUREMENT_SCHEMA)) == MEASUREMENT_SCHEMA_SHA
        and main.get(MEASUREMENT_SCHEMA) == MEASUREMENT_SCHEMA_SHA,
        "Compiled measurement schema/source differs",
    )
    measurement_entries(tests, value["support_classes"])
    return value


def measurement_members(members: dict[str, bytes]) -> None:
    for name, raw in members.items():
        metadata = class_metadata(raw)
        owner = name[:-6].split("$", 1)[0]
        require(metadata["name"] == name[:-6], "Class package/name differs from census")
        require(metadata["source"] == owner.rsplit("/", 1)[1] + ".java", "Class source differs from owner")
        if name == MEASUREMENT_OWNER + ".class":
            require(
                metadata["access"] & 0x0011 == 0x0011
                and not metadata["access"] & (0x0200 | 0x0400 | 0x2000 | 0x4000)
                and metadata["parent"] == "java/lang/Object"
                and metadata["interfaces"] == ["br/com/maezo/workload/NativeMeasurementPort"],
                "Instrument must be public final with exact port and Object superclass",
            )
            require(
                [descriptor for flags, descriptor in metadata["constructors"] if flags & 1]
                == ["(Lbr/com/maezo/workload/NativeMeasurementConfiguration;)V"],
                "Instrument public constructor differs",
            )
            require(
                all(not flags & 1 or flags & 16 for flags, _, _ in metadata["fields"]),
                "Mutable public instrument field",
            )


def measurement_resource_census(artifacts: dict[str, dict[str, bytes]], origin: str | None) -> None:
    observed = []
    for owner, entries in artifacts.items():
        for name, raw in entries.items():
            logical = logical_member(name)
            while logical.startswith("META-INF/versions/"):
                logical = logical_member(logical)
            if logical == MEASUREMENT_SCHEMA:
                observed.append((owner, name, sha(raw)))
    require(
        observed == ([] if origin is None else [(origin, MEASUREMENT_SCHEMA, MEASUREMENT_SCHEMA_SHA)]),
        "Measurement schema logical origin/bytes differ",
    )


def zip_entries(path: Path) -> dict[str, bytes]:
    return zip_bytes(read(path))


def zip_bytes(raw: bytes) -> dict[str, bytes]:
    import io

    result: dict[str, bytes] = {}
    seen: set[str] = set()
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as error:
        raise NativeObservationBuildError("Invalid JAR format") from error
    with archive as jar:
        for entry in jar.infolist():
            require(entry.filename not in seen, "Duplicate JAR entry")
            seen.add(entry.filename)
            require(
                not entry.filename.startswith("/") and ".." not in entry.filename and not entry.flag_bits & 1,
                "JAR path/encryption refused",
            )
            if entry.is_dir():
                continue
            require(
                entry.file_size <= LIMIT and not stat.S_ISLNK(entry.external_attr >> 16),
                "JAR member size/type refused",
            )
            result[entry.filename] = jar.read(entry)
    return result


def disjoint_outputs(outputs: tuple[Path, ...], inputs: tuple[Path, ...]) -> None:
    """Reject all aliases/ancestor relationships before any stage write."""
    for path in (*outputs, *inputs):
        require(
            not any(part.is_symlink() for part in (path, *path.parents)),
            "Output parent symlink refused" if path in outputs else "Boundary symlink refused",
        )
        require(path.is_absolute() and path.resolve(strict=False) == path, "Canonical boundary required")
    for index, output in enumerate(outputs):
        for other in (*inputs, *outputs[index + 1 :]):
            require(
                not output.is_relative_to(other) and not other.is_relative_to(output),
                "Input/output boundaries overlap",
            )


def support_roots(support: dict[str, Any]) -> tuple[Path, ...]:
    roots = tuple(Path(support[name]) for name in ("classes", "test_classes", "fixtures", "support_jar"))
    if support.get("shade") is not None:
        shade = support["shade"]
        roots += (Path(shade["reference"]), *(Path(item["path"]) for item in shade["dependencies"]))
    if support.get("measurement") is not None:
        measurement = support["measurement"]
        roots += (Path(measurement["inputs"]), *measurement_roots(measurement["spec"]))
    return roots + (JAVA / "pom.xml", JAVA / "src/main/resources")


def logical_member(name: str) -> str:
    require(
        "\\" not in name and all(part not in ("", ".", "..") for part in name.split("/")),
        "Noncanonical JAR path",
    )
    if name.startswith("META-INF/versions/"):
        parts = name.split("/", 3)
        require(len(parts) == 4 and parts[2].isdigit() and int(parts[2]) >= 9, "Multi-release origin refused")
        return parts[3]
    return name


def phase_resource_census(
    artifacts: dict[str, dict[str, bytes]], expected: dict[str, tuple[str, bytes]]
) -> None:
    """Four resources, logical names and exact literal bytes; never class filtering.

    Before inventory generation, expected is empty and no phase resource may
    appear in shade references/dependencies. At finalize, each has exactly one
    literal origin in its prescribed owner. Versioned/path aliases or copies in
    another artifact cannot be admitted by preserving the correct literal too.
    """
    resources = (*VENDOR_RESOURCES, *DESCRIPTOR_RESOURCES)
    require(set(expected) <= set(resources), "Unknown phase resource expectation")
    origins: dict[str, list[tuple[str, str, bytes]]] = {name: [] for name in resources}
    for origin, entries in artifacts.items():
        for member, raw in entries.items():
            logical = logical_member(member)
            # Normalize nested version prefixes only for this finite resource
            # census. Do not change legitimate measured MR class accounting.
            while logical.startswith("META-INF/versions/"):
                logical = logical_member(logical)
            if logical in origins:
                origins[logical].append((origin, member, raw))
    for resource in resources:
        wanted = [(expected[resource][0], resource, expected[resource][1])] if resource in expected else []
        require(origins[resource] == wanted, "Phase security resource logical origin/bytes differ")


def class_map(entries: dict[str, bytes]) -> dict[str, str]:
    return {name: sha(raw) for name, raw in entries.items() if logical_member(name).endswith(".class")}


def spi_map(compilation: dict[str, str]) -> dict[str, str]:
    return {
        name: digest
        for name, digest in compilation.items()
        if name == "br/com/maezo/workload/CertificateAuthenticationProvider.class"
        or name.startswith("br/com/maezo/workload/SecuredFetchAndLockContextListener")
        and name.endswith(".class")
    }


def default_resource_pins(compilation: dict[str, str]) -> dict[str, str]:
    values = {name: sha(read(JAVA / "src/main/resources" / name)) for name in DEFAULT_RESOURCES}
    require(
        all(compilation.get(name) == digest for name, digest in values.items()),
        "Compiled default resource/source differs",
    )
    return values


def require_multi_release_manifest(entries: dict[str, bytes]) -> None:
    """Versioned class paths require the JVM's actual MR loader opt-in."""
    if not any(name.startswith("META-INF/versions/") and name.endswith(".class") for name in entries):
        return
    raw = entries.get("META-INF/MANIFEST.MF", b"")
    # JDK Manifest.FastInputStream accepts only CRLF/LF/CR as line endings.
    # JarFile's Attributes.read uses a 512-byte buffer including the terminator.
    # Validate each raw header BEFORE unfolding: only the value can continue.
    physical = re.split(rb"(\r\n|\n|\r)", raw)
    attributes: dict[bytes, bytes] = {}
    previous: bytes | None = None
    for index in range(0, len(physical), 2):
        line = physical[index]
        if not line:
            break  # Only main attributes count, even if later sections are present.
        require(
            index + 1 < len(physical) and len(line) + len(physical[index + 1]) <= 512,
            "Multi-Release manifest invalid",
        )
        if line.startswith(b" "):
            require(previous is not None, "Multi-Release manifest invalid")
            assert previous is not None
            attributes[previous] += line[1:]
            continue
        name, separator, value = line.partition(b": ")
        # Attributes.Name permits only these 1..70 ASCII characters. No strip,
        # Unicode folding or value normalization may turn a JVM refusal into MR.
        require(
            bool(separator) and re.fullmatch(rb"[A-Za-z0-9_-]{1,70}", name) is not None,
            "Multi-Release manifest invalid",
        )
        previous = name.lower()
        require(previous not in attributes, "Multi-Release manifest invalid")
        attributes[previous] = value
    require(attributes.get(b"multi-release", b"").lower() == b"true", "Multi-Release manifest required")


def versioned_this_class(raw: bytes) -> str:
    """Declared class name of a versioned entry without loading bytecode.

    Any release is accepted: versioned classes may target majors older than
    the main compilation, so unlike the census no release is asserted here.
    """
    cursor = 0

    def take(size: int) -> bytes:
        nonlocal cursor
        require(size >= 0 and cursor + size <= len(raw), "Truncated versioned class")
        value = raw[cursor : cursor + size]
        cursor += size
        return value

    def number(size: int) -> int:
        return int.from_bytes(take(size), "big")

    require(take(4) == b"\xca\xfe\xba\xbe", "Actual classfile required")
    number(2)  # Minor and major name a release, never the loader opt-in gate.
    number(2)
    pool: dict[int, tuple[int, Any]] = {}
    count = number(2)
    index = 1
    while index < count:
        tag = number(1)
        if tag == 1:
            pool[index] = (tag, take(number(2)))
        elif tag in (7, 8, 16, 19, 20):
            pool[index] = (tag, number(2))
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            take(4)
        elif tag in (5, 6):
            take(8)
            index += 1
        elif tag == 15:
            take(3)
        else:
            raise NativeObservationBuildError("Class constant pool tag refused")
        index += 1
    number(2)  # access_flags never name the class.
    item = pool.get(number(2))
    require(item is not None and item[0] == 7, "Class type identity missing")
    utf8 = pool.get(item[1])
    require(
        utf8 is not None and utf8[0] == 1 and isinstance(utf8[1], bytes),
        "Class UTF identity missing",
    )
    try:
        return utf8[1].decode("ascii")
    except UnicodeDecodeError as error:
        raise NativeObservationBuildError("Class identity must be ASCII") from error


def require_multi_release_entry_classes(entries: dict[str, bytes]) -> None:
    """A versioned entry must declare the class its relocated path names.

    Shade rewrites constant-pool internal names on every relocation, but only
    the rawString path relocation moves the versioned entry itself. A path
    that disagrees with the declared class selects nothing on the MR loader
    path. Module descriptors keep their original name by design and are
    accounted by the dependency census instead.
    """
    for name, raw in entries.items():
        if not name.startswith("META-INF/versions/") or not name.endswith(".class"):
            continue
        parts = name.split("/", 3)
        require(len(parts) == 4 and parts[2].isdigit(), "Versioned entry path invalid")
        if parts[3] == "module-info.class":
            continue
        require(
            parts[3][:-6] == versioned_this_class(raw),
            "Versioned entry path differs from declared class",
        )


def shade_snapshot(
    compilation: dict[str, str], reference: Path, dependencies: tuple[Path, ...]
) -> dict[str, Any]:
    """Measured post-shade bytes; ROOT must separately prove the actual Maven step.

    Shade rewrites own constant pools too. Matching pre-shade bytes is therefore
    invalid. Dependency entry names are derived from the immutable POM mapping;
    no extra own class is admitted merely by a namespace prefix.
    """
    require(
        1 <= len(dependencies) <= 32 and len(set(dependencies)) == len(dependencies),
        "Finite shade dependencies required",
    )
    pom_raw = read(JAVA / "pom.xml")
    document = ET.fromstring(pom_raw)
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    mappings = {
        (
            node.findtext("m:pattern", namespaces=ns),
            node.findtext("m:shadedPattern", namespaces=ns),
            node.findtext("m:rawString", default="false", namespaces=ns),
        )
        for node in document.findall(".//m:relocation", ns)
    }
    source = "com.fasterxml.jackson.core"
    destination = "br.com.maezo.human.internal.jackson"
    require(
        mappings
        == {
            (source, destination, "false"),
            (
                "^(META-INF/versions/[0-9]+/)com/fasterxml/jackson/core/",
                "$1br/com/maezo/human/internal/jackson/",
                "true",
            ),
        },
        "Exact POM shade mapping required",
    )
    source_path, destination_path = source.replace(".", "/") + "/", destination.replace(".", "/") + "/"
    raw = read(reference)
    entries = zip_bytes(raw)
    actual_classes = class_map(entries)
    require_multi_release_manifest(entries)
    require_multi_release_entry_classes(entries)
    compiled_main = {
        name: digest
        for name, digest in compilation.items()
        if name.endswith(".class") and name not in spi_map(compilation)
    }
    phase_artifacts = {"SHADE_REFERENCE": entries}
    phase_resource_census(phase_artifacts, {})
    for name, digest in default_resource_pins(compilation).items():
        require(name in entries and sha(entries[name]) == digest, "Shade reference default resource differs")
    contributions: dict[str, str] = {}
    dep_records: list[dict[str, Any]] = []
    module_descriptors: dict[str, str] = {}
    for dependency in dependencies:
        dependency_raw = read(dependency)
        dependency_entries = zip_bytes(dependency_raw)
        phase_artifacts[str(dependency)] = dependency_entries
        phase_resource_census(phase_artifacts, {})
        classes = class_map(dependency_entries)
        require(bool(classes), "Empty dependency class census")
        relocated: dict[str, str] = {}
        for name, digest in classes.items():
            logical = logical_member(name)
            if logical == "module-info.class":
                # Account for the measured outcome in either direction. ROOT
                # proves the actual plugin step; no dependency file is stripped.
                origin = str(dependency) + "!" + name
                if name in actual_classes:
                    require(
                        name not in contributions and name not in compiled_main,
                        "Competing module descriptor origin",
                    )
                    contributions[name] = origin
                    relocated[name] = name
                else:
                    module_descriptors[origin] = digest
                continue
            require(logical.startswith(source_path), "Dependency class outside declared relocation")
            mapped = name[: -len(logical)] + destination_path + logical[len(source_path) :]
            require(
                mapped not in contributions and mapped not in compiled_main,
                "Competing shade dependency origin",
            )
            contributions[mapped] = str(dependency) + "!" + name
            relocated[name] = mapped
        dep_records.append(
            {
                "path": str(dependency),
                "sha256": sha(dependency_raw),
                "classes": classes,
                "relocated_entries": relocated,
            }
        )
    require(
        set(actual_classes) == set(compiled_main) | set(contributions),
        "Complete measured shade class census differs",
    )
    return {
        "reference": str(reference),
        "reference_sha256": sha(raw),
        "post_shade_classes": actual_classes,
        "pre_shade_classes": compiled_main,
        "pom_sha256": sha(pom_raw),
        "dependencies": dep_records,
        "dependency_origins": contributions,
        "unpackaged_module_descriptors": module_descriptors,
        "required_root_gate": (
            "ACTUAL_SAME_COMPILATION_MAVEN_SHADE_INVOCATION_AND_DEPENDENCY_ORIGINS_NOT_PROVEN_BY_THIS_MAP"
        ),
    }


def build_support(
    classes: Path,
    test_classes: Path,
    fixtures: Path,
    output: Path,
    *,
    shade_reference: Path | None = None,
    shade_dependencies: tuple[Path, ...] = (),
    measurement_input: Path | None = None,
) -> Path:
    """Snapshot one compilation, package finite TestOnly classes/maps exactly once."""
    extra_inputs = (
        ((shade_reference,) if shade_reference is not None else ())
        + shade_dependencies
        + ((measurement_input,) if measurement_input is not None else ())
    )
    disjoint_outputs(
        (output,),
        (classes, test_classes, fixtures, JAVA / "src/main/resources", JAVA / "pom.xml", *extra_inputs),
    )
    require(
        shade_reference is not None or not shade_dependencies, "Shade dependencies require original reference"
    )
    canonical(fixtures, directory=True)
    main = tree(classes)
    tests = tree(test_classes)
    default_resource_pins(main)
    require(
        all(
            logical_member(name) == name
            for name in main
            if logical_member(name).startswith("br/com/maezo/") and name.endswith(".class")
        ),
        "Unadmitted compiled own multi-release origin",
    )
    shade = shade_snapshot(main, shade_reference, shade_dependencies) if shade_reference is not None else None
    require(
        not any(name in main for name in VENDOR_RESOURCES),
        "Generated resources pre-exist compilation snapshot",
    )
    measurement: dict[str, Any] | None = None
    if measurement_input is not None:
        require(shade_reference is not None, "Measurement requires actual same-compilation shade snapshot")
        input_raw = read(measurement_input)
        spec = measurement_inputs(measurement_input, tests, main)
        compiled = pinned_record(spec["root_compile_freeze"], "ROOT compilation")
        require(
            all(str(path) in compiled["dependency_jars"] for path in shade_dependencies),
            "Shade dependencies outside ROOT compiled custody",
        )
        require(read(measurement_input) == input_raw, "Measurement input changed while capturing")
        measurement = {"inputs": str(measurement_input), "inputs_sha256": sha(input_raw), "spec": spec}
        selected = measurement_entries(tests, spec["support_classes"])
        require(not set(selected) & set(main), "Measurement owners cannot be MAIN")
        assert shade_reference is not None
        measurement_resource_census({"SHADE_REFERENCE": zip_entries(shade_reference)}, "SHADE_REFERENCE")
        for dependency in shade_dependencies:
            measurement_resource_census({str(dependency): zip_entries(dependency)}, None)
    else:
        selected = support_entries(tests)
    members = {name: read(test_classes / name) for name in selected}
    require(
        {name: sha(raw) for name, raw in members.items()} == selected,
        "Compiled member changed while capturing",
    )
    if measurement is not None:
        measurement_members(members)
        disjoint_outputs((output,), measurement_roots(measurement["spec"]))
    descriptor_maps: dict[str, str] = {}
    for phase, resource in zip(PHASES, DESCRIPTOR_RESOURCES, strict=True):
        raw = read(fixtures / phase / "secured-startup-descriptors.sha256")
        require(set(parse_inventory(raw)) == DESCRIPTORS, "Descriptor domain must be complete")
        require(read(test_classes / resource) == raw, "Compiled support descriptor resource differs")
        members[resource] = raw
        descriptor_maps[resource] = sha(raw)
    require(
        tree(classes) == main and tree(test_classes) == tests, "Compilation changed before support effect"
    )
    require(
        output.is_absolute() and not output.exists() and not output.is_symlink(),
        "Fresh absolute assembly directory required",
    )
    if measurement is not None:
        assert measurement_input is not None
        require(
            measurement_inputs(measurement_input, tests, main) == measurement["spec"],
            "Measurement inputs drift before support effect",
        )
    fresh_directory(output)
    output.mkdir(parents=True)
    jar = output / "provider-auth-test-support.jar"
    with jar.open("xb") as file:
        with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, raw in sorted(members.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 9, 8, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                archive.writestr(info, raw)
        file.flush()
        os.fsync(file.fileno())
    require(set(zip_entries(jar)) == set(members), "Support package member set differs")
    require(
        tree(classes) == main and tree(test_classes) == tests, "Compilation changed during support effect"
    )
    manifest = {
        "schema": "provider-native-observation-build-support.v1",
        "scope": "TESTONLY_BUILD_BYTE_CORRESPONDENCE_NOT_RUNTIME",
        "classes": str(classes),
        "test_classes": str(test_classes),
        "fixtures": str(fixtures),
        "main_compilation": main,
        "shade": shade,
        "test_compilation": tests,
        "support_classes": selected,
        "descriptor_resources": descriptor_maps,
        "support_jar": str(jar),
        "support_jar_sha256": sha(read(jar)),
    }
    if measurement is not None:
        assert measurement_input is not None
        require(
            measurement_inputs(measurement_input, tests, main) == measurement["spec"],
            "Measurement inputs drift during support effect",
        )
        manifest["schema"] = "provider-native-observation-build-support.v2"
        manifest["measurement"] = measurement
    path = output / "support-inputs.json"
    write_new(path, json_bytes(manifest))
    return path


def check_compilation(manifest: dict[str, Any], additions: dict[str, str] | None = None) -> None:
    measurement = manifest.get("measurement")
    require(
        manifest.get("schema")
        == "provider-native-observation-build-support.v" + ("2" if measurement is not None else "1"),
        "Support schema differs",
    )
    if measurement is not None:
        require(
            isinstance(measurement, dict) and set(measurement) == {"inputs", "inputs_sha256", "spec"},
            "Measurement support keys differ",
        )
        require(
            sha(read(Path(measurement["inputs"]))) == measurement["inputs_sha256"], "Measurement input drift"
        )
        require(
            measurement_inputs(
                Path(measurement["inputs"]), manifest["test_compilation"], manifest["main_compilation"]
            )
            == measurement["spec"],
            "Measurement input spec drift",
        )
        selected = measurement_entries(manifest["test_compilation"], measurement["spec"]["support_classes"])
        require(manifest.get("shade") is not None, "Measurement requires same-compilation shade snapshot")
        compiled = pinned_record(measurement["spec"]["root_compile_freeze"], "ROOT compilation")
        require(
            all(item["path"] in compiled["dependency_jars"] for item in manifest["shade"]["dependencies"]),
            "Shade dependencies outside ROOT compiled custody",
        )
        measurement_members({name: read(Path(manifest["test_classes"]) / name) for name in selected})
    else:
        selected = support_entries(manifest["test_compilation"])
    require(
        set(manifest["descriptor_resources"]) == set(DESCRIPTOR_RESOURCES),
        "Support descriptor census differs",
    )
    require(
        selected == manifest["support_classes"],
        "Support class census differs",
    )
    default_resource_pins(manifest["main_compilation"])
    if manifest.get("shade") is not None:
        shade = manifest["shade"]
        require(
            shade_snapshot(
                manifest["main_compilation"],
                Path(shade["reference"]),
                tuple(Path(item["path"]) for item in shade["dependencies"]),
            )
            == shade,
            "Measured shade inputs drift",
        )
    require(
        tree(Path(manifest["classes"])) == {**manifest["main_compilation"], **(additions or {})},
        "Main compilation changed; never recompile",
    )
    require(
        tree(Path(manifest["test_classes"])) == manifest["test_compilation"],
        "Test compilation changed; never rebuild support",
    )
    require(sha(read(Path(manifest["support_jar"]))) == manifest["support_jar_sha256"], "Support JAR drift")
    expected = {**manifest["support_classes"], **manifest["descriptor_resources"]}
    require(
        {key: sha(raw) for key, raw in zip_entries(Path(manifest["support_jar"])).items()} == expected,
        "Support finite members differ",
    )


def validate_descriptors(root: Path, expected: dict[str, str]) -> None:
    require(set(expected) == DESCRIPTORS, "Closed descriptor domain differs")
    for name, digest in expected.items():
        require(sha(read(root / name)) == digest, "Staged descriptor bytes differ")
    raw = read(root / "conf/bpm-platform.xml")
    require(b"<!DOCTYPE" not in raw and b"<!ENTITY" not in raw, "Descriptor DTD refused")
    document = ET.fromstring(raw)
    engines = [e for e in document.iter() if e.tag.rsplit("}", 1)[-1] == "process-engine"]
    require(len(engines) == 1 and engines[0].get("name") == "default", "Closed engine name differs")
    for name, value in (
        ("authorizationEnabled", "true"),
        ("tenantCheckEnabled", "true"),
        ("databaseSchemaUpdate", "false"),
    ):
        require(
            [
                node.text
                for node in document.iter()
                if node.tag.rsplit("}", 1)[-1] == "property" and node.get("name") == name
            ]
            == [value],
            "Explicit engine guard differs",
        )


def covered(name: str) -> bool:
    """Exact existing StartupAdmission.inventoried domain; no known-name filter."""
    return (
        name.endswith(".jar")
        and name not in OWN
        or "/WEB-INF/classes/" in name
        or name.endswith("/META-INF/context.xml")
    )


def verify_owner_origins(root: Path, files: dict[str, str], support: dict[str, Any]) -> None:
    """Reject competing Maezo origins; never strip/reorder legitimate vendor classes."""
    expected = support["support_classes"]
    observed: dict[str, list[str]] = {name: [] for name in expected}
    phase_artifacts: dict[str, dict[str, bytes]] = {}
    for name in files:
        if not covered(name):
            continue
        if name.endswith(".jar"):
            entries = zip_entries(root / name)
            phase_artifacts[name] = entries
            for member, raw in entries.items():
                logical = logical_member(member)
                while logical.startswith("META-INF/versions/"):
                    logical = logical_member(logical)
                if logical in (*DEFAULT_RESOURCES, *VENDOR_RESOURCES, *DESCRIPTOR_RESOURCES):
                    require(
                        name == "lib/provider-auth-test-support.jar"
                        and member == logical
                        and logical in support["descriptor_resources"]
                        and sha(raw) == support["descriptor_resources"][logical],
                        "Competing security resource origin",
                    )
                if not logical.startswith("br/com/maezo/") or not logical.endswith(".class"):
                    continue
                require(
                    name == "lib/provider-auth-test-support.jar"
                    and logical in expected
                    and member == logical
                    and sha(raw) == expected[logical],
                    "Competing own class origin",
                )
                observed[logical].append(name)
        elif "/WEB-INF/classes/" in name:
            member = name.split("/WEB-INF/classes/", 1)[1]
            logical = logical_member(member)
            while logical.startswith("META-INF/versions/"):
                logical = logical_member(logical)
            phase_artifacts[name] = {member: read(root / name)}
            require(
                logical not in (*DEFAULT_RESOURCES, *VENDOR_RESOURCES, *DESCRIPTOR_RESOURCES),
                "Competing loose security resource origin",
            )
            require(
                not (logical.startswith("br/com/maezo/") and logical.endswith(".class")),
                "Competing loose own class origin",
            )
    if support.get("measurement") is not None:
        measurement_resource_census(phase_artifacts, None)
    support_origin = "lib/provider-auth-test-support.jar"
    phase_resource_census(
        phase_artifacts,
        {
            resource: (support_origin, phase_artifacts[support_origin][resource])
            for resource in DESCRIPTOR_RESOURCES
        },
    )
    require(
        all(origins == ["lib/provider-auth-test-support.jar"] for origins in observed.values()),
        "Owner classes must each have one support origin",
    )


def stage_vendor(support_manifest: Path, phase_a: Path, phase_b: Path, output: Path) -> Path:
    """ROOT supplies actual extracted, sealed phase trees including support JAR."""
    support_raw = read(support_manifest)
    support = load(support_manifest)
    require(json_bytes(support) == support_raw, "Support manifest changed while parsing")
    disjoint_outputs(
        (output,), (support_manifest.parent, support_manifest, phase_a, phase_b, *support_roots(support))
    )
    check_compilation(support)
    require(output.is_absolute() and not output.exists(), "Fresh vendor output directory required")
    inputs: dict[str, Any] = {}
    resources: dict[str, bytes] = {}
    for phase, root in zip(PHASES, (phase_a, phase_b), strict=True):
        canonical(root, directory=True)
        require(
            {path.name for path in (root / "webapps").iterdir()} == set(APPS), "Actual webapp set differs"
        )
        files = {
            **{f"lib/{name}": digest for name, digest in tree(root / "lib").items()},
            **{f"webapps/{name}": digest for name, digest in tree(root / "webapps").items()},
        }
        descriptor_resource = f"provider-native/{phase}/secured-startup-descriptors.sha256"
        support_map = zip_entries(Path(support["support_jar"]))[descriptor_resource]
        descriptors = parse_inventory(support_map)
        validate_descriptors(root, descriptors)
        require(
            files.get("lib/provider-auth-test-support.jar") == support["support_jar_sha256"],
            "Actual staged support differs",
        )
        for name in files:
            leaf = Path(name).name
            require(
                leaf not in {"camunda.cfg.xml", "activiti.cfg.xml", "activiti-context.xml", "processes.xml"},
                "Alternate engine descriptor refused",
            )
            require(
                not name.endswith(
                    (
                        "WEB-INF/tomcat-web.xml",
                        "META-INF/services/jakarta.servlet.ServletContainerInitializer",
                        "META-INF/services/javax.servlet.ServletContainerInitializer",
                    )
                ),
                "Unadmitted startup resource refused",
            )
        verify_owner_origins(root, files, support)
        values = {name: digest for name, digest in files.items() if covered(name)}
        resource = f"provider-native/{phase}/secured-startup-vendor.sha256"
        resources[resource] = inventory(values)
        inputs[phase] = {"root": str(root), "covered": values, "descriptors": descriptors}
    require(
        inputs["phase-a"]["covered"] == inputs["phase-b"]["covered"],
        "Same candidate/vendor binaries required across phases",
    )
    check_compilation(support)
    for data in inputs.values():
        root = Path(data["root"])
        validate_descriptors(root, data["descriptors"])
        fresh = {
            **{f"lib/{name}": digest for name, digest in tree(root / "lib").items()},
            **{f"webapps/{name}": digest for name, digest in tree(root / "webapps").items()},
        }
        require(
            {key: value for key, value in fresh.items() if covered(key)} == data["covered"],
            "Staging changed before inventory effect",
        )
    require(read(support_manifest) == support_raw, "Support manifest changed before inventory effect")
    fresh_directory(output)
    output.mkdir(parents=True)
    for resource, raw in resources.items():
        write_new(output / "resources" / resource, raw)
    manifest = {
        "schema": "provider-native-observation-build-vendor.v1",
        "scope": "TESTONLY_BUILD_BYTE_CORRESPONDENCE_NOT_RUNTIME",
        "support_manifest": str(support_manifest),
        "support_manifest_sha256": sha(support_raw),
        "phase_inputs": inputs,
        "generated_resources": {name: sha(raw) for name, raw in resources.items()},
        "generated_resources_root": str(output / "resources"),
    }
    if support.get("measurement") is not None:
        manifest["schema"] = "provider-native-observation-build-vendor.v2"
        manifest["measurement"] = support["measurement"]
    path = output / "vendor-inputs.json"
    write_new(path, json_bytes(manifest))
    return path


def verify_final(
    vendor_manifest: Path,
    main_jar: Path,
    rest_spi: Path,
    output: Path,
    *,
    image_context: Path | None = None,
) -> Path:
    """Join final bytes to snapshots; actual compiler/shade invocation is ROOT's gate."""
    vendor_raw = read(vendor_manifest)
    vendor = load(vendor_manifest)
    require(json_bytes(vendor) == vendor_raw, "Vendor manifest changed while parsing")
    require(
        vendor.get("schema")
        == "provider-native-observation-build-vendor.v"
        + ("2" if vendor.get("measurement") is not None else "1"),
        "Vendor schema differs",
    )
    support_path = Path(vendor["support_manifest"])
    require(sha(read(support_path)) == vendor["support_manifest_sha256"], "Support provenance drift")
    support = load(support_path)
    require(vendor.get("measurement") == support.get("measurement"), "Vendor measurement passthrough differs")
    require(
        set(vendor["generated_resources"]) == set(VENDOR_RESOURCES)
        and set(vendor["phase_inputs"]) == set(PHASES),
        "Finite variant resource/domain differs",
    )
    boundary_inputs = (
        vendor_manifest.parent,
        support_path.parent,
        vendor_manifest,
        support_path,
        main_jar,
        rest_spi,
        Path(vendor["generated_resources_root"]),
        *(Path(data["root"]) for data in vendor["phase_inputs"].values()),
        *support_roots(support),
    )
    disjoint_outputs((output,) + ((image_context,) if image_context is not None else ()), boundary_inputs)
    check_compilation(support, vendor["generated_resources"])
    main_raw = read(main_jar)
    spi_raw = read(rest_spi)
    support_raw = read(Path(support["support_jar"]))
    require(sha(support_raw) == support["support_jar_sha256"], "Support JAR drift during capture")
    main = zip_bytes(main_raw)
    spi = zip_bytes(spi_raw)
    support_entries_map = zip_bytes(support_raw)
    phase_artifacts = {"MAIN": main, "SPI": spi, "SUPPORT": support_entries_map}
    if support.get("measurement") is not None:
        measurement_resource_census(phase_artifacts, "MAIN")
    if support.get("shade") is not None:
        shade = support["shade"]
        phase_artifacts["SHADE_REFERENCE"] = zip_entries(Path(shade["reference"]))
        for dependency in shade["dependencies"]:
            phase_artifacts["SHADE_DEPENDENCY:" + dependency["path"]] = zip_entries(Path(dependency["path"]))
    expected_phase: dict[str, tuple[str, bytes]] = {}
    for resource, digest in support["descriptor_resources"].items():
        raw = support_entries_map[resource]
        require(sha(raw) == digest, "Support phase descriptor bytes differ")
        expected_phase[resource] = ("SUPPORT", raw)
    for resource, digest in vendor["generated_resources"].items():
        raw = read(Path(vendor["generated_resources_root"]) / resource)
        require(
            sha(raw) == digest and main.get(resource) == raw and resource not in spi,
            "Generated vendor origin differs",
        )
        expected_phase[resource] = ("MAIN", raw)
    require(
        not any(name in main or name in spi for name in DESCRIPTOR_RESOURCES),
        "Descriptor origin must be support only",
    )
    phase_resource_census(phase_artifacts, expected_phase)
    require(
        not any(name in main or name in spi for name in support["support_classes"]),
        "TestOnly owners must be support only",
    )
    expected_spi = spi_map(support["main_compilation"])
    expected_main = {
        name: digest
        for name, digest in support["main_compilation"].items()
        if name.endswith(".class") and name not in expected_spi
    }
    require(bool(expected_spi) and class_map(spi) == expected_spi, "SPI origin/member set differs")
    if support.get("shade") is not None:
        expected_main = support["shade"]["post_shade_classes"]
    require_multi_release_manifest(main)
    require_multi_release_entry_classes(main)
    require(class_map(main) == expected_main, "MAIN complete class census differs")
    defaults = default_resource_pins(support["main_compilation"])
    for resource, digest in defaults.items():
        origins = [
            (name, sha(raw))
            for entries in (main, spi, zip_entries(Path(support["support_jar"])))
            for name, raw in entries.items()
            if logical_member(name) == resource
        ]
        require(origins == [(resource, digest)], "Default production resource byte/origin differs")
    context_members: dict[str, bytes] = {}
    for phase, data in vendor["phase_inputs"].items():
        root = Path(data["root"])
        validate_descriptors(root, data["descriptors"])
        current = {
            **{f"lib/{name}": digest for name, digest in tree(root / "lib").items()},
            **{f"webapps/{name}": digest for name, digest in tree(root / "webapps").items()},
        }
        require(
            {name: digest for name, digest in current.items() if covered(name)} == data["covered"],
            "Final vendor staging drift",
        )
        resource = f"provider-native/{phase}/secured-startup-vendor.sha256"
        actual_covered = {name: digest for name, digest in current.items() if covered(name)}
        require(
            parse_inventory(main[resource]) == data["covered"] == actual_covered,
            "Packaged vendor resource/full staging map differs",
        )
        verify_owner_origins(root, current, support)
        for name, digest in data["descriptors"].items():
            raw = read(root / name)
            require(sha(raw) == digest, "Descriptor changed before context effect")
            context_members[f"{phase}/secured-layout/{name}"] = raw
        for name, raw in (
            ("maezo-human-command.jar", main_raw),
            ("maezo-rest-spi.jar", spi_raw),
            ("provider-auth-test-support.jar", support_raw),
        ):
            context_members[f"{phase}/{name}"] = raw
    result = {
        "schema": "provider-native-observation-build-final.v1",
        "scope": "BUILD_ONLY_NOT_IMAGE_ABI_OR_RUNTIME",
        "vendor_manifest_sha256": sha(vendor_raw),
        "main_jar": str(main_jar),
        "main_jar_sha256": sha(main_raw),
        "rest_spi_jar": str(rest_spi),
        "rest_spi_jar_sha256": sha(spi_raw),
        "support_jar_sha256": support["support_jar_sha256"],
        "shade_reference": support.get("shade"),
        "generated_resources": vendor["generated_resources"],
        "descriptor_resources": support["descriptor_resources"],
        "next_gates": [
            "same-final-JAR deployed-runtime-compatibility CIB2.1 JDBC10.1.47",
            "ROOT actual image/extraction/full classpath/unique origins",
            "ROOT physical secured phaseA/B qualification",
        ],
    }
    if support.get("measurement") is not None:
        check_compilation(support, vendor["generated_resources"])
        result["schema"] = "provider-native-observation-build-final.v2"
        result["measurement"] = support["measurement"]
        result["required_root_source_build_gate"] = (
            "INDEPENDENT_SOURCE_PAIR_AND_ACTUAL_COMPOSED_SOURCE_SINGLE_COMPILATION_EQUIVALENCE_NOT_PROVEN_BY_SUPPLIED_MAP"
        )
    require(
        read(vendor_manifest) == vendor_raw
        and read(main_jar) == main_raw
        and read(rest_spi) == spi_raw
        and read(Path(support["support_jar"])) == support_raw,
        "Inputs changed before final output effect",
    )
    require(
        output.is_absolute() and not output.exists() and not output.is_symlink(),
        "Fresh final output required",
    )
    require(output.resolve(strict=False) == output, "Canonical final output required")
    if image_context is not None:
        require(
            image_context.is_absolute() and not image_context.exists() and not image_context.is_symlink(),
            "Fresh absolute image context required",
        )
        fresh_directory(image_context)
        result["image_context_file_sha256"] = {
            name: sha(raw) for name, raw in sorted(context_members.items())
        }
        for name, raw in sorted(context_members.items()):
            write_new(image_context / name, raw)
    write_new(output, json_bytes(result))
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    support = commands.add_parser("support")
    for name in ("classes", "test-classes", "fixtures", "output"):
        support.add_argument("--" + name, type=Path, required=True)
    support.add_argument("--shade-reference", type=Path)
    support.add_argument("--shade-dependency", type=Path, action="append", default=[])
    measured = commands.add_parser("support-measurement")
    for name in ("classes", "test-classes", "fixtures", "output", "measurement-inputs", "shade-reference"):
        measured.add_argument("--" + name, type=Path, required=True)
    measured.add_argument("--shade-dependency", type=Path, action="append", required=True)
    vendor = commands.add_parser("stage-vendor")
    for name in ("support-manifest", "phase-a", "phase-b", "output"):
        vendor.add_argument("--" + name, type=Path, required=True)
    final = commands.add_parser("finalize")
    for name in ("vendor-manifest", "main-jar", "rest-spi", "output"):
        final.add_argument("--" + name, type=Path, required=True)
    final.add_argument("--image-context", type=Path)
    args = parser.parse_args()
    if args.command in ("support", "support-measurement"):
        build_support(
            args.classes,
            args.test_classes,
            args.fixtures,
            args.output,
            shade_reference=args.shade_reference,
            shade_dependencies=tuple(args.shade_dependency),
            measurement_input=args.measurement_inputs if args.command == "support-measurement" else None,
        )
    elif args.command == "stage-vendor":
        stage_vendor(args.support_manifest, args.phase_a, args.phase_b, args.output)
    else:
        verify_final(
            args.vendor_manifest, args.main_jar, args.rest_spi, args.output, image_context=args.image_context
        )


if __name__ == "__main__":
    main()
