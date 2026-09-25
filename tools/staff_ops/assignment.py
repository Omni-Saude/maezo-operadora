"""Plano de atribuicao (Onda 8): instalacao nativa + fonte revisada ATIVA, pelo codigo do repo.

O BFF so liga o plano humano com `portal_assignment_source.state='active'`
(`AssignmentRuntime.start` -> `NativeAssignmentClient._active`). A linha NUNCA e escrita a mao: ela
nasce e muda so pelos metodos de `PostgresStaffAssignmentAdministration` (`portal/admin/assignments.py`)
e so fica `active` com o ACK do engine (`ack_native`), que so vem de uma publicacao aceita por
`AssignmentPublication.java`. Dois tempos, cada um com o seu dono:

* ``install`` (dono do schema nativo, dentro do `rows`): a linha `MZO_HUMAN_ASSIGNMENT_INSTALLATION`
  que o `AssignmentInstallation.acquire` do engine exige (OIDs medidos no banco, digest do
  `assignment-trust.json`, recibo de deploy). Recusa se o login do engine puder escrever nela.
* ``activate`` (login da administracao + a chave `human-authority` do job, na task do job):
  `prepare_change` com a revisao corrente, SEM `membership_changes`, policy, binding nem
  designacao (a 1a geracao carrega so as memberships staff revisadas de `portal_memberships`;
  claim/release/reassign continuam recusados pelo engine), assinatura pela chave da FONTE
  (`StaffAssignmentSourceSigner`) e publicacao pelo `StaffAssignmentPublisher`, que grava o pedido
  duravel, envia ao `/maezo-human/v1/authority` e so entao faz o `ack_native`.

Idempotente: `active` sem pendencia = nada muda; `frozen` = reenvia os MESMOS bytes do pedido
duravel (reconcile); ausente/`disabled` = congela e publica. Fail-closed: qualquer recusa do
engine sai 1 com o codigo publico, a linha fica como o codigo a deixou (nunca `DELETE`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .common import OpsError, b64, env, secret_json, secrets_client

NATIVE_SCHEMA = "maezo_native"
INSTALL_SCHEMA = "staff-assignment-installation.v1"
ACTIVATE_SCHEMA = "staff-assignment-activate.v1"
RESULT_SCHEMA = "staff-assignment-activate-result.v1"
JOB_CONFIG = "/run/maezo-job/config.json"
_HEX = set("0123456789abcdef")


def _digest(value: object, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise OpsError(f"{where}: esperado SHA-256 hex")
    return value


def _pin(value: object, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"artifact_ref", "digest"}:
        raise OpsError(f"{where}: esperado {{artifact_ref, digest}}")
    ref = value["artifact_ref"]
    if not isinstance(ref, str) or not ref or len(ref) > 255:
        raise OpsError(f"{where}.artifact_ref invalido")
    return {"artifact_ref": ref, "digest": _digest(value["digest"], f"{where}.digest")}


# --- install (dono do schema nativo) ------------------------------------------------------------


@dataclass(frozen=True)
class Installation:
    tenant: str
    environment: str
    engine_name: str
    database_incarnation: str
    configuration_digest: str
    deployment_receipt: dict[str, str]
    valid_until: int
    runtime_role: str


def installation_from_trust(trust_raw: bytes, *, runtime_role: str) -> Installation:
    """A linha sai do PROPRIO arquivo que o engine carrega: digest = sha256(JCS) (`AssignmentTrust.digest`)."""
    from maezo.portal.engine.profile import canonicalize, strict_loads

    trust = strict_loads(trust_raw)
    if not isinstance(trust, dict) or trust.get("schema") != "human-assignment-trust.v1":
        raise OpsError("assignment-trust.json nao e human-assignment-trust.v1")
    if canonicalize(trust) != trust_raw:
        raise OpsError("assignment-trust.json nao esta em JCS")
    return Installation(
        tenant=trust["tenant"],
        environment=trust["environment"],
        engine_name=trust["engine_name"],
        database_incarnation=trust["database_incarnation"],
        configuration_digest=hashlib.sha256(trust_raw).hexdigest(),
        deployment_receipt=_pin(trust["deployment_receipt"], "deployment_receipt"),
        valid_until=int(trust["valid_until"]),
        runtime_role=runtime_role,
    )


def parse_installation(document: dict[str, Any], *, tenant: str, runtime_role: str) -> Installation:
    """Bloco `assignment.installation` do segredo do `rows` (so partes publicas do trust)."""
    keys = {
        "schema",
        "environment",
        "engine_name",
        "database_incarnation",
        "configuration_digest",
        "deployment_receipt",
        "valid_until",
    }
    if set(document) != keys or document["schema"] != INSTALL_SCHEMA:
        raise OpsError(f"assignment.installation: esperado {INSTALL_SCHEMA} com {sorted(keys)}")
    until = document["valid_until"]
    if not isinstance(until, str) or not until.isdigit():
        raise OpsError("assignment.installation.valid_until: epoch em segundos (texto)")
    return Installation(
        tenant=tenant,
        environment=str(document["environment"]),
        engine_name=str(document["engine_name"]),
        database_incarnation=str(document["database_incarnation"]),
        configuration_digest=_digest(document["configuration_digest"], "configuration_digest"),
        deployment_receipt=_pin(document["deployment_receipt"], "deployment_receipt"),
        valid_until=int(until),
        runtime_role=runtime_role,
    )


async def install(owner: Any, spec: Installation, *, native_schema: str = NATIVE_SCHEMA) -> str:
    """Insere/realinha a linha `qualified` do tenant. Conexao asyncpg do DONO do schema nativo."""
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {native_schema}")
        ids = await owner.fetchrow(
            "SELECT d.oid::bigint AS db, n.oid::bigint AS ns, r.oid::bigint AS login FROM pg_database d, "
            "pg_namespace n, pg_roles r WHERE d.datname=current_database() AND n.nspname=$1 AND r.rolname=$2",
            native_schema,
            spec.runtime_role,
        )
        if ids is None:
            raise OpsError("schema nativo ou login do engine inexistente")
        if await owner.fetchval("SELECT 1 FROM mzo_human_tenant WHERE tenant_=$1", spec.tenant) is None:
            raise OpsError("mzo_human_tenant sem o tenant: o bootstrap do `rows` roda antes")
        # AssignmentInstallation.java recusa a instalacao que o login do engine consiga escrever.
        if await owner.fetchval(
            "SELECT has_table_privilege($1, 'mzo_human_assignment_installation', "
            "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')",
            spec.runtime_role,
        ):
            raise OpsError(
                "o login do engine escreve em mzo_human_assignment_installation (reaplique o install.sql)"
            )
        desired = dict(
            environment_=spec.environment,
            engine_name_=spec.engine_name,
            database_incarnation_=spec.database_incarnation,
            database_oid_=ids["db"],
            engine_schema_oid_=ids["ns"],
            engine_login_oid_=ids["login"],
            configuration_digest_=spec.configuration_digest,
            deployment_receipt_ref_=spec.deployment_receipt["artifact_ref"],
            deployment_receipt_digest_=spec.deployment_receipt["digest"],
            state_="qualified",
            valid_until_=spec.valid_until,
        )
        row = await owner.fetchrow(
            "SELECT environment_, engine_name_, database_incarnation_, database_oid_::bigint AS database_oid_, "
            "engine_schema_oid_::bigint AS engine_schema_oid_, engine_login_oid_::bigint AS engine_login_oid_, "
            "configuration_digest_, deployment_receipt_ref_, deployment_receipt_digest_, state_, valid_until_, "
            "revision_ FROM mzo_human_assignment_installation WHERE tenant_=$1 FOR UPDATE",
            spec.tenant,
        )
        columns = list(desired)
        if row is None:
            await owner.execute(
                f"INSERT INTO mzo_human_assignment_installation(tenant_,{','.join(columns)},revision_) "
                f"VALUES($1,{','.join(f'${i + 2}' for i in range(len(columns)))},1)",
                spec.tenant,
                *desired.values(),
            )
            return "inserida rev 1"
        if all(row[c] == v for c, v in desired.items()):
            return f"igual rev {row['revision_']}"
        # Trust novo (chaves renovadas, janela nova) = requalificacao pelo dono; o engine so aceita
        # a linha que bate com o arquivo que ele carregou.
        await owner.execute(
            f"UPDATE mzo_human_assignment_installation SET "
            f"{','.join(f'{c}=${i + 2}' for i, c in enumerate(columns))},revision_=revision_+1 WHERE tenant_=$1",
            spec.tenant,
            *desired.values(),
        )
        return f"requalificada rev {row['revision_'] + 1}"


# --- activate (administracao da fonte + chave human-authority do job) --------------------------


@dataclass(frozen=True)
class Plan:
    admin_login: str
    source_key_id: str
    source_fingerprint: str
    owner_ref: str
    source_ref: str
    owner_receipt: dict[str, str]
    valid_until: datetime


def parse_plan(document: dict[str, Any]) -> tuple[Plan, str, bytes]:
    """(plano, DSN da administracao, PEM da chave da fonte) do segredo `staff-assignment-activate.v1`."""
    keys = {
        "schema",
        "admin_dsn",
        "admin_login",
        "source_key_pem_b64",
        "source_key_id",
        "source_fingerprint",
        "owner_ref",
        "source_ref",
        "owner_receipt",
        "valid_days",
    }
    if set(document) != keys or document["schema"] != ACTIVATE_SCHEMA:
        raise OpsError(f"segredo da ativacao: esperado {ACTIVATE_SCHEMA} com {sorted(keys)}")
    days = document["valid_days"]
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 14:
        raise OpsError("valid_days: 1 a 14")
    for name in ("admin_login", "source_key_id", "owner_ref", "source_ref"):
        if not isinstance(document[name], str) or not document[name]:
            raise OpsError(f"{name} invalido")
    dsn = document["admin_dsn"]
    if not isinstance(dsn, str) or not dsn.startswith("postgresql+asyncpg://"):
        raise OpsError("admin_dsn: esperado postgresql+asyncpg://")
    plan = Plan(
        admin_login=document["admin_login"],
        source_key_id=document["source_key_id"],
        source_fingerprint=_digest(document["source_fingerprint"], "source_fingerprint"),
        owner_ref=document["owner_ref"],
        source_ref=document["source_ref"],
        owner_receipt=_pin(document["owner_receipt"], "owner_receipt"),
        valid_until=datetime.now(UTC) + timedelta(days=days),
    )
    return plan, dsn, b64(document["source_key_pem_b64"], "source_key_pem")


def source_key(pem: bytes, fingerprint: str) -> Any:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception:
        raise OpsError("chave da fonte ilegivel") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise OpsError("chave da fonte nao e Ed25519")
    spki = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    if hashlib.sha256(spki).hexdigest() != fingerprint:
        raise OpsError("chave da fonte nao e a do fingerprint do trust")
    return key


async def source_row(admin_engine: Any, tenant: str) -> dict[str, Any] | None:
    from sqlalchemy import text

    from maezo.gateway.audit_postgres import schema_for_tenant

    async with admin_engine.begin() as db:
        await db.execute(text(f'SET LOCAL search_path TO "{schema_for_tenant(tenant)}"'))
        row = (
            (
                await db.execute(
                    text(
                        "SELECT state, source_revision, native_revision, pending_publication_id, "
                        "active_generation_digest FROM portal_assignment_source WHERE tenant=:tenant"
                    ),
                    {"tenant": tenant},
                )
            )
            .mappings()
            .first()
        )
    return None if row is None else dict(row)


async def native_revision(native_engine: Any, tenant: str, native_schema: str) -> int:
    """O contador CAS do tenant no engine (`MZO_HUMAN_TENANT.REV_`), pelo login da fonte de tarefas."""
    from sqlalchemy import text

    async with native_engine.connect() as db:
        value = (
            await db.execute(
                text(f"SELECT rev_ FROM {native_schema}.mzo_human_tenant WHERE tenant_=:tenant"),
                {"tenant": tenant},
            )
        ).scalar_one_or_none()
    if value is None:
        raise OpsError("mzo_human_tenant sem o tenant")
    return int(value)


async def activate(
    plan: Plan,
    *,
    scope: Any,
    engine_name: str,
    database_incarnation: str,
    admin_engine: Any,
    native_engine: Any,
    native_schema: str,
    key: Any,
    authority: Any,
    lifetime: Any,
) -> dict[str, Any]:
    from maezo.gateway.human.assignment_publisher import StaffAssignmentPublisher
    from maezo.gateway.human.credentials import AssignmentSigningLease
    from maezo.gateway.human.errors import GatewayRefusalError
    from maezo.gateway.human.read_profile import ArtifactPin
    from maezo.portal.admin.assignments import (
        PostgresStaffAssignmentAdministration,
        StaffAssignmentSourceSigner,
    )
    from maezo.portal.engine.profile import strict_loads

    tenant = scope.tenant
    before = await source_row(admin_engine, tenant)
    if before is not None and before["state"] == "active" and before["pending_publication_id"] is None:
        return dict(
            action="ja-ativa",
            state="active",
            pending=None,
            source_revision=before["source_revision"],
            generation_digest=before["active_generation_digest"],
        )
    counter = await native_revision(native_engine, tenant, native_schema)
    owner_receipt = ArtifactPin.model_validate(plan.owner_receipt)
    administration = PostgresStaffAssignmentAdministration(
        engine=admin_engine,
        scope=scope,
        engine_name=engine_name,
        database_incarnation=database_incarnation,
        database_role=plan.admin_login,
        artifacts=(),
        approved_owner_receipts=(owner_receipt,),
        valid_until=plan.valid_until,
        initial_native_revision=counter,
    )
    lease = AssignmentSigningLease(
        scope=scope,
        key_id=plan.source_key_id,
        purpose="human-staff-assignment-source",
        audience=plan.source_ref,
        fingerprint=plan.source_fingerprint,
        not_before=datetime.now(UTC) - timedelta(seconds=1),
        not_after=plan.valid_until,
        max_envelope_seconds=30,
        _key=key,
        _live=lifetime.bounded(plan.valid_until),
    )
    signer = StaffAssignmentSourceSigner(
        administration=administration, signing=lease, owner_ref=plan.owner_ref, source_ref=plan.source_ref
    )
    publisher = StaffAssignmentPublisher(administration=administration, source=signer, client=authority)
    if before is None or before["state"] == "disabled":
        frozen = await administration.prepare_change(
            0 if before is None else before["source_revision"], (), (), (), (), owner_receipt
        )
        action = "congelada-e-publicada"
    elif before["state"] == "frozen":
        frozen = await administration.read_frozen(before["source_revision"])
        action = "reconciliada"
    else:
        raise OpsError("estado da fonte desconhecido")
    if frozen.operation != "replace":
        raise OpsError("a fonte congelada e um disable: a troca de geracao nao e desta ferramenta")
    try:
        receipt = await publisher.publish(frozen.source_revision)
    except GatewayRefusalError as refusal:
        if refusal.code != "revision_conflict":
            raise OpsError(f"engine recusou a publicacao ({refusal.code})") from None
        # O pedido duravel pina o contador do engine; se ele andou (o job T1.5 publicou entre o
        # congelamento e o envio), o engine recusa o pedido EXATO sem efeito. Rebase pelo codigo da
        # administracao (`rebase_undelivered`) e UMA nova tentativa com o contador corrente.
        current = await native_revision(native_engine, tenant, native_schema)
        await administration.rebase_undelivered(frozen.source_revision, current)
        frozen = await administration.prepare_change(frozen.source_revision, (), (), (), (), owner_receipt)
        action += f"+rebase({frozen.expected_native_revision})"
        try:
            receipt = await publisher.publish(frozen.source_revision)
        except GatewayRefusalError as again:
            raise OpsError(
                f"engine recusou a publicacao apos o rebase ({again.code}): o job publicou de novo no "
                "meio? pause o agendamento do job e rode de novo"
            ) from None
    after = await source_row(admin_engine, tenant)
    generation = strict_loads(frozen.payload)
    return dict(
        action=action,
        state=None if after is None else after["state"],
        pending=None if after is None else after["pending_publication_id"],
        source_revision=receipt.source_revision,
        authority_revision=receipt.authority_revision,
        generation_digest=receipt.generation_digest,
        memberships=int(generation["membership_count"]),
    )


async def activate_from_job(
    plan: Plan, admin_dsn: str, key_pem: bytes, *, job_config: str = JOB_CONFIG, ssl_context: Any = None
) -> dict[str, Any]:
    """Composicao na task do job: pacote humano pinado + chave `human-authority` + TLS do job."""
    import ssl

    from sqlalchemy.ext.asyncio import create_async_engine

    from maezo.gateway.human import membership_publication_job as job
    from maezo.gateway.human.assignment_transport import AssignmentPrivateTransport
    from maezo.gateway.human.models import Scope
    from maezo.gateway.human.production_materials import (
        MATERIAL_DIRECTORY,
        HumanMaterialPin,
        load_human_materials,
    )
    from maezo.gateway.human.read_materials import MaterialLifetime

    config = job.load_config(job_config)
    if config.native_source is None:
        raise OpsError("a configuracao do job nao tem native_source (contador do tenant)")
    lifetime = MaterialLifetime()
    material = load_human_materials(
        MATERIAL_DIRECTORY,
        HumanMaterialPin(
            tenant=config.tenant,
            material_version_id=env("MAEZO_HUMAN_MATERIAL_VERSION_ID"),
            public_manifest_sha256=env("MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256"),
        ),
    )
    manifest = material.manifest
    scope = Scope(
        tenant=manifest.scope.tenant,
        environment=manifest.scope.environment,
        workload_ref=manifest.scope.workload_ref,
    )
    tls = ssl_context or ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    authority = AssignmentPrivateTransport(
        origin=manifest.read_surface.origin,
        tls_context=job.job_tls_context(config, manifest.read_surface, scope, material.directory),
        server_spki_sha256=manifest.read_surface.server_spki_sha256,
        signing=job.authority_lease(config.authority, scope, lifetime),
        timeout_seconds=manifest.read_surface.timeout_seconds,
    )
    admin_engine = create_async_engine(
        admin_dsn, hide_parameters=True, pool_size=1, max_overflow=0, connect_args={"ssl": tls}
    )
    native_engine = create_async_engine(
        Path(config.native_source.dsn_file).read_text(encoding="utf-8").strip(),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        connect_args={"ssl": tls},
    )
    try:
        return await activate(
            plan,
            scope=scope,
            engine_name=manifest.engine_name,
            database_incarnation=manifest.database_incarnation,
            admin_engine=admin_engine,
            native_engine=native_engine,
            native_schema=config.native_source.native_schema,
            key=source_key(key_pem, plan.source_fingerprint),
            authority=authority,
            lifetime=lifetime,
        )
    finally:
        await authority.aclose()
        await admin_engine.dispose()
        await native_engine.dispose()
        lifetime.close()


def activate_main() -> int:
    """`python -m tools.staff_ops assignment-activate` na task do job (dev): segredo por ARN."""
    try:
        plan, dsn, pem = parse_plan(secret_json(secrets_client(), env("STAFF_ASSIGNMENT_SECRET_ARN")))
        result = asyncio.run(activate_from_job(plan, dsn, pem))
    except OpsError as failure:
        print(f"recusado: {failure}", file=sys.stderr)
        return 1
    except Exception as failure:
        print(f"falhou: {type(failure).__name__}", file=sys.stderr)
        return 1
    ok = result.get("state") == "active" and result.get("pending") is None
    print(json.dumps(dict(schema=RESULT_SCHEMA, ok=ok, **result), sort_keys=True, default=str))
    return 0 if ok else 2


__all__ = [
    "Installation",
    "Plan",
    "activate",
    "activate_from_job",
    "activate_main",
    "install",
    "installation_from_trust",
    "parse_installation",
    "parse_plan",
    "source_key",
]
