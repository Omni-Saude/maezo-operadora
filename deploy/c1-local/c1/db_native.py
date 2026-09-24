"""Passo `db-native`: a Onda 3 local, pelos scripts de `deploy/sql` (T1.4), na ordem do README deles.

1. admin (`postgres`): `engine-native-roles.sql` com um `maezo.verifier.<login>` por login. Os
   verificadores saem de `tools.staff_materials.scram` (a mesma funcao do `generate`);
2. dono nativo (`maezo_native_schema_owner`, TLS): `engine-native-install.sql`;
3. dono de `amh` (`maezo_app`): `amh-native-source-grants.sql`.

Depois, o que o repo ainda NAO tem e o harness faz (pendencias listadas no README):
* D3 a variante D-D de `portal_identity.lock_external_session` (plano T1.4 cita
  `deploy/sql/portal-identity-lock.sql.tmpl`, que nao existe): o bloco canonico de
  `external-case-schema-postgres.sql` com `public.` -> `amh.`, e os logins do portal
  (`portal_staff_lock_amh`, `portal_staff_witness_amh`) com os verificadores de `dba/role-verifiers.json`;
* D4 o witness do PORTAL sem grant em `mzo_portal_read_membership`/`mzo_human_principal` (o install so
  concede ao witness do emissor);
* a linha `MZO_HUMAN_TENANT(amh,0)` (bootstrap explicito, comentario do proprio DDL).
Por fim mede os pins (OID/dono) que o `assemble`, a composicao do engine e o emissor exigem.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

import asyncpg
from tools.staff_materials import scram

from maezo.portal.engine.profile import strict_loads

from .common import (
    ADMIN,
    MATERIALS,
    NATIVE_LOGINS,
    NATIVE_SCHEMA,
    OWNER_LOGIN,
    SESSION_LOCK_LOGIN,
    TENANT,
    WITNESS_LOGIN,
    admin_dsn,
    password,
    read_text,
    save_state,
    step,
    tls_context,
    write,
)

SQL = Path("/repo/deploy/sql")
EXTERNAL = Path("/repo/src/maezo/portal/engine/java/src/main/resources/external-case-schema-postgres.sql")
STAFF_OWNED = (
    "mzo_staff_case_designation_event mzo_staff_case_designation_current mzo_staff_case_source_event "
    "mzo_staff_case_source_head mzo_staff_case_publication_receipt mzo_staff_case_grant "
    "mzo_staff_case_checkpoint_chunk mzo_staff_case_checkpoint_accepted mzo_staff_case_continuity "
    "mzo_staff_case_cursor mzo_staff_native_event_head mzo_staff_case_policy_version "
    "mzo_staff_case_policy_current mzo_staff_case_policy_dependency"
).split()
PORTAL_PINNED = ("mzo_portal_read_membership", "mzo_human_principal")


def _identity_block() -> str:
    text = EXTERNAL.read_text(encoding="utf-8")
    block = re.search(r"/\* BEGIN IDENTITY DATABASE INSTALLATION.*?\n\n(.*?)\nEND IDENTITY DATABASE INSTALLATION \*/", text, re.S)
    assert block, "bloco canonico de identidade mudou de forma"
    body = block.group(1).replace("public.portal_sessions", f"{TENANT}.portal_sessions")
    body = body.replace("public.portal_memberships", f"{TENANT}.portal_memberships")
    assert "public." not in body
    return body


async def _login(role: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        admin_dsn(user=role, password_file=f"{role}-password"), ssl=tls_context(), timeout=10
    )


async def main_async() -> None:
    ssl = tls_context()
    for login in NATIVE_LOGINS:
        if not (ADMIN / f"{login}-password").exists():
            write(ADMIN / f"{login}-password", password(), 0o400)
    verifiers = strict_loads((MATERIALS / "dba" / "role-verifiers.json").read_bytes())
    su = await asyncpg.connect(admin_dsn(), ssl=ssl, timeout=10)
    try:
        for login in NATIVE_LOGINS:
            await su.execute(
                "SELECT set_config($1,$2,false)", f"maezo.verifier.{login}", scram.verifier(read_text(ADMIN / f"{login}-password"))
            )
        await su.execute((SQL / "engine-native-roles.sql").read_text(encoding="utf-8"))
        roles_ok = True
    finally:
        await su.close()
    owner = await _login(OWNER_LOGIN)
    try:
        await owner.execute((SQL / "engine-native-install.sql").read_text(encoding="utf-8"))
        await owner.execute(
            "INSERT INTO maezo_native.mzo_human_tenant(tenant_,rev_) VALUES($1,0) ON CONFLICT DO NOTHING", TENANT
        )
        relations = await owner.fetchval(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=$1 AND c.relkind='r'",
            NATIVE_SCHEMA,
        )
    finally:
        await owner.close()
    app = await asyncpg.connect(admin_dsn(user="maezo_app", password_file="maezo-app-password"), ssl=ssl, timeout=10)
    try:
        await app.execute((SQL / "amh-native-source-grants.sql").read_text(encoding="utf-8"))
    finally:
        await app.close()

    # D3/D4 (harness): identidade D-D e os dois logins do portal.
    su = await asyncpg.connect(admin_dsn(), ssl=ssl, timeout=10)
    try:
        if not await su.fetchval("SELECT to_regnamespace('portal_identity')"):
            async with su.transaction():
                await su.execute(_identity_block())
        for login in (SESSION_LOCK_LOGIN, WITNESS_LOGIN):
            exists = await su.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", login)
            await su.execute(
                f"{'ALTER' if exists else 'CREATE'} ROLE {login} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                f"NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD '{verifiers[login]}'"
            )
        await su.execute(f"GRANT USAGE ON SCHEMA portal_identity TO {SESSION_LOCK_LOGIN}")
        await su.execute(f"GRANT EXECUTE ON FUNCTION portal_identity.lock_external_session(text) TO {SESSION_LOCK_LOGIN}")
        await su.execute(
            "INSERT INTO portal_identity.external_login_tenant VALUES($1,$2) ON CONFLICT DO NOTHING",
            SESSION_LOCK_LOGIN,
            TENANT,
        )
    finally:
        await su.close()
    app = await asyncpg.connect(admin_dsn(user="maezo_app", password_file="maezo-app-password"), ssl=ssl, timeout=10)
    try:
        await app.execute(f"GRANT USAGE ON SCHEMA {TENANT} TO portal_external_identity_reader")
        await app.execute(
            f"GRANT SELECT, UPDATE(payload) ON {TENANT}.portal_sessions, {TENANT}.portal_memberships "
            "TO portal_external_identity_reader"
        )
    finally:
        await app.close()
    owner = await _login(OWNER_LOGIN)
    try:
        await owner.execute(f"GRANT USAGE ON SCHEMA {NATIVE_SCHEMA} TO {WITNESS_LOGIN}")
        await owner.execute(
            f"GRANT SELECT ON {', '.join(f'{NATIVE_SCHEMA}.{t}' for t in PORTAL_PINNED)} TO {WITNESS_LOGIN}"
        )
        rows = await owner.fetch(
            "SELECT c.relname, c.oid::bigint AS oid, pg_get_userbyid(c.relowner) AS owner FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=$1 AND c.relkind='r'",
            NATIVE_SCHEMA,
        )
    finally:
        await owner.close()
    su = await asyncpg.connect(admin_dsn(), ssl=ssl, timeout=10)
    try:
        function = await su.fetchrow(
            "SELECT p.oid::bigint AS oid, pg_get_userbyid(p.proowner) AS owner, pg_get_functiondef(p.oid) AS definition "
            "FROM pg_proc p WHERE p.oid=to_regprocedure('portal_identity.lock_external_session(text)')"
        )
    finally:
        await su.close()
    pins = {r["relname"]: {"oid": str(r["oid"]), "owner": r["owner"]} for r in rows}
    missing = [t for t in (*STAFF_OWNED, *PORTAL_PINNED, "mzo_portal_read_admission") if t not in pins]
    save_state(
        "pins",
        dict(
            relations=pins,
            lock_function=dict(
                oid=str(function["oid"]),
                owner=function["owner"],
                definition_sha256=hashlib.sha256(function["definition"].encode()).hexdigest(),
            ),
        ),
    )
    step(
        "db-native",
        roles_ok and not missing,
        f"roles.sql+install.sql+amh-grants ok; {relations} relacoes em {NATIVE_SCHEMA}; "
        f"pins medidos (faltando: {missing or 'nenhum'}); D3/D4 aplicados",
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
