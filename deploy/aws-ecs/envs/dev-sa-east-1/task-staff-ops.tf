# ---------------------------------------------------------------------------
# Operacao do plano staff (Ondas 4-7, dev): tres tasks na imagem de operacao
# (`deploy/ops/staff-install.Dockerfile`, `python -m tools.staff_ops <comando>`).
#
#   staff-rows  avulsa: designacao instalada + admissao Q2, como maezo_native_schema_owner
#   staff-syn   avulsa: fixture sintetica SYN- (runbook portal-dev-provisionamento §10)
#   staff-job   AGENDADA a cada 4 min (EventBridge Scheduler): job T1.5 de publicacao, com
#               ledger duravel em S3 versionado e alarme de 2 rodadas seguidas sem sucesso
#
# Segredos: todos sob a CMK D-G `engine-native` (kms-native-materials.tf), criados FORA do
# Terraform pelo caminho sancionado. Nenhum valor passa por environment/containerOverrides:
# rows/syn leem por GetSecretValue (task role, ARN exato); o job recebe o seu pelo `secrets`
# do ECS, versao IMUTAVEL, so no init container.
# null (default) = nada e criado.
# ---------------------------------------------------------------------------

variable "staff_ops" {
  description = "Tasks de operacao do plano staff. null = nada. image_digest = imagem de operacao; *_secret_arn sob a CMK engine-native; job_* = pins publicos do pacote humano."
  type = object({
    image_digest              = string
    rows_secret_arn           = string
    syn_secret_arn            = string
    job_secret_arn            = string
    job_secret_version_id     = string
    human_material_version_id = string
    human_manifest_sha256     = string
    job_schedule_enabled      = bool
  })
  default = null

  validation {
    condition = var.staff_ops == null ? true : (
      can(regex("^sha256:[0-9a-f]{64}$", var.staff_ops.image_digest)) &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/staff-install/rows-[A-Za-z0-9]{6}$", var.staff_ops.rows_secret_arn)) &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/staff-install/syn-fixture-[A-Za-z0-9]{6}$", var.staff_ops.syn_secret_arn)) &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/staff-job/materials-[A-Za-z0-9]{6}$", var.staff_ops.job_secret_arn)) &&
      can(regex("^[A-Za-z0-9-]{32,64}$", var.staff_ops.job_secret_version_id)) &&
      can(regex("^[A-Za-z0-9-]{1,64}$", var.staff_ops.human_material_version_id)) &&
      can(regex("^[0-9a-f]{64}$", var.staff_ops.human_manifest_sha256))
    )
    error_message = "staff_ops: digest sha256, os 3 ARNs nos nomes exatos desta conta/regiao, versionId e pins do pacote humano."
  }
  validation {
    condition     = var.staff_ops == null || var.staff_install != null
    error_message = "staff_ops reutiliza as credenciais de login da Onda 3 (var.staff_install)."
  }
}

locals {
  staff_ops           = var.staff_ops == null ? {} : { this = var.staff_ops }
  staff_ops_schedule  = { for key, ops in local.staff_ops : key => ops if ops.job_schedule_enabled }
  staff_ops_image     = var.staff_ops == null ? null : "${aws_ecr_repository.app.repository_url}@${var.staff_ops.image_digest}"
  staff_ops_owner_arn = var.staff_install == null ? null : var.staff_install.login_secret_arns["maezo_native_schema_owner"]
  staff_ops_cmk_arn   = aws_kms_key.native_materials["engine-native"].arn
}

resource "aws_cloudwatch_log_group" "staff_ops" {
  for_each          = var.staff_ops == null ? toset([]) : toset(["staff-rows", "staff-syn", "staff-job"])
  name              = "/ecs/${local.name}/${each.value}"
  retention_in_days = 30
  tags              = local.base_tags
}

data "aws_kms_key" "staff_ops_master" {
  for_each = var.staff_ops == null ? {} : { for k, s in { admin = data.aws_secretsmanager_secret.staff_install["admin"] } : k => s.kms_key_id if try(length(s.kms_key_id), 0) > 0 }
  key_id   = each.value
}

