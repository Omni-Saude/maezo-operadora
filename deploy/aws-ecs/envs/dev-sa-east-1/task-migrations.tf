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

    # `-x tenant=` e' a fonte de tenant de MAIOR precedencia no env.py (o docstring
    # dele manda usar exatamente isso). MAEZO_TENANT_ID vai junto como rede de
    # seguranca. Atencao: NAO e' `MAEZO_TENANT` — esse nome, que o comentario do
    # job-migrations.yaml do chart cita, nao existe no codigo; usa-lo faria o
    # search_path cair silenciosamente no schema `public`, e as tabelas do tenant
    # nasceriam no lugar errado sem erro nenhum.
    command = ["sh", "-c", "set -eu\n${local.dsn_export_alembic}\nexec python -m alembic -x tenant=${var.tenant_id} upgrade head"]

    environment = concat(local.db_env, [
      # asyncpg, nao psycopg: run_async_migrations usa async_engine_from_config, e
      # uma URL sincrona falha com "The asyncio extension requires an async driver".
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "MAEZO_TENANT_ID", value = var.tenant_id },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = local.db_secrets_maezo

    # Raiz somente-leitura E sem volume: com o DSN vindo de substituicao de comando
    # nao ha o que escrever. Um volume de task nasceria root:root e o container roda
    # como uid 1000 — foi exatamente assim que a primeira execucao falhou.
    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.migrations.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  }])

  tags = local.base_tags
}
