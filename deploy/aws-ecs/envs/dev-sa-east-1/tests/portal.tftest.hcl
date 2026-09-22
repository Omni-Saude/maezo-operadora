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
  # Mock-provider fixture only; never a deployable image qualification.
  diagnostics_image_digest = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  # Required digest variables (IMMUTABLE repos — 19/09/2026 owner decision, trivy/IaC D2
  # packet): terraform test needs a syntactically valid value for every required variable
  # even though these run blocks target only the portal graph. Synthetic, never deployable.
  worker_image_digest = "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  agents_image_digest = "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  engine_image_digest = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  tenant_id           = "portaltest"
  # PINADO AQUI de proposito. Sem esta linha `portal_enabled` vinha de
  # `portal.auto.tfvars`, que e' o arquivo do AMBIENTE VIVO — ou seja, o
  # significado desta suite mudava quando alguem ligava o portal em dev. E mudava
  # para quebrado: `disabled_by_default` zera `var.portal`, e a regra de validacao
  # de `portal-variables.tf:7` recusa `portal_enabled = true` com `portal = null`.
  # O teste morria em "Invalid value for variable" antes de asserir coisa alguma
  # (0 passed, 1 failed, 31 skipped — medido no branch que ligou o portal). Um
  # teste tem de declarar as proprias precondicoes; herda-las do estado de
  # producao e' o contrario de um teste.
  portal_enabled         = false
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
      MAEZO_PORTAL_CAPABILITIES      = "identity"
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

