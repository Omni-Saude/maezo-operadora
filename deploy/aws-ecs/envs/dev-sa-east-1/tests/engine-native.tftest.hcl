# Onda 4 (engine-native.tf): testes de CONFIGURACAO com o provider AWS mockado. Nenhuma chamada
# real, credencial ou apply na nuvem. O NO-OP contra o dev vivo e' provado a parte pelo
# `terraform plan` real (PR); aqui fica o formato do que a chave liga e as recusas da variavel.
mock_provider "aws" {
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:sa-east-1:203312548462:log-group:test-engine" } }
  mock_resource "aws_lb_target_group" { defaults = { arn = "arn:aws:elasticloadbalancing:sa-east-1:203312548462:targetgroup/test-engine/0000000000000000" } }
  mock_resource "aws_service_discovery_service" { defaults = { arn = "arn:aws:servicediscovery:sa-east-1:203312548462:service/srv-test" } }
  mock_resource "aws_ecs_task_definition" { defaults = { arn = "arn:aws:ecs:sa-east-1:203312548462:task-definition/test-engine:1" } }
  mock_resource "aws_ecs_cluster" { defaults = { arn = "arn:aws:ecs:sa-east-1:203312548462:cluster/test", id = "arn:aws:ecs:sa-east-1:203312548462:cluster/test" } }
  mock_resource "aws_lb" { defaults = { arn = "arn:aws:elasticloadbalancing:sa-east-1:203312548462:loadbalancer/net/test-engine/0000000000000000", dns_name = "test-engine.elb.sa-east-1.amazonaws.com", zone_id = "Z0000000000000000ELB" } }
  mock_resource "aws_ecr_repository" { defaults = { repository_url = "203312548462.dkr.ecr.sa-east-1.amazonaws.com/test/app", arn = "arn:aws:ecr:sa-east-1:203312548462:repository/test/app" } }
  mock_data "aws_iam_policy_document" { defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" } }
  mock_data "aws_security_group" { defaults = { vpc_id = "vpc-00000000000000001" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_vpc" { defaults = { id = "vpc-00000000000000001", cidr_block = "10.40.0.0/16" } }
  mock_data "aws_rds_cluster" {
    defaults = {
      endpoint               = "db.test"
      port                   = 5432
      vpc_security_group_ids = ["sg-00000000000000003"]
      master_user_secret     = [{ secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:test/master-abcdef", secret_status = "active", kms_key_id = "" }]
    }
  }
  mock_data "aws_secretsmanager_secret" { defaults = { kms_key_id = "", arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:test/data-abcdef" } }
}
override_data {
  target = data.aws_subnet.portal_public["subnet-00000000000000001"]
  values = { vpc_id = "vpc-00000000000000001", availability_zone = "sa-east-1a" }
}
override_data {
  target = data.aws_subnet.portal_public["subnet-00000000000000002"]
  values = { vpc_id = "vpc-00000000000000001", availability_zone = "sa-east-1b" }
}
override_data {
  target = data.aws_secretsmanager_secret.engine_native["this"]
  values = { kms_key_id = "alias/test-engine-native" }
}
override_data {
  target = data.aws_kms_key.engine_native["this"]
  values = { arn = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111" }
}
override_resource {
  target = aws_service_discovery_private_dns_namespace.this
  values = { name = "maezo-operadora-dev.internal", hosted_zone = "Z0000000000000CLOUDMAP" }
}
override_resource {
  target = aws_security_group.portal["this"]
  values = { id = "sg-00000000000000001" }
}
override_resource {
  target = aws_security_group.tasks
  values = { id = "sg-00000000000000010" }
}
override_resource {
  target = aws_security_group.engine_native_nlb["this"]
  values = { id = "sg-00000000000000020" }
}
override_resource {
  target = aws_iam_role.task_execution
  values = { arn = "arn:aws:iam::203312548462:role/test-exec", id = "test-exec" }
}
override_resource {
  target = aws_iam_role.task
  values = { arn = "arn:aws:iam::203312548462:role/test-task", id = "test-task" }
}
variables {
  # Fixtures sinteticas; nada aqui e' implantavel.
  diagnostics_image_digest = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  worker_image_digest      = "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  agents_image_digest      = "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  engine_image_digest      = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  tenant_id                = "portaltest"
  portal_enabled           = false
  fhir_cognito_client_id   = "machine123"
  portal = {
    tenant                  = "portaltest"
    issuer                  = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_TestPool"
    cognito_origin          = "https://login.example.test"
    human_client_id         = "human123"
    human_client_purpose    = "dedicated-human-code-pkce"
    machine_client_id       = "machine123"
    public_origin           = "https://portal.example.test"
    image_digest            = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    database_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/session-dsn-abcdef"
    certificate_arn         = "arn:aws:acm:sa-east-1:203312548462:certificate/00000000-0000-0000-0000-000000000000"
    public_subnet_ids       = ["subnet-00000000000000001", "subnet-00000000000000002"]
    https_egress_ipv4_cidrs = ["192.0.2.10/32"]
  }
  engine_native = null
}

run "null_nao_cria_nada" {
  command = apply
  plan_options { target = [aws_ecs_service.cibseven, aws_service_discovery_instance.engine_native, aws_iam_role_policy.task_execution_engine_native, aws_vpc_security_group_ingress_rule.engine_native_nlb_from_portal, aws_vpc_security_group_ingress_rule.tasks_engine_native_from_nlb] }
  assert {
    condition = (
      length(aws_lb.engine_native) == 0 && length(aws_security_group.engine_native_nlb) == 0 &&
      length(aws_service_discovery_instance.engine_native) == 0 && length(aws_iam_role_policy.task_execution_engine_native) == 0 &&
      length(aws_vpc_security_group_ingress_rule.tasks_engine_native_from_nlb) == 0 &&
      length(aws_ecs_task_definition.cibseven.volume) == 0 && length(aws_ecs_service.cibseven.load_balancer) == 0 &&
      length(jsondecode(aws_ecs_task_definition.cibseven.container_definitions)) == 1 &&
      jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[0].portMappings == [{ containerPort = 8080, protocol = "tcp" }] &&
      endswith({ for e in jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[0].environment : e.name => e.value }["DB_URL"], "?currentSchema=cibseven") &&
      !contains(keys(jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[0]), "mountPoints") &&
      output.engine_native_origin == null && output.engine_native_nlb_sg_id == null
    )
    error_message = "engine_native = null tem de deixar o engine de hoje: sem NLB/SG/registro/init, so 8080 e currentSchema=cibseven."
  }
}

run "ligado_sem_emissor" {
  command = apply
  plan_options { target = [aws_ecs_service.cibseven, aws_service_discovery_instance.engine_native, aws_iam_role_policy.task_execution_engine_native, aws_vpc_security_group_ingress_rule.engine_native_nlb_from_portal, aws_vpc_security_group_ingress_rule.engine_native_nlb_from_issuer, aws_vpc_security_group_egress_rule.engine_native_nlb_to_engine, aws_vpc_security_group_ingress_rule.tasks_engine_native_from_nlb] }
  variables {
    engine_native = {
      native_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-abcdef"
      native_secret_version_id = "00000000-0000-0000-0000-000000000001"
      kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      app_image_digest         = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      staff_case_issuer        = false
    }
  }
  assert {
    condition = (
      aws_lb.engine_native["this"].internal && aws_lb.engine_native["this"].load_balancer_type == "network" &&
      aws_lb.engine_native["this"].security_groups == toset(["sg-00000000000000020"]) &&
      aws_lb_target_group.engine_native["this"].port == 8443 && aws_lb_target_group.engine_native["this"].protocol == "TCP" &&
      aws_lb_target_group.engine_native["this"].target_type == "ip" &&
      aws_lb_listener.engine_native["this"].port == 443 && aws_lb_listener.engine_native["this"].protocol == "TCP"
    )
    error_message = "D-B: NLB INTERNO, TCP 443 -> alvo ip TCP 8443, sem terminar TLS."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.engine_native_nlb_from_portal["this"].referenced_security_group_id == "sg-00000000000000001" &&
      aws_vpc_security_group_ingress_rule.engine_native_nlb_from_portal["this"].from_port == 443 &&
      aws_vpc_security_group_ingress_rule.engine_native_nlb_from_portal["this"].to_port == 443 &&
      length(aws_vpc_security_group_ingress_rule.engine_native_nlb_from_issuer) == 0 &&
      aws_vpc_security_group_ingress_rule.tasks_engine_native_from_nlb["this"].security_group_id == "sg-00000000000000010" &&
      aws_vpc_security_group_ingress_rule.tasks_engine_native_from_nlb["this"].referenced_security_group_id == "sg-00000000000000020" &&
      aws_vpc_security_group_ingress_rule.tasks_engine_native_from_nlb["this"].from_port == 8443 &&
      aws_vpc_security_group_egress_rule.engine_native_nlb_to_engine["this"].referenced_security_group_id == "sg-00000000000000010"
    )
    error_message = "443 do NLB so do SG do portal; 8443 das tasks so do SG do NLB."
  }
  assert {
    condition = (
      aws_service_discovery_service.engine_native["this"].name == "engine-native" &&
      aws_service_discovery_service.engine_native["this"].dns_config[0].routing_policy == "WEIGHTED" &&
      output.engine_native_origin == "https://engine-native.maezo-operadora-dev.internal" &&
      output.engine_native_nlb_sg_id == "sg-00000000000000020"
    )
    error_message = "Registro na zona do Cloud Map (sem zona nova) e outputs para o portal."
  }
  assert {
    condition = (
      [for c in jsondecode(aws_ecs_task_definition.cibseven.container_definitions) : c.name] == ["cibseven", "engine-native-materialize"] &&
      jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[0].dependsOn == [{ containerName = "engine-native-materialize", condition = "SUCCESS" }] &&
      jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[0].mountPoints == [
        { sourceVolume = "engine-native", containerPath = "/run/maezo/native", readOnly = true },
        { sourceVolume = "engine-run", containerPath = "/run/maezo/engine", readOnly = true },
      ] &&
      [for p in jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[0].portMappings : p.containerPort] == [8080, 8443] &&
      !jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[1].essential &&
      endswith(jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[1].command[2], "exec python -m maezo.platform.engine_native_materialize") &&
      jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[1].secrets == [{ name = "MAEZO_ENGINE_NATIVE_SECRET", valueFrom = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-abcdef:::00000000-0000-0000-0000-000000000001" }] &&
      jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[1].image == "203312548462.dkr.ecr.sa-east-1.amazonaws.com/test/app@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc" &&
      [for v in aws_ecs_task_definition.cibseven.volume : v.name] == ["engine-native", "engine-run"]
    )
    error_message = "Init materializa o segredo pinado; o engine monta os volumes read-only e expoe 8443."
  }
  assert {
    condition = { for e in jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[0].environment : e.name => e.value if startswith(e.name, "MAEZO_") || startswith(e.name, "DB_") } == {
      DB_DRIVER                       = "org.postgresql.Driver"
      DB_URL                          = "jdbc:postgresql://db.test:5432/maezo?currentSchema=maezo_native,cibseven"
      DB_SCHEMA_UPDATE                = "true"
      DB_HOST                         = "db.test"
      DB_PORT                         = "5432"
      DB_NAME                         = "maezo"
      MAEZO_HUMAN_TRUST_FILE          = "/run/maezo/engine/trust.json"
      MAEZO_PORTAL_READ_TRUST_FILE    = "/run/maezo/engine/portal-read-trust.json"
      MAEZO_PORTAL_READ_PROVIDER_FILE = "/run/maezo/engine/portal-read-provider.json"
      MAEZO_STAFF_COMPOSITION_FILE    = "/run/maezo/engine/staff/staff-composition.json"
    }
    error_message = "Env do engine nativo: os nomes do harness C1 e o path maezo_native,cibseven."
  }
  assert {
    condition = (
      length(aws_ecs_service.cibseven.load_balancer) == 1 &&
      one(aws_ecs_service.cibseven.load_balancer).container_name == "cibseven" &&
      one(aws_ecs_service.cibseven.load_balancer).container_port == 8443
    )
    error_message = "O servico registra o container cibseven:8443 no target group nativo."
  }
}

run "ligado_com_emissor" {
  command = apply
  plan_options { target = [aws_ecs_service.cibseven, aws_vpc_security_group_ingress_rule.engine_native_nlb_from_issuer] }
  variables {
    engine_native = {
      native_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-abcdef"
      native_secret_version_id = "00000000-0000-0000-0000-000000000001"
      kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      app_image_digest         = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      staff_case_issuer        = true
    }
  }
  assert {
    condition = (
      [for c in jsondecode(aws_ecs_task_definition.cibseven.container_definitions) : c.name] == ["cibseven", "engine-native-materialize", "staff-case-issuer"] &&
      !jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[2].essential &&
      jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[2].mountPoints == [{ sourceVolume = "staff-issuer", containerPath = "/run/maezo/staff-issuer", readOnly = true }, { sourceVolume = "staff-issuer-tmp", containerPath = "/run/maezo/staff-issuer-scratch", readOnly = false }] &&
      jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[2].environment[0] == { name = "MAEZO_STAFF_CASE_ISSUER_FILE", value = "/run/maezo/staff-issuer/composition.json" } &&
      [for v in aws_ecs_task_definition.cibseven.volume : v.name] == ["engine-native", "engine-run", "staff-issuer", "staff-issuer-tmp"] &&
      aws_vpc_security_group_ingress_rule.engine_native_nlb_from_issuer["this"].referenced_security_group_id == "sg-00000000000000010"
    )
    error_message = "Sidecar do emissor: nao essencial, composicao read-only do mesmo init, 443 do SG das tasks."
  }
}

run "recusa_chave_extra" {
  command = plan
  plan_options { target = [aws_ecs_service.cibseven] }
  variables {
    engine_native = {
      native_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-abcdef"
      native_secret_version_id = "00000000-0000-0000-0000-000000000001"
      kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      app_image_digest         = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      staff_case_issuer        = false
      extra                    = "x"
    }
  }
  expect_failures = [var.engine_native]
}

run "recusa_segredo_de_outro_nome" {
  command = plan
  plan_options { target = [aws_ecs_service.cibseven] }
  variables {
    engine_native = {
      native_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      native_secret_version_id = "00000000-0000-0000-0000-000000000001"
      kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      app_image_digest         = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      staff_case_issuer        = false
    }
  }
  expect_failures = [var.engine_native]
}

run "recusa_sem_portal" {
  command = plan
  plan_options { target = [aws_ecs_service.cibseven] }
  variables {
    portal = null
    engine_native = {
      native_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-abcdef"
      native_secret_version_id = "00000000-0000-0000-0000-000000000001"
      kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      app_image_digest         = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      staff_case_issuer        = false
    }
  }
  expect_failures = [var.engine_native]
}

# Onda 8: `assignment_trust = true` acrescenta SO o arquivo do trust de atribuicao ao env do engine.
run "ligado_com_plano_de_atribuicao" {
  command = apply
  plan_options { target = [aws_ecs_service.cibseven] }
  variables {
    engine_native = {
      native_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-abcdef"
      native_secret_version_id = "00000000-0000-0000-0000-000000000001"
      kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      app_image_digest         = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      staff_case_issuer        = false
      assignment_trust         = true
    }
  }
  assert {
    condition = { for e in jsondecode(aws_ecs_task_definition.cibseven.container_definitions)[0].environment : e.name => e.value if startswith(e.name, "MAEZO_HUMAN_") } == {
      MAEZO_HUMAN_TRUST_FILE            = "/run/maezo/engine/trust.json"
      MAEZO_HUMAN_ASSIGNMENT_TRUST_FILE = "/run/maezo/engine/assignment-trust.json"
    }
    error_message = "assignment_trust=true: o engine le o trust de atribuicao do mesmo volume engine-run."
  }
}

run "recusa_assignment_trust_nao_booleano" {
  command = plan
  plan_options { target = [aws_ecs_service.cibseven] }
  variables {
    engine_native = {
      native_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-abcdef"
      native_secret_version_id = "00000000-0000-0000-0000-000000000001"
      kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      app_image_digest         = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      staff_case_issuer        = false
      assignment_trust         = "sim"
    }
  }
  expect_failures = [var.engine_native]
}
