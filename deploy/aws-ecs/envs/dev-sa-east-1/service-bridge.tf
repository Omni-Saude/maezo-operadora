# Ponte de notificacao (`notifications_bridge`) — quem CONSOME o topico interno.
#
# O DEFEITO (passo 9 do fluxo de triagem, apontado pelo diretor em 09/09/2026): o worker
# publica em `operadora.notifications.internal` e NADA consumia esse topico no ambiente que
# esta' de pe'. O modulo existe e esta' testado (`platform/integrations/notifications_bridge.py`,
# 03-05/09), o Helm tem tres deployments de ponte — mas o ambiente vivo e' ECS, e aqui nao
# havia servico. Sem isto, um alerta de risco de SLA nunca vira User Task para um humano, e o
# "aviso chegar a pessoa" nao fecha nem com as filas criadas.
#
# O QUE A PONTE FAZ: consome o topico, casa o `type` da mensagem com uma regra registrada e
# INICIA o processo alvo pelo chokepoint auditado (`build_cibseven_process_starter` ->
# `start_process_idempotent`, ADR-0007/T-C2) — nunca POST cru no motor. Precisa de banco
# (audit sink), motor e Kafka. Nao expoe porta: e' consumer puro (o Helm usa liveness por
# processo vivo, `pgrep`; a imagem python:3.12-slim nao traz `pgrep`, e um `essential`
# container que morre ja' derruba a task — o ECS reinicia).
#
# IMAGEM: a do receptor (`webhook_receiver_image_tag`), porque o modulo da ponte NAO EXISTE na
# imagem dos agentes (`40dc6d4`): `ModuleNotFoundError` no boot — exatamente o que o docstring
# do modulo descreve ter acontecido no Helm.

resource "aws_cloudwatch_log_group" "bridge" {
  name              = "/ecs/${local.name}/notifications-bridge"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "bridge" {
  family                   = "${local.name}-notifications-bridge"
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
    name      = "notifications-bridge"
    image     = "${aws_ecr_repository.app.repository_url}:${var.webhook_receiver_image_tag}"
    essential = true

    # Mesmo entrypoint do Helm. DSN montado como nos demais: a ponte exige DATABASE_URL para o
    # audit sink (fail-closed — sem ele nao inicia processo nenhum).
    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.platform.integrations.notifications_bridge"]

    environment = concat(local.db_env, [
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      { name = "CIBSEVEN_BASE_URL", value = local.cibseven_base_url },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = local.kafka_bootstrap },
      # Topico e consumer group ficam nos defaults do modulo (`NOTIFICATIONS_TOPIC`,
      # `DEFAULT_CONSUMER_GROUP_ID`): sao contrato do codigo, nao configuracao de ambiente.
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = local.db_secrets_maezo

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.bridge.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "bridge"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "bridge" {
  name            = "notifications-bridge"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.bridge.arn
  desired_count   = var.bridge_desired_count
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
