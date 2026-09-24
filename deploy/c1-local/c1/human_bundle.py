"""O pacote `portal-human-material.v1` que o job da T1.5 le (`load_human_materials`).

Nenhuma ferramenta do repo gera este pacote (D6 no README): o `generate` da T1.3 so cobre o plano
staff. O formato e o de `tests/support/materials_builder.py`, com tres diferencas que um pacote de
teste nao tem e um de implantacao precisa ter:
* os certificados de cliente sao emitidos por uma CA de cliente PROPRIA do job, que entra no
  truststore do listener 8443 ao lado da CA de clientes do `generate` (D5);
* o `read-admission.json` espelha a admissao Q2 que o engine vai de fato carregar (mesmo
  deployment, geracao = revisao, `capability_digest` = SHA-256 do registro assinado);
* as superficies apontam para o engine local (`https://engine-native.c1.internal`, SPKI do servidor
  do `generate`).
A raiz deste pacote e outra raiz de TESTE, descartavel, distinta da raiz staff.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from maezo.gateway.human.production_materials import FILES, PUBLIC_FILES, HumanPublicManifest
from maezo.gateway.human.read_credentials import ReadAdmission
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.engine.profile import canonicalize

from .common import iso


def spki(key: object) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)  # type: ignore[attr-defined]


def pem(key: Ed25519PrivateKey | ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


@dataclass
class ClientCa:
    key: ec.EllipticCurvePrivateKey
    certificate: x509.Certificate

    @classmethod
    def new(cls, name: str, start: datetime, end: datetime) -> ClientCa:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(end)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        return cls(key, cert)

    def client(self, name: str, start: datetime, end: datetime) -> tuple[bytes, bytes, str]:
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(self.certificate.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(end)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.certificate.public_key()), critical=False
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(self.key, hashes.SHA256())
        )
        return cert.public_bytes(Encoding.PEM), pem(key), hashlib.sha256(spki(key)).hexdigest()


@dataclass
class HumanBundle:
    manifest: HumanPublicManifest
    manifest_sha256: str
    read_key: Ed25519PrivateKey
    command_key: Ed25519PrivateKey
    read_client_spki: str
    command_client_spki: str
    key_ids: dict[str, str]


def build(
    *,
    directory: Path,
    scope: dict,
    engine_name: str,
    incarnation: str,
    admission: dict,
    origin: str,
    server_spki: str,
    server_ca_pem: bytes,
    client_ca: ClientCa,
    audience_read: str,
    catalog_ref: str,
    source_dsn: bytes,
    outbox_dsn: bytes,
    window: tuple[datetime, datetime],
) -> HumanBundle:
    issued_at, valid_until = window
    root = Ed25519PrivateKey.generate()
    read_key, assignment_key, command_key = (Ed25519PrivateKey.generate() for _ in range(3))
    read_cert, read_private, read_spki = client_ca.client("c1 job read client", issued_at, valid_until)
    command_cert, command_private, command_spki = client_ca.client("c1 job command client", issued_at, valid_until)
    record = parse_model(ReadAdmission, admission)
    read_admission = json.dumps(
        {
            "schema": "portal-human-read-admission.v1",
            "record": wire(record),
            "signature": base64.b64encode(root.sign(canonicalize(wire(record)))).decode("ascii"),
        }
    ).encode()
    cursor_current, cursor_previous = os.urandom(32), os.urandom(32)
    files: dict[str, bytes] = {
        "installation-root.der": spki(root),
        "read-admission.json": read_admission,
        "read-ca.pem": server_ca_pem,
        "read-client-certificate.pem": read_cert,
        "command-ca.pem": server_ca_pem,
        "command-client-certificate.pem": command_cert,
        "read-signing-key.pem": pem(read_key),
        "assignment-signing-key.pem": pem(assignment_key),
        "command-signing-key.pem": pem(command_key),
        "cursor-keys.json": json.dumps(
            {
                "schema": "portal-human-cursor-keys.v1",
                "not_before": iso(issued_at),
                "not_after": iso(valid_until),
                "keys": {
                    "cursor-current": base64.b64encode(cursor_current).decode("ascii"),
                    "cursor-previous": base64.b64encode(cursor_previous).decode("ascii"),
                },
            }
        ).encode(),
        "read-client-key.pem": read_private,
        "command-client-key.pem": command_private,
        "outbox-dsn.txt": outbox_dsn,
        "source-dsn.txt": source_dsn,
    }
    assert set(files) == FILES
    key_ids = {"portal-task-read": "c1-human-read-1", "human-assignment-read": "c1-human-assignment-1",
               "human-command": "c1-human-command-1"}
    fp = {k: hashlib.sha256(spki(v)).hexdigest() for k, v in
          (("portal-task-read", read_key), ("human-assignment-read", assignment_key), ("human-command", command_key))}
    surface = dict(origin=origin, server_spki_sha256=server_spki, timeout_seconds="10")
    payload = {
        "schema": "portal-human-material.v1",
        "material_version_id": "c1human" + os.urandom(13).hex(),
        "scope": scope,
        "issuer": "https://identity.c1.invalid",
        "issued_at": iso(issued_at),
        "valid_until": iso(valid_until),
        "root_key_fingerprint": hashlib.sha256(spki(root)).hexdigest(),
        "engine_name": engine_name,
        "database_incarnation": incarnation,
        "assignment_workload_ref": scope["workload_ref"] + "-assignment",
        "command_endpoint": origin,
        "catalog_ref": catalog_ref,
        "publisher_ref": scope["workload_ref"],
        "keys": [
            {"purpose": p, "key_id": key_ids[p], "fingerprint": fp[p], "max_envelope_seconds": "30",
             "workload_ref": scope["workload_ref"] + ("-assignment" if p == "human-assignment-read" else ""),
             "audience": audience_read if p == "portal-task-read" else "c1-engine-" + p}
            for p in ("portal-task-read", "human-assignment-read", "human-command")
        ],
        "cursor_keys": [
            {"key_id": "cursor-current", "key_tag": os.urandom(16).hex(),
             "material_sha256": hashlib.sha256(cursor_current).hexdigest(), "generation": "2", "current": True},
            {"key_id": "cursor-previous", "key_tag": os.urandom(16).hex(),
             "material_sha256": hashlib.sha256(cursor_previous).hexdigest(), "generation": "1", "current": False},
        ],
        "read_surface": dict(surface, ca_file="read-ca.pem", certificate_file="read-client-certificate.pem",
                             private_key_file="read-client-key.pem", client_spki_sha256=read_spki),
        "command_surface": dict(surface, ca_file="command-ca.pem", certificate_file="command-client-certificate.pem",
                                private_key_file="command-client-key.pem", client_spki_sha256=command_spki),
        "outbox_connection": {"host": "postgres", "port": "5432", "database": "maezo", "login": "c1_unused_outbox"},
        "source_connection": {"host": "postgres", "port": "5432", "database": "maezo", "login": "c1_unused_source"},
        "relay_lease_seconds": "30",
        "relay_retry_seconds": "1",
        "relay_poll_seconds": "1",
        "revocation_snapshot": {"scope": scope, "source_ref": "c1-revocation", "revision": "1",
                                "observed_at": iso(issued_at), "valid_until": iso(valid_until),
                                "revoked_fingerprints": []},
        "files": {**{n: hashlib.sha256(files[n]).hexdigest() for n in PUBLIC_FILES},
                  **{n: None for n in FILES - PUBLIC_FILES}},
    }
    manifest = parse_model(HumanPublicManifest, payload)
    current = directory / "current"
    if current.exists():
        os.chmod(current, 0o700)
        for child in current.iterdir():
            child.unlink()
    else:
        current.mkdir(parents=True, mode=0o700)
    for name, raw in {**files, "manifest.json": manifest.canonical()}.items():
        path = current / name
        path.write_bytes(raw)
        os.chmod(path, 0o400)
    os.chmod(current, 0o500)
    return HumanBundle(
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest.canonical()).hexdigest(),
        read_key=read_key,
        command_key=command_key,
        read_client_spki=read_spki,
        command_client_spki=command_spki,
        key_ids=key_ids,
    )


def window(hours: float = 20) -> tuple[datetime, datetime]:
    from .common import now

    start = now() - timedelta(minutes=5)
    return start, start + timedelta(hours=hours)
