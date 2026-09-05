# Environment: prod-amh-sa-east-1
# Maezo production environment for tenant AMH.
# Region: sa-east-1 (São Paulo — LGPD/LGPD data residency; PHI zone).
#
# Key differences from staging:
# - EKS: dedicated cluster option (create_cluster=true) per ADR-0004 isolation
# - Aurora: provisioned instances (not serverless), deletion protection ON
# - Aurora reader replica enabled for read-heavy workloads (memory queries)
# - Image tags: IMMUTABLE (provenance guarantee)

locals {
  env         = "prod-amh"
  region      = var.aws_region
  name_prefix = "maezo"
  base_tags = {
    Platform    = "maezo"
    ManagedBy   = "terraform"
    Repo        = "Maezo-Healthcare-Plan"
    environment = local.env
    tenant      = "amh"
    owner       = var.owner_email
    cost_center = var.cost_center
    DataClass   = "PHI"
  }
}

# ---------------------------------------------------------------------------
# VPC — existing amh-data-platform VPC (prod tier)
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
# EKS — dedicated cluster per ADR-0004 (var.create_eks_cluster toggleable)
# ---------------------------------------------------------------------------
module "eks" {
  source = "../../modules/eks-cluster"

  create_cluster        = var.create_eks_cluster
  existing_cluster_name = var.eks_cluster_name
  name_prefix           = local.name_prefix
  environment           = local.env

  # Used only when create_cluster=true
  subnet_ids         = concat(data.aws_subnets.private_app.ids, data.aws_subnets.private_data.ids)
  private_subnet_ids = data.aws_subnets.private_app.ids

  endpoint_public_access = false # prod: private endpoint only
  node_desired_size      = 3
  node_min_size          = 2
  node_max_size          = 10
  node_instance_types    = ["m6g.xlarge", "m6g.2xlarge"]
  node_capacity_type     = "ON_DEMAND"

  tags = local.base_tags
}

# ---------------------------------------------------------------------------
# Aurora PostgreSQL 16 — provisioned, deletion-protected
# ---------------------------------------------------------------------------
module "aurora" {
  source = "../../modules/aurora-postgres"

  name_prefix   = local.name_prefix
  environment   = local.env
  vpc_id        = data.aws_vpc.shared.id
  db_subnet_ids = data.aws_subnets.private_data.ids

  allowed_security_group_ids = var.eks_node_security_group_ids

  database_name              = "maezo"
  master_username            = "maezo"
  backup_retention_days      = 14
  enable_deletion_protection = true
  serverless                 = false
  instance_class             = "db.r6g.xlarge"
  reader_count               = 1

  tags = local.base_tags
}

# ---------------------------------------------------------------------------
# ECR — immutable tags for prod provenance
# ---------------------------------------------------------------------------
module "ecr" {
  source = "../../modules/ecr"

  repository_name      = "maezo-agent"
  image_tag_mutability = "IMMUTABLE"
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
  create_oidc_provider = false # shared OIDC provider from amh-data-platform bootstrap

  ecr_repository_arns  = [module.ecr.repository_arn]
  secrets_manager_arns = ["arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:maezo/${local.env}/*"]

  tags = local.base_tags
}

# ---------------------------------------------------------------------------
# Secrets shells
# ---------------------------------------------------------------------------
resource "aws_kms_key" "secrets" {
  description             = "CMK for Maezo ${local.env} Secrets Manager"
  deletion_window_in_days = 30
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
