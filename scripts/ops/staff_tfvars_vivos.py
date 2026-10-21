#!/usr/bin/env python3
"""Var-files LOCAIS de `engine_native`, `staff_install` e `staff_ops` lidos das task definitions VIVAS.

Por que existe: esses tres objetos nao tem default no repo e os var-files de quem aplicou as Ondas 4-10
ficavam em `%TEMP%`/scratchpad, que a limpeza do Windows apaga (06/10 e 08/10/2026). Um apply sem eles
planeja DESTRUIR o plano nativo inteiro. Aqui eles sao reconstruidos da verdade (o que roda), trocando so
os pins da renovacao (`docs/runbooks/renovar-material-staff-dev.md`, passo 4). Nada aqui e segredo:
ARNs, version ids e digests. Escreve FORA do repositorio.

Uso: AWS_PROFILE=adm-dev python scripts/ops/staff_tfvars_vivos.py --saida <dir> --native-version <id>
       --job-version <id> --human-version <material_version_id> --human-manifest <sha256>
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

PREFIXO = "maezo-operadora-dev-"
SEGREDO = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/"


def td(familia: str) -> list[dict[str, Any]]:
    proc = subprocess.run(  # argv fixo, sem shell
        [
            "aws",
            "ecs",
            "describe-task-definition",
            "--task-definition",
            PREFIXO + familia,
            "--region",
            "sa-east-1",
            "--query",
            "taskDefinition.containerDefinitions",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def kms(alias: str) -> str:
    proc = subprocess.run(
        [
            "aws",
            "kms",
            "describe-key",
            "--key-id",
            alias,
            "--region",
            "sa-east-1",
            "--query",
            "KeyMetadata.Arn",
            "--output",
            "text",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def digest(container: dict[str, Any]) -> str:
    return container["image"].split("@", 1)[1]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--native-version", required=True)
    p.add_argument("--job-version", required=True)
    p.add_argument("--human-version", required=True)
    p.add_argument("--human-manifest", required=True)
    a = p.parse_args()
    motor = {c["name"]: c for c in td("cibseven")}
    materializa = motor["engine-native-materialize"]
    env_motor = {e["name"] for e in motor["cibseven"]["environment"]}
    engine = dict(
        engine_native=dict(
            native_secret_arn=materializa["secrets"][0]["valueFrom"].split(":::")[0],
            native_secret_version_id=a.native_version,
            kms_key_arn=kms("alias/maezo-operadora-dev-engine-native"),
            app_image_digest=digest(materializa),
            staff_case_issuer="staff-case-issuer" in motor,
            assignment_trust="MAEZO_HUMAN_ASSIGNMENT_TRUST_FILE" in env_motor,
        ),
        # O `onda3-digests.tfvars` local pode estar atrasado: o motor que roda e o que manda.
        engine_image_digest=digest(motor["cibseven"]),
    )
    install = td("staff-install")[0]
    env = {e["name"]: e["value"] for e in install["environment"]}
    extra = json.loads(env["STAFF_INSTALL_EXTRA_LOGIN_SECRET_ARNS"])
    job = {c["name"]: c for c in td("staff-job")}
    ops = dict(
        staff_install=dict(
            image_digest=digest(install), login_secret_arns=json.loads(env["STAFF_INSTALL_LOGIN_SECRET_ARNS"])
        ),
        staff_ops=dict(
            image_digest=digest(job["staff-job"]),
            rows_secret_arn=SEGREDO + "staff-install/rows-R8q4LK",
            syn_secret_arn=SEGREDO + "staff-install/syn-fixture-wt3sOm",
            job_secret_arn=job["staff-job-init"]["secrets"][0]["valueFrom"].split(":::")[0],
            job_secret_version_id=a.job_version,
            human_material_version_id=a.human_version,
            human_manifest_sha256=a.human_manifest,
            job_schedule_enabled=True,
            task_source_secret_arn=extra["portal_task_source_amh"],
            human_outbox_secret_arn=extra["portal_human_outbox_amh"],
            human_source_secret_arn=extra["portal_human_source_amh"],
            assignment_admin_secret_arn=extra["portal_assignment_admin_amh"],
            assignment_secret_arn=SEGREDO + "staff-install/assignment-EJgSnC",
        ),
    )
    a.saida.mkdir(parents=True, exist_ok=True)
    (a.saida / "cur-engine.tfvars.json").write_text(json.dumps(engine, indent=1), encoding="utf-8")
    (a.saida / "cur-ops.tfvars.json").write_text(json.dumps(ops, indent=1), encoding="utf-8")
    print(f"engine_image_digest={engine['engine_image_digest']}")
    print(f"app_image_digest={engine['engine_native']['app_image_digest']}")
    print(f"staff_ops.image_digest={ops['staff_ops']['image_digest']}")
    print(f"var-files em {a.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
