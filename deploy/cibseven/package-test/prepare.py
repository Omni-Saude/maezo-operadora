#!/usr/bin/env python3
"""ADR0049 D5: offline disposable mTLS fixture, never a deployment default.

Run from a fresh, clean exact-SHA checkout. This program does not operate Docker.
All generated credentials stay under an explicit daemon-visible private root,
outside the checkout and evidence tree.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import secrets
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

POSTGRES = "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
TENANT = "package-test"


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def public_bytes(key) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def canonical_existing(path: Path, label: str, *, directory: bool) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} must exist") from exc
    if resolved != path:
        raise ValueError(f"{label} must be canonical and contain no symlink")
    mode = path.lstat().st_mode
    if directory and not stat.S_ISDIR(mode):
        raise ValueError(f"{label} must be a directory")
    if not directory and not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular file")
    return path


def overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def validate_output_roots(checkout: Path, private_root: Path, evidence_root: Path, output: Path) -> None:
    canonical_existing(checkout, "checkout", directory=True)
    canonical_existing(private_root, "private root", directory=True)
    canonical_existing(evidence_root, "evidence root", directory=True)
    root_stat = private_root.stat()
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise ValueError("private root must have mode 0700")
    if root_stat.st_uid != os.geteuid():
        raise ValueError("private root must be owned by the current user")
    for label, protected in (("checkout", checkout), ("evidence root", evidence_root)):
        if overlaps(private_root, protected):
            raise ValueError(f"private root must not overlap {label}")
    if not output.is_absolute() or output.resolve(strict=False) != output:
        raise ValueError("output must be an absolute canonical path with no symlink escape")
    if output.parent != private_root:
        raise ValueError("output must be a direct child of the private root")
    if os.path.lexists(output):
        raise ValueError("output must be a NEW path")


def prepare(
    checkout: Path,
    sha: str,
    original: Path,
    private_root: Path,
    evidence_root: Path,
    output: Path,
    image: str,
    *,
    relay_synthetic: bool = False,
) -> None:
    if type(relay_synthetic) is not bool:
        raise ValueError("relay synthetic opt-in must be explicit boolean")
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("full exact source SHA required")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=checkout, text=True
    )
    if actual != sha or dirty:
        raise ValueError("checkout must be clean and at the specified SHA")
    canonical_existing(original, "base server XML", directory=False)
    validate_output_roots(checkout, private_root, evidence_root, output)
    old_umask = os.umask(0o077)
    try:
        _generate_fixture(checkout, sha, original, output, image, relay_synthetic=relay_synthetic)
    finally:
        os.umask(old_umask)


def _generate_fixture(
    checkout: Path, sha: str, original: Path, output: Path, image: str, *, relay_synthetic: bool = False
) -> None:
    output.mkdir(mode=0o700)
    now = datetime.now(UTC)
    # Separate, explicitly opted-in disposable lane. Production and the original
    # five package smokes retain the inactive synthetic projector by default.
    tenant = "relay_" + secrets.token_hex(12) if relay_synthetic else TENANT

    def issue(name: str, ca_key=None, ca_cert=None, purpose=None):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject if ca_cert else subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=2))
            .add_extension(x509.BasicConstraints(ca=ca_cert is None, path_length=None), critical=True)
        )
        if purpose:
            builder = builder.add_extension(x509.ExtendedKeyUsage([purpose]), critical=True)
        if purpose == ExtendedKeyUsageOID.SERVER_AUTH:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
        cert = builder.sign(ca_key or key, hashes.SHA256())
        (output / f"{name}.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        (output / f"{name}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return key, cert

    ca_key, ca = issue("ca")
    issue("server", ca_key, ca, ExtendedKeyUsageOID.SERVER_AUTH)
    clients = {}
    for purpose in ("command", "authority"):
        clients[purpose], _ = issue(purpose, ca_key, ca, ExtendedKeyUsageOID.CLIENT_AUTH)
    alien_key, alien_ca = issue("untrusted-ca")
    issue("untrusted-client", alien_key, alien_ca, ExtendedKeyUsageOID.CLIENT_AUTH)
    trust_password = secrets.token_hex(24)
    (output / "truststore.p12").write_bytes(
        pkcs12.serialize_java_truststore(
            [pkcs12.PKCS12Certificate(ca, b"package-test-ca")],
            serialization.BestAvailableEncryption(trust_password.encode()),
        )
    )
    keys = []
    for purpose, client in clients.items():
        signing = ed25519.Ed25519PrivateKey.generate()
        (output / f"{purpose}-signing.key").write_bytes(
            signing.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        keys.append(
            {
                "id": f"package-{purpose}",
                "purpose": f"human-{purpose}",
                "workload": f"package-{purpose}-workload",
                "peer_spki_sha256": hashlib.sha256(public_bytes(client)).hexdigest(),
                "public_key_spki_base64": base64.b64encode(public_bytes(signing)).decode(),
                "not_before": str(int((now - timedelta(minutes=1)).timestamp())),
                "not_after": str(int((now + timedelta(hours=2)).timestamp())),
            }
        )
    dump(
        output / "trust.json",
        {
            "schema": "human-trust.v1",
            "tenant": tenant,
            "audience": "package-engine",
            "engine_name": "default",
            "max_lifetime_seconds": "60",
            "keys": keys,
            "enable_synthetic_fixture": relay_synthetic,
        },
    )
    database_password = secrets.token_hex(24)
    (output / "postgres-password").write_text(database_password)
    tree = ET.parse(original)
    server = tree.getroot()
    server.set("port", "-1")
    resources = server.findall("./GlobalNamingResources/Resource[@name='jdbc/ProcessEngine']")
    if len(resources) != 1:
        raise ValueError("pinned Tomcat datasource layout changed")
    resources[0].attrib.update(
        {
            "driverClassName": "org.postgresql.Driver",
            "url": "jdbc:postgresql://postgres:5432/maezo",
            "username": "maezo",
            "password": database_password,
        }
    )
    service = server.find("./Service[@name='Catalina']")
    if service is None or server.findall(".//Valve[@className='org.apache.catalina.valves.RemoteIpValve']"):
        raise ValueError("unexpected Tomcat layout or proxy identity trust")
    for connector in service.findall("Connector"):
        service.remove(connector)
    ET.SubElement(service, "Connector", port="8080", protocol="HTTP/1.1", connectionTimeout="5000")
    tls = ET.SubElement(
        service,
        "Connector",
        {
            "port": "8443",
            "protocol": "org.apache.coyote.http11.Http11NioProtocol",
            "SSLEnabled": "true",
            "scheme": "https",
            "secure": "true",
            "maxThreads": "20",
            "sslImplementationName": "org.apache.tomcat.util.net.jsse.JSSEImplementation",
        },
    )
    sslhost = ET.SubElement(
        tls,
        "SSLHostConfig",
        {
            "certificateVerification": "required",
            "protocols": "TLSv1.3",
            "truststoreFile": "/run/maezo/truststore.p12",
            "truststoreType": "PKCS12",
            "truststorePassword": trust_password,
        },
    )
    ET.SubElement(
        sslhost,
        "Certificate",
        {
            "certificateFile": "/run/maezo/server.crt",
            "certificateKeyFile": "/run/maezo/server.key",
            "type": "EC",
        },
    )
    tree.write(output / "server.xml", encoding="utf-8", xml_declaration=True)
    sql = checkout / "src/maezo/portal/engine/java/src/main/resources/human-schema-postgres.sql"
    (output / "01-human.sql").write_bytes(sql.read_bytes())
    (output / "02-tenant.sql").write_text(
        f"INSERT INTO MZO_HUMAN_TENANT(TENANT_,REV_) VALUES ('{tenant}',0);\n"
    )

    def mount(name: str, target: str) -> dict:
        return {
            "type": "bind",
            "source": str(output / name),
            "target": target,
            "read_only": True,
            "bind": {"create_host_path": False},
        }

    dump(
        output / "compose.json",
        {
            "services": {
                "postgres": {
                    "image": POSTGRES,
                    "ports": ["127.0.0.1:15433:5432"],
                    "environment": {
                        "POSTGRES_USER": "maezo",
                        "POSTGRES_DB": "maezo",
                        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password",
                    },
                    "volumes": [
                        mount("postgres-password", "/run/secrets/postgres-password"),
                        mount("01-human.sql", "/docker-entrypoint-initdb.d/01-human.sql"),
                        mount("02-tenant.sql", "/docker-entrypoint-initdb.d/02-tenant.sql"),
                    ],
                    "healthcheck": {
                        "test": ["CMD", "pg_isready", "-U", "maezo", "-d", "maezo"],
                        "interval": "2s",
                        "timeout": "2s",
                        "retries": 45,
                    },
                },
                "engine": {
                    "image": image,
                    "user": "0:0",  # Disposable fixture can read host mode-0600 mounts.
                    "ports": ["127.0.0.1:18080:8080", "127.0.0.1:18443:8443"],
                    "environment": {
                        "SKIP_DB_CONFIG": "true",
                        "MAEZO_HUMAN_TRUST_FILE": "/run/maezo/trust.json",
                        "JAVA_OPTS": "-Xms128m -Xmx512m",
                    },
                    "volumes": [mount("server.xml", "/camunda/conf/server.xml")]
                    + [
                        mount(n, "/run/maezo/" + n)
                        for n in ("server.crt", "server.key", "truststore.p12", "trust.json")
                    ],
                    "depends_on": {"postgres": {"condition": "service_healthy"}},
                },
            }
        },
    )
    dump(
        output / "test-env.json",
        {
            "MAEZO_HUMAN_PACKAGE_HTTPS_URL": "https://localhost:18443",
            "MAEZO_HUMAN_PACKAGE_HTTP_URL": "http://localhost:18080",
            "MAEZO_HUMAN_PACKAGE_CA_FILE": str(output / "ca.crt"),
            "MAEZO_HUMAN_PACKAGE_CLIENT_CERT": str(output / "command.crt"),
            "MAEZO_HUMAN_PACKAGE_CLIENT_KEY": str(output / "command.key"),
            "MAEZO_HUMAN_PACKAGE_UNTRUSTED_CERT": str(output / "untrusted-client.crt"),
            "MAEZO_HUMAN_PACKAGE_UNTRUSTED_KEY": str(output / "untrusted-client.key"),
        },
    )
    dump(
        output / "public-receipt.json",
        {
            "source_sha": sha,
            "created_at": now.isoformat(),
            "fixture_only": True,
            "relay_synthetic": relay_synthetic,
            "base_server_xml_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
            "human_sql_sha256": hashlib.sha256(sql.read_bytes()).hexdigest(),
            "client_spki_sha256": {
                p: hashlib.sha256(public_bytes(k)).hexdigest() for p, k in clients.items()
            },
            "signing_spki_sha256": {
                k["purpose"]: hashlib.sha256(base64.b64decode(k["public_key_spki_base64"])).hexdigest()
                for k in keys
            },
            "tls": "TLSv1.3",
            "certificate_verification": "required",
            "engine_image": image,
            "postgres_image": POSTGRES,
        },
    )
    if relay_synthetic:
        dump(
            output / "relay-fixture.json",
            {
                "schema": "human-relay-fixture.v1",
                "synthetic_opt_in": True,
                "tenant": tenant,
                "source_sha": sha,
                "rest_url": "http://localhost:18080/engine-rest",
                "human_url": "https://localhost:18443/maezo-human",
                "database": {
                    "host": "127.0.0.1",
                    "port": 15433,
                    "user": "maezo",
                    "database": "maezo",
                    "password_file": str(output / "postgres-password"),
                },
            },
        )
    for path in output.iterdir():
        path.chmod(0o600)
    # PostgreSQL drops to its own UID before reading public schema/bootstrap SQL.
    for name in ("01-human.sql", "02-tenant.sql"):
        (output / name).chmod(0o444)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--base-server-xml", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--relay-synthetic", action="store_true", help="opt in to disposable nonclinical D6 relay tests"
    )
    args = parser.parse_args()
    prepare(
        args.checkout,
        args.sha,
        args.base_server_xml,
        args.private_root,
        args.evidence_root,
        args.output,
        args.image,
        relay_synthetic=args.relay_synthetic,
    )
    print("Private disposable fixture prepared; no services started and no tests executed.")
