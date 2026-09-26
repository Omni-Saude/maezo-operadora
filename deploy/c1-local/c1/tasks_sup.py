"""Passo `tasks-sup` (servico `job`, depois do `supervisor`): a fonte de tarefas do job T1.5 publica a
`UT_SupervisorAssume` (o catalogo do `h1-task` ja carrega a entrada) e a fila `team` do supervisor,
lida como o BFF (um `EngineReadBundle` por requisicao), a mostra; a do grupo da DMN fica vazia.

Reproduz o dev (26/09): `/tasks?queue=team` do `supervisao-atendimento` dava 503
`read_dependency_unavailable` porque o PREFLIGHT do discover tratava um candidato fora do catalogo
como falha da fila inteira. Agora o candidato nao admitido e omitido e o admitido aparece.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

from . import publish
from .common import ENGINE_SCHEMA, TENANT, admin_dsn, save_state, state, step, tls_context
from .h1_task import IN_GROUP, JOB, PROCESS_KEY, SUPERVISOR_KEY, _read_as

SUPERVISOR = "staff-c1-supervisor"


async def main_async() -> None:
    engine = state("engine")
    os.environ["MAEZO_HUMAN_MATERIAL_VERSION_ID"] = engine["human_version"]
    os.environ["MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256"] = engine["human_manifest_sha256"]
    detail: list[str] = []
    try:
        pg = await asyncpg.connect(admin_dsn(), ssl=tls_context(), timeout=10)
        try:
            task_id = await pg.fetchval(
                f"SELECT t.id_ FROM {ENGINE_SCHEMA}.act_ru_task t JOIN {ENGINE_SCHEMA}.act_re_procdef d "
                "ON d.id_=t.proc_def_id_ WHERE t.tenant_id_=$1 AND t.task_def_key_=$2 AND d.key_=$3 "
                "AND t.suspension_state_=1 ORDER BY t.create_time_ DESC LIMIT 1",
                TENANT, SUPERVISOR_KEY, PROCESS_KEY,
            )
        finally:
            await pg.close()
        detail.append(f"{SUPERVISOR_KEY}={task_id}")
        code, out, err = await asyncio.to_thread(publish.run_once, str(JOB / "config-tasks.json"))
        detail.append(f"job rc={code} {out or err}"[:400])
        seen: dict[str, object] = {}
        for name in (SUPERVISOR, IN_GROUP):
            try:
                seen[name] = (await _read_as(name, None))[0]
            except Exception as failure:  # medido: a causa vai na linha
                seen[name] = f"{type(failure).__name__}: {str(failure)[:120]}"
        detail.append(f"team[{SUPERVISOR}]={seen[SUPERVISOR]} team[{IN_GROUP}]={seen[IN_GROUP]}")
        ok = (
            task_id is not None and code == 0
            and seen[SUPERVISOR] == [task_id] and seen[IN_GROUP] == []
        )
        save_state("tasks-sup", dict(task_id=task_id))
        step("tasks-sup", ok, "; ".join(detail))
    except Exception as failure:  # o passo mede; a causa vai na linha
        step("tasks-sup", False, "; ".join(detail) + f"; {type(failure).__name__}: {str(failure)[:300]}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
