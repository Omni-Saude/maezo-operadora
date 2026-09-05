variable "name_prefix" {
  description = "Prefixo dos nomes dos recursos (ex.: 'maezo')."
  type        = string
  default     = "maezo"
}

variable "environment" {
  description = "Nome do ambiente (staging, prod-amh)."
  type        = string
}

# ---------------------------------------------------------------------------
# SEM DEFAULT DE PROPOSITO — nao acrescente um.
#
# Decisao do dono R-045 (gap B-02-b): o teto mensal e insumo de FINANCAS e foi
# deliberadamente diferido. A ausencia de default e o mecanismo: `terraform plan`
# falha FECHADO enquanto o dono nao informar o valor, em vez de nascer com um
# numero inventado por um agente. Um default aqui reabre o gap sem que nenhum
# alarme toque.
# ---------------------------------------------------------------------------
variable "monthly_budget_amount" {
  description = <<-EOT
    Teto mensal de custo, na moeda de `budget_currency`. SEM DEFAULT por decisao
    do dono (R-045): o `terraform plan` deve falhar fechado ate que financas
    informe o valor via tfvars.
  EOT
  type        = number

  validation {
    condition     = var.monthly_budget_amount > 0
    error_message = "monthly_budget_amount deve ser maior que zero."
  }
}

variable "budget_currency" {
  description = "Moeda do teto mensal (`limit_unit` do aws_budgets_budget)."
  type        = string
  default     = "USD"
}

# Tambem sem default: o alarme de anomalia so existe de fato se houver para quem
# avisar. Um endereco inventado seria um guardrail silenciosamente inerte.
variable "notification_email" {
  description = "E-mail que recebe os alertas de orcamento e de anomalia de custo. Sem default."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.notification_email))
    error_message = "notification_email deve ser um endereco de e-mail valido."
  }
}

variable "budget_notifications" {
  description = <<-EOT
    Disparos do orcamento. `notification_type` e ACTUAL ou FORECASTED;
    `threshold_percent` e o percentual do teto mensal a partir do qual o alerta
    dispara. Estes sao limiares de ALERTA (sensibilidade), nao o teto em si — por
    isso tem default, ao contrario de `monthly_budget_amount`.
  EOT
  type = list(object({
    notification_type = string
    threshold_percent = number
  }))
  default = [
    { notification_type = "ACTUAL", threshold_percent = 80 },
    { notification_type = "ACTUAL", threshold_percent = 100 },
    { notification_type = "FORECASTED", threshold_percent = 100 },
  ]

  validation {
    condition = alltrue([
      for n in var.budget_notifications :
      contains(["ACTUAL", "FORECASTED"], n.notification_type) && n.threshold_percent > 0
    ])
    error_message = "Cada notificacao precisa de notification_type ACTUAL|FORECASTED e threshold_percent > 0."
  }
}

variable "anomaly_impact_threshold" {
  description = <<-EOT
    Impacto absoluto minimo (mesma moeda do orcamento) para que o Cost Anomaly
    Detection notifique. Limiar de sensibilidade do detector, nao teto de gasto;
    por isso tem default.
  EOT
  type        = number
  default     = 100

  validation {
    condition     = var.anomaly_impact_threshold > 0
    error_message = "anomaly_impact_threshold deve ser maior que zero."
  }
}

variable "cost_filter_tag" {
  description = <<-EOT
    Filtro `TagKeyValue` opcional do orcamento, no formato `user:Chave$Valor`
    (ex.: `user:Platform$maezo`). O DEFAULT E `null` — orcamento da conta
    inteira — de proposito: um filtro por tag so funciona depois que a tag de
    alocacao de custo e ATIVADA no Billing, e um filtro nao ativado casa zero
    linhas, ou seja, o orcamento nunca dispararia sem nenhum erro visivel.
    Estreite so depois de ativar a tag.
  EOT
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags adicionais."
  type        = map(string)
  default     = {}
}
