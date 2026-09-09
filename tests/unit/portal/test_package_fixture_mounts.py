"""Offline fences for the ADR-0049 D5 disposable image fixture."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

PREPARE_PATH = Path(__file__).parents[3] / "deploy/cibseven/package-test/prepare.py"
SPEC = importlib.util.spec_from_file_location("portal_package_prepare", PREPARE_PATH)
assert SPEC and SPEC.loader
prepare_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_module)


def _git_checkout(path: Path) -> tuple[Path, str]:
    checkout = path / "checkout"
    sql = checkout / "src/maezo/portal/engine/java/src/main/resources/human-schema-postgres.sql"
    sql.parent.mkdir(parents=True)
    sql.write_text("CREATE TABLE fixture_test(id integer);\n")
    subprocess.run(["git", "init", "-q", checkout], check=True)
    subprocess.run(["git", "-C", checkout, "config", "user.name", "Fixture Test"], check=True)
    subprocess.run(
        ["git", "-C", checkout, "config", "user.email", "fixture-test@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", checkout, "add", "."], check=True)
    subprocess.run(["git", "-C", checkout, "commit", "-qm", "fixture"], check=True)
    sha = subprocess.check_output(["git", "-C", checkout, "rev-parse", "HEAD"], text=True).strip()
    return checkout.resolve(), sha


def _server_xml(path: Path) -> Path:
    original = path / "base-server.xml"
    original.write_text(
        '<Server port="8005"><GlobalNamingResources>'
        '<Resource name="jdbc/ProcessEngine"/>'
        '</GlobalNamingResources><Service name="Catalina">'
        '<Connector port="8080"/></Service></Server>\n'
    )
    return original.resolve()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_explicit_private_root_generates_fail_closed_mounts(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    checkout, sha = _git_checkout(root)
    original = _server_xml(root)
    private_root = root / "daemon-visible-private"
    private_root.mkdir(mode=0o700)
    evidence_root = root / "evidence"
    evidence_root.mkdir()
    output = private_root / "fixture"
    previous_umask = os.umask(0o022)
    os.umask(previous_umask)

    prepare_module.prepare(
        checkout,
        sha,
        original,
        private_root,
        evidence_root,
        output,
        "portal-package:test",
    )

    observed_umask = os.umask(previous_umask)
    os.umask(observed_umask)
    assert observed_umask == previous_umask
    assert _mode(output) == 0o700
    compose = json.loads((output / "compose.json").read_text())
    mounts = [mount for service in compose["services"].values() for mount in service.get("volumes", [])]
    assert len(mounts) == 8
    assert all(mount["bind"] == {"create_host_path": False} for mount in mounts)
    assert all(mount["source"].startswith(str(output) + os.sep) for mount in mounts)
    assert all(_mode(path) == 0o600 for path in output.iterdir() if path.suffix != ".sql")
    assert {_mode(output / name) for name in ("01-human.sql", "02-tenant.sql")} == {0o444}

    with pytest.raises(ValueError, match="NEW path"):
        prepare_module.validate_output_roots(checkout, private_root, evidence_root, output)


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o777])
def test_private_root_requires_exact_private_permissions(tmp_path: Path, mode: int) -> None:
    root = tmp_path.resolve()
    checkout, _ = _git_checkout(root)
    private_root = root / "private"
    private_root.mkdir(mode=mode)
    private_root.chmod(mode)
    evidence_root = root / "evidence"
    evidence_root.mkdir()

    with pytest.raises(ValueError, match="mode 0700"):
        prepare_module.validate_output_roots(checkout, private_root, evidence_root, private_root / "fixture")


def test_private_root_rejects_overlap_nested_output_and_symlink(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    checkout, _ = _git_checkout(root)
    evidence_root = root / "evidence"
    evidence_root.mkdir()
    private_root = root / "private"
    private_root.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="direct child"):
        prepare_module.validate_output_roots(
            checkout, private_root, evidence_root, private_root / "nested" / "fixture"
        )
    evidence_root.chmod(0o700)
    with pytest.raises(ValueError, match="overlap evidence root"):
        prepare_module.validate_output_roots(
            checkout, evidence_root, evidence_root, evidence_root / "fixture"
        )
    checkout.chmod(0o700)
    with pytest.raises(ValueError, match="overlap checkout"):
        prepare_module.validate_output_roots(checkout, checkout, evidence_root, checkout / "fixture")

    link = root / "private-link"
    link.symlink_to(private_root, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical"):
        prepare_module.validate_output_roots(checkout, link, evidence_root, link / "fixture")

    dangling = private_root / "fixture-link"
    dangling.symlink_to(private_root / "missing")
    with pytest.raises(ValueError, match="canonical"):
        prepare_module.validate_output_roots(checkout, private_root, evidence_root, dangling)
