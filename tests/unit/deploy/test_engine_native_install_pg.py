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
_RES = _ENGINE / "java/src/main/resources"
_FAMILIES = {
    "auth": [
        "mzo_auth_" + t
        for t in (
            "installation",
            "trust",
            "revoked_key",
            "input_head",
            "input_version",
            "guide_claim",
            "instance_head",
            "doc_occurrence",
            "effect_receipt",
        )
    ],
    "consumer-lineage": [
        "mzo_human_consumer_" + t
        for t in ("database", "trust", "revoked", "qualification", "head", "pointer", "pointer_head", "link")
    ],
}


def _pin(family: str) -> str:
    return (_RES / f"native-catalog-pin-{family}.sha256").read_text(encoding="utf-8").strip()


async def _catalog_digest(conn: asyncpg.Connection, tables: list[str]) -> str:
    sql = (_RES / "native-catalog-digest-postgres.sql").read_text(encoding="utf-8")
    for n in range(1, 5):
        sql = sql.replace("?", f"${n}", 1)
    row = await conn.fetchrow(
        sql, "maezo_native", ",".join(tables), "maezo_native_schema_owner", "cibseven_app"
    )
    assert row["relations"] == len(tables)
    return row["digest"]


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
                elif name.startswith("mzo_human_consumer_"):
                    continue  # matriz do ConsumerEdgeInstallation, coberta pelo pin do catalogo
                elif name == "mzo_human_decision_binding":
                    want = (True, False, False)
                elif name == "mzo_portal_read_designation":
                    want = (True, True, False)  # UPDATE so por coluna (abaixo)
                elif name.startswith("mzo_portal_read_") or immutable.search(name):
                    want = (True, True, False)
                else:
                    want = (True, True, True)
                if (r["sel"], r["ins"], r["upd"]) != want or r["forbidden"]:
                    wrong[name] = tuple(r)
            assert wrong == {}
            # D-J.4 2.c + C1 F4b: UPDATE da designacao so nas colunas que a revogacao e o upsert da
            # publicacao escrevem; a chave (TENANT_..CATALOG_) nunca.
            cols = await engine.fetch(
                "SELECT a.attname FROM pg_attribute a"
                " WHERE a.attrelid='maezo_native.mzo_portal_read_designation'::regclass"
                " AND a.attnum>0 AND has_column_privilege(a.attrelid,a.attnum,'UPDATE') ORDER BY 1"
            )
            assert [r["attname"] for r in cols] == [
                "digest_",
                "publication_",
                "publisher_",
                "revision_",
                "revoked_",
                "source_",
                "valid_until_",
            ]
            assert (
                await engine.execute(
                    "UPDATE maezo_native.mzo_portal_read_designation"
                    " SET revoked_=true, publication_='p' WHERE false"
                )
                == "UPDATE 0"
            )
            await _refused(
                engine, "UPDATE maezo_native.mzo_portal_read_designation SET catalog_='x' WHERE false"
            )
            for table, allowed in (
                ("mzo_portal_read_membership", ["payload_", "publication_", "revision_", "source_"]),
                ("mzo_portal_read_resource", ["payload_", "publication_", "source_"]),
            ):
                cols = await engine.fetch(
                    "SELECT a.attname FROM pg_attribute a"
                    f" WHERE a.attrelid='maezo_native.{table}'::regclass"
                    " AND a.attnum>0 AND has_column_privilege(a.attrelid,a.attnum,'UPDATE') ORDER BY 1"
                )
                assert [r["attname"] for r in cols] == allowed, table
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
        owner = await env.login("maezo_native_schema_owner")
        try:
            await owner.execute("SET search_path TO ''")
            await owner.execute(ddl)  # o DONO executa o DDL (sem CREATE ROLE)
        finally:
            await owner.close()
        admin = await env.admin()
        try:
            await admin.execute(_sql("external-case-owners.sql"))
            await admin.execute(_sql("external-case-owners.sql"))
            owners = await admin.fetch(
                "SELECT DISTINCT pg_get_userbyid(c.relowner) AS o FROM pg_class c JOIN pg_namespace n"
                " ON n.oid=c.relnamespace WHERE n.nspname='maezo_external' AND c.relkind='r'"
            )
            assert [r["o"] for r in owners] == ["maezo_native_schema_owner"]
            definers = await admin.fetch(
                "SELECT DISTINCT pg_get_userbyid(p.proowner) AS o FROM pg_proc p JOIN pg_namespace n"
                " ON n.oid=p.pronamespace WHERE n.nspname='maezo_external' AND p.prosecdef"
            )
            assert {r["o"] for r in definers} <= {
                "portal_external_source_definer",
                "portal_external_checkpoint_definer",
                "portal_external_ingress_reader",
                "portal_external_publisher_definer",
            } and definers
            ext = await admin.fetch(
                "SELECT rolname, rolcanlogin, EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid"
                " OR m.roleid=r.oid) AS linked FROM pg_roles r WHERE starts_with(rolname,'portal_external_')"
            )
            assert len(ext) == 7 and not [r for r in ext if r["rolcanlogin"] or r["linked"]]
        finally:
            await admin.close()

        # D-J.4 2.a: o catalogo AUTH/consumer instalado pelo script e o pin que os instaladores Java
        # exigem; qualquer divergencia muda o digest (o Java recusa: NativeCatalogPinPgTest).
        owner = await env.login("maezo_native_schema_owner")
        try:
            for family, tables in _FAMILIES.items():
                assert await _catalog_digest(owner, tables) == _pin(family), family
            tx = owner.transaction()
            await tx.start()
            await owner.execute("GRANT DELETE ON maezo_native.mzo_auth_trust TO cibseven_app")
            assert await _catalog_digest(owner, _FAMILIES["auth"]) != _pin("auth")
            await tx.rollback()
            tx = owner.transaction()
            await tx.start()
            await owner.execute("ALTER TABLE maezo_native.mzo_human_consumer_link ADD COLUMN x_ int")
            assert await _catalog_digest(owner, _FAMILIES["consumer-lineage"]) != _pin("consumer-lineage")
            await tx.rollback()
        finally:
            await owner.close()
        issuer = await env.login("maezo_native_case_issuer")
        try:
            await issuer.fetch("SELECT tenant_, instance_, case_ FROM maezo_native.mzo_auth_guide_claim")
            await _refused(issuer, "SELECT guide_ FROM maezo_native.mzo_auth_guide_claim")
        finally:
            await issuer.close()
        engine = await env.login("cibseven_app")
        try:
            await _refused(engine, "CREATE TABLE maezo_native.mzo_auth_x(x int)")
        finally:
            await engine.close()

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
        # C1 (item 5): coluna extra nas MZO_PORTAL_READ_* (o pin Java nao cobre estas tabelas).
        "GRANT SELECT (payload_) ON maezo_native.mzo_portal_read_membership TO maezo_native_issuer_witness",
        "GRANT UPDATE (tenant_) ON maezo_native.mzo_portal_read_resource TO maezo_native_case_issuer",
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
            # D-J.4 2.b: definer externo com membership com o dono (em qualquer direcao): recusa.
            for grant in (
                "GRANT portal_external_source_definer TO maezo_native_schema_owner",
                "GRANT maezo_native_schema_owner TO portal_external_source_definer",
            ):
                await admin.execute(grant)
                with pytest.raises(asyncpg.RaiseError, match="membro|membership"):
                    await env.roles(admin)
                await admin.execute(grant.replace("GRANT", "REVOKE").replace(" TO ", " FROM "))
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


