"""Instalador in-VPC da Onda 3 (B5), com Secrets Manager e PostgreSQL falsos.

O que se prova aqui: a ORDEM (a mesma do harness `deploy/c1-local/c1/db_native.py`, com os ajustes
do Aurora), quem executa cada script, que senha/verificador nunca aparecem na saida e que o
formato dos segredos e conferido. A execucao contra um PostgreSQL real fica em
`tests/integration/staff_install/`.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from tools.staff_install import installer
from tools.staff_install.installer import (
    ALL_LOGINS,
    NATIVE_LOGINS,
    OWNER_LOGIN,
    PORTAL_PINNED,
    REQUIRED_RELATIONS,
    SQL_DIR,
    STAFF_OWNED,
    Credentials,
    InstallError,
)

ADMIN = "amh_admin"
PASSWORDS = {login: f"senha-{login}-" + "x" * 20 for login in ALL_LOGINS}
ADMIN_PASSWORD = "senha-mestre-" + "y" * 20
ARNS = {
    login: f"arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/{login}-AbCdEf"
    for login in ALL_LOGINS
}
ADMIN_ARN = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:rds!cluster-1-AbCdEf"


class FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.calls: list[str] = []

    def get_secret_value(self, SecretId: str) -> dict[str, str]:  # noqa: N803 - assinatura do boto3
        self.calls.append(SecretId)
        return {"SecretString": self.values[SecretId]}


def secrets_client(**overrides: str) -> FakeSecrets:
    values = {ADMIN_ARN: json.dumps({"username": ADMIN, "password": ADMIN_PASSWORD})}
    for login, arn in ARNS.items():
        values[arn] = json.dumps({"username": login, "password": PASSWORDS[login]})
    # As duas do portal chegam como a DSN do `generate`, sem tratamento.
    for login in ("portal_staff_lock_amh", "portal_staff_witness_amh"):
        values[ARNS[login]] = f"postgresql+asyncpg://{login}:{PASSWORDS[login]}@db.internal:5432/maezo"
    values.update(overrides)
    return FakeSecrets(values)


class FakeConnection:
    def __init__(self, db: FakeDb, user: str) -> None:
        self.db, self.user = db, user

    def _log(self, query: str, args: tuple[Any, ...]) -> None:
        self.db.log.append((self.user, query, args))

    async def execute(self, query: str, *args: Any) -> str:
        self._log(query, args)
        return "OK"

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._log(query, args)
        if "pg_stat_ssl" in query:
            return self.db.tls
        if "to_regclass('maezo_external" in query:
            return self.db.external_installed
        if "to_regnamespace('portal_identity')" in query:
            return self.db.lock_installed
        if query.startswith("SELECT has_table_privilege('portal_staff_witness_amh'") and query.endswith(
            "'SELECT')"
        ):
            return True
        if query.startswith("SELECT has_") or query.startswith("SELECT pg_has_role"):
            return self.db.negative
        raise AssertionError(query)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self._log(query, args)
        if "FROM pg_roles" in query:
            return [{"rolname": OWNER_LOGIN}]
        if "FROM pg_class" in query:
            return [
                {"relname": t, "oid": 16000 + i, "owner": OWNER_LOGIN}
                for i, t in enumerate(self.db.relations)
            ]
        raise AssertionError(query)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any]:
        self._log(query, args)
        if "FROM pg_proc" in query:
            return {"oid": 17001, "owner": "portal_external_identity_reader", "definition_sha256": "ab" * 32}
        if "FROM pg_namespace" in query:
            return {"nspname": "maezo_native", "owner": OWNER_LOGIN}
        raise AssertionError(query)

    @contextlib.asynccontextmanager
    async def _tx(self) -> Any:
        self._log("BEGIN", ())
        yield
        self._log("COMMIT", ())

    def transaction(self) -> Any:
        return self._tx()

    async def close(self) -> None:
        self._log("CLOSE", ())


class FakeDb:
    def __init__(self) -> None:
        self.log: list[tuple[str, str, tuple[Any, ...]]] = []
        self.connects: list[tuple[str, str]] = []
        self.tls = True
        self.external_installed = False
        self.lock_installed = False
        self.negative = False
        self.relations = list(REQUIRED_RELATIONS)

    async def connect(self, user: str, password: str) -> FakeConnection:
        self.connects.append((user, password))
        return FakeConnection(self, user)

    def sql(self) -> Iterator[tuple[str, str]]:
        return ((u, q) for u, q, _ in self.log)

    def index(self, user: str, needle: str) -> int:
        for i, (u, q, _) in enumerate(self.log):
            if u == user and needle in q:
                return i
        raise AssertionError(f"{user}: {needle!r} nao executado")


def credentials() -> Credentials:
    return Credentials(ADMIN, ADMIN_PASSWORD, dict(PASSWORDS))


def run(db: FakeDb) -> dict[str, Any]:
    import asyncio

    return asyncio.run(installer.install(db.connect, credentials()))


# ----------------------------------------------------------------------- segredos


@pytest.mark.parametrize(
    "secret",
    [
        json.dumps({"username": OWNER_LOGIN, "password": PASSWORDS[OWNER_LOGIN]}),
        f"postgresql://{OWNER_LOGIN}:{PASSWORDS[OWNER_LOGIN]}@h:5432/maezo",
        f"postgresql+asyncpg://{OWNER_LOGIN}:{PASSWORDS[OWNER_LOGIN]}@h:5432/maezo?ssl=verify-full\n",
    ],
)
def test_credential_accepts_json_and_dsn(secret: str) -> None:
    assert installer.parse_credential(secret, OWNER_LOGIN) == PASSWORDS[OWNER_LOGIN]


def test_credential_percent_decodes_the_dsn_password() -> None:
    assert (
        installer.parse_credential(f"postgresql://{OWNER_LOGIN}:a%2Fb%40c{'z' * 20}@h/m", OWNER_LOGIN)
        == "a/b@c" + "z" * 20
    )


@pytest.mark.parametrize(
    ("secret", "reason"),
    [
        (json.dumps({"username": "maezo_app", "password": "p" * 30}), "username"),
        (json.dumps({"username": OWNER_LOGIN, "password": "curta"}), "curta"),
        (json.dumps({"username": OWNER_LOGIN, "password": "p" * 30, "extra": 1}), "esperado"),
        ("{nao json p" + "p" * 30, "JSON"),
        ("p" * 40, "formato"),
    ],
)
def test_credential_refusals_never_echo_the_secret(secret: str, reason: str) -> None:
    with pytest.raises(InstallError, match=reason) as refused:
        installer.parse_credential(secret, OWNER_LOGIN)
    assert "p" * 30 not in str(refused.value) and "curta" not in str(refused.value).replace(
        "curta demais", ""
    )


def test_login_arns_must_be_exactly_the_six_logins() -> None:
    assert installer.parse_login_arns(json.dumps(ARNS)) == ARNS
    with pytest.raises(InstallError, match="exatamente"):
        installer.parse_login_arns(json.dumps({k: v for k, v in ARNS.items() if k != OWNER_LOGIN}))
    with pytest.raises(InstallError, match="ARN"):
        installer.parse_login_arns(json.dumps({**ARNS, OWNER_LOGIN: "segredo-em-claro"}))


def test_fetch_credentials_reads_each_arn_once_and_refuses_reused_passwords() -> None:
    client = secrets_client()
    got = installer.fetch_credentials(client, ADMIN_ARN, ARNS)
    assert got.admin_user == ADMIN and dict(got.passwords) == PASSWORDS
    assert sorted(client.calls) == sorted([ADMIN_ARN, *ARNS.values()])
    reused = json.dumps({"username": "maezo_native_case_issuer", "password": PASSWORDS[OWNER_LOGIN]})
    with pytest.raises(InstallError, match="mesma senha"):
        installer.fetch_credentials(
            secrets_client(**{ARNS["maezo_native_case_issuer"]: reused}), ADMIN_ARN, ARNS
        )


# ----------------------------------------------------------------------- SQL


def test_roles_script_splits_into_its_three_blocks_byte_for_byte() -> None:
    text = (SQL_DIR / "engine-native-roles.sql").read_text(encoding="utf-8")
    blocks = installer.split_roles_sql(text)
    assert list(blocks) == ["roles", "external", "schemas"]
    assert "".join(blocks.values()) == text
    assert (
        "CREATE SCHEMA" in blocks["schemas"] and "CREATE SCHEMA" not in blocks["roles"] + blocks["external"]
    )
    with pytest.raises(InstallError, match="blocos"):
        installer.split_roles_sql(text + "\nDO $novo$ BEGIN END $novo$;\n")


def test_lock_patch_wraps_the_single_owner_change_and_undoes_it() -> None:
    from tools.staff_materials.lock_sql import render

    text = render(
        tenant="amh",
        tenant_schema="amh",
        session_lock_login="portal_staff_lock_amh",
        witness_login="portal_staff_witness_amh",
    )
    patched = installer.patch_lock_sql(text)
    order = [
        "GRANT portal_external_identity_reader TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;",
        "GRANT CREATE ON SCHEMA portal_identity TO portal_external_identity_reader;",
        installer.LOCK_ALTER_LINE,
        "REVOKE CREATE ON SCHEMA portal_identity FROM portal_external_identity_reader;",
        "REVOKE portal_external_identity_reader FROM CURRENT_USER;",
    ]
    positions = [patched.index(line) for line in order]
    assert positions == sorted(positions)
    # Fora da injecao, o template renderizado fica identico.
    for line in order[:2] + order[3:]:
        patched = patched.replace(line + "\n", "", 1).replace("\n" + line, "", 1)
    assert patched == text
    with pytest.raises(InstallError, match="ancora"):
        installer.patch_lock_sql(text.replace(installer.LOCK_ALTER_LINE, ""))


def test_definer_roles_come_from_the_owners_script() -> None:
    roles = installer.definer_roles((SQL_DIR / "external-case-owners.sql").read_text(encoding="utf-8"))
    assert roles == sorted(
        {
            "portal_external_source_definer",
            "portal_external_checkpoint_definer",
            "portal_external_ingress_reader",
            "portal_external_publisher_definer",
        }
    )


# ----------------------------------------------------------------------- instalacao


def test_full_sequence_order_and_who_runs_each_script() -> None:
    db = FakeDb()
    pins = run(db)
    assert [u for u, _ in db.connects] == [ADMIN, OWNER_LOGIN, ADMIN, OWNER_LOGIN, ADMIN]
    roles = (SQL_DIR / "engine-native-roles.sql").read_text(encoding="utf-8")
    blocks = installer.split_roles_sql(roles)
    steps = [
        db.index(ADMIN, "SELECT set_config"),
        db.index(ADMIN, blocks["roles"]),
        db.index(ADMIN, blocks["external"]),
        db.index(ADMIN, f"GRANT {OWNER_LOGIN} TO CURRENT_USER WITH INHERIT TRUE, SET TRUE"),
        db.index(ADMIN, blocks["schemas"]),
        db.index(OWNER_LOGIN, "GERADO por deploy/sql/build_engine_native_install.py"),
        db.index(OWNER_LOGIN, "mzo_human_tenant"),
        db.index(OWNER_LOGIN, "CREATE TABLE maezo_external.mzo_external_source_head"),
        db.index(ADMIN, "ALTER FUNCTION maezo_external.lock_ingress(jsonb) OWNER TO"),
        db.index(ADMIN, "SET LOCAL ROLE maezo_app"),
        db.index(ADMIN, "GRANT SELECT (tenant, issuer, subject, payload) ON amh.portal_memberships"),
        db.index(ADMIN, "CREATE SCHEMA portal_identity;"),
        db.index(OWNER_LOGIN, "GRANT USAGE ON SCHEMA maezo_native TO portal_staff_witness_amh"),
        db.index(
            OWNER_LOGIN,
            "GRANT SELECT ON maezo_native.mzo_portal_read_membership, maezo_native.mzo_human_principal",
        ),
    ]
    assert steps == sorted(steps)
    # O REVOKE final vem depois do D4 e antes do roles.sql COMPLETO de novo (a prova de postura).
    revokes = [
        i
        for i, (u, q, _) in enumerate(db.log)
        if u == ADMIN and q == f"REVOKE {OWNER_LOGIN} FROM CURRENT_USER"
    ]
    rechecks = [i for i, (u, q, _) in enumerate(db.log) if u == ADMIN and q == blocks["roles"]]
    externals = [i for i, (u, q, _) in enumerate(db.log) if u == ADMIN and q == blocks["external"]]
    assert (
        len(rechecks) == 2
        and revokes[-1] > steps[-1]
        and rechecks[1] > revokes[-1]
        and externals[1] > rechecks[1]
    )
    # O `$schemas$` so roda uma vez, com o admin ainda membro do dono.
    assert [q for u, q, _ in db.log].count(blocks["schemas"]) == 1
    assert pins["ok"] and pins["steps"]["lock_sql"] == "ok" and pins["steps"]["external_ddl"] == "ok"


def test_owners_script_runs_inside_one_transaction_with_temporary_set_and_create() -> None:
    db = FakeDb()
    run(db)
    begin = (
        db.index(
            ADMIN, "GRANT portal_external_checkpoint_definer TO CURRENT_USER WITH INHERIT FALSE, SET TRUE"
        )
        - 1
    )
    assert db.log[begin][1] == "BEGIN"
    body = [q for _, q, _ in db.log[begin:]]
    end = body.index("COMMIT")
    body = body[:end]
    owners = next(q for q in body if "ALTER FUNCTION" in q)
    before, after = body[: body.index(owners)], body[body.index(owners) + 1 :]
    for role in installer.definer_roles(owners):
        assert f"GRANT {role} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE" in before
        assert f"GRANT CREATE ON SCHEMA maezo_external TO {role}" in before
        assert f"REVOKE CREATE ON SCHEMA maezo_external FROM {role}" in after
        assert f"REVOKE {role} FROM CURRENT_USER" in after


def test_verifiers_not_passwords_reach_the_database() -> None:
    db = FakeDb()
    run(db)
    guc = [(args[0], args[1], args[2]) for u, q, args in db.log if q.startswith("SELECT set_config")]
    names = [name for name, _, _ in guc]
    # 4 nativos no passo 1, 2 do portal (LOCAIS a transacao do lock), 4 nativos no recheck.
    assert names == [
        f"maezo.verifier.{x}"
        for x in (*NATIVE_LOGINS, "portal_staff_lock_amh", "portal_staff_witness_amh", *NATIVE_LOGINS)
    ]
    assert [local for _, _, local in guc] == [False] * 4 + [True] * 2 + [False] * 4
    for _, value, _ in guc:
        assert re.fullmatch(r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+", value)
    everything = repr(db.log)
    for password in (*PASSWORDS.values(), ADMIN_PASSWORD):
        assert password not in everything
    # A senha so e usada para abrir a sessao do proprio login.
    assert set(db.connects) == {(ADMIN, ADMIN_PASSWORD), (OWNER_LOGIN, PASSWORDS[OWNER_LOGIN])}


def test_rerun_skips_the_non_idempotent_ddl() -> None:
    db = FakeDb()
    db.external_installed = db.lock_installed = True
    pins = run(db)
    assert pins["steps"]["external_ddl"] == "ja instalado" and pins["steps"]["lock_sql"] == "ja instalado"
    assert not any("CREATE SCHEMA portal_identity" in q for _, q in db.sql())
    assert not any("CREATE TABLE maezo_external.mzo_external_source_head" in q for _, q in db.sql())
    # Sobra de execucao interrompida e limpa antes do roles.sql (que a recusaria).
    first_roles = db.index(ADMIN, "DO $roles$")
    assert db.index(ADMIN, f"REVOKE {OWNER_LOGIN} FROM CURRENT_USER") < first_roles


def test_pins_carry_the_staff_owned_tables_and_fail_on_missing_or_a_positive_negative() -> None:
    pins = run(FakeDb())
    assert pins["schema"] == "maezo-staff-install-pins.v1"
    assert set(pins["staff_owned"]) == set(STAFF_OWNED) and len(STAFF_OWNED) == 14
    assert all(v and v["owner"] == OWNER_LOGIN and v["oid"].isdigit() for v in pins["staff_owned"].values())
    assert set(pins["portal_pinned"]) == set(PORTAL_PINNED)
    assert pins["native_schema"] == {"name": "maezo_native", "owner": OWNER_LOGIN}
    assert pins["function_pin"]["owner"] == "portal_external_identity_reader"
    assert not any(pins["negatives_must_be_false"].values())
    db = FakeDb()
    db.relations.remove("mzo_staff_case_grant")
    missing = run(db)
    assert not missing["ok"] and missing["missing_relations"] == ["mzo_staff_case_grant"]
    db = FakeDb()
    db.negative = True
    assert not run(db)["ok"]


# ----------------------------------------------------------------------- main


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_HOST", "aurora.cluster.sa-east-1.rds.amazonaws.com")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "maezo")
    monkeypatch.setenv("STAFF_INSTALL_ADMIN_SECRET_ARN", ADMIN_ARN)
    monkeypatch.setenv("STAFF_INSTALL_LOGIN_SECRET_ARNS", json.dumps(ARNS))


@pytest.mark.usefixtures("env")
def test_main_prints_only_public_json(capsys: pytest.CaptureFixture[str]) -> None:
    db = FakeDb()
    assert installer.main(secrets_client=secrets_client(), connector=db.connect) == 0
    captured = capsys.readouterr()
    pins = json.loads(captured.out)
    assert pins["ok"] and pins["session_tls"] == {"admin": True, "owner": True}
    assert captured.err == ""
    for secret in (*PASSWORDS.values(), ADMIN_PASSWORD, "SCRAM-SHA-256$"):
        assert secret not in captured.out


@pytest.mark.usefixtures("env")
def test_main_without_tls_is_not_ok(capsys: pytest.CaptureFixture[str]) -> None:
    db = FakeDb()
    db.tls = False
    assert installer.main(secrets_client=secrets_client(), connector=db.connect) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.usefixtures("env")
def test_main_refusal_goes_to_stderr_without_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    wrong = json.dumps({"username": "maezo_app", "password": PASSWORDS[OWNER_LOGIN]})
    assert (
        installer.main(
            secrets_client=secrets_client(**{ARNS[OWNER_LOGIN]: wrong}), connector=FakeDb().connect
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == "" and "recusado" in captured.err and PASSWORDS[OWNER_LOGIN] not in captured.err


@pytest.mark.usefixtures("env")
def test_main_database_failure_reports_only_type_and_server_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BoomError(Exception):
        sqlstate = "42501"
        message = "permission denied for schema maezo_native"

    async def connect(user: str, password: str) -> Any:
        raise BoomError(f"query com {password}")

    assert installer.main(secrets_client=secrets_client(), connector=connect) == 1
    err = capsys.readouterr().err
    assert "BoomError: permission denied" in err and ADMIN_PASSWORD not in err


def test_main_requires_env(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("DB_HOST", raising=False)
    assert installer.main(secrets_client=secrets_client(), connector=FakeDb().connect) == 1
    assert "DB_HOST" in capsys.readouterr().err


def test_tls_context_verifies_chain_and_hostname() -> None:
    import ssl

    context = installer.tls_context(None)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


# ----------------------------------------------------------------------- Onda 8 (passo 9)


class _ExtraDb:
    def __init__(self, roles: set[str], settings: dict[str, str], good: dict[str, str]) -> None:
        self.roles, self.settings, self.good = roles, settings, good
        self.statements: list[str] = []

    async def connect(self, user: str, password: str) -> Any:
        if user != ADMIN and self.good.get(user) != password:
            raise RuntimeError("password authentication failed")
        db = self

        class _C:
            async def fetchval(self, query: str, *args: Any) -> Any:
                if "FROM pg_roles" in query:
                    return 1 if args[0] in db.roles else None
                if "pg_db_role_setting" in query:
                    return db.settings.get(args[0])
                raise AssertionError(query)

            async def execute(self, query: str, *args: Any) -> str:
                db.statements.append(query)
                return "OK"

            async def close(self) -> None:
                return None

        return _C()


def test_extra_login_arns_allowlist() -> None:
    assert installer.parse_extra_login_arns(None) == {}
    arn = "arn:aws:secretsmanager:sa-east-1:1:secret:x"
    assert installer.parse_extra_login_arns(json.dumps({"portal_human_outbox_amh": arn}))
    for bad in (json.dumps({"postgres": arn}), json.dumps({"portal_human_outbox_amh": "x"}), "{", "{}"):
        with pytest.raises(installer.InstallError):
            installer.parse_extra_login_arns(bad)


def test_extra_logins_create_then_idempotent() -> None:
    import asyncio

    passwords = {"portal_task_source_amh": "a" * 32, "portal_human_outbox_amh": "b" * 32}
    db = _ExtraDb(set(), {}, {})
    first = asyncio.run(installer.ensure_extra_logins(db.connect, credentials(), passwords))
    assert first["portal_task_source_amh"] == "criado"
    assert first["portal_human_outbox_amh"] == "criado; search_path=amh aplicado"
    creates = [s for s in db.statements if s.startswith("CREATE ROLE")]
    assert len(creates) == 2 and all("SCRAM-SHA-256$" in s and "NOBYPASSRLS" in s for s in creates)
    assert not any(p in "".join(db.statements) for p in passwords.values())
    # Segunda execucao: senha ja autentica e search_path igual -> nenhum SQL de escrita.
    again = _ExtraDb(set(passwords), {"portal_human_outbox_amh": "search_path=amh"}, passwords)
    second = asyncio.run(installer.ensure_extra_logins(again.connect, credentials(), passwords))
    assert again.statements == []
    assert second == {
        "portal_human_outbox_amh": "igual; search_path=amh igual",
        "portal_task_source_amh": "igual",
    }
    # Senha divergente: so realinha a senha (sem re-declarar atributos, que o RDS recusa).
    drift = _ExtraDb(set(passwords), {"portal_human_outbox_amh": "search_path=amh"}, {})
    asyncio.run(installer.ensure_extra_logins(drift.connect, credentials(), passwords))
    assert len(drift.statements) == 2
    assert all("LOGIN PASSWORD" in s and "BYPASSRLS" not in s for s in drift.statements)


def test_extra_logins_refuse_outside_allowlist() -> None:
    import asyncio

    with pytest.raises(installer.InstallError):
        asyncio.run(
            installer.ensure_extra_logins(_ExtraDb(set(), {}, {}).connect, credentials(), {"postgres": "x"})
        )
