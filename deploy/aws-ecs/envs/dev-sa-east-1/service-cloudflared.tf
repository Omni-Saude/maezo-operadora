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
# Aqui a conexao e' de DENTRO PARA FORA: o `cloudflared` abre 443 de saida (regra que
# este SG ja tem, para ECR/Secrets/Bedrock) e o Cloudflare entrega o trafego por
# dentro dela. Nao existe porta de entrada para escanear, e o Access decide QUEM
# chega antes de qualquer byte tocar o engine.
#
# O QUE FICA EXPOSTO
#
# O hostname publico e o destino interno sao configurados NO PAINEL do Cloudflare
# (tunel gerenciado remotamente), nao aqui. O destino pretendido e' o Cockpit/Tasklist
# do CIB Seven:
#
#     https://<subdominio>.austa.com.br  ->  http://cibseven.maezo-operadora-dev.internal:8080
#
# E' onde o medico auditor decide a tarefa `UT_AnaliseMedicoAuditor` de verdade, em
# vez de alguem completar por REST.
#
# ATENCAO, PENDENCIA DE SEGURANCA REAL (nao resolvida por este arquivo)
#
# A imagem `cibseven/cibseven:2.1.0` cria dados de demonstracao no boot
# (`DemoDataGenerator.createUsers` aparece no log da nossa task) — o que inclui o
# usuario `demo`. Enquanto ele existir, QUEM PASSAR PELO ACCESS tem administracao do
# engine. Em dev tecnico isso e' aceitavel SE for dito em voz alta, e esta dito aqui.
# Antes de qualquer uso com dado real: remover/trocar o usuario demo e a app de
# exemplo `invoice`. O Access e' a fronteira de identidade, nao a de autorizacao.
#
# COMO ATIVAR (nada disto e' feito por codigo, de proposito)
#
#   1. Cloudflare Zero Trust -> Networks -> Tunnels -> Create a tunnel (tipo
#      "Cloudflared"). Copiar o TOKEN.
#   2. No tunel, Public Hostname: subdominio de `austa.com.br`, servico
#      `HTTP` -> `cibseven.maezo-operadora-dev.internal:8080`.
#   3. Zero Trust -> Access -> Applications: criar a aplicacao para esse hostname e
#      a politica (ex.: e-mails terminando em `@austa.com.br`, ou lista nominal).
#      SEM ESTA ETAPA o hostname fica publico — o tunel entrega, o Access filtra.
#      Um sem o outro nao e' zero trust.
#   4. Gravar o token no segredo criado abaixo:
#        aws secretsmanager put-secret-value \
#          --secret-id maezo/dev/cloudflared/tunnel-token \
#          --secret-string file://token.json --region sa-east-1
#      formato: {"token":"<TOKEN>"}   (use ARQUIVO — JSON inline pelo PowerShell
#      perde as aspas e o ECS rejeita com "invalid character")
#   5. `terraform apply -var=cloudflared_desired_count=1`
#
# O token e' credencial de borda: quem o tem publica hostname na sua zona. Por isso
# ele NAO passa pelo Terraform e nao vive em variavel — mesma postura das outras.

resource "aws_secretsmanager_secret" "cloudflared_token" {
  name        = "maezo/${local.env}/cloudflared/tunnel-token"
  description = "Token do tunel Cloudflare (borda do maezo). Valor populado fora do Terraform."

  recovery_window_in_days = 7

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
  retention_in_days = var.log_retention_days
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
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name = "cloudflared"
    # Tag FIXA, nao `latest`: a borda e' o componente onde uma troca silenciosa de
    # versao e' menos aceitavel. 2026.8.2 e' a mais recente publicada (Docker Hub,
    # consultado em 18/08/2026).
    image     = "cloudflare/cloudflared:${var.cloudflared_image_tag}"
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
  desired_count   = var.cloudflared_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    # Mesmo SG dos demais componentes: a regra de auto-referencia na 8080 ja permite
    # que o tunel alcance o engine, e a saida 443 ja existe. Nenhuma regra de ENTRADA
    # e' criada — a borda nao tem porta.
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
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
