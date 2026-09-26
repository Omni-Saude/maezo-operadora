"""`task-facts`: os fatos DEPLOYADOS que o catalogo com tarefas (H2) pina, lidos no banco do engine.

So leitura, com a credencial mestre (`STAFF_ADMIN_SECRET_ARN`). Imprime uma linha JSON PUBLICA:
a `UT_TratarEscalonamento` viva do tenant (id opaco + grupos candidatos), a definicao
SP-OP-ESCALATION-001 e a DMN `escalation_routing` do tenant (id, key, versao e SHA-256 dos BYTES
deployados) e onde estao as tabelas do outbox humano (schema + dono). Nenhum dado de negocio.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from typing import Any

from .common import OpsError, env, secrets_client

TASK_KEY = "UT_TratarEscalonamento"
PROCESS_KEY = "SP-OP-ESCALATION-001"
DMN_KEY = "escalation_routing"
OUTBOX_TABLES = (
    "human_command_outbox",
    "human_command_delivery",
    "audit_chain",
    "audit_emit_dedup",
    "portal_assignment_source",
    "portal_assignment_receipt_source",
)


async def collect(pg: Any, tenant: str, engine_schema: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", engine_schema):
        raise OpsError("engine_schema invalido")
    s = engine_schema
    task = await pg.fetchrow(
        f"SELECT t.id_, t.proc_def_id_ FROM {s}.act_ru_task t "
        f"JOIN {s}.act_re_procdef d ON d.id_=t.proc_def_id_ "
        f"WHERE t.tenant_id_=$1 AND t.task_def_key_=$2 AND d.key_=$3 AND t.suspension_state_=1 "
        "ORDER BY t.create_time_ DESC LIMIT 1",
        tenant,
        TASK_KEY,
        PROCESS_KEY,
    )
    if task is None:
        raise OpsError("nenhuma UT_TratarEscalonamento viva no tenant")
    groups = [
        r["group_id_"]
        for r in await pg.fetch(
            f"SELECT group_id_ FROM {s}.act_ru_identitylink WHERE task_id_=$1 AND type_='candidate' "
            "AND group_id_ IS NOT NULL ORDER BY group_id_",
            task["id_"],
        )
    ]
    definition = await pg.fetchrow(
        f"SELECT d.id_, d.key_, d.version_, b.bytes_ FROM {s}.act_re_procdef d JOIN {s}.act_ge_bytearray b "
        "ON b.deployment_id_=d.deployment_id_ AND b.name_=d.resource_name_ WHERE d.id_=$1",
        task["proc_def_id_"],
    )
    dmn = await pg.fetchrow(
        f"SELECT d.id_, d.key_, d.version_, b.bytes_ FROM {s}.act_re_decision_def d "
        f"JOIN {s}.act_ge_bytearray b ON b.deployment_id_=d.deployment_id_ AND b.name_=d.resource_name_ "
        "WHERE d.key_=$1 AND d.tenant_id_=$2 "
        "ORDER BY d.version_ DESC LIMIT 1",
        DMN_KEY,
        tenant,
    )
    if definition is None or dmn is None:
        raise OpsError("definicao ou DMN do tenant ausente")
    outbox = [
        dict(schema=r["schemaname"], table=r["tablename"], owner=r["tableowner"])
        for r in await pg.fetch(
            "SELECT schemaname, tablename, tableowner FROM pg_tables WHERE tablename = ANY($1::text[]) "
            "ORDER BY schemaname, tablename",
            list(OUTBOX_TABLES),
        )
    ]

    def artifact(row: Any) -> dict[str, str]:
        return dict(
            id=row["id_"],
            key=row["key_"],
            version=str(row["version_"]),
            sha256=hashlib.sha256(bytes(row["bytes_"])).hexdigest(),
        )

    return dict(
        schema="staff-task-facts.v1",
        tenant=tenant,
        task=dict(id=task["id_"], process_definition_id=task["proc_def_id_"], candidate_groups=groups),
        definition=artifact(definition),
        dmn=artifact(dmn),
        outbox_tables=outbox,
    )


def main() -> int:
    from tools.staff_install.installer import parse_admin, tls_context

    try:
        import asyncpg  # type: ignore[import-untyped]

        client = secrets_client()
        user, password = parse_admin(
            client.get_secret_value(SecretId=env("STAFF_ADMIN_SECRET_ARN"))["SecretString"]
        )

        async def run() -> dict[str, Any]:
            pg = await asyncpg.connect(
                host=env("DB_HOST"),
                port=int(env("DB_PORT")),
                user=user,
                password=password,
                database=env("DB_NAME"),
                ssl=tls_context(None),
                timeout=15,
            )
            try:
                async with pg.transaction(readonly=True):
                    return await collect(pg, "amh", "cibseven")
            finally:
                await pg.close()

        print(json.dumps(asyncio.run(run()), sort_keys=True))
        return 0
    except OpsError as failure:
        print(f"recusado: {failure}", file=sys.stderr)
        return 1
    except Exception as failure:
        print(f"falhou: {type(failure).__name__}", file=sys.stderr)
        return 1
