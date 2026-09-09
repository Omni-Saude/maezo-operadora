# Configuration-only tests. All AWS APIs are mocked; never an engine/infrastructure
# integration test, and no credential, network request or real cloud apply occurs.
# Positive runs use an in-memory MOCK apply (Terraform 1.10 cannot provide computed
# resource attributes at plan time); negatives use plan. No real AWS apply occurs.
# Targets bound these configuration tests to the portal dependency graph; they are
# not a deployment recipe. In particular they do not accept/waive the unrelated
# existing agent ratification check with its pending-sha256 artifact. Full-root
# terraform validate is separate; actual un-targeted cloud plan remains required.
mock_provider "aws" {
  mock_resource "aws_lb" { defaults = { arn = "arn:aws:elasticloadbalancing:sa-east-1:203312548462:loadbalancer/net/test-portal/0000000000000000" } }
  mock_resource "aws_lb_target_group" { defaults = { arn = "arn:aws:elasticloadbalancing:sa-east-1:203312548462:targetgroup/test-portal/0000000000000000" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:sa-east-1:203312548462:log-group:test-portal" } }

  mock_data "aws_iam_policy_document" { defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" } }
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
  mock_resource "aws_ecr_repository" { defaults = { repository_url = "203312548462.dkr.ecr.sa-east-1.amazonaws.com/test/app", arn = "arn:aws:ecr:sa-east-1:203312548462:repository/test/app" } }
}
override_data {
  target = data.aws_subnet.portal_public["subnet-00000000000000001"]
  values = { vpc_id = "vpc-00000000000000001", availability_zone = "sa-east-1a" }
}
override_data {
  target = data.aws_subnet.portal_public["subnet-00000000000000002"]
  values = { vpc_id = "vpc-00000000000000001", availability_zone = "sa-east-1b" }
}
override_resource {
  target = aws_iam_role.portal_execution["this"]
  values = { "arn" : "arn:aws:iam::203312548462:role/test-portal-execution", "id" : "test-portal-execution" }
}
override_resource {
  target = aws_iam_role.portal_task["this"]
  values = { "arn" : "arn:aws:iam::203312548462:role/test-portal-task", "id" : "test-portal-task" }
}
override_resource {
  target = aws_ecs_task_definition.portal["this"]
  values = { "arn" : "arn:aws:ecs:sa-east-1:203312548462:task-definition/test-portal:1" }
}
override_resource {
  target = aws_security_group.portal["this"]
  values = { "id" : "sg-00000000000000001" }
}
override_resource {
  target = aws_security_group.portal_ingress["this"]
  values = { "id" : "sg-00000000000000002" }
}
variables {
  # Synthetic fixtures only. These are not deployed origins, credentials or image IDs.
  tenant_id              = "portaltest"
  fhir_cognito_client_id = "machine123"
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
}
run "disabled_by_default" {
  command = apply
  plan_options { target = [aws_ecs_service.portal, aws_lb.portal, aws_vpc_security_group_egress_rule.portal_https, aws_vpc_security_group_egress_rule.portal_postgres, aws_vpc_security_group_ingress_rule.portal_from_ingress] }
  variables { portal = null }
  assert {
    condition     = length(aws_ecs_service.portal) == 0 && length(aws_ecs_task_definition.portal) == 0 && length(aws_lb.portal) == 0
    error_message = "Default must create no portal workload or public ingress."
  }
}
run "prepared_not_activated" {
  command = apply
  plan_options { target = [aws_ecs_service.portal, aws_lb.portal, aws_vpc_security_group_egress_rule.portal_https, aws_vpc_security_group_egress_rule.portal_postgres, aws_vpc_security_group_ingress_rule.portal_from_ingress] }
  assert {
    condition     = aws_ecs_service.portal["this"].desired_count == 0
    error_message = "Prepare must not start the portal before external owner prerequisites."
  }
}
run "enabled_consumer" {
  command = apply
  plan_options { target = [aws_ecs_service.portal, aws_lb.portal, aws_vpc_security_group_egress_rule.portal_https, aws_vpc_security_group_egress_rule.portal_postgres, aws_vpc_security_group_ingress_rule.portal_from_ingress] }
  variables { portal_enabled = true }
  assert {
    condition = (
      aws_ecs_service.portal["this"].desired_count == 1 &&
      aws_ecs_service.portal["this"].task_definition == aws_ecs_task_definition.portal["this"].arn &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].command == ["python", "-m", "maezo.portal.api"] &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].portMappings[0].containerPort == 8080 &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].image == "${aws_ecr_repository.app.repository_url}@${var.portal.image_digest}"
    )
    error_message = "Service must run the actual portal entrypoint at port8080 from the pinned app digest."
  }
  assert {
    condition = { for item in jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].environment : item.name => item.value } == {
      MAEZO_PORTAL_TENANT            = var.tenant_id
      MAEZO_PORTAL_ISSUER            = var.portal.issuer
      MAEZO_PORTAL_COGNITO_ORIGIN    = var.portal.cognito_origin
      MAEZO_PORTAL_CLIENT_ID         = var.portal.human_client_id
      MAEZO_PORTAL_CLIENT_PURPOSE    = "dedicated-human-code-pkce"
      MAEZO_PORTAL_MACHINE_CLIENT_ID = var.fhir_cognito_client_id
      MAEZO_PORTAL_PUBLIC_ORIGIN     = var.portal.public_origin
      MAEZO_PORTAL_MODE              = "production"
      PYTHONDONTWRITEBYTECODE        = "1"
    }
    error_message = "All identity settings must feed the real consumer with production mode and fixed tenant."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].secrets == [{ name = "MAEZO_PORTAL_DATABASE_URL", valueFrom = var.portal.database_secret_arn }]
    error_message = "Only dedicated secret-backed portal DSN may be injected."
  }
  assert {
    condition = (
      aws_ecs_task_definition.portal["this"].execution_role_arn == aws_iam_role.portal_execution["this"].arn &&
      aws_ecs_task_definition.portal["this"].task_role_arn == aws_iam_role.portal_task["this"].arn &&
      aws_ecs_service.portal["this"].network_configuration[0].security_groups == toset([aws_security_group.portal["this"].id]) &&
      !aws_ecs_service.portal["this"].network_configuration[0].assign_public_ip &&
      !aws_ecs_service.portal["this"].enable_execute_command
    )
    error_message = "Portal must use only its dedicated roles/SG, without public task IP or ECS Exec."
  }
  assert {
    condition = (
      aws_lb_listener.portal["this"].protocol == "TLS" && aws_lb_listener.portal["this"].port == 443 &&
      aws_lb_target_group.portal["this"].protocol == "TCP" && aws_lb_target_group.portal["this"].port == 8080 &&
      aws_lb_target_group.portal["this"].health_check[0].protocol == "TCP" &&
      !aws_lb_target_group.portal["this"].proxy_protocol_v2 &&
      aws_vpc_security_group_ingress_rule.portal_from_ingress["this"].referenced_security_group_id == aws_security_group.portal_ingress["this"].id &&
      aws_vpc_security_group_egress_rule.portal_postgres["this"].referenced_security_group_id == local.aurora_security_group_id &&
      toset([for rule in aws_vpc_security_group_egress_rule.portal_https : rule.cidr_ipv4]) == var.portal.https_egress_ipv4_cidrs
    )
    error_message = "Actual TLS/target/SG wiring must preserve Host and deny unrelated egress."
  }
}
run "execution_permissions" {
  command = apply
  plan_options { target = [aws_iam_role_policy.portal_execution] }
  assert {
    condition = (
      length(data.aws_iam_policy_document.portal_execution["this"].statement) == 4 &&
      anytrue([for s in data.aws_iam_policy_document.portal_execution["this"].statement : s.actions == toset(["secretsmanager:GetSecretValue"]) && s.resources == toset([var.portal.database_secret_arn])]) &&
      anytrue([for s in data.aws_iam_policy_document.portal_execution["this"].statement : s.actions == toset(["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]) && s.resources == toset([aws_ecr_repository.app.arn])])
    )
    error_message = "Execution permissions must resolve exactly the dedicated secret and application repository."
  }
}
run "missing_config" {
  command = plan
  plan_options { target = [aws_ecs_service.portal, aws_lb.portal, aws_vpc_security_group_egress_rule.portal_https, aws_vpc_security_group_egress_rule.portal_postgres, aws_vpc_security_group_ingress_rule.portal_from_ingress] }
  variables {
    portal_enabled = true
    portal         = null
  }
  expect_failures = [var.portal_enabled]
}

run "wrong_purpose" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { human_client_purpose = "client-credentials" })
  }
  expect_failures = [var.portal]
}

run "same_client" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { human_client_id = "machine123" })
  }
  expect_failures = [var.portal]
}

