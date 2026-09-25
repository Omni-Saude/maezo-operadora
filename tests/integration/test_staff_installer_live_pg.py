"""O instalador da Onda 3 contra um PostgreSQL REAL, com um admin NAO superusuario (como no Aurora).

Cria papeis de CLUSTER (os 6 logins, os NOLOGIN do DDL externo): so roda num PostgreSQL
DESCARTAVEL, e so com `MAEZO_STAFF_INSTALL_DISPOSABLE_PG=1` e `MAEZO_TEST_DATABASE_URL`
(superusuario desse Postgres; sem ela, o Postgres do compose). Sem isso, skip ALTO. Exemplo
(bash), um container NOVO por execucao (os papeis sao do cluster):

    docker run -d --rm --name si-pg -e POSTGRES_PASSWORD=maezo -p 127.0.0.1:55432:5432 postgres:17-alpine
    export MAEZO_STAFF_INSTALL_DISPOSABLE_PG=1
    export MAEZO_TEST_DATABASE_URL=postgresql://postgres:maezo@127.0.0.1:55432/postgres
    MAEZO_ROOT_FIXTURES=1 uv run pytest tests/integration/test_staff_installer_live_pg.py

O superusuario so monta o cenario do `bootstrap-db` (maezo_app dono do database e de `amh`,
cibseven_app, o admin membro de maezo_app). Toda a Onda 3 roda pelo admin `CREATEROLE`
sem SUPERUSER, que e o que o Aurora da ao `amh_admin`. A sessao de teste nao usa TLS (o Postgres
descartavel nao tem certificado); o `main` de producao exige TLS verificado e o unit prova isso.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from typing import Any
from urllib.parse import urlsplit

import pytest
from tools.staff_install import installer
from tools.staff_install.installer import ALL_LOGINS, OWNER_LOGIN, Credentials

# `root_fixture`: exige um PostgreSQL DESCARTAVEL (os 6 logins sao papeis de CLUSTER, com os mesmos
# nomes fixos que tests/unit/deploy/test_engine_native_install_pg.py exige ausentes); fora da lane
# global, registrado em tests/unit/ci/test_root_fixture_deselection.py.
pytestmark = [pytest.mark.integration, pytest.mark.root_fixture]

ADMIN = "staff_install_admin_like_rds"


def _dsn() -> str:
    """Mesma resolucao do resto do repo (fence `test_live_suite_defaults_are_served`)."""
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit.replace("postgresql+asyncpg://", "postgresql://", 1)
    return f"postgresql://maezo:maezo@localhost:{os.environ.get('MAEZO_PG_HOST_PORT', '5433')}/maezo"


def _base() -> Any:
    # Os papeis sao do CLUSTER: mesmo com o Postgres do compose no ar, so com opt-in explicito.
    if os.environ.get("MAEZO_STAFF_INSTALL_DISPOSABLE_PG") != "1":
        pytest.skip("COULD NOT VERIFY: exige MAEZO_STAFF_INSTALL_DISPOSABLE_PG=1 (Postgres descartavel)")
    return urlsplit(_dsn())


def test_install_on_real_postgres_with_non_superuser_admin_is_idempotent() -> None:
    asyncpg = pytest.importorskip("asyncpg")
    parts = _base()
    database = "maezo_si_" + secrets.token_hex(4)
    admin_password = secrets.token_urlsafe(24)
    passwords = {login: secrets.token_urlsafe(32) for login in ALL_LOGINS}

    async def connect_as(user: str, password: str, db: str = database) -> Any:
        return await asyncpg.connect(
            host=parts.hostname,
            port=parts.port or 5432,
            user=user,
            password=password,
            database=db,
            ssl=False,
            timeout=10,
        )

    async def scenario() -> None:
        su = await connect_as(parts.username, parts.password, parts.path.lstrip("/") or "postgres")
        try:
            for role, attrs in (
                (ADMIN, "LOGIN CREATEROLE CREATEDB NOSUPERUSER"),
                ("maezo_app", "LOGIN NOSUPERUSER"),
                ("cibseven_app", "LOGIN NOSUPERUSER"),
            ):
                if not await su.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", role):
                    await su.execute(f"CREATE ROLE {role} {attrs}")
            await su.execute(f"ALTER ROLE {ADMIN} PASSWORD '{admin_password}'")
            await su.execute(f"GRANT maezo_app TO {ADMIN}")  # como a task bootstrap-db faz
            await su.execute(f"CREATE DATABASE {database} OWNER maezo_app")
        finally:
            await su.close()
        su = await connect_as(parts.username, parts.password)
        try:
            await su.execute("SET ROLE maezo_app")
            await su.execute("CREATE SCHEMA amh")
            await su.execute(
                "CREATE TABLE amh.portal_memberships(tenant text, issuer text, subject text, "
                "principal_ref text, payload text)"
            )
            await su.execute(
                "CREATE TABLE amh.portal_sessions(tenant text, secret_hash text, "
                "expires_at timestamptz, payload text)"
            )
            await su.execute("CREATE TABLE amh.pacientes(id int)")
        finally:
            await su.close()

        credentials = Credentials(ADMIN, admin_password, passwords)
        first = await installer.install(connect_as, credentials)
        assert first["ok"], first
        assert first["negatives_must_be_false"]["admin_set_native_owner"] is False
        assert first["function_pin"]["owner"] == "portal_external_identity_reader"
        assert all(first["steps"][k] == "ok" for k in first["steps"])
        assert first["session_tls"] == {"admin": False, "owner": False}  # sessao de teste, sem TLS

        second = await installer.install(connect_as, credentials)
        assert second["ok"], second
        assert second["native_relation_pins"] == first["native_relation_pins"]
        assert second["function_pin"] == first["function_pin"]
        assert second["steps"]["lock_sql"] == second["steps"]["external_ddl"] == "ja instalado"

        # Negativos provados com os PROPRIOS logins (senha -> verificador calculado na task).
        witness = await connect_as("portal_staff_witness_amh", passwords["portal_staff_witness_amh"])
        try:
            assert await witness.fetchval("SELECT count(*) FROM maezo_native.mzo_portal_read_membership") == 0
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await witness.execute("DELETE FROM maezo_native.mzo_human_principal")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await witness.fetchval("SELECT count(*) FROM amh.portal_memberships")
        finally:
            await witness.close()
        lock = await connect_as("portal_staff_lock_amh", passwords["portal_staff_lock_amh"])
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await lock.fetchval("SELECT count(*) FROM amh.portal_sessions")
            # Entra na funcao (EXECUTE concedido) e ela nega o hash invalido com o SQLSTATE proprio.
            with pytest.raises(asyncpg.PostgresError, match="external_case_denied"):
                await lock.fetch("SELECT * FROM portal_identity.lock_external_session('x')")
        finally:
            await lock.close()
        observer = await connect_as("portal_read_source_amh", passwords["portal_read_source_amh"])
        try:
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM (SELECT tenant, issuer, subject, payload "
                    "FROM amh.portal_memberships) s"
                )
                == 0
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await observer.fetchval("SELECT count(*) FROM amh.pacientes")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await observer.fetchval("SELECT principal_ref FROM amh.portal_memberships")
        finally:
            await observer.close()
        owner = await connect_as(OWNER_LOGIN, passwords[OWNER_LOGIN])
        try:
            assert (
                await owner.fetchval(
                    "SELECT rolsuper OR rolcreaterole OR rolbypassrls FROM pg_roles "
                    "WHERE rolname=current_user"
                )
                is False
            )
        finally:
            await owner.close()

        # Atributo que so superusuario escreve, posto por fora: recusa (o admin nao o desfaria).
        su = await connect_as(parts.username, parts.password)
        try:
            await su.execute("ALTER ROLE portal_read_source_amh BYPASSRLS")
        finally:
            await su.close()
        with pytest.raises(asyncpg.RaiseError, match="BYPASSRLS"):
            await installer.install(connect_as, credentials)

    asyncio.run(scenario())
