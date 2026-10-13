# Tarefa avulsa do SIMULADOR DE BENEFICIARIO (`python -m maezo.qa.simulador`). So' dev, so' RunTask:
# nao ha' servico, porta nem ECS Exec. Runbook: `docs/runbooks/simulador-beneficiario.md`.
#
# O QUE ELA E'. O receptor do WhatsApp sem o HTTP: a MESMA imagem, o MESMO ambiente e os MESMOS
# segredos (`local.webhook_receiver_contrato`, em service-webhook-receiver.tf — um so' lugar, para
# os dois nao derivarem), compondo o MESMO despachante (`build_live_dispatcher`). O simulador troca
# so' as bordas de saida: a Meta vira captura, SP-OP-ESCALATION-001 vira "teria escalado", a
# gravacao de consentimento de terceiros na AMH nao acontece (modo padrao `somente-leitura`).
#
# O QUE ELA NAO TEM, de proposito:
#   * credenciais de envio da Meta (`secrets_envio`): o simulador nunca envia;
#   * a custodia do telefone (`recipient_vault_env`): numero sintetico nao vai para o cofre;
#   * a role do receptor: role PROPRIA, com o Bedrock que o receptor tem (o modelo dos agentes e o
#     da persona/juiz e' o mesmo) e S3/KMS SO' no bucket do simulador (qa-simulador.tf).
#
# ARGUMENTOS. `SIMULADOR_ARGS` (sobrescrito no RunTask) vira a linha de comando; o padrao roda 10
# conversas. Rede: subnets privadas do env e o SG das tasks (a mesma rede do receptor: Aurora, motor,
# AMH e Bedrock).

variable "simulador_image_tag" {
  description = <<-EOT
    Tag da imagem do simulador. Vazia (padrao) = a MESMA do receptor (`webhook_receiver_image_tag`).
    Preencher so' para rodar o simulador com uma imagem mais nova sem promover o receptor.
  EOT
  type        = string
  default     = ""
}

locals {
  simulador_image_tag = var.simulador_image_tag != "" ? var.simulador_image_tag : var.webhook_receiver_image_tag
}

resource "aws_cloudwatch_log_group" "simulador" {
  name              = "/ecs/${local.name}/simulador"
  retention_in_days = var.log_retention_days
  tags              = merge(local.base_tags, { zona = "general" })
}

resource "aws_iam_role" "simulador_task" {
  name               = "${local.name}-simulador-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

# O mesmo Bedrock do receptor (o documento e' o mesmo: `task_bedrock`, secrets.tf).
resource "aws_iam_role_policy" "simulador_bedrock" {
  name   = "${local.name}-bedrock"
  role   = aws_iam_role.simulador_task.id
  policy = data.aws_iam_policy_document.task_bedrock.json
}

data "aws_iam_policy_document" "simulador_bucket" {
  statement {
    sid       = "ListarSoORosterEAsExecucoes"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.qa_simulador.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["roster/*", "runs/*"]
    }
  }
  statement {
    sid       = "LerRoster"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.qa_simulador.arn}/roster/*"]
  }
  statement {
    sid       = "LerEGravarExecucoes"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.qa_simulador.arn}/runs/*"]
  }
  statement {
    sid       = "UsarACmkDoBucket"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.qa_simulador.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "simulador_bucket" {
  name   = "${local.name}-simulador-bucket"
  role   = aws_iam_role.simulador_task.id
  policy = data.aws_iam_policy_document.simulador_bucket.json
}

resource "aws_ecs_task_definition" "simulador" {
  family                   = "${local.name}-simulador"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.agent_cpu)
  memory                   = tostring(var.agent_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.simulador_task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "simulador"
    image     = "${aws_ecr_repository.app.repository_url}:${local.simulador_image_tag}"
    essential = true

    # O DSN e' montado como no receptor; `SIMULADOR_ARGS` e' dividido em palavras de proposito.
    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.qa.simulador $SIMULADOR_ARGS"]

    environment = concat(
      local.webhook_receiver_contrato.environment_app,
      local.webhook_receiver_contrato.environment_amh,
      [
        { name = "MAEZO_SIMULADOR_BUCKET", value = aws_s3_bucket.qa_simulador.bucket },
        { name = "SIMULADOR_ARGS", value = "--conversas 10" },
      ],
    )

    secrets = concat(
      local.webhook_receiver_contrato.secrets_app,
      local.amh_interop_secrets,
    )

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.simulador.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "simulador"
      }
    }
  }])

  tags = merge(local.base_tags, { zona = "general" })
}

output "simulador_task_definition" {
  description = "Familia da task avulsa do simulador de beneficiario (aws ecs run-task)."
  value       = aws_ecs_task_definition.simulador.family
}
