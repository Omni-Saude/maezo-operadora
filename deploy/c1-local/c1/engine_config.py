"""Passo `engine-config`: o segredo `engine/native-materials` da Onda 4, pela ferramenta.

`python -m tools.staff_materials native-secret` (a funcao `native_secret.build`) monta, da saida do
`generate` e da raiz PUBLICA de TESTE: trusts (Q2 com uma chave por proposito, humano com o
`human-authority` do job), `portal-read-provider.json`, `continuity-keys.json` e a composicao staff
com o PKCS12 do AUTH. O harness so acrescenta:

engine-native/  client-ca.p12 = CA de clientes do `generate` + CA do pacote humano do job (D5, por
                causa do D6)
engine-run/     observer-dsn.txt
banco           a admissao Q2 assinada pela raiz de TESTE (`approver.sign_admission`), a designacao
                instalada (event + current) e a linha MZO_AUTH_INSTALLATION (D8: sem instalador AUTH)
/c1/human-materials/current  o pacote humano do job (human_bundle.py, D6)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from datetime import timedelta
from urllib.parse import quote

import asyncpg
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, pkcs12
from tools.staff_materials import approver, native_secret

from maezo.gateway.human.membership_publication_job import staff_catalog_artifact
from maezo.portal.engine.profile import strict_loads

from . import human_bundle
from .common import (
    ADMIN,
    AUTH_FIXTURE,
    APPROVER_OUT,
    CATALOG_REF,
    DATABASE,
    ENGINE_NAME,
    ENGINE_NATIVE,
    ENGINE_RUN,
    ENGINE_SCHEMA,
    ENVIRONMENT,
    INCARNATION,
    MATERIALS,
    MEMBERSHIP_PREFIX,
    NATIVE_HOSTNAME,
    NATIVE_SCHEMA,
    OBSERVER_LOGIN,
    OWNER_LOGIN,
    PG_HOST,
    PORTAL_WORKLOAD,
    ROOT,
    STATE,
    TENANT,
    TEST_ROOT,
    admin_dsn,
    b64,
    iso,
    jcs,
    now,
    read_text,
    save_state,
    sha256,
    state,
    step,
    tls_context,
    write,
)
from .db_native import STAFF_OWNED

ADMISSION_REF = "c1-admission"
READ_DEPLOYMENT_REF = "c1-read-deployment"
READ_DEPLOYMENT_DIGEST = sha256(b"c1 read deployment")
READ_AUDIENCE = "c1-engine-read"
HUMAN_AUDIENCE = "c1-engine-human"
CATALOG_PREFIX = f"staff-catalog:{TENANT}:"
DEPLOYMENT_RECEIPT_REF = "c1-deployment-receipt"
DEPLOYMENT_RECEIPT_DIGEST = sha256(b"c1 deployment receipt")
PUBLICATION_KEY_ID = "c1-portal-read-publication"
AUTHORITY_KEY_ID = "c1-human-authority"
AUTH_SCOPE = dict(tenant=TENANT, environment=ENVIRONMENT, engine_name=ENGINE_NAME, database_incarnation=INCARNATION,
                  installation_ref="c1-auth-installation", installation_revision="1")


def _spki_b64(key: Ed25519PrivateKey) -> str:
    return b64(key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo))


def _code_digests() -> dict[str, str]:
    # Escrito pelo run.sh: sha256sum dos dois JARs DENTRO da imagem do engine.
    lines = (STATE / "code-digests.txt").read_text(encoding="utf-8").split("\n")
    by_name = {line.split()[1]: line.split()[0] for line in lines if line.strip()}
    return dict(
        engine=by_name["/camunda/lib/maezo-human-command.jar"],
        provider=by_name["/camunda/lib/maezo-portal-read-provider.jar"],
    )


async def main_async() -> None:
    pins = state("pins")["relations"]
    summary = state("materials")["summary"]
    designation_digest = state("materials")["designation_digest"]
    engine_dir = MATERIALS / "engine"
    root = approver.load_root(TEST_ROOT / "installation-root-key.pem")
    root_spki = (TEST_ROOT / "installation-root.der").read_bytes()
    start, end = now() - timedelta(minutes=5), now() + timedelta(days=5)
    scope = dict(tenant=TENANT, environment=ENVIRONMENT, workload_ref=PORTAL_WORKLOAD)

    # --- admissao Q2 (portal-read-admission.v1), assinada pela raiz de TESTE -------------------
    catalog_raw = staff_catalog_artifact(
        catalog_ref=CATALOG_REF, publisher_ref=PORTAL_WORKLOAD,
        deployment_receipt_ref=DEPLOYMENT_RECEIPT_REF, deployment_receipt_digest=DEPLOYMENT_RECEIPT_DIGEST,
    )
    # D5/D6: o pacote humano (D6) emite os certificados dele por esta CA; o truststore do 8443 a soma.
    job_ca = human_bundle.ClientCa.new("c1 human bundle client CA (D6)", start, end)
    # Cliente mTLS da fixture sintetica AUTH (D-K.2): a mesma CA D6, um peer proprio, so no volume.
    fixture_cert, fixture_key, fixture_spki = job_ca.client("c1-auth-fixture-SYN", start, end)
    write(AUTH_FIXTURE / "client-certificate.pem", fixture_cert, 0o444)
    write(AUTH_FIXTURE / "client-key.pem", fixture_key, 0o400)
    write(AUTH_FIXTURE / "client-spki-sha256.txt", fixture_spki, 0o444)
    # O trust Q2 precisa da chave de leitura do pacote humano, que precisa da admissao (espelho):
    # gera o pacote em dois tempos com as chaves fixas.
    continuity = summary["continuity"]
    admission_times = (iso(start), iso(now() + timedelta(days=4)))

    def admission_record(trust_digest: str) -> dict:
        return dict(
            schema="portal-read-admission.v1", admission_ref=ADMISSION_REF, admission_revision="1", scope=scope,
            engine_name=ENGINE_NAME, database_incarnation=INCARNATION, read_deployment_ref=READ_DEPLOYMENT_REF,
            read_deployment_digest=READ_DEPLOYMENT_DIGEST, trust_configuration_digest=trust_digest,
            purposes=["portal-read-publication", "portal-task-read"], code_digests=_code_digests(),
            continuity_keys=[dict(key_id=continuity["key_ref"], generation="1", commitment=continuity["commitment"],
                                  not_before=iso(start), not_after=iso(end))],
            catalog=dict(catalog_ref=CATALOG_REF, publisher_ref=PORTAL_WORKLOAD, catalog_digest=sha256(catalog_raw)),
            publishers=[
                dict(kind="membership", publisher_ref=PORTAL_WORKLOAD, source_ref_prefix=MEMBERSHIP_PREFIX),
                dict(kind="catalog-designate", publisher_ref=PORTAL_WORKLOAD, source_ref_prefix=CATALOG_PREFIX),
            ],
            statement_timeout_seconds="5", observation_seconds="600",
            not_before=admission_times[0], valid_until=admission_times[1],
        )

    # 1a passada: pacote humano com admissao provisoria so para fixar chaves e certificados.
    observer_password = read_text(ADMIN / f"{OBSERVER_LOGIN}-password")
    unused = b"postgresql+asyncpg://c1_unused_%s:unused@postgres:5432/maezo"
    human_dir = ROOT / "human-materials"
    human_dir.mkdir(mode=0o700, exist_ok=True)
    server_ca = (MATERIALS / "portal" / "native-ca.pem").read_bytes()
    common = dict(
        directory=human_dir, scope=scope, engine_name=ENGINE_NAME, incarnation=INCARNATION,
        origin="https://" + NATIVE_HOSTNAME, server_spki=summary["native_server_spki_sha256"],
        server_ca_pem=server_ca, client_ca=job_ca, audience_read=READ_AUDIENCE, catalog_ref=CATALOG_REF,
        source_dsn=unused % b"source", outbox_dsn=unused % b"outbox", window=human_bundle.window(),
    )
    provisional = dict(scope=scope, engine_name=ENGINE_NAME, database_incarnation=INCARNATION,
                       read_deployment_ref=READ_DEPLOYMENT_REF, read_deployment_digest=READ_DEPLOYMENT_DIGEST,
                       runtime_admission_generation="1", capability_digest="0" * 64, observed_at=iso(start),
                       valid_until=admission_times[1], provider_ref=ADMISSION_REF, provider_revision="1")
    bundle = human_bundle.build(admission=provisional, **common)
    # O segredo nativo da Onda 4 sai da ferramenta (`native-secret`), com a chave de leitura e a de
    # comando do pacote humano; publicacao e `human-authority` sao do job (`generate`, job/).
    native_input = dict(
        schema="staff-materials-native-secret.v1", scope=scope, engine_name=ENGINE_NAME,
        database_incarnation=INCARNATION, not_before=iso(start), not_after=iso(end),
        read_trust=dict(
            audience=READ_AUDIENCE, read_deployment_ref=READ_DEPLOYMENT_REF,
            read_deployment_digest=READ_DEPLOYMENT_DIGEST, validity_policy_ref="c1-validity",
            validity_policy_digest=sha256(b"c1 validity"), max_envelope_seconds="60", catalog_ref=CATALOG_REF,
            portal_read_key=dict(key_id=bundle.key_ids["portal-task-read"], workload_ref=PORTAL_WORKLOAD,
                                 public_key_spki_base64=_spki_b64(bundle.read_key),
                                 peer_spki_sha256=bundle.read_client_spki),
            publication_key_id=PUBLICATION_KEY_ID,
        ),
        human_trust=dict(
            audience=HUMAN_AUDIENCE, max_lifetime_seconds="60",
            command_key=dict(key_id="c1-human-command", workload_ref=PORTAL_WORKLOAD + "-command",
                             public_key_spki_base64=_spki_b64(bundle.command_key),
                             peer_spki_sha256=bundle.command_client_spki),
            authority_key_id=AUTHORITY_KEY_ID,
        ),
        provider=dict(
            admission_ref=ADMISSION_REF, minimum_admission_revision="1", native_schema=NATIVE_SCHEMA,
            admission_table=dict(oid=pins["mzo_portal_read_admission"]["oid"],
                                 owner=pins["mzo_portal_read_admission"]["owner"]),
            continuity_keys_file="/run/maezo/c1/continuity-keys.json", continuity_generation="1",
            membership_source=dict(dsn_file="/run/maezo/c1/observer-dsn.txt", ca_file="/run/maezo/c1/pg-ca.pem",
                                   source_schema=TENANT, publisher_ref=PORTAL_WORKLOAD),
        ),
        staff=dict(
            auth_scope=AUTH_SCOPE, native_role="cibseven_app", native_schema=NATIVE_SCHEMA,
            engine_schema=ENGINE_SCHEMA,
            relation_pins={t: dict(oid=pins[t]["oid"], owner=pins[t]["owner"]) for t in STAFF_OWNED},
            maximum_seconds="10", catalog_ref=CATALOG_REF, catalog_publisher_ref=PORTAL_WORKLOAD,
            auth=dict(audience="c1-engine-auth", max_lifetime_seconds="60", timeout_seconds="5",
                      alias="auth-result", key_id="c1-auth-result-1", issuer="c1-engine"),
        ),
    )
    native_files, native_public = native_secret.build(
        MATERIALS, root_spki, native_secret.load_input(jcs(native_input))
    )
    native_secret.write(ROOT / "native-secret", native_files, native_public)
    record = admission_record(native_public["trust_configuration_digest"])
    record_raw = jcs(record)
    # A admissao Q2 passa pela revisao e pela assinatura do proprio `approver` (F6 corrigido).
    _, shown, _ = approver.review_admission(record_raw)
    signature = base64.b64decode(approver.sign_admission(record_raw, root, confirm_digest=shown))
    # 2a passada: o espelho exato do que o engine vai devolver (geracao = revisao = 1).
    mirrored = dict(provisional, capability_digest=sha256(record_raw))
    # Mesmas chaves: reescreve SO o read-admission; o pacote e remontado com as chaves da 1a passada.
    bundle = _rebuild(bundle, mirrored, common)

    # --- arquivos do engine: os do `native-secret`, com o truststore somando a CA do pacote humano (D6)
    for name, raw in native_files.items():
        if name == "engine-native/client-ca.p12":
            continue
        base = ENGINE_NATIVE if name.startswith("engine-native/") else ENGINE_RUN
        target = base / name.split("/", 1)[1]
        target.parent.mkdir(mode=0o755, exist_ok=True)
        os.chmod(target.parent, 0o755)
        write(target, raw, 0o400 if name in native_secret.PRIVATE_OUTPUT else 0o444)
    generate_client_ca = x509.load_pem_x509_certificate((engine_dir / "native-client-ca.pem").read_bytes())
    write(ENGINE_NATIVE / "client-ca.p12", pkcs12.serialize_java_truststore(
        [pkcs12.PKCS12Certificate(generate_client_ca, b"maezo-native-client-ca"),
         pkcs12.PKCS12Certificate(job_ca.certificate, b"c1-human-bundle-client-ca")],
        serialization.NoEncryption()), 0o444)
    write(ENGINE_RUN / "observer-dsn.txt",
          f"postgresql://{OBSERVER_LOGIN}:{quote(observer_password, safe='')}@{PG_HOST}:5432/{DATABASE}", 0o400)
    configuration_digest = native_public["staff_native_configuration_digest"]

    # --- banco: admissao, designacao instalada, instalacao AUTH (D8) ---------------------------
    designation_raw = (MATERIALS / "portal" / "designation.json").read_bytes()
    proof_raw = (APPROVER_OUT / "installation-proof.json").read_bytes()
    owner = await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )
    try:
        async with owner.transaction():
            await owner.execute(f"SET LOCAL search_path = {NATIVE_SCHEMA}")
            await owner.execute("DELETE FROM mzo_portal_read_admission WHERE admission_ref_=$1", ADMISSION_REF)
            await owner.execute(
                "INSERT INTO mzo_portal_read_admission(admission_ref_,revision_,record_,signature_) VALUES($1,1,$2,$3)",
                ADMISSION_REF, record_raw, signature,
            )
            key = (TENANT, ENVIRONMENT, ENGINE_NAME, INCARNATION)
            await owner.execute("DELETE FROM mzo_staff_case_designation_current WHERE tenant=$1", TENANT)
            await owner.execute("DELETE FROM mzo_staff_case_designation_event WHERE tenant=$1", TENANT)
            await owner.execute(
                "INSERT INTO mzo_staff_case_designation_event VALUES($1,$2,$3,$4,1,$5,$6,$7)",
                *key, designation_digest, designation_raw.decode(), proof_raw.decode(),
            )
            await owner.execute(
                "INSERT INTO mzo_staff_case_designation_current VALUES($1,$2,$3,$4,1,$5)", *key, designation_digest
            )
            ids = await owner.fetchrow(
                "SELECT d.oid::bigint AS db, n.oid::bigint AS ns FROM pg_database d, pg_namespace n "
                "WHERE d.datname=$1 AND n.nspname=$2", DATABASE, NATIVE_SCHEMA,
            )
            binding = dict(schema="human-auth-native-database.v1", database_name=DATABASE, database_oid=str(ids["db"]),
                           schema_name=NATIVE_SCHEMA, schema_oid=str(ids["ns"]), owner_role=OWNER_LOGIN,
                           runtime_role="cibseven_app")
            qualification = dict(
                schema="human-auth-installation-qualification.v1",
                definition=dict(process_key="SP-OP-AUTH-001", definition_id="c1-auth-definition-not-qualified",
                                definition_digest=sha256(b"c1"), deployment_id="c1-not-deployed",
                                input_profile="portal-auth-intake.v1", profile_digest=sha256(b"c1 profile")),
                native_code_digest=_code_digests()["engine"], source_freeze_contract_digest=sha256(b"c1 freeze"),
                cutover_ref="c1-cutover", review_receipt_ref="c1-review", runtime_qualification_ref="c1-runtime",
                valid_until=iso(now() + timedelta(days=4)),
            )
            await owner.execute("DELETE FROM mzo_auth_installation WHERE tenant_=$1", TENANT)
            await owner.execute(
                "INSERT INTO mzo_auth_installation(tenant_,incarnation_,rev_,scope_,binding_,qualification_) "
                "VALUES($1,$2,1,$3,$4,$5)",
                TENANT, INCARNATION, jcs(AUTH_SCOPE).decode(), jcs(binding).decode(), jcs(qualification).decode(),
            )
    finally:
        await owner.close()

    save_state("engine", dict(
        human_manifest_sha256=bundle.manifest_sha256, human_version=bundle.manifest.material_version_id,
        configuration_digest=configuration_digest, admission_digest=sha256(record_raw),
        authority_fingerprint=summary["job_key_fingerprints"]["human-authority"],
        publication_fingerprint=summary["job_key_fingerprints"]["portal-read-publication"],
        catalog_digest=sha256(catalog_raw),
        read_key_id=bundle.key_ids["portal-task-read"],
    ))
    step("engine-config", True,
         f"trust/Q2 trust/provider/composicao montados; admissao {sha256(record_raw)[:12]} assinada pela raiz de TESTE; "
         f"configuration_digest {configuration_digest[:12]}; designacao e AUTH instaladas")


def _rebuild(first: human_bundle.HumanBundle, admission: dict, common: dict) -> human_bundle.HumanBundle:
    """Rescreve o pacote humano com as MESMAS chaves e certificados e a admissao-espelho final."""
    import json

    from maezo.gateway.human.production_materials import HumanPublicManifest
    from maezo.gateway.human.read_credentials import ReadAdmission
    from maezo.gateway.human.read_profile import parse_model, wire
    from maezo.portal.engine.profile import canonicalize

    current = common["directory"] / "current"
    os.chmod(current, 0o700)
    files = {p.name: p.read_bytes() for p in current.iterdir()}
    # A raiz humana nao foi guardada de proposito na 1a passada; aqui ela e regerada e o manifesto
    # passa a apontar para ela (o pacote e todo local e de teste).
    root = Ed25519PrivateKey.generate()
    record = parse_model(ReadAdmission, admission)
    files["installation-root.der"] = root.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    files["read-admission.json"] = json.dumps({
        "schema": "portal-human-read-admission.v1", "record": wire(record),
        "signature": base64.b64encode(root.sign(canonicalize(wire(record)))).decode("ascii")}).encode()
    manifest = strict_loads(files.pop("manifest.json"))
    manifest["root_key_fingerprint"] = sha256(files["installation-root.der"])
    for name in ("installation-root.der", "read-admission.json"):
        manifest["files"][name] = sha256(files[name])
    parsed = parse_model(HumanPublicManifest, manifest)
    for child in current.iterdir():
        child.unlink()
    for name, raw in {**files, "manifest.json": parsed.canonical()}.items():
        (current / name).write_bytes(raw)
        os.chmod(current / name, 0o400)
    os.chmod(current, 0o500)
    first.manifest = parsed
    first.manifest_sha256 = hashlib.sha256(parsed.canonical()).hexdigest()
    return first


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
