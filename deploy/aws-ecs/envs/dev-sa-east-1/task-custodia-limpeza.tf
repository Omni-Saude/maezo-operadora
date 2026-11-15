# ---------------------------------------------------------------------------
# Limpeza da custodia do telefone (ADR-0061 Proposto, dev): task AGENDADA que apaga as linhas
# VENCIDAS de `beneficiario_contato_retomada` (`python -m maezo.platform.custodia_limpeza`, que chama
# `PostgresRecipientVault.purge_expired`). Ate' aqui a leitura so' IGNORAVA a linha vencida; nada a apagava.
#
# NASCE DESLIGADO E SEM EFEITO, em tres camadas independentes:
#   1. `var.custodia_limpeza = null` (default): NENHUM recurso deste arquivo existe.
#   2. Com o objeto, `habilitado = false` (default) -> `MAEZO_CUSTODIA_LIMPEZA=false`: a task sobe, loga
#      `modo=desligado` e sai antes de montar qualquer dependencia.
#   3. `schedule_enabled = false` (default) cria o agendamento DESLIGADO: so' execucao manual.
# Ligar e' um PR que troca estes valores + apply, DEPOIS da ciencia do DPO para o ADR-0061
# (`docs/sme-dispatch/dpo/CIENCIA-ADR-0061-RASCUNHO.md`, decisao (c) da planilha do DPO de 20/10/2026).
#
# AGENDAMENTO proposto: uma vez por dia, 03h30 America/Sao_Paulo. Repetir e' inofensivo (DELETE do
# que ja' venceu). Sem KMS: o job monta o cofre com `SemChaveKeyWrapper` e nunca abre um telefone;
# a role e' a `task` compartilhada (nenhuma permissao na chave da custodia). Banco: o MESMO login da
# aplicacao (`local.db_secrets_maezo`) que grava a custodia.
# ---------------------------------------------------------------------------

variable "custodia_limpeza" {
  description = "Limpeza diaria da custodia do telefone (ADR-0061). null = nada e criado. Desligado por padrao."
  type = object({
    habilitado       = optional(bool, false)
    schedule_enabled = optional(bool, false)
  })
  default = null
}

locals {
  custodia_limpeza = var.custodia_limpeza == null ? {} : { this = var.custodia_limpeza }
}

resource "aws_cloudwatch_log_group" "custodia_limpeza" {
  for_each          = local.custodia_limpeza
  name              = "/ecs/${local.name}/custodia-limpeza"
  retention_in_days = var.log_retention_days
  tags              = merge(local.base_tags, { zona = "general" })
}

resource "aws_ecs_task_definition" "custodia_limpeza" {
  for_each                 = local.custodia_limpeza
  family                   = "${local.name}-custodia-limpeza"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "custodia-limpeza"
    image     = "${aws_ecr_repository.app.repository_url}:${var.webhook_receiver_image_tag}"
    essential = true

    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.platform.custodia_limpeza"]

    environment = concat(local.db_env, [
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      { name = "MAEZO_CUSTODIA_LIMPEZA", value = tostring(each.value.habilitado) },
    ])

    secrets = local.db_secrets_maezo

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.custodia_limpeza[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "custodia-limpeza"
      }
    }
  }])

  tags = merge(local.base_tags, { zona = "phi" })
}

resource "aws_iam_role" "custodia_limpeza_scheduler" {
  for_each           = local.custodia_limpeza
  name               = "${local.name}-custodia-limpeza-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "custodia_limpeza_scheduler" {
  for_each = local.custodia_limpeza
  statement {
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:task-definition/${aws_ecs_task_definition.custodia_limpeza[each.key].family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task_execution.arn, aws_iam_role.task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "custodia_limpeza_scheduler" {
  for_each = local.custodia_limpeza
  name     = "${local.name}-custodia-limpeza-scheduler"
  role     = aws_iam_role.custodia_limpeza_scheduler[each.key].id
  policy   = data.aws_iam_policy_document.custodia_limpeza_scheduler[each.key].json
}

resource "aws_scheduler_schedule" "custodia_limpeza" {
  for_each   = local.custodia_limpeza
  name       = "${local.name}-custodia-limpeza"
  group_name = "default"
  state      = each.value.schedule_enabled ? "ENABLED" : "DISABLED"

  # Uma vez por dia, 03h30 (America/Sao_Paulo). Repetir e' inofensivo: apaga so' o que ja' venceu.
  schedule_expression          = "cron(30 3 * * ? *)"
  schedule_expression_timezone = "America/Sao_Paulo"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.custodia_limpeza_scheduler[each.key].arn
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.custodia_limpeza[each.key].arn
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
