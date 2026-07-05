output "repository_url" {
  description = "Full ECR repository URL (use in Helm values as image.repository)."
  value       = aws_ecr_repository.this.repository_url
}

output "repository_arn" {
  description = "ECR repository ARN."
  value       = aws_ecr_repository.this.arn
}

output "registry_id" {
  description = "ECR registry ID (AWS account ID)."
  value       = aws_ecr_repository.this.registry_id
}
