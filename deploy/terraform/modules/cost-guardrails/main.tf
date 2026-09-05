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
#
# PREMISSA DE UMA CONTA POR RAIZ (nao verificada, achado do verificador VER-A3-TF
# F2): este modulo assume UMA conta AWS por raiz de `deploy/terraform/envs/`. Ele
# e' instanciado por staging-sa-east-1 E prod-amh-sa-east-1 com `cost_filter_tag`
# no default `null` (orcamento e monitor de escopo de CONTA INTEIRA, nao de tag) e
# com `aws_ce_anomaly_monitor.monitor_type = "DIMENSIONAL"` /
# `monitor_dimension = "SERVICE"`. Se as duas raizes apontarem para a MESMA conta
# AWS (os dois `terraform.tfvars.example` ainda trazem o mesmo placeholder de
# `aws_account_id`, e as duas raizes compartilham o bucket de state do bootstrap
# amh-data-platform — nada aqui prova contas separadas): (a) o teto de staging
# passa a alarmar sobre gasto de prod e vice-versa, porque os dois orcamentos
# medem a MESMA conta; e (b) a AWS pode permitir apenas UM monitor
# DIMENSIONAL/SERVICE por conta (nao confirmado — sem credencial, sem apply
# ainda: D13-03), caso em que o `apply` da segunda raiz falharia ao criar o seu
# proprio `aws_ce_anomaly_monitor`. Nao e' regressao de seguranca nem viola o
# `floor_note` de R-045 (nao envolve leitura de segredo); e' uma lacuna de
# desenho para o dono resolver ANTES do primeiro apply real, configurando
# `cost_filter_tag` por ambiente (exige a tag de alocacao de custo ativada em
# Billing — ver acima) ou instanciando o modulo uma unica vez para as duas raizes
# se elas de fato compartilharem conta. Nao alterado aqui: mudar o default
# seria o agente decidindo por materia de financas/contas, nao documentando.
#
# ESCOPO FORA DESTE MODULO (INFO, gap F3 / R-045): `deploy/terraform/envs/**` e'
# o unico escopo que a decisao aprovada nomeia. Este repositorio tambem contem
# `deploy/aws-ecs/envs/dev-sa-east-1` (a infraestrutura dev de ECS/Fargate, com
# `aws_rds_cluster`, `aws_lb`, `aws_codebuild_project` reais), `deploy/aws-identity-center`
# e `deploy/cloudflare/envs/dev` — nenhum ganha guardrail de custo aqui, e a raiz
# ECS/Fargate e' justamente a que mais provavelmente carrega gasto real hoje.
# Nao e' lacuna deste pacote (a resposta aprovada nao pede essas raizes), mas o
# dono deve decidir explicitamente se `cost-guardrails` deve ser estendido a elas.

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
