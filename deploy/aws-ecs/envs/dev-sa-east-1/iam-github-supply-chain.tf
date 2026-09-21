# ---------------------------------------------------------------------------
# Role assumida pelo workflow `.github/workflows/supply-chain.yml` (GitHub OIDC)
# para PUBLICAR SBOM + assinatura cosign do digest da imagem de aplicacao.
#
# POR QUE AQUI, e nao em `deploy/terraform/modules/github-oidc`:
#   aquele modulo e' consumido pelas raizes `deploy/terraform/envs/*` (a stack EKS
#   antiga, provider AWS ~> 5.0, state proprio) e nao conhece o repositorio ECR
#   `amh/maezo-operadora` — que e' recurso DESTE state (`ecs.tf`,
#   `aws_ecr_repository.app`). Uma policy de menor privilegio precisa do ARN
#   concreto do repositorio; declara-la aqui mantem dono e recurso no mesmo state
#   e evita um `data` cross-state so' para descobrir o ARN.
#
# RAIZ DE CONFIANCA: a chave KMS assimetrica de `kms-image-signing.tf` (alias
# fixo). Esta e' a UNICA identidade autorizada a chamar `kms:Sign` com ela — o
# deny explicito da policy da chave fecha o resto da conta, inclusive
# administrador. O motivo de ser chave e nao keyless esta escrito la: o portao e'
# VERIFICAR NO DEPLOY, e verificacao keyless dependeria de alcancar Fulcio/Rekor
# num ambiente cujo egresso e' allowlist de /32.
#
# ESCOPO: ler e escrever no repositorio `amh/maezo-operadora` e usar a chave de
# assinatura. Nada mais. Sem `ecr:DeleteImage`, sem `ecs:*`, sem
# `secretsmanager:*`, sem `iam:*`. Publicar assinatura nao e' implantar.
# ---------------------------------------------------------------------------

# O provider OIDC do GitHub ja existe nesta conta (criado pelo amh-data-platform).
# Lido, nunca criado: um segundo provider para a mesma URL e' recusado pela AWS, e
# o dono dele nao e' este state.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

locals {
  github_repo_sub = "repo:Omni-Saude/maezo-operadora"

  # Subs aceitos. Duas entradas, nenhuma com curinga de repositorio:
  #   1) `main` — o caminho permanente (push em main e workflow_dispatch a partir
  #      de main emitem este mesmo sub);
  #   2) o branch DESTA entrega — temporario, e existe por um motivo mecanico: o
  #      GitHub so' aceita `workflow_dispatch` de um workflow que ja esteja no
  #      branch default, entao a PRIMEIRA execucao (a que prova o portao antes do
  #      merge) so' pode vir por `push` neste branch. Remover depois do merge.
  # `pull_request` NAO entra aqui de proposito: num PR o workflow que roda e' o do
  # HEAD do PR, entao qualquer um que abrisse um PR editando `supply-chain.yml`
  # estaria assinando com a chave. Quem roda em PR e' a role VERIFY-ONLY abaixo.
  github_supply_chain_subs = [
    "${local.github_repo_sub}:ref:refs/heads/main",
    "${local.github_repo_sub}:ref:refs/heads/ci/supply-chain-sbom-assinatura",
  ]

  # Role de VERIFICACAO: roda em PR e em main, nao assina nada.
  github_verify_subs = [
    "${local.github_repo_sub}:ref:refs/heads/main",
    "${local.github_repo_sub}:ref:refs/heads/ci/supply-chain-sbom-assinatura",
    "${local.github_repo_sub}:pull_request",
  ]
}

data "aws_iam_policy_document" "github_supply_chain_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    # Sem esta condicao a role aceitaria token de QUALQUER repositorio do GitHub.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_supply_chain_subs
    }
  }
}

resource "aws_iam_role" "github_supply_chain" {
  name = "${local.name}-github-supply-chain"
  # ASCII puro de proposito: o IAM recusa `description` com caractere fora de
  # [\u0009\u000A\u000D -~¡-ÿ] — um travessao aqui derruba o
  # CreateRole com ValidationError (medido).
  description = "GitHub Actions (Omni-Saude/maezo-operadora): publica SBOM + assinatura cosign no ECR amh/maezo-operadora. Sem deploy."

  assume_role_policy = data.aws_iam_policy_document.github_supply_chain_trust.json

  # 1h: o job inteiro (login, syft, cosign sign/attest, verify) roda em minutos.
  max_session_duration = 3600

  tags = merge(local.base_tags, { Name = "${local.name}-github-supply-chain" })
}

