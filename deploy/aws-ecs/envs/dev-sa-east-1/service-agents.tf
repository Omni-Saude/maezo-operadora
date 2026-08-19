# Agentes — um service POR AGENTE, mesma imagem, AGENT_ID diferente.
#
# Por que nao um service com 3 replicas: cada agente tem identidade, zona de
# seguranca e definicao propria. Uma replica nao e' intercambiavel com a outra.
#
# ZONA GERAL vs ZONA PHI — a divisao que decide o que sobe aqui:
#
#   helena  = zona GERAL. Nao ve dado de paciente. Modelo real via Bedrock.
#   rafael  = zona PHI.  Sobe, e o provedor dele depende do portao abaixo.
#   marina  = zona PHI.  Idem rafael.
#
# O motivo nao e' tecnico e nao e' meu para resolver: o modelo default e'
# `global.anthropic.claude-opus-5`, e um perfil `global.*` ROTEIA ENTRE REGIOES
# por desenho (src/maezo/runtime/inference.py:485). Mandar PHI por ele significa
# inferencia possivelmente executada fora do Brasil — o que exige o DPA do
# endpoint BR-resident (docs/Tarefas_Pendentes.md §3.1, item 🔴 de juridico/DPO).
#
# O QUE MUDOU, E POR QUE NAO E' O MESMO QUE "liberamos PHI": rafael e marina passaram a
# subir com `var.caso_sintetico_zona_geral = true`, que e' uma declaracao de que o
# ambiente NAO TEM dado real de paciente — nao uma ratificacao de que PHI pode sair do
# Brasil. Enquanto essa chave estiver ligada, o que trafega e' caso ficticio. No dia em
# que entrar dado real ela precisa ser desligada ANTES, e ai' os dois voltam ao provedor
# de zona PHI (`var.phi_zone_provider`) automaticamente, porque o portao abaixo le a
# mesma variavel. A ratificacao de verdade — DPO + medico auditor sobre
# `spec/policies/phi/dossier-narrative-zone.yaml` — continua PENDENTE e nenhum agente
# pode assina-la.

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

  # DUAS vias levam a narrativa para a zona geral, e elas afirmam coisas diferentes:
  # a ratificacao (o DADO e' de zona geral) e a declaracao de ambiente sintetico (o
  # AMBIENTE nao ve dado real). O runtime faz exatamente a mesma distincao em
  # `platform/privacy/dossier_zone.py`.
  narrativa_zona_geral = local.narrativa_geral_ratificada || var.caso_sintetico_zona_geral

  # Provedor da zona PHI por padrao; provedor real quando uma das vias autoriza.
  _provider_phi = local.narrativa_zona_geral ? var.inference_provider : var.phi_zone_provider
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
      phi_zone_required = !local.narrativa_zona_geral
      motivo            = "zona PHI — provedor que satisfaz o contrato da zona (ver var.phi_zone_provider)"
    }
    marina = {
      zona              = "phi"
      replicas          = 1
      provider          = local._provider_phi
      phi_zone_required = !local.narrativa_zona_geral
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
      # A rota que EXECUTA um turno. So' o rafael a recebe: o contrato do corpo do POST e'
      # o `RafaelState`, e um ingresso ligado num agente cujo grafo espera outra forma
      # aceitaria pedido e falharia no primeiro no'.
      #
      # Ate' 19/08/2026 nenhum agente tinha ingresso, e era por isso que o log dizia
      # `agent_graph_execution_not_performed_here` e `llm_token_usage` era zero: o daemon
      # compilava o grafo e nao tinha por onde ser chamado.
      { name = "MAEZO_AGENT_INGRESS_ENABLED", value = each.key == "rafael" ? "1" : "0" },
      # Liga a verificacao de contrato de zona NO BOOT: com phi_zone_required=true, o
      # `InferenceProvider` recusa CONSTRUIR se as capacidades declaradas do provedor
      # nao satisfizerem os cinco criterios de `phi_zone_denial_reasons` (phi_allowed,
      # regiao elegivel, zero-retention, treino proibido, classificacao admite PHI).
      # E' o que impede alguem de apontar um agente PHI para o provedor geral e
      # descobrir isso no primeiro paciente.
      { name = "MAEZO_INFERENCE_PHI_ZONE_REQUIRED", value = tostring(each.value.phi_zone_required) },
      # A chave viaja para o container SO' quando ligada. Assim ela aparece — ou nao —
      # no diff da task definition, e "quem ligou isso?" tem resposta no historico.
      { name = "MAEZO_DOSSIER_NARRATIVE_GENERAL_ZONE_SYNTHETIC_ONLY", value = var.caso_sintetico_zona_geral ? "1" : "0" },
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

# DNS interno estavel por agente. Nao existia ate 19/08/2026 porque nada precisava
# ALCANCAR um agente: eles eram daemons de saude que ninguem chamava. Com a rota de ingresso
# ligada, o Canal de Teste precisa de um nome — o IP da task muda a cada deploy, e um IP
# fixado numa variavel e' uma quebra silenciosa no proximo apply.
resource "aws_service_discovery_service" "agente" {
  for_each = local.agentes

  name = "agent-${each.key}"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id

    dns_records {
      ttl  = 10 # baixo: o IP muda a cada deploy da task
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  tags = merge(local.base_tags, { agente = each.key })
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

  service_registries {
    registry_arn = aws_service_discovery_service.agente[each.key].arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = merge(local.base_tags, { agente = each.key, zona = each.value.zona })
}
