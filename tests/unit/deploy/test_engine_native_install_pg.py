"""T1.4: `deploy/sql/engine-native-*.sql` executados num PostgreSQL real (>= 17), com os negativos.

Cada contrato vem de uma decisao do plano `portal-autoridade-nativa-dev.md` / ADR-0060:

* `maezo_native` e `maezo_external` com dono `maezo_native_schema_owner`, CREATE so do dono,
  PUBLIC sem nada (ADR-0060 D1/D2; D-I);
* `mzo_staff_case_issuer_ledger`: `maezo_native_case_issuer` SELECT/INSERT/UPDATE, sem DELETE;
  `cibseven_app` e o witness sem grant (D-H.3);
* `maezo_native_issuer_witness` SELECT-only (D-H.2);
* `portal_read_source_amh` SELECT so em `tenant, issuer, subject, payload`, membro de nada (T1.7b);
* a tabela de admissao passa no `PIN_SQL` do provedor (T1.7a), e o script recusa um grant por
  coluna, trigger, RLS ou papel alcancavel por SET ROLE.

Os verificadores SCRAM daqui sao de senha descartavel de teste, calculados pela mesma funcao da
T1.3 (`tools.staff_materials.scram`). Sem `MAEZO_TEST_DATABASE_URL` (superusuario de um servidor
descartavel) o teste e SKIP honesto.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
from tools.staff_materials import scram

from maezo.gateway.audit_postgres import normalize_dsn

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[3]
_SQL = _ROOT / "deploy/sql"
_ENGINE = _ROOT / "src/maezo/portal/engine"
_PIN_JAVA = (
    _ENGINE / "read-provider/src/main/java/br/com/maezo/human/readprovider" / "InstalledReadProviders.java"
)
_AUTH_DDL = _ENGINE / "java/src/main/resources/human-auth-intake-documents-postgres.sql"
_LOGINS = (
    "maezo_native_schema_owner",
    "maezo_native_case_issuer",
    "maezo_native_issuer_witness",
    "portal_read_source_amh",
)


def test_generated_install_matches_the_canonical_ddl() -> None:
    result = subprocess.run(
        [sys.executable, str(_SQL / "build_engine_native_install.py"), "--check"], check=False
    )
    assert result.returncode == 0, "engine-native-install.sql desatualizado: regere com o builder"


def _pin_sql() -> str:
    """O PIN_SQL do provedor Q2, lido do Java (a cerca segue o codigo, nunca o contrario)."""
    java = _PIN_JAVA.read_text(encoding="utf-8")
    block = re.search(r"static final String PIN_SQL = (.*?);\n", java, re.S)
    assert block, "PIN_SQL moveu; atualize esta cerca"
    sql = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', block.group(1)))
    return sql.replace("?", "$1", 1).replace("?", "$2", 1)


def _dsn() -> str:
    dsn = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("COULD NOT VERIFY: MAEZO_TEST_DATABASE_URL required to execute the T1.4 SQL")
    return normalize_dsn(dsn)


def _sql(name: str) -> str:
    return (_SQL / name).read_text(encoding="utf-8")


class Env:
    def __init__(self, dsn: str, database: str, passwords: dict[str, str]):
        self.dsn, self.database, self.passwords = dsn, database, passwords

    async def admin(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.dsn, database=self.database, timeout=5)

    async def login(self, role: str) -> asyncpg.Connection:
        return await asyncpg.connect(
            self.dsn, database=self.database, user=role, password=self.passwords[role], timeout=5
        )

    async def roles(self, conn: asyncpg.Connection) -> None:
        for login in _LOGINS:
            await conn.execute(
                "SELECT set_config($1, $2, false)",
                f"maezo.verifier.{login}",
                scram.verifier(self.passwords[login]),
            )
        await conn.execute(_sql("engine-native-roles.sql"))

    async def run_all(self) -> None:
        admin = await self.admin()
        try:
            await self.roles(admin)
        finally:
            await admin.close()
        owner = await self.login("maezo_native_schema_owner")
        try:
            await owner.execute(_sql("engine-native-install.sql"))
        finally:
            await owner.close()
        app = await self.login("maezo_app")
        try:
            await app.execute(_sql("amh-native-source-grants.sql"))
        finally:
            await app.close()


async def _with_env(check) -> None:
    dsn = _dsn()
    token = uuid.uuid4().hex[:10]
    database = f"t14_{token}"
    passwords = {name: uuid.uuid4().hex for name in (*_LOGINS, "cibseven_app", "maezo_app")}
    server = await asyncpg.connect(dsn, timeout=5)
    try:
        assert await server.fetchval("SELECT current_setting('server_version_num')::int") >= 170000
        # Os nomes sao fixos (sao contrato): o servidor tem que ser descartavel e sem eles.
        existing = await server.fetchval(
            "SELECT count(*) FROM pg_roles WHERE rolname = ANY($1::text[])",
            [*_LOGINS, "cibseven_app", "maezo_app"],
        )
        assert existing == 0, "servidor de teste nao e descartavel (logins T1.4 ja existem)"
        for role in ("cibseven_app", "maezo_app"):
            await server.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{passwords[role]}'")
        await server.execute(f'CREATE DATABASE "{database}" OWNER maezo_app')
        env = Env(dsn, database, passwords)
        try:
            su = await env.admin()
            try:
                await su.execute("CREATE SCHEMA cibseven AUTHORIZATION cibseven_app")
            finally:
                await su.close()
            app = await env.login("maezo_app")
            try:
                await app.execute("CREATE SCHEMA amh")
                await app.execute(
                    "CREATE TABLE amh.portal_memberships(tenant text, issuer text, subject text,"
                    " principal_ref text, payload text, PRIMARY KEY(tenant,issuer,subject))"
                )
                await app.execute("INSERT INTO amh.portal_memberships VALUES('amh','iss','sub','p1','{}')")
            finally:
                await app.close()
            eng = await env.login("cibseven_app")
            try:
                await eng.execute("CREATE TABLE cibseven.act_ge_property(name_ text)")
            finally:
                await eng.close()
            await check(env)
        finally:
            await server.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1", database
            )
            await server.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        external = await server.fetch(
            "SELECT rolname FROM pg_roles WHERE starts_with(rolname,'portal_external_')"
        )
        for role in (*_LOGINS, "cibseven_app", "maezo_app", *(r["rolname"] for r in external)):
            await server.execute(f"DROP ROLE IF EXISTS {role}")
        await server.close()


def _run(check) -> None:
    asyncio.run(_with_env(check))


async def _refused(conn: asyncpg.Connection, sql: str, *args) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute(sql, *args)


def test_install_is_idempotent_and_every_contract_holds() -> None:
    async def check(env: Env) -> None:
        await env.run_all()
        await env.run_all()  # idempotente: a segunda passada reaplica e reconfere a postura
        admin = await env.admin()
        try:
            # Dono e CREATE dos dois schemas; PUBLIC sem nada.
            rows = await admin.fetch(
                """SELECT n.nspname, pg_get_userbyid(n.nspowner) AS owner,
                     ARRAY(SELECT DISTINCT CASE a.grantee WHEN 0 THEN 'PUBLIC'
                           ELSE pg_get_userbyid(a.grantee) END
                           FROM aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
                           WHERE a.privilege_type='CREATE') AS creators,
                     EXISTS(SELECT 1 FROM aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
                           WHERE a.grantee=0) AS public_any
                   FROM pg_namespace n WHERE n.nspname IN ('maezo_native','maezo_external')
                   ORDER BY 1"""
            )
            assert [(r["nspname"], r["owner"], r["creators"], r["public_any"]) for r in rows] == [
                ("maezo_external", "maezo_native_schema_owner", ["maezo_native_schema_owner"], False),
                ("maezo_native", "maezo_native_schema_owner", ["maezo_native_schema_owner"], False),
            ]
            # Logins: sem atributo privilegiado, membro de nada, SCRAM.
            logins = await admin.fetch(
                """SELECT r.rolname, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolbypassrls,
                     r.rolreplication, r.rolcanlogin,
                     EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid) AS member_of_any
                   FROM pg_roles r WHERE r.rolname = ANY($1::text[])""",
                list(_LOGINS),
            )
            assert len(logins) == 4
            for r in logins:
                assert (r["rolsuper"], r["rolcreaterole"], r["rolcreatedb"], r["rolbypassrls"]) == (
                    False,
                    False,
                    False,
                    False,
                )
                assert r["rolcanlogin"] and not r["rolreplication"] and not r["member_of_any"]
            # Nenhuma relacao act_% em maezo_native; relacoes do dono.
            relations = await admin.fetch(
                """SELECT c.relname, pg_get_userbyid(c.relowner) AS owner FROM pg_class c
                   JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='maezo_native' AND c.relkind='r'"""
            )
            assert relations and not [r for r in relations if r["relname"].lower().startswith("act_")]
            assert {r["owner"] for r in relations} == {"maezo_native_schema_owner"}
        finally:
            await admin.close()

        engine = await env.login("cibseven_app")
        try:
            await engine.execute("SET search_path TO maezo_native, cibseven")
            await _refused(engine, "CREATE TABLE maezo_native.shadow(x int)")
            await _refused(engine, "CREATE TABLE maezo_external.shadow(x int)")
            # Admissao: SELECT e nada mais; passa no PIN_SQL do provedor.
            await engine.fetch("SELECT * FROM mzo_portal_read_admission")
            row = (
                "INSERT INTO mzo_portal_read_admission(admission_ref_,revision_,record_,signature_)"
                " VALUES('a',1,'\\x0102',$1)"
            )
            await _refused(engine, row, b"\x00" * 64)
            await _refused(engine, "UPDATE mzo_portal_read_admission SET revoked_=true")
            await _refused(engine, "DELETE FROM mzo_portal_read_admission")
            pin = await engine.fetchrow(_pin_sql(), "maezo_native", "mzo_portal_read_admission")
            assert pin["owner"] == "maezo_native_schema_owner" and pin["kind"] == "r"
            assert pin["can_select"] and not pin["can_write"]
            assert (pin["foreign_acl"], pin["engine_select"]) == (0, 1)
            for flag in (
                "column_acl",
                "has_trigger",
                "has_rule",
                "row_security",
                "owner_member",
                "write_all",
                "privileged",
            ):
                assert pin[flag] is False, flag
            # Ledger: sem grant nenhum.
            await _refused(engine, "SELECT * FROM mzo_staff_case_issuer_ledger")
            # O ACT_* continua resolvendo em cibseven, atras de maezo_native.
            assert await engine.fetchval("SELECT to_regclass('act_ge_property')::text") == "act_ge_property"
        finally:
            await engine.close()

        issuer = await env.login("maezo_native_case_issuer")
        try:
            await issuer.execute(
                "INSERT INTO maezo_native.mzo_staff_case_issuer_ledger"
                "(tenant,environment,engine_name,database_incarnation,policy_ref,revision,issued_state)"
                " VALUES('amh','dev','default','inc','staff-escalation-routing@d1',0,'\\x7b7d')"
            )
            updated = await issuer.execute(
                "UPDATE maezo_native.mzo_staff_case_issuer_ledger SET revision=1 WHERE revision=0"
            )
            assert updated == "UPDATE 1"
            await _refused(issuer, "DELETE FROM maezo_native.mzo_staff_case_issuer_ledger")
            await _refused(issuer, "TRUNCATE maezo_native.mzo_staff_case_issuer_ledger")
            await _refused(issuer, "SELECT * FROM maezo_native.mzo_portal_read_admission")
            await _refused(issuer, "SELECT principal_ref FROM amh.portal_memberships")
            assert await issuer.fetchval("SELECT count(payload) FROM amh.portal_memberships") == 1
        finally:
            await issuer.close()

        witness = await env.login("maezo_native_issuer_witness")
        try:
            await witness.fetch("SELECT * FROM maezo_native.mzo_portal_read_membership")
            await witness.fetch("SELECT * FROM maezo_native.mzo_human_principal")
            await _refused(witness, "DELETE FROM maezo_native.mzo_portal_read_membership")
            await _refused(witness, "SELECT * FROM maezo_native.mzo_staff_case_issuer_ledger")
            writable = await witness.fetchval(
                """SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='maezo_native' AND c.relkind='r'
                     AND (has_any_column_privilege(c.oid,'INSERT') OR has_any_column_privilege(c.oid,'UPDATE')
                          OR has_table_privilege(c.oid,'DELETE,TRUNCATE'))"""
            )
            assert writable == 0
        finally:
            await witness.close()

        source = await env.login("portal_read_source_amh")
        try:
            got = await source.fetchrow("SELECT tenant, issuer, subject, payload FROM amh.portal_memberships")
            assert tuple(got) == ("amh", "iss", "sub", "{}")
            await _refused(source, "SELECT principal_ref FROM amh.portal_memberships")
            await _refused(source, "SELECT * FROM amh.portal_memberships")
            await _refused(source, "UPDATE amh.portal_memberships SET payload='x'")
            await _refused(source, "SELECT * FROM maezo_native.mzo_portal_read_admission")
            # Postura que o MembershipSourceObserver exige (T1.7b).
            posture = await source.fetchrow(
                """SELECT has_any_column_privilege(session_user,c.oid,'INSERT') AS ins,
                     has_any_column_privilege(session_user,c.oid,'UPDATE') AS upd,
                     has_table_privilege(session_user,c.oid,'DELETE') AS del,
                     has_table_privilege(session_user,c.oid,'TRUNCATE') AS trunc,
                     EXISTS(SELECT 1 FROM pg_auth_members a JOIN pg_roles s ON s.oid=a.member
                            WHERE s.rolname=session_user) AS member_of_any
                   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='amh' AND c.relname='portal_memberships'"""
            )
            assert tuple(posture) == (False, False, False, False, False)
        finally:
            await source.close()

        # D-J.2c: matriz DML do engine nas relacoes deste script; DELETE recusado de verdade.
        engine = await env.login("cibseven_app")
        try:
            matrix = await engine.fetch(
                """SELECT c.relname,
                     has_table_privilege(c.oid,'SELECT') AS sel, has_table_privilege(c.oid,'INSERT') AS ins,
                     has_table_privilege(c.oid,'UPDATE') AS upd,
                     has_table_privilege(c.oid,'DELETE,TRUNCATE,REFERENCES,TRIGGER') AS forbidden
                   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='maezo_native' AND c.relkind='r'
                     AND (starts_with(c.relname,'mzo_human_') OR starts_with(c.relname,'mzo_portal_read_'))"""
            )
            immutable = re.compile(r"_(event|receipt|continuity|cursor|version|dependency|chunk|ledger)$")
            assert len(matrix) > 10
            wrong = {}
            for r in matrix:
                name = r["relname"]
                if name == "mzo_portal_read_admission":
                    want = (True, False, False)
                elif name == "mzo_portal_read_designation":
                    want = (True, True, True)
                elif name.startswith("mzo_portal_read_") or immutable.search(name):
                    want = (True, True, False)
                else:
                    want = (True, True, True)
                if (r["sel"], r["ins"], r["upd"]) != want or r["forbidden"]:
                    wrong[name] = tuple(r)
            assert wrong == {}
            await _refused(engine, "DELETE FROM maezo_native.mzo_human_tenant")
            await _refused(engine, "DELETE FROM maezo_native.mzo_portal_read_publication_receipt")
            await _refused(engine, "TRUNCATE maezo_native.mzo_human_principal")
            await _refused(engine, "UPDATE maezo_native.mzo_portal_read_publication_receipt SET receipt_=''")
            # D-J.3: a consulta do ExternalCaseReadCommand, qualificada, com search_path vazio.
            await engine.execute("SET search_path TO ''")
            java = (_ENGINE / "java/src/main/java/br/com/maezo/human/ExternalCaseReadCommand.java").read_text(
                encoding="utf-8"
            )
            assert (
                'FROM \\""+StaffCaseStore.schema(nativeSchema)+"\\".MZO_PORTAL_READ_PUBLICATION_RECEIPT'
                in java
            )
            sql = (
                'SELECT KEY_FINGERPRINT_ FROM "maezo_native".MZO_PORTAL_READ_PUBLICATION_RECEIPT WHERE '
                "TENANT_=$1 AND ENVIRONMENT_=$2 AND ENGINE_=$3 AND INCARNATION_=$4 AND PUBLICATION_=$5"
            )
            assert await engine.fetch(sql, "amh", "dev", "e", "i", "p") == []
            with pytest.raises(asyncpg.UndefinedTableError):
                await engine.fetch("SELECT 1 FROM MZO_PORTAL_READ_PUBLICATION_RECEIPT")
        finally:
            await engine.close()

        # D-J.2b: o DDL externo requalificado instala sobre o layout D-C2 (nada em public).
        ddl = (_ENGINE / "java/src/main/resources/external-case-schema-postgres.sql").read_text(
            encoding="utf-8"
        )
        assert not re.search(r"public\.mzo_", ddl, re.I)
        admin = await env.admin()
        try:
            await admin.execute("SET search_path TO ''")
            await admin.execute(ddl.replace("CREATE SCHEMA maezo_external;", "", 1))
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
                    " WHERE n.nspname='maezo_external' AND c.relkind='r'"
                )
                > 5
            )
        finally:
            await admin.close()

        # O grant de coluna em mzo_auth_guide_claim, depois da instalacao AUTH (simulada pelo dono).
        owner = await env.login("maezo_native_schema_owner")
        try:
            await _refused_or_raise(owner, _sql("engine-native-post-auth-grants.sql"))
            await owner.execute("SET search_path TO maezo_native")
            await owner.execute(_AUTH_DDL.read_text(encoding="utf-8"))
            await owner.execute(_sql("engine-native-post-auth-grants.sql"))
            await owner.execute(_sql("engine-native-post-auth-grants.sql"))
        finally:
            await owner.close()
        issuer = await env.login("maezo_native_case_issuer")
        try:
            await issuer.fetch("SELECT tenant_, instance_, case_ FROM maezo_native.mzo_auth_guide_claim")
            await _refused(issuer, "SELECT guide_ FROM maezo_native.mzo_auth_guide_claim")
        finally:
            await issuer.close()

    _run(check)


async def _refused_or_raise(conn: asyncpg.Connection, sql: str) -> None:
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(sql)


def test_install_refuses_the_posture_the_runtime_refuses() -> None:
    """Negativos: cada coisa que a postura T1.7a recusa faz a reexecucao do script falhar."""

    breakers = (
        "GRANT UPDATE (revoked_) ON maezo_native.mzo_portal_read_admission TO cibseven_app",
        "GRANT SELECT (record_) ON maezo_native.mzo_portal_read_admission TO maezo_native_issuer_witness",
        "CREATE FUNCTION maezo_native.keep() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN OLD; END$$;"
        " CREATE TRIGGER keep BEFORE UPDATE ON maezo_native.mzo_portal_read_admission"
        " FOR EACH ROW EXECUTE FUNCTION maezo_native.keep()",
        "ALTER TABLE maezo_native.mzo_portal_read_admission ENABLE ROW LEVEL SECURITY",
        "GRANT INSERT ON maezo_native.mzo_portal_read_admission TO maezo_native_issuer_witness",
        "GRANT DELETE ON maezo_native.mzo_staff_case_issuer_ledger TO maezo_native_case_issuer",
        "CREATE TABLE maezo_native.act_ru_task(id_ text)",
    )

    async def check(env: Env) -> None:
        await env.run_all()
        for breaker in breakers:
            owner = await env.login("maezo_native_schema_owner")
            try:
                tx = owner.transaction()
                await tx.start()
                await owner.execute(breaker)
                with pytest.raises(asyncpg.RaiseError, match="postura|matriz do ledger|act_"):
                    await owner.execute(_sql("engine-native-install.sql"))
                await tx.rollback()
            finally:
                await owner.close()

    _run(check)


def test_roles_refuse_set_role_reachability_bad_verifier_and_foreign_owner() -> None:
    async def check(env: Env) -> None:
        await env.run_all()
        admin = await env.admin()
        try:
            # rolcreaterole concedido por fora e normalizado de volta.
            await admin.execute("ALTER ROLE portal_read_source_amh CREATEROLE")
            await env.roles(admin)
            assert (
                await admin.fetchval(
                    "SELECT rolcreaterole FROM pg_roles WHERE rolname=$1", "portal_read_source_amh"
                )
                is False
            )
            # Papel alcancavel por SET ROLE (INHERIT FALSE): recusa.
            await admin.execute("GRANT maezo_app TO portal_read_source_amh WITH INHERIT FALSE, SET TRUE")
            with pytest.raises(asyncpg.RaiseError, match="membro de um papel"):
                await env.roles(admin)
            await admin.execute("REVOKE maezo_app FROM portal_read_source_amh")
            # Alguem membro de um dos logins (o engine no dono nativo): recusa nos dois scripts.
            await admin.execute("GRANT maezo_native_schema_owner TO cibseven_app")
            with pytest.raises(asyncpg.RaiseError, match="alguem e membro de maezo_native_schema_owner"):
                await env.roles(admin)
            owner = await env.login("maezo_native_schema_owner")
            try:
                with pytest.raises(asyncpg.RaiseError, match="alcanca o dono nativo"):
                    await owner.execute(_sql("engine-native-install.sql"))
            finally:
                await owner.close()
            await admin.execute("REVOKE maezo_native_schema_owner FROM cibseven_app")
            await admin.execute(
                "GRANT maezo_native_schema_owner TO cibseven_app WITH INHERIT FALSE, SET TRUE"
            )
            owner = await env.login("maezo_native_schema_owner")
            try:
                with pytest.raises(asyncpg.RaiseError, match="alcanca o dono nativo"):
                    await owner.execute(_sql("engine-native-install.sql"))
            finally:
                await owner.close()
            await admin.execute("REVOKE maezo_native_schema_owner FROM cibseven_app")
            # Verificador que nao e SCRAM: recusa (nunca uma senha em claro).
            await admin.execute(
                "SELECT set_config($1,'hunter2',false)", "maezo.verifier.maezo_native_case_issuer"
            )
            with pytest.raises(asyncpg.RaiseError, match="SCRAM"):
                await admin.execute(_sql("engine-native-roles.sql"))
            # Schema com outro dono: recusa, nao toma posse.
            await env.roles(admin)
            await admin.execute("ALTER SCHEMA maezo_external OWNER TO maezo_app")
            with pytest.raises(asyncpg.RaiseError, match="pertence a maezo_app"):
                await env.roles(admin)
            await admin.execute("ALTER SCHEMA maezo_external OWNER TO maezo_native_schema_owner")
            # CREATE concedido por fora e retirado na reexecucao.
            await admin.execute("GRANT CREATE ON SCHEMA maezo_native TO cibseven_app, PUBLIC")
            await env.roles(admin)
            assert not await admin.fetchval(
                "SELECT has_schema_privilege($1,'maezo_native','CREATE')", "cibseven_app"
            )
            assert not await admin.fetchval(
                "SELECT has_schema_privilege($1,'maezo_native','CREATE')", "maezo_app"
            )
        finally:
            await admin.close()
        # Script do dono rodado por outro login: recusa.
        engine = await env.login("cibseven_app")
        try:
            with pytest.raises(asyncpg.PostgresError):
                await engine.execute(_sql("engine-native-install.sql"))
        finally:
            await engine.close()

    _run(check)
