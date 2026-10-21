# ---------------------------------------------------------------------------
# Sincronizacao automatica do egresso 443 do portal para o Cognito (decisao do dono,
# 12/10/2026, opcao A). Os dois hosts de que o portal depende —
#   amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com  (POST /oauth2/token, managed login)
#   cognito-idp.sa-east-1.amazonaws.com                  (JWKS do issuer)
# — rotacionam IP, e a lista /32 manual de `portal.auto.tfvars` quebrou o login 5 vezes
# (#695, #720 e anteriores). PrivateLink nao cobre os endpoints de DOMINIO do Cognito (docs
# AWS), entao foi descartado.
#
# Uma Lambda agendada (fora da VPC: so DNS publico + API EC2) resolve os hosts e mantem regras
# /32 tcp/443 PROPRIAS no SG do portal, marcadas `maezo:managed-by=cognito-egress-sync`. Ela
# nunca toca as regras do Terraform (sem a tag) e o Terraform nao ve as dela (regras avulsas
# de `aws_vpc_security_group_egress_rule` nao reconciliam regras alheias do mesmo SG).
# Invariantes e operacao: docs/runbooks/portal-egresso-cognito.md.
# ---------------------------------------------------------------------------

variable "portal_cognito_egress_sync" {
  description = "Rotina que mantem /32 do Cognito no SG do portal. enabled=false nao cria nada. security_group_id=null usa o SG do portal (aws_security_group.portal)."
  type = object({
    enabled             = optional(bool, true)
    hosts               = optional(list(string), ["amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com", "cognito-idp.sa-east-1.amazonaws.com"])
    security_group_id   = optional(string)
    stale_hours         = optional(number, 24)
    max_rules           = optional(number, 20)
    schedule_expression = optional(string, "rate(5 minutes)")
  })
  default = {}

  validation {
    condition = (
      length(var.portal_cognito_egress_sync.hosts) > 0 &&
      alltrue([for h in var.portal_cognito_egress_sync.hosts : can(regex("^[a-z0-9.-]+\\.(amazoncognito\\.com|amazonaws\\.com)$", h))]) &&
      (var.portal_cognito_egress_sync.security_group_id == null || can(regex("^sg-[0-9a-f]{8,17}$", var.portal_cognito_egress_sync.security_group_id))) &&
      var.portal_cognito_egress_sync.stale_hours >= 1 &&
      var.portal_cognito_egress_sync.max_rules >= 1 && var.portal_cognito_egress_sync.max_rules <= 40
    )
    error_message = "portal_cognito_egress_sync: hosts so *.amazoncognito.com / *.amazonaws.com, SG sg-..., stale_hours >= 1, 1 <= max_rules <= 40."
  }
}

locals {
  cognito_egress_sync_sg_id = coalesce(
    var.portal_cognito_egress_sync.security_group_id,
    try(aws_security_group.portal["this"].id, null),
    "sg-00000000000000000",
  )
  # Liga so com um SG real: o do portal (var.portal != null) ou um informado.
  cognito_egress_sync      = var.portal_cognito_egress_sync.enabled && (var.portal != null || var.portal_cognito_egress_sync.security_group_id != null) ? { this = var.portal_cognito_egress_sync } : {}
  cognito_egress_sync_name = "${local.name}-portal-cognito-egress-sync"
  # SCP `require-mandatory-tags` (p-bb3h9tps) nega lambda:CreateFunction sem as cinco tags obrigatorias
  # (tenant, environment, workload, owner, cost_center); medido no primeiro apply (18/10/2026, 403).
  # Mesmo conjunto do bucket do simulador (qa-simulador.tf).
  cognito_egress_sync_tags = merge(local.base_tags, {
    Component   = "portal-cognito-egress-sync"
    tenant      = "amh"
    environment = "dev"
    workload    = "maezo-portal-cognito-egress-sync"
    owner       = "data-platform@amh.health"
    cost_center = "amh-data-dev"
  })
}

data "archive_file" "cognito_egress_sync" {
  type        = "zip"
  source_file = "${path.module}/../../lambdas/cognito_egress_sync/handler.py"
  output_path = "${path.module}/.build/cognito_egress_sync.zip"
}

resource "aws_cloudwatch_log_group" "cognito_egress_sync" {
  for_each          = local.cognito_egress_sync
  name              = "/aws/lambda/${local.cognito_egress_sync_name}"
  retention_in_days = 30
  tags              = local.cognito_egress_sync_tags
}

data "aws_iam_policy_document" "cognito_egress_sync_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_iam_role" "cognito_egress_sync" {
  for_each           = local.cognito_egress_sync
  name               = local.cognito_egress_sync_name
  assume_role_policy = data.aws_iam_policy_document.cognito_egress_sync_assume.json
  tags               = local.cognito_egress_sync_tags
}

