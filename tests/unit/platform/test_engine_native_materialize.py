"""Init container `engine-native-materialize` (Onda 4): allowlist fechada, modos exatos, sem symlink."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import pytest

from maezo.platform import engine_native_materialize as m

POSIX = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"), reason="dir_fd/O_NOFOLLOW POSIX"
)


def _secret(names: frozenset[str], **extra: str) -> str:
    value = {name: base64.b64encode(f"conteudo de {name}".encode()).decode() for name in sorted(names)}
    value.update(extra)
    return json.dumps(value)


def _volumes(root: Path) -> Path:
    for volume in m.VOLUMES:
        (root / volume).mkdir()
    return root


def test_allowlist_cobre_exatamente_a_saida_do_native_secret() -> None:
    from tools.staff_materials import native_secret

    assert native_secret.PRIVATE_OUTPUT < m.ENGINE_REQUIRED
    assert len(m.ENGINE_REQUIRED) == 11
    assert not (m.ENGINE_REQUIRED | m.ENGINE_OPTIONAL) & m.ISSUER_REQUIRED


def test_decode_aceita_o_conjunto_do_engine() -> None:
    files = m.decode(_secret(m.ENGINE_REQUIRED), staff_issuer=False)
    assert set(files) == m.ENGINE_REQUIRED
    assert files["engine-native/server.key"] == b"conteudo de engine-native/server.key"


def test_decode_aceita_opcionais_e_emissor() -> None:
    names = m.ENGINE_REQUIRED | m.ENGINE_OPTIONAL | m.ISSUER_REQUIRED
    assert set(m.decode(_secret(names), staff_issuer=True)) == names


@pytest.mark.parametrize(
    "raw",
    [
        _secret(m.ENGINE_REQUIRED - {"engine-run/trust.json"}),  # obrigatorio ausente
        _secret(m.ENGINE_REQUIRED, **{"engine-run/extra.json": "eA=="}),  # chave desconhecida
        _secret(m.ENGINE_REQUIRED, **{"engine-run/../etc/passwd": "eA=="}),  # travessia
        _secret(m.ENGINE_REQUIRED, **{"/engine-run/trust.json": "eA=="}),  # absoluto
        _secret(m.ENGINE_REQUIRED, **{"engine-run/trust.json": "não é base64"}),
        _secret(m.ENGINE_REQUIRED, **{"engine-run/trust.json": "eA"}),  # sem padding
        _secret(m.ENGINE_REQUIRED, **{"engine-run/trust.json": ""}),
        _secret(m.ENGINE_REQUIRED, **{"engine-run/trust.json": "eA==\n"}),  # nao canonico
        _secret(m.ISSUER_REQUIRED | m.ENGINE_REQUIRED),  # emissor sem o flag
        '{"engine-run/trust.json": "eA==", "engine-run/trust.json": "eQ=="}',  # chave repetida
        "[]",
        "nao json",
    ],
)
def test_decode_recusa(raw: str) -> None:
    with pytest.raises(m.MaterializeError):
        m.decode(raw, staff_issuer=False)


def test_decode_recusa_emissor_incompleto() -> None:
    with pytest.raises(m.MaterializeError):
        m.decode(
            _secret(m.ENGINE_REQUIRED | (m.ISSUER_REQUIRED - {"staff-issuer/witness-dsn.txt"})),
            staff_issuer=True,
        )


def test_decode_recusa_arquivo_grande() -> None:
    big = base64.b64encode(b"x" * (m.MAX_FILE + 1)).decode()
    with pytest.raises(m.MaterializeError):
        m.decode(_secret(m.ENGINE_REQUIRED, **{"engine-run/trust.json": big}), staff_issuer=False)


@pytest.mark.parametrize(
    "name", ["engine-run", "outro/x", "engine-run/../x", "engine-run//x", "engine-run/./x"]
)
def test_materialize_recusa_nome_fora_do_layout(tmp_path: Path, name: str) -> None:
    with pytest.raises(m.MaterializeError):
        m.materialize({name: b"x"}, root=_volumes(tmp_path), owner=None)


@POSIX
def test_materialize_escreve_arquivos_regulares_0400_e_diretorios_0500(tmp_path: Path) -> None:
    names = m.ENGINE_REQUIRED | m.ISSUER_REQUIRED
    files = m.decode(_secret(names), staff_issuer=True)
    root = _volumes(tmp_path)
    m.materialize(files, root=root, owner=None)
    for name, content in files.items():
        info = os.lstat(root / name)
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o400
        assert info.st_uid == os.getuid() and info.st_nlink == 1
        assert (root / name).read_bytes() == content
    for directory in ["engine-native", "engine-run", "engine-run/staff", "staff-issuer"]:
        info = os.lstat(root / directory)
        assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o500
    written = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert written == names


@POSIX
def test_materialize_recusa_volume_nao_vazio(tmp_path: Path) -> None:
    root = _volumes(tmp_path)
    (root / "engine-run" / "sobra").write_bytes(b"x")
    with pytest.raises(m.MaterializeError):
        m.materialize(m.decode(_secret(m.ENGINE_REQUIRED), staff_issuer=False), root=root, owner=None)


@POSIX
def test_materialize_recusa_volume_symlink(tmp_path: Path) -> None:
    (tmp_path / "alvo").mkdir()
    for volume in m.VOLUMES:
        if volume != "engine-native":
            (tmp_path / volume).mkdir()
    (tmp_path / "engine-native").symlink_to(tmp_path / "alvo")
    with pytest.raises(OSError):
        m.materialize(m.decode(_secret(m.ENGINE_REQUIRED), staff_issuer=False), root=tmp_path, owner=None)
    assert not any((tmp_path / "alvo").iterdir())


def test_main_recusa_sem_flag_e_nao_vaza_nome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(m.SECRET_ENV, _secret(m.ENGINE_REQUIRED))
    monkeypatch.delenv(m.ISSUER_ENV, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        m.main()
    assert exit_info.value.code == 1
    assert capsys.readouterr().err.strip() == "engine_native_materialize_refused"
    assert m.SECRET_ENV not in os.environ
