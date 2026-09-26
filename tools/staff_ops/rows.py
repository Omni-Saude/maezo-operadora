"""`rows`: designacao instalada + admissao Q2 no schema nativo, como o DONO do schema.

Entradas (nenhuma por env/override alem de ARNs e do endereco do banco):
* `STAFF_ROWS_SECRET_ARN` -> `staff-native-rows.v1`: designacao, prova de instalacao, registro
  `portal-read-admission.v1` e a assinatura Ed25519 (64 bytes), todos em base64, mais os digests
  esperados. O conteudo e publico (assinado), mas a fonte unica e o segredo pinado, para que o
  que se instala seja exatamente o que o aprovador assinou;
* `STAFF_OWNER_SECRET_ARN` -> credencial de `maezo_native_schema_owner`;
* `STAFF_IDENTITY_SECRET_ARN` -> credencial do PROPRIO `identity_login`, SO para
  `ALTER ROLE CURRENT_USER SET search_path` (o job T1.5 le `portal_memberships` sem qualificar o
  schema). A credencial mestre do Aurora NAO entra nesta task: os logins das Ondas 8
  (`task_source`, `human_plane`, `assignment.admin`) sao criados pela task `staff-install`; aqui
  so se confere que existem e se aplicam os grants pelos donos dos schemas.

Bootstrap que o engine nativo exige ANTES do 1o boot (`HumanCommandPlugin.staffCurrent`, medido no
apply de 25/09) e que o DDL deixa explicito por comentario: `MZO_HUMAN_TENANT(<tenant>,0)` e a linha
`MZO_AUTH_INSTALLATION` do tenant (escopo AUTH da composicao, binding medido no proprio banco e uma
qualificacao `not-qualified` que a fixture SYN substitui pela definicao deployada — o mesmo D8 do
C1). Ambas so se ausentes; presente com outro escopo = recusa.

Antes de escrever, confere por conta propria: SHA-256 da designacao, que a prova assina ESSE digest
com a raiz instalada (verificador do portal), e a assinatura da admissao no dominio
`maezo/portal-read-admission/v1\\0`. Idempotente: linha igual = nada muda; linha diferente na mesma
chave = recusa (nunca DELETE/UPDATE). Depois reler e comparar byte a byte (prova).

Rotacao da designacao (Onda 8): um segredo com a revisao N+1 (rascunho de
`python -m tools.staff_materials next-designation`, assinado com `sign-designation`) insere o evento
novo e anda o ponteiro `mzo_staff_case_designation_current` na MESMA transacao, sob a trava do
trigger; o evento N fica no historico. Revisao menor, igual com outro digest, ou que nao declara a
corrente como `expected_previous_revision` = recusa; reaplicar a N+1 = `igual`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import ssl
import sys
from typing import Any

from .common import OpsError, b64, env, secret_json, secrets_client

SCHEMA = "staff-native-rows.v1"
OWNER = "maezo_native_schema_owner"
NATIVE_SCHEMA = "maezo_native"
ADMISSION_DOMAIN = b"maezo/portal-read-admission/v1\x00"
_KEYS = {
    "schema",
    "scope",
    "designation_revision",
    "designation_b64",
    "designation_sha256",
    "installation_proof_b64",
    "root_public_key_b64",
    "admission_ref",
    "admission_revision",
    "admission_record_b64",
    "admission_signature_b64",
    "admission_sha256",
    "identity_login",
    "identity_search_path",
    "auth",
}
#: Opcional (D14, Onda 8): o login da fonte de tarefas do job T1.5 e os grants de
#: `deploy/sql/portal-task-source-grants.sql`, cada parte pelo dono do schema dela.
_OPTIONAL = {"task_source", "human_plane", "assignment"}
_AUTH_SCOPE = {
    "tenant",
    "environment",
    "engine_name",
    "database_incarnation",
    "installation_ref",
    "installation_revision",
}
_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def parse(document: dict[str, Any]) -> dict[str, Any]:
    if not _KEYS <= set(document) <= _KEYS | _OPTIONAL or document["schema"] != SCHEMA:
        raise OpsError(f"segredo de linhas: esperado {SCHEMA} com {sorted(_KEYS)}")
    scope = document["scope"]
    if not isinstance(scope, dict) or set(scope) != {
        "tenant",
        "environment",
        "engine_name",
        "database_incarnation",
    }:
        raise OpsError("scope invalido")
    if scope["environment"] != "dev":
        raise OpsError("este instalador so roda em dev")
    for name in ("identity_login", "identity_search_path"):
        if not isinstance(document[name], str) or not _NAME.fullmatch(document[name]):
            raise OpsError(f"{name} invalido")
    auth = document["auth"]
    if not isinstance(auth, dict) or set(auth) != {
        "auth_scope",
        "native_code_digest",
        "runtime_role",
        "valid_days",
    }:
        raise OpsError("auth invalido")
    if not isinstance(auth["auth_scope"], dict) or set(auth["auth_scope"]) != _AUTH_SCOPE:
        raise OpsError("auth_scope invalido")
    if any(auth["auth_scope"][k] != scope[k] for k in scope):
        raise OpsError("auth_scope diverge do escopo da designacao")
    if not re.fullmatch(r"[0-9a-f]{64}", str(auth["native_code_digest"])) or not _NAME.fullmatch(
        str(auth["runtime_role"])
    ):
        raise OpsError("auth: digest ou role invalido")
    if (
        not isinstance(auth["valid_days"], int)
        or isinstance(auth["valid_days"], bool)
        or not 1 <= auth["valid_days"] <= 14
    ):
        raise OpsError("auth.valid_days: 1 a 14")
    task_source = document.get("task_source")
    if task_source is not None and (
        not isinstance(task_source, dict)
        or set(task_source) != {"login", "password_secret_arn", "engine_schema"}
        or not _NAME.fullmatch(str(task_source["login"]))
        or not _NAME.fullmatch(str(task_source["engine_schema"]))
        or not str(task_source["password_secret_arn"]).startswith("arn:aws:secretsmanager:")
    ):
        raise OpsError("task_source invalido")
    plane = document.get("human_plane")
    if plane is not None and (
        not isinstance(plane, dict)
        or set(plane) != {"schema", "outbox", "source"}
        or not _NAME.fullmatch(str(plane["schema"]))
        or any(
            not isinstance(plane[k], dict)
            or set(plane[k]) != {"login", "password_secret_arn"}
            or not _NAME.fullmatch(str(plane[k]["login"]))
            for k in ("outbox", "source")
        )
        or plane["outbox"]["login"] == plane["source"]["login"]
    ):
        raise OpsError("human_plane invalido")
    assignment = document.get("assignment")
    if assignment is not None:
        from .assignment import parse_installation

        if (
            not isinstance(assignment, dict)
            or set(assignment) != {"schema", "installation", "admin"}
            or not _NAME.fullmatch(str(assignment["schema"]))
            or not isinstance(assignment["admin"], dict)
            or set(assignment["admin"]) != {"login", "password_secret_arn"}
            or not _NAME.fullmatch(str(assignment["admin"]["login"]))
            or not str(assignment["admin"]["password_secret_arn"]).startswith("arn:aws:secretsmanager:")
            or not isinstance(assignment["installation"], dict)
        ):
            raise OpsError("assignment invalido")
        installation = parse_installation(
            assignment["installation"], tenant=scope["tenant"], runtime_role=str(auth["runtime_role"])
        )
        if (installation.environment, installation.engine_name, installation.database_incarnation) != (
            scope["environment"],
            scope["engine_name"],
            scope["database_incarnation"],
        ):
            raise OpsError("assignment.installation diverge do escopo da designacao")
    rows = dict(document)
    rows["designation"] = b64(document["designation_b64"], "designation")
    rows["proof"] = b64(document["installation_proof_b64"], "installation_proof")
    rows["root"] = b64(document["root_public_key_b64"], "root_public_key")
    rows["record"] = b64(document["admission_record_b64"], "admission_record")
    rows["signature"] = b64(document["admission_signature_b64"], "admission_signature")
    for name in ("designation_revision", "admission_revision"):
        if not isinstance(document[name], int) or isinstance(document[name], bool) or document[name] < 1:
            raise OpsError(f"{name} invalido")
    return rows


def verify_designation(rows: dict[str, Any]) -> Any:
    """Designacao + prova de instalacao contra a raiz, com os verificadores do runtime; devolve a raiz."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from maezo.gateway.external_cases.models import now_utc, parse
    from maezo.gateway.staff_cases.authority import InstalledStaffAuthority
    from maezo.gateway.staff_cases.models import Designation, Proof

    if hashlib.sha256(rows["designation"]).hexdigest() != rows["designation_sha256"]:
        raise OpsError("designacao nao e a do digest aprovado")
    root = serialization.load_der_public_key(rows["root"])
    if not isinstance(root, Ed25519PublicKey):
        raise OpsError("raiz nao e Ed25519")
    try:
        designation = parse(Designation, rows["designation"])
    except Exception:
        raise OpsError("designacao nao passa no modelo do runtime") from None
    if str(designation.designation_revision) != str(rows["designation_revision"]):
        raise OpsError("designation_revision diverge da designacao assinada")
    try:
        InstalledStaffAuthority.verify(
            designation_bytes=rows["designation"],
            installation_proof=parse(Proof, rows["proof"]),
            expected_digest=rows["designation_sha256"],
            expected_scope=designation.scope,
            root=root,
            revoked_fingerprints=frozenset(),
            now=now_utc(),
        )
    except Exception:
        raise OpsError("prova de instalacao nao verifica contra a raiz") from None
    return root


