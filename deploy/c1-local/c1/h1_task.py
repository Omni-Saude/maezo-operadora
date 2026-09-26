"""Passo `h1-task` (servico `job`): H1-H4 da D-N, a tarefa humana VIVA publicada pela fonte real.

Roda depois do `issuer` (a `UT_TratarEscalonamento` da escalacao `SYN-` esta viva, com o candidato
que a DMN `escalation_routing` escolheu). Sem os contornos D11-D13 do H1:

1. le do banco os fatos DEPLOYADOS (definicao, DMN, bytes) e monta o catalogo com a entrada da
   tarefa (form/policies sinteticos `SYN-`, o compilado de `PortalReadCommand.COMPILED`);
2. o aprovador (raiz de TESTE) revisa a admissao Q2 revisao 2 = revisao 1 + catalogo com tarefas +
   publicador `resource` + bloco `human` CONTRA o catalogo (`approver.review_admission(..,
   catalog)`, H4) e assina; o dono a instala. A revisao 2 so pode existir depois do deploy: ela
   pina o digest dos bytes deployados (nao e desvio, e a ordem da instalacao);
3. a instalacao cria o login da fonte de tarefas e aplica `deploy/sql/portal-task-source-grants.sql`
   (duas partes, cada uma pelo dono do schema dela);
4. o `__main__` do job T1.5 (duas rodadas) com `native_source` + `tasks`: segue o contador real do
   tenant (D13), designa o catalogo com tarefas, renova as memberships e publica evidencia +
   recurso da tarefa pela fonte REAL (H2). A chave do job ja tem `resource` no trust (H4, fim do D11);
5. le o Q2 pelo cliente Python de producao (`EngineReadBundle`, D12: a revisao admitida vem do
   engine, o pacote humano pina so o piso): catalogo -> fila `team` de cada principal -> `task`. A
   tarefa aparece para `atendimento-humano` e NAO para `enfermagem-triagem`, embora o recurso
   conceda os dois (quem separa e o candidato real, `verifyIdentityPolicy`).

Unico desvio: D14 (README) — o login da fonte de tarefas nasce aqui, como os outros logins de teste.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import asyncpg
from tools.staff_materials import approver

from .common import (
    ADMIN,
    CATALOG_REF,
    ENGINE_SCHEMA,
    NATIVE_SCHEMA,
    OWNER_LOGIN,
    PORTAL_WORKLOAD,
    TENANT,
    TEST_ROOT,
    admin_dsn,
    iso,
    jcs,
    now,
    password,
    read_text,
    save_state,
    sha256,
    state,
    step,
    tls_context,
    write,
)
from .engine_config import ADMISSION_REF
from .seed import ISSUER, PRINCIPALS

TASK_KEY = "UT_TratarEscalonamento"
#: A tarefa do SLA de resolucao vencido (grupo LITERAL do BPMN). Sem esta entrada no catalogo
#: admitido a fila do supervisor nao a mostra (e, antes da correcao do PREFLIGHT, virava 503 inteira).
SUPERVISOR_KEY, SUPERVISOR_GROUP = "UT_SupervisorAssume", "supervisao-atendimento"
PROCESS_KEY = "SP-OP-ESCALATION-001"
DMN_KEY = "escalation_routing"
#: `PortalReadCommand.COMPILED["SP-OP-ESCALATION-001/UT_TratarEscalonamento"]` (form_key, status, inputs).
COMPILED = ("escalation", "BPMN_FORMDATA", ["resultado", "notas_resolucao"])
RESOURCE_PREFIX = f"portal-resource:{TENANT}:task:"
IN_GROUP, OUTSIDE = "staff-c1-no-grupo", "staff-c1-outro-grupo"
TASK_SOURCE_LOGIN = f"portal_task_source_{TENANT}"
GRANTS_SQL = Path("/repo/deploy/sql/portal-task-source-grants.sql")
JOB = Path("/c1/job")


def _pin(name: str) -> dict[str, str]:
    return dict(artifact_ref=f"SYN-{name}", digest=sha256(f"SYN {name}".encode()))


def _artifact(name: str) -> dict[str, str]:
    raw = f"SYN {name}".encode()
    return dict(artifact_ref=f"SYN-{name}", digest=sha256(raw), bytes_base64=base64.b64encode(raw).decode())


POLICIES = ("subject-policy", "consent-policy", "resource-policy", "disclosure-policy", "opaque-task-id-policy")
CLASSIFICATION = dict(
    classification_ref="SYN-classification-escalation",
    classification_digest=sha256(b"SYN classification escalation"),
    policy_ref=_pin("disclosure-policy")["artifact_ref"],
    policy_digest=_pin("disclosure-policy")["digest"],
    projection="full_task_detail.v1",
    fields_digest=sha256(b"SYN fields escalation"),
)


async def _facts(pg: asyncpg.Connection) -> dict:
    s = ENGINE_SCHEMA
    task = await pg.fetchrow(
        f"SELECT t.id_, t.proc_def_id_ FROM {s}.act_ru_task t JOIN {s}.act_re_procdef d ON d.id_=t.proc_def_id_ "
        f"WHERE t.tenant_id_=$1 AND t.task_def_key_=$2 AND d.key_=$3 AND t.suspension_state_=1 "
        "ORDER BY t.create_time_ DESC LIMIT 1", TENANT, TASK_KEY, PROCESS_KEY,
    )
    if task is None:
        raise RuntimeError("nenhuma UT_TratarEscalonamento viva no tenant (o `issuer` rodou?)")
    groups = [r["group_id_"] for r in await pg.fetch(
        f"SELECT group_id_ FROM {s}.act_ru_identitylink WHERE task_id_=$1 AND type_='candidate' "
        "AND group_id_ IS NOT NULL ORDER BY group_id_", task["id_"])]
    definition = await pg.fetchrow(
        f"SELECT d.id_, d.key_, d.version_, b.bytes_ FROM {s}.act_re_procdef d JOIN {s}.act_ge_bytearray b "
        "ON b.deployment_id_=d.deployment_id_ AND b.name_=d.resource_name_ WHERE d.id_=$1", task["proc_def_id_"])
    dmn = await pg.fetchrow(
        f"SELECT d.id_, d.key_, d.version_, b.bytes_ FROM {s}.act_re_decision_def d JOIN {s}.act_ge_bytearray b "
        "ON b.deployment_id_=d.deployment_id_ AND b.name_=d.resource_name_ WHERE d.key_=$1 AND d.tenant_id_=$2 "
        "ORDER BY d.version_ DESC LIMIT 1", DMN_KEY, TENANT)
    return dict(task=task, groups=groups, definition=definition, dmn=dmn)


def _catalog(facts: dict, deployment: tuple[str, str]) -> tuple[list[dict], dict]:
    d, m = facts["definition"], facts["dmn"]
    form_key, status, inputs = COMPILED
    form = dict(_artifact(f"read-form-{form_key}"), artifact_ref=form_key)
    entry = dict(
        process_definition_id=d["id_"], process_definition_key=d["key_"], process_definition_version=str(d["version_"]),
        process_definition_digest=sha256(bytes(d["bytes_"])), task_definition_key=TASK_KEY, form_key=form_key,
        form_version="1", form_digest=form["digest"], form_source_status=status, allowed_inputs=inputs,
        required_roles=["atendente"], subject_policy=_pin("subject-policy"), consent_policy=_pin("consent-policy"),
        resource_policy=_pin("resource-policy"), disclosure_policy=_pin("disclosure-policy"),
        opaque_task_id_policy=_pin("opaque-task-id-policy"),
        group_domain=dict(
            kind="dmn", groups=["atendimento-humano", "enfermagem-triagem", "plantao-clinico"],
            dmn_definition_id=m["id_"], dmn_definition_key=m["key_"], dmn_definition_version=str(m["version_"]),
            dmn_resource_digest=sha256(bytes(m["bytes_"])),
        ),
    )
    # `PortalReadCommand.COMPILED["SP-OP-ESCALATION-001/UT_SupervisorAssume"]` e o mesmo form.
    supervisor = dict(
        entry, task_definition_key=SUPERVISOR_KEY,
        group_domain=dict(kind="static", groups=[SUPERVISOR_GROUP], dmn_definition_id=None,
                          dmn_definition_key=None, dmn_definition_version=None, dmn_resource_digest=None),
    )
    # O recibo de deploy e o do catalogo staff (config do job): o job confere os dois no artefato.
    artifact = dict(
        schema="portal-read-catalog.v1", catalog_ref=CATALOG_REF, publisher_ref=PORTAL_WORKLOAD, entries=[entry, supervisor],
        policies=[_artifact(p) for p in POLICIES], forms=[form], deployment_receipt_ref=deployment[0],
        deployment_receipt_digest=deployment[1],
    )
    return [entry, supervisor], artifact


def _human(entries: list[dict]) -> dict:
    return dict(entries=[dict(
        process_definition_id=entry["process_definition_id"], task_definition_key=entry["task_definition_key"],
        classification=CLASSIFICATION, identity_policy=_pin("opaque-task-id-policy"),
        # H4: a imagem de Dockerfile.human (o engine de dev) gera ids UUID, nao o DbIdGenerator decimal.
        task_id_format="uuid", candidate_groups=entry["group_domain"]["groups"], user_candidates="refused",
    ) for entry in entries])


async def _owner() -> asyncpg.Connection:
    return await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )


async def _admission_rev2(catalog_raw: bytes, human: dict) -> tuple[bytes, str, int]:
    owner = await _owner()
    try:
        raw = await owner.fetchval(
            f"SELECT record_ FROM {NATIVE_SCHEMA}.mzo_portal_read_admission WHERE admission_ref_=$1 AND revision_=1",
            ADMISSION_REF,
        )
        revision = 1 + await owner.fetchval(
            f"SELECT max(revision_) FROM {NATIVE_SCHEMA}.mzo_portal_read_admission WHERE admission_ref_=$1",
            ADMISSION_REF,
        )
        record = json.loads(bytes(raw))
        record["admission_revision"] = str(revision)
        record["catalog"]["catalog_digest"] = sha256(catalog_raw)
        record["publishers"].append(dict(kind="resource", publisher_ref=PORTAL_WORKLOAD, source_ref_prefix=RESOURCE_PREFIX))
        record["human"] = human
        record_raw = jcs(record)
        # H4: o aprovador revisa a admissao CONTRA o catalogo com tarefas (sem ele, recusa).
        _, shown, lines = approver.review_admission(record_raw, catalog_raw)
        root = approver.load_root(TEST_ROOT / "installation-root-key.pem")
        signature = base64.b64decode(approver.sign_admission(record_raw, root, confirm_digest=shown, catalog=catalog_raw))
        await owner.execute(
            f"INSERT INTO {NATIVE_SCHEMA}.mzo_portal_read_admission(admission_ref_,revision_,record_,signature_) "
            "VALUES($1,$2,$3,$4)", ADMISSION_REF, revision, record_raw, signature,
        )
        return record_raw, shown, revision
    finally:
        await owner.close()


async def _task_source_login() -> str:
    """D14: o login da fonte de tarefas (no dev, a instalacao da Onda 3 cria; aqui, o harness)."""
    secret_file = ADMIN / f"{TASK_SOURCE_LOGIN}-password"
    if not secret_file.exists():
        write(secret_file, password(), 0o400)
    secret = read_text(secret_file)
    su = await asyncpg.connect(admin_dsn(), ssl=tls_context(), timeout=10)
    try:
        exists = await su.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", TASK_SOURCE_LOGIN)
        await su.execute(
            f"{'ALTER' if exists else 'CREATE'} ROLE {TASK_SOURCE_LOGIN} LOGIN NOINHERIT PASSWORD '{secret}'"
        )
    finally:
        await su.close()
    sql = GRANTS_SQL.read_text(encoding="utf-8")
    for part, connection in (
        ("native", await _owner()),
        ("engine", await asyncpg.connect(
            admin_dsn(user="cibseven_app", password_file="cibseven-password"), ssl=tls_context(), timeout=10)),
    ):
        try:
            await connection.execute(f"SET maezo.task_source.login = '{TASK_SOURCE_LOGIN}'")
            await connection.execute(f"SET maezo.task_source.part = '{part}'")
            await connection.execute(f"SET maezo.task_source.engine_schema = '{ENGINE_SCHEMA}'")
            await connection.execute(sql)
        finally:
            await connection.close()
    return f"postgresql+asyncpg://{TASK_SOURCE_LOGIN}:{quote(secret, safe='')}@postgres:5432/maezo"


def _principal(name: str) -> Any:
    from maezo.gateway.human.read_profile import parse_model
    from maezo.portal.contracts.models import HumanPrincipal

    subject, group = PRINCIPALS[name]
    return parse_model(HumanPrincipal, dict(
        schema_version="1", principal_ref=name, issuer=ISSUER, subject=subject, tenant=TENANT,
        membership_revision="1",
        memberships=[dict(membership_ref=f"{name}-m1", roles=["atendente"], groups=[group])],
        session_ref=f"SYN-session-{name}", authenticated_at=iso(now() - timedelta(seconds=5)), subject_bindings=[],
    ))


async def _read_as(name: str, task_id: str | None) -> tuple[list[str], Any]:
    """Uma requisicao do BFF: um `EngineReadBundle` novo, como `compose_human_plane.new_bundle`."""
    from maezo.gateway.human.engine_reads import (
        EngineCatalogExpectationSource,
        EngineHumanTaskQuery,
        EngineHumanTaskTransport,
        EngineReadBundle,
    )
    from maezo.gateway.human.production import _surface_context
    from maezo.gateway.human.production_materials import MATERIAL_DIRECTORY, HumanMaterialPin, load_human_materials
    from maezo.gateway.human.queue import CatalogTrustAnchor
    from maezo.gateway.human.queue_cursor import AeadQueueCursorCustody
    from maezo.gateway.human.read_credentials import ReadCredentialPartition
    from maezo.gateway.human.read_materials import MaterialLifetime, read_providers
    from maezo.gateway.human.read_transport import PortalReadClient
    from maezo.portal.contracts.queues import TaskQueueRequest

    engine_state = state("engine")
    material = load_human_materials(MATERIAL_DIRECTORY, HumanMaterialPin(
        tenant=TENANT, material_version_id=engine_state["human_version"],
        public_manifest_sha256=engine_state["human_manifest_sha256"],
    ))
    lifetime = MaterialLifetime()
    manifest = material.manifest
    admission, credentials, cursor_keys = read_providers(material, lifetime)
    partition = ReadCredentialPartition(
        scope=manifest.scope, engine_name=manifest.engine_name, key_id=manifest.key("portal-task-read").key_id,
        credentials=credentials, admission=admission,
    )
    client = PortalReadClient(
        origin=manifest.read_surface.origin,
        tls_context=_surface_context(manifest.read_surface, manifest.scope, material.directory),
        server_spki_sha256=manifest.read_surface.server_spki_sha256, partition=partition,
        timeout_seconds=manifest.read_surface.timeout_seconds,
    )
    anchor = CatalogTrustAnchor(scope=manifest.scope, catalog_ref=manifest.catalog_ref, publisher_ref=manifest.publisher_ref)
    bundle = EngineReadBundle(client=client, anchor=anchor, cursor=AeadQueueCursorCustody(
        partition=partition, provider=cursor_keys, key_id=manifest.current_cursor().key_id))
    try:
        expectation = await EngineCatalogExpectationSource(bundle).current_catalog(anchor=anchor)
        window = await EngineHumanTaskQuery(bundle).discover(
            _principal(name), TaskQueueRequest(queue="team", limit=25), expected_catalog=expectation
        )
        task = await EngineHumanTaskTransport(bundle).read_task(task_id) if task_id else None
        return list(window.task_ids), task
    finally:
        await client.close()
        lifetime.close()


async def main_async() -> None:
    from . import publish

    engine_state = state("engine")
    os.environ["MAEZO_HUMAN_MATERIAL_VERSION_ID"] = engine_state["human_version"]
    os.environ["MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256"] = engine_state["human_manifest_sha256"]
    detail: list[str] = []
    pg = await asyncpg.connect(admin_dsn(), ssl=tls_context(), timeout=10)
    try:
        facts = await _facts(pg)
        task_id = facts["task"]["id_"]
        detail.append(f"tarefa {task_id} candidatos={facts['groups']}")
        base = publish.config()
        deployment = (base["catalog"]["deployment_receipt_ref"], base["catalog"]["deployment_receipt_digest"])
        entries, artifact = _catalog(facts, deployment)
        catalog_raw = jcs(artifact)
        record_raw, admission_digest, revision = await _admission_rev2(catalog_raw, _human(entries))
        detail.append(f"admissao rev{revision} {admission_digest[:12]} (aprovador conferiu o catalogo)")
        designated = await pg.fetchval(
            f"SELECT revision_ FROM {NATIVE_SCHEMA}.mzo_portal_read_designation WHERE tenant_=$1 AND catalog_=$2",
            TENANT, CATALOG_REF)
    finally:
        await pg.close()
    try:
        dsn = await _task_source_login()
        write(JOB / "native-dsn.txt", dsn, 0o400)
        write(JOB / "task-catalog.json", catalog_raw, 0o444)
        write(JOB / "task-admission.json", record_raw, 0o444)
        config = dict(base)
        config["catalog"] = dict(
            base["catalog"], admitted_catalog_digest=sha256(catalog_raw), catalog_revision=str(int(designated) + 1),
            artifact_file=str(JOB / "task-catalog.json"),
        )
        config["native_source"] = dict(
            dsn_file=str(JOB / "native-dsn.txt"), native_schema=NATIVE_SCHEMA, engine_schema=ENGINE_SCHEMA
        )
        config["tasks"] = dict(admission_record_file=str(JOB / "task-admission.json"), evidence_seconds="21600")
        path = JOB / "config-tasks.json"
        write(path, json.dumps(config), 0o400)
        # O `__main__` do job chama asyncio.run: fora do loop deste passo (numa thread). Chamado
        # direto de dentro do loop ele morre com RuntimeError antes de publicar nada: era o rc=2
        # que o H1 anotou como D13.
        first = await asyncio.to_thread(publish.run_once, str(path))
        second = await asyncio.to_thread(publish.run_once, str(path))
        detail.append(f"job rc={first[0]} {first[1] or first[2]}")
        detail.append(f"2a rc={second[0]} {second[1] or second[2]}")
        published = first[0] == 0 and '"tasks_published": 1' in first[1]
        idempotent = second[0] == 0 and '"tasks_published": 0' in second[1] and '"tasks_unchanged": 1' in second[1]
        seen: dict[str, Any] = {}
        for name in (IN_GROUP, OUTSIDE):
            try:
                ids, task = await _read_as(name, task_id if name == IN_GROUP else None)
                seen[name] = (ids, task)
            except Exception as failure:  # medido: a causa vai na linha
                seen[name] = (f"{type(failure).__name__}: {str(failure)[:120]}", None)
        groups = list(seen[IN_GROUP][1].snapshot.eligible_candidate_groups) if seen[IN_GROUP][1] else None
        detail.append(f"team[{IN_GROUP}]={seen[IN_GROUP][0]} team[{OUTSIDE}]={seen[OUTSIDE][0]} task {groups}")
        ok = (
            published and idempotent
            and isinstance(seen[IN_GROUP][0], list) and task_id in seen[IN_GROUP][0]
            and seen[OUTSIDE][0] == []
            and groups == ["atendimento-humano"]
        )
        save_state("h1", dict(task_id=task_id, admission_digest=admission_digest, catalog_digest=sha256(catalog_raw)))
        step("h1-task", ok, "; ".join(detail))
    except Exception as failure:  # o passo mede; a causa vai na linha
        step("h1-task", False, "; ".join(detail) + f"; {type(failure).__name__}: {str(failure)[:300]}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
