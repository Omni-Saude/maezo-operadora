# Bootstrap de identidade do engine: cria o administrador REAL e remove o legado de
# demonstracao.
#
# POR QUE E' UMA TASK E NAO UM COMANDO SOLTO
#
# Isto e' destrutivo (apaga usuarios, grupos e deployments) e sera' necessario de novo
# num restore. Um comando no historico do terminal de alguem nao sobrevive a isso.
#
# ORDEM QUE IMPORTA: cria o admin ANTES de apagar o `demo`. O `demo` e' o unico membro
# de `camunda-admin` hoje (medido); apagar primeiro deixaria o Cockpit sem nenhum
# administrador, e o webapp cairia na tela de "criar o primeiro usuario" — que, atras do
# Access, entrega administracao do motor a primeira pessoa que abrir a pagina.
#
# DRY RUN POR PADRAO: sem `CONFIRMAR=1` a task apenas LISTA o que faria. Uma task que
# apaga por padrao e' uma task que alguem roda por engano.
resource "aws_cloudwatch_log_group" "engine_bootstrap" {
  name              = "/ecs/${local.name}/engine-bootstrap"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "engine_bootstrap" {
  family                   = "${local.name}-engine-bootstrap"
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
    name      = "engine-bootstrap"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    command = ["python", "-m", "maezo.platform.engine_bootstrap"]

    environment = [
      { name = "ENGINE_REST_URL", value = local.cibseven_base_url },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
      # Vazio = dry run. A task de execucao real passa CONFIRMAR=1 por override.
      { name = "CONFIRMAR", value = "" },
      # O deployment que NAO pode ser apagado em nenhuma hipotese.
      { name = "DEPLOYMENT_PRESERVAR", value = "maezo-spec-processes" },
    ]

    secrets = [
      { name = "ADMIN_USER", valueFrom = "${aws_secretsmanager_secret.engine_admin.arn}:usuario::" },
      { name = "ADMIN_PASSWORD", valueFrom = "${aws_secretsmanager_secret.engine_admin.arn}:senha::" },
    ]

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.engine_bootstrap.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "bootstrap"
      }
    }
  }])

  tags = local.base_tags
}
