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
            inventory = _runtime_probe(python, self.root, minimal_environment(home))
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
            self._runtime = dict(python=str(python), home=str(home), inventory=inventory)
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
            and _runtime_probe(
                Path(self._runtime["python"]), self.root, minimal_environment(Path(self._runtime["home"]))
            )
            == self._runtime["inventory"]
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
            allowed = {str(self.root / path): sha for path, sha in before["files"].items()}
            allowed.update(runtime)
            allowed[str(tool)] = digest(regular_bytes(tool))
            home = packet / "home"
            home.mkdir(mode=0o700)
            environment = minimal_environment(home)
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
