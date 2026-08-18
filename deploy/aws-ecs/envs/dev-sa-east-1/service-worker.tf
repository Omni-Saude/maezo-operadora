# worker-runtime — o daemon que registra os 16 workers de external task no engine
# BPMN e executa o trabalho de cada processo SP-OP.
#
# KAFKA: CORRECAO DE UMA AFIRMACAO MINHA (18/08/2026).
#
# Eu havia escrito aqui que "sem broker o daemon sobe e trabalha; apenas nao
# publica evento de dominio". Isso vale para o publisher OPCIONAL dos 17 modulos
# de registro (`kafka: KafkaPublisher | None = None`), e NAO vale para o worker
# generico `operadora.events.publish`, onde publicar E' o trabalho.
#
# Medido: o PRIMEIRO service task do SP-OP-AUTH-001 (`ST_PublishReceived`) usa esse
# topico. Sem broker ele falha fechado e o processo nao passa do passo 1 —
# instancia 82456366-9aa6-11f1-a88f-06ad701ee363, incidente failedExternalTask,
# `KafkaConnectionError: Unable to bootstrap from [('localhost', 9092)]`.
# O teste local passava porque o docker-compose subia Kafka.
#
# Dai o broker de dev em `service-kafka.tf` e a variavel abaixo. Producao e' MSK
# (ou outra decisao ratificada), nao esse nó unico.
#
# DATABASE_URL e' obrigatorio de verdade: sem ele o sink duravel de auditoria nao e'
# construido, o harness falha fechado e registra ZERO workers. Foi medido no boot
# local de 2026-08-12 (o compose nao injetava a variavel e o worker subia mudo).

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.worker_cpu)
  memory                   = tostring(var.worker_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "worker-runtime"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.runtime.worker_runtime"]

    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    environment = concat(local.db_env, [
      # asyncpg: a aplicacao usa driver assincrono (o normalize_dsn do checkpoint
      # cuida do caso psycopg internamente, mas a fonte deve ser a real).
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      # Identidade do worker no engine. Precisa ser estavel o suficiente para
      # rastrear, e distinta por task para nao haver dois donos do mesmo lock.
      { name = "WORKER_ID", value = "${local.name}-worker" },
      { name = "CIBSEVEN_BASE_URL", value = local.cibseven_base_url },
      { name = "FHIR_BASE_URL", value = local.fhir_base_url },
      { name = "HEALTH_PORT", value = "8000" },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = local.kafka_bootstrap },
      # production liga os gates fail-closed. Em nuvem nao existe motivo honesto
      # para rodar em `local`: seria afirmar que os controles estao ativos quando
      # nao estao.
      { name = "AGENT_RUNTIME_MODE", value = var.agent_runtime_mode },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = concat(local.db_secrets_maezo, [
      # A aresta de delegacao de dossie (worker -> Carolina/Andre) SO' e' composta
      # com esta chave. Sem ela o runtime recusa montar o dispatcher e loga
      # `dossier_delegation_ready=False` — foi exatamente o que a primeira subida
      # mostrou, e e' o gate do ADR-0039 funcionando, nao um defeito.
      { name = "MAEZO_A2A_CARD_SIGNING_KEY__${upper(var.tenant_id)}", valueFrom = "${aws_secretsmanager_secret.a2a_card_signing_key.arn}:key::" },
      { name = "PHI_HMAC_KEY", valueFrom = "${aws_secretsmanager_secret.phi_hmac_key.arn}:key::" },
    ])

    readonlyRootFilesystem = true

    healthCheck = {
      # python, nao curl: a imagem e' python:3.12-slim e NAO traz curl. Um
      # healthCheck com curl daria "unhealthy" eterno por comando inexistente —
      # falha que parece de aplicacao e e' de ferramenta ausente. Mesmo comando que
      # o docker-compose local usa.
      command     = ["CMD-SHELL", "python -c \"import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://localhost:8000/healthz',timeout=3).status==200 else 1)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "worker" {
  name            = "worker-runtime"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  # O worker nao recebe trafego de entrada — nao ha ALB nem registro em Cloud Map.
  # Ele PUXA trabalho do engine (external task pattern), entao a direcao da conexao
  # e' worker -> engine, e nada precisa alcancar o worker de fora.

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}
