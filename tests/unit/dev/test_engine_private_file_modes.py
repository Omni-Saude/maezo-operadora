"""ADR-0006: staging pytest privado desde a primeira escrita, sem umask global."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.dev import run_engine_integration as runner

from tests.unit.dev.test_engine_runner_final_repair import authenticate_fixture


def _exercise(root: Path, mask: int, writes: bool) -> None:
    """Processo isolado permite variar o umask sem tocar no processo pytest pai."""
    root.chmod(0o700)
    audit = root / "write-modes.jsonl"
    audit.touch(mode=0o600)
    (root / ".gitignore").write_text("write-modes.jsonl\n")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration"]\n')
    (root / "test_subject.py").write_text(
        "import pytest\n@pytest.mark.integration\ndef test_subject():\n    assert True\n"
    )
    # O audit hook real observa os opens de escrita ANTES de truncar/escrever
    # os arquivos do plugin autenticado e do JUnit do pytest real.
    (root / "conftest.py").write_text(
        "import json, os, pathlib, sys\n"
        "def observe(event, args):\n"
        "    if event != 'open' or not isinstance(args[0], (str, bytes)): return\n"
        "    path = pathlib.Path(os.fsdecode(args[0]))\n"
        "    if path.name not in ('junit.xml', 'execution.json'): return\n"
        "    if not args[2] & (os.O_WRONLY | os.O_RDWR): return\n"
        "    mode = path.stat().st_mode & 0o777\n"
        "    assert mode == 0o600, (path.name, oct(mode))\n"
        f"    with open({str(audit)!r}, 'a') as stream:\n"
        "        stream.write(json.dumps([path.name, mode]) + '\\n')\n"
        "sys.addaudithook(observe)\n"
    )
    authenticate_fixture(root)
    out = root / "out"
    original_run = runner._run
    staging: list[Path] = []

    def observed_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        xml_args = [arg for arg in command if arg.startswith("--junitxml=")]
        if xml_args:
            private = Path(xml_args[0].split("=", 1)[1]).parent
            staging.append(private)
            assert private.stat().st_mode & 0o777 == 0o700
            for name in ("junit.xml", "execution.json", "pytest.log"):
                path = private / name
                assert path.stat().st_mode & 0o777 == 0o600
                assert path.stat().st_size == 0
        result = original_run(command, **kwargs)
        if xml_args:
            for path in staging[-1].iterdir():
                assert path.stat().st_mode & 0o777 == 0o600
        return result

    def forbidden_umask(*args: Any) -> None:
        raise AssertionError("runner must not mutate the parent umask")

    def sentinel(name: str) -> None:
        path = root / name
        path.write_text("synthetic")
        assert path.stat().st_mode & 0o777 == 0o666 & ~mask

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_uv_python", lambda _root, *args: [sys.executable, "-I", *args])
        patch.setattr(runner, "_run", observed_run)
        patch.setattr(os, "umask", forbidden_umask)
        sentinel("before")
        runner._collect(root, ["test_subject.py", "-m", "integration"], root / "collection.log")
        expected = runner._collected_items[root / "collection.log"]
        if not writes:
            patch.setattr(
                runner,
                "_pytest_command",
                lambda _root, *args, **kwargs: [
                    sys.executable,
                    "-c",
                    "print('synthetic private unwritten diagnostic'); raise SystemExit(2)",
                    *args,
                ],
            )
        result = runner._run_pytest(root, "core", "test_subject.py", {}, out, 1, expected)
        sentinel("after")
    assert len(staging) == 1 and not staging[0].exists()
    assert not runner._pending_groups
    if writes:
        assert result["return_code"] == 0
        records = [json.loads(line) for line in audit.read_text().splitlines()]
        assert {name for name, _mode in records} == {"junit.xml", "execution.json"}
        assert all(mode == 0o600 for _name, mode in records)
    else:
        assert result["return_code"] != 0 and result["errors"]
        assert not (out / "pytest-execution.json").exists()
        assert (out / "junit.xml").read_text() == "<!-- XML bruto inválido retido da publicação -->\n"
        assert "Diagnostico pytest retido" in (out / "pytest.log").read_text()
        assert "synthetic private unwritten diagnostic" not in "".join(
            path.read_text() for path in out.iterdir() if path.is_file()
        )


@pytest.mark.parametrize("mask", [0o000, 0o022, 0o077], ids=["000", "022", "077"])
@pytest.mark.parametrize("writes", [True, False], ids=["written", "unwritten"])
def test_private_files_from_first_write_without_changing_parent_umask(
    tmp_path: Path, mask: int, writes: bool
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; "
            "from tests.unit.dev.test_engine_private_file_modes import _exercise; "
            f"_exercise(Path({str(tmp_path)!r}), {mask}, {writes!r})",
        ],
        cwd=Path(__file__).resolve().parents[3],
        umask=mask,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
