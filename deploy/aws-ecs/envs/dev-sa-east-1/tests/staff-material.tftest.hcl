# Testes de CONFIGURACAO da task avulsa `staff-material` (task-staff-material.tf, DL-0113). AWS toda mockada:
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
    target = [aws_ecs_task_definition.staff_material, aws_s3_bucket.staff_material, aws_kms_key.staff_material, aws_iam_role.staff_material]
  }
  assert {
    condition = (
      length(aws_ecs_task_definition.staff_material) == 0 &&
      length(aws_s3_bucket.staff_material) == 0 &&
      length(aws_kms_key.staff_material) == 0 &&
      length(aws_iam_role.staff_material) == 0
    )
    error_message = "staff_material = null tem de ser NO-OP (DL-0113: inerte ate ligar)."
  }
}

run "sem_image_digest_so_a_infraestrutura" {
  command = plan
  plan_options {
    target = [aws_ecs_task_definition.staff_material, aws_s3_bucket.staff_material, aws_kms_key.staff_material, aws_iam_role.staff_material]
  }
  variables {
    staff_material = {
      native_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-AAAAAA"
      staff_secret_arn      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-materials-BBBBBB"
      job_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-job/materials-CCCCCC"
      rows_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/rows-DDDDDD"
      assignment_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-EEEEEE"
    }
  }
  assert {
    condition = (
      length(aws_s3_bucket.staff_material) == 1 &&
      length(aws_kms_key.staff_material) == 1 &&
      length(aws_iam_role.staff_material) == 1 &&
      length(aws_ecs_task_definition.staff_material) == 0
    )
    error_message = "sem image_digest: bucket, CMK e role existem; a task definition NAO."
  }
}

run "com_image_digest_nasce_a_task_definition" {
  command = plan
  plan_options {
    target = [aws_ecs_task_definition.staff_material, aws_s3_bucket.staff_material]
  }
  variables {
    staff_material = {
      image_digest          = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
      native_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/engine/native-materials-AAAAAA"
      staff_secret_arn      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-materials-BBBBBB"
      job_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-job/materials-CCCCCC"
      rows_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/rows-DDDDDD"
      assignment_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-EEEEEE"
    }
  }
  assert {
    condition     = length(aws_ecs_task_definition.staff_material) == 1
    error_message = "com image_digest a task definition `staff-material` tem de existir."
  }
}

run "arn_fora_do_nome_exato_e_recusado" {
  command = plan
  plan_options {
    target = [aws_s3_bucket.staff_material]
  }
  variables {
    staff_material = {
      native_secret_arn     = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:outro-segredo-AAAAAA"
      staff_secret_arn      = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-materials-BBBBBB"
      job_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-job/materials-CCCCCC"
      rows_secret_arn       = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/rows-DDDDDD"
      assignment_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/staff-install/assignment-EEEEEE"
    }
  }
  expect_failures = [var.staff_material]
}
