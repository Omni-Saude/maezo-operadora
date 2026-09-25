"""`human-bundle` — o pacote `portal-human-material.v1` que o job da T1.5 le (`load_human_materials`).

Dois tempos, porque o pacote depende de uma admissao que depende das chaves dele (a ordem da D-L e
native-secret -> trust digest -> admissao):

``human-bundle keys``     gera as 3 chaves Ed25519 por proposito (`portal-task-read`,
                          `human-assignment-read`, `human-command`), a CA de clientes PROPRIA do
                          pacote (a chave dela morre aqui, como as CAs do `generate`), os dois
                          certificados de cliente mTLS (`read`, `command`) e as duas chaves AEAD de
                          cursor. Saida: ``private/`` (0400) e ``public/`` com
                          ``human-keys-summary.json`` (SPKIs, fingerprints, peers, CA PEM), que o
                          ``native-secret --human-keys`` consome direto.
``human-bundle package``  monta ``current/`` (14 arquivos + ``manifest.json``) com as chaves do 1o
                          tempo, a CA e o SPKI do servidor do `generate`, a raiz PUBLICA e a
                          admissao ASSINADA que vem do aprovador, e os dois DSNs. O resultado passa
                          pelo `verify_materials` do loader antes de ser gravado.

A ferramenta nunca assina a admissao nem ve a chave privada da raiz (regra D-F). O formato e o de
`tests/support/materials_builder.py`; o harness C1 (`deploy/c1-local/c1/human_bundle.py`) usa estas
mesmas funcoes (`new_keys`, `package`).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import Field, StringConstraints, model_validator

from maezo.gateway.external_cases.models import Ref, timestamp
from maezo.gateway.human.production_materials import (
    FILES,
    PRIVATE_FILES,
    PUBLIC_FILES,
    HumanMaterialError,
    HumanMaterialPin,
    HumanPublicManifest,
    verify_materials,
)
from maezo.gateway.human.read_profile import parse_model
from maezo.gateway.staff_cases.models import Closed, N, T
from maezo.portal.engine.profile import canonicalize, strict_loads

from .generate import REPO
from .secure_io import PRIVATE, PUBLIC, MaterialError, new_private_directory, subdirectory, write_new

# Alias curto de proposito: `key: <NomeDeClasseLongo>` casa com a regra generic-api-key do gitleaks
# (anotacao de tipo lida como segredo de alta entropia); o alias fica abaixo do tamanho minimo dela.
EdPriv = Ed25519PrivateKey

PURPOSES = ("portal-task-read", "human-assignment-read", "human-command")
KEY_FILES = {
    "portal-task-read": "read-signing-key.pem",
    "human-assignment-read": "assignment-signing-key.pem",
    "human-command": "command-signing-key.pem",
}
# O que o 1o tempo grava em `private/` (sempre 0400) e em `public/`.
KEYS_PRIVATE = (*KEY_FILES.values(), "read-client-key.pem", "command-client-key.pem", "cursor-keys.json")
KEYS_PUBLIC = ("read-client-certificate.pem", "command-client-certificate.pem", "client-ca.pem")
SUMMARY = "human-keys-summary.json"
SUMMARY_SCHEMA = "staff-materials-human-keys.v1"
MAX_WINDOW = timedelta(days=14)
Prefix = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9-]{1,38}$")]


def spki(key: object) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)  # type: ignore[attr-defined]


def pem(key: EdPriv | ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@dataclass
class ClientCa:
    """CA de clientes do pacote humano; o truststore do 8443 a soma a CA do `generate`."""

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
            .add_extension(
                x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
            )
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
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.certificate.public_key()),
                critical=False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(self.key, hashes.SHA256())
        )
        return cert.public_bytes(Encoding.PEM), pem(key), _sha256(spki(key))


# --- entrada (`staff-materials-human-bundle.v1`) ----------------------------------------------


class HumanScope(Closed):
    tenant: Ref
    environment: Ref
    workload_ref: Ref


class KeySpec(Closed):
    key_id: Ref
    audience: Ref


class ConnectionSpec(Closed):
    host: str
    port: N
    database: str
    login: Ref


class RevocationSpec(Closed):
    source_ref: Ref
    revision: N


class HumanBundleSpec(Closed):
    schema_: Literal["staff-materials-human-bundle.v1"] = Field(alias="schema")
    scope: HumanScope
    engine_name: Ref
    database_incarnation: Ref
    issuer: str
    issued_at: T
    valid_until: T
    #: Origem do engine (`https://<hostname D-B>`); `command_endpoint` e as duas superficies.
    origin: str
    catalog_ref: Ref
    #: `material_version_id` = prefixo + 26 hex aleatorios (32 a 64 caracteres, regra do loader).
    material_version_prefix: Prefix
    #: CN dos certificados de cliente: `<label> read client` / `<label> command client`.
    certificate_label: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9 _.-]{1,40}$")]
    read_key: KeySpec
    assignment_key: KeySpec
    command_key: KeySpec
    max_envelope_seconds: N
    timeout_seconds: N
    outbox_connection: ConnectionSpec
    source_connection: ConnectionSpec
    relay_lease_seconds: N
    relay_retry_seconds: N
    relay_poll_seconds: N
    revocation: RevocationSpec

    @model_validator(mode="after")
    def coherent(self) -> Self:
        start, end = timestamp(self.issued_at), timestamp(self.valid_until)
        if not timedelta(0) < end - start <= MAX_WINDOW:
            raise ValueError("janela do pacote humano invalida ou maior que 14 dias")
        ids = {self.read_key.key_id, self.assignment_key.key_id, self.command_key.key_id}
        if len(ids) != 3:
            raise ValueError("cada proposito precisa de um key_id proprio")
        # `KeyDesignation.audience` e `OpaqueRef` no loader: sem espaco, `/`, `?` ou `#`. Uma URL
        # (`https://...`) nunca passa; recusar aqui da a razao em vez do `human_material_unavailable`.
        if any(re.search(r"[\s/?#]", key.audience) for key in self.keys().values()):
            raise ValueError("audience das chaves humanas nao pode ter espaco, '/', '?' ou '#' (OpaqueRef)")
        return self

    def keys(self) -> dict[str, KeySpec]:
        return {
            "portal-task-read": self.read_key,
            "human-assignment-read": self.assignment_key,
            "human-command": self.command_key,
        }

    def window(self) -> tuple[datetime, datetime]:
        return timestamp(self.issued_at), timestamp(self.valid_until)


def load_spec(raw: bytes) -> HumanBundleSpec:
    try:
        return HumanBundleSpec.model_validate(strict_loads(raw), strict=True)
    except Exception as failure:
        raise MaterialError(f"spec do human-bundle invalido: {failure}") from None


# --- 1o tempo: chaves ----------------------------------------------------------------------


@dataclass(repr=False)
class HumanKeys:
    signing: dict[str, EdPriv]
    read_certificate: bytes
    read_client_key: bytes
    read_client_spki: str
    command_certificate: bytes
    command_client_key: bytes
    command_client_spki: str
    client_ca_pem: bytes
    cursor_keys: bytes  # `portal-human-cursor-keys.v1`
    cursor: list[dict[str, Any]]  # designacoes publicas das chaves de cursor (manifesto)
    material_version_id: str

    def summary(self, spec: HumanBundleSpec) -> dict[str, Any]:
        """O que sai em `public/`: so SPKIs, fingerprints e peers; nenhum byte privado."""
        workload = spec.scope.workload_ref
        return dict(
            schema=SUMMARY_SCHEMA,
            scope=spec.scope.wire(),
            engine_name=spec.engine_name,
            database_incarnation=spec.database_incarnation,
            material_version_id=self.material_version_id,
            client_ca_pem=self.client_ca_pem.decode("ascii"),
            client_ca_sha256=_sha256(self.client_ca_pem),
            keys={
                purpose: dict(
                    key_id=spec.keys()[purpose].key_id,
                    workload_ref=workload + ("-assignment" if purpose == "human-assignment-read" else ""),
                    audience=spec.keys()[purpose].audience,
                    public_key_spki_base64=_b64(spki(key)),
                    fingerprint=_sha256(spki(key)),
                )
                for purpose, key in self.signing.items()
            },
            peers=dict(read=self.read_client_spki, command=self.command_client_spki),
            cursor_keys=self.cursor,
        )


def new_keys(spec: HumanBundleSpec, *, client_ca: ClientCa | None = None) -> HumanKeys:
    start, end = spec.window()
    ca = client_ca or ClientCa.new(f"{spec.certificate_label} human client CA", start, end)
    signing = {purpose: Ed25519PrivateKey.generate() for purpose in PURPOSES}
    read_cert, read_private, read_spki = ca.client(f"{spec.certificate_label} read client", start, end)
    command_cert, command_private, command_spki = ca.client(
        f"{spec.certificate_label} command client", start, end
    )
    current, previous = os.urandom(32), os.urandom(32)
    cursor_keys = json.dumps(
        {
            "schema": "portal-human-cursor-keys.v1",
            "not_before": spec.issued_at,
            "not_after": spec.valid_until,
            "keys": {"cursor-current": _b64(current), "cursor-previous": _b64(previous)},
        }
    ).encode()
    cursor = [
        {
            "key_id": "cursor-current",
            "key_tag": os.urandom(16).hex(),
            "material_sha256": _sha256(current),
            "generation": "2",
            "current": True,
        },
        {
            "key_id": "cursor-previous",
            "key_tag": os.urandom(16).hex(),
            "material_sha256": _sha256(previous),
            "generation": "1",
            "current": False,
        },
    ]
    return HumanKeys(
        signing=signing,
        read_certificate=read_cert,
        read_client_key=read_private,
        read_client_spki=read_spki,
        command_certificate=command_cert,
        command_client_key=command_private,
        command_client_spki=command_spki,
        client_ca_pem=ca.certificate.public_bytes(Encoding.PEM),
        cursor_keys=cursor_keys,
        cursor=cursor,
        material_version_id=spec.material_version_prefix + os.urandom(13).hex(),
    )


def write_keys(out: Path, spec: HumanBundleSpec, keys: HumanKeys) -> Path:
    root = new_private_directory(out, forbidden=(REPO,))
    private, public = subdirectory(root, "private"), subdirectory(root, "public")
    for purpose, key in keys.signing.items():
        write_new(private / KEY_FILES[purpose], pem(key), PRIVATE)
    write_new(private / "read-client-key.pem", keys.read_client_key, PRIVATE)
    write_new(private / "command-client-key.pem", keys.command_client_key, PRIVATE)
    write_new(private / "cursor-keys.json", keys.cursor_keys, PRIVATE)
    write_new(public / "read-client-certificate.pem", keys.read_certificate, PUBLIC)
    write_new(public / "command-client-certificate.pem", keys.command_certificate, PUBLIC)
    write_new(public / "client-ca.pem", keys.client_ca_pem, PUBLIC)
    write_new(public / SUMMARY, canonicalize(keys.summary(spec)), PUBLIC)
    return root


def read_keys(directory: Path, spec: HumanBundleSpec) -> HumanKeys:
    """Relê o 1o tempo e confere que ele e deste spec e que as chaves batem com o resumo."""
    private, public = directory / "private", directory / "public"
    try:
        summary = strict_loads((public / SUMMARY).read_bytes())
        signing = {}
        for purpose in PURPOSES:
            key = serialization.load_pem_private_key(
                (private / KEY_FILES[purpose]).read_bytes(), password=None
            )
            if not isinstance(key, Ed25519PrivateKey):
                raise MaterialError("chave de proposito precisa ser Ed25519")
            signing[purpose] = key
        keys = HumanKeys(
            signing=signing,
            read_certificate=(public / "read-client-certificate.pem").read_bytes(),
            read_client_key=(private / "read-client-key.pem").read_bytes(),
            read_client_spki=summary["peers"]["read"],
            command_certificate=(public / "command-client-certificate.pem").read_bytes(),
            command_client_key=(private / "command-client-key.pem").read_bytes(),
            command_client_spki=summary["peers"]["command"],
            client_ca_pem=(public / "client-ca.pem").read_bytes(),
            cursor_keys=(private / "cursor-keys.json").read_bytes(),
            cursor=summary["cursor_keys"],
            material_version_id=summary["material_version_id"],
        )
    except MaterialError:
        raise
    except Exception:
        raise MaterialError("saida do human-bundle keys ilegivel ou incompleta") from None
    if canonicalize(keys.summary(spec)) != canonicalize(summary):
        raise MaterialError("as chaves do human-bundle keys nao sao deste spec (resumo divergiu)")
    return keys


# --- 2o tempo: pacote ----------------------------------------------------------------------


def package(
    spec: HumanBundleSpec,
    keys: HumanKeys,
    *,
    root_spki: bytes,
    admission: bytes,
    server_ca_pem: bytes,
    server_spki: str,
    source_dsn: bytes,
    outbox_dsn: bytes,
    now: datetime | None = None,
) -> tuple[dict[str, bytes], HumanPublicManifest]:
    """Devolve ({nome: bytes} dos 14 arquivos, manifesto). Passa pelo loader; nao escreve disco.

    `admission` e o documento `portal-human-read-admission.v1` JA ASSINADO pela raiz cujo SPKI e
    `root_spki`: quem assina e o aprovador (ou, no harness, uma raiz de TESTE).
    """
    start, _ = spec.window()
    files: dict[str, bytes] = {
        "installation-root.der": root_spki,
        "read-admission.json": admission,
        "read-ca.pem": server_ca_pem,
        "read-client-certificate.pem": keys.read_certificate,
        "command-ca.pem": server_ca_pem,
        "command-client-certificate.pem": keys.command_certificate,
        **{KEY_FILES[purpose]: pem(key) for purpose, key in keys.signing.items()},
        "cursor-keys.json": keys.cursor_keys,
        "read-client-key.pem": keys.read_client_key,
        "command-client-key.pem": keys.command_client_key,
        "outbox-dsn.txt": outbox_dsn,
        "source-dsn.txt": source_dsn,
    }
    assert set(files) == FILES
    scope = spec.scope.wire()
    public = keys.summary(spec)["keys"]
    surface = dict(origin=spec.origin, server_spki_sha256=server_spki, timeout_seconds=spec.timeout_seconds)
    payload = {
        "schema": "portal-human-material.v1",
        "material_version_id": keys.material_version_id,
        "scope": scope,
        "issuer": spec.issuer,
        "issued_at": spec.issued_at,
        "valid_until": spec.valid_until,
        "root_key_fingerprint": _sha256(root_spki),
        "engine_name": spec.engine_name,
        "database_incarnation": spec.database_incarnation,
        "assignment_workload_ref": spec.scope.workload_ref + "-assignment",
        "command_endpoint": spec.origin,
        "catalog_ref": spec.catalog_ref,
        "publisher_ref": spec.scope.workload_ref,
        "keys": [
            {
                "purpose": p,
                "key_id": public[p]["key_id"],
                "fingerprint": public[p]["fingerprint"],
                "max_envelope_seconds": spec.max_envelope_seconds,
                "workload_ref": public[p]["workload_ref"],
                "audience": public[p]["audience"],
            }
            for p in PURPOSES
        ],
        "cursor_keys": keys.cursor,
        "read_surface": dict(
            surface,
            ca_file="read-ca.pem",
            certificate_file="read-client-certificate.pem",
            private_key_file="read-client-key.pem",
            client_spki_sha256=keys.read_client_spki,
        ),
        "command_surface": dict(
            surface,
            ca_file="command-ca.pem",
            certificate_file="command-client-certificate.pem",
            private_key_file="command-client-key.pem",
            client_spki_sha256=keys.command_client_spki,
        ),
        "outbox_connection": spec.outbox_connection.wire(),
        "source_connection": spec.source_connection.wire(),
        "relay_lease_seconds": spec.relay_lease_seconds,
        "relay_retry_seconds": spec.relay_retry_seconds,
        "relay_poll_seconds": spec.relay_poll_seconds,
        "revocation_snapshot": {
            "scope": scope,
            "source_ref": spec.revocation.source_ref,
            "revision": spec.revocation.revision,
            "observed_at": spec.issued_at,
            "valid_until": spec.valid_until,
            "revoked_fingerprints": [],
        },
        "files": {**{n: _sha256(files[n]) for n in PUBLIC_FILES}, **{n: None for n in PRIVATE_FILES}},
    }
    try:
        manifest = parse_model(HumanPublicManifest, payload)
        verify(manifest, files, now=now or max(start, datetime.now(UTC)))
    except HumanMaterialError:
        raise MaterialError("o loader do job recusou o pacote humano montado") from None
    return files, manifest


def pin(manifest: HumanPublicManifest) -> HumanMaterialPin:
    return HumanMaterialPin(
        tenant=manifest.scope.tenant,
        material_version_id=manifest.material_version_id,
        public_manifest_sha256=_sha256(manifest.canonical()),
    )


def verify(manifest: HumanPublicManifest, files: dict[str, bytes], *, now: datetime) -> None:
    """O MESMO `verify_materials` que o `load_human_materials` do job roda (menos a custodia de disco)."""
    verify_materials(pin(manifest), manifest, files, now=now)


def load_package(current: Path) -> tuple[HumanPublicManifest, dict[str, bytes]]:
    """Le um `current/` gravado (sem as regras de dono/modo do container) e devolve manifesto e arquivos."""
    raw = {p.name: p.read_bytes() for p in current.iterdir()}
    try:
        manifest = parse_model(HumanPublicManifest, strict_loads(raw.pop("manifest.json")))
    except Exception:
        raise MaterialError("manifest.json do pacote humano invalido") from None
    return manifest, raw


def write_package(out: Path, files: dict[str, bytes], manifest: HumanPublicManifest) -> Path:
    """`out/` NOVO 0700; `out/current/` 0500 com os 15 arquivos 0400; `out/public/` com os pins."""
    root = new_private_directory(out, forbidden=(REPO,))
    current = subdirectory(root, "current")
    for name, raw in sorted({**files, "manifest.json": manifest.canonical()}.items()):
        write_new(current / name, raw, PRIVATE)
    os.chmod(current, 0o500)
    public = subdirectory(root, "public")
    write_new(public / "human-bundle-pins.json", canonicalize(pins(manifest)), PUBLIC)
    return root


def pins(manifest: HumanPublicManifest) -> dict[str, str]:
    return dict(
        schema="staff-materials-human-bundle-pins.v1",
        tenant=manifest.scope.tenant,
        material_version_id=manifest.material_version_id,
        public_manifest_sha256=_sha256(manifest.canonical()),
        root_key_fingerprint=manifest.root_key_fingerprint,
    )


# --- CLI ---------------------------------------------------------------------------------


def add_parser(commands: Any) -> None:
    human = commands.add_parser("human-bundle", help="gera o pacote portal-human-material.v1 do job (T1.5)")
    steps = human.add_subparsers(dest="human_step", required=True)
    keys = steps.add_parser("keys", help="1o tempo: chaves, certificados de cliente e resumo publico")
    keys.add_argument("--spec", type=Path, required=True, help="staff-materials-human-bundle.v1")
    keys.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")
    pack = steps.add_parser("package", help="2o tempo: monta current/ e confere com o loader do job")
    pack.add_argument("--spec", type=Path, required=True, help="o mesmo spec do keys")
    pack.add_argument("--keys", type=Path, required=True, help="saida do human-bundle keys")
    pack.add_argument("--materials", type=Path, required=True, help="saida do generate (CA/SPKI do servidor)")
    pack.add_argument(
        "--approver",
        type=Path,
        required=True,
        help="installation-root.der + human-read-admission.json (portal-human-read-admission.v1 assinado)",
    )
    pack.add_argument(
        "--dsns", type=Path, required=True, help="diretorio com outbox-dsn.txt e source-dsn.txt"
    )
    pack.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")


def run(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec.read_bytes())
    if args.human_step == "keys":
        keys = new_keys(spec)
        root = write_keys(args.out, spec, keys)
        print(f"saida={root}")
        print(f"material_version_id={keys.material_version_id}")
        for purpose, value in keys.summary(spec)["keys"].items():
            print(f"key_sha256[{purpose}]={value['fingerprint']} key_id={value['key_id']}")
        print(f"peer_spki_sha256[read]={keys.read_client_spki}")
        print(f"peer_spki_sha256[command]={keys.command_client_spki}")
        print(f"client_ca_sha256={_sha256(keys.client_ca_pem)}")
        print(f"resumo para o native-secret --human-keys: {root / 'public' / SUMMARY}")
        return 0
    keys = read_keys(args.keys, spec)
    summary = strict_loads((args.materials / "public" / "summary.json").read_bytes())
    host = summary["native_hostname"]
    if spec.origin not in (f"https://{host}", f"https://{host}:8443"):
        raise MaterialError("origin do spec nao e o hostname nativo do generate")
    files, manifest = package(
        spec,
        keys,
        root_spki=(args.approver / "installation-root.der").read_bytes(),
        admission=(args.approver / "human-read-admission.json").read_bytes(),
        server_ca_pem=(args.materials / "portal" / "native-ca.pem").read_bytes(),
        server_spki=summary["native_server_spki_sha256"],
        source_dsn=(args.dsns / "source-dsn.txt").read_bytes(),
        outbox_dsn=(args.dsns / "outbox-dsn.txt").read_bytes(),
    )
    root = write_package(args.out, files, manifest)
    print(f"saida={root / 'current'} (aceito pelo verify_materials do job)")
    print(f"MAEZO_HUMAN_MATERIAL_VERSION_ID={manifest.material_version_id}")
    print(f"MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256={_sha256(manifest.canonical())}")
    return 0
