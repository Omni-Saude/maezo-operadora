"""Fresh, all-PASS current verdicts, separate from legacy mathematical equality."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import types
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.ci import check_evidence_ledger_hashes as legacy

if TYPE_CHECKING:
    from scripts.ci.ledger_current_relations import RelationScope, Requirement

SCHEMA = "maezo-ledger-current-execution/v1"
OBSERVATION_SCHEMA = "maezo-ledger-current-observation/v1"
STATUSES = frozenset({"ACCEPTED", "REFUSED", "FAILED", "UNRESOLVED"})
_OBSERVATION_KEYS = frozenset(
    {
        "schema",
        "run_id",
        "root",
        "test_path",
        "discovered",
        "selected",
        "deselected",
        "collection_errors",
        "reports",
        "finished",
        "exitstatus",
        "runtime",
        "sources",
    }
)
_ITEM_KEYS = frozenset({"nodeid", "file", "body_file", "body_digest"})
_PHASE_KEYS = frozenset({"nodeid", "phase", "outcome", "wasxfail", "body_entered"})
_RUNTIME_KEYS = frozenset(
    {
        "executable",
        "version",
        "prefix",
        "base_prefix",
        "pytest_version",
        "initial_environment",
        "final_environment",
        "plugin_autoload_disabled",
    }
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def strict_json(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_JSON_FIELD")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=pairs)


def minimal_environment(home: Path) -> dict[str, str]:
    env = {
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "HOME": str(home),
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    if sys.platform == "darwin":
        # macOS otherwise synthesizes this key at exec, defeating exact equality.
        env["__CF_USER_TEXT_ENCODING"] = f"0x{os.getuid():X}:0x0:0x0"
    return env


def regular_bytes(path: Path) -> bytes:
    """Reject symlink components and special files without following their targets."""
    absolute = path.absolute()
    for component in [*reversed(absolute.parents), absolute]:
        if component.is_symlink():
            raise ValueError("SOURCE_SYMLINK")
    descriptor = os.open(absolute, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("SOURCE_NOT_REGULAR")
        return stream.read()


def git(root: Path, *argv: str) -> bytes:
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *argv],
        cwd=root,
        stderr=subprocess.PIPE,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_REPLACE_OBJECTS": "1",
        },
    )


def source_inventory(root: Path) -> dict[str, Any]:
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("CANDIDATE_NOT_CLEAN")
    files: dict[str, str] = {}
    for raw in git(root, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if raw:
            metadata, name_bytes = raw.split(b"\t", 1)
            mode, kind, object_id = metadata.decode().split()
            name = name_bytes.decode()
            payload = regular_bytes(root / name)
            actual_blob = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
            if mode not in {"100644", "100755"} or kind != "blob" or actual_blob != object_id:
                raise ValueError("CANDIDATE_BLOB_DRIFT")
            files[name] = digest(payload)
    return {
        "commit": git(root, "rev-parse", "HEAD").decode().strip(),
        "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
        "files": files,
        "file_stats": {name: file_identity(root / name) for name in files},
    }


def file_identity(path: Path) -> list[int]:
    metadata = path.stat()
    return [metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_mode]


# Finite locally selected tools. No caller environment, executable or capsule input.
_UV = Path(shutil.which("uv") or "/missing-uv").resolve()
_RUNTIME_PROBE = r"""
import hashlib, importlib.metadata, json, pathlib, sys, sysconfig
files = {}
for root in sorted({pathlib.Path(sysconfig.get_path(n)) for n in ('stdlib','purelib','platlib')}):
    for path in sorted(root.rglob('*')):
        if path.is_file():
            path = path.resolve()
            files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
for path in (pathlib.Path(sys.executable).resolve(), pathlib.Path(sys.prefix) / 'pyvenv.cfg'):
    files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
stats = {}
for name in files:
    s = pathlib.Path(name).stat()
    stats[name] = [s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode]
distributions = sorted(({'name': d.metadata['Name'], 'version':d.version,
    'location':str(pathlib.Path(d.locate_file('')).resolve()), 'direct_url':d.read_text('direct_url.json')}
    for d in importlib.metadata.distributions()), key=lambda d: d['name'])
identity = dict(executable=str(pathlib.Path(sys.executable).resolve()), version=sys.version,
    prefix=sys.prefix, base_prefix=sys.base_prefix, pytest_version=importlib.metadata.version('pytest'))
print(json.dumps(dict(files=files, stats=stats, distributions=distributions,
    identity=identity),sort_keys=True))
