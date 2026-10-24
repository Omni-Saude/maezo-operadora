#!/usr/bin/env python3
"""Repina o catalogo Q2 e as entradas `human` numa VERSAO NOVA de um processo ja admitido (DL-0113).

Runbook: `docs/runbooks/material-staff-na-aws.md` passo 5 ("Versao nova de processo ja admitido").

Por que existe: o portal so le tarefas cujo par (`process_definition_id`, tarefa) a admissao Q2 nomeia. Um
`deploy-processes` que muda os bytes de um BPMN com tarefa no portal cria a versao N+1 no motor, as instancias
NOVAS nascem nela e as tarefas delas somem de todas as filas (sem recurso de leitura), ate a admissao ser
reassinada. Foi o que aconteceu com o `SP-OP-ESCALATION-001` em 28/09, 13/10 e 17/10/2026 (v3, #816).

Le o segredo VIVO `staff-job/materials` (so o catalogo e a admissao Q2 dele, que nao sao segredo) e escreve em
`--out`:

``task-catalog.json``      o catalogo vivo MAIS um clone de cada entrada da versao admitida mais nova do
                           processo, trocando so `process_definition_id`, `process_definition_version` e
                           `process_definition_digest` (JCS; mesmo modelo que o job usa). Formularios, papeis,
                           grupos e politicas ficam iguais: as tarefas e os grupos do BPMN novo TEM de ser os
                           mesmos (confira antes; se mudaram, este script nao serve);
``entradas-humanas.json``  o clone das entradas `human` da mesma versao, com o `process_definition_id` novo.

As entradas da versao anterior FICAM (instancias antigas continuam nela). O `gerar` troca o catalogo, atualiza
o `catalog_digest` e o `review_admission` confere catalogo x admissao antes de assinar. Imprime so digests.

Os fatos do processo vem de um GET sem efeito no motor: `--process-id` de `/process-definition?key=...`
(tenant `amh`) e `--bpmn-sha256` o SHA-256 dos bytes de `/deployment/{id}/resources/{id}/data`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SEGREDO = "maezo-operadora/dev/staff-job/materials"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def montar(
    catalogo_vivo: dict[str, Any], admissao_viva: dict[str, Any], process_id: str, bpmn_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Catalogo novo e entradas `human` novas. Puro: nada de rede."""
    m = re.fullmatch(r"([A-Z][A-Z0-9-]*[A-Z0-9]):([1-9][0-9]*):[0-9a-f-]{36}", process_id)
    if m is None or not re.fullmatch(r"[0-9a-f]{64}", bpmn_sha256):
        raise SystemExit("--process-id (<CHAVE>:<versao>:<uuid>) ou --bpmn-sha256 invalido")
    chave, versao = m.group(1), m.group(2)
    if any(e["process_definition_id"] == process_id for e in catalogo_vivo["entries"]):
        raise SystemExit("o catalogo vivo ja tem este process_definition_id")
    da_chave = [e for e in catalogo_vivo["entries"] if e["process_definition_key"] == chave]
    if not da_chave:
        raise SystemExit(f"{chave} nao tem entrada no catalogo vivo: use o fluxo de processo NOVO")
    anterior = max(int(e["process_definition_version"]) for e in da_chave)
    if int(versao) <= anterior:
        raise SystemExit(f"versao {versao} nao e mais nova que a admitida ({anterior})")
    modelos = [e for e in da_chave if int(e["process_definition_version"]) == anterior]
    id_anterior = {e["process_definition_id"] for e in modelos}
    if len(id_anterior) != 1:
        raise SystemExit("a versao admitida mais nova tem mais de um process_definition_id")
    (id_anterior_unico,) = id_anterior
    catalogo = json.loads(json.dumps(catalogo_vivo))
    for e in modelos:
        catalogo["entries"].append(
            dict(
                e,
                process_definition_digest=bpmn_sha256,
                process_definition_id=process_id,
                process_definition_version=versao,
            )
        )
    tarefas = {e["task_definition_key"] for e in modelos}
    humanas = [
        e
        for e in admissao_viva["human"]["entries"]
        if e["process_definition_id"] == id_anterior_unico and e["task_definition_key"] in tarefas
    ]
    if {e["task_definition_key"] for e in humanas} != tarefas:
        raise SystemExit("admissao viva nao cobre exatamente as tarefas do catalogo para a versao anterior")
    entradas = [dict(e, process_definition_id=process_id) for e in humanas]
    return catalogo, entradas


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--process-id", required=True, help="id da definicao viva nova (tenant amh)")
    p.add_argument("--bpmn-sha256", required=True, help="SHA-256 dos bytes do BPMN publicado")
    p.add_argument("--out", type=Path, required=True, help="diretorio (existente) de saida, FORA do repo")
    args = p.parse_args()

    import boto3

    from maezo.portal.engine.profile import canonicalize, strict_loads

    cliente = boto3.client("secretsmanager", region_name="sa-east-1")
    job = json.loads(cliente.get_secret_value(SecretId=SEGREDO)["SecretString"])
    catalogo_vivo = json.loads(base64.b64decode(job["job/task-catalog.json"]))
    admissao_viva = json.loads(base64.b64decode(job["job/task-admission.json"]))
    catalogo, entradas = montar(catalogo_vivo, admissao_viva, args.process_id, args.bpmn_sha256)
    bruto = canonicalize(strict_loads(json.dumps(catalogo).encode()))
    args.out.joinpath("task-catalog.json").write_bytes(bruto)
    args.out.joinpath("entradas-humanas.json").write_bytes(json.dumps(entradas, sort_keys=True).encode())
    print(
        json.dumps(
            {
                "catalog_digest": sha(bruto),
                "catalog_bytes": len(bruto),
                "entradas_catalogo": len(catalogo["entries"]),
                "entradas_humanas_novas": len(entradas),
                "tarefas": sorted(e["task_definition_key"] for e in entradas),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
