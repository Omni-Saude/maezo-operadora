"""T1.6 — as portas D-H do emissor contra PostgreSQL 17 e CIB Seven REAIS.

* Banco: o `engine-native-install.sql` real (T1.4) instalado num banco descartavel pelo harness da
  T1.4 (`_with_env`, `MAEZO_TEST_DATABASE_URL` superusuario de um servidor descartavel). O emissor
  fala com o login REAL `maezo_native_case_issuer`: ledger CAS, grantees de `amh.portal_memberships`
  (so as colunas concedidas) e a reivindicacao AUTH (so `tenant_`, `instance_`, `case_`).
* Engine: SP-OP-ESCALATION-001 + DMN reais; a instancia AUTH e um BPMN minimo com o MESMO id
  `SP-OP-AUTH-001`, deployado em memoria no tenant (nunca escrito em `spec/`). O digest da definicao
  que a ancora calcula e conferido contra os bytes deployados.

Sem banco ou sem engine: SKIP explicito ("COULD NOT VERIFY"), nunca passe silencioso.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.integration.gateway.test_staff_case_issuer_live import _deploy, _drive_to_user_task, record
from tests.unit.deploy.test_engine_native_install_pg import Env, _with_env
from tests.unit.gateway.test_staff_case_issuer import ESCALATIONS, SCOPE, A, B, C, run, world

from maezo.gateway.staff_cases.case_issuer import CaseIssuerError, StaffCaseIssuerJob
from maezo.gateway.staff_cases.case_issuer_sources import (
    AuthClaimAnchor,
    EngineEscalationSource,
    PostgresIssuerLedger,
    PostgresStaffGranteeSource,
)

pytestmark = pytest.mark.integration
ISSUER_LOGIN = "maezo_native_case_issuer"


def _pg_dsn() -> str:
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    return f"postgresql://maezo:maezo@localhost:{os.environ.get('MAEZO_PG_HOST_PORT', '5433')}/maezo"


def _need_db() -> None:
    if not os.environ.get("MAEZO_TEST_DATABASE_URL"):
        pytest.skip("COULD NOT VERIFY: MAEZO_TEST_DATABASE_URL (PG 17 descartavel) ausente")


def _engine(env: Env, login: str) -> AsyncEngine:
    url = make_url(env.dsn).set(
        drivername="postgresql+asyncpg", username=login, password=env.passwords[login], database=env.database
    )
    return create_async_engine(url, hide_parameters=True)


async def _installed(env: Env) -> None:
    # Desde o #495 o script de instalacao ja cria as tabelas AUTH e o grant de coluna do emissor.
    await env.run_all()


# ---------------------------------------------------------------- D-H.3: ledger


async def test_ledger_is_durable_cas_and_one_row_per_policy_ref() -> None:
    _need_db()

    async def check(env: Env) -> None:
        await _installed(env)
        engine = _engine(env, ISSUER_LOGIN)
        try:
            w = world()
            ledger = PostgresIssuerLedger(engine, scope=SCOPE, policy_ref=w.policy.policy_ref)
            job, sent = run(w, ESCALATIONS, (A, B, C), ledger)  # type: ignore[arg-type]
            first = await job.run_once()
            assert first.published == len(sent) > 0

            # Duravel: outra instancia (outro processo) le o MESMO estado; a 2a rodada nao publica.
            again = PostgresIssuerLedger(engine, scope=SCOPE, policy_ref=w.policy.policy_ref)
            state = await again.load()
            assert state.source_revision == len(sent) and state.pending is None
            job2, sent2 = run(w, ESCALATIONS, (A, B, C), again)  # type: ignore[arg-type]
            assert (await job2.run_once()).published == 0 and sent2 == []

            # CAS: duas copias carregadas; a segunda escrita perde, nada e sobrescrito.
            x = PostgresIssuerLedger(engine, scope=SCOPE, policy_ref=w.policy.policy_ref)
            y = PostgresIssuerLedger(engine, scope=SCOPE, policy_ref=w.policy.policy_ref)
            await x.load()
            await y.load()
            await x.begin(b'{"a":"1"}')
            with pytest.raises(CaseIssuerError, match="ledger_conflict"):
                await y.begin(b'{"a":"1"}')
            assert (await y.load()).pending == b'{"a":"1"}'
            with pytest.raises(CaseIssuerError, match="pending_publication"):
                await y.begin(b'{"b":"2"}')

            # D-H.6: policy_ref novo = linha nova, revisao recomecando do zero.
            fresh = PostgresIssuerLedger(engine, scope=SCOPE, policy_ref="staff-escalation-routing@d2")
            assert (await fresh.load()).source_revision == 0
        finally:
            await engine.dispose()

        admin = await env.admin()
        try:
            rows = await admin.fetch(
                "SELECT policy_ref, revision FROM maezo_native.mzo_staff_case_issuer_ledger"
                " ORDER BY policy_ref"
            )
            assert [r["policy_ref"] for r in rows] == [
                "staff-escalation-policy",
                "staff-escalation-routing@d2",
            ]
            assert rows[0]["revision"] >= 2 * len(sent) and rows[1]["revision"] == 0
        finally:
            await admin.close()

    await _with_env(check)


async def test_pending_publication_survives_a_crash_and_is_resent_byte_identical() -> None:
    _need_db()

    async def check(env: Env) -> None:
        await _installed(env)
        engine = _engine(env, ISSUER_LOGIN)
        try:
            w = world()
            ledger = PostgresIssuerLedger(engine, scope=SCOPE, policy_ref=w.policy.policy_ref)
            calls: list[bytes] = []

            async def broken(raw: bytes) -> dict[str, Any]:
                calls.append(raw)
                raise httpx.ConnectError("queda")

            job = StaffCaseIssuerJob(
                issuer=w.issuer(),
                escalations=lambda: ESCALATIONS,
                grantees=lambda: (A,),
                witness=w.witness,
                ledger=ledger,
                publish=broken,
                clock=lambda: w.signer.clock(),
            )
            with pytest.raises(httpx.ConnectError):
                await job.run_once()
            reborn = PostgresIssuerLedger(engine, scope=SCOPE, policy_ref=w.policy.policy_ref)
            assert (await reborn.load()).pending == calls[0]
            job2, sent = run(w, ESCALATIONS, (A,), reborn)  # type: ignore[arg-type]
            resent: list[bytes] = []
            original = job2.publish

            async def spy(raw: bytes) -> Any:
                resent.append(raw)
                return await original(raw)

            job2.publish = spy
            await job2.run_once()
            assert resent[0] == calls[0]
        finally:
            await engine.dispose()

    await _with_env(check)


async def test_ledger_refuses_another_login() -> None:
    _need_db()

    async def check(env: Env) -> None:
        await _installed(env)
        engine = _engine(env, "maezo_native_issuer_witness")
        try:
            ledger = PostgresIssuerLedger(engine, scope=SCOPE, policy_ref="p", login=ISSUER_LOGIN)
            with pytest.raises(CaseIssuerError, match="ledger_login"):
                await ledger.load()
            # E o witness, com o login certo no ledger, nao tem grant nenhum (T1.4).
            own = PostgresIssuerLedger(
                engine, scope=SCOPE, policy_ref="p", login="maezo_native_issuer_witness"
            )
            with pytest.raises(Exception, match="permission denied"):
                await own.load()
        finally:
            await engine.dispose()

    await _with_env(check)


# ---------------------------------------------------------------- grantees pelo login do emissor


async def test_grantees_are_read_with_only_the_granted_columns() -> None:
    _need_db()

    async def check(env: Env) -> None:
        await _installed(env)
        app = await env.login("maezo_app")
        try:
            await app.execute("DELETE FROM amh.portal_memberships")
            for r in (record("a", "plantao-clinico"), record("b", "enfermagem-triagem")):
                await app.execute(
                    "INSERT INTO amh.portal_memberships VALUES($1,$2,$3,$4,$5)",
                    r.tenant,
                    r.issuer,
                    r.subject,
                    r.principal_ref,
                    r.model_dump_json(),
                )
        finally:
            await app.close()
        engine = _engine(env, ISSUER_LOGIN)
        try:
            grantees = await PostgresStaffGranteeSource(engine, tenant="amh", schema="amh")()
            assert {g.principal_ref for g in grantees} == {"principal-a", "principal-b"}
            assert all(g.memberships for g in grantees)
        finally:
            await engine.dispose()

    await _with_env(check)


# ---------------------------------------------------------------- D-H.1: ancora (PG + engine)

AUTH_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
                  id="Definitions_t16_auth" targetNamespace="http://maezo.health/test/t1-6-anchor">
  <bpmn:process id="SP-OP-AUTH-001" name="T1.6 anchor fixture" isExecutable="true"
                camunda:historyTimeToLive="1">
    <bpmn:startEvent id="S"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>
    <bpmn:userTask id="U"><bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:userTask>
    <bpmn:endEvent id="E"><bpmn:incoming>f2</bpmn:incoming></bpmn:endEvent>
    <bpmn:sequenceFlow id="f1" sourceRef="S" targetRef="U"/>
    <bpmn:sequenceFlow id="f2" sourceRef="U" targetRef="E"/>
  </bpmn:process>
</bpmn:definitions>
"""


