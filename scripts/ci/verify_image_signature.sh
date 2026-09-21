#!/usr/bin/env bash
# VERIFICA a assinatura e o SBOM de uma imagem, pela chave KMS da conta.
#
#   ./scripts/ci/verify_image_signature.sh <registry>/<repo>@sha256:<64 hex>
#
# E' o MESMO comando que o CI roda e que uma pessoa roda antes de aplicar um
# digest novo — de proposito: portao conferido por um caminho e documentado por
# outro vira dois portoes diferentes, e um deles apodrece.
#
# Precisa de: cosign, e credencial da conta com `kms:Verify` + leitura do ECR
# (`aws ecr get-login-password ... | cosign login ...` antes, se for repositorio
# privado). NAO precisa de rede para fora da AWS: a confianca e' a chave, nao o
# Rekor — por isso `--insecure-ignore-tlog`, que aqui significa "nao ha log de
# transparencia publico neste desenho", e nao "ignore uma checagem que existia".
set -euo pipefail

IMAGE_REF="${1:-}"
if [ -z "${IMAGE_REF}" ]; then
  echo "uso: $0 <registry>/<repo>@sha256:<64 hex>" >&2
  exit 2
fi
case "${IMAGE_REF}" in
  *@sha256:*) : ;;
  # Verificar por tag seria verificar um alvo movel: a tag pode apontar para
  # outro artefato depois que alguem conferiu.
  *) echo "ERRO: verifique por DIGEST (repo@sha256:...), nunca por tag." >&2; exit 2 ;;
esac

KMS_KEY_URI="${KMS_KEY_URI:-awskms:///alias/maezo-operadora-dev-image-signing}"
REFERRERS_MODE="${REFERRERS_MODE:-legacy}"
# O cosign ja terminou a verificacao e mesmo assim demora a ENCERRAR (bug
# conhecido do proprio cosign). O veredito vem do marcador no stderr, nao do
# tempo: se o bloco de checagens apareceu, passou; se nao apareceu ate o timeout,
# reprova.
TIMEOUT_S="${TIMEOUT_S:-300}"
MARCADOR="The following checks were performed"

_run() {
  local titulo="$1" saida="$2"; shift 2
  local rc=0
  echo "== ${titulo}"
  timeout -s INT "${TIMEOUT_S}" "$@" > "${saida}.stdout" 2> "${saida}" || rc=$?
  cat "${saida}"
  if ! grep -q "${MARCADOR}" "${saida}"; then
    echo "::error title=${titulo} REPROVOU::sem o bloco de verificacao do cosign para ${IMAGE_REF}"
    return 1
  fi
  if [ "${rc}" -ne 0 ]; then
    echo "::warning title=${titulo}::cosign verificou mas nao encerrou sozinho (rc=${rc}); veredito lido do marcador."
  fi
  echo "-> ${titulo}: OK"
}

_run "cosign verify (assinatura)" cosign-verify.txt \
  cosign verify \
    --key "${KMS_KEY_URI}" \
    --insecure-ignore-tlog=true \
    --registry-referrers-mode="${REFERRERS_MODE}" \
    "${IMAGE_REF}"

_run "cosign verify-attestation (SBOM SPDX)" cosign-verify-attestation.txt \
  cosign verify-attestation \
    --key "${KMS_KEY_URI}" \
    --insecure-ignore-tlog=true \
    --registry-referrers-mode="${REFERRERS_MODE}" \
    --type spdxjson \
    "${IMAGE_REF}"

echo "VERIFICADO: ${IMAGE_REF}"
