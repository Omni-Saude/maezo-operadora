# Borda: tunel Cloudflare + Cloudflare Access. ZERO porta aberta.
#
# POR QUE ASSIM, E NAO UM ALB PUBLICO
#
# `austa.com.br` e' servido pelo Cloudflare (medido 18/08/2026: os NS do dominio sao
# `crystal.ns.cloudflare.com` / `austin.ns.cloudflare.com`), e o padrao da casa para
# borda e' tunel com admin atras do Access. Um ALB `internet-facing` exigiria
# certificado ACM, regra de SG de ENTRADA, WAF e um IP publico exposto — e mesmo
# assim a autenticacao ficaria por conta da aplicacao.
#
# Aqui a conexao e' de DENTRO PARA FORA: o `cloudflared` disca para a borda do
# Cloudflare e o trafego entra por dentro dessa conexao. Nao existe porta de entrada
# para escanear, e o Access decide QUEM chega antes de qualquer byte tocar o engine.
#
# A porta e' 7844, NAO 443 — e isto esta escrito porque eu errei: subi a borda
# confiando na regra de saida 443 que o SG ja tinha (ECR/Secrets/Bedrock) e o tunel
# NAO REGISTROU. O diagnostico do proprio cloudflared foi explicito:
#
#   UDP Connectivity  region1.v2.argotunnel.com  FAIL  QUIC connection failed
#   TCP Connectivity  region1.v2.argotunnel.com  FAIL  HTTP/2 blocked or unreachable
#   ERROR: Allow outbound QUIC traffic on port 7844 or use HTTP2.
#
# Dai as duas regras em `security_groups_cloudflared.tf`: UDP 7844 (QUIC, preferido) e
# TCP 7844 (fallback HTTP/2).

resource "aws_secretsmanager_secret" "cloudflared_token" {
  name        = "maezo/${local.env}/cloudflared/tunnel-token"
  description = "Token do tunel Cloudflare (borda do maezo). Valor populado fora do Terraform."

  recovery_window_in_days = 30 # prod: janela cheia (no dev e' 7 para recriar rapido)

  tags = merge(local.base_tags, { Name = "maezo-${local.env}-cloudflared-tunnel-token" })
}

data "aws_iam_policy_document" "task_execution_cloudflared_secret" {
  statement {
    sid       = "LerTokenDoTunel"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.cloudflared_token.arn]
  }
}

resource "aws_iam_role_policy" "task_execution_cloudflared_secret" {
  name   = "${local.name}-cloudflared-secret"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_cloudflared_secret.json
}

resource "aws_cloudwatch_log_group" "cloudflared" {
  name              = "/ecs/${local.name}/cloudflared"
  retention_in_days = var.log_retention_infra_days
  kms_key_id        = local.logs_kms_key_arn
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "cloudflared" {
  family                   = "${local.name}-cloudflared"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "ARM64" # imagem multi-arch (indice amd64+arm64 medido em 05/11/2026)
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name = "cloudflared"
    # Tag FIXA, nao `latest`: a borda e' o componente onde uma troca silenciosa de
    # versao e' menos aceitavel. 2026.8.2 e' a mais recente publicada (Docker Hub,
    # consultado em 18/08/2026).
    image     = "cloudflare/cloudflared@${var.cloudflared_image_digest}"
    essential = true

    # `run` sem argumento de tunel: o `cloudflared` le TUNNEL_TOKEN do ambiente e o
    # token ja identifica o tunel. `--no-autoupdate` porque quem decide a versao e' a
    # task definition, nao o binario se atualizando sozinho em producao.
    command = ["tunnel", "--no-autoupdate", "run"]

    environment = [
      # Metricas locais (nao expostas): dao um /ready para diagnostico via ECS Exec.
      { name = "TUNNEL_METRICS", value = "0.0.0.0:2000" },
      # Log em JSON nao ajuda aqui — este container e' lido por humano em incidente.
      { name = "TUNNEL_LOGLEVEL", value = "info" },
    ]

    secrets = [
      { name = "TUNNEL_TOKEN", valueFrom = "${aws_secretsmanager_secret.cloudflared_token.arn}:token::" },
    ]

    readonlyRootFilesystem = true

    # Sem healthCheck de container de proposito: a imagem do cloudflared nao traz
    # shell nem curl, e um healthCheck que nao pode rodar marca a task como unhealthy
    # para sempre — falha que parece de rede e e' de ferramenta ausente (foi o que
    # quase aconteceu com o worker). A saude real do tunel aparece no painel do
    # Cloudflare e no log desta task.

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.cloudflared.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "cloudflared"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "cloudflared" {
  name            = "cloudflared"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.cloudflared.arn
  desired_count   = var.servicos_habilitados && var.borda_publica_habilitada ? var.cloudflared_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.ecs_exec_habilitado # prod: desligado por padrao (break-glass por PR)

  network_configuration {
    # PROD: SG PROPRIO (security_groups_cloudflared.tf), sem o SG das tasks e sem o
    # marcador do Aurora. Nenhuma regra de ENTRADA — a borda nao tem porta.
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.cloudflared.id]
    assign_public_ip = false
  }

  # Duas replicas do tunel dariam alta disponibilidade de borda (o Cloudflare
  # balanceia entre conexoes do mesmo tunel). Em dev, uma basta — e o custo de uma
  # borda caida aqui e' um teste interrompido, nao atendimento parado.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}
