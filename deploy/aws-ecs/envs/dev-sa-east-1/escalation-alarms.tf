# Alarmes do escalonamento humano (SP-OP-ESCALATION-001) e da retomada (agent_resume).
#
# O QUE CONTAM: fatos estruturados que o codigo emite com tokens FECHADOS (sem chave de negocio,
# sem texto de excecao). Os nomes sao constantes do codigo, e o teste
# `tests/unit/deploy/test_escalation_alarms.py` IMPORTA essas constantes e confere que cada
# padrao abaixo casa com a linha que o renderizador de log produz:
#   * `agent_resume.falha`             (agent_resume.EVENTO_RESUME_FALHA): retomada nao concluida,
#     offset nao confirmado; `motivo` em {motor_indisponivel, envio_falhou, aviso_equipe_falhou, outro}.
#   * `agent_resume.fila_morta`        (agent_resume.EVENTO_RESUME_FILA_MORTA): evento desviado
#     para `agents.events.process_completed.dlq` (`reason` = codigo de BRIDGE_DLQ_REASONS).
#   * `escalation.aviso_falhou`        (escalation.EVENTO_AVISO_FALHOU): o aviso a equipe ou ao
#     supervisor nao foi publicado (o BPMN segue pelo fallback modelado ERR_ESC_NOTIFY_FAILED).
#   * `escalation.supervisor_alertado` com `motivo_alerta=sla_ack_breached`
#     (escalation.EVENTO_SUPERVISOR_ALERTADO / MOTIVO_ALERTA_SLA_ACK): prazo de ciencia estourado.
#   * `escalation.sla_resolucao_estourada` (events.EVENTO_SLA_RESOLUCAO_ESTOURADA): prazo de
#     RESOLUCAO estourado (`BT_SlaResolucao` -> `ST_PublishResolBreach` -> `UT_SupervisorAssume`;
#     esse caminho nunca passa por `notify_supervisor`).
#
# DESLIGADO: `escalation_alarms_enabled` default false. Com false NENHUM recurso deste arquivo
# existe e o plan do ambiente nao muda. Os alarmes da retomada so existem se o servico
# agent-resume tambem existir (o log group dele e condicional, `service-agent-resume.tf`).
#
# DESTINO: o topico de alertas do staff (`aws_sns_topic.staff_alerts`, `task-staff-ops.tf`) quando
# ele existe; sem ele o alarme fica so no console. Quem recebe e decisao do dono.
#
# Sem dado = sem evento = nada aconteceu (`notBreaching`): estes contadores so sobem quando algo
# deu errado (ou, no caso do supervisor, quando um prazo estourou).

variable "escalation_alarms_enabled" {
  description = "Liga os alarmes de escalonamento/retomada (escalation-alarms.tf). DEFAULT false: nenhum recurso existe. Destino: topico staff_alerts."
  type        = bool
  default     = false
}

locals {
  escalation_alarms_namespace = "maezo-operadora/dev/escalation"
  escalation_alarms_ativo     = var.escalation_alarms_enabled ? 1 : 0
  # A retomada so tem log group quando o servico existe.
  resume_alarms_ativo  = var.escalation_alarms_enabled && local.agent_resume_ativo == 1 ? 1 : 0
  escalation_alarm_sns = [for topic in aws_sns_topic.staff_alerts : topic.arn]
}

