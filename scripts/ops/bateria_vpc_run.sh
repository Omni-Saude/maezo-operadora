#!/usr/bin/env bash
# Roda um script Python (so' biblioteca padrao) DENTRO da VPC de dev na task avulsa `bateria` e despeja o log.
# Variante de scripts/ops/staff_vpc_run.sh para a `bateria`, cujo entryPoint e' so' o interpretador
# (`command` livre: `["-c", "<codigo>"]`). A `bateria` NAO tem senha nem identidade AWS; o SG dela alcanca
# cibseven:8080/engine-rest (aberto so' em dev, decisao N5). Runbook: docs/runbooks/cancel-001-pf.md.
#
# Nada secreto no override (ecs:DescribeTasks devolve o override inteiro): so' codigo e CHAVE=valor publicos.
#
# uso: AWS_PROFILE=adm-dev scripts/ops/bateria_vpc_run.sh <script.py> [CHAVE=valor...]
#  ex: AWS_PROFILE=adm-dev scripts/ops/bateria_vpc_run.sh scripts/ops/cancel_pf_dev.py MODO=listar
set -euo pipefail
export MSYS_NO_PATHCONV=1 AWS_REGION=sa-east-1
SCRIPT=$1
shift 1
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
python - "$SCRIPT" "$@" > "$TMP/ov.json" <<'PY'
import base64, gzip, json, sys
codigo = base64.b64encode(gzip.compress(open(sys.argv[1], "rb").read())).decode()
fonte = f"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('{codigo}')),'<vpc>','exec'),{{'__name__':'vpc'}})"
env = [dict(zip(("name", "value"), kv.split("=", 1))) for kv in sys.argv[2:]]
print(json.dumps({"containerOverrides": [{"name": "bateria", "command": ["-c", fonte], "environment": env}]}))
PY
OV="$TMP/ov.json"; command -v cygpath >/dev/null && OV=$(cygpath -m "$OV")
DONO=$(aws sts get-caller-identity --query UserId --output text | tr -d '\r')
NET="awsvpcConfiguration={subnets=[subnet-0e1f840dbec44e66a,subnet-06647b0c664d0d22a],securityGroups=[sg-0e2aebe1a2c1d253d],assignPublicIp=DISABLED}"
TA=$(aws ecs run-task --cluster maezo-operadora-dev --task-definition maezo-operadora-dev-bateria --launch-type FARGATE \
  --network-configuration "$NET" --no-enable-execute-command --overrides "file://$OV" \
  --tags key=MaezoPurpose,value=cancel-pf-dev "key=MaezoOwner,value=$DONO" \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster maezo-operadora-dev --tasks "$TA"
aws ecs describe-tasks --cluster maezo-operadora-dev --tasks "$TA" --query 'tasks[0].containers[].[name,exitCode]' --output text
aws logs filter-log-events --log-group-name "/ecs/maezo-operadora-dev/bateria" \
  --start-time $(( ($(date +%s) - 900) * 1000 )) --query 'events[].[logStreamName,message]' --output text \
  | grep "${TA##*/}" | cut -f2
