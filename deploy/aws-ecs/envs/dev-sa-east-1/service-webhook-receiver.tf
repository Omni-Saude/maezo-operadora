# Receptor de webhook do WhatsApp — a peca que EXECUTA a Helena.
#
# POR QUE ESTE SERVICO EXISTE, E POR QUE SO' AGORA
#
# `agent-helena` esta' no ar desde 13/08 e NAO executa turno: o proprio runtime diz
# `agent_graph_execution_not_performed_here` — para a Helena, o turno e' conduzido pelo
# receptor de webhook, que recebe a mensagem da Meta, valida a assinatura HMAC, deduplica por
# `wamid` e despacha o grafo EM PROCESSO (DMN de red flag + start auditado de
# SP-OP-ESCALATION-001). O receptor existia no Helm (`deployment-webhook-receiver.yaml`) e no
# compose desde T1.6, e nunca foi implantado aqui. Medido em 09/09/2026: 8 servicos no cluster,
# nenhum deles o receptor. Sem ele, os passos 2, 4, 5 e 12 do fluxo de triagem sao
# intestaveis — e' a menor peca que destrava o maior pedaco.
#
# O QUE ISTO NAO E'. Nao e' a integracao com a Meta. Nao ha conta WhatsApp Business, nao ha
# numero aprovado, nao ha app. O segredo que este servico le (`whatsapp_meta`) e' ALEATORIO,
# gerado por nos, e serve para receber mensagem SIMULADA assinada com o mesmo segredo —
# exercitando a Helena de ponta a ponta sem depender da Meta. A URL publica do receptor NAO
# deve ser cadastrada na Meta enquanto o segredo for o nosso.
#
# IMAGEM SEPARADA (`var.webhook_receiver_image_tag`), e o motivo esta' na variavel: o receptor
# mudou 1.593 linhas entre a imagem dos agentes e a `main` de 09/09.
#
# ZONA: geral (mesma da Helena — `maezo.io/phi-zone: general` no Helm). O texto que chega ja'
# vem pseudonimizado pelo proprio receptor (PHI_HMAC_KEY), e a inferencia e' o provedor da
# zona geral (`var.inference_provider`).

resource "aws_cloudwatch_log_group" "webhook_receiver" {
  name              = "/ecs/${local.name}/webhook-receiver"
  retention_in_days = var.log_retention_days
  tags              = merge(local.base_tags, { zona = "general" })
}

# DNS interno estavel: e' por este nome que o tunel (e a bateria de teste) alcancam o receptor.
resource "aws_service_discovery_service" "webhook_receiver" {
  name = "webhook-receiver"

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

resource "aws_ecs_task_definition" "webhook_receiver" {
  family                   = "${local.name}-webhook-receiver"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.agent_cpu)
  memory                   = tostring(var.agent_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "webhook-receiver"
    image     = "${aws_ecr_repository.app.repository_url}:${var.webhook_receiver_image_tag}"
    essential = true

    # Mesmo entrypoint do Helm e do compose. O DSN e' montado do mesmo jeito que nos agentes:
    # o despacho da Helena exige DATABASE_URL (fail-closed — sem ele o /webhook responde 501).
    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.platform.webhooks"]

    portMappings = [{ containerPort = 8080, protocol = "tcp" }]

    # O bloco de ambiente ESPELHA o do `agent-helena` em `service-agents.tf` — porque o grafo
    # que roda aqui e' o dela. Divergir os dois e' a forma classica de a Helena "funcionar no
    # receptor e nao no agente" (ou o inverso) sem que ninguem saiba por que.
    environment = concat(local.db_env, [
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      { name = "AGENT_ID", value = "helena" },
      { name = "AGENT_SECURITY_ZONE", value = local.agentes.helena.zona },
      { name = "CIBSEVEN_BASE_URL", value = local.cibseven_base_url },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = local.kafka_bootstrap },
      { name = "HEALTH_PORT", value = "8080" },
      { name = "RUNTIME_MODE", value = var.agent_runtime_mode },
      { name = "AGENT_RUNTIME_MODE", value = var.agent_runtime_mode },
      { name = "MAEZO_INFERENCE_PROVIDER", value = local.agentes.helena.provider },
      { name = "MAEZO_INFERENCE_PHI_ZONE_REQUIRED", value = tostring(local.agentes.helena.phi_zone_required) },
      { name = "MAEZO_BEDROCK_MODEL_ID", value = var.bedrock_model_id },
      { name = "MAEZO_BEDROCK_REGION", value = var.aws_region },
      { name = "MAEZO_DOSSIER_NARRATIVE_GENERAL_ZONE_SYNTHETIC_ONLY", value = var.caso_sintetico_zona_geral ? "1" : "0" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = concat(local.db_secrets_maezo, [
      # Sem esta o Pseudonymizer falha FECHADO (ADR-0035) — e o receptor e' quem pseudonimiza
      # o numero e o wamid antes de qualquer coisa tocar o texto.
      { name = "PHI_HMAC_KEY", valueFrom = "${aws_secretsmanager_secret.phi_hmac_key.arn}:key::" },
      { name = "MAEZO_A2A_CARD_SIGNING_KEY__${upper(var.tenant_id)}", valueFrom = "${aws_secretsmanager_secret.a2a_card_signing_key.arn}:key::" },
      # As duas que o receptor EXIGE no boot (settings.py: sem default, fail-closed).
      { name = "WHATSAPP_APP_SECRET", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:app_secret::" },
      { name = "WHATSAPP_VERIFY_TOKEN", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:verify_token::" },
      # O mesmo processo envia a resposta; ambas as configuracoes sao obrigatorias no envio.
      { name = "WHATSAPP_PHONE_NUMBER_ID", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:phone_number_id::" },
      { name = "WHATSAPP_TOKEN", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:waba_token::" },
    ])

    readonlyRootFilesystem = true

    healthCheck = {
      # python, nao curl: a imagem e' python:3.12-slim. Mesmo /healthz do compose.
      command     = ["CMD-SHELL", "python -c \"import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://localhost:8080/healthz',timeout=3).status==200 else 1)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.webhook_receiver.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "webhook"
      }
    }
  }])

  tags = merge(local.base_tags, { zona = "general" })
}

resource "aws_ecs_service" "webhook_receiver" {
  name            = "webhook-receiver"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.webhook_receiver.arn
  desired_count   = var.webhook_receiver_desired_count
  launch_type     = "FARGATE"

  # ECS Exec exige escrita no filesystem raiz; preserve o container somente leitura.
  enable_execute_command = false

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.webhook_receiver.arn
  }

  # Rollback automatico se a task nao ficar saudavel — foi o que salvou o ambiente quando o
  # canal de teste subiu quebrado em 19/08.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = merge(local.base_tags, { zona = "general" })
}

# Porta 8080 entre componentes do maezo: JA' EXISTE em `security_groups.tf` (e' a porta do
# engine, que agentes e worker alcancam). O receptor escuta na mesma porta e herda a regra;
# declarar outra aqui duplicaria o par ingress/egress e o apply falharia por regra repetida.
