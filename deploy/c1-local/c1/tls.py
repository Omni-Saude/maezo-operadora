"""Passo `tls` (roda como root no runner): CA e certificado do PostgreSQL descartavel + senhas de teste.

So o que nenhuma ferramenta do repo gera: a CA local do banco (no lugar das raizes RDS pinadas) e as
senhas do superusuario, do `cibseven_app` e do `maezo_app` (logins que no dev ja existem). Os logins
novos da T1.4 NAO saem daqui: o `db` usa o verificador SCRAM da ferramenta (`tools.staff_materials.scram`).
"""

from __future__ import annotations

import ipaddress
import os
from datetime import timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .common import ADMIN, ENGINE_NATIVE, ENGINE_RUN, PG_HOST, PGTLS, ROOT, STATE, now, password, step, write

POSTGRES_UID = 70  # postgres:17-alpine
ENGINE_UID = 1000  # camunda (Dockerfile.human) e o usuario do runner


def _pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def main() -> None:
    start = now() - timedelta(minutes=5)
    end = now() + timedelta(days=2)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "c1 disposable postgres CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, PG_HOST)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(PG_HOST), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    for directory, uid in ((ROOT, ENGINE_UID), (ADMIN, ENGINE_UID), (STATE, ENGINE_UID), (ENGINE_RUN, ENGINE_UID),
                           (ENGINE_NATIVE, ENGINE_UID), (PGTLS, POSTGRES_UID), (ROOT / "staff-materials", ENGINE_UID),
                           (ROOT / "human-materials", ENGINE_UID)):
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chown(directory, uid, uid)
        os.chmod(directory, 0o755 if directory in (ROOT, PGTLS) else 0o700)
    write(PGTLS / "server.crt", _pem(server), 0o444, uid=POSTGRES_UID)
    write(
        PGTLS / "server.key",
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        0o400,
        uid=POSTGRES_UID,
    )
    write(PGTLS / "ca.pem", _pem(ca), 0o444, uid=POSTGRES_UID)
    # O entrypoint do postgres le a senha como root antes de trocar de usuario.
    os.chmod(ADMIN, 0o755)
    write(ADMIN / "postgres-password", password(), 0o444, uid=ENGINE_UID)
    cibseven = password()
    write(ADMIN / "cibseven-password", cibseven, 0o444, uid=ENGINE_UID)
    write(ADMIN / "maezo-app-password", password(), 0o400, uid=ENGINE_UID)
    write(ENGINE_RUN / "cibseven-password", cibseven, 0o400, uid=ENGINE_UID)
    write(ENGINE_RUN / "pg-ca.pem", _pem(ca), 0o444, uid=ENGINE_UID)
    os.chmod(ENGINE_RUN, 0o755)
    os.chmod(ENGINE_NATIVE, 0o755)
    step("tls", True, "CA local do PostgreSQL (SAN postgres) e senhas de teste no volume c1private")


if __name__ == "__main__":
    main()
