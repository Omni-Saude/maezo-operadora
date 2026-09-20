"""ADR-0049 D8 / PUOI-01: pinned roots and real OpenSSL, with no cloud or services.

Installation paths/trust files are temporary. Debian's update-ca-certificates and
the full image are separate artifact checks; these tests do not claim either ran.
Synthetic TLS handshakes use MemoryBIO, not sockets or any RDS private key.
"""

from __future__ import annotations

import hashlib
import importlib.util
import ssl
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "deploy/certificates/sa-east-1-bundle.pem"


def synthetic_ca(common_name: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """Throwaway self-signed root generated in-test, never from a host trust store."""
    now = datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    return key, (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )


@pytest.fixture
def installer():
    spec = importlib.util.spec_from_file_location(
        "install_rds_roots", ROOT / "deploy/certificates/install_rds_roots.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_trust(tmp_path, monkeypatch):
    # One unrelated synthetic root is retained as the pre-existing system-store control.
    # Deriving it from ssl.create_default_context() made the fixture depend on the host:
    # on CI runners that source is empty or RDS-only, so bare next() raised StopIteration
    # in setup. The control must stay non-RDS and outside the pinned bundle, which a
    # self-signed in-test root guarantees on every machine.
    _, unrelated = synthetic_ca("Synthetic unrelated root; never for deployment")
    path = tmp_path / "trust.pem"
    path.write_bytes(unrelated.public_bytes(serialization.Encoding.PEM))
    empty_dir = tmp_path / "empty-capath"
    empty_dir.mkdir()
    monkeypatch.setenv("SSL_CERT_FILE", str(path))
    monkeypatch.setenv("SSL_CERT_DIR", str(empty_dir))
    return path


def trusted_fingerprints():
    return {
        hashlib.sha256(cert).hexdigest()
        for cert in ssl.create_default_context().get_ca_certs(binary_form=True)
    }


def test_runtime_stage_wires_pinned_installation_before_unprivileged_execution():
    # Source propagation fence, additional to behavioral tests and the full image gate.
    runtime = (ROOT / "deploy/Dockerfile").read_text().split(" AS runtime\n", 1)[1]
    copy = (
        "COPY deploy/certificates/install_rds_roots.py "
        "deploy/certificates/sa-east-1-bundle.pem /tmp/maezo-rds-ca/"
    )
    install = "RUN python /tmp/maezo-rds-ca/install_rds_roots.py"
    assert "apt-get install -y --no-install-recommends libpq5 ca-certificates" in runtime
    assert runtime.index(copy) < runtime.index(install) < runtime.index("USER 1000")
    assert "    && rm -rf /tmp/maezo-rds-ca\n" in runtime


def test_build_context_admits_only_the_installer_and_the_pinned_bundle():
    # Nothing else from deploy/certificates (README included) reaches the image context.
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert [line for line in ignore if "deploy/certificates" in line] == [
        "!deploy/certificates/",
        "deploy/certificates/*",
        "!deploy/certificates/install_rds_roots.py",
        "!deploy/certificates/sa-east-1-bundle.pem",
    ]


def test_official_bundle_contains_exact_current_self_signed_regional_roots(installer):
    contents = BUNDLE.read_bytes()
    assert len(contents) == 4572
    assert hashlib.sha256(contents).hexdigest() == installer.BUNDLE_SHA256
    certificates = x509.load_pem_x509_certificates(contents)
    assert len(certificates) == 3
    assert {cert.fingerprint(hashes.SHA256()).hex() for cert in certificates} == set(
        installer.ROOTS_SHA256.values()
    )
    now = datetime.now(UTC)
    for cert in certificates:
        assert cert.subject == cert.issuer
        assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        assert "Amazon RDS sa-east-1 Root CA" in cert.subject.rfc4514_string()
        assert cert.not_valid_before_utc <= now < cert.not_valid_after_utc
        cert.verify_directly_issued_by(cert)


def test_install_and_default_python_trust_preserve_existing_root(installer, isolated_trust, tmp_path):
    before = trusted_fingerprints()
    assert before.isdisjoint(installer.ROOTS_SHA256.values())
    with pytest.raises(RuntimeError, match="missing from Python default trust store"):
        installer.verify_default_trust()
    destination = tmp_path / "certificates"
    installer.install_roots(BUNDLE, destination)
    paths = sorted(destination.iterdir())
    assert {p.name for p in paths} == {f"rds-sa-east-1-{name}.crt" for name in installer.ROOTS_SHA256}
    for path in paths:
        assert path.stat().st_mode & 0o777 == 0o644
        assert len(x509.load_pem_x509_certificates(path.read_bytes())) == 1
    # Portable fixture assembly only; Linux update-ca-certificates runs in image verification.
    with isolated_trust.open("ab") as store:
        for path in paths:
            store.write(path.read_bytes())
    installer.verify_default_trust()
    assert trusted_fingerprints() == before | set(installer.ROOTS_SHA256.values())
    context = ssl.create_default_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    installer.install_roots(BUNDLE, destination)
    assert sorted(destination.iterdir()) == paths


@pytest.mark.parametrize("mutation", ["changed-byte", "missing-root", "extra-root", "wrong-root", "empty"])
def test_bad_bundle_fails_before_any_write(installer, isolated_trust, tmp_path, mutation):
    contents = BUNDLE.read_bytes()
    first = x509.load_pem_x509_certificates(contents)[0].public_bytes(serialization.Encoding.PEM)
    bad = {
        "changed-byte": contents.replace(b"MI", b"NI", 1),
        "missing-root": contents[len(first) :],
        "extra-root": contents + first,
        "wrong-root": isolated_trust.read_bytes(),
        "empty": b"",
    }[mutation]
    path = tmp_path / "bad.pem"
    path.write_bytes(bad)
    destination = tmp_path / "not-created"
    with pytest.raises(ValueError, match="bundle SHA-256 mismatch"):
        installer.install_roots(path, destination)
    assert not destination.exists()


def test_missing_bundle_fails_before_any_write(installer, tmp_path):
    destination = tmp_path / "not-created"
    with pytest.raises(FileNotFoundError):
        installer.install_roots(tmp_path / "absent.pem", destination)
    assert not destination.exists()


def test_pin_only_change_cannot_authorize_an_extra_root(installer, isolated_trust, tmp_path, monkeypatch):
    path = tmp_path / "extra.pem"
    path.write_bytes(BUNDLE.read_bytes() + isolated_trust.read_bytes())
    monkeypatch.setattr(installer, "BUNDLE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="root fingerprints mismatch"):
        installer.install_roots(path, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_write_failure_prevents_trust_store_update(installer, tmp_path, monkeypatch):
    destination = tmp_path / "file-not-directory"
    destination.write_bytes(b"sentinel")
    monkeypatch.setattr(installer, "DESTINATION", destination)

    def unexpected_update(*args, **kwargs):
        pytest.fail("trust update must not run after an installation failure")

    monkeypatch.setattr(installer.subprocess, "run", unexpected_update)
    with pytest.raises(FileExistsError):
        installer.main()
    assert destination.read_bytes() == b"sentinel"


@pytest.mark.parametrize("failure", ["missing-command", "nonzero", "no-trust-added"])
def test_trust_update_failure_aborts_build(installer, isolated_trust, tmp_path, monkeypatch, failure):
    monkeypatch.setattr(installer, "DESTINATION", tmp_path / "roots")

    def update(command, *, check):
        assert command == ["update-ca-certificates"] and check is True
        if failure == "missing-command":
            raise FileNotFoundError(command[0])
        if failure == "nonzero":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(installer.subprocess, "run", update)
    error = {
        "missing-command": FileNotFoundError,
        "nonzero": subprocess.CalledProcessError,
        "no-trust-added": RuntimeError,
    }[failure]
    with pytest.raises(error):
        installer.main()


@pytest.fixture
def synthetic_chain(tmp_path):
    now = datetime.now(UTC)
    ca_key, ca = synthetic_ca("Synthetic test CA; never for deployment")
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "db.synthetic.invalid")]))
        .issuer_name(ca.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("db.synthetic.invalid")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_file = tmp_path / "synthetic-leaf.pem"
    cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_file = tmp_path / "synthetic-leaf.key"
    key_file.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    key_file.chmod(0o600)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_file, key_file)
    return ca.public_bytes(serialization.Encoding.PEM), leaf.public_bytes(serialization.Encoding.DER), server


