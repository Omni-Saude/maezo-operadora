#!/usr/bin/env python3
"""Executa uma suíte de integração por stack CIB Seven nova e com posse exclusiva."""

from __future__ import annotations

import argparse
import ast
import codecs
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

PROJECT = "maezo-completion-engine"
DOCKER_CONTEXT = "colima"
LOCK_DIR = Path("/Users/familia/code/maezo-operadora/engine.lock")
ENGINE_URL = "http://localhost:18080/engine-rest"
PG_PORT = "15433"
KAFKA_PORT = "19092"
HAPI_URL = "http://localhost:18081/fhir/metadata"
PG_DSN = f"postgresql://maezo:maezo@127.0.0.1:{PG_PORT}/maezo"
BUSY_EXIT = 73
USAGE_EXIT = 64
PROCESS_TERM_GRACE = 1.0
PROCESS_KILL_GRACE = 3.0
_pending_groups: set[int] = set()
_process_owner: EngineLock | None = None
_source_digests: dict[Path, tuple[str, dict[str, str]]] = {}
_collected_items: dict[Path, list[dict[str, Any]]] = {}
_source_leases: dict[Path, EngineLock] = {}
_source_cleanup_ready: set[Path] = set()

CHECKOUT_INPUTS = (
    "docker-compose.yml",
    "pyproject.toml",
    "uv.lock",
    "src",
    "spec",
    "tests",
    "scripts",
    "config",
    "conftest.py",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "uv.toml",
    ".python-version",
    ".env",
)
PASSTHROUGH_ENV = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "TMPDIR",
)
PINNED_DSN_ENV = (
    "MAEZO_TEST_DATABASE_URL",
    "MAEZO_TEST_A2A_EDGE_DATABASE_URL",
    "MAEZO_TEST_AMH_INBOX_DATABASE_URL",
    "MAEZO_TEST_AUDIT_ANCHOR_DRILL_DATABASE_URL",
    "MAEZO_TEST_CHECKPOINT_DATABASE_URL",
)
#: Opt-in que o ROOT liga para SELECIONAR as suites `root_fixture` (as que so' correm com um
#: fixture PRIVADO fornecido pelo ROOT). A descoberta usa-o para distinguir uma desselecao
#: DECLARADA de um ficheiro de teste vazio — ver `discover()`. Tem de ser o mesmo nome que
#: `tests/integration/conftest.py::ROOT_FIXTURE_OPT_IN_ENV`, o que
#: `tests/unit/dev/test_engine_integration_runner.py` fixa.
ROOT_FIXTURE_OPT_IN_ENV = "MAEZO_ROOT_FIXTURES"

RUNTIME_ENV_KEYS = frozenset(
    (
        "DATABASE_URL",
        "ENGINE_REST_URL",
        "CIBSEVEN_BASE_URL",
        "FHIR_BASE_URL",
        "HAPI_FHIR_BASE_URL",
        "KAFKA_BOOTSTRAP_SERVERS",
        "MAEZO_PG_DB",
        "MAEZO_PG_HOST_PORT",
        "MAEZO_PG_PASSWORD",
        "MAEZO_PG_USER",
        ROOT_FIXTURE_OPT_IN_ENV,
        "NO_PROXY",
        "PYTHONHASHSEED",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        *PINNED_DSN_ENV,
    )
)
_URI_PASSWORD = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s/@]+(@)")
_NAMED_SECRET = re.compile(
    r"""(?ix)
    (\b(?:password|passwd|secret|token|api[_-]?key)\b["']?\s*[:=]\s*)
    ("(?:\\.|[^"\\])*(?:"|\Z)|'(?:\\.|[^'\\])*(?:'|\Z)|[^\s,;}\]]+)
    """
)
_SECRET_KEY = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|.+[_-](?:password|secret|token))\Z"
)


class RunnerError(RuntimeError):
    """Erro operacional que deve aparecer no resultado, sem traceback ruidoso."""


class RunnerInterrupted(BaseException):
    """Interrupção solicitada por sinal."""


class ProcessGroupCleanupError(RunnerError):
    """O grupo criado pelo runner não atingiu quiescência no prazo."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_data(payload: Any) -> Any:
    """Projeção pública preserva hashes do núcleo; chaves têm semântica própria."""
    if isinstance(payload, str):
        return _redact_text(payload)
    if isinstance(payload, dict):
        return {
            _redact_text(key) if isinstance(key, str) else key: "<redacted>"
            if isinstance(key, str) and _SECRET_KEY.fullmatch(key)
            else _safe_data(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list | tuple):
        return [_safe_data(value) for value in payload]
    return payload


def _atomic_json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _json_write(path: Path, payload: object) -> None:
    _atomic_json_write(path, _safe_data(payload))


def _append_event(path: Path, event: str, **fields: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"at": _now(), "event": event, **fields}
    with path.open("a") as stream:
        stream.write(json.dumps(_safe_data(payload), sort_keys=True) + "\n")


def _redact_text(value: str) -> str:
    def named(match: re.Match[str]) -> str:
        quote = match[2][0] if match[2].startswith(('"', "'")) else ""
        return f"{match[1]}{quote}<redacted>{quote}"

    return _NAMED_SECRET.sub(named, _URI_PASSWORD.sub(r"\1<redacted>\2", value))


def _base_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    ambient = os.environ if source is None else source
    env = {key: ambient[key] for key in PASSTHROUGH_ENV if key in ambient}
    env.setdefault("PATH", os.defpath)
    env.update(
        {
            "NO_PROXY": "localhost,127.0.0.1",
            "PYTHONHASHSEED": "0",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }
    )
    return env


def _subprocess_env(source: Mapping[str, str] | None) -> dict[str, str]:
    env = _base_env(source)
    if source is not None:
        env.update({key: source[key] for key in RUNTIME_ENV_KEYS if key in source})
    return env


def _group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin reports EPERM briefly while a killed orphan is awaiting reaping.
        # It is still not proof of quiescence, so keep waiting until ESRCH.
        return True
    return True


def _wait_group_gone(group_id: int, timeout: float, *, leader: subprocess.Popen[str] | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while _group_exists(group_id):
        if leader is not None:
            leader.poll()
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.02, max(deadline - time.monotonic(), 0)))
    return True


@contextmanager
def _cleanup_signal_mask() -> Iterator[None]:
    signals = {signal.SIGINT, signal.SIGTERM}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextmanager
def _spawn_signal_guard(on_interrupt: Callable[[], None] | None = None) -> Iterator[Callable[[], None]]:
    """Protect the complete owned-child lifetime without changing its signal mask.

    Call the yielded activation function after Popen assignment and registration.
    Before activation parent signals defer; afterwards they close the owned child
    and dispatch the original handlers promptly, including during communicate().
    Reentrant signals defer through cleanup, then dispatch individually so a second
    raising handler cannot interrupt entry into the first handler's cleanup.

    The caller's finally must quiesce its child INSIDE this scope. Original handlers
    restore only after that cleanup, never while a normally owned child remains live.
    Caught handlers reset on exec; ignored dispositions and preexisting masks stay
    unchanged. Like CLI signal setup, this guard requires the main Python thread.
    """
    previous_handlers: dict[int, Any] = {}
    deferred: dict[int, Any] = {}
    active = False
    delivering = False

    def deliver() -> None:
        nonlocal active, delivering
        failure: BaseException | None = None
        delivering = True
        try:
            if on_interrupt is not None:
                try:
                    on_interrupt()
                except BaseException as exc:
                    failure = exc
            while deferred:
                signum = min(deferred)
                frame = deferred.pop(signum)
                previous = previous_handlers[signum]
                try:
                    if callable(previous):
                        previous(signum, frame)
                    else:
                        # A default disposition must retain its OS semantics. The
                        # owned child is already closed before restoring it here.
                        signal.signal(signum, previous)
                        try:
                            signal.raise_signal(signum)
                        finally:
                            signal.signal(signum, defer)
                except BaseException as exc:
                    if failure is None:
                        failure = exc
        finally:
            # Once interruption starts unwinding, late signals must remain deferred
            # until the caller's finally has closed its group and restored its mask.
            if failure is not None:
                active = False
            delivering = False
        if failure is not None:
            raise failure

    def defer(signum: int, frame: object) -> None:
        deferred[signum] = frame
        if active and not delivering:
            deliver()

    def activate() -> None:
        nonlocal active
        active = True
        if deferred:
            deliver()

    try:
        with _cleanup_signal_mask():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous = signal.getsignal(signum)
                if previous is None:
                    raise RunnerError("spawn signal handler cannot be restored")
                if previous != signal.SIG_IGN:
                    signal.signal(signum, defer)
                    previous_handlers[signum] = previous
        yield activate
    finally:
        active = False
        try:
            if deferred:
                deliver()
        finally:
            with _cleanup_signal_mask():
                for signum, previous in previous_handlers.items():
                    signal.signal(signum, previous)


def _record_pending(group_id: int) -> None:
    _pending_groups.add(group_id)
    if _process_owner is not None and not _process_owner.update(
        "subprocess_running", subprocess_quiescent=False, pending_pgids=sorted(_pending_groups)
    ):
        raise ProcessGroupCleanupError("posse perdida ao registrar PGID")


def _record_quiescent(group_id: int) -> None:
    remaining = _pending_groups - {group_id}
    if _process_owner is not None and not _process_owner.update(
        "subprocess_reaped",
        subprocess_quiescent=not remaining,
        pending_pgids=sorted(remaining),
        last_reaped_pgid=group_id,
        quiescence_confirmed_at=_now(),
    ):
        raise ProcessGroupCleanupError("posse perdida ao registrar quiescência")
    _pending_groups.discard(group_id)


def _quiesce_group(process: subprocess.Popen[str], group_id: int) -> tuple[str | None, str | None]:
    with _cleanup_signal_mask():
        if group_id not in _pending_groups or group_id != process.pid:
            raise ProcessGroupCleanupError("PGID não pertence a este subprocesso")
        if _group_exists(group_id):
            with suppress(ProcessLookupError):
                os.killpg(group_id, signal.SIGTERM)
            if not _wait_group_gone(group_id, PROCESS_TERM_GRACE, leader=process):
                with suppress(ProcessLookupError):
                    os.killpg(group_id, signal.SIGKILL)
                if not _wait_group_gone(group_id, PROCESS_KILL_GRACE, leader=process):
                    raise ProcessGroupCleanupError(f"grupo {group_id} permaneceu ativo após TERM e KILL")
        try:
            output = process.communicate(timeout=PROCESS_KILL_GRACE)
        except subprocess.TimeoutExpired as exc:
            raise ProcessGroupCleanupError(
                f"líder {process.pid} não foi recolhido após quiescência do grupo {group_id}"
            ) from exc
        _record_quiescent(group_id)
        return output


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 300,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    # Saída bruta fica somente em FD anônimo, nunca no arquivo de evidência.
    # O finally publica uma cópia redigida mesmo em sinal/EPERM/cleanup incerto.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as raw:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("")
        process: subprocess.Popen[str] | None = None
        stdout: str | None = None
        stderr: str | None = None

        def close_spawned_group() -> None:
            nonlocal stdout, stderr
            if process is not None and process.pid in _pending_groups:
                stdout, stderr = _quiesce_group(process, process.pid)

        with _spawn_signal_guard(close_spawned_group) as activate:
            try:
                if _process_owner is not None and not _process_owner.update(
                    "subprocess_spawning", subprocess_quiescent=False
                ):
                    raise ProcessGroupCleanupError("posse perdida antes do spawn")
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=_subprocess_env(env),
                    text=True,
                    stdout=raw if log_path is not None else subprocess.PIPE,
                    stderr=subprocess.STDOUT if log_path is not None else subprocess.PIPE,
                    start_new_session=True,
                )
                _record_pending(process.pid)
                activate()
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                    return_code = int(process.returncode)
                except subprocess.TimeoutExpired:
                    return_code = 124
            finally:
                with _cleanup_signal_mask():
                    try:
                        if process is not None and process.pid in _pending_groups:
                            stdout, stderr = _quiesce_group(process, process.pid)
                    finally:
                        if log_path is not None:
                            raw.flush()
                            raw.seek(0)
                            log_path.write_text(_redact_text(raw.read()))
        if log_path is not None:
            stdout, stderr = log_path.read_text(errors="replace"), ""
        return subprocess.CompletedProcess(
            command, return_code, _redact_text(stdout or ""), _redact_text(stderr or "")
        )


def _checked(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 300,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    result = _run(command, cwd=cwd, env=env, timeout=timeout, log_path=log_path)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RunnerError(
            _redact_text(f"comando falhou (rc={result.returncode}): {' '.join(command)}\n{detail}")
        )
    return result


def _git(checkout: Path, *args: str) -> str:
    safe_args = list(args)
    if safe_args and safe_args[0] == "diff":
        safe_args[1:1] = ["--no-ext-diff", "--no-textconv"]
    result = _checked(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *safe_args],
        cwd=checkout,
        timeout=30,
    )
    return result.stdout.strip()


def _uv_resolution_args(checkout: Path) -> list[str]:
    options = tomllib.loads((checkout / "uv.lock").read_text()).get("options", {})
    cutoff = options.get("exclude-newer")
    return ["--exclude-newer", str(cutoff)] if cutoff is not None else []


def _uv_python(checkout: Path, *args: str) -> list[str]:
    return [
        "uv",
        "run",
        "--locked",
        "--no-config",
        "--no-env-file",
        "--extra",
        "dev",
        *_uv_resolution_args(checkout),
        "--project",
        str(checkout),
        "python",
        "-I",
        *args,
    ]


EVIDENCE_RELATIVE = "scripts/ci/pytest_execution_evidence.py"
# Selecao fechada do protocolo MUI-01. A autoridade de fonte e o catalogo
# autenticado do nucleo, conferido por discovery; estes nomes nao ampliam skips.
MUTATIONS = {
    "tests/integration/chaos/test_crash_between_seams.py::"
    "test_b1a_mutation_check_chain_insert_outside_lock_turns_suite_red": "b1a",
    "tests/integration/chaos/test_crash_between_seams.py::"
    "test_b1b_posture_mutation_check_restart_on_missing_instance_turns_suite_red": "b1b_posture",
    "tests/integration/chaos/test_crash_between_seams.py::"
    "test_b1b_mutation_check_effect_before_emit_turns_suite_red": "b1b",
    "tests/integration/chaos/test_sink_down_failclosed.py::"
    "test_c1_down_mutation_check_fail_open_swallow_turns_suite_red": "c1_down",
    "tests/integration/processes/test_t33_a1_cancel_handoff_redelivery_idempotency.py::"
    "test_a1_mutation_check_broken_idempotency_creates_a_second_instance": "a1_a2",
    "tests/integration/processes/test_t33_a2_agent_start_idempotency_matrix.py::"
    "test_a2_mutation_check_broken_idempotency_creates_a_second_instance": "a1_a2",
}


def _mutation_selection(nodeid: str) -> tuple[str, str, str]:
    if nodeid not in MUTATIONS:
        raise RunnerError("selecao de mutacao fora do catalogo fechado")
    test_file = nodeid.split("::", 1)[0]
    suite = "chaos" if test_file.startswith("tests/integration/chaos/") else "core"
    return suite, test_file, MUTATIONS[nodeid]


_PYTEST_BOOTSTRAP = """
import hashlib, pathlib, sys, types, pytest
path = pathlib.Path(sys.argv[1])
content = path.read_bytes()
if hashlib.sha256(content).hexdigest() != sys.argv[2]:
    raise SystemExit("núcleo pytest diverge dos bytes autenticados")
