# Module: github-oidc
# GitHub Actions OIDC trust + scoped IAM role for Maezo CI/CD.
# Mirrors amh-data-platform ADR-022 pattern (no long-lived keys).
# Two roles: plan (read-only, PR events) and deploy (push/main/env-gated).

locals {
  common_tags = merge(var.tags, {
    Module    = "github-oidc"
    ManagedBy = "terraform"
    Platform  = "maezo"
    Component = "ci-cd"
  })
}

# ---------------------------------------------------------------------------
# GitHub OIDC provider (singleton per account — conditional creation)
# ---------------------------------------------------------------------------
data "aws_iam_openid_connect_provider" "github_existing" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = var.create_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
  tags            = local.common_tags
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github_existing[0].arn
}

# ---------------------------------------------------------------------------
# Role: maezo-github-plan (PRs → terraform plan, read-only)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "plan_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:pull_request"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "maezo-github-plan-${var.environment}"
  description          = "Assumed by Maezo GitHub Actions on PRs (terraform plan). Read-only."
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = 3600
  tags                 = local.common_tags
}

# ---------------------------------------------------------------------------
# Policy inline de menor privilegio da role `plan` (gap D6-02 / decisao do dono
# R-041, opcao A). Substitui o attachment da policy gerenciada `ReadOnlyAccess`,
# que concedia leitura da conta INTEIRA a qualquer workflow de pull request e que
# a AWS EXPANDE ao longo do tempo sem revisao nossa.
#
# Escopo derivado dos 7 modulos de `deploy/terraform/modules/` e das raizes de
# `deploy/terraform/envs/` — um `sid` por servico que o `terraform plan` precisa
# LER para dar refresh no estado:
#
#   rds     modules/aurora-postgres/main.tf:37,45,69,106,143,158
#           (aws_db_subnet_group, aws_rds_cluster_parameter_group,
#            aws_db_parameter_group, aws_rds_cluster, aws_rds_cluster_instance)
#   ecr     modules/ecr/main.tf:15,31,96
#           (aws_ecr_repository, aws_ecr_lifecycle_policy, aws_ecr_repository_policy)
#   eks     modules/eks-cluster/main.tf:25,53,120
#           (data aws_eks_cluster, aws_eks_cluster, aws_eks_node_group)
#   aps     modules/observability/main.tf:30,63
#           (aws_prometheus_workspace, aws_prometheus_rule_group_namespace)
#   iam     modules/github-oidc/main.tf:18,23,59,100,155;
#           modules/eks-cluster/main.tf:98,105,111,150,164,170,176,190;
#           modules/observability/main.tf:100,140
#   ec2     envs/staging-sa-east-1/main.tf:26,30,38 e
#           envs/prod-amh-sa-east-1/main.tf:30,34,42 (data aws_vpc / aws_subnets)
#           + modules/aurora-postgres/main.tf:78 (aws_security_group)
#
# LIMITE QUE A PROPRIA DECISAO IMPOE (R-041, `floor_note`): nenhuma acao de
# leitura de segredo entra aqui. Em particular NAO ha `secretsmanager:*` nem
# `kms:Decrypt` — e por isso que os servicos abaixo ficaram DE FORA mesmo tendo
# recursos nos modulos. Cada um vira um `plan` VERMELHO e visivel no primeiro uso
# real (dependencia D13-03), que e exatamente o modo de falha escolhido:
#
#   secretsmanager  modules/secrets/main.tf:31,49,67,82,96,115,135,139 e
#                   modules/observability/main.tf:151,164. Atencao: o data source
#                   `aws_secretsmanager_secret_version.msk_bootstrap`
#                   (modules/secrets/main.tf:139) executa `GetSecretValue` em
#                   tempo de PLAN — a acao proibida pela decisao. Enquanto ela
#                   nao entrar por decisao explicita do dono, o plan das raizes
#                   falha ali.
#   kms             modules/aurora-postgres/main.tf:22,29;
#                   modules/eks-cluster/main.tf:39,47; envs/*/main.tf
#   logs            modules/observability/main.tf:43
#   grafana         modules/observability/main.tf:174,194,202
#   s3 / dynamodb   backend remoto de estado (envs/*/versions.tf:13) — hoje
#                   concedido pela policy do bucket compartilhado do bootstrap
#                   amh-data-platform, nao por esta role.
#   budgets / ce    modules/cost-guardrails/main.tf, resources
#                   aws_ce_anomaly_monitor.service, aws_ce_anomaly_subscription.alerts
#                   e aws_budgets_budget.monthly (citados por simbolo, nao por linha —
#                   por §Delta D1, a linha se move a cada edicao deste cabecalho) —
#                   o 7o modulo, introduzido por ESTE MESMO branch (gap B-02-b /
#                   R-045). O refresh desses recursos exige `budgets:ViewBudget` e
#                   `ce:GetAnomaly*`/`ce:GetAnomalySubscriptions`. Nota: mesmo se
#                   um dia entrassem, `budgets:ViewBudget` NAO casa o formato
#                   `Describe*`/`Get*`/`List*` que R-041 aprovou para este bloco —
#                   entao a role `plan` nao pode ler orcamentos por este desenho,
#                   por construcao, ate o dono decidir alargar o formato. Vira
#                   plan vermelho a partir do 2o plan de cada raiz (o 1o, com
#                   estado vazio, nao faz refresh de recursos ja aplicados).
#
# `sts:GetCallerIdentity` nao aparece porque a AWS nao exige permissao para ela.
# Prefixos que hoje nao casam com nenhuma acao real do servico (p.ex. `rds:Get*`)
# nao concedem nada; ficam pelo formato uniforme que a decisao aprovou.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "plan_policy" {
  statement {
    sid    = "PlanReadRDS"
    effect = "Allow"
    actions = [
      "rds:Describe*",
      "rds:Get*",
      "rds:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "PlanReadECR"
    effect = "Allow"
    actions = [
      "ecr:Describe*",
      "ecr:Get*",
      "ecr:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "PlanReadEKS"
    effect = "Allow"
    actions = [
      "eks:Describe*",
      "eks:Get*",
      "eks:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "PlanReadAPS"
    effect = "Allow"
    actions = [
      "aps:Describe*",
      "aps:Get*",
      "aps:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "PlanReadIAM"
    effect = "Allow"
    actions = [
      "iam:Describe*",
      "iam:Get*",
      "iam:List*",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "PlanReadEC2VPC"
    effect = "Allow"
    actions = [
      "ec2:Describe*",
      "ec2:Get*",
      "ec2:List*",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "plan_least_privilege" {
  name        = "maezo-github-plan-${var.environment}"
  description = "Leitura de menor privilegio para `terraform plan` em PRs (gap D6-02 / R-041). Sem secretsmanager e sem kms:Decrypt."
  policy      = data.aws_iam_policy_document.plan_policy.json
  tags        = local.common_tags
}

resource "aws_iam_role_policy_attachment" "plan_least_privilege" {
  role       = aws_iam_role.plan.name
  policy_arn = aws_iam_policy.plan_least_privilege.arn
}

# ---------------------------------------------------------------------------
# Role: maezo-github-deploy (push to main + env-gated workflows)
# Scoped to ECR push, EKS describe/update, Secrets Manager read.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "deploy_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main",
        "repo:${var.github_org}/${var.github_repo}:environment:${var.environment}",
      ]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "maezo-github-deploy-${var.environment}"
  description          = "Assumed by Maezo GitHub Actions on main+dispatch for ECR push and EKS rollout."
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  max_session_duration = 7200
  tags                 = local.common_tags
}

data "aws_iam_policy_document" "deploy_policy" {
  # ECR: push images
  statement {
    sid       = "ECRAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "ECRPush"
    effect = "Allow"
    actions = [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = var.ecr_repository_arns
  }

  # EKS: describe cluster + manage rollout (kubectl uses IRSA, not this role)
  statement {
    sid    = "EKSDescribe"
    effect = "Allow"
    actions = [
      "eks:DescribeCluster",
      "eks:ListClusters",
    ]
    resources = ["*"]
  }

  # Secrets Manager: read non-PHI deployment secrets
  statement {
    sid    = "SecretsRead"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = var.secrets_manager_arns
  }
}

resource "aws_iam_role_policy" "deploy_inline" {
  name   = "maezo-deploy-permissions"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_policy.json
}
