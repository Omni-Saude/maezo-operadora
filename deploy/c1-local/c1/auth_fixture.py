"""Passo `auth-fixture` (servico `issuer`): a fixture SINTETICA do intake AUTH nativo (D-K.2).

So existe no harness do C1 (`deploy/c1-local/`), nunca na imagem. O que faz:

1. RECUSA fora do Postgres descartavel do harness: host `postgres`, schema-marcador
   `c1_local_harness` com o comentario do `db-base`, e a raiz de TESTE presente no volume;
2. gera DUAS chaves de TESTE (publicacao e start, uma por proposito) no volume — sem pacote,
   sem pin — e o dono nativo as designa em `MZO_AUTH_TRUST` para o peer mTLS da fixture
   (certificado emitido no `engine-config` pela CA D6);
3. publica em `MZO_AUTH_INPUT_HEAD`, pelo `/v1/auth-input-publication` do engine, `actor`,
   `resource_authority`, `guide` (com `numero_guia_tiss`, T1.11), `start_facts`,
   `document_policy` e o `audit_intent` da admissao. Tudo com prefixo `SYN-`; nenhum dado
   clinico, so os campos estruturais minimos (procedimento de consulta, valor simbolico);
4. envia um `human-auth-start.v1` assinado ao `/v1/auth-start`. O engine abre a instancia
   `AUTH-{tenant}-{numero_guia_tiss}` e grava a reivindicacao (`mzo_auth_guide_claim`).

DESVIO D10: o tenant fica `amh` (o da instalacao do harness e o nome do schema de membership);
o prefixo `SYN-` vai na guia e em todas as referencias da fixture.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import ssl
from datetime import datetime, timedelta

import asyncpg
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from maezo.gateway.human.auth_profile import (
    Actor,
    AuditIntent,
    AuditIntentPayload,
    Definition,
    DocumentPolicy,
    GuideIdentity,
    HumanStartCommand,
    InputPublication,
    Pin,
    ResourceAuthority,
    Scope,
    SessionBinding,
    StartFacts,
)
from maezo.gateway.human.auth_projection import projection_digest, start_variables
from maezo.gateway.human.auth_transport import AuthCredentialLease, AuthEffectCeiling, sign_request
from maezo.gateway.human.read_profile import ArtifactPin, MembershipProjection, SourceProvenance, digest, parse_model
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding
from maezo.tools.process_business_keys import auth_business_key

from .common import (
    AUTH_FIXTURE,
    HARNESS_MARKER,
    HARNESS_MARKER_SCHEMA,
    MATERIALS,
    NATIVE_HOSTNAME,
    NATIVE_SCHEMA,
    OWNER_LOGIN,
    PG_HOST,
    ROOT,
    TENANT,
    TEST_ROOT,
    admin_dsn,
    jcs,
    now,
    read_text,
    save_state,
    state,
    step,
    tls_context,
    write,
)
from .engine_config import AUTH_SCOPE

PREFIX = "SYN-"
#: A guia sintetica. A escalacao do passo `issuer` (`ESC-amh-sla-auth-{GUIDE_NUMBER}`) aponta para ela.
GUIDE_NUMBER = "SYN-C1GUIA1"
WORKLOAD = "SYN-c1-auth-fixture"
AUDIENCE = "c1-engine-auth"  # staff.auth.audience do engine-config
PUBLISHER = "SYN-c1-fixture-publisher"
SOURCE = "SYN-c1-fixture-source"
GUIDE_REF = "SYN-guide-c1-0001"
INTAKE = "SYN-intake-c1-0001"
COMMAND = "SYN-command-c1-0001"
INTENT = "SYN-intent-c1-0001"
PRINCIPAL = "SYN-provider-principal-1"
PROVIDER = "SYN-provider-1"
BENEFICIARY = "SYN-beneficiary-1"
ASSESSMENT = "SYN-assessment-1"
ISSUER = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SYNc1Fixture"
SUBJECT = "00000000-0000-4000-8000-00000000c1a1"
RESULT_KEY_ID, RESULT_ISSUER = "c1-auth-result-1", "c1-engine"  # staff.auth do engine-config
KEYS = {"human-auth-input-publication": "SYN-c1-fixture-publication", "human-auth-start": "SYN-c1-fixture-start"}


class FixtureRefusedError(RuntimeError):
    """A fixture recusou rodar: nao esta no Postgres descartavel do harness."""


async def _refuse_outside_harness() -> None:
    if PG_HOST != "postgres" or TENANT != "amh" or not TEST_ROOT.is_dir():
        raise FixtureRefusedError("fora do harness C1")
    for value in (GUIDE_NUMBER, GUIDE_REF, INTAKE, PRINCIPAL, PROVIDER, BENEFICIARY, WORKLOAD):
        if not value.startswith(PREFIX):
            raise FixtureRefusedError(f"referencia sem prefixo {PREFIX}: {value}")
    su = await asyncpg.connect(admin_dsn(), ssl=tls_context(), timeout=10)
    try:
        marker = await su.fetchval(
            "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname=$1", HARNESS_MARKER_SCHEMA
        )
    finally:
        await su.close()
    if marker != HARNESS_MARKER:
        raise FixtureRefusedError("schema-marcador do harness ausente: recusado fora do Postgres local")


def _key(purpose: str) -> Ed25519PrivateKey:
    path = AUTH_FIXTURE / f"{KEYS[purpose]}.pem"
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        write(path, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()), 0o400)
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    loaded = load_pem_private_key(path.read_bytes(), None)
    assert isinstance(loaded, Ed25519PrivateKey)
    return loaded


def _spki(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _designate(keys: dict[str, Ed25519PrivateKey], peer: str, grants: list[dict], start: datetime,
                     end: datetime) -> None:
    owner = await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )
    try:
        async with owner.transaction():
            await owner.execute(f"SET LOCAL search_path = {NATIVE_SCHEMA}")
            for purpose, key in keys.items():
                designation = dict(
                    schema="human-auth-key-designation.v1", key_id=KEYS[purpose], issuer=WORKLOAD, purpose=purpose,
                    peer_spki_sha256=peer, public_key_base64=base64.b64encode(_spki(key)).decode("ascii"),
                    not_before=_iso(start), not_after=_iso(end),
                    source_grants=grants if purpose == "human-auth-input-publication" else [],
                )
                await owner.execute("DELETE FROM mzo_auth_trust WHERE tenant_=$1 AND key_id_=$2", TENANT, KEYS[purpose])
                await owner.execute("INSERT INTO mzo_auth_trust(tenant_,key_id_,designation_) VALUES($1,$2,$3)",
                                    TENANT, KEYS[purpose], jcs(designation).decode())
            # O signatario de RESULTADO do engine (PKCS12 do `native-secret`) tambem precisa da
            # designacao do dono: sem ela todo efeito AUTH e recusado no fechamento (AuthResultSigner).
            result = _result_designation(start, end)
            await owner.execute("DELETE FROM mzo_auth_trust WHERE tenant_=$1 AND key_id_=$2", TENANT, result["key_id"])
            await owner.execute("INSERT INTO mzo_auth_trust(tenant_,key_id_,designation_) VALUES($1,$2,$3)",
                                TENANT, result["key_id"], jcs(result).decode())
    finally:
        await owner.close()


def _result_designation(start: datetime, end: datetime) -> dict:
    from cryptography.hazmat.primitives.serialization import pkcs12

    found = sorted(ROOT.rglob("auth-signing.p12"))
    if not found:
        raise FixtureRefusedError("auth-signing.p12 ausente no volume")
    secret = (found[0].parent / "auth-signing.password").read_bytes().strip()
    _, certificate, _ = pkcs12.load_key_and_certificates(found[0].read_bytes(), secret)
    assert certificate is not None
    spki = certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return dict(
        schema="human-auth-key-designation.v1", key_id=RESULT_KEY_ID, issuer=RESULT_ISSUER, purpose="human-auth-result",
        peer_spki_sha256=hashlib.sha256(spki).hexdigest(), public_key_base64=base64.b64encode(spki).decode("ascii"),
        not_before=_iso(start), not_after=_iso(end), source_grants=[],
    )


def _lease(purpose: str, key: Ed25519PrivateKey, scope: Scope, peer: str, start: datetime,
           end: datetime) -> AuthCredentialLease:
    return AuthCredentialLease(
        scope=scope, purpose=purpose, workload_ref=WORKLOAD, key_id=KEYS[purpose], audience=AUDIENCE,  # type: ignore[arg-type]
        public_key_sha256=hashlib.sha256(_spki(key)).hexdigest(), peer_spki_sha256=peer,
        not_before=start, valid_until=end, max_envelope_seconds=50, key=key, live=lambda: None,
    )


def _client() -> httpx.Client:
    context = ssl.create_default_context(cafile=str(MATERIALS / "portal/native-ca.pem"))
    context.load_cert_chain(str(AUTH_FIXTURE / "client-certificate.pem"), str(AUTH_FIXTURE / "client-key.pem"))
    return httpx.Client(base_url=f"https://{NATIVE_HOSTNAME}", verify=context, timeout=30, trust_env=False)


def _post(client: httpx.Client, path: str, raw: bytes) -> tuple[int, dict]:
    response = client.post(path, content=raw, headers={"content-type": "application/json"})
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:200]}
    return response.status_code, body


def main() -> None:
    asyncio.run(_refuse_outside_harness())
    start = now() - timedelta(minutes=2)
    until = now() + timedelta(hours=2)
    end = now() + timedelta(hours=3)
    scope = parse_model(Scope, AUTH_SCOPE)
    peer = read_text(AUTH_FIXTURE / "client-spki-sha256.txt")
    keys = {purpose: _key(purpose) for purpose in KEYS}
    definition = parse_model(Definition, state("auth")["definition"])
    cutover = "c1-cutover"  # qualificacao do engine-config

    def source(receipt: str) -> SourceProvenance:
        return SourceProvenance(publisher_ref=PUBLISHER, source_ref=SOURCE, source_revision=1,
                                source_digest=hashlib.sha256(b"SYN c1 fixture").hexdigest(),
                                receipt_ref=f"SYN-receipt-{receipt}", observed_at=start, valid_until=until)

    actor = Actor(principal_ref=PRINCIPAL, issuer=ISSUER, subject=SUBJECT, membership_revision=1, audience="provider")
    membership = MembershipProjection(
        principal_ref=PRINCIPAL, issuer=ISSUER, subject=SUBJECT, membership_revision=1, audience="provider",
        memberships=(MembershipBinding(membership_ref="SYN-membership-1", roles=("provider",), groups=()),),
        subject_bindings=(SubjectBinding(kind="provider", resource_ref=PROVIDER),), state="active",
        reviewed_until=until,
    )
    session = SessionBinding(session_ref="SYN-session-1", authenticated_at=start, session_expires_at=until,
                             authorization_until=until, session_source_revision=1,
                             session_record_digest=hashlib.sha256(b"SYN session").hexdigest())
    admitted = hashlib.sha256(b"SYN c1 admitted request").hexdigest()
    intent = AuditIntentPayload(intent_ref=INTENT, intake_or_response_ref=INTAKE, command_id=COMMAND, actor=actor,
                                admitted_digest=admitted, operation="auth.start", state="committed",
                                admitted_at=start + timedelta(seconds=1), session_binding=session)
    authority = ResourceAuthority(
        authority_ref="SYN-authority-1", actor=actor, beneficiary_ref=BENEFICIARY, provider_ref=PROVIDER,
        resource_kind="guide", resource_ref=GUIDE_REF, action="auth.start", request_ref=None,
        relationship_revision=1, consent_revision=1, grant_ref="SYN-grant-1", basis_ref="SYN-basis-1",
        legal_basis="other_qualified_basis", consent_state="not_required", state="active",
        valid_from=start, valid_until=until, source=source("authority"),
    )
    guide = GuideIdentity(
        guide_identity_ref=GUIDE_REF, source_ref=SOURCE, namespace_ref="SYN-namespace",
        source_guide_ref="SYN-source-guide-1", numero_guia_tiss=GUIDE_NUMBER, cutover_ref=cutover,
        cutover_revision=1, legacy_state="absent_at_cutover", prior_instance_id=None, prior_case_ref=None,
        source=source("guide"),
    )
    policy_pin = ArtifactPin(artifact_ref="SYN-policy-1", digest=hashlib.sha256(b"SYN policy").hexdigest())
    facts = StartFacts(
        facts_ref="SYN-facts-1", intake_ref=INTAKE, guide_identity_ref=GUIDE_REF, beneficiary_pseudo_id=BENEFICIARY,
        provider_ref=PROVIDER, procedure_code="10101012", category="consulta", character="eletivo",
        claimed_amount_cents=100, document_refs=(), requer_autorizacao=True, beneficiario_ativo=True,
        carencia_cumprida=True, documentacao_completa=True, missing_requirement_codes=(),
        documentary_assessment_ref=ASSESSMENT, request_digest=admitted, factual_sources=(source("facts"),),
        policy_artifacts=(policy_pin,),
    )
    policy = DocumentPolicy(
        assessment_ref=ASSESSMENT, resource_kind="intake", resource_ref=INTAKE, request_ref=None, request_revision=0,
        policy=policy_pin, policy_revision=1, recipient_principal_refs=(PRINCIPAL,), required_codes=(),
        missing_codes=(), submitted_response_digest=None, effective_document_refs=(), document_set_digest=digest(()),
        complete=True, source=source("policy"), valid_until=until,
    )
    inputs = [("actor", PRINCIPAL, membership, source("actor")), ("resource_authority", "SYN-authority-1", authority,
              authority.source), ("guide", GUIDE_REF, guide, guide.source), ("start_facts", "SYN-facts-1", facts,
              source("facts-head")), ("document_policy", ASSESSMENT, policy, policy.source),
              ("audit_intent", INTENT, intent, source("intent"))]
    grants = [dict(kind=kind, source_ref=SOURCE, publisher_ref=PUBLISHER, resource_ref=ref) for kind, ref, _, _ in inputs]
    asyncio.run(_designate(keys, peer, grants, start, end))

    publication_lease = _lease("human-auth-input-publication", keys["human-auth-input-publication"], scope, peer,
                               start, end)
    heads: dict[str, tuple[str, int, SourceProvenance, str]] = {}
    results = []
    with _client() as client:
        for kind, ref, payload, provenance in inputs:
            publication = InputPublication.model_validate(dict(
                schema="human-auth-input-publication.v1", scope=scope, workload_ref=WORKLOAD,
                publication_id=f"SYN-publication-{kind}", kind=kind, resource_ref=ref, expected_generation=0,
                source=provenance, state="active", payload=payload, payload_digest=digest(payload),
                valid_until=until))
            status, body = _post(client, "/maezo-human/v1/auth-input-publication", sign_request(publication, publication_lease, now()))
            generation = int(str(_find(body, "head_generation") or 0))
            results.append(f"{kind}={status}")
            heads[kind] = (ref, generation, provenance, digest(payload))
            if status != 200:
                step("auth-fixture", False, f"publicacao {kind} -> {status} {json.dumps(body)[:200]}")
                return
        pins = tuple(sorted(
            (Pin(kind=kind, resource_ref=ref, head_generation=generation, source=provenance, payload_digest=payload)
             for kind, (ref, generation, provenance, payload) in heads.items() if kind != "audit_intent"),
            key=lambda p: (p.kind, p.resource_ref)))
        command = HumanStartCommand.model_validate(dict(
            schema="human-auth-start.v1", scope=scope, workload_ref=WORKLOAD, actor=actor, intake_ref=INTAKE,
            command_id=COMMAND, admission=AuditIntent(intent_ref=INTENT, admitted_command_id=COMMAND,
                                                      admitted_digest=admitted, source=heads["audit_intent"][2]),
            guide_identity_ref=GUIDE_REF, definition=definition, input_pins=pins, start_facts_ref="SYN-facts-1",
            start_facts_digest=digest(facts),
            projected_variables_digest=projection_digest(start_variables(scope.tenant, facts))))
        ceiling = AuthEffectCeiling(command_digest=digest(command), session=session, valid_until=until)
        start_lease = _lease("human-auth-start", keys["human-auth-start"], scope, peer, start, end)
        status, body = _post(client, "/maezo-human/v1/auth-start", sign_request(command, start_lease, now(), effect_ceiling=ceiling))
    key = auth_business_key(tenant_id=TENANT, numero_guia_tiss=GUIDE_NUMBER)
    save_state("auth-fixture", dict(guide_number=GUIDE_NUMBER, business_key=key, start_status=status))
    outcome = _find(body, "outcome")
    step("auth-fixture", status == 200 and outcome in ("started", "existing"),
         f"publicacoes {' '.join(results)}; auth-start -> {status} outcome={outcome} "
         f"instancia={_find(body, 'process_instance_id')} chave esperada {key}"
         + ("" if status == 200 else f" corpo={json.dumps(body)[:200]}"))


def _find(value: object, name: str) -> object:
    """O campo `name` no primeiro nivel em que aparecer (o resultado nativo vem envelopado e assinado)."""
    if isinstance(value, dict):
        if name in value:
            return value[name]
        for item in value.values():
            found = _find(item, name)
            if found is not None:
                return found
    return None


if __name__ == "__main__":
    main()
