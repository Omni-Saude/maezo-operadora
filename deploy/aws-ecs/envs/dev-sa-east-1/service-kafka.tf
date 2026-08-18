# Broker Kafka de DESENVOLVIMENTO — nao e' o MSK, e nao pretende ser.
#
# POR QUE EXISTE
#
# O primeiro service task do SP-OP-AUTH-001 e' `ST_PublishReceived`, no topico
# `operadora.events.publish`. Publicar E' o trabalho dele, e ele falha FECHADO:
# medido em 18/08/2026, instancia 82456366-9aa6-11f1-a88f-06ad701ee363,
#
#   incidente failedExternalTask em ST_PublishReceived
#   KafkaConnectionError: Unable to bootstrap from [('localhost', 9092)]
#
# Sem broker o processo nao passa do PRIMEIRO passo. O teste local passava porque
# o docker-compose subia Kafka; na AWS o ADR-036 da plataforma estacionou o CDC e
# DELETOU o cluster MSK.
#
# Isto corrige uma afirmacao minha anterior ("o worker roda sem Kafka, so nao
# publica evento"): vale para o publisher OPCIONAL dos modulos de registro, nao
# para este passo.
#
# POR QUE NAO O MSK
#
# Ligar `create_msk=true` no env da plataforma recria o cluster que alguem deletou
# DE PROPOSITO para cortar custo (~US$ 100+/mes de base). Essa e' uma decisao do
# dono da plataforma, com ADR proprio — nao uma consequencia silenciosa de eu
# querer rodar um teste. Um broker de nó unico no NOSSO cluster custa ~US$ 20/mes,
# nao toca o state da plataforma e some com `kafka_desired_count = 0`.
#
# LIMITES, ditos na cara: nó unico, sem replicacao, dados em disco EFEMERO do
# Fargate — reiniciou, perdeu o log de eventos. Serve para provar que o fluxo
# atravessa. NAO serve para producao, onde o destino e' MSK (ou outra decisao
# ratificada).

resource "aws_cloudwatch_log_group" "kafka" {
  name              = "/ecs/${local.name}/kafka"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_service_discovery_service" "kafka" {
  name = "kafka"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  tags = local.base_tags
}

resource "aws_ecs_task_definition" "kafka" {
  family                   = "${local.name}-kafka"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "kafka"
    image     = "confluentinc/cp-kafka:7.7.0"
    essential = true

    portMappings = [{ containerPort = 9092, protocol = "tcp" }]

    environment = [
      # KRaft (sem ZooKeeper) — mesmo modo do docker-compose local.
      { name = "KAFKA_NODE_ID", value = "1" },
      { name = "KAFKA_PROCESS_ROLES", value = "broker,controller" },
      { name = "KAFKA_CONTROLLER_QUORUM_VOTERS", value = "1@127.0.0.1:29093" },
      { name = "CLUSTER_ID", value = "maezo-operadora-dev-01" },

      # O endereco ANUNCIADO tem de ser o que o cliente consegue alcancar. Um
      # broker que anuncia `localhost` manda todo cliente remoto falar consigo
      # mesmo — e o sintoma e' um timeout que parece de rede. Aqui anunciamos o
      # nome do Cloud Map, que e' exatamente o que worker e agentes usam.
      { name = "KAFKA_LISTENERS", value = "PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:29093" },
      { name = "KAFKA_ADVERTISED_LISTENERS", value = "PLAINTEXT://kafka.${local.name}.internal:9092" },
      { name = "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", value = "PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT" },
      { name = "KAFKA_INTER_BROKER_LISTENER_NAME", value = "PLAINTEXT" },
      { name = "KAFKA_CONTROLLER_LISTENER_NAMES", value = "CONTROLLER" },

      # Nó unico: fator de replicacao 1 em tudo, senao o broker recusa criar os
      # topicos internos e nada funciona.
      { name = "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR", value = "1" },
      { name = "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", value = "1" },
      { name = "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", value = "1" },
      { name = "KAFKA_AUTO_CREATE_TOPICS_ENABLE", value = "true" },
      { name = "KAFKA_HEAP_OPTS", value = "-Xms256m -Xmx640m" },
    ]

    healthCheck = {
      command     = ["CMD-SHELL", "kafka-broker-api-versions --bootstrap-server localhost:9092 >/dev/null 2>&1 || exit 1"]
      interval    = 30
      timeout     = 10
      retries     = 5
      startPeriod = 90
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.kafka.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "kafka"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "kafka" {
  name            = "kafka"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.kafka.arn
  desired_count   = var.kafka_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.kafka.arn
  }

  # Nó unico com estado em disco efemero: subir o novo antes de matar o velho
  # criaria dois brokers com o mesmo node id disputando o mesmo nome DNS.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}

# 9092 entre os componentes do maezo (auto-referencia no mesmo SG).
resource "aws_vpc_security_group_ingress_rule" "interno_9092" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Kafka de dev a partir dos demais componentes do maezo"
  ip_protocol                  = "tcp"
  from_port                    = 9092
  to_port                      = 9092
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_egress_rule" "interno_9092" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Saida para o Kafka de dev"
  ip_protocol                  = "tcp"
  from_port                    = 9092
  to_port                      = 9092
  referenced_security_group_id = aws_security_group.tasks.id
}
