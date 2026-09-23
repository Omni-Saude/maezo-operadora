"""Escrita de material sensivel: diretorio NOVO 0700, arquivo novo, modo explicito, sem sobrescrever.

Fail-closed fora de POSIX. No Windows, `chmod(0o400)`/`mkdir(mode=0o700)` nao mudam a ACL: medido
em 23/09/2026, `D:\\` e `D:\\tmp` herdam `Usuarios autenticados:(OI)(CI)(M)`, entao chave e DSN
ficariam legiveis por qualquer usuario da maquina. A ferramenta recusa antes de criar qualquer
coisa; rode em Linux, WSL ou num container.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

PRIVATE = 0o400
PUBLIC = 0o444


class MaterialError(RuntimeError):
    """Recusa da ferramenta. A mensagem nunca carrega bytes de chave, senha ou DSN."""


def require_posix() -> None:
    if os.name != "posix":
        raise MaterialError(
            "fora de POSIX o modo 0400/0700 nao protege o arquivo (ACL herdada): "
            "rode em Linux, WSL ou num container"
        )


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def new_private_directory(path: Path, *, forbidden: tuple[Path, ...] = ()) -> Path:
    """Cria um diretorio NOVO com modo 0700. Recusa caminho existente e caminho dentro do repo."""
    require_posix()
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
    old = os.umask(0o077)
    try:
        path.mkdir(mode=0o700)
    finally:
        os.umask(old)
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise MaterialError("o diretorio de saida nao ficou 0700")
    return path


def subdirectory(parent: Path, name: str) -> Path:
    require_posix()
    path = parent / name
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


def write_new(path: Path, data: bytes, mode: int) -> None:
    """Grava um arquivo que ainda nao existe, com `O_EXCL`/`O_NOFOLLOW` e o modo pedido."""
    require_posix()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)
    os.chmod(path, mode)
