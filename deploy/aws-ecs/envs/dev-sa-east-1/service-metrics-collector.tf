# Coletor de metricas — Frente 5, passo 5.1 de HELENA_EM_PRODUCAO_O_QUE_FALTA.md.
#
# O QUE FALTAVA, MEDIDO EM 14/09/2026: o workspace gerenciado (`amh-prometheus-dev`) esta' ACTIVE,
# com ZERO scrapers; o cluster tem ZERO coletor; o `prometheus.yml` do repo aponta para o
# docker-compose da maquina de quem desenvolve. Ou seja: `maezo_agent_desfecho_total` e' emitido
# desde CC-09, as regras de agregacao existem desde ADR-0014, e NINGUEM LE. O contador zera a cada
# redeploy e a taxa de escalonamento que o `agent.yaml` chama de KPI nunca foi um numero.
#
# ESTE ARQUIVO E' A METADE DE INFRA; a outra metade e' `deploy/observability/otel-collector-ecs-dev.yaml`,
# que descreve O QUE raspar. A divisao e' de proposito: quem muda um alvo mexe no YAML versionado,
# nao numa task definition, e o `terraform plan` mostra a diferenca porque o YAML entra por
# `file()` — nao ha configuracao viva fora do repo.

# ---------------------------------------------------------------------------
# O WORKSPACE GERENCIADO — descoberto, nao criado.
# ---------------------------------------------------------------------------
# NAO criamos o workspace aqui, e a razao nao e' preguica: `amh-prometheus-dev` ja' existe e e' da
# plataforma, compartilhado com outros times. Um `resource` neste state reivindicaria a posse de um
# recurso de terceiro — e um `terraform destroy` do maezo apagaria a serie historica de quem nunca
# pediu nada. Lemos pelo alias e escrevemos nele.
data "aws_prometheus_workspaces" "amh" {
  alias_prefix = var.prometheus_workspace_alias
}

locals {
  _amp_encontrado = length(data.aws_prometheus_workspaces.amh.workspace_ids) > 0
  amp_workspace_id = (
    local._amp_encontrado ? data.aws_prometheus_workspaces.amh.workspace_ids[0] : ""
  )

  # `https://aps-workspaces.<regiao>.amazonaws.com/workspaces/<id>/api/v1/remote_write`. Montado a
  # partir do id em vez de fixado numa variavel: um endpoint colado a mao e' a forma classica de
  # escrever no workspace errado sem que nada reclame — a escrita seria aceita, so' que noutro
  # lugar, e o painel ficaria vazio parecendo canal sem trafego.
  amp_remote_write_url = (
    local._amp_encontrado
    ? "https://aps-workspaces.${var.aws_region}.amazonaws.com/workspaces/${local.amp_workspace_id}/api/v1/remote_write"
    : ""
  )
}

# Diagnostico honesto no plan. Sem isto, um alias errado produziria um coletor no ar escrevendo
# para string vazia — task saudavel, health check verde, e nenhuma metrica chegando. O modo de
# falha silencioso e' exatamente o que esta frente existe para acabar.
check "workspace_de_metricas_existe" {
  assert {
    condition     = local._amp_encontrado || var.metrics_collector_desired_count == 0
    error_message = "Nenhum workspace AMP com alias comecando em '${var.prometheus_workspace_alias}' na regiao ${var.aws_region}. Corrija `prometheus_workspace_alias` ou zere `metrics_collector_desired_count` - subir o coletor sem destino e' um coletor que parece saudavel e nao coleta nada."
  }
}

# ---------------------------------------------------------------------------
# AS REGRAS, CARREGADAS NO WORKSPACE
# ---------------------------------------------------------------------------
# ADR-0014: mesmo YAML, destino diferente. O Prometheus local carrega `alert-rules.yml` por
# `rule_files`; aqui o MESMO arquivo vira um namespace de regras do workspace gerenciado. Nao ha
# copia e nao ha bifurcacao — uma regra corrigida num lugar e' a mesma regra nos dois.
#
# O QUE ISTO TORNA VERDADEIRO: as tres regras de KPI de `maezo_agent_kpi_derived` deixam de ser
# texto num arquivo e passam a ser series avaliadas de 30 em 30 segundos. E' a diferenca entre "a
# taxa de escalonamento esta' definida" e "a taxa de escalonamento e' 0,18".
#
# O QUE ISTO NAO TORNA VERDADEIRO, e vale dizer: varias regras deste arquivo leem metricas que
# NINGUEM emite neste ambiente (`kube_job_status_failed`, `kafka_topic_partition_current_offset`).
# Elas nao passam a funcionar por estarem carregadas; ficam sem serie, que e' o mesmo estado
# honesto que ja' tinham no compose local e esta' documentado regra a regra la'.
resource "aws_prometheus_rule_group_namespace" "maezo" {
  count = local._amp_encontrado ? 1 : 0

  name         = local.name
  workspace_id = local.amp_workspace_id
  data         = file("${path.module}/../../../observability/alert-rules.yml")
}

# ---------------------------------------------------------------------------
# ROLE PROPRIA DO COLETOR
# ---------------------------------------------------------------------------
# Role SEPARADA da role das tasks do maezo, e nao e' zelo decorativo: a role das tasks pode ler
# segredo de aplicacao e falar com o Bedrock. O coletor nao precisa de nada disso — ele precisa de
# UMA permissao, escrever no workspace. Reaproveitar a role existente daria ao processo que raspa
# `/metrics` o mesmo alcance de quem atende beneficiario.
resource "aws_iam_role" "metrics_collector_task" {
  name = "${local.name}-metrics-collector-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.base_tags
}

