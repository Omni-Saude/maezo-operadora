# Cadeia de suprimentos da imagem — SBOM e assinatura (o portão 1.4 do portal)

O item 1.4 de `deploy/aws-ecs/envs/dev-sa-east-1/portal.md` exige **SBOM e assinatura
verificados do digest exato** antes de aceitar uma imagem para o portal. Este runbook diz
**qual identidade assina**, **qual issuer**, **onde os artefatos moram** e **o comando exato**
que qualquer pessoa roda para conferir um digest — sem precisar de credencial de deploy.

Vale para o repositório ECR `amh/maezo-operadora` (conta `203312548462`, `sa-east-1`).

## 1. O que o portão passa a significar

Um digest é aceitável quando, e só quando, as duas verificações abaixo saem verdes:

| O quê | Como é provado |
|---|---|
| **Assinatura** | `cosign verify` sobre `repo@sha256:...`, com a identidade e o issuer da tabela 2 |
| **SBOM** | `cosign verify-attestation --type spdxjson` sobre o MESMO `repo@sha256:...` |

Verde significa: existe, no registro, uma assinatura Sigstore cujo certificado efêmero foi
emitido pelo Fulcio para **a URI do workflow `supply-chain.yml` deste repositório**,
autenticado pelo **OIDC do GitHub Actions**, e um atestado SPDX ligado ao mesmo digest.

O que **não** significa: que o conteúdo do SBOM foi auditado, que a imagem não tem CVE, ou
que alguém leu o que está dentro dela. Assinatura prova **origem e integridade**, não
qualidade. As CVEs são assunto do portão L3 (`.github/workflows/security.yml`).

## 2. Quem assina, e com o quê

| | Valor |
|---|---|
| Raiz de confiança | **Sigstore keyless** (Fulcio + Rekor). **Não há chave** — nem KMS, nem secret no repositório |
| Identidade (`--certificate-identity-regexp`) | `^https://github\.com/Omni-Saude/maezo-operadora/\.github/workflows/supply-chain\.yml@refs/heads/.+$` |
| Issuer (`--certificate-oidc-issuer`) | `https://token.actions.githubusercontent.com` |
| Quem publica | `.github/workflows/supply-chain.yml` |
| Permissão na AWS | role `maezo-operadora-dev-github-supply-chain` (`deploy/aws-ecs/envs/dev-sa-east-1/iam-github-supply-chain.tf`) |
| Onde ficam os artefatos | no próprio ECR, como tags `sha256-<digest sem prefixo>.sig` e `.att` |

**Por que keyless e não KMS:** o `cd.yml` já declarava esta raiz de confiança nos passos
`Sign image (cosign keyless / Sigstore OIDC)` e `Attest SBOM`. Assinar com uma chave KMS
criaria uma **segunda** raiz de confiança, diferente da declarada, só para satisfazer a letra
do portão — e uma chave é um segredo a mais para rotacionar, revogar e vazar. A identidade
que assina aqui é um **arquivo de workflow em `main`**, revisável por PR e coberto pelo
CODEOWNERS de `/.github/workflows/`.

**Consequência que precisa ser dita:** quem consegue mudar `supply-chain.yml` em `main`
consegue assinar. É por isso que a verificação fixa a URI do workflow — e é por isso que o
gate de revisão daquele diretório é parte do portão, não burocracia.

## 3. Verificar um digest (o comando exato)

Precisa apenas de `cosign` e de leitura no ECR (`aws ecr get-login-password`). Não precisa
da role de assinatura.

```bash
DIGEST=sha256:685ddb6d80c89f713ac776700bb7b759cb56253d85ab1b273cc7946ecdf12c3c
REPO=203312548462.dkr.ecr.sa-east-1.amazonaws.com/amh/maezo-operadora

aws ecr get-login-password --region sa-east-1 --profile adm-dev \
  | cosign login 203312548462.dkr.ecr.sa-east-1.amazonaws.com -u AWS --password-stdin

# 1) assinatura
cosign verify \
  --certificate-identity-regexp '^https://github\.com/Omni-Saude/maezo-operadora/\.github/workflows/supply-chain\.yml@refs/heads/.+$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  "${REPO}@${DIGEST}"

# 2) SBOM (atestação SPDX sobre o MESMO digest)
cosign verify-attestation --type spdxjson \
  --certificate-identity-regexp '^https://github\.com/Omni-Saude/maezo-operadora/\.github/workflows/supply-chain\.yml@refs/heads/.+$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  "${REPO}@${DIGEST}"
```