# Synthetic staff deployment metadata only. No bundle/private value enters Terraform.
override_data {
  target = data.aws_secretsmanager_secret.portal_staff["this"]
  values = { kms_key_id = "alias/test-staff-material" }
}
override_data {
  target = data.aws_kms_key.portal_staff["this"]
  values = { arn = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111" }
}
override_data {
  target = data.aws_security_group.portal_staff_native_https["this"]
  values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000004", vpc_id = "vpc-00000000000000001" }
}
override_data {
  target = data.aws_security_group.portal_staff_native_database["this"]
  values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000005", vpc_id = "vpc-00000000000000001" }
}
run "staff_material_delivery" {
  command = apply
  plan_options { target = [aws_ecs_service.portal, aws_iam_role_policy.portal_execution, aws_vpc_security_group_egress_rule.portal_staff_native_https, aws_vpc_security_group_egress_rule.portal_staff_native_database] }
  variables {
    portal = merge(var.portal, { staff = {
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000005"
      native_database_port              = 5432
    } })
  }
  assert {
    condition = (
      aws_ecs_service.portal["this"].desired_count == 0 &&
      length(jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)) == 2 &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].image == jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1].image &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].image == "${aws_ecr_repository.app.repository_url}@${var.portal.staff.portal_image_digest}" &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].dependsOn == [{ containerName = "portal-staff-materialize", condition = "SUCCESS" }] &&
      aws_ecs_task_definition.portal["this"].ephemeral_storage[0].size_in_gib == 21 &&
      toset([for v in aws_ecs_task_definition.portal["this"].volume : v.name]) == toset(["staff-materials", "staff-scratch"])
    )
    error_message = "Prepared staff task must use one immutable image, init SUCCESS, exact volumes and finite shared disk without activation."
  }
  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].mountPoints == [
        { sourceVolume = "staff-materials", containerPath = "/run/maezo-staff-materials", readOnly = true },
        { sourceVolume = "staff-scratch", containerPath = "/run/maezo-staff-scratch", readOnly = false }
      ] &&
      alltrue([for c in jsondecode(aws_ecs_task_definition.portal["this"].container_definitions) : c.user == "1000:1000" && c.readonlyRootFilesystem && c.linuxParameters.capabilities.drop == ["ALL"]]) &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].secrets == [{ name = "MAEZO_PORTAL_DATABASE_URL", valueFrom = var.portal.database_secret_arn }]
    )
    error_message = "Main BFF must retain only identity DSN, read-only material, writable fixed scratch and nonroot/no-capabilities posture."
  }
  assert {
    condition = (
      !jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1].essential &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1].command == ["python", "-m", "maezo.gateway.staff_cases.materialize"] &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1].mountPoints == [{ sourceVolume = "staff-materials", containerPath = "/run/maezo-staff-materials", readOnly = false }] &&
      jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1].secrets == [{ name = "MAEZO_PORTAL_STAFF_SECRET_BUNDLE", valueFrom = "${var.portal.staff.material_secret_arn}:::${var.portal.staff.material_secret_version_id}" }] &&
      !contains(keys(jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1]), "logConfiguration") &&
      !contains(keys(jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1]), "portMappings") &&
      !contains(keys(jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1]), "restartPolicy")
    )
    error_message = "Only the nonessential finite materializer may receive the exact secret version, with no logs, listener or automatic restart."
  }
  assert {
    condition = (
      { for item in jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].environment : item.name => item.value if startswith(item.name, "MAEZO_PORTAL_STAFF_") } ==
      { for item in jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[1].environment : item.name => item.value if startswith(item.name, "MAEZO_PORTAL_STAFF_") } &&
      { for item in jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].environment : item.name => item.value if startswith(item.name, "MAEZO_PORTAL_STAFF_") } == {
        MAEZO_PORTAL_STAFF_MATERIAL_DIRECTORY          = "/run/maezo-staff-materials/current"
        MAEZO_PORTAL_STAFF_MATERIAL_VERSION_ID         = var.portal.staff.material_secret_version_id
        MAEZO_PORTAL_STAFF_PUBLIC_MANIFEST_SHA256      = var.portal.staff.public_manifest_sha256
        MAEZO_PORTAL_STAFF_ROOT_KEY_SHA256             = var.portal.staff.root_key_sha256
        MAEZO_PORTAL_STAFF_DESIGNATION_SHA256          = var.portal.staff.designation_sha256
        MAEZO_PORTAL_STAFF_NATIVE_CONFIGURATION_SHA256 = var.portal.staff.native_configuration_sha256
        MAEZO_PORTAL_STAFF_SCOPE                       = jsonencode(var.portal.staff.scope)
        MAEZO_PORTAL_STAFF_NATIVE_ORIGIN               = var.portal.staff.native_origin
        MAEZO_PORTAL_STAFF_NATIVE_SERVER_SPKI_SHA256   = var.portal.staff.native_server_spki_sha256
        MAEZO_PORTAL_STAFF_READ_KEY_SHA256             = var.portal.staff.read_key_sha256
        MAEZO_PORTAL_STAFF_WITNESS_KEY_SHA256          = var.portal.staff.witness_key_sha256
        MAEZO_PORTAL_STAFF_MAXIMUM_SECONDS             = tostring(var.portal.staff.maximum_seconds)
      } &&
      one([for item in jsondecode(aws_ecs_task_definition.portal["this"].container_definitions)[0].environment : item.value if item.name == "TMPDIR"]) == "/run/maezo-staff-scratch" &&
      alltrue([for c in jsondecode(aws_ecs_task_definition.portal["this"].container_definitions) : one([for item in c.environment : item.value if item.name == "MAEZO_PORTAL_CAPABILITIES"]) == "identity,staff_cases"])
    )
    error_message = "Both consumers require identical exact public material pins and scope; only main uses fixed scratch."
  }
  assert {
    condition = (
      anytrue([for st in data.aws_iam_policy_document.portal_execution["this"].statement : st.actions == toset(["secretsmanager:GetSecretValue"]) && st.resources == toset([var.portal.staff.material_secret_arn])]) &&
      anytrue([for st in data.aws_iam_policy_document.portal_execution["this"].statement : st.actions == toset(["kms:Decrypt"]) && st.resources == toset([var.portal.staff.material_kms_key_arn]) && length(st.condition) == 2 &&
        anytrue([for c in st.condition : c.variable == "kms:ViaService" && c.test == "StringEquals" && toset(c.values) == toset(["secretsmanager.sa-east-1.amazonaws.com"])]) &&
        anytrue([for c in st.condition : c.variable == "kms:EncryptionContext:SecretARN" && c.test == "StringEquals" && toset(c.values) == toset([var.portal.staff.material_secret_arn])])
      ]) &&
      aws_ecs_task_definition.portal["this"].execution_role_arn == aws_iam_role.portal_execution["this"].arn &&
      aws_ecs_task_definition.portal["this"].task_role_arn == aws_iam_role.portal_task["this"].arn &&
      !aws_ecs_service.portal["this"].enable_execute_command
    )
    error_message = "Only execution role may resolve material via exact CMK/secret context; runtime role and Exec posture remain separate."
  }
  assert {
    condition = (
      aws_vpc_security_group_egress_rule.portal_staff_native_https["this"].referenced_security_group_id == var.portal.staff.native_https_security_group_id &&
      aws_vpc_security_group_egress_rule.portal_staff_native_https["this"].from_port == 443 &&
      aws_vpc_security_group_egress_rule.portal_staff_native_https["this"].to_port == 443 &&
      aws_vpc_security_group_egress_rule.portal_staff_native_database["this"].referenced_security_group_id == var.portal.staff.native_database_security_group_id &&
      aws_vpc_security_group_egress_rule.portal_staff_native_database["this"].from_port == var.portal.staff.native_database_port &&
      aws_vpc_security_group_egress_rule.portal_staff_native_database["this"].to_port == var.portal.staff.native_database_port
    )
    error_message = "Staff egress must reach only owner-specified native HTTPS and witness DB SG/ports; no owner ingress is created."
  }
}
run "staff_mutable_version_refused" {
  command = plan
  plan_options { target = [aws_ecs_task_definition.portal] }
  variables {
    portal = merge(var.portal, { staff = merge({
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000005"
      native_database_port              = 5432
    }, { material_secret_version_id = "AWSCURRENT" }) })
  }
  expect_failures = [var.portal]
}
run "staff_duplicate_signer_refused" {
  command = plan
  plan_options { target = [aws_ecs_task_definition.portal] }
  variables {
    portal = merge(var.portal, { staff = merge({
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000005"
      native_database_port              = 5432
    }, { witness_key_sha256 = "1111111111111111111111111111111111111111111111111111111111111111" }) })
  }
  expect_failures = [var.portal]
}
run "staff_unknown_field_refused" {
  command = plan
  plan_options { target = [aws_ecs_task_definition.portal] }
  variables {
    portal = merge(var.portal, { staff = merge({
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000005"
      native_database_port              = 5432
    }, { arbitrary_environment = "prohibited" }) })
  }
  expect_failures = [var.portal]
}

