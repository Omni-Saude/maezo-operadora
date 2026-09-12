# ADR-0006/0007/0049; sucessor PR357 F1-F4. Diagnostico de conectividade,
# nao acesso a banco, Kafka, engine REST ou shell. A task nao recebe identidade
# runtime, secrets, volumes ou entrada de rede. O ECS so' permite substituir
# command, nao entryPoint: somente o argumento fixo probe e' aceito; environment nao e' lido.
#
# Antes de RunTask, o operador de deploy qualifica o digest da imagem Python,
# provisiona esta definicao e passa subnets privadas + SG de tasks autorizado,
# assignPublicIp=DISABLED, --no-enable-execute-command e as duas tags exigidas
# pelo permission set. RunTask nao oferece condicoes IAM para subnet/SG/public IP;
# esta fonte NAO afirma que IAM valida esses parametros ou que ja houve apply.
# As sondas emitem somente label/estado, sem enviar nem receber payload de negocio.
variable "diagnostics_image_digest" {
  description = "Digest sha256 completo da imagem ECR app revisada para a sonda DNS/TCP; obrigatorio no deploy, sem tag mutavel ou digest inventado."
  type        = string
  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.diagnostics_image_digest))
    error_message = "Forneca o digest sha256 completo de uma imagem app qualificada."
  }
}

locals {
  diagnostics_probe = <<-PY
    import os
    import sys
    os.environ.clear()
    import json
    import signal
    import socket

    def deadline(signum, frame):
        print(json.dumps({"status": "timeout"}), flush=True)
        raise SystemExit(1)

    if sys.argv != ["-c", "probe"]:
        print(json.dumps({"status": "arguments_rejected"}), flush=True)
        raise SystemExit(2)
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(20)
    failed = False
    for label, host, port in (
        ("engine", "cibseven.${local.name}.internal", 8080),
        ("broker", "kafka.${local.name}.internal", 9092),
    ):
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
            status = "reachable"
        except OSError:
            failed = True
            status = "unreachable"
        print(json.dumps({"target": label, "dns_tcp": status}), flush=True)
    signal.alarm(0)
    raise SystemExit(1 if failed else 0)
  PY
}

resource "aws_cloudwatch_log_group" "diagnostics" {
  name              = "/ecs/${local.name}/diagnostics"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_iam_role" "diagnostics_execution" {
  name               = "${local.name}-diagnostics-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

# Politica propria: nao herda task_execution_secrets, master/bootstrap, SSM,
# Bedrock nem managed policies futuras. A execution role nao e' credencial do
# processo; nao ha task_role_arn na definicao abaixo.
data "aws_iam_policy_document" "diagnostics_execution" {
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
    resources = ["${aws_cloudwatch_log_group.diagnostics.arn}:*"]
  }
}

resource "aws_iam_role_policy" "diagnostics_execution" {
  name   = "${local.name}-diagnostics-execution"
  role   = aws_iam_role.diagnostics_execution.id
  policy = data.aws_iam_policy_document.diagnostics_execution.json
}

resource "aws_ecs_task_definition" "diagnostics" {
  family                   = "${local.name}-diagnostics"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.diagnostics_execution.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name                   = "diagnostics"
    image                  = "${aws_ecr_repository.app.repository_url}@${var.diagnostics_image_digest}"
    essential              = true
    user                   = "1000:1000"
    entryPoint             = ["/usr/local/bin/python", "-I", "-S", "-c", local.diagnostics_probe]
    command                = ["probe"]
    environment            = []
    secrets                = []
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { drop = ["ALL"] } }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.diagnostics.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "diagnostics"
      }
    }
  }])

  # Nunca propagar MaezoOwner de uma definicao/service. O valor de ownership vem
  # do request RunTask autenticado e e' verificado contra aws:userid pelo IAM.
  tags = local.base_tags
}
