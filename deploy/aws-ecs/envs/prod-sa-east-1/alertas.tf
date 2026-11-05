# Topico de alertas operacionais de PROD e a trust do EventBridge Scheduler.
#
# No dev estes dois objetos moravam em `task-staff-ops.tf` (plano staff), que NAO foi portado.
# Os alarmes de escalonamento (`escalation-alarms.tf`) e de inferencia (`inferencia-alarmes.tf`)
# continuam precisando de um destino, entao o topico existe aqui por conta propria. O nome do
# recurso (`staff_alerts`) e' o do dev para os arquivos copiados nao divergirem.
#
# Topico CIFRADO com a chave gerida do SNS (`alias/aws/sns`): o alarme nao carrega PHI, mas o
# texto do alarme cita servico/modelo, e topico sem cifra reprova o portao de IaC.

resource "aws_sns_topic" "staff_alerts" {
  for_each          = toset(["alertas"])
  name              = "${local.name}-alertas"
  kms_master_key_id = "alias/aws/sns"
  tags              = local.base_tags
}

variable "staff_alerts_email" {
  description = "E-mail que assina o topico de alertas de prod (o destinatario confirma a assinatura). Vazio = sem assinante; decidir QUEM recebe e' passo do cutover."
  type        = string
  default     = ""
  validation {
    condition     = var.staff_alerts_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.staff_alerts_email))
    error_message = "staff_alerts_email: vazio ou um endereco de e-mail."
  }
}

resource "aws_sns_topic_subscription" "staff_alerts_email" {
  for_each  = var.staff_alerts_email == "" ? {} : aws_sns_topic.staff_alerts
  topic_arn = each.value.arn
  protocol  = "email"
  endpoint  = var.staff_alerts_email
}

check "alertas_sem_assinante_com_servicos_ligados" {
  assert {
    condition     = !var.servicos_habilitados || var.staff_alerts_email != ""
    error_message = "servicos_habilitados = true com staff_alerts_email vazio: os alarmes de prod nao chegam a ninguem."
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}
