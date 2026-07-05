output "eks_cluster_name" {
  description = "EKS cluster name (referenced)."
  value       = module.eks.cluster_name
}

output "aurora_writer_endpoint" {
  description = "Aurora writer endpoint."
  value       = module.aurora.cluster_endpoint
}

output "aurora_reader_endpoint" {
  description = "Aurora reader endpoint."
  value       = module.aurora.reader_endpoint
}

output "aurora_master_user_secret_arn" {
  description = "Secrets Manager ARN holding the Aurora master password."
  value       = module.aurora.master_user_secret_arn
}

# The aurora_* below feed the Helm ExternalSecret that COMPOSES database_url at ESO sync
# (predeploy DB-4). Map them into deploy/helm/maezo-tenant/values-staging.yaml `aurora.*` before
# `helm upgrade`: aurora_master_user_secret_arn->masterSecretArn, aurora_writer_endpoint->endpoint,
# aurora_port->port, aurora_database_name->database. See docs/Tarefas_Pendentes.md §1.1.
output "aurora_port" {
  description = "Aurora PostgreSQL port (for the composed ESO database_url — predeploy DB-4)."
  value       = module.aurora.port
}

output "aurora_database_name" {
  description = "Aurora default database name (for the composed ESO database_url — predeploy DB-4)."
  value       = module.aurora.database_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for maezo-agent image."
  value       = module.ecr.repository_url
}

output "github_deploy_role_arn" {
  description = "IAM role ARN for GitHub Actions deployments."
  value       = module.github_oidc.deploy_role_arn
}

output "github_plan_role_arn" {
  description = "IAM role ARN for GitHub Actions terraform plan."
  value       = module.github_oidc.plan_role_arn
}

output "secrets_whatsapp_arn" {
  description = "ARN of the WhatsApp secret shell (BLOCKED)."
  value       = module.secrets.whatsapp_waba_token_arn
}

output "secrets_whatsapp_app_secret_arn" {
  description = "ARN of the WhatsApp webhook Meta App Secret shell (BLOCKED; predeploy DB-2)."
  value       = module.secrets.whatsapp_app_secret_arn
}

output "secrets_whatsapp_verify_token_arn" {
  description = "ARN of the WhatsApp webhook verify token shell (BLOCKED; predeploy DB-2)."
  value       = module.secrets.whatsapp_verify_token_arn
}

output "secrets_phi_hmac_arn" {
  description = "ARN of the PHI pseudonymizer HMAC key shell (BLOCKED; ADR-0006 §6.2)."
  value       = module.secrets.phi_hmac_key_arn
}

output "secrets_llm_arn" {
  description = "ARN of the LLM API keys secret shell (BLOCKED)."
  value       = module.secrets.llm_api_keys_arn
}

output "secrets_tasy_arn" {
  description = "ARN of the Tasy Oracle credentials secret shell (BLOCKED)."
  value       = module.secrets.tasy_oracle_arn
}
