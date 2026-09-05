# Environment: staging-sa-east-1
# Maezo staging environment.
# Region: sa-east-1 (São Paulo — LGPD data residency).
#
# EKS: references the existing amh-data-platform cluster (create_cluster=false).
# Aurora: serverless v2 (scale-to-zero; cost-friendly for staging).
# MSK: referenced from amh-data-platform via Secrets Manager data source.

locals {
  env         = "staging"
  region      = var.aws_region
  name_prefix = "maezo"
  base_tags = {
    Platform    = "maezo"
    ManagedBy   = "terraform"
    Repo        = "Maezo-Healthcare-Plan"
    environment = local.env
    owner       = var.owner_email
    cost_center = var.cost_center
  }
}

# ---------------------------------------------------------------------------
# Existing VPC + subnets (from amh-data-platform — reference via data sources)
# ---------------------------------------------------------------------------
data "aws_vpc" "shared" {
  tags = { Name = var.shared_vpc_name }
}

data "aws_subnets" "private_app" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.shared.id]
  }
  tags = { Tier = "private-app" }
}

data "aws_subnets" "private_data" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.shared.id]
  }
  tags = { Tier = "private-data" }
}

# ---------------------------------------------------------------------------
# EKS — reference existing amh-data-platform cluster
# ---------------------------------------------------------------------------
module "eks" {
  source = "../../modules/eks-cluster"

  create_cluster        = false
  existing_cluster_name = var.eks_cluster_name
  name_prefix           = local.name_prefix
  environment           = local.env
  tags                  = local.base_tags
}

# ---------------------------------------------------------------------------
# Aurora PostgreSQL 16 — agent state, memory, audit + pgvector
# ---------------------------------------------------------------------------
module "aurora" {
  source = "../../modules/aurora-postgres"

  name_prefix   = local.name_prefix
  environment   = local.env
  vpc_id        = data.aws_vpc.shared.id
  db_subnet_ids = data.aws_subnets.private_data.ids

  # EKS nodes reach Aurora; SG ID resolved from existing cluster node group.
  allowed_security_group_ids = var.eks_node_security_group_ids

  database_name              = "maezo"
  master_username            = "maezo"
  backup_retention_days      = 3
  enable_deletion_protection = false # staging: allow destroy
  serverless                 = true
  serverless_min_acu         = 0.5
  serverless_max_acu         = 4.0
  reader_count               = 0 # staging: writer only

  tags = local.base_tags
}

# ---------------------------------------------------------------------------
# ECR — maezo-agent image registry
# ---------------------------------------------------------------------------
module "ecr" {
  source = "../../modules/ecr"

  repository_name      = "maezo-agent"
  image_tag_mutability = "MUTABLE" # staging: allow overwrite for iteration
  push_role_arns       = [module.github_oidc.deploy_role_arn]
  pull_role_arns       = var.eks_node_role_arns

  tags = local.base_tags
}

# ---------------------------------------------------------------------------
# GitHub OIDC roles
# ---------------------------------------------------------------------------
module "github_oidc" {
  source = "../../modules/github-oidc"

  github_org           = var.github_org
  github_repo          = var.github_repo
  environment          = local.env
  create_oidc_provider = false # amh-data-platform bootstrap already created it

  ecr_repository_arns  = [module.ecr.repository_arn]
  secrets_manager_arns = ["arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:maezo/${local.env}/*"]

  tags = local.base_tags
}

# ---------------------------------------------------------------------------
# Secrets shells (BLOCKED items + MSK reference)
# ---------------------------------------------------------------------------
resource "aws_kms_key" "secrets" {
  description             = "CMK for Maezo ${local.env} Secrets Manager"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  tags                    = merge(local.base_tags, { Name = "maezo-${local.env}-secrets-cmk" })
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/maezo/${local.env}/secrets"
  target_key_id = aws_kms_key.secrets.id
}

module "secrets" {
  source = "../../modules/secrets"

  name_prefix = local.name_prefix
  environment = local.env
  kms_key_arn = aws_kms_key.secrets.arn

  tags = local.base_tags
}

# ---------------------------------------------------------------------------
# Guardrails de custo — orcamento + deteccao de anomalia (gap B-02-b / R-045)
# ---------------------------------------------------------------------------
module "cost_guardrails" {
  source = "../../modules/cost-guardrails"

  providers = {
    aws = aws.us_east_1
  }

  name_prefix           = local.name_prefix
  environment           = local.env
  monthly_budget_amount = var.monthly_budget_amount
  notification_email    = var.cost_alert_email

  tags = local.base_tags
}
