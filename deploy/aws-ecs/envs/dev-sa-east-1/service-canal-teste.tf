# Canal de Teste — a pagina que o time de dados usa para testar SEM CLI e SEM mim.
#
# Mesma experiencia do teste local (formulario de lancamento dos processos SP-OP +
# visor de evidencia ao lado), agora dentro da AWS e atras do Cloudflare Access.
#
# POR QUE O SCRIPT VEM DE `file()` E NAO DA IMAGEM
#
# O canal e' stdlib pura, e a imagem que esta no ECR (`8f06890`) ainda nao contem o
# modulo — o Docker Desktop desta estacao esta parado e nao consigo publicar imagem
# nova agora. Em vez de bloquear a entrega, o Terraform LE o arquivo do repositorio e
# o passa como argumento de `python -c`. Consequencias, ditas:
#
#   + o arquivo continua sendo a UNICA fonte de verdade (nada de copia colada aqui);
#   + a task definition muda quando o script muda, entao o deploy e' rastreavel;
#   - o script viaja na task definition (~17 KB do limite de 64 KB), nao na imagem,
#     e portanto NAO tem o digest da imagem como proveniencia.
#
# Assim que houver build, troque `command` por
# `["python", "-m", "maezo.platform.testchannel"]` e apague o `file()`. O modulo ja
# vive em `src/maezo/`, entao entra no wheel sem force-include.
#
# SEGURANCA, sem rodeio: o canal NAO tem autenticacao propria e faz proxy de caminho
# arbitrario para o `engine-rest`. Quem abre a pagina dirige o motor. Isso e' aceitavel
# aqui SOMENTE porque o Access exige identidade antes, e e' a mesma exposicao que o
# Cockpit ja tem. A autorizacao continua sendo do engine — que segue com o usuario
# `demo` do CIB Seven ativo.

resource "aws_cloudwatch_log_group" "canal_teste" {
  name              = "/ecs/${local.name}/canal-teste"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_service_discovery_service" "canal_teste" {
  name = "canal-teste"

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

resource "aws_ecs_task_definition" "canal_teste" {
  family                   = "${local.name}-canal-teste"
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
    name      = "canal-teste"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    # `python -c <programa>` como UM argumento de argv: sem shell no meio, portanto sem
    # heredoc, sem escape de aspas e sem risco de o conteudo do script ser interpretado
    # pelo `sh`. O arquivo e' lido no plan.
    command = [
      "python",
      "-c",
      file("${path.module}/../../../../src/maezo/platform/testchannel/server.py"),
    ]

    portMappings = [{ containerPort = 8500, protocol = "tcp" }]

    environment = [
      { name = "ENGINE_REST_URL", value = local.cibseven_base_url },
      { name = "CANAL_PORT", value = "8500" },
      # 0.0.0.0 e' obrigatorio: ligado em 127.0.0.1 o container sobe, nada alcanca a
      # porta, e o sintoma e' um servico "saudavel" que nao responde.
      { name = "CANAL_BIND", value = "0.0.0.0" },
      # O link do Cockpit na pagina aponta para o hostname PUBLICO, nao para o DNS
      # interno — quem abre a pagina esta no navegador, fora da VPC.
      { name = "COCKPIT_URL", value = "https://${var.hostname_cockpit}" },
      # Ingresso do agente, pelo DNS do Cloud Map — o IP da task muda a cada deploy.
      # Vazio esconde o painel do agente na pagina em vez de deixar um botao que sempre
      # falha; e' o mesmo raciocinio do `desired_count` que descreve o que existe.
      { name = "AGENT_INGRESS_URL", value = local.agent_ingress_url },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ]

    readonlyRootFilesystem = true

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://localhost:8500/',timeout=3).status==200 else 1)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.canal_teste.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "canal"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "canal_teste" {
  name            = "canal-teste"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.canal_teste.arn
  desired_count   = var.canal_teste_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.canal_teste.arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}

# 8500 entre os componentes do maezo (o tunel alcanca o canal por aqui).
resource "aws_vpc_security_group_ingress_rule" "interno_8500" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Canal de Teste a partir do tunel"
  ip_protocol                  = "tcp"
  from_port                    = 8500
  to_port                      = 8500
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_egress_rule" "interno_8500" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Saida para o Canal de Teste"
  ip_protocol                  = "tcp"
  from_port                    = 8500
  to_port                      = 8500
  referenced_security_group_id = aws_security_group.tasks.id
}
