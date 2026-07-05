output "cluster_name" {
  description = "EKS cluster name (created or referenced)."
  value       = var.create_cluster ? aws_eks_cluster.this[0].name : data.aws_eks_cluster.existing[0].name
}

output "cluster_endpoint" {
  description = "Kubernetes API server endpoint."
  value       = var.create_cluster ? aws_eks_cluster.this[0].endpoint : data.aws_eks_cluster.existing[0].endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded certificate authority data."
  value       = var.create_cluster ? aws_eks_cluster.this[0].certificate_authority[0].data : data.aws_eks_cluster.existing[0].certificate_authority[0].data
}

output "cluster_token" {
  description = "Authentication token for the cluster."
  value       = var.create_cluster ? null : data.aws_eks_cluster_auth.existing[0].token
  sensitive   = true
}

output "oidc_provider_arn" {
  description = "ARN of the IAM OIDC provider (for IRSA). Only set when create_cluster=true."
  value       = var.create_cluster ? aws_iam_openid_connect_provider.eks[0].arn : null
}

output "oidc_issuer_url" {
  description = "OIDC issuer URL (strip https:// for IRSA trust policies)."
  value       = var.create_cluster ? aws_eks_cluster.this[0].identity[0].oidc[0].issuer : data.aws_eks_cluster.existing[0].identity[0].oidc[0].issuer
}
