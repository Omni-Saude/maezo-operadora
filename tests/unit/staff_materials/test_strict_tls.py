"""Os certificados gerados passam no verificador ESTRITO do Python 3.13+ (achado da Onda 0/S1).

Python 3.13 liga `VERIFY_X509_STRICT | VERIFY_X509_PARTIAL_CHAIN` em `create_default_context()`.
O teste liga as mesmas flags explicitamente, entao prova o comportamento de 3.13+ tambem no
3.12 do portal. O handshake e real (OpenSSL do interpretador, TLS 1.3, mTLS), em memoria.

O controle negativo reproduz o harness de hoje (CA e folha sem AKI/SKI, `prepare.py`): sob a flag
estrita ele e recusado e sem a flag ele passa — a recusa vem da flag, nao de outro defeito.
"""

from __future__ import annotations

import hashlib
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from tools.staff_materials.generate import Generated

STRICT = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN


def _pump(client: ssl.SSLObject, server: ssl.SSLObject, bios: tuple[ssl.MemoryBIO, ...]) -> None:
    c_in, c_out, s_in, s_out = bios
    done = {"client": False, "server": False}
    for _ in range(50):
        for name, side in (("client", client), ("server", server)):
            if done[name]:
                continue
            try:
                side.do_handshake()
                done[name] = True
            except ssl.SSLWantReadError:
                pass
        if data := c_out.read():
            s_in.write(data)
        if data := s_out.read():
            c_in.write(data)
        # TLS 1.3: o servidor so verifica o certificado do cliente ao ler o Finished dele, entao
        # "pronto" exige os DOIS lados, nao so o cliente.
        if all(done.values()) and not c_out.pending and not s_out.pending:
            return
    raise AssertionError("handshake nao terminou")


def handshake(
    *,
    server_cert: Path,
    server_key: Path,
    client_ca: Path,
    server_ca: Path,
    client_cert: Path,
    client_key: Path,
    hostname: str,
    flags: int,
) -> ssl.SSLObject:
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_3
    server_context.load_cert_chain(str(server_cert), str(server_key))
    server_context.verify_mode = ssl.CERT_REQUIRED
    server_context.load_verify_locations(cafile=str(client_ca))
    server_context.verify_flags |= flags
    client_context = ssl.create_default_context(cafile=str(server_ca))
    client_context.minimum_version = ssl.TLSVersion.TLSv1_3
    client_context.verify_flags |= flags
    client_context.load_cert_chain(str(client_cert), str(client_key))
    bios = tuple(ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(bios[0], bios[1], server_hostname=hostname)
    server = server_context.wrap_bio(bios[2], bios[3], server_side=True)
    _pump(client, server, bios)
    assert server.getpeercert(binary_form=True) is not None
    return client


@pytest.mark.parametrize("client", ["portal/read-client", "issuer/publication-importer-client"])
def test_generated_mtls_passes_python313_strict_verification(generated: Generated, client: str) -> None:
    d = generated.directory
    session = handshake(
        server_cert=d / "engine/native-server-certificate.pem",
        server_key=d / "engine/native-server-key.pem",
        client_ca=d / "engine/native-client-ca.pem",
        server_ca=d / "portal/native-ca.pem",
        client_cert=d / f"{client}-certificate.pem",
        client_key=d / f"{client}-key.pem",
        hostname=generated.summary["native_hostname"],
        flags=STRICT,
    )
    der = session.getpeercert(binary_form=True)
    assert der is not None
    peer = x509.load_der_x509_certificate(der)
    spki = peer.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # O pin que o portal confere (`publisher.py`, peer SPKI) e o que o generate publicou.
    assert hashlib.sha256(spki).hexdigest() == generated.summary["native_server_spki_sha256"]


def test_wrong_hostname_and_wrong_client_ca_are_refused(generated: Generated) -> None:
    d = generated.directory
    base = dict(
        server_cert=d / "engine/native-server-certificate.pem",
        server_key=d / "engine/native-server-key.pem",
        client_ca=d / "engine/native-client-ca.pem",
        server_ca=d / "portal/native-ca.pem",
        client_cert=d / "portal/read-client-certificate.pem",
        client_key=d / "portal/read-client-key.pem",
        hostname=generated.summary["native_hostname"],
        flags=STRICT,
    )
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(**base | {"hostname": "outro.maezo-operadora-dev.internal"})
    # O servidor confia so na CA de clientes: a CA do servidor nao autentica cliente nenhum.
    with pytest.raises(ssl.SSLError):
        handshake(**base | {"client_ca": d / "portal/native-ca.pem"})


def _harness_style(directory: Path) -> dict[str, Path]:
    """O `issue()` de `deploy/cibseven/package-test/prepare.py`: sem AKI, sem SKI, sem KeyUsage."""
    now = datetime.now(UTC)
    out: dict[str, Path] = {}

    def issue(
        name: str, ca: tuple[ec.EllipticCurvePrivateKey, x509.Certificate] | None, eku: object
    ) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca[1].subject if ca else subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=2))
            .add_extension(x509.BasicConstraints(ca=ca is None, path_length=None), critical=True)
        )
        if eku is not None:
            builder = builder.add_extension(x509.ExtendedKeyUsage([eku]), critical=True)  # type: ignore[list-item]
        if eku == ExtendedKeyUsageOID.SERVER_AUTH:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
            )
        cert = builder.sign(ca[0] if ca else key, hashes.SHA256())
        (directory / f"{name}.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        (directory / f"{name}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        out[name] = directory / f"{name}.crt"
        out[name + ".key"] = directory / f"{name}.key"
        return key, cert

    ca = issue("ca", None, None)
    issue("server", ca, ExtendedKeyUsageOID.SERVER_AUTH)
    issue("client", ca, ExtendedKeyUsageOID.CLIENT_AUTH)
    return out


def test_negative_control_harness_certificates_fail_only_under_strict(tmp_path: Path) -> None:
    files = _harness_style(tmp_path)
    base = dict(
        server_cert=files["server"],
        server_key=files["server.key"],
        client_ca=files["ca"],
        server_ca=files["ca"],
        client_cert=files["client"],
        client_key=files["client.key"],
        hostname="localhost",
    )
    handshake(**base, flags=0)  # type: ignore[arg-type]
    with pytest.raises(ssl.SSLCertVerificationError, match="(?i)key identifier"):
        handshake(**base, flags=STRICT)  # type: ignore[arg-type]