run "wrong_machine" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { machine_client_id = "anothermachine" })
  }
  expect_failures = [var.portal]
}

run "tenant_mismatch" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { tenant = "other" })
  }
  expect_failures = [var.portal]
}

run "unsafe_public_origin" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { public_origin = "http://portal.example.test" })
  }
  expect_failures = [var.portal]
}

run "origin_path" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { cognito_origin = "https://login.example.test/path" })
  }
  expect_failures = [var.portal]
}

run "origin_userinfo" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { cognito_origin = "https://user:pass@login.example.test" })
  }
  expect_failures = [var.portal]
}

run "origin_fragment" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { public_origin = "https://portal.example.test#" })
  }
  expect_failures = [var.portal]
}

run "wrong_issuer" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { issuer = "https://cognito-idp.us-east-1.amazonaws.com/sa-east-1_TestPool" })
  }
  expect_failures = [var.portal]
}

run "missing_dsn" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { database_secret_arn = "" })
  }
  expect_failures = [var.portal]
}

run "cross_tenant_dsn" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { database_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/other/session-dsn-abcdef" })
  }
  expect_failures = [var.portal]
}

run "mutable_image" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { image_digest = "latest" })
  }
  expect_failures = [var.portal]
}

run "universal_egress" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { https_egress_ipv4_cidrs = ["0.0.0.0/0"] })
  }
  expect_failures = [var.portal]
}

