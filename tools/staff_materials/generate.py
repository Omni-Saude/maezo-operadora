"""`generate` — o lado da ENGENHARIA (plano, Onda 2). Nunca toca a raiz de instalacao.

Produz, num diretorio NOVO 0700 fora do repositorio:

``portal/``  arquivos do pacote staff do portal (11 dos 12 do manifesto; faltam
             `installation-root.der` e `installation-proof.json`, que so o aprovador produz)
``engine/``  material do listener mTLS e da autoridade nativa (segredo nativo, Onda 4); a CA de
             clientes vai em PEM e em `client-ca.p12` (truststore PKCS12 sem senha, o que o
             Tomcat 10.1/JSSE le)
``issuer/``  chaves e certificado do emissor de casos e do importador, e a chave do witness PROPRIO
             do emissor (`case-issuer-witness`, D-H.2) (T1.6)
``job/``     identidade do job de publicacao da T1.5: certificado de cliente mTLS (CA de clientes
             nativa) e as chaves PROPRIAS dos propositos `portal-read-publication` e
             `human-authority` (uma chave por proposito, F8 do C1)
``dba/``     verificadores SCRAM dos dois logins novos (Onda 3; a senha em claro so existe no DSN)
``public/summary.json``  o que o aprovador confere: digests, fingerprints e pins de SPKI

As chaves privadas das duas CAs nativas **nao sao gravadas**: existem so em memoria para emitir
os certificados. Reemitir e rodar `generate` de novo (novos pins, nova aprovacao) — o mesmo ciclo
de renovacao de 14 dias da N2.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12

from maezo.gateway.external_cases.models import digest, parse, timestamp
from maezo.gateway.staff_cases.authority import fingerprint
from maezo.gateway.staff_cases.case_issuer_runtime import WITNESS_LOGIN as ISSUER_WITNESS_LOGIN
from maezo.gateway.staff_cases.case_issuer_sources import WITNESS_ENTRY as ISSUER_WITNESS_ENTRY
from maezo.gateway.staff_cases.models import FIELDS, Designation
from maezo.portal.engine.profile import canonicalize

from . import scram
from .pki import Issued, issue_ca, issue_leaf
from .secure_io import PRIVATE, PUBLIC, MaterialError, new_private_directory, subdirectory, write_new
from .spec import MaterialsSpec

REPO = Path(__file__).resolve().parents[2]
RDS_BUNDLE = REPO / "deploy/certificates/sa-east-1-bundle.pem"
# Pin revisado em `deploy/certificates/README.md` (4.572 bytes, obtido em 2026-09-09).
RDS_BUNDLE_SHA256 = "c2f9255eadfa939dd6f965ede75d8e0d4168c9cbb7ca1e7baa9bff6d5e2c96e1"
CONTINUITY_DOMAIN = b"maezo/portal-native-read-continuity/v1/commitment"

# Capacidades por papel: fixas, nao configuraveis. O leitor do portal precisa das tres
# projecoes e exatamente de `detail`+`list`, nessa ordem: e o que o loader exige (`materials.py`,
# conferencia do `read_requester`, D-H.4) e o que `/cases` precisa. O emissor de casos NAO e
# designado para `staff_current_task.v1`: a restricao da Onda 7 (plano, T1.6) vira fato da
# designacao, e o engine recusa qualquer decisao de tarefa corrente assinada por ele (`StaffCaseInstallation.grant`, `projections.contains`).
CAPABILITIES: dict[str, tuple[list[str], list[str], list[str]]] = {
    "read_requester": (
        ["staff-case-read.v1", "staff-case-finalize.v1"],
        sorted(FIELDS),
        ["detail", "list"],
    ),
    "identity_verifier": (["membership_current"], [], []),
    "native_result": (["native_result"], [], []),
    "case_issuer": (
        ["staff_case_grant", "staff_policy_head", "scope_complete"],
        ["staff_escalation.v1", "staff_identity.v1", "staff_summary.v1"],
        ["detail", "list"],
    ),
    "publication_importer": (["staff-case-publication.v1"], [], []),
}
# A entrada `identity_verifier` PROPRIA do emissor (D-H.2): mesma fonte do witness do portal, chave
# e login dele (`IssuerWitness` recusa a chave do portal). Chave no `summary` sob este nome.
ISSUER_WITNESS_KEY = "case_issuer_witness"
# O job T1.5 assina com uma chave por proposito; nenhuma e a de leitura do portal.
JOB_PURPOSES = ("portal-read-publication", "human-authority")
JOB_KEY_FILES = {
    "portal-read-publication": "publication-signing-key.pem",
    "human-authority": "authority-signing-key.pem",
}
JOB_CLIENT = "publication_job"


def _private_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _public_der_b64(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode("ascii")


def continuity_commitment(key: bytes) -> str:
    if len(key) != 32:
        raise MaterialError("chave de continuidade precisa de 32 bytes")
    return hmac.new(key, CONTINUITY_DOMAIN, hashlib.sha256).hexdigest()


def rds_bundle() -> bytes:
    raw = RDS_BUNDLE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != RDS_BUNDLE_SHA256:
        raise MaterialError("bundle RDS vendorizado difere do pin revisado")
    return raw


def client_truststore(client_ca: Issued) -> bytes:
    """Truststore PKCS12 SEM senha com SO a CA de clientes (montado em `/run/maezo/native/client-ca.p12`).

    Medido na T1.2 (#482): o Tomcat 10.1 com JSSE ignora `caCertificateFile` e recusou um cliente
    confiavel; o listener mTLS le a CA de clientes deste truststore. Leva o atributo de "trusted
    certificate" do Java (`serialize_java_truststore`) e nenhuma chave privada.
    """
    return pkcs12.serialize_java_truststore(
        [pkcs12.PKCS12Certificate(client_ca.certificate, b"maezo-native-client-ca")],
        serialization.NoEncryption(),
    )


def dsn(connection: Any, password: str) -> bytes:
    return (
        f"postgresql+asyncpg://{connection.login}:{quote(password, safe='')}"
        f"@{connection.host}:{connection.port}/{connection.database}"
    ).encode("ascii")


@dataclass(frozen=True)
class Generated:
    directory: Path
    summary: dict[str, Any]


def designation_draft(
    spec: MaterialsSpec,
    keys: dict[str, Ed25519PrivateKey],
    certificate_spki: dict[str, str],
) -> dict[str, Any]:
    entries = []
    for role, settings in spec.roles().items():
        purposes, projections, operations = CAPABILITIES[role]
        entries.append(
            dict(
                entry_ref=role,
                role=role,
                source_namespace=settings.source_namespace,
                source_ref=settings.source_ref,
                key_fingerprint=fingerprint(keys[role].public_key()),
                certificate_spki=certificate_spki.get(role),
                public_key=_public_der_b64(keys[role]),
                login_role=settings.login_role,
                purposes=purposes,
                projections=projections,
                operations=operations,
                not_before=spec.designation.not_before,
                valid_until=spec.designation.valid_until,
            )
        )
    witness = spec.identity_verifier
    entries.append(
        dict(
            entry_ref=ISSUER_WITNESS_ENTRY,
            role="identity_verifier",
            source_namespace=witness.source_namespace,
            source_ref=witness.source_ref,
            key_fingerprint=fingerprint(keys[ISSUER_WITNESS_KEY].public_key()),
            certificate_spki=None,
            public_key=_public_der_b64(keys[ISSUER_WITNESS_KEY]),
            login_role=ISSUER_WITNESS_LOGIN,
            purposes=CAPABILITIES["identity_verifier"][0],
            projections=[],
            operations=[],
            not_before=spec.designation.not_before,
            valid_until=spec.designation.valid_until,
        )
    )
    value = dict(
        schema="staff-case-designation.v1",
        scope=spec.scope.wire(),
        designation_ref=spec.designation.designation_ref,
        designation_revision=spec.designation.designation_revision,
        expected_previous_revision=spec.designation.expected_previous_revision,
        authority_ref=spec.designation.authority_ref,
        authority_revision=spec.designation.authority_revision,
        entries=entries,
        issued_at=spec.designation.not_before,
        valid_until=spec.designation.valid_until,
        state="active",
    )
    # A mesma porta de entrada do loader: um rascunho que o modelo recusa nao sai daqui.
    parse(Designation, canonicalize(value))
    return value


def generate(spec: MaterialsSpec, out: Path) -> Generated:
    root = new_private_directory(out, forbidden=(REPO,))
    portal, engine, issuer, job, dba, public = (
        subdirectory(root, name) for name in ("portal", "engine", "issuer", "job", "dba", "public")
    )
    # X.509 guarda segundos inteiros: sem truncar, a folha "passaria" da CA por microssegundos.
    start = (timestamp(spec.designation.not_before) - timedelta(minutes=5)).replace(microsecond=0)
    end = timestamp(spec.certificate_not_after).replace(microsecond=0)

    # Duas CAs: a do servidor (o portal confia nela, `native-ca.pem`) e a dos clientes (o
    # Tomcat confia nela). Um certificado de cliente nunca encadeia para a raiz em que o portal
    # confia como servidor. As chaves das CAs morrem ao fim desta funcao.
    server_ca = issue_ca("maezo native server CA", start, end)
    client_ca = issue_ca("maezo native client CA", start, end)
    server = issue_leaf(server_ca, spec.native_hostname, "server", start, end, hostname=spec.native_hostname)
    clients: dict[str, Issued] = {
        role: issue_leaf(client_ca, f"maezo {role.replace('_', '-')}", "client", start, end)
        for role in ("read_requester", "publication_importer", JOB_CLIENT)
    }
    job_keys = {purpose: Ed25519PrivateKey.generate() for purpose in JOB_PURPOSES}
    keys = {role: Ed25519PrivateKey.generate() for role in (*CAPABILITIES, ISSUER_WITNESS_KEY)}
    designation = designation_draft(
        spec, keys, {role: c.spki_sha256() for role, c in clients.items() if role != JOB_CLIENT}
    )
    continuity_key = secrets.token_bytes(32)
    continuity_ref = "continuity-" + secrets.token_hex(16)
    rds = rds_bundle()
    passwords = {name: scram.new_password() for name in ("session_lock", "native_witness")}
    connections = {
        "session_lock": spec.session_lock_connection,
        "native_witness": spec.native_witness_connection,
    }

    files: list[tuple[Path, bytes, int]] = [
        (portal / "read-signing-key.pem", _private_pem(keys["read_requester"]), PRIVATE),
        (portal / "witness-signing-key.pem", _private_pem(keys["identity_verifier"]), PRIVATE),
        (portal / "read-client-key.pem", clients["read_requester"].key_pem(), PRIVATE),
        (portal / "read-client-certificate.pem", clients["read_requester"].certificate_pem(), PUBLIC),
        (portal / "native-ca.pem", server_ca.certificate_pem(), PUBLIC),
        (portal / "session-lock-ca.pem", rds, PUBLIC),
        (portal / "native-witness-ca.pem", rds, PUBLIC),
        (
            portal / "session-lock-dsn.txt",
            dsn(connections["session_lock"], passwords["session_lock"]),
            PRIVATE,
        ),
        (
            portal / "native-witness-dsn.txt",
            dsn(connections["native_witness"], passwords["native_witness"]),
            PRIVATE,
        ),
        (portal / "designation.json", canonicalize(designation), PUBLIC),
        (engine / "native-server-key.pem", server.key_pem(), PRIVATE),
        (engine / "native-server-certificate.pem", server.certificate_pem(), PUBLIC),
        (engine / "native-client-ca.pem", client_ca.certificate_pem(), PUBLIC),
        (engine / "client-ca.p12", client_truststore(client_ca), PUBLIC),
        (engine / "native-result-signing-key.pem", _private_pem(keys["native_result"]), PRIVATE),
        (engine / "portal-read-continuity-key.bin", continuity_key, PRIVATE),
        (issuer / "case-issuer-signing-key.pem", _private_pem(keys["case_issuer"]), PRIVATE),
        (issuer / "issuer-witness-signing-key.pem", _private_pem(keys[ISSUER_WITNESS_KEY]), PRIVATE),
        (
            issuer / "publication-importer-signing-key.pem",
            _private_pem(keys["publication_importer"]),
            PRIVATE,
        ),
        (issuer / "publication-importer-client-key.pem", clients["publication_importer"].key_pem(), PRIVATE),
        (
            issuer / "publication-importer-client-certificate.pem",
            clients["publication_importer"].certificate_pem(),
            PUBLIC,
        ),
        (job / "job-client-key.pem", clients[JOB_CLIENT].key_pem(), PRIVATE),
        (job / "job-client-certificate.pem", clients[JOB_CLIENT].certificate_pem(), PUBLIC),
        *(
            (job / JOB_KEY_FILES[purpose], _private_pem(key), PRIVATE)
            for purpose, key in sorted(job_keys.items())
        ),
        (
            dba / "role-verifiers.json",
            canonicalize(
                {connections[name].login: scram.verifier(password) for name, password in passwords.items()}
            ),
            PRIVATE,
        ),
    ]
    summary: dict[str, Any] = dict(
        schema="staff-materials-summary.v1",
        scope=spec.scope.wire(),
        native_hostname=spec.native_hostname,
        designation_sha256=digest(designation),
        designation_valid_until=spec.designation.valid_until,
        certificate_not_after=spec.certificate_not_after,
        key_fingerprints={role: fingerprint(key.public_key()) for role, key in sorted(keys.items())},
        client_certificate_spki_sha256={role: c.spki_sha256() for role, c in sorted(clients.items())},
        job_key_fingerprints={
            purpose: fingerprint(key.public_key()) for purpose, key in sorted(job_keys.items())
        },
        native_server_spki_sha256=server.spki_sha256(),
        native_ca_sha256=hashlib.sha256(server_ca.certificate_pem()).hexdigest(),
        native_client_ca_sha256=hashlib.sha256(client_ca.certificate_pem()).hexdigest(),
        native_client_ca_der_sha256=hashlib.sha256(
            client_ca.certificate.public_bytes(serialization.Encoding.DER)
        ).hexdigest(),
        continuity=dict(key_ref=continuity_ref, commitment=continuity_commitment(continuity_key)),
        logins=dict(
            session_lock=spec.session_lock_connection.login,
            native_witness=spec.native_witness_connection.login,
        ),
        pending_from_approver=["installation-root.der", "installation-proof.json"],
    )
    files.append((public / "summary.json", canonicalize(summary), PUBLIC))
    for path, data, mode in files:
        write_new(path, data, mode)
    return Generated(root, summary)
