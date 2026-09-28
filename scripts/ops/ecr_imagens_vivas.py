#!/usr/bin/env python3
"""Varre as task definitions VIVAS do dev e confere cada imagem ECR contra `describe-images`.

Vivas = revisao de todo deployment de servico do cluster + ultima revisao ACTIVE de cada familia
`maezo-operadora-dev-*` + alvo de cada EventBridge Schedule. Sai 1 se alguma imagem nao existir.
E' a prova do incidente de 27/09/2026 (lifecycle do ECR apagou imagem em uso, ver `ecs.tf`) e o
unico jeito seguro de decidir o que pode ser apagado a mao: o que nao aparece aqui.

Uso: AWS_PROFILE=<perfil> python scripts/ops/ecr_imagens_vivas.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

REGIAO = "sa-east-1"
CLUSTER = "maezo-operadora-dev"


def aws(*args: str) -> tuple[dict[str, Any], int]:
    proc = subprocess.run(  # argv fixo, sem shell
        ["aws", *args, "--region", REGIAO, "--output", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (json.loads(proc.stdout) if proc.stdout.strip() else {}), proc.returncode


def task_definitions_vivas() -> dict[str, set[str]]:
    tds: dict[str, set[str]] = {}
    servicos = aws("ecs", "list-services", "--cluster", CLUSTER)[0]["serviceArns"]
    for i in range(0, len(servicos), 10):
        lote = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", *servicos[i : i + 10])[0]
        for svc in lote["services"]:
            for dep in svc["deployments"]:
                tds.setdefault(dep["taskDefinition"], set()).add("svc:" + svc["serviceName"])
    familias = aws("ecs", "list-task-definition-families", "--family-prefix", CLUSTER, "--status", "ACTIVE")[
        0
    ]["families"]
    for familia in familias:
        arn = aws("ecs", "describe-task-definition", "--task-definition", familia)[0]["taskDefinition"][
            "taskDefinitionArn"
        ]
        tds.setdefault(arn, set()).add("familia")
    for sched in aws("scheduler", "list-schedules")[0].get("Schedules", []):
        alvo = aws("scheduler", "get-schedule", "--name", sched["Name"])[0]
        arn = alvo.get("Target", {}).get("EcsParameters", {}).get("TaskDefinitionArn")
        if arn:
            tds.setdefault(arn, set()).add("schedule:" + sched["Name"])
    return tds


def main() -> int:
    faltando = 0
    cache: dict[tuple[str, str], bool] = {}
    for arn, quem in sorted(task_definitions_vivas().items()):
        td = aws("ecs", "describe-task-definition", "--task-definition", arn)[0]["taskDefinition"]
        for container in td["containerDefinitions"]:
            imagem = container["image"]
            if ".dkr.ecr." not in imagem:
                continue  # imagem publica (kafka, cloudflared, otel): fora do lifecycle do ECR
            repo_ref = imagem.split("/", 1)[1]
            if "@" in repo_ref:
                repo, ref = repo_ref.split("@")
                image_id = f"imageDigest={ref}"
            else:
                repo, ref = repo_ref.rsplit(":", 1)
                image_id = f"imageTag={ref}"
            if (repo, ref) not in cache:
                cache[(repo, ref)] = (
                    aws("ecr", "describe-images", "--repository-name", repo, "--image-ids", image_id)[1] == 0
                )
            existe = cache[(repo, ref)]
            faltando += 0 if existe else 1
            estado = "OK      " if existe else "FALTANDO"
            print(estado, arn.rsplit("/", 1)[1], container["name"], repo, ref[:24], ",".join(sorted(quem)))
    print("FALTANDO_TOTAL", faltando)
    return 1 if faltando else 0


if __name__ == "__main__":
    sys.exit(main())