data "aws_iam_policy_document" "cognito_egress_sync" {
  for_each = local.cognito_egress_sync

  # Describe* do EC2 nao suporta recurso granular (nao aceita ARN); a resposta e so metadado
  # de regra de SG. Unico `*` da policy.
  statement {
    sid     = "ListarRegrasDoSG"
    actions = ["ec2:DescribeSecurityGroupRules"]
    # trivy:ignore:AWS-0057 exp:2027-04-12
    resources = ["*"]
  }

  # Criar/revogar egress: so no SG alvo (o recurso security-group e obrigatorio nas duas acoes).
  statement {
    sid       = "EgressNoSGAlvo"
    actions   = ["ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupEgress"]
    resources = ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:security-group/${local.cognito_egress_sync_sg_id}"]
  }

  # A regra NASCE marcada (TagSpecifications): sem a tag da rotina o Authorize e negado.
  statement {
    sid       = "RegraNovaSoComATagDaRotina"
    actions   = ["ec2:AuthorizeSecurityGroupEgress"]
    resources = ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:security-group-rule/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/maezo:managed-by"
      values   = ["cognito-egress-sync"]
    }
  }

  # Tag-on-create: CreateTags so como parte do Authorize (nao serve para marcar regra alheia).
  statement {
    sid       = "TagNaCriacao"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:security-group-rule/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["AuthorizeSecurityGroupEgress"]
    }
  }

  # Atualizar `maezo:last-seen`: so em regras JA marcadas pela rotina, e so essa chave.
  statement {
    sid       = "AtualizarLastSeenDasRegrasDaRotina"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:security-group-rule/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/maezo:managed-by"
      values   = ["cognito-egress-sync"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["maezo:last-seen"]
    }
  }

  # Revogar por SecurityGroupRuleId: so regras marcadas pela rotina (nunca as do Terraform).
  statement {
    sid       = "RevogarSoRegrasDaRotina"
    actions   = ["ec2:RevokeSecurityGroupEgress"]
    resources = ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:security-group-rule/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/maezo:managed-by"
      values   = ["cognito-egress-sync"]
    }
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.cognito_egress_sync[each.key].arn}:*"]
  }
}

resource "aws_iam_role_policy" "cognito_egress_sync" {
  for_each = local.cognito_egress_sync
  name     = local.cognito_egress_sync_name
  role     = aws_iam_role.cognito_egress_sync[each.key].id
  policy   = data.aws_iam_policy_document.cognito_egress_sync[each.key].json
}

# Fora da VPC de proposito: so fala com a API EC2 e com o DNS publico (nada de PHI, nada interno).
# Sem X-Ray (AWS-0066, LOW): job de DNS + 3 chamadas EC2; a auditoria e o log JSON/EMF.
# trivy:ignore:AWS-0066 exp:2027-04-12
resource "aws_lambda_function" "cognito_egress_sync" {
  for_each         = local.cognito_egress_sync
  function_name    = local.cognito_egress_sync_name
  description      = "Mantem /32 tcp/443 do Cognito no SG do portal (regras tag maezo:managed-by=cognito-egress-sync)"
  role             = aws_iam_role.cognito_egress_sync[each.key].arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.cognito_egress_sync.output_path
  source_code_hash = data.archive_file.cognito_egress_sync.output_base64sha256
  architectures    = ["arm64"]
  memory_size      = 128
  timeout          = 60
  # 1: duas rodadas simultaneas poderiam criar a mesma regra / disputar o teto.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      SECURITY_GROUP_ID = local.cognito_egress_sync_sg_id
      HOSTS             = join(",", each.value.hosts)
      STALE_HOURS       = tostring(each.value.stale_hours)
      MAX_RULES         = tostring(each.value.max_rules)
      METRIC_NAMESPACE  = "maezo-operadora/${local.env}/portal"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.cognito_egress_sync[each.key].name
  }

  tags = local.cognito_egress_sync_tags
}

# Invocacao assincrona pelo Scheduler: sem retry (a proxima rodada vem em 5 min) e sem DLQ —
# o evento nao carrega estado; uma falha so adia a sincronizacao e aparece no alarme abaixo.
resource "aws_lambda_function_event_invoke_config" "cognito_egress_sync" {
  for_each                     = local.cognito_egress_sync
  function_name                = aws_lambda_function.cognito_egress_sync[each.key].function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 300
}

# ------------------------------------------------------------------ agendamento
# Mesmo padrao do job staff (task-staff-ops.tf): EventBridge Scheduler com role propria.
data "aws_iam_policy_document" "cognito_egress_sync_scheduler" {
  for_each = local.cognito_egress_sync
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.cognito_egress_sync[each.key].arn]
  }
}

resource "aws_iam_role" "cognito_egress_sync_scheduler" {
  for_each           = local.cognito_egress_sync
  name               = "${local.cognito_egress_sync_name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = local.cognito_egress_sync_tags
}

resource "aws_iam_role_policy" "cognito_egress_sync_scheduler" {
  for_each = local.cognito_egress_sync
  name     = "${local.cognito_egress_sync_name}-scheduler"
  role     = aws_iam_role.cognito_egress_sync_scheduler[each.key].id
  policy   = data.aws_iam_policy_document.cognito_egress_sync_scheduler[each.key].json
}

resource "aws_scheduler_schedule" "cognito_egress_sync" {
  for_each            = local.cognito_egress_sync
  name                = local.cognito_egress_sync_name
  group_name          = "default"
  description         = "Sincroniza /32 do Cognito no SG do portal (runbook portal-egresso-cognito)"
  schedule_expression = each.value.schedule_expression
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_lambda_function.cognito_egress_sync[each.key].arn
    role_arn = aws_iam_role.cognito_egress_sync_scheduler[each.key].arn
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

# Falha em 3 rodadas seguidas (15 min): a rotina parou; o login so quebra na PROXIMA rotacao,
# mas a protecao deixou de existir.
resource "aws_cloudwatch_metric_alarm" "cognito_egress_sync_errors" {
  for_each            = local.cognito_egress_sync
  alarm_name          = "${local.cognito_egress_sync_name}-falhando"
  alarm_description   = "Lambda de sincronizacao do egresso Cognito do portal falhou 3 rodadas seguidas. Runbook: docs/runbooks/portal-egresso-cognito.md"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.cognito_egress_sync[each.key].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"
  # Reaproveita o topico de alertas do staff quando existe (task-staff-ops.tf); senao, so console.
  alarm_actions = [for topic in aws_sns_topic.staff_alerts : topic.arn]
  tags          = local.cognito_egress_sync_tags
}