_STAFF_JAVA = _ENGINE / "java/src/main/java/br/com/maezo/human/StaffCaseStore.java"


def _designation_lock_sql() -> str:
    """O DESIGNATION_LOCK do leitor, lido do Java (a cerca segue o codigo)."""
    found = re.search(
        r'static final String DESIGNATION_LOCK="(.*?)";', _STAFF_JAVA.read_text(encoding="utf-8")
    )
    assert found, "DESIGNATION_LOCK moveu; atualize esta cerca"
    sql = found.group(1)
    for n in range(1, 5):
        sql = sql.replace("?", f"${n}", 1)
    return sql


def test_designation_writer_waits_for_the_reader_shared_lock() -> None:
    """C1 F1: o gravador da designacao (qualquer um: a trigger toma o lock EXCLUSIVO) nao muda a
    linha enquanto o leitor segura o lock COMPARTILHADO de mesma chave; muda depois do commit."""
    key = ("t", "dev", "default", "inc")

    async def check(env: Env) -> None:
        await env.run_all()
        owner = await env.login("maezo_native_schema_owner")
        reader = await env.login("cibseven_app")
        try:
            insert_event = (
                "INSERT INTO maezo_native.mzo_staff_case_designation_event"
                " VALUES($1,$2,$3,$4,$5,$6,'{}','{}')"
            )
            await owner.execute(insert_event, *key, 1, "a" * 64)
            await owner.execute(
                "INSERT INTO maezo_native.mzo_staff_case_designation_current VALUES($1,$2,$3,$4,1,$5)",
                *key,
                "a" * 64,
            )
            await owner.execute(insert_event, *key, 2, "b" * 64)
            read = reader.transaction()
            await read.start()
            await reader.execute(_designation_lock_sql(), *key)
            before = await reader.fetchval(
                "SELECT designation_digest FROM maezo_native.mzo_staff_case_designation_current"
                " WHERE tenant=$1",
                key[0],
            )
            change = (
                "UPDATE maezo_native.mzo_staff_case_designation_current"
                " SET designation_revision=2, designation_digest=$2 WHERE tenant=$1"
            )
            write = owner.transaction()
            await write.start()
            await owner.execute("SET LOCAL lock_timeout='300ms'")
            with pytest.raises(asyncpg.LockNotAvailableError):
                await owner.execute(change, key[0], "b" * 64)
            await write.rollback()
            # Outro escopo nao compartilha a chave: nao espera.
            await owner.execute(insert_event, "outro", *key[1:], 1, "c" * 64)
            after = await reader.fetchval(
                "SELECT designation_digest FROM maezo_native.mzo_staff_case_designation_current"
                " WHERE tenant=$1",
                key[0],
            )
            assert before == after == "a" * 64
            await read.commit()
            await owner.execute("SET lock_timeout='300ms'")
            await owner.execute(change, key[0], "b" * 64)
        finally:
            await reader.close()
            await owner.close()

    _run(check)