"""


def _policy_value(value: Any) -> Any:
    """Compare loaded policy code/defaults/constants against the committed module."""
    if isinstance(value, types.CodeType):
        return [
            value.co_filename,
            value.co_name,
            value.co_qualname,
            value.co_code.hex(),
            value.co_exceptiontable.hex(),
            value.co_linetable.hex(),
            value.co_firstlineno,
            value.co_posonlyargcount,
            value.co_names,
            value.co_varnames,
            value.co_freevars,
            value.co_cellvars,
            value.co_flags,
            value.co_argcount,
            value.co_kwonlyargcount,
            [_policy_value(item) for item in value.co_consts],
        ]
    if isinstance(value, types.FunctionType):
        return [
            _policy_value(value.__code__),
            _policy_value(value.__defaults__),
            _policy_value(value.__kwdefaults__),
        ]
    if isinstance(value, (staticmethod, classmethod)):
        return _policy_value(value.__func__)
    if isinstance(value, property):
        return [_policy_value(value.fget), _policy_value(value.fset)]
    if isinstance(value, dict):
        return sorted((str(key), _policy_value(item)) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return [_policy_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_policy_value(item) for item in value), key=repr)
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return repr(value)
    return str(type(value)) + ":" + getattr(value, "__qualname__", str(value))


def _module_policy(module: types.ModuleType) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        if getattr(value, "__module__", None) == module.__name__:
            if isinstance(value, type):
                result[name] = {
                    key: _policy_value(item)
                    for key, item in vars(value).items()
                    if isinstance(item, (types.FunctionType, staticmethod, classmethod, property))
                }
            elif isinstance(value, types.FunctionType):
                result[name] = _policy_value(value)
        elif name.isupper():
            result[name] = _policy_value(value)
    return result


def _g2_policy() -> dict[str, Any]:
    # Use the actual helper selected by the actual classifier, never a same-name path.
    from scripts.ci import pytest_metadata_admission

    for module in (legacy, pytest_metadata_admission):
        filename = module.__file__
        if not isinstance(filename, str):
            raise ValueError("G2_LOADED_POLICY_DRIFT")
        path = Path(filename).resolve()
        if path != Path(__file__).resolve().with_name(module.__name__.rsplit(".", 1)[1] + ".py"):
            raise ValueError("G2_LOADED_POLICY_DRIFT")
        payload = git(path.parents[2], "show", "HEAD:scripts/ci/" + path.name)
        if regular_bytes(path) != payload:
            raise ValueError("G2_LOADED_POLICY_DRIFT")
        trusted = types.ModuleType(module.__name__)
        trusted.__file__ = str(path)
        for value in vars(module).values():
            members = (
                vars(value).values()
                if isinstance(value, type) and value.__module__ == module.__name__
                else (value,)
            )
            for member in members:
                if isinstance(member, (staticmethod, classmethod)):
                    member = member.__func__
                if (
                    isinstance(member, types.FunctionType)
                    and member.__module__ == module.__name__
                    and member.__code__.co_filename == str(path)
                    and member.__globals__ is not vars(module)
                ):
                    raise ValueError("G2_LOADED_POLICY_DRIFT")
        exec(compile(payload, str(path), "exec", dont_inherit=True), vars(trusted))
        if _module_policy(module) != _module_policy(trusted):
            raise ValueError("G2_LOADED_POLICY_DRIFT")
    if legacy._metadata_module() is not pytest_metadata_admission:
        raise ValueError("G2_LOADED_POLICY_DRIFT")
    return {
        module.__name__: digest(canonical(_module_policy(module)))
        for module in (legacy, pytest_metadata_admission)
    }


def _tool_provenance() -> dict[str, Any]:
    policy_root = Path(__file__).resolve().parents[2]
    names = (
        "ledger_current_execution.py",
        "ledger_current_observer.py",
        "check_evidence_ledger_current.py",
        "check_evidence_ledger_hashes.py",
        "pytest_metadata_admission.py",
        "ledger_current_relations.py",
        "ledger_invalid_declarations.py",
        "ledger_history_proofs.py",
    )
    files: dict[str, str] = {}
    for name in names:
        relative = "scripts/ci/" + name
        path = policy_root / relative
        payload = regular_bytes(path)
        if payload != git(policy_root, "show", "HEAD:" + relative):
            raise ValueError("TOOL_SOURCE_NOT_COMMITTED")
        files[str(path)] = digest(payload)
    return {
        "commit": git(policy_root, "rev-parse", "HEAD").decode().strip(),
        "tree": git(policy_root, "rev-parse", "HEAD^{tree}").decode().strip(),
        "files": files,
        "stats": {path: file_identity(Path(path)) for path in files},
        "loaded_g2": _g2_policy(),
    }


def _admission_state(admission: Any) -> dict[str, Any]:
    """The full result of the real classifier, including its absence rechecks."""
    return {
        "status": admission.status,
        "reason": admission.reason,
        "source_sha256": admission.source_sha256,
        "dependencies": [list(pair) for pair in admission.dependencies],
    }


def _runtime_probe(python: Path, root: Path, environment: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", _RUNTIME_PROBE],
        cwd=root,
        env=environment,
        capture_output=True,
        check=True,
        timeout=60,
    )
    value: dict[str, Any] = strict_json(result.stdout)
    return value


def _launcher_identity(python: Path) -> dict[str, Any]:
    """Attest the launcher without executing potentially replaced runtime code."""
    resolved = python.resolve(strict=True)
    metadata = python.lstat()
    return {
        "path": str(python),
        "resolved": str(resolved),
        "link": os.readlink(python) if stat.S_ISLNK(metadata.st_mode) else None,
        "entry": [
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ],
        "sha256": digest(regular_bytes(resolved)),
        "stat": file_identity(resolved),
    }


@dataclass(frozen=True)
class Occurrence:
    line: int
    raw: str

    @property
    def identity(self) -> str:
        return f"{self.line}:{digest(self.raw.encode())}"

    @property
    def row(self) -> legacy.DeclaredRow:
        row = legacy.parse_row_line(self.raw)
        if self.line < 1 or row is None or not legacy.is_date_qualified(legacy.extract_row_date(self.raw)):
            raise ValueError("INVALID_DECLARED_OCCURRENCE")
        return row


@dataclass(frozen=True)
class CurrentExecutionVerdict:
    schema: str
    occurrence: str
    run_id: str
    status: str
    reasons: tuple[str, ...]
    packet: str
    receipt_sha256: str


def validate_observation(
    observation: Any, binding: dict[str, Any], returncode: int | None
) -> tuple[str, ...]:
    """Closed pure validation. A validation result alone cannot mint a fresh verdict."""
    errors: list[str] = []
    if not isinstance(observation, dict) or set(observation) != _OBSERVATION_KEYS:
        return ("OBSERVATION_SCHEMA",)
    if (
        observation["schema"] != OBSERVATION_SCHEMA
        or observation["run_id"] != binding["run_id"]
        or observation["root"] != binding["root"]
        or observation["test_path"] != binding["test_path"]
        or observation["finished"] is not True
        or type(observation["exitstatus"]) is not int
        or observation["exitstatus"] != returncode
        or type(returncode) is not int
        or returncode != 0
    ):
        errors.append("TERMINAL_BINDING")
    runtime = observation["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != _RUNTIME_KEYS:
        errors.append("RUNTIME_SCHEMA")
    elif (
        runtime["executable"]
        != binding.get("runtime_identity", {}).get("executable", str(Path(sys.executable).resolve()))
        or runtime["version"] != binding.get("runtime_identity", {}).get("version", sys.version)
        or runtime["prefix"] != binding.get("runtime_identity", {}).get("prefix", sys.prefix)
        or runtime["base_prefix"] != binding.get("runtime_identity", {}).get("base_prefix", sys.base_prefix)
        or runtime["pytest_version"] != binding["pytest_version"]
        or runtime["initial_environment"] != binding["environment"]
        or runtime["final_environment"] != binding["environment"]
        or runtime["plugin_autoload_disabled"] is not True
    ):
        errors.append("RUNTIME_ENVIRONMENT_BINDING")
    discovered, selected = observation["discovered"], observation["selected"]
    if not isinstance(discovered, list) or not isinstance(selected, list) or not discovered:
        return tuple(errors + ["EMPTY_OR_INVALID_COLLECTION"])
    allowed = binding["allowed_sources"]
    identities: list[str] = []
    for item in discovered:
        if (
            not isinstance(item, dict)
            or set(item) != _ITEM_KEYS
            or any(not isinstance(value, str) for value in item.values())
            or not item["nodeid"].startswith(binding["test_path"] + "::")
            or item["file"] != str(Path(binding["root"]) / binding["test_path"])
            or item["body_file"] not in allowed
            or len(item["body_digest"]) != 64
        ):
            return tuple(errors + ["COLLECTION_SOURCE_BINDING"])
        identities.append(item["nodeid"])
    if len(identities) != len(set(identities)) or discovered != selected:
        errors.append("COLLECTION_DUPLICATE_OR_CHANGED")
    if observation["deselected"] != [] or observation["collection_errors"] != []:
        errors.append("COLLECTION_INCOMPLETE")
    reports = observation["reports"]
    if not isinstance(reports, list):
        return tuple(errors + ["PHASE_SCHEMA"])
    seen: Counter[tuple[str, str]] = Counter()
    for report in reports:
        if (
            not isinstance(report, dict)
            or set(report) != _PHASE_KEYS
            or report["nodeid"] not in identities
            or not isinstance(report["phase"], str)
            or report["phase"] not in {"setup", "call", "teardown"}
            or report["outcome"] != "passed"
            or report["wasxfail"] is not False
            or type(report["body_entered"]) is not bool
            or (report["phase"] == "call" and report["body_entered"] is not True)
        ):
            errors.append("PHASE_NOT_COMPLETE_PASS")
            continue
        seen[(report["nodeid"], report["phase"])] += 1
    expected = Counter((node, phase) for node in identities for phase in ("setup", "call", "teardown"))
    if seen != expected:
        errors.append("PHASE_IDENTITY_OR_CARDINALITY")
    sources = observation["sources"]
    if (
        not isinstance(sources, list)
        or any(not isinstance(path, str) or path not in allowed for path in sources)
        or not {item["body_file"] for item in discovered}.issubset(sources)
    ):
        errors.append("OBSERVED_SOURCE_ESCAPE")
    return tuple(dict.fromkeys(errors))


class CurrentRunner:
    """Own each fresh run and consume its handle once; reports are never proof inputs."""

    def __init__(
        self,
        root: Path,
        output: Path,
        *,
        base: str | None = None,
        allow_live: bool = False,
        timeout: float = 60,
    ) -> None:
        self.root = root.resolve()
        self.output = output.resolve()
        self.allow_live = allow_live
        if not 0 < timeout <= 300 or self.output.is_relative_to(self.root):
            raise ValueError("INVALID_RUN_BOUNDARY")
        self.timeout = timeout
        self._issued: dict[str, CurrentExecutionVerdict] = {}
        self._executed: set[str] = set()
        self.base = base
        self._scope: RelationScope | None = None
        self._runtime: dict[str, Any] | None = None
        self._preparation: dict[str, Any] | None = None

    def _requirement(self, occurrence: Occurrence) -> Requirement:
        from scripts.ci import ledger_current_relations as relations

        base = self.base or git(self.root, "rev-parse", "HEAD").decode().strip()
        planned = relations.plan_current_relations(self.root, base)
        if self._scope is not None and planned != self._scope:
            raise ValueError("CURRENT_EXPECTATION_STALE")
        self._scope = planned
        for requirement in planned.requirements:
            if requirement.occurrence.identity == occurrence.identity:
                return requirement
        # Compatibility for direct, unselected ordinary occurrences. Relations
        # may only execute when included in the authoritative planned scope.
        ordinary = next((row for row in planned.rows if row.identity == occurrence.identity), None)
        if (
            self.base is not None
            or ordinary is None
            or any(
                ordinary.identity in {edge.claim.target.identity, edge.successor.identity}
                for edge in planned.relations
            )
        ):
            raise ValueError("CURRENT_OCCURRENCE_NOT_SCHEDULED")
        return relations.Requirement(
            ordinary,
            relations.CurrentExpectation(
                ordinary,
                planned.candidate,
                digest(regular_bytes(self.root / ordinary.test_path)),
                ordinary.declared_hash,
                relations._eligibility(ordinary),
            ),
            (),
        )

    def _prepare_runtime(self, packet: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        from scripts.ci.ledger_history_proofs import validate_locked_inventory

        if self._runtime is None:
            runtime_root = packet / "runtime"
            runtime_root.mkdir(mode=0o700)
            home = runtime_root / "home"
            home.mkdir(mode=0o700)
            environment = minimal_environment(Path.home())
            environment["UV_PROJECT_ENVIRONMENT"] = str(runtime_root / ".venv")
            # UV's cache contains only installation artifacts; credentials are not inherited.
            uv_hash = digest(regular_bytes(_UV))
            uv_stat = file_identity(_UV)
            command = [
                str(_UV),
                "sync",
                "--offline",
                "--frozen",
                "--no-config",
                "--extra",
                "dev",
                "--no-install-project",
                "--python",
                str(Path(sys.executable).resolve()),
                "--project",
                str(self.root),
            ]
            started = datetime.now(UTC).isoformat()
            with (
                (packet / "prepare.stdout").open("xb") as stdout,
                (packet / "prepare.stderr").open("xb") as stderr,
            ):
                result = subprocess.run(
                    command,
                    cwd=self.root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=180,
                )
            preparation = dict(
                command=command,
                environment=environment,
                started=started,
                ended=datetime.now(UTC).isoformat(),
                returncode=result.returncode,
                uv_path=str(_UV),
                uv_sha256=uv_hash,
                uv_stat=uv_stat,
                lock_sha256=digest(regular_bytes(self.root / "uv.lock")),
                config_sha256=digest(regular_bytes(self.root / "pyproject.toml")),
                streams={
                    str(packet / name): digest(regular_bytes(packet / name))
                    for name in ("prepare.stdout", "prepare.stderr")
                },
            )
            (packet / "preparation.json").write_bytes(canonical(preparation))
            if (
                result.returncode != 0
                or digest(regular_bytes(_UV)) != uv_hash
                or file_identity(_UV) != uv_stat
            ):
                raise ValueError("LOCKED_RUNTIME_PREPARATION_REFUSED")
            python = runtime_root / ".venv/bin/python"
            launcher = _launcher_identity(python)
            if launcher["resolved"] != str(Path(sys.executable).resolve()):
                raise ValueError("LOCKED_RUNTIME_LAUNCHER_IDENTITY")
            inventory = _runtime_probe(python, self.root, minimal_environment(home))
            if _launcher_identity(python) != launcher:
                raise ValueError("LOCKED_RUNTIME_LAUNCHER_DRIFT")
            validate_locked_inventory(regular_bytes(self.root / "uv.lock"), inventory)
            identity = inventory["identity"]
            if (
                identity["prefix"] != str(runtime_root / ".venv")
                or identity["executable"] != str(Path(sys.executable).resolve())
                or identity["version"] != sys.version
                or any(
                    not Path(item["location"]).is_relative_to(runtime_root / ".venv") or item["direct_url"]
                    for item in inventory["distributions"]
                )
            ):
                raise ValueError("LOCKED_RUNTIME_IDENTITY")
            self._runtime = dict(python=str(python), home=str(home), inventory=inventory, launcher=launcher)
            self._preparation = preparation
        assert self._preparation is not None
        if not self._runtime_stable():
            raise ValueError("LOCKED_RUNTIME_DRIFT")
        return self._runtime, self._preparation

    def _runtime_stable(self) -> bool:
        if self._runtime is None or self._preparation is None:
            return False
        prepared = self._preparation
        return (
            digest(regular_bytes(_UV)) == prepared["uv_sha256"]
            and file_identity(_UV) == prepared["uv_stat"]
            and digest(regular_bytes(self.root / "uv.lock")) == prepared["lock_sha256"]
            and digest(regular_bytes(self.root / "pyproject.toml")) == prepared["config_sha256"]
            and all(digest(regular_bytes(Path(path))) == sha for path, sha in prepared["streams"].items())
            and _launcher_identity(Path(self._runtime["python"])) == self._runtime["launcher"]
            and _runtime_probe(
                Path(self._runtime["python"]), self.root, minimal_environment(Path(self._runtime["home"]))
            )
            == self._runtime["inventory"]
            and _launcher_identity(Path(self._runtime["python"])) == self._runtime["launcher"]
        )

    def run(self, occurrence: Occurrence) -> CurrentExecutionVerdict:
        row = occurrence.row
        run_id = uuid.uuid4().hex
        packet = self.output / run_id
        packet.mkdir(parents=True, mode=0o700)
        reasons: list[str] = []
        status = "REFUSED"
        binding: dict[str, Any] = {}
        returncode: int | None = None
        recipe_hash: str | None = None
        started = datetime.now(UTC).isoformat()
        try:
            if not legacy.is_safe_test_path(row.test_path):
                raise ValueError("UNSAFE_TEST_PATH")
            ledger_lines = regular_bytes(self.root / legacy.DEFAULT_LEDGER_PATH).decode().splitlines()
            if occurrence.line > len(ledger_lines) or ledger_lines[occurrence.line - 1] != occurrence.raw:
                raise ValueError("ROW_SOURCE_BINDING")
            classifier = getattr(legacy, "classify_test_admission", None)
            if classifier is None:
                raise ValueError("G2_ADMISSION_UNAVAILABLE")
            provenance = _tool_provenance()
            requirement = self._requirement(occurrence)
            expectation = requirement.current
            admission = classifier(self.root, expectation.authoritative_row.test_path)
            admission_state = _admission_state(admission)
            if admission.status not in {"OFFLINE", "LIVE"}:
                raise ValueError("G2_ADMISSION_UNKNOWN")
            if admission.status == "LIVE":
                if not self.allow_live:
                    raise ValueError("G2_LIVE_ASSERTION_REQUIRED")
                raise ValueError("LIVE_OWNER_CONTEXT_NOT_IMPLEMENTED")
            before = source_inventory(self.root)
            if expectation.candidate.source_sha256 != digest(
                canonical(before)
            ) or expectation.test_source_sha256 != before["files"].get(row.test_path):
                raise ValueError("CURRENT_EXPECTATION_SOURCE_DRIFT")
            if before["files"].get(row.test_path) != admission.source_sha256:
                raise ValueError("G2_ADMISSION_SOURCE_DRIFT")
            dependencies = dict(admission.dependencies)
            if len(dependencies) != len(admission.dependencies) or any(
                before["files"].get(path) != sha for path, sha in dependencies.items()
            ):
                raise ValueError("G2_ADMISSION_DEPENDENCY_DRIFT")
            if "uv.lock" not in before["files"] or "pyproject.toml" not in before["files"]:
                raise ValueError("SOURCE_CONFIG_LOCK_REQUIRED")
            tool = Path(__file__).with_name("ledger_current_observer.py").resolve()
            tools = provenance["files"]
            prepared_runtime, preparation = self._prepare_runtime(packet)
            runtime = prepared_runtime["inventory"]["files"]
            runtime_stats = prepared_runtime["inventory"]["stats"]
            if source_inventory(self.root) != before or _tool_provenance() != provenance:
                raise ValueError("PRELAUNCH_SOURCE_TOOL_DRIFT")
            if _admission_state(classifier(self.root, row.test_path)) != admission_state:
                raise ValueError("PRELAUNCH_ADMISSION_DRIFT")
            if _launcher_identity(Path(prepared_runtime["python"])) != prepared_runtime["launcher"]:
                raise ValueError("PRELAUNCH_RUNTIME_LAUNCHER_DRIFT")
            allowed = {str(self.root / path): sha for path, sha in before["files"].items()}
            allowed.update(runtime)
            allowed[str(tool)] = digest(regular_bytes(tool))
            home = packet / "home"
            home.mkdir(mode=0o700)
            environment = minimal_environment(home)
            environment["PATH"] = str(Path(prepared_runtime["python"]).parent) + ":/usr/bin:/bin"
            pytest_argv = [
                row.test_path,
                "-v",
                "--tb=no",
                "-p",
                "no:cacheprovider",
                "--disable-plugin-autoload",
                "-o",
                "addopts=",
                "--rootdir",
                str(self.root),
                "--confcutdir",
                str(self.root),
                "-p",
                "pytest_asyncio.plugin",
            ]
            request = {
                "root": str(self.root),
                "test_path": row.test_path,
                "run_id": run_id,
                "observation": str(packet / "observation.json"),
                "pytest_argv": pytest_argv,
            }
            request_path = packet / "request.json"
            request_path.write_bytes(canonical(request))
            command = [prepared_runtime["python"], "-I", "-B", str(tool), str(request_path)]
            binding = {
                **request,
                "occurrence": occurrence.identity,
                "row": asdict(row),
                "original": asdict(requirement.occurrence),
                "current_expectation": asdict(expectation),
                "required_relations": list(requirement.required_relations),
                "base": self._scope.base if self._scope else None,
                "tool_provenance": provenance,
                "runtime_identity": prepared_runtime["inventory"]["identity"],
                "runtime_launcher": prepared_runtime["launcher"],
                "runtime_preparation": preparation,
                "source": before,
                "runtime_files": runtime,
                "runtime_stats": runtime_stats,
                "environment": environment,
                "admission": admission_state,
                "command": command,
                "allowed_sources": allowed,
            }
            binding["tool_files"] = tools
            binding["pytest_version"] = prepared_runtime["inventory"]["identity"]["pytest_version"]
            (packet / "binding.json").write_bytes(canonical(binding))
            status = "FAILED"
            with (packet / "stdout").open("wb") as stdout, (packet / "stderr").open("wb") as stderr:
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                self._executed.add(run_id)
                deadline = time.monotonic() + self.timeout
                try:
                    while process.poll() is None:
                        if time.monotonic() > deadline or max(stdout.tell(), stderr.tell()) > 8 * 1024 * 1024:
                            reasons.append("TIMEOUT_OR_OUTPUT_LIMIT")
                            break
                        time.sleep(0.01)
                except KeyboardInterrupt:
                    reasons.append("INTERRUPTED")
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                    returncode = process.wait()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                os.killpg(process.pid, signal.SIGKILL)
                reasons.append("PROCESS_GROUP_SURVIVED")
            raw = regular_bytes(packet / "stdout") + regular_bytes(packet / "stderr")
            lines = legacy.extract_result_lines(raw.decode("utf-8", errors="replace"))
            if lines:
                recipe_hash = legacy.compute_recipe_hash(lines)
            recipe_version = "fixed"
            if (
                recipe_hash != "sha256:" + expectation.recipe_sha256
                and expectation.recipe_eligibility == "FIXED_OR_LEGACY"
            ):
                legacy_lines = legacy.extract_result_lines(
                    raw.decode("utf-8", errors="replace"), node_id_regex=legacy._RESULT_LINE_RE_LEGACY
                )
                if (
                    legacy_lines
                    and legacy.compute_recipe_hash(legacy_lines) == "sha256:" + expectation.recipe_sha256
                ):
                    recipe_hash = legacy.compute_recipe_hash(legacy_lines)
                    recipe_version = "legacy"
            binding["recipe_version"] = recipe_version
            # Version is derived from the same raw capture, not an input policy flag.
            (packet / "binding.json").write_bytes(canonical(binding))
            if recipe_hash != "sha256:" + expectation.recipe_sha256:
                reasons.append("MATHEMATICAL_HASH_MISMATCH")
            if source_inventory(self.root) != before:
                reasons.append("SOURCE_DRIFT")
            if (
                not self._runtime_stable()
                or _tool_provenance() != provenance
                or self._requirement(occurrence) != requirement
            ):
                reasons.append("RUNTIME_TOOL_DRIFT")
            if _admission_state(legacy.classify_test_admission(self.root, row.test_path)) != admission_state:
                reasons.append("G2_ADMISSION_DRIFT")
            observation = strict_json(regular_bytes(packet / "observation.json"))
            reasons.extend(validate_observation(observation, binding, returncode))
            expected_lines = [f"{item['nodeid']} PASSED" for item in observation.get("selected", [])]
            if sorted(lines) != sorted(expected_lines):
                reasons.append("RESULT_COLLECTION_MISMATCH")
            if not reasons:
                status = "ACCEPTED"
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            legacy.SupersessionError,
            subprocess.SubprocessError,
        ) as exc:
            code = str(exc)
            reasons.append(
                code
                if isinstance(exc, (ValueError, legacy.SupersessionError)) and code.isupper()
                else "EXECUTION_OR_ARTIFACT_ERROR"
            )
        artifacts = {path.name: digest(path.read_bytes()) for path in packet.iterdir() if path.is_file()}
        receipt = {
            "schema": SCHEMA,
            "occurrence": occurrence.identity,
            "run_id": run_id,
            "status": status,
            "reasons": list(dict.fromkeys(reasons)),
            "start": started,
            "end": datetime.now(UTC).isoformat(),
            "returncode": returncode,
            "recipe_hash": recipe_hash,
            "binding_sha256": digest(canonical(binding)) if binding else None,
            "current_expectation": binding.get("current_expectation"),
            "original": binding.get("original"),
            "artifacts": artifacts,
        }
        receipt_bytes = canonical(receipt)
        (packet / "receipt.json").write_bytes(receipt_bytes)
        verdict = CurrentExecutionVerdict(
            SCHEMA,
            occurrence.identity,
            run_id,
            status,
            tuple(dict.fromkeys(reasons)),
            str(packet),
            digest(receipt_bytes),
        )
        self._issued[run_id] = verdict
        return verdict

    def consume(self, verdict: CurrentExecutionVerdict, occurrence: Occurrence) -> bool:
        issued = self._issued.pop(verdict.run_id, None)
        if issued is not verdict or verdict.occurrence != occurrence.identity or verdict.status != "ACCEPTED":
            return False
        packet = Path(verdict.packet)
        try:
            raw = regular_bytes(packet / "receipt.json")
            if digest(raw) != verdict.receipt_sha256:
                return False
            receipt = strict_json(raw)
            binding = strict_json(regular_bytes(packet / "binding.json"))
            stable = (
                source_inventory(self.root) == binding["source"]
                and self._runtime_stable()
                and _tool_provenance() == binding["tool_provenance"]
                and _admission_state(legacy.classify_test_admission(self.root, occurrence.row.test_path))
                == binding["admission"]
                and asdict(self._requirement(occurrence).current) == binding["current_expectation"]
                and asdict(self._requirement(occurrence).occurrence) == binding["original"]
                and digest(canonical(binding)) == receipt["binding_sha256"]
            )
            return stable and all(
                digest(regular_bytes(packet / name)) == sha for name, sha in receipt["artifacts"].items()
            )
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            legacy.SupersessionError,
            subprocess.SubprocessError,
        ):
            return False


_HISTORY_ACCEPTED = frozenset(
    {"VALID_HISTORICAL_EQUALITY", "CORRECTED_WITH_INVALID_HISTORY", "VERIFIED_HISTORY_OWN_LOCK"}
)


def _history_policy() -> dict[str, Any]:
    """Attest actual loaded adapter dependencies, including capsule wrapper closures."""
    from scripts.ci import ledger_archived_catalog, ledger_history_proofs, ledger_invalid_declarations

    modules = (ledger_history_proofs, ledger_invalid_declarations, ledger_archived_catalog)
    result = {}
    for module in modules:
        path = Path(__file__).resolve().with_name(module.__name__.rsplit(".", 1)[1] + ".py")
        if module.__file__ != str(path):
            raise ValueError("HISTORY_LOADED_POLICY_DRIFT")
        payload = git(path.parents[2], "show", "HEAD:scripts/ci/" + path.name)
        if regular_bytes(path) != payload:
            raise ValueError("HISTORY_LOADED_POLICY_DRIFT")
        trusted = types.ModuleType(module.__name__)
        trusted.__file__ = str(path)
        exec(compile(payload, str(path), "exec", dont_inherit=True), vars(trusted))
        actual, expected = _module_policy(module), _module_policy(trusted)
        for name, value in vars(module).items():
            if isinstance(value, types.FunctionType) and value.__module__ == module.__name__:
                original = value
                reference = getattr(trusted, name, None)
                # contextmanager wrappers carry the selected implementation in a closure.
                while hasattr(original, "__wrapped__"):
                    if not isinstance(reference, types.FunctionType) or not hasattr(reference, "__wrapped__"):
                        raise ValueError("HISTORY_LOADED_POLICY_DRIFT")
                    actual[name + ":closure"] = [
                        _policy_value(c.cell_contents) for c in original.__closure__ or ()
                    ]
                    expected[name + ":closure"] = [
                        _policy_value(c.cell_contents) for c in reference.__closure__ or ()
                    ]
                    original, reference = original.__wrapped__, reference.__wrapped__
                if original.__globals__ is not vars(module):
                    raise ValueError("HISTORY_LOADED_POLICY_DRIFT")
                actual[name + ":unwrapped"] = _policy_value(original)
                expected[name + ":unwrapped"] = _policy_value(reference)
        if actual != expected:
            raise ValueError("HISTORY_LOADED_POLICY_DRIFT")
        result[module.__name__] = dict(
            sha256=digest(payload), policy=digest(canonical(actual)), stat=file_identity(path)
        )
    if (
        ledger_history_proofs.checker is not legacy
        or ledger_history_proofs.static is not ledger_invalid_declarations
    ):
        raise ValueError("HISTORY_LOADED_POLICY_DRIFT")
    return result


@dataclass(frozen=True)
class HistoryVerdict:
    schema: str
    relation: str
    run_id: str
    status: str
    historical_claim_verified: bool | None
    historical_test_status: str
    reasons: tuple[str, ...]
    execution_count: int | None
    packet: str
    receipt_sha256: str


class HistoryRunner:
    """Finite real adapters. Only a locally issued, freshly bound object is consumable."""

    def __init__(self, root: Path, output: Path, *, base: str) -> None:
        from scripts.ci.ledger_current_relations import plan_current_relations

        self.root = root.resolve()
        self.output = output.resolve()
        if self.output.is_relative_to(self.root):
            raise ValueError("INVALID_HISTORY_BOUNDARY")
        self.base = (
            git(self.root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
        )
        self._scope = plan_current_relations(self.root, self.base)
        self._issued: dict[str, tuple[HistoryVerdict, dict[str, Any]]] = {}
        self._runtime = CurrentRunner(self.root, self.output, base=self.base)

    def _edge(self, identity: str) -> Any:
        from scripts.ci.ledger_current_relations import plan_current_relations

        if plan_current_relations(self.root, self.base) != self._scope:
            raise ValueError("HISTORY_SCOPE_DRIFT")
        selected = [
            edge
            for edge in self._scope.relations
            if edge.identity == identity and identity in self._scope.required_relations
        ]
        if len(selected) != 1:
            raise ValueError("HISTORY_EDGE_NOT_SCHEDULED")
        return selected[0]

    def _admission(self, edge: Any) -> dict[str, Any]:
        admission = legacy.classify_test_admission(
            self.root,
            edge.claim.target.test_path,
            edge.claim.source_commit,
            expected_source_sha256=edge.claim.source_test_sha256,
        )
        if admission.status != "OFFLINE":
            raise ValueError("HISTORY_ADMISSION_" + admission.status)
        return _admission_state(admission)

    def _custody(self, edge: Any) -> dict[str, Any]:
        return dict(
            source=source_inventory(self.root),
            edge=asdict(self._edge(edge.identity)),
            tools=_tool_provenance(),
            history_policy=_history_policy(),
            admission=self._admission(edge),
        )

    def _v1(self, edge: Any, packet: Path) -> dict[str, Any]:
        ledger = regular_bytes(self.root / legacy.DEFAULT_LEDGER_PATH).decode()
        plan = legacy.build_supersession_plan(self.root, ledger)
        actual = plan.by_target_row_sha256[edge.claim.target.row_sha256]
        if (
            actual.target.raw_line != edge.claim.target.raw_line
            or actual.successor.raw_line != edge.successor.raw_line
            or actual.claim.source_commit != edge.claim.source_commit
        ):
            raise ValueError("HISTORY_LEGACY_EDGE_DRIFT")
        if git(self.root, "show", edge.claim.source_commit + ":uv.lock") != regular_bytes(
            self.root / "uv.lock"
        ):
            raise ValueError("HISTORY_V1_LOCK_MISMATCH")
        runtime, _ = self._runtime._prepare_runtime(packet)
        before = self._custody(edge)
        env = minimal_environment(Path(runtime["home"]))
        env["PATH"] = str(Path(runtime["python"]).parent) + ":/usr/bin:/bin"
        saved = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(env)
            verification = legacy.verify_historical_row(
                self.root, actual, runtime["python"], allow_live=False
            )
            (packet / "verification.json").write_bytes(canonical(asdict(verification)))
            # The immutable verifier deliberately exposes no raw capture. A second
            # real capture retains the test outcome without changing its API or math.
            capture = legacy._capture_historical_recipe(
                self.root, actual, runtime["python"], allow_live=False
            )
        finally:
            os.environ.clear()
            os.environ.update(saved)
        (packet / "historical.stdout").write_text(capture.combined_output or "")
        (packet / "capture.json").write_bytes(canonical(asdict(capture)))
        (packet / "invocation.json").write_bytes(
            canonical(
                dict(
                    environment=env,
                    python=runtime["python"],
                    verifier="verify_historical_row",
                    retained_capture="_capture_historical_recipe",
                    allow_live=False,
                )
            )
        )
        if self._custody(edge) != before or not self._runtime._runtime_stable():
            raise ValueError("HISTORY_V1_POSTRUN_DRIFT")
        outcome = legacy.recipe_outcome_from_capture(capture, actual.target.test_path)
        matching = "fixed"
        equal = outcome.ok and outcome.computed_hash == "sha256:" + actual.target.declared_hash
        if (
            not equal
            and actual.target.row_date
            and actual.target.row_date < legacy.LEGACY_NODE_ID_RECIPE_CUTOFF_DATE
        ):
            outcome = legacy.recipe_outcome_from_capture(
                capture, actual.target.test_path, node_id_regex=legacy._RESULT_LINE_RE_LEGACY
            )
            equal = outcome.ok and outcome.computed_hash == "sha256:" + actual.target.declared_hash
            matching = "legacy-date-eligible"
        lines = legacy.extract_result_lines(capture.combined_output or "")
        historical_status = (
            "FAILED"
            if any(line.endswith(" FAILED") for line in lines)
            else "PASSED"
            if lines and capture.returncode == 0
            else "INCOMPLETE"
        )
        return dict(
            status="VALID_HISTORICAL_EQUALITY" if verification.ok and equal else "FAILED",
            historical_claim_verified=bool(verification.ok and equal),
            historical_test_status=historical_status,
            reasons=[]
            if verification.ok and equal
            else ["HISTORICAL_VERIFICATION_FAILED", verification.message],
            execution_count=2,
            verification=asdict(verification),
            outcome=asdict(outcome),
            matching_algorithm=matching,
            original_declaration=actual.target.declared_hash,
        )

    def _operational(self, edge: Any, packet: Path) -> dict[str, Any]:
        from scripts.ci import ledger_history_proofs as proof
        from scripts.ci import ledger_invalid_declarations as static

        ledger = regular_bytes(self.root / legacy.DEFAULT_LEDGER_PATH).decode()
        plan = static.build_plan(self.root, ledger, legacy.build_supersession_plan(self.root, ledger))
        selected = static.selected_unresolved(
            self.root,
            plan,
            [
                Occurrence(edge.claim.target.line, edge.claim.target.raw_line).row,
                Occurrence(edge.successor.line, edge.successor.raw_line).row,
            ],
            self.base,
        )
        pending = tuple(
            item
            for item in selected
            if (item.target_row_sha256, item.correction_row_sha256)
            == (edge.claim.target.row_sha256, edge.successor.row_sha256)
        )
        if len(pending) != 1:
            raise ValueError("HISTORY_OPERATIONAL_EDGE_MISSING")
        out = packet / "producer"
        out.mkdir(mode=0o700)
        results = proof.execute_selected(self.root, plan, pending, out)
        if len(results) != 1:
            raise ValueError("HISTORY_OPERATIONAL_PARTIAL_RESULT")
        result = results[0]
        payload = result.to_dict()
        (packet / "operational.json").write_bytes(canonical(payload))
        if not isinstance(result, proof.CompleteCorrection):
            return dict(
                status="REFUSED"
                if result.reason.startswith("IC_OPERATIONAL_PROOF_REFUSED")
                else "UNRESOLVED",
                historical_claim_verified=False
                if edge.claim.kind == "V2_INVALID_SOURCE_DECLARATION"
                else None,
                historical_test_status="UNRESOLVED",
                reasons=[result.reason],
                execution_count=result.recorded_fresh_attempts,
            )
        directory = out / edge.successor.row_sha256
        if (
            result.candidate != self._scope.candidate.commit
            or result.tree != self._scope.candidate.tree
            or result.target_row_sha256 != edge.claim.target.row_sha256
            or result.correction_row_sha256 != edge.successor.row_sha256
            or strict_json(regular_bytes(directory / "result.json")) != payload
        ):
            raise ValueError("HISTORY_OPERATIONAL_RESULT_BINDING")
        # Authenticated producer output is retained in full; physical current
        # obligations are still executed independently by CurrentRunner.
        raw = regular_bytes(directory / "fresh-historical/pytest.stdout") + regular_bytes(
            directory / "fresh-historical/pytest.stderr"
        )
        lines = legacy.extract_result_lines(raw.decode())
        valid = edge.claim.kind == "V3_VERIFIED_OWN_LOCK"
        if (
            result.historical_claim_verified is not valid
            or result.status != ("VERIFIED_HISTORY_RELATION" if valid else "CORRECTED_WITH_INVALID_HISTORY")
            or result.historical_status
            != ("VERIFIED_HISTORY_OWN_LOCK" if valid else "INVALID_HISTORICAL_DECLARATION")
            or not lines
        ):
            raise ValueError("HISTORY_OPERATIONAL_DISCRIMINANT")
        return dict(
            status="VERIFIED_HISTORY_OWN_LOCK" if valid else "CORRECTED_WITH_INVALID_HISTORY",
            historical_claim_verified=valid,
            historical_test_status="FAILED" if any(line.endswith(" FAILED") for line in lines) else "PASSED",
            reasons=[],
            execution_count=2,
        )

    def _artifacts(self, packet: Path) -> dict[str, str]:
        result = {}
        for root, directories, files in os.walk(packet, followlinks=False):
            current = Path(root)
            if current == packet:
                directories[:] = [name for name in directories if name != "runtime"]
            for name in directories:
                if (current / name).is_symlink():
                    raise ValueError("HISTORY_PACKET_SYMLINK")
            for name in files:
                path = current / name
                if path == packet / "receipt.json":
                    continue
                result[str(path.relative_to(packet))] = digest(regular_bytes(path))
        return result

    def run(self, identity: str) -> HistoryVerdict:
        run_id = uuid.uuid4().hex
        packet = self.output / run_id
        packet.mkdir(parents=True, mode=0o700)
        before: dict[str, Any] = {}
        data: dict[str, Any] = dict(
            status="REFUSED",
            historical_claim_verified=None,
            historical_test_status="UNRESOLVED",
            reasons=[],
            execution_count=0,
        )
        started = datetime.now(UTC).isoformat()
        try:
            edge = self._edge(identity)
            before = self._custody(edge)
            (packet / "binding.json").write_bytes(canonical(before))
            data = (
                self._v1(edge, packet)
                if edge.claim.kind == "V1_RECIPE_EQUALITY"
                else self._operational(edge, packet)
            )
            if self._custody(edge) != before:
                raise ValueError("HISTORY_POSTRUN_CUSTODY_DRIFT")
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            data.update(
                status="REFUSED", historical_claim_verified=None, reasons=[str(exc)], execution_count=None
            )
        receipt = dict(
            schema="maezo-ledger-history-execution/v1",
            relation=identity,
            run_id=run_id,
            started=started,
            ended=datetime.now(UTC).isoformat(),
            result=data,
            artifacts=self._artifacts(packet),
        )
        raw = canonical(receipt)
        (packet / "receipt.json").write_bytes(raw)
        verdict = HistoryVerdict(
            receipt["schema"],
            identity,
            run_id,
            data["status"],
            data["historical_claim_verified"],
            data["historical_test_status"],
            tuple(data["reasons"]),
            data["execution_count"],
            str(packet),
            digest(raw),
        )
        self._issued[run_id] = (verdict, before)
        return verdict

    def consume(self, verdict: HistoryVerdict, identity: str) -> bool:
        issued = self._issued.pop(verdict.run_id, None)
        if (
            issued is None
            or issued[0] is not verdict
            or verdict.relation != identity
            or verdict.status not in _HISTORY_ACCEPTED
        ):
            return False
        try:
            packet = Path(verdict.packet)
            raw = regular_bytes(packet / "receipt.json")
            edge = self._edge(identity)
            return (
                digest(raw) == verdict.receipt_sha256
                and self._custody(edge) == issued[1]
                and strict_json(raw)["artifacts"] == self._artifacts(packet)
                and (edge.claim.kind != "V1_RECIPE_EQUALITY" or self._runtime._runtime_stable())
            )
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            RuntimeError,
            subprocess.SubprocessError,
        ):
            return False
