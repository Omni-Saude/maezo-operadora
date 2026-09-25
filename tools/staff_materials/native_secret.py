"""`native-secret` — o segredo `engine/native-materials` da Onda 4, montado da saida do `generate`.

Entradas, nenhuma gerada fora daqui:

* a saida do `generate` (``engine/``, ``job/`` e ``public/summary.json``);
* ``installation-root.der`` do APROVADOR (so a chave publica, regra D-F);
* o arquivo ``staff-materials-native-secret.v1`` com os fatos de implantacao que o `generate` nao
  conhece: a chave de leitura e a de comando do pacote humano (publicas), os pins do banco, os
  caminhos montados no container e os parametros do AUTH.

Saida, num diretorio NOVO 0700 fora do repositorio:

``engine-native/``  ``server.crt``, ``server.key`` e ``client-ca.p12`` (keystore e truststore do 8443)
``engine-run/``     ``portal-read-trust.json`` (`portal-read-trust.v1`, UMA chave por proposito Q2:
                    a de leitura do portal e a de publicacao do job, cada uma com o seu peer mTLS),
                    ``trust.json`` (`human-trust.v1`: comando do portal e `human-authority` do job),
                    ``portal-read-provider.json``, ``continuity-keys.json`` e ``staff/``
                    (`staff-deployment-composition.v1`, chave de resultado nativo e o PKCS12 do AUTH)
``public/native-secret-summary.json``  os digests que a admissao Q2 e o aprovador conferem

O AUTH nasce aqui (par Ed25519 novo, como o `AuthResultSigner` exige, e certificado autoassinado,
num PKCS12 cifrado com senha aleatoria) e so o SPKI dele sai no resumo. A chave privada raiz nunca
passa por aqui.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, pkcs12
from cryptography.x509.oid import NameOID
from pydantic import Field

from maezo.gateway.external_cases.models import Digest, Ref, parse, timestamp
from maezo.gateway.staff_cases.models import Closed, N, T
from maezo.portal.engine.profile import canonicalize, strict_loads

from .generate import JOB_CLIENT, JOB_KEY_FILES, REPO
from .secure_io import PRIVATE, PUBLIC, MaterialError, new_private_directory, subdirectory, write_new

# Alias curto de proposito: `key: <NomeDeClasseLongo>` casa com a regra generic-api-key do gitleaks
# (anotacao de tipo lida como segredo de alta entropia); o alias fica abaixo do tamanho minimo dela.
EdPriv = Ed25519PrivateKey
EdPub = Ed25519PublicKey

# H4 (D-N): o job publica tambem `resource` (tarefas humanas, H2). O trust so PERMITE o kind a
# chave; quem o ADMITE e a admissao Q2 assinada pelo aprovador (`resource` so com o bloco `human`,
# `AdmissionRecord`/`approver._admission_shape`). Uma admissao staff-only continua recusando.
PUBLICATION_KINDS = ["catalog-designate", "membership", "resource"]
# Janela maxima das chaves no segredo: a mesma renovacao de 14 dias da N2.
MAX_WINDOW = timedelta(days=14)
DATASOURCE_JNDI = "java:jdbc/ProcessEngine"


class ReadScope(Closed):
    tenant: Ref
    environment: Ref
    workload_ref: Ref


class PublicKeyInput(Closed):
    key_id: Ref
    workload_ref: Ref
    public_key_spki_base64: str
    peer_spki_sha256: Digest


class ReadTrustInput(Closed):
    audience: Ref
    read_deployment_ref: Ref
    read_deployment_digest: Digest
    validity_policy_ref: Ref
    validity_policy_digest: Digest
    max_envelope_seconds: N
    catalog_ref: Ref
    portal_read_key: PublicKeyInput
    publication_key_id: Ref


class HumanTrustInput(Closed):
    audience: Ref
    max_lifetime_seconds: N
    command_key: PublicKeyInput
    authority_key_id: Ref


class TablePin(Closed):
    oid: N
    owner: Ref


class MembershipSourceInput(Closed):
    dsn_file: str
    ca_file: str
    source_schema: Ref
    publisher_ref: Ref


class ProviderInput(Closed):
    admission_ref: Ref
    minimum_admission_revision: N
    native_schema: Ref
    admission_table: TablePin
    continuity_keys_file: str
    continuity_generation: N
    membership_source: MembershipSourceInput


class AuthScope(Closed):
    tenant: Ref
    environment: Ref
    engine_name: Ref
    database_incarnation: Ref
    installation_ref: Ref
    installation_revision: N


class AuthInput(Closed):
    audience: Ref
    max_lifetime_seconds: N
    timeout_seconds: N
    alias: Ref
    key_id: Ref
    issuer: Ref


class StaffInput(Closed):
    auth_scope: AuthScope
    native_role: Ref
    native_schema: Ref
    engine_schema: Ref
    relation_pins: dict[str, TablePin]
    maximum_seconds: N
    catalog_ref: Ref
    catalog_publisher_ref: Ref
    auth: AuthInput


class NativeSecretInput(Closed):
    schema_: Literal["staff-materials-native-secret.v1"] = Field(alias="schema")
    scope: ReadScope
    engine_name: Ref
    database_incarnation: Ref
    not_before: T
    not_after: T
    read_trust: ReadTrustInput
    human_trust: HumanTrustInput
    provider: ProviderInput
    staff: StaffInput


def load_input(raw: bytes, human_keys: dict[str, Any] | None = None) -> NativeSecretInput:
    """`human_keys` = `public/human-keys-summary.json` do `human-bundle keys`.

    Com ele, `read_trust.portal_read_key` e `human_trust.command_key` saem DO RESUMO (e nao podem
    vir tambem na entrada): uma so fonte para a chave publica, o `key_id`, o workload e o peer.
    """
    if human_keys is not None:
        raw = _with_human_keys(raw, human_keys)
    try:
        return parse(NativeSecretInput, raw)
    except Exception as failure:
        raise MaterialError(f"entrada do native-secret invalida: {failure}") from None


def _with_human_keys(raw: bytes, summary: dict[str, Any]) -> bytes:
    try:
        value = strict_loads(raw)
        read_trust, human_trust = value["read_trust"], value["human_trust"]
    except Exception:
        raise MaterialError("entrada do native-secret invalida") from None
    if not isinstance(summary, dict) or summary.get("schema") != "staff-materials-human-keys.v1":
        raise MaterialError("--human-keys nao e um staff-materials-human-keys.v1")
    if "portal_read_key" in read_trust or "command_key" in human_trust:
        raise MaterialError("com --human-keys, portal_read_key e command_key saem so do resumo")
    for field in ("scope", "engine_name", "database_incarnation"):
        if summary.get(field) != value.get(field):
            raise MaterialError(f"--human-keys e de outro {field}")

    def public(purpose: str, peer: str) -> dict[str, str]:
        key = summary["keys"][purpose]
        return dict(
            key_id=key["key_id"],
            workload_ref=key["workload_ref"],
            public_key_spki_base64=key["public_key_spki_base64"],
            peer_spki_sha256=summary["peers"][peer],
        )

    read_trust["portal_read_key"] = public("portal-task-read", "read")
    command = public("human-command", "command")
    # Trust.java recusa dois propositos no MESMO workload; a `human-authority` e do workload do
    # escopo (o job T1.5), entao o comando vai em `<workload>-command` (o layout do C1; medido no
    # boot da Onda 4 em 25/09, INVALID_COMMAND em Trust.<init>).
    command["workload_ref"] = command["workload_ref"] + "-command"
    human_trust["command_key"] = command
    return canonicalize(value)


@dataclass(frozen=True)
class NativeSecret:
    directory: Path
    summary: dict[str, Any]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ed25519_private(raw: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(raw, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise MaterialError("chave de assinatura precisa ser Ed25519")
    return key


def _spki(key: EdPriv | EdPub) -> bytes:
    public = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _decode_spki(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
        key = serialization.load_der_public_key(raw)
    except Exception:
        raise MaterialError("chave publica do pacote humano invalida") from None
    if not isinstance(key, Ed25519PublicKey) or _b64(raw) != value:
        raise MaterialError("chave publica do pacote humano precisa ser SPKI Ed25519 canonica")
    return raw


def _epoch(value: str) -> str:
    return str(int(timestamp(value).timestamp()))


def _auth_pkcs12(inputs: NativeSecretInput, password: str) -> tuple[bytes, str]:
    key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, inputs.staff.auth.issuer)])
    start = timestamp(inputs.not_before).replace(microsecond=0)
    end = timestamp(inputs.not_after).replace(microsecond=0)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .sign(key, None)
    )
    raw = pkcs12.serialize_key_and_certificates(
        inputs.staff.auth.alias.encode("ascii"),
        key,
        certificate,
        None,
        serialization.BestAvailableEncryption(password.encode("ascii")),
    )
    return raw, _sha256(
        certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )


def _truststore(engine: Path, human_client_ca: bytes | None) -> bytes:
    if human_client_ca is None:
        return (engine / "client-ca.p12").read_bytes()
    try:
        human = x509.load_pem_x509_certificate(human_client_ca)
    except Exception:
        raise MaterialError("CA de clientes do pacote humano invalida") from None
    generated = x509.load_pem_x509_certificate((engine / "native-client-ca.pem").read_bytes())
    return pkcs12.serialize_java_truststore(
        [
            pkcs12.PKCS12Certificate(generated, b"maezo-native-client-ca"),
            pkcs12.PKCS12Certificate(human, b"maezo-human-client-ca"),
        ],
        serialization.NoEncryption(),
    )


def build(
    materials: Path,
    root_public_key: bytes,
    inputs: NativeSecretInput,
    *,
    human_client_ca: bytes | None = None,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Devolve ({caminho relativo: bytes}, resumo publico). Nao escreve disco.

    `human_client_ca` (PEM, do `human-bundle keys`) entra no `client-ca.p12` ao lado da CA de
    clientes do `generate`: e ela que emite os certificados `read`/`command` do pacote humano.
    """
    summary = strict_loads((materials / "public" / "summary.json").read_bytes())
    engine, job = materials / "engine", materials / "job"
    try:
        root = serialization.load_der_public_key(root_public_key)
    except Exception:
        raise MaterialError("installation-root.der nao e uma chave publica DER") from None
    if not isinstance(root, Ed25519PublicKey):
        raise MaterialError("a raiz precisa ser Ed25519")
    window = timestamp(inputs.not_after) - timestamp(inputs.not_before)
    if not timedelta(0) < window <= MAX_WINDOW:
        raise MaterialError("janela das chaves invalida ou maior que 14 dias")
    if timestamp(inputs.not_after) > timestamp(summary["certificate_not_after"]):
        raise MaterialError("as chaves nao podem durar mais que os certificados do generate")
    scope = inputs.scope.wire()
    publication = _ed25519_private((job / JOB_KEY_FILES["portal-read-publication"]).read_bytes())
    authority = _ed25519_private((job / JOB_KEY_FILES["human-authority"]).read_bytes())
    job_peer = summary["client_certificate_spki_sha256"][JOB_CLIENT]
    read = inputs.read_trust.portal_read_key
    command = inputs.human_trust.command_key
    read_spki, command_spki = (
        _decode_spki(read.public_key_spki_base64),
        _decode_spki(command.public_key_spki_base64),
    )
    q2_ids = {read.key_id, inputs.read_trust.publication_key_id}
    q2_keys = {_sha256(read_spki), _sha256(_spki(publication))}
    q2_peers = {read.peer_spki_sha256, job_peer}
    # Uma chave, um key_id e um peer por proposito: o engine recusa qualquer repeticao (F8 do C1).
    if len(q2_ids) != 2 or len(q2_keys) != 2 or len(q2_peers) != 2:
        raise MaterialError("leitura e publicacao Q2 precisam de chave, key_id e peer proprios")
    if read.workload_ref != inputs.scope.workload_ref:
        raise MaterialError("a chave de leitura e do workload do escopo")
    human_ids = {command.key_id, inputs.human_trust.authority_key_id}
    if len(human_ids) != 2 or _sha256(command_spki) == _sha256(_spki(authority)):
        raise MaterialError("comando e human-authority precisam de chave e key_id proprios")

    read_trust = dict(
        schema="portal-read-trust.v1",
        scope=scope,
        engine_name=inputs.engine_name,
        database_incarnation=inputs.database_incarnation,
        audience=inputs.read_trust.audience,
        read_deployment_ref=inputs.read_trust.read_deployment_ref,
        read_deployment_digest=inputs.read_trust.read_deployment_digest,
        validity_policy_ref=inputs.read_trust.validity_policy_ref,
        validity_policy_digest=inputs.read_trust.validity_policy_digest,
        max_envelope_seconds=inputs.read_trust.max_envelope_seconds,
        public_keys=[
            dict(
                key_id=read.key_id,
                purpose="portal-task-read",
                workload_ref=read.workload_ref,
                peer_spki_sha256=read.peer_spki_sha256,
                public_key_spki_base64=read.public_key_spki_base64,
                not_before=inputs.not_before,
                not_after=inputs.not_after,
            ),
            dict(
                key_id=inputs.read_trust.publication_key_id,
                purpose="portal-read-publication",
                workload_ref=inputs.scope.workload_ref,
                peer_spki_sha256=job_peer,
                public_key_spki_base64=_b64(_spki(publication)),
                not_before=inputs.not_before,
                not_after=inputs.not_after,
                publication_kinds=PUBLICATION_KINDS,
                catalog_ref=inputs.read_trust.catalog_ref,
            ),
        ],
    )
    human_trust = dict(
        schema="human-trust.v1",
        tenant=inputs.scope.tenant,
        audience=inputs.human_trust.audience,
        engine_name=inputs.engine_name,
        max_lifetime_seconds=inputs.human_trust.max_lifetime_seconds,
        enable_synthetic_fixture=False,
        keys=[
            dict(
                id=command.key_id,
                purpose="human-command",
                workload=command.workload_ref,
                peer_spki_sha256=command.peer_spki_sha256,
                public_key_spki_base64=command.public_key_spki_base64,
                not_before=_epoch(inputs.not_before),
                not_after=_epoch(inputs.not_after),
            ),
            # A chave `human-authority` do job, amarrada ao certificado de cliente DELE.
            dict(
                id=inputs.human_trust.authority_key_id,
                purpose="human-authority",
                workload=inputs.scope.workload_ref,
                peer_spki_sha256=job_peer,
                public_key_spki_base64=_b64(_spki(authority)),
                not_before=_epoch(inputs.not_before),
                not_after=_epoch(inputs.not_after),
            ),
        ],
    )
    continuity = summary["continuity"]
    continuity_key = (engine / "portal-read-continuity-key.bin").read_bytes()
    continuity_keys = dict(
        schema="portal-read-continuity-keys.v1",
        keys=[
            dict(
                key_id=continuity["key_ref"],
                generation=inputs.provider.continuity_generation,
                secret_base64=_b64(continuity_key),
                not_before=inputs.not_before,
                not_after=inputs.not_after,
            )
        ],
    )
    provider = dict(
        schema="portal-read-provider.v1",
        root_public_key_spki_base64=_b64(root_public_key),
        root_public_key_sha256=_sha256(root_public_key),
        admission_ref=inputs.provider.admission_ref,
        minimum_admission_revision=inputs.provider.minimum_admission_revision,
        datasource_jndi=DATASOURCE_JNDI,
        native_schema=inputs.provider.native_schema,
        admission_table_oid=inputs.provider.admission_table.oid,
        admission_table_owner=inputs.provider.admission_table.owner,
        continuity_keys_file=inputs.provider.continuity_keys_file,
        membership_source=inputs.provider.membership_source.wire(),
    )

    staff = inputs.staff
    result_key = _ed25519_private((engine / "native-result-signing-key.pem").read_bytes())
    auth_password = secrets.token_urlsafe(24)
    auth_p12, auth_spki = _auth_pkcs12(inputs, auth_password)
    relation_pins = {name: pin.wire() for name, pin in staff.relation_pins.items()}
    configuration = dict(
        schema="staff-case-native-configuration.v2",
        auth_scope=staff.auth_scope.wire(),
        designation_digest=summary["designation_sha256"],
        root_key_fingerprint=_sha256(root_public_key),
        result_key_fingerprint=_sha256(_spki(result_key)),
        native_role=staff.native_role,
        native_schema=staff.native_schema,
        engine_schema=staff.engine_schema,
        relation_pins=relation_pins,
        maximum_seconds=staff.maximum_seconds,
    )
    configuration_digest = _sha256(
        canonicalize(configuration)
    )  # = StaffCaseInstallation.Configuration.digest()
    composition = dict(
        schema="staff-deployment-composition.v1",
        auth_scope=staff.auth_scope.wire(),
        designation_digest=summary["designation_sha256"],
        root_public_key=_b64(root_public_key),
        result_public_key=_b64(_spki(result_key)),
        result_private_key_file="native-result.pk8",
        native_role=staff.native_role,
        native_schema=staff.native_schema,
        engine_schema=staff.engine_schema,
        relation_pins=relation_pins,
        maximum_seconds=staff.maximum_seconds,
        catalog_anchor=dict(
            scope=scope, catalog_ref=staff.catalog_ref, publisher_ref=staff.catalog_publisher_ref
        ),
        auth=dict(
            audience=staff.auth.audience,
            max_lifetime_seconds=staff.auth.max_lifetime_seconds,
            timeout_seconds=staff.auth.timeout_seconds,
            signing_pkcs12_file="auth-signing.p12",
            signing_password_file="auth-signing.password",
            alias=staff.auth.alias,
            key_id=staff.auth.key_id,
            issuer=staff.auth.issuer,
            signing_spki_sha256=auth_spki,
        ),
        configuration_digest=configuration_digest,
    )
    read_trust_raw = canonicalize(read_trust)
    # Publico e privado em dicionarios SEPARADOS: o resumo so digere (SHA-256 de conteudo publico,
    # nao derivacao de senha) o que e publico; o privado nunca passa por hash nenhum.
    public_files = {
        "engine-native/server.crt": (engine / "native-server-certificate.pem").read_bytes(),
        "engine-native/client-ca.p12": _truststore(engine, human_client_ca),
        "engine-run/portal-read-trust.json": read_trust_raw,
        "engine-run/trust.json": canonicalize(human_trust),
        "engine-run/portal-read-provider.json": canonicalize(provider),
        "engine-run/staff/staff-composition.json": canonicalize(composition),
    }
    private_files = {
        "engine-native/server.key": (engine / "native-server-key.pem").read_bytes(),
        "engine-run/continuity-keys.json": canonicalize(continuity_keys),
        "engine-run/staff/native-result.pk8": result_key.private_bytes(
            Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        "engine-run/staff/auth-signing.p12": auth_p12,
        "engine-run/staff/auth-signing.password": auth_password.encode("ascii"),
    }
    if frozenset(private_files) != PRIVATE_OUTPUT or PRIVATE_OUTPUT & frozenset(public_files):
        raise MaterialError("particao publico/privado do native-secret divergiu de PRIVATE_OUTPUT")
    digests: dict[str, str | None] = {name: _sha256(raw) for name, raw in public_files.items()}
    digests.update(dict.fromkeys(private_files))
    files = {**public_files, **private_files}
    public = dict(
        schema="staff-materials-native-secret-summary.v1",
        scope=scope,
        trust_configuration_digest=_sha256(read_trust_raw),
        staff_native_configuration_digest=configuration_digest,
        auth_signing_spki_sha256=auth_spki,
        root_public_key_sha256=_sha256(root_public_key),
        publication_key=dict(
            key_id=inputs.read_trust.publication_key_id,
            fingerprint=_sha256(_spki(publication)),
            peer_spki_sha256=job_peer,
        ),
        authority_key=dict(key_id=inputs.human_trust.authority_key_id, fingerprint=_sha256(_spki(authority))),
        continuity=continuity,
        files=dict(sorted(digests.items())),
    )
    return files, public


PRIVATE_OUTPUT = frozenset(
    {
        "engine-native/server.key",
        "engine-run/continuity-keys.json",
        "engine-run/staff/native-result.pk8",
        "engine-run/staff/auth-signing.p12",
        "engine-run/staff/auth-signing.password",
    }
)


def write(out: Path, files: dict[str, bytes], public: dict[str, Any]) -> NativeSecret:
    root = new_private_directory(out, forbidden=(REPO,))
    directories: dict[str, Path] = {"": root}
    for name in sorted(files):
        parent = ""
        for part in name.split("/")[:-1]:
            child = f"{parent}/{part}" if parent else part
            if child not in directories:
                directories[child] = subdirectory(directories[parent], part)
            parent = child
    for name, raw in sorted(files.items()):
        write_new(root / name, raw, PRIVATE if name in PRIVATE_OUTPUT else PUBLIC)
    summary_dir = subdirectory(root, "public")
    write_new(summary_dir / "native-secret-summary.json", canonicalize(public), PUBLIC)
    return NativeSecret(root, public)
