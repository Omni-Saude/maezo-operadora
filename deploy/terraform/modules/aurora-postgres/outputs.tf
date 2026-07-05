output "cluster_identifier" {
  description = "Aurora cluster identifier."
  value       = aws_rds_cluster.this.id
}

output "cluster_endpoint" {
  description = "Writer endpoint (use for write connections)."
  value       = aws_rds_cluster.this.endpoint
}

output "reader_endpoint" {
  description = "Reader endpoint (use for read-only connections)."
  value       = aws_rds_cluster.this.reader_endpoint
}

output "port" {
  description = "PostgreSQL port."
  value       = aws_rds_cluster.this.port
}

output "database_name" {
  description = "Default database name."
  value       = aws_rds_cluster.this.database_name
}

output "master_user_secret_arn" {
  description = "ARN of the Secrets Manager secret that holds the master password."
  value       = aws_rds_cluster.this.master_user_secret[0].secret_arn
}

output "security_group_id" {
  description = "Security group attached to Aurora."
  value       = aws_security_group.aurora.id
}

output "kms_key_arn" {
  description = "ARN of the KMS CMK used to encrypt storage."
  value       = aws_kms_key.aurora.arn
}
