# Consumidor de retomada (`agent_resume`) — quem ESCUTA `agents.events.process_completed`.
#
# O DEFEITO (GAP-XHITL-4, spec da diretoria de 24/09/2026): quando o humano conclui a tarefa de
# SP-OP-ESCALATION-001 como `devolvido_agente`, o motor publica o evento e NINGUEM escuta. A
# conversa no WhatsApp nunca e' retomada e o beneficiario nao recebe a instrucao que o humano
# escreveu. O modulo esta' em `platform/integrations/agent_resume.py` (mesma forma do
# `notifications_bridge`: consumer + fila morta `<topic>.dlq` + falha fechada).
#
# POR QUE UM SERVICO PROPRIO, e nao dentro do `worker-runtime` nem do receptor de webhook:
#   * o `worker-runtime` e' o executor de external tasks do MOTOR. Ele nao tem nada do que uma
#     retomada precisa — inferencia da Helena, checkpointer duravel das conversas, cliente e
#     segredos do WhatsApp, PHI_HMAC_KEY — e colocar envio ao beneficiario ali alargaria o raio
#     de falha e os segredos do processo que move TODOS os processos;
#   * o receptor de webhook tem todos esses deps, mas e' a porta de entrada da Meta: um consumer
#     Kafka preso num evento (motor fora, envio falho — o offset nao anda, de proposito) nao pode
#     disputar o mesmo processo com a latencia do webhook, e escalar um nao deve escalar o outro;
#   * um servico proprio liga e desliga sozinho (`agent_resume_desired_count`), como a ponte.
# A composicao da Helena e' a MESMA do receptor (`webhooks.service._build_dispatcher`), por isso a
# imagem, o ambiente e os segredos abaixo espelham `service-webhook-receiver.tf`. O codigo
# funciona igual se um dia for hospedado no receptor: `run_resume_loop` nao sabe onde roda.
#
# DESLIGADO (desired_count = 0) ATE' A CUSTODIA DO ADR-0061 SER LIGADA (`recipient_vault.tf`). O WhatsApp exige o numero e
# v2 guarda so' o hash keyed do telefone (ADR-0006/ADR-0035). Sem essa decisao do dono/DPO,
# `main()` RECUSA SUBIR (`RecipientCustodyUnavailableError`) — com 1 replica o ECS so' veria a
# task morrer. Por isso o default e' 0 — e com 0 NENHUM recurso deste arquivo existe (`count`),
# entao o `terraform plan` do ambiente continua sem mudanca ate' alguem ligar de proposito.
#
# Zona: le `notas_resolucao` (texto livre humano, Zona PHI) do historico do motor e a manda ao
# beneficiario. Mesma zona declarada do receptor da Helena neste staging tecnico (sem dado real
# de paciente — `main.tf`); a mudanca de zona acompanha a do receptor.

locals {
  agent_resume_ativo = var.agent_resume_desired_count > 0 ? 1 : 0
}

resource "aws_cloudwatch_log_group" "agent_resume" {
  count             = local.agent_resume_ativo
  name              = "/ecs/${local.name}/agent-resume"
  retention_in_days = var.log_retention_days
  tags              = merge(local.base_tags, { zona = "general" })
}

resource "aws_ecs_task_definition" "agent_resume" {
  count                    = local.agent_resume_ativo
  family                   = "${local.name}-agent-resume"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.agent_cpu)
  memory                   = tostring(var.agent_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  # ADR-0061: role propria, SO' kms:Decrypt na chave da custodia.
  task_role_arn = local.agent_resume_task_role_arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name = "agent-resume"
    # A imagem do receptor: e' a que tem a Helena, o webhook e os modulos de integracao.
    image     = "${aws_ecr_repository.app.repository_url}:${var.webhook_receiver_image_tag}"
    essential = true

    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.platform.integrations.agent_resume"]

    # Mesmo ambiente do receptor (a Helena e' montada pela mesma raiz). Topico e consumer group
    # ficam nos defaults do modulo (`PROCESS_COMPLETED_TOPIC`, `DEFAULT_RESUME_CONSUMER_GROUP_ID`):
    # sao contrato do codigo, nao configuracao de ambiente — como na ponte.
    environment = concat(local.db_env, [
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      { name = "AGENT_ID", value = "helena" },
      { name = "AGENT_SECURITY_ZONE", value = local.agentes.helena.zona },
      { name = "CIBSEVEN_BASE_URL", value = local.cibseven_base_url },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = local.kafka_bootstrap },
      { name = "RUNTIME_MODE", value = var.agent_runtime_mode },
      { name = "AGENT_RUNTIME_MODE", value = var.agent_runtime_mode },
      { name = "MAEZO_INFERENCE_PROVIDER", value = local.agentes.helena.provider },
      { name = "MAEZO_INFERENCE_PHI_ZONE_REQUIRED", value = tostring(local.agentes.helena.phi_zone_required) },
      { name = "MAEZO_PHI_ENDPOINT_URL", value = var.phi_endpoint_url },
      { name = "MAEZO_PHI_VENDOR_DPA_REF", value = var.phi_vendor_dpa_ref },
      { name = "MAEZO_INFERENCE_MODEL", value = local.agentes.helena.provider == "bedrock_br" || local.agentes.helena.provider == "br_resident" ? var.phi_model_id : "" },
      { name = "MAEZO_BEDROCK_MODEL_ID", value = var.bedrock_model_id },
      { name = "MAEZO_BEDROCK_REGION", value = var.aws_region },
      { name = "WHATSAPP_WEBHOOK_MEMORIA_CLINICA", value = var.helena_memoria_clinica ? "true" : "false" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ], local.recipient_vault_env)

    # WHATSAPP_APP_SECRET/VERIFY_TOKEN nao sao usados pela retomada, mas sao campos OBRIGATORIOS
    # de `WhatsAppWebhookSettings` (a raiz compartilhada): sem eles a task nao sobe.
    secrets = concat(local.db_secrets_maezo, [
      { name = "PHI_HMAC_KEY", valueFrom = "${aws_secretsmanager_secret.phi_hmac_key.arn}:key::" },
      { name = "MAEZO_A2A_CARD_SIGNING_KEY__${upper(var.tenant_id)}", valueFrom = "${aws_secretsmanager_secret.a2a_card_signing_key.arn}:key::" },
      { name = "WHATSAPP_APP_SECRET", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:app_secret::" },
      { name = "WHATSAPP_VERIFY_TOKEN", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:verify_token::" },
      { name = "WHATSAPP_PHONE_NUMBER_ID", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:phone_number_id::" },
      { name = "WHATSAPP_TOKEN", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:waba_token::" },
    ])

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.agent_resume[0].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "agent-resume"
      }
    }
  }])

  tags = merge(local.base_tags, { zona = "general" })
}

resource "aws_ecs_service" "agent_resume" {
  count           = local.agent_resume_ativo
  name            = "agent-resume"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.agent_resume[0].arn
  desired_count   = var.agent_resume_desired_count
  launch_type     = "FARGATE"

  # ECS Exec exige root gravavel; o container e' somente leitura (mesma decisao do receptor).
  enable_execute_command = false

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = merge(local.base_tags, { zona = "general" })
}