# ------------------------------------------------------------------ task roles
resource "aws_iam_role" "staff_ops" {
  for_each           = var.staff_ops == null ? toset([]) : toset(["rows", "syn", "job"])
  name               = "${local.name}-staff-${each.value}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.base_tags
}

locals {
  staff_ops_task_secrets = var.staff_ops == null ? {} : {
    rows = [var.staff_ops.rows_secret_arn, local.staff_ops_owner_arn, local.aurora_master_secret_arn]
    syn  = [var.staff_ops.syn_secret_arn, local.staff_ops_owner_arn]
  }
}

data "aws_iam_policy_document" "staff_ops_task" {
  for_each = local.staff_ops_task_secrets
  statement {
    sid       = "LerSegredosExatos"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = sort(each.value)
  }
  statement {
    sid       = "DecifrarCMKEngineNative"
    actions   = ["kms:Decrypt"]
    resources = [local.staff_ops_cmk_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
  dynamic "statement" {
    for_each = each.key == "rows" ? data.aws_kms_key.staff_ops_master : {}
    content {
      sid       = "DecifrarSegredoMestre"
      actions   = ["kms:Decrypt"]
      resources = [statement.value.arn]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
      }
    }
  }
}

resource "aws_iam_role_policy" "staff_ops_task" {
  for_each = local.staff_ops_task_secrets
  name     = "${local.name}-staff-${each.key}-secrets"
  role     = aws_iam_role.staff_ops[each.key].id
  policy   = data.aws_iam_policy_document.staff_ops_task[each.key].json
}

# O job: so o ledger no S3 (o segredo dele chega pelo `secrets` do ECS, na execution role).
data "aws_iam_policy_document" "staff_job_task" {
  for_each = local.staff_ops
  statement {
    sid       = "LedgerDoJob"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.staff_job_ledger[each.key].arn}/ledger/*"]
  }
  statement {
    sid       = "LedgerAusenteENoSuchKey"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.staff_job_ledger[each.key].arn]
  }
}

resource "aws_iam_role_policy" "staff_job_task" {
  for_each = local.staff_ops
  name     = "${local.name}-staff-job-ledger"
  role     = aws_iam_role.staff_ops["job"].id
  policy   = data.aws_iam_policy_document.staff_job_task[each.key].json
}

data "aws_iam_policy_document" "task_execution_staff_job" {
  for_each = local.staff_ops
  statement {
    sid       = "LerSegredoDoJob"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [each.value.job_secret_arn]
  }
  statement {
    sid       = "DecifrarSegredoDoJob"
    actions   = ["kms:Decrypt"]
    resources = [local.staff_ops_cmk_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:SecretARN"
      values   = [each.value.job_secret_arn]
    }
  }
}

resource "aws_iam_role_policy" "task_execution_staff_job" {
  for_each = local.staff_ops
  name     = "${local.name}-staff-job-secret"
  role     = aws_iam_role.task_execution.id
  policy   = data.aws_iam_policy_document.task_execution_staff_job[each.key].json
}

# ------------------------------------------------------------------ ledger S3
resource "aws_s3_bucket" "staff_job_ledger" {
  for_each      = local.staff_ops
  bucket        = "${local.name}-staff-job-ledger-${var.aws_account_id}"
  force_destroy = false
  # SCP `require-mandatory-tags` (p-bb3h9tps) nega CreateBucket sem estas cinco tags.
  tags = merge(local.base_tags, {
    tenant      = "amh"
    environment = "dev"
    workload    = "maezo-staff-job-ledger"
    owner       = "data-platform@amh.health"
    cost_center = "amh-data-dev"
  })
}

resource "aws_s3_bucket_versioning" "staff_job_ledger" {
  for_each = local.staff_ops
  bucket   = aws_s3_bucket.staff_job_ledger[each.key].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "staff_job_ledger" {
  for_each = local.staff_ops
  bucket   = aws_s3_bucket.staff_job_ledger[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "staff_job_ledger" {
  for_each                = local.staff_ops
  bucket                  = aws_s3_bucket.staff_job_ledger[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "staff_job_ledger" {
  for_each = local.staff_ops
  bucket   = aws_s3_bucket.staff_job_ledger[each.key].id
  rule {
    id     = "versoes-antigas-30d"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# ------------------------------------------------------------------ task definitions
locals {
  staff_ops_common = {
    readonlyRootFilesystem = true
    user                   = "1000:1000"
  }
}

resource "aws_ecs_task_definition" "staff_ops_oneshot" {
  for_each                 = var.staff_ops == null ? {} : { rows = "staff-rows", syn = "staff-syn" }
  family                   = "${local.name}-${each.value}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.staff_ops[each.key].arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  dynamic "volume" {
    for_each = each.key == "syn" ? ["dev-syn"] : []
    content {
      name = volume.value
    }
  }

  container_definitions = jsonencode([merge(local.staff_ops_common, {
    name        = each.value
    image       = local.staff_ops_image
    essential   = true
    command     = ["tools.staff_ops", each.key]
    mountPoints = each.key == "syn" ? [{ sourceVolume = "dev-syn", containerPath = "/run/dev-syn", readOnly = false }] : []
    environment = concat(local.db_env, [
      { name = "AWS_REGION", value = var.aws_region },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
      { name = "STAFF_OWNER_SECRET_ARN", value = local.staff_ops_owner_arn },
      ], each.key == "rows" ? [
      { name = "STAFF_ROWS_SECRET_ARN", value = var.staff_ops.rows_secret_arn },
      { name = "STAFF_ADMIN_SECRET_ARN", value = local.aurora_master_secret_arn },
      ] : [
      { name = "STAFF_SYN_SECRET_ARN", value = var.staff_ops.syn_secret_arn },
      # Cercas da ferramenta (explicitas, sem default): so esta conta e so dev.
      { name = "MAEZO_DEV_SYN_AWS_ACCOUNT_ID", value = var.aws_account_id },
      { name = "MAEZO_DEV_SYN_ENVIRONMENT", value = "dev" },
    ])
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.staff_ops[each.value].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = each.value
      }
    }
  })])

  tags = local.base_tags
}

resource "aws_ecs_task_definition" "staff_job" {
  for_each                 = local.staff_ops
  family                   = "${local.name}-staff-job"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.staff_ops["job"].arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  volume { name = "staff-job-human" }
  volume { name = "staff-job-files" }
  volume { name = "staff-job-ledger" }

  container_definitions = jsonencode([
    {
      name                   = "staff-job-init"
      image                  = local.staff_ops_image
      essential              = false
      command                = ["tools.staff_ops", "job-init"]
      user                   = "0:0" # so para o fchown -> 1000; resto das capabilities cai
      readonlyRootFilesystem = true
      linuxParameters = { capabilities = { drop = [
        "AUDIT_WRITE", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD", "NET_BIND_SERVICE",
        "NET_RAW", "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT"
      ] } }
      mountPoints = [
        { sourceVolume = "staff-job-human", containerPath = "/run/staff-job-init/human", readOnly = false },
        { sourceVolume = "staff-job-files", containerPath = "/run/staff-job-init/job", readOnly = false },
      ]
      environment = [{ name = "PYTHONDONTWRITEBYTECODE", value = "1" }]
      # SecretString inteiro, versao IMUTAVEL.
      secrets = [{ name = "STAFF_JOB_MATERIALS", valueFrom = "${each.value.job_secret_arn}:::${each.value.job_secret_version_id}" }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.staff_ops["staff-job"].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "staff-job-init"
        }
      }
    },
    merge(local.staff_ops_common, {
      name      = "staff-job"
      image     = local.staff_ops_image
      essential = true
      command   = ["tools.staff_ops", "job-run"]
      dependsOn = [{ containerName = "staff-job-init", condition = "SUCCESS" }]
      mountPoints = [
        { sourceVolume = "staff-job-human", containerPath = "/run/maezo-human-materials", readOnly = true },
        { sourceVolume = "staff-job-files", containerPath = "/run/maezo-job", readOnly = true },
        { sourceVolume = "staff-job-ledger", containerPath = "/run/staff-job-ledger", readOnly = false },
      ]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
        { name = "STAFF_JOB_LEDGER_BUCKET", value = aws_s3_bucket.staff_job_ledger[each.key].bucket },
        { name = "STAFF_JOB_LEDGER_KEY", value = "ledger/amh.json" },
        { name = "MAEZO_HUMAN_MATERIAL_VERSION_ID", value = each.value.human_material_version_id },
        { name = "MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256", value = each.value.human_manifest_sha256 },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.staff_ops["staff-job"].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "staff-job"
        }
      }
    }),
  ])

  tags = local.base_tags
}

# ------------------------------------------------------------------ agenda (4 min)
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_iam_role" "staff_job_scheduler" {
  for_each           = local.staff_ops
  name               = "${local.name}-staff-job-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = local.base_tags
}

data "aws_iam_policy_document" "staff_job_scheduler" {
  for_each = local.staff_ops
  statement {
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:task-definition/${aws_ecs_task_definition.staff_job[each.key].family}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task_execution.arn, aws_iam_role.staff_ops["job"].arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "staff_job_scheduler" {
  for_each = local.staff_ops
  name     = "${local.name}-staff-job-scheduler"
  role     = aws_iam_role.staff_job_scheduler[each.key].id
  policy   = data.aws_iam_policy_document.staff_job_scheduler[each.key].json
}

resource "aws_scheduler_schedule" "staff_job" {
  for_each   = local.staff_ops_schedule
  name       = "${local.name}-staff-job"
  group_name = "default"
  # CADENCIA (membership_publication_job.py): < min(observation_seconds=600, catalog valid)/2.
  schedule_expression = "rate(4 minutes)"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.staff_job_scheduler[each.key].arn
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.staff_job[each.key].arn
      launch_type         = "FARGATE"
      task_count          = 1
      network_configuration {
        subnets          = data.aws_subnets.private_app.ids
        security_groups  = [aws_security_group.tasks.id]
        assign_public_ip = false
      }
    }
    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 120
    }
  }
}

# ------------------------------------------------------------------ alarme: 2 rodadas seguidas sem sucesso
resource "aws_cloudwatch_log_metric_filter" "staff_job_ok" {
  for_each       = local.staff_ops
  name           = "${local.name}-staff-job-ok"
  log_group_name = aws_cloudwatch_log_group.staff_ops["staff-job"].name
  pattern        = "\"T15_RESULT ok=true\""
  metric_transformation {
    name          = "StaffJobSuccess"
    namespace     = "maezo-operadora/dev/staff"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_sns_topic" "staff_alerts" {
  for_each = local.staff_ops
  name     = "${local.name}-staff-alerts"
  tags     = local.base_tags
}

resource "aws_cloudwatch_metric_alarm" "staff_job_failing" {
  for_each            = local.staff_ops_schedule
  alarm_name          = "${local.name}-staff-job-2-falhas-seguidas"
  alarm_description   = "Job T1.5: duas janelas de 4 min seguidas sem `T15_RESULT ok=true`. Com duas faltas a fonte de membership expira (observation 600 s) e /cases responde 503."
  namespace           = "maezo-operadora/dev/staff"
  metric_name         = "StaffJobSuccess"
  statistic           = "Sum"
  period              = 240
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.staff_alerts[each.key].arn]
  ok_actions          = [aws_sns_topic.staff_alerts[each.key].arn]
  tags                = local.base_tags
}

output "staff_ops" {
  description = "Familias das tasks de operacao staff e o bucket do ledger do job."
  value = var.staff_ops == null ? null : {
    rows_family   = aws_ecs_task_definition.staff_ops_oneshot["rows"].family
    syn_family    = aws_ecs_task_definition.staff_ops_oneshot["syn"].family
    job_family    = aws_ecs_task_definition.staff_job["this"].family
    ledger_bucket = aws_s3_bucket.staff_job_ledger["this"].bucket
    alerts_topic  = aws_sns_topic.staff_alerts["this"].arn
  }
}