run "staff_reuses_exact_existing_database_egress" {
  override_data {
    target = data.aws_security_group.portal_staff_native_database["this"]
    values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000003", vpc_id = "vpc-00000000000000001" }
  }
  command = apply
  plan_options { target = [aws_ecs_service.portal, aws_vpc_security_group_egress_rule.portal_postgres, aws_vpc_security_group_egress_rule.portal_staff_native_database] }
  variables {
    portal = merge(var.portal, { staff = {
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000003"
      native_database_port              = 5432
    } })
  }
  assert {
    condition = (
      length(aws_vpc_security_group_egress_rule.portal_staff_native_database) == 0 &&
      aws_vpc_security_group_egress_rule.portal_postgres["this"].referenced_security_group_id == var.portal.staff.native_database_security_group_id &&
      aws_vpc_security_group_egress_rule.portal_postgres["this"].from_port == var.portal.staff.native_database_port &&
      data.aws_security_group.portal_staff_native_database["this"].arn == "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.aws_account_id}:security-group/${var.portal.staff.native_database_security_group_id}"
    )
    error_message = "An explicitly identical Aurora SG/port reuses its existing rule while retaining native target qualification."
  }
}

run "staff_db_reuses_https_egress" {
  override_data {
    target = data.aws_security_group.portal_staff_native_database["this"]
    values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000004", vpc_id = "vpc-00000000000000001" }
  }
  command = apply
  plan_options { target = [aws_ecs_service.portal, aws_vpc_security_group_egress_rule.portal_postgres, aws_vpc_security_group_egress_rule.portal_staff_native_database, aws_vpc_security_group_egress_rule.portal_staff_native_https] }
  variables {
    portal = merge(var.portal, { staff = {
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000004"
      native_database_port              = 443
    } })
  }
  assert {
    condition = (
      length(aws_vpc_security_group_egress_rule.portal_postgres) +
      length(aws_vpc_security_group_egress_rule.portal_staff_native_https) +
      length(aws_vpc_security_group_egress_rule.portal_staff_native_database) == 2 &&
      length(toset([for rule in concat(
        values(aws_vpc_security_group_egress_rule.portal_postgres),
        values(aws_vpc_security_group_egress_rule.portal_staff_native_https),
        values(aws_vpc_security_group_egress_rule.portal_staff_native_database)
      ) : jsonencode([rule.security_group_id, rule.referenced_security_group_id, rule.ip_protocol, rule.from_port, rule.to_port])])) == 2 &&
      data.aws_security_group.portal_staff_native_https["this"].arn == "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.aws_account_id}:security-group/${var.portal.staff.native_https_security_group_id}" &&
      data.aws_security_group.portal_staff_native_database["this"].arn == "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.aws_account_id}:security-group/${var.portal.staff.native_database_security_group_id}"
    )
    error_message = "All selected destinations retain qualified metadata and exactly one effective SG/TCP/port tuple."
  }
}

