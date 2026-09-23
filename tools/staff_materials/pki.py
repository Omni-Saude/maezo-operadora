"""CAs e certificados do mTLS nativo, com os campos que o verificador estrito exige.

Python 3.13 liga `ssl.VERIFY_X509_STRICT` em `create_default_context()`. Sob essa flag o OpenSSL
recusa cadeia sem `AuthorityKeyIdentifier` (a Onda 0/S1 viu `Missing Authority Key Identifier`
com Python 3.14 contra o harness, que nao traz AKI/SKI). Todo certificado emitido aqui traz:

* CA: `BasicConstraints(ca=True, path_length=0)` critico, `KeyUsage(keyCertSign, cRLSign)`
  critico, `SubjectKeyIdentifier` e `AuthorityKeyIdentifier`;
* folha: `BasicConstraints(ca=False)` critico, `KeyUsage(digitalSignature)` critico,
  `ExtendedKeyUsage` (servidor OU cliente, nunca os dois), `SubjectKeyIdentifier`,
  `AuthorityKeyIdentifier` apontando para a SKI da CA e, no servidor, `SubjectAlternativeName`
  com o hostname exato de D-B.

Chaves TLS sao ECDSA P-256, como no harness que a S1 ja provou contra o Tomcat/JSSE do engine. As
chaves de assinatura dos papeis designados sao Ed25519 e nao passam por aqui.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .secure_io import MaterialError

HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


@dataclass(frozen=True, repr=False)
class Issued:
    key: ec.EllipticCurvePrivateKey
    certificate: x509.Certificate

    def __repr__(self) -> str:
        return f"Issued(subject={self.certificate.subject.rfc4514_string()!r})"

    def certificate_pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)

    def key_pem(self) -> bytes:
        return self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )

    def spki_sha256(self) -> str:
        return spki_sha256(self.certificate)


def spki_sha256(certificate: x509.Certificate) -> str:
    return hashlib.sha256(
        certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).hexdigest()


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def issue_ca(common_name: str, not_before: datetime, not_after: datetime) -> Issued:
    if not not_before < not_after:
        raise MaterialError("janela de CA invalida")
    key = ec.generate_private_key(ec.SECP256R1())
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ski, critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
        .sign(key, hashes.SHA256())
    )
    return Issued(key, certificate)


def issue_leaf(
    ca: Issued,
    common_name: str,
    usage: Literal["server", "client"],
    not_before: datetime,
    not_after: datetime,
    *,
    hostname: str | None = None,
) -> Issued:
    if not not_before < not_after or not_after > ca.certificate.not_valid_after_utc:
        raise MaterialError("janela de certificado invalida ou maior que a da CA")
    if (usage == "server") != (hostname is not None):
        raise MaterialError("so o certificado de servidor leva hostname")
    if hostname is not None and not HOSTNAME.fullmatch(hostname):
        raise MaterialError("hostname do servidor nativo invalido")
    key = ec.generate_private_key(ec.SECP256R1())
    ca_ski = ca.certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    eku = ExtendedKeyUsageOID.SERVER_AUTH if usage == "server" else ExtendedKeyUsageOID.CLIENT_AUTH
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(ca.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False)
    )
    if hostname is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
    return Issued(key, builder.sign(ca.key, hashes.SHA256()))
