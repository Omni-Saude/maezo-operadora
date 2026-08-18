#!/usr/bin/env bash
# Cria o túnel do maezo no Cloudflare e entrega o TOKEN direto ao Secrets Manager.
#
# Por que um script e não Terraform: criar o túnel devolve o token, e o token é
# credencial de borda — quem o tem publica hostname na zona da empresa. Se o
# Terraform criasse o túnel, o token ficaria no state, que é um arquivo que muita
# gente lê. Aqui ele nasce, vai para o cofre e não toca disco nem terminal.
#
# O que este script NÃO faz: política de Access, DNS e rota do túnel. Isso é
# declarativo e mora no Terraform (`envs/dev/`), revisável em pull request.
#
# Uso:
#   export CLOUDFLARE_API_TOKEN=...     # Tunnel:Edit, Access:Edit, Zone DNS:Edit
#   export AWS_PROFILE=maezo-data
#   ./criar-tunel.sh [nome-do-tunel] [zona]
set -euo pipefail

NOME_TUNEL="${1:-maezo-operadora-dev}"
ZONA="${2:-austa.com.br}"
SEGREDO="maezo/dev/cloudflared/tunnel-token"
REGIAO="sa-east-1"
API="https://api.cloudflare.com/client/v4"

: "${CLOUDFLARE_API_TOKEN:?defina CLOUDFLARE_API_TOKEN}"
command -v aws >/dev/null || { echo "aws CLI não encontrado"; exit 1; }

cf() { curl -sS -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json" "$@"; }

# Extrai um caminho do JSON da resposta e ABORTA se `success` for falso — sem isto,
# um token sem permissão devolveria 200 com `success: false` e o script seguiria
# adiante com valores vazios.
extrai() {
  python -c '
import json,sys
d = json.load(sys.stdin)
if not d.get("success", False):
    erros = "; ".join(e.get("message","?") for e in d.get("errors") or [])
    sys.exit("Cloudflare recusou: " + (erros or json.dumps(d)[:300]))
alvo = d["result"]
for parte in sys.argv[1].split("."):
    alvo = alvo[int(parte)] if parte.isdigit() else alvo[parte]
print(alvo)
' "$1"
}

echo "==> descobrindo a conta"
# NAO usamos /user/tokens/verify: um token ACCOUNT-OWNED (prefixo `cfat_`) e' recusado
# ali com "Invalid API Token" mesmo estando perfeito — medido em 18/08/2026, e quase
# me fez devolver um token bom como quebrado. A prova de vida e' uma chamada de
# recurso real, que e' o que a linha abaixo faz.
ACCOUNT_ID=$(cf "${API}/accounts?per_page=50" | extrai "0.id")
echo "    account_id = ${ACCOUNT_ID}"
echo "    (se a conta acima nao for a esperada, o token ve mais de uma: pare e escolha)"

echo "==> descobrindo a zona ${ZONA}"
ZONE_ID=$(cf "${API}/zones?name=${ZONA}" | extrai "0.id")
echo "    zone_id    = ${ZONE_ID}"

echo "==> criando o túnel ${NOME_TUNEL}"
# config_src=cloudflare: túnel gerenciado remotamente, para o Terraform poder
# declarar as rotas de ingress sem arquivo de config no container.
RESP=$(cf -X POST "${API}/accounts/${ACCOUNT_ID}/cfd_tunnel" \
  --data "{\"name\":\"${NOME_TUNEL}\",\"config_src\":\"cloudflare\"}")
TUNNEL_ID=$(printf '%s' "$RESP" | extrai "id")
echo "    tunnel_id  = ${TUNNEL_ID}"

echo "==> gravando o token no Secrets Manager (${SEGREDO})"
# Arquivo temporário com permissão restrita: JSON inline pela linha de comando
# perde as aspas em alguns shells e o ECS rejeita com "invalid character".
# O `aws` aqui e' o binario NATIVO do Windows: ele nao resolve o /tmp do MSYS.
# `mktemp` puro devolveu /tmp/tmp.XXXX e o put-secret-value falhou com
# "No such file or directory" — depois de o script ja ter destruido o arquivo, o que
# perdeu o token (recuperavel por API, mas so' porque ha um endpoint para isso).
# TMPDIR aponta para um caminho que os DOIS mundos veem.
TMPDIR="${TMPDIR:-$LOCALAPPDATA/Temp}"
TMP=$(mktemp "${TMPDIR}/maezo-tunnel-XXXXXX.json"); chmod 600 "$TMP"
TMP_WIN=$(cygpath -m "$TMP" 2>/dev/null || printf '%s' "$TMP")
trap 'rm -f "$TMP"' EXIT
printf '%s' "$RESP" | python -c '
import json,sys
d = json.load(sys.stdin)
sys.stdout.write(json.dumps({"token": d["result"]["token"]}))
' > "$TMP"
aws secretsmanager put-secret-value \
  --secret-id "${SEGREDO}" --secret-string "file://${TMP_WIN}" \
  --region "${REGIAO}" --query VersionId --output text
echo "    token gravado (não exibido em nenhum momento)"
echo ""
echo "    Se esta etapa falhar depois de o túnel existir, o token NÃO se perdeu:"
echo "    GET ${API}/accounts/<conta>/cfd_tunnel/<tunel>/token devolve o mesmo valor."

cat <<FIM

==> Escreva em deploy/cloudflare/envs/dev/terraform.tfvars:

account_id = "${ACCOUNT_ID}"
zone_id    = "${ZONE_ID}"
tunnel_id  = "${TUNNEL_ID}"

==> Depois, nesta ordem:

  cd deploy/cloudflare/envs/dev
  terraform init -backend-config="bucket=amh-tfstate-dev" \\
    -backend-config="key=envs/dev-sa-east-1/maezo-operadora-cloudflare/terraform.tfstate" \\
    -backend-config="region=${REGIAO}"
  terraform apply          # DNS + rota do tunel + Access com login por e-mail

  cd ../../../aws-ecs/envs/dev-sa-east-1
  terraform apply -var=cloudflared_desired_count=1 ...   # sobe a borda

O Access precisa existir ANTES da borda subir. Túnel no ar sem política de Access
é um subdomínio público — o túnel entrega, o Access filtra.
FIM
