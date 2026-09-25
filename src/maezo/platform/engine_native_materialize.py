"""Init container `engine-native-materialize` da Onda 4 (portal-autoridade-nativa-dev.md).

Recebe o segredo `maezo-operadora/dev/engine/native-materials` pelo `secrets` do ECS (versao
pinada, SecretString inteiro) em `MAEZO_ENGINE_NATIVE_SECRET`: um objeto JSON
``{"<prefixo>/<nome>": "<base64>"}``. Escreve cada arquivo, regular, dono uid/gid 1000, modo
``0400``, nos volumes da task montados em `ROOT/<prefixo>`, e fecha cada diretorio em ``0500``.
O engine (e o sidecar `staff-case-issuer`) montam esses volumes READ-ONLY.

Fail-closed, sem default: nome fora da allowlist fechada, `..`, barra extra, chave repetida,
base64 nao canonico, arquivo obrigatorio ausente, volume nao vazio ou symlink = sai com 1, sem
dizer qual (o log nao carrega nome nem conteudo de segredo).

Roda como root SO para o `fchown` para 1000 (o volume vazio do Fargate nasce root:root); a task
definition derruba toda capability exceto CHOWN e o rootfs e read-only.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

SECRET_ENV = "MAEZO_ENGINE_NATIVE_SECRET"
ISSUER_ENV = "MAEZO_ENGINE_NATIVE_STAFF_ISSUER"
ROOT = Path("/run/maezo-engine-native")
OWNER = 1000
FILE_MODE = 0o400
DIRECTORY_MODE = 0o500
MAX_FILE = 256 * 1024

# Os 11 arquivos de `tools.staff_materials.native_secret.build` (engine-native/ e engine-run/).
ENGINE_REQUIRED = frozenset(
    {
        "engine-native/server.crt",
        "engine-native/server.key",
        "engine-native/client-ca.p12",
        "engine-run/portal-read-trust.json",
        "engine-run/trust.json",
        "engine-run/portal-read-provider.json",
        "engine-run/continuity-keys.json",
        "engine-run/staff/staff-composition.json",
        "engine-run/staff/native-result.pk8",
        "engine-run/staff/auth-signing.p12",
        "engine-run/staff/auth-signing.password",
    }
)
# Fonte de membership do provider Q2 (`dsn_file`/`ca_file` do native-secret input), quando
# apontam para o volume em vez do bundle RDS da imagem.
ENGINE_OPTIONAL = frozenset({"engine-run/observer-dsn.txt", "engine-run/pg-ca.pem"})
# A composicao `staff-case-issuer-composition.v1` e os irmaos dela (deploy/c1-local/c1/issuer.py).
ISSUER_REQUIRED = frozenset(
    "staff-issuer/" + name
    for name in (
        "composition.json",
        "designation.json",
        "installation-proof.json",
        "installation-root.der",
        "case-issuer-signing-key.pem",
        "issuer-witness-signing-key.pem",
        "native-ca.pem",
        "importer-client-certificate.pem",
        "importer-client-key.pem",
        "importer-signing-key.pem",
        "issuer-dsn.txt",
        "witness-dsn.txt",
    )
)
VOLUMES = ("engine-native", "engine-run", "staff-issuer")


class MaterializeError(Exception):
    """Recusa sem detalhe: o motivo nunca carrega nome nem conteudo do segredo."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise MaterializeError()
    return value


def decode(raw: str, *, staff_issuer: bool) -> dict[str, bytes]:
    """Valida o SecretString inteiro contra a allowlist fechada e devolve nome -> bytes."""
    try:
        document = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        raise MaterializeError() from None
    if not isinstance(document, dict):
        raise MaterializeError()
    required = ENGINE_REQUIRED | (ISSUER_REQUIRED if staff_issuer else frozenset())
    allowed = required | ENGINE_OPTIONAL
    names = frozenset(document)
    if not required <= names or not names <= allowed:
        raise MaterializeError()
    files: dict[str, bytes] = {}
    for name, value in document.items():
        if not isinstance(value, str) or not value:
            raise MaterializeError()
        try:
            content = base64.b64decode(value.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error):
            raise MaterializeError() from None
        if not content or len(content) > MAX_FILE or base64.b64encode(content).decode("ascii") != value:
            raise MaterializeError()
        files[name] = content
    return files


def _open_directory(name: str, dir_fd: int | None) -> int:
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise MaterializeError()
    return fd


def _write(directory: int, name: str, content: bytes, owner: int | None) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise MaterializeError()
            view = view[written:]
        os.fchmod(fd, FILE_MODE)
        if owner is not None:
            os.fchown(fd, owner, owner)
        os.fsync(fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != FILE_MODE
            or info.st_nlink != 1
            or (owner is not None and (info.st_uid, info.st_gid) != (owner, owner))
        ):
            raise MaterializeError()
    finally:
        os.close(fd)


def _seal(fd: int, owner: int | None) -> None:
    os.fchmod(fd, DIRECTORY_MODE)
    if owner is not None:
        os.fchown(fd, owner, owner)
    os.fsync(fd)


def materialize(files: dict[str, bytes], root: Path = ROOT, owner: int | None = OWNER) -> None:
    """Escreve `files` nos volumes `root/<prefixo>`; cada volume precisa existir e estar vazio."""
    tree: dict[str, dict[tuple[str, ...], dict[str, bytes]]] = {}
    for name, content in files.items():
        parts = name.split("/")
        if len(parts) < 2:
            raise MaterializeError()
        volume, *directories, leaf = parts
        if volume not in VOLUMES or any(p in ("", ".", "..") for p in parts):
            raise MaterializeError()
        tree.setdefault(volume, {}).setdefault(tuple(directories), {})[leaf] = content
    base = _open_directory(str(root), None)
    try:
        for volume_name, subtree in sorted(tree.items()):
            top = _open_directory(volume_name, base)
            try:
                # Um unico escritor por volume novo: conteudo previo e um predecessor incerto.
                if os.listdir(top):
                    raise MaterializeError()
                opened: dict[tuple[str, ...], int] = {(): top}
                try:
                    for subdir in sorted(subtree, key=len):
                        for depth in range(1, len(subdir) + 1):
                            prefix = subdir[:depth]
                            if prefix not in opened:
                                os.mkdir(prefix[-1], mode=0o700, dir_fd=opened[prefix[:-1]])
                                opened[prefix] = _open_directory(prefix[-1], opened[prefix[:-1]])
                    for subdir, leaves in sorted(subtree.items()):
                        for leaf_name, data in sorted(leaves.items()):
                            _write(opened[subdir], leaf_name, data, owner)
                    # Mais fundo primeiro: o volume so fica 0500/1000 depois de todos os filhos.
                    for sealed in sorted(opened, key=len, reverse=True):
                        _seal(opened[sealed], owner)
                finally:
                    for opened_key, fd in opened.items():
                        if opened_key:
                            os.close(fd)
            finally:
                os.close(top)
    finally:
        os.close(base)


def main() -> None:
    try:
        raw = os.environ.pop(SECRET_ENV)
        flag = os.environ.get(ISSUER_ENV)
        if flag not in ("true", "false"):
            raise MaterializeError()
        materialize(decode(raw, staff_issuer=flag == "true"))
    except Exception:
        print("engine_native_materialize_refused", file=sys.stderr)
        raise SystemExit(1) from None
    print("engine_native_materialize_ok", file=sys.stderr)


if __name__ == "__main__":
    main()
