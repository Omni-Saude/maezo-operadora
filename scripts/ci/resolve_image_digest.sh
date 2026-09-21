#!/usr/bin/env bash
# Resolve o DIGEST da imagem de aplicacao que sera assinada/verificada.
#
# Entrada (env):
#   REQUESTED       digest `sha256:<64 hex>` OU tag. Vazio = o digest que o PORTAL
#                   consome, lido de ${PORTAL_TFVARS}.
#   PORTAL_TFVARS   caminho do tfvars do portal.
#   ECR_REPOSITORY  ex.: amh/maezo-operadora
#   AWS_REGION      ex.: sa-east-1
#   REGISTRY        host do ECR (saida do amazon-ecr-login)
#
# Saida: digest, digest_hex, image_ref, sbom_file, skip -> $GITHUB_OUTPUT.
#
# Por que a fonte default e' o tfvars do portal, e nao a tag do commit: o portao
# de `portal.md` 1.4 e' sobre o ARTEFATO QUE O PORTAL ACEITA. Amarrando o alvo
# aquele arquivo, trocar o digest do portal passa a exigir, no mesmo PR, SBOM e
# assinatura verificados do digest novo.
set -euo pipefail

: "${ECR_REPOSITORY:?}" "${AWS_REGION:?}" "${REGISTRY:?}" "${GITHUB_OUTPUT:?}"
PORTAL_TFVARS="${PORTAL_TFVARS:-deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars}"
requested="${REQUESTED:-}"

if [ -z "${requested}" ]; then
  requested="$(grep -Eo 'image_digest[[:space:]]*=[[:space:]]*"sha256:[0-9a-f]{64}"' \
                "${PORTAL_TFVARS}" 2>/dev/null | grep -Eo 'sha256:[0-9a-f]{64}' | head -n 1 || true)"
  if [ -z "${requested}" ]; then
    # Acontece enquanto o tfvars do portal nao existir no branch (ele entra pelo
    # PR do portal). Nada a assinar nao e' o mesmo que portao violado.
    echo "::notice title=Sem digest declarado::Nenhum image_digest em ${PORTAL_TFVARS}; nada a fazer."
    echo "skip=true" >> "$GITHUB_OUTPUT"
    exit 0
  fi
  echo "Sem entrada explicita: alvo = o digest que o portal consome (${requested})."
fi

if printf '%s' "${requested}" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
  digest="${requested}"
  if ! aws ecr describe-images --repository-name "${ECR_REPOSITORY}" \
       --image-ids imageDigest="${digest}" --region "${AWS_REGION}" >/dev/null 2>&1; then
    echo "::error title=Digest inexistente::${ECR_REPOSITORY}@${digest} nao existe no ECR."
    exit 1
  fi
else
  digest="$(aws ecr describe-images --repository-name "${ECR_REPOSITORY}" \
              --image-ids imageTag="${requested}" --region "${AWS_REGION}" \
              --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)"
  if [ -z "${digest}" ] || [ "${digest}" = "None" ]; then
    echo "::error title=Tag inexistente::Tag '${requested}' nao existe em ${ECR_REPOSITORY}."
    exit 1
  fi
fi

{
  echo "skip=false"
  echo "digest=${digest}"
  echo "digest_hex=${digest#sha256:}"
  echo "image_ref=${REGISTRY}/${ECR_REPOSITORY}@${digest}"
  echo "sbom_file=sbom-${digest#sha256:}.spdx.json"
} >> "$GITHUB_OUTPUT"

echo "Alvo: ${REGISTRY}/${ECR_REPOSITORY}@${digest}"
