"""`assignment-plane` — o trust `human-assignment-trust.v1` do engine (Onda 8, plano de atribuicao).

O portal so liga o plano humano com a fonte de atribuicao ATIVA (`portal_assignment_source.state=
'active'`, `assignment_transport._active`), e ela so fica ativa com o ACK do engine a uma publicacao
`human-assignment-publication.v1` (`AssignmentPublication.java`). O engine aceita essa publicacao so
com `MAEZO_HUMAN_ASSIGNMENT_TRUST_FILE` (`HumanCommandPlugin.preInit`), que este modulo monta:

``assignment-plane source-key``  gera a chave Ed25519 da FONTE revisada (proposito
                                 `human-staff-assignment-source`, a do `StaffAssignmentSourceSigner`).
                                 Saida: ``private/assignment-source-key.pem`` (0400) e
                                 ``public/assignment-source.json`` (SPKI, fingerprint, key_id).
``assignment-plane trust``       monta ``assignment-trust.json`` so de partes PUBLICAS:
                                 * `publisher` = a chave `human-authority` do `trust.json` do
                                   native-secret (a do job T1.5: ele ja publica principal/evidencia);
                                 * `read_keys` = a chave `human-assignment-read` do pacote humano
                                   (`human-keys-summary.json`; no dev, `amh-dev-assignment-20260924`),
                                   amarrada ao peer `read` do pacote;
                                 * `source` = a chave publica do `source-key`;
                                 * nenhuma policy/binding aprovada (a 1a geracao so carrega as
                                   memberships: claim/release/reassign continuam recusados).

Nada aqui ve chave privada alem da que o `source-key` gera; o digest impresso e o
`CONFIGURATION_DIGEST_` que a linha `MZO_HUMAN_ASSIGNMENT_INSTALLATION` pina (`AssignmentInstallation`).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from maezo.gateway.external_cases.models import timestamp
from maezo.portal.engine.profile import canonicalize, strict_loads

from .secure_io import PRIVATE, PUBLIC, MaterialError, new_private_directory, subdirectory, write_new

SCHEMA = "human-assignment-trust.v1"
SPEC_SCHEMA = "staff-materials-assignment-trust.v1"
SOURCE_SCHEMA = "staff-materials-assignment-source.v1"
SOURCE_KEY_FILE = "assignment-source-key.pem"
SOURCE_PUBLIC_FILE = "assignment-source.json"
TRUST_FILE = "assignment-trust.json"
_SPEC_KEYS = {
    "schema",
    "tenant",
    "environment",
    "engine_name",
    "database_incarnation",
    "deployment_receipt",
    "validity_policy",
    "owner_ref",
    "source_ref",
    "not_before",
    "not_after",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _spki(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _epoch(value: str) -> str:
    return str(int(timestamp(value).timestamp()))


def _pin(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"artifact_ref", "digest"}:
        raise MaterialError(f"{where}: esperado {{artifact_ref, digest}}")
    if len(str(value["digest"])) != 64 or any(c not in "0123456789abcdef" for c in str(value["digest"])):
        raise MaterialError(f"{where}: digest nao e SHA-256 hex")
    return {"artifact_ref": str(value["artifact_ref"]), "digest": str(value["digest"])}


def new_source_key(key_id: str) -> tuple[bytes, dict[str, str]]:
    """(PEM PKCS8 privado, resumo publico). Nao escreve disco."""
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    spki = _spki(key)
    public = dict(
        schema=SOURCE_SCHEMA,
        key_id=key_id,
        public_key_spki_base64=base64.b64encode(spki).decode("ascii"),
        fingerprint=_sha256(spki),
    )
    return private, public


def build_trust(
    spec: dict[str, Any], human_trust: dict[str, Any], human_keys: dict[str, Any], source: dict[str, Any]
) -> tuple[bytes, str]:
    """(bytes JCS do `human-assignment-trust.v1`, SHA-256 = `AssignmentTrust.digest`)."""
    if set(spec) != _SPEC_KEYS or spec["schema"] != SPEC_SCHEMA:
        raise MaterialError(f"spec: esperado {SPEC_SCHEMA} com {sorted(_SPEC_KEYS)}")
    if human_trust.get("schema") != "human-trust.v1":
        raise MaterialError("--human-trust nao e o trust.json (human-trust.v1) do native-secret")
    if (human_trust["tenant"], human_trust["engine_name"]) != (spec["tenant"], spec["engine_name"]):
        raise MaterialError("tenant/engine do spec divergem do trust humano instalado")
    scope = human_keys.get("scope") or {}
    if (scope.get("tenant"), scope.get("environment"), human_keys.get("engine_name")) != (
        spec["tenant"],
        spec["environment"],
        spec["engine_name"],
    ) or human_keys.get("database_incarnation") != spec["database_incarnation"]:
        raise MaterialError("escopo do pacote humano diverge do spec")
    if source.get("schema") != SOURCE_SCHEMA:
        raise MaterialError("--source nao e o assignment-source.json do source-key")
    not_before, not_after = _epoch(spec["not_before"]), _epoch(spec["not_after"])
    if int(not_after) <= int(not_before):
        raise MaterialError("janela invalida")
    authority = [k for k in human_trust["keys"] if k["purpose"] == "human-authority"]
    if len(authority) != 1:
        raise MaterialError("o trust humano precisa de exatamente uma chave human-authority")
    publisher_fp = _sha256(base64.b64decode(authority[0]["public_key_spki_base64"]))
    read = human_keys["keys"]["human-assignment-read"]
    read_peer = human_keys["peers"]["read"]
    # As recusas do AssignmentTrust.java, antes de o engine ver o arquivo: nenhuma chave repetida
    # entre os propositos e o peer/workload da leitura de atribuicao fora do trust humano.
    human_fps = {_sha256(base64.b64decode(k["public_key_spki_base64"])) for k in human_trust["keys"]}
    source_fp = _sha256(base64.b64decode(source["public_key_spki_base64"]))
    if source_fp != source["fingerprint"] or read["fingerprint"] != _sha256(
        base64.b64decode(read["public_key_spki_base64"])
    ):
        raise MaterialError("fingerprint nao confere com o SPKI")
    if len(human_fps | {source_fp, read["fingerprint"]}) != len(human_fps) + 2:
        raise MaterialError("fonte, leitura de atribuicao e trust humano precisam de chaves proprias")
    if any(
        k["workload"] == read["workload_ref"] or k["peer_spki_sha256"] == read_peer
        for k in human_trust["keys"]
    ) or any(k["id"] in (read["key_id"], source["key_id"]) for k in human_trust["keys"]):
        raise MaterialError(
            "a leitura de atribuicao nao pode reusar workload, peer ou key_id do trust humano"
        )
    value = dict(
        schema=SCHEMA,
        tenant=spec["tenant"],
        environment=spec["environment"],
        engine_name=spec["engine_name"],
        database_incarnation=spec["database_incarnation"],
        deployment_receipt=_pin(spec["deployment_receipt"], "deployment_receipt"),
        validity_policy=_pin(spec["validity_policy"], "validity_policy"),
        valid_until=not_after,
        source=dict(
            owner_ref=spec["owner_ref"],
            source_ref=spec["source_ref"],
            key_id=source["key_id"],
            public_key_spki_base64=source["public_key_spki_base64"],
            not_before=not_before,
            not_after=not_after,
        ),
        publisher=dict(workload_ref=authority[0]["workload"], key_fingerprint=publisher_fp),
        approved_policy_pins=[],
        approved_binding_pins=[],
        read_keys=[
            dict(
                id=read["key_id"],
                workload=read["workload_ref"],
                peer_spki_sha256=read_peer,
                public_key_spki_base64=read["public_key_spki_base64"],
                not_before=not_before,
                not_after=not_after,
            )
        ],
        receipt_disclosure_source=None,
        approved_receipt_policy_pins=[],
    )
    raw = canonicalize(value)
    return raw, _sha256(raw)


def add_parser(commands: Any) -> None:
    plane = commands.add_parser("assignment-plane", help="trust do plano de atribuicao (Onda 8)")
    steps = plane.add_subparsers(dest="assignment_step", required=True)
    key = steps.add_parser("source-key", help="chave Ed25519 da fonte revisada de atribuicao")
    key.add_argument("--key-id", required=True)
    key.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")
    trust = steps.add_parser("trust", help="monta assignment-trust.json (so partes publicas)")
    trust.add_argument("--spec", type=Path, required=True, help=SPEC_SCHEMA)
    trust.add_argument("--human-trust", type=Path, required=True, help="trust.json do native-secret")
    trust.add_argument("--human-keys", type=Path, required=True, help="public/human-keys-summary.json")
    trust.add_argument("--source", type=Path, required=True, help="public/assignment-source.json")
    trust.add_argument("--out", type=Path, required=True, help="diretorio NOVO, fora do repositorio")


def run(args: argparse.Namespace) -> int:
    from .generate import REPO

    if args.assignment_step == "source-key":
        private, public = new_source_key(args.key_id)
        out = new_private_directory(args.out, forbidden=(REPO,))
        write_new(subdirectory(out, "private") / SOURCE_KEY_FILE, private, PRIVATE)
        write_new(subdirectory(out, "public") / SOURCE_PUBLIC_FILE, canonicalize(public), PUBLIC)
        print(f"saida={out}")
        print(f"source_key_sha256={public['fingerprint']} key_id={public['key_id']}")
        return 0
    raw, digest = build_trust(
        strict_loads(args.spec.read_bytes()),
        strict_loads(args.human_trust.read_bytes()),
        strict_loads(args.human_keys.read_bytes()),
        strict_loads(args.source.read_bytes()),
    )
    out = new_private_directory(args.out, forbidden=(REPO,))
    write_new(out / TRUST_FILE, raw, PUBLIC)
    print(f"saida={out}")
    print(f"assignment_configuration_digest={digest} (CONFIGURATION_DIGEST_ da instalacao)")
    return 0
