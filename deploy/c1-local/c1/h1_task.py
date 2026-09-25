"""Passo `h1-task` (servico `job`): H1 da D-N, a tarefa humana VIVA lida pelo Q2 no engine real.

Roda depois do `issuer` (a `UT_TratarEscalonamento` da escalacao `SYN-` esta viva, com o candidato
que a DMN `escalation_routing` escolheu). O que ele faz, na ordem:

1. renova as memberships (o job da T1.5, uma rodada) — elas valem `observation_seconds`;
2. le do banco os fatos DEPLOYADOS (definicao, DMN, bytes) e monta o catalogo v2 com a entrada da
   tarefa (form/policies sinteticos `SYN-`, o compilado de `PortalReadCommand.COMPILED`);
3. a raiz de TESTE assina a admissao Q2 revisao 2 = revisao 1 + catalogo v2 + publicador `resource` +
   bloco `human` (classificacao e politica de identidade da tarefa), e o dono a instala;
4. publica o catalogo v2 (`catalog-designate`), a evidencia da tarefa (`human-authority`
   `evidence`) e o recurso da tarefa (`resource`) pelo contrato do vetor
   `tests/fixtures/portal_read/jcs-resource-vector.json`;
5. le o Q2 como os DOIS principais (`catalog` -> `discover` team; `task`): a tarefa aparece para o
   grupo `atendimento-humano` e NAO aparece para `enfermagem-triagem`, embora o recurso conceda os
   dois (o que separa e o candidato real da tarefa).

**D11/D12 (desvios, ver README):** a chave de publicacao do job recebe o kind `resource` no trust
(`native_secret.PUBLICATION_KINDS` no `engine-config`, H4 decide o de dev), e os envelopes deste
passo sao assinados aqui, com as chaves do volume: o cliente Python (`PortalReadClient`) pina a
admissao revisao 1 no pacote humano, e a revisao 2 so existe depois do deploy. A fonte `resource`
e sintetica (`SYN-`): a real e a H2.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import ssl
from datetime import timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from cryptography.hazmat.primitives import serialization
from tools.staff_materials import approver

from .common import (
    CATALOG_REF,
    ENGINE_NAME,
    ENGINE_SCHEMA,
    ENVIRONMENT,
    INCARNATION,
    MATERIALS,
    NATIVE_HOSTNAME,
    NATIVE_SCHEMA,
    OWNER_LOGIN,
    PORTAL_WORKLOAD,
    ROOT,
    TENANT,
    TEST_ROOT,
    admin_dsn,
    iso,
    jcs,
    now,
    save_state,
    sha256,
    state,
    step,
    tls_context,
)
from .engine_config import (
    ADMISSION_REF,
    AUTHORITY_KEY_ID,
    CATALOG_PREFIX,
    HUMAN_AUDIENCE,
    PUBLICATION_KEY_ID,
    READ_AUDIENCE,
    READ_DEPLOYMENT_DIGEST,
    READ_DEPLOYMENT_REF,
)
from .seed import ISSUER, PRINCIPALS

TASK_KEY = "UT_TratarEscalonamento"
PROCESS_KEY = "SP-OP-ESCALATION-001"
DMN_KEY = "escalation_routing"
#: `PortalReadCommand.COMPILED["SP-OP-ESCALATION-001/UT_TratarEscalonamento"]` (form_key, status, inputs).
COMPILED = ("escalation", "BPMN_FORMDATA", ["resultado", "notas_resolucao"])
RESOURCE_PREFIX = f"portal-resource:{TENANT}:task:"
HUMAN_BUNDLE = ROOT / "human-materials" / "current"
IN_GROUP, OUTSIDE = "staff-c1-no-grupo", "staff-c1-outro-grupo"
SCOPE = dict(tenant=TENANT, environment=ENVIRONMENT, workload_ref=PORTAL_WORKLOAD)


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


def _key(path: Path) -> Any:
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def _sign(key: Any, outer: dict) -> dict:
    outer = dict(outer)
    outer["signature"] = base64.urlsafe_b64encode(key.sign(jcs(outer))).rstrip(b"=").decode("ascii")
    return outer


class Engine:
    """mTLS para o listener nativo (8443 via `engine-native.c1.internal:443`), com o SAN verificado."""

    def __init__(self, certificate: Path, key: Path) -> None:
        context = ssl.create_default_context(cafile=str(MATERIALS / "portal" / "native-ca.pem"))
        context.load_cert_chain(str(certificate), str(key))
        self.http = httpx.AsyncClient(verify=context, timeout=20, trust_env=False)

    async def post(self, path: str, body: dict) -> tuple[int, Any]:
        response = await self.http.post(
            f"https://{NATIVE_HOSTNAME}{path}", content=jcs(body),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            return response.status_code, json.loads(response.content)
        except ValueError:
            return response.status_code, None

    async def close(self) -> None:
        await self.http.aclose()


async def _revision(pg: asyncpg.Connection) -> int:
    return int(await pg.fetchval(f"SELECT rev_ FROM {NATIVE_SCHEMA}.mzo_human_tenant WHERE tenant_=$1", TENANT))


def _common() -> dict:
    return dict(
        scope=SCOPE, engine_name=ENGINE_NAME, database_incarnation=INCARNATION,
        read_deployment_ref=READ_DEPLOYMENT_REF, read_deployment_digest=READ_DEPLOYMENT_DIGEST,
    )


async def _publish(job: Engine, key: Any, pg: asyncpg.Connection, kind: str, source: dict, payload: dict):
    request = dict(
        _common(), schema="portal-read-publication.v1", publication_id="publication-" + secrets.token_hex(32),
        expected_authority_revision=str(await _revision(pg)), source=source, kind=kind, payload=payload,
    )
    issued = int(now().timestamp())
    outer = _sign(key, dict(
        schema="portal-read-envelope.v1", purpose="portal-read-publication", algorithm="Ed25519",
        audience=READ_AUDIENCE, issuer=PORTAL_WORKLOAD, tenant=TENANT, key_id=PUBLICATION_KEY_ID,
        issued_at=str(issued), expires_at=str(issued + 30), digest=sha256(jcs(request)), request=request,
    ))
    return await job.post("/maezo-human-read/v1/publications", outer)


async def _read(reader: Engine, key: Any, key_id: str, operation: str, extra: dict):
    request = dict(
        _common(), schema="portal-engine-read.v1", operation=operation,
        request_id=base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode(),
        read_context_id=base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode(), **extra,
    )
    issued = int(now().timestamp())
    outer = _sign(key, dict(
        schema="portal-read-envelope.v1", purpose="portal-task-read", algorithm="Ed25519",
        audience=READ_AUDIENCE, issuer=PORTAL_WORKLOAD, tenant=TENANT, key_id=key_id,
        issued_at=str(issued), expires_at=str(issued + 30), digest=sha256(jcs(request)), request=request,
    ))
    return await reader.post(f"/maezo-human-read/v1/{operation}", outer)


def _principal(name: str) -> dict:
    subject, group = PRINCIPALS[name]
    return dict(
        schema_version="1", principal_ref=name, issuer=ISSUER, subject=subject, tenant=TENANT,
        membership_revision="1",
        memberships=[dict(membership_ref=f"{name}-m1", roles=["atendente"], groups=[group])],
        session_ref=f"SYN-session-{name}", authenticated_at=iso(now() - timedelta(seconds=5)), subject_bindings=[],
    )


async def _facts(pg: asyncpg.Connection) -> dict:
    s = ENGINE_SCHEMA
    task = await pg.fetchrow(
        f"SELECT t.id_, t.rev_, t.proc_def_id_ FROM {s}.act_ru_task t JOIN {s}.act_re_procdef d ON d.id_=t.proc_def_id_ "
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


def _catalog(facts: dict) -> tuple[dict, dict]:
    d, m = facts["definition"], facts["dmn"]
    form_key, status, inputs = COMPILED
    form = _artifact(f"read-form-{form_key}")
    form = dict(form, artifact_ref=form_key)
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
    artifact = dict(
        schema="portal-read-catalog.v1", catalog_ref=CATALOG_REF, publisher_ref=PORTAL_WORKLOAD, entries=[entry],
        policies=[_artifact(p) for p in POLICIES], forms=[form], deployment_receipt_ref="SYN-c1-h1-deployment",
        deployment_receipt_digest=sha256(jcs([entry])),
    )
    return entry, artifact


def _human(entry: dict) -> dict:
    return dict(entries=[dict(
        process_definition_id=entry["process_definition_id"], task_definition_key=TASK_KEY,
        classification=CLASSIFICATION, identity_policy=_pin("opaque-task-id-policy"),
        # Medido no C1 (25/09): a imagem de Dockerfile.human gera ids UUID (nao o DbIdGenerator do standalone).
        task_id_format="uuid", candidate_groups=entry["group_domain"]["groups"], user_candidates="refused",
    )])


async def _admission_rev2(catalog_digest: str, human: dict) -> tuple[dict, str]:
    owner = await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )
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
        record["catalog"]["catalog_digest"] = catalog_digest
        record["publishers"].append(dict(kind="resource", publisher_ref=PORTAL_WORKLOAD, source_ref_prefix=RESOURCE_PREFIX))
        record["human"] = human
        record_raw = jcs(record)
        _, shown, _ = approver.review_admission(record_raw)  # o espelho do shape Java, com o bloco human
        root = approver.load_root(TEST_ROOT / "installation-root-key.pem")
        signature = base64.b64decode(approver.sign_admission(record_raw, root, confirm_digest=shown))
        await owner.execute(
            f"INSERT INTO {NATIVE_SCHEMA}.mzo_portal_read_admission(admission_ref_,revision_,record_,signature_) "
            "VALUES($1,$2,$3,$4)", ADMISSION_REF, revision, record_raw, signature,
        )
        return record, shown
    finally:
        await owner.close()


async def main_async() -> None:
    from . import publish

    # 1. memberships frescas (o job da T1.5, uma rodada, ANTES da revisao 2 trocar o catalogo admitido)
    engine_state = state("engine")
    os.environ["MAEZO_HUMAN_MATERIAL_VERSION_ID"] = engine_state["human_version"]
    os.environ["MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256"] = engine_state["human_manifest_sha256"]
    # O ledger do job fica atras da revisao depois do `issuer`; renovar e melhor-esforco (as memberships
    # do `publish` valem observation_seconds e este passo roda logo depois).
    # Melhor-esforco: depois da revisao 2 o job recusa (o catalogo admitido mudou) e o ledger fica
    # atras da revisao do `issuer`; por isso o run.sh roda este passo LOGO depois do `issuer`, com as
    # memberships do `publish` ainda dentro de observation_seconds.
    code, out, err = publish.run_once(str(publish.JOB / "config.json"))
    renewed = (code, (out or err)[:120])
    pg = await asyncpg.connect(admin_dsn(), ssl=tls_context(), timeout=10)
    job = Engine(MATERIALS / "job" / "job-client-certificate.pem", MATERIALS / "job" / "job-client-key.pem")
    reader = Engine(HUMAN_BUNDLE / "read-client-certificate.pem", HUMAN_BUNDLE / "read-client-key.pem")
    publication_key = _key(MATERIALS / "job" / "publication-signing-key.pem")
    authority_key = _key(MATERIALS / "job" / "authority-signing-key.pem")
    read_key = _key(HUMAN_BUNDLE / "read-signing-key.pem")
    read_key_id = engine_state["read_key_id"]
    detail: list[str] = [f"renovacao rc={renewed[0]} {renewed[1]}"]
    try:
        facts = await _facts(pg)
        task_id = facts["task"]["id_"]
        detail.append(f"tarefa {task_id} candidatos={facts['groups']}")
        # 2-3. catalogo v2 + admissao revisao 2 (a raiz de TESTE)
        entry, artifact = _catalog(facts)
        catalog_digest = sha256(jcs(artifact))
        record, admission_digest = await _admission_rev2(catalog_digest, _human(entry))
        detail.append(f"admissao rev{record['admission_revision']} {admission_digest[:12]}")
        observation = int(record["observation_seconds"])
        # 4a. catalogo v2
        observed = now() - timedelta(seconds=1)
        until = observed + timedelta(hours=6)
        catalog_revision = str(1 + await pg.fetchval(
            f"SELECT revision_ FROM {NATIVE_SCHEMA}.mzo_portal_read_designation WHERE tenant_=$1 AND catalog_=$2",
            TENANT, CATALOG_REF))
        designation = dict(
            catalog_ref=CATALOG_REF, catalog_revision=catalog_revision, catalog_digest=catalog_digest,
            catalog_artifact_base64=base64.b64encode(jcs(artifact)).decode(),
            deployment_receipt_ref=artifact["deployment_receipt_ref"],
            deployment_receipt_digest=artifact["deployment_receipt_digest"], valid_until=iso(until),
        )
        source = dict(publisher_ref=PORTAL_WORKLOAD, source_ref=CATALOG_PREFIX + catalog_revision,
                      source_revision=catalog_revision,
                      source_digest=sha256(jcs(designation)), receipt_ref=f"SYN-catalog-{CATALOG_REF}@{catalog_revision}",
                      observed_at=iso(observed), valid_until=iso(until))
        code, body = await _publish(job, publication_key, pg, "catalog-designate", source, designation)
        detail.append(f"catalogo v2 -> {code}")
        if code != 200:
            raise RuntimeError(f"catalog-designate {code} {body}")
        # 4b. evidencia da tarefa (human-authority `evidence`)
        evidence = dict(
            schema="human-authority.v1", tenant=TENANT, workload_ref=PORTAL_WORKLOAD, operation="evidence",
            expected_revision=str(await _revision(pg)), task_id=task_id,
            process_definition_id=entry["process_definition_id"], evidence_ref=f"SYN-evidence-{task_id}",
            evidence_digest=sha256(f"SYN evidence {task_id}".encode()),
            valid_until=str(int((now() + timedelta(hours=6)).timestamp())),
        )
        issued = int(now().timestamp())
        outer = _sign(authority_key, dict(
            schema="human-envelope.v1", purpose="human-authority", algorithm="Ed25519", audience=HUMAN_AUDIENCE,
            issuer=PORTAL_WORKLOAD, tenant=TENANT, key_id=AUTHORITY_KEY_ID, issued_at=str(issued),
            expires_at=str(issued + 30), digest=sha256(jcs(evidence)), command=evidence,
        ))
        code, body = await job.post("/maezo-human/v1/authority", outer)
        detail.append(f"evidencia -> {code}")
        if code != 200:
            raise RuntimeError(f"evidence {code} {body}")
        ev = await pg.fetchrow(f"SELECT rev_, ref_, digest_ FROM {NATIVE_SCHEMA}.mzo_human_evidence "
                               "WHERE tenant_=$1 AND task_=$2", TENANT, task_id)
        rev = await pg.fetchval(f"SELECT rev_ FROM {ENGINE_SCHEMA}.act_ru_task WHERE id_=$1", task_id)
        # 4c. recurso da tarefa: o recurso concede OS DOIS principais (revisao acima da ja publicada)
        prior = await pg.fetchval(
            f"SELECT (source_::jsonb->>'source_revision')::bigint FROM {NATIVE_SCHEMA}.mzo_portal_read_resource "
            "WHERE tenant_=$1 AND task_=$2", TENANT, task_id)
        resource_revision = str((prior or 0) + 1)
        valid = now() + timedelta(hours=6)
        grants = []
        for name in (IN_GROUP, OUTSIDE):
            subject, _ = PRINCIPALS[name]
            grants.append(dict(
                issuer=ISSUER, subject=subject, principal_ref=name, membership_revision="1", consent_scopes=[],
                decision_receipt_ref=f"SYN-decision-{task_id}-{name}", decision_digest=sha256(f"SYN {name}".encode()),
                valid_until=iso(valid),
            ))
        payload = dict(
            task_id=task_id, process_definition_id=entry["process_definition_id"],
            process_definition_digest=entry["process_definition_digest"], observed_task_revision=str(rev),
            evidence_ref=ev["ref_"], evidence_revision=str(ev["rev_"]), evidence_digest=ev["digest_"],
            resource_ref=f"SYN-resource-{task_id}", resource_revision=resource_revision, resource_digest=sha256(b"SYN resource"),
            resource_policy=_pin("resource-policy"), classification=dict(CLASSIFICATION, valid_until=iso(valid)),
            required_subject_bindings=[], required_consent_scopes=[], positive_grants=grants,
            read_only_evidence=None, state="complete", valid_until=iso(valid),
        )
        observed = now() - timedelta(seconds=1)
        source = dict(
            publisher_ref=PORTAL_WORKLOAD, source_ref=RESOURCE_PREFIX + task_id, source_revision=resource_revision,
            source_digest=sha256(jcs(payload)), receipt_ref=f"portal-resource:{TENANT}:task:{task_id}@{resource_revision}",
            observed_at=iso(observed), valid_until=iso(observed + timedelta(seconds=observation)),
        )
        code, body = await _publish(job, publication_key, pg, "resource", source, payload)
        detail.append(f"recurso -> {code}")
        if code != 200:
            raise RuntimeError(f"resource {code} {body}")
        # 5. Q2 como os dois principais
        anchor = dict(scope=SCOPE, catalog_ref=CATALOG_REF, publisher_ref=PORTAL_WORKLOAD)
        seen: dict[str, Any] = {}
        for name in (IN_GROUP, OUTSIDE):
            code, catalog = await _read(reader, read_key, read_key_id, "catalog", dict(anchor=anchor))
            if code != 200:
                raise RuntimeError(f"catalog {code} {catalog}")
            code, team = await _read(reader, read_key, read_key_id, "discover", dict(
                principal=_principal(name), expectation=catalog["value"]["expectation"], queue="team",
                limit="25", after_task_id=None,
            ))
            seen[name] = (code, (team or {}).get("value", {}).get("task_ids") if code == 200 else team)
        code, task = await _read(reader, read_key, read_key_id, "task", dict(anchor=anchor, task_id=task_id))
        groups = task["value"]["task"]["snapshot"]["eligible_candidate_groups"] if code == 200 else task
        detail.append(f"team[{IN_GROUP}]={seen[IN_GROUP]} team[{OUTSIDE}]={seen[OUTSIDE]} task->{code} {groups}")
        ok = (
            seen[IN_GROUP][0] == 200 and task_id in seen[IN_GROUP][1]
            and seen[OUTSIDE][0] == 200 and task_id not in seen[OUTSIDE][1]
            and code == 200 and groups == ["atendimento-humano"]
        )
        save_state("h1", dict(task_id=task_id, admission_digest=admission_digest, catalog_digest=catalog_digest))
        step("h1-task", ok, "; ".join(detail))
    except Exception as failure:  # o passo mede; a causa vai na linha
        step("h1-task", False, "; ".join(detail) + f"; {type(failure).__name__}: {str(failure)[:300]}")
    finally:
        await job.close()
        await reader.close()
        await pg.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