run "empty_egress" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { https_egress_ipv4_cidrs = [] })
  }
  expect_failures = [var.portal]
}

run "ipv6_universal" {
  command = plan
  plan_options { target = [aws_ecs_service.portal] }
  variables {
    portal_enabled = true
    portal         = merge(var.portal, { https_egress_ipv4_cidrs = ["::/0"] })
  }
  expect_failures = [var.portal]
}

run "cmk_scoped_secret_context" {
  command = apply
  plan_options { target = [aws_iam_role_policy.portal_execution] }
  override_data {
    target = data.aws_secretsmanager_secret.portal_session["this"]
    values = { kms_key_id = "alias/test-portal" }
  }
  override_data {
    target = data.aws_kms_key.portal_session["this"]
    values = { arn = "arn:aws:kms:sa-east-1:203312548462:key/00000000-0000-0000-0000-000000000000" }
  }
  assert {
    condition = anytrue([for s in data.aws_iam_policy_document.portal_execution["this"].statement :
      s.actions == toset(["kms:Decrypt"]) &&
      s.resources == toset(["arn:aws:kms:sa-east-1:203312548462:key/00000000-0000-0000-0000-000000000000"]) &&
      length(s.condition) == 2 &&
      anytrue([for c in s.condition : c.test == "StringEquals" && c.variable == "kms:EncryptionContext:SecretARN" && toset(c.values) == toset([var.portal.database_secret_arn])]) &&
      anytrue([for c in s.condition : c.test == "StringEquals" && c.variable == "kms:ViaService" && toset(c.values) == toset(["secretsmanager.sa-east-1.amazonaws.com"])])
    ])
    error_message = "CMK must resolve to exact ARN and restrict decrypt to this secret through regional Secrets Manager."
  }
}
