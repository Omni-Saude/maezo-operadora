"""`login-secrets` (B6): um JSON 0400 por login nativo, num diretorio NOVO 0700, e nada na saida."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from tools.staff_install.installer import NATIVE_LOGINS as INSTALLER_LOGINS
from tools.staff_install.installer import parse_credential
from tools.staff_materials import login_secrets, secure_io
from tools.staff_materials.__main__ import main
from tools.staff_materials.generate import REPO
from tools.staff_materials.secure_io import MaterialError

posix = pytest.mark.skipif(os.name != "posix", reason="a ferramenta so roda em POSIX (secure_io)")
SQL_LOGINS = (REPO / "deploy/sql/engine-native-roles.sql").read_text(encoding="utf-8")


def test_logins_are_exactly_the_four_of_the_roles_script_and_of_the_installer() -> None:
    assert login_secrets.NATIVE_LOGINS == INSTALLER_LOGINS
    assert "ARRAY['maezo_native_schema_owner','maezo_native_case_issuer'," in SQL_LOGINS
    assert "'maezo_native_issuer_witness','portal_read_source_amh']::name[]" in SQL_LOGINS


@posix
def test_writes_one_private_json_per_login_in_a_new_private_directory(tmp_path: Path) -> None:
    written = login_secrets.write(tmp_path / "out")
    assert stat.S_IMODE(written.directory.stat().st_mode) == 0o700
    assert sorted(p.name for p in written.directory.iterdir()) == sorted(
        f"{x}.json" for x in INSTALLER_LOGINS
    )
    passwords = set()
    for login, path in zip(login_secrets.NATIVE_LOGINS, written.files, strict=True):
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
        raw = path.read_bytes()
        assert not raw.endswith(b"\n")
        value = json.loads(raw)
        assert set(value) == {"username", "password"} and value["username"] == login
        assert len(value["password"]) >= 40 and value["password"].isascii()
        # O instalador le exatamente este formato.
        assert parse_credential(raw.decode(), login) == value["password"]
        passwords.add(value["password"])
    assert len(passwords) == 4


@posix
def test_existing_directory_and_repo_paths_are_refused(tmp_path: Path) -> None:
    (tmp_path / "exists").mkdir()
    with pytest.raises(MaterialError, match="NOVO"):
        login_secrets.write(tmp_path / "exists")
    with pytest.raises(MaterialError, match="repositorio"):
        login_secrets.write(REPO / "tmp-login-secrets", forbidden=(REPO,))
    assert not (REPO / "tmp-login-secrets").exists()


@posix
def test_cli_prints_only_paths_never_passwords(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["login-secrets", "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    for path in (tmp_path / "out").iterdir():
        assert json.loads(path.read_bytes())["password"] not in out
        assert f"file://{path}" in out


def test_non_posix_host_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(secure_io.os, "name", "nt")
    with pytest.raises(MaterialError, match="POSIX"):
        login_secrets.write(tmp_path / "out")
    assert main(["login-secrets", "--out", str(tmp_path / "out2")]) == 1
    assert "POSIX" in capsys.readouterr().err
    monkeypatch.undo()
    assert not (tmp_path / "out").exists() and not (tmp_path / "out2").exists()
