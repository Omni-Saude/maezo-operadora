# CIB Seven — o engine BPMN/DMN. E' a espinha dorsal de governanca do produto:
# auditoria, SLA regulatorio e escalonamento humano sao garantidos por ele, nao por
# prompt. Sem o engine de pe, nenhum processo SP-OP existe.
#
# No chart era um StatefulSet. Aqui e' um service Fargate comum porque o estado NAO
# mora no pod — mora no Aurora (schema `cibseven`). Um StatefulSet so faria sentido
# se houvesse volume por replica, que nao ha.
#
# Replica unica de proposito: o engine faz job acquisition com lock em banco, e
# duas replicas em dev so multiplicam contencao sem ganho.

resource "aws_cloudwatch_log_group" "cibseven" {
  name              = "/ecs/${local.name}/cibseven"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_service_discovery_service" "cibseven" {
  name = "cibseven"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id

    dns_records {
      ttl  = 10 # baixo: o IP muda a cada deploy da task
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  # Sem `health_check_custom_config`: o argumento `failure_threshold` foi depreciado
  # (a AWS forca 1) e o ECS ja gerencia o registro/desregistro da instancia no Cloud
  # Map conforme a saude da task.

  tags = local.base_tags
}

resource "aws_ecs_task_definition" "cibseven" {
  family                   = "${local.name}-cibseven"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name = "cibseven"
    # 2.1.0 e nao 2.1.3: a 2.1.3 nao tem imagem publicada (DL-0006). Puxada do
    # Docker Hub pela NAT. Se o rate limit do Hub incomodar, espelhe em ECR — a
    # imagem nao muda, entao um espelho manual resolve.
    image     = "cibseven/cibseven:2.1.0"
    essential = true

    portMappings = [{ containerPort = 8080, protocol = "tcp" }]

    environment = [
      # DB_DRIVER e' OBRIGATORIO: sem ele o engine cai para H2 em memoria e o
      # webapp nem sobe — mesmo achado que o compose local ja registrava.
      { name = "DB_DRIVER", value = "org.postgresql.Driver" },
      # currentSchema mantem as tabelas do engine no schema `cibseven`, separadas
      # das do maezo. A role cibseven_app e' dona desse schema (V001 do repo da
      # plataforma).
      { name = "DB_URL", value = "jdbc:postgresql://${local.aurora_endpoint}:${local.aurora_port}/${var.aurora_database_name}?currentSchema=cibseven" },
      # Criacao/atualizacao automatica do schema do engine. Aceitavel em dev, onde
      # o schema ainda nao existe. Em producao isto vira `false` e o schema passa a
      # ser aplicado por migration versionada — DDL automatica em producao e' como
      # se perde a capacidade de saber o que mudou e quando.
      { name = "DB_SCHEMA_UPDATE", value = "true" },
      { name = "TZ", value = "America/Sao_Paulo" },
      { name = "JAVA_OPTS", value = "-Xms512m -Xmx1400m -XX:+UseG1GC -XX:+ExitOnOutOfMemoryError" },
    ]

    secrets = [
      { name = "DB_USERNAME", valueFrom = "${data.aws_secretsmanager_secret.cibseven_app_db.arn}:username::" },
      { name = "DB_PASSWORD", valueFrom = "${data.aws_secretsmanager_secret.cibseven_app_db.arn}:password::" },
    ]

    healthCheck = {
      # /engine-rest/version responde sem autenticacao e prova que o engine subiu
      # de verdade — nao apenas que a JVM esta viva.
      command     = ["CMD-SHELL", "curl -sf http://localhost:8080/engine-rest/version || exit 1"]
      interval    = 30
      timeout     = 10
      retries     = 5
      startPeriod = 120 # boot do engine + criacao de schema na primeira subida
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.cibseven.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "cibseven"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "cibseven" {
  name            = "cibseven"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.cibseven.arn
  desired_count   = var.cibseven_desired_count
  launch_type     = "FARGATE"

  # Shell dentro da task rodando — em dev e' o que responde perguntas que so o
  # interior da VPC responde. Desligar em producao.
  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false # saida pela NAT; a task nunca e' alcancavel da internet
  }

  service_registries {
    registry_arn = aws_service_discovery_service.cibseven.arn
  }

  # Replica unica: derrubar a velha antes de subir a nova evita duas instancias do
  # engine disputando lock de job. Ha uma janela de indisponibilidade — aceita em
  # dev, e o motivo pelo qual isto NAO serve para producao sem revisao.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}
