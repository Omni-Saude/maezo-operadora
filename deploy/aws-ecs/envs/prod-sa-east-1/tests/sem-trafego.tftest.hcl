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
}

run "servicos_ligados_nao_ligam_a_borda" {
  command         = apply
  expect_failures = [check.ratificacao_da_zona_da_narrativa]

  variables {
    servicos_habilitados = true
    staff_alerts_email   = "alertas@example.test"
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

run "tag_no_lugar_de_digest_reprova" {
  command = plan

  variables {
    app_image_digest = "d443b52f"
  }

  expect_failures = [var.app_image_digest, check.ratificacao_da_zona_da_narrativa]
}
