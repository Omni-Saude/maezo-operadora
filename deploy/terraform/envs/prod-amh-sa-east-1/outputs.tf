output "eks_cluster_name" {
  value = module.eks.cluster_name
}

output "aurora_writer_endpoint" {
  value = module.aurora.cluster_endpoint
}

output "aurora_reader_endpoint" {
  value = module.aurora.reader_endpoint
}

output "aurora_master_user_secret_arn" {
  value = module.aurora.master_user_secret_arn
}

# The aurora_* below feed the Helm ExternalSecret that COMPOSES database_url at ESO sync
# (predeploy DB-4). Map them into deploy/helm/maezo-tenant/values-amh.yaml `aurora.*` before
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
  value = module.ecr.repository_url
}

output "github_deploy_role_arn" {
  value = module.github_oidc.deploy_role_arn
}