Saída esperada de cada um: `Verification for ... --> The following checks were performed...`
seguida do JSON do bundle. Qualquer outra coisa — em especial
`no matching signatures` — significa **portão não satisfeito**; não interprete, não contorne.

Conferência independente, sem cosign, de que os artefatos existem no registro:

```bash
aws ecr describe-images --repository-name amh/maezo-operadora --region sa-east-1 \
  --profile adm-dev --query "imageDetails[?imageTags!=null].imageTags[]" --output text \
  | tr '\t' '\n' | grep "^sha256-${DIGEST#sha256:}"
# esperado: sha256-<...>.sig  e  sha256-<...>.att
```

Ler o SBOM em si (lista de pacotes) é `cosign download attestation ... | jq -r .payload |
base64 -d | jq .predicate` — útil em resposta a CVE, não é parte do portão.

## 4. Assinar um digest novo

1. A imagem já tem de estar no ECR. **Quem constrói é o CodeBuild**
   (`deploy/aws-ecs/envs/dev-sa-east-1/codebuild.tf`), não este workflow: aqui não se
   constrói nem se implanta nada, só se acrescenta prova sobre um artefato existente.
2. `gh workflow run supply-chain.yml --ref main -f image=sha256:<64 hex>`
   (aceita também uma tag; o workflow resolve para o digest antes de assinar — assinar por
   tag seria assinar um alvo móvel).
3. Sem `-f image`, o workflow assina o `image_digest` declarado em
   `deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars`. É o mesmo caminho que roda no
   `push` para `main`: **trocar o digest do portal passa a exigir, no mesmo push, SBOM e
   assinatura verificados do digest novo**.
4. O job só fica verde depois de `cosign verify` **e** `cosign verify-attestation`. Assinar
   sem verificar é metade do portão.

## 5. Detalhes do ECR que já custaram tempo

- **Imutabilidade não atrapalha.** O repositório é `IMMUTABLE` (`ecs.tf`), mas os artefatos
  do cosign são **tags novas** (`sha256-<digest>.sig` / `.att`), nunca reescrita de uma tag
  existente. Confirmado em execução real.
- **Retenção.** As assinaturas são imagens tageadas para o ECR e concorriam na mesma
  contagem de 20 da política de ciclo de vida: cada digest assinado empurraria **duas**
  imagens de aplicação para fora, e a assinatura podia expirar antes da imagem que ela
  prova. Por isso existe a regra de prioridade 2 em `ecs.tf`, que retém 60 artefatos
  `sha256-*` e os tira da contagem da regra seguinte.
- **A role não implanta.** `maezo-operadora-dev-github-supply-chain` tem
  `ecr:GetAuthorizationToken` e, **só no repositório da aplicação**, leitura
  (`BatchGetImage`, `GetDownloadUrlForLayer`, `BatchCheckLayerAvailability`,
  `DescribeImages`) e publicação (`PutImage`, `InitiateLayerUpload`, `UploadLayerPart`,
  `CompleteLayerUpload`). Sem `ecr:DeleteImage`, sem `ecs:*`, sem `secretsmanager:*`.
- **A confiança é por `sub`**, restrita a `repo:Omni-Saude/maezo-operadora` em refs
  nomeadas. As roles `github-actions-ecr-push` / `github-actions-deploy` da conta confiam em
  **outro** repositório (`amh-data-platform`) e **não** foram alargadas — alargá-las daria a
  este workflow permissão de deploy que ele não precisa.
- **`AWS_ENABLED` continua ausente, e isso é de propósito.** Os passos de syft/cosign do
  `cd.yml` estão atrás daquele gate junto com build, push e deploy; ligá-lo para obter SBOM
  habilitaria o caminho de deploy inteiro. Este workflow não lê nem define essa variável.

## 6. Histórico

- **2026-09-21** — portão fechado pela primeira vez. Role, workflow e este runbook criados
  após review P1 do dono em `Omni-Saude/maezo-operadora#454` (o portal tinha sido ligado com
  o item 1.4 não satisfeito; funcionar não satisfaz o portão). Digest assinado e verificado:
  `sha256:685ddb6d80c89f713ac776700bb7b759cb56253d85ab1b273cc7946ecdf12c3c` (tag `8b014a60`),
  o que o portal consome.
