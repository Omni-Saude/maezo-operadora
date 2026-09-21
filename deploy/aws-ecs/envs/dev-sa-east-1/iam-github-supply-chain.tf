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
# RAIZ DE CONFIANCA: keyless (Sigstore/Fulcio + OIDC do GitHub), a MESMA que o
# `cd.yml` ja declara nos passos `Sign image (cosign keyless / Sigstore OIDC)` e
# `Attest SBOM`. Esta role NAO assina nada — ela so' da ao runner o direito de
# escrever os artefatos `sha256-<digest>.sig` / `.att` no ECR. Nenhuma chave de
# assinatura existe nesta conta, e de proposito: uma chave KMS criaria uma
# SEGUNDA raiz de confianca, diferente da declarada.
#
# ESCOPO: ler e escrever no repositorio `amh/maezo-operadora`, nada mais. Sem
# `ecr:DeleteImage`, sem `ecs:*`, sem `secretsmanager:*`, sem `iam:*`. Publicar
# assinatura nao e' implantar.
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
  github_supply_chain_subs = [
    "${local.github_repo_sub}:ref:refs/heads/main",
    "${local.github_repo_sub}:ref:refs/heads/ci/supply-chain-sbom-assinatura",
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
  name        = "${local.name}-github-supply-chain"
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
