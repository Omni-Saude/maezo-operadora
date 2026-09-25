"""O pacote `portal-human-material.v1` que o job da T1.5 le (`load_human_materials`).

Quem gera e a ferramenta (`tools/staff_materials/human_bundle.py`, `human-bundle keys|package`);
este modulo so passa os valores do C1 e escreve em `/c1/human-materials/current` (que o harness
reescreve a cada passada). O que um pacote de teste nao tem e este tem:
* os certificados de cliente sao emitidos por uma CA de cliente PROPRIA do pacote (`job_ca`), que
  entra no truststore do listener 8443 ao lado da CA de clientes do `generate` (D5);
* o `read-admission.json` espelha a admissao Q2 que o engine vai de fato carregar (mesmo
  deployment, geracao = revisao, `capability_digest` = SHA-256 do registro assinado);
* as superficies apontam para o engine local (`https://engine-native.c1.internal`, SPKI do servidor
  do `generate`).
A raiz que assina a admissao humana e outra raiz de TESTE, descartavel, distinta da raiz staff; ela
nasce aqui, nunca na ferramenta.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.staff_materials import human_bundle as tool
from tools.staff_materials.human_bundle import ClientCa, EdPriv, pem, spki

from maezo.gateway.human.production_materials import HumanPublicManifest
from maezo.gateway.human.read_credentials import ReadAdmission
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.engine.profile import canonicalize

from .common import HUMAN_OUTBOX_LOGIN, HUMAN_SOURCE_LOGIN, iso

__all__ = ["ClientCa", "EdPriv", "HumanBundle", "build", "pem", "rebuild", "spki", "window"]


@dataclass
class HumanBundle:
    manifest: HumanPublicManifest
    manifest_sha256: str
    read_key: EdPriv
    command_key: EdPriv
    read_client_spki: str
    command_client_spki: str
    key_ids: dict[str, str]
    spec: tool.HumanBundleSpec
    keys: tool.HumanKeys


def _spec(common: dict) -> tool.HumanBundleSpec:
    issued_at, valid_until = common["window"]
    connection = dict(host="postgres", port="5432", database="maezo")
    value = dict(
        schema="staff-materials-human-bundle.v1", scope=common["scope"], engine_name=common["engine_name"],
        database_incarnation=common["incarnation"], issuer="https://identity.c1.invalid",
        issued_at=iso(issued_at), valid_until=iso(valid_until), origin=common["origin"],
        catalog_ref=common["catalog_ref"], material_version_prefix="c1human", certificate_label="c1 job",
        read_key=dict(key_id="c1-human-read-1", audience=common["audience_read"]),
        assignment_key=dict(key_id="c1-human-assignment-1", audience="c1-engine-human-assignment-read"),
        command_key=dict(key_id="c1-human-command-1", audience="c1-engine-human-command"),
        max_envelope_seconds="30", timeout_seconds="10",
        # Onda 8: os logins reais do plano humano (o `assignment` os cria e aplica os grants do repo).
        outbox_connection=dict(connection, login=HUMAN_OUTBOX_LOGIN),
        source_connection=dict(connection, login=HUMAN_SOURCE_LOGIN),
        relay_lease_seconds="30", relay_retry_seconds="1", relay_poll_seconds="1",
        revocation=dict(source_ref="c1-revocation", revision="1"),
    )
    return tool.load_spec(canonicalize(value))


def _write(directory: Path, files: dict[str, bytes], manifest: HumanPublicManifest) -> None:
    current = directory / "current"
    if current.exists():
        os.chmod(current, 0o700)
        for child in current.iterdir():
            child.unlink()
    else:
        current.mkdir(parents=True, mode=0o700)
    for name, raw in {**files, "manifest.json": manifest.canonical()}.items():
        path = current / name
        path.write_bytes(raw)
        os.chmod(path, 0o400)
    os.chmod(current, 0o500)


def _package(spec: tool.HumanBundleSpec, keys: tool.HumanKeys, admission: dict, common: dict) -> HumanBundle:
    # Raiz de TESTE nova a cada passada (a da 1a passada nao e guardada de proposito).
    root = Ed25519PrivateKey.generate()
    record = parse_model(ReadAdmission, admission)
    document = json.dumps(
        {
            "schema": "portal-human-read-admission.v1",
            "record": wire(record),
            "signature": base64.b64encode(root.sign(canonicalize(wire(record)))).decode("ascii"),
        }
    ).encode()
    files, manifest = tool.package(
        spec, keys, root_spki=spki(root), admission=document, server_ca_pem=common["server_ca_pem"],
        server_spki=common["server_spki"], source_dsn=common["source_dsn"], outbox_dsn=common["outbox_dsn"],
    )
    _write(common["directory"], files, manifest)
    return HumanBundle(
        manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest.canonical()).hexdigest(),
        read_key=keys.signing["portal-task-read"],
        command_key=keys.signing["human-command"],
        read_client_spki=keys.read_client_spki,
        command_client_spki=keys.command_client_spki,
        key_ids={purpose: key.key_id for purpose, key in spec.keys().items()},
        spec=spec,
        keys=keys,
    )


def build(*, admission: dict, client_ca: ClientCa, **common) -> HumanBundle:
    """1a passada: chaves novas (emitidas pela CA `client_ca` do harness) e admissao provisoria."""
    spec = _spec(common)
    return _package(spec, tool.new_keys(spec, client_ca=client_ca), admission, common)


def rebuild(first: HumanBundle, admission: dict, common: dict) -> HumanBundle:
    """Reescreve o pacote com as MESMAS chaves e certificados e a admissao-espelho final."""
    return _package(first.spec, first.keys, admission, common)


def window(hours: float = 20) -> tuple[datetime, datetime]:
    from .common import now

    start = now() - timedelta(minutes=5)
    return start, start + timedelta(hours=hours)
