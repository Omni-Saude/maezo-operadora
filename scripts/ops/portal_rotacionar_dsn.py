# Rotaciona a senha da role `portal_bff_amh` SEM que senha ou verificador SCRAM
# passem por `containerOverrides` (campo recuperavel por ecs:DescribeTasks).
#
# Contrato: faz o BANCO concordar com o Secrets Manager. A DSN nova ja' foi
# gravada no segredo; aqui ela e' BUSCADA por GetSecretValue (TLS + IAM, com a
# task role), o verificador SCRAM e' derivado DENTRO do container e so' ele vai
# no SQL — `log_statement=ddl` registraria `ALTER ROLE ... PASSWORD` em claro.
# O override carrega apenas CODIGO e o ARN do segredo (que nao e' segredo: vive
# em portal.auto.tfvars, versionado).
import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets

import asyncpg
import boto3

ROLE = "portal_bff_amh"
SECRET_ARN = os.environ["PORTAL_SECRET_ARN"]


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """Verificador SCRAM-SHA-256 calculado NO CLIENTE (RFC 5802)."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    stored = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return (
        f"SCRAM-SHA-256${iterations}:{base64.b64encode(salt).decode()}"
        f"${base64.b64encode(stored).decode()}:{base64.b64encode(server).decode()}"
    )


async def main() -> None:
    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "sa-east-1"))
    dsn = sm.get_secret_value(SecretId=SECRET_ARN)["SecretString"]
    m = re.fullmatch(r"postgresql\+asyncpg://([^:]+):([^@]+)@([^:/]+):(\d+)/(\S+)", dsn.strip())
    if not m:
        raise SystemExit("DSN fora do formato esperado; nada foi alterado")
    user, pwd, host, port, db = m.group(1), m.group(2), m.group(3), int(m.group(4)), m.group(5)
    if user != ROLE:
        raise SystemExit(f"usuario da DSN ({user}) nao e' a role do portal; nada foi alterado")
    print(f"DSN lida do Secrets Manager por API: role={user} host={host} db={db} (senha nunca impressa)")

    verificador = scram_verifier(pwd)
    print("verificador SCRAM-SHA-256 derivado dentro do container (4096 iteracoes, salt de 16 bytes)")

    admin = await asyncpg.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["ADMIN_USER"],
        password=os.environ["ADMIN_PASSWORD"],
        database=db,
    )
    try:
        await admin.execute(f"ALTER ROLE \"{ROLE}\" PASSWORD '{verificador}'")
        print(f"ALTER ROLE {ROLE}: verificador gravado (nenhuma senha em claro no SQL)")
    finally:
        await admin.close()

    prova = await asyncpg.connect(host=host, port=port, user=user, password=pwd, database=db, ssl="require")
    try:
        quem = await prova.fetchval("select current_user")
        caminho = await prova.fetchval("show search_path")
        membros = await prova.fetchval("select count(*) from amh.portal_memberships")
        negado = "nao testado"
        try:
            await prova.fetchval("select count(*) from amh.pacientes")
            negado = "FALHOU: leu tabela fora do grant"
        except asyncpg.PostgresError as exc:
            negado = type(exc).__name__
    finally:
        await prova.close()
    print(f"login com a senha NOVA: current_user={quem} search_path={caminho} portal_memberships={membros}")
    print(f"negativo preservado (tabela fora do grant): {negado}")
    print("ROTACAO OK")


asyncio.run(main())
