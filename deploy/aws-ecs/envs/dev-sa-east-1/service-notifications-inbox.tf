# Inbox duravel de avisos de escalonamento (`notifications_inbox`) — o SEGUNDO consumidor do
# topico interno `operadora.notifications.internal`, ao lado da ponte (`service-bridge.tf`).
#
# O QUE FAZ: le `escalation.notify_team` (publicado por `ST_NotificarTime` e pelo aviso da
# retomada) e grava UMA linha em `escalation_team_notice` (migracao 0015) — o fato que permite
# ao portal afirmar que a equipe foi avisada (contrato SP-OP-ESCALATION-001: "Kafka aceitou nao
# significa humano recebeu"). O offset so' anda depois da linha gravada; qualquer falha propaga
# e a mensagem volta. Consumer puro: sem porta, sem motor, so' Kafka + Postgres.
#
# DESLIGADO (desired_count = 0) ATE' DECISAO DO DONO. Com 0, NENHUM recurso deste arquivo existe
# (`count`), entao o `terraform plan` do ambiente nao muda ate' alguem ligar de proposito.
#
# BANCO E ROLE (medido no repo, NAO no ambiente): mesmo segredo da aplicacao
# (`local.db_secrets_maezo` -> role `maezo_app`), o MESMO com que `task-migrations.tf` roda o
# alembic. A tabela nasce, portanto, com dono `maezo_app`; o `REVOKE ALL ... FROM PUBLIC` da 0015
# nao tira privilegio do dono. Nenhum GRANT novo e' necessario nem inventado aqui. O estado real
# do banco do dev (dono efetivo da tabela, migracao 0015 aplicada) NAO foi medido.
#
# TETO 1 REPLICA: o inbox e' idempotente por `notice_ref`, mas a ponte e' singleton declarado
# (SC-04-b) e as duas leem o mesmo topico; escalar alem de 1 e' decisao a parte.
#
# CONSUMER GROUP: o default do modulo (`DEFAULT_INBOX_CONSUMER_GROUP_ID` =
# `maezo-notifications-inbox`), DISTINTO do da ponte (`notifications-bridge`) — compartilhar o
# grupo faria os dois disputarem particoes e cada um perderia mensagens em silencio. Nao vira
# variavel de ambiente: e' contrato do codigo, como na ponte.
#
# IMAGEM: a do receptor (`webhook_receiver_image_tag`), a mesma da ponte — o modulo importa a
# maquinaria Kafka/DLQ de `notifications_bridge`.

locals {
  notifications_inbox_ativo = var.notifications_inbox_desired_count > 0 ? 1 : 0
}

resource "aws_cloudwatch_log_group" "notifications_inbox" {
  count             = local.notifications_inbox_ativo
  name              = "/ecs/${local.name}/notifications-inbox"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "notifications_inbox" {
  count                    = local.notifications_inbox_ativo
  family                   = "${local.name}-notifications-inbox"
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
    name      = "notifications-inbox"
    image     = "${aws_ecr_repository.app.repository_url}:${var.webhook_receiver_image_tag}"
    essential = true

    # DATABASE_URL e' obrigatorio: sem ele `build_inbox` RECUSA subir (um inbox que nao grava
    # nao pode licenciar commit de offset).
    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.platform.integrations.notifications_inbox"]

    environment = concat(local.db_env, [
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = local.kafka_bootstrap },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = local.db_secrets_maezo

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.notifications_inbox[0].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "notifications-inbox"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "notifications_inbox" {
  count           = local.notifications_inbox_ativo
  name            = "notifications-inbox"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.notifications_inbox[0].arn
  desired_count   = var.notifications_inbox_desired_count
  launch_type     = "FARGATE"

  # ECS Exec exige root gravavel; o container e' somente leitura (mesma decisao do receptor).
  enable_execute_command = false

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}
