"""One-to-one replacement and wrong-source checks for the six maintained consumers."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import venv
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

_SUCCESSORS = {
    "EXTsuccessor-pr358-ab-independent-delta-review/independent_controls.py": (
        "scripts.dev.verify_pr358_ab_delta",
        [],
    ),
    "completion-pr356-ecs-independent-delta-f38ec6ad/prove_scope.py": (
        "scripts.dev.verify_pr356_ecs_scope",
        [],
    ),
    "completion-pr356-ecs-independent-delta-f38ec6ad/test_ecs_interfaces.py": (
        "scripts.dev.verify_pr356_ecs_interfaces",
        [],
    ),
    "completion-pr356-ecs-independent-delta-f38ec6ad/source_custody.py": (
        "scripts.dev.capture_pr356_ecs_source",
        ["--label", "source-before"],
    ),
    "completion-pr356-ecs-independent-delta-f38ec6ad/record.py": (
        "scripts.dev.record_pr356_ecs_command",
        ["--label", "fixture", "/usr/bin/true"],
    ),
    "completion-pr356-ecs-independent-delta-f38ec6ad/test_delta_controls.py": (
        "scripts.dev.verify_pr356_ecs_delta_controls",
        [],
    ),
}


def _wrong_source(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(path), "config", "user.email", "verification@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/git", "-C", str(path), "config", "user.name", "Verification Fixture"],
        check=True,
    )
    (path / "tracked.txt").write_text("wrong source\n")
    subprocess.run(["/usr/bin/git", "-C", str(path), "add", "tracked.txt"], check=True)
    subprocess.run(["/usr/bin/git", "-C", str(path), "commit", "-q", "-m", "wrong"], check=True)
    return path


def test_successor_mapping_is_six_explicit_commands_without_historical_checkout_paths() -> None:
    assert len(_SUCCESSORS) == 6
    for module, _extra in _SUCCESSORS.values():
        path = _REPO_ROOT.joinpath(*module.split(".")).with_suffix(".py")
        source = path.read_text()
        assert "/Users/familia/" not in source
        assert "maezo-completion-wt" not in source
        assert 'parser.add_argument("--source"' in source
        assert 'parser.add_argument("--evidence"' in source
        assert 'parser.add_argument("--output"' in source


@pytest.mark.parametrize(("module", "extra"), list(_SUCCESSORS.values()))
def test_each_successor_refuses_a_clean_wrong_revision_without_creating_output(
    tmp_path: Path, module: str, extra: list[str]
) -> None:
    source = _wrong_source(tmp_path / "wrong-source")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    output = tmp_path / "output"
    command = [
        sys.executable,
        "-m",
        module,
        "--source",
        str(source),
        "--evidence",
        str(evidence),
        "--output",
        str(output),
        *extra,
    ]

    result = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 2
    assert "source is not the required Git revision" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_recorder_preserves_virtualenv_invocation_and_records_binary_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.dev import record_pr356_ecs_command as recorder

    source = _wrong_source(tmp_path / "source")
    head = subprocess.check_output(["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"]).decode().strip()
    monkeypatch.setattr(recorder, "HEAD", head)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "terraform-cli.tfrc").write_text("")
    environment = tmp_path / "selected-venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    executable = environment / "bin/python"
    assert executable.is_symlink()
    output = tmp_path / "recorded"
    command = "import json,sys; print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))"

    result = recorder.main(
        [
            "--source",
            str(source),
            "--evidence",
            str(evidence),
            "--output",
            str(output),
            "--label",
            "venv",
            str(executable),
            "-c",
            command,
        ]
    )

    assert result == 0
    observed = json.loads((output / "venv.stdout").read_text())
    assert Path(observed["prefix"]).resolve() == environment.resolve()
    assert observed["prefix"] != observed["base"]
    receipt = json.loads((output / "venv.json").read_text())
    assert receipt["argv"][0] == str(executable)
    assert receipt["tool"]["path"] == str(executable)
    assert receipt["tool"]["resolved_path"] == str(executable.resolve())
    assert receipt["tool"]["sha256"] == hashlib.sha256(executable.resolve().read_bytes()).hexdigest()
