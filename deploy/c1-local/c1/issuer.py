"""Passo `issuer` (servico `issuer`, na rede do engine para o REST em localhost): a T1.6 contra o engine.

1. deploy (tenant `amh`) de SP-OP-AUTH-001, SP-OP-ESCALATION-001 e da DMN `escalation_routing`, os
   arquivos de `spec/processes`, pelo `engine-rest` da 8080 (o mesmo caminho do deploy de dev);
2. a escalacao `ESC-amh-sla-auth-SYN-C1GUIA1` com `motivo_categoria=solicitacao_humano` (a DMN escolhe
   `atendimento-humano`), com as duas tarefas externas antes da tarefa humana concluidas como worker.
   A instancia `AUTH-amh-SYN-C1GUIA1` NAO nasce aqui: vem do start nativo do passo `auth-fixture`;
3. a composicao `staff-case-issuer-composition.v1` com o material do `generate` e uma rodada do
   `python -m maezo.gateway.staff_cases`, cuja linha JSON e o resultado medido.
A reivindicacao humana (`mzo_auth_guide_claim`) NAO e fabricada: ela nasce pelo intake AUTH
(`/v1/auth-start`) da fixture sintetica (`auth_fixture.py`, D-K.2).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

from tools.dev_syn_fixture import core

from .common import ADMIN, APPROVER_OUT, ISSUER_LOGIN, ISSUER_WITNESS_LOGIN, MATERIALS, ROOT, TENANT, read_text, state, step, write

REST = "http://localhost:8080/engine-rest"
SPEC = Path("/repo/spec/processes")
from .auth_fixture import GUIDE_NUMBER as GUIDE  # noqa: E402
RUN = ROOT / "issuer-run"


def _deploy(client: httpx.Client) -> dict:
    return core.deploy(client, TENANT, SPEC, "c1-staff")


def _escalation(client: httpx.Client) -> tuple[str, list[str]]:
    outcome = core.open_escalation(client, TENANT, GUIDE, motivo="solicitacao_humano", severidade="moderada",
                                   agent="c1-agent", conversation="c1-conversation", canal="c1", worker="c1-worker")
    return str(outcome.tasks), [*outcome.groups, f"{core.auth_instance_key(TENANT, GUIDE)}={outcome.auth_instances}"]


def composition() -> Path:
    engine, materials = state("engine"), state("materials")
    pins = state("pins")["relations"]
    RUN.mkdir(mode=0o700, exist_ok=True)
    copies = {
        "designation.json": MATERIALS / "portal/designation.json",
        "installation-proof.json": APPROVER_OUT / "installation-proof.json",
        "installation-root.der": APPROVER_OUT / "installation-root.der",
        "case-issuer-signing-key.pem": MATERIALS / "issuer/case-issuer-signing-key.pem",
        "issuer-witness-signing-key.pem": MATERIALS / "issuer/issuer-witness-signing-key.pem",
        "native-ca.pem": MATERIALS / "portal/native-ca.pem",
        "importer-client-certificate.pem": MATERIALS / "issuer/publication-importer-client-certificate.pem",
        "importer-client-key.pem": MATERIALS / "issuer/publication-importer-client-key.pem",
        "importer-signing-key.pem": MATERIALS / "issuer/publication-importer-signing-key.pem",
    }
    for name, source in copies.items():
        write(RUN / name, source.read_bytes(), 0o400)
    for login, name in ((ISSUER_LOGIN, "issuer-dsn.txt"), (ISSUER_WITNESS_LOGIN, "witness-dsn.txt")):
        secret = quote(read_text(ADMIN / f"{login}-password"), safe="")
        write(RUN / name, f"postgresql+asyncpg://{login}:{secret}@postgres:5432/maezo", 0o400)
    summary = materials["summary"]
    value = {
        "schema": "staff-case-issuer-composition.v1", "tenant": TENANT, "environment": summary["scope"]["environment"],
        "engine_name": summary["scope"]["engine_name"], "database_incarnation": summary["scope"]["database_incarnation"],
        "native_schema": "maezo_native", "membership_schema": TENANT, "designation_file": "designation.json",
        "installation_proof_file": "installation-proof.json", "root_public_key_file": "installation-root.der",
        "designation_digest": materials["designation_digest"], "revoked_fingerprints": [],
        "case_issuer_key_file": "case-issuer-signing-key.pem", "witness_key_file": "issuer-witness-signing-key.pem",
        "issuer_dsn_file": "issuer-dsn.txt", "witness_dsn_file": "witness-dsn.txt",
        "relation_pins": {t: {"oid": int(pins[t]["oid"]), "owner": pins[t]["owner"]}
                          for t in ("mzo_portal_read_membership", "mzo_human_principal")},
        "engine_rest_url": REST,
        "native": {"origin": "https://engine-native.c1.internal", "ca_file": "native-ca.pem",
                   "client_certificate_file": "importer-client-certificate.pem",
                   "client_key_file": "importer-client-key.pem", "importer_key_file": "importer-signing-key.pem",
                   "server_spki_sha256": summary["native_server_spki_sha256"],
                   "configuration_digest": engine["configuration_digest"]},
        "seconds": 10,
    }
    path = RUN / "composition.json"
    write(path, json.dumps(value), 0o400)
    return path


def main() -> None:
    with httpx.Client(base_url=REST, timeout=30, trust_env=False) as client:
        deployment = _deploy(client)
        tasks, groups = _escalation(client)
    path = composition()
    completed = subprocess.run([sys.executable, "-m", "maezo.gateway.staff_cases"], capture_output=True, text=True,
                               env={**os.environ, "MAEZO_STAFF_CASE_ISSUER_FILE": str(path)})
    line = (completed.stdout.strip().splitlines() or [completed.stderr.strip()[-300:]])[-1]
    step("issuer", completed.returncode == 0 and '"grants": 1' in line,
         f"deploy {deployment.get('id', '?')[:8]} (tenant {TENANT}); UT_TratarEscalonamento={tasks} grupo(s) candidato {groups}; "
         f"emissor rc={completed.returncode} {line}")


if __name__ == "__main__":
    main()