def test_portal_read_designation_revocation_is_terminal() -> None:
    """C1 (item 4): REVOKED_ (NOT NULL) preenchido nao volta atras, nem por UPDATE direto do engine."""

    async def check(env: Env) -> None:
        await env.run_all()
        eng = await env.login("cibseven_app")
        try:
            await eng.execute(
                "INSERT INTO maezo_native.mzo_portal_read_designation VALUES"
                "('t','dev','default','inc','cat',1,$1,'pub','{}','pubr',now(),false)",
                "a" * 64,
            )
            await eng.execute(
                "UPDATE maezo_native.mzo_portal_read_designation SET revoked_=true,publication_='rv'"
            )
            for sql in (
                "UPDATE maezo_native.mzo_portal_read_designation SET revoked_=false",
                "UPDATE maezo_native.mzo_portal_read_designation SET revoked_=NULL",
            ):
                with pytest.raises((asyncpg.RaiseError, asyncpg.NotNullViolationError)):
                    await eng.execute(sql)
            assert await eng.fetchval("SELECT revoked_ FROM maezo_native.mzo_portal_read_designation") is True
        finally:
            await eng.close()

    _run(check)


def test_extra_engine_column_grant_on_portal_read_does_not_survive_the_install() -> None:
    """C1 (item 5): para o proprio engine o script nao recusa, REFAZ: o REVOKE ALL por tabela leva o
    grant por coluna junto, e a postura exata confere o que sobrou."""

    async def check(env: Env) -> None:
        await env.run_all()
        owner = await env.login("maezo_native_schema_owner")
        try:
            await owner.execute(
                "GRANT UPDATE (catalog_) ON maezo_native.mzo_portal_read_designation TO cibseven_app"
            )
            await owner.execute(_sql("engine-native-install.sql"))
            assert not await owner.fetchval(
                "SELECT has_column_privilege('cibseven_app','maezo_native.mzo_portal_read_designation',"
                "'catalog_','UPDATE')"
            )
        finally:
            await owner.close()

    _run(check)
