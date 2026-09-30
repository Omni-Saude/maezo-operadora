# Tarefa avulsa SEM SENHAS para a bateria de mensagens de teste (pedido da Diretoria de
# Tecnologia, 29/09/2026: temp/PEDIDO_TASK_BATERIA_SEM_SENHAS.md). Copia da `diagnostics` com UMA
# diferenca: o `entryPoint` e' so' o interpretador, e o `command` fica livre para o RunTask
# sobrescrever com o script da bateria (`["-c", "<codigo>"]`).
#
# O que ela NAO tem, de proposito: `task_role_arn` (o processo nao recebe identidade AWS),
# `secrets`, `environment`, volumes, porta de entrada, exec. A execution role e' propria e so'
# puxa a imagem e escreve no proprio grupo de log — nao herda task_execution_secrets nem
# master/bootstrap. Quem precisa de banco usa outra tarefa; esta nao serve para isso e e' assim
# que ela protege a senha (a `bootstrap-db` guarda 2 segredos e o ambiente ja' barrou o uso dela
# para rodar codigo).
#
# Rede (na chamada do RunTask, nao aqui): subnets privadas do env, SG `sg-0e2aebe1a2c1d253d` (o do
# canal-teste; as regras dele liberam 8500 e 8080 entre membros), assignPublicIp=DISABLED,
# --no-enable-execute-command, e as duas tags do permission set (MaezoPurpose, MaezoOwner=<STS
# UserId>). Destinos so' por DNS interno: canal-teste:8500, cibseven:8080/engine-rest,
# webhook-receiver:8080. Nada aqui cria token do Cloudflare Access nem abre porta publica.
#
# Imagem: o MESMO digest qualificado da sonda (`diagnostics_image_digest`), para nao existir uma
# segunda cadeia de qualificacao de imagem por causa de uma tarefa avulsa.

resource "aws_cloudwatch_log_group" "bateria" {
  name              = "/ecs/${local.name}/bateria"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_iam_role" "bateria_execution" {
  name               = "${local.name}-bateria-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "bateria_execution" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = [aws_ecr_repository.app.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.bateria.arn}:*"]
  }
}

resource "aws_iam_role_policy" "bateria_execution" {
  name   = "${local.name}-bateria-execution"
  role   = aws_iam_role.bateria_execution.id
  policy = data.aws_iam_policy_document.bateria_execution.json
}

resource "aws_ecs_task_definition" "bateria" {
  family                   = "${local.name}-bateria"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.bateria_execution.arn
  # Sem task_role_arn: e' a garantia de que o script nao tem credencial AWS nenhuma.

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "bateria"
    image     = "${aws_ecr_repository.app.repository_url}@${var.diagnostics_image_digest}"
    essential = true
    user      = "1000:1000"
    # `-I` (isolado): ignora PYTHON* do ambiente e nao poe o cwd no sys.path. O script vem no
    # `command` do RunTask. Sem override, a tarefa so' imprime como usa-la e sai com 2.
    entryPoint             = ["/usr/local/bin/python", "-I"]
    command                = ["-c", "import sys; print('bateria: passe o script em containerOverrides.command = [\"-c\", \"<codigo>\"]'); sys.exit(2)"]
    environment            = []
    secrets                = []
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { drop = ["ALL"] } }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.bateria.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "bateria"
      }
    }
  }])

  tags = local.base_tags
}
