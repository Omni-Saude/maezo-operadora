"""Passo `assignment` (servico `job`): o plano de atribuicao ATIVO pelo instalador/ops do repo (Onda 8).

Roda depois do `h1-task` (o job ja publicou principais e a tarefa; a config `config-tasks.json` tem a
fonte nativa, de onde sai o contador do tenant). Nada aqui escreve `portal_assignment_source`:

1. os tres logins de teste (como a instalacao da Onda 3 faz no dev): `outbox`/`source` do plano
   humano do BFF e o da ADMINISTRACAO da fonte; os grants sao os SQL do repo, aplicados pelo DONO do
   schema do tenant (`portal-human-plane-grants.sql`, `portal-assignment-admin-grants.sql`);
2. `tools.staff_ops.assignment.activate_from_job` — o mesmo codigo da task do dev: pacote humano
   pinado, chave `human-authority` e TLS do job, chave da FONTE gerada no `engine-config`;
   `prepare_change` -> assinatura -> pedido duravel -> `/maezo-human/v1/authority` -> `ack_native`;
3. segunda rodada = `ja-ativa` (idempotencia), e a linha relida pelo superusuario.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import asyncpg

from .common import (
    ADMIN,
    ASSIGNMENT,
    ASSIGNMENT_ADMIN_LOGIN,
    HUMAN_OUTBOX_LOGIN,
    HUMAN_SOURCE_LOGIN,
    TENANT,
    admin_dsn,
    password,
    read_text,
    sha256,
    state,
    step,
    tls_context,
    write,
)

PLANE_SQL = Path("/repo/deploy/sql/portal-human-plane-grants.sql")
ADMIN_SQL = Path("/repo/deploy/sql/portal-assignment-admin-grants.sql")
JOB_CONFIG = "/c1/job/config-tasks.json"
#: O recibo da revisao do dono da fonte (`approved_owner_receipts`): artefato de TESTE, so aqui.
OWNER_RECEIPT = dict(artifact_ref="c1-assignment-owner-review", digest=sha256(b"c1 assignment owner review"))


async def _logins() -> None:
    su = await asyncpg.connect(admin_dsn(), ssl=tls_context(), timeout=10)
    try:
        for login in (HUMAN_OUTBOX_LOGIN, HUMAN_SOURCE_LOGIN, ASSIGNMENT_ADMIN_LOGIN):
            secret_file = ADMIN / f"{login}-password"
            if not secret_file.exists():
                write(secret_file, password(), 0o400)
            exists = await su.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", login)
            await su.execute(
                f"{'ALTER' if exists else 'CREATE'} ROLE {login} LOGIN NOINHERIT PASSWORD '{read_text(secret_file)}'"
            )
            # As tabelas moram no schema do tenant e o codigo nao qualifica (mesmo arranjo do `rows`).
            await su.execute(f"ALTER ROLE {login} SET search_path = {TENANT}")
    finally:
        await su.close()
    app = await asyncpg.connect(admin_dsn(user="maezo_app", password_file="maezo-app-password"), ssl=tls_context())
    try:
        async with app.transaction():
            await app.execute("SELECT set_config('maezo.human_plane.schema', $1, true)", TENANT)
            await app.execute("SELECT set_config('maezo.human_plane.outbox_login', $1, true)", HUMAN_OUTBOX_LOGIN)
            await app.execute("SELECT set_config('maezo.human_plane.source_login', $1, true)", HUMAN_SOURCE_LOGIN)
            await app.execute(PLANE_SQL.read_text(encoding="utf-8"))
        async with app.transaction():
            await app.execute("SELECT set_config('maezo.assignment_admin.schema', $1, true)", TENANT)
            await app.execute("SELECT set_config('maezo.assignment_admin.login', $1, true)", ASSIGNMENT_ADMIN_LOGIN)
            await app.execute(ADMIN_SQL.read_text(encoding="utf-8"))
    finally:
        await app.close()


async def main_async() -> None:
    from tools.staff_ops.assignment import Plan, activate_from_job

    engine = state("engine")
    os.environ["MAEZO_HUMAN_MATERIAL_VERSION_ID"] = engine["human_version"]
    os.environ["MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256"] = engine["human_manifest_sha256"]
    detail: list[str] = []
    try:
        await _logins()
        detail.append("logins outbox/source/admin + grants do repo pelo dono do schema")
        facts = state("assignment")
        plan = Plan(
            admin_login=ASSIGNMENT_ADMIN_LOGIN, source_key_id=facts["source_key_id"],
            source_fingerprint=facts["source_fingerprint"], owner_ref=facts["owner_ref"],
            source_ref=facts["source_ref"], owner_receipt=OWNER_RECEIPT,
            valid_until=datetime.now(UTC) + timedelta(days=2),
        )
        secret = quote(read_text(ADMIN / f"{ASSIGNMENT_ADMIN_LOGIN}-password"), safe="")
        dsn = f"postgresql+asyncpg://{ASSIGNMENT_ADMIN_LOGIN}:{secret}@postgres:5432/maezo"
        pem = (ASSIGNMENT / "source-key.pem").read_bytes()
        first = await activate_from_job(plan, dsn, pem, job_config=JOB_CONFIG, ssl_context=tls_context())
        detail.append(f"1a: {first}")
        second = await activate_from_job(plan, dsn, pem, job_config=JOB_CONFIG, ssl_context=tls_context())
        detail.append(f"2a: {second['action']}")
        su = await asyncpg.connect(admin_dsn(), ssl=tls_context(), timeout=10)
        try:
            row = await su.fetchrow(
                f"SELECT state, source_revision, pending_publication_id, active_generation_digest "
                f"FROM {TENANT}.portal_assignment_source WHERE tenant=$1", TENANT,
            )
            native = await su.fetchrow(
                "SELECT state_, generation_digest_ FROM maezo_native.mzo_human_assignment_generation WHERE tenant_=$1",
                TENANT,
            )
        finally:
            await su.close()
        detail.append(f"fonte={dict(row) if row else None} engine={dict(native) if native else None}")
        ok = (
            first.get("state") == "active" and second["action"] == "ja-ativa"
            and row is not None and row["state"] == "active" and row["pending_publication_id"] is None
            and native is not None and native["state_"] == "active"
            and native["generation_digest_"] == row["active_generation_digest"]
        )
        step("assignment", ok, "; ".join(detail))
    except Exception as failure:  # o passo mede; a causa vai na linha
        step("assignment", False, "; ".join(detail) + f"; {type(failure).__name__}: {str(failure)[:300]}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
