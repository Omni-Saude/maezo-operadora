output "whatsapp_waba_token_arn" {
  description = "ARN of the WhatsApp WABA token secret (BLOCKED — shell only)."
  value       = aws_secretsmanager_secret.whatsapp_waba_token.arn
}

output "whatsapp_app_secret_arn" {
  description = "ARN of the WhatsApp webhook Meta App Secret (BLOCKED — shell only; predeploy DB-2)."
  value       = aws_secretsmanager_secret.whatsapp_app_secret.arn
}

output "whatsapp_verify_token_arn" {
  description = "ARN of the WhatsApp webhook verify token secret (BLOCKED — shell only; predeploy DB-2)."
  value       = aws_secretsmanager_secret.whatsapp_verify_token.arn
}

output "phi_hmac_key_arn" {
  description = "ARN of the PHI pseudonymizer HMAC key secret (BLOCKED — shell only; ADR-0006 §6.2)."
  value       = aws_secretsmanager_secret.phi_hmac_key.arn
}

output "llm_api_keys_arn" {
  description = "ARN of the LLM API keys secret (BLOCKED — shell only)."
  value       = aws_secretsmanager_secret.llm_api_keys.arn
}

output "tasy_oracle_arn" {
  description = "ARN of the Tasy Oracle credentials secret (BLOCKED — shell only)."
  value       = aws_secretsmanager_secret.tasy_oracle.arn
}

output "msk_bootstrap_secret_arn" {
  description = "ARN of the amh-data-platform MSK bootstrap servers secret (referenced, not owned)."
  value       = data.aws_secretsmanager_secret.msk_bootstrap.arn
}

output "msk_bootstrap_servers" {
  description = "MSK bootstrap servers value (from amh-data-platform)."
  value       = data.aws_secretsmanager_secret_version.msk_bootstrap.secret_string
  sensitive   = true
}
