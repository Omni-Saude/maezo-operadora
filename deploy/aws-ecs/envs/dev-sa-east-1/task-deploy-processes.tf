# Deploy dos processos no engine — 16 BPMN + 62 DMN.
#
# Sem isto o engine esta VAZIO: ele sobe, responde /engine-rest/version e nao
# executa processo nenhum, porque nao conhece nenhum. Foi o passo que faltava no
# meu plano inicial, e ele nao e' opcional — e' o que transforma "o motor esta de
# pe" em "a plataforma existe".
#
# Task avulsa, como as migrations: roda, sai, deixa log. O nome do deployment e'
# ESTAVEL (`maezo-spec-processes`) porque o engine filtra duplicata por nome —
# mudar o nome derrota a idempotencia e cria versao nova de tudo a cada execucao.
#
# `--spec-dir /opt/maezo/spec` e' o override sancionado pela Wave-1 Q-6 (o CLI
# documenta isso no proprio --help). Os artefatos vivem fora de /app de proposito;
# ver o comentario no deploy/Dockerfile.

resource "aws_cloudwatch_log_group" "deploy_processes" {
  name              = "/ecs/${local.name}/deploy-processes"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "deploy_processes" {
  family                   = "${local.name}-deploy-processes"
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
    name      = "deploy-processes"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    command = [
      "python", "-m", "maezo.platform.deploy",
      "--spec-dir", "/opt/maezo/spec",
      "--deployment-name", "maezo-spec-processes",
    ]

    environment = [
      # O CLI le ENGINE_REST_URL (nao CIBSEVEN_BASE_URL — sao variaveis diferentes
      # para consumidores diferentes; confundir as duas faz o deploy apontar para
      # localhost e falhar sem dizer por que).
      { name = "ENGINE_REST_URL", value = local.cibseven_base_url },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ]

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.deploy_processes.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "deploy"
      }
    }
  }])

  tags = local.base_tags
}
