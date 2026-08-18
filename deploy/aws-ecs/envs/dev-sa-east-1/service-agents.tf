# Agentes — um service POR AGENTE, mesma imagem, AGENT_ID diferente.
#
# Por que nao um service com 3 replicas: cada agente tem identidade, zona de
# seguranca e definicao propria. Uma replica nao e' intercambiavel com a outra.
#
# ZONA GERAL vs ZONA PHI — a divisao que decide o que sobe aqui:
#
#   helena  = zona GERAL. Nao ve dado de paciente. Sobe.
#   rafael  = zona PHI.  NAO sobe (desired_count 0).
#   marina  = zona PHI.  NAO sobe (desired_count 0).
#
# O motivo nao e' tecnico e nao e' meu para resolver: o modelo default e'
# `global.anthropic.claude-opus-5`, e um perfil `global.*` ROTEIA ENTRE REGIOES
# por desenho (src/maezo/runtime/inference.py:485). Mandar PHI por ele significa
# inferencia possivelmente executada fora do Brasil — o que exige o DPA do
# endpoint BR-resident (docs/Tarefas_Pendentes.md §3.1, item 🔴 de juridico/DPO).
#
# Subir rafael e marina apontando para o perfil global "para testar" seria criar
# exatamente o precedente que a zona PHI existe para impedir. Eles ficam em zero,
# com a task definition PRONTA: no dia em que o endpoint BR-resident e o DPA
# existirem, e' mudar uma variavel, nao escrever codigo.

# ---------------------------------------------------------------------------
# O MESMO PORTAO DO RUNTIME, APLICADO NA INFRA
# ---------------------------------------------------------------------------
# `platform/privacy/dossier_zone.py` decide em tempo de execucao se a narrativa do
# dossie e' zona PHI ou zona geral, lendo o artefato ratificado. Este bloco le O MESMO
# ARQUIVO e decide qual provedor a task definition recebe.
#
# Por que duplicar a leitura em vez de confiar so' no runtime: sem isto, alguem
# poderia apontar rafael para `bedrock` por variavel de Terraform. O runtime ainda
# recusaria a chamada (`phi=True` + `phi_allowed=False`) — mas o `except Exception` do
# call site ENGOLE a recusa e a narrativa sai VAZIA, em silencio. Ou seja: o modo de
# falha de configurar errado nao e' um erro visivel, e' um dossie pior sem aviso.
# Portanto a infra tambem tem de recusar.
#
# O digest e' conferido aqui pelo mesmo motivo que o loader confere: uma ratificacao
# assinada sobre um prompt nao vale para outro prompt.
locals {
  _ratificacao_arquivo = "${path.module}/../../../../spec/policies/phi/dossier-narrative-zone.yaml"
  _grafo_rafael        = "${path.module}/../../../../src/maezo/agents/rafael/graph.py"

  # `try` porque um artefato ausente ou malformado deve resultar em ZONA PHI, nunca em
  # erro de plan que alguem contorna comentando a linha.
  _ratificacao = try(yamldecode(file(local._ratificacao_arquivo)), {})

  _digest_atual    = filesha256(local._grafo_rafael)
  _digest_assinado = lower(trimspace(try(tostring(local._ratificacao.graph_sha256), "")))
  _digest_confere  = local._digest_assinado == local._digest_atual

  # Os quatro requisitos, na mesma ordem do loader Python.
  narrativa_geral_ratificada = (
    try(local._ratificacao.ratificado, false) == true &&
    upper(trimspace(try(tostring(local._ratificacao.zona_declarada), ""))) == "GERAL" &&
    upper(trimspace(try(tostring(local._ratificacao.dpo_review), ""))) == "APPROVED" &&
    upper(trimspace(try(tostring(local._ratificacao.medico_auditor_review), ""))) == "APPROVED" &&
    local._digest_confere
  )

  # Provedor da zona PHI enquanto nao houver ratificacao; provedor real depois dela.
  _provider_phi = local.narrativa_geral_ratificada ? var.inference_provider : var.phi_zone_provider
}

# Diagnostico honesto no plan: diz POR QUE o regime e' o que e', em vez de deixar
# alguem adivinhar se esqueceu de assinar ou se assinou errado.
check "ratificacao_da_zona_da_narrativa" {
  assert {
    condition     = local.narrativa_geral_ratificada || !local._digest_confere || try(local._ratificacao.ratificado, false) != true
    error_message = "Artefato marcado como ratificado, digest confere, mas uma das revisoes (dpo_review / medico_auditor_review) nao esta APPROVED - a narrativa segue na zona PHI."
  }

  assert {
    condition     = local._digest_assinado == "" || local._digest_confere
    error_message = "A ratificacao carimbou graph_sha256=${local._digest_assinado} mas graph.py tem ${local._digest_atual} - o prompt mudou depois da assinatura. Reveja e re-assine; a narrativa segue na zona PHI."
  }
}

