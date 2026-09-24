"""T1.6 contra PostgreSQL e CIB Seven REAIS: a membership revisada e o grupo que a DMN escolheu.

* Grantees: tabela `portal_memberships` criada pela migracao REAL 0012 (schema descartavel por teste,
  migracoes reais aplicadas por `_apply_migrations`).
* Escalacoes: BPMN SP-OP-ESCALATION-001 + DMN `escalation_routing` da arvore, deployados no engine
  real. O teste so completa as external tasks de publicacao/notificacao (sem worker de dominio) para
  a instancia chegar em `UT_TratarEscalonamento`; o grupo e o que o ENGINE resolveu da DMN.

A ancora escalacao -> caso staff e a porta bloqueada da T1.6: aqui ela e um dicionario do teste,
declarado como tal. Nenhum grant e publicado aqui (isso e a C1).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.integration.conftest import _apply_migrations, _pg_reachable

from maezo.gateway.audit_postgres import normalize_dsn
from maezo.gateway.external_cases.models import Identity
from maezo.gateway.staff_cases.case_issuer import CaseIssuerError, visible_cases
from maezo.gateway.staff_cases.case_issuer_sources import (
    EngineEscalationSource,
    LiveEscalation,
    PostgresStaffGranteeSource,
)
from maezo.portal.api.records import MembershipRecord
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
BPMN = REPO / "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn"
DMN = REPO / "spec/processes/dmn/escalation_routing.dmn"
ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_test"
TOPICS = ("operadora.events.publish", "operadora.escalation.notify_team")


def record(name: str, *groups: str, tenant: str = "amh", **changes: Any) -> MembershipRecord:
    value: dict[str, Any] = dict(
        tenant=tenant,
        issuer=ISSUER,
        subject=f"subject-{name}",
        principal_ref=f"principal-{name}",
        revision=3,
        audience="staff",
        memberships=(MembershipBinding(membership_ref=f"m-{name}", roles=("staff",), groups=groups),),
        subject_bindings=(),
        reviewed_until=datetime.now(UTC) + timedelta(days=1),
        revoked=False,
    )
    value.update(changes)
    return MembershipRecord(**value)


_DEFAULT_PORT = "5433"  # docker-compose.yml: ports ["${MAEZO_PG_HOST_PORT:-5433}:5432"]


def _pg_dsn() -> str:
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", _DEFAULT_PORT)
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


@pytest.fixture
def pg_schema() -> Iterator[tuple[str, str]]:
    """Schema descartavel por teste com as migracoes REAIS (inclui a 0012). Skip alto sem banco."""
    dsn = _pg_dsn()
    if not _pg_reachable(dsn):
        pytest.skip(f"COULD NOT VERIFY: Postgres inalcancavel em {dsn!r} (MAEZO_TEST_DATABASE_URL)")
    schema = "t16_" + uuid.uuid4().hex[:12]

    async def _schema(sql: str) -> None:
        connection = await asyncpg.connect(normalize_dsn(dsn))
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    asyncio.run(_schema(f'CREATE SCHEMA "{schema}"'))
    try:
        _apply_migrations(dsn, schema)
        yield dsn, schema
    finally:
        asyncio.run(_schema(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture
async def pg(pg_schema: tuple[str, str]) -> AsyncIterator[tuple[AsyncEngine, str]]:
    dsn, schema = pg_schema
    url = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    try:
        yield engine, schema
    finally:
        await engine.dispose()


async def insert(engine: AsyncEngine, schema: str, *records: MembershipRecord, **override: str) -> None:
    async with engine.begin() as connection:
        for r in records:
            await connection.execute(
                text(
                    f'INSERT INTO "{schema}".portal_memberships '
                    "(tenant, issuer, subject, principal_ref, payload) "
                    "VALUES (:tenant, :issuer, :subject, :principal_ref, :payload)"
                ),
                dict(
                    tenant=override.get("tenant", r.tenant),
                    issuer=override.get("issuer", r.issuer),
                    subject=r.subject,
                    principal_ref=override.get("principal_ref", r.principal_ref),
                    payload=r.model_dump_json(),
                ),
            )


async def test_grantees_are_the_current_staff_memberships_of_the_tenant(pg: tuple[AsyncEngine, str]) -> None:
    engine, schema = pg
    await insert(
        engine,
        schema,
        record("a", "plantao-clinico"),
        record("b", "enfermagem-triagem"),
        record("c", "atendimento-humano"),
        record("r", "plantao-clinico", revoked=True),
        record("e", "plantao-clinico", reviewed_until=datetime.now(UTC) - timedelta(seconds=1)),
        record("o", "plantao-clinico", tenant="outra"),
        record(
            "ben",
            "plantao-clinico",
            audience="beneficiary",
            subject_bindings=(SubjectBinding(kind="beneficiary", resource_ref="ben-1"),),
        ),
    )
    grantees = await PostgresStaffGranteeSource(engine, tenant="amh", schema=schema)()
    assert {(g.principal_ref, tuple(sorted(g.groups))) for g in grantees} == {
        ("principal-a", ("plantao-clinico",)),
        ("principal-b", ("enfermagem-triagem",)),
        ("principal-c", ("atendimento-humano",)),
    }


async def test_row_that_disagrees_with_its_payload_is_refused(pg: tuple[AsyncEngine, str]) -> None:
    engine, schema = pg
    await insert(engine, schema, record("a", "plantao-clinico"), issuer="https://intruso.test")
    with pytest.raises(CaseIssuerError):
        await PostgresStaffGranteeSource(engine, tenant="amh", schema=schema)()


def identity(n: int) -> Identity:
    return Identity(
        upstream_resource_key=f"guide-{n}",
        case_ref=f"case_{n:012d}",
        process_instance_ref=f"auth-instance-{n}",
        process_definition_id="SP-OP-AUTH-001:3:def",
        process_definition_key="SP-OP-AUTH-001",
        process_definition_version="3",
        process_definition_digest="a" * 64,
        kind="authorization",
    )


async def _drive_to_user_task(client: httpx.AsyncClient, instances: set[str]) -> None:
    worker = "t16-" + uuid.uuid4().hex[:8]
    for _ in range(40):
        response = await client.post(
            "/external-task/fetchAndLock",
            json={
                "workerId": worker,
                "maxTasks": 20,
                "topics": [{"topicName": topic, "lockDuration": 30000} for topic in TOPICS],
            },
        )
        response.raise_for_status()
        for task in response.json():
            if task["processInstanceId"] in instances:
                done = await client.post(f"/external-task/{task['id']}/complete", json={"workerId": worker})
                done.raise_for_status()
            else:
                await client.post(f"/external-task/{task['id']}/unlock")
        tasks = (
            await client.get(
                "/task", params={"taskDefinitionKey": "UT_TratarEscalonamento", "maxResults": "500"}
            )
        ).json()
        if instances <= {t["processInstanceId"] for t in tasks}:
            return
    raise AssertionError("as escalacoes nao chegaram em UT_TratarEscalonamento")


async def _deploy(client: httpx.AsyncClient, tenant: str | None) -> None:
    files = [(p.name, (p.name, p.read_bytes(), "application/xml")) for p in (BPMN, DMN)]
    data = {
        "deployment-name": f"t16-staff-case-issuer-{tenant or 'sem-tenant'}",
        "enable-duplicate-filtering": "true",
    }
    if tenant is not None:
        data["tenant-id"] = tenant
    deployed = await client.post("/deployment/create", files=files, data=data)
    assert deployed.status_code in (200, 201), deployed.text[:300]


async def _start(
    client: httpx.AsyncClient, *, tenant: str | None, variable_tenant: str, motivo: str, severidade: str
) -> str:
    conversation = f"t16-{uuid.uuid4().hex[:10]}"
    variables = {
        "tenant_id": variable_tenant,
        "source_agent_id": "helena",
        "source_agent_version": "qa-1.0",
        "conversation_id": conversation,
        "beneficiario_pseudo_id": "PSEUDO-TESTE-001",
        "canal": "whatsapp",
        "motivo_categoria": motivo,
        "severidade": severidade,
        "resumo_contexto": "sintese sintetica",
    }
    if tenant is None:
        (definition,) = (
            await client.get(
                "/process-definition",
                params={"key": "SP-OP-ESCALATION-001", "withoutTenantId": "true", "latestVersion": "true"},
            )
        ).json()
        path = f"/process-definition/{definition['id']}/start"
    else:
        path = f"/process-definition/key/SP-OP-ESCALATION-001/tenant-id/{tenant}/start"
    response = await client.post(
        path,
        json={
            "businessKey": f"ESC-{variable_tenant}-{conversation}",
            "variables": {k: {"value": v, "type": "String"} for k, v in variables.items()},
        },
    )
    assert response.status_code == 200, response.text[:300]
    return str(response.json()["id"])


async def _task(client: httpx.AsyncClient, instance: str) -> str:
    (task,) = (await client.get("/task", params={"processInstanceId": instance})).json()
    return str(task["id"])


async def test_live_escalations_carry_the_group_the_engine_resolved_from_the_dmn(
    engine_client: httpx.AsyncClient, pg: tuple[AsyncEngine, str]
) -> None:
    client = engine_client
    await _deploy(client, "amh")
    await _deploy(client, None)
    started: dict[str, str] = {}
    try:
        for label, tenant, variable, motivo, severidade in (
            ("p1", "amh", "amh", "red_flag_clinico", "grave"),
            ("enf", "amh", "amh", "red_flag_clinico", "moderada"),
            ("p3", "amh", "amh", "solicitacao_humano", "leve"),
            ("variavel_divergente", "amh", "outra", "red_flag_clinico", "grave"),
            ("sem_tenant_nativo", None, "amh", "red_flag_clinico", "grave"),
            ("link_trocado", "amh", "amh", "red_flag_clinico", "moderada"),
            ("link_extra", "amh", "amh", "solicitacao_humano", "leve"),
        ):
            started[label] = await _start(
                client, tenant=tenant, variable_tenant=variable, motivo=motivo, severidade=severidade
            )
        await _drive_to_user_task(client, set(started.values()))

        # Adulteracao pelo engine-rest: o grupo candidato deixa de ser o que a DMN registrou.
        swapped = await _task(client, started["link_trocado"])
        for action, group in (("delete", "enfermagem-triagem"), ("", "plantao-clinico")):
            response = await client.post(
                f"/task/{swapped}/identity-links" + (f"/{action}" if action else ""),
                json={"groupId": group, "type": "candidate"},
            )
            assert response.status_code == 204, response.text[:200]
        extra = await _task(client, started["link_extra"])
        response = await client.post(
            f"/task/{extra}/identity-links", json={"groupId": "plantao-clinico", "type": "candidate"}
        )
        assert response.status_code == 204, response.text[:200]

        anchors = {started["p1"]: identity(1), started["enf"]: identity(2), started["p3"]: identity(3)}

        def anchor(live: LiveEscalation) -> Identity | None:
            return anchors.get(live.escalation_ref)

        source = EngineEscalationSource(client, tenant="amh", anchor=anchor)
        live = {e.escalation_ref: e for e in await source.live() if e.escalation_ref in started.values()}
        by_label = {label: live[iid].grupo_atendimento for label, iid in started.items() if iid in live}
        # So as tres coerentes passam; divergencia de tenant, tenant ausente e link adulterado recusam.
        assert by_label == {
            "p1": "plantao-clinico",
            "enf": "enfermagem-triagem",
            "p3": "atendimento-humano",
        }
        assert source.report.refused >= 3 and source.report.foreign >= 1

        routed = [e for e in await source() if e.escalation_ref in started.values()]
        engine, schema = pg
        await insert(
            engine,
            schema,
            record("a", "plantao-clinico"),
            record("b", "enfermagem-triagem"),
            record("c", "atendimento-humano"),
        )
        grantees = {
            g.principal_ref: g
            for g in await PostgresStaffGranteeSource(engine, tenant="amh", schema=schema)()
        }
        # N3 de ponta a ponta: DMN real no engine real + membership real no Postgres.
        assert set(visible_cases(routed, grantees["principal-a"])) == {identity(1).case_ref}
        assert set(visible_cases(routed, grantees["principal-b"])) == {identity(2).case_ref}
        assert set(visible_cases(routed, grantees["principal-c"])) == {identity(3).case_ref}
    finally:
        for instance in started.values():
            await client.delete(f"/process-instance/{instance}", params={"skipCustomListeners": "true"})
