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

                                 * EXIGE o documento de dono `staff-assignment-owner.v1` assinado pela
                                   raiz (`approver sign-assignment-owner`, D-O): confere a assinatura
                                   contra `installation-root.der`, cada campo contra o spec e a chave
                                   da fonte, e a janela; grava `assignment-owner-receipt.json`
                                   (`{artifact_ref, digest}`, digest = SHA-256 do arquivo assinado, que
                                   e JCS) - o `owner_receipt` do segredo da ativacao.

Nada aqui ve chave privada alem da que o `source-key` gera; o digest impresso e o
`CONFIGURATION_DIGEST_` que a linha `MZO_HUMAN_ASSIGNMENT_INSTALLATION` pina (`AssignmentInstallation`).
O `owner_receipt` nao entra no trust do engine (o Java nao o le): e o pin que a administracao da fonte
(`approved_owner_receipts`) exige no `prepare_change`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from maezo.gateway.external_cases.models import timestamp
from maezo.portal.engine.profile import canonicalize, strict_loads

from .secure_io import PRIVATE, PUBLIC, MaterialError, new_private_directory, subdirectory, write_new

SCHEMA = "human-assignment-trust.v1"
SPEC_SCHEMA = "staff-materials-assignment-trust.v1"
SOURCE_SCHEMA = "staff-materials-assignment-source.v1"
SOURCE_KEY_FILE = "assignment-source-key.pem"
SOURCE_PUBLIC_FILE = "assignment-source.json"
TRUST_FILE = "assignment-trust.json"
OWNER_SCHEMA = "staff-assignment-owner.v1"
OWNER_PROOF_SCHEMA = "staff-assignment-owner-proof.v1"
#: Dominio de assinatura proprio: a raiz nunca assina estes bytes em outro contexto.
OWNER_DOMAIN = b"maezo/staff-assignment-owner/v1\x00"
OWNER_RECEIPT_FILE = "assignment-owner-receipt.json"
OWNER_MAX_WINDOW = timedelta(days=14)  # N2: a mesma janela maxima da designacao/admissao
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


_Ref = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")]
_Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_Instant = Annotated[
    str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$")
]


