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

# ---------------------------------------------------------------------------
# DIGEST DA IMAGEM DO ENGINE (19/09/2026, decisao do dono — pacote trivy/IaC D2)
# O repositorio `amh/cibseven-maezo` ficou IMMUTABLE (ecs.tf), entao a task def
# consome DIGEST, nao tag — mesmo padrao da sonda (`diagnostics_image_digest`) e
# do portal: variavel obrigatoria, sem default, sem "digest inventado". O digest
# sai da saida do CodeBuild (que imprime `describe-images ... imageDigest` no fim).
# ---------------------------------------------------------------------------
variable "engine_image_digest" {
  description = "Digest sha256 completo da imagem ECR do engine (`amh/cibseven-maezo`) que o servico cibseven executa; obrigatorio no deploy (o repo e' IMMUTABLE), sem tag mutavel ou digest inventado."
  type        = string
  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.engine_image_digest))
    error_message = "Forneca o digest sha256 completo de uma imagem do engine qualificada."
  }
}

resource "aws_cloudwatch_log_group" "cibseven" {
  name              = "/ecs/${local.name}/cibseven"
  retention_in_days = var.log_retention_auditoria_days
  kms_key_id        = local.logs_kms_key_arn
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

  # X86_64: a imagem do engine e' construida so' para amd64 (CodeBuild x86_64). Registrado em
  # 05/11/2026 na decisao de custo: ARM64 so' onde a imagem ja' e' multi-arch.
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  # PROD: o engine nativo (Onda 4 do dev: NLB 443/mTLS, materiais KMS, sidecar staff-case-issuer)
  # NAO foi portado — os materiais sao por ambiente e nao existem em prod (README, "Fora deste PR").
  container_definitions = jsonencode([{
    name = "cibseven"
    # Imagem PROPRIA, derivada de cibseven/cibseven:2.1.0 sem o showcase de
    # demonstracao (deploy/cibseven/Dockerfile). A oficial cria o usuario `demo` — e o
    # recria a cada boot — o que dava administracao do motor a quem passasse pelo Access.
    #
    # 2.1.0 e nao 2.1.3 na base: a 2.1.3 nao tem imagem publicada (DL-0006).
    # DIGEST, nao tag (19/09/2026, decisao do dono — pacote trivy/IaC D2): o repo do
    # engine ficou IMMUTABLE (ecs.tf) e o que roda fica amarrado ao artefato provado.
    image     = "${aws_ecr_repository.engine.repository_url}@${var.engine_image_digest}"
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
      # PROD: DDL automatica do engine DESLIGADA por padrao (o comentario do dev ja' dizia:
      # "em producao isto vira false"). A primeira subida em banco vazio precisa criar o
      # schema: ligar `engine_db_schema_update = true` por PR so' para esse boot e voltar a
      # `false` no PR seguinte (README, cutover passo "schema do engine").
      { name = "DB_SCHEMA_UPDATE", value = var.engine_db_schema_update ? "true" : "false" },
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
  desired_count   = var.servicos_habilitados ? var.cibseven_desired_count : 0
  launch_type     = "FARGATE"

  # Shell dentro da task rodando — em dev e' o que responde perguntas que so o
  # interior da VPC responde. Desligar em producao.
  enable_execute_command = var.ecs_exec_habilitado # prod: desligado por padrao (break-glass por PR)

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = local.task_security_groups
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

  # N5 em PROD (review de seguranca do PR #1204): a declaracao `client-certificate` (main.tf)
  # nao basta. Com servicos ligados, o engine so' sobe com imagem `secured-v2` COMPROVADA duas
  # vezes: (1) atestacao explicita de quem promoveu (`engine_image_variante`) e (2) a imagem do
  # digest, no ECR de prod, carregando uma tag `secured-v2-*` (o ECR e' IMMUTABLE: tag nao e'
  # reescrita). A imagem aberta do dev (`sem-showcase-*`) reprova o plan.
  lifecycle {
    precondition {
      condition = !var.servicos_habilitados || (
        var.engine_image_variante == "secured-v2" &&
        length([for img in data.aws_ecr_image.engine : img if anytrue([for t in coalesce(img.image_tags, []) : startswith(t, "secured-v2-")])]) == 1
      )
      error_message = "Prod com servicos ligados exige engine secured-v2 comprovado: engine_image_variante = \"secured-v2\" E o digest do engine com tag secured-v2-* no ECR de prod. A imagem aberta do dev nao sobe aqui (N5)."
    }
  }

  tags = local.base_tags
}

variable "engine_image_variante" {
  description = <<-EOT
    ATESTACAO de quem promove o engine: qual Dockerfile gerou `engine_image_digest`. So' `secured-v2`
    (deploy/cibseven/Dockerfile.secured-v2: engine-rest por certificado, sem fallback anonimo) e'
    aceito com servicos ligados. Vazio (default) = nenhuma atestacao, e o servico nao pode subir.
  EOT
  type        = string
  default     = ""
  validation {
    condition     = contains(["", "secured-v2"], var.engine_image_variante)
    error_message = "engine_image_variante: vazio ou \"secured-v2\" (a imagem aberta do dev nao e' opcao em prod)."
  }
}

# So' com servicos ligados (com tudo parado a imagem pode nem ter sido copiada ainda).
data "aws_ecr_image" "engine" {
  count           = var.servicos_habilitados ? 1 : 0
  repository_name = aws_ecr_repository.engine.name
  image_digest    = var.engine_image_digest
}
