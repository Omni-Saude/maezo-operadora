"""`assemble` — monta o pacote `portal-staff-material.v2` a partir do que JA existe.

Entradas, todas fornecidas, nenhuma gerada aqui:

* a saida do `generate` (``portal/`` e ``public/summary.json``);
* o spec do `generate` (as conexoes session-lock/witness);
* ``installation-root.der`` e ``installation-proof.json``, escritos pelo APROVADOR na maquina
  dele. A raiz privada nunca passa por esta ferramenta: so a chave publica, que o proprio loader
  confere contra a prova (regra D-F);
* o arquivo ``staff-materials-assemble.v1`` com os fatos de implantacao (emissor, pins de
  funcao/relacao, snapshot de revogacao, schemas).

Antes de devolver, o manifesto passa pelo modelo fechado do loader (`PublicManifest`) e a
designacao e conferida no ponto que o loader exige: a entrada `read_requester` com
``operations == ["detail", "list"]`` e o `identity_verifier` com chave PROPRIA (D-H.2), distinta
da de leitura.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field

from maezo.gateway.external_cases.models import Digest, Ref, digest, parse
from maezo.gateway.staff_cases.authority import fingerprint
from maezo.gateway.staff_cases.models import Closed, N, T
from maezo.gateway.staff_cases.production_config import (
    FILES,
    PRIVATE_FILES,
    EngineSchema,
    FunctionPin,
    NativeRelationPin,
    NativeSchema,
    PublicManifest,
)
from maezo.portal.engine.profile import canonicalize, strict_loads

from .secure_io import MaterialError
from .spec import MaterialsSpec

APPROVER_FILES = ("installation-root.der", "installation-proof.json")


class RevocationInput(Closed):
    source_ref: Ref
    revision: N
    observed_at: T
    valid_until: T
    revoked_fingerprints: tuple[Digest, ...]


class AssembleInput(Closed):
    schema_: Literal["staff-materials-assemble.v1"] = Field(alias="schema")
    material_version_id: str = Field(pattern=r"^[A-Za-z0-9-]{32,64}$")
    issuer: str
    issued_at: T
    valid_until: T
    native_configuration_digest: Digest
    native_maximum_seconds: N
    native_schema: NativeSchema
    engine_schema: EngineSchema
    session_lock_function_pin: FunctionPin
    native_relation_pins: dict[str, NativeRelationPin]
    revocation: RevocationInput


def load_input(raw: bytes) -> AssembleInput:
    try:
        # Same entry as the loader (`parse`): strict JSON profile, closed model.
        return parse(AssembleInput, raw)
    except Exception as failure:
        raise MaterialError(f"entrada do assemble invalida: {failure}") from None


def _designation_entries(designation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["role"]: entry for entry in designation.get("entries", [])}


def assemble(
    portal_files: dict[str, bytes],
    approver_files: dict[str, bytes],
    summary: dict[str, Any],
    spec: MaterialsSpec,
    inputs: AssembleInput,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Devolve (manifesto publico, arquivos do pacote). Nao le nem escreve disco."""
    if set(approver_files) != set(APPROVER_FILES):
        raise MaterialError("faltam do aprovador: installation-root.der e installation-proof.json")
    files = {**portal_files, **approver_files}
    if set(files) != FILES:
        raise MaterialError("conjunto de arquivos do pacote diferente do exigido pelo loader")
    try:
        root = serialization.load_der_public_key(files["installation-root.der"])
    except Exception:
        raise MaterialError("installation-root.der nao e uma chave publica DER") from None
    if not isinstance(root, Ed25519PublicKey):
        raise MaterialError("a raiz precisa ser Ed25519")
    designation = strict_loads(files["designation.json"])
    if digest(designation) != summary["designation_sha256"]:
        raise MaterialError("designation.json nao e a do summary do generate")
    entries = _designation_entries(designation)
    read, witness = entries.get("read_requester"), entries.get("identity_verifier")
    keys = summary["key_fingerprints"]
    if read is None or witness is None:
        raise MaterialError("designacao sem read_requester ou identity_verifier")
    if read["operations"] != ["detail", "list"]:
        raise MaterialError("read_requester precisa de operations [detail, list]")
    if (
        keys["identity_verifier"] == keys["read_requester"]
        or witness["key_fingerprint"] != keys["identity_verifier"]
        or read["key_fingerprint"] != keys["read_requester"]
        or witness["login_role"] != spec.native_witness_connection.login
    ):
        raise MaterialError("identity_verifier precisa de chave propria e do login witness (D-H.2)")
    scope = summary["scope"]
    lock = spec.session_lock_connection.validated("session-lock-ca.pem").wire()
    lock["function_pin"] = inputs.session_lock_function_pin.wire()
    revocation = inputs.revocation.wire()
    manifest: dict[str, Any] = dict(
        schema="portal-staff-material.v2",
        material_version_id=inputs.material_version_id,
        scope=scope,
        issuer=inputs.issuer,
        issued_at=inputs.issued_at,
        valid_until=inputs.valid_until,
        root_key_fingerprint=fingerprint(root),
        designation_digest=summary["designation_sha256"],
        native_configuration_digest=inputs.native_configuration_digest,
        native_maximum_seconds=inputs.native_maximum_seconds,
        read_key_fingerprint=keys["read_requester"],
        witness_key_fingerprint=keys["identity_verifier"],
        native_origin="https://" + summary["native_hostname"],
        native_server_spki_sha256=summary["native_server_spki_sha256"],
        native_schema=inputs.native_schema,
        engine_schema=inputs.engine_schema,
        session_lock_connection=lock,
        native_witness_connection=spec.native_witness_connection.validated("native-witness-ca.pem").wire(),
        native_relation_pins={name: pin.wire() for name, pin in inputs.native_relation_pins.items()},
        revocation_snapshot=dict(revocation, scope=scope, designation_digest=summary["designation_sha256"]),
        files={
            n: None if n in PRIVATE_FILES else hashlib.sha256(v).hexdigest() for n, v in sorted(files.items())
        },
    )
    try:
        parse(PublicManifest, canonicalize(manifest))
    except Exception:
        raise MaterialError("manifesto fora do perfil fechado do loader (PublicManifest v2)") from None
    return manifest, files


def bundle_bytes(manifest: dict[str, Any], files: dict[str, bytes]) -> bytes:
    return canonicalize(
        dict(
            schema="portal-staff-secret-bundle.v1",
            material_version_id=manifest["material_version_id"],
            public_manifest=manifest,
            files={n: base64.b64encode(v).decode() for n, v in sorted(files.items())},
        )
    )


def read_directories(
    materials: Path, approver: Path
) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, Any]]:
    portal = {p.name: p.read_bytes() for p in (materials / "portal").iterdir() if p.is_file()}
    extra = sorted(set(portal) & set(APPROVER_FILES))
    if extra:
        raise MaterialError("portal/ do generate nao pode trazer material do aprovador")
    approver_files = {
        name: (approver / name).read_bytes() for name in APPROVER_FILES if (approver / name).is_file()
    }
    summary = strict_loads((materials / "public" / "summary.json").read_bytes())
    return portal, approver_files, summary