# ------------------------------------------------------------------ retomada: falha (offset parado)
resource "aws_cloudwatch_log_metric_filter" "agent_resume_falha" {
  count          = local.resume_alarms_ativo
  name           = "${local.name}-agent-resume-falha"
  log_group_name = aws_cloudwatch_log_group.agent_resume[0].name
  pattern        = "\"agent_resume.falha\""
  metric_transformation {
    name      = "AgentResumeFalha"
    namespace = local.escalation_alarms_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "agent_resume_falha" {
  count               = local.resume_alarms_ativo
  alarm_name          = "${local.name}-agent-resume-falha"
  alarm_description   = "Retomada pos-humano falhou (motor, envio, aviso a equipe ou outro) e o evento volta para reentrega; a pessoa ainda nao recebeu a instrucao. Runbook: docs/runbooks/cloudwatch-alarms/EscalationAgentResumeFalha.md"
  namespace           = local.escalation_alarms_namespace
  metric_name         = "AgentResumeFalha"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.escalation_alarm_sns
  tags                = local.base_tags
}

# ------------------------------------------------------------------ retomada: fila morta
resource "aws_cloudwatch_log_metric_filter" "agent_resume_fila_morta" {
  count          = local.resume_alarms_ativo
  name           = "${local.name}-agent-resume-fila-morta"
  log_group_name = aws_cloudwatch_log_group.agent_resume[0].name
  pattern        = "\"agent_resume.fila_morta\""
  metric_transformation {
    name      = "AgentResumeFilaMorta"
    namespace = local.escalation_alarms_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "agent_resume_fila_morta" {
  count               = local.resume_alarms_ativo
  alarm_name          = "${local.name}-agent-resume-fila-morta"
  alarm_description   = "Evento de retomada desviado para agents.events.process_completed.dlq (malformado, sem lastro no historico ou sem destinatario). Runbook: docs/runbooks/cloudwatch-alarms/EscalationAgentResumeFilaMorta.md"
  namespace           = local.escalation_alarms_namespace
  metric_name         = "AgentResumeFilaMorta"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.escalation_alarm_sns
  tags                = local.base_tags
}

# ------------------------------------------------------------------ escalonamento: aviso nao publicado
resource "aws_cloudwatch_log_metric_filter" "escalation_aviso_falhou" {
  count          = local.escalation_alarms_ativo
  name           = "${local.name}-escalation-aviso-falhou"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "\"escalation.aviso_falhou\""
  metric_transformation {
    name      = "EscalationAvisoFalhou"
    namespace = local.escalation_alarms_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "escalation_aviso_falhou" {
  count               = local.escalation_alarms_ativo
  alarm_name          = "${local.name}-escalation-aviso-falhou"
  alarm_description   = "O aviso de escalonamento (equipe ou supervisor) nao foi publicado no topico interno; o processo seguiu pelo fallback ERR_ESC_NOTIFY_FAILED. Runbook: docs/runbooks/cloudwatch-alarms/EscalationAvisoFalhou.md"
  namespace           = local.escalation_alarms_namespace
  metric_name         = "EscalationAvisoFalhou"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.escalation_alarm_sns
  tags                = local.base_tags
}

# ------------------------------------------------------------------ escalonamento: prazo de ciencia estourado
resource "aws_cloudwatch_log_metric_filter" "escalation_supervisor_alertado" {
  count          = local.escalation_alarms_ativo
  name           = "${local.name}-escalation-supervisor-alertado-prazo"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "\"escalation.supervisor_alertado\" \"motivo_alerta=sla_ack_breached\""
  metric_transformation {
    name      = "EscalationSupervisorAlertadoPrazo"
    namespace = local.escalation_alarms_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "escalation_supervisor_alertado" {
  count               = local.escalation_alarms_ativo
  alarm_name          = "${local.name}-escalation-prazo-estourado"
  alarm_description   = "Ao menos um caso de escalonamento estourou o prazo de ciencia na ultima hora e o supervisor foi alertado. Runbook: docs/runbooks/cloudwatch-alarms/EscalationSupervisorAlertado.md"
  namespace           = local.escalation_alarms_namespace
  metric_name         = "EscalationSupervisorAlertadoPrazo"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.escalation_alarm_sns
  ok_actions          = local.escalation_alarm_sns
  tags                = local.base_tags
}

# ------------------------------------------------------------------ escalonamento: prazo de resolucao estourado
resource "aws_cloudwatch_log_metric_filter" "escalation_sla_resolucao_estourada" {
  count          = local.escalation_alarms_ativo
  name           = "${local.name}-escalation-sla-resolucao-estourada"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "\"escalation.sla_resolucao_estourada\""
  metric_transformation {
    name      = "EscalationSlaResolucaoEstourada"
    namespace = local.escalation_alarms_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "escalation_sla_resolucao_estourada" {
  count               = local.escalation_alarms_ativo
  alarm_name          = "${local.name}-escalation-prazo-resolucao-estourado"
  alarm_description   = "Ao menos um caso de escalonamento estourou o prazo de RESOLUCAO na ultima hora: a tarefa foi interrompida e entregue a supervisao-atendimento (UT_SupervisorAssume). Runbook: docs/runbooks/cloudwatch-alarms/EscalationSlaResolucaoEstourada.md"
  namespace           = local.escalation_alarms_namespace
  metric_name         = "EscalationSlaResolucaoEstourada"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.escalation_alarm_sns
  ok_actions          = local.escalation_alarm_sns
  tags                = local.base_tags
}