resource "aws_iam_role_policy" "metrics_collector_remote_write" {
  name = "remote-write"
  role = aws_iam_role.metrics_collector_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "aps:RemoteWrite",
        # `GetSeries`/`GetLabels`/`GetMetricMetadata` NAO entram: o coletor so' escreve. Quem le'
        # e' o painel, com a identidade de quem esta' olhando, nao com a da task.
      ]
      Resource = (
        local._amp_encontrado
        ? "arn:aws:aps:${var.aws_region}:${var.aws_account_id}:workspace/${local.amp_workspace_id}"
        : "arn:aws:aps:${var.aws_region}:${var.aws_account_id}:workspace/NAO-RESOLVIDO"
      )
    }]
  })
}

resource "aws_cloudwatch_log_group" "metrics_collector" {
  name              = "/ecs/${local.name}/metrics-collector"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

# ---------------------------------------------------------------------------
# A TASK
# ---------------------------------------------------------------------------
resource "aws_ecs_task_definition" "metrics_collector" {
  family                   = "${local.name}-metrics-collector"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.metrics_collector_cpu)
  memory                   = tostring(var.metrics_collector_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.metrics_collector_task.arn

  container_definitions = jsonencode([{
    name      = "collector"
    image     = var.metrics_collector_image
    essential = true

    # 8888 e' a auto-telemetria do coletor, raspada por ele mesmo pelo job `otel-collector` da
    # config. Nao ha registro no Cloud Map: ninguem alcanca o coletor, ele e' quem alcanca.
    portMappings = [{ containerPort = 8888, protocol = "tcp" }]

    environment = [
      # A CONFIGURACAO INTEIRA POR VARIAVEL DE AMBIENTE. E' o caminho que o entrypoint da imagem
      # `aws-otel-collector` le (`AOT_CONFIG_CONTENT`), e evita um parametro no SSM que seria uma
      # segunda fonte de verdade — editavel pelo console, invisivel no `git log`.
      { name = "AOT_CONFIG_CONTENT", value = file("${path.module}/../../../observability/otel-collector-ecs-dev.yaml") },

      { name = "AWS_REGION", value = var.aws_region },
      { name = "MAEZO_ENV", value = local.env },
      { name = "MAEZO_CLUSTER", value = local.name },
      # O sufixo DNS do Cloud Map, para a config nao ter o nome do ambiente escrito dentro.
      { name = "MAEZO_NAMESPACE", value = aws_service_discovery_private_dns_namespace.this.name },
      { name = "AMP_REMOTE_WRITE_URL", value = local.amp_remote_write_url },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.metrics_collector.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "collector"
      }
    }

    # O health check da extensao `health_check` (13133), pelo binario que a PROPRIA imagem traz.
    #
    # CORRIGIDO EM 18/09/2026, no primeiro apply em dev. A versao anterior era
    # `["CMD-SHELL", "wget -q -O - http://localhost:13133/healthz || exit 1"]`, e o comentario que a
    # acompanhava dizia "a imagem do coletor nao traz curl; traz `wget`, o mesmo que o compose local
    # ja' usa". A premissa estava errada por uma razao simples: **o compose local usa OUTRA imagem**
    # (`otel/opentelemetry-collector-contrib`, base Alpine, que tem wget). Esta aqui e'
    # `aws-observability/aws-otel-collector`, e o conteudo dela foi MEDIDO layer a layer no registry:
    # os unicos executaveis sao `/awscollector` e `/healthcheck`. Nao ha wget, nao ha curl e **nao ha
    # `/bin/sh`** — entao `CMD-SHELL` nao tinha nem como ser interpretado.
    #
    # O SINTOMA NAO FOI UM COLETOR QUEBRADO, e e' por isso que vale registrar: o coletor subiu,
    # raspou os tres jobs e escreveu no workspace (22 metricas `maezo_*` medidas no AMP). Quem falhou
    # foi so' o health check, e o ECS deployment circuit breaker derrubou um servico que estava
    # FUNCIONANDO. Um healthcheck errado nao devolve "nao sei"; devolve "doente".
    #
    # `CMD` e nao `CMD-SHELL` de proposito: `CMD` executa o binario direto, sem shell. Trocar o
    # comando mantendo `CMD-SHELL` continuaria falhando pelo mesmo motivo.
    healthCheck = {
      command     = ["CMD", "/healthcheck"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "metrics_collector" {
  name            = "${local.name}-metrics-collector"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.metrics_collector.arn
  desired_count   = var.metrics_collector_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  # MESMO SECURITY GROUP DAS TASKS, e e' o que torna a raspagem possivel sem abrir nada novo: as
  # regras auto-referenciadas de 8000 (ingresso dos agentes) e 8080 ja' existem desde 19/08, e a
  # saida 443 pela NAT e' a que leva ao endpoint do workspace. Nenhuma regra de rede nova entra
  # nesta frente — vale conferir isso no plan, porque uma frente de observabilidade que precisa
  # abrir porta e' uma frente que esta' medindo do lugar errado.
  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}
