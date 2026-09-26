"""`task-read`: prova da fila humana no plano Q2 do engine (Onda 8), SEM o BFF.

Roda dentro da task do job T1.5 (mesmos volumes: pacote humano em /run/maezo-human-materials e a
DSN de identidade em /run/maezo-job). Para cada principal staff de `portal_memberships`, monta o
`HumanPrincipal` do registro revisado e le, pelo cliente Python de PRODUCAO (`EngineReadBundle`,
chave `portal-task-read` do pacote, mTLS), o catalogo designado e a fila `team`; com tarefa na fila,
le o detalhe. Imprime so contagens, grupos e o id opaco da tarefa. Nenhum dado de negocio.

Existe porque o BFF so liga o plano humano com o plano de atribuicao ATIVO
(`portal_assignment_source.state='active'`, `assignment_transport._active`), que nao faz parte do
H1-H6: a fila de leitura e medida aqui, no mesmo cliente que o BFF usaria.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


async def read_all() -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import create_async_engine

    from maezo.gateway.human.engine_reads import (
        EngineCatalogExpectationSource,
        EngineHumanTaskQuery,
        EngineHumanTaskTransport,
        EngineReadBundle,
    )
    from maezo.gateway.human.production import _surface_context
    from maezo.gateway.human.production_materials import (
        MATERIAL_DIRECTORY,
        HumanMaterialPin,
        load_human_materials,
    )
    from maezo.gateway.human.queue import CatalogTrustAnchor
    from maezo.gateway.human.queue_cursor import AeadQueueCursorCustody
    from maezo.gateway.human.read_credentials import ReadCredentialPartition
    from maezo.gateway.human.read_materials import MaterialLifetime, read_providers
    from maezo.gateway.human.read_profile import parse_model
    from maezo.gateway.human.read_transport import PortalReadClient
    from maezo.portal.api.postgres import PostgresIdentityStore
    from maezo.portal.contracts.models import HumanPrincipal
    from maezo.portal.contracts.queues import TaskQueueRequest

    config = json.loads(Path("/run/maezo-job/config.json").read_text())  # noqa: ASYNC240 - 1 leitura no inicio
    tenant = config["tenant"]
    tls = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    dsn = Path(config["identity_dsn_file"]).read_text().strip()  # noqa: ASYNC240 - 1 leitura no inicio
    engine = create_async_engine(dsn, hide_parameters=True, connect_args={"ssl": tls})
    store = PostgresIdentityStore(tenant, engine)
    from sqlalchemy import text

    async with engine.connect() as db:
        pairs = [
            (r[0], r[1])
            for r in await db.execute(
                text(
                    "SELECT issuer, subject FROM portal_memberships WHERE tenant=:t ORDER BY issuer, subject"
                ),
                {"t": tenant},
            )
        ]
    material = load_human_materials(
        MATERIAL_DIRECTORY,
        HumanMaterialPin(
            tenant=tenant,
            material_version_id=os.environ["MAEZO_HUMAN_MATERIAL_VERSION_ID"],
            public_manifest_sha256=os.environ["MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256"],
        ),
    )
    manifest = material.manifest
    report: dict[str, Any] = {"principals": []}
    for issuer, subject in pairs:
        record = await store.get_membership(issuer, subject)
        if record is None or record.audience != "staff":
            continue
        groups = sorted({g for m in record.memberships for g in m.groups})
        principal = parse_model(
            HumanPrincipal,
            dict(
                schema_version="1",
                principal_ref=record.principal_ref,
                issuer=issuer,
                subject=subject,
                tenant=tenant,
                membership_revision=str(record.revision),
                memberships=[
                    dict(membership_ref=m.membership_ref, roles=list(m.roles), groups=list(m.groups))
                    for m in record.memberships
                ],
                session_ref=f"SYN-probe-{record.principal_ref}",
                authenticated_at=(datetime.now(UTC) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                subject_bindings=[],
            ),
        )
        lifetime = MaterialLifetime()
        admission, credentials, cursor_keys = read_providers(material, lifetime)
        partition = ReadCredentialPartition(
            scope=manifest.scope,
            engine_name=manifest.engine_name,
            key_id=manifest.key("portal-task-read").key_id,
            credentials=credentials,
            admission=admission,
        )
        client = PortalReadClient(
            origin=manifest.read_surface.origin,
            tls_context=_surface_context(manifest.read_surface, manifest.scope, material.directory),
            server_spki_sha256=manifest.read_surface.server_spki_sha256,
            partition=partition,
            timeout_seconds=manifest.read_surface.timeout_seconds,
        )
        anchor = CatalogTrustAnchor(
            scope=manifest.scope, catalog_ref=manifest.catalog_ref, publisher_ref=manifest.publisher_ref
        )
        bundle = EngineReadBundle(
            client=client,
            anchor=anchor,
            cursor=AeadQueueCursorCustody(
                partition=partition, provider=cursor_keys, key_id=manifest.current_cursor().key_id
            ),
        )
        entry: dict[str, Any] = {"groups": groups}
        try:
            expectation = await EngineCatalogExpectationSource(bundle).current_catalog(anchor=anchor)
            window = await EngineHumanTaskQuery(bundle).discover(
                principal, TaskQueueRequest(queue="team", limit=25), expected_catalog=expectation
            )
            entry["team"] = list(window.task_ids)
            if window.task_ids:
                task = await EngineHumanTaskTransport(bundle).read_task(window.task_ids[0])
                entry["detail_candidate_groups"] = list(task.snapshot.eligible_candidate_groups)
        except Exception as failure:
            entry["refused"] = f"{type(failure).__name__}: {str(failure)[:120]}"
        finally:
            await client.close()
            lifetime.close()
        report["principals"].append(entry)
    await engine.dispose()
    return report


def main() -> int:
    try:
        print(json.dumps(asyncio.run(read_all()), sort_keys=True))
        return 0
    except Exception as failure:
        print(f"task-read falhou: {type(failure).__name__}", file=sys.stderr)
        return 1
