# Module: eks-cluster
# Provision an EKS cluster for Maezo tenant namespaces OR reference an existing
# cluster managed by amh-data-platform via data sources.
#
# Design decision: var.create_cluster=false (default) references the shared EKS
# cluster from amh-data-platform. Set to true to provision a dedicated cluster
# (expected for isolated prod tenants per ADR-0004).
#
# Per binding AWS direction: region sa-east-1, EKS for tenant namespaces,
# per-tenant KMS CMK pattern (mirrors amh-data-platform kms-tenant module).

locals {
  cluster_name = var.create_cluster ? "${var.name_prefix}-eks-${var.environment}" : var.existing_cluster_name
  common_tags = merge(var.tags, {
    Module      = "eks-cluster"
    ManagedBy   = "terraform"
    Platform    = "maezo"
    environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# OPTION A — reference existing cluster (default; amh-data-platform owns it)
# ---------------------------------------------------------------------------
data "aws_eks_cluster" "existing" {
  count = var.create_cluster ? 0 : 1
  name  = var.existing_cluster_name
}

data "aws_eks_cluster_auth" "existing" {
  count = var.create_cluster ? 0 : 1
  name  = var.existing_cluster_name
}

# ---------------------------------------------------------------------------
# OPTION B — dedicated cluster (create_cluster=true)
# KMS CMK for secrets-at-rest — mirrors amh-data-platform kms-tenant pattern.
# ---------------------------------------------------------------------------
resource "aws_kms_key" "eks" {
  count                   = var.create_cluster ? 1 : 0
  description             = "CMK for EKS cluster ${local.cluster_name} secrets encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = merge(local.common_tags, { Name = "${local.cluster_name}-cmk" })
}

resource "aws_kms_alias" "eks" {
  count         = var.create_cluster ? 1 : 0
  name          = "alias/maezo/${var.environment}/eks"
  target_key_id = aws_kms_key.eks[0].id
}

resource "aws_eks_cluster" "this" {
  count    = var.create_cluster ? 1 : 0
  name     = local.cluster_name
  role_arn = aws_iam_role.eks_cluster[0].arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = var.subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = var.endpoint_public_access
    public_access_cidrs     = var.endpoint_public_access ? var.public_access_cidrs : []
  }

  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = aws_kms_key.eks[0].arn
    }
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  tags = merge(local.common_tags, { Name = local.cluster_name })

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
    aws_iam_role_policy_attachment.eks_vpc_policy,
  ]
}

# ---------------------------------------------------------------------------
# IAM role for the EKS control plane
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "eks_assume" {
  count = var.create_cluster ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eks_cluster" {
  count              = var.create_cluster ? 1 : 0
  name               = "${local.cluster_name}-eks-role"
  assume_role_policy = data.aws_iam_policy_document.eks_assume[0].json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  count      = var.create_cluster ? 1 : 0
  role       = aws_iam_role.eks_cluster[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_iam_role_policy_attachment" "eks_vpc_policy" {
  count      = var.create_cluster ? 1 : 0
  role       = aws_iam_role.eks_cluster[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSVPCResourceController"
}

# ---------------------------------------------------------------------------
# EKS Managed Node Group
# ---------------------------------------------------------------------------
resource "aws_eks_node_group" "agents" {
  count           = var.create_cluster ? 1 : 0
  cluster_name    = aws_eks_cluster.this[0].name
  node_group_name = "${local.cluster_name}-agents"
  node_role_arn   = aws_iam_role.node_group[0].arn
  subnet_ids      = var.private_subnet_ids

  scaling_config {
    desired_size = var.node_desired_size
    max_size     = var.node_max_size
    min_size     = var.node_min_size
  }

  instance_types = var.node_instance_types
  capacity_type  = var.node_capacity_type

  labels = {
    workload    = "maezo-agents"
    environment = var.environment
  }

  tags = merge(local.common_tags, { Name = "${local.cluster_name}-agents" })

  depends_on = [
    aws_iam_role_policy_attachment.node_worker_policy,
    aws_iam_role_policy_attachment.node_ecr_policy,
    aws_iam_role_policy_attachment.node_cni_policy,
  ]
}

resource "aws_iam_role" "node_group" {
  count = var.create_cluster ? 1 : 0
  name  = "${local.cluster_name}-node-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "node_worker_policy" {
  count      = var.create_cluster ? 1 : 0
  role       = aws_iam_role.node_group[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_ecr_policy" {
  count      = var.create_cluster ? 1 : 0
  role       = aws_iam_role.node_group[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy_attachment" "node_cni_policy" {
  count      = var.create_cluster ? 1 : 0
  role       = aws_iam_role.node_group[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

# ---------------------------------------------------------------------------
# IRSA: IAM OIDC provider for the cluster (enables pod-level IAM via IRSA)
# ---------------------------------------------------------------------------
data "tls_certificate" "eks_oidc" {
  count = var.create_cluster ? 1 : 0
  url   = aws_eks_cluster.this[0].identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  count           = var.create_cluster ? 1 : 0
  url             = aws_eks_cluster.this[0].identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks_oidc[0].certificates[0].sha1_fingerprint]
  tags            = local.common_tags
}