run "staff_https_reuses_aurora_egress" {
  override_data {
    target = data.aws_security_group.portal_staff_native_https["this"]
    values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000003", vpc_id = "vpc-00000000000000001" }
  }
  override_data {
    target = data.aws_rds_cluster.shared
    values = {
      endpoint               = "db.test"
      port                   = 443
      vpc_security_group_ids = ["sg-00000000000000003"]
      master_user_secret     = [{ secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:test/master-abcdef", secret_status = "active", kms_key_id = "" }]
    }
  }
  command = apply
  plan_options { target = [aws_ecs_service.portal, aws_vpc_security_group_egress_rule.portal_postgres, aws_vpc_security_group_egress_rule.portal_staff_native_database, aws_vpc_security_group_egress_rule.portal_staff_native_https] }
  variables {
    portal = merge(var.portal, { staff = {
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000003"
      native_database_security_group_id = "sg-00000000000000005"
      native_database_port              = 5432
    } })
  }
  assert {
    condition = (
      length(aws_vpc_security_group_egress_rule.portal_postgres) +
      length(aws_vpc_security_group_egress_rule.portal_staff_native_https) +
      length(aws_vpc_security_group_egress_rule.portal_staff_native_database) == 2 &&
      length(toset([for rule in concat(
        values(aws_vpc_security_group_egress_rule.portal_postgres),
        values(aws_vpc_security_group_egress_rule.portal_staff_native_https),
        values(aws_vpc_security_group_egress_rule.portal_staff_native_database)
      ) : jsonencode([rule.security_group_id, rule.referenced_security_group_id, rule.ip_protocol, rule.from_port, rule.to_port])])) == 2 &&
      data.aws_security_group.portal_staff_native_https["this"].arn == "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.aws_account_id}:security-group/${var.portal.staff.native_https_security_group_id}" &&
      data.aws_security_group.portal_staff_native_database["this"].arn == "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.aws_account_id}:security-group/${var.portal.staff.native_database_security_group_id}"
    )
    error_message = "All selected destinations retain qualified metadata and exactly one effective SG/TCP/port tuple."
  }
}

run "staff_all_destinations_share_one_egress" {
  override_data {
    target = data.aws_security_group.portal_staff_native_https["this"]
    values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000003", vpc_id = "vpc-00000000000000001" }
  }
  override_data {
    target = data.aws_security_group.portal_staff_native_database["this"]
    values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000003", vpc_id = "vpc-00000000000000001" }
  }
  override_data {
    target = data.aws_rds_cluster.shared
    values = {
      endpoint               = "db.test"
      port                   = 443
      vpc_security_group_ids = ["sg-00000000000000003"]
      master_user_secret     = [{ secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:test/master-abcdef", secret_status = "active", kms_key_id = "" }]
    }
  }
  command = apply
  plan_options { target = [aws_ecs_service.portal, aws_vpc_security_group_egress_rule.portal_postgres, aws_vpc_security_group_egress_rule.portal_staff_native_database, aws_vpc_security_group_egress_rule.portal_staff_native_https] }
  variables {
    portal = merge(var.portal, { staff = {
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000003"
      native_database_security_group_id = "sg-00000000000000003"
      native_database_port              = 443
    } })
  }
  assert {
    condition = (
      length(aws_vpc_security_group_egress_rule.portal_postgres) +
      length(aws_vpc_security_group_egress_rule.portal_staff_native_https) +
      length(aws_vpc_security_group_egress_rule.portal_staff_native_database) == 1 &&
      length(toset([for rule in concat(
        values(aws_vpc_security_group_egress_rule.portal_postgres),
        values(aws_vpc_security_group_egress_rule.portal_staff_native_https),
        values(aws_vpc_security_group_egress_rule.portal_staff_native_database)
      ) : jsonencode([rule.security_group_id, rule.referenced_security_group_id, rule.ip_protocol, rule.from_port, rule.to_port])])) == 1 &&
      data.aws_security_group.portal_staff_native_https["this"].arn == "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.aws_account_id}:security-group/${var.portal.staff.native_https_security_group_id}" &&
      data.aws_security_group.portal_staff_native_database["this"].arn == "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${var.aws_account_id}:security-group/${var.portal.staff.native_database_security_group_id}"
    )
    error_message = "All selected destinations retain qualified metadata and exactly one effective SG/TCP/port tuple."
  }
}

run "staff_refuses_foreign_security_group_owner" {
  command = plan
  plan_options { target = [aws_ecs_task_definition.portal] }
  override_data {
    target = data.aws_security_group.portal_staff_native_https["this"]
    values = { arn = "arn:aws:ec2:sa-east-1:999999999999:security-group/sg-00000000000000004", vpc_id = "vpc-00000000000000001" }
  }
  variables {
    portal = merge(var.portal, { staff = {
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000005"
      native_database_port              = 5432
    } })
  }
  expect_failures = [aws_ecs_task_definition.portal]
}

run "staff_refuses_different_security_group_identity" {
  command = plan
  plan_options { target = [aws_ecs_task_definition.portal] }
  override_data {
    target = data.aws_security_group.portal_staff_native_https["this"]
    values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000006", vpc_id = "vpc-00000000000000001" }
  }
  variables {
    portal = merge(var.portal, { staff = {
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000005"
      native_database_port              = 5432
    } })
  }
  expect_failures = [aws_ecs_task_definition.portal]
}

run "staff_refuses_native_security_group_other_vpc" {
  command = plan
  plan_options { target = [aws_ecs_task_definition.portal] }
  override_data {
    target = data.aws_security_group.portal_staff_native_https["this"]
    values = { arn = "arn:aws:ec2:sa-east-1:203312548462:security-group/sg-00000000000000004", vpc_id = "vpc-00000000000000002" }
  }
  variables {
    portal = merge(var.portal, { staff = {
      material_secret_arn               = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/portaltest/staff-materials-abcdef"
      material_secret_version_id        = "11111111-2222-3333-4444-555555555555"
      material_kms_key_arn              = "arn:aws:kms:sa-east-1:203312548462:key/11111111-1111-1111-1111-111111111111"
      portal_image_digest               = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      public_manifest_sha256            = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      root_key_sha256                   = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      designation_sha256                = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
      native_configuration_sha256       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      scope                             = { tenant = "portaltest", environment = "explicit-owner-dev", engine_name = "payer", database_incarnation = "native-incarnation-fixture" }
      native_origin                     = "https://native.example.test"
      native_server_spki_sha256         = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      read_key_sha256                   = "1111111111111111111111111111111111111111111111111111111111111111"
      witness_key_sha256                = "2222222222222222222222222222222222222222222222222222222222222222"
      maximum_seconds                   = 5
      native_https_security_group_id    = "sg-00000000000000004"
      native_database_security_group_id = "sg-00000000000000005"
      native_database_port              = 5432
    } })
  }
  expect_failures = [aws_ecs_task_definition.portal]
}
