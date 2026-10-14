# ---------------------------------------------------------------------------
# Lembrete proativo de cobranca (dev): task AGENDADA que lembra o beneficiario, antes do vencimento, de
# que a mensalidade vence. `python -m maezo.platform.lembretes` (docs/runbooks/lembrete-cobranca.md).
#
# NASCE DESLIGADO E SEM EFEITO, em tres camadas independentes:
#   1. `var.lembrete_cobranca = null` (default): NENHUM recurso deste arquivo existe.
#   2. Com o objeto, `habilitado = false` (default) -> `MAEZO_LEMBRETE_COBRANCA=false`: a task sobe, loga
#      "desligado" e sai antes de montar qualquer dependencia.
#   3. Com `habilitado = true`, `dry_run = true` (default) -> so' calcula e loga CONTAGENS.
# Enviar de verdade exige `dry_run = false` E, em dev, `allowlist_conversas`/`allowlist_telefones`
# (`exigir_allowlist = true` recusa envio real sem lista). Ligar e' um PR que troca estes valores + apply.
#
# AGENDAMENTO: de hora em hora, das 09h as 18h em America/Sao_Paulo (o codigo ainda recusa fora de
# 08h-20h). A idempotencia (uma por pessoa e competencia) torna a repeticao inofensiva. `schedule_enabled =
# false` cria o agendamento DESLIGADO: so' execucao manual (`aws ecs run-task`, runbook).
#
# Ambiente e segredos: os do receptor do WhatsApp (`local.webhook_receiver_contrato`, o mesmo contrato do
# simulador), porque a task monta a MESMA fonte de cobranca do Lucas, o MESMO cliente de envio e o MESMO
# gate. Role: a da retomada (`agent_resume_task`, so' kms:Decrypt na custodia do telefone + Bedrock).
# ---------------------------------------------------------------------------

variable "lembrete_cobranca" {
  description = "Lembrete proativo de cobranca. null = nada e criado. Tudo desligado/dry-run por padrao."
  type = object({
    habilitado           = optional(bool, false)
    dry_run              = optional(bool, true)
    schedule_enabled     = optional(bool, false)
    dias_antecedencia    = optional(number, 3)
    allowlist_conversas  = optional(string, "")
    exigir_allowlist     = optional(bool, true)
    template_nome        = optional(string, "")
    limite_por_execucao  = optional(number, 50)
    pausa_entre_envios_s = optional(number, 1)
  })
  default = null

  validation {
    condition     = var.lembrete_cobranca == null ? true : (var.lembrete_cobranca.dias_antecedencia >= 1 && var.lembrete_cobranca.dias_antecedencia <= 15)
    error_message = "lembrete_cobranca.dias_antecedencia deve ficar entre 1 e 15."
  }
}

locals {
  lembrete_cobranca = var.lembrete_cobranca == null ? {} : { this = var.lembrete_cobranca }
}

resource "aws_cloudwatch_log_group" "lembrete_cobranca" {
  for_each          = local.lembrete_cobranca
  name              = "/ecs/${local.name}/lembrete-cobranca"
  retention_in_days = var.log_retention_days
  tags              = merge(local.base_tags, { zona = "general" })
}

resource "aws_ecs_task_definition" "lembrete_cobranca" {
  for_each                 = local.lembrete_cobranca
  family                   = "${local.name}-lembrete-cobranca"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.agent_cpu)
  memory                   = tostring(var.agent_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = local.agent_resume_task_role_arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "lembrete-cobranca"
    image     = "${aws_ecr_repository.app.repository_url}:${var.webhook_receiver_image_tag}"
    essential = true

    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.platform.lembretes"]

    environment = concat(
      local.webhook_receiver_contrato.environment_app,
      local.recipient_vault_env,
      local.webhook_receiver_contrato.environment_amh,
      [
        { name = "MAEZO_LEMBRETE_COBRANCA", value = tostring(each.value.habilitado) },
        { name = "MAEZO_LEMBRETE_DRY_RUN", value = tostring(each.value.dry_run) },
        { name = "MAEZO_LEMBRETE_DIAS_ANTECEDENCIA", value = tostring(each.value.dias_antecedencia) },
        { name = "MAEZO_LEMBRETE_ALLOWLIST_CONVERSAS", value = each.value.allowlist_conversas },
        { name = "MAEZO_LEMBRETE_EXIGIR_ALLOWLIST", value = tostring(each.value.exigir_allowlist) },
        { name = "MAEZO_LEMBRETE_TEMPLATE_NAME", value = each.value.template_nome },
        { name = "MAEZO_LEMBRETE_LIMITE_POR_EXECUCAO", value = tostring(each.value.limite_por_execucao) },
        { name = "MAEZO_LEMBRETE_PAUSA_ENTRE_ENVIOS_S", value = tostring(each.value.pausa_entre_envios_s) },
      ],
    )

    secrets = concat(
      local.webhook_receiver_contrato.secrets_app,
      local.webhook_receiver_contrato.secrets_envio,
      local.amh_interop_secrets,
    )

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.lembrete_cobranca[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "lembrete-cobranca"
      }
    }
  }])

  tags = merge(local.base_tags, { zona = "general" })
}

resource "aws_iam_role" "lembrete_cobranca_scheduler" {
  for_each           = local.lembrete_cobranca
  name               = "${local.name}-lembrete-cobranca-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "lembrete_cobranca_scheduler" {
  for_each = local.lembrete_cobranca
  statement {
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:task-definition/${aws_ecs_task_definition.lembrete_cobranca[each.key].family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task_execution.arn, local.agent_resume_task_role_arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "lembrete_cobranca_scheduler" {
  for_each = local.lembrete_cobranca
  name     = "${local.name}-lembrete-cobranca-scheduler"
  role     = aws_iam_role.lembrete_cobranca_scheduler[each.key].id
  policy   = data.aws_iam_policy_document.lembrete_cobranca_scheduler[each.key].json
}

resource "aws_scheduler_schedule" "lembrete_cobranca" {
  for_each   = local.lembrete_cobranca
  name       = "${local.name}-lembrete-cobranca"
  group_name = "default"
  state      = each.value.schedule_enabled ? "ENABLED" : "DISABLED"

  # De hora em hora, 09h-18h (America/Sao_Paulo). Repetir e' inofensivo: uma mensagem por pessoa e competencia.
  schedule_expression          = "cron(0 9-18 * * ? *)"
  schedule_expression_timezone = "America/Sao_Paulo"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.lembrete_cobranca_scheduler[each.key].arn
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.lembrete_cobranca[each.key].arn
      launch_type         = "FARGATE"
      task_count          = 1
      network_configuration {
        subnets          = data.aws_subnets.private_app.ids
        security_groups  = [aws_security_group.tasks.id]
        assign_public_ip = false
      }
    }
    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 300
    }
  }
}
