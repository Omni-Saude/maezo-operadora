"""`lock-sql` — renderiza `deploy/sql/portal-identity-lock.sql.tmpl` (T1.4, variante D-D por tenant).

O template e o bloco canonico de identidade de `external-case-schema-postgres.sql` com o schema
das tabelas trocado, mais a ligacao do login do lock ao tenant. Aqui so se substituem os quatro
marcadores, e so por identificadores PostgreSQL validos: nada de texto livre entra no SQL.
"""

from __future__ import annotations

import re
from pathlib import Path

from .secure_io import MaterialError

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "deploy/sql/portal-identity-lock.sql.tmpl"
CANONICAL = REPO / "src/maezo/portal/engine/java/src/main/resources/external-case-schema-postgres.sql"
MARKERS = ("TENANT", "TENANT_SCHEMA", "SESSION_LOCK_LOGIN", "WITNESS_LOGIN")
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def render(*, tenant: str, tenant_schema: str, session_lock_login: str, witness_login: str) -> str:
    values = dict(
        TENANT=tenant,
        TENANT_SCHEMA=tenant_schema,
        SESSION_LOCK_LOGIN=session_lock_login,
        WITNESS_LOGIN=witness_login,
    )
    for name, value in values.items():
        if not _IDENTIFIER.fullmatch(value) or value == "public":
            raise MaterialError(f"{name.lower()} precisa ser um identificador PostgreSQL (e nao public)")
    if session_lock_login == witness_login:
        raise MaterialError("session-lock e witness sao logins diferentes")
    text = TEMPLATE.read_text(encoding="ascii")
    for name, value in values.items():
        text = text.replace("{{" + name + "}}", value)
    if "{{" in text or "}}" in text:
        raise MaterialError("marcador desconhecido no template")
    return text