locals {
  # Zona, provedor de inferencia e replicas por agente.
  #
  # O provedor NAO e' o mesmo para todos, e essa e' a decisao central deste arquivo:
  # a zona de seguranca do agente escolhe quem serve a inferencia dele.
  agentes = {
    helena = {
      zona              = "general"
      replicas          = 1
      provider          = var.inference_provider # bedrock: modelo real
      phi_zone_required = false
      motivo            = "zona geral — sem PHI, inferencia real via Bedrock"
    }
    rafael = {
      zona     = "phi"
      replicas = 1
      # Derivado da ratificacao, nao escolhido a mao.
      provider          = local._provider_phi
      phi_zone_required = !local.narrativa_geral_ratificada
      motivo            = "zona PHI — provedor que satisfaz o contrato da zona (ver var.phi_zone_provider)"
    }
    marina = {
      zona              = "phi"
      replicas          = 1
      provider          = local._provider_phi
      phi_zone_required = !local.narrativa_geral_ratificada
      motivo            = "zona PHI — idem rafael"
    }
  }
}

resource "aws_cloudwatch_log_group" "agente" {
  for_each = local.agentes

  name              = "/ecs/${local.name}/agent-${each.key}"
  retention_in_days = var.log_retention_days
  tags              = merge(local.base_tags, { agente = each.key, zona = each.value.zona })
}

resource "aws_ecs_task_definition" "agente" {
  for_each = local.agentes

  family                   = "${local.name}-agent-${each.key}"
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
    name      = "agent-${each.key}"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.runtime.agent_runtime"]

    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    environment = concat(local.db_env, [
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      { name = "AGENT_ID", value = each.key },
      { name = "AGENT_SECURITY_ZONE", value = each.value.zona },
      # AGENT_DEFINITION_PATH deliberadamente AUSENTE.
      #
      # As definicoes viajam no wheel (force-include no pyproject.toml) e aterrissam
      # ao lado do pacote. Sem esta variavel, o runtime E o `tool_registry` resolvem
      # do MESMO lugar — uma fonte de verdade. Apontar a variavel para outro caminho
      # criaria duas, e elas divergiriam no primeiro dia em que alguem editasse uma
      # so'. Foi assim que a helena subiu "viva mas sem ferramenta" em 13/08.
      #
      # Hoje a definicao efetiva E' o L0: nao existe overlay de tenant em
      # spec/agents/, e o `provision_tenant.py` / `agent_def_merge` que o chart
      # citava como produtores da definicao mesclada NUNCA EXISTIRAM no codigo
      # (verificado em 13/08/2026). Quando um overlay real aparecer, o merge tem de
      # rodar ANTES do build da imagem — e nao virar uma variavel de ambiente.
      { name = "CIBSEVEN_BASE_URL", value = local.cibseven_base_url },
      { name = "FHIR_BASE_URL", value = local.fhir_base_url },
      { name = "HEALTH_PORT", value = "8000" },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = local.kafka_bootstrap },
      { name = "AGENT_RUNTIME_MODE", value = var.agent_runtime_mode },
      # Inferencia real via Bedrock (provado ao vivo em 12/08/2026 no ambiente
      # local). `MAEZO_INFERENCE_PROVIDER=bedrock` troca o stub pelo cliente real.
      { name = "MAEZO_INFERENCE_PROVIDER", value = each.value.provider },
      # Liga a verificacao de contrato de zona NO BOOT: com phi_zone_required=true, o
      # `InferenceProvider` recusa CONSTRUIR se as capacidades declaradas do provedor
      # nao satisfizerem os cinco criterios de `phi_zone_denial_reasons` (phi_allowed,
      # regiao elegivel, zero-retention, treino proibido, classificacao admite PHI).
      # E' o que impede alguem de apontar um agente PHI para o provedor geral e
      # descobrir isso no primeiro paciente.
      { name = "MAEZO_INFERENCE_PHI_ZONE_REQUIRED", value = tostring(each.value.phi_zone_required) },
      { name = "MAEZO_BEDROCK_MODEL_ID", value = var.bedrock_model_id },
      { name = "MAEZO_BEDROCK_REGION", value = var.aws_region },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = concat(local.db_secrets_maezo, [
      # Sem esta, o Pseudonymizer falha FECHADO em producao (ADR-0035) — nunca cai
      # para pseudonimo reversivel.
      { name = "PHI_HMAC_KEY", valueFrom = "${aws_secretsmanager_secret.phi_hmac_key.arn}:key::" },
      # Sem esta, o registry A2A admitiria Card nao assinado — e o runtime recusa
      # compor o dispatcher (ADR-0039). Nome do env var carrega o tenant.
      { name = "MAEZO_A2A_CARD_SIGNING_KEY__${upper(var.tenant_id)}", valueFrom = "${aws_secretsmanager_secret.a2a_card_signing_key.arn}:key::" },
    ])

    readonlyRootFilesystem = true

    healthCheck = {
      # python, nao curl: a imagem nao traz curl (python:3.12-slim).
      command     = ["CMD-SHELL", "python -c \"import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://localhost:8000/healthz',timeout=3).status==200 else 1)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.agente[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "agent"
      }
    }
  }])

  tags = merge(local.base_tags, { agente = each.key, zona = each.value.zona })
}

resource "aws_ecs_service" "agente" {
  for_each = local.agentes

  name            = "agent-${each.key}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.agente[each.key].arn
  desired_count   = each.value.replicas
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = merge(local.base_tags, { agente = each.key, zona = each.value.zona })
}
