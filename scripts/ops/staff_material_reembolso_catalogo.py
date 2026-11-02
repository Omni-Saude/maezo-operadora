#!/usr/bin/env python3
"""Monta o catalogo e as entradas `human` das 5 tarefas do SP-OP-REEMBOLSO-001 v0.2.0 (DL-0161/DL-0162).

Runbook: `docs/runbooks/reembolso-001.md` §3 e `docs/runbooks/material-staff-na-aws.md` passo 5.
Molde: `staff_material_cancel_catalogo.py`. Este script NAO executa nada na AWS por si: le o segredo vivo
(leitura) e escreve arquivos em `--out`; quem aplica e' o dono, depois de subir o plugin do motor (senao as
filas caem com 503).

Le o segredo VIVO `staff-job/materials` (so o catalogo e a admissao Q2 dele, que nao sao segredo) e escreve em
`--out`:

``task-catalog.json``      o catalogo vivo MAIS as 5 tarefas do reembolso e os 4 formularios (JCS; passa pelo
                           mesmo modelo que o job usa: `ReadCatalogArtifact`);
``entradas-humanas.json``  as 5 entradas `human` da admissao Q2 (mesmas politicas SYN dev das entradas vivas).

Os fatos do processo vem de um GET sem efeito no motor (`/process-definition/{id}` e `/xml`): `--process-id` e
o SHA-256 dos bytes do BPMN publicado (`--bpmn-sha256`). O `gerar` troca o catalogo, atualiza `catalog_digest`
e o `review_admission` confere catalogo x admissao antes de assinar. Imprime so digests.
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
PROCESSO = "SP-OP-REEMBOLSO-001"
DECISAO = [
    "decisao_reembolso",
    "valor_reembolso_aprovado_cents",
    "justificativa",
    "fundamentacao_contratual",
    "info_solicitada",
    "texto_ao_beneficiario",
]
AUDITOR = [*DECISAO[:4], "cid10_referencia", "parecer_auditor", "info_solicitada", "texto_ao_beneficiario"]
#: `PortalReadCommand.COMPILED`: (tarefa, form_key, status, entradas, grupos, papel exigido).
TAREFAS = (
    (
        "UT_AnaliseReembolso",
        "reembolso_decisao",
        "BPMN_FORMDATA",
        DECISAO,
        ["analise-reembolso"],
        "reembolso-analisar",
    ),
    (
        "UT_CoordenacaoReembolso",
        "reembolso_decisao",
        "BPMN_FORMDATA",
        DECISAO,
        ["coordenacao-reembolso"],
        "reembolso-analisar",
    ),
    (
        "UT_RevisaoAuditorMedico",
        "reembolso_auditor",
        "BPMN_FORMDATA",
        AUDITOR,
        ["medico-auditor"],
        "reembolso-auditar",
    ),
    (
        "UT_DecidirPendenciaExpirada",
        "reembolso_pendencia",
        "BPMN_FORMDATA",
        ["decisao_pendencia"],
        ["analise-reembolso"],
        "reembolso-analisar",
    ),
    (
        "UT_RegistrarPagamento",
        "reembolso_pagamento",
        "BPMN_FORMDATA",
        ["data_pagamento"],
        ["financeiro-reembolso"],
        "reembolso-pagar",
    ),
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def montar(
    catalogo_vivo: dict[str, Any], admissao_viva: dict[str, Any], process_id: str, bpmn_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Catalogo novo e entradas `human` novas. Puro: nada de rede."""
    m = re.fullmatch(rf"{PROCESSO}:([1-9][0-9]*):[0-9a-f-]{{36}}", process_id)
    if m is None or not re.fullmatch(r"[0-9a-f]{64}", bpmn_sha256):
        raise SystemExit("--process-id (SP-OP-REEMBOLSO-001:<versao>:<uuid>) ou --bpmn-sha256 invalido")
    versao = m.group(1)
    if any(e["process_definition_id"] == process_id for e in catalogo_vivo["entries"]):
        raise SystemExit("o catalogo vivo ja tem este process_definition_id")
    modelo = catalogo_vivo["entries"][0]
    modelo_h = admissao_viva["human"]["entries"][0]
    catalogo = json.loads(json.dumps(catalogo_vivo))
    formularios = {f["artifact_ref"] for f in catalogo["forms"]}
    digests: dict[str, str] = {}
    for form_key in sorted({t[1] for t in TAREFAS}):
        bruto = f"SYN read-form-{form_key}".encode()
        digests[form_key] = sha(bruto)
        if form_key not in formularios:
            catalogo["forms"].append(
                {
                    "artifact_ref": form_key,
                    "bytes_base64": base64.b64encode(bruto).decode(),
                    "digest": digests[form_key],
                }
            )
    entradas_humanas: list[dict[str, Any]] = []
    for tarefa, form_key, status, entradas, grupos, papel in TAREFAS:
        catalogo["entries"].append(
            dict(
                modelo,
                allowed_inputs=entradas,
                form_digest=digests[form_key],
                form_key=form_key,
                form_source_status=status,
                form_version="1",
                group_domain={
                    "dmn_definition_id": None,
                    "dmn_definition_key": None,
                    "dmn_definition_version": None,
                    "dmn_resource_digest": None,
                    "groups": grupos,
                    "kind": "static",
                },
                process_definition_digest=bpmn_sha256,
                process_definition_id=process_id,
                process_definition_key=PROCESSO,
                process_definition_version=versao,
                required_roles=[papel],
                task_definition_key=tarefa,
            )
        )
        sufixo = form_key.replace("_", "-")
        entradas_humanas.append(
            dict(
                modelo_h,
                candidate_groups=grupos,
                classification=dict(
                    modelo_h["classification"],
                    classification_ref=f"SYN-classification-{sufixo}",
                    classification_digest=sha(f"SYN classification {sufixo}".encode()),
                    fields_digest=sha(f"SYN fields {sufixo}".encode()),
                ),
                process_definition_id=process_id,
                task_definition_key=tarefa,
            )
        )
    return catalogo, entradas_humanas


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--process-id", required=True, help="id da definicao viva (GET /process-definition?key=...)"
    )
    p.add_argument("--bpmn-sha256", required=True, help="SHA-256 dos bytes do BPMN publicado (GET .../xml)")
    p.add_argument("--out", type=Path, required=True, help="diretorio (existente) de saida")
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
                "entradas_humanas": len(entradas),
                "tarefas": [t[0] for t in TAREFAS],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
