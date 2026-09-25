"""Onda 3 inteira, idempotente, numa task avulsa in-VPC (bloqueio B5 do plano).

Referencia executavel: `deploy/c1-local/c1/db_native.py` (harness local). A ordem e a mesma:

1. admin: `engine-native-roles.sql` com `maezo.verifier.<login>` dos 4 logins nativos;
2. dono nativo (login proprio, TLS): `engine-native-install.sql`, a linha `MZO_HUMAN_TENANT(amh,0)`
   e `external-case-schema-postgres.sql` (so na 1a vez: o DDL externo nao e idempotente);
3. admin: `external-case-owners.sql`;
4. `maezo_app` (por `SET ROLE`; o admin e membro dele desde a task `bootstrap-db`):
   `amh-native-source-grants.sql`;
5. admin: o lock D-D renderizado por `tools.staff_materials.lock_sql` (so na 1a vez);
6. dono nativo: D4, o witness do PORTAL com SELECT nas duas relacoes pinadas;
7. admin: `REVOKE maezo_native_schema_owner FROM <admin>` e os blocos `$roles$`/`$external$` de
   `engine-native-roles.sql` de novo, que agora provam a postura (recusam qualquer membro alem do
   ADMIN implicito sem INHERIT/SET). O `$schemas$` nao: ele exige ser dono do schema;
8. mede e imprime os pins PUBLICOS.

O que difere do harness, e por que: la o admin e `postgres` (superusuario); no Aurora ele e so
`rds_superuser`/CREATEROLE, e o PostgreSQL 16+ exige, para `CREATE SCHEMA ... AUTHORIZATION`,
`ALTER ... OWNER TO` e reatribuicao de funcao, que o admin possa `SET ROLE` no dono de destino e que
este tenha CREATE no schema. Por isso:

* o `engine-native-roles.sql` roda em dois tempos: os blocos `$roles$`/`$external$` (que exigem que
  ninguem alem do ADMIN implicito seja membro dos logins), depois o `GRANT <dono> TO <admin> WITH
  INHERIT TRUE, SET TRUE` (README de deploy/sql, D-J.2e), e so entao `$schemas$`;
* o passo 3 concede, na mesma transacao e so durante ela, SET nos papeis definer e CREATE em
  `maezo_external` a eles, e desfaz tudo antes do COMMIT;
* o passo 5 injeta a mesma dupla (SET em `portal_external_identity_reader` e CREATE temporario em
  `portal_identity`) em volta do unico `ALTER FUNCTION ... OWNER TO` do template, e desfaz logo depois.
  O template renderizado fica igual; a injecao e ancorada numa linha exata e falha fechado.

Nada aqui imprime senha, verificador ou DSN. Mensagens de erro carregam so o tipo e o texto do
PostgreSQL (que nunca ecoa o SQL enviado).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from tools.staff_materials import scram
from tools.staff_materials.lock_sql import render as render_lock_sql

REPO = Path(__file__).resolve().parents[2]
SQL_DIR = REPO / "deploy/sql"
EXTERNAL_DDL = REPO / "src/maezo/portal/engine/java/src/main/resources/external-case-schema-postgres.sql"

TENANT = "amh"
NATIVE_SCHEMA = "maezo_native"
EXTERNAL_SCHEMA = "maezo_external"
OWNER_LOGIN = "maezo_native_schema_owner"
ISSUER_LOGIN = "maezo_native_case_issuer"
ISSUER_WITNESS_LOGIN = "maezo_native_issuer_witness"
OBSERVER_LOGIN = "portal_read_source_amh"
NATIVE_LOGINS = (OWNER_LOGIN, ISSUER_LOGIN, ISSUER_WITNESS_LOGIN, OBSERVER_LOGIN)
SESSION_LOCK_LOGIN = f"portal_staff_lock_{TENANT}"
WITNESS_LOGIN = f"portal_staff_witness_{TENANT}"
PORTAL_LOGINS = (SESSION_LOCK_LOGIN, WITNESS_LOGIN)
ALL_LOGINS = (*NATIVE_LOGINS, *PORTAL_LOGINS)
APP_ROLE = "maezo_app"
ENGINE_ROLE = "cibseven_app"
IDENTITY_READER = "portal_external_identity_reader"

STAFF_OWNED = (
    "mzo_staff_case_designation_event",
    "mzo_staff_case_designation_current",
    "mzo_staff_case_source_event",
    "mzo_staff_case_source_head",
    "mzo_staff_case_publication_receipt",
    "mzo_staff_case_grant",
    "mzo_staff_case_checkpoint_chunk",
    "mzo_staff_case_checkpoint_accepted",
    "mzo_staff_case_continuity",
    "mzo_staff_case_cursor",
    "mzo_staff_native_event_head",
    "mzo_staff_case_policy_version",
    "mzo_staff_case_policy_current",
    "mzo_staff_case_policy_dependency",
)
PORTAL_PINNED = ("mzo_portal_read_membership", "mzo_human_principal")
REQUIRED_RELATIONS = (*STAFF_OWNED, *PORTAL_PINNED, "mzo_portal_read_admission")

LOCK_ALTER_LINE = f"ALTER FUNCTION portal_identity.lock_external_session(text) OWNER TO {IDENTITY_READER};"
PINS_SCHEMA = "maezo-staff-install-pins.v1"


class InstallError(RuntimeError):
    """Recusa do instalador. A mensagem nunca carrega senha, verificador ou DSN."""


class Connection(Protocol):
    async def execute(self, query: str, *args: Any) -> str: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def close(self) -> None: ...
    def transaction(self) -> Any: ...


Connector = Callable[[str, str], Awaitable[Connection]]


# --------------------------------------------------------------------------- segredos


def parse_credential(secret_string: str, expected_login: str) -> str:
    """Senha de um segredo `{"username","password"}` ou de uma DSN `postgresql[+asyncpg]://`.

    O usuario tem de ser o login esperado: um ARN trocado entre logins e recusa, nao instalacao
    silenciosa com a senha de outro papel.
    """
    text = secret_string.strip()
    if text.startswith("{"):
        try:
            value = json.loads(text)
        except ValueError:
            raise InstallError(f"segredo de {expected_login}: JSON invalido") from None
        if not isinstance(value, dict) or set(value) - {
            "username",
            "password",
            "engine",
            "host",
            "port",
            "dbname",
        }:
            raise InstallError(f"segredo de {expected_login}: esperado {{username,password}}")
        username, password = value.get("username"), value.get("password")
    elif re.match(r"postgres(ql)?(\+asyncpg)?://", text):
        parts = urlsplit(text)
        username = unquote(parts.username) if parts.username else None
        password = unquote(parts.password) if parts.password else None
    else:
        raise InstallError(f"segredo de {expected_login}: formato desconhecido (JSON ou DSN)")
    if username != expected_login:
        raise InstallError(f"segredo de {expected_login}: username nao e o login esperado")
    if not isinstance(password, str) or len(password) < 16:
        raise InstallError(f"segredo de {expected_login}: senha ausente ou curta demais")
    return password


def parse_admin(secret_string: str) -> tuple[str, str]:
    """O segredo mestre gerido pelo RDS: `{"username","password"}`."""
    try:
        value = json.loads(secret_string)
    except ValueError:
        raise InstallError("segredo admin: JSON invalido") from None
    if not isinstance(value, dict):
        raise InstallError("segredo admin: esperado objeto JSON")
    user, password = value.get("username"), value.get("password")
    if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", user):
        raise InstallError("segredo admin: username invalido")
    if not isinstance(password, str) or not password:
        raise InstallError("segredo admin: senha ausente")
    return user, password


def parse_login_arns(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except ValueError:
        raise InstallError("STAFF_INSTALL_LOGIN_SECRET_ARNS: JSON invalido") from None
    if not isinstance(value, dict) or set(value) != set(ALL_LOGINS):
        raise InstallError(f"STAFF_INSTALL_LOGIN_SECRET_ARNS: exige exatamente {sorted(ALL_LOGINS)}")
    for login, arn in value.items():
        if not isinstance(arn, str) or not arn.startswith("arn:aws:secretsmanager:"):
            raise InstallError(f"STAFF_INSTALL_LOGIN_SECRET_ARNS: ARN invalido para {login}")
    return value


@dataclass(frozen=True)
class Credentials:
    admin_user: str
    admin_password: str
    passwords: Mapping[str, str]


def fetch_credentials(client: Any, admin_arn: str, login_arns: Mapping[str, str]) -> Credentials:
    def get(arn: str) -> str:
        response = client.get_secret_value(SecretId=arn)
        value = response.get("SecretString")
        if not isinstance(value, str):
            raise InstallError("segredo sem SecretString")
        return value

    admin_user, admin_password = parse_admin(get(admin_arn))
    passwords = {login: parse_credential(get(login_arns[login]), login) for login in ALL_LOGINS}
    if len(set(passwords.values())) != len(passwords):
        raise InstallError("dois logins com a mesma senha: recuse e gere de novo")
    return Credentials(admin_user, admin_password, passwords)


# --------------------------------------------------------------------------- SQL


def split_roles_sql(text: str) -> dict[str, str]:
    """Os tres blocos `DO $tag$ ... END $tag$;` de `engine-native-roles.sql`, pela tag.

    O cabecalho de comentarios vai junto do primeiro bloco. Recusa se a concatenacao nao
    reproduzir o arquivo byte a byte (bloco novo ou texto fora de bloco).
    """
    starts = [m.start() for m in re.finditer(r"(?m)^DO \$(\w+)\$", text)]
    tags = re.findall(r"(?m)^DO \$(\w+)\$", text)
    if tags != ["roles", "external", "schemas"]:
        raise InstallError(f"engine-native-roles.sql: blocos inesperados {tags}")
    bounds = [0, *starts[1:], len(text)]
    blocks = {tag: text[bounds[i] : bounds[i + 1]] for i, tag in enumerate(tags)}
    if "".join(blocks.values()) != text:
        raise InstallError("engine-native-roles.sql: divisao nao reproduz o arquivo")
    return blocks


def patch_lock_sql(text: str) -> str:
    """Envolve o unico `ALTER FUNCTION ... OWNER TO` do lock com SET/CREATE temporarios (ver modulo)."""
    if text.count(LOCK_ALTER_LINE) != 1:
        raise InstallError("lock-sql: ancora do ALTER FUNCTION ausente ou repetida")
    before = (
        f"GRANT {IDENTITY_READER} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;\n"
        f"GRANT CREATE ON SCHEMA portal_identity TO {IDENTITY_READER};\n"
    )
    after = (
        f"\nREVOKE CREATE ON SCHEMA portal_identity FROM {IDENTITY_READER};\n"
        f"REVOKE {IDENTITY_READER} FROM CURRENT_USER;"
    )
    return text.replace(LOCK_ALTER_LINE, before + LOCK_ALTER_LINE + after)


def definer_roles(owners_sql: str) -> list[str]:
    roles = sorted(set(re.findall(r"OWNER TO (portal_external_\w+);", owners_sql)))
    if not roles:
        raise InstallError("external-case-owners.sql: nenhum papel definer encontrado")
    return roles


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- instalacao


async def _set_verifiers(
    conn: Connection, passwords: Mapping[str, str], logins: tuple[str, ...], *, local: bool
) -> None:
    for login in logins:
        await conn.execute(
            "SELECT set_config($1, $2, $3)",
            f"maezo.verifier.{login}",
            scram.verifier(passwords[login]),
            local,
        )


async def _session_tls(conn: Connection) -> bool:
    return bool(await conn.fetchval("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"))


async def install(
    connect: Connector,
    credentials: Credentials,
    *,
    sql_dir: Path = SQL_DIR,
    external_ddl: Path = EXTERNAL_DDL,
) -> dict[str, Any]:
    admin, pw = credentials.admin_user, credentials.passwords
    roles_sql = _read(sql_dir / "engine-native-roles.sql")
    blocks = split_roles_sql(roles_sql)
    owners_sql = _read(sql_dir / "external-case-owners.sql")
    definers = definer_roles(owners_sql)
    steps: dict[str, str] = {}
    tls: dict[str, bool] = {}

    async def as_admin() -> Connection:
        return await connect(admin, credentials.admin_password)

    # 1. roles (em dois tempos, ver o docstring do modulo)
    su = await as_admin()
    try:
        tls["admin"] = await _session_tls(su)
        # Estado limpo: sobra de uma execucao interrompida (GRANT explicito ao admin) faria o
        # proprio roles.sql recusar. REVOKE do que nao foi concedido e so WARNING.
        existing = {
            r["rolname"]
            for r in await su.fetch(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::name[])",
                [OWNER_LOGIN, IDENTITY_READER, *definers],
            )
        }
        for role in (OWNER_LOGIN, IDENTITY_READER, *definers):
            if role in existing:
                await su.execute(f"REVOKE {role} FROM CURRENT_USER")
        await _set_verifiers(su, pw, NATIVE_LOGINS, local=False)
        await su.execute(blocks["roles"])
        await su.execute(blocks["external"])
        await su.execute(f"GRANT {OWNER_LOGIN} TO CURRENT_USER WITH INHERIT TRUE, SET TRUE")
        await su.execute(blocks["schemas"])
        steps["roles"] = "ok"
    finally:
        await su.close()

    # 2. dono nativo: install, tenant, DDL externo
    owner = await connect(OWNER_LOGIN, pw[OWNER_LOGIN])
    try:
        tls["owner"] = await _session_tls(owner)
        await owner.execute(_read(sql_dir / "engine-native-install.sql"))
        await owner.execute(
            f"INSERT INTO {NATIVE_SCHEMA}.mzo_human_tenant(tenant_, rev_) VALUES($1, 0) "
            "ON CONFLICT DO NOTHING",
            TENANT,
        )
        steps["install"] = "ok"
        if await owner.fetchval(f"SELECT to_regclass('{EXTERNAL_SCHEMA}.mzo_external_source_head')"):
            steps["external_ddl"] = "ja instalado"
        else:
            await owner.execute(_read(external_ddl))
            steps["external_ddl"] = "ok"
    finally:
        await owner.close()

    # 3. admin: donos das funcoes SECURITY DEFINER; 4. maezo_app: grants de amh
    su = await as_admin()
    try:
        async with su.transaction():
            for role in definers:
                await su.execute(f"GRANT {role} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE")
                await su.execute(f"GRANT CREATE ON SCHEMA {EXTERNAL_SCHEMA} TO {role}")
            await su.execute(owners_sql)
            for role in definers:
                await su.execute(f"REVOKE CREATE ON SCHEMA {EXTERNAL_SCHEMA} FROM {role}")
                await su.execute(f"REVOKE {role} FROM CURRENT_USER")
        steps["external_owners"] = "ok"
        async with su.transaction():
            await su.execute(f"SET LOCAL ROLE {APP_ROLE}")
            await su.execute(_read(sql_dir / "amh-native-source-grants.sql"))
        steps["amh_grants"] = "ok"

        # 5. lock D-D (uma vez; o bloco canonico cria o schema e nao e reexecutavel)
        if await su.fetchval("SELECT to_regnamespace('portal_identity')"):
            steps["lock_sql"] = "ja instalado"
        else:
            lock_sql = render_lock_sql(
                tenant=TENANT,
                tenant_schema=TENANT,
                session_lock_login=SESSION_LOCK_LOGIN,
                witness_login=WITNESS_LOGIN,
            )
            async with su.transaction():
                await _set_verifiers(su, pw, PORTAL_LOGINS, local=True)
                await su.execute(patch_lock_sql(lock_sql))
            steps["lock_sql"] = "ok"
    finally:
        await su.close()

    # 6. D4: witness do portal
    owner = await connect(OWNER_LOGIN, pw[OWNER_LOGIN])
    try:
        await owner.execute(f"GRANT USAGE ON SCHEMA {NATIVE_SCHEMA} TO {WITNESS_LOGIN}")
        await owner.execute(
            f"GRANT SELECT ON {', '.join(f'{NATIVE_SCHEMA}.{t}' for t in PORTAL_PINNED)} TO {WITNESS_LOGIN}"
        )
        steps["d4_witness"] = "ok"
    finally:
        await owner.close()

    # 7. REVOKE do dono ao admin e prova da postura; 8. pins
    su = await as_admin()
    try:
        await su.execute(f"REVOKE {OWNER_LOGIN} FROM CURRENT_USER")
        await _set_verifiers(su, pw, NATIVE_LOGINS, local=False)
        # So `$roles$`/`$external$`: sao eles que provam "ninguem alem do ADMIN implicito". O
        # `$schemas$` (REVOKE/GRANT no schema) exige ser o dono, que o admin acabou de deixar de ser;
        # a postura do schema ja foi aplicada no passo 1 e e medida abaixo.
        await su.execute(blocks["roles"])
        await su.execute(blocks["external"])
        steps["revoke_owner_and_recheck"] = "ok"
        pins = await measure(su)
    finally:
        await su.close()
    pins["steps"] = steps
    pins["session_tls"] = tls
    return pins


async def measure(conn: Connection) -> dict[str, Any]:
    rows = await conn.fetch(
        "SELECT c.relname, c.oid::bigint AS oid, pg_get_userbyid(c.relowner) AS owner FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = $1 AND c.relkind = 'r' ORDER BY c.relname",
        NATIVE_SCHEMA,
    )
    relations = {r["relname"]: {"oid": str(r["oid"]), "owner": r["owner"]} for r in rows}
    function = await conn.fetchrow(
        "SELECT p.oid::bigint AS oid, pg_get_userbyid(p.proowner) AS owner, "
        "encode(sha256(convert_to(pg_get_functiondef(p.oid), 'UTF8')), 'hex') AS definition_sha256 "
        "FROM pg_proc p WHERE p.oid = to_regprocedure('portal_identity.lock_external_session(text)')"
    )
    schema = await conn.fetchrow(
        "SELECT n.nspname, pg_get_userbyid(n.nspowner) AS owner FROM pg_namespace n WHERE n.nspname = $1",
        NATIVE_SCHEMA,
    )

    def rel(schema_name: str, table: str) -> str:
        # Por OID, nao por nome: resolver `schema.tabela` exige USAGE no schema, e o admin ja nao
        # tem nenhum em `maezo_native` depois do REVOKE (que e justamente o que se quer medir).
        return (
            "(SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = '{schema_name}' AND c.relname = '{table}')"
        )

    def table_priv(role: str, schema_name: str, table: str, priv: str) -> str:
        return f"has_table_privilege('{role}', {rel(schema_name, table)}, '{priv}')"

    checks = {
        "cibseven_app_create_maezo_native": (
            f"has_schema_privilege('{ENGINE_ROLE}','{NATIVE_SCHEMA}','CREATE')"
        ),
        "cibseven_app_create_maezo_external": (
            f"has_schema_privilege('{ENGINE_ROLE}','{EXTERNAL_SCHEMA}','CREATE')"
        ),
        "maezo_app_create_maezo_native": f"has_schema_privilege('{APP_ROLE}','{NATIVE_SCHEMA}','CREATE')",
        "cibseven_app_insert_admission": table_priv(
            ENGINE_ROLE, NATIVE_SCHEMA, "mzo_portal_read_admission", "INSERT"
        ),
        "observer_usage_maezo_native": f"has_schema_privilege('{OBSERVER_LOGIN}','{NATIVE_SCHEMA}','USAGE')",
        "lock_login_select_amh_portal_sessions": table_priv(
            SESSION_LOCK_LOGIN, TENANT, "portal_sessions", "SELECT"
        ),
        "lock_login_select_amh_portal_memberships": table_priv(
            SESSION_LOCK_LOGIN, TENANT, "portal_memberships", "SELECT"
        ),
        "admin_set_native_owner": f"pg_has_role(current_user,'{OWNER_LOGIN}','SET')",
        **{
            f"witness_{priv.lower()}_{t}": table_priv(WITNESS_LOGIN, NATIVE_SCHEMA, t, priv)
            for t in PORTAL_PINNED
            for priv in ("INSERT", "UPDATE", "DELETE")
        },
    }
    negatives = {name: bool(await conn.fetchval(f"SELECT {expr}")) for name, expr in checks.items()}
    positives = {
        f"witness_select_{t}": bool(
            await conn.fetchval(f"SELECT {table_priv(WITNESS_LOGIN, NATIVE_SCHEMA, t, 'SELECT')}")
        )
        for t in PORTAL_PINNED
    }
    missing = [t for t in REQUIRED_RELATIONS if t not in relations]
    ok = (
        not missing
        and function is not None
        and schema is not None
        and schema["owner"] == OWNER_LOGIN
        and all(positives.values())
        # O admin de um PG local e superusuario (SET em tudo); no Aurora tem de dar f.
        and not any(v for k, v in negatives.items() if k != "admin_set_native_owner")
    )
    return {
        "schema": PINS_SCHEMA,
        "ok": ok,
        "native_schema": None if schema is None else {"name": schema["nspname"], "owner": schema["owner"]},
        "function_pin": None
        if function is None
        else {
            "oid": str(function["oid"]),
            "owner": function["owner"],
            "definition_sha256": function["definition_sha256"],
        },
        "native_relation_pins": relations,
        "staff_owned": {t: relations.get(t) for t in STAFF_OWNED},
        "portal_pinned": {t: relations.get(t) for t in PORTAL_PINNED},
        "missing_relations": missing,
        "negatives_must_be_false": negatives,
        "positives_must_be_true": positives,
    }


# --------------------------------------------------------------------------- entrada


def tls_context(cafile: str | None) -> ssl.SSLContext:
    """Verificacao completa (cadeia + hostname). Sem `cafile`, o trust store do sistema, onde a imagem
    do app ja instalou as raizes regionais pinadas do RDS (`deploy/certificates`)."""
    context = ssl.create_default_context(cafile=cafile)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise InstallError(f"variavel {name} ausente")
    return value


def main(
    argv: list[str] | None = None, *, secrets_client: Any = None, connector: Connector | None = None
) -> int:
    del argv
    try:
        host, port, database = _env("DB_HOST"), int(_env("DB_PORT")), _env("DB_NAME")
        admin_arn = _env("STAFF_INSTALL_ADMIN_SECRET_ARN")
        login_arns = parse_login_arns(_env("STAFF_INSTALL_LOGIN_SECRET_ARNS"))
        if secrets_client is None:
            import boto3  # type: ignore[import-untyped]

            secrets_client = boto3.client(
                "secretsmanager", region_name=os.environ.get("AWS_REGION", "sa-east-1")
            )
        credentials = fetch_credentials(secrets_client, admin_arn, login_arns)
        if connector is None:
            import asyncpg  # type: ignore[import-untyped]

            context = tls_context(os.environ.get("STAFF_INSTALL_CA_FILE") or None)

            async def connector(user: str, password: str) -> Connection:
                connection: Connection = await asyncpg.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    ssl=context,
                    timeout=15,
                )
                return connection

        pins = asyncio.run(install(connector, credentials))
        if not all(pins["session_tls"].values()):
            pins["ok"] = False
    except InstallError as failure:
        print(f"recusado: {failure}", file=sys.stderr)
        return 1
    except Exception as failure:  # noqa: BLE001 - so o tipo e a mensagem do servidor, nunca o SQL
        message = getattr(failure, "message", None) if hasattr(failure, "sqlstate") else None
        print(f"falhou: {type(failure).__name__}{': ' + message if message else ''}", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(pins, indent=2, sort_keys=True) + "\n")
    return 0 if pins["ok"] else 2
