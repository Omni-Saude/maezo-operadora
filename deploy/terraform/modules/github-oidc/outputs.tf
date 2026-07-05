output "plan_role_arn" {
  description = "ARN of the terraform-plan role (assumed on PR events)."
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "ARN of the deploy role (assumed on push to main / env-gated workflows)."
  value       = aws_iam_role.deploy.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC provider used."
  value       = local.oidc_provider_arn
}
