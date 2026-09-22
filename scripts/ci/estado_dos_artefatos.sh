#!/usr/bin/env bash
# Para um digest ja' assinado, diz o que ainda FALTA publicar.
#
# POR QUE ISTO EXISTE. Este repositorio do ECR e' `IMMUTABLE`, e o cosign no modo
# legacy publica em TAGS derivadas do digest: `sha256-<hex>.sig` e
# `sha256-<hex>.att`. Rodar o workflow duas vezes sobre o MESMO digest tenta
# escrever a mesma tag de novo, e o registro recusa:
#
#   TAG_INVALID: The image tag 'sha256-<hex>.att' already exists ... and cannot be
#   overwritten because the tag is immutable.
#
# (medido: run 35666402636). A assinatura passou nessa mesma execucao porque o
# manifesto dela saiu byte a byte igual ao que ja' estava la' — um PUT identico o
# ECR aceita como no-op. A atestacao nao: o SBOM e' gerado na hora e carrega o
# instante da geracao, entao o manifesto muda e o PUT vira conflito. Ou seja o
# job so' era verde na PRIMEIRA vez, e qualquer re-run — re-run manual, push que
# reaproveita o digest, retentativa depois de falha adiante — ficava vermelho por
# um motivo que nao e' defeito nenhum.
#
# O QUE ESTE SCRIPT NAO FAZ, e e' o ponto: ele NAO diz "ja' existe, entao esta'
# bom". Ele so' decide se ha' o que PUBLICAR. Quem diz se o artefato presta e' a
# etapa de verificacao, que roda sempre, depois desta, e reprova o job se a
# assinatura ou o SBOM nao baterem com a chave. A diferenca importa: um `.att`
# invalido preso numa tag imutavel continua deixando o job VERMELHO — e tem de
# deixar, porque so' uma pessoa pode resolver isso (apagar a tag ou republicar a
# imagem). O que some e' o falso vermelho de "ja' esta' tudo publicado".
#
# Saida: falta_assinatura, falta_atestacao, tags_existentes -> $GITHUB_OUTPUT.
set -euo pipefail

: "${AWS_REGION:?}"
: "${ECR_REPOSITORY:?}"
: "${DIGEST_HEX:?}"

prefixo="sha256-${DIGEST_HEX}"

# Uma chamada so': `describe-images` por digest NAO lista as tags dos artefatos do
# cosign (eles sao OUTRAS imagens, com outro digest), por isso a busca e' pelo
# prefixo no repositorio inteiro.
#
# A chamada e' feita em DOIS tempos, e isso nao e' estilo. Escrever
# `aws ... 2>/dev/null | grep ... || true` numa linha so' faz um erro da AWS —
# credencial vencida, conta errada, permissao faltando — virar "lista vazia", e
# lista vazia aqui significa "falta publicar tudo". O job entao tentaria
# republicar e morreria com `TAG_INVALID`: o sintoma volta, so' que agora vindo de
# um lugar onde ninguem procura. Medido de verdade enquanto eu escrevia este
# script, com credencial de outra conta. Entao: a saida da AWS e' capturada e o
# codigo dela e' conferido ANTES de qualquer filtro. O unico nao-zero tolerado e'
# o do `grep` sem casamento, que e' resposta e nao erro.
if ! bruto="$(aws ecr describe-images \
  --repository-name "${ECR_REPOSITORY}" \
  --region "${AWS_REGION}" \
  --query "imageDetails[].imageTags[]" \
  --output text)"; then
  echo "::error title=Nao foi possivel LER o ECR::sem a lista de tags nao da' para saber o que ja' foi publicado, e assumir 'nada' faria o job tentar reescrever uma tag imutavel." >&2
  exit 1
fi
tags="$(printf '%s\n' "${bruto}" | tr '\t' '\n' | grep "^${prefixo}" || true)"

existe() {
  printf '%s\n' "${tags}" | grep -qx "$1"
}

falta_assinatura=true
falta_atestacao=true
existe "${prefixo}.sig" && falta_assinatura=false
existe "${prefixo}.att" && falta_atestacao=false

echo "== artefatos ja' publicados para ${prefixo}"
if [ -n "${tags}" ]; then printf '%s\n' "${tags}"; else echo "(nenhum)"; fi
echo "-> falta assinar:  ${falta_assinatura}"
echo "-> falta atestar:  ${falta_atestacao}"
if [ "${falta_assinatura}" = false ] || [ "${falta_atestacao}" = false ]; then
  echo "::notice title=Artefato ja' publicado::o que ja' existe nao e' republicado (tag imutavel); a verificacao adiante decide se presta."
fi

{
  echo "falta_assinatura=${falta_assinatura}"
  echo "falta_atestacao=${falta_atestacao}"
  echo "tags_existentes<<FIM"
  printf '%s\n' "${tags}"
  echo "FIM"
} >> "${GITHUB_OUTPUT:-/dev/stdout}"
