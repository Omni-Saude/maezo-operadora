# a2a-outbox-relay (SC-01 / R-004) — drena `a2a_fact_outbox` (Postgres) para o Kafka,
# at-least-once. Servico dedicado, ECS-equivalente de `deployment-a2a-outbox-relay.yaml`
# no chart Helm (mesma decisao do dono, OWNER-DECISIONS-REGISTER R-004 opcao B).
#
# DRIFT vs a leitura literal do memorando ("servico equivalente ... mirroring how the existing
# bridge service is declared" / "espelhando o sibling notificationsBridge"): nenhuma das tres
# pontes Kafka->process-start (notifications-bridge, network-change-bridge,
# consent-revocation-bridge) tem service ECS neste ambiente — sao Helm-only (verificado:
# `grep -rln "notifications_bridge\|notifications-bridge" deploy/aws-ecs/envs/dev-sa-east-1/*.tf`
# nao acha nada). Nao ha "servico de bridge existente" para espelhar literalmente. Este arquivo
# implementa a INTENCAO da decisao (relay drenando o outbox em producao, com paridade ECS/Helm)
# usando o padrao mais proximo que EXISTE no ambiente: `service-worker.tf` (daemon Python de
# longa duracao, TENANT_ID + DATABASE_URL via `local.dsn_export_app` + KAFKA_BOOTSTRAP_SERVERS,
# sem trafego de entrada, sem ALB/Cloud Map).
#
# Sem healthCheck (ao contrario do worker): `outbox_relay.py` nao sobe servidor de health (mesma
# situacao do notifications-bridge no chart Helm — so' roda o loop de poll). Um healthCheck via
# `pgrep`, como a liveness do chart, nao existe como primitiva de container `healthCheck` do ECS
# (que so' roda `CMD`/`CMD-SHELL` contra o PROPRIO container — pgrep funcionaria, mas mediria
# apenas "o processo esta' vivo", o mesmo que ECS ja' garante via `essential = true` + o exit code
# do container). Omitido por honestidade: nenhum sinal adicional real estaria sendo checado.

resource "aws_cloudwatch_log_group" "a2a_outbox_relay" {
  name              = "/ecs/${local.name}/a2a-outbox-relay"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "a2a_outbox_relay" {
  family                   = "${local.name}-a2a-outbox-relay"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.a2a_outbox_relay_cpu)
  memory                   = tostring(var.a2a_outbox_relay_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "a2a-outbox-relay"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    # `build_relay` (outbox_relay.py) recusa construir sem DATABASE_URL nem
    # KAFKA_BOOTSTRAP_SERVERS — fail-closed, nunca reivindica linhas do outbox e as descarta em
    # silencio. Sem `--once`: o modulo faz poll ate SIGTERM/SIGINT por padrao (module docstring).
    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.a2a.outbox_relay"]

    environment = concat(local.db_env, [
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = local.kafka_bootstrap },
      # Mesmos defaults do bloco `a2aOutboxRelay` do chart Helm (outbox.py `DEFAULT_BATCH_SIZE` /
      # outbox_relay.py `DEFAULT_POLL_INTERVAL_S`) — paridade Helm/ECS deliberada.
      { name = "A2A_OUTBOX_RELAY_BATCH_SIZE", value = "100" },
      { name = "A2A_OUTBOX_RELAY_POLL_INTERVAL_S", value = "2" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = local.db_secrets_maezo

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.a2a_outbox_relay.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "a2a-outbox-relay"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "a2a_outbox_relay" {
  name            = "a2a-outbox-relay"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.a2a_outbox_relay.arn
  desired_count   = var.a2a_outbox_relay_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  # O relay nao recebe trafego de entrada — nao ha ALB nem registro em Cloud Map. Ele PUXA do
  # outbox Postgres e EMPURRA para o Kafka; nada precisa alcanca-lo de fora (mesma topologia do
  # worker-runtime em service-worker.tf).

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}
