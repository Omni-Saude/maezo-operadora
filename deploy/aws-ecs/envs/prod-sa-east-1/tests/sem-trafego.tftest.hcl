# PROD nasce SEM TRAFEGO: teste de CONFIGURACAO com o provider AWS mockado e o remote state da
# plataforma sobrescrito. Nenhuma chamada real, credencial ou apply. Prova o que o README promete:
#   * com os interruptores no default, NENHUM servico tem task e nenhuma rotina esta' ligada;
#   * ligar os servicos NAO liga a borda publica (tunel Cloudflare) junto;
#   * logs de auditoria >= 365 dias, sem Container Insights, ARM64 so' nas imagens multi-arch;
#   * nada aponta para o dev (conta, state, Cognito, FHIR).
mock_provider "aws" {
  mock_data "aws_iam_policy_document" { defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_region" { defaults = { name = "sa-east-1", region = "sa-east-1" } }
  mock_data "aws_caller_identity" { defaults = { account_id = "441395443640" } }
  mock_data "aws_vpc" { defaults = { id = "vpc-00000000000000042", cidr_block = "10.42.0.0/16" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-0000000000000000a", "subnet-0000000000000000b"] } }
  mock_data "aws_kms_alias" { defaults = { target_key_arn = "arn:aws:kms:sa-east-1:441395443640:key/22222222-2222-2222-2222-222222222222" } }
  mock_data "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::amh-pipeline-artifacts-prod-sa-east-1" } }
  mock_data "aws_cognito_user_pool" { defaults = { domain = "amh-maezo-bpm-prod" } }
  mock_data "aws_cognito_user_pool_clients" {
    defaults = {
      client_names = ["agent-rafael-austa_operadora", "maezo-operadora-interop", "maezo-operadora-interop-boleto"]
      client_ids   = ["rafaelprod000000000000000a", "interopprod00000000000000b", "boletoprod000000000000000c"]
    }
  }
  mock_data "aws_rds_cluster" {
    defaults = {
      endpoint               = "amh-aurora-hapi-prod.cluster-test.sa-east-1.rds.amazonaws.com"
      port                   = 5432
      vpc_security_group_ids = ["sg-00000000000000003"]
      master_user_secret     = [{ secret_arn = "arn:aws:secretsmanager:sa-east-1:441395443640:secret:rds!cluster-abcdef", secret_status = "active", kms_key_id = "" }]
    }
  }
  mock_data "aws_secretsmanager_secret" { defaults = { kms_key_id = "", arn = "arn:aws:secretsmanager:sa-east-1:441395443640:secret:test/data-abcdef" } }
  mock_resource "aws_ecr_repository" { defaults = { repository_url = "441395443640.dkr.ecr.sa-east-1.amazonaws.com/amh/maezo-operadora", arn = "arn:aws:ecr:sa-east-1:441395443640:repository/amh/maezo-operadora" } }
  mock_resource "aws_ecs_cluster" { defaults = { arn = "arn:aws:ecs:sa-east-1:441395443640:cluster/maezo-operadora-prod", id = "arn:aws:ecs:sa-east-1:441395443640:cluster/maezo-operadora-prod" } }
  mock_resource "aws_ecs_task_definition" { defaults = { arn = "arn:aws:ecs:sa-east-1:441395443640:task-definition/test:1" } }
  mock_resource "aws_service_discovery_service" { defaults = { arn = "arn:aws:servicediscovery:sa-east-1:441395443640:service/srv-test" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:sa-east-1:441395443640:log-group:test" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::441395443640:role/test" } }
  mock_resource "aws_sns_topic" { defaults = { arn = "arn:aws:sns:sa-east-1:441395443640:test" } }
  mock_resource "aws_secretsmanager_secret" { defaults = { arn = "arn:aws:secretsmanager:sa-east-1:441395443640:secret:test-abcdef" } }
}

override_data {
  target = data.terraform_remote_state.foundation
  values = {
    outputs = {
      vpc_id                     = "vpc-00000000000000042"
      private_app_subnet_ids     = ["subnet-0000000000000000a", "subnet-0000000000000000b"]
      aurora_hapi_clientes_sg_id = "sg-0000000000000c11e"
      aurora_hapi_app_role_secrets = {
        hapi_app     = "arn:aws:secretsmanager:sa-east-1:441395443640:secret:amh-aurora-hapi-prod/hapi_app-aaaaaa"
        maezo_app    = "arn:aws:secretsmanager:sa-east-1:441395443640:secret:amh-aurora-hapi-prod/maezo_app-bbbbbb"
        cibseven_app = "arn:aws:secretsmanager:sa-east-1:441395443640:secret:amh-aurora-hapi-prod/cibseven_app-cccccc"
      }
      cognito_user_pool_id = "sa-east-1_ProdPool"
      lake_buckets         = { artifacts = "amh-pipeline-artifacts-prod-sa-east-1" }
    }
  }
}

override_data {
  target = data.terraform_remote_state.hapi
  values = { outputs = { internal_ingest_url = "https://fhir-int-amh.austa.com.br/fhir" } }
}

override_resource {
  target = aws_security_group.tasks
  values = { id = "sg-00000000000000010" }
}

variables {
  # Fixtures SINTETICAS (nada implantavel). A atestacao PHI e' -var no apply real; aqui o teste a
  # preenche porque a variavel nao tem default — o que este teste tambem prova.
  app_image_digest              = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  webhook_receiver_image_digest = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  worker_image_digest           = "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  agents_image_digest           = "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  engine_image_digest           = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  phi_zone_provider             = "phi_zone_mock"
  phi_model_id                  = "mistral.mistral-large-3-675b-instruct"
  phi_vendor_dpa_ref            = ""
  helena_zona_phi               = false
}

run "nasce_sem_trafego" {
  command = apply # provider MOCKADO: nada sai da maquina

  # Diagnostico PRE-EXISTENTE do repo (o mesmo do dev): a ratificacao da narrativa carimbou
  # `pending-sha256`. Nao e' efeito deste env; quando alguem ratificar, remova esta linha.
  expect_failures = [check.ratificacao_da_zona_da_narrativa]

  assert {
    condition     = var.servicos_habilitados == false && var.borda_publica_habilitada == false
    error_message = "prod.auto.tfvars tem de manter os dois interruptores desligados."
  }
  assert {
    condition = alltrue([
      aws_ecs_service.webhook_receiver.desired_count == 0,
      aws_ecs_service.worker.desired_count == 0,
      aws_ecs_service.cibseven.desired_count == 0,
      aws_ecs_service.kafka.desired_count == 0,
      aws_ecs_service.bridge.desired_count == 0,
      aws_ecs_service.a2a_outbox_relay.desired_count == 0,
      aws_ecs_service.cloudflared.desired_count == 0,
      aws_ecs_service.metrics_collector.desired_count == 0,
    ])
    error_message = "Com servicos_habilitados = false todo servico tem de nascer com 0 tasks."
  }
  assert {
    condition     = alltrue([for s in aws_ecs_service.agente : s.desired_count == 0])
    error_message = "Agentes com task em prod sem cutover."
  }
  assert {
    condition     = alltrue([for s in aws_ecs_service.agent_resume : s.desired_count == 0]) && alltrue([for s in aws_ecs_service.notifications_inbox : s.desired_count == 0])
    error_message = "Retomada/inbox com task em prod sem cutover."
  }
  assert {
    condition     = alltrue([for s in aws_scheduler_schedule.lembrete_cobranca : s.state == "DISABLED"])
    error_message = "Rotina agendada ligada em prod sem cutover."
  }
  assert {
    condition     = alltrue([for s in aws_ecs_service.agente : s.enable_execute_command == false]) && aws_ecs_service.cibseven.enable_execute_command == false && aws_ecs_service.worker.enable_execute_command == false
    error_message = "ECS Exec tem de nascer desligado em prod."
  }
  assert {
    condition     = one([for s in aws_ecs_cluster.this.setting : s.value if s.name == "containerInsights"]) == "disabled"
    error_message = "Container Insights tem de ficar desligado (decisao de custo)."
  }
  assert {
    condition = alltrue([
      aws_cloudwatch_log_group.webhook_receiver.retention_in_days >= 365,
      aws_cloudwatch_log_group.cibseven.retention_in_days >= 365,
      aws_cloudwatch_log_group.worker.retention_in_days >= 365,
      aws_cloudwatch_log_group.migrations.retention_in_days >= 365,
      aws_cloudwatch_log_group.kafka.retention_in_days <= 90,
      aws_cloudwatch_log_group.bridge.retention_in_days <= 90,
    ])
    error_message = "Retencao fora da decisao (auditoria >= 365; resto 30-90)."
  }
  assert {
    condition     = aws_cloudwatch_log_group.webhook_receiver.kms_key_id == "arn:aws:kms:sa-east-1:441395443640:key/22222222-2222-2222-2222-222222222222"
    error_message = "Log group de prod sem a CMK de logs."
  }
  assert {
    condition = alltrue([
      one(aws_ecs_task_definition.kafka.runtime_platform).cpu_architecture == "ARM64",
      one(aws_ecs_task_definition.cloudflared.runtime_platform).cpu_architecture == "ARM64",
      one(aws_ecs_task_definition.metrics_collector.runtime_platform).cpu_architecture == "ARM64",
      one(aws_ecs_task_definition.cibseven.runtime_platform).cpu_architecture == "X86_64",
      one(aws_ecs_task_definition.worker.runtime_platform).cpu_architecture == "X86_64",
    ])
    error_message = "ARM64 so' onde a imagem e' multi-arch; o resto X86_64."
  }
  assert {
    condition     = strcontains(aws_ecs_task_definition.webhook_receiver.container_definitions, "@sha256:bbbb") && !strcontains(aws_ecs_task_definition.webhook_receiver.container_definitions, "DEVOLVE_TURNO")
    error_message = "Receptor de prod: imagem por digest e SEM devolver o turno no ack."
  }
  assert {
    condition     = local.fhir_base_url == "https://fhir-int-amh.austa.com.br/fhir" && local.fhir_cognito_client_id == "rafaelprod000000000000000a" && strcontains(local.cognito_token_url, "amh-maezo-bpm-prod")
    error_message = "FHIR/Cognito tem de vir da plataforma de PROD (remote state + pool por nome)."
  }
  assert {
    condition     = contains(aws_ecs_service.webhook_receiver.network_configuration[0].security_groups, "sg-0000000000000c11e")
    error_message = "Tasks sem o SG-marcador do Aurora de prod."
  }
  assert {
    condition     = var.aws_account_id == "441395443640" && local.engine_rest_authentication == "client-certificate"
    error_message = "Trava de conta de prod ou declaracao N5 ausentes."
  }

  # --- Review de seguranca do PR #1204 ---
  # Bedrock so' em Sao Paulo: (1) os ARNs que a config monta, (2) varredura do TEXTO de todo .tf
  # do env — pega policy nova que alguem acrescente sem passar por task_bedrock.
  assert {
    condition = alltrue([
      for arn in concat(
        flatten([for s in data.aws_iam_policy_document.task_bedrock.statement : s.resources]),
        flatten([for s in data.aws_iam_policy_document.roteador_modelo.statement : s.resources]),
        flatten([for s in data.aws_iam_policy_document.busca_semantica_saude.statement : s.resources]),
      ) : startswith(arn, "arn:aws:bedrock:sa-east-1:") && !strcontains(arn, "global.")
    ])
    error_message = "Policy Bedrock de prod fora de sa-east-1 ou com perfil global."
  }
  assert {
    condition = alltrue([
      for f in fileset(path.module, "*.tf") :
      !can(regex("bedrock:\\*:|inference-profile/global\\.|bedrock:us-|bedrock:eu-", file("${path.module}/${f}")))
    ])
    error_message = "Algum .tf de prod cita regiao curinga, us/eu ou perfil global do Bedrock."
  }
  assert {
    condition     = !fileexists("${path.module}/experimento-saude-gerativa.tf") && local.phi_regiao == "sa-east-1"
    error_message = "Saude gerativa (perfil global) ou excecao us-east-1 presentes em prod."
  }
  assert {
    condition     = length(aws_ecs_task_definition.bootstrap_db) == 0 && length(aws_iam_role.bootstrap_db_execution) == 0
    error_message = "A task com a credencial MESTRE do Aurora so' existe com bootstrap_db_habilitado = true."
  }
  assert {
    condition     = length(aws_iam_role_policy.task_exec_ssm) == 0
    error_message = "ssmmessages so' com ECS Exec ligado."
  }
  assert {
    condition = (
      aws_ecs_service.cloudflared.network_configuration[0].security_groups == toset([aws_security_group.cloudflared.id]) &&
      !contains(aws_ecs_service.cloudflared.network_configuration[0].security_groups, "sg-0000000000000c11e")
    )
    error_message = "O cloudflared tem de usar SO' o SG proprio (sem o das tasks e sem o marcador do Aurora)."
  }
}

run "servicos_ligados_nao_ligam_a_borda" {
  command         = apply
  expect_failures = [check.ratificacao_da_zona_da_narrativa]

  variables {
    servicos_habilitados  = true
    staff_alerts_email    = "alertas@example.test"
    engine_image_variante = "secured-v2"
  }

  override_data {
    target = data.aws_ecr_image.engine[0]
    values = { image_tags = ["secured-v2-4f2a91c0"] }
  }

  assert {
    condition     = aws_ecs_service.webhook_receiver.desired_count == 1 && aws_ecs_service.cibseven.desired_count == 1
    error_message = "Com servicos_habilitados = true os servicos sobem com o desired_count declarado."
  }
  assert {
    condition     = aws_ecs_service.cloudflared.desired_count == 0
    error_message = "A borda publica so' liga com borda_publica_habilitada = true."
  }
}

# N5: a imagem aberta do dev (tag sem-showcase-*) nao sobe em prod, mesmo atestada.
run "engine_aberto_nao_sobe" {
  command = plan

  variables {
    servicos_habilitados  = true
    staff_alerts_email    = "alertas@example.test"
    engine_image_variante = "secured-v2"
  }

  override_data {
    target = data.aws_ecr_image.engine[0]
    values = { image_tags = ["sem-showcase-executor-global-grupos-hifen"] }
  }

  expect_failures = [aws_ecs_service.cibseven, check.ratificacao_da_zona_da_narrativa]
}

# N5: sem atestacao explicita tambem nao sobe, mesmo com a tag certa.
run "engine_sem_atestacao_nao_sobe" {
  command = plan

  variables {
    servicos_habilitados = true
    staff_alerts_email   = "alertas@example.test"
  }

  override_data {
    target = data.aws_ecr_image.engine[0]
    values = { image_tags = ["secured-v2-4f2a91c0"] }
  }

  expect_failures = [aws_ecs_service.cibseven, check.ratificacao_da_zona_da_narrativa]
}

run "bootstrap_tem_execution_role_propria" {
  command         = apply
  expect_failures = [check.ratificacao_da_zona_da_narrativa]

  variables {
    bootstrap_db_habilitado = true
  }

  override_resource {
    target = aws_iam_role.bootstrap_db_execution["this"]
    values = { arn = "arn:aws:iam::441395443640:role/maezo-operadora-prod-bootstrap-db-execution" }
  }

  assert {
    condition = (
      aws_ecs_task_definition.bootstrap_db["this"].execution_role_arn == "arn:aws:iam::441395443640:role/maezo-operadora-prod-bootstrap-db-execution" &&
      aws_iam_role_policy.bootstrap_db_master_secret["this"].role == aws_iam_role.bootstrap_db_execution["this"].id
    )
    error_message = "O segredo mestre do Aurora so' pode ser lido pela execution role propria do bootstrap."
  }
}

# Saidas de emergencia do dev travadas em prod.
run "excecao_us_east_1_reprova" {
  command = plan
  variables {
    phi_excecao_regional = "excecao_us_east_1_autorizada_dpo_2026-10-21"
    phi_endpoint_url     = "https://bedrock-runtime.us-east-1.amazonaws.com"
  }
  expect_failures = [var.phi_excecao_regional, var.phi_endpoint_url, check.ratificacao_da_zona_da_narrativa]
}

run "caso_sintetico_reprova" {
  command = plan
  variables { caso_sintetico_zona_geral = true }
  expect_failures = [var.caso_sintetico_zona_geral, check.ratificacao_da_zona_da_narrativa]
}

run "zona_geral_com_perfil_global_reprova" {
  command = plan
  variables { bedrock_model_id = "global.anthropic.claude-opus-5" }
  expect_failures = [var.bedrock_model_id, check.ratificacao_da_zona_da_narrativa]
}

run "saude_gerativa_reprova" {
  command = plan
  variables { helena_saude_gerativa = true }
  expect_failures = [var.helena_saude_gerativa, check.ratificacao_da_zona_da_narrativa]
}

run "conta_do_dev_reprova" {
  command = plan
  variables { aws_account_id = "203312548462" }
  expect_failures = [var.aws_account_id, check.ratificacao_da_zona_da_narrativa]
}

run "tag_no_lugar_de_digest_reprova" {
  command = plan

  variables {
    app_image_digest = "d443b52f"
  }

  expect_failures = [var.app_image_digest, check.ratificacao_da_zona_da_narrativa]
}