def handshake(client_context, server_context, hostname):
    incoming, outgoing, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(incoming, outgoing, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    done = [False, False]
    for _ in range(20):
        for index, peer in enumerate((client, server)):
            if not done[index]:
                try:
                    peer.do_handshake()
                    done[index] = True
                except ssl.SSLWantReadError:
                    pass
        server_in.write(outgoing.read())
        incoming.write(server_out.read())
        if all(done):
            return client.getpeercert(binary_form=True)
    pytest.fail("synthetic TLS handshake did not complete")


@pytest.mark.parametrize("control", ["trusted-host", "wrong-host", "untrusted-chain"])
def test_real_default_context_synthetic_tls(installer, isolated_trust, tmp_path, synthetic_chain, control):
    destination = tmp_path / "roots"
    installer.install_roots(BUNDLE, destination)
    ca, leaf, server = synthetic_chain
    with isolated_trust.open("ab") as store:
        for path in sorted(destination.iterdir()):
            store.write(path.read_bytes())
        if control != "untrusted-chain":
            store.write(ca)
    installer.verify_default_trust()
    context = ssl.create_default_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    if control == "trusted-host":
        assert handshake(context, server, "db.synthetic.invalid") == leaf
    else:
        hostname = "wrong.synthetic.invalid" if control == "wrong-host" else "db.synthetic.invalid"
        with pytest.raises(ssl.SSLCertVerificationError) as refused:
            handshake(context, server, hostname)
        assert refused.value.verify_code == (62 if control == "wrong-host" else 20)
