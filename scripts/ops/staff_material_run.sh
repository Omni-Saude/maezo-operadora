#!/usr/bin/env bash
# Roda a task Fargate `staff-material` (material staff/human NA AWS, DL-0113) com UM modo e imprime o log.
# Runbook: docs/runbooks/material-staff-na-aws.md. So passa variaveis NAO secretas por `containerOverrides`
# (ecs:DescribeTasks devolve o override inteiro): modo, ids, sufixo, hash aprovado da raiz, chaves em `insumos/`.
#
# uso: AWS_PROFILE=adm-dev scripts/ops/staff_material_run.sh <modo> [CHAVE=valor...]
#   modos: verificar | ensaio | raiz-gerar | gerar
#   ex.:   scripts/ops/staff_material_run.sh verificar
#          scripts/ops/staff_material_run.sh ensaio MATERIAL_SUFIXO=20261016
#          scripts/ops/staff_material_run.sh raiz-gerar MATERIAL_RAIZ_ID=raiz-20261016
#          scripts/ops/staff_material_run.sh gerar MATERIAL_RAIZ_ID=raiz-20261016 MATERIAL_RAIZ_SHA256=<hex> \
#              MATERIAL_SAIDA_ID=onda12-20261016 MATERIAL_SUFIXO=20261016
set -euo pipefail
export AWS_REGION=sa-east-1
MODO=${1:?uso: staff_material_run.sh <verificar|ensaio|raiz-gerar|gerar> [CHAVE=valor...]}
shift
case "$MODO" in verificar | ensaio | raiz-gerar | gerar) ;; *) echo "modo invalido: $MODO" >&2; exit 64 ;; esac
for kv in "$@"; do
  case "$kv" in
    MATERIAL_*=*) ;;
    *) echo "so MATERIAL_*=valor e aceito (nunca segredo): $kv" >&2; exit 64 ;;
  esac
done
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
python - "$MODO" "$@" > "$TMP/ov.json" <<'PY'
import json, sys

modo, pares = sys.argv[1], sys.argv[2:]
env = [{"name": "MATERIAL_MODO", "value": modo}]
env += [dict(zip(("name", "value"), kv.split("=", 1))) for kv in pares]
print(json.dumps({"containerOverrides": [{"name": "staff-material", "environment": env}]}))
PY
OV="$TMP/ov.json"
command -v cygpath > /dev/null && OV=$(cygpath -m "$OV")
NET="awsvpcConfiguration={subnets=[subnet-0e1f840dbec44e66a,subnet-06647b0c664d0d22a],securityGroups=[sg-0e2aebe1a2c1d253d],assignPublicIp=DISABLED}"
TA=$(aws ecs run-task --cluster maezo-operadora-dev --task-definition maezo-operadora-dev-staff-material \
  --launch-type FARGATE --network-configuration "$NET" --overrides "file://$OV" \
  --query 'tasks[0].taskArn' --output text)
echo "task: ${TA##*/} (modo $MODO)"
aws ecs wait tasks-stopped --cluster maezo-operadora-dev --tasks "$TA"
# O container `staff-material` e o essencial; o `prepare` sai antes. Codigo != 0 = recusado/falhou.
CODIGO=$(aws ecs describe-tasks --cluster maezo-operadora-dev --tasks "$TA" \
  --query "tasks[0].containers[?name=='staff-material'].exitCode | [0]" --output text)
sleep 5 # o CloudWatch Logs leva alguns segundos para expor o fim do stream
# Stream: <prefixo>/<container>/<taskId>. Filtra pelo id da task: o grupo e compartilhado entre execucoes.
# Via boto3: no Git Bash do Windows o `/ecs/...` na linha de comando da aws virava um caminho `C:/...`, e
# `MSYS_NO_PATHCONV=1` quebra a propria aws; o boto3 nao passa pelo shell.
python - "${TA##*/}" <<'PY'
import sys, time

import boto3

task = sys.argv[1]
logs = boto3.client("logs", region_name="sa-east-1")
inicio = int((time.time() - 1800) * 1000)
for pagina in logs.get_paginator("filter_log_events").paginate(
    logGroupName="/ecs/maezo-operadora-dev/staff-material", startTime=inicio
):
    for evento in pagina["events"]:
        if task in evento["logStreamName"]:
            print(evento["message"])
PY
echo "exitCode do staff-material: $CODIGO"
test "$CODIGO" = "0"
