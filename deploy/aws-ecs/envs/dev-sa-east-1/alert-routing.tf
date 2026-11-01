# Roteamento dos alertas do Maezo no AMP (R-085, decisao do dono 2026-10-31, issue #1084, Rodada 2
# "Observabilidade"): critical -> page, warning -> ticket.
#
# O QUE FALTAVA: `service-metrics-collector.tf` carrega `alert-rules.yml` no workspace AMP, mas
# nenhum workspace tinha definicao de Alertmanager — `severity` era inerte, nada paginava nem abria
# ticket. Este arquivo instala a definicao a partir da fonte unica
# `deploy/observability/alertmanager-amp.yml.tftpl` (ADR-0014: mesmo artefato do modulo
# observability de staging/prod).
#
# DESLIGADO: `amp_alert_routing_enabled` default false. Com false NENHUM recurso deste arquivo existe
# e o plan nao muda.
#
# ANTES DE LIGAR (NEEDS-HUMAN, nao e detalhe): o workspace `amh-prometheus-dev` e DA PLATAFORMA,
# compartilhado com outros times (ver o cabecalho de `service-metrics-collector.tf`). A definicao de
# Alertmanager do AMP e UMA POR WORKSPACE: instala-la aqui passa a rotear TAMBEM os alertas dos
# outros times pelos topicos do Maezo (ou falha no apply se outro time ja tiver uma definicao).
# Ligar exige o aceite do time de plataforma — ou um workspace proprio do Maezo.
#
# QUEM RECEBE: os dois topicos SNS nascem sem assinante. Assinar (pager no `page`; e-mail/ferramenta
# de chamados no `ticket`) e decisao do dono, fora deste arquivo. Sem assinante o alerta chega ao
# topico e para ali — visivel no console do SNS, nunca silenciosamente descartado pelo AMP.

variable "amp_alert_routing_enabled" {
  description = "Liga o roteamento de alertas do AMP (alert-routing.tf): topicos SNS page/ticket + definicao de Alertmanager. DEFAULT false. Workspace compartilhado: exige aceite do time de plataforma."
  type        = bool
  default     = false
}

locals {
  amp_alert_routing_ativo = var.amp_alert_routing_enabled && local._amp_encontrado ? 1 : 0
  amp_workspace_arn       = "arn:aws:aps:${var.aws_region}:${var.aws_account_id}:workspace/${local.amp_workspace_id}"
}

# `page` e `ticket` sao os DOIS receptores fechados de alertmanager-amp.yml.tftpl.
resource "aws_sns_topic" "alertas" {
  for_each = local.amp_alert_routing_ativo == 1 ? toset(["page", "ticket"]) : toset([])
  name     = "${local.name}-alertas-${each.key}"
  tags     = local.base_tags
}

# O AMP publica nos topicos como o servico `aps.amazonaws.com`, SO a partir DESTE workspace e DESTA
# conta (confused-deputy: `aws:SourceArn` + `aws:SourceAccount`). Uma politica por topico.
data "aws_iam_policy_document" "alertas_amp_publish" {
  for_each = aws_sns_topic.alertas

  statement {
    sid     = "AMPAlertmanagerPublish"
    effect  = "Allow"
    actions = ["sns:Publish", "sns:GetTopicAttributes"]
    principals {
      type        = "Service"
      identifiers = ["aps.amazonaws.com"]
    }
    resources = [each.value.arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [local.amp_workspace_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alertas" {
  for_each = aws_sns_topic.alertas
  arn      = each.value.arn
  policy   = data.aws_iam_policy_document.alertas_amp_publish[each.key].json
}

resource "aws_prometheus_alert_manager_definition" "maezo" {
  count        = local.amp_alert_routing_ativo
  workspace_id = local.amp_workspace_id
  definition = templatefile("${path.module}/../../../observability/alertmanager-amp.yml.tftpl", {
    page_topic_arn   = aws_sns_topic.alertas["page"].arn
    ticket_topic_arn = aws_sns_topic.alertas["ticket"].arn
    aws_region       = var.aws_region
  })

  depends_on = [aws_sns_topic_policy.alertas]
}