async def _start(client: httpx.AsyncClient, key: str, business_key: str, variables: dict[str, str]) -> str:
    response = await client.post(
        f"/process-definition/key/{key}/tenant-id/amh/start",
        json={
            "businessKey": business_key,
            "variables": {k: {"value": v, "type": "String"} for k, v in variables.items()},
        },
    )
    assert response.status_code == 200, response.text[:300]
    return str(response.json()["id"])


def _escalation_variables() -> dict[str, str]:
    return {
        "tenant_id": "amh",
        "source_agent_id": "sla-auth",
        "source_agent_version": "qa-1.0",
        "conversation_id": "t16-" + uuid.uuid4().hex[:10],
        "beneficiario_pseudo_id": "PSEUDO-TESTE-001",
        "canal": "whatsapp",
        "motivo_categoria": "red_flag_clinico",
        "severidade": "grave",
        "resumo_contexto": "sintese sintetica",
    }


async def test_only_the_claimed_auth_instance_of_an_sla_escalation_anchors_a_case(
    engine_client: httpx.AsyncClient,
) -> None:
    _need_db()
    client = engine_client
    await _deploy(client, "amh")
    deployed = await client.post(
        "/deployment/create",
        files=[("t16-auth.bpmn", ("t16-auth.bpmn", AUTH_BPMN.encode(), "application/xml"))],
        data={"deployment-name": "t16-auth-anchor", "tenant-id": "amh", "enable-duplicate-filtering": "true"},
    )
    assert deployed.status_code in (200, 201), deployed.text[:300]
    token = uuid.uuid4().hex[:8]
    guides = {name: f"G-{token}-{name}" for name in ("ok", "sem-claim", "duas")}
    started: list[str] = []
    try:
        auth = {}
        for name, guide in guides.items():
            auth[name] = await _start(client, "SP-OP-AUTH-001", f"AUTH-amh-{guide}", {})
            started.append(auth[name])
        started.append(await _start(client, "SP-OP-AUTH-001", f"AUTH-amh-{guides['duas']}", {}))
        escalations = {
            name: await _start(
                client, "SP-OP-ESCALATION-001", f"ESC-amh-sla-auth-{guide}", _escalation_variables()
            )
            for name, guide in guides.items()
        }
        escalations["conversa"] = await _start(
            client, "SP-OP-ESCALATION-001", f"ESC-amh-conversa-{token}", _escalation_variables()
        )
        started.extend(escalations.values())
        await _drive_to_user_task(client, set(escalations.values()))

        case_ref = f"case_{token}00000000"

        async def check(env: Env) -> None:
            await _installed(env)
            admin = await env.admin()
            try:
                # A reivindicacao real e do plano humano (FKs de instalacao AUTH); o teste grava a linha
                # sem as FKs, como superusuario, so para a ancora ler pelo login real.
                await admin.execute("SET session_replication_role = replica")
                for name in ("ok", "duas"):
                    await admin.execute(
                        "INSERT INTO maezo_native.mzo_auth_guide_claim(tenant_,guide_,intake_,command_,"
                        "digest_,principal_,instance_,case_,definition_)"
                        " VALUES('amh',$1,$2,'c',$3,'p',$4,$5,'{}')",
                        guides[name],
                        "intake-" + name,
                        "d" * 64,
                        auth[name],
                        case_ref if name == "ok" else case_ref + "x",
                    )
            finally:
                await admin.close()
            engine = _engine(env, ISSUER_LOGIN)
            try:
                anchor = AuthClaimAnchor(client, engine, tenant="amh", native_schema="maezo_native")
                source = EngineEscalationSource(client, tenant="amh", anchor=anchor)
                routed = {e.escalation_ref: e for e in await source()}
            finally:
                await engine.dispose()
            (only,) = [routed[i] for i in escalations.values() if i in routed]
            assert only.escalation_ref == escalations["ok"]
            assert only.case.case_ref == case_ref and only.case.process_instance_ref == auth["ok"]
            assert only.case.process_definition_key == "SP-OP-AUTH-001"
            assert only.case.upstream_resource_key == guides["ok"]
            assert only.case.process_definition_digest == hashlib.sha256(AUTH_BPMN.encode()).hexdigest()
            assert only.grupo_atendimento == "plantao-clinico"
            reasons = source.report.reasons
            assert reasons.get("not_sla_auth", 0) >= 1
            assert reasons.get("claim_absent", 0) >= 1
            assert reasons.get("auth_instances_many", 0) >= 1

        await _with_env(check)
    finally:
        for instance in started:
            await client.delete(f"/process-instance/{instance}", params={"skipCustomListeners": "true"})


def test_fixture_membership_is_current() -> None:
    assert record("x").reviewed_until > datetime.now(UTC) + timedelta(hours=1)
