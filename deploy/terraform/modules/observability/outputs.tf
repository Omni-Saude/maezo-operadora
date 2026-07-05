output "amp_workspace_id" {
  description = "Amazon Managed Prometheus workspace ID."
  value       = aws_prometheus_workspace.maezo.id
}

output "amp_workspace_arn" {
  description = "Amazon Managed Prometheus workspace ARN."
  value       = aws_prometheus_workspace.maezo.arn
}

output "amp_prometheus_endpoint" {
  description = "AMP Prometheus query endpoint (for AMG datasource and Thanos querier)."
  value       = aws_prometheus_workspace.maezo.prometheus_endpoint
}

output "amp_remote_write_url" {
  description = "AMP remote-write URL for the OTel collector (set PROMETHEUS_REMOTE_WRITE_ENDPOINT to this value)."
  value       = "${aws_prometheus_workspace.maezo.prometheus_endpoint}api/v1/remote_write"
}

output "collector_irsa_role_arn" {
  description = "IAM role ARN for the OTel collector IRSA (SigV4 for AMP). Set in Helm values: observability.collector.irsaRoleArn"
  value       = aws_iam_role.collector_irsa.arn
}

output "amg_workspace_id" {
  description = "Amazon Managed Grafana workspace ID."
  value       = aws_grafana_workspace.maezo.id
}

output "amg_workspace_url" {
  description = "Amazon Managed Grafana workspace URL (https endpoint for browser access)."
  value       = "https://${aws_grafana_workspace.maezo.endpoint}"
}

output "amp_endpoint_secret_arn" {
  description = "Secrets Manager secret ARN storing the AMP remote-write endpoint URL (ESO ExternalSecret source)."
  value       = aws_secretsmanager_secret.amp_endpoint.arn
}
