"""Escrita de material sensivel: diretorio NOVO 0700, arquivo novo, modo explicito, sem sobrescrever."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

PRIVATE = 0o400
PUBLIC = 0o444
POSIX = sys.platform != "win32"


class MaterialError(RuntimeError):
    """Recusa da ferramenta. A mensagem nunca carrega bytes de chave, senha ou DSN."""


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def new_private_directory(path: Path, *, forbidden: tuple[Path, ...] = ()) -> Path:
    """Cria um diretorio NOVO com modo 0700. Recusa caminho existente e caminho dentro do repo."""
    if not path.is_absolute():
        raise MaterialError("o diretorio de saida precisa ser absoluto")
    resolved = path.resolve(strict=False)
    for protected in forbidden:
        root = protected.resolve(strict=False)
        if _inside(resolved, root) or _inside(root, resolved):
            raise MaterialError("o diretorio de saida nao pode ficar dentro do repositorio")
    if os.path.lexists(path):
        raise MaterialError("o diretorio de saida precisa ser NOVO")
    if not path.parent.is_dir():
        raise MaterialError("o diretorio pai da saida precisa existir")
    old = os.umask(0o077) if POSIX else None
    try:
        path.mkdir(mode=0o700)
    finally:
        if old is not None:
            os.umask(old)
    if POSIX:
        os.chmod(path, 0o700)
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise MaterialError("o diretorio de saida nao ficou 0700")
    return path


def subdirectory(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir(mode=0o700)
    if POSIX:
        os.chmod(path, 0o700)
    return path


def write_new(path: Path, data: bytes, mode: int) -> None:
    """Grava um arquivo que ainda nao existe, com `O_EXCL` e o modo pedido."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)
    if POSIX:
        os.chmod(path, mode)


def private_mode_ok(path: Path) -> bool:
    """Em POSIX exige 0400/0600 do dono; fora dele (estacao Windows de teste) nao ha o que medir."""
    if sys.platform == "win32":
        return True
    st = path.stat()
    return (
        stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) in (0o400, 0o600) and st.st_uid == os.geteuid()
    )
