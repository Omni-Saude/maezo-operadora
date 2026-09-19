"""Prepare the default HOME cache; never relax historical offline admission.

Run only in the online CI preparation phase. Source pins, generated fixture locks
and every command/result are retained. Unexpected fixture resolution refuses
before fixture installation. No production lock or historical producer is edited.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.dev import run_engine_integration as process_groups  # noqa: E402

INPUTS = (
    "uv.lock",
    "pyproject.toml",
    ".python-version",
    ".github/workflows/ci.yml",
    "scripts/ci/prepare_offline_fixture_cache.py",
    "scripts/dev/run_engine_integration.py",
    "tests/unit/dev/test_historical_unit_recipe.py",
    "tests/unit/dev/test_historical_async_recipe.py",
)
FIXTURES = (
    ("tiny", ("pytest==9.1.1",)),
    ("tiny-async", ("pytest==9.1.1", "pytest-asyncio==1.4.0")),
)


class CommandRefusedError(ValueError):
    """Um comando uv da preparacao saiu com codigo != 0 (ou estourou o prazo).

    Herda de ValueError de proposito: todo chamador/guarda que ja tratava
    ValueError continua igual. O que muda e que a excecao CARREGA a causa real
    (label, argv, returncode, stderr) para o log do job poder nomea-la, em vez
    de so imprimir "refused" e esconder o detalhe no diretorio --output.
    """

    def __init__(
        self,
        label: str,
        argv: list[str],
        returncode: int | None,
        timed_out: bool,
        stderr: str,
    ) -> None:
        super().__init__(
            "cache preparation command refused: "
            f"{label} returncode={returncode} timed_out={timed_out} argv={argv}"
        )
        self.label = label
        self.argv = argv
        self.returncode = returncode
        self.timed_out = timed_out
        self.stderr = stderr

    def detail(self) -> str:
        tail = "\n".join(self.stderr.splitlines()[-40:])
        header = f"--- {self.label}.stderr (ultimas linhas) ---"
        return "\n".join((str(self), header, tail))


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def regular(path: Path) -> bytes:
    require(path.is_absolute(), "absolute source required")
    require(not any(p.is_symlink() for p in (path, *path.parents)), "source symlink")
    require(path.is_file(), "source file missing")
    return path.read_bytes()


def clean_environment() -> dict[str, str]:
    forbidden = [k for k in os.environ if k.startswith(("UV_", "PYTHON", "PYTEST_"))]
    require(not forbidden, "ambient Python/pytest/uv configuration")
    # Same HOME default as both existing offline preparers. In particular there
    # is no setup-uv UV_CACHE_DIR or inherited XDG_CACHE_HOME override here.
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(Path.home()),
        "LC_ALL": "C",
        "TZ": "UTC",
    }


def inventory(raw: bytes) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    for package in tomllib.loads(raw.decode())["package"]:
        source = package["source"]
        if source == {"virtual": "."} or source == {"editable": "."}:
            continue
        require(
            source == {"registry": "https://pypi.org/simple"},
            "nonregistry fixture dependency",
        )
        key = (package["name"], package["version"])
        require(key not in result, "duplicate locked package")
        result[key] = package
    require(bool(result), "empty package graph")
    return result


def check_fixture(root_lock: bytes, fixture_lock: bytes) -> None:
    root = inventory(root_lock)
    for key, package in inventory(fixture_lock).items():
        require(key in root, "fixture dependency outside selected lock")
        # Source, URL and hash must all belong to the source-selected package.
        expected = root[key]
        require(package["source"] == expected["source"], "fixture registry drift")
        for kind in ("sdist", "wheels"):
            values = package.get(kind, [])
            allowed = expected.get(kind, [])
            if kind == "sdist":
                values, allowed = (
                    ([values] if values else []),
                    ([allowed] if allowed else []),
                )
            for item in values:
                require(
                    any(item["url"] == a["url"] and item["hash"] == a["hash"] for a in allowed),
                    "fixture artifact outside selected lock",
                )
        require(bool(package.get("wheels")), "fixture wheel unavailable")


def fixture_constraints(root_lock: bytes, dependencies: tuple[str, ...]) -> bytes:
    """Close the tiny fixture over exact versions from the selected root lock."""
    packages = inventory(root_lock)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for (name, _), package in packages.items():
        by_name.setdefault(name, []).append(package)
    pending = []
    for requirement in dependencies:
        match = re.fullmatch(r"([a-z0-9-]+)==([0-9.]+)", requirement)
        require(match is not None, "fixture dependency must be exact")
        assert match is not None
        name, version = match.groups()
        require((name, version) in packages, "fixture dependency outside selected lock")
        pending.append(name)
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        candidates = by_name.get(name, [])
        require(len(candidates) == 1, "ambiguous selected fixture dependency")
        package = candidates[0]
        selected[name] = package["version"]
        pending.extend(dependency["name"] for dependency in package.get("dependencies", []))
    return "".join(f"{name}=={version}\n" for name, version in sorted(selected.items())).encode()


def fixture_config(name: str, dependencies: tuple[str, ...]) -> bytes:
    return (
        "[project]\nname=" + json.dumps(name) + '\nversion="0.0.0"\n'
        'requires-python=">=3.12,<3.13"\n[project.optional-dependencies]\ndev='
        + json.dumps(list(dependencies))
        + "\n[tool.uv]\npackage=false\n"
    ).encode()


class Preparation:
    def __init__(self, root: Path, output: Path) -> None:
        self.root, self.output = root.resolve(strict=True), output.absolute()
        require(
            not self.output.is_relative_to(self.root),
            "preparation output must be outside source",
        )
        self.environment = clean_environment()
        self.uv = Path(shutil.which("uv", path=self.environment["PATH"]) or "/missing-uv").resolve()
        self.python = Path(sys.executable).resolve(strict=True)
        self.output.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.deadline = time.monotonic() + 600
        self.head = self.git("rev-parse", "HEAD").decode().strip()
        self.inputs = {p: regular(self.root / p) for p in INPUTS}
        self.tool_pins = {str(p): sha(regular(p)) for p in (self.uv, self.python)}
        self.current()
        self.record(
            "inputs.json",
            {
                "head": self.head,
                "files": {p: sha(b) for p, b in self.inputs.items()},
                "tools": self.tool_pins,
                "environment": self.environment,
            },
        )
        self.sequence = 0

    def git(self, *args: str) -> bytes:
        return subprocess.check_output(
            ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *args],
            cwd=self.root,
            env=self.environment,
            stderr=subprocess.PIPE,
            timeout=10,
        )

    def record(self, name: str, value: Any) -> None:
        (self.output / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")

    def current(self) -> None:
        require(
            self.git("rev-parse", "HEAD").decode().strip() == self.head,
            "checkout HEAD drift",
        )
        for name, raw in self.inputs.items():
            require(
                regular(self.root / name) == raw == self.git("show", f"{self.head}:{name}"),
                "selected source differs from commit",
            )
        require(
            all(sha(regular(Path(p))) == h for p, h in self.tool_pins.items()),
            "tool drift",
        )

    def command(self, cwd: Path, *args: str) -> bytes:
        self.current()
        require(time.monotonic() < self.deadline, "preparation deadline")
        self.sequence += 1
        label = f"command-{self.sequence:02}"
        argv = [str(self.uv), *args]
        start = time.monotonic()
        timed_out = False
        process: subprocess.Popen[str] | None = None
        code: int | None = None

        def cleanup() -> None:
            if process is not None and process.pid in process_groups._pending_groups:
                process_groups._quiesce_group(process, process.pid)

        with (
            (self.output / (label + ".stdout")).open("xb") as out,
            (self.output / (label + ".stderr")).open("xb") as err,
            process_groups._spawn_signal_guard(cleanup) as activate,
        ):
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=self.environment,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                    text=True,
                )
                process_groups._record_pending(process.pid)
                activate()
                try:
                    code = process.wait(timeout=min(120, self.deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    code = 124
            finally:
                try:
                    cleanup()
                finally:
                    self.record(
                        label + ".json",
                        {
                            "argv": argv,
                            "cwd": str(cwd),
                            "returncode": code,
                            "timed_out": timed_out,
                            "seconds": time.monotonic() - start,
                            "pgid": process.pid if process is not None else None,
                            "quiescent": (
                                process is None or process.pid not in process_groups._pending_groups
                            ),
                        },
                    )
        self.current()
        if timed_out or code != 0:
            # Antes aqui havia um `require(...)` com mensagem generica: o job so via
            # "refused" e a causa (argv/returncode/stderr) ficava no --output, que
            # somente o job release-floor publicava como artefato.
            raise CommandRefusedError(
                label,
                argv,
                code,
                timed_out,
                (self.output / (label + ".stderr")).read_text(errors="replace"),
            )
        return (self.output / (label + ".stdout")).read_bytes()

    def project(self, name: str, config: bytes, lock: bytes | None = None) -> Path:
        path = self.output / name
        path.mkdir(mode=0o700)
        (path / "pyproject.toml").write_bytes(config)
        (path / ".python-version").write_bytes(self.inputs[".python-version"])
        if lock is not None:
            (path / "uv.lock").write_bytes(lock)
        return path

    def sync(self, project: Path, *, offline: bool, cutoff: str | None = None) -> None:
        before = {p: regular(project / p) for p in ("pyproject.toml", "uv.lock", ".python-version")}
        self.command(
            project,
            "sync",
            *(["--offline"] if offline else []),
            "--locked",
            "--no-config",
            "--extra",
            "dev",
            "--no-install-project",
            "--no-build",
            "--no-python-downloads",
            "--python",
            str(self.python),
            *(["--exclude-newer", cutoff] if cutoff else []),
        )
        require(
            all(regular(project / p) == b for p, b in before.items()),
            "prepared input drift",
        )

    def run(self) -> None:
        require(sys.version_info[:2] == (3, 12), "fixture Python differs")
        workflow = self.inputs[".github/workflows/ci.yml"].decode()
        version = re.findall(r'^  MAEZO_CI_UV_VERSION: "([0-9.]+)"$', workflow, re.M)
        require(len(version) == 1, "uv version selection unavailable")
        require(
            self.command(self.root, "--version").decode().split()[:2] == ["uv", version[0]],
            "uv version differs",
        )
        cache = self.command(self.root, "cache", "dir", "--no-config").decode().strip()
        self.record(
            "cache.json",
            {"path": cache, "selection": "HOME/default; no UV or XDG override"},
        )
        root_lock = self.inputs["uv.lock"]
        cutoff = tomllib.loads(root_lock.decode())["options"]["exclude-newer"]
        config_cutoff = tomllib.loads(self.inputs["pyproject.toml"].decode())["tool"]["uv"]["exclude-newer"]
        require(
            cutoff == config_cutoff and isinstance(cutoff, str),
            "source cutoff mismatch",
        )
        # Sonda + cura do cache HOME restaurado (correcao de raiz da falha erratica).
        #
        # Evidencia medida (run 35373462749, PR #414, tentativa 2, job "lint / type /
        # unit"): o setup-uv registrou "Cache hit" na chave compartilhada e restaurou
        # 103 MB em ~/.cache/uv; o PRIMEIRO comando de sync deste bloco -- que e
        # ONLINE, sem --offline -- morreu em 0,16 s com
        #   error: Failed to install: pytest-9.1.1-py3-none-any.whl (pytest==9.1.1)
        #     Caused by: Invalid package version
        # isto e, a entrada de cache restaurada estava corrompida. O que se publica
        # como cache e o estado do FIM do job anterior (depois dos testes que rodam uv
        # contra este mesmo cache default e matam grupos de processo), nao o estado
        # validado logo apos esta preparacao -- e ninguem valida na gravacao nem na
        # restauracao.
        #
        # Na fase ONLINE existe rede: derrubar o build inteiro por causa de uma entrada
        # de cache podre e a escolha errada. Limpamos o cache e repopulamos UMA vez, com
        # a causa impressa e gravada na prova. A fase OFFLINE logo abaixo continua sem
        # rede e sem segunda chance -- e ela que prova a admissao historica, e nada aqui
        # a relaxa.
        try:
            self.sync(
                self.project("root-online", self.inputs["pyproject.toml"], root_lock),
                offline=False,
                cutoff=cutoff,
            )
        except CommandRefusedError as refusal:
            print(refusal.detail(), file=sys.stderr)
            print(
                "restored default cache rejected an ONLINE command; cleaning it and repopulating once",
                file=sys.stderr,
            )
            # Anotacao no stdout (workflow commands so' sao lidos de stdout): sem isto um cache
            # corrompido em TODO run custaria um redownload completo por run sem ninguem perceber.
            print(
                "::warning title=uv cache::restored default uv cache was corrupt; cleaned and "
                "repopulated once (details in cache-recovery.json of the proof artifact)"
            )
            self.record(
                "cache-recovery.json",
                {
                    "label": refusal.label,
                    "argv": refusal.argv,
                    "returncode": refusal.returncode,
                    "timed_out": refusal.timed_out,
                    "action": "uv cache clean + one online repopulation",
                },
            )
            self.command(self.root, "cache", "clean", "--no-config")
            self.sync(
                self.project("root-online-repaired", self.inputs["pyproject.toml"], root_lock),
                offline=False,
                cutoff=cutoff,
            )
        self.sync(
            self.project("root-offline", self.inputs["pyproject.toml"], root_lock),
            offline=True,
            cutoff=cutoff,
        )
        for name, dependencies in FIXTURES:
            config = fixture_config(name, dependencies)
            online = self.project(name + "-online", config)
            constraints = self.output / (name + "-constraints.txt")
            constraints.write_bytes(fixture_constraints(root_lock, dependencies))
            requirements = self.output / (name + "-requirements.txt")
            requirements.write_text("\n".join(dependencies) + "\n")
            # A locked sync caches wheel URLs, not the index/metadata a fresh lock needs.
            # Prime only selected versions; online unconstrained resolution could download
            # newer transitive metadata and make the immutable offline fixture drift.
            self.command(
                online,
                "pip",
                "compile",
                str(requirements),
                "--constraint",
                str(constraints),
                "--universal",
                "--no-config",
                "--no-build",
                "--no-python-downloads",
                "--python",
                str(self.python),
            )
            self.command(
                online,
                "lock",
                "--offline",
                "--no-config",
                "--no-build",
                "--no-python-downloads",
                "--python",
                str(self.python),
            )
            locked = regular(online / "uv.lock")
            check_fixture(root_lock, locked)
            self.sync(online, offline=False)
            offline = self.project(name + "-offline", config)
            self.command(
                offline,
                "lock",
                "--offline",
                "--no-config",
                "--no-build",
                "--no-python-downloads",
                "--python",
                str(self.python),
            )
            require(
                regular(offline / "uv.lock") == locked,
                "offline fixture resolution drift",
            )
            self.sync(offline, offline=True)
            self.record(
                name + "-lock.json",
                {"lock_sha256": sha(locked), "config_sha256": sha(config)},
            )
        self.current()
        self.record(
            "result.json",
            {
                "status": "prepared",
                "head": self.head,
                "commands": self.sequence,
                "runtime_acceptance": False,
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with process_groups._spawn_signal_guard(
            handlers={signal.SIGTERM: process_groups._signal_handler}
        ) as activate:
            activate()
            Preparation(args.root, args.output).run()
    except (KeyboardInterrupt, process_groups.RunnerInterrupted):
        print("offline fixture cache preparation interrupted", file=sys.stderr)
        return 130
    except (
        ValueError,
        OSError,
        subprocess.SubprocessError,
        process_groups.RunnerError,
    ) as refusal:
        # Metade do defeito era diagnostico: so saia esta linha, e a causa ficava no
        # diretorio --output, que so um dos dois jobs consumidores publicava como
        # artefato. Agora a causa vai para o stderr do job, onde quem investiga
        # ja esta olhando.
        print("offline fixture cache preparation refused", file=sys.stderr)
        if isinstance(refusal, CommandRefusedError):
            print(refusal.detail(), file=sys.stderr)
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
