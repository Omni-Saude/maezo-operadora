# Migrations — task AVULSA, nao service.
#
# No chart isto era um Helm hook `pre-install,pre-upgrade`, e essa escolha criou o
# defeito DB-7: o hook rodava ANTES do ExternalSecret existir, o pod morria em
# CreateContainerConfigError e o `--atomic` derrubava o release inteiro. Dai o
# deploy em duas fases documentado no cd.yml.
#
# Em ECS o problema simplesmente nao existe: `run-task` e' um passo explicito do
# runbook/CD, executado quando o operador (ou o pipeline) manda, com o segredo ja
# resolvido pela execution role. Roda, sai com codigo 0 ou 1, e o log fica. Nao ha
# ordenacao implicita para dar errado.
#
# Comando: alembic exige DSN SINCRONO (psycopg). O env.py le ALEMBIC_DATABASE_URL e
# MAEZO_TENANT — nao DATABASE_URL/TENANT_ID (ver job-migrations.yaml:8-12).

resource "aws_ecs_task_definition" "migrations" {
  family                   = "${local.name}-migrations"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    # A imagem e' construida em runner amd64 (GitHub Actions ubuntu-latest e o
    # docker build local no Windows). ARM64 exigiria buildx com emulacao.
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "migrate"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    command = ["sh", "-c", "${local.dsn_builder}\nexec python -m alembic upgrade head"]

    environment = concat(local.db_env, [
      { name = "DSN_VAR", value = "ALEMBIC_DATABASE_URL" },
      { name = "DSN_SCHEME", value = "postgresql+psycopg" },
      { name = "MAEZO_TENANT", value = var.tenant_id },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = local.db_secrets_maezo

    readonlyRootFilesystem = true

    mountPoints = [{
      sourceVolume  = "tmp"
      containerPath = "/tmp"
      readOnly      = false
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.migrations.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  }])

  # Raiz somente-leitura exige um volume gravavel para /tmp (o DSN composto e os
  # temporarios do alembic moram la).
  volume {
    name = "tmp"
  }

  tags = local.base_tags
}
