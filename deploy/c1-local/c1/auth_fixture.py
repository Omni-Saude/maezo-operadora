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

Os payloads e as chamadas vem do nucleo compartilhado `tools.dev_syn_fixture.core` (o mesmo
do executor de dev); aqui ficam so as cercas do harness e o que vem do volume.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from datetime import timedelta

import asyncpg
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from maezo.gateway.human.auth_profile import Definition, Scope
from maezo.gateway.human.read_profile import parse_model
from tools.dev_syn_fixture import core
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
    now,
    read_text,
    save_state,
    state,
    step,
    tls_context,
    write,
)
from .engine_config import AUTH_SCOPE

PREFIX = core.PREFIX
AUDIENCE = "c1-engine-auth"  # staff.auth.audience do engine-config
#: As referencias `SYN-` do C1. A escalacao do passo `issuer` (`ESC-amh-sla-auth-{GUIDE_NUMBER}`) aponta para a guia.
REFS = core.FixtureRefs.for_label("c1", "SYN-C1GUIA1", audience=AUDIENCE)
GUIDE_NUMBER = REFS.guide_number
RESULT_KEY_ID, RESULT_ISSUER = "c1-auth-result-1", "c1-engine"  # staff.auth do engine-config
KEYS = REFS.keys


class FixtureRefusedError(core.SyntheticRefusedError):
    """A fixture recusou rodar: nao esta no Postgres descartavel do harness."""


async def _refuse_outside_harness() -> None:
    if PG_HOST != "postgres" or TENANT != "amh" or not TEST_ROOT.is_dir():
        raise FixtureRefusedError("fora do harness C1")
    REFS.check()
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


def _result_spki() -> bytes:
    from cryptography.hazmat.primitives.serialization import pkcs12

    found = sorted(ROOT.rglob("auth-signing.p12"))
    if not found:
        raise FixtureRefusedError("auth-signing.p12 ausente no volume")
    secret = (found[0].parent / "auth-signing.password").read_bytes().strip()
    _, certificate, _ = pkcs12.load_key_and_certificates(found[0].read_bytes(), secret)
    assert certificate is not None
    return certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


async def _designate(designations: list[dict]) -> None:
    owner = await asyncpg.connect(
        admin_dsn(user=OWNER_LOGIN, password_file=f"{OWNER_LOGIN}-password"), ssl=tls_context(), timeout=10
    )
    try:
        await core.designate(owner, NATIVE_SCHEMA, TENANT, designations)
    finally:
        await owner.close()


def _client() -> httpx.Client:
    context = ssl.create_default_context(cafile=str(MATERIALS / "portal/native-ca.pem"))
    context.load_cert_chain(str(AUTH_FIXTURE / "client-certificate.pem"), str(AUTH_FIXTURE / "client-key.pem"))
    return httpx.Client(base_url=f"https://{NATIVE_HOSTNAME}", verify=context, timeout=30, trust_env=False)


def main() -> None:
    asyncio.run(_refuse_outside_harness())
    start = now() - timedelta(minutes=2)
    until = now() + timedelta(hours=2)
    end = now() + timedelta(hours=3)
    scope = parse_model(Scope, AUTH_SCOPE)
    peer = read_text(AUTH_FIXTURE / "client-spki-sha256.txt")
    keys = {purpose: _key(purpose) for purpose in KEYS}
    definition = parse_model(Definition, state("auth")["definition"])
    inputs = core.build_inputs(REFS, start, until, cutover="c1-cutover")  # qualificacao do engine-config
    designations = [core.key_designation(REFS, purpose, key, peer, inputs.grants(REFS), start, end)
                    for purpose, key in keys.items()]
    # O signatario de RESULTADO do engine (PKCS12 do `native-secret`) tambem precisa da
    # designacao do dono: sem ela todo efeito AUTH e recusado no fechamento (AuthResultSigner).
    designations.append(core.result_designation(_result_spki(), RESULT_KEY_ID, RESULT_ISSUER, start, end))
    asyncio.run(_designate(designations))

    with _client() as client:
        outcome = core.publish_and_start(client, REFS, scope, definition, keys, peer, inputs,
                                         start=start, until=until, end=end, now=now)
    if outcome.failed is not None:
        step("auth-fixture", False, f"publicacao {outcome.failed} -> {outcome.status} {json.dumps(outcome.body)[:200]}")
        return
    status, body = outcome.status, outcome.body
    key = auth_business_key(tenant_id=TENANT, numero_guia_tiss=GUIDE_NUMBER)
    save_state("auth-fixture", dict(guide_number=GUIDE_NUMBER, business_key=key, start_status=status))
    step("auth-fixture", outcome.ok,
         f"publicacoes {' '.join(outcome.publications)}; auth-start -> {status} outcome={outcome.outcome} "
         f"instancia={core.find(body, 'process_instance_id')} chave esperada {key}"
         + ("" if status == 200 else f" corpo={json.dumps(body)[:200]}"))


if __name__ == "__main__":
    main()
