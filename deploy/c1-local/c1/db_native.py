"""Passo `db-native`: a Onda 3 local, pelos scripts de `deploy/sql` (T1.4), na ordem do README deles.

1. admin (`postgres`): `engine-native-roles.sql` com um `maezo.verifier.<login>` por login. Os
   verificadores saem de `tools.staff_materials.scram` (a mesma funcao do `generate`);
2. dono nativo (`maezo_native_schema_owner`, TLS): `engine-native-install.sql`;
3. dono de `amh` (`maezo_app`): `amh-native-source-grants.sql`.

4. admin: `deploy/sql/portal-identity-lock.sql.tmpl` renderizado por `tools.staff_materials.lock_sql`
   (variante D-D do lock de sessao e os logins do portal, com os verificadores de
   `dba/role-verifiers.json` como GUC da sessao).

Depois, o que o repo ainda NAO tem e o harness faz (pendencias listadas no README):
* D4 o witness do PORTAL sem grant em `mzo_portal_read_membership`/`mzo_human_principal` (o install so
  concede ao witness do emissor);
* a linha `MZO_HUMAN_TENANT(amh,0)` (bootstrap explicito, comentario do proprio DDL).
Por fim mede os pins (OID/dono) que o `assemble`, a composicao do engine e o emissor exigem.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import asyncpg
from tools.staff_materials import scram
from tools.staff_materials.lock_sql import render as render_lock_sql

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
EXTERNAL_DDL = Path("/repo/src/maezo/portal/engine/java/src/main/resources/external-case-schema-postgres.sql")
STAFF_OWNED = (
    "mzo_staff_case_designation_event mzo_staff_case_designation_current mzo_staff_case_source_event "
    "mzo_staff_case_source_head mzo_staff_case_publication_receipt mzo_staff_case_grant "
    "mzo_staff_case_checkpoint_chunk mzo_staff_case_checkpoint_accepted mzo_staff_case_continuity "
    "mzo_staff_case_cursor mzo_staff_native_event_head mzo_staff_case_policy_version "
    "mzo_staff_case_policy_current mzo_staff_case_policy_dependency"
).split()
PORTAL_PINNED = ("mzo_portal_read_membership", "mzo_human_principal")


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
        # README de deploy/sql, passo 4 (faltava no C1): o schema D-I que NativeCaseIdentityReader le
        # (`maezo_external.mzo_external_source_head`); DDL nao idempotente, entao so na 1a vez.
        if not await owner.fetchval("SELECT to_regclass('maezo_external.mzo_external_source_head')"):
            await owner.execute((EXTERNAL_DDL).read_text(encoding="utf-8"))
        relations = await owner.fetchval(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=$1 AND c.relkind='r'",
            NATIVE_SCHEMA,
        )
    finally:
        await owner.close()
    su = await asyncpg.connect(admin_dsn(), ssl=ssl, timeout=10)
    try:
        await su.execute((SQL / "external-case-owners.sql").read_text(encoding="utf-8"))
    finally:
        await su.close()
    app = await asyncpg.connect(admin_dsn(user="maezo_app", password_file="maezo-app-password"), ssl=ssl, timeout=10)
    try:
        await app.execute((SQL / "amh-native-source-grants.sql").read_text(encoding="utf-8"))
    finally:
        await app.close()

    # Passo 4: o template D-D da T1.4, renderizado pela ferramenta (o bloco canonico cria o schema:
    # roda uma vez por volume; `run.sh all` sempre parte do zero).
    lock_sql = render_lock_sql(
        tenant=TENANT, tenant_schema=TENANT, session_lock_login=SESSION_LOCK_LOGIN, witness_login=WITNESS_LOGIN
    )
    su = await asyncpg.connect(admin_dsn(), ssl=ssl, timeout=10)
    try:
        if not await su.fetchval("SELECT to_regnamespace('portal_identity')"):
            async with su.transaction():
                for login in (SESSION_LOCK_LOGIN, WITNESS_LOGIN):
                    await su.execute("SELECT set_config($1,$2,true)", f"maezo.verifier.{login}", verifiers[login])
                await su.execute(lock_sql)
    finally:
        await su.close()
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
        f"pins medidos (faltando: {missing or 'nenhum'}); lock-sql (T1.4) e D4 aplicados",
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