class AssignmentOwner(BaseModel):
    """`staff-assignment-owner.v1`: o dono revisado da fonte de atribuicao (formato fechado)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=False)

    schema_: Literal["staff-assignment-owner.v1"] = Field(alias="schema")
    owner_ref: _Ref
    source_ref: _Ref
    tenant: _Ref
    environment: _Ref
    engine_name: _Ref
    database_incarnation: _Ref
    source_key_fingerprint: _Hex
    not_before: _Instant
    valid_until: _Instant


def parse_owner(raw: bytes) -> tuple[dict[str, Any], str, list[str]]:
    """(documento, SHA-256 do JCS, linhas de revisao). Recusa extra, faltante, nao-JCS e janela ruim."""
    try:
        value = strict_loads(raw)
        if type(value) is not dict:
            raise ValueError
        AssignmentOwner.model_validate(value)
        start, end = timestamp(value["not_before"]), timestamp(value["valid_until"])
    except (ValueError, ValidationError):
        raise MaterialError(f"documento fora do formato fechado {OWNER_SCHEMA}") from None
    if canonicalize(value) != raw:
        raise MaterialError("o documento de dono precisa estar em JCS canonico")
    if not start < end or end - start > OWNER_MAX_WINDOW:
        raise MaterialError("janela do documento de dono invalida ou maior que 14 dias")
    lines = [f"{key}={canonicalize(value[key]).decode()}" for key in sorted(value)]
    return value, _sha256(raw), lines


def owner_proof(document: dict[str, Any], signature: bytes) -> bytes:
    """O arquivo assinado (JCS) `{schema, document, signature}`; seu SHA-256 e o `owner_receipt`."""
    return canonicalize(
        dict(schema=OWNER_PROOF_SCHEMA, document=document, signature=base64.b64encode(signature).decode())
    )


def verify_owner(
    signed: bytes, root_spki: bytes, *, expected: dict[str, str], now: datetime
) -> tuple[dict[str, Any], str]:
    """Confere o documento de dono assinado pela raiz. (documento, digest do `owner_receipt`)."""
    try:
        proof = strict_loads(signed)
    except ValueError:
        raise MaterialError("documento de dono assinado ilegivel") from None
    if (
        type(proof) is not dict
        or set(proof) != {"schema", "document", "signature"}
        or proof["schema"] != OWNER_PROOF_SCHEMA
    ):
        raise MaterialError(f"esperado {OWNER_PROOF_SCHEMA} com {{schema, document, signature}}")
    if canonicalize(proof) != signed:
        raise MaterialError("o documento de dono assinado precisa estar em JCS canonico")
    raw = canonicalize(proof["document"])
    document, _, _ = parse_owner(raw)
    try:
        root = serialization.load_der_public_key(root_spki)
        if not isinstance(root, Ed25519PublicKey) or type(proof["signature"]) is not str:
            raise ValueError
        root.verify(base64.b64decode(proof["signature"], validate=True), OWNER_DOMAIN + raw)
    except (ValueError, InvalidSignature):
        raise MaterialError("assinatura do documento de dono nao e da raiz instalada") from None
    for name, value in expected.items():
        if document[name] != value:
            raise MaterialError(f"documento de dono: {name} diverge do plano")
    if not timestamp(document["not_before"]) <= now < timestamp(document["valid_until"]):
        raise MaterialError("documento de dono fora da janela de validade")
    return document, _sha256(signed)


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
    spec: dict[str, Any],
    human_trust: dict[str, Any],
    human_keys: dict[str, Any],
    source: dict[str, Any],
    *,
    owner: bytes,
    root_spki: bytes,
    now: datetime,
) -> tuple[bytes, str, str]:
    """(JCS do `human-assignment-trust.v1`, SHA-256 = `AssignmentTrust.digest`, digest do `owner_receipt`).

    `owner` e o `staff-assignment-owner-proof.v1` assinado pela raiz; sem ele valido nada sai.
    """
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
    _, owner_digest = verify_owner(
        owner,
        root_spki,
        expected=dict(
            owner_ref=spec["owner_ref"],
            source_ref=spec["source_ref"],
            tenant=spec["tenant"],
            environment=spec["environment"],
            engine_name=spec["engine_name"],
            database_incarnation=spec["database_incarnation"],
            source_key_fingerprint=source_fp,
        ),
        now=now,
    )
    raw = canonicalize(value)
    return raw, _sha256(raw), owner_digest


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
    trust.add_argument("--owner", type=Path, required=True, help=f"{OWNER_PROOF_SCHEMA} assinado pela raiz")
    trust.add_argument("--root", type=Path, required=True, help="installation-root.der (a raiz publica)")
    trust.add_argument("--owner-receipt-ref", required=True, help="artifact_ref do owner_receipt")
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
    from maezo.gateway.external_cases.models import now_utc

    raw, digest, owner_digest = build_trust(
        strict_loads(args.spec.read_bytes()),
        strict_loads(args.human_trust.read_bytes()),
        strict_loads(args.human_keys.read_bytes()),
        strict_loads(args.source.read_bytes()),
        owner=args.owner.read_bytes(),
        root_spki=args.root.read_bytes(),
        now=now_utc(),
    )
    receipt = _pin(dict(artifact_ref=args.owner_receipt_ref, digest=owner_digest), "owner_receipt")
    out = new_private_directory(args.out, forbidden=(REPO,))
    write_new(out / TRUST_FILE, raw, PUBLIC)
    write_new(out / OWNER_RECEIPT_FILE, canonicalize(receipt), PUBLIC)
    print(f"owner_receipt={canonicalize(receipt).decode()} (o owner_receipt do segredo da ativacao)")
    print(f"saida={out}")
    print(f"assignment_configuration_digest={digest} (CONFIGURATION_DIGEST_ da instalacao)")
    return 0
