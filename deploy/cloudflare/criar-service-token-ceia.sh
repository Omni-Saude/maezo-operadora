#!/usr/bin/env bash
# Cria o service token do Cloudflare Access do time do CEIA e guarda o Client Secret no
# Secrets Manager. O secret NAO passa por Terraform, disco nem terminal.
#
# Por que um script e nao Terraform: o Client Secret so' e' devolvido na criacao; se o
# Terraform criasse o token ele iria para o state (arquivo que muita gente le). O
# Terraform so' referencia o ID, que nao e' segredo (var `ceia_service_token_id`).
#
# Uso:
#   export CLOUDFLARE_API_TOKEN=...      # permissao: Access: Service Tokens Edit
#   export AWS_PROFILE=adm-dev
#   ./criar-service-token-ceia.sh
# Saida: so' o ID do token (nao-secreto) e a data de validade. Ponha o ID em
# `terraform.tfvars` (ceia_service_token_id) e abra o PR; o apply segue o fluxo do repo.
#
# Uso pelo CEIA: cabecalhos `CF-Access-Client-Id` e `CF-Access-Client-Secret` em
# https://maezo-dev.austa.com.br/engine-rest/... Entregar o secret por canal seguro
# (leitura do segredo no Secrets Manager), nunca por chat, e-mail ou PR.
set -euo pipefail

ACCOUNT_ID="500155ff329616cdfb16d45929318e87"
NOME="ceia-engine-rest-dev"
DURACAO="2160h" # 90 dias: validade curta de proposito; renovar = rodar de novo e trocar o ID
SEGREDO="maezo/dev/ceia/cloudflare-access-service-token"
REGIAO="sa-east-1"
API="https://api.cloudflare.com/client/v4"

: "${CLOUDFLARE_API_TOKEN:?defina CLOUDFLARE_API_TOKEN}"
command -v aws >/dev/null || { echo "aws CLI nao encontrado"; exit 1; }

RESP=$(curl -sS -X POST -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json" \
  "${API}/accounts/${ACCOUNT_ID}/access/service_tokens" \
  --data "{\"name\":\"${NOME}\",\"duration\":\"${DURACAO}\"}")

# Extrai direto para o Secrets Manager; imprime so' id e validade. Aborta se success=false.
echo "$RESP" | python -c '
import json, sys

import boto3

d = json.load(sys.stdin)
if not d.get("success"):
    sys.exit("Cloudflare recusou: " + "; ".join(e.get("message", "?") for e in d.get("errors") or []))
r = d["result"]
# boto3, nao a CLI: o secret nao vai em argumento de processo (visivel em `ps`).
boto3.client("secretsmanager", region_name=sys.argv[2]).create_secret(
    Name=sys.argv[1],
    SecretString=json.dumps({"client_id": r["client_id"], "client_secret": r["client_secret"]}),
)
print("token_id=" + r["id"])
print("expira_em=" + str(r.get("expires_at")))
print("secret gravado em " + sys.argv[1])
' "$SEGREDO" "$REGIAO"
