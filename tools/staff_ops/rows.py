"""`rows`: designacao instalada + admissao Q2 no schema nativo, como o DONO do schema.

Entradas (nenhuma por env/override alem de ARNs e do endereco do banco):
* `STAFF_ROWS_SECRET_ARN` -> `staff-native-rows.v1`: designacao, prova de instalacao, registro
  `portal-read-admission.v1` e a assinatura Ed25519 (64 bytes), todos em base64, mais os digests
  esperados. O conteudo e publico (assinado), mas a fonte unica e o segredo pinado, para que o
  que se instala seja exatamente o que o aprovador assinou;
* `STAFF_OWNER_SECRET_ARN` -> credencial de `maezo_native_schema_owner`;
* `STAFF_ADMIN_SECRET_ARN` -> credencial mestre, SO para `ALTER ROLE <identity_login> SET
  search_path` (o job T1.5 le `portal_memberships` sem qualificar o schema).

Antes de escrever, confere por conta propria: SHA-256 da designacao, que a prova assina ESSE digest
com a raiz instalada (verificador do portal), e a assinatura da admissao no dominio
`maezo/portal-read-admission/v1\\0`. Idempotente: linha igual = nada muda; linha diferente na mesma
chave = recusa (nunca DELETE/UPDATE). Depois reler e comparar byte a byte (prova).
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
}
_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def parse(document: dict[str, Any]) -> dict[str, Any]:
    if set(document) != _KEYS or document["schema"] != SCHEMA:
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


def verify(rows: dict[str, Any]) -> None:
    """Confere o que vai ser instalado com os MESMOS verificadores do runtime."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from maezo.gateway.external_cases.models import now_utc, parse
    from maezo.gateway.staff_cases.authority import InstalledStaffAuthority
    from maezo.gateway.staff_cases.models import Designation, Proof

    if hashlib.sha256(rows["designation"]).hexdigest() != rows["designation_sha256"]:
        raise OpsError("designacao nao e a do digest aprovado")
    if hashlib.sha256(rows["record"]).hexdigest() != rows["admission_sha256"]:
        raise OpsError("admissao nao e a do digest aprovado")
    root = serialization.load_der_public_key(rows["root"])
    if not isinstance(root, Ed25519PublicKey):
        raise OpsError("raiz nao e Ed25519")
    try:
        designation = parse(Designation, rows["designation"])
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


async def install(owner: Any, admin: Any, rows: dict[str, Any]) -> dict[str, Any]:
    scope = rows["scope"]
    key = (scope["tenant"], scope["environment"], scope["engine_name"], scope["database_incarnation"])
    revision, digest = rows["designation_revision"], rows["designation_sha256"]
    actions: dict[str, str] = {}
    if await owner.fetchval("SELECT current_user") != OWNER:
        raise OpsError("sessao nao e do dono do schema nativo")
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {NATIVE_SCHEMA}")
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
        current = await owner.fetchrow(
            "SELECT designation_revision, designation_digest FROM mzo_staff_case_designation_current "
            "WHERE tenant=$1 AND environment=$2 AND engine_name=$3 AND database_incarnation=$4",
            *key,
        )
        if current is None:
            await owner.execute(
                "INSERT INTO mzo_staff_case_designation_current(tenant,environment,engine_name,"
                "database_incarnation,designation_revision,designation_digest) VALUES($1,$2,$3,$4,$5,$6)",
                *key,
                revision,
                digest,
            )
            actions["designation_current"] = "inserida"
        elif (current["designation_revision"], current["designation_digest"]) != (revision, digest):
            raise OpsError("designacao corrente e outra: rotacao nao e deste instalador")
        else:
            actions["designation_current"] = "igual"
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
    await admin.execute(f"ALTER ROLE {login} SET search_path = {path}")
    actions["identity_search_path"] = f"{login}={path}"
    return actions


async def prove(owner: Any, admin: Any, rows: dict[str, Any]) -> dict[str, bool]:
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
    setting = await admin.fetchval(
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
        admin_user, admin_password = parse_admin(
            client.get_secret_value(SecretId=env("STAFF_ADMIN_SECRET_ARN"))["SecretString"]
        )
        context: ssl.SSLContext = tls_context(None)

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
            admin = await asyncpg.connect(
                host=host,
                port=port,
                user=admin_user,
                password=admin_password,
                database=database,
                ssl=context,
                timeout=15,
            )
            try:
                actions = await install(owner, admin, rows)
                proof = await prove(owner, admin, rows)
                tls = await owner.fetchval("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            finally:
                await owner.close()
                await admin.close()
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
