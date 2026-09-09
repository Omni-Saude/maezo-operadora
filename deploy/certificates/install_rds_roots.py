"""Build-time installation of the pinned sa-east-1 RDS roots (ADR-0049 D8).

Public source, reviewed 2026-09-09:
https://truststore.pki.rds.amazonaws.com/sa-east-1/sa-east-1-bundle.pem
See README.md for provenance and rotation. No download or runtime configuration.
Only the standard library is needed, before the application venv is copied.
"""

from __future__ import annotations

import hashlib
import re
import ssl
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).with_name("sa-east-1-bundle.pem")
DESTINATION = Path("/usr/local/share/ca-certificates/maezo-rds-sa-east-1")
BUNDLE_SHA256 = "c2f9255eadfa939dd6f965ede75d8e0d4168c9cbb7ca1e7baa9bff6d5e2c96e1"
# DER fingerprints of the three authenticated, self-signed regional roots.
ROOTS_SHA256 = {
    "rsa2048-g1": "376e82dd0d72b3f4234e1dad34f7bc086d51947f8b343a887e3ae122e0fd212b",
    "ecc384-g1": "80ca8be42d38ea097fc733d79fc73d595f95f3834e04bb4efed15f1be53e8790",
    "rsa4096-g1": "4d895d0786fd52b513554e73a07c2c0df47138e834a4338866e4d4bdc85778a3",
}


def install_roots(bundle: Path, destination: Path) -> None:
    """Validate all bytes before writing one .crt per root for Debian's store."""
    contents = bundle.read_bytes()
    if hashlib.sha256(contents).hexdigest() != BUNDLE_SHA256:
        raise ValueError("RDS regional bundle SHA-256 mismatch")
    certificates = re.findall(
        rb"-----BEGIN CERTIFICATE-----\n.*?-----END CERTIFICATE-----\n", contents, re.DOTALL
    )
    roots = {
        hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))).hexdigest(): pem for pem in certificates
    }
    if len(certificates) != len(ROOTS_SHA256) or roots.keys() != set(ROOTS_SHA256.values()):
        raise ValueError("RDS regional root fingerprints mismatch")
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    for name, fingerprint in ROOTS_SHA256.items():
        path = destination / f"rds-sa-east-1-{name}.crt"
        path.write_bytes(roots[fingerprint])
        path.chmod(0o644)


def verify_default_trust() -> None:
    """Fail the image build unless the default Python context trusts every root."""
    context = ssl.create_default_context()
    trusted = {hashlib.sha256(der).hexdigest() for der in context.get_ca_certs(binary_form=True)}
    if not set(ROOTS_SHA256.values()).issubset(trusted):
        raise RuntimeError("RDS regional roots missing from Python default trust store")
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("Python default certificate/hostname verification required")


def main() -> None:
    install_roots(BUNDLE, DESTINATION)
    subprocess.run(["update-ca-certificates"], check=True)
    verify_default_trust()
    print("Installed and verified 3 pinned sa-east-1 RDS roots in Python default trust store")


if __name__ == "__main__":
    main()