data "aws_iam_policy_document" "github_supply_chain" {
  # GetAuthorizationToken nao aceita recurso: a AWS so' avalia `*`. E' o token de
  # login do registro, nao acesso a repositorio nenhum.
  statement {
    sid       = "EcrLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # Leitura do artefato a assinar (o syft baixa as camadas; o cosign le o manifesto
  # e o `verify` le de volta o `.sig`/`.att` publicado).
  statement {
    sid    = "EcrReadAppRepository"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      # Usada SO' pelo passo de evidencia do workflow, que imprime as tags
      # `sha256-*.sig`/`.att` publicadas. Leitura de metadado, nao de conteudo.
      "ecr:DescribeImages",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  # Escrita EXCLUSIVA dos artefatos de assinatura: o cosign publica `.sig` e `.att`
  # como imagens OCI novas, com TAG NOVA (`sha256-<digest>.sig`). Por isso o
  # `image_tag_mutability = "IMMUTABLE"` do repositorio nao atrapalha: nenhuma tag
  # existente e' sobrescrita. E por isso tambem NAO ha `ecr:DeleteImage` aqui —
  # esta role pode acrescentar prova, nunca apagar artefato.
  statement {
    sid    = "EcrPushSignatureArtifacts"
    effect = "Allow"
    actions = [
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  # A chave de assinatura. `kms:Sign` aqui e' metade da historia: a outra metade
  # e' o deny da policy da propria chave, que fecha o resto da conta.
  statement {
    sid    = "KmsSignImages"
    effect = "Allow"
    actions = [
      "kms:Sign",
      "kms:Verify",
      "kms:GetPublicKey",
      "kms:DescribeKey",
    ]
    resources = [aws_kms_key.image_signing.arn]
  }
}

resource "aws_iam_policy" "github_supply_chain" {
  name        = "${local.name}-github-supply-chain"
  description = "Menor privilegio para publicar/verificar SBOM e assinatura cosign em amh/maezo-operadora."
  policy      = data.aws_iam_policy_document.github_supply_chain.json
  tags        = local.base_tags
}

resource "aws_iam_role_policy_attachment" "github_supply_chain" {
  role       = aws_iam_role.github_supply_chain.name
  policy_arn = aws_iam_policy.github_supply_chain.arn
}

# ---------------------------------------------------------------------------
# Role VERIFY-ONLY — o portao no caminho de entrega.
#
# "Assinatura que ninguem confere nao e' portao": o job `verificar-digest-do-portal`
# roda em PULL REQUEST e confere o digest declarado em `portal.auto.tfvars`. Num PR
# quem roda e' o workflow do HEAD do PR, entao este caminho NAO PODE ter `kms:Sign`
# nem `ecr:PutImage` — se tivesse, abrir um PR seria o bastante para assinar
# qualquer coisa. Esta role so' LE o registro e chama `kms:Verify`.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "github_verify_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.github_verify_subs
    }
  }
}

resource "aws_iam_role" "github_verify" {
  name        = "${local.name}-github-verify"
  description = "GitHub Actions (Omni-Saude/maezo-operadora): VERIFICA assinatura/SBOM de um digest. Sem assinar, sem publicar, sem deploy."

  assume_role_policy   = data.aws_iam_policy_document.github_verify_trust.json
  max_session_duration = 3600

  tags = merge(local.base_tags, { Name = "${local.name}-github-verify" })
}

data "aws_iam_policy_document" "github_verify" {
  statement {
    sid       = "EcrLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrReadAppRepository"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:DescribeImages",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    sid    = "KmsVerifyOnly"
    effect = "Allow"
    actions = [
      "kms:Verify",
      "kms:GetPublicKey",
      "kms:DescribeKey",
    ]
    resources = [aws_kms_key.image_signing.arn]
  }
}

resource "aws_iam_policy" "github_verify" {
  name        = "${local.name}-github-verify"
  description = "Menor privilegio para VERIFICAR assinatura/SBOM de imagem. Sem Sign, sem PutImage."
  policy      = data.aws_iam_policy_document.github_verify.json
  tags        = local.base_tags
}

resource "aws_iam_role_policy_attachment" "github_verify" {
  role       = aws_iam_role.github_verify.name
  policy_arn = aws_iam_policy.github_verify.arn
}
