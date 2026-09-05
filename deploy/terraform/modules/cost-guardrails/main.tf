# Modulo: cost-guardrails
# Orcamento e deteccao de anomalia de custo COMO CODIGO (gap B-02-b / decisao do
# dono R-045). Antes deste modulo, `grep -rn aws_budgets_budget deploy/` e
# `grep -rn cost_anomaly deploy/` retornavam ZERO: no dia em que `AWS_ENABLED`
# fosse ligado, o gasto comecaria sem teto e sem alarme.
#
# Duas metades com regimes DIFERENTES de proposito:
#
#   1. Anomalia (`aws_ce_anomaly_monitor` + `aws_ce_anomaly_subscription`):
#      INCONDICIONAIS. Nao ha `count`, nao ha `var.enabled`, nao dependem do flip
#      de `AWS_ENABLED` — instalar o detector e trivial enquanto nao ha gasto e
#      vira urgencia no dia do flip.
#   2. Orcamento (`aws_budgets_budget`): o `limit_amount` vem de
#      `var.monthly_budget_amount`, que NAO TEM DEFAULT. O `plan` falha fechado
#      ate financas informar o teto. Essa falha e a decisao, nao um bug.
#
# REGIAO: Budgets e Cost Explorer sao servicos globais ancorados em us-east-1.
# As raizes de `envs/**` operam em sa-east-1 e por isso passam um provider
# aliado (`aws.us_east_1`) no slot padrao deste modulo via `providers = {}`.
#
# Custo dos proprios guardrails: `aws_budgets_budget` e gratuito ate 2 budgets e
# o Cost Anomaly Detection nao tem custo — o guardrail nao e o gasto.

locals {
  common_tags = merge(var.tags, {
    Module    = "cost-guardrails"
    ManagedBy = "terraform"
    Platform  = "maezo"
    Component = "finops"
  })

  budget_name = "${var.name_prefix}-${var.environment}-monthly"
}

# ---------------------------------------------------------------------------
# Cost Anomaly Detection — incondicional (ver cabecalho)
# ---------------------------------------------------------------------------
resource "aws_ce_anomaly_monitor" "service" {
  name              = "${var.name_prefix}-${var.environment}-anomaly-monitor"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
  tags              = local.common_tags
}

resource "aws_ce_anomaly_subscription" "alerts" {
  name             = "${var.name_prefix}-${var.environment}-anomaly-subscription"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.service.arn]

  subscriber {
    type    = "EMAIL"
    address = var.notification_email
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.anomaly_impact_threshold)]
    }
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Orcamento mensal — teto vindo de variable SEM default (fail-closed)
# ---------------------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  name         = local.budget_name
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_amount = tostring(var.monthly_budget_amount)
  limit_unit   = var.budget_currency

  dynamic "cost_filter" {
    for_each = var.cost_filter_tag == null ? [] : [var.cost_filter_tag]
    content {
      name   = "TagKeyValue"
      values = [cost_filter.value]
    }
  }

  dynamic "notification" {
    for_each = var.budget_notifications
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold_percent
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.notification_type
      subscriber_email_addresses = [var.notification_email]
    }
  }
}
