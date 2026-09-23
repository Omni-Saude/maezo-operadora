"""O verificador SCRAM calculado no cliente autentica num PostgreSQL REAL (tecnica da Onda 2).

`integration`: precisa de Postgres. DSN como no resto do repo: `MAEZO_TEST_DATABASE_URL`, senao
o Postgres do compose (`MAEZO_PG_HOST_PORT`, padrao 5433). Sem banco alcancavel, skip ALTO (nao
verificado), nunca verde falso. O role criado e descartado no fim.
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlsplit, urlunsplit

import pytest
from tools.staff_materials import scram

pytestmark = pytest.mark.integration


def _dsn() -> str:
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit.replace("postgresql+asyncpg://", "postgresql://", 1)
    return f"postgresql://maezo:maezo@localhost:{os.environ.get('MAEZO_PG_HOST_PORT', '5433')}/maezo"


def _as(dsn: str, user: str, password: str) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    netloc = f"{user}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


async def test_client_computed_verifier_authenticates_and_a_wrong_password_does_not() -> None:
    asyncpg = pytest.importorskip("asyncpg")
    admin_dsn = _dsn()
    try:
        admin = await asyncpg.connect(admin_dsn, timeout=5)
    except Exception:
        pytest.skip(f"COULD NOT VERIFY: Postgres inalcancavel em {urlsplit(admin_dsn).netloc.split('@')[-1]}")
    role = "t13_scram_" + secrets.token_hex(6)
    password = scram.new_password()
    try:
        # O servidor recebe SO o verificador; a senha em claro nunca passa por ele aqui.
        await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{scram.verifier(password)}'")
        stored = await admin.fetchval("SELECT rolpassword FROM pg_authid WHERE rolname=$1", role)
        assert stored.startswith("SCRAM-SHA-256$4096:")
        method = await admin.fetchval(
            "SELECT count(*) FROM pg_hba_file_rules "
            "WHERE type='host' AND auth_method IN ('scram-sha-256','md5')"
        )
        if not method:
            pytest.skip("COULD NOT VERIFY: pg_hba nao exige senha em conexao TCP neste Postgres")
        user = await asyncpg.connect(_as(admin_dsn, role, password), timeout=5)
        try:
            assert await user.fetchval("SELECT session_user") == role
        finally:
            await user.close()
        with pytest.raises(asyncpg.InvalidPasswordError):
            await asyncpg.connect(_as(admin_dsn, role, password + "x"), timeout=5)
    finally:
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()
