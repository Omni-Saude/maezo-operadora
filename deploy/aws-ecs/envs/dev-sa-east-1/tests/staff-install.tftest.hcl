# Testes de CONFIGURACAO da task avulsa `staff-install` (task-staff-install.tf). AWS toda mockada:
# nenhuma credencial, rede ou apply real. O apply e o MOCK em memoria do Terraform.
mock_provider "aws" {
  mock_data "aws_iam_policy_document" { defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_rds_cluster" {
    defaults = {
      endpoint               = "db.test"
      port                   = 5432
      vpc_security_group_ids = ["sg-00000000000000003"]
      master_user_secret     = [{ secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:rds!cluster-test-abcdef", secret_status = "active", kms_key_id = "" }]
    }
  }
  mock_data "aws_secretsmanager_secret" { defaults = { kms_key_id = "" } }
  mock_resource "aws_ecr_repository" { defaults = { repository_url = "203312548462.dkr.ecr.sa-east-1.amazonaws.com/test/app", arn = "arn:aws:ecr:sa-east-1:203312548462:repository/test/app" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::203312548462:role/test-role" } }
}

variables {
  # Fixtures sinteticas, nunca implantaveis.
  diagnostics_image_digest = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  worker_image_digest      = "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  agents_image_digest      = "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  engine_image_digest      = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  fhir_cognito_client_id   = "machine123"
  portal_enabled           = false
  portal                   = null
}

run "null_by_default_is_a_no_op" {
  command = plan
  plan_options {
    target = [aws_ecs_task_definition.staff_install, aws_iam_role_policy.staff_install]
  }
  assert {
    condition     = length(aws_ecs_task_definition.staff_install) == 0 && length(aws_iam_role.staff_install) == 0 && length(data.aws_secretsmanager_secret.staff_install) == 0
    error_message = "staff_install = null tem de ser NO-OP."
  }
}

run "enabled_reads_only_the_exact_secrets" {
  command = apply
  plan_options {
    target = [aws_ecs_task_definition.staff_install, aws_iam_role_policy.staff_install]
  }
  variables {
    staff_install = {
      image_digest = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
      login_secret_arns = {
        maezo_native_schema_owner   = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/db/native/maezo_native_schema_owner-AAAAAA"
        maezo_native_case_issuer    = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/db/native/maezo_native_case_issuer-BBBBBB"
        maezo_native_issuer_witness = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/db/native/maezo_native_issuer_witness-CCCCCC"
        portal_read_source_amh      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/db/native/portal_read_source_amh-DDDDDD"
        portal_staff_lock_amh       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-install/portal_staff_lock_amh-EEEEEE"
        portal_staff_witness_amh    = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-install/portal_staff_witness_amh-FFFFFF"
      }
    }
  }
  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.staff_install["this"].container_definitions)[0].image == "203312548462.dkr.ecr.sa-east-1.amazonaws.com/test/app@sha256:1111111111111111111111111111111111111111111111111111111111111111" &&
      !can(jsondecode(aws_ecs_task_definition.staff_install["this"].container_definitions)[0].secrets) &&
      !can(jsondecode(aws_ecs_task_definition.staff_install["this"].container_definitions)[0].command) &&
      jsondecode(aws_ecs_task_definition.staff_install["this"].container_definitions)[0].readonlyRootFilesystem
    )
    error_message = "A task roda a imagem por digest, sem `secrets`/command: o instalador busca os segredos pela API."
  }
  assert {
    condition     = aws_ecs_task_definition.staff_install["this"].task_role_arn == aws_iam_role.staff_install["this"].arn
    error_message = "Task role propria, nunca a `task` compartilhada pelos services."
  }
  assert {
    condition = (
      length(data.aws_iam_policy_document.staff_install["this"].statement) == 1 &&
      toset(data.aws_iam_policy_document.staff_install["this"].statement[0].actions) == toset(["secretsmanager:GetSecretValue"]) &&
      length(data.aws_iam_policy_document.staff_install["this"].statement[0].resources) == 7 &&
      contains(data.aws_iam_policy_document.staff_install["this"].statement[0].resources, "arn:aws:secretsmanager:sa-east-1:203312548462:secret:rds!cluster-test-abcdef")
    )
    error_message = "GetSecretValue so nos 7 ARNs exatos (mestre + 6 logins); sem kms com a chave padrao."
  }
}

run "refuses_missing_login_and_tag" {
  command = plan
  plan_options {
    target = [aws_ecs_task_definition.staff_install]
  }
  variables {
    staff_install = {
      image_digest = "latest"
      login_secret_arns = {
        maezo_native_schema_owner = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/db/native/maezo_native_schema_owner-AAAAAA"
      }
    }
  }
  expect_failures = [var.staff_install]
}