module = types.ModuleType('maezo_execution_evidence')
module.__file__ = str(path)
sys.modules[module.__name__] = module
exec(compile(content, str(path), 'exec'), module.__dict__)
plugin = module.EvidencePlugin(pathlib.Path(sys.argv[4]), pathlib.Path(sys.argv[3]))
raise SystemExit(pytest.main(sys.argv[5:], plugins=[plugin]))
"""


def _evidence_source(checkout: Path) -> tuple[Path, bytes, str]:
    checkout = checkout.resolve()
    _assert_execution_source(checkout)
    expected = _source_digests[checkout][1].get(EVIDENCE_RELATIVE)
    path = checkout / EVIDENCE_RELATIVE
    content = path.read_bytes()
    if expected is None or hashlib.sha256(content).hexdigest() != expected:
        raise RunnerError("núcleo de evidência fora dos bytes autenticados")
    return path, content, expected


def _evidence_api(checkout: Path) -> Any:
    path, content, _ = _evidence_source(checkout)
    # Compila exatamente os bytes conferidos, sem loader/pyc nem releitura da origem.
    module = ModuleType("maezo_execution_evidence")
    module.__file__ = str(path)
    exec(compile(content, str(path), "exec"), module.__dict__)
    if module.SCHEMA_VERSION != 2:
        raise RunnerError("núcleo de evidência requer schema 2")
    return module


def _evidence_validator(checkout: Path) -> Any:
    return _evidence_api(checkout).validate_execution


def _pytest_command(
    checkout: Path,
    *args: str,
    evidence_path: Path | None = None,
    mutation_nodeid: str | None = None,
) -> list[str]:
    checkout = checkout.resolve()
    bootstrap = _PYTEST_BOOTSTRAP
    if mutation_nodeid is not None:
        _, _, mutation = _mutation_selection(mutation_nodeid)
        if evidence_path is None or not args or args[0] != mutation_nodeid:
            raise RunnerError("mutacao exige evidencia e selecao exata")
        # Literal de catalogo, nunca texto livre/env herdado; antes de qualquer import pytest.
        bootstrap = f"import os\nos.environ['MAEZO_CHAOS_MUTATE'] = {mutation!r}\n" + bootstrap
    if evidence_path is None:
        entry = ["-m", "pytest"]
    else:
        path, _, digest = _evidence_source(checkout)
        entry = ["-c", bootstrap, str(path), digest, str(checkout), str(evidence_path)]
    return _uv_python(
        checkout,
        *entry,
        "--disable-plugin-autoload",
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "no:cacheprovider",
        "-c",
        str(checkout / "pyproject.toml"),
        "--confcutdir",
        str(checkout),
        "--rootdir",
        str(checkout),
        *args,
    )


def validate_checkout(checkout_input: str, expected_sha: str) -> tuple[Path, str]:
    checkout = Path(checkout_input).expanduser().resolve()
    if not checkout.is_dir() or not (checkout / ".git").exists():
        raise RunnerError(f"checkout Git inexistente: {checkout}")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise RunnerError("SHA esperado deve ter exatamente 40 caracteres hexadecimais minúsculos")
    root = Path(_git(checkout, "rev-parse", "--show-toplevel")).resolve()
    if root != checkout:
        raise RunnerError(f"CHECKOUT deve ser a raiz do worktree: informado={checkout}, raiz={root}")
    actual_sha = _git(checkout, "rev-parse", "HEAD")
    if actual_sha != expected_sha:
        raise RunnerError(f"SHA esperado {expected_sha}, mas CHECKOUT está em {actual_sha}")

    tracked_dirty = set(filter(None, _git(checkout, "diff", "--name-only").splitlines()))
    tracked_dirty.update(filter(None, _git(checkout, "diff", "--cached", "--name-only").splitlines()))
    if tracked_dirty:
        raise RunnerError("checkout possui alterações tracked: " + ", ".join(sorted(tracked_dirty)))
    untracked = _git(checkout, "ls-files", "--others", "--exclude-standard", "--", *CHECKOUT_INPUTS)
    ignored = _git(checkout, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    foreign = []
    for relative in filter(None, ignored.split("\0")):
        path = Path(relative)
        # Não executar nem copiar esses caches; a cópia possui venv nova.
        if path.parts[0] in {".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache"}:
            continue
        if "__pycache__" in path.parts and path.suffix == ".pyc":
            continue
        if (
            path.parts[0] in CHECKOUT_INPUTS
            or (len(path.parts) == 1 and path.suffix in {".py", ".pth", ".so", ".pyd"})
            or path.name in {".env", "uv.toml", "pytest.ini", "setup.cfg", "tox.ini", ".python-version"}
        ):
            foreign.append(relative)
    root_python = _git(
        checkout, "ls-files", "--others", "--exclude-standard", "--", ":(top,glob)*.py", ":(top,glob)*.pth"
    )
    if foreign or root_python:
        raise RunnerError("fontes/configurações untracked ou ignored fora do SHA recusadas")
    if untracked:
        raise RunnerError("entradas de execução untracked: " + ", ".join(untracked.splitlines()))

    required = (
        checkout / "docker-compose.yml",
        checkout / "scripts/dev/docker-compose.engine-integration.yml",
    )
    for path in required:
        relative = path.relative_to(checkout).as_posix()
        result = _run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=checkout, timeout=30)
        if result.returncode:
            raise RunnerError(f"entrada Compose não rastreada no SHA alvo: {relative}")

    # Nunca importa Python do checkout fornecido: .venv/.pth não pertencem ao SHA.
    return checkout, (checkout / "src/maezo/__init__.py").as_posix()


@contextmanager
def execution_checkout(checkout: Path, expected_sha: str, results_dir: Path) -> Iterator[tuple[Path, str]]:
    """Cópia própria dos blobs Git; nenhum reset/clean no checkout do usuário."""
    scratch = Path(tempfile.mkdtemp(prefix="maezo-engine-source-")).resolve()
    target = scratch / "checkout"
    try:
        _checked(
            ["git", "clone", "--shared", "--no-checkout", "--quiet", "--", str(checkout), str(target)],
            cwd=scratch,
            timeout=120,
        )
        _checked(
            ["git", "-c", "core.hooksPath=/dev/null", "checkout", "--quiet", "--detach", expected_sha],
            cwd=target,
            timeout=120,
        )
        if _git(target, "rev-parse", "HEAD") != expected_sha:
            raise RunnerError("cópia de execução diverge do SHA esperado")
        tracked = _git(target, "ls-tree", "-rz", expected_sha).split("\0")
        digests: dict[str, str] = {}
        git_blobs: dict[str, str] = {}
        for entry in filter(None, tracked):
            header, relative = entry.split("\t", 1)
            mode, kind, blob_id = header.split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise RunnerError("entrada de execução não regular no SHA")
            path = target / relative
            if path.is_symlink() or not path.is_file():
                raise RunnerError("entrada de execução não regular no SHA")
            if path.name == ".env":
                raise RunnerError("arquivo dotenv executável no SHA recusado")
            content = path.read_bytes()
            blob = b"blob " + str(len(content)).encode() + b"\0" + content
            if hashlib.sha1(blob, usedforsecurity=False).hexdigest() != blob_id:
                raise RunnerError("bytes materializados divergem do blob declarado pelo SHA Git")
            git_blobs[relative] = blob_id
            digests[relative] = hashlib.sha256(content).hexdigest()
        probe = _checked(
            _uv_python(target, "-c", "import pathlib,maezo; print(pathlib.Path(maezo.__file__).resolve())"),
            cwd=target,
            timeout=180,
        )
        imported = Path(probe.stdout.strip().splitlines()[-1]).resolve()
        if (target / "src/maezo").resolve() not in imported.parents:
            raise RunnerError("import maezo resolveu fora da cópia do SHA")
        _json_write(
            results_dir / "execution-source.json",
            {
                "source_checkout": str(checkout),
                "sha": expected_sha,
                "execution_checkout": str(target),
                "maezo_import": str(imported),
                "tracked_sha256": digests,
                "tracked_git_blob": git_blobs,
                "uv_lock_sha256": digests["uv.lock"],
                "owned_fresh_environment": True,
            },
        )
        _source_digests[target] = (expected_sha, digests)
        yield target, str(imported)
    finally:
        # A generator may finalize during unwind/GC. Cleanup permission belongs to
        # the source lease, never to the lifetime of the Python context reference.
        owner = _source_leases.get(target)
        if not _pending_groups and (
            owner is None
            or (
                target in _source_cleanup_ready
                and owner.owns()
                and owner.owner.get("subprocess_quiescent") is not False
            )
        ):
            if target in _source_digests:
                _assert_execution_source(target)
            shutil.rmtree(scratch)
            _source_digests.pop(target, None)
            _source_leases.pop(target, None)
            _source_cleanup_ready.discard(target)


def _bind_execution_source(checkout: Path, lock: EngineLock) -> None:
    _assert_execution_source(checkout)
    if not lock.owns() or checkout in _source_leases:
        raise RunnerError("fonte sem posse exclusiva para vincular à lease")
    _source_leases[checkout] = lock


def _assert_execution_source(checkout: Path) -> None:
    if checkout not in _source_digests:
        raise RunnerError("fonte de execução não autenticada por snapshot")
    sha, digests = _source_digests[checkout]
    validate_checkout(str(checkout), sha)
    for relative, digest in digests.items():
        path = checkout / relative
        if path.is_symlink() or not path.is_file() or _sha256(path) != digest:
            raise RunnerError("fonte de execução mudou após materialização do SHA")


def _collect(
    checkout: Path,
    args: list[str],
    log_path: Path,
    *,
    mutation_nodeid: str | None = None,
    env: dict[str, str] | None = None,
) -> set[str]:
    evidence = log_path.with_suffix(".execution.json")
    _collected_items.pop(log_path, None)
    evidence.unlink(missing_ok=True)
    log_path.unlink(missing_ok=True)
    private = Path(tempfile.mkdtemp(prefix="maezo-private-collection-")).resolve()
    raw_evidence = private / "execution.json"
    raw_log = private / "collection.log"
    payload: dict[str, Any] = {}
    return_code: int | None = None
    validated = False
    try:
        _assert_execution_source(checkout)
        for path in (raw_evidence, raw_log):
            path.touch(mode=0o600)
        result = _run(
            _pytest_command(
                checkout,
                *args,
                "--collect-only",
                "-q",
                evidence_path=raw_evidence,
                mutation_nodeid=mutation_nodeid,
            ),
            cwd=checkout,
            env=env,
            timeout=180,
            log_path=raw_log,
        )
        return_code = result.returncode
        payload = json.loads(raw_evidence.read_text()) if raw_evidence.exists() else {}
        if return_code != 0:
            raise RunnerError(f"coleta pytest falhou (rc={return_code})")
        _assert_execution_source(checkout)
        nodeids = [item["nodeid"] for item in payload["collection"]]
        if (
            not nodeids
            or len(set(nodeids)) != len(nodeids)
            or payload.get("finished") is not True
            or payload.get("pytest_exitstatus") != return_code
            or payload.get("reports") != []
            or payload.get("schema") != _evidence_api(checkout).SCHEMA_VERSION
        ):
            raise RunnerError("coleta vazia, duplicada, interrompida ou schema divergente")
        validated = True
    except Exception as exc:
        digest = hashlib.sha256(str(exc).encode()).hexdigest()
        raise RunnerError(f"coleta pytest falhou: {type(exc).__name__} sha256={digest}") from None
    finally:
        with _cleanup_signal_mask():
            try:
                if validated:
                    _publish_execution_json(raw_evidence, evidence)
                else:
                    _json_write(
                        evidence,
                        {
                            "finished": False,
                            "pytest_exitstatus": return_code,
                            "error": "Coleta sem validacao completa; original retido da publicacao",
                            "evidence_sha256": _sha256(raw_evidence) if raw_evidence.exists() else None,
                        },
                    )
                # Na coleta ainda não há XML/propriedades do corpo. Nenhuma
                # narrativa livre do console possui contexto para publicação.
                summary = {
                    "pytest_return_code": return_code,
                    "collection_validated": validated,
                    "collected": len(payload.get("collection", [])) if validated else 0,
                    "log_sha256": _sha256(raw_log) if raw_log.exists() else None,
                    "diagnostic": "Diagnostico pytest retido da publicacao",
                }
                _json_write(log_path, summary)
            except BaseException as exc:
                evidence.unlink(missing_ok=True)
                log_path.unlink(missing_ok=True)
                if isinstance(exc, Exception):
                    digest = hashlib.sha256(str(exc).encode()).hexdigest()
                    raise RunnerError(
                        f"publicacao da coleta recusada: {type(exc).__name__} sha256={digest}"
                    ) from None
                raise
            finally:
                for public in (evidence, log_path):
                    public.with_name(f".{public.name}.{os.getpid()}.tmp").unlink(missing_ok=True)
                shutil.rmtree(private)
    _collected_items[log_path] = payload["collection"]
    return set(nodeids)


def _invalidate_mutation_selection(results_dir: Path) -> None:
    """Retira somente a selecao propria anterior, sem varrer outros artefatos."""
    log = results_dir / "collect-mutation.log"
    _collected_items.pop(log, None)
    for path in (results_dir / "mutation-selection.json", log, log.with_suffix(".execution.json")):
        path.unlink(missing_ok=True)
        path.with_name(f".{path.name}.{os.getpid()}.tmp").unlink(missing_ok=True)


def _collect_mutation(
    checkout: Path, nodeid: str, inactive_items: list[dict[str, Any]], results_dir: Path
) -> list[dict[str, Any]]:
    _invalidate_mutation_selection(results_dir)
    _, _, mutation = _mutation_selection(nodeid)
    candidates = [item for item in inactive_items if item.get("nodeid") == nodeid]
    if len(candidates) != 1:
        raise RunnerError("companion nao aparece exatamente uma vez no manifest")
    inactive = candidates[0]
    declaration = inactive.get("inactive_companion")
    if (
        not declaration
        or declaration.get("mutation") != mutation
        or declaration.get("obligation") != "separate_opt_in_RED_required"
        or inactive.get("xfail") is not None
    ):
        raise RunnerError("companion sem declaracao canonica autenticada")
    log = results_dir / "collect-mutation.log"
    nodeids = _collect(checkout, [nodeid], log, mutation_nodeid=nodeid)
    active = _collected_items[log]
    if nodeids != {nodeid} or active != [{**inactive, "inactive_companion": None}]:
        raise RunnerError("coleta ativa diverge da identidade/fonte/guarda canonica")
    _json_write(
        results_dir / "mutation-selection.json",
        {
            "nodeid": nodeid,
            "mutation": mutation,
            "inactive_declaration": declaration,
            "active_items": active,
            "oracle_review_required": True,
        },
    )
    return active


def _dependency_evidence(checkout: Path, nodeids: set[str], pattern: re.Pattern[str]) -> list[str]:
    files = sorted({nodeid.split("::", 1)[0] for nodeid in nodeids})
    return [relative for relative in files if pattern.search(_code_signals(checkout / relative))]


def _code_signals(path: Path) -> str:
    """Retorna nomes e strings executáveis, excluindo comentários e docstrings."""
    source = path.read_text(errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstring_nodes.add(id(body[0].value))
    signals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            signals.append(node.id)
        elif isinstance(node, ast.Attribute):
            signals.append(node.attr)
        elif isinstance(node, ast.alias):
            signals.append(node.name)
        elif (
            isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes
        ):
            signals.append(node.value)
    return "\n".join(signals)


def _dependencies_for(
    checkout: Path,
    suite_name: str,
    nodeids: set[str],
    *,
    engine_pattern: re.Pattern[str],
    kafka_pattern: re.Pattern[str],
    hapi_pattern: re.Pattern[str],
) -> dict[str, object]:
    engine_evidence = _dependency_evidence(checkout, nodeids, engine_pattern)
    kafka_evidence = _dependency_evidence(checkout, nodeids, kafka_pattern)
    hapi_evidence = _dependency_evidence(checkout, nodeids, hapi_pattern)
    engine_required = suite_name == "core" or bool(engine_evidence)
    # Quando CIB é necessário, a fundação segue a ordem operacional exigida:
    # Postgres+Kafka prontos antes do engine, mesmo se o teste não usa Kafka diretamente.
    kafka_required = suite_name == "core" or engine_required or bool(kafka_evidence)
    return {
        "postgres_required": True,
        "engine_required": engine_required,
        "engine_evidence": engine_evidence,
        "kafka_required": kafka_required,
        "kafka_evidence": kafka_evidence,
        "hapi_required": bool(hapi_evidence),
        "hapi_evidence": hapi_evidence,
    }


def discover(checkout: Path, results_dir: Path, *, imported_module: str) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    _invalidate_mutation_selection(results_dir)
    discovery_path = results_dir / "discovery.json"
    discovery_path.unlink(missing_ok=True)
    # Uma nova descoberta invalida a tentativa inteira, mesmo se a primeira
    # coleta falhar e as seleções seguintes nunca chegarem a executar.
    for label in ("integration", "integration-dir", "core", "chaos", "db-unit", "root-fixture"):
        path = results_dir / f"collect-{label}.log"
        _collected_items.pop(path, None)
        path.unlink(missing_ok=True)
        path.with_suffix(".execution.json").unlink(missing_ok=True)
    integration = _collect(
        checkout,
        ["tests", "-m", "integration"],
        results_dir / "collect-integration.log",
    )
    integration_dir = _collect(
        checkout,
        ["tests/integration"],
        results_dir / "collect-integration-dir.log",
    )
    core = _collect(
        checkout,
        ["tests/integration", "-m", "integration and not chaos"],
        results_dir / "collect-core.log",
    )
    chaos = _collect(
        checkout,
        ["tests/integration/chaos", "-m", "integration and chaos"],
        results_dir / "collect-chaos.log",
    )
    db_unit = _collect(
        checkout,
        ["tests/unit", "-m", "integration"],
        results_dir / "collect-db-unit.log",
    )
    # Segunda coleta do MESMO escopo, com o opt-in declarado ligado. Serve apenas para
    # DISTINGUIR "o ficheiro existe mas todos os seus casos foram desselecionados por um
    # mecanismo DECLARADO" de "o ficheiro nao produz nodeid nenhum". Sem isto, a desselecao
    # `root_fixture` (suites cuja unica coordenada e' um fixture PRIVADO do ROOT — ver
    # tests/integration/conftest.py e a cerca tests/unit/ci/test_root_fixture_deselection.py)
    # era indistinguivel de um ficheiro de teste vazio/partido, e a descoberta abortava.
    # A distincao e' MEDIDA, nao declarada: o ficheiro tem de voltar a produzir nodeids sob o
    # opt-in. Um ficheiro vazio sob AMBAS as coletas continua a ser erro (fail-closed).
    root_fixture_optin = _collect(
        checkout,
        ["tests/integration"],
        results_dir / "collect-root-fixture.log",
        env={**os.environ, ROOT_FIXTURE_OPT_IN_ENV: "1"},
    )
    identities = _collected_items[results_dir / "collect-integration.log"]
    test_files = {
        path.relative_to(checkout).as_posix() for path in (checkout / "tests/integration").rglob("test_*.py")
    }
    collected_files = {nodeid.split("::", 1)[0] for nodeid in integration_dir}
    unmarked = sorted(integration_dir - integration)
    overlap = sorted((core & chaos) | (core & db_unit) | (chaos & db_unit))
    partition = core | chaos | db_unit
    uncovered = sorted(integration - partition)
    unexpected = sorted(partition - integration)
    optin_files = {nodeid.split("::", 1)[0] for nodeid in root_fixture_optin}
    absent_files = test_files - collected_files
    # Desselecionado por mecanismo declarado: nada na coleta normal, mas casos reais sob o opt-in.
    deselected_root_fixture_files = sorted(absent_files & optin_files)
    # Genuinamente sem nodeid nenhum, com ou sem opt-in — continua a abortar a descoberta.
    missing_files = sorted(absent_files - optin_files)
    required_families = {
        "lgpd": sorted(nodeid for nodeid in integration if "test_sp_op_lgpd_dsr_001.py::" in nodeid),
        "escalation": sorted(nodeid for nodeid in integration if "test_sp_op_escalation_001.py::" in nodeid),
    }

    engine_pattern = re.compile(
        r"\b(?:ENGINE_REST_URL|CIBSEVEN_BASE_URL|resolve_engine_rest_url)\b|"
        r"https?://(?:localhost|127\.0\.0\.1):(?:8080|18080)/engine-rest"
    )
    kafka_pattern = re.compile(r"\b(?:KAFKA_BOOTSTRAP_SERVERS|AioKafkaProducer|AIOKafkaProducer|aiokafka)\b")
    hapi_pattern = re.compile(r"\b(?:FHIR_BASE_URL|HAPI_FHIR_BASE_URL)\b|localhost:8081|/fhir/")
    suite_dependencies: dict[str, dict[str, object]] = {}
    execution_manifest: list[dict[str, Any]] = []
    for suite_name, nodeids in (("core", core), ("chaos", chaos), ("db-unit", db_unit)):
        suite_dependencies[suite_name] = _dependencies_for(
            checkout,
            suite_name,
            nodeids,
            engine_pattern=engine_pattern,
            kafka_pattern=kafka_pattern,
            hapi_pattern=hapi_pattern,
        )
        files = sorted({nodeid.split("::", 1)[0] for nodeid in nodeids})
        for test_file in files:
            file_nodeids = {nodeid for nodeid in nodeids if nodeid.startswith(f"{test_file}::")}
            execution_manifest.append(
                {
                    "suite": suite_name,
                    "test_file": test_file,
                    "expected_count": len(file_nodeids),
                    "nodeids": sorted(file_nodeids),
                    "items": [item for item in identities if item["nodeid"] in file_nodeids],
                    "dependencies": _dependencies_for(
                        checkout,
                        suite_name,
                        file_nodeids,
                        engine_pattern=engine_pattern,
                        kafka_pattern=kafka_pattern,
                        hapi_pattern=hapi_pattern,
                    ),
                }
            )

    payload: dict[str, Any] = {
        "generated_at": _now(),
        "evidence_schema": _evidence_api(checkout).SCHEMA_VERSION,
        "evidence_module_sha256": _evidence_source(checkout)[2],
        "evidence_module": str(checkout / EVIDENCE_RELATIVE),
        "checkout": checkout.as_posix(),
        "sha": _git(checkout, "rev-parse", "HEAD"),
        "maezo_import": imported_module,
        "integration_count": len(integration),
        "integration_dir_count": len(integration_dir),
        "core_count": len(core),
        "chaos_count": len(chaos),
        "db_unit_count": len(db_unit),
        "integration_nodeids": sorted(integration),
        "core_nodeids": sorted(core),
        "chaos_nodeids": sorted(chaos),
        "db_unit_nodeids": sorted(db_unit),
        "unmarked_nodeids": unmarked,
        "overlap_nodeids": overlap,
        "uncovered_nodeids": uncovered,
        "unexpected_nodeids": unexpected,
        "test_files": sorted(test_files),
        "missing_test_files": missing_files,
        "deselected_root_fixture_files": deselected_root_fixture_files,
        "required_families": required_families,
        "suite_dependencies": suite_dependencies,
        "execution_manifest": execution_manifest,
    }
    failures: list[str] = []
    if not integration:
        failures.append("coleta canônica pytest tests -m integration vazia")
    if unmarked:
        failures.append("há testes de integração sem marker integration")
    if overlap or uncovered or unexpected:
        failures.append("partição core/chaos/db-unit não cobre a coleção integration exatamente uma vez")
    if missing_files:
        failures.append("há arquivos test_*.py sem nodeid coletado")
    if not root_fixture_optin >= integration_dir:
        failures.append("o opt-in root_fixture perdeu casos que a coleta normal coletou")
    if not all(required_families.values()):
        failures.append("famílias obrigatórias LGPD/escalation não foram coletadas")
    manifest_nodeids = {str(nodeid) for entry in execution_manifest for nodeid in entry["nodeids"]}
    if manifest_nodeids != integration or any(not entry["expected_count"] for entry in execution_manifest):
        failures.append("manifest por arquivo está vazio ou não cobre a coleta integration exatamente")
    payload["validation_errors"] = failures
    # O retorno e o cache são os originais privados usados pela execução.
    # Só a cópia publicada pode reter narrativas sem contexto pré-corpo.
    try:
        _json_write(discovery_path, _project_narratives(payload, _withhold_narrative))
    except BaseException as exc:
        discovery_path.unlink(missing_ok=True)
        if isinstance(exc, Exception):
            digest = hashlib.sha256(str(exc).encode()).hexdigest()
            raise RunnerError(
                f"publicacao discovery recusada: {type(exc).__name__} sha256={digest}"
            ) from None
        raise
    finally:
        discovery_path.with_name(f".{discovery_path.name}.{os.getpid()}.tmp").unlink(missing_ok=True)
    if failures:
        raise RunnerError("descoberta incompleta: " + "; ".join(failures))
    return payload


@dataclass
class EngineLock:
    path: Path
    token: str
    owner: dict[str, object]
    acquired: bool = False

    @classmethod
    def create(cls, path: Path, *, checkout: str, sha: str, suite: str) -> EngineLock:
        token = uuid.uuid4().hex
        owner: dict[str, object] = {
            "pid": os.getpid(),
            "token": token,
            "checkout": checkout,
            "sha": sha,
            "suite": suite,
            "project": PROJECT,
            "checkpoint": "acquiring",
            "created_at": _now(),
            "updated_at": _now(),
        }
        return cls(path=path, token=token, owner=owner)

    @property
    def owner_path(self) -> Path:
        return self.path / "owner.json"

    def acquire(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(timeout, 0)
        while True:
            try:
                self.path.mkdir(mode=0o700)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(min(1.0, max(deadline - time.monotonic(), 0.05)))
                continue
            self.acquired = True
            self.update("acquired")
            return True

    def owns(self) -> bool:
        if not self.acquired:
            return False
        try:
            disk = json.loads(self.owner_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        if not isinstance(disk, dict):
            return False
        return disk.get("pid") == os.getpid() and disk.get("token") == self.token

    def update(self, checkpoint: str, **fields: object) -> bool:
        if checkpoint != "acquired" and not self.owns():
            return False
        self.owner.update(fields)
        self.owner["checkpoint"] = checkpoint
        self.owner["updated_at"] = _now()
        # Token de posse é estado privado de controle (diretório 0700), não credencial
        # publicável. Somente este caminho restaura o token criado pelo próprio dono.
        private_owner = _safe_data(self.owner)
        private_owner["token"] = self.token
        _atomic_json_write(self.owner_path, private_owner)
        return True

    def release(self) -> bool:
        global _process_owner
        if _pending_groups or self.owner.get("subprocess_quiescent") is False:
            self.update("subprocess_cleanup_unconfirmed", pending_pgids=sorted(_pending_groups))
            return False
        if not self.owns() or any(owner is self for owner in _source_leases.values()):
            return False
        self.owner_path.unlink()
        try:
            self.path.rmdir()
        except OSError:
            return False
        self.acquired = False
        if _process_owner is self:
            _process_owner = None
        return True


def _compose_base(checkout: Path) -> list[str]:
    # Also covers readiness/schema commands that call _run directly with this argv.
    _assert_execution_source(checkout)
    owner = _source_leases.get(checkout)
    if owner is not None and (
        not owner.owns() or _pending_groups or owner.owner.get("subprocess_quiescent") is False
    ):
        raise RunnerError("Compose recusado sem posse e quiescência da fonte")
    return [
        "docker",
        "--context",
        DOCKER_CONTEXT,
        "compose",
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(checkout),
        "-f",
        str(checkout / "docker-compose.yml"),
        "-f",
        str(checkout / "scripts/dev/docker-compose.engine-integration.yml"),
        "--project-name",
        PROJECT,
        "--profile",
        "core",
    ]


def _validate_docker_context(checkout: Path, env: dict[str, str]) -> str:
    result = _checked(
        [
            "docker",
            "--context",
            DOCKER_CONTEXT,
            "context",
            "inspect",
            DOCKER_CONTEXT,
            "--format",
            "{{json .Endpoints.docker.Host}}",
        ],
        cwd=checkout,
        env=env,
        timeout=30,
    )
    try:
        endpoint = str(json.loads(result.stdout.strip()))
    except (json.JSONDecodeError, TypeError) as exc:
        raise RunnerError(f"endpoint Docker inválido para contexto {DOCKER_CONTEXT}") from exc
    if not endpoint.startswith("unix://"):
        raise RunnerError(f"contexto Docker {DOCKER_CONTEXT} não aponta para socket Unix local")
    socket_path = Path(endpoint.removeprefix("unix://")).expanduser().resolve()
    home = Path(env.get("HOME", "")).expanduser().resolve()
    allowed_root = (home / ".colima").resolve()
    if socket_path != Path("/var/run/docker.sock") and allowed_root not in socket_path.parents:
        raise RunnerError(f"contexto Docker {DOCKER_CONTEXT} aponta fora do Colima local")
    return endpoint


def _compose(
    checkout: Path,
    args: list[str],
    *,
    env: dict[str, str],
    timeout: float = 300,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return _checked(_compose_base(checkout) + args, cwd=checkout, env=env, timeout=timeout, log_path=log_path)


def _poll(label: str, probe: Any, *, timeout: float, interval: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            if probe():
                return
        except Exception as exc:  # readiness guarda o último erro e falha ao fim do prazo
            last = str(exc)
        time.sleep(min(interval, max(deadline - time.monotonic(), 0)))
    raise RunnerError(f"timeout aguardando {label}: {last}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise RunnerError("redirecionamento HTTP recusado")


def _local_opener(url: str) -> urllib.request.OpenerDirector:
    from urllib.parse import urlsplit

    endpoint = urlsplit(url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"localhost", "127.0.0.1"}
        or endpoint.port not in {18080, 18081}
        or endpoint.username
        or endpoint.password
    ):
        raise RunnerError("endpoint HTTP fora das portas locais autorizadas")
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _http_json(url: str, *, timeout: float = 10) -> Any:
    try:
        with _local_opener(url).open(url, timeout=timeout) as response:  # URL local fixa
            if response.status // 100 != 2:
                raise RunnerError(f"HTTP {response.status} em {url}")
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RunnerError(f"falha HTTP em {url}: {exc}") from exc


def _http_ready(url: str) -> bool:
    try:
        with _local_opener(url).open(url, timeout=5) as response:  # URL local fixa
            return int(response.status) // 100 == 2
    except (urllib.error.URLError, TimeoutError):
        return False


def _start_stack(
    checkout: Path,
    dependencies: dict[str, object],
    env: dict[str, str],
    results_dir: Path,
    lock: EngineLock,
) -> list[str]:
    _compose(checkout, ["down", "-v", "--remove-orphans"], env=env, log_path=results_dir / "fresh-down.log")
    lock.update("fresh_project_removed")
    if not dependencies["kafka_required"]:
        _compose(checkout, ["up", "-d", "postgres"], env=env, log_path=results_dir / "start-postgres.log")
        services = ["postgres"]
    else:
        _compose(
            checkout,
            ["up", "-d", "postgres", "kafka"],
            env=env,
            log_path=results_dir / "start-foundation.log",
        )
        services = ["postgres", "kafka"]

    def postgres_ready() -> bool:
        result = _run(
            _compose_base(checkout) + ["exec", "-T", "postgres", "pg_isready", "-U", "maezo", "-d", "maezo"],
            cwd=checkout,
            env=env,
            timeout=15,
        )
        return result.returncode == 0

    _poll("Postgres", postgres_ready, timeout=90)
    if dependencies["kafka_required"]:

        def kafka_ready() -> bool:
            result = _run(
                _compose_base(checkout)
                + [
                    "exec",
                    "-T",
                    "kafka",
                    "kafka-broker-api-versions",
                    "--bootstrap-server",
                    "kafka:29092",
                ],
                cwd=checkout,
                env=env,
                timeout=20,
            )
            return result.returncode == 0

        _poll("Kafka no listener interno kafka:29092", kafka_ready, timeout=120, interval=5)
        lock.update("foundation_ready")
    if dependencies["engine_required"] or dependencies["hapi_required"]:
        engine_services = ["cibseven"]
        if dependencies["hapi_required"]:
            engine_services.append("hapi-fhir")
        _compose(
            checkout,
            ["up", "-d", *engine_services],
            env=env,
            log_path=results_dir / "start-engine.log",
        )
        services.extend(engine_services)
        _poll("CIB Seven", lambda: _http_ready(f"{ENGINE_URL}/version"), timeout=360, interval=5)
        if dependencies["hapi_required"]:
            _poll("HAPI FHIR", lambda: _http_ready(HAPI_URL), timeout=360, interval=5)
        lock.update("engine_ready")
    else:
        lock.update("selected_services_ready")
    return services


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _definition_keys(path: Path) -> list[tuple[str, str]]:
    root = ET.parse(path).getroot()
    kind = "process" if path.suffix == ".bpmn" else "decision"
    return [
        (kind, str(element.attrib["id"]))
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == kind and element.attrib.get("id")
    ]


def _deploy_and_verify(checkout: Path, env: dict[str, str], results_dir: Path) -> dict[str, Any]:
    _assert_execution_source(checkout)
    deploy_log = results_dir / "deployment.log"
    _checked(
        _uv_python(
            checkout,
            "-m",
            "maezo.platform.deploy",
            "--engine-url",
            ENGINE_URL,
            "--spec-dir",
            str(checkout / "spec"),
        ),
        cwd=checkout,
        env=env,
        timeout=300,
        log_path=deploy_log,
    )
    records: list[dict[str, Any]] = []
    for path in sorted((checkout / "spec/processes").glob("*/*.bpmn")) + sorted(
        (checkout / "spec/processes").glob("*/*.dmn")
    ):
        source_hash = _sha256(path)
        for kind, key in _definition_keys(path):
            definition = _http_json(f"{ENGINE_URL}/{kind}-definition/key/{key}")
            definition_id = str(definition["id"])
            xml_field = "bpmn20Xml" if kind == "process" else "dmnXml"
            xml_payload = _http_json(f"{ENGINE_URL}/{kind}-definition/{definition_id}/xml")
            engine_xml = str(xml_payload[xml_field]).encode()
            engine_hash = hashlib.sha256(engine_xml).hexdigest()
            record = {
                "kind": kind,
                "key": key,
                "definition_id": definition_id,
                "deployment_id": definition.get("deploymentId"),
                "version": definition.get("version"),
                "resource": path.relative_to(checkout).as_posix(),
                "source_sha256": source_hash,
                "engine_xml_sha256": engine_hash,
                "exact_match": engine_hash == source_hash,
            }
            records.append(record)
    mismatches = [record for record in records if not record["exact_match"]]
    if not records or mismatches:
        _json_write(
            results_dir / "deployment-provenance.json", {"definitions": records, "mismatches": mismatches}
        )
        raise RunnerError(
            f"proveniência de deploy inválida: definitions={len(records)}, mismatches={len(mismatches)}"
        )
    payload = {
        "verified_at": _now(),
        "engine": _http_json(f"{ENGINE_URL}/version"),
        "declared_image": "cibseven/cibseven:2.1.0",
        "checkout_sha": _git(checkout, "rev-parse", "HEAD"),
        "compose_sha256": _sha256(checkout / "docker-compose.yml"),
        "override_sha256": _sha256(checkout / "scripts/dev/docker-compose.engine-integration.yml"),
        "definitions": records,
        "mismatches": [],
    }
    _json_write(results_dir / "deployment-provenance.json", payload)
    return payload


def _parse_outcomes(output: str) -> dict[str, int]:
    outcomes = {name: 0 for name in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")}
    pattern = re.compile(r"(?P<count>\d+) (?P<name>passed|failed|skipped|xfailed|xpassed|errors?)\b")
    for match in pattern.finditer(output):
        name = match.group("name")
        if name == "error":
            name = "errors"
        outcomes[name] = max(outcomes[name], int(match.group("count")))
    return outcomes


_LEGACY_MUTATION_REASON_HINT = re.compile(
    r"^(?:Skipped:\s*)?(?:only runs when|mutation-check only runs when) "
    r"MAEZO_CHAOS_MUTATE=[a-z0-9_]+\b"
)


def _junit_cases(path: Path) -> list[dict[str, str]]:
    """Leitor legado de apresentação; não autoriza resultado nem exceção de skip."""
    if not path.is_file():
        return []
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise RunnerError(f"JUnit inválido em {path}: {exc}") from exc
    cases: list[dict[str, str]] = []
    for case in root.iter("testcase"):
        status = "passed"
        reason = ""
        for child in case:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag in {"failure", "error"}:
                status = "failed" if tag == "failure" else "error"
                reason = child.attrib.get("message", "") or child.text or ""
                break
            if tag == "skipped":
                reason = child.attrib.get("message", "") or child.text or ""
                if child.attrib.get("type") == "pytest.xfail":
                    status = "xfailed"
                elif _LEGACY_MUTATION_REASON_HINT.match(reason):
                    status = "mutation_skipped"
                else:
                    status = "skipped"
        cases.append(
            {
                "node": "::".join(
                    part for part in (case.attrib.get("classname", ""), case.attrib.get("name", "")) if part
                ),
                "status": status,
                "reason": _redact_text(reason.strip()),
            }
        )
    return cases


def verified_result_code(
    pytest_return_code: int,
    *,
    actual_count: int,
    expected_count: int,
    passed_count: int,
    skipped_count: int,
    xfailed_count: int,
    xpassed_count: int,
    test_file: str,
) -> tuple[int, str | None]:
    errors: list[str] = []
    if actual_count != expected_count or expected_count <= 0:
        errors.append(
            f"JUnit executou {actual_count} casos; manifest esperava {expected_count} para {test_file}"
        )
    if skipped_count:
        errors.append(f"{skipped_count} skip inesperado impediu verificação em {test_file}")
    if xpassed_count:
        errors.append(f"{xpassed_count} XPASS inesperado em {test_file}")
    if actual_count > 0 and passed_count + xfailed_count == 0:
        errors.append(f"nenhum corpo de teste foi verificado em {test_file}")
    if errors:
        return pytest_return_code or 1, "; ".join(errors)
    return pytest_return_code, None


def _publication_context(source: Path) -> Callable[[str], str]:
    """Propaga somente valores privados conhecidos; o XML original é autoridade."""
    tree = ET.parse(source)
    secrets: set[str] = set()

    def remember(value: str) -> None:
        if value:
            secrets.update((value, repr(value)[1:-1], json.dumps(value)[1:-1]))

    for prop in tree.iter("property"):
        if _SECRET_KEY.fullmatch(prop.get("name", "")):
            remember(prop.get("value", ""))
    for case in tree.iter("testcase"):
        for key in ("classname", "name"):
            _, marker, parameter = case.get(key, "").partition("[")
            if marker:
                parameter = parameter.removesuffix("]")
                remember(parameter)
                # Pytest escapa IDs Unicode/controle; decodifica escapes individuais
                # sem reinterpretar caracteres Unicode que já chegaram literais.
                remember(
                    re.sub(
                        r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[\\abfnrtv])",
                        lambda match: codecs.decode(match[0], "unicode_escape"),
                        parameter,
                    )
                )
    literals = (
        re.compile("|".join(re.escape(value) for value in sorted(secrets, key=len, reverse=True)))
        if secrets
        else None
    )

    def diagnostic(value: str) -> str:
        safe = literals.sub(lambda _: "<redacted>", value) if literals else value
        return _redact_text(safe)

    return diagnostic


def _project_narratives(payload: Any, diagnostic: Callable[[str], str], *, narrative: bool = False) -> Any:
    """Preserva autoridade estrutural/identidades; projeta folhas narrativas."""
    if isinstance(payload, dict):
        return {
            key: _project_narratives(
                value,
                diagnostic,
                narrative=key
                in {
                    "skip_reason",
                    "wasxfail",
                    "errors",
                    "collection_error",
                    "reason",
                },
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_project_narratives(value, diagnostic, narrative=narrative) for value in payload]
    return diagnostic(payload) if narrative and isinstance(payload, str) else payload


def _withhold_narrative(value: str) -> str:
    """Sem corpo/XML, nenhuma razão livre é sabidamente pública."""
    return "<redacted: contexto indisponivel>" if value else value


def _publish_execution_json(
    source: Path, destination: Path, diagnostic: Callable[[str], str] | None = None
) -> None:
    try:
        payload = json.loads(source.read_text())
    except (OSError, ValueError):
        payload = {"finished": False, "error": "JSON bruto inválido retido da publicação"}
    if diagnostic is not None:
        payload = _project_narratives(payload, diagnostic)
    else:
        payload = _project_narratives(payload, _withhold_narrative)
    if "collection" in payload:
        payload["collection"] = _project_narratives(payload["collection"], _withhold_narrative)
    try:
        _json_write(destination, payload)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        destination.with_name(f".{destination.name}.{os.getpid()}.tmp").unlink(missing_ok=True)


def _publish_xml(
    source: Path,
    destination: Path,
    *,
    identity_projector: Callable[[str, str], dict[str, str]] | None = None,
    diagnostic_log: tuple[Path, Path] | None = None,
) -> None:
    def publish_log(project: Callable[[str], str] | None = None) -> None:
        if diagnostic_log is None:
            return
        private_log, public_log = diagnostic_log
        if not private_log.exists():
            return
        raw = private_log.read_text()
        # XML ausente/parcial não fornece contexto para publicar narrativa livre.
        safe = (
            project(raw)
            if project is not None
            else "Diagnostico pytest retido: XML sem contexto valido de propriedades; "
            f"sha256={hashlib.sha256(raw.encode()).hexdigest()}\n"
        )
        public_log.write_text(safe)

    if not source.exists():
        publish_log()
        return
    try:
        tree = ET.parse(source)
        diagnostic = _publication_context(source)
        for prop in tree.iter("property"):
            if _SECRET_KEY.fullmatch(prop.get("name", "")):
                prop.set("value", "<redacted>")
        for node in tree.iter():
            if node.tag == "testcase":
                if identity_projector is None:
                    # Leitor legado de apresentação sem autoridade para publicar IDs.
                    node.attrib.pop("classname", None)
                    node.attrib.pop("name", None)
                else:
                    identity = identity_projector(node.get("classname", ""), node.get("name", ""))
                    node.set("classname", identity["classname"])
                    node.set("name", identity["name"])
                    node.set("maezo_identity_sha256", identity["junit_identity_sha256"])
            for key, value in tuple(node.attrib.items()):
                if node.tag == "property" and key == "value" and value == "<redacted>":
                    continue

                if node.tag == "testcase" and key in {"classname", "name", "maezo_identity_sha256"}:
                    continue
                if key in {"tests", "errors", "failures", "skipped", "time", "timestamp", "line"}:
                    node.set(key, _redact_text(value))
                else:
                    node.set(key, diagnostic(value))
            if node.text:
                node.text = diagnostic(node.text)
            if node.tail:
                node.tail = diagnostic(node.tail)
        tree.write(destination, encoding="utf-8", xml_declaration=True)
        publish_log(diagnostic)
    except ET.ParseError:
        destination.write_text("<!-- XML bruto inválido retido da publicação -->\n")
        publish_log()


def _run_pytest(
    checkout: Path,
    suite: str,
    test_file: str,
    env: dict[str, str],
    results_dir: Path,
    expected_count: int,
    expected_items: list[dict[str, Any]] | None = None,
    *,
    mutation_nodeid: str | None = None,
) -> dict[str, Any]:
    markers = {
        "chaos": "integration and chaos",
        "core": "integration and not chaos",
        "db-unit": "integration",
    }
    if mutation_nodeid is not None:
        selected_suite, selected_file, _ = _mutation_selection(mutation_nodeid)
        if (suite, test_file, expected_count) != (selected_suite, selected_file, 1) or expected_items is None:
            raise RunnerError("execucao de mutacao exige manifest seletivo autenticado")
    selection = [mutation_nodeid or test_file, "-m", markers[suite]]
    results_dir.mkdir(parents=True, exist_ok=True)
    for name in ("suite-results.json", "pytest-execution.json", "junit.xml", "pytest.log"):
        (results_dir / name).unlink(missing_ok=True)
    if expected_items is None:
        _collect(checkout, selection, results_dir / "collect-selected.log")
        expected_items = _collected_items[results_dir / "collect-selected.log"]
    raw_dir = Path(tempfile.mkdtemp(prefix="maezo-private-junit-")).resolve()
    raw_xml = raw_dir / "junit.xml"
    raw_log = raw_dir / "pytest.log"
    public_log = results_dir / "pytest.log"
    public_log.unlink(missing_ok=True)
    raw_evidence = raw_dir / "execution.json"
    evidence_path = results_dir / "pytest-execution.json"
    evidence_path.unlink(missing_ok=True)
    junit = results_dir / "junit.xml"
    junit.unlink(missing_ok=True)
    command = _pytest_command(
        checkout,
        *selection,
        "-q",
        "-ra",
        "--durations=40",
        f"--junitxml={raw_xml}",
        evidence_path=raw_evidence,
        mutation_nodeid=mutation_nodeid,
    )
    evidence_api = _evidence_api(checkout)
    diagnostic: Callable[[str], str] | None = None
    try:
        # Os escritores filhos preservam estes modos desde a primeira escrita,
        # independentemente do umask herdado, sem alterar o umask global.
        for path in (raw_xml, raw_evidence, raw_log):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        result = _run(command, cwd=checkout, env=env, timeout=7200, log_path=raw_log)
        _assert_execution_source(checkout)
        evidence = json.loads(raw_evidence.read_text() or "{}") if raw_evidence.exists() else {}
        # Valida identidades completas antes de projetar artefatos seguros.
        try:
            validation = evidence_api.validate_execution(raw_xml, evidence, expected_items, result.returncode)
        except Exception as exc:
            # Nao publicar str(exc): pode conter valores privados de um report malformado.
            raise RunnerError(f"validador de execucao falhou fechado: {type(exc).__name__}") from None
    finally:
        with _cleanup_signal_mask():
            try:
                try:
                    diagnostic = _publication_context(raw_xml)
                except (OSError, ET.ParseError):
                    diagnostic = None
                _publish_xml(
                    raw_xml,
                    junit,
                    identity_projector=evidence_api.public_junit_identity,
                    diagnostic_log=(raw_log, public_log),
                )
                if raw_evidence.exists() and raw_evidence.stat().st_size:
                    _publish_execution_json(raw_evidence, evidence_path, diagnostic)
            except BaseException:
                for public in (junit, public_log, evidence_path):
                    public.unlink(missing_ok=True)
                raise
            finally:
                shutil.rmtree(raw_dir)
    if diagnostic is None:
        validation = _project_narratives(validation, lambda _: "<redacted: contexto indisponivel>")
        validation["errors"].append("publicacao sem contexto XML completo")
        validation["return_code"] = result.returncode or 1
    else:
        validation = _project_narratives(validation, diagnostic)
    if len(expected_items) != expected_count:
        validation["errors"].append("manifest de identidades diverge da contagem esperada")
        validation["return_code"] = result.returncode or 1
    payload = {
        "finished_at": _now(),
        "suite": suite,
        "test_file": test_file,
        **validation,
        "expected_collected": expected_count,
        "collection_error": "; ".join(validation["errors"]) or None,
        "outcomes": _parse_outcomes(result.stdout + result.stderr),
        "junit_xml": junit.as_posix(),
        "pytest_log": (results_dir / "pytest.log").as_posix(),
        "execution_evidence": evidence_path.as_posix(),
        "pytest_return_code": result.returncode,
    }
    if mutation_nodeid is not None:
        payload.update(
            mutation_nodeid=mutation_nodeid,
            mutation=MUTATIONS[mutation_nodeid],
            oracle_review_required=True,
            positive_validator_return_code=validation["return_code"],
            # Nem PASS do mutante nem falha de infraestrutura constituem prova RED.
            return_code=result.returncode or 1,
        )
    suite_results = results_dir / "suite-results.json"
    try:
        _json_write(suite_results, payload)
    except BaseException:
        suite_results.unlink(missing_ok=True)
        raise
    finally:
        suite_results.with_name(f".{suite_results.name}.{os.getpid()}.tmp").unlink(missing_ok=True)
    return payload


def _runtime_env() -> dict[str, str]:
    env = _base_env()
    env.update(
        {
            "MAEZO_PG_HOST_PORT": PG_PORT,
            "MAEZO_PG_USER": "maezo",
            "MAEZO_PG_PASSWORD": "maezo",
            "MAEZO_PG_DB": "maezo",
            "DATABASE_URL": PG_DSN,
            "ENGINE_REST_URL": ENGINE_URL,
            "CIBSEVEN_BASE_URL": ENGINE_URL,
            "KAFKA_BOOTSTRAP_SERVERS": f"localhost:{KAFKA_PORT}",
            "FHIR_BASE_URL": "http://localhost:18081/fhir",
            "HAPI_FHIR_BASE_URL": "http://localhost:18081/fhir",
        }
    )
    env.update(dict.fromkeys(PINNED_DSN_ENV, PG_DSN))
    return env


def run_suite(args: argparse.Namespace) -> int:
    global _process_owner
    results_dir = Path(args.results_dir).expanduser().resolve()
    state: dict[str, Any] = {
        "started_at": _now(),
        "state": "preflight",
        "suite": args.suite,
        "test_file": args.test_file,
        "return_code": None,
        "project": PROJECT,
        "lock": LOCK_DIR.as_posix(),
    }
    _json_write(results_dir / "run-state.json", state)
    lock: EngineLock | None = None
    services: list[str] = []
    return_code = 1
    stack_touched = False
    env = _runtime_env()
    source_context = None
    cleanup_complete = False
    try:
        _invalidate_mutation_selection(results_dir)
        original, _ = validate_checkout(args.checkout, args.sha)
        source_context = execution_checkout(original, args.sha, results_dir)
        checkout, imported = source_context.__enter__()
        discovery_payload = discover(checkout, results_dir, imported_module=imported)
        requested_path = Path(args.test_file)
        if requested_path.is_absolute() or ".." in requested_path.parts:
            raise RunnerError("--test-file deve ser caminho relativo seguro dentro do CHECKOUT")
        test_file = requested_path.as_posix()
        manifest_matches = [
            entry
            for entry in discovery_payload["execution_manifest"]
            if entry["suite"] == args.suite and entry["test_file"] == test_file
        ]
        if len(manifest_matches) != 1:
            raise RunnerError(
                "arquivo/grupo não aparece exatamente uma vez no manifest: "
                f"suite={args.suite}, file={test_file}"
            )
        manifest_entry = dict(manifest_matches[0])
        mutation_nodeid = getattr(args, "mutation_nodeid", None)
        if mutation_nodeid is not None:
            selected_suite, selected_file, _ = _mutation_selection(mutation_nodeid)
            if (args.suite, test_file) != (selected_suite, selected_file):
                raise RunnerError("suite/arquivo diverge da selecao de mutacao")
            manifest_entry["items"] = _collect_mutation(
                checkout, mutation_nodeid, manifest_entry["items"], results_dir
            )
            manifest_entry["expected_count"] = 1
            state.update(mutation_nodeid=mutation_nodeid, mutation=MUTATIONS[mutation_nodeid])
        expected_count = int(manifest_entry["expected_count"])
        if expected_count <= 0:
            raise RunnerError(f"arquivo {test_file} não coletou testes na suíte {args.suite}")
        lock = EngineLock.create(
            LOCK_DIR,
            checkout=checkout.as_posix(),
            sha=args.sha,
            suite=f"{args.suite}:{test_file}",
        )
        if not lock.acquire(args.lock_timeout):
            return_code = BUSY_EXIT
            state.update(state="lock_busy", return_code=BUSY_EXIT, finished_at=_now())
            _json_write(results_dir / "run-state.json", state)
            return BUSY_EXIT
        _process_owner = lock
        _bind_execution_source(checkout, lock)
        lock.update("preflight_complete", results_dir=results_dir.as_posix())
        state["state"] = "lock_acquired"
        _json_write(results_dir / "run-state.json", state)
        docker_endpoint = _validate_docker_context(checkout, env)
        state.update(docker_context=DOCKER_CONTEXT, docker_endpoint=docker_endpoint)
        lock.update("docker_context_validated", docker_context=DOCKER_CONTEXT)
        dependencies = manifest_entry["dependencies"]
        stack_touched = True
        services = _start_stack(checkout, dependencies, env, results_dir, lock)
        if dependencies["engine_required"]:
            lock.update("deploying")
            _deploy_and_verify(checkout, env, results_dir)
            lock.update("deployment_verified")
        lock.update("pytest_running")
        suite_result = _run_pytest(
            checkout,
            args.suite,
            test_file,
            env,
            results_dir,
            expected_count,
            manifest_entry["items"],
            **({"mutation_nodeid": mutation_nodeid} if mutation_nodeid is not None else {}),
        )
        return_code = int(suite_result["return_code"])
        state["pytest_return_code"] = suite_result.get("pytest_return_code", return_code)
        state["state"] = "pytest_complete" if return_code == 0 else "pytest_failed"
    except (KeyboardInterrupt, RunnerInterrupted):
        return_code = 130
        state["state"] = "interrupted"
    except ProcessGroupCleanupError as exc:
        return_code = 1
        state["state"] = "subprocess_cleanup_unconfirmed"
        state["error"] = _redact_text(str(exc))
        print(f"ERRO: {_redact_text(str(exc))}", file=sys.stderr)
    except (RunnerError, subprocess.TimeoutExpired, OSError, KeyError, ValueError) as exc:
        return_code = 1
        state["state"] = "failed"
        state["error"] = _redact_text(str(exc))
        print(f"ERRO: {_redact_text(str(exc))}", file=sys.stderr)
    finally:
        state.update(return_code=return_code, services=services)
        _json_write(results_dir / "run-state.json", state)
        try:
            if lock is not None and lock.owns():
                if _pending_groups or lock.owner.get("subprocess_quiescent") is False:
                    return_code = 1
                    state.update(state="subprocess_cleanup_unconfirmed", return_code=1)
                    lock.update("subprocess_cleanup_unconfirmed", return_code=1, state=state["state"])
                elif not stack_touched:
                    _assert_execution_source(checkout)
                    cleanup_complete = True
                else:
                    outcome_before_teardown = state["state"]
                    # Nem o estado nem o rc durável são verdes durante a desmontagem.
                    # A persistência precede o primeiro efeito e é atômica contra sinais.
                    with _cleanup_signal_mask():
                        state.update(
                            state="teardown",
                            return_code=return_code or 1,
                            pytest_return_code=state.get("pytest_return_code", return_code),
                            teardown_started_at=_now(),
                        )
                        _json_write(results_dir / "run-state.json", state)
                        if not lock.update("teardown", return_code=state["return_code"], state="teardown"):
                            raise RunnerError("posse perdida antes do teardown")
                    try:
                        _compose(
                            checkout,
                            ["down", "-v", "--remove-orphans"],
                            env=env,
                            timeout=300,
                            log_path=results_dir / "teardown.log",
                        )
                    except (KeyboardInterrupt, RunnerInterrupted) as exc:
                        return_code = 130
                        state.update(state="teardown_interrupted", teardown_error=type(exc).__name__)
                        lock.update(
                            "teardown_interrupted", return_code=130, teardown_error=state["teardown_error"]
                        )
                    except (RunnerError, subprocess.TimeoutExpired, OSError) as exc:
                        return_code = return_code or 1
                        state.update(state="teardown_failed", teardown_error=_redact_text(str(exc)))
                        lock.update(
                            "teardown_failed", return_code=return_code, teardown_error=state["teardown_error"]
                        )
                    else:
                        _assert_execution_source(checkout)
                        if not lock.update("teardown_complete", return_code=return_code):
                            raise RunnerError("posse perdida ao confirmar teardown")
                        state.update(state=outcome_before_teardown, teardown_finished_at=_now())
                        cleanup_complete = True
                    if _pending_groups or lock.owner.get("subprocess_quiescent") is False:
                        return_code = 1
                        state["state"] = "subprocess_cleanup_unconfirmed"
                        lock.update("subprocess_cleanup_unconfirmed", return_code=1)
            elif lock is not None and lock.acquired:
                state.update(state="ownership_lost", ownership_lost=True)
                return_code = return_code or 1
        except (KeyboardInterrupt, RunnerInterrupted) as exc:
            return_code = 130
            state.update(state="cleanup_interrupted", teardown_error=type(exc).__name__)
        except (RunnerError, subprocess.TimeoutExpired, OSError) as exc:
            return_code = return_code or 1
            state.update(state="cleanup_failed", teardown_error=_redact_text(str(exc)))
        finally:
            try:
                if source_context is not None:
                    if cleanup_complete:
                        _source_cleanup_ready.add(checkout)
                    source_context.__exit__(None, None, None)
                if cleanup_complete and lock is not None and not lock.release():
                    state.update(
                        state="lock_release_failed",
                        lock_release_error="fonte retida, posse mudou ou arquivos inesperados na lease",
                    )
                    return_code = return_code or 1
            except (KeyboardInterrupt, RunnerInterrupted) as exc:
                return_code = 130
                state.update(state="source_cleanup_interrupted", error=type(exc).__name__)
            except (RunnerError, OSError) as exc:
                return_code = return_code or 1
                state.update(state="source_cleanup_failed", error=_redact_text(str(exc)))
            if state["state"] == "pytest_complete" and return_code == 0:
                state["state"] = "passed"
            state.update(return_code=return_code, services=services, finished_at=_now())
            _json_write(results_dir / "run-state.json", state)
    return return_code


def run_mutation(args: argparse.Namespace) -> int:
    suite, test_file, _ = _mutation_selection(args.nodeid)
    return run_suite(
        argparse.Namespace(
            checkout=args.checkout,
            sha=args.sha,
            results_dir=args.results_dir,
            lock_timeout=args.lock_timeout,
            suite=suite,
            test_file=test_file,
            mutation_nodeid=args.nodeid,
        )
    )


def lock_probe(args: argparse.Namespace) -> int:
    events = Path(args.events).resolve()
    lock = EngineLock.create(Path(args.lock_dir).resolve(), checkout="probe", sha="probe", suite="probe")
    if not lock.acquire(args.timeout):
        _append_event(events, "lock_busy")
        return BUSY_EXIT
    _append_event(events, "lock_acquired")
    return_code = 0
    try:
        while not Path(args.wait_for).exists():
            time.sleep(0.05)
    except (KeyboardInterrupt, RunnerInterrupted):
        return_code = 130
        lock.update("interrupted", return_code=return_code)
        _append_event(events, "interrupted", return_code=return_code)
    finally:
        if lock.owns():
            _append_event(events, "cleanup_permitted")
            if lock.release():
                _append_event(events, "lock_released")
    return return_code


def child_probe(args: argparse.Namespace) -> int:
    """Exercita supervisão de um filho real sem tocar Docker."""
    global _process_owner
    events = Path(args.events).resolve()
    lock = EngineLock.create(Path(args.lock_dir).resolve(), checkout="probe", sha="probe", suite="child")
    if not lock.acquire(0):
        _append_event(events, "lock_busy")
        return BUSY_EXIT
    _append_event(events, "lock_acquired")
    _process_owner = lock
    _append_event(events, "child_started")
    return_code = 1
    child_code = (
        "import os,pathlib,sys,time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "print('child-started', flush=True); time.sleep(3600)"
    )
    try:
        result = _run(
            [sys.executable, "-c", child_code, str(Path(args.child_pid_file).resolve())],
            cwd=Path.cwd(),
            timeout=args.timeout,
            log_path=Path(args.child_log).resolve(),
        )
        return_code = result.returncode
        if result.returncode == 124:
            lock.update("child_timeout", return_code=return_code)
            _append_event(events, "child_timeout", return_code=return_code)
    except (KeyboardInterrupt, RunnerInterrupted):
        return_code = 130
        lock.update("interrupted", return_code=return_code)
        _append_event(events, "interrupted", return_code=return_code)
    except (RunnerError, OSError) as exc:
        lock.update("subprocess_cleanup_unconfirmed", error=_redact_text(str(exc)))
        _append_event(events, "subprocess_cleanup_unconfirmed")
    finally:
        if lock.owns():
            if _pending_groups or lock.owner.get("subprocess_quiescent") is False:
                return_code = 1
                lock.update("subprocess_cleanup_unconfirmed", return_code=return_code)
            else:
                _append_event(events, "cleanup_permitted")
                if lock.release():
                    _append_event(events, "lock_released")
                else:
                    return_code = 1
    return return_code


def discovery_command(args: argparse.Namespace) -> int:
    try:
        checkout, _ = validate_checkout(args.checkout, args.sha)
        results_dir = Path(args.results_dir).expanduser().resolve()
        with execution_checkout(checkout, args.sha, results_dir) as (source, imported):
            discover(source, results_dir, imported_module=imported)
    except (RunnerError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"ERRO: {_redact_text(str(exc))}", file=sys.stderr)
        return USAGE_EXIT
    return 0


def _signal_handler(signum: int, _frame: object) -> None:
    raise RunnerInterrupted(f"signal {signum}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    discover_parser = subparsers.add_parser("discover", help="valida checkout e coleta todas as suítes")
    discover_parser.add_argument("--checkout", required=True)
    discover_parser.add_argument("--sha", required=True)
    discover_parser.add_argument("--results-dir", required=True)
    discover_parser.set_defaults(func=discovery_command)

    run_parser = subparsers.add_parser("run", help="executa exatamente uma suíte em stack nova")
    run_parser.add_argument("--checkout", required=True)
    run_parser.add_argument("--sha", required=True)
    run_parser.add_argument("--suite", choices=("core", "chaos", "db-unit"), required=True)
    run_parser.add_argument("--test-file", required=True)
    run_parser.add_argument("--results-dir", required=True)
    run_parser.add_argument("--lock-timeout", type=float, default=0.0)
    run_parser.set_defaults(func=run_suite)

    mutation_parser = subparsers.add_parser(
        "run-mutation", help="obtem evidencia negativa de um companion em stack nova"
    )
    mutation_parser.add_argument("--checkout", required=True)
    mutation_parser.add_argument("--sha", required=True)
    mutation_parser.add_argument("--nodeid", choices=tuple(MUTATIONS), required=True)
    mutation_parser.add_argument("--results-dir", required=True)
    mutation_parser.add_argument("--lock-timeout", type=float, default=0.0)
    mutation_parser.set_defaults(func=run_mutation)

    probe = subparsers.add_parser("lock-probe", help=argparse.SUPPRESS)
    probe.add_argument("--lock-dir", required=True)
    probe.add_argument("--events", required=True)
    probe.add_argument("--wait-for", required=True)
    probe.add_argument("--timeout", type=float, default=0.0)
    probe.set_defaults(func=lock_probe)

    child = subparsers.add_parser("child-probe", help=argparse.SUPPRESS)
    child.add_argument("--lock-dir", required=True)
    child.add_argument("--events", required=True)
    child.add_argument("--child-pid-file", required=True)
    child.add_argument("--child-log", required=True)
    child.add_argument("--timeout", type=float, required=True)
    child.set_defaults(func=child_probe)
    return parser


def main() -> int:
    signal.signal(signal.SIGTERM, _signal_handler)
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
