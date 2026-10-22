# PROTECOES E ALARME das chamadas a zona PHI (Helena e Lucas), decisao de 22/10/2026 depois das duas paradas dos
# modelos grandes regionais (GLM-5 em 21/10, Mistral Large 3 em 21-22/10): as chamadas ficavam penduradas sem erro
# nem metrica no AWS/Bedrock (a chamada que nao termina nao conta).
#
# PROTECOES (variaveis abaixo, nas task definitions do receptor, dos agentes e da retomada):
#   * prazo REAL de cada chamada = tempo de leitura do cliente boto (`MAEZO_INFERENCE_TIMEOUT_S`): a thread volta no
#     prazo; nada fica pendurado;
#   * teto de tokens de SAIDA por chamada (`MAEZO_INFERENCE_MAX_TOKENS`);
#   * pool PROPRIO de threads para as chamadas (`MAEZO_INFERENCE_DEDICATED_WORKERS`).
#
# CONTADOR: o transporte regional emite uma linha `inferencia_chamada` por chamada (modelo, resultado ok|falha|prazo,
# ms, tokens). ALARME: falha + prazo por MODELO (o da zona PHI e o do roteador) >= `inferencia_alarme_limiar` em 5 min,
# no log do receptor. Destino: o mesmo dos alarmes existentes (`aws_sns_topic.staff_alerts`).

variable "inferencia_prazo_s" {
  description = "Prazo REAL (segundos) de cada chamada a zona PHI: `MAEZO_INFERENCE_TIMEOUT_S`."
  type        = number
  default     = 20
  validation {
    condition     = var.inferencia_prazo_s >= 3 && var.inferencia_prazo_s <= 60
    error_message = "inferencia_prazo_s entre 3 e 60."
  }
}

variable "inferencia_max_tokens" {
  description = "Teto de tokens de SAIDA por chamada a zona PHI: `MAEZO_INFERENCE_MAX_TOKENS`."
  type        = number
  default     = 1024
  validation {
    condition     = var.inferencia_max_tokens >= 64 && var.inferencia_max_tokens <= 4096
    error_message = "inferencia_max_tokens entre 64 e 4096."
  }
}

variable "inferencia_workers" {
  description = "Threads PROPRIAS das chamadas a zona PHI: `MAEZO_INFERENCE_DEDICATED_WORKERS`."
  type        = number
  default     = 16
  validation {
    condition     = var.inferencia_workers >= 1 && var.inferencia_workers <= 64
    error_message = "inferencia_workers entre 1 e 64."
  }
}

variable "inferencia_alarme_limiar" {
  description = "Falhas + prazos estourados por modelo em 5 min que disparam o alarme."
  type        = number
  default     = 5
}

locals {
  inferencia_namespace = "maezo-operadora/dev/inferencia"
  inferencia_modelos   = toset(compact([var.phi_model_id, var.helena_roteador_modelo]))
}

resource "aws_cloudwatch_log_metric_filter" "inferencia_falhas" {
  for_each       = local.inferencia_modelos
  name           = "${local.name}-inferencia-falhas-${replace(each.value, ":", "_")}"
  log_group_name = aws_cloudwatch_log_group.webhook_receiver.name
  pattern        = "\"inferencia_chamada\" \"modelo=${each.value}\" -\"resultado=ok\""
  metric_transformation {
    name      = "InferenciaFalhas_${replace(each.value, ":", "_")}"
    namespace = local.inferencia_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "inferencia_falhas" {
  for_each            = local.inferencia_modelos
  alarm_name          = "${local.name}-inferencia-falhas-${replace(each.value, ":", "_")}"
  alarm_description   = "Chamadas ao modelo ${each.value} (zona PHI regional) com falha ou prazo estourado em 5 min. A Helena/Lucas do dev pode estar sem modelo. Runbook: docs/runbooks/phi-inference-ops.md §7."
  namespace           = local.inferencia_namespace
  metric_name         = "InferenciaFalhas_${replace(each.value, ":", "_")}"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.inferencia_alarme_limiar
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [for topic in aws_sns_topic.staff_alerts : topic.arn]
  ok_actions          = [for topic in aws_sns_topic.staff_alerts : topic.arn]
  tags                = local.base_tags
}
