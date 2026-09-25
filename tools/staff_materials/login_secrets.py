"""`login-secrets` — as senhas dos 4 logins nativos da Onda 3 (bloqueio B6 do plano).

Nada no repo gerava essas senhas: o harness local usa `password()` de `deploy/c1-local`. Aqui cada
login ganha um arquivo `<login>.json` = `{"username","password"}` (0400) num diretorio NOVO 0700,
no formato que `aws secretsmanager create-secret --secret-string file://<login>.json` aceita e que o
instalador in-VPC (`tools.staff_install`) le por `GetSecretValue`. O verificador SCRAM NAO sai
daqui: ele e calculado dentro da task, e nenhum dos dois passa por `containerOverrides`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .scram import new_password
from .secure_io import PRIVATE, new_private_directory, require_posix, write_new

#: Os logins de `deploy/sql/engine-native-roles.sql` (mesma ordem do array `logins` de la).
NATIVE_LOGINS = (
    "maezo_native_schema_owner",
    "maezo_native_case_issuer",
    "maezo_native_issuer_witness",
    "portal_read_source_amh",
)


@dataclass(frozen=True)
class Written:
    directory: Path
    files: tuple[Path, ...]


def secret_bytes(login: str, password: str) -> bytes:
    # Sem newline no fim: `file://` entrega o arquivo byte a byte como SecretString.
    return json.dumps({"username": login, "password": password}, separators=(",", ":")).encode("ascii")


def write(out: Path, *, forbidden: tuple[Path, ...] = ()) -> Written:
    require_posix()
    directory = new_private_directory(out, forbidden=forbidden)
    passwords: set[str] = set()
    files = []
    for login in NATIVE_LOGINS:
        password = new_password()
        while password in passwords:  # pragma: no cover - 32 bytes de entropia
            password = new_password()
        passwords.add(password)
        path = directory / f"{login}.json"
        write_new(path, secret_bytes(login, password), PRIVATE)
        files.append(path)
    return Written(directory, tuple(files))
