#!/usr/bin/env bash
# Roda um script Python DENTRO da VPC de dev numa task avulsa existente e despeja o log do stream dela.
# Runbook: docs/runbooks/renovar-material-staff-dev.md. Variante bash de scripts/ops/run-db-task.ps1 para
# as imagens de OPERACAO, cujo ENTRYPOINT e `python -m` (containerOverrides nao troca entryPoint): o codigo
# viaja gzip+base64 no `-s` do `python -m timeit` (teto de 8192 bytes do override).
#
# Segredo NUNCA vai no override (ecs:DescribeTasks devolve o override inteiro): o script le o que precisa
# por GetSecretValue com a task role. Aqui so codigo e variaveis NAO secretas (CHAVE=valor).
#
# uso: AWS_PROFILE=adm-dev scripts/ops/staff_vpc_run.sh <script.py> <familia-td> <container> <grupo-log> [CHAVE=valor...]
#  ex: scripts/ops/staff_vpc_run.sh scripts/ops/staff_material_reparo_vpc.py maezo-operadora-dev-staff-syn staff-syn staff-syn
set -euo pipefail
export MSYS_NO_PATHCONV=1 AWS_REGION=sa-east-1
SCRIPT=$1 TD=$2 CONTAINER=$3 GRUPO=$4
shift 4
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
python - "$SCRIPT" "$CONTAINER" "$@" > "$TMP/ov.json" <<'PY'
import base64, gzip, json, sys
codigo = base64.b64encode(gzip.compress(open(sys.argv[1], "rb").read())).decode()
setup = f"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('{codigo}')),'<vpc>','exec'),{{'__name__':'vpc'}})"
env = [dict(zip(("name", "value"), kv.split("=", 1))) for kv in sys.argv[3:]]
print(json.dumps({"containerOverrides": [{"name": sys.argv[2], "command": ["timeit", "-n", "1", "-r", "1", "-s", setup, "pass"], "environment": env}]}))
PY
# No Git Bash do Windows a aws.exe nao enxerga o /tmp do MSYS: caminho misto (C:/...).
OV="$TMP/ov.json"; command -v cygpath >/dev/null && OV=$(cygpath -m "$OV")
NET="awsvpcConfiguration={subnets=[subnet-0e1f840dbec44e66a,subnet-06647b0c664d0d22a],securityGroups=[sg-0e2aebe1a2c1d253d],assignPublicIp=DISABLED}"
TA=$(aws ecs run-task --cluster maezo-operadora-dev --task-definition "$TD" --launch-type FARGATE \
  --network-configuration "$NET" --overrides "file://$OV" --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster maezo-operadora-dev --tasks "$TA"
aws ecs describe-tasks --cluster maezo-operadora-dev --tasks "$TA" --query 'tasks[0].containers[].[name,exitCode]' --output text
# O stream e <prefixo>/<container>/<taskId>: tasks avulsas compartilham o grupo, nunca pegue "o mais recente".
aws logs filter-log-events --log-group-name "/ecs/maezo-operadora-dev/$GRUPO" \
  --start-time $(( ($(date +%s) - 900) * 1000 )) --query 'events[].[logStreamName,message]' --output text \
  | grep "${TA##*/}" | cut -f2 | grep -v 'loop, best of'
