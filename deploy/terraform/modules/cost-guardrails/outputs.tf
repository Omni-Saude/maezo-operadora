output "budget_name" {
  description = "Nome do orcamento mensal criado."
  value       = aws_budgets_budget.monthly.name
}

output "budget_arn" {
  description = "ARN do orcamento mensal."
  value       = aws_budgets_budget.monthly.arn
}

output "anomaly_monitor_arn" {
  description = "ARN do monitor de anomalia de custo."
  value       = aws_ce_anomaly_monitor.service.arn
}

output "anomaly_subscription_arn" {
  description = "ARN da assinatura de alertas de anomalia."
  value       = aws_ce_anomaly_subscription.alerts.arn
}