def verify(rows: dict[str, Any]) -> None:
    """Confere o que vai ser instalado com os MESMOS verificadores do runtime."""
    if hashlib.sha256(rows["record"]).hexdigest() != rows["admission_sha256"]:
        raise OpsError("admissao nao e a do digest aprovado")
    root = verify_designation(rows)
    if len(rows["signature"]) != 64:
        raise OpsError("assinatura da admissao precisa de 64 bytes")
    try:
        root.verify(rows["signature"], ADMISSION_DOMAIN + rows["record"])
    except Exception:
        raise OpsError("assinatura da admissao nao verifica contra a raiz") from None
    record = json.loads(rows["record"])
    if record.get("admission_ref") != rows["admission_ref"] or record.get("admission_revision") != str(
        rows["admission_revision"]
    ):
        raise OpsError("admission_ref/revision divergem do registro assinado")


def _canonical(value: dict[str, Any]) -> str:
    from maezo.portal.engine.profile import canonicalize

    return canonicalize(value).decode()


async def bootstrap(owner: Any, rows: dict[str, Any]) -> dict[str, str]:
    """`MZO_HUMAN_TENANT` e `MZO_AUTH_INSTALLATION` do tenant, so se ausentes."""
    from datetime import UTC, datetime, timedelta

    auth, tenant = rows["auth"], rows["scope"]["tenant"]
    scope = auth["auth_scope"]
    actions: dict[str, str] = {}
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {NATIVE_SCHEMA}")
        inserted = await owner.execute(
            "INSERT INTO mzo_human_tenant(tenant_,rev_) VALUES($1,0) ON CONFLICT DO NOTHING", tenant
        )
        actions["human_tenant"] = "inserida" if inserted.endswith(" 1") else "igual"
        row = await owner.fetchrow(
            "SELECT scope_, qualification_ FROM mzo_auth_installation WHERE tenant_=$1 FOR UPDATE", tenant
        )
        if row is not None:
            if json.loads(row["scope_"]) != scope:
                raise OpsError("mzo_auth_installation com outro escopo: recusado")
            # Imagem nova do engine = codigo novo: a qualificacao AUTH pina o SHA-256 do JAR nativo
            # carregado (`AuthRuntime.java:49`). O dono re-qualifica SO esse campo (a definicao e
            # a validade ficam; quem as renova e a fixture/instalacao AUTH).
            qualification = json.loads(row["qualification_"])
            if qualification.get("native_code_digest") != auth["native_code_digest"]:
                qualification["native_code_digest"] = auth["native_code_digest"]
                await owner.execute(
                    "UPDATE mzo_auth_installation SET qualification_=$2 WHERE tenant_=$1",
                    tenant,
                    _canonical(qualification),
                )
                actions["auth_installation"] = "requalificada (codigo nativo)"
            else:
                actions["auth_installation"] = "igual"
            return actions
        ids = await owner.fetchrow(
            "SELECT d.oid::bigint AS db, n.oid::bigint AS ns, current_database() AS name FROM pg_database d, "
            "pg_namespace n WHERE d.datname=current_database() AND n.nspname=$1",
            NATIVE_SCHEMA,
        )
        binding = dict(
            schema="human-auth-native-database.v1",
            database_name=ids["name"],
            database_oid=str(ids["db"]),
            schema_name=NATIVE_SCHEMA,
            schema_oid=str(ids["ns"]),
            owner_role=OWNER,
            runtime_role=auth["runtime_role"],
        )
        tag = b"dev not-qualified"
        until = datetime.now(UTC) + timedelta(days=auth["valid_days"])
        qualification = dict(
            schema="human-auth-installation-qualification.v1",
            definition=dict(
                process_key="SP-OP-AUTH-001",
                definition_id="dev-auth-definition-not-qualified",
                definition_digest=hashlib.sha256(tag).hexdigest(),
                deployment_id="dev-not-deployed",
                input_profile="portal-auth-intake.v1",
                profile_digest=hashlib.sha256(tag + b" profile").hexdigest(),
            ),
            native_code_digest=auth["native_code_digest"],
            source_freeze_contract_digest=hashlib.sha256(tag + b" freeze").hexdigest(),
            cutover_ref="SYN-dev-cutover",
            review_receipt_ref="SYN-dev-review",
            runtime_qualification_ref="SYN-dev-runtime",
            valid_until=until.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
        await owner.execute(
            "INSERT INTO mzo_auth_installation(tenant_,incarnation_,rev_,scope_,binding_,qualification_) "
            "VALUES($1,$2,$3,$4,$5,$6)",
            tenant,
            scope["database_incarnation"],
            int(scope["installation_revision"]),
            _canonical(scope),
            _canonical(binding),
            _canonical(qualification),
        )
        actions["auth_installation"] = "inserida"
    return actions


GRANTS_SQL = "deploy/sql/portal-task-source-grants.sql"


async def require_login(connection: Any, login: str) -> None:
    """Fail-closed: o login e criado pela task `staff-install` (credencial mestre), nunca aqui."""
    if not await connection.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", login):
        raise OpsError(f"login {login} ausente: rode a task staff-install antes do rows")


async def task_source(owner: Any, engine_owner: Any, rows: dict[str, Any]) -> str:
    """D14: o login ja existe (staff-install); aplica as DUAS partes do SQL do repo."""
    from pathlib import Path

    spec = rows["task_source"]
    login, engine_schema = spec["login"], spec["engine_schema"]
    await require_login(owner, login)
    sql = (Path("/app") / GRANTS_SQL).read_text(encoding="utf-8")
    for part, connection in (("native", owner), ("engine", engine_owner)):
        async with connection.transaction():
            await connection.execute("SELECT set_config('maezo.task_source.login', $1, true)", login)
            await connection.execute("SELECT set_config('maezo.task_source.part', $1, true)", part)
            await connection.execute(
                "SELECT set_config('maezo.task_source.engine_schema', $1, true)", engine_schema
            )
            await connection.execute(sql)
    return f"{login} presente; grants native+engine"


PLANE_SQL = "deploy/sql/portal-human-plane-grants.sql"


async def human_plane(schema_owner: Any, rows: dict[str, Any]) -> str:
    """H5: os logins outbox/source (e o search_path do tenant deles) vem da task `staff-install`;
    aqui so os grants do SQL do repo, pelo dono do schema do tenant."""
    from pathlib import Path

    plane = rows["human_plane"]
    done = []
    for key in ("outbox", "source"):
        await require_login(schema_owner, plane[key]["login"])
        done.append(f"{plane[key]['login']} presente")
    sql = (Path("/app") / PLANE_SQL).read_text(encoding="utf-8")
    async with schema_owner.transaction():
        await schema_owner.execute("SELECT set_config('maezo.human_plane.schema', $1, true)", plane["schema"])
        await schema_owner.execute(
            "SELECT set_config('maezo.human_plane.outbox_login', $1, true)", plane["outbox"]["login"]
        )
        await schema_owner.execute(
            "SELECT set_config('maezo.human_plane.source_login', $1, true)", plane["source"]["login"]
        )
        await schema_owner.execute(sql)
    return "; ".join(done) + "; grants do plano humano"


ASSIGNMENT_SQL = "deploy/sql/portal-assignment-admin-grants.sql"


async def assignment_plane(owner: Any, schema_owner: Any, rows: dict[str, Any]) -> str:
    """Onda 8: instalacao nativa do plano de atribuicao (dono nativo) + login da administracao da
    fonte e os grants do SQL do repo (dono do schema do tenant). A fonte em si (a linha
    `portal_assignment_source`) NAO nasce aqui: so o `assignment-activate`, pelo codigo."""
    from pathlib import Path

    from .assignment import install as install_assignment
    from .assignment import parse_installation

    spec = rows["assignment"]
    tenant, login = rows["scope"]["tenant"], spec["admin"]["login"]
    installed = await install_assignment(
        owner,
        parse_installation(spec["installation"], tenant=tenant, runtime_role=rows["auth"]["runtime_role"]),
    )
    # O login da administracao nasce na task `staff-install` (credencial mestre), nunca aqui.
    await require_login(schema_owner, login)
    sql = (Path("/app") / ASSIGNMENT_SQL).read_text(encoding="utf-8")
    async with schema_owner.transaction():
        await schema_owner.execute(
            "SELECT set_config('maezo.assignment_admin.schema', $1, true)", spec["schema"]
        )
        await schema_owner.execute("SELECT set_config('maezo.assignment_admin.login', $1, true)", login)
        await schema_owner.execute(sql)
    return f"instalacao {installed}; {login} presente; grants da administracao"


def previous_revision(designation: bytes) -> int:
    """`expected_previous_revision` da designacao (texto decimal no wire)."""
    try:
        value = json.loads(designation)["expected_previous_revision"]
        return int(value) if isinstance(value, str) and value.isdigit() else -1
    except (ValueError, KeyError, TypeError):
        return -1


async def install_designation(owner: Any, rows: dict[str, Any]) -> dict[str, str]:
    """Designacao: 1a instalacao, igual (nada muda) ou ROTACAO N->N+1, na transacao aberta pelo chamador.

    A rotacao insere o evento da revisao nova (historico imutavel: nunca DELETE nem UPDATE no evento)
    e anda o ponteiro `current` com UPDATE condicionado a revisao lida sob a trava. Revisao menor,
    mesma revisao com outro digest, ou `expected_previous_revision` que nao e a corrente = recusa.
    """
    scope = rows["scope"]
    key = (scope["tenant"], scope["environment"], scope["engine_name"], scope["database_incarnation"])
    revision, digest = rows["designation_revision"], rows["designation_sha256"]
    actions: dict[str, str] = {}
    # A MESMA trava exclusiva que o trigger toma na escrita, tomada ANTES de ler: a leitura da
    # corrente e a troca do ponteiro nao intercalam com outro instalador nem com o leitor.
    await owner.fetchval(
        "SELECT pg_advisory_xact_lock(hashtextextended(CONCAT_WS(chr(31),"
        "'mzo_staff_case_designation',$1::text,$2::text,$3::text,$4::text),0))::text",
        *key,
    )
    current = await owner.fetchrow(
        "SELECT designation_revision, designation_digest FROM mzo_staff_case_designation_current "
        "WHERE tenant=$1 AND environment=$2 AND engine_name=$3 AND database_incarnation=$4 FOR UPDATE",
        *key,
    )
    rotating = current is not None and current["designation_revision"] != revision
    if current is not None and revision < current["designation_revision"]:
        raise OpsError(f"revisao {revision} menor que a corrente {current['designation_revision']}: recusado")
    if (
        current is not None
        and revision == current["designation_revision"]
        and (current["designation_digest"] != digest)
    ):
        raise OpsError("designacao corrente e outra nesta revisao: recusado")
    if rotating and previous_revision(rows["designation"]) != current["designation_revision"]:
        raise OpsError(
            "expected_previous_revision da designacao nova nao e a corrente: recusado (rotacao e N->N+1)"
        )
    event = await owner.fetchrow(
        "SELECT designation_digest, canonical_designation, installation_proof FROM "
        "mzo_staff_case_designation_event WHERE tenant=$1 AND environment=$2 AND engine_name=$3 "
        "AND database_incarnation=$4 AND designation_revision=$5",
        *key,
        revision,
    )
    designation_text, proof_text = rows["designation"].decode("utf-8"), rows["proof"].decode("utf-8")
    if event is None:
        await owner.execute(
            "INSERT INTO mzo_staff_case_designation_event(tenant,environment,engine_name,"
            "database_incarnation,designation_revision,designation_digest,canonical_designation,"
            "installation_proof) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
            *key,
            revision,
            digest,
            designation_text,
            proof_text,
        )
        actions["designation_event"] = "inserida"
    elif (event["designation_digest"], event["canonical_designation"], event["installation_proof"]) != (
        digest,
        designation_text,
        proof_text,
    ):
        raise OpsError("ja existe OUTRA designacao nesta revisao: recusado (sem DELETE)")
    else:
        actions["designation_event"] = "igual"
    if current is None:
        await owner.execute(
            "INSERT INTO mzo_staff_case_designation_current(tenant,environment,engine_name,"
            "database_incarnation,designation_revision,designation_digest) VALUES($1,$2,$3,$4,$5,$6)",
            *key,
            revision,
            digest,
        )
        actions["designation_current"] = "inserida"
    elif rotating:
        # Rotacao: a revisao anterior fica no historico (event e imutavel); so o ponteiro anda.
        await owner.execute(
            "UPDATE mzo_staff_case_designation_current SET designation_revision=$5, designation_digest=$6 "
            "WHERE tenant=$1 AND environment=$2 AND engine_name=$3 AND database_incarnation=$4 "
            "AND designation_revision=$7",
            *key,
            revision,
            digest,
            current["designation_revision"],
        )
        actions["designation_current"] = f"rotacionada r{current['designation_revision']}->r{revision}"
    else:
        actions["designation_current"] = "igual"
    return actions


async def install(owner: Any, identity: Any, rows: dict[str, Any]) -> dict[str, Any]:
    if await owner.fetchval("SELECT current_user") != OWNER:
        raise OpsError("sessao nao e do dono do schema nativo")
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {NATIVE_SCHEMA}")
        actions: dict[str, Any] = await install_designation(owner, rows)
        admission = await owner.fetchrow(
            "SELECT record_, signature_, revoked_ FROM mzo_portal_read_admission WHERE admission_ref_=$1 "
            "AND revision_=$2",
            rows["admission_ref"],
            rows["admission_revision"],
        )
        if admission is None:
            await owner.execute(
                "INSERT INTO mzo_portal_read_admission(admission_ref_,revision_,record_,signature_) "
                "VALUES($1,$2,$3,$4)",
                rows["admission_ref"],
                rows["admission_revision"],
                rows["record"],
                rows["signature"],
            )
            actions["admission"] = "inserida"
        elif (bytes(admission["record_"]), bytes(admission["signature_"]), admission["revoked_"]) != (
            rows["record"],
            rows["signature"],
            False,
        ):
            raise OpsError("ja existe OUTRA admissao nesta revisao (ou revogada): recusado")
        else:
            actions["admission"] = "igual"
    login, path = rows["identity_login"], rows["identity_search_path"]
    if await identity.fetchval("SELECT current_user") != login:
        raise OpsError("credencial de identidade nao e o identity_login: recusado")
    await identity.execute(f"ALTER ROLE CURRENT_USER SET search_path = {path}")
    actions["identity_search_path"] = f"{login}={path}"
    return actions


async def prove(owner: Any, identity: Any, rows: dict[str, Any]) -> dict[str, bool]:
    scope = rows["scope"]
    key = (scope["tenant"], scope["environment"], scope["engine_name"], scope["database_incarnation"])
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {NATIVE_SCHEMA}")
        event = await owner.fetchrow(
            "SELECT e.canonical_designation, e.installation_proof, e.designation_digest FROM "
            "mzo_staff_case_designation_current c JOIN mzo_staff_case_designation_event e USING "
            "(tenant,environment,engine_name,database_incarnation,designation_revision,designation_digest) "
            "WHERE c.tenant=$1 AND c.environment=$2 AND c.engine_name=$3 AND c.database_incarnation=$4",
            *key,
        )
        admission = await owner.fetchrow(
            "SELECT record_, signature_ FROM mzo_portal_read_admission "
            "WHERE admission_ref_=$1 AND revision_=$2",
            rows["admission_ref"],
            rows["admission_revision"],
        )
    setting = await identity.fetchval(
        "SELECT array_to_string(setconfig, ',') FROM pg_db_role_setting s JOIN pg_roles r ON r.oid=s.setrole "
        "WHERE r.rolname=$1 AND s.setdatabase=0",
        rows["identity_login"],
    )
    return dict(
        designation=event is not None
        and event["canonical_designation"].encode() == rows["designation"]
        and event["installation_proof"].encode() == rows["proof"]
        and event["designation_digest"] == rows["designation_sha256"],
        admission=admission is not None
        and bytes(admission["record_"]) == rows["record"]
        and bytes(admission["signature_"]) == rows["signature"],
        identity_search_path=f"search_path={rows['identity_search_path']}" in (setting or ""),
    )


def main() -> int:
    from tools.staff_install.installer import parse_admin, parse_credential, tls_context

    try:
        import asyncpg  # type: ignore[import-untyped]

        host, port, database = env("DB_HOST"), int(env("DB_PORT")), env("DB_NAME")
        client = secrets_client()
        rows = parse(secret_json(client, env("STAFF_ROWS_SECRET_ARN")))
        verify(rows)
        owner_password = parse_credential(
            client.get_secret_value(SecretId=env("STAFF_OWNER_SECRET_ARN"))["SecretString"], OWNER
        )
        identity_password = parse_credential(
            client.get_secret_value(SecretId=env("STAFF_IDENTITY_SECRET_ARN"))["SecretString"],
            rows["identity_login"],
        )
        context: ssl.SSLContext = tls_context(None)
        app_credentials: tuple[str, str] = ("", "")
        if rows.get("human_plane") is not None or rows.get("assignment") is not None:
            app_credentials = parse_admin(
                client.get_secret_value(SecretId=env("STAFF_APP_DB_SECRET_ARN"))["SecretString"]
            )
        engine_credentials: tuple[str, str] = ("", "")
        if rows.get("task_source") is not None:
            engine_credentials = parse_admin(
                client.get_secret_value(SecretId=env("STAFF_ENGINE_DB_SECRET_ARN"))["SecretString"]
            )
            if engine_credentials[0] != "cibseven_app":
                raise OpsError("segredo do engine nao e do cibseven_app")

        async def run() -> dict[str, Any]:
            owner = await asyncpg.connect(
                host=host,
                port=port,
                user=OWNER,
                password=owner_password,
                database=database,
                ssl=context,
                timeout=15,
            )
            identity = await asyncpg.connect(
                host=host,
                port=port,
                user=rows["identity_login"],
                password=identity_password,
                database=database,
                ssl=context,
                timeout=15,
            )
            try:
                actions = await bootstrap(owner, rows)
                actions.update(await install(owner, identity, rows))
                if rows.get("task_source") is not None:
                    engine_user, engine_password = engine_credentials
                    engine_owner = await asyncpg.connect(
                        host=host,
                        port=port,
                        user=engine_user,
                        password=engine_password,
                        database=database,
                        ssl=context,
                        timeout=15,
                    )
                    try:
                        actions["task_source"] = await task_source(owner, engine_owner, rows)
                    finally:
                        await engine_owner.close()
                if rows.get("human_plane") is not None or rows.get("assignment") is not None:
                    app_user, app_password = app_credentials
                    app_owner = await asyncpg.connect(
                        host=host,
                        port=port,
                        user=app_user,
                        password=app_password,
                        database=database,
                        ssl=context,
                        timeout=15,
                    )
                    try:
                        if rows.get("human_plane") is not None:
                            actions["human_plane"] = await human_plane(app_owner, rows)
                        if rows.get("assignment") is not None:
                            actions["assignment"] = await assignment_plane(owner, app_owner, rows)
                    finally:
                        await app_owner.close()
                proof = await prove(owner, identity, rows)
                tls = await owner.fetchval("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            finally:
                await owner.close()
                await identity.close()
            return dict(actions=actions, proof=proof, owner_tls=bool(tls))

        result = asyncio.run(run())
    except OpsError as failure:
        print(f"recusado: {failure}", file=sys.stderr)
        return 1
    except Exception as failure:
        message = getattr(failure, "message", None) if hasattr(failure, "sqlstate") else None
        print(f"falhou: {type(failure).__name__}{': ' + message if message else ''}", file=sys.stderr)
        return 1
    ok = all(result["proof"].values()) and result["owner_tls"]
    print(
        json.dumps(
            dict(
                schema="staff-native-rows-result.v1",
                ok=ok,
                designation_sha256=rows["designation_sha256"],
                admission_sha256=rows["admission_sha256"],
                **result,
            ),
            sort_keys=True,
        )
    )
    return 0 if ok else 2
